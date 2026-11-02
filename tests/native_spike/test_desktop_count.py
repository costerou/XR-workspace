"""Configurable managed-desktop count (1..6): validation, per-model profiles, output lifecycle and recovery.

Offline only: synthetic broker transport, real journal files, no compositor or renderer.
"""
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import geometry, profiles, view
from tools.native_spike.geometry import DEFAULT_DESKTOP, MAX_DESKTOPS, MIN_DESKTOPS, resize_desktops, validate_desktops
from tools.native_spike.hyprland import OwnershipBlocked, create_outputs, plan_outputs
from tools.native_spike.journal import Journal, recover
from tools.native_spike.ownership import OwnershipBrokerClient
from tools.native_spike.panel_consent import build_consent, load_panel_consent, write_consent
from tools.native_spike.supervisor import capture_arguments
from test_managed import INSTANCE, ManagedTransport
from test_ownership import Transport

ROOT = Path(__file__).resolve().parents[2]
D = {'width': 1920, 'height': 1080, 'scale': 1.0}
WIDE = {'width': 3840, 'height': 1080, 'scale': 2.0}
COUNTS = (1, 2, 4, 6)


class LimitsTests(unittest.TestCase):
    def test_limits_and_default(self):
        self.assertEqual((MIN_DESKTOPS, MAX_DESKTOPS, geometry.DEFAULT_DESKTOP_COUNT), (1, 6, 3))
        self.assertEqual(geometry.MANAGED_NAME, f'XR-[1-{MAX_DESKTOPS}]')  # single digit, so names sort like indexes

    def test_mirrors_agree(self):
        """The broker, renderer and panel each carry a copy of the limit; they must not drift."""
        registry = (ROOT / 'spike/ownership/registry.hpp').read_text()
        renderer = (ROOT / 'spike/include/xr/view.hpp').read_text()
        model = (ROOT / 'omarchy-plugin/cody.viture-workspace/Model.js').read_text()
        self.assertEqual(re.findall(r'constexpr int kMaxDesktops=(\d+);', registry), [str(MAX_DESKTOPS)])
        self.assertEqual(re.findall(r'constexpr std::uint32_t kMaxDesktops = (\d+);', renderer), [str(MAX_DESKTOPS)])
        self.assertEqual(re.findall(r'var MIN_DESKTOPS = (\d+), MAX_DESKTOPS = (\d+);', model),
                         [(str(MIN_DESKTOPS), str(MAX_DESKTOPS))])

    def test_validate_desktops_bounds(self):
        for n in range(MIN_DESKTOPS, MAX_DESKTOPS + 1):
            self.assertEqual(validate_desktops([D] * n), [D] * n)
        for bad in ([], [D] * 7, D, None, (D,), [D, {'width': 1921, 'height': 1080, 'scale': 1.0}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_desktops(bad)

    def test_resize(self):
        self.assertEqual(resize_desktops([D, WIDE], 4), [D, WIDE, WIDE, WIDE])
        self.assertEqual(resize_desktops([D, WIDE, D], 1), [D])
        grown = resize_desktops([D], 2); grown[1]['width'] = 640
        self.assertEqual(grown[0]['width'], 1920)  # copies, not aliases
        for bad in (0, 7, 2.0, True, '2', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError): resize_desktops([D], bad)


class ProfileCountTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'viture-workspace' / 'profiles.json'

    def test_count_saved_per_model(self):
        for model, n in (('beast', 6), ('luma-ultra', 1)):
            p = profiles.load_profile(self.path, model)
            profiles.save_profile(self.path, model, dict(p, desktops=[WIDE] * n))
        self.assertEqual(profiles.load_profile(self.path, 'beast')['desktops'], [WIDE] * 6)
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['desktops'], [WIDE])
        self.assertEqual(profiles.load_profile(self.path, 'unknown')['desktops'], [DEFAULT_DESKTOP] * 3)

    def test_view_store_preserves_count(self):
        p = profiles.load_profile(self.path, 'beast')
        profiles.save_profile(self.path, 'beast', dict(p, desktops=[D] * 5))
        store = profiles.ProfileStore(self.path, 'beast')
        store.save(dict(store.load(), spacing=80))
        self.assertEqual(len(profiles.load_profile(self.path, 'beast')['desktops']), 5)

    def test_save_rejects_bad_counts(self):
        p = profiles.load_profile(self.path, 'beast')
        for bad in ([], [D] * 7):
            with self.subTest(n=len(bad)), self.assertRaises(ValueError):
                profiles.save_profile(self.path, 'beast', dict(p, desktops=bad))
        self.assertFalse(self.path.exists())

    def test_damaged_file_keeps_count_or_falls_back(self):
        def raw(desktops):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({'schema': profiles.SCHEMA, 'profiles': {'beast': {'desktops': desktops}}}))
            return profiles.load_profile(self.path, 'beast')['desktops']
        # One bad entry falls back alone; the user's count is kept.
        self.assertEqual(raw([WIDE, {'width': 'x'}, WIDE, WIDE, WIDE]), [WIDE, DEFAULT_DESKTOP, WIDE, WIDE, WIDE])
        # An unusable list is the default layout.
        for bad in ([], [WIDE] * 7, 'three', None):
            with self.subTest(bad=bad): self.assertEqual(raw(bad), [DEFAULT_DESKTOP] * 3)


class ConsentCountTests(unittest.TestCase):
    def scope(self, n):
        sha = 'a' * 64
        return {'run_id': f'run{n}', 'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1', 'model': 'beast',
                'tracking': False, 'mode_switch': False, 'plugin': {'path': '/opt/p.so', 'sha256': sha},
                'renderer': {'path': '/opt/r', 'sha256': sha}, 'desktops': [D] * n}

    def test_any_count_round_trips_exactly(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        fd = os.open(tmp.name, os.O_DIRECTORY); self.addCleanup(os.close, fd)
        for n in COUNTS:
            with self.subTest(n=n):
                name = write_consent(fd, build_consent(self.scope(n)))
                self.assertEqual(len(load_panel_consent(Path(tmp.name) / name, INSTANCE, 'DP-2')['desktops']), n)

    def test_out_of_range_refused(self):
        for n in (0, 7):
            with self.subTest(n=n), self.assertRaises(ValueError): build_consent(self.scope(n))


class ViewCountTests(unittest.TestCase):
    def test_single_desktop_cannot_move(self):
        v = view.ViewState(['XR-1'])
        self.assertIsNone(v.apply('next')); self.assertIsNone(v.apply('prev'))
        self.assertEqual(v.apply('zoom_in'), 'XR-1')

    def test_six_desktops_reach_the_last(self):
        names = [f'XR-{i}' for i in range(1, 7)]
        v = view.ViewState(names)
        self.assertEqual([v.apply('next') for _ in range(6)], names[1:] + [None])
        self.assertEqual(v.selected, 5)

    def test_cursor_follow_covers_every_managed_output(self):
        v = view.ViewState([f'XR-{i}' for i in range(1, 7)], cursor_follow=True)
        self.assertTrue(v.cursor_entered('XR-6')); self.assertEqual(v.selected, 5)
        self.assertEqual(view.FOCUSEDMON.match('focusedmon>>XR-6,8').group(1), 'XR-6')
        self.assertIsNone(view.FOCUSEDMON.match('focusedmon>>XR-7,8'))

    def test_state_rejects_bad_name_lists(self):
        for bad in ([], [f'XR-{i}' for i in range(1, 8)], ['XR-1', 'XR-1'], ['XR-7'], ['DP-1']):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.ViewState(bad)

    def test_capture_argv_is_numeric_order(self):
        names = ['XR-6', 'XR-2', 'XR-4', 'XR-1', 'XR-5', 'XR-3']
        self.assertEqual(capture_arguments(names, 'DP-2'), ['--capture', 'XR-1,XR-2,XR-3,XR-4,XR-5,XR-6'])
        self.assertEqual(capture_arguments(['XR-1'], 'DP-2'), ['--capture', 'XR-1'])


class LifecycleCountTests(unittest.TestCase):
    """Broker-backed creation, readback, partial failure and recovery for every supported count."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def broker(self, name, **transport):
        path = Path(self.tmp.name) / f'{name}.json'
        journal = Journal.open(path, 'run', 'session')
        t = Transport(path)
        for k, v in transport.items(): setattr(t, k, v)
        return journal, t, OwnershipBrokerClient(t, 'run', 'session', 'DP-1')

    def test_create_and_recover_every_count(self):
        for n in range(MIN_DESKTOPS, MAX_DESKTOPS + 1):
            with self.subTest(n=n):
                journal, t, client = self.broker(f'full-{n}')
                specs = plan_outputs([], [], 'run', [D] * n)
                self.assertEqual(create_outputs(specs, journal, client), [f'XR-{i}' for i in range(1, n + 1)])
                self.assertEqual(t.events.count('create'), n); self.assertEqual(t.events.count('inspect'), n)
                self.assertEqual([r['state'] for r in Journal.open(journal.path, 'run', 'session').operations], ['confirmed'] * n)
                self.assertTrue(recover(journal, client).complete)
                self.assertEqual(t.events.count('evacuate_remove'), n)
                self.assertTrue(all(r['state'] == 'dead' for r in t.registry.values()))
                self.assertTrue(recover(journal, client).complete)  # idempotent
                self.assertEqual(t.events.count('evacuate_remove'), n)

    def test_partial_failure_at_any_index_of_six_rolls_back(self):
        for index in range(MAX_DESKTOPS):
            with self.subTest(index=index):
                journal, t, client = self.broker(f'partial-{index}', fail_index=index)
                with self.assertRaises(OwnershipBlocked): create_outputs(plan_outputs([], [], 'run', [D] * 6), journal, client)
                self.assertEqual(len(journal.operations), index + 1)  # nothing after the failure was attempted
                self.assertTrue(recover(journal, client).complete)
                self.assertEqual(len(t.registry), index + 1)
                self.assertTrue(all(r['state'] == 'dead' for r in t.registry.values()))

    def test_lost_reply_on_single_and_six(self):
        for n in (1, 6):
            with self.subTest(n=n):
                journal, t, client = self.broker(f'lost-{n}', lost_reply=True)
                with self.assertRaises(OSError): create_outputs(plan_outputs([], [], 'run', [D] * n), journal, client)
                restarted = OwnershipBrokerClient(t, 'run', 'session', 'DP-1'); restarted.require_output_ownership()
                self.assertTrue(recover(Journal.open(journal.path, 'run', 'session'), restarted).complete)
                self.assertTrue(all(r['state'] == 'dead' for r in t.registry.values()))

    def test_controller_restart_recovers_six_from_saved_epoch(self):
        journal, t, client = self.broker('restart')
        client.require_output_ownership()
        journal.bind_broker({'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1',
                             'epoch': client.epoch, 'fallback': client.fallback})
        create_outputs(plan_outputs([], [], 'run', [D] * 6), journal, client)
        t.instance = INSTANCE
        reopened = Journal.open(journal.path, 'run', 'session')
        restarted = OwnershipBrokerClient.from_journal(t, reopened)
        t.events.clear()
        self.assertTrue(recover(reopened, restarted).complete)
        self.assertNotIn('hello', t.events)
        self.assertEqual(t.events.count('evacuate_remove'), 6)
        self.assertEqual([r['state'] for r in Journal.open(journal.path, 'run', 'session').operations], ['done'] * 6)

    def test_six_desktop_requests_pass_actual_cpp_validator(self):
        import subprocess
        binary = ROOT / 'build/native-spike/ownership_test'
        if not binary.is_file(): self.skipTest('opt-in ownership native build not present')
        journal, t, client = self.broker('cpp')
        original = t._request
        def checked(wire):
            result = subprocess.run([str(binary), '--validate'], input=wire[2:].encode(), capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, wire)
            return original(wire)
        t._request = checked
        create_outputs(plan_outputs([], [], 'run', [D] * 6), journal, client)
        self.assertTrue(recover(journal, client).complete)
        seventh = json.loads(json.dumps({'schema': 1, 'session': 'session', 'run_id': 'run', 'request_id': 99, 'epoch': client.epoch,
                   'op': 'create', 'payload': {'id': '12345678-1234-4234-8234-123456789abc', 'name': 'XR-7', 'index': 6,
                   'width': 1920, 'height': 1080, 'refresh_millihz': 120000, 'scale': 1.0, 'x': 0, 'y': 0}}))
        refused = subprocess.run([str(binary), '--validate'], input=('xrspike ' + json.dumps(seventh)).encode(), capture_output=True, timeout=5)
        self.assertNotEqual(refused.returncode, 0)

    def test_epoch_loss_preserves_every_output(self):
        journal, t, client = self.broker('epoch')
        create_outputs(plan_outputs([], [], 'run', [D] * 6), journal, client)
        t.epoch = 'replacement'
        self.assertFalse(recover(journal, client).complete)
        self.assertTrue(all(r['state'] == 'live' for r in t.registry.values()))
        self.assertTrue(all(r['state'] != 'done' for r in journal.operations))

    def test_guard_watches_every_output(self):
        from tools.native_spike.managed import ManagedGuard
        from test_hyprland import RawFixture
        journal, t, client = self.broker('guard')
        specs = plan_outputs([], [], 'run', [D] * 6)
        create_outputs(specs, journal, client)
        snapshot = RawFixture().monitors
        snapshot.append(dict(snapshot[0], id=1, name='DP-2', x=1920))
        t.query = lambda kind: snapshot
        guard = ManagedGuard(client, 'DP-2', specs)
        guard()
        t.registry[specs[-1]['id']]['observation']['refresh_millihz'] = 60000  # only XR-6 drifts
        with self.assertRaises(OwnershipBlocked): guard()


class ManagedRunCountTests(unittest.TestCase):
    """Full run_managed path (plan, journal, create, guard, renderer, teardown) at the count limits."""
    def run_count(self, n, returncode=0):
        from test_hyprland import RawFixture
        from tools.native_spike.managed import run_managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name); runtime.chmod(0o700)
        path = runtime / 'xr-workspace-spike' / f'run{n}' / 'journal.json'
        t = ManagedTransport(path); t.path = path; t.runtime_dir = runtime
        t.require_output_ownership = RawFixture().require_output_ownership
        t._peer = (os.getpid(), os.getuid(), os.getgid(), 'synthetic')
        peer, wayland = socket.socketpair()
        self.addCleanup(peer.close); self.addCleanup(wayland.close)
        t.connect_wayland = lambda session: wayland
        seen = {}
        class Child:
            def poll(self): return returncode
        def spawn(argv, **kwargs):
            seen['ops'] = [r['state'] for r in Journal.open(path, f'run{n}', 'session').operations]
            seen['live'] = sum(r['state'] == 'live' for r in t.registry.values())
            endpoint = socket.fromfd(int(argv[argv.index('--supervised-fd') + 1]), socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(endpoint.close)
            return Child()
        consent = dict(instance=INSTANCE, run_id=f'run{n}', output='DP-2', recovery_output='DP-1')
        with patch('subprocess.Popen', side_effect=spawn):
            result = run_managed(t, consent, runtime, desktops=[WIDE] * n)
        return result, seen, t, path

    def test_run_and_teardown_at_limits(self):
        for n in (1, 6):
            with self.subTest(n=n):
                result, seen, t, path = self.run_count(n)
                self.assertEqual((seen['ops'], seen['live']), (['confirmed'] * n, n))  # all outputs up before the renderer
                self.assertTrue(result['complete']); self.assertEqual(result['renderer_returncode'], 0)
                self.assertEqual(t.events.count('evacuate_remove'), n)
                self.assertEqual([r['state'] for r in Journal.open(path, f'run{n}', 'session').operations], ['done'] * n)

    def test_renderer_crash_still_removes_all_six(self):
        result, seen, t, path = self.run_count(6, returncode=-11)
        self.assertNotEqual(result['renderer_returncode'], 0)
        self.assertTrue(result['complete'])
        self.assertTrue(all(r['state'] == 'dead' for r in t.registry.values()))
        self.assertEqual([r['state'] for r in Journal.open(path, 'run6', 'session').operations], ['done'] * 6)


if __name__ == '__main__':
    unittest.main()
