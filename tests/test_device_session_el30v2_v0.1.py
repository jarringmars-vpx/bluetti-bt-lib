import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bluetti_bt_lib.bluetooth.device_session import (
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices.el30v2 import EL30V2


POLL_SECONDS = 5.0
RUN_COUNT = 20


def _future():
    return asyncio.get_running_loop().create_future()


async def main():
    address = os.environ.get("BLUETTI_BLE_ADDRESS")

    if not address:
        print(
            "Set BLUETTI_BLE_ADDRESS to the EL30V2 BLE address before "
            "running this test."
        )
        return 2

    device = EL30V2()
    config = DeviceSessionConfig(
        timeout=60,
        use_encryption=True,
    )

    session = DeviceSession(
        address,
        device,
        _future,
        config=config,
    )

    print("Connecting persistent DeviceSession...")

    try:
        await session.connect()

        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )
        print(
            "The BLUETTI Bluetooth indicator should remain on for the "
            "entire test."
        )

        next_start = time.monotonic()

        for index in range(1, RUN_COUNT + 1):
            started = time.monotonic()
            data = await session.read()
            elapsed = time.monotonic() - started

            if not data:
                print(
                    f"#{index:03d} no data "
                    f"connected={session.is_connected} "
                    f"ready={session.is_ready}"
                )
            else:
                print(
                    f"#{index:03d} "
                    f"connected={session.is_connected} "
                    f"ready={session.is_ready} "
                    f"SOC={data.get('total_battery_percent')}% "
                    f"ACin={data.get('ac_input_power')}W "
                    f"DCin={data.get('dc_input_power')}W "
                    f"ACout={data.get('ac_output_power')}W "
                    f"DCout={data.get('dc_output_power')}W "
                    f"AC={data.get('ctrl_ac')} "
                    f"DC={data.get('ctrl_dc')} "
                    f"mode={data.get('ctrl_charging_mode')} "
                    f"temp={data.get('temperature')} "
                    f"read={elapsed:.2f}s"
                )

            next_start += POLL_SECONDS
            delay = next_start - time.monotonic()

            if delay > 0 and index < RUN_COUNT:
                await asyncio.sleep(delay)

        return 0

    finally:
        print("Disconnecting persistent DeviceSession...")
        await session.disconnect()
        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
