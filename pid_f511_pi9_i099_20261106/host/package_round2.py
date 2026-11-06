"""Publish the evidence of a broader search without claiming global optimality."""
import argparse
import json
import math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def checked(path):
    r=read(path/'restoration.json')
    if not r['restored'] or r.get('trial_error'):raise RuntimeError('Incomplete measurement: '+str(path))
    return read(path/'results.json')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dense',type=Path,required=True)
    parser.add_argument('--baseline-repeat',type=Path,required=True)
    parser.add_argument('--final-check',type=Path,required=True)
    args=parser.parse_args()
    broad_paths=[ROOT/'results/20261106_170703_768952_pi_scan',ROOT/'results/20261106_171653_112307_pi_scan']
    all_trials=[t for path in broad_paths for t in checked(path)]
    dense=checked(args.dense)[0];baseline=checked(args.baseline_repeat)[0]
    repeat_points=sorted(baseline['points'],key=lambda p:p['requested_hz'])
    a,b=repeat_points[:2]
    reference_band=10**(math.log10(a['requested_hz'])+(-3-a['closed_over_held_db'])/
        (b['closed_over_held_db']-a['closed_over_held_db'])*math.log10(b['requested_hz']/a['requested_hz']))
    if not a['closed_over_held_db']<=-3<b['closed_over_held_db']:raise RuntimeError('Reference does not bracket -3dB.')
    repeat_peak=max(p['closed_over_held_db'] for p in repeat_points)
    qualified=read(args.dense/'qualification.json')
    noise_path=ROOT/'results/20261106_172343_093800_natural_noise'
    noise=read(noise_path/'repeat_comparison.json');final=read(args.final_check/'result.json')
    if final['kp']!=9 or final['ki']!=32440/32768:raise RuntimeError('Expected previous validated profile retained.')
    unique=sorted(set((t['kp'],t['ki']) for t in all_trials))
    summary=dict(user_priority='Preserve strong low-frequency suppression and prioritize overall noise reduction',
        unique_broad_candidates=len(unique),broad_trials_including_reference_repeat=len(all_trials),
        gains=unique,frequency_pairs_in_broad_scan=sum(len(t['points']) for t in all_trials),
        candidate_dense=qualified,noise_repeated=noise,baseline_repeat=baseline,final=final,
        baseline_repeat_bandwidth_hz=reference_band,baseline_repeat_maximum_tested_db=repeat_peak,
        decision='Retain P=9, I=0.989990234375. No reliable overall-noise improvement established; broad search is not a proof of global optimum.',
        firmware_changed_this_round=False,flash_written=False,github_uploaded=False,
        host_change='Clamp rounded Q15 Ki payload to32767 so gains just below1 cannot wrap to zero; five offline tests pass.',
        measurement='Actuator-injected in-loop rejection and ADC-derived noise. Independent optical RIN not verified.')
    (ROOT/'results/round2_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    fig,ax=plt.subplots(figsize=(10,6),layout='constrained')
    old=read(ROOT/'results/20261106_164350_885116_pi_scan/results.json')[0]
    for trial,label,color in [(old,'Previous dense scan: P=9, I=0.99','#346b9e'),
                             (dense,'Candidate dense scan: P=11, I=0.99997','#c2672c'),
                             (baseline,'Longer repeat: P=9, I=0.99','#244b36')]:
        p=sorted(trial['points'],key=lambda x:x['requested_hz'])
        ax.semilogx([v['requested_hz'] for v in p],[v['closed_over_held_db'] for v in p],'o-',
                    label=label,color=color,markersize=4)
    ax.axhline(7,color='#ae3333',linestyle='--');ax.axhline(-3,color='gray',linestyle='--')
    ax.axvline(15000,color='gray',linestyle=':');ax.axhline(0,color='gray',linewidth=.6)
    ax.set(xlabel='Frequency (Hz)',ylabel='Closed / held tone amplitude (dB)',
           title='Repeated measurements: bandwidth and amplification trade-offs at approximately 1 mW')
    ax.grid(which='both',alpha=.2);ax.legend(fontsize=9)
    fig.savefig(ROOT/'results/round2_response_comparison.png',dpi=170);plt.close(fig)
    band=qualified['minus_3db_upward_crossings'][0]['estimated_hz']
    text=f'''第二轮 PI 搜索：定性认识与实测结论

用户优先级：保住低频强抑制，优先整体压噪；原目标仍为 -3dB 带宽约15kHz、放大峰不超过+7dB。
当前约1mW工作点，最终保留 P=9，I=0.989990234375。未改FPGA固件，未写Flash，未上传GitHub。
本轮没有证实新的参数能在保住低频和放大峰约束的同时，可靠降低整体噪声。

P、I、D 怎样理解
P 根据当前误差立即修正。适度增大通常使纠偏更有力，但它也改变阻尼和峰的位置；过大或过小都可能变差。
I 根据持续误差累积修正。增大通常加强低频压噪、加快消除持续偏差，也可能放大中高频振铃；减小则更温和，但低频抑制会减弱。
这里 I 是5MHz逐有效采样的离散增益，不是有些控制器界面中的积分时间 Ti。
D 根据变化速度修正，适当滤波和设计可能增加阻尼，但也会放大测量噪声。当前F511实际是PI，没有D项可直接调。
P/I的配合决定比例和积分在不同频段的相对作用；峰值低并不必然意味着整段频率内的噪声总量低。

这轮实际做了什么
本轮比较 {len(unique)} 组不同参数、{len(all_trials)} 次宽范围候选测试（含原参数重复），合计 {summary['frequency_pairs_in_broad_scan']} 个频率对。
覆盖 P=0..22、I约0.05..0.99997，包括低P强I、高P弱I和接近原解的边界候选。
按原参数—候选—原参数—候选交替测量真实环内噪声，每组2次；随后对候选做30点加密扫频，并对原参数关键频点加长记录复测。
P=3、I约0.99曾在35kHz测到+8.12dB，P=0时约+8.36dB：P变小不能保证更稳定。
P=20、I约0.05初筛在15kHz约-3.11dB、已测峰约+4.90dB，但1kHz只有约-5.94dB；按用户优先级不留用。
P=11、I=0.999969482的加密结果：-3dB交点约{band/1000:.2f}kHz，已测最高放大{qualified['maximum_measured_amplification_db']:+.2f}dB。
P=9、I约0.99的本轮长记录复测：-3dB交点约{reference_band/1000:.2f}kHz，7个关键频点内最大放大{repeat_peak:+.2f}dB；其原先30点扫描约13.8kHz/+6.3dB，说明指标有测量/现场波动，不能把历史值当固定常数。
原/候选两次闭环1–120kHz RMS均值分别约0.781%/0.784%；记录有重叠波动，不能宣称整体噪声下降。
10–1000Hz RMS均值约0.0454%/0.0423%；各只有两次记录，不能据此证明统计显著改善。
RMS分子为对应频带内积分功率谱的平方根，分母为同次记录的历史标定平均功率；这是环内ADC估计。

关于局部最优
上轮得到的是有限候选中的可用解，并未证明局部或全局最优。
这轮已尝试相距很远的参数组合，减少只围绕单一起点搜索的盲区；有限网格和带噪测量仍不能证明全局最优。
常用Q15积分编码上限接近1（另有旧移位编码能表示恰好1），搜索边界不等于物理极限；D尚未实现。
进一步显著提升应结合实测开环幅相频响、总延迟、ADC噪声和不同功率点建模，再判断扩展积分范围、带滤波相位补偿等是否有效。
更复杂的优化算法可以帮助寻找候选，但不能保证越过硬件、时延或测量噪声的限制。

限制
仅当前约1mW光路；不是0–15mW全范围验收，也不是独立光学RIN验收。
OTR历史位仍为{final['status']}中的bit1；此前加载后即已置位，原版仅复位清除，触发原因仍未确定。采集按实时fault/clipping标志判错。
本轮新增主机端边界修复：I非常接近1时，Q15舍入上溢可能误编码成0；现已钳位至32767，并新增边界测试。实际已测参数没有触发旧问题。

数据
宽范围：{broad_paths[0].relative_to(ROOT)}、{broad_paths[1].relative_to(ROOT)}
候选加密：{args.dense.resolve().relative_to(ROOT)}
原参数长记录复测：{args.baseline_repeat.resolve().relative_to(ROOT)}
交替噪声：{noise_path.relative_to(ROOT)}
完整摘要：results/round2_summary.json

参考
MathWorks PID Tuning Algorithm：https://www.mathworks.com/help/control/getstart/pid-tuning-algorithm.html
MathWorks PID Controller Types：https://www.mathworks.com/help/control/ug/pid-controller-types-for-tuning.html
'''
    (ROOT/'ROUND2_RESULT.txt').write_text(text,encoding='utf-8')
    scope=read(ROOT/'optimization_scope.json')
    scope.update(latest_summary='results/round2_summary.json',latest_report='ROUND2_RESULT.txt',
                 user_noise_priority=summary['user_priority'],
                 optimization_status='Broader second search completed; no reliable overall-noise improvement established; previous gains retained')
    (ROOT/'optimization_scope.json').write_text(json.dumps(scope,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    readme=ROOT/'README.txt';content=readme.read_text(encoding='utf-8')
    latest='最新进展：第二轮广域搜索与交替噪声对照见 ROUND2_RESULT.txt；目前保留 P=9、I约0.99。\n\n'
    if latest not in content:readme.write_text(latest+content,encoding='utf-8')
    print('ROUND2_PACKAGED',json.dumps(dict(candidate_bandwidth_hz=band,candidate_peak_db=qualified['maximum_measured_amplification_db'],retained=final)))

if __name__=='__main__':main()
