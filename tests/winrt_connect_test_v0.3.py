import asyncio

from winrt.windows.devices.bluetooth import BluetoothLEDevice
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
)


ADDRESS = "DC:B4:D9:54:C6:86"
TARGET_ADDRESS = int(ADDRESS.replace(":", ""), 16)


async def scan_for_target(timeout=15.0):
    print("Scanning with the native WinRT advertisement watcher...")

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE

    found_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def received(sender, args):
        address = args.bluetooth_address

        if address == TARGET_ADDRESS:
            print()
            print("*** TARGET ADVERTISEMENT FOUND ***")
            print(f"Address: 0x{address:012X}")

            try:
                local_name = args.advertisement.local_name
                print(f"Name:    {local_name!r}")
            except Exception:
                pass

            loop.call_soon_threadsafe(found_event.set)

    token = watcher.add_received(received)

    try:
        watcher.start()

        try:
            await asyncio.wait_for(found_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    finally:
        watcher.stop()
        watcher.remove_received(token)


async def main():
    print("Direct WinRT BLE/GATT test v0.3")
    print("===============================")
    print(f"Target address: {ADDRESS}")
    print()

    found = await scan_for_target()

    if not found:
        print()
        print("*** NOT FOUND ***")
        print("WinRT did not see an advertisement from the EL30V2.")
        return

    print()
    print("Advertisement scan populated the Windows BLE cache.")
    print()
    print("Opening BluetoothLEDevice directly through WinRT...")

    try:
        device = await asyncio.wait_for(
            BluetoothLEDevice.from_bluetooth_address_async(TARGET_ADDRESS),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        print()
        print("*** DEVICE OPEN TIMEOUT ***")
        return
    except Exception as exc:
        print()
        print("*** DEVICE OPEN EXCEPTION ***")
        print(type(exc).__name__, exc)
        return

    if device is None:
        print()
        print("*** FAILED AFTER SCAN ***")
        print("WinRT saw the EL30V2 advertisement,")
        print("but FromBluetoothAddressAsync still returned None.")
        return

    print()
    print("*** DEVICE OBJECT OPENED ***")
    print(f"Name:    {device.name}")
    print(f"Address: 0x{device.bluetooth_address:012X}")

    print()
    print("Requesting GATT services...")
    print("This is the actual GATT connection test.")

    try:
        result = await asyncio.wait_for(
            device.get_gatt_services_async(),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        print()
        print("*** GATT TIMEOUT ***")
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
        print(f"Could not enumerate services: {exc}")

    device.close()

    print()
    print("Test complete.")


if __name__ == "__main__":
    asyncio.run(main())