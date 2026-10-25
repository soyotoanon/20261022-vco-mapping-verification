"""Full sweeps for V1/V2/V3 after screening (peak threshold skipped on purpose:
plant gain drifts ~±40% over an hour, so screening dB shifts with it; the full
curves are the comparable record). Restores C4 at the end; RAM only.
"""
import sys, time, threading
from pathlib import Path
import numpy as np

BASE = Path("D:/codex/2026-10-23/wo/outputs/pid_gain_tuning")
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
from device import Device
from measurement import Measurement, save_json

OUT = BASE / "measurements" / (time.strftime("%Y%m%d_%H%M%S") + "_fullV")
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


C4 = {0x1A: 0x8C00, 0x1B: 1}
CANDS = [("V1", 10.5, 0.658), ("V2", 14.0, 0.858), ("V3", 12.0, 0.750)]
AMP = 64


def regs_of(kp, ki):
    return {0x1A: 0x8000 | round(kp * 256), 0x1B: 0x8000 | round(ki * 32768)}


def apply(dev, regs, check=True):
    dev.inject(0, 0)
    dev.hold(False)
    for addr in (0x1B, 0x1A):
        dev.write(addr, regs[addr])
        if dev.read(addr) != regs[addr]:
            raise RuntimeError(f"参数读回不一致 @{hex(addr)}")
    if not check:
        return
    time.sleep(1.0)
    for _ in range(10):
        dev.require_closed_loop()
        if abs(dev.read(3) - dev.read(0x18)) > 150:
            raise RuntimeError("光强偏离目标")
        time.sleep(0.05)


dev = Device("COM9")
log(f"connected firmware={dev.read(0x3F):#06x}")
try:
    if dev.read(0x3F) != 0xA624:
        raise RuntimeError("需要 A624 固件")
    m = Measurement(dev, OUT, cancel, notify, prompt)
    for cid, kp, ki in CANDS:
        cdir = OUT / cid
        cdir.mkdir(parents=True, exist_ok=True)
        try:
            apply(dev, regs_of(kp, ki))
            m.folder = cdir
            rows, bw = m.sweep(100.0, 250000.0, AMP, 20)
            o10 = next((r["open_amplitude_ADC_codes"] for r in rows
                        if abs(r["frequency_Hz"] - 10000) < 200), None)
            d10 = next((r["db"] for r in rows
                        if abs(r["frequency_Hz"] - 10000) < 200), None)
            peak = max((r["db"] for r in rows if r["valid"]), default=None)
            save_json(cdir / "sweep_full.json", dict(rows=rows, bandwidth_Hz=bw,
                      Kp=kp, Ki_per_sample=ki, open10k_codes=o10))
            log(f"{cid} Kp={kp} Ki={ki}: BW={bw and round(bw/1e3,2)} kHz, "
                f"10k={d10 and round(d10,2)} dB, peak={peak and round(peak,2)} dB, "
                f"open10k={o10 and round(o10,3)} codes")
        except Exception as e:
            log(f"{cid}: ERROR {e}")
        finally:
            apply(dev, C4, check=False)
finally:
    try:
        dev.inject(0, 0)
        dev.hold(False)
        for addr in (0x1B, 0x1A):
            dev.write(addr, C4[addr])
        log("restored C4")
    finally:
        dev.close()
log("ALL DONE")
