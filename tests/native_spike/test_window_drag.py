"""Overview window drag (free_pointer.py: Window drag; unified_cursor.py over home; send.py window_at / WindowSender.drop):
press and hold on a window on a desktop, then drag, moves that window to another desktop; a quick click still zooms in.

Regression (overview-window-drag, 2026-11-03): before it, a press on a desktop in the overview zoomed into it at once
(OverviewPointer.on_op called _click_desktop on resize_press), so there was no way to hold a press on a window and the
overview could not move windows between desktops at all. Now a desktop press waits: released quickly (no HOLD_S hold,
no WINDOW_DRAG_PX of motion) it is the same click and zooms in; held or dragged, it lifts the window under it, shows its
ghost round the dot and frames the desktop under the dot, and a release over another desktop moves the window there
(one Hyprland move, follow off) without zooming in or selecting anything.

Offline only: fake clock, the free-pointer test rig, a fake window sender (and for send.py a fake Hyprland client).
No compositor, renderer binary or glasses.
"""
import threading
import unittest

from tools.native_spike import free_pointer as fp, send, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import G, OUTPUTS, T0, T1, X1, X2, X3, frames, last
from test_unified_cursor import UnifiedBase

T2 = G['tiles'][2]
WINDOW = {'address': '0xabc', 'w': 960, 'h': 540, 'pinned': False}


def centre(tile):
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)


class FakeSender:
    """send.WindowSender's loop-facing side: lookups answered at once (or held back), drops recorded."""
    def __init__(self, window=WINDOW, answer=True):
        self.window, self.answer = window, answer
        self.lookups, self.drops, self.pending = [], [], []

    def lookup(self, name, point, done):
        self.lookups.append((name, point))
        if self.answer: done(self.window)
        else: self.pending.append(done)

    def drop(self, window, source, point, target):
        self.drops.append((window, source, point, target))


class DragBase(UnifiedBase):
    def setUp(self):
        super().setUp()
        self.sender = self.control.sender = FakeSender()

    def tick(self, dt):
        self.clock.now += dt
        return frames(self.control.poll())

    def hold(self):
        """Keep the button down, the mouse still, past HOLD_S."""
        return self.tick(fp.HOLD_S + 0.01)

    def free_on(self, i, q=None):
        """Free dot onto desktop i (another desktop than home XR-1: the dot only) at tile pixel q (default its centre)."""
        self.free()
        self.aim(i, q or centre(G['tiles'][i]))
        self.assertEqual((self.p.mode, self.p.hover[:2]), ('free', ('desktop', i)))

    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])

    def onto(self, i, q=None):
        tile = G['tiles'][i]
        return self.dot_to(fp.project(G, tile, self.control.state.placements[i], *(q or centre(tile))))


class QuickClickTests(DragBase):
    def test_a_quick_click_on_another_desktop_still_zooms_into_it(self):
        self.free_on(1)
        out = self.op('resize_press')
        self.assertIsNone(last(out, 'view'))  # nothing yet: a hold would lift the window instead
        self.tick(fp.HOLD_S / 2)
        out = self.op('resize_release')
        self.assertTrue(last(out, 'view')['zoomed']); self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertEqual((self.sender.lookups, self.sender.drops, self.p.window_lifts), ([], [], 0))

    def test_a_click_with_a_little_jitter_still_zooms(self):
        self.free_on(1)
        self.op('resize_press')
        self.move_canvas(fp.WINDOW_DRAG_PX * 0.7, 0)  # past the pill's CLICK_PX, short of a window drag
        out = self.op('resize_release')
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertEqual(self.p.window_lifts, 0)

    def test_a_quick_click_on_home_still_reaches_the_window_and_zooms(self):
        self.at(X1 + 600, 400)
        self.op('resize_press')
        self.tick(fp.HOLD_S / 2)
        self.assertEqual(self.p.mode, 'desktop')
        out = self.op('resize_release')
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertEqual(self.sender.lookups, [])


class FreeDragTests(DragBase):
    def test_hold_drag_and_release_on_another_desktop_moves_the_window_there(self):
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.5)
        self.free_on(1, q)
        self.op('resize_press')
        out = self.hold()
        self.assertEqual(self.p.window_lifts, 1)
        self.assertEqual(self.sender.lookups, [('XR-2', (X2 + 480, 540))])  # the window shown at the press
        ghost = last(out, 'overview_pointer')['drag']
        self.assertEqual(ghost['target'], -1)  # still over its own desktop: nothing framed
        self.assertEqual((ghost['w'], ghost['h']), fp.ghost_size(WINDOW, OUTPUTS[1], T1, DEFAULT_PLACEMENT))
        self.assertTrue(last(out, 'overview_pointer')['held'])
        out = self.onto(2)
        self.assertEqual(last(out, 'overview_pointer')['drag']['target'], 2)  # XR-3 framed
        self.assertEqual(self.control.state.selected, 0)  # the dot over XR-3 selects nothing
        out = self.op('resize_release')
        self.assertEqual(self.sender.drops, [(WINDOW, 'XR-2', (X2 + 480, 540), 'XR-3')])
        self.assertIsNone(last(out, 'view'))
        self.assertFalse(self.control.state.zoomed)  # no zoom
        self.assertEqual(self.control.state.selected, 0)  # nor a selection
        self.assertEqual(self.p.mode, 'free'); self.assertIsNone(self.p.drag)
        self.assertNotIn('drag', last(out, 'overview_pointer'))  # the ghost and the frame are gone
        self.assertEqual(self.p.window_drops, 1)

    def test_dragging_past_the_threshold_lifts_without_waiting_for_the_hold(self):
        self.free_on(1)
        self.op('resize_press')
        out = self.move_canvas(fp.WINDOW_DRAG_PX + 3, 0)
        self.assertEqual(self.p.window_lifts, 1)
        self.assertIn('drag', last(out, 'overview_pointer'))
        self.onto(2)
        self.op('resize_release')
        self.assertEqual([d[3] for d in self.sender.drops], ['XR-3'])

    def test_dropping_on_home_moves_it_there_and_the_dot_stays_free(self):
        self.free_on(2)
        self.op('resize_press'); self.hold()
        self.onto(0)
        self.assertEqual(self.p.mode, 'free')  # never hands back into home mid-drag
        self.op('resize_release')
        self.assertEqual([(d[1], d[3]) for d in self.sender.drops], [('XR-3', 'XR-1')])
        self.assertFalse(self.control.state.zoomed)

    def test_releasing_outside_any_desktop_cancels(self):
        self.free_on(1)
        self.op('resize_press'); self.hold()
        out = self.dot_to((T1[0] + T1[2] / 2, T1[1] - 200))  # above the desktops: empty space
        self.assertEqual(last(out, 'overview_pointer')['drag']['target'], -1)
        out = self.op('resize_release')
        self.assertEqual(self.sender.drops, [])
        self.assertFalse(self.control.state.zoomed); self.assertIsNone(last(out, 'view'))
        self.assertEqual(self.p.window_cancels, 1)
        self.assertNotIn('drag', last(out, 'overview_pointer'))

    def test_releasing_over_a_pill_cancels(self):
        self.free_on(1)
        self.op('resize_press'); self.hold()
        x, y, w, h = fp.pill_rect(T2)
        self.dot_to(fp.project_glass(G, T2, DEFAULT_PLACEMENT, x + w / 2, y + h / 2))
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])
        self.assertEqual(self.control.state.placements[2], DEFAULT_PLACEMENT)  # and the pill did not move its desktop

    def test_releasing_on_its_own_desktop_cancels_and_does_not_zoom(self):
        self.free_on(1)
        self.op('resize_press'); self.hold()
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])
        self.assertFalse(self.control.state.zoomed)

    def test_no_window_under_the_press_drags_nothing(self):
        self.sender.window = None  # an empty desktop
        self.free_on(1)
        self.op('resize_press'); self.hold()
        out = self.onto(2)
        self.assertNotIn('drag', last(out, 'overview_pointer'))
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])
        self.assertFalse(self.control.state.zoomed)

    def test_a_pinned_window_drags_nothing(self):
        self.sender.window = dict(WINDOW, pinned=True)
        self.free_on(1)
        self.op('resize_press'); self.hold(); self.onto(2)
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])

    def test_a_release_before_the_lookup_answers_still_moves_what_is_there(self):
        self.sender.answer = False
        self.free_on(1)
        self.op('resize_press'); out = self.hold()
        self.assertEqual(last(out, 'overview_pointer')['drag'][('w')], fp.GHOST_DEFAULT[0])  # a default ghost meanwhile
        self.onto(2)
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [(None, 'XR-2', (X2 + 960, 540), 'XR-3')])  # the sender looks it up itself
        self.sender.pending[0](WINDOW)  # a late answer belongs to no drag now
        self.tick(0.01)
        self.assertIsNone(self.p.drag)

    def test_a_late_answer_never_lands_on_the_next_drag(self):
        self.sender.answer = False
        self.free_on(1)
        self.op('resize_press'); self.hold(); self.op('resize_release')
        late = self.sender.pending.pop()
        self.op('resize_press'); self.hold()
        late(dict(WINDOW, address='0xdead'))
        self.tick(0.01)
        self.assertIsNone(self.p.drag['window'])  # still waiting for its own
        self.sender.pending.pop()(WINDOW)
        self.tick(0.01)
        self.assertEqual(self.p.drag['window'], WINDOW)

    def test_without_a_sender_nothing_is_dragged(self):
        self.control.sender = None
        self.free_on(1)
        self.op('resize_press'); self.hold()
        self.assertNotIn('drag', self.p.sent)
        self.onto(2)
        self.op('resize_release')
        self.assertFalse(self.control.state.zoomed)


class NoConflictTests(DragBase):
    def test_a_pill_drag_still_moves_its_desktop_and_lifts_no_window(self):
        self.at(X1 + 960, 540); self.at(X1 + 960, 1079)
        x, y, w, h = fp.pill_rect(T1)
        self.dot_to(fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + w / 2, y + h / 2))
        self.assertEqual(self.p.hover, ('pill', 1))
        self.op('resize_press'); self.hold()
        self.move_canvas(60, 0)
        self.op('resize_release')
        self.assertNotEqual(self.control.state.placements[1]['x'], DEFAULT_PLACEMENT['x'])
        self.assertEqual((self.sender.lookups, self.p.window_lifts), ([], 0))

    def test_a_corner_drag_still_resizes_and_lifts_no_window(self):
        self.at(X1 + 960, 300); self.at(X1 + 1919, 0)
        self.move_canvas(4, -4)
        self.assertEqual(self.p.hover[0], 'corner')
        self.op('resize_press'); self.hold()
        self.move_canvas(40, -40)
        self.op('resize_release')
        self.assertNotEqual(self.control.state.placements[0]['distance'], DEFAULT_PLACEMENT['distance'])
        self.assertEqual((self.sender.lookups, self.p.window_lifts), ([], 0))

    def test_a_roll_icon_press_lifts_no_window(self):
        self.p.set_roll_geometry([None, 1200, None])  # XR-2 rolled: its icon above a top corner
        self.free_on(1)
        side = fp.roll_side(1200, 0)
        x, y, w, h = fp.roll_icon_rect(T1, side)
        self.dot_to(fp.project(G, T1, DEFAULT_PLACEMENT, x + w / 2, y + h / 2))
        self.assertEqual(self.p.hover[0], 'roll')
        self.op('resize_press'); self.hold(); self.op('resize_release')
        self.assertEqual((self.sender.lookups, self.p.window_lifts), ([], 0))

    def test_the_help_card_takes_its_own_clicks(self):
        self.control.state.selected = 1  # home XR-2, the card over its middle
        self.op('help')
        self.control.set_help_geometry({'card': (560, 300, 800, 600)})
        self.at(X2 + 960, 540)
        self.assertEqual((self.p.mode, self.p.hover), ('free', ('help', 'card')))
        self.op('resize_press'); self.hold(); self.op('resize_release')
        self.assertEqual((self.sender.lookups, self.p.window_lifts), ([], 0))

    def test_idle_ends_a_drag_without_moving_anything(self):
        self.free_on(1)
        self.op('resize_press'); self.hold(); self.onto(2)
        out = self.tick(view.OVERLAY_IDLE_CLOSE_S + 1)
        self.assertIsNone(self.p.drag)
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])


class HomeDragTests(DragBase):
    def test_holding_on_a_window_on_home_lifts_it_and_the_dot_carries_it_to_another_desktop(self):
        self.at(X1 + 600, 400)
        self.assertEqual(self.p.mode, 'desktop')
        self.op('resize_press')
        self.assertEqual(self.p.want[1], 'desktop')  # the press reached the window, as any click there
        out = self.hold()
        self.assertEqual(self.p.mode, 'free')  # the dot turned free where it is: the grab takes the release
        self.assertEqual(self.p.want[1], 'free'); self.assertEqual(self.p._at(self.p.want[2])[0], 0)  # parked on home
        self.assertEqual(self.sender.lookups, [('XR-1', (X1 + 600, 400))])
        self.assertIn('drag', last(out, 'overview_pointer'))
        out = self.onto(1)
        self.assertEqual(last(out, 'overview_pointer')['drag']['target'], 1)
        out = self.op('resize_release')
        self.assertEqual(self.sender.drops, [(WINDOW, 'XR-1', (X1 + 600, 400), 'XR-2')])
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 0)  # home stays home
        self.assertIsNone(last(out, 'view'))

    def test_moving_first_on_home_is_the_windows_own_drag_and_never_lifts(self):
        self.at(X1 + 600, 400)
        self.op('resize_press')
        self.at(X1 + 700, 420)  # a selection or a title-bar drag in the window
        self.hold(); self.hold()
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.p.window_lifts, 0)
        self.assertEqual(self.op('resize_release', ops=('view',)), [])
        self.assertFalse(self.control.state.zoomed)

    def test_a_held_press_on_home_released_where_it_was_cancels(self):
        self.at(X1 + 600, 400)
        self.op('resize_press'); self.hold()
        self.op('resize_release')
        self.assertEqual(self.sender.drops, [])
        self.assertFalse(self.control.state.zoomed)


class FrameTests(unittest.TestCase):
    def test_the_pointer_frame_carries_the_drag_strictly(self):
        base = {'visible': True, 'x': 1, 'y': 2, 'held': True, 'az': 0, 'el': 0}
        fp.overview_pointer_frame('run', 'session', 1, {**base, 'drag': {'w': 72, 'h': 45, 'target': 2}})
        fp.overview_pointer_frame('run', 'session', 1, {**base, 'drag': {'w': 1, 'h': 512, 'target': -1}})
        for bad in ({'w': 0, 'h': 45, 'target': 2}, {'w': 513, 'h': 45, 'target': 2}, {'w': 72, 'h': 45, 'target': 4},
                    {'w': 72, 'h': 45, 'target': -2}, {'w': 72, 'h': 45}, {'w': 72, 'h': 45, 'target': 0, 'x': 1},
                    {'w': 7.5, 'h': 45, 'target': 0}, [72, 45, 0], None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fp.overview_pointer_frame('run', 'session', 1, {**base, 'drag': bad})

    def test_the_ghost_is_small_and_keeps_the_windows_shape(self):
        w, h = fp.ghost_size(WINDOW, OUTPUTS[1], T1, DEFAULT_PLACEMENT)
        self.assertAlmostEqual(w / h, 16 / 9, delta=0.05)
        self.assertLessEqual(max(w, h), fp.GHOST_MAX_PX); self.assertGreaterEqual(max(w, h), fp.GHOST_MIN_PX)
        self.assertEqual(max(fp.ghost_size({'w': 9000, 'h': 100}, OUTPUTS[1], T1, DEFAULT_PLACEMENT)), fp.GHOST_MAX_PX)
        self.assertEqual(max(fp.ghost_size({'w': 4, 'h': 4}, OUTPUTS[1], T1, DEFAULT_PLACEMENT)), fp.GHOST_MIN_PX)
        self.assertEqual(fp.ghost_size(None, OUTPUTS[1], T1, DEFAULT_PLACEMENT), fp.GHOST_DEFAULT)
        self.assertEqual(fp.ghost_size(WINDOW, None, T1, DEFAULT_PLACEMENT), fp.GHOST_DEFAULT)

    def test_desktop_at_is_the_rectangle_only(self):
        from tools.native_spike import orbit
        flat = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        at = lambda point: fp.desktop_at(G, flat, orbit.from_plane(G, point))
        self.assertEqual(at(centre(T2)), 2)
        self.assertIsNone(at((T1[0] + T1[2] / 2, T1[1] + T1[3] + fp.PILL_GAP + 2)))  # its pill
        self.assertIsNone(at(((T0[0] + T0[2] + T1[0]) / 2, T0[1] + T0[3] / 2)))  # the gap between two desktops
        self.assertIsNone(fp.desktop_at(None, flat, (0, 0)))


MONITORS_JSON = [{'id': 1, 'name': 'XR-1', 'activeWorkspace': {'id': 11}}, {'id': 2, 'name': 'XR-2', 'activeWorkspace': {'id': 12}},
                 {'id': 3, 'name': 'XR-3', 'activeWorkspace': {'id': 13}}, {'id': 4, 'name': 'XR-4', 'activeWorkspace': {'id': -98}}]


def client(address, ws, at, size, **extra):
    return {'address': address, 'workspace': {'id': ws}, 'at': list(at), 'size': list(size), 'mapped': True, 'hidden': False,
            'floating': False, 'pinned': False, 'fullscreen': 0, 'monitor': ws - 10, 'focusHistoryID': 5, **extra}


class WindowAtTests(unittest.TestCase):
    def test_the_window_shown_at_the_point(self):
        tiled = client('0x1', 12, (4480, 0), (960, 1080))
        other = client('0x2', 11, (4480, 0), (960, 1080))  # XR-1's workspace (bad coordinates, never considered)
        floating = client('0x3', 12, (4600, 100), (400, 300), floating=True, focusHistoryID=9)
        clients = [tiled, other, floating]
        self.assertEqual(send.window_at(clients, MONITORS_JSON, 'XR-2', 4500, 50)['address'], '0x1')
        self.assertEqual(send.window_at(clients, MONITORS_JSON, 'XR-2', 4700, 200),
                         {'address': '0x3', 'w': 400, 'h': 300, 'pinned': False})  # floating above tiled
        self.assertIsNone(send.window_at(clients, MONITORS_JSON, 'XR-2', 5500, 500))  # empty
        full = client('0x4', 12, (4480, 0), (1920, 1080), fullscreen=1, focusHistoryID=20)
        self.assertEqual(send.window_at(clients + [full], MONITORS_JSON, 'XR-2', 4700, 200)['address'], '0x4')
        hidden = client('0x5', 12, (4600, 100), (400, 300), floating=True, focusHistoryID=0, hidden=True)
        self.assertEqual(send.window_at(clients + [hidden], MONITORS_JSON, 'XR-2', 4700, 200)['address'], '0x3')
        pinned = client('0x6', 99, (4600, 100), (400, 300), pinned=True, monitor=2, focusHistoryID=0)
        self.assertTrue(send.window_at(clients + [pinned], MONITORS_JSON, 'XR-2', 4700, 200)['pinned'])
        self.assertIsNone(send.window_at(clients, MONITORS_JSON, 'XR-4', 4700, 200))  # a special workspace
        self.assertIsNone(send.window_at(clients, MONITORS_JSON, 'XR-9', 4700, 200))
        self.assertIsNone(send.window_at([dict(tiled, address='0x1; rm')], MONITORS_JSON, 'XR-2', 4500, 50))


class FakeHypr:
    def __init__(self, clients):
        self.clients, self.sent = clients, []
    def query(self, kind, timeout=None):
        return {'clients': self.clients, 'monitors': MONITORS_JSON}[kind]
    def mutate(self, command):
        self.sent.append(command)
        return 'ok'


class DropTests(unittest.TestCase):
    def setUp(self):
        self.hypr = FakeHypr([client('0x1', 12, (4480, 0), (960, 1080))])
        self.notes = []
        self.sender = send.WindowSender(self.hypr, ['XR-1', 'XR-2', 'XR-3'], 'DP-1', notify=self.notes.append)

    def test_the_drop_moves_onto_the_targets_workspace_without_following(self):
        self.assertIsNone(self.sender.drop_now({'address': '0x1', 'pinned': False}, 'XR-2', (4500, 50), 'XR-3'))
        self.assertEqual(self.hypr.sent, ['dispatch hl.dsp.window.move({ workspace = "13", follow = false, window = "address:0x1" })'])

    def test_without_an_address_it_moves_what_is_at_the_point(self):
        self.assertIsNone(self.sender.drop_now(None, 'XR-2', (4500, 50), 'XR-1'))
        self.assertEqual(len(self.hypr.sent), 1); self.assertIn('workspace = "11"', self.hypr.sent[0])
        self.hypr.sent.clear()
        self.assertIsNone(self.sender.drop_now(None, 'XR-2', (6000, 50), 'XR-1'))  # nothing there: nothing moves
        self.assertEqual(self.hypr.sent, [])

    def test_refusals(self):
        self.assertIn('Pinned', self.sender.drop_now({'address': '0x1', 'pinned': True}, 'XR-2', (4500, 50), 'XR-3'))
        self.assertIn('not part', self.sender.drop_now({'address': '0x1'}, 'XR-2', (4500, 50), 'XR-4'))
        self.sender.set_names(['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        self.assertIn('regular workspace', self.sender.drop_now({'address': '0x1'}, 'XR-2', (4500, 50), 'XR-4'))
        self.assertEqual(self.hypr.sent, [])

    def test_lookup_and_drop_run_off_the_loop(self):
        got = threading.Event(); found = []
        self.sender.lookup('XR-2', (4500, 50), lambda w: (found.append(w), got.set()))
        self.assertTrue(got.wait(2))
        self.assertEqual(found[0]['address'], '0x1')
        self.sender.drop(found[0], 'XR-2', (4500, 50), 'XR-3')
        self.sender.close(2)
        self.assertEqual(len(self.hypr.sent), 1)
        self.sender.drop(found[0], 'XR-2', (4500, 50), 'XR-1')  # closed (run end): nothing more moves
        self.sender.close(2)
        self.assertEqual(len(self.hypr.sent), 1)


if __name__ == '__main__':
    unittest.main()
