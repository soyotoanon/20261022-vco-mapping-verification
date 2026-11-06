"""Package measured results and source/build evidence; no hardware or network writes."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def rel(path):return path.resolve().relative_to(ROOT).as_posix()
def utilization(path):
    text=path.read_text(encoding='utf-8')
    row=re.search(r'^\| light_lock_top\s+\|[^\n]+',text,re.M).group(0)
    values=[v.strip() for v in row.split('|')][3:]
    return dict(luts=int(values[0]),flip_flops=int(values[4]),dsp=int(values[7]),bram36=int(values[5]),bram18=int(values[6]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scan',type=Path,required=True)
    parser.add_argument('--validation',type=Path,required=True)
    parser.add_argument('--application',type=Path,required=True)
    parser.add_argument('--extended',type=Path)
    args=parser.parse_args()
    quality=read(args.scan/'qualification.json')
    validation=read(args.validation/'restoration.json')
    if not validation['restored'] or validation.get('trial_error'):raise RuntimeError('Validation failed.')
    applied=read(args.application/'result.json')
    if applied['final_status']&0x3D:raise RuntimeError('Applied profile has a fault.')
    build=read(ROOT/'candidate/build_summary.json')
    tests=read(ROOT/'candidate/build/verification/results.json')
    if len(tests)!=9 or not all(t['result']=='PASS' for t in tests):raise RuntimeError('RTL regression evidence missing.')
    digest=hashlib.sha256((ROOT/'candidate/bitstream/light_lock_top.bit').read_bytes()).hexdigest()
    if digest!=build['bit_sha256']:raise RuntimeError('Build hash mismatch.')
    original=utilization(ROOT/'results/baseline_build_evidence/build/utilization_hierarchy.rpt')
    candidate=utilization(ROOT/'candidate/build/utilization_hierarchy.rpt')
    sustained=read(args.validation/'sustained_lock.json')['summary']
    dark=read(args.validation/'shutdown.json')['summary']
    recovery=read(args.validation/'recovery.json')['summary']
    points=[dict(requested_mw=p,**read(args.validation/f'{i:02d}_steady.json')['summary'])
            for i,p in enumerate([.6,.3,.6,1.0])]
    extended=[]
    if args.extended:
        ext_restore=read(args.extended/'restoration.json')
        if not ext_restore['restored'] or ext_restore.get('trial_error'):raise RuntimeError('Extended scan failed.')
        extended=[dict(frequency_hz=p['requested_hz'],db=p['closed_over_held_db'],resolved=p['held_tone_resolved'])
                  for p in read(args.extended/'results.json')[0]['points']]
    result=dict(status='First operating point qualified; full 0–15 mW range not qualified',
                nominal_power_mw=1.0,qualification=quality,sustained_lock=sustained,
                shutdown=dark,recovery=recovery,target_checks=points,applied=applied,extended_frequency_checks=extended,
                resources_original=original,resources_candidate=candidate,
                resource_savings=dict(luts=original['luts']-candidate['luts'],
                    luts_percent_of_original=100*(original['luts']-candidate['luts'])/original['luts'],
                    flip_flops=original['flip_flops']-candidate['flip_flops'],
                    flip_flops_percent_of_original=100*(original['flip_flops']-candidate['flip_flops'])/original['flip_flops']),
                build=build,rtl_tests=tests,baseline_source=read(ROOT/'workspace_verification.json'),
                sources=dict(scan=rel(args.scan),validation=rel(args.validation),application=rel(args.application),
                             extended=None if args.extended is None else rel(args.extended)),
                flash_written=False,github_uploaded=False,user_accepted_optimization=False,
                independent_optical_rin_verified=False,
                hardware_status=dict(final_status=applied['final_status'],
                    historical_otr_latched=bool(applied['final_status']&2),
                    note='OTR history bit already set in first post-program snapshot before control writes. Baseline adc_drv clears only on reset; software clear affects monitor but sticky ADC state reasserts it. Recorded waveform captures did not flag live OTR. Event cause not established.'))
    (ROOT/'results/first_operating_point_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.copy2(args.scan/'qualification.png',ROOT/'results/qualified_response_1mw.png')
    bandwidth=quality['minus_3db_upward_crossings'][0]['estimated_hz']/1000
    maximum=quality['maximum_measured_amplification_db'];peak=quality['peak_frequency_hz']/1000
    report=f'''F511 第一工作点：实测调参与独立副本精简

范围与结论
当前实测主光路最大功率约 1.44 mW，本轮以约 1.00 mW 为调参工作点。
用户要求：-3 dB 抑制带宽约 15 kHz，高频放大不超过 +7 dB。
P={applied['kp']:g}，I={applied['ki']:.9f}，D 未启用（本工程是 PI）。
I 是每个有效采样的离散增益；采样率为 5 MHz，不是每秒积分系数。
目标 ADC={applied['target_adc']}，DAC 运行范围 545–13000；这是本次实测的正斜率支路。
当前参数已留在 FPGA RAM；未写 Flash，重新上电不会自动保留本次 RAM 固件和运行参数。

频响
-3 dB 交点约 {bandwidth:.2f} kHz，相邻测点之间按对数频率插值。
已测频点中最大放大 +{maximum:.2f} dB，频率 {peak:g} kHz。
主验收扫描 {quality['measured_points']} 个频点，覆盖 {quality['measured_frequency_range_hz'][0]:g}–{quality['measured_frequency_range_hz'][1]:g} Hz。
分贝定义：20 log10(闭环拟合正弦幅度 / 同频同幅扰动、冻结反馈时的拟合正弦幅度)。
测量来自 DAC 小扰动注入与 FPGA 环内 ADC；有限频点之间可能有未测细节。
功率计用于平均功率及慢波动核对；本轮不是独立光学 RIN 验收。
高频补测明细（resolved=false 的幅度比不能作为可靠定量结果）：
{json.dumps(extended,ensure_ascii=False)}

功能实测
目标依次切换 0.6、0.3、0.6、1.0 mW，保存 FPGA 波形和功率计读数。
关断残余均值 {dark['mean_mw']:.7f} mW，恢复均值 {recovery['mean_mw']:.6f} mW。
稳定保持实际记录 {sustained['elapsed_meter_seconds']:.1f} 秒，平均 {sustained['mean_mw']:.6f} mW。
慢波动 RMS {sustained['rms_percent_of_actual_mean']:.4f}%；分子为样本标准差，分母为同段实际平均功率。
该慢波动数值只描述这次记录，不代表更长时间稳定性或高频噪声。
遗留状态：最终 STATUS={applied['final_status']}，OTR 历史位已置位；它在 RAM 加载后的首个快照、任何控制写入前就已存在。
原版 adc_drv 的该历史位仅复位清除，现有清标志命令不能彻底清掉它；触发原因尚未查明。
本轮保存的波形采集没有报告实时 OTR；不能据此声称所有时刻均无过载，也不把该历史状态隐藏成“全正常”。

代码与验证
原始提交 dbd82b81c37a0ecfdc101d2257ec997aab8e0bce 的 46 个文件 SHA256 校验保持一致。
独立 candidate 修改寄存器固定地址读写逻辑，保留所有原寄存器、限幅、上电默认值及使能语义。
保持实时目标、主光路显示、在线 PI、新 DAC 范围、关断恢复、采集和上位机通信；ARTIQ 模拟输入预留。
9 项 RTL 仿真通过，包含 20,713 次寄存器等价对照；PI 与 DAC 的逐周期验证保留。
完整顶层综合和布线通过：LUT {original['luts']} → {candidate['luts']}，寄存器 {original['flip_flops']} → {candidate['flip_flops']}。
DSP {original['dsp']} → {candidate['dsp']}，BRAM 36k/18k 保持 {candidate['bram36']}/{candidate['bram18']}。
WNS {build['wns_ns']:+.3f} ns，WHS {build['whs_ns']:+.3f} ns。
bit SHA256：{build['bit_sha256']}

边界与下一步
这一结果只验证当前入射光、标定和约 1 mW 工作点的带宽/放大峰；0.3–1.0 mW 的目标切换检查不等于各功率点都达到同样带宽。
0–15 mW 全范围尚未验收；提高可用光功率或改变光路、RF/VCO/衍射工作区后应重新测支路与环路频响。
尚未上传 GitHub；严格按用户要求，只有用户明确认可优化足够好后才上传。

数据位置
最终频响：{rel(args.scan)}
功能与稳定性：{rel(args.validation)}
保留参数：{rel(args.application)}
完整摘要：results/first_operating_point_summary.json
原始数据与失败/恢复记录均保留在 results；不以筛掉的高放大参数作为合格结果。
'''
    (ROOT/'FIRST_RESULT.txt').write_text(report,encoding='utf-8')
    scope=read(ROOT/'optimization_scope.json')
    scope.update(optimization_status=result['status'],firmware_on_board_at_latest_check='F511 register-optimized independent candidate',
                 current_profile='runtime_profile_1mw.json',latest_summary='results/first_operating_point_summary.json',
                 validated_target_switches_mw=[.3,.6,1.0],qualified_bandwidth_operating_point_mw=1.0)
    (ROOT/'optimization_scope.json').write_text(json.dumps(scope,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'README.txt').write_text('F511 独立优化工程\n\n本次完整结果见 FIRST_RESULT.txt。\n原始快照 baseline_f511 不改；所有代码修改在 candidate/host。\n主光路约 1 mW 的实测参数见 runtime_profile_1mw.json。\n完整 0–15 mW 范围尚未验收，尚未上传 GitHub。\n\n源码校验：python -B verify_workspace.py\nRTL 仿真：python -B candidate/scripts/verify.py\n完整构建：python -B candidate/scripts/run_build.py\n\n现场操作顺序：先确认功率计读数与串口独占，再加载候选 RAM、建立指定工作点，最后应用匹配的参数配置。\n功率计大屏使用 127.0.0.1:8766 的 556 通道；不要另开直接功率计会话争抢设备。\n当前参数是 RAM 配置，重启后需重新建立工作点。\n',encoding='utf-8')
    print('FIRST_OPERATING_POINT_PACKAGED',json.dumps(dict(bandwidth_hz=bandwidth*1000,peak_db=maximum,resources=candidate)))


if __name__=='__main__':main()
