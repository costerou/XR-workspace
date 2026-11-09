"""Immersive 3D, the supervisor's side (tools/native_spike/immersive.py; the renderer's: spike/tests/immersive_test.cpp,
glass_contract_test.py): the setting and its defaults, saving per glasses profile, Ctrl+Alt+I in the central bind table and on
the help card, the 3D button's hit zone, the renderer frames, and above all the engine and glasses lifecycle: nothing runs
unless a desktop is 3D, the order of going up and down, and the fall back to 2D from every point, with a toast."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, immersive, ipc, keybinds, managed, profiles, view, xr_control
from tools.native_spike.immersive import EngineError, ImmersiveRun, NoDeviceSbs, ProcessEngine, SbsError
from tools.native_spike.profiles import LayoutStore, ProfileStore

NAMES = ['XR-1', 'XR-2', 'XR-3']
def full(every, desktops):
    """A whole setting with the default look: the tests about which desktops are 3D do not care about it."""
    return {**immersive.DEFAULTS, 'global': every, 'desktops': desktops}


OFF = full(False, {})
ALL = full(True, {})
FIXTURE = Path(__file__).parent / 'fixtures' / 'immersive_payloads.json'
WAIT_S = 5.0


def wait_for(test, what='condition'):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if test(): return True
        time.sleep(0.002)
    raise AssertionError(f'timed out: {what}')


class FakeEngine:
    """The Engine interface, recording its calls in `log`."""
    def __init__(self, log=None, fail=None, gate=None):
        self.log, self.fail, self.gate, self.up = log if log is not None else [], fail, gate, False

    def start(self, desktops, cancelled=lambda: False):
        self.log.append(('start', list(desktops)))
        while self.gate is not None and not self.gate.is_set():
            if cancelled(): return False
            time.sleep(0.001)
        if self.fail is not None: raise self.fail
        self.up = True
        return True

    def set_desktops(self, desktops):
        self.log.append(('set', list(desktops)))

    def alive(self):
        return self.up

    def stop(self):
        self.log.append(('stop',))
        self.up = False


class FakeSbs:
    def __init__(self, log, enter_fails=None, leave_fails=None):
        self.log, self.enter_fails, self.leave_fails = log, enter_fails, leave_fails

    def enter(self, cancelled=None):
        self.log.append(('enter',))
        if self.enter_fails is not None: raise self.enter_fails

    def leave(self):
        self.log.append(('leave',))
        if self.leave_fails is not None: raise self.leave_fails


class Store:
    def __init__(self): self.saved = []
    def save(self, settings): self.saved.append(settings)


def frames(data):
    return [json.loads(line) for line in (data or b'').split(b'\n') if line]


def immersive_frames(data):
    """The frames' payloads without their `params`, which LookTests is about."""
    return [{k: v for k, v in f['payload'].items() if k != 'params'} for f in frames(data) if f['op'] == 'immersive']


class SettingTests(unittest.TestCase):
    def test_off_by_default_everywhere(self):
        self.assertEqual(immersive.DEFAULTS, OFF)
        self.assertEqual(view.DEFAULT_SETTINGS['immersive'], OFF)
        self.assertEqual(profiles.DEFAULT_PROFILE['immersive'], OFF)
        self.assertEqual(view.ViewState(NAMES).immersive, OFF)
        self.assertEqual(immersive.on_names(OFF, NAMES), [])

    def test_anything_invalid_reads_as_off_and_never_raises(self):
        for bad in (None, 3, [], 'on', {'global': 1}, {'desktops': 'XR-1'}, {'global': None, 'desktops': []}):
            self.assertEqual(immersive.clean(bad), OFF, bad)
        self.assertEqual(immersive.clean(full(True, {'XR-9': False, 'HDMI-A-1': False, 3: False, 'XR-2': 'no'})), ALL)

    def test_an_exception_that_says_nothing_is_dropped(self):
        self.assertEqual(immersive.clean(full(True, {'XR-1': True, 'XR-2': False})),
                         full(True, {'XR-2': False}))
        self.assertFalse(immersive.is_valid(full(True, {'XR-1': True})))
        self.assertRaises(ValueError, immersive.validate, {'global': False})
        self.assertRaises(ValueError, immersive.validate, {'global': False, 'desktops': {}, 'extra': 1})

    def test_one_desktop_and_all_of_them(self):
        one = immersive.toggled_desktop(OFF, 'XR-2')
        self.assertEqual(immersive.on_names(one, NAMES), ['XR-2'])
        self.assertEqual(immersive.toggled_desktop(one, 'XR-2'), OFF)  # no leftover exception
        self.assertEqual(immersive.toggled_all(OFF, NAMES), ALL)
        # All on, one off: that desktop is the exception, and a desktop added later follows `global`.
        most = immersive.toggled_desktop(ALL, 'XR-1')
        self.assertEqual(immersive.on_names(most, NAMES + ['XR-4']), ['XR-2', 'XR-3', 'XR-4'])

    def test_the_toggle_for_all_turns_off_anything_on_first(self):
        one = immersive.toggled_desktop(OFF, 'XR-2')
        self.assertEqual(immersive.toggled_all(one, NAMES), OFF)
        self.assertEqual(immersive.toggled_all(ALL, NAMES), OFF)
        self.assertEqual(immersive.toggled_all(immersive.toggled_desktop(ALL, 'XR-1'), NAMES), OFF)

    def test_the_tail_lights_everything_unless_everything_is_lit(self):
        one = immersive.toggled_desktop(OFF, 'XR-2')
        self.assertEqual(immersive.toggled_every(OFF, NAMES), ALL)
        self.assertEqual(immersive.toggled_every(one, NAMES), ALL)
        self.assertEqual(immersive.toggled_every(immersive.toggled_desktop(ALL, 'XR-1'), NAMES), ALL)
        self.assertEqual(immersive.toggled_every(ALL, NAMES), OFF)

    def test_it_applies_only_to_the_desktops_that_exist(self):
        only_gone = immersive.toggled_desktop(OFF, 'XR-4')
        self.assertEqual(immersive.on_names(only_gone, NAMES), [])
        self.assertEqual(immersive.toggled_all(only_gone, NAMES), ALL)

    def test_the_accessor_reads_a_dict_or_an_object(self):
        self.assertEqual(immersive.immersive({'immersive': ALL}), ALL)
        self.assertEqual(immersive.immersive({}), OFF)
        self.assertEqual(immersive.immersive(view.ViewState(NAMES, immersive=ALL)), ALL)


class SavedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'profiles.json'

    def test_each_glasses_model_keeps_its_own(self):
        profile = {**profiles.DEFAULT_PROFILE, 'immersive': full(False, {'XR-2': True})}
        profiles.save_profile(self.path, 'luma-ultra', profile)
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['immersive'], profile['immersive'])
        self.assertEqual(profiles.load_profile(self.path, 'beast')['immersive'], OFF)

    def test_the_run_saves_to_the_profile_and_the_next_start_reads_it(self):
        profiles.save_profile(self.path, 'luma-ultra', dict(profiles.DEFAULT_PROFILE))
        store = ProfileStore(self.path, 'luma-ultra')
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, store, None)
        control.immersive_toggle()
        self.assertEqual(store.load()['immersive'], ALL)
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['immersive'], ALL)

    def test_a_profile_from_before_the_feature_is_off(self):
        self.path.write_text(json.dumps({'version': 1, 'profiles': {'luma-ultra': {'spacing': 60}}}))
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['immersive'], OFF)

    def test_layout_json_for_cli_runs(self):
        store = LayoutStore(Path(self.dir.name) / 'layout.json')
        store.save({**view.DEFAULT_SETTINGS, 'immersive': ALL})
        self.assertEqual(store.load()['immersive'], ALL)
        Path(store.path).write_text(json.dumps({'immersive': {'global': 'yes'}}))
        self.assertEqual(store.load()['immersive'], OFF)


class BindTests(unittest.TestCase):
    def test_in_the_central_table(self):
        self.assertIn(immersive.TOGGLE_BIND, view.BINDS)
        self.assertIn(immersive.TOGGLE_OP, xr_control.OPS)
        self.assertIn(immersive.TOGGLE_OP, view.IGNORED_WHILE_TYPING)
        self.assertIn(immersive.TOGGLE_OP, keybinds.RUN_OPS)
        self.assertLessEqual(len(keybinds.label(immersive.TOGGLE_OP)), keybinds.MAX_LABEL)

    def test_no_other_run_key_uses_it(self):
        chords = [bind_check.normalize(c) for _, c, _ in bind_check.run_chords()]
        self.assertEqual(chords.count(bind_check.normalize(immersive.TOGGLE_BIND[0])), 1)

    def test_it_shares_the_backgrounds_row_and_the_card_stays_at_14(self):
        self.assertEqual(len(view.help_rows()), 14)
        self.assertIn(['Ctrl+Alt+E / I', 'Background / immersive 3D: on or off'], view.help_rows())
        self.assertLessEqual(len('Background / immersive 3D: on or off'), 42)  # a row's budget

    def test_a_moved_key_shows_on_the_row(self):
        binds = tuple(('CTRL + ALT + J', op) if op == immersive.TOGGLE_OP else (c, op) for c, op in view.BINDS)
        self.assertIn(['Ctrl+Alt+E / J', 'Background / immersive 3D: on or off'], view.help_rows(binds))


class FrameTests(unittest.TestCase):
    def test_the_fixture_is_what_the_supervisor_sends(self):
        data = json.loads(FIXTURE.read_text())
        for case in data['valid']:
            line = immersive.renderer_frame('r', 's', 9, case['flags'], case['stereo'], case['hover'], case['tail'], case['every'])
            self.assertEqual(json.loads(line)['payload'], case['payload'])
            self.assertTrue(line.endswith(b'\n'))

    def test_a_run_with_nothing_on_never_sends_one(self):
        # So a renderer from before this feature never sees the op.
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        self.assertEqual(immersive_frames(control.initial_frame()), [])
        self.assertFalse(control.poll())

    def test_a_desktop_added_or_removed_with_nothing_on_sends_none_either(self):
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        control.state.names = NAMES + ['XR-4']
        self.assertFalse(control.poll())
        control.state.names = NAMES[:1]
        self.assertFalse(control.poll())

    def test_a_saved_one_is_in_the_runs_first_frames(self):
        control = view.ViewControl(view.ViewState(NAMES, immersive=ALL), 'r', 's', None, Store(), None)
        self.assertEqual(immersive_frames(control.initial_frame()), [{'desktops': [True, True, True, False], 'global': True, 'stereo': False, 'hover': -1, 'tail': False}])

    def test_the_panel_op_decodes(self):
        frame = {'schema': 1, 'request_id': 1, 'run_id': 'run-1', 'session': 'session-1', 'op': immersive.SET_OP}
        self.assertEqual(ipc.decode_control(json.dumps({**frame, 'payload': ALL}).encode() + b'\n')['payload'], ALL)
        self.assertRaises(ValueError, ipc.decode_control, json.dumps({**frame, 'payload': {'global': 2}}).encode() + b'\n')
        toggle = {**frame, 'op': immersive.TOGGLE_OP, 'payload': {}}
        self.assertEqual(ipc.decode_control(json.dumps(toggle).encode() + b'\n')['op'], immersive.TOGGLE_OP)


G = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': [(0.0, 450.0, 586.0, 330.0), (626.0, 450.0, 586.0, 330.0), (1252.0, 450.0, 586.0, 330.0)]}
FLAT = {'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}


class ButtonTests(unittest.TestCase):
    """Connected Tail, Cody's pick (option 02 of 3d-button-mockups, a832d9a): the sizes are the mockup's own."""
    def centre(self, tile, rect=None):
        x, y, w, h = (rect or fp.immersive_main_rect)(tile)
        return x + w / 2, y + h / 2

    def test_joined_to_the_level_bar_on_its_line(self):
        for tile in G['tiles']:
            k = fp.level_bar_scale(tile)
            bx, by, bw, bh = fp.level_bar_rect(tile)
            self.assertEqual(fp.immersive_main_rect(tile), (bx + bw + 2 * k, by, 40 * k, bh))
            mx = fp.immersive_main_rect(tile)
            self.assertEqual(fp.immersive_tail_rect(tile), (mx[0] + mx[2] + 2 * k, by, 28 * k, bh))
            self.assertLess(by + bh, tile[1])

    def test_each_press_finds_its_control_on_that_desktop(self):
        for i, tile in enumerate(G['tiles']):
            for rect, part in ((fp.immersive_main_rect, 'main'), (fp.immersive_tail_rect, 'tail')):
                point = fp.project(G, tile, FLAT, *self.centre(tile, rect))
                self.assertEqual(fp.hit(G, [FLAT] * 3, point, icons=['bar'] * 3), ('immersive', i, part))

    def test_the_gaps_split_down_their_middles_and_the_bar_gives_way(self):
        tile = G['tiles'][1]
        k = fp.level_bar_scale(tile)
        bx, by, bw, bh = fp.level_bar_rect(tile)
        mx, _, mw, _ = fp.immersive_main_rect(tile)
        tx = fp.immersive_tail_rect(tile)[0]
        cy = by + bh / 2
        for x, want in ((bx + bw + 0.9 * k, ('roll', 1, 'cw')), (bx + bw + 1.1 * k, ('immersive', 1, 'main')), (mx + mw + 0.9 * k, ('immersive', 1, 'main')),
                        (mx + mw + 1.1 * k, ('immersive', 1, 'tail')), (tx + 1, ('immersive', 1, 'tail'))):
            point = fp.project(G, tile, FLAT, x, cy)
            self.assertEqual(fp.hit(G, [FLAT] * 3, point, icons=['bar'] * 3), want, x)

    def test_it_shows_only_with_the_bars(self):
        point = fp.project(G, G['tiles'][0], FLAT, *self.centre(G['tiles'][0]))
        self.assertNotEqual((fp.hit(G, [FLAT] * 3, point) or ('x',))[0], 'immersive')
        self.assertNotEqual((fp.hit(G, [FLAT] * 3, point, icons=['none'] * 3) or ('x',))[0], 'immersive')

    def test_it_is_a_control_with_the_plain_dot(self):
        self.assertEqual(fp.zone_of(('immersive', 1, 'tail')), 'control')
        self.assertIsNone(fp.look_for(('immersive', 1, 'main')))

    def test_the_segments_of_the_bar_keep_their_presses(self):
        tile = G['tiles'][1]
        x, y, w, h = fp.level_bar_rect(tile)
        for segment, fx in (('ccw', 1 / 6), ('level', 3 / 6), ('cw', 5 / 6)):
            point = fp.project(G, tile, FLAT, x + fx * w, y + h / 2)
            self.assertEqual(fp.hit(G, [FLAT] * 3, point, icons=['bar'] * 3), ('roll', 1, segment))

    def test_never_on_content_a_corner_or_a_narrow_desktop(self):
        for tile in G['tiles']:
            x, y, w, h = fp.immersive_tail_rect(tile)
            self.assertIsNone(fp.immersive_part(tile, (x + w / 2, tile[1] + 4)))
            self.assertLessEqual(x + w, tile[0] + tile[2] - fp.IMMERSIVE_CLEAR)
        narrow = (900.0, 500.0, 120.0, 68.0)
        self.assertEqual(fp.immersive_main_rect(narrow), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(fp.immersive_tail_rect(narrow), (0.0, 0.0, 0.0, 0.0))
        self.assertIsNone(fp.immersive_part(narrow, (950.0, 480.0)))
        self.assertEqual(fp.immersive_main_rect((0, 0, 0, 0)), (0.0, 0.0, 0.0, 0.0))
        self.assertIsNone(fp.immersive_part(G['tiles'][0], None))

    def test_a_press_turns_that_desktop_on_and_off(self):
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        control.overview_pointer = pointer
        pointer.geometry, pointer.mode, pointer.outputs = G, 'free', [{'index': i} for i in range(3)]
        pointer.pointer = fp.project(G, G['tiles'][1], FLAT, *self.centre(G['tiles'][1]))
        out = pointer.on_op('resize_press')
        self.assertEqual(immersive_frames(out)[0]['desktops'], [False, True, False, False])
        self.assertEqual(control.state.immersive, full(False, {'XR-2': True}))
        pointer.on_op('resize_release')
        self.assertEqual(control.state.immersive, full(False, {'XR-2': True}))  # the press did it, once
        pointer.on_op('resize_press')
        self.assertEqual(control.state.immersive, OFF)

    def test_a_press_on_the_tail_is_every_desktop(self):
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        control.overview_pointer = pointer
        pointer.geometry, pointer.mode, pointer.outputs = G, 'free', [{'index': i} for i in range(3)]
        pointer.pointer = fp.project(G, G['tiles'][2], FLAT, *self.centre(G['tiles'][2], fp.immersive_tail_rect))
        out = pointer.on_op('resize_press')
        self.assertEqual(immersive_frames(out)[0]['global'], True)
        self.assertEqual(control.state.immersive, ALL)
        pointer.on_op('resize_release')
        pointer.on_op('resize_press')
        self.assertEqual(control.state.immersive, OFF)

    def test_the_tail_is_lit_only_with_every_desktop_on_and_says_what_a_click_does(self):
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        out = control.immersive_click(1)
        self.assertFalse(immersive_frames(out)[0]['global'])
        out = control.immersive_tail_click(0)  # one on, the tail unlit: a press lights everything, as it looks like it will
        self.assertTrue(immersive_frames(out)[0]['global'])
        self.assertEqual(control.state.immersive, ALL)
        out = control.immersive_click(0)  # one off: no longer every desktop
        self.assertFalse(immersive_frames(out)[0]['global'])
        control.immersive_click(0)
        self.assertTrue(immersive_frames(control.immersive_tail_click(1))[0]['global'] is False)
        self.assertEqual(control.state.immersive, OFF)

    def test_the_control_under_the_dot_lights(self):
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        self.assertEqual(immersive_frames(control.immersive_pointer(2)), [{'desktops': [False] * 4, 'global': False, 'stereo': False, 'hover': 2, 'tail': False}])
        self.assertEqual(control.immersive_pointer(2), b'')
        self.assertTrue(immersive_frames(control.immersive_pointer(2, True))[0]['tail'])
        last = immersive_frames(control.immersive_pointer(-1))[0]
        self.assertEqual((last['hover'], last['tail']), (-1, False))

    def test_the_mockups_hover_frames_reach_the_pointer(self):
        control = mock.Mock()
        control.state = view.ViewState(NAMES)
        control.immersive_pointer.return_value = b''
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        pointer.overview = lambda: True
        pointer.mode, pointer.hover = 'free', ('immersive', 1, 'tail')
        pointer._immersive_hover(False)
        control.immersive_pointer.assert_called_with(1, True)
        pointer.hover = ('immersive', 1, 'main')
        pointer._immersive_hover(False)
        control.immersive_pointer.assert_called_with(1, False)
        pointer.hover = None
        pointer._immersive_hover(False)
        control.immersive_pointer.assert_called_with(-1)

    def test_a_stale_click_does_nothing(self):
        for setup in (lambda c: setattr(c.state, 'zoomed', True), lambda c: setattr(c.state, 'typing', True),
                      lambda c: setattr(c, 'video_active', True)):
            control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
            setup(control)
            self.assertEqual(control.immersive_click(0), b'')
            self.assertEqual(control.immersive_tail_click(0), b'')
            self.assertEqual(control.state.immersive, OFF)
        control = view.ViewControl(view.ViewState(NAMES), 'r', 's', None, Store(), None)
        for bad in (-1, 3, 'XR-1', None, True):
            self.assertEqual(control.immersive_click(bad), b'')
            self.assertEqual(control.immersive_tail_click(bad), b'')


class RunTests(unittest.TestCase):
    """ImmersiveRun on its own, on a real worker thread with fake adapters."""
    def run_of(self, engine=None, **sbs):
        self.log = engine.log if engine is not None else []
        engine = engine if engine is not None else FakeEngine(self.log)
        run = ImmersiveRun(engine, FakeSbs(self.log, **sbs))
        self.addCleanup(run.close)
        return run, engine

    def events(self, run, n=1):
        got = []
        def have():
            got.extend(run.take_events())
            return len(got) >= n
        wait_for(have, f'{n} events, got {got}')
        return got

    def test_poll_during_failure_cleanup_does_not_restart_the_engine(self):
        stopped, release = threading.Event(), threading.Event()
        class FailingEngine(FakeEngine):
            def stop(self):
                stopped.set()
                release.wait(2)
                super().stop()
        run, engine = self.run_of(FailingEngine(fail=EngineError('missing')))
        self.addCleanup(release.set)
        run.request(['XR-1'])
        self.assertTrue(stopped.wait(1))
        run.request(['XR-1'])  # loop has not received the failure yet
        release.set()
        self.assertEqual(self.events(run), [('failed', 'engine', 'missing')])
        time.sleep(0.03)
        self.assertEqual(run.starts, 1)
        self.assertEqual(run.take_events(), [])

    def test_nothing_runs_until_something_is_asked_for(self):
        before = threading.active_count()
        run, engine = self.run_of()
        run.request([])
        self.assertEqual(threading.active_count(), before)
        self.assertEqual(self.log, [])
        run.close()
        self.assertEqual(self.log, [])

    def test_up_is_the_engine_first_then_the_glasses(self):
        run, _ = self.run_of()
        run.request(['XR-1'])
        self.assertEqual(self.events(run), [('active', ['XR-1'])])
        self.assertEqual(self.log, [('start', ['XR-1']), ('enter',)])

    def test_down_is_the_glasses_first_then_the_engine(self):
        run, _ = self.run_of()
        run.request(['XR-1']); self.events(run); self.log.clear()
        run.request([])
        self.assertEqual(self.events(run), [('off',)])
        self.assertEqual(self.log, [('leave',), ('stop',)])

    def test_a_change_of_desktops_keeps_the_engine_and_the_mode(self):
        run, _ = self.run_of()
        run.request(['XR-1']); self.events(run); self.log.clear()
        run.request(['XR-1', 'XR-3'])
        self.assertEqual(self.events(run), [('active', ['XR-1', 'XR-3'])])
        self.assertEqual(self.log, [('set', ['XR-1', 'XR-3'])])

    def test_an_engine_that_will_not_start_never_touches_the_glasses(self):
        run, _ = self.run_of(FakeEngine(fail=EngineError('it is not installed')))
        run.request(['XR-1'])
        self.assertEqual(self.events(run), [('failed', 'engine', 'it is not installed')])
        self.assertEqual(self.log, [('start', ['XR-1']), ('stop',)])

    def test_glasses_that_will_not_switch_stop_the_engine(self):
        run, engine = self.run_of(enter_fails=SbsError('no reply'))
        run.request(['XR-1'])
        self.assertEqual(self.events(run), [('failed', 'sbs', 'no reply')])
        self.assertEqual(self.log[:2], [('start', ['XR-1']), ('enter',)])
        self.assertEqual(self.log[-1], ('stop',))
        self.assertFalse(engine.up)

    def test_an_engine_that_dies_takes_the_glasses_out_of_3d(self):
        run, engine = self.run_of()
        run.request(['XR-1']); self.events(run); self.log.clear()
        engine.up = False
        self.assertEqual(self.events(run), [('failed', 'died', 'it quit')])
        self.assertEqual(self.log, [('leave',), ('stop',)])

    def test_turning_it_off_while_it_loads_ends_the_wait(self):
        gate = threading.Event()
        run, _ = self.run_of(FakeEngine(gate=gate))
        run.request(['XR-1'])
        wait_for(lambda: self.log == [('start', ['XR-1'])])
        run.request([])
        wait_for(lambda: run._thread is not None and not run._current and not run.take_events() and ('enter',) not in self.log)
        time.sleep(0.05)
        self.assertNotIn(('enter',), self.log)

    def test_a_broken_adapter_is_a_failure_not_a_dead_worker(self):
        run, _ = self.run_of(FakeEngine(fail=KeyError('bug')))
        run.request(['XR-1'])
        self.assertEqual(self.events(run)[0][:2], ('failed', 'engine'))
        self.assertTrue(run._thread.is_alive())
        run2, _ = self.run_of(enter_fails=KeyError('bug'))
        run2.request(['XR-1'])
        self.assertEqual(self.events(run2)[0][:2], ('failed', 'sbs'))

    def test_a_refused_leave_still_stops_the_engine_and_says_so(self):
        run, engine = self.run_of(leave_fails=SbsError('no reply'))
        run.request(['XR-1']); self.events(run)
        run.request([])
        self.assertEqual(self.events(run, 2), [('leave', 'no reply'), ('off',)])
        self.assertFalse(engine.up)

    def test_a_reload_says_sbs_again_only_if_it_is_on(self):
        run, _ = self.run_of()
        run.reassert()
        run.request(['XR-1']); self.events(run); self.log.clear()
        run.reassert()
        wait_for(lambda: self.log == [('enter',)])

    def test_the_end_of_a_run_brings_it_all_down_before_recovery(self):
        run, _ = self.run_of()
        run.request(['XR-1']); self.events(run); self.log.clear()
        run.close()
        self.assertEqual(self.log, [('leave',), ('stop',)])
        self.assertFalse(run._thread.is_alive())
        run.request(['XR-2'])  # nothing starts after the end
        self.assertEqual(self.log, [('leave',), ('stop',)])

    def test_the_end_of_a_run_during_a_load_ends_the_wait(self):
        gate = threading.Event()
        run, _ = self.run_of(FakeEngine(gate=gate))
        run.request(['XR-1'])
        wait_for(lambda: self.log == [('start', ['XR-1'])])
        started = time.monotonic()
        run.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(run._thread.is_alive())
        self.assertNotIn(('enter',), self.log)

    def test_an_adapter_that_hangs_cannot_hold_recovery_up(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        log = []
        class Stuck(FakeSbs):
            def enter(self, cancelled=None):
                gate.wait(WAIT_S)
        engine = FakeEngine(log)
        run = ImmersiveRun(engine, Stuck(log))
        run.request(['XR-1'])
        wait_for(lambda: engine.up)
        started = time.monotonic()
        run.close(timeout=0.05)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(engine.up)


class ViewTests(unittest.TestCase):
    """ViewControl with a real ImmersiveRun: the toggle to the toast."""
    def setUp(self):
        self.log = []
        self.toasts = []
        self.store = Store()

    def control(self, engine=None, sbs=None, setting=None):
        c = view.ViewControl(view.ViewState(NAMES, immersive=setting), 'r', 's', None, self.store, None)
        self.engine = engine if engine is not None else FakeEngine(self.log)
        c.immersive_run = ImmersiveRun(self.engine, sbs if sbs is not None else FakeSbs(self.log))
        c.notify = self.toasts.append
        self.addCleanup(c.immersive_run.close)
        return c

    def until_stereo(self, c, want=True):
        wait_for(lambda: (c.poll(), c.immersive_stereo == want)[1], f'stereo {want}')

    def toasted(self):
        wait_for(lambda: self.toasts, 'a toast')
        return self.toasts[0]

    def test_the_key_brings_everything_up_and_lights_the_buttons_at_once(self):
        c = self.control()
        out = c.on_message({'op': immersive.TOGGLE_OP})
        self.assertEqual(immersive_frames(out), [{'desktops': [True, True, True, False], 'global': True, 'stereo': False, 'hover': -1, 'tail': False}])
        self.assertEqual(self.store.saved[-1]['immersive'], ALL)
        got = []
        def stereo():
            got.extend(immersive_frames(c.poll()))
            return got
        wait_for(stereo, 'the stereo frame')
        self.assertEqual(got, [{'desktops': [True, True, True, False], 'global': True, 'stereo': True, 'hover': -1, 'tail': False}])
        self.assertEqual(self.log[0], ('start', NAMES))
        self.assertFalse(c._immersive_status()['failed'])

    def test_off_tells_the_renderer_in_the_same_breath(self):
        c = self.control()
        c.on_message({'op': immersive.TOGGLE_OP}); self.until_stereo(c); self.log.clear()
        out = c.on_message({'op': immersive.TOGGLE_OP})
        self.assertEqual(immersive_frames(out), [{'desktops': [False] * 4, 'global': False, 'stereo': False, 'hover': -1, 'tail': False}])
        wait_for(lambda: self.log == [('leave',), ('stop',)], 'the worker to go down')

    def test_two_desktops_keep_one_engine_and_the_last_one_stops_it(self):
        c = self.control()
        c.immersive_click(0); self.until_stereo(c)
        c.immersive_click(2); c.poll()
        wait_for(lambda: ('set', ['XR-1', 'XR-3']) in self.log)
        self.assertEqual(sum(1 for e in self.log if e[0] == 'start'), 1)
        c.immersive_click(0); c.poll()
        self.assertTrue(c.immersive_stereo)
        out = c.immersive_click(2)
        self.assertFalse(immersive_frames(out)[0]['stereo'])
        wait_for(lambda: ('stop',) in self.log)

    def test_the_engine_failing_falls_back_with_a_toast_and_keeps_the_saved_setting(self):
        c = self.control(FakeEngine(self.log, fail=EngineError('it is not installed')))
        c.on_message({'op': immersive.TOGGLE_OP})
        out = b''
        def failed():
            nonlocal out
            out += c.poll() or b''
            return c.immersive_failed
        wait_for(failed, 'the failure')
        self.assertEqual(immersive_frames(out), [{'desktops': [False] * 4, 'global': False, 'stereo': False, 'hover': -1, 'tail': False}])
        self.assertEqual(self.toasted(), '3D is off: the depth engine could not start (it is not installed). Back to 2D.')
        self.assertEqual(c.state.immersive, ALL)
        self.assertEqual(c._immersive_status(), {'setting': ALL, 'names': [], 'stereo': False, 'failed': True, 'phase': 'off', 'installed': False, 'switching': False, 'button': 'R1'})

    def test_it_says_so_once_and_does_not_retry_on_its_own(self):
        c = self.control(FakeEngine(self.log, fail=EngineError('no')))
        c.on_message({'op': immersive.TOGGLE_OP})
        wait_for(lambda: (c.poll(), c.immersive_failed)[1])
        for _ in range(20): c.poll()
        time.sleep(0.05)
        for _ in range(20): c.poll()
        self.assertEqual(sum(1 for e in self.log if e[0] == 'start'), 1)
        self.toasted()
        self.assertEqual(len(self.toasts), 1)

    def test_the_next_press_tries_again_with_the_same_desktops(self):
        c = self.control(FakeEngine(self.log, fail=EngineError('no')))
        c.immersive_click(1)
        wait_for(lambda: (c.poll(), c.immersive_failed)[1])
        self.engine.fail = None
        c.on_message({'op': immersive.TOGGLE_OP})
        self.assertFalse(c.immersive_failed)
        self.assertEqual(c.state.immersive, full(False, {'XR-2': True}))
        self.until_stereo(c)

    def test_a_button_press_after_a_failure_tries_again(self):
        c = self.control(FakeEngine(self.log, fail=EngineError('no')))
        c.immersive_click(1)
        wait_for(lambda: (c.poll(), c.immersive_failed)[1])
        self.engine.fail = None
        c.immersive_click(1)
        self.assertEqual(c.state.immersive, full(False, {'XR-2': True}))
        self.until_stereo(c)

    def test_the_glasses_refusing_falls_back_with_a_toast(self):
        c = self.control(sbs=FakeSbs(self.log, enter_fails=SbsError('no reply')))
        c.on_message({'op': immersive.TOGGLE_OP})
        wait_for(lambda: (c.poll(), c.immersive_failed)[1])
        self.assertEqual(self.toasted(), '3D is off: the glasses could not switch to 3D mode (no reply). Back to 2D.')
        self.assertFalse(self.engine.up)

    def test_today_a_real_run_falls_back_and_sends_the_glasses_nothing(self):
        c = self.control(sbs=NoDeviceSbs())
        c.on_message({'op': immersive.TOGGLE_OP})
        wait_for(lambda: (c.poll(), c.immersive_failed)[1])
        self.assertIn('3D mode is not part of this run', self.toasted())
        self.assertFalse(self.engine.up)

    def test_an_engine_dying_mid_run_goes_flat_with_a_toast(self):
        c = self.control()
        c.on_message({'op': immersive.TOGGLE_OP}); self.until_stereo(c); self.log.clear()
        self.engine.up = False
        out = b''
        def down():
            nonlocal out
            out += c.poll() or b''
            return c.immersive_failed
        wait_for(down, 'the fall back')
        self.assertEqual(immersive_frames(out)[-1], {'desktops': [False] * 4, 'global': False, 'stereo': False, 'hover': -1, 'tail': False})
        self.assertEqual(self.toasted(), '3D is off: the depth engine stopped (it quit). Back to 2D.')
        self.assertEqual(self.log, [('leave',), ('stop',)])

    def test_a_vr_video_stands_3d_down_and_it_comes_back(self):
        c = self.control()
        c.on_message({'op': immersive.TOGGLE_OP}); self.until_stereo(c); self.log.clear()
        c.video_active = True
        out = c.poll()
        self.assertEqual(immersive_frames(out), [{'desktops': [True, True, True, False], 'global': True, 'stereo': False, 'hover': -1, 'tail': False}])
        wait_for(lambda: ('stop',) in self.log)
        c.video_active = False
        self.until_stereo(c)
        self.assertEqual(c.state.immersive, ALL)

    def test_changing_it_under_a_video_is_saved_and_says_when(self):
        c = self.control()
        c.video_active = True
        c.on_message({'op': immersive.TOGGLE_OP})
        self.assertEqual(self.toasted(), immersive.TOASTS['video'])
        self.assertEqual(self.log, [])

    def test_a_desktop_added_later_follows_global(self):
        c = self.control(setting=ALL)
        c.poll(); self.until_stereo(c)
        c.state.names = NAMES + ['XR-4']
        c.poll()
        wait_for(lambda: ('set', NAMES + ['XR-4']) in self.log)

    def test_the_last_3d_desktop_removed_stops_everything(self):
        c = self.control(setting=full(False, {'XR-3': True}))
        c.poll(); self.until_stereo(c)
        c.state.names = NAMES[:2]
        out = c.poll()
        self.assertFalse(immersive_frames(out)[0]['stereo'])
        wait_for(lambda: ('stop',) in self.log)

    def test_typing_mode_ignores_the_key(self):
        c = self.control()
        c.state.typing = True
        self.assertIsNone(c.on_message({'op': immersive.TOGGLE_OP}))
        self.assertEqual(c.state.immersive, OFF)

    def test_the_panel_sets_it_absolutely_and_bad_ones_are_dropped(self):
        c = self.control()
        one = full(False, {'XR-2': True})
        c.on_message({'op': immersive.SET_OP, 'payload': one})
        self.assertEqual(c.state.immersive, one)
        self.assertIsNone(c.on_message({'op': immersive.SET_OP, 'payload': {'global': 3}}))
        self.assertEqual(c.state.immersive, one)

    def test_a_save_that_fails_never_stops_the_run(self):
        c = self.control()
        c.settings = mock.Mock(save=mock.Mock(side_effect=OSError('disk full')))
        self.assertTrue(c.on_message({'op': immersive.TOGGLE_OP}))

    def test_status_tells_the_panel_what_is_shown(self):
        c = self.control()
        self.assertEqual(c.status_reply({})['immersive'], {'setting': OFF, 'names': [], 'stereo': False, 'failed': False, 'phase': 'off', 'installed': False, 'switching': False, 'button': 'R1'})
        c.immersive_click(1); self.until_stereo(c)
        self.assertEqual(c.status_reply({})['immersive'], {'setting': full(False, {'XR-2': True}), 'names': ['XR-2'],
                                                           'stereo': True, 'failed': False, 'phase': 'on', 'installed': False, 'switching': False, 'button': 'R1'})


class Sbs2dTests(unittest.TestCase):
    def test_no_device_refuses_and_leaving_is_harmless(self):
        self.assertRaises(SbsError, NoDeviceSbs().enter)
        NoDeviceSbs().leave()
        self.assertIsInstance(immersive.make_sbs(), NoDeviceSbs)

    def test_recovery_leaves_sbs_and_never_raises(self):
        log = []
        immersive.restore_2d(FakeSbs(log))
        self.assertEqual(log, [('leave',)])
        immersive.restore_2d(FakeSbs(log, leave_fails=SbsError('no')))
        immersive.restore_2d()

    def test_recovery_calls_it_before_putting_anything_back(self):
        src = Path(managed.__file__).read_text()
        for caller in ('def recover_managed', 'def recover_restoring'):
            start = src.index(caller)
            self.assertLess(src.index('immersive.restore_2d(', start), src.index('recover_restoring(client' if caller.endswith('managed') else 'names = getattr', start))

    def test_the_run_closes_it_before_recovery(self):
        src = Path(managed.__file__).read_text()
        self.assertLess(src.index('control.immersive_run.close()'), src.index('client.remove_view_binds()', src.index('control.immersive_run.close()')))


ENGINE = """#!/bin/sh
exec {python} -I -c '{body}' "$@"
"""
BODY = r"""
import json, os, signal, sys, time
mode, out = os.environ["ENGINE_MODE"], os.environ["ENGINE_OUT"]
log = open(out, "a", buffering=1)
log.write("args %s\n" % json.dumps(sys.argv[1:]))
if mode == "hang": signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "quit": sys.exit(3)
if mode == "slow": time.sleep(30)
if mode == "noise": print("loading model", flush=True); print("still", flush=True)
print("ready", flush=True)
if mode == "die": sys.exit(1)
for line in sys.stdin: log.write("line %s" % line)
log.write("eof\n")
if mode == "hang": time.sleep(30)
"""


@unittest.skipUnless(sys.platform.startswith('linux'), 'process groups')
class ProcessEngineTests(unittest.TestCase):
    """ProcessEngine against a real child process."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = Path(self.dir.name)
        self.binary = d / immersive.ENGINE_NAME
        self.binary.write_text(ENGINE.format(python=sys.executable, body=BODY.replace("'", "'\"'\"'")))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.out = d / 'out.log'
        self.env = mock.patch.dict(os.environ, {'ENGINE_OUT': str(self.out), 'ENGINE_MODE': 'ok'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def engine(self, mode='ok', **kw):
        os.environ['ENGINE_MODE'] = mode
        e = ProcessEngine(self.binary, **kw)
        self.addCleanup(e.stop)
        return e

    def lines(self):
        return self.out.read_text().splitlines() if self.out.exists() else []

    def test_it_is_found_only_beside_the_pinned_renderer(self):
        self.assertEqual(immersive.find_engine(self.binary.with_name('xr-native-spike')), self.binary)
        self.assertIsNone(immersive.find_engine(None))
        self.assertIsNone(immersive.find_engine(Path(self.dir.name) / 'none' / 'xr-native-spike'))
        self.binary.chmod(0o600)
        self.assertIsNone(immersive.find_engine(self.binary))

    def test_not_installed_is_an_error_that_starts_nothing(self):
        with self.assertRaisesRegex(EngineError, 'not installed'):
            ProcessEngine(None).start(['XR-1'])

    def test_it_starts_on_ready_changes_and_stops_on_end_of_input(self):
        e = self.engine()
        self.assertTrue(e.start(['XR-1', 'XR-3']))
        self.assertTrue(e.alive())
        self.assertEqual(self.lines()[0], 'args ["--desktops", "XR-1,XR-3"]')
        e.set_desktops(['XR-2'])
        wait_for(lambda: 'line {"desktops": ["XR-2"]}' in self.lines())
        e.stop()
        self.assertFalse(e.alive())
        self.assertEqual(self.lines()[-1], 'eof')
        e.stop()  # twice is fine

    def test_other_output_before_ready_is_ignored(self):
        self.assertTrue(self.engine('noise').start(['XR-1']))

    def test_one_that_quits_while_starting(self):
        e = self.engine('quit')
        with self.assertRaisesRegex(EngineError, 'quit while starting'): e.start(['XR-1'])
        self.assertIsNone(e.child)

    def test_one_that_never_says_ready_is_stopped(self):
        e = self.engine('slow', ready_timeout=0.3, stop_wait=0.2)
        with self.assertRaisesRegex(EngineError, 'too long'): e.start(['XR-1'])
        self.assertIsNone(e.child)

    def test_cancelling_a_load_stops_it(self):
        e = self.engine('slow', stop_wait=0.2)
        self.assertFalse(e.start(['XR-1'], cancelled=lambda: self.out.exists()))
        self.assertIsNone(e.child)

    def test_one_that_ignores_eof_and_sigterm_is_killed(self):
        e = self.engine('hang', stop_wait=0.2)
        e.start(['XR-1'])
        child = e.child
        e.stop()
        self.assertEqual(child.returncode, -signal.SIGKILL)

    def test_a_program_that_cannot_run(self):
        with self.assertRaisesRegex(EngineError, 'would not run'):
            ProcessEngine(Path(self.dir.name) / 'missing').start(['XR-1'])

    def test_it_noticed_dying(self):
        e = self.engine('die')
        e.start(['XR-1'])
        wait_for(lambda: not e.alive())
        with self.assertRaises(EngineError):
            for _ in range(200): e.set_desktops(['XR-2']); time.sleep(0.001)

    def test_a_dead_supervisor_takes_the_engine_with_it(self):
        # End of input is the contract: the pipe's write end goes with the process that held it.
        code = ('import sys; sys.path.insert(0, sys.argv[1])\n'
                'from tools.native_spike.immersive import ProcessEngine\n'
                'e = ProcessEngine(sys.argv[2]); e.start(["XR-1"]); print(e.child.pid, flush=True)\n'
                'import os; os._exit(0)\n')
        root = str(Path(immersive.__file__).parents[2])
        done = subprocess.run([sys.executable, '-c', code, root, str(self.binary)], capture_output=True, text=True, timeout=WAIT_S)
        pid = int(done.stdout)
        def gone():
            try: os.kill(pid, 0)
            except ProcessLookupError: return True
            return False
        wait_for(gone, 'the orphan to exit')
        self.assertEqual(self.lines()[-1], 'eof')


class WholeRunTests(unittest.TestCase):
    """managed.view_controls, the code a real run goes through, with a stand-in compositor."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        runtime = Path(self.dir.name) / 'rt'
        runtime.mkdir(mode=0o700)

        class Client:
            runtime_dir = runtime
            def install_view_binds(self, *args, **kw): pass
            def remove_view_binds(self): pass
            def query(self, *args, **kw): raise OSError('no compositor here')
        self.client, self.store = Client(), LayoutStore(Path(self.dir.name) / 'layout.json')

    def test_a_run_as_built_today_falls_back_because_nothing_is_installed(self):
        with managed.view_controls(self.client, 'run1', 's1', NAMES, store=self.store) as control:
            c = control.view
            self.assertIsNone(c.immersive_run.engine.binary)
            self.assertIsInstance(c.immersive_run.sbs, NoDeviceSbs)
            c.notify, toasts = (lambda text: toasts.append(text)), []
            control.on_message({'op': immersive.TOGGLE_OP})
            wait_for(lambda: (control.poll(time.monotonic()), c.immersive_failed)[1], 'the fall back')
            wait_for(lambda: toasts, 'the toast')
            self.assertEqual(toasts, ['3D is off: the depth engine could not start (it is not installed). Back to 2D.'])
        self.assertEqual(self.store.load()['immersive'], ALL)

    def test_the_engine_is_gone_and_the_glasses_flat_when_the_run_ends(self):
        log, engine = [], None
        def make(renderer, memory=None):
            nonlocal engine
            self.assertEqual(renderer, '/installed/xr-native-spike')
            engine = FakeEngine(log)
            return engine
        with mock.patch.object(immersive, 'ProcessEngine', make), mock.patch.object(immersive, 'make_sbs', lambda *a, **k: FakeSbs(log)), \
                mock.patch.object(immersive, 'find_engine', lambda renderer: renderer):
            with managed.view_controls(self.client, 'run1', 's1', NAMES, store=self.store, renderer='/installed/xr-native-spike') as control:
                c = control.view
                control.on_message({'op': immersive.TOGGLE_OP})
                wait_for(lambda: (control.poll(time.monotonic()), c.immersive_stereo)[1], 'stereo')
                self.assertEqual(log, [('start', NAMES), ('enter',)])
        self.assertEqual(log[2:], [('leave',), ('stop',)])

    def test_a_saved_setting_comes_up_at_the_next_start(self):
        self.store.save({**view.DEFAULT_SETTINGS, 'immersive': full(False, {'XR-2': True})})
        log = []
        with mock.patch.object(immersive, 'ProcessEngine', lambda renderer, memory=None: FakeEngine(log)), mock.patch.object(immersive, 'make_sbs', lambda *a, **k: FakeSbs(log)):
            with managed.view_controls(self.client, 'run1', 's1', NAMES, store=self.store) as control:
                self.assertEqual(immersive_frames(control.initial_frame())[0]['desktops'], [False, True, False, False])
                wait_for(lambda: (control.poll(time.monotonic()), control.view.immersive_stereo)[1], 'stereo')
                self.assertEqual(log[0], ('start', ['XR-2']))


if __name__ == '__main__':
    unittest.main()


class LookTests(unittest.TestCase):
    """What 3D looks like: the numbers come from docs/3d-comfort.md, through tools/stereo_eval/presets.json."""
    def test_the_presets_are_the_comfort_study_s(self):
        study = json.loads((Path(__file__).parents[2] / 'tools/stereo_eval/presets.json').read_text())
        self.assertEqual(set(immersive.PRESETS), {k for k in study if k not in ('_comment', 'naive')})
        for name, p in immersive.PRESETS.items():
            s = study[name]
            self.assertEqual((p['near'], p['far'], p['convergence'], p['range_ms']), (s['near_arcmin'], s['far_arcmin'], s['anchor_pct'], s['range_ms']), name)
            self.assertEqual(p['text'], 0 if s['text_pin'] else 100, name)  # pinned text has no parallax left

    def test_the_default_is_desktop_at_standard_strength(self):
        self.assertEqual(immersive.wire_params(None), {'strength': 100, 'convergence': 92, 'near': 4, 'far': 14, 'text': 0, 'range_ms': 3000})
        self.assertFalse(immersive.DEFAULTS['global'])

    def test_every_preset_stays_inside_the_renderer_s_wire_limits(self):
        for name in immersive.PRESETS:
            p = immersive.wire_params(immersive.with_preset(None, name))
            self.assertTrue(p['near'] <= 90 and p['far'] <= 60 and 0 <= p['text'] <= 100 and p['range_ms'] <= 10000, name)

    def test_a_preset_brings_its_plane(self):
        video = immersive.with_preset(ALL, 'video')
        self.assertEqual((video['preset'], video['convergence'], video['global']), ('video', 70, True))
        self.assertRaises(ValueError, immersive.with_preset, ALL, 'cinema')

    def test_toggling_keeps_the_look(self):
        look = {**OFF, 'preset': 'games', 'strength': 150, 'convergence': 60}
        for changed in (immersive.toggled_all(look, NAMES), immersive.toggled_every(look, NAMES), immersive.toggled_desktop(look, 'XR-2')):
            self.assertEqual((changed['preset'], changed['strength'], changed['convergence']), ('games', 150, 60))

    def test_bad_values_fall_back_one_at_a_time(self):
        got = immersive.clean({'global': True, 'preset': 'cinema', 'strength': 900, 'convergence': -1})
        self.assertEqual(got, ALL)
        self.assertEqual(immersive.clean({'strength': True})['strength'], 100)  # a bool is not a number here
        self.assertEqual(immersive.clean({'convergence': 50.5})['convergence'], 92)
        self.assertEqual(immersive.clean({'preset': 'video'})['convergence'], 70)  # no plane saved: the preset's

    def test_a_profile_from_before_the_look_gets_the_defaults(self):
        self.assertEqual(immersive.immersive({'immersive': {'global': True, 'desktops': {'XR-1': False}}}), full(True, {'XR-1': False}))

    def control(self, setting=None):
        store = mock.Mock(); store.load.return_value = view.DEFAULT_SETTINGS
        return view.ViewControl(view.ViewState(NAMES, immersive=setting), 'run1', 's1', lambda name: None, store, mock.Mock())

    def test_the_look_goes_on_the_wire_only_while_something_is_3d(self):
        c = self.control({**OFF, 'strength': 150})
        self.assertEqual(c._immersive_sync(), b'')  # a saved strength alone sends nothing: a 2D run's frames stay what they were
        payload = json.loads(c.set_immersive({**ALL, 'strength': 150, 'preset': 'video', 'convergence': 70}).decode())['payload']
        self.assertEqual(payload['params'], {'strength': 150, 'convergence': 70, 'near': 14, 'far': 36, 'text': 100, 'range_ms': 1000})

    def test_changing_the_look_goes_at_once(self):
        c = self.control(ALL)
        c.initial_frame()
        frame = c.set_immersive({**ALL, 'strength': 50})
        self.assertEqual(json.loads(frame.decode())['payload']['params']['strength'], 50)

    def test_a_new_renderer_is_told_3d_is_on(self):
        c = self.control(ALL)
        self.assertTrue(immersive_frames(c.initial_frame()))
        self.assertFalse(immersive_frames(c.initial_frame()))
        c.renderer_restarted()
        self.assertTrue(immersive_frames(c.initial_frame()))

    def test_a_new_renderer_with_nothing_on_gets_no_frame(self):
        c = self.control()
        c.initial_frame()
        c.renderer_restarted()
        self.assertFalse(immersive_frames(c.initial_frame()))


class OffIsUnchangedTests(unittest.TestCase):
    """Default off, and off means a run as it was before this feature."""
    def test_a_run_with_nothing_on_starts_nothing(self):
        run = ImmersiveRun(mock.Mock(), mock.Mock())
        run.request([])
        self.assertIsNone(run._thread)
        run.engine.start.assert_not_called()
        run.sbs.enter.assert_not_called()

    def test_no_engine_means_no_memory_and_no_depth_argument(self):
        calls = []
        class Stop(Exception): pass
        def popen(argv, **kw):
            calls.append((argv, kw)); raise Stop
        import socket
        from tools.native_spike import supervisor
        a, b = socket.socketpair()
        client = mock.Mock(); client._peer = (os.getpid(), os.getuid(), os.getgid())
        with mock.patch('struct.unpack', lambda fmt, data: client._peer), self.assertRaises(Stop):
            supervisor._launch(client, 'r1', 's1', 'DP-2', b, None, popen=popen, capture=None)
        a.close()
        self.assertNotIn('--depth-fd', calls[0][0])
        self.assertEqual(len(calls[0][1]['pass_fds']), 3)

    def test_with_the_engine_the_memory_is_inherited(self):
        calls = []
        class Stop(Exception): pass
        def popen(argv, **kw):
            calls.append((argv, kw)); raise Stop
        import socket
        from tools.native_spike import supervisor
        a, b = socket.socketpair()
        client = mock.Mock(); client._peer = (os.getpid(), os.getuid(), os.getgid())
        memory = immersive.DepthMemory()
        self.addCleanup(memory.close)
        with mock.patch('struct.unpack', lambda fmt, data: client._peer), self.assertRaises(Stop):
            supervisor._launch(client, 'r1', 's1', 'DP-2', b, None, popen=popen, capture=None, depth_fd=memory.fd)
        a.close()
        argv = calls[0][0]
        self.assertEqual(argv[argv.index('--depth-fd') + 1], str(memory.fd))
        self.assertIn(memory.fd, calls[0][1]['pass_fds'])

    def test_a_2d_consent_is_what_older_code_reads(self):
        from tools.native_spike import panel_consent
        self.assertNotIn('sbs_mode', panel_consent.PANEL_KEYS)


class MemoryTests(unittest.TestCase):
    def test_the_size_is_the_renderer_s(self):
        from tools.depth_engine import shm as S
        self.assertEqual(immersive.DepthMemory.size(), S.shm_bytes(4, 420, 266, 420, 266))
        header = (Path(__file__).parents[2] / 'spike/include/xr/depth.hpp').read_text()
        self.assertIn('kDepthLinkShm{4, kDepthLongBalanced, kDepthMinTall, kDepthLongBalanced, kDepthMinTall}', header)
        self.assertEqual((immersive.DepthMemory.IN_W, immersive.DepthMemory.IN_H), (420, 266))

    def test_it_has_no_name_and_goes_with_its_last_descriptor(self):
        m = immersive.DepthMemory()
        self.addCleanup(m.close)
        self.assertEqual(os.fstat(m.fd).st_size, m.size())
        self.assertEqual(os.fstat(m.fd).st_nlink, 0)
        self.assertTrue(os.readlink(f'/proc/self/fd/{m.fd}').startswith('/memfd:'))
        self.assertFalse(os.get_inheritable(m.fd))  # only ever passed on purpose

    def test_the_heartbeat(self):
        import struct
        m = immersive.DepthMemory()
        self.addCleanup(m.close)
        self.assertIsNone(m.heartbeat_age())  # no engine yet
        os.pwrite(m.fd, struct.pack('<Q', 5_000_000_000), m.HEARTBEAT_AT)
        self.assertEqual(m.heartbeat_age(7_000_000_000), 2.0)

    def test_a_hung_engine_counts_as_dead(self):
        import struct
        m = immersive.DepthMemory()
        self.addCleanup(m.close)
        engine = ProcessEngine('/bin/true', memory=m)
        engine.child = mock.Mock(); engine.child.poll.return_value = None
        self.assertTrue(engine.alive())  # still loading: no heartbeat yet
        os.pwrite(m.fd, struct.pack('<Q', time.monotonic_ns()), m.HEARTBEAT_AT)
        self.assertTrue(engine.alive())
        os.pwrite(m.fd, struct.pack('<Q', time.monotonic_ns() - int(1e9 * (immersive.HEARTBEAT_LIMIT_S + 1))), m.HEARTBEAT_AT)
        self.assertFalse(engine.alive())


class RealEngineTests(unittest.TestCase):
    """The real tools.depth_engine in its own process, over a real memfd, with the fake model: the contract, and the crash."""
    def setUp(self):
        self.memory = immersive.DepthMemory()
        self.addCleanup(self.memory.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.binary = Path(directory.name) / immersive.ENGINE_NAME
        root = Path(__file__).parents[2]
        self.binary.write_text(f"#!/bin/sh\nexec {sys.executable} -c \"import sys,runpy; sys.path.insert(0,'{root}'); sys.argv[0]='xr-depth-engine'; "
                               f"runpy.run_module('tools.depth_engine',run_name='__main__')\" serve --supervised --backend fake --wait 5 \"$@\"\n")
        self.binary.chmod(0o700)
        from tools.depth_engine import shm as S
        path = f'/proc/{os.getpid()}/fd/{self.memory.fd}'
        self.S = S
        self.shm = None
        self.path = path

    def layout(self):
        """What the renderer does on its first 3D frame."""
        import numpy as np
        S = self.S
        fd = self.memory.fd
        size = self.memory.size()
        import mmap, struct
        view_ = mmap.mmap(fd, 4096)
        struct.pack_into('<IIIIIIII', view_, 0, 0, S.VERSION, 4, S.SLOTS, 420, 266, 420, 266)
        struct.pack_into('<QQ', view_, 32, S.channel_stride(420, 266, 420, 266), size)
        struct.pack_into('<I', view_, 0, S.MAGIC)
        view_.close()
        self.shm = S.DepthShm.open(self.path)
        self.addCleanup(self.shm.close)
        return np

    def test_up_serving_and_gone_at_end_of_input(self):
        engine = ProcessEngine(self.binary, memory=self.memory)
        worker = threading.Thread(target=lambda: time.sleep(0.2) or self.layout())  # the engine starts first, as in a run
        worker.start()
        self.assertTrue(engine.start(['XR-1'], lambda: False))
        worker.join()
        self.addCleanup(engine.stop)
        self.assertTrue(engine.alive())
        self.assertIsNotNone(self.memory.heartbeat_age())
        import numpy as np
        S = self.S
        self.shm.set_control(0, S.ENABLED)
        px = np.full((266, 420, 4), 200, np.uint8); px[:133, :, :3] = 40
        self.assertTrue(self.shm.publish(S.INPUT, 0, px, S.FMT_RGBA8, 1, time.monotonic_ns()))
        deadline = time.monotonic() + 5
        out = None
        while out is None and time.monotonic() < deadline:
            out = self.shm.fetch(S.OUTPUT, 0)
            time.sleep(0.01)
        self.assertIsNotNone(out)
        self.assertEqual((out.w, out.h, out.format), (420, 266, S.FMT_R16F))
        engine.set_desktops(['XR-1', 'XR-2'])  # says so, and stays up
        self.assertTrue(engine.alive())
        child = engine.child
        child.stdin.close()
        self.assertEqual(child.wait(5), 0)  # end of input: gone, by itself
        engine.child = None

    def test_killed_outright(self):
        engine = ProcessEngine(self.binary, memory=self.memory)
        worker = threading.Thread(target=self.layout)
        worker.start()
        self.assertTrue(engine.start(['XR-1'], lambda: False))
        worker.join()
        self.addCleanup(engine.stop)
        os.killpg(engine.child.pid, signal.SIGKILL)
        engine.child.wait(5)
        self.assertFalse(engine.alive())  # what the worker sees; the renderer sees the heartbeat stop (depth_link_render_test.cpp)

    def test_memory_never_laid_out_is_a_failure_to_start_not_a_hang(self):
        self.binary.write_text(self.binary.read_text().replace('--wait 5', '--wait 0.1'))
        with self.assertRaisesRegex(EngineError, 'quit'):
            ProcessEngine(self.binary, memory=self.memory, ready_timeout=8).start(['XR-1'], lambda: False)
