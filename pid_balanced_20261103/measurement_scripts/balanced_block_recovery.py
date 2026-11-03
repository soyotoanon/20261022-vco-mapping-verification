"""Physical block/release on PD1, F129 observer + independent power meter."""
from three_common import *
import requests, numpy as np, time, json
OUT=OUT.parent/'balanced_followup'/'recovery_current'
OUT.mkdir(parents=True,exist_ok=True)
for m in [pd,hw,ls]:m.OUT=OUT
c=requests.Session();c.headers['X-MOT-Console']='1'
base='http://127.0.0.1:8766'
def api(route,body=None):
    r=c.get(base+route+'?channel=556',timeout=5) if body is None else c.post(base+route+'?channel=556',json=body,timeout=5)
    r.raise_for_status();return r.json()
state=api('/api/state')['state']
assert state['fpga']['connected'] and state['meter']['connected']
assert not state.get('lock_comparison',{}).get('running')
api('/api/disconnect/fpga',{})
rows=[];last_meter=None;capture_start=None
try:
    with hw.Board('physical_recovery') as b:
        assert b.original['1A']==35584 and b.original['1B']==56361
        start=time.monotonic()
        def sample():
            global last_meter
            t=time.monotonic()-start
            row=dict(elapsed_s=t,adc=b.u.read(3),dac=b.u.read(5),status=b.u.read(1))
            meter=api('/api/state')['state']['meter']
            if meter['sampled_at']!=last_meter:
                row.update(power_mw=meter['power_mw'],meter_timestamp_ms=meter['sampled_at'])
                last_meter=meter['sampled_at']
            rows.append(row)
            with (OUT/'live.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
            return row
        for _ in range(30):sample();time.sleep(.03)
        b.wr(0x35,12)
        print('READY_TO_BLOCK: P11 I0.72000122; block upstream PD1 for about 2 seconds then release.',flush=True)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            row=sample()
            if row['adc']<8400:
                capture_start=time.monotonic()-start
                b.wr(0x34,1)
                print('DARK_DETECTED_CAPTURE_STARTED',capture_start,flush=True)
                break
            time.sleep(.02)
        if capture_start is None:raise TimeoutError('No physical dark event in 180 seconds')
        while not (b.u.read(0x36)&2):
            sample();time.sleep(.025)
            if time.monotonic()-start-capture_start>10:raise TimeoutError('Observer completion')
        flags=b.u.read(0x36);count=b.u.read(0x3e);seq=b.u.read(0x3d)
        assert count==8192 and flags&2
        end=time.monotonic()+5
        while time.monotonic()<end:sample();time.sleep(.03)
        hw.save('slow_record.json',dict(rows=rows,capture_start_s=capture_start,configuration=CONFIGS[1],meter_identity=state['meter']['identity']))
        raw=np.empty(8192,dtype=np.uint16)
        for offset in range(0,8192,64):
            payload=b''.join(b.u.frame(1,0x37,j)+b.u.frame(2,0x3b) for j in range(offset,offset+64))
            b.u.send(payload);reply=bytearray();deadline=time.monotonic()+3
            while len(reply)<512:
                reply.extend(b.u.serial.read(512-len(reply)))
                if time.monotonic()>deadline:raise TimeoutError('Observer transfer')
            for j in range(64):
                packet=reply[j*8:(j+1)*8]
                assert packet[:3]==bytes([197,2,0x3b]) and packet[5:]==bytes([92,197,197])
                raw[offset+j]=packet[3]|(packet[4]<<8)
        assert b.u.read(0x3d)==seq
        np.save(OUT/'pd1_raw.npy',raw)
        hw.save('capture.json',dict(configuration=CONFIGS[1],sample_interval_s=4096/5e6,samples=8192,flags=flags,sequence=seq,capture_start_s=capture_start,encoding='ADC code times 4; third-order CIC, decimation 4096',note='Dark event can set sticky fault flag. This records mechanical shutter recovery with about 0.8192 ms sample spacing and CIC smoothing; not intrinsic microsecond PI latency.',after=hw.snap(b.u)))
        print('RECOVERY_SAVED',len(raw),'samples; flags',flags,flush=True)
finally:
    api('/api/connect/fpga',{'port':'COM9'})
