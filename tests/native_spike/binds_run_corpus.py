"""Corpus for the Rust run's Ctrl+Alt binds (rust/crates/xr-workspace/src/view_controls/binds.rs).

rust/corpus/supervisor/binds_run.json, made only by this module from the shipped Python code: `managed.view_controls`
with its bind closures (`bind_args`, the session mode, `after_reload`, `menu_binds`, `typing_binds`, and in the
`finally` `stop_menu_binds` and `remove_view_binds`) and `ViewControl`'s bind work (`_reload_work`, `_menu_bind_work`,
`_typing_bind_work`), on the real `hyprland.HyprlandClient` whose `_request` answers from a small scripted compositor
(the monitor list, changed by the mode and placement rules the binds' reload evaluates).

Each scenario starts the run's view controls with only the binds' client methods offered (no pointer, sender, privacy,
event stream, key bind panel client, video or options binds), drives the view with ops and `configreloaded>>` events,
each step's threads joined before the next, then leaves the `with`. Every step records:

* `calls`: what the view asked of the binds, in order (`_sync_menu_binds(force)` with the view state it then reads,
  `_sync_typing_binds()` with the typing mode, the reload);
* `wire`: the requests the client sent, with the scripted replies (the view's own style-frame read of the border
  colour, `j/getoption general:col.active_border`, is left out: it is the view's, not the binds').

The Rust test rust/crates/xr-workspace/tests/binds_run.rs makes the same calls on `view_controls::binds::Binds` and
replays the wire through a stand-in compositor socket.

Regenerate (after a deliberate change to managed.view_controls, view.py's bind work or the bind Lua) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/binds_run_corpus.py --write

test_binds_run_corpus.py fails when the file and the Python code disagree.
"""
import copy
import json
import re
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import hyprland, managed, sbs, view  # noqa: E402
from tools.native_spike.profiles import LayoutStore  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'supervisor' / 'binds_run.json'
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
NAMES = ['XR-1', 'XR-2', 'XR-3']
RUN_ID, SESSION = 'run-1', 'a1b2c3'
PYTHON = '/usr/bin/python3'
SOCKET = '/run/user/1000/xr-workspace-spike/run-1/control.sock'
CHECKOUT = '/opt/viture-workspace'  # the checkout's path in the corpus (the bind helper's)
BORDER = 'j/getoption general:col.active_border'
RULE = re.compile(r'hl\.monitor\(\{ output = "([^"]+)", mode = "(\d+)x(\d+)@([0-9.]+)", position = "(-?\d+)x(-?\d+)"')

# Methods of the client the binds use; everything else is hidden so no other feature starts.
OFFERED = ('runtime_dir', 'query', 'install_view_binds', 'reinstall_view_binds', 'remove_view_binds',
           'install_menu_binds', 'remove_menu_binds', 'park_presentation', 'apply_session_mode', 'apply_wide_mode')


def monitor(name, ident, **changes):
    result = dict(id=ident, name=name, width=1920, height=1080, x=0, y=0, scale=1.0, transform=0, refreshRate=60.0,
                  disabled=False, dpmsStatus=True, mirrorOf='none', availableModes=[])
    return dict(result, **changes)


DESKTOP = monitor('DP-1', 0)
PARKED = dict(x=-1920, y=9272)  # hyprland.presentation_placement of the glasses beside DESKTOP
SESSION_MODE = dict(height=1200, refreshRate=119.999)
WIDE_MODES = ['1920x1200@119.999Hz', '3840x1200@60.00Hz', '3840x1200@90.00Hz']


class Recorder(hyprland.HyprlandClient):
    """The real client, its socket replaced: the monitor list as state, every request kept with its reply."""
    def __init__(self, glasses):
        super().__init__(INSTANCE, Path('/run/user/1000'))
        self.monitors, self.wire = [copy.deepcopy(DESKTOP), glasses], []

    def _request(self, request, timeout=hyprland.DEADLINE):
        if request == 'j/monitors all': reply = json.dumps(self.monitors)
        elif request.startswith('j/getoption'): reply = json.dumps({'option': 'general:col.active_border', 'str': ''})
        else:
            for name, w, h, hz, x, y in RULE.findall(request):
                for m in self.monitors:
                    if m['name'] == name: m.update(width=int(w), height=int(h), refreshRate=float(hz), x=int(x), y=int(y))
            reply = 'ok'
        if request != BORDER: self.wire.append([request.replace(str(ROOT), CHECKOUT), reply])
        return reply.encode()


class Offered:
    """The run's client as the binds see it: only their methods (managed.view_controls gates on hasattr)."""
    def __init__(self, client): self._client = client

    def __getattr__(self, name):
        if name not in OFFERED: raise AttributeError(name)
        return getattr(self._client, name)


class Server:
    """ipc.PrivateServer at a fixed path: the corpus does not depend on the temporary folder."""
    def __init__(self, runtime_dir, run_id, session): self.path, self.socket = Path(SOCKET), None
    def close(self): pass


SCENARIOS = {
    # test_typing_mode's run wiring: typing mode survives a reload; control mode's reload as before.
    'typing': {'glasses': dict(SESSION_MODE, **PARKED), 'sbs': None,
               'steps': [('op', view.TYPING_OP), ('reload',), ('op', view.TYPING_OP), ('reload',)]},
    # test_view_menu_wiring: the placement panel's modal binds, again after a reload, off, and gone with typing mode.
    'menu': {'glasses': dict(SESSION_MODE, **PARKED), 'sbs': None,
             'steps': [('op', 'menu_toggle'), ('reload',), ('op', 'menu_toggle'), ('op', 'menu_toggle'),
                       ('op', view.TYPING_OP), ('reload',), ('op', view.TYPING_OP)]},
    # test_reload_preserves_120hz_after_parking: the reload reset the glasses; parked first, then 120 Hz again.
    'presentation': {'glasses': dict(SESSION_MODE, **PARKED), 'sbs': None,
                     'steps': [('reset', {'x': 1920, 'y': 0, 'height': 1080, 'refreshRate': 60.0}), ('reload',)]},
    # Glasses not in the session mode at the start: a reload parks them and leaves their mode alone.
    'no-session-mode': {'glasses': dict(PARKED), 'sbs': None,
                        'steps': [('reset', {'x': 1920, 'y': 0}), ('reload',)]},
    # Glasses side by side, the consent's 3D scope given: their wide mode instead of the session mode.
    'wide': {'glasses': dict(SESSION_MODE, availableModes=WIDE_MODES, **PARKED), 'sbs': sbs.SCOPE,
             'steps': [('reset', {'height': 1080, 'refreshRate': 60.0}), ('reload',)]},
    # Typing mode when the run ends, the panel open before it: the removal takes the table.
    'end-typing': {'glasses': dict(SESSION_MODE, **PARKED), 'sbs': None,
                   'steps': [('op', 'menu_toggle'), ('op', view.TYPING_OP)]},
}


def state(control):
    return {'menu_open': control.state.menu_open, 'menu_keys_off': control.menu_keys_off,
            'options_owned': control.options.keyboard_owned, 'typing': control.state.typing}


def wrap(control, calls):
    """Records what the view asks of the binds: the syncs it starts and the reload."""
    menu, typing, reload = control._sync_menu_binds, control._sync_typing_binds, control._reload_work

    def sync_menu(force=False):
        calls.append(['menu', force, state(control)]); return menu(force)

    def sync_typing():
        calls.append(['typing', control.state.typing]); return typing()

    def reload_work():
        calls.append(['reload']); return reload()
    control._sync_menu_binds, control._sync_typing_binds, control._reload_work = sync_menu, sync_typing, reload_work


def scenario(spec):
    built = []
    shipped = view.ViewControl

    class Recorded(shipped):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)
    executable = sys.executable
    import tools.native_spike.ipc as ipc
    real_server = ipc.PrivateServer
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glasses = monitor('DP-2', 1, **spec['glasses'])
        client = Recorder(glasses)
        client.runtime_dir = tmp
        sys.executable, view.ViewControl, ipc.PrivateServer = PYTHON, Recorded, Server
        try:
            steps = []
            with managed.view_controls(Offered(client), RUN_ID, SESSION, NAMES, 'DP-2',
                                       store=LayoutStore(tmp / 'layout.json'), fallback='DP-1', sbs_mode=spec['sbs']):
                control, calls = built[0], []
                control.join_focus(2)
                steps.append({'step': ['open'], 'calls': [], 'wire': client.wire})
                wrap(control, calls)
                for step in spec['steps']:
                    client.wire, calls[:] = [], []
                    if step[0] == 'op': control.on_message({'op': step[1]})
                    elif step[0] == 'reload': control.on_events(b'configreloaded>>\n')
                    else: client.monitors[1].update(step[1])
                    control.join_focus(2)
                    steps.append({'step': list(step), 'calls': list(calls), 'wire': client.wire})
                client.wire = []
            steps.append({'step': ['close'], 'calls': [], 'wire': client.wire})
        finally:
            sys.executable, view.ViewControl, ipc.PrivateServer = executable, shipped, real_server
        return {'glasses': glasses_at_start(spec), 'sbs_mode': spec['sbs'] is not None, 'steps': steps}


def glasses_at_start(spec):
    return monitor('DP-2', 1, **spec['glasses'])


def build():
    return {'instance': INSTANCE, 'desktop': DESKTOP, 'run_id': RUN_ID, 'session': SESSION,
            'bind_args': [PYTHON, f'{CHECKOUT}/tools/native_spike/xr_control.py', SOCKET, RUN_ID, SESSION],
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
        print('stale: rust/corpus/supervisor/binds_run.json')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
