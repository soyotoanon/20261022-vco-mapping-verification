import sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,r'C:\Users\浩宇\Documents\Codex\2026-11-03\wo\work')
import pd2_tools as pd,loop_response_hw as hw,loop_scope as ls
OUT=Path(__file__).resolve().parents[2]/'outputs/pi_three_20261103'
OUT.mkdir(parents=True,exist_ok=True)
for m in [pd,hw,ls]:m.OUT=OUT
CONFIGS=[dict(name='baseline',label='C07',kp=9,ki=17695/32768),dict(name='balanced',label='P11 I0.720001',kp=11,ki=round(.72*32768)/32768),dict(name='fast',label='P12 I0.779999',kp=12,ki=round(.78*32768)/32768)]
def basic_check(s,mode=0,tolerance=30):
    assert s['00']==1 and s['2E']==1 and s['3F']==0xA624 and s['34']==0xF129
    assert s['11']==4 and s['23']==0 and s['30']==mode
    assert s['18']==8840 and s['1E']==1400 and s['1F']==15000
    assert s['01']&0x29==0,'Light-loss, freeze or clock fault'
    assert s['1E']<=s['05']<=s['1F']
    if mode==0:assert abs(s['03']-s['18'])<tolerance,'Target not tracked'
hw.check=basic_check
