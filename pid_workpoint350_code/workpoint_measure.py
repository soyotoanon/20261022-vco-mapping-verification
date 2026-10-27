"""Measure one already configured optical workpoint without changing its target.

Hardware access occurs only from run_session()/main().  This driver never programs
firmware and never writes the target, bias, limits, sampling clock or loop arming.
Outputs are confined to the current experiment's output directory.  The supplied
workpoint label is metadata, not a claim that an RF voltage was calibrated here.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

WORKSPACE = Path(r"D:\codex\2026-10-27\1-3-txt-vco-13-khz")
OUTPUT_ROOT = WORKSPACE / "outputs" / "workpoint_350mVpp_20261027"
ARCHIVE = Path(r"D:\codex\2026-10-23\wo\work\vco_remote\pid_gain_tuning")
sys.path[:0] = [str(ARCHIVE / "host" / "vendor"), str(ARCHIVE / "host" / "performance")]

import serial
from analysis import crossing
from device import Device
from measurement import Measurement

FREQUENCIES = {
    "screen": [10000, 30000, 40000, 48000, 55000, 65000, 75000, 100000],
    "fine": [10000, 11000, 12000, 13000, 14000, 15000, 16000, 18000, 20000, 25000],
}
# The serial proxy enforces this whitelist, including batched capture commands.
ALLOWED_WRITES = frozenset([0x1A, 0x1B, 0x28, 0x30, 0x31, 0x32, 0x33,
                            0x34, 0x35, 0x37])
PROTECTED_REGS = [0x00, 0x02, 0x11, 0x18, 0x19, 0x1C, 0x1D, 0x1E,
                  0x1F, 0x20, 0x21, 0x2C, 0x2D, 0x2E, 0x3F]
RESTORABLE_REGS = [0x1A, 0x1B, 0x28, 0x30, 0x31, 0x32, 0x33, 0x35, 0x37]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_default(value):
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(type(value).__name__)


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    default=json_default, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def within_outputs(path):
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(OUTPUT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Measurement output must remain inside " + str(OUTPUT_ROOT)) from exc
    return resolved


class Audit:
    def __init__(self, folder):
        self.folder = within_outputs(folder)
        self.phase = "preflight"

    def event(self, kind, **values):
        record = dict(utc=utc_now(), phase=self.phase, kind=kind, **values)
        with (self.folder / "writes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=json_default,
                                    allow_nan=False) + "\n")
            handle.flush()


def mutation_frames(data):
    data = bytes(data)
    if len(data) % 8:
        raise RuntimeError("Refusing a partial UART frame")
    mutations = []
    for offset in range(0, len(data), 8):
        frame = data[offset:offset + 8]
        if frame[0] != 197 or frame[5:] != bytes([92, 197, 197]) or frame[1] not in (1, 2):
            raise RuntimeError("Refusing an unrecognized UART command")
        if frame[1] == 1:
            address = frame[2]
            if address not in ALLOWED_WRITES:
                raise RuntimeError(f"Forbidden register write 0x{address:02X}")
            mutations.append(dict(address=f"0x{address:02X}", value=frame[3] | frame[4] << 8))
    return mutations


class AuditedSerial:
    """Log and constrain all writes, including Device.capture's direct batches."""
    def __init__(self, wrapped, audit):
        self.wrapped = wrapped
        self.audit = audit

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def write(self, data):
        mutations = mutation_frames(data)
        if mutations:
            self.audit.event("uart_write_attempt", frames=mutations, payload_hex=bytes(data).hex())
        try:
            count = self.wrapped.write(data)
        except BaseException as exc:
            if mutations:
                self.audit.event("uart_write_error", error=repr(exc))
            raise
        if count != len(data):
            self.audit.event("uart_short_write", expected=len(data), actual=count)
            raise IOError("UART write was incomplete")
        return count


class LoggedDevice(Device):
    def __init__(self, port, audit):
        self.audit = audit
        self.target_tolerance_codes = getattr(audit, "target_tolerance_codes", 30)
        self.allowed_latched_status_bits = getattr(audit, "allowed_latched_status_bits", 0)
        # Base initialization uses self.write(), so its stream write is logged too.
        super().__init__(port)
        self.s = AuditedSerial(self.s, audit)

    def write(self, address, value):
        mutation_frames(self.frame(1, address, value))
        if not isinstance(self.s, AuditedSerial):
            self.audit.event("initialization_write_attempt", address=f"0x{address:02X}", value=value)
        return super().write(address, value)

    def require_closed_loop(self):
        super().require_closed_loop()
        # The inherited mask omits latched OTR and the integral-limit flag.
        # Stop conservatively; clearing historical sticky flags is not owned here.
        status = self.read(0x01)
        if status & (0x3F & ~self.allowed_latched_status_bits):
            raise RuntimeError(f"FPGA fault/limit flags present (possibly historical sticky flags): 0x{status:04X}")
        if abs(self.read(0x03) - self.read(0x18)) > self.target_tolerance_codes:
            raise RuntimeError("ADC no longer matches the existing target within "
                               f"{self.target_tolerance_codes:g} codes")

    def close(self):
        # Device.close() would change restored probe values; explicit cleanup owns them.
        self.s.close()


def low_level_read(port, address):
    port.write(Device.frame(2, address))
    deadline = time.monotonic() + 2
    buffer = bytearray()
    while time.monotonic() < deadline:
        buffer.extend(port.read(max(1, min(port.in_waiting, 256))))
        while len(buffer) >= 8:
            if buffer[0] == 197 and buffer[5:8] == bytes([92, 197, 197]):
                packet = bytes(buffer[:8])
                del buffer[:8]
                if packet[1] == 2 and packet[2] == address:
                    return packet[3] | packet[4] << 8
            else:
                del buffer[0]
    raise TimeoutError(f"No read reply for register 0x{address:02X}")


def pure_snapshot(port_name):
    """No Device construction, no command-1 frames, no output-changing writes."""
    report = dict(utc=utc_now(), port=port_name, register_writes=False,
                  registers={}, complete=False)
    handle = None
    try:
        handle = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=2)
        handle.dtr = False
        handle.rts = False
        handle.port = port_name
        handle.open()
        firmware = low_level_read(handle, 0x3F)
        report["registers"]["0x3f"] = firmware
        report["firmware_hex"] = f"0x{firmware:04X}"
        if firmware != 0xA624:
            raise RuntimeError("This driver requires A624; firmware was not changed")
        for address in range(0x3F):
            report["registers"][hex(address)] = low_level_read(handle, address)
        report["complete"] = True
    except BaseException as exc:
        report["error"] = repr(exc)
    finally:
        if handle is not None and handle.is_open:
            handle.close()
    return report


def register(snapshot, address):
    return int(snapshot["registers"][hex(address)])


def validate_snapshot(snapshot, frequencies, amplitude, target_tolerance_codes=30,
                      allow_latched_otr=False):
    if not snapshot.get("complete"):
        raise RuntimeError("Read-only FPGA snapshot failed: " + snapshot.get("error", "incomplete"))
    get = lambda address: register(snapshot, address)
    if get(0x3F) != 0xA624:
        raise RuntimeError("A624 is required; fixed gain writes to A623 are forbidden")
    if get(0x00) != 1 or get(0x2E) != 1:
        raise RuntimeError("PI must already be armed and enabled in its original program")
    allowed_bits = 0x02 if allow_latched_otr else 0
    if get(0x01) & (0x3F & ~allowed_bits):
        raise RuntimeError(f"FPGA fault or limit status is nonzero: 0x{get(0x01):04X}")
    if get(0x30) != 0 or get(0x23) & 1:
        raise RuntimeError("An existing probe or target disturbance is active")
    if max(abs(get(0x03) - get(0x18)), abs(get(0x06) - get(0x18))) > target_tolerance_codes:
        raise RuntimeError("ADC and current target differ by more than "
                           f"{target_tolerance_codes:g} codes")
    if not 1 <= amplitude <= 64:
        raise ValueError("This small-signal driver allows amplitude 1..64 DAC peak codes")
    if min(get(0x05) - get(0x1E), get(0x1F) - get(0x05)) < 2 * amplitude:
        raise RuntimeError("Current output has less than twice the requested disturbance headroom")
    raw_fs = 50e6 / (2 * (get(0x11) + 1))
    for frequency in frequencies:
        if not 0 < frequency <= raw_fs / 20:
            raise ValueError(f"{frequency:g} Hz exceeds the fixed-clock measurement limit")
        choices = [d for d in range(15) if raw_fs / 2 ** d >= 12 * frequency
                   and 8192 * frequency / (raw_fs / 2 ** d) >= 10]
        if not choices:
            raise ValueError(f"No valid capture rate for {frequency:g} Hz")
    return raw_fs


def encode_gain(value, kind):
    value = float(value)
    scale = 256 if kind == "kp" else 32768
    if not math.isfinite(value) or not 0 <= value <= 32767 / scale:
        raise ValueError(f"{kind} must be finite and within 0..{32767 / scale:g}")
    return 0x8000 | round(value * scale)


def decode_gain(value, kind):
    if value & 0x8000:
        return (value & 32767) / (256 if kind == "kp" else 32768)
    return 0.0 if value >= (15 if kind == "kp" else 23) else 2.0 ** (-value)


def verified_write(dev, audit, address, value):
    dev.write(address, value)
    actual = dev.read(address)
    audit.event("readback", address=f"0x{address:02X}", requested=value, actual=actual)
    if actual != value:
        raise RuntimeError(f"Readback mismatch at 0x{address:02X}: requested {value}, got {actual}")


def validate_mean_shift(row, maximum_codes):
    """Keep the raw fit, but reject a ratio obtained at different optical DCs."""
    validated = dict(row, max_mean_shift_ADC_codes=maximum_codes)
    reasons = []
    try:
        open_mean = float(row["open_mean_ADC_codes"])
        closed_mean = float(row["closed_mean_ADC_codes"])
        if not all(math.isfinite(value) for value in (open_mean, closed_mean)):
            raise ValueError("nonfinite DC mean")
        shift = abs(closed_mean - open_mean)
        validated["mean_shift_ADC_codes"] = shift
        validated["mean_comparison_valid"] = shift <= maximum_codes
        if shift > maximum_codes:
            reasons.append(f"Open/closed DC means differ by {shift:g} ADC codes, exceeding {maximum_codes:g}")
    except (KeyError, TypeError, ValueError) as exc:
        validated["mean_shift_ADC_codes"] = None
        validated["mean_comparison_valid"] = False
        reasons.append("Open/closed DC means unavailable or invalid: " + repr(exc))
    if reasons:
        validated["valid"] = False
        validated["reason"] = "; ".join(filter(None, [row.get("reason", ""), *reasons]))
    return validated


def restore_initialization_stream(dev, audit):
    """Before experiments begin, own only Device.__init__'s stream-disable write."""
    audit.phase = "cleanup_constructor_stream_only"
    result = dict(scope="constructor_stream_only", errors=[], parameters_restored=False,
                  protected_or_probe_configuration_restoration_attempted=False,
                  requested_stream=dev.original_stream)
    try:
        verified_write(dev, audit, 0x28, dev.original_stream)
        result["parameters_restored"] = True
    except BaseException as exc:
        result["errors"].append(dict(address="0x28", error=repr(exc)))
    return result


def restore_state(dev, original, audit, restore_gains):
    """Always stop injection, restore owned settings and verify protected ones.

    If a gain cannot be restored and verified, retain hold instead of releasing
    the loop with unverified gains.  The report explicitly records this failure.
    Integrator history and capture sequence numbers are intentionally not claimed
    to be restored: neither has a writable restoration interface.
    """
    allowed_bits = getattr(audit, "allowed_latched_status_bits", 0)
    result = dict(errors=[], readback={}, gains_restored=True, parameters_restored=False,
                  allowed_latched_status_bits=allowed_bits)
    get = lambda address: register(original, address)
    audit.phase = "cleanup"

    def attempt(address, value, verify=True):
        try:
            if verify:
                verified_write(dev, audit, address, value)
            else:
                dev.write(address, value)
            return True
        except BaseException as exc:
            result["errors"].append(dict(address=hex(address), value=value, error=repr(exc)))
            audit.event("cleanup_error", address=hex(address), error=repr(exc))
            return False

    attempt(0x34, 2, verify=False)  # abort is a command; readback is always zero
    attempt(0x30, 6, verify=False)  # disable injection and hold current PI output
    try:
        if dev.read(0x30) != 2:
            raise RuntimeError("Cleanup could not establish injection-off hold")
    except BaseException as exc:
        result["errors"].append(dict(address="0x30", error=repr(exc)))
    if restore_gains:
        for address in (0x1B, 0x1A):
            if not attempt(address, get(address)):
                result["gains_restored"] = False
    for address in (0x31, 0x32, 0x33, 0x35, 0x37):
        attempt(address, get(address))
    if result["gains_restored"]:
        attempt(0x30, get(0x30))
    else:
        result["hold_retained_due_to_gain_restore_failure"] = True
    attempt(0x28, get(0x28))
    for address in sorted(set(PROTECTED_REGS + RESTORABLE_REGS)):
        try:
            actual = dev.read(address)
            result["readback"][hex(address)] = actual
            if actual != get(address):
                result["errors"].append(dict(address=hex(address), expected=get(address), actual=actual))
        except BaseException as exc:
            result["errors"].append(dict(address=hex(address), error=repr(exc)))
    try:
        result["status_after"] = dev.read(0x01)
        result["live_adc_after"] = dev.read(0x03)
        result["live_output_after"] = dev.read(0x05)
        result["observed_allowed_latched_status_bits"] = result["status_after"] & allowed_bits
        result["healthy_after"] = (result["status_after"] & (0x3F & ~allowed_bits)) == 0
        if not result["healthy_after"]:
            result["errors"].append(dict(error="FPGA fault or limit remains after restoration"))
    except BaseException as exc:
        result["errors"].append(dict(error=repr(exc)))
    result["parameters_restored"] = not result["errors"]
    result["dynamic_state_note"] = "Integrator history, capture RAM, flags and sequence counters are not restored"
    return result


def run_session(args, *, snapshot_fn=pure_snapshot, device_factory=LoggedDevice,
                measurement_factory=Measurement, folder=None):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.workpoint_label).strip("._")
    if not label:
        raise ValueError("A nonempty workpoint label is required")
    frequencies = FREQUENCIES[args.mode]
    for kind in ("kp", "ki"):
        if getattr(args, kind) is not None:
            encode_gain(getattr(args, kind), kind)
    for value, name in ((args.target_tolerance_codes, "target tolerance"),
                        (args.max_mean_shift_codes, "maximum mean shift")):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be positive and finite")
    if folder is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        folder = OUTPUT_ROOT / f"{stamp}_{label}_{args.mode}"
    folder = within_outputs(folder)
    folder.mkdir(parents=True, exist_ok=False)
    audit = Audit(folder)
    audit.target_tolerance_codes = args.target_tolerance_codes
    audit.allowed_latched_status_bits = 0x02 if args.allow_latched_otr else 0
    report = dict(created_utc=utc_now(), status="preflight", mode=args.mode,
                  workpoint_label=args.workpoint_label,
                  workpoint_label_is_unverified_metadata=True,
                  target_bias_limits_clock_firmware_writes=False,
                  amplitude_DAC_peak_codes=args.amplitude,
                  target_tolerance_ADC_codes=args.target_tolerance_codes,
                  max_mean_shift_ADC_codes=args.max_mean_shift_codes,
                  allow_latched_otr=args.allow_latched_otr,
                  allowed_latched_status_bits=audit.allowed_latched_status_bits,
                  current_capture_fault_mask=12,
                  frequencies_Hz=frequencies, requested_kp=args.kp, requested_ki=args.ki,
                  rows=[], point_errors=[], source_archive=str(ARCHIVE),
                  gain_restore_policy="restore original gains on success, failure and interruption",
                  definition="20 log10(closed/open fundamental amplitude); in-loop ADC",
                  limitations=["No RF-workpoint or AC input-voltage calibration is performed here",
                               "No independent out-of-loop optical measurement",
                               "Peak denotes largest measured point, not a continuous-frequency guarantee",
                               "No RIN comparison or dark-level assumption in this driver"])
    dev = None
    original = None
    gains_touched = False
    experiment_started = False
    exit_code = 1
    print(json.dumps(dict(session_directory=str(folder), status="preflight")), flush=True)
    save_json(folder / "status.json", report)
    try:
        original = snapshot_fn(args.port)
        original["status_interpretation"] = dict(
            allow_latched_otr=args.allow_latched_otr,
            allowed_latched_status_bits=audit.allowed_latched_status_bits,
            observed_allowed_latched_status_bits=(
                original.get("registers", {}).get("0x1", 0) & audit.allowed_latched_status_bits),
            current_capture_faults_remain_rejected=True,
            capture_fault_validation="Unchanged Measurement.record rejects capture status & 12")
        save_json(folder / "fpga_before.json", original)
        report["raw_sample_rate_Hz"] = validate_snapshot(
            original, frequencies, args.amplitude, args.target_tolerance_codes,
            args.allow_latched_otr)
        save_json(folder / "original_gains.json", dict(
            registers={hex(address): register(original, address) for address in (0x1A, 0x1B)},
            kp=decode_gain(register(original, 0x1A), "kp"),
            ki_per_sample=decode_gain(register(original, 0x1B), "ki"),
            raw_sample_rate_Hz=report["raw_sample_rate_Hz"]))
        audit.event("preflight_passed", firmware="A624", register_writes=False)
        report["driver_register_writes_started"] = True
        dev = device_factory(args.port, audit)
        # A different program may have run between the two serial opens.  Never
        # overwrite its gain/target changes using the earlier snapshot.
        for address in PROTECTED_REGS + [0x1A, 0x1B, 0x30, 0x31, 0x32, 0x33, 0x35, 0x37]:
            actual = dev.read(address)
            if actual != register(original, address):
                raise RuntimeError(f"Configuration changed between preflight and measurement at {hex(address)}")
        if dev.original_stream != register(original, 0x28):
            raise RuntimeError("Stream configuration changed between preflight and measurement")
        dev.require_closed_loop()
        audit.phase = "gain_setup"
        for address, kind in ((0x1B, "ki"), (0x1A, "kp")):
            requested = getattr(args, kind)
            if requested is not None:
                experiment_started = True
                gains_touched = True  # restore even if the following write times out
                verified_write(dev, audit, address, encode_gain(requested, kind))
        time.sleep(.3)
        dev.require_closed_loop()
        actual_gains = {kind: decode_gain(dev.read(address), kind)
                        for kind, address in (("kp", 0x1A), ("ki", 0x1B))}
        report["tested_gains"] = actual_gains
        tested_registers = {hex(address): dev.read(address)
                            for address in sorted(set(PROTECTED_REGS + RESTORABLE_REGS))}
        report["tested_registers"] = tested_registers
        # Measurement stores dev.snapshot in each raw session.  Refresh it after
        # optional gain changes so raw files are labelled with the tested gains.
        dev.snapshot.update(tested_registers)
        report["status"] = "running"
        save_json(folder / "status.json", report)
        cancel = threading.Event()
        for frequency in frequencies:
            audit.phase = f"measure_{frequency}Hz"
            point_folder = folder / f"f{frequency:06d}Hz"
            point_folder.mkdir(exist_ok=False)

            def notify(kind, payload):
                # The source fit does not apply our DC-matching requirement.
                # Publish the row only after that additional validation below.
                return None

            measurement = measurement_factory(dev, point_folder, cancel, notify,
                                              lambda *unused: False)
            try:
                experiment_started = True
                rows, unused_bandwidth = measurement.sweep(frequency, frequency, args.amplitude, points=1)
                if len(rows) != 1:
                    raise RuntimeError("A single-frequency measurement did not return exactly one result")
                row = validate_mean_shift(dict(rows[0], raw_directory=str(point_folder)),
                                          args.max_mean_shift_codes)
                save_json(point_folder / "validated_result.json", row)
                report["rows"].append(row)
                print(json.dumps(dict(frequency_Hz=frequency, row=row), default=json_default,
                                 allow_nan=False), flush=True)
            except BaseException as exc:
                report["point_errors"].append(dict(frequency_Hz=frequency, error=repr(exc),
                                                    raw_directory=str(point_folder)))
                save_json(folder / "status.json", report)
                raise
            save_json(folder / "status.json", report)
        valid_rows = [row for row in report["rows"] if row.get("valid") and row.get("db") is not None]
        report["valid_points"] = len(valid_rows)
        if valid_rows:
            peak = max(valid_rows, key=lambda row: row["db"])
            report["largest_measured_dB"] = peak["db"]
            report["largest_measured_at_Hz"] = peak["frequency_Hz"]
        ten_k = next((row for row in report["rows"] if row["requested_Hz"] == 10000), None)
        report["rejection_10kHz_dB"] = ten_k["db"] if ten_k and ten_k.get("valid") else None
        if args.mode == "fine":
            report["minus3dB_rejection_crossing_Hz"] = crossing(
                [row["frequency_Hz"] for row in report["rows"]],
                [row["db"] if row.get("valid") else float("nan") for row in report["rows"]])
        report["status"] = ("complete" if len(valid_rows) == len(frequencies) else
                            "partial_invalid_points" if valid_rows else "failed_all_points_invalid")
        exit_code = 0 if report["status"] == "complete" else 2
    except BaseException as exc:
        report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        exit_code = 1
    finally:
        # Retain every candidate, including a failed or partially measured one.
        candidate = {key: value for key, value in report.items() if key != "traceback"}
        candidate["disposition"] = "retained_measurement_candidate_not_final_acceptance"
        candidate["measurement_ended_utc"] = utc_now()
        save_json(folder / "candidate.json", candidate)
        if dev is not None:
            try:
                report["restoration"] = (restore_state(dev, original, audit, gains_touched)
                    if experiment_started else restore_initialization_stream(dev, audit))
                if not report["restoration"]["parameters_restored"]:
                    report["measurement_status_before_cleanup_failure"] = report["status"]
                    report["status"] = "failed_cleanup"
                    exit_code = 1
            except BaseException as exc:
                report["status"] = "failed_cleanup"
                report["cleanup_error"] = repr(exc)
                exit_code = 1
            finally:
                # Never call inherited Device.close(): it would overwrite settings
                # that restore_state has just restored and verified.
                try:
                    dev.s.close()
                except BaseException as exc:
                    report["serial_close_error"] = repr(exc)
                    report["status"] = "failed_cleanup"
                    exit_code = 1
        else:
            report.setdefault("driver_register_writes_started", False)
        report["finished_utc"] = utc_now()
        save_json(folder / "status.json", report)
        print(json.dumps(dict(session_directory=str(folder), status=report["status"],
                              exit_code=exit_code), default=json_default), flush=True)
    return exit_code, folder


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=sorted(FREQUENCIES))
    result.add_argument("--workpoint-label", required=True,
                        help="Metadata only; configure and verify the RF workpoint separately")
    result.add_argument("--port", default="COM9")
    result.add_argument("--amplitude", type=int, choices=range(1, 65), default=32,
                        metavar="1..64", help="DAC peak codes, not RF mVpp (default: 32)")
    result.add_argument("--kp", type=float, default=None)
    result.add_argument("--ki", type=float, default=None, help="Discrete gain per effective ADC sample")
    result.add_argument("--target-tolerance-codes", type=float, default=30,
                        help="Maximum live ADC/target offset (default: 30 ADC codes)")
    result.add_argument("--max-mean-shift-codes", type=float, default=20,
                        help="Reject open/closed pairs with greater DC shift (default: 20 ADC codes)")
    result.add_argument("--allow-latched-otr", action="store_true",
                        help="Explicitly permit STATUS bit1's historical OTR indication only; "
                             "current OTR/fault/limit in capture status remains rejected")
    return result


def main():
    arguments = parser().parse_args()
    exit_code, unused_directory = run_session(arguments)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
