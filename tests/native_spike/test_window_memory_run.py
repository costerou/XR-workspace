"""Window memory on the real paths of a run: _run_owned's start order, view_controls' last capture, Ctrl+Alt+R through the real
ViewControl and DesktopManager (it must not wipe the memory, and XR-4's windows go to XR-3), a pill's Remove and Add (the same
desktop back, its windows with it), and the reset leaving the memory's file alone.

Offline: test_desktops' fake broker and real journal, a fake compositor answering from its own state, temporary profiles."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import bind_check, desktops as dk, free_pointer as fp, geometry as g, managed, profiles, view, window_memory as wm
from test_desktops import Run, SupervisorStandIn
from test_window_memory import mon, win, ws, record, memory as memory_of

MODEL = 'beast'
D169 = dict(g.DEFAULT_DESKTOP)
FLAT = dict(g.DEFAULT_PLACEMENT)
PLACED = [{'x': -450, 'y': 30, 'distance': 80, 'curvature': 40}, {'x': 0, 'y': -120, 'distance': 140, 'curvature': 0},
          {'x': 1800, 'y': 0, 'distance': 100, 'curvature': 120}, {'x': 600, 'y': 900, 'distance': 10, 'curvature': 7}]
ROLLS = [250, -1200, 0, 900]
XR4 = ['XR-1', 'XR-2', 'XR-3', 'XR-4']


class Compositor:
    """Answers j/ queries from `state`; place_windows only records (the evals are test_window_memory_lua's)."""
    instance = 'inst'

    def __init__(self, clients, spaces, monitors):
        self.state = {'clients': clients, 'workspaces': spaces, 'monitors': monitors, 'activewindow': {}}
        self.calls = []

    def query(self, kind, timeout=None):
        return self.state[kind]

    def place_windows(self, placement, phase):
        self.calls.append((phase, placement))


def four_monitors():
    return [mon('DP-1')] + [mon(n, x=2000 * (i + 1), active=61 + 10 * i) for i, n in enumerate(XR4)]


class StartOrderTests(unittest.TestCase):
    def setUp(self):
        from test_managed import ManagedTransport
        from tools.native_spike.journal import Journal
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.path.parent.mkdir(parents=True, mode=0o700); self.path.parent.parent.chmod(0o700)
        self.journal = Journal.open(self.path, 'run', 'session')
        env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.dir / 'state')})  # the layout snapshot
        env.start(); self.addCleanup(env.stop)
        spaces, clients = [ws(1, 'DP-1'), ws(2, 'DP-1')], [win('0xa', 1), win('0xb', 2, cls='other')]
        self.order = order = []
        class Transport(ManagedTransport):
            def query(self, kind, timeout=None):
                if kind == 'workspaces': return spaces
                if kind == 'clients': return clients
                if kind == 'monitors': return [mon('DP-1'), mon('XR-1', x=2000), mon('XR-2', x=4000), mon('XR-3', x=6000)]
                if kind == 'activewindow': return {'address': '0xa'}
                return super().query(kind)
            def place_windows(self, placement, phase): order.append(phase)
            def adopt_windows(self, moves): order.append('adopt')
            def connect_wayland(self, session): return open(os.devnull, 'rb')
        self.Transport = Transport
        self.store = profiles.ProfileStore(self.dir / 'profiles.json', MODEL)
        wm.Store(wm.memory_path(self.store.path), MODEL).save(memory_of(record('0xa', 'XR-2', 1, 71)))

    def start(self, store):
        from tools.native_spike.hyprland import plan_outputs
        from tools.native_spike.ownership import OwnershipBrokerClient
        transport = self.Transport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1'); broker.require_output_ownership()
        class Stop(Exception): pass
        order = self.order
        def controls(*a, **k): order.append('controls'); self.windows = a[8]; raise Stop
        with mock.patch.object(managed, 'view_controls', controls), mock.patch.object(managed, 'recover'), \
                mock.patch('tools.native_spike.restore.take', lambda *a: order.append('snapshot')), \
                mock.patch.object(managed, 'recover_restoring', return_value=mock.Mock(complete=True, remaining=[])), \
                self.assertRaises(Stop):
            managed._run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'), guard=lambda: None,
                               capture=['XR-1', 'XR-2', 'XR-3'], adopt=True, store=store)

    def test_the_arrangement_is_saved_then_memory_then_adoption_then_settling_then_the_renderer(self):
        self.start(self.store)
        self.assertEqual(self.order, ['snapshot', 'moves', 'adopt', 'settle', 'controls'])

    def test_the_run_keeps_the_tracker(self):
        self.start(self.store)
        self.assertIsInstance(self.windows, wm.WindowTracker)

    def test_a_run_without_a_glasses_profile_has_no_memory(self):
        self.start(profiles.LayoutStore(self.dir / 'layout.json'))
        self.assertEqual(self.order, ['snapshot', 'adopt', 'controls'])
        self.assertIsNone(self.windows)

    def test_nothing_remembered_is_exactly_as_before(self):
        wm.memory_path(self.store.path).unlink()
        self.start(self.store)
        self.assertEqual(self.order, ['snapshot', 'adopt', 'controls'])

    def test_the_memory_sits_beside_the_profiles(self):
        t = wm.tracker_for(self.Transport(self.path), self.store, 'DP-1', ['XR-1'])
        self.assertEqual(t.store.path, self.dir / 'window-memory.json')
        self.assertEqual(t.store.model, MODEL)
        self.assertIsNone(wm.tracker_for(object(), self.store, 'DP-1', ['XR-1']))


class EndTests(unittest.TestCase):
    def test_view_controls_takes_the_last_capture_after_the_setup_and_wires_the_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            for name in ('lock_pointer', 'enter_desktop'): delattr(client, name)
            run = Run(self)
            store = profiles.ProfileStore(Path(tmp) / 'profiles.json', MODEL)
            tracker = mock.Mock()
            order = []
            tracker.close.side_effect = lambda: order.append('window memory')
            real = view.ViewControl.save_setup
            def save(c): order.append('setup'); real(c)
            with mock.patch.object(view.ViewControl, 'save_setup', save), \
                    managed.view_controls(client, 'run', 'session', run.manager.names(), store=store, desktops=run.manager, windows=tracker):
                self.assertIs(run.manager.leaving, tracker.leaving)
                self.assertEqual(order, [])
            self.assertEqual(order, ['setup', 'window memory'])


class Live(unittest.TestCase):
    """A running four-desktop session: the real ViewControl and DesktopManager, a real overview pointer, and the tracker on
    a compositor whose state the test moves as Hyprland and the broker would."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / 'profiles.json'
        self.run = Run(self, [D169] * 4)
        self.store = profiles.PlacementStore(self.path, MODEL)
        profiles.save_profile(self.path, MODEL, profiles.DEFAULT_PROFILE)
        self.store.save_desktops(self.run.manager.desktops(), PLACED, ROLLS)
        self.now = 100.0
        self.told = []
        self.compositor = Compositor(
            [win('0xa', 91, at=(8000, 26), cls='foot', title='vim'), win('0xb', 91, at=(8400, 26), cls='foot', title='zsh'),
             win('0xc', 81, at=(6000, 26), cls='firefox', title='docs'), win('0xd', 61, at=(2000, 26), cls='mpv', title='film')],
            [ws(61, 'XR-1'), ws(81, 'XR-3'), ws(91, 'XR-4')], four_monitors())
        self.memory = wm.Store(wm.memory_path(self.path), MODEL)
        self.tracker = wm.WindowTracker(self.compositor, self.memory, 'DP-1', XR4, clock=lambda: self.now, threads=False)
        state = view.ViewState(self.run.manager.names(), placements=[dict(p) for p in PLACED])
        settings = profiles.ProfileStore(self.path, MODEL)
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, settings, None, clock=lambda: self.now, placements=self.store)
        self.c.desktops = self.run.manager
        self.run.manager.store = self.store
        self.run.manager.move_windows = lambda source, target: self.land(86, target)
        state.wide, state.sizes = self.run.manager.wide(), self.run.manager.desktops()
        self.c.notify = self.told.append
        self.c.attach_supervisor(SupervisorStandIn())
        self.pointer = self.c.overview_pointer = fp.OverviewPointer(self.c, clock=lambda: self.now)
        self.addCleanup(self.pointer.close)
        self.pointer.rolls[:4] = ROLLS
        # As managed.view_controls wires it.
        self.c.windows = self.tracker
        self.tracker.context = self.c.window_context
        self.c.desktop_listeners.append(self.tracker.set_names)
        self.run.manager.leaving = self.tracker.leaving
        self.tick(0)
        self.tracker.mark(0)
        self.tick(0)
        self.first = self.memory.load()

    def tick(self, dt):
        self.now += dt
        out = self.c.poll(self.now)
        self.c.join_focus()
        return out

    def land(self, workspace, monitor):
        """What the removal does to XR-4's windows."""
        c = self.compositor.state
        c['workspaces'] = [w for w in c['workspaces'] if w['id'] != 91] + [ws(workspace, monitor)]
        for w in c['clients']:
            if w['workspace']['id'] == 91: w['workspace'] = {'id': workspace, 'name': str(workspace)}
        c['monitors'] = [m for m in c['monitors'] if m['name'] != 'XR-4']

    def reset(self):
        self.assertIsNone(self.c.on_message({'op': view.RESET_OP}))
        self.c.on_message({'op': view.RESET_OP})
        self.c.join_focus()
        for _ in range(4): self.tick(view.RESET_ANIM_S)
        self.tick(wm.CAPTURE_DELAY_S)
        self.tick(wm.CAPTURE_DELAY_S)

    def add(self):
        """The pill's Add on XR-1: the free name is XR-4 again."""
        self.compositor.state['monitors'] = self.compositor.state['monitors'] + [mon('XR-4', x=8000, active=91)]
        self.c.state.open_menu(0)
        self.assertTrue(self.c.desktop_action(0, 'add'))
        self.tick(0)

    def saved(self):
        return {w['address']: w for w in self.memory.load()['windows']}


class ResetTests(Live):
    def test_the_memory_knows_the_context_before_the_reset(self):
        self.assertEqual(self.first['desktops']['XR-4']['placement'], PLACED[3])
        self.assertEqual(self.first['desktops']['XR-2']['roll'], -1200)

    def test_a_reset_does_not_wipe_the_memory(self):
        self.reset()
        self.assertEqual(sorted(self.saved()), ['0xa', '0xb', '0xc', '0xd'])

    def test_a_removed_desktops_windows_go_to_xr3_and_stay_remembered_for_xr4(self):
        moved = []
        self.run.manager.move_windows = lambda source, target: (moved.append((source, target)), self.land(86, target))
        self.reset()
        self.assertEqual(moved, [('XR-4', 'XR-3')])
        by = self.saved()
        self.assertEqual((by['0xa']['parked'], by['0xa']['output'], by['0xa']['workspace'], by['0xa']['landing']), (True, 'XR-4', 91, 'XR-3'))
        self.assertFalse(by['0xc']['parked'])

    def test_windows_on_desktops_that_stay_are_kept_where_they_are(self):
        self.reset()
        by = self.saved()
        self.assertEqual((by['0xc']['output'], by['0xc']['workspace']), ('XR-3', 81))
        self.assertEqual((by['0xd']['output'], by['0xd']['workspace']), ('XR-1', 61))

    def test_the_profile_reset_never_touches_the_memory_file(self):
        before = wm.memory_path(self.path).read_bytes()
        profiles.PlacementStore(self.path, MODEL).reset()
        self.assertEqual(wm.memory_path(self.path).read_bytes(), before)

    def test_the_next_start_sends_them_to_xr3_by_the_same_rule(self):
        self.reset()
        self.tracker.close()
        clients = [win(a, 1, cls=c, title=t) for a, c, t in (('0xa', 'foot', 'vim'), ('0xb', 'foot', 'zsh'))]
        fresh = Compositor(clients, [ws(1, 'DP-1')], four_monitors()[:4])
        t = wm.WindowTracker(fresh, self.memory, 'DP-1', view.ViewState(['XR-1', 'XR-2', 'XR-3']).names, threads=False)
        t.place(['XR-1', 'XR-2', 'XR-3'])
        p = fresh.calls[0][1]
        self.assertEqual(p['whole'], [(1, 'XR-3', 91)])
        self.assertTrue(all(x['moved'] for x in p['placed']))

    def test_adding_xr4_after_a_reset_brings_its_windows_back(self):
        self.reset()
        self.add()
        moved = [p for phase, p in self.compositor.calls if phase == 'moves']
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]['whole'], [(86, 'XR-4', 91)])
        self.assertEqual(sorted(p['address'] for p in moved[0]['placed']), ['0xa', '0xb'])

    def test_after_coming_home_a_window_is_remembered_there_as_usual(self):
        self.reset()
        self.add()
        c = self.compositor.state  # what the eval did
        c['workspaces'] = [w for w in c['workspaces'] if w['id'] != 86] + [ws(91, 'XR-4')]
        for w in c['clients']:
            if w['workspace']['id'] == 86: w['workspace'] = {'id': 91, 'name': '91'}
        self.tracker.close()
        by = self.saved()
        self.assertEqual((by['0xa']['output'], by['0xa']['parked'], by['0xa']['index']), ('XR-4', False, 3))


class RemoveTests(Live):
    def setUp(self):
        super().setUp()
        self.c.state.open_menu(3)

    def remove(self):
        self.c.desktop_action(3, 'remove')
        self.c.desktop_action(3, 'remove')
        self.land(5, 'DP-1')  # the broker's evacuation
        self.tick(0)
        self.tick(wm.CAPTURE_DELAY_S)

    def test_the_memory_parks_them_on_the_physical_monitor(self):
        self.remove()
        by = self.saved()
        self.assertEqual((by['0xa']['parked'], by['0xa']['landing'], by['0xa']['output']), (True, 'DP-1', 'XR-4'))

    def test_the_same_desktop_added_again_takes_them_back(self):
        self.remove()
        self.add()
        moved = [p for phase, p in self.compositor.calls if phase == 'moves']
        self.assertEqual(moved[0]['whole'], [(5, 'XR-4', 91)])


class ManagerOrderTests(unittest.TestCase):
    def test_the_memory_hears_of_a_removal_before_anything_moves(self):
        run = Run(self, [D169] * 4)
        order = []
        run.manager.leaving = lambda name, target: order.append(('leaving', name, target))
        run.manager.move_windows = lambda source, target: order.append(('move', source, target))
        change = dk.plan(run.manager.specs, [FLAT] * 4, dk.RESET, 0, held=run.manager.held())
        run.manager.start(change)
        self.assertEqual(order, [('leaving', 'XR-4', 'XR-3'), ('move', 'XR-4', 'XR-3')])

    def test_a_tracker_that_cannot_read_never_stops_a_removal(self):
        run = Run(self, [D169] * 4)
        client = mock.Mock()
        client.query.side_effect = OSError('gone')
        with tempfile.TemporaryDirectory() as tmp:
            tracker = wm.WindowTracker(client, wm.Store(Path(tmp) / 'w.json', MODEL), 'DP-1', XR4, threads=False)
            run.manager.leaving = tracker.leaving
            change = dk.plan(run.manager.specs, [FLAT] * 4, 'remove', 3, held=run.manager.held())
            run.manager.start(change)
            result = run.manager.take_result()
        self.assertIsNone(result[1])
        self.assertEqual(run.manager.names(), XR4[:3])


class BindCheckTests(unittest.TestCase):
    def test_the_generated_evals_load_in_hyprland_with_the_binds(self):
        lua = bind_check.harness_lua('/tmp/x/hyprland.lua', '/tmp/x/out.tsv')
        for line in wm.verify_lua(): self.assertIn(line, lua)

    def test_the_sample_covers_every_dispatcher_the_evals_use(self):
        code = '\n'.join(wm.verify_lua())
        for call in ('hl.dsp.layout("consume")', 'hl.dsp.layout("expel")', 'hl.dsp.window.swap', 'hl.dsp.window.float', 'hl.dsp.window.resize',
                     'hl.dsp.window.fullscreen_state', 'hl.dsp.workspace.move', 'hl.dsp.workspace.change_id', 'hl.dsp.cursor.move', 'hl.dsp.focus',
                     'colresize'):
            self.assertIn(call, code)


class DocsTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_the_docs_describe_it(self):
        self.assertIn('window-memory.json', (self.ROOT / 'docs/technical.md').read_text())
        self.assertIn('window-memory.json', (self.ROOT / 'README.md').read_text())

    def test_the_glasses_script_exists(self):
        plan = (self.ROOT / 'docs/placement-menu-test-plan.md').read_text()
        self.assertIn('## Window memory: automated coverage and glasses test script', plan)
        self.assertIn('| W1 |', plan)
        self.assertIn('| W16 |', plan)


if __name__ == '__main__':
    unittest.main()
