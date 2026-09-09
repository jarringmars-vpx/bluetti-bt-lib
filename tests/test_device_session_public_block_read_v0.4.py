#!/usr/bin/env python3
r"""
DeviceSession public block-read API validation v0.4.

Validates the new public read_registers(start, count) API against the two
known-good EL30V2 blocks discovered in the v0.7.2 optimization probe.

No register writes are performed.
"""

from __future__ import annotations

import asyncio
import os
import time

from bluetti_bt_lib.bluetooth.device_session import (
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices import EL30V2


ADDRESS = os.environ.get(
    "BLUETTI_BLE_ADDRESS",
    "DC:B4:D9:54:C6:86",
)


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
        print("Connecting...")
        await session.connect()
        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )

        for label, start, count in (
            ("CONTROL", 2011, 10),
            ("POWER", 140, 10),
        ):
            t0 = time.monotonic()
            values = await session.read_registers(start, count)
            elapsed = time.monotonic() - t0

            print()
            print(
                f"{label} R{start}-R{start + count - 1} "
                f"elapsed={elapsed:.3f}s"
            )
            for address, value in values.items():
                print(f"  R{address}={value}")

        print()
        print("Rapid control-block test: 20 reads")
        timings = []

        for index in range(1, 21):
            t0 = time.monotonic()
            values = await session.read_registers(2011, 10)
            elapsed = time.monotonic() - t0
            timings.append(elapsed)

            print(
                f"#{index:02d} {elapsed:.3f}s "
                f"AC={values[2011]} "
                f"DC={values[2012]} "
                f"MODE={values[2020]} "
                f"connected={session.is_connected} "
                f"ready={session.is_ready}"
            )

            await asyncio.sleep(0.10)

        print()
        print(
            f"Average={sum(timings) / len(timings):.3f}s "
            f"Min={min(timings):.3f}s "
            f"Max={max(timings):.3f}s"
        )
        print(
            f"SUCCESS connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

    finally:
        await session.disconnect()
        print(
            f"Disconnected: connected={session.is_connected} "
            f"ready={session.is_ready}"
        )


if __name__ == "__main__":
    asyncio.run(main())
