"""Overview corner resize (resize.py): hit test, drag math, modal mouse binds, hover/drag state, pointer poller, wiring.

Offline only: fake clocks, fake compositor clients and socketpairs. No compositor, renderer binary or glasses.
"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import resize, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

NAMES = ['XR-1', 'XR-2', 'XR-3']
# Three managed outputs as plan_outputs lays them out right of a 2560-wide physical monitor; XR-2 at 125 % scale.
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.25},
            {'name': 'XR-3', 'x': 6528, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
LIVE_BIND_ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-integrate/tools/native_spike/xr_control.py',
                  '/run/user/1000/xr-workspace-spike/' + 'a' * 32 + '/control.sock', 'a' * 32, 'e' * 64)


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class MemoryPlacements:
    def __init__(self):
        self.saved = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        self.saves = 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements):
        self.saved = [dict(p) for p in placements]; self.saves += 1


class GeometryTests(unittest.TestCase):
    def test_outputs_are_logical_rectangles_in_run_order(self):
        self.assertEqual([o['index'] for o in OUTPUTS], [0, 1, 2])
        self.assertEqual((OUTPUTS[1]['x'], OUTPUTS[1]['width'], OUTPUTS[1]['height']), (4480, 2048.0, 1152.0))
        # Missing or malformed outputs are skipped, never guessed.
        broken = [dict(MONITORS[1], width='1920'), dict(MONITORS[3], scale=0)]
        self.assertEqual(resize.outputs_from_monitors(broken, NAMES), [])
        self.assertEqual([o['index'] for o in resize.outputs_from_monitors(MONITORS[2:3], NAMES)], [1])
        self.assertEqual(resize.outputs_from_monitors(None, NAMES), [])

    def test_zone_size_is_a_share_of_the_short_side_clamped(self):
        self.assertEqual(resize.zone_size(1920, 1080), 86)
        self.assertEqual(resize.zone_size(640, 200), resize.ZONE_MIN)
        self.assertEqual(resize.zone_size(7680, 4320), resize.ZONE_MAX)

    def test_each_corner_of_each_desktop_and_nothing_else(self):
        x1 = 2560
        cases = {(x1 + 5, 5): (0, 'tl'), (x1 + 1915, 5): (0, 'tr'), (x1 + 5, 1075): (0, 'bl'), (x1 + 1919, 1079): (0, 'br'),
                 (4480 + 2040, 1150): (1, 'br'), (6528 + 1, 1): (2, 'tl')}
        for pointer, expected in cases.items():
            with self.subTest(pointer): self.assertEqual(resize.corner_at(pointer, OUTPUTS), expected)
        for pointer in ((x1 + 960, 540), (x1 + 960, 5), (x1 + 5, 540), (100, 100), (x1 + 1920 + 2048 + 1920 + 1, 5), (x1 + 5, 1080)):
            with self.subTest(pointer): self.assertIsNone(resize.corner_at(pointer, OUTPUTS))
        self.assertIsNone(resize.corner_at(None, OUTPUTS))
        self.assertIsNone(resize.corner_at((x1 + 5, 5), []))

    def test_drag_outward_grows_inward_shrinks_from_every_corner(self):
        w, h = 1920, 1080
        half = (w + h) / 2
        out = {'tl': (-1, -1), 'tr': (1, -1), 'bl': (-1, 1), 'br': (1, 1)}
        for corner, (sx, sy) in out.items():
            with self.subTest(corner):
                self.assertEqual(resize.drag_distance(100, corner, 0, 0, w, h), 100)
                self.assertEqual(resize.drag_distance(100, corner, sx * half, 0, w, h), 50)  # one axis is enough
                self.assertEqual(resize.drag_distance(100, corner, 0, sy * half / 4, w, h), 80)
                self.assertEqual(resize.drag_distance(100, corner, -sx * half / 4, 0, w, h), 133)
                self.assertLess(resize.drag_distance(100, corner, sx * 30, sy * 30, w, h), 100)
                self.assertGreater(resize.drag_distance(100, corner, -sx * 30, -sy * 30, w, h), 100)

    def test_drag_is_clamped_to_the_distance_bounds(self):
        lo, hi = PLACEMENT_BOUNDS['distance']
        self.assertEqual(resize.drag_distance(100, 'br', 100000, 100000, 1920, 1080), lo)
        self.assertEqual(resize.drag_distance(100, 'br', -100000, -100000, 1920, 1080), hi)
        self.assertEqual(resize.drag_distance(60, 'tl', -100000, 0, 1920, 1080), lo)
        with self.assertRaises(ValueError): resize.drag_distance(100, 'middle', 0, 0, 1920, 1080)


class BindTests(unittest.TestCase):
    def test_press_and_release_binds_on_the_left_button_only(self):
        lua = resize.install_resize_binds_lua(*LIVE_BIND_ARGS)
        self.assertTrue(lua.startswith(resize.remove_resize_binds_lua()))  # replaces a surviving set, never doubled
        self.assertEqual(lua.count('hl.bind('), 2)
        self.assertEqual(lua.count('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "resize_press"))'), 1)
        self.assertEqual(lua.count('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "resize_release"), { release = true })'), 1)
        self.assertIn('local c = "{} -I {} {} {} {} "'.format(*LIVE_BIND_ARGS), lua)
        self.assertNotIn('os.execute', lua); self.assertNotIn('io.', lua)
        self.assertLess(len(lua), 4096)  # HyprlandClient._eval cap; a separate eval from the run binds

    def test_unsafe_arguments_are_refused(self):
        for i, bad in ((0, 'python3'), (1, '/x/"; os.execute("y'), (3, 'run id'), (4, '')):
            args = list(LIVE_BIND_ARGS); args[i] = bad
            with self.subTest(bad), self.assertRaises(ValueError): resize.install_resize_binds_lua(*args)

    def test_the_bind_helper_knows_the_ops_and_the_fixed_removal_text(self):
        self.assertEqual(xr_control.OPS[-2:], resize.RESIZE_OPS)
        self.assertEqual(xr_control.REMOVE_RESIZE_BINDS, resize.remove_resize_binds_lua())
        self.assertFalse(set(resize.RESIZE_OPS) & set(view.USER_OPS))  # not panel buttons
        for op in resize.RESIZE_OPS:
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
            with self.assertRaises(ValueError):  # no payload smuggling (no sizes from the bind side)
                decode_control((json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op,
                                            'payload': {'distance': 50}}) + '\n').encode())

    def test_dead_run_resize_click_releases_the_left_button(self):
        dead = '/nonexistent/control.sock'
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'resize_press']), 1)
            drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'zoom_in']), 1)
            self.assertEqual(drop.call_count, 1)

    def test_hyprland_client_sends_generated_lua_only(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.install_resize_binds(*LIVE_BIND_ARGS); client.remove_resize_binds()
        self.assertEqual(sent, ['/eval ' + resize.install_resize_binds_lua(*LIVE_BIND_ARGS), '/eval ' + resize.remove_resize_binds_lua()])

    def test_run_bind_set_and_allowlists_are_unchanged(self):
        from tools.native_spike import hyprland as h
        self.assertNotIn('mouse', view.install_binds_lua(*LIVE_BIND_ARGS))
        self.assertEqual(h.QUERY, {'monitors', 'workspaces', 'clients', 'activewindow', 'getoption', 'cursorpos'})


class CornerResizeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clock, self.store = Clock(), MemoryPlacements()
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                                        None, placements=self.store, clock=self.clock)
        self.addCleanup(self.control.join_focus, 2)
        self.r = self.control.resize = resize.CornerResize(self.control, clock=self.clock)
        self.addCleanup(self.r.close)

    def at(self, x, y, advance=0.02):
        self.clock.now += advance
        self.r.feed((x, y), OUTPUTS)
        return frames(self.control.poll())

    def op(self, name):
        return frames(self.control.on_message({'op': name, 'payload': {}}))

    def test_hover_shows_a_handle_and_wants_the_binds_leaving_hides_it(self):
        self.assertEqual(self.at(2560 + 960, 540), [])
        self.assertFalse(self.r.binds_wanted)
        shown = self.at(2560 + 1910, 1070)
        self.assertEqual([f['op'] for f in shown], ['resize_handle'])
        self.assertEqual(shown[0]['payload'], {'visible': True, 'desktop': 0, 'corner': 'br', 'active': False})
        self.assertTrue(self.r.binds_wanted)
        self.assertEqual(self.at(2560 + 1905, 1065), [])  # same corner: nothing new
        moved = self.at(4480 + 5, 5)
        self.assertEqual(moved[0]['payload'], {'visible': True, 'desktop': 1, 'corner': 'tl', 'active': False})
        hidden = self.at(4480 + 500, 500)
        self.assertEqual(hidden[0]['payload']['visible'], False)
        self.assertFalse(self.r.binds_wanted)
        self.assertEqual(self.store.saves, 0)  # hovering changes nothing

    def test_drag_resizes_live_saves_debounced_and_on_release(self):
        self.at(2560 + 1915, 1075)
        pressed = self.op('resize_press')
        self.assertEqual(pressed[-1]['payload'], {'visible': True, 'desktop': 0, 'corner': 'br', 'active': True})
        out = self.at(2560 + 1915 + 300, 1075)  # outward onto XR-2: global coordinates keep counting
        self.assertEqual([f['op'] for f in out], ['placement'])
        first = out[0]['payload']['desktops'][0]['distance']
        self.assertLess(first, 100)
        self.assertEqual(out[0]['payload']['desktops'][1:], [DEFAULT_PLACEMENT] * 2)
        self.assertTrue(self.r.binds_wanted)  # a drag keeps them even off the corner, for the release
        self.assertIsNotNone(self.control.save_due)
        self.assertEqual(self.store.saves, 0)
        bigger = self.at(2560 + 1915 + 600, 1075)
        self.assertLess(bigger[0]['payload']['desktops'][0]['distance'], first)
        released = self.op('resize_release')
        self.assertEqual(released[-1]['payload']['active'], False)
        self.assertEqual(self.store.saves, 1)
        self.assertEqual(self.store.saved[0]['distance'], bigger[0]['payload']['desktops'][0]['distance'])
        self.assertIsNone(self.control.save_due)
        self.assertEqual(self.control.status_reply({})['placements'][0]['distance'], self.store.saved[0]['distance'])  # panel sync
        self.assertEqual(released[-1]['payload'], {'visible': False, 'desktop': 0, 'corner': 'br', 'active': False})  # off the corner
        self.assertFalse(self.r.binds_wanted)

    def test_debounced_save_lands_while_still_dragging(self):
        self.at(2560 + 5, 5); self.op('resize_press')
        self.at(2560 + 5 + 200, 5 + 200)  # inward from the top-left: smaller
        self.assertGreater(self.control.state.placements[0]['distance'], 100)
        self.clock.now += view.MENU_SAVE_DELAY + 0.01
        self.control.poll()
        self.assertEqual(self.store.saves, 1)
        self.assertGreater(self.store.saved[0]['distance'], 100)

    def test_drag_stops_at_the_bounds_and_keeps_other_placement_fields(self):
        self.control.state.placements[2] = {**DEFAULT_PLACEMENT, 'yaw': 20, 'curvature': 40}
        self.at(6528 + 1915, 1075); self.op('resize_press')
        self.at(6528 + 1915 + 5000, 1075)
        self.assertEqual(self.control.state.placements[2], {**DEFAULT_PLACEMENT, 'yaw': 20, 'curvature': 40, 'distance': 50})
        self.assertEqual(self.at(6528 + 1915 + 9000, 1075), [])  # pinned at the bound: nothing more is sent
        self.at(6528 - 8000, 1075)
        self.assertEqual(self.control.state.placements[2]['distance'], 200)

    def test_press_off_a_corner_or_while_zoomed_does_nothing(self):
        self.at(2560 + 960, 540)
        self.assertEqual(self.op('resize_press'), [])
        self.assertIsNone(self.r.drag)
        self.at(2560 + 5, 5)
        self.control.on_message({'op': 'zoom_in', 'payload': {}})
        hidden = self.at(2560 + 6, 6)
        self.assertEqual(hidden[-1]['payload']['visible'], False)
        self.assertEqual(self.op('resize_press'), [])
        self.assertIsNone(self.r.drag)
        self.assertFalse(self.r.binds_wanted)

    def test_zooming_in_mid_drag_ends_and_saves_it(self):
        self.at(2560 + 5, 1075); self.op('resize_press')
        self.at(2560 + 5 - 400, 1075)
        size = self.control.state.placements[0]['distance']
        self.control.on_message({'op': 'zoom_in', 'payload': {}})
        out = self.at(2560 + 5 - 800, 1075)
        self.assertIsNone(self.r.drag)
        self.assertEqual(out[-1]['payload']['visible'], False)
        self.assertEqual(self.store.saved[0]['distance'], size)  # the zoom did not take the last move
        self.assertFalse(self.r.binds_wanted)

    def test_a_lost_release_is_finished_by_the_next_press(self):
        self.at(2560 + 5, 5); self.op('resize_press')
        self.at(2560 + 5 - 300, 5)
        self.at(6528 + 5, 5)
        self.op('resize_press')  # the release never arrived
        self.assertEqual(self.store.saves, 1)
        self.assertEqual((self.r.drag['index'], self.r.drag['corner']), (2, 'tl'))

    def test_idle_pointer_hides_the_handle_and_releases_the_button(self):
        self.at(2560 + 5, 5)
        self.assertTrue(self.r.binds_wanted)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.assertEqual(frames(self.control.poll()), [])
        self.clock.now += 1.01
        hidden = frames(self.control.poll())
        self.assertEqual(hidden[-1]['payload']['visible'], False)
        self.assertFalse(self.r.binds_wanted)
        self.assertEqual(self.at(2560 + 6, 6)[-1]['payload']['visible'], True)  # moving brings it back

    def test_idle_mid_drag_ends_and_saves_it(self):
        self.at(2560 + 5, 5); self.op('resize_press'); self.at(2560 + 5 - 200, 5)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S
        self.control.poll()
        self.assertIsNone(self.r.drag)
        self.assertEqual(self.store.saves, 1)
        self.assertFalse(self.r.binds_wanted)

    def test_open_menu_follows_the_drag(self):
        self.control.on_message({'op': 'menu_toggle', 'payload': {}})
        self.at(2560 + 1915, 1075); self.op('resize_press')
        out = self.at(2560 + 1915 + 300, 1075)
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
        self.assertEqual(out[1]['payload']['placements'][0]['distance'], out[0]['payload']['desktops'][0]['distance'])

    def test_a_press_counts_as_input_for_the_overlays(self):
        self.control.on_message({'op': 'help', 'payload': {}})
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.at(2560 + 5, 5, advance=0); self.op('resize_press'); self.op('resize_release')
        self.clock.now += 2
        self.assertTrue(self.control.help_visible)
        self.control.poll()
        self.assertTrue(self.control.help_visible)

    def test_without_resize_the_ops_are_ignored(self):
        self.control.resize = None
        self.assertIsNone(self.control.on_message({'op': 'resize_press', 'payload': {}}))
        self.assertIsNone(self.control.poll())

    def test_config_reload_marks_the_binds_dropped(self):
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.r.binds_dropped)

    def test_pointer_samples_wake_the_supervisor_loop(self):
        self.assertTrue(self.r.feed((1, 1), OUTPUTS))
        self.assertFalse(self.r.feed((1, 1), OUTPUTS))  # unchanged: no wake-up
        self.assertEqual(self.r.wake_reader.recv(16), b'.')
        self.r.feed((2, 2), OUTPUTS); self.r.drain()
        with self.assertRaises(BlockingIOError): self.r.wake_reader.recv(16)


class FakeClient:
    def __init__(self, pointer=(2565, 5)):
        self.pointer, self.calls, self.lock = pointer, [], threading.Lock()
        self.fail_install = False
    def query(self, kind, argument=None, timeout=2.0):
        with self.lock: self.calls.append(kind)
        if kind == 'monitors': return MONITORS
        if kind == 'cursorpos':
            if self.pointer is None: raise TimeoutError('gone')
            return {'x': self.pointer[0], 'y': self.pointer[1]}
        raise ValueError(kind)
    def install_resize_binds(self, *args):
        with self.lock: self.calls.append(('install', args))
        if self.fail_install: raise ValueError('rejected')
    def remove_resize_binds(self):
        with self.lock: self.calls.append('remove')
    def count(self, item):
        with self.lock: return self.calls.count(item)


class PointerPollerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.r = self.control.resize = resize.CornerResize(self.control); self.addCleanup(self.r.close)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return True
            time.sleep(0.005)
        return predicate()

    def test_feeds_samples_and_installs_then_removes_the_binds(self):
        client = FakeClient()
        poller = resize.PointerPoller(client, NAMES, self.r, LIVE_BIND_ARGS, period=0.005).start()
        self.assertTrue(self.wait(lambda: self.r._sample == ((2565, 5), OUTPUTS)))
        self.control.poll()
        self.assertTrue(self.r.binds_wanted)
        self.assertTrue(self.wait(lambda: client.count(('install', LIVE_BIND_ARGS)) == 1))
        client.pointer = (3000, 500)
        self.assertTrue(self.wait(lambda: self.r._sample[0] == (3000, 500)))
        self.control.poll()
        self.assertTrue(self.wait(lambda: client.count('remove') == 1))
        poller.stop()
        self.assertEqual(client.count('remove'), 2)  # stop always removes once more if they were ever installed
        self.assertEqual(client.count(('install', LIVE_BIND_ARGS)), 1)

    def test_no_pointer_reads_while_zoomed(self):
        client = FakeClient()
        self.control.state.zoomed = True
        poller = resize.PointerPoller(client, NAMES, self.r, LIVE_BIND_ARGS, period=0.005).start()
        time.sleep(0.15)
        poller.stop()
        self.assertEqual(client.count('cursorpos'), 0)
        self.assertEqual(client.count('remove'), 0)  # never installed, nothing to remove

    def test_read_errors_and_rejected_installs_are_survived(self):
        client = FakeClient(pointer=None)
        poller = resize.PointerPoller(client, NAMES, self.r, LIVE_BIND_ARGS, period=0.005).start()
        self.assertTrue(self.wait(lambda: poller.errors >= 3))
        client.pointer, client.fail_install = (2565, 5), True
        self.assertTrue(self.wait(lambda: self.r._sample[0] == (2565, 5)))
        self.control.poll()
        self.assertTrue(self.wait(lambda: client.count(('install', LIVE_BIND_ARGS)) >= 2))  # retried
        self.assertFalse(poller.installed)
        client.fail_install = False
        self.assertTrue(self.wait(lambda: poller.installed))
        poller.stop()
        self.assertFalse(poller.installed)

    def test_reload_reinstalls_wanted_binds(self):
        client = FakeClient()
        poller = resize.PointerPoller(client, NAMES, self.r, LIVE_BIND_ARGS, period=0.005).start()
        self.assertTrue(self.wait(lambda: self.r._sample[0] == (2565, 5)))
        self.control.poll()
        self.assertTrue(self.wait(lambda: client.count(('install', LIVE_BIND_ARGS)) == 1))
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.wait(lambda: client.count(('install', LIVE_BIND_ARGS)) == 2))
        poller.stop()


class WiringTests(unittest.TestCase):
    def test_supervisor_loop_wakes_on_a_pointer_sample(self):
        from tools.native_spike.supervisor import Supervisor
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        r = control.resize = resize.CornerResize(control); self.addCleanup(r.close)
        control.wake = r.wake_reader
        control.server = mock.Mock()
        polled = []
        real_poll = control.poll
        control.poll = lambda now=None: polled.append(now) or real_poll(now)
        child = mock.Mock(); child.poll.side_effect = [None] * 6 + [0] * 4
        r.feed((2565, 5), OUTPUTS)
        a, b = socket.socketpair()
        with a, b, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as dummy:
            control.server.socket = dummy
            Supervisor(child, lambda: 'done', control=control).run(a, interval=0.05)
            b.setblocking(False)
            sent = b.recv(65536)
        self.assertTrue(polled)
        self.assertEqual(frames(sent)[0]['payload'], {'visible': True, 'desktop': 0, 'corner': 'tl', 'active': False})
        with self.assertRaises(BlockingIOError): r.wake_reader.recv(16)  # drained

    def test_view_controls_starts_the_poller_and_removes_the_binds_at_run_end(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        state = {'pointer': (2565, 5)}
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': state['pointer'][0], 'y': state['pointer'][1]} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            self.assertIsInstance(control.resize, resize.CornerResize)
            self.assertIs(control.wake, control.resize.wake_reader)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.install_resize_binds.called:
                control.poll(); time.sleep(0.01)
            self.assertTrue(client.install_resize_binds.called)
            self.assertEqual(client.install_resize_binds.call_args.args, client.install_view_binds.call_args.args)
        client.remove_resize_binds.assert_called()
        names = [c[0] for c in client.method_calls]
        self.assertLess(max(i for i, n in enumerate(names) if n == 'remove_resize_binds'), names.index('remove_view_binds'))
        self.assertEqual(control.resize.wake_reader.fileno(), -1)  # closed with the run


if __name__ == '__main__':
    unittest.main()
