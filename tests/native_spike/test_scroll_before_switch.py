"""Scroll before switch (columns.py, with unified_cursor.EdgePush): zoomed in on a workspace in Hyprland's scrolling
layout, a push at the selected desktop's left or right edge scrolls its columns first. It switches to the desktop
placed that way only once no tiled window is left past that edge, and then only after the push has rested
(columns.END_REST_S) and counted again from nothing (pushing on, or pushing again later). Master, dwindle, floating,
a fullscreen window, the up and down edges and a failed read: exactly as before (test_directional_switch.py). The
desktop's own edge scroller (~/.config/hypr/scripts/scroll-edge.py, outside the repo; modelled by FakeHelper) does the
scrolling when it acts on the XR outputs and this yields to it: never a switch while a column is left, and a column per
step.

Offline only: fake clocks, a fake compositor whose scrolling layout ScrollLayout models, and the generated Lua run
against a stub `hl`. No compositor, no scroll-edge.py, no glasses.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from tools.native_spike import columns as cols, free_pointer as fp, unified_cursor as uc, view

from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, X1, X2, X3, frames
from test_unified_cursor import FakeUnifiedClient

ACTIVE = {'DP-1': 1, 'XR-1': 11, 'XR-2': 12, 'XR-3': 13}  # each monitor's active workspace
COL = 960  # a column is half a desktop wide: two fit in view


def neighbours(**table):
    return lambda edge: table.get(edge)


def monitors(active=ACTIVE):
    """MONITORS as j/monitors gives them (the fields columns.read and resize.outputs_from_monitors read)."""
    return [{**m, 'id': i, 'transform': 0, 'disabled': False,
             'activeWorkspace': {'id': active[m['name']], 'name': str(active[m['name']])}} for i, m in enumerate(MONITORS)]


class ScrollLayout:
    """Hyprland's scrolling layout on one XR desktop's active workspace as j/clients shows it: `n` columns COL px wide
    side by side from the monitor's left edge, `offset` of them scrolled off to the left. Focusing a column fits it
    into view (what scroll-edge.py relies on); `moves` False: focusing moves nothing. Thread-safe (the sampler test)."""
    def __init__(self, n=4, offset=0, layout='scrolling', fullscreen=False, floating=False, moves=True, base=X1, ws=11):
        self.n, self.offset, self.layout, self.fullscreen, self.floating, self.moves = n, offset, layout, fullscreen, floating, moves
        self.base, self.ws, self.lock = base, ws, threading.Lock()

    @staticmethod
    def address(i):
        return '0x%x' % (0xa0 + i)

    def clients(self):
        with self.lock:
            out = [{'address': self.address(i), 'workspace': {'id': self.ws, 'name': str(self.ws)}, 'floating': self.floating,
                    'mapped': True, 'hidden': False, 'at': [self.base + (i - self.offset) * COL, 38], 'size': [COL, 1000]}
                   for i in range(self.n)]
        # Another workspace's window far past the edge: never a column of this one.
        return out + [{'address': '0xfff', 'workspace': {'id': 99, 'name': '99'}, 'floating': False, 'mapped': True,
                       'hidden': False, 'at': [self.base + 5000, 38], 'size': [COL, 1000]}]

    def workspaces(self):
        return [{'id': w, 'monitor': name, 'tiledLayout': self.layout if w == self.ws else 'master',
                 'hasfullscreen': self.fullscreen if w == self.ws else False} for name, w in ACTIVE.items()]

    def focus(self, address):
        i = int(address, 16) - 0xa0
        with self.lock:
            if not self.moves or not 0 <= i < self.n: return
            if i - self.offset > 1: self.offset = i - 1
            elif i < self.offset: self.offset = i

    def past(self, side):
        """scroll-edge.py's own candidates, written out as it has them (not columns.columns_past)."""
        if self.layout != 'scrolling' or self.fullscreen: return []
        wins = [c for c in self.clients() if c['workspace']['id'] == self.ws and not c['floating']]
        if side == 'right':
            cand = [c for c in wins if c['at'][0] + c['size'][0] > self.base + 1920 + 1]
            cand.sort(key=lambda c: c['at'][0])
        else:
            cand = [c for c in wins if c['at'][0] < self.base - 1]
            cand.sort(key=lambda c: -(c['at'][0] + c['size'][0]))
        return [c['address'] for c in cand]


class LayoutClient:
    """The compositor the scroller reads and scrolls through (query / scroll_to_window), on the rig's clock."""
    def __init__(self, layout, clock, fail=False):
        self.layout, self.clock, self.fail = layout, clock, fail
        self.calls, self.scrolled = [], []

    def query(self, kind, argument=None, timeout=2.0):
        self.calls.append(kind)
        if self.fail: raise TimeoutError('compositor busy')
        if kind == 'monitors': return monitors()
        if kind == 'workspaces': return self.layout.workspaces()
        if kind == 'clients': return self.layout.clients()
        raise ValueError(kind)

    def scroll_to_window(self, address):
        self.scrolled.append((self.clock(), address))
        self.layout.focus(address)


class FakeHelper:
    """scroll-edge.py's edge loop as it is on 2026-11-03: every POLL it reads the cursor; within EDGE px of the left /
    right edge of a monitor whose workspace is scrolling it focuses the nearest tiled window past that edge, at most
    every COOLDOWN while the cursor stays there (leaving the edge re-arms it at once). Its focus lands `delay` after its
    cursor read (its monitors / workspaces / clients reads and the focus eval) and it reads the layout as it is then."""
    POLL, COOLDOWN, EDGE = 1 / 30, 0.35, 2

    def __init__(self, layout, start, delay=0.005):
        self.layout, self.next_poll, self.next_ok, self.delay = layout, start, 0.0, delay
        self.pending, self.focuses = None, []

    def tick(self, now, cursor):
        if self.pending is not None and now >= self.pending[0]:
            side, self.pending = self.pending[1], None
            cand = self.layout.past(side)
            if cand:
                self.layout.focus(cand[0])
                self.focuses.append((now, cand[0]))
        if now < self.next_poll: return
        self.next_poll = now + self.POLL
        b = self.layout.base
        side = None
        if b <= cursor[0] < b + 1920 and 0 <= cursor[1] < 1080:
            if cursor[0] >= b + 1920 - self.EDGE: side = 'right'
            elif cursor[0] <= b + self.EDGE - 1: side = 'left'
        if side is None:
            self.next_ok = 0.0
            return
        if now < self.next_ok: return
        self.next_ok = now + self.COOLDOWN
        if self.pending is None: self.pending = (now + self.delay, side)


class Rig:
    """EdgePush with ColumnScroll stepped by hand on a fake clock, a sample every free_pointer.SAMPLE_S, as
    UnifiedSampler._confine drives them for home `home`; `helper` (FakeHelper) sees the cursor as each sample left it.
    `plain` steps a second EdgePush without columns on the same samples (what the push did before)."""
    def __init__(self, layout, n, home=0, helper=False, delay=0.005, fail=False):
        self.layout, self.n, self.home, self.name = layout, n, home, NAMES[home]
        self.now, self.prev, self.plain_prev = 100.0, None, None
        self.client = LayoutClient(layout, lambda: self.now, fail)
        self.scroll = cols.ColumnScroll(self.client)
        self.push, self.plain = uc.EdgePush(), uc.EdgePush()
        self.helper = FakeHelper(layout, self.now, delay) if helper else None
        self.warps = []

    def columns(self, side, now, confirm=False):
        return self.scroll.held(self.name, side, now, confirm)

    def step(self, cursor, dt=fp.SAMPLE_S):
        self.now += dt
        warp, switch = self.push.step(OUTPUTS, self.home, cursor, self.prev, self.now, self.n, self.columns)
        self.prev = warp or cursor
        self.warps.append(warp)
        if self.helper is not None: self.helper.tick(self.now, self.prev)
        return warp, switch

    def both(self, cursor, dt=fp.SAMPLE_S):
        """One sample through both pushes; their answers."""
        new = self.step(cursor, dt)
        old = self.plain.step(OUTPUTS, self.home, cursor, self.plain_prev, self.now, self.n)
        self.plain_prev = old[0] or cursor
        return new, old

    def until_switch(self, cursor, seconds):
        """The hand keeps pushing to `cursor` (Hyprland putting it there each sample) until a switch, or `seconds`:
        (time, switch) or None."""
        end = self.now + seconds
        while self.now < end:
            _, switch = self.step(cursor)
            if switch is not None: return self.now, switch
        return None

    def rest(self, seconds, cursor=(X1 + 960, 500)):
        end = self.now + seconds
        while self.now < end: self.step(cursor)


class EndOfColumnsTests(unittest.TestCase):
    """columns.read / columns_past: the explicit end-of-columns test against j/monitors, j/workspaces, j/clients."""
    RECT = (X1, 0, 1920, 1080)

    def client(self, at, size=(COL, 1000), **extra):
        return {'address': '0xa%d' % len(str(at)), 'workspace': {'id': 11}, 'floating': False, 'mapped': True,
                'hidden': False, 'at': [at, 38], 'size': list(size), **extra}

    def test_one_px_tolerance_either_side(self):
        right = X1 + 1920
        for end, past in ((right, False), (right + 1, False), (right + 2, True)):
            c = self.client(end - COL)
            self.assertEqual(bool(cols.columns_past(self.RECT, [c], 11)['right']), past, end - right)
        for start, past in ((X1, False), (X1 - 1, False), (X1 - 2, True)):
            c = self.client(start)
            self.assertEqual(bool(cols.columns_past(self.RECT, [c], 11)['left']), past, start - X1)

    def test_nearest_first_and_only_this_workspaces_tiled_windows(self):
        layout = ScrollLayout(n=6, offset=2)  # 0, 1 off the left; 2, 3 in view; 4, 5 off the right
        clients = layout.clients()
        clients += [{**clients[5], 'address': '0xb1', 'floating': True},   # floating: not a column
                    {**clients[5], 'address': '0xb2', 'hidden': True},    # a group's hidden tab
                    {**clients[5], 'address': '0xb3', 'mapped': False}]
        out = cols.columns_past(self.RECT, clients, 11)
        self.assertEqual(out['right'], ('0xa4', '0xa5'))
        self.assertEqual(out['left'], ('0xa1', '0xa0'))
        self.assertEqual(len(out['layout']), 6)
        self.assertEqual(cols.columns_past(self.RECT, clients, 12), {'left': (), 'right': (), 'layout': ()})

    def test_partly_off_screen_counts_as_past(self):
        c = self.client(X1 + 1500)  # 540 px off the right edge
        self.assertEqual(cols.columns_past(self.RECT, [c], 11)['right'], (c['address'],))

    def test_logical_rect_scale_and_transform(self):
        m = {'x': 100, 'y': 50, 'width': 1920, 'height': 1080, 'scale': 1.25, 'transform': 0}
        self.assertEqual(cols.logical_rect(m), (100, 50, 1536.0, 864.0))
        self.assertEqual(cols.logical_rect({**m, 'transform': 1}), (100, 50, 864.0, 1536.0))
        for bad in ({**m, 'scale': 0}, {**m, 'width': 'x'}, {**m, 'transform': 1.0}):
            with self.assertRaises(ValueError): cols.logical_rect(bad)

    def test_read_scrolling_and_not(self):
        layout = ScrollLayout(n=4, offset=1)
        client = LayoutClient(layout, lambda: 0.0)
        out = cols.read(client, 'XR-1')
        self.assertEqual((out['workspace'], out['scrolling'], out['left'], out['right']), (11, True, ('0xa0',), ('0xa3',)))
        self.assertEqual(client.calls, ['monitors', 'workspaces', 'clients'])
        for change in ({'layout': 'master'}, {'layout': 'dwindle'}, {'fullscreen': True}):
            with self.subTest(**change):
                client = LayoutClient(ScrollLayout(n=4, offset=1, **change), lambda: 0.0)
                out = cols.read(client, 'XR-1')
                self.assertEqual((out['scrolling'], out['left'], out['right']), (False, (), ()))
                self.assertEqual(client.calls, ['monitors', 'workspaces'])  # no clients read for those
        self.assertIsNone(cols.read(client, 'XR-9'))
        self.assertEqual(cols.read(LayoutClient(ScrollLayout(ws=77), lambda: 0.0), 'XR-1')['scrolling'], False)  # not listed

    def test_a_malformed_tiled_window_fails_the_read(self):
        c = self.client(X1)
        for bad in ({**c, 'at': [1, 'x']}, {**c, 'address': 'x; os.execute()'}, {**c, 'size': [1]}):
            with self.assertRaises(ValueError): cols.columns_past(self.RECT, [bad], 11)
        cols.columns_past(self.RECT, [{**c, 'workspace': {'id': 5}, 'at': None}], 11)  # another workspace's: not read


class HoldTests(unittest.TestCase):
    """The push at a scrolling workspace's left / right edge: scroll first, switch only after the end, as tuned."""
    def test_columns_past_the_right_edge_scroll_first_and_then_it_switches(self):
        rig = Rig(ScrollLayout(n=4, offset=0), neighbours(right=1))  # columns 2 and 3 off the right
        rig.step((X1 + 960, 300))
        start = rig.now
        got = rig.until_switch((X2 + 9, 300), 3.0)  # pushed over onto XR-2, 10 px every sample
        self.assertIsNotNone(got)
        when, switch = got
        self.assertEqual(switch, (1, 'right', 300 / 1079))
        scrolls = rig.client.scrolled
        self.assertEqual([a for _, a in scrolls], ['0xa2', '0xa3'])  # one column at a time, nearest first
        self.assertGreaterEqual(scrolls[0][0] - start, cols.SCROLL_DWELL_S)
        self.assertGreaterEqual(scrolls[1][0] - scrolls[0][0], cols.SCROLL_REPEAT_S - 1e-9)
        self.assertGreaterEqual(when - scrolls[1][0], cols.END_REST_S)  # the end rests before the push counts again
        self.assertLess(when - scrolls[1][0], cols.END_REST_S + 0.1)    # ... and then it is the usual 48 px push
        self.assertEqual(rig.layout.offset, 2)
        held = [w for w in rig.warps[1:] if w is not None][:50]
        self.assertTrue(held and all(w == (X1 + 1918, 300) for w in held))  # back into scroll-edge.py's 2 px band

    def test_columns_past_the_left_edge_too(self):
        rig = Rig(ScrollLayout(n=4, offset=2), neighbours(left=2))  # columns 1 and 0 off the left
        rig.step((X1 + 960, 300))
        got = rig.until_switch((X1 - 9, 300), 3.0)  # pushed onto the physical monitor by hand
        self.assertEqual(got[1], (2, 'left', 300 / 1079))
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa1', '0xa0'])
        self.assertEqual(rig.layout.offset, 0)
        self.assertIn((X1 + 1, 300), rig.warps)

    def test_a_clamped_edge_holds_without_a_nudge_and_switches_after_the_end(self):
        # XR-3's right edge: Hyprland's layout has nothing beyond it, so the cursor sits on the outermost pixel.
        rig = Rig(ScrollLayout(n=3, offset=0, base=X3, ws=13), neighbours(right=0), home=2)
        rig.step((X3 + 960, 300))
        start = rig.now
        for _ in range(int(0.5 / fp.SAMPLE_S)):
            warp, switch = rig.step((X3 + 1919, 300))
            self.assertIsNone(warp); self.assertIsNone(switch)  # held: no nudge, no count
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa2'])
        when, switch = rig.until_switch((X3 + 1919, 300), 2.0)
        self.assertEqual(switch[:2], (0, 'right'))
        self.assertGreaterEqual(when - rig.client.scrolled[0][0], cols.END_REST_S)
        self.assertGreater(when - start, cols.SCROLL_DWELL_S + cols.END_REST_S)

    def test_no_switch_while_any_column_is_left(self):
        rig = Rig(ScrollLayout(n=12, offset=0), neighbours(right=1))
        rig.step((X1 + 960, 300))
        self.assertIsNone(rig.until_switch((X2 + 9, 300), 2.0))  # 10 columns off the right: 2 s of scrolling
        self.assertEqual(len(rig.client.scrolled), 6)  # at 0.15 s and then every 0.35 s
        self.assertGreater(rig.layout.offset, 0)

    def test_the_view_already_at_its_end_switches_exactly_as_before(self):
        rig = Rig(ScrollLayout(n=4, offset=2), neighbours(right=1, left=2))  # nothing off the right
        rig.both((X1 + 960, 300))
        for _ in range(60):
            new, old = rig.both((X2 + 9, 300))
            self.assertEqual(new, old)
        self.assertEqual(rig.client.scrolled, [])

    def test_a_push_again_after_the_end_is_the_usual_push(self):
        rig = Rig(ScrollLayout(n=3, offset=0), neighbours(right=1))
        rig.step((X1 + 960, 300))
        self.assertIsNone(rig.until_switch((X2 + 9, 300), 0.4))  # one column scrolled in: the end
        self.assertEqual(rig.layout.offset, 1)
        rig.rest(cols.END_REST_S + 0.1)  # let go of the edge
        rig.plain.reset()
        start, samples = rig.now, 0
        while True:
            samples += 1
            if rig.step((X2 + 9, 300))[1] is not None: break
        self.assertEqual(samples, -(-uc.PUSH_PX // 10))  # 48 px at 10 px a sample: the fifth
        self.assertLess(rig.now - start, 0.05)

    def test_a_column_that_turns_up_as_the_push_switches_holds_it(self):
        layout = ScrollLayout(n=2, offset=0)  # both in view: nothing past
        rig = Rig(layout, neighbours(right=1))
        rig.step((X1 + 960, 300))
        for _ in range(4): self.assertIsNone(rig.step((X2 + 9, 300))[1])  # 40 px of push
        layout.n = 3  # a new window opens as a third column, off the right
        warp, switch = rig.step((X2 + 9, 300))  # the 48 px sample: read again first
        self.assertIsNone(switch); self.assertEqual(warp, (X1 + 1918, 300))
        self.assertEqual(rig.push.amount, 0)

    def test_a_column_that_does_not_move_is_the_end(self):
        rig = Rig(ScrollLayout(n=4, offset=0, moves=False), neighbours(right=1))
        rig.step((X1 + 960, 300))
        got = rig.until_switch((X2 + 9, 300), 2.0)
        self.assertIsNotNone(got)
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa2'])  # tried once, never again
        self.assertLess(got[0] - rig.client.scrolled[0][0], cols.OWN_SETTLE_S + cols.REFRESH_S + 0.1)

    def test_typing_mode_scrolls_and_never_switches(self):
        # No neighbour (typing mode: the sampler gives none), so after the columns only the plain confinement.
        rig = Rig(ScrollLayout(n=4, offset=0), neighbours())
        rig.step((X1 + 960, 300))
        self.assertIsNone(rig.until_switch((X2 + 9, 300), 2.0))
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa2', '0xa3'])
        self.assertEqual(rig.warps[-1], uc.confine_target(OUTPUTS, 0, (X2 + 9, 300)))  # as before, after the rest


class AsBeforeTests(unittest.TestCase):
    """Everything that is not a left / right edge of a scrolling workspace with a column past it: the push is the
    same, sample for sample, as an EdgePush without columns."""
    PATH = ([((X1 + 960, 300), 5)] + [((X2 + 9, 300), 30)] + [((X1 + 960, 300), 80)] + [((X1 - 9, 300), 30)]
            + [((X1 + 960, 300), 80)] + [((X1 + 960, 0), 30)] + [((X1 + 960, 1079), 30)] + [((X1 + 1919, 0), 20)]
            + [((X1 + 1919, 300), 20)] + [((X1, 300), 20)])

    def walk(self, rig):
        for cursor, samples in self.PATH:
            for _ in range(samples):
                new, old = rig.both(cursor)
                self.assertEqual(new, old, (cursor, rig.now))

    def test_master_dwindle_fullscreen_and_floating_are_unchanged(self):
        for change in ({'layout': 'master'}, {'layout': 'dwindle'}, {'fullscreen': True}, {'floating': True}):
            with self.subTest(**change):
                rig = Rig(ScrollLayout(n=6, offset=2, **change), neighbours(right=1, left=2, up=1, down=2))
                self.walk(rig)
                self.assertEqual(rig.client.scrolled, [])
                self.assertNotIn('clients', rig.client.calls if 'floating' not in change else [])

    def test_a_failed_read_is_unchanged_and_not_retried_every_sample(self):
        rig = Rig(ScrollLayout(n=6, offset=2), neighbours(right=1, left=2), fail=True)
        rig.step((X1 + 960, 300)); rig.plain_prev = rig.prev
        for _ in range(250):  # a second at the right edge
            new, old = rig.both((X2 + 9, 300))
            self.assertEqual(new, old)
        self.assertLessEqual(len(rig.client.calls), 1 / cols.REFRESH_S + 2)
        self.assertGreater(rig.scroll.errors, 0)

    def test_up_and_down_on_a_scrolling_workspace_are_unchanged_and_read_nothing(self):
        rig = Rig(ScrollLayout(n=4, offset=1), neighbours(up=1, down=2))  # columns off both sides
        rig.both((X1 + 960, 300))
        switched = False
        for cursor in [(X1 + 960, 0)] * 40 + [(X1 + 960, 500)] * 250 + [(X1 + 400, 1079)] * 40:
            new, old = rig.both(cursor)
            self.assertEqual(new, old)
            switched |= new[1] is not None
        self.assertTrue(switched)
        self.assertEqual(rig.client.calls, [])  # no left / right edge touched: not one read

    def test_the_middle_reads_nothing(self):
        rig = Rig(ScrollLayout(n=4, offset=1), neighbours(right=1))
        for _ in range(250): rig.step((X1 + 1917, 300))  # 3 px from the edge: outside the band
        self.assertEqual(rig.client.calls, [])


class HelperTests(unittest.TestCase):
    """scroll-edge.py acting on the XR outputs (as it does since 2026-11-03): it scrolls, this yields."""
    def test_the_helper_scrolls_this_never_doubles_and_the_end_still_rests(self):
        rig = Rig(ScrollLayout(n=4, offset=0), neighbours(right=1), helper=True)
        rig.step((X1 + 960, 300))
        when, switch = rig.until_switch((X2 + 9, 300), 3.0)
        self.assertEqual(switch[:2], (1, 'right'))
        self.assertEqual(rig.client.scrolled, [])  # every column was the helper's
        focuses = rig.helper.focuses
        self.assertEqual([a for _, a in focuses], ['0xa2', '0xa3'])  # one per column: none skipped by a double
        self.assertEqual(rig.layout.offset, 2)
        self.assertGreaterEqual(when - focuses[-1][0], cols.END_REST_S)
        self.assertEqual(rig.scroll.yields, 2)

    def test_many_columns_with_the_helper_one_step_at_a_time(self):
        rig = Rig(ScrollLayout(n=8, offset=0), neighbours(right=1), helper=True)
        rig.step((X1 + 960, 300))
        got = rig.until_switch((X2 + 9, 300), 5.0)
        self.assertIsNotNone(got)
        self.assertEqual(rig.client.scrolled, [])
        times = [t for t, _ in rig.helper.focuses]
        self.assertEqual(len(times), 6)
        self.assertTrue(all(b - a >= FakeHelper.COOLDOWN - 1e-9 for a, b in zip(times, times[1:])))

    def test_a_slow_helper_can_double_the_first_step_then_this_yields(self):
        # The helper's focus lands 0.25 s after its cursor read (a loaded machine): longer than this one's dwell. The
        # first step then scrolls twice (this, then the helper on the layout this left); after that only the helper.
        rig = Rig(ScrollLayout(n=6, offset=0), neighbours(right=1), helper=True, delay=0.25)
        rig.step((X1 + 960, 300))
        got = rig.until_switch((X2 + 9, 300), 5.0)
        self.assertIsNotNone(got)
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa2'])
        self.assertEqual([a for _, a in rig.helper.focuses], ['0xa3', '0xa4', '0xa5'])
        self.assertEqual(rig.layout.offset, 4)

    def test_if_the_helper_stops_this_takes_over(self):
        rig = Rig(ScrollLayout(n=6, offset=0), neighbours(right=1), helper=True)
        rig.step((X1 + 960, 300))
        rig.until_switch((X2 + 9, 300), 0.5)
        self.assertEqual(len(rig.helper.focuses), 2)
        rig.helper = None  # it was stopped (scroll-edge.py --stop)
        got = rig.until_switch((X2 + 9, 300), 4.0)
        self.assertIsNotNone(got)
        self.assertEqual([a for _, a in rig.client.scrolled], ['0xa4', '0xa5'])
        self.assertGreaterEqual(rig.client.scrolled[0][0] - rig.scroll.hold[2], cols.HELPER_GRACE_S - 0.4)


class LuaTests(unittest.TestCase):
    """columns.scroll_lua, run by a real Lua interpreter against a stub `hl` (no compositor)."""
    STUB = r'''
local log = {}
local nw = %s
local broken = %s
hl = { dsp = {} }
function hl.get_config(k)
  if k == "cursor.no_warps" then if broken then error("no such option") end return nw end
  error("unknown option " .. k)
end
function hl.config(t) if t.cursor then nw = t.cursor.no_warps log[#log + 1] = "set " .. tostring(nw) end end
function hl.dsp.focus(t) return { window = t.window } end
function hl.dispatch(p) log[#log + 1] = "focus " .. p.window .. " no_warps=" .. tostring(nw) end
'''

    def run_lua(self, body, nw='false', broken='false'):
        lua = shutil.which('lua') or shutil.which('luajit')
        if lua is None: self.skipTest('no Lua interpreter')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'run.lua'
            script.write_text(self.STUB % (nw, broken) + body + '\nprint(table.concat(log, "|"))\n')
            done = subprocess.run([lua, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip().split('|')

    def test_focus_with_no_warps_for_that_one_focus_then_as_it_was(self):
        code = cols.scroll_lua('0x55d4c3a8e1b0')
        self.assertEqual(self.run_lua(code), ['set true', 'focus address:0x55d4c3a8e1b0 no_warps=true', 'set false'])
        self.assertEqual(self.run_lua(code, nw='true'), ['set true', 'focus address:0x55d4c3a8e1b0 no_warps=true', 'set true'])
        self.assertEqual(self.run_lua(code, broken='true'), ['set true', 'focus address:0x55d4c3a8e1b0 no_warps=true', 'set false'])

    def test_only_a_window_address(self):
        for bad in ('55d4', '0xZZ', '0x1"); os.exit(', '', None, '0x' + '1' * 17):
            with self.assertRaises(ValueError): cols.scroll_lua(bad)

    def test_the_client_evals_exactly_that(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.scroll_to_window('0xabc')
        self.assertEqual(sent, ['/eval ' + cols.scroll_lua('0xabc')])
        with self.assertRaises(ValueError): client.scroll_to_window('window')
        self.assertEqual(len(sent), 1)
        client._request = lambda wire: b'error: nope'
        with self.assertRaises(ValueError): client.scroll_to_window('0xabc')


class BandTests(unittest.TestCase):
    def test_the_band_and_the_hold_point_are_scroll_edge_pys(self):
        o = OUTPUTS[0]
        self.assertEqual([uc._band(o, (X1 + x, 300)) for x in (0, 1, 2, 1917, 1918, 1919)],
                         ['left', 'left', None, None, 'right', 'right'])
        self.assertEqual(uc.hold_point(o, (X2 + 40, 300), 'right'), (X1 + 1918, 300))
        self.assertEqual(uc.hold_point(o, (X1 - 40, 2000), 'left'), (X1 + 1, 1079 - fp.INSET_PX))
        odd = {'index': 0, 'x': 1000, 'y': 0, 'width': 1919 / 1.75, 'height': 600}  # a fractional logical width
        x, _ = uc.hold_point(odd, (5000, 10), 'right')
        self.assertEqual(uc._band(odd, (x, 10)), 'right')
        self.assertLessEqual(x, 1000 + odd['width'] - 1)


class FakeScrollClient(FakeUnifiedClient):
    """The sampler test's compositor: FakeUnifiedClient plus j/monitors with the active workspaces, j/workspaces,
    j/clients from a ScrollLayout, and scroll_to_window."""
    def __init__(self, layout, cursor):
        super().__init__(cursor=cursor)
        self.layout = layout

    def query(self, kind, argument=None, timeout=2.0):
        if kind in ('monitors', 'workspaces', 'clients'):
            with self.lock: self.calls.append(kind)
            return {'monitors': monitors, 'workspaces': self.layout.workspaces, 'clients': self.layout.clients}[kind]()
        return super().query(kind, argument, timeout)

    def scroll_to_window(self, address):
        self._call('scroll', address)
        self.layout.focus(address)


class SamplerTests(unittest.TestCase):
    """The running sampler thread (UnifiedSampler) with a compositor that scrolls."""
    def setUp(self):
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)
        self.control.state.zoomed = True

    def start(self, client):
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def push(self, client, cursor, until, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            client.cursor = cursor
            self.control.poll()
            if until(): return True
            time.sleep(0.005)
        return until()

    def test_the_columns_scroll_in_and_then_the_push_switches(self):
        layout = ScrollLayout(n=4, offset=0)
        client = FakeScrollClient(layout, (X1 + 960, 300))
        sampler = self.start(client)
        seen = []
        def switched():
            if self.control.state.selected == 1:
                seen.append(layout.offset)
                return True
            return False
        self.assertTrue(self.push(client, (X2 + 8, 300), switched, timeout=5.0))
        self.assertEqual(client.args('scroll'), ['0xa2', '0xa3'])
        self.assertEqual(seen, [2])  # every column was in before the switch
        self.assertIn((X1 + 1918, 300, 'XR-1'), client.args('confine'))  # held in the band meanwhile
        self.assertEqual(sampler.columns.scrolls, 2)
        self.assertEqual(self.p.edge_switches, 1)

    def test_typing_mode_scrolls_but_never_switches(self):
        self.control.state.typing = True
        layout = ScrollLayout(n=3, offset=0)
        client = FakeScrollClient(layout, (X1 + 960, 300))
        self.start(client)
        self.assertFalse(self.push(client, (X2 + 8, 300), lambda: self.control.state.selected != 0, timeout=1.2))
        self.assertEqual(client.args('scroll'), ['0xa2'])

    def test_no_scrolling_during_a_vr_video(self):
        self.control.video_active = True
        client = FakeScrollClient(ScrollLayout(n=4, offset=0), (X1 + 960, 300))
        self.start(client)
        self.push(client, (X2 + 8, 300), lambda: False, timeout=0.5)
        self.assertEqual(client.count('scroll'), 0)
        self.assertEqual(client.count('clients'), 0)
        self.assertEqual(client.args('confine')[-1], (X1 + 1919 - fp.INSET_PX, 300, 'XR-1'))  # confined as before

    def test_a_client_that_cannot_scroll_is_as_before(self):
        sampler = self.start(FakeUnifiedClient(cursor=(X1 + 960, 300)))
        self.assertIsNone(sampler.columns)


if __name__ == '__main__':
    unittest.main()
