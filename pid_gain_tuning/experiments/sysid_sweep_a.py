"""System identification sweep A: same-session open+closed tone sweep under C4.

Gives |P(f)| (open amplitude / injected DAC codes) and |S_C4(f)| (dB) in one
session so the loop model is fitted on self-consistent data.
Ends with injection off, hold off, C4 gains restored. Never touches Flash.
"""
import sys, json, time, threading
from pathlib import Path
import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
from device import Device
from measurement import Measurement, save_json

OUT = BASE / "measurements" / (time.strftime("%Y%m%d_%H%M%S") + "_sysidA")
OUT.mkdir(parents=True)
cancel = threading.Event()


def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line, flush=True)
    with (OUT / "driver.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(kind, data):
    text = str(data)
    log(f"[{kind}] {text[:160]}")


def prompt(title, text):
    raise RuntimeError("本脚本不做遮光噪声测量,不应出现提示: " + title)


C4 = {0x1A: 0x8C00, 0x1B: 1}  # Kp=12 (0x8000|12*256), Ki=2^-1=0.5 (legacy format)
FREQS = np.geomspace(100.0, 250000.0, 30)
AMP_CODES = 64  # 0.0836 Vpp nominal, same as prior rounds

dev = Device("COM9")
log(f"connected, firmware={dev.read(0x3F):#06x}, raw_fs={dev.raw_fs:g}")
try:
    if dev.read(0x3F) != 0xA624:
        raise RuntimeError("需要 A624 增益格式固件")
    dev.inject(0, 0)
    dev.hold(False)
    for addr in (0x1B, 0x1A):
        dev.write(addr, C4[addr])
        if dev.read(addr) != C4[addr]:
            raise RuntimeError(f"C4 参数读回不一致 @{hex(addr)}")
    time.sleep(1.0)
    dev.require_closed_loop()
    mean_adc = dev.read(3)
    log(f"C4 restored; live ADC mean={mean_adc}, target={dev.read(0x18)}")
    if abs(mean_adc - dev.read(0x18)) > 150:
        raise RuntimeError("光强偏离目标超过 150 码,先人工恢复工作点")

    m = Measurement(dev, OUT / "sweepA", cancel, notify, prompt)
    save_json(OUT / "plan.json", dict(
        freqs_Hz=list(FREQS), amp_codes=AMP_CODES, controller="C4 Kp=12 Ki=0.5",
        regs={hex(a): dev.read(a) for a in [0, 1, 3, 0x11, 0x18, 0x19, 0x1A, 0x1B,
                                            0x1C, 0x1D, 0x1E, 0x1F, 0x2E, 0x3F]}))
    log("sweepA start: 30 pts, open+closed each")
    rows, bw = m.sweep(float(FREQS[0]), float(FREQS[-1]), AMP_CODES, len(FREQS))
    save_json(OUT / "sweepA_summary.json", dict(bandwidth_Hz=bw, rows=rows))
    log(f"sweepA done, interpolated crossing={bw}")
finally:
    try:
        dev.inject(0, 0)
        dev.hold(False)
        for addr in (0x1B, 0x1A):
            dev.write(addr, C4[addr])
        final = {hex(a): dev.read(a) for a in [0, 1, 0x11, 0x18, 0x1A, 0x1B, 0x2E, 0x30, 0x31]}
        save_json(OUT / "final_registers.json", final)
        log(f"final registers: {final}")
    finally:
        dev.close()
log("ALL DONE")
