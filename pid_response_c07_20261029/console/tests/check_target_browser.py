"""Real Edge UI -> local HTTP handler -> frame-level fake UART. No instruments."""
from pathlib import Path
import base64, json, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer
import requests, websocket
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Controller, Handler, FPGA
from test_channels import FakeSerial
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tests/output';OUT.mkdir(exist_ok=True)
class TestHandler(Handler):
    old_service=False
    def do_GET(self):
        if self.path.startswith('/api/ports'):self.respond(200,{'ports':[]});return
        if self.path.startswith('/api/meters'):self.respond(200,{'meters':[]});return
        super().do_GET()
    def respond(self,status,payload):
        if self.old_service:payload={**payload,'target_modes':[]}
        super().respond(status,payload)
controllers={k:Controller(int(k)) for k in ('556','399')}
f=FPGA('COM99',FakeSerial)
f.serial.registers.update({0:1,1:0,3:9468,5:8731,0x18:9468,0x2c:1,0x1a:35072,0x1b:50463})
controllers['556'].fpga=f
controllers['556'].state['fpga']=f.snapshot()
http=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
http.controllers=controllers;http.connection_lock=threading.RLock()
threading.Thread(target=http.serve_forever,daemon=True).start()
with socket.socket() as s:s.bind(('127.0.0.1',0));debug=s.getsockname()[1]
browser=subprocess.Popen([r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    '--headless=new','--disable-gpu','--no-first-run','--disable-extensions',
    f'--remote-debugging-port={debug}','--user-data-dir='+str(OUT/f'edge-{time.time_ns()}'),'about:blank'],
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
counter=0;errors=[];checks=[];ws=None
try:
    for _ in range(100):
        try:
            pages=requests.get(f'http://127.0.0.1:{debug}/json',timeout=1).json()
            if pages:break
        except requests.RequestException:pass
        time.sleep(.1)
    ws=websocket.create_connection(next(p for p in pages if p['type']=='page')['webSocketDebuggerUrl'],timeout=15,suppress_origin=True)
    def call(method,params=None):
        global counter
        counter+=1;ident=counter
        ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
        while True:
            m=json.loads(ws.recv())
            if m.get('method')=='Runtime.exceptionThrown':errors.append(m['params'])
            if m.get('id')==ident:
                assert 'error' not in m,m
                return m.get('result',{})
    def js(code):
        r=call('Runtime.evaluate',{'expression':code,'returnByValue':True,'awaitPromise':True})
        assert 'exceptionDetails' not in r,r
        return r['result'].get('value')
    def wait(code):
        for _ in range(100):
            if js(code):return
            time.sleep(.1)
        raise AssertionError((code,js('document.body.innerText.slice(-1800)')))
    call('Runtime.enable');call('Page.enable')
    call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
    call('Page.navigate',{'url':f'http://127.0.0.1:{http.server_port}'})
    wait("document.querySelector('#fpga-strip')?.textContent.includes('COM99')")
    assert js("document.querySelector('#target-mode').value==='step' && document.querySelector('#ramp-rate').disabled")
    checks.append('Default direct target and disabled rate input')
    js("document.querySelector('#target-input').value='11';document.querySelector('#target-input').dispatchEvent(new Event('input'));document.querySelector('#setpoint-form').requestSubmit()")
    wait("document.querySelector('#toast-stack').textContent.includes('目标已直接更新并读回')")
    assert f.read(0x2c)==16383 and f.read(0x18)!=9468
    checks.append('Browser submit reaches actual HTTP handler and UART step write')
    js("document.querySelector('#target-mode').value='ramp';document.querySelector('#target-mode').dispatchEvent(new Event('change'))")
    assert not js("document.querySelector('#ramp-rate').disabled")
    js("document.querySelector('#target-input').value='12';document.querySelector('#ramp-rate').value='0.01';document.querySelector('#setpoint-form').requestSubmit()")
    wait("document.querySelector('#toast-stack').textContent.includes('正在按速率渐变')")
    assert controllers['556'].ramp_job is not None
    checks.append('Explicit slow ramp schedules gradual updates')
    with controllers['556'].lock:controllers['556'].ramp_job=None
    TestHandler.old_service=True
    before=len(f.serial.writes)
    js("document.querySelector('#target-mode').value='step';document.querySelector('#target-mode').dispatchEvent(new Event('change'));document.querySelector('#target-input').value='13';document.querySelector('#setpoint-form').requestSubmit()")
    wait("document.querySelector('#target-error').textContent.includes('重新启动')")
    assert len(f.serial.writes)==before
    checks.append('Old backend cannot silently turn a direct step into a ramp')
    TestHandler.old_service=False
    js("document.querySelector('[data-channel=\"399\"]').click()")
    assert js("document.querySelector('#target-mode').value==='step'")
    js("document.querySelector('[data-channel=\"556\"]').click()")
    assert js("document.querySelector('#target-mode').value==='step'")
    assert not errors,errors
    js("document.querySelector('#target-mode').scrollIntoView({block:'center'})")
    (OUT/'target-modes.png').write_bytes(base64.b64decode(call('Page.captureScreenshot',{'format':'png'})['data']))
    (OUT/'target-browser.json').write_text(json.dumps({'passed':True,'checks':checks,'runtime_errors':errors,
        'physical_hardware_writes':0,'fixture_uart_writes':len(f.serial.writes)},indent=2)+'\n',encoding='utf-8')
    print('BROWSER_PASS',len(checks),'checks; physical hardware writes=0')
finally:
    if ws:ws.close()
    browser.terminate();browser.wait(timeout=15)
    http.shutdown();http.server_close()
    for c in controllers.values():c.close()
