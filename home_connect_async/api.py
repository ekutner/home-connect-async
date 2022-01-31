import logging
import asyncio
from collections.abc import Callable

from .auth import AbstractAuth
from .const import *

_LOGGER = logging.getLogger(__name__)


class HomeConnectAPI():

    def __init__(self, auth:AbstractAuth):
        self._auth = auth

    async def _async_request(self, method, endpoint, data=None):
        retry = 3
        response = None
        while retry:
            try:
                response = await self._auth.request(method, endpoint, data=data)
                if method=='get' and response.status == 200:
                    json_body = await response.json()
                    return json_body['data']
                elif response.status == 204:
                    return True
                elif response.status == 429:    # Too Many Requests
                    wait_time = response.headers.get('Retry-After')
                    _LOGGER.debug('HTTP Error 429 - Too Many Requests. Sleeping for %s seconds and will retry', wait_time)
                    await asyncio.sleep(int(wait_time)+1)
                elif method=='get' and response.status in [404, 409]:
                    # This is expected because some appliances don't have active programs or programs at all
                    return None
                elif response.status == 401: # Unauthorized
                    # This is probably caused by an expires token so the next retry will get a new one automatically
                    pass
                else:
                    _LOGGER.info('HTTP Error %d when calling %s : %s ', response.status, response.url, await response.text())
                    return None
            except Exception as ex:
                _LOGGER.exception("Unexpected exeption when calling HomeConnect service", exc_info=ex)
            finally:
                if response:
                    response.close()
                    response = None
            retry -= 1
        _LOGGER.error('Failed to get a valid response after 3 retries')
        return None


    async def async_get(self, endpoint, lang='en-GB'):
        return await self._async_request('get', endpoint)

    async def async_put(self, endpoint:str, data:str, lang='en-GB') -> bool:
        return await self._async_request('put', endpoint, data=data)


    async def async_delete(self, endpoint:str) -> bool:
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
