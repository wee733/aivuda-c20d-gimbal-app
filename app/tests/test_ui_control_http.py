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
for name in ('rclpy', 'rclpy.node', 'rclpy.qos', 'c20d_driver', 'c20d_driver.msg'):
    mods[name] = sys.modules.get(name)
    sys.modules[name] = types.ModuleType(name)
sys.modules['rclpy.node'].Node = object
sys.modules['rclpy.qos'].qos_profile_sensor_data = object()
for name in ('AngularRateCommand', 'GimbalState', 'RelativeAngleCommand'):
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
        self.confirm_echo = True
        self.core = ControlCore(lambda r,p: self.events.append(('rate',r,p)),
                                lambda: self.events.append(('center',)),
                                publish_down_view=lambda: self.events.append(('down',)),
                                begin_down_fence=lambda: (self.events.append(('rate',0.0,0.0)) or (lambda: self.confirm_echo)))
    def status(self):
        return dict(ok=True, control_enabled=True, teleop_enabled=True, connected=True, state={'hardware_error':0, 'gimbal_state':0},
                    rate_subscribers=1, center_subscribers=1, max_rate_deg_s=80,
                    pitch_limit_deg=self.pitch_limit, rate_echo_publishers=1)


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

    def test_down_view_reports_missing_stop_echo_without_publishing_angle(self):
        c='00000000-0000-4000-8000-000000000004'
        down={'action':'down_view','client_id':c,'seq':1}
        headers={'Authorization':'Bearer valid','Content-Type':'application/json'}
        self.node.confirm_echo = False
        before = self.node.events.count(('down',))
        try:
            status,data,_ = self.request('POST','/command',json.dumps(down),headers)
            self.assertEqual(status,504)
            self.assertIn('not sent',data['error'])
            self.assertEqual(self.node.events.count(('down',)),before)
        finally:
            self.node.confirm_echo = True

    def test_zero_rate_fence_only_accepts_its_matching_echo(self):
        class Clock:
            def now(self): return self
            def to_msg(self): return types.SimpleNamespace(sec=123, nanosec=456)
        class Msg:
            def __init__(self):
                self.header=types.SimpleNamespace(stamp=None,frame_id='')
                self.roll_deg_s=0.; self.pitch_deg_s=0.
        sent=[]
        node=types.SimpleNamespace(get_clock=lambda:Clock(),_fence_lock=threading.Lock(),
                                   _fences={},rate_pub=types.SimpleNamespace(publish=sent.append))
        prior=server_mod.AngularRateCommand
        server_mod.AngularRateCommand=Msg
        try:
            waiter=server_mod.ControlNode.begin_down_fence(node)
        finally:
            server_mod.AngularRateCommand=prior
        message=sent[0]
        wrong=Msg(); wrong.header.stamp=message.header.stamp; wrong.header.frame_id='other'
        server_mod.ControlNode.on_rate_echo(node,wrong)
        self.assertFalse(next(iter(node._fences.values())).is_set())
        message.pitch_deg_s=1
        server_mod.ControlNode.on_rate_echo(node,message)
        self.assertFalse(next(iter(node._fences.values())).is_set())
        message.pitch_deg_s=0
        server_mod.ControlNode.on_rate_echo(node,message)
        self.assertTrue(waiter())
        self.assertEqual(node._fences,{})


if __name__ == '__main__': unittest.main()
