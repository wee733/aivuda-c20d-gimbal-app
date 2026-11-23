import { bindLocale, t } from './i18n/index.mjs';
import { HoldController, PANEL_MAX_RATE, sendCommandAfterStop, sendPreset } from './control.mjs';

const API = '/c20d-control/api';
const APP_ID = 'app_gimbal_control_c20d_20260526_512d09';
const $ = id => document.getElementById(id);

const ui = {
  connection: $('connection'), connectionText: $('connection-text'),
  modeChip: $('mode-chip'),
  tabs: [...document.querySelectorAll('.mode-tab')],
  panels: {
    hori_rate: $('panel-hori-rate'),
    hori_angle: $('panel-hori-angle'),
    fpv_angle: $('panel-fpv-angle'),
  },
  directions: [...document.querySelectorAll('.direction')],
  stop: $('stop'), speed: $('speed'), speedPresets: [...document.querySelectorAll('.speed-preset')],
  speedHint: $('speed-limit-hint'),
  useProportional: $('use-proportional'), propRateHint: $('prop-rate-hint'),
  attRoll: $('att-roll'), attRollRange: $('att-roll-range'),
  attPitch: $('att-pitch'), attPitchRange: $('att-pitch-range'),
  attLimitHint: $('att-limit-hint'),
  sendAttitude: $('send-attitude'), attLevel: $('att-level'), attDown: $('att-down'),
  fpvRoll: $('fpv-roll'), fpvRollRange: $('fpv-roll-range'),
  fpvPitch: $('fpv-pitch'), fpvPitchRange: $('fpv-pitch-range'),
  fpvSens: $('fpv-sens'), fpvSensRange: $('fpv-sens-range'),
  fpvLimitHint: $('fpv-limit-hint'),
  sendFpv: $('send-fpv'), fpvZero: $('fpv-zero'),
  center: $('center'), downView: $('down-view'), downHint: $('down-view-hint'),
  cmdEnable: $('cmd-enable'), cmdDisable: $('cmd-disable'),
  cmdHold: $('cmd-hold'), cmdCalibrate: $('cmd-calibrate'),
  stateBadge: $('state-badge'), mountBadge: $('mount-badge'),
  tempBadge: $('temp-badge'), error: $('error-value'), cmdReplyBadge: $('cmd-reply-badge'),
  roll: $('roll-value'), pitch: $('pitch-value'),
  attRollVal: $('att-roll-value'), attPitchVal: $('att-pitch-value'), attYawVal: $('att-yaw-value'),
  rateRollVal: $('rate-roll-value'), ratePitchVal: $('rate-pitch-value'),
  output: $('output'), logStatus: $('log-status'), lastCommand: $('last-command'),
  clear: $('clear'), notice: $('notice'),
};

let activeMode = 'hori_rate';
let moveReady = false;
let propMoveReady = false;
let centerReady = false;
let downViewReady = false;
let attitudeReady = false;
let fpvReady = false;
let serviceReady = false;
let canEnableMotors = false;
let canCalibrate = false;
let currentRollLimit = 60;
let currentPitchLimit = 120;
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

let statusBanner = { text: '', isError: false };
let actionBanner = { text: '', isError: false, until: 0 };

const token = () => localStorage.getItem('aivuda_ui_token') || '';

function renderNotice() {
  const now = Date.now();
  if (actionBanner.text && (now < actionBanner.until || !statusBanner.text)) {
    ui.notice.textContent = actionBanner.text;
    ui.notice.classList.toggle('error', actionBanner.isError);
    return;
  }
  ui.notice.textContent = statusBanner.text || '';
  ui.notice.classList.toggle('error', statusBanner.isError);
}

function notice(message, isError = false, durationMs = 5000) {
  actionBanner = message ? { text: message, isError, until: Date.now() + durationMs } : { text: '', isError: false, until: 0 };
  renderNotice();
}

function setStatusNotice(message = '', isError = false) {
  statusBanner = { text: message, isError };
  renderNotice();
}

function rateActionSelected() {
  return ui.useProportional.checked && !ui.useProportional.disabled ? 'proportional_rate' : 'move';
}

function effectiveMoveReady() {
  return rateActionSelected() === 'proportional_rate' ? propMoveReady : moveReady;
}

function syncButtonStates() {
  const canMove = effectiveMoveReady();
  ui.directions.forEach(button => { button.disabled = !canMove || presetBusy; });
  ui.center.disabled = !centerReady || presetBusy;
  ui.downView.disabled = !downViewReady || presetBusy;
  ui.sendAttitude.disabled = !attitudeReady || presetBusy;
  ui.attLevel.disabled = !attitudeReady || presetBusy;
  ui.attDown.disabled = !attitudeReady || presetBusy || currentPitchLimit < 90;
  ui.sendFpv.disabled = !fpvReady || presetBusy;
  ui.fpvZero.disabled = !fpvReady || presetBusy;
  ui.cmdEnable.disabled = !canEnableMotors || presetBusy;
  ui.cmdDisable.disabled = !serviceReady || !centerReady || presetBusy;
  ui.cmdHold.disabled = !serviceReady || !centerReady || presetBusy;
  ui.cmdCalibrate.disabled = !canCalibrate || presetBusy;
  if (!canMove && activeButton) release();
}

function switchMode(mode) {
  if (!ui.panels[mode] || mode === activeMode) return;
  if (activeButton) release();
  activeMode = mode;
  for (const tab of ui.tabs) {
    const selected = tab.dataset.mode === mode;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
  }
  for (const [key, panel] of Object.entries(ui.panels)) {
    panel.classList.toggle('hidden', key !== mode);
  }
  const chipLabels = { hori_rate: 'HORI-RATE', hori_angle: 'HORI-ANGL', fpv_angle: 'FPVM-ANGL' };
  ui.modeChip.textContent = chipLabels[mode] || 'HORI-RATE';
}

ui.tabs.forEach(tab => {
  tab.addEventListener('click', () => switchMode(tab.dataset.mode));
});

function linkPair(numInput, rangeInput) {
  numInput.addEventListener('input', () => {
    const v = Number(numInput.value);
    if (Number.isFinite(v)) rangeInput.value = String(v);
  });
  rangeInput.addEventListener('input', () => {
    numInput.value = rangeInput.value;
  });
}
linkPair(ui.attRoll, ui.attRollRange);
linkPair(ui.attPitch, ui.attPitchRange);
linkPair(ui.fpvRoll, ui.fpvRollRange);
linkPair(ui.fpvPitch, ui.fpvPitchRange);
linkPair(ui.fpvSens, ui.fpvSensRange);

ui.speedPresets.forEach(button => {
  button.addEventListener('click', () => {
    const max = Number(ui.speed.max);
    const val = Number(button.dataset.speed);
    if (Number.isFinite(max) && val > max) {
      ui.speed.value = String(Number(max.toFixed(1)));
    } else {
      ui.speed.value = String(val);
    }
  });
});

ui.useProportional.addEventListener('change', () => {
  if (activeButton) release();
  syncButtonStates();
});

async function request(path, options = {}, timeoutMs = 2500) {
  const sessionToken = token();
  if (!sessionToken) throw new Error(t('login'));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), Authorization: `Bearer ${sessionToken}` },
      signal: controller.signal,
      cache: 'no-store',
    });
    if (response.status === 401 || response.status === 403) throw new Error(t('expired'));
    let body = {};
    try { body = await response.json(); } catch { /* handled below */ }
    if (!response.ok || body.ok === false) {
      throw new Error(body.error || body.detail || t('requestFailed', { status: response.status }));
    }
    return body;
  } finally {
    clearTimeout(timeout);
  }
}

async function send(command) {
  try {
    const payload = { ...command, client_id: clientId, seq: ++sequence };
    const result = await request(`${API}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      keepalive: command.action === 'stop',
      body: JSON.stringify(payload),
    });
    if (!['move', 'proportional_rate', 'stop'].includes(command.action)) {
      notice(result.message || t('positionSent'));
    }
    return true;
  } catch (error) {
    if (command.action !== 'stop') {
      notice(error.name === 'AbortError' ? t('timeout') : error.message, true);
    }
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
  if (!Number.isFinite(value) || value < 0.1 || value > max) {
    notice(t('speedRange', { max }), true);
    return null;
  }
  return value;
}

function begin(button) {
  if (!effectiveMoveReady() || activeButton || presetBusy) return;
  const speed = speedValue();
  if (speed === null) return;
  const roll = Number(button.dataset.roll) * speed;
  const pitch = Number(button.dataset.pitch) * speed;
  if (motion.start(roll, pitch, rateActionSelected())) {
    activeButton = button;
    button.classList.add('active');
    notice('');
  }
}

ui.directions.forEach(button => {
  button.disabled = true;
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !effectiveMoveReady()) return;
    event.preventDefault();
    begin(button);
    if (activeButton === button) {
      try { button.setPointerCapture(event.pointerId); } catch { /* document-level release still applies */ }
    }
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
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { cancelPendingPreset(); release(); }
});

ui.stop.addEventListener('click', () => {
  cancelPendingPreset();
  if (activeButton) activeButton.classList.remove('active');
  activeButton = null;
  void motion.stop();
  notice(t('stopRequested'));
});

async function runSerialized(runFn) {
  if (presetBusy) return;
  presetBusy = true;
  const generation = ++presetGeneration;
  syncButtonStates();
  if (activeButton) activeButton.classList.remove('active');
  activeButton = null;
  try {
    const ok = await runFn(() => generation === presetGeneration && !stopped);
    if (!ok && generation === presetGeneration) {
      notice(t('presetFailed'), true);
    }
  } finally {
    presetBusy = false;
    syncButtonStates();
    void refreshStatus();
  }
}

function applyPreset(action) {
  if ((action === 'center' ? !centerReady : !downViewReady)) return;
  void runSerialized(isCurrent => sendPreset(motion, send, action, isCurrent));
}

function parseAngle(input, axisName, limit) {
  const v = Number(input.value);
  if (!Number.isFinite(v) || Math.abs(v) > limit) {
    notice(t('angleRange', { axis: axisName, limit }), true);
    return null;
  }
  return v;
}

function sendAttitudeTarget(rollDeg, pitchDeg) {
  if (!attitudeReady) return;
  if (Math.abs(rollDeg) > currentRollLimit) {
    notice(t('angleRange', { axis: t('rollTarget'), limit: currentRollLimit }), true);
    return;
  }
  if (Math.abs(pitchDeg) > currentPitchLimit) {
    notice(t('angleRange', { axis: t('pitchTarget'), limit: currentPitchLimit }), true);
    return;
  }
  ui.attRoll.value = String(rollDeg);
  ui.attRollRange.value = String(rollDeg);
  ui.attPitch.value = String(pitchDeg);
  ui.attPitchRange.value = String(pitchDeg);
  void runSerialized(isCurrent => sendCommandAfterStop(
    motion, send, { action: 'attitude', roll_deg: rollDeg, pitch_deg: pitchDeg }, isCurrent));
}

function sendFpvTarget(rollDeg, pitchDeg, sensitivity) {
  if (!fpvReady) return;
  if (Math.abs(rollDeg) > currentRollLimit) {
    notice(t('angleRange', { axis: t('rollTarget'), limit: currentRollLimit }), true);
    return;
  }
  if (Math.abs(pitchDeg) > currentPitchLimit) {
    notice(t('angleRange', { axis: t('pitchTarget'), limit: currentPitchLimit }), true);
    return;
  }
  if (!Number.isInteger(sensitivity) || sensitivity < -16 || sensitivity > 15) {
    notice(t('sensRange'), true);
    return;
  }
  ui.fpvRoll.value = String(rollDeg);
  ui.fpvRollRange.value = String(rollDeg);
  ui.fpvPitch.value = String(pitchDeg);
  ui.fpvPitchRange.value = String(pitchDeg);
  ui.fpvSens.value = String(sensitivity);
  ui.fpvSensRange.value = String(sensitivity);
  void runSerialized(isCurrent => sendCommandAfterStop(
    motion, send,
    { action: 'fpv_angle', roll_deg: rollDeg, pitch_deg: pitchDeg, fpv_sensitivity: sensitivity },
    isCurrent));
}

function triggerDeviceCommand(code) {
  if (code === 3 && !window.confirm(t('confirmDisable'))) return;
  if (code === 1 && !window.confirm(t('confirmCalibrate'))) return;
  void runSerialized(isCurrent => sendCommandAfterStop(
    motion, send, { action: 'device_command', command: code }, isCurrent));
}

ui.center.addEventListener('click', () => applyPreset('center'));
ui.downView.addEventListener('click', () => applyPreset('down_view'));
ui.sendAttitude.addEventListener('click', () => {
  const r = parseAngle(ui.attRoll, t('rollTarget'), currentRollLimit);
  const p = parseAngle(ui.attPitch, t('pitchTarget'), currentPitchLimit);
  if (r !== null && p !== null) sendAttitudeTarget(r, p);
});
ui.attLevel.addEventListener('click', () => sendAttitudeTarget(0, 0));
ui.attDown.addEventListener('click', () => sendAttitudeTarget(0, -90));

ui.sendFpv.addEventListener('click', () => {
  const r = parseAngle(ui.fpvRoll, t('rollTarget'), currentRollLimit);
  const p = parseAngle(ui.fpvPitch, t('pitchTarget'), currentPitchLimit);
  const sens = Number(ui.fpvSens.value);
  if (r !== null && p !== null) {
    if (!Number.isInteger(sens) || sens < -16 || sens > 15) {
      notice(t('sensRange'), true);
      return;
    }
    sendFpvTarget(r, p, sens);
  }
});
ui.fpvZero.addEventListener('click', () => {
  const sens = Number(ui.fpvSens.value);
  sendFpvTarget(0, 0, Number.isInteger(sens) && sens >= -16 && sens <= 15 ? sens : 0);
});

ui.cmdEnable.addEventListener('click', () => triggerDeviceCommand(2));
ui.cmdDisable.addEventListener('click', () => triggerDeviceCommand(3));
ui.cmdHold.addEventListener('click', () => triggerDeviceCommand(4));
ui.cmdCalibrate.addEventListener('click', () => triggerDeviceCommand(1));

function number(value) { return Number.isFinite(value) ? value.toFixed(1) : '—'; }

function stateText(code) {
  const map = { 0: 'state0', 1: 'state1', 2: 'state2', 3: 'state3', 4: 'state4', 5: 'state5' };
  return map[code] ? t(map[code]) : t('stateUnknown', { code });
}

function setBadge(el, text, tone = '') {
  el.textContent = text;
  el.classList.remove('ok', 'warn', 'err');
  if (tone) el.classList.add(tone);
}

function updateAngleBounds(rollLimit, pitchLimit) {
  currentRollLimit = rollLimit;
  currentPitchLimit = pitchLimit;
  for (const el of [ui.attRoll, ui.attRollRange, ui.fpvRoll, ui.fpvRollRange]) {
    el.min = String(-rollLimit);
    el.max = String(rollLimit);
  }
  for (const el of [ui.attPitch, ui.attPitchRange, ui.fpvPitch, ui.fpvPitchRange]) {
    el.min = String(-pitchLimit);
    el.max = String(pitchLimit);
  }
  const hint = t('angleLimitHint', { rollLimit, pitchLimit });
  ui.attLimitHint.textContent = hint;
  ui.fpvLimitHint.textContent = hint;
}

async function refreshStatus() {
  if (statusBusy || stopped) return;
  statusBusy = true;
  try {
    const data = await request(`${API}/status`);
    const connected = data.connected === true;
    const enabled = data.control_enabled === true;
    const state = data.state || {};
    const hwErr = Number(state.hardware_error || 0);
    const gimbalState = state.gimbal_state;
    const safe = hwErr === 0 && ![1, 2, 3].includes(gimbalState);

    const limit = Math.min(PANEL_MAX_RATE, Number(data.max_rate_deg_s));
    const validLimit = Number.isFinite(limit) && limit >= 0.1;
    if (validLimit) {
      const displayLimit = Number(limit.toFixed(2));
      ui.speed.max = String(displayLimit);
      if (document.activeElement !== ui.speed && Number(ui.speed.value) > displayLimit) {
        ui.speed.value = String(displayLimit);
      }
      ui.speedHint.textContent = limit < PANEL_MAX_RATE
        ? t('limitHint', { displayLimit })
        : t('maxHint', { max: PANEL_MAX_RATE });
    } else {
      ui.speedHint.textContent = t('noLimit');
    }

    const rollLimit = Number.isFinite(Number(data.roll_limit_deg)) && Number(data.roll_limit_deg) > 0
      ? Math.min(60, Number(data.roll_limit_deg)) : 60;
    const pitchLimit = Number.isFinite(Number(data.pitch_limit_deg)) && Number(data.pitch_limit_deg) > 0
      ? Math.min(120, Number(data.pitch_limit_deg)) : 120;
    updateAngleBounds(rollLimit, pitchLimit);

    const auxEnabled = data.enable_auxiliary_interfaces === true;
    const propSubs = Number(data.proportional_rate_subscribers || 0);
    ui.useProportional.disabled = !auxEnabled || propSubs < 1;
    if (ui.useProportional.disabled && ui.useProportional.checked) {
      ui.useProportional.checked = false;
    }
    ui.propRateHint.textContent = auxEnabled ? t('propRateReady') : t('propRateUnavailable');

    serviceReady = data.hold_service_ready === true;
    moveReady = connected && enabled && safe && validLimit && Number(data.rate_subscribers) > 0;
    propMoveReady = connected && enabled && safe && validLimit && auxEnabled && propSubs > 0;
    centerReady = connected && enabled && safe && Number(data.center_subscribers) > 0;
    fpvReady = centerReady && serviceReady;
    attitudeReady = connected && enabled && safe && serviceReady && Number(data.attitude_subscribers || 0) > 0;
    downViewReady = fpvReady && pitchLimit >= 90;
    canEnableMotors = connected && enabled && serviceReady && hwErr === 0 && ![1, 3].includes(gimbalState);
    canCalibrate = connected && enabled && safe && serviceReady && state.temperature_ready === true;

    ui.downView.title = pitchLimit < 90 ? t('downLimit') : '';
    ui.downHint.textContent = ui.downView.title || t('downHint');
    syncButtonStates();

    ui.connection.classList.toggle('online', connected);
    ui.connection.classList.toggle('offline', !connected);
    ui.connectionText.textContent = connected ? t('online') : t('waiting');

    if (connected) {
      const stateTone = gimbalState === 3 || gimbalState === 2 ? 'warn' : (gimbalState === 4 || gimbalState === 5 ? 'ok' : '');
      setBadge(ui.stateBadge, stateText(gimbalState), stateTone);
      setBadge(ui.mountBadge, state.upright ? t('mountUpright') : t('mountInverted'));
      setBadge(ui.tempBadge, state.temperature_ready ? t('tempReady') : t('tempWarming'),
        state.temperature_ready ? 'ok' : 'warn');
      setBadge(ui.error, hwErr === 0 ? t('faultOk') : String(hwErr), hwErr === 0 ? 'ok' : 'err');
      const cmdCode = Number(state.command_code || 0);
      const cmdStatus = Number(state.command_status || 0);
      if (cmdCode > 0) {
        const stText = t(`cmdStatus${cmdStatus <= 2 ? cmdStatus : 0}`);
        const stTone = cmdStatus === 1 ? 'ok' : (cmdStatus === 2 ? 'err' : 'warn');
        setBadge(ui.cmdReplyBadge, `#${cmdCode} · ${stText}`, stTone);
      } else {
        setBadge(ui.cmdReplyBadge, '—');
      }

      const rel = Array.isArray(state.relative_angle_rpy) ? state.relative_angle_rpy : [];
      const att = Array.isArray(state.camera_attitude_rpy) ? state.camera_attitude_rpy : [];
      const rate = Array.isArray(state.camera_rate_rpy) ? state.camera_rate_rpy : [];
      ui.roll.textContent = number(rel[0]);
      ui.pitch.textContent = number(rel[1]);
      ui.attRollVal.textContent = number(att[0]);
      ui.attPitchVal.textContent = number(att[1]);
      ui.attYawVal.textContent = number(att[2]);
      ui.rateRollVal.textContent = number(rate[0]);
      ui.ratePitchVal.textContent = number(rate[1]);
    } else {
      for (const el of [ui.stateBadge, ui.mountBadge, ui.tempBadge, ui.error, ui.cmdReplyBadge]) {
        setBadge(el, '—');
      }
      for (const el of [ui.roll, ui.pitch, ui.attRollVal, ui.attPitchVal, ui.attYawVal, ui.rateRollVal, ui.ratePitchVal]) {
        el.textContent = '—';
      }
    }

    ui.lastCommand.textContent = data.last_command
      ? t('lastCommand', { message: String(data.last_command) }) : t('noCommands');

    if (connected && hwErr !== 0) setStatusNotice(t('hwError', { code: hwErr }), true);
    else if (connected && gimbalState === 3) setStatusNotice(t('stateProtected'), true);
    else if (connected && gimbalState === 2) setStatusNotice(t('stateStopped'), false);
    else if (connected && gimbalState === 1) setStatusNotice(t('stateInitializing'), false);
    else if (connected && !validLimit) setStatusNotice(t('limitTooLow'), true);
    else if (connected && !enabled) setStatusNotice(t('disabled'), false);
    else if (connected && !moveReady) setStatusNotice(t('noSubscriber'), false);
    else setStatusNotice('');
  } catch (error) {
    moveReady = false; propMoveReady = false; centerReady = false; downViewReady = false;
    attitudeReady = false; fpvReady = false; serviceReady = false;
    canEnableMotors = false; canCalibrate = false;
    syncButtonStates();
    ui.connection.classList.remove('online');
    ui.connection.classList.add('offline');
    ui.connectionText.textContent = t('offline');
    for (const el of [ui.stateBadge, ui.mountBadge, ui.tempBadge, ui.error, ui.cmdReplyBadge]) {
      setBadge(el, '—');
    }
    for (const el of [ui.roll, ui.pitch, ui.attRollVal, ui.attPitchVal, ui.attYawVal, ui.rateRollVal, ui.ratePitchVal]) {
      el.textContent = '—';
    }
    setStatusNotice(error.name === 'AbortError' ? t('panelTimeout') : error.message, true);
  } finally {
    statusBusy = false;
  }
}

async function refreshLogs() {
  if (logBusy || stopped) return;
  const sessionToken = token();
  if (!sessionToken) { ui.logStatus.textContent = t('signIn'); return; }
  logBusy = true;
  try {
    // AivudaOS itself currently authenticates this endpoint with a query token.
    const url = `/aivuda_os/api/apps/${encodeURIComponent(APP_ID)}/logs?offset=${logOffset}&limit=32768&token=${encodeURIComponent(sessionToken)}`;
    const response = await fetch(url, { cache: 'no-store', referrerPolicy: 'no-referrer' });
    if (!response.ok) throw new Error(response.status === 401 ? t('signIn') : t('logError', { status: response.status }));
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
    ui.logStatus.textContent = t('live');
  } catch (error) {
    ui.logStatus.textContent = error.message === t('signIn') ? error.message : t('noLog');
  } finally {
    logBusy = false;
  }
}

ui.clear.addEventListener('click', () => { ui.output.textContent = ''; logInitialized = true; });
syncButtonStates();
bindLocale(() => { void refreshStatus(); void refreshLogs(); });
const statusTimer = setInterval(refreshStatus, 1000);
const logTimer = setInterval(refreshLogs, 1000);
window.addEventListener('pagehide', () => { stopped = true; clearInterval(statusTimer); clearInterval(logTimer); });
