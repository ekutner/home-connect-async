import asyncio
import logging
import os
from aioconsole import ainput
from home_connect_async import HomeConnect, AuthManager, Appliance

logging.basicConfig(level=logging.DEBUG)

REFRESH_TOKEN_FILE = 'examples/refresh_token.txt'
APPLIANCES_DATA_FILE = 'examples/appliances_data.json'

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')

async def event_handler(appliance:Appliance, key:str, value) -> None:
    print(f"{appliance.name} -> {key}: {value}" )


async def main():

    refresh_token = None
    am = AuthManager(CLIENT_ID, CLIENT_SECRET, simulate=True)
    if os.path.exists(REFRESH_TOKEN_FILE):
        with open(REFRESH_TOKEN_FILE, 'r') as f:
            refresh_token = f.readline()
            am.refresh_token = refresh_token
    else:
        am.login()
        refresh_token = am.refresh_token
        with open(REFRESH_TOKEN_FILE, 'w+') as f:
            f.write(refresh_token)

    js = None
    if os.path.exists(APPLIANCES_DATA_FILE):
        with open(APPLIANCES_DATA_FILE, 'r') as file:
            js = file.read()

    hc = await HomeConnect.create(am, json_data=js)


    if js is None:
        js = hc.to_json(indent=2)
        with open(APPLIANCES_DATA_FILE, 'w+') as file:
            file.write(js)
    for appliance in hc.appliances.values():
        appliance.register_callback(event_handler, 'BSH.Common.Status.DoorState' )
    hc.subscribe_for_updates()
    exit = False
    while not exit:
        line = await ainput()
        if line == 'exit': exit=True
        elif line == 'active':
            p = await hc.get_appliance('SIEMENS-HCS02DWH1-D1349B55F7EC').get_active_program()
            if p is not None:
                print(p.to_json(indent=2))
            else:
                print("No active program")
        elif line == 'selected':
            p = await hc.get_appliance('SIEMENS-HCS02DWH1-D1349B55F7EC').get_selected_program()
            if p is not None:
                print(p.to_json(indent=2))
            else:
                print("No selected program")

    hc.close()
    await am.close()


asyncio.get_event_loop().run_until_complete(main())
