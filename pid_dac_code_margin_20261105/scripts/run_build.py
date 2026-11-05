from pathlib import Path
import subprocess, re, hashlib, json
root=Path(__file__).resolve().parents[1]
with (root/'build-console.log').open('w',encoding='utf-8') as h:
    p=subprocess.run([r'D:\Xilinx\Vivado\2023.2\bin\vivado.bat','-mode','batch','-notrace','-source','scripts/build.tcl','-log','build-vivado.log','-journal','build-vivado.jou'],cwd=root,stdout=h,stderr=subprocess.STDOUT,timeout=1200)
s=(root/'build-console.log').read_text(encoding='utf-8',errors='replace')
if p.returncode or 'BUILD_PASS' not in s:raise RuntimeError(s[-5000:])
summary={'exit_code':p.returncode,'bit_sha256':hashlib.sha256((root/'bitstream/light_lock_top.bit').read_bytes()).hexdigest(),'mcs_sha256':hashlib.sha256((root/'bitstream/light_lock_top.mcs').read_bytes()).hexdigest(),'wns_ns':float(re.search(r'FINAL_WNS=([\d.-]+)',s).group(1)),'whs_ns':float(re.search(r'FINAL_WHS=([\d.-]+)',s).group(1))}
(root/'build_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('BUILD_PASS',json.dumps(summary),flush=True)
