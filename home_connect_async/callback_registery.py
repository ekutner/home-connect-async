from __future__ import annotations
import fnmatch
import inspect
import re
from typing import Callable
from collections.abc import Sequence

from .appliance import Appliance


WILDCARD_KEY = "WILDCARD"
DEFAULT_KEY = "DEFAULT"
class CallbackRegistry():
    """ Calss for managing callback registration and notifications """

    def __init__(self) -> None:
        self._callbacks = {}

    def register_callback(self, callback:Callable[[Appliance, str], None] | Callable[[Appliance, str, any], None], keys:str|Sequence[str], appliance:Appliance|str = None):
        """ Register callback for change event notifications

        Use the Appliance.register_callback() to register for appliance data update events
        """

        if not isinstance(keys, list):
            keys = [ keys ]

        haid = appliance.haId if isinstance(appliance, Appliance) else appliance

        if haid not in self._callbacks:
            self._callbacks[haid] = {}

        for key in keys:
            if '*' in key:
                callback_record = {
                    "key": re.compile(fnmatch.translate(key), re.IGNORECASE),
                    "callback": callback
                }
                if WILDCARD_KEY not in self._callbacks[haid]:
                    self._callbacks[haid][WILDCARD_KEY] = []
                self._callbacks[haid][WILDCARD_KEY].append(callback_record)
            else:
                if key not in self._callbacks[haid]:
                    self._callbacks[haid][key] = set()
                self._callbacks[haid][key].add(callback)

    def deregister_callback(self,
        callback:Callable[[Appliance], None] | Callable[[Appliance, str], None] | Callable[[Appliance, str, any], None],
        keys:str|Sequence[str],
        appliance:Appliance|str = None
    ):
        """ Clear a callback that was prevesiously registered so it stops getting notifications """

        if not isinstance(keys, list):
            keys = [ keys ]

        haid = appliance.haId if isinstance(appliance, Appliance) else appliance

        if haid not in self._callbacks:
            self._callbacks[haid] = {}

        for key in keys:
            if '*' in key:
                callback_record = {
                    "key": re.compile(fnmatch.translate(key), re.IGNORECASE),
                    "callback": callback
                }
                try:
                    if haid in self._callbacks and WILDCARD_KEY in self._callbacks[haid]:
                        self._callbacks[haid][WILDCARD_KEY].remove(callback_record)
                except ValueError:
                    # ignore if the value is not found in the list
                    pass
            else:
                if haid in self._callbacks and key in self._callbacks[haid]:
                    self._callbacks[haid][key].remove(callback)

    def clear_all_callbacks(self):
        """ Clear all the registered callbacks """
        self._callbacks = {}

    def clear_appliance_callbacks(self, appliance:Appliance|str):
        """ Clear all the registered callbacks """
        haid = appliance.haId if isinstance(appliance, Appliance) else appliance

        if haid in self._callbacks:
            del self._callbacks[haid]

    async def async_broadcast_event(self, appliance:Appliance, event_key:str, value:any = None) -> None:
        """ Broadcast an event to all subscribed callbacks """

        handled:bool = False
        haid = appliance.haId

        # dispatch simple event callbacks
        for haid in [ handler for handler in [None, appliance.haId] if handler in self._callbacks] :
            if event_key in self._callbacks[haid]:
                for callback in self._callbacks[haid][event_key]:
                    await self._async_call(callback, appliance, event_key, value)
                    handled = True

            # dispatch wildcard or value based callbacks
            if WILDCARD_KEY in self._callbacks[haid]:
                for callback_record in self._callbacks[haid][WILDCARD_KEY]:
                    if callback_record["key"].fullmatch(event_key):
                        callback = callback_record['callback']
                        await self._async_call(callback, appliance, event_key, value)
                        handled = True

            # dispatch default callbacks for unhandled events
            if not handled and DEFAULT_KEY in self._callbacks[haid]:
                for callback in self._callbacks[DEFAULT_KEY]:
                    self._async_call(callback, appliance, event_key, value)


    async def _async_call(self, callback:Callable, appliance:Appliance, event_key:str, value:any) -> None:
        """ Helper funtion to make the right kind of call to the callback funtion """
        sig = inspect.signature(callback)
        param_count = len(sig.parameters)
        if inspect.iscoroutinefunction(callback):
            if param_count == 3:
                await callback(appliance, event_key, value)
            elif param_count == 2:
                await callback(appliance, event_key)
            elif param_count == 1:
                await callback(appliance)
            elif param_count == 0:
                await callback()
            else:
                raise ValueError(f"Unexpected number of callback parameters: {sig}")
        else:
            if param_count == 3:
                callback(appliance, event_key, value)
            elif param_count == 2:
                callback(appliance, event_key)
            elif param_count == 1:
                callback(appliance)
            elif param_count == 0:
                callback()
            else:
                raise ValueError(f"Unexpected number of callback parameters: {sig}")
