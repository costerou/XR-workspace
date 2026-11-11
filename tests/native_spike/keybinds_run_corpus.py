"""Corpus for the Rust run's key bind panel (rust/crates/xr-workspace/src/view_controls/keybinds.rs).

rust/corpus/supervisor/keybinds_run.json, made only by this module from the shipped Python code: the key bind panel as
`managed.view_controls` builds and drives it, with the Hyprland client's requests on the wire. Each scenario:

* `managed.view_controls`'s key bind block: `keybinds.load(keys_path)` (a `keybinds.json` written first, or none), the
  desktop's binds read (`client.binds_text(timeout=0.5)`, `DesktopBinds.from_text`), `BindControl(...)`,
  `client.install_view_binds(*bind_args, **keys.bind_kwargs())`, `keys.installed()`, `keys.startup_toast()`;
* then the panel's ops (`keys.on_message`) and loop ticks (`keys.poll`), each step's off-loop work joined before the next;
* then `keys.stop()`.

Every step records the requests the real `hyprland.HyprlandClient` sent (its `_request` answered by a script: the
desktop's binds for `binds`, `ok` for an eval), the frames, and the toasts. The Rust test
rust/crates/xr-workspace/tests/keybinds_run.rs replays the requests through a stand-in compositor socket.

Regenerate (after a deliberate change to keybinds.py, view.py's bind Lua or managed.view_controls) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/keybinds_run_corpus.py --write

test_keybinds_run_corpus.py fails when the file and the Python code disagree.
"""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'tests' / 'native_spike'):
    if str(p) not in sys.path: sys.path.insert(0, str(p))

from tools.native_spike import hyprland, keybinds as kb, view  # noqa: E402
from tools.native_spike.profiles import LayoutStore  # noqa: E402
import test_keybinds  # noqa: E402  (the `hyprctl binds` fixture text the Python tests use)

CORPUS = ROOT / 'rust' / 'corpus' / 'supervisor' / 'keybinds_run.json'
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = test_keybinds.ARGS
UP, DOWN, ENTER, ESC = 111, 116, 36, 9
CTRL, ALT = 4, 8
Y = 29


class Recorder(hyprland.HyprlandClient):
    """The real client, its socket replaced: every request kept with the scripted reply it got."""
    def __init__(self, text):
        super().__init__(INSTANCE, Path('/run/user/1000'))
        self.text, self.wire = text, []

    def _request(self, request, timeout=hyprland.DEADLINE):
        reply = self.text if request == 'binds' else 'ok'
        self.wire.append([request, reply])
        return reply.encode()


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


def frames(data):
    return (data or b'').decode()


SCENARIOS = {
    # A saved bind on keys Omarchy holds (left out, said once) and one changed; Ctrl+Alt+K, a bind changed live, Esc.
    'conflict': {'saved': {'next': 'CTRL + ALT + Delete', 'prev': 'CTRL + ALT + J'}, 'desktop': test_keybinds.DESKTOP,
                 'steps': [('op', kb.PANEL_OP, None), ('poll', 0.1), ('key', 0, DOWN), ('key', 0, ENTER),
                           ('key', CTRL | ALT, Y), ('poll', 0.1), ('key', 0, ESC), ('poll', 0.1)]},
    # No saved file, no desktop binds: the default bind Lua; the panel opens, its keys move, it closes by itself.
    'defaults': {'saved': None, 'desktop': '',
                 'steps': [('op', kb.PANEL_OP, None), ('poll', 0.1), ('key', 0, UP), ('key', 0, DOWN),
                           ('poll', view.OVERLAY_IDLE_CLOSE_S + 1), ('op', kb.PANEL_OP, None), ('op', kb.PANEL_OP, None)]},
}


def settle(keys):
    for t in list(keys._threads): t.join(2)


def scenario(spec):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = LayoutStore(tmp / 'layout.json')
        if spec['saved'] is not None:
            (tmp / 'keybinds.json').write_text(json.dumps({'schema': 1, 'binds': spec['saved']}))
        clock = Clock()
        control = view.ViewControl(view.ViewState(NAMES), ARGS[3], ARGS[4], lambda n: None, store, None, clock=clock)
        client = Recorder(spec['desktop'])
        toasts = []
        # managed.view_controls, its key bind block.
        keys_path = Path(store.path).with_name('keybinds.json')
        desktop = kb.DesktopBinds.from_text(client.binds_text(timeout=0.5))
        keys = control.keys = kb.BindControl(control, kb.load(keys_path), keys_path, client=client, bind_args=ARGS,
                                             desktop=desktop, notify=toasts.append)
        client.install_view_binds(*ARGS, **keys.bind_kwargs())
        keys.installed()
        keys.startup_toast()
        settle(keys)
        steps = [{'step': ['open'], 'wire': client.wire, 'frames': '', 'toasts': toasts[:]}]
        for step in spec['steps']:
            client.wire, del_toasts = [], len(toasts)
            if step[0] == 'op':
                out = keys.on_message({'op': step[1], 'payload': {}})
            elif step[0] == 'key':
                out = keys.on_message({'op': kb.WIRE_OP, 'payload': {'mods': step[1], 'code': step[2]}})
            else:
                clock.now += step[1]
                out = keys.poll(clock.now)
            settle(keys)
            steps.append({'step': list(step), 'wire': client.wire, 'frames': frames(out), 'toasts': toasts[del_toasts:]})
        client.wire = []
        keys.stop()
        steps.append({'step': ['stop'], 'wire': client.wire, 'frames': '', 'toasts': []})
        saved = (tmp / 'keybinds.json').read_text() if (tmp / 'keybinds.json').exists() else None
        return {'saved': spec['saved'], 'desktop': spec['desktop'], 'clock': 100.0, 'first_request_id': 2,
                'steps': steps, 'saved_after': saved}


def build():
    return {'instance': INSTANCE, 'bind_args': list(ARGS),
            'scenarios': {name: scenario(spec) for name, spec in SCENARIOS.items()}}


def render(data):
    return json.dumps(data, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    built = render(build())
    if '--write' in argv:
        CORPUS.parent.mkdir(parents=True, exist_ok=True)
        CORPUS.write_text(built)
        return 0
    if not CORPUS.exists() or CORPUS.read_text() != built:
        print('stale: rust/corpus/supervisor/keybinds_run.json')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
