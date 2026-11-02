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
    times a second while the overview is shown (never while zoomed, and never while the placement menu is open, whose
    mouse mode owns the left button) and the XR-n output geometry every 2 s.
  * CornerResize, on the supervisor loop, hit-tests the pointer against each desktop's corner zones. On hover it
    shows a subtle corner handle (renderer op `resize_handle`) and asks for two modal left-button binds: press runs
    `resize_press`, release runs `resize_release`, through the same xr_control.py helper as every run key. These binds
    exist only while the pointer is in a corner zone or a drag is in progress. Ordinary clicks anywhere else are never
    taken. While they exist, the left click at that corner resizes instead of reaching the window under it.
Grab pill (move), added 2026-11-02:
  * A long, thin pill is drawn centred just above each desktop in the overview. Its grab zone is the top-centre strip of
    the desktop's own pixels (the pointer can never be above a desktop), PILL_SHARE of its width and as tall as a corner
    zone. The corner zones win where they meet, and the strip is kept clear of them, so a click is a resize or a move,
    never both. Hovering the strip brightens that desktop's pill.
  * Dragging the pill moves the desktop like a title bar: it changes the desktop's `x` / `y` placement (percent of its own
    width / height, PLACEMENT_BOUNDS -100..100), live, saved like a size. During the drag the cursor is hidden and kept
    near the middle of its monitor (the placement menu's mouse-mode technique, own Lua state), so a desktop can go up or
    sideways even though the pointer sits at the top of the layout. On release the cursor is shown again where it was,
    which is still on the pill: the pill moved with the desktop's pixels.
  * Free placement with two guards, not a snap grid. Each axis snaps back to the desktop's own row slot (0) within
    SNAP_PCT, so putting it back is easy. A move is clamped so the desktop overlaps no other desktop by more than
    MAX_OVERLAP of the smaller one's area (approximate overview rectangles: shared height, own aspect, apparent size
    100/distance, rotation ignored, gaps ignored so the guard errs early). The bounds keep it within one desktop width or
    height of its slot, so it stays in reach. If the target overlaps, the desktop slides along the free axis, else stops.
  * Corner resize and pill drag use the same modal left-button binds and ops (resize_press / resize_release), so
    whatever disables one (zoomed in, the placement menu open, a VR video playing) disables the other.
  * OLED care: the handle's brightest level is 184/255 (the cards' cap is 191), and it hides after OVERLAY_IDLE_CLOSE_S
    without pointer movement (the binds go with it, and a drag still held then ends and is saved). It has no pixel
    orbit or drift (that is being removed from the cards in feature/remove-pixel-orbit).
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
# Grab pill: hover strip PILL_SHARE of the desktop's width (clamped to logical px), as tall as a corner zone.
PILL_SHARE, PILL_MIN, PILL_MAX = 0.3, 120, 720
MOVE_GAIN = 2.0          # percent of the desktop's own width/height per percent of mouse travel over it (half its width moves it a full width)
SNAP_PCT = 4             # an axis within this many percent of the row slot snaps to it
MAX_OVERLAP = 0.10       # share of the smaller desktop's area another desktop may cover after a move
JUMP_PX = 400            # one pointer step farther than this is a cursor warp (re-centring), not a hand movement
EDGE_PX = 200            # while moving, a hidden cursor this close to its monitor's edge is put back in the middle
GRAB_PERIOD = 1 / 60     # pointer reads per second during a pill drag
GRAB_TABLE = 'xr_workspace_grab'
# Pill drag, entering: remember the cursor's visibility and position once, put it in the middle of its monitor, hide it.
# Every compositor call is in pcall, as in view._MOUSE_ENTER_LUA; a refused cursor change never breaks the drag.
GRAB_ENTER_LUA = (f'if {GRAB_TABLE} == nil then '
                  'local ok, hidden = pcall(hl.get_config, "cursor.invisible") local p = hl.get_cursor_pos() '
                  f'{GRAB_TABLE} = {{ invisible = ok and hidden == true, home = p and {{ x = p.x, y = p.y }} }} end '
                  'local m = hl.get_monitor_at_cursor() '
                  'if m then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = m.x + m.width / m.scale / 2, '
                  'y = m.y + m.height / m.scale / 2 })) end) end '
                  'pcall(hl.config, { cursor = { invisible = true } })')
# Re-centring during the drag (the hidden cursor neared an edge); never touches the saved home.
GRAB_RECENTRE_LUA = ('local m = hl.get_monitor_at_cursor() '
                     'if m then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = m.x + m.width / m.scale / 2, '
                     'y = m.y + m.height / m.scale / 2 })) end) end')
# Leaving: visibility and position back. A no-op when no drag hid the cursor, so it also rides along with every removal
# of the resize binds (including xr_control.py's stale-bind cleanup after a run died mid-drag).
GRAB_LEAVE_LUA = (f'do local s = {GRAB_TABLE} {GRAB_TABLE} = nil if s then '
                  'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                  'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                  'end end')
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


def pill_at(pointer, outputs):
    """Desktop index if the pointer is in a desktop's top-centre grab strip (never inside a corner zone), else None."""
    if pointer is None: return None
    px, py = pointer
    for o in outputs:
        lx, ly = px - o['x'], py - o['y']
        if not (0 <= lx < o['width'] and 0 <= ly < o['height']): continue
        z = zone_size(o['width'], o['height'])
        half = min(max(o['width'] * PILL_SHARE, PILL_MIN), PILL_MAX, o['width'] - 4 * z) / 2  # clear of both corners
        return o['index'] if ly < z and half > 0 and abs(lx - o['width'] / 2) <= half else None
    return None


def target_at(pointer, outputs):
    """What a left click would grab: ('corner', index, corner), ('pill', index) or None. Corners win."""
    corner = corner_at(pointer, outputs)
    if corner is not None: return ('corner',) + corner
    pill = pill_at(pointer, outputs)
    return ('pill', pill) if pill is not None else None


def _snap(value):
    return 0 if abs(value) <= SNAP_PCT else value


def move_offset(start, dx, dy, width, height):
    """New (x, y) placement for a pill drag of (dx, dy) mouse pixels from offsets `start` on a width x height desktop.

    Right is +x, down is -y (placement y is up). Clamped to PLACEMENT_BOUNDS, each axis snapped to the row slot."""
    (xlo, xhi), (ylo, yhi) = PLACEMENT_BOUNDS['x'], PLACEMENT_BOUNDS['y']
    x = min(max(round(start[0] + dx / width * 100 * MOVE_GAIN), xlo), xhi)
    y = min(max(round(start[1] - dy / height * 100 * MOVE_GAIN), ylo), yhi)
    return _snap(x), _snap(y)


def apparent_rects(aspects, placements):
    """Approximate overview rectangles (cx, cy, w, h) in units of the shared tile height: tiles side by side with no gap,
    offset by x/y percent of their own size, apparent size 100/distance. Rotation and curvature are ignored."""
    out, left = [], 0.0
    for aspect, p in zip(aspects, placements):
        k = 100 / p['distance']
        out.append((left + aspect / 2 + p['x'] / 100 * aspect, p['y'] / 100, aspect * k, k))
        left += aspect
    return out


def _overlap(a, b):
    w = min(a[0] + a[2] / 2, b[0] + b[2] / 2) - max(a[0] - a[2] / 2, b[0] - b[2] / 2)
    h = min(a[1] + a[3] / 2, b[1] + b[3] / 2) - max(a[1] - a[3] / 2, b[1] - b[3] / 2)
    return max(w, 0) * max(h, 0)


def overlap_ok(index, placements, aspects):
    """False if desktop `index` covers another desktop by more than MAX_OVERLAP of the smaller one's area."""
    rects = apparent_rects(aspects, placements)
    if index >= len(rects): return True
    a = rects[index]
    return all(_overlap(a, b) <= MAX_OVERLAP * min(a[2] * a[3], b[2] * b[3]) + 1e-9 for i, b in enumerate(rects) if i != index)


def _with(placements, index, xy):
    return [{**p, 'x': xy[0], 'y': xy[1]} if i == index else p for i, p in enumerate(placements)]


def _covered(index, placements, aspects):
    rects = apparent_rects(aspects, placements)
    return sum(_overlap(rects[index], b) for i, b in enumerate(rects) if i != index)


def allowed_move(index, current, target, placements, aspects):
    """The (x, y) a move toward `target` may take from `current` without a bad overlap: the target if it is fine, else the
    closest of: as far as possible straight toward it, x first then y, y first then x (so a desktop slides along a
    neighbour instead of sticking to it). A desktop that already overlaps (set from the panel) may still move anywhere it
    overlaps no more than now, so it is never stuck."""
    now = _covered(index, _with(placements, index, current), aspects)

    def ok(xy):
        trial = _with(placements, index, xy)
        return overlap_ok(index, trial, aspects) or _covered(index, trial, aspects) <= now + 1e-9

    def reach(a, b):  # farthest whole-percent point from a toward b that stays ok
        best, steps = a, max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        for i in range(1, steps + 1):
            xy = (round(a[0] + (b[0] - a[0]) * i / steps), round(a[1] + (b[1] - a[1]) * i / steps))
            if not ok(xy): break
            best = xy
        return best

    current, target = tuple(current), tuple(target)
    if ok(target): return target
    candidates = [reach(current, target),
                  reach(reach(current, (target[0], current[1])), target),
                  reach(reach(current, (current[0], target[1])), target)]
    return min(candidates, key=lambda xy: ((xy[0] - target[0]) ** 2 + (xy[1] - target[1]) ** 2, xy != current))


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
    """Also shows the cursor again and puts it back if a pill drag hid it (no-op otherwise)."""
    return view._remove_lua(BIND_TABLE) + ' ' + GRAB_LEAVE_LUA


def pill_frame(run_id, session, request_id, pill):
    """Renderer `move_handle` frame: the overview's grab pills. visible = shown above every desktop; desktop + hover =
    the one under the pointer (brighter); active = that one is being dragged (brightest)."""
    if (not isinstance(pill, dict) or set(pill) != {'visible', 'desktop', 'hover', 'active'}
            or any(type(pill[k]) is not bool for k in ('visible', 'hover', 'active'))
            or type(pill['desktop']) is not int or not 0 <= pill['desktop'] <= 2):
        raise ValueError('invalid move handle')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'move_handle',
                        'payload': dict(pill)}, separators=(',', ':')) + '\n').encode()


PILLS_HIDDEN = {'visible': False, 'desktop': 0, 'hover': False, 'active': False}


def handle_frame(run_id, session, request_id, handle):
    if (not isinstance(handle, dict) or set(handle) != {'visible', 'desktop', 'corner', 'active'}
            or type(handle['visible']) is not bool or type(handle['active']) is not bool
            or type(handle['desktop']) is not int or not 0 <= handle['desktop'] <= 2 or handle['corner'] not in CORNERS):
        raise ValueError('invalid resize handle')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'resize_handle',
                        'payload': dict(handle)}, separators=(',', ':')) + '\n').encode()


HIDDEN = {'visible': False, 'desktop': 0, 'corner': 'br', 'active': False}


class CornerResize:
    """Hover/drag state (corner resize and pill move) on the supervisor loop. The poller thread only hands in samples
    (feed) and reads binds_wanted / grab_wanted."""
    def __init__(self, control, clock=time.monotonic):
        self.control, self.clock = control, clock
        self._lock = threading.Lock()
        self._sample, self._fresh = (None, []), False
        self.pointer, self.outputs = None, []
        self.last_move = clock()
        self.hover = None          # target_at(): ('corner', index, corner), ('pill', index) or None
        # Corner: {'kind': 'corner', 'index', 'corner', 'start', 'distance', 'width', 'height'}
        # Pill:   {'kind': 'pill', 'index', 'start': (x, y) offsets, 'moved': [dx, dy] mouse px, 'last', 'width', 'height'}
        self.drag = None
        self.shown = dict(HIDDEN)        # last corner handle state sent to the renderer
        self.pills = dict(PILLS_HIDDEN)  # last pill state sent to the renderer
        self.binds_wanted = False  # read by the poller thread
        self.grab_wanted = False   # pill drag: the poller hides the cursor and keeps it centred (read by the poller)
        self.binds_dropped = False # set on a Hyprland config reload (runtime binds are gone); cleared by the poller
        self.drags = self.moves = 0
        reader, writer = socket.socketpair()
        reader.setblocking(False); writer.setblocking(False)
        self.wake_reader, self._wake_writer = reader, writer

    def close(self):
        self.wake_reader.close(); self._wake_writer.close()

    def overview(self):
        # Off while the placement menu is open: its mouse mode binds the same left button, and the menu has its own
        # Distance slider. Opening the menu ends a drag (saved) and removes the resize binds; closing it brings them back.
        return not self.control.state.zoomed and not self.control.state.menu_open

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
        d = self.drag
        if d is not None and d['kind'] == 'pill' and pointer is not None:
            if d['last'] is not None:  # pill moves add up per step, so the hidden cursor can be re-centred under them
                dx, dy = pointer[0] - d['last'][0], pointer[1] - d['last'][1]
                if abs(dx) <= JUMP_PX and abs(dy) <= JUMP_PX: d['moved'][0] += dx; d['moved'][1] += dy
            d['last'] = pointer
        self.pointer, self.outputs = pointer, outputs

    def _output(self, index):
        return next((o for o in self.outputs if o['index'] == index), None)

    def _wanted(self):
        self.binds_wanted = self.hover is not None or self.drag is not None
        self.grab_wanted = self.drag is not None and self.drag['kind'] == 'pill'

    def poll(self, now):
        """Supervisor tick: apply pointer movement to hover and an active drag; returns renderer frames (bytes)."""
        self._take(now)
        out = b''
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        if not self.overview() or idle:
            out += self._end_drag()
            self.hover = None
        else:
            self.hover = target_at(self.pointer, self.outputs)
            if self.drag is not None: out += self._apply_drag()
        self._wanted()
        return out + self._handle() + self._pills(idle)

    def on_op(self, op):
        if op not in RESIZE_OPS: raise ValueError('unsupported resize operation')
        now = self.clock()
        self._take(now)
        self.last_move = now  # a click is input: the handle stays up
        if self.overview(): self.hover = target_at(self.pointer, self.outputs) or self.hover
        out = b''
        if op == 'resize_release':
            out += self._apply_drag() + self._end_drag()
        else:
            out += self._end_drag()  # a press during a drag: its release was lost; finish that one first
            if self.overview() and self.hover is not None and self.pointer is not None:
                index = self.hover[1]
                o = self._output(index)
                if o is not None and index < len(self.control.state.placements):
                    p = self.control.state.placements[index]
                    if self.hover[0] == 'corner':
                        self.drag = {'kind': 'corner', 'index': index, 'corner': self.hover[2], 'start': self.pointer,
                                     'width': o['width'], 'height': o['height'], 'distance': p['distance']}
                        self.drags += 1
                    else:
                        self.drag = {'kind': 'pill', 'index': index, 'start': (p['x'], p['y']), 'moved': [0, 0],
                                     'last': self.pointer, 'width': o['width'], 'height': o['height']}
                        self.moves += 1
        self._wanted()
        return (out + self._handle() + self._pills(False)) or None

    def _aspects(self):
        n = len(self.control.state.placements)
        sizes = {o['index']: o['width'] / o['height'] for o in self.outputs}
        return [sizes.get(i, 16 / 9) for i in range(n)]

    def _apply_drag(self):
        d = self.drag
        if d is None or self.pointer is None: return b''
        c = self.control
        current = c.state.placements[d['index']]
        if d['kind'] == 'corner':
            updated = {**current, 'distance': drag_distance(d['distance'], d['corner'], self.pointer[0] - d['start'][0],
                                                            self.pointer[1] - d['start'][1], d['width'], d['height'])}
        else:
            target = move_offset(d['start'], d['moved'][0], d['moved'][1], d['width'], d['height'])
            x, y = allowed_move(d['index'], (current['x'], current['y']), target, c.state.placements, self._aspects())
            updated = {**current, 'x': x, 'y': y}
        if updated == current: return b''
        c.state.placements = [updated if i == d['index'] else p for i, p in enumerate(c.state.placements)]
        c.save_due = c.clock() + view.MENU_SAVE_DELAY
        return c._placement_frame() + (c._menu_frame() if c.state.menu_open else b'')

    def _end_drag(self):
        if self.drag is None: return b''
        self.drag = None
        self.control.flush()  # saved at once on release (no-op if nothing changed)
        return b''

    def _send(self, make, state):
        c = self.control
        frame = make(c.run_id, c.session, c.next_id, state)
        c.next_id += 1
        return frame

    def _handle(self):
        d = self.drag
        if d is not None and d['kind'] == 'corner': target, active = (d['index'], d['corner']), True
        elif d is None and self.hover is not None and self.hover[0] == 'corner': target, active = self.hover[1:], False
        else: target, active = None, False
        want = ({'visible': True, 'desktop': target[0], 'corner': target[1], 'active': active}
                if target is not None else {**self.shown, 'visible': False, 'active': False})
        if want == self.shown: return b''
        self.shown = want
        return self._send(handle_frame, want)

    def _pills(self, idle):
        """Pills show above every desktop in the overview while the pointer has moved in the last 30 s (OLED idle close)."""
        visible = self.overview() and not idle and len(self.outputs) > 0
        d = self.drag
        if d is not None and d['kind'] == 'pill': target, hover, active = d['index'], True, True
        elif d is None and self.hover is not None and self.hover[0] == 'pill': target, hover, active = self.hover[1], True, False
        else: target, hover, active = self.pills['desktop'], False, False
        want = {'visible': visible, 'desktop': target, 'hover': hover and visible, 'active': active and visible}
        if not visible: want = {**want, 'hover': False, 'active': False}
        if want == self.pills: return b''
        self.pills = want
        return self._send(pill_frame, want)


class PointerPoller:
    """Background reads of the pointer (cursorpos) and XR-n geometry, plus the modal resize binds' compositor calls.

    Every compositor call for this feature happens here, never on the supervisor loop. stop() removes any installed
    binds before returning, so none outlives the run."""
    def __init__(self, client, names, resize, bind_args, period=POLL_PERIOD):
        self.client, self.names, self.resize, self.bind_args, self.period = client, list(names), resize, bind_args, period
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.installed = self.ever_installed = False
        self.grabbed = False  # GRAB_ENTER_LUA ran: the cursor is hidden until GRAB_LEAVE_LUA (also part of the bind removal)
        self.errors = 0

    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=2.5):
        self._stop.set()
        self._thread.join(timeout)
        if self.ever_installed:  # best effort even if the thread is stuck; xr_control drops stale ones on the next click
            try: self.client.remove_resize_binds()  # also shows and puts back a cursor a pill drag hid
            except (OSError, ValueError, TypeError, KeyError): pass
            self.installed = self.grabbed = False

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
                self._recentre(pointer, outputs)
            else:
                self.resize.feed(None, outputs)
            self._sync_binds()
            self._sync_grab()
            self._stop.wait((GRAB_PERIOD if self.grabbed else self.period) if overview else IDLE_PERIOD)

    def _sync_grab(self):
        want = self.resize.grab_wanted and not self._stop.is_set()
        if want == self.grabbed: return
        try:
            if want: self.client.grab_cursor()
            else: self.client.release_cursor()
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return  # retried next tick; bind removal also releases it
        self.grabbed = want

    def _recentre(self, pointer, outputs):
        """Pill drag: a hidden cursor near its monitor's edge goes back to the middle (CornerResize skips the jump)."""
        if not self.grabbed or pointer is None: return
        o = next((o for o in outputs if 0 <= pointer[0] - o['x'] < o['width'] and 0 <= pointer[1] - o['y'] < o['height']), None)
        near = o is None or min(pointer[0] - o['x'], o['x'] + o['width'] - pointer[0],
                                pointer[1] - o['y'], o['y'] + o['height'] - pointer[1]) < EDGE_PX
        if near:
            try: self.client.recentre_cursor()
            except (OSError, ValueError, TypeError, KeyError, AttributeError): pass

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
