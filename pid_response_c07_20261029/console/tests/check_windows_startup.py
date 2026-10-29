"""Exercise the actual Windows scheduled entry point on an isolated port."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.mkdtemp(prefix='mot-startup-check-'))
OUT.mkdir(parents=True, exist_ok=True)
PS = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass']
PORT = 18766
TASK = f'MOT-PID-Control-Console-{PORT}'
URL = f'http://127.0.0.1:{PORT}'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
report = {'checks': [], 'port': PORT, 'hardware_connected': False, 'hardware_control_writes': 0}


def ps(command):
    return subprocess.run(PS + ['-Command', command], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45, check=True).stdout


def fetch(path):
    with opener.open(URL + path, timeout=5) as response:
        return response.read()


def healthy(timeout=20, old_pid=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(fetch('/api/health'))
            if data.get('healthy') and (old_pid is None or data['pid'] != old_pid):
                return data
        except Exception:
            pass
        time.sleep(.2)
    raise AssertionError('Service did not become ready')


def launch():
    result = subprocess.run(PS + ['-File', str(ROOT/'start.ps1'), '-Port', str(PORT), '-NoOpen'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result.stdout.strip()


with socket.socket() as s:
    assert s.connect_ex(('127.0.0.1', PORT)) != 0, 'Test port is already occupied'
assert ps(f"[bool](Get-ScheduledTask -ErrorAction Stop | Where-Object {{ $_.TaskName -eq '{TASK}' }})").strip() == 'False', 'Test task already exists'
try:
    report['launch_output'] = launch()
    first = healthy()
    report['first'] = first
    report['checks'].append('Scheduled entry starts a healthy local server after its launcher shell exits')
    state = json.loads(fetch('/api/state'))['state']
    assert not state['fpga']['connected'] and not state['meter']['connected']
    report['checks'].append('Startup leaves both instruments disconnected; no target or PI writes')
    launch()
    assert healthy()['pid'] == first['pid']
    report['checks'].append('Repeated open reuses the same server PID')
    duplicate = subprocess.run([sys.executable, '-B', str(ROOT/'launcher.py'), '--port', str(PORT)], capture_output=True, timeout=8)
    assert duplicate.returncode == 0 and healthy()['pid'] == first['pid']
    report['checks'].append('Duplicate watcher exits without spawning another server')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        bodies = list(pool.map(fetch, ['/', '/app.js', '/styles.css', '/api/health'] * 10))
    assert all(bodies)
    assert healthy()['pid'] == first['pid']
    report['checks'].append('40 page/assets/health requests pass without restarting the service')
    # Kill only this isolated server with no hardware attached, then verify recovery.
    ps(f'Stop-Process -Id {first["pid"]} -ErrorAction Stop')
    begin = time.monotonic()
    recovered = healthy(old_pid=first['pid'])
    report['recovery_seconds'] = round(time.monotonic()-begin, 2)
    report['recovered'] = recovered
    state = json.loads(fetch('/api/state'))['state']
    assert not state['fpga']['connected'] and not state['meter']['connected']
    report['checks'].append('Exited server recovers with a new PID and no automatic hardware commands')
    report['task'] = json.loads(ps(f"Get-ScheduledTask -TaskName '{TASK}' | Select-Object TaskName,State,@{{n='ExecutionTimeLimit';e={{$_.Settings.ExecutionTimeLimit}}}},@{{n='RunLevel';e={{$_.Principal.RunLevel}}}},@{{n='LogonType';e={{$_.Principal.LogonType}}}},@{{n='Trigger';e={{$_.Triggers.CimClass.CimClassName}}}} | ConvertTo-Json"))
    assert report['task']['ExecutionTimeLimit'] == 'PT0S'
    report['checks'].append('Current-user login trigger and unlimited task lifetime read back')
    report['passed'] = True
finally:
    ps(f"if (Get-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue) {{ Stop-ScheduledTask -TaskName '{TASK}'; Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false }}")
    time.sleep(.5)
    # Task Scheduler normally closes the job tree. Do not leave an isolated test server.
    for known in (report.get('recovered', {}), report.get('first', {})):
        if 'pid' in known:
            ps(f"$testProc = Get-CimInstance Win32_Process -Filter 'ProcessId = {known['pid']}' -ErrorAction SilentlyContinue; if ($testProc -and $testProc.CommandLine -like '*server.py*--port {PORT}*') {{ Stop-Process -Id $testProc.ProcessId -ErrorAction Stop }}")
    (OUT/'startup-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
