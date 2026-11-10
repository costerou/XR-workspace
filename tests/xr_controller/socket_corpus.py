"""The socket corpus (rust/corpus/sockets/frames.json): every message on the panel's controller socket and on a run's
control socket, as the shipped code writes it and as the shipped code judges it, for the Rust rebuild
(docs/rust-rebuild-plan.md, phase 1, xr-wire) to round-trip byte for byte.

Nothing is written by hand. A real `server.Server` with a real `state.Controller` (fake probe, store and spawn, as
tests/xr_controller/test_state.py fakes them) serves a temporary socket. The panel's requests are written as
ControllerClient.qml writes them (`JSON.stringify(Object.assign({}, request, {schema: 1, request_id: id}))`: compact,
non-ASCII as is, which is `json.dumps(..., separators=(',', ':'), ensure_ascii=False)`), and the replies and state events
are read back off the socket. The controller's run frames come from the real `__main__.Units.control` writing to a real
`ipc.PrivateServer` in a temporary runtime directory, where the real `supervisor.control_request` takes them and answers
status. The key helper's frames come from `xr_control.control_frame`. The run is walked from idle through a confirmed
start, a running session (every setting the panel forwards) to Stop.

Each kind of frame has its writer's style, which a round trip must reproduce:
  panel_request      compact, non-ASCII as is; the request's keys, then schema, then request_id
  controller_message compact, ASCII only; schema first, request_id last (a reply) or schema, event, snapshot (an event)
  run_frame          compact; ASCII only from the controller, non-ASCII as is from the key helper
  status_reply       json.dumps defaults (", " and ": "), ASCII only

`run_cases` and `panel_cases` are frames wrong (or right) on purpose, with what the shipped decoder made of them:
`ipc.ControlQueue.push` (decode_control, then identity, replay and queue limit) for a run frame, and whether the real
Server answered or dropped the connection for a panel line. A run case whose refusal comes from a payload rule that a
module other than the wire owns (options_menu, video, environment, immersive, geometry, view) carries `owner`: the wire
layer passes that payload on unread, and the owner checks it. A case where the Rust decoder is decided stricter than
Python carries `divergence`.

    TMPDIR=/tmp /usr/bin/python3 -m tests.xr_controller.socket_corpus --write

test_socket_corpus.py fails when the checked-in file differs from what the shipped code produces today.
"""
import copy
from contextlib import ExitStack
import itertools
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest import mock

from tools.native_spike import ipc, supervisor
from tools.native_spike.ipc import MAX_FRAME
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.native_spike.view import USER_OPS
from tools.native_spike.xr_control import control_frame
from tools.xr_controller import __main__ as controller_main, server, state

FILE = Path(__file__).resolve().parents[2] / 'rust' / 'corpus' / 'sockets' / 'frames.json'
SESSION = 'session-1'
JOURNAL_DIR = '/run/user/1000/xr-workspace-spike'  # the snapshot shows the journal path: a fixed one, not the temp folder
FIRST_REQUEST_ID = 1762000000000000000  # time.time_ns() stand-in, counting up
DIVERGENCE = {
    'depth': 'nested deeper than 32 levels: Python takes it, the Rust decoder refuses it (xr_wire::json::MAX_DEPTH)',
    'bom': 'a leading UTF-8 byte order mark: json.loads of bytes skips it, the Rust decoder refuses it; no panel writes one',
}


class Store:
    def __init__(self): self.profile = copy.deepcopy(DEFAULT_PROFILE)
    def load(self, model): return copy.deepcopy(self.profile)
    def save(self, model, p): self.profile = copy.deepcopy(p)


class Units(controller_main.Units):
    """The real run control (Units.control); the systemd calls faked."""
    active = False
    def __init__(self, runtime): super().__init__(runtime); self.stopped = []
    def alive(self, run_id): return self.active
    def alive_many(self, run_ids): return {r: self.active for r in run_ids}
    def stop(self, run_id): self.stopped.append(run_id)


def status_reply(names):
    """What a run's ViewControl.status_reply holds, in its key order."""
    return dict(capabilities=[*USER_OPS, 'options_runtime_complete'],
                view=dict(selected=1, zoomed=False, mode='anchor', spacing=40, prediction=True, menu_open=False),
                status=dict(frames=1200, missed=2, fps=59.94, latency_ms=11.5, pose_age_ms=0.25),
                placements=[dict(x=0, y=0, distance=100, curvature=0), dict(x=-12.5, y=3, distance=110, curvature=20)],
                names=names, immersive=dict(names=[], stereo=False, failed=False),
                desktops=[dict(width=1920, height=1080, scale=1.0), dict(width=3840, height=1080, scale=1.25)])


class Run:
    """A run's control socket: the real PrivateServer and supervisor.control_request on a thread, every frame taken and
    every status reply sent recorded."""
    def __init__(self, runtime, run_id, session):
        self.server = ipc.PrivateServer(runtime, run_id, session)
        self.server.socket.settimeout(0.05)
        queue, frames = self.server.queue, []
        push = queue.push
        def recording_push(raw):
            frames.append(raw)
            return push(raw)
        queue.push = recording_push
        self.frames, self.replies, self.names = frames, [], ['XR-1', 'XR-2']
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True); self.thread.start()

    def status_reply(self, status): return status_reply(list(self.names))

    def _loop(self):
        rig = self
        class Listener:
            def accept(self):
                conn, addr = rig.server.socket.accept()
                return Conn(conn), addr
        class Conn:
            def __init__(self, conn): self.conn = conn
            def __getattr__(self, name): return getattr(self.conn, name)
            def __enter__(self): return self
            def __exit__(self, *args): self.conn.close()
            def sendall(self, data): rig.replies.append(data); return self.conn.sendall(data)
        class Control:
            server = type('S', (), {})()
        control = Control()
        control.server.socket = Listener()
        control.server.receive = lambda conn: self.server.receive(conn.conn)
        control.server.queue = self.server.queue
        control.status_reply = self.status_reply
        while not self.done.is_set():
            supervisor.control_request(control, None)

    def close(self):
        self.done.set(); self.thread.join(); self.server.close()


class Rig:
    def __init__(self, stack, tmp):
        self.runtime = tmp / 'runtime'; self.state_dir = tmp / 'state'
        for d in (self.runtime, self.state_dir): d.mkdir(mode=0o700)
        fd = os.open(self.state_dir, os.O_DIRECTORY); stack.callback(os.close, fd)
        self.now = 0; self.spawned = []
        self.observation = dict(model='beast', output='DP-2', reason=None, instance='instance',
                                monitors=[dict(name='DP-2'), dict(name='DP-1')], journals=[],
                                installed={k: dict(path='/p/' + k, sha256='a' * 64) for k in ('plugin', 'renderer')}, plugins=[])
        self.units = Units(self.runtime)
        self.controller = state.Controller(lambda: self.now, lambda: copy.deepcopy(self.observation),
                                           lambda *a: self.spawned.append(a), self.units, Store(), fd, self.state_dir)
        self.server = server.Server(self.controller, self.runtime / 'viture-workspace' / 'controller.sock')
        stack.callback(self.server.close)
        self.server.next_tick = float('inf')  # ticks are taken by hand, in order
        self.panel_requests, self.controller_messages = [], []
        self.client = self.connect()
        self.next_id = itertools.count(1)
        self.run = None

    def connect(self):
        c = socket.socket(socket.AF_UNIX); c.connect(str(self.server.path)); c.setblocking(False)
        self.server.step(0.05)
        return c

    def drain(self, c):
        """Step the server until it has nothing more to say to `c`: the lines it sent, and whether it hung up."""
        data, closed = b'', False
        for _ in range(6):
            self.server.step(0.02)
            while True:
                try: chunk = c.recv(65536)
                except BlockingIOError: break
                if not chunk: closed = True; break
                data += chunk
            if closed: break
        return data, closed

    def take(self, data):
        for line in data.splitlines(keepends=True):
            msg = json.loads(line)
            self.controller_messages.append(dict(kind='state' if msg.get('event') == 'state' else 'reply', text=line.decode()))

    def panel(self, request):
        """One request as ControllerClient.qml sends it; the reply and any state event come back in order."""
        line = json.dumps({**request, 'schema': 1, 'request_id': next(self.next_id)}, separators=(',', ':'), ensure_ascii=False) + '\n'
        self.panel_requests.append(dict(text=line))
        self.client.sendall(line.encode())
        data, closed = self.drain(self.client)
        assert not closed and data, (request, data)
        self.take(data)
        return [json.loads(l) for l in data.splitlines()]

    def tick(self):
        self.controller.tick()
        data, _ = self.drain(self.client)
        self.take(data)


def walk(rig):
    p = rig.panel
    p(dict(op='status'))
    p(dict(op='get_settings'))
    p(dict(op='set_settings', settings=dict(fallback='DP-1')))
    p(dict(op='set_settings', settings=dict(stabilization=40)))
    p(dict(op='set_settings', settings=dict(tracking=False)))
    p(dict(op='set_settings', settings=dict(tracking=True)))
    p(dict(op='set_settings', settings=dict(mode_switch=True)))
    p(dict(op='set_settings', settings=dict(desktops=[dict(width=1920, height=1080, scale=1.0), dict(width=2560, height=1440, scale=1.5)])))  # refused
    p(dict(op='set_settings', settings=dict(desktops=[dict(width=1920, height=1080, scale=1.0), dict(width=2560, height=1440, scale=1.25), dict(width=3840, height=1080, scale=1.0)])))
    p(dict(op='set_pro2_projection', projection=1.5))  # not a Pro 2: refused
    p(dict(op='cancel'))  # nothing to cancel: refused
    p(dict(op='stop'))  # nothing running: refused
    p(dict(op='begin_recover'))  # nothing to recover: refused
    p(dict(op='frobnicate'))  # unknown: refused with the snapshot
    begin = next(m for m in p(dict(op='begin_start')) if 'request_id' in m)
    p(dict(op='cancel'))
    begin = next(m for m in p(dict(op='begin_start')) if 'request_id' in m)
    p(dict(op='confirm_start', scope_id='0' * 32))  # stale scope
    p(dict(op='confirm_start', scope_id=begin['scope_id'], optional=dict(tracking=True, mode_switch=False)))
    run_id = begin['scope']['run_id']

    # The runner made its journal and its control socket: the controller finds the run alive and polls its status.
    rig.run = Run(rig.runtime, run_id, SESSION)
    rig.observation['journals'] = [dict(run_id=run_id, session=SESSION, path=f'{JOURNAL_DIR}/{run_id}/journal.json', operations=[])]
    rig.units.active = True
    rig.now = 10; rig.tick()
    p(dict(op='status'))
    p(dict(op='view_op', operation='recenter'))
    p(dict(op='view_op', operation='zoom_in'))
    p(dict(op='view_op', operation='video_pause'))
    p(dict(op='view_op', operation='rm -rf'))  # not a view operation: refused
    p(dict(op='set_settings', settings=dict(stabilization=60)))
    p(dict(op='set_settings', settings=dict(spacing=80)))
    p(dict(op='set_settings', settings=dict(prediction=False)))
    p(dict(op='set_settings', settings=dict(desktops=[dict(width=1920, height=1080, scale=1.0)])))  # locked while running
    p(dict(op='set_settings', settings=dict(placements=[dict(x=0, y=0, distance=100, curvature=0), dict(x=-12.5, y=3, distance=110, curvature=20)])))  # refused
    p(dict(op='set_settings', settings=dict(placements=[dict(x=0, y=0, distance=100, curvature=0), dict(x=-12, y=3, distance=110, curvature=20), dict(x=30, y=-4, distance=90, curvature=10)])))
    p(dict(op='set_settings', settings=dict(environment=dict(background='grid', last='grid', brightness=80))))
    p(dict(op='set_settings', settings=dict(immersive=dict(**{'global': True}))))
    p(dict(op='video_open', path='/home/user/Videos/Café 360° — “trip”.mp4'))
    p(dict(op='video_open', url='https://example.com/watch?v=abc&t=10'))
    p(dict(op='video_open', path='relative.mp4'))  # refused before any frame
    rig.run.names = ['XR-1', 'Écran ☕']
    rig.now = 11; rig.tick()  # a new status: a state event with the run's non-ASCII desktop name
    p(dict(op='stop'))
    # The unit ended with its journal left: Recover is offered, and confirmed.
    rig.units.active = False
    rig.now = 20; rig.tick()
    recover = next(m for m in p(dict(op='begin_recover')) if 'request_id' in m)
    p(dict(op='confirm_recover', scope_id=recover['scope_id']))
    return run_id


def key_helper_frames(run_id):
    """What the key helper (xr_control) sends: control_frame, with its fast path for an empty payload."""
    rid = itertools.count(FIRST_REQUEST_ID + 500)
    cases = [('zoom_in', {}), ('menu_toggle', {}), ('video_pause', {}), ('binds_key', {'mods': 64, 'code': 38}),
             ('options_scroll', {'delta': -3}), ('video_open', {'path': '/home/user/Videos/Café ☕ 360.mp4'}),
             ('video_open', {'url': 'https://example.com/v?id=1&t=2'}), ('resize_cancel', {})]
    return [control_frame(run_id, SESSION, op, payload, next(rid)) for op, payload in cases]


def verdict(push, raw):
    try: push(raw); return dict(verdict='accepted')
    except (ValueError, TypeError, KeyError) as exc: return dict(verdict='refused', error=str(exc))


def run_cases():
    """(about, earlier frames, the frame, owner): each decided by a fresh ControlQueue('run-1', SESSION)."""
    base = '{"schema":1,"request_id":%d,"run_id":"run-1","session":"session-1","op":"%s","payload":%s}'
    def f(op='status', payload='{}', rid=FIRST_REQUEST_ID): return (base % (rid, op, payload) + '\n').encode()
    cases = [
        ('a status request', [], f(), None),
        ('the largest request id', [], f(rid=2**64 - 1), None),
        ('request id 1', [], f(rid=1), None),
        ('whitespace around the object, CR before the newline', [], b' ' + f()[:-1] + b' \r\n', None),
        ('keys in another order', [], b'{"op":"status","payload":{},"session":"session-1","run_id":"run-1","request_id":7,"schema":1}\n', None),
        ('a 128 character run token', [], f().replace(b'"run-1"', b'"' + b'r' * 128 + b'"'), None),
        ('select', [], f('select', '{"output_id":"DP-2"}'), None),
        ('binds_key', [], f('binds_key', '{"mods":64,"code":38}'), None),
        ('binds_key at its bounds', [], f('binds_key', '{"mods":0,"code":255}'), None),
        ('binds_key mods 255 code 8', [], f('binds_key', '{"mods":255,"code":8}'), None),
        ('every plain op takes an empty payload', [], f('layout_reset'), None),
        ('a video key', [], f('video_pause'), None),
        ('placement', [], f('placement', '{"desktops":[{"x":0,"y":0,"distance":100,"curvature":0}]}'), None),
        ('stabilization', [], f('stabilization', '{"strength":40}'), None),
        ('environment', [], f('environment', '{"background":"grid","last":"grid","brightness":80}'), None),
        ('video_open with a path', [], f('video_open', '{"path":"/v/a.mp4"}'), None),
        ('options_scroll', [], f('options_scroll', '{"delta":-3}'), None),
        ('two in order', [f(rid=5)], f(rid=6), None),
        # framing
        ('no newline', [], f()[:-1], None),
        ('a newline inside', [], f()[:20] + b'\n' + f()[20:], None),
        ('two frames in one', [], f() + f(rid=FIRST_REQUEST_ID + 1), None),
        ('one byte over the frame limit', [], f('status', '{}')[:-2] + b' ' * (MAX_FRAME - len(f()) + 1) + b'}\n', None),
        ('exactly the frame limit', [], f('status', '{}')[:-2] + b' ' * (MAX_FRAME - len(f())) + b'}\n', None),
        ('invalid UTF-8', [], f('status', '{}').replace(b'"session-1"', b'"session-\xff"'), None),
        ('a byte order mark', [], b'\xef\xbb\xbf' + f(), None),
        ('not JSON', [], b'status\n', None),
        ('an empty line', [], b'\n', None),
        ('an array', [], b'[1,2]\n', None),
        ('a duplicate key', [], f()[:-2] + b',"op":"status"}\n', None),
        ('NaN', [], f('select', '{"output_id":NaN}'), None),
        ('Infinity request id', [], base.replace('%d', 'Infinity').__mod__(('status', '{}')).encode() + b'\n', None),
        # the six keys
        ('a missing key', [], b'{"schema":1,"request_id":9,"run_id":"run-1","session":"session-1","op":"status"}\n', None),
        ('an extra key', [], f()[:-2] + b',"extra":1}\n', None),
        ('schema 2', [], f().replace(b'"schema":1', b'"schema":2'), None),
        ('schema true', [], f().replace(b'"schema":1', b'"schema":true'), None),
        ('schema 1.0', [], f().replace(b'"schema":1', b'"schema":1.0'), None),
        ('schema "1"', [], f().replace(b'"schema":1', b'"schema":"1"'), None),
        ('request id 0', [], f(rid=0), None),
        ('request id -1', [], f(rid=-1), None),
        ('request id 2**64', [], f(rid=2**64), None),
        ('request id 1.0', [], f().replace(b'%d' % FIRST_REQUEST_ID, b'1.0'), None),
        ('request id true', [], f().replace(b'%d' % FIRST_REQUEST_ID, b'true'), None),
        ('request id "5"', [], f().replace(b'%d' % FIRST_REQUEST_ID, b'"5"'), None),
        ('request id null', [], f().replace(b'%d' % FIRST_REQUEST_ID, b'null'), None),
        ('an empty run token', [], f().replace(b'"run-1"', b'""'), None),
        ('a run token starting with -', [], f().replace(b'"run-1"', b'"-run"'), None),
        ('a run token with a space', [], f().replace(b'"run-1"', b'"run 1"'), None),
        ('a 129 character run token', [], f().replace(b'"run-1"', b'"' + b'r' * 129 + b'"'), None),
        ('a run token that is a number', [], f().replace(b'"run-1"', b'1'), None),
        ('a session token with a slash', [], f().replace(b'"session-1"', b'"a/b"'), None),
        ('a non-ASCII session', [], f().replace(b'"session-1"', '"sessión"'.encode()), None),
        # identity, replay and the queue
        ('another run', [], f().replace(b'"run-1"', b'"run-2"'), None),
        ('another session', [], f().replace(b'"session-1"', b'"session-2"'), None),
        ('a replayed request id', [f(rid=5)], f(rid=5), None),
        ('an older request id', [f(rid=6)], f(rid=5), None),
        ('a ninth queued frame', [f(rid=i) for i in range(1, 9)], f(rid=9), None),
        # the op
        ('an unknown op', [], f('bogus'), None),
        ('a legacy op', [], f('menu_mouse'), None),
        ('an op that is not a string', [], f().replace(b'"op":"status"', b'"op":7'), None),
        ('an op that is null', [], f().replace(b'"op":"status"', b'"op":null'), None),
        ('an unknown options op', [], f('options_bogus'), None),
        ('an unknown video op', [], f('video_bogus'), None),
        # the payload the wire owns
        ('a payload that is an array', [], f('status', '[]'), None),
        ('a payload that is null', [], f('status', 'null'), None),
        ('a payload that is a string', [], f('status', '"x"'), None),
        ('a payload on a plain op', [], f('status', '{"x":1}'), None),
        ('a payload on a video key', [], f('video_pause', '{"x":1}'), 'video'),
        ('select without output_id', [], f('select', '{}'), None),
        ('select with an extra key', [], f('select', '{"output_id":"DP-2","x":1}'), None),
        ('select with a bad token', [], f('select', '{"output_id":"DP 2"}'), None),
        ('select with a number', [], f('select', '{"output_id":2}'), None),
        ('binds_key without code', [], f('binds_key', '{"mods":64}'), None),
        ('binds_key with an extra key', [], f('binds_key', '{"mods":64,"code":38,"x":1}'), None),
        ('binds_key mods 256', [], f('binds_key', '{"mods":256,"code":38}'), None),
        ('binds_key mods -1', [], f('binds_key', '{"mods":-1,"code":38}'), None),
        ('binds_key code 7', [], f('binds_key', '{"mods":64,"code":7}'), None),
        ('binds_key code 256', [], f('binds_key', '{"mods":64,"code":256}'), None),
        ('binds_key code a string', [], f('binds_key', '{"mods":64,"code":"38"}'), None),
        ('binds_key code true', [], f('binds_key', '{"mods":64,"code":true}'), None),
        ('binds_key code 38.0', [], f('binds_key', '{"mods":64,"code":38.0}'), None),
        # payloads their owners check
        ('options_scroll without delta', [], f('options_scroll', '{}'), 'options_menu'),
        ('options_close with a payload', [], f('options_close', '{"x":1}'), 'options_menu'),
        ('options_open with a payload that is an array', [], f('options_open', '[]'), None),
        ('video_open with no source', [], f('video_open', '{}'), 'video'),
        ('video_open with a relative path', [], f('video_open', '{"path":"a.mp4"}'), 'video'),
        ('video_open with a payload that is an array', [], f('video_open', '[]'), None),
        ('environment with nothing', [], f('environment', '{}'), 'environment'),
        ('immersive with nothing', [], f('immersive', '{}'), 'immersive'),
        ('placement with no desktops', [], f('placement', '{"desktops":[]}'), 'geometry'),
        ('placement with an extra key', [], f('placement', '{"desktops":[{"x":0,"y":0,"distance":100,"curvature":0}],"x":1}'), 'geometry'),
        ('stabilization out of range', [], f('stabilization', '{"strength":1000}'), 'view'),
        ('stabilization with an extra key', [], f('stabilization', '{"strength":40,"x":1}'), 'view'),
        ('stabilization with a payload that is an array', [], f('stabilization', '[]'), None),
    ]
    out = []
    for about, earlier, raw, owner in cases:
        run_id = 'r' * 128 if about == 'a 128 character run token' else 'run-1'  # the queue's own run
        queue = ipc.ControlQueue(run_id, SESSION)
        for e in earlier: queue.push(e)
        case = dict(about=about, run_id=run_id, earlier=[e.decode() for e in earlier], **encoded(raw), **verdict(queue.push, raw))
        if owner:
            assert case['verdict'] == 'refused', about
            case['owner'] = owner
        if about == 'a byte order mark': assert case['verdict'] == 'refused'
        out.append(case)
    return out


def encoded(raw):
    try: return dict(text=raw.decode('utf-8'))
    except UnicodeDecodeError: return dict(hex=raw.hex())


PANEL_CASES = [
    ('a status request', b'{"op":"status","schema":1,"request_id":1}\n', None),
    ('no request id', b'{"op":"status","schema":1}\n', None),
    ('a null request id', b'{"op":"status","schema":1,"request_id":null}\n', None),
    ('the largest request id', b'{"op":"status","schema":1,"request_id":18446744073709551615}\n', None),
    ('no op', b'{"schema":1,"request_id":2}\n', None),
    ('an unknown op', b'{"op":"bogus","schema":1,"request_id":3}\n', None),
    ('an op that is not a string', b'{"op":7,"schema":1,"request_id":3}\n', None),
    ('extra keys', b'{"op":"status","schema":1,"request_id":4,"anything":[1,{"a":null}]}\n', None),
    ('whitespace and CR', b' {"op":"status", "schema":1, "request_id":5} \r\n', None),
    ('the longest line', b'{"op":"status","schema":1,"request_id":6' + b' ' * (MAX_FRAME - 42) + b'}\n', None),
    ('two in one write', b'{"op":"status","schema":1,"request_id":7}\n{"op":"status","schema":1,"request_id":8}\n', None),
    ('a line one byte too long', b'{"op":"status","schema":1,"request_id":6' + b' ' * (MAX_FRAME - 41) + b'}\n', None),
    ('a full buffer with no newline', b'{"op":"status","schema":1,"request_id":6' + b' ' * (MAX_FRAME - 40), None),
    ('no schema', b'{"op":"status","request_id":1}\n', None),
    ('schema 2', b'{"op":"status","schema":2,"request_id":1}\n', None),
    ('schema true', b'{"op":"status","schema":true,"request_id":1}\n', None),
    ('schema 1.0', b'{"op":"status","schema":1.0,"request_id":1}\n', None),
    ('schema "1"', b'{"op":"status","schema":"1","request_id":1}\n', None),
    ('request id 0', b'{"op":"status","schema":1,"request_id":0}\n', None),
    ('request id -1', b'{"op":"status","schema":1,"request_id":-1}\n', None),
    ('request id 2**64', b'{"op":"status","schema":1,"request_id":18446744073709551616}\n', None),
    ('request id 1.5', b'{"op":"status","schema":1,"request_id":1.5}\n', None),
    ('request id true', b'{"op":"status","schema":1,"request_id":true}\n', None),
    ('request id "1"', b'{"op":"status","schema":1,"request_id":"1"}\n', None),
    ('an array', b'[1]\n', None),
    ('a string', b'"status"\n', None),
    ('an empty line', b'\n', None),
    ('not JSON', b'status\n', None),
    ('a duplicate key', b'{"op":"status","schema":1,"op":"stop"}\n', None),
    ('NaN', b'{"op":"status","schema":1,"x":NaN}\n', None),
    ('invalid UTF-8', b'{"op":"st\xffatus","schema":1}\n', None),
    ('a good line, then a bad one', b'{"op":"status","schema":1,"request_id":9}\n{"schema":2}\n', None),
    ('deeper than 32', b'{"op":"status","schema":1,"x":' + b'[' * 33 + b']' * 33 + b'}\n', 'depth'),
    ('a byte order mark', b'\xef\xbb\xbf{"op":"status","schema":1}\n', 'bom'),
]


def panel_cases(rig):
    out = []
    for about, raw, divergence in PANEL_CASES:
        c = rig.connect()
        c.sendall(raw)
        data, closed = rig.drain(c)
        c.close(); rig.server.step(0.02)
        replies = len(data.splitlines())
        case = dict(about=about, **encoded(raw), verdict='refused' if closed else 'accepted', replies=replies)
        if divergence:
            assert case['verdict'] == 'accepted', about
            case['divergence'] = DIVERGENCE[divergence]
        out.append(case)
    return out


def generate():
    counter = itertools.count(1)
    times = itertools.count(FIRST_REQUEST_ID)
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        os.chmod(tmp, 0o700)
        stack.enter_context(mock.patch.object(state.secrets, 'token_hex', lambda n: format(next(counter), f'0{2 * n}x')))
        stack.enter_context(mock.patch.object(controller_main.time, 'time_ns', lambda: next(times)))
        rig = Rig(stack, Path(tmp))
        try:
            run_id = walk(rig)
            cases = panel_cases(rig)
        finally:
            if rig.run: rig.run.close()
        run = rig.run
        status = [dict(request=req.decode(), text=rep.decode()) for req, rep in
                  zip([f for f in run.frames if json.loads(f)['op'] == 'status'], run.replies)]
        assert len(status) == len(run.replies) == 2, run.replies
        frames = [dict(writer='controller', text=f.decode()) for f in run.frames]
        frames += [dict(writer='key_helper', text=f) for f in key_helper_frames(run_id)]
        snapshot_keys = list(rig.controller.snapshot())
        assert rig.units.stopped == [run_id] and [a[0] for a in rig.spawned] == ['start', 'recover'], rig.spawned
    for f in frames:  # every frame the shipped writers send is one the run takes
        assert verdict(ipc.ControlQueue(run_id, SESSION).push, f['text'].encode())['verdict'] == 'accepted', f
    return dict(
        about='Generated by tests/xr_controller/socket_corpus.py: do not edit by hand.',
        max_frame=MAX_FRAME, max_clients=32, max_queued=8, session=SESSION, run_id=run_id,
        snapshot_keys=snapshot_keys,
        panel_requests=rig.panel_requests, controller_messages=rig.controller_messages,
        run_frames=frames, status_replies=status,
        run_cases=run_cases(), panel_cases=cases)


def dump(corpus): return json.dumps(corpus, indent=1) + '\n'


if __name__ == '__main__':
    text = dump(generate())
    if '--write' in sys.argv:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print(f'wrote {FILE}')
    else:
        sys.stdout.write(text)
