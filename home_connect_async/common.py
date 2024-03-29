""" Common classes shared across the code """

import asyncio
from datetime import datetime, timedelta
from enum import IntFlag
from logging import Logger

class ConditionalLogger:
    """ Class for conditional logging based on the log mode """
    class LogMode(IntFlag):
        """ Enum to control special logging """
        NONE = 0
        VERBOSE = 1
        REQUESTS = 2
        RESPONSES = 4

    _log_flags:LogMode = None

    @classmethod
    def mode(self, log_flags:LogMode=None) -> LogMode:
        """ Gets or Sets the log flags for conditional logging """
        if log_flags:
            self._log_flags = log_flags
        return self._log_flags


    @classmethod
    def ismode(self, logmode:LogMode) -> bool:
        """ Check if the specified logmode is enabled """
        return self._log_flags & logmode

    @classmethod
    def debug(self, logger:Logger, logmode:LogMode, *args, **kwargs ) -> None:
        """ Conditional debug log """
        if self._log_flags & logmode:
            logger.debug(*args, **kwargs)


class HomeConnectError(Exception):
    """ Common exception class for the SDK """
    def __init__(self, msg:str = None, code:int = None, response = None, inner_exception = None):
        self.msg:str = msg
        self.code:int = code
        self.response = response
        self.inner_exception = inner_exception
        if response:
            self.error_key:str = response.error_key
            self.error_description:str = response.error_description
            if not code: self.code = response.status
        else:
            self.error_key = None
            self.error_description = None

        super().__init__(msg, code, self.error_key, self.error_description, inner_exception)


class Synchronization():
    """ Class to hold global syncronization objects """
    selected_program_lock = asyncio.Lock()


class HealthStatus:
    """ Store the Home Connect connection health status """
    class Status(IntFlag):
        """ Enum for the current status of the Home Connect data loading process """
        INIT = 0
        RUNNING = 1
        LOADED = 3
        UPDATES = 4
        UPDATES_NO_DATA = 5
        READY = 7
        LOADING_FAILED = 8
        BLOCKED = 16

    def __init__(self) -> None:
        self._status:self.Status = self.Status.INIT
        self._blocked_until:datetime = None

    def set_status(self, status:Status, delay:int=None) -> None:
        """ Set the status """
        self._status |= status
        if delay:
            self._blocked_until = datetime.now() + timedelta(seconds=delay)

    def unset_status(self, status:Status) -> None:
        """ Set the status """
        self._status &= ~status
        if status == self.Status.BLOCKED:
            self._blocked_until = None

    def get_status(self) -> Status:
        """ Get the status """
        if self._status & self.Status.BLOCKED:
            return self.Status.BLOCKED
        elif self._status & self.Status.LOADING_FAILED:
            return self.Status.LOADING_FAILED
        return self._status

    def get_status_str(self) -> str:
        """ Return the status as a formatted string"""
        if self._blocked_until:
            return f"Blocked for {self.get_block_time_str()}"
        elif self._status & self.Status.LOADING_FAILED:
            return self.Status.LOADING_FAILED.name
        else:
            return self._status.name

    def get_blocked_until(self):
        return self._blocked_until

    def get_block_time_str(self):
        if self._blocked_until:
            delta = (self._blocked_until - datetime.now()).seconds
            if delta < 60:
                return f"{delta}s"
            else:
                hours = delta //3600
                minutes = (delta - hours*3600) // 60
                return f"{hours}:{minutes:02}h"
        else:
            return None




