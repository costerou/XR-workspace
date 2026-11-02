"""Send the focused window to the glasses (an XR desktop) or back to the physical monitor, during a run.

Run binds (SEND_BINDS), installed and removed with the run's other Ctrl+Alt binds, in their own Lua table:

    Ctrl+Alt+G        the focused window -> the desktop selected in the glasses view (the one zoomed into, or
                      highlighted in the overview; Ctrl+Alt+Left/Right change it)
    Ctrl+Alt+1/2/3    the focused window -> XR-1 / XR-2 / XR-3 (bound by keycode, code:10..12, like Omarchy's
                      workspace digits, so they work on any keyboard layout)
    Ctrl+Alt+B        the focused window, if it is on an XR desktop -> back to the run's physical fallback monitor
                      (the output Stop and Recover return every window to)

The window lands on the workspace the target output is showing, and focus follows it. Each key runs xr_control.py,
which sends one op to the run's private control socket; the supervisor (ViewControl -> WindowSender) reads the focused
window and the monitors from Hyprland and dispatches one allowlisted move. That work runs off the watchdog loop.

Stop/Recover safety. Recovery evacuates every regular workspace of an XR output to the fallback, and refuses (leaving
recovery incomplete) when a pinned window is on it. So:
  * a pinned window is never sent to the glasses (pinned windows show on every workspace of their monitor);
  * a window always lands on a regular workspace (the target's active one, id > 0), never a special one;
  * at run end the managed run closes the sender before its binds go and before recovery starts. A move already in
    flight finishes first (recovery then evacuates it like any other window); none starts afterwards.
With no run, the binds do not exist. If a run died and its binds survived, xr_control.py finds no socket and shows a
short desktop notification instead; nothing moves.

Refusals and failures show a short desktop notification (notify-send, best-effort) and never affect the run.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

SEND_OPS = ('send_glasses', 'send_glasses_1', 'send_glasses_2', 'send_glasses_3', 'send_back')
# Digits by keycode (code:10 is the 1 key), as Omarchy binds its workspace digits.
SEND_BINDS = (('CTRL + ALT + G', 'send_glasses'), ('CTRL + ALT + code:10', 'send_glasses_1'),
              ('CTRL + ALT + code:11', 'send_glasses_2'), ('CTRL + ALT + code:12', 'send_glasses_3'),
              ('CTRL + ALT + B', 'send_back'))
ADDRESS = re.compile(r'0x[0-9a-f]{1,16}\Z')
APP = 'VITURE Workspace'
# The one dispatch a send makes (hyprland.HyprlandClient.mutate allowlists exactly this shape). It is the call Omarchy's
# own move-window-to-workspace bind makes (default/hypr/monitor-workspaces.lua), with follow on.
MOVE = 'dispatch hl.dsp.window.move({{ workspace = "{workspace}", follow = true, window = "address:{address}" }})'
MOVE_PATTERN = r'dispatch hl\.dsp\.window\.move\(\{ workspace = "([1-9][0-9]{0,9})", follow = true, window = "address:0x[0-9a-f]{1,16}" \}\)'


def move_command(address, workspace):
    if not isinstance(address, str) or not ADDRESS.match(address): raise ValueError('invalid window address')
    if type(workspace) is not int or not 0 < workspace <= 2147483647: raise ValueError('invalid workspace')
    return MOVE.format(workspace=workspace, address=address)


def notify(message, run=subprocess.run, environ=os.environ):
    """A short desktop notification; best-effort, never raises. Fixed argv, no shell."""
    binary = shutil.which('notify-send', path=environ.get('PATH') or '/usr/bin:/bin')
    if not binary: return False
    env = dict(environ)
    runtime = env.get('XDG_RUNTIME_DIR', '')
    if 'DBUS_SESSION_BUS_ADDRESS' not in env and runtime.startswith('/') and Path(runtime, 'bus').exists():
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={runtime}/bus'  # a bare systemd unit may lack it
    try:
        run([binary, '--app-name', APP, '--expire-time', '4000', APP, str(message)[:200]], env=env, timeout=2,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def target_for(op, names, selected, fallback):
    """Output name an op sends to (None: send back with no fallback)."""
    if op not in SEND_OPS: raise ValueError('unsupported send operation')
    if op == 'send_back': return fallback
    return selected if op == 'send_glasses' else f'XR-{op[-1]}'


class WindowSender:
    """Supervisor side: one op -> at most one window move, on a worker thread, serialized, stoppable before recovery."""
    def __init__(self, client, names, fallback, notify=notify, deadline=2.0):
        self.client, self.names, self.fallback, self.notify = client, list(names), fallback, notify
        self.deadline = deadline        # per Hyprland request (hyprland.DEADLINE)
        self._lock = threading.Lock()   # held for a whole move; close() waits on it
        self.closed = False
        self._threads = []
        self.moves = 0                  # diagnostic: moves Hyprland accepted

    def request(self, op, selected):
        """From the supervisor loop: never blocks on the compositor."""
        if op not in SEND_OPS or self.closed: return
        t = threading.Thread(target=self._work, args=(op, selected), daemon=True); t.start()
        self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _work(self, op, selected):
        with self._lock:
            if self.closed: return  # the run is ending: recovery owns the outputs now
            try: message = self.move(op, selected)
            except (OSError, ValueError, TypeError, KeyError, AttributeError): message = 'Could not move the window.'
        if message and self.notify is not None: self.notify(message)

    def move(self, op, selected):
        """Do it now (caller holds the lock). Returns a message for the user, or None if it moved or had nothing to do."""
        target = target_for(op, self.names, selected, self.fallback)
        if op != 'send_back' and target not in self.names: return f'{target} is not part of this session.'
        if target is None: return 'This session has no physical monitor to send windows back to.'
        window = self.client.query('activewindow', timeout=self.deadline)
        if not isinstance(window, dict) or not window.get('address'): return 'No focused window to send.'
        address = window['address']
        if not isinstance(address, str) or not ADDRESS.match(address): return 'Could not identify the focused window.'
        if window.get('pinned') is not False: return 'Pinned windows stay where they are: unpin it first.'
        monitors = self.client.query('monitors', timeout=self.deadline)
        if not isinstance(monitors, list): raise ValueError('invalid monitors')
        current = next((m.get('name') for m in monitors if isinstance(m, dict) and type(m.get('id')) is int
                        and m.get('id') == window.get('monitor')), None)
        if op == 'send_back' and current not in self.names: return 'That window is not on the glasses.'
        if current == target: return None  # already there
        found = [m for m in monitors if isinstance(m, dict) and m.get('name') == target]
        if len(found) != 1: return f'{target} is not available.'
        active = found[0].get('activeWorkspace')
        workspace = active.get('id') if isinstance(active, dict) else None
        if type(workspace) is not int or workspace <= 0: return f'{target} is not showing a regular workspace.'
        reply = self.client.mutate(move_command(address, workspace))
        if reply != 'ok': return 'Hyprland did not move the window.'
        self.moves += 1
        return None

    def close(self, timeout=None):
        """Run end, before the binds go and recovery starts: no move starts after this; waits (bounded) for one in flight."""
        self.closed = True
        if self._lock.acquire(timeout=3 * self.deadline + 0.5 if timeout is None else timeout): self._lock.release()
        for t in self._threads: t.join(0.1)
