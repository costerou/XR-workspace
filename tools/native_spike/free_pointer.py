"""Overview free pointer: in the overview the mouse moves freely through the glasses' space, not only over the desktops.

Design (feature/overview-free-pointer, 2026-11-02; glass pills, feature/glass-pills, 2026-11-02):
  * Entering the overview hands the mouse to a pointer that can go anywhere in the XR view: between desktops, below
    them, past their corners. The real cursor is confined to the XR-n monitor layout, so it cannot reach a desktop's
    glass pill (drawn under it) or go round a corner.
  * Two modes, switched by where the pointer is:
      - desktop: the pointer is inside a desktop. The real cursor is visible there (the capture paints it) and every
        click, hover, window drag and window-border resize goes to the window under it as usual (the run never sees
        those clicks, so a click inside a desktop leaves an open placement panel open). The pointer's place in space is
        the cursor's position mapped onto that desktop's surface.
      - free: the cursor reached the edge of its desktop, or crossed onto another monitor. The cursor is hidden and
        parked in the middle of its monitor (put back there whenever it strays RECENTRE_PX), and its motion moves a
        small dim dot drawn by the renderer on the overview's plane (`overview_pointer`). While free, the left button is
        bound to the run (`resize_press` / `resize_release`, the existing modal binds): see Clicks below. Moving
        ENTER_PX into a desktop hands the pointer back to the cursor without a click.
    The switch back to desktop mode needs the pointer ENTER_PX inside, while leaving needs the very edge pixel, so
    the modes never flap at an edge. Moving into a desktop (or clicking it) also selects it, so Ctrl+Alt+Up zooms into
    the desktop the pointer is on and the highlight shows it.
  * Glass pills (spike/include/xr/glass.hpp, mirrored here: pill_rect, panel_rect and the PILL_* / PANEL_* constants; the
    ctest `glass_contract` compares them): a frosted-glass pill sits PILL_GAP under every desktop, on that desktop's own
    surface. One at a time expands into the desktop's placement panel (PANEL_W x PANEL_H, centred under the desktop, its
    top where the pill's is): a header strip (the pill itself) and one slider row per placement setting. The state is
    view.ViewState's menu_open / menu_desktop; Ctrl+Alt+L and the panel's modal keys drive the same state (view.py).
  * Clicks in free mode, tested front to back (hit): the open panel first (it is drawn on top), then each desktop's
    corners (they win over its pill), its pill zone (the pill padded by PILL_PAD_X / PILL_PAD_Y, plus the gap up to the
    desktop's bottom edge) and the desktop itself.
      - pill, or the open panel's header: press and drag moves the desktop (its x / y placement, the pill staying under
        the pointer). The desktop does not move until the pointer is CLICK_PX away from the press, so a click never
        nudges it; a release before that is a click: it expands that pill (any other open panel collapses) or collapses
        it if it is the open one (view.ViewControl.pill_click, the same path as Ctrl+Alt+L: modal keys, save, idle).
        A key that changed which panel is open while the button was down (Escape, Tab, Ctrl+Alt+L) wins: that release
        is dropped, not a click, so it never undoes the key.
      - a slider of the open panel: that setting is highlighted and its value jumps to the pointer; holding and
        dragging moves it live; the release saves. The value is measured on the panel as it was at the press, so a
        setting that moves its own desktop never chases itself, and until the release the renderer draws the panel on
        that same press-time surface (the `placement_menu` frame's `surface`) while the desktop itself moves live, so
        the knob stays under the pointer. On release the panel joins its desktop again.
      - elsewhere in the open panel: nothing (it stays open).
      - corner: resize (the `distance` placement), as before. A release that resized nothing, pressed on the corner
        zone's inner half (on the desktop), is a click on the desktop (below).
      - desktop: select it, focus its monitor in Hyprland (view.ViewControl.focus_desktop), hand the cursor back there
        (_clicked; under the input lock the held cursor moves there instead, so focus-follows-mouse agrees).
    A press while a panel is open on anything but that desktop's own pill / header, panel, sliders or corners
    collapses the panel first (saved, modal keys removed); the press then carries on as listed (another desktop's pill
    still starts its drag or click, a desktop is still focused, empty space does nothing more).
  * Geometry: the renderer reports the overview's canvas size, FOV split and each desktop's overview slot on its
    status socket (`overview_geometry`, when it changes). Hit testing maps the pointer onto each desktop's surface with
    the same placement maths as spike/src/view.cpp place_point (orbit, distance, curvature; always facing you), front
    desktop first. The overview's slots never move with head pose, so neither does this. In anchor mode past the view,
    what is under the dot itself always wins; a pill found only along the stretched direction (orbit.stretched_pointer) is the fallback.
  * Drags follow the pointer: a pill drag turns the desktop round you (its x / y placement, an orbit: orbit.py) so the
    point grabbed stays exactly under the dot across the dot's whole reach, and it never overlaps another desktop. A corner drag changes `distance` so the corner
    follows the pointer along its diagonal; a slider sets its setting. All are saved like a panel key change (0.5 s
    debounce, and at once on release). Drags also end, and save, after OVERLAY_IDLE_CLOSE_S without pointer movement,
    on zoom-in and when a VR video opens.
  * Help card (Ctrl+Alt+H, view.py): it is drawn over everything in the middle of the view, head-locked, so while it is
    up the renderer reports where it lies on the canvas under the current head rotation (`help_geometry`, the card and,
    on a paged card, its previous / next arrows and page indicator; parse_help_geometry). It is the top layer for the
    pointer: in desktop mode a cursor that comes onto the card, or that the card opens or moves over, turns into the
    free dot there (the card hides the desktop under it, so a click must not reach the window), and in free mode the dot stays free over the card, entering a desktop only ENTER_PX clear of
    it. A click on the previous arrow turns to the previous page, on the next arrow or the indicator to the next
    (view.ViewControl.help_turn, the same frame as Ctrl+Alt+PgUp / PgDn); anywhere else on the card does nothing. A
    click on the card never collapses an open placement panel.
  * Off in typing mode (Ctrl+Alt+T: the pills hide too), while zoomed (pills and panels are overview-only) and while a
    VR video plays (resize ops are in video.IGNORED_WHILE_PLAYING). An open panel no longer turns it off: the pointer
    is how a panel is clicked. Leaving the overview removes the binds and shows the cursor again.
  * OLED care: the dot is grey at 170/255 (184 while held; cards and glass cap at 191), drawn only in DOT_MODES, never
    moves on its own, and hides with the pills and bracket after OVERLAY_IDLE_CLOSE_S without pointer movement (an
    open panel stays until the view's own idle close, which pointer motion over it postpones). A click while hidden
    only wakes them.
  * Circle only (overview_cursor.py, display only): the dot is the overview's only pointer. In desktop mode too it is
    drawn, where the cursor maps onto the desktop, and the real cursor is hidden there as in free mode; it still does
    everything desktop mode says above. The sampler hides and shows it; it shows again away, outside the overview and
    at run end.
  * Threads: the sampler reads `cursorpos` every SAMPLE_S (250 Hz) in the overview and does every compositor call
    (binds, hide/show, warps); the supervisor loop owns all state and frames. A run that dies in free mode leaves
    the left-button bind; the next click runs xr_control.py, which removes it and shows the cursor again.
"""
import json
import math
import socket
import threading
import time

from . import orbit, resize, view
from .geometry import PLACEMENT_BOUNDS
from .overview_cursor import DOT_MODES, OverviewCursor

SAMPLE_S = 1 / 250        # cursor reads per second in the overview (faster than the glasses' 120 Hz)
IDLE_S = 0.1              # not in the overview: just notice when it is again
OUTPUTS_REFRESH = 2.0     # seconds between XR-n monitor geometry reads
EDGE_PX = 0.5             # desktop mode: the cursor this close to its monitor's edge (logical px) has hit it
ENTER_PX = 4.0            # free mode: this far inside a desktop (canvas px) hands the pointer back to the cursor
INSET_PX = 2              # a cursor handed back lands at least this far inside its monitor (logical px)
RECENTRE_PX = 200         # free mode: the hidden cursor goes back to its parking point once it strays this far
JUMP_PX = 400             # a cursor step this long is a warp or focus change (keyboard), not a hand movement
SAMPLE_JUMP_PX = 1500     # a single sample's motion beyond this is dropped (warps the sampler did not make)
# Grab zones, canvas px: corners are squares centred on the corner (half inside, half outside the desktop).
CORNER_SHARE, CORNER_MIN, CORNER_MAX = 0.06, 10.0, 28.0
# Glass pill (glass.hpp glass_pill: share of the desktop's width clamped to px, height, gap under the desktop) and the
# padding around it that still grabs it; the zone also covers the gap up to the desktop's bottom edge.
PILL_SHARE, PILL_MIN, PILL_MAX, PILL_THICKNESS, PILL_GAP = 0.24, 64.0, 160.0, 14.0, 10.0
PILL_PAD_X, PILL_PAD_Y = 10.0, 10.0
# Expanded pill = placement panel (glass.hpp glass_panel and its layout, panel px from its top-left): the header strip
# (the pill itself: drag to move, click to collapse), then one row per placement setting (geometry.PLACEMENT_KEYS order)
# whose slider knob centre runs PANEL_KNOB_LO (low bound) .. PANEL_KNOB_HI (high bound).
PANEL_W, PANEL_H, PANEL_HEADER_H = 480.0, 220.0, 42.0
PANEL_ROWS_TOP, PANEL_ROW_H, PANEL_KNOB_LO, PANEL_KNOB_HI = 46.0, 34.0, 191.0, 367.0
PANEL_SLOP_PX = 12.0      # a press this far past either end of a slider's knob range still grabs it (the knob and the dot)
CLICK_PX = 4.0            # a pill press moving less than this (canvas px) before release is a click: the desktop stays put
MIN_SCALE = 0.05          # a corner drag cannot shrink the apparent size past this (the distance bound clamps first)
POINTER_HIDDEN = {'visible': False, 'x': 0, 'y': 0, 'held': False}
MAX_POINTER_PX = view.MAX_POINTER_PX  # spike/include/xr/ipc.hpp kMaxPointerPx; the overview pointer may be negative


def parse_overview_geometry(value):
    """The renderer's `overview_geometry` status line (spike/src/view.cpp overview_geometry_json) -> validated dict
    {'w', 'h', 'half': (tan half-FOV x, y), 'tiles': [(x, y, w, h) canvas px] in managed-output order}. ValueError if bad."""
    def number(v, lo, hi):
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi: raise ValueError('invalid overview geometry')
        return float(v)
    if not isinstance(value, dict) or set(value) != {'w', 'h', 'half', 'tiles'}: raise ValueError('invalid overview geometry')
    w, h = value['w'], value['h']
    if type(w) is not int or type(h) is not int or not 0 < w <= MAX_POINTER_PX or not 0 < h <= MAX_POINTER_PX:
        raise ValueError('invalid overview geometry')
    half, tiles = value['half'], value['tiles']
    if not isinstance(half, list) or len(half) != 2 or not isinstance(tiles, list) or not 1 <= len(tiles) <= 3:
        raise ValueError('invalid overview geometry')
    half = tuple(number(v, 1e-3, 100.0) for v in half)
    out = []
    for t in tiles:
        if not isinstance(t, list) or len(t) != 4: raise ValueError('invalid overview geometry')
        x, y, tw, th = (number(v, -MAX_POINTER_PX, MAX_POINTER_PX) for v in t)
        if tw <= 0 or th <= 0: raise ValueError('invalid overview geometry')
        out.append((x, y, tw, th))
    return {'w': w, 'h': h, 'half': half, 'tiles': out}


HELP_RECTS = ('card', 'prev', 'indicator', 'next')


def parse_help_geometry(value):
    """The renderer's `help_geometry` status line (spike/include/xr/hud.hpp help_geometry_json) -> {'card': (x, y, w, h),
    and 'prev' / 'indicator' / 'next' on a paged card} in canvas px, or None (no card up in the overview). ValueError if
    bad."""
    if value is None: return None
    if not isinstance(value, dict) or 'card' not in value or not set(value) <= set(HELP_RECTS):
        raise ValueError('invalid help geometry')
    controls = set(value) - {'card'}
    if controls and controls != {'prev', 'indicator', 'next'}: raise ValueError('invalid help geometry')
    out = {}
    for name, r in value.items():
        if not isinstance(r, list) or len(r) != 4 or any(type(v) is not int or not -MAX_POINTER_PX <= v <= MAX_POINTER_PX for v in r) \
                or r[2] <= 0 or r[3] <= 0:
            raise ValueError('invalid help geometry')
        out[name] = tuple(r)
    return out


def help_hit(help_, pointer, margin=0.0):
    """('help', 'prev' | 'indicator' | 'next' | 'card') if canvas point `pointer` is on the help card (`help_`, from
    parse_help_geometry; `margin` canvas px around the card counts too), else None."""
    if not help_ or pointer is None: return None
    def inside(r, m=0.0): return r[0] - m <= pointer[0] < r[0] + r[2] + m and r[1] - m <= pointer[1] < r[1] + r[3] + m
    for name in ('prev', 'next', 'indicator'):
        if name in help_ and inside(help_[name]): return ('help', name)
    return ('help', 'card') if inside(help_['card'], margin) else None


def place_point(g, tile, p, px, py):
    """World position of canvas pixel (px, py) on a desktop laid out at `tile` with wire placement `p`
    (geometry.PLACEMENT_BOUNDS units). Mirrors spike/src/view.cpp place_point exactly (orbit.place_point)."""
    return orbit.place_point(g, tile, p, px, py)


def to_canvas(g, point):
    """A world point seen from the eye, on the canvas plane (z = -1) in canvas px; None if it is not in front or lands
    past MAX_POINTER_PX (a desktop turned far round you: nowhere the pointer, or a frame, can go)."""
    x, y, z = point
    if z > -1e-6: return None
    hw, hh = g['half']
    out = ((x / -z / hw + 1) * g['w'] / 2, (1 - y / -z / hh) * g['h'] / 2)
    return out if abs(out[0]) <= MAX_POINTER_PX and abs(out[1]) <= MAX_POINTER_PX else None


def project(g, tile, p, px, py):
    """Where tile pixel (px, py) of a placed desktop appears on the overview plane (canvas px), or None."""
    return to_canvas(g, place_point(g, tile, p, px, py))


def unproject(g, tile, p, pointer):
    """The tile pixel (px, py) that appears under canvas point `pointer` on a placed desktop's surface, or None when the
    pointer's ray misses that surface. An orbit (x / y) is a rotation about the eye, so the pointer's ray is turned back
    by it first (orbit.unorbit_pointer). Newton's method on `project`, started from the exact flat answer."""
    if p['x'] or p['y']:
        pointer = orbit.unorbit_pointer(g, tile, p, pointer)
        if pointer is None: return None
        p = {**p, 'x': 0, 'y': 0}
    tx, ty, tw, th = tile
    cx, cy = tx + tw / 2, ty + th / 2
    d = p['distance'] / 100
    q = [cx + (pointer[0] - cx) * d, cy + (pointer[1] - cy) * d]
    limit = 4 * max(tw, th)
    for _ in range(16):
        f = project(g, tile, p, q[0], q[1])
        if f is None: return None
        ex, ey = f[0] - pointer[0], f[1] - pointer[1]
        if abs(ex) < 0.01 and abs(ey) < 0.01: return (q[0], q[1])
        step = 0.5
        fx, fy = project(g, tile, p, q[0] + step, q[1]), project(g, tile, p, q[0], q[1] + step)
        if fx is None or fy is None: return None
        a, b = (fx[0] - f[0]) / step, (fy[0] - f[0]) / step
        c, e = (fx[1] - f[1]) / step, (fy[1] - f[1]) / step
        det = a * e - b * c
        if abs(det) < 1e-9: return None
        dx, dy = (e * ex - b * ey) / det, (a * ey - c * ex) / det
        q = [q[0] - dx, q[1] - dy]
        if abs(q[0] - cx) > limit or abs(q[1] - cy) > limit: return None
    f = project(g, tile, p, q[0], q[1])
    return (q[0], q[1]) if f is not None and math.hypot(f[0] - pointer[0], f[1] - pointer[1]) < 0.5 else None


def corner_zone(tile):
    """Half-size of a corner's grab square, canvas px."""
    return min(max(CORNER_SHARE * min(tile[2], tile[3]), CORNER_MIN), CORNER_MAX)


def pill_rect(tile):
    """The drawn glass pill (glass.hpp glass_pill): (x, y, w, h) canvas px on the desktop's surface, centred PILL_GAP
    under the desktop."""
    tx, ty, tw, th = tile
    w = min(min(max(PILL_SHARE * tw, PILL_MIN), PILL_MAX), tw)
    return (tx + (tw - w) / 2, ty + th + PILL_GAP, w, PILL_THICKNESS)


def panel_rect(tile):
    """The expanded pill, the placement panel (glass.hpp glass_panel): (x, y, w, h) canvas px on the desktop's surface,
    centred under the desktop with its top where the pill's top is."""
    tx, ty, tw, th = tile
    return (tx + (tw - PANEL_W) / 2, ty + th + PILL_GAP, PANEL_W, PANEL_H)


def slider_fraction(lx):
    """Panel x (px from the panel's left) -> fraction 0..1 along a slider's knob range (glass.cpp draws the knob there)."""
    return min(max((lx - PANEL_KNOB_LO) / (PANEL_KNOB_HI - PANEL_KNOB_LO), 0.0), 1.0)


def panel_hit(lx, ly, index):
    """What panel point (lx, ly) (panel px from its top-left, inside the panel) is on: ('pill', index) for the header
    strip, ('slider', index, field, lx) on a slider row's knob range (PANEL_SLOP_PX either side), else ('panel', index)."""
    from .geometry import PLACEMENT_KEYS
    if ly < PANEL_HEADER_H: return ('pill', index)
    row = math.floor((ly - PANEL_ROWS_TOP) / PANEL_ROW_H) if ly >= PANEL_ROWS_TOP else -1
    if 0 <= row < len(PLACEMENT_KEYS) and PANEL_KNOB_LO - PANEL_SLOP_PX <= lx <= PANEL_KNOB_HI + PANEL_SLOP_PX:
        return ('slider', index, PLACEMENT_KEYS[row], lx)
    return ('panel', index)


def front_order(g, placements):
    """Desktop indices nearest first (the reverse of the renderer's back-to-front draw order)."""
    def depth(i):
        t = g['tiles'][i]
        x, y, z = place_point(g, t, placements[i], t[0] + t[2] / 2, t[1] + t[3] / 2)
        return x * x + y * y + z * z
    n = min(len(g['tiles']), len(placements))
    return sorted(range(n), key=depth)


def hit(g, placements, pointer, open_=None, around=False):
    """What is under canvas point `pointer`. `open_`: the desktop whose pill is expanded into its panel, or None. That
    panel is drawn on top, so it is tested first (panel_hit: ('pill', i) for its header, ('slider', i, field, panel x),
    ('panel', i)); then each desktop front to back: ('corner', index, 'tl'|'tr'|'bl'|'br'), ('pill', index),
    ('desktop', index, (px, py) tile pixel). None if nothing. `around` (anchor mode): past the view, pills are found
    along a stretched direction (orbit.stretched_pointer), so a pill placed anywhere round you can be grabbed."""
    if g is None or pointer is None: return None
    n = min(len(g['tiles']), len(placements))
    if type(open_) is int and 0 <= open_ < n:
        tile = g['tiles'][open_]
        q = unproject(g, tile, placements[open_], pointer)
        if q is not None:
            x, y, w, h = panel_rect(tile)
            if x <= q[0] < x + w and y <= q[1] < y + h: return panel_hit(q[0] - x, q[1] - y, open_)
    stretched = around and orbit.beyond_view(g, pointer)
    reached = None  # past the view: the first pill found only along the stretched direction (what is under the dot wins)
    for i in front_order(g, placements):
        tile = g['tiles'][i]
        q = unproject(g, tile, placements[i], pointer)
        qp = None
        if stretched and reached is None:
            seen = orbit.stretched_pointer(g, tile, placements[i], pointer)
            qp = unproject(g, tile, {**placements[i], 'x': 0, 'y': 0}, seen) if seen is not None else None
        tx, ty, tw, th = tile
        if q is not None:
            z = corner_zone(tile)
            for corner, (cx, cy) in (('tl', (tx, ty)), ('tr', (tx + tw, ty)), ('bl', (tx, ty + th)), ('br', (tx + tw, ty + th))):
                if abs(q[0] - cx) <= z and abs(q[1] - cy) <= z: return ('corner', i, corner)
            if on_pill(tile, q): return ('pill', i)
            if tx <= q[0] < tx + tw and ty <= q[1] < ty + th: return ('desktop', i, q)
        if on_pill(tile, qp): reached = ('pill', i)
    return reached


def on_pill(tile, q):
    """True if tile pixel `q` (None: missed) is in a desktop's pill zone: the pill padded by PILL_PAD_X / PILL_PAD_Y,
    plus the gap up to the desktop's bottom edge."""
    if q is None: return False
    px, py, pw, ph = pill_rect(tile)
    return px - PILL_PAD_X <= q[0] <= px + pw + PILL_PAD_X and tile[1] + tile[3] <= q[1] <= py + ph + PILL_PAD_Y


def inside_by(tile, q):
    """How far tile pixel q is inside the tile (canvas px; negative outside)."""
    tx, ty, tw, th = tile
    return min(q[0] - tx, tx + tw - q[0], q[1] - ty, ty + th - q[1])


def resize_distance(start, corner, dx, dy, tile):
    """New `distance` for a corner drag of (dx, dy) canvas px: the corner follows the pointer along its diagonal
    (apparent size is 100 / distance, about the desktop's centre). Clamped to the distance bounds."""
    if corner not in resize.CORNERS: raise ValueError('invalid corner')
    vx = (-1 if corner[1] == 'l' else 1) * tile[2] / 2
    vy = (-1 if corner[0] == 't' else 1) * tile[3] / 2
    scale = max(MIN_SCALE, 100 / start + (dx * vx + dy * vy) / (vx * vx + vy * vy))
    lo, hi = PLACEMENT_BOUNDS['distance']
    return min(max(round(100 / scale), lo), hi)


def overview_pointer_frame(run_id, session, request_id, pointer):
    """Renderer `overview_pointer` frame: {visible, x, y, held}, x/y canvas px (spike/src/ipc.cpp checks the same)."""
    if (not isinstance(pointer, dict) or set(pointer) != {'visible', 'x', 'y', 'held'} or type(pointer['visible']) is not bool
            or type(pointer['held']) is not bool
            or any(type(pointer[k]) is not int or not -MAX_POINTER_PX <= pointer[k] <= MAX_POINTER_PX for k in 'xy')):
        raise ValueError('invalid overview pointer')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'overview_pointer',
                        'payload': dict(pointer)}, separators=(',', ':')) + '\n').encode()


# Compositor Lua (generated, fixed shape; every cursor call in pcall so a refused change never breaks the binds).
# Free mode: remember the cursor's visibility and position once (the same table the bind removal restores, so a run
# that dies in free mode gives the cursor back on the next click), hide it and park it at (x, y).
_HIDE_LUA = (f'if {resize.GRAB_TABLE} == nil then '
             'local ok, hidden = pcall(hl.get_config, "cursor.invisible") local p = hl.get_cursor_pos() '
             f'{resize.GRAB_TABLE} = {{ invisible = ok and hidden == true, home = p and {{ x = p.x, y = p.y }} }} end '
             'pcall(hl.config, { cursor = { invisible = true } })')
# Back to desktop mode: the left button goes back to windows and the cursor's own visibility returns (not its old
# position: it is then warped to where the pointer is).
_SHOW_LUA = (f'do local s = {resize.GRAB_TABLE} {resize.GRAB_TABLE} = nil if s then '
             'pcall(hl.config, { cursor = { invisible = s.invisible } }) end end')


def grab_pointer_lua(python, client, control_socket, run_id, session, x, y):
    """Enter free mode: the left-button binds, the cursor hidden and parked at layout pixel (x, y). One eval."""
    return resize.install_resize_binds_lua(python, client, control_socket, run_id, session) + ' ' + _HIDE_LUA + ' ' + \
        view.warp_cursor_lua(x, y)


def release_pointer_lua(x=None, y=None):
    """Leave free mode for desktop mode: binds removed, cursor shown and (if given) put at layout pixel (x, y). One eval."""
    out = view._remove_lua(resize.BIND_TABLE) + ' ' + _SHOW_LUA
    return out + (' ' + view.warp_cursor_lua(x, y) if x is not None else '')


class OverviewPointer:
    """Pointer state, hover, drags and renderer frames, on the supervisor loop. The sampler thread hands in cursor samples
    (feed) and applies `want` (what the cursor and the left button should be doing) with compositor calls."""
    def __init__(self, control, clock=time.monotonic):
        self.control, self.clock = control, clock
        self._lock = threading.Lock()
        self._abs, self._motion, self._applied, self._outputs, self._fresh = None, [0.0, 0.0], 0, [], False
        self.geometry = None       # parse_overview_geometry, from the renderer
        self.help_geometry = None  # parse_help_geometry, from the renderer: the help card while it is up in the overview
        self.outputs = []          # resize.outputs_from_monitors, from the sampler
        self.mode = 'off'          # off (not in the overview), away (cursor not on a desktop), desktop, free
        self.desktop = None        # desktop index the cursor is on (desktop mode) or left from (free mode)
        self.pointer = None        # (x, y) canvas px
        self.gain = 1.0            # canvas px per cursor px in free mode (the desktop's own scale when it was left)
        self.hover = None          # hit(): what a click would grab (free mode only)
        # Corner: {'kind': 'corner', 'index', 'corner', 'start', 'distance', 'tile'}
        # Pill:   {'kind': 'pill', 'index', 'start', 'xy', 'tile', 'moved', 'open_at_press'}  (moved: past CLICK_PX, so
        #         not a click; open_at_press: the open panel's desktop or None after the press, a click needs it unchanged)
        # Slider: {'kind': 'slider', 'index', 'field', 'tile', 'placement'}  (placement: the desktop's at the press, the
        #         surface both the drag and the renderer's panel use until the release)
        self.drag = None
        self.last_move = clock()
        self.shown, self.pills, self.sent = dict(resize.HIDDEN), dict(resize.PILLS_HIDDEN), dict(POINTER_HIDDEN)
        self.want = (0, 'off', None)  # (serial, 'off'|'away'|'desktop'|'free', layout point or None); read by the sampler
        self._sync = 0                # cursor samples count in desktop mode only once the sampler applied this serial
        self.summoned = False         # Ctrl+Alt+Down: bring a cursor that is off the desktops into the selected one
        self.binds_dropped = False    # config reload: runtime binds and cursor state are gone (sampler redoes them)
        self.entries = self.exits = self.drags = self.moves = self.focus_clicks = 0  # diagnostics
        self.pill_clicks = self.slides = self.outside_closes = self.stale_clicks = self.help_clicks = 0  # diagnostics
        reader, writer = socket.socketpair()
        reader.setblocking(False); writer.setblocking(False)
        self.wake_reader, self._wake_writer = reader, writer

    def close(self):
        self.wake_reader.close(); self._wake_writer.close()

    def overview(self):
        """In the overview with nothing else owning the mouse: not zoomed, no VR video, and not in typing mode (view.py,
        Ctrl+Alt+T: every run bind but the toggle is released, the left button and cursor too, and the pills hide). An
        open placement panel keeps it on: the pointer clicks its pill and drags its sliders."""
        s = self.control.state
        return not s.zoomed and not getattr(self.control, 'video_active', False) and not getattr(s, 'typing', False)

    def active(self):
        return self.overview() and self.geometry is not None and len(self.outputs) > 0

    # --- inputs ---------------------------------------------------------------------------------------------------
    def set_geometry(self, geometry):
        self.geometry = geometry
        self._poke()

    def set_help_geometry(self, geometry):
        self.help_geometry = geometry
        self._poke()

    def summon(self):
        self.summoned = True

    def feed(self, cursor, motion, outputs, applied):
        """Sampler thread: latest cursor (global logical px or None), motion since the last sample, monitor rectangles and
        the last `want` serial it applied. Wakes the loop when anything changed."""
        with self._lock:
            changed = cursor != self._abs or motion[0] or motion[1] or outputs != self._outputs or applied != self._applied
            self._abs, self._outputs, self._applied = cursor, outputs, applied
            self._motion[0] += motion[0]; self._motion[1] += motion[1]
            if changed: self._fresh = True
        if changed: self._poke()
        return changed

    def _poke(self):
        try: self._wake_writer.send(b'.')
        except OSError: pass  # full or closed: the loop's own 50 ms tick picks it up

    def drain(self):
        try:
            while self.wake_reader.recv(4096): pass
        except OSError: pass

    def _take(self):
        with self._lock:
            fresh, cursor, outputs, applied = self._fresh, self._abs, self._outputs, self._applied
            motion, self._motion, self._fresh = tuple(self._motion), [0.0, 0.0], False
        self.outputs = outputs
        return fresh, cursor, motion, applied

    # --- helpers --------------------------------------------------------------------------------------------------
    def _set_want(self, kind, point=None, sync=False):
        serial = self.want[0] + 1
        self.want = (serial, kind, point)
        if sync: self._sync = serial

    def _open(self):
        """The desktop whose pill is expanded into its panel, or None."""
        s = self.control.state
        return s.menu_desktop if s.menu_open else None

    def _help(self):
        """The help card's canvas geometry while the card is open (the renderer may still report a closing one)."""
        return self.help_geometry if getattr(self.control, 'help_visible', False) else None

    def _hit(self):
        """hit(), with the help card on top of everything while it is open."""
        return help_hit(self._help(), self.pointer) or hit(self.geometry, self.control.state.placements, self.pointer, self._open(),
                                                          around=self.control.state.mode == 'anchor')

    def _output(self, index):
        return next((o for o in self.outputs if o['index'] == index), None)

    def _placement(self, i):
        p = self.control.state.placements
        return p[i] if i < len(p) else None

    def _tile(self, i):
        g = self.geometry
        return g['tiles'][i] if g is not None and i < len(g['tiles']) else None

    def _from_logical(self, i, lx, ly):
        o, tile, p = self._output(i), self._tile(i), self._placement(i)
        if o is None or tile is None or p is None: return None
        return project(self.geometry, tile, p, tile[0] + lx / o['width'] * tile[2], tile[1] + ly / o['height'] * tile[3])

    def _to_layout(self, i, q):
        """Tile pixel q of desktop i -> integer layout pixel inside its monitor (at least INSET_PX from every edge)."""
        o, tile = self._output(i), self._tile(i)
        if o is None or tile is None: return None
        lx = min(max((q[0] - tile[0]) / tile[2] * o['width'], INSET_PX), o['width'] - 1 - INSET_PX)
        ly = min(max((q[1] - tile[1]) / tile[3] * o['height'], INSET_PX), o['height'] - 1 - INSET_PX)
        return (int(o['x'] + lx), int(o['y'] + ly))

    def _gain(self, i):
        o, tile, p = self._output(i), self._tile(i), self._placement(i)
        if o is None or tile is None or p is None: return self.gain
        return tile[2] / o['width'] * 100 / p['distance']

    def _clamp(self, point):
        g = self.geometry
        return (min(max(point[0], -g['w'] / 2), g['w'] * 1.5), min(max(point[1], -g['h'] / 2), g['h'] * 1.5))

    def _at(self, cursor):
        """(index, lx, ly) of the XR monitor holding layout point `cursor`, or None."""
        for o in self.outputs:
            lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
            if 0 <= lx < o['width'] and 0 <= ly < o['height']: return o['index'], lx, ly
        return None

    def _select(self, i):
        """The desktop the pointer went into (or clicked) becomes the selected one: Ctrl+Alt+Up zooms into it. Returns a
        view frame if that changed."""
        s = self.control.state
        if i >= len(s.names) or s.selected == i: return b''
        s.selected = i
        return self.control._frame()

    def _enter(self, i, q):
        """Free -> desktop mode on desktop i at tile pixel q: the cursor comes back there, visible."""
        target = self._to_layout(i, q)
        if target is None: return b''
        self.mode, self.desktop, self.hover = 'desktop', i, None
        self._set_want('desktop', target, sync=True)
        self.entries += 1
        return self._select(i)

    def _clicked(self, i, q):
        """A click on desktop i at tile pixel q (after focus_desktop): the cursor comes back there, so Hyprland's
        focus-follows-mouse agrees with the focus the click asked for."""
        return self._enter(i, q)

    # --- the loop -------------------------------------------------------------------------------------------------
    def poll(self, now):
        """Supervisor tick: apply the newest cursor sample; returns renderer frames (bytes)."""
        fresh, cursor, motion, applied = self._take()
        out = b''
        if not self.active():
            if self.mode != 'off':
                out += self._end_drag()
                self.mode, self.hover, self.pointer, self.summoned = 'off', None, None, False
                self._set_want('off')
        else:
            if self.mode == 'off':
                self.mode, self.desktop, self.pointer = 'away', None, None
                self._set_want('away')
            if self.mode == 'free':
                if motion[0] or motion[1]: out += self._move(motion[0] * self.gain, motion[1] * self.gain, now)
            elif fresh and cursor is not None and applied >= self._sync:
                out += self._absolute(cursor, now)
            else:
                self._onto_help()  # no cursor motion: the card may have come over the cursor itself
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        if idle:
            out += self._end_drag()
            self.hover = None
        return out + self._handle() + self._pills(idle) + self._pointer_frame(idle)

    def _absolute(self, cursor, now):
        """Away / desktop mode: the cursor's own position decides."""
        out = b''
        at = self._at(cursor)
        if self.mode == 'away':
            if at is None:
                if self.summoned:  # Ctrl+Alt+Down with the cursor elsewhere: into the middle of the selected desktop
                    self.summoned = False
                    i = self.control.state.selected
                    tile = self._tile(i)
                    if tile is not None and self._output(i) is not None:
                        self.mode, self.desktop = 'desktop', i
                        self._set_want('desktop', self._to_layout(i, (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)), sync=True)
                return out
            self.mode, self.desktop = 'desktop', at[0]
            out += self._select(at[0])
        self.summoned = False
        o = self._output(self.desktop)
        if o is None:
            self.mode, self.pointer = 'away', None
            return out
        lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
        w, h = o['width'], o['height']
        if EDGE_PX <= lx <= w - 1 - EDGE_PX and EDGE_PX <= ly <= h - 1 - EDGE_PX:
            point = self._from_logical(self.desktop, lx, ly)
            if point is not None and point != self.pointer: self.pointer, self.last_move = point, now
            self._onto_help()
            return out
        cx, cy = min(max(lx, 0), w - 1), min(max(ly, 0), h - 1)
        over = (lx - cx, ly - cy)
        if math.hypot(*over) > JUMP_PX:  # a keyboard focus change or warp, not the hand: follow the cursor
            if at is not None:
                self.desktop = at[0]
                out += self._select(at[0])
                self.pointer = self._from_logical(at[0], at[1], at[2])
            else:
                self.mode, self.pointer = 'away', None
            return out
        start = self._from_logical(self.desktop, cx, cy)
        if start is None: return out
        self.gain = self._gain(self.desktop)
        self.pointer = self._clamp((start[0] + over[0] * self.gain, start[1] + over[1] * self.gain))
        self.mode, self.last_move = 'free', now
        self.exits += 1
        self._set_want('free', (int(o['x'] + w / 2), int(o['y'] + h / 2)))
        self.hover = self._hit()
        return out

    def _onto_help(self):
        """Desktop mode with the pointer on the open help card (the cursor moved onto it, or the card opened or turned
        with the head over a still cursor): the card is on top, so the pointer turns into the free dot right there and
        the left button goes to the run, not to the window hidden under the card."""
        o = self._output(self.desktop)
        if self.mode != 'desktop' or o is None or help_hit(self._help(), self.pointer) is None: return
        self.gain = self._gain(self.desktop)
        self.mode = 'free'
        self.exits += 1
        self._set_want('free', (int(o['x'] + o['width'] / 2), int(o['y'] + o['height'] / 2)))
        self.hover = self._hit()

    def _move(self, dx, dy, now):
        """Free mode: the pointer moves by the cursor's motion; a drag follows it, otherwise it may go into a desktop."""
        self.pointer = self._clamp((self.pointer[0] + dx, self.pointer[1] + dy))
        self.last_move = now
        if self.control.state.menu_open: self.control.last_input = now  # moving over an open panel keeps it up
        if self.drag is not None: return self._apply_drag()
        self.hover = self._hit()
        if self.hover is not None and self.hover[0] == 'desktop' and inside_by(self._tile(self.hover[1]), self.hover[2]) >= ENTER_PX \
                and help_hit(self._help(), self.pointer, ENTER_PX) is None:  # ENTER_PX clear of the help card too
            return self._enter(self.hover[1], self.hover[2])
        return b''

    def on_op(self, op):
        """resize_press / resize_release: the left button, bound only in free mode (module docstring: Clicks)."""
        if op not in resize.RESIZE_OPS: raise ValueError('unsupported resize operation')
        now = self.clock()
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        self.last_move = now  # a click is input: the pointer, pills and bracket come back
        out = b''
        if op == 'resize_release':
            d = self.drag
            out += self._apply_drag()
            if d is not None and d['kind'] == 'pill' and not d['moved']:  # a click, not a drag: expand / collapse
                if self._open() == d['open_at_press']:
                    self.drag = None
                    self.pill_clicks += 1
                    out += self.control.pill_click(d['index'])
                else:  # a key changed the open panel while the button was down: that key wins, the release is dropped
                    self.stale_clicks += 1
            elif d is not None and d['kind'] == 'corner' and d.get('on') is not None and self.pointer is not None \
                    and math.hypot(self.pointer[0] - d['start'][0], self.pointer[1] - d['start'][1]) <= CLICK_PX \
                    and self.control.state.placements[d['index']]['distance'] == d['distance']:
                self.drag = None  # a click on the desktop's own corner, not a resize: focus it like any desktop click
                self.focus_clicks += 1
                out += self.control.focus_desktop(d['index']) + self._clicked(d['index'], d['on'])
            out += self._end_drag()
        elif self.active() and self.mode == 'free' and not idle and self.pointer is not None:
            out += self._end_drag()  # a press during a drag: its release was lost; finish that one first
            open_ = self._open()
            self.hover = h = self._hit()
            if h is not None and h[0] == 'help':  # the help card is on top: its arrows and indicator turn pages
                self.help_clicks += 1
                if h[1] != 'card': out += self.control.help_turn(-1 if h[1] == 'prev' else 1)
                return (out + self._handle() + self._pills(False) + self._pointer_frame(False)) or None
            if open_ is not None and not (h is not None and h[1] == open_ and h[0] in ('pill', 'panel', 'slider', 'corner')):
                self.outside_closes += 1
                out += self.control.pill_close()  # a click outside collapses the open panel; the press carries on (what
                # it hit was not on that panel, so the panel's going changes nothing about it)
            if h is not None and h[0] in ('corner', 'pill'):
                i, tile, p = h[1], self._tile(h[1]), self._placement(h[1])
                if h[0] == 'corner':
                    q = unproject(self.geometry, tile, p, self.pointer)  # the corner zone's inner half is on the desktop
                    on = q if q is not None and inside_by(tile, q) >= 0 else None
                    self.drag = {'kind': 'corner', 'index': i, 'corner': h[2], 'start': self.pointer, 'distance': p['distance'], 'tile': tile,
                                 'on': on}  # a release there that resized nothing is a click on the desktop: it focuses
                    self.drags += 1
                else:  # the desktop moves only once the pointer is CLICK_PX away; a release before that is a click
                    self.drag = {'kind': 'pill', 'index': i, 'start': self.pointer, 'xy': (p['x'], p['y']), 'tile': tile, 'moved': False,
                                 'open_at_press': self._open(),  # after any click-outside collapse above
                                 # grabbed along the stretched direction (hit's fallback past the view), not under the dot
                                 'reached': h != hit(self.geometry, self.control.state.placements, self.pointer, open_)}
            elif h is not None and h[0] == 'slider':
                i = h[1]
                self.drag = {'kind': 'slider', 'index': i, 'field': h[2], 'tile': self._tile(i), 'placement': dict(self._placement(i))}
                self.slides += 1
                # The value jumps to the pointer; the panel stays drawn on the press-time placement until the release.
                out += self.control.pill_slide(i, h[2], slider_fraction(h[3]), surface=self.drag['placement'])
            elif h is not None and h[0] == 'desktop':  # focus it and hand the cursor back right there
                self.focus_clicks += 1
                out += self.control.focus_desktop(h[1])  # selected, and its monitor gets Hyprland's focus (off the loop)
                out += self._clicked(h[1], h[2])
            # ('panel', i): elsewhere on the open panel, nothing; None: empty space, nothing (beyond the collapse above)
        return (out + self._handle() + self._pills(False) + self._pointer_frame(False)) or None

    def _apply_drag(self):
        d = self.drag
        if d is None or self.pointer is None: return b''
        c = self.control
        if d['kind'] == 'slider':
            # Measured on the panel as it was at the press, where the renderer keeps drawing it until the release (the
            # frame's `surface`): a setting that moves its own desktop would otherwise move the panel and chase itself.
            # A key that collapsed or moved the panel meanwhile dropped that surface: the rest of this drag does nothing.
            if c.state.menu_surface is None: return b''
            q = unproject(self.geometry, d['tile'], d['placement'], self.pointer)
            if q is None: return b''
            return c.pill_slide(d['index'], d['field'], slider_fraction(q[0] - panel_rect(d['tile'])[0]))
        current = c.state.placements[d['index']]
        dx, dy = self.pointer[0] - d['start'][0], self.pointer[1] - d['start'][1]
        if d['kind'] == 'corner':
            updated = {**current, 'distance': resize_distance(d['distance'], d['corner'], dx, dy, d['tile'])}
        else:
            if not d['moved']:
                if math.hypot(dx, dy) <= CLICK_PX: return b''  # still a click
                d['moved'] = True
                self.moves += 1
            if 'orbit' not in d:  # the turn round you keeping the grabbed point under the dot, never overlapping (orbit.py)
                d['orbit'] = orbit.PillDrag(self.geometry, d['index'], d['xy'], d['start'], reached=d.get('reached', False))
            x, y = d['orbit'].follow(self.pointer, c.state.placements)
            updated = {**current, 'x': x, 'y': y}
        if updated == current: return b''
        c.state.placements = [updated if i == d['index'] else p for i, p in enumerate(c.state.placements)]
        c.save_due = c.clock() + view.MENU_SAVE_DELAY
        return c._placement_frame() + (c._menu_frame() if c.state.menu_open else b'')

    def _end_drag(self):
        """Release, idle, zoom-in, typing mode or a video: the drag ends and is saved at once. A held slider's panel goes
        back onto its desktop's live placement (a `placement_menu` frame without `surface`)."""
        d = self.drag
        if d is None: return b''
        self.drag = None
        out = self.control.pill_release() if d['kind'] == 'slider' else b''
        self.control.flush()  # saved at once on release (no-op if nothing changed)
        return out

    # --- frames ---------------------------------------------------------------------------------------------------
    def _send(self, make, state):
        c = self.control
        frame = make(c.run_id, c.session, c.next_id, state)
        c.next_id += 1
        return frame

    def _handle(self):
        d, h = self.drag, self.hover
        if d is not None and d['kind'] == 'corner': target, active = (d['index'], d['corner']), True
        elif d is None and self.mode == 'free' and h is not None and h[0] == 'corner': target, active = h[1:], False
        else: target, active = None, False
        want = ({'visible': True, 'desktop': target[0], 'corner': target[1], 'active': active}
                if target is not None else {**self.shown, 'visible': False, 'active': False})
        if want == self.shown: return b''
        self.shown = want
        return self._send(resize.handle_frame, want)

    def _pills(self, idle):
        """`move_handle`: the glass pills show under every desktop in the overview while the pointer moved in the last
        30 s (OLED idle close; an open panel is drawn regardless). hover: the pointer is on that pill or the open panel's
        header (or holds it); active: that pill is being dragged (moved past CLICK_PX)."""
        visible = self.active() and not idle
        d, h = self.drag, self.hover
        if d is not None and d['kind'] == 'pill': target, hover, active = d['index'], True, d['moved']
        elif d is None and self.mode == 'free' and h is not None and h[0] == 'pill': target, hover, active = h[1], True, False
        else: target, hover, active = self.pills['desktop'], False, False
        want = {'visible': visible, 'desktop': target, 'hover': hover and visible, 'active': active and visible}
        if want == self.pills: return b''
        self.pills = want
        return self._send(resize.pill_frame, want)

    def _pointer_frame(self, idle):
        """The dot: in desktop and free mode (the real cursor is hidden in both: overview_cursor.py), hidden when idle."""
        p = self.pointer
        if self.mode in DOT_MODES and p is not None and not idle and self.active():
            want = {'visible': True, 'x': int(math.floor(p[0] + 0.5)), 'y': int(math.floor(p[1] + 0.5)), 'held': self.drag is not None}
        else:
            want = {**self.sent, 'visible': False, 'held': False}
        if want == self.sent: return b''
        self.sent = want
        return self._send(overview_pointer_frame, want)


class PointerSampler:
    """Background cursor reads (250 Hz in the overview) and every compositor call this feature makes: the left-button
    binds, hiding / showing / parking the cursor and handing it back. stop() undoes them before returning."""
    def __init__(self, client, names, pointer, bind_args, period=SAMPLE_S):
        self.client, self.names, self.pointer, self.bind_args, self.period = client, list(names), pointer, bind_args, period
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.applied = 0          # last want serial applied
        self.grabbed = False      # binds installed and cursor hidden (free mode); removal shows it again
        self.ever_grabbed = False
        self.park = None          # where the hidden cursor is kept
        self._last = None         # previous cursor sample (motion is measured from it)
        self._regrabbed_at = None
        self.errors = self.samples = self.recentres = 0
        self.cursor = OverviewCursor(pointer.control)  # circle only: the real cursor hidden while the dot is the pointer

    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=2.5):
        self._stop.set()
        self._thread.join(timeout)
        if self.ever_grabbed:  # best effort even if the thread is stuck; xr_control drops stale ones on the next click
            try: self.client.remove_resize_binds()  # also shows the cursor again and puts it back
            except (OSError, ValueError, TypeError, KeyError): pass
            self.grabbed = False
        self.cursor.stop(self.client)  # and the overview's hidden cursor (any order with the line above is safe)

    def _run(self):
        outputs, refreshed = [], None
        while not self._stop.is_set():
            if not self.pointer.overview():
                self._release()
                self.cursor.step(self.client, False)  # the normal cursor outside the overview
                self.applied = None  # re-apply the current want when the overview is back
                self._last = None
                self._stop.wait(IDLE_S)
                continue
            now = time.monotonic()
            try:
                if refreshed is None or now - refreshed >= OUTPUTS_REFRESH:
                    outputs = resize.outputs_from_monitors(self.client.query('monitors', timeout=0.5), self.names)
                    refreshed = now
            except (OSError, ValueError, TypeError, KeyError):
                self.errors += 1
            self._apply()
            self.cursor.step(self.client, self.pointer.mode in DOT_MODES)  # hidden while the dot is the pointer
            cursor, motion = self._sample()
            self.pointer.feed(cursor, motion, outputs, self.applied if self.applied is not None else -1)
            self._recentre(cursor)
            self._stop.wait(self.period)

    def _sample(self):
        try:
            pos = self.client.query('cursorpos', timeout=0.25)
            x, y = pos['x'], pos['y']
            if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('cursor position')
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
            return None, (0.0, 0.0)
        self.samples += 1
        cursor = (int(x), int(y))
        last, self._last = self._last, cursor
        if last is None: return cursor, (0.0, 0.0)
        dx, dy = cursor[0] - last[0], cursor[1] - last[1]
        if abs(dx) > SAMPLE_JUMP_PX or abs(dy) > SAMPLE_JUMP_PX: return cursor, (0.0, 0.0)
        return cursor, (float(dx), float(dy))

    def _recentre(self, cursor):
        """Free mode: the hidden cursor has strayed from its parking point; put it back (motion is measured from there)."""
        if not self.grabbed or cursor is None or self.park is None: return
        if math.hypot(cursor[0] - self.park[0], cursor[1] - self.park[1]) <= RECENTRE_PX: return
        try:
            self.client.warp_cursor(*self.park)
            self._last = self.park; self.recentres += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError): pass

    def _apply(self):
        serial, kind, point = self.pointer.want
        if self.pointer.binds_dropped:  # a config reload removed the binds and showed the cursor
            self.pointer.binds_dropped = False
            if self.grabbed:
                now = time.monotonic()
                if self._regrabbed_at is None or now - self._regrabbed_at >= 2.0:  # never a reload loop
                    self._regrabbed_at = now
                    self.grabbed = False
                    self.applied = None
        if serial == self.applied: return
        try:
            if kind == 'free':
                if point is None: return
                self.ever_grabbed = True  # set first: a failed or partial grab is still undone at stop
                self.client.grab_pointer(*self.bind_args, *point)
                self.grabbed, self.park, self._last = True, point, point
            elif kind == 'off':
                self._release()
            elif point is not None:
                self.client.release_pointer(*point)
                self.grabbed, self._last = False, point
            elif self.grabbed:
                self.client.release_pointer()
                self.grabbed = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
            return  # retried on the next sample
        self.applied = serial

    def _release(self):
        if not self.grabbed: return
        try:
            self.client.remove_resize_binds()  # binds gone, cursor shown and put back where free mode found it
            self.grabbed = False
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
