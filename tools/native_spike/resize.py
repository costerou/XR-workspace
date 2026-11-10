"""Overview corner resize and glass pill moves: the shared pieces. The pointer, hit testing, drags, pill clicks and the
placement panel's sliders live in free_pointer.py (the overview free pointer, 2026-11-02), which replaced this module's
logical-pixel zones and its 30 Hz pointer poller; the pills' look and geometry are the renderer's (glass.hpp).

Design (decided for the zoom-out resize feature, 2026-11-01, still in force):
  * What changes is the desktop's size in space, never its pixels. A corner drag drives the existing per-desktop
    `distance` placement (geometry.PLACEMENT_BOUNDS, 10..200): apparent size is 100/distance, so a desktop can be shown
    from half to ten times its default size (well past the field of view; raised from twice on 2026-11-02) and the
    clamp is the placement's own. Resolution and Hyprland scale stay what
    the desktop editor set; they change only at session start through the journaled, read-back output creation, so
    this feature adds no compositor output mutation and no journal record kind. The aspect ratio is always kept.
  * A pill drag moves the desktop like a title bar through its `x` / `y` placement (the pill sits under the desktop;
    an expanded pill's panel header drags the same way). Since 2026-11-02 that placement is an orbit round your head
    and a drag carries a desktop round you, exactly under the dot: orbit.py (drag_target, PillDrag) replaced the flat
    offset, its 10% overlap allowance and its one-desktop reach, and it bumps what it runs into (bump.py).
  * Sizes and positions are saved with the other placements in the glasses model's profile (PlacementStore), 0.5 s
    after the last change and at once on release.
  * Every free-pointer click (corner and pill drags, pill clicks, panel sliders) uses the same modal left-button binds
    and ops (resize_press / resize_release), so whatever disables one (zoomed in, typing mode, a VR video playing)
    disables them all. An open placement panel does not: the pointer is how it is clicked.
  * Super + drag is a window's, never a monitor's (Hyprland's own bindm movewindow / resizewindow). The press bind has
    no modifier and Hyprland matches a bind's modifiers exactly, so with Super (or any modifier) held the press never
    reaches the run: no monitor move, resize or bump can start. It must stay that way: no ignore_mods on it, and no
    bind here on a Super chord. A monitor drag that began before Super went down stops when it does: the two Super keys
    are bound, non-consuming (they still do everything they did) and ignoring modifiers, to CANCEL_OP
    (OverviewPointer.super_down), in the same table, so only while the left button is the run's, and every removal
    of it takes them too (a dead run's: the next Super press cleans up like a click). pcall'd: a refused bind is left
    out, never breaking the left button. The release bind ignores modifiers as well: however a monitor drag's button
    comes up, the drag ends and never stays on the dot; with no drag it does nothing.
  * OLED care: no pixel of the corner caps, the level bars or the glass passes 191/255 (the cards' cap); they hide after
    OVERLAY_IDLE_CLOSE_S without pointer movement. None of them drifts or orbits.
"""
import json

from . import view
from .geometry import MAX_DESKTOPS

RESIZE_OPS = ('resize_press', 'resize_release')
CORNERS = ('tl', 'tr', 'bl', 'br')
RESIZE_BUTTON = 'mouse:272'   # left button
CANCEL_OP = 'resize_cancel'   # Super went down (OverviewPointer.super_down); xr_control.HELPER_OPS, ipc.decode_control
SUPER_KEYS = ('Super_L', 'Super_R')
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


def install_resize_binds_lua(python, client, control_socket, run_id, session):
    """The modal left-button press/release binds (replacing any surviving set, so never doubled); generated Lua only."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not view.SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not view.SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    base = f'{python} -I {client} {control_socket} {run_id} {session}'
    keys = ' '.join(f'do local ok, b = pcall(hl.bind, "{key}", hl.dsp.exec_cmd(c .. "{CANCEL_OP}"), '
                    f'{{ ignore_mods = true, non_consuming = true }}) if ok and b then t[#t + 1] = b end end' for key in SUPER_KEYS)
    return (remove_resize_binds_lua() + f' local c = "{base} " {BIND_TABLE} = {{ '
            f'hl.bind("{RESIZE_BUTTON}", hl.dsp.exec_cmd(c .. "resize_press")), '
            f'hl.bind("{RESIZE_BUTTON}", hl.dsp.exec_cmd(c .. "resize_release"), {{ release = true, ignore_mods = true }}) }} '
            f'local t = {BIND_TABLE} {keys}')


def remove_resize_binds_lua():
    """Also shows the cursor again and puts it back if free mode hid it (no-op otherwise)."""
    return view._remove_lua(BIND_TABLE) + ' ' + GRAB_LEAVE_LUA


def pill_frame(run_id, session, request_id, pill):
    """Renderer `move_handle` frame: the overview's glass pills. visible = shown under every desktop (an open placement
    panel is drawn either way); desktop + hover = the one under the pointer or held (brighter); active = that one is
    being dragged (brightest); desktop + gear = the pointer is on that desktop's options gear (the gear's hover glass)."""
    if (not isinstance(pill, dict) or set(pill) != {'visible', 'desktop', 'hover', 'active', 'gear'}
            or any(type(pill[k]) is not bool for k in ('visible', 'hover', 'active', 'gear'))
            or type(pill['desktop']) is not int or not 0 <= pill['desktop'] < MAX_DESKTOPS):
        raise ValueError('invalid move handle')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'move_handle',
                        'payload': dict(pill)}, separators=(',', ':')) + '\n').encode()


PILLS_HIDDEN = {'visible': False, 'desktop': 0, 'hover': False, 'active': False, 'gear': False}


def handle_frame(run_id, session, request_id, handle):
    """Renderer `resize_handle` frame: visible = corner `corner` of `desktop` is under the dot or held, so its cap lights up
    (the caps themselves show with the level bars: free_pointer.roll_frame); active = it is being dragged."""
    if (not isinstance(handle, dict) or set(handle) != {'visible', 'desktop', 'corner', 'active'}
            or type(handle['visible']) is not bool or type(handle['active']) is not bool
            or type(handle['desktop']) is not int or not 0 <= handle['desktop'] < MAX_DESKTOPS or handle['corner'] not in CORNERS):
        raise ValueError('invalid resize handle')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'resize_handle',
                        'payload': dict(handle)}, separators=(',', ':')) + '\n').encode()


HIDDEN = {'visible': False, 'desktop': 0, 'corner': 'br', 'active': False}
