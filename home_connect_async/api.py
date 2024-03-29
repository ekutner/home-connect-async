from __future__ import annotations
from dataclasses import dataclass
import logging
import asyncio
from collections.abc import Callable
from aiohttp import ClientResponse

from .auth import AbstractAuth
from .common import ConditionalLogger, HomeConnectError, HealthStatus

_LOGGER = logging.getLogger(__name__)

class HomeConnectApi():
    """ A class that provides basic API calling facilities to the Home Connect API """
    @dataclass
    class ApiResponse():
        """ Class to encapsulate a service response """
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
            """ Dynamically extract the error key from the response """
            if self.error and "key" in self.error:
                return self.error["key"]
            return None

        @property
        def error_description(self) -> str | None:
            """ Dynamically extract the error description from the response """
            if self.error and "description" in self.error:
                return self.error["description"]
            return None


    def __init__(self, auth:AbstractAuth, lang:str, health:HealthStatus):
        self._auth = auth
        self._lang = lang
        self._health = health
        self._call_counter = 0

    async def _async_request(self, method:str, endpoint:str, data=None) -> ApiResponse:
        """ Main function to call the Home Connect API over HTTPS """
        method = method.upper()
        retry = 3
        response = None
        while retry:
            try:
                self._call_counter += 1

                if ConditionalLogger.ismode(ConditionalLogger.LogMode.REQUESTS):
                    if data:
                         _LOGGER.debug("\nHTTP %s %s (try=%d count=%d)\n%s\n", method, endpoint, 4-retry, self._call_counter, data)
                    else:
                        _LOGGER.debug("\nHTTP %s %s (try=%d count=%d)\n", method, endpoint, 4-retry, self._call_counter)

                response = await self._auth.request(method, endpoint, self._lang,  data=data)


                # if self._log_mode and (self._log_mode & LogMode.REQUESTS) and (self._log_mode & LogMode.RESPONSES):
                #     _LOGGER.debug("\nHTTP RESPONSE [%d] (try=%d count=%d) ====>\n%s\n", response.status,4-retry, self._call_counter, await response.text(encoding="UTF-8"))
                #     if data:
                #         _LOGGER.debug("\nHTTP %s %s [%d] (try=%d count=%d)\n%s\nResponse ====>\n%s", method, endpoint, response.status, 4-retry, self._call_counter, data, await response.text(encoding="UTF-8"))
                #     else:
                #         _LOGGER.debug("\nHTTP %s %s [%d] (try=%d count=%d)\nResponse ====>\n%s", method, endpoint, response.status, 4-retry, self._call_counter, await response.text(encoding="UTF-8"))
                # elif self._log_mode and (self._log_mode & LogMode.REQUESTS) and data:
                #     _LOGGER.debug("\nHTTP %s %s [%d] (try=%d count=%d)\n%s", method, endpoint, response.status, 4-retry, self._call_counter, data)
                if ConditionalLogger.ismode(ConditionalLogger.LogMode.RESPONSES):
                    if response.content_length and response.content_length>0:
                        _LOGGER.debug("\nHTTP %s %s (try=%d count=%d) [%d %s] ====>\n%s\n", method, endpoint, 4-retry, self._call_counter, response.status, response.reason, await response.text(encoding="UTF-8"))
                    else:
                        _LOGGER.debug("\nHTTP %s %s (try=%d count=%d) [%d %s]\n", method, endpoint, 4-retry, self._call_counter, response.status, response.reason)
                else:
                    _LOGGER.debug("HTTP %s %s (try=%d count=%d) [%d]", method, endpoint, 4-retry, self._call_counter, response.status)
                if response.status == 429:    # Too Many Requests
                    wait_time = response.headers.get('Retry-After')
                    _LOGGER.debug('HTTP Error 429 - Too Many Requests. Sleeping for %s seconds and will retry', wait_time)
                    self._health.set_status(self._health.Status.BLOCKED, int(wait_time))
                    await asyncio.sleep(int(wait_time)+1)
                    self._health.unset_status(self._health.Status.BLOCKED)
                elif method in ["PUT", "DELETE"] and response.status == 204:
                    result = self.ApiResponse(response, None)
                    return result
                else:
                    result = self.ApiResponse(response,  await response.json(encoding='UTF-8'))
                    if result.status == 401 or result.status >= 500: # Unauthorized or service error
                        # This is probably caused by an expired token so the next retry will get a new one automatically
                        _LOGGER.debug("API got error code=%d key=%s - %d retries left", response.status, result.error_key, retry)
                    else:
                        if result.error:
                            _LOGGER.debug("API call failed with code=%d error=%s", response.status, result.error_key)
                        return result
            except Exception as ex:
                _LOGGER.debug("HTTP call failed %s %s", method, endpoint, exc_info=ex)
                if not retry:
                    raise HomeConnectError("API call to HomeConnect service failed", code=901, inner_exception=ex) from ex
            finally:
                if response:
                    response.close()
                    response = None
            retry -= 1

        # all retries were exhausted without a valid response
        raise HomeConnectError("Failed to get a valid response from Home Connect server", 902)


    async def async_get(self, endpoint) -> ApiResponse:
        """ Implements a HTTP GET request """
        return await self._async_request('GET', endpoint)

    async def async_put(self, endpoint:str, data:str) -> ApiResponse:
        """ Implements a HTTP PUT request """
        return await self._async_request('PUT', endpoint, data=data)

    async def async_delete(self, endpoint:str) -> ApiResponse:
        """ Implements a HTTP DELETE request """
        return await self._async_request('DELETE', endpoint)

    async def async_get_event_stream(self, endpoint:str, timeout:int):
        """ Returns a Server Sent Events (SSE) stream to be consumed by the caller """
        return await self._auth.stream(endpoint, self._lang, timeout)


    # async def async_stream(self, endpoint:str, timeout:int, event_handler:Callable[[str], None]):
    #     """ Implements a SSE consumer which calls the defined event handler on every new event"""
    #     backoff = 2
    #     event_source = None
    #     while True:
    #         try:
    #             event_source = await self._auth.stream(endpoint, self._lang, timeout)
    #             await event_source.connect()
    #             async for event in event_source:
    #                 backoff = 1
    #                 try:
    #                     await event_handler(event)
    #                 except Exception as ex:
    #                     _LOGGER.exception('Unhandled exception in stream event handler', exc_info=ex)
    #         except asyncio.CancelledError:
    #             break
    #         except ConnectionRefusedError as ex:
    #             _LOGGER.exception('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
    #         except ConnectionError as ex:
    #             error_code = self.parse_sse_error(ex.args[0])
    #             if error_code == 429:
    #                 backoff *= 2
    #                 if backoff > 3600: backoff = 3600
    #                 elif backoff < 60: backoff = 60
    #                 _LOGGER.info('Got error 429 when opening event stream connection, will sleep for %s seconds and retry', backoff)
    #             else:
    #                 _LOGGER.exception('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
    #                 backoff *= 2
    #                 if backoff > 120: backoff = 120

    #             await asyncio.sleep(backoff)

    #         except asyncio.TimeoutError:
    #             # it is expected that the connection will time out every hour
    #             _LOGGER.debug("The SSE connection timeout, will renew and retry")
    #         except Exception as ex:
    #             _LOGGER.exception('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
    #             await asyncio.sleep(backoff)
    #             backoff *= 2
    #             if backoff > 120: backoff = 120

    #         finally:
    #             if event_source:
    #                 await event_source.close()
    #                 event_source = None


    # def parse_sse_error(self, error:str) -> int:
    #     """ Helper function to parse the error code from a SSE exception """
    #     try:
    #         parts = error.split(': ')
    #         error_code = int(parts[-1])
    #         return error_code
    #     except:
    #         return 0
