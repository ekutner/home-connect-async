from __future__ import annotations
import asyncio
from asyncio import Task
from enum import Enum
import inspect
import logging
import json
from typing import ClassVar, Optional, Sequence
from datetime import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses_json import Undefined, config, DataClassJsonMixin

from aiohttp_sse_client.client import MessageEvent

from .const import Events
from .common import ConditionalLogger, HomeConnectError, HealthStatus
from .callback_registery import CallbackRegistry
from .appliance import Appliance
from .auth import AuthManager
from .api import HomeConnectApi

_LOGGER = logging.getLogger(__name__)


#@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class HomeConnect(DataClassJsonMixin):
    """ The main class that wraps the whole data model,
    coordinates the loading of data from the cloud service and listens for update events
    """

    class RefreshMode(Enum):
        """ Enum for the supported data refresh modes """
        NOTHING = 0
        VALIDATE = 1
        DYNAMIC_ONLY = 2
        ALL = 3

    # This is a class variable used as configuration for the dataclass_json
    dataclass_json_config:ClassVar[config] = config(undefined=Undefined.EXCLUDE)

    # The data calss fields
    appliances:dict[str, Appliance] = field(default_factory=dict)
    # status:HomeConnect.HomeConnectStatus = \
    #     field(
    #         default=HomeConnectStatus.INIT,
    #         metadata=config(encoder = lambda val: val.name, exclude = lambda val: True)
    #     )


    _disabled_appliances:Optional[list[str]] = field(default_factory=lambda: list() )

    # Internal fields - not serialized to JSON
    _api:Optional[HomeConnectApi] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _updates_task:Optional[Task] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _load_task:Optional[Task] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _health:Optional[HealthStatus] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _callbacks:Optional[CallbackRegistry] = field(default_factory=lambda: CallbackRegistry(), metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _sse_timeout:Optional[int] = field(default=None)

    @classmethod
    async def async_create(cls,
        am:AuthManager,
        json_data:str=None,
        delayed_load:bool=False,
        refresh:RefreshMode=RefreshMode.DYNAMIC_ONLY,
        auto_update:bool=False,
        lang:str=None,
        disabled_appliances:list[str] = [],
        sse_timeout:int=10
        ) -> HomeConnect:
        """ Factory for creating a HomeConnect object - DO NOT USE THE DEFAULT CONSTRUCTOR

        Parameters:
        * json_data - A JSON string of cached data model data obtained by calling .to_json() on a previously loaded HomeConnect object
        * delayed_load - Should appliance data be loaded synchronously, within the execution of this call or skipped and called explicitly.
        * refresh - Specifies which parts of the data should be refreshed. Only applicable when json_data was provided and ignored for delayed_load.
        * auto_update - Subscribe for real-time updates to the data model, ignored for delayed_load

        Notes:
        If delayed_load is set then async_load_data() should be called to complete the loading of the data.

        If auto_update is set to False then subscribe_for_updates() should be called to receive real-time updates to the data
        """
        health = HealthStatus()
        api = HomeConnectApi(am, lang, health)
        hc:HomeConnect = None
        if json_data:
            try:
                hc = HomeConnect.from_json(json_data)
                #hc.status = cls.HomeConnectStatus.INIT
                # manually initialize the appliances because they were created from json
                for appliance in hc.appliances.values():
                    appliance._homeconnect = hc
                    appliance._callbacks = hc._callbacks
                    appliance._api = api
            except Exception as ex:
                _LOGGER.exception("Exception when loading HomeConnect data from JSON", exc_info=ex)
        if not hc:
            hc = HomeConnect()

        hc._api = api
        hc._health = health
        hc._refresh_mode = refresh
        hc._disabled_appliances = disabled_appliances
        hc._sse_timeout = sse_timeout

        if not delayed_load:
            await hc.async_load_data(refresh)

        if auto_update and not delayed_load:
            hc.subscribe_for_updates()

        return hc

    def start_load_data_task(self,
        refresh:RefreshMode = None,
        on_complete:Callable[[HomeConnect], None] = None,
        on_error:Callable[[HomeConnect, Exception], None] = None
    ) -> asyncio.Task:
        """Complete the loading of the data when using delayed load

        This method can also be used for refreshing the data after it has been loaded.

        Parameters:
        * on_complete - an optional callback method that will be called after the loading has completed
        * refresh - optional refresh mode, if not supplied the value from async_create() will be used
        """
        refresh = refresh if refresh else self._refresh_mode
        self._load_task = asyncio.create_task(self.async_load_data(refresh, on_complete, on_error), name="_async_load_data")
        return self._load_task

    async def async_load_data(self,
        refresh:RefreshMode=RefreshMode.DYNAMIC_ONLY,
        on_complete:Callable[[HomeConnect], None] = None,
        on_error:Callable[[HomeConnect, Exception], None] = None
    ) -> None:
        """ Loads or just refreshes the data model from the cloud service """
        #self.status |= self.HomeConnectStatus.LOADING
        self._health.set_status(self._health.Status.RUNNING)
        self._health.unset_status(self._health.Status.LOADING_FAILED)

        try:
            if refresh == self.RefreshMode.NOTHING:
                for appliance in self.appliances.values():
                    await self._callbacks.async_broadcast_event(appliance, Events.PAIRED)

            else:
                response = await self._api.async_get('/api/homeappliances')
                if response.status != 200:
                    _LOGGER.warning("Failed to get the list of appliances code=%d error=%s", response.status, response.error_key)
                    raise HomeConnectError(f"Failed to get the list of appliances (code={response.status})", response=response)
                data = response.data

                haid_list = []
                if 'homeappliances' in data:
                    for ha in data['homeappliances']:
                        haid = ha['haId']
                        if  haid in self._disabled_appliances or haid.lower().replace('-','_') in self._disabled_appliances:
                            continue

                        haid_list.append(haid)
                        if ha['connected']:
                            if haid in self.appliances:
                                # the appliance was already loaded so just refresh the data
                                if refresh == self.RefreshMode.DYNAMIC_ONLY:
                                    await self.appliances[haid].async_fetch_data(include_static_data=False)
                                elif refresh == self.RefreshMode.ALL:
                                    await self.appliances[haid].async_fetch_data(include_static_data=True)
                            else:
                                appliance = await Appliance.async_create(self, ha)
                                self.appliances[haid] = appliance
                            await self._callbacks.async_broadcast_event(self.appliances[ha['haId']], Events.PAIRED)
                            await self._callbacks.async_broadcast_event(self.appliances[ha['haId']], Events.DATA_CHANGED)
                            _LOGGER.debug("Loadded appliance: %s", self.appliances[ha['haId']].name)
                        elif haid in self.appliances:
                            _LOGGER.warning("The appliance (%s) is disconnected when loading for the first time", haid)
                            await self.appliances[haid].async_set_connection_state(False)

                # clear appliances that are no longer paired with the service
                for haId in self.appliances.keys():
                    if haId not in haid_list:
                        await self._callbacks.async_broadcast_event(self.appliances[haId], Events.DEPAIRED)
                        del self.appliances[haId]

            #self.status |= self.HomeConnectStatus.LOADED
            self._health.set_status(self._health.Status.LOADED)
        except Exception as ex:
            _LOGGER.warning("Failed to load data from Home Connect (%s)", str(ex), exc_info=ex)
            #self.status = self.HomeConnectStatus.LOADING_FAILED
            self._health.set_status(self._health.Status.LOADING_FAILED)
            if on_error:
                if inspect.iscoroutinefunction(on_error):
                    await on_error(self, ex)
                else:
                    on_error(self, ex)
            raise

        if on_complete:
            if inspect.iscoroutinefunction(on_complete):
                await on_complete(self)
            else:
                on_complete(self)


    def subscribe_for_updates(self):
        """ Subscribe to receive real-time updates from the Home Connect cloud service

        close() must be called before the HomeConnect object is terminated to cleanly close the updates channel
        """
        if not self._updates_task:
            #self._updates_task = asyncio.create_task(self._api.stream('/api/homeappliances/events', message_handler=self._async_process_updates), name="subscribe_for_updates")
            self._updates_task = asyncio.create_task(self.async_events_stream(), name="subscribe_for_updates")
            return self._updates_task


    def close(self):
        """ Close the updates channel and clear all the configured callbacks

        This method must be called if updates subscription was requested
        """
        if self._load_task and not self._load_task.cancelled():
            self._load_task.cancel()
            self._load_task = None

        if  self._updates_task and not self._updates_task.cancelled():
            self._updates_task.cancel()
            self._updates_task = None

        self.clear_all_callbacks()

        for appliance in self.appliances.values():
            appliance.clear_all_callbacks()


    def __getitem__(self, haId) -> Appliance:
        """ Supports simple access to an appliance based on its haId """
        return self.appliances.get(haId)

    @property
    def health(self):
        return self._health


    #region - Event stream and updates

    async def async_events_stream(self):
        """ Open the SSE channel, process the incoming events and handle errors """

        def parse_sse_error(error:str) -> int:
            try:
                parts = error.split(': ')
                error_code = int(parts[-1])
                return error_code
            except:
                return 0


        backoff = 2
        event_source = None
        while True:
            try:
                _LOGGER.debug("Connecting to SSE stream")
                event_source = await self._api.async_get_event_stream('/api/homeappliances/events', self._sse_timeout)
                await event_source.connect()
                #self.status |= self.HomeConnectStatus.UPDATES
                self._health.set_status(self._health.Status.UPDATES)

                async for event in event_source:
                    _LOGGER.debug("Received event from SSE stream: %s", str(event))
                    backoff = 1
                    try:
                        await self._async_process_updates(event)
                    except Exception as ex:
                        _LOGGER.debug('Unhandled exception in stream event handler', exc_info=ex)
            except asyncio.CancelledError as ex:
                _LOGGER.debug('Got asyncio.CancelledError exception. Home Assistant is probably closing so aborting SSE loop', exc_info=ex)
                break
            except ConnectionRefusedError as ex:
                #self.status &= self.HomeConnectStatus.NOUPDATES
                self._health.unset_status(self._health.Status.UPDATES)
                _LOGGER.debug('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
            except ConnectionError as ex:
                #self.status &= self.HomeConnectStatus.NOUPDATES
                self._health.unset_status(self._health.Status.UPDATES)
                error_code = parse_sse_error(ex.args[0])
                if error_code == 429:
                    backoff *= 2
                    if backoff > 3600: backoff = 3600
                    elif backoff < 60: backoff = 60
                    _LOGGER.debug('Got error 429 when opening event stream connection, will sleep for %s seconds and retry', backoff)
                else:
                    _LOGGER.debug('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                    backoff *= 2
                    if backoff > 120: backoff = 120

                await asyncio.sleep(backoff)

            except asyncio.TimeoutError:
                # it is expected that the connection will time out every hour
                _LOGGER.debug("The SSE connection timeed-out, will renew and retry")
            except Exception as ex:
                #self.status &= self.HomeConnectStatus.NOUPDATES
                _LOGGER.debug('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                self._health.unset_status(self._health.Status.UPDATES)
                await asyncio.sleep(backoff)
                backoff *= 2
                if backoff > 120: backoff = 120

            finally:
                if event_source:
                    await event_source.close()
                    event_source = None

        #self.status &= self.HomeConnectStatus.NOUPDATES
        self._health.unset_status(self._health.Status.UPDATES)
        _LOGGER.debug("Exiting SSE event stream")


    async def _async_process_updates(self, event:MessageEvent):
        """ Handle the different kinds of events received over the SSE channel """
        haid = event.last_event_id
        if event.type == 'KEEP-ALIVE' or haid.lower().replace('-','_') in self._disabled_appliances:
            self._last_update = datetime.now()
            return
        if haid not in self.appliances:
            # handle cases where the appliance wasn't loaded before
            _LOGGER.debug("Unknown haId '%s' reloading HomeConnected from the API", haid)
            await self.async_load_data()
        if event.type == 'PAIRED':
            self.appliances[haid] = await Appliance.async_create(self, haId=haid)
            await self._callbacks.async_broadcast_event(self.appliances[haid],  Events.PAIRED)
        elif event.type == 'DEPAIRED':
            if haid in self.appliances:
                await self._callbacks.async_broadcast_event(self.appliances[haid], Events.DEPAIRED)
                del self.appliances[haid]
        elif event.type =='DISCONNECTED':
            if haid in self.appliances:
                await self.appliances[haid].async_set_connection_state(False)
                await self._callbacks.async_broadcast_event(self.appliances[haid], Events.DISCONNECTED)
        elif event.type == 'CONNECTED':
            if haid in self.appliances:
                await self.appliances[haid].async_set_connection_state(True)
                await self._callbacks.async_broadcast_event(self.appliances[haid], Events.CONNECTED)
            else:
                self.appliances[haid] = await Appliance.async_create(self, haId=haid)
                await self._callbacks.async_broadcast_event(self.appliances[haid], Events.PAIRED)
        else:
            # Type is NOTIFY or EVENT
            data = json.loads(event.data)
            haid = data['haId']
            if haid not in self.appliances:
                _LOGGER.debug("Unknown haId '%s' reloading HomeConnected from the API", haid)
                await self.async_load_data()
            if 'items' in data:
                for item in data['items']:
                    # haid = self._get_haId_from_event(item) if 'uri' in item else haid
                    if haid in self.appliances:
                        appliance = self.appliances[haid]
                        await appliance.async_update_data(item)


    # def _get_haId_from_event(self, event:dict):
    #     """ Parse the uri field that exists in some streamed events to extract the haID
    #     This seems safer than relying on the last_event_id field so preferred when it's available
    #     """
    #     uri_parts = event['uri'].split('/')
    #     assert(uri_parts[0]=='')
    #     assert(uri_parts[1]=='api')
    #     assert(uri_parts[2]=='homeappliances')
    #     haId = uri_parts[3]
    #     return haId


    def register_callback(self, callback:Callable[[Appliance, str], None] | Callable[[Appliance, str, any], None], keys:str|Sequence[str], appliance:Appliance|str = None):
        """ Register callback for change event notifications

        Use the Appliance.register_callback() to register for appliance data update events
        """

        self._callbacks.register_callback(callback, keys, appliance)


    def clear_all_callbacks(self):
        """ Clear all the registered callbacks """
        self._callbacks.clear_all_callbacks()

    #endregion


