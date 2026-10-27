"""Optional shorter UART read for the existing >=10 kHz tone measurements.

The FPGA still captures all 8192 uniformly timed samples.  Only the leading
2048/4096/8192 samples are transferred; ADC clock, CIC decimation, clipping and
current-fault validation remain unchanged.  Importing this module opens no device.
"""
from __future__ import annotations

import json
import argparse
import math
import sys
import time

sys.dont_write_bytecode = True

import numpy as np
import workpoint_measure as base

HARDWARE_SAMPLES = 8192
READ_CHOICES = (2048, 4096, 8192)
STRICT_BASE_VALIDATE_SNAPSHOT = base.validate_snapshot


def validate_read_samples(value):
    if value not in READ_CHOICES:
        raise ValueError("Tone reads must contain 2048, 4096 or 8192 samples")
    return int(value)


def parse_frequencies(text):
    try:
        values = [float(item.strip()) for item in text.split(",")]
        if (not 1 <= len(values) <= 64 or
                any(not math.isfinite(value) or value < 10000 or not value.is_integer()
                    for value in values)):
            raise ValueError()
        frequencies = [int(value) for value in values]
        if len(set(frequencies)) != len(frequencies):
            raise ValueError()
        return frequencies
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use 1..64 unique comma-separated integer frequencies in Hz, all >=10000; "
            "the unchanged fixed-clock preflight also checks their upper limit") from exc


class FastLoggedDevice(base.LoggedDevice):
    READ_SAMPLES = 2048

    def __init__(self, port, audit, read_samples=None):
        self.read_samples = validate_read_samples(self.READ_SAMPLES if read_samples is None else read_samples)
        super().__init__(port, audit)

    def capture(self, decim_log, cancel, progress=lambda text: None):
        count = validate_read_samples(self.read_samples)
        if not 0 <= decim_log <= 14:
            raise ValueError("Decimation exponent must be in 0..14")
        probe_control_before = self.read(0x30)
        hold_code_before = self.read(0x3C)
        actual_before = self.read(0x05)
        self.audit.event("tone_capture_plan", hardware_capture_samples=HARDWARE_SAMPLES,
                         returned_samples=count, start_index=0, decim_log=decim_log,
                         probe_control_before=probe_control_before,
                         hold_DAC_code_before=hold_code_before,
                         actual_DAC_code_before=actual_before)
        self.write(0x35, decim_log)
        if self.read(0x35) != decim_log:
            raise RuntimeError("FPGA decimation setting did not read back")
        before = self.read(0x3D)
        self.write(0x34, 1)
        fs = self.raw_fs / 2 ** decim_log
        started = time.monotonic()
        last_progress = -1
        deadline = started + 8198 / fs + 5
        while True:
            if cancel.is_set():
                self.write(0x34, 2)
                raise InterruptedError("Measurement cancelled")
            state = self.read(0x36)
            if state & 2:
                break
            elapsed = int(time.monotonic() - started)
            if elapsed != last_progress:
                last_progress = elapsed
                progress(f"Capturing 8192 hardware samples: {elapsed} s elapsed")
            if time.monotonic() > deadline:
                raise TimeoutError("FPGA timed capture did not complete")
            cancel.wait(.05)
        sequence = self.read(0x3D)
        if sequence != (before + 1) & 65535 or self.read(0x3E) != HARDWARE_SAMPLES:
            raise RuntimeError("Hardware capture sequence or full 8192-sample length is invalid")
        raw = np.empty(count, dtype=np.uint16)
        for index in range(0, count, 64):
            if cancel.is_set():
                raise InterruptedError("Measurement cancelled during UART transfer")
            self.s.write(b"".join(self.frame(1, 0x37, sample_index) + self.frame(2, 0x3B)
                                  for sample_index in range(index, index + 64)))
            reply = bytearray()
            deadline = time.monotonic() + 3
            while len(reply) < 64 * 8:
                reply.extend(self.s.read(64 * 8 - len(reply)))
                if time.monotonic() > deadline:
                    raise TimeoutError("UART capture batch was incomplete")
            for item in range(64):
                packet = reply[item * 8:(item + 1) * 8]
                if packet[:3] != bytes([197, 2, 0x3B]) or packet[5:] != bytes([92, 197, 197]):
                    raise RuntimeError("Capture response frame is misaligned or invalid")
                raw[index + item] = packet[3] | packet[4] << 8
            if index % 512 == 0:
                progress(f"Reading leading samples: {index}/{count}")
        if self.read(0x3D) != sequence:
            raise RuntimeError("FPGA capture was replaced during UART transfer")
        if self.read(0x3E) != HARDWARE_SAMPLES:
            raise RuntimeError("Full hardware capture length changed during UART transfer")
        final_state = self.read(0x36)
        if final_state & 3 != 2:
            raise RuntimeError("FPGA capture is no longer in the completed state")
        # Retain both fault/clip flags sampled at completion and any later latched
        # clip/fault flag.  Measurement.record still rejects status & 12 and saves
        # the raw waveform before rejecting it.  Historical STATUS bit1 permission
        # never masks any of these capture flags.
        state |= final_state & 12
        probe_control_after = self.read(0x30)
        hold_code_after = self.read(0x3C)
        actual_after = self.read(0x05)
        return raw.astype(float) / 4, dict(
            fs=fs, raw_fs=self.raw_fs, decimation=2 ** decim_log,
            sequence=sequence, status=state,
            encoding="unsigned ADC codes with 2 fractional bits",
            hardware_capture_samples=HARDWARE_SAMPLES, returned_samples=count,
            start_index=0, reader="leading_contiguous_samples_no_resampling",
            probe_control=probe_control_before, hold_DAC_code=hold_code_before,
            probe_control_before=probe_control_before, probe_control_after=probe_control_after,
            hold_DAC_code_before=hold_code_before, hold_DAC_code_after=hold_code_after,
            actual_DAC_code_before=actual_before, actual_DAC_code_after=actual_after,
            output_readback_timing="before capture arm and after UART transfer; actual DAC codes are instantaneous")


def fast_validate_snapshot(snapshot, frequencies, amplitude, target_tolerance_codes=30,
                           allow_latched_otr=False):
    """Extend only the tone amplitude ceiling; all original guards still execute."""
    if not isinstance(amplitude, int) or not 1 <= amplitude <= 128:
        raise ValueError("Fast tone amplitude must be 1..128 DAC peak codes")
    raw_fs = STRICT_BASE_VALIDATE_SNAPSHOT(
        snapshot, frequencies, min(amplitude, 64), target_tolerance_codes, allow_latched_otr)
    get = lambda address: base.register(snapshot, address)
    if min(get(0x05) - get(0x1E), get(0x1F) - get(0x05)) < 2 * amplitude:
        raise RuntimeError("Current output has less than twice the full requested disturbance headroom")
    return raw_fs


def run_session(args, *, device_factory=None, **kwargs):
    """Same return/API as base.run_session, with short reads and amplitude<=128.

    Validator and frequency overrides exist only for the duration of this call.
    Source files and any other running Python processes remain untouched.
    """
    read_samples = validate_read_samples(getattr(args, "read_samples", FastLoggedDevice.READ_SAMPLES))
    frequencies = getattr(args, "frequencies", None)
    if frequencies is not None:
        if any(frequency < 10000 for frequency in frequencies):
            raise ValueError("Short reads are limited to >=10 kHz tone measurements")
        if args.mode == "fine" and any(right <= left for left, right in zip(frequencies, frequencies[1:])):
            raise ValueError("Fine-mode frequencies must be strictly increasing")
    factory = device_factory or (lambda port, audit: FastLoggedDevice(port, audit, read_samples))
    original_validator = base.validate_snapshot
    original_frequencies = base.FREQUENCIES[args.mode]
    base.validate_snapshot = fast_validate_snapshot
    if frequencies is not None:
        base.FREQUENCIES[args.mode] = frequencies
    try:
        exit_code, folder = base.run_session(args, device_factory=factory, **kwargs)
    finally:
        base.validate_snapshot = original_validator
        base.FREQUENCIES[args.mode] = original_frequencies
    reader = dict(hardware_capture_samples=HARDWARE_SAMPLES, returned_samples=read_samples,
                  start_index=0, uniform_original_sample_rate=True,
                  no_resampling=True, current_capture_fault_mask_unchanged=12,
                  explicit_frequencies_Hz=frequencies, maximum_tone_DAC_peak_codes=128)
    for filename in ("status.json", "candidate.json"):
        path = folder / filename
        report = json.loads(path.read_text(encoding="utf-8"))
        report["tone_capture_reader"] = reader
        base.save_json(path, report)
    return exit_code, folder


def parser():
    result = base.parser()
    result.description = __doc__
    for action in result._actions:
        if action.dest == "amplitude":
            action.choices = range(1, 129)
            action.metavar = "1..128"
            action.help = "DAC peak codes, not RF mVpp; full 2*amplitude headroom required (default: 32)"
    result.add_argument("--read-samples", type=int, choices=READ_CHOICES, default=FastLoggedDevice.READ_SAMPLES,
                        help="Leading consecutive UART samples; FPGA still captures 8192 (default: 2048)")
    result.add_argument("--frequencies", type=parse_frequencies, default=None,
                        help="Optional tone-frequency override in integer Hz, e.g. 10000,48000,55000; "
                             "all >=10000 and fixed-clock limits still apply")
    return result


def main():
    command_parser = parser()
    args = command_parser.parse_args()
    if args.mode == "fine" and args.frequencies is not None:
        if any(right <= left for left, right in zip(args.frequencies, args.frequencies[1:])):
            command_parser.error("Fine-mode frequencies must be strictly increasing for crossing interpolation")
    # Both inherited modes contain only >=10 kHz tone tests; no noise/RIN path is
    # exposed.  At every allowed decimation their leading 2048 samples contain
    # well above the ten cycles required by the unchanged sine-fit validator.
    exit_code, unused_folder = run_session(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
