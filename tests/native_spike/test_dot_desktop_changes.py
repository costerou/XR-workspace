"""The dot cursor through live desktop changes (a pill's Add / Remove / Ultrawide, desktops.py), with the run's unified
cursor (unified_cursor.UnifiedPointer) and the real ViewControl and DesktopManager (test_desktops' fake broker).

Regression (dot-cursor-new-desktop, 2026-11-03, installed master cb1ce12): after a desktop change from the pill the
circular dot was gone for the rest of the run, also after zooming in and back out. OverviewPointer.desktops_changed
dropped the renderer's overview layout (`overview_geometry`) and waited for a new one, but the renderer reports it only
when its line changes (spike/src/wayland.cpp take_overview_geometry). A Remove notifies twice (the renderer lets go of
the output first, then the worker finishes with the same desktops), so the second drop was never answered: no layout,
the pointer stayed inactive, no dot and no pills, whatever the zoom. The tilts (roll-level icons) were dropped the same
way. Also, after a change the first sample back counted only once the mouse moved, so a still mouse left the dot off.
Now the last layout is kept (used again once it has one slot per desktop), tilts are kept by name, the first sample
back in the overview counts, and after a change the cursor is brought onto the selected desktop wherever the change
left it. Nothing here selects another desktop: the dot still never switches monitors on its own.

The renderer stand-in reports the layout exactly like the real one: once per change of its line, never again.
Offline only: fake clock, fake broker, no compositor, renderer or glasses.
"""
import re
import unittest
from pathlib import Path
from unittest import mock

from tools.native_spike import free_pointer as fp, resize, unified_cursor as uc, view

from test_desktops import Run, SupervisorStandIn
from test_free_pointer import G, Clock, frames, last

PHYSICAL = {'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0}
W, H = 1920, 1080


def layout(wide):
    """The renderer's overview layout for desktops in a row (the fixture's slots, one more each to the right), an
    ultrawide one (`wide`, one flag per desktop) a third wider."""
    tiles = list(G['tiles'])
    while len(tiles) < len(wide):
        x, y, w, h = tiles[-1]
        tiles.append((x + 300.0, y, w, h))
    return {**G, 'tiles': [(x, y, w * 4 / 3 if wd else w, h) for (x, y, w, h), wd in zip(tiles, wide)]}


class Renderer:
    """Reports `overview_geometry` for the desktops it was last told about, only when that line changes (the sizes are
    what the broker made them: state.wide)."""
    def __init__(self, control):
        self.control, self.names, self.sent, self.reports = control, list(control.state.names), None, 0

    def take(self, raw):
        for f in frames(raw):
            if f['op'] == 'desktops': self.names = f['payload']['outputs']

    def report(self):
        wide = dict(zip(self.control.state.names, self.control.state.wide))
        g = layout([wide.get(n, False) for n in self.names])
        if g == self.sent: return
        self.sent = g
        self.reports += 1
        self.control.set_overview_geometry(g)


class DesktopChangeBase(unittest.TestCase):
    def setUp(self):
        self.run = Run(self)
        self.clock = Clock()
        state = view.ViewState(['XR-1', 'XR-2', 'XR-3'])
        self.focused = []
        self.c = view.ViewControl(state, 'run', 'session', self.focused.append, None, None, clock=self.clock)
        self.addCleanup(self.c.join_focus, 2)
        self.c.desktops = self.run.manager
        self.c.attach_supervisor(SupervisorStandIn())
        self.run.manager.store = mock.Mock()
        self.p = self.c.overview_pointer = uc.UnifiedPointer(self.c, clock=self.clock)
        self.addCleanup(self.p.close)
        self.renderer = Renderer(self.c)
        self.renderer.report()
        self.cursor = self.mid(0)
        self.delay = 0  # ticks the renderer takes to report a new layout

    # --- the run's moving parts ---------------------------------------------------------------------------------------
    def monitors(self):
        """Hyprland's monitors: the run's outputs in a row right of the physical monitor (the broker slides them)."""
        return [PHYSICAL] + [{'name': n, 'x': 2560 + W * i, 'y': 0, 'width': W, 'height': H, 'scale': 1.0}
                             for i, n in enumerate(self.c.state.names)]

    def mid(self, i):
        return (2560 + W * i + W // 2, H // 2)

    def tick(self, cursor=None, n=1):
        """n supervisor ticks with the sampler's samples; the cursor goes wherever the sampler last warped it."""
        out = []
        for _ in range(n):
            self.clock.now += 0.01
            if cursor is not None: self.cursor, cursor = cursor, None
            elif self.p.want[2] is not None and self.p.want[1] == 'desktop': self.cursor = self.p.want[2]
            outputs = resize.outputs_from_monitors(self.monitors(), self.c.state.names)
            self.p.feed(self.cursor, (0.0, 0.0), outputs, self.p.want[0])
            raw = self.c.poll() or b''
            self.renderer.take(raw)
            out += frames(raw)
            if self.delay: self.delay -= 1
            else: self.renderer.report()
        return out

    def op(self, name):
        raw = self.c.on_message({'op': name, 'payload': {}}) or b''
        self.renderer.take(raw)
        self.c.join_focus(2)
        return frames(raw)

    def press(self, desktop, action):
        """A panel button. The renderer answers its frames (a Remove's: it let go of the output) long before the broker
        is done (the next poll collects the worker's result here)."""
        raw = self.c.desktop_action(desktop, action)
        self.renderer.take(raw)
        if not self.delay: self.renderer.report()
        return frames(raw)

    def dot(self):
        """The dot as the renderer last got it: shown, at a point on the selected desktop."""
        return self.p.sent['visible']

    def assert_dot_on_home(self):
        self.assertTrue(self.dot(), (self.p.mode, self.p.geometry is not None, len(self.p.outputs)))
        self.assertIn(self.p.mode, fp.DOT_MODES)
        home = self.c.state.selected
        self.assertEqual(self.p.desktop, home)
        at = self.p._at(self.cursor)
        self.assertIsNotNone(at); self.assertEqual(at[0], home)  # the real cursor is on the selected desktop

    def zoom_round_trip(self):
        self.op('zoom_in'); self.tick(n=3)
        self.assertFalse(self.dot())
        self.op('zoom_out'); self.tick(n=3)
        self.assert_dot_on_home()

    def add(self, on=0):
        self.c.state.open_menu(on)
        self.press(on, 'add')
        return self.tick(n=4)

    def remove(self, desktop):
        self.c.state.open_menu(desktop)
        self.press(desktop, 'remove')
        self.clock.now += 1
        self.press(desktop, 'remove')  # the second press removes: the renderer lets go of it first
        return self.tick(n=4)


class AddTests(DesktopChangeBase):
    def test_the_dot_stays_after_adding_a_desktop(self):
        self.tick(n=2)
        self.assert_dot_on_home()
        self.add()
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        self.assert_dot_on_home()
        self.zoom_round_trip()

    def test_the_dot_reaches_the_new_desktop(self):
        self.tick(n=2)
        self.add()
        for _ in range(3): self.op('next')  # Ctrl+Alt+Right onto the new XR-4
        self.tick(n=3)
        self.assertEqual(self.c.state.selected, 3)
        self.assert_dot_on_home()
        self.assertEqual(self.p.pointer, self.p._from_logical(3, W / 2, H / 2))  # drawn on XR-4's own slot
        self.zoom_round_trip()

    def test_adding_never_moves_the_cursor_to_the_new_desktop(self):
        self.tick(self.mid(1), n=2)
        self.op('next'); self.tick(n=2)  # home is XR-2
        self.focused.clear()
        self.add(on=1)
        self.assertEqual((self.c.state.menu_open, self.c.state.menu_desktop), (True, 3))  # the panel went to XR-4
        self.assertEqual(self.c.state.selected, 1)  # the selection and the cursor did not
        self.assert_dot_on_home()
        self.assertEqual(self.focused, [])

    def test_a_slow_renderer_report_brings_the_dot_back_without_mouse_motion(self):
        self.tick(n=2)
        self.delay = 6
        self.add()
        self.assertFalse(self.dot())  # the old layout has no slot for XR-4: no hit testing with it
        self.tick(n=4)
        self.assert_dot_on_home()


class RemoveTests(DesktopChangeBase):
    def test_the_dot_stays_after_removing_a_desktop(self):
        self.tick(n=2)
        self.remove(2)
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-2'])
        self.assert_dot_on_home()
        self.zoom_round_trip()

    def test_the_layout_survives_the_removals_second_notice(self):
        # The renderer answers the first notice (it let go of XR-3) and has nothing new to say to the second.
        self.tick(n=2)
        before = self.renderer.reports
        self.remove(2)
        self.assertEqual(self.renderer.reports, before + 1)  # one report, before the worker was done
        self.assertIsNotNone(self.p.geometry)
        self.assertTrue(self.p.active())

    def test_removing_the_selected_desktop_brings_the_cursor_onto_the_new_one(self):
        self.op('next'); self.tick(n=2)
        self.assertEqual(self.c.state.selected, 1)
        self.c.state.open_menu(1)
        self.press(1, 'remove'); self.clock.now += 1; self.press(1, 'remove')
        self.tick((1200, 700), n=4)  # Hyprland moved the cursor off the removed output, onto the physical monitor
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-3'])
        self.assert_dot_on_home()
        self.zoom_round_trip()

    def test_remove_then_add_and_back(self):
        self.tick(n=2)
        self.remove(1)
        self.assert_dot_on_home()
        self.add()
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-2', 'XR-3'])
        self.assert_dot_on_home()
        self.zoom_round_trip()

    def test_tilts_are_kept_by_name(self):
        self.c.set_roll_geometry([300, None, -40])
        self.tick(n=2)
        self.remove(1)
        self.assertEqual(self.p.tilts, [300, -40])


class UltrawideTests(DesktopChangeBase):
    def test_the_dot_stays_after_ultrawide(self):
        self.tick(n=2)
        self.c.state.open_menu(0)
        self.press(0, 'ultrawide')
        self.tick(n=4)
        self.assertTrue(self.c.state.wide[0])
        self.assertEqual(self.p.geometry, layout([True, False, False]))
        self.assert_dot_on_home()
        self.zoom_round_trip()

    def test_a_refused_reshape_keeps_the_dot(self):
        # The broker refuses: the desktops are as they were, so the renderer's layout is too and it reports nothing.
        self.tick(n=2)
        before = self.renderer.reports
        self.run.transport.refuse_reshape = True
        self.c.state.open_menu(0)
        self.press(0, 'ultrawide')
        self.tick(n=4)
        self.assertEqual(self.renderer.reports, before)
        self.assert_dot_on_home()
        self.zoom_round_trip()


class StartWithFourTests(unittest.TestCase):
    """Regression (dot-missing-overview, 2026-11-03, installed master 37393b1): a run that starts with four desktops (a
    profile saved with four, or xr-start-adopt-windows adding one before the renderer starts) never showed the dot, the
    pills or the roll icons in the overview, whatever the mouse did. The renderer's overview_geometry_json still capped
    its line at three tiles (the old fixed three-desktop design), so with four it reported no layout at all and the
    pointer never went active. The same cap hid the layout after a pill's Add to a fourth desktop."""
    NAMES = ['XR-1', 'XR-2', 'XR-3', 'XR-4']

    def setUp(self):
        self.clock = Clock()
        self.c = view.ViewControl(view.ViewState(list(self.NAMES)), 'run', 'session', lambda name: None, None, None,
                                  clock=self.clock)
        self.addCleanup(self.c.join_focus, 2)
        self.p = self.c.overview_pointer = uc.UnifiedPointer(self.c, clock=self.clock)
        self.addCleanup(self.p.close)
        self.renderer = Renderer(self.c)
        self.cursor = (1200, 700)  # on the physical monitor, as after adoption moved its windows away

    def monitors(self):
        return [PHYSICAL] + [{'name': n, 'x': 2560 + W * i, 'y': 0, 'width': W, 'height': H, 'scale': 1.0}
                             for i, n in enumerate(self.NAMES)]

    def tick(self, n=1):
        out = []
        for _ in range(n):
            self.clock.now += 0.01
            if self.p.want[2] is not None and self.p.want[1] == 'desktop': self.cursor = self.p.want[2]
            self.p.feed(self.cursor, (0.0, 0.0), resize.outputs_from_monitors(self.monitors(), self.NAMES), self.p.want[0])
            out += frames(self.c.poll() or b'')
            self.renderer.report()
        return out

    def test_the_overview_shows_the_dot_and_the_pills_from_the_start(self):
        self.c.on_message({'op': 'zoom_in', 'payload': {}}); self.c.join_focus(2)
        self.tick(n=3)  # zoomed into a desktop: the renderer has reported its layout by now
        self.assertFalse(self.p.active())
        self.c.on_message({'op': 'zoom_out', 'payload': {}})  # Ctrl+Alt+Down: summons a cursor off the desktops
        fs = self.tick(n=4)
        self.assertEqual(len(self.p.geometry['tiles']), 4)
        self.assertTrue(self.p.active())
        self.assertTrue(self.p.sent['visible'])
        self.assertEqual(self.p.desktop, self.c.state.selected)
        self.assertIsNotNone(self.p._at(self.cursor))  # the real cursor came onto the selected desktop
        self.assertTrue(any(f['payload']['visible'] for f in fs if f['op'] == 'move_handle'))  # the glass pills

    def test_the_renderer_reports_a_layout_for_every_desktop_count(self):
        # The stand-in above reports any count; the real line must too (spike/src/view.cpp overview_geometry_json, ctest
        # free_pointer checks it on the renderer's own output). No fixed cap below the run's limit may come back.
        src = (Path(__file__).resolve().parents[2] / 'spike/src/view.cpp').read_text()
        body = src[src.index('std::string overview_geometry_json('):]
        body = body[:body.index('\n}\n')]
        self.assertIn('tiles.size() > kMaxDesktops', body)
        self.assertIsNone(re.search(r'tiles\.size\(\) *> *\d', body))
        line = {'w': 1920, 'h': 1200, 'half': [0.4136, 0.2585],
                'tiles': [[110.0 + 430 * i, 458.0, 410.0, 256.25] for i in range(fp.MAX_DESKTOPS)]}
        self.assertEqual(len(fp.parse_overview_geometry(line)['tiles']), fp.MAX_DESKTOPS)


if __name__ == '__main__':
    unittest.main()
