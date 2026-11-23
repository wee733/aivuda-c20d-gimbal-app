import http.client
import importlib.util
import json
import sys
import threading
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ui_control_logic import ControlCore

# Import the HTTP server without a ROS runtime; only its HTTP handlers are used.
mods = {}
for name in ('rclpy', 'rclpy.node', 'rclpy.qos', 'c20d_driver', 'c20d_driver.msg', 'c20d_driver.srv'):
    mods[name] = sys.modules.get(name)
    sys.modules[name] = types.ModuleType(name)
sys.modules['c20d_driver.srv'].DeviceCommand = object
sys.modules['rclpy.node'].Node = object
sys.modules['rclpy.qos'].qos_profile_sensor_data = object()
for name in ('AngularRateCommand', 'AttitudeCommand', 'GimbalState',
             'ProportionalRateCommand', 'RelativeAngleCommand'):
    setattr(sys.modules['c20d_driver.msg'], name, object)
spec = importlib.util.spec_from_file_location('c20d_ui_server_test', SCRIPTS / 'ui_control_server.py')
server_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_mod)
for name, prior in mods.items():
    if prior is None:
        del sys.modules[name]
    else:
        sys.modules[name] = prior


class FakeNode:
    def __init__(self):
        self.events = []
        self.pitch_limit = 120
        self.roll_limit = 60
        self.confirm_hold = True
        self.core = ControlCore(lambda r,p: self.events.append(('rate',r,p)),
                                lambda: self.events.append(('center',)),
                                publish_down_view=lambda: self.events.append(('down',)),
                                begin_down_fence=lambda: (self.events.append(('rate',0.0,0.0)) or (lambda: self.confirm_hold)),
                                publish_fpv_angle=lambda r,p,s: self.events.append(('fpv',r,p,s)),
                                publish_attitude=lambda r,p: self.events.append(('att',r,p)),
                                publish_proportional_rate=lambda r,p: self.events.append(('prop',r,p)),
                                send_device_command=lambda cmd: self.events.append(('dev',cmd)) or f'Device command {cmd}: OK')
    def status(self):
        return dict(ok=True, control_enabled=True, teleop_enabled=True,
                    enable_auxiliary_interfaces=True, publish_joint_states=False,
                    connected=True, state={'hardware_error':0, 'gimbal_state':4, 'temperature_ready':True},
                    rate_subscribers=1, center_subscribers=1, attitude_subscribers=1,
                    proportional_rate_subscribers=1, max_rate_deg_s=80,
                    roll_limit_deg=self.roll_limit, pitch_limit_deg=self.pitch_limit, hold_service_ready=True)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = FakeNode()
        cls.server = server_mod.ControlHTTPServer(('127.0.0.1', 0), cls.node, lambda token: token == 'valid')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        default = {'Host': 'jetson.test:8543', 'Origin':'https://jetson.test:8543',
                   'X-Forwarded-Proto':'https', 'Sec-Fetch-Site':'same-origin'}
        default.update(headers or {})
        conn.request(method, path, body=body, headers=default)
        response = conn.getresponse(); data=json.loads(response.read()); status=response.status
        cors = response.getheader('Access-Control-Allow-Origin')
        conn.close()
        return status, data, cors

    def test_aivuda_gateway_token_validation(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return b'{"username":"tester","role":"admin"}'
        class Opener:
            def open(self, request, timeout):
                self.last_url=request.full_url
                self.last_timeout=timeout
                return Response()
        fake=Opener(); prior=server_mod._AUTH_OPENER
        server_mod._AUTH_OPENER=fake
        try:
            self.assertTrue(server_mod.validate_token('a+b/='))
            self.assertEqual(fake.last_url,'http://127.0.0.1:8000/aivuda_os/api/auth/me?token=a%2Bb%2F%3D')
            self.assertEqual(fake.last_timeout,2)
        finally:
            server_mod._AUTH_OPENER=prior

    def test_auth_and_origin(self):
        self.assertEqual(self.request('GET','/status')[0], 401)
        self.assertEqual(self.request('GET','/status',headers={'Authorization':'Bearer invalid'})[0], 401)
        self.assertEqual(self.request('GET','/status',headers={'Authorization':'Bearer valid', 'Origin':'https://evil.test'})[0], 403)
        status, body, cors = self.request('GET','/status',headers={'Authorization':'Bearer valid'})
        self.assertEqual(status, 200); self.assertTrue(body['ok']); self.assertIsNone(cors)

    def test_post_seq_move_and_stop(self):
        c='00000000-0000-4000-8000-000000000001'; g='10000000-0000-4000-8000-000000000001'
        headers={'Authorization':'Bearer valid','Content-Type':'application/json'}
        move={'action':'move','client_id':c,'seq':1,'gesture_id':g,'roll_deg_s':2,'pitch_deg_s':0}
        self.assertEqual(self.request('POST','/command',json.dumps(move),headers)[0],200)
        stop={'action':'stop','client_id':c,'seq':2,'gesture_id':g}
        self.assertEqual(self.request('POST','/command',json.dumps(stop),headers)[0],200)
        self.assertEqual(self.request('POST','/command',json.dumps(move),headers)[0],409)
        self.assertEqual(self.node.events[-1],('rate',0.0,0.0))
        self.assertEqual(self.request('POST','/command',json.dumps(move),{'Authorization':'Bearer valid','Content-Type':'text/plain'})[0],400)

    def test_down_view_requires_auth_and_respects_pitch_limit(self):
        c='00000000-0000-4000-8000-000000000003'
        down={'action':'down_view','client_id':c,'seq':1}
        headers={'Authorization':'Bearer valid','Content-Type':'application/json'}
        self.assertEqual(self.request('POST','/command',json.dumps(down),{'Content-Type':'application/json'})[0],401)
        self.node.pitch_limit = 89
        status, data, _ = self.request('POST','/command',json.dumps(down),headers)
        self.assertEqual(status,409)
        self.assertIn('pitch limit',data['error'])
        self.node.pitch_limit = 90
        self.assertEqual(self.request('POST','/command',json.dumps(down),headers)[0],200)
        self.assertEqual(self.node.events[-2:],[('rate',0.0,0.0),('down',)])
        self.assertEqual(self.request('POST','/command',json.dumps(down),headers)[0],409)
        self.node.pitch_limit = 120

    def test_down_view_publishes_relative_pitch_without_center_bits(self):
        class FakeStamp:
            def now(self): return self
            def to_msg(self): return 'stamp'
        class Msg:
            def __init__(self):
                self.header=types.SimpleNamespace(stamp=None)
                self.center_roll=False; self.center_pitch=False
        published=[]
        node=types.SimpleNamespace(get_clock=lambda:FakeStamp(),
                                   center_pub=types.SimpleNamespace(publish=published.append),
                                   status=FakeNode().status)
        prior=server_mod.RelativeAngleCommand
        server_mod.RelativeAngleCommand=Msg
        try:
            server_mod.ControlNode.send_down_view(node)
        finally:
            server_mod.RelativeAngleCommand=prior
        self.assertEqual((published[0].roll_deg,published[0].pitch_deg),(0.0,-90.0))
        self.assertFalse(published[0].center_roll)
        self.assertFalse(published[0].center_pitch)

    def test_down_view_reports_missing_hold_response_without_publishing_angle(self):
        c='00000000-0000-4000-8000-000000000004'
        down={'action':'down_view','client_id':c,'seq':1}
        headers={'Authorization':'Bearer valid','Content-Type':'application/json'}
        self.node.confirm_hold = False
        before = self.node.events.count(('down',))
        try:
            status,data,_ = self.request('POST','/command',json.dumps(down),headers)
            self.assertEqual(status,504)
            self.assertIn('not sent',data['error'])
            self.assertEqual(self.node.events.count(('down',)),before)
        finally:
            self.node.confirm_hold = True

    def test_fpv_attitude_and_device_command_routes(self):
        c = '00000000-0000-4000-8000-000000000005'
        headers = {'Authorization': 'Bearer valid', 'Content-Type': 'application/json'}
        fpv = {'action': 'fpv_angle', 'client_id': c, 'seq': 1, 'roll_deg': 10, 'pitch_deg': -20, 'fpv_sensitivity': 3}
        self.assertEqual(self.request('POST', '/command', json.dumps(fpv), headers)[0], 200)
        self.assertEqual(self.node.events[-1], ('fpv', 10.0, -20.0, 3))
        att = {'action': 'attitude', 'client_id': c, 'seq': 2, 'roll_deg': 0, 'pitch_deg': 15}
        self.assertEqual(self.request('POST', '/command', json.dumps(att), headers)[0], 200)
        self.assertEqual(self.node.events[-1], ('att', 0.0, 15.0))
        dev = {'action': 'device_command', 'client_id': c, 'seq': 3, 'command': 1}
        self.assertEqual(self.request('POST', '/command', json.dumps(dev), headers)[0], 200)
        self.assertEqual(self.node.events[-1], ('dev', 1))
        bad_roll = {'action': 'attitude', 'client_id': c, 'seq': 4, 'roll_deg': 61, 'pitch_deg': 0}
        self.assertEqual(self.request('POST', '/command', json.dumps(bad_roll), headers)[0], 400)


if __name__ == '__main__': unittest.main()
