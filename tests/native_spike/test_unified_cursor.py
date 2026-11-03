"""Unified cursor (unified_cursor.py): the dot is the one real cursor and only ever controls the selected desktop
("home"). In the overview, over home the real cursor is the pointer (its clicks and wheel reach the window, and a
plain click is the window's alone: it never maximizes); past home's edge the dot is free and parked on home, and over
another desktop it is only the dot (no selection, no warp there) until a click selects that desktop, still in the
overview, and the cursor comes back there under the dot. Only Ctrl+Alt+Up maximizes (anchor-no-click-maximize,
2026-11-03; until then both clicks zoomed in). Zoomed in, the cursor is kept on home. Keys move home; nothing else
changes monitor. Also the generated Lua (run by a real Lua interpreter against a stub `hl`), the
sampler thread, bind_check and the run's wiring.

Regression (scope change 2026-11-02): before it, the dot carried the cursor onto whichever desktop it moved into
(selecting it), a held button followed the cursor across monitors, focus warps moved the dot to the other monitor, and
cursor follow (Ctrl+Alt+F) switched the view to the monitor the cursor crossed onto. All of that is gone.

Offline only: fake clocks, fake compositor clients and a stub `hl` (Hyprland itself only as --verify-config, through
bind_check, when installed). No compositor, renderer binary or glasses.
"""
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, input_lock as il, orbit, resize, unified_cursor as uc, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from tools.native_spike.profiles import LayoutStore

from test_free_pointer import BIND_ARGS, G, GAIN, MONITORS, NAMES, OUTPUTS, T0, T1, X1, X2, X3, PointerBase, frames, last
import test_input_lock as til
from test_input_lock import LONG_ARGS, FakeLockClient

T2 = G['tiles'][2]
HYPRLAND = shutil.which('Hyprland')
MID1 = (X1 + 960, 540)


class UnifiedBase(PointerBase):
    """PointerBase with the run's pointer, the unified cursor's. Feeds stand in for the sampler (each sample after it has
    applied every request): a fed cursor is where the real cursor is."""
    def setUp(self):
        super().setUp()
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)

    def free(self):
        """Free mode off XR-1's (home's) top edge."""
        self.at(X1 + 960, 300); self.at(X1 + 960, 0)
        self.assertEqual(self.p.mode, 'free')

    def aim(self, i, q):
        """Free dot onto desktop i at tile pixel q (on its placed surface)."""
        aim = fp.project(G, G['tiles'][i], self.control.state.placements[i], *q)
        return self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])


class HomeTests(UnifiedBase):
    def test_over_home_the_real_cursor_is_the_pointer_and_nothing_swallows_its_input(self):
        out = self.at(*MID1)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        self.assertEqual(self.p.want[1:], ('desktop', None))  # the click binds, no warp: the cursor already is there
        self.assertAlmostEqual(self.p.pointer[0], fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)[0], places=2)
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.at(X1 + 1200, 700)
        want = fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + 1200 / 1920 * T0[2], T0[1] + 700 / 1080 * T0[3])
        self.assertAlmostEqual(self.p.pointer[0], want[0], places=2); self.assertAlmostEqual(self.p.pointer[1], want[1], places=2)

    def test_the_desktop_mode_binds_are_the_left_button_only_and_non_consuming(self):
        lua = uc.click_binds_lua(*BIND_ARGS)
        self.assertEqual(lua.count('hl.bind'), 2)
        self.assertEqual(lua.count('non_consuming = true'), 2)
        for chord in il.OTHER_BUTTONS + il.WHEEL: self.assertNotIn(f'"{chord}"', lua)
        self.assertIn('{ release = true, non_consuming = true }', lua)
        self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
        with self.assertRaises(ValueError): uc.click_binds_lua('/usr/bin/python3;rm', *BIND_ARGS[1:])
        with self.assertRaises(ValueError): uc.click_binds_lua(*BIND_ARGS[:3], 'bad token', BIND_ARGS[4])
        self.assertLess(len(uc.enter_desktop_lua(*LONG_ARGS, 5440, 540)), 4096)

    def test_a_plain_click_reaches_the_window_and_never_maximizes_home(self):
        self.control.state.selected = 1
        self.at(X2 + 600, 400)
        self.assertEqual(self.op('resize_press', ops=('view',)), [])
        self.assertEqual(self.p.want[1], 'desktop')  # no grab: the press went to the window too
        self.assertEqual(self.op('resize_release', ops=('view',)), [])  # the window's click, nothing more
        self.control.join_focus(2)
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.focused, [])
        self.assertEqual((self.p.mode, self.p.desktop, self.p.want[1]), ('desktop', 1, 'desktop'))  # still the pointer
        self.assertEqual((self.p.window_clicks, self.p.focus_clicks), (1, 0))
        self.assertTrue(self.p.overview())
        for _ in range(3):  # however many clicks
            self.op('resize_press'); self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)

    def test_ctrl_alt_up_still_maximizes_home(self):
        self.control.state.selected = 1
        self.at(X2 + 600, 400)
        self.op('resize_press'); self.op('resize_release')  # a click on a window there first
        out = self.op('zoom_in')
        self.control.join_focus(2)
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed'], last(out, 'view')['mode']), (1, True, 'follow'))
        self.assertEqual(self.focused, ['XR-2'])
        self.at(X2 + 600, 400)
        self.assertEqual(self.p.mode, 'off')  # out of the overview: the free pointer lets go

    def test_a_window_drag_or_selection_does_not_zoom(self):
        self.at(X1 + 600, 400)
        self.op('resize_press'); self.at(X1 + 900, 420)
        self.assertEqual(self.op('resize_release', ops=('view',)), [])
        self.assertFalse(self.control.state.zoomed)

    def test_a_held_button_never_takes_the_cursor_onto_another_monitor(self):
        self.at(X1 + 1800, 500)
        self.op('resize_press')
        self.at(X1 + 1919, 500)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        self.at(X2 + 40, 500)  # Hyprland let the drag onto XR-2
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        self.assertEqual(self.control.state.selected, 0)  # never switched
        self.assertEqual(self.p.want[1:], ('desktop', (X1 + 1919 - fp.INSET_PX, 500)))  # pulled back onto XR-1
        self.at(2000, 500)  # onto the physical monitor too
        self.assertEqual(self.p.mode, 'desktop'); self.assertEqual(self.p._at(self.p.want[2])[0], 0)

    def test_a_press_while_idle_only_wakes_the_dot(self):
        self.at(X1 + 600, 400)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.op('resize_press'); self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)


class EdgeTests(UnifiedBase):
    def test_past_homes_edge_the_cursor_is_parked_on_home(self):
        self.at(X1 + 700, 300)
        self.at(X1 + 700, 0)
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1:], ('free', (X1 + 700, il.PARK_MARGIN_PX)))
        self.at(X1 + 1900, 540); self.setUp()
        self.at(X1 + 1900, 540)
        self.at(X2 + 6, 540)  # Hyprland let it onto XR-2: free, and parked back on XR-1
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p._at(self.p.want[2])[0], 0)
        self.assertEqual(self.control.state.selected, 0)

    def test_the_dot_onto_another_desktop_gives_it_the_real_cursor_under_the_dot(self):
        # anchor-no-click-maximize: every desktop in the overview is a normal screen. Until then the dot over another
        # desktop was only the dot (no selection, the cursor parked on home) until a click.
        self.free()
        out = self.aim(1, (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2))  # deep into XR-2
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, 540)))  # the cursor goes under the dot, on XR-2
        self.assertEqual(self.control.state.selected, 1)  # XR-2 is home: highlighted, Ctrl+Alt+Up maximizes it
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, False))
        self.assertEqual(self.p.entries, 1)
        out = self.at(X2 + 960, 540)  # the sampler's warp landed: the real cursor is the pointer there
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertIsNone(last(out, 'view'))
        self.at(X2 + 1200, 700)  # hand motion on XR-2 moves the dot over XR-2's surface
        want = fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + 1200 / 1920 * T1[2], T1[1] + 700 / 1080 * T1[3])
        self.assertAlmostEqual(self.p.pointer[0], want[0], places=2); self.assertAlmostEqual(self.p.pointer[1], want[1], places=2)

    def test_another_desktops_edge_resizes_it_and_moving_in_enters_it(self):
        # The border round a desktop is its resize edges (free_pointer.border_handle): a drag there resizes it, a click
        # does nothing, and the dot moving on in hands that desktop the cursor (there is no click-only band any more).
        self.free()
        self.aim(1, (T1[0] - 4, T1[1] + T1[3] * 0.5))  # just left of XR-2's left edge
        self.assertEqual((self.p.mode, self.p.hover), ('free', ('edge', 1, 'l')))
        self.op('resize_press'); self.op('resize_release')
        self.assertEqual((self.control.state.selected, self.control.state.placements[1]), (0, DEFAULT_PLACEMENT))
        self.op('resize_press'); self.move_canvas(-T1[2] / 4, 0); self.op('resize_release')  # the left edge outward
        self.assertLess(self.control.state.placements[1]['distance'], 100)
        self.assertEqual((self.control.state.selected, self.control.state.zoomed), (0, False))
        self.aim(1, (T1[0] + 10, T1[1] + T1[3] * 0.5))  # on into its content
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))

    def test_inside_another_desktops_corner_is_content_and_outside_it_resizes(self):
        self.free()
        self.aim(1, (T1[0] + 6, T1[1] + 6))  # inside XR-2's top-left corner: its content
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.setUp()
        self.free()
        self.aim(1, (T1[0] - 4, T1[1] - 4))  # just outside that corner: the resize handle
        self.assertEqual((self.p.mode, self.p.hover), ('free', ('corner', 1, 'tl')))
        self.op('resize_press'); self.op('resize_release')  # a click on a handle: nothing
        self.assertEqual((self.control.state.selected, self.control.state.placements[1]), (0, DEFAULT_PLACEMENT))
        self.assertFalse(self.control.state.zoomed)

    def test_back_into_home_hands_the_cursor_back_under_the_dot(self):
        self.control.state.placements = [{**DEFAULT_PLACEMENT, 'x': -150, 'y': 80, 'curvature': 60, 'distance': 140},
                                         dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT)]
        self.at(X1 + 960, 300)
        self.at(X1 + 960, 0)
        self.aim(0, (T0[0] + T0[2] * 0.3, T0[1] + T0[3] * 0.6))
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        _, _, (x, y) = self.p.want
        self.assertLessEqual(abs(x - (X1 + 0.3 * 1920)), 3); self.assertLessEqual(abs(y - 0.6 * 1080), 3)

    def test_every_viewing_mode_hands_home_back_at_the_matching_point(self):
        # The overview is always anchor now (maximize-follow); every mode is still checked, the mode forced.
        for mode in view.MODES:
            with self.subTest(mode=mode), \
                    mock.patch.object(view.ViewState, 'mode', new_callable=mock.PropertyMock, return_value=mode):
                self.setUp()
                self.free()
                self.aim(0, (T0[0] + T0[2] * 0.5, T0[1] + T0[3] * 0.25))
                self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
                _, _, (x, y) = self.p.want
                self.assertLessEqual(abs(x - (X1 + 960)), 3); self.assertLessEqual(abs(y - 270), 3)

    def test_free_mode_swallows_buttons_and_wheel_and_keeps_pill_drags(self):
        self.at(*MID1); self.at(X1 + 960, 1079)
        self.assertEqual(self.p.want[1], 'free')
        x, y, w, h = fp.pill_rect(T0)
        self.move_canvas(x + w / 2 - self.p.pointer[0], y + h / 2 - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('pill', 0))
        self.op('resize_press'); self.move_canvas(0, -T0[3] * 1.3); self.move_canvas(T0[2] * 0.2, 0)
        want = orbit.drag_target(G, self.p.drag['tile'], (0, 0), self.p.drag['start'], self.p.pointer)
        self.op('resize_release')
        self.assertEqual((self.store.saved[0]['x'], self.store.saved[0]['y']), want)


class SwitchTests(UnifiedBase):
    def test_a_key_switch_moves_home_and_the_cursor_with_it(self):
        self.at(*MID1)
        out = self.op('next')  # Ctrl+Alt+Right in the overview
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.at(*MID1)  # the cursor has not moved yet
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, 540)))  # warped to the middle of the new home
        self.assertEqual(self.p.rehomes, 1)
        self.free_from_new_home()

    def free_from_new_home(self):
        self.at(X2 + 960, 540); self.at(X2 + 960, 0)
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p._at(self.p.want[2])[0], 1)

    def test_a_key_switch_while_free_brings_the_dot_home(self):
        self.free()
        self.op('prev')  # already the first: nothing
        self.assertEqual(self.p.mode, 'free')
        self.op('next')
        self.at(X1 + 960, il.PARK_MARGIN_PX)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, 540)))

    def test_a_warp_onto_another_monitor_while_free_is_pulled_back(self):
        self.free()
        park = self.p.want[2]
        self.at(X3 + 960, 540)  # something warped the parked cursor to XR-3
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1:], ('free', park))  # re-parked on XR-1
        self.assertEqual(self.control.state.selected, 0)

    def test_a_warp_onto_another_monitor_over_home_is_pulled_back(self):
        self.at(*MID1)
        self.at(X3 + 960, 540)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        self.assertEqual(self.p.want[1:], ('desktop', MID1))
        self.assertEqual(self.control.state.selected, 0)

    def test_a_warp_to_the_physical_monitor_lets_go(self):
        self.free()
        self.at(800, 600)  # a window sent back took the cursor with it
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')

    def test_a_cursor_coming_from_the_physical_monitor_lands_on_home(self):
        self.at(300, 300)
        self.assertEqual(self.p.mode, 'away')
        self.at(X1 + 5, 300)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0)); self.assertEqual(self.p.want[1:], ('desktop', None))
        self.setUp()
        self.at(300, 300); self.at(X2 + 5, 300)  # onto XR-2, not home: to the middle of XR-1
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0)); self.assertEqual(self.p.want[1:], ('desktop', MID1))
        self.assertEqual(self.control.state.selected, 0)

    def test_zooming_out_and_in(self):
        self.control.state.selected = 1
        self.op('zoom_in'); self.at(X2 + 300, 300)
        self.assertEqual(self.p.mode, 'off')
        self.op('zoom_out'); self.at(X2 + 300, 301)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1)); self.assertEqual(self.p.want[1:], ('desktop', None))
        self.op('zoom_in'); self.at(X2 + 300, 302)
        self.assertEqual(self.p.want[1], 'off')


class ConfineTests(unittest.TestCase):
    def test_confine_target(self):
        t = uc.confine_target
        self.assertIsNone(t(OUTPUTS, 0, MID1))  # on home
        self.assertEqual(t(OUTPUTS, 0, (X2 + 3, 500)), (X1 + 1919 - fp.INSET_PX, 500))  # pushed onto XR-2: back
        self.assertEqual(t(OUTPUTS, 0, (X3 + 900, 20)), (X1 + 1919 - fp.INSET_PX, 20))
        self.assertEqual(t(OUTPUTS, 1, (X1 + 1919, 1079)), (X2 + fp.INSET_PX, 1079 - fp.INSET_PX))
        self.assertEqual(t(OUTPUTS, 0, (X1 - 3, 500), previous=(X1 + 2, 500)), (X1 + fp.INSET_PX, 500))  # off to the physical one
        self.assertIsNone(t(OUTPUTS, 0, (100, 500), previous=(X1 + 2, 500)))  # a jump: a warp, left there
        self.assertIsNone(t(OUTPUTS, 0, (X1 - 3, 500), previous=(X1 - 30, 500)))  # already on the physical monitor
        self.assertIsNone(t(OUTPUTS, 0, (X1 - 3, 500)))
        self.assertEqual(t(OUTPUTS, 0, (X2 + 3, 500), previous=(100, 100)), (X1 + 1919 - fp.INSET_PX, 500))  # never onto XR-2
        self.assertIsNone(t([], 0, (X2 + 3, 500)))

    def test_confine_lua(self):
        lua = uc.confine_lua(3000, 400, 'XR-1')
        self.assertEqual(lua, view.warp_cursor_lua(3000, 400) + ' pcall(function() hl.dispatch(hl.dsp.focus({ monitor = "XR-1" })) end)')
        for bad in ('DP-1', 'XR-1") os.exit() --', None):
            with self.assertRaises(ValueError): uc.confine_lua(3000, 400, bad)
        with self.assertRaises(ValueError): uc.confine_lua(3000.5, 400, 'XR-1')


class FakeUnifiedClient(FakeLockClient):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.focused = []  # enter_desktop's monitor focus, in order (None: none asked)

    def enter_desktop(self, *args, monitor=None):
        self._call('enter', args)
        with self.lock: self.focused.append(monitor)
        if len(args) == 7: self.cursor = args[-2:]

    def confine_cursor(self, x, y, name):
        self._call('confine', (x, y, name)); self.cursor = (x, y)


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def start(self, client):
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def test_home_free_and_back_with_real_compositor_calls(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        self.assertEqual(client.args('enter')[0], BIND_ARGS)  # click binds, no warp
        client.cursor = (X1 + 1919, 540)
        self.assertTrue(self.wait(lambda: sampler.grabbed))
        self.assertEqual(client.args('lock')[-1][5:], il.park_point(OUTPUTS[0], (X1 + 1919, 540)))
        self.p.pointer = (T1[0] + T1[2] / 2 - 1, T1[1] + T1[3] / 2)  # the dot onto XR-2: it takes the cursor there
        self.p.feed(client.cursor, (1.0, 0.0), OUTPUTS, sampler.applied)
        self.assertTrue(self.wait(lambda: client.count('enter') == 2 and sampler.bound and not sampler.grabbed))
        self.assertEqual(self.p._at(client.args('enter')[-1][5:])[0], 1)
        self.assertEqual(client.focused[-1], 'XR-2')  # that monitor focused first: the keyboard goes to its window
        self.assertEqual(self.control.state.selected, 1)
        client.cursor = (X2, 540)  # its left edge: free again, parked on XR-2 now
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))
        self.assertEqual(self.p._at(client.args('lock')[-1][5:])[0], 1)
        self.p.pointer = (T0[0] + T0[2] / 2 - 1, T0[1] + T0[3] / 2)  # back over XR-1
        self.p.feed(client.cursor, (1.0, 0.0), OUTPUTS, sampler.applied)
        self.assertTrue(self.wait(lambda: client.count('enter') == 3))
        self.assertEqual(self.p._at(client.args('enter')[-1][5:])[0], 0)
        self.assertEqual(client.focused, [None, 'XR-2', 'XR-1'])  # the first had no warp: the cursor was already there
        self.assertEqual(self.control.state.selected, 0)

    def test_zoomed_in_the_cursor_is_kept_on_home(self):
        self.control.state.zoomed = True
        client = FakeUnifiedClient(cursor=MID1)
        sampler = self.start(client)
        time.sleep(0.05)
        self.assertEqual(client.count('confine'), 0)
        client.cursor = (X2 + 10, 300)  # the hand pushed it onto XR-2
        self.assertTrue(self.wait(lambda: client.count('confine') == 1))
        self.assertEqual(client.args('confine'), [(X1 + 1919 - fp.INSET_PX, 300, 'XR-1')])
        self.assertEqual(self.control.state.selected, 0)
        client.cursor = (X1 + 1, 300); time.sleep(0.02)
        client.cursor = (X1 - 4, 300)  # off home onto the physical monitor, by hand
        self.assertTrue(self.wait(lambda: client.count('confine') == 2))
        self.assertEqual(client.args('confine')[-1], (X1 + fp.INSET_PX, 300, 'XR-1'))
        client.cursor = (200, 300)  # a warp (window sent back): left there
        time.sleep(0.05)
        self.assertEqual(client.count('confine'), 2)
        self.control.state.selected = 2  # Ctrl+Alt+Right twice; Hyprland has not moved the cursor yet
        client.cursor = (X2 + 500, 300)
        self.assertTrue(self.wait(lambda: client.count('confine') == 3))
        self.assertEqual(client.args('confine')[-1], (X3 + fp.INSET_PX, 300, 'XR-3'))
        self.assertEqual(sampler.confines, 3)

    def test_a_click_on_home_stays_in_the_overview_and_ctrl_alt_up_still_maximizes(self):
        self.control.state.selected = 1
        client = FakeUnifiedClient(cursor=(X2 + 500, 300))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        self.control.on_message({'op': 'resize_press', 'payload': {}})
        out = frames(self.control.on_message({'op': 'resize_release', 'payload': {}}))
        self.assertIsNone(last(out, 'view'))
        time.sleep(0.05); self.control.poll()
        self.assertFalse(self.control.state.zoomed)
        self.assertTrue(sampler.bound)  # the click binds stay: the overview goes on
        self.assertEqual((client.count('release'), client.count('lock'), client.count('enter')), (0, 0, 1))
        self.assertEqual(client.cursor, (X2 + 500, 300))  # nothing moved the cursor
        self.assertEqual(self.focused, [])
        out = frames(self.control.on_message({'op': 'zoom_in', 'payload': {}}))  # Ctrl+Alt+Up
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.assertEqual(client.args('release'), [()])  # the cursor shown where it is
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-2'])
        time.sleep(0.05)
        self.assertEqual(client.count('confine'), 0)  # zoomed into XR-2 with the cursor on XR-2

    def test_a_dot_click_on_another_desktop_brings_the_cursor_onto_it_under_the_dot(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        client.cursor = (X1 + 1919, 540)  # past home's right edge: free, parked on XR-1
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.p.pointer = q  # the dot onto XR-2 (flat, at its slot: its tile pixel is the canvas point)
        self.control.on_message({'op': 'resize_press', 'payload': {}})
        out = frames(self.control.on_message({'op': 'resize_release', 'payload': {}}))
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, False))
        self.assertTrue(self.wait(lambda: client.count('enter') == 2 and sampler.bound and not sampler.grabbed))
        at = client.args('enter')[-1][5:]  # the grab removed, the cursor warped under the dot, the click binds back
        self.assertEqual(self.p._at(at)[0], 1)
        self.assertLessEqual(abs(at[0] - (X2 + 480)), 2); self.assertLessEqual(abs(at[1] - 810), 2)
        time.sleep(0.05); self.control.poll()
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))  # XR-2 is home now
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual((client.count('release'), client.count('confine')), (0, 0))  # still the overview
        self.assertEqual(client.focused[-1], 'XR-2')  # focused with the warp, in the same eval (enter_desktop_lua)
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])  # no separate focus call: it would warp the cursor away from the dot

    def test_a_config_reload_reinstalls_the_click_binds(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound))
        self.p.binds_dropped = True
        self.assertTrue(self.wait(lambda: client.count('enter') == 2))

    def test_run_end_removes_whatever_set_is_live(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.assertTrue(self.wait(lambda: sampler.bound))
        sampler.stop()
        self.assertGreaterEqual(client.count('remove'), 1)


class LuaTests(unittest.TestCase):
    """enter_desktop_lua, the click binds and confine_lua, run against the stub `hl` (test_input_lock.LuaRunTests)."""
    STUB, run_lua, lock = til.LuaRunTests.STUB, til.LuaRunTests.run_lua, til.LuaRunTests.lock
    OPTS = r'''
local plain_bind = hl.bind
local nc = {}
hl.bind = function(chord, action, opts)
  local key = chord .. ((opts and opts.release) and "^" or "")
  nc[key] = opts and opts.non_consuming == true or false
  return plain_bind(chord, action, opts)
end
local focused = nil
hl.dsp.focus = function(t) return { focus = t.monitor } end
local plain_dispatch = hl.dispatch
hl.dispatch = function(p) if p.focus then focused = p.focus else plain_dispatch(p) end end
'''

    def test_free_then_home_then_out(self):
        out = self.run_lua('\n'.join([
            self.OPTS, self.lock(3520, 540), 'show("free")',
            uc.enter_desktop_lua(*BIND_ARGS, 3000, 600), 'show("desktop")',
            'print("nc", tostring(nc["mouse:272"]), tostring(nc["mouse:272^"]), state.binds["mouse:272"], state.binds["mouse:272^"], '
            'tostring(state.binds["mouse:273"]), tostring(state.binds["mouse_up"]))',
            fp.release_pointer_lua(), 'show("away")']))
        n = 2 + len(resize.SUPER_KEYS) + len(il.OTHER_BUTTONS) + len(il.WHEEL)
        self.assertEqual(out[0], ['free', 'true', '3520', '540', str(n), '0', str(n + 1)])
        self.assertEqual(out[1], ['desktop', 'false', '3000', '600', '2', '300', '2'])
        self.assertEqual(out[2], ['nc', 'true', 'true', 'resize_press', 'resize_release', 'nil', 'nil'])
        self.assertEqual(out[3], ['away', 'false', '3000', '600', '0', '300', '0'])

    def test_confine_puts_the_cursor_back_and_refocuses_home(self):
        out = self.run_lua('\n'.join([self.OPTS, uc.confine_lua(4400, 300, 'XR-1'), 'show("confined")',
                                      'print("focus", tostring(focused))']))
        self.assertEqual(out, [['confined', 'false', '4400', '300', '0', '300', '0'], ['focus', 'XR-1']])

    def test_entering_twice_never_doubles_the_binds_and_a_dead_run_is_cleaned(self):
        from tools.native_spike import xr_control
        out = self.run_lua('\n'.join([
            uc.enter_desktop_lua(*BIND_ARGS), uc.enter_desktop_lua(*BIND_ARGS, 4000, 500), 'show("twice")',
            xr_control.REMOVE_RESIZE_BINDS, 'show("dead")']))
        self.assertEqual(out, [['twice', 'false', '4000', '500', '2', '300', '2'], ['dead', 'false', '4000', '500', '0', '300', '0']])

    def test_a_refused_click_bind_is_left_out_not_made_consuming(self):
        out = self.run_lua('\n'.join(['refuse["mouse:272"] = true', uc.enter_desktop_lua(*BIND_ARGS), 'show("refused")']))
        self.assertEqual(out, [['refused', 'false', '100', '200', '0', '300', '0']])


class BindCheckTests(unittest.TestCase):
    def test_the_harness_loads_the_click_binds(self):
        lua = bind_check.harness_lua('/x/hyprland.lua', '/x/out.tsv')
        self.assertIn(uc.click_binds_lua(*bind_check.DUMMY_ARGS), lua)
        self.assertGreater(lua.rindex(resize.remove_resize_binds_lua()), lua.index(uc.click_binds_lua(*bind_check.DUMMY_ARGS)))

    def test_cursor_follow_is_no_run_bind(self):
        self.assertNotIn('cursor_follow', [op for _, _, op in bind_check.run_chords()])
        self.assertNotIn('CTRL + ALT + F', [c for c, _ in view.BINDS])

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_accepts_the_non_consuming_binds(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            (hypr / 'hyprland.lua').write_text('hl.bind("SUPER + mouse:272", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(hypr / 'hyprland.lua', HYPRLAND, home)
            self.assertEqual(status, 0, lines)
            self.assertIn("non-consuming click binds", lines[-1])


class CursorFollowRemovedTests(unittest.TestCase):
    def test_monitor_focus_never_switches_the_view(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(c.stop_menu_binds)
        self.assertIsNone(c.on_events(b'focusedmon>>XR-2,2\nfocusedmonv2>>XR-3,3\n'))
        self.assertEqual(c.state.selected, 0)
        self.assertFalse(hasattr(c.state, 'cursor_follow'))
        self.assertNotIn('cursor_follow', view.USER_OPS)
        self.assertIs(c.state.view()['cursor_follow'], False)  # the renderer's decoder still needs the key
        self.assertNotIn('cursor_follow', c.state.settings())

    def test_an_old_saved_cursor_follow_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'layout.json'
            path.write_text('{"spacing": 60, "mode": "follow", "cursor_follow": true, "prediction": false}')
            self.assertEqual(view.load_settings(path), {'spacing': 60, 'mode': 'follow', 'prediction': False,
                                                        'video_quality': {'filter': 'catmull-rom', 'deband': False},
                                                        'environment': {'background': 'none', 'last': 'grid', 'brightness': 100}})


class WiringTests(unittest.TestCase):
    def test_the_run_uses_the_unified_cursor(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': X1 + 960, 'y': 540} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            self.assertIsInstance(control.overview_pointer, uc.UnifiedPointer)
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.enter_desktop.called:
                control.poll(); time.sleep(0.01)
            self.assertEqual(client.enter_desktop.call_args.args, client.install_view_binds.call_args.args)  # no warp
        client.lock_pointer.assert_not_called()
        client.grab_pointer.assert_not_called()
        client.remove_resize_binds.assert_called()

    def test_the_hyprland_client_sends_the_generated_lua(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient.__new__(HyprlandClient)
        sent = []
        client._request = lambda line: sent.append(line) or b'ok'
        client.enter_desktop(*BIND_ARGS, 5000, 600)
        client.confine_cursor(3000, 400, 'XR-1')
        self.assertEqual(sent, ['/eval ' + uc.enter_desktop_lua(*BIND_ARGS, 5000, 600), '/eval ' + uc.confine_lua(3000, 400, 'XR-1')])


if __name__ == '__main__':
    unittest.main()
