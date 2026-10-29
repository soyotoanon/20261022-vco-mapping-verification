"""Publish console files and preserve instrument connections; zero register writes."""
from pathlib import Path
from datetime import datetime
import hashlib,json,shutil,subprocess,sys,time
import requests,psutil
root=Path(r'D:\DSproject\workbench\tasks\pid-response-c07-20261029')
live=Path(r'D:\DSproject\workbench\sites\mot556-console')
out=root/'console-deployment'/datetime.now().strftime('%Y%m%d-%H%M%S')
out.mkdir(parents=True)
def api(path='/api/state',channel='556',body=None):
    url='http://127.0.0.1:8766'+path+'?channel='+channel
    r=requests.get(url,timeout=10) if body is None else requests.post(url,json=body,headers={'X-MOT-Console':'1'},timeout=10)
    r.raise_for_status();return r.json()
before={c:api(channel=c)['state'] for c in ['556','399']}
(out/'before.json').write_text(json.dumps(before,indent=2)+'\n',encoding='utf-8')
assert not any(s['fpga'].get('ramp') for s in before.values()),'Setpoint ramp is in progress'
found={c.pid for c in psutil.net_connections(kind='tcp') if c.status=='LISTEN' and c.laddr.port==8766}
assert len(found)==1,found
old=psutil.Process(next(iter(found)))
assert any(Path(arg)==live/'server.py' for arg in old.cmdline()),old.cmdline()
names=['server.py','app.js','index.html','styles.css','tests/test_target_modes.py','tests/check_target_browser.py']
hashes={}
for name in names:
    p=live/name
    if p.exists():
        dest=out/'before-files'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    p.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(root/'console'/name,p)
    digest=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
    assert digest(p)==digest(root/'console'/name)
    hashes[name]=digest(p)
for c,s in before.items():
    for device in ['fpga','meter']:
        if s[device].get('connected'):api('/api/disconnect/'+device,c,{})
old.terminate();old.wait(timeout=10)
new=None
for _ in range(70):
    try:
        new=api('/api/health')
        if new['version']=='1.3.0' and new['pid']!=old.pid:break
    except requests.RequestException:pass
    time.sleep(.2)
else:
    # The normal watcher usually restarts it; only start if the port is free.
    assert not any(c.status=='LISTEN' and c.laddr.port==8766 for c in psutil.net_connections(kind='tcp'))
    with (out/'server.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-B','-u',str(live/'server.py'),'--port','8766'],cwd=live,
            stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(70):
        try:
            new=api('/api/health')
            if new['version']=='1.3.0':break
        except requests.RequestException:pass
        time.sleep(.2)
    else:raise RuntimeError('New service did not start')
for c,s in before.items():
    if s['fpga'].get('connected'):api('/api/connect/fpga',c,{'port':s['fpga']['port']})
    if s['meter'].get('connected'):api('/api/connect/meter',c,{'serial':s['meter']['identity']['serial']})
after={c:api(channel=c)['state'] for c in ['556','399']}
keys=['ctrl','armed','setpoint_code','kp_register','ki_register','out_min','out_max','hardware_slew']
for c in before:
    if before[c]['fpga'].get('connected'):
        assert after[c]['fpga']['connected']
        assert all(before[c]['fpga'][k]==after[c]['fpga'][k] for k in keys),(before[c]['fpga'],after[c]['fpga'])
result={'passed':True,'version':new['version'],'old_pid':old.pid,'new_pid':new['pid'],
    'register_writes':0,'preserved_register_fields':keys,'file_sha256':hashes,'after':after}
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('CONSOLE_DEPLOY_PASS',out,flush=True)
