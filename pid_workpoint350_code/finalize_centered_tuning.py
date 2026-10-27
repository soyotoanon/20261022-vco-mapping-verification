"""Create a traceable tuning report from completed physical runs and live readback."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import analyze_workpoint_comparison as analysis

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'workpoint_350mVpp_20261027'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fmt(value, scale=1, digits=2):
    return '未细测' if value is None else f'{value/scale:.{digits}f}'


def gather():
    sessions = {}
    for folder in sorted(OUT.glob('local_scan_*_c[0-9][0-9]')):
        state = read(folder / 'status.json')
        if state.get('nominal_DAC_code') != 8198:
            continue
        if state['status'] != 'complete':
            raise RuntimeError(f'Incomplete centered candidate: {folder.name}: {state["status"]}')
        sessions.setdefault(state['workpoint_label'], []).append(analysis.load_session(folder))
    groups = []
    for label, inputs in sessions.items():
        group = analysis.summarize_group(label, inputs)
        bracket = group['crossing'].get('crossing_bracket')
        group['fine_bandwidth_Hz'] = (bracket['frequency_Hz'] if bracket and bracket['fine_bracket'] else None)
        group['gains'] = inputs[0]['metadata']['tested_gains']
        group['all_valid_observations'] = [row for item in inputs for row in item['rows'] if row['valid']]
        peak = max(group['all_valid_observations'], key=lambda row: row['db'])
        group['highest_observed_peak_dB'] = peak['db']
        group['highest_observed_peak_at_Hz'] = peak['frequency_Hz']
        group['source_status_sha256'] = {str(p['metadata']['source_directory']): sha(Path(p['metadata']['source_directory']) / 'status.json') for p in inputs}
        groups.append(group)
    return groups


def plot(groups, selected, historical, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
                         'axes.unicode_minus': False, 'font.size': 10})
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    colors = {'c03': '#bb4554', 'c04': '#7a8a96', 'c06': '#e49b36', 'c07': '#157c81'}
    for group in groups:
        if group['label'] not in {'c03', 'c04', 'c06', 'c07', selected}:
            continue
        rows = group['rows']
        x = np.array([r['frequency_Hz']/1000 for r in rows])
        y = np.array([r['db'] if r['valid'] else np.nan for r in rows])
        gains = group['gains']
        name = f"{group['label'].upper()}  P={gains['kp']:g}, I={gains['ki']:.3f}"
        width = 2.4 if group['label'] == selected else 1.4
        for ax in axes:
            ax.plot(x, y, 'o-', ms=4, lw=width, color=colors.get(group['label']), label=name)
            observations = group['all_valid_observations']
            ax.scatter([r['frequency_Hz']/1000 for r in observations],
                       [r['db'] for r in observations], s=25, facecolors='none',
                       edgecolors=colors.get(group['label']), alpha=.45, linewidths=.7)
        px = group['highest_observed_peak_at_Hz']/1000
        py = group['highest_observed_peak_dB']
        annotation_y = {'c03': 9.5, 'c04': 4.7, 'c06': 8.5, 'c07': 7.4}
        axes[1].annotate(f'{group["label"].upper()}: {py:+.2f} dB @ {px:.0f} kHz',
                         (px, py), xytext=(92, annotation_y.get(group['label'], 9)),
                         textcoords='data', fontsize=8, ha='left',
                         bbox=dict(facecolor='white', edgecolor='none', alpha=.85),
                         arrowprops=dict(arrowstyle='-', color=colors.get(group['label']), lw=.8))
    for ax in axes:
        ax.axhline(0, color='#aaaaaa', lw=.8)
        ax.axhline(-3, color='#5d6670', ls='--', lw=1)
        ax.axhline(6, color='#b8605d', ls=':', lw=1)
        ax.grid(True, alpha=.22)
        ax.set_xlabel('调制频率 / kHz')
        ax.set_ylabel('闭环 / 开环扰动幅度 / dB')
    axes[0].set(xlim=(10, 20), ylim=(-8, 3), title='带宽附近：相邻有效点插值')
    axes[1].set(xlim=(30, 210), ylim=(-5, 11), title='高频：同时比较峰高与峰频')
    axes[0].axvline(historical['bandwidth_Hz']/1000, color='#444444', ls=':', lw=1,
                    label=f"历史C21交点 {historical['bandwidth_Hz']/1000:.2f} kHz")
    axes[0].legend(fontsize=8, loc='lower right')
    axes[1].legend(fontsize=8, loc='upper right')
    fig.suptitle('350 mVpp 名义工作点：PI 候选的实测扰动抑制')
    fig.supxlabel('实心连线为较后测点；空心圆保留重复观测。逐频点回到DAC8198并重测目标；不同时间有漂移。', fontsize=9)
    fig.savefig(directory/'反馈频响比较.png', dpi=180)
    fig.savefig(directory/'反馈频响比较.svg')
    plt.close(fig)


def package_code(directory):
    names = ['configure_workpoint.py', 'workpoint_measure.py', 'workpoint_measure_fast.py',
             'scan_workpoint_local.py', 'probe_workpoint_devices.py', 'quiesce_probe.py',
             'analyze_workpoint_comparison.py', 'centered_tuning_catalog.py',
             'fit_model_candidates.py', 'centered_model_frontier.py',
             'test_configure_workpoint.py', 'test_workpoint_measure.py',
             'test_workpoint_measure_fast.py', 'test_scan_workpoint_local.py',
             'finalize_centered_tuning.py', 'verify_final_live.py']
    code = directory/'code'
    code.mkdir()
    manifest = []
    for name in names:
        source, target = ROOT/'work'/name, code/name
        shutil.copy2(source, target)
        if sha(source) != sha(target):
            raise RuntimeError(f'Code copy mismatch: {name}')
        manifest.append(dict(name=name, sha256=sha(target), source=str(source), path=str(target)))
    save(directory/'代码校验.json', manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selected', required=True)
    parser.add_argument('--final-setup', type=Path, required=True)
    parser.add_argument('--final-snapshot', type=Path, required=True)
    parser.add_argument('--long-validation', type=Path, required=True)
    parser.add_argument('--live-check', type=Path, required=True)
    args = parser.parse_args()
    for key in ('final_setup', 'final_snapshot', 'long_validation', 'live_check'):
        setattr(args, key, getattr(args, key).resolve())
    groups = gather()
    selected = next(g for g in groups if g['label'] == args.selected)
    config = read(args.final_setup/'result.json')
    snapshot = read(args.final_snapshot)
    registers = snapshot['fpga']['registers']
    gains = selected['gains']
    expected = {'0x00': 1, '0x19': 8198, '0x1E': 1400, '0x1F': 15000,
                '0x1A': 0x8000 | round(gains['kp']*256),
                '0x1B': 0x8000 | round(gains['ki']*32768),
                '0x2E': 1, '0x30': 0, '0x31': 0, '0x11': 4}
    if not config.get('success') or snapshot['errors']:
        raise RuntimeError('Final setup or readback did not pass')
    if snapshot['fpga']['firmware_hex'] != '0xA624' or registers['0x01'] & 0x3D:
        raise RuntimeError('Wrong firmware or current blocking status flags')
    for key, value in expected.items():
        if registers[key] != value:
            raise RuntimeError(f'Live register mismatch {key}: {registers[key]} != {value}')
    long_run = read(args.long_validation/'status.json')
    if long_run['status'] != 'complete' or long_run['tested_gains'] != gains:
        raise RuntimeError('Long validation incomplete or different gains')
    if not all(r['valid'] for r in long_run['rows']):
        raise RuntimeError('Long validation includes invalid measurement')
    for row in long_run['rows']:
        for capture in row['captures'].values():
            if read(capture['metadata_file'])['returned_samples'] != 8192:
                raise RuntimeError('Long validation did not return 8192 consecutive samples')
    live_check = read(args.live_check)
    if not live_check.get('pass') or len(live_check.get('samples', [])) != 31:
        raise RuntimeError('Final 30-second live check did not pass')
    if any(live_check['final_registers'][key] != value for key,value in expected.items()):
        raise RuntimeError('Live check candidate differs from final register snapshot')
    historical = analysis.historical_reference()
    integrity = read(ROOT/'outputs'/'baseline_backup_20261027'/'post_tuning_integrity.json')
    if not historical['available'] or not integrity['pass']:
        raise RuntimeError('Historical reference or preservation check failed')
    directory = OUT / ('final_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    directory.mkdir()
    records = []
    for group in groups:
        records.append(dict(candidate=group['label'], Kp=group['gains']['kp'],
            Ki_per_sample=group['gains']['ki'], rejection_10kHz_dB=group['rejection_10kHz_dB'],
            bandwidth_Hz=group['fine_bandwidth_Hz'],
            largest_measured_dB=group['highest_observed_peak_dB'],
            largest_measured_at_Hz=group['highest_observed_peak_at_Hz'],
            live_on_board=group['label']==args.selected))
    with (directory/'候选比较.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    report = dict(created_utc=datetime.now(timezone.utc).isoformat(), selected_candidate=args.selected,
        definition=analysis.DEFINITION, candidates=records, full_groups=groups,
        historical_C21=historical, mapping=dict(b=.226, k=.0087, channel=8, carrier_MHz=100,
            nominal_DAC_code=8198, RF_mVpp_prediction=350.07547195151136,
            RF_is_not_newly_measured=True, diffraction_efficiency_not_newly_measured=True),
        final_setup=str(args.final_setup), final_snapshot=str(args.final_snapshot),
        final_30second_live_check=str(args.live_check),
        final_registers=registers, long_validation=str(args.long_validation),
        long_validation_rows=long_run['rows'], original_preservation=integrity,
        no_out_of_loop_RIN_or_MOT_temperature_acceptance_claimed=True)
    save(directory/'调参结果.json', report)
    shutil.copy2(args.final_snapshot, directory/'最终板上回读.json')
    shutil.copy2(args.final_setup/'result.json', directory/'最终配置执行记录.json')
    shutil.copy2(args.live_check, directory/'最终30秒运行核对.json')
    shutil.copy2(OUT/'556nm_MOT与伺服峰文献对照.md', directory/'556nm_MOT与伺服峰文献对照.md')
    plot(groups, args.selected, historical, directory)
    package_code(directory)
    lines = ['# 350 mVpp 居中工作点的 PI 调参结果', '',
        f"当前留板候选 **{args.selected.upper()}**，Kp={gains['kp']:g}，Ki={gains['ki']:.9f}/采样，采样率 5 MHz。",
        f"最新合并测点的 −3 dB 扰动抑制交点为 **{fmt(selected['fine_bandwidth_Hz'],1000)} kHz**。",
        f"该候选所有保留的有效观测中，最高已测放大为 **{selected['highest_observed_peak_dB']:+.2f} dB @ {selected['highest_observed_peak_at_Hz']/1000:.1f} kHz**。", '',
        '| 候选 | Kp | Ki/采样 | 10 kHz / dB | −3 dB交点 / kHz | 最高已测 / dB | 峰频 / kHz |',
        '|---|---:|---:|---:|---:|---:|---:|']
    lines.append(f"| 历史C21（2026-10-23） | {historical['Kp']:g} | {historical['Ki_per_sample']:g} | {historical['rejection_10kHz_dB']:.2f} | {historical['bandwidth_Hz']/1000:.2f} | +{historical['largest_measured_dB']:.2f} | {historical['largest_measured_at_Hz']/1000:.1f} |")
    for row in records:
        lines.append(f"| {row['candidate'].upper()}{'（留板）' if row['live_on_board'] else ''} | {row['Kp']:g} | {row['Ki_per_sample']:.5f} | {fmt(row['rejection_10kHz_dB'])} | {fmt(row['bandwidth_Hz'],1000)} | +{row['largest_measured_dB']:.2f} | {row['largest_measured_at_Hz']/1000:.1f} |")
    lines += ['', '![实测频响](反馈频响比较.png)', '',
        '表中只给相邻有效测点夹出的细扫交点；未细测的候选不从相隔很远的两个点估出精确带宽。最高放大取全部保留的有效观测最大值，重复测量后的较低值不会抹去以前的较高值。合并曲线对重复频点采用较后记录；JSON 保留每次观测和来源。', '',
        '## 长记录复核', '',
        f"原始目录：[{args.long_validation.name}]({args.long_validation.as_posix()})。每条返回 8192 个连续采样，FPGA 当前触限和故障掩码检查保持启用。", '',
        '| 频率 / kHz | 闭环/开环 / dB | 有效 |', '|---:|---:|---|']
    for row in long_run['rows']:
        lines.append(f"| {row['frequency_Hz']/1000:g} | {row['db']:+.3f} | 是 |")
    lines += ['', '## 当前配置与恢复', '',
        f"DDS 第8路由用户手动设置为 A(V)=0.226+0.0087V，100 MHz。DAC 偏置8198、输出范围1400–15000；最终重新采集的 ADC 目标为 {registers['0x18']}，回读瞬时输出 {registers['0x05']}。350.075 mVpp 是旧标定外推的新映射预测，未冒充此次现场 RF 或衍射效率实测。", '',
        f"最终回读：CTRL={registers['0x00']}、ARM={registers['0x2E']}、扰动控制={registers['0x30']}、扰动幅度={registers['0x31']}。STATUS={registers['0x01']} 的旧 OTR 锁存位单独保留；当前采集故障和触限仍逐次严格检查。", '',
        'DAC8198是建立目标时的居中基准；闭环启用后实际DAC输出会补偿入射光功率变化，因此不会一直固定在8198，也不代表RF幅度在闭环期间始终恰好350mVpp。最终30秒核对记录已保存，检查通过。', '',
        '需要重新应用当前工作点时，先保持同一 DDS 手动映射，运行随附代码中的在线配置命令；它会先保存板上配置，按受限步幅移动 DAC，并重新测量光功率对应的目标。不要把今天的 ADC 目标当成今后光路变化后的固定标定。', '',
        '```powershell',
        f"& 'D:\\mamba\\python.exe' -B '.\\code\\configure_workpoint.py' --execute --dac-code 8198 --minimum 1400 --kp {gains['kp']:g} --ki {gains['ki']:.9f} --label restore350 --allow-latched-otr --arm",
        '```', '', '以上命令应在本报告目录运行；硬件驱动仍使用保留在原位置的实验依赖。A624 当前运行于 FPGA RAM，Flash 未被本轮重写。', '',
        '## 如何解释结果', '',
        '这些数据表征工作点附近注入扰动的抑制，不是开环单位增益交越频率、直接测得的相位裕度、独立探测器环外RIN或MOT温度。逐频点回到DAC8198并重测ADC目标，控制工作点漂移；因此也未据此声称长期稳功率性能提高。', '',
        '历史C21来自不同日期，且注入幅度与采样长度存在差别。今天各轮也有增益和光功率漂移。小幅带宽差异只作为当前实测候选比较，不据此把全部变化归因于350mVpp工作点或宣称MOT装载率/温度已经改善。', '',
        '对556nm三维MOT，高频峰要同时看频率、宽度、绝对强度噪声和原子响应。约6dB不是全球统一标准。已另附[文献对照](556nm_MOT与伺服峰文献对照.md)，列出代表性课题组的不同取舍。', '',
        '## 旧结果保护', '',
        f"原3200个文件在调参后的逐文件SHA-256、大小和修改时间复核均一致；备份ZIP哈希仍为 `{integrity['archive_sha256']}`。旧C21和所有新旧候选均保留。备份与复核：[baseline_backup_20261027]({(ROOT/'outputs'/'baseline_backup_20261027').as_posix()})。", '',
        '完整数据、每轮设置快照、写入审计、原始波形及失败探索记录都保存在本报告上级实验目录；主JSON列出本轮居中映射的全部来源与哈希。', '']
    (directory/'调参结果.md').write_text('\n'.join(lines), encoding='utf-8')
    save(OUT/'latest_result.json', dict(directory=str(directory), selected_candidate=args.selected,
        report=str(directory/'调参结果.md'), selected_metrics=next(r for r in records if r['live_on_board'])))
    print(json.dumps(dict(output_directory=str(directory), candidates=records), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
