from __future__ import annotations
import inspect
import json
import logging
import fnmatch
from dataclasses import dataclass, field
import re
from typing import Optional
from dataclasses_json import dataclass_json, Undefined, config
from collections.abc import Sequence, Callable

from .common import HomeConnectError
from .api import HomeConnectApi

_LOGGER = logging.getLogger(__name__)

@dataclass_json
@dataclass
class Option:
    """ Class to represent a Home Connect Option """
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
    def create(cls, data:dict):
        """ A factory to create a new instance from a dictionary in the Home COnnect format """
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
        """ Construct an option dict that can be sent to the Home Connect API """
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
    """ Class to represent a Home Connect Program """

    key:str
    name:Optional[str] = None
    options:dict[str, Option] = None
    execution:Optional[str] = None
    active:Optional[bool] = False

    @classmethod
    def create(cls, data:dict):
        """ A factory to create a new instance from a dict in the Home Connect format """
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
    """ Class to represent a Home Connect Appliance """
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
    _wildcard_callbacks:Optional[Sequence[str]] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _updates_callbacks:Optional[dict[str, Callable[[Appliance, str, any], None]]] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))

    #region - Manage Programs
    async def async_get_active_program(self):
        """ Get the active program """
        prog = await self._async_fetch_programs('active')
        self.active_program = prog
        return prog

    async def async_get_selected_program(self):
        """ Get the selected program """
        prog = await self._async_fetch_programs('selected')
        self.selected_program = prog
        return prog

    async def async_select_program(self, key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        """ Set the selected program

        Parameters:
        key: The key of the program to select
        options: Additional program options to set
        program: A Program object that represents the selected program. If used then "key" is ignored.
        """
        if program is not None:
            key = program.key

        if key is None:
            _LOGGER.error('Either "program" or "key" must be specified')
            return False

        return await self._async_set_program(key, options, 'selected')

    async def async_start_program(self, key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        """ Started the specified program

        Parameters:
        key: The key of the program to select
        options: Additional program options to set
        program: A Program object that represents the selected program. If used then "key" is ignored.
        """
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

    async def async_stop_active_program(self) -> bool:
        """ Stop the active program """
        if self.active_program is None:
            await self.async_get_active_program()
        if self.active_program:
            response = await self._api.async_delete(f'{self._base_endpoint}/programs/active')
            if response.status == 204:
                return True
            elif response.error_description:
                raise HomeConnectError(response.error_description, response=response)
            raise HomeConnectError("Failed to stop the program ({response.status})", response=response)
        return False

    async def async_set_option(self, key, value) -> bool:
        """ Set a value for a specific program option """
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
        """ Apply a global appliance setting """
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
        """ Main function to handle all scenarions of setting a program """
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
            raise HomeConnectError(response.error_description, response=response)
        raise HomeConnectError("Failed to set program ({response.status})", response=response)



    async def async_set_connection_state(self, connected:bool):
        """ Update the appliance connection state when notified about a state change from the event stream """
        self.connected = connected
        if connected:
            await self.async_fetch_data(include_static_data=False)
        await self._async_broadcast_event("CONNECTION_CHANGED", connected)


    #region - Handle Updates, Events and Callbacks

    async def _async_update_data(self, key:str, value) -> None:
        """ Read or update the object's data structure with data from the cloud service """
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
        """ Broadcast an event to all subscribed callbacks """
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
        """ Register a callback to be called when an update is received for the specified keys
            Wildcard syntax is also supported for the keys

            They key "CONNECTION_CHANGED" will be used when the connection state of the appliance changes

            The special key "DEFAULT" may be used to catch all unhandled events
        """
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
        """ Clear a callback that was prevesiously registered so it stops getting notifications """
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
        """ Clear all the registered callbacks """
        self._wildcard_callbacks = []
        self._updates_callbacks = {}


    #endregion

    #region - Initialization and Data Loading
    @classmethod
    async def async_create(cls, api:HomeConnectApi, properties:dict=None, haId:str=None) -> Appliance:
        """ A factory to create an instance of the class """
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
        """ Load the appliance data from the cloud service """
        if include_static_data:
            self.available_programs = await self._async_fetch_programs('available')
        self.selected_program = await self._async_fetch_programs('selected')
        self.active_program = await self._async_fetch_programs('active')
        self.settings = await self._async_fetch_settings()
        self.status = await self._async_fetch_status()

    async def _async_fetch_programs(self, kind:str):
        """ Main function to fetch the different kinds of programs with their options from the cloud service """
        endpoint = f'{self._base_endpoint}/programs/{kind}'
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
                options = await self._async_fetch_options(f"programs/{kind}/{p['key']}", "options")
            prog.options = options

            programs[p['key']] = prog

        if kind in ['selected', 'active'] and len(programs)==1:
            return list(programs.values())[0]
        else:
            return programs

    async def _async_fetch_status(self):
        """ Fetch the appliance status values """
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
        """ Fetch the appliance settings """
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
        """ Helper function to fetch detailed options of a program """
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