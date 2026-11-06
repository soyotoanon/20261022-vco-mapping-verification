"""Run the retained RTL checks plus independent old/new numerical equivalence."""
from pathlib import Path
import subprocess, re, sys, json
root=Path(__file__).resolve().parents[1]
bin=Path('D:/Xilinx/Vivado/2023.2/bin')
out=root/'build/verification';out.mkdir(parents=True,exist_ok=True)
tests=sys.argv[1:] or ['tb_fast_equivalence','tb_pi_revision','tb_performance_probe','tb_sample_revision','tb_dac_timing_revision','tb_instant_display_revision','tb_dac_guard','tb_dac_routes']
def run(name,args,log):
    r=subprocess.run([str(bin/(name+'.bat')),*map(str,args)],cwd=out,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    s=r.stdout.decode('utf-8',errors='replace');(out/log).write_text(s,encoding='utf-8')
    if r.returncode or re.search(r'(^ERROR:|FATAL_ERROR|Fatal:)',s,re.M):raise RuntimeError(s[-5000:])
    return s
run('xvlog',['--sv','-i',root/'rtl',*sorted((root/'rtl').glob('*.v')),*sorted((root/'reference').glob('*.v')),*[root/'tb'/(t+'.v') for t in tests]],'compile.log')
summary=[]
for test in tests:
    run('xelab',[test,'-s',test+'_sim'],test+'_elaborate.log')
    s=run('xsim',[test+'_sim','-runall'],test+'.log')
    if re.search(r'\[FAIL\]|^FAIL |Fatal:|TIMEOUT|超时',s,re.M) or 'REVISION_PASS' not in s or '$finish called' not in s:raise RuntimeError(s[-5000:])
    evidence=[line for line in s.splitlines() if 'REVISION_PASS' in line]
    summary.append({'test':test,'result':'PASS','evidence':evidence})
    print('SIM_PASS',test,*evidence,flush=True)
(out/'results.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('SIM_SUITE_PASS tests='+str(len(summary)),flush=True)
