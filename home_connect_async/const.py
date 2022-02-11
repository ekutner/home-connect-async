from enum import Enum

SIM_HOST = "https://simulator.home-connect.com"
API_HOST = "https://api.home-connect.com"
ENDPOINT_AUTHORIZE = "/security/oauth/authorize"
ENDPOINT_TOKEN = "/security/oauth/token"
DEFAULT_SCOPES = [ 'IdentifyAppliance', 'Monitor', 'Control', 'Settings'  ]

class Events(str,Enum):
    """ Enum for special event types """
    DATA_CHANGED = "DATA_CHANGED"
    CONNECTION_CHANGED = "CONNECTION_CHANGED"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    PAIRED = "PAIRED"
    DEPAIRED = "DEPAIRED"
    PROGRAM_STARTED = "PROGRAM_STARTED"
    PROGRAM_FINISHED = "PROGRAM_FINISHED"
    UNHANDLED = "UNHANDLED"


