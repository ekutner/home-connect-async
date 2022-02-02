from __future__ import annotations
from dataclasses import dataclass
import logging
import asyncio
from collections.abc import Callable
from aiohttp import ClientResponse

from .auth import AbstractAuth
from .common import HomeConnectError
from .const import *

_LOGGER = logging.getLogger(__name__)


class HomeConnectApi():

    @dataclass
    class ApiResponse():
        response:ClientResponse
        status:int
        json:str
        data:any
        error:any

        def __init__(self, response:ClientResponse, json_body):
            self.response = response
            self.status = response.status
            self.json_body = json_body
            self.data = json_body['data'] if json_body and 'data' in json_body else None
            self.error = json_body['error'] if json_body and 'error' in json_body else None

        @property
        def error_key(self) -> str | None:
            if self.error and "key" in self.error:
                return self.error["key"]
            return None

        @property
        def error_description(self) -> str | None:
            if self.error and "description" in self.error:
                return self.error["description"]
            return None


    def __init__(self, auth:AbstractAuth):
        self._auth = auth


    async def _async_request(self, method, endpoint, data=None) -> ApiResponse:
        retry = 3
        response = None
        while retry:
            try:
                response = await self._auth.request(method, endpoint, data=data)
                if response.status == 429:    # Too Many Requests
                    wait_time = response.headers.get('Retry-After')
                    _LOGGER.debug('HTTP Error 429 - Too Many Requests. Sleeping for %s seconds and will retry', wait_time)
                    await asyncio.sleep(int(wait_time)+1)
                elif response.status == 401 or response.status >= 500: # Unauthorized
                    # This is probably caused by an expires token so the next retry will get a new one automatically
                    pass
                elif not response.content_length:
                    result = self.ApiResponse(response, None)
                    return result
                else:
                    result = self.ApiResponse(response, await response.json())
                    return result
            except Exception as ex:
                _LOGGER.debug("Unexpected exeption when calling HomeConnect service", exc_info=ex)
                if not retry: raise HomeConnectError("Unexpected exception when calling HomeConnect service", code=901, inner_exception=ex)
            finally:
                if response:
                    response.close()
                    response = None
            retry -= 1

        # all retries were exhausted without a valid response
        raise HomeConnectError("Failed to get a valid response from Home Connect server", 902)


    async def async_get(self, endpoint, lang='en-GB') -> ApiResponse:
        return await self._async_request('get', endpoint)

    async def async_put(self, endpoint:str, data:str, lang='en-GB') -> ApiResponse:
        return await self._async_request('put', endpoint, data=data)

    async def async_delete(self, endpoint:str) -> ApiResponse:
        return await self._async_request('delete', endpoint)

    async def async_get_event_stream(self, endpoint):
        return await self._auth.stream(endpoint)


    async def async_stream(self, endpoint:str, event_handler:Callable[[str], None]):
        backoff = 2
        event_source = None
        while True:
            try:
                event_source = await self._auth.stream(endpoint)
                await event_source.connect()
                async for event in event_source:
                    backoff = 1
                    try:
                        await event_handler(event)
                    except Exception as ex:
                        _LOGGER.exception('Unhandled exception in stream event handler', exc_info=ex)
            except asyncio.CancelledError:
                break
            except ConnectionRefusedError as ex:
                _LOGGER.exception('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
            except ConnectionError as ex:
                error_code = self.parse_sse_error(ex.args[0])
                if error_code == 429:
                    backoff *= 2
                    if backoff > 3600: backoff = 3600
                    elif backoff < 60: backoff = 60
                    _LOGGER.info('Got error 429 when opening event stream connection, will sleep for %s seconds and retry', backoff)
                else:
                    _LOGGER.exception('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                    backoff *= 2
                    if backoff > 120: backoff = 120

                await asyncio.sleep(backoff)

            except asyncio.TimeoutError:
                # it is expected that the connection will time out every hour
                _LOGGER.debug("The SSE connection timeout, will renew and retry")
                pass
            except Exception as ex:
                _LOGGER.exception('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                await asyncio.sleep(backoff)
                backoff *= 2
                if backoff > 120: backoff = 120

            finally:
                if event_source:
                    await event_source.close()
                    event_source = None


    def parse_sse_error(self, error:str) -> int:
        try:
            parts = error.split(': ')
            error_code = int(parts[-1])
            return error_code
        except:
            return 0
