from __future__ import annotations
import inspect
import json
import logging
import fnmatch
from dataclasses import dataclass, field
import re
from typing import Optional, Pattern
from urllib import response
from dataclasses_json import dataclass_json, Undefined, config
from marshmallow import fields
from collections.abc import Sequence, Callable

from home_connect_async.common import HomeConnectError

from .api import HomeConnectApi

_LOGGER = logging.getLogger(__name__)

@dataclass_json
@dataclass
class Option:
    key:str
    type:Optional[str]
    name:Optional[str]
    unit:Optional[str]
    value:Optional[any]
    min:Optional[int] = None
    max:Optional[int] = None
    stepsize:Optional[int] = None
    allowedvalues:Optional[list[str]] = None

    @classmethod
    def create(self, data:dict):
        option = Option(
            key = data['key'],
            type = data.get('type'),
            name = data.get('name'),
            value = data.get('value'),
            unit = data.get('unit'),
        )
        if 'constraints' in data:
            constraints:dict = data['constraints']
            option.min = constraints.get('min')
            option.max = constraints.get('max')
            option.stepsize = constraints.get('stepsize')
            option.allowedvalues = constraints.get('allowedvalues')
        return option

    def get_option_to_apply(self, value, exception_on_error=False):
        def value_error():
            if exception_on_error:
                raise ValueError(f'Invalid value for this option: {value}')
            else:
                return None

        if self.allowedvalues is not None and value not in self.allowedvalues:
            return value_error()

        if self.min is not None and value < self.min:
            return value_error()

        if self.max is not None and value > self.max:
            return value_error()

        if self.stepsize is not None and value % self.stepsize != 0:
            return value_error()

        return { 'key': self.key, 'value': self.value, 'unit': self.unit}



@dataclass_json
@dataclass
class Program:
    key:str
    name:Optional[str] = None
    options:dict[str, Option] = None
    execution:Optional[str] = None
    active:Optional[bool] = False

    @classmethod
    def create(cls, data:dict):
        program = cls(data['key'])
        program._update(data)
        return program

    def _update(self, data:dict):
        self.key = data['key']
        self.name = data.get('name')
        if 'constraints' in data:
            constraints:dict = data['constraints']
            self.execution = constraints.get('execution')
        if 'options' in data:
            self.options = {}
            for opt in data['options']:
                o = Option.create(opt)
                self.options[o.key] = o
        return self


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Appliance():
    name:str
    brand:str
    vib:str
    connected:bool
    type:str
    enumber:str
    haId:str
    uri:str
    available_programs:Optional[dict[str, Program]] = None
    active_program:Optional[Program] = None
    selected_program:Optional[Program] = None
    status:dict[str, any] = None
    settings:dict[str, Option] = None

    # Internal fields
    _api:Optional[HomeConnectApi] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))

    #region - Manage Programs
    async def async_get_active_program(self):
        prog = await self._async_fetch_programs('active')
        self.active_program = prog
        return prog

    async def async_get_selected_program(self):
        prog = await self._async_fetch_programs('selected')
        self.selected_program = prog
        return prog

    async def async_select_program(self, key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        if program is not None:
            key = program.key

        if key is None:
            _LOGGER.error('Either "program" or "key" must be specified')
            return False

        return await self._async_set_program(key, options, 'selected')

    async def async_start_program(self, key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        if program is not None:
            key = program.key

        if key is None and self.selected_program is not None:
            key = self.selected_program.key
        else:
            _LOGGER.error('Either "program" or "key" must be specified')
            return False

        if options is None and self.selected_program is not None:
            options = []
            for opt in self.selected_program.options.values():
                option = { "key": opt.key, "value": opt.value}
                options.append(option)

        return await self._async_set_program(key, options, 'active')

    async def async_stop_active_program(self):
        if self.active_program is None:
            await self.async_get_active_program()

    async def async_set_option(self, key, value) -> bool:
        url = f'{self._base_endpoint}/programs/selected/options/{key}'

        command = {
            "data": {
                "key": key,
                "value": value
            }
        }
        jscmd = json.dumps(command)
        response = await self._api.async_put(url, jscmd)
        if response.status == 204:
            return True
        elif response.error_description:
            raise HomeConnectError(response.error_description, response=response)
        raise HomeConnectError("Failed to set option ({response.status})", response=response)


    async def async_apply_setting(self, key, value):
        url = f'{self._base_endpoint}/settings/{key}'

        command = {
            "data": {
                "key": key,
                "value": value
            }
        }
        jscmd = json.dumps(command)
        response = await self._api.async_put(url, jscmd)
        if response.status == 204:
            return True
        elif response.error_description:
            raise HomeConnectError(response.error_description, response=response)
        raise HomeConnectError("Failed to apply setting ({response.status})", response=response)


    async def _async_set_program(self, key, options:Sequence[dict], mode:str) -> bool:
        url = f'{self._base_endpoint}/programs/{mode}'
        if options is not None and not isinstance(options, list):
            options = [ options ]

        command = {
            "data": {
                "key": key,
                "options": []
            }
        }
        if options:
            command['data']['options'] = options

        jscmd = json.dumps(command)
        response = await self._api.async_put(url, jscmd)
        if response.status == 204:
            return True
        elif response.error_description:
            raise HomeConnectError(response.error_description, code=response.status, error_key=response.error_key, response=response)
        raise HomeConnectError("Failed to set program ({response.status})", response=response)



    async def async_set_connection_state(self, connected:bool):
        self.connected = connected
        if connected:
            await self.async_fetch_data(include_static_data=False)
        await self._async_broadcast_event("CONNECTION_CHANGED", connected)


    #region - Handle Updates, Events and Callbacks

    async def _async_update_data(self, key:str, value) -> None:
        if key == 'BSH.Common.Root.SelectedProgram':
            self.selected_program = await self._async_fetch_programs('selected')
        elif key == 'BSH.Common.Root.ActiveProgram':
            self.active_program = await self._async_fetch_programs('active')
        else:
            if key == 'BSH.Common.Status.OperationState' and value == 'BSH.Common.EnumType.OperationState.Finished':
                self.active_program = None
            if self.selected_program and key in self.selected_program.options:
                self.selected_program.options[key].value = value
            if self.active_program and key in self.active_program.options:
                self.active_program.options[key].value = value
            if key in self.status:
                self.status[key] = value
            if key in self.settings:
                self.settings[key].value = value


    async def _async_broadcast_event(self, key:str, value):
        # first update the local data
        await self._async_update_data(key, value)

        # then dispatch the registered callbacks

        handled:bool = False
        # dispatch simple event callbacks
        if key in self._updates_callbacks:
            for callback in self._updates_callbacks[key]:
                if inspect.iscoroutinefunction(callback):
                    await callback(self, key, value)
                else:
                    callback(self, key, value)
                handled = True

        # dispatch wildcard or value based callbacks
        for callback_record in self._wildcard_callbacks:
            if callback_record["key"].fullmatch(key):
                callback = callback_record['callback']
                if inspect.iscoroutinefunction(callback):
                    await callback(self, key, value)
                else:
                    callback(self, key, value)
                handled = True

        # dispatch default callbacks for unhandled events
        if not handled and 'DEFAULT' in self._updates_callbacks:
            for callback in self._updates_callbacks["DEFAULT"]:
                if inspect.iscoroutinefunction(callback):
                    await callback(self, key, value)
                else:
                    callback(self, key, value)
                handled = True


    def register_callback(self, callback:Callable[[Appliance, str, any], None], keys:str|Sequence[str] ) -> None:
        ''' Register a callback to be called when an update is received for the specified keys
            Wildcard syntax is also supported for the keys

            They key "CONNECTION_CHANGED" will be used when the connection state of the appliance changes

            The special key "DEFAULT" may be used to catch all unhandled events
        '''
        if keys is None:  raise ValueError("An event key must be specified")
        elif not isinstance(keys, list): keys = [ keys ]

        for key in keys:
            if '*' in key:
                callback_record = {
                    "key": re.compile(fnmatch.translate(key), re.IGNORECASE),
                    "callback": callback
                }
                self._wildcard_callbacks.append(callback_record)
            else:
                if key not in self._updates_callbacks:
                    self._updates_callbacks[key] = set()
                self._updates_callbacks[key].add(callback)

    def deregister_callback(self, callback:Callable[[], None], keys:str|Sequence[str]) -> None:
        if keys is None:  raise ValueError("An event key must be specified")
        elif not isinstance(keys, list): keys = [ keys ]

        for key in keys:
            if '*' in key:
                callback_record = { "key": key, "callback": callback}
                try:
                    self._wildcard_callbacks.remove(callback_record)
                except ValueError:
                    # ignore if the value is not found in the list
                    pass
            else:
                if key in self._updates_callbacks:
                    self._updates_callbacks[key].remove(callback)

    def clear_all_callbacks(self):
        self._wildcard_callbacks = []
        self._updates_callbacks = {}


    #endregion

    #region - Initialization and Data Loading
    @classmethod
    async def async_create(cls, api:HomeConnectApi, properties:dict=None, haId:str=None) -> Appliance:
        if haId:
            response = await api.async_get(f"/api/homeappliances/{haId}")  # This should either work or raise an exception
            properties = response.data

        appliance = cls(
            name = properties['name'],
            brand = properties['brand'],
            type = properties['type'],
            vib = properties['vib'],
            connected = properties['connected'],
            enumber = properties['enumber'],
            haId = properties['haId'],
            uri = f"/api/homeappliances/{properties['haId']}"
        )
        appliance._api = api
        appliance.clear_all_callbacks()

        await appliance.async_fetch_data()

        return appliance

    _base_endpoint = property(lambda self: f"/api/homeappliances/{self.haId}")

    async def async_fetch_data(self, include_static_data:bool=True):
        if include_static_data:
            self.available_programs = await self._async_fetch_programs('available')
        self.selected_program = await self._async_fetch_programs('selected')
        self.active_program = await self._async_fetch_programs('active')
        self.settings = await self._async_fetch_settings()
        self.status = await self._async_fetch_status()

    async def _async_fetch_programs(self, type:str):
        endpoint = f'{self._base_endpoint}/programs/{type}'
        response = await self._api.async_get(endpoint)
        if response.status == 404 or response.error_key == "SDK.Error.UnsupportedOperation":
            return None
        elif not response.data:
            raise HomeConnectError(msg=f"Failed to get a valid response from the Home Connect service ({response.status})", response=response)
        data = response.data

        programs = {}
        if 'programs' not in data:
            # When fetching selected and active programs the parent program node doesn't exist so we force it
            data = { 'programs': [ data ] }

        for p in data['programs']:
            prog = Program.create(p)
            if 'options' in p:
                options = self.optionlist_to_dict(p['options'])
            else:
                options = await self._async_fetch_options(f"programs/{type}/{p['key']}", "options")
            prog.options = options

            programs[p['key']] = prog

        if type in ['selected', 'active'] and len(programs)==1:
            return list(programs.values())[0]
        else:
            return programs

    async def _async_fetch_status(self):
        endpoint = f'{self._base_endpoint}/status'
        response = await self._api.async_get(endpoint)
        if response.status == 409:
            raise HomeConnectError(msg="The appliance didn't respond (409)", response=response)
        data = response.data
        if data is None or 'status' not in data: return None

        res = {}
        for status in data['status']:
            res[status['key']] = status['value']
        return res

    async def _async_fetch_settings(self):
        endpoint = f'{self._base_endpoint}/settings'
        response = await self._api.async_get(endpoint)
        if response.status == 408:
            raise HomeConnectError(msg="The appliance didn't respond (408)", response=response)
        data = response.data
        if data is None or 'settings' not in data: return None

        settings = {}
        for setting in data['settings']:
            endpoint = f'{self._base_endpoint}/settings/{setting["key"]}'
            response = await self._api.async_get(endpoint)
            if response.status != 200:
                continue
            settings[setting['key']] = Option.create(response.data)
        return settings

    async def _async_fetch_options(self, uri_suffix, subkey=None):
        if subkey == None:
            subkey = uri_suffix
        endpoint = f'{self._base_endpoint}/{uri_suffix}'
        response = await self._api.async_get(endpoint)      # This is expected to always succeed if the previous call succeeds
        data = response.data
        if data is None or subkey not in data:
            return None
        else:
            return self.optionlist_to_dict(data[subkey])

    def optionlist_to_dict(self, l:Sequence[dict]) -> dict:
        d = {}
        for element in l:
            d[element['key']] = Option.create(element)
        return d

    #endregion