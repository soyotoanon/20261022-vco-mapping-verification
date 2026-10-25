"""Model-guided candidate verification.

Order per candidate: 10 kHz screen -> danger-zone peak scan -> full sweep.
Model predictions (current plant B=0.0244, fp=111k, tau=5.3us):
  V1 (10.5, 0.658): BW 10.3k, peak +3.2
  V2 (14.0, 0.858): BW 12.9k, peak +4.5
  V3 (12.0, 0.750): BW ~11.6k, peak ~+3.9
Ends with the best candidate applied (or C4 if none qualifies). RAM only.
"""
import sys, json, time, threading
from pathlib import Path
import numpy as np

BASE = Path("D:/codex/2026-10-23/wo/outputs/pid_gain_tuning")
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
from device import Device
from measurement import Measurement, save_json
from analysis import crossing

OUT = BASE / "measurements" / (time.strftime("%Y%m%d_%H%M%S") + "_verifyV")
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
SCREEN = [10000, 30000, 40000, 50000, 65000, 75000, 100000]
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
            raise RuntimeError("光强偏离目标超过 150 码")
        time.sleep(0.05)


def one_point(m, dev, freq, dest):
    m.folder = dest
    dest.mkdir(parents=True, exist_ok=True)
    rows, _ = m.sweep(float(freq), float(freq), AMP, 1)
    return rows[0]


dev = Device("COM9")
log(f"connected firmware={dev.read(0x3F):#06x}")
results = {}
try:
    if dev.read(0x3F) != 0xA624:
        raise RuntimeError("需要 A624 固件")
    m = Measurement(dev, OUT, cancel, notify, prompt)

    # session control: fresh C4 reference at 10 kHz
    apply(dev, C4)
    r = one_point(m, dev, 10000, OUT / "C4ref_10k")
    log(f"C4 reference 10 kHz: {r['db']:.2f} dB (open {r['open_amplitude_ADC_codes']:.3f} codes)")
    results["C4ref_10k"] = r

    for cid, kp, ki in CANDS:
        cdir = OUT / cid
        cdir.mkdir(parents=True, exist_ok=True)
        crec = dict(id=cid, Kp=kp, Ki_per_sample=ki, fs_Hz=dev.raw_fs,
                    registers={hex(a): v for a, v in regs_of(kp, ki).items()},
                    status="screening", rows=[])
        try:
            apply(dev, regs_of(kp, ki))
            for freq in SCREEN:
                r = one_point(m, dev, freq, cdir / f"{freq}")
                r = dict(r); crec["rows"].append(r)
                log(f"{cid} Kp={kp} Ki={ki}: {freq} Hz -> {r['db'] if r['valid'] else float('nan'):.2f} dB")
                save_json(cdir / "candidate.json", crec)
                if freq == 10000 and (not r["valid"] or r["db"] > -3.0):
                    crec.update(status="below_10kHz_target")
                    break
            else:
                pass
            if crec["status"] == "screening":
                vals = [x["db"] for x in crec["rows"] if x["valid"]]
                peak = max(vals)
                crec["screen_peak_dB"] = peak
                if peak <= 4.6:
                    log(f"{cid}: screen passed (peak {peak:.2f}), full sweep")
                    m.folder = cdir / "full"
                    (cdir / "full").mkdir(exist_ok=True)
                    rows, bw = m.sweep(100.0, 250000.0, AMP, 20)
                    crec.update(status="fully_measured", bandwidth_Hz=bw,
                                full_rows=[{k: v for k, v in r.items()} for r in rows])
                    save_json(cdir / "sweep_full.json",
                              dict(rows=crec["full_rows"], bandwidth_Hz=bw))
                    log(f"{cid}: full sweep BW={bw and round(bw/1e3,2)} kHz")
                else:
                    crec.update(status="peak_too_high")
        except Exception as e:
            crec.update(status="error", error=str(e))
            log(f"{cid}: ERROR {e}")
        finally:
            save_json(cdir / "candidate.json", crec)
            results[cid] = crec
            apply(dev, C4, check=False)

    # pick best: fully measured, 10k<=-3, then max BW
    good = [c for c in results.values() if isinstance(c, dict)
            and c.get("status") == "fully_measured"
            and any(r["frequency_Hz"] < 10001 and r["valid"] and r["db"] <= -3
                    for r in c["rows"])]
    if good:
        best = max(good, key=lambda c: c.get("bandwidth_Hz") or 0)
        best_regs = {int(a, 16): v for a, v in best["registers"].items()}
        log(f"selecting {best['id']} as final")
    else:
        best_regs = C4
        log("no candidate qualified; restoring C4")
    apply(dev, best_regs)
    save_json(OUT / "selection.json",
              dict(selected=best["id"] if good else "C4",
                   registers={hex(a): v for a, v in best_regs.items()},
                   summary={k: (v.get("status"), v.get("bandwidth_Hz"),
                                v.get("screen_peak_dB"))
                            for k, v in results.items() if isinstance(v, dict)}))
finally:
    try:
        dev.inject(0, 0)
        dev.hold(False)
        final = {hex(a): dev.read(a) for a in [0, 1, 0x11, 0x18, 0x1A, 0x1B, 0x2E, 0x30, 0x31]}
        save_json(OUT / "final_registers.json", final)
        log(f"final: {final}")
    finally:
        dev.close()
log("ALL DONE")
