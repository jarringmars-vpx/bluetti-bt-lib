from ..base_devices import BaseDeviceV2
from ..enums import ChargingMode, EcoMode
from ..fields import (
    FieldName,
    UIntField,
    DecimalField,
    SwitchField,
    SelectField,
    SerialNumberField,
)


class AP300(BaseDeviceV2):
    """AP300 register map confirmed while attached through an HA system.

    The tested AP300 map substantially matches EL30V2. Temperature register
    1153 is intentionally omitted because no AP300 temperature response has
    been observed there. Register 154 is also intentionally omitted pending
    identification; testing shows it behaves as an independent per-device
    counter that increments roughly every five minutes. It is exposed with an
    explicitly unknown semantic name rather than being guessed as PV data.
    """

    def __init__(self):
        super().__init__(
            [
                DecimalField(FieldName.TIME_REMAINING, 104, 4, 167),
                UIntField(FieldName.UNKNOWN_COUNTER_154, 154),
                UIntField(FieldName.DC_OUTPUT_POWER, 140),
                UIntField(FieldName.AC_OUTPUT_POWER, 142),
                UIntField(FieldName.DC_INPUT_POWER, 144),
                UIntField(FieldName.AC_INPUT_POWER, 146),
                DecimalField(FieldName.AC_INPUT_VOLTAGE, 1314, 1),
                SerialNumberField(FieldName.COMMUNICATION_BOARD_SERIAL, 11006),
                SwitchField(FieldName.CTRL_AC, 2011),
                SwitchField(FieldName.CTRL_DC, 2012),
                SwitchField(FieldName.CTRL_ECO_DC, 2014),
                SelectField(FieldName.CTRL_ECO_TIME_MODE_DC, 2015, EcoMode),
                UIntField(FieldName.CTRL_ECO_MIN_POWER_DC, 2016),
                SwitchField(FieldName.CTRL_ECO_AC, 2017),
                # 1..4 represent the number of hours before AC ECO shutdown.
                SelectField(FieldName.CTRL_ECO_SHUTDOWN_HOURS_AC, 2018, EcoMode),
                UIntField(FieldName.CTRL_ECO_MIN_POWER_AC, 2019),
                SelectField(FieldName.CTRL_CHARGING_MODE, 2020, ChargingMode),
                SwitchField(FieldName.CTRL_POWER_LIFTING, 2021),
            ],
        )
