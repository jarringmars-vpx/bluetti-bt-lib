from ..base_devices import BluettiDevice
from ..enums import HAACOutputState
from ..fields import BoolField, EnumField, FieldName, UIntField
from ..registers import ReadableRegisters


class HA(BluettiDevice):
    """BLUETTI HA system endpoint (confirmed read-side definition).

    Exhaustive testing found that HA slaves 0 and 4 respond in the R154-R175
    block while ordinary EL30V2/AP300 telemetry registers time out there.
    Confirmed useful HA-level semantics are R161 inverter state and R171 HA AC
    output state. R154 is a counter-like value that increments about every five
    minutes, but its purpose is not yet known.

    Aggregate SOC and power-flow telemetry are *not* HA registers: applications
    obtain them from the member AP300 slave addresses. BLUETTI displays HA SOC
    as the arithmetic mean of member AP300 SOC values. Power totals are derived
    from the corresponding member AP300 measurements.

    The default definition targets slave 0. The same HA status behavior has
    also been observed on slave 4 and may be queried by applications explicitly.
    """

    def __init__(self):
        super().__init__([
            UIntField(FieldName.UNKNOWN_COUNTER_154, 154),
            BoolField(FieldName.INVERTER_ON, 161),
            EnumField(FieldName.HA_AC_OUTPUT_STATE, 171, HAACOutputState),
        ])
        self.polling_registers = [
            ReadableRegisters(154, 1, slave_address=0),
            ReadableRegisters(161, 1, slave_address=0),
            ReadableRegisters(171, 1, slave_address=0),
        ]

    def get_full_registers_range(self):
        # R154-R175 is the HA block known to respond during exhaustive scans.
        return [ReadableRegisters(154, 22, slave_address=0)]

    def get_device_type_registers(self):
        # Device identity is obtained from member AP300s, not HA slaves 0/4.
        return []

    def get_device_sn_registers(self):
        return []

    def get_iot_version(self):
        return 2
