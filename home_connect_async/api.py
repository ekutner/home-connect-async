import logging
import asyncio

from .auth import AbstractAuth
from .const import *

_LOGGER = logging.getLogger(__name__)


class HomeConnectAPI():

    def __init__(self, auth:AbstractAuth):
        self._auth = auth

    async def _request(self, method, endpoint, data=None):
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
                if method=='get' and response.status in [404, 409]:
                    # This is expected because some appliances don't have active programs or programs at all
                    return None
                elif response.status == 401: # Unauthorized
                    # This is probably caused by an expires token so the next retry will get a new one automatically
                    pass
                else:
                    _LOGGER.info('HTTP Error %d when calling %s : %s ', response.status, response.url, await response.text())
                    return None
            except:
                pass
            finally:
                if response:
                    response.close()
                    response = None
            retry -= 1
        _LOGGER.error('Failed to get a valid response after 3 retries')
        return None


    async def get(self, endpoint, lang='en-GB'):
        return await self._request('get', endpoint)

    async def put(self, endpoint:str, data:str, lang='en-GB') -> bool:
        return await self._request('put', endpoint, data=data)


    async def delete(self, endpoint:str) -> bool:
        return await self._request('delete', endpoint)

    async def stream(self, endpoint, message_handler):
        backoff = 2
        event_source = None
        while True:
            try:
                event_source = await self._auth.stream(endpoint)
                await event_source.connect()
                async for event in event_source:
                    backoff = 2
                    try:
                        await message_handler(event)
                    except Exception as ex:
                        _LOGGER.exception('Unhandled exception in event handler', exc_info=ex)
            except asyncio.CancelledError:
                break
            except ConnectionRefusedError as ex:
                _LOGGER.exception('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
            except ConnectionError as ex:
                _LOGGER.exception('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                await asyncio.sleep(backoff)
                if backoff < 120:
                    backoff *= 2
            except asyncio.TimeoutError:
                # it is expected that the connection will time out every hour
                _LOGGER.debug("The SSE connection timeout, will renew and retry")
                pass
            except Exception as ex:
                _LOGGER.exception('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
                await asyncio.sleep(backoff)
                if backoff < 120:
                    backoff *= 2
            finally:
                if event_source:
                    await event_source.close()
                    event_source = None