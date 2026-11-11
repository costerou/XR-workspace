"""The window memory tracker's corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `window_memory`).

rust/corpus/window_memory/tracker.json: for each scenario (the WindowTracker cases of test_window_memory.py), the shipped
window_memory.WindowTracker drives the shipped hyprland.HyprlandClient against a scripted stand-in compositor on a temporary
socket (never the user's Hyprland), step by step: `place`, `finish`, `capture` (a mark and the poll that runs it) and
`close`. Each step keeps the bytes sent and the replies, the memory file after it and the tracker's counts.
rust/crates/xr-workspace/tests/window_memory_tracker.rs replays it against view_controls::window_memory::Tracker: the n-th
connection gets the n-th reply, and the requests, the file and the counts must be the same.

Regenerate (after a deliberate change) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/window_memory_tracker_corpus.py --write

With no argument it exits 1 when the file and the Python disagree (test_window_memory_tracker_corpus.py).
"""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path: sys.path.insert(0, str(path))

from tools.native_spike import hyprland, window_memory as wm  # noqa: E402
import test_window_memory as fx  # noqa: E402

FILE = ROOT / 'rust' / 'corpus' / 'window_memory' / 'tracker.json'
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
MODEL = fx.MODEL
FALLBACK = 'DP-1'
BAD = 'unknown request'  # not JSON: a query that fails


class Compositor:
    """Answers queries from `state`, evals with `eval_reply`, and keeps every exchange. One request per connection."""
    def __init__(self, root, state):
        self.state, self.eval_reply, self.fail, self.exchanges = state, 'ok', False, []
        directory = root / 'hypr' / INSTANCE
        directory.mkdir(parents=True, mode=0o700)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(directory / '.socket.sock'))
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _reply(self, text):
        if text.startswith('/eval '): return self.eval_reply
        kinds = {'j/clients': 'clients', 'j/workspaces': 'workspaces', 'j/monitors all': 'monitors',
                 'j/activewindow': 'activewindow'}
        if self.fail or text not in kinds: return BAD
        return json.dumps(self.state[kinds[text]])

    def _serve(self):
        with self.server:
            while True:
                try: conn, _ = self.server.accept()
                except OSError: return
                with conn:
                    request = bytearray(conn.recv(65536))
                    conn.settimeout(0.05)  # the client sends one request and then waits: read until it pauses
                    try:
                        while part := conn.recv(65536): request.extend(part)
                    except TimeoutError: pass
                    if request == b'stop': return
                    text = bytes(request).decode('utf-8')
                    reply = self._reply(text)
                    self.exchanges.append({'request': text, 'reply': reply})
                    try: conn.sendall(reply.encode())
                    except (BrokenPipeError, ConnectionResetError): pass

    def close(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.server.getsockname()); s.sendall(b'stop')
        self.thread.join(3)


def state(clients=(), spaces=(), monitors=fx.MONITORS, active=None):
    return {'clients': list(clients), 'workspaces': list(spaces), 'monitors': list(monitors), 'activewindow': active or {}}


def saved(*windows, instance=INSTANCE):
    """A memory file holding `windows` for MODEL, as Store.save writes it."""
    return {'schema': wm.SCHEMA, 'models': {MODEL: wm.validate(fx.memory(*windows, instance=instance))}}


PLACED = {'placement': {'x': 100, 'y': 0, 'distance': 90, 'curvature': 0}, 'roll': -300}
FOCUSED = [dict(m, focused=(m['name'] == 'DP-1')) for m in fx.MONITORS]


def scenarios():
    """(name, file before the run (a value, raw text, or None), first state, context, steps). A step: {'do': place |
    finish | capture | close} with optional 'state' (replaced before it), 'fail' (queries answered with junk) and 'eval'
    (the eval reply)."""
    one = state([fx.win('0xa', 61, at=(4100, 26))], [fx.ws(61, 'XR-1')])
    home = state([fx.win('0xa', 1)], [fx.ws(1, 'DP-1')])
    return [
        ('a_mark_saves_after_the_delay', None, one, None, [{'do': 'capture'}]),
        ('the_memory_is_only_written_when_it_changed', None, one, None, [
            {'do': 'capture'}, {'do': 'capture'},
            {'do': 'capture', 'state': state([fx.win('0xa', 61, at=(4200, 26))], [fx.ws(61, 'XR-1')])}]),
        ('a_failed_read_keeps_what_was_saved_and_tries_again', None, one, None, [
            {'do': 'capture'}, {'do': 'capture', 'fail': True, 'state': state([], [fx.ws(61, 'XR-1')])},
            {'do': 'capture', 'state': one}]),
        ('an_empty_capture_never_replaces_a_memory', None, one, None, [
            {'do': 'capture'}, {'do': 'capture', 'state': state([], [fx.ws(61, 'XR-1')])}, {'do': 'close'}]),
        ('the_last_capture_is_at_the_end', None, one, None, [{'do': 'close'}, {'do': 'capture'}, {'do': 'close'}]),
        ('the_context_comes_from_the_view', None, one, {'XR-1': PLACED}, [{'do': 'close'}]),
        ('nothing_saved_does_nothing', None, home, None, [{'do': 'place'}, {'do': 'finish'}]),
        ('an_unusable_memory_does_nothing', '{not json', home, None, [{'do': 'place'}, {'do': 'finish'}]),
        ('moves_then_settles_with_one_plan', saved(fx.record('0xa', 'XR-2', 1, 71)), home, None,
         [{'do': 'place'}, {'do': 'finish'}]),
        ('focus_is_read_before_anything_moves', saved(fx.record('0xa', 'XR-2', 1, 71)),
         state([fx.win('0xa', 1)], [fx.ws(1, 'DP-1')], FOCUSED, {'address': '0xa'}), None, [{'do': 'place'}, {'do': 'finish'}]),
        ('a_compositor_failure_leaves_the_windows_to_adoption', saved(fx.record('0xa', 'XR-2', 1, 71)), home, None,
         [{'do': 'place', 'fail': True}, {'do': 'finish'}]),
        ('a_refused_move_does_not_stop_the_run', saved(fx.record('0xa', 'XR-2', 1, 71)), home, None,
         [{'do': 'place', 'eval': 'refused'}, {'do': 'finish'}]),
        ('a_refused_settle_does_not_stop_the_run', saved(fx.record('0xa', 'XR-2', 1, 71)), home, None,
         [{'do': 'place'}, {'do': 'finish', 'eval': 'refused'}]),
        ('the_address_needs_the_same_session', saved(fx.record('0xa', 'XR-2', 1, 71, cls='foot', title='vim'), instance='older'),
         state([fx.win('0xa', 1, cls='firefox', title='x'), fx.win('0xb', 1, cls='foot', title='vim')], [fx.ws(1, 'DP-1')]),
         None, [{'do': 'place'}, {'do': 'finish'}]),
        ('the_first_capture_after_a_start_replaces_the_old_memory',
         saved(fx.record('0xa', 'XR-2', 1, 71), fx.record('0xb', 'XR-3', 2, 81)), home, None, [
             {'do': 'place'}, {'do': 'finish'},
             {'do': 'close', 'state': state([fx.win('0xa', 71, at=(6016, 26))], [fx.ws(71, 'XR-2')])}]),
        ('a_window_on_a_missing_desktop_stays_parked_there', saved(fx.record('0xa', 'XR-4', 3, 91)), home, None, [
            {'do': 'place'}, {'do': 'finish'},
            {'do': 'close', 'state': state([fx.win('0xa', 82, at=(7936, 26))], [fx.ws(82, 'XR-3')])}]),
        ('a_parked_window_moved_elsewhere_is_no_longer_parked', saved(fx.record('0xa', 'XR-4', 3, 91)), home, None, [
            {'do': 'place'}, {'do': 'finish'},
            {'do': 'capture', 'state': state([fx.win('0xa', 82, at=(7936, 26))], [fx.ws(82, 'XR-3')])},
            {'do': 'close', 'state': state([fx.win('0xa', 71, at=(6016, 26))], [fx.ws(71, 'XR-2')])}]),
    ]


def _file(path):
    try: text = path.read_text()
    except FileNotFoundError: return None
    try: return json.loads(text)
    except ValueError: return {'raw': text}


def record(name, before, first, context, steps):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        (root / 'hypr').mkdir(mode=0o700)
        config = root / 'config'
        config.mkdir(mode=0o700)
        path = config / wm.FILE_NAME
        if isinstance(before, str): path.write_text(before)
        elif before is not None: path.write_text(json.dumps(before))
        compositor = Compositor(root, copy.deepcopy(first))
        out = []
        try:
            client = hyprland.HyprlandClient(INSTANCE, root)
            tracker = wm.WindowTracker(client, wm.Store(path, MODEL), FALLBACK, fx.XR, threads=False)
            tracker.delay = 0
            if context is not None: tracker.context = lambda: copy.deepcopy(context)
            for step in steps:
                if 'state' in step: compositor.state = copy.deepcopy(step['state'])
                compositor.fail, compositor.eval_reply = step.get('fail', False), step.get('eval', 'ok')
                compositor.exchanges = []
                if step['do'] == 'place': tracker.place(fx.XR)
                elif step['do'] == 'finish': tracker.finish()
                elif step['do'] == 'capture': tracker.mark(); tracker.poll(tracker.clock() + 1); tracker.join()
                elif step['do'] == 'close': tracker.close()
                else: raise ValueError(step['do'])
                out.append({**step, 'exchanges': compositor.exchanges, 'file': _file(path),
                            'counts': {k: getattr(tracker, k) for k in ('captures', 'writes', 'failures', 'restored')}})
        finally: compositor.close()
        return {'name': name, 'before': before, 'state': first, 'context': context, 'steps': out}


def corpus():
    return {'schema': 1, 'source': 'tests/native_spike/window_memory_tracker_corpus.py', 'instance': INSTANCE, 'model': MODEL,
            'fallback': FALLBACK, 'names': fx.XR,
            'note': 'One request per connection. Requests and replies are the exact bytes, as UTF-8 text.',
            'scenarios': [record(*s) for s in scenarios()]}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: window_memory_tracker_corpus.py [--write]')
    text = dumps(corpus())
    if argv == ['--write']:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print('wrote', FILE.relative_to(ROOT))
        return 0
    if not FILE.exists() or FILE.read_text() != text:
        print('stale:', FILE.relative_to(ROOT))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
