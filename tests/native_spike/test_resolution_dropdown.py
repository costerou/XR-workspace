"""The placement panel's Resolution dropdown: a desktop's pixel size, picked in the glasses, applied live and saved.

The open panel (an expanded glass pill) has a Resolution row above its Scale row: a box with the desktop's size; a click
opens a list of the common sizes by shape (16:9, 16:10, ultrawide: geometry.RESOLUTIONS); a click on one resizes that
desktop's own output in place through the broker (desktops.RESOLUTION: the same reshape as Ultrawide and Scale, never a
remove and re-create, so its windows stay on it and nothing else is modeset), keeping a scale that still divides the
new size and otherwise taking the nearest one that does, and the profile takes it, so the next session starts with it.
Offline: the broker is test_desktops' fake transport, the journal the real one; nothing talks to a compositor.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.native_spike import desktops as dk, free_pointer as fp, geometry as g, profiles, view
from tools.native_spike.hyprland import plan_outputs
from test_desktops import D169, FLAT, TILTED, Run, SupervisorStandIn, frames
from test_placement_menu import FIXTURE, check_menu_payload

ROOT = Path(__file__).resolve().parents[2]
QHD = {'width': 2560, 'height': 1440, 'scale': 1.0}
CODY = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160),   # 16:9
        (1680, 1050), (1920, 1200), (2560, 1600),                             # 16:10
        (2560, 1080), (3440, 1440), (3840, 1080), (5120, 1440))               # ultrawide


class ListTests(unittest.TestCase):
    def test_the_sizes_are_one_list_everywhere(self):
        """geometry.RESOLUTIONS (by shape), the renderer's kResolutions / kResolutionGroups, its size bounds."""
        self.assertEqual(g.RESOLUTIONS, CODY)  # Cody, 2026-11-03: 16:9, 16:10 and ultrawide sizes
        self.assertEqual([name for name, _ in g.RESOLUTION_GROUPS], ['16:9', '16:10', 'Ultrawide'])
        hpp = (ROOT / 'spike/include/xr/desktops.hpp').read_text()
        listed = re.search(r'kResolutions\{\{(.*?)\}\};', hpp, re.S).group(1)
        self.assertEqual(tuple((int(w), int(h)) for w, h in re.findall(r'\{(\d+), (\d+)\}', listed)), g.RESOLUTIONS)
        self.assertIn('kResolutionGroups{{{"16:9", 5}, {"16:10", 3}, {"Ultrawide", 4}}}', hpp)
        (wlo, whi), (hlo, hhi) = g.CUSTOM_BOUNDS['width'], g.CUSTOM_BOUNDS['height']
        self.assertIn(f'kDesktopMinW = {wlo}, kDesktopMaxW = {whi}, kDesktopMinH = {hlo}, kDesktopMaxH = {hhi};', hpp)
        self.assertIn(f'if(w<{wlo} || w>{whi} || h<{hlo} || h>{hhi} || w%2 || h%2) return false;',
                      (ROOT / 'spike/ownership/registry.cpp').read_text())  # the broker's own bound

    def test_every_size_is_a_desktop_the_broker_takes_and_the_ultrawides_are_ultrawide(self):
        for w, h in g.RESOLUTIONS:
            with self.subTest((w, h)): self.assertEqual(g.validate_desktop({'width': w, 'height': h, 'scale': 1.0})['width'], w)
        self.assertEqual([size for size in g.RESOLUTIONS if g.is_ultrawide({'width': size[0], 'height': size[1]})],
                         list(g.RESOLUTION_GROUPS[2][1]))  # the Ultrawide button lights for exactly those
        self.assertEqual(g.toggle_ultrawide(D169)['width'], 2560)  # the Ultrawide button's own sizes are in the list
        self.assertEqual(g.toggle_ultrawide(QHD)['width'], 3440)

    def test_with_resolution_keeps_a_scale_that_divides_the_new_size(self):
        self.assertEqual(g.with_resolution(D169, 2560, 1440), QHD)
        self.assertEqual(g.with_resolution({**D169, 'scale': 1.5}, 1680, 1050), {'width': 1680, 'height': 1050, 'scale': 1.5})
        self.assertEqual(g.with_resolution({**D169, 'scale': 2.0}, 3840, 2160), {'width': 3840, 'height': 2160, 'scale': 2.0})
        self.assertEqual(g.with_resolution({**QHD, 'scale': 1.25}, 1280, 720), {'width': 1280, 'height': 720, 'scale': 1.25})

    def test_an_invalid_scale_becomes_the_nearest_valid_one(self):
        cases = {(1920, 1080, 1.5, 2560, 1440): 1.25,   # 2560 / 1.5 is not whole: 1.25 is nearer than 2
                 (1920, 1080, 3.0, 2560, 1440): 2.5,    # 300% does not divide 2560: 250%
                 (1920, 1080, 3.0, 1280, 720): 2.5,
                 (2800, 1400, 1.75, 1920, 1080): 2.0,   # 1.5 and 2 are as near: the larger, text never shrinks more
                 (1920, 1080, 1.5, 5120, 1440): 1.25,
                 (1920, 1080, 1.5, 1600, 900): 1.25}
        for (w, h, s, nw, nh), want in cases.items():
            with self.subTest((w, h, s, nw, nh)):
                got = g.with_resolution({'width': w, 'height': h, 'scale': s}, nw, nh)
                self.assertEqual(got, {'width': nw, 'height': nh, 'scale': want})
                self.assertNotIn(s, g.valid_scales(nw, nh))

    def test_every_listed_size_from_every_scale_is_valid(self):
        for w, h in ((1920, 1080), (2800, 1400), (3440, 1440)):
            for s in g.valid_scales(w, h):
                for nw, nh in g.RESOLUTIONS:
                    got = g.with_resolution({'width': w, 'height': h, 'scale': s}, nw, nh)
                    self.assertIn(got['scale'], g.valid_scales(nw, nh))
                    if s in g.valid_scales(nw, nh): self.assertEqual(got['scale'], s)
                    else: self.assertEqual(got['scale'], g.nearest_scale(nw, nh, s))

    def test_refuses_sizes_the_broker_would_refuse(self):
        for bad in ((1921, 1080), (1920, 1079), (320, 240), (8192, 1440), (3840, 4322), (True, 1080), ('1920', 1080),
                    (1920.0, 1080), (None, 1080)):
            with self.subTest(bad), self.assertRaises(ValueError): g.with_resolution(D169, *bad)


class PlanTests(unittest.TestCase):
    def test_growing_reshapes_in_place_and_slides_the_right_neighbours(self):
        specs = plan_outputs([], [], 'run')  # XR-1..3, 1920x1080 at x 0, 1920, 3840
        p = dk.plan(specs, [TILTED, FLAT, FLAT], dk.RESOLUTION, 0, size=(2560, 1440))
        self.assertEqual([(s['name'], s['width'], s['height'], s['scale'], s['x']) for s in p.specs],
                         [('XR-1', 2560, 1440, 1.0, 0), ('XR-2', 1920, 1080, 1.0, 2560), ('XR-3', 1920, 1080, 1.0, 4480)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-3', 'XR-2', 'XR-1'])  # the right end makes room first
        self.assertEqual([s['id'] for s in p.specs], [s['id'] for s in specs])  # the same outputs: nothing re-created
        self.assertEqual((p.resized, p.create, p.remove, p.focus), (['XR-1'], [], None, 0))
        self.assertEqual(p.placements, [TILTED, FLAT, FLAT])  # every desktop stays where it is in the glasses

    def test_shrinking_goes_left_to_right(self):
        specs = plan_outputs([], [], 'run')
        p = dk.plan(specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(1280, 720))
        self.assertEqual([(s['name'], s['width'], s['x']) for s in p.specs], [('XR-1', 1920, 0), ('XR-2', 1280, 1920), ('XR-3', 1920, 3200)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-2', 'XR-3'])
        self.assertEqual(p.resized, ['XR-2'])

    def test_the_last_desktop_moves_nobody(self):
        specs = plan_outputs([], [], 'run')
        p = dk.plan(specs, [FLAT] * 3, dk.RESOLUTION, 2, size=(5120, 1440))
        self.assertEqual([s['name'] for s in p.reshape], ['XR-3'])
        self.assertEqual((p.specs[2]['width'], p.specs[2]['height'], p.specs[2]['x']), (5120, 1440, 3840))

    def test_a_scale_the_new_size_cannot_take_moves_to_the_nearest_step(self):
        specs = plan_outputs([], [], 'run', [D169, {**D169, 'scale': 1.5}, D169])
        p = dk.plan(specs, [FLAT] * 3, dk.RESOLUTION, 1, size=(2560, 1440))
        self.assertEqual((p.specs[1]['width'], p.specs[1]['scale']), (2560, 1.25))
        self.assertEqual(p.specs[2]['x'], 1920 + 2048)  # 2560 px at 125% is 2048 logical px wide

    def test_bad_sizes_are_refused(self):
        specs = plan_outputs([], [], 'run')
        for bad in (None, 1920, (1920,), (1920, 1080, 60), (1921, 1080), (100, 100)):
            with self.subTest(bad), self.assertRaises(ValueError): dk.plan(specs, [FLAT] * 3, dk.RESOLUTION, 1, size=bad)


class StateTests(unittest.TestCase):
    def state(self):
        s = view.ViewState(['XR-1', 'XR-2', 'XR-3'])
        s.sizes = [dict(D169), {**D169, 'scale': 1.25}, dict(QHD)]
        return s

    def test_the_panel_payload_carries_the_open_desktops_size(self):
        s = self.state()
        s.open_menu(2)
        m = s.menu()
        self.assertEqual((m['resolution'], m['resolution_picker'], m['scale'], m['scales']), ([2560, 1440], False, 100, [100, 125, 200, 250]))
        s.resolution_picker = True
        check_menu_payload(s.menu())
        frame = json.loads(view.placement_menu_frame('run', 's', 1, s.menu()))
        self.assertEqual((frame['payload']['resolution'], frame['payload']['resolution_picker']), ([2560, 1440], True))

    def test_no_sizes_no_dropdown(self):
        self.assertNotIn('resolution', view.ViewState(['XR-1']).menu())

    def test_the_list_closes_on_any_switch_collapse_or_key(self):
        s = self.state()
        for close in (lambda: s.apply_menu('menu_desktop_next'), lambda: s.apply_menu('menu_toggle'),
                      lambda: s.apply_menu('menu_field_next'), lambda: s.apply_menu('menu_reset'), lambda: s.open_menu(0),
                      lambda: s.set_desktops(s.names, s.placements, s.wide, sizes=s.sizes)):
            s.open_menu(1); s.resolution_picker = True
            self.assertTrue(s.menu()['resolution_picker'])
            close()
            self.assertFalse(s.resolution_picker)
            self.assertFalse(s.menu()['resolution_picker'])

    def test_frames_encode_every_fixture_payload_and_refuse_the_bad_ones(self):
        cases = json.loads(FIXTURE.read_text())
        sized = [p for p in cases['valid'] if {'resolution', 'resolution_picker'} & set(p)]
        self.assertGreaterEqual(len(sized), 4)
        for p in sized:
            with self.subTest(p=p):
                check_menu_payload(p)
                self.assertEqual(json.loads(view.placement_menu_frame('r', 's', 1, p))['payload'], p)
        refused = 0
        for case in cases['invalid']:
            p = case['payload']
            if not isinstance(p, dict) or not {'resolution', 'resolution_picker'} & set(p): continue
            refused += 1
            with self.subTest(case['why']):
                with self.assertRaises(ValueError): view.placement_menu_frame('r', 's', 1, p)
                with self.assertRaises(ValueError): check_menu_payload(p)
        self.assertGreaterEqual(refused, 10)


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

    def broker_ops(self):
        return [json.loads(w[10:]) for w in self.run.transport.wires]

    def pick(self, desktop, size):
        self.c.state.open_menu(desktop)
        self.c.resolution_box(desktop)
        return self.c.resolution_pick(desktop, size)

    def test_pick_a_resolution_end_to_end_live_in_place_and_saved(self):
        """Picking 2560x1440 in XR-2's open Resolution list resizes XR-2's own Hyprland output live (a broker reshape,
        never a remove or create: its workspaces and windows stay on it, no other output is touched but the one sliding
        along the row), the renderer renews only that capture, the panel shows the new size with the Scale steps of the
        new size, every placement stays, and the profile keeps the size for the next start."""
        self.c.state.open_menu(1)
        box = frames(self.c.resolution_box(1))
        self.assertEqual([f['op'] for f in box], ['placement_menu'])
        self.assertEqual((box[0]['payload']['resolution_picker'], box[0]['payload']['resolution']), (True, [1920, 1080]))
        self.run.transport.wires.clear()
        before = frames(self.c.resolution_pick(1, (2560, 1440)))
        self.assertEqual([f['op'] for f in before], ['placement_menu'])  # no desktops frame: the capture keeps going
        self.assertTrue(before[0]['payload']['busy'])
        self.assertFalse(before[0]['payload']['resolution_picker'])
        self.assertEqual(self.sup.calls, ['pause'])
        ops = self.broker_ops()
        self.assertEqual({o['op'] for o in ops}, {'reshape', 'inspect'})  # in place (and read back): nothing removed or created
        reshapes = [o['payload'] for o in ops if o['op'] == 'reshape']
        self.assertEqual([(p['name'], p['width'], p['height'], p['x']) for p in reshapes],
                         [('XR-3', 1920, 1080, 4480), ('XR-2', 2560, 1440, 1920)])
        after = frames(self.collect())
        self.assertEqual([f['op'] for f in after], ['desktops', 'view', 'placement', 'placement_menu'])
        self.assertEqual(after[0]['payload'], {'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-2']})
        self.assertEqual(after[2]['payload']['desktops'], [TILTED, FLAT, FLAT])
        menu = after[3]['payload']
        self.assertEqual((menu['open'], menu['desktop'], menu['resolution'], menu['busy'], menu['resolution_picker']),
                         (True, 1, [2560, 1440], False, False))
        self.assertEqual((menu['scale'], menu['scales']), (100, [100, 125, 200, 250]))  # 150% and 300% grey out now
        self.assertEqual(self.run.manager.desktops()[1], QHD)
        self.assertEqual(self.c.status_reply({})['desktops'][1], QHD)
        self.assertEqual(self.sup.calls[-1][0], 'replace')
        saved = profiles.load_profile(self.profile, 'luma-ultra')
        self.assertEqual(saved['desktops'], [D169, QHD, D169])  # the per-desktop setup the next start restores
        self.assertEqual(saved['placements'], [TILTED, FLAT, FLAT])
        setup, why = profiles.load_setup(self.profile, 'luma-ultra')
        self.assertIsNone(why)
        self.assertEqual(setup['desktops'], [D169, QHD, D169])
        self.assertEqual(self.told, [])

    def test_a_scale_the_new_size_cannot_take_becomes_the_nearest_step(self):
        self.c.state.open_menu(1)
        self.c.scale_box(1); self.c.scale_pick(1, 150); self.collect()
        self.assertEqual(self.c.state.menu()['scales'], [100, 125, 150, 200, 250, 300])
        self.pick(1, (2560, 1440))
        menu = frames(self.collect())[-1]['payload']
        self.assertEqual((menu['resolution'], menu['scale'], menu['scales']), ([2560, 1440], 125, [100, 125, 200, 250]))
        self.assertEqual(self.run.manager.desktops()[1], {**QHD, 'scale': 1.25})
        self.assertEqual(profiles.load_profile(self.profile, 'luma-ultra')['desktops'][1], {**QHD, 'scale': 1.25})
        self.pick(1, (1920, 1080))  # back: 125% divides 1920x1080 too, so it stays
        self.collect()
        self.assertEqual(self.run.manager.desktops()[1], {**D169, 'scale': 1.25})

    def test_an_ultrawide_size_lights_ultrawide_and_back(self):
        self.pick(0, (3440, 1440)); self.collect()
        self.assertTrue(self.c.state.menu()['wide'])
        self.pick(0, (1680, 1050)); self.collect()
        self.assertEqual(self.run.manager.desktops()[0], {'width': 1680, 'height': 1050, 'scale': 1.0})
        self.assertFalse(self.c.state.menu()['wide'])

    def test_the_current_size_or_an_unlisted_one_changes_nothing(self):
        out = frames(self.pick(0, (1920, 1080)))  # its size already
        self.assertEqual(([f['op'] for f in out], out[0]['payload']['resolution_picker']), (['placement_menu'], False))
        for unlisted in ((2800, 1400), (1920, 1080, 60), '1920x1080', None):
            self.c.resolution_box(0)
            self.c.resolution_pick(0, unlisted)
        self.assertEqual(self.sup.calls, [])
        self.assertFalse(self.run.manager.busy())

    def test_only_on_the_open_panel_and_never_while_busy(self):
        self.assertEqual(self.c.resolution_box(0), b'')  # closed
        self.c.state.open_menu(1)
        self.assertEqual(self.c.resolution_box(0), b'')  # another desktop's panel
        self.assertEqual(self.c.resolution_pick(1, (2560, 1440)), b'')  # the list is not open
        self.c.state.desktops_busy = True
        self.assertEqual(self.c.resolution_box(1), b'')
        self.assertFalse(self.c.state.resolution_picker)

    def test_typing_mode_video_and_no_manager_do_nothing(self):
        self.c.state.open_menu(1)
        self.c.state.typing = True
        self.assertEqual(self.c.resolution_box(1), b'')
        self.c.state.typing = False
        self.c.video_active = True  # a VR video plays in place of the desktops
        self.assertEqual(self.c.resolution_box(1), b'')
        self.c.video_active = False
        self.c.desktops = None
        self.assertEqual(self.c.resolution_box(1), b'')
        self.assertFalse(self.c.state.resolution_picker)

    def test_one_list_at_a_time(self):
        self.c.state.open_menu(1)
        self.c.resolution_box(1)
        self.assertTrue(self.c.state.resolution_picker)
        menu = frames(self.c.scale_box(1))[0]['payload']  # the Scale box: its list opens, this one closes
        self.assertEqual((menu['picker'], menu['resolution_picker']), (True, False))
        menu = frames(self.c.resolution_box(1))[0]['payload']
        self.assertEqual((menu['picker'], menu['resolution_picker']), (False, True))
        self.assertEqual(frames(self.c.resolution_close())[0]['payload']['resolution_picker'], False)
        self.assertEqual(self.c.resolution_close(), b'')  # already closed

    def test_a_refused_reshape_is_told_and_the_panel_comes_back(self):
        self.run.transport.refuse_reshape = True
        self.pick(2, (3840, 2160))
        after = frames(self.collect())
        self.assertEqual(after[-1]['payload']['busy'], False)
        self.assertEqual(len(self.told), 1)  # the run's guard then judges the output (and ends the run if it is wrong)

    def test_the_ultrawide_button_and_the_list_agree_after_a_pick(self):
        self.pick(1, (2560, 1080)); self.collect()
        self.c.state.open_menu(1)
        self.c.desktop_action(1, 'ultrawide'); self.collect()  # 21:9 back to 16:9 at its height
        self.assertEqual(self.c.state.menu()['resolution'], [1920, 1080])


class PointerTests(unittest.TestCase):
    def test_the_box_and_the_list_are_hit_on_the_open_panel(self):
        x, y, w, h = fp.PANEL_RES_BOX
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1), ('resolution_box', 1))
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1, resolution_picker=True), ('resolution_box', 1))  # closes it
        for i, size in enumerate(g.RESOLUTIONS):
            ix, iy, iw, ih = fp.resolution_item_rect(i)
            self.assertGreaterEqual(iy, fp.PANEL_HEADER_H)
            self.assertEqual(fp.panel_hit(ix + iw / 2, iy + ih / 2, 1, resolution_picker=True), ('resolution', 1, size))
            self.assertNotEqual(fp.panel_hit(ix + iw / 2, iy + ih / 2, 1)[0], 'resolution')  # closed: the sliders there
        # Columns by shape, left to right; a heading or an empty cell of the open list is the panel (the press closes it).
        lx, ly, lw, lh = fp.resolution_list_rect()
        self.assertEqual((lx, ly, lx + lw), (10.0, fp.PANEL_ROWS_TOP, fp.PANEL_W - 10.0))  # it covers the slider rows
        self.assertEqual(fp.panel_hit(lx + 20, ly + 5, 1, resolution_picker=True), ('panel', 1))
        self.assertEqual(fp.panel_hit(lx + lw - 20, ly + lh - 5, 1, resolution_picker=True), ('panel', 1))  # under 5120x1440
        self.assertNotEqual(fp.panel_hit(fp.PANEL_KNOB_LO, fp.PANEL_ROWS_TOP + 3, 1, resolution_picker=True)[0], 'slider')
        self.assertEqual(fp.panel_hit(fp.PANEL_KNOB_LO, fp.PANEL_ROWS_TOP + 3, 1)[0], 'slider')
        self.assertEqual(fp.resolution_item_rect(len(g.RESOLUTIONS)), (0.0, 0.0, 0.0, 0.0))
        # The Resolution row is above the Scale row, both between the sliders and the desktop row.
        self.assertEqual((fp.PANEL_RES_TOP, fp.PANEL_SCALE_TOP), (fp.PANEL_ROWS_TOP + 4 * fp.PANEL_ROW_H, fp.PANEL_RES_TOP + fp.PANEL_ROW_H))
        self.assertGreaterEqual(fp.PANEL_SCALE_BOX[1], y + h)
        self.assertGreaterEqual(fp.PANEL_ACTIONS_TOP, fp.PANEL_SCALE_BOX[1] + fp.PANEL_SCALE_BOX[3])
        # The open Scale list lies over this box: its steps take the press there.
        self.assertEqual(fp.panel_hit(x + w / 2, y + h / 2, 1, picker=True)[0], 'scale')

    def press(self, hit, picker=False, resolution_picker=False):
        control = mock.Mock()
        control.state = view.ViewState(['XR-1', 'XR-2'])
        control.state.open_menu(1)
        control.state.scale_picker, control.state.resolution_picker = picker, resolution_picker
        for name in ('scale_box', 'scale_pick', 'scale_close', 'resolution_box', 'resolution_pick', 'resolution_close',
                     'desktop_action', 'pill_close'):
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
                mock.patch.object(fp.OverviewPointer, '_roll_frame', return_value=b''):
            pointer.on_op('resize_press')
        return control

    def test_presses_reach_the_control(self):
        c = self.press(('resolution_box', 1))
        c.resolution_box.assert_called_once_with(1); c.pill_close.assert_not_called(); c.resolution_close.assert_not_called()
        c = self.press(('resolution', 1, (2560, 1440)), resolution_picker=True)
        c.resolution_pick.assert_called_once_with(1, (2560, 1440)); c.pill_close.assert_not_called()
        c.resolution_close.assert_not_called()
        c = self.press(('panel', 1), resolution_picker=True)  # elsewhere on the panel: the list closes
        c.resolution_close.assert_called_once_with(); c.pill_close.assert_not_called()
        c = self.press(('scale_box', 1), resolution_picker=True)  # the other box: this list closes, that one opens
        c.resolution_close.assert_called_once_with(); c.scale_box.assert_called_once_with(1)
        c = self.press(('resolution_box', 1), picker=True)
        c.scale_close.assert_called_once_with(); c.resolution_box.assert_called_once_with(1)
        c = self.press(('panel', 1))  # nothing open: nothing to close
        c.resolution_close.assert_not_called(); c.scale_close.assert_not_called()

    def test_hit_passes_the_open_list_through(self):
        with mock.patch.object(fp, 'panel_hit', return_value=('panel', 0)) as panel_hit, \
                mock.patch.object(fp, 'unproject', return_value=(0.0, 0.0)), \
                mock.patch.object(fp, 'desktop_to_glass', side_effect=lambda tile, p, q: (tile[0] + tile[2] / 2, tile[1] + tile[3] + 20)):
            fp.hit({'tiles': [(0.0, 0.0, 800.0, 450.0)]}, [FLAT], (1.0, 1.0), open_=0, resolution_picker=True)
        self.assertEqual(panel_hit.call_args.args[3:], (False, True, False))


class BrokerTests(unittest.TestCase):
    def test_every_size_passes_the_actual_cpp_request_validator(self):
        binary = next((p for p in (ROOT / 'build/release/ownership_test', ROOT / 'build/native-spike/ownership_test') if p.is_file()), None)
        if binary is None: self.skipTest('opt-in ownership native build not present')
        r = Run(self)
        for size in g.RESOLUTIONS:
            r.transport.wires.clear()
            r.manager.start(dk.plan(r.manager.specs, [FLAT] * 3, dk.RESOLUTION, 1, size=size))
            self.assertIsNone(r.manager.take_result()[1])
            for wire in r.transport.wires:
                with self.subTest(size=size, op=json.loads(wire[10:])['op']):
                    checked = subprocess.run([str(binary), '--validate'], input=wire[2:].encode(), capture_output=True, timeout=5)
                    self.assertEqual(checked.returncode, 0, wire)
            self.assertEqual((r.manager.desktops()[1]['width'], r.manager.desktops()[1]['height']), size)


if __name__ == '__main__':
    unittest.main()
