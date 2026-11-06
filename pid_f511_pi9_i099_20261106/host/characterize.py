"""Small fixed-DAC response measurements on F511, using the live meter API."""
import argparse
import json
import time
import numpy as np
from bench import UART,console_free,new_run,save
from deploy_ram import meter

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--codes',required=True)
    args=parser.parse_args()
    codes=[int(x) for x in args.codes.split(',')]
    console_free();out=new_run('fixed_dac_characterization');u=UART('COM9',out);rows=[]
    original=None
    try:
        original=u.snapshot();save(out/'initial_registers.json',original)
        if original['34']!=0xF511 or original['00']!=8 or original['2E']!=0:raise RuntimeError('Expected F511 held output.')
        if any(not original['1E']<=v<=original['1F'] for v in codes):raise ValueError('DAC code outside limits.')
        current=original['05']
        for code in codes:
            if abs(code-current)>1000:raise ValueError('Characterization steps are limited to 1000 DAC codes.')
            u.write(2,code);current=code;time.sleep(.25)
            samples=[]
            for _ in range(12):
                value=meter()
                if not 0<=value['power_mw']<=15:raise RuntimeError('Measured power outside 0–15 mW.')
                samples.append(dict(adc=u.read(3),power_mw=value['power_mw'],sampled_at=value['sampled_at']))
                time.sleep(.035)
            row=dict(dac=code,adc_mean=float(np.mean([v['adc'] for v in samples])),
                adc_std=float(np.std([v['adc'] for v in samples])),
                power_mean_mw=float(np.mean([v['power_mw'] for v in samples])),samples=samples)
            rows.append(row);save(out/'measurements.json',rows)
            print('POINT',json.dumps({k:v for k,v in row.items() if k!='samples'}),flush=True)
    finally:
        try:
            if original and original['34']==0xF511 and original['00']==8 and original['2E']==0:
                u.write(2,original['02'])
                final=u.snapshot();save(out/'final_registers.json',final)
                if final['05']!=original['05']:raise RuntimeError('Saved fixed DAC output was not restored.')
        finally:u.close()
    print('CHARACTERIZATION_RESTORED',out,flush=True)

if __name__=='__main__':main()
