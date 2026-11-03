"""The placement panel's Scale dropdown: a desktop's Hyprland output scale, picked in the glasses, applied live and saved.

The open panel (an expanded glass pill) has a Scale row under its sliders: a box with the desktop's scale; a click opens
a list of the steps (100..200% in quarter steps, then 250% and 300%: the broker's own); a click on a step reshapes that desktop's
output through the broker (desktops.SCALE) and the profile takes it, so the next session starts with it. Offline: the
broker is test_desktops' fake transport, the journal the real one; nothing talks to a compositor.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.native_spike import desktops as dk, free_pointer as fp, geometry as g, profiles, view
from tools.native_spike.hyprland import plan_outputs
from test_desktops import D169, FLAT, TILTED, Run, SupervisorStandIn, frames
from test_placement_menu import FIXTURE, check_menu_payload

QHD = {'width': 2560, 'height': 1440, 'scale': 1.0}


class StepTests(unittest.TestCase):
    def test_the_steps_are_one_list_everywhere(self):
        """geometry.SCALES, the wire's percents, the renderer's kScalePercents, the broker's steps, the panel."""
        self.assertEqual(g.SCALES, (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0))  # Cody: 125% and more, up to 300%
        self.assertEqual(view.SCALE_PERCENTS, tuple(round(s * 100) for s in g.SCALES))
        root = Path(__file__).resolve().parents[2]
        self.assertIn('kScalePercents{100, 125, 150, 175, 200, 250, 300}', (root / 'spike/include/xr/desktops.hpp').read_text())
        # The broker: quarter steps 1.0 .. 2.0, then half steps to 3.0 (n = scale * 4).
        self.assertIn('n<4 || n>12 || (n>8 && n%2)', (root / 'spike/ownership/registry.cpp').read_text())
        self.assertIn('const SCALES = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];', (root / 'omarchy-plugin/cody.viture-workspace/Model.js').read_text())
        self.assertIn('model: Model.SCALES', (root / 'omarchy-plugin/cody.viture-workspace/DesktopEditor.qml').read_text())

    def test_with_scale_keeps_the_size_and_refuses_inexact_scales(self):
        self.assertEqual(g.with_scale(D169, 1.5), {'width': 1920, 'height': 1080, 'scale': 1.5})
        self.assertEqual(g.with_scale(QHD, 1.25), {'width': 2560, 'height': 1440, 'scale': 1.25})
        self.assertEqual(g.with_scale(D169, 2.5), {'width': 1920, 'height': 1080, 'scale': 2.5})
        self.assertEqual(g.with_scale(D169, 3), {'width': 1920, 'height': 1080, 'scale': 3.0})
        for bad in (1.75, 1.1, 2.25, 2.75, 3.5, 4.0, 0.5, True, '1.5', None):  # 1920 / 1.75 is not a whole pixel count
            with self.subTest(bad), self.assertRaises(ValueError): g.with_scale(D169, bad)

    def test_valid_steps_by_resolution(self):
        self.assertEqual(g.valid_scales(1920, 1080), [1.0, 1.25, 1.5, 2.0, 2.5, 3.0])  # 1920/1.75 is not a whole pixel count
        self.assertEqual(g.valid_scales(2560, 1440), [1.0, 1.25, 2.0, 2.5])  # 2560 / 1.5 and 2560 / 3 are not whole either
        self.assertEqual(g.valid_scales(2800, 1400), [1.0, 1.25, 1.75, 2.0, 2.5])  # 1.75 where 7 divides the size
        self.assertEqual(g.valid_scales(3840, 1080), [1.0, 1.25, 1.5, 2.0, 2.5, 3.0])  # the ultrawide


class PlanTests(unittest.TestCase):
    def test_scale_reshapes_in_place_and_slides_the_right_neighbours(self):
        specs = plan_outputs([], [], 'run')  # XR-1..3, 1920x1080 at x 0, 1920, 3840
        p = dk.plan(specs, [TILTED, FLAT, FLAT], dk.SCALE, 0, scale=1.5)
        # 1920 px at 1.5 is 1280 logical px wide: the others slide left, the shrinking one first (left to right).
        self.assertEqual([(s['name'], s['width'], s['scale'], s['x']) for s in p.specs],
                         [('XR-1', 1920, 1.5, 0), ('XR-2', 1920, 1.0, 1280), ('XR-3', 1920, 1.0, 3200)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual((p.resized, p.create, p.remove, p.focus), (['XR-1'], [], None, 0))
        self.assertEqual(p.placements, [TILTED, FLAT, FLAT])
        back = dk.plan(p.specs, p.placements, dk.SCALE, 0, scale=1.0)  # growing: the right end makes room first
        self.assertEqual([s['name'] for s in back.reshape], ['XR-3', 'XR-2', 'XR-1'])
        self.assertEqual([s['x'] for s in back.specs], [0, 1920, 3840])

    def test_a_scale_that_does_not_divide_the_desktop_is_refused(self):
        specs = plan_outputs([], [], 'run')
        for bad in (1.75, 2.25, 3.5, None):
            with self.subTest(bad), self.assertRaises(ValueError): dk.plan(specs, [FLAT] * 3, dk.SCALE, 1, scale=bad)

    def test_the_new_large_steps_plan_like_the_others(self):
        """250% and 300% (2026-11-03): 1920 px is 768 / 640 logical px wide, so the right neighbours slide further left."""
        specs = plan_outputs([], [], 'run')
        for scale, wide in ((2.5, 768), (3.0, 640)):
            p = dk.plan(specs, [FLAT] * 3, dk.SCALE, 1, scale=scale)
            self.assertEqual([(s['name'], s['scale'], s['x']) for s in p.specs],
                             [('XR-1', 1.0, 0), ('XR-2', scale, 1920), ('XR-3', 1.0, 1920 + wide)])
            self.assertEqual(p.resized, ['XR-2'])


class StateTests(unittest.TestCase):
    def state(self):
        s = view.ViewState(['XR-1', 'XR-2', 'XR-3'])
        s.sizes = [dict(D169), {**D169, 'scale': 1.25}, dict(QHD)]
        return s

    def test_the_panel_payload_carries_the_open_desktops_scale(self):
        s = self.state()
        s.open_menu(1)
        m = s.menu()
        self.assertEqual((m['scale'], m['scales'], m['picker']), (125, [100, 125, 150, 200, 250, 300], False))
        s.open_menu(2)
        self.assertEqual((s.menu()['scale'], s.menu()['scales']), (100, [100, 125, 200, 250]))
        check_menu_payload(s.menu())
        frame = json.loads(view.placement_menu_frame('run', 's', 1, s.menu()))
        self.assertEqual(frame['payload']['scale'], 100)

    def test_no_sizes_no_dropdown(self):
        s = view.ViewState(['XR-1'])
        self.assertNotIn('scale', s.menu())

    def test_the_list_closes_on_any_switch_collapse_or_key(self):
        s = self.state()
        for close in (lambda: s.apply_menu('menu_desktop_next'), lambda: s.apply_menu('menu_toggle'),
                      lambda: s.apply_menu('menu_field_next'), lambda: s.open_menu(0),
                      lambda: s.set_desktops(s.names, s.placements, s.wide, sizes=s.sizes)):
            s.open_menu(1); s.scale_picker = True
            self.assertTrue(s.menu()['picker'])
            close()
            self.assertFalse(s.scale_picker)
            self.assertFalse(s.menu()['picker'])

    def test_frames_encode_every_fixture_payload_and_refuse_the_bad_ones(self):
        cases = json.loads(FIXTURE.read_text())
        scaled = [p for p in cases['valid'] if 'scale' in p or 'scales' in p or 'picker' in p]
        self.assertGreaterEqual(len(scaled), 3)
        for p in scaled:
            with self.subTest(p=p): self.assertEqual(json.loads(view.placement_menu_frame('r', 's', 1, p))['payload'], p)
        for case in cases['invalid']:
            p = case['payload']
            if not isinstance(p, dict) or not {'scale', 'scales', 'picker'} & set(p): continue
            with self.subTest(case['why']), self.assertRaises(ValueError): view.placement_menu_frame('r', 's', 1, p)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(self)
        self.now = 100.0
        state = view.ViewState(['XR-1', 'XR-2', 'XR-3'], placements=[TILTED, FLAT, FLAT])
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, None, None, clock=lambda: self.now)
        self.c.desktops = self.run.manager
        state.wide, state.sizes = self.run.manager.wide(), self.run.manager.desktops()  # as managed.view_controls does
        self.told = []
        self.c.notify = self.told.append
        self.sup = SupervisorStandIn()
        self.c.attach_supervisor(self.sup)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name) / 'profiles.json'
        self.run.manager.store = profiles.PlacementStore(self.profile, 'luma-ultra')

    def collect(self):
        out = self.c.poll(self.now)
        self.c.join_focus()
        return out

    def test_pick_a_scale_end_to_end_live_and_saved(self):
        """Regression: picking 150% in the open panel's Scale list reshapes XR-2's output at scale 1.5 through the broker
        (the live Hyprland output), the renderer and the panel follow, and the profile keeps it for the next start."""
        self.c.state.open_menu(1)
        box = frames(self.c.scale_box(1))
        self.assertEqual([f['op'] for f in box], ['placement_menu'])
        self.assertEqual((box[0]['payload']['picker'], box[0]['payload']['scale']), (True, 100))
        self.run.transport.wires.clear()
        before = frames(self.c.scale_pick(1, 150))
        self.assertEqual([f['op'] for f in before], ['placement_menu'])
        self.assertTrue(before[0]['payload']['busy'])
        self.assertFalse(before[0]['payload']['picker'])
        self.assertEqual(self.sup.calls, ['pause'])
        reshapes = [json.loads(w[10:])['payload'] for w in self.run.transport.wires if json.loads(w[10:])['op'] == 'reshape']
        self.assertEqual([(p['name'], p['scale'], p['x']) for p in reshapes], [('XR-2', 1.5, 1920), ('XR-3', 1.0, 3200)])
        after = frames(self.collect())
        self.assertEqual([f['op'] for f in after], ['desktops', 'view', 'placement', 'placement_menu'])
        self.assertEqual(after[0]['payload'], {'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-2']})
        menu = after[3]['payload']
        self.assertEqual((menu['open'], menu['desktop'], menu['scale'], menu['busy'], menu['picker']), (True, 1, 150, False, False))
        self.assertEqual(self.run.manager.desktops()[1], {**D169, 'scale': 1.5})
        self.assertEqual(self.c.status_reply({})['desktops'][1]['scale'], 1.5)
        saved = profiles.load_profile(self.profile, 'luma-ultra')
        self.assertEqual(saved['desktops'], [D169, {**D169, 'scale': 1.5}, D169])
        self.assertEqual(saved['placements'], [TILTED, FLAT, FLAT])
        self.assertEqual(self.told, [])
        # The next start's desktops (what the controller puts in the consent) keep it.
        self.assertEqual(profiles.load_profile(self.profile, 'luma-ultra')['desktops'][1]['scale'], 1.5)

    def test_the_current_step_or_a_dimmed_one_changes_nothing(self):
        self.c.state.open_menu(0)
        self.c.scale_box(0)
        out = frames(self.c.scale_pick(0, 100))  # already 100%
        self.assertEqual(([f['op'] for f in out], out[0]['payload']['picker']), (['placement_menu'], False))
        self.c.scale_box(0)
        self.c.scale_pick(0, 175)  # 1920x1080 / 1.75 is not whole: dimmed
        self.assertEqual(self.sup.calls, [])
        self.assertFalse(self.run.manager.busy())

    def test_only_on_the_open_panel_and_never_while_busy(self):
        self.assertEqual(self.c.scale_box(0), b'')  # closed
        self.c.state.open_menu(1)
        self.assertEqual(self.c.scale_box(0), b'')  # another desktop's panel
        self.assertEqual(self.c.scale_pick(1, 150), b'')  # the list is not open
        self.c.state.desktops_busy = True
        self.assertEqual(self.c.scale_box(1), b'')
        self.assertFalse(self.c.state.scale_picker)

    def test_typing_mode_and_no_manager_do_nothing(self):
        self.c.state.open_menu(1)
        self.c.state.typing = True
        self.assertEqual(self.c.scale_box(1), b'')
        self.c.state.typing = False
        self.c.desktops = None
        self.assertEqual(self.c.scale_box(1), b'')

    def test_removing_a_desktop_keeps_the_others_sizes_in_step(self):
        self.c.state.open_menu(1)
        self.c.scale_box(1); self.c.scale_pick(1, 125); self.collect()
        self.c.state.open_menu(0)
        self.c.desktop_action(0, 'remove'); self.c.desktop_action(0, 'remove')
        self.assertEqual(len(self.c.state.sizes), len(self.c.state.names))
        self.collect()
        self.assertEqual(self.c.state.names, ['XR-2', 'XR-3'])
        self.assertEqual([d['scale'] for d in self.c.state.sizes], [1.25, 1.0])


class PointerTests(unittest.TestCase):
    def test_the_box_and_the_list_are_hit_on_the_open_panel(self):
        x, y, w, h = fp.PANEL_SCALE_BOX
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1), ('scale_box', 1))
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1, picker=True), ('scale_box', 1))  # closes it again
        for i, percent in enumerate(view.SCALE_PERCENTS):
            ix, iy, iw, ih = fp.scale_item_rect(i)
            self.assertGreaterEqual(iy, fp.PANEL_HEADER_H)
            self.assertEqual(fp.panel_hit(ix + iw / 2, iy + ih / 2, 1, picker=True), ('scale', 1, percent))
            self.assertNotEqual(fp.panel_hit(ix + iw / 2, iy + ih / 2, 1)[0], 'scale')  # closed: the sliders there
        # Open, no slider grabs: not beside or under the list, nor in the first row above it (since the Resolution row
        # went above Scale, the seven steps reach the second slider row); closed, they all do again.
        self.assertEqual(fp.panel_hit(fp.PANEL_SCALE_BOX[0] - 3, fp.scale_item_rect(0)[1] + 3, 1, picker=True), ('panel', 1))
        self.assertTrue(fp.PANEL_ROWS_TOP < fp.scale_item_rect(0)[1] <= fp.PANEL_ROWS_TOP + fp.PANEL_ROW_H)
        self.assertNotEqual(fp.panel_hit(fp.PANEL_KNOB_LO, fp.PANEL_ROWS_TOP + 3, 1, picker=True)[0], 'slider')
        self.assertEqual(fp.panel_hit(fp.PANEL_KNOB_LO, fp.PANEL_ROWS_TOP + 3, 1)[0], 'slider')
        # The desktop row is under the Scale row, inside the panel.
        self.assertGreaterEqual(fp.PANEL_ACTIONS_TOP, y + h)
        self.assertLessEqual(fp.action_rect(0)[1] + fp.PANEL_ACTION_H, fp.PANEL_H)

    def press(self, hit, picker=False):
        control = mock.Mock()
        control.state = view.ViewState(['XR-1', 'XR-2'])
        control.state.open_menu(1)
        control.state.scale_picker = picker
        for name in ('scale_box', 'scale_pick', 'scale_close', 'desktop_action', 'pill_close'):
            getattr(control, name).return_value = b''
        pointer = fp.OverviewPointer(control)
        self.addCleanup(pointer.close)
        pointer.mode, pointer.pointer, pointer.geometry = 'free', (10.0, 10.0), {'w': 100, 'h': 100}
        pointer.outputs = [{'index': 0}]
        with mock.patch.object(fp.OverviewPointer, '_hit', return_value=hit), \
                mock.patch.object(fp.OverviewPointer, 'active', return_value=True), \
                mock.patch.object(fp.OverviewPointer, '_handle', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_pills', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_pointer_frame', return_value=b''), \
                mock.patch.object(fp.OverviewPointer, '_roll_frame', return_value=b''):  # the level bars always show
            pointer.on_op('resize_press')
        return control

    def test_presses_reach_the_control(self):
        c = self.press(('scale_box', 1))
        c.scale_box.assert_called_once_with(1); c.pill_close.assert_not_called(); c.scale_close.assert_not_called()
        c = self.press(('scale', 1, 150), picker=True)
        c.scale_pick.assert_called_once_with(1, 150); c.pill_close.assert_not_called(); c.scale_close.assert_not_called()
        c = self.press(('panel', 1), picker=True)  # elsewhere on the panel: the list closes
        c.scale_close.assert_called_once_with(); c.pill_close.assert_not_called()
        c = self.press(('panel', 1))  # closed list: nothing to close
        c.scale_close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
