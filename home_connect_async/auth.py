from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional
import webbrowser
import logging
import aiohttp
from aiohttp import ClientSession, ClientResponse
from aiohttp_sse_client import client as sse_client
from oauth2_client.credentials_manager import CredentialManager, ServiceInformation


from .const import SIM_HOST, API_HOST, DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN

_LOGGER = logging.getLogger(__name__)

# This is for compatability with Home Assistant
class AbstractAuth(ABC):
    """Abstract class to make authenticated requests. This is a pattern required by Home Assistant """

    def __init__(self, websession: ClientSession, host: str):
        """Initialize the auth."""
        self.websession = websession
        self.host = host

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""

    async def request(self, method, endpoint:str, lang:str=None, **kwargs) -> ClientResponse:
        """Make a request."""
        headers = kwargs.get("headers")

        if headers is None:
            headers = {}
        else:
            headers = dict(headers)

        access_token = await self.async_get_access_token()
        headers['authorization'] = f'Bearer {access_token}'
        headers['Accept'] = 'application/vnd.bsh.sdk.v1+json'
        if lang:
            headers['Accept-Language'] = lang
        if method == 'put':
            headers['Content-Type'] = 'application/vnd.bsh.sdk.v1+json'

        return await self.websession.request(
            method, f"{self.host}{endpoint}", **kwargs, headers=headers,
        )

    async def stream(self, endpoint:str, lang:str=None, **kwargs) -> sse_client.EventSource:
        """ Initiate a SSE stream """
        headers = {}
        access_token = await self.async_get_access_token()
        headers['authorization'] = f'Bearer {access_token}'
        headers['Accept'] = 'application/vnd.bsh.sdk.v1+json'
        if lang:
            headers['Accept-Language'] = lang
        #timeout = aiohttp.ClientTimeout(total = ( self._auth.access_token_expirs_at - datetime.now() ).total_seconds() )
        timeout = aiohttp.ClientTimeout(total = 900 )
        return sse_client.EventSource(f"{self.host}{endpoint}", session=self.websession, headers=headers, timeout=timeout, **kwargs)


class AuthManager(AbstractAuth):
    """ Class the implements a full fledged authentication manager when the SDK is not being used by Home Assistant """
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

    def renew_token(self):
        """ Renews the access token using the stored refresh token """
        self._cm.init_with_token(self.refresh_token)

    def get_access_token(self):
        """ Gets an access token """
        if self._cm.access_token_expirs_at and datetime.now() > self._cm.access_token_expirs_at:
            self.renew_token()
        return self._cm._access_token

    async def async_get_access_token(self) -> str:
        """ Gets an access token """
        return self.get_access_token()


    access_token = property(get_access_token)
    access_token_expirs_at = property(lambda self: self._cm.access_token_expirs_at)
    refresh_token = property(
        lambda self: self._cm.refresh_token,
        lambda self, token: self._cm.init_with_token(token)
    )

    def login(self, redirect_url:str=None):
        """ Login to the Home Connect service using the code flow of OAuth 2 """
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
        """ Close the authentication manager when it is no longer in use """
        await self.websession.close()


# Extend the CredentialManager class so we can capture the token expiration time
class HomeConnectCredentialsManager(CredentialManager):
    """ Extend the oauth2_client library CredentialManager to handle and store the received token """

    def __init__(self, service_information: ServiceInformation, proxies: Optional[dict] = None):
        super().__init__(service_information, proxies)
        self._raw_token = None
        self.access_token_expirs_at = None
        self.id_token = None

    def _process_token_response(self, token_response: dict, refresh_token_mandatory: bool):
        """ Override the parent's method to handle the extra data we care about """
        self._raw_token = token_response
        if 'expires_in' in token_response:
            self.access_token_expirs_at = datetime.now() + timedelta(seconds=token_response['expires_in'])
        else:
            self.access_token_expirs_at = None
        self.id_token = token_response.get('id_token')
        return super()._process_token_response(token_response, refresh_token_mandatory)


