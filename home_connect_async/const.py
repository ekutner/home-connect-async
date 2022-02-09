from enum import Enum
SIM_HOST = "https://simulator.home-connect.com"
API_HOST = "https://api.home-connect.com"
ENDPOINT_AUTHORIZE = "/security/oauth/authorize"
ENDPOINT_TOKEN = "/security/oauth/token"
DEFAULT_SCOPES = [ 'IdentifyAppliance', 'Monitor', 'Control', 'Settings'  ]

class Events(str,Enum):
    """ Enum for special event types """
    DATA_REFRESHED = "DATA_REFRESHED"
    CONNECTION_CHANGED = "CONNECTION_CHANGED"
    DEFAULT = "DEFAULT"


EVENT_DATA_REFRESHED = "DATA_REFRESHED"
EVENT_CONNECTION_CHANGED = "CONNECTION_CHANGED"
