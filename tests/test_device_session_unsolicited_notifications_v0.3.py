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


class NotificationProbe:
    """
    Wrap DeviceSession's notification handler so every incoming GATT
    notification is timestamped before the normal DeviceSession handler
    processes it.

    The probe deliberately includes passive capture periods in which it sends
    no BLE commands. Any notification received during those periods is therefore
    unsolicited by this test.
    """

    def __init__(self, session: DeviceSession, log_path: Path):
        self.session = session
        self.log_path = log_path
        self.original_handler = session._notification_handler
        self.passive = False
        self.notification_count = 0
        self.passive_notification_count = 0
        self.started = time.monotonic()

    def log(self, line: str) -> None:
        msg = f"{ts()} | +{time.monotonic() - self.started:8.3f}s | {line}"
        print(msg, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    async def handler(self, sender: int, data: bytearray):
        self.notification_count += 1
        if self.passive:
            self.passive_notification_count += 1

        future = self.session.notify_future
        pending = bool(future is not None and not future.done())
        reg = self.session.current_registers
        reg_start = getattr(reg, "starting_address", None)

        self.log(
            "NOTIFY "
            f"#{self.notification_count} "
            f"passive={self.passive} "
            f"connected={self.session.is_connected} "
            f"ready={self.session.is_ready} "
            f"pending_read={pending} "
            f"pending_start={reg_start} "
            f"len={len(data)} "
            f"hex={bytes(data).hex(' ')}"
        )

        try:
            await self.original_handler(sender, data)
        except Exception as exc:
            self.log(
                f"NOTIFY_HANDLER_EXCEPTION {type(exc).__name__}: {exc}"
            )
            raise

    async def passive_window(self, seconds: float, label: str) -> None:
        before_total = self.notification_count
        before_passive = self.passive_notification_count

        self.log(
            f"PASSIVE_BEGIN label={label!r} seconds={seconds:.1f} "
            f"connected={self.session.is_connected} ready={self.session.is_ready}"
        )

        self.passive = True
        deadline = time.monotonic() + seconds

        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                print(
                    f"\rPassive capture: {remaining:4.1f}s remaining "
                    f"| connected={self.session.is_connected} "
                    f"| notifications={self.notification_count - before_total}",
                    end="",
                    flush=True,
                )
                await asyncio.sleep(min(0.25, max(0.0, remaining)))
        finally:
            self.passive = False
            print()

        gained = self.passive_notification_count - before_passive
        self.log(
            f"PASSIVE_END label={label!r} unsolicited_notifications={gained} "
            f"connected={self.session.is_connected} ready={self.session.is_ready}"
        )


def display_state(data: dict[str, Any] | None) -> str:
    data = data or {}
    return (
        f"SOC={data.get('total_battery_percent')} "
        f"AC={data.get('ctrl_ac')} "
        f"DC={data.get('ctrl_dc')} "
        f"mode={data.get('ctrl_charging_mode')} "
        f"ACin={data.get('ac_input_power')}W "
        f"ACout={data.get('ac_output_power')}W "
        f"DCin={data.get('dc_input_power')}W "
        f"DCout={data.get('dc_output_power')}W"
    )


async def safe_read(session: DeviceSession, probe: NotificationProbe, label: str):
    probe.log(
        f"READ_BEGIN label={label!r} connected={session.is_connected} "
        f"ready={session.is_ready}"
    )
    started = time.monotonic()

    try:
        data = await session.read()
    except Exception as exc:
        probe.log(
            f"READ_ERROR label={label!r} elapsed={time.monotonic() - started:.3f}s "
            f"type={type(exc).__name__} message={exc!r} "
            f"connected={session.is_connected} ready={session.is_ready}"
        )
        return None

    probe.log(
        f"READ_OK label={label!r} elapsed={time.monotonic() - started:.3f}s "
        f"connected={session.is_connected} ready={session.is_ready} "
        f"{display_state(data)}"
    )
    return data


async def run(address: str, passive_seconds: float) -> int:
    logs_dir = Path("tests") / "registers" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / (
        "community_el30v2_unsolicited_notification_probe_"
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

    probe = NotificationProbe(session, log_path)

    # Install the wrapper before connect(), so the BLE notifier is registered
    # with our wrapper and then delegated to the normal DeviceSession handler.
    session._notification_handler = probe.handler

    probe.log("EL30V2 unsolicited-notification probe v0.3")
    probe.log(f"Log file: {log_path}")
    probe.log("Connecting/authenticating once...")

    try:
        await session.connect()
        probe.log(
            f"CONNECTED connected={session.is_connected} ready={session.is_ready}"
        )

        await safe_read(session, probe, "baseline")

        print()
        print("=" * 78)
        print("PASSIVE TEST 1 — AC OUTPUT")
        print("=" * 78)
        print(
            "During the passive capture window, physically toggle AC Output on the "
            "EL30V2 at least once. Do NOT use the BLUETTI mobile app during this test."
        )
        print(
            "The script will send NO BLE commands during the window. Any GATT "
            "notification received then is unsolicited by this test."
        )
        input("Press Enter when you are ready to begin the AC passive window... ")
        await probe.passive_window(passive_seconds, "physical AC toggle")
        await safe_read(session, probe, "after AC passive window")

        print()
        print("=" * 78)
        print("PASSIVE TEST 2 — DC OUTPUT")
        print("=" * 78)
        print(
            "During this passive window, physically toggle DC Output on the EL30V2 "
            "at least once."
        )
        input("Press Enter when you are ready to begin the DC passive window... ")
        await probe.passive_window(passive_seconds, "physical DC toggle")
        await safe_read(session, probe, "after DC passive window")

        print()
        print("=" * 78)
        print("PASSIVE TEST 3 — IDLE CONTROL")
        print("=" * 78)
        print(
            "Do not press any controls during this window. This tells us whether the "
            "EL30V2 sends periodic notifications even when nothing changes."
        )
        input("Press Enter when you are ready to begin the idle passive window... ")
        await probe.passive_window(passive_seconds, "idle control")
        await safe_read(session, probe, "after idle passive window")

        probe.log(
            "SUMMARY "
            f"total_notifications={probe.notification_count} "
            f"passive_notifications={probe.passive_notification_count} "
            f"connected={session.is_connected} ready={session.is_ready}"
        )

        print()
        print("TEST COMPLETE")
        print(f"Log saved to: {log_path}")
        print(
            "Please paste the console output or upload the generated .txt log so "
            "we can compare AC, DC, and idle notification behavior."
        )
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
        description="Probe EL30V2 for unsolicited BLE GATT notifications."
    )
    parser.add_argument(
        "--address",
        default=os.environ.get("BLUETTI_BLE_ADDRESS", DEFAULT_ADDRESS),
        help="EL30V2 BLE address.",
    )
    parser.add_argument(
        "--passive-seconds",
        type=float,
        default=15.0,
        help="Length of each passive no-command capture window (default: 15).",
    )
    args = parser.parse_args()

    return asyncio.run(run(args.address, max(5.0, args.passive_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
