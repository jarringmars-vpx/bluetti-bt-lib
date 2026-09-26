import unittest
from bluetti_bt_lib.devices import AP300
from bluetti_bt_lib.fields import FieldName


class TestAP300Definition(unittest.TestCase):
    def test_confirmed_fields(self):
        device = AP300()
        addresses = {field.name: field.address for field in device.fields}
        self.assertEqual(addresses[FieldName.BATTERY_SOC.value], 102)
        self.assertEqual(addresses[FieldName.UNKNOWN_COUNTER_154.value], 154)
        self.assertEqual(addresses[FieldName.AC_OUTPUT_POWER.value], 142)
        self.assertEqual(addresses[FieldName.AC_INPUT_POWER.value], 146)
        self.assertEqual(addresses[FieldName.AC_INPUT_VOLTAGE.value], 1314)
        self.assertEqual(addresses[FieldName.CTRL_AC.value], 2011)
        self.assertEqual(addresses[FieldName.CTRL_ECO_SHUTDOWN_HOURS_AC.value], 2018)
        self.assertNotIn(FieldName.TEMPERATURE.value, addresses)
