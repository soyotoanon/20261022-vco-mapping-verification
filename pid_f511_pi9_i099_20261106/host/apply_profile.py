"""Retain a validated gain profile at its measured operating point; never write flash."""
import argparse
import json
import time
from pathlib import Path
from bench import UART, console_free, new_run, save, require_lock,ki_word
from deploy_ram import meter


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile',type=Path)
    args=parser.parse_args()
    profile=json.loads(args.profile.read_text(encoding='utf-8'))
    kp,ki=profile['kp'],profile['ki']
    if not 0<kp<=20 or not 0<ki<1:raise ValueError('Invalid qualified gain values.')
    console_free();out=new_run('profile_application');u=UART('COM9',out);original=None;success=False
    try:
        original=u.snapshot();save(out/'initial_registers.json',original)
        if original['34']!=0xF511 or original['3F']!=0xA624 or original['11']!=4:
            raise RuntimeError('Unexpected firmware or sample rate.')
        if original['30'] or original['36']&1 or original['20']!=1 or original['2D']!=0:
            raise RuntimeError('Unexpected active probe or feedback routing.')
        if original['18']!=profile['target_adc'] or [original['1E'],original['1F']]!=profile['dac_limits']:
            raise RuntimeError('This profile was not qualified at the current operating configuration.')
        require_lock(original)
        u.write(0x1B,ki_word(ki));u.write(0x1A,0x8000|round(kp*256))
        records=[];deadline=time.monotonic()+15;last=None
        while time.monotonic()<deadline:
            state=u.live();require_lock(state)
            value=meter()
            if abs(value['power_mw']-profile['nominal_power_mw'])>.05:
                raise RuntimeError('Meter power no longer matches this qualification point.')
            if value['sampled_at']!=last:
                records.append(dict(registers=state,meter=value));last=value['sampled_at']
            time.sleep(.1)
        final=u.snapshot()
        if final['1A']!=(0x8000|round(kp*256)) or final['1B']!=ki_word(ki):
            raise RuntimeError('Gain readback did not match.')
        save(out/'telemetry.json',records);save(out/'final_registers.json',final)
        result=dict(kp=(final['1A']&32767)/256,ki=(final['1B']&32767)/32768,target_adc=final['18'],
                    mean_power_mw=sum(r['meter']['power_mw'] for r in records)/len(records),
                    final_status=final['01'],retained_in_ram=True,flash_written=False,
                    profile_source=str(args.profile.resolve()))
        save(out/'result.json',result);success=True
        print('PROFILE_APPLIED',json.dumps(result),flush=True);print('SAVED',out,flush=True)
    finally:
        if original and not success:
            u.write(0x1B,original['1B']);u.write(0x1A,original['1A'])
            save(out/'rollback.json',u.snapshot())
        u.close()


if __name__=='__main__':main()
