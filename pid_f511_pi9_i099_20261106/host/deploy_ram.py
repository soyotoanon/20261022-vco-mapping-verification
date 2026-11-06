"""Load verified F511 into RAM, then hold the saved DAC code for characterization."""
import hashlib
import json
import subprocess
import time
import urllib.request

from bench import ROOT, UART, console_free, new_run, save

def meter():
    with urllib.request.urlopen('http://127.0.0.1:8766/api/state?channel=556',timeout=4) as response:
        value=json.load(response)['state']['meter']
    if not value.get('connected') or time.time()-value['sampled_at']/1000 > 2:
        raise RuntimeError('Power meter data is unavailable or stale.')
    if value['identity']['serial'] != 'P2007293' or value['identity']['wavelength_nm'] != 556:
        raise RuntimeError('Unexpected power meter identity/wavelength.')
    return value

def main():
    console_free()
    out=new_run('f511_ram_deployment')
    build=json.loads((ROOT/'candidate/build_summary.json').read_text())
    tests=json.loads((ROOT/'candidate/build/verification/results.json').read_text())
    expected={'tb_fast_equivalence','tb_pi_revision','tb_performance_probe','tb_sample_revision',
              'tb_dac_timing_revision','tb_instant_display_revision','tb_dac_guard','tb_dac_routes',
              'tb_reg_file_equivalence'}
    if {t['test'] for t in tests}!=expected or not all(t['result']=='PASS' for t in tests):
        raise RuntimeError('All nine RTL checks, including register equivalence, are required.')
    if build['wns_ns']<0 or build['whs_ns']<0:raise RuntimeError('Timing not met.')
    bit=ROOT/'candidate/bitstream/light_lock_top.bit'
    if hashlib.sha256(bit.read_bytes()).hexdigest()!=build['bit_sha256']:raise RuntimeError('Bitstream hash mismatch.')
    u=UART('COM9',out)
    try:
        initial=u.snapshot();save(out/'before.json',initial)
        if initial['34'] not in (0xF129,0xF511) or initial['3F']!=0xA624:raise RuntimeError('Unexpected FPGA version.')
        if initial['30']!=0 or initial['36']&1:raise RuntimeError('Probe operation active.')
        held=initial['05']
        if not 545<=held<=15851:raise RuntimeError('Saved DAC code outside F511 range.')
        value=meter();save(out/'meter_before.json',value)
        if not 0<=value['power_mw']<=15:raise RuntimeError('Optical power outside authorized range.')
    finally:u.close()
    with (out/'program_console.log').open('w',encoding='utf-8') as log:
        p=subprocess.run([r'D:\Xilinx\Vivado\2023.2\bin\vivado.bat','-mode','batch','-notrace',
            '-source',str(ROOT/'host/program_candidate_ram.tcl'),'-log',str(out/'vivado.log'),
            '-journal',str(out/'vivado.jou')],cwd=out,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    log=(out/'program_console.log').read_text(encoding='utf-8',errors='replace')
    if p.returncode or 'RAM_DONE=1' not in log:
        raise RuntimeError('RAM programming not verified: '+log[-2000:])
    u=UART('COM9',out)
    try:
        boot=u.snapshot();save(out/'boot_before_control_writes.json',boot)
        if boot['34']!=0xF511 or boot['1E']!=545 or boot['1F']!=15851:raise RuntimeError('F511 boot readback mismatch.')
        u.write(2,held);u.write(0,8);u.write(0x2E,0)
        u.write(0,8|16|512);u.write(0,8)
        final=u.snapshot();save(out/'after.json',final)
        if final['00']!=8 or final['2E']!=0 or final['05']!=held:raise RuntimeError('Fixed-output readback mismatch.')
        value=meter();save(out/'meter_after.json',value)
        save(out/'result.json',dict(ram_programmed=True,flash_written=False,firmware='F511',fixed_dac=held,
            bit_sha256=build['bit_sha256'],power_mw=value['power_mw']))
        print('F511_RAM_VERIFIED',json.dumps(dict(fixed_dac=held,power_mw=value['power_mw'],status=final['01'],out_min=final['1E'],out_max=final['1F'])),flush=True)
        print('SAVED',out,flush=True)
    finally:u.close()

if __name__=='__main__':main()
