""" Common classes shared across the code """

import asyncio
from datetime import datetime, timedelta
from enum import IntFlag


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
            return "BLOCKED"
        elif cls._status & cls.Status.LOADING_FAILED:
            return "LOADING_FAILED"
        return cls._status

    @classmethod
    def get_status_str(cls) -> str:
        """ Return the status as a formatted string"""
        if cls._blocked_until:
            delta = (cls._blocked_until - datetime.now()).seconds
            if delta < 60:
                return f"Blocked for {delta}s"
            else:
                hours = delta //3600
                minutes = (delta - hours*3600) // 60
                return f"Blocked for {hours}:{minutes:02}h"
        else:
            return str(cls._status.name)





