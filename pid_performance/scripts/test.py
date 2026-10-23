"""Run actual Vivado XSim tests, rejecting assertion failures AND missing end markers."""
from pathlib import Path
import subprocess, re, sys, json
root=Path(__file__).resolve().parents[1]
bin=Path('D:/Xilinx/Vivado/2023.2/bin')
out=root/'build'/'revision_sim'; out.mkdir(parents=True,exist_ok=True)
tests=sys.argv[1:] or ['tb_pi_revision','tb_sample_revision',
    'tb_dac_timing_revision','tb_performance_probe']
def run(name,args,log):
    r=subprocess.run([str(bin/(name+'.bat')),*map(str,args)],cwd=out,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    s=r.stdout.decode('utf-8',errors='replace');(out/log).write_text(s,encoding='utf-8')
    if r.returncode or re.search(r'(^ERROR:|FATAL_ERROR|Fatal:)',s,re.M):
        print(s[-6000:]);raise RuntimeError(f'{name}: exit={r.returncode}, log={log}')
    return s
summary=[]
for test in tests:
    dialect=['--sv'] if test.endswith('_revision') else []
    run('xvlog',[*dialect,'-i',root/'rtl',*sorted((root/'rtl').glob('*.v')),root/'tb'/(test+'.v')],test+'_compile.log')
    run('xelab',[test,'-s',test+'_sim'],test+'_elaborate.log')
    s=run('xsim',[test+'_sim','-runall'],test+'.log')
    if re.search(r'\[FAIL\]|result: FAIL|^FAIL |Fatal:|TIMEOUT|超时',s,re.M):
        print(s[-6000:]);raise RuntimeError(f'{test}: failure in simulation log')
    if not re.search(r'REVISION_PASS|PASS|通过',s):raise RuntimeError(f'{test}: no success evidence')
    # Older benches report Chinese totals and use $finish even on failure.
    if re.search(r'[1-9]\d*\s*项?失败',s):raise RuntimeError(f'{test}: nonzero failed assertions')
    if '$finish called' not in s:raise RuntimeError(f'{test}: did not finish')
    summary.append({'test':test,'result':'PASS','log':test+'.log'})
    print('SIM_PASS',test,flush=True)
(out/'results.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(f'SIM_SUITE_PASS tests={len(summary)}')
