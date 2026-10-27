"""Summarize the new centered-mapping candidates without touching hardware."""
import json
from datetime import datetime, timezone
from pathlib import Path
import analyze_workpoint_comparison as analysis

ROOT = Path(__file__).resolve().parents[1] / "outputs" / "workpoint_350mVpp_20261027"
groups = {}
for folder in sorted(ROOT.glob("local_scan_*_c[0-9][0-9]")):
    state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    if state.get("nominal_DAC_code") != 8198:
        continue
    label = state["workpoint_label"]
    groups.setdefault(label, []).append(analysis.load_session(folder))
rows = []
for label, sessions in groups.items():
    summary = analysis.summarize_group(label, sessions)
    bracket = summary["crossing"].get("crossing_bracket")
    observations = [r for r in summary["all_input_rows"] if r["valid"]]
    observed_peak = max(observations, key=lambda r: r["db"]) if observations else None
    rows.append(dict(candidate=label, gains=sessions[0]["metadata"]["tested_gains"],
        status=[s["metadata"]["source_status"] for s in sessions],
        rejection_10kHz_dB=summary["rejection_10kHz_dB"],
        largest_measured_dB=summary["largest_measured_dB"],
        largest_measured_at_Hz=summary["largest_measured_at_Hz"],
        highest_observed_including_prior_repeats_dB=observed_peak["db"] if observed_peak else None,
        highest_observed_including_prior_repeats_at_Hz=observed_peak["frequency_Hz"] if observed_peak else None,
        bandwidth_Hz=(summary["crossing"]["minus3dB_rejection_crossing_Hz"]
                      if bracket and bracket.get("fine_bracket") else None),
        crossing_bracket=bracket, valid_points=summary["valid_points"],
        invalid_points=summary["invalid_or_missing_points"],
        source_directories=[s["metadata"]["source_directory"] for s in sessions]))
result = dict(updated_utc=datetime.now(timezone.utc).isoformat(),
    current_mapping=dict(b_peak_V=.226, k_V_per_V=.0087, application_reported_by_user=True),
    old_C21_results_preserved=True, high_frequency_peak_user_preference_dB=6,
    application="Yb 556 nm 3D MOT cooling light, confirmed by user",
    peak_frequency_is_part_of_candidate_comparison=True,
    peak_height_is_not_a_universal_or_frequency_independent_acceptance_limit=True,
    measurement_kind="Injected-tone in-loop closed/open response; not absolute out-of-loop RIN",
    live_configuration_is_not_implied_by_candidate_archive=True, candidates=rows)
(ROOT / "centered_pid_catalog.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
