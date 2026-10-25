"""Fit plant model from a same-session sweep, then optimize PI gains numerically.

Model: P(f) = B / (1 + j f/fp) * exp(-j 2 pi f tau)
Loop:  C(f) = Kp + Ki * z^-1/(1 - z^-1), z = exp(j 2 pi f / fs), fs = 5 MHz
       S(f) = 1 / (1 + C(f) P(f))   (measured rejection = |S|)

Plant phase is reconstructed from |S| and |L| = |C||P| without any phase
reference:  cos(phi_L) = (|S|^-2 - 1 - |L|^2) / (2|L|), negative (lag) branch.

Usage: python model_fit_opt.py <sweepA_dir> [second_sweep_dir ...]
"""
import sys, json, csv
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares

FS = 5e6
T = 1 / FS
C4 = (12.0, 0.5)


def load_sweep(folder):
    """Return f, |P|, |S| arrays from a sweep folder, CSV, or sweep.json."""
    import json as _json
    p = Path(folder)
    if p.is_dir():
        src = p / "rejection.csv"
        if not src.exists():
            src = p / "sweep.json"
    else:
        src = p
    if src.suffix.lower() == ".json":
        d = _json.loads(src.read_text(encoding="utf-8"))
        rows = d["rows"]
        f = np.array([r["frequency_Hz"] for r in rows if r["valid"]], float)
        pm = np.array([r["open_amplitude_ADC_codes"] / r["amplitude_DAC_codes"]
                       for r in rows if r["valid"]], float)
        sm = np.array([10 ** (r["db"] / 20) for r in rows if r["valid"]], float)
        return f, pm, sm
    f, pm, sm = [], [], []
    with open(src, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("valid")) != "True":
                continue
            f.append(float(r["frequency_Hz"]))
            pm.append(float(r["open_amplitude_ADC_codes"]) / float(r["amplitude_DAC_codes"]))
            sm.append(10 ** (float(r["db"]) / 20))
    return np.array(f), np.array(pm), np.array(sm)


def controller(f, kp, ki):
    z = np.exp(2j * np.pi * f / FS)
    return kp + ki / (z - 1.0)  # ki * z^-1/(1-z^-1) == ki/(z-1)


def reconstruct_phase(f, pmag, smag, kp, ki):
    C = controller(f, kp, ki)
    L = np.abs(C) * pmag
    with np.errstate(divide="ignore", invalid="ignore"):
        c = (smag ** -2 - 1 - L ** 2) / (2 * L)
    ok = np.isfinite(c) & (np.abs(c) <= 1) & (L > 0.15) & (L < 60)
    phi_L = -np.arccos(np.clip(c, -1, 1))  # lag branch
    phi_P = phi_L - np.angle(C)
    return phi_P, ok


def plant_model(f, B, fp, tau):
    return B / (1 + 1j * f / fp) * np.exp(-2j * np.pi * f * tau)


def fit_plant(datasets):
    """datasets: list of (f, |P|, |S|, kp, ki). Shared fp, tau; per-set B."""
    # stacked residual: log magnitude on all points, phase on reconstructable midband
    def resid(x):
        fp, tau = x[:2]
        Bs = x[2:]
        out = []
        for i, (f, pmag, smag, kp, ki) in enumerate(datasets):
            P = plant_model(f, Bs[i], fp, tau)
            out.append(np.log(np.abs(P) / pmag))  # magnitude
            phi_P, ok = reconstruct_phase(f, pmag, smag, kp, ki)
            mid = ok & (f >= 4000) & (f <= 60000)
            if mid.any():
                d = np.angle(P[mid]) - phi_P[mid]
                d = (d + np.pi) % (2 * np.pi) - np.pi
                out.append(2.0 * d)  # weight phase
        return np.concatenate(out)

    x0 = [1e5, 7e-6] + [0.035] * len(datasets)
    bounds = ([1e4, 1e-6] + [1e-3] * len(datasets),
              [1e6, 3e-5] + [1.0] * len(datasets))
    sol = least_squares(resid, x0, bounds=bounds)
    return sol.x[0], sol.x[1], sol.x[2:], sol.cost


def sensitivity(f, kp, ki, B, fp, tau):
    L = controller(f, kp, ki) * plant_model(f, B, fp, tau)
    return 1 / (1 + L)


def db(x):
    return 20 * np.log10(np.abs(x))


def controller_fs(f, kp, ki, fs):
    z = np.exp(2j * np.pi * f / fs)
    return kp + ki / (z - 1.0)


def sensitivity_fs(f, kp, ki, fs, B, fp, tau):
    L = controller_fs(f, kp, ki, fs) * plant_model(f, B, fp, tau)
    return 1 / (1 + L)


def design(B, fp, tau, f_trust_max, gain_margins=(0.7, 1.0, 1.3)):
    """Grid over (fs, Kp, Ki<=0.999). Constraints must hold at all gain margins."""
    f = np.geomspace(1000, f_trust_max, 2500)
    results = []
    for fs in [5e6, 6.25e6, 8.333e6, 12.5e6]:
        kps = np.arange(0, 40.01, 0.5)
        kis = np.r_[np.geomspace(0.05, 0.999, 80)]
        for kp in kps:
            for ki in kis:
                peaks = {}
                bw = 0.0
                for g in gain_margins:
                    s = db(sensitivity_fs(f, kp, ki, fs, B * g, fp, tau))
                    peaks[g] = s.max()
                    if g == 1.0:
                        below = np.where(s < -3)[0]
                        if len(below):
                            i = below[-1]
                            if i + 1 < len(f):
                                frac = (-3 - s[i]) / (s[i + 1] - s[i])
                                bw = f[i] * (f[i + 1] / f[i]) ** frac
                            else:
                                bw = f[i]
                s10 = db(sensitivity_fs(np.array([1e4]), kp, ki, fs, B, fp, tau))[0]
                results.append(dict(fs_Hz=fs, Kp=float(kp), Ki=float(ki),
                                    bandwidth_Hz=float(bw),
                                    peak_dB=float(peaks[1.0]),
                                    peak_dB_gain_down=float(peaks.get(0.7, np.nan)),
                                    peak_dB_gain_up=float(peaks.get(1.3, np.nan)),
                                    peak_worst_dB=float(max(peaks.values())),
                                    s10k_dB=float(s10)))
    return results


if __name__ == "__main__":
    datasets = []
    for d in sys.argv[1:]:
        f, p, s = load_sweep(d)
        datasets.append((f, p, s, *C4))
        print(f"loaded {d}: {len(f)} pts, median |P|={np.median(p):.4f}")
    # Historical sweeps with different controllers tighten the shared fp/tau fit.
    # Their gain B is fitted per dataset; only fp and tau are shared.
    HIST = Path(__file__).resolve().parents[1] / "measurements"
    extra = []
    c5 = HIST / "20261023_165315/C5"
    c6 = HIST / "20261023_165315/C6"
    for path, kp, ki in [(c5, 16, 0.399993896484375), (c6, 16, 0.45001220703125)]:
        src = path / "rejection.csv"
        if not src.exists():
            src = path / "sweep.json"
        if src.exists():
            f, p, s = load_sweep(src)
            datasets.append((f, p, s, float(kp), float(ki)))
            extra.append((path.parent.name + "/" + path.name, len(f)))
    for name, n in extra:
        print(f"loaded historical {name}: {n} pts")
    fp, tau, Bs, cost = fit_plant(datasets)
    print(f"\nfit: fp={fp/1e3:.1f} kHz  tau={tau*1e6:.2f} us  B={[round(float(b),4) for b in Bs]}  cost={cost:.3f}")
    B = float(Bs[0])
    # verify model against first dataset
    f, p, s = datasets[0][:3]
    pred = db(sensitivity(f, *C4, B, fp, tau))
    meas = db(s)
    for i in range(len(f)):
        print(f"  {f[i]:9.0f} Hz  meas {meas[i]:7.2f} dB   pred {pred[i]:7.2f} dB   |P| meas {p[i]:.4f} fit {np.abs(plant_model(f[i],B,fp,tau)):.4f}")
    print("\noptimizing (peak must stay <= +3 dB at -30%, nominal, +30% plant gain) ...")
    f_trust = min(250e3, max(float(d[0].max()) for d in datasets))
    res = design(B, fp, tau, f_trust)
    base = [r for r in res if r["s10k_dB"] <= -3.0 and r["bandwidth_Hz"] >= 10000]
    print(f"trust region 1 kHz-{f_trust/1e3:.0f} kHz; {len(base)}/{len(res)} designs have 10kHz<=-3dB and BW>=10kHz")
    for peak_lim in [1.5, 3.0, 4.5, 6.0]:
        ok = [r for r in base if r["peak_worst_dB"] <= peak_lim]
        ok.sort(key=lambda r: (-r["bandwidth_Hz"], r["peak_worst_dB"]))
        print(f"\n== worst-case peak <= +{peak_lim} dB across gain drift: {len(ok)} designs ==")
        print(f"{'fs/MHz':>6} {'Kp':>5} {'Ki':>7} {'BW/kHz':>8} {'peak':>6} {'pk-30%':>7} {'pk+30%':>7} {'10kHz':>7}")
        for r in ok[:8]:
            print(f"{r['fs_Hz']/1e6:6.2f} {r['Kp']:5.1f} {r['Ki']:7.3f} {r['bandwidth_Hz']/1e3:8.1f} "
                  f"{r['peak_dB']:6.2f} {r['peak_dB_gain_down']:7.2f} {r['peak_dB_gain_up']:7.2f} {r['s10k_dB']:7.2f}")
    ok = [r for r in base if r["peak_worst_dB"] <= 3.0]
    ok.sort(key=lambda r: (-r["bandwidth_Hz"], r["peak_worst_dB"]))
    out = dict(plant=dict(fp_Hz=float(fp), tau_s=float(tau), Bs=[float(b) for b in Bs]),
               trust_max_Hz=f_trust, designs=ok[:60])
    Path("model_result.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote model_result.json")
