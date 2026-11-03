"""Analyze only saved real captures. Synthetic data is confined to unit tests."""
from pathlib import Path
import json, os, sys
import numpy as np

HERE=Path(__file__).resolve().parent
OUT=HERE.parents[1]/'outputs/pid_step_response_20261028'
os.environ.setdefault('MPLCONFIGDIR',str(HERE/'mpl-cache'))
sys.path.insert(0,str(HERE))
from run_measurement import CAL,OUT

def measures(t, power, target, hold_s, tolerance_fraction=.02):
    t=np.asarray(t,dtype=float);p=np.asarray(power,dtype=float)
    assert t.ndim==p.ndim==1 and len(t)==len(p) and len(t)>5
    assert np.all(np.diff(t)>0) and np.all(np.isfinite(p))
    pre=t<0;post=t>=0
    assert np.count_nonzero(pre)>=5 and np.count_nonzero(post)>=5
    baseline=float(np.median(p[pre]));delta=target-baseline
    assert abs(delta)>1e-8
    tp,pp=t[post],p[post]
    band=abs(target)*tolerance_fraction
    inside=np.abs(pp-target)<=band
    def first(mask):
        ix=np.flatnonzero(mask)
        return None if not len(ix) else float(tp[ix[0]])
    enter=first(inside)
    outside=np.flatnonzero(~inside)
    last_out=int(outside[-1]) if len(outside) else -1
    candidate=last_out+1
    settle=None
    if candidate<len(tp) and tp[-1]-tp[candidate]>=hold_s:
        settle=float(tp[candidate])
    progress=(pp-baseline)/delta
    t10=first(progress>=.1);t90=first(progress>=.9)
    overshoot=max(0.,float(np.max(np.sign(delta)*(pp-target))))
    tail=p[-max(10,len(p)//10):]
    pre_std=float(np.std(p[pre]))
    starts_inside_band=abs(delta)<=band
    snr=abs(delta)/max(pre_std,1e-15)
    raw_enter,raw_settle=enter,settle
    # A small step can already lie inside a +/-2% of TARGET tolerance before
    # the command. Zero seconds in that situation is not a settling measurement.
    if starts_inside_band:enter=None;settle=None
    return dict(baseline_mw=baseline,target_mw=float(target),actual_response_span_to_target_mw=float(delta),
        tolerance_mw=float(band),tolerance_definition='plus/minus 2 percent of final target power',
        first_enter_s=enter,settling_s=settle,required_hold_s=hold_s,
        t10_s=t10,t90_s=t90,rise_10_90_s=None if t90 is None or t10 is None else t90-t10,
        overshoot_mw=overshoot,overshoot_pct_of_response_span=100*overshoot/abs(delta),
        final_mean_mw=float(np.mean(tail)),final_std_mw=float(np.std(tail)),
        final_error_mw=float(np.mean(tail)-target),pre_std_mw=pre_std,
        response_span_over_pre_noise=snr,t90_reliable=bool(snr>=5),
        inside_tolerance_before_step=bool(starts_inside_band),raw_first_enter_s=raw_enter,raw_settling_s=raw_settle,
        observation_after_step_s=float(tp[-1]),sample_interval_s=float(np.median(np.diff(t))),
        status='inside_band_before_step' if starts_inside_band else 'settled' if settle is not None else 'reached_not_settled' if enter is not None else 'not_reached')

def load_trace(path):
    m=json.loads(path.read_text(encoding='utf-8'))
    raw=np.load(path.with_name(path.stem+'_raw.npy'),allow_pickle=False)
    assert len(raw)==8192 and m['trigger_valid']
    ratio=m['decimation'];fs=m['raw_fs_hz']
    # CIC integrator pipeline is 3 raw samples, plus 3*(R-1)/2.
    # First stored result follows the sixth decimation boundary (5 warmups).
    t=((np.arange(len(raw))+6)*ratio-m['CIC_group_delay_raw_samples']-m['trigger_raw_tick'])/fs
    adc=raw.astype(float)/4
    power=np.interp(adc,CAL.codes,CAL.powers)
    assert adc.min()>=min(CAL.codes) and adc.max()<=max(CAL.codes)
    target=CAL.to_power(m['end_target_code'])
    result=measures(t,power,target,hold_s=.001 if m['mode']=='fast' else 1.)
    result.update(name=m['name'],direction=m['direction'],mode=m['mode'],repeat=m['repeat'],
        requested_delta_mw=m['requested_delta_mw'],commanded_delta_mw=m['expected_step_mw'],
        trigger_raw_tick=m['trigger_raw_tick'],capture_status=m['capture_status'],
        capture_limit_or_fault_seen=bool(m['capture_status']&12),
        end_dac=m['final']['05'],output_at_limit=m['final']['05'] in [m['final']['1E'],m['final']['1F']],
        meter_before_mw=m['meter_before'].get('power_mw'),meter_after_mw=m['meter_after'].get('power_mw'),
        timestamp_quantization_s=m['timestamp_quantization_s'],raw_file=path.with_name(path.stem+'_raw.npy').name)
    return m,t,power,result

def main():
    paths=sorted([*OUT.glob('fast_*_rep*_out.json'),*OUT.glob('fast_*_rep*_return.json'),
                  *OUT.glob('long_*_rep*_out.json'),*OUT.glob('long_*_rep*_return.json')])
    traces=[load_trace(p) for p in paths]
    results=[r for m,t,p,r in traces]
    summary={'criteria':{'arrival':'First entry into final target +/-2%; can leave again.',
            'settling':'Last entry into same band; stays through end of record, with minimum hold 1 ms (fast) or 1 s (long).',
            'power':'Historical 2026-10-20 ADC calibration; separate PM100USB readings included.',
            'timing':'FPGA first-setpoint-write timestamp; 5 MHz raw ADC; CIC group delay compensated.',
            'separate_trials':'Fast and long records are separate real steps, not stitched waveforms.'},'results':results}
    (OUT/'metrics.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for r in results:
        print(json.dumps({k:r[k] for k in ['name','status','first_enter_s','settling_s','t90_s','final_mean_mw','target_mw','overshoot_mw','end_dac','capture_status']},ensure_ascii=False))
    if not traces:return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':11})
    order=sorted(set(m['requested_delta_mw'] for m,t,p,r in traces if m['requested_delta_mw']>0))
    for mode in ['fast','long']:
        selected=[x for x in traces if x[0]['mode']==mode and x[0]['direction']=='out' and x[0]['requested_delta_mw']>0 and x[0]['repeat']==1]
        if not selected:continue
        rows=max(1,(len(order)+2)//3)
        fig,axes=plt.subplots(rows,3,figsize=(15,4*rows),layout='constrained',squeeze=False)
        for ax,amp in zip(axes.flat,order):
            candidates=[x for x in selected if x[0]['requested_delta_mw']==amp]
            if not candidates:ax.set_visible(False);continue
            m,t,p,r=candidates[0]
            scale=1000 if mode=='fast' else 1
            ax.plot(t*scale,p,color='#137a70',lw=.8,label='实测 ADC 换算功率')
            ax.axhline(r['target_mw'],color='#bd6b2d',ls='--',lw=1.2,label='目标')
            ax.axhspan(r['target_mw']-r['tolerance_mw'],r['target_mw']+r['tolerance_mw'],color='#bd6b2d',alpha=.1)
            ax.axvline(0,color='#999999',lw=.8)
            status='阶跃小于判据误差带' if r['inside_tolerance_before_step'] else '到达并保持' if r['status']=='settled' else '到达、未持续收敛' if r['status']=='reached_not_settled' else '未到达目标'
            if r['output_at_limit']:status+=' · DAC 触上限' if r['end_dac']==m['final']['1F'] else ' · DAC 触下限'
            ax.set_title(f'+{amp:g} mW → {r["target_mw"]:.3f} mW\n{status}',fontsize=11)
            ax.set_xlabel('相对 FPGA 目标写入时刻 / '+('ms' if mode=='fast' else 's'))
            ax.set_ylabel('光功率 / mW');ax.grid(alpha=.18)
        for ax in list(axes.flat)[len(order):]:ax.set_visible(False)
        handles,labels=next(a for a in axes.flat if a.get_visible()).get_legend_handles_labels()
        fig.legend(handles,labels,loc='outside lower center',ncol=2,frameon=False)
        fig.suptitle('C07 相对功率阶跃实测 · '+('高速波形' if mode=='fast' else '秒级持续收敛复核')+'\nKp=9，Ki=0.540008545/采样，5 MHz；阴影为目标 ±2%',fontsize=16)
        fig.savefig(OUT/f'{mode}_step_response.png',dpi=180)
        plt.close(fig)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.output:OUT=args.output.resolve()
    main()
