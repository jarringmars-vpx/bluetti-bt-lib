#!/usr/bin/env python3
r"""
BLUETTI EL30V2 encrypted polling capture.

Purpose:
    Establish a normal encrypted Community Library DeviceSession,
    print the IoT-v2 AES session key, and repeatedly poll two
    known-good register blocks while an nRF52840 captures the
    BLE traffic over the air.

Reads only. No register writes are performed.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from bluetti_bt_lib.bluetooth.device_session import (
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices import EL30V2


ADDRESS = "DC:B4:D9:54:C6:86"

RUN_SECONDS = 300
POLL_INTERVAL = 1.0


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

        print(
            f"Polling for {RUN_SECONDS} seconds "
            f"at approximately {POLL_INTERVAL:.1f}s intervals."
        )
        print("CTRL = R2011-R2020")
        print("POWER = R140-R149")
        print()

        started = time.monotonic()
        poll_number = 0

        while time.monotonic() - started < RUN_SECONDS:
            poll_number += 1

            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            try:
                t0 = time.monotonic()

                control = await session.read_registers(2011, 10)
                power = await session.read_registers(140, 10)

                elapsed = time.monotonic() - t0

                print(
                    f"{timestamp} "
                    f"poll={poll_number:04d} "
                    f"elapsed={elapsed:.3f}s "
                    f"AC={control.get(2011)} "
                    f"DC={control.get(2012)} "
                    f"MODE={control.get(2020)} "
                    f"POWER={power}"
                )

            except Exception as exc:
                print(
                    f"{timestamp} "
                    f"poll={poll_number:04d} "
                    f"ERROR={type(exc).__name__}: {exc}"
                )

            await asyncio.sleep(POLL_INTERVAL)

        print()
        print("Polling interval complete.")

    finally:
        print("Disconnecting...")

        await session.disconnect()

        print(
            f"Disconnected: connected={session.is_connected} "
            f"ready={session.is_ready}"
        )


if __name__ == "__main__":
    asyncio.run(main())