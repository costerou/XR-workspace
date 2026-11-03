"""Control-plane speed-ups keep their behaviour: the compositor request path (string paths, /proc read, the session
check's query), PrivateServer.receive's peek-then-read, strict_json's duplicate-key hook, the bind helper's import-free
key path and directly written frames, and the watchdog's lazy multiprocessing import.

Offline only: temporary sockets and files, the helper run against a throwaway control socket. Timing harness:
tools/native_spike/bench_control.py.
"""
import json
import os
from pathlib import Path
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

from tools.native_spike import hyprland as h, ipc, xr_control
from tools.native_spike.ipc import MAX_FRAME, PrivateServer, strict_json

from test_hyprland import INSTANCE, SocketFixture

HELPER = Path(xr_control.__file__)


def wire(request_id, op='status', run_id='run', session='session'):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': op,
                        'payload': {}}) + '\n').encode()


class RequestPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_read_text_matches_path_read_text(self):
        for body in (b'1234 (Hyprland) S 1 2 3', b'7 (a) b)) R ' + b'9 ' * 30 + b'\n', b'x\r\ny\rz\n', b'',
                     'é (ü) S'.encode(), b'0' * 9000):
            with self.subTest(body=body[:20]):
                path = self.root / 'stat'; path.write_bytes(body)
                self.assertEqual(h._read_text(str(path)), path.read_text())
        path = self.root / 'bad'; path.write_bytes(b'\xff (x) S')
        with self.assertRaises(UnicodeDecodeError): path.read_text()
        with self.assertRaises(UnicodeDecodeError): h._read_text(str(path))
        with self.assertRaises(FileNotFoundError): h._read_text(str(self.root / 'missing'))

    def test_request_still_checks_every_directory_and_the_socket(self):
        server = SocketFixture(self.root, b'{"x": 1, "y": 2}')
        client = h.HyprlandClient(INSTANCE, self.root)
        self.assertEqual(client.query('cursorpos'), {'x': 1, 'y': 2})
        server.join()
        for path in (self.root, self.root / 'hypr', self.root / 'hypr' / INSTANCE):
            with self.subTest(path=path):
                path.chmod(0o777)
                try:
                    with self.assertRaisesRegex(PermissionError, 'unsafe Hyprland socket directory'): client.query('cursorpos')
                finally:
                    path.chmod(0o700)
        os.unlink(self.root / 'hypr' / INSTANCE / '.socket.sock')
        (self.root / 'hypr' / INSTANCE / '.socket.sock').write_text('')
        with self.assertRaisesRegex(PermissionError, 'unsafe IPC socket'): client.query('cursorpos')

    def test_replaced_paths_are_used(self):
        client = h.HyprlandClient(INSTANCE, self.root)
        other = self.root / 'other'; other.mkdir(mode=0o700)
        server = SocketFixture(other, b'[]')
        with self.assertRaises(FileNotFoundError): client.query('monitors')  # nothing listens under the first root yet
        client.runtime_dir, client.directory = other, other / 'hypr' / INSTANCE
        self.assertEqual(client.query('monitors'), [])
        server.join(); self.assertEqual(server.requests, [b'j/monitors all'])

    def test_session_check_establishes_the_peer_with_the_smallest_query(self):
        server = SocketFixture(self.root, b'{"x": 0, "y": 0}')
        with self.assertRaisesRegex(ValueError, 'not the selected Hyprland process'):  # a Python peer, not Hyprland
            h.HyprlandClient(INSTANCE, self.root).verified_session()
        server.join()
        self.assertEqual(server.requests, [b'j/cursorpos'])

    def test_strict_json_hook_still_refuses_repeats_and_nonfinite_numbers(self):
        self.assertEqual(strict_json(b'{"a": 1, "b": {"a": 2}, "c": [{"a": 3}]}'), {'a': 1, 'b': {'a': 2}, 'c': [{'a': 3}]})
        self.assertEqual(list(strict_json('{"z": 1, "a": 2}')), ['z', 'a'])
        for raw in (b'{"a": 1, "a": 1}', b'{"b": {"x": 1, "x": 2}}', b'[NaN]', b'{"a": Infinity}', b'-Infinity'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): strict_json(raw)


class ReceiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.server = PrivateServer(Path(tmp.name), 'run', 'session'); self.addCleanup(self.server.close)

    def pair(self):
        a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
        return a, b

    def test_bytes_after_the_frame_stay_on_the_connection(self):
        a, b = self.pair()
        a.sendall(wire(1) + wire(2, 'zoom_in') + b'tail')
        self.assertEqual(self.server.receive(b)['request_id'], 1)
        self.assertEqual(self.server.receive(b)['op'], 'zoom_in')
        b.setblocking(False)
        self.assertEqual(b.recv(64), b'tail')  # never read by receive

    def test_a_frame_in_pieces(self):
        a, b = self.pair()
        raw = wire(3)
        def drip():
            for i in range(0, len(raw), 7):
                a.sendall(raw[i:i + 7]); time.sleep(0.005)
        t = threading.Thread(target=drip); t.start()
        self.assertEqual(self.server.receive(b)['request_id'], 3)
        t.join()

    def test_size_limit_is_unchanged(self):
        a, b = self.pair()
        threading.Thread(target=lambda: a.sendall(b' ' * MAX_FRAME + b'\n'), daemon=True).start()
        with self.assertRaisesRegex(ValueError, 'oversized message'): self.server.receive(b)
        a2, b2 = self.pair()
        raw = wire(4)
        exact = raw[:-2] + b' ' * (MAX_FRAME - len(raw)) + raw[-2:]  # a whole MAX_FRAME, newline last: accepted
        self.assertEqual(len(exact), MAX_FRAME)
        threading.Thread(target=lambda: a2.sendall(exact), daemon=True).start()
        self.assertEqual(self.server.receive(b2)['request_id'], 4)

    def test_truncated_and_silent_peers(self):
        a, b = self.pair()
        a.sendall(wire(5)[:-1]); a.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(ValueError, 'truncated message'): self.server.receive(b)
        a2, b2 = self.pair()
        a2.sendall(wire(6)[:10])
        with self.assertRaisesRegex(ValueError, 'incomplete message'): self.server.receive(b2)  # 0.25 s timeout


class BindHelperTests(unittest.TestCase):
    def test_frames_are_json_dumps_bytes(self):
        for op in xr_control.OPS + xr_control.VIDEO_OPS:
            for run_id, session in (('a' * 32, 'e' * 64), ('R-1.x:y_z', 'S'), ('0', '9' * 128)):
                want = json.dumps({'schema': 1, 'request_id': 1700000000123456789, 'run_id': run_id, 'session': session,
                                   'op': op, 'payload': {}}, separators=(',', ':'), ensure_ascii=False) + '\n'
                self.assertEqual(xr_control.control_frame(run_id, session, op, {}, 1700000000123456789), want)
        for args in (('r', 's', 'video_open', {'path': '/v/ä "q".mp4'}, 5), ('r', 's', 'video_open', {'url': 'https://x/y'}, 6),
                     ('r"', 's', 'zoom_in', {}, 7), ('r', 's\n', 'zoom_in', {}, 8), ('r', 's', 'zoom_in', {}, True)):
            run_id, session, op, payload, rid = args
            want = json.dumps({'schema': 1, 'request_id': rid, 'run_id': run_id, 'session': session, 'op': op,
                               'payload': payload}, separators=(',', ':'), ensure_ascii=False) + '\n'
            self.assertEqual(xr_control.control_frame(*args), want)

    def test_token_check_is_token_match(self):
        pattern = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
        rnd = random.Random(7)
        alphabet = 'aZ09_.:-/" \\\n\tä\x00'
        cases = ['', 'a', '_a', '.a', 'a' * 128, 'a' * 129, 'a\n', 'ab:c-d.e_f', '-x', 'é', 'a é', '０']
        cases += [''.join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 6))) for _ in range(5000)]
        for case in cases:
            self.assertEqual(xr_control._is_token(case), bool(pattern.match(case)), repr(case))
        self.assertEqual(xr_control.TOKEN.pattern, pattern.pattern)
        self.assertTrue(xr_control.INSTANCE.match('a' * 40 + '_1_2'))
        with self.assertRaises(AttributeError): xr_control.NOT_THERE

    def test_a_key_press_imports_no_json_re_or_socket(self):
        # The left button in the overview and every bound key start this helper: its imports are input latency.
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, '-X', 'importtime', '-I', str(HELPER), tmp + '/none.sock', 'run', 'session',
                                'zoom_in'], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 1)  # nobody listening
        imported = {line.rsplit('|', 1)[1].strip() for line in r.stderr.splitlines() if line.startswith('import time:')}
        self.assertTrue(imported)
        self.assertFalse(imported & {'json', 're', 'socket', 'struct', 'enum'}, sorted(imported))

    def test_a_key_press_reaches_the_run(self):
        with tempfile.TemporaryDirectory() as tmp, PrivateServer(Path(tmp), 'run', 'session') as server:
            p = subprocess.Popen([sys.executable, '-I', str(HELPER), str(server.path), 'run', 'session', 'resize_press'])
            server.socket.settimeout(5)
            conn, _ = server.socket.accept()
            with conn: message = server.receive(conn)
            self.assertEqual(p.wait(5), 0)
        self.assertEqual((message['op'], message['payload'], message['run_id'], message['session']),
                         ('resize_press', {}, 'run', 'session'))


class ReadbackCadenceTests(unittest.TestCase):
    """Session mode and parking read the monitor back every 10 ms for the first 250 ms (Hyprland applies the change on a
    following frame), then every 50 ms as before, within the same 2 s deadline."""
    def setUp(self):
        self.client = h.HyprlandClient(INSTANCE, Path('/nonexistent'))
        now = [0.0]
        self.now, self.sleeps = now, []
        for name, fn in (('monotonic', lambda: now[0]), ('sleep', lambda s: (self.sleeps.append(round(s, 6)), now.__setitem__(0, now[0] + s)))):
            p = unittest.mock.patch.object(h.time, name, side_effect=fn); p.start(); self.addCleanup(p.stop)
        self.evals = []
        p = unittest.mock.patch.object(self.client, '_eval', side_effect=self.evals.append); p.start(); self.addCleanup(p.stop)

    def monitor(self, **changes):
        m = {'id': 0, 'name': 'DP-2', 'width': 1920, 'height': 1080, 'x': 1920, 'y': 0, 'scale': 1.0, 'transform': 0,
             'refreshRate': 60.0, 'disabled': False, 'dpmsStatus': True, 'mirrorOf': 'none'}
        m.update(changes); return m

    def test_session_mode_is_seen_a_frame_after_the_eval(self):
        before, after = self.monitor(), self.monitor(height=1200, refreshRate=119.909)
        reads = iter([[before], [before], [before], [after]])  # the first read is before the eval
        with unittest.mock.patch.object(self.client, 'query', side_effect=lambda *a, **k: next(reads)):
            self.assertEqual(self.client.apply_session_mode('DP-2'), {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.0})
        self.assertEqual(self.sleeps, [0.01, 0.01])
        self.assertEqual(len(self.evals), 1)

    def test_slow_changes_fall_back_to_the_old_cadence_and_deadline(self):
        before = self.monitor()
        with unittest.mock.patch.object(self.client, 'query', side_effect=lambda *a, **k: [before]):
            with self.assertRaisesRegex(ValueError, 'not observed within 2s'): self.client.apply_session_mode('DP-2')
        fast = [s for s in self.sleeps if s == 0.01]
        self.assertEqual(len(fast), 25)  # 250 ms of 10 ms steps
        self.assertTrue(all(s == 0.05 for s in self.sleeps[25:-1]) and self.sleeps[-1] <= 0.05)
        self.assertAlmostEqual(self.now[0], 2.0)

    def test_parking_uses_the_same_cadence(self):
        desk = self.monitor(id=1, name='DP-1', x=0, width=2560, height=1440)
        glasses = self.monitor(x=2560)
        parked = h.presentation_placement([desk, glasses], 'DP-2')
        reads = iter([[desk, glasses], [desk, glasses], [desk, glasses], [desk, parked]])
        with unittest.mock.patch.object(self.client, 'query', side_effect=lambda *a, **k: next(reads)):
            self.assertEqual(self.client.park_presentation('DP-2')['y'], parked['y'])
        self.assertEqual(self.sleeps, [0.01, 0.01])


class RendererSendTests(unittest.TestCase):
    """Supervisor._send: one non-blocking send when the frame fits, else the same bounded 0.25 s blocking send."""
    def supervisor(self):
        from tools.native_spike.supervisor import Supervisor

        class Child:
            def poll(self): return None
            def terminate(self): pass
            def kill(self): pass
        return Supervisor(Child(), lambda: None)

    def test_frames_arrive_whole_and_in_order(self):
        sup = self.supervisor()
        a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
        frames = [(json.dumps({'op': 'overview_pointer', 'n': i}) + '\n').encode() for i in range(200)]
        received = bytearray()
        reader = threading.Thread(target=lambda: [received.extend(b.recv(65536)) for _ in iter(lambda: len(received) < sum(map(len, frames)), False)])
        reader.start()
        for f in frames: sup._send(f, a)
        big = b'x' * (1 << 20) + b'\n'  # larger than the socket buffer: partly non-blocking, the rest blocking
        frames.append(big)
        sup._send(big, a)
        reader.join(10)
        self.assertEqual(bytes(received), b''.join(frames))
        self.assertIsNone(sup.stopping_at)
        self.assertIsNone(a.gettimeout())  # blocking again afterwards

    def test_a_renderer_that_stops_reading_is_disconnected_after_the_same_bound(self):
        sup = self.supervisor()
        a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
        started = time.monotonic()
        sup._send(b'y' * (8 << 20), a)  # nobody reads
        elapsed = time.monotonic() - started
        self.assertIsNotNone(sup.stopping_at)
        self.assertGreaterEqual(elapsed, 0.2); self.assertLess(elapsed, 1.0)
        self.assertIsNone(a.gettimeout())

    def test_a_closed_renderer_is_a_disconnect(self):
        sup = self.supervisor()
        a, b = socket.socketpair(); self.addCleanup(a.close)
        b.close()
        sup._send(b'z\n', a)
        self.assertIsNotNone(sup.stopping_at)


class LazyImportTests(unittest.TestCase):
    def test_watchdog_module_leaves_multiprocessing_to_runs_with_a_guard(self):
        root = Path(__file__).resolve().parents[2]
        code = (f'import sys; sys.path.insert(0, {str(root)!r}); import tools.native_spike.supervisor; '
                'print("multiprocessing" in sys.modules)')
        r = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), 'False', r.stderr)


if __name__ == '__main__':
    unittest.main()
