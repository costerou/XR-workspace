"""A scripted Hyprland and ownership plugin for tests/managed_real.rs: real sockets, no compositor.

Run by a copy of the Python executable named `Hyprland` (the clients check the peer's executable name):
`Hyprland compositor.py <runtime> <instance> <case>`. It binds `<runtime>/hypr/<instance>/.socket.sock` and
`.socket2.sock` and `<runtime>/wayland-1`, writes the lock the clients read, then `<runtime>/fake-ready`.

Every request is logged to `<runtime>/wire.jsonl` as `{"pid": caller, "request": text}`, so a test can compare what two
supervisors sent, request for request. Unknown requests are logged as errors and answered with an error.

The world: DP-1 (the fallback, one window on workspace 1) and DP-2 (the glasses). The broker keeps a registry of the
run's outputs as the plugin does: `create` makes one live, `inspect` reads it back, `evacuate_remove` makes it dead.

Cases (the third argument):
- `hello-refused`: the broker's hello is not qualified.
- `create-fail`: the second output created does not go live.
- `recovery-stuck`: the first `evacuate_remove` is refused with an error reply.
- `no-glasses`: DP-2 is not there.
- anything else: everything works.

`<runtime>/fake-unplug` existing takes DP-1 away (the run's guard then fails).
"""
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
instance, case = sys.argv[2], sys.argv[3]
os.chdir(root)
directory = root / 'hypr' / instance
directory.mkdir(parents=True, mode=0o700)
(directory / 'hyprland.lock').write_text(f'{os.getpid()}\nwayland-1\n')
(directory / 'hyprland.lock').chmod(0o600)
trace = root / 'wire.jsonl'
lock = threading.Lock()
registry = {}
plugins = []
refused = []
EPOCH = 'epoch-one'


def log(event):
    with open(trace, 'a') as f:
        f.write(json.dumps(event, sort_keys=True) + '\n')


def monitor(name, mid, x, y, model, focused=False):
    return dict(name=name, id=mid, x=x, y=y, width=1920, height=1080, refreshRate=120.0, scale=1.0, transform=0,
                disabled=False, dpmsStatus=True, mirrorOf='none', activeWorkspace=dict(id=1 if mid == 0 else 2,
                name='1' if mid == 0 else '2'), reserved=[0, 0, 0, 0], model=model, description='fixture',
                availableModes=['1920x1080@120.00Hz', '1920x1080@60.00Hz'], focused=focused, make='fixture',
                serial='', specialWorkspace=dict(id=0, name=''), vrr=False, solitary='0', activelyTearing=False,
                directScanoutTo='0', currentFormat='XRGB8888', colorManagementPreset='srgb', sdrBrightness=1.0,
                sdrSaturation=1.0, sdrMinLuminance=0.2, sdrMaxLuminance=80)


physical = [monitor('DP-1', 0, 0, 0, 'ordinary', focused=True)]
if case != 'no-glasses':
    physical.append(monitor('DP-2', 1, 1920, 0, 'VITURE'))


def monitors():
    out = [m for m in physical if not (m['name'] == 'DP-1' and (root / 'fake-unplug').exists())]
    for r in registry.values():
        if r['state'] == 'live':
            o = r['observation']
            m = monitor(r['name'], 10 + len(out), o['x'], o['y'], 'headless')
            m.update(width=o['width'], height=o['height'], activeWorkspace=dict(id=o['workspace'], name=str(o['workspace'])))
            out.append(m)
    return out


def workspaces():
    out = [dict(id=1, name='1', monitor='DP-1', monitorID=0, windows=1, hasfullscreen=False, lastwindow='0x1',
                lastwindowtitle='t'),
           dict(id=2, name='2', monitor='DP-2', monitorID=1, windows=0, hasfullscreen=False, lastwindow='0x0',
                lastwindowtitle='')]
    for r in registry.values():
        if r['state'] == 'live':
            w = r['observation']['workspace']
            out.append(dict(id=w, name=str(w), monitor=r['name'], monitorID=0, windows=0, hasfullscreen=False,
                            lastwindow='0x0', lastwindowtitle=''))
    return out


CLIENT = dict(address='0x1', mapped=True, hidden=False, at=[10, 10], size=[800, 600],
              workspace=dict(id=1, name='1'), floating=False, pseudo=False, monitor=0, **{'class': 'kitty'},
              title='t', initialClass='kitty', initialTitle='t', pid=4242, xwayland=False, pinned=False,
              fullscreen=0, fullscreenClient=0, grouped=[], tags=[], swallowing='0x0', focusHistoryID=0,
              inhibitingIdle=False, xdgTag='', xdgDescription='')


def broker(j):
    p, op = j['payload'], j['op']
    if op == 'hello':
        return dict(qualified=case != 'hello-refused', fallback='physical-handle')
    if op == 'cursor_shape':
        return dict(shape='')
    if op == 'create':
        registry[p['id']] = dict(
            id=p['id'], name=p['name'], state='live', physical=False, fallback_usable=True,
            observation=dict(**{k: p[k] for k in ('width', 'height', 'refresh_millihz', 'scale', 'x', 'y')},
                             workspace=61 + 10 * p['index'], enabled=True, dpms=True, mirrored=False, transform=0,
                             workspaces=[dict(id=61 + 10 * p['index'], name=str(61 + 10 * p['index']), special=False)],
                             windows=[]))
        if case == 'create-fail' and p['index'] == 1:
            registry[p['id']]['state'] = 'failed'
    if p.get('id') not in registry:
        return None
    if op == 'evacuate_remove':
        if case == 'recovery-stuck' and not refused:
            refused.append(p['id'])
            return None
        registry[p['id']]['state'] = 'dead'
        registry[p['id']]['observation'] = None
    return registry[p['id']]


# One reply function per view-controls feature (rust/crates/xr-workspace/VIEW_CONTROLS.md): a feature's port answers
# its own requests in its own function, so ports in parallel never edit the same lines. Asked first, in this order.


def reply_socket(wire):
    """view_controls::socket's requests (VIEW_CONTROLS.md); None: not this feature's. The `hold` case: the glasses (DP-2)
    are gone while `<runtime>/fake-glasses-gone` exists (the fake renderer makes it as it ends; the test removes it)."""
    if wire in ('j/monitors', 'j/monitors all') and (root / 'fake-glasses-gone').exists():
        return [m for m in monitors() if m['name'] != 'DP-2']
    return None


def reply_binds(wire):
    """view_controls::binds's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


def events_binds():
    """view_controls::binds's event lines: a config reload (Omarchy's theme switch) once the case makes
    `<runtime>/fake-config-reload`, which is taken as it is sent."""
    reload = root / 'fake-config-reload'
    if not reload.exists(): return []
    reload.unlink()
    return [b'configreloaded>>\n']


def reply_view(wire):
    """view_controls::view's requests (VIEW_CONTROLS.md); None: not this feature's. A view key that selects a desktop
    gives its monitor Hyprland's focus (`view.focus_command`)."""
    if wire.startswith('/dispatch hl.dsp.focus({monitor="XR-') and wire.endswith('"})'):
        return 'ok'
    return None


def reply_overview_pointer(wire):
    """view_controls::overview_pointer's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


sent = {}  # view_controls::send: the focused window's monitor and workspace once a send key moved it


def reply_send(wire):
    """view_controls::send's requests (VIEW_CONTROLS.md); None: not this feature's.

    A send key's move (`dispatch hl.dsp.window.move({ workspace = "N", follow = true, window = "address:0x1" })`) puts
    the one window (CLIENT, focused) on the monitor showing workspace N, so Ctrl+Alt+G then Ctrl+Alt+B reads it on an
    XR desktop and moves it back to DP-1. Until a move, the built-in replies answer."""
    import re
    move = re.fullmatch(r'/dispatch hl\.dsp\.window\.move\(\{ workspace = "(\d+)", follow = (?:true|false), '
                        r'window = "address:(0x[0-9a-f]+)" \}\)', wire)
    if move:
        workspace = int(move[1])
        shown = [m for m in monitors() if m['activeWorkspace']['id'] == workspace]
        if move[2] != CLIENT['address'] or len(shown) != 1:
            return 'error: no such window or workspace'
        sent.update(monitor=shown[0]['id'], workspace=dict(id=workspace, name=str(workspace)))
        return 'ok'
    if sent and wire in ('j/activewindow', 'j/clients'):
        window = dict(CLIENT, **sent)
        return window if wire == 'j/activewindow' else [window]
    return None


PRIVACY = dict(asleep=False)


def reply_privacy(wire):
    """view_controls::privacy's requests (VIEW_CONTROLS.md); None: not this feature's.

    The physical monitors keep a DPMS state (their `dpmsStatus`): privacy.py's blank Lua turns its first output off
    and its second on when both exist, its wake Lua turns its output on, and the stale bind's every monitor. With
    `<runtime>/fake-privacy-asleep` made mid-run, DP-1 goes DPMS-off at the next request (as privacy mode leaves it)."""
    import re
    if not PRIVACY['asleep'] and (root / 'fake-privacy-asleep').exists():
        PRIVACY['asleep'] = True
        for m in physical:
            if m['name'] == 'DP-1': m['dpmsStatus'] = False

    def dpms(name, on):
        for m in physical:
            if name is None or m['name'] == name: m['dpmsStatus'] = on

    names = {m['name'] for m in monitors()}
    if blank := re.match(r'/eval local t,g="([^"]+)","([^"]+)" ', wire):
        if blank[1] in names and blank[2] in names: dpms(blank[1], False); dpms(blank[2], True)
        return 'ok'
    if wake := re.match(r'/eval local t="([^"]+)" ', wire):
        if 'hl.dsp.dpms(' in wire: dpms(wake[1], True); return 'ok'
    if wire == '/eval hl.dispatch(hl.dsp.dpms({action="on"}))':
        dpms(None, True)
        return 'ok'
    return None


def reply_immersive(wire):
    """view_controls::immersive's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


# The desktop's own binds (plain `hyprctl binds`, as Hyprland 0.56 prints it). In the `keybinds` case a described
# desktop bind holds Ctrl+Alt+P, the run's default prediction toggle: both runs must leave that bind out.
DESKTOP_BINDS = ('bindd\n\tmodmask: 12\n\tsubmap: \n\tkey: P\n\tkeycode: 0\n\tcatchall: false\n'
                 '\tdescription: Pick a color\n\tdispatcher: exec\n\targ: hyprpicker\n\n')


def reply_keybinds(wire):
    """view_controls::keybinds's requests (VIEW_CONTROLS.md); None: not this feature's."""
    if wire == 'binds':  # the key bind panel's read of the desktop's binds (`client.binds_text`)
        return DESKTOP_BINDS if case == 'keybinds' else ''
    return None


def reply_video(wire):
    """view_controls::video's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


def reply_desktops(wire):
    """view_controls::desktops's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


remembered = []  # case `window-memory`: the window memory put workspace 1 back on XR-2


def reply_window_memory(wire):
    """view_controls::window_memory's requests (VIEW_CONTROLS.md); None: not this feature's.

    Case `window-memory`: window-memory.json remembers the window 0x1 on XR-2. Once the placement's eval moves its
    workspace there whole, the window is on XR-2's workspace 71, at the same place
    inside it, so the run's last capture finds it there. The placement's moves eval names it as the row `{1,"XR-2",...}`."""
    if case != 'window-memory':
        return None
    if wire.startswith('/eval ') and '{{1,"XR-2",' in wire:
        remembered.append(wire)
        return 'ok'
    if wire == 'j/clients' and remembered:
        live = [r['observation'] for r in registry.values() if r['state'] == 'live' and r['name'] == 'XR-2']
        if live:
            o = live[0]
            return [dict(CLIENT, at=[o['x'] + 10, o['y'] + 10], workspace=dict(id=o['workspace'], name=str(o['workspace'])),
                         monitor=11)]
    return None


def reply_setup(wire):
    """view_controls::setup's requests (VIEW_CONTROLS.md); None: not this feature's."""
    return None


FEATURE_REPLIES = (reply_socket, reply_binds, reply_view, reply_overview_pointer, reply_send, reply_privacy,
                   reply_immersive, reply_keybinds, reply_video, reply_desktops, reply_window_memory, reply_setup)


def respond(wire):
    for reply in FEATURE_REPLIES:
        answer = reply(wire)
        if answer is not None:
            return answer
    if wire == 'j/cursorpos':
        return dict(x=100, y=100)
    if wire in ('j/monitors', 'j/monitors all'):
        return monitors()
    if wire == 'j/workspaces':
        return workspaces()
    if wire == 'j/clients':
        return [CLIENT]
    if wire == 'j/activewindow':
        return dict(CLIENT)
    if wire == 'j/binds':
        return []
    if wire.startswith('j/getoption'):
        return dict(option=wire.split()[-1], int=0, float=0.0, str='', set=False)
    if wire == 'j/plugin list':
        return [dict(name=p, author='fixture', handle='0x1', version='1', description='') for p in plugins]
    if wire.startswith('/plugin load '):
        plugins.append('xr-ownership')
        return 'ok'
    if wire.startswith('/plugin unload '):
        plugins.clear()
        return 'ok'
    if wire == '/reload':
        return 'ok'
    if wire.startswith('/eval '):
        import re
        if 'hl.monitor(' in wire:
            name = re.search(r'output\s*=\s*"([^"]+)"', wire)
            pos = re.search(r'position\s*=\s*"(-?\d+)x(-?\d+)"', wire)
            for m in physical:
                if name and pos and m['name'] == name[1]:
                    m['x'], m['y'] = int(pos[1]), int(pos[2])
        return 'ok'
    if wire.startswith('j/xrspike '):
        j = json.loads(wire[len('j/xrspike '):])
        result = broker(j)
        if result is None:
            return dict(error='ownership blocked')
        return dict(**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, epoch=EPOCH, result=result)
    return None


def handle(conn, kind):
    pid = None
    try:
        if kind == 'events':
            conn.settimeout(0.05)
            while True:
                try:
                    if not conn.recv(1024): return
                except TimeoutError:
                    pass
                for line in events_binds(): conn.sendall(line)
        if kind != 'rpc':
            while conn.recv(1024):
                pass
            return
        pid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        data = conn.recv(1 << 20)  # one write of at most 16 KiB arrives whole on a local stream socket
        wire = data.decode()
        with lock:
            log(dict(pid=pid, request=wire, t=round(time.monotonic(), 3)))
            result = respond(wire)
            if result is None and not wire.startswith('j/xrspike '):
                log(dict(pid=pid, error='unhandled request', request=wire[:200]))
                result = 'unknown request'
        conn.sendall((result if isinstance(result, str) else json.dumps(result)).encode())
    except Exception as e:
        log(dict(pid=pid, error=repr(e)))
    finally:
        conn.close()


def serve(listener, kind):
    while True:
        conn, _ = listener.accept()
        threading.Thread(target=handle, args=(conn, kind), daemon=True).start()


listeners = []
for path, kind in [(directory / '.socket.sock', 'rpc'), (directory / '.socket2.sock', 'events'),
                   (root / 'wayland-1', 'wayland')]:
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path.relative_to(root)))
    s.listen(100)
    listeners.append((s, kind))
for listener, kind in listeners:
    threading.Thread(target=serve, args=(listener, kind), daemon=True).start()
(root / 'fake-ready').write_text('ready')
while True:
    time.sleep(0.1)
