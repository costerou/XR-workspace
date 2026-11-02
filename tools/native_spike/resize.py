"""Overview corner resize and drag bar (grab pill): the shared pieces. The pointer, hit testing and drags live in
free_pointer.py (the overview free pointer, 2026-11-02), which replaced this module's logical-pixel zones and its
30 Hz pointer poller.

Design (decided for the zoom-out resize feature, 2026-11-01, still in force):
  * What changes is the desktop's size in space, never its pixels. A corner drag drives the existing per-desktop
    `distance` placement (geometry.PLACEMENT_BOUNDS, 50..200): apparent size is 100/distance, so a desktop can be shown
    from half to twice its default size and the clamp is the placement's own. Resolution and Hyprland scale stay what
    the desktop editor set; they change only at session start through the journaled, read-back output creation, so
    this feature adds no compositor output mutation and no journal record kind. The aspect ratio is always kept.
  * A bar drag moves the desktop like a title bar through its `x` / `y` placement. Free placement with two guards, not
    a snap grid: each axis snaps back to the desktop's own row slot (0) within SNAP_PCT, and a move is clamped so the
    desktop overlaps no other desktop by more than MAX_OVERLAP of the smaller one's area (approximate overview
    rectangles: shared height, own aspect, apparent size 100/distance, rotation ignored, gaps ignored so the guard
    errs early). If the target overlaps, the desktop slides along the free axis, else stops.
  * Sizes and positions are saved with the other placements in the glasses model's profile (PlacementStore), 0.5 s
    after the last change and at once on release.
  * Both drags use the same modal left-button binds and ops (resize_press / resize_release), so whatever disables one
    (zoomed in, the placement menu open, a VR video playing) disables the other.
  * OLED care: the bracket's brightest level is 184/255 and the bars' 184/255 (the cards' cap is 191); they hide after
    OVERLAY_IDLE_CLOSE_S without pointer movement. Neither drifts or orbits.
"""
import json

from . import view

RESIZE_OPS = ('resize_press', 'resize_release')
CORNERS = ('tl', 'tr', 'bl', 'br')
RESIZE_BUTTON = 'mouse:272'   # left button
SNAP_PCT = 4             # an axis within this many percent of the row slot snaps to it
MAX_OVERLAP = 0.10       # share of the smaller desktop's area another desktop may cover after a move
# Free pointer (free_pointer.py): the cursor's visibility and position from before it was hidden, restored by every
# removal of the left-button binds (including xr_control.py's stale-bind cleanup after a run died in free mode).
GRAB_TABLE = 'xr_workspace_grab'
# Leaving: visibility and position back. A no-op when free mode never hid the cursor, so it rides along with every
# removal of the resize binds.
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


def _snap(value):
    return 0 if abs(value) <= SNAP_PCT else value


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
    """Also shows the cursor again and puts it back if free mode hid it (no-op otherwise)."""
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
