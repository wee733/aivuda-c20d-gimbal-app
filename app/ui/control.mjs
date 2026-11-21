/* A held direction is refreshed; release and focus loss immediately request zero. */
export const PANEL_MAX_RATE = 80;

export class HoldController {
  constructor(send, intervalMs = 100, newGesture = () => crypto.randomUUID()) {
    this.send = send;
    this.intervalMs = intervalMs;
    this.newGesture = newGesture;
    this.timer = null;
    this.current = null;
    this.inFlight = false;
    this.pending = null;
    this.generation = 0;
  }
  start(roll, pitch) {
    if (!Number.isFinite(roll) || !Number.isFinite(pitch) || Math.abs(roll) > PANEL_MAX_RATE || Math.abs(pitch) > PANEL_MAX_RATE || (!roll && !pitch)) return false;
    if (this.current) this.stop();
    this.generation += 1;
    this.current = { action: 'move', gesture_id: this.newGesture(), roll_deg_s: roll, pitch_deg_s: pitch };
    this.pulse();
    this.timer = setInterval(() => this.pulse(), this.intervalMs);
    return true;
  }
  pulse() {
    if (!this.current || this.inFlight) return;
    const generation = this.generation;
    const command = this.current;
    this.inFlight = true;
    const pending = Promise.resolve().then(() => this.send(command)).then(ok => {
      if (ok === false && generation === this.generation) void this.stop();
    }).catch(() => {
      if (generation === this.generation) void this.stop();
    }).finally(async () => {
      // Complete any compensating stop before allowing a position preset.
      if (generation !== this.generation) {
        await Promise.resolve().then(() => this.send({action: 'stop', gesture_id: command.gesture_id})).catch(() => {});
      }
      this.inFlight = false;
    });
    this.pending = pending;
    pending.finally(() => { if (this.pending === pending) this.pending = null; }).catch(() => {});
  }
  stop() {
    const pending = this.pending;
    const gesture = this.current?.gesture_id;
    this.generation += 1;
    this.current = null;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    const command = gesture ? {action: 'stop', gesture_id: gesture} : {action: 'stop'};
    const sent = Promise.resolve().then(() => this.send(command)).catch(() => false);
    return Promise.allSettled([sent, pending]).then(([result]) =>
      result.status === 'fulfilled' && result.value !== false);
  }
}

export async function sendPreset(motion, send, action, isCurrent = () => true) {
  if (!['center', 'down_view'].includes(action)) throw new Error('Unknown preset');
  if (!await motion.stop() || !isCurrent()) return false;
  return send({action});
}
