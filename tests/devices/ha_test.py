import unittest
from bluetti_bt_lib.devices import HA
from bluetti_bt_lib.enums import HAACOutputState
from bluetti_bt_lib.fields import FieldName


class TestHADefinition(unittest.TestCase):
    def test_confirmed_status_fields(self):
        device = HA()
        parsed = device.parse(154, bytes.fromhex("0C81"))
        self.assertEqual(parsed[FieldName.UNKNOWN_COUNTER_154.value], 3201)
        parsed = device.parse(161, bytes.fromhex("0001"))
        self.assertTrue(parsed[FieldName.INVERTER_ON.value])
        parsed = device.parse(171, bytes.fromhex("0022"))
        self.assertEqual(parsed[FieldName.HA_AC_OUTPUT_STATE.value], HAACOutputState.ON)
        parsed = device.parse(171, bytes.fromhex("0020"))
        self.assertEqual(parsed[FieldName.HA_AC_OUTPUT_STATE.value], HAACOutputState.OFF)
        self.assertTrue(all(r.slave_address == 0 for r in device.get_polling_registers()))
        self.assertEqual(device.get_device_type_registers(), [])
        self.assertEqual(device.get_device_sn_registers(), [])
