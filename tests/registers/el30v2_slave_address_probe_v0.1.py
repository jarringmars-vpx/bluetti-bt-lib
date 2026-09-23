#!/usr/bin/env python3
"""EL30V2 read-only Modbus slave-address probe v0.1."""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime
from pathlib import Path

from bluetti_bt_lib.bluetooth.device_session import DeviceSession, DeviceSessionConfig
from bluetti_bt_lib.devices import EL30V2
from bluetti_bt_lib.registers import ReadableRegisters
from bluetti_bt_lib.registers.DeviceRegister import modbus_crc


VERSION = "0.1"
DEFAULT_ADDRESS = "DC:B4:D9:54:C6:86"

# All registers below are already known/readable on the EL30V2.
TESTS = [
    ("SOC", 102, 1),
    ("DEVICE_TYPE", 110, 6),
    ("SERIAL", 116, 4),
    ("POWER_GENERATION_KWH", 154, 1),
    ("DC_OUTPUT_W", 140, 1),
    ("AC_OUTPUT_W", 142, 1),
    ("DC_INPUT_W", 144, 1),
    ("AC_INPUT_W", 146, 1),
    ("TEMPERATURE", 1153, 1),
    ("AC_INPUT_VOLTAGE", 1314, 1),
    ("AC_OUTPUT_STATE", 2011, 1),
    ("DC_OUTPUT_STATE", 2012, 1),
    ("CHARGING_MODE", 2020, 1),
]


def make_read(start: int, count: int, slave: int) -> ReadableRegisters:
    req = ReadableRegisters(start, count)
    req.cmd[0] = slave
    crc = modbus_crc(req.cmd[:-2])
    req.cmd[-2:] = crc.to_bytes(2, "little")
    return req


def parse(req: ReadableRegisters, response: bytes):
    if len(response) < 5:
        raise ValueError(f"short response ({len(response)} bytes)")
    if not req.is_valid_response(response):
        raise ValueError("bad Modbus CRC")
    if req.is_exception_response(response):
        code = response[2] if len(response) > 2 else None
        raise ValueError(f"Modbus exception code={code}")

    body = req.parse_response(response)
    if len(body) != req.quantity * 2:
        raise ValueError(
            f"expected {req.quantity * 2} data bytes, got {len(body)}"
        )

    values = [
        int.from_bytes(body[i * 2 : i * 2 + 2], "big")
        for i in range(req.quantity)
    ]
    return body, values


def decode_type(body: bytes) -> str:
    data = bytearray(body)
    for i in range(0, len(data) - 1, 2):
        data[i], data[i + 1] = data[i + 1], data[i]
    return bytes(data).rstrip(b"\0").decode("ascii", errors="replace")


def decode_serial(values):
    if len(values) != 4:
        return None
    return (
        values[0]
        + (values[1] << 16)
        + (values[2] << 32)
        + (values[3] << 48)
    )


async def read_registers(session, slave, start, count):
    req = make_read(start, count, slave)
    tx = bytes(req)
    started = time.monotonic()

    async with session.command_lock:
        rx = await session._async_send_command(req)

    body, values = parse(req, rx)
    return tx, rx, body, values, time.monotonic() - started


async def main():
    parser = argparse.ArgumentParser(
        description="Read-only EL30V2 Modbus slave-address probe"
    )
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--slaves", default="0,1,2,3,4,5")
    args = parser.parse_args()

    slaves = [
        int(value.strip(), 0)
        for value in args.slaves.split(",")
        if value.strip()
    ]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path.cwd() / f"EL30V2_slave_probe_{stamp}.txt"

    def log(message: str):
        line = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} "
            f"{message}"
        )
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    loop = asyncio.get_running_loop()
    session = DeviceSession(
        args.address,
        EL30V2(),
        loop.create_future,
        config=DeviceSessionConfig(
            timeout=60,
            use_encryption=True,
            command_timeout=3.0,
            command_retries=0,
            retry_delay=0.0,
        ),
    )

    log(f"EL30V2 slave-address probe v{VERSION}")
    log(f"BLE={args.address} slaves={slaves}")
    log("READ ONLY: no register writes are performed")

    try:
        log("Connecting...")
        await session.connect()
        log(
            f"CONNECTED connected={session.is_connected} "
            f"ready={session.is_ready}"
        )

        for slave in slaves:
            log(f"--- SLAVE {slave} ---")

            identity_real = False

            for name, start, count in TESTS:
                try:
                    tx, rx, body, values, elapsed = await read_registers(
                        session, slave, start, count
                    )

                    decoded = ""
                    if name == "DEVICE_TYPE":
                        device_type = decode_type(body)
                        decoded = f" decoded={device_type!r}"
                        if device_type:
                            identity_real = True
                    elif name == "SERIAL":
                        serial = decode_serial(values)
                        decoded = f" decoded={serial}"
                        if serial:
                            identity_real = True
                    elif count == 1:
                        decoded = f" value={values[0]}"

                    log(
                        f"SLAVE={slave} {name} R{start}/{count} OK"
                        f"{decoded} values={values} elapsed={elapsed:.3f}s "
                        f"TX={tx.hex(' ').upper()} "
                        f"RX={rx.hex(' ').upper()}"
                    )

                except asyncio.TimeoutError:
                    log(
                        f"SLAVE={slave} {name} R{start}/{count} TIMEOUT"
                    )
                except Exception as exc:
                    log(
                        f"SLAVE={slave} {name} R{start}/{count} ERROR "
                        f"{type(exc).__name__}: {exc}"
                    )

                await asyncio.sleep(0.15)

            log(
                f"SLAVE={slave} IDENTITY_RESULT="
                f"{'REAL_DEVICE' if identity_real else 'NO_DEVICE_IDENTITY'}"
            )

        log("Probe complete.")

    finally:
        try:
            await session.disconnect()
        except Exception as exc:
            log(
                f"Disconnect warning: {type(exc).__name__}: {exc}"
            )

        log(f"Log file: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
