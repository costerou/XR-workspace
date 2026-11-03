"""Glass placement pills, supervisor side: the pill and panel geometry mirror (free_pointer.py, glass.hpp), hit testing
with a panel open, pill clicks and drags (keys pressed while the button is held win over the release), the panel's
sliders (the press-time `surface` the renderer keeps a held panel on), one panel at a time, click-outside, the keyboard
path (Ctrl+Alt+L, Tab, Escape, zoom), typing mode, idle close, corner resizing with a panel open, the legacy mouse-mode
cleanup, the settings panel's hint text and bind_check.

The pills replaced the head-locked placement menu card and its Ctrl+Alt+M mouse mode (feature/glass-pills, 2026-11-02).
Offline only: a fake clock, fake bind callables, the renderer's overview geometry fixture and (when installed) a stub
Lua run and `Hyprland --verify-config` on a throwaway config. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, orbit, resize, video, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, PLACEMENT_KEYS
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
NAMES = ['XR-1', 'XR-2', 'XR-3']
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-3', 'x': 6400, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
X1, X2 = 2560, 4480
T0, T1, T2 = G['tiles']
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)
HYPRLAND = shutil.which('Hyprland')
LUA = shutil.which('lua') or shutil.which('luajit')


def frames(data, ops=None):
    out = [json.loads(line) for line in (data or b'').decode().splitlines()]
    return [f for f in out if ops is None or f['op'] in ops]


def last(fs, op):
    return next((f['payload'] for f in reversed(fs) if f['op'] == op), None)


def ops_of(fs):
    return [f['op'] for f in fs]


def row_y(field):
    """Panel y of the middle of `field`'s slider row."""
    return fp.PANEL_ROWS_TOP + fp.PANEL_ROW_H * (PLACEMENT_KEYS.index(field) + 0.5)


def knob_x(t):
    """Panel x of the knob at fraction t of the track."""
    return fp.PANEL_KNOB_LO + t * (fp.PANEL_KNOB_HI - fp.PANEL_KNOB_LO)


def slider_value(field, lx):
    lo, hi = PLACEMENT_BOUNDS[field]
    return lo + math.floor(fp.slider_fraction(lx) * (hi - lo) + 0.5)


def on_panel(tile, lx, ly, placement=DEFAULT_PLACEMENT):
    """Canvas point of panel pixel (lx, ly) for a desktop at `tile` (each desktop faces you, so a side desktop's panel
    is turned toward you: projected, not flat)."""
    x, y, _, _ = fp.panel_rect(tile)
    return fp.project_glass(G, tile, placement, x + lx, y + ly)


EDGE = 1e-6  # canvas px: a point on a pill / corner zone's edge is moved just inside it (hit_at's ray round trip is ~1e-13)


def hit_at(g, placements, point, open_=None):
    """fp.hit for the dot looking at canvas point `point` on the overview's plane (orbit.from_plane)."""
    return fp.hit(g, placements, orbit.from_plane(g, point), open_)


def pill_centre(tile, placement=DEFAULT_PLACEMENT):
    x, y, w, h = fp.pill_rect(tile)
    return fp.project_glass(G, tile, placement, x + w / 2, y + h / 2)


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class MemoryPlacements:
    def __init__(self):
        self.saved = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        self.saves = 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements): self.saved = [dict(p) for p in placements]; self.saves += 1


# --- geometry -------------------------------------------------------------------------------------------------------
class GeometryTests(unittest.TestCase):
    def test_the_mirror_holds_the_renderers_numbers(self):
        # glass.hpp; the ctest glass_contract compares them with the renderer's own dump.
        self.assertEqual((fp.PILL_SHARE, fp.PILL_MIN, fp.PILL_MAX, fp.PILL_THICKNESS, fp.PILL_GAP), (0.24, 64.0, 160.0, 14.0, 10.0))
        self.assertEqual((fp.PANEL_W, fp.PANEL_H, fp.PANEL_HEADER_H), (480.0, 292.0, 42.0))  # four setting rows, Scale, the desktop row
        self.assertEqual((fp.PANEL_ROWS_TOP, fp.PANEL_ROW_H, fp.PANEL_KNOB_LO, fp.PANEL_KNOB_HI), (46.0, 34.0, 191.0, 367.0))
        self.assertEqual((fp.PILL_PAD_X, fp.PILL_PAD_Y, fp.PANEL_SLOP_PX, fp.CLICK_PX), (10.0, 10.0, 12.0, 4.0))
        # Seven rows and the footer fit the panel (glass.hpp kPanelFooterTop + kPanelHintPx + 6 <= kPanelH).
        self.assertLessEqual(fp.PANEL_ROWS_TOP + len(PLACEMENT_KEYS) * fp.PANEL_ROW_H + 8 + 18 + 6, fp.PANEL_H)
        self.assertGreaterEqual(fp.PANEL_ROWS_TOP, fp.PANEL_HEADER_H)

    def test_the_pill_sits_centred_under_the_desktop(self):
        x, y, w, h = fp.pill_rect(T0)
        self.assertAlmostEqual(w, 0.24 * T0[2])
        self.assertAlmostEqual(x + w / 2, T0[0] + T0[2] / 2)
        self.assertEqual((y, h), (T0[1] + T0[3] + 10, 14.0))  # under the desktop, not above it
        self.assertGreater(y, T0[1] + T0[3])

    def test_the_pill_width_is_clamped_and_never_wider_than_its_desktop(self):
        self.assertEqual(fp.pill_rect((0, 0, 1000, 300))[2], 160.0)   # 24 % would be 240
        self.assertEqual(fp.pill_rect((0, 0, 200, 300))[2], 64.0)     # 24 % would be 48
        self.assertEqual(fp.pill_rect((0, 0, 50, 30))[2], 50.0)       # narrower than the minimum: the desktop's width
        self.assertEqual(fp.pill_rect((0, 0, 50, 30))[:2], (0, 40.0))

    def test_the_panel_is_centred_under_the_desktop_with_its_top_at_the_pills(self):
        for tile in G['tiles'] + [(0.0, 100.0, 60.0, 40.0)]:
            with self.subTest(tile=tile):
                x, y, w, h = fp.panel_rect(tile)
                self.assertEqual((w, h), (480.0, 292.0))
                self.assertAlmostEqual(x + w / 2, tile[0] + tile[2] / 2)
                self.assertEqual(y, fp.pill_rect(tile)[1])

    def test_slider_fraction_runs_along_the_knob_range_and_clamps(self):
        self.assertEqual(fp.slider_fraction(fp.PANEL_KNOB_LO), 0.0)
        self.assertEqual(fp.slider_fraction(fp.PANEL_KNOB_HI), 1.0)
        self.assertAlmostEqual(fp.slider_fraction((fp.PANEL_KNOB_LO + fp.PANEL_KNOB_HI) / 2), 0.5)
        self.assertEqual(fp.slider_fraction(fp.PANEL_KNOB_LO - 12), 0.0)
        self.assertEqual(fp.slider_fraction(fp.PANEL_KNOB_HI + 12), 1.0)
        self.assertEqual(fp.slider_fraction(-1e9), 0.0); self.assertEqual(fp.slider_fraction(1e9), 1.0)

    def test_panel_hit_header_rows_slop_and_body(self):
        self.assertEqual(fp.panel_hit(240, 0, 1), ('pill', 1))
        self.assertEqual(fp.panel_hit(5, fp.PANEL_HEADER_H - 0.01, 1), ('pill', 1))  # the whole header strip is the pill
        self.assertEqual(fp.panel_hit(250, fp.PANEL_HEADER_H, 1), ('panel', 1))      # between header and first row
        self.assertEqual(fp.panel_hit(250, fp.PANEL_ROWS_TOP - 0.01, 1), ('panel', 1))
        for i, field in enumerate(PLACEMENT_KEYS):
            with self.subTest(field=field):
                top = fp.PANEL_ROWS_TOP + i * fp.PANEL_ROW_H
                self.assertEqual(fp.panel_hit(250, top, 2), ('slider', 2, field, 250))
                self.assertEqual(fp.panel_hit(250, top + fp.PANEL_ROW_H - 0.01, 2), ('slider', 2, field, 250))
                lo, hi = fp.PANEL_KNOB_LO - fp.PANEL_SLOP_PX, fp.PANEL_KNOB_HI + fp.PANEL_SLOP_PX
                self.assertEqual(fp.panel_hit(lo, top + 5, 0)[:3], ('slider', 0, field))   # slop either end, inclusive
                self.assertEqual(fp.panel_hit(hi, top + 5, 0)[:3], ('slider', 0, field))
                self.assertEqual(fp.panel_hit(lo - 0.5, top + 5, 0), ('panel', 0))       # the label
                self.assertEqual(fp.panel_hit(hi + 0.5, top + 5, 0), ('panel', 0))       # the value
        footer = fp.PANEL_ROWS_TOP + len(PLACEMENT_KEYS) * fp.PANEL_ROW_H
        self.assertEqual(fp.panel_hit(250, footer, 0), ('panel', 0))
        self.assertEqual(fp.panel_hit(250, fp.PANEL_H - 1, 0), ('panel', 0))


# --- hit testing ----------------------------------------------------------------------------------------------------
class HitTests(unittest.TestCase):
    def test_the_pill_zone_is_under_the_desktop_and_nothing_is_above_it(self):
        x, y, w, h = fp.pill_rect(T1)
        mid = x + w / 2
        self.assertEqual(hit_at(G, FLAT, (mid, y + h / 2)), ('pill', 1))
        self.assertEqual(hit_at(G, FLAT, (mid, T1[1] + T1[3] + 1)), ('pill', 1))    # the gap up to the desktop grabs it
        self.assertEqual(hit_at(G, FLAT, (mid, y + h + fp.PILL_PAD_Y - EDGE)), ('pill', 1))  # and a little below it
        self.assertIsNone(hit_at(G, FLAT, (mid, y + h + fp.PILL_PAD_Y + 1)))
        self.assertEqual(hit_at(G, FLAT, (x - fp.PILL_PAD_X + EDGE, y + 3)), ('pill', 1))
        self.assertEqual(hit_at(G, FLAT, (x + w + fp.PILL_PAD_X - EDGE, y + 3)), ('pill', 1))
        self.assertIsNone(hit_at(G, FLAT, (x - fp.PILL_PAD_X - 1, y + 3)))
        self.assertEqual(hit_at(G, FLAT, (mid, T1[1] + T1[3] - 1))[:2], ('desktop', 1))  # just inside: the desktop
        self.assertIsNone(hit_at(G, FLAT, (mid, T1[1] - 3)))   # where the old drag bar was: nothing now
        self.assertIsNone(hit_at(G, FLAT, (mid, T1[1] - 12)))
        for i, tile in enumerate(G['tiles']):  # every desktop's own (turned) pill
            self.assertEqual(fp.hit(G, FLAT, pill_centre(tile)), ('pill', i))

    def test_bottom_corners_win_over_the_pill_zone(self):
        x0, y0 = G['w'] / 2 - 30, G['h'] / 2 - 20  # centred, so it is the canvas itself
        tile = (x0, y0, 60.0, 40.0)  # a very small desktop: its pill zone is wider than the space between the corners
        g = dict(G, tiles=[tile])
        z = fp.corner_zone(tile)
        seen = set()
        for x in range(-12, 74, 2):
            x += EDGE if x in (-z, 60 - z) else -EDGE if x in (z, 60 + z) else 0  # (a zone's edge: just inside, see EDGE)
            h = hit_at(g, FLAT[:1], (x0 + x, y0 + 40 + 3))  # in the gap under the desktop
            seen.add(h and h[0])
            if abs(x) <= z: self.assertEqual(h, ('corner', 0, 'bl'), x)
            elif abs(x - 60) <= z: self.assertEqual(h, ('corner', 0, 'br'), x)
            elif -10 <= x <= 70: self.assertEqual(h, ('pill', 0), x)
            else: self.assertIsNone(h, x)
        self.assertEqual(seen, {'corner', 'pill', None})

    def test_an_open_panel_is_hit_first_header_sliders_and_body(self):
        self.assertEqual(fp.hit(G, FLAT, on_panel(T0, 240, 20), 0), ('pill', 0))  # header = the pill
        self.assertEqual(fp.hit(G, FLAT, on_panel(T0, 10, 5), 0), ('pill', 0))    # wider than the closed pill's zone
        for field in PLACEMENT_KEYS:
            with self.subTest(field=field):
                h = fp.hit(G, FLAT, on_panel(T0, knob_x(0.25), row_y(field)), 0)
                self.assertEqual(h[:3], ('slider', 0, field))
                self.assertAlmostEqual(h[3], knob_x(0.25), places=2)
        self.assertEqual(fp.hit(G, FLAT, on_panel(T0, 60, row_y('curvature')), 0), ('panel', 0))  # the row's label
        self.assertEqual(fp.hit(G, FLAT, on_panel(T0, 430, row_y('curvature')), 0), ('panel', 0))  # its value
        self.assertEqual(fp.hit(G, FLAT, on_panel(T0, 240, fp.PANEL_H - 10), 0), ('panel', 0))  # the footer
        # Without the panel open the same spots are empty space (the panel area is under the desktop).
        self.assertIsNone(fp.hit(G, FLAT, on_panel(T0, knob_x(0.25), row_y('curvature'))))
        # Just outside the panel: nothing.
        self.assertIsNone(fp.hit(G, FLAT, on_panel(T0, -1, 100), 0))
        self.assertIsNone(fp.hit(G, FLAT, on_panel(T0, 240, fp.PANEL_H + 1), 0))

    def test_with_a_panel_open_the_rest_still_hits_as_before(self):
        self.assertEqual(fp.hit(G, FLAT, pill_centre(T1), 0), ('pill', 1))                  # another desktop's pill
        self.assertEqual(hit_at(G, FLAT, (T1[0] + 200, T1[1] + 100), 0)[:2], ('desktop', 1))
        self.assertEqual(hit_at(G, FLAT, (T0[0] - 8, T0[1] - 8), 0), ('corner', 0, 'tl'))    # the open desktop's corners
        self.assertEqual(hit_at(G, FLAT, (T0[0] + T0[2] + 6, T0[1] + T0[3] + 2), 0), ('corner', 0, 'br'))
        self.assertEqual(hit_at(G, FLAT, (T0[0] + T0[2] / 2, T0[1] + T0[3] + 4), 0), ('pill', 0))  # the gap over the header
        self.assertIsNone(hit_at(G, FLAT, (960, 100), 0))

    def test_the_open_panel_wins_over_a_desktop_it_covers(self):
        # XR-1 turned 10 degrees right and lifted 10 degrees: its panel now hangs over XR-2.
        moved = [{**DEFAULT_PLACEMENT, 'x': 100, 'y': 100}, dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT)]
        point = on_panel(T0, knob_x(0.5), row_y('curvature'), moved[0])
        self.assertEqual(fp.hit(G, moved, point)[:2], ('desktop', 1))
        h = fp.hit(G, moved, point, 0)
        self.assertEqual(h[:3], ('slider', 0, 'curvature')); self.assertAlmostEqual(h[3], knob_x(0.5), places=2)

    def test_a_tilted_desktops_panel_is_hit_on_its_own_surface(self):
        tilted = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'curvature': 60}, dict(DEFAULT_PLACEMENT)]
        target = on_panel(T1, knob_x(0.8), row_y('curvature'), tilted[1])
        h = fp.hit(G, tilted, target, 1)
        self.assertEqual(h[:3], ('slider', 1, 'curvature'))
        self.assertAlmostEqual(h[3], knob_x(0.8), delta=0.1)
        self.assertEqual(fp.hit(G, tilted, on_panel(T1, 240, 8, tilted[1]), 1), ('pill', 1))

    def test_a_bad_open_index_is_ignored(self):
        point = on_panel(T0, knob_x(0.5), row_y('x'))
        for open_ in (None, -1, 3, 5, True, '0', 0.0):
            with self.subTest(open_=open_): self.assertIsNone(fp.hit(G, FLAT, point, open_))
        self.assertIsNone(fp.hit(G, FLAT[:1], on_panel(T1, 240, 5), 1))  # a one-desktop run has no desktop 1


# --- the pointer, the panel and the keys, together ------------------------------------------------------------------
class Base(unittest.TestCase):
    """A ViewControl with an OverviewPointer and recorded panel binds, driven like the sampler would, on a fake clock."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clock, self.store, self.binds = Clock(), MemoryPlacements(), []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None,
                                        LayoutStore(Path(tmp.name) / 'l.json'), None, placements=self.store,
                                        menu_binds=self.binds.append, clock=self.clock)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = fp.OverviewPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)
        self.state = self.control.state
        self.cur = None

    # sampler stand-ins
    def at(self, x, y, advance=0.004):
        self.clock.now += advance
        self.cur = (x, y)
        self.p.feed((x, y), (0.0, 0.0), OUTPUTS, self.p.want[0])
        return frames(self.control.poll())

    def move(self, dx, dy, advance=0.004):
        self.clock.now += advance
        self.p.feed(self.cur, (float(dx), float(dy)), OUTPUTS, self.p.want[0])
        return frames(self.control.poll())

    def to(self, x, y):
        """Free mode: move the dot to canvas point (x, y) in one step."""
        return self.move((x - self.p.pointer[0]) / self.p.gain, (y - self.p.pointer[1]) / self.p.gain)

    def by(self, dx, dy):
        return self.move(dx / self.p.gain, dy / self.p.gain)

    def free(self):
        """Push the cursor against XR-1's bottom edge: free mode just above the gap to its pill."""
        self.at(X1 + 960, 540)
        out = self.at(X1 + 960, 1079)
        self.assertEqual(self.p.mode, 'free')
        return out

    # keys and clicks
    def key(self, op):
        out = frames(self.control.on_message({'op': op, 'payload': {}}))
        self.join()
        return out

    def press(self): return self.key('resize_press')
    def release(self): return self.key('resize_release')

    def click(self, x, y):
        self.to(x, y)
        return self.press() + self.release()

    def join(self):
        for t in list(self.control._menu_threads): t.join(2)
        return self.binds

    def open_by_click(self, i=0):
        if self.p.mode != 'free': self.free()
        out = self.click(*pill_centre(G['tiles'][i]))
        self.assertTrue(self.state.menu_open); self.assertEqual(self.state.menu_desktop, i)
        return out


class PillClickTests(Base):
    def test_a_click_expands_the_pill_installs_the_keys_and_never_moves_the_desktop(self):
        self.free()
        out = self.to(*pill_centre(T0))
        self.assertEqual(self.p.hover, ('pill', 0))
        self.assertEqual(last(out, 'move_handle'), {'visible': True, 'desktop': 0, 'hover': True, 'active': False})
        out = self.press()
        self.assertEqual(self.p.drag['kind'], 'pill'); self.assertFalse(self.p.drag['moved'])
        self.assertEqual(self.p.pills, {'visible': True, 'desktop': 0, 'hover': True, 'active': False})  # held, not active
        self.assertTrue(last(out, 'overview_pointer')['held'])
        self.assertNotIn('placement_menu', ops_of(out))  # nothing opens on the press
        out = self.by(fp.CLICK_PX * 0.6, -fp.CLICK_PX * 0.6)  # a shaky hand: under CLICK_PX
        self.assertNotIn('placement', ops_of(out))
        self.assertFalse(self.p.drag['moved'])
        out = self.release()
        self.assertEqual(ops_of(out).count('placement_menu'), 1)
        self.assertEqual({k: last(out, 'placement_menu')[k] for k in ('open', 'desktop')}, {'open': True, 'desktop': 0})
        self.assertNotIn('placement', ops_of(out))
        self.assertEqual(self.state.placements, FLAT)
        self.assertEqual(self.store.saves, 0)
        self.assertEqual(self.join(), [True])  # the panel's arrows, Tab, R and Escape
        self.assertEqual((self.p.pill_clicks, self.p.moves), (1, 0))
        self.assertIsNone(self.p.drag)
        self.assertFalse(last(out, 'overview_pointer')['held'])

    def test_clicking_the_open_header_collapses_it_and_releases_the_keys(self):
        self.open_by_click(0)
        self.assertEqual(fp.hit(G, FLAT, self.p.pointer, 0), ('pill', 0))  # the pointer is now on the panel's header strip
        out = self.press()
        self.assertTrue(self.state.menu_open)  # its own header: no click-outside close on the press
        out += self.release()
        self.assertFalse(self.state.menu_open)
        self.assertFalse(last(out, 'placement_menu')['open'])
        self.assertEqual(self.join(), [True, False])
        self.assertEqual((self.p.pill_clicks, self.p.outside_closes), (2, 0))

    def test_a_click_anywhere_on_the_header_toggles_too(self):
        self.open_by_click(0)
        self.click(*on_panel(T0, 12, 30))
        self.assertFalse(self.state.menu_open)

    def test_a_click_while_hidden_by_idle_only_wakes_the_pills(self):
        self.free(); self.to(*pill_centre(T0))
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        self.assertFalse(last(frames(self.control.poll()), 'move_handle')['visible'])
        out = self.press() + self.release()
        self.assertFalse(self.state.menu_open); self.assertIsNone(self.p.drag)
        self.assertTrue(last(out, 'move_handle')['visible'])

    def test_escape_during_a_held_header_press_is_not_undone_by_the_release(self):
        self.open_by_click(0)
        self.press()  # on the open header
        self.key('menu_back')
        self.assertFalse(self.state.menu_open)
        out = self.release()
        self.assertFalse(self.state.menu_open)  # the release is not a click that opens it again
        self.assertNotIn('placement_menu', ops_of(out))
        self.assertEqual((self.p.pill_clicks, self.p.stale_clicks), (1, 1))
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.join(), [True, False])

    def test_tab_during_a_held_header_press_is_not_undone_by_the_release(self):
        self.open_by_click(0)
        self.press()
        self.key('menu_desktop_next')
        self.assertEqual(self.state.menu_desktop, 1)
        out = self.release()
        self.assertTrue(self.state.menu_open); self.assertEqual(self.state.menu_desktop, 1)  # stays where Tab put it
        self.assertNotIn('placement_menu', ops_of(out))
        self.assertEqual((self.p.pill_clicks, self.p.stale_clicks), (1, 1))

    def test_ctrl_alt_l_during_a_held_pill_press_is_not_undone_by_the_release(self):
        self.free()
        self.to(*pill_centre(T1))
        self.press()  # XR-2's closed pill; XR-1 is the selected desktop
        self.key('menu_toggle')
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 0))
        self.release()
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 0))  # not switched to XR-2's
        self.assertEqual((self.p.pill_clicks, self.p.stale_clicks), (0, 1))

    def test_a_click_after_a_click_outside_collapse_still_opens_the_other_pill(self):
        self.open_by_click(0)
        self.to(*pill_centre(T1))
        self.press()  # collapses XR-1's panel first; the press is recorded after that
        self.assertFalse(self.state.menu_open)
        self.release()
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 1))
        self.assertEqual((self.p.pill_clicks, self.p.stale_clicks, self.p.outside_closes), (2, 0, 1))

    def test_in_desktop_mode_a_click_goes_to_the_window_and_the_panel_stays(self):
        self.key('menu_toggle')
        self.at(X2 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.press() + self.release(), [])  # the run never gets such a click; if it did, nothing happens
        self.assertTrue(self.state.menu_open)


class PillDragTests(Base):
    def grab(self, i=0):
        self.free()
        self.to(*pill_centre(G['tiles'][i]))
        self.press()
        self.assertEqual(self.p.drag['kind'], 'pill')

    def test_a_drag_past_click_px_moves_the_desktop_live_and_saves_on_release(self):
        self.grab(0)
        out = self.by(0, -(fp.CLICK_PX - 0.1))  # within CLICK_PX: still a click
        self.assertFalse(self.p.drag['moved']); self.assertNotIn('placement', ops_of(out))
        out = self.by(0, -1.5)
        self.assertTrue(self.p.drag['moved']); self.assertEqual(self.p.moves, 1)
        self.assertEqual(last(out, 'move_handle'), {'visible': True, 'desktop': 0, 'hover': True, 'active': True})
        self.assertEqual(self.state.placements[0], DEFAULT_PLACEMENT)  # 1.7 % of the height: snapped back to the slot
        self.by(0, -T0[3] * 1.3)  # lifted clear of the row (no desktop may overlap another)...
        out = self.by(T0[2] * 0.2, 0)  # ...then to the right
        p = self.state.placements[0]
        want = orbit.drag_target(G, self.p.drag['tile'], (0, 0), self.p.drag['start'], self.p.pointer)  # the turn round you the pointer asks for
        self.assertEqual((p['x'], p['y']), want); self.assertGreater(p['x'], 0); self.assertGreater(p['y'], 0)
        self.assertEqual(last(out, 'placement')['desktops'][0]['y'], want[1])
        self.assertNotIn('placement_menu', ops_of(out))  # no panel is open
        self.assertEqual(self.store.saves, 0)  # debounced while dragging
        bar = fp.pill_rect(T0)  # the pill stays under the pointer
        centre = fp.project(G, T0, p, bar[0] + bar[2] / 2, bar[1] + bar[3] / 2)
        self.assertLess(math.hypot(centre[0] - self.p.pointer[0], centre[1] - self.p.pointer[1]), 8)
        out = self.release()
        self.assertEqual((self.store.saved[0]['x'], self.store.saved[0]['y']), want)
        self.assertFalse(self.state.menu_open)  # a drag is not a click
        self.assertEqual(self.p.pill_clicks, 0)
        self.assertEqual(self.join(), [])
        self.assertFalse(last(out, 'move_handle')['active'])

    def test_a_drag_bumps_its_neighbours_and_the_slot_snap_still_applies(self):
        self.grab(0)
        self.by(T0[2] * 0.6, 0)  # straight into XR-2 (the slots are 40 px, ~0.9 degrees, apart): it is pushed out of the way
        moved = [dict(p) for p in self.state.placements]
        self.assertGreater(moved[0]['x'], 9); self.assertGreater(moved[1]['x'], 0)
        self.assertEqual(orbit.overlap(orbit.footprint(G, T0, moved[0]), orbit.footprint(G, T1, moved[1])), 0)
        self.by(-T0[2] * 0.6 + T0[2] * 0.03, 0)  # back to 3 % from the slot: snaps home, XR-2 stays where it was pushed
        self.assertEqual(self.state.placements[0]['x'], 0)
        self.assertEqual(self.state.placements[1:], moved[1:])
        self.release()

    def test_a_lost_release_is_finished_by_the_next_press(self):
        self.grab(0)
        self.by(T0[2] * 0.1, -T0[3] * 0.2)
        moved = dict(self.state.placements[0])
        self.assertNotEqual(moved, DEFAULT_PLACEMENT)
        self.press()  # its release never came
        self.assertEqual(self.store.saved[0], moved)

    def test_the_open_headers_drag_moves_the_desktop_and_keeps_the_panel_open(self):
        self.open_by_click(0)
        self.to(*on_panel(T0, 400, 20))  # the header, right of where the closed pill was
        self.assertEqual(self.p.hover, ('pill', 0))
        self.press()
        out = self.by(0, -T0[3] * 0.3)
        want = orbit.drag_target(G, self.p.drag['tile'], (0, 0), self.p.drag['start'], self.p.pointer)
        self.assertGreater(want[1], 0); self.assertEqual(self.state.placements[0]['y'], want[1])
        self.assertEqual(ops_of(out)[:2], ['placement', 'placement_menu'])  # the open panel shows the new value
        self.assertEqual(last(out, 'placement_menu')['placements'][0]['y'], want[1])
        self.release()
        self.assertTrue(self.state.menu_open)
        self.assertEqual(self.store.saved[0]['y'], want[1])
        self.assertEqual(self.join(), [True])

    def test_idle_mid_drag_ends_and_saves_it(self):
        self.grab(0); self.by(30, -40)
        moved = dict(self.state.placements[0])
        self.assertNotEqual(moved, DEFAULT_PLACEMENT)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        self.control.poll()
        self.assertIsNone(self.p.drag); self.assertEqual(self.store.saved[0], moved)

    def test_zoom_in_ends_a_drag_and_saves_it(self):
        self.grab(0); self.by(30, -40)
        moved = dict(self.state.placements[0])
        self.key('zoom_in'); self.control.poll()
        self.assertEqual(self.p.mode, 'off'); self.assertIsNone(self.p.drag)
        self.assertEqual(self.store.saved[0], moved)


class SliderTests(Base):
    def setUp(self):
        super().setUp()
        self.open_by_click(0)

    def under(self, field, surface=DEFAULT_PLACEMENT):
        """The value of `field`'s slider at the pointer, on XR-1's panel drawn on `surface` (the press-time placement)."""
        q = fp.desktop_to_glass(T0, surface, fp.unproject(G, T0, surface, self.p.pointer))
        return slider_value(field, q[0] - fp.panel_rect(T0)[0])

    def press_slider(self, field, lx):
        self.to(*on_panel(T0, lx, row_y(field)))
        self.assertEqual(self.p.hover[:3], ('slider', 0, field))
        return self.press()

    def test_resizing_never_changes_the_panel_size_and_its_sliders_still_hit(self):
        # Drag the distance slider to 50 (twice the size): released, the panel rides on the resized desktop, yet its
        # track is exactly as long on screen as before, and a press at its far end still lands on the slider (200).
        row = row_y('distance')
        def track(p): return math.dist(on_panel(T0, knob_x(0), row, p), on_panel(T0, knob_x(1), row, p))  # on screen
        before = track(DEFAULT_PLACEMENT)
        self.press_slider('distance', knob_x((50 - 10) / 190))
        self.release()
        live = self.state.placements[0]
        self.assertEqual(live['distance'], 50)
        self.assertAlmostEqual(track(live), before, delta=0.005 * before)  # T0 is off-centre: turned to face you
        self.to(*on_panel(T0, knob_x(1), row, live))
        self.assertEqual(self.p.hover[:3], ('slider', 0, 'distance'))
        self.press()
        self.assertEqual(self.state.placements[0]['distance'], 200)
        self.release()

    def test_a_press_selects_the_setting_and_snaps_it_to_the_pointer(self):
        out = self.press_slider('distance', knob_x(0.5))
        self.assertEqual(self.state.menu_field, 'distance')
        self.assertEqual(self.state.placements[0]['distance'], 105)  # 10 + 0.5 * 190
        self.assertEqual(ops_of(out)[:2], ['placement', 'placement_menu'])
        menu = last(out, 'placement_menu')
        self.assertEqual((menu['open'], menu['desktop'], menu['field']), (True, 0, 'distance'))
        self.assertEqual(menu['placements'][0]['distance'], 105)
        self.assertEqual(last(out, 'placement')['desktops'][0]['distance'], 105)
        self.assertEqual(self.store.saves, 0); self.assertIsNotNone(self.control.save_due)
        self.assertEqual(self.p.drag['kind'], 'slider'); self.assertEqual(self.p.slides, 1)
        self.assertTrue(last(out, 'overview_pointer')['held'])
        self.release()
        self.assertEqual(self.store.saves, 1); self.assertEqual(self.store.saved[0]['distance'], 105)  # saved at once
        self.assertTrue(self.state.menu_open)
        self.assertIsNone(self.p.drag)

    def test_holding_and_dragging_moves_it_live_and_the_save_waits_for_release(self):
        self.press_slider('y', knob_x(0.5))
        self.assertEqual(self.state.placements[0]['y'], 0)
        values = []
        for _ in range(8):
            out = self.by(-11, 0)  # 11 px left: 1/16 of the track, 5.6 degrees
            values.append(self.state.placements[0]['y'])
            self.assertEqual(last(out, 'placement')['desktops'][0]['y'], values[-1])  # live, every step
            self.assertEqual(last(out, 'placement_menu')['placements'][0]['y'], values[-1])
        self.assertEqual(values, sorted(set(values), reverse=True))  # follows the pointer left, smaller every step
        self.assertEqual(values[-1], self.under('y'))  # the knob is where the pointer is on the (turned) panel
        self.assertEqual(self.store.saves, 0)  # the drag keeps postponing the debounced save
        self.release()
        self.assertEqual(self.store.saves, 1); self.assertEqual(self.store.saved[0]['y'], values[-1])

    def test_a_held_slider_saves_after_the_debounce_even_before_release(self):
        self.press_slider('curvature', knob_x(0.9))
        self.control.poll(self.clock.now + view.MENU_SAVE_DELAY - 0.01)
        self.assertEqual(self.store.saves, 0)
        self.control.poll(self.clock.now + view.MENU_SAVE_DELAY)
        self.assertEqual(self.store.saves, 1); self.assertEqual(self.store.saved[0]['curvature'], slider_value('curvature', knob_x(0.9)))

    def test_every_setting_reaches_both_bounds_through_the_slop_and_drags_clamp(self):
        for field in PLACEMENT_KEYS:
            lo, hi = PLACEMENT_BOUNDS[field]
            with self.subTest(field=field):
                self.press_slider(field, fp.PANEL_KNOB_LO - fp.PANEL_SLOP_PX + 0.01)
                self.assertEqual(self.state.placements[0][field], lo)
                self.by(-300, 0)  # dragged far past the low end
                self.assertEqual(self.state.placements[0][field], lo)
                self.release()
                self.assertEqual(self.store.saved[0][field], lo)
                self.key('menu_reset')  # flat again, so the panel is back where on_panel expects it
                self.press_slider(field, fp.PANEL_KNOB_HI + fp.PANEL_SLOP_PX - 0.01)
                self.assertEqual(self.state.placements[0][field], hi)
                self.by(300, 0)
                self.assertEqual(self.state.placements[0][field], hi)
                self.release()
                self.assertEqual(self.store.saved[0][field], hi)
                self.key('menu_reset')
        self.assertEqual(self.p.slides, 2 * len(PLACEMENT_KEYS))
        self.assertTrue(self.state.menu_open)

    def test_a_press_off_the_track_or_elsewhere_in_the_panel_does_nothing(self):
        for lx, ly in ((fp.PANEL_KNOB_LO - fp.PANEL_SLOP_PX - 2, row_y('curvature')), (430, row_y('x')), (240, fp.PANEL_H - 8),
                       (240, fp.PANEL_HEADER_H + 2)):
            with self.subTest(lx=lx, ly=ly):
                self.to(*on_panel(T0, lx, ly))
                self.assertEqual(self.p.hover, ('panel', 0))
                out = self.press() + self.release()
                self.assertNotIn('placement', ops_of(out)); self.assertNotIn('placement_menu', ops_of(out))
                self.assertTrue(self.state.menu_open); self.assertIsNone(self.p.drag)
        self.assertEqual(self.state.placements, FLAT)
        self.assertEqual((self.p.outside_closes, self.p.slides), (0, 0))
        self.assertEqual(self.join(), [True])

    def test_pressing_the_value_already_set_only_moves_the_highlight(self):
        out = self.press_slider('y', knob_x(0.5))  # y 0 = the slot: no placement change
        self.assertEqual(ops_of(out).count('placement'), 0)
        self.assertEqual(last(out, 'placement_menu')['field'], 'y')
        self.release()
        out = self.press_slider('y', knob_x(0.5))
        self.assertNotIn('placement', ops_of(out))
        menu = last(out, 'placement_menu')  # only the held surface is new: the values and highlight are unchanged
        self.assertEqual({k: menu[k] for k in ('field', 'placements')}, {'field': 'y', 'placements': FLAT})
        self.assertEqual(menu['surface'], DEFAULT_PLACEMENT)
        out = self.release()
        self.assertNotIn('placement', ops_of(out)); self.assertNotIn('surface', last(out, 'placement_menu'))

    def test_a_setting_that_moves_its_own_panel_does_not_chase_itself(self):
        # x moves the desktop, and the panel with it, sideways: the drag is measured on the panel as it was at the press.
        self.press_slider('x', knob_x(0.5))
        self.assertEqual(self.state.placements[0]['x'], 0)
        self.by(44, 0)
        x = self.state.placements[0]['x']
        self.assertEqual(x, self.under('x'))  # measured on the press-time panel, not the moved one
        self.assertGreater(x, 800)  # 44 px of a 176 px track: the desktop turned ~80 degrees round you
        for _ in range(5):
            self.by(0, 0.5)  # tiny motion along the row: the value stays put, no oscillation
            self.assertEqual(self.state.placements[0]['x'], x)
        self.release()
        self.assertEqual(self.store.saved[0]['x'], x)

    def knob_on(self, placement, field):
        """Canvas point where the renderer draws `field`'s knob for its current value, on XR-1's panel placed at
        `placement`."""
        lo, hi = PLACEMENT_BOUNDS[field]
        lx = fp.PANEL_KNOB_LO + (self.state.placements[0][field] - lo) / (hi - lo) * (fp.PANEL_KNOB_HI - fp.PANEL_KNOB_LO)
        x, y, _, _ = fp.panel_rect(T0)
        return fp.project(G, T0, placement, x + lx, y + row_y(field))

    def test_a_held_panel_is_drawn_on_its_press_surface_so_the_knob_stays_under_the_pointer(self):
        # Settings that move the desktop, and with it a panel drawn on its live placement: x and y turn it round you
        # (about 20 and 10 tenths of a degree per knob px), distance scales it about its centre.
        for field, steps, drift in (('x', (5, 10, 20), 100), ('y', (8, 30, -60), 5), ('distance', (6, -20, 40), 5)):
            with self.subTest(field=field):
                pressed = dict(self.state.placements[0])  # before the press snaps the value to the pointer
                out = self.press_slider(field, knob_x(0.5))
                self.assertEqual(last(out, 'placement_menu')['surface'], pressed)
                start = self.p.pointer
                for dx in steps:
                    out = self.to(start[0] + dx, start[1])
                    menu = last(out, 'placement_menu')
                    self.assertEqual(menu['surface'], pressed)  # every frame of the drag: the press-time surface
                    self.assertEqual(menu['placements'][0][field], self.state.placements[0][field])  # the value is live
                    self.assertNotEqual(self.state.placements[0], pressed)
                    knob = self.knob_on(menu['surface'], field)
                    self.assertLess(math.hypot(knob[0] - self.p.pointer[0], knob[1] - self.p.pointer[1]), 1.5)
                live = self.knob_on(self.state.placements[0], field)  # where a live-placed panel would have put it
                self.assertGreater(math.hypot(live[0] - self.p.pointer[0], live[1] - self.p.pointer[1]), drift)  # x: 135 px
                out = self.release()
                self.assertNotIn('surface', last(out, 'placement_menu'))  # back on the desktop's live placement
                self.assertIsNone(self.state.menu_surface)
                self.key('menu_reset')

    def test_a_key_that_drops_the_held_surface_ends_what_the_drag_does(self):
        self.press_slider('x', knob_x(0.5))
        self.key('menu_desktop_next')  # Tab while holding: the panel moves to XR-2, no surface there
        self.assertIsNone(self.state.menu_surface)
        self.assertNotIn('surface', self.state.menu())
        out = self.by(40, 0)
        self.assertEqual(ops_of(out).count('placement'), 0)  # XR-1's slider no longer applies
        out = self.release()
        self.assertNotIn('placement', ops_of(out)); self.assertNotIn('placement_menu', ops_of(out))
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 1))
        self.assertEqual(self.state.placements, FLAT)

    def test_the_surface_is_never_sent_without_an_open_panel(self):
        self.press_slider('curvature', knob_x(0.8))
        out = self.key('menu_back')  # Escape while holding
        self.assertFalse(last(out, 'placement_menu')['open']); self.assertNotIn('surface', last(out, 'placement_menu'))
        self.release()
        s = view.ViewState(NAMES)
        s.hold_surface(dict(DEFAULT_PLACEMENT))
        self.assertIsNone(s.menu_surface)  # collapsed: ignored
        s.open_menu(0); s.hold_surface({**DEFAULT_PLACEMENT, 'curvature': 10})
        self.assertEqual(s.menu()['surface']['curvature'], 10)
        with self.assertRaises(ValueError): s.hold_surface({**DEFAULT_PLACEMENT, 'curvature': 999})
        s.apply_menu('menu_toggle')
        self.assertIsNone(s.menu_surface)
        frame = json.loads(view.placement_menu_frame('run', 's', 4, {**s.menu(), 'open': True, 'surface': dict(DEFAULT_PLACEMENT)}))
        self.assertEqual(frame['payload']['surface'], DEFAULT_PLACEMENT)
        for bad in ({**s.menu(), 'surface': dict(DEFAULT_PLACEMENT)},  # closed
                    {**s.menu(), 'open': True, 'surface': None}, {**s.menu(), 'open': True, 'surface': {**DEFAULT_PLACEMENT, 'x': 1801}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.placement_menu_frame('run', 's', 4, bad)

    def test_the_state_rejects_bad_slider_input_and_ignores_a_closed_panel(self):
        s = view.ViewState(NAMES)
        self.assertFalse(s.slide('x', 0.5))  # every pill collapsed: nothing
        s.open_menu(1)
        for field, t in (('zoom', 0.5), ('x', float('nan')), ('x', float('inf')), ('x', '0.5'), ('x', None)):
            with self.subTest(field=field, t=t), self.assertRaises(ValueError): s.slide(field, t)
        self.assertTrue(s.slide('curvature', 1.0)); self.assertEqual(s.placements[1]['curvature'], 120)
        self.assertEqual(s.menu_field, 'curvature')
        self.assertTrue(s.slide('curvature', -3)); self.assertEqual(s.placements[1]['curvature'], 0)
        self.assertEqual(s.placements[0], DEFAULT_PLACEMENT)
        for bad in (-1, 3, True, '1', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError): s.open_menu(bad)
        with self.assertRaises(ValueError): view.ViewState(['XR-1']).open_menu(1)

    def test_a_slide_for_a_panel_that_is_not_open_does_nothing(self):
        self.assertEqual(self.control.pill_slide(1, 'x', 1.0), b'')
        self.assertEqual(self.state.placements, FLAT)
        self.key('menu_back')
        self.assertEqual(self.control.pill_slide(0, 'x', 1.0), b'')
        self.assertEqual(self.state.placements, FLAT)


class OneAtATimeTests(Base):
    def test_clicking_another_pill_switches_the_open_panel(self):
        self.open_by_click(0)
        self.key('menu_inc')  # a change on XR-1's panel, still inside its save delay
        self.assertEqual(self.store.saves, 0)
        out = self.to(*pill_centre(T1))
        self.assertEqual(self.p.hover, ('pill', 1))
        out = self.press()
        self.assertFalse(self.state.menu_open)  # the press outside XR-1's panel collapses it at once...
        self.assertFalse(last(out, 'placement_menu')['open'])
        self.assertEqual(self.store.saves, 1); self.assertEqual(self.store.saved[0]['x'], 2)  # ...saved
        self.assertEqual(self.p.drag['index'], 1)  # ...and the press carries on as XR-2's pill press
        out = self.release()
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 1))
        self.assertEqual({k: last(out, 'placement_menu')[k] for k in ('open', 'desktop')}, {'open': True, 'desktop': 1})
        self.assertEqual(self.join(), [True, False, True])
        self.assertEqual((self.p.outside_closes, self.p.pill_clicks), (1, 2))

    def test_pill_click_switches_directly_and_saves_the_previous_panel_first(self):
        self.control.pill_click(0); self.key('menu_inc')
        out = frames(self.control.pill_click(2))
        self.assertEqual(ops_of(out), ['placement_menu'])
        self.assertEqual((last(out, 'placement_menu')['open'], last(out, 'placement_menu')['desktop']), (True, 2))
        self.assertEqual(self.store.saved[0]['x'], 2)
        self.assertEqual(self.join(), [True])  # the keys stay installed: one panel stayed open throughout
        self.assertEqual(frames(self.control.pill_click(2))[-1]['payload']['open'], False)  # its own pill: collapse
        self.assertEqual(self.join(), [True, False])

    def test_a_bad_desktop_index_is_ignored(self):
        two = view.ViewControl(view.ViewState(['XR-1', 'XR-2']), 'run', 'session', None, None, None)
        for bad in (2, -1, None, '0', True):
            with self.subTest(bad=bad): self.assertEqual(two.pill_click(bad), b'')
        self.assertFalse(two.state.menu_open)


class ClickOutsideTests(Base):
    def setUp(self):
        super().setUp()
        self.open_by_click(0)
        self.key('menu_inc')  # pending, unsaved

    def assert_closed_and_saved(self, out):
        self.assertFalse(self.state.menu_open)
        self.assertFalse(last(out, 'placement_menu')['open'])
        self.assertEqual(self.store.saved[0]['x'], 2)
        self.assertEqual(self.join(), [True, False])
        self.assertEqual(self.p.outside_closes, 1)

    def test_empty_space_closes_it_and_does_nothing_else(self):
        self.to(960, 150)  # above the desktops
        self.assertIsNone(self.p.hover)
        out = self.press() + self.release()
        self.assert_closed_and_saved(out)
        self.assertNotIn('placement', ops_of(out)); self.assertNotIn('view', ops_of(out))
        self.assertEqual(self.p.mode, 'free'); self.assertIsNone(self.p.drag)

    def test_a_desktop_press_closes_it_and_still_zooms_into_that_desktop(self):
        self.to(T1[0] + T1[2] / 2, T1[1] + 1)  # just inside XR-2's top edge (less than ENTER_PX: still free)
        self.assertEqual(self.p.hover[:2], ('desktop', 1)); self.assertEqual(self.p.mode, 'free')
        out = self.press()
        self.assert_closed_and_saved(out)
        out += self.release()  # a click (no hold): zoom in on its release
        self.assertTrue(self.state.zoomed); self.assertEqual(self.state.selected, 1)
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertEqual(self.p.focus_clicks, 1)

    def test_another_desktops_pill_drag_closes_it_and_still_moves_that_desktop(self):
        self.to(*pill_centre(T2))
        out = self.press()
        self.assert_closed_and_saved(out)
        self.by(0, -T2[3] * 0.4)
        want = orbit.drag_target(G, self.p.drag['tile'], (0, 0), self.p.drag['start'], self.p.pointer)[1]
        self.assertGreater(want, 0); self.assertEqual(self.state.placements[2]['y'], want)
        self.release()
        self.assertFalse(self.state.menu_open)  # a drag, not a click: nothing opens
        self.assertEqual(self.store.saved[2]['y'], want)

    def test_the_open_desktops_own_pieces_do_not_count_as_outside(self):
        # header, panel body, a corner of the same desktop, and the gap over the header (its pill zone)
        for point, click_collapses in ((on_panel(T0, 240, 10), True), (on_panel(T0, 240, fp.PANEL_H - 6), False),
                                       (orbit.from_plane(G, (T0[0] - 6, T0[1] - 6)), False),
                                       (orbit.from_plane(G, (T0[0] + T0[2] / 2, T0[1] + T0[3] + 3)), True)):
            with self.subTest(point=point):
                self.to(*point)
                self.press()
                self.assertTrue(self.state.menu_open)  # the press itself never collapses it
                self.release()
                self.assertEqual(self.state.menu_open, not click_collapses)  # a click on its own pill does
                if click_collapses: self.control.pill_click(0)
        self.assertEqual(self.p.outside_closes, 0)
        self.assertEqual(self.state.placements[0], {**DEFAULT_PLACEMENT, 'x': 2})  # nothing moved


class KeyboardTests(Base):
    def test_ctrl_alt_l_opens_the_selected_desktops_pill_and_closes_whichever_is_open(self):
        self.state.selected = 2
        out = self.key('menu_toggle')
        self.assertEqual(ops_of(out), ['placement_menu'])
        self.assertEqual({k: out[0]['payload'][k] for k in ('open', 'desktop')}, {'open': True, 'desktop': 2})
        self.assertEqual(self.binds, [True])
        self.control.pill_click(1)  # the pointer switched to XR-2's pill; XR-3 is still the selected desktop
        out = self.key('menu_toggle')
        self.assertFalse(out[0]['payload']['open'])  # closes the open one, never switches
        self.assertEqual(self.binds, [True, False])

    def test_ctrl_alt_l_while_zoomed_in_zooms_out_first(self):
        self.key('zoom_in')
        self.assertTrue(self.state.zoomed)
        out = self.key('menu_toggle')
        self.assertEqual(ops_of(out), ['view', 'placement_menu'])
        self.assertFalse(out[0]['payload']['zoomed']); self.assertFalse(self.state.zoomed)
        self.assertTrue(out[1]['payload']['open'])
        self.assertTrue(self.p.summoned)  # a cursor off the desktops is brought into the selected one
        out = self.key('menu_toggle')  # closing never zooms
        self.assertEqual(ops_of(out), ['placement_menu'])

    def test_a_stale_pill_click_while_zoomed_in_zooms_out_first_too(self):
        self.key('zoom_in')
        out = frames(self.control.pill_click(1))
        self.assertEqual(ops_of(out), ['view', 'placement_menu'])
        self.assertFalse(self.state.zoomed)
        self.assertEqual((self.state.menu_open, self.state.menu_desktop), (True, 1))
        self.assertEqual(self.join(), [True])

    def test_escape_collapses_and_is_a_no_op_when_nothing_is_open(self):
        self.key('menu_toggle')
        out = self.key('menu_back')
        self.assertEqual(ops_of(out), ['placement_menu']); self.assertFalse(out[0]['payload']['open'])
        self.assertEqual(self.key('menu_back'), [])
        self.assertEqual(self.binds, [True, False])

    def test_zoom_in_collapses_an_open_panel_first_saved_and_released(self):
        self.open_by_click(0); self.key('menu_inc_big')
        out = self.key('zoom_in')
        self.assertEqual(ops_of(out), ['placement_menu', 'view'])
        self.assertFalse(out[0]['payload']['open']); self.assertTrue(out[1]['payload']['zoomed'])
        self.assertEqual(self.store.saved[0]['x'], view.MENU_STEPS['x'][1])
        self.assertEqual(self.binds, [True, False])
        out = frames(self.control.poll())
        self.assertEqual(self.p.mode, 'off')
        self.assertFalse(last(out, 'move_handle')['visible'])

    def test_tab_moves_the_one_open_panel_to_the_next_and_previous_desktop(self):
        self.open_by_click(0)
        seen = []
        for op in ('menu_desktop_next', 'menu_desktop_next', 'menu_desktop_next', 'menu_desktop_prev'):
            out = self.key(op)
            self.assertEqual(ops_of(out), ['placement_menu'])
            self.assertTrue(out[0]['payload']['open'])
            seen.append(self.state.menu_desktop)
        self.assertEqual(seen, [1, 2, 0, 2])
        self.assertEqual(self.binds, [True])  # still the same keys: one panel open throughout
        self.to(*pill_centre(T2))
        self.assertEqual(self.p.hover, ('pill', 2))  # where XR-3's pill was is now its open panel's header
        self.to(*on_panel(T2, knob_x(1.0), row_y('curvature')))
        self.assertEqual(self.p.hover[:3], ('slider', 2, 'curvature'))  # the pointer follows the moved panel
        self.press(); self.release()
        self.assertEqual(self.state.placements[2]['curvature'], 120)

    def test_arrows_shift_and_r_still_work_on_a_clicked_panel(self):
        self.open_by_click(1)
        self.key('menu_field_next'); self.key('menu_inc')  # y + 0.2 degrees
        self.key('menu_inc_big')                           # y + 10 degrees
        self.assertEqual(self.state.placements[1]['y'], sum(view.MENU_STEPS['y']))
        self.key('menu_reset')
        self.assertEqual(self.state.placements[1], DEFAULT_PLACEMENT)

    def test_ctrl_alt_m_and_mouse_mode_are_gone(self):
        chords = {c for c, _ in view.BINDS + view.MENU_BINDS}
        self.assertNotIn('CTRL + ALT + M', chords)
        self.assertNotIn('menu_mouse', view.MENU_OPS)
        for name in ('MOUSE_OPS', 'MOUSE_BINDS', 'MOUSE_GAIN', 'parse_menu_geometry', 'menu_pointer_frame',
                     'install_mouse_binds_lua', 'remove_mouse_binds_lua'):
            self.assertFalse(hasattr(view, name), name)
        self.assertTrue(hasattr(view, 'MAX_POINTER_PX')); self.assertTrue(hasattr(view, 'warp_cursor_lua'))
        for name in ('mouse', 'geometry', 'pointer', 'set_geometry', 'mouse_move'):
            self.assertFalse(hasattr(self.state, name), name)
        for name in ('mouse_wake', 'set_menu_geometry', 'drain_mouse_wake', 'mouse_binds'):
            self.assertFalse(hasattr(self.control, name), name)
        self.assertEqual(self.key('menu_mouse'), [])  # an unknown op: ignored
        self.assertFalse(self.state.menu_open)


class TypingModeTests(Base):
    def test_typing_mode_collapses_the_panel_hides_the_pills_and_turns_the_pointer_off(self):
        self.open_by_click(0); self.key('menu_inc')
        out = self.key(view.TYPING_OP)
        self.assertEqual(ops_of(out), ['placement_menu', 'view'])
        self.assertFalse(out[0]['payload']['open']); self.assertTrue(out[1]['payload']['typing'])
        self.assertEqual(self.store.saved[0]['x'], 2)
        self.assertEqual(self.binds, [True, False])
        out = self.move(0, 10)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(self.p.want[1], 'off')  # cursor shown, left button back
        self.assertFalse(last(out, 'move_handle')['visible'])  # the pills hide
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertFalse(self.p.overview())
        # Nothing reaches the pills while typing: stale clicks and the keys are ignored.
        self.assertEqual(self.control.pill_click(0), b'')
        self.assertIsNone(self.control.on_message({'op': 'resize_press'}))
        self.assertEqual(self.key('menu_toggle'), [])
        self.assertFalse(self.state.menu_open); self.assertIsNone(self.p.drag)
        self.key(view.TYPING_OP)
        self.at(X1 + 960, 540)
        out = self.at(X1 + 960, 1079)
        self.assertEqual(self.p.mode, 'free')
        self.assertTrue(self.p.pills['visible'])
        self.assertFalse(self.state.menu_open)  # leaving typing mode reopens nothing

    def test_typing_mode_ends_a_held_slider_and_saves_it(self):
        self.open_by_click(0)
        self.to(*on_panel(T0, knob_x(0.9), row_y('curvature')))
        self.press()
        self.key(view.TYPING_OP)
        self.control.poll()
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.store.saved[0]['curvature'], slider_value('curvature', knob_x(0.9)))


class PointerStaysOnTests(Base):
    def test_the_free_pointer_stays_on_while_a_panel_is_open(self):
        self.free()
        self.key('menu_toggle')
        self.assertTrue(self.p.overview()); self.assertTrue(self.p.active())
        out = self.by(0, 30)
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p.want[1], 'free')
        self.assertTrue(last(out, 'overview_pointer')['visible'])

    def test_moving_over_an_open_panel_postpones_its_idle_close(self):
        self.open_by_click(0)
        for _ in range(3):
            self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
            self.by(0, 3)
            self.assertTrue(self.state.menu_open)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S
        out = frames(self.control.poll())
        self.assertFalse(self.state.menu_open); self.assertFalse(last(out, 'placement_menu')['open'])
        self.assertEqual(self.join(), [True, False])

    def test_a_panel_opened_by_a_click_closes_when_idle_saved_and_released(self):
        self.open_by_click(0)
        self.to(*on_panel(T0, knob_x(0.76), row_y('distance'))); self.press(); self.release()
        self.assertTrue(self.state.menu_open)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        out = frames(self.control.poll())
        self.assertFalse(self.state.menu_open)
        self.assertFalse(last(out, 'placement_menu')['open'])
        self.assertFalse(last(out, 'move_handle')['visible'])  # the pills hide with the dot after 30 s too
        self.assertEqual(self.store.saved[0]['distance'], slider_value('distance', knob_x(0.76)))
        self.assertEqual(self.join(), [True, False])

    def test_a_playing_video_turns_it_off_and_ignores_stale_pill_clicks(self):
        # (Opening a video also collapses the panel: test_video.VideoWithPillsAndResizeTests.)
        self.open_by_click(0)
        self.control.video_active = True
        self.assertEqual(self.control.pill_click(1), b'')
        self.assertFalse(self.p.overview())
        out = self.move(0, 10)
        self.assertEqual(self.p.mode, 'off')
        self.assertFalse(last(out, 'move_handle')['visible'])
        self.assertIn('resize_press', video.IGNORED_WHILE_PLAYING)
        for op in view.MENU_OPS: self.assertIn(op, video.IGNORED_WHILE_PLAYING)


class FakeClient:
    """Compositor client stand-in for the real sampler thread: the cursor rests on XR-1's bottom edge."""
    def __init__(self):
        self.calls, self.lock = [], threading.Lock()
    def query(self, kind, argument=None, timeout=2.0):
        with self.lock: self.calls.append(kind)
        if kind == 'monitors': return MONITORS
        if kind == 'cursorpos': return {'x': X1 + 960, 'y': 1079}
        raise ValueError(kind)
    def _call(self, kind):
        with self.lock: self.calls.append(kind)
    def grab_pointer(self, *args): self._call('grab')
    def release_pointer(self, *args): self._call('release')
    def remove_resize_binds(self): self._call('remove')
    def warp_cursor(self, x, y): self._call('warp')
    def count(self, kind):
        with self.lock: return self.calls.count(kind)


class SamplerWithPanelTests(unittest.TestCase):
    """The real sampler thread keeps reading and grabbing while a panel is open (the pointer is how it is clicked)."""
    def start(self, typing):
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(control.stop_menu_binds)
        p = control.overview_pointer = fp.OverviewPointer(control); self.addCleanup(p.close)
        p.set_geometry(G)
        control.state.open_menu(1)
        control.state.typing = typing
        client = FakeClient()
        sampler = fp.PointerSampler(client, NAMES, p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return control, p, client, sampler

    def test_reads_and_grabs_with_a_panel_open(self):
        control, p, client, sampler = self.start(typing=False)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not (client.count('grab') and p.mode == 'free'):
            control.poll(); time.sleep(0.003)
        sampler.stop()
        self.assertGreater(client.count('cursorpos'), 0)
        self.assertEqual(client.count('grab'), 1); self.assertEqual(p.mode, 'free')
        self.assertTrue(control.state.menu_open)

    def test_no_reads_and_no_grab_in_typing_mode_even_with_a_panel_state(self):
        control, p, client, sampler = self.start(typing=True)
        time.sleep(0.12)
        sampler.stop()
        self.assertEqual((client.count('cursorpos'), client.count('grab')), (0, 0))


class CornerWithPanelTests(Base):
    def test_resizing_the_open_desktop_by_its_corner_keeps_the_panel_open_and_in_step(self):
        self.key('menu_toggle')  # XR-1's pill, from the keyboard
        self.free()
        self.to(T0[0] + T0[2] + 4, T0[1] - 4)  # outside the top-right corner
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        out = self.press()
        self.assertTrue(last(out, 'resize_handle')['active'])
        self.assertTrue(self.state.menu_open)
        out = self.by(T0[2] / 2, -T0[3] / 2)  # twice as far from the centre: twice the size
        self.assertEqual(self.state.placements[0]['distance'], 50)
        self.assertEqual(last(out, 'placement')['desktops'][0]['distance'], 50)
        self.assertEqual(last(out, 'placement_menu')['placements'][0]['distance'], 50)  # the panel shows it live
        self.release()
        self.assertEqual(self.store.saved[0]['distance'], 50)
        self.assertTrue(self.state.menu_open); self.assertEqual(self.p.outside_closes, 0)
        self.assertEqual(self.binds, [True])

    def test_another_desktops_corner_closes_it_then_resizes(self):
        self.key('menu_toggle')
        self.free()
        self.to(T1[0] - 4, T1[1] - 4)
        self.assertEqual(self.p.hover, ('corner', 1, 'tl'))
        self.press()
        self.assertFalse(self.state.menu_open); self.assertEqual(self.p.drag['kind'], 'corner')
        self.by(-T1[2] / 2, -T1[3] / 2)
        self.release()
        self.assertEqual(self.store.saved[1]['distance'], 50)


# --- legacy cleanup, wire and binds ---------------------------------------------------------------------------------
class LegacyMouseModeTests(unittest.TestCase):
    """Builds before the glass pills had a menu mouse mode; a run of one that died in it left a left-button bind."""
    def test_the_bind_helper_keeps_the_old_op_names_for_cleanup_only(self):
        self.assertEqual(xr_control.LEGACY_OPS, ('menu_mouse', 'menu_mouse_press', 'menu_mouse_release'))
        self.assertEqual(xr_control.OPS[-3:], xr_control.LEGACY_OPS)
        for op in xr_control.LEGACY_OPS:
            self.assertNotIn(op, view.MENU_OPS + view.USER_OPS + resize.RESIZE_OPS)
            with self.subTest(op=op), mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
                self.assertEqual(xr_control.main(['x', '/nonexistent/control.sock', 'run', 's', op]), 1)
                drop.assert_called_once_with()  # the menu removal text, legacy tail included

    def test_the_run_rejects_the_old_ops_and_the_menu_pointer(self):
        for op in xr_control.LEGACY_OPS + ('menu_pointer', 'menu_geometry'):
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            with self.subTest(op=op), self.assertRaises(ValueError): decode_control(frame)

    def test_the_removal_text_is_shared_and_restores_the_cursor(self):
        self.assertEqual(xr_control.REMOVE_MENU_BINDS, view.remove_menu_binds_lua())
        restore = 'pcall(hl.config, { cursor = { invisible = s.invisible } })'
        for name, lua in (('panel closed', view.remove_menu_binds_lua()), ('run end', view.remove_binds_lua()),
                          ('dead run key', xr_control.REMOVE_MENU_BINDS)):
            with self.subTest(name):
                self.assertIn('xr_workspace_menu_binds = nil', lua)
                self.assertIn('xr_workspace_mouse_binds = nil', lua)
                self.assertIn(restore, lua)
                self.assertIn('hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })', lua)
        # The current build never creates the legacy tables: neither its panel binds nor the free pointer's.
        for lua in (view.install_menu_binds_lua(*BIND_ARGS), fp.grab_pointer_lua(*BIND_ARGS, 1, 2), fp.release_pointer_lua(),
                    resize.install_resize_binds_lua(*BIND_ARGS), view.install_binds_lua(*BIND_ARGS)):
            self.assertNotIn('xr_workspace_mouse = {', lua)
            self.assertNotIn('xr_workspace_mouse_binds = {', lua)
            self.assertNotIn('menu_mouse', lua)

    @unittest.skipUnless(LUA, 'no Lua interpreter')
    def test_the_legacy_tail_restores_an_old_runs_cursor_and_is_a_no_op_otherwise(self):
        import subprocess
        stub = r'''
local state = { invisible = true, x = 5, y = 6, removed = 0 }
hl = { dsp = { cursor = {} } }
function hl.config(t) state.invisible = t.cursor.invisible end
function hl.dsp.cursor.move(p) return p end
function hl.dispatch(p) state.x, state.y = p.x, p.y end
local function bind() return setmetatable({ remove = function() state.removed = state.removed + 1 end },
                                          { __tostring = function() return "HL.Keybind" end }) end
local function show(tag) print(tag, tostring(state.invisible), state.x, state.y, state.removed,
                               tostring(xr_workspace_mouse == nil), tostring(xr_workspace_mouse_binds == nil)) end
'''
        body = '\n'.join([
            view.remove_menu_binds_lua(), 'show("none")',  # nothing left behind: nothing changes
            'xr_workspace_mouse_binds = { bind(), bind() } xr_workspace_mouse = { invisible = false, home = { x = 100, y = 200 } }',
            'xr_workspace_menu_binds = { bind() }',
            xr_control.REMOVE_MENU_BINDS, 'show("legacy")',
            xr_control.REMOVE_MENU_BINDS, 'show("again")'])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'run.lua'
            script.write_text(stub + body)
            done = subprocess.run([LUA, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([line.split('\t') for line in done.stdout.splitlines()],
                         [['none', 'true', '5', '6', '0', 'true', 'true'],
                          ['legacy', 'false', '100', '200', '3', 'true', 'true'],  # binds gone, cursor shown and put back
                          ['again', 'false', '100', '200', '3', 'true', 'true']])

    def test_warp_lua_is_kept_fixed_and_validated(self):
        self.assertEqual(view.warp_cursor_lua(10, 20), 'pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = 10, y = 20 })) end)')
        self.assertEqual(view.warp_cursor_lua(-40, 0), 'pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = -40, y = 0 })) end)')
        for bad in ((1.5, 2), ('1', 2), (True, 2), (10 ** 7, 0), (0, float('nan'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.warp_cursor_lua(*bad)

    def test_the_compositor_client_has_no_mouse_mode_calls(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        self.assertFalse(hasattr(client, 'install_mouse_binds')); self.assertFalse(hasattr(client, 'remove_mouse_binds'))
        sent = []
        client._eval = sent.append
        client.warp_cursor(1920, -40)
        self.assertEqual(sent, [view.warp_cursor_lua(1920, -40)])

    def test_view_control_takes_no_mouse_wiring(self):
        import inspect
        params = inspect.signature(view.ViewControl).parameters
        for name in ('mouse_binds', 'cursor', 'warp'):
            self.assertNotIn(name, params)

    def test_a_stale_menu_geometry_status_line_is_ignored(self):
        from tools.native_spike.supervisor import Supervisor
        control = mock.Mock(spec=['set_overview_geometry'])
        sup = Supervisor(mock.Mock(), lambda: None, control=control)
        sup.status_bytes(b'{"presented_hz":120}\n')
        sup.status_bytes(b'{"menu_geometry":{"w":1229,"h":733,"tabs":[],"sliders":[]}}\n')
        self.assertEqual(sup.status, {'presented_hz': 120})  # neither run status nor routed anywhere
        control.set_overview_geometry.assert_not_called()


class WireTests(unittest.TestCase):
    def test_placement_menu_now_means_the_expanded_pill(self):
        s = view.ViewState(NAMES)
        s.open_menu(2)
        frame = json.loads(view.placement_menu_frame('run', 's', 4, s.menu()))
        self.assertEqual(frame['op'], 'placement_menu')
        # No pointer; since desktops change in XR, also the panel's desktop row (desktops.py).
        self.assertEqual(set(frame['payload']), {'open', 'desktop', 'field', 'placements', 'names', 'wide', 'armed', 'busy'})
        self.assertEqual((frame['payload']['open'], frame['payload']['desktop']), (True, 2))

    def test_move_handle_drives_the_pills(self):
        frame = json.loads(resize.pill_frame('run', 's', 4, {'visible': True, 'desktop': 1, 'hover': True, 'active': False}))
        self.assertEqual(frame['op'], 'move_handle')
        for bad in ({'visible': True, 'desktop': 4, 'hover': False, 'active': False},
                    {'visible': True, 'desktop': -1, 'hover': False, 'active': False},
                    {'visible': 1, 'desktop': 0, 'hover': False, 'active': False},
                    {'visible': True, 'desktop': 0, 'hover': False}):
            with self.subTest(bad), self.assertRaises(ValueError): resize.pill_frame('run', 's', 4, bad)

    def test_a_fourth_desktop_gets_its_resize_handle_and_pill(self):
        # 2026-11-03: a run with XR-4 (the pill's Add) ended with 'invalid resize handle' when the pointer reached
        # that desktop's corner; both frames capped the index at 2. The renderer takes up to kMaxDesktops - 1.
        from tools.native_spike.geometry import MAX_DESKTOPS
        last = MAX_DESKTOPS - 1
        self.assertEqual(last, 3)
        for corner in resize.CORNERS:
            frame = json.loads(resize.handle_frame('run', 's', 5, {'visible': True, 'desktop': last, 'corner': corner, 'active': True}))
            self.assertEqual((frame['op'], frame['payload']['desktop'], frame['payload']['corner']), ('resize_handle', last, corner))
        frame = json.loads(resize.pill_frame('run', 's', 6, {'visible': True, 'desktop': last, 'hover': True, 'active': True}))
        self.assertEqual((frame['op'], frame['payload']['desktop']), ('move_handle', last))
        for bad in (MAX_DESKTOPS, -1, True):
            with self.subTest(bad), self.assertRaises(ValueError):
                resize.handle_frame('run', 's', 5, {'visible': True, 'desktop': bad, 'corner': 'br', 'active': False})

    def test_the_panel_footer_names_bound_keys(self):
        import re
        src = (ROOT / 'spike/src/glass.cpp').read_text()
        ascii_hint = re.search(r'glass_panel_hint\(bool ascii\) \{\s*return ascii \? "([^"]+)"', src).group(1)
        # "UP/DN FIELD  L/R ADJUST  TAB  R FLAT": each part's keys and what they do
        expected = {'UP/DN FIELD': (('up', 'menu_field_prev'), ('down', 'menu_field_next')),
                    'L/R ADJUST': (('left', 'menu_dec'), ('right', 'menu_inc')),
                    'TAB': (('Tab', 'menu_desktop_next'),), 'R FLAT': (('R', 'menu_reset'),)}
        parts = [part.strip() for part in ascii_hint.split('  ') if part.strip()]
        self.assertEqual(parts, list(expected))
        for part, binds in expected.items():
            for bind in binds:
                with self.subTest(part=part, bind=bind): self.assertIn(bind, view.MENU_BINDS)


class HelpCardTextTests(unittest.TestCase):
    def test_the_rows_name_the_pills_and_stay_short(self):
        self.assertEqual(view.HELP_ACTIONS['menu_toggle'], 'Placement pill of the selected desktop')
        self.assertEqual(view.HELP_ACTIONS['zoom_out'], 'Overview: free mouse, pills, corners')
        self.assertLessEqual(max(len(a) for a in view.HELP_ACTIONS.values()), 42)  # the card's width is another thread's
        rows = dict(view.help_rows())
        self.assertEqual(rows['Ctrl+Alt+L'], 'Placement pill of the selected desktop')
        self.assertNotIn('Ctrl+Alt+M', ' '.join(k + ' ' + a for k, a in view.help_rows()))
        self.assertFalse(any('menu' in a.lower() or 'bars' in a.lower() for _, a in view.help_rows()))

    def test_the_settings_panels_placement_hint_names_the_pill_under_the_desktop(self):
        # The panel's Place hint (PlacementEditor.qml) was the last user-facing text describing the old bar above.
        qml = (ROOT / 'omarchy-plugin/cody.viture-workspace/PlacementEditor.qml').read_text()
        self.assertIn("dragging the glass pill under a desktop in the glasses' overview sets them", qml)
        self.assertNotIn('bar above', qml)
        self.assertIn("dragging a desktop's corner in the glasses' overview sets it too", qml)  # still true


class BindCheckTests(unittest.TestCase):
    """tools/native_spike/bind_check.py: the run's chords against a desktop config, through Hyprland's own loader."""
    def test_normalize(self):
        self.assertEqual(bind_check.normalize('CTRL + ALT + L'), bind_check.normalize('alt+control+l'))
        self.assertEqual(bind_check.normalize('SUPER + mouse:272'), (frozenset({'SUPER'}), 'mouse:272'))
        self.assertNotEqual(bind_check.normalize('mouse:272'), bind_check.normalize('SUPER + mouse:272'))

    def test_the_run_chords_are_the_pills_keys_and_the_free_pointers_button(self):
        chords = bind_check.run_chords()
        flat = [c for _, c, _ in chords]
        self.assertNotIn('CTRL + ALT + M', flat)
        self.assertFalse(any(op.startswith('menu_mouse') for _, _, op in chords))
        self.assertEqual({w for w, _, _ in chords}, {'run', 'send', 'menu', 'pointer', 'lock'})  # lock: test_input_lock.py
        self.assertEqual([(c, op) for w, c, op in chords if w == 'pointer'],
                         [('mouse:272', 'resize_press'), ('mouse:272', 'resize_release')])
        self.assertEqual([(c, op) for w, c, op in chords if w == 'menu'], list(view.MENU_BINDS))
        self.assertEqual(bind_check.clashes([]), [])  # press and release on one button are one bind, not a clash

    def test_clashes_from_records(self):
        rec = [('desktop', 'bind', 'SUPER + M'), ('desktop', 'bind', 'CTRL + ALT + L'), ('desktop', 'unbind', 'CTRL + ALT + L'),
               ('desktop', 'bind', 'CTRL + ALT + M')]  # Ctrl+Alt+M is free again: no longer ours
        self.assertEqual(bind_check.clashes(rec), [])
        problems = bind_check.clashes(rec + [('desktop', 'bind', 'ALT + CTRL + l')])
        self.assertEqual(len(problems), 1); self.assertIn('menu_toggle', problems[0])
        problems = bind_check.clashes([('desktop', 'bind', 'mouse:272')])
        self.assertEqual(len(problems), 2); self.assertIn('resize_press', ' '.join(problems))
        with mock.patch.object(view, 'MENU_BINDS', view.MENU_BINDS + (('CTRL + ALT + H', 'menu_reset'),)):
            self.assertIn('bound twice', ' '.join(bind_check.clashes([])))  # Ctrl+Alt+H is help during a run

    def test_the_harness_loads_the_free_pointers_binds_not_mouse_mode(self):
        lua = bind_check.harness_lua('/tmp/x/hyprland.lua', '/tmp/x/out.tsv')
        self.assertIn(resize.install_resize_binds_lua(*bind_check.DUMMY_ARGS), lua)
        self.assertIn(view.install_menu_binds_lua(*bind_check.DUMMY_ARGS), lua)
        self.assertNotIn('menu_mouse', lua)
        self.assertNotIn('xr_workspace_mouse_binds = {', lua)

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_finds_a_clash_and_loads_our_lua(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            cfg = hypr / 'hyprland.lua'
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\nhl.bind("CTRL + ALT + M", hl.dsp.exec_cmd("true"))\n'
                           'local t = hl.timer(function() end, { timeout = 1000, type = "repeat" })\n')  # timers: Hyprland 0.56 exit crash
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 0, lines)  # Ctrl+Alt+M is not ours any more
            self.assertIn('config ok', lines[0])
            self.assertIn("placement panel binds", lines[-1]); self.assertIn("free pointer's left button", lines[-1])
            self.assertNotIn('Ctrl+Alt+M', lines[-1]); self.assertNotIn('mouse-mode', lines[-1])
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\nhl.bind("ALT + CTRL + L", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 1, lines)
            self.assertTrue(any('menu_toggle' in l for l in lines[2:]), lines)
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\nhl.nope()\n')
            self.assertEqual(bind_check.check(cfg, HYPRLAND, home)[0], 2)
            self.assertFalse((Path(home) / '.local').exists() and any((Path(home) / '.local').rglob('*.tsv')))  # nothing left behind

    def test_missing_binary_or_config_cannot_check(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(bind_check.check(Path(home) / 'none.lua', '/bin/true', home)[0], 2)
        with mock.patch.object(shutil, 'which', return_value=None):
            self.assertEqual(bind_check.check(None, None, '/nonexistent')[0], 2)


if __name__ == '__main__':
    unittest.main()
