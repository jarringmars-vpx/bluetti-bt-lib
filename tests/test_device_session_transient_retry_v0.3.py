from __future__ import annotations
import argparse, asyncio, os, time
from datetime import datetime
from pathlib import Path
from bluetti_bt_lib.bluetooth.device_session import DeviceSession, DeviceSessionConfig
from bluetti_bt_lib.devices import EL30V2

DEFAULT_ADDRESS = "DC:B4:D9:54:C6:86"

async def run(address: str, duration: float) -> int:
    d=Path("tests/registers/logs"); d.mkdir(parents=True, exist_ok=True)
    log_path=d/f"community_el30v2_device_session_retry_v0.3_{datetime.now():%Y%m%d_%H%M%S}.txt"
    t0=time.monotonic()
    def log(msg):
        line=f"{datetime.now().isoformat(timespec='milliseconds')} | +{time.monotonic()-t0:8.3f}s | {msg}"
        print(line, flush=True)
        with log_path.open("a",encoding="utf-8") as f: f.write(line+"\n")

    loop=asyncio.get_running_loop()
    session=DeviceSession(
        address, EL30V2(), loop.create_future,
        config=DeviceSessionConfig(
            timeout=60, use_encryption=True,
            command_timeout=5.0, command_retries=1, retry_delay=0.4
        ),
    )
    log("DeviceSession transient-timeout recovery validation v0.3")
    try:
        log("Connecting/authenticating once...")
        await session.connect()
        log(f"CONNECTED connected={session.is_connected} ready={session.is_ready}")
        print("\nPhysically toggle AC Output OFF and ON several times while reads run.")
        print("Do not use the BLUETTI mobile app during this test.")
        input("Press Enter to begin 90-second validation... ")

        end=time.monotonic()+duration; polls=failures=0
        while time.monotonic()<end:
            polls+=1; begun=time.monotonic()
            log(f"POLL_BEGIN #{polls} connected={session.is_connected} ready={session.is_ready}")
            try:
                data=await session.read()
                log(
                    f"POLL_OK #{polls} elapsed={time.monotonic()-begun:.3f}s "
                    f"SOC={data.get('total_battery_percent') if data else None} "
                    f"AC={data.get('ctrl_ac') if data else None} "
                    f"DC={data.get('ctrl_dc') if data else None} "
                    f"connected={session.is_connected} ready={session.is_ready}"
                )
            except Exception as exc:
                failures+=1
                log(
                    f"POLL_ERROR #{polls} elapsed={time.monotonic()-begun:.3f}s "
                    f"type={type(exc).__name__} message={exc!r} "
                    f"connected={session.is_connected} ready={session.is_ready}"
                )
            await asyncio.sleep(0.5)

        log(f"SUMMARY polls={polls} failures={failures} connected={session.is_connected} ready={session.is_ready}")
        print(f"\nLog saved to: {log_path}")
        return 0
    finally:
        log(f"DISCONNECT_BEGIN connected={session.is_connected} ready={session.is_ready}")
        await session.disconnect()
        log(f"DISCONNECT_END connected={session.is_connected} ready={session.is_ready}")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--address",default=os.environ.get("BLUETTI_BLE_ADDRESS",DEFAULT_ADDRESS))
    ap.add_argument("--duration",type=float,default=90.0)
    a=ap.parse_args()
    return asyncio.run(run(a.address,max(20.0,a.duration)))

if __name__=="__main__":
    raise SystemExit(main())
