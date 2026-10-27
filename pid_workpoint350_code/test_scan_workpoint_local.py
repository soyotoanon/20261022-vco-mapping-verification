"""Offline orchestration checks; no serial or instrument connections are opened."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scan_workpoint_local as subject


class FakeFast:
    def __init__(self, state, *, drift_first=False, cleanup_failure=False):
        self.state = state
        self.drift_first = drift_first
        self.cleanup_failure = cleanup_failure

    @staticmethod
    def parser():
        parser = argparse.ArgumentParser()
        parser.add_argument("mode")
        for option in ("workpoint-label", "port", "amplitude", "read-samples",
                       "target-tolerance-codes", "max-mean-shift-codes", "frequencies"):
            parser.add_argument("--" + option)
        parser.add_argument("--allow-latched-otr", action="store_true")
        return parser

    def run_session(self, args, folder):
        frequency = int(args.frequencies)
        self.state["events"].append(("measurement", self.state["code"], frequency))
        folder = Path(folder)
        folder.mkdir()
        raw = folder / f"f{frequency:06d}Hz"
        raw.mkdir()
        self.state["measurement_count"] += 1
        held = self.state["code"] + (600 if self.drift_first and self.state["measurement_count"] == 1 else 10)
        for name, control in (("open", 3), ("closed", 1)):
            subject.setup.save_json(raw / ("001_tone_" + name + ".json"), dict(
                status=2, hold_DAC_code_before=held, hold_DAC_code_after=held,
                actual_DAC_code_before=held + 1, actual_DAC_code_after=held - 1,
                probe_control_before=control, probe_control_after=control))
        row = dict(frequency_Hz=frequency, requested_Hz=frequency, valid=True, db=-4,
                   reason="", raw_directory=str(raw), open_amplitude_ADC_codes=5,
                   closed_amplitude_ADC_codes=3, amplitude_DAC_codes=128)
        status = dict(status="failed_cleanup" if self.cleanup_failure else "complete",
                      tested_gains=dict(kp=9, ki=.5), rows=[row],
                      restoration=dict(parameters_restored=not self.cleanup_failure,
                                       errors=["cleanup failure"] if self.cleanup_failure else []))
        subject.setup.save_json(folder / "status.json", status)
        subject.setup.save_json(folder / "fpga_before.json", dict(registers={"0x19": self.state["code"]}))
        return (1 if self.cleanup_failure else 0), folder


class LocalScanChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local_scan_mock_", dir=subject.ROOT / "work")
        self.addCleanup(self.temp.cleanup)
        self.state = dict(code=None, events=[], measurement_count=0)
        self.output = Path(self.temp.name)

    def arguments(self, paired=False):
        values = ["--dac-code", "1143", "--kp", "9", "--ki", ".5",
                  "--label", "B350", "--frequencies", "10000,48000"]
        if paired:
            values += ["--paired-dac-code", "6860", "--paired-label", "A400"]
        return subject.parser().parse_args(values)

    def fake_setup(self, config, output_root):
        self.state["code"] = config.dac_code
        self.state["events"].append(("setup", config.dac_code))
        self.assertTrue(config.arm)
        self.assertTrue(config.allow_latched_otr)
        folder = Path(output_root) / config.label
        folder.mkdir(parents=True)
        return folder, dict(success=True, target_ADC=9100 + self.state["measurement_count"])

    def run_scan(self, *, paired=False, drift=False, cleanup=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return subject.run_scans(self.arguments(paired), setup_fn=self.fake_setup,
                fast_module=FakeFast(self.state, drift_first=drift, cleanup_failure=cleanup),
                output_root=self.output)

    def test_default_cli_plan_never_calls_setup_or_measurement(self):
        with mock.patch.object(subject, "run_scans", side_effect=AssertionError("Hardware path invoked")):
            with contextlib.redirect_stdout(io.StringIO()) as text:
                exit_code = subject.main(["--dac-code", "1143", "--kp", "9", "--ki", ".5",
                                         "--label", "B350", "--frequencies", "10000"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(text.getvalue())["mode"], "plan_only_no_hardware_access")

    def test_recenter_before_every_frequency_and_retain_capture_metadata(self):
        exit_code, folders = self.run_scan()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.state["events"], [("setup", 1143), ("measurement", 1143, 10000),
                                               ("setup", 1143), ("measurement", 1143, 48000)])
        report = subject.load_json(folders[0] / "status.json")
        self.assertEqual(report["status"], "complete")
        self.assertTrue(report["target_recentered_each_point"])
        self.assertEqual(report["before_snapshot"]["registers"]["0x19"], 1143)
        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual(report["rows"][0]["captures"]["open"]["hold_DAC_code_before"], 1153)
        self.assertTrue(report["rows"][0]["workpoint_validation"]["valid"])
        self.assertIn("RF_mVpp_estimates", report["rows"][0]["captures"]["closed"])
        self.assertEqual(subject.load_json(folders[0] / "candidate.json")["rows"], report["rows"])

    def test_paired_mode_interleaves_workpoints_inside_each_frequency(self):
        exit_code, folders = self.run_scan(paired=True)
        self.assertEqual(exit_code, 0)
        measurements = [event for event in self.state["events"] if event[0] == "measurement"]
        self.assertEqual(measurements, [("measurement", 1143, 10000), ("measurement", 6860, 10000),
                                        ("measurement", 1143, 48000), ("measurement", 6860, 48000)])
        self.assertEqual(len(folders), 2)
        self.assertTrue(all(subject.load_json(folder / "status.json")["status"] == "complete" for folder in folders))

    def test_large_hold_drift_invalidates_one_point_and_continues_next_frequency(self):
        exit_code, folders = self.run_scan(drift=True)
        self.assertEqual(exit_code, 2)
        report = subject.load_json(folders[0] / "status.json")
        self.assertEqual(report["status"], "partial_invalid_points")
        self.assertFalse(report["rows"][0]["valid"])
        self.assertTrue(report["rows"][1]["valid"])
        self.assertEqual(report["rows"][0]["workpoint_validation"]["open_hold_errors_DAC_codes"], [600, 600])
        self.assertTrue(Path(report["rows"][0]["raw_directory"]).exists())
        self.assertEqual(len(report["point_errors"]), 1)

    def test_cleanup_failure_stops_before_any_other_workpoint_or_frequency(self):
        exit_code, folders = self.run_scan(paired=True, cleanup=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.state["measurement_count"], 1)
        self.assertTrue(all(subject.load_json(folder / "status.json")["status"] == "stopped_unsafe_state_requires_review" for folder in folders))

    def test_setup_readback_failure_stops_and_saves_error_candidate(self):
        def bad_setup(config, output_root):
            folder = Path(output_root)
            folder.mkdir(parents=True)
            return folder, dict(success=False, error="TimeoutError: UART read failed",
                                control_register_writes_attempted=True,
                                recovery=dict(hardware_state_readback_available=False))
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code, folders = subject.run_scans(self.arguments(), setup_fn=bad_setup,
                fast_module=FakeFast(self.state), output_root=self.output)
        self.assertEqual(exit_code, 1)
        report = subject.load_json(folders[0] / "candidate.json")
        self.assertFalse(report["rows"][0]["valid"])
        self.assertEqual(report["point_errors"][0]["stage"], "setup")
        self.assertEqual(self.state["measurement_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
