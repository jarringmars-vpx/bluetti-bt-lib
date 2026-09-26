import unittest
from bluetti_bt_lib.registers import ReadableRegisters, WriteableRegister


class TestSlaveAddress(unittest.TestCase):
    def test_read_slave_address(self):
        self.assertEqual(bytes(ReadableRegisters(2011, 1, slave_address=2))[0], 2)

    def test_write_slave_address(self):
        self.assertEqual(bytes(WriteableRegister(2011, 1, slave_address=4))[0], 4)

    def test_default_is_backwards_compatible(self):
        self.assertEqual(bytes(ReadableRegisters(2011, 1))[0], 1)
