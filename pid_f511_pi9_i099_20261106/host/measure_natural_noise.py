"""Compare held/closed in-loop ADC noise and meter slow fluctuations at a fixed target."""
import argparse
import json
import time
import numpy as np
from scipy.signal import welch
from bench import ProbeSession,CONSOLE,new_run,save
from deploy_ram import meter

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gains',required=True)
    args=parser.parse_args()
    pairs=[tuple(map(float,s.split(':'))) for s in args.gains.split(',')]
    if any(len(p)!=2 or not 0<p[0]<=30 or not 0<p[1]<1 for p in pairs):parser.error('Invalid gains.')
    out=new_run('natural_noise');rows=[]
    calibration=json.loads((CONSOLE/'data/calibration.json').read_text(encoding='utf-8-sig'))
    knots=sorted((v['adc_code'],v['power_mw']) for v in calibration['points'])
    with ProbeSession(out) as board:
        for trial,(kp,ki) in enumerate(pairs):
            board.gains(kp,ki)
            for held in [True,False]:
                state='held' if held else 'closed'
                for decim in [8,3]:
                    name=f'{trial:02d}_{state}_d{decim}'
                    try:
                        board.quiet();board.check()
                        if held:board.u.write(0x30,6)
                        time.sleep(.1)
                        raw,meta=board.capture(decim,8192)
                    finally:board.quiet()
                    adc=raw.astype(float)/4
                    power=np.interp(adc,[k[0] for k in knots],[k[1] for k in knots])
                    mean=float(np.mean(power))
                    if mean<=0:raise RuntimeError('Relative noise denominator is not positive.')
                    f,psd=welch(power/mean,fs=meta['fs_hz'],nperseg=2048,detrend='constant')
                    ratio=meta['decimation'];omega=2*np.pi*f/meta['raw_fs_hz']
                    transfer=np.ones_like(f)
                    transfer[1:]=abs(np.sin(omega[1:]*ratio/2)/(ratio*np.sin(omega[1:]/2)))**3
                    psd=psd/(transfer*transfer)
                    bands=[(10,1000)] if decim==8 else [(1000,10000),(10000,60000),(1000,60000),
                                                     (60000,120000),(10000,120000),(1000,120000)]
                    metrics={}
                    for lo,hi in bands:
                        ix=(f>=lo)&(f<=hi)
                        metrics[f'{lo}_{hi}_hz_rms_percent']=float(100*np.sqrt(np.sum(psd[ix])*(f[1]-f[0])))
                    record=dict(trial=trial,kp=kp,ki=ki,state=state,decimation_log2=decim,mean_mw_historical_calibration=mean,
                                bands=metrics,measurement='in_loop_ADC_not_out_of_loop_RIN',**meta)
                    np.savez_compressed(out/(name+'.npz'),raw=raw,frequency_hz=f,rin_psd_per_hz=psd)
                    save(out/(name+'.json'),record);rows.append(record);save(out/'summary.json',rows)
                    print('NOISE',json.dumps(dict(kp=kp,ki=ki,state=state,**metrics)),flush=True)
            readings=[];deadline=time.monotonic()+12;last_stamp=None
            while time.monotonic()<deadline:
                board.check();value=meter()
                if value['sampled_at']!=last_stamp:
                    readings.append(value);last_stamp=value['sampled_at']
                time.sleep(.1)
            powers=np.array([v['power_mw'] for v in readings])
            result=dict(kp=kp,ki=ki,duration_s=12,readings=readings,mean_mw=float(powers.mean()),
                        rms_percent=float(powers.std()/powers.mean()*100),
                        peak_to_peak_percent=float(np.ptp(powers)/powers.mean()*100))
            save(out/f'{trial:02d}_meter.json',result)
            print('METER',json.dumps({k:v for k,v in result.items() if k!='readings'}),flush=True)
    print('NOISE_COMPARISON_RESTORED',out,flush=True)

if __name__=='__main__':main()
