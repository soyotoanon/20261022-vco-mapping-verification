"""Offline fault-path tests.  Real serial access is forbidden in every test."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.dont_write_bytecode = True
import workpoint_measure as driver


def baseline():
    values = {hex(address): 0 for address in range(64)}
    values.update({hex(address): value for address, value in {
        0x00: 1, 0x03: 9199, 0x05: 6860, 0x06: 9199, 0x11: 4,
        0x18: 9199, 0x19: 6860, 0x1A: 0x8C00, 0x1B: 0xE000,
        0x1C: 0, 0x1D: 128, 0x1E: 1400, 0x1F: 15000,
        0x20: 1, 0x21: 16, 0x28: 1, 0x2C: 1, 0x2E: 1,
        0x32: 123, 0x33: 12, 0x35: 3, 0x37: 17, 0x3F: 0xA624,
    }.items()})
    return dict(complete=True, registers=values, register_writes=False)


class FakeHandle:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeDevice:
    instances = []
    fail_restore_gain = False

    def __init__(self, port, audit):
        self.regs = {int(key, 16): value for key, value in baseline()["registers"].items()}
        self.original_stream = self.regs[0x28]
        self.original_probe = 0
        self.raw_fs = 5e6
        self.snapshot = baseline()["registers"].copy()
        self.audit = audit
        self.writes = []
        self.s = FakeHandle()
        self.write(0x28, 0)
        self.instances.append(self)

    def read(self, address):
        return self.regs[address]

    def write(self, address, value):
        driver.mutation_frames(driver.Device.frame(1, address, value))
        self.writes.append((address, value))
        self.audit.event("fake_write", address=hex(address), value=value)
        if self.fail_restore_gain and self.audit.phase == "cleanup" and address == 0x1A:
            raise IOError("simulated gain-restore transport failure")
        self.regs[address] = value & 3 if address == 0x30 else value

    def require_closed_loop(self):
        if self.regs[0x01] & 0x3F:
            raise RuntimeError("simulated fault")


class InvalidMeasurement:
    def __init__(self, dev, folder, cancel, notify, prompt):
        self.folder = Path(folder)

    def sweep(self, low, high, amp, points=1):
        return [dict(frequency_Hz=low, requested_Hz=low, db=None, valid=False,
                     open_mean_ADC_codes=9199, closed_mean_ADC_codes=9199,
                     reason="offline test: open tone undetected")], None


class InterruptedMeasurement(InvalidMeasurement):
    def sweep(self, low, high, amp, points=1):
        raise KeyboardInterrupt("offline injected interruption")


class DriftedMeasurement(InvalidMeasurement):
    def sweep(self, low, high, amp, points=1):
        return [dict(frequency_Hz=low, requested_Hz=low, db=-5, valid=True,
                     open_mean_ADC_codes=9199, closed_mean_ADC_codes=9230,
                     reason="")], None


class DriverFaultTests(unittest.TestCase):
    def setUp(self):
        driver.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="_offline_test_", dir=driver.OUTPUT_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "session"
        FakeDevice.instances = []
        FakeDevice.fail_restore_gain = False
        self.addCleanup(setattr, FakeDevice, "fail_restore_gain", False)
        self.no_real_serial = patch.object(driver.serial, "Serial", side_effect=AssertionError("Real serial forbidden"))
        self.no_real_serial.start()
        self.addCleanup(self.no_real_serial.stop)

    def args(self, *extra):
        return driver.parser().parse_args(["screen", "--workpoint-label", "offline_test", *extra])

    def run_fake(self, measurement, *extra):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(driver.time, "sleep"):
            return driver.run_session(self.args(*extra), folder=self.folder,
                                      snapshot_fn=lambda port: baseline(),
                                      device_factory=FakeDevice,
                                      measurement_factory=measurement)

    def test_all_invalid_points_are_durable_and_original_gains_restore(self):
        code, folder = self.run_fake(InvalidMeasurement, "--kp", "11", "--ki", "0.7")
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        candidate = json.loads((folder / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(status["status"], "failed_all_points_invalid")
        self.assertEqual(len(status["rows"]), 8)
        self.assertEqual(candidate["tested_gains"]["kp"], 11)
        self.assertEqual(candidate["tested_registers"]["0x1a"], driver.encode_gain(11, "kp"))
        self.assertTrue(status["restoration"]["parameters_restored"])
        dev = FakeDevice.instances[0]
        for address in driver.PROTECTED_REGS + driver.RESTORABLE_REGS:
            self.assertEqual(dev.regs[address], driver.register(baseline(), address))
        self.assertTrue(dev.s.closed)
        self.assertEqual(dev.snapshot["0x1a"], driver.encode_gain(11, "kp"))
        self.assertFalse(any(address in driver.PROTECTED_REGS for address, value in dev.writes))

    def test_interrupt_restores_gains_probe_stream_and_decimation(self):
        code, folder = self.run_fake(InterruptedMeasurement, "--kp", "11.5")
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "interrupted")
        self.assertEqual(len(status["point_errors"]), 1)
        self.assertTrue(status["restoration"]["parameters_restored"])
        self.assertTrue((folder / "candidate.json").is_file())
        self.assertTrue(FakeDevice.instances[0].s.closed)

    def test_failed_gain_restore_retains_hold_and_reports_failure(self):
        FakeDevice.fail_restore_gain = True
        code, folder = self.run_fake(InterruptedMeasurement, "--kp", "11.5")
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "failed_cleanup")
        self.assertTrue(status["restoration"]["hold_retained_due_to_gain_restore_failure"])
        self.assertEqual(FakeDevice.instances[0].regs[0x30], 2)
        self.assertEqual(FakeDevice.instances[0].regs[0x28], 1)

    def test_unhealthy_preflight_never_constructs_a_device(self):
        original = baseline()
        original["registers"]["0x1"] = 2  # latched OTR also blocks this driver
        with contextlib.redirect_stdout(io.StringIO()):
            code, folder = driver.run_session(self.args(), folder=self.folder,
                snapshot_fn=lambda port: original,
                device_factory=lambda *unused: self.fail("Device constructor must not run"))
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "failed")
        self.assertFalse(status["driver_register_writes_started"])
        self.assertTrue((folder / "fpga_before.json").is_file())

    def test_forbidden_batched_mutation_is_rejected_before_send(self):
        raw = FakeHandle()
        raw.write = lambda data: self.fail("Forbidden bytes must not reach transport")
        audit_folder = self.folder
        audit_folder.mkdir()
        proxy = driver.AuditedSerial(raw, driver.Audit(audit_folder))
        batch = driver.Device.frame(1, 0x37, 5) + driver.Device.frame(1, 0x18, 9000)
        with self.assertRaisesRegex(RuntimeError, "Forbidden register"):
            proxy.write(batch)

    def test_a623_and_unsupported_capture_rate_are_refused(self):
        original = baseline()
        original["registers"]["0x3f"] = 0xA623
        with self.assertRaisesRegex(RuntimeError, "A624"):
            driver.validate_snapshot(original, driver.FREQUENCIES["screen"], 32)
        original = baseline()
        original["registers"]["0x11"] = 499
        with self.assertRaisesRegex(ValueError, "measurement limit"):
            driver.validate_snapshot(original, driver.FREQUENCIES["screen"], 32)

    def test_different_optical_dc_invalidates_an_otherwise_valid_ratio(self):
        code, folder = self.run_fake(DriftedMeasurement)
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(status["status"], "failed_all_points_invalid")
        self.assertFalse(status["rows"][0]["valid"])
        self.assertEqual(status["rows"][0]["mean_shift_ADC_codes"], 31)
        self.assertIn("exceeding 20", status["rows"][0]["reason"])
        self.assertTrue((folder / "f010000Hz" / "validated_result.json").is_file())

    def test_preflight_target_drift_over_30_codes_is_refused(self):
        original = baseline()
        original["registers"]["0x3"] += 31
        with self.assertRaisesRegex(RuntimeError, "30 codes"):
            driver.validate_snapshot(original, driver.FREQUENCIES["screen"], 32)

    def test_open_race_restores_only_constructor_stream(self):
        class RaceDevice(FakeDevice):
            def __init__(self, port, audit):
                super().__init__(port, audit)
                self.regs[0x30] = 3
                self.regs[0x31] = 99
                self.regs[0x32] = 1234
                self.original_stream = 7

        with contextlib.redirect_stdout(io.StringIO()):
            code, folder = driver.run_session(self.args(), folder=self.folder,
                snapshot_fn=lambda port: baseline(), device_factory=RaceDevice,
                measurement_factory=InvalidMeasurement)
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        dev = FakeDevice.instances[0]
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "failed")
        self.assertIn("changed between", status["error"])
        self.assertEqual(status["restoration"]["scope"], "constructor_stream_only")
        self.assertEqual(dev.regs[0x28], 7)
        self.assertEqual(dev.regs[0x30], 3)
        self.assertEqual(dev.regs[0x31], 99)
        self.assertEqual(dev.regs[0x32], 1234)
        self.assertTrue(all(address == 0x28 for address, value in dev.writes))

    def test_initial_snapshot_uses_read_commands_only(self):
        class SnapshotHandle:
            def __init__(self, **unused):
                self.is_open = False
                self.buffer = bytearray()
                self.frames = []
                self.dtr = self.rts = None

            @property
            def in_waiting(self):
                return len(self.buffer)

            def open(self):
                self.is_open = True
                if self.dtr is not False or self.rts is not False:
                    raise AssertionError("DTR/RTS must be disabled before open")

            def close(self):
                self.is_open = False

            def write(self, payload):
                self.frames.append(bytes(payload))
                if payload[1] != 2:
                    raise AssertionError("Initial snapshot must never send a mutation command")
                value = driver.register(baseline(), payload[2])
                self.buffer.extend(driver.Device.frame(2, payload[2], value))
                return len(payload)

            def read(self, count):
                value = self.buffer[:count]
                del self.buffer[:count]
                return bytes(value)

        handle = SnapshotHandle()
        with patch.object(driver.serial, "Serial", return_value=handle):
            report = driver.pure_snapshot("FAKE_ONLY")
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["registers"]), 64)
        self.assertEqual(handle.frames[0][2], 0x3F)
        self.assertFalse(handle.is_open)
        self.assertTrue(all(frame[1] == 2 for frame in handle.frames))

    def test_explicit_latched_otr_override_permits_only_status_bit1(self):
        original = baseline()
        original["registers"]["0x1"] = 2
        with self.assertRaisesRegex(RuntimeError, "status is nonzero"):
            driver.validate_snapshot(original, driver.FREQUENCIES["screen"], 32)
        self.assertEqual(driver.validate_snapshot(
            original, driver.FREQUENCIES["screen"], 32, allow_latched_otr=True), 5e6)
        for bit in (0, 2, 3, 4, 5):
            original["registers"]["0x1"] = 2 | (1 << bit)
            with self.assertRaisesRegex(RuntimeError, "status is nonzero"):
                driver.validate_snapshot(original, driver.FREQUENCIES["screen"], 32,
                                         allow_latched_otr=True)

    def test_latched_otr_is_recorded_and_cleanup_allows_only_that_bit(self):
        class LatchedDevice(FakeDevice):
            def __init__(self, port, audit):
                super().__init__(port, audit)
                self.regs[0x01] = 2

            def require_closed_loop(self):
                if self.regs[0x01] & 0x3D:
                    raise RuntimeError("other fault")

        original = baseline()
        original["registers"]["0x1"] = 2
        with contextlib.redirect_stdout(io.StringIO()), patch.object(driver.time, "sleep"):
            code, folder = driver.run_session(self.args("--allow-latched-otr"), folder=self.folder,
                snapshot_fn=lambda port: original, device_factory=LatchedDevice,
                measurement_factory=InvalidMeasurement)
        report = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        snapshot = json.loads((folder / "fpga_before.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 2)  # invalid tones, not an OTR/cleanup false failure
        self.assertEqual(report["status"], "failed_all_points_invalid")
        self.assertEqual(report["allowed_latched_status_bits"], 2)
        self.assertEqual(snapshot["status_interpretation"]["observed_allowed_latched_status_bits"], 2)
        self.assertTrue(report["restoration"]["parameters_restored"])
        self.assertEqual(report["restoration"]["observed_allowed_latched_status_bits"], 2)

    def test_allow_latched_otr_never_allows_current_capture_fault_or_clip(self):
        self.folder.mkdir()
        audit = driver.Audit(self.folder)
        audit.allowed_latched_status_bits = 2
        dev = FakeDevice("FAKE_ONLY", audit)
        for capture_flags in (4, 8, 12):
            point_folder = self.folder / f"capture_flags_{capture_flags}"
            meta = dict(fs=5e6, raw_fs=5e6, decimation=1, sequence=1, status=capture_flags)
            dev.capture = lambda *unused, m=meta: (np.full(8192, 9199.0), m)
            measurement = driver.Measurement(dev, point_folder, None,
                                              lambda *unused: None, lambda *unused: False)
            with self.assertRaisesRegex(RuntimeError, "采集期间出现限幅或环路故障"):
                measurement.record("tone_open", 0)
            self.assertTrue((point_folder / "001_tone_open.npz").is_file())


if __name__ == "__main__":
    unittest.main()
