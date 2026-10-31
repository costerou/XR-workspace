"""Synthetic boundaries only: never connect a renderer to a live compositor."""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import __main__ as cli, supervisor as s
from tools.native_spike import hyprland as h
from test_hyprland import RawFixture, INSTANCE


class PresentationOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'consent.json'
        self.data = dict(schema=1, gate='P2-presentation-only', run_id='run', instance=INSTANCE,
                         output='DP-2', recovery_output='DP-1', allow=['test-pattern'],
                         capture=False, input=False, sdk=False, mode_switch=False)
        self.raw = RawFixture()
        self.raw.monitors.append(dict(self.raw.monitors[0], id=1, name='DP-2'))
        self.raw._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        self.peer, self.wayland = socket.socketpair()
        self.addCleanup(self.peer.close); self.addCleanup(self.wayland.close)

    def test_connects_only_lock_selected_wayland_and_validates_peer(self):
        self.assertTrue(hasattr(h.HyprlandClient, 'connect_wayland'), 'selected Wayland connector missing')
        root = Path(self.tmp.name)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try: server.bind(f'/proc/self/fd/{fd}/wayland-42')
        finally: os.close(fd)
        server.listen(1)
        client = h.HyprlandClient(INSTANCE, root)
        client._peer = self.raw._peer
        client._wayland_name = 'wayland-42'
        with patch.object(client, 'verified_session', return_value={'id': 'verified-session'}):
            with client.connect_wayland('verified-session') as conn:
                accepted, _ = server.accept(); accepted.close()
                self.assertGreaterEqual(conn.fileno(), 0)
            client._peer = (999999, os.getuid(), os.getgid(), 'wrong')
            with self.assertRaises(PermissionError): client.connect_wayland('verified-session')
            accepted, _ = server.accept(); accepted.close()

    def args(self):
        return ['present', '--instance', INSTANCE, '--output', 'DP-2', '--consent-file', str(self.path)]

    def consent(self):
        self.path.write_text(json.dumps(self.data)); self.path.chmod(0o600)

    def test_missing_consent_never_constructs_client(self):
        with patch.object(cli, 'HyprlandClient') as client:
            self.assertEqual(cli.main(self.args()), 2)
            client.assert_not_called()

    def test_cli_wires_real_launcher_with_zero_mutations_or_journal(self):
        self.consent()
        with patch.object(cli, 'HyprlandClient', return_value=self.raw), \
             patch.object(self.raw, 'connect_wayland', create=True, return_value=self.wayland), \
             patch('subprocess.Popen', return_value=Exited()) as spawn, \
             patch.object(cli.Journal, 'open', side_effect=AssertionError('journal forbidden')):
            self.assertEqual(cli.main(self.args()), 0)
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.raw.commands, [])
        self.assertEqual(self.wayland.fileno(), -1)

    def test_managed_consent_cannot_authorize_present(self):
        self.data.update(gate='P2', allow=['headless-outputs', 'test-pattern']); self.consent()
        with patch.object(cli, 'HyprlandClient') as client:
            self.assertEqual(cli.main(self.args()), 2); client.assert_not_called()

    def launch(self, spawn):
        self.assertTrue(hasattr(s, 'run_presentation_only'), 'presentation-only launcher missing')
        return s.run_presentation_only(self.raw, 'run', 'verified-session', 'DP-2', 'DP-1', self.wayland, popen=spawn)

    def test_parent_fd_closed_before_watchdog_and_child_exit_releases_peer(self):
        duplicates = []
        def spawn(argv, **kwargs):
            duplicates.append(socket.fromfd(self.wayland.fileno(), socket.AF_UNIX, socket.SOCK_STREAM))
            return Exited()
        def run(watchdog, heartbeat):
            self.assertEqual(self.wayland.fileno(), -1)
            duplicates.pop().close()
            self.peer.settimeout(0.2); self.assertEqual(self.peer.recv(1), b'')
            watchdog.tick(0)
            return watchdog.result
        with patch.object(s.Supervisor, 'run', run): self.assertEqual(self.launch(spawn), 0)

    def test_spawn_failure_closes_parent_fd(self):
        def fail(*a, **k): raise OSError('spawn failed')
        with self.assertRaises(OSError): self.launch(fail)
        self.assertEqual(self.wayland.fileno(), -1)

    def test_peer_mismatch_closes_fd_without_spawn(self):
        self.raw._peer = (999999, os.getuid(), os.getgid(), 'wrong')
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(PermissionError): self.launch(spawn)
            spawn.assert_not_called()
        self.assertEqual(self.wayland.fileno(), -1)

    def test_session_mismatch_closes_fd_without_spawn(self):
        self.raw.verified_session = lambda: {'id': 'changed'}
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(ValueError): self.launch(spawn)
            spawn.assert_not_called()
        self.assertEqual(self.wayland.fileno(), -1)

    def test_missing_recovery_display_refuses_spawn(self):
        self.raw.monitors[0]['dpmsStatus'] = False
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(ValueError): self.launch(spawn)
            spawn.assert_not_called()
        self.assertEqual(self.wayland.fileno(), -1)

    def test_startup_rejects_mirrored_missing_or_invalid_evidence(self):
        for index in (0, 1):
            for value in ('0', '1', None, True, 0, '', 'unknown', 'missing'):
                with self.subTest(index=index, value=value):
                    raw = RawFixture()
                    raw.monitors.append(dict(raw.monitors[0], id=1, name='DP-2'))
                    if value == 'missing': del raw.monitors[index]['mirrorOf']
                    else: raw.monitors[index]['mirrorOf'] = value
                    peer, wayland = socket.socketpair()
                    with peer, wayland, patch('subprocess.Popen') as spawn:
                        with self.assertRaises(ValueError):
                            s.run_presentation_only(raw, 'run', 'verified-session', 'DP-2', 'DP-1', wayland)
                        spawn.assert_not_called()
                        self.assertEqual(wayland.fileno(), -1)
                        peer.settimeout(0.2)
                        self.assertEqual(peer.recv(1), b'')

    def test_runtime_mirroring_transition_stops_child(self):
        import time
        for index in (0, 1):
            with self.subTest(index=index):
                # Worker first validates the independent pair, then sees mirroring.
                guard = RuntimePair(self.raw, index)
                child = RunningChild()
                parent, peer = socket.socketpair()
                with parent, peer:
                    started = time.monotonic()
                    s.Supervisor(child, lambda: None, guard=guard).run(parent)
                    self.assertGreater(time.monotonic() - started, 0.5)
                    self.assertLess(time.monotonic() - started, 1.5)
                self.assertTrue(child.stopped)
                self.raw.monitors[index]['mirrorOf'] = 'none'
        self.assertEqual(self.raw.commands, [])

    def test_recovery_loss_stops_running_child_without_mutations(self):
        class Child:
            stopped = False
            def poll(self): return -15 if self.stopped else None
            def terminate(self): self.stopped = True
            def kill(self): self.stopped = True
        child = Child()
        def spawn(argv, **kwargs):
            self.raw.monitors[0]['dpmsStatus'] = False
            control = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(control, socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return child
        import time
        started = time.monotonic()
        self.assertEqual(self.launch(spawn), -15)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(child.stopped)
        self.assertEqual(self.raw.commands, [])

    def test_real_child_exit_releases_wayland_connection(self):
        import subprocess
        import sys
        def spawn(argv, **kwargs):
            control = argv[argv.index('--supervised-fd') + 1]
            wayland = argv[argv.index('--wayland-fd') + 1]
            # A Python child only, not the renderer: inherited socket lifecycle.
            script = ('import socket,sys; c=socket.socket(fileno=int(sys.argv[1])); '
                      'w=socket.socket(fileno=int(sys.argv[2])); '
                      'c.recv(16384); w.close(); c.sendall(b"H"); c.close()')
            return subprocess.Popen([sys.executable, '-c', script, control, wayland], **kwargs)
        # EOF can race process exit; the watchdog may terminate the exact child.
        self.assertIn(self.launch(spawn), (0, -15))
        self.peer.settimeout(0.5)
        self.assertEqual(self.peer.recv(1), b'')
        self.assertEqual(self.wayland.fileno(), -1)

    def test_watchdog_error_reaps_child_before_propagating(self):
        class Child:
            stopped = False
            def poll(self): return -15 if self.stopped else None
            def terminate(self): self.stopped = True
            def kill(self): self.stopped = True
        child = Child()
        def spawn(argv, **kwargs):
            fd = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return child
        with patch.object(s.Supervisor, 'run', side_effect=OSError('control failed')):
            with self.assertRaises(OSError): self.launch(spawn)
        self.assertTrue(child.stopped)
        self.assertEqual(self.wayland.fileno(), -1)

    def test_interrupt_terminates_and_reaps_exact_child(self):
        class Child:
            stopped = False
            def poll(self): return -15 if self.stopped else None
            def terminate(self): self.stopped = True
            def kill(self): self.stopped = True
        child = Child()
        def spawn(argv, **kwargs):
            fd = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return child
        with patch.object(s.Supervisor, 'run', side_effect=KeyboardInterrupt):
            self.assertEqual(self.launch(spawn), -15)
        self.assertTrue(child.stopped)
        self.assertEqual(self.raw.commands, [])


class RuntimePair:
    def __init__(self, raw, index): self.raw, self.index = raw, index
    def __call__(self):
        s.validate_physical_pair(self.raw, 'verified-session', 'DP-2', 'DP-1')
        self.raw.monitors[self.index]['mirrorOf'] = str(1 - self.index)


class RunningChild:
    stopped = False
    def poll(self): return -15 if self.stopped else None
    def terminate(self): self.stopped = True
    def kill(self): self.stopped = True


class Exited:
    def poll(self): return 0
