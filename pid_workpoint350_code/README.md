# Optical intensity PI workpoint measurement code

Source-only snapshot of the laboratory control, acquisition, analysis and offline test code. Experimental waveforms, result tables, device readback snapshots and calibration data are retained locally and are not included in this addition.

The existing firmware and host dependencies are in `../pid_gain_tuning`. These scripts retain laboratory Windows paths for traceability; adapt those paths before running on a different machine. They are not a standalone installer.

Main entries:
- `configure_workpoint.py`: bounded workpoint setup with snapshots, readback checks and recovery. Without `--execute`, prints a plan only.
- `scan_workpoint_local.py`: single-tone or paired sweeps with per-point workpoint setup; records gain and peak frequency, rejecting clipping, failed readback and mismatched operating points. Without `--execute`, prints a plan only.
- `workpoint_measure.py` and `workpoint_measure_fast.py`: uniform FPGA captures and tone fitting.
- `analyze_workpoint_comparison.py`: preserved-candidate comparison.
- `verify_final_live.py`: read-only final-state verification.
- `test_*.py`: 47 offline tests passed before publication. Run `python -B -m unittest discover -s . -p "test_*.py"` after configuring the existing host dependency paths.

Model search scripts are conditional guidance, not measurements. Historical peak-cap scenarios in those scripts are not a mandatory hardware limit; subsequent optimization must report each candidate's measured bandwidth, peak height and peak frequency. Hardware bounds and capture fault checks remain enforced.
