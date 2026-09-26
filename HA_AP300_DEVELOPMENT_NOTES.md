# HA / AP300 device-definition development notes — v0.2

## Confirmed HA behavior
- HA Modbus slaves 0 and 4 respond in the R154-R175 block used by the scanner.
- R161: inverter state (`0` off, `1` on).
- R171: HA AC output state (`32` off, `34` on), read-side confirmed. Write behavior is not yet confirmed.
- R154: unknown counter-like value; observed changing about once every five minutes. Do not label it PV generation.
- EL30V2/AP300 telemetry register ranges time out when queried as HA slave 0/4 telemetry.
- HA aggregate SOC/power values are derived from member AP300 telemetry, not separate HA aggregate registers.
- BLUETTI HA SOC is the arithmetic mean of the member AP300 SOC percentages.

## AP300 behavior
- Tested AP300 telemetry/control registers substantially match EL30V2 when queried at the AP300 member slave address.
- R1153 temperature is not included for AP300 because no AP300 temperature response has been observed.
- R154 is exposed only as `unknown_counter_154`; its purpose is not known.
- R1314 AC input voltage uses 0.1 V scaling.
- R2018 is AC ECO shutdown duration: whole-number hours 1 through 4.

## Still unknown / intentionally not guessed
- R154 semantic purpose.
- Individual AP300 PV input 1 / PV input 2 voltage and watt registers.
- HA R171 write behavior.
