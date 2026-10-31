"""Explicit isolated-VM managed mode, offline: synthetic sysfs tree and broker wire.

The synthetic guest tree is injected only by patching the module SYSROOT in tests;
production has no flag, file or environment override for VM evidence.
"""
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import isolated
from tools.native_spike.hyprland import OwnershipBlocked
from tools.native_spike.journal import Journal, recover
from tools.native_spike.ownership import OwnershipBrokerClient, IsolatedBrokerClient

INSTANCE = 'b' * 40 + '_123_456'
BOOT = '0f0e0d0c-0b0a-4908-8706-050403020100'
TARGET, FALLBACK = 'b' * 48, 'c' * 48


def guest_tree(root, *, hypervisor=True, driver='virtio_gpu', vendor='0x1af4', connectors=None,
               extra_card=None):
    root = Path(root)
    (root / 'proc/sys/kernel/random').mkdir(parents=True)
    (root / 'proc/sys/kernel/random/boot_id').write_text(BOOT + '\n')
    flags = 'fpu vme sse2' + (' hypervisor' if hypervisor else '')
    (root / 'proc/cpuinfo').write_text(''.join(f'processor\t: {i}\nflags\t\t: {flags}\n\n' for i in range(2)))
    cards = [('card0', '226:0', driver, vendor, connectors or {'Virtual-1': 35, 'Virtual-2': 36})]
    if extra_card: cards.append(extra_card)
    # Mirrors the layout observed in a real virtio-gpu guest (xr-isolated-twohead
    # serial.log): drm/cardN sits under the virtio-pci PCI function; the PCI
    # function has one virtioN child bound to virtio_gpu.
    for index, (card, dev, drv, ven, conns) in enumerate(cards):
        pci = root / f'sys/devices/pci0000:00/0000:00:0{index + 2}.0'
        virtio = pci / f'virtio{index}'
        (pci / 'drm' / card).mkdir(parents=True)
        virtio.mkdir()
        (pci / 'vendor').write_text(ven + '\n')
        pci_driver = 'virtio-pci' if drv == 'virtio_gpu' else drv
        for bus, driver_name in (('virtio', drv), ('pci', pci_driver)):
            (root / f'sys/bus/{bus}/drivers/{driver_name}').mkdir(parents=True, exist_ok=True)
        os.symlink(root / f'sys/bus/pci/drivers/{pci_driver}', pci / 'driver')
        os.symlink(root / 'sys/bus/pci', pci / 'subsystem')
        os.symlink(root / f'sys/bus/virtio/drivers/{drv}', virtio / 'driver')
        os.symlink(root / 'sys/bus/virtio', virtio / 'subsystem')
        (pci / 'drm' / card / 'dev').write_text(dev + '\n')
        os.symlink(pci, pci / 'drm' / card / 'device')
        (root / 'sys/class/drm').mkdir(parents=True, exist_ok=True)
        os.symlink(pci / 'drm' / card, root / 'sys/class/drm' / card)
        for name, connector in conns.items():
            path = pci / 'drm' / card / f'{card}-{name}'
            path.mkdir()
            (path / 'connector_id').write_text(f'{connector}\n')
            (path / 'status').write_text('connected\n')
            (path / 'enabled').write_text('enabled\n')
            os.symlink(path, root / 'sys/class/drm' / f'{card}-{name}')
    return root


def side(handle, name, connector, minor=0, **changes):
    return {'handle': handle, 'name': name, 'device_major': 226, 'device_minor': minor,
            'connector_id': connector, 'drm_eligible': True, 'classification': 'unclassified',
            'enabled': True, 'dpms': True, 'mirrored': False, **changes}


class PairTransport:
    """Schema-2 broker double; any schema-1 request is a test failure."""
    instance = INSTANCE

    def __init__(self, path):
        self.path = path
        self.epoch = 'epoch-iso'
        self.registry, self.events = {}, []
        self.pair = {'mode': 'isolated-vm', 'target': side(TARGET, 'Virtual-1', 35),
                     'fallback': side(FALLBACK, 'Virtual-2', 36)}
        self.pair_valid = True
        self.runtime_dir = path.parents[2]
        self._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')

    def verified_session(self): return {'id': 'session'}

    binds = None
    def install_view_binds(self, python, client, sock, run_id, session):
        from tools.native_spike.view import install_binds_lua
        self.binds = install_binds_lua(python, client, sock, run_id, session)  # validates like the real client
    def remove_view_binds(self): self.binds = 'removed'
    def mutate(self, command): return 'ok'

    def query(self, kind):
        monitor = {'id': 0, 'name': 'Virtual-1', 'width': 1280, 'height': 800, 'x': 0, 'y': 0,
                   'scale': 1.0, 'transform': 0, 'refreshRate': 74.994, 'disabled': False,
                   'dpmsStatus': True, 'mirrorOf': 'none', 'description': 'Red Hat Inc. QEMU Monitor'}
        return [monitor, dict(monitor, id=1, name='Virtual-2', x=1280)] if kind == 'monitors' else []

    def _request(self, wire):
        assert wire.startswith('j/xrspike ')
        j = json.loads(wire[10:]); p = dict(j['payload']); op = j['op']
        self.events.append(op)
        assert j['schema'] == 2 and p.pop('mode') == 'isolated-vm', j
        if op == 'pair_hello':
            assert p == {'target_name': 'Virtual-1', 'fallback_name': 'Virtual-2'}
            result = copy.deepcopy(self.pair)
        elif j['epoch'] != self.epoch:
            return b'{"error":"epoch lost"}'
        elif op == 'pair_observe':
            if not self.pair_valid or p != {'target': TARGET, 'fallback': FALLBACK}:
                return b'{"error":"ownership blocked"}'
            result = copy.deepcopy(self.pair)
        else:
            if op == 'create':
                records = json.loads(self.path.read_text())['operations']
                assert any(r['operation']['id'] == p['id'] and r['state'] == 'pending' for r in records)
                self.registry[p['id']] = {'id': p['id'], 'name': p['name'], 'state': 'live', 'physical': False,
                    'fallback_usable': True, 'observation': {**{k: p[k] for k in ('width', 'height', 'refresh_millihz', 'scale', 'x', 'y')}, 'workspace': 61 + 10 * p['index'],
                    'enabled': True, 'dpms': True, 'mirrored': False, 'transform': 0,
                    'workspaces': [{'id': 61 + 10 * p['index'], 'name': str(61 + 10 * p['index']), 'special': False}], 'windows': []}}
            if p['id'] not in self.registry: return b'{"error":"unproven UUID"}'
            if op == 'evacuate_remove':
                assert p['fallback'] == FALLBACK
                self.registry[p['id']].update(state='dead', observation=None)
            result = copy.deepcopy(self.registry[p['id']])
        return json.dumps({**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')},
                           'epoch': self.epoch, 'result': result}).encode()


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.sysroot = guest_tree(self.base / 'guest')
        self.path = self.runtime / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.transport = PairTransport(self.path)

    def consent(self, **changes):
        data = {'schema': 1, 'gate': 'VM-isolated', 'mode': 'isolated-vm', 'run_id': 'run',
                'instance': INSTANCE, 'boot_id': BOOT, 'session': 'session', 'runtime': str(self.runtime),
                'output': 'Virtual-1', 'recovery_output': 'Virtual-2',
                'allow': ['headless-outputs', 'test-pattern'],
                'capture': False, 'input': False, 'sdk': False, 'mode_switch': False, **changes}
        path = self.base / f'consent-{len(list(self.base.glob("consent-*")))}.json'
        path.write_text(json.dumps(data)); path.chmod(0o600)
        return path

    # --- guest evidence ---------------------------------------------------
    def test_guest_evidence_reads_virtio_connectors(self):
        evidence = isolated.collect_guest_evidence(self.sysroot)
        self.assertEqual(evidence, {'boot_id': BOOT, 'connectors': {
            'Virtual-1': {'device': '226:0', 'connector': 35},
            'Virtual-2': {'device': '226:0', 'connector': 36}}})

    def test_missing_or_false_vm_evidence_fails_closed(self):
        cases = {'host-cpu': dict(hypervisor=False), 'host-gpu-driver': dict(driver='amdgpu'),
                 'non-virtio-pci-parent': dict(vendor='0x1002'),
                 'passthrough-card': dict(extra_card=('card1', '226:1', 'amdgpu', '0x1002', {'DP-1': 402}))}
        for name, kwargs in cases.items():
            with self.subTest(name):
                root = guest_tree(self.base / name, **kwargs)
                with self.assertRaises((ValueError, OSError)):
                    isolated.collect_guest_evidence(root)
        pci = 'sys/devices/pci0000:00/0000:00:02.0'
        no_child = guest_tree(self.base / 'no-child')
        import shutil; shutil.rmtree(no_child / pci / 'virtio0')
        two_children = guest_tree(self.base / 'two-children')
        shutil.copytree(two_children / pci / 'virtio0', two_children / pci / 'virtio9', symlinks=True)
        for name, root in (('no-virtio-child', no_child), ('two-virtio-children', two_children)):
            with self.subTest(name), self.assertRaises((ValueError, OSError)):
                isolated.collect_guest_evidence(root)
        for missing in ('proc/sys/kernel/random/boot_id', 'proc/cpuinfo',
                        'sys/class/drm/card0-Virtual-1/connector_id', 'sys/class/drm/card0/dev'):
            with self.subTest(missing):
                root = guest_tree(self.base / ('missing' + str(abs(hash(missing)))))
                (root / missing).unlink()
                with self.assertRaises((ValueError, OSError)):
                    isolated.collect_guest_evidence(root)

    def test_real_host_is_rejected_by_positive_evidence(self):
        # Read-only on the actual machine running the tests. Unless this suite itself
        # runs inside a verified virtio guest, positive VM proof must be absent.
        cpuinfo = Path('/proc/cpuinfo').read_text()
        if 'hypervisor' in cpuinfo.split():
            self.skipTest('test runner is virtualized')
        with self.assertRaises((ValueError, OSError)):
            isolated.collect_guest_evidence(Path('/'))

    # --- native proof join -------------------------------------------------
    def proof(self, target=None, fallback=None):
        from tools.native_spike.ownership import parse_pair
        pair = {'mode': 'isolated-vm', 'target': target or side(TARGET, 'Virtual-1', 35),
                'fallback': fallback or side(FALLBACK, 'Virtual-2', 36)}
        return parse_pair(pair, 'Virtual-1', 'Virtual-2')

    def test_native_proof_joins_guest_sysfs(self):
        handles, proof = self.proof()
        self.assertEqual(handles, (TARGET, FALLBACK))
        isolated.join_proof(isolated.collect_guest_evidence(self.sysroot), proof)

    def test_substitution_and_aliasing_are_rejected(self):
        evidence = isolated.collect_guest_evidence(self.sysroot)
        bad = {'wrong-device': dict(target=side(TARGET, 'Virtual-1', 35, minor=1)),
               'same-name-other-connector': dict(target=side(TARGET, 'Virtual-1', 99)),
               'fallback-swapped-connector': dict(fallback=side(FALLBACK, 'Virtual-2', 35))}
        for name, kwargs in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                isolated.join_proof(evidence, self.proof(**kwargs)[1])
        from tools.native_spike.ownership import parse_pair
        malformed = [side(TARGET, 'Virtual-1', 35, classification='physical'),
                     side(TARGET, 'Virtual-1', 35, dpms=False), side(TARGET, 'Virtual-1', 35, mirrored=True),
                     side(TARGET, 'Virtual-1', 35, connector_id=True), side(TARGET, 'Virtual-1', 35, extra=1),
                     side(TARGET, 'Virtual-3', 35), side(FALLBACK, 'Virtual-1', 35)]
        for target in malformed:
            with self.subTest(target=target), self.assertRaises(ValueError):
                parse_pair({'mode': 'isolated-vm', 'target': target, 'fallback': side(FALLBACK, 'Virtual-2', 36)},
                           'Virtual-1', 'Virtual-2')

    # --- consent -----------------------------------------------------------
    def test_consent_is_mode_separated(self):
        from tools.native_spike.__main__ import load_consent
        good = self.consent()
        self.assertEqual(isolated.load_isolated_consent(good, INSTANCE, 'Virtual-1')['boot_id'], BOOT)
        with self.assertRaises(ValueError): load_consent(good, INSTANCE, 'Virtual-1')
        with self.assertRaises(ValueError):
            load_consent(good, INSTANCE, 'Virtual-1', presentation_only=True)
        p2 = self.base / 'p2.json'
        p2.write_text(json.dumps({'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE,
            'output': 'Virtual-1', 'recovery_output': 'Virtual-2', 'allow': ['headless-outputs', 'test-pattern'],
            'capture': False, 'input': False, 'sdk': False, 'mode_switch': False}))
        p2.chmod(0o600)
        with self.assertRaises(ValueError): isolated.load_isolated_consent(p2, INSTANCE, 'Virtual-1')
        for changes in ({'capture': True}, {'mode': 'normal'}, {'gate': 'P2'}, {'output': 'DP-1'},
                        {'recovery_output': 'Virtual-1'}, {'boot_id': 'not-a-uuid'}, {'runtime': 'relative'}, {'runtime': str(self.runtime) + '/'},
                        {'allow': ['test-pattern']}, {'schema': True}):
            with self.subTest(changes), self.assertRaises(ValueError):
                isolated.load_isolated_consent(self.consent(**changes), INSTANCE, 'Virtual-1')
        forbidden = isolated.FORBIDDEN_INSTANCE
        with self.assertRaises(ValueError):
            isolated.load_isolated_consent(self.consent(instance=forbidden), forbidden, 'Virtual-1')
        # Rejected before the consent file is opened at all.
        with self.assertRaisesRegex(ValueError, 'physical compositor'):
            isolated.load_isolated_consent(self.base / 'absent.json', forbidden, 'Virtual-1')

    # --- broker / journal cross-mode --------------------------------------
    def test_isolated_client_never_sends_schema_one(self):
        self.path.parent.mkdir(parents=True, mode=0o700); self.path.parent.parent.chmod(0o700)
        Journal.open(self.path, 'run', 'session')
        broker = IsolatedBrokerClient(self.transport, 'run', 'session', 'Virtual-1', 'Virtual-2')
        broker.require_output_ownership()
        self.assertEqual((broker.target, broker.fallback), (TARGET, FALLBACK))
        broker.observe_pair()
        # Well-formed reply, changed DRM identity behind the same handles.
        self.transport.pair['fallback']['connector_id'] = 37
        with self.assertRaises(OwnershipBlocked): broker.observe_pair()
        self.transport.pair['fallback']['connector_id'] = 36
        self.transport.pair_valid = False
        with self.assertRaises(OwnershipBlocked): broker.observe_pair()
        self.assertEqual(self.transport.events, ['pair_hello', 'pair_observe', 'pair_observe', 'pair_observe'])

    def bound_journal(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True); self.path.parent.parent.chmod(0o700)
        journal = Journal.open(self.path, 'run', 'session')
        broker = IsolatedBrokerClient(self.transport, 'run', 'session', 'Virtual-1', 'Virtual-2')
        broker.require_output_ownership()
        journal.bind_broker(isolated.broker_context(self.transport, broker, BOOT, self.runtime))
        from tools.native_spike.hyprland import create_outputs, plan_outputs
        self.specs = plan_outputs([], [], 'run')
        create_outputs(self.specs, journal, broker)
        return journal

    def test_journal_authority_cannot_cross_modes(self):
        journal = self.bound_journal()
        reopened = Journal.open(self.path, 'run', 'session')
        self.assertEqual(reopened.broker['mode'], 'isolated-vm')
        with self.assertRaises(OwnershipBlocked): OwnershipBrokerClient.from_journal(self.transport, reopened)
        restored = IsolatedBrokerClient.from_journal(self.transport, reopened)
        self.assertEqual((restored.epoch, restored.target, restored.fallback), ('epoch-iso', TARGET, FALLBACK))
        # A legacy journal is never restorable as isolated authority.
        legacy_path = self.runtime / 'legacy.json'
        legacy = Journal.open(legacy_path, 'run', 'session')
        legacy.bind_broker({'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1',
                            'epoch': 'epoch-one', 'fallback': 'physical-handle'})
        with self.assertRaises(OwnershipBlocked): IsolatedBrokerClient.from_journal(self.transport, legacy)
        # Tampered saved proof is rejected at load.
        data = json.loads(self.path.read_text()); data['broker']['proof']['target']['connector'] = 0
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): Journal.open(self.path, 'run', 'session')
        del journal

    def test_restart_recovery_uses_saved_handles_only(self):
        self.bound_journal()
        self.transport.events.clear()
        self.transport.pair_valid = False  # target lost: recovery still needs only the fallback
        reopened = Journal.open(self.path, 'run', 'session')
        restored = IsolatedBrokerClient.from_journal(self.transport, reopened)
        self.assertTrue(recover(reopened, restored).complete)
        self.assertNotIn('pair_hello', self.transport.events)
        self.assertNotIn('hello', self.transport.events)

    # --- guard -------------------------------------------------------------
    def test_guard_revalidates_pair_and_guest_evidence(self):
        self.bound_journal()
        broker = IsolatedBrokerClient.from_journal(self.transport, Journal.open(self.path, 'run', 'session'))
        guard = isolated.IsolatedGuard(broker, 'Virtual-1', self.specs, BOOT, self.sysroot)
        guard()
        # Owned output readback is part of every guard period.
        observation = self.transport.registry[self.specs[1]['id']]['observation']
        observation['x'] += 1
        with self.assertRaises(ValueError): guard()
        observation['x'] -= 1
        guard()
        (self.sysroot / 'sys/class/drm/card0-Virtual-2/connector_id').write_text('99\n')
        with self.assertRaises(ValueError): guard()
        (self.sysroot / 'sys/class/drm/card0-Virtual-2/connector_id').write_text('36\n')
        guard()
        self.transport.pair_valid = False
        with self.assertRaises(ValueError): guard()
        self.transport.pair_valid = True
        with self.assertRaises(ValueError):
            isolated.IsolatedGuard(broker, 'Virtual-1', self.specs, 'ffffffff-0000-4000-8000-000000000000', self.sysroot)()

    def test_guard_runs_in_real_spawn_worker(self):
        # Production worker in a real spawned process with a pickled guard (no inherited state).
        import multiprocessing
        from tools.native_spike.supervisor import _guard_worker
        self.bound_journal()
        broker = IsolatedBrokerClient.from_journal(self.transport, Journal.open(self.path, 'run', 'session'))
        guard = isolated.IsolatedGuard(broker, 'Virtual-1', self.specs, BOOT, self.sysroot)
        context = multiprocessing.get_context('spawn')
        for corrupt in (False, True):
            if corrupt: (self.sysroot / 'sys/class/drm/card0-Virtual-1/connector_id').write_text('77\n')
            receiver, sender = context.Pipe(duplex=False)
            worker = context.Process(target=_guard_worker, args=(guard, sender))
            worker.start(); sender.close()
            try:
                self.assertTrue(receiver.poll(20))
                self.assertEqual(receiver.recv_bytes(1), b'F' if corrupt else b'V')
            finally:
                worker.kill(); worker.join(); worker.close(); receiver.close()

    def test_connector_skips_and_ambiguity(self):
        root = guest_tree(self.base / 'skips', connectors={'Virtual-1': 35, 'Virtual-2': 36, 'Virtual-3': 37, 'Writeback-1': 38})
        drm = root / 'sys/class/drm'
        (drm / 'card0-Virtual-2/status').write_text('disconnected\n')
        (drm / 'card0-Virtual-3/enabled').write_text('disabled\n')
        self.assertEqual(set(isolated.collect_guest_evidence(root)['connectors']), {'Virtual-1'})
        os.symlink(drm / 'card0-Virtual-1', drm / 'card9-Virtual-1')
        with self.assertRaisesRegex(ValueError, 'verified card'): isolated.collect_guest_evidence(root)
        (drm / 'card9-Virtual-1').unlink()
        dup = guest_tree(self.base / 'dup', extra_card=('card1', '226:1', 'virtio_gpu', '0x1af4', {'Virtual-1': 35}))
        with self.assertRaisesRegex(ValueError, 'ambiguous'): isolated.collect_guest_evidence(dup)
        malformed = guest_tree(self.base / 'nocolon')
        (malformed / 'proc/cpuinfo').write_text('flags hypervisor\n')
        with self.assertRaises(ValueError): isolated.collect_guest_evidence(malformed)

    # --- CLI ---------------------------------------------------------------
    def run_cli(self, argv):
        from tools.native_spike.__main__ import main
        with patch('tools.native_spike.__main__.HyprlandClient', return_value=self.transport), \
             patch.object(isolated, 'SYSROOT', self.sysroot), \
             patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime)):
            return main(argv)

    def test_cli_isolated_run_creates_supervises_and_recovers(self):
        peer, wayland = socket.socketpair()
        self.addCleanup(peer.close); self.addCleanup(wayland.close)
        self.transport.connect_wayland = lambda session: wayland
        class Child:
            def poll(self): return 0
        launched = []
        def spawn(argv, **kwargs):
            saved = Journal.open(self.path, 'run', 'session')
            self.assertEqual(saved.broker['mode'], 'isolated-vm')
            self.assertEqual([r['state'] for r in saved.operations], ['confirmed'] * 3)
            launched.append(argv[argv.index('--output') + 1])
            endpoint = socket.fromfd(int(argv[argv.index('--supervised-fd') + 1]), socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return Child()
        with patch('subprocess.Popen', side_effect=spawn):
            code = self.run_cli(['isolated-run', '--instance', INSTANCE, '--output', 'Virtual-1',
                                 '--consent-file', str(self.consent())])
        self.assertEqual(code, 0)
        self.assertEqual(launched, ['Virtual-1'])
        self.assertEqual([r['state'] for r in Journal.open(self.path, 'run', 'session').operations], ['done'] * 3)
        self.assertNotIn('hello', self.transport.events)
        self.assertEqual(wayland.fileno(), -1)

    def test_cli_capture_scope_reaches_renderer_only_when_granted(self):
        for granted in (True, False):
            with self.subTest(granted=granted):
                self.setUp()
                peer, wayland = socket.socketpair()
                self.addCleanup(peer.close); self.addCleanup(wayland.close)
                self.transport.connect_wayland = lambda session: wayland
                class Child:
                    def poll(self): return 0
                seen = []
                def spawn(argv, **kwargs):
                    seen.append(argv)
                    endpoint = socket.fromfd(int(argv[argv.index('--supervised-fd') + 1]), socket.AF_UNIX, socket.SOCK_STREAM)
                    self.addCleanup(endpoint.close)
                    return Child()
                changes = {'capture': 'managed-outputs', 'allow': ['headless-outputs', 'capture-managed-outputs']} if granted else {}
                binds_during = []
                original = spawn
                def spawn(argv, _original=original, **kwargs):
                    binds_during.append(self.transport.binds); return _original(argv, **kwargs)
                with patch('subprocess.Popen', side_effect=spawn), patch.dict(os.environ, XDG_CONFIG_HOME=str(self.base / 'cfg')):
                    code = self.run_cli(['isolated-run', '--instance', INSTANCE, '--output', 'Virtual-1',
                                         '--consent-file', str(self.consent(**changes))])
                self.assertEqual(code, 0)
                argv = seen[0]
                if granted:
                    self.assertEqual(argv[argv.index('--capture') + 1], 'XR-1,XR-2,XR-3')
                    self.assertIn('CTRL + ALT + up', binds_during[0])   # runtime binds exist while running
                    self.assertEqual(self.transport.binds, 'removed')  # and are removed at stop
                else:
                    self.assertNotIn('--capture', argv); self.assertIsNone(self.transport.binds)

    def test_cli_rejects_before_broker_on_scope_or_evidence_mismatch(self):
        cases = {'boot': dict(consent={'boot_id': 'ffffffff-0000-4000-8000-000000000000'}),
                 'session': dict(consent={'session': 'other'}),
                 'runtime': dict(consent={'runtime': str(self.base)}),
                 'host': dict(sysroot=guest_tree(self.base / 'host', hypervisor=False)),
                 'substituted': dict(sysroot=guest_tree(self.base / 'subst', connectors={'Virtual-1': 41, 'Virtual-2': 36}))}
        for name, case in cases.items():
            with self.subTest(name):
                self.transport.events.clear()
                path = self.consent(**case.get('consent', {}))
                with patch.object(isolated, 'SYSROOT', case.get('sysroot', self.sysroot)):
                    from tools.native_spike.__main__ import main
                    with patch('tools.native_spike.__main__.HyprlandClient', return_value=self.transport), \
                         patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime)):
                        code = main(['isolated-run', '--instance', INSTANCE, '--output', 'Virtual-1', '--consent-file', str(path)])
                self.assertEqual(code, 2)
                self.assertNotIn('create', self.transport.events)
                if name != 'substituted': self.assertEqual(self.transport.events, [])
                self.assertFalse(self.path.exists())

    def test_cli_forbidden_instance_rejected_before_client(self):
        from tools.native_spike.__main__ import main
        forbidden = isolated.FORBIDDEN_INSTANCE
        with patch('tools.native_spike.__main__.HyprlandClient') as client:
            code = main(['isolated-run', '--instance', forbidden, '--output', 'Virtual-1',
                         '--consent-file', str(self.consent(instance=forbidden))])
        self.assertEqual(code, 2)
        client.assert_not_called()

    def test_cli_normal_and_isolated_recovery_do_not_cross(self):
        self.bound_journal()
        code = self.run_cli(['recover', '--journal', str(self.path), '--instance', INSTANCE,
                             '--output', 'Virtual-1', '--consent-file', str(self.consent())])
        self.assertEqual(code, 2)
        # Fallback connector substituted in the guest kernel: recovery preserves the journal.
        connector = self.sysroot / 'sys/class/drm/card0-Virtual-2/connector_id'
        connector.write_text('99\n')
        before = self.path.read_bytes()
        code = self.run_cli(['isolated-recover', '--journal', str(self.path), '--instance', INSTANCE,
                             '--output', 'Virtual-1', '--consent-file', str(self.consent())])
        self.assertEqual((code, self.path.read_bytes()), (2, before))
        connector.write_text('36\n')
        code = self.run_cli(['isolated-recover', '--journal', str(self.path), '--instance', INSTANCE,
                             '--output', 'Virtual-1', '--consent-file', str(self.consent())])
        self.assertEqual(code, 0)
        self.assertEqual([r['state'] for r in Journal.open(self.path, 'run', 'session').operations], ['done'] * 3)
        self.assertNotIn('pair_hello', self.transport.events[1:])


if __name__ == '__main__':
    unittest.main()
