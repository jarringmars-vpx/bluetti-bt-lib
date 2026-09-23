#!/usr/bin/env python3
r"""
EL30V2 full-scan cleanup / ambiguity resolver v0.1.

READ-ONLY. This program never writes device registers.

It consumes EL30V2_Register_Scan_State.txt produced by the adaptive scanner,
analytically reclassifies legacy v0.1 illegal-address exceptions as UNREADABLE,
and physically re-reads only addresses whose latest result is TIMEOUT or ERROR.
Oversized-response addresses are deliberately retried five times with detailed
per-attempt diagnostics preserved.

Outputs are written beside this script and DO NOT modify the original scan:
  EL30V2_Register_Cleanup_State.txt
  EL30V2_Register_Cleanup_Log.txt
  EL30V2_Register_Cleanup_Map.txt
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from bluetti_bt_lib.bluetooth.device_session import DeviceSession, DeviceSessionConfig
from bluetti_bt_lib.devices import EL30V2

VERSION = "0.1"
ADDRESS = os.environ.get("BLUETTI_BLE_ADDRESS", "DC:B4:D9:54:C6:86")
HERE = Path(__file__).resolve().parent
ORIGINAL_STATE = HERE / "EL30V2_Register_Scan_State.txt"
CLEANUP_STATE = HERE / "EL30V2_Register_Cleanup_State.txt"
CLEANUP_LOG = HERE / "EL30V2_Register_Cleanup_Log.txt"
CLEANUP_MAP = HERE / "EL30V2_Register_Cleanup_Map.txt"
HEALTH_REGISTER = 102
COMMAND_TIMEOUT = 1.25
NORMAL_RETRIES = 3
OVERSIZED_RETRIES = 5
INTER_PROBE_DELAY = 0.05
HEALTH_EVERY = 100

# The normal Modbus exception observed for an illegal/unreadable holding register.
ILLEGAL_SIGNATURE = "ResponseHex=01 83 03 01 31"
ACTUAL_RE = re.compile(r"actual=(\d+)B")


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line.rstrip("\n") + "\n")
        f.flush()


def say(msg: str) -> None:
    line = f"{stamp()}  {msg}"
    print(line, flush=True)
    append(CLEANUP_LOG, line)


def parse_original_state():
    if not ORIGINAL_STATE.exists():
        raise FileNotFoundError(f"Required original state file not found: {ORIGINAL_STATE}")
    latest = {}
    with ORIGINAL_STATE.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("REG\t"):
                continue
            parts = line.rstrip("\n").split("\t", 6)
            if len(parts) < 6:
                continue
            try:
                addr = int(parts[1])
            except ValueError:
                continue
            latest[addr] = {
                "status": parts[2],
                "value": parts[3] if len(parts) > 3 else "",
                "source": parts[4] if len(parts) > 4 else "",
                "timestamp": parts[5] if len(parts) > 5 else "",
                "detail": parts[6] if len(parts) > 6 else "",
            }
    return latest


def load_cleanup_results():
    results = {}
    if not CLEANUP_STATE.exists():
        return results
    with CLEANUP_STATE.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("RESULT\t"):
                continue
            parts = line.rstrip("\n").split("\t", 5)
            if len(parts) < 5:
                continue
            try:
                addr = int(parts[1])
            except ValueError:
                continue
            results[addr] = {
                "status": parts[2],
                "value": parts[3],
                "detail": parts[5] if len(parts) > 5 else "",
            }
    return results


def is_legacy_illegal(rec) -> bool:
    return rec["status"] == "ERROR" and ILLEGAL_SIGNATURE in rec["detail"] and "actual=0B" in rec["detail"]


def is_oversized(rec) -> bool:
    m = ACTUAL_RE.search(rec["detail"])
    return bool(m and int(m.group(1)) > 2)


def diagnostics_text(session) -> str:
    try:
        d = session.consume_scan_diagnostics()
    except Exception:
        return ""
    if not d:
        return ""
    if isinstance(d, (list, tuple)):
        return " | ".join(str(x).replace("\t", " ").replace("\n", " ") for x in d)
    return str(d).replace("\t", " ").replace("\n", " ")


def record_result(addr: int, status: str, value: str = "", detail: str = "") -> None:
    safe = detail.replace("\t", " ").replace("\n", " ")
    append(CLEANUP_STATE, f"RESULT\t{addr}\t{status}\t{value}\t{stamp()}\t{safe}")


def write_map(original, cleanup):
    merged = {}
    for addr in range(65536):
        rec = original.get(addr)
        if rec is None:
            merged[addr] = ("MISSING", "")
        elif addr in cleanup:
            merged[addr] = (cleanup[addr]["status"], cleanup[addr]["value"])
        elif is_legacy_illegal(rec):
            merged[addr] = ("UNREADABLE", "")
        else:
            merged[addr] = (rec["status"], rec["value"])

    counts = Counter(status for status, _ in merged.values())
    readable = [a for a, (s, _) in merged.items() if s == "READABLE"]
    islands = []
    if readable:
        start = prev = readable[0]
        for a in readable[1:]:
            if a == prev + 1:
                prev = a
            else:
                islands.append((start, prev))
                start = prev = a
            prev = a
        islands.append((start, prev))

    with CLEANUP_MAP.open("w", encoding="utf-8", newline="\n") as f:
        f.write("EL30V2 Definitive Register Discovery Map - Cleanup v0.1\n")
        f.write(f"Generated: {stamp()}\n")
        f.write(f"BLE address: {ADDRESS}\n")
        f.write("Method: original full scan + read-only ambiguity cleanup; no writes performed\n\n")
        f.write("SUMMARY\n")
        f.write("-------\n")
        f.write("Address space: 65536 (R0-R65535)\n")
        for key in ("READABLE", "UNREADABLE", "TIMEOUT", "ERROR", "AMBIGUOUS", "MISSING"):
            if counts[key]:
                f.write(f"{key.title()}: {counts[key]}\n")
        f.write(f"Definitively classified: {counts['READABLE'] + counts['UNREADABLE']} / 65536\n\n")
        f.write("CONTIGUOUS READABLE ISLANDS\n")
        f.write("---------------------------\n")
        for a, b in islands:
            f.write(f"R{a}-R{b} ({b-a+1} registers)\n")
        f.write("\nUNRESOLVED ADDRESSES\n")
        f.write("--------------------\n")
        unresolved = [(a, s) for a, (s, _) in merged.items() if s not in ("READABLE", "UNREADABLE")]
        if not unresolved:
            f.write("None\n")
        else:
            for a, s in unresolved:
                f.write(f"R{a}: {s}\n")
        f.write("\nREADABLE REGISTER VALUES (snapshot only)\n")
        f.write("----------------------------------------\n")
        for a in readable:
            # Sensitive values are deliberately redacted from the human-readable map.
            if 12002 <= a <= 12007:
                v = "<REDACTED: Wi-Fi SSID field>"
            elif 12018 <= a <= 12023:
                v = "<REDACTED: Wi-Fi password field>"
            else:
                v = merged[a][1]
            f.write(f"R{a}={v}\n")


async def health(session) -> None:
    values = await session.read_registers(HEALTH_REGISTER, 1)
    say(f"HEALTH PASS R{HEALTH_REGISTER}={values.get(HEALTH_REGISTER)}")


async def reread_address(session, addr: int, attempts: int):
    outcomes = []
    for n in range(1, attempts + 1):
        try:
            values = await session.read_registers(addr, 1)
            value = values.get(addr)
            diag = diagnostics_text(session)
            outcomes.append(("READABLE", str(value), diag))
            say(f"R{addr} attempt {n}/{attempts}: READABLE value={value}")
        except asyncio.TimeoutError as exc:
            diag = diagnostics_text(session)
            outcomes.append(("TIMEOUT", "", f"{type(exc).__name__}: {exc} | {diag}"))
            say(f"R{addr} attempt {n}/{attempts}: TIMEOUT")
        except Exception as exc:
            diag = diagnostics_text(session)
            detail = f"{type(exc).__name__}: {exc} | {diag}"
            if ILLEGAL_SIGNATURE in detail and "actual=0B" in detail:
                status = "UNREADABLE"
            else:
                m = ACTUAL_RE.search(detail)
                status = "OVERSIZED" if m and int(m.group(1)) > 2 else "ERROR"
            outcomes.append((status, "", detail))
            say(f"R{addr} attempt {n}/{attempts}: {status}")
        await asyncio.sleep(INTER_PROBE_DELAY)

    statuses = [x[0] for x in outcomes]
    readable = [x for x in outcomes if x[0] == "READABLE"]
    if readable:
        vals = {x[1] for x in readable}
        final = "READABLE"
        value = readable[-1][1]
        detail = f"attempt_statuses={statuses}; readable_values={sorted(vals)}"
    elif all(x == "UNREADABLE" for x in statuses):
        final, value = "UNREADABLE", ""
        detail = f"attempt_statuses={statuses}"
    else:
        final, value = "AMBIGUOUS", ""
        detail = f"attempt_statuses={statuses}"
    # Preserve full attempt diagnostics in the chronological log, not the concise state/map.
    for i, (s, v, d) in enumerate(outcomes, 1):
        append(CLEANUP_LOG, f"{stamp()}  DETAIL R{addr} attempt={i} status={s} value={v} {d}")
    return final, value, detail


async def main() -> None:
    original = parse_original_state()
    if len(original) != 65536:
        raise RuntimeError(f"Original state has {len(original)} unique addresses; expected 65536")

    cleanup = load_cleanup_results()
    legacy = [a for a, r in original.items() if is_legacy_illegal(r)]
    ambiguous = [a for a, r in original.items() if r["status"] in ("ERROR", "TIMEOUT") and not is_legacy_illegal(r)]
    oversized = [a for a in ambiguous if is_oversized(original[a])]

    if not CLEANUP_STATE.exists():
        append(CLEANUP_STATE, "# EL30V2 Register Cleanup State")
        append(CLEANUP_STATE, f"# Version: {VERSION}; READ-ONLY; original scan is never modified")
    say(f"Loaded complete original state: {len(original)} addresses")
    say(f"Legacy illegal-address errors analytically reclassified UNREADABLE: {len(legacy)}")
    say(f"Addresses requiring physical read: {len(ambiguous)} (oversized-response subset: {len(oversized)})")

    pending = [a for a in ambiguous if a not in cleanup]
    if not pending:
        say("No physical cleanup reads remain; regenerating definitive map.")
        write_map(original, cleanup)
        return

    loop = asyncio.get_running_loop()
    session = DeviceSession(
        ADDRESS, EL30V2(), loop.create_future,
        config=DeviceSessionConfig(
            timeout=60, use_encryption=True,
            command_timeout=COMMAND_TIMEOUT, command_retries=0, retry_delay=0.0,
        ),
    )
    try:
        say(f"Connecting to {ADDRESS}...")
        await session.connect()
        say(f"Connected={session.is_connected} Ready={session.is_ready}")
        await health(session)

        done = 0
        for addr in pending:
            attempts = OVERSIZED_RETRIES if addr in oversized else NORMAL_RETRIES
            final, value, detail = await reread_address(session, addr, attempts)
            record_result(addr, final, value, detail)
            cleanup[addr] = {"status": final, "value": value, "detail": detail}
            done += 1
            if done % 25 == 0 or done == len(pending):
                say(f"Progress {done}/{len(pending)} cleanup addresses this run")
            if done % HEALTH_EVERY == 0:
                await health(session)

        await health(session)
        write_map(original, cleanup)
        unresolved = sum(1 for r in cleanup.values() if r["status"] not in ("READABLE", "UNREADABLE"))
        say(f"CLEANUP COMPLETE. Physical addresses processed={done}; unresolved cleanup results={unresolved}")
        say(f"Definitive map written: {CLEANUP_MAP.name}")
    except KeyboardInterrupt:
        write_map(original, cleanup)
        say("Interrupted. State preserved; rerun the same command to resume.")
    except Exception as exc:
        write_map(original, cleanup)
        say(f"STOPPED: {type(exc).__name__}: {exc}")
        say("State preserved; rerun the same command to resume.")
        raise
    finally:
        try:
            await session.disconnect()
        except Exception:
            pass
        say("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
