(function () {
  'use strict';
  const C = MotCore, $ = id => document.getElementById(id), all = q => [...document.querySelectorAll(q)];
  const fmt = (v, n = 2) => C.finite(v) ? v.toFixed(n) : '-';
  const escape = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = n => `<svg aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const measuredTransmission556 = 4.96 / 23 * 100; // User measured fiber entrance -> final MOT, 2026-10-28.
  const defaultConfig = id => ({ efficiency: null, transmission: id === '556' ? measuredTransmission556 : null, minimum: null, maximum: null, margin: 5, tolerance: 1 });
  function validateConfig(value) {
    for (const k of ['efficiency', 'transmission', 'minimum', 'maximum']) if (value[k] !== null && !(C.finite(value[k]) && value[k] > 0 && value[k] <= 100)) throw Error('效率及透过率需大于 0 且不超过 100%，未知可留空');
    if (!C.finite(value.margin) || !(value.margin >= 0 && value.margin < 50) || !C.finite(value.tolerance) || !(value.tolerance >= .01 && value.tolerance <= 20)) throw Error('请检查预留余量与误差带');
    if ([value.efficiency, value.minimum, value.maximum].every(C.finite)) C.estimateRange({ ...value, reference: 10 });
  }
  function makeChannel(id) {
    let config = defaultConfig(id), calibration = id === '556' ? C.normalizeCalibration(MOT_DEFAULT_CALIBRATION) : null, serial = '', port = '', rangeReference = null;
    try {
      const saved = JSON.parse(localStorage.getItem(`mot-pid-optics-${id}-v1`) || (id === '556' ? localStorage.getItem('mot556-optics-v1') : null) || 'null');
      if (saved) { validateConfig(saved); config = { ...config, ...saved }; }
      const savedCal = JSON.parse(localStorage.getItem(`mot-pid-calibration-${id}-v1`) || 'null');
      if (savedCal && savedCal.wavelength_nm === Number(id)) calibration = C.normalizeCalibration(savedCal);
      serial = localStorage.getItem(`mot-pid-meter-${id}`) || '';
      const savedPort = (localStorage.getItem(`mot-pid-port-${id}`) || '').toUpperCase();
      if (/^COM[1-9][0-9]{0,3}$/.test(savedPort)) port = savedPort;
    } catch {}
    try {
      const savedRange = JSON.parse(localStorage.getItem(`mot-pid-range-${id}-v1`) || 'null');
      if (savedRange?.channel === id && C.finite(savedRange.time) && savedRange.time <= Date.now() && typeof savedRange.context === 'string') {
        C.estimateRange({ ...savedRange, margin: config.margin }); rangeReference = savedRange;
      }
    } catch {}
    // Apply this newly supplied measurement once, including existing browsers.
    // Subsequent edits or an explicit blank survive reloads.
    if (id === '556') {
      try {
        if (!localStorage.getItem('mot-pid-transmission-556-measurement-20261028')) {
          config.transmission = measuredTransmission556;
          localStorage.setItem('mot-pid-optics-556-v1', JSON.stringify(config));
          localStorage.setItem('mot-pid-transmission-556-measurement-20261028', '1');
        }
      } catch {}
    }
    return { id, config, calibration, target: id === '556' ? 10 : null, draft: id === '556' ? '10.00' : '', rate: '2.0', port, serial, chosen: false, applied: false, snapshot: null, polling: false, busy: false, history: [], window: 60, paused: false, pauseTime: 0, lastMeter: null, lastAdc: null, error: '', previousConnected: false, statsSince: { power: 0, adc: 0 }, lastTargetCode: null, wasRamping: false, rangeReference, rangeCandidate: null };
  }
  const channels = { '556': makeChannel('556'), '399': makeChannel('399') };
  let channelId = '556';
  let meterDevices = [], meterScanning = false, meterScanReady = false, meterScanError = '';
  let portDevices = [], portScanReady = false;
  const current = () => channels[channelId];
  const anyBusy = () => Object.values(channels).some(c => c.busy);
  function toast(message, bad = false) { const el = document.createElement('div'); el.className = 'toast' + (bad ? ' error' : ''); el.innerHTML = icon(bad ? 'info' : 'check') + `<span>${escape(message)}</span>`; $('toast-stack').append(el); setTimeout(() => el.remove(), 4200); }
  async function api(path, body, id = channelId) {
    if (!/^https?:$/.test(location.protocol)) throw Error('请双击 start.cmd，通过本机网页连接设备');
    // Verify channel routing before sending writes, including when an older
    // single-channel server is still running after the local files are updated.
    if (body !== undefined) {
      const service = await api('/api/state', undefined, id);
      if (path === '/api/target' && !service.target_modes?.includes(body.mode)) throw Error('请重新启动控制台服务，以使用直接更新和渐变选项');
    }
    const response = await fetch(`${path}?channel=${encodeURIComponent(id)}`, { method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-MOT-Console': '1' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(12000), cache: 'no-store' });
    let value; try { value = await response.json(); } catch { throw Error('本机服务未启动，请双击 start.cmd'); }
    if (!response.ok) throw Error(value.error || '设备服务异常');
    if (value.app !== 'mot556-console') throw Error('此地址不是 MOT 控制台设备服务');
    if (value.version === '1.0.0' || value.state && value.state.wavelength_nm !== Number(id)) throw Error('检测到旧版设备服务，请关闭旧控制台命令窗口，再重新启动 start.cmd');
    return value;
  }
  const fresh = d => !!(d?.connected && C.finite(d.sampled_at) && Date.now() - d.sampled_at < 2500);
  function readings(s = current()) {
    const f = s.snapshot?.fpga, m = s.snapshot?.meter; let adcPower = null;
    if (fresh(f) && s.calibration) { try { adcPower = C.toPower(s.calibration, f.adc_code); } catch {} }
    return { f, m, power: fresh(m) ? m.power_mw : null, adcPower, fpgaLive: fresh(f), meterLive: fresh(m) };
  }
  function loadControls() {
    const s = current(); $('target-input').value = s.draft; $('ramp-rate').value = s.rate;
    $('target-mode').value = s.targetMode || 'step';
    $('ramp-rate').disabled = $('target-mode').value !== 'ramp';
    $('target-input').max = s.calibration ? s.calibration.points.at(-1).power_mw : 100000;
    $('serial-port').value = s.port; renderMeterOptions();
    $('transmission-input').value = C.finite(s.config.transmission) ? fmt(s.config.transmission) : '';
    $('transmission-error').textContent = '';
    all('[data-window]').forEach(b => b.classList.toggle('active', Number(b.dataset.window) === s.window));
    $('pause-chart').innerHTML = icon(s.paused ? 'play' : 'pause'); preview();
  }
  function switchChannel(id) {
    if (!channels[id] || id === channelId || anyBusy()) return;
    const s = current(); s.draft = $('target-input').value; s.rate = $('ramp-rate').value;
    channelId = id; $('target-error').textContent = ''; loadControls(); render();
  }
  all('[data-channel]').forEach(b => b.onclick = () => switchChannel(b.dataset.channel));
  $('target-mode').onchange = () => {
    current().targetMode = $('target-mode').value;
    $('ramp-rate').disabled = current().targetMode !== 'ramp';
    render();
  };
  function preview() {
    const s = current(), value = $('target-input').value.trim();
    const n = value === '' ? NaN : Number(value);
    try {
      if (value && (!C.finite(n) || n < 0)) throw Error('目标功率需为非负数字');
      $('code-preview').textContent = s.calibration && value ? C.toCode(s.calibration, n) : '-';
      $('target-error').textContent = '';
    } catch (e) { $('code-preview').textContent = '-'; $('target-error').textContent = e.message; }
    all('[data-preset]').forEach(b => b.classList.toggle('selected', Number(b.dataset.preset) === n));
    renderRange(readings(s).power);
  }
  $('target-input').addEventListener('input', () => { current().chosen = true; current().draft = $('target-input').value; preview(); });
  for (const [id, step] of [['target-minus', -.1], ['target-plus', .1]]) $(id).onclick = () => { $('target-input').value = Math.max(0, Number($('target-input').value || 0) + step).toFixed(2); current().chosen = true; current().draft = $('target-input').value; preview(); };
  all('[data-preset]').forEach(b => b.onclick = () => { $('target-input').value = Number(b.dataset.preset).toFixed(2); current().chosen = true; current().draft = $('target-input').value; preview(); });
  $('setpoint-form').onsubmit = async e => {
    e.preventDefault(); if (anyBusy()) return;
    const s = current(), id = channelId;
    try {
      const value = $('target-input').value, target = Number(value), rate = Number($('ramp-rate').value);
      if (!value.trim() || !C.finite(target) || target < 0) throw Error('请输入非负目标功率');
      if (s.calibration) C.toCode(s.calibration, target);
      const mode = $('target-mode').value;
      if (mode === 'ramp' && !(rate >= .01 && rate <= 100)) throw Error('变化速率为 0.01–100 mW/s');
      s.busy = true; render();
      if (s.snapshot?.fpga.connected) {
        if (!s.calibration) throw Error(`请先导入 ${id} nm 本路标定，再应用目标`);
        const result = await api('/api/target', { power_mw: target, mode, rate_mw_s: mode === 'ramp' ? rate : null, calibration: s.calibration }, id);
        s.snapshot = result.state; s.applied = true; toast(mode === 'step' ? `${id} nm 目标已直接更新并读回` : `${id} nm 目标已接收，正在按速率渐变`);
      } else { s.applied = false; toast(`${id} nm 目标已保存，尚未下发到设备`); }
      s.target = target; s.draft = value; if (Number.isFinite(rate)) s.rate = String(rate); s.targetMode = mode; s.chosen = true;
    } catch (err) { $('target-error').textContent = err.message; toast(err.message, true); }
    finally { s.busy = false; render(); }
  };
  async function setPi(enabled) {
    if (anyBusy()) return; const s = current(), id = channelId; s.busy = true; render();
    try { s.snapshot = (await api('/api/pi', { enabled }, id)).state; toast(enabled ? `${id} nm PI 已使能并读回` : `${id} nm PI 已停用；RF 与光源未关闭`); }
    catch (e) { toast(e.message, true); } finally { s.busy = false; render(); }
  }
  $('pi-toggle').onclick = () => setPi(!current().snapshot?.fpga.pi_enabled); $('stop-loop').onclick = () => setPi(false);
  all('.close-dialog').forEach(b => b.onclick = () => b.closest('dialog').close());
  all('dialog').forEach(d => d.onclick = e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } });
  $('nav-overview').onclick = () => window.scrollTo({ top: 0, behavior: 'smooth' });
  function rememberPort(s) { try { localStorage.setItem(`mot-pid-port-${s.id}`, s.port); } catch {} }
  function availablePorts(s = current()) {
    const occupied = Object.values(channels).filter(c => c.id !== s.id && c.snapshot?.fpga?.connected).map(c => c.snapshot.fpga.port);
    return portDevices.filter(p => !occupied.includes(p.device));
  }
  function renderPortOptions() {
    const s = current(), connected = s.snapshot?.fpga?.connected, eligible = availablePorts(s);
    if (connected) s.port = s.snapshot.fpga.port;
    else if (portScanReady && !eligible.some(p => p.device === s.port)) s.port = eligible.length === 1 ? eligible[0].device : '';
    const choices = [...eligible];
    if (connected && !choices.some(p => p.device === s.port)) choices.push({ device: s.port, description: '本路已连接' });
    $('serial-ports').innerHTML = choices.map(p => `<option value="${escape(p.device)}">${escape(p.description)}</option>`).join('');
    $('serial-port').value = s.port;
    $('service-state').textContent = connected ? `本机服务就绪 · FPGA 已连接 ${s.port}` : !portDevices.length ? '本机服务就绪 · 未发现串口，请检查 USB 连接' : `本机服务就绪 · 发现 ${portDevices.length} 个串口` + (s.port ? ` · 已选 ${s.port}` : eligible.length ? ' · 请选择 FPGA 串口' : ' · 串口已被另一光路使用');
  }
  async function refreshPorts(id = channelId) {
    const value = await api('/api/ports', undefined, id);
    if (!Array.isArray(value.ports)) throw Error('本机服务没有返回有效串口列表');
    portDevices = value.ports; portScanReady = true; renderPortOptions();
  }
  async function inspectService() {
    try { await refreshPorts(); }
    catch (e) { $('service-state').textContent = e.message; }
  }
  function rememberMeter(s) { try { localStorage.setItem(`mot-pid-meter-${s.id}`, s.serial); } catch {} }
  function renderMeterOptions() {
    const s = current(), connected = s.snapshot?.meter?.connected, identity = s.snapshot?.meter?.identity;
    const devices = meterDevices.map(d => ({ ...d }));
    // Keep the actively read instrument visible even if discovery reports it busy.
    if (connected && !devices.some(d => d.serial === identity.serial)) devices.push({ ...identity, connected_channel: s.id });
    const eligible = devices.filter(d => !d.connected_channel || d.connected_channel === s.id);
    if (connected) s.serial = identity.serial;
    else if (meterScanReady && !eligible.some(d => d.serial === s.serial)) s.serial = eligible.length === 1 ? eligible[0].serial : '';
    const placeholder = meterScanning ? '正在扫描…' : devices.length ? '请选择功率计' : '未发现功率计';
    $('meter-serial').innerHTML = `<option value="">${placeholder}</option>` + devices.map(d => {
      const assigned = d.connected_channel && d.connected_channel !== s.id;
      return `<option value="${escape(d.serial)}" ${assigned ? 'disabled' : ''}>${escape(d.model)} · ${escape(d.serial)}${d.connected_channel ? ` · ${escape(d.connected_channel)} nm 已连接` : ''}</option>`;
    }).join('');
    $('meter-serial').value = s.serial;
    $('meter-scan-state').textContent = meterScanning ? '正在查找 USB 功率计…' : meterScanError || (devices.length ? `发现 ${devices.length} 台功率计` : '未发现功率计，请接入 USB 后重新扫描');
  }
  async function scanMeters() {
    if (meterScanning || anyBusy()) return;
    meterScanning = true; meterScanError = ''; renderMeterOptions(); render();
    try {
      const value = await api('/api/meters');
      if (!Array.isArray(value.meters)) throw Error('请重新启动本机控制台服务');
      meterDevices = value.meters;
    } catch (e) { meterDevices = []; meterScanError = e.message; }
    finally { meterScanning = false; meterScanReady = true; renderMeterOptions(); render(); }
  }
  function openConnect() { const s = current(); $('serial-port').value = s.port; renderMeterOptions(); $('connection-error').textContent = ''; $('connection-feedback').textContent = `${channelId} nm · 连接后设置功率计波长，不自动应用目标。`; $('connect-dialog').showModal(); inspectService(); scanMeters(); }
  ['connect-button', 'nav-devices'].forEach(id => $(id).onclick = openConnect); $('refresh-ports').onclick = inspectService;
  $('serial-port').addEventListener('input', () => { current().port = $('serial-port').value.trim().toUpperCase(); });
  $('refresh-meters').onclick = scanMeters;
  $('meter-serial').addEventListener('change', () => { current().serial = $('meter-serial').value; rememberMeter(current()); render(); });
  async function connect(kind) {
    if (anyBusy()) return; const s = current(), id = channelId;
    s.port = $('serial-port').value.trim().toUpperCase(); s.serial = $('meter-serial').value.trim(); s.busy = true;
    $('connection-error').textContent = ''; $('connection-feedback').textContent = '正在连接并读取设备…'; render();
    try {
      const connected = kind === 'fpga' ? s.snapshot?.fpga.connected : s.snapshot?.meter.connected;
      if (kind === 'fpga' && !connected) {
        await refreshPorts(id);
        if (!s.port) throw Error(availablePorts(s).length ? '检测到多个可用串口，请选择 FPGA 对应的串口' : portDevices.length ? '串口已被另一光路使用，请先断开原通道' : '未发现串口，请检查 FPGA 的 USB 连接后刷新');
      }
      const value = connected ? await api(`/api/disconnect/${kind}`, {}, id) : await api(`/api/connect/${kind}`, kind === 'fpga' ? { port: s.port } : { serial: s.serial }, id);
      accept(value.state, id); $('connection-feedback').textContent = connected ? '设备已断开，相关读数已清空。' : kind === 'fpga' ? `${id} nm FPGA 已连接，未自动修改目标。` : `功率计已连接，${id} nm 已回读。`;
      if (kind === 'meter') { rememberMeter(s); meterDevices = meterDevices.map(d => d.serial === s.serial ? { ...d, connected_channel: connected ? null : id } : d); renderMeterOptions(); }
      toast(connected ? `${id} nm 设备已断开` : `${id} nm ${kind === 'fpga' ? 'FPGA' : '功率计'}已连接`);
    } catch (e) { $('connection-error').textContent = e.message; $('connection-feedback').textContent = '连接未完成'; toast(e.message, true); }
    finally { s.busy = false; render(); }
  }
  $('connect-fpga').onclick = () => connect('fpga'); $('connect-meter').onclick = () => connect('meter');
  function accept(snapshot, id) {
    const s = channels[id], f = snapshot.fpga, m = snapshot.meter;
    const now = Date.now(), previous = s.snapshot;
    const ramping = !!(f.connected && f.ramp);
    const targetChanged = f.connected && s.lastTargetCode !== null && f.setpoint_code !== s.lastTargetCode;
    if (targetChanged || ramping || s.wasRamping) s.statsSince = { power: now, adc: now };
    if (f.connected) s.lastTargetCode = f.setpoint_code;
    s.wasRamping = ramping;
    for (const [key, device, old, identity] of [['power', m, previous?.meter, 'serial'], ['adc', f, previous?.fpga, 'port']]) {
      const deviceId = key === 'power' ? device.identity?.[identity] : device[identity];
      const oldId = key === 'power' ? old?.identity?.[identity] : old?.[identity];
      if (!fresh(device) || !fresh(old) || deviceId !== oldId || device.sampled_at - old.sampled_at > 2500 || device.sampled_at < old.sampled_at) s.statsSince[key] = now;
    }
    const meterChanged = !!m.connected !== !!s.snapshot?.meter?.connected || m.identity?.serial !== s.snapshot?.meter?.identity?.serial;
    if (f.connected && (s.port !== f.port || !s.previousConnected)) {
      s.port = f.port; rememberPort(s);
      if (id === channelId) $('serial-port').value = s.port;
    }
    if (f.connected && !s.previousConnected) {
      if (!s.chosen && s.calibration) { try { s.target = C.toPower(s.calibration, f.setpoint_code); s.draft = s.target.toFixed(2); s.applied = true; if (id === channelId) { $('target-input').value = s.draft; preview(); } } catch {} }
      else s.applied = false;
    }
    if (!f.connected) s.applied = false;
    s.previousConnected = !!f.connected; s.snapshot = snapshot;
    if (m.connected && m.identity?.serial && s.serial !== m.identity.serial) { s.serial = m.identity.serial; rememberMeter(s); }
    if (meterChanged && id === channelId) renderMeterOptions();
    const error = [f.error, m.error].filter(Boolean).join('；'); if (error && error !== s.error) toast(`${id} nm · ${error}`, true); s.error = error;
    const r = readings(s), newM = fresh(m) && m.sampled_at !== s.lastMeter, newF = fresh(f) && f.sampled_at !== s.lastAdc;
    if (newM || newF) { s.history.push({ time: now, power: newM ? r.power : null, powerTime: newM ? m.sampled_at : null, adc: newF ? r.adcPower : null, adcTime: newF ? f.sampled_at : null, target: s.target }); if (newM) s.lastMeter = m.sampled_at; if (newF) s.lastAdc = f.sampled_at; if (s.history.length > 3000) s.history.splice(0, s.history.length - 3000); }
  }
  async function poll(id) {
    const s = channels[id]; if (s.polling || !/^https?:$/.test(location.protocol)) return; s.polling = true;
    try { accept((await api('/api/state', undefined, id)).state, id); s.serviceError = ''; }
    catch (e) { s.snapshot = null; s.applied = false; s.previousConnected = false; s.serviceError = e.message.includes('旧版') ? e.message : ''; }
    finally { s.polling = false; if (id === channelId) render(); }
  }
  function opticalConfig(s = current()) {
    if (s.id !== '556') return { ...s.config };
    let coverage = {};
    try {
      if (!fresh(s.snapshot?.fpga)) throw Error('等待 FPGA 实时 DAC 读数');
      coverage = C.opticsCoverage556(s.snapshot.fpga);
      return { ...s.config, ...C.estimateOptics556(s.snapshot.fpga), automatic: true };
    } catch (e) { return { ...s.config, ...coverage, efficiency: null, minimum: null, maximum: null, automatic: true, error: e.message }; }
  }
  function clearRangeReference(s) {
    s.rangeReference = null; s.rangeCandidate = null;
    try { localStorage.removeItem(`mot-pid-range-${s.id}-v1`); } catch {}
  }
  function rangeContext(s) {
    const f = s.snapshot?.fpga, m = s.snapshot?.meter;
    return JSON.stringify([s.id, f?.port, f?.firmware, f?.out_min, f?.out_max, m?.identity?.serial, s.config.transmission,
      s.id === '399' ? [s.config.efficiency, s.config.minimum, s.config.maximum] : null]);
  }
  function openParameters() { const s = current(), config = opticalConfig(s); Object.keys(s.config).forEach(k => { const field = $('range-form').elements[k]; field.value = config[k] ?? ''; field.readOnly = s.id === '556' && ['efficiency','minimum','maximum'].includes(k); }); $('range-error').textContent = ''; $('range-dialog').showModal(); }
  $('range-settings').onclick = openParameters; $('nav-parameters').onclick = openParameters;
  function saveTransmission() {
    const s = current(), raw = $('transmission-input').value.trim();
    const transmission = raw === '' ? null : Number(raw);
    try {
      if (transmission !== null && (!C.finite(transmission) || transmission <= 0 || transmission > 100)) throw Error('透过率应大于 0 且不超过 100%，未知可留空');
      const next = { ...s.config, transmission };
      localStorage.setItem(`mot-pid-optics-${s.id}-v1`, JSON.stringify(next));
      s.config = next; clearRangeReference(s); $('transmission-error').textContent = ''; render();
      toast(transmission === null ? '透过率已清空，显示未校正的等效功率' : `${s.id} nm 下游透过率已保存：${fmt(transmission)}%`);
    } catch (e) { $('transmission-error').textContent = e.message; }
  }
  $('save-transmission').onclick = saveTransmission;
  $('transmission-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); saveTransmission(); } });

  $('range-form').onsubmit = e => {
    e.preventDefault(); const s = current(), f = e.currentTarget;
    const next = Object.fromEntries(Object.keys(s.config).map(k => [k, !f.elements[k].value.trim() ? null : Number(f.elements[k].value)]));
    try { validateConfig(next); s.config = next; clearRangeReference(s); try { localStorage.setItem(`mot-pid-optics-${channelId}-v1`, JSON.stringify(next)); } catch {} $('transmission-input').value = C.finite(next.transmission) ? fmt(next.transmission) : ''; $('transmission-error').textContent = ''; $('range-dialog').close(); toast(`${channelId} nm 光路参数已保存`); render(); }
    catch (err) { $('range-error').textContent = err.message; }
  };
  $('channel-calibration-file').onchange = async event => {
    const file = event.target.files[0], s = current(), id = channelId; if (!file || anyBusy()) return;
    try {
      if (s.snapshot?.fpga.ramp) throw Error('请等待目标斜坡完成后再切换标定');
      if (file.size > 2000000) throw Error('标定文件不能大于 2 MB');
      const input = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
      if (input.wavelength_nm !== Number(id)) throw Error(`标定文件须声明 wavelength_nm: ${id}，不能跨通道复用`);
      const calibration = C.normalizeCalibration(input); s.calibration = calibration; s.history = []; s.lastAdc = null; s.lastMeter = null; clearRangeReference(s);
      try { localStorage.setItem(`mot-pid-calibration-${id}-v1`, JSON.stringify(calibration)); } catch {}
      loadControls(); render(); toast(`${id} nm 标定已载入，未修改设备目标`);
    } catch (e) { toast(e.message, true); } finally { event.target.value = ''; }
  };
  function renderRange(power) {
    const s = current(), config = opticalConfig(s), f = s.snapshot?.fpga, m = s.snapshot?.meter, context = rangeContext(s);
    let range = null, historical = false, reason = config.error || '等待功率计与本路效率参数';
    if (C.finite(power) && power > 0) { try { range = C.estimateRange({ ...config, reference: power }); } catch (e) { reason = config.error || e.message; } }
    if (range && !f?.ramp) {
      const time = m.sampled_at;
      if (s.rangeReference?.time !== time || s.rangeReference?.context !== context) {
        s.rangeReference = { channel: s.id, context, time, reference: power, efficiency: config.efficiency, minimum: config.minimum, maximum: config.maximum };
        try { localStorage.setItem(`mot-pid-range-${s.id}-v1`, JSON.stringify(s.rangeReference)); } catch {}
      }
    } else if (!range && C.finite(power) && power > 0 && fresh(f) && s.rangeReference?.context === context) {
      try { range = C.estimateRange({ ...s.rangeReference, margin: s.config.margin }); historical = true; } catch {}
    }
    if (!C.finite(power)) reason = '等待功率计读数';
    else if (power <= 0) reason = '功率计读数需大于 0';
    const hasTransmission = C.finite(config.transmission) && config.transmission > 0 && config.transmission <= 100;
    const equivalent = C.finite(power) && power >= 0 && C.finite(config.efficiency) && config.efficiency > 0 ? power / (config.efficiency / 100) : null;
    const upstream = C.finite(equivalent) ? hasTransmission ? equivalent / (config.transmission / 100) : equivalent : null;
    $('upstream-label').textContent = hasTransmission ? 'AOM 前光功率' : '等效入射功率';
    $('upstream-tag').textContent = hasTransmission ? '估算' : '未校正';
    $('upstream-metric').textContent = fmt(upstream);
    $('upstream-note').textContent = !C.finite(power) ? '等待功率计读数' : !C.finite(equivalent) ? config.error || '等待衍射效率' : hasTransmission ? `η ${fmt(config.efficiency)}% · T ${fmt(config.transmission)}%` : 'P/η · 未校正下游损耗，实际入射功率需再除以 T';
    $('range-heading').textContent = historical ? '上次有效范围（参考）' : config.partial ? '已标定可调范围' : '可调范围（估算）';
    $('range-values').closest('.target-range').dataset.state = historical ? 'reference' : range ? 'live' : 'unavailable';
    $('range-values').innerHTML = range ? `${fmt(range.low)} <span>–</span> ${fmt(range.high)}` : '-';
    $('range-recommend').textContent = range ? `${historical ? '参考区间' : '建议稳定区间'} ${fmt(range.recommendedLow)}–${fmt(range.recommendedHigh)} mW` : '尚无有效功率范围';
    const draft = $('target-input').value.trim(), target = draft ? Number(draft) : s.target;
    $('range-target-label').textContent = range && C.finite(target) && (target < range.low || target > range.high) ? `输入目标超出${historical ? '参考' : '已标定'}范围` : '';
    const recordedAt = historical ? new Date(s.rangeReference.time).toLocaleString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour12: false }) : '';
    let guidance = '';
    if (!range && config.calibrated === false && C.finite(power)) guidance = config.rf < config.lowRF ? `；请小步提高目标，使 DAC 回到 ${config.lowDac} 以上后重算` : `；需回到 DAC ${config.lowDac}–${config.highDac} 的标定区间`;
    $('range-status').textContent = historical ? `${reason}；保留 ${recordedAt} 的估算，光路变化后需重新测量。` : range ? '' : reason + guidance;
    s.rangeCandidate = null;
    if (range && s.calibration) {
      const low = Math.ceil(Math.max(range.recommendedLow, s.calibration.points[0].power_mw) * 100), high = Math.floor(Math.min(range.recommendedHigh, s.calibration.points.at(-1).power_mw) * 100);
      if (low <= high) s.rangeCandidate = Math.round((low + high) / 2) / 100;
    }
    $('use-range-target').hidden = !C.finite(s.rangeCandidate);
    $('use-range-target').disabled = anyBusy();
    $('use-range-target').textContent = `填入${historical ? '参考' : '区间中'}值 ${fmt(s.rangeCandidate)} mW`;
    $('range-note').textContent = range ? config.automatic ?
      `RF ≈ ${fmt(config.rf)} mVpp · η ≈ ${fmt(config.efficiency)}% · 标定覆盖 DAC ${config.lowDac}–${config.highDac}${config.partial ? '（部分范围，未外推）' : ''}；按当前光路透过率不变估算。` :
      `按实测功率和手填效率估算；改变 RF 后须更新效率。` : reason;
    if (historical) $('range-note').textContent = `使用上次有效功率及效率估算，不对当前 RF 外推。记录时间 ${recordedAt}。填入参考值只修改输入框，点击应用后才下发。`;
    if (config.automatic) $('efficiency-detail').innerHTML = C.finite(config.efficiency) ? `${fmt(config.efficiency)}% <small>DAC → RF → 实测曲线</small>` : `<small>${escape(config.error || '等待有效 DAC 读数')}</small>`;
  }
  $('use-range-target').onclick = () => {
    const s = current(); if (!C.finite(s.rangeCandidate) || anyBusy()) return;
    $('target-input').value = fmt(s.rangeCandidate); s.draft = $('target-input').value; s.chosen = true; preview();
    toast(`已填入 ${s.draft} mW；点击“应用目标功率”后生效`);
  };
  function statusText(status) { const a = [[1, '丢光'], [2, 'ADC 过量程历史标志'], [4, '输出饱和'], [8, '积分到限'], [16, '反馈冻结'], [32, 'ADC 时钟停止']].filter(([b]) => status & b).map(([, s]) => s); return a.length ? a.join(' / ') : '正常'; }
  function render() {
    const s = current(), config = s.config, cal = s.calibration, r = readings(s), f = r.f, m = r.m, busy = anyBusy();
    $('footer-channel').textContent = `${channelId} nm MOT`;
    document.title = `${channelId} nm MOT · PID Control Console`;
    $('connect-dialog-title').textContent = `连接 ${channelId} nm 设备`; $('range-dialog-title').textContent = `${channelId} nm · AOM 功率与范围估算`;
    $('meter-wavelength-note').textContent = `支持 PM100D / PM100USB；连接后自动设置为 ${channelId} nm。`;
    all('[data-channel]').forEach(b => { const selected = b.dataset.channel === channelId; b.classList.toggle('active', selected); b.setAttribute('aria-selected', String(selected)); b.disabled = busy; });
    $('mode-text').textContent = f?.connected || m?.connected ? '设备模式' : '设备未连接';
    $('notice-text').textContent = s.serviceError || `${channelId} nm · 功率与稳定度`;
    $('fpga-power-metric').textContent = fmt(r.adcPower);
    $('fpga-power-tag').textContent = r.fpgaLive ? '估算' : '未连接';
    $('fpga-power-foot').textContent = !r.fpgaLive ? '等待 FPGA 连接' : !cal ? '需本路功率标定' : !C.finite(r.adcPower) ? 'ADC 超出本路标定范围' : '实时 ADC · 本路标定换算';
    $('power-metric').textContent = fmt(r.power); $('meter-tag').textContent = r.meterLive ? '实测' : m?.connected ? '等待读数' : '未连接';
    $('meter-foot').textContent = r.meterLive ? m.identity?.model || '实测' : '选接功率计';
    $('tolerance-foot').innerHTML = `<i></i>误差带 ±${fmt(config.tolerance)}%`;
    $('adc-code').textContent = r.fpgaLive ? f.adc_code : '-'; $('adc-target').textContent = r.fpgaLive ? f.setpoint_code : '-'; $('dac-code').textContent = r.fpgaLive ? f.dac_code : '-'; $('adc-power').innerHTML = fmt(r.adcPower) + '<small> mW</small>';
    $('readback-label').textContent = r.fpgaLive ? '14 位数字码 · 设备读回' : '等待 FPGA 连接'; $('dac-detail').textContent = r.fpgaLive ? `${f.dac_code === f.out_max ? '已达上限 · ' : f.dac_code === f.out_min ? '已达下限 · ' : ''}限幅 ${f.out_min}–${f.out_max}` : '未连接';
    $('pi-toggle').classList.toggle('on', !!(r.fpgaLive && f.pi_enabled)); $('pi-toggle').setAttribute('aria-checked', String(!!(r.fpgaLive && f.pi_enabled))); $('pi-toggle').disabled = busy || !r.fpgaLive; $('stop-loop').disabled = busy || !r.fpgaLive || !f.pi_enabled;
    $('loop-status').textContent = r.fpgaLive ? (f.status & 63 ? statusText(f.status) : f.pi_enabled ? '反馈已使能' : '反馈已停用') : '未连接'; $('loop-status').classList.toggle('warn', r.fpgaLive && !!(f.status & 63));
    $('efficiency-detail').innerHTML = C.finite(config.efficiency) ? fmt(config.efficiency) + '% <small>手填参考值</small>' : '<small>本路尚未填写</small>';
    $('apply-target').innerHTML = (f?.connected ? '应用目标功率' : '保存目标设定') + ' ' + icon('arrow'); $('apply-target').disabled = busy || !!(f?.connected && !cal);
    $('apply-hint').textContent = !cal ? `应用 ${channelId} nm 目标前，需导入本路标定。` : f?.connected ? (s.targetMode === 'ramp' ? '按设定速率逐步更新目标。' : '一次更新目标，不附加变化速率限制。') : '连接本路 FPGA 后可在线应用目标光功率。';
    $('channel-calibration-status').textContent = cal ? `${channelId} nm · ${cal.points.length - 1} 段标定` : `${channelId} nm · 待标定`;
    $('channel-calibration-status').classList.toggle('channel-calibration-missing', !cal);
    $('calibration-summary').textContent = cal ? `${channelId} nm · ${cal.points.length - 1} 段标定` : `${channelId} nm 本路标定待载入`;
    $('calibration-summary-sub').textContent = cal ? cal.historical ? '2026-10-20 历史数据，使用前核对光路' : '已载入本路独立标定' : '不套用 556 nm 标定，换算值显示“-”';
    $('live-label').innerHTML = '<span class="small-dot"></span>' + (s.paused ? '显示已暂停' : r.meterLive || r.fpgaLive ? '实时监测' : '等待连接');
    $('fpga-dot').classList.toggle('connected', r.fpgaLive); $('pm-dot').classList.toggle('connected', r.meterLive);
    $('fpga-strip').textContent = f?.connected ? f.port + (r.fpgaLive ? ' · 已连接' : ' · 数据过期') : '未连接'; $('pm-strip').textContent = m?.connected ? (m.identity?.model || 'Thorlabs') + (r.meterLive ? ` · ${channelId} nm` : ' · 等待读数') : '未连接'; $('last-update').textContent = r.meterLive || r.fpgaLive ? '数据更新中' : '尚无设备读数';
    $('connect-fpga').textContent = f?.connected ? '断开 FPGA' : '连接 FPGA'; $('connect-meter').textContent = m?.connected ? '断开功率计' : '连接功率计'; $('connect-fpga').disabled = busy; $('connect-meter').disabled = busy || (!m?.connected && (!s.serial || meterScanning));
    $('serial-port').disabled = busy || !!f?.connected; $('refresh-ports').disabled = busy;
    $('meter-serial').disabled = busy || meterScanning || !!m?.connected; $('refresh-meters').disabled = busy || meterScanning;
    renderStability(r); renderRange(r.power); if (!s.paused) renderChart();
  }
  function renderStability(r = readings()) {
    const s = current(), now = Date.now();
    for (const [key, id, live] of [['adc', 'fpga', r.fpgaLive], ['power', 'meter', r.meterLive]]) {
      const stats = C.windowStats(live ? s.history : [], key, now, s.window, s.statsSince[key]);
      const value = stats.rms;
      $(`${id}-rms`).textContent = C.finite(value) ? value > 0 && value < .001 ? '<0.001' : fmt(value, 3) : '-';
      $(`${id}-rms`).dataset.count = String(stats.count);
      $(`${id}-rms-note`).textContent = !live ? '未连接' : key === 'adc' && !s.calibration ? '需本路功率标定' : key === 'adc' && !C.finite(r.adcPower) ? '超出标定范围' : s.wasRamping ? '目标切换中' : stats.count < 2 ? `采样中 · ${stats.count} 点` : stats.mean <= 0 ? '均值需大于 0' : `近 ${s.window} 秒 · 已采 ${fmt(stats.duration, 1)} 秒 / ${stats.count} 点`;
      $(`${id}-rms`).title = `各自波动 RMS ÷ 各自平均值 × 100%；均值 ${fmt(stats.mean, 4)} mW，波动 RMS ${fmt(stats.sigma, 4)} mW。目标切换或重连后重新统计；仅反映网页慢速监测。`;
    }
  }
  function renderChart() {
    const s = current(), config = s.config, end = s.paused ? s.pauseTime : Date.now(), start = end - s.window * 1000, data = s.history.filter(d => d.time >= start && d.time <= end), nominal = s.target || 1;
    const values = data.flatMap(d => [d.power, d.adc, d.target]).filter(C.finite);
    let low = Math.min(nominal * (1 - config.tolerance / 100), ...(values.length ? values : [nominal])), high = Math.max(nominal * (1 + config.tolerance / 100), ...(values.length ? values : [nominal]));
    const pad = Math.max((high - low) * .3, .04); low = Math.max(0, low - pad); high += pad;
    const x = t => 58 + (t - start) / (end - start) * 760, y = v => 208 - (v - low) / (high - low) * 180;
    let out = '<defs><clipPath id="plot-clip"><rect x="57" y="23" width="763" height="187"/></clipPath></defs>';
    for (let i = 0; i <= 4; i++) { const v = low + (high - low) * i / 4, yy = y(v); out += `<path d="M58 ${yy}H818" stroke="#e2e5d7"/><text x="46" y="${yy + 3}" text-anchor="end">${fmt(v, high - low > 5 ? 1 : 2)}</text>`; }
    for (let i = 0; i <= 5; i++) out += `<text x="${58 + i / 5 * 760}" y="236" text-anchor="${i === 0 ? 'start' : i === 5 ? 'end' : 'middle'}">${i === 5 ? '现在' : '-' + Math.round(s.window * (1 - i / 5)) + 's'}</text>`;
    const top = y(nominal * (1 + config.tolerance / 100)), bottom = y(nominal * (1 - config.tolerance / 100));
    out += `<g clip-path="url(#plot-clip)"><rect x="58" y="${top}" width="760" height="${bottom - top}" fill="#edf0df"/>`;
    if (C.finite(s.target)) out += `<path d="M58 ${y(s.target)}H818" stroke="#b19b66" stroke-dasharray="5 5"/>`;
    for (const [key, color, width] of [['adc', '#64889b', 1.2], ['power', '#39725a', 1.7]]) {
      let path = '', previous = null;
      for (const d of data) { if (!C.finite(d[key])) continue; path += (previous && d.time - previous.time < 2500 ? 'L' : 'M') + x(d.time).toFixed(2) + ',' + y(d[key]).toFixed(2); previous = d; }
      if (path) out += `<path d="${path}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round"/>`;
    }
    out += '</g>';
    if (!data.some(d => C.finite(d.power) || C.finite(d.adc))) out += '<rect x="244" y="88" width="360" height="57" rx="2" fill="#fcfbf6" fill-opacity=".95"/><text x="424" y="112" text-anchor="middle" style="font:12px Microsoft YaHei,sans-serif;fill:#63775d">等待本路设备连接</text><text x="424" y="133" text-anchor="middle" style="font:10px Microsoft YaHei,sans-serif;fill:#7d8b72">连接 FPGA 后显示实时功率曲线</text>';
    $('trend-svg').setAttribute('aria-label', `${channelId} nm 近 ${s.window} 秒目标与功率曲线`);
    $('trend-svg').innerHTML = out;
    const stats = C.windowStats(readings(s).meterLive ? s.history : [], 'power', end, s.window, s.statsSince.power);
    $('stat-mean').innerHTML = fmt(stats.mean, 3) + '<small> mW</small>'; $('stat-pp').innerHTML = fmt(stats.pp, 3) + '<small> mW</small>'; $('stat-count').innerHTML = stats.count + '<small> 点</small>';
  }
  all('[data-window]').forEach(b => b.onclick = () => { current().window = Number(b.dataset.window); all('[data-window]').forEach(x => x.classList.toggle('active', x === b)); renderChart(); renderStability(); });
  $('pause-chart').onclick = () => { const s = current(); s.paused = !s.paused; s.pauseTime = Date.now(); $('pause-chart').innerHTML = icon(s.paused ? 'play' : 'pause'); $('pause-chart').setAttribute('aria-label', s.paused ? '继续显示' : '暂停显示'); renderChart(); render(); };
  loadControls(); render();
  const pollAll = () => { for (const id of Object.keys(channels)) poll(id); };
  pollAll(); setInterval(pollAll, 600); setInterval(render, 500);
})();
