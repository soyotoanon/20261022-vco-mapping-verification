from pathlib import Path
import sys,json,numpy as np,os
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/balanced_followup/steps_stability07'
sys.path.insert(0,str(ROOT/'work/measurement'))
from three_common import *
OUT=ROOT/'outputs/balanced_followup/steps_stability07'
sys.path.insert(0,r'D:\DSproject\workbench\tasks\pid-step-response-20261028\f129_step_round_20261029\host')
from run_measurement import CAL
meter=json.loads((ROOT/'outputs/website_lock_comparison_30s_balanced/website_record.json').read_text(encoding='utf-8'))['lock_comparison']
mr=[x for x in meter['rows'] if x['phase']=='closed'];mt=np.array([x['elapsed_s'] for x in mr]);mv=np.array([x['power_mw'] for x in mr])
stats={'meter_closed':dict(samples=len(mr),span_s=float(np.ptp(mt)),median_read_interval_s=float(np.median(np.diff(mt))),mean_mw=float(mv.mean()),pp_mw=float(np.ptp(mv)),pp_percent=float(np.ptp(mv)/mv.mean()*100),rms_percent=float(mv.std()/mv.mean()*100)),'fast_tail':[]}
traces=[]
for path in sorted(OUT.glob('balanced_fast_plus*mW_out.json')):
    m=json.loads(path.read_text(encoding='utf-8'));raw=np.load(path.with_name(path.stem+'_raw.npy'));adc=raw.astype(float)/4
    t=((np.arange(len(raw))+6)*m['decimation']-m['CIC_group_delay_raw_samples']-m['trigger_raw_tick'])/m['raw_fs_hz'];p=np.interp(adc,CAL.codes,CAL.powers)
    sel=np.flatnonzero(t>=t[-1]-.001);v=p[sel];jmin=int(sel[np.argmin(v)]);jmax=int(sel[np.argmax(v)])
    r=dict(name=m['name'],mean_mw=float(v.mean()),pp_mw=float(np.ptp(v)),pp_percent=float(np.ptp(v)/v.mean()*100),rms_percent=float(v.std()/v.mean()*100),sample_interval_us=float(np.median(np.diff(t))*1e6),min_mw=float(p[jmin]),min_time_ms=float(t[jmin]*1000),max_mw=float(p[jmax]),max_time_ms=float(t[jmax]*1000),adc_min=float(adc[jmin]),adc_max=float(adc[jmax]),samples=len(v))
    stats['fast_tail'].append(r);traces.append((m,t,p,r))
(OUT/'meter_fast_comparison.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
os.environ['MPLCONFIGDIR']=str(ROOT/'outputs/balanced_followup/mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
m,t,p,r=next(x for x in traces if x[0]['requested_delta_mw']==2)
fig,axs=plt.subplots(2,1,figsize=(11,7),layout='constrained');y=(p/r['mean_mw']-1)*100
axs[0].plot(t*1000,y,lw=.8);axs[0].set(xlim=(1.58,2.60),xlabel='阶跃写入后 / ms',ylabel='相对末段均值偏差 / %',title=f'+2 mW阶跃最后1ms：峰峰值 {r["pp_percent"]:.3f}%，RMS {r["rms_percent"]:.3f}%')
center=r['min_time_ms'];axs[1].plot(t*1000,y,'o-',ms=3,lw=.8);axs[1].set(xlim=(center-.02,center+.02),xlabel='阶跃写入后 / ms',ylabel='相对末段均值偏差 / %',title='末段最小值附近放大（原始采样点，未删点或额外平滑）')
tail=y[t>=t[-1]-.001]
for ax in axs:
    ax.set_ylim(float(tail.min()-.25),float(tail.max()+.25));ax.axhline(0,color='gray',lw=.6);ax.grid(alpha=.2)
fig.suptitle('高速峰峰值核对：它发生在阶跃后的末段，不是初始过冲',fontsize=15)
fig.savefig(OUT/'高速末段波动核对.png',dpi=160);plt.close(fig)
print(json.dumps(stats,ensure_ascii=False,indent=2))
