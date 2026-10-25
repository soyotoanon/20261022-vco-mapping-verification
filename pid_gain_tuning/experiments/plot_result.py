"""Plot measured candidate sweeps vs model predictions (post-optimization figure)."""
import sys, json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FULLV = sorted((BASE / "measurements").glob("*_fullV"))[-1]
SYSID = sorted((BASE / "measurements").glob("*_sysidA"))[-1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_fit_opt import load_sweep, plant_model, sensitivity_fs  # noqa

# model calibrated on sweepA, gain rescaled to the verification session (18:10)
fp, tau = 110.8e3, 5.31e-6
B_now = 0.0302 / np.abs(plant_model(1e4, 1.0, fp, tau))

fig, ax = plt.subplots(figsize=(10, 5.6), dpi=150, layout="constrained")
f = np.geomspace(100, 250e3, 600)

fA, pA, sA = load_sweep(SYSID / "sweepA")
ax.plot(fA, 20 * np.log10(sA), "o--", color="#7c8798", ms=4, lw=1,
        label="C4 (12, 0.5) 实测 · 同轮系统辨识")

colors = {"V1": "#176bc5", "V2": "#c2473c", "V3": "#1d8a4e"}
for cid, kp, ki in [("V1", 10.5, 0.658), ("V2", 14.0, 0.858), ("V3", 12.0, 0.750)]:
    js = FULLV / cid / "sweep_full.json"
    if not js.exists():
        continue
    d = json.loads(js.read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r["valid"]]
    fm = [r["frequency_Hz"] for r in rows]
    dm = [r["db"] for r in rows]
    s = 20 * np.log10(np.abs(sensitivity_fs(f, kp, ki, 5e6, B_now, fp, tau)))
    ax.plot(f, s, color=colors[cid], lw=1, alpha=0.45)
    ax.plot(fm, dm, "o-", color=colors[cid], ms=4, lw=1.4,
            label=f"{cid} ({kp}, {ki}) 实测 BW={d['bandwidth_Hz']/1e3:.1f} kHz")

ax.axhline(-3, color="#7c8798", ls="--", lw=1)
ax.axhline(0, color="#bbc3cf", lw=0.8)
ax.axvline(10e3, color="#e0b64f", ls=":", lw=1)
ax.set_xscale("log")
ax.set_xlabel("频率 / Hz")
ax.set_ylabel("闭环 / 开环振幅 · dB")
ax.set_title("模型引导的 PI 优化：实测点 + 模型曲线(淡) · 2026-10-23 晚场", loc="left", fontsize=11)
ax.grid(alpha=0.2)
ax.legend(fontsize=9, loc="lower left")
out = BASE / "results" / "模型引导优化对比.png"
fig.savefig(out, dpi=180)
print("saved", out)
