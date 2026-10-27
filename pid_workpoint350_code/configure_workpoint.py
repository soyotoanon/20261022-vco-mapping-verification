"""Prepare an A624 optical workpoint; default invocation prints a plan only.

This file never loads FPGA firmware, writes Flash, or edits old experiment files.
The RF label is metadata: the caller must supply the calibrated DAC code.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import traceback

sys.dont_write_bytecode = True
WORKSPACE = Path(r"D:\codex\2026-10-27\1-3-txt-vco-13-khz")
OUTPUT_ROOT = WORKSPACE / "outputs" / "workpoint_350mVpp_20261027"
VENDOR = Path(r"D:\codex\2026-10-23\wo\outputs\pid_gain_tuning\host\vendor")
HK = timezone(timedelta(hours=8))
PHYSICAL_MIN = max(0, math.ceil(8198 - 5 / 0.0006533))
PHYSICAL_MAX = min(16383, math.floor(8198 + 5 / 0.0006533))
FIXED_CTRL = 8
FAULT_MASK = 0x3F
STATUS_NAMES = {0: "light_loss", 1: "ADC_OTR", 2: "output_limit",
                3: "integrator_limit", 4: "frozen", 5: "ADC_clock_missing"}
ALLOWED_WRITES = {0x00, 0x02, 0x18, 0x19, 0x1A, 0x1B, 0x1E,
                  0x23, 0x2E, 0x30, 0x31, 0x32, 0x33}


def now():
    return datetime.now(HK).isoformat()


def save_json(path, data):
    # Only this new setup directory is writable; old data is never opened for write.
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def status_faults(value):
    return [name for bit, name in STATUS_NAMES.items() if value & (1 << bit)]


def volts(code):
    return (code - 8198) * 0.0006533


@dataclass(frozen=True)
class Config:
    dac_code: int
    dark_reference: float | None = None
    label: str = "350mVpp"
    kp: float = 12.0
    ki: float = 0.75
    minimum: int | None = None
    min_signal_codes: float = 50.0
    port: str = "COM9"
    allow_latched_otr: bool = False
    arm: bool = False

    def validate(self):
        if not isinstance(self.dac_code, int) or not PHYSICAL_MIN <= self.dac_code <= PHYSICAL_MAX:
            raise ValueError(f"DAC code must be in the confirmed +/-5 V range {PHYSICAL_MIN}..{PHYSICAL_MAX}")
        if self.dark_reference is not None and (not math.isfinite(self.dark_reference) or not 0 <= self.dark_reference <= 16383):
            raise ValueError("--dark-reference must be a finite current-session measured ADC value")
        if not math.isfinite(self.min_signal_codes) or self.min_signal_codes < 50:
            raise ValueError("--min-signal-codes must be at least 50")
        if not math.isfinite(self.kp) or not 0 <= self.kp <= 32767 / 256:
            raise ValueError("Kp is outside unsigned A624 Q8 gain range")
        if not math.isfinite(self.ki) or not 0 <= self.ki <= 32767 / 32768:
            raise ValueError("Ki is outside unsigned A624 Q15 gain range")
        if self.minimum is not None and not PHYSICAL_MIN <= self.minimum <= PHYSICAL_MAX:
            raise ValueError("Explicit minimum is outside the confirmed +/-5 V range")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", self.label):
            raise ValueError("Label must contain only 1..80 ASCII letters, digits, dot, dash or underscore")
        return self

    @property
    def kp_register(self):
        return 0x8000 | round(self.kp * 256)

    @property
    def ki_register(self):
        return 0x8000 | round(self.ki * 32768)

    @property
    def fault_mask(self):
        # This exception concerns the reset-only STATUS latch, not the current
        # physical OTR input. Uniform capture fault checks remain mandatory.
        return FAULT_MASK & (~2 if self.allow_latched_otr else 0xFFFF)

    @property
    def status_policy(self):
        return dict(allow_latched_otr=self.allow_latched_otr,
                    effective_fault_mask=self.fault_mask,
                    ignored_STATUS_bits=[1] if self.allow_latched_otr else [],
                    raw_STATUS_is_preserved=True,
                    latched_otr_allowance_is_not_live_OTR_validation=True,
                    live_capture_fault_validation_required=True,
                    explicit_arm_requested=self.arm)


def make_plan(config):
    config.validate()
    return dict(
        mode="plan_only_no_hardware_access", configuration=asdict(config),
        target_DAC_port_volts=volts(config.dac_code),
        RF_label_is_metadata_not_a_voltage_measurement=True,
        status_policy=config.status_policy,
        physical_DAC_bounds=[PHYSICAL_MIN, PHYSICAL_MAX],
        fixed_gain_registers={"0x1A": config.kp_register, "0x1B": config.ki_register},
        requires="A624, 5 MHz, no active probe/disturbance/capture/stream; either measured dark reference or an on-site local slope check",
        steps=["Read and save all 64 registers before any mutation",
               "Validate target bounds and at least 128 DAC codes of headroom on both sides",
               "Hold current actual 0x05 output using DAC_CODE then CTRL=8",
               "Ramp at most 128 codes per step with at least 20 ms between changes",
               "If dark reference is absent, measure 128 samples at target +/-128 codes and verify significant slope with the original DIR_POL",
               "Return to target, read 32 ADC3 samples and reject OTR/rail conditions; if dark is supplied also reject insufficient optical signal",
               "Set bias and measured mean target; encode Kp Q8 and Ki Q15",
               "Pulse set_valid in fixed mode; preserve original ARM unless --arm explicitly requests ARM=1; set CTRL=1",
               "Check status, output bounds, ADC signal and target error in five rounds",
               "Leave the new workpoint only on success; save final state and all write/readback records"],
        failure_strategy="Restore original static configuration. Restart the original mode only when its pre-test status was healthy and mode/ARM were supported. Otherwise hold a code bounded by the restored original limits with CTRL=8 and ARM=0; record all intentional differences. If UART recovery fails, report state as unknown.",
        dynamic_integral_history_restorable=False,
    )


class SerialUART:
    """Minimal existing C5 UART protocol; no side-effecting driver initializer."""
    def __init__(self, port):
        sys.path.insert(0, str(VENDOR))
        import serial  # imported only for an explicit execution
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=2)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    @staticmethod
    def frame(command, address, value=0):
        return bytes([197, command, address, value & 255, value >> 8, 92, 197, 197])

    def send(self, payload):
        if self.serial.write(payload) != len(payload):
            raise IOError("Incomplete UART command write")
        self.serial.flush()

    def read(self, address):
        self.send(self.frame(2, address))
        deadline = time.monotonic() + 2
        packet = bytearray()
        while time.monotonic() < deadline:
            packet.extend(self.serial.read(max(1, min(self.serial.in_waiting, 512))))
            while len(packet) >= 8:
                if packet[0] == 197 and packet[5:8] == bytes([92, 197, 197]):
                    reply = bytes(packet[:8])
                    del packet[:8]
                    if reply[1] == 2 and reply[2] == address:
                        return reply[3] | reply[4] << 8
                else:
                    del packet[0]
        raise TimeoutError(f"No valid reply for register 0x{address:02X}")

    def write(self, address, value):
        self.send(self.frame(1, address, value))

    def close(self):
        self.serial.close()


class Setup:
    def __init__(self, config, uart, folder, sleeper=time.sleep):
        self.config = config.validate()
        self.uart = uart
        self.folder = Path(folder).resolve()
        self.folder.relative_to(WORKSPACE.resolve())
        self.sleep = sleeper
        self.original = None
        self.phase = "snapshot"
        self.mutated = False
        self.current_values = {}
        self.last_fixed = None

    def event(self, kind, **fields):
        with (self.folder / "writes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(time=now(), phase=self.phase, kind=kind, **fields),
                                    ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def snapshot(self, name):
        result = {}
        try:
            for address in range(64):
                result[address] = self.uart.read(address)
        except BaseException:
            save_json(self.folder / (name + "_partial.json"),
                      {f"0x{address:02X}": value for address, value in result.items()})
            raise
        save_json(self.folder / (name + ".json"),
                  {f"0x{address:02X}": value for address, value in result.items()})
        return result

    def write(self, address, value, expected=None):
        if address not in ALLOWED_WRITES or not 0 <= value <= 65535:
            raise ValueError("Register mutation is outside the setup whitelist")
        if expected is None:
            expected = 1 if address == 0x2E and value == 0xA501 else 0 if address == 0x2E else value
        self.event("write_attempt", address=f"0x{address:02X}", value=value, expected=expected)
        # A serial exception may occur after the FPGA received the command.
        self.mutated = True
        try:
            self.uart.write(address, value)
            actual = self.uart.read(address)
            self.event("write_readback", address=f"0x{address:02X}", value=value,
                       expected=expected, actual=actual, matches=actual == expected)
            if actual != expected:
                raise RuntimeError(f"Register 0x{address:02X}: expected {expected}, read {actual}")
            self.current_values[address] = actual
            if address == 0x02:
                self.last_fixed = actual
        except BaseException as exc:
            self.event("write_failed", address=f"0x{address:02X}", value=value, error=repr(exc))
            raise

    def preflight(self):
        r, c = self.original, self.config
        if r[0x3F] != 0xA624:
            raise RuntimeError(f"A624 required; found 0x{r[0x3F]:04X}. No control registers changed.")
        if r[0x11] != 4:
            raise RuntimeError("ADC_HALF must be 4 (5 MHz); this setup never changes sample rate")
        if r[0x30] != 0 or r[0x23] & 1 or r[0x36] & 1 or r[0x28] & 1:
            raise RuntimeError("Existing probe, target disturbance, capture or stream must be stopped first")
        if r[0] & 15 not in (1, 8) or r[0] & (16 | 256 | 512 | 1024):
            raise RuntimeError("Original CTRL is not a stable PI/fixed mode; refusing to guess its recovery")
        if r[0x2E] not in (0, 1):
            raise RuntimeError("Unrecognized ARM state")
        self.new_min = r[0x1E] if c.minimum is None else c.minimum
        self.new_max = r[0x1F]
        if not PHYSICAL_MIN <= self.new_min < self.new_max <= PHYSICAL_MAX:
            raise RuntimeError("New output limits must remain entirely inside confirmed +/-5 V bounds")
        if not self.new_min + 128 <= c.dac_code <= self.new_max - 128:
            raise RuntimeError("Target lacks 2*64 DAC codes of headroom; no control registers changed")
        if not r[0x1E] <= r[5] <= r[0x1F] or not PHYSICAL_MIN <= r[5] <= PHYSICAL_MAX:
            raise RuntimeError("Initial actual output is outside its original or confirmed physical bounds")
        if r[0x1E] > r[0x1F]:
            raise RuntimeError("Original output limits are reversed")
        self.ramp_min = max(PHYSICAL_MIN, min(r[0x1E], self.new_min))
        self.ramp_max = min(PHYSICAL_MAX, r[0x1F])
        self.desired_arm = 1 if c.arm else r[0x2E]
        self.event("preflight_passed", original_status=r[1], original_faults=status_faults(r[1]),
                   new_min=self.new_min, new_max=self.new_max,
                   target_code=c.dac_code, original_arm=r[0x2E], desired_arm=self.desired_arm,
                   status_policy=c.status_policy)

    def ramp(self, start, target, low, high):
        if not low <= start <= high or not low <= target <= high:
            raise RuntimeError("Ramp endpoint is outside permitted code bounds")
        current = start
        while current != target:
            # Readback/file fsync overhead is additional to this minimum dwell.
            self.sleep(.020)
            current += max(-128, min(128, target - current))
            self.write(0x02, current)
            actual = self.uart.read(0x05)
            self.event("fixed_output_check", expected=current, actual=actual)
            if actual != current:
                raise RuntimeError("Actual fixed output did not follow its verified DAC_CODE")

    def read_adc_samples(self, count):
        samples = []
        for _ in range(count):
            samples.append(self.uart.read(0x03))
            self.sleep(.003)
        return dict(samples=samples, mean=statistics.mean(samples),
                    std=statistics.pstdev(samples), minimum=min(samples), maximum=max(samples))

    def check_signal(self, reading):
        if reading["minimum"] <= 0 or reading["maximum"] >= 16383:
            raise RuntimeError("ADC samples reach a rail")
        if self.config.dark_reference is not None and reading["mean"] <= self.config.dark_reference + self.config.min_signal_codes:
            raise RuntimeError("PD signal is too close to the supplied measured dark reference")
        status = self.uart.read(1)
        self.event("signal_STATUS_check", raw_STATUS=status,
                   raw_faults=status_faults(status), status_policy=self.config.status_policy)
        if status & (2 | 32) & self.config.fault_mask:
            raise RuntimeError("ADC OTR or ADC clock fault during optical signal validation")

    def measure_local_gain_if_needed(self):
        if self.config.dark_reference is not None:
            return None
        target = self.config.dac_code
        self.phase = "verify_local_optical_response"
        self.ramp(target, target - 128, self.new_min, self.new_max)
        self.sleep(.100)
        low = self.read_adc_samples(128)
        self.check_signal(low)
        self.ramp(target - 128, target + 128, self.new_min, self.new_max)
        self.sleep(.100)
        high = self.read_adc_samples(128)
        self.check_signal(high)
        delta = high["mean"] - low["mean"]
        noise_se = math.sqrt(low["std"] ** 2 / len(low["samples"]) +
                             high["std"] ** 2 / len(high["samples"]))
        threshold = max(1.0, 5 * noise_se)
        direction = 1 if self.original[0x20] & 1 else -1
        passed = direction * delta > threshold
        result = dict(low_DAC_code=target - 128, high_DAC_code=target + 128,
                      low=low, high=high, ADC_difference=delta,
                      combined_standard_error=noise_se, significance_threshold=threshold,
                      DIR_POL=self.original[0x20], passed=passed,
                      ADC_codes_per_DAC_code=delta / 256,
                      dark_reference_measured=False, RIN_computed=False)
        save_json(self.folder / "local_gain_measurement.json", result)
        self.event("local_gain_check", ADC_difference=delta, threshold=threshold,
                   direction=direction, passed=passed)
        self.ramp(target + 128, target, self.new_min, self.new_max)
        if not passed:
            raise RuntimeError("Local optical response is insignificant or inconsistent with original DIR_POL")
        return result

    def configure(self):
        r, c = self.original, self.config
        self.phase = "hold_and_ramp"
        actual = self.uart.read(0x05)
        if not self.ramp_min <= actual <= self.ramp_max:
            raise RuntimeError("Output moved outside permitted bounds after snapshot")
        self.write(0x02, actual)
        self.write(0x00, FIXED_CTRL)
        self.write(0x30, 4, expected=0)
        self.write(0x31, 0)
        self.write(0x23, 0)
        # Clear pre-existing monitor flags only after output is held. Integral
        # history is deliberately reset later by set_valid and is not recoverable.
        self.write(0x00, FIXED_CTRL | 512)
        self.write(0x00, FIXED_CTRL)
        self.ramp(actual, c.dac_code, self.ramp_min, self.ramp_max)
        if c.minimum is not None:
            self.write(0x1E, self.new_min)

        local_gain = self.measure_local_gain_if_needed()
        self.phase = "measure_workpoint"
        self.sleep(.100)
        reading = self.read_adc_samples(32)
        save_json(self.folder / "workpoint_adc.json", dict(
            dark_reference=c.dark_reference, min_signal_codes=c.min_signal_codes,
            target_DAC_code=c.dac_code, target_DAC_port_volts=volts(c.dac_code),
            RF_label=c.label, RF_voltage_measured_here=False, **reading))
        self.check_signal(reading)
        target = round(reading["mean"])

        self.phase = "apply_pi"
        self.write(0x19, c.dac_code)
        self.write(0x18, target)
        self.write(0x1A, c.kp_register)
        self.write(0x1B, c.ki_register)
        self.write(0x00, FIXED_CTRL | 16)
        self.write(0x00, FIXED_CTRL)
        self.write(0x2E, 0xA501 if self.desired_arm else 0)
        self.write(0x00, 1)

        self.phase = "verify_new_workpoint"
        tolerance = 20.0 if c.dark_reference is None else max(20.0, (reading["mean"] - c.dark_reference) * .05)
        rounds = []
        for index in range(5):
            self.sleep(.100)
            status = self.uart.read(1)
            measured = self.read_adc_samples(8)
            output = self.uart.read(5)
            record = dict(round=index + 1, status=status, faults=status_faults(status),
                          blocked_faults=status_faults(status & c.fault_mask),
                          latched_OTR_ignored=bool(c.allow_latched_otr and status & 2),
                          output_code=output, target=target, tolerance=tolerance,
                          target_error=measured["mean"] - target, **measured)
            rounds.append(record)
            save_json(self.folder / "verification_rounds.json", rounds)
            if status & c.fault_mask:
                raise RuntimeError("Workpoint status fault: " + ", ".join(status_faults(status & c.fault_mask)))
            self.check_signal(measured)
            if abs(record["target_error"]) > tolerance:
                raise RuntimeError("Measured ADC mean is outside workpoint target tolerance")
            if not self.new_min <= output <= self.new_max:
                raise RuntimeError("Closed-loop output is outside configured limits")
            if self.uart.read(0x2E) != self.desired_arm or self.uart.read(0) != 1:
                raise RuntimeError("CTRL or expected ARM permission changed unexpectedly")
        final = self.snapshot("final_registers")
        required = {0: 1, 0x11: r[0x11], 0x18: target, 0x19: c.dac_code, 0x1A: c.kp_register,
                    0x1B: c.ki_register, 0x1E: self.new_min, 0x1F: self.new_max,
                    0x2E: self.desired_arm, 0x30: 0, 0x31: 0, 0x23: 0, 0x3F: 0xA624}
        if any(final[address] != value for address, value in required.items()):
            raise RuntimeError("Final configuration snapshot does not match the intended workpoint")
        if final[1] & c.fault_mask:
            raise RuntimeError("Fault found in final status snapshot")
        return dict(outcome="configured_closed_loop" if self.desired_arm else "configured_unarmed",
                    closed_loop_active=bool(self.desired_arm), target_ADC=target,
                    measured_workpoint=reading, original_ARM_preserved=self.desired_arm == r[0x2E],
                    original_ARM=r[0x2E], final_ARM=final[0x2E], final_STATUS=final[1],
                    final_STATUS_faults=status_faults(final[1]), status_policy=c.status_policy,
                    local_gain_measurement=local_gain, target_error_tolerance_ADC_codes=tolerance,
                    dark_reference_measured=c.dark_reference is not None,
                    RIN_computed=False,
                    final_registers_file="final_registers.json",
                    dynamic_integral_history_restored=False)

    def recover(self):
        self.phase = "error_recovery"
        original = self.original
        errors = []
        report = dict(original_status=original[1], original_faults=status_faults(original[1]),
                      original_CTRL=original[0], original_ARM=original[0x2E],
                      status_policy=self.config.status_policy,
                      dynamic_integral_history_restored=False, errors=errors)
        try:
            # Before lowering old limits, return the physical fixed code into them.
            current = self.uart.read(5)
            live_min = self.uart.read(0x1E)
            live_max = self.uart.read(0x1F)
            safe_low = max(PHYSICAL_MIN, original[0x1E])
            safe_high = min(PHYSICAL_MAX, original[0x1F])
            if safe_low > safe_high:
                raise RuntimeError("No safe original recovery output interval")
            permitted_low = max(PHYSICAL_MIN, min(live_min, original[0x1E]))
            permitted_high = min(PHYSICAL_MAX, max(live_max, original[0x1F]))
            if not permitted_low <= current <= permitted_high:
                raise RuntimeError("Recovery actual output is outside confirmed bounds")
            self.write(0x02, current)
            self.write(0, FIXED_CTRL)
            self.write(0x30, 4, expected=0)
            self.write(0x31, 0)
            self.write(0x23, 0)
            bounded = min(safe_high, max(safe_low, current))
            self.ramp(current, bounded, permitted_low, permitted_high)
            for address in [0x1E, 0x18, 0x19, 0x1A, 0x1B, 0x31, 0x32, 0x33, 0x23]:
                if self.uart.read(address) != original[address]:
                    self.write(address, original[address])
            self.write(0x30, original[0x30])
            healthy = not original[1] & self.config.fault_mask
            original_mode = original[0] & 15
            can_resume = healthy and (original_mode == 8 or (original_mode == 1 and original[0x2E] == 1))
            if can_resume:
                if original_mode == 8:
                    original_fixed = original[0x02]
                    if not safe_low <= original_fixed <= safe_high:
                        raise RuntimeError("Original fixed code is not inside safe restored limits")
                    self.ramp(bounded, original_fixed, safe_low, safe_high)
                else:
                    self.write(0, FIXED_CTRL | 16)
                    self.write(0, FIXED_CTRL)
                self.write(0x2E, 0xA501 if original[0x2E] else 0)
                self.write(0, original[0])
                # In a resumed PI mode, restoring this inactive debug code cannot
                # create the abrupt output jump it would cause in fixed mode.
                self.write(0x02, original[0x02])
                self.sleep(.100)
                resumed_status = self.uart.read(1)
                if resumed_status & self.config.fault_mask:
                    raise RuntimeError("Original mode became unhealthy after recovery")
                report["outcome"] = "original_static_configuration_and_mode_restored"
            else:
                self.write(0x2E, 0)
                self.write(0, FIXED_CTRL)
                report["outcome"] = "original_static_configuration_restored_fixed_output_disarmed"
                report["held_DAC_code"] = bounded
                report["reason"] = "Original status or original ARM/mode did not permit automatic loop restart"
        except BaseException as exc:
            errors.append(dict(stage="restore", error=repr(exc)))
            # A failed readback can mean the previous write was received. Make
            # independent best-effort hold/disarm attempts, and never claim success
            # if the physical state cannot be checked over UART.
            emergency_code = self.last_fixed
            if emergency_code is not None:
                low = max(PHYSICAL_MIN, original[0x1E])
                high = min(PHYSICAL_MAX, original[0x1F])
                emergency_code = min(high, max(low, emergency_code))
            for address, value in [(0x02, emergency_code), (0, FIXED_CTRL), (0x2E, 0), (0x30, 0), (0x23, 0)]:
                if value is None:
                    continue
                try:
                    self.write(address, value)
                except BaseException as last_exc:
                    errors.append(dict(stage="emergency_hold", address=f"0x{address:02X}", error=repr(last_exc)))
            report["outcome"] = "recovery_incomplete_hardware_state_requires_review"
        try:
            final = self.snapshot("failure_final_registers")
            config_addresses = [0, 2, 0x18, 0x19, 0x1A, 0x1B, 0x1E, 0x1F, 0x23, 0x2E, 0x30, 0x31, 0x32, 0x33]
            report["differences_from_original_configuration"] = {
                f"0x{a:02X}": dict(original=original[a], final=final[a])
                for a in config_addresses if original[a] != final[a]}
            report["final_STATUS"] = final[1]
            report["final_CTRL"] = final[0]
            report["final_ARM"] = final[0x2E]
            report["final_actual_DAC_code"] = final[5]
            report["final_fixed_code_in_restored_original_limits"] = original[0x1E] <= final[2] <= original[0x1F] and PHYSICAL_MIN <= final[2] <= PHYSICAL_MAX
            report["hardware_state_readback_available"] = True
        except BaseException as exc:
            errors.append(dict(stage="final_snapshot", error=repr(exc)))
            report["hardware_state_readback_available"] = False
        save_json(self.folder / "recovery.json", report)
        return report

    def run(self):
        save_json(self.folder / "requested_configuration.json", dict(time=now(), **asdict(self.config)))
        save_json(self.folder / "status_policy.json", self.config.status_policy)
        self.event("status_policy", **self.config.status_policy)
        result = dict(started_at=now(), label=self.config.label, original_results_touched=False,
                      firmware_programming=False, flash_writes=False,
                      status_policy=self.config.status_policy)
        try:
            self.original = self.snapshot("original_registers")
            result["original_STATUS"] = self.original[1]
            self.preflight()
            result.update(self.configure())
            result["success"] = True
        except BaseException as exc:
            result.update(success=False, error=repr(exc), traceback=traceback.format_exc(),
                          control_register_writes_attempted=self.mutated)
            self.event("setup_failed", error=repr(exc), mutations_attempted=self.mutated)
            if self.mutated and self.original is not None:
                result["recovery"] = self.recover()
            else:
                result["recovery"] = dict(outcome="not_needed_no_control_writes")
        result["completed_at"] = now()
        save_json(self.folder / "result.json", result)
        return result


def run_setup(config, transport_factory=SerialUART, sleeper=time.sleep, output_root=OUTPUT_ROOT):
    config.validate()
    output_root = Path(output_root).resolve()
    output_root.relative_to(WORKSPACE.resolve())
    output_root.mkdir(parents=True, exist_ok=True)
    folder = output_root / ("setup_" + datetime.now(HK).strftime("%Y%m%d_%H%M%S_%f") + "_" + config.label)
    folder.mkdir(exist_ok=False)
    # No hardware driver initializer has run before the new output path exists.
    uart = None
    try:
        uart = transport_factory(config.port)
        result = Setup(config, uart, folder, sleeper=sleeper).run()
    except BaseException as exc:
        result = dict(success=False, error=repr(exc), hardware_open_failed=uart is None,
                      hardware_state_unknown=uart is not None)
        save_json(folder / "opening_or_driver_error.json", result)
    finally:
        if uart is not None:
            uart.close()  # Close never writes any control registers.
    return folder, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Open UART and execute; otherwise print plan only")
    parser.add_argument("--dac-code", type=lambda value: int(value, 0), required=True,
                        help="Explicit code derived from the current RF calibration")
    parser.add_argument("--dark-reference", type=float, default=None,
                        help="ADC dark reference measured in this session; if omitted, validate on-site local slope instead")
    parser.add_argument("--label", default="350mVpp")
    parser.add_argument("--kp", type=float, default=12.0)
    parser.add_argument("--ki", type=float, default=.75)
    parser.add_argument("--minimum", type=lambda value: int(value, 0), default=None,
                        help="Only this explicit argument permits changing OUT_MIN")
    parser.add_argument("--min-signal-codes", type=float, default=50.0)
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--allow-latched-otr", action="store_true",
                        help="Ignore only reset-only STATUS bit1; preserve raw status and require live capture fault checks")
    parser.add_argument("--arm", action="store_true",
                        help="Explicitly request ARM=1 and verify the closed loop is active; otherwise preserve original ARM")
    args = parser.parse_args(argv)
    config = Config(**{name: getattr(args, name) for name in Config.__dataclass_fields__}).validate()
    if not args.execute:
        print(json.dumps(make_plan(config), ensure_ascii=False, indent=2))
        return 0
    folder, result = run_setup(config)
    print(json.dumps(dict(saved=str(folder), **result), ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
