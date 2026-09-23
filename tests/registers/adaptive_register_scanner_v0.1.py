#!/usr/bin/env python3
r"""
EL30V2 Adaptive Register Scanner v0.1

Purpose
-------
Autonomously discover readable Modbus holding registers on the BLUETTI EL30V2
using the Community library's persistent encrypted DeviceSession.

SAFETY:
- READ ONLY. This program contains no register-write operations.
- Unknown registers are never written.
- The scan stops if repeated health checks against a known-good register fail.
- Progress is journaled after every probe so an interrupted scan can resume.

Strategy
--------
The complete 0..65535 register space is covered progressively.

Pass 1 tests every 32nd address.
Pass 2 tests the halfway offset (16).
Later passes fill the remaining gaps at 8, 4, 2, and finally 1-register
resolution. Any readable discovery causes a dense +/- ISLAND_RADIUS
neighborhood investigation after the current reconnaissance pass.

If allowed to finish all passes, every register address will have been tested
at least once, except addresses already tested during island investigation.

Output files (created beside this script)
----------------------------------------
EL30V2_Register_Scan_State.txt  - append-only resumable scan journal
EL30V2_Register_Scan_Log.txt    - detailed human-readable chronological log
EL30V2_Register_Map.txt         - current/final human-readable summary

Run from the repository root with:
C:\Python310\python.exe tests\registers\adaptive_register_scanner_v0.1.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


# ---------------------------------------------------------------------------
# Make the repository importable even when this script is launched directly.
# tests/registers/<this file> -> parents[2] is repository root.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bluetti_bt_lib.bluetooth.device_session import (  # noqa: E402
    DeviceSession,
    DeviceSessionConfig,
)
from bluetti_bt_lib.devices import EL30V2  # noqa: E402


VERSION = "0.1"

# EL30V2 BLE address already established for this test device.
ADDRESS = os.environ.get("BLUETTI_BLE_ADDRESS", "DC:B4:D9:54:C6:86")

MIN_REGISTER = 0
MAX_REGISTER = 65535

# Reconnaissance pass definitions.
# Each tuple is: (display name, spacing, offsets newly introduced this pass)
#
# Combined, these passes eventually test every residue modulo 32 exactly once:
#   0
#   16
#   8,24
#   4,12,20,28
#   2,6,10,...,30
#   1,3,5,...,31
PASS_PLAN = (
    ("PASS 1 / spacing 32", 32, (0,)),
    ("PASS 2 / spacing 16", 32, (16,)),
    ("PASS 3 / spacing 8", 32, tuple(range(8, 32, 16))),
    ("PASS 4 / spacing 4", 32, tuple(range(4, 32, 8))),
    ("PASS 5 / spacing 2", 32, tuple(range(2, 32, 4))),
    ("PASS 6 / spacing 1", 32, tuple(range(1, 32, 2))),
)

# Dense neighborhood scanned around each new readable discovery.
ISLAND_RADIUS = 64

# Scanner pacing/safety.
COMMAND_TIMEOUT = 0.75
COMMAND_RETRIES = 0
RETRY_DELAY = 0.0
INTER_PROBE_DELAY = 0.03

HEALTH_REGISTER = 102
HEALTH_EVERY_PROBES = 250
HEALTH_FAILURE_LIMIT = 2

# Periodic cooldown reduces uninterrupted BLE traffic without requiring the
# operator to restart the program.
COOLDOWN_AFTER_SECONDS = 30 * 60
COOLDOWN_SECONDS = 2 * 60

# Console status frequency. Hits/errors/health events are printed immediately.
CONSOLE_PROGRESS_EVERY = 50

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "EL30V2_Register_Scan_State.txt"
LOG_FILE = BASE_DIR / "EL30V2_Register_Scan_Log.txt"
MAP_FILE = BASE_DIR / "EL30V2_Register_Map.txt"


@dataclass
class Result:
    status: str
    value: Optional[int] = None
    detail: str = ""


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_detail(text: str) -> str:
    return " ".join(str(text).replace("\t", " ").replace("\r", " ").splitlines())


class Scanner:
    def __init__(self) -> None:
        self.results: Dict[int, Result] = {}
        self.session: Optional[DeviceSession] = None
        self.total_new_probes = 0
        self.readable_count = 0
        self.timeout_count = 0
        self.error_count = 0
        self.health_failures = 0
        self.new_hits_since_island_scan: set[int] = set()
        self.started_monotonic = time.monotonic()
        self.last_cooldown_monotonic = self.started_monotonic
        self.stop_reason = ""

    def log(self, message: str, *, console: bool = False) -> None:
        line = f"{now_text()}  {message}"
        with LOG_FILE.open("a", encoding="utf-8", buffering=1) as fh:
            fh.write(line + "\n")
            fh.flush()
        if console:
            print(line, flush=True)

    def append_state(self, address: int, result: Result, source: str) -> None:
        # Append-only plain-text journal. Tabs are used only as field separators;
        # this is intentionally not CSV.
        value = "" if result.value is None else str(result.value)
        detail = safe_detail(result.detail)
        with STATE_FILE.open("a", encoding="utf-8", buffering=1) as fh:
            fh.write(
                f"REG\t{address}\t{result.status}\t{value}\t"
                f"{source}\t{now_text()}\t{detail}\n"
            )
            fh.flush()

    def append_marker(self, kind: str, text: str) -> None:
        with STATE_FILE.open("a", encoding="utf-8", buffering=1) as fh:
            fh.write(f"{kind}\t{now_text()}\t{safe_detail(text)}\n")
            fh.flush()

    def load_state(self) -> None:
        if not STATE_FILE.exists():
            with STATE_FILE.open("w", encoding="utf-8") as fh:
                fh.write("# EL30V2 Adaptive Register Scanner State\n")
                fh.write(f"# Version: {VERSION}\n")
                fh.write("# Append-only plain-text journal; not CSV.\n")
                fh.write("# REG fields: address status value source timestamp detail\n")
            return

        loaded = 0
        with STATE_FILE.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if not raw.startswith("REG\t"):
                    continue
                parts = raw.rstrip("\n").split("\t", 6)
                if len(parts) < 6:
                    continue
                try:
                    address = int(parts[1])
                except ValueError:
                    continue
                status = parts[2]
                value = None
                if parts[3]:
                    try:
                        value = int(parts[3])
                    except ValueError:
                        pass
                detail = parts[6] if len(parts) >= 7 else ""
                self.results[address] = Result(status, value, detail)
                loaded += 1

        self._recount()
        self.log(
            f"RESUME loaded {len(self.results)} unique previously tested "
            f"registers from {loaded} journal records.",
            console=True,
        )

    def _recount(self) -> None:
        self.readable_count = sum(
            1 for result in self.results.values() if result.status == "READABLE"
        )
        self.timeout_count = sum(
            1 for result in self.results.values() if result.status == "TIMEOUT"
        )
        self.error_count = sum(
            1 for result in self.results.values() if result.status == "ERROR"
        )

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        self.session = DeviceSession(
            ADDRESS,
            EL30V2(),
            loop.create_future,
            config=DeviceSessionConfig(
                timeout=60,
                use_encryption=True,
                command_timeout=COMMAND_TIMEOUT,
                command_retries=COMMAND_RETRIES,
                retry_delay=RETRY_DELAY,
            ),
        )
        self.log(f"Connecting to EL30V2 at {ADDRESS}...", console=True)
        await self.session.connect()
        self.log(
            f"CONNECTED connected={self.session.is_connected} "
            f"ready={self.session.is_ready}",
            console=True,
        )

    async def disconnect(self) -> None:
        if self.session is None:
            return
        try:
            await self.session.disconnect()
        except Exception as exc:
            self.log(f"Disconnect warning: {type(exc).__name__}: {exc}", console=True)

    async def raw_read_one(self, address: int) -> Result:
        assert self.session is not None
        try:
            values = await self.session.read_registers(address, 1)
            diagnostics = self.session.consume_scan_diagnostics()
            value = values.get(address)
            detail = " | ".join(diagnostics)
            return Result("READABLE", value, detail)
        except asyncio.TimeoutError as exc:
            diagnostics = self.session.consume_scan_diagnostics()
            detail = " | ".join(diagnostics) or str(exc) or "command timeout"
            return Result("TIMEOUT", None, detail)
        except Exception as exc:
            diagnostics = self.session.consume_scan_diagnostics()
            detail_parts = [f"{type(exc).__name__}: {exc}"]
            detail_parts.extend(diagnostics)
            return Result("ERROR", None, " | ".join(detail_parts))

    async def probe(
        self,
        address: int,
        source: str,
        *,
        force: bool = False,
        count_as_scan_probe: bool = True,
    ) -> Result:
        if not force and address in self.results:
            return self.results[address]

        result = await self.raw_read_one(address)

        if count_as_scan_probe:
            self.total_new_probes += 1
            self.results[address] = result
            self.append_state(address, result, source)

            if result.status == "READABLE":
                self.readable_count += 1
                self.new_hits_since_island_scan.add(address)
                self.log(
                    f"*** HIT R{address}={result.value} source={source} ***",
                    console=True,
                )
            elif result.status == "TIMEOUT":
                self.timeout_count += 1
            else:
                self.error_count += 1
                self.log(
                    f"ERROR R{address} source={source}: {result.detail}",
                    console=True,
                )

            if self.total_new_probes % CONSOLE_PROGRESS_EVERY == 0:
                self.print_progress(address, source)

            if (
                self.total_new_probes > 0
                and self.total_new_probes % HEALTH_EVERY_PROBES == 0
            ):
                await self.health_check()

            await self.maybe_cooldown()

            if INTER_PROBE_DELAY:
                await asyncio.sleep(INTER_PROBE_DELAY)

        return result

    async def health_check(self) -> None:
        self.log(
            f"HEALTH checking known-good R{HEALTH_REGISTER}...",
            console=True,
        )
        result = await self.raw_read_one(HEALTH_REGISTER)

        if result.status == "READABLE":
            self.health_failures = 0
            self.log(
                f"HEALTH PASS R{HEALTH_REGISTER}={result.value}",
                console=True,
            )
            return

        self.health_failures += 1
        self.log(
            f"HEALTH FAIL {self.health_failures}/{HEALTH_FAILURE_LIMIT}: "
            f"{result.status} {result.detail}",
            console=True,
        )

        if self.health_failures >= HEALTH_FAILURE_LIMIT:
            self.stop_reason = (
                f"Known-good health register R{HEALTH_REGISTER} failed "
                f"{self.health_failures} consecutive checks."
            )
            raise RuntimeError(self.stop_reason)

        await asyncio.sleep(2.0)

    async def maybe_cooldown(self) -> None:
        elapsed = time.monotonic() - self.last_cooldown_monotonic
        if elapsed < COOLDOWN_AFTER_SECONDS:
            return

        self.log(
            f"COOLDOWN pausing BLE requests for {COOLDOWN_SECONDS} seconds...",
            console=True,
        )
        await asyncio.sleep(COOLDOWN_SECONDS)
        self.last_cooldown_monotonic = time.monotonic()
        await self.health_check()
        self.log("COOLDOWN complete; resuming.", console=True)

    def print_progress(self, current: int, source: str) -> None:
        elapsed = time.monotonic() - self.started_monotonic
        tested = len(self.results)
        percent = tested * 100.0 / (MAX_REGISTER - MIN_REGISTER + 1)
        print(
            f"[{now_text()}] {source} current=R{current} "
            f"unique_tested={tested}/65536 ({percent:.2f}%) "
            f"readable={self.readable_count} "
            f"timeouts={self.timeout_count} errors={self.error_count} "
            f"session_new_probes={self.total_new_probes} "
            f"elapsed={elapsed/60:.1f}m",
            flush=True,
        )

    @staticmethod
    def addresses_for_pass(modulus: int, offsets: Iterable[int]) -> Iterable[int]:
        for offset in offsets:
            address = MIN_REGISTER + offset
            while address <= MAX_REGISTER:
                yield address
                address += modulus

    async def run_recon_pass(
        self,
        pass_number: int,
        name: str,
        modulus: int,
        offsets: Tuple[int, ...],
    ) -> None:
        candidates = list(self.addresses_for_pass(modulus, offsets))
        remaining = sum(1 for address in candidates if address not in self.results)

        self.append_marker("PASS_START", f"{pass_number} {name} remaining={remaining}")
        self.log(
            f"===== {name} START: {remaining} untested candidate addresses =====",
            console=True,
        )

        for address in candidates:
            if address in self.results:
                continue
            await self.probe(address, f"recon-pass-{pass_number}")

        self.append_marker("PASS_END", f"{pass_number} {name}")
        self.log(f"===== {name} COMPLETE =====", console=True)

        await self.investigate_new_hits(pass_number)
        self.write_map()

    async def investigate_new_hits(self, pass_number: int) -> None:
        if not self.new_hits_since_island_scan:
            self.log(
                f"No new readable discoveries to expand after pass {pass_number}.",
                console=True,
            )
            return

        seeds = sorted(self.new_hits_since_island_scan)
        self.new_hits_since_island_scan.clear()

        self.log(
            f"ISLAND EXPANSION after pass {pass_number}: "
            f"{len(seeds)} seed register(s).",
            console=True,
        )
        self.append_marker(
            "ISLAND_START",
            f"after_pass={pass_number} seeds={','.join(map(str, seeds))}",
        )

        # Merge overlapping +/- radius windows so nearby hits are scanned once.
        windows = []
        for seed in seeds:
            start = max(MIN_REGISTER, seed - ISLAND_RADIUS)
            end = min(MAX_REGISTER, seed + ISLAND_RADIUS)
            if windows and start <= windows[-1][1] + 1:
                windows[-1] = (windows[-1][0], max(windows[-1][1], end))
            else:
                windows.append((start, end))

        for start, end in windows:
            untested = sum(
                1 for address in range(start, end + 1)
                if address not in self.results
            )
            self.log(
                f"Expanding candidate island R{start}-R{end}; "
                f"{untested} addresses remain untested.",
                console=True,
            )
            for address in range(start, end + 1):
                if address in self.results:
                    continue
                await self.probe(
                    address,
                    f"island-after-pass-{pass_number}",
                )

        self.append_marker("ISLAND_END", f"after_pass={pass_number}")
        self.log(
            f"ISLAND EXPANSION after pass {pass_number} complete.",
            console=True,
        )

        # Hits found during expansion do not require recursive expansion here.
        # Later global passes will still find any larger/remote continuation,
        # and all addresses are eventually covered by Pass 6.
        self.new_hits_since_island_scan.clear()

    def readable_islands(self) -> list[tuple[int, int]]:
        readable = sorted(
            address
            for address, result in self.results.items()
            if result.status == "READABLE"
        )
        if not readable:
            return []

        islands = []
        start = previous = readable[0]
        for address in readable[1:]:
            if address == previous + 1:
                previous = address
                continue
            islands.append((start, previous))
            start = previous = address
        islands.append((start, previous))
        return islands

    def write_map(self) -> None:
        self._recount()
        tested = len(self.results)
        with MAP_FILE.open("w", encoding="utf-8") as fh:
            fh.write("EL30V2 REGISTER MAP\n")
            fh.write("====================\n")
            fh.write(f"Scanner version: {VERSION}\n")
            fh.write(f"Generated: {now_text()}\n")
            fh.write(f"BLE device: {ADDRESS}\n")
            fh.write("Method: FC03/read-only single-register discovery\n")
            fh.write("No writes are performed by this scanner.\n\n")

            fh.write("SUMMARY\n")
            fh.write("-------\n")
            fh.write(f"Unique addresses tested: {tested} / 65536\n")
            fh.write(f"Coverage: {tested * 100.0 / 65536:.2f}%\n")
            fh.write(f"Readable: {self.readable_count}\n")
            fh.write(f"Timeout: {self.timeout_count}\n")
            fh.write(f"Error: {self.error_count}\n")
            fh.write(f"Untested: {65536 - tested}\n\n")

            fh.write("CONTIGUOUS READABLE ISLANDS\n")
            fh.write("---------------------------\n")
            islands = self.readable_islands()
            if not islands:
                fh.write("(none discovered yet)\n")
            else:
                for start, end in islands:
                    count = end - start + 1
                    if start == end:
                        fh.write(f"R{start}  (1 register)\n")
                    else:
                        fh.write(f"R{start}-R{end}  ({count} registers)\n")

            fh.write("\nREADABLE REGISTERS\n")
            fh.write("------------------\n")
            for address in sorted(self.results):
                result = self.results[address]
                if result.status == "READABLE":
                    fh.write(f"R{address} = {result.value}\n")

            fh.write("\nNON-READABLE/AMBIGUOUS COUNTS\n")
            fh.write("-----------------------------\n")
            fh.write(
                "TIMEOUT means no valid response arrived within the configured "
                f"{COMMAND_TIMEOUT:.2f}s command timeout.\n"
            )
            fh.write(
                "ERROR means a response or session condition produced an "
                "exception; inspect the chronological log/state journal.\n"
            )

        self.log(f"Register map updated: {MAP_FILE.name}")

    async def run(self) -> int:
        self.load_state()

        print()
        print("EL30V2 Adaptive Register Scanner v0.1")
        print("-------------------------------------")
        print(f"Device:             {ADDRESS}")
        print("Mode:               READ ONLY")
        print(f"Previously tested:  {len(self.results)}")
        print(f"Previously readable:{self.readable_count}")
        print(f"State:              {STATE_FILE}")
        print(f"Log:                {LOG_FILE}")
        print(f"Map:                {MAP_FILE}")
        print()

        self.append_marker("SESSION_START", f"scanner_version={VERSION}")

        try:
            await self.connect()

            # Always verify communication before beginning/resuming discovery.
            await self.health_check()

            for pass_number, (name, modulus, offsets) in enumerate(
                PASS_PLAN, start=1
            ):
                await self.run_recon_pass(
                    pass_number,
                    name,
                    modulus,
                    offsets,
                )

            self.write_map()
            self.append_marker("SCAN_COMPLETE", "all progressive passes complete")
            self.log(
                "===== FULL 0-65535 READ DISCOVERY COMPLETE =====",
                console=True,
            )
            return 0

        except KeyboardInterrupt:
            self.stop_reason = "Operator interrupted with Ctrl+C."
            self.log(self.stop_reason, console=True)
            return 130

        except Exception as exc:
            if not self.stop_reason:
                self.stop_reason = f"{type(exc).__name__}: {exc}"
            self.log(f"SCAN STOPPED: {self.stop_reason}", console=True)
            return 1

        finally:
            self.write_map()
            self.append_marker(
                "SESSION_END",
                self.stop_reason or "normal completion",
            )
            await self.disconnect()
            self.log(
                f"State preserved. Re-run the same command to resume. "
                f"Unique addresses currently recorded: {len(self.results)}",
                console=True,
            )


async def async_main() -> int:
    scanner = Scanner()
    return await scanner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        print("\nInterrupted. The append-only state journal preserves completed probes.")
        raise SystemExit(130)
