"""A glasses USB drop keeps the session: what counts as one, how long it waits, what it does when they return."""
import json
import os
import socket
import struct
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.native_spike import managed, supervisor, survive, video, view
from tools.native_spike.profiles import LayoutStore


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, s): self.now += s


def bus(tmp, *devices):
    root = Path(tmp)
    for name, vendor, product, busnum, devnum in devices:
        d = root / name; d.mkdir()
        for k, v in (('idVendor', vendor), ('idProduct', product), ('busnum', busnum), ('devnum', devnum)):
            (d / k).write_text(f'{v}\n')
    return root


class UsbIdentityTests(unittest.TestCase):
    def test_the_glasses_by_vendor_and_product_never_by_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = bus(tmp, ('5-2', '1a86', '8091', 5, 3), ('5-2.1', '35ca', '1104', 5, 4), ('5-2.3', '35ca', '1102', 5, 6))
            (root / '5-2:1.0').mkdir()  # an interface has no ids
            self.assertEqual(survive.usb_identity(root), (5, 4))

    def test_absent_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(survive.usb_identity(bus(tmp, ('1-1', '1a86', '8091', 1, 2))))
        self.assertIsNone(survive.usb_identity(Path('/nonexistent')))


class ShouldHoldTests(unittest.TestCase):
    def test_a_lost_output_is_a_drop_whichever_noticed_first(self):
        for code, reason in ((2, None), (1, None), (None, 'guard-failed'), (-15, 'guard-failed'), (1, 'renderer-closed'), (-9, 'heartbeat-timeout')):
            with self.subTest(code=code, reason=reason):
                self.assertTrue(survive.should_hold(code, reason, output_ok=False, usb_before=(5, 4), usb_now=None))

    def test_the_bus_moving_is_a_drop_even_with_the_output_back(self):
        self.assertTrue(survive.should_hold(1, None, output_ok=True, usb_before=(5, 4), usb_now=(5, 9)))

    def test_a_clean_stop_is_never_held(self):
        self.assertFalse(survive.should_hold(0, None, output_ok=False, usb_before=(5, 4), usb_now=None))

    def test_a_person_stopping_is_never_held(self):
        for reason in ('interrupted',):
            self.assertFalse(survive.should_hold(-15, reason, output_ok=False, usb_before=(5, 4), usb_now=None))

    def test_a_renderer_crash_with_the_glasses_fine_is_not_a_drop(self):
        self.assertFalse(survive.should_hold(-11, None, output_ok=True, usb_before=(5, 4), usb_now=(5, 4)))

    def test_nothing_to_compare_is_not_evidence(self):
        self.assertFalse(survive.should_hold(1, None, output_ok=True, usb_before=None, usb_now=(5, 4)))


class HoldsTests(unittest.TestCase):
    def test_a_bouncing_cable_ends_the_session_rather_than_looping(self):
        clock = Clock(); holds = survive.Holds(clock=clock)
        for _ in range(survive.MAX_HOLDS):
            self.assertTrue(holds.begin()); clock.now += 20; holds.finish(True)
        self.assertFalse(holds.begin())

    def test_old_drops_stop_counting(self):
        clock = Clock(); holds = survive.Holds(clock=clock)
        for _ in range(survive.MAX_HOLDS):
            holds.begin(); holds.finish(True)
        clock.now += survive.HOLD_WINDOW_S + 1
        self.assertTrue(holds.begin())

    def test_the_record(self):
        clock = Clock(); holds = survive.Holds(clock=clock)
        holds.begin(); clock.now += 7; holds.finish(True)
        holds.begin(); clock.now += 9; holds.finish(False)
        self.assertEqual([(h['seconds'], h['resumed']) for h in holds.record], [(7.0, True), (9.0, False)])
        self.assertEqual(holds.survived, 1)


class WaitTests(unittest.TestCase):
    def wait(self, ready, **kw):
        clock = Clock(); self.calls = []
        out = survive.wait_for_glasses(ready, restore=kw.pop('restore', lambda: self.calls.append('restore')),
                                       serve=lambda s: clock.sleep(s), clock=clock, **kw)
        return out, clock.now

    def test_comes_back_once_settled(self):
        states = iter([False] * 8 + [True] * 100)
        out, took = self.wait(lambda: next(states))
        self.assertTrue(out); self.assertEqual(self.calls, ['restore'])
        self.assertGreaterEqual(took, survive.SETTLE_S); self.assertLess(took, survive.SETTLE_S + 4)

    def test_a_flicker_starts_the_settling_again(self):
        # One second up, gone again, as the hub does between the two drops of a pair.
        states = iter(([True] * 2 + [False]) * 2 + [True] * 100)
        out, took = self.wait(lambda: next(states))
        self.assertTrue(out); self.assertEqual(self.calls, ['restore']); self.assertGreater(took, survive.SETTLE_S + 1)

    def test_gives_up_at_the_limit(self):
        out, took = self.wait(lambda: False, limit=120)
        self.assertFalse(out); self.assertEqual(self.calls, []); self.assertGreaterEqual(took, 120)

    def test_a_restore_that_fails_waits_and_tries_again(self):
        tries = []
        def restore():
            tries.append(1)
            if len(tries) < 3: raise ValueError('session mode was not read back')
        out, _ = self.wait(lambda: True, restore=restore)
        self.assertTrue(out); self.assertEqual(len(tries), 3)

    def test_stop_during_the_wait_gets_out(self):
        def serve(s): raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            survive.wait_for_glasses(lambda: False, restore=lambda: None, serve=serve)


class ReviveTrackingTests(unittest.TestCase):
    class T:
        def __init__(self, running, fails=False): self.running, self.fails, self.started = running, fails, 0
        def status(self, runtime): return {'running': self.running, 'calibrated': False}
        def restart_same(self, runtime):
            self.started += 1
            if self.fails: raise ValueError('did not calibrate')

    def test_a_driver_still_reconnecting_is_left_alone(self):
        t = self.T(True); self.assertIs(survive.revive_tracking('rt', t), True); self.assertEqual(t.started, 0)

    def test_a_dead_driver_is_started(self):
        t = self.T(False); self.assertIs(survive.revive_tracking('rt', t), True); self.assertEqual(t.started, 1)

    def test_tracking_that_will_not_start_does_not_end_the_session(self):
        t = self.T(False, fails=True); self.assertIs(survive.revive_tracking('rt', t), False)


class Loop:
    """keep_renderer against a scripted run: each entry is one renderer's end (code, stop reason, output there after it)."""
    def __init__(self, renderers, *, restore_fails=0, glasses_back=True, revive=True):
        self.renderers, self.launches, self.told = list(renderers), 0, []
        self.restores, self.restore_fails = 0, restore_fails
        self.glasses_back, self.report, self.output, self.gone = glasses_back, {}, True, False
        self.control = None
        self.holds = survive.Holds(); self.holds.revive = lambda: revive
        self.usb = (5, 4)

    def query(self, kind):
        if self.gone: raise OSError('compositor gone')
        return [{'name': 'DP-1', 'disabled': False}] + ([{'name': 'DP-2', 'disabled': False}] if self.output else [])

    def launch(self):
        self.launches += 1
        code, reason, output = self.renderers.pop(0)
        self.report.clear()
        if reason: self.report['stop_reason'] = reason
        self.output = output
        if not output: self.usb = None
        return code

    def restore(self):
        self.restores += 1
        if self.restores <= self.restore_fails: raise ValueError('not read back')

    def run(self, serve=None):
        def patched(control, seconds):
            if serve is not None: serve()
            if self.glasses_back: self.output, self.usb = True, (5, 9)
            Loop.clock.now += seconds
        Loop.clock = Clock()
        self.holds.clock = Loop.clock
        with patch.object(managed, 'serve_without_renderer', patched), patch.object(survive, 'usb_identity', lambda *_: self.usb), \
             patch.object(survive, 'monitors_of', lambda client: client.query('monitors')), \
             patch.object(survive, 'time', types.SimpleNamespace(monotonic=Loop.clock)):
            return managed.keep_renderer(self.launch, self, 'DP-2', self.report, self.control, self.restore, self.holds, tell=self.told.append)


class KeepRendererTests(unittest.TestCase):
    def test_a_clean_end_is_one_renderer(self):
        loop = Loop([(0, None, True)])
        self.assertEqual(loop.run(), 0); self.assertEqual(loop.launches, 1); self.assertEqual(loop.told, [])

    def test_a_drop_starts_a_new_renderer_and_the_run_ends_clean(self):
        loop = Loop([(2, None, False), (0, None, True)])
        self.assertEqual(loop.run(), 0)
        self.assertEqual((loop.launches, loop.restores), (2, 1))
        self.assertEqual(loop.holds.survived, 1)
        self.assertEqual(len(loop.told), 2)

    def test_the_guard_noticing_first_is_the_same_drop(self):
        loop = Loop([(-15, 'guard-failed', False), (0, None, True)])
        self.assertEqual(loop.run(), 0); self.assertEqual(loop.launches, 2)

    def test_two_drops_in_one_session(self):
        loop = Loop([(2, None, False), (2, None, False), (0, None, True)])
        self.assertEqual(loop.run(), 0); self.assertEqual(loop.holds.survived, 2)

    def test_a_person_stopping_ends_it_at_once(self):
        loop = Loop([(-15, 'interrupted', False)])
        self.assertEqual(loop.run(), -15); self.assertEqual(loop.launches, 1); self.assertEqual(loop.restores, 0)

    def test_a_renderer_crash_with_the_glasses_fine_ends_as_a_crash(self):
        loop = Loop([(-11, None, True)])
        self.assertEqual(loop.run(), -11); self.assertEqual(loop.launches, 1)

    def test_glasses_that_never_return_end_the_run_the_ordinary_way(self):
        loop = Loop([(2, None, False)], glasses_back=False)
        self.assertEqual(loop.run(), 2)
        self.assertEqual(loop.launches, 1); self.assertEqual(loop.holds.record[0]['resumed'], False)
        self.assertIn('did not come back', loop.told[-1])

    def test_stop_during_a_hold_is_a_stop_not_an_error(self):
        def press(): raise KeyboardInterrupt
        loop = Loop([(2, None, False)])
        self.assertEqual(loop.run(serve=press), 2)
        self.assertEqual(loop.report['stop_reason'], 'interrupted'); self.assertEqual(loop.holds.record[0]['resumed'], False)

    def test_the_compositor_going_too_is_not_a_hold(self):
        loop = Loop([(2, None, False)])
        real = loop.launch
        def launch():
            code = real(); loop.gone = True; return code
        loop.launch = launch
        self.assertEqual(loop.run(), 2); self.assertEqual(loop.told, [])

    def test_too_many_drops_give_up(self):
        loop = Loop([(2, None, False)] * (survive.MAX_HOLDS + 1))
        self.assertEqual(loop.run(), 2)
        self.assertEqual(loop.launches, survive.MAX_HOLDS + 1); self.assertIn('Check the cable', loop.told[-1])

    def test_no_restore_is_the_old_single_renderer(self):
        loop = Loop([(2, None, False)])
        self.assertEqual(managed.keep_renderer(loop.launch, loop, 'DP-2', loop.report, None, None, loop.holds, tell=loop.told.append), 2)
        self.assertEqual(loop.launches, 1)

    def test_no_head_tracking_after_a_drop_is_said_and_keeps_the_session(self):
        loop = Loop([(2, None, False), (0, None, True)], revive=False)
        self.assertEqual(loop.run(), 0)
        self.assertTrue(any('without head tracking' in t for t in loop.told))


class SecondRendererTests(unittest.TestCase):
    """The real _launch and a real ViewControl, twice in one run: everything but the process itself."""
    def test_the_second_renderer_gets_a_whole_start(self):
        peer = struct.unpack('3i', socket.socketpair()[0].getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        class Client:
            _peer = peer
            @staticmethod
            def query(kind): return [{'name': 'DP-2', 'disabled': False}] if self.ends else [{'name': 'DP-1', 'disabled': False}]
        self.ends = 1
        spawned, read_ends = [], []
        class Child:
            def __init__(self, code): self.code = code
            def poll(self): return self.code
        def spawn(argv, **kwargs):
            spawned.append(argv)
            read_ends.append(socket.socket(fileno=os.dup(kwargs['pass_fds'][0])))
            return Child(2 if len(spawned) == 1 else 0)
        state = view.ViewState(['XR-1', 'XR-2'])
        here, there = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        control = view.ViewControl(state, 'run', 'session', lambda name: None, LayoutStore(Path(tempfile.mkdtemp()) / 'l.json'),
                                   types.SimpleNamespace(socket=here))
        report = {}
        def launch():
            report.clear()
            _, wayland = socket.socketpair()
            return supervisor._launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=spawn, capture=['XR-1', 'XR-2'],
                                      control=control, report=report)
        def restore(): self.ends = 1
        holds = survive.Holds(); holds.revive = lambda: True
        with patch.object(survive, 'usb_identity', lambda *_: (5, 4)), patch.object(survive, 'monitors_of', lambda c: c.query('monitors')), \
             patch.object(managed, 'serve_without_renderer', lambda c, s: setattr(self, 'ends', 1)), patch.object(survive, 'SETTLE_S', 0):
            real = launch
            def first_loses_the_output():
                code = real()
                self.ends = 0 if code else 1
                return code
            code = managed.keep_renderer(first_loses_the_output, Client, 'DP-2', report, control, restore, holds, tell=lambda t: None)
        self.assertEqual(code, 0)
        self.assertEqual(len(spawned), 2); self.assertEqual(spawned[0], spawned[1])
        ids = []
        for end in read_ends:
            end.settimeout(0.2)
            lines = end.recv(1 << 20).decode().splitlines()
            frames = [json.loads(line) for line in lines]
            self.assertEqual([f['op'] for f in frames][:2], ['start', 'view'])
            ids.append([f['request_id'] for f in frames[1:]])
            end.close()
        self.assertGreater(min(ids[1]), max(ids[0]), 'the replay floor: a later frame always has a higher id')
        here.close(); there.close()


class ServeWithoutRendererTests(unittest.TestCase):
    def test_the_panel_gets_its_status_and_view_ops_are_dropped(self):
        import socket
        server_socket, client_socket = socket.socketpair()
        answered = []
        class Server:
            socket = server_socket
            queue = types.SimpleNamespace(pop=lambda: None)
            def receive(self, conn): return {'op': 'status', 'request_id': 1, 'run_id': 'r', 'session': 's'}
        class Control:
            server = Server()
            def status_reply(self, status): answered.append(status); return {'view': {}}
        with client_socket, server_socket, patch.object(supervisor, 'control_request', lambda c, s: c.status_reply(s)):
            client_socket.send(b'x')
            supervisor.serve_without_renderer(Control(), 0.01)
        self.assertEqual(answered, [{}])


class RestartedRendererTests(unittest.TestCase):
    def test_the_rolls_go_again_and_the_help_card_is_shut(self):
        pointer = types.SimpleNamespace(roll_sent={'roll': [1]})
        control = types.SimpleNamespace(help_visible=True, overview_pointer=pointer)
        view.ViewControl.renderer_restarted(control)
        self.assertIsNone(pointer.roll_sent); self.assertFalse(control.help_visible)

    def test_a_video_goes_with_the_old_renderer(self):
        from test_video import ControlTests
        fixture=ControlTests(); fixture.setUp()
        try:
            fixture.open()
            completed=[]
            fixture.v.on_message({'op':'video_pause','payload':{}},done=lambda *result:completed.append(result))
            self.assertTrue(fixture.v._owner_pending)
            fixture.v.renderer_restarted()
            self.assertEqual(completed,[(False,'Renderer restarted')])
            self.assertFalse(fixture.v.active)
            self.assertFalse(fixture.view.video_active)
            self.assertFalse(fixture.v._owner_pending)
            fixture.v.renderer_restarted()
            self.assertEqual(len(completed),1)
        finally:
            fixture.join_binds(); fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
