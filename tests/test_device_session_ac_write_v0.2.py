import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bluetti_bt_lib.bluetooth.device_session import (
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices.el30v2 import EL30V2


BLE_ADDRESS = "DC:B4:D9:54:C6:86"
SETTLE_SECONDS = 3.0


def _future():
    return asyncio.get_running_loop().create_future()


async def read_ac_state(session):
    data = await session.read()
    if not data:
        raise RuntimeError("No telemetry returned")

    return data.get("ctrl_ac"), data


async def main(execute):
    address = os.environ.get("BLUETTI_BLE_ADDRESS", BLE_ADDRESS)

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

    print("Persistent DeviceSession AC write/read-back test")
    print("Connecting once...")

    try:
        await session.connect()

        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )

        original_state, data = await read_ac_state(session)

        if original_state is None:
            raise RuntimeError(
                "ctrl_ac was not returned by the EL30V2 polling data"
            )

        original_state = bool(original_state)
        test_state = not original_state

        print(f"Initial AC output state: {original_state}")
        print(
            f"Test will change AC output to {test_state}, verify it, "
            f"then restore AC output to {original_state}."
        )

        if not execute:
            print()
            print("DRY RUN ONLY - no write was performed.")
            print(
                "Run again with --execute after making sure toggling AC "
                "output is safe for anything connected to the power station."
            )
            return 0

        print()
        print(f"WRITE #1: ctrl_ac={test_state}")
        await session.write("ctrl_ac", test_state)

        print(
            f"After WRITE #1: connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

        await asyncio.sleep(SETTLE_SECONDS)

        readback_state, _ = await read_ac_state(session)

        print(
            f"READBACK #1: ctrl_ac={readback_state} "
            f"connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

        first_verified = bool(readback_state) == test_state

        print(
            "Verification #1: "
            + ("PASS" if first_verified else "FAIL")
        )

        print()
        print(f"WRITE #2 RESTORE: ctrl_ac={original_state}")
        await session.write("ctrl_ac", original_state)

        print(
            f"After WRITE #2: connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

        await asyncio.sleep(SETTLE_SECONDS)

        restored_state, _ = await read_ac_state(session)

        print(
            f"READBACK #2: ctrl_ac={restored_state} "
            f"connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

        restore_verified = bool(restored_state) == original_state

        print(
            "Verification #2: "
            + ("PASS" if restore_verified else "FAIL")
        )

        print()
        if first_verified and restore_verified:
            print(
                "SUCCESS: read -> write -> read -> write -> read all "
                "completed over the same persistent BLE/encryption session."
            )
            return 0

        print("TEST FAILED: one or more read-back checks did not match.")
        return 1

    finally:
        print()
        print("Disconnecting DeviceSession...")
        await session.disconnect()

        print(
            f"Connected={session.is_connected} "
            f"Ready={session.is_ready}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually toggle AC output and restore its original state.",
    )
    args = parser.parse_args()

    raise SystemExit(asyncio.run(main(args.execute)))
