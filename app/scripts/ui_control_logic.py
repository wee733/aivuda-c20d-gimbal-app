"""Transport-independent validation, access checks and command watchdog."""
import ipaddress
import json
import math
import re
from collections import OrderedDict
import threading
import time
from urllib.parse import urlsplit

MAX_RATE_DEG_S = 80.0
MAX_ROLL_DEG = 60.0
MAX_PITCH_DEG = 120.0
WATCHDOG_SECONDS = 0.35
VALID_DEVICE_COMMANDS = (1, 2, 3, 4)


def _loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _authority(value, scheme):
    """Normalize a host header or origin authority, including default ports."""
    try:
        parsed = urlsplit(f'{scheme}://{value}')
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        return (parsed.hostname.lower(), parsed.port or (443 if scheme == 'https' else 80))
    except ValueError:
        return None


def allowed_origin(headers, client_ip):
    """Allow same-origin browser requests or direct loopback clients, not cross-site."""
    if not _loopback(client_ip):
        return False
    fetch_site = headers.get('Sec-Fetch-Site', '').lower()
    if fetch_site in ('cross-site', 'same-site'):
        return False
    origin = headers.get('Origin', '')
    host = headers.get('Host', '')
    if origin:
        try:
            parsed = urlsplit(origin)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment):
                return False
            forwarded_proto = headers.get('X-Forwarded-Proto', parsed.scheme).lower()
            if forwarded_proto != parsed.scheme:
                return False
            return _authority(parsed.netloc, parsed.scheme) == _authority(host, parsed.scheme)
        except ValueError:
            return False
    if fetch_site == 'same-origin':
        return bool(host)
    # Explicit direct local callers must target the loopback-bound service itself.
    auth = _authority(host, 'http')
    return bool(auth and auth[1] == 19291 and _loopback(auth[0]))


def bearer_token(headers):
    value = headers.get('Authorization', '')
    prefix = 'Bearer '
    if not value.startswith(prefix):
        return None
    token = value[len(prefix):]
    if not token or len(token) > 4096 or any(ch.isspace() for ch in token):
        return None
    return token


_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _parse_finite_number(obj, name, minimum, maximum):
    raw = obj[name]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        value = float(raw)
    except (OverflowError, ValueError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f'{name} must be between {minimum:g} and {maximum:g}')
    return value


def parse_command_request(body):
    """Parse and validate a command JSON payload into (action, arg1, arg2, client, seq, gesture, extra)."""
    try:
        obj = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite number')))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise ValueError('Invalid JSON') from None
    if not isinstance(obj, dict):
        raise ValueError('Expected a JSON object')
    action, client, seq, gesture = (obj.get('action'), obj.get('client_id'),
                                     obj.get('seq'), obj.get('gesture_id'))
    if not isinstance(client, str) or not _UUID.fullmatch(client):
        raise ValueError('client_id must be a lowercase UUID')
    if type(seq) is not int or not 1 <= seq <= 9007199254740991:
        raise ValueError('seq must be a positive safe integer')
    if gesture is not None and (not isinstance(gesture, str) or not _UUID.fullmatch(gesture)):
        raise ValueError('gesture_id must be a lowercase UUID')

    base = {'action', 'client_id', 'seq'}
    if action == 'stop':
        if set(obj) not in (base, base | {'gesture_id'}):
            raise ValueError('Unexpected command fields')
        return action, 0.0, 0.0, client, seq, gesture, None

    if action in ('center', 'down_view'):
        if set(obj) != base:
            raise ValueError('Unexpected command fields')
        return action, 0.0, 0.0, client, seq, None, None

    if action in ('move', 'proportional_rate'):
        if not gesture or set(obj) != base | {'gesture_id', 'roll_deg_s', 'pitch_deg_s'}:
            raise ValueError(f'Invalid {action} command fields')
        roll = _parse_finite_number(obj, 'roll_deg_s', -MAX_RATE_DEG_S, MAX_RATE_DEG_S)
        pitch = _parse_finite_number(obj, 'pitch_deg_s', -MAX_RATE_DEG_S, MAX_RATE_DEG_S)
        if [roll, pitch] == [0.0, 0.0]:
            raise ValueError('Use stop for zero rate')
        return action, roll, pitch, client, seq, gesture, None

    if action == 'fpv_angle':
        if set(obj) not in (base | {'roll_deg', 'pitch_deg'},
                            base | {'roll_deg', 'pitch_deg', 'fpv_sensitivity'}):
            raise ValueError('Unexpected fpv_angle command fields')
        roll = _parse_finite_number(obj, 'roll_deg', -MAX_ROLL_DEG, MAX_ROLL_DEG)
        pitch = _parse_finite_number(obj, 'pitch_deg', -MAX_PITCH_DEG, MAX_PITCH_DEG)
        sensitivity = obj.get('fpv_sensitivity', 0)
        if type(sensitivity) is not int or not -16 <= sensitivity <= 15:
            raise ValueError('fpv_sensitivity must be an integer between -16 and 15')
        return action, roll, pitch, client, seq, None, {'fpv_sensitivity': sensitivity}

    if action == 'attitude':
        if set(obj) != base | {'roll_deg', 'pitch_deg'}:
            raise ValueError('Unexpected attitude command fields')
        roll = _parse_finite_number(obj, 'roll_deg', -MAX_ROLL_DEG, MAX_ROLL_DEG)
        pitch = _parse_finite_number(obj, 'pitch_deg', -MAX_PITCH_DEG, MAX_PITCH_DEG)
        return action, roll, pitch, client, seq, None, None

    if action == 'device_command':
        if set(obj) != base | {'command'}:
            raise ValueError('Unexpected device_command fields')
        cmd = obj['command']
        if type(cmd) is not int or cmd not in VALID_DEVICE_COMMANDS:
            raise ValueError('command must be 1 (calibrate), 2 (enable), 3 (disable), or 4 (manual)')
        return action, float(cmd), 0.0, client, seq, None, {'command': cmd}

    raise ValueError('Expected move, proportional_rate, stop, center, down_view, fpv_angle, attitude, or device_command')


def parse_command(body):
    """Backward-compatible 6-tuple parser."""
    return parse_command_request(body)[:6]


class ControlCore:
    """Serialize sequenced commands and expire gestures after input loss."""
    def __init__(self, publish_rate, publish_center, clock=time.monotonic,
                 publish_down_view=None, begin_down_fence=None,
                 publish_fpv_angle=None, publish_attitude=None,
                 publish_proportional_rate=None, send_device_command=None):
        self._publish_rate = publish_rate
        self._publish_center = publish_center
        self._publish_down_view = publish_down_view
        self._begin_down_fence = begin_down_fence
        self._publish_fpv_angle = publish_fpv_angle
        self._publish_attitude = publish_attitude
        self._publish_proportional_rate = publish_proportional_rate
        self._send_device_command = send_device_command
        self._clock = clock
        self._lock = threading.Lock()
        self._deadline = None
        self._active = None  # (client_id, gesture_id)
        self._active_rate_kind = None  # 'move' or 'proportional_rate'
        self._last_sequence = OrderedDict()
        self._retired = OrderedDict()
        self._pending_down = None
        self.last_command = 'Waiting for a command'

    def _remember(self, mapping, key, value):
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > 10000:
            mapping.popitem(last=False)

    def _retire_active(self):
        if self._active is not None:
            self._remember(self._retired, self._active, True)
        self._active = None
        self._active_rate_kind = None
        self._deadline = None

    def _publish_zero_rate(self):
        if self._active_rate_kind == 'proportional_rate' and self._publish_proportional_rate is not None:
            self._publish_proportional_rate(0.0, 0.0)
        else:
            self._publish_rate(0.0, 0.0)

    def _expire_if_needed(self):
        if self._deadline is not None and self._clock() >= self._deadline:
            self._publish_zero_rate()
            self._retire_active()
            self.last_command = 'Control input expired; zero rate sent'
            return True
        return False

    def _run_fenced(self, fence_id, fence_waiter, on_confirmed, timeout_msg, cancel_msg, done_msg):
        """Wait for manual-hold UART ack outside mutex so STOP stays non-blocking."""
        try:
            confirmed = fence_waiter()
        except Exception:
            with self._lock:
                if self._pending_down is fence_id:
                    self._pending_down = None
            raise
        with self._lock:
            if self._pending_down is not fence_id:
                raise ValueError(cancel_msg)
            self._pending_down = None
            if not confirmed:
                self.last_command = timeout_msg
                raise TimeoutError(self.last_command)
            on_confirmed()
            self.last_command = done_msg
            return self.last_command

    def command(self, action, roll, pitch, client_id, seq, gesture_id, extra=None):
        fence_waiter = None
        fence_id = None
        fence_args = None
        device_cmd = None
        with self._lock:
            self._expire_if_needed()
            previous = self._last_sequence.get(client_id, 0)
            if action == 'stop':
                # An explicit STOP always wins. A release for an old gesture must
                # not cancel a newer one-shot target or another active gesture.
                key = (client_id, gesture_id) if gesture_id is not None else None
                did_stop = key is None or self._active == key
                if did_stop:
                    self._pending_down = None
                    self._publish_zero_rate()
                    self._retire_active()
                    self.last_command = 'Stop command sent'
                if key is not None:
                    self._remember(self._retired, key, True)
                self._remember(self._last_sequence, client_id, max(seq, previous))
                return 'Stop command sent' if did_stop else 'Gesture ended'
            if seq <= previous:
                raise ValueError('Out-of-order command ignored')
            if self._pending_down is not None:
                raise ValueError('Waiting for stop confirmation; try again')
            self._remember(self._last_sequence, client_id, seq)

            if action in ('move', 'proportional_rate'):
                if action == 'proportional_rate' and self._publish_proportional_rate is None:
                    raise ValueError('Proportional rate is unavailable')
                key = (client_id, gesture_id)
                if key in self._retired:
                    raise ValueError('This control gesture has ended; release and press again')
                if self._active is not None and self._active != key:
                    raise ValueError('Another control gesture is active')
                if action == 'proportional_rate':
                    self._publish_proportional_rate(roll, pitch)
                    self.last_command = f'Proportional rate sent: roll {roll:g}, pitch {pitch:g} deg/s'
                else:
                    self._publish_rate(roll, pitch)
                    self.last_command = f'Rate sent: roll {roll:g}, pitch {pitch:g} deg/s'
                self._active = key
                self._active_rate_kind = action
                self._deadline = self._clock() + WATCHDOG_SECONDS
                return self.last_command

            if self._active is not None and self._active[0] != client_id:
                raise ValueError('Another control gesture is active')

            if action == 'center':
                self._retire_active()
                self._publish_center()
                self.last_command = 'Center request sent once'
                return self.last_command

            if action == 'down_view':
                if self._publish_down_view is None or self._begin_down_fence is None:
                    raise ValueError('Downward view is unavailable')
                fence_id = object()
                fence_waiter = self._begin_down_fence()
                self._retire_active()
                self._pending_down = fence_id
                self.last_command = 'Waiting for stop confirmation'
                fence_args = (
                    self._publish_down_view,
                    'Stop confirmation timed out; downward view was not sent',
                    'Downward view canceled by STOP',
                    'Downward view request sent once',
                )
            elif action == 'fpv_angle':
                if self._publish_fpv_angle is None:
                    raise ValueError('FPV angle control is unavailable')
                sens = int((extra or {}).get('fpv_sensitivity', 0))
                send_fn = lambda: self._publish_fpv_angle(roll, pitch, sens)
                done_msg = f'FPV angle sent: roll {roll:g}°, pitch {pitch:g}° (sens {sens})'
                if self._begin_down_fence is not None:
                    fence_id = object()
                    fence_waiter = self._begin_down_fence()
                    self._retire_active()
                    self._pending_down = fence_id
                    self.last_command = 'Waiting for stop confirmation'
                    fence_args = (
                        send_fn,
                        'Stop confirmation timed out; FPV angle was not sent',
                        'FPV angle canceled by STOP',
                        done_msg,
                    )
                else:
                    self._retire_active()
                    send_fn()
                    self.last_command = done_msg
                    return self.last_command
            elif action == 'attitude':
                if self._publish_attitude is None:
                    raise ValueError('Attitude angle control is unavailable')
                send_fn = lambda: self._publish_attitude(roll, pitch)
                done_msg = f'Attitude angle sent: roll {roll:g}°, pitch {pitch:g}°'
                if self._begin_down_fence is not None:
                    fence_id = object()
                    fence_waiter = self._begin_down_fence()
                    self._retire_active()
                    self._pending_down = fence_id
                    self.last_command = 'Waiting for stop confirmation'
                    fence_args = (
                        send_fn,
                        'Stop confirmation timed out; attitude angle was not sent',
                        'Attitude angle canceled by STOP',
                        done_msg,
                    )
                else:
                    self._retire_active()
                    send_fn()
                    self.last_command = done_msg
                    return self.last_command
            elif action == 'device_command':
                if self._send_device_command is None:
                    raise ValueError('Device command service is unavailable')
                device_cmd = int((extra or {}).get('command', int(roll)))
                if self._active is not None or device_cmd in (1, 3, 4):
                    self._publish_zero_rate()
                    self._retire_active()
                self.last_command = f'Sending device command {device_cmd}'
            else:
                raise ValueError('Unknown command')

        if fence_waiter is not None:
            return self._run_fenced(fence_id, fence_waiter, *fence_args)

        result = self._send_device_command(device_cmd)
        with self._lock:
            self.last_command = result
        return result

    def watchdog(self):
        with self._lock:
            return self._expire_if_needed()


def availability_error(action, status, roll=0.0, pitch=0.0, extra=None):
    """Give a useful refusal before publishing; the driver remains authoritative."""
    if action == 'stop':
        return None
    if not status['control_enabled']:
        return 'Control is disabled in configuration; enable it and restart the app'
    if action in ('move', 'proportional_rate') and max(abs(roll), abs(pitch)) > status['max_rate_deg_s']:
        return f"Rate exceeds the configured limit ({status['max_rate_deg_s']:g} deg/s)"
    if action in ('fpv_angle', 'attitude'):
        roll_limit = float(status.get('roll_limit_deg', MAX_ROLL_DEG))
        pitch_limit = float(status['pitch_limit_deg'])
        if abs(roll) > roll_limit:
            return f'Roll angle exceeds the configured limit ({roll_limit:g} deg)'
        if abs(pitch) > pitch_limit:
            return f'Pitch angle exceeds the configured limit ({pitch_limit:g} deg)'
    if not status['connected']:
        return 'No fresh gimbal feedback; check serial connection'
    state = status['state'] or {}
    if state.get('hardware_error'):
        return 'The gimbal reports a hardware error'

    gimbal_state = state.get('gimbal_state')
    if action == 'device_command':
        cmd = int((extra or {}).get('command', int(roll)))
        # Allow ENABLE_MOTORS (2) when motors are stopped (gimbal_state == 2) so users can recover.
        blocked_states = (1, 3) if cmd == 2 else (1, 2, 3)
        if gimbal_state in blocked_states:
            return 'The gimbal is initializing, stopped, or protected'
        if cmd == 1 and not state.get('temperature_ready'):
            return 'Temperature is not ready for gyro calibration'
        if not status['hold_service_ready']:
            return 'Waiting for the driver device command service'
        return None

    if gimbal_state in (1, 2, 3):
        return 'The gimbal is initializing, stopped, or protected'
    if action == 'down_view' and status['pitch_limit_deg'] < 90.0:
        return 'Downward view requires a pitch limit of at least 90 degrees; check the app configuration'
    if action in ('down_view', 'fpv_angle', 'attitude') and not status['hold_service_ready']:
        return 'Waiting for the driver hold service'
    if action == 'proportional_rate':
        if not status.get('enable_auxiliary_interfaces'):
            return 'Proportional rate requires control.enable_auxiliary_interfaces=true in configuration'
        if status.get('proportional_rate_subscribers', 0) < 1:
            return 'The driver proportional rate subscriber is unavailable'
        return None
    if action == 'attitude':
        if status.get('attitude_subscribers', 0) < 1:
            return 'The driver attitude subscriber is unavailable'
        return None
    subscriber_key = 'center_subscribers' if action in ('center', 'down_view', 'fpv_angle') else 'rate_subscribers'
    if status[subscriber_key] < 1:
        return 'The driver command subscriber is unavailable'
    return None
