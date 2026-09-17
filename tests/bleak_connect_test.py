import asyncio
from bleak import BleakClient


ADDRESS = "DC:B4:D9:54:C6:86"


async def main():
    print(f"Connecting directly with Bleak to {ADDRESS}...")

    client = BleakClient(ADDRESS, timeout=30.0)

    try:
        await client.connect()

        print(f"Connected={client.is_connected}")

        services = client.services
        print(f"Service count={len(services.services)}")

        for service in services:
            print(service.uuid)

    finally:
        if client.is_connected:
            await client.disconnect()

        print(f"Disconnected={not client.is_connected}")


if __name__ == "__main__":
    asyncio.run(main())