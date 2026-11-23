#!/usr/bin/env python3
"""Local, authenticated HTTP bridge for the C20D embedded UI."""
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from c20d_driver.msg import (AngularRateCommand, AttitudeCommand, GimbalState,
                             ProportionalRateCommand, RelativeAngleCommand)
from c20d_driver.srv import DeviceCommand

from render_config import load_app_config
from ui_control_logic import (MAX_RATE_DEG_S, ControlCore, allowed_origin,
                              availability_error, bearer_token, parse_command_request)


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
    """Load validated app config through the shared render_config validator."""
    cfg = load_app_config(path)
    cfg['max_rate'] = min(float(cfg['max_rate']), MAX_RATE_DEG_S)
    return cfg


def _normalize_settings(settings):
    if isinstance(settings, dict):
        return settings
    enabled, teleop, max_rate, pitch_limit = settings
    return {
        'enabled': bool(enabled),
        'teleop_enabled': bool(teleop),
        'max_rate': min(float(max_rate), MAX_RATE_DEG_S),
        'roll_limit': 60.0,
        'pitch_limit': float(pitch_limit),
        'command_timeout': 0.5,
        'publish_joint_states': False,
        'enable_auxiliary_interfaces': False,
    }


class ControlNode(Node):
    def __init__(self, settings):
        super().__init__('c20d_web_control')
        cfg = _normalize_settings(settings)
        self.control_enabled = cfg['enabled']
        self.teleop_enabled = cfg['teleop_enabled']
        self.max_rate_deg_s = cfg['max_rate']
        self.roll_limit_deg = cfg['roll_limit']
        self.pitch_limit_deg = cfg['pitch_limit']
        self.command_timeout_sec = cfg['command_timeout']
        self.publish_joint_states = cfg['publish_joint_states']
        self.enable_auxiliary_interfaces = cfg['enable_auxiliary_interfaces']

        self.rate_pub = self.create_publisher(AngularRateCommand, '/c20d_driver/command/angular_rate', 1)
        self.center_pub = self.create_publisher(RelativeAngleCommand, '/c20d_driver/command/relative_angle', 1)
        self.attitude_pub = self.create_publisher(AttitudeCommand, '/c20d_driver/command/attitude', 1)
        self.proportional_pub = self.create_publisher(
            ProportionalRateCommand, '/c20d_driver/command/proportional_rate', 1)

        self.feedback = None
        self.feedback_received = None
        self.feedback_lock = threading.Lock()
        self.sub = self.create_subscription(GimbalState, '/c20d_driver/state', self.on_feedback,
                                            qos_profile_sensor_data)
        self.device = self.create_client(DeviceCommand, '/c20d_driver/device_command')
        self.core = ControlCore(
            self.send_rate,
            self.send_center,
            publish_down_view=self.send_down_view,
            begin_down_fence=self.begin_down_fence,
            publish_fpv_angle=self.send_fpv_angle,
            publish_attitude=self.send_attitude,
            publish_proportional_rate=self.send_proportional_rate,
            send_device_command=self.send_device_command,
        )
        self.watchdog_timer = self.create_timer(0.05, self.core.watchdog)

    def on_feedback(self, msg):
        # Use monotonic arrival age; the ROS clock may change or be unsynchronized.
        def safe_array(values):
            return [float(v) if math.isfinite(float(v)) else None for v in values]

        data = {
            'relative_angle_rpy': safe_array(msg.relative_angle_rpy),
            'camera_attitude_rpy': safe_array(msg.camera_attitude_rpy),
            'camera_rate_rpy': safe_array(msg.camera_rate_rpy),
            'hardware_error': int(msg.hardware_error),
            'gimbal_state': int(msg.gimbal_state),
            'upright': bool(msg.upright),
            'temperature_ready': bool(msg.temperature_ready),
            'firmware_version_raw': int(msg.firmware_version_raw),
            'command_code': int(msg.command_code),
            'command_status': int(msg.command_status),
        }
        with self.feedback_lock:
            self.feedback = data
            self.feedback_received = time.monotonic()

    def send_rate(self, roll, pitch):
        msg = AngularRateCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg_s, msg.pitch_deg_s = float(roll), float(pitch)
        self.rate_pub.publish(msg)

    def send_proportional_rate(self, roll, pitch):
        msg = ProportionalRateCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg_s, msg.pitch_deg_s = float(roll), float(pitch)
        self.proportional_pub.publish(msg)

    def send_center(self):
        msg = RelativeAngleCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.center_roll = True
        msg.center_pitch = True
        self.center_pub.publish(msg)

    def send_fpv_angle(self, roll, pitch, sensitivity=0):
        msg = RelativeAngleCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg, msg.pitch_deg = float(roll), float(pitch)
        msg.fpv_sensitivity = int(sensitivity)
        self.center_pub.publish(msg)

    def send_attitude(self, roll, pitch):
        msg = AttitudeCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.roll_deg, msg.pitch_deg = float(roll), float(pitch)
        self.attitude_pub.publish(msg)

    def _call_device_service(self, cmd, timeout_sec=1.5):
        request = DeviceCommand.Request()
        request.command = int(cmd)
        future = self.device.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=timeout_sec):
            self.device.remove_pending_request(future)
            return None
        return future.result()

    def begin_down_fence(self):
        request = DeviceCommand.Request()
        request.command = DeviceCommand.Request.MANUAL
        future = self.device.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())

        def wait_for_hold():
            if not done.wait(timeout=1.0):
                self.device.remove_pending_request(future)
                return False
            response = future.result()
            if not response.accepted:
                raise ValueError(response.message)
            return True
        return wait_for_hold

    def send_device_command(self, cmd):
        response = self._call_device_service(cmd, timeout_sec=1.5)
        if response is None:
            raise TimeoutError(f'Device command {cmd} timed out')
        if not response.accepted:
            raise ValueError(response.message)
        return f'Device command {cmd}: {response.message}'

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
        return {
            'ok': True,
            'connected': age is not None and age < 0.5,
            'feedback_age_sec': round(age, 3) if age is not None else None,
            'state': state,
            'last_command': self.core.last_command,
            'control_enabled': self.control_enabled,
            'teleop_enabled': self.teleop_enabled,
            'publish_joint_states': self.publish_joint_states,
            'enable_auxiliary_interfaces': self.enable_auxiliary_interfaces,
            'command_timeout_sec': self.command_timeout_sec,
            'max_rate_deg_s': self.max_rate_deg_s,
            'roll_limit_deg': self.roll_limit_deg,
            'pitch_limit_deg': self.pitch_limit_deg,
            'rate_subscribers': self.rate_pub.get_subscription_count(),
            'center_subscribers': self.center_pub.get_subscription_count(),
            'attitude_subscribers': self.attitude_pub.get_subscription_count(),
            'proportional_rate_subscribers': self.proportional_pub.get_subscription_count(),
            'hold_service_ready': self.device.service_is_ready(),
        }


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
            action, roll, pitch, client_id, seq, gesture_id, extra = parse_command_request(
                self.rfile.read(int(raw_length)))
        except ValueError as exc:
            self.close_connection = True
            self.respond(400, {'ok': False, 'error': str(exc)})
            return
        error = availability_error(action, self.server.node.status(), roll, pitch, extra)
        if error:
            self.respond(409, {'ok': False, 'error': error})
            return
        try:
            message = self.server.node.core.command(action, roll, pitch, client_id, seq, gesture_id, extra)
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
