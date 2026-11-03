"""Inspect the console; --apply writes only the measured P11/I gain pair."""
from pathlib import Path
import argparse,json,time,requests

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--url',default='http://127.0.0.1:8766');args=parser.parse_args()
    profile=json.loads((ROOT/'live_profile.json').read_text(encoding='utf-8'))
    assert profile['gain_registers']['1A']==0x8000|round(profile['kp']*256)
    assert profile['gain_registers']['1B']==0x8000|round(profile['ki_per_sample']*32768)
    client=requests.Session();client.headers['X-MOT-Console']='1'
    def api(route,body=None):
        url=args.url.rstrip('/')+route+'?channel=556'
        r=client.get(url,timeout=5) if body is None else client.post(url,json=body,timeout=5)
        r.raise_for_status();return r.json()
    state=api('/api/state')['state'];fpga=state['fpga']
    print(json.dumps({'current':fpga,'selected_profile':profile},ensure_ascii=False,indent=2))
    if not args.apply:return
    if not fpga['connected'] or not fpga['pi_enabled'] or fpga.get('ramp') or state.get('lock_comparison',{}).get('running'):
        raise RuntimeError('Connect the FPGA with the normal closed loop active and finish other measurements first.')
    import serial
    port=fpga['port'];api('/api/disconnect/fpga',{});s=None
    try:
        s=serial.Serial(port=None,baudrate=115200,timeout=.05,write_timeout=2);s.dtr=False;s.rts=False;s.port=port;s.open()
        def frame(cmd,addr,value=0):return bytes([197,cmd,addr,value&255,value>>8,92,197,197])
        def read(addr):
            s.write(frame(2,addr));deadline=time.monotonic()+2;buf=bytearray()
            while time.monotonic()<deadline:
                buf.extend(s.read(max(1,s.in_waiting)))
                while len(buf)>=8:
                    if buf[:3]!=bytes([197,2,addr]) or buf[5:8]!=bytes([92,197,197]):del buf[0];continue
                    return buf[3]|buf[4]<<8
            raise TimeoutError(f'Register {addr:02X}')
        def write(addr,value):
            if s.write(frame(1,addr,value))!=8:raise IOError('Incomplete write')
            if read(addr)!=value:raise IOError(f'Register {addr:02X} readback mismatch')
        if read(0x34)!=0xF129 or read(0x3f)!=0xA624:raise RuntimeError('Expected F129/A624')
        if read(0x30)!=0 or read(0x23)!=0 or read(0x36)&1:raise RuntimeError('Measurement/injection active')
        for addr,value in profile['workpoint_registers'].items():
            if read(int(addr,16))!=value:raise RuntimeError(f'Workpoint {addr} differs; set the documented workpoint using the console first. No gains written.')
        old={a:read(a) for a in [0x1b,0x1a]}
        try:
            for addr in [0x1b,0x1a]:write(addr,profile['gain_registers'][f'{addr:02X}'])
        except Exception:
            for addr,value in old.items():write(addr,value)
            raise
        print('P11_PROFILE_APPLIED_AND_READ_BACK',read(0x1a),read(0x1b))
    finally:
        if s:s.close()
        api('/api/connect/fpga',{'port':port})

if __name__=='__main__':main()
