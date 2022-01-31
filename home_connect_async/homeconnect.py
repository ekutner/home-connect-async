from __future__ import annotations
import asyncio
from enum import Enum, IntFlag
import inspect
import logging
import json
from dataclasses import dataclass, field
from typing import Sequence
from dataclasses_json import dataclass_json, Undefined, config
from marshmallow import fields
from datetime import datetime
from collections.abc import Callable

from aiohttp_sse_client.client import MessageEvent
from .appliance import Appliance
from .auth import AuthManager
from .api import HomeConnectAPI
from .const import *

_LOGGER = logging.getLogger(__name__)


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class HomeConnect:
    class HomeConnectStatus(IntFlag):
        INIT = 0,
        LOADING = 1,
        LOADED = 3,
        UPDATES = 4,
        NOUPDATES = ~4,
        READY = 7

    class RefreshMode(Enum):
        NOTHING = 0,
        VALIDATE = 1,
        DYNAMIC_ONLY = 2,
        ALL = 3


    appliances:dict[str, Appliance] = field(default_factory=dict)
    status:HomeConnect.HomeConnectStatus = \
        field(
            default=HomeConnectStatus.INIT,
            metadata=config(
                encoder=lambda val: val.name,
                decoder=lambda val: HomeConnect.HomeConnectStatus.INIT,
                mm_field=fields.String()
            )
        )


    @classmethod
    async def async_create(cls, am:AuthManager, json_data:str=None, delayed_load:bool=False, refresh:RefreshMode=RefreshMode.DYNAMIC_ONLY, auto_update:bool=False) -> HomeConnect:
        """ Create a HomeConnect object - DO NOT USE THE DEFAULT CONSTRUCTOR

        Parameters:
        * json_data - A JSON string of cached data model data obtained by calling .to_json() on a previously loaded HomeConnect object
        * delayed_load - Should appliance data be loaded synchronously, within the execution of this call or skipped and called explicitly.
        * refresh - Specifies which parts of the data should be refreshed. Only applicable when json_data was provided and ignored for delayed_load.
        * auto_update - Subscribe for real-time updates to the data model, ignored for delayed_load

        Notes:
        If delayed_load is set then async_load_data() should be called to complete the loading of the data.

        If auto_update is set to False then subscribe_for_updates() should be called to receive real-time updates to the data
        """
        api = HomeConnectAPI(am)
        if json_data:
            hc:HomeConnect = HomeConnect.from_json(json_data)
            # manually initialize the appliances because they were created from json
            for appliance in hc.appliances.values():
                appliance._api = api
                appliance.clear_all_callbacks()
        else:
            hc = HomeConnect()
        hc._api = api
        hc._refresh_mode = refresh

        if not delayed_load:
            await hc._async_load_data(refresh)

        if auto_update and not delayed_load:
            hc.subscribe_for_updates()

        return hc

    def continue_data_load(self, refresh:RefreshMode = None, on_complete:Callable[[HomeConnect], None] = None) -> asyncio.Task:
        """Complete the loading of the data when using delayed load

        This method can also be used for refreshing the data after it has been loaded.

        Parameters:
        * on_complete - an optional callback method that will be called after the loading has completed
        * refresh - optional refresh mode, if not supplied the value from async_create() will be used
        """
        refresh = refresh if refresh else self._refresh_mode
        self._load_task = asyncio.create_task(self._async_load_data(refresh, on_complete), name="_async_load_data")
        return self._load_task

    async def _async_load_data(self, refresh:RefreshMode=RefreshMode.DYNAMIC_ONLY, on_complete:Callable[[HomeConnect], None] = None) -> None:
        self.status |= self.HomeConnectStatus.LOADING

        if refresh == self.RefreshMode.NOTHING:
            for appliance in self.appliances.values():
                await self._broadcast_event(appliance, "PAIRED")

        else:
            data = await self._api.async_get('/api/homeappliances')
            if data is None:
                raise ConnectionError("Failed to read data from Home Connect API")

            haid_list = []
            if 'homeappliances' in data:
                for ha in data['homeappliances']:
                    haid_list.append(ha['haId'])
                    if ha['connected']:
                        if ha['haId'] in self.appliances and refresh==self.RefreshMode.DYNAMIC_ONLY:
                            # the appliance was already loaded so just refresh the data
                            await self.appliances[ha['haId']].async_fetch_data(include_static_data=False)
                        elif ha['haId'] not in self.appliances or refresh==self.RefreshMode.ALL:
                            appliance = await Appliance.async_create(self._api, ha)
                            self.appliances[ha['haId']] = appliance
                        await self._broadcast_event(self.appliances[ha['haId']], "PAIRED")

            # clear appliances that are no longer paired with the service
            for haId in self.appliances.keys():
                if haId not in haid_list:
                    await self._broadcast_event(self.appliances[haId], "DEPAIRED")
                    del self.appliances[haId]

        self.status |= self.HomeConnectStatus.LOADED

        if on_complete:
            if inspect.iscoroutinefunction(on_complete):
                    await on_complete(self)
            else:
                on_complete(self)


    def subscribe_for_updates(self):
        """ Subscribe to receive real-time updates from the Home Connect cloud service

        close() must be called before the HomeConnect object is terminated to cleanly close the updates channel
        """
        if not hasattr(self, "_updates_task"):
            self._updates_task = None
        if not self._updates_task:
            #self._updates_task = asyncio.create_task(self._api.stream('/api/homeappliances/events', message_handler=self._async_process_updates), name="subscribe_for_updates")
            self._updates_task = asyncio.create_task(self.async_events_stream(), name="subscribe_for_updates")
            return self._updates_task


    def close(self):
        """ Close the updates channel and clear all the configured callbacks

        This method must be called if updates subscription was requested
        """
        if hasattr(self, "_load_task") and self._load_task and not self._load_task.cancelled():
            self._load_task.cancel()
            self._load_task = None

        if  hasattr(self, "_updates_task") and self._updates_task and not self._updates_task.cancelled():
            self._updates_task.cancel()
            self._updates_task = None

        self.clear_all_callbacks()

        for appliance in self.appliances.values():
            appliance.clear_all_callbacks()


    def __getitem__(self, haId) -> Appliance:
        self.appliances.get(haId)

    def _get_haId_from_event(self, event:dict):
        uri_parts = event['uri'].split('/')
        assert(uri_parts[0]=='')
        assert(uri_parts[1]=='api')
        assert(uri_parts[2]=='homeappliances')
        haId = uri_parts[3]
        return haId


    #region - Event stream and updates

    async def async_events_stream(self):
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
                event_source = await self._api.async_get_event_stream('/api/homeappliances/events')
                await event_source.connect()
                self.status |= self.HomeConnectStatus.UPDATES

                async for event in event_source:
                    backoff = 1
                    try:
                        await self._async_process_updates(event)
                    except Exception as ex:
                        _LOGGER.exception('Unhandled exception in stream event handler', exc_info=ex)
            except asyncio.CancelledError:
                break
            except ConnectionRefusedError as ex:
                self.status &= self.HomeConnectStatus.NOUPDATES
                _LOGGER.exception('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
            except ConnectionError as ex:
                self.status &= self.HomeConnectStatus.NOUPDATES
                error_code = parse_sse_error(ex.args[0])
                if error_code == 429:
                    backoff *= 2
                    if backoff > 3600: backoff = 3600
                    elif backoff < 60: backoff = 60
                    _LOGGER.info('Got error 429 when opening event stream connection, will sleep for %s seconds and retry', backoff)
                else:
                    _LOGGER.exception('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                    backoff *= 2
                    if backoff > 120: backoff = 120

                await asyncio.sleep(backoff)

            except asyncio.TimeoutError:
                # it is expected that the connection will time out every hour
                _LOGGER.debug("The SSE connection timeout, will renew and retry")
            except Exception as ex:
                self.status &= self.HomeConnectStatus.NOUPDATES
                _LOGGER.exception('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                await asyncio.sleep(backoff)
                backoff *= 2
                if backoff > 120: backoff = 120

            finally:
                if event_source:
                    await event_source.close()
                    event_source = None


    async def _async_process_updates(self, event:MessageEvent):
        haId = event.last_event_id
        if event.type == 'KEEP-ALIVE':
            self._last_update = datetime.now()
        elif event.type == 'PAIRED':
            self.appliances[haId] = await Appliance.async_create(self._api, haId=haId)
            await self._broadcast_event(self.appliances[haId], event.type)
        elif event.type == 'DEPAIRED':
            if haId in self.appliances:
                await self._broadcast_event(self.appliances[haId], event.type)
                del self.appliances[haId]
        elif event.type == 'DISCONNECTED':
            if haId in self.appliances:
                await self.appliances[haId].async_set_connection_state(False)
                await self._broadcast_event(self.appliances[haId], event.type)
        elif event.type == 'CONNECTED':
            if haId in self.appliances:
                await self.appliances[haId].async_set_connection_state(True)
                await self._broadcast_event(self.appliances[haId], event.type)
            else:
                self.appliances[haId] = await Appliance.async_create(self._api, haId=haId)
                await self._broadcast_event(self.appliances[haId], "PAIRED")
        else:
            # Type is NOTIFY or EVENT
            data = json.loads(event.data)
            if 'items' in data:
                for item in data['items']:
                    haId = self._get_haId_from_event(item) if 'uri' in item else haId
                    if haId in self.appliances:
                        await self.appliances[haId]._async_broadcast_event(item['key'], item['value'])


    def register_callback(self, callback:Callable[[Appliance, str], None], keys:str|Sequence[str]):
        if not isinstance(keys, list):
            keys = [ keys ]

        if not hasattr(self, "_callbacks"):
            self._callbacks = {}

        for key in keys:
            if key not in self._callbacks:
                self._callbacks[key] = set()
            self._callbacks[key].add(callback)


    def clear_all_callbacks(self):
        self._callbacks = {}


    async def _broadcast_event(self, appliance:Appliance, event:str):
        if not hasattr(self, "_callbacks"):
            self._callbacks = {}

        callbacks = self._callbacks.get(event)

        if callbacks:
            for callback in callbacks:
                if inspect.iscoroutinefunction(callback):
                    await callback(appliance, event)
                else:
                    callback(appliance, event)

    #endregion