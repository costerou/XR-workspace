"""The overview's text cursor (Cody's pick, 2026-11-03: the Serif Beam): where the app under the dot asks for a text cursor,
the dot's look is `text` and the renderer draws the beam instead of the dot. The shape's name comes through the ownership
broker (`cursor_shape`, read-only), the sampler thread asks while the dot is on a desktop's content, and everything here
ends in the `overview_pointer` frame's `look`. While the plugin pushes each change on socket2 (`xrcursor`), that line does the
work and the reads only check it.

Offline only: fake clocks, fake compositor clients and the real C++ validators when the ownership build is present. Nothing
here has watched a real app ask for a cursor, or been seen in the glasses.
"""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, managed, orbit, ownership, unified_cursor as uc, view
from tools.native_spike.hyprland import OwnershipBlocked
from tools.native_spike.ownership import IsolatedBrokerClient, OwnershipBrokerClient

from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, T0, X1, X2, PointerBase, frames, last
from test_unified_cursor import MID1, FakeUnifiedClient, UnifiedBase

FIXTURES = Path(__file__).with_name('fixtures')
BENCH_LOCAL = Path('/tmp').is_dir()
ROOT = Path(__file__).resolve().parents[2]
BINARY = next((p for p in (ROOT / 'build/release/ownership_test', ROOT / 'build/native-spike/ownership_test') if p.is_file()), None)
# Every name of wp_cursor_shape_device_v1 as Hyprland keeps it (src/helpers/CursorShapes.hpp at efb5099), and its own.
CURSOR_SHAPES = ('invalid', 'default', 'context-menu', 'help', 'pointer', 'progress', 'wait', 'cell', 'crosshair', 'text', 'vertical-text',
                 'alias', 'copy', 'move', 'no-drop', 'not-allowed', 'grab', 'grabbing', 'e-resize', 'n-resize', 'ne-resize', 'nw-resize', 's-resize',
                 'se-resize', 'sw-resize', 'w-resize', 'ew-resize', 'ns-resize', 'nesw-resize', 'nwse-resize', 'col-resize', 'row-resize',
                 'all-scroll', 'zoom-in', 'zoom-out', 'dnd-ask', 'all-resize', 'left_ptr', 'top_side', 'bottom_right_corner')


class MappingTests(unittest.TestCase):
    def test_every_text_like_name_is_text(self):
        for name in ('text', 'vertical-text', 'xterm', 'ibeam', 'Text', 'IBeam', 'IBEAM', 'XTerm', 'vertical_text', 'Vertical-Text'):
            self.assertTrue(fp.is_text_shape(name), name)

    def test_every_other_cursor_keeps_the_dot(self):
        for name in CURSOR_SHAPES:
            if name not in ('text', 'vertical-text'): self.assertFalse(fp.is_text_shape(name), name)
        for name in (None, '', 'textual', 'context', 'ibeam2', 'text ', 'xterm-256color', 12, b'text', ['text'], True):
            self.assertFalse(fp.is_text_shape(name), repr(name))

    def test_the_wire_look_is_one_word_and_only_the_renderers(self):
        self.assertIn('text', fp.POINTER_LOOKS)
        self.assertNotIn('xterm', fp.POINTER_LOOKS)
        self.assertNotIn('text', fp.RESIZE_LOOKS.values())
        for h in (None, ('desktop', 0, (1, 1)), ('pill', 0), ('edge', 0, 't'), ('corner', 0, 'tl'), ('roll', 0, 'level'), ('panel', 0)):
            self.assertNotEqual(fp.look_for(h), 'text')  # a hit never means text: only the app under the real cursor does


class HoldTests(unittest.TestCase):
    def test_it_comes_on_at_once(self):
        h = fp.TextHold()
        self.assertFalse(h.step(False, 0.0))
        self.assertTrue(h.step(True, 0.0))

    def test_it_goes_at_once_when_the_text_stops(self):
        h = fp.TextHold()
        h.step(True, 1.0)
        self.assertFalse(h.step(False, 2.0))
        self.assertFalse(h.step(False, 3.0))

    def test_the_filter_is_a_few_tens_of_milliseconds(self):
        self.assertLessEqual(fp.TEXT_BOUNCE_S, 0.08)
        self.assertLess(fp.TEXT_BOUNCE_S, fp.TEXT_SWEEP_S)
        self.assertLessEqual(fp.TEXT_SWEEP_S, 0.5)

    def test_only_the_first_gap_of_a_sweep_can_show_the_dot(self):
        h = fp.TextHold()
        now, shown = 1.0, []
        for text in (True, False, False, True, False, True, True, False, False, True):  # 20 ms apart, line after line
            shown.append(h.step(text, now)); now += 0.02
        self.assertEqual(shown, [True, False, False, True, True, True, True, True, True, True])

    def test_a_leave_in_a_sweep_waits_the_filter_and_no_longer(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.01); h.step(True, 1.03)  # a gap
        self.assertTrue(h.step(False, 1.2))
        self.assertTrue(h.step(False, 1.2 + fp.TEXT_BOUNCE_S - 0.001))
        self.assertFalse(h.step(False, 1.2 + fp.TEXT_BOUNCE_S))

    def test_each_gap_of_a_sweep_keeps_the_sweep_going(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.01); h.step(True, 1.03)
        now = 1.03
        for _ in range(30):  # far longer than one TEXT_SWEEP_S
            now += fp.TEXT_SWEEP_S * 0.9
            self.assertTrue(h.step(False, now))
            now += fp.TEXT_BOUNCE_S * 0.5
            self.assertTrue(h.step(True, now))

    def test_the_sweep_ends_and_leaving_is_at_once_again(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.01); h.step(True, 1.03)
        self.assertFalse(h.step(False, 1.03 + fp.TEXT_SWEEP_S))

    def test_a_slow_gap_is_no_bounce(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.1)
        h.step(True, 1.1 + fp.TEXT_BOUNCE_S + 0.001)   # the dot was there long enough to be seen as the right answer
        self.assertFalse(h.step(False, 1.3))

    def test_clear_drops_it_at_once(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.01); h.step(True, 1.02); h.step(False, 1.03)  # in a sweep, waiting
        self.assertTrue(h.on)
        h.clear()
        self.assertFalse(h.on)
        self.assertFalse(h.step(False, 1.04))

    def test_clear_ends_the_sweep_too(self):
        h = fp.TextHold()
        h.step(True, 1.0); h.step(False, 1.01); h.step(True, 1.02)
        h.clear()
        h.step(True, 1.03)
        self.assertFalse(h.step(False, 1.04))


class LookTests(UnifiedBase):
    """The dot on XR-1's content (home), the app under it answering through feed_shape as the sampler would."""
    def shape(self, name, advance=0.03):
        self.clock.now += advance
        self.p.feed_shape(name)
        return frames(self.control.poll(), ('overview_pointer',))

    def look(self):
        return self.p.sent.get('look')

    def on_content(self):
        out = self.at(*MID1)
        self.assertEqual(self.p.mode, 'desktop')
        return last(out, 'overview_pointer')

    def test_over_text_the_frame_says_text_and_nothing_else_changes(self):
        plain = self.on_content()
        out = self.shape('text')
        beam = last(out, 'overview_pointer')
        self.assertEqual(beam, {**plain, 'look': 'text'})  # the same pixel and direction: the click point is the beam's centre

    def test_the_vertical_shape_is_the_same_beam(self):
        self.on_content()
        self.assertEqual(last(self.shape('vertical-text'), 'overview_pointer')['look'], 'text')

    def test_it_goes_back_to_the_dot_on_the_tick_the_app_stops_asking(self):
        plain = self.on_content()
        self.shape('text')
        out = self.shape('left_ptr', advance=0.0)
        self.assertEqual(last(out, 'overview_pointer'), plain)

    def test_a_sweep_down_the_lines_shows_the_dot_once_at_most(self):
        self.on_content()
        self.shape('text')
        looks = []
        for i, name in enumerate(('default', 'text', 'default', 'text', 'default', 'text', 'default', 'text')):  # gap, line ...
            self.clock.now += 0.02
            self.p.feed_shape(name)
            looks += [f['payload'].get('look') for f in self.at(X1 + 900, 500 + i, advance=0.0, ops=('overview_pointer',))]
        self.assertEqual(looks[:2], [None, 'text'])  # the first gap
        self.assertEqual(set(looks[2:]), {'text'})

    def test_a_failed_read_is_the_plain_dot(self):
        plain = self.on_content()
        self.assertEqual(self.shape(None), [])
        self.assertIsNone(self.look())
        self.assertEqual(self.p.sent, plain)

    def test_an_app_with_a_picture_cursor_or_any_other_shape_keeps_the_dot(self):
        self.on_content()
        for name in ('', None, 'pointer', 'crosshair', 'wait', 'left_ptr'):
            self.assertEqual(self.shape(name), [], name)
        self.assertIsNone(self.look())

    def test_a_handle_wins_at_once_with_no_hold(self):
        self.on_content()
        self.shape('text')
        self.p.feed_shape('text')  # still says text: the parked cursor sits on a window while the dot is on the edge
        out = self.at(X1 + 960, 0, ops=('overview_pointer',))
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(last(out, 'overview_pointer')['look'], 'resize_ns')

    def test_every_handle_keeps_its_arrows_whatever_the_app_says(self):
        self.on_content()
        self.shape('text')
        self.at(X1 + 300, 540); self.at(X1 + 300, 1079)  # free, off the bottom edge
        self.p.feed_shape('text')
        for target, want in ((lambda: fp.project_glass(G, T0, self.control.state.placements[0], *self.pill_middle()), 'move'),
                             (lambda: self.bar_point(), 'rotate')):
            aim = target()
            out = self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1], ops=('overview_pointer',))
            self.assertEqual(self.p.mode, 'free')
            self.assertEqual(last(out, 'overview_pointer')['look'], want)

    def pill_middle(self):
        x, y, w, h = fp.pill_rect(T0)
        return x + w / 2, y + h / 2

    def bar_point(self):
        x, y, w, h = fp.level_bar_rect(T0)
        return fp.project(G, T0, self.control.state.placements[0], x + w / 2, y + h / 2)

    def test_free_over_empty_space_stays_the_dot(self):
        self.on_content()
        self.shape('text')
        self.at(X1 + 960, 0)
        self.p.feed_shape('text')
        self.move_canvas(0, -160)
        self.assertEqual(self.p.mode, 'free')
        self.assertIsNone(self.p.hover)
        self.assertIsNone(self.look())

    def test_a_held_button_on_a_window_keeps_the_beam_at_the_held_level(self):
        self.on_content()
        self.shape('text')
        out = self.op('resize_press', ops=('overview_pointer',))  # a text selection: the click binds are non-consuming
        self.assertTrue(last(out, 'overview_pointer')['held'])
        self.assertEqual(last(out, 'overview_pointer')['look'], 'text')

    def test_off_while_maximized(self):
        self.on_content()
        self.shape('text')
        self.op('zoom_in')
        self.control.join_focus(2)
        out = frames(self.control.poll(), ('overview_pointer',))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', self.p.sent)
        self.p.feed_shape('text')
        self.assertEqual(frames(self.control.poll(), ('overview_pointer',)), [])

    def test_off_in_typing_mode(self):
        self.on_content()
        self.shape('text')
        self.op(view.TYPING_OP)
        out = frames(self.control.poll(), ('overview_pointer',))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', self.p.sent)
        self.p.feed_shape('text')
        self.assertEqual(frames(self.control.poll(), ('overview_pointer',)), [])

    def test_off_during_a_video(self):
        self.on_content()
        self.shape('text')
        self.control.video_active = True
        out = frames(self.control.poll(), ('overview_pointer',))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', self.p.sent)

    def test_off_with_the_key_bind_panel(self):
        self.on_content()
        self.shape('text')
        with mock.patch.object(type(self.control), 'keys_open', True, create=True):
            out = frames(self.control.poll(), ('overview_pointer',))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', self.p.sent)

    def test_back_in_the_overview_the_dot_waits_for_a_fresh_read(self):
        self.on_content()
        self.shape('text')
        self.op('zoom_in'); self.control.join_focus(2)
        self.control.poll()
        self.p.feed_shape('text')  # the last read: from before the app's window went away, say
        self.control.poll()
        self.op('zoom_out'); self.control.join_focus(2)
        self.clock.now += 1
        out = self.at(*MID1, ops=('overview_pointer',))
        self.assertEqual(self.p.mode, 'desktop')
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', last(out, 'overview_pointer'))

    def test_the_hidden_dot_says_nothing_after_the_idle_time(self):
        self.on_content()
        self.shape('text')
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.p.feed_shape('text')
        out = frames(self.control.poll(), ('overview_pointer',))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertNotIn('look', last(out, 'overview_pointer'))

    def test_the_help_card_over_the_dot_wins(self):
        self.on_content()
        self.shape('text')
        x, y = orbit.plane_at(G, orbit.pointer_ray(G, self.p.pointer))
        self.control.help_visible = True
        self.p.set_help_geometry({'card': (x - 100, y - 100, 200, 200)})
        frames(self.control.poll())
        self.assertEqual(self.p.mode, 'free')
        self.assertNotIn('look', self.p.sent)

    def test_an_open_panel_drawn_over_the_content_keeps_the_dot(self):
        self.on_content()
        with mock.patch.object(self.p, '_hit', return_value=('panel', 0)):
            self.assertEqual(self.shape('text'), [])
            self.assertIsNone(self.look())

    def test_the_sliver_at_a_desktops_edge_is_the_resize_zones_not_text(self):
        self.on_content()
        with mock.patch.object(self.p, '_hit', return_value=('edge', 0, 't')):
            self.assertEqual(self.shape('text'), [])
        self.assertIsNone(self.look())

    def test_a_super_drag_shows_its_own_arrows_and_leaves_no_beam_behind(self):
        self.on_content()
        self.shape('text')
        self.op('super_left_press')
        self.clock.now += 0.2
        self.p.feed_shape('text')
        frames(self.control.poll())
        self.assertEqual(self.look(), 'move')
        self.assertFalse(self.p.text_hold.on)  # so the drop is the plain dot until a fresh read says otherwise

    def test_the_gate_holds_on_its_own(self):
        self.on_content()
        self.assertTrue(self.p._text_allowed())
        self.p.mode = 'free'                    # the dot alone, even with content under it
        self.assertFalse(self.p._text_allowed())
        self.p.mode = 'desktop'
        self.control.state.typing = True        # before the loop has noticed
        self.assertFalse(self.p._text_allowed())
        self.control.state.typing = False
        self.p.super_drag = 'resize'
        self.assertFalse(self.p._text_allowed())
        self.p.super_drag = None
        self.p.drag = {'kind': 'slider'}
        self.assertFalse(self.p._text_allowed())
        self.p.drag = None
        self.assertTrue(self.p._text_allowed())

    def test_the_look_never_shows_a_beam_off_the_content_even_between_ticks(self):
        self.on_content()
        self.p.text_hold.on = True
        self.assertEqual(self.p._look(), 'text')
        self.p.mode = 'carry'
        self.assertIsNone(self.p._look())
        self.p.mode, self.p.hover = 'free', ('pill', 0)
        self.assertEqual(self.p._look(), 'move')

    def test_a_leaves_wait_wakes_the_loop_at_every_read_so_it_ends_on_time(self):
        self.on_content()
        self.shape('text')
        self.shape('default', advance=0.0)
        self.shape('text', advance=0.01)        # a gap: leaves wait now
        self.p.feed_shape('default')            # a change wakes it
        self.assertTrue(self.woken())
        frames(self.control.poll())
        self.assertIsNotNone(self.p.text_hold.until)
        self.p.feed_shape('default')            # no change, but the leave is waiting
        self.assertTrue(self.woken())
        self.clock.now += fp.TEXT_BOUNCE_S
        frames(self.control.poll())
        self.assertIsNone(self.p.text_hold.until)
        self.p.feed_shape('default')            # no wait, no change: nothing to do
        self.assertFalse(self.woken())

    def woken(self):
        readable = bool(select.select([self.p.wake_reader], [], [], 0)[0])
        self.p.drain()
        return readable

    def test_a_key_switch_takes_a_fresh_read_on_the_new_home(self):
        self.on_content()
        self.shape('text')
        self.p.feed_shape('default')
        self.op('switch_right')
        self.control.join_focus(2)
        self.clock.now += 0.2
        self.at(X2 + 960, 540)
        self.p.feed_shape('default')
        self.clock.now += 0.2
        frames(self.control.poll())
        self.assertIsNone(self.look())


class PlainPointerTests(PointerBase):
    """The gate and the look live on OverviewPointer, not only on the run's unified pointer."""
    def test_the_base_pointer_shows_the_beam_on_content_and_its_arrows_on_an_edge(self):
        self.at(X1 + 960, 540)
        self.clock.now += 0.03
        self.p.feed_shape('text')
        self.assertEqual(last(frames(self.control.poll(), ('overview_pointer',)), 'overview_pointer')['look'], 'text')
        self.p.feed_shape('text')
        out = self.at(X1 + 960, 0, ops=('overview_pointer',))
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(last(out, 'overview_pointer')['look'], 'resize_ns')


class FakeReads:
    """What the sampler calls for the name: a list of answers (an Exception is raised), the last one repeated."""
    def __init__(self, answers=('text',)):
        self.answers, self.calls = list(answers), 0

    def __call__(self):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        if isinstance(answer, Exception): raise answer
        return answer


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda name: None, None, None)
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

    def start(self, reads, client=None):
        sampler = uc.UnifiedSampler(client or FakeUnifiedClient(cursor=MID1), NAMES, self.p, BIND_ARGS, cursor_shape=reads).start()
        self.addCleanup(sampler.stop)
        return sampler

    def run_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.control.poll(); time.sleep(0.003)

    def test_on_content_with_no_push_it_asks_up_to_120_times_a_second_and_the_frame_follows(self):
        reads = FakeReads()
        self.start(reads)
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') == 'text'))
        before = reads.calls
        self.run_for(0.5)
        self.assertGreaterEqual(reads.calls - before, 20)  # a loaded machine reads less often, never more
        self.assertLessEqual(reads.calls - before, 62)

    def test_while_pushes_arrive_it_only_checks(self):
        reads = FakeReads()
        self.start(reads)
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') == 'text'))
        self.p.push_shape('text')
        before = reads.calls
        self.run_for(0.6)
        self.assertLessEqual(reads.calls - before, 4)      # 2 or 3, plus the one in flight
        self.assertGreaterEqual(reads.calls - before, 1)   # the check is still there

    def test_a_read_that_disagrees_with_the_last_push_brings_the_fast_reads_back(self):
        reads = FakeReads(['text'])
        self.start(reads)
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') == 'text'))
        self.p.push_shape('default')                       # and now the app says text: a push went missing
        self.assertTrue(self.wait(lambda: not self.p.shape_pushed))
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') == 'text'))
        before = reads.calls
        self.run_for(0.3)
        self.assertGreaterEqual(reads.calls - before, 10)
        self.p.push_shape('text')                          # pushes are back
        self.assertTrue(self.p.shape_pushed)

    def test_off_the_content_it_does_not_ask(self):
        reads = FakeReads()
        client = FakeUnifiedClient(cursor=MID1)
        sampler = self.start(reads, client)
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') == 'text'))
        client.cursor = (X1 + 1919, 540)
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))
        self.assertTrue(self.wait(lambda: self.p.sent.get('look') != 'text'))
        calls = reads.calls
        self.run_for(0.2)
        self.assertEqual(reads.calls, calls)

    def test_maximized_it_does_not_ask(self):
        self.control.state.zoomed = True
        reads = FakeReads()
        client = FakeUnifiedClient(cursor=MID1)
        self.start(reads, client)
        self.assertTrue(self.wait(lambda: client.count('cursorpos') > 3))
        self.run_for(0.1)
        self.assertEqual(reads.calls, 0)

    def test_in_typing_mode_it_does_not_ask(self):
        self.control.state.typing = True
        reads = FakeReads()
        client = FakeUnifiedClient(cursor=MID1)
        self.start(reads, client)
        self.assertTrue(self.wait(lambda: client.count('monitors') > 0))
        self.run_for(0.2)
        self.assertEqual(reads.calls, 0)

    def test_during_a_video_it_does_not_ask(self):
        self.control.video_active = True
        reads = FakeReads()
        client = FakeUnifiedClient(cursor=MID1)
        self.start(reads, client)
        self.assertTrue(self.wait(lambda: client.count('monitors') > 0))
        self.run_for(0.2)
        self.assertEqual(reads.calls, 0)

    def test_during_a_super_move_in_typing_mode_it_does_not_ask(self):
        self.control.state.typing = True        # the pointer is on for the drag, the handles are not (overview, _handles_on)
        self.p.super_drag = 'move'
        reads = FakeReads()
        sampler = uc.UnifiedSampler(FakeUnifiedClient(cursor=MID1), NAMES, self.p, BIND_ARGS, cursor_shape=reads)
        self.p.mode, self.p.outputs, sampler.applied = 'desktop', OUTPUTS, 0
        self.assertTrue(self.p.overview())
        sampler._read_shape()
        self.assertEqual(reads.calls, 0)

    def test_a_failed_read_is_the_plain_dot_and_waits_a_second(self):
        for error in (OwnershipBlocked('missing/invalid ownership broker'), OSError('gone'), ValueError('bad'), KeyError('x'), TypeError('y')):
            reads = FakeReads(['text', error])
            sampler = self.start(reads)
            self.assertTrue(self.wait(lambda: reads.calls >= 2))
            self.assertTrue(self.wait(lambda: self.p.sent.get('look') is None))
            calls = reads.calls
            self.run_for(0.15)
            self.assertEqual(reads.calls, calls, repr(error))
            self.assertEqual(sampler.shape_errors, 1)
            sampler.stop()

    def test_with_no_reader_nothing_is_asked_and_the_dot_stays(self):
        client = FakeUnifiedClient(cursor=MID1)
        self.start(None, client)
        self.assertTrue(self.wait(lambda: self.p.mode == 'desktop'))
        self.run_for(0.1)
        self.assertIsNone(self.p.sent.get('look'))
        self.assertFalse(self.p.shape_wired)            # and no push counts either

    def test_a_reader_is_what_lets_pushes_in(self):
        self.start(FakeReads())
        self.assertTrue(self.p.shape_wired)

    def test_the_first_read_waits_for_the_app_to_see_the_warp(self):
        reads = FakeReads()
        sampler = uc.UnifiedSampler(FakeUnifiedClient(cursor=MID1), NAMES, self.p, BIND_ARGS, cursor_shape=reads)
        self.p.mode, self.p.outputs, sampler.applied = 'desktop', OUTPUTS, 3
        self.p._sync = 4  # a warp the sampler has not applied yet
        sampler._read_shape()
        self.assertEqual(reads.calls, 0)
        sampler.applied = 4
        sampler._read_shape()
        self.assertEqual(reads.calls, 0)  # the read after that one, once the app has had time to answer
        sampler._shape_due = 0.0
        sampler._read_shape()
        self.assertEqual(reads.calls, 1)

    def test_after_a_warp_the_read_is_not_put_off_by_the_check(self):
        reads = FakeReads()
        sampler = uc.UnifiedSampler(FakeUnifiedClient(cursor=MID1), NAMES, self.p, BIND_ARGS, cursor_shape=reads)
        self.p.mode, self.p.outputs, sampler.applied = 'desktop', OUTPUTS, 3
        self.p.push_shape('text')
        sampler._read_shape()
        self.assertEqual(reads.calls, 1)
        self.p._sync = 4                  # a warp, at once after that read
        sampler._read_shape()
        sampler.applied = 4
        sampler._shape_due = 0.0          # the app has had its time, far inside SHAPE_CHECK_S of the last read
        sampler._read_shape()
        self.assertEqual(reads.calls, 2)

    def test_when_the_gate_opens_the_read_is_not_put_off_by_the_check(self):
        reads = FakeReads()
        sampler = uc.UnifiedSampler(FakeUnifiedClient(cursor=MID1), NAMES, self.p, BIND_ARGS, cursor_shape=reads)
        self.p.mode, self.p.outputs, sampler.applied = 'desktop', OUTPUTS, 3
        self.p.push_shape('text')
        sampler._read_shape()
        self.assertEqual(reads.calls, 1)
        self.p.shape_gated = True         # a handle, or a drag: no push will say the cursor is still text afterwards
        sampler._read_shape()             # the gate shut: every SHAPE_S
        self.assertEqual(reads.calls, 1)  # not this instant
        sampler._shape_read = 0.0
        sampler._read_shape()
        self.assertEqual(reads.calls, 2)
        self.p.shape_gated = False        # open again, and a read taken while it was shut says nothing of now
        sampler._shape_read = time.monotonic() - fp.SHAPE_S
        sampler._read_shape()
        self.assertEqual(reads.calls, 3)
        sampler._shape_read = time.monotonic() - fp.SHAPE_S
        sampler._read_shape()
        self.assertEqual(reads.calls, 3)  # that one done, it only checks again


class PushTests(PointerBase):
    """The plugin's `xrcursor` line on socket2 to the frame, with no sampler and no cursor motion."""
    def setUp(self):
        super().setUp()
        self.p.shape_wired = True
        self.at(X1 + 900, 500)
        self.p._applied = self.p._sync

    def event(self, *lines):
        return (self.control.on_events(b''.join(line + b'\n' for line in lines)) or b'') + (self.control.poll() or b'')

    def look(self):
        return self.p.sent.get('look')

    def test_the_line_is_the_frame_on_the_same_pass_of_the_loop(self):
        self.assertEqual(last(frames(self.event(b'xrcursor>>text'), ('overview_pointer',)), 'overview_pointer')['look'], 'text')
        self.assertNotIn('look', last(frames(self.event(b'xrcursor>>default'), ('overview_pointer',)), 'overview_pointer'))

    def test_every_text_name_and_no_name(self):
        for name in (b'xterm', b'ibeam', b'vertical-text'):
            self.clock.now += fp.TEXT_SWEEP_S  # each its own, not a sweep
            self.event(b'xrcursor>>' + name)
            self.assertEqual(self.look(), 'text')
            self.event(b'xrcursor>>')       # a picture, or off the run's outputs
            self.assertIsNone(self.look())

    def test_a_split_read_and_a_line_among_others(self):
        self.control.on_events(b'activewindow>>a,b\nxrcur')
        self.control.on_events(b'sor>>text\nworkspace>>3\n')
        self.control.poll()
        self.assertEqual(self.look(), 'text')

    def test_the_last_word_of_a_burst_stands(self):
        self.event(b'xrcursor>>text', b'xrcursor>>default', b'xrcursor>>xterm')
        self.assertEqual(self.look(), 'text')

    def test_anything_but_a_plain_word_is_dropped(self):
        self.event(b'xrcursor>>text')
        for line in (b'xrcursor>>te xt', b'xrcursor>>-text', b'xrcursor>>text>>x', b'xrcursor>>' + b'a' * 65, b'xrcursor>>te\xc3\xa9',
                     b'xrcursor2>>default', b'xrcursor>default', b' xrcursor>>default'):
            self.event(line)
            self.assertEqual(self.look(), 'text', line)
        self.assertEqual(self.p._shape_serial, 1)

    def test_a_run_with_no_read_takes_no_push(self):
        self.p.shape_wired = False
        self.event(b'xrcursor>>text')
        self.assertIsNone(self.look())
        self.assertFalse(self.p.shape_pushed)

    def test_no_pointer_no_harm(self):
        self.control.overview_pointer = None
        self.assertEqual(self.control.on_events(b'xrcursor>>text\n') or b'', b'')

    def test_a_push_from_before_the_warp_is_only_proof_of_life(self):
        self.p._sync = self.p._applied + 1
        self.control.on_events(b'xrcursor>>text\n')
        self.assertFalse(self.p._shape_text)
        self.assertTrue(self.p.shape_pushed)

    def test_the_gate_wins_over_a_push(self):
        with mock.patch.object(self.p, '_text_allowed', return_value=False):
            self.event(b'xrcursor>>text')
        self.assertFalse(self.p.text_hold.on)
        self.assertTrue(self.p.shape_gated)
        self.control.poll()
        self.assertFalse(self.p.shape_gated)
        self.assertIsNone(self.look())      # a word from behind the gate counts for nothing: a fresh read decides

    def test_a_read_a_push_overtook_counts_for_nothing(self):
        self.event(b'xrcursor>>text')
        serial = self.p.shape_serial()
        self.event(b'xrcursor>>default')
        self.p.feed_shape('text', serial)   # asked before the change, answered after it
        self.control.poll()
        self.assertIsNone(self.look())
        self.assertTrue(self.p.shape_pushed)

    def test_a_read_after_it_stands(self):
        self.event(b'xrcursor>>default')
        self.p.feed_shape('text', self.p.shape_serial())
        self.control.poll()
        self.assertEqual(self.look(), 'text')
        self.assertFalse(self.p.shape_pushed)


class SourceContractTests(unittest.TestCase):
    def test_the_event_is_the_plugins(self):
        source = (ROOT / 'spike/ownership/plugin.cpp').read_text()
        self.assertEqual(ownership.CURSOR_EVENT, 'xrcursor>>')
        self.assertIn('SHyprIPCEvent{"xrcursor",shape}', source)

    def test_the_hooks_run_the_original_first_and_keep_its_arguments(self):
        source = (ROOT / 'spike/ownership/plugin.cpp').read_text()
        for name, call in (('hookedCursorName', '(renderer,name,force);'), ('hookedCursorSurface', 'renderer,std::move(surface),hotspotX,hotspotY,force);')):
            body = source[source.index(f'void {name}('):]
            body = body[:body.index('\n}\n')]
            self.assertLess(body.index(call), body.index('cursorChanged();'), name)

    def test_the_hooks_are_taken_out_on_unload_before_the_broker_goes(self):
        source = (ROOT / 'spike/ownership/plugin.cpp').read_text()
        body = source[source.index('PLUGIN_EXIT()'):]
        self.assertLess(body.index('unhook(pluginHandle,cursorNameHook)'), body.index('broker.reset()'))
        self.assertLess(body.index('unhook(pluginHandle,cursorSurfaceHook)'), body.index('broker.reset()'))

    @unittest.skipUnless(BENCH_LOCAL, 'needs a local socket directory')
    def test_through_real_sockets_it_follows_both_ways_with_no_hold(self):
        out = subprocess.run([sys.executable, str(Path(__file__).with_name('beam_latency_bench.py')), '--push', '--cycles', '6',
                              '--cpu-seconds', '0', '--json'], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        report = json.loads(out.stdout)
        self.assertLess(report['enter']['median_ms'], 30)  # a busy machine is slower, a poll behind is not this
        self.assertLess(report['leave']['median_ms'], 30)  # the 100 ms hold is gone


class Transport:
    """What the plugin answers on the broker channel."""
    def __init__(self, shape='text'):
        self.shape, self.wires, self.timeouts, self.sessions = shape, [], [], 0
        self.reply = None

    def verified_session(self):
        self.sessions += 1
        return {'id': 'session'}

    def _request(self, wire, timeout=None):
        self.wires.append(wire); self.timeouts.append(timeout)
        j = json.loads(wire[len('j/xrspike '):])
        if j['op'] == 'hello': body = {**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, 'epoch': 'epoch-one', 'result': {'qualified': True, 'fallback': 'handle'}}
        else: body = {**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, 'epoch': 'epoch-one', 'result': {'shape': self.shape}}
        if self.reply is not None: body = self.reply(body)
        return json.dumps(body).encode()


class BrokerClientTests(unittest.TestCase):
    def client(self, shape='text'):
        t = Transport(shape)
        c = OwnershipBrokerClient(t, 'run', 'session', 'DP-1')
        c.require_output_ownership()
        t.wires.clear(); t.timeouts.clear(); t.sessions = 0
        return t, c, c.cursor_reader()

    def test_the_request_is_the_strict_op_and_a_short_wait(self):
        t, c, read = self.client()
        self.assertEqual(read(), 'text')
        self.assertEqual(len(t.wires), 1)
        self.assertTrue(t.wires[0].startswith('j/xrspike '))
        self.assertEqual(json.loads(t.wires[0][10:]), {'schema': 1, 'session': 'session', 'run_id': 'run', 'request_id': 1, 'epoch': 'epoch-one',
                                                       'op': 'cursor_shape', 'payload': {}})
        self.assertEqual(t.timeouts, [ownership.CURSOR_TIMEOUT])
        self.assertLessEqual(ownership.CURSOR_TIMEOUT, 0.25)

    def test_nothing_says_none(self):
        _, _, read = self.client('')
        self.assertIsNone(read())

    def test_every_name_comes_through(self):
        for name in CURSOR_SHAPES:
            _, _, read = self.client(name)
            self.assertEqual(read(), name)

    def test_it_does_no_session_proof_of_its_own(self):
        t, _, read = self.client()
        read(); read()
        self.assertEqual(t.sessions, 0)  # the transport checks the peer on connect and the run's guard does this twice a second

    def test_the_reader_keeps_its_own_ids_so_the_lifecycle_thread_is_not_disturbed(self):
        t, c, read = self.client()
        lifecycle = c.request_id
        read(); read()
        self.assertEqual(c.request_id, lifecycle)
        self.assertEqual([json.loads(w[10:])['request_id'] for w in t.wires], [1, 2])

    def test_the_lifecycle_call_is_unchanged(self):
        t, c, _ = self.client()
        with self.assertRaises(ValueError): c.inspect('12345678-1234-1234-1234-123456789abc')
        self.assertEqual(t.timeouts, [None])  # the default wait
        self.assertEqual(t.sessions, 2)       # and its session proof, either side of the request

    def test_a_reply_that_is_not_exactly_the_shape_is_refused(self):
        bad = [lambda b: {'error': 'ownership blocked'}, lambda b: {**b, 'extra': 1}, lambda b: {**b, 'schema': 2},
               lambda b: {**b, 'session': 'other'}, lambda b: {**b, 'run_id': 'other'}, lambda b: {**b, 'request_id': 99},
               lambda b: {**b, 'request_id': '1'}, lambda b: {**b, 'epoch': 'epoch-two'}, lambda b: {**b, 'result': []},
               lambda b: {**b, 'result': {}}, lambda b: {**b, 'result': {'shape': 'text', 'more': 1}},
               lambda b: {**b, 'result': {'shape': None}}, lambda b: {**b, 'result': {'shape': 7}}, lambda b: {**b, 'result': {'shape': ['text']}}]
        for reply in bad:
            t, _, read = self.client()
            t.reply = reply
            with self.assertRaises(ValueError): read()

    def test_a_name_that_is_not_one_plain_word_is_refused(self):
        for name in json.loads((FIXTURES / 'cursor_shape_names.json').read_text())['refused']:
            t, _, read = self.client(name)
            with self.assertRaises(OwnershipBlocked, msg=repr(name)): read()

    def test_before_the_handshake_there_is_no_reader(self):
        c = OwnershipBrokerClient(Transport(), 'run', 'session', 'DP-1')
        self.assertIsNone(c.cursor_reader())

    def test_an_isolated_run_has_no_such_read(self):
        c = IsolatedBrokerClient(Transport(), 'run', 'session', 'XR-1', 'DP-1')
        c.epoch = 'epoch-one'
        self.assertIsNone(c.cursor_reader())


class ContractTests(unittest.TestCase):
    """Plugin, supervisor and renderer against each other."""
    def test_the_supervisors_request_passes_the_plugins_validator(self):
        if BINARY is None: self.skipTest('no ownership build')
        t = Transport()
        c = OwnershipBrokerClient(t, 'run', 'session', 'DP-1')
        c.require_output_ownership()
        t.wires.clear()
        c.cursor_reader()()
        checked = subprocess.run([str(BINARY), '--validate'], input=t.wires[0][2:].encode(), capture_output=True, timeout=5)
        self.assertEqual(checked.returncode, 0, t.wires[0])

    def test_the_validator_refuses_anything_else_in_that_op(self):
        if BINARY is None: self.skipTest('no ownership build')
        base = {'schema': 1, 'session': 'session', 'run_id': 'run', 'request_id': 1, 'epoch': 'epoch-one', 'op': 'cursor_shape', 'payload': {}}
        for change in ({'payload': {'id': 'x'}}, {'payload': {'shape': 'text'}}, {'schema': 2}, {'schema': 2, 'payload': {'mode': 'isolated-vm'}}, {'op': 'cursor'}):
            wire = 'xrspike ' + json.dumps({**base, **change})
            checked = subprocess.run([str(BINARY), '--validate'], input=wire.encode(), capture_output=True, timeout=5)
            self.assertEqual(checked.returncode, 2, wire)

    def test_both_sides_agree_on_what_a_name_is(self):
        names = json.loads((FIXTURES / 'cursor_shape_names.json').read_text())
        for name in names['accepted']:
            self.assertIsNotNone(ownership.CURSOR_NAME.fullmatch(name), name)
        for name in names['refused']:
            self.assertIsNone(ownership.CURSOR_NAME.fullmatch(name), repr(name))
        if BINARY is None: self.skipTest('no ownership build')
        for name, want in [(n, n) for n in names['accepted']] + [(n, '') for n in names['refused']]:
            got = subprocess.run([str(BINARY), '--cursor-name'], input=name.encode(), capture_output=True, timeout=5)
            self.assertEqual((got.returncode, got.stdout.decode()), (0, want), repr(name))

    def test_every_look_is_in_the_fixture_the_renderer_decodes(self):
        cases = json.loads((FIXTURES / 'overview_pointer_payloads.json').read_text())
        looks = {p['look'] for p in cases['valid'] if 'look' in p}
        self.assertEqual(looks, set(fp.POINTER_LOOKS))
        for p in cases['valid']:
            fp.overview_pointer_frame('run', 'session', 1, p)  # the supervisor's check takes what the renderer takes
        for c in cases['invalid']:
            with self.assertRaises((ValueError, TypeError, KeyError, AttributeError), msg=c['why']):
                fp.overview_pointer_frame('run', 'session', 1, c['payload'])

    def test_every_text_shape_is_a_name_the_broker_can_report(self):
        for name in fp.TEXT_SHAPES: self.assertIsNotNone(ownership.CURSOR_NAME.fullmatch(name), name)

    def test_the_beam_keeps_the_shape_the_renderer_was_tested_on(self):
        beam = json.loads((FIXTURES / 'serif_beam_shape.json').read_text())
        for key, half_w, half_h in (('ring', 7.0, 14.0), ('ink', 5.0, 12.0)):
            self.assertEqual(len(beam[key]), 138)
            xs, ys = [p[0] for p in beam[key]], [p[1] for p in beam[key]]
            self.assertAlmostEqual(min(xs), -half_w); self.assertAlmostEqual(max(xs), half_w)
            self.assertAlmostEqual(min(ys), -half_h); self.assertAlmostEqual(max(ys), half_h)


class EndToEndTests(UnifiedBase):
    def test_plugin_answer_to_renderer_frame(self):
        t = Transport('xterm')
        c = OwnershipBrokerClient(t, 'run', 'session', 'DP-1')
        c.require_output_ownership()
        read = c.cursor_reader()
        self.at(*MID1)
        self.clock.now += 0.03
        self.p.feed_shape(read())
        payload = last(frames(self.control.poll(), ('overview_pointer',)), 'overview_pointer')
        self.assertEqual(payload['look'], 'text')
        fp.overview_pointer_frame('run', 'session', 1, payload)


class WiringTests(unittest.TestCase):
    def run_owned(self, broker):
        journal = mock.MagicMock(operations=[], broker=None, run_id='run', session='session')
        with mock.patch.object(managed, 'create_outputs'), mock.patch.object(managed, '_launch', return_value=0), \
                mock.patch.object(managed, 'recover_restoring', return_value=mock.Mock(complete=True, remaining=[])), \
                mock.patch.object(managed, 'view_controls') as controls:
            managed._run_owned(mock.MagicMock(instance='i'), journal, broker, 'DP-2', 'DP-1', [], capture=['XR-1'], guard=lambda: None)
        return controls.call_args.kwargs['cursor_shape']

    def test_a_managed_run_reads_through_its_own_broker(self):
        broker = OwnershipBrokerClient(Transport(), 'run', 'session', 'DP-1')
        broker.require_output_ownership()
        self.assertEqual(self.run_owned(broker)(), 'text')

    def test_a_broker_with_no_such_read_leaves_the_dot(self):
        self.assertIsNone(self.run_owned(mock.Mock(spec=['epoch', 'fallback'], epoch='e', fallback='f')))

    def test_view_controls_hands_it_to_the_sampler(self):
        from tools.native_spike.profiles import LayoutStore
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else {'x': X1 + 960, 'y': 540} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        reads = FakeReads()
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json'), cursor_shape=reads) as control:
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and control.overview_pointer.sent.get('look') != 'text':
                control.poll(); time.sleep(0.01)
            self.assertEqual(control.overview_pointer.sent.get('look'), 'text')


if __name__ == '__main__':
    unittest.main()
