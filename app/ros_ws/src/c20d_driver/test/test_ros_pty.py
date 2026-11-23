# Copyright 2026 wee733
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Exercise the compiled C++ ROS node through a pseudo-terminal; no hardware required."""

import binascii
import math
import os
import pty
import signal
import struct
import subprocess
import sys
import time

from c20d_driver.msg import AngularRateCommand
from c20d_driver.msg import AttitudeCommand
from c20d_driver.msg import CameraData
from c20d_driver.msg import CarrierData
from c20d_driver.msg import GimbalState
from c20d_driver.msg import ProportionalRateCommand
from c20d_driver.msg import RelativeAngleCommand
from c20d_driver.srv import DeviceCommand
from geometry_msgs.msg import Twist
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState


def test_default_graph():
    """Discover the driver alone before creating any test publishers or subscriptions."""
    domain = 100 + os.getpid() % 100
    rclpy.init(domain_id=domain)
    node = rclpy.create_node('c20d_graph_test')
    master, slave = pty.openpty()
    process = subprocess.Popen([
        os.environ['C20D_TEST_EXECUTABLE'], '--ros-args', '-p',
        'port:=' + os.ttyname(slave)], env=dict(os.environ, ROS_DOMAIN_ID=str(domain)))
    client = node.create_client(DeviceCommand, '/c20d_driver/device_command')
    try:
        assert client.wait_for_service(timeout_sec=8)
        expected = {'/c20d_driver/' + name for name in (
            'state', 'diagnostics', 'command/angular_rate', 'command/relative_angle',
            'command/attitude')}
        end = time.monotonic() + 5
        while True:
            topics = {name for name, _ in node.get_topic_names_and_types()
                      if name.startswith(('/c20d_driver/', '/gimbal_teleop/'))}
            if topics == expected or time.monotonic() >= end:
                break
            rclpy.spin_once(node, timeout_sec=0.05)
        assert topics == expected
    finally:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
        node.destroy_node()
        rclpy.shutdown()
        os.close(slave)
        os.close(master)


def test_ros_serial_roundtrip():
    """Check feedback, command bits, acknowledgements, watchdog and reconnect behavior."""
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    domain = 100 + os.getpid() % 100
    rclpy.init(domain_id=domain)
    node = rclpy.create_node('c20d_pty_test')
    states = []
    joints = []
    node.create_subscription(
        JointState, '/c20d_driver/joint_states', joints.append, qos_profile_sensor_data)
    subscription = node.create_subscription(
        GimbalState, '/c20d_driver/state', states.append, qos_profile_sensor_data)
    publisher = node.create_publisher(AttitudeCommand, '/c20d_driver/command/attitude', 1)
    relative = node.create_publisher(
        RelativeAngleCommand, '/c20d_driver/command/relative_angle', 1)
    rate = node.create_publisher(AngularRateCommand, '/c20d_driver/command/angular_rate', 1)
    proportional = node.create_publisher(
        ProportionalRateCommand, '/c20d_driver/command/proportional_rate', 1)
    carrier = node.create_publisher(CarrierData, '/c20d_driver/carrier', 1)
    camera = node.create_publisher(CameraData, '/c20d_driver/camera', 1)
    teleop = node.create_publisher(Twist, '/gimbal_teleop/cmd', 1)
    client = node.create_client(DeviceCommand, '/c20d_driver/device_command')
    rates = []
    node.create_subscription(
        AngularRateCommand, '/c20d_driver/command/angular_rate', rates.append, 1)
    environment = dict(os.environ, ROS_DOMAIN_ID=str(domain))
    process = subprocess.Popen([
        os.environ['C20D_TEST_EXECUTABLE'], '--ros-args', '-p',
        'port:=' + os.ttyname(slave), '-p', 'command_timeout:=0.2',
        '-p', 'publish_joint_states:=true', '-p', 'enable_auxiliary_interfaces:=true',
        '-p', 'auxiliary_timeout:=0.2', '-p', 'feedback_timeout:=0.3'], env=environment)
    relay = subprocess.Popen([sys.executable, os.environ['C20D_TEST_RELAY']], env=environment)
    wire = bytearray()
    sent = []
    respond = True
    simulate_motion = False
    acknowledged_code = 0

    def pump(seconds):
        nonlocal acknowledged_code
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            assert process.poll() is None, 'C++ node exited unexpectedly'
            assert relay.poll() is None, 'Teleop relay exited unexpectedly'
            try:
                wire.extend(os.read(master, 8192))
            except BlockingIOError:
                pass
            while len(wire) >= 40:
                assert wire[:2] == b'\xa9\x5b'
                packet = bytes(wire[:40])
                del wire[:40]
                assert binascii.crc_hqx(packet[:38], 0) == int.from_bytes(packet[38:], 'big')
                sent.append(packet)
                if packet[2] >> 3:
                    acknowledged_code = packet[2] >> 3
                if respond:
                    reply = bytearray.fromhex(
                        'b5 9a 24 00 19 21 ff ff 00 00 fe ff '
                        '09 00 8c 35 b9 01 52 c9 5f 00 fe ff')
                    if simulate_motion:
                        # Ideal device fixture: positive wire targets raise the camera.
                        # Encoder and gyro pitch feedback are opposite to attitude pitch.
                        pitch = struct.unpack_from('<h', packet, 8)[0]
                        if packet[7] >> 6 == 0:
                            struct.pack_into('<h', reply, 14, pitch)
                            struct.pack_into('<h', reply, 18, -pitch)
                            struct.pack_into('<h', reply, 6, 0)
                        else:
                            struct.pack_into('<h', reply, 6, -pitch)
                    reply[5] = (acknowledged_code << 3) | 1
                    reply.extend(struct.pack('>H', binascii.crc_hqx(reply, 0)))
                    os.write(master, reply)
            rclpy.spin_once(node, timeout_sec=0.002)

    try:
        pump(2.0)
        assert states and publisher.get_subscription_count() == 1
        state = states[-1]
        assert state.firmware_version_raw == 36
        assert state.upright and state.temperature_ready
        assert list(state.camera_rate_raw_pyr) == [-1, 0, -2]
        assert math.isclose(state.relative_angle_rpy[0], 0.95)
        assert math.isclose(state.relative_angle_rpy[1], 139.98)
        assert math.isclose(state.camera_rate_rpy[1], 0.1)
        assert math.isclose(state.camera_attitude_rpy[1], 137.08)
        assert joints and math.isclose(joints[-1].position[0], math.radians(0.95))
        assert math.isclose(joints[-1].position[1], math.radians(139.98))
        assert state.header.frame_id == 'c20d_head_up'
        assert list(state.relative_angle_raw_pyr) == [-13998, 95, -2]
        assert list(state.camera_attitude_raw_rpy) == [9, 13708, 441]
        assert all(packet[2] >> 3 == 0 for packet in sent), 'Startup must only poll'
        assert relative.get_subscription_count() == 1
        assert rate.get_subscription_count() == 2  # Driver and relay observer in this test.
        assert proportional.get_subscription_count() == 1
        assert node.count_subscribers('/c20d_driver/command') == 0
        simulate_motion = True

        command = AttitudeCommand()
        command.roll_deg = 2.0
        command.pitch_deg = -3.0
        command.center_roll = True
        publisher.publish(command)
        pump(0.15)
        assert any(packet[2] >> 3 == 4 for packet in sent)
        assert sent[-1][2] >> 3 == 0, 'Command code must clear after acknowledgement'
        assert sent[-1][4] == 0x18
        assert sent[-1][7] == 0x10
        assert struct.unpack_from('<h', sent[-1], 5)[0] == 200
        assert struct.unpack_from('<h', sent[-1], 8)[0] == -300
        assert math.isclose(states[-1].camera_attitude_rpy[1], -3.0)
        previous = sent[-1][4:13]

        command.roll_deg = float('nan')
        publisher.publish(command)
        pump(0.05)
        assert sent[-1][4:13] == previous
        command.roll_deg = 61.0
        publisher.publish(command)
        pump(0.05)
        assert sent[-1][4:13] == previous

        # Reject stale/future targets without replacing the last accepted command.
        command = AttitudeCommand()
        command.pitch_deg = 4.0
        command.header.stamp.sec = 1
        publisher.publish(command)
        pump(0.05)
        assert sent[-1][4:13] == previous
        command.header.stamp.sec = node.get_clock().now().seconds_nanoseconds()[0] + 10
        publisher.publish(command)
        pump(0.05)
        assert sent[-1][4:13] == previous

        for target in (2.0, -2.0):
            command = RelativeAngleCommand()
            command.roll_deg = 1.0
            command.pitch_deg = target
            command.fpv_sensitivity = -3
            relative.publish(command)
            pump(0.1)
            assert sent[-1][4] == 0x28
            assert sent[-1][7] == 0x20
            assert sent[-1][10] == 0x20, 'FPV applies to all protocol axes'
            assert sent[-1][3] >> 3 == 29  # Signed five-bit -3.
            assert struct.unpack_from('<h', sent[-1], 5)[0] == 100
            assert struct.unpack_from('<h', sent[-1], 8)[0] == round(target * 100)
            assert math.isclose(states[-1].relative_angle_rpy[1], target)
            assert math.isclose(joints[-1].position[1], math.radians(target))
            assert states[-1].relative_angle_raw_pyr[0] == round(-target * 100)
        pump(0.3)
        assert struct.unpack_from('<h', sent[-1], 8)[0] == -200, 'Angles keep holding'
        previous = sent[-1][4:13]
        command.fpv_sensitivity = 16
        relative.publish(command)
        pump(0.05)
        assert sent[-1][4:13] == previous

        for target in (2.0, -2.0):
            command = AttitudeCommand()
            command.pitch_deg = target
            publisher.publish(command)
            pump(0.1)
            assert sent[-1][7] == 0x10
            assert struct.unpack_from('<h', sent[-1], 8)[0] == round(target * 100)
            assert math.isclose(states[-1].camera_attitude_rpy[1], target)

        for target in (5.0, -5.0):
            command = AngularRateCommand()
            command.pitch_deg_s = target
            rate.publish(command)
            pump(0.1)
            assert sent[-1][7] == 0x90
            assert struct.unpack_from('<h', sent[-1], 8)[0] == round(target * 10)
            assert math.isclose(states[-1].camera_rate_rpy[1], target)
            assert states[-1].camera_rate_raw_pyr[0] == round(-target * 10)
            # Invalid speed must not refresh the watchdog.
            command.pitch_deg_s = 1000.0
            rate.publish(command)
            pump(0.25)
            assert struct.unpack_from('<h', sent[-1], 8)[0] == 0

        # A later angle command supersedes a rate command, including its watchdog.
        command = AngularRateCommand()
        command.pitch_deg_s = 5.0
        rate.publish(command)
        pump(0.05)
        angle = RelativeAngleCommand()
        angle.pitch_deg = -3.0
        relative.publish(angle)
        pump(0.3)
        assert sent[-1][7] == 0x20
        assert struct.unpack_from('<h', sent[-1], 8)[0] == -300

        # MANUAL must not acknowledge until the zero-rate frame has crossed the UART.
        # A queued rate message older than the hold must never override the down target.
        delayed = AngularRateCommand()
        delayed.header.stamp = node.get_clock().now().to_msg()
        delayed.pitch_deg_s = 5.0
        respond = False
        request = DeviceCommand.Request()
        request.command = DeviceCommand.Request.MANUAL
        hold = client.call_async(request)
        pump(0.05)
        assert not hold.done() and sent[-1][2] >> 3 == 4
        assert sent[-1][7] >> 6 == 2 and struct.unpack_from('<h', sent[-1], 8)[0] == 0
        respond = True
        pump(0.05)
        assert hold.done() and hold.result().accepted
        angle.pitch_deg = -90.0
        relative.publish(angle)
        pump(0.03)
        rate.publish(delayed)
        pump(0.04)
        assert sent[-1][7] >> 6 == 0 and struct.unpack_from('<h', sent[-1], 8)[0] == -9000

        # Proportional rate is a distinct input and requires camera metadata.
        scaled_rate = ProportionalRateCommand()
        scaled_rate.pitch_deg_s = 3.0
        proportional.publish(scaled_rate)
        pump(0.05)
        assert sent[-1][7] == 0x20

        inertial = CarrierData()
        inertial.valid = True
        inertial.attitude_rpy = [5.0] * 3
        inertial.acceleration_neu = [0.1] * 3
        carrier.publish(inertial)
        pump(0.1)
        assert sent[-1][13] == 0x80
        assert struct.unpack_from('<hhh', sent[-1], 14) == (500, 500, 500)
        pump(0.2)
        assert sent[-1][13] == 0

        optical = CameraData()
        optical.vertical_fov_deg = 60
        optical.zoom = 1.0
        optical.horizontal_offset = 2.0
        optical.vertical_offset = -3.0
        camera.publish(optical)
        pump(0.1)
        assert struct.unpack_from('<ff', sent[-1], 30) == (2.0, -3.0)
        for target in (3.0, -3.0):
            camera.publish(optical)
            pump(0.05)
            scaled_rate.pitch_deg_s = target
            proportional.publish(scaled_rate)
            pump(0.05)
            assert sent[-1][7] == 0x50
            assert struct.unpack_from('<h', sent[-1], 8)[0] == round(target * 10)
        # Refresh command but let camera expire: scaled speed must stop anyway.
        for _ in range(4):
            proportional.publish(scaled_rate)
            pump(0.06)
        assert struct.unpack_from('<h', sent[-1], 8)[0] == 0

        assert client.service_is_ready()
        request = DeviceCommand.Request()
        request.command = 2
        future = client.call_async(request)
        pump(0.15)
        assert future.done() and future.result().accepted
        triggers = [p[2] & 7 for p in sent if p[2] >> 3 == 2]
        future = client.call_async(request)
        pump(0.15)
        assert future.done() and future.result().accepted
        new_triggers = [p[2] & 7 for p in sent if p[2] >> 3 == 2]
        assert triggers[-1] != new_triggers[-1], 'Repeat command must change trigger count'

        # Actual relay -> ROS driver -> UART, without any real hardware.
        assert teleop.get_subscription_count() == 1
        assert node.count_publishers('/gimbal_teleop/input_echo') == 0
        twist = Twist()
        twist.angular.x = -10.0
        twist.angular.y = 10.0
        teleop.publish(twist)
        pump(0.15)
        assert struct.unpack_from('<h', sent[-1], 5)[0] == -100
        assert struct.unpack_from('<h', sent[-1], 8)[0] == 100
        assert rates[-1].roll_deg_s == -10.0
        assert rates[-1].pitch_deg_s == 10.0
        assert rates[-1].header.stamp.sec > 0
        last_rate_count = len(rates)
        twist.angular.y = float('nan')
        teleop.publish(twist)
        pump(0.1)
        assert len(rates) == last_rate_count
        pump(0.3)
        assert struct.unpack_from('<h', sent[-1], 5)[0] == 0
        assert struct.unpack_from('<h', sent[-1], 8)[0] == 0
        assert len(rates) == last_rate_count, 'Relay must not repeat stale input'
        teleop.publish(Twist())
        pump(0.1)
        assert len(rates) == last_rate_count + 1
        assert rates[-1].roll_deg_s == 0.0
        assert rates[-1].pitch_deg_s == 0.0

        # Replay actual pad traffic: release and middle press both have linear.z=1.
        # Neither may center the gimbal or toggle the protocol's center bits.
        before_release = sent[-1]
        release = Twist()
        release.linear.z = 1.0
        for axis, speed in (('y', 15.0), ('y', -15.0), ('x', 15.0)):
            directional = Twist()
            setattr(directional.angular, axis, speed)
            teleop.publish(directional)
            pump(0.1)
            offset = 5 if axis == 'x' else 8
            assert struct.unpack_from('<h', sent[-1], offset)[0] == int(speed * 10)
            teleop.publish(release)
            pump(0.1)
            assert rates[-1].roll_deg_s == 0.0
            assert rates[-1].pitch_deg_s == 0.0
            assert struct.unpack_from('<h', sent[-1], 5)[0] == 0
            assert struct.unpack_from('<h', sent[-1], 8)[0] == 0
            assert (sent[-1][4] ^ before_release[4]) & 8 == 0
            assert (sent[-1][7] ^ before_release[7]) & 8 == 0
        # Holding the middle/stop button also remains a zero velocity request.
        for _ in range(8):
            teleop.publish(release)
            pump(0.05)
        assert rates[-1].pitch_deg_s == 0.0
        invalid = Twist()
        invalid.linear.z = float('nan')
        rate_count = len(rates)
        teleop.publish(invalid)
        pump(0.1)
        assert len(rates) == rate_count

        respond = False
        pump(0.45)
        reconnect_start = len(sent)
        pump(1.0)
        respond = True
        pump(1.2)
        assert len(sent) > reconnect_start
        assert all(p[2] >> 3 == 0 for p in sent[reconnect_start:])
        assert struct.unpack_from('<h', sent[-1], 8)[0] == 0
    finally:
        relay.send_signal(signal.SIGINT)
        relay.wait(timeout=5)
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
        node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()
        os.close(slave)
        os.close(master)
