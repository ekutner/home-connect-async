from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Sequence, Callable
from dataclasses import dataclass, field
import re
from typing import Optional
from dataclasses_json import dataclass_json, Undefined, config
from home_connect_async.api import HomeConnectApi
import home_connect_async.homeconnect as homeconnect
import home_connect_async.callback_registery as callback_registery
from .const import Events
from .common import HomeConnectError, Synchronization


_LOGGER = logging.getLogger(__name__)

@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Status():
    """ Class to represent a Home Connect Status """
    key:str
    value:Optional[any] = None
    name:Optional[str] = None
    displayvalue:Optional[str] = None

    @classmethod
    def create(cls, data:dict):
        """ A factory to create a new instance from a dictionary in the Home Connect format """
        status = Status(
            key = data['key'],
            name = data.get('name'),
            value = data.get('value'),
            displayvalue= data.get('displayvalue')
        )
        return status


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Command():
    """ Class to represent a Home Connect Command """
    key:str
    name:Optional[str] = None

    @classmethod
    def create(cls, data:dict):
        """ A factory to create a new instance from a dictionary in the Home Connect format """
        status = Command(
            key = data['key'],
            name = data.get('name'),
        )
        return status

@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass
class Option():
    """ Class to represent a Home Connect Option """
    key:str
    value:Optional[any] = None
    type:Optional[str] = None
    name:Optional[str] = None
    unit:Optional[str] = None
    displayvalue:Optional[str] = None
    min:Optional[int] = None
    max:Optional[int] = None
    stepsize:Optional[int] = None
    allowedvalues:Optional[list[str]] = None
    execution:Optional[str] = None
    liveupdate:Optional[bool] = None

    @classmethod
    def create(cls, data:dict):
        """ A factory to create a new instance from a dictionary in the Home Connect format """
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
            option.execution = constraints.get('execution')
            option.liveupdate = constraints.get('liveupdate')
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
    status:dict[str, Status] = None
    settings:dict[str, Option] = None
    commands:dict[str, Command] = None
    startonly_options:dict[str, Option] = None
    startonly_program:Optional[Program] = None

    # Internal fields
    _homeconnect:Optional[homeconnect.HomeConnect] = field(default_factory=lambda: None, metadata=config(encoder=lambda val: None, decoder=lambda val: None, exclude=lambda val: True))
    _api:Optional[HomeConnectApi] = field(default=None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))
    _callbacks:Optional[callback_registery.CallbackRegistry] = field(default_factory=lambda: None, metadata=config(encoder=lambda val: None, exclude=lambda val: True))


    #region - Helper functions
    def get_applied_program(self) -> Program|None:
        """ gets the currently applied program which is the active or startonly or selected program """
        if self.active_program:
            return self.active_program
        elif self.startonly_program:
            return self.startonly_program
        elif self.selected_program:
            return self.selected_program
        else:
            return None

    def get_applied_program_available_options(self) -> dict[Option]|None:
        """ gets the available options for the applied program """
        prog = self.get_applied_program()
        if prog and self.available_programs and prog.key in self.available_programs:
            return self.available_programs[prog.key].options
        else:
            return None

    def get_applied_program_available_option(self, option_key:str) -> Option|None:
        """ gets a specific available option for the applied program """
        opts = self.get_applied_program_available_options()
        if  option_key in opts:
            return opts[option_key]
        else:
            return None

    def is_available_program(self, program_key:str) -> bool:
        """ Test if the specified program is currently available """
        return self.available_programs and program_key in self.available_programs

    def is_available_option(self, option_key:str) -> bool:
        """ Test if the specified option key is currently available for the applied program """
        opt = self.get_applied_program_available_option(option_key)
        return opt is not None

    #endregion

    #region - Manage Programs
    def set_start_option(self, option_key:str, value) -> None:
        """ Set an option that will be used when starting the program """
        if not self.startonly_options:
            self.startonly_options = {}
        if option_key not in self.startonly_options:
            self.startonly_options[option_key] = Option(option_key, value=value)
        else:
            self.startonly_options[option_key].value = value

    def clear_start_option(self, option_key:str) -> None:
        """ Clear a previously set start option """
        if self.startonly_options and option_key in self.startonly_options:
            del self.startonly_options[option_key]


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

    async def async_select_program(self, key:str=None, options:Sequence[dict]=None, program:Program=None):
        """ Set the selected program

        Parameters:
        key: The key of the program to select
        options: Additional program options to set
        program: A Program object that represents the selected program. If used then "key" is ignored.
        """

        if key is None and program is None:
            _LOGGER.error('Either "program" or "key" must be specified')
            raise HomeConnectError('Either "program" or "key" must be specified')


        if program is None:
            if  self.available_programs and self.available_programs[key]:
                program = self.available_programs[key]
            else:
                _LOGGER.error("The selected program key is not available")
                raise HomeConnectError("The selected program key is not available")


        key = program.key
        previous_program = self.startonly_program if self.startonly_program else self.selected_program
        if program.execution == 'startonly':
            self.startonly_program = program
            if previous_program.key != key:
                await self._callbacks.async_broadcast_event(self, Events.PROGRAM_SELECTED)
            return
        else:
            self.startonly_program = None

        async with Synchronization.selected_program_lock:
            res = await self._async_set_program(key, options, 'selected')
            if res and (previous_program.key != key):
                # There is a race condition between this and the selected program event
                # so check if it was alreayd update so we don't call twice
                # Note that this can't be dropped because the new options notification may arrive before the
                # program selected event and then the option values will not match the values that were there for the
                # previous program
                self.selected_program = await self._async_fetch_programs('selected')
                self.available_programs = await self._async_fetch_programs('available')
                await self._callbacks.async_broadcast_event(self, Events.PROGRAM_SELECTED)
                await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)


    async def async_start_program(self, program_key:str=None, options:Sequence[dict]=None, program:Program=None) -> bool:
        """ Started the specified program

        Parameters:
        key: The key of the program to select
        options: Additional program options to set
        program: A Program object that represents the selected program. If used then "key" is ignored.
        """
        if program is not None:
            program_key = program.key

        if not program_key:
            if self.startonly_program:
                program_key = self.startonly_program.key
            elif self.selected_program:
                program_key = self.selected_program.key
            else:
                _LOGGER.error('Either "program" or "key" must be specified')
                raise HomeConnectError('Either "program" or "key" must be specified')

        if not self.available_programs or program_key not in self.available_programs:
            _LOGGER.warning("The selected program in not one of the available programs (not supported by the API)")
            raise HomeConnectError("The specified program in not one of the available programs (not supported by the API)")

        if options is None:
            options = []
            if self.selected_program and self.available_programs and not self.startonly_program:
                for opt in self.selected_program.options.values():
                    if opt.key in self.available_programs[program_key].options and (not self.startonly_options or opt.key not in self.startonly_options):
                        option = { "key": opt.key, "value": opt.value}
                        options.append(option)
            if self.startonly_options:
                for opt in self.startonly_options.values():
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
            response = await self._api.async_delete(endpoint)
            if response.status == 204:
                return True
            elif response.error_description:
                raise HomeConnectError(response.error_description, response=response)
            raise HomeConnectError("Failed to stop the program ({response.status})", response=response)
        return False

    async def async_pause_active_program(self):
        """ Pause the active program """
        if "BSH.Common.Command.PauseProgram" in self.commands \
            and "BSH.Common.Status.OperationState" in self.status \
            and self.status["BSH.Common.Status.OperationState"].value == "BSH.Common.EnumType.OperationState.Run":
            return await self.async_send_command("BSH.Common.Command.PauseProgram", True)
        return False

    async def async_resume_paused_program(self):
        """ Resume a paused program """
        if "BSH.Common.Command.ResumeProgram" in self.commands \
            and "BSH.Common.Status.OperationState" in self.status \
            and self.status["BSH.Common.Status.OperationState"].value == "BSH.Common.EnumType.OperationState.Pause":
            return await self.async_send_command("BSH.Common.Command.ResumeProgram", True)
        return False

    async def async_send_command(self, command_key:str, value:any) -> bool:
        """ Stop the active program """
        return await self._async_set_service_value("commands", command_key, value)

    async def async_set_option(self, option_key, value) -> bool:
        """ Set a value for a specific program option """
        opt = self.get_applied_program_available_option(option_key)
        if not opt:
            _LOGGER.debug("Attempting to set unavailable option: %s", option_key)
            _LOGGER.debug(self.available_programs)
            raise ValueError("The option isn't currently available")

        if opt.execution == "startonly":
            if value:
                self.set_start_option(option_key, value)
            else:
                self.clear_start_option(option_key)
            return True

        return await self._async_set_service_value("options", option_key, value)

    async def async_apply_setting(self, setting_key, value) -> bool:
        """ Apply a global appliance setting """
        return await self._async_set_service_value("settings", setting_key, value)

    async def _async_set_service_value(self, service_type:str, key:str, value:any) -> bool:
        """ Helper function to set key/value type service properties """
        if service_type in ['settings', 'commands']:
            endpoint = f'{self._base_endpoint}/{service_type}/{key}'
        elif service_type == 'options':
            if self.active_program:
                endpoint = f'{self._base_endpoint}/programs/active/options/{key}'
            elif self.selected_program:
                endpoint = f'{self._base_endpoint}/programs/selected/options/{key}'
            else:
                raise ValueError("No active/selected program to apply the options to")
        else:
            raise ValueError(f"Unsupported service_type value: '{service_type}'")

        command = {
            "data": {
                "key": key,
                "value": value
            }
        }
        jscmd = json.dumps(command, indent=2)
        _LOGGER.debug("Calling %s with:\n%s", endpoint, jscmd)
        response = await self._api.async_put(endpoint, jscmd)
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
            response = await self._api.async_put(endpoint, jscmd)
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
        if connected != self.connected:
            self.connected = connected
            if connected:
                await self.async_fetch_data(include_static_data=False)
            await self._callbacks.async_broadcast_event(self, Events.CONNECTION_CHANGED, connected)


    #region - Handle Updates, Events and Callbacks


    async def async_update_data(self, data:dict) -> None:
        """ Update the appliance data model from a change event notification """
        key:str = data['key']
        value = data['value']

        if not self.connected:
            # an event was received for a disconnected appliance, which means we didn't get the CONNECTED event, so reload the appliace data
            await self.async_fetch_data()
            await self._callbacks.async_broadcast_event(self, Events.PAIRED)
            await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        if key == 'BSH.Common.Root.SelectedProgram' and (not self.selected_program or self.selected_program.key != value):
            # handle selected program
            async with Synchronization.selected_program_lock:
                # Have to check again after aquiring the lock
                if key == 'BSH.Common.Root.SelectedProgram' and (not self.selected_program or self.selected_program.key != value):
                    if value:
                        self.selected_program = await self._async_fetch_programs('selected')
                        self.available_programs = await self._async_fetch_programs('available')
                        await self._callbacks.async_broadcast_event(self, Events.PROGRAM_SELECTED)
                    else:
                        self.selected_program = None
                        self.available_programs = await self._async_fetch_programs('available')
                    await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif ( (key == 'BSH.Common.Root.ActiveProgram' and value) or
                # apparently it is possible to get progress notifications without getting the ActiveProgram event first so we handle that
                (key in ['BSH.Common.Option.ProgramProgress', 'BSH.Common.Option.RemainingProgramTime']) or
                # it is also possible to get operation state Run without getting the ActiveProgram event
                (key == 'BSH.Common.Status.OperationState' and value=='BSH.Common.EnumType.OperationState.Run')
            ) and not self.active_program:  #
            # handle program start
            self.active_program = await self._async_fetch_programs('active')
            self.available_programs = await self._async_fetch_programs('available')
            self.commands = await self._async_fetch_commands()
            await self._callbacks.async_broadcast_event(self, Events.PROGRAM_STARTED)
            await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif ( (key == 'BSH.Common.Root.ActiveProgram' and not value) or
               (key == 'BSH.Common.Status.OperationState' and value=='BSH.Common.EnumType.OperationState.Ready') or
               (key == 'BSH.Common.Event.ProgramFinished')
            ) and self.active_program:
            # handle program end
            self.active_program = None
            self.commands = await self._async_fetch_commands()
            self.available_programs = await self._async_fetch_programs('available')
            await self._callbacks.async_broadcast_event(self, Events.PROGRAM_FINISHED)
            await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif key == 'BSH.Common.Status.OperationState' and \
             value!='BSH.Common.EnumType.OperationState.Run' and \
             self.status.get('BSH.Common.Status.OperationState') != value:
            # ignore repeat notifiations of the same state
            await self.async_fetch_data(include_static_data=False)
            await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif key =='BSH.Common.Status.RemoteControlStartAllowed':
            self.available_programs = await self._async_fetch_programs('available')
            await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        elif ( not self.available_programs or len(self.available_programs) < 2) and \
             ( key in ['BSH.Common.Status.OperationState', 'BSH.Common.Status.RemoteControlActive'] ) and \
             ( 'BSH.Common.Status.OperationState' not in self.status or self.status['BSH.Common.Status.OperationState'].value == 'BSH.Common.EnumType.OperationState.Ready' ) and \
             ( 'BSH.Common.Status.RemoteControlActive' not in self.status or self.status['BSH.Common.Status.RemoteControlActive'].value):
            # Handle cases were the appliance data was loaded without getting all the programs (for example when HA is restarted while a program is active)
            # If the state is Ready and remote control is possible and we didn't load the available programs before then load them now
            available_programs = await self._async_fetch_programs("available")
            if available_programs:
                self.available_programs = available_programs
                await self._callbacks.async_broadcast_event(self, Events.PAIRED)
                await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)
        else:
            # update options, statuses and settings in the data model
            if self.selected_program and key in self.selected_program.options:
                self.selected_program.options[key].value = value
                self.selected_program.options[key].name = data.get('name')
                self.selected_program.options[key].displayvalue = data.get('displayvalue')
            if self.active_program and key in self.active_program.options:
                self.active_program.options[key].value = value
                self.active_program.options[key].name = data.get('name')
                self.active_program.options[key].displayvalue = data.get('displayvalue')

            if key in self.status:
                self.status[key].value = value
                self.status[key].name = data.get('name')
                self.status[key].displayvalue = data.get('displayvalue')
            elif 'uri' in data and '/status/' in data['uri']:
                self.status = await self._async_fetch_status()
                await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)

            if key in self.settings:
                self.settings[key].value = value
                self.settings[key].name = data.get('name')
                self.settings[key].displayvalue = data.get('displayvalue')
            elif 'uri' in data and '/settings/' in data['uri']:
                self.settings = await self._async_fetch_settings()
                await self._callbacks.async_broadcast_event(self, Events.DATA_CHANGED)

        # broadcast the specific event that was received
        await self._callbacks.async_broadcast_event(self, key, value)

    def register_callback(self, callback:Callable[[Appliance, str, any], None], keys:str|Sequence[str] ) -> None:
        """ Register a callback to be called when an update is received for the specified keys
            Wildcard syntax is also supported for the keys

            The key Events.CONNECTION_CHANGED will be used when the connection state of the appliance changes

            The special key "DEFAULT" may be used to catch all unhandled events
        """
        self._callbacks.register_callback(callback, keys, self)

    def deregister_callback(self, callback:Callable[[], None], keys:str|Sequence[str]) -> None:
        """ Clear a callback that was prevesiously registered so it stops getting notifications """
        self._callbacks.deregister_callback(callback, keys, self)

    def clear_all_callbacks(self):
        """ Clear all the registered callbacks """
        self._callbacks.clear_appliance_callbacks(self)


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
        appliance._callbacks = hc._callbacks
        appliance._api = hc._api

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

            self.selected_program = await self._async_fetch_programs('selected')
            self.active_program = await self._async_fetch_programs('active')
            self.settings = await self._async_fetch_settings()
            self.status = await self._async_fetch_status()
            self.commands = await self._async_fetch_commands()
            self.available_programs = await self._async_fetch_programs('available')
            # if include_static_data or not self.available_programs:
            #     if  (
            #             'BSH.Common.Status.OperationState' not in self.status
            #             or self.status['BSH.Common.Status.OperationState'].value == 'BSH.Common.EnumType.OperationState.Ready'
            #         ) and (
            #             'BSH.Common.Status.RemoteControlActive' not in self.status
            #             or self.status['BSH.Common.Status.RemoteControlActive'].value):
            #         # Only load the available programs if the state allows them to be loaded
            #         available_programs = await self._async_fetch_programs('available')
            #         if available_programs and (not self.available_programs or len(self.available_programs)<2):
            #             # Only update the available programs if we got new data
            #             self.available_programs = available_programs
            #     else:
            #         self.available_programs = None
            #         _LOGGER.debug("Not loading available programs becuase BSH.Common.Status.OperationState=%s  and BSH.Common.Status.RemoteControlActive=%s",
            #             self.status['BSH.Common.Status.OperationState'].value if 'BSH.Common.Status.OperationState' in self.status else None,
            #             str(self.status['BSH.Common.Status.RemoteControlActive'].value) if 'BSH.Common.Status.RemoteControlActive' in self.status else None )

            _LOGGER.debug("Finished loading appliance data for %s (%s)", self.name, self.haId)
            if not self.connected:
                await self.async_set_connection_state(True)
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
        response = await self._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load %s programs with error code=%d key=%s", program_type, response.status, response.error_key)
            return None
        elif not response.data:
            _LOGGER.debug("Didn't get any data for Programs: %s", program_type)
            raise HomeConnectError(msg=f"Failed to get a valid response from the Home Connect service ({response.status})", response=response)
        data = response.data

        current_program_key = self.active_program.key if self.active_program else self.selected_program.key if self.selected_program else None
        programs = {}
        if 'programs' not in data:
            # When fetching selected and active programs the parent program node doesn't exist so we force it
            data = { 'programs': [ data ] }

        for p in data['programs']:
            prog = Program.create(p)
            if 'options' in p:
                options = self.optionlist_to_dict(p['options'])
                _LOGGER.debug("Loaded %d Options for %s/%s", len(options), program_type, prog.key)
            elif program_type=='available' and (prog.key == current_program_key or prog.execution == 'startonly'):
                options = await self._async_fetch_available_options(prog.key)
            else:
                options = None
            prog.options = options

            programs[p['key']] = prog


        if program_type in ['selected', 'active'] and len(programs)==1:
            _LOGGER.debug("Loaded data for %s Program", program_type)
            return list(programs.values())[0]
        else:
            _LOGGER.debug("Loaded %d available Programs", len(programs))
            return programs

    async def _async_fetch_available_options(self, program_key:str=None):
        """ Fetch detailed options of a program """
        endpoint = f"{self._base_endpoint}/programs/available/{program_key}"
        response = await self._api.async_get(endpoint)      # This is expected to always succeed if the previous call succeeds
        if response.error_key:
            _LOGGER.debug("Failed to load Options of available/%s with error code=%d key=%s", program_key, response.status, response.error_key)
            return None
        data = response.data
        if data is None or 'options' not in data:
            _LOGGER.debug("Didn't get any data for Options of available/%s", program_key)
            return None

        options = self.optionlist_to_dict(data['options'])
        _LOGGER.debug("Loaded %d Options for available/%s", len(options), program_key)
        return options


    async def _async_fetch_status(self):
        """ Fetch the appliance status values """
        endpoint = f'{self._base_endpoint}/status'
        response = await self._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Status with error code=%d key=%s", response.status, response.error_key)
            return {}
        data = response.data
        if data is None or 'status' not in data:
            _LOGGER.debug("Didn't get any data for Status")
            return {}

        statuses = {}
        for status in data['status']:
            statuses[status['key']] = Status.create(status)

        _LOGGER.debug("Loaded %d Statuses", len(statuses))
        return statuses


    async def _async_fetch_settings(self):
        """ Fetch the appliance settings """
        endpoint = f'{self._base_endpoint}/settings'
        response = await self._api.async_get(endpoint)
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
            response = await self._api.async_get(endpoint)
            if response.status != 200:
                continue
            settings[setting['key']] = Option.create(response.data)
        _LOGGER.debug("Loaded %d Settings", len(settings))
        return settings


    async def _async_fetch_commands(self):
        """ Fetch the appliance commands """
        endpoint = f'{self._base_endpoint}/commands'
        response = await self._api.async_get(endpoint)
        if response.error_key:
            _LOGGER.debug("Failed to load Settings with error code=%d key=%s", response.status, response.error_key)
            return {}
        data = response.data
        if data is None or 'commands' not in data:
            _LOGGER.debug("Didn't get any data for Settings")
            return {}

        commands = {}
        for command in data['commands']:
            commands[command['key']] = Command.create(command)

        _LOGGER.debug("Loaded %d Commands", len(commands))
        return commands


    def optionlist_to_dict(self, options_list:Sequence[dict]) -> dict:
        """ Helper funtion to convert a list of options into a dictionary keyd by the option "key" """
        d = {}
        for element in options_list:
            d[element['key']] = Option.create(element)
        return d

    #endregion