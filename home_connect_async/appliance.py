from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Sequence, Callable
from dataclasses import dataclass, field
import re
from typing import Optional
from dataclasses_json import dataclass_json, Undefined, config

import home_connect_async.homeconnect as homeconnect
from .const import Events
from .common import HomeConnectError


_LOGGER = logging.getLogger(__name__)

@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Option():
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
class Program():
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


# _KT = typing.TypeVar("_KT") #  key type
# _VT = typing.TypeVar("_VT") #  value type
# class ProgramsDict(dict[str, Program]):
#     """ A custom dictionary class to handle undocumented program keys that have sub-keys """
#     def __contains__(self, key: str) -> bool:
#         if not super().__contains__(key) and isinstance(key, str):
#             key_parts = key.split('.')
#             if super().__contains__('.'.join(key_parts[:-1])):
#                 return True
#         return super().__contains__(key)

#     def __getitem__(self, __k: _KT) -> _VT:
#         if not super().__contains__(__k) and isinstance(__k, str):
#             key_parts = __k.split('.')
#             subkey = '.'.join(key_parts[:-1])
#             if super().__contains__(subkey):
#                 return super().__getitem__(subkey)

#         return super().__getitem__(__k)

#     def get(self, __key:str, __default=None, exact:bool=False):
#         key = self.contained_subkey(__key, exact)
#         if key:
#             return super().get(key)
#         return __default

#     def contains(self, key: _KT, exact:bool=False):
#         k = self.contained_subkey(key, exact)
#         return k is not None

#     def contained_subkey(self, key, exact:bool=False) -> str|None:
#         """ Get the longest valid subkey of the the passed key which is contained
#         in the dictionary or None if no such subkey exists"""
#         try_subkeys = 1 if  exact else 2
#         key_parts = key.split('.')
#         for l in range(len(key_parts), len(key_parts)-try_subkeys, -1):
#             subkey = '.'.join(key_parts[:l])
#             if super().__contains__(subkey):
#                 return subkey
#         return None

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
    #available_programs:Optional[ProgramsDict[str,Program]] = None
    available_programs:Optional[dict[str,Program]] = None
    active_program:Optional[Program] = None
    selected_program:Optional[Program] = None
    status:dict[str, any] = None
    settings:dict[str, Option] = None
    commands:dict[str, any] = None

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

    async def async_start_program(self, program_key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        """ Started the specified program

        Parameters:
        key: The key of the program to select
        options: Additional program options to set
        program: A Program object that represents the selected program. If used then "key" is ignored.
        """
        if program is not None:
            program_key = program.key

        if program_key is None and self.selected_program is not None:
            program_key = self.selected_program.key
        else:
            _LOGGER.error('Either "program" or "key" must be specified')
            return False

        if not self.available_programs or program_key not in self.available_programs:
            _LOGGER.warning("The selected program in not one of the available programs (not supported by the API)")
            return False

        if options is None and self.selected_program and self.available_programs:
            options = []
            for opt in self.selected_program.options.values():
                if opt.key in self.available_programs[program_key].options:
                    option = { "key": opt.key, "value": opt.value}
                    options.append(option)

        return await self._async_set_program(program_key, options, 'active')

    async def async_stop_active_program(self) -> bool:
        """ Stop the active program """
        if self.active_program is None:
            await self.async_get_active_program()
        if self.active_program:
            endpoint = f'{self._base_endpoint}/programs/active'
            _LOGGER.debug("Calling %s with delete verb", endpoint)
            response = await self._homeconnect._api.async_delete(endpoint)
            if response.status == 204:
                return True
            elif response.error_description:
                raise HomeConnectError(response.error_description, response=response)
            raise HomeConnectError("Failed to stop the program ({response.status})", response=response)
        return False

    async def async_pause_active_program(self):
        """ Pause the active program """
        if "BSH.Common.Command.PauseProgram" in self.commands \
            and self.status.get("BSH.Common.Status.OperationState") == "BSH.Common.EnumType.OperationState.Run":
            return await self.async_send_command("BSH.Common.Command.PauseProgram", True)
        return False

    async def async_resume_paused_program(self):
        """ Resume a paused program """
        if "BSH.Common.Command.ResumeProgram" in self.commands \
            and self.status.get("BSH.Common.Status.OperationState") == "BSH.Common.EnumType.OperationState.Pause":
            return await self.async_send_command("BSH.Common.Command.ResumeProgram", True)
        return False

    async def async_send_command(self, command_key:str, value:any) -> bool:
        """ Stop the active program """
        return await self._async_set_service_value("commands", command_key, value)

    async def async_set_option(self, option_key, value) -> bool:
        """ Set a value for a specific program option """
        return await self._async_set_service_value("options", option_key, value)

    async def async_apply_setting(self, setting_key, value) -> bool:
        """ Apply a global appliance setting """
        return await self._async_set_service_value("settings", setting_key, value)

    async def _async_set_service_value(self, service_type:str, key:str, value:any) -> bool:
        """ Helper function to set key/value type service properties """
        if service_type in ['settings', 'commands']:
            endpoint = f'{self._base_endpoint}/{service_type}/{key}'
        elif service_type == 'options':
            endpoint = f'{self._base_endpoint}/programs/selected/options/{key}'
        else:
            raise ValueError(f"Unsupported service_type value: {service_type}")

        command = {
            "data": {
                "key": key,
                "value": value
            }
        }
        jscmd = json.dumps(command, indent=2)
        _LOGGER.debug("Calling %s with:\n%s", endpoint, jscmd)
        response = await self._homeconnect._api.async_put(endpoint, jscmd)
        if response.status == 204:
            return True
        elif response.error_description:
            raise HomeConnectError(response.error_description, response=response)
        raise HomeConnectError("Failed to set service value ({response.status})", response=response)

    async def _async_set_program(self, key, options:Sequence[dict], mode:str) -> bool:
        """ Main function to handle all scenarions of setting a program """
        endpoint = f'{self._base_endpoint}/programs/{mode}'
        if options and not isinstance(options, list):
            options = [ options ]

        command = {
            "data": {
                "key": key,
                "options": []
            }
        }
        retry = True
        while retry:
            if options:
                command['data']['options'] = options

            jscmd = json.dumps(command, indent=2)
            _LOGGER.debug("Calling %s with:\n%s", endpoint, jscmd)
            response = await self._homeconnect._api.async_put(endpoint, jscmd)
            if response.status == 204:
                return True
            elif response.error_key == "SDK.Error.UnsupportedOption":
                m = re.fullmatch("Option ([^ ]*) not supported", response.error_description)
                if m:
                    bad_option = m.group(1)
                    options = [option for option in options if option['key']!= bad_option]
                else:
                    retry = False
            else:
                retry = False

        if response.error_description:
            raise HomeConnectError(response.error_description, response=response)
        raise HomeConnectError("Failed to set program ({response.status})", response=response)


    async def async_set_connection_state(self, connected:bool):
        """ Update the appliance connection state when notified about a state change from the event stream """
        self.connected = connected
        if connected:
            await self.async_fetch_data(include_static_data=False)
        await self._homeconnect._callbacks.async_broadcast_event(self, Events.CONNECTION_CHANGED, connected)


    #region - Handle Updates, Events and Callbacks

    async def async_update_data(self, key:str, value) -> None:
        """ Update the appliance data model from a change event notification """

        if key == 'BSH.Common.Root.SelectedProgram':
            self.selected_program = await self._async_fetch_programs('selected')
            await self._homeconnect._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif key == 'BSH.Common.Root.ActiveProgram':
            # self.active_program = await self._async_fetch_programs('active')
            # self.commands = await self._async_fetch_commands()
            await self._homeconnect._callbacks.async_broadcast_event(self, Events.PROGRAM_STARTED)
            #await self._homeconnect._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif key == 'BSH.Common.Event.ProgramFinished':
            self.commands = await self._async_fetch_commands()
            await self._homeconnect._callbacks.async_broadcast_event(self, Events.PROGRAM_FINISHED)
        elif key == 'BSH.Common.Status.OperationState':
            self.active_program = await self._async_fetch_programs('active')
            self.commands = await self._async_fetch_commands()

            is_new_state = self.status.get('BSH.Common.Status.OperationState') != value
            self.status[key] = value
            # if value == 'BSH.Common.EnumType.OperationState.Finished':
            #     await self._homeconnect._callbacks.async_broadcast_event(self, Events.PROGRAM_FINISHED)
            #     await self._homeconnect._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
            if value == 'BSH.Common.EnumType.OperationState.Ready':
                self.commands = await self._async_fetch_commands()  # Just for the chance we would get a different result when a program is inactive
                # Workaround for the fact the API doesn't provide all the data (such as available programs)
                # when a program is active, so if for some reason we were loaded with missing data reload it
                old_available_programs_count = len(self.available_programs) if self.available_programs else 0
                try:
                    await self.async_fetch_data(include_static_data=True)
                except HomeConnectError:
                    pass
                if old_available_programs_count != len(self.available_programs):
                    await self._homeconnect._callbacks.async_broadcast_event(self, Events.PAIRED)
            if is_new_state:
                await self._homeconnect._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
                # if not self.available_programs or len(self.available_programs) < 2:
                #     await self.async_fetch_data(include_static_data=True)
                #     await self._homeconnect._callbacks.async_broadcast_event(self, Events.PAIRED)
        else:
            # update options, statuses and settings in the data model
            if self.selected_program and key in self.selected_program.options:
                # self.selected_program.options[key].value = value
                self.selected_program = await self._async_fetch_programs('selected')
                #self.selected_program.options[key].value = value
            if self.active_program and key in self.active_program.options:
                #self.active_program.options[key].value = value
                self.active_program = await self._async_fetch_programs('active')
            if key in self.status:
                self.status[key] = value
            if key in self.settings:
                #self.settings = await self._async_fetch_settings()
                self.settings[key].value = value

        await self._homeconnect._callbacks.async_broadcast_event(self, key, value)

    def register_callback(self, callback:Callable[[Appliance, str, any], None], keys:str|Sequence[str] ) -> None:
        """ Register a callback to be called when an update is received for the specified keys
            Wildcard syntax is also supported for the keys

            The key Events.CONNECTION_CHANGED will be used when the connection state of the appliance changes

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

        Either a Events.DATA_CHANGED or Events.CONNECTION_CHANGED even will be fired after the data is updated
        """

        if delay>0:
            _LOGGER.debug("Sleeping for %ds before starting to load appliance data for %s (%s)", delay, self.name, self.haId )
            await asyncio.sleep(delay)
        try:
            _LOGGER.debug("Starting to load appliance data for %s (%s)", self.name, self.haId)
            if include_static_data:
                available_programs = await self._async_fetch_programs('available')
                if available_programs and (not self.available_programs or len(available_programs)<2):
                    # Only update the available programs if we got new data
                    self.available_programs = available_programs
                    self.commands = await self._async_fetch_commands()

            self.selected_program = await self._async_fetch_programs('selected')
            self.active_program = await self._async_fetch_programs('active')
            self.settings = await self._async_fetch_settings()
            self.status = await self._async_fetch_status()

            _LOGGER.debug("Finished loading appliance data for %s (%s)", self.name, self.haId)
            if not self.connected:
                await self.async_set_connection_state(True)
            else:
                await self._homeconnect._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        except HomeConnectError as ex:
            if ex.error_key:
                delay = delay + 60 if delay<300 else 300
                _LOGGER.debug("Got an error loading appliance data for %s (%s) code=%d error=%s - will retry in %ds", self.name, self.haId, ex.code, ex.error_key, delay)
                await self.async_set_connection_state(False)
                self._wait_for_device_task = asyncio.create_task(self.async_fetch_data(include_static_data, delay=delay))
        except Exception as ex:
            _LOGGER.debug("Unexpected exception in Appliance.async_fetch_data", exc_info=ex)
            raise HomeConnectError("Unexpected exception in Appliance.async_fetch_data", inner_exception=ex)

    async def _async_fetch_programs(self, program_type:str):
        """ Main function to fetch the different kinds of programs with their options from the cloud service """
        endpoint = f'{self._base_endpoint}/programs/{program_type}'
        response = await self._homeconnect._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Programs: %s with error code=%d key=%s", program_type, response.status, response.error_key)
            return None
        elif not response.data:
            _LOGGER.debug("Didn't get any data for Programs: %s", program_type)
            raise HomeConnectError(msg=f"Failed to get a valid response from the Home Connect service ({response.status})", response=response)
        data = response.data

        #programs = ProgramsDict()
        programs = {}
        if 'programs' not in data:
            # When fetching selected and active programs the parent program node doesn't exist so we force it
            data = { 'programs': [ data ] }

        for p in data['programs']:
            prog = Program.create(p)
            if 'options' in p:
                options = self.optionlist_to_dict(p['options'])
                _LOGGER.debug("Loaded %d Options for %s/%s", len(options), program_type, prog.key)
            else:
                options = await self._async_fetch_options(program_type, p['key'])
            prog.options = options

            programs[p['key']] = prog


        if program_type in ['selected', 'active'] and len(programs)==1:
            _LOGGER.debug("Loaded data for %s Program", program_type)
            return list(programs.values())[0]
        else:
            _LOGGER.debug("Loaded %d available Programs", len(programs))
            return programs

    async def _async_fetch_options(self, program_type:str, program_key:str=None):
        """ Fetch detailed options of a program """

        # TODO: The program_type is not really used so it may make sense to clean this code up
        if program_type=='available':
            endpoint = f"{self._base_endpoint}/programs/available/{program_key}"
        else:
            endpoint = f"{self._base_endpoint}/programs/{program_type}/options"

        response = await self._homeconnect._api.async_get(endpoint)      # This is expected to always succeed if the previous call succeeds
        if response.error_key:
            _LOGGER.debug("Failed to load Options of %s/%s with error code=%d key=%s", program_type, program_key, response.status, response.error_key)
            return None
        data = response.data
        if data is None or 'options' not in data:
            _LOGGER.debug("Didn't get any data for Options of %s/%s", program_type, program_key)
            return None


        options = self.optionlist_to_dict(data['options'])
        _LOGGER.debug("Loaded %d Options for %s/%s", len(options), program_type, program_key)
        return options

        # if program_type=='avilable':
        #     return self.optionlist_to_dict(data['options'])
        # else:
        #     options = {}
        #     for option in data['options']:
        #         endpoint = f"{self._base_endpoint}/programs/available/options/{option['key']}"
        #         respnose = await self._homeconnect.api.async_get(endpoint)
        #         o = Option.create(response.data)
        #         options[o.key] = o
        #     return options

    async def _async_fetch_status(self):
        """ Fetch the appliance status values """
        endpoint = f'{self._base_endpoint}/status'
        response = await self._homeconnect._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Status with error code=%d key=%s", response.status, response.error_key)
            return {}
        data = response.data
        if data is None or 'status' not in data:
            _LOGGER.debug("Didn't get any data for Status")
            return {}

        statuses = {}
        for status in data['status']:
            statuses[status['key']] = status['value']

        _LOGGER.debug("Loaded %d Statuses", len(statuses))
        return statuses


    async def _async_fetch_settings(self):
        """ Fetch the appliance settings """
        endpoint = f'{self._base_endpoint}/settings'
        response = await self._homeconnect._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Settings with error code=%d key=%s", response.status, response.error_key)
            return {}
        data = response.data
        if data is None or 'settings' not in data:
            _LOGGER.debug("Didn't get any data for Settings")
            return {}

        settings = {}
        for setting in data['settings']:
            endpoint = f'{self._base_endpoint}/settings/{setting["key"]}'
            response = await self._homeconnect._api.async_get(endpoint)
            if response.status != 200:
                continue
            settings[setting['key']] = Option.create(response.data)
        _LOGGER.debug("Loaded %d Settings", len(settings))
        return settings


    async def _async_fetch_commands(self):
        """ Fetch the appliance commands """
        endpoint = f'{self._base_endpoint}/commands'
        response = await self._homeconnect._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Settings with error code=%d key=%s", response.status, response.error_key)
            return {}
        data = response.data
        if data is None or 'commands' not in data:
            _LOGGER.debug("Didn't get any data for Settings")
            return {}

        commands = {}
        for command in data['commands']:
            commands[command['key']] = command['name']

        _LOGGER.debug("Loaded %d Commands", len(commands))
        return commands


    def optionlist_to_dict(self, l:Sequence[dict]) -> dict:
        """ Helper funtion to convert a list of options into a dictionary keyd by the option "key" """
        d = {}
        for element in l:
            d[element['key']] = Option.create(element)
        return d

    #endregion