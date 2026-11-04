"""Send the focused window to the glasses (an XR desktop) or back to the physical monitor, during a run.

Run binds (SEND_BINDS), installed and removed with the run's other Ctrl+Alt binds, in their own Lua table:

    Ctrl+Alt+G        the focused window -> the desktop selected in the glasses view (the one zoomed into, or
                      highlighted in the overview; Ctrl+Alt+Left/Right change it)
    Ctrl+Alt+1..4     the focused window -> XR-1 .. XR-4 (bound by keycode, code:10..13, like Omarchy's
                      workspace digits, so they work on any keyboard layout); a number with no such desktop in this
                      session (desktops come and go with the pills' Add and Remove) says so and moves nothing
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

The overview's window drag (free_pointer.py: Window drag) uses the same worker: lookup() finds the window shown at the
pressed point (window_at, from Hyprland's clients and monitors) and drop() moves that window by address onto the target
desktop's active regular workspace with follow off (DROP), so neither focus nor the selected desktop changes. The same
safety rules hold: never a pinned window, never a special workspace, nothing once close() ran.

Refusals and failures show a short desktop notification (notify-send, best-effort) and never affect the run.
"""
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

SEND_OPS = ('send_glasses', 'send_glasses_1', 'send_glasses_2', 'send_glasses_3', 'send_glasses_4', 'send_back')
# Digits by keycode (code:10 is the 1 key), as Omarchy binds its workspace digits.
SEND_BINDS = (('CTRL + ALT + G', 'send_glasses'), ('CTRL + ALT + code:10', 'send_glasses_1'),
              ('CTRL + ALT + code:11', 'send_glasses_2'), ('CTRL + ALT + code:12', 'send_glasses_3'),
              ('CTRL + ALT + code:13', 'send_glasses_4'),
              ('CTRL + ALT + B', 'send_back'))
ADDRESS = re.compile(r'0x[0-9a-f]{1,16}\Z')
APP = 'XR Workspace'  # notification app name and title (the product's name)
# The one dispatch a send makes (hyprland.HyprlandClient.mutate allowlists exactly this shape). It is the call Omarchy's
# own move-window-to-workspace bind makes (default/hypr/monitor-workspaces.lua), with follow on.
MOVE = 'dispatch hl.dsp.window.move({{ workspace = "{workspace}", follow = true, window = "address:{address}" }})'
MOVE_PATTERN = r'dispatch hl\.dsp\.window\.move\(\{ workspace = "([1-9][0-9]{0,9})", follow = (true|false), window = "address:0x[0-9a-f]{1,16}" \}\)'
# The overview's window drag (free_pointer.py) moves the window without following it: the focus, the cursor and the
# selected desktop stay where they are (the dot never switches desktops on its own).
DROP = 'dispatch hl.dsp.window.move({{ workspace = "{workspace}", follow = false, window = "address:{address}" }})'


def move_command(address, workspace, follow=True):
    if not isinstance(address, str) or not ADDRESS.match(address): raise ValueError('invalid window address')
    if type(workspace) is not int or not 0 < workspace <= 2147483647: raise ValueError('invalid workspace')
    return (MOVE if follow else DROP).format(workspace=workspace, address=address)


def land_lua(address, workspace, x=None, y=None):
    """One eval: window `address` onto regular workspace `workspace`, not following it; floating, then to layout pixel (x, y),
    which cancels the shift the workspace move makes (Hyprland keeps a floating window's offset from its old monitor, so it
    would land by the monitors' distance from where it was dropped). It keeps the focus: the move hands it on, a window
    focus warps the cursor to the window, and the cursor goes straight back to where the hand left it."""
    if not isinstance(address, str) or not ADDRESS.match(address): raise ValueError('invalid window address')
    if type(workspace) is not int or not 0 < workspace <= 2147483647: raise ValueError('invalid workspace')
    if (x is None) != (y is None) or any(v is not None and (type(v) is not int or not -1_000_000 <= v <= 1_000_000) for v in (x, y)):
        raise ValueError('invalid window position')
    place = f'hl.dispatch(hl.dsp.window.move({{ x = {x}, y = {y}, window = w }})) ' if x is not None else ''
    return (f'do local w = "address:{address}" local p = hl.get_cursor_pos() '
            f'hl.dispatch(hl.dsp.window.move({{ workspace = "{workspace}", follow = false, window = w }})) {place}'
            'hl.dispatch(hl.dsp.focus({ window = w })) '
            'if p then hl.dispatch(hl.dsp.cursor.move({ x = p.x, y = p.y })) end end')


def landing(window, monitor):
    """Where a floating window (Hyprland `activewindow`: at, size) dropped by a Super drag lands on `monitor` (`monitors`
    entry): as near as it can be to where the drag left it, which is still under the cursor, but whole on that monitor (the
    rest of the layout is some other desktop, elsewhere round you). Wider or taller than the monitor: from its left or top
    edge. Either way the cursor, which is on the monitor and was on the window, stays on it. Integer layout px."""
    at, size = window.get('at'), window.get('size')
    w, h, x, y, scale = (monitor.get(k) for k in ('width', 'height', 'x', 'y', 'scale'))
    def number(v): return type(v) in (int, float) and math.isfinite(v)
    if not isinstance(at, list) or not isinstance(size, list) or len(at) != 2 or len(size) != 2 \
            or not all(number(v) for v in (*at, *size, w, h, x, y, scale)) or scale <= 0:
        raise ValueError('invalid window or monitor geometry')
    def axis(v, length, lo, span): return int(round(max(lo, min(v, lo + span - length))))
    return axis(at[0], size[0], x, w / scale), axis(at[1], size[1], y, h / scale)


def _monitor(monitors, name):
    """(monitor dict, its active regular workspace id or None) for output `name`, or (None, None)."""
    if not isinstance(monitors, list): raise ValueError('invalid monitors')
    found = [m for m in monitors if isinstance(m, dict) and m.get('name') == name]
    if len(found) != 1: return None, None
    active = found[0].get('activeWorkspace')
    workspace = active.get('id') if isinstance(active, dict) else None
    return found[0], (workspace if type(workspace) is int and workspace > 0 else None)


def window_at(clients, monitors, name, x, y):
    """The window shown at layout point (x, y) on output `name` (Hyprland `clients` / `monitors` JSON), as
    {'address', 'w', 'h', 'pinned'}, or None. What is shown there: a fullscreen window on that output's active workspace
    covers it; otherwise floating windows (and pinned ones, on every workspace of their monitor) are above tiled ones, the
    most recently focused first (Hyprland raises a focused floating window)."""
    monitor, workspace = _monitor(monitors, name)
    if monitor is None or workspace is None or not isinstance(clients, list): return None
    def number(v): return type(v) in (int, float) and math.isfinite(v)
    found = []
    for c in clients:
        if not isinstance(c, dict) or not isinstance(c.get('address'), str) or not ADDRESS.match(c['address']): continue
        if c.get('mapped') is False or c.get('hidden') is True: continue
        ws = c.get('workspace')
        on = (isinstance(ws, dict) and ws.get('id') == workspace) or (c.get('pinned') is True and c.get('monitor') == monitor.get('id'))
        at, size = c.get('at'), c.get('size')
        if not on or not isinstance(at, list) or not isinstance(size, list) or len(at) != 2 or len(size) != 2 \
                or not all(number(v) for v in at + size) or size[0] <= 0 or size[1] <= 0:
            continue
        if not (at[0] <= x < at[0] + size[0] and at[1] <= y < at[1] + size[1]): continue
        rank = 0 if c.get('fullscreen') not in (None, False, 0) else 1 if c.get('floating') is True or c.get('pinned') is True else 2
        history = c.get('focusHistoryID')
        found.append((rank, history if type(history) is int and history >= 0 else 1 << 30, c))
    if not found: return None
    c = min(found, key=lambda f: f[:2])[2]
    return {'address': c['address'], 'w': c['size'][0], 'h': c['size'][1], 'pinned': c.get('pinned') is True}


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


def complete(done, success, detail=''):
    """Bounded worker completion, isolated from callback failures."""
    if done is not None:
        try: done(bool(success),str(detail).encode('utf-8')[:256].decode('utf-8','ignore'))
        except Exception: pass


class WindowSender:
    """Supervisor side: one op -> at most one window move, on a worker thread, serialized, stoppable before recovery."""
    def __init__(self, client, names, fallback, notify=notify, deadline=2.0):
        self.client, self.names, self.fallback, self.notify = client, list(names), fallback, notify
        self.deadline = deadline        # per Hyprland request (hyprland.DEADLINE)
        self._lock = threading.Lock()   # held for a whole move; close() waits on it
        self.closed = False
        self._threads = []
        self.moves = 0                  # diagnostic: moves Hyprland accepted

    def set_names(self, names):
        """The run's desktops changed (a pill's Add / Remove): later sends go by the new set."""
        self.names = list(names)

    def request(self, op, selected, done=None):
        """From the supervisor loop: never blocks on the compositor."""
        if op not in SEND_OPS or self.closed:
            complete(done,False,'Window sender is closed or request invalid'); return
        t = threading.Thread(target=self._work, args=(op, selected, done), daemon=True); t.start()
        self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _work(self, op, selected, done=None):
        with self._lock:
            if self.closed:
                complete(done,False,'Window sender closed'); return
            try: message = self.move(op, selected, verify=done is not None)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc: message = str(exc) or 'Could not move the window.'
        complete(done,message is None,message or 'Window target verified')
        if message and self.notify is not None: self.notify(message)

    def move(self, op, selected, verify=False):
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
        if current == target:
            workspace=window.get('workspace',{}).get('id')
            if verify and (type(workspace) is not int or workspace<=0): return 'Target window is not on a regular workspace'
            return None  # authoritative activewindow + monitor identity already match
        found = [m for m in monitors if isinstance(m, dict) and m.get('name') == target]
        if len(found) != 1: return f'{target} is not available.'
        active = found[0].get('activeWorkspace')
        workspace = active.get('id') if isinstance(active, dict) else None
        if type(workspace) is not int or workspace <= 0: return f'{target} is not showing a regular workspace.'
        reply = self.client.mutate(move_command(address, workspace))
        if reply != 'ok': return 'Hyprland did not move the window.'
        if verify:
            clients = self.client.query('clients',timeout=self.deadline)
            after = self.client.query('monitors',timeout=self.deadline)
            targets = [m for m in after if isinstance(m,dict) and m.get('name')==target]
            found = [w for w in clients if isinstance(w,dict) and w.get('address')==address]
            if len(targets)!=1 or len(found)!=1 or found[0].get('monitor')!=targets[0].get('id') or found[0].get('workspace',{}).get('id')!=workspace or found[0].get('pinned') is not False:
                return 'Window target/workspace readback mismatch; window remains under compositor authority.'
        self.moves += 1
        return None

    # --- the overview's window drag (free_pointer.py) ----------------------------------------------------------------
    def lookup(self, name, point, done):
        """From the supervisor loop: find the window shown at layout `point` on output `name` off the loop, then call
        done(window_at's dict or None) from the worker. Never blocks on the compositor; nothing if closed."""
        if self.closed: return
        def work():
            try:
                found = window_at(self.client.query('clients', timeout=self.deadline),
                                  self.client.query('monitors', timeout=self.deadline), name, *point)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                found = None
            done(found)
        self._start(work)

    def under_cursor(self, done):
        """From the supervisor loop: find the window under the cursor now, if the cursor is on one of the run's desktops,
        off the loop, then call done(window_at's dict or None) from the worker. A Super drag's press: that is the window
        Hyprland just began dragging. Never blocks on the compositor; nothing if closed."""
        if self.closed: return
        def work():
            try:
                pos = self.client.query('cursorpos', timeout=self.deadline)
                monitors = self.client.query('monitors', timeout=self.deadline)
                found = None
                for name in self.names:
                    m, _ = _monitor(monitors, name)
                    if m is not None and m['x'] <= pos['x'] < m['x'] + m['width'] / m['scale'] \
                            and m['y'] <= pos['y'] < m['y'] + m['height'] / m['scale']:
                        found = window_at(self.client.query('clients', timeout=self.deadline), monitors, name, pos['x'], pos['y'])
            except (OSError, ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
                found = None
            done(found)
        self._start(work)

    def land(self, address, target):
        """From the supervisor loop: a Super drag that crossed desktops was dropped with the cursor on output `target`: see
        that window `address` is there (land_now). Off the loop, serialized with the send keys; a failure is a desktop
        notification."""
        if self.closed: return
        def work():
            with self._lock:
                if self.closed: return
                try: message = self.land_now(address, target)
                except (OSError, ValueError, TypeError, KeyError, AttributeError): message = 'Could not move the window.'
            if message and self.notify is not None: self.notify(message)
        self._start(work)

    def land_now(self, address, target):
        """Do it now (caller holds the lock). Hyprland hands a dragged window to the monitor under its middle, so a floating
        one held far from its middle can be dropped with the cursor on one monitor and itself on another: then it moves to
        the cursor's, placed by landing. Only the window the drag ended on: Hyprland focuses that one at the end of a drag,
        so anything else active means this is not it (it never moves a window the drag did not hold). Returns a message for
        the user, or None."""
        if target not in self.names: return f'{target} is not part of this session.'
        monitors = self.client.query('monitors', timeout=self.deadline)
        found, workspace = _monitor(monitors, target)
        if found is None or workspace is None: return None
        active = self.client.query('activewindow', timeout=self.deadline)
        if not isinstance(active, dict) or active.get('address') != address or active.get('pinned'): return None
        on = active.get('workspace')
        if isinstance(on, dict) and on.get('id') == workspace: return None  # it is there: Hyprland's own rule took it
        x, y = landing(active, found) if active.get('floating') is True else (None, None)  # a tiled one tiles there
        self.client.land_window(address, workspace, x, y)
        self.moves += 1
        return None

    def drop(self, window, source, point, target):
        """From the supervisor loop: move the dragged window (window_at's dict, or None: whatever window_at finds at layout
        `point` on output `source` now) onto the workspace output `target` shows, not following it. Off the loop,
        serialized with the send keys; a refusal or failure is a desktop notification."""
        if self.closed: return
        def work():
            with self._lock:
                if self.closed: return
                try: message = self.drop_now(window, source, point, target)
                except (OSError, ValueError, TypeError, KeyError, AttributeError): message = 'Could not move the window.'
            if message and self.notify is not None: self.notify(message)
        self._start(work)

    def drop_now(self, window, source, point, target):
        """Do it now (caller holds the lock). Returns a message for the user, or None if it moved."""
        if target not in self.names: return f'{target} is not part of this session.'
        monitors = self.client.query('monitors', timeout=self.deadline)
        if window is None:
            window = window_at(self.client.query('clients', timeout=self.deadline), monitors, source, *point)
            if window is None: return None  # nothing under the press: nothing to move
        if window.get('pinned'): return 'Pinned windows stay where they are: unpin it first.'
        address = window.get('address')
        if not isinstance(address, str) or not ADDRESS.match(address): return 'Could not identify the window.'
        found, workspace = _monitor(monitors, target)
        if found is None: return f'{target} is not available.'
        if workspace is None: return f'{target} is not showing a regular workspace.'
        reply = self.client.mutate(move_command(address, workspace, follow=False))
        if reply != 'ok': return 'Hyprland did not move the window.'
        self.moves += 1
        return None

    def _start(self, work):
        t = threading.Thread(target=work, daemon=True); t.start()
        self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def close(self, timeout=None):
        """Run end, before the binds go and recovery starts: no move starts after this; waits (bounded) for one in flight."""
        self.closed = True
        if self._lock.acquire(timeout=3 * self.deadline + 0.5 if timeout is None else timeout): self._lock.release()
        for t in self._threads: t.join(0.1)
