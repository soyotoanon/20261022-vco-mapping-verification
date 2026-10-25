"""System identification sweep B at 12.5 MHz loop rate, 100 Hz-600 kHz.

Extends |P(f)| beyond the 250 kHz injection limit of the 5 MHz configuration.
Closed-loop reference uses V3-equivalent per-second gains: Kp=12, Ki=0.30/sample
(= 0.75/sample at 5 MHz). Ends restored to V3 at 5 MHz. RAM only.
"""
import sys, time, threading
from pathlib import Path
import numpy as np

BASE = Path("D:/codex/2026-10-23/wo/outputs/pid_gain_tuning")
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
from device import Device
from measurement import Measurement, save_json

OUT = BASE / "measurements" / (time.strftime("%Y%m%d_%H%M%S") + "_sysidB")
OUT.mkdir(parents=True)
cancel = threading.Event()


def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line, flush=True)
    with (OUT / "driver.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(kind, data):
    if kind != "status" or "采集中" not in str(data):
        log(f"[{kind}] {str(data)[:150]}")


def prompt(title, text):
    raise RuntimeError("不应出现提示: " + title)


V3_5M = {0x11: 4, 0x1A: 0x8C00, 0x1B: 0x8000 | round(0.75 * 32768)}
V3_12M = {0x11: 1, 0x1A: 0x8C00, 0x1B: 0x8000 | round(0.30 * 32768)}  # same per-second
AMP = 64
FREQS = np.geomspace(100.0, 600000.0, 32)

dev = Device("COM9")
log(f"connected firmware={dev.read(0x3F):#06x}")


def apply(regs, check=True):
    dev.inject(0, 0)
    dev.hold(False)
    for addr in (0x1B, 0x1A, 0x11):  # gains before rate
        dev.write(addr, regs[addr])
        if dev.read(addr) != regs[addr]:
            raise RuntimeError(f"读回不一致 @{hex(addr)}")
    if not check:
        return
    time.sleep(1.0)
    for _ in range(10):
        dev.require_closed_loop()
        if abs(dev.read(3) - dev.read(0x18)) > 150:
            raise RuntimeError("光强偏离目标")
        time.sleep(0.05)


try:
    if dev.read(0x3F) != 0xA624:
        raise RuntimeError("需要 A624 固件")
    log("switch to 12.5 MHz: V3-equivalent (12, 0.30)")
    apply(V3_12M)
    dev.raw_fs = 50e6 / (2 * (dev.read(0x11) + 1))
    log(f"raw_fs now {dev.raw_fs/1e6} MHz")
    m = Measurement(dev, OUT / "sweepB", cancel, notify, prompt)
    save_json(OUT / "plan.json", dict(freqs_Hz=list(FREQS), amp_codes=AMP,
        controller="Kp=12 Ki=0.30/sample @12.5MHz (V3-equivalent)",
        regs={hex(a): dev.read(a) for a in [0, 1, 3, 0x11, 0x18, 0x19, 0x1A, 0x1B, 0x2E, 0x3F]}))
    log("sweepB start: 32 pts 100 Hz-600 kHz, open+closed each")
    rows, bw = m.sweep(float(FREQS[0]), float(FREQS[-1]), AMP, len(FREQS))
    save_json(OUT / "sweepB_summary.json", dict(bandwidth_Hz=bw, rows=rows))
    log(f"sweepB done, crossing={bw}")
    # quick ADC health check at 12.5 MHz: closed-loop capture stats, no injection
    dev.inject(0, 0)
    y, meta = dev.capture(4, cancel)  # 781 kHz effective
    log(f"ADC health @12.5M: mean={y.mean():.1f} std={y.std():.2f} codes "
        f"(5M reference: std ~ 1-2 codes expected)")
finally:
    try:
        apply(V3_5M, check=False)
        dev.inject(0, 0)
        dev.hold(False)
        final = {hex(a): dev.read(a) for a in [0, 1, 0x11, 0x18, 0x1A, 0x1B, 0x2E, 0x30, 0x31]}
        save_json(OUT / "final_registers.json", final)
        log(f"restored V3@5MHz: {final}")
    finally:
        dev.close()
log("ALL DONE")
