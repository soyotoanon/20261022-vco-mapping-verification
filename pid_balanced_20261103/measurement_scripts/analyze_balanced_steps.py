from three_common import *
OUT=OUT.parent/"balanced_followup"
for m in [pd,hw,ls]:m.OUT=OUT
import os,json,numpy as np
os.environ['PID_STEP_OUTPUT']=str(OUT/'steps_current')
os.environ['MPLCONFIGDIR']=str(OUT/'mpl-cache')
sys.path.insert(0,r'D:\DSproject\workbench\tasks\pid-step-response-20261028\f129_step_round_20261029\host')
from run_measurement import CAL
from step_response_metrics import measures
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
rows=[];traces=[]
for path in sorted((OUT/'steps_current').glob('*_plus*mW_*.json')):
    m=json.loads(path.read_text(encoding="utf-8"));raw=np.load(path.with_name(path.stem+'_raw.npy'))
    ratio=m['decimation'];fs=m['raw_fs_hz']
    t=((np.arange(len(raw))+6)*ratio-m['CIC_group_delay_raw_samples']-m['trigger_raw_tick'])/fs
    power=np.interp(raw.astype(float)/4,CAL.codes,CAL.powers);target=CAL.to_power(m['end_target_code'])
    result=measures(t,power,target,.001)
    result.update(configuration=m['configuration'],name=m['name'],mode=m['mode'],direction=m['direction'],requested_delta_mw=m['requested_delta_mw'],capture_flags=m['capture_status'],end_dac=m['final']['05'],limited_or_fault=bool(m['capture_status']&12),samples=len(raw))
    rows.append(result);traces.append((m,t,power,result))
hw.save('step_metrics.json',dict(criteria='First 90% arrival; strict settling stays within +/-2% of STEP AMPLITUDE until end, at least 1ms. Historical ADC-power calibration, not fresh power-meter calibration.',results=rows))
for cfg in [CONFIGS[1]]:
    fig,axs=plt.subplots(4,2,figsize=(12,12),layout='constrained')
    for amp,aa in zip([.2,.5,1.,2.],axs):
        for mode,ax in zip(['fast','long'],aa):
            for m,t,p,r in traces:
                if m['configuration']['name']!=cfg['name'] or m['requested_delta_mw']!=amp or m['mode']!=mode or m['direction']!='out':continue
                scale=1e6 if mode=='fast' else 1e3
                ax.plot(t*scale,p,lw=.7);ax.axvline(0,c='gray',lw=.7);ax.axhspan(r['target_mw']-r['tolerance_mw'],r['target_mw']+r['tolerance_mw'],color='orange',alpha=.15)
                ax.set(title=f"+{amp:g} mW, t90={r['t90_s']*1e6:.1f} us" if r['t90_s'] is not None else f'+{amp:g} mW, 未到90%',xlabel='目标写入后时间 / '+('μs' if mode=='fast' else 'ms'),ylabel='ADC旧标定等效功率 / mW');ax.grid(alpha=.2)
                if mode=='fast':ax.set_xlim(-50,500)
                else:ax.set_title(f'+{amp:g} mW，长记录（采样间隔409.6 μs）')
    fig.suptitle(f"{cfg['label']}：阶跃响应；阴影为目标±阶跃幅度的2%")
    fig.savefig(OUT/f"阶跃响应_{cfg['name']}.png",dpi=160);plt.close(fig)
print('Analyzed',len(rows),'step traces')
