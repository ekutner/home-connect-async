from __future__ import annotations
from abc import ABC, abstractmethod
import imp
import webbrowser
import logging
import asyncio
from wsgiref import headers
import aiohttp
from aiohttp import ClientSession, ClientResponse
from aiohttp_sse_client import client as sse_client
from oauth2_client.credentials_manager import CredentialManager, ServiceInformation
from datetime import datetime, timedelta

from .const import *

_LOGGER = logging.getLogger(__name__)

# This is for compatability with Home Assistant
class AbstractAuth(ABC):
    """Abstract class to make authenticated requests."""

    def __init__(self, websession: ClientSession, host: str):
        """Initialize the auth."""
        self.websession = websession
        self.host = host

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""

    async def request(self, method, endpoint:str, **kwargs) -> ClientResponse:
        """Make a request."""
        headers = kwargs.get("headers")

        if headers is None:
            headers = {}
        else:
            headers = dict(headers)

        access_token = await self.async_get_access_token()
        headers['authorization'] = f'Bearer {access_token}'
        headers['Accept'] = 'application/vnd.bsh.sdk.v1+json'
        headers['Accept-Language'] = 'en-GB'
        if method == 'put':
            headers['Content-Type'] = 'application/vnd.bsh.sdk.v1+json'

        return await self.websession.request(
            method, f"{self.host}{endpoint}", **kwargs, headers=headers,
        )

    async def stream(self, endpoint:str, message_handler=None, **kwargs) -> sse_client.EventSource:
        # headers = {}
        # backoff = 2
        # while True:
        #     try:
        #         access_token = await self.async_get_access_token()
        #         headers['authorization'] = f'Bearer {access_token}'
        #         headers['Accept'] = 'application/vnd.bsh.sdk.v1+json'
        #         headers['Accept-Language'] = 'en-GB'

        #         #timeout = aiohttp.ClientTimeout(total = ( self._auth.access_token_expirs_at - datetime.now() ).total_seconds() )
        #         timeout = aiohttp.ClientTimeout(total = timedelta(seconds=3600) )
        #         async with sse_client.EventSource(endpoint, session=self.websession, timeout=timeout) as event_source:
        #             async for event in event_source:
        #                 backoff = 2
        #                 try:
        #                     await on_message_handler(event)
        #                 except Exception as ex:
        #                     _LOGGER.exception('Unhandled exception in event handler', exc_info=ex)
        #     except asyncio.CancelledError:
        #         break
        #     except ConnectionRefusedError as ex:
        #         _LOGGER.exception('ConnectionRefusedError in SSE connection refused. Will try again', exc_info=ex)
        #     except ConnectionError as ex:
        #         _LOGGER.exception('ConnectionError in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
        #         await asyncio.sleep(backoff)
        #         if backoff < 120:
        #             backoff *= 2
        #     except Exception as ex:
        #         _LOGGER.exception('Exception in SSE event stream. Will wait for %d seconds and retry ', backoff, exc_info=ex)
        #         await asyncio.sleep(backoff)
        #         if backoff < 120:
        #             backoff *= 2
        headers = {}
        access_token = await self.async_get_access_token()
        headers['authorization'] = f'Bearer {access_token}'
        headers['Accept'] = 'application/vnd.bsh.sdk.v1+json'
        headers['Accept-Language'] = 'en-GB'
        #timeout = aiohttp.ClientTimeout(total = ( self._auth.access_token_expirs_at - datetime.now() ).total_seconds() )
        timeout = aiohttp.ClientTimeout(total = 3600 )
        return sse_client.EventSource(f"{self.host}{endpoint}", session=self.websession, headers=headers, timeout=timeout)


class AuthManager(AbstractAuth):

    def __init__(self, client_id, client_secret, scopes=None, simulate=False):
        host = SIM_HOST if simulate else API_HOST
        session = ClientSession()
        super().__init__(session, host)

        if scopes is None: scopes = DEFAULT_SCOPES
        service_information = ServiceInformation(
            f'{host}{ENDPOINT_AUTHORIZE}',
            f'{host}{ENDPOINT_TOKEN}',
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes
        )
        self._cm = HomeConnectCredentialsManager(service_information)

    async def async_get_access_token(self) -> str:
        return self.get_access_token()

    def renew_token(self):
        self._cm.init_with_token(self.refresh_token)

    def get_access_token(self):
        if self._cm.access_token_expirs_at and datetime.now() > self._cm.access_token_expirs_at:
            self.renew_token()
        return self._cm._access_token

    access_token = property(get_access_token)
    access_token_expirs_at = property(lambda self: self._cm.access_token_expirs_at)
    refresh_token = property(
        lambda self: self._cm.refresh_token,
        lambda self, token: self._cm.init_with_token(token)
    )

    def login(self, redirect_url:str=None):
        if redirect_url is None:
            redirect_url = 'http://localhost:7878/auth'

        # Builds the authorization url and starts the local server according to the redirect_uri parameter
        url = self._cm.init_authorize_code_process(redirect_url, state='ignore')
        webbrowser.open(url)

        code = self._cm.wait_and_terminate_authorize_code_process()
        # From this point the http server is opened on the specified port and waits to receive a single GET request
        _LOGGER.debug('Code got = %s', code)
        self._cm.init_with_authorize_code(redirect_url, code)
        _LOGGER.debug('Access got = %s', self.access_token)

    async def close(self):
        await self.websession.close()


# Extend the CredentialManager class so we can capture the token expiration time
class HomeConnectCredentialsManager(CredentialManager):
    def _process_token_response(self, token_response: dict, refresh_token_mandatory: bool):
        self._raw_token = token_response
        if 'expires_in' in token_response:
            self.access_token_expirs_at = datetime.now() + timedelta(seconds=token_response['expires_in'])
        else:
            self.access_token_expirs_at = None
        self.id_token = token_response.get('id_token')
        return super()._process_token_response(token_response, refresh_token_mandatory)


