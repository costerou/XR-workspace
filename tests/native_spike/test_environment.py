"""The background behind the desktops and a VR video (tools/native_spike/environment.py; the renderer's side:
spike/tests/environment_test.cpp, environment_render_test.cpp, video_render_test.cpp): the setting and its defaults, Ctrl+Alt+E in
the central bind table and on the help card, the renderer frames (none while there is no background), saving and restoring
with the view settings, the placement panel's dropdown, switch and Brightness slider, and the quality menu's row."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import bind_check, environment, free_pointer as fp, ipc, profiles, quality, video, view, xr_control
from tools.native_spike.environment import BRIGHTNESS_FIELD, SET_OP, TOGGLE_OP
from tools.native_spike.profiles import LayoutStore, ProfileStore

from test_video import Clock, FakeSupervisor, frames, info

NAMES = ['XR-1', 'XR-2', 'XR-3']
NONE = {'background': 'none', 'last': 'grid', 'brightness': 100}
GRID = {'background': 'grid', 'last': 'grid', 'brightness': 100}
FIXTURE = Path(__file__).parent / 'fixtures' / 'environment_payloads.json'


class SettingTests(unittest.TestCase):
    def test_none_at_todays_brightness_by_default(self):
        self.assertEqual(environment.DEFAULTS, NONE)
        self.assertEqual(view.DEFAULT_SETTINGS['environment'], NONE)
        self.assertEqual(profiles.DEFAULT_PROFILE['environment'], NONE)
        self.assertEqual(view.ViewState(NAMES).environment, NONE)

    def test_the_table_starts_with_none_and_more_can_follow(self):
        self.assertEqual(environment.BACKGROUNDS[0], ('none', 'None'))
        self.assertEqual(environment.BACKGROUNDS[1:3], (('grid', 'Grid'), ('smoke', 'Smoke')))
        for name, label in environment.BACKGROUNDS:
            self.assertLessEqual(len(label), quality.MAX_LABEL)
        self.assertLessEqual(len(environment.MENU_LABEL), quality.MAX_LABEL)
        with mock.patch.object(environment, 'BACKGROUNDS', environment.BACKGROUNDS + (('stars', 'Stars'),)), \
                mock.patch.object(environment, 'NAMES', environment.NAMES + ('stars',)):
            # A new row is all the setting needs: pick it, step to it, and Ctrl+Alt+E comes back to it.
            stars = environment.picked(GRID, 'stars')
            self.assertEqual(stars, {'background': 'stars', 'last': 'stars', 'brightness': 100})
            self.assertEqual(environment.toggled(environment.toggled(stars)), stars)
            self.assertEqual(environment.stepped(environment.picked(GRID, 'smoke'), 1)['background'], 'stars')
            self.assertEqual(environment.stepped(stars, 1)['background'], 'none')
            self.assertEqual(environment.label(stars), 'Stars')

    def test_smoke_is_a_background_like_grid(self):
        smoke = environment.picked(GRID, 'smoke')
        self.assertEqual(smoke, {'background': 'smoke', 'last': 'smoke', 'brightness': 100})
        self.assertEqual(environment.label(smoke), 'Smoke')
        self.assertEqual(environment.stepped(GRID, 1), smoke)
        self.assertEqual(environment.environment({'environment': smoke}), smoke)

    def test_the_toggle_comes_back_to_the_last_one_used(self):
        smoke = environment.picked(GRID, 'smoke')
        off = environment.toggled(smoke)
        self.assertEqual(off, {'background': 'none', 'last': 'smoke', 'brightness': 100})
        self.assertEqual(environment.toggled(off), smoke)
        grid = environment.picked(off, 'grid')  # switching between the two moves `last` with it
        self.assertEqual(environment.toggled(environment.toggled(grid)), grid)
        self.assertEqual(environment.environment({'environment': off})['last'], 'smoke')  # as saved

    def test_the_accessor_cleans_each_field_on_its_own(self):
        self.assertEqual(environment.environment({}), NONE)
        self.assertEqual(environment.environment({'environment': 'grid'}), NONE)
        self.assertEqual(environment.environment({'environment': GRID}), GRID)
        self.assertEqual(environment.environment({'environment': {'background': 'nope', 'brightness': 60}}), {**NONE, 'brightness': 60})
        self.assertEqual(environment.environment({'environment': {'background': 'grid', 'brightness': 500}}), GRID)
        self.assertEqual(environment.environment({'environment': {'background': ['grid']}}), NONE)  # unhashable: no crash
        self.assertEqual(environment.environment({'environment': {'brightness': True}}), NONE)      # True is not 1
        self.assertEqual(environment.environment({'environment': {'on': True, 'color': 'cyan'}}), NONE)  # an early build's keys
        self.assertEqual(environment.environment({'environment': {'background': 'none', 'last': 'none'}}), NONE)
        self.assertEqual(environment.environment(view.ViewState(NAMES, environment=GRID)), GRID)
        self.assertEqual(environment.environment(object()), NONE)

    def test_validate_takes_only_a_whole_consistent_setting(self):
        self.assertEqual(environment.validate(GRID), GRID)
        for bad in ({}, {'background': 'grid'}, {**GRID, 'brightness': 9}, {**GRID, 'brightness': 201}, {**GRID, 'brightness': True},
                    {**GRID, 'background': 'red'}, {**NONE, 'last': 'none'}, {**GRID, 'color': 'cyan'}, None, 'grid'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): environment.validate(bad)

    def test_ctrl_alt_e_goes_between_none_and_the_last_used(self):
        self.assertEqual(environment.toggled(NONE), GRID)
        self.assertEqual(environment.toggled(GRID), NONE)
        dim = {**GRID, 'brightness': 40}
        self.assertEqual(environment.toggled(environment.toggled(dim)), dim)  # None keeps the brightness too

    def test_a_pick_is_remembered_as_the_last_used(self):
        self.assertEqual(environment.picked(NONE, 'grid'), GRID)
        self.assertEqual(environment.picked(GRID, 'none'), NONE)
        with self.assertRaises(ValueError): environment.picked(NONE, 'cyan')
        with self.assertRaises(ValueError): environment.picked(NONE, ['grid'])

    def test_brightness_stays_in_its_bounds(self):
        self.assertEqual(environment.brightened(GRID, 5)['brightness'], 10)
        self.assertEqual(environment.brightened(GRID, 900)['brightness'], 200)
        self.assertEqual(environment.slid(GRID, 0)['brightness'], 10)
        self.assertEqual(environment.slid(GRID, 1)['brightness'], 200)
        self.assertEqual(environment.slid(GRID, 2)['brightness'], 200)
        self.assertEqual(environment.slid(GRID, 90 / 190)['brightness'], 100)  # the default is on the slider, not at its end
        with self.assertRaises(ValueError): environment.slid(GRID, float('nan'))

    def test_the_payload_fixture_matches_the_renderers_decoder(self):
        # spike/tests/environment_test.cpp runs the renderer's decoder over the same file.
        data = json.loads(FIXTURE.read_text())
        for case in data['valid']:
            frame = json.loads(environment.renderer_frame('run', 's', 1, case['setting']))
            self.assertEqual(frame['op'], 'environment')
            self.assertEqual(frame['payload'], case['payload'])
            self.assertEqual(environment.NAMES[case['background']], case['payload']['background'])
        for case in data['invalid']:
            with self.subTest(case=case), self.assertRaises(ValueError): environment.renderer_frame('run', 's', 1, case)


class BindTests(unittest.TestCase):
    def test_in_the_central_bind_table(self):
        self.assertIn(('CTRL + ALT + E', TOGGLE_OP), view.BINDS)
        self.assertIn(TOGGLE_OP, view.HELP_ACTIONS)
        self.assertIn(TOGGLE_OP, xr_control.OPS)
        self.assertIn(TOGGLE_OP, view.IGNORED_WHILE_TYPING)
        self.assertNotIn(TOGGLE_OP, view.USER_OPS)  # no panel button

    def test_the_colour_mode_and_its_key_are_gone(self):
        self.assertNotIn('CTRL + ALT + SHIFT + E', [chord for chord, _ in view.BINDS])
        self.assertNotIn('environment_color', [op for _, op in view.BINDS])
        self.assertNotIn('environment_color', xr_control.OPS)
        self.assertFalse(hasattr(environment, 'COLOR_OP') or hasattr(view, 'ENVIRONMENT_COLOR_OP'))
        self.assertNotIn('color', environment.DEFAULTS)

    def test_no_other_run_key_uses_it(self):
        chords = [bind_check.normalize(c) for _, c, _ in bind_check.run_chords()]
        self.assertEqual(len(chords), len(set(chords)) + 1)  # the left button's press / release is the one pair

    def test_one_help_row_in_the_modes_section(self):
        self.assertIn(['Ctrl+Alt+E / I', 'Background / immersive 3D: on or off'], view.help_rows())  # the row 3D shares
        self.assertIn(TOGGLE_OP, view.HELP_SECTIONS[1])

    def test_the_lua_binds_the_helper_runs(self):
        lua = view.install_binds_lua('/usr/bin/python3', '/opt/xr_control.py', '/run/user/1000/control.sock', 'run', 'session')
        self.assertIn(f'hl.bind("CTRL + ALT + E", hl.dsp.exec_cmd(c .. "{TOGGLE_OP}"))', lua)
        typing = view.install_binds_lua('/usr/bin/python3', '/opt/xr_control.py', '/run/user/1000/control.sock', 'run', 'session', typing=True)
        self.assertNotIn(TOGGLE_OP, typing)  # typing mode hands the key back

    def test_the_run_accepts_the_ops(self):
        def raw(op, payload):
            return (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': payload}) + '\n').encode()
        self.assertEqual(ipc.decode_control(raw(TOGGLE_OP, {}))['op'], TOGGLE_OP)
        self.assertEqual(ipc.decode_control(raw(SET_OP, GRID))['payload'], GRID)
        with self.assertRaises(ValueError): ipc.decode_control(raw(SET_OP, {'background': 'red'}))
        with self.assertRaises(ValueError): ipc.decode_control(raw('environment_color', {}))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.layout = Path(self.tmp.name) / 'layout.json'
        self.store = LayoutStore(self.layout)
        self.now = 100.0

    def control(self, env=None, store=None):
        return view.ViewControl(view.ViewState(NAMES, environment=env), 'run1', 's1', lambda name: None, store or self.store, None,
                                clock=lambda: self.now)

    def lines(self, data):
        return [json.loads(line) for line in (data or b'').splitlines()]

    def sent(self, control, op, payload=None):
        return self.lines(control.on_message({'op': op, 'payload': payload or {}}))

    def saved(self):
        return view.load_settings(self.layout)['environment']

    def test_toggle_fades_and_saves(self):
        c = self.control()
        out = self.sent(c, TOGGLE_OP)
        self.assertEqual([f['op'] for f in out], ['environment'])
        self.assertEqual(out[0]['payload'], {'background': 'grid', 'brightness': 100})
        self.assertEqual(self.saved(), GRID)
        self.assertEqual(self.sent(c, TOGGLE_OP)[0]['payload'], {'background': 'none', 'brightness': 100})
        self.assertEqual(self.saved(), NONE)

    def test_the_controller_sets_it_absolutely(self):
        c = self.control()
        dim = {**GRID, 'brightness': 60}
        self.assertEqual(self.sent(c, SET_OP, dim)[0]['payload'], {'background': 'grid', 'brightness': 60})
        self.assertEqual(self.saved(), dim)
        self.assertEqual(self.sent(c, SET_OP, dim), [])                    # no change, nothing sent
        self.assertEqual(self.sent(c, SET_OP, {'background': 'red'}), [])  # malformed: dropped

    def test_request_ids_keep_rising(self):
        c = self.control()
        a, b = self.sent(c, TOGGLE_OP)[0], self.sent(c, TOGGLE_OP)[0]
        self.assertGreater(b['request_id'], a['request_id'])

    def test_nothing_to_send_at_the_start_with_none(self):
        # A renderer from before this feature would refuse the op: with None, it never sees one.
        self.assertNotIn(b'"op":"environment"', self.control().initial_frame())
        self.assertNotIn(b'"op":"environment"', self.control({**NONE, 'brightness': 50}).initial_frame())

    def test_a_saved_background_comes_back_at_the_start(self):
        dim = {**GRID, 'brightness': 60}
        view.save_settings(self.layout, {**view.DEFAULT_SETTINGS, 'environment': dim})
        saved = self.store.load()
        self.assertEqual(saved['environment'], dim)
        c = self.control(environment.environment(saved))
        sent = self.lines(c.initial_frame())
        self.assertEqual([f['payload'] for f in sent if f['op'] == 'environment'], [{'background': 'grid', 'brightness': 60}])

    def test_an_old_saved_layout_has_none(self):
        self.layout.write_text('{"spacing": 60, "mode": "anchor", "prediction": false}')
        self.assertEqual(self.saved(), NONE)
        self.layout.write_text('{"environment": {"background": "lava", "brightness": 900}}')
        self.assertEqual(self.saved(), NONE)

    def test_the_glasses_profile_keeps_it(self):
        path = Path(self.tmp.name) / 'profiles.json'
        store = ProfileStore(path, 'luma-ultra')
        c = self.control(store=store)
        c.on_message({'op': TOGGLE_OP, 'payload': {}})
        self.assertEqual(store.load()['environment'], GRID)
        self.assertEqual(profiles.load_profile(path, 'luma-ultra')['environment'], GRID)
        self.assertEqual(profiles.load_profile(path, 'beast')['environment'], NONE)  # per glasses model
        # A later save of the placements keeps it, as it does every other field.
        profiles.PlacementStore(path, 'luma-ultra').save([{'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}] * 3)
        self.assertEqual(profiles.load_profile(path, 'luma-ultra')['environment'], GRID)

    def test_a_failed_save_keeps_the_run_going(self):
        class Broken:
            def save(self, settings): raise OSError('disk full')
        c = self.control(store=Broken())
        self.assertEqual(self.sent(c, TOGGLE_OP)[0]['payload']['background'], 'grid')

    def test_a_stale_key_in_typing_mode_does_nothing(self):
        c = self.control()
        c.on_message({'op': 'typing_toggle', 'payload': {}})
        self.assertIsNone(c.on_message({'op': TOGGLE_OP, 'payload': {}}))
        self.assertEqual(c.state.environment, NONE)

    def test_the_view_payload_is_untouched(self):
        # The renderer's view decoder counts its keys, and the background never depends on the viewing mode.
        c = self.control()
        before = c.state.view()
        c.on_message({'op': TOGGLE_OP, 'payload': {}})
        self.assertEqual(c.state.view(), before)
        self.assertNotIn('environment', before)

    def test_any_view_keeps_it(self):
        c = self.control(GRID)
        for op in ('zoom_in', 'mode_next', 'zoom_out', 'next', 'recenter'):
            c.on_message({'op': op, 'payload': {}})
        self.assertEqual(c.state.environment, GRID)


class PanelTests(ControlTests):
    """The open placement panel's Background dropdown, its switch and the Brightness slider."""
    def panel(self, env=GRID):
        c = self.control(env)
        c.state.open_menu(1)
        return c

    def ops(self, data):
        return [f['op'] for f in self.lines(data)]

    def menu(self, data):
        return next(f['payload'] for f in self.lines(data) if f['op'] == 'placement_menu')

    def test_every_panel_shows_it(self):
        for desktop in range(3):
            s = view.ViewState(NAMES, environment={**GRID, 'brightness': 40})
            s.open_menu(desktop)
            frame = json.loads(view.placement_menu_frame('run', 's', 4, s.menu()))
            self.assertEqual((frame['payload']['background'], frame['payload']['brightness'], frame['payload']['background_picker']), ('grid', 40, False))

    def test_the_box_opens_and_closes_its_list(self):
        c = self.panel()
        self.assertTrue(self.menu(c.background_box(1))['background_picker'])
        self.assertFalse(self.menu(c.background_box(1))['background_picker'])
        self.assertEqual(c.background_box(0), b'')  # another desktop's panel: stale

    def test_one_list_at_a_time(self):
        c = self.panel()
        c.state.scale_picker = True
        menu = self.menu(c.background_box(1))
        self.assertTrue(menu['background_picker'])
        self.assertFalse(c.state.scale_picker)

    def test_a_pick_applies_saves_and_closes_the_list(self):
        c = self.panel(NONE)
        c.background_box(1)
        out = c.background_pick(1, 'grid')
        self.assertEqual(self.ops(out), ['environment', 'placement_menu'])
        self.assertEqual(self.lines(out)[0]['payload'], {'background': 'grid', 'brightness': 100})
        menu = self.menu(out)
        self.assertEqual((menu['background'], menu['background_picker']), ('grid', False))
        self.assertEqual(self.saved(), GRID)

    def test_picking_what_it_already_is_just_closes_the_list(self):
        c = self.panel()
        c.background_box(1)
        out = c.background_pick(1, 'grid')
        self.assertEqual(self.ops(out), ['placement_menu'])
        self.assertFalse(self.menu(out)['background_picker'])

    def test_a_press_elsewhere_closes_the_list(self):
        c = self.panel()
        self.assertEqual(c.background_close(), b'')
        c.background_box(1)
        self.assertFalse(self.menu(c.background_close())['background_picker'])

    def test_any_panel_key_or_collapse_closes_the_list(self):
        c = self.panel()
        c.background_box(1)
        self.assertFalse(self.menu(c.on_message({'op': 'menu_field_next', 'payload': {}}))['background_picker'])
        c.background_box(1)
        c.on_message({'op': 'menu_toggle', 'payload': {}})
        self.assertFalse(c.state.background_picker)

    def test_the_switch_is_the_same_as_the_key(self):
        c = self.panel()
        out = c.background_switch(1)
        self.assertEqual(self.ops(out), ['environment', 'placement_menu'])
        self.assertEqual(self.menu(out)['background'], 'none')
        self.assertEqual(self.menu(c.background_switch(1))['background'], 'grid')
        self.assertEqual(self.saved(), GRID)

    def test_the_key_updates_an_open_panel(self):
        c = self.panel()
        self.assertEqual([f['op'] for f in self.sent(c, TOGGLE_OP)], ['environment', 'placement_menu'])

    def test_stale_panel_clicks_do_nothing(self):
        c = self.panel()
        c.state.typing = True
        for call in (lambda: c.background_box(1), lambda: c.background_switch(1), lambda: c.background_pick(1, 'none')):
            self.assertEqual(call(), b'')
        c.state.typing, c.video_active = False, True
        self.assertEqual(c.background_switch(1), b'')
        self.assertEqual(c.state.environment, GRID)

    def test_the_slider_applies_live_and_saves_after_a_pause(self):
        c = self.panel()
        out = c.pill_slide(1, BRIGHTNESS_FIELD, 0.5, surface=dict(c.state.placements[1]))
        self.assertEqual(self.ops(out), ['environment', 'placement_menu'])
        self.assertEqual(self.lines(out)[0]['payload'], {'background': 'grid', 'brightness': 105})
        menu = self.menu(out)
        self.assertEqual((menu['field'], menu['brightness']), (BRIGHTNESS_FIELD, 105))
        self.assertFalse(self.layout.exists())  # not on every pointer move
        self.now += view.MENU_SAVE_DELAY
        c.poll(self.now)
        self.assertEqual(self.saved()['brightness'], 105)

    def test_a_release_saves_at_once(self):
        c = self.panel()
        c.pill_slide(1, BRIGHTNESS_FIELD, 1.0, surface=dict(c.state.placements[1]))
        c.flush()
        self.assertEqual(self.saved()['brightness'], 200)
        self.assertIsNone(c.environment_save_due)

    def test_the_slider_touches_no_placement(self):
        c = self.panel()
        before = [dict(p) for p in c.state.placements]
        out = c.pill_slide(1, BRIGHTNESS_FIELD, 0.2)
        self.assertNotIn('placement', self.ops(out))
        self.assertEqual(c.state.placements, before)
        self.assertIsNone(c.save_due)

    def test_with_none_the_slider_does_nothing(self):
        c = self.panel(NONE)
        self.assertNotIn('environment', self.ops(c.pill_slide(1, BRIGHTNESS_FIELD, 1.0)))
        self.assertEqual(c.state.environment, NONE)

    def test_up_down_reach_it_and_left_right_step_it(self):
        c = self.panel()
        for _ in range(4): c.on_message({'op': 'menu_field_next', 'payload': {}})
        self.assertEqual(c.state.menu_field, BRIGHTNESS_FIELD)
        before = [dict(p) for p in c.state.placements]
        small, big = environment.BRIGHTNESS_STEPS
        out = self.sent(c, 'menu_inc')
        self.assertEqual([f['op'] for f in out], ['environment', 'placement_menu'])
        self.assertEqual((out[0]['payload']['brightness'], out[1]['payload']['brightness']), (100 + small, 100 + small))
        self.assertEqual(self.sent(c, 'menu_dec_big')[0]['payload']['brightness'], 100 + small - big)
        self.assertEqual(c.state.placements, before)
        for _ in range(10): c.on_message({'op': 'menu_dec_big', 'payload': {}})
        self.assertEqual(c.state.environment['brightness'], 10)  # stops at its end
        self.assertEqual(self.sent(c, 'menu_dec'), [])

    def test_the_frame_refuses_bad_fields(self):
        s = view.ViewState(NAMES); s.open_menu(0)
        for bad in ({'background': 'cyan'}, {'background': 1}, {'brightness': 5}, {'brightness': True}, {'background_picker': 1},
                    {'background_picker': True, 'picker': True}, {'background_picker': True, 'open': False}, {'field': 'gain'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                view.placement_menu_frame('run', 's', 4, {**s.menu(), **bad})


class HitTests(unittest.TestCase):
    def test_the_rows_sit_between_the_scale_row_and_the_buttons(self):
        self.assertEqual(fp.PANEL_BACKGROUND_TOP, fp.PANEL_SCALE_TOP + fp.PANEL_ROW_H)
        self.assertEqual(fp.PANEL_BRIGHTNESS_TOP, fp.PANEL_BACKGROUND_TOP + fp.PANEL_ROW_H)
        self.assertGreaterEqual(fp.PANEL_ACTIONS_TOP, fp.PANEL_BRIGHTNESS_TOP + fp.PANEL_ROW_H)
        self.assertLessEqual(fp.action_rect(0)[1] + fp.PANEL_ACTION_H, fp.PANEL_H)

    def test_box_switch_and_slider(self):
        x, y, w, h = fp.PANEL_BACKGROUND_BOX
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1), ('background_box', 1))
        x, y, w, h = fp.PANEL_BACKGROUND_SWITCH
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1), ('background_switch', 1))
        ly = fp.PANEL_BRIGHTNESS_TOP + fp.PANEL_ROW_H / 2
        self.assertEqual(fp.panel_hit(fp.PANEL_KNOB_LO, ly, 1), ('slider', 1, BRIGHTNESS_FIELD, fp.PANEL_KNOB_LO))
        self.assertEqual(fp.panel_hit(fp.PANEL_KNOB_HI + fp.PANEL_SLOP_PX + 1, ly, 1), ('panel', 1))
        self.assertEqual(fp.panel_hit(30, ly, 1), ('panel', 1))  # the label is not the slider

    def test_the_open_list_takes_the_presses_over_it(self):
        for i, (name, _) in enumerate(environment.BACKGROUNDS):
            x, y, w, h = fp.background_item_rect(i)
            self.assertGreaterEqual(y, fp.PANEL_HEADER_H)
            self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1, background_picker=True), ('background', 1, name))
            self.assertNotEqual(fp.panel_hit(x + w / 2, y + h / 2, 1)[0], 'background')  # closed: what is under it

    def press(self, hit, picker=False):
        control = mock.Mock()
        control.state = view.ViewState(['XR-1', 'XR-2'])
        control.state.open_menu(1)
        control.state.background_picker = picker
        for name in ('background_box', 'background_pick', 'background_switch', 'background_close', 'scale_box', 'pill_close'):
            getattr(control, name).return_value = b''
        pointer = fp.OverviewPointer(control)
        pointer.mode, pointer.pointer = 'free', (0.0, 0.0)
        pointer.active = lambda: True
        pointer._hit = lambda: hit
        pointer._frames = lambda *a: b''
        pointer.on_op('resize_press')
        return control

    def test_presses_reach_the_controls(self):
        self.press(('background_box', 1)).background_box.assert_called_once_with(1)
        self.press(('background', 1, 'grid'), picker=True).background_pick.assert_called_once_with(1, 'grid')
        self.press(('background_switch', 1)).background_switch.assert_called_once_with(1)

    def test_a_press_on_the_panel_away_from_the_open_list_closes_it(self):
        c = self.press(('panel', 1), picker=True)
        c.background_close.assert_called_once_with()
        c.pill_close.assert_not_called()
        self.press(('background_box', 1), picker=True).background_close.assert_not_called()

    def test_they_count_as_on_the_panel(self):
        for hit in (('background_box', 1), ('background', 1, 'none'), ('background_switch', 1)):
            self.press(hit).pill_close.assert_not_called()


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
        """Down to the background's row (the last one)."""
        for _ in quality.SETTINGS: self.op('video_down')

    def menu(self, out):
        return next(f['payload'] for f in out if f['op'] == 'video' and f['payload']['action'] == 'menu')

    def test_a_row_after_the_video_settings(self):
        rows = self.menu(self.op('video_down'))['rows']
        self.assertEqual(rows[len(quality.SETTINGS)], {'label': 'Background', 'value': 'None'})
        self.assertEqual(len(rows), len(quality.SETTINGS) + 1)
        self.assertLessEqual(len(rows), video.MAX_MENU_ROWS)

    def test_left_right_walk_the_backgrounds(self):
        self.row()
        out = self.op('video_seek_fwd')
        self.assertEqual([f['payload']['background'] for f in out if f['op'] == 'environment'], ['grid'])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'Grid')
        self.assertEqual(view.load_settings(self.layout)['environment'], GRID)
        out = self.op('video_seek_fwd')
        self.assertEqual([f['payload']['background'] for f in out if f['op'] == 'environment'], ['smoke'])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'Smoke')
        out = self.op('video_seek_fwd')  # wraps round to None
        self.assertEqual([f['payload']['background'] for f in out if f['op'] == 'environment'], ['none'])
        self.assertEqual(self.menu(out)['rows'][-1]['value'], 'None')
        out = self.op('video_seek_back')  # and back
        self.assertEqual([f['payload']['background'] for f in out if f['op'] == 'environment'], ['smoke'])

    def test_the_video_settings_stay_as_they_were(self):
        self.row()
        before = self.v.quality()
        out = self.op('video_seek_fwd')
        self.assertEqual(self.v.quality(), before)
        self.assertFalse([f for f in out if f['op'] == 'video' and f['payload']['action'] == 'quality'])
        video.validate_renderer_payload(self.menu(out))

    def test_ctrl_alt_e_works_during_a_video(self):
        self.assertEqual([f['payload']['background'] for f in self.op(TOGGLE_OP) if f['op'] == 'environment'], ['grid'])


if __name__ == '__main__':
    unittest.main()
