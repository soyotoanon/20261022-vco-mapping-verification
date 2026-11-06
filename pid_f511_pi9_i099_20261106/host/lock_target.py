"""Establish a measured low-power F511 operating point with conservative gains."""
import argparse
import json
import math
import time
import numpy as np
from bench import CONSOLE,UART,console_free,new_run,save,require_lock,ki_word
from deploy_ram import meter

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--power-mw',type=float,default=1.0)
    parser.add_argument('--bias',type=int,default=10000)
    parser.add_argument('--kp',type=float,default=2.0)
    parser.add_argument('--ki',type=float,default=.12)
    args=parser.parse_args()
    if not .1<=args.power_mw<=1.05 or not 4000<=args.bias<=12000 or not 0<args.kp<=12 or not 0<args.ki<1:
        parser.error('This initializer is limited to the measured 0.1–1.05 mW range.')
    cal=json.loads((CONSOLE/'data/calibration.json').read_text(encoding='utf-8-sig'))
    points=sorted((v['power_mw'],v['adc_code']) for v in cal['points'])
    target=math.floor(float(np.interp(args.power_mw,[p[0] for p in points],[p[1] for p in points]))+.5)
    console_free();out=new_run('lock_target');u=UART('COM9',out);original=None
    try:
        original=u.snapshot();save(out/'initial_registers.json',original)
        if original['34']!=0xF511 or original['00']!=8 or original['2E']!=0:raise RuntimeError('Start from measured fixed-output mode.')
        if original['20']!=1 or original['2D']!=0:raise RuntimeError('Unexpected feedback direction/channel.')
        u.write(2,args.bias);time.sleep(.3)
        power=meter();save(out/'fixed_bias_meter.json',power)
        if not 0<power['power_mw']<=1.6:raise RuntimeError('Unexpected optical level at the characterized bias.')
        for a,v in [(0x18,target),(0x19,args.bias),(0x1A,0x8000|round(args.kp*256)),
                    (0x1B,ki_word(args.ki)),(0x1E,545),(0x1F,13000)]:u.write(a,v)
        u.write(0,8|16|256|512|1024);u.write(0,8)
        u.write(0x2E,0xA501);u.write(0,1)
        time.sleep(.25)
        u.write(0,1|512);u.write(0,1)
        rows=[]
        for _ in range(25):
            state=u.live();require_lock(state,tolerance=35,headroom=128)
            value=meter()
            if not 0<value['power_mw']<1.6:raise RuntimeError('Unexpected meter reading after closing loop.')
            rows.append(dict(registers=state,meter=value));time.sleep(.08)
        final=u.snapshot();save(out/'final_registers.json',final);save(out/'telemetry.json',rows)
        summary=dict(target_adc=target,target_mw_historical=args.power_mw,kp=args.kp,ki=(final['1B']&32767)/32768,
                     meter_mean_mw=float(np.mean([r['meter']['power_mw'] for r in rows])),
                     dac_range=[min(r['registers']['05'] for r in rows),max(r['registers']['05'] for r in rows)],
                     monotonic_branch_out_min=545,monotonic_branch_out_max=13000)
        save(out/'result.json',summary);print('LOCK_VERIFIED',json.dumps(summary),flush=True);print('SAVED',out,flush=True)
    except BaseException as error:
        if original:
            try:
                u.write(2,args.bias);u.write(0,8);u.write(0x2E,0)
                for a in [0x18,0x19,0x1A,0x1B,0x1E,0x1F]:u.write(a,original[f'{a:02X}'])
                u.write(2,original['02'])
                save(out/'failure_recovery.json',dict(error=str(error),registers=u.snapshot()))
            except Exception as recovery_error:save(out/'failure_recovery.json',dict(error=str(error),recovery_error=str(recovery_error)))
        raise
    finally:u.close()

if __name__=='__main__':main()
