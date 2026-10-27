"""Offline, magnitude-only model guidance for the B350 same-session data.

No phase response or stability margin is measured or reported here. The model
is only a conditional predictor to choose a few PI settings for real screening.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import least_squares

sys.dont_write_bytecode = True
ROOT = Path(r"D:\codex\2026-10-27\1-3-txt-vco-13-khz")
SOURCE = ROOT / "outputs" / "workpoint_350mVpp_20261027" / "20261027T045840_497990Z_B350_PI9p6_I0p60_screen" / "status.json"
OUTPUT = ROOT / "outputs" / "model_guided_candidates.json"
FS = 5e6


def db(value):
    return 20 * np.log10(np.abs(value))


def controller(f, kp, ki):
    return kp + ki / np.expm1(2j * np.pi * np.asarray(f) / FS)


def plant(f, B, fp, tau_us):
    return B / (1 + 1j * np.asarray(f) / fp) * np.exp(-2j * np.pi * np.asarray(f) * tau_us * 1e-6)


def rejection(f, kp, ki, B, fp, tau_us):
    return db(1 / (1 + controller(f, kp, ki) * plant(f, B, fp, tau_us)))


def first_upward_crossing(f, values, level=-3):
    for index in range(len(f) - 1):
        if values[index] <= level < values[index + 1]:
            weight = (level - values[index]) / (values[index + 1] - values[index])
            return float(np.exp(np.log(f[index]) * (1 - weight) + np.log(f[index + 1]) * weight))
    return None


def main():
    content = SOURCE.read_bytes()
    source = json.loads(content)
    rows = [row for row in source["rows"] if row.get("valid") and row.get("mean_comparison_valid", True)]
    frequency = np.array([row["frequency_Hz"] for row in rows])
    measured_plant = np.array([row["open_amplitude_ADC_codes"] / row["amplitude_DAC_codes"] for row in rows])
    measured_db = np.array([row["db"] for row in rows])
    kp, ki = source["tested_gains"]["kp"], source["tested_gains"]["ki"]
    if source["raw_sample_rate_Hz"] != FS:
        raise RuntimeError("Unexpected sampling rate")

    def residual(x):
        model = plant(frequency, np.exp(x[0]), np.exp(x[1]), x[2])
        modeled_s = db(1 / (1 + controller(frequency, kp, ki) * model))
        return np.r_[db(model) - db(measured_plant), modeled_s - measured_db]

    fits = []
    for delay in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 20, 23, 25, 28, 30]:
        solution = least_squares(residual, [np.log(.046), np.log(1e5), delay],
            bounds=([np.log(.005), np.log(1e4), 0], [np.log(.2), np.log(2e6), 30]))
        fits.append(solution)
    fit = min(fits, key=lambda solution: np.sum(solution.fun ** 2))
    B, fp, tau = float(np.exp(fit.x[0])), float(np.exp(fit.x[1])), float(fit.x[2])

    trust_min, trust_max = float(frequency.min()), float(frequency.max())
    grid = np.unique(np.r_[np.geomspace(1000, trust_max, 1800), 10000.0])
    P = plant(grid, B, fp, tau)
    integral = 1 / np.expm1(2j * np.pi * grid / FS)
    ten_index = int(np.argmin(abs(grid - 10000)))

    def evaluate(kp_value, ki_value):
        # Match the actual gain quantization before predicting candidates.
        pword, iword = round(kp_value * 256), round(ki_value * 32768)
        actual_kp, actual_ki = pword / 256, iword / 32768
        C = actual_kp + actual_ki * integral
        values = db(1 / (1 + C * P))
        scaled = [db(1 / (1 + C * P * scale)) for scale in [.9, 1.0, 1.1]]
        peak_index = int(np.argmax(values))
        return dict(Kp=actual_kp, Ki_per_sample=actual_ki,
                    Kp_register=0x8000 | pword, Ki_register=0x8000 | iword,
                    predicted_10kHz_dB=float(values[ten_index]),
                    predicted_peak_dB=float(values[peak_index]),
                    predicted_peak_at_Hz=float(grid[peak_index]),
                    predicted_minus3dB_first_upward_crossing_Hz=first_upward_crossing(grid, values),
                    modeled_peak_with_10percent_gain_down_dB=float(scaled[0].max()),
                    modeled_peak_with_10percent_gain_up_dB=float(scaled[2].max()),
                    modeled_peak_worst_with_plusminus10percent_gain_dB=float(max(v.max() for v in scaled)),
                    modeled_10kHz_worst_with_plusminus10percent_gain_dB=float(max(v[ten_index] for v in scaled)))

    designs = []
    for p in np.arange(6, 12.0001, .1):
        for i in np.arange(.35, .650001, .005):
            designs.append(evaluate(float(p), float(i)))
    nominal = [d for d in designs if d["predicted_10kHz_dB"] <= -3 and d["predicted_peak_dB"] <= 5]
    robust = [d for d in nominal if d["modeled_peak_worst_with_plusminus10percent_gain_dB"] <= 5
              and d["modeled_10kHz_worst_with_plusminus10percent_gain_dB"] <= -3]
    conservative = [d for d in robust if d["predicted_peak_dB"] <= 4.2]
    key = lambda d: -(d["predicted_minus3dB_first_upward_crossing_Hz"] or 0)
    for collection in [nominal, robust, conservative]:
        collection.sort(key=key)
    recommendations = []
    chosen_keys = set()
    for reason, collection in [("first_screen_margin_for_model_error", conservative),
                                ("larger_bandwidth_with_plusminus10percent_gain_test", robust),
                                ("highest_nominal_bandwidth_under_5dB_peak_only", nominal)]:
        if collection:
            candidate = dict(collection[0])
            identity = candidate["Kp_register"], candidate["Ki_register"]
            if identity not in chosen_keys:
                recommendations.append(dict(priority=len(recommendations) + 1,
                                            selection_reason=reason, **candidate))
                chosen_keys.add(identity)

    prediction = rejection(frequency, kp, ki, B, fp, tau)
    modeled_plant = plant(frequency, B, fp, tau)
    residuals = fit.fun
    output = dict(
        created_UTC=datetime.now(timezone.utc).isoformat(),
        analysis_kind="conditional_magnitude_only_model_guidance_not_a_measurement",
        source_file=str(SOURCE), source_sha256=hashlib.sha256(content).hexdigest(),
        source_status=source["status"], source_workpoint_label=source["workpoint_label"],
        data_points=len(rows), sample_rate_Hz=FS,
        source_controller=dict(Kp=kp, Ki_per_sample=ki),
        model=dict(plant="P(f)=B/(1+j*f/fp)*exp(-j*2*pi*f*tau)",
                   controller="C(f)=Kp+Ki/(exp(j*2*pi*f/fs)-1)",
                   sensitivity="S(f)=1/(1+C(f)*P(f))",
                   fitting="joint unweighted equal-scale dB residuals of open plant magnitude and closed/open amplitude ratio; 18 delay initializations",
                   fitted_B_ADC_per_DAC=B, fitted_fp_Hz=fp, fitted_tau_microseconds=tau,
                   joint_residual_RMSE_dB=float(np.sqrt(np.mean(residuals ** 2))),
                   worst_absolute_rejection_residual_dB=float(np.max(np.abs(prediction - measured_db))),
                   optimizer_parameter_bounds=dict(B=[.005, .2], fp_Hz=[1e4, 2e6], tau_microseconds=[0, 30])),
        fit_comparison=[dict(frequency_Hz=float(f), measured_open_gain_ADC_per_DAC=float(p),
                             modeled_open_gain_ADC_per_DAC=float(abs(model_p)),
                             measured_rejection_dB=float(s), modeled_rejection_dB=float(model_s))
                        for f, p, model_p, s, model_s in zip(frequency, measured_plant, modeled_plant, measured_db, prediction)],
        current_controller_model=evaluate(kp, ki),
        search=dict(Kp_range=[6, 12], Kp_step=.1, Ki_range=[.35, .65], Ki_step=.005,
                    prediction_grid_Hz=[float(grid[0]), float(grid[-1])],
                    actual_data_frequency_range_Hz=[trust_min, trust_max],
                    nominal_feasible_count=len(nominal), plusminus10percent_gain_feasible_count=len(robust),
                    gain_variation_is_a_design_scenario_not_a_measured_confidence_interval=True),
        recommended_candidates=recommendations,
        nominal_high_bandwidth_alternatives=nominal[:8],
        robust_high_bandwidth_alternatives=robust[:8],
        limitations=["All candidate values are predictions requiring real 10 kHz and peak-band screening.",
                     "No phase was measured or inferred as an experimental result; no phase margin is reported.",
                     "One-pole plus delay is an assumed model; the fitted delay is not an independently measured hardware latency.",
                     "Finite magnitude data can admit other model structures and delay branches.",
                     "Prediction below the lowest measured 10 kHz and between sparse high-frequency points relies on the assumed model.",
                     "Gain +/-10% is only a selected design scenario; model shape error and drift can exceed it.",
                     "No claim that the 350 mVpp workpoint outperforms 400 mVpp is made before a real matched comparison."],
    )
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(saved=str(OUTPUT), fitted_model=output["model"],
                         current=output["current_controller_model"],
                         candidates=recommendations), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
