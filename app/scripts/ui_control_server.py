#!/usr/bin/env python3
"""Local, authenticated HTTP bridge for the C20D embedded UI."""
import json
import math
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import yaml
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from c20d_driver.msg import AngularRateCommand, GimbalState, RelativeAngleCommand

from ui_control_logic import (MAX_RATE_DEG_S, ControlCore, allowed_origin,
                              availability_error, bearer_token, parse_command)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_AUTH_OPENER = build_opener(ProxyHandler({}), NoRedirect())


def validate_token(token):
    # The AivudaOS gateway's /aivuda_os/api/auth/me accepts its session token as a query
    # parameter. This process never logs the request URL, token or headers.
    url = 'http://127.0.0.1:8000/aivuda_os/api/auth/me?' + urlencode({'token': token})
    try:
        with _AUTH_OPENER.open(Request(url, headers={'Accept': 'application/json'}), timeout=2) as response:
            data = json.loads(response.read(8192))
            return response.status == 200 and isinstance(data, dict) and bool(data.get('username'))
    except HTTPError as exc:
        if exc.code in (401, 403):
            return False
        raise RuntimeError('Authentication service unavailable') from None
    except (URLError, OSError, ValueError, TypeError):
        raise RuntimeError('Authentication service unavailable') from None


def load_settings(path):
    config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    control = config['control']
    enabled, teleop, rate, pitch_limit = (control['enabled'], control['teleop_enabled'],
                                           control['max_rate'], control['pitch_limit'])
    if type(enabled) is not bool or type(teleop) is not bool:
        raise ValueError('Control settings must be boolean')
    if isinstance(rate, bool) or not isinstance(rate, (float, int)) or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Invalid configured maximum rate')
    if (isinstance(pitch_limit, bool) or not isinstance(pitch_limit, (float, int))
            or not math.isfinite(pitch_limit) or not 0 < pitch_limit <= 120):
        raise ValueError('Invalid configured pitch limit')
    return enabled, teleop, min(float(rate), MAX_RATE_DEG_S), float(pitch_limit)


class ControlNode(Node):
    def __init__(self, settings):
        super().__init__('c20d_web_control')
        self.control_enabled, self.teleop_enabled, self.max_rate_deg_s, self.pitch_limit_deg = settings
        self.rate_pub = self.create_publisher(AngularRateCommand, '/c20d_driver/command/angular_rate', 1)
        self.center_pub = self.create_publisher(RelativeAngleCommand, '/c20d_driver/command/relative_angle', 1)
        self.feedback = None
        self.feedback_received = None
        self.feedback_lock = threading.Lock()
        self._fence_lock = threading.Lock()
        self._fences = {}
        self.sub = self.create_subscription(GimbalState, '/c20d_driver/state', self.on_feedback,
                                            qos_profile_sensor_data)
        # Volatile/reliable subscription is compatible with the driver's reliable,
        # transient-local echo publisher and receives new accepted commands.
        self.echo_sub = self.create_subscription(AngularRateCommand, '/c20d_driver/command_echo/angular_rate',
                                                 self.on_rate_echo, 1)
        self.core = ControlCore(self.send_rate, self.send_center, publish_down_view=self.send_down_view,
                                begin_down_fence=self.begin_down_fence)
        self.watchdog_timer = self.create_timer(0.05, self.core.watchdog)

    def on_feedback(self, msg):
        # Use monotonic arrival age; the ROS clock may change or be unsynchronized.
        def safe_array(values):
            return [float(v) if math.isfinite(float(v)) else None for v in values]
        data = {'relative_angle_rpy': safe_array(msg.relative_angle_rpy),
                'camera_attitude_rpy': safe_array(msg.camera_attitude_rpy),
                'hardware_error': int(msg.hardware_error), 'gimbal_state': int(msg.gimbal_state),
                'command_code': int(msg.command_code), 'command_status': int(msg.command_status)}
        with self.feedback_lock:
            self.feedback = data
            self.feedback_received = time.monotonic()

    def send_rate(self, roll, pitch):
        msg = AngularRateCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg_s, msg.pitch_deg_s = roll, pitch
        self.rate_pub.publish(msg)

    def send_center(self):
        msg = RelativeAngleCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.center_roll = True
        msg.center_pitch = True
        self.center_pub.publish(msg)

    def on_rate_echo(self, msg):
        if msg.roll_deg_s != 0.0 or msg.pitch_deg_s != 0.0:
            return
        stamp = msg.header.stamp
        key = (stamp.sec, stamp.nanosec, msg.header.frame_id)
        with self._fence_lock:
            event = self._fences.get(key)
            if event is not None:
                event.set()

    def begin_down_fence(self):
        # The driver echoes this exact header only after accepting the zero-rate
        # command. The nonce makes a stale transient echo impossible to match.
        msg = AngularRateCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'c20d-panel-stop-' + uuid.uuid4().hex
        msg.roll_deg_s = 0.0
        msg.pitch_deg_s = 0.0
        stamp = msg.header.stamp
        key = (stamp.sec, stamp.nanosec, msg.header.frame_id)
        event = threading.Event()
        with self._fence_lock:
            self._fences[key] = event
        try:
            self.rate_pub.publish(msg)
        except Exception:
            with self._fence_lock:
                self._fences.pop(key, None)
            raise

        def wait_for_echo():
            try:
                return event.wait(timeout=1.0)
            finally:
                with self._fence_lock:
                    self._fences.pop(key, None)
        return wait_for_echo

    def send_down_view(self):
        error = availability_error('down_view', self.status())
        if error:
            raise ValueError(error)
        # FPV relative angle: mechanical zero looks forward, negative pitch looks down.
        # This controls both axes; keep roll at zero and do not toggle center bits.
        msg = RelativeAngleCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg, msg.pitch_deg = 0.0, -90.0
        self.center_pub.publish(msg)

    def status(self):
        with self.feedback_lock:
            age = None if self.feedback_received is None else max(0, time.monotonic() - self.feedback_received)
            state = dict(self.feedback) if self.feedback is not None else None
        return {'ok': True, 'connected': age is not None and age < 0.5,
                'feedback_age_sec': round(age, 3) if age is not None else None,
                'state': state, 'last_command': self.core.last_command,
                'control_enabled': self.control_enabled, 'teleop_enabled': self.teleop_enabled,
                'max_rate_deg_s': self.max_rate_deg_s, 'pitch_limit_deg': self.pitch_limit_deg,
                'rate_subscribers': self.rate_pub.get_subscription_count(),
                'rate_echo_publishers': self.echo_sub.get_publisher_count(),
                'center_subscribers': self.center_pub.get_subscription_count()}


class ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, node, validator=validate_token):
        super().__init__(address, Handler)
        self.node = node
        self.validator = validator


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # In particular, never log bearer tokens or query parameters.
        return

    def respond(self, code, body):
        encoded = json.dumps(body, allow_nan=False, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def authorize(self):
        if not allowed_origin(self.headers, self.client_address[0]):
            self.respond(403, {'ok': False, 'error': 'Cross-origin request denied'})
            return False
        token = bearer_token(self.headers)
        if token is None:
            self.respond(401, {'ok': False, 'error': 'Sign in to AivudaOS first'})
            return False
        try:
            valid = self.server.validator(token)
        except RuntimeError:
            self.respond(503, {'ok': False, 'error': 'AivudaOS authentication is unavailable'})
            return False
        if not valid:
            self.respond(401, {'ok': False, 'error': 'AivudaOS session expired; sign in again'})
            return False
        return True

    def do_GET(self):
        if not self.authorize():
            return
        if self.path != '/status':
            self.respond(404, {'ok': False, 'error': 'Not found'})
            return
        self.respond(200, self.server.node.status())

    def do_POST(self):
        if not self.authorize():
            self.close_connection = True
            return
        if self.path != '/command':
            self.close_connection = True
            self.respond(404, {'ok': False, 'error': 'Not found'})
            return
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Content-Type must be application/json')
            raw_length = self.headers.get('Content-Length', '')
            if not raw_length.isascii() or not raw_length.isdigit() or not 0 < int(raw_length) <= 2048:
                raise ValueError('Invalid request size')
            action, roll, pitch, client_id, seq, gesture_id = parse_command(self.rfile.read(int(raw_length)))
        except ValueError as exc:
            self.close_connection = True
            self.respond(400, {'ok': False, 'error': str(exc)})
            return
        error = availability_error(action, self.server.node.status(), roll, pitch)
        if error:
            self.respond(409, {'ok': False, 'error': error})
            return
        try:
            message = self.server.node.core.command(action, roll, pitch, client_id, seq, gesture_id)
        except TimeoutError as exc:
            self.respond(504, {'ok': False, 'error': str(exc)})
            return
        except ValueError as exc:
            self.respond(409, {'ok': False, 'error': str(exc)})
            return
        except Exception:
            self.respond(503, {'ok': False, 'error': 'ROS command could not be published'})
            return
        self.respond(200, {'ok': True, 'message': message + '; check device feedback for execution'})


def main():
    root = Path(__file__).resolve().parent.parent
    config = os.environ.get('AIVUDA_APP_CONFIG_PATH') or str(root / 'config/default_config.yaml')
    settings = load_settings(config)
    rclpy.init()
    node = ControlNode(settings)
    server = ControlHTTPServer(('127.0.0.1', 19291), node)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    node.get_logger().info('C20D web control ready on local port 19291')
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        # Do not issue a nonzero command during shutdown; the driver watchdog is
        # the independent final safeguard if this process is interrupted.
        server.shutdown()
        server.server_close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
