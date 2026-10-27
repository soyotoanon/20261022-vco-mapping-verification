"""Offline register-model checks only; this module never opens a hardware port."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import configure_workpoint as subject


class MockUART:
    def __init__(self, *, firmware=0xA624, status=0, minimum=1400,
                 dark=False, flat=False, arm=1, fail_gain_readback=False,
                 latched_otr=False, status_on_enable=0, refuse_arm=False):
        self.registers = dict.fromkeys(range(64), 0)
        self.registers.update({0: 1, 1: status, 2: 3855, 3: 8172, 5: 6860,
                               0x11: 4, 0x18: 9199, 0x19: 6860,
                               0x1A: 0x8C00, 0x1B: 0xE000,
                               0x1E: minimum, 0x1F: 15000,
                               0x20: 1, 0x2E: arm, 0x3F: firmware})
        self.original = dict(self.registers)
        self.writes = []
        self.dark = dark
        self.flat = flat
        self.latched_otr = latched_otr
        self.status_on_enable = status_on_enable
        self.refuse_arm = refuse_arm
        if latched_otr:
            self.registers[1] |= 2
        self.fail_gain_readback = fail_gain_readback
        self.failed_once = False
        self.closed = False

    def read(self, address):
        if address == 1:
            return self.registers[1] | (2 if self.latched_otr else 0)
        if address == 3:
            if self.flat:
                return 8100
            return 8020 if self.dark else round(8000 + (self.registers[5] - 6000) * .2)
        if (address == 0x1A and self.fail_gain_readback
                and not self.failed_once and any(a == 0x1A for a, _ in self.writes)):
            self.failed_once = True
            return 0
        return self.registers[address]

    def write(self, address, value):
        self.writes.append((address, value))
        if address == 0x30:
            self.registers[address] = value & 3
        elif address == 0x2E:
            self.registers[address] = int(value == 0xA501 and not self.refuse_arm)
        else:
            self.registers[address] = value
        if address == 2 and self.registers[0] & 15 == 8:
            self.registers[5] = value
        if address == 0:
            if value & 15 == 8:
                self.registers[5] = self.registers[2]
            if value & 16:
                self.registers[1] = 0
            if value & 15 == 1:
                self.registers[5] = self.registers[0x19]
                self.registers[1] |= self.status_on_enable

    def close(self):
        self.closed = True


class ConfigurationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="configure_mock_", dir=subject.WORKSPACE / "work")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_case(self, uart, **overrides):
        args = dict(dac_code=6500, dark_reference=8000.0)
        args.update(overrides)
        dwell = []
        folder, result = subject.run_setup(subject.Config(**args),
            transport_factory=lambda port: uart, sleeper=dwell.append, output_root=self.root)
        return folder, result, dwell

    def test_a624_success_order_ramp_and_original_arm(self):
        uart = MockUART()
        folder, result, dwell = self.run_case(uart)
        self.assertTrue(result["success"])
        self.assertEqual(uart.writes[:2], [(2, 6860), (0, 8)])
        self.assertEqual(uart.registers[0x19], 6500)
        self.assertEqual(uart.registers[0x18], 8100)
        self.assertEqual(uart.registers[0x1A], 0x8C00)
        self.assertEqual(uart.registers[0x1B], 0xE000)
        self.assertEqual(uart.registers[0x2E], 1)
        self.assertEqual(uart.registers[0], 1)
        self.assertNotIn(0x1E, [a for a, _ in uart.writes])
        codes = [value for address, value in uart.writes if address == 2]
        self.assertTrue(all(abs(b - a) <= 128 for a, b in zip(codes, codes[1:])))
        self.assertGreaterEqual(dwell.count(.020), len(codes) - 1)
        events = [json.loads(line) for line in (folder / "writes.jsonl").read_text().splitlines()]
        self.assertEqual(len([e for e in events if e["kind"] == "write_attempt"]), len(uart.writes))
        self.assertTrue(all(e["matches"] for e in events if e["kind"] == "write_readback"))
        self.assertEqual(len(json.loads((folder / "original_registers.json").read_text())), 64)
        self.assertTrue(uart.closed)

    def test_a623_rejected_without_mutation(self):
        uart = MockUART(firmware=0xA623)
        _, result, _ = self.run_case(uart)
        self.assertFalse(result["success"])
        self.assertEqual(uart.writes, [])
        self.assertIn("A624 required", result["error"])

    def test_insufficient_headroom_rejected_without_mutation(self):
        uart = MockUART()
        _, result, _ = self.run_case(uart, dac_code=1450)
        self.assertFalse(result["success"])
        self.assertEqual(uart.writes, [])
        self.assertIn("headroom", result["error"])

    def test_wrong_sample_rate_or_active_probe_rejected_without_mutation(self):
        for address, value in [(0x11, 3), (0x30, 1), (0x23, 1), (0x36, 1), (0x28, 1)]:
            with self.subTest(address=address):
                uart = MockUART()
                uart.registers[address] = value
                _, result, _ = self.run_case(uart)
                self.assertFalse(result["success"])
                self.assertEqual(uart.writes, [])

    def test_dark_failure_never_restarts_previously_faulted_loop(self):
        uart = MockUART(status=4, dark=True)
        folder, result, _ = self.run_case(uart)
        self.assertFalse(result["success"])
        self.assertEqual(uart.registers[0], 8)
        self.assertEqual(uart.registers[0x2E], 0)
        self.assertNotIn((0, 1), uart.writes)
        recovery = json.loads((folder / "recovery.json").read_text())
        self.assertTrue(recovery["final_fixed_code_in_restored_original_limits"])
        self.assertIn("0x00", recovery["differences_from_original_configuration"])
        self.assertIn("0x2E", recovery["differences_from_original_configuration"])

    def test_restore_old_minimum_only_after_fixed_output_enters_old_range(self):
        uart = MockUART(status=4, minimum=6600, dark=True)
        _, result, _ = self.run_case(uart, dac_code=6400, minimum=6100)
        self.assertFalse(result["success"])
        self.assertEqual(uart.registers[0x1E], 6600)
        self.assertEqual(uart.registers[2], 6600)
        self.assertEqual(uart.registers[5], 6600)
        old_min_index = uart.writes.index((0x1E, 6600))
        self.assertIn((2, 6600), uart.writes[:old_min_index])
        self.assertEqual(uart.registers[0], 8)
        self.assertEqual(uart.registers[0x2E], 0)

    def test_preserves_original_unarmed_permission_and_reports_unarmed(self):
        uart = MockUART(arm=0)
        _, result, _ = self.run_case(uart)
        self.assertTrue(result["success"])
        self.assertFalse(result["closed_loop_active"])
        self.assertEqual(result["outcome"], "configured_unarmed")
        self.assertNotIn((0x2E, 0xA501), uart.writes)

    def test_gain_readback_failure_logged_and_original_healthy_mode_restored(self):
        uart = MockUART(fail_gain_readback=True)
        folder, result, _ = self.run_case(uart)
        self.assertFalse(result["success"])
        self.assertEqual(uart.registers[0x18], uart.original[0x18])
        self.assertEqual(uart.registers[0x19], uart.original[0x19])
        self.assertEqual(uart.registers[0x1A], uart.original[0x1A])
        self.assertEqual(uart.registers[0x1B], uart.original[0x1B])
        self.assertEqual(uart.registers[0], 1)
        self.assertEqual(uart.registers[0x2E], 1)
        events = [json.loads(line) for line in (folder / "writes.jsonl").read_text().splitlines()]
        self.assertTrue(any(e["kind"] == "write_failed" and e["address"] == "0x1A" for e in events))

    def test_default_command_prints_plan_without_driver_or_output(self):
        with mock.patch.object(subject, "run_setup", side_effect=AssertionError("Hardware path invoked")):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = subject.main(["--dac-code", "6500"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["mode"], "plan_only_no_hardware_access")

    def test_without_dark_requires_significant_local_gain_and_returns_to_target(self):
        uart = MockUART()
        folder, result, _ = self.run_case(uart, dark_reference=None)
        self.assertTrue(result["success"])
        gain = json.loads((folder / "local_gain_measurement.json").read_text())
        self.assertTrue(gain["passed"])
        self.assertEqual(len(gain["low"]["samples"]), 128)
        self.assertEqual(len(gain["high"]["samples"]), 128)
        self.assertGreater(gain["ADC_difference"], gain["significance_threshold"])
        self.assertEqual(uart.registers[5], 6500)
        codes = [value for address, value in uart.writes if address == 2]
        self.assertIn(6372, codes)
        self.assertIn(6628, codes)
        self.assertEqual(codes[-1], 6500)
        self.assertFalse(result["RIN_computed"])
        self.assertEqual(result["target_error_tolerance_ADC_codes"], 20)

    def test_without_dark_flat_or_wrong_direction_prevents_pi_enable(self):
        for flat, direction in [(True, 1), (False, 0)]:
            with self.subTest(flat=flat, direction=direction):
                uart = MockUART(status=4, flat=flat)
                uart.registers[0x20] = direction
                _, result, _ = self.run_case(uart, dark_reference=None)
                self.assertFalse(result["success"])
                self.assertNotIn((0, 1), uart.writes)
                self.assertEqual(uart.registers[0], 8)
                self.assertEqual(uart.registers[0x2E], 0)

    def test_latched_otr_is_strict_by_default(self):
        uart = MockUART(latched_otr=True)
        _, result, _ = self.run_case(uart)
        self.assertFalse(result["success"])
        self.assertIn("ADC OTR", result["error"])
        self.assertEqual(result["status_policy"]["effective_fault_mask"], 0x3F)
        self.assertNotIn((0, 1), uart.writes)

    def test_explicit_latched_otr_exception_preserves_raw_status_everywhere(self):
        uart = MockUART(latched_otr=True)
        folder, result, _ = self.run_case(uart, allow_latched_otr=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["original_STATUS"], 2)
        self.assertEqual(result["final_STATUS"], 2)
        self.assertEqual(result["final_STATUS_faults"], ["ADC_OTR"])
        self.assertEqual(result["status_policy"]["effective_fault_mask"], 0x3D)
        self.assertTrue(result["status_policy"]["live_capture_fault_validation_required"])
        rounds = json.loads((folder / "verification_rounds.json").read_text())
        self.assertTrue(all(r["status"] == 2 and r["latched_OTR_ignored"] for r in rounds))
        self.assertTrue(all(r["blocked_faults"] == [] for r in rounds))
        self.assertEqual(json.loads((folder / "final_registers.json").read_text())["0x01"], 2)
        self.assertTrue(json.loads((folder / "requested_configuration.json").read_text())["allow_latched_otr"])

    def test_otr_exception_does_not_ignore_any_other_closed_loop_fault(self):
        for fault in [1, 4, 8, 16, 32]:
            with self.subTest(fault=fault):
                uart = MockUART(latched_otr=True, status_on_enable=fault)
                _, result, _ = self.run_case(uart, allow_latched_otr=True)
                self.assertFalse(result["success"])
                self.assertIn("Workpoint status fault", result["error"])

    def test_explicit_arm_enables_and_verifies_originally_unarmed_loop(self):
        uart = MockUART(arm=0)
        _, result, _ = self.run_case(uart, arm=True)
        self.assertTrue(result["success"])
        self.assertTrue(result["closed_loop_active"])
        self.assertFalse(result["original_ARM_preserved"])
        self.assertEqual(result["original_ARM"], 0)
        self.assertEqual(result["final_ARM"], 1)
        self.assertIn((0x2E, 0xA501), uart.writes)

    def test_explicit_arm_is_not_successful_if_arming_readback_fails(self):
        uart = MockUART(arm=0, refuse_arm=True)
        _, result, _ = self.run_case(uart, arm=True)
        self.assertFalse(result["success"])
        self.assertIn("Register 0x2E", result["error"])
        self.assertEqual(uart.registers[0x2E], 0)
        self.assertEqual(uart.registers[0], 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
