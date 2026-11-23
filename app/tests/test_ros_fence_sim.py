#!/usr/bin/env python3
"""Exercise the down-view stop fence with ROS 2 and a simulated driver.

No real gimbal is used. The C20D topics and hold service are remapped to a fresh random
test namespace, and the caller must select a nonzero ROS domain separately.
"""
import os
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

try:
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from c20d_driver.msg import AngularRateCommand, GimbalState, RelativeAngleCommand
    from c20d_driver.srv import DeviceCommand
except ModuleNotFoundError as exc:
    ROS_ERROR = str(exc)
    Node = object
else:
    ROS_ERROR = ''

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if not ROS_ERROR:
    from ui_control_server import ControlNode


def wait_until(predicate, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class FakeDriver(Node):
    def __init__(self, base):
        super().__init__('c20d_fence_fake_driver')
        self.hold_accepted = True
        self.requests = []
        self.lock = threading.Lock()
        self.rates = []
        self.targets = []
        self.state_pub = self.create_publisher(GimbalState, f'{base}/state', 10)
        self.hold_service = self.create_service(DeviceCommand, f'{base}/hold', self.on_hold)
        self.rate_sub = self.create_subscription(AngularRateCommand, f'{base}/command/rate', self.on_rate, 10)
        self.angle_sub = self.create_subscription(RelativeAngleCommand, f'{base}/command/angle', self.on_angle, 10)
        self.timer = self.create_timer(0.05, self.send_state)

    def send_state(self):
        state = GimbalState()
        state.gimbal_state = 4
        state.hardware_error = 0
        self.state_pub.publish(state)

    def on_rate(self, message):
        with self.lock:
            self.rates.append(message)

    def on_hold(self, request, response):
        with self.lock:
            self.requests.append(request.command)
            response.accepted = self.hold_accepted and request.command == DeviceCommand.Request.MANUAL
            response.message = 'Hold acknowledged' if response.accepted else 'Hold rejected'
        return response

    def on_angle(self, message):
        with self.lock:
            self.targets.append(message)


@unittest.skipIf(ROS_ERROR, f'ROS 2 test dependencies not available: {ROS_ERROR}')
class RosFenceSimulation(unittest.TestCase):
    def test_down_view_requires_accepted_manual_hold(self):
        domain = os.environ.get('ROS_DOMAIN_ID', '')
        if not domain.isdigit() or int(domain) == 0:
            self.skipTest('Set ROS_DOMAIN_ID to a separate, nonzero test domain')
        base = '/c20d_fence_sim_' + uuid.uuid4().hex
        remaps = {
            '/c20d_driver/state': f'{base}/state',
            '/c20d_driver/command/angular_rate': f'{base}/command/rate',
            '/c20d_driver/device_command': f'{base}/hold',
            '/c20d_driver/command/relative_angle': f'{base}/command/angle',
        }
        args = ['--ros-args']
        for source, target in remaps.items():
            args += ['-r', f'{source}:={target}']
        rclpy.init(args=args)
        control = ControlNode((True, True, 80.0, 120.0))
        fake = FakeDriver(base)
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(control)
        executor.add_node(fake)
        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        try:
            ready = lambda: (control.status()['connected']
                             and control.status()['rate_subscribers'] >= 1
                             and control.status()['hold_service_ready']
                             and control.status()['center_subscribers'] >= 1)
            self.assertTrue(wait_until(ready), 'simulated driver discovery timed out')
            client = str(uuid.uuid4())
            result = control.core.command('down_view', 0.0, 0.0, client, 1, None)
            self.assertIn('sent once', result)
            self.assertTrue(wait_until(lambda: len(fake.targets) == 1), 'no down-view target')
            with fake.lock:
                self.assertEqual(fake.requests, [DeviceCommand.Request.MANUAL])
                self.assertFalse(fake.rates)
                target = fake.targets[0]
                self.assertEqual((target.roll_deg, target.pitch_deg), (0.0, -90.0))
                self.assertFalse(target.center_roll)
                self.assertFalse(target.center_pitch)
                fake.hold_accepted = False
            with self.assertRaisesRegex(ValueError, 'Hold rejected'):
                control.core.command('down_view', 0.0, 0.0, client, 2, None)
            with fake.lock:
                self.assertEqual(len(fake.targets), 1, 'target published after a rejected hold')
                self.assertEqual(fake.requests, [4, 4])
        finally:
            executor.shutdown(timeout_sec=3.0)
            thread.join(timeout=3.0)
            fake.destroy_node()
            control.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
