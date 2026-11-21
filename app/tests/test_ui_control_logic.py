import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ui_control_logic import ControlCore, allowed_origin, availability_error, bearer_token, parse_command

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

    def test_down_view_does_not_publish_when_stop_echo_times_out(self):
        calls=[]
        core=ControlCore(lambda r,p: calls.append(('rate',r,p)), lambda: None,
                         publish_down_view=lambda: calls.append(('down',)),
                         begin_down_fence=lambda: (calls.append(('rate',0.0,0.0)) or (lambda: False)))
        with self.assertRaisesRegex(TimeoutError,'not sent'):
            core.command(*parse_command(raw('down_view',seq=1)))
        self.assertEqual(calls,[('rate',0.0,0.0)])

    def test_explicit_stop_cancels_pending_down_without_waiting_for_echo(self):
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
                 pitch_limit_deg=120, rate_echo_publishers=1)
        self.assertIsNone(availability_error('move',s,5,0))
        self.assertIn('configured limit',availability_error('move',s,9,0))
        s['teleop_enabled']=False
        self.assertIn('disabled',availability_error('move',s,5,0))
        self.assertIsNone(availability_error('center',s))
        self.assertIsNone(availability_error('down_view',s))
        s['pitch_limit_deg'] = 89.9
        self.assertIn('at least 90 degrees',availability_error('down_view',s))
        s['pitch_limit_deg'] = 90
        self.assertIsNone(availability_error('down_view',s))
        s['rate_echo_publishers'] = 0
        self.assertIn('stop-confirmation',availability_error('down_view',s))
        s['rate_echo_publishers'] = 1
        s['control_enabled']=False
        self.assertIsNone(availability_error('stop',s))
        self.assertIn('disabled',availability_error('center',s))
        s.update(control_enabled=True, connected=False)
        self.assertIn('feedback',availability_error('center',s))


if __name__ == '__main__':
    unittest.main()
