import asyncio

from winrt.windows.devices.bluetooth import BluetoothLEDevice


ADDRESS = "DC:B4:D9:54:C6:86"


def address_to_int(address: str) -> int:
    return int(address.replace(":", ""), 16)


async def main():
    print("Direct WinRT BLE/GATT test")
    print("==========================")
    print(f"Target address: {ADDRESS}")
    print()

    bluetooth_address = address_to_int(ADDRESS)

    print(f"Numeric address: 0x{bluetooth_address:012X}")
    print()
    print("Opening BluetoothLEDevice directly through WinRT...")

    try:
        device = await asyncio.wait_for(
            BluetoothLEDevice.from_bluetooth_address_async(bluetooth_address),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        print()
        print("*** TIMEOUT ***")
        print("BluetoothLEDevice.from_bluetooth_address_async()")
        print("did not complete within 30 seconds.")
        return
    except Exception as exc:
        print()
        print("*** EXCEPTION OPENING DEVICE ***")
        print(type(exc).__name__, exc)
        return

    if device is None:
        print()
        print("*** FAILED ***")
        print("WinRT returned None for the EL30V2.")
        return

    print()
    print("*** DEVICE OBJECT OPENED ***")
    print(f"Name: {device.name}")
    print(f"Address: 0x{device.bluetooth_address:012X}")
    print()

    # Getting a BluetoothLEDevice object does not by itself prove that
    # a usable GATT connection can be established. Requesting the
    # services forces Windows farther down the actual GATT path.
    print("Requesting GATT services directly through WinRT...")
    print("This is the important connection test.")

    try:
        result = await asyncio.wait_for(
            device.get_gatt_services_async(),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        print()
        print("*** GATT TIMEOUT ***")
        print("Direct WinRT GATT service discovery did not complete")
        print("within 30 seconds.")
        device.close()
        return
    except Exception as exc:
        print()
        print("*** GATT EXCEPTION ***")
        print(type(exc).__name__, exc)
        device.close()
        return

    print()
    print("*** GATT REQUEST COMPLETED ***")
    print(f"Status: {result.status}")

    try:
        services = result.services
        print(f"Service count: {len(services)}")

        for service in services:
            print(f"  {service.uuid}")
    except Exception as exc:
        print(f"Could not enumerate returned services: {exc}")

    device.close()
    print()
    print("Test complete.")


if __name__ == "__main__":
    asyncio.run(main())