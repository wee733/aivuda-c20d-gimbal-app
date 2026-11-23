import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ui_control_logic import (ControlCore, allowed_origin, availability_error, bearer_token,
                              parse_command, parse_command_request)

C1 = '00000000-0000-4000-8000-000000000001'
C2 = '00000000-0000-4000-8000-000000000002'
G1 = '10000000-0000-4000-8000-000000000001'
G2 = '10000000-0000-4000-8000-000000000002'


def raw(action='move', seq=1, client=C1, gesture=G1, roll=2, pitch=0):
    obj = {'action': action, 'client_id': client, 'seq': seq}
    if action == 'move':
        obj.update(gesture_id=gesture, roll_deg_s=roll, pitch_deg_s=pitch)
    elif action == 'stop' and gesture is not None:
        obj['gesture_id'] = gesture
    return json.dumps(obj).encode()


class OriginTests(unittest.TestCase):
    def test_same_origin_https_with_custom_port(self):
        self.assertTrue(allowed_origin({'Host': 'jetson.example:8543', 'Origin': 'https://jetson.example:8543',
                                        'X-Forwarded-Proto': 'https', 'Sec-Fetch-Site': 'same-origin'}, '127.0.0.1'))
        self.assertTrue(allowed_origin({'Host': 'jetson.example', 'Sec-Fetch-Site': 'same-origin'}, '::1'))

    def test_denies_cross_site_even_same_hostname(self):
        base = {'Host': 'jetson.example', 'Origin': 'https://jetson.example', 'Sec-Fetch-Site': 'cross-site'}
        self.assertFalse(allowed_origin(base, '127.0.0.1'))
        self.assertFalse(allowed_origin({'Host': 'jetson.example', 'Origin': 'https://other.example'}, '127.0.0.1'))
        self.assertFalse(allowed_origin({'Host': 'jetson.example', 'Origin': 'https://jetson.example',
                                         'X-Forwarded-Proto': 'http'}, '127.0.0.1'))
        self.assertFalse(allowed_origin({'Host': 'jetson.example'}, '127.0.0.1'))
        self.assertFalse(allowed_origin({'Host': '127.0.0.1:19291'}, '192.0.2.1'))

    def test_direct_local_and_bearer(self):
        self.assertTrue(allowed_origin({'Host': '127.0.0.1:19291'}, '127.0.0.1'))
        self.assertFalse(allowed_origin({'Host': 'localhost:19291'}, '127.0.0.1'))
        self.assertEqual(bearer_token({'Authorization': 'Bearer abc'}), 'abc')
        for value in ('', 'Basic abc', 'Bearer ', 'Bearer a b', 'Bearer x\nfoo'):
            self.assertIsNone(bearer_token({'Authorization': value}))


class CommandTests(unittest.TestCase):
    def test_valid_and_invalid_values(self):
        self.assertEqual(parse_command(raw()), ('move', 2.0, 0.0, C1, 1, G1))
        self.assertEqual(parse_command(raw(roll=80))[1], 80.0)
        for data in (raw(roll=float('nan')), raw(roll=float('inf')), raw(roll=80.1),
                     raw(roll=True), raw(seq=-1), raw(client='arbitrary'), raw(gesture='bad'),
                     raw(roll=0,pitch=0), b'[]'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_command(data)

    def test_release_rejects_late_move_and_watchdog(self):
        now = [0.0]; calls = []
        core = ControlCore(lambda r,p: calls.append(('rate',r,p)), lambda: calls.append(('center',)), lambda: now[0])
        core.command(*parse_command(raw(seq=1)))
        core.command(*parse_command(raw('stop',seq=3)))
        with self.assertRaisesRegex(ValueError, 'Out-of-order'):
            core.command(*parse_command(raw(seq=2)))
        with self.assertRaisesRegex(ValueError, 'gesture has ended'):
            core.command(*parse_command(raw(seq=4)))
        core.command(*parse_command(raw(seq=5,gesture=G2)))
        now[0] = 0.36
        self.assertTrue(core.watchdog())
        self.assertFalse(core.watchdog())
        self.assertEqual(calls[-1], ('rate',0.0,0.0))
        with self.assertRaisesRegex(ValueError, 'gesture has ended'):
            core.command(*parse_command(raw(seq=6,gesture=G2)))

    def test_stop_before_late_move_and_multiple_clients(self):
        calls = []
        core = ControlCore(lambda r,p: calls.append(('rate',r,p)), lambda: calls.append(('center',)))
        core.command(*parse_command(raw('stop',seq=2)))
        with self.assertRaises(ValueError):
            core.command(*parse_command(raw(seq=1)))
        core.command(*parse_command(raw(seq=3,gesture=G2)))
        with self.assertRaisesRegex(ValueError, 'Another'):
            core.command(*parse_command(raw(client=C2,seq=1,gesture=G1)))
        core.command(*parse_command(raw('stop',client=C2,seq=2,gesture=None)))
        with self.assertRaisesRegex(ValueError, 'gesture has ended'):
            core.command(*parse_command(raw(seq=4,gesture=G2)))
        self.assertEqual(calls[-1], ('rate',0.0,0.0))

    def test_center_is_one_shot_and_retires_motion(self):
        now = [0]; calls = []
        core = ControlCore(lambda r,p: calls.append(('rate',r,p)), lambda: calls.append(('center',)), lambda: now[0])
        core.command(*parse_command(raw(seq=1)))
        core.command(*parse_command(raw('center',seq=2)))
        now[0] = 1
        self.assertFalse(core.watchdog())
        with self.assertRaises(ValueError):
            core.command(*parse_command(raw(seq=3)))
        self.assertEqual(calls.count(('center',)), 1)

    def test_down_view_is_one_shot_and_old_release_does_not_cancel_it(self):
        calls = []
        core = ControlCore(lambda r,p: calls.append(('rate',r,p)),
                           lambda: calls.append(('center',)),
                           publish_down_view=lambda: calls.append(('down',)),
                           begin_down_fence=lambda: (calls.append(('rate',0.0,0.0)) or (lambda: True)))
        core.command(*parse_command(raw(seq=1)))
        core.command(*parse_command(raw('stop',seq=2)))
        core.command(*parse_command(raw('down_view',seq=3)))
        self.assertEqual(calls[-2:], [('rate',0.0,0.0), ('down',)])
        core.command(*parse_command(raw('stop',seq=4)))
        self.assertEqual(calls[-1], ('down',))
        with self.assertRaisesRegex(ValueError, 'gesture has ended'):
            core.command(*parse_command(raw(seq=5)))
        with self.assertRaisesRegex(ValueError, 'Out-of-order'):
            core.command(*parse_command(raw('down_view',seq=3)))
        self.assertEqual(calls.count(('down',)), 1)
        core.command(*parse_command(raw('stop',seq=6,gesture=None)))
        self.assertEqual(calls[-1], ('rate',0.0,0.0))

    def test_down_view_does_not_publish_when_hold_service_times_out(self):
        calls=[]
        core=ControlCore(lambda r,p: calls.append(('rate',r,p)), lambda: None,
                         publish_down_view=lambda: calls.append(('down',)),
                         begin_down_fence=lambda: (calls.append(('rate',0.0,0.0)) or (lambda: False)))
        with self.assertRaisesRegex(TimeoutError,'not sent'):
            core.command(*parse_command(raw('down_view',seq=1)))
        self.assertEqual(calls,[('rate',0.0,0.0)])

    def test_explicit_stop_cancels_pending_down_without_waiting_for_service(self):
        calls=[]; started=threading.Event(); ack=threading.Event(); errors=[]
        def begin():
            calls.append(('rate',0.0,0.0)); started.set()
            return lambda: ack.wait(timeout=2)
        core=ControlCore(lambda r,p: calls.append(('rate',r,p)),lambda:None,
                         publish_down_view=lambda: calls.append(('down',)),begin_down_fence=begin)
        def down():
            try:
                core.command(*parse_command(raw('down_view',seq=1)))
            except Exception as error:
                errors.append(error)
        worker=threading.Thread(target=down); worker.start(); self.assertTrue(started.wait(1))
        core.command(*parse_command(raw('stop',seq=2,gesture=None)))
        self.assertEqual(calls[-1],('rate',0.0,0.0))
        ack.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors and 'canceled by STOP' in str(errors[0]))
        self.assertNotIn(('down',),calls)

    def test_availability_controls(self):
        s = dict(control_enabled=True, teleop_enabled=True, max_rate_deg_s=8, connected=True,
                 state={'gimbal_state': 0, 'hardware_error': 0}, rate_subscribers=1, center_subscribers=1,
                 pitch_limit_deg=120, hold_service_ready=1)
        self.assertIsNone(availability_error('move',s,5,0))
        self.assertIn('configured limit',availability_error('move',s,9,0))
        s['teleop_enabled']=False
        self.assertIsNone(availability_error('move',s,5,0))
        self.assertIsNone(availability_error('center',s))
        self.assertIsNone(availability_error('down_view',s))
        s['pitch_limit_deg'] = 89.9
        self.assertIn('at least 90 degrees',availability_error('down_view',s))
        s['pitch_limit_deg'] = 90
        self.assertIsNone(availability_error('down_view',s))
        s['hold_service_ready'] = 0
        self.assertIn('hold service',availability_error('down_view',s))
        s['hold_service_ready'] = 1
        s['control_enabled']=False
        self.assertIsNone(availability_error('stop',s))
        self.assertIn('disabled',availability_error('center',s))
        s.update(control_enabled=True, connected=False)
        self.assertIn('feedback',availability_error('center',s))

    def test_new_modes_and_device_commands(self):
        fpv_raw = json.dumps({'action': 'fpv_angle', 'client_id': C1, 'seq': 1,
                              'roll_deg': 12.5, 'pitch_deg': -30, 'fpv_sensitivity': -5}).encode()
        self.assertEqual(parse_command_request(fpv_raw),
                         ('fpv_angle', 12.5, -30.0, C1, 1, None, {'fpv_sensitivity': -5}))
        att_raw = json.dumps({'action': 'attitude', 'client_id': C1, 'seq': 2,
                              'roll_deg': -10, 'pitch_deg': 15}).encode()
        self.assertEqual(parse_command_request(att_raw)[:4], ('attitude', -10.0, 15.0, C1))
        dev_raw = json.dumps({'action': 'device_command', 'client_id': C1, 'seq': 3, 'command': 2}).encode()
        self.assertEqual(parse_command_request(dev_raw)[-1], {'command': 2})
        for bad in (
            {'action': 'fpv_angle', 'client_id': C1, 'seq': 1, 'roll_deg': 61, 'pitch_deg': 0},
            {'action': 'fpv_angle', 'client_id': C1, 'seq': 1, 'roll_deg': 0, 'pitch_deg': 0, 'fpv_sensitivity': 16},
            {'action': 'attitude', 'client_id': C1, 'seq': 1, 'roll_deg': 0, 'pitch_deg': -121},
            {'action': 'device_command', 'client_id': C1, 'seq': 1, 'command': 5},
            {'action': 'device_command', 'client_id': C1, 'seq': 1, 'command': True},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_command_request(json.dumps(bad).encode())

        calls = []
        now = [0.0]
        core = ControlCore(
            lambda r, p: calls.append(('rate', r, p)),
            lambda: calls.append(('center',)),
            clock=lambda: now[0],
            begin_down_fence=lambda: (lambda: True),
            publish_fpv_angle=lambda r, p, s: calls.append(('fpv', r, p, s)),
            publish_attitude=lambda r, p: calls.append(('att', r, p)),
            publish_proportional_rate=lambda r, p: calls.append(('prop', r, p)),
            send_device_command=lambda cmd: calls.append(('dev', cmd)) or f'Device command {cmd}: OK',
        )
        core.command(*parse_command_request(fpv_raw))
        core.command(*parse_command_request(att_raw))
        core.command(*parse_command_request(json.dumps({
            'action': 'proportional_rate', 'client_id': C1, 'seq': 4, 'gesture_id': G1,
            'roll_deg_s': 4.0, 'pitch_deg_s': -2.0}).encode()))
        now[0] = 0.4
        self.assertTrue(core.watchdog())
        core.command(*parse_command_request(json.dumps({
            'action': 'device_command', 'client_id': C1, 'seq': 5, 'command': 2}).encode()))
        self.assertEqual(calls, [
            ('fpv', 12.5, -30.0, -5),
            ('att', -10.0, 15.0),
            ('prop', 4.0, -2.0),
            ('prop', 0.0, 0.0),
            ('dev', 2),
        ])

    def test_availability_for_stopped_motors_and_limits(self):
        s = dict(control_enabled=True, teleop_enabled=False, max_rate_deg_s=80,
                 roll_limit_deg=30, pitch_limit_deg=90, enable_auxiliary_interfaces=False,
                 connected=True, state={'gimbal_state': 2, 'hardware_error': 0, 'temperature_ready': False},
                 rate_subscribers=1, center_subscribers=1, attitude_subscribers=1,
                 proportional_rate_subscribers=0, hold_service_ready=True)
        # Stopped motors (state 2) allow ENABLE_MOTORS (2), but block motion & calibration.
        self.assertIsNone(availability_error('device_command', s, extra={'command': 2}))
        self.assertIn('initializing, stopped', availability_error('device_command', s, extra={'command': 1}))
        self.assertIn('initializing, stopped', availability_error('attitude', s, 0, 0))
        s['state']['gimbal_state'] = 4
        self.assertIn('Temperature is not ready', availability_error('device_command', s, extra={'command': 1}))
        s['state']['temperature_ready'] = True
        self.assertIsNone(availability_error('device_command', s, extra={'command': 1}))
        self.assertIn('Roll angle', availability_error('fpv_angle', s, 31, 0))
        self.assertIn('Pitch angle', availability_error('attitude', s, 0, -91))
        self.assertIn('enable_auxiliary_interfaces', availability_error('proportional_rate', s, 5, 0))


if __name__ == '__main__':
    unittest.main()
