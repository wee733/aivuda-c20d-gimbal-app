import { HoldController, PANEL_MAX_RATE, sendPreset } from './control.mjs';

const API = '/c20d-control/api';
const APP_ID = 'app_gimbal_control_c20d_20260526_512d09';
const $ = id => document.getElementById(id);
const ui = {
  connection: $('connection'), connectionText: $('connection-text'), output: $('output'),
  logStatus: $('log-status'), notice: $('notice'), speed: $('speed'), center: $('center'), downView: $('down-view'),
  downHint: $('down-view-hint'), speedHint: $('speed-limit-hint'),
  stop: $('stop'), roll: $('roll-value'), pitch: $('pitch-value'), error: $('error-value'),
  lastCommand: $('last-command'), clear: $('clear'), directions: [...document.querySelectorAll('.direction')],
};
let ready = false;
let centerReady = false;
let downViewReady = false;
let presetBusy = false;
let presetGeneration = 0;
let activeButton = null;
const clientId = crypto.randomUUID();
let sequence = 0;
let logOffset = 0;
let logInitialized = false;
let stopped = false;
let statusBusy = false;
let logBusy = false;
const token = () => localStorage.getItem('aivuda_ui_token') || '';

function notice(message, isError = false) {
  ui.notice.textContent = message || '';
  ui.notice.classList.toggle('error', isError);
}
function setAvailable(move, center, downView = false) {
  ready = Boolean(move);
  centerReady = Boolean(center);
  downViewReady = Boolean(downView);
  ui.directions.forEach(button => { button.disabled = !ready || presetBusy; });
  ui.center.disabled = !centerReady || presetBusy;
  ui.downView.disabled = !downViewReady || presetBusy;
  if (!ready && activeButton) release();
}
async function request(path, options = {}, timeoutMs = 2000) {
  const sessionToken = token();
  if (!sessionToken) throw new Error('Sign in to AivudaOS to use this panel.');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { ...options, headers: { ...(options.headers || {}), Authorization: `Bearer ${sessionToken}` }, signal: controller.signal, cache: 'no-store' });
    if (response.status === 401 || response.status === 403) throw new Error('Session expired. Sign in to AivudaOS again.');
    let body = {};
    try { body = await response.json(); } catch { /* handled below */ }
    if (!response.ok || body.ok === false) throw new Error(body.error || body.detail || `Request failed (${response.status}).`);
    return body;
  } finally { clearTimeout(timeout); }
}
async function send(command) {
  try {
    const payload = { ...command, client_id: clientId, seq: ++sequence };
    const result = await request(`${API}/command`, { method: 'POST', headers: {'Content-Type': 'application/json'}, keepalive: command.action === 'stop', body: JSON.stringify(payload) });
    if (command.action === 'center' || command.action === 'down_view') notice(result.message || 'Position command sent. Check gimbal feedback.');
    return true;
  } catch (error) {
    if (command.action !== 'stop') notice(error.name === 'AbortError' ? 'Command timed out. Check the app connection.' : error.message, true);
    return false;
  }
}
const motion = new HoldController(send);
function release() {
  if (!activeButton && !motion.current) return;
  if (activeButton) activeButton.classList.remove('active');
  activeButton = null;
  void motion.stop();
}
function speedValue() {
  const value = Number(ui.speed.value);
  const max = Number(ui.speed.max);
  if (!Number.isFinite(value) || value < 0.1 || value > max) { notice(`Speed must be between 0.1 and ${max} °/s.`, true); return null; }
  return value;
}
function begin(button) {
  if (!ready || activeButton || presetBusy) return;
  const speed = speedValue();
  if (speed === null) return;
  const roll = Number(button.dataset.roll) * speed;
  const pitch = Number(button.dataset.pitch) * speed;
  if (motion.start(roll, pitch)) { activeButton = button; button.classList.add('active'); notice(''); }
}
ui.directions.forEach(button => {
  button.disabled = true;
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !ready) return;
    event.preventDefault();
    begin(button);
    if (activeButton === button) { try { button.setPointerCapture(event.pointerId); } catch { /* document-level release still applies */ } }
  });
  button.addEventListener('pointerup', release);
  button.addEventListener('pointercancel', release);
  button.addEventListener('lostpointercapture', release);
  button.addEventListener('keydown', event => {
    if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { event.preventDefault(); begin(button); }
  });
  button.addEventListener('keyup', event => {
    if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); release(); }
  });
});
document.addEventListener('pointerup', release);
function cancelPendingPreset() { presetGeneration += 1; }
window.addEventListener('blur', () => { cancelPendingPreset(); release(); });
window.addEventListener('pagehide', () => { cancelPendingPreset(); release(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { cancelPendingPreset(); release(); } });
ui.stop.addEventListener('click', () => {
  cancelPendingPreset();
  if (activeButton) activeButton.classList.remove('active');
  activeButton = null;
  void motion.stop();
  notice('Stop requested.');
});
async function applyPreset(action) {
  if (presetBusy || (action === 'center' ? !centerReady : !downViewReady)) return;
  presetBusy = true;
  const generation = ++presetGeneration;
  setAvailable(ready, centerReady, downViewReady);
  if (activeButton) activeButton.classList.remove('active');
  activeButton = null;
  try {
    if (!await sendPreset(motion, send, action, () => generation === presetGeneration && !stopped)
        && generation === presetGeneration) {
      notice('Command could not be sent. Check the app connection and gimbal state.', true);
    }
  } finally {
    presetBusy = false;
    setAvailable(ready, centerReady, downViewReady);
    void refreshStatus();
  }
}
ui.center.disabled = true;
ui.downView.disabled = true;
ui.center.addEventListener('click', () => { void applyPreset('center'); });
ui.downView.addEventListener('click', () => { void applyPreset('down_view'); });

function number(value) { return Number.isFinite(value) ? value.toFixed(1) : '—'; }
async function refreshStatus() {
  if (statusBusy || stopped) return;
  statusBusy = true;
  try {
    const data = await request(`${API}/status`);
    const connected = data.connected === true;
    const enabled = data.control_enabled === true;
    const teleop = data.teleop_enabled === true;
    const safe = !data.state?.hardware_error && ![1, 2, 3].includes(data.state?.gimbal_state);
    const limit = Math.min(PANEL_MAX_RATE, Number(data.max_rate_deg_s));
    const validLimit = Number.isFinite(limit) && limit >= 0.1;
    if (validLimit) {
      ui.speed.max = String(limit);
      if (Number(ui.speed.value) > limit) ui.speed.value = String(limit);
      const displayLimit = Number(limit.toFixed(2));
      ui.speedHint.textContent = limit < PANEL_MAX_RATE
        ? `Current limit ${displayLimit} °/s — change control.max_rate in Config to allow up to 80 °/s.`
        : `Maximum speed ${PANEL_MAX_RATE} °/s.`;
    } else {
      ui.speedHint.textContent = 'Configured speed limit is unavailable.';
    }
    const moveReady = connected && enabled && teleop && safe && validLimit && Number(data.rate_subscribers) > 0;
    const canCenter = connected && enabled && safe && Number(data.center_subscribers) > 0;
    const pitchLimit = Number(data.pitch_limit_deg);
    const canLookDown = canCenter && Number.isFinite(pitchLimit) && pitchLimit >= 90;
    ui.downView.title = Number.isFinite(pitchLimit) && pitchLimit < 90
      ? 'Down view requires a pitch limit of at least 90° in the app configuration.' : '';
    ui.downHint.textContent = ui.downView.title || 'Down view targets −90° pitch and 0° roll relative to the mount.';
    setAvailable(moveReady, canCenter, canLookDown);
    ui.connection.classList.toggle('online', connected);
    ui.connection.classList.toggle('offline', !connected);
    ui.connectionText.textContent = connected ? 'Feedback connected' : 'Waiting for feedback';
    const angles = Array.isArray(data.state?.relative_angle_rpy) ? data.state.relative_angle_rpy : [];
    ui.roll.textContent = connected ? number(angles[0]) : '—';
    ui.pitch.textContent = connected ? number(angles[1]) : '—';
    ui.error.textContent = connected && Number.isFinite(data.state?.hardware_error) ? String(data.state.hardware_error) : '—';
    ui.lastCommand.textContent = data.last_command ? `Last command: ${String(data.last_command)}` : 'No commands sent.';
    if (connected && !safe) notice('Gimbal reports a hardware error or protected state; commands are disabled.', true);
    else if (connected && !validLimit) notice('Configured speed limit is too low to use this panel.', true);
    else if (connected && !enabled) notice('Control is disabled in the app configuration. Restart after changing it.');
    else if (connected && !teleop) notice('Direction control is disabled in the app configuration.');
    else if (connected && !moveReady) notice('Waiting for the gimbal driver command subscriber.');
  } catch (error) {
    setAvailable(false, false);
    ui.connection.classList.remove('online'); ui.connection.classList.add('offline');
    ui.connectionText.textContent = 'Panel disconnected';
    ui.roll.textContent = '—'; ui.pitch.textContent = '—'; ui.error.textContent = '—';
    notice(error.name === 'AbortError' ? 'Panel connection timed out.' : error.message, true);
  } finally { statusBusy = false; }
}
async function refreshLogs() {
  if (logBusy || stopped) return;
  const sessionToken = token();
  if (!sessionToken) { ui.logStatus.textContent = 'Sign in required'; return; }
  logBusy = true;
  try {
    // AivudaOS itself currently authenticates this endpoint with a query token.
    const url = `/aivuda_os/api/apps/${encodeURIComponent(APP_ID)}/logs?offset=${logOffset}&limit=32768&token=${encodeURIComponent(sessionToken)}`;
    const response = await fetch(url, { cache: 'no-store', referrerPolicy: 'no-referrer' });
    if (!response.ok) throw new Error(response.status === 401 ? 'Sign in required' : `Log unavailable (${response.status})`);
    const data = await response.json();
    if (data.reset || !logInitialized) { ui.output.textContent = ''; logInitialized = true; }
    if (data.chunk) {
      // Logs are text; never interpret application output as HTML.
      const atBottom = ui.output.scrollHeight - ui.output.scrollTop - ui.output.clientHeight < 40;
      const plain = String(data.chunk).replace(/\x1b\[[0-9;]*[A-Za-z]/g, '');
      ui.output.textContent = (ui.output.textContent + plain).slice(-120000);
      if (atBottom) ui.output.scrollTop = ui.output.scrollHeight;
    }
    logOffset = Number.isFinite(data.next_offset) ? data.next_offset : logOffset;
    ui.logStatus.textContent = 'Live';
  } catch (error) { ui.logStatus.textContent = error.message === 'Sign in required' ? error.message : 'Log unavailable'; }
  finally { logBusy = false; }
}
ui.clear.addEventListener('click', () => { ui.output.textContent = ''; logInitialized = true; });
setAvailable(false, false);
void refreshStatus(); void refreshLogs();
const statusTimer = setInterval(refreshStatus, 1500);
const logTimer = setInterval(refreshLogs, 1000);
window.addEventListener('pagehide', () => { stopped = true; clearInterval(statusTimer); clearInterval(logTimer); });
