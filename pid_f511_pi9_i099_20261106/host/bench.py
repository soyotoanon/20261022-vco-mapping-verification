"""Isolated F511 measurement transport. No instrument is opened on import."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import sys
import time
import urllib.request

import numpy as np

CONSOLE = Path(r'D:\DSproject\workbench\sites\mot556-console')
sys.path.insert(0, str(CONSOLE / 'vendor'))
import serial

ROOT = Path(__file__).resolve().parents[1]
FS = 5_000_000

def quantize_ki(value):
    if not math.isfinite(value) or not 0 <= value < 1:
        raise ValueError('Fixed-format Ki must be finite and in [0,1).')
    # Rounding a value near 1 must not spill into the format-select bit and
    # silently encode zero gain. The largest Q15 payload is 32767.
    return min(32767, round(value * 32768)) / 32768

def ki_word(value):
    return 0x8000 | round(quantize_ki(value) * 32768)

def stamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()

def new_run(label):
    name = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f') + '_' + label
    path = ROOT / 'results' / name
    path.mkdir(parents=True, exist_ok=False)
    return path

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def console_free():
    for channel in ('556', '399'):
        url = 'http://127.0.0.1:8766/api/state?channel=' + channel
        with urllib.request.urlopen(url, timeout=4) as response:
            state = json.load(response)['state']
        if state['fpga'].get('connected'):
            raise RuntimeError('Control console owns a serial device; do not open a competing session.')

class UART:
    def __init__(self, port, log_dir):
        self.log_dir = Path(log_dir)
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=2)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    @staticmethod
    def frame(cmd, addr, value=0):
        if not 0 <= addr < 64 or not 0 <= value <= 65535:
            raise ValueError('Invalid UART register/value.')
        return bytes([197, cmd, addr, value & 255, value >> 8, 92, 197, 197])

    def event(self, **fields):
        with (self.log_dir / 'register_events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(time=stamp(), **fields), ensure_ascii=False) + '\n')

    def send(self, payload):
        if self.serial.write(payload) != len(payload):
            raise IOError('Incomplete UART write.')

    def read(self, addr):
        self.send(self.frame(2, addr))
        buf = bytearray()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            buf.extend(self.serial.read(max(1, min(4096, self.serial.in_waiting))))
            while len(buf) >= 8:
                if buf[0] != 197 or buf[5:8] != bytes([92, 197, 197]):
                    del buf[0]
                    continue
                frame = bytes(buf[:8])
                del buf[:8]
                if frame[1] == 2 and frame[2] == addr:
                    return frame[3] | frame[4] << 8
        raise TimeoutError(f'No response from register {addr:02X}.')

    def write(self, addr, value):
        self.event(action='write_attempt', register=f'{addr:02X}', value=value)
        self.send(self.frame(1, addr, value))
        if addr == 0x34:  # Capture command reads back the immutable firmware ID.
            return
        expected = (value & 3) if addr == 0x30 else (int(value == 0xA501) if addr == 0x2E else value)
        actual = self.read(addr)
        self.event(action='readback', register=f'{addr:02X}', value=actual)
        if actual != expected:
            raise RuntimeError(f'Register {addr:02X} expected {expected}, got {actual}.')

    def snapshot(self):
        return {f'{addr:02X}': self.read(addr) for addr in range(64)}

    def live(self):
        return {f'{addr:02X}': self.read(addr) for addr in (0, 1, 3, 5, 0x18, 0x1E, 0x1F, 0x2E, 0x30)}

    def close(self):
        self.serial.close()

def require_lock(state, tolerance=40, headroom=128):
    if state['00'] != 1 or state['2E'] != 1:
        raise RuntimeError('PI is not enabled and armed.')
    # Bit 1 is a historical ADC OTR latch: baseline adc_drv clears it only at
    # reset. Keep it visible in snapshots; capture() separately rejects live OTR.
    if state['01'] & 0x3D:
        raise RuntimeError(f'Loop fault/limit flags: {state["01"]:#x}.')
    if not state['1E'] + headroom < state['05'] < state['1F'] - headroom:
        raise RuntimeError('Insufficient actuator headroom.')
    if abs(state['03'] - state['18']) > tolerance:
        raise RuntimeError('Measured power does not track the setpoint.')

class ProbeSession:
    """Restore gain/probe settings even if a trial or waveform transfer fails."""
    probe_regs = (0x30, 0x31, 0x32, 0x33, 0x35, 0x37)

    def __init__(self, out, port='COM9'):
        self.out = Path(out)
        self.port = port

    def __enter__(self):
        console_free()
        self.u = UART(self.port, self.out)
        try:
            self.original = self.u.snapshot()
            save(self.out / 'initial_registers.json', self.original)
            if self.original['34'] != 0xF511 or self.original['3F'] != 0xA624:
                raise RuntimeError('Expected F511; refuse to tune a different firmware.')
            if self.original['11'] != 4 or self.original['23'] != 0 or self.original['28'] != 0:
                raise RuntimeError('Unexpected sample rate, setpoint injection or UART stream.')
            if self.original['30'] != 0 or self.original['36'] & 1:
                raise RuntimeError('Another probe operation is active.')
            require_lock(self.original)
        except BaseException:
            self.u.close()
            raise
        return self

    def quiet(self):
        self.u.write(0x30, 0)

    def check(self):
        state = self.u.live()
        require_lock(state)
        return state

    def gains(self, kp, ki):
        if not math.isfinite(kp) or not math.isfinite(ki) or not 0 <= kp <= 30 or not 0 <= ki < 1:
            raise ValueError('Requested gain is outside the initial scan envelope.')
        self.quiet()
        self.check()
        self.u.write(0x1B, ki_word(ki))
        self.u.write(0x1A, 0x8000 | round(kp * 256))
        for _ in range(5):
            time.sleep(.04)
            self.check()

    def tone(self, frequency, amplitude, held):
        amplitude_limit = 512 if frequency >= 300000 else 128
        if not 100 <= frequency <= 1000000 or not 1 <= amplitude <= amplitude_limit:
            raise ValueError('Requested probe tone is outside the initial scan envelope.')
        self.quiet()
        state = self.check()
        if not state['1E'] + amplitude + 128 < state['05'] < state['1F'] - amplitude - 128:
            raise RuntimeError('Insufficient DAC headroom for the requested tone amplitude.')
        increment = round(frequency / FS * 2**32)
        self.u.write(0x31, amplitude)
        self.u.write(0x32, increment & 65535)
        self.u.write(0x33, increment >> 16)
        if held:
            self.u.write(0x30, 6)
        self.u.write(0x30, 7 if held else 5)
        return increment * FS / 2**32

    def capture(self, decimation_log2, points=2048, after_start=None):
        if not 0 <= decimation_log2 <= 12 or points not in (2048, 4096, 8192):
            raise ValueError('Invalid capture configuration.')
        self.u.write(0x35, decimation_log2)
        seq = self.u.read(0x3D)
        self.u.write(0x34, 1)
        host_event = after_start() if after_start is not None else None
        deadline = time.monotonic() + 8198 * 2**decimation_log2 / FS + 3
        while True:
            flags = self.u.read(0x36)
            if flags & 12:
                raise RuntimeError(f'Capture clipped or faulted: {flags:#x}.')
            if flags & 2:
                break
            if time.monotonic() > deadline:
                raise TimeoutError('FPGA capture timeout.')
            time.sleep(.003)
        if self.u.read(0x3D) != (seq + 1) & 65535 or self.u.read(0x3E) != 8192:
            raise RuntimeError('Capture sequence/count did not advance correctly.')
        during = self.u.live()
        self.quiet()
        raw = np.empty(points, np.uint16)
        for base in range(0, points, 64):
            payload = b''.join(self.u.frame(1, 0x37, j) + self.u.frame(2, 0x3B) for j in range(base, base + 64))
            self.u.send(payload)
            reply = bytearray()
            deadline = time.monotonic() + 3
            while len(reply) < 512:
                reply.extend(self.u.serial.read(512 - len(reply)))
                if time.monotonic() > deadline:
                    raise TimeoutError('Incomplete BRAM waveform transfer.')
            for j in range(64):
                frame = reply[j*8:(j+1)*8]
                if frame[:3] != bytes([197, 2, 0x3B]) or frame[5:] != bytes([92, 197, 197]):
                    raise RuntimeError('Malformed BRAM response.')
                raw[base+j] = frame[3] | frame[4] << 8
            if base % 256 == 0:
                self.check()
        if self.u.read(0x36) & 15 != 2 or self.u.read(0x3D) != (seq + 1) & 65535:
            raise RuntimeError('Capture flags or sequence changed during transfer.')
        return raw, dict(time=stamp(), fs_hz=FS / 2**decimation_log2, raw_fs_hz=FS,
                         decimation=2**decimation_log2, hardware_samples=8192,
                         returned_samples=points, flags=flags, registers_during_capture=during,
                         host_event=host_event,
                         encoding='unsigned ADC codes multiplied by four; third-order CIC')

    def __exit__(self, typ, value, traceback):
        try:
            self.quiet()
            self.u.write(0x34, 2)
            for addr in (0x1B, 0x1A, *self.probe_regs[1:]):
                self.u.write(addr, self.original[f'{addr:02X}'])
            self.u.write(0x30, self.original['30'])
            final = self.u.snapshot()
            save(self.out / 'final_registers.json', final)
            restored = all(final[f'{a:02X}'] == self.original[f'{a:02X}']
                           for a in (0x1A, 0x1B, *self.probe_regs))
            save(self.out / 'restoration.json', dict(time=stamp(), restored=restored,
                 trial_error=None if value is None else str(value), final_status=final['01']))
            if not restored:
                raise RuntimeError('Final configuration restoration did not match baseline.')
        finally:
            self.u.close()

def fit_tone(raw, meta, frequency):
    y = raw.astype(float) / 4
    fs = meta['fs_hz']
    t = np.arange(len(y)) / fs
    t -= t.mean()
    design = np.column_stack((np.cos(2*np.pi*frequency*t), np.sin(2*np.pi*frequency*t), np.ones(len(y)), t))
    coeff = np.linalg.lstsq(design, y, rcond=None)[0]
    ratio = meta['decimation']
    omega = 2*np.pi*frequency/FS
    droop = abs(np.sin(omega*ratio/2)/(ratio*np.sin(omega/2)))**3
    amp = float(np.hypot(*coeff[:2])) / droop
    return dict(amplitude_peak_adc_codes=amp, mean_adc=float(coeff[2]),
                residual_rms_adc=float(np.std(y-design@coeff)), cic_magnitude=float(droop))
