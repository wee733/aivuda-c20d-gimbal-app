import test from 'node:test';
import assert from 'node:assert/strict';
import { HoldController, sendCommandAfterStop, sendPreset } from '../ui/control.mjs';

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
test('hold starts a bounded command and release sends stop', async () => {
  const commands = [];
  const control = new HoldController(async command => { commands.push(command); return true; }, 1000, () => 'gesture-1');
  assert.equal(control.start(5, 0), true);
  await tick();
  assert.deepEqual(commands[0], {action:'move', gesture_id:'gesture-1', roll_deg_s:5, pitch_deg_s:0});
  await control.stop();
  assert.deepEqual(commands.at(-1), {action:'stop', gesture_id:'gesture-1'});
  assert.equal(control.current, null);
});
test('rejects invalid or excessive speed', () => {
  const commands = [];
  const control = new HoldController(async command => { commands.push(command); }, 1000, () => 'gesture-1');
  assert.equal(control.start(80.1, 0), false);
  assert.equal(control.start(NaN, 0), false);
  assert.equal(control.start(0, 0), false);
  assert.deepEqual(commands, []);
});
test('a move completing after release is followed by another stop', async () => {
  const commands = [];
  let finishMove;
  const control = new HoldController(command => {
    commands.push(command.action);
    return command.action === 'move' ? new Promise(resolve => { finishMove = resolve; }) : Promise.resolve();
  }, 1000, () => 'gesture-1');
  control.start(0, 5);
  await tick();
  const stopping = control.stop();
  await tick();
  assert.deepEqual(commands, ['move', 'stop']);
  finishMove();
  await stopping;
  assert.deepEqual(commands, ['move', 'stop', 'stop']);
});
test('accepts 80 degrees per second and rejects values above 80', async () => {
  const commands = [];
  const control = new HoldController(async command => { commands.push(command); return true; }, 1000, () => 'gesture-80');
  assert.equal(control.start(80, -80), true);
  await tick();
  await control.stop();
  assert.equal(commands[0].roll_deg_s, 80);
  assert.equal(commands[0].pitch_deg_s, -80);
  assert.equal(control.start(-80.01, 0), false);
});
test('down-view waits for in-flight motion and stop, then sends exactly one preset', async () => {
  const commands = [];
  let finishMove;
  const send = command => {
    commands.push(command.action);
    return command.action === 'move' ? new Promise(resolve => { finishMove = resolve; }) : Promise.resolve(true);
  };
  const control = new HoldController(send, 1000, () => 'gesture-3');
  assert.equal(control.start(0, -10), true);
  await tick();
  const preset = sendPreset(control, send, 'down_view');
  await tick();
  assert.deepEqual(commands, ['move', 'stop']);
  finishMove(true);
  assert.equal(await preset, true);
  assert.deepEqual(commands, ['move', 'stop', 'stop', 'down_view']);
});
test('preset is withheld when stop fails', async () => {
  const commands = [];
  const send = async command => { commands.push(command.action); return command.action !== 'stop'; };
  const control = new HoldController(send, 1000, () => 'gesture-4');
  assert.equal(await sendPreset(control, send, 'down_view'), false);
  assert.deepEqual(commands, ['stop']);
});
test('a canceled preset is not sent when the preceding stop completes', async () => {
  const commands = [];
  let finishStop;
  let current = true;
  const send = command => {
    commands.push(command.action);
    return new Promise(resolve => { finishStop = resolve; });
  };
  const control = new HoldController(send, 1000, () => 'gesture-5');
  const preset = sendPreset(control, send, 'down_view', () => current);
  await tick();
  current = false;
  finishStop(true);
  assert.equal(await preset, false);
  assert.deepEqual(commands, ['stop']);
});
test('a rejected motion clears the hold and requests stop', async () => {
  const commands = [];
  const control = new HoldController(async command => { commands.push(command.action); return command.action !== 'move'; }, 1000, () => 'gesture-2');
  control.start(-5, 0);
  await tick();
  assert.equal(control.current, null);
  assert.equal(commands[0], 'move');
  assert.ok(commands.slice(1).every(command => command === 'stop'));
  assert.ok(commands.length >= 2);
});
test('proportional_rate hold and sendCommandAfterStop work cleanly', async () => {
  const commands = [];
  const control = new HoldController(async command => { commands.push(command); return true; }, 1000, () => 'gesture-prop');
  assert.equal(control.start(5, -5, 'proportional_rate'), true);
  await tick();
  assert.deepEqual(commands[0], {action:'proportional_rate', gesture_id:'gesture-prop', roll_deg_s:5, pitch_deg_s:-5});
  assert.equal(await sendCommandAfterStop(control, async c => { commands.push(c); return true; }, {action:'attitude', roll_deg:0, pitch_deg:15}), true);
  assert.deepEqual(commands.slice(-2), [{action:'stop', gesture_id:'gesture-prop'}, {action:'attitude', roll_deg:0, pitch_deg:15}]);
});
