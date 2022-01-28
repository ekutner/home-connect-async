from __future__ import annotations
import asyncio
import logging
import json
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from datetime import datetime

from .appliance import Appliance
from .auth import AuthManager
from .api import HomeConnectAPI
from .const import *

_LOGGER = logging.getLogger(__name__)


@dataclass_json
@dataclass
class HomeConnect:
    appliances:dict[str, Appliance] = field(default_factory=dict)

    @classmethod
    async def create(cls, am:AuthManager, auto_update=False, json_data:str=None, refresh_dynamic_data:bool=True, verify_data:bool=True) -> HomeConnect:
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
        hc._updates_task = None

        if json_data is None or refresh_dynamic_data or verify_data:
            data = await api.get('/api/homeappliances')
            if data is None:
                raise ConnectionError("Failed to read data from Home Connect API")

            haid_list = []
            if 'homeappliances' in data:
                for ha in data['homeappliances']:
                    haid_list.append(ha['haId'])
                    if ha['haId'] in hc.appliances:
                        if refresh_dynamic_data:
                            # the appliance was already loaded so just refresh the data
                            await hc.appliances[ha['haId']].async_fetch_data(include_static_data=False)
                    else:
                        appliance = await Appliance.async_create(api, ha)
                        hc.appliances[appliance.haId] = appliance

            if json_data:
                # clear appliances that are no longer paired with the service
                for haId in hc.appliances.keys():
                    if haId not in haid_list:
                        del hc.appliances[haId]

        if auto_update:
            hc.subscribe_for_updates()

        return hc

    def close(self):
        if  self._updates_task:
            self._updates_task.cancel()
            self._updates_task = None

        for appliance in self.appliances.values():
            appliance.clear_all_callbacks()

    def subscribe_for_updates(self):
        self._updates_task = asyncio.create_task(self._api.stream('/api/homeappliances/events', message_handler=self.process_updates))


    async def process_updates(self, event):
        haId = event.last_event_id
        if event.type == 'KEEP-ALIVE':
            self._last_update = datetime.now()
        elif event.type == 'PAIRED':
            self.appliances[haId] = Appliance.async_create(self._api, haId=haId)
            # TODO: Add callback support
        elif event.type == 'DEPAIRED':
            if haId in self.appliances:
                del self.appliances[haId]
        elif event.type == 'DISCONNECTED':
            if haId in self.appliances:
                self.appliances[haId].connection_state(False)
        elif event.type == 'CONNECTED':
            if haId in self.appliances:
                self.appliances[haId].connection_state(True)
        else:
            # Type is NOTIFY or EVENT
            data = json.loads(event.data)
            if 'items' in data:
                for item in data['items']:
                    haId = self._get_haId_from_event(item) if 'uri' in item else haId
                    if haId in self.appliances:
                        await self.appliances[haId]._async_on_stream_event(item['key'], item['value'])


    def __getitem__(self, haId) -> Appliance:
        self.appliances.get(haId)

    def _get_haId_from_event(self, event:dict):
        uri_parts = event['uri'].split('/')
        assert(uri_parts[0]=='')
        assert(uri_parts[1]=='api')
        assert(uri_parts[2]=='homeappliances')
        haId = uri_parts[3]
        return haId

