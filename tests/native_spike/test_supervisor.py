"""Offline watchdog regressions: harmless subprocesses and local socketpairs."""
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools.native_spike.supervisor import Supervisor


class Guard:
    def __init__(self, path, mode): self.path, self.mode = path, mode
    def __call__(self):
        Path(self.path).write_text(str(os.getpid()))
        if self.mode == 'fail': raise ValueError('synthetic invalid outputs')
        if self.mode == 'crash': os._exit(1)
        if self.mode == 'slow': time.sleep(4)
        if self.mode == 'hung': time.sleep(6)


class SupervisorGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'guard.pid')
        self.before = {p.pid for p in multiprocessing.active_children()}

    def child(self):
        # Ignore TERM so the watchdog must also escalate and reap.
        child = subprocess.Popen([sys.executable, '-c',
            'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'],
            stdout=subprocess.PIPE, text=True)
        assert child.stdout is not None
        self.addCleanup(child.stdout.close)
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        self.assertEqual(child.stdout.readline(), 'ready\n')
        return child

    def assert_clean(self):
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, self.before)
        if Path(self.path).exists():
            pid = int(Path(self.path).read_text())
            self.assertNotEqual(pid, os.getpid(), 'guard must not execute in watchdog process')
            with self.assertRaises(ChildProcessError): os.waitpid(pid, os.WNOHANG)

    def test_slow_or_hung_guard_does_not_suspend_heartbeat_or_reaping(self):
        for mode in ('slow', 'hung'):
            with self.subTest(mode=mode):
                child = self.child()
                parent, peer = socket.socketpair()
                with parent, peer:
                    # Queued heartbeat must not be timestamped fresh after a blocking guard.
                    peer.sendall(b'H')
                    watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, mode), grace=0.1)
                    started = time.monotonic()
                    result = watchdog.run(parent)
                    elapsed = time.monotonic() - started
                self.assertEqual(result, -signal.SIGKILL)
                self.assertGreaterEqual(elapsed, 2)
                self.assertLess(elapsed, 2.8)
                self.assert_clean()

    def test_guard_failure_and_crash_reap_child_and_worker(self):
        for mode in ('fail', 'crash'):
            with self.subTest(mode=mode):
                child = self.child()
                parent, peer = socket.socketpair()
                with parent, peer:
                    watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, mode), grace=0.1)
                    started = time.monotonic()
                    self.assertEqual(watchdog.run(parent), -signal.SIGKILL)
                    self.assertLess(time.monotonic() - started, 1)
                self.assert_clean()

    def test_a_failed_guard_says_so(self):
        for mode, reason in (('fail', 'guard-failed'), ('crash', 'guard-failed')):
            with self.subTest(mode=mode):
                child = self.child()
                parent, peer = socket.socketpair()
                with parent, peer:
                    watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, mode), grace=0.1)
                    watchdog.run(parent)
                self.assertEqual(watchdog.stop_reason, reason)

    def test_a_hung_guard_says_so(self):
        child = self.child()
        parent, peer = socket.socketpair()
        with parent, peer:
            watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, 'hung'), grace=0.1)
            watchdog.heartbeat(time.monotonic() + 3600)  # the heartbeat stays fresh; only the guard is late
            watchdog._guard_deadline = 0.0
            watchdog._guard_worker, watchdog._guard_receiver, watchdog._guard_ready = _Alive(), object(), None
            watchdog._check_guard(1.0)
            watchdog._guard_worker = watchdog._guard_receiver = None
        self.assertEqual(watchdog.stop_reason, 'guard-timeout')

    def test_launcher_interrupt_and_error_reap_renderer_and_started_worker(self):
        from tools.native_spike import supervisor as s
        from test_hyprland import RawFixture
        for failure in (KeyboardInterrupt, OSError):
            with self.subTest(failure=failure):
                Path(self.path).unlink(missing_ok=True)
                child = self.child()
                raw = RawFixture()
                raw.monitors.append(dict(raw.monitors[0], id=1, name='DP-2'))
                raw._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
                parent, wayland = socket.socketpair()
                endpoints = []
                def spawn(argv, **kwargs):
                    fd = int(argv[argv.index('--supervised-fd') + 1])
                    endpoints.append(socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM))
                    return child
                def interrupted_select(*args):
                    until = time.monotonic() + 1
                    while not Path(self.path).exists() and time.monotonic() < until:
                        time.sleep(0.01)
                    self.assertTrue(Path(self.path).exists(), 'worker must be running before fault')
                    raise failure('synthetic shutdown')
                with parent, wayland, patch.object(s, 'PhysicalPairGuard', return_value=Guard(self.path, 'hung')), \
                     patch('selectors.EpollSelector.select', side_effect=interrupted_select):
                    try:
                        if failure is KeyboardInterrupt:
                            self.assertEqual(s.run_presentation_only(raw, 'run', 'verified-session', 'DP-2', 'DP-1', wayland, popen=spawn), -signal.SIGKILL)
                        else:
                            with self.assertRaises(OSError):
                                s.run_presentation_only(raw, 'run', 'verified-session', 'DP-2', 'DP-1', wayland, popen=spawn)
                        self.assertEqual(child.poll(), -signal.SIGKILL)
                        parent.settimeout(0.2)
                        self.assertEqual(parent.recv(1), b'')
                        self.assertEqual(wayland.fileno(), -1)
                    finally:
                        for endpoint in endpoints: endpoint.close()
                self.assert_clean()

    def test_launch_uses_given_renderer(self):
        from tools.native_spike import supervisor as s
        from test_hyprland import RawFixture
        raw = RawFixture()
        raw._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        seen = []
        def spawn(argv, **kwargs):
            seen.append(argv[0])
            status_fd=int(argv[argv.index('--status-fd')+1])
            self.assertIn(status_fd,kwargs['pass_fds'])
            self.assertNotEqual(status_fd,int(argv[argv.index('--supervised-fd')+1]))
            with socket.socket(fileno=os.dup(status_fd)) as status:
                self.assertEqual(status.type,socket.SOCK_DGRAM)
            raise OSError('stop before running')
        for renderer, expected in ((Path('/pinned/xr-native-spike'), '/pinned/xr-native-spike'), (None, None)):
            parent, wayland = socket.socketpair()
            with parent, wayland, self.assertRaises(OSError):
                s._launch(raw, 'run', 'verified-session', 'DP-2', wayland, None, popen=spawn, renderer=renderer)
            if expected: self.assertEqual(seen[-1], expected)
            else: self.assertTrue(seen[-1].endswith('build/native-spike/xr-native-spike'))

    def test_shutdown_and_selector_error_clean_worker(self):
        for error in (False, True):
            with self.subTest(error=error):
                child = self.child()
                parent, peer = socket.socketpair()
                with parent, peer:
                    watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, 'hung'), grace=0.1)
                    if error:
                        with patch('selectors.EpollSelector.select', side_effect=OSError('synthetic selector failure')):
                            with self.assertRaises(OSError): watchdog.run(parent)
                    else:
                        peer.close()
                        watchdog.run(parent)
                self.assert_clean()

    def test_hung_guard_fails_closed_even_with_fresh_heartbeats(self):
        child = self.child()
        parent, peer = socket.socketpair()
        stop = threading.Event()
        def feed():
            while not stop.wait(0.05):
                try: peer.sendall(b'H')
                except OSError: break
        thread = threading.Thread(target=feed)
        with parent, peer:
            thread.start()
            try:
                watchdog = Supervisor(child, lambda: child.poll(), guard=Guard(self.path, 'hung'), grace=0.1)
                started = time.monotonic()
                self.assertEqual(watchdog.run(parent), -signal.SIGKILL)
                self.assertLess(time.monotonic() - started, 5)
            finally:
                stop.set(); thread.join(1)
        self.assert_clean()

class _Alive:
    def is_alive(self): return True
    def join(self): pass
    def close(self): pass
    pid = None


class _Renderer:
    """A child that is up until told otherwise."""
    def __init__(self, code=None): self.code = code
    def poll(self): return self.code
    def terminate(self): self.code = 0
    def kill(self): self.code = -signal.SIGKILL


class StopReasonTests(unittest.TestCase):
    """Why the supervisor ended the renderer. Without it a watchdog's kill looks like any other exit (incident_detect)."""
    def test_no_heartbeat_for_two_seconds(self):
        w = Supervisor(_Renderer(), lambda: 0, now=0)
        w.tick(2.0)
        self.assertEqual(w.stop_reason, 'heartbeat-timeout')

    def test_a_healthy_run_has_no_reason(self):
        w = Supervisor(_Renderer(), lambda: 0, now=0)
        w.tick(1.0)
        self.assertIsNone(w.stop_reason)

    def test_the_first_reason_stands(self):
        w = Supervisor(_Renderer(), lambda: 0, now=0)
        w.disconnect(0, 'interrupted')
        w.disconnect(0, 'heartbeat-timeout')
        self.assertEqual(w.stop_reason, 'interrupted')

    def test_a_renderer_already_gone_was_not_stopped_by_anyone(self):
        # A send to a renderer that died by itself fails too: that is its exit code's story, not a watchdog's.
        w = Supervisor(_Renderer(code=1), lambda: 1, now=0)
        w.disconnect(0, 'send-failed')
        self.assertIsNone(w.stop_reason)

    def test_a_disconnect_with_no_reason_still_works(self):
        w = Supervisor(_Renderer(), lambda: 0, now=0)
        w.disconnect(0)
        self.assertIsNone(w.stop_reason)

    def run_on(self, data):
        parent, peer = socket.socketpair()
        w = Supervisor(_Renderer(), lambda: 'done')
        if data: peer.sendall(data)
        peer.shutdown(socket.SHUT_WR)
        with parent, peer: w.run(parent)
        return w.stop_reason

    def test_a_bad_heartbeat(self):
        self.assertEqual(self.run_on(b'HHX'), 'heartbeat-invalid')

    def test_the_renderer_closing_its_end(self):
        self.assertEqual(self.run_on(b''), 'renderer-closed')

    def test_a_failed_send(self):
        parent, peer = socket.socketpair()
        peer.close()
        w = Supervisor(_Renderer(), lambda: 0, now=0)
        with parent: w._send(b'x' * 8, parent)
        self.assertEqual(w.stop_reason, 'send-failed')

    def launch(self, child, report):
        from tools.native_spike import supervisor as s
        _, wayland = socket.socketpair()
        class Client: _peer = (*__import__('struct').unpack('3i', wayland.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)), 'x')
        def spawn(argv, **kw):
            self.addCleanup(os.close, os.dup(kw['pass_fds'][0]))  # a renderer holds its end of the control socket
            return child
        return s._launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=spawn, report=report)

    def test_launch_hands_the_reason_back(self):
        report = {}
        with patch.object(Supervisor, 'run', lambda w, *a, **k: (w.disconnect(0, 'heartbeat-timeout'), 0)[1]):
            self.launch(_Renderer(), report)
        self.assertEqual(report['stop_reason'], 'heartbeat-timeout')

    def test_an_interrupt_is_named(self):
        report = {}
        def interrupted(w, *a, **k): raise KeyboardInterrupt
        with patch.object(Supervisor, 'run', interrupted):
            self.launch(_Renderer(), report)
        self.assertEqual(report['stop_reason'], 'interrupted')

    def test_a_run_ends_with_the_reason_in_its_result(self):
        from tools.native_spike import managed
        seen = {}
        def launch(*a, report=None, **k):
            report['stop_reason'] = 'guard-failed'
            return -15
        class Wayland:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        class Client:
            instance = 'i'
            def connect_wayland(self, session): return Wayland()
        class Journal:
            run_id, session, operations, broker, path = 'run', 's', [], None, 'j'
            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
            def bind_broker(self, context): pass
        class Recovered: complete, remaining, reasons = True, [], []
        class Broker: epoch, fallback = 1, 'DP-1'
        with patch.object(managed, '_launch', launch), patch.object(managed, 'create_outputs', lambda *a: None), \
                patch.object(managed, 'recover_restoring', lambda *a: Recovered()):
            seen = managed._run_owned(Client(), Journal(), Broker(), 'DP-2', 'DP-1', [], guard=lambda: None)
        self.assertEqual(seen['stop_reason'], 'guard-failed')
        self.assertEqual(seen['renderer_returncode'], -15)


class StatusTests(unittest.TestCase):
    def test_status_lines_kept_latest(self):
        from tools.native_spike.supervisor import Supervisor
        w = Supervisor(None, None, now=0)
        w.status_bytes(b'{"presented_hz":119.8}\n{"miss')
        self.assertEqual(w.status, {'presented_hz':119.8})
        w.status_bytes(b'ed":3}\n')
        self.assertEqual(w.status, {'missed':3})
        w.status_bytes(b'{"presented_hz":NaN}\n{"fake":120}\n')
        self.assertEqual(w.status, {'missed':3})
        w.status_bytes(b'x'*17000+b'\n{"missed":4}\n')
        self.assertEqual(w.status, {'missed':4})

    def test_the_first_status_with_a_frame_marks_the_start_up_timeline(self):
        from tools.native_spike import startup
        from tools.native_spike.supervisor import Supervisor
        startup.reset(); self.addCleanup(startup.reset)
        w = Supervisor(None, None, now=0)
        w.status_bytes(b'{"presented_hz":NaN}\n{"tracking":true}\n')  # refused, then no frame yet
        self.assertNotIn('first_status', startup.marks())
        w.status_bytes(b'{"missed":0}\n')
        first = startup.marks()['first_status']
        w.status_bytes(b'{"missed":0,"presented_hz":120}\n')
        self.assertEqual(startup.marks()['first_status'], first)  # the first one only

    def test_tracking_and_capture_failures_are_measured_values(self):
        w = Supervisor(None, None, now=0)
        w.status_bytes(b'{"presented_hz":120,"tracking":false,"capture_failure_run":2}\n')
        self.assertEqual(w.status, {'presented_hz': 120, 'tracking': False, 'capture_failure_run': 2})

    def test_their_types_are_checked(self):
        for bad in (b'{"tracking":1}\n', b'{"tracking":"yes"}\n', b'{"capture_failure_run":-1}\n', b'{"capture_failure_run":1.5}\n', b'{"capture_failure_run":true}\n'):
            w = Supervisor(None, None, now=0)
            w.status_bytes(bad)
            self.assertEqual(w.status, {}, bad)

    def test_heartbeat_channel_still_H_only(self):
        class Child:
            done=False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done=True
            def kill(self): self.done=True
        for data in (b'H', b'{"missed":1}\n', b'HHX'):
            with self.subTest(data=data):
                parent,peer=socket.socketpair()
                child=Child(); w=Supervisor(child,lambda:'done')
                # A valid heartbeat updates its clock; invalid bytes disconnect immediately.
                peer.sendall(data); peer.shutdown(socket.SHUT_WR)
                try:
                    before=w.last_heartbeat
                    self.assertEqual(w.run(parent),'done')
                    if data==b'H': self.assertGreater(w.last_heartbeat,before)
                    else: self.assertEqual(w.last_heartbeat,before)
                finally: parent.close(); peer.close()

    def test_status_socket_is_separate_and_does_not_refresh_heartbeat(self):
        class Child:
            done=False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done=True
            def kill(self): self.done=True
        parent,peer=socket.socketpair()
        status,writer=socket.socketpair(type=socket.SOCK_DGRAM)
        child=Child(); w=Supervisor(child,lambda:'done')
        before=w.last_heartbeat
        writer.send(b'{"missed":3}\n')
        def finish():
            time.sleep(.1); child.done=True
        thread=threading.Thread(target=finish); thread.start()
        try:
            self.assertEqual(w.run(parent,status_socket=status),'done')
            self.assertEqual(w.status,{'missed':3})
            self.assertEqual(w.last_heartbeat,before)
        finally:
            thread.join(); parent.close(); peer.close(); status.close(); writer.close()
