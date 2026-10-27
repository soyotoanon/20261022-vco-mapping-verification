"""Read-only analysis of real workpoint_measure.py sessions; no hardware imports.

Example after acquisition (repeat a label to combine its screen/fine sessions):
  python analyze_workpoint_comparison.py --session A400=PATH --session A400=PATH \
      --session B350_same_PI=PATH --session B350_tuned=PATH

No session arguments means no output files and no measurements are fabricated.
Each run creates a new comparison directory; source measurements are never edited.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = WORKSPACE / "outputs" / "workpoint_350mVpp_20261027"
HISTORICAL_C21 = Path(r"D:\codex\2026-10-23\wo\outputs\pid_gain_tuning\candidates\C21.json")
HK = timezone(timedelta(hours=8))
DEFINITION = (
    "20 log10(closed/open fundamental amplitude), in-loop ADC; bandwidth is the "
    "first upward -3 dB rejection-ratio crossing from the sampled low-frequency "
    "suppressed region, using adjacent valid points and linear interpolation in log(f)."
)


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get_register(registers, address):
    for key, value in registers.items():
        try:
            if int(key, 0) == address:
                return value
        except (TypeError, ValueError):
            continue
    return None


def point_key(row):
    requested = finite(row.get("requested_Hz"))
    actual = finite(row.get("frequency_Hz"))
    return requested if requested is not None else actual


def normalize_point(row, source, kind="measured"):
    copied = dict(row)
    frequency = finite(row.get("frequency_Hz"))
    requested = point_key(row)
    db = finite(row.get("db"))
    copied.update(requested_Hz=requested,
                  frequency_Hz=frequency if frequency is not None else requested,
                  db=db, valid=row.get("valid") is True and db is not None,
                  source_session=str(source), acquisition_kind=kind)
    if requested is None or requested <= 0:
        raise ValueError(f"Invalid/nonpositive frequency in {source}: {row!r}")
    if not copied["valid"] and not copied.get("reason"):
        copied["reason"] = "invalid or nonfinite rejection result"
    return copied


def load_session(directory):
    directory = Path(directory).resolve()
    if directory.is_file():
        directory = directory.parent
    status_path = directory / "status.json"
    candidate_path = directory / "candidate.json"
    if not status_path.is_file() and not candidate_path.is_file():
        raise FileNotFoundError(f"No real status.json/candidate.json in {directory}")
    status = load_json(status_path) if status_path.is_file() else None
    candidate = load_json(candidate_path) if candidate_path.is_file() else None
    # status.json includes cleanup outcome; do not hide it behind candidate.json.
    document = status if status is not None else candidate
    rows = [normalize_point(row, directory) for row in document.get("rows", [])]
    if document.get("target_recentered_each_point") is True:
        for row in rows:
            validation = row.get("workpoint_validation")
            if not isinstance(validation, dict) or validation.get("valid") is not True:
                row["valid"] = False
                row["reason"] = "; ".join(filter(None, [row.get("reason"),
                    "Pointwise hold-code validation missing or failed"]))
    by_key = {point_key(row): row for row in rows}
    for error in document.get("point_errors", []):
        key = finite(error.get("frequency_Hz"))
        if key is not None and key not in by_key:
            point = normalize_point(dict(
                requested_Hz=key, frequency_Hz=key, db=None, valid=False,
                reason=error.get("error", "acquisition failed"),
                raw_directory=error.get("raw_directory")), directory, "acquisition_error")
            rows.append(point)
            by_key[key] = point
    for key in document.get("frequencies_Hz", []):
        key = finite(key)
        if key is not None and key not in by_key:
            point = normalize_point(dict(requested_Hz=key, frequency_Hz=key,
                db=None, valid=False, reason="Requested point not acquired"),
                directory, "not_acquired")
            rows.append(point)
            by_key[key] = point
    snapshot = document.get("before_snapshot", {})
    snapshot_registers = snapshot.get("registers", snapshot) if isinstance(snapshot, dict) else {}
    registers = document.get("tested_registers") or snapshot_registers
    gains = document.get("tested_gains", {})
    metadata = dict(
        source_directory=str(directory), source_status=document.get("status"),
        created_utc=document.get("created_utc"), finished_utc=document.get("finished_utc"),
        workpoint_label=document.get("workpoint_label"),
        workpoint_label_is_unverified_metadata=document.get("workpoint_label_is_unverified_metadata", True),
        tested_gains=gains, tested_registers=registers,
        firmware_register=get_register(registers, 0x3F),
        adc_setpoint=get_register(registers, 0x18), dac_bias=get_register(registers, 0x19),
        nominal_DAC_code=document.get("nominal_DAC_code"),
        raw_sample_rate_Hz=document.get("raw_sample_rate_Hz"),
        amplitude_DAC_peak_codes=document.get("amplitude_DAC_peak_codes"),
        target_recentered_each_point=document.get("target_recentered_each_point") is True,
        measurement_protocol=("pointwise_recentered_local_rejection" if document.get("target_recentered_each_point") is True
                              else "sequential_scan_exploratory_drift_diagnostic"),
        before_snapshot=snapshot,
        workpoint_control_metadata={key: value for key, value in document.items()
            if any(token in key.lower() for token in ("hold", "workpoint", "bias", "recenter", "dac_code", "tolerance"))},
        restoration=document.get("restoration"),
        error=document.get("error"), cleanup_error=document.get("cleanup_error"),
        source_files=[dict(path=str(p), sha256=digest(p)) for p in (status_path, candidate_path) if p.is_file()],
    )
    return dict(metadata=metadata, rows=rows)


def crossing_summary(points, low=12000.0, high=20000.0):
    """Never bridge invalid/missing points; distinguish local and first crossings."""
    points = sorted(points, key=point_key)
    local = []
    lowest_suppressed = bool(points and points[0]["valid"] and points[0]["db"] < -3)
    prefix_valid = lowest_suppressed
    for index in range(1, len(points)):
        left, right = points[index-1:index+1]
        if not left["valid"] or not right["valid"]:
            prefix_valid = False
            continue
        f1, f2 = left["frequency_Hz"], right["frequency_Hz"]
        d1, d2 = left["db"], right["db"]
        if d1 < -3 <= d2 and f1 < f2:
            fraction = (-3-d1)/(d2-d1)
            frequency = math.exp(math.log(f1)+fraction*(math.log(f2)-math.log(f1)))
            local.append(dict(frequency_Hz=frequency,
                bracket_Hz=[f1, f2], bracket_dB=[d1, d2],
                bracket_frequency_ratio=f2/f1,
                fine_bracket=f2/f1 <= 1.25,
                source_sessions=[left["source_session"], right["source_session"]],
                no_invalid_or_missing_lower_sampled_points=prefix_valid,
                in_requested_12_to_20k_region=low <= frequency <= high))
        # Once an upward crossing is found, later crossings are not the first.
        if local:
            prefix_valid = False
    first = local[0] if local and local[0]["no_invalid_or_missing_lower_sampled_points"] else None
    if first:
        reason = "Adjacent valid measured points bracket the first sampled upward crossing"
    elif local:
        reason = "Local bracket exists, but lower missing/invalid points or no suppressed starting point prevent a first-crossing claim"
    elif not lowest_suppressed:
        reason = "The lowest requested point does not establish a valid response below -3 dB"
    else:
        reason = "No adjacent valid measured points bracket an upward -3 dB crossing"
    return dict(definition=DEFINITION, interpolation="linear dB versus log(frequency)",
        minus3dB_rejection_crossing_Hz=first["frequency_Hz"] if first else None,
        minus3dB_rejection_crossing_12_to_20k_Hz=(first["frequency_Hz"] if first and low <= first["frequency_Hz"] <= high else None),
        crossing_bracket=first, local_valid_brackets=local, reason=reason,
        limitation="Unmeasured narrow features between valid points remain possible")


def fingerprint(metadata):
    registers = metadata["tested_registers"]
    return (metadata["tested_gains"].get("kp"), metadata["tested_gains"].get("ki"),
            metadata.get("raw_sample_rate_Hz"),
            metadata["target_recentered_each_point"],
            metadata.get("nominal_DAC_code"),
            *(get_register(registers, address) for address in
              ((0x19, 0x1A, 0x1B, 0x1E, 0x1F, 0x3F) if metadata["target_recentered_each_point"]
               else (0x18, 0x19, 0x1A, 0x1B, 0x1E, 0x1F, 0x3F))))


def summarize_group(label, sessions, low=12000.0, high=20000.0):
    configurations = {fingerprint(session["metadata"]) for session in sessions if session["rows"]}
    if len(configurations) > 1:
        raise ValueError(f"{label}: grouped sessions have different gains/targets/bias/limits/firmware; give them separate labels")
    selected = {}
    all_rows = []
    duplicates = []
    for session in sessions:
        for row in session["rows"]:
            key = point_key(row)
            all_rows.append(row)
            if key in selected:
                duplicates.append(dict(requested_Hz=key,
                    earlier_source=selected[key]["source_session"], later_source=row["source_session"],
                    policy="later CLI input wins; all original records retained"))
            selected[key] = row
    points = [selected[key] for key in sorted(selected)]
    valid = [p for p in points if p["valid"]]
    peak = max(valid, key=lambda point: point["db"]) if valid else None
    ten = next((p for p in points if abs(p["requested_Hz"]-10000) < 0.01), None)
    recentered = bool(sessions and all(s["metadata"]["target_recentered_each_point"] for s in sessions))
    holds = [finite(point.get("captures", {}).get("open", {}).get(key))
        for point in points for key in ("hold_DAC_code_before", "hold_DAC_code_after")]
    holds = [value for value in holds if value is not None]
    fault_rows = []
    for point in points:
        for capture_name in ("open", "closed"):
            state = point.get("captures", {}).get(capture_name, {}).get("capture_status")
            if isinstance(state, int) and state & 12:
                fault_rows.append(dict(requested_Hz=point["requested_Hz"], capture=capture_name,
                    raw_capture_status=state, fault_mask=state & 12,
                    injection_clipped=bool(state & 4), capture_fault_seen=bool(state & 8)))
    crossing = crossing_summary(points, low, high)
    if not recentered:
        crossing["exploratory_crossing_Hz"] = crossing["minus3dB_rejection_crossing_Hz"]
        crossing["minus3dB_rejection_crossing_Hz"] = None
        crossing["minus3dB_rejection_crossing_12_to_20k_Hz"] = None
        crossing["reason"] = ("Sequential scans are retained for drift diagnostics; "
                              "not used to claim a fixed-workpoint bandwidth. " + crossing["reason"])
    return dict(label=label, status="measured" if valid else "no_valid_measured_points",
        target_recentered_each_point=recentered,
        inference_role="local_rejection_comparison" if recentered else "exploratory_drift_diagnostic",
        open_hold_DAC_code_range=[min(holds), max(holds)] if holds else None,
        capture_faults=fault_rows,
        temporal_scope=("Target recentered at each frequency; local disturbance rejection only, "
                        "not evidence of long-term optical-power stability improvement" if recentered else
                        "Fixed-target sequential scan may change actual output/workpoint over time"),
        sources=[session["metadata"] for session in sessions],
        definition=DEFINITION, valid_points=len(valid), invalid_or_missing_points=len(points)-len(valid),
        rejection_10kHz_dB=ten["db"] if ten and ten["valid"] else None,
        largest_measured_dB=peak["db"] if peak else None,
        largest_measured_at_Hz=peak["frequency_Hz"] if peak else None,
        peak_is_amplification=bool(peak and peak["db"] > 0),
        peak_definition="maximum among valid measured points, including negative values if none amplify",
        crossing=crossing,
        rows=points, all_input_rows=all_rows, duplicate_frequency_resolution=duplicates,
        disposition="retained_candidate_regardless_of_peak_or_invalid_points")


def historical_reference():
    if not HISTORICAL_C21.is_file():
        return dict(available=False, source=str(HISTORICAL_C21))
    result = load_json(HISTORICAL_C21)
    return dict(available=True, source=str(HISTORICAL_C21), sha256=digest(HISTORICAL_C21),
        historical_date="2026-10-23", role="historical only; not today's baseline and not plotted as today's data",
        **{key: result.get(key) for key in ("id", "Kp", "Ki_per_sample", "sample_rate_Hz", "firmware_id",
            "bandwidth_Hz", "rejection_10kHz_dB", "largest_measured_dB", "largest_measured_at_Hz")})


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def number(value, unit="", scale=1):
    return "—" if value is None else f"{value/scale:.3f}{unit}"


def plot_comparison(groups, out, low, high):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "font.size": 10})
    figure, axes = plt.subplots(1, 2, figsize=(13.4, 5.4), constrained_layout=True)
    for group_index, group in enumerate(groups):
        color = f"C{group_index % 10}"
        xs = [p["frequency_Hz"]/1000 for p in group["rows"]]
        ys = [p["db"] if p["valid"] else float("nan") for p in group["rows"]]
        for axis in axes:
            label = group["label"] + (" [exploratory]" if not group["target_recentered_each_point"] else "")
            axis.plot(xs, ys, "o-", color=color, label=label, linewidth=1.25, markersize=4)
            invalid_with_y = [p for p in group["rows"] if not p["valid"] and p["db"] is not None]
            axis.scatter([p["frequency_Hz"]/1000 for p in invalid_with_y],
                         [p["db"] for p in invalid_with_y], color=color, marker="x", s=55, zorder=5)
            absent = [p for p in group["rows"] if not p["valid"] and p["db"] is None]
            axis.scatter([p["frequency_Hz"]/1000 for p in absent], [0.035]*len(absent),
                         transform=axis.get_xaxis_transform(), color=color, marker="v", s=42, zorder=5)
            crossing = group["crossing"]["minus3dB_rejection_crossing_Hz"]
            if crossing is not None:
                axis.scatter([crossing/1000], [-3], marker="D", s=35,
                             facecolor="white", edgecolor=color, zorder=6)
    for axis in axes:
        axis.axhline(0, color="#666666", linewidth=.8)
        axis.axhline(-3, color="#666666", linestyle="--", linewidth=.9)
        axis.set_xlabel("Modulation frequency (kHz)")
        axis.set_ylabel("20 log10(closed/open amplitude) (dB)")
        axis.grid(True, which="both", alpha=.25)
    axes[0].set_xscale("log")
    axes[0].set_title("Today's measured rejection; lines guide the eye")
    axes[0].legend(fontsize=9)
    axes[1].set_xlim(low/1000, high/1000)
    axes[1].set_title("Crossing region; no interpolation across invalid points")
    figure.suptitle("Local disturbance rejection — in-loop FPGA ADC", fontsize=13)
    figure.supxlabel("o valid measurement · x invalid result · bottom v missing/no result · hollow diamond interpolated -3 dB crossing", fontsize=9)
    for suffix in ("png", "svg"):
        figure.savefig(out / f"workpoint_comparison.{suffix}", dpi=190)
    plt.close(figure)


def write_report(report, directory):
    lines = ["# 工作点反馈频响比较", "", f"生成时间：{report['generated_local']}。", "",
        "单频响应为 `20 log10(闭环/开环基频幅度)`，负值表示抑制，正值表示放大。"
        "−3 dB 指随频率升高，从已有低频抑制区首次回升到 −3 dB 的交点；"
        "仅在相邻有效测点间按对数频率插值，不跨越无效或缺测点。", "",
        "| 今日测量标签 | 测量协议 | 有效/无效或缺测 | 10 kHz (dB) | 已测最大值 (dB) | 对应频率 (kHz) | −3 dB 交点 (kHz) |", 
        "|---|---|---:|---:|---:|---:|---:|"]
    for group in report["groups"]:
        protocol = "逐频点重设目标" if group["target_recentered_each_point"] else "旧顺序扫描/漂移诊断"
        lines.append(f"| {group['label']} | {protocol} | {group['valid_points']}/{group['invalid_or_missing_points']} | "
            f"{number(group['rejection_10kHz_dB'])} | {number(group['largest_measured_dB'])} | "
            f"{number(group['largest_measured_at_Hz'], scale=1000)} | "
            f"{number(group['crossing']['minus3dB_rejection_crossing_Hz'], scale=1000)} |")
    lines += ["", "![今日实测比较](workpoint_comparison.png)", "",
        "图中的圆点是有效测点，叉号是有数值但判为无效的点，图底部三角标记表示没有有效数值；"
        "折线只作阅读辅助，遇无效或缺测点断开。空心菱形表示有效相邻测点间的插值交点。"
        "最高放大只取已测频点最大值，未保证捕捉所有窄峰。", "",
        "RF 工作点标签来自采集元数据；没有现场 RF 电平核对的标签不等于实测 RF 幅度，"
        "也不能直接沿用旧效率。这里没有用历史 C21 替代今天的 A 基准。", "",
        "**逐频点重设目标的测试只比较该工作点附近的局部扰动抑制。每个频率都会重新设置 DAC 偏置并采集对应 ADC 目标，"
        "不要求各频点长期锁在同一光功率，也不能据此证明长期稳功率性能改善。旧固定目标的顺序扫描另列为探索/漂移诊断，"
        "不用于声称固定 350/400 mVpp 工作点的精确带宽。**", ""]
    if not any(group["crossing"]["minus3dB_rejection_crossing_Hz"] is not None for group in report["groups"]):
        lines += ["本轮没有取得满足连续有效测点条件的 −3 dB 交点，不能据此证明已超过历史约 13.51 kHz。"
            "孤立频点仍有价值，但不能跨越无效点或缺测点拼出一条带宽曲线。", ""]
    lines += ["限幅核查采用采集状态中的当前故障记录：`status & 4` 表示注入触限，"
        "`status & 8` 表示采集期间出现环路故障条件；`status & 12` 同时检查两者。"
        "例如原始状态 6、14 含采集完成位 2，其故障掩码分别为 4、12。"
        "故障位不只反映采集前后两个瞬时输出码，因此不能用端点未触限来否定采集期间的触限记录。", ""]
    for group in report["groups"]:
        lines += [f"## {group['label']}", "", f"交点判定：{group['crossing']['reason']}。", ""]
        bracket = group["crossing"].get("crossing_bracket")
        if bracket:
            bounds = bracket["bracket_Hz"]
            lines += [f"交点夹在 {bounds[0]/1000:.3f} 与 {bounds[1]/1000:.3f} kHz 两个测点之间。"
                + ("该夹区较宽，交点仅作粗略插值，应在附近补测。" if not bracket["fine_bracket"] else "") , ""]
        if group.get("open_hold_DAC_code_range"):
            bounds = group["open_hold_DAC_code_range"]
            lines += [f"已采开环保持码范围：{bounds[0]:g}–{bounds[1]:g}。", ""]
        for source in group["sources"]:
            directory_source = Path(source["source_directory"])
            gains = source.get("tested_gains", {})
            lines.append(f"- [原始测量目录]({directory_source.as_posix()})；状态 `{source['source_status']}`；"
                f"Kp={gains.get('kp', '未知')}，Ki={gains.get('ki', '未知')}/采样；"
                f"目标 ADC={source.get('adc_setpoint')}，DAC 偏置={source.get('dac_bias')}。")
            if source.get("restoration") is not None:
                lines.append(f"- 参数恢复记录：`{json.dumps(source['restoration'], ensure_ascii=False)}`。")
        if group["target_recentered_each_point"]:
            lines += ["", "逐点目标与开环保持码（RF 值仍按旧映射估算）：", "",
                "| 频率 (kHz) | 目标 ADC | 名义 DAC | 开环保持码：采集前/后 | 响应 (dB) | 闭环采集状态/故障掩码 | 综合有效 | 原始目录 |",
                "|---:|---:|---:|---|---:|---|---|---|"]
            for point in group["rows"]:
                capture = point.get("captures", {}).get("open", {})
                before = capture.get("hold_DAC_code_before")
                after = capture.get("hold_DAC_code_after")
                validation = point.get("workpoint_validation", {})
                closed_state = point.get("captures", {}).get("closed", {}).get("capture_status")
                fault_mask = closed_state & 12 if isinstance(closed_state, int) else None
                path = point.get("measurement_directory") or point.get("raw_directory")
                raw_link = f"[采样]({Path(path).as_posix()})" if path else "—"
                lines.append(f"| {point['requested_Hz']/1000:g} | {point.get('target_ADC', '—')} | "
                    f"{point.get('nominal_DAC_code', '—')} | {before}/{after} | "
                    f"{number(point['db'])} | {closed_state}/{fault_mask} | "
                    f"{'有效' if point['valid'] else '无效或缺测'} | {raw_link} |")
        invalid = [point for point in group["rows"] if not point["valid"]]
        if invalid:
            lines += ["", "无效或缺测点：", ""]
            lines += [f"- {point['requested_Hz']/1000:g} kHz：{point.get('reason', '')}。" for point in invalid]
        if group["duplicate_frequency_resolution"]:
            lines += ["", "同标签有重复频点：采用命令行中较后的输入；全部原记录及来源仍保存在 JSON，未平均或删除。"]
        lines += [""]
    historical = report["historical_C21"]
    exploratory = report.get("exploratory_drift_diagnostics", [])
    if exploratory:
        lines += ["## 此前顺序扫描（探索与漂移诊断）", "",
            "以下数据完整保留，但未逐频点重设工作点，不进入上方主表或主图，也不据此给出固定工作点带宽。"
            "正式逐点重设后的数字不必复现这些值；采样时点、工作点重设及漂移控制均已改变。", ""]
        for group in exploratory:
            lines += [f"- {group['label']}：10 kHz {number(group['rejection_10kHz_dB'], ' dB')}；"
                f"已测最大 {number(group['largest_measured_dB'], ' dB')}。"]
            for source in group["sources"]:
                lines += [f"  原始目录：[{Path(source['source_directory']).name}]({Path(source['source_directory']).as_posix()})。"]
        lines += [""]
    lines += ["## 历史 C21（独立参考）", ""]
    if historical["available"]:
        lines += [f"2026-10-23 档案：Kp={historical['Kp']}、Ki={historical['Ki_per_sample']}/采样，"
            f"10 kHz {historical['rejection_10kHz_dB']:.3f} dB，"
            f"−3 dB 交点 {historical['bandwidth_Hz']/1000:.3f} kHz，"
            f"已测最大值 {historical['largest_measured_dB']:.3f} dB "
            f"@{historical['largest_measured_at_Hz']/1000:.3f} kHz。"
            "该记录不是今天复测结果，也不代表当前板上配置。"]
    else:
        lines += ["历史 C21 档案当前不可读；未据记忆填入数字。"]
    lines += ["", "后续测量先要求 350 附近的 10 kHz 单点试测通过：开闭环采集无当前故障或注入触限、"
        "工作点保持码核查通过、均值与拟合有效，再检查高频峰区，最后补连续交点测点。"
        "若 10 kHz 试测仍不通过，应先处理工作余量或稳定性，不继续用失败结果拼接带宽。", "",
        "所有候选均保留，包括峰值较高、失败或含无效点的候选；本报告不自动选择最终运行配置。", ""]
    (directory / "工作点比较报告.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="append", default=[], metavar="LABEL=DIR")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--cross-low", type=float, default=12000)
    parser.add_argument("--cross-high", type=float, default=20000)
    args = parser.parse_args(argv)
    if not args.session:
        parser.error("Supply actual measurement directories with --session LABEL=DIR; no data fabricated")
    if not 0 < args.cross_low < args.cross_high:
        parser.error("cross-low/cross-high must be positive and increasing")
    grouped = {}
    for specification in args.session:
        label, separator, path = specification.partition("=")
        if not separator or not label.strip() or not path.strip():
            parser.error("Each --session must be LABEL=DIR")
        session = load_session(path)
        if session["metadata"]["source_status"] in {"running", "preflight", "configuring", "measuring"}:
            parser.error(f"Session is still active; wait for its final status before comparison: {path}")
        grouped.setdefault(label.strip(), []).append(session)
    all_groups = [summarize_group(label, sessions, args.cross_low, args.cross_high)
                  for label, sessions in grouped.items()]
    groups = [group for group in all_groups if group["target_recentered_each_point"]]
    exploratory = [group for group in all_groups if not group["target_recentered_each_point"]]
    if not groups:
        parser.error("No pointwise-recentered local_scan sessions supplied; sequential scans cannot establish the fixed-workpoint comparison")
    output_root = args.output_root.resolve()
    output_root.relative_to(OUTPUT_ROOT.resolve())
    directory = output_root / ("comparison_"+datetime.now(HK).strftime("%Y%m%d_%H%M%S_%f"))
    directory.mkdir(parents=True, exist_ok=False)
    report = dict(generated_local=datetime.now(HK).isoformat(), output_directory=str(directory),
        definition=DEFINITION, groups=groups, exploratory_drift_diagnostics=exploratory,
        historical_C21=historical_reference(),
        policy="read-only inputs; every candidate retained; no hardware access or automatic candidate selection")
    save_json(directory / "comparison.json", report)
    columns = ["label", "valid_points", "invalid_or_missing_points", "rejection_10kHz_dB",
               "largest_measured_dB", "largest_measured_at_Hz", "minus3dB_rejection_crossing_Hz"]
    with (directory / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for group in groups:
            row = {key: group.get(key) for key in columns}
            row["minus3dB_rejection_crossing_Hz"] = group["crossing"]["minus3dB_rejection_crossing_Hz"]
            writer.writerow(row)
    plot_comparison(groups, directory, args.cross_low, args.cross_high)
    write_report(report, directory)
    print(json.dumps({"output_directory": str(directory), "groups": [
        {"label": g["label"], "rejection_10kHz_dB": g["rejection_10kHz_dB"],
         "largest_measured_dB": g["largest_measured_dB"],
         "minus3dB_rejection_crossing_Hz": g["crossing"]["minus3dB_rejection_crossing_Hz"]}
        for g in groups]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
