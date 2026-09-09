from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bluetti_bt_lib.bluetooth.device_session import DeviceSession, DeviceSessionConfig
from bluetti_bt_lib.devices import EL30V2


DEFAULT_ADDRESS = "DC:B4:D9:54:C6:86"


def ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class CollisionProbe:
    """
    Reproduce the GUI failure condition by continuously polling while the user
    physically toggles AC Output on the EL30V2.

    Unlike the GUI backend, this probe does NOT intentionally tear down the
    DeviceSession after a read failure. It records transport/session state and
    immediately tries a known-good R102 read on the same BLE/encryption session.
    """

    def __init__(self, session: DeviceSession, log_path: Path):
        self.session = session
        self.log_path = log_path
        self.started = time.monotonic()
        self.poll_number = 0
        self.notify_number = 0

        self.original_handler = session._notification_handler
        self.original_send = session._async_send_command

    def log(self, line: str) -> None:
        msg = f"{ts()} | +{time.monotonic() - self.started:8.3f}s | {line}"
        print(msg, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    async def notification_handler(self, sender: int, data: bytearray):
        self.notify_number += 1
        future = self.session.notify_future
        pending = bool(future is not None and not future.done())
        reg = self.session.current_registers
        reg_start = getattr(reg, "starting_address", None)

        self.log(
            "NOTIFY "
            f"#{self.notify_number} "
            f"connected={self.session.is_connected} "
            f"ready={self.session.is_ready} "
            f"pending={pending} "
            f"pending_start={reg_start} "
            f"len={len(data)} "
            f"hex={bytes(data).hex(' ')}"
        )

        return await self.original_handler(sender, data)

    async def send_command(self, register):
        start = getattr(register, "starting_address", None)
        count = getattr(register, "quantity", None)
        if count is None:
            count = getattr(register, "count", None)

        self.log(
            f"CMD_BEGIN start={start} count={count} "
            f"connected={self.session.is_connected} ready={self.session.is_ready}"
        )
        begun = time.monotonic()

        try:
            response = await self.original_send(register)
        except Exception as exc:
            self.log(
                f"CMD_ERROR start={start} elapsed={time.monotonic() - begun:.3f}s "
                f"type={type(exc).__name__} message={exc!r} "
                f"connected={self.session.is_connected} ready={self.session.is_ready}"
            )
            raise

        self.log(
            f"CMD_OK start={start} elapsed={time.monotonic() - begun:.3f}s "
            f"response_len={len(response) if response else 0} "
            f"connected={self.session.is_connected} ready={self.session.is_ready}"
        )
        return response


def state_text(data: dict[str, Any] | None) -> str:
    data = data or {}
    return (
        f"SOC={data.get('total_battery_percent')} "
        f"AC={data.get('ctrl_ac')} "
        f"DC={data.get('ctrl_dc')} "
        f"mode={data.get('ctrl_charging_mode')} "
        f"ACin={data.get('ac_input_power')} "
        f"ACout={data.get('ac_output_power')} "
        f"DCin={data.get('dc_input_power')} "
        f"DCout={data.get('dc_output_power')}"
    )


def find_register_102(session: DeviceSession):
    for register in session.bluetti_device.get_polling_registers():
        if getattr(register, "starting_address", None) == 102:
            return register
    raise RuntimeError("Could not find polling register beginning at R102.")


async def targeted_r102_read(session: DeviceSession, probe: CollisionProbe):
    reg = find_register_102(session)
    probe.log(
        f"R102_RECOVERY_BEGIN connected={session.is_connected} ready={session.is_ready}"
    )
    begun = time.monotonic()

    try:
        response = await session._async_send_command(reg)
        if not response:
            raise RuntimeError("R102 returned no response.")

        body = reg.parse_response(response)
        parsed = session.bluetti_device.parse(reg.starting_address, body)

        probe.log(
            f"R102_RECOVERY_OK elapsed={time.monotonic() - begun:.3f}s "
            f"SOC={parsed.get('total_battery_percent')} "
            f"connected={session.is_connected} ready={session.is_ready}"
        )
        return True

    except Exception as exc:
        probe.log(
            f"R102_RECOVERY_ERROR elapsed={time.monotonic() - begun:.3f}s "
            f"type={type(exc).__name__} message={exc!r} "
            f"connected={session.is_connected} ready={session.is_ready}"
        )
        return False


async def run(address: str, poll_interval: float, duration: float) -> int:
    logs_dir = Path("tests") / "registers" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / (
        "community_el30v2_ac_toggle_poll_collision_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".txt"
    )

    loop = asyncio.get_running_loop()
    session = DeviceSession(
        address,
        EL30V2(),
        loop.create_future,
        config=DeviceSessionConfig(timeout=60, use_encryption=True),
    )

    probe = CollisionProbe(session, log_path)

    # Wrap the notifier and command sender before connect/read.
    session._notification_handler = probe.notification_handler
    session._async_send_command = probe.send_command

    probe.log("EL30V2 AC-toggle/poll-collision probe v0.4")
    probe.log(f"Log file: {log_path}")
    probe.log("Connecting/authenticating once...")

    try:
        await session.connect()
        probe.log(
            f"CONNECTED connected={session.is_connected} ready={session.is_ready}"
        )

        print()
        print("=" * 78)
        print("INSTRUCTIONS")
        print("=" * 78)
        print("The test will continuously perform full DeviceSession reads.")
        print("While it is polling, physically toggle AC Output OFF and ON several times.")
        print("Try to press the AC button while register activity is scrolling.")
        print("Do NOT use the BLUETTI mobile app during this test.")
        print()
        input("Press Enter to begin continuous polling... ")

        deadline = time.monotonic() + duration
        failures = 0
        recoveries = 0

        while time.monotonic() < deadline:
            probe.poll_number += 1
            n = probe.poll_number
            begun = time.monotonic()

            probe.log(
                f"POLL_BEGIN #{n} connected={session.is_connected} "
                f"ready={session.is_ready}"
            )

            try:
                data = await session.read()
                probe.log(
                    f"POLL_OK #{n} elapsed={time.monotonic() - begun:.3f}s "
                    f"{state_text(data)} "
                    f"connected={session.is_connected} ready={session.is_ready}"
                )

            except Exception as exc:
                failures += 1
                probe.log(
                    f"POLL_ERROR #{n} elapsed={time.monotonic() - begun:.3f}s "
                    f"type={type(exc).__name__} message={exc!r} "
                    f"connected={session.is_connected} ready={session.is_ready}"
                )

                # Critical diagnostic: DO NOT disconnect here.
                # Immediately test whether the same BLE/encryption session still works.
                if session.is_connected and session.is_ready:
                    ok = await targeted_r102_read(session, probe)
                    if ok:
                        recoveries += 1
                        probe.log(
                            "RECOVERY_RESULT same_session_reusable=True "
                            "(no reconnect/re-authentication performed)"
                        )
                    else:
                        probe.log(
                            "RECOVERY_RESULT same_session_reusable=False "
                            "(R102 failed on existing session)"
                        )
                else:
                    probe.log(
                        "RECOVERY_SKIPPED because transport/session is not ready "
                        f"connected={session.is_connected} ready={session.is_ready}"
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            await asyncio.sleep(min(poll_interval, remaining))

        probe.log(
            f"SUMMARY polls={probe.poll_number} failures={failures} "
            f"same_session_recoveries={recoveries} "
            f"notifications={probe.notify_number} "
            f"connected={session.is_connected} ready={session.is_ready}"
        )

        print()
        print("TEST COMPLETE")
        print(f"Log saved to: {log_path}")
        print("Upload the generated .txt log so the failed command can be identified.")
        return 0

    finally:
        probe.log(
            f"DISCONNECT_BEGIN connected={session.is_connected} ready={session.is_ready}"
        )
        try:
            await session.disconnect()
        finally:
            probe.log(
                f"DISCONNECT_END connected={session.is_connected} ready={session.is_ready}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce EL30V2 physical AC-toggle vs active-poll collision."
    )
    parser.add_argument(
        "--address",
        default=os.environ.get("BLUETTI_BLE_ADDRESS", DEFAULT_ADDRESS),
        help="EL30V2 BLE address.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=90.0,
        help="Continuous-poll test duration in seconds (default: 90).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Pause between completed full reads (default: 0.5 sec).",
    )
    args = parser.parse_args()

    return asyncio.run(
        run(
            args.address,
            max(0.0, args.poll_interval),
            max(20.0, args.duration),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
