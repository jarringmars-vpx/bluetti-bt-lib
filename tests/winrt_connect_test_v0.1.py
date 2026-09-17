import asyncio

from winrt.windows.devices.bluetooth import BluetoothLEDevice
from winrt.windows.devices.enumeration import DeviceInformation


ADDRESS = "DC:B4:D9:54:C6:86"
DEVICE_NAME = "EL30V22543131120851"


async def main():
    print("Direct WinRT BLE test")
    print("=====================")
    print(f"Target address: {ADDRESS}")
    print(f"Target name:    {DEVICE_NAME}")
    print()

    selector = BluetoothLEDevice.get_device_selector()

    print("Searching for BLE devices using Windows WinRT...")
    devices = await DeviceInformation.find_all_async(selector)

    print(f"Windows returned {len(devices)} BLE device entries.")

    target = None

    for device in devices:
        name = device.name or ""
        print(f"  {name!r}  ID={device.id}")

        if DEVICE_NAME.lower() in name.lower():
            target = device

    if target is None:
        print()
        print("EL30V2 was not found by WinRT.")
        return

    print()
    print("Found EL30V2:")
    print(f"  Name: {target.name}")
    print(f"  ID:   {target.id}")
    print()

    print("Opening BluetoothLEDevice directly through WinRT...")
    print("If this hangs here, the problem is below Bleak.")

    try:
        ble_device = await asyncio.wait_for(
            BluetoothLEDevice.from_id_async(target.id),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        print()
        print("*** TIMEOUT ***")
        print("WinRT did not complete BluetoothLEDevice.from_id_async()")
        print("within 30 seconds.")
        return
    except Exception as exc:
        print()
        print("*** EXCEPTION ***")
        print(type(exc).__name__, exc)
        return

    if ble_device is None:
        print()
        print("WinRT returned None instead of a BluetoothLEDevice.")
        return

    print()
    print("*** SUCCESS ***")
    print("Direct WinRT opened the EL30V2.")
    print(f"Name: {ble_device.name}")
    print(f"Bluetooth address: 0x{ble_device.bluetooth_address:012X}")

    ble_device.close()


if __name__ == "__main__":
    asyncio.run(main())