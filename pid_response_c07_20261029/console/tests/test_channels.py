"""Channel isolation checks; these tests never open real instruments."""
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Calibration, Controller, FPGA, Handler


class FakeSerial:
    def __init__(self, **kwargs):
        self.rx = bytearray()
        self.writes = []
        self.registers = {0x3f: 0xA624, 0x38: 0x5030, 0x2f: 0xD216, 0x2e: 1, 0x18: 9468}
    def open(self): pass
    def close(self): pass
    @property
    def in_waiting(self): return len(self.rx)
    def write(self, data):
        if data[1] == 1:
            self.writes.append(bytes(data))
            self.registers[data[2]] = data[3] + 256 * data[4]
        else:
            self.rx.extend(b'noise' + FPGA.frame(3, 3, 9000) + FPGA.frame(2, data[2], self.registers.get(data[2], 0)))
    def read(self, n):
        data = bytes(self.rx[:n])
        del self.rx[:n]
        return data


class ChannelTests(unittest.TestCase):
    def test_uart_connection_does_not_write(self):
        device = FPGA('COM9', FakeSerial)
        self.assertEqual(device.serial.writes, [])
        device.write_verified(0x18, 9500)
        self.assertEqual(device.read(0x18), 9500)
        device.close()

    def test_channel_state_and_wrong_wavelength_calibration(self):
        a, b = Controller(556), Controller(399)
        try:
            a.state['meter']['power_mw'] = 10
            self.assertIsNone(b.state['meter']['power_mw'])
            self.assertEqual(b.snapshot()['wavelength_nm'], 399)
            payload = json.loads((ROOT/'data'/'calibration.json').read_text(encoding='utf-8'))
            with self.assertRaisesRegex(ValueError, '399'):
                b.target(10, 2, payload)
        finally:
            a.close(); b.close()

    def test_power_meter_receives_each_channels_wavelength(self):
        class Meter:
            def __init__(self, serial, wavelength):
                self.identity = {'serial': serial, 'wavelength_nm': wavelength}
            def read_mw(self): return 5.0
            def close(self): pass
        for wave in (556, 399):
            c = Controller(wave)
            try:
                with patch('pm_driver.ThorlabsPowerMeter', Meter):
                    c.connect_meter('TEST_ONLY')
                    self.assertEqual(c.snapshot()['meter']['identity']['wavelength_nm'], wave)
            finally:
                c.close()

    def test_shared_instrument_is_rejected(self):
        a, b = Controller(556), Controller(399)
        try:
            a.state['meter'].update(connected=True, identity={'serial': 'SAME_METER'})
            h = Handler.__new__(Handler)
            h.path = '/api/connect/meter?channel=399'
            import threading
            h.server = types.SimpleNamespace(controllers={'556': a, '399': b}, connection_lock=threading.RLock())
            with self.assertRaisesRegex(ValueError, '556'):
                h.connect_device('meter', 'SAME_METER')
        finally:
            a.close(); b.close()

    def test_existing_556_calibration_is_preserved(self):
        raw = json.loads((ROOT/'data'/'calibration.json').read_text(encoding='utf-8'))
        c = Calibration(raw)
        self.assertEqual(raw['wavelength_nm'], 556)
        self.assertEqual(len(c.points), 31)
        self.assertEqual(c.to_code(10), 9468)
        for power, code in c.points:
            self.assertAlmostEqual(c.to_power(code), power)


if __name__ == '__main__':
    unittest.main()
