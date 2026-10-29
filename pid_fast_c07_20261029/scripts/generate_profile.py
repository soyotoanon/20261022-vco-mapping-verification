"""Generate startup constants from the explicit retained tuning profile."""
from pathlib import Path
import json
root=Path(__file__).resolve().parents[1]
p=json.loads((root/'boot_profile.json').read_text(encoding='utf-8'))
r=p['registers']
assert r['1A']==0x8000|round(p['kp']*256)
assert r['1B']==0x8000|round(p['ki_per_sample']*32768)
assert p['sample_rate_hz']==50_000_000//(2*(r['11']+1))
assert r['1E']<=r['19']<=r['1F'] and 0<=r['18']<=16383
text='// Generated from boot_profile.json. Registers remain runtime writable.\n'
for macro,addr in [('SETPOINT','18'),('BIAS','19'),('KP','1A'),('KI','1B')]:
    text+=f"`define BOOT_{macro} 16'd{r[addr]}\n"
(root/'rtl/boot_profile.vh').write_text(text,encoding='ascii')
print('PROFILE_PASS',p['name'],f"Kp={p['kp']} Ki={p['ki_per_sample']}")
