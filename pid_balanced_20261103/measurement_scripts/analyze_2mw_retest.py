from pathlib import Path
import sys,json,os,ast,numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/balanced_followup/steps_2mw_retest';OLD=OUT.parent/'steps_stability07'
sys.path.insert(0,str(ROOT/'work/measurement'))
from three_common import pd,hw,ls
sys.path.insert(0,r'D:\DSproject\workbench\tasks\pid-step-response-20261028\f129_step_round_20261029\host')
from run_measurement import CAL
# Reuse the exact previously agreed metric function without executing its report.
source=(ROOT/'work/measurement/analyze_stability20.py').read_text(encoding='utf-8')
node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='metrics')
exec(ast.get_source_segment(source,node))
os.environ['MPLCONFIGDIR']=str(OUT.parent/'mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':10})
def load_trace(path):
    m=json.loads(path.read_text(encoding='utf-8'));raw=np.load(path.with_name(path.stem+'_raw.npy'))
    t=((np.arange(len(raw))+6)*m['decimation']-m['CIC_group_delay_raw_samples']-m['trigger_raw_tick'])/m['raw_fs_hz'];p=np.interp(raw.astype(float)/4,CAL.codes,CAL.powers)
    r,wt,ratios=metrics(t,p,m['mode']);r.update(name=m['name'],direction=m['direction'],mode=m['mode'],configuration=m['configuration'],requested_delta_mw=m['requested_delta_mw'],capture_flags=m['capture_status'],limited_or_fault=bool(m['capture_status']&12),samples=len(raw))
    return m,t,p,r,wt,ratios
new=[load_trace(p) for p in sorted(OUT.glob('balanced_*_plus2mW_*.json'))]
assert len(new)==4 and all(x[3]['samples']==8192 for x in new)
report=dict(criteria='Rolling >=1ms (max-min)/abs(actual final mean)<=2%, all subsequent complete windows pass until record end. Final mean is last1ms for fast or last0.2s for long; t90 is90% of measured change; no target agreement requirement or extra smoothing.',results=[x[3] for x in new],new_measurement=True)
(OUT/'metrics20.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def fmt(v):return '未满足' if v is None else f'{v*1e6:.1f} μs'
def finish(fig,name):
    for ax in fig.axes:ax.grid(alpha=.2)
    fig.savefig(OUT/name,dpi=160);plt.close(fig)
colors={'out':'#216b62','return':'#b26631'};names={'out':'上升 +2 mW','return':'返回原功率'}

# Every step amplitude, with both directions. Only 2mW uses the new capture.
fig,axs=plt.subplots(4,2,figsize=(12,12),layout='constrained')
for amp,aa in zip([.2,.5,1,2],axs):
    for direction,ax in zip(['out','return'],aa):
        trace=next(x for x in new if x[0]['mode']=='fast' and x[0]['direction']==direction) if amp==2 else load_trace(OLD/f'balanced_fast_plus{amp:g}mW_{direction}.json')
        m,t,p,r,wt,ratios=trace;ax.plot(t*1e6,p,color=colors[direction],lw=.85);ax.axhline(r['final_mean_mw'],ls='--',color='#888',lw=.8);ax.axvline(0,color='#888',lw=.6)
        ax.set(xlim=(-20,120),xlabel='阶跃写入后 / μs',ylabel='PD1等效功率 / mW',title=f'{amp:g}mW {"上升" if direction=="out" else "返回"}｜t90 {fmt(r["t90_s"])}｜窗口起点 {fmt(r["settling_s"])}'+(' *' if r['limited_or_fault'] else ''))
fig.suptitle('P11 / I0.72000122：四种阶跃的上升与返回\n0.2 / 0.5 / 1 mW沿用上一轮；2 mW为本次重测；*表示综合故障标志',fontsize=14)
finish(fig,'全部阶跃_上升与返回.png')

# Full fast records, and the corresponding peak-to-peak criterion.
fig,axs=plt.subplots(3,2,figsize=(12,10),layout='constrained')
for direction,col in [('out',0),('return',1)]:
    m,t,p,r,wt,ratios=next(x for x in new if x[0]['mode']=='fast' and x[0]['direction']==direction);color=colors[direction]
    axs[0,col].plot(t*1000,p,color=color,lw=.7);axs[0,col].axhline(r['final_mean_mw'],color='#888',ls='--');axs[0,col].set(xlabel='写入后 / ms',ylabel='PD1等效功率 / mW',title=names[direction]+f'：完整8192点，后段{t[-1]*1000:.2f}ms')
    sel=t>=.0001;dev=(p/r['final_mean_mw']-1)*100;axs[1,col].plot(t[sel]*1000,dev[sel],color=color,lw=.7);axs[1,col].set(xlabel='写入后 / ms',ylabel='相对最终均值偏差 / %',title='100 μs之后的残余波动（未额外平滑）')
    ax=axs[2,col];ax.plot(wt*1000,ratios*100,color=color,lw=.9);ax.axhline(2,color='#b97827',ls='--',label='2%门槛');ax.set_yscale('log');ax.set_yticks([.5,1,2,5,10,20,50]);ax.yaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:g}'));ax.set(xlabel='1ms窗口起点 / ms',ylabel='窗口峰峰值 / 最终均值 / %',title='合格窗口起点：'+fmt(r['settling_s']));ax.legend(loc='upper right')
    if r['settling_s'] is not None:ax.axvline(r['settling_s']*1000,color='#666',ls=':')
fig.suptitle('2 mW重新实测：高速完整波形与2%峰峰值判据\n0.8 μs/点；窗口起点之后还需至少1 ms观测才可确认',fontsize=14)
finish(fig,'2mW_完整高速与稳定判据.png')

# Microsecond detail without hiding the transient.
fig,axs=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
for direction,ax in zip(['out','return'],axs):
    m,t,p,r,wt,ratios=next(x for x in new if x[0]['mode']=='fast' and x[0]['direction']==direction);ax.plot(t*1e6,p,'o-',ms=2.5,lw=.85,color=colors[direction]);ax.axhline(r['final_mean_mw'],color='#888',ls='--');ax.axvline(r['t90_s']*1e6,color='#b97827',ls='--',label='t90')
    if r['settling_s'] is not None and r['settling_s']<.00015:ax.axvline(r['settling_s']*1e6,color='#555',ls=':',label='合格窗口起点')
    ax.set(xlim=(-15,150),xlabel='写入后 / μs',ylabel='PD1等效功率 / mW',title=names[direction]+f'｜t90 {fmt(r["t90_s"])}');ax.legend()
fig.suptitle('本次2 mW重测：阶跃边沿局部放大（原始采样点）',fontsize=14);finish(fig,'2mW_边沿局部放大.png')

fig,axs=plt.subplots(2,2,figsize=(12,7),layout='constrained')
for direction,col in [('out',0),('return',1)]:
    m,t,p,r,wt,ratios=next(x for x in new if x[0]['mode']=='long' and x[0]['direction']==direction);color=colors[direction]
    axs[0,col].plot(t,p,color=color,lw=.8);axs[0,col].set(xlabel='写入后 / s',ylabel='PD1等效功率 / mW',title=names[direction]+'：长记录')
    sel=t>=t[-1]-.01;axs[1,col].plot((t[sel]-t[-1]+.01)*1000,(p[sel]/r['final_mean_mw']-1)*100,'o-',ms=3,color=color,lw=.8);axs[1,col].set(xlabel='末段10 ms内时间 / ms',ylabel='相对最终均值偏差 / %',title='末段10 ms放大（可见量化台阶）')
fig.suptitle('本次2 mW重测：约3.15秒后段保持\n409.6 μs/点，三阶CIC平均；此图不能替代高速峰峰判据',fontsize=14);finish(fig,'2mW_长时间保持.png')
lines=['2mW本次重测；P11/I0.72000122；2%窗口峰峰判据',report['criteria']]
for r in report['results']:lines.append(f"{r['mode']} {r['direction']}: t90={fmt(r['t90_s'])}, window_start={fmt(r['settling_s'])}, tail_pp={r['tail_window_pp_percent']:.4f}%, max_window_after100us={r['worst_window_pp_percent_after_100us']:.4f}%, flags={r['capture_flags']}, post={r['record_post_s']:.6f}s")
(OUT/'结果说明.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8');print('\n'.join(lines),flush=True)
