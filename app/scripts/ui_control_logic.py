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
WATCHDOG_SECONDS = 0.35


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


def parse_command(body):
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
        return action, 0.0, 0.0, client, seq, gesture
    if action in ('center', 'down_view'):
        if set(obj) != base:
            raise ValueError('Unexpected command fields')
        return action, 0.0, 0.0, client, seq, None
    if action != 'move' or not gesture or set(obj) != base | {'gesture_id', 'roll_deg_s', 'pitch_deg_s'}:
        raise ValueError('Expected move, stop, center, or down_view')
    values = []
    for name in ('roll_deg_s', 'pitch_deg_s'):
        raw = obj[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f'{name} must be a finite number')
        try:
            value = float(raw)
        except (OverflowError, ValueError):
            raise ValueError(f'{name} must be a finite number') from None
        if not math.isfinite(value) or abs(value) > MAX_RATE_DEG_S:
            raise ValueError(f'{name} must be between -{MAX_RATE_DEG_S:g} and {MAX_RATE_DEG_S:g}')
        values.append(value)
    if values == [0.0, 0.0]:
        raise ValueError('Use stop for zero rate')
    return action, *values, client, seq, gesture


class ControlCore:
    """Serialize sequenced commands and expire gestures after input loss."""
    def __init__(self, publish_rate, publish_center, clock=time.monotonic,
                 publish_down_view=None, begin_down_fence=None):
        self._publish_rate = publish_rate
        self._publish_center = publish_center
        self._publish_down_view = publish_down_view
        self._begin_down_fence = begin_down_fence
        self._clock = clock
        self._lock = threading.Lock()
        self._deadline = None
        self._active = None  # (client_id, gesture_id)
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
        self._deadline = None

    def _expire_if_needed(self):
        if self._deadline is not None and self._clock() >= self._deadline:
            self._publish_rate(0.0, 0.0)
            self._retire_active()
            self.last_command = 'Control input expired; zero rate sent'
            return True
        return False

    def command(self, action, roll, pitch, client_id, seq, gesture_id):
        fence_waiter = None
        fence_id = None
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
                    self._publish_rate(0.0, 0.0)
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
            if action == 'move':
                key = (client_id, gesture_id)
                if key in self._retired:
                    raise ValueError('This control gesture has ended; release and press again')
                if self._active is not None and self._active != key:
                    raise ValueError('Another control gesture is active')
                self._publish_rate(roll, pitch)
                self._active = key
                self._deadline = self._clock() + WATCHDOG_SECONDS
                self.last_command = f'Rate sent: roll {roll:g}, pitch {pitch:g} deg/s'
            elif action in ('center', 'down_view'):
                if self._active is not None and self._active[0] != client_id:
                    raise ValueError('Another control gesture is active')
                if action == 'down_view':
                    if self._publish_down_view is None or self._begin_down_fence is None:
                        raise ValueError('Downward view is unavailable')
                    fence_id = object()
                    fence_waiter = self._begin_down_fence()
                    self._retire_active()
                    self._pending_down = fence_id
                    self.last_command = 'Waiting for stop confirmation'
                else:
                    self._retire_active()
                    self._publish_center()
                    self.last_command = 'Center request sent once'
            else:
                raise ValueError('Unknown command')
            if fence_waiter is None:
                return self.last_command

        # Wait outside the mutex: explicit STOP must remain immediately usable,
        # and the ROS executor must be able to receive the matching echo.
        try:
            confirmed = fence_waiter()
        except Exception:
            with self._lock:
                if self._pending_down is fence_id:
                    self._pending_down = None
            raise
        with self._lock:
            if self._pending_down is not fence_id:
                raise ValueError('Downward view canceled by STOP')
            self._pending_down = None
            if not confirmed:
                self.last_command = 'Stop confirmation timed out; downward view was not sent'
                raise TimeoutError(self.last_command)
            self._publish_down_view()
            self.last_command = 'Downward view request sent once'
            return self.last_command

    def watchdog(self):
        with self._lock:
            return self._expire_if_needed()


def availability_error(action, status, roll=0.0, pitch=0.0):
    """Give a useful refusal before publishing; the driver remains authoritative."""
    if action == 'stop':
        return None
    if not status['control_enabled']:
        return 'Control is disabled in configuration; enable it and restart the app'
    if action == 'move' and not status['teleop_enabled']:
        return 'Teleoperation is disabled in configuration; enable it and restart the app'
    if action == 'move' and max(abs(roll), abs(pitch)) > status['max_rate_deg_s']:
        return f"Rate exceeds the configured limit ({status['max_rate_deg_s']:g} deg/s)"
    if not status['connected']:
        return 'No fresh gimbal feedback; check serial connection'
    state = status['state'] or {}
    if state.get('hardware_error'):
        return 'The gimbal reports a hardware error'
    if state.get('gimbal_state') in (1, 2, 3):
        return 'The gimbal is initializing, stopped, or protected'
    if action == 'down_view' and status['pitch_limit_deg'] < 90.0:
        return 'Downward view requires a pitch limit of at least 90 degrees; check the app configuration'
    if action == 'down_view' and status['rate_echo_publishers'] < 1:
        return 'Waiting for the driver stop-confirmation topic'
    subscriber_key = 'center_subscribers' if action in ('center', 'down_view') else 'rate_subscribers'
    if status[subscriber_key] < 1:
        return 'The driver command subscriber is unavailable'
    return None
