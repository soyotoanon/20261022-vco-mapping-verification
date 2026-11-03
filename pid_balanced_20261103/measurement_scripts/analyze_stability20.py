"""User criterion: rolling >=1ms peak-to-peak / measured final mean <=2%."""
from pathlib import Path
import json,os,sys,numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/balanced_followup/steps_stability07'
sys.path.insert(0,str(ROOT/'work/measurement'))
from three_common import pd,hw,ls
sys.path.insert(0,r'D:\DSproject\workbench\tasks\pid-step-response-20261028\f129_step_round_20261029\host')
from run_measurement import CAL
os.environ['MPLCONFIGDIR']=str(ROOT/'outputs/balanced_followup/mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})

def metrics(t,p,mode):
    dt=float(np.median(np.diff(t)));n=int(np.ceil(.001/dt-1e-8))+1
    tail_seconds=.001 if mode=='fast' else .2
    final=float(p[t>=t[-1]-tail_seconds].mean());before=float(np.median(p[t<0]));delta=final-before
    hit=np.flatnonzero((t>=0)&((p-before)*np.sign(delta)>=.9*abs(delta)))
    t90=None if not len(hit) else float(t[hit[0]])
    start=int(np.flatnonzero(t>=0)[0]);w=np.lib.stride_tricks.sliding_window_view(p[start:],n)
    pp=np.ptp(w,axis=1);ratios=pp/abs(final);wt=t[start:start+len(pp)];bad=np.flatnonzero(ratios>.02)
    k=int(bad[-1]+1) if len(bad) else 0
    settling=float(wt[k]) if k<len(wt) else None
    result=dict(before_mean_mw=before,final_mean_mw=final,actual_step_mw=delta,t90_s=t90,settling_s=settling,threshold_fraction=.02,window_samples=n,window_span_s=(n-1)*dt,final_mean_tail_s=tail_seconds,tail_window_pp_percent=float(ratios[-1]*100),worst_window_pp_percent_after_100us=float(ratios[wt>=.0001].max()*100),settling_hold_to_record_end_s=None if settling is None else float(t[-1]-settling),record_post_s=float(t[-1]),sample_interval_s=dt)
    return result,wt,ratios

rows=[];traces=[]
for path in sorted(OUT.glob('balanced_*_plus*mW_*.json')):
    m=json.loads(path.read_text(encoding='utf-8'));raw=np.load(path.with_name(path.stem+'_raw.npy'))
    t=((np.arange(len(raw))+6)*m['decimation']-m['CIC_group_delay_raw_samples']-m['trigger_raw_tick'])/m['raw_fs_hz']
    p=np.interp(raw.astype(float)/4,CAL.codes,CAL.powers)
    result,wt,ratios=metrics(t,p,m['mode']);result.update(name=m['name'],mode=m['mode'],direction=m['direction'],requested_delta_mw=m['requested_delta_mw'],capture_flags=m['capture_status'],limited_or_fault=bool(m['capture_status']&12),configuration=m['configuration'])
    rows.append(result);traces.append((m,t,p,result,wt,ratios))
assert len(rows)==16,len(rows)
report=dict(criteria='Rolling window spanning >=1ms: (maximum-minimum)/abs(measured final mean)<=0.02. Settling is the first post-step window start for which every subsequent complete window also passes until record end. No condition on target agreement. t90 is first arrival at 90% of measured before-to-final change. No extra smoothing. Final mean is last1ms (fast) or last0.2s (long). Historical ADC-power calibration is used only for conversion, not as a target requirement.',results=rows)
(OUT/'metrics20.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
fig,axs=plt.subplots(4,3,figsize=(16,12),layout='constrained')
for amp,aa in zip([.2,.5,1,2],axs):
    for m,t,p,r,wt,ratios in traces:
        if m['direction']!='out' or m['requested_delta_mw']!=amp:continue
        if m['mode']=='fast':
            a,b=aa[:2];a.plot(t*1e6,p,lw=.7);a.axhline(r['final_mean_mw'],color='#b97827',ls='--');a.set(xlim=(-20,100),xlabel='写入后 / μs',ylabel='PD1等效功率 / mW',title=f'+{amp:g} mW：原始高速波形')
            b.plot(wt*1e6,ratios*100,lw=1,color='#216b62');b.axhline(2,color='#b97827',ls='--');b.set(xlim=(0,200),xlabel='1ms窗口起点 / μs',ylabel='峰峰值 / 最终均值 / %',title='高速回稳：'+('未满足' if r['settling_s'] is None else f'{r["settling_s"]*1e6:.1f} μs'))
        else:
            a=aa[2];a.plot(t,p,lw=.8,color='#216b62');a.axhline(r['final_mean_mw'],color='#b97827',ls='--');a.set(xlabel='写入后 / s',ylabel='PD1等效功率 / mW',title='长记录（409.6 μs/点，CIC平均）')
    for a in aa:a.grid(alpha=.2)
fig.suptitle('P11 / I0.72000122：本轮阶跃数据重算；1ms窗口峰峰值 / 实际最终均值 ≤2%',fontsize=16)
fig.savefig(OUT/'阶跃响应_峰峰值20.png',dpi=160);plt.close(fig)
lines=['P11 / I0.72000122，2%峰峰值新判据重算',report['criteria'],'','幅度/mW | 方向 | 模式 | t90/us | 回稳/us | 末窗口峰峰/% | 记录标志']
for r in rows:
    fmt=lambda x:'未满足' if x is None else f'{x*1e6:.1f}'
    lines.append(f"{r['requested_delta_mw']:g} | {r['direction']} | {r['mode']} | {fmt(r['t90_s'])} | {fmt(r['settling_s'])} | {r['tail_window_pp_percent']:.4f} | {r['capture_flags']}")
lines+=['','最终均值不要求等于设定功率。高速0.8us/点，长记录409.6us/点；长记录经较强CIC平均，不能拿其低峰峰值替代高速结果。', '回稳从满足窗口的起点计，须看到完整窗口才可判定；确认延迟至少1ms。']
(OUT/'结果说明20.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines),flush=True)
