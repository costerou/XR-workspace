"""Live desktop changes from a glass pill in the glasses: Ultrawide, Add new, Remove (tools/native_spike/desktops.py).

Offline: the broker is test_ownership's fake transport (plus `reshape`), the journal is the real durable one, the
supervisor is a stand-in recording guard pauses and replacements. Nothing here talks to a compositor.
"""
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.native_spike import desktops as dk, free_pointer as fp, geometry as g, profiles, view
from tools.native_spike.hyprland import create_outputs, plan_outputs, OwnershipBlocked
from tools.native_spike.journal import Journal, recover
from tools.native_spike.ownership import OwnershipBrokerClient
from test_ownership import Transport

ROOT = Path(__file__).resolve().parents[2]
D169 = {'width': 1920, 'height': 1080, 'scale': 1.0}
WIDE = {'width': 2560, 'height': 1080, 'scale': 1.0}
TILTED = {'x': -20, 'y': 10, 'distance': 120, 'curvature': 40}
FLAT = dict(g.DEFAULT_PLACEMENT)


def frames(raw):
    if not raw: return []
    return [json.loads(line) for line in bytes(raw).decode().splitlines()]


def ops(raw): return [f['op'] for f in frames(raw)]


class BrokerTransport(Transport):
    """test_ownership's fake broker, plus `reshape` (the plugin swaps the output's monitor rule) and refusals."""
    def __init__(self, path):
        super().__init__(path)
        self.refuse_remove = self.refuse_reshape = False
        self.wires = []

    def _request(self, wire):
        self.wires.append(wire)
        j = json.loads(wire[10:]); p = j['payload']
        if j['op'] == 'reshape':
            self.events.append('reshape')
            entry = self.registry[p['id']]
            assert entry['name'] == p['name'] and entry['state'] == 'live'
            if not self.refuse_reshape: entry['observation'].update({k: p[k] for k in ('width', 'height', 'scale', 'x', 'y')})
            else: entry['state'] = 'invalid'
            return json.dumps({**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, 'epoch': self.epoch,
                               'result': copy.deepcopy(entry)}).encode()
        if j['op'] == 'evacuate_remove' and self.refuse_remove:
            self.events.append('evacuate_remove')
            entry = {**copy.deepcopy(self.registry[p['id']]), 'state': 'blocked'}
            return json.dumps({**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, 'epoch': self.epoch,
                               'result': entry}).encode()
        return super()._request(wire)


class Run:
    """A journaled run with three created outputs, as run_managed leaves it before the renderer starts."""
    def __init__(self, test, desktops=None):
        tmp = tempfile.TemporaryDirectory(); test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.journal = Journal.open(self.dir / 'journal.json', 'run', 'session')
        self.transport = BrokerTransport(self.journal.path)
        self.broker = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')
        self.specs = plan_outputs([], [], 'run', desktops)
        create_outputs(self.specs, self.journal, self.broker)
        self.guards = []
        self.manager = dk.DesktopManager(self.broker, self.journal, self.specs, threads=False,
                                         guard_factory=lambda live: self.guards.append([s['name'] for s in live]) or ('guard', len(self.guards)))


# --- the rules ------------------------------------------------------------------------------------------------------
class LimitTests(unittest.TestCase):
    def test_max_desktops_is_one_number_everywhere(self):
        self.assertEqual(g.MAX_DESKTOPS, 4)
        hpp = (ROOT / 'spike/include/xr/desktops.hpp').read_text()
        self.assertEqual(int(re.search(r'kMaxDesktops = (\d+);', hpp).group(1)), g.MAX_DESKTOPS)
        model = (ROOT / 'omarchy-plugin/cody.viture-workspace/Model.js').read_text()
        self.assertEqual(int(re.search(r'const MAX_DESKTOPS = (\d+);', model).group(1)), g.MAX_DESKTOPS)
        registry = (ROOT / 'spike/ownership/registry.cpp').read_text()  # the broker's highest output index
        self.assertIn(f'p["index"]>{g.MAX_DESKTOPS - 1}', registry)
        self.assertEqual(fp.MAX_DESKTOPS, g.MAX_DESKTOPS)
        for pattern in (view.MANAGED,):  # FOCUSEDMON went with cursor follow
            self.assertIn(f'[1-{g.MAX_DESKTOPS}]', pattern.pattern)

    def test_ultrawide_toggles_at_the_desktops_own_height(self):
        cases = {(1920, 1080, 1.0): (2560, 1080), (2560, 1440, 1.25): (3440, 1440), (3840, 2160, 1.5): (5118, 2160),
                 (1600, 900, 1.0): (2136, 900)}
        for (w, h, s), (uw, uh) in cases.items():
            with self.subTest((w, h, s)):
                wide = g.toggle_ultrawide({'width': w, 'height': h, 'scale': s})
                self.assertEqual((wide['width'], wide['height'], wide['scale']), (uw, uh, s))
                self.assertTrue(g.is_ultrawide(wide))
                self.assertEqual(g.validate_desktop(wide), wide)  # always a desktop the broker accepts
                back = g.toggle_ultrawide(wide)
                self.assertFalse(g.is_ultrawide(back))
                self.assertEqual((back['width'], back['height'], back['scale']), (w, h, s))
        self.assertTrue(g.is_ultrawide({'width': 3840, 'height': 1080, 'scale': 1.0}))  # 32:9 counts: Ultrawide -> 16:9
        self.assertEqual(g.toggle_ultrawide({'width': 3840, 'height': 1080, 'scale': 1.0})['width'], 1920)
        tall = g.toggle_ultrawide({'width': 640, 'height': 4320, 'scale': 1.0})
        self.assertEqual(g.validate_desktop(tall), tall)
        self.assertEqual(g.toggle_ultrawide({'width': 7680, 'height': 4320, 'scale': 1.0})['width'], 7680)  # clamped
        with self.assertRaises(ValueError): g.toggle_ultrawide({'width': 1921, 'height': 1080, 'scale': 1.0})


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.specs = plan_outputs([], [], 'run')  # XR-1..3 at x 0, 1920, 3840
        self.placements = [TILTED, FLAT, FLAT]

    def test_ultrawide_widens_in_place_and_slides_the_right_neighbours_right_to_left(self):
        p = dk.plan(self.specs, self.placements, 'ultrawide', 0)
        self.assertEqual([(s['name'], s['width'], s['x']) for s in p.specs], [('XR-1', 2560, 0), ('XR-2', 1920, 2560), ('XR-3', 1920, 4480)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-3', 'XR-2', 'XR-1'])  # the right end makes room first
        self.assertEqual(p.resized, ['XR-1'])
        self.assertEqual((p.create, p.remove, p.focus), ([], None, 0))
        self.assertEqual([s['id'] for s in p.specs], [s['id'] for s in self.specs])  # the same outputs
        self.assertEqual(p.placements, self.placements)
        back = dk.plan(p.specs, p.placements, 'ultrawide', 0)  # and back: shrink first, then the neighbours slide left
        self.assertEqual([s['name'] for s in back.reshape], ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual([(s['width'], s['x']) for s in back.specs], [(1920, 0), (1920, 1920), (1920, 3840)])

    def test_ultrawide_of_the_last_desktop_touches_only_it(self):
        p = dk.plan(self.specs, self.placements, 'ultrawide', 2)
        self.assertEqual([s['name'] for s in p.reshape], ['XR-3'])

    def test_add_takes_the_lowest_free_number_at_the_end_of_the_row(self):
        p = dk.plan(self.specs, self.placements, 'add', 1)
        self.assertEqual([s['name'] for s in p.specs], ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        (new,) = p.create
        self.assertEqual((new['name'], new['index'], new['x'], new['width'], new['refresh_millihz']), ('XR-4', 3, 5760, 1920, 120000))
        self.assertEqual(p.reshape, [])
        self.assertEqual(p.placements, self.placements + [FLAT])
        self.assertEqual(p.focus, 3)  # the panel moves to the new desktop
        full = plan_outputs([], [], 'run', [D169] * 4)
        with self.assertRaisesRegex(ValueError, 'at most 4'): dk.plan(full, [FLAT] * 4, 'add', 0)

    def test_add_copies_the_pills_desktop_but_never_as_ultrawide(self):
        specs = plan_outputs([], [], 'run', [D169, WIDE, {'width': 2560, 'height': 1440, 'scale': 1.25}])
        self.assertEqual(dk.desktop_of(dk.plan(specs, [FLAT] * 3, 'add', 2).create[0]), {'width': 2560, 'height': 1440, 'scale': 1.25})
        self.assertEqual(dk.desktop_of(dk.plan(specs, [FLAT] * 3, 'add', 1).create[0]), D169)

    def test_add_fills_a_gap_and_the_right_part_of_the_row_moves_first(self):
        gap = [s for s in self.specs if s['name'] != 'XR-2']
        gap[1] = {**gap[1], 'x': 1920}  # XR-3 slid left when XR-2 went
        p = dk.plan(gap, [FLAT, TILTED], 'add', 0)
        self.assertEqual([(s['name'], s['x']) for s in p.specs], [('XR-1', 0), ('XR-2', 1920), ('XR-3', 3840)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-3'])
        self.assertEqual([s['name'] for s in p.create], ['XR-2'])
        self.assertEqual([k for k, _ in p.steps()], ['reshape', 'create'])
        self.assertEqual(p.placements, [FLAT, FLAT, TILTED])  # XR-3 keeps its own placement
        self.assertEqual(p.focus, 1)

    def test_remove_drops_it_and_closes_the_gap_left_to_right(self):
        p = dk.plan(self.specs, [FLAT, TILTED, FLAT], 'remove', 0)
        self.assertEqual(p.remove['name'], 'XR-1')
        self.assertEqual([(s['name'], s['x']) for s in p.specs], [('XR-2', 0), ('XR-3', 1920)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-2', 'XR-3'])
        self.assertEqual([k for k, _ in p.steps()], ['remove', 'reshape', 'reshape'])
        self.assertEqual((p.placements, p.focus), ([TILTED, FLAT], None))
        last = dk.plan(plan_outputs([], [], 'run', [D169]), [FLAT], 'ultrawide', 0)  # one desktop: still reshaped
        self.assertEqual(len(last.specs), 1)
        with self.assertRaisesRegex(ValueError, 'at least one'): dk.plan(plan_outputs([], [], 'run', [D169]), [FLAT], 'remove', 0)

    def test_the_row_starts_where_the_run_started(self):
        specs = [{**s, 'x': s['x'] + 2560, 'y': -40} for s in self.specs]
        p = dk.plan(specs, self.placements, 'remove', 0)
        self.assertEqual([(s['x'], s['y']) for s in p.specs], [(2560, -40), (4480, -40)])
        self.assertEqual([(s['x'], s['y']) for s in dk.plan(p.specs, [FLAT, FLAT], 'add', 0, origin=(2560, -40)).specs],
                         [(2560, -40), (4480, -40), (6400, -40)])

    def test_bad_requests(self):
        for args in (('zoom', 0), ('add', 3), ('add', -1), ('add', True)):
            with self.subTest(args), self.assertRaises(ValueError): dk.plan(self.specs, self.placements, *args)
        with self.assertRaises(ValueError): dk.plan(self.specs, [FLAT], 'add', 0)

    def test_no_two_outputs_ever_share_layout_space(self):
        """Every intermediate layout while the steps run is overlap-free (Hyprland lays the cursor's path out of it)."""
        def overlaps(layout):
            spans = sorted((s['x'], s['x'] + g.logical_width(dk.desktop_of(s))) for s in layout.values())
            return any(a[1] > b[0] for a, b in zip(spans, spans[1:]))
        start = plan_outputs([], [], 'run', [D169, WIDE, {'width': 2560, 'height': 1440, 'scale': 1.25}])
        for action in dk.ACTIONS:
            for index in range(3):
                with self.subTest(action=action, index=index):
                    p = dk.plan(start, [FLAT] * 3, action, index)
                    layout = {s['name']: s for s in start}
                    for kind, spec in p.steps():
                        if kind == 'remove': del layout[spec['name']]
                        else: layout[spec['name']] = spec
                        self.assertFalse(overlaps(layout), (kind, spec['name'], layout))
                    self.assertEqual(sorted(layout), [s['name'] for s in p.specs])


# --- the broker steps -----------------------------------------------------------------------------------------------
class ManagerTests(unittest.TestCase):
    def test_add_is_journaled_before_the_broker_creates_it(self):
        r = Run(self)
        r.transport.events.clear()
        self.assertTrue(r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'add', 0)))
        change, error, done = r.manager.take_result()
        self.assertIsNone(error)
        self.assertEqual(done, ['create'])
        self.assertEqual(r.transport.events, ['create', 'inspect'])
        self.assertEqual(r.manager.names(), ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        records = Journal.open(r.journal.path, 'run', 'session').operations
        self.assertEqual([(x['operation']['name'], x['state']) for x in records][-1], ('XR-4', 'confirmed'))
        self.assertEqual(r.manager.guard(), ('guard', 1))
        self.assertEqual(r.guards, [['XR-1', 'XR-2', 'XR-3', 'XR-4']])
        self.assertTrue(recover(r.journal, r.broker).complete)  # recovery removes the added output with the others
        self.assertEqual(r.transport.events.count('evacuate_remove'), 4)

    def test_remove_is_the_recovery_transaction_then_done_in_the_journal(self):
        r = Run(self)
        r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'remove', 1))
        _, error, done = r.manager.take_result()
        self.assertIsNone(error)
        self.assertEqual(done, ['remove', 'reshape'])  # XR-3 slides left into XR-2's place
        self.assertEqual(r.manager.names(), ['XR-1', 'XR-3'])
        self.assertEqual([s['x'] for s in r.manager.specs], [0, 1920])
        states = {x['operation']['name']: x['state'] for x in Journal.open(r.journal.path, 'run', 'session').operations}
        self.assertEqual(states, {'XR-1': 'confirmed', 'XR-2': 'done', 'XR-3': 'confirmed'})
        r.transport.events.clear()
        self.assertTrue(recover(r.journal, r.broker).complete)
        self.assertEqual(r.transport.events.count('evacuate_remove'), 2)  # the removed one is left alone
        # and its number is free again
        r2 = Run(self)
        r2.manager.start(dk.plan(r2.manager.specs, [FLAT] * 3, 'remove', 1)); r2.manager.take_result()
        r2.manager.start(dk.plan(r2.manager.specs, [FLAT] * 2, 'add', 0))
        self.assertIsNone(r2.manager.take_result()[1])
        self.assertEqual(r2.manager.names(), ['XR-1', 'XR-2', 'XR-3'])

    def test_ultrawide_reshapes_through_the_broker_and_reads_back(self):
        r = Run(self)
        r.transport.events.clear()
        r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'ultrawide', 1))
        _, error, done = r.manager.take_result()
        self.assertIsNone(error)
        self.assertEqual(r.transport.events, ['reshape', 'inspect', 'reshape', 'inspect'])
        self.assertEqual(r.manager.wide(), [False, True, False])
        self.assertEqual(r.manager.desktops()[1], WIDE)

    def test_a_refused_removal_changes_nothing(self):
        r = Run(self)
        r.transport.refuse_remove = True
        r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'remove', 0))
        _, error, done = r.manager.take_result()
        self.assertIn('could not be removed', error)
        self.assertEqual((done, r.manager.names()), ([], ['XR-1', 'XR-2', 'XR-3']))
        self.assertTrue(all(x['state'] == 'confirmed' for x in Journal.open(r.journal.path, 'run', 'session').operations))

    def test_a_reshape_that_does_not_read_back_is_kept_untrusted(self):
        r = Run(self)
        r.transport.refuse_reshape = True
        r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'ultrawide', 2))
        _, error, done = r.manager.take_result()
        self.assertIsNotNone(error)
        self.assertEqual(r.manager.desktops()[2], WIDE)  # the guard checks the geometry asked for, and fails the run
        self.assertTrue(recover(r.journal, r.broker).complete)

    def test_one_change_at_a_time_and_none_after_close(self):
        r = Run(self)
        change = dk.plan(r.manager.specs, [FLAT] * 3, 'add', 0)
        self.assertTrue(r.manager.start(change))
        self.assertTrue(r.manager.busy())
        self.assertFalse(r.manager.start(change))  # its result is not collected yet
        r.manager.take_result()
        self.assertIsNone(r.manager.take_result())
        r.manager.close()
        self.assertFalse(r.manager.start(dk.plan(r.manager.specs, [FLAT] * 4, 'remove', 0)))

    def test_threaded_worker(self):
        r = Run(self)
        r.manager.threads = True
        r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, 'add', 2))
        dk.wait_idle(r.manager)
        self.assertIsNone(r.manager.take_result()[1])
        r.manager.close()

    def test_broker_requests_pass_the_actual_cpp_validator(self):
        binary = next((p for p in (ROOT / 'build/release/ownership_test', ROOT / 'build/native-spike/ownership_test') if p.is_file()), None)
        if binary is None: self.skipTest('opt-in ownership native build not present')
        r = Run(self)
        r.transport.wires.clear()
        for action, index in (('ultrawide', 0), ('add', 0), ('remove', 1)):
            r.manager.start(dk.plan(r.manager.specs, [FLAT] * len(r.manager.specs), action, index))
            self.assertIsNone(r.manager.take_result()[1])
        kinds = set()
        for wire in r.transport.wires:
            kinds.add(json.loads(wire[10:])['op'])
            checked = subprocess.run([str(binary), '--validate'], input=wire[2:].encode(), capture_output=True, timeout=5)
            self.assertEqual(checked.returncode, 0, wire)
        self.assertEqual(kinds, {'reshape', 'inspect', 'create', 'evacuate_remove'})

    def test_create_live_refuses_a_name_already_live(self):
        r = Run(self)
        with self.assertRaises(OwnershipBlocked): r.broker.create_live({**r.specs[0], 'id': '12345678-1234-4234-8234-123456789abc'}, r.journal)

    def test_profile_takes_the_new_desktops(self):
        r = Run(self)
        path = r.dir / 'profiles.json'
        r.manager.store = profiles.PlacementStore(path, 'beast')
        r.manager.start(dk.plan(r.manager.specs, [TILTED, FLAT, FLAT], 'ultrawide', 0)); r.manager.take_result()
        r.manager.save([TILTED, FLAT, FLAT])
        saved = profiles.load_profile(path, 'beast')
        self.assertEqual(saved['desktops'], [WIDE, D169, D169])
        self.assertEqual(saved['placements'], [TILTED, FLAT, FLAT])
        r.manager.start(dk.plan(r.manager.specs, [TILTED, FLAT, FLAT], 'remove', 2)); r.manager.take_result()
        r.manager.save([TILTED, FLAT])
        saved = profiles.load_profile(path, 'beast')
        self.assertEqual((len(saved['desktops']), len(saved['placements'])), (2, 2))


class ProfileTests(unittest.TestCase):
    def test_one_to_four_desktops_round_trip_and_bad_counts_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            base = profiles.load_profile(path, 'beast')
            for n in range(1, g.MAX_DESKTOPS + 1):
                profiles.save_profile(path, 'beast', {**base, 'desktops': [WIDE] * n, 'placements': [TILTED] * n})
                self.assertEqual(profiles.load_profile(path, 'beast')['desktops'], [WIDE] * n)
            for bad in ([], [D169] * 5):
                with self.assertRaises(ValueError): profiles.save_profile(path, 'beast', {**base, 'desktops': bad, 'placements': [FLAT] * len(bad)})
            with self.assertRaises(ValueError): profiles.save_profile(path, 'beast', {**base, 'desktops': [D169] * 2, 'placements': [FLAT] * 3})
            raw = json.loads(path.read_text())
            raw['profiles']['beast']['desktops'] = [D169] * 6
            path.write_text(json.dumps(raw))
            self.assertEqual(len(profiles.load_profile(path, 'beast')['desktops']), 3)  # out of range: the default three
            raw['profiles']['beast'].update(desktops=[D169, WIDE], placements=[TILTED])  # mismatched placements: flat
            path.write_text(json.dumps(raw))
            self.assertEqual(profiles.load_profile(path, 'beast')['placements'], [FLAT, FLAT])

    def test_placement_store_keeps_one_per_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            store = profiles.PlacementStore(path, 'beast')
            store.save([TILTED] * 4)  # a run that had more desktops than the profile: cut to the profile's
            self.assertEqual(store.load(), [TILTED] * 3)
            store.save_desktops([D169, WIDE], [FLAT, TILTED])
            self.assertEqual(profiles.load_profile(path, 'beast')['desktops'], [D169, WIDE])
            store.save([TILTED])  # fewer: the tail stays
            self.assertEqual(store.load(), [TILTED, TILTED])


# --- the supervisor loop ------------------------------------------------------------------------------------------
class SupervisorStandIn:
    def __init__(self): self.calls = []
    def pause_guard(self): self.calls.append('pause')
    def replace_guard(self, guard): self.calls.append(('replace', guard))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(self)
        self.now = 100.0
        self.saved = []
        self.told = []
        self.listened = []
        state = view.ViewState(['XR-1', 'XR-2', 'XR-3'], placements=[TILTED, FLAT, FLAT])
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, None, None, clock=lambda: self.now)
        self.c.desktops = self.run.manager
        self.c.notify = self.told.append
        self.c.desktop_listeners.append(self.listened.append)
        self.sup = SupervisorStandIn()
        self.c.attach_supervisor(self.sup)
        self.run.manager.store = mock.Mock()

    def press(self, desktop, action):
        return self.c.desktop_action(desktop, action)

    def collect(self):
        out = self.c.poll(self.now)
        self.c.join_focus()
        return out

    def test_buttons_act_only_on_the_open_panel(self):
        self.assertEqual(self.press(0, 'add'), b'')  # closed
        self.c.state.open_menu(1)
        self.assertEqual(self.press(0, 'add'), b'')  # another desktop's panel
        self.assertEqual(self.sup.calls, [])

    def test_ultrawide_end_to_end(self):
        self.c.state.open_menu(1)
        before = frames(self.press(1, 'ultrawide'))
        self.assertEqual([f['op'] for f in before], ['placement_menu'])
        self.assertTrue(before[0]['payload']['busy'])
        self.assertEqual(self.sup.calls, ['pause'])
        after = frames(self.collect())
        self.assertEqual([f['op'] for f in after], ['desktops', 'view', 'placement', 'placement_menu'])
        self.assertEqual(after[0]['payload'], {'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-2']})  # XR-3 only slides
        menu = after[3]['payload']
        self.assertEqual((menu['open'], menu['desktop'], menu['wide'], menu['busy'], menu['names']), (True, 1, True, False, [1, 2, 3]))
        self.assertEqual(self.sup.calls[1], ('replace', ('guard', 1)))
        self.run.manager.store.save_desktops.assert_called_once_with([D169, WIDE, D169], [TILTED, FLAT, FLAT])
        self.assertEqual(self.listened, [['XR-1', 'XR-2', 'XR-3']])
        self.assertEqual(self.c.status_reply({})['desktops'], [D169, WIDE, D169])

    def test_add_moves_the_panel_to_the_new_desktop(self):
        self.c.state.open_menu(0)
        self.press(0, 'add')
        after = frames(self.collect())
        self.assertEqual(after[0]['payload'], {'outputs': ['XR-1', 'XR-2', 'XR-3', 'XR-4']})
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        self.assertEqual(self.c.state.placements, [TILTED, FLAT, FLAT, FLAT])
        self.assertEqual((self.c.state.menu_open, self.c.state.menu_desktop), (True, 3))
        self.assertEqual(after[2]['payload']['desktops'], [TILTED, FLAT, FLAT, FLAT])
        self.c.state.open_menu(3)
        self.press(3, 'add')  # at four: says why, starts nothing
        self.assertEqual(self.told[-1], 'at most 4 desktops')
        self.assertEqual(self.sup.calls.count('pause'), 1)

    def test_remove_asks_twice_and_lets_go_of_the_capture_first(self):
        self.c.state.open_menu(0)
        armed = frames(self.press(0, 'remove'))
        self.assertEqual([f['op'] for f in armed], ['placement_menu'])
        self.assertTrue(armed[0]['payload']['armed'])
        self.assertEqual(self.sup.calls, [])  # nothing happens on the first press
        self.now += 1
        first = frames(self.press(0, 'remove'))
        self.assertEqual([f['op'] for f in first], ['desktops', 'view', 'placement', 'placement_menu'])
        self.assertEqual(first[0]['payload'], {'outputs': ['XR-2', 'XR-3']})  # before the broker removes XR-1
        self.assertFalse(first[3]['payload']['open'])  # its panel is gone
        self.assertEqual(first[2]['payload']['desktops'], [FLAT, FLAT])
        after = frames(self.collect())
        self.assertEqual(after[0]['payload'], {'outputs': ['XR-2', 'XR-3']})  # they only slid left: no capture restarts
        self.assertEqual(self.c.state.names, ['XR-2', 'XR-3'])
        self.assertEqual(after[3]['payload']['names'], [2, 3])
        self.assertEqual(self.run.manager.names(), ['XR-2', 'XR-3'])

    def test_remove_disarms_after_a_while_and_on_switching_panels(self):
        self.c.state.open_menu(0)
        self.press(0, 'remove')
        self.now += dk.REMOVE_ARM_S + 0.1
        self.assertFalse(frames(self.c.poll(self.now))[-1]['payload']['armed'])
        frames(self.press(0, 'remove'))
        self.assertTrue(self.c.state.remove_armed)
        self.c.on_message({'op': 'menu_desktop_next'})
        self.assertFalse(self.c.state.remove_armed)
        self.assertEqual(self.sup.calls, [])

    def test_a_refused_removal_puts_the_desktop_back(self):
        self.run.transport.refuse_remove = True
        self.c.state.open_menu(2)
        self.press(2, 'remove'); self.press(2, 'remove')
        after = frames(self.collect())
        self.assertEqual(after[0]['payload']['outputs'], ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual(self.c.state.placements, [TILTED, FLAT, FLAT])
        self.assertIn('could not be removed', self.told[-1])
        self.assertEqual(self.sup.calls[-1][0], 'replace')

    def test_keys_are_the_buttons(self):
        self.c.state.open_menu(1)
        self.assertEqual(ops(self.c.on_message({'op': 'menu_ultrawide'})), ['placement_menu'])
        self.collect()
        self.assertEqual(self.c.state.wide, [False, True, False])
        self.c.on_message({'op': 'menu_delete'})
        self.assertTrue(self.c.state.remove_armed)
        self.c.on_message({'op': 'menu_toggle'})  # closing disarms
        self.assertFalse(self.c.state.remove_armed)
        self.assertIsNone(self.c.on_message({'op': 'menu_add'}))  # closed: nothing
        self.c.state.typing = True
        self.c.state.open_menu(0)
        self.assertIsNone(self.c.on_message({'op': 'menu_add'}))  # typing mode releases these keys

    def test_one_change_at_a_time(self):
        self.run.manager.threads = True
        gate = __import__('threading').Event()
        original = self.run.broker.create_live
        self.run.broker.create_live = lambda *a: (gate.wait(5), original(*a))[1]
        self.c.state.open_menu(0)
        self.press(0, 'add')
        self.assertTrue(self.c.state.desktops_busy)
        self.assertEqual(self.press(0, 'ultrawide'), b'')
        self.assertEqual(self.c.poll(self.now), None)  # still working
        gate.set(); dk.wait_idle(self.run.manager)
        self.assertIn('desktops', ops(self.c.poll(self.now)))
        self.assertFalse(self.c.state.desktops_busy)
        self.run.manager.close()


class HeldNameTests(unittest.TestCase):
    """Regression (ultrawide-add-monitor, 2026-11-03, live run): one ultrawide XR-1 left, and Add said "output name
    already live" every time. An earlier Add's create of XR-2 had failed after its write-ahead journal record, which
    stays pending until recovery (journal.live_outputs counts it), but plan() picked the lowest XR-n missing from the
    run's outputs only, so every Add asked for XR-2 again. Add now takes the lowest name nothing holds."""
    def setUp(self):
        self.run = Run(self, [WIDE])
        self.run.journal.prepare({'kind': 'output', 'id': '7538a1bc-d92e-4c3e-94e3-27cfdc576726', 'name': 'XR-2',
                                  'index': 1, 'before': None})  # a failed create's record, as the live journal held it
        self.told = []
        state = view.ViewState(['XR-1'], placements=[FLAT])
        state.wide = [True]
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, None, None, clock=lambda: 100.0)
        self.c.desktops, self.c.notify = self.run.manager, self.told.append
        self.c.attach_supervisor(SupervisorStandIn())

    def test_add_skips_a_name_the_journal_still_holds(self):
        self.assertEqual(self.run.manager.held(), {'XR-1', 'XR-2'})
        self.c.state.open_menu(0)
        self.c.desktop_action(0, 'add')
        self.c.poll(100.0); self.c.join_focus()
        self.assertEqual(self.told, [])
        self.assertEqual(self.run.manager.names(), ['XR-1', 'XR-3'])
        self.assertEqual(self.run.manager.wide(), [True, False])  # the new one 16:9, beside the ultrawide
        self.assertEqual((self.c.state.names, self.c.state.menu_desktop), (['XR-1', 'XR-3'], 1))
        self.assertEqual([(o['name'], o['index']) for o in self.run.journal.live_outputs()],
                         [('XR-1', 0), ('XR-2', 1), ('XR-3', 2)])  # recovery still has the failed one

    def test_plan_takes_the_lowest_name_nothing_holds(self):
        specs = self.run.manager.specs
        self.assertEqual(dk.plan(specs, [FLAT], 'add', 0).create[0]['name'], 'XR-2')  # nothing else held
        made = dk.plan(specs, [FLAT], 'add', 0, held={'XR-2'}).create[0]
        self.assertEqual((made['name'], made['index'], made['x']), ('XR-3', 2, specs[0]['x'] + 2560))
        with self.assertRaises(ValueError): dk.plan(specs, [FLAT], 'add', 0, held={'XR-2', 'XR-3', 'XR-4'})


class PointerTests(unittest.TestCase):
    def test_the_desktop_row_is_hit_on_the_open_panel(self):
        for i, name in enumerate(fp.PANEL_ACTIONS):
            x, y, w, h = fp.action_rect(i)
            self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 2), ('action', 2, name))
            self.assertEqual(fp.panel_hit(x - 1, y + h / 2, 2)[0] if i == 0 else fp.panel_hit(x - 1, y + h / 2, 2)[0], 'panel')
        self.assertEqual(fp.panel_hit(30, fp.PANEL_ROWS_TOP + 5, 0), ('panel', 0))  # a label: nothing
        self.assertEqual(fp.panel_hit(fp.PANEL_KNOB_LO, fp.PANEL_ROWS_TOP + 5, 0)[0], 'slider')
        x, y, w, h = fp.action_rect(len(fp.PANEL_ACTIONS) - 1)
        self.assertLessEqual(x + w, fp.PANEL_W - fp.PANEL_PAD + 1e-6)
        self.assertLessEqual(y + h, fp.PANEL_H)

    def test_a_press_on_a_button_is_that_action(self):
        control = mock.Mock()
        control.state = view.ViewState(['XR-1', 'XR-2'])
        control.state.open_menu(1)
        control.desktop_action.return_value = b''
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        pointer.mode, pointer.pointer, pointer.geometry = 'free', (10.0, 10.0), {'w': 100, 'h': 100}
        pointer.outputs = [{'index': 0}]
        with mock.patch.object(fp.OverviewPointer, '_hit', return_value=('action', 1, 'add')), \
                mock.patch.object(fp.OverviewPointer, 'active', return_value=True), \
                mock.patch.object(fp.OverviewPointer, '_handle', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_pills', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_pointer_frame', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_roll_frame', return_value=b''):  # both roll icons always show now
            pointer.on_op('resize_press')
        control.desktop_action.assert_called_once_with(1, 'add')
        control.pill_close.assert_not_called()  # a press on its own panel never collapses it
        self.assertEqual(pointer.actions, 1)

    def test_desktop_changes_drop_drags_and_stale_layouts(self):
        control = mock.Mock(); control.state = view.ViewState(['XR-1'])
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        pointer.drag, pointer.outputs, pointer.desktop = {'kind': 'pill'}, [{'index': 2}], 2
        pointer.set_geometry({'w': 1, 'tiles': [(0, 0, 1, 1)] * 3})
        pointer.desktops_changed()
        self.assertEqual((pointer.drag, pointer.outputs, pointer.desktop), (None, [], None))
        # The last layout is kept (the renderer may never report it again: dot-cursor-new-desktop), but one with a slot
        # per old desktop is not hit-tested with until the renderer's new one comes.
        self.assertEqual(pointer.geometry['w'], 1)
        self.assertFalse(pointer._layout_ready())
        pointer.set_geometry({'w': 2, 'tiles': [(0, 0, 1, 1)]})
        self.assertTrue(pointer._layout_ready())


class WireTests(unittest.TestCase):
    def test_desktops_frame(self):
        f = frames(view.desktops_frame('run', 's', 7, ['XR-3', 'XR-1'], ['XR-3']))[0]
        self.assertEqual((f['op'], f['payload']), ('desktops', {'outputs': ['XR-1', 'XR-3'], 'renew': ['XR-3']}))
        self.assertEqual(frames(view.desktops_frame('run', 's', 7, ['XR-4']))[0]['payload'], {'outputs': ['XR-4']})
        for names, renew in (([], ()), (['XR-5'], ()), (['XR-1', 'XR-1'], ()), (['DP-1'], ()), (['XR-1'], ['XR-2']),
                             (['XR-1', 'XR-2', 'XR-3', 'XR-4', 'XR-1'], ())):
            with self.subTest(names), self.assertRaises(ValueError): view.desktops_frame('run', 's', 7, names, renew)

    def test_renderer_decodes_the_frames_we_send(self):
        """ipc_test covers the C++ side; here: the op is in the renderer's decoder and in the panel's key ops."""
        src = (ROOT / 'spike/src/ipc.cpp').read_text()
        self.assertIn('name == "desktops"', src)
        from tools.native_spike import ipc, xr_control
        for op in ('menu_ultrawide', 'menu_add', 'menu_delete', 'send_glasses_4'):
            self.assertIn(op, xr_control.OPS)
            self.assertEqual(ipc.decode_control((json.dumps({'schema': 1, 'request_id': 1, 'run_id': 'r', 'session': 's', 'op': op,
                                                             'payload': {}}) + '\n').encode())['op'], op)
        self.assertEqual({op for _, op in view.MENU_BINDS} & set(view.MENU_DESKTOP_OPS), set(view.MENU_DESKTOP_OPS))

    def test_send_to_a_fourth_desktop(self):
        from tools.native_spike import send
        self.assertEqual(send.target_for('send_glasses_4', ['XR-1', 'XR-4'], 'XR-1', 'DP-1'), 'XR-4')
        s = send.WindowSender(mock.Mock(), ['XR-1', 'XR-2'], 'DP-1')
        s.set_names(['XR-1', 'XR-4'])
        self.assertEqual(s.names, ['XR-1', 'XR-4'])


class SupervisorGuardTests(unittest.TestCase):
    def test_pause_and_replace_swap_the_guard_worker(self):
        from tools.native_spike.supervisor import Supervisor
        child = mock.Mock(); child.poll.return_value = None
        s = Supervisor(child, lambda: None, guard=None)
        with mock.patch.object(Supervisor, '_start_guard') as start, mock.patch.object(Supervisor, '_stop_guard') as stop:
            s.pause_guard()
            self.assertTrue(s.guard_paused); stop.assert_called_once(); start.assert_not_called()
            s.replace_guard('new guard')
            self.assertFalse(s.guard_paused); self.assertEqual(s.guard, 'new guard'); start.assert_called_once()
        self.assertEqual(s.guard_restarts, 1)
        s._check_guard(0.0)  # no worker: nothing to check, never a disconnect
        self.assertIsNone(s.stopping_at)


if __name__ == '__main__':
    unittest.main()
