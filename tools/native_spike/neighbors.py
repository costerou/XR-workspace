"""Which desktop lies past an edge of the selected one, as placed round you in 3D. Pure maths, no I/O.

Design (dot-directional-switch, 2026-11-03): zoomed in, pushing the cursor past an edge of the selected desktop ("home")
switches to the desktop placed that way round you (unified_cursor.EdgePush), not to the next one in the row.
  * Each desktop is a direction from your eye: its centre as drawn (orbit.place_point at its overview slot, with its
    orbit and distance), plus its own right and up axes there (the desktop is a billboard, so these are the screen axes
    the cursor moves along on it). A maximized home is drawn level, so the live caller passes no roll
    (unified_cursor.UnifiedPointer.neighbour): pushing right is right as you see it.
  * A candidate's bearing from home is the great-circle direction from home's centre toward its centre, read in home's
    right / up axes; its distance is the angle between the two centres. Pushing right means home's +right, up its +up,
    and so on, so a placement with a 'roll' would turn the directions with it.
  * The candidate must lie within CONE of the push direction. Among those the nearest wins, an off-axis one counting as
    further: score = angle / cos(off-axis). A desktop (within BEHIND of) directly behind home has no bearing and is never picked.
  * No candidate in the cone: None, the cursor stays on home (clamped at the edge).
  * The live push measures all of that from the point of home's edge the cursor is on, as the desktops are placed, within a
    narrower cone (pick_at, cursor-switch-animation 2026-11-03: Cody, "only pan over ... if the monitor IS actually in that
    general direction"; edge_pan.py). A desktop near the end of a long edge can be in reach from one end and not the other.
Without an overview layout from the renderer (none reported yet) only the row order is known: right is the next desktop,
left the previous one, up and down nothing.
"""
import math

from . import orbit

DIRECTIONS = {'right': (1.0, 0.0), 'left': (-1.0, 0.0), 'up': (0.0, 1.0), 'down': (0.0, -1.0)}
CONE = math.radians(60)  # half-angle round the push direction a neighbour must lie in (60: a 45-degree diagonal is
                         # reachable from both of its sides)
PAN_CONE = math.radians(45)  # pick_at's half-angle: a 45-degree diagonal is still reachable from both of its sides
BEHIND = math.radians(1)  # a desktop within this of directly behind home's direction has no usable bearing
_EPS = 1e-9


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def frame(g, tile, p):
    """(centre direction, right axis, up axis) of a desktop laid out at `tile` with placement `p` (orbit.place_point's,
    'roll' included), all unit vectors in the eye's frame, or None if degenerate."""
    tx, ty, tw, th = tile
    cx, cy = tx + tw / 2, ty + th / 2
    c = orbit.place_point(g, tile, p, cx, cy)
    d = orbit._norm(c)
    if d is None: return None
    r = _sub(orbit.place_point(g, tile, p, cx + 1, cy), c)
    u = _sub(orbit.place_point(g, tile, p, cx, cy - 1), c)  # canvas y grows down
    r = orbit._norm(_sub(r, _scale(d, orbit._dot(r, d))))
    if r is None: return None
    u = _sub(u, _scale(d, orbit._dot(u, d)))
    u = orbit._norm(_sub(u, _scale(r, orbit._dot(u, r))))
    if u is None: return None
    return d, r, u


def frames(g, placements):
    """frame() for every desktop that has a slot in overview layout `g` (free_pointer.parse_overview_geometry)."""
    return [frame(g, g['tiles'][i], p) if i < len(g['tiles']) else None for i, p in enumerate(placements)]


def bearing(home, other):
    """(angle between the centres in radians, unit 2D bearing in home's (right, up) axes) of `other` seen from `home`
    (frame() tuples), or None when it has no bearing (the same direction, or within BEHIND of exactly opposite)."""
    c, r, u = home
    d = other[0]
    cos = max(-1.0, min(1.0, orbit._dot(c, d)))
    t = _sub(d, _scale(c, cos))
    a, b = orbit._dot(t, r), orbit._dot(t, u)
    n = math.hypot(a, b)
    angle = math.acos(cos)
    if n < _EPS or angle > math.pi - BEHIND: return None
    return angle, (a / n, b / n)


def pick(fs, home, direction, cone=CONE):
    """The index of the desktop past home's `direction` edge ('left', 'right', 'up', 'down') among frames `fs`, or None."""
    if direction not in DIRECTIONS: raise ValueError('invalid direction')
    if not 0 <= home < len(fs) or fs[home] is None: return None
    px, py = DIRECTIONS[direction]
    best, best_score = None, math.inf
    for j, f in enumerate(fs):
        if j == home or f is None: continue
        b = bearing(fs[home], f)
        if b is None: continue
        angle, (bx, by) = b
        off = math.acos(max(-1.0, min(1.0, bx * px + by * py)))
        if off > cone + 1e-12: continue
        score = angle / max(math.cos(off), _EPS)
        if score < best_score - 1e-12: best, best_score = j, score
    return best


def edge_frame(g, tile, p, direction, along):
    """frame() of the point of edge `direction` `along` (0..1, left / top first) of the way along it: its direction and
    home's right and up axes taken to that point. None if degenerate."""
    x, y, w, h = tile
    f = max(0.0, min(1.0, along))
    px, py = {'right': (x + w, y + h * f), 'left': (x, y + h * f), 'up': (x + w * f, y), 'down': (x + w * f, y + h)}[direction]
    base, e = frame(g, tile, p), orbit._norm(orbit.place_point(g, tile, p, px, py))
    if base is None or e is None: return None
    r = orbit._norm(_sub(base[1], _scale(e, orbit._dot(base[1], e))))
    if r is None: return None
    u = _sub(base[2], _scale(e, orbit._dot(base[2], e)))
    u = orbit._norm(_sub(u, _scale(r, orbit._dot(u, r))))
    return None if u is None else (e, r, u)


def pick_at(g, placements, home, direction, along, cone=PAN_CONE):
    """pick() for a cursor `along` (0..1) of the way along home's `direction` edge: the bearings are from that point of the
    edge, on the desktops as placed, within `cone`."""
    if direction not in DIRECTIONS: raise ValueError('invalid direction')
    if not 0 <= home < len(placements) or home >= len(g['tiles']): return None
    origin = edge_frame(g, g['tiles'][home], placements[home], direction, along)
    if origin is None: return None
    fs = frames(g, placements)
    fs[home] = origin
    return pick(fs, home, direction, cone)


def row_fallback(count, home, direction):
    """No layout: right is the next desktop in the row, left the previous one; up / down nothing."""
    if direction not in DIRECTIONS: raise ValueError('invalid direction')
    target = home + 1 if direction == 'right' else home - 1 if direction == 'left' else None
    return target if target is not None and 0 <= target < count else None
