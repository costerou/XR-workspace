"""The controller's 1 s probe asks systemd about every finished run in its state directory with one systemctl call, with
the same answers as one call per run; the run status reply is read whole lines at a time. No systemd or runs: subprocess
is patched and the run's control socket is a local fake."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools.native_spike.ipc import MAX_FRAME
from tools.xr_controller.__main__ import ACTIVE_STATES, Host, Units


def done(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout)


class SystemctlFake:
    """subprocess.run stand-in: `systemctl --user is-active [--quiet] unit...` answered from `states`."""
    def __init__(self, states, reply=None):
        self.states, self.reply, self.calls = states, reply, []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        units = [a for a in argv[3:] if a != '--quiet']
        found = [self.states.get(u.removeprefix('viture-xr-'), 'inactive') for u in units]
        code = 0 if any(s in ACTIVE_STATES for s in found) else 3
        if '--quiet' in argv: return done(None, code)
        return done(self.reply if self.reply is not None else ''.join(s + '\n' for s in found), code)


class AliveManyTests(unittest.TestCase):
    def test_one_call_same_answers(self):
        states = {'a': 'active', 'b': 'inactive', 'c': 'failed', 'd': 'reloading', 'e': 'deactivating', 'f': 'activating'}
        fake = SystemctlFake(states)
        with patch('tools.xr_controller.__main__.subprocess.run', fake):
            got = Units('/runtime').alive_many(list(states))
            one_by_one = {r: Units('/runtime').alive(r) for r in states}
        self.assertEqual(got, one_by_one)
        self.assertEqual(got, {'a': True, 'b': False, 'c': False, 'd': True, 'e': False, 'f': False})
        self.assertEqual(fake.calls[0], ['systemctl', '--user', 'is-active', *('viture-xr-' + r for r in states)])
        self.assertEqual(len(fake.calls), 1 + len(states))  # the batch, then the one-by-one reference

    def test_a_single_run_is_the_original_call(self):
        fake = SystemctlFake({'a': 'active'})
        with patch('tools.xr_controller.__main__.subprocess.run', fake):
            self.assertEqual(Units('/runtime').alive_many(['a']), {'a': True})
            self.assertEqual(Units('/runtime').alive_many([]), {})
        self.assertEqual(fake.calls, [['systemctl', '--user', 'is-active', '--quiet', 'viture-xr-a']])

    def test_an_unexpected_answer_falls_back_to_one_call_per_run(self):
        for reply in ('active\n', 'active\ninactive\nactive\n', 'active\nsleeping\n', '', 'inactive\ninactive\n'):
            with self.subTest(reply=reply):
                fake = SystemctlFake({'a': 'active'}, reply=reply)  # the last: exit 0 but no unit active
                with patch('tools.xr_controller.__main__.subprocess.run', fake):
                    self.assertEqual(Units('/runtime').alive_many(['a', 'b']), {'a': True, 'b': False})
                self.assertEqual([c[3] for c in fake.calls[1:]], ['--quiet', '--quiet'])

    def test_invalid_run_ids_are_refused_before_any_call(self):
        fake = SystemctlFake({})
        with patch('tools.xr_controller.__main__.subprocess.run', fake):
            with self.assertRaisesRegex(ValueError, 'invalid identifier'): Units('/runtime').alive_many(['a', 'x;y'])
        self.assertEqual(fake.calls, [])

    def test_timeout_still_raises(self):
        def slow(argv, **kwargs): raise subprocess.TimeoutExpired(argv, 2)
        with patch('tools.xr_controller.__main__.subprocess.run', slow):
            with self.assertRaises(subprocess.TimeoutExpired): Units('/runtime').alive_many(['a', 'b'])


class ProbeTests(unittest.TestCase):
    def test_one_systemctl_call_for_every_finished_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            runs = [f'{i:032x}' for i in range(30)]
            for run_id in runs:
                for name, body in ((run_id + '.json', {'run_id': run_id}), (run_id + '.result.json', {'run_id': run_id})):
                    path = state / name; path.write_text(json.dumps(body)); path.chmod(0o600)
            live = runs[7]
            (state / (live + '.progress.json')).write_text(json.dumps({'run_id': live, 'phase': 'tracking'}))
            (state / (live + '.progress.json')).chmod(0o600)
            fake = SystemctlFake({live: 'active'})
            with patch('tools.xr_controller.__main__.subprocess.run', fake), \
                    patch('tools.xr_controller.__main__.HyprlandClient', side_effect=ValueError('no compositor')):
                result = Host(tmp, tmp, tmp).probe()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][3:], ['viture-xr-' + r for r in sorted(runs)])
        self.assertEqual(result['journals'], [dict(run_id=live, session=None, path=None, operations=[], phase='tracking')])
        self.assertEqual(result['reason'], 'no compositor')

    def test_no_finished_runs_no_call(self):
        fake = SystemctlFake({})
        with tempfile.TemporaryDirectory() as tmp, patch('tools.xr_controller.__main__.subprocess.run', fake), \
                patch('tools.xr_controller.__main__.HyprlandClient', side_effect=ValueError('no compositor')):
            Host(tmp, tmp, tmp).probe()
        self.assertEqual(fake.calls, [])


class StatusReplyTests(unittest.TestCase):
    """Units.control(..., 'status'): the run's one-line reply, however it arrives."""
    def run_reply(self, chunks, request=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name)
        directory = runtime / 'xr-workspace-spike' / 'run'
        directory.mkdir(parents=True, mode=0o700); (runtime / 'xr-workspace-spike').chmod(0o700); runtime.chmod(0o700)
        server = socket.socket(socket.AF_UNIX); self.addCleanup(server.close)
        server.bind(str(directory / 'control.sock')); server.listen(1)
        seen = []

        def serve():
            conn, _ = server.accept()
            with conn:
                line = b''
                while not line.endswith(b'\n'): line += conn.recv(4096)
                seen.append(json.loads(line))
                for chunk in chunks(seen[0]):
                    try: conn.sendall(chunk)
                    except OSError: return
                    time.sleep(0.01)
        t = threading.Thread(target=serve, daemon=True); t.start()
        reply = Units(runtime).control('run', 'session', 'status')
        t.join(2)
        return reply

    def reply_line(self, request, **extra):
        return (json.dumps(dict(schema=1, request_id=request['request_id'], run_id='run', session='session',
                                status={'presented_hz': 120.0}, **extra)) + '\n').encode()

    def test_a_reply_in_pieces_with_bytes_after_it(self):
        def chunks(request):
            line = self.reply_line(request, view={'zoomed': False}, placements=[{'x': 0}] * 3)
            return [line[:5], line[5:40], line[40:] + b'{"junk"', b'more\n']
        reply = self.run_reply(chunks)
        self.assertEqual(reply['status'], {'presented_hz': 120.0})
        self.assertEqual(reply['view'], {'zoomed': False})

    def test_wrong_identity_or_no_line_is_false(self):
        self.assertFalse(self.run_reply(lambda r: [self.reply_line(dict(r, request_id=r['request_id'] + 1))]))
        self.assertFalse(self.run_reply(lambda r: [b'{"schema": 1']))  # closed before the newline
        self.assertFalse(self.run_reply(lambda r: [b' ' * (MAX_FRAME + 10)]))  # no newline within the frame limit


if __name__ == '__main__':
    unittest.main()
