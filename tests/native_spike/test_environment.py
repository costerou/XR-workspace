"""The TRON grid (tools/native_spike/environment.py; the renderer's side: spike/tests/environment_test.cpp and
environment_render_test.cpp): the setting and its defaults, its keys in the central bind table and on the help card,
the renderer frames (none while it stays off), saving and restoring with the view settings, and the quality menu's row."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.native_spike import bind_check, environment, ipc, profiles, quality, send, video, view, xr_control
from tools.native_spike.environment import COLOR_OP, TOGGLE_OP
from tools.native_spike.profiles import LayoutStore, ProfileStore

from test_video import Clock, FakeSupervisor, frames, info

NAMES = ['XR-1', 'XR-2', 'XR-3']
OFF = {'on': False, 'color': 'cyan'}
ON = {'on': True, 'color': 'cyan'}
THEME = {'on': True, 'color': 'theme'}
FIXTURE = Path(__file__).parent / 'fixtures' / 'environment_payloads.json'


class SettingTests(unittest.TestCase):
    def test_off_and_cyan_by_default(self):
        self.assertEqual(environment.DEFAULTS, OFF)
        self.assertEqual(view.DEFAULT_SETTINGS['environment'], OFF)
        self.assertEqual(profiles.DEFAULT_PROFILE['environment'], OFF)
        self.assertEqual(view.ViewState(NAMES).environment, OFF)

    def test_the_accessor_cleans_each_field_on_its_own(self):
        self.assertEqual(environment.environment({}), OFF)
        self.assertEqual(environment.environment({'environment': 'on'}), OFF)
        self.assertEqual(environment.environment({'environment': THEME}), THEME)
        self.assertEqual(environment.environment({'environment': {'on': True, 'color': 'red'}}), ON)
        self.assertEqual(environment.environment({'environment': {'on': 1, 'color': 'theme'}}), {'on': False, 'color': 'theme'})
        self.assertEqual(environment.environment({'environment': {'on': True, 'color': ['theme']}}), ON)  # unhashable: no crash
        self.assertEqual(environment.environment(view.ViewState(NAMES, environment=THEME)), THEME)
        self.assertEqual(environment.environment(object()), OFF)

    def test_keys_change_it_as_their_ops_say(self):
        self.assertEqual(environment.toggled(OFF), ON)
        self.assertEqual(environment.toggled(ON), OFF)
        self.assertEqual(environment.toggled({'on': False, 'color': 'theme'}), THEME)  # off keeps the colour
        self.assertEqual(environment.toggled(THEME), {'on': False, 'color': 'theme'})
        self.assertEqual(environment.recolored(ON), THEME)
        self.assertEqual(environment.recolored(THEME), ON)
        self.assertEqual(environment.recolored(OFF), THEME)  # the colour key always shows what it did

    def test_the_payload_fixture_matches_the_renderers_decoder(self):
        # spike/tests/environment_test.cpp runs the renderer's decoder over the same file.
        data = json.loads(FIXTURE.read_text())
        for case in data['valid']:
            frame = json.loads(environment.renderer_frame('run', 's', 1, case['payload']))
            self.assertEqual(frame['op'], 'environment')
            self.assertEqual(frame['payload'], case['payload'])
            self.assertEqual(environment.COLORS[case['color']], case['payload']['color'])
        for case in data['invalid']:
            with self.subTest(case=case), self.assertRaises(ValueError): environment.renderer_frame('run', 's', 1, case)

    def test_the_operations_a_running_session_gets(self):
        for start in (OFF, ON, THEME, {'on': False, 'color': 'theme'}):
            for goal in (OFF, ON, THEME, {'on': False, 'color': 'theme'}):
                state = start
                for op in environment.operations(start, goal):
                    state = environment.recolored(state) if op == COLOR_OP else environment.toggled(state)
                self.assertEqual(state, goal, (start, goal))
        self.assertEqual(environment.operations(ON, ON), [])


class BindTests(unittest.TestCase):
    def test_in_the_central_bind_table(self):
        self.assertIn(('CTRL + ALT + E', TOGGLE_OP), view.BINDS)
        self.assertIn(('CTRL + ALT + SHIFT + E', COLOR_OP), view.BINDS)
        self.assertEqual(view.chord_label('CTRL + ALT + SHIFT + E'), 'Ctrl+Alt+Shift+E')
        for op in environment.OPS:
            self.assertIn(op, view.HELP_ACTIONS)
            self.assertIn(op, xr_control.OPS)
            self.assertIn(op, view.IGNORED_WHILE_TYPING)
            self.assertNotIn(op, view.USER_OPS)  # no panel button

    def test_no_other_run_key_uses_them(self):
        chords = [bind_check.normalize(c) for _, c, _ in bind_check.run_chords()]
        self.assertEqual(len(chords), len(set(chords)) + 1)  # the left button's press / release is the one pair
        ours = {bind_check.normalize(c) for c, _ in (environment.TOGGLE_BIND, environment.COLOR_BIND)}
        self.assertEqual(len(ours), 2)
        self.assertFalse(ours & {bind_check.normalize(c) for c, _ in send.SEND_BINDS})

    def test_one_help_row_in_the_modes_section(self):
        self.assertIn(['Ctrl+Alt+E / Shift+E', 'TRON grid: on, off / cyan, theme'], view.help_rows())
        modes = view.HELP_SECTIONS[1]
        self.assertIn(TOGGLE_OP, modes)
        self.assertEqual(sum(1 for keys, _ in view.help_rows() if 'E' in keys.split('+')[-1]), 1)

    def test_the_lua_binds_the_helper_runs(self):
        lua = view.install_binds_lua('/usr/bin/python3', '/opt/xr_control.py', '/run/user/1000/control.sock', 'run', 'session')
        self.assertIn(f'hl.bind("CTRL + ALT + E", hl.dsp.exec_cmd(c .. "{TOGGLE_OP}"))', lua)
        self.assertIn(f'hl.bind("CTRL + ALT + SHIFT + E", hl.dsp.exec_cmd(c .. "{COLOR_OP}"))', lua)
        typing = view.install_binds_lua('/usr/bin/python3', '/opt/xr_control.py', '/run/user/1000/control.sock', 'run', 'session', typing=True)
        self.assertNotIn(TOGGLE_OP, typing)  # typing mode hands the keys back

    def test_the_run_accepts_the_ops(self):
        for op in environment.OPS:
            raw = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(ipc.decode_control(raw)['op'], op)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.layout = Path(self.tmp.name) / 'layout.json'
        self.store = LayoutStore(self.layout)

    def control(self, env=None, store=None):
        return view.ViewControl(view.ViewState(NAMES, environment=env), 'run1', 's1', lambda name: None, store or self.store, None)

    def sent(self, control, op):
        return [json.loads(line) for line in (control.on_message({'op': op, 'payload': {}}) or b'').splitlines()]

    def test_toggle_fades_and_saves(self):
        c = self.control()
        frames_ = self.sent(c, TOGGLE_OP)
        self.assertEqual([f['op'] for f in frames_], ['environment'])
        self.assertEqual(frames_[0]['payload'], ON)
        self.assertEqual(view.load_settings(self.layout)['environment'], ON)
        self.assertEqual(self.sent(c, TOGGLE_OP)[0]['payload'], OFF)
        self.assertEqual(view.load_settings(self.layout)['environment'], OFF)

    def test_colour_turns_it_on_and_saves(self):
        c = self.control()
        self.assertEqual(self.sent(c, COLOR_OP)[0]['payload'], THEME)
        self.assertEqual(view.load_settings(self.layout)['environment'], THEME)
        self.assertEqual(self.sent(c, COLOR_OP)[0]['payload'], ON)

    def test_request_ids_keep_rising(self):
        c = self.control()
        a, b = self.sent(c, TOGGLE_OP)[0], self.sent(c, TOGGLE_OP)[0]
        self.assertGreater(b['request_id'], a['request_id'])

    def test_nothing_to_send_at_the_start_while_off(self):
        # A renderer from before this feature would refuse the op: off, it never sees one.
        self.assertNotIn(b'"op":"environment"', self.control().initial_frame())

    def test_a_saved_grid_comes_back_at_the_start(self):
        view.save_settings(self.layout, {**view.DEFAULT_SETTINGS, 'environment': THEME})
        saved = self.store.load()
        self.assertEqual(saved['environment'], THEME)
        c = self.control(environment.environment(saved))
        sent = [json.loads(line) for line in c.initial_frame().splitlines()]
        self.assertEqual([f['payload'] for f in sent if f['op'] == 'environment'], [THEME])

    def test_an_old_saved_layout_has_it_off(self):
        self.layout.write_text('{"spacing": 60, "mode": "anchor", "prediction": false}')
        self.assertEqual(view.load_settings(self.layout)['environment'], OFF)
        self.layout.write_text('{"environment": {"on": "yes", "color": "green"}}')
        self.assertEqual(view.load_settings(self.layout)['environment'], OFF)

    def test_the_glasses_profile_keeps_it(self):
        path = Path(self.tmp.name) / 'profiles.json'
        store = ProfileStore(path, 'luma-ultra')
        c = self.control(store=store)
        c.on_message({'op': COLOR_OP, 'payload': {}})
        self.assertEqual(store.load()['environment'], THEME)
        self.assertEqual(profiles.load_profile(path, 'luma-ultra')['environment'], THEME)
        self.assertEqual(profiles.load_profile(path, 'beast')['environment'], OFF)  # per glasses model
        # A later save of the placements keeps it, as it does every other field.
        profiles.PlacementStore(path, 'luma-ultra').save([{'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}] * 3)
        self.assertEqual(profiles.load_profile(path, 'luma-ultra')['environment'], THEME)

    def test_a_failed_save_keeps_the_run_going(self):
        class Broken:
            def save(self, settings): raise OSError('disk full')
        c = self.control(store=Broken())
        self.assertEqual(self.sent(c, TOGGLE_OP)[0]['payload'], ON)

    def test_stale_keys_in_typing_mode_do_nothing(self):
        c = self.control()
        c.on_message({'op': 'typing_toggle', 'payload': {}})
        self.assertIsNone(c.on_message({'op': TOGGLE_OP, 'payload': {}}))
        self.assertIsNone(c.on_message({'op': COLOR_OP, 'payload': {}}))
        self.assertEqual(c.state.environment, OFF)

    def test_the_view_payload_is_untouched(self):
        # The renderer's view decoder counts its keys, and the grid never depends on the viewing mode.
        c = self.control()
        before = c.state.view()
        c.on_message({'op': TOGGLE_OP, 'payload': {}})
        self.assertEqual(c.state.view(), before)
        self.assertNotIn('environment', before)

    def test_any_view_keeps_it(self):
        c = self.control(ON)
        for op in ('zoom_in', 'mode_next', 'zoom_out', 'next', 'recenter'):
            c.on_message({'op': op, 'payload': {}})
        self.assertEqual(c.state.environment, ON)


class QualityMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / 'Beach_360_TB.mp4'; self.clip.write_bytes(b'v' * 100)
        self.layout = Path(self.tmp.name) / 'layout.json'
        self.clock = Clock()
        vc = view.ViewControl(view.ViewState(NAMES), 'run1', 's1', lambda name: None, LayoutStore(self.layout), None, clock=self.clock)
        self.v = video.VideoControl(vc, probe=lambda fd: info(w=4096, h=4096), threads=False, clock=self.clock)
        self.v.attach_supervisor(FakeSupervisor())
        self.v.on_message({'op': 'video_open', 'payload': {'path': str(self.clip)}})
        self.addCleanup(self.v.poll(self.clock()).close_fds)
        self.op('video_quality')

    def op(self, op):
        return frames(self.v.on_message({'op': op, 'payload': {}}))

    def row(self):
        """Down to the grid's row (the last one)."""
        for _ in quality.SETTINGS: self.op('video_down')

    def menu(self, out):
        return next(f['payload'] for f in out if f['op'] == 'video' and f['payload']['action'] == 'menu')

    def test_a_row_after_the_video_settings(self):
        rows = self.menu(self.op('video_down'))['rows']
        self.assertEqual(rows[len(quality.SETTINGS)], {'label': 'TRON grid', 'value': 'Off'})
        self.assertEqual(len(rows), len(quality.SETTINGS) + 1)
        self.assertLessEqual(len(rows), video.MAX_MENU_ROWS)
        for value in (*environment.MENU_VALUES.values(), environment.MENU_OFF, environment.MENU_LABEL):
            self.assertLessEqual(len(value), quality.MAX_LABEL)

    def test_left_right_walk_off_cyan_theme(self):
        self.row()
        out = self.op('video_seek_fwd')
        self.assertEqual([f['payload'] for f in out if f['op'] == 'environment'], [ON])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'TRON cyan')
        self.assertEqual(view.load_settings(self.layout)['environment'], ON)
        out = self.op('video_seek_fwd')
        self.assertEqual([f['payload'] for f in out if f['op'] == 'environment'], [THEME])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'Theme colour')
        out = self.op('video_seek_fwd')  # wraps round to Off, keeping the colour
        self.assertEqual([f['payload'] for f in out if f['op'] == 'environment'], [{'on': False, 'color': 'theme'}])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'Off')
        out = self.op('video_seek_back')  # and back
        self.assertEqual([f['payload'] for f in out if f['op'] == 'environment'], [THEME])

    def test_the_video_settings_stay_as_they_were(self):
        self.row()
        before = self.v.quality()
        out = self.op('video_seek_fwd')
        self.assertEqual(self.v.quality(), before)
        self.assertFalse([f for f in out if f['op'] == 'video' and f['payload']['action'] == 'quality'])
        video.validate_renderer_payload(self.menu(out))

    def test_every_frame_is_one_the_renderer_takes(self):
        self.row()
        for _ in range(4):
            for f in self.op('video_seek_fwd'):
                if f['op'] == 'video': video.validate_renderer_payload(f['payload'])
                else: environment.validate(f['payload'])


if __name__ == '__main__':
    unittest.main()
