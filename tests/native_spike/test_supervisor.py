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
