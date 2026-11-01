"""Offline boundary tests: temporary sockets only, never the user's compositor."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.native_spike import hyprland as h
from tools.native_spike.geometry import DEFAULT_DESKTOP
from tools.native_spike.__main__ import main, load_consent
from tools.native_spike.journal import Journal, recover

INSTANCE = 'a' * 40 + '_123_456'

class SocketFixture:
    def __init__(self, root, reply):
        self.path = root / 'hypr' / INSTANCE
        self.path.mkdir(parents=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        try: self.server.bind(f'/proc/self/fd/{fd}/.socket.sock')
        finally: os.close(fd)
        self.server.listen(1)
        self.requests = []
        def serve():
            with self.server:
                conn, _ = self.server.accept()
                with conn:
                    self.requests.append(conn.recv(4096))
                    try:
                        if callable(reply): reply(conn)
                        else: conn.sendall(reply)
                    except (BrokenPipeError, ConnectionResetError): pass
        self.thread = threading.Thread(target=serve, daemon=True); self.thread.start()
    def join(self):
        self.thread.join(3)
        if self.thread.is_alive(): raise AssertionError('socket test hung')

class RawFixture:
    def __init__(self):
        self.commands = []
        self.monitors = [{'id': 0, 'name': 'DP-1', 'width': 1920, 'height': 1080,
            'x': 0, 'y': 0, 'scale': 1.0, 'transform': 0, 'refreshRate': 60.0, 'disabled': False,
            'dpmsStatus': True, 'mirrorOf': 'none', 'description': 'Synthetic physical test monitor'}]
    def verified_session(self): return {'id': 'verified-session'}
    def query(self, kind, argument=None):
        return {'monitors': self.monitors, 'workspaces': [{'id': 1, 'name': '1', 'monitor': 'DP-1'}],
                'getoption': {'option': 'input:follow_mouse', 'int': 1}, 'clients': []}[kind]
    def mutate(self, command): self.commands.append(command); return 'ok'
    def require_output_ownership(self): raise h.OwnershipBlocked('no incarnation proof')

class HyprlandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def test_raw_query_bytes_and_json(self):
        server = SocketFixture(self.root, b'[{"id":0}]')
        self.assertEqual(h.HyprlandClient(INSTANCE, self.root).query('monitors'), [{'id': 0}])
        server.join(); self.assertEqual(server.requests, [b'j/monitors all'])
    def test_allowlist_rejects_normalized_queries_without_connect(self):
        c = h.HyprlandClient(INSTANCE, self.root)
        for kind in ('session', 'outputs', 'settings', 'version', 'monitors;exec'):
            with self.assertRaises(ValueError): c.query(kind)
        for value in ('../evil', '', INSTANCE + '/x'):
            with self.assertRaises(ValueError): h.HyprlandClient(value, self.root)
    def test_mutation_validation_before_socket(self):
        c = h.HyprlandClient(INSTANCE, self.root)
        for command in ('dispatch exec evil', 'output remove DP-1', 'output remove XR-1', 'output create headless X\nexec evil',
                        'keyword input:follow_mouse 4', 'dispatch moveworkspacetomonitor 0 DP-1'):
            with self.assertRaises(ValueError): c.mutate(command)
    def test_response_cap(self):
        server = SocketFixture(self.root, b' ' * (1048576 + 1))
        with self.assertRaises(ValueError): h.HyprlandClient(INSTANCE, self.root).query('clients')
        server.join()
    def test_whole_response_deadline_not_reset_by_dripping_peer(self):
        import time
        def drip(conn):
            for _ in range(40):
                conn.sendall(b' '); time.sleep(0.1)
        server = SocketFixture(self.root, drip)
        before = time.monotonic()
        with self.assertRaises(TimeoutError): h.HyprlandClient(INSTANCE, self.root).query('clients')
        self.assertLess(time.monotonic() - before, 3.0)
        server.join()
    def test_malformed_monitor_cannot_become_physical_fallback(self):
        raw = RawFixture(); del raw.monitors[0]['dpmsStatus']
        with self.assertRaises((ValueError, KeyError)):
            h.RecoveryAdapter(raw, approved_physical='DP-1').query('outputs')
    def test_mirror_evidence_is_required_and_validated(self):
        for value in (None, True, 0, -1, '', 'NONE', '-1', '01', [], {}):
            with self.subTest(value=value):
                raw = RawFixture().monitors
                raw[0]['mirrorOf'] = value
                with self.assertRaises(ValueError): h.monitors(raw)
        raw = RawFixture().monitors
        del raw[0]['mirrorOf']
        with self.assertRaises(ValueError): h.monitors(raw)

    def test_mirrored_monitor_is_not_physical_recovery(self):
        raw = RawFixture(); raw.monitors[0]['mirrorOf'] = '1'
        self.assertFalse(h.RecoveryAdapter(raw, approved_physical='DP-1').query('outputs')[0]['physical'])

    def test_duplicate_keys_rejected(self):
        server = SocketFixture(self.root, b'{"x":1,"x":2}')
        with self.assertRaises(ValueError): h.HyprlandClient(INSTANCE, self.root).query('cursorpos')
        server.join()
    def test_ambiguous_session_refused(self):
        # A real same-UID Unix peer is insufficient: this is Python, not Hyprland.
        server = SocketFixture(self.root, b'[]')
        with self.assertRaises((ValueError, OSError)):
            h.HyprlandClient(INSTANCE, self.root).verified_session()
        server.join()
    def test_adapter_normalizes_without_invented_raw_queries(self):
        a = h.RecoveryAdapter(RawFixture(), approved_physical='DP-1')
        self.assertEqual(a.query('session'), {'id': 'verified-session'})
        self.assertEqual(a.query('workspaces'), [{'id': 1, 'output': 'DP-1'}])
        self.assertEqual(a.query('settings'), {'input:follow_mouse': 1})
        self.assertTrue(a.query('outputs')[0]['physical'])
    def test_legacy_managed_names_are_still_unprovable_orphans(self):
        raw = RawFixture(); raw.monitors[0]['name'] = 'XR-SPIKE-run-0'
        with self.assertRaises(h.OwnershipBlocked): h.RecoveryAdapter(raw, approved_physical='DP-1').query('outputs')
    def test_name_and_numeric_id_never_become_uuid_ownership(self):
        raw = RawFixture(); raw.monitors[0]['name'] = 'XR-1'
        a = h.RecoveryAdapter(raw, approved_physical='DP-1')
        with self.assertRaises(h.OwnershipBlocked): a.query('outputs')
        journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        journal.prepare({'kind': 'output', 'id': '12345678-1234-4234-8234-123456789abc',
                         'name': 'XR-1', 'index': 0, 'before': None})
        self.assertFalse(recover(journal, a).complete)
        self.assertEqual(raw.commands, [])
    def test_ok_without_output_is_failure(self):
        with self.assertRaises(ValueError): h.verify_output([], {'name': 'XR-1'})
    def test_creation_is_blocked_before_partial_mutation(self):
        raw = RawFixture(); journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        specs = h.plan_outputs(raw.monitors, [], 'run')
        with self.assertRaises(h.OwnershipBlocked): h.create_outputs(specs, journal, raw)
        self.assertEqual(raw.commands, []); self.assertEqual(journal.operations, [])
    def test_layout_does_not_overlap_scaled_existing_outputs(self):
        raw = RawFixture(); raw.monitors[0].update(x=-100, width=3840, scale=2.0)
        specs = h.plan_outputs(raw.monitors, [{'id': 1, 'name': '1'}], 'run')
        self.assertEqual([(s['width'], s['height'], s['refresh_millihz'], s['scale']) for s in specs],
                         [(1920, 1080, 120000, 1.0)] * 3)
        self.assertEqual([s['x'] for s in specs], [1820, 3740, 5660])
        self.assertEqual([s['name'] for s in specs], ['XR-1', 'XR-2', 'XR-3'])
        raw.monitors.append(dict(raw.monitors[0], id=1, name='XR-2', x=9000))
        with self.assertRaises(ValueError): h.plan_outputs(raw.monitors, [], 'run')  # stale/orphan name
        self.assertEqual(len({s['id'] for s in specs}), 3)
    def test_plan_default_unchanged(self):
        raw = RawFixture()
        specs = h.plan_outputs(raw.monitors, [], 'run')
        explicit = h.plan_outputs(raw.monitors, [], 'run', [dict(DEFAULT_DESKTOP)] * 3)
        for s in specs + explicit: s.pop('id')
        self.assertEqual(specs, explicit)
        self.assertEqual(specs, [{'name': 'XR-%d' % (i + 1), 'index': i, 'width': 1920, 'height': 1080,
                                  'refresh_millihz': 120000, 'scale': 1.0, 'x': 1920 * (i + 1), 'y': 0}
                                 for i in range(3)])
    def test_plan_mixed_positions(self):
        raw = RawFixture(); raw.monitors[0].update(x=0, width=2560, scale=1.0)
        ds = [{'width': 1920, 'height': 1080, 'scale': 1.0}, {'width': 3840, 'height': 1080, 'scale': 1.0},
              {'width': 3440, 'height': 1440, 'scale': 1.25}]
        specs = h.plan_outputs(raw.monitors, [], 'run', ds)
        self.assertEqual([s['x'] for s in specs], [2560, 4480, 8320])
        self.assertEqual([(s['width'], s['height'], s['scale']) for s in specs],
                         [(1920, 1080, 1.0), (3840, 1080, 1.0), (3440, 1440, 1.25)])
    def test_plan_rejects_wrong_count(self):
        d = {'width': 1920, 'height': 1080, 'scale': 1.0}
        for n in (2, 4):
            with self.assertRaises(ValueError): h.plan_outputs(RawFixture().monitors, [], 'run', [d] * n)
    def test_rotated_output_reserves_transformed_logical_rectangle(self):
        raw = RawFixture(); raw.monitors[0].update(width=1080, height=1920, transform=1)
        self.assertEqual(h.plan_outputs(raw.monitors, [], 'run')[0]['x'], 1920)
    def test_readback_mode_must_match_exactly(self):
        spec = h.plan_outputs(RawFixture().monitors, [], 'run')[0]
        monitor = dict(spec, refreshRate=119.88)
        with self.assertRaises(ValueError): h.verify_output([monitor], spec)
    def test_recovery_cannot_prove_absence_from_empty_snapshot(self):
        raw = RawFixture(); raw.monitors = []
        adapter = h.RecoveryAdapter(raw, approved_physical='DP-1')
        journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        journal.prepare({'kind': 'output', 'id': '12345678-1234-4234-8234-123456789abc',
                         'name': 'XR-1', 'index': 0, 'before': None})
        self.assertFalse(recover(journal, adapter).complete)
        self.assertEqual(raw.commands, [])
    def test_supervisor_wiring_sends_start_and_recovers_after_child_exit(self):
        from tools.native_spike.supervisor import run_presentation
        from tools.native_spike.ipc import decode_control
        raw = RawFixture()
        # Synthetic qualified adapter ONLY for exercising launcher wiring; this is
        # not an ownership proof and is never provided by the production adapter.
        raw.require_output_ownership = lambda: None
        raw._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        adapter = h.RecoveryAdapter(raw, approved_physical='DP-1')
        journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        parent, wayland = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(wayland.close)
        observed = {}; finished = threading.Event()
        class Child:
            def poll(self): return 0 if finished.is_set() else None
            def terminate(self): finished.set()
            def kill(self): finished.set()
        def spawn(argv, **kwargs):
            observed['argv'] = argv; observed['kwargs'] = kwargs
            fd = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            def child():
                with endpoint:
                    raw_frame = bytearray()
                    while not raw_frame.endswith(b'\n'): raw_frame.extend(endpoint.recv(1))
                    observed['start'] = decode_control(bytes(raw_frame))
                    endpoint.sendall(b'H')
                finished.set()
            worker = threading.Thread(target=child); worker.start(); self.addCleanup(worker.join, 3)
            return Child()
        result = run_presentation(journal, adapter, 'DP-1', wayland, popen=spawn)
        self.assertTrue(result.complete)
        self.assertEqual(observed['start'], {'schema': 1, 'request_id': 1, 'run_id': 'run',
                          'session': 'verified-session', 'op': 'start', 'payload': {}})
        self.assertIn('DP-1', observed['argv'])
        self.assertEqual(observed['kwargs']['shell'], False)
        self.assertTrue(observed['kwargs']['close_fds'])
    def test_launcher_does_not_retry_recovery_storage_failure(self):
        from tools.native_spike.supervisor import run_presentation
        raw = RawFixture(); raw.require_output_ownership = lambda: None
        raw._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        adapter = h.RecoveryAdapter(raw, approved_physical='DP-1')
        journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        parent, wayland = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(wayland.close)
        class Exited:
            def poll(self): return 0
        def spawn(argv, **kwargs):
            fd = int(argv[argv.index('--supervised-fd') + 1])
            endpoint = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return Exited()
        with patch('tools.native_spike.journal.recover', side_effect=OSError('fsync failed')) as recovery:
            with self.assertRaises(OSError): run_presentation(journal, adapter, 'DP-1', wayland, popen=spawn)
            self.assertEqual(recovery.call_count, 1)
    def test_launcher_refuses_unproven_ownership_before_spawn(self):
        from tools.native_spike.supervisor import run_presentation
        raw = RawFixture(); adapter = h.RecoveryAdapter(raw, approved_physical='DP-1')
        journal = Journal.open(self.root / 'journal.json', 'run', 'verified-session')
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(h.OwnershipBlocked): run_presentation(journal, adapter, 'DP-1', None, popen=spawn)
            spawn.assert_not_called()
    def test_permission_missing_has_no_side_effect(self):
        with patch('tools.native_spike.__main__.HyprlandClient') as client:
            self.assertEqual(main(['run', '--instance', INSTANCE, '--output', 'DP-1',
                                   '--consent-file', str(self.root / 'missing')]), 2)
            client.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
    def test_consent_exact_scope_and_private_file(self):
        data = {'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE,
                'output': 'DP-1', 'recovery_output': 'DP-2', 'allow': ['headless-outputs', 'test-pattern'],
                'capture': False, 'input': False, 'sdk': False, 'mode_switch': False}
        path = self.root / 'consent.json'; path.write_text(json.dumps(data)); path.chmod(0o600)
        self.assertEqual(load_consent(path, INSTANCE, 'DP-1'), data)
        for key, value in [('schema', True), ('capture', True), ('instance', 'wrong'), ('allow', ['test-pattern'])]:
            altered = dict(data); altered[key] = value; path.write_text(json.dumps(altered))
            with self.assertRaises((ValueError, PermissionError)): load_consent(path, INSTANCE, 'DP-1')
        path.write_text(json.dumps(data)); path.chmod(0o644)
        with self.assertRaises(PermissionError): load_consent(path, INSTANCE, 'DP-1')

class PluginLifecycleTests(unittest.TestCase):
    """Plugin/mode lifecycle over a fake transport; the wire strings are confirmed in the VM (Task 12)."""
    def setUp(self):
        from tools.xr_controller.install import install
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'build').mkdir()
        (root / 'build' / 'xr-native-spike').write_bytes(b'renderer')
        (root / 'build' / 'libxr-ownership.so').write_bytes(b'plugin')
        self.data = root / 'data'; self.data.mkdir()
        self.installed = install(root / 'build', self.data)
        self.plugin = self.installed['plugin']['path']
        self.client = h.HyprlandClient(INSTANCE, root)
        self.requests, self.replies = [], {}
        def fake(request, timeout=h.DEADLINE):
            self.requests.append(request)
            reply = self.replies[request.split(' ')[0] if request.startswith('/eval') else request]
            return reply.pop(0) if isinstance(reply, list) else reply
        self.client._request = fake

    def test_plugin_path_must_be_installed(self):
        bad = [self.installed['renderer']['path'], self.plugin + ' ', self.plugin + '\n', ' ' + self.plugin,
               self.plugin.replace('/bin/', '/bin/../bin/'), 'libxr-ownership.so', str(Path(self.plugin).parent) + '/x/libxr-ownership.so',
               '/tmp/libxr-ownership.so', None, b'/x', '']
        for path in bad:
            for method in (self.client.plugin_load, self.client.plugin_unload):
                with self.subTest(path=path, method=method.__name__), self.assertRaises(ValueError):
                    method(path, data_home=self.data)
        with self.assertRaises(ValueError): self.client.plugin_load(self.plugin, data_home=Path(self.tmp.name) / 'nodata')
        self.assertEqual(self.requests, [])

    def test_plugin_load_unload_wire(self):
        self.replies = {'/plugin load ' + self.plugin: b'ok', '/plugin unload ' + self.plugin: b'ok\n'}
        self.client.plugin_load(self.plugin, data_home=self.data)
        self.client.plugin_unload(self.plugin, data_home=self.data)
        self.assertEqual(self.requests, ['/plugin load ' + self.plugin, '/plugin unload ' + self.plugin])
        self.replies = {'/plugin load ' + self.plugin: b'error: could not load'}
        with self.assertRaises(ValueError): self.client.plugin_load(self.plugin, data_home=self.data)

    def test_plugins_loaded(self):
        entry = {'name': 'xr-ownership', 'author': 'a', 'handle': '0x1', 'version': '1', 'description': 'd'}
        self.replies = {'j/plugin list': [b'[]', json.dumps([entry]).encode(), b'{}', b'[{"name": 3}]', b'no plugins loaded']}
        self.assertEqual(self.client.plugins_loaded(), [])
        self.assertEqual(self.client.plugins_loaded(), ['xr-ownership'])
        for _ in range(3):
            with self.assertRaises(ValueError): self.client.plugins_loaded()

    def monitor(self, **changes):
        m = dict(RawFixture().monitors[0], name='DP-2', x=1920, y=0, scale=1.0, width=1920, height=1080, refreshRate=60.0)
        m.update(changes); return m

    def test_apply_session_mode(self):
        before, after = [self.monitor()], [self.monitor(height=1200, refreshRate=119.909)]
        self.replies = {'j/monitors all': [json.dumps(before).encode(), json.dumps(after).encode()], '/eval': b'ok'}
        mode = self.client.apply_session_mode('DP-2')
        self.assertEqual(mode, {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.0})
        self.assertEqual(self.requests[1], '/eval hl.monitor({ output = "DP-2", mode = "1920x1200@120", position = "1920x0", scale = 1 })')

    def test_apply_session_mode_requires_readback(self):
        for after in ([self.monitor()], [self.monitor(height=1200, refreshRate=60.0)], []):
            with self.subTest(after=after):
                self.requests.clear()
                self.replies = {'j/monitors all': [json.dumps([self.monitor()]).encode(), json.dumps(after).encode()], '/eval': b'ok'}
                with self.assertRaises(ValueError): self.client.apply_session_mode('DP-2')
        self.requests.clear()
        self.replies = {'j/monitors all': [json.dumps([self.monitor(name='XR-1')]).encode()], '/eval': b'ok'}
        with self.assertRaises(ValueError): self.client.apply_session_mode('XR-1')
        self.assertEqual(self.requests, ['j/monitors all'])


if __name__ == '__main__': unittest.main()
