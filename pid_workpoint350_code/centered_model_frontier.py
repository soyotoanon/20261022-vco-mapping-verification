"""Choose real-test candidates from the newly measured magnitude-only model."""
import json
from pathlib import Path
import numpy as np
from fit_model_candidates import plant, first_upward_crossing

ROOT = Path(__file__).resolve().parents[1] / "outputs" / "workpoint_350mVpp_20261027"
data = json.loads((ROOT / "centered_model_c01.json").read_text(encoding="utf-8"))
model = data["model"]
frequencies = np.unique(np.r_[np.geomspace(1000, 250000, 1500), 10000])
p = plant(frequencies, model["fitted_B_ADC_per_DAC"], model["fitted_fp_Hz"], model["fitted_tau_microseconds"])
integral = 1 / np.expm1(2j * np.pi * frequencies / 5e6)
ten = int(np.flatnonzero(frequencies == 10000)[0])
frontiers = {cap: None for cap in [4.5, 5., 5.6, 6., 6.5, 7.]}
for kp in np.arange(0, 18.001, .1):
    kp = round(kp * 256) / 256
    for ki in np.arange(.25, .90001, .005):
        ki = round(ki * 32768) / 32768
        values = 20 * np.log10(np.abs(1 / (1 + (kp + ki * integral) * p)))
        if values[ten] > -3:
            continue
        peak_index = int(values.argmax())
        peak = float(values[peak_index])
        if peak > max(frontiers):
            continue
        bw = first_upward_crossing(frequencies, values)
        row = dict(kp=kp, ki=ki, predicted_10kHz_dB=float(values[ten]),
                   predicted_peak_dB=peak, predicted_peak_at_Hz=float(frequencies[peak_index]),
                   predicted_bandwidth_Hz=bw)
        for cap in frontiers:
            if peak <= cap and bw and (frontiers[cap] is None or bw > frontiers[cap]["predicted_bandwidth_Hz"]):
                frontiers[cap] = row
out = dict(source_model="centered_model_c01.json", predictions_only=True,
           peak_caps_are_comparison_scenarios_not_user_approval=True,
           prediction_grid_Hz=[1000, 250000], modeled_frontiers=frontiers,
           limitation="Magnitude-only, assumed one-pole/delay model; every selected candidate requires physical screening")
(ROOT / "centered_candidate_frontier.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=2))
