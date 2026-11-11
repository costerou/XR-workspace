"""The window sender's corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `send`, level 1).

One golden file, rust/corpus/send/sender.json, made only by this module from the shipped Python: send.WindowSender
driven through a real HyprlandClient against a scripted stand-in compositor on a temporary socket (never the user's
Hyprland). Each scenario is a list of calls (the send keys' `request`, the overview drag's `lookup`, `under_cursor`,
`land` and `drop`, `set_names`, `close`), each one let finish before the next. It keeps:

* exchanges: every request the sender sent, one per connection, and the reply it got, as UTF-8 text;
* notes: the desktop notifications, in order;
* results: what each call handed back (a request's `done(success, detail)`, a lookup's window), null where nothing;
* moves: the moves Hyprland accepted.

The Rust replay (tests/send_corpus.rs) answers the n-th connection with the n-th recorded reply and must send the same
bytes in the same order, notify the same text and hand back the same results.

Regenerate (after a deliberate change to send.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/send_corpus.py --write

test_send_corpus.py fails when the file and the Python disagree.
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
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import hyprland, send  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'send'
FILE = CORPUS / 'sender.json'
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
NAMES = ['XR-1', 'XR-2', 'XR-3']
WINDOW = {'address': '0x55aa01', 'monitor': 0, 'pinned': False, 'workspace': {'id': 1}, 'floating': False,
          'at': [100, 100], 'size': [800, 600]}


def monitors(active=None, **over):
    """DP-1 the physical fallback, DP-2 the glasses, XR-1..3 the run's desktops side by side; active workspace per name."""
    active = {'DP-1': 1, 'DP-2': 2, 'XR-1': 31, 'XR-2': 41, 'XR-3': 51, **(active or {})}
    out = []
    for i, n in enumerate(['DP-1', 'DP-2', 'XR-1', 'XR-2', 'XR-3']):
        m = {'id': i, 'name': n, 'x': 1920 * i, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0,
             'activeWorkspace': {'id': active[n], 'name': str(active[n])}}
        m.update(over.get(n, {}))
        out.append(m)
    return out


def window(**over):
    w = copy.deepcopy(WINDOW)
    w.update(over)
    return w


class Compositor:
    """A scripted stand-in for Hyprland's request socket, one request per connection. `script` maps a request ('j/...'
    or 'dispatch' for any /dispatch, 'eval' for any /eval) to its reply: text as is, anything else as JSON."""
    def __init__(self, root, script):
        self.script, self.exchanges = script, []
        directory = root / 'hypr' / INSTANCE
        directory.mkdir(parents=True, mode=0o700)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(directory / '.socket.sock'))
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _answer(self, key):
        value = self.script.get(key, 'unknown request')
        return value if isinstance(value, str) else json.dumps(value)

    def _reply(self, request):
        text = request.decode('utf-8')
        if text.startswith('/dispatch '): return self._answer('dispatch').encode()
        if text.startswith('/eval '): return self._answer('eval').encode()
        return self._answer(text).encode()

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
                    reply = self._reply(bytes(request))
                    self.exchanges.append({'request': bytes(request).decode('utf-8'), 'reply': reply.decode('utf-8')})
                    try: conn.sendall(reply)
                    except (BrokenPipeError, ConnectionResetError): pass

    def close(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.server.getsockname()); s.sendall(b'stop')
        self.thread.join(3)


def script(win=WINDOW, mons=None, dispatch='ok', **more):
    out = {'j/activewindow': win, 'j/monitors all': monitors() if mons is None else mons, 'dispatch': dispatch,
           'eval': 'ok'}
    out.update(more)
    return out


def scenarios():
    """(name, names, fallback, script, calls)."""
    on_xr2 = window(monitor=3, workspace={'id': 41})
    tiled = window(address='0x55aa02', monitor=2, workspace={'id': 31}, at=[3840, 0], size=[1920, 1080])
    floating = window(address='0x55aa03', monitor=2, workspace={'id': 31}, at=[4000, 100], size=[640, 480],
                      floating=True, focusHistoryID=0)
    bad_ws = monitors()
    bad_ws[3]['activeWorkspace'] = {'id': 0}
    two_xr2 = monitors() + [dict(monitors()[3], id=9)]
    req = lambda op, sel='XR-2', verify=False: ['request', op, sel, verify]
    return [
        # The send keys (test_send_window.MoveTests), through the worker.
        ('send_glasses.selected', NAMES, 'DP-1', script(), [req('send_glasses')]),
        ('send_glasses_1', NAMES, 'DP-1', script(), [req('send_glasses_1')]),
        ('send_glasses_3', NAMES, 'DP-1', script(), [req('send_glasses_3')]),
        ('send_glasses_3.not_in_session', ['XR-1'], 'DP-1', script(), [req('send_glasses_3', 'XR-1')]),
        ('send_glasses_4.not_in_session', NAMES, 'DP-1', script(), [req('send_glasses_4')]),
        ('pinned.true', NAMES, 'DP-1', script(win=window(pinned=True)), [req('send_glasses')]),
        ('pinned.missing', NAMES, 'DP-1', script(win={k: v for k, v in WINDOW.items() if k != 'pinned'}), [req('send_glasses')]),
        ('pinned.text', NAMES, 'DP-1', script(win=window(pinned='yes')), [req('send_glasses')]),
        ('no_window.empty', NAMES, 'DP-1', script(win={}), [req('send_glasses')]),
        ('no_window.blank_address', NAMES, 'DP-1', script(win={'address': ''}), [req('send_glasses')]),
        ('no_window.list', NAMES, 'DP-1', script(win=[]), [req('send_glasses')]),
        ('bad_address', NAMES, 'DP-1', script(win=window(address='0xZZ')), [req('send_glasses')]),
        ('bad_address.injection', NAMES, 'DP-1', script(win=window(address='0x1"}) hl.exec("x')), [req('send_glasses')]),
        ('bad_address.number', NAMES, 'DP-1', script(win={'address': 5, 'pinned': False}), [req('send_glasses')]),
        ('already_there', NAMES, 'DP-1', script(win=on_xr2), [req('send_glasses')]),
        ('already_there.bad_workspace', NAMES, 'DP-1', script(win=window(monitor=3, workspace='41')), [req('send_glasses')]),
        ('monitors.not_a_list', NAMES, 'DP-1', script(mons={'name': 'XR-2'}), [req('send_glasses')]),
        ('target.special_workspace', NAMES, 'DP-1', script(mons=bad_ws), [req('send_glasses')]),
        ('target.missing', NAMES, 'DP-1', script(mons=[m for m in monitors() if m['name'] != 'XR-2']), [req('send_glasses_2', 'XR-1')]),
        ('target.ambiguous', NAMES, 'DP-1', script(mons=two_xr2), [req('send_glasses_2', 'XR-1')]),
        ('send_back', NAMES, 'DP-1', script(win=window(address='0xabc', monitor=4)), [req('send_back', 'XR-1')]),
        ('send_back.on_fallback', NAMES, 'DP-1', script(), [req('send_back', 'XR-1')]),
        ('send_back.on_glasses_output', NAMES, 'DP-1', script(win=window(monitor=1)), [req('send_back', 'XR-1')]),
        ('send_back.unknown_monitor', NAMES, 'DP-1', script(win=window(monitor=99)), [req('send_back', 'XR-1')]),
        ('send_back.no_fallback', NAMES, None, script(), [req('send_back', 'XR-1')]),
        ('send_back.pinned', NAMES, 'DP-1', script(win=window(monitor=2, pinned=True)), [req('send_back', 'XR-1')]),
        ('refused_by_hyprland', NAMES, 'DP-1', script(dispatch='error: nope'), [req('send_glasses', 'XR-1')]),
        ('unknown_reply', NAMES, 'DP-1', script(**{'j/activewindow': 'not json'}), [req('send_glasses')]),
        ('invalid_op', NAMES, 'DP-1', script(), [req('zoom_in', 'XR-1', True)]),
        # The options menu's owner path: done(success, detail) and the read-back after the move.
        ('verify.moved', NAMES, 'DP-1',
         script(**{'j/clients': [window(monitor=3, workspace={'id': 41})]}), [req('send_glasses', 'XR-2', True)]),
        ('verify.mismatch', NAMES, 'DP-1',
         script(**{'j/clients': [window(monitor=2, workspace={'id': 31})]}), [req('send_glasses', 'XR-2', True)]),
        ('verify.already_there', NAMES, 'DP-1', script(win=on_xr2), [req('send_glasses', 'XR-2', True)]),
        ('verify.already_there.no_workspace', NAMES, 'DP-1', script(win=window(monitor=3, workspace={})),
         [req('send_glasses', 'XR-2', True)]),
        ('verify.refused', NAMES, 'DP-1', script(win=window(pinned=True)), [req('send_glasses', 'XR-2', True)]),
        # A pill's Add / Remove.
        ('set_names', NAMES, 'DP-1', script(), [['set_names', ['XR-1']], req('send_glasses_2', 'XR-1'),
                                                ['set_names', NAMES], req('send_glasses_2', 'XR-1')]),
        # Run end: nothing starts after close.
        ('closed', NAMES, 'DP-1', script(), [['close'], req('send_glasses'), req('send_glasses', 'XR-2', True),
                                             ['lookup', 'XR-1', [4000, 100]], ['under_cursor'], ['land', '0x55aa01', 'XR-1'],
                                             ['drop', None, 'XR-1', [4000, 100], 'XR-2']]),
        # The overview's window drag.
        ('lookup.floating_over_tiled', NAMES, 'DP-1', script(**{'j/clients': [tiled, floating]}),
         [['lookup', 'XR-1', [4100, 200]], ['lookup', 'XR-1', [5000, 900]], ['lookup', 'XR-1', [3000, 10]],
          ['lookup', 'XR-9', [4100, 200]]]),
        ('lookup.compositor_error', NAMES, 'DP-1', script(**{'j/clients': 'nope'}), [['lookup', 'XR-1', [4100, 200]]]),
        ('under_cursor', NAMES, 'DP-1', script(**{'j/clients': [tiled, floating], 'j/cursorpos': {'x': 4100, 'y': 200}}),
         [['under_cursor']]),
        ('under_cursor.off_the_desktops', NAMES, 'DP-1', script(**{'j/clients': [tiled], 'j/cursorpos': {'x': 100, 'y': 200}}),
         [['under_cursor']]),
        ('under_cursor.zero_scale', NAMES, 'DP-1',
         script(mons=monitors(**{'XR-1': {'scale': 0}}), **{'j/cursorpos': {'x': 4100, 'y': 200}}), [['under_cursor']]),
        ('land.floating', NAMES, 'DP-1', script(win=floating), [['land', '0x55aa03', 'XR-2']]),
        ('land.tiled', NAMES, 'DP-1', script(win=tiled), [['land', '0x55aa02', 'XR-2']]),
        ('land.already_there', NAMES, 'DP-1', script(win=window(address='0x55aa03', workspace={'id': 41})),
         [['land', '0x55aa03', 'XR-2']]),
        ('land.other_window', NAMES, 'DP-1', script(win=floating), [['land', '0x55aa09', 'XR-2']]),
        ('land.pinned', NAMES, 'DP-1', script(win=dict(floating, pinned=True)), [['land', '0x55aa03', 'XR-2']]),
        ('land.not_in_session', NAMES, 'DP-1', script(), [['land', '0x55aa03', 'XR-7']]),
        ('land.special_workspace', NAMES, 'DP-1', script(mons=bad_ws), [['land', '0x55aa03', 'XR-2']]),
        ('land.bad_geometry', NAMES, 'DP-1', script(win=dict(floating, size='big')), [['land', '0x55aa03', 'XR-2']]),
        ('land.eval_refused', NAMES, 'DP-1', script(win=floating, eval='error'), [['land', '0x55aa03', 'XR-2']]),
        ('drop.found', NAMES, 'DP-1', script(),
         [['drop', {'address': '0x55aa03', 'w': 640, 'h': 480, 'pinned': False}, 'XR-1', [4100, 200], 'XR-3']]),
        ('drop.look_up', NAMES, 'DP-1', script(**{'j/clients': [tiled, floating]}), [['drop', None, 'XR-1', [4100, 200], 'XR-3']]),
        ('drop.nothing_there', NAMES, 'DP-1', script(**{'j/clients': []}), [['drop', None, 'XR-1', [4100, 200], 'XR-3']]),
        ('drop.no_point', NAMES, 'DP-1', script(**{'j/clients': []}), [['drop', None, 'XR-1', None, 'XR-3']]),
        ('drop.pinned', NAMES, 'DP-1', script(),
         [['drop', {'address': '0x55aa03', 'w': 640, 'h': 480, 'pinned': True}, 'XR-1', [4100, 200], 'XR-3']]),
        ('drop.bad_address', NAMES, 'DP-1', script(), [['drop', {'address': 'x', 'pinned': False}, 'XR-1', [4100, 200], 'XR-3']]),
        ('drop.not_in_session', NAMES, 'DP-1', script(),
         [['drop', {'address': '0x55aa03', 'pinned': False}, 'XR-1', [4100, 200], 'XR-8']]),
        ('drop.target_missing', ['XR-1', 'XR-2', 'XR-3', 'XR-4'], 'DP-1', script(),
         [['drop', {'address': '0x55aa03', 'pinned': False}, 'XR-1', [4100, 200], 'XR-4']]),
        ('drop.special_workspace', NAMES, 'DP-1', script(mons=bad_ws),
         [['drop', {'address': '0x55aa03', 'pinned': False}, 'XR-1', [4100, 200], 'XR-2']]),
        ('drop.refused', NAMES, 'DP-1', script(dispatch='no'),
         [['drop', {'address': '0x55aa03', 'pinned': False}, 'XR-1', [4100, 200], 'XR-2']]),
    ]


def _settle(s):
    for t in list(s._threads): t.join(10)


def run(names, fallback, script_, calls):
    """One scenario against a fresh stand-in compositor."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        (root / 'hypr').mkdir(mode=0o700)
        compositor = Compositor(root, script_)
        notes, results = [], []
        try:
            s = send.WindowSender(hyprland.HyprlandClient(INSTANCE, root), names, fallback, notify=notes.append)
            for call in calls:
                got = []
                kind, args = call[0], call[1:]
                if kind == 'request':
                    op, selected, verify = args
                    s.request(op, selected, done=(lambda ok, detail: got.append([ok, detail])) if verify else None)
                elif kind == 'lookup':
                    s.lookup(args[0], tuple(args[1]), got.append)
                elif kind == 'under_cursor':
                    s.under_cursor(got.append)
                elif kind == 'land':
                    s.land(*args)
                elif kind == 'drop':
                    window_, source, point, target = args
                    s.drop(window_, source, None if point is None else tuple(point), target)
                elif kind == 'set_names':
                    s.set_names(args[0])
                elif kind == 'close':
                    s.close()
                else:
                    raise ValueError(kind)
                _settle(s)
                results.append(got[0] if got else None)
            s.close()
        finally: compositor.close()
        return {'exchanges': compositor.exchanges, 'notes': notes, 'results': results, 'moves': s.moves}


def build():
    out = {'schema': 1, 'source': 'tests/native_spike/send_corpus.py', 'instance': INSTANCE,
           'note': 'One request per connection, each call let finish before the next. Requests and replies are UTF-8 text.',
           'scenarios': []}
    for name, names, fallback, script_, calls in scenarios():
        out['scenarios'].append({'name': name, 'names': names, 'fallback': fallback, 'calls': calls,
                                 **run(names, fallback, script_, calls)})
    return out


def render(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: send_corpus.py [--write]')
    text = render(build())
    if argv == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print('wrote', FILE.relative_to(ROOT))
        return 0
    if not FILE.exists() or FILE.read_text() != text:
        print('stale:', FILE.relative_to(ROOT)); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
