"""Check target changes, shutdown/recovery and a sustained lock; restore on exit.

This does not install gains permanently. All original target, mode, fixed output,
arming and probe settings are restored, including after a failed check.
"""
import argparse
import json
import math
import time
import numpy as np
from bench import CONSOLE, ProbeSession, new_run, save
from deploy_ram import meter


def target_code(power):
    cal=json.loads((CONSOLE/'data/calibration.json').read_text(encoding='utf-8-sig'))
    knots=sorted((v['power_mw'],v['adc_code']) for v in cal['points'])
    return math.floor(float(np.interp(power,[k[0] for k in knots],[k[1] for k in knots]))+.5)


def telemetry(board, seconds, expected, check_lock=True):
    readings=[];last_stamp=None;deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        state=board.check() if check_lock else board.u.live()
        value=meter()
        if not 0<=value['power_mw']<=1.6:
            raise RuntimeError('Power outside the characterized range.')
        if value['sampled_at']!=last_stamp:
            readings.append(dict(registers=state,meter=value));last_stamp=value['sampled_at']
        time.sleep(.1)
    powers=np.array([r['meter']['power_mw'] for r in readings])
    if len(powers)<5:raise RuntimeError('Too few fresh meter samples.')
    if expected is not None and abs(powers.mean()-expected)>max(.04,expected*.1):
        raise RuntimeError('Power meter does not match the requested operating point.')
    summary=dict(samples=len(powers),mean_mw=float(powers.mean()),
                 std_mw=float(powers.std()),peak_to_peak_mw=float(np.ptp(powers)),
                 dac_min=min(r['registers']['05'] for r in readings),
                 dac_max=max(r['registers']['05'] for r in readings),
                 elapsed_meter_seconds=(readings[-1]['meter']['sampled_at']-readings[0]['meter']['sampled_at'])/1000)
    if powers.mean()>.01:
        summary['rms_percent_of_actual_mean']=float(powers.std()/powers.mean()*100)
    return dict(summary=summary,readings=readings)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kp',type=float,required=True)
    parser.add_argument('--ki',type=float,required=True)
    parser.add_argument('--hold-seconds',type=float,default=60)
    args=parser.parse_args()
    if not 0<args.kp<=20 or not 0<args.ki<1 or not 10<=args.hold_seconds<=120:
        parser.error('Invalid validation profile.')
    out=new_run('profile_validation')
    with ProbeSession(out) as board:
        try:
            board.gains(args.kp,args.ki)
            save(out/'requested_profile.json',vars(args))
            board.u.write(0x18,target_code(1.0));time.sleep(.5)
            for i,power in enumerate([.6,.3,.6,1.0]):
                previous=board.u.read(0x18);target=target_code(power)
                def change_target():
                    board.u.write(0x18,target)
                    return dict(previous_target_adc=previous,new_target_adc=target,
                                requested_mw_historical_calibration=power,
                                trigger='UART write shortly after capture start; locate edge in ADC waveform')
                raw,meta=board.capture(5,8192,after_start=change_target)
                np.savez_compressed(out/f'{i:02d}_step.npz',raw=raw)
                save(out/f'{i:02d}_step.json',meta)
                time.sleep(.5)
                reading=telemetry(board,4,power)
                save(out/f'{i:02d}_steady.json',reading)
                print('SETPOINT_CHECK',json.dumps(dict(target_mw=power,**reading['summary'])),flush=True)
            # Fixed-output minimum makes shutdown independent of the dark ADC offset.
            current=board.u.read(5)
            board.u.write(2,current);board.u.write(0,8);board.u.write(0x2E,0)
            board.u.write(2,board.original['1E']);time.sleep(.5)
            dark=telemetry(board,3,None,check_lock=False)
            if dark['summary']['mean_mw']>.01:
                raise RuntimeError('Shutdown residual exceeds 0.01 mW in this setup.')
            save(out/'shutdown.json',dark)
            board.u.write(2,board.original['02'])
            board.u.write(0x2E,0xA501);board.u.write(0,1)
            time.sleep(.5)
            recovered=telemetry(board,5,1.0)
            save(out/'recovery.json',recovered)
            print('SHUTDOWN_RECOVERY',json.dumps(dict(shutdown_mw=dark['summary']['mean_mw'],
                  recovered_mw=recovered['summary']['mean_mw'])),flush=True)
            stable=telemetry(board,args.hold_seconds,1.0)
            save(out/'sustained_lock.json',stable)
            print('SUSTAINED_LOCK',json.dumps(stable['summary']),flush=True)
        finally:
            board.quiet()
            board.u.write(0x18,board.original['18'])
            board.u.write(2,board.original['02'])
            board.u.write(0x2E,0xA501)
            board.u.write(0,board.original['00'])
            time.sleep(.3)
            board.check()
    print('PROFILE_VALIDATION_RESTORED',out,flush=True)


if __name__=='__main__':main()
