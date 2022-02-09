from __future__ import annotations
import asyncio
import json
import logging

from collections.abc import Sequence, Callable
from dataclasses import dataclass, field
from typing import Optional
from dataclasses_json import dataclass_json, Undefined, config

import home_connect_async.homeconnect as homeconnect
from .common import DeviceOfflineError, HomeConnectError
from .const import EVENT_CONNECTION_CHANGED, EVENT_DATA_REFRESHED

_LOGGER = logging.getLogger(__name__)

@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Option:
    """ Class to represent a Home Connect Option """
    key:str
    type:Optional[str] = None
    name:Optional[str] = None
    unit:Optional[str] = None
    value:Optional[any] = None
    displayvalue:Optional[str] = None
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
            displayvalue= data.get('displayvalue')
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
    #_api:Optional[HomeConnectApi] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _homeconnect:Optional[homeconnect.HomeConnect] = field(default_factory=lambda: None, metadata=config(encoder=lambda val: None, decoder=lambda val: None, exclude=lambda val: True))
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
            response = await self._homeconnect._api.async_delete(f'{self._base_endpoint}/programs/active')
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
        response = await self._homeconnect._api.async_put(url, jscmd)
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
        response = await self._homeconnect._api.async_put(url, jscmd)
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
        response = await self._homeconnect._api.async_put(url, jscmd)
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
        await self._homeconnect._callbacks.async_broadcast_event(self, EVENT_CONNECTION_CHANGED, connected)


    #region - Handle Updates, Events and Callbacks

    # async def _async_update_data(self, key:str, value) -> None:
    #     """ Read or update the object's data structure with data from the cloud service """
    #     if key == 'BSH.Common.Root.SelectedProgram' or key == 'BSH.Common.Root.ActiveProgram':
    #         self.selected_program = await self._async_fetch_programs('selected')
    #         self.active_program = await self._async_fetch_programs('active')
    #     else:
    #         if key == 'BSH.Common.Status.OperationState' and value == 'BSH.Common.EnumType.OperationState.Finished':
    #             self.active_program = None
    #         if self.selected_program and key in self.selected_program.options:
    #             self.selected_program.options[key].value = value
    #         if self.active_program and key in self.active_program.options:
    #             self.active_program.options[key].value = value
    #         if key in self.status:
    #             self.status[key] = value
    #         if key in self.settings:
    #             self.settings[key].value = value


    def register_callback(self, callback:Callable[[Appliance, str, any], None], keys:str|Sequence[str] ) -> None:
        """ Register a callback to be called when an update is received for the specified keys
            Wildcard syntax is also supported for the keys

            They key EVENT_CONNECTION_CHANGED will be used when the connection state of the appliance changes

            The special key "DEFAULT" may be used to catch all unhandled events
        """
        self._homeconnect._callbacks.register_callback(callback, keys, self)

    def deregister_callback(self, callback:Callable[[], None], keys:str|Sequence[str]) -> None:
        """ Clear a callback that was prevesiously registered so it stops getting notifications """
        self._homeconnect._callbacks.deregister_callback(callback, keys, self)

    def clear_all_callbacks(self):
        """ Clear all the registered callbacks """
        self._homeconnect._callbacks.clear_appliance_callbacks(self)


    #endregion

    #region - Initialization and Data Loading
    @classmethod
    async def async_create(cls, hc:homeconnect.HomeConnect, properties:dict=None, haId:str=None) -> Appliance:
        """ A factory to create an instance of the class """
        if haId:
            response = await hc._api.async_get(f"/api/homeappliances/{haId}")  # This should either work or raise an exception
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
        appliance._homeconnect = hc

        await appliance.async_fetch_data()

        return appliance

    _base_endpoint = property(lambda self: f"/api/homeappliances/{self.haId}")

    async def async_fetch_data(self, include_static_data:bool=True, delay=0):
        """ Load the appliance data from the cloud service

        Either a DATA_REFRESHED or CONNECTION_CHANGED even will be fired after the data is updated
        """

        if delay>0:
            await asyncio.sleep(delay)
        try:
            if include_static_data:
                self.available_programs = await self._async_fetch_programs('available')
            self.selected_program = await self._async_fetch_programs('selected')
            self.active_program = await self._async_fetch_programs('active')
            self.settings = await self._async_fetch_settings()
            self.status = await self._async_fetch_status()

            if not self.connected:
                await self.async_set_connection_state(True)
            else:
                await self._homeconnect._callbacks.async_broadcast_event(self, EVENT_DATA_REFRESHED)

        except DeviceOfflineError:
            # sometime devices are offline despite the appliance being listed as connected so no event is sent when the device goes online again
            # so we mark the appliance as not connected and keep retrying until we get the data
            await self.async_set_connection_state(False)
            delay = delay + 60 if delay<300 else 300
            self._wait_for_device_task = asyncio.create_task(self.async_fetch_data(include_static_data, delay=delay))

    async def _async_fetch_programs(self, program_type:str):
        """ Main function to fetch the different kinds of programs with their options from the cloud service """
        endpoint = f'{self._base_endpoint}/programs/{program_type}'
        response = await self._homeconnect._api.async_get(endpoint)
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
                options = await self._async_fetch_options(program_type, p['key'])
            prog.options = options

            programs[p['key']] = prog

        if program_type in ['selected', 'active'] and len(programs)==1:
            return list(programs.values())[0]
        else:
            return programs

    async def _async_fetch_status(self):
        """ Fetch the appliance status values """
        endpoint = f'{self._base_endpoint}/status'
        response = await self._homeconnect._api.async_get(endpoint)
        data = response.data
        if data is None or 'status' not in data: return {}

        res = {}
        for status in data['status']:
            res[status['key']] = status['value']
        return res

    async def _async_fetch_settings(self):
        """ Fetch the appliance settings """
        endpoint = f'{self._base_endpoint}/settings'
        response = await self._homeconnect._api.async_get(endpoint)
        data = response.data
        if data is None or 'settings' not in data: return {}

        settings = {}
        for setting in data['settings']:
            endpoint = f'{self._base_endpoint}/settings/{setting["key"]}'
            response = await self._homeconnect._api.async_get(endpoint)
            if response.status != 200:
                continue
            settings[setting['key']] = Option.create(response.data)
        return settings

    async def _async_fetch_options(self, program_type:str, program_key:str=None):
        """ Fetch detailed options of a program """

        if True or program_type=='available':
            endpoint = f"{self._base_endpoint}/programs/available/{program_key}"
        else:
            endpoint = f"{self._base_endpoint}/programs/{program_type}/options"

        response = await self._homeconnect._api.async_get(endpoint)      # This is expected to always succeed if the previous call succeeds
        data = response.data
        if data is None or 'options' not in data:
            return None

        if True or program_type=='avilable':
            return self.optionlist_to_dict(data['options'])
        # else:
        #     options = {}
        #     for option in data['options']:
        #         endpoint = f"{self._base_endpoint}/programs/available/options/{option['key']}"
        #         respnose = await self._homeconnect.api.async_get(endpoint)
        #         o = Option.create(response.data)
        #         options[o.key] = o
        #     return options


    def optionlist_to_dict(self, l:Sequence[dict]) -> dict:
        """ Helper funtion to convert a list of options into a dictionary keyd by the option "key" """
        d = {}
        for element in l:
            d[element['key']] = Option.create(element)
        return d

    #endregion