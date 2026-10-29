"""Vendor API contract checks for both meter models; no instruments are opened."""
import ctypes as C
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pm_driver import ThorlabsPowerMeter, discover_meters


class FakeTLPMX:
    devices = [("PM100D3", "M01306624", "USB0::OLD::INSTR"),
               ("PM100USB", "P2007293", "USB0::NEW::INSTR")]

    def __init__(self):
        self.wavelength = 0
        self.power = .00582
        self.unit_readback = 0
        self.opened = None
        self.calls = []
        for name in ("findRsrc", "getRsrcName", "getRsrcInfo", "init", "close", "setTimeoutValue",
                     "setWavelength", "getWavelength", "setPowerUnit", "getPowerUnit", "measPower", "errorMessage"):
            setattr(self, "TLPMX_" + name, Mock(side_effect=lambda *args, n=name: self.invoke(n, args)))

    def invoke(self, name, args):
        self.calls.append((name, args))
        if name == "findRsrc":
            args[1]._obj.value = len(self.devices)
        elif name == "getRsrcName":
            args[2].value = self.devices[args[1]][2].encode()
        elif name == "getRsrcInfo":
            model, serial, _ = self.devices[args[1]]
            args[2].value, args[3].value, args[4].value = model.encode(), serial.encode(), b"Thorlabs"
            args[5]._obj.value = 1
        elif name == "init":
            self.opened = args[0].decode()
            args[3]._obj.value = 7
        elif name == "setWavelength":
            self.wavelength = args[1]
        elif name == "getWavelength":
            args[2]._obj.value = self.wavelength
        elif name == "getPowerUnit":
            args[1]._obj.value = self.unit_readback
        elif name == "measPower":
            args[1]._obj.value = self.power
        return 0


class PowerMeterTests(unittest.TestCase):
    def test_scan_lists_both_models_without_open_or_configure(self):
        dll = FakeTLPMX()
        with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
            devices = discover_meters()
        self.assertEqual([(d["model"], d["serial"]) for d in devices],
                         [("PM100D3", "M01306624"), ("PM100USB", "P2007293")])
        self.assertTrue(all(name in ("findRsrc", "getRsrcName", "getRsrcInfo") for name, _ in dll.calls))

    def test_both_models_use_selected_serial_wavelength_and_watts(self):
        for model, serial, resource in FakeTLPMX.devices:
            for wave in (556, 399):
                with self.subTest(model=model, wavelength=wave):
                    dll = FakeTLPMX()
                    with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
                        meter = ThorlabsPowerMeter(serial, wave)
                    self.assertEqual(dll.opened, resource)
                    self.assertEqual(meter.identity["model"], model)
                    self.assertEqual(meter.identity["wavelength_nm"], wave)
                    self.assertEqual(meter.identity["unit"], "W")
                    self.assertAlmostEqual(meter.read_mw(), 5.82)
                    self.assertEqual(dll.TLPMX_init.call_args.args[2], 0, "Do not reset instruments")
                    self.assertEqual(dll.TLPMX_setPowerUnit.call_args.args[1], 0)
                    meter.close()
                    self.assertEqual(meter.session.value, 0)

    def test_unplugged_old_serial_never_silently_connects_other_meter(self):
        dll = FakeTLPMX()
        dll.devices = [FakeTLPMX.devices[1]]
        with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
            with self.assertRaisesRegex(RuntimeError, "P2007293"):
                ThorlabsPowerMeter("M01306624")
        dll.TLPMX_init.assert_not_called()

    def test_open_handle_placeholder_is_not_a_second_selectable_meter(self):
        dll = FakeTLPMX()
        dll.devices = [("USB0::0x1313::0x8072::::INSTR", "n/a", "USB0::0x1313::0x8072::::INSTR")]
        with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
            self.assertEqual(discover_meters(), [])

    def test_unit_mismatch_closes_handle_before_reporting_failure(self):
        dll = FakeTLPMX()
        dll.unit_readback = 1
        with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
            with self.assertRaisesRegex(RuntimeError, "单位"):
                ThorlabsPowerMeter("P2007293")
        dll.TLPMX_close.assert_called_once()

    def test_nonfinite_power_is_rejected(self):
        dll = FakeTLPMX()
        with patch("pm_driver.C.WinDLL", return_value=dll, create=True):
            meter = ThorlabsPowerMeter("P2007293")
        dll.power = math.nan
        with self.assertRaisesRegex(RuntimeError, "无效"):
            meter.read_mw()
        meter.close()


if __name__ == "__main__":
    unittest.main()
