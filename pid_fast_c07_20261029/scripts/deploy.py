"""Program the verified C07 build; preserve live target and mode by default.

Flash mode backs up all 16 MiB and verifies programming before booting it.
The reported startup-gain readback happens BEFORE any gain register writes.
"""
from pathlib import Path
from datetime import datetime
import argparse, hashlib, json, subprocess, sys, time
import requests
root=Path(__file__).resolve().parents[1]
console=Path(r'D:\DSproject\workbench\sites\mot556-console')
sys.path.insert(0,str(console/'vendor'))
import serial

def api(path='/api/state',body=None):
    url='http://127.0.0.1:8766'+path+'?channel=556'
    r=requests.get(url,timeout=10) if body is None else requests.post(url,json=body,headers={'X-MOT-Console':'1'},timeout=10)
    r.raise_for_status();return r.json()

class UART:
    def __init__(self,port):
        self.s=serial.Serial(port=None,baudrate=115200,timeout=.05,write_timeout=2)
        self.s.dtr=False;self.s.rts=False;self.s.port=port;self.s.open()
    def send(self,cmd,addr,value=0):
        raw=bytes([197,cmd,addr,value&255,value>>8,92,197,197])
        if self.s.write(raw)!=8:raise IOError('Incomplete UART frame')
    def read(self,addr):
        self.send(2,addr);deadline=time.monotonic()+2;buf=bytearray()
        while time.monotonic()<deadline:
            buf.extend(self.s.read(max(1,min(4096,self.s.in_waiting))))
            while len(buf)>=8:
                if buf[0]!=197 or buf[5:8]!=bytes([92,197,197]):del buf[0];continue
                f=bytes(buf[:8]);del buf[:8]
                if f[1]==2 and f[2]==addr:return f[3]+256*f[4]
        raise TimeoutError(f'Register {addr:02X} did not respond')
    def write(self,addr,value):
        self.send(1,addr,value);expect=(1 if value==0xA501 else 0) if addr==0x2E else value
        got=self.read(addr)
        if got!=expect:raise RuntimeError(f'{addr:02X}: {got} != {expect}')
    def snapshot(self):return {f'{a:02X}':self.read(a) for a in range(64)}
    def close(self):self.s.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['ram','flash'],required=True)
    p.add_argument('--after',choices=['preserve','closed-loop','fixed'],default='preserve')
    p.add_argument('--fixed-code',type=int)
    args=p.parse_args()
    if (args.after=='fixed') != (args.fixed_code is not None):
        p.error('--after fixed requires --fixed-code, which is only valid in fixed mode')
    build=json.loads((root/'build_summary.json').read_text())
    tests=json.loads((root/'build/verification/results.json').read_text())
    assert len(tests)==6 and all(t['result']=='PASS' for t in tests)
    assert build['wns_ns']>=0 and build['whs_ns']>=0
    for suffix,key in [('bit','bit_sha256'),('mcs','mcs_sha256')]:
        assert hashlib.sha256((root/f'bitstream/light_lock_top.{suffix}').read_bytes()).hexdigest()==build[key]
    profile=json.loads((root/'boot_profile.json').read_text(encoding='utf-8'))
    if args.fixed_code is not None:
        assert profile['registers']['1E']<=args.fixed_code<=profile['registers']['1F']
    folder=root/'deployment'/datetime.now().strftime('%Y%m%d-%H%M%S');folder.mkdir(parents=True)
    def save(name,data):(folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    initial=api()['state'];save('service-before.json',initial)
    f=initial['fpga'];assert f['connected'] and not f.get('ramp')
    port=f['port'];u=None;before=None;programmed=False;held=None
    api('/api/disconnect/fpga',{})
    try:
        u=UART(port);before=u.snapshot();save('registers-before.json',before)
        assert before['2F']==0xD216 and before['3F'] in (0xA623,0xA624)
        assert not(before['30']&3) and not(before['36']&1), 'Measurement in progress'
        assert before['00'] in (1,8), 'Unsupported current control mode'
        held=args.fixed_code if args.after=='fixed' else before['05']
        save('requested-final-state.json',{'after':args.after,'fixed_dac':held if args.after=='fixed' else None})
        u.close();u=None
        script=root/'scripts'/('program_flash.tcl' if args.mode=='flash' else 'program_ram.tcl')
        with (folder/'program-console.log').open('w',encoding='utf-8') as h:
            result=subprocess.run([r'D:\Xilinx\Vivado\2023.2\bin\vivado.bat','-mode','batch','-notrace','-source',str(script),'-log',str(folder/'vivado.log'),'-journal',str(folder/'vivado.jou')],cwd=root,stdout=h,stderr=subprocess.STDOUT,timeout=600)
        log=(folder/'program-console.log').read_text(encoding='utf-8',errors='replace')
        marker='FLASH_BOOT_DONE=1' if args.mode=='flash' else 'RAM_DONE=1'
        if result.returncode or marker not in log:raise RuntimeError(log[-4000:])
        programmed=True;u=UART(port)
        boot={f'{a:02X}':u.read(a) for a in [0x1A,0x1B,0x18,0x19,0x11,0x2F,0x34,0x38,0x3F]}
        save('startup-before-any-gain-writes.json',boot)
        assert boot['1A']==35072 and boot['1B']==50463 and boot['11']==4
        assert boot['34']==0xF129 and boot['3F']==0xA624 and boot['38']==0x5031
        u.write(2,held);u.write(0,8);u.write(0x2E,0)
        # Retained gains/bias/bounds, live target/channel/slew; never replay old gains.
        desired={int(k,16):v for k,v in profile['registers'].items()}
        for a in [0x18,0x2C,0x2D]:desired[a]=before[f'{a:02X}']
        for a,v in desired.items():
            if u.read(a)!=v:u.write(a,v)
        u.write(0,8|16);u.write(0,8)
        close_loop=args.after=='closed-loop' or (args.after=='preserve' and before['00']==1 and before['2E']==1)
        if close_loop:u.write(0x2E,0xA501);u.write(0,1)
        rows=[]
        for _ in range(30):
            rows.append({f'{a:02X}':u.read(a) for a in [0,1,3,5,0x18,0x1A,0x1B,0x2E]});time.sleep(.1)
        after=u.snapshot();save('registers-after.json',after);save('telemetry.json',rows)
        assert all(after[f'{a:02X}']==v for a,v in desired.items())
        assert after['00']==(1 if close_loop else 8) and after['2E']==(1 if close_loop else 0)
        if not close_loop:
            assert after['02']==held and after['05']==held
            assert all(r['00']==8 and r['2E']==0 and r['05']==held for r in rows)
        if close_loop:assert not(after['01']&0x3D), 'Live loss/rail/integrator/freeze/clock fault'
        save('result.json',{'passed':True,'mode':args.mode,'after':args.after,'startup_gains_read_before_any_gain_writes':True,'profile':profile['name'],'flash_written':args.mode=='flash','bit_sha256':build['bit_sha256'],'final_ctrl':after['00'],'final_arm':after['2E'],'fixed_dac':held if not close_loop else None,'target_adc':after['18']})
        print('DEPLOY_PASS',folder,flush=True)
    except BaseException as exc:
        recovery={}
        try:
            if u is None:u=UART(port)
            if held is not None:
                u.write(2,held);u.write(0,8);u.write(0x2E,0)
                recovery={'fixed_dac':u.read(5),'ctrl':u.read(0),'arm':u.read(0x2E)}
        except Exception as recovery_error:recovery={'state':'unknown','error':str(recovery_error)}
        save('failure.json',{'error':str(exc),'programmed':programmed,'recovery':recovery})
        raise
    finally:
        if u:u.close()
        api('/api/connect/fpga',{'port':port})
        save('service-after.json',api()['state'])

if __name__=='__main__':main()
