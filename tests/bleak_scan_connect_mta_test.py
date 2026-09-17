import asyncio

from bleak.backends.winrt.util import (
    assert_mta,
    uninitialize_sta,
)

print("Calling uninitialize_sta()...")
uninitialize_sta()

from bleak import BleakClient, BleakScanner


ADDRESS = "DC:B4:D9:54:C6:86"


async def main():
    print("Checking Windows COM apartment...")
    await assert_mta()
    print("MTA check passed.")

    print("Scanning specifically for the EL30V2...")

    device = await BleakScanner.find_device_by_address(
        ADDRESS,
        timeout=15.0,
    )

    if device is None:
        print("EL30V2 was not found.")
        return

    print(f"Found: {device.address} {device.name}")
    print("Connecting using discovered BLEDevice...")

    client = BleakClient(device, timeout=30.0)

    try:
        await client.connect()

        print(f"Connected={client.is_connected}")

        if client.is_connected:
            print("SUCCESS: raw Bleak connection completed.")

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc!r}")

    finally:
        if client.is_connected:
            print("Disconnecting...")
            await client.disconnect()

        print(f"Final connected state={client.is_connected}")


if __name__ == "__main__":
    asyncio.run(main())