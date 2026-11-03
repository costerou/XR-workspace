"""Unified overview cursor (unified_cursor.py): the real Hyprland cursor follows the dot. Over a desktop it is on that
desktop's monitor at the matching point (its clicks and wheel reach the window, a plain click also zooms in); past an
edge the dot carries on through space and hands the cursor back on the next desktop, in 3D and in every viewing mode;
focus warps are followed; leaving the overview lets go where the cursor is. Also the generated Lua (run by a real Lua
interpreter against a stub `hl`), the sampler thread, bind_check and the run's wiring.

Offline only: fake clocks, fake compositor clients and a stub `hl` (Hyprland itself only as --verify-config, through
bind_check, when installed). No compositor, renderer binary or glasses.
"""
import math
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, input_lock as il, orbit, resize, unified_cursor as uc, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.profiles import LayoutStore

from test_free_pointer import BIND_ARGS, G, GAIN, MONITORS, NAMES, OUTPUTS, T0, T1, X1, X2, X3, FakeClient, PointerBase, frames, last
import test_input_lock as til
from test_input_lock import LONG_ARGS, FakeLockClient

T2 = G['tiles'][2]
HYPRLAND = shutil.which('Hyprland')


class UnifiedBase(PointerBase):
    """PointerBase with the run's pointer, the unified cursor's. Feeds stand in for the sampler (each sample after it has
    applied every request): a fed cursor is where the real cursor is."""
    def setUp(self):
        super().setUp()
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)

    def warped(self):
        """Where the sampler would put the cursor for the current request (it warps on 'desktop' with a point)."""
        _, kind, point = self.p.want
        return point if kind == 'desktop' and point is not None else self.cur

    def into(self, i, q):
        """Free mode off XR-1's top edge, then the dot into desktop i at tile pixel q (on its placed surface)."""
        self.at(X1 + 960, 300); self.at(X1 + 960, 0)
        self.assertEqual(self.p.mode, 'free')
        aim = fp.project(G, G['tiles'][i], self.control.state.placements[i], *q, around=self.p._around())
        return self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])


class DesktopModeTests(UnifiedBase):
    def test_over_a_desktop_the_real_cursor_is_the_pointer_and_nothing_swallows_its_input(self):
        out = self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.p.want[1:], ('desktop', None))  # the click binds, no warp: the cursor already is there
        self.assertAlmostEqual(self.p.pointer[0], T0[0] + T0[2] / 2, places=2)
        self.assertAlmostEqual(self.p.pointer[1], T0[1] + T0[3] / 2, places=2)
        self.assertTrue(last(out, 'overview_pointer')['visible'])  # the dot is drawn where the cursor is
        self.at(X1 + 1200, 700)  # hand motion moves the real cursor; the dot follows its mapping
        self.assertEqual(self.p.mode, 'desktop')
        want = fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + 1200 / 1920 * T0[2], T0[1] + 700 / 1080 * T0[3])
        self.assertAlmostEqual(self.p.pointer[0], want[0], places=2); self.assertAlmostEqual(self.p.pointer[1], want[1], places=2)

    def test_the_desktop_mode_binds_are_the_left_button_only_and_non_consuming(self):
        lua = uc.click_binds_lua(*BIND_ARGS)
        self.assertEqual(lua.count('hl.bind'), 2)
        self.assertEqual(lua.count('non_consuming = true'), 2)  # the window still gets the press and the release
        self.assertIn(f'"{resize.RESIZE_BUTTON}"', lua)
        for chord in il.OTHER_BUTTONS + il.WHEEL: self.assertNotIn(f'"{chord}"', lua)  # right, middle, wheel: the window's
        self.assertIn('resize_press', lua); self.assertIn('{ release = true, non_consuming = true }', lua)
        self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
        with self.assertRaises(ValueError): uc.click_binds_lua('/usr/bin/python3;rm', *BIND_ARGS[1:])
        with self.assertRaises(ValueError): uc.click_binds_lua(*BIND_ARGS[:3], 'bad token', BIND_ARGS[4])
        self.assertLess(len(uc.enter_desktop_lua(*LONG_ARGS, 5440, 540)), 4096)  # one eval
        self.assertLess(len(il.lock_pointer_lua(*LONG_ARGS, 5440, 540)), 4096)

    def test_a_plain_click_reaches_the_window_and_zooms_into_that_desktop(self):
        self.at(X2 + 600, 400)
        self.control.state.selected = 0
        self.assertEqual(self.op('resize_press', ops=('view',)), [])  # the press alone changes no view
        self.assertEqual(self.p.want[1], 'desktop')  # no grab: the press went to the window too (non-consuming)
        out = self.op('resize_release')
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertEqual(self.control.state.selected, 1)
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-2'])
        self.assertEqual(self.p.release_at, (X2 + 600, 400))  # the cursor stays where it was clicked
        self.assertEqual(self.p.focus_clicks, 1)
        self.at(X2 + 600, 401)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(self.p.want[1], 'off')  # zoomed in: the binds go

    def test_a_window_drag_or_selection_does_not_zoom(self):
        self.at(X1 + 600, 400)
        self.op('resize_press')
        self.at(X1 + 900, 420)  # dragged on: past CLICK_PX
        out = self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)
        self.assertIsNone(last(out, 'view'))
        self.assertEqual(self.p.focus_clicks, 0); self.assertEqual(self.p.mode, 'desktop')

    def test_a_held_button_follows_the_real_cursor_onto_the_next_monitor(self):
        self.at(X1 + 1800, 500)
        self.op('resize_press')
        self.at(X1 + 1919, 500)  # the edge pixel: free mode if the button were up
        self.assertEqual(self.p.mode, 'desktop'); self.assertEqual(self.p.want[1], 'desktop')
        self.at(X2 + 40, 500)  # Hyprland carries the drag onto XR-2
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.control.state.selected, 1)
        want = fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + 40 / 1920 * T1[2], T1[1] + 500 / 1080 * T1[3])
        self.assertAlmostEqual(self.p.pointer[0], want[0], places=2)
        self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)  # a drag across, not a click
        self.at(X2 + 1919, 500)
        self.assertEqual(self.p.mode, 'free')  # released: the edge turns it free again

    def test_a_held_button_off_the_xr_monitors_lets_go(self):
        self.at(X1 + 10, 500)
        self.op('resize_press')
        self.at(2000, 500)  # onto the physical monitor
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')
        self.assertIsNone(self.p.click)
        self.assertEqual(self.op('resize_release', ops=('view',)), [])
        self.assertFalse(self.control.state.zoomed)

    def test_a_press_while_idle_only_wakes_the_dot(self):
        self.at(X1 + 600, 400)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.op('resize_press'); self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)


class EdgeTests(UnifiedBase):
    def test_past_the_edge_the_cursor_is_parked_just_inside_where_it_left(self):
        self.at(X1 + 700, 300)
        out = self.at(X1 + 700, 0)
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1:], ('free', (X1 + 700, il.PARK_MARGIN_PX)))  # not the monitor's middle
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.p.feed(self.cur, (0.0, -100.0), OUTPUTS, self.p.want[0]); self.control.poll()
        self.assertEqual(self.p.mode, 'free')
        self.assertAlmostEqual(self.p.pointer[1], fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + 700 * GAIN, T0[1])[1] - 100 * GAIN, delta=0.5)

    def test_the_dot_carries_on_to_the_next_desktop_and_the_cursor_is_warped_there(self):
        self.at(X1 + 1900, 540)
        self.at(X1 + 1919, 540)
        self.assertEqual(self.p.mode, 'free')
        out = self.move_canvas(T1[0] + 30 - self.p.pointer[0], 0)  # through the gap, onto XR-2's surface
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.control.state.selected, 1); self.assertEqual(last(out, 'view')['selected'], 1)
        kind, (x, y) = self.p.want[1:]
        self.assertEqual(kind, 'desktop')  # the grab goes and the real cursor is put on XR-2 under the dot
        q = fp.unproject(G, T1, DEFAULT_PLACEMENT, self.p.pointer)
        self.assertLessEqual(abs(x - (X2 + (q[0] - T1[0]) / T1[2] * 1920)), 1)
        self.assertLessEqual(abs(y - (q[1] - T1[1]) / T1[3] * 1080), 1)
        self.at(x, y)  # the sampler warped it there: it is the pointer again
        self.assertEqual(self.p.mode, 'desktop')
        self.assertAlmostEqual(self.p.pointer[0], fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + (x - X2) / 1920 * T1[2],
                                                             T1[1] + y / 1080 * T1[3])[0], delta=0.5)

    def test_a_desktop_turned_and_curved_round_you_is_entered_where_the_dot_is_on_it(self):
        self.control.state.placements = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': 150, 'y': 80, 'curvature': 60,
                                                                     'distance': 140}, dict(DEFAULT_PLACEMENT)]
        q = (T1[0] + T1[2] * 0.3, T1[1] + T1[3] * 0.6)
        self.into(1, q)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        _, _, (x, y) = self.p.want
        self.assertLessEqual(abs(x - (X2 + 0.3 * 1920)), 3); self.assertLessEqual(abs(y - 0.6 * 1080), 3)

    def test_every_viewing_mode_hands_the_cursor_to_the_matching_point(self):
        for mode in view.MODES:
            with self.subTest(mode=mode):
                self.setUp()
                self.control.state.mode = mode
                self.into(2, (T2[0] + T2[2] * 0.5, T2[1] + T2[3] * 0.25))
                self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 2))
                _, _, (x, y) = self.p.want
                self.assertLessEqual(abs(x - (X3 + 960)), 3); self.assertLessEqual(abs(y - 270), 3)

    def test_free_mode_swallows_buttons_and_wheel_and_keeps_pill_drags(self):
        self.at(X1 + 960, 540); self.at(X1 + 960, 1079)
        self.assertEqual(self.p.want[1], 'free')  # the sampler grabs with the lock's grab (other buttons, wheel)
        x, y, w, h = fp.pill_rect(T0)
        self.move_canvas(x + w / 2 - self.p.pointer[0], y + h / 2 - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('pill', 0))
        self.op('resize_press'); self.move_canvas(0, -T0[3] * 1.3); self.move_canvas(T0[2] * 0.2, 0)
        want = orbit.drag_target(G, self.p.drag['tile'], (0, 0), self.p.drag['start'], self.p.pointer)
        self.op('resize_release')
        self.assertEqual((self.store.saved[0]['x'], self.store.saved[0]['y']), want)
        self.assertEqual(self.p.mode, 'free')

    def test_a_free_dot_click_near_a_desktop_still_zooms_in(self):
        self.at(X1 + 960, 300); self.at(X1 + 960, 0)
        self.move_canvas(0, fp.ENTER_PX / 2)  # just onto XR-1, not far enough to hand back
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p.hover[:2], ('desktop', 0))
        out = frames(self.control.on_message({'op': 'resize_press', 'payload': {}}))
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertEqual(self.p._at(self.p.release_at)[0], 0)


class WarpTests(UnifiedBase):
    def test_a_focus_warp_while_free_is_followed_onto_that_desktop(self):
        self.at(X1 + 960, 300); self.at(X1 + 960, 0)
        self.at(X3 + 960, 540)  # Ctrl+Alt+Right: Hyprland warped the parked cursor to XR-3
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 2))
        self.assertEqual(self.p.want[1:], ('desktop', None))  # left where it landed, the grab gone
        self.assertEqual(self.control.state.selected, 2)
        self.assertAlmostEqual(self.p.pointer[0], T2[0] + T2[2] / 2, places=2)
        self.assertEqual(self.p.follows, 1)

    def test_a_warp_off_the_xr_monitors_lets_go(self):
        self.at(X1 + 960, 300); self.at(X1 + 960, 0)
        self.at(800, 600)  # a window sent back to the physical monitor took the cursor with it
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')

    def test_a_focus_warp_in_desktop_mode_is_followed(self):
        self.at(X1 + 960, 540)
        self.at(X3 + 960, 540)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 2))
        self.assertEqual(self.p.want[1], 'desktop')


class OverviewInOutTests(UnifiedBase):
    def test_zooming_out_puts_the_dot_where_the_cursor_is_and_zooming_in_lets_go_there(self):
        self.op('zoom_in'); self.at(X2 + 300, 300)
        self.assertEqual(self.p.mode, 'off')
        self.op('zoom_out'); self.at(X2 + 300, 301)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', None))
        self.op('zoom_in'); self.at(X2 + 300, 302)
        self.assertEqual(self.p.want[1], 'off')
        self.assertIsNone(self.p.release_at)  # a key zoom-in: the cursor stays where it is

    def test_the_physical_monitor_is_left_alone(self):
        self.at(300, 300)
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')
        self.at(X1 + 5, 300)  # onto XR-1 in Hyprland's layout
        self.assertEqual(self.p.mode, 'desktop'); self.assertEqual(self.p.want[1], 'desktop')


class FakeUnifiedClient(FakeLockClient):
    def enter_desktop(self, *args):
        self._call('enter', args)
        if len(args) == 7: self.cursor = args[-2:]


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

    def test_desktop_free_and_back_with_real_compositor_calls(self):
        client = FakeUnifiedClient(cursor=(X1 + 960, 540))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        self.assertEqual(client.args('enter')[0], BIND_ARGS)  # click binds, no warp
        self.assertEqual(client.count('lock'), 0)
        client.cursor = (X1 + 1919, 540)  # the hand pushes it to the edge
        self.assertTrue(self.wait(lambda: sampler.grabbed))
        self.assertEqual(client.args('lock')[-1][5:], il.park_point(OUTPUTS[0], (X1 + 1919, 540)))
        self.assertFalse(sampler.bound)
        self.p.pointer = (T1[0] + T1[2] / 2 - 1, T1[1] + T1[3] / 2)  # the dot carried on (stand-in for many samples)
        self.p.feed(client.cursor, (1.0, 0.0), OUTPUTS, sampler.applied)  # one more step: deep inside XR-2
        self.assertTrue(self.wait(lambda: client.count('enter') == 2))
        x, y = client.args('enter')[-1][5:]
        self.assertEqual(self.p._at((x, y))[0], 1)  # the real cursor put on XR-2 under the dot
        self.assertLessEqual(abs(x - (X2 + 960)), 3); self.assertLessEqual(abs(y - 540), 3)
        self.assertTrue(self.wait(lambda: sampler.bound and not sampler.grabbed and self.p.mode == 'desktop'))

    def test_a_click_zooms_in_and_the_cursor_stays_where_it_was_clicked(self):
        client = FakeUnifiedClient(cursor=(X2 + 500, 300))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        self.control.on_message({'op': 'resize_press', 'payload': {}})
        out = frames(self.control.on_message({'op': 'resize_release', 'payload': {}}))
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.assertEqual(client.args('release'), [(X2 + 500, 300)])
        self.assertFalse(sampler.bound or sampler.grabbed)
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-2'])
        time.sleep(0.05)
        self.assertEqual(client.count('enter'), 1)  # nothing bound again while zoomed in

    def test_a_config_reload_reinstalls_the_click_binds(self):
        client = FakeUnifiedClient(cursor=(X1 + 960, 540))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.bound))
        self.p.binds_dropped = True
        self.assertTrue(self.wait(lambda: client.count('enter') == 2))

    def test_run_end_removes_whatever_set_is_live(self):
        client = FakeUnifiedClient(cursor=(X1 + 960, 540))
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.assertTrue(self.wait(lambda: sampler.bound))
        sampler.stop()
        self.assertGreaterEqual(client.count('remove'), 1)


class LuaTests(unittest.TestCase):
    """enter_desktop_lua and the click binds, run against the stub `hl` (test_input_lock.LuaRunTests)."""
    STUB, run_lua, lock = til.LuaRunTests.STUB, til.LuaRunTests.run_lua, til.LuaRunTests.lock
    OPTS = r'''
local plain_bind = hl.bind
local nc = {}
hl.bind = function(chord, action, opts)
  local key = chord .. ((opts and opts.release) and "^" or "")
  nc[key] = opts and opts.non_consuming == true or false
  return plain_bind(chord, action, opts)
end
'''

    def test_free_then_into_a_desktop_then_out(self):
        out = self.run_lua('\n'.join([
            self.OPTS, self.lock(3520, 540), 'show("free")',
            uc.enter_desktop_lua(*BIND_ARGS, 5000, 600), 'show("desktop")',
            'print("nc", tostring(nc["mouse:272"]), tostring(nc["mouse:272^"]), state.binds["mouse:272"], state.binds["mouse:272^"], '
            'tostring(state.binds["mouse:273"]), tostring(state.binds["mouse_up"]))',
            fp.release_pointer_lua(), 'show("away")']))
        n = 2 + len(il.OTHER_BUTTONS) + len(il.WHEEL)
        self.assertEqual(out[0], ['free', 'true', '3520', '540', str(n), '0', str(n + 1)])
        # Into XR-2: the grab and the lock's binds gone (scroll delay restored), cursor at the dot, two click binds.
        self.assertEqual(out[1], ['desktop', 'false', '5000', '600', '2', '300', '2'])
        self.assertEqual(out[2], ['nc', 'true', 'true', 'resize_press', 'resize_release', 'nil', 'nil'])
        self.assertEqual(out[3], ['away', 'false', '5000', '600', '0', '300', '0'])

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

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_accepts_the_non_consuming_binds(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            (hypr / 'hyprland.lua').write_text('hl.bind("SUPER + mouse:272", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(hypr / 'hyprland.lua', HYPRLAND, home)
            self.assertEqual(status, 0, lines)
            self.assertIn("non-consuming click binds", lines[-1])


class WiringTests(unittest.TestCase):
    def test_the_run_uses_the_unified_cursor(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': X2 + 960, 'y': 540} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            self.assertIsInstance(control.overview_pointer, uc.UnifiedPointer)
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.enter_desktop.called:
                control.poll(); time.sleep(0.01)
            self.assertEqual(client.enter_desktop.call_args.args, client.install_view_binds.call_args.args)  # no warp
            self.assertEqual(control.state.selected, 1)
        client.lock_pointer.assert_not_called()  # over a desktop nothing is held
        client.grab_pointer.assert_not_called()
        client.remove_resize_binds.assert_called()  # run end

    def test_the_hyprland_client_sends_the_enter_lua(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient.__new__(HyprlandClient)
        sent = []
        client._request = lambda line: sent.append(line) or b'ok'
        client.enter_desktop(*BIND_ARGS, 5000, 600)
        client.enter_desktop(*BIND_ARGS)
        self.assertEqual(sent, ['/eval ' + uc.enter_desktop_lua(*BIND_ARGS, 5000, 600), '/eval ' + uc.enter_desktop_lua(*BIND_ARGS)])


if __name__ == '__main__':
    unittest.main()
