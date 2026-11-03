from pathlib import Path
import json,os,numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/balanced_followup/recovery_current'
os.environ['MPLCONFIGDIR']=str(ROOT/'outputs/balanced_followup/mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
load=lambda name:json.loads((OUT/name).read_text(encoding='utf-8'))
record=load('slow_record.json');meta=load('capture.json');a=np.load(OUT/'pd1_raw.npy').astype(float)/4;dt=meta['sample_interval_s'];t=np.arange(len(a))*dt
rows=record['rows'];start=record['capture_start_s']
pre=[r for r in rows if r['elapsed_s']<start-.3]
baseline=float(np.median([r['adc'] for r in pre]));dark=float(np.median(a[:500]));span=baseline-dark
assert span>300
y=(a-dark)/span
i10=int(np.flatnonzero(y>=.1)[0]);i90=int(np.flatnonzero(y>=.9)[0])
last_out=int(np.flatnonzero(np.abs(y-1)>.02)[-1]);stable=last_out+1
assert stable>=i90 and stable<len(y) and t[-1]-t[stable]>=.1
meter=[r for r in rows if 'power_mw' in r]
pre_m=np.array([r['power_mw'] for r in pre if 'power_mw'in r]);post_m=np.array([r['power_mw'] for r in meter if r['elapsed_s']>start+t[i90]+2])
summary=dict(configuration=record['configuration'],adc_dark=dark,adc_before=baseline,adc_final_median=float(np.median(a[-1000:])),sample_interval_ms=dt*1000,observed_rise_10_90_ms=float((t[i90]-t[i10])*1000),observed_settling_from_10percent_ms=float((t[stable]-t[i10])*1000),settling_band='pre-block ADC level +/-2% of pre-block-minus-dark signal; remains in band until record end, at least 0.1s',hold_after_settling_s=float(t[-1]-t[stable]),dark_to_10percent_s=float(t[i10]),meter_before_mw=float(pre_m.mean()),meter_after_mw=float(post_m.mean()),meter_change_percent=float((post_m.mean()/pre_m.mean()-1)*100),meter_min_mw=min(r['power_mw'] for r in meter),dac_max=max(r['dac'] for r in rows),final_registers=load('physical_recovery_after.json'),note='Observed physical shutter recovery, not intrinsic PI latency. Third-order CIC decimation4096; dt0.8192ms, filter group delay about1.23ms. Flags10=done+sticky fault during intentional dark event; final live status2 (normal).')
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
fig,axs=plt.subplots(3,1,figsize=(11,10));fig.subplots_adjust(top=.92,bottom=.1,hspace=.43)
tm=np.array([r['elapsed_s']-start for r in meter]);pm=np.array([r['power_mw'] for r in meter])
axs[0].plot(tm,pm,color='#216b62',lw=1.5);axs[0].set(xlim=(-2,t[-1]+3),ylabel='功率计 / mW',xlabel='相对检测到遮光的时间 / s',title=f'独立功率计：遮光前 {pre_m.mean():.4f} mW → 恢复后 {post_m.mean():.4f} mW')
axs[1].plot(t,y*100,color='#216b62');axs[1].set(xlabel='FPGA记录内时间 / s',ylabel='PD1信号 / %',title='PD1连续记录：扣除实测暗电平，遮光前光信号归一化为100%')
tr=(t-t[i10])*1000
axs[2].plot(tr,y*100,'o-',ms=3,color='#216b62');axs[2].axhspan(98,102,alpha=.15,color='#216b62');axs[2].axvline(tr[i90],ls='--',color='#b97827');axs[2].axvline(tr[stable],ls=':',color='#555');axs[2].set(xlim=(-5,max(30,tr[stable]+10)),xlabel='相对光信号首次回到10%的时间 / ms',ylabel='PD1信号 / %',title=f'观测10–90%上升 {summary["observed_rise_10_90_ms"]:.2f} ms；进入±2%且保持 {summary["observed_settling_from_10percent_ms"]:.2f} ms')
for ax in axs:ax.grid(alpha=.2)
fig.suptitle('P=11，I=0.72000122：本次人工遮光恢复实测',fontsize=16)
fig.text(.5,.025,'采样间隔0.8192 ms，PD1采用三阶CIC抽取；时间包含挡板机械移动与采集滤波。\n回稳判据针对扣暗后的整次遮光幅度；本图不能代表PI电子环路本征恢复时间。',ha='center',fontsize=10)
fig.savefig(OUT/'遮光恢复_P11.png',dpi=170);plt.close(fig)
print(json.dumps({k:v for k,v in summary.items() if k!='final_registers'},ensure_ascii=False,indent=2))
