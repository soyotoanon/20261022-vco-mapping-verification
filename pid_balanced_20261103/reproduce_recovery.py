"""Recompute physical shutter metrics without instruments: pip install numpy."""
from pathlib import Path
import json,numpy as np
folder=Path(__file__).resolve().parent/'results/recovery_current'
summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
record=json.loads((folder/'slow_record.json').read_text(encoding='utf-8'))
meta=json.loads((folder/'capture.json').read_text(encoding='utf-8'))
adc=np.load(folder/'pd1_raw.npy').astype(float)/4
dt=meta['sample_interval_s'];dark=float(np.median(adc[:500]))
baseline=float(np.median([r['adc'] for r in record['rows'] if r['elapsed_s']<record['capture_start_s']-.3]))
y=(adc-dark)/(baseline-dark)
i10=int(np.flatnonzero(y>=.1)[0]);i90=int(np.flatnonzero(y>=.9)[0]);stable=int(np.flatnonzero(np.abs(y-1)>.02)[-1])+1
assert stable>=i90 and (len(y)-1-stable)*dt>=.1
result={'observed_rise_10_90_ms':(i90-i10)*dt*1000,'observed_settling_from_10percent_ms':(stable-i10)*dt*1000}
for k,v in result.items():assert abs(v-summary[k])<1e-9
print('RAW_RECOMPUTE_PASS',json.dumps(result))
