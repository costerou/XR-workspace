"""Offline production orchestration; synthetic transport, real journal files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike.journal import Journal
from tools.native_spike.ownership import OwnershipBrokerClient
from tools.native_spike.hyprland import create_outputs, plan_outputs, OwnershipBlocked
from test_ownership import Transport

INSTANCE = 'a' * 40 + '_123_456'


class ManagedTransport(Transport):
    instance = INSTANCE

    def __init__(self, path):
        super().__init__(path)
        self.runtime_dir = path.parents[2]

    def query(self, kind):
        from test_hyprland import RawFixture
        snapshot = RawFixture().monitors
        snapshot.append(dict(snapshot[0], id=1, name='DP-2', x=1920))
        return snapshot if kind == 'monitors' else []


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.path.parent.mkdir(parents=True, mode=0o700)
        self.path.parent.parent.chmod(0o700)
        self.journal = Journal.open(self.path, 'run', 'session')
        self.transport = Transport(self.path)
        self.transport.runtime_dir = Path(self.tmp.name)
        self.broker = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')

    def test_restart_context_is_durable_before_first_create(self):
        self.broker.require_output_ownership()
        context = {'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1',
                   'epoch': self.broker.epoch, 'fallback': self.broker.fallback}
        self.assertTrue(hasattr(self.journal, 'bind_broker'), 'missing durable broker binding')
        self.journal.bind_broker(context)
        original = self.transport._request
        def checked(wire):
            if json.loads(wire[10:])['op'] == 'create':
                reopened = Journal.open(self.path, 'run', 'session')
                self.assertEqual(reopened.broker, context)
            return original(wire)
        self.transport._request = checked
        create_outputs(plan_outputs([], [], 'run'), self.journal, self.broker)
        self.assertEqual(Journal.open(self.path, 'run', 'session').broker, context)
        with self.assertRaises(ValueError):
            self.journal.bind_broker(dict(context, epoch='other'))

    def test_restart_uses_saved_epoch_and_handle_without_new_hello(self):
        from tools.native_spike.journal import recover
        self.test_restart_context_is_durable_before_first_create()
        self.transport.instance = INSTANCE
        self.assertTrue(hasattr(OwnershipBrokerClient, 'from_journal'), 'missing pinned restart')
        reopened = Journal.open(self.path, 'run', 'session')
        restarted = OwnershipBrokerClient.from_journal(self.transport, reopened)
        self.transport.epoch = 'replacement-epoch'
        before = self.path.read_bytes()
        self.transport.events.clear()
        self.assertFalse(recover(reopened, restarted).complete)
        self.assertNotIn('hello', self.transport.events)
        self.assertNotIn('evacuate_remove', self.transport.events)
        self.assertEqual(before, self.path.read_bytes())
        self.transport.epoch = 'epoch-one'
        self.assertTrue(recover(reopened, restarted).complete)
        self.assertTrue(recover(reopened, restarted).complete)

    def test_cli_creates_supervises_and_recovers_with_durable_authority(self):
        import socket
        from tools.native_spike.__main__ import main
        from test_hyprland import RawFixture
        self.path = Path(self.tmp.name) / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.transport.path = self.path
        self.transport.instance = INSTANCE
        snapshot = RawFixture().monitors
        snapshot.append(dict(snapshot[0], id=1, name='DP-2', x=1920))
        self.transport.query = lambda kind: snapshot if kind == 'monitors' else []
        self.transport.require_output_ownership = RawFixture().require_output_ownership
        self.transport._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        peer, wayland = socket.socketpair()
        self.addCleanup(peer.close); self.addCleanup(wayland.close)
        self.transport.connect_wayland = lambda session: wayland
        consent = {'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE,
                   'output': 'DP-2', 'recovery_output': 'DP-1',
                   'allow': ['headless-outputs', 'test-pattern'],
                   'capture': False, 'input': False, 'sdk': False, 'mode_switch': False}
        consent_path = Path(self.tmp.name) / 'consent.json'
        consent_path.write_text(json.dumps(consent)); consent_path.chmod(0o600)
        class Child:
            def poll(self): return 0
        def spawn(argv, **kwargs):
            saved = Journal.open(self.path, 'run', 'session')
            self.assertEqual(saved.broker['epoch'], 'epoch-one')
            self.assertEqual([r['state'] for r in saved.operations], ['confirmed'] * 3)
            endpoint = socket.fromfd(int(argv[argv.index('--supervised-fd') + 1]), socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return Child()
        with patch('tools.native_spike.__main__.HyprlandClient', return_value=self.transport), \
             patch.dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name), patch('subprocess.Popen', side_effect=spawn):
            result = main(['run', '--instance', INSTANCE, '--output', 'DP-2',
                           '--consent-file', str(consent_path)])
        self.assertEqual(result, 0, 'production managed CLI still blocked')
        saved = Journal.open(self.path, 'run', 'session')
        self.assertEqual([r['state'] for r in saved.operations], ['done'] * 3)
        self.assertEqual(wayland.fileno(), -1)

    def test_cli_restart_recovery_requires_explicit_saved_scope(self):
        from tools.native_spike.__main__ import main
        from test_hyprland import RawFixture
        self.test_restart_context_is_durable_before_first_create()
        self.transport.instance = INSTANCE
        snapshot = RawFixture().monitors
        snapshot.append(dict(snapshot[0], id=1, name='DP-2', x=1920))
        self.transport.query = lambda kind: snapshot
        data = {'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE,
                'output': 'DP-2', 'recovery_output': 'DP-1',
                'allow': ['headless-outputs', 'test-pattern'],
                'capture': False, 'input': False, 'sdk': False, 'mode_switch': False}
        path = self.path.with_name('consent.json')
        path.write_text(json.dumps(data)); path.chmod(0o600)
        with patch('tools.native_spike.__main__.HyprlandClient', return_value=self.transport), \
             patch.dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name):
            try:
                code = main(['recover', '--journal', str(self.path), '--instance', INSTANCE,
                             '--output', 'DP-2', '--consent-file', str(path)])
            except SystemExit:
                self.fail('recover CLI has no explicit consent/instance workflow')
        self.assertEqual(code, 0)
        self.assertEqual([r['state'] for r in Journal.open(self.path, 'run', 'session').operations], ['done'] * 3)

    def test_active_guard_rejects_authority_loss_and_mirrored_recovery(self):
        from tools.native_spike import managed
        from test_hyprland import RawFixture
        self.assertTrue(hasattr(managed, 'ManagedGuard'), 'no active broker authority watchdog')
        specs = plan_outputs([], [], 'run')
        create_outputs(specs, self.journal, self.broker)
        snapshot = RawFixture().monitors
        snapshot.append(dict(snapshot[0], id=1, name='DP-2', x=1920))
        self.transport.query = lambda kind: snapshot
        guard = managed.ManagedGuard(self.broker, 'DP-2', specs)
        guard()
        self.transport.epoch = 'replacement'
        with self.assertRaises(ValueError): guard()
        self.transport.epoch = 'epoch-one'
        snapshot[0]['mirrorOf'] = '1'
        with self.assertRaises(ValueError): guard()

    def test_copied_journal_cannot_bypass_active_canonical_owner(self):
        from tools.native_spike.journal import managed_owner
        from tools.native_spike.managed import recover_managed
        runtime = Path(self.tmp.name)
        canonical = runtime / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.path = canonical
        self.journal = Journal.open(canonical, 'run', 'session')
        self.transport = ManagedTransport(canonical)
        self.transport.runtime_dir = runtime
        self.broker = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')
        self.test_restart_context_is_durable_before_first_create()
        copied = canonical.with_name('copied.json')
        copied.write_bytes(canonical.read_bytes()); copied.chmod(0o600)
        before = canonical.read_bytes()
        consent = dict(instance=INSTANCE, run_id='run', output='DP-2', recovery_output='DP-1')
        self.transport.events.clear()
        with managed_owner(canonical):
            with self.assertRaises(BlockingIOError):
                with managed_owner(canonical):
                    self.fail('canonical lifetime owner was not held')
            with self.assertRaises((ValueError, OSError)):
                recover_managed(self.transport, consent, copied)
            self.assertEqual(self.transport.events, [], 'refusal must precede broker requests')
            self.assertTrue(all(r['state'] == 'live' for r in self.transport.registry.values()))
            self.assertEqual(canonical.read_bytes(), before)
            self.assertEqual(copied.read_bytes(), before)
        # Copies are never authoritative, even after the original owner exits.
        with self.assertRaises((ValueError, OSError)):
            recover_managed(self.transport, consent, copied)
        self.assertEqual(self.transport.events, [])
        self.assertTrue(recover_managed(self.transport, consent, canonical)['complete'])
        self.assertEqual([r['state'] for r in Journal.open(canonical, 'run', 'session').operations], ['done'] * 3)
        removed = self.transport.events.count('evacuate_remove')
        self.assertEqual(removed, 3)
        self.assertTrue(recover_managed(self.transport, consent, canonical)['complete'])
        self.assertEqual(self.transport.events.count('evacuate_remove'), removed)

    def test_recovery_rejects_copy_and_alias_paths_before_broker_requests(self):
        from tools.native_spike.journal import managed_owner
        from tools.native_spike.managed import recover_managed
        self.test_restart_context_is_durable_before_first_create()
        transport = ManagedTransport(self.path)
        transport.registry = self.transport.registry
        consent = dict(instance=INSTANCE, run_id='run', output='DP-2', recovery_output='DP-1')
        before = self.path.read_bytes()
        elsewhere = Path(self.tmp.name) / 'copied-run'
        elsewhere.mkdir(mode=0o700)
        copied = elsewhere / 'journal.json'
        copied.write_bytes(before); copied.chmod(0o600)
        symlink = self.path.with_name('alias.json')
        symlink.symlink_to(self.path)
        directory_alias = Path(self.tmp.name) / 'alias-dir'
        directory_alias.symlink_to(self.path.parent, target_is_directory=True)
        aliases = (copied, symlink, directory_alias / 'journal.json',
                   self.path.parent / '..' / 'run' / 'journal.json')
        for alias in aliases:
            with self.subTest(alias=alias):
                with managed_owner(self.path):
                    with self.assertRaises((ValueError, OSError)):
                        recover_managed(transport, consent, alias)
                with self.assertRaises((ValueError, OSError)):
                    recover_managed(transport, consent, alias)
                self.assertEqual(transport.events, [])
                self.assertEqual(self.path.read_bytes(), before)
                if alias in (copied, symlink):
                    self.assertFalse(alias.with_name(alias.name + '.owner').exists())
        # Harmless lexical spelling still reaches the same canonical flock.
        spelling = str(self.path.parent) + '/./journal.json'
        with managed_owner(self.path):
            with self.assertRaises(BlockingIOError): recover_managed(transport, consent, spelling)
        self.assertTrue(recover_managed(transport, consent, spelling)['complete'])
        self.assertEqual([r['state'] for r in Journal.open(self.path, 'run', 'session').operations], ['done'] * 3)

    def test_canonical_location_keeps_no_follow_and_single_link_guards(self):
        from tools.native_spike.managed import recover_managed
        self.test_restart_context_is_durable_before_first_create()
        transport = ManagedTransport(self.path)
        transport.registry = self.transport.registry
        consent = dict(instance=INSTANCE, run_id='run', output='DP-2', recovery_output='DP-1')
        before = self.path.read_bytes()
        saved = self.path.with_name('saved.json')
        self.path.rename(saved)
        for alias_kind in ('symlink', 'hardlink'):
            with self.subTest(alias_kind=alias_kind):
                if alias_kind == 'symlink': self.path.symlink_to(saved)
                else: os.link(saved, self.path)
                try:
                    with self.assertRaises(OSError): recover_managed(transport, consent, self.path)
                    self.assertEqual(transport.events, [])
                    self.assertEqual(saved.read_bytes(), before)
                finally:
                    self.path.unlink()
        saved.rename(self.path)
        original_dir = self.path.parent
        renamed_dir = original_dir.with_name('saved-run')
        original_dir.rename(renamed_dir)
        original_dir.symlink_to(renamed_dir, target_is_directory=True)
        try:
            with self.assertRaises(OSError): recover_managed(transport, consent, self.path)
            self.assertEqual(transport.events, [])
            self.assertEqual(self.path.read_bytes(), before)
        finally:
            original_dir.unlink()
            renamed_dir.rename(original_dir)
        self.assertTrue(recover_managed(transport, consent, self.path)['complete'])

    def test_run_and_recovery_have_one_lifetime_owner(self):
        from tools.native_spike import journal
        self.assertTrue(hasattr(journal, 'managed_owner'), 'no lifetime exclusion')
        with journal.managed_owner(self.path):
            with self.assertRaises(BlockingIOError):
                with journal.managed_owner(self.path):
                    self.fail('second controller acquired active run')
        with journal.managed_owner(self.path):
            pass

    def test_partial_creation_and_launch_failures_recover_handles(self):
        from tools.native_spike.managed import _run_owned
        for failure in ('partial', 'connect', 'spawn'):
            with self.subTest(failure=failure):
                path = self.path.with_name(failure + '.json')
                journal = Journal.open(path, 'run', 'session')
                transport = ManagedTransport(path)
                broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1')
                broker.require_output_ownership()
                if failure == 'partial': transport.fail_index = 1
                def connect(session):
                    if failure == 'connect': raise OSError('connect failed')
                    import socket
                    peer, wayland = socket.socketpair()
                    self.addCleanup(peer.close); self.addCleanup(wayland.close)
                    transport._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
                    return wayland
                transport.connect_wayland = connect
                with patch('subprocess.Popen', side_effect=OSError('spawn failed')):
                    with self.assertRaises((OSError, ValueError)):
                        _run_owned(transport, journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'))
                self.assertTrue(journal.operations)
                self.assertTrue(all(r['state'] == 'done' for r in Journal.open(path, 'run', 'session').operations))
                self.assertTrue(all(r['state'] == 'dead' for r in transport.registry.values()))

    def test_real_watchdog_reaps_child_on_managed_epoch_loss(self):
        import multiprocessing
        import socket
        import subprocess
        import sys
        import time
        from tools.native_spike.managed import ManagedGuard
        from tools.native_spike.supervisor import Supervisor
        from tools.native_spike.journal import recover
        transport = ManagedTransport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1')
        specs = plan_outputs([], [], 'run')
        create_outputs(specs, self.journal, broker)
        transport.epoch = 'lost-epoch'
        before = self.path.read_bytes()
        workers = {p.pid for p in multiprocessing.active_children()}
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        parent, peer = socket.socketpair()
        with parent, peer:
            watchdog = Supervisor(child, lambda: recover(self.journal, broker),
                                  guard=ManagedGuard(broker, 'DP-2', specs), grace=0.1)
            started = time.monotonic()
            result = watchdog.run(parent)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(child.poll())
        self.assertFalse(result.complete)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, workers)

    def test_missing_context_scope_and_journal_fail_without_removal(self):
        from tools.native_spike.managed import recover_managed
        from tools.native_spike.journal import managed_owner
        consent = dict(instance=INSTANCE, run_id='run', output='DP-2', recovery_output='DP-1')
        transport = ManagedTransport(self.path)
        with self.assertRaises(ValueError): recover_managed(transport, consent, self.path)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError): recover_managed(transport, consent, self.path)
        self.assertFalse(self.path.exists())
        self.journal = Journal.open(self.path, 'run', 'session')
        self.test_restart_context_is_durable_before_first_create()
        transport.registry = self.transport.registry
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            recover_managed(transport, dict(consent, output='DP-1', recovery_output='DP-2'), self.path)
        with managed_owner(self.path):
            with self.assertRaises(BlockingIOError): recover_managed(transport, consent, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertNotIn('evacuate_remove', transport.events)

    def test_context_fsync_failure_prevents_creation(self):
        from tools.native_spike.managed import _run_owned
        transport = ManagedTransport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1')
        broker.require_output_ownership()
        with patch.object(self.journal, '_save', side_effect=OSError('durability failed')):
            with self.assertRaises(OSError):
                _run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'))
        self.assertEqual(transport.events, ['hello'])
        self.assertIsNone(Journal.open(self.path, 'run', 'session').broker)

    def test_bad_broker_context_and_legacy_rebinding_are_rejected(self):
        from tools.native_spike.journal import validate_broker
        context = dict(instance=INSTANCE, output='DP-2', recovery_output='DP-1', epoch='epoch', fallback='handle')
        for invalid in (None, {}, dict(context, unknown=True), dict(context, epoch=True),
                        dict(context, instance='guess'), dict(context, output='DP-1')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): validate_broker(invalid)
        create_outputs(plan_outputs([], [], 'run'), self.journal, self.broker)
        with self.assertRaises(ValueError): self.journal.bind_broker(context)

    def test_run_managed_threads_desktops_and_store(self):
        from tools.native_spike import managed
        desktops = [{'width': 2560, 'height': 1440, 'scale': 1.25}, {'width': 1920, 'height': 1080, 'scale': 1.0},
                    {'width': 3840, 'height': 1080, 'scale': 2.0}]
        consent = dict(instance=INSTANCE, run_id='new-run', output='DP-2', recovery_output='DP-1')
        store, seen = object(), {}
        def owned(client, journal, broker, output, fallback, specs, **kw):
            seen.update(kw, specs=specs); return 'result'
        with patch.object(managed, '_run_owned', owned):
            self.assertEqual(managed.run_managed(ManagedTransport(self.path), consent, Path(self.tmp.name),
                                                 desktops=desktops, store=store, renderer='/r'), 'result')
        self.assertEqual([(s['width'], s['height'], s['scale']) for s in seen['specs']],
                         [(2560, 1440, 1.25), (1920, 1080, 1.0), (3840, 1080, 2.0)])
        self.assertIs(seen['store'], store); self.assertEqual(seen['renderer'], '/r')
        with patch.object(managed, '_run_owned', owned):
            managed.run_managed(ManagedTransport(self.path), dict(consent, run_id='cli-run'), Path(self.tmp.name))
        self.assertEqual({(s['width'], s['height'], s['scale']) for s in seen['specs']}, {(1920, 1080, 1.0)})
        self.assertIsNone(seen['store'])

    def test_view_controls_receives_store(self):
        from tools.native_spike import managed
        transport = ManagedTransport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1'); broker.require_output_ownership()
        store, seen = object(), {}
        class Stop(Exception): pass
        def controls(client, run_id, session, capture, output=None, store=None, fallback=None, desktops=None, windows=None, cursor_shape=None, renderer=None):
            seen['store'], seen['fallback'], seen['desktops'] = store, fallback, desktops; raise Stop
        transport.connect_wayland = lambda session: open(os.devnull, 'rb')
        with patch.object(managed, 'view_controls', controls), patch.object(managed, 'recover'), self.assertRaises(Stop):
            managed._run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'), store=store)
        self.assertIs(seen['store'], store)
        self.assertEqual(seen['fallback'], 'DP-1')  # Ctrl+Alt+B sends windows back to the run's recovery output
        self.assertIsNone(seen['desktops'])  # no capture, no glass pills: nothing to add or remove

    def test_missing_plugin_or_physical_pair_cannot_create_journal(self):
        from tools.native_spike.managed import run_managed
        consent = dict(instance=INSTANCE, run_id='new-run', output='DP-2', recovery_output='DP-1')
        transport = ManagedTransport(self.path)
        transport._request = lambda wire: b'no broker'
        with self.assertRaises(ValueError): run_managed(transport, consent, Path(self.tmp.name))
        self.assertFalse((Path(self.tmp.name) / 'xr-workspace-spike' / 'new-run').exists())
        transport = ManagedTransport(self.path)
        snapshot = transport.query('monitors')[:1]
        transport.query = lambda kind: snapshot
        with self.assertRaises(ValueError): run_managed(transport, consent, Path(self.tmp.name))
        self.assertEqual(transport.events, [])
        self.assertFalse((Path(self.tmp.name) / 'xr-workspace-spike' / 'new-run').exists())
