#!/usr/bin/env python3
r"""
Capture the BLUETTI IoT-v2 session AES key and perform one known
encrypted register read.

Test operation:
    Read R102, count 1 -- EL30V2 battery SOC

Diagnostic only. No register writes are performed.
"""

from __future__ import annotations

import asyncio

from bluetti_bt_lib.bluetooth.device_session import (
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices import EL30V2


ADDRESS = "DC:B4:D9:54:C6:86"


async def main() -> None:
    loop = asyncio.get_running_loop()

    session = DeviceSession(
        ADDRESS,
        EL30V2(),
        loop.create_future,
        config=DeviceSessionConfig(
            timeout=60,
            use_encryption=True,
            command_timeout=5.0,
            command_retries=1,
            retry_delay=0.4,
        ),
    )

    try:
        print("Connecting/authenticating...")

        await session.connect()

        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )

        key = session.encryption.secure_aes_key

        if key is None:
            raise RuntimeError(
                "Authentication completed but secure_aes_key is None"
            )

        print()
        print("=" * 70)
        print("BLUETTI IOT-V2 SESSION ESTABLISHED")
        print(f"SESSION_AES_KEY={key.hex()}")
        print("=" * 70)

        print()
        print("Performing ONE encrypted register read: R102 count=1")

        values = await session.read_registers(102, 1)

        print()
        print("R102 READ COMPLETE")
        print(f"Returned values: {values}")

        if 102 in values:
            print(f"R102={values[102]}")
        else:
            print("WARNING: R102 was not present in returned values.")

        print()
        print("Leaving BLE connection idle for 10 seconds...")
        await asyncio.sleep(10)

    finally:
        print("Disconnecting...")
        await session.disconnect()

        print(
            f"Disconnected: connected={session.is_connected} "
            f"ready={session.is_ready}"
        )


if __name__ == "__main__":
    asyncio.run(main())