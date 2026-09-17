import asyncio
from bleak import BleakClient, BleakScanner


ADDRESS = "DC:B4:D9:54:C6:86"


async def main():
    print("Scanning specifically for the EL30V2...")

    device = await BleakScanner.find_device_by_address(
        ADDRESS,
        timeout=15.0,
    )

    if device is None:
        print("EL30V2 was not found.")
        return

    print(f"Found: {device.address} {device.name}")
    print("Connecting using the discovered BLEDevice object...")

    client = BleakClient(device, timeout=30.0)

    try:
        await asyncio.wait_for(
            client.connect(),
            timeout=35.0,
        )

        print(f"Connected={client.is_connected}")

        if client.is_connected:
            print("Raw Bleak connection succeeded.")

    except asyncio.TimeoutError:
        print("TIMEOUT: Bleak connect did not complete within 35 seconds.")

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")

    finally:
        if client.is_connected:
            print("Disconnecting...")
            await client.disconnect()

        print(f"Final connected state={client.is_connected}")


if __name__ == "__main__":
    asyncio.run(main())