from three_common import *
OUT=OUT.parent/"balanced_followup"
OUT.mkdir(exist_ok=True)
for m in [pd,hw,ls]:m.OUT=OUT
import time,json,os,subprocess,inspect,numpy as np
STEP_SRC=Path(r'D:\DSproject\workbench\tasks\pid-step-response-20261028\f129_step_round_20261029\host')
os.environ['PID_STEP_OUTPUT']=str(OUT/'steps_stability07')
sys.path.insert(0,str(STEP_SRC))
import run_measurement as r
r.OUT=OUT/'steps_stability07';r.OUT.mkdir(exist_ok=True)
r.FW=Path(r'D:\DSproject\workbench\tasks\pid-step-response-20261028\firmware_f129_timestamp')
r.STOCK=Path(r'D:\DSproject\workbench\tasks\pid-fast-c07-20261029')
# The hardware still acquires 8192 samples; download the first 4096 continuous
# samples to halve UART transfer time. Their actual time window is analyzed.
capture_src=inspect.getsource(r.capture).replace('np.empty(8192,dtype=np.uint16)','np.empty(4096,dtype=np.uint16)').replace('range(0,8192,64)','range(0,4096,64)')
exec(capture_src,r.__dict__)
class Session:
    def __init__(self):self.port='COM9';self.u=None
    def __enter__(self):
        state=r.api()['state'];assert state['fpga']['connected'] and not state.get('lock_comparison',{}).get('running')
        r.api('/api/disconnect/fpga',{});self.u=pd.SerialUART(self.port);return self
    def __exit__(self,*args):
        if self.u:self.u.close()
        r.api('/api/connect/fpga',{'port':self.port})

def program(s,source,label,desired):
    held=s.u.read(5);temp=desired.copy();temp['19']=held
    original_run=subprocess.run
    def local_run(*args,**kwargs):
        kwargs['cwd']=str(OUT)
        return original_run(*args,**kwargs)
    subprocess.run=local_run
    try:r.program(s,source,label,temp)
    finally:subprocess.run=original_run
    bias=held
    while bias!=desired['19']:
        bias+=max(-64,min(64,desired['19']-bias));r.write(s.u,0x19,bias);time.sleep(.01)

with Session() as s:
    initial=r.snapshot(s.u);assert initial['1A']==35584 and initial['1B']==56361;r.save('initial.json',initial)
    programmed=False
    try:
        programmed=True;program(s,r.FW,'timestamp_observer',initial)
        assert r.marker(s.u)[0] in (True,False)
        base=initial['18'];p0=r.CAL.to_power(base)
        for cfg in [CONFIGS[1]]:
            for a,v in [(0x1B,0x8000|round(cfg['ki']*32768)),(0x1A,0x8000|round(cfg['kp']*256)),(0x2C,16383)]:r.write(s.u,a,v)
            time.sleep(.3)
            for amp in [.2,.5,1.,2.]:
                target=r.CAL.to_code(p0+amp)
                for mode,decim,pre in [('fast',2,0),('long',11,.2)]:
                    for direction,new,old in [('out',target,base),('return',base,target)]:
                        name=f"{cfg['name']}_{mode}_plus{amp:g}mW_{direction}"
                        raw,m=r.capture(s.u,decim,new,pre_s=pre)
                        final=r.snapshot(s.u)
                        m.update(name=name,mode=mode,direction=direction,configuration=cfg,requested_delta_mw=amp,end_target_code=new,start_target_code=old,expected_step_mw=r.CAL.to_power(new)-r.CAL.to_power(old),final=final)
                        np.save(r.OUT/(name+'_raw.npy'),raw);r.save(name+'.json',m)
                        print('STEP',name,'flags',m['capture_status'],'ADC',final['03'],'DAC',final['05'],flush=True)
                        if final['01']&0x29:raise RuntimeError('Light-loss or freeze; stop steps')
                    r.write(s.u,0x18,base);time.sleep(.2)
    finally:
        if s.u:
            r.write(s.u,0x18,initial['18']);r.write(s.u,0x34,2)
        if programmed:program(s,r.STOCK,'restore_F129',initial)
        r.save('final_restored.json',r.snapshot(s.u))
