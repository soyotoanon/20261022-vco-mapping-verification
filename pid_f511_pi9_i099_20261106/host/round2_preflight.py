"""Read-only check before continuing the measured PI search."""
import json
from bench import UART,console_free,new_run,save,require_lock
from deploy_ram import meter

console_free();out=new_run('round2_preflight');u=UART('COM9',out)
try:
    regs=u.snapshot();value=meter()
    save(out/'registers.json',regs);save(out/'meter.json',value)
    if regs['34']!=0xF511 or regs['11']!=4 or regs['18']!=8414:
        raise RuntimeError('Operating setup differs from the previous qualification.')
    require_lock(regs)
    if not .94<value['power_mw']<1.06:raise RuntimeError('Meter power has changed materially.')
    result=dict(power_mw=value['power_mw'],kp=(regs['1A']&32767)/256,ki=(regs['1B']&32767)/32768,
                status=regs['01'],adc=regs['03'],dac=regs['05'],dac_limits=[regs['1E'],regs['1F']],
                firmware=hex(regs['34']),source=str(out))
    save(out/'result.json',result);print(json.dumps(result),flush=True)
finally:u.close()
