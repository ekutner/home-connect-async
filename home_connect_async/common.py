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
    def mode(cls, log_flags:LogMode=None) -> LogMode:
        """ Gets or Sets the log flags for conditional logging """
        if log_flags:
            cls._log_flags = log_flags
        return cls._log_flags


    @classmethod
    def ismode(cls, logmode:LogMode) -> bool:
        """ Check if the specified logmode is enabled """
        return cls._log_flags & logmode

    @classmethod
    def debug(cls, logger:Logger, logmode:LogMode, *args, **kwargs ) -> None:
        """ Conditional debug log """
        if cls._log_flags & logmode:
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


class GlobalStatus:
    """ Store a global status for the library """
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

    _status:Status = Status.INIT
    _blocked_until:datetime = None

    @classmethod
    def set_status(cls, status:Status, delay:int=None) -> None:
        """ Set the status """
        cls._status |= status
        if delay:
            cls._blocked_until = datetime.now() + timedelta(seconds=delay)

    @classmethod
    def unset_status(cls, status:Status) -> None:
        """ Set the status """
        cls._status &= ~status
        if status == cls.Status.BLOCKED:
            cls._blocked_until = None

    @classmethod
    def get_status(cls) -> Status:
        """ Get the status """
        if cls._status & cls.Status.BLOCKED:
            return cls.Status.BLOCKED
        elif cls._status & cls.Status.LOADING_FAILED:
            return cls.Status.LOADING_FAILED
        return cls._status

    @classmethod
    def get_status_str(cls) -> str:
        """ Return the status as a formatted string"""
        if cls._blocked_until:
            return f"Blocked for {cls.get_block_time_str()}"
        elif cls._status & cls.Status.LOADING_FAILED:
            return cls.Status.LOADING_FAILED.name
        else:
            return cls._status.name

    @classmethod
    def get_blocked_until(cls):
        return cls._blocked_until

    @classmethod
    def get_block_time_str(cls):
        if cls._blocked_until:
            delta = (cls._blocked_until - datetime.now()).seconds
            if delta < 60:
                return f"{delta}s"
            else:
                hours = delta //3600
                minutes = (delta - hours*3600) // 60
                return f"{hours}:{minutes:02}h"
        else:
            return None




