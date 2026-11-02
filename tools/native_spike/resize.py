"""Overview corner resize: drag a desktop's corner with the mouse in the glasses overview to make it bigger or smaller.

Design (decided for the zoom-out resize feature, 2026-11-01):
  * What changes is the desktop's size in space, never its pixels. A drag drives the existing per-desktop `distance`
    placement (geometry.PLACEMENT_BOUNDS, 50..200): apparent size is 100/distance, so a desktop can be shown from half
    to twice its default size and the clamp is the placement's own. The renderer renders mono and keeps the centre's
    direction when distance changes, so a nearer desktop looks exactly like a bigger one, scaled about its centre.
    Resolution and Hyprland scale stay what the desktop editor set. They change only at session start, where the
    journaled, read-back output creation (ownership.create_journaled) applies them, so this feature adds no new
    compositor output mutation and needs no new journal record kind.
  * The aspect ratio is always kept. The captured image has the output's fixed pixel aspect, so a free resize could
    only stretch text. Changing shape stays a desktop-editor setting (applied at the next start).
  * The size is saved with the other placements in the glasses model's profile (PlacementStore), 0.5 s after the last
    change and at once on release. The panel sees it through the run's status reply, and an open placement menu shows it.

How it is wired (supervisor side; the renderer only draws the handle):
  * PointerPoller, a background thread, reads the pointer with the allowlisted read-only `cursorpos` query about 30
    times a second while the overview is shown (never while zoomed) and the XR-n output geometry every 2 s.
  * CornerResize, on the supervisor loop, hit-tests the pointer against each desktop's corner zones. On hover it
    shows a subtle corner handle (renderer op `resize_handle`) and asks for two modal left-button binds: press runs
    `resize_press`, release runs `resize_release`, through the same xr_control.py helper as every run key. These binds
    exist only while the pointer is in a corner zone or a drag is in progress. Ordinary clicks anywhere else are never
    taken. While they exist, the left click at that corner resizes instead of reaching the window under it.
  * OLED care, the same rules as the help card and the menu: the handle's brightest level is 184/255 (cap 191), it
    follows the cards' 7x7 pixel orbit, and it hides after OVERLAY_IDLE_CLOSE_S without pointer movement (the binds go
    with it, and a drag still held then ends and is saved).
"""
import json
import socket
import threading
import time

from . import view
from .geometry import PLACEMENT_BOUNDS

RESIZE_OPS = ('resize_press', 'resize_release')
CORNERS = ('tl', 'tr', 'bl', 'br')
# Corner zone: a square of this share of the desktop's shorter logical side, clamped to [min, max] logical pixels.
ZONE_FRACTION, ZONE_MIN, ZONE_MAX = 0.08, 32, 160
POLL_PERIOD = 1 / 30          # pointer reads per second while the overview shows
IDLE_PERIOD = 0.1             # zoomed in: no pointer reads, just this wake-up to notice the overview again
OUTPUTS_REFRESH = 2.0         # seconds between XR-n geometry reads
MIN_GAIN = 0.2                # a drag can at most divide the start size by 5 (the distance bound clamps first anyway)
RESIZE_BUTTON = 'mouse:272'   # left button
BIND_TABLE = 'xr_workspace_resize_binds'


def outputs_from_monitors(raw, names):
    """Logical rectangles of this run's managed outputs, in `names` order: [{'index', 'x', 'y', 'width', 'height'}]."""
    out = []
    if not isinstance(raw, list): return out
    for index, name in enumerate(names):
        m = next((m for m in raw if isinstance(m, dict) and m.get('name') == name), None)
        if m is None: continue
        w, h, x, y, scale = (m.get(k) for k in ('width', 'height', 'x', 'y', 'scale'))
        if (type(w) is not int or type(h) is not int or type(x) is not int or type(y) is not int
                or type(scale) not in (int, float) or not 0.25 <= scale <= 4 or w <= 0 or h <= 0):
            continue
        out.append({'index': index, 'x': x, 'y': y, 'width': w / scale, 'height': h / scale})
    return out


def zone_size(width, height):
    return min(max(round(min(width, height) * ZONE_FRACTION), ZONE_MIN), ZONE_MAX)


def corner_at(pointer, outputs):
    """(desktop index, corner) if the global logical pointer is in one of a desktop's corner zones, else None."""
    if pointer is None: return None
    px, py = pointer
    for o in outputs:
        lx, ly = px - o['x'], py - o['y']
        if not (0 <= lx < o['width'] and 0 <= ly < o['height']): continue
        z = zone_size(o['width'], o['height'])
        horizontal = 'l' if lx < z else 'r' if lx >= o['width'] - z else None
        vertical = 't' if ly < z else 'b' if ly >= o['height'] - z else None
        return (o['index'], vertical + horizontal) if horizontal and vertical else None
    return None


def drag_distance(start, corner, dx, dy, width, height):
    """New `distance` for a drag of (dx, dy) logical pixels from `corner` of a width x height desktop at `start`.

    Moving outward along either axis grows the desktop (smaller distance). The pointer stops at the edge of the monitor
    layout, so either axis alone is enough: (width + height) / 2 pixels outward doubles the size."""
    if corner not in CORNERS: raise ValueError('invalid corner')
    sx = -1 if corner[1] == 'l' else 1
    sy = -1 if corner[0] == 't' else 1
    gain = max(MIN_GAIN, 1 + (dx * sx + dy * sy) / ((width + height) / 2))
    lo, hi = PLACEMENT_BOUNDS['distance']
    return min(max(round(start / gain), lo), hi)


def install_resize_binds_lua(python, client, control_socket, run_id, session):
    """The modal left-button press/release binds (replacing any surviving set, so never doubled); generated Lua only."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not view.SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not view.SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    base = f'{python} -I {client} {control_socket} {run_id} {session}'
    return (remove_resize_binds_lua() + f' local c = "{base} " {BIND_TABLE} = {{ '
            f'hl.bind("{RESIZE_BUTTON}", hl.dsp.exec_cmd(c .. "resize_press")), '
            f'hl.bind("{RESIZE_BUTTON}", hl.dsp.exec_cmd(c .. "resize_release"), {{ release = true }}) }}')


def remove_resize_binds_lua():
    return view._remove_lua(BIND_TABLE)


def handle_frame(run_id, session, request_id, handle):
    if (not isinstance(handle, dict) or set(handle) != {'visible', 'desktop', 'corner', 'active'}
            or type(handle['visible']) is not bool or type(handle['active']) is not bool
            or type(handle['desktop']) is not int or not 0 <= handle['desktop'] <= 2 or handle['corner'] not in CORNERS):
        raise ValueError('invalid resize handle')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'resize_handle',
                        'payload': dict(handle)}, separators=(',', ':')) + '\n').encode()


HIDDEN = {'visible': False, 'desktop': 0, 'corner': 'br', 'active': False}


class CornerResize:
    """Hover/drag state on the supervisor loop. The poller thread only hands in samples (feed) and reads binds_wanted."""
    def __init__(self, control, clock=time.monotonic):
        self.control, self.clock = control, clock
        self._lock = threading.Lock()
        self._sample, self._fresh = (None, []), False
        self.pointer, self.outputs = None, []
        self.last_move = clock()
        self.hover = None          # (index, corner) under the pointer
        self.drag = None           # {'index', 'corner', 'start', 'distance', 'width', 'height'}
        self.shown = dict(HIDDEN)  # last handle state sent to the renderer
        self.binds_wanted = False  # read by the poller thread
        self.binds_dropped = False # set on a Hyprland config reload (runtime binds are gone); cleared by the poller
        self.drags = 0
        reader, writer = socket.socketpair()
        reader.setblocking(False); writer.setblocking(False)
        self.wake_reader, self._wake_writer = reader, writer

    def close(self):
        self.wake_reader.close(); self._wake_writer.close()

    def overview(self):
        return not self.control.state.zoomed

    def feed(self, pointer, outputs):
        """Poller thread: latest pointer (global logical px, or None) and output rectangles; wakes the loop on change."""
        with self._lock:
            changed = (pointer, outputs) != self._sample
            if changed: self._sample, self._fresh = (pointer, outputs), True
        if changed:
            try: self._wake_writer.send(b'.')
            except OSError: pass  # full or closed: the loop's own 50 ms tick picks the sample up
        return changed

    def drain(self):
        try:
            while self.wake_reader.recv(4096): pass
        except OSError: pass

    def _take(self, now):
        with self._lock:
            fresh, (pointer, outputs) = self._fresh, self._sample
            self._fresh = False
        if not fresh: return
        if pointer != self.pointer: self.last_move = now
        self.pointer, self.outputs = pointer, outputs

    def _output(self, index):
        return next((o for o in self.outputs if o['index'] == index), None)

    def poll(self, now):
        """Supervisor tick: apply pointer movement to hover and an active drag; returns renderer frames (bytes)."""
        self._take(now)
        out = b''
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        if not self.overview() or idle:
            out += self._end_drag()
            self.hover = None
        else:
            self.hover = corner_at(self.pointer, self.outputs)
            if self.drag is not None: out += self._apply_drag()
        self.binds_wanted = self.hover is not None or self.drag is not None
        return out + self._handle()

    def on_op(self, op):
        if op not in RESIZE_OPS: raise ValueError('unsupported resize operation')
        now = self.clock()
        self._take(now)
        self.last_move = now  # a click is input: the handle stays up
        if self.overview(): self.hover = corner_at(self.pointer, self.outputs) or self.hover
        out = b''
        if op == 'resize_release':
            out += self._apply_drag() + self._end_drag()
        else:
            out += self._end_drag()  # a press during a drag: its release was lost; finish that one first
            if self.overview() and self.hover is not None and self.pointer is not None:
                index, corner = self.hover
                o = self._output(index)
                if o is not None and index < len(self.control.state.placements):
                    self.drag = {'index': index, 'corner': corner, 'start': self.pointer, 'width': o['width'],
                                 'height': o['height'], 'distance': self.control.state.placements[index]['distance']}
                    self.drags += 1
        self.binds_wanted = self.hover is not None or self.drag is not None
        return (out + self._handle()) or None

    def _apply_drag(self):
        d = self.drag
        if d is None or self.pointer is None: return b''
        distance = drag_distance(d['distance'], d['corner'], self.pointer[0] - d['start'][0], self.pointer[1] - d['start'][1],
                                 d['width'], d['height'])
        c = self.control
        current = c.state.placements[d['index']]
        if current['distance'] == distance: return b''
        c.state.placements = [{**p, 'distance': distance} if i == d['index'] else p for i, p in enumerate(c.state.placements)]
        c.save_due = c.clock() + view.MENU_SAVE_DELAY
        return c._placement_frame() + (c._menu_frame() if c.state.menu_open else b'')

    def _end_drag(self):
        if self.drag is None: return b''
        self.drag = None
        self.control.flush()  # saved at once on release (no-op if nothing changed)
        return b''

    def _handle(self):
        target = (self.drag['index'], self.drag['corner']) if self.drag is not None else self.hover
        want = ({'visible': True, 'desktop': target[0], 'corner': target[1], 'active': self.drag is not None}
                if target is not None else {**self.shown, 'visible': False, 'active': False})
        if want == self.shown: return b''
        self.shown = want
        c = self.control
        frame = handle_frame(c.run_id, c.session, c.next_id, want)
        c.next_id += 1
        return frame


class PointerPoller:
    """Background reads of the pointer (cursorpos) and XR-n geometry, plus the modal resize binds' compositor calls.

    Every compositor call for this feature happens here, never on the supervisor loop. stop() removes any installed
    binds before returning, so none outlives the run."""
    def __init__(self, client, names, resize, bind_args, period=POLL_PERIOD):
        self.client, self.names, self.resize, self.bind_args, self.period = client, list(names), resize, bind_args, period
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.installed = self.ever_installed = False
        self.errors = 0

    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=2.5):
        self._stop.set()
        self._thread.join(timeout)
        if self.ever_installed:  # best effort even if the thread is stuck; xr_control drops stale ones on the next click
            try: self.client.remove_resize_binds()
            except (OSError, ValueError, TypeError, KeyError): pass
            self.installed = False

    def _pointer(self):
        pos = self.client.query('cursorpos', timeout=0.25)
        if not isinstance(pos, dict) or type(pos.get('x')) not in (int, float) or type(pos.get('y')) not in (int, float):
            return None
        return (int(pos['x']), int(pos['y']))

    def _run(self):
        outputs, refreshed = [], None
        while not self._stop.is_set():
            overview = self.resize.overview()
            if overview:
                now = time.monotonic()
                try:
                    if refreshed is None or now - refreshed >= OUTPUTS_REFRESH:
                        outputs, refreshed = outputs_from_monitors(self.client.query('monitors', timeout=0.5), self.names), now
                    pointer = self._pointer()
                except (OSError, ValueError, TypeError, KeyError):
                    pointer = None; self.errors += 1
                self.resize.feed(pointer, outputs)
            else:
                self.resize.feed(None, outputs)
            self._sync_binds()
            self._stop.wait(self.period if overview else IDLE_PERIOD)

    def _sync_binds(self):
        if self.resize.binds_dropped:
            self.resize.binds_dropped = False; self.installed = False
        want = self.resize.binds_wanted and not self._stop.is_set()
        if want == self.installed: return
        try:
            if want:
                self.ever_installed = True  # set first: a failed or partial install is still removed at stop
                self.client.install_resize_binds(*self.bind_args)
            else: self.client.remove_resize_binds()
        except (OSError, ValueError, TypeError, KeyError):
            return  # best effort; retried on the next tick
        self.installed = want
