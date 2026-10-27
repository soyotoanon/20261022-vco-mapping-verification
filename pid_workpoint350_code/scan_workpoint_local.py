"""Recenter a nominal DAC workpoint before every single-frequency measurement.

Optional paired mode alternates two workpoints at each frequency using the same
PI gains. No hardware is accessed unless the CLI includes --execute. Historical
results are left intact; all setups, raw captures and invalid points are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
import configure_workpoint as setup

ROOT = Path(r"D:\codex\2026-10-27\1-3-txt-vco-13-khz")
OUTPUT_ROOT = ROOT / "outputs" / "workpoint_350mVpp_20261027"
MAX_HOLD_ERROR = 512
RF_MAPPING_SOURCE = str(OUTPUT_ROOT / "测试依据与原结果保护.md")
RF_INTERCEPT_MVPP = 411.709
RF_SLOPE_MVPP_PER_V = 13.389


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_frequencies(text):
    try:
        values = [float(item.strip()) for item in text.split(",")]
        if not 1 <= len(values) <= 64 or any(not math.isfinite(v) or not v.is_integer() or not 10000 <= v <= 250000 for v in values):
            raise ValueError()
        result = [int(v) for v in values]
        if len(set(result)) != len(result):
            raise ValueError()
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use 1..64 unique integer frequencies from 10000 to 250000 Hz, separated by commas") from exc


def estimate_RF_mVpp(code):
    return RF_INTERCEPT_MVPP + RF_SLOPE_MVPP_PER_V * (float(code) - 8198) * .0006533


def configurations(args):
    if args.amplitude < 1 or args.amplitude > 128:
        raise ValueError("Amplitude must be in 1..128 DAC peak codes")
    if args.read_samples not in (2048, 4096, 8192):
        raise ValueError("Read samples must be 2048, 4096 or 8192")
    points = [(args.dac_code, args.label)]
    if (args.paired_dac_code is None) != (args.paired_label is None):
        raise ValueError("Supply both --paired-dac-code and --paired-label")
    if args.paired_dac_code is not None:
        if args.paired_label == args.label:
            raise ValueError("Paired workpoint labels must differ")
        points.append((args.paired_dac_code, args.paired_label))
    result = []
    for code, label in points:
        cfg = setup.Config(dac_code=code, label=label, kp=args.kp, ki=args.ki,
                           minimum=args.minimum, allow_latched_otr=True, arm=True,
                           port=args.port).validate()
        if code < args.minimum + 2 * args.amplitude:
            raise ValueError("Nominal target lacks twice the injection amplitude above the requested output minimum")
        result.append(cfg)
    return result


def make_plan(args):
    configs = configurations(args)
    return dict(mode="plan_only_no_hardware_access", frequencies_Hz=args.frequencies,
                paired_interleaved=len(configs) == 2,
                workpoints=[dict(label=c.label, nominal_DAC_code=c.dac_code,
                                 historical_mapping_estimated_RF_mVpp=estimate_RF_mVpp(c.dac_code)) for c in configs],
                Kp=args.kp, Ki_per_sample=args.ki, minimum=args.minimum,
                amplitude_DAC_peak_codes=args.amplitude, returned_samples=args.read_samples,
                target_recentered_each_point=True,
                steps=["At each frequency, visit the primary then optional paired workpoint",
                       "Run a fresh bounded setup, measure the local response and ADC target, then enable the requested PI",
                       "Capture exactly one open/closed tone pair using the fast uniform FPGA reader",
                       "Preserve real capture fault checks and reject an open held code farther than 512 codes from the nominal DAC target",
                       "Save parent status/candidate after every point; stop if readback or cleanup cannot be verified"],
                RF_estimates_are_historical_mapping_only=True,
                RF_mapping_source=RF_MAPPING_SOURCE)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_reports(session):
    report, folder = session["report"], session["folder"]
    report["updated_utc"] = utc_now()
    valid = [r for r in report["rows"] if r.get("valid") and r.get("db") is not None]
    report["valid_points"] = len(valid)
    if valid:
        peak = max(valid, key=lambda r: r["db"])
        report["largest_measured_dB"] = peak["db"]
        report["largest_measured_at_Hz"] = peak["frequency_Hz"]
    ten_k = next((r for r in valid if r.get("requested_Hz") == 10000), None)
    report["rejection_10kHz_dB"] = ten_k["db"] if ten_k else None
    setup.save_json(folder / "status.json", report)
    setup.save_json(folder / "candidate.json", dict(report,
                    disposition="retained_local_workpoint_candidate_not_final_acceptance"))


def capture_metadata(measurement_directory):
    captures = {}
    for name in ("open", "closed"):
        files = sorted(Path(measurement_directory).rglob("*_tone_" + name + ".json"))
        if len(files) != 1:
            captures[name] = dict(metadata_available=False,
                                  metadata_files=[str(p) for p in files])
            continue
        meta = load_json(files[0])
        keys = ["probe_control_before", "probe_control_after", "hold_DAC_code_before",
                "hold_DAC_code_after", "actual_DAC_code_before", "actual_DAC_code_after"]
        values = {key: meta.get(key) for key in keys}
        captures[name] = dict(metadata_available=True, metadata_file=str(files[0]),
                              capture_status=meta.get("status"), **values,
                              RF_mVpp_estimates={key: estimate_RF_mVpp(value) for key, value in values.items()
                                                  if "DAC_code" in key and value is not None},
                              RF_estimates_are_historical_mapping_not_live_RF_measurements=True)
    return captures


def validate_workpoint(row, nominal, captures):
    original_valid = bool(row.get("valid"))
    reasons = []
    open_capture = captures.get("open", {})
    held_codes = [open_capture.get("hold_DAC_code_before"), open_capture.get("hold_DAC_code_after")]
    errors = [abs(value - nominal) for value in held_codes if value is not None]
    if len(errors) != 2:
        reasons.append("Open-capture held DAC metadata is incomplete")
    elif max(errors) > MAX_HOLD_ERROR:
        reasons.append("Open held DAC code moved more than 512 codes from the nominal workpoint")
    controls = [open_capture.get("probe_control_before"), open_capture.get("probe_control_after")]
    if any(value is None or not int(value) & 2 for value in controls):
        reasons.append("Open capture was not verified held at both metadata reads")
    for name, capture in captures.items():
        state = capture.get("capture_status")
        if state is None:
            reasons.append(name + " capture status is unavailable")
        elif int(state) & 12:
            reasons.append(name + " capture contains current fault or clipping flags")
    row["valid_before_workpoint_check"] = original_valid
    row["workpoint_validation"] = dict(valid=not reasons,
        maximum_allowed_hold_code_error=MAX_HOLD_ERROR,
        open_hold_errors_DAC_codes=errors, reasons=reasons)
    if reasons:
        row["valid"] = False
        row["reason"] = "; ".join(([row["reason"]] if row.get("reason") else []) + reasons)
    return row


def setup_failure_is_unsafe(result):
    recovery = result.get("recovery", {})
    if result.get("hardware_open_failed") or result.get("hardware_state_unknown"):
        return True
    if recovery.get("hardware_state_readback_available") is False or recovery.get("errors"):
        return True
    if recovery.get("outcome") == "recovery_incomplete_hardware_state_requires_review":
        return True
    # A preflight/setup failure before any mutation requires operator review;
    # retrying the same unavailable or incompatible device at another tone is futile.
    if not result.get("control_register_writes_attempted", False):
        return True
    return readback_failure(result.get("error", ""))


def readback_failure(error):
    text = str(error).lower()
    return any(token in text for token in ("timeouterror", "serialexception", "permissionerror",
        "readback", "read back", "no valid reply", "no read reply", "uart", "incomplete"))


def measure_arguments(fast, args, label, frequency):
    return fast.parser().parse_args(["screen", "--workpoint-label", label,
        "--port", args.port, "--amplitude", str(args.amplitude),
        "--read-samples", str(args.read_samples), "--allow-latched-otr",
        "--target-tolerance-codes", "30", "--max-mean-shift-codes", "20",
        "--frequencies", str(frequency)])


def run_scans(args, *, setup_fn=None, fast_module=None, output_root=OUTPUT_ROOT):
    global RF_INTERCEPT_MVPP, RF_SLOPE_MVPP_PER_V, RF_MAPPING_SOURCE
    profile_path = getattr(args, "mapping_profile", None)
    RF_INTERCEPT_MVPP, RF_SLOPE_MVPP_PER_V = 411.709, 13.389
    RF_MAPPING_SOURCE = str(OUTPUT_ROOT / "测试依据与原结果保护.md")
    if profile_path is not None:
        profile = load_json(profile_path)
        RF_INTERCEPT_MVPP = float(profile["proposed_RF_estimate"]["center_mVpp"])
        RF_SLOPE_MVPP_PER_V = float(profile["proposed_RF_estimate"]["slope_mVpp_per_control_V"])
        if not (math.isfinite(RF_INTERCEPT_MVPP) and math.isfinite(RF_SLOPE_MVPP_PER_V)
                and RF_INTERCEPT_MVPP > 0 and RF_SLOPE_MVPP_PER_V > 0):
            raise ValueError("Invalid physical mapping profile")
        RF_MAPPING_SOURCE = str(Path(profile_path).resolve())
    configs = configurations(args)
    if setup_fn is None:
        setup_fn = setup.run_setup
    if fast_module is None:
        import workpoint_measure_fast as fast_module
    output_root = Path(output_root).resolve()
    output_root.relative_to(ROOT.resolve())
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    sessions = []
    for cfg in configs:
        folder = output_root / ("local_scan_" + stamp + "_" + cfg.label)
        folder.mkdir(exist_ok=False)
        report = dict(created_utc=utc_now(), status="running", mode="local_scan",
            workpoint_label=cfg.label, workpoint_label_is_unverified_metadata=True,
            nominal_DAC_code=cfg.dac_code, target_recentered_each_point=True,
            paired_interleaved=len(configs) == 2,
            frequencies_Hz=args.frequencies, amplitude_DAC_peak_codes=args.amplitude,
            returned_samples=args.read_samples, raw_sample_rate_Hz=5e6,
            tested_gains=dict(kp=(cfg.kp_register & 0x7FFF) / 256,
                              ki=(cfg.ki_register & 0x7FFF) / 32768),
            target_tolerance_ADC_codes=30, max_mean_shift_ADC_codes=20,
            allow_latched_otr=True, current_capture_fault_mask=12,
            maximum_allowed_hold_code_error=MAX_HOLD_ERROR,
            before_snapshot=None, rows=[], point_errors=[],
            RF_estimate=dict(formula_mVpp=f"{RF_INTERCEPT_MVPP}+{RF_SLOPE_MVPP_PER_V}*(DAC_code-8198)*.0006533",
                source=RF_MAPPING_SOURCE, historical_calibration_prediction_only=True, live_RF_measured=False,
                current_mapping_reported_applied_by_user=profile_path is not None),
            definition="20 log10(closed/open fundamental amplitude); in-loop ADC; per-point target recentering",
            original_results_touched=False,
            limitations=["RF values are historical mapping estimates, not present RF measurements",
                         "The 512-code held-output tolerance is checked separately for every tone",
                         "Finite-tone peaks and in-loop rejection do not constitute complete optical-system acceptance",
                         "Old sequential scans remain retained as drift diagnostics"])
        session = dict(config=cfg, folder=folder, report=report)
        sessions.append(session)
        save_reports(session)
    fatal = None
    try:
        for index, frequency in enumerate(args.frequencies):
            for session in sessions:
                cfg, folder, report = session["config"], session["folder"], session["report"]
                short_label = cfg.label[:48] + f"_p{index:02d}_f{frequency}"
                point_setup = setup.Config(dac_code=cfg.dac_code, label=short_label,
                    kp=cfg.kp, ki=cfg.ki, minimum=cfg.minimum,
                    allow_latched_otr=True, arm=True, port=args.port)
                setup_dir = None
                measurement_dir = folder / f"point_{index:02d}_f{frequency:06d}Hz"
                row = dict(frequency_Hz=frequency, requested_Hz=frequency,
                    valid=False, db=None, reason="Point did not complete",
                    nominal_DAC_code=cfg.dac_code, target_recentered_each_point=True,
                    measurement_directory=str(measurement_dir))
                try:
                    setup_dir, configured = setup_fn(point_setup, output_root=folder / "setups")
                    row["setup_directory"] = str(setup_dir)
                    if not configured.get("success"):
                        row["reason"] = "Per-point setup failed: " + configured.get("error", "unknown setup failure")
                        report["point_errors"].append(dict(frequency_Hz=frequency, stage="setup",
                            error=row["reason"], setup_directory=str(setup_dir)))
                        if setup_failure_is_unsafe(configured):
                            fatal = dict(reason="Setup could not establish a verified recoverable state", label=cfg.label,
                                         frequency_Hz=frequency, setup_directory=str(setup_dir))
                    else:
                        row["target_ADC"] = configured["target_ADC"]
                        arguments = measure_arguments(fast_module, args, short_label, frequency)
                        exit_code, returned_dir = fast_module.run_session(arguments, folder=measurement_dir)
                        measurement_dir = Path(returned_dir)
                        measured = load_json(measurement_dir / "status.json")
                        before_file = measurement_dir / "fpga_before.json"
                        if report["before_snapshot"] is None and before_file.exists():
                            report["before_snapshot"] = load_json(before_file)
                            report["before_snapshot_file"] = str(before_file)
                        if measured.get("tested_gains"):
                            report["tested_gains"] = measured["tested_gains"]
                        if len(measured.get("rows", [])) == 1:
                            row.update(measured["rows"][0])
                            row.update(setup_directory=str(setup_dir), target_ADC=configured["target_ADC"],
                                       nominal_DAC_code=cfg.dac_code,
                                       target_recentered_each_point=True, measurement_directory=str(measurement_dir))
                        else:
                            row["reason"] = measured.get("error", "Expected exactly one single-frequency row")
                        row["captures"] = capture_metadata(measurement_dir)
                        validate_workpoint(row, cfg.dac_code, row["captures"])
                        if not row.get("valid") or exit_code:
                            report["point_errors"].append(dict(frequency_Hz=frequency, stage="measurement",
                                error=row.get("reason") or measured.get("error", "Measurement returned nonzero exit"),
                                child_status=measured.get("status"), measurement_directory=str(measurement_dir)))
                        restoration = measured.get("restoration", {})
                        if (measured.get("status") == "failed_cleanup" or measured.get("cleanup_error")
                                or restoration.get("parameters_restored") is False or restoration.get("errors")
                                or readback_failure(measured.get("error", ""))):
                            fatal = dict(reason="Single-tone readback or cleanup failed", label=cfg.label,
                                         frequency_Hz=frequency, measurement_directory=str(measurement_dir))
                        if exit_code:
                            row["valid"] = False
                except BaseException as exc:
                    row["valid"] = False
                    row["reason"] = repr(exc)
                    report["point_errors"].append(dict(frequency_Hz=frequency, stage="orchestrator",
                        error=repr(exc), traceback=traceback.format_exc(),
                        setup_directory=str(setup_dir) if setup_dir else None,
                        measurement_directory=str(measurement_dir)))
                    fatal = dict(reason="Interrupted" if isinstance(exc, KeyboardInterrupt) else "Uncaught setup/measurement failure; hardware state requires review",
                                 error=repr(exc), label=cfg.label, frequency_Hz=frequency)
                report["rows"].append(row)
                report["last_completed_frequency_Hz"] = frequency
                save_reports(session)
                print(json.dumps(dict(session_directory=str(folder), frequency_Hz=frequency,
                                      valid=row.get("valid"), db=row.get("db"), fatal=fatal)), flush=True)
                if fatal:
                    break
            if fatal:
                break
    finally:
        for session in sessions:
            report = session["report"]
            if fatal:
                report["status"] = "stopped_unsafe_state_requires_review"
                report["stop_reason"] = fatal
            elif len(report["rows"]) == len(args.frequencies) and all(r.get("valid") for r in report["rows"]):
                report["status"] = "complete"
            elif any(r.get("valid") for r in report["rows"]):
                report["status"] = "partial_invalid_points"
            else:
                report["status"] = "failed_all_points_invalid"
            report["finished_utc"] = utc_now()
            save_reports(session)
    return (0 if all(s["report"]["status"] == "complete" for s in sessions) else 1 if fatal else 2), [s["folder"] for s in sessions]


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--execute", action="store_true")
    result.add_argument("--dac-code", type=lambda s: int(s, 0), required=True)
    result.add_argument("--kp", type=float, required=True)
    result.add_argument("--ki", type=float, required=True)
    result.add_argument("--label", required=True)
    result.add_argument("--frequencies", type=parse_frequencies, required=True)
    result.add_argument("--minimum", type=lambda s: int(s, 0), default=700)
    result.add_argument("--amplitude", type=int, choices=range(1, 129), default=128)
    result.add_argument("--read-samples", type=int, choices=(2048, 4096, 8192), default=2048)
    result.add_argument("--port", default="COM9")
    result.add_argument("--paired-dac-code", type=lambda s: int(s, 0), default=None)
    result.add_argument("--paired-label", default=None)
    result.add_argument("--mapping-profile", type=Path, default=None,
                        help="Physical mapping design JSON; RF values remain predictions, not live RF readings")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.execute:
        print(json.dumps(make_plan(args), ensure_ascii=False, indent=2))
        return 0
    exit_code, folders = run_scans(args)
    print(json.dumps(dict(exit_code=exit_code, session_directories=[str(p) for p in folders])), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
