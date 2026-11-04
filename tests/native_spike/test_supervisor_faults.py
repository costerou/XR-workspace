"""A bug in the supervisor costs one key, not the session, and says where it was (faults.py, Supervisor._contained).

The real Supervisor.run on real sockets, with handlers that raise. Offline only: no compositor, renderer or glasses.
"""
import contextlib
import io
import json
import socket
import threading
import time
import unittest

from tools.native_spike import supervisor
from tools.native_spike.faults import Faults, MAX_FAULTS, MAX_FRAMES, MAX_TEXT
from tools.native_spike.ipc import read_private_json


class Child:
    def __init__(self): self.rc = None
    def poll(self): return self.rc
    def terminate(self): self.rc = -15
    def kill(self): self.rc = -9


class Server:
    """The control socket a run listens on, with one request queued by push()."""
    def __init__(self):
        self.socket, self.client = socket.socketpair()
        self.queue = self
        self.waiting = []

    def receive(self, conn): return self.waiting.pop(0)
    def pop(self): pass


class Control:
    """The view control's face to the supervisor, raising where told."""
    def __init__(self):
        self.server = Server()
        self.calls = []
        self.broken = {}

    def _run(self, name, value):
        self.calls.append(name)
        if name in self.broken: raise self.broken[name]
        return value

    def on_message(self, message): return self._run(message['op'], b'{"from":"handler"}\n')
    def on_events(self, data): return self._run('events', b'{"from":"events"}\n')
    def poll(self, now): return self._run('poll', None)
    def set_overview_geometry(self, geometry): self.calls.append('geometry'); raise KeyError('layout')
    def status_reply(self, status): return {}


class Rig:
    def __init__(self, test):
        self.test = test
        self.child, self.control = Child(), Control()
        self.parent, self.renderer = socket.socketpair()
        self.status_parent, self.status_child = socket.socketpair(type=socket.SOCK_DGRAM)
        self.faults = Faults()
        self.sup = supervisor.Supervisor(self.child, lambda: 'recovered', control=self.control, status_socket=self.status_parent, faults=self.faults)
        self.stderr = io.StringIO()
        self.done = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.renderer.settimeout(5)
        for s in (self.parent, self.renderer, self.status_parent, self.status_child, self.control.server.socket, self.control.server.client):
            test.addCleanup(s.close)
        test.addCleanup(self.finish)

    def _run(self):
        with contextlib.redirect_stderr(self.stderr):
            self.done = self.sup.run(self.parent, interval=0.01)

    def start(self):
        self.thread.start()
        return self

    def alive(self):
        self.renderer.sendall(b'H')
        time.sleep(0.03)
        self.test.assertIsNone(self.child.rc)
        self.test.assertIsNone(self.sup.stop_reason)
        self.test.assertTrue(self.thread.is_alive())

    def finish(self):
        self.child.rc = 0
        self.thread.join(5)

    def op(self, name):
        self.control.server.waiting.append({'op': name})
        self.control.server.client.send(b'x')  # the socket is readable: `accept` is the stand-in's
        for _ in range(500):
            if name in self.control.calls: return
            time.sleep(0.002)
        self.test.fail(f'{name} never ran')


class LoopTests(unittest.TestCase):
    def rig(self):
        rig = Rig(self)
        # A socket pair has no accept: one connected end stands for each request.
        def request(control, status):
            return control.server.waiting.pop(0) if control.server.waiting else None
        original, supervisor.control_request = supervisor.control_request, request
        self.addCleanup(setattr, supervisor, 'control_request', original)
        return rig.start()

    def test_a_key_whose_handler_raises_costs_that_key_and_not_the_session(self):
        rig = self.rig()
        rig.control.broken['menu_reset'] = KeyError('placements')
        rig.op('menu_reset')
        rig.alive()
        rig.op('menu_inc')  # and the next key works
        rig.renderer.settimeout(5)
        self.assertIn(b'"from":"handler"', rig.renderer.recv(4096))
        rig.alive()

    def test_the_journal_gets_the_whole_traceback(self):
        rig = self.rig()
        rig.control.broken['menu_reset'] = KeyError('placements')
        rig.op('menu_reset')
        rig.alive()
        text = rig.stderr.getvalue()
        self.assertIn('Traceback (most recent call last)', text)
        self.assertIn('test_supervisor_faults.py', text)
        self.assertIn("KeyError: 'placements'", text)
        self.assertIn('menu_reset', text)

    def test_the_run_keeps_it_for_the_incident_report(self):
        rig = self.rig()
        rig.control.broken['menu_reset'] = KeyError('placements')
        rig.op('menu_reset')
        rig.alive()
        report = rig.faults.report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]['where'], 'key menu_reset')
        self.assertEqual(report[0]['count'], 1)
        self.assertEqual(report[0]['traceback'][-1], "KeyError: 'placements'")
        self.assertIn('_run', report[0]['traceback'][-2])

    def test_poll_raising_every_pass_is_one_report_and_a_live_session(self):
        rig = self.rig()
        rig.control.broken['poll'] = ValueError('every time')
        for _ in range(3): rig.alive()
        report = rig.faults.report()
        self.assertEqual([f['where'] for f in report], ['poll'])
        self.assertGreater(report[0]['count'], 1)
        self.assertEqual(rig.stderr.getvalue().count('Traceback'), 1)  # the journal has it once

    def test_a_hyprland_event_handler_raising_is_contained(self):
        child_events, events = socket.socketpair()
        self.addCleanup(child_events.close); self.addCleanup(events.close)
        rig = Rig(self)
        rig.control.events = events
        events.setblocking(False)
        rig.control.broken['events'] = RuntimeError('event')
        rig.start()
        child_events.send(b'configreloaded>>\n')
        rig.alive()
        self.assertEqual([f['where'] for f in rig.faults.report()], ['hyprland event'])

    def test_a_status_line_whose_handler_raises_is_contained(self):
        rig = self.rig()
        line = {'overview_geometry': {'w': 1920, 'h': 1200, 'half': [0.36, 0.225], 'tiles': [[40.0, 435.0, 586.67, 330.0]]}}
        rig.status_child.send(json.dumps(line).encode() + b'\n')
        for _ in range(500):
            if 'geometry' in rig.control.calls: break
            time.sleep(0.002)
        rig.alive()
        self.assertEqual([f['where'] for f in rig.faults.report()], ['renderer status'])

    def test_a_stop_still_stops_a_run_with_faults(self):
        rig = self.rig()
        rig.control.broken['poll'] = ValueError('every time')
        rig.alive()
        rig.child.rc = 0
        rig.thread.join(5)
        self.assertFalse(rig.thread.is_alive())
        self.assertEqual(rig.done, 'recovered')

    def test_the_interrupt_that_is_stop_is_not_a_fault(self):
        rig = Rig(self)
        rig.control.broken['poll'] = KeyboardInterrupt()
        got = []

        def run():
            try: rig.sup.run(rig.parent, interval=0.01)
            except KeyboardInterrupt: got.append('stopped')
        rig.thread = threading.Thread(target=run, daemon=True); rig.thread.start()
        rig.renderer.sendall(b'H')
        rig.thread.join(5)
        self.assertEqual(got, ['stopped'])
        self.assertEqual(rig.faults.report(), [])


class FaultsTests(unittest.TestCase):
    def caught(self, make):
        try: make()
        except Exception as exc: return exc

    def quiet(self, faults, *args, **kw):
        with contextlib.redirect_stderr(io.StringIO()) as out: faults.record(*args, **kw)
        return out.getvalue()

    def test_one_place_failing_again_is_counted_not_repeated(self):
        faults = Faults()
        def bad(): raise ValueError('again')
        for _ in range(5): self.quiet(faults, 'poll', self.caught(bad))
        self.assertEqual([(f['where'], f['count']) for f in faults.report()], [('poll', 5)])

    def test_the_short_form_names_the_code_innermost_last_and_shortens_paths(self):
        faults = Faults()
        def inner(): raise ValueError('why')
        def outer(): inner()
        self.quiet(faults, 'poll', self.caught(outer))
        lines = faults.report()[0]['traceback']
        self.assertEqual(lines[-1], 'ValueError: why')
        self.assertRegex(lines[-2], r'test_supervisor_faults\.py:\d+ inner$')
        self.assertRegex(lines[-3], r'test_supervisor_faults\.py:\d+ outer$')
        self.assertTrue(all(not line.startswith('/') for line in lines), lines)

    def test_the_worst_case_fits_the_result_file_with_room_to_spare(self):
        # read_private_json refuses a result over 16 KiB, and a run with no readable result looks killed.
        faults = Faults()
        def deep(n):
            if n: return deep(n - 1)
            raise ValueError('☃' * 5000)
        for i in range(MAX_FAULTS + 5): self.quiet(faults, f'key {"x" * 200} {i}', self.caught(lambda: deep(200)))
        report = faults.report()
        self.assertLessEqual(len(report), MAX_FAULTS + 1)  # and one line saying how many more
        self.assertEqual(report[-1]['count'], 5)
        self.assertLess(len(json.dumps(report)), 8192)
        for fault in report[:-1]:
            self.assertLessEqual(len(fault['traceback']), MAX_FRAMES + 1)
            self.assertTrue(all(len(line) <= MAX_TEXT for line in fault['traceback']))

    def test_a_journal_that_cannot_be_written_costs_nothing(self):
        faults = Faults()
        class Broken(io.StringIO):
            def write(self, text): raise OSError('closed')
        with contextlib.redirect_stderr(Broken()):
            faults.record('poll', self.caught(lambda: 1 / 0))
        self.assertEqual(faults.report()[0]['where'], 'poll')

    def test_fatal_says_so(self):
        faults = Faults()
        first = lambda text: text.splitlines()[0]  # (a traceback quotes this very line)
        self.assertEqual(first(self.quiet(faults, 'poll', self.caught(lambda: 1 / 0))), 'xr-workspace: error in poll, the run carries on:')
        self.assertEqual(first(self.quiet(faults, 'the run', self.caught(lambda: 1 / 0), fatal=True)), 'xr-workspace: error in the run:')

    def test_the_result_file_reader_takes_a_full_report(self):
        import tempfile, os
        from pathlib import Path
        faults = Faults()
        for i in range(MAX_FAULTS): self.quiet(faults, f'key {i}', self.caught(lambda: 1 / 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'r.json'
            path.write_text(json.dumps({'faults': faults.report(), 'error': 'x' * 2000}))
            os.chmod(path, 0o600)
            self.assertEqual(len(read_private_json(path)['faults']), MAX_FAULTS)


if __name__ == '__main__':
    unittest.main()
