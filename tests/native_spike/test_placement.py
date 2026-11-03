"""Per-desktop placement (position round you, distance, curvature): rules, profile storage, wire, live path.

Offline only: no compositor, renderer binary or glasses. Placement rendering is covered by spike/tests/view_test.cpp.
"""
import copy
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import unittest

from tools.native_spike import geometry as g, profiles, view
from tools.native_spike.ipc import decode_control, PrivateServer

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['XR-1', 'XR-2', 'XR-3']
TILTED = {'x': -20, 'y': 10, 'distance': 120, 'curvature': 40}


def frame(op, payload, request_id=5):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': 'run', 'session': 'session', 'op': op,
                        'payload': payload}) + '\n').encode()


class PlacementRuleTests(unittest.TestCase):
    def test_default_is_flat_and_valid(self):
        self.assertEqual(g.validate_placement(g.DEFAULT_PLACEMENT), g.DEFAULT_PLACEMENT)
        self.assertEqual(g.DEFAULT_PLACEMENT['curvature'], 0)
        self.assertEqual(g.DEFAULT_PLACEMENT['distance'], 100)

    def test_bounds_inclusive(self):
        for key, (lo, hi) in g.PLACEMENT_BOUNDS.items():
            for v in (lo, hi):
                self.assertEqual(g.validate_placement({**g.DEFAULT_PLACEMENT, key: v})[key], v)
            for v in (lo - 1, hi + 1, float(lo), True, None, str(lo)):
                with self.subTest(key=key, v=v), self.assertRaises(ValueError):
                    g.validate_placement({**g.DEFAULT_PLACEMENT, key: v})

    def test_exact_keys(self):
        for bad in ({**g.DEFAULT_PLACEMENT, 'z': 0}, {k: v for k, v in g.DEFAULT_PLACEMENT.items() if k != 'curvature'}, [], None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                g.validate_placement(bad)

    def test_list_of_three(self):
        self.assertEqual(g.validate_placements([TILTED] * 3), [TILTED] * 3)
        for bad in ([TILTED] * 2, [TILTED] * 4, TILTED, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                g.validate_placements(bad)

    def test_mirrored_bounds_match(self):
        """Renderer (ipc.cpp) and panel (Model.js) carry the same table; drift would reject or clip valid values."""
        cpp = (ROOT / 'spike/src/ipc.cpp').read_text()
        js = (ROOT / 'omarchy-plugin/cody.viture-workspace/Model.js').read_text()
        for key, (lo, hi) in g.PLACEMENT_BOUNDS.items():
            with self.subTest(key=key):
                self.assertRegex(cpp, r'\{"%s",\s*%d,\s*%d\}' % (key, lo, hi))
                self.assertRegex(js, r'\b%s:\s*\[%d,\s*%d\]' % (key, lo, hi))
        self.assertEqual(re.findall(r'\{"(\w+)",\s*-?\d+,\s*-?\d+\}', cpp), list(g.PLACEMENT_KEYS))


class PlacementProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cfg' / 'profiles.json'

    def write(self, profile_by_model):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps({'schema': 2, 'profiles': profile_by_model}))

    def test_defaults_flat(self):
        self.assertEqual(profiles.load_profile(self.path, 'beast')['placements'], [g.DEFAULT_PLACEMENT] * 3)

    def test_profile_saved_before_placements_loads(self):
        old = {k: v for k, v in profiles.DEFAULT_PROFILE.items() if k != 'placements'}
        self.write({'beast': {**old, 'spacing': 80}})
        p = profiles.load_profile(self.path, 'beast')
        self.assertEqual((p['spacing'], p['placements']), (80, [g.DEFAULT_PLACEMENT] * 3))

    def test_a_profile_saved_with_the_removed_facing_settings_keeps_the_rest(self):
        # The user's real profile had yaw +-30 and distance 102: the facing settings are gone (every desktop faces you),
        # position, distance and curvature are kept, and the next save writes the four fields only.
        old = [{**TILTED, 'yaw': 30, 'pitch': 0, 'roll': 0}, {**g.DEFAULT_PLACEMENT, 'distance': 102, 'yaw': -30, 'pitch': 5, 'roll': 2},
               {**TILTED, 'yaw': 999, 'pitch': 0, 'roll': 0}]  # an out-of-range removed setting does not matter any more
        self.write({'luma-ultra': {**profiles.DEFAULT_PROFILE, 'placements': old}})
        loaded = profiles.load_profile(self.path, 'luma-ultra')['placements']
        self.assertEqual(loaded, [TILTED, {**g.DEFAULT_PLACEMENT, 'distance': 102}, TILTED])
        profiles.PlacementStore(self.path, 'luma-ultra').save(loaded)
        saved = json.loads(self.path.read_text())['profiles']['luma-ultra']['placements']
        self.assertTrue(all(set(q) == set(g.PLACEMENT_KEYS) for q in saved))
        self.assertEqual(g.legacy_placement({**TILTED, 'yaw': 1}), {**TILTED, 'yaw': 1})  # not the old shape: left to validation
        with self.assertRaises(ValueError): g.validate_placement({**TILTED, 'yaw': 0, 'pitch': 0, 'roll': 0})  # never on the wire

    def test_invalid_placement_falls_back_alone(self):
        self.write({'beast': {**profiles.DEFAULT_PROFILE, 'placements': [TILTED, {**TILTED, 'curvature': 999}, TILTED]}})
        self.assertEqual(profiles.load_profile(self.path, 'beast')['placements'], [TILTED, g.DEFAULT_PLACEMENT, TILTED])

    def test_saved_per_glasses_model(self):
        store = profiles.PlacementStore(self.path, 'luma-ultra')
        store.save([TILTED, g.DEFAULT_PLACEMENT, TILTED])
        self.assertEqual(store.load(), [TILTED, g.DEFAULT_PLACEMENT, TILTED])
        self.assertEqual(profiles.PlacementStore(self.path, 'beast').load(), [g.DEFAULT_PLACEMENT] * 3)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), '0o600')

    def test_placement_store_preserves_other_fields(self):
        profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'spacing': 120, 'tracking': False})
        profiles.PlacementStore(self.path, 'beast').save([TILTED] * 3)
        p = profiles.load_profile(self.path, 'beast')
        self.assertEqual((p['spacing'], p['tracking'], p['placements']), (120, False, [TILTED] * 3))
        profiles.ProfileStore(self.path, 'beast').save({**view.DEFAULT_SETTINGS, 'spacing': 60})  # view saves keep placements
        self.assertEqual(profiles.load_profile(self.path, 'beast')['placements'], [TILTED] * 3)

    def test_save_rejects_invalid(self):
        for bad in ([TILTED] * 2, [{**TILTED, 'curvature': -1}] * 3, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'placements': bad})
        self.assertFalse(self.path.exists())


class PlacementWireTests(unittest.TestCase):
    def test_control_socket_accepts_placements(self):
        msg = decode_control(frame('placement', {'desktops': [TILTED, g.DEFAULT_PLACEMENT]}))
        self.assertEqual(msg['payload']['desktops'], [TILTED, g.DEFAULT_PLACEMENT])

    def test_control_socket_rejects_bad_placements(self):
        for payload in ({}, {'desktops': []}, {'desktops': [TILTED] * 4}, {'desktops': [TILTED], 'extra': 1},
                        {'desktops': [{**TILTED, 'distance': 201}]}, {'desktops': [{**TILTED, 'x': 1.5}]}, {'desktops': TILTED}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_control(frame('placement', payload))

    def test_renderer_frame(self):
        raw = view.placement_frame('run', 'session', 9, [TILTED])
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(json.loads(raw), {'schema': 1, 'request_id': 9, 'run_id': 'run', 'session': 'session', 'op': 'placement',
                                           'payload': {'desktops': [TILTED]}})
        with self.assertRaises(ValueError): view.placement_frame('run', 'session', 9, [{**TILTED, 'curvature': 121}])


class MemoryStore:
    def __init__(self, value=None): self.value, self.saved = value or [dict(g.DEFAULT_PLACEMENT) for _ in range(3)], []
    def load(self): return copy.deepcopy(self.value)
    def save(self, value): self.value = copy.deepcopy(value); self.saved.append(copy.deepcopy(value))


class PlacementControlTests(unittest.TestCase):
    def control(self, names=NAMES, initial=None, store=None):
        return view.ViewControl(view.ViewState(names, placements=initial), 'run', 'session', lambda n: None, None, None,
                                placements=store)

    def test_initial_frame_only_when_not_flat(self):
        flat = self.control()
        self.assertEqual([json.loads(l)['op'] for l in flat.initial_frame().splitlines()], ['view'])
        tilted = self.control(initial=[TILTED, g.DEFAULT_PLACEMENT, g.DEFAULT_PLACEMENT])
        frames = [json.loads(l) for l in tilted.initial_frame().splitlines()]
        self.assertEqual([f['op'] for f in frames], ['view', 'placement'])
        self.assertEqual(frames[1]['payload']['desktops'][0], TILTED)
        self.assertEqual([f['request_id'] for f in frames], [2, 3])

    def test_live_placement_applied_and_saved(self):
        store = MemoryStore()
        c = self.control(store=store)
        out = json.loads(c.on_message({'op': 'placement', 'payload': {'desktops': [TILTED] * 3}}))
        self.assertEqual((out['op'], out['payload']['desktops']), ('placement', [TILTED] * 3))
        self.assertEqual(store.saved, [[TILTED] * 3])
        self.assertEqual(c.status_reply({})['placements'], [TILTED] * 3)
        self.assertIsNone(c.on_message({'op': 'placement', 'payload': {'desktops': [TILTED] * 3}}))  # unchanged
        self.assertEqual(len(store.saved), 1)

    def test_invalid_live_placement_ignored(self):
        store = MemoryStore()
        c = self.control(store=store)
        for payload in ({'desktops': [TILTED] * 2}, {'desktops': [{**TILTED, 'distance': 49}] * 3}, None, {'desktops': 'x'}):
            with self.subTest(payload=payload):
                self.assertIsNone(c.on_message({'op': 'placement', 'payload': payload}))
        self.assertEqual((store.saved, c.state.placements), ([], [g.DEFAULT_PLACEMENT] * 3))

    def test_fewer_outputs_keep_saved_tail(self):
        store = MemoryStore([dict(g.DEFAULT_PLACEMENT), dict(g.DEFAULT_PLACEMENT), TILTED])
        c = self.control(names=['XR-1'], initial=store.load(), store=store)
        self.assertEqual(c.state.placements, [g.DEFAULT_PLACEMENT])
        c.on_message({'op': 'placement', 'payload': {'desktops': [TILTED]}})
        self.assertEqual(store.value, [TILTED, g.DEFAULT_PLACEMENT, TILTED])

    def test_save_failure_still_applies(self):
        class Broken(MemoryStore):
            def save(self, value): raise OSError('read-only')
        c = self.control(store=Broken())
        self.assertIsNotNone(c.on_message({'op': 'placement', 'payload': {'desktops': [TILTED] * 3}}))

    def test_real_controller_units_to_renderer_channel(self):
        """Controller Units.control -> run's private socket -> real Supervisor loop -> renderer frame and saved profile."""
        from tools.native_spike.supervisor import Supervisor
        from tools.xr_controller.__main__ import Units
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name) / 'rt'; runtime.mkdir(mode=0o700)
        path = Path(tmp.name) / 'profiles.json'
        server = PrivateServer(runtime, 'run', 'session'); self.addCleanup(server.close)
        store = profiles.PlacementStore(path, 'beast')
        control = view.ViewControl(view.ViewState(NAMES, placements=store.load()), 'run', 'session', lambda n: None,
                                   profiles.ProfileStore(path, 'beast'), server, placements=store)
        parent, renderer = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(renderer.close)
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def kill(self): self.done = True
        child = Child()
        sup = Supervisor(child, lambda: 'recovered', control=control)
        sent = []
        def drive():
            time.sleep(0.15)
            sent.append(Units(runtime).control('run', 'session', 'placement', {'desktops': [TILTED, TILTED, g.DEFAULT_PLACEMENT]}))
            time.sleep(0.2)
            sent.append(Units(runtime).control('run', 'session', 'status'))
            renderer.sendall(b'H'); time.sleep(0.1); child.done = True
        t = threading.Thread(target=drive); t.start()
        renderer.sendall(b'H')
        self.assertEqual(sup.run(parent), 'recovered')
        t.join(5)
        frames = [json.loads(l) for l in renderer.recv(65536).decode().splitlines()]
        self.assertEqual([f['op'] for f in frames], ['placement'])
        self.assertEqual(frames[0]['payload']['desktops'], [TILTED, TILTED, g.DEFAULT_PLACEMENT])
        self.assertIs(sent[0], True)
        self.assertEqual(sent[1]['placements'], [TILTED, TILTED, g.DEFAULT_PLACEMENT])
        self.assertEqual(profiles.load_profile(path, 'beast')['placements'], [TILTED, TILTED, g.DEFAULT_PLACEMENT])


if __name__ == '__main__':
    unittest.main()
