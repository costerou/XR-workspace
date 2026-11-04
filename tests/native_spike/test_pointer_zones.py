"""The dot is one cursor, and the zone contract says what it does where (anchor-no-click-maximize; Cody, 2026-11-03
2:51 PM; the contract is shared with the sibling branches: the level bar sits above the top edge, resize at edges and corners,
content goes to apps). Most important first, on a desktop's own surface (free_pointer.hit, zone_of, border_handle): an open
panel, a corner, a pill, content, a level bar, an edge; else empty space. The dot's look shows it (look_for, the
`overview_pointer` frame's `look`, drawn by the renderer as marks round the dot: spike/tests/free_pointer_test.cpp): a
resize direction on an edge or corner, move on a pill, rotate on a level bar, plain over content and space.

Checked here:
  * Classification: every zone, its limits and its precedence, on flat, placed, curved, resized and rolled desktops.
  * Looks: on hover, held through a drag even when the dot leaves the zone, gone when hidden, plain in desktop mode.
  * One cursor: leaving a desktop puts the dot just past the edge it reached, on that resize handle, with no jump, and it
    moves at the same speed either side; any move back inside hands the real cursor back under the dot; hand jitter at an
    edge does not flap; a held button on a window draws the dot held, as a handle drag does.
  * Resize drags: each of the four edges (along its normal) and four corners (along the diagonal), the shape kept, saved
    on release, a cap lit for corners only.

Offline only: fake clock and the free-pointer test rig. No compositor, renderer binary or glasses.
"""
import unittest

from tools.native_spike import free_pointer as fp, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import G, GAIN, OUTPUTS, T0, T1, X1, X2, FLAT, frames, last
from test_unified_cursor import UnifiedBase

T2 = G['tiles'][2]
CASES = {
    'flat': {},
    'moved round you': {'x': 300, 'y': -120},
    'curved': {'curvature': 90},
    'bigger': {'distance': 60},
    'smaller': {'distance': 160},
    'rolled': {'roll': 1500},
    'everything': {'x': -250, 'y': 150, 'curvature': 60, 'distance': 140, 'roll': -2500},
}


def placed(case):
    return {**DEFAULT_PLACEMENT, **CASES[case]}


def rim(tile, out):
    """Tile pixels on each edge's middle and each corner, `out` px past the edge (negative: inside): {handle: q}."""
    tx, ty, tw, th = tile
    mx, my = tx + tw / 2, ty + th / 2
    return {'t': (mx + 90, ty - out), 'b': (tx + 40, ty + th + out), 'l': (tx - out, my), 'r': (tx + tw + out, my),
            'tl': (tx - out, ty - out), 'tr': (tx + tw + out, ty - out), 'bl': (tx - out, ty + th + out), 'br': (tx + tw + out, ty + th + out)}


def on_bar(tile, segment):
    """A tile point in the middle of a segment of the desktop's level bar."""
    x, y, w, h = fp.level_bar_rect(tile)
    return (x + w * (fp.LEVEL_SEGMENTS.index(segment) + 0.5) / 3, y + h / 2)


class BorderTests(unittest.TestCase):
    def test_edges_and_corners_are_named_and_end_where_the_contract_says(self):
        for handle, q in rim(T1, 4).items():
            with self.subTest(handle=handle):
                self.assertEqual(fp.border_handle(T1, q), ('corner' if len(handle) == 2 else 'edge', handle))

    def test_the_border_runs_from_edge_out_px_outside_to_enter_px_inside(self):
        tx, ty, tw, th = T1
        y = ty + th / 2
        self.assertEqual(fp.border_handle(T1, (tx - fp.EDGE_OUT_PX, y)), ('edge', 'l'))
        self.assertIsNone(fp.border_handle(T1, (tx - fp.EDGE_OUT_PX - 0.01, y)))  # further out: nothing of this desktop's
        self.assertEqual(fp.border_handle(T1, (tx + fp.ENTER_PX - 0.01, y)), ('edge', 'l'))
        self.assertIsNone(fp.border_handle(T1, (tx + fp.ENTER_PX, y)))  # further in: content

    def test_a_corner_is_its_square_but_never_deeper_than_the_border(self):
        tx, ty, tw, th = T1
        z = fp.corner_zone(T1)
        self.assertEqual(fp.border_handle(T1, (tx - z + 0.01, ty - z + 0.01)), ('corner', 'tl'))  # its whole outer square
        self.assertIsNone(fp.border_handle(T1, (tx - z - 0.01, ty - z + 0.01)))
        self.assertEqual(fp.border_handle(T1, (tx + z - 0.01, ty)), ('corner', 'tl'))  # along the edge, in the border
        self.assertEqual(fp.border_handle(T1, (tx + z + 0.01, ty)), ('edge', 't'))
        self.assertIsNone(fp.border_handle(T1, (tx + z / 2, ty + z / 2)))           # inside the square, deeper: content

    def test_a_corner_is_never_smaller_than_the_edges_band(self):
        self.assertGreaterEqual(fp.CORNER_MIN, fp.EDGE_OUT_PX)  # so the band always ends in a corner square, no gap


class ZoneTests(unittest.TestCase):
    ICONS = ['bar'] * 3

    def test_zone_names_for_every_hit(self):
        for h, zone in ((None, 'space'), (('desktop', 1, (0, 0)), 'content'), (('corner', 0, 'tl'), 'resize'), (('edge', 2, 'r'), 'resize'),
                        (('pill', 1), 'move'), (('roll', 0, 'cw'), 'rotate'), (('slider', 0, 'x', 1.0), 'control'), (('panel', 0), 'control'),
                        (('action', 0, 'add'), 'control'), (('scale', 0, 100), 'control'), (('scale_box', 0), 'control'),
                        (('help', 'card'), 'control'), (('help', 'next'), 'control')):
            with self.subTest(hit=h):
                self.assertEqual(fp.zone_of(h), zone)
                self.assertIn(zone, fp.ZONES)

    def test_every_look_name_is_a_look_and_resize_names_its_direction(self):
        self.assertEqual({fp.look_for(('edge', 0, e)) for e in 'lr'}, {'resize_ew'})
        self.assertEqual({fp.look_for(('edge', 0, e)) for e in 'tb'}, {'resize_ns'})
        self.assertEqual({fp.look_for(('corner', 0, c)) for c in ('tl', 'br')}, {'resize_nwse'})
        self.assertEqual({fp.look_for(('corner', 0, c)) for c in ('tr', 'bl')}, {'resize_nesw'})
        self.assertEqual(fp.look_for(('pill', 0)), 'move')
        self.assertEqual(fp.look_for(('roll', 0, 'ccw')), 'rotate')
        for plain in (None, ('desktop', 0, (0, 0)), ('slider', 0, 'x', 0.0), ('panel', 0), ('help', 'card')):
            self.assertIsNone(fp.look_for(plain))
        self.assertLessEqual(set(fp.RESIZE_LOOKS.values()), set(fp.POINTER_LOOKS))

    def test_each_zone_on_every_placement(self):
        g = dict(G, tiles=[T1])  # one desktop: nothing in front of it
        for case in CASES:
            p = placed(case)
            for handle, q in rim(T1, 3).items():
                with self.subTest(case=case, handle=handle):
                    h = fp.hit(g, [p], fp.project(g, T1, p, *q))
                    self.assertEqual(h, ('corner' if len(handle) == 2 else 'edge', 0, handle))
                    self.assertEqual(fp.zone_of(h), 'resize')
            tx, ty, tw, th = T1
            for q in ((tx + tw * 0.5, ty + th * 0.5), (tx + 3, ty + th / 2), (tx + 2, ty + 2), (tx + tw - 2, ty + th - 2)):
                with self.subTest(case=case, content=q):
                    self.assertEqual(fp.zone_of(fp.hit(g, [p], fp.project(g, T1, p, *q))), 'content')
            with self.subTest(case=case, space=True):
                far = fp.project(g, T1, p, tx + tw / 2, ty - fp.corner_zone(T1) - 12)
                self.assertEqual(fp.zone_of(fp.hit(g, [p], far)), 'space')

    def test_a_corner_wins_over_the_level_bar(self):
        # On a desktop as narrow as the bar, the bar's ends reach over the corners' zones: the corner keeps them.
        tile = (900.0, 500.0, 120.0, 68.0)
        g = {**G, 'tiles': [tile]}
        x, y, w, h = fp.level_bar_rect(tile)
        q = (x - 1, y + h + 2 * fp.level_bar_scale(tile))  # the bar's padding, past its left end, under its bottom edge
        self.assertEqual(fp.level_bar_segment(tile, q), 'ccw')
        self.assertEqual(fp.border_handle(tile, q), ('corner', 'tl'))
        placements = [DEFAULT_PLACEMENT]
        self.assertEqual(fp.hit(g, placements, fp.project(g, tile, DEFAULT_PLACEMENT, *q), icons=['bar']), ('corner', 0, 'tl'))

    def test_the_level_bar_takes_an_edges_border_from_its_clear_band_out(self):
        mid = on_bar(T1, 'level')[0]
        top = T1[1]
        for out, want in ((fp.LEVEL_EDGE_CLEAR - 2, ('edge', 1, 't')), (fp.LEVEL_EDGE_CLEAR + 1, ('edge', 1, 't')),  # short of its padding
                          (fp.LEVEL_BAR_GAP - fp.LEVEL_BAR_PAD + 1, ('roll', 1, 'level')), (fp.EDGE_OUT_PX - 0.5, ('roll', 1, 'level'))):
            with self.subTest(out=out):
                pointer = fp.project(G, T1, DEFAULT_PLACEMENT, mid, top - out * fp.level_bar_scale(T1))
                self.assertEqual(fp.hit(G, FLAT, pointer)[:3], ('edge', 1, 't'))  # with no bars shown
                self.assertEqual(fp.hit(G, FLAT, pointer, icons=self.ICONS), want)

    def test_a_corner_wins_over_a_pill_and_a_pill_over_an_edge(self):
        x, y, w, h = fp.pill_rect(T1)
        mid = x + w / 2
        under = fp.project(G, T1, DEFAULT_PLACEMENT, mid, T1[1] + T1[3] + 3)  # just under the bottom edge, over the pill
        self.assertEqual(fp.border_handle(T1, (mid, T1[1] + T1[3] + 3)), ('edge', 'b'))
        self.assertEqual(fp.hit(G, FLAT, under), ('pill', 1))
        beside = fp.project(G, T1, DEFAULT_PLACEMENT, x - fp.PILL_PAD_X - 20, T1[1] + T1[3] + 3)  # along the edge, past the pill
        self.assertEqual(fp.hit(G, FLAT, beside), ('edge', 1, 'b'))
        tile = (G['w'] / 2 - 30, G['h'] / 2 - 20, 60.0, 40.0)  # so small its pill zone reaches a corner's square
        g = dict(G, tiles=[tile])
        self.assertEqual(fp.hit(g, FLAT[:1], fp.project(g, tile, FLAT[0], tile[0] + 2, tile[1] + 43))[0], 'corner')

    def test_an_open_panel_wins_over_a_neighbours_zones(self):
        # XR-2 lifted over... its neighbour's content under XR-1's open panel: the panel is drawn on top.
        x, y, w, h = fp.panel_rect(T1)
        placements = [dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT)]
        point = fp.project_glass(G, T1, placements[1], x + 240, y + 100)
        self.assertEqual(fp.zone_of(fp.hit(G, placements, point, 1)), 'control')
        header = fp.project_glass(G, T1, placements[1], x + 240, y + 10)
        self.assertEqual(fp.zone_of(fp.hit(G, placements, header, 1)), 'move')  # its header strip is the pill


class Base(UnifiedBase):
    def dot_to(self, point, ops=None):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1], ops=ops)

    def onto(self, q, i=1):
        return self.dot_to(fp.project(G, G['tiles'][i], self.p._placement(i), *q))


class LookTests(Base):
    def looks(self, out):
        frame = last(out, 'overview_pointer')
        return frame is not None, None if frame is None else frame.get('look')

    def test_the_look_follows_what_the_dot_is_over(self):
        self.free()
        for q, look in (((T1[0] + T1[2] + 4, T1[1] + 100), 'resize_ew'), ((T1[0] - 4, T1[1] + 100), 'resize_ew'),
                        ((T1[0] + 100, T1[1] - 4), 'resize_ns'), ((T1[0] + 100, T1[1] + T1[3] + 4), 'resize_ns'),
                        ((T1[0] - 4, T1[1] - 4), 'resize_nwse'), ((T1[0] + T1[2] + 4, T1[1] + T1[3] + 4), 'resize_nwse'),
                        ((T1[0] + T1[2] + 4, T1[1] - 4), 'resize_nesw'), ((T1[0] - 4, T1[1] + T1[3] + 4), 'resize_nesw')):
            with self.subTest(q=q):
                out = self.onto(q)
                self.assertEqual(self.p.mode, 'free')
                self.assertEqual(self.looks(out), (True, look))

    def test_pill_level_bar_and_empty_space(self):
        self.free()
        x, y, w, h = fp.pill_rect(T1)
        out = self.dot_to(fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + w / 2, y + h / 2))
        self.assertEqual(self.looks(out), (True, 'move'))
        out = self.onto(on_bar(T1, 'ccw'))
        self.assertEqual(self.looks(out), (True, 'rotate'))
        out = self.dot_to((T1[0] + 100, T1[1] - 120))  # above everything
        self.assertEqual(self.p.hover, None)
        self.assertEqual(self.looks(out), (True, None))  # the plain dot

    def test_over_content_the_dot_is_plain_on_every_desktop(self):
        self.free()
        self.onto((T1[0] + T1[2] + 4, T1[1] + 100))
        out = self.onto((T1[0] + T1[2] / 2, T1[1] + T1[3] / 2))
        self.assertEqual(self.p.mode, 'desktop')
        self.assertNotIn('look', last(out, 'overview_pointer'))
        self.assertNotIn('look', self.p.sent)
        self.at(*self.p.want[2])
        self.assertNotIn('look', self.p.sent)

    def test_open_panel_controls_are_plain_and_its_header_is_move(self):
        self.control.pill_click(1)
        self.free()
        x, y, w, h = fp.panel_rect(T1)
        out = self.dot_to(fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + 240, y + 100))
        self.assertEqual(fp.zone_of(self.p.hover), 'control')
        self.assertEqual(self.looks(out), (True, None))
        out = self.dot_to(fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + 240, y + 10))
        self.assertEqual(self.looks(out), (True, 'move'))

    def test_the_look_is_held_through_a_drag_even_past_the_zone(self):
        self.free()
        self.onto((T1[0] + T1[2] + 4, T1[1] + 100))
        self.op('resize_press')
        self.assertEqual(self.p.sent['look'], 'resize_ew'); self.assertTrue(self.p.sent['held'])
        out = self.move_canvas(0, -300)  # far off the edge: still the resize it started as
        self.assertEqual(self.looks(out), (True, 'resize_ew'))
        self.op('resize_release')
        self.assertNotIn('look', self.p.sent)  # up there, empty space again
        x, y, w, h = fp.pill_rect(T1)
        self.dot_to(fp.project_glass(G, T1, self.p._placement(1), x + w / 2, y + h / 2))
        self.op('resize_press')
        self.move_canvas(0, 200)
        self.assertEqual(self.p.sent['look'], 'move')
        self.op('resize_release')

    def test_a_roll_drag_keeps_the_rotate_look(self):
        self.free()
        self.onto(on_bar(T1, 'cw'))
        self.op('resize_press')
        self.move_canvas(0, 90)
        self.assertEqual(self.p.sent['look'], 'rotate')
        self.op('resize_release')

    def test_the_hidden_dot_has_no_look(self):
        self.free()
        self.onto((T1[0] + T1[2] + 4, T1[1] + 100))
        self.assertIn('look', self.p.sent)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        out = frames(self.control.poll())
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', last(out, 'overview_pointer'))

    def test_the_frame_refuses_an_unknown_look(self):
        base = {'visible': True, 'x': 1, 'y': 2, 'held': False}
        for look in fp.POINTER_LOOKS: fp.overview_pointer_frame('run', 'session', 1, {**base, 'look': look})
        for bad in ('plain', '', None, 3, 'MOVE'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fp.overview_pointer_frame('run', 'session', 1, {**base, 'look': bad})


class OneCursorTests(Base):
    def test_leaving_a_desktop_lands_on_that_edges_handle_with_no_jump(self):
        for edge, cursor in (('t', (X1 + 900, 0)), ('b', (X1 + 300, 1079)), ('l', (X1, 500)), ('r', (X1 + 1919, 500))):
            with self.subTest(edge=edge):
                self.setUp()
                self.at(cursor[0] + (40 if edge == 'l' else -40 if edge == 'r' else 0), cursor[1] + (40 if edge == 't' else -40 if edge == 'b' else 0))
                before = self.p.pointer
                out = self.at(*cursor)
                self.assertEqual((self.p.mode, self.p.hover[:3]), ('free', ('edge', 0, edge)))
                self.assertEqual(last(out, 'overview_pointer')['look'], fp.RESIZE_LOOKS[edge])
                moved = abs(self.p.pointer[0] - before[0]) + abs(self.p.pointer[1] - before[1])
                self.assertLess(moved, (40 + fp.EXIT_PX + 2) * GAIN * 1.2)  # the hand's 40 px, and the few px past the edge

    def test_a_corner_pixel_leaves_onto_the_corner(self):
        self.at(X1 + 1900, 20)
        out = self.at(X1 + 1919, 0)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.assertEqual(last(out, 'overview_pointer')['look'], 'resize_nesw')

    def test_the_dot_moves_at_the_same_speed_either_side_of_the_edge(self):
        self.at(X1 + 900, 40)
        a = self.p.pointer
        self.at(X1 + 900, 20)  # 20 hand px on the desktop
        inside = abs(self.p.pointer[1] - a[1])
        self.at(X1 + 900, 0)
        self.assertEqual(self.p.mode, 'free')
        b = self.p.pointer
        self.move(0, -20)  # 20 hand px past it
        outside = abs(self.p.pointer[1] - b[1])
        self.assertAlmostEqual(inside, outside, delta=0.05 * inside)

    def test_any_move_back_inside_hands_the_real_cursor_back_under_the_dot(self):
        self.at(X1 + 900, 40); self.at(X1 + 900, 0)
        self.assertEqual(self.p.hover[0], 'edge')
        self.move(0, 2)  # still on the edge's outer side (EXIT_PX past it)
        self.assertEqual(self.p.mode, 'free')
        self.move(0, 4)  # past the ENTER_PX sliver
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 0))
        self.assertLessEqual(self.p.want[2][1], fp.INSET_PX + 2)  # right at the top of the desktop, under the dot
        self.assertEqual(self.p.want[1], 'desktop')

    def test_hand_jitter_at_an_edge_never_flaps(self):
        self.at(X1 + 900, 40); self.at(X1 + 900, 0)
        serial = self.p.want[0]
        for dy in (1, -1, 1, -1, 1, -1):  # a shaky hand on the handle
            self.move(0, dy)
        self.assertEqual((self.p.mode, self.p.want[0]), ('free', serial))
        self.at(*self.cur)
        self.assertEqual(self.p.mode, 'free')

    def test_a_held_button_on_a_window_draws_the_dot_held(self):
        self.at(X1 + 600, 400)
        self.assertFalse(self.p.sent['held'])
        out = self.op('resize_press')
        self.assertTrue(last(out, 'overview_pointer')['held'])
        out = self.op('resize_release')
        self.assertFalse(last(out, 'overview_pointer')['held'])

    def test_the_frame_has_the_same_shape_in_both_modes(self):
        self.at(X1 + 600, 400)
        over_content = set(self.p.sent)
        self.at(X1 + 600, 40); self.at(X1 + 600, 0)
        self.move(0, -220)  # clear the new top maximize capsule: free, plain
        self.assertIsNone(self.p.hover)
        self.assertEqual(set(self.p.sent), over_content)


class ResizeDragTests(Base):
    def test_resize_distance_along_an_edges_normal(self):
        w, h = T0[2], T0[3]
        for edge, (sx, sy) in (('l', (-1, 0)), ('r', (1, 0)), ('t', (0, -1)), ('b', (0, 1))):
            with self.subTest(edge=edge):
                self.assertEqual(fp.resize_distance(100, edge, 0, 0, T0), 100)
                self.assertEqual(fp.resize_distance(100, edge, sx * w / 2, sy * h / 2, T0), 50)     # twice as far out
                self.assertEqual(fp.resize_distance(100, edge, -sx * w / 6, -sy * h / 6, T0), 150)  # inward: smaller
                self.assertEqual(fp.resize_distance(200, edge, sx * w / 4, sy * h / 4, T0), 100)    # from half size
                self.assertEqual(fp.resize_distance(100, edge, sy * 500, sx * 500, T0), 100)         # along the edge: nothing
        lo, hi = fp.PLACEMENT_BOUNDS['distance']
        self.assertEqual(fp.resize_distance(100, 'r', 1e6, 0, T0), lo)
        self.assertEqual(fp.resize_distance(100, 'r', -1e6, 0, T0), hi)
        with self.assertRaises(ValueError): fp.resize_distance(100, 'x', 0, 0, T0)

    def test_each_edge_and_corner_drag_resizes_about_the_centre_and_keeps_the_shape(self):
        for handle, q in rim(T1, 3).items():
            with self.subTest(handle=handle):
                self.setUp()
                self.free()
                self.onto(q)
                self.assertEqual(self.p.hover[2], handle)
                self.op('resize_press')
                self.assertEqual((self.p.drag['kind'], self.p.drag['handle']), ('resize', handle))
                ox = {'l': -1, 'r': 1}.get(handle[-1], 0) * T1[2] / 4
                oy = {'t': -1, 'b': 1}.get(handle[0], 0) * T1[3] / 4
                self.move_canvas(ox, oy)  # outward by a quarter of the size on each axis it moves
                self.assertEqual(self.control.state.placements[1]['distance'], 67)  # half again as big
                before = dict(self.control.state.placements[1])
                self.assertEqual({k: v for k, v in before.items() if k != 'distance'},
                                 {k: v for k, v in DEFAULT_PLACEMENT.items() if k != 'distance'})  # only the size
                self.op('resize_release')
                self.assertEqual(self.store.saved[1], before)  # saved on release
                self.assertIsNone(self.p.drag)
                self.assertEqual((self.control.state.selected, self.control.state.zoomed), (0, False))

    def test_an_edge_follows_the_dot(self):
        self.free()
        self.onto((T1[0] + T1[2] + 3, T1[1] + 100))
        self.op('resize_press')
        self.move_canvas(40, 0)
        d = self.control.state.placements[1]['distance']
        self.op('resize_release')
        half = T1[2] / 2 * 100 / d  # the right edge, as drawn, from the desktop's centre
        centre = T1[0] + T1[2] / 2
        self.assertLessEqual(abs(centre + half - (T1[0] + T1[2] + 3 + 40)), T1[2] * 0.02 + 3)

    def test_a_drag_that_ends_over_content_hands_the_real_cursor_back_at_once(self):
        self.free()
        self.onto((T1[0] + T1[2] + 3, T1[1] + 100))
        self.op('resize_press')
        self.move_canvas(-T1[2] * 0.35, 0)  # the right edge inward, past the smallest size: the dot ends on what is left
        self.assertEqual(self.control.state.placements[1]['distance'], fp.PLACEMENT_BOUNDS['distance'][1])
        self.assertEqual(self.p.mode, 'free')
        self.op('resize_release')
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))
        self.assertEqual(self.p.want[1], 'desktop'); self.assertEqual(self.p._at(self.p.want[2])[0], 1)

    def test_a_click_on_a_handle_resizes_nothing(self):
        for q in rim(T1, 3).values():
            self.setUp()
            self.free()
            self.onto(q)
            out = self.op('resize_press') + self.op('resize_release')
            self.assertFalse(any(f['op'] in ('placement', 'view') for f in out))
            self.assertEqual(self.control.state.placements[1], DEFAULT_PLACEMENT)

    def test_a_cap_lights_for_a_corner_and_not_for_an_edge(self):
        self.free()
        out = self.onto((T1[0] + T1[2] + 3, T1[1] + 100))  # an edge
        self.assertFalse(self.p.shown['visible'])
        self.op('resize_press')
        self.assertFalse(self.p.shown['visible'])  # nor while dragging it: the dot's look is its affordance
        self.op('resize_release')
        self.onto((T1[0] + T1[2] + 3, T1[1] - 3))  # a corner
        self.assertEqual((self.p.shown['visible'], self.p.shown['desktop'], self.p.shown['corner'], self.p.shown['active']), (True, 1, 'tr', False))
        self.op('resize_press')
        self.assertTrue(self.p.shown['active'])
        self.op('resize_release')

    def test_a_resize_on_an_open_panels_own_desktop_keeps_the_panel_open(self):
        self.control.pill_click(1)
        self.free()
        self.onto((T1[0] + T1[2] + 3, T1[1] + 100))
        self.op('resize_press'); self.move_canvas(30, 0); self.op('resize_release')
        self.assertTrue(self.control.state.menu_open)
        self.assertLess(self.control.state.placements[1]['distance'], 100)

    def test_ctrl_alt_up_ends_a_resize_and_saves(self):
        self.free()
        self.onto((T1[0] + T1[2] + 3, T1[1] + 100))
        self.op('resize_press'); self.move_canvas(30, 0)
        self.op('zoom_in')
        self.control.poll()
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.store.saved[1], self.control.state.placements[1])


if __name__ == '__main__':
    unittest.main()
