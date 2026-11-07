"""The push past a maximized desktop's edge, drawn by the hand: which way it goes and how far. Pure maths, no I/O.

Design (cursor-switch-animation, 2026-11-03; Cody: "only pan over to the monitor with cursor if the monitor IS actually in that
general direction and try to make it as 1:1 as possible with where the cursor is"). Every other switch stays a cut.
  * Real positions. A maximized desktop fills the view and covers its neighbours, so where it is drawn says nothing about
    where they are. All of this is measured on the desktops as placed, at their overview size (orbit.place_point on the
    renderer's overview layout), and only the way across (below) reads the drawn edge.
  * General direction (neighbors.pick_at). From exit point E, the point of home's edge the cursor is on, a desktop's centre
    must lie within neighbors.PAN_CONE of the push. Nothing there: None, and the cursor just stops at the edge. So the
    same desktop can be in reach from one end of an edge and out of it from the other.
  * Entry point (entry_fraction). The point of the target's near edge at the height the cursor left at, measured against
    home's own up (for a left / right push; its right for up / down), clamped to that edge. Not its centre, and not the same
    fraction along it unless the two desktops are level with each other.
  * The hand (unified_cursor.EdgePush measures it, signed, so it can come back). Travel is logical px past the edge:
      - START_PX of push shows nothing, as a brush of the edge never did;
      - from there the view turns toward the target at rate() (radians per px: the maximized desktop's whole angle over its
        whole width), 1:1 with the cursor, every px of the way;
      - the peek, peek_px: the zoom falls to pan_zoom() and the dot rides home's edge;
      - the crossing, gap_px: the dot leaves the edge at the drawn E and goes along the great circle to P at that same rate.
    Neighbours in a row are under a degree apart as placed and a maximized desktop covers them, so the crossing alone would
    leave the view nearly still and the switch a clip. So the peek is as long as it takes the view to turn MEET_SHARE of the
    way from one desktop's centre to the other's (PEEK_PX at the least): the hand carries the view until the dot meets the
    target with both in sight, in the row about half a desktop's width of mouse travel. Far apart the gap is the whole of it.
    Coming back runs all of that backwards; back at the edge, or at rest for REST_S, the pan ends and the view is back on
    home. The pan is complete at total_px, however the hand got there; the renderer eases the landing from where it stands.
  * pan_zoom(). As high as keeps both desktops in view: home's drawn edge no more than half way across the placed gap, so
    there is a way across, and the far edges of the two within the view with KEEP_IN_VIEW of it to spare. Where no zoom can get both in view (a desktop far round you) it is the
    highest that does not cover the target, at most MAX_ZOOM, which keeps the dot off the very edge of the glasses. Across the
    crossing it goes on down to 0, because that is the one zoom where the renderer can hand from one desktop to the other.
  * Overview (overview-edge-pan, 2026-11-07; Cody: "similar to the maximized panning with the cursor but in overview mode ...
    snaps in the direction where the next monitor is ... the same kind of animation"; his picks: the view turns and the room
    re-centres on the target, and the snap comes after the resize handle). The free dot pushed on past the edge it left
    (unified_cursor.UnifiedPointer: Overview push) toward a desktop in the same cone, from the same edge point, pans there by
    hand the same way: START_PX first, the same peek and crossing with the dot on the same path, the view (the room, which is
    anchored) turning 1:1 the same way about the same axis. Nothing zooms (zoom 0: the overview already shows both as
    placed), and the rate is home's own as placed in the overview (overview_rate), so the view turns as fast as the dot
    went over it. Reaching the target selects it in the overview (`pan`), the cursor comes in at the entry point, and the
    renderer eases the rest of the way until the target is where the centre of the view was, which the room keeps.
The renderer's mirror: view.hpp edge_pan_path (the `edge_pan_contract` ctest compares them).
"""
import json
import math

from . import neighbors, orbit

START_PX = 24       # logical px of push before anything shows
PEEK_PX = 48        # the peek is at least this long. With START_PX, the least that can ever switch
MEET_SHARE = 0.5    # the view turns at least this share of the way between the two desktops' centres by hand
REST_S = 0.6        # a hand this long without moving: the pan ends and the view goes back (the hand may pause, not park)
MAX_ZOOM = 0.8      # the zoom never stays higher than this during a pan, so the dot is inside the view
KEEP_IN_VIEW = 0.96  # of the view's width (or height): the most the two desktops may span
SIDES = ('right', 'left', 'up', 'down')  # view.hpp EdgePan::side
OPPOSITE = {'right': 'left', 'left': 'right', 'up': 'down', 'down': 'up'}
_BISECT = 24
_TO_WIRE = 10000


def _unit(v):
    n = math.sqrt(orbit._dot(v, v))
    return None if n < 1e-12 else (v[0] / n, v[1] / n, v[2] / n)


def _angle(a, b):
    return math.acos(max(-1.0, min(1.0, orbit._dot(a, b))))


def edge_pixel(tile, side, f):
    """The pixel `f` (0..1, left / top first) along `side` of `tile`: view.hpp edge_point."""
    x, y, w, h = tile
    f = max(0.0, min(1.0, f))
    if side == 'right': return x + w, y + h * f
    if side == 'left': return x, y + h * f
    if side == 'up': return x + w * f, y
    return x + w * f, y + h


def point(g, tile, p, side, f):
    """The unit direction of that pixel on a desktop laid out at `tile` with placement `p`, or None."""
    return _unit(orbit.place_point(g, tile, p, *edge_pixel(tile, side, f)))


def _slot(g, tile):
    W, H, (hw, hh) = g['w'], g['h'], g['half']
    wx, wy = (2 * (tile[0] + tile[2] / 2) / W - 1) * hw, (1 - 2 * (tile[1] + tile[3] / 2) / H) * hh
    return math.atan2(wx, 1.0), math.atan2(wy, math.sqrt(1 + wx * wx)), math.sqrt(1 + wx * wx + wy * wy)


def fit_tile(g, tile):
    """The tile of a desktop maximized: the view, keeping its aspect, centred (view.cpp zoomed_layout on it)."""
    W, H = g['w'], g['h']
    h = min(H, W * tile[3] / tile[2])
    w = h * tile[2] / tile[3]
    return (W - w) / 2, (H - h) / 2, w, h


def drawn_home(g, tile, p, zoom):
    """(tile, placement) of the desktop the zoom is on, at `zoom`, as the renderer draws it: view.cpp blended_layout and
    zoomed_placements, without the roll (a maximized desktop is level, neighbors.py)."""
    if zoom <= 0: return tile, p
    z = min(zoom, 1.0)
    fit = fit_tile(g, tile)
    now = tuple(a + (b - a) * z for a, b in zip(tile, fit))
    a0, e0, r0 = _slot(g, tile)
    a1, e1, r1 = _slot(g, now)
    d = p['distance'] / 100 if p['distance'] > 0 else 1.0
    rigid = d * (now[3] / tile[3]) * r0 / r1
    return now, {'x': math.remainder(p['x'] * orbit.TENTH + a0 - a1, 2 * math.pi) / orbit.TENTH,
                 'y': (p['y'] * orbit.TENTH + e0 - e1) / orbit.TENTH,
                 'distance': (rigid + (1 - rigid) * z) * 100, 'curvature': p['curvature']}


def overview_rate(g, tile, p, side, size):
    """Radians per logical px of the cursor on a desktop in the overview, along the push: its angle as placed, from edge to
    edge through its middle, over its width (or height). The overview pan's 1:1 (module docstring: Overview). None if
    degenerate."""
    a, b, n = ('left', 'right', size[0]) if side in ('right', 'left') else ('up', 'down', size[1])
    e0, e1 = point(g, tile, p, a, 0.5), point(g, tile, p, b, 0.5)
    if e0 is None or e1 is None or n <= 0: return None
    r = _angle(e0, e1) / n
    return r if r > 0 else None


def rate(g, tile, side, size):
    """Radians per logical px of the cursor on a maximized desktop, along the push: its whole angle over its whole width
    (or height). Not constant across a flat desktop; this is its mean."""
    W, H, (hw, hh) = g['w'], g['h'], g['half']
    _, _, w, h = fit_tile(g, tile)
    if side in ('right', 'left'): return 2 * math.atan(w / W * hw) / size[0]
    return 2 * math.atan(h / H * hh) / size[1]


def entry_fraction(g, home_tile, home_p, target_tile, target_p, side, along):
    """How far along the target's near edge (0..1, left / top first) the cursor comes in after leaving `side` of home `along`
    of the way along it: the point at its height (module docstring), clamped to the edge. None if degenerate."""
    frame = neighbors.frame(g, home_tile, home_p)
    e = point(g, home_tile, home_p, side, along)
    if frame is None or e is None: return None
    axis = frame[2] if side in ('right', 'left') else frame[1]
    want = orbit._dot(e, axis)
    near = OPPOSITE[side]

    def off(f):
        q = point(g, target_tile, target_p, near, f)
        return None if q is None else orbit._dot(q, axis) - want
    lo, hi = off(0.0), off(1.0)
    if lo is None or hi is None: return None
    if lo * hi > 0: return 0.0 if abs(lo) <= abs(hi) else 1.0
    a, b = 0.0, 1.0
    for _ in range(_BISECT):
        m = (a + b) / 2
        v = off(m)
        if v is None: return None
        if v * lo > 0: a = m
        else: b = m
    return (a + b) / 2


def pan_zoom(g, home_tile, home_p, target_tile, target_p, side, along, entry):
    """The zoom a pan falls to in its peek (module docstring)."""
    near, far = OPPOSITE[side], side
    centre = neighbors.frame(g, home_tile, home_p)
    p = point(g, target_tile, target_p, near, entry)
    p_far = point(g, target_tile, target_p, far, entry)
    placed = point(g, home_tile, home_p, side, along)
    if centre is None or p is None or p_far is None or placed is None: return 0.0
    hw, hh = g['half']
    view = 2 * math.atan(hw if side in ('right', 'left') else hh) * KEEP_IN_VIEW
    edge, reach = _angle(centre[0], placed), _angle(centre[0], p)
    reach = edge + max(reach - edge, 0.0) / 2 + 1e-9  # half way across the gap

    def clear(z):  # home's drawn edge is not over the entry point
        t, q = drawn_home(g, home_tile, home_p, z)
        e = point(g, t, q, side, along)
        return e is not None and _angle(centre[0], e) <= reach

    def fits(z):  # the two desktops, far edge to far edge, are within the view
        t, q = drawn_home(g, home_tile, home_p, z)
        e = point(g, t, q, near, along)
        return clear(z) and e is not None and _angle(e, p_far) <= view

    def top(ok):  # the highest zoom up to MAX_ZOOM that passes; every test only gets easier going down
        if ok(MAX_ZOOM): return MAX_ZOOM
        if not ok(0.0): return None
        a, b = 0.0, MAX_ZOOM
        for _ in range(_BISECT):
            m = (a + b) / 2
            if ok(m): a = m
            else: b = m
        return a
    z = top(fits)
    if z is None: z = top(clear)
    return 0.0 if z is None else z


class Plan:
    """One pan: from `home` past `side` toward `target`. exit / entry: fractions along the two edges; zoom: pan_zoom();
    rate: radians per px of the hand; peek_px, gap_px: the two stretches; total_px: both, past START_PX."""
    __slots__ = ('home', 'target', 'side', 'exit', 'entry', 'zoom', 'rate', 'peek_px', 'gap_px', 'total_px')

    def __init__(self, home, target, side, exit_, entry, zoom, rate_, peek_px, gap_px):
        self.home, self.target, self.side, self.exit, self.entry, self.zoom = home, target, side, exit_, entry, zoom
        self.rate, self.peek_px, self.gap_px, self.total_px = rate_, peek_px, gap_px, peek_px + gap_px

    def frame(self, travel):
        """The `edge_pan` payload at `travel` px past START_PX."""
        cross = min(max(travel - self.peek_px, 0.0) / self.gap_px, 1.0) if self.gap_px > 0 else 0.0
        wire = lambda v: round(max(0.0, min(1.0, v)) * _TO_WIRE)
        return {'active': True, 'from': self.home, 'to': self.target, 'side': self.side, 'exit': wire(self.exit),
                'entry': wire(self.entry), 'peek': wire(travel / self.peek_px), 'cross': wire(cross),
                'turn': wire(travel * self.rate / math.pi), 'zoom': wire(self.zoom)}


def plan(g, placements, home, target, side, along, size, overview=False):
    """The Plan for that push, `size` home's logical (width, height), or None (no geometry for it, or degenerate: the
    switch is then a plain cut). `overview`: the push past a desktop's edge in the overview (module docstring: Overview):
    nothing zooms, and the rate is the desktop's own as placed (overview_rate)."""
    n = len(g['tiles'])
    if not (0 <= home < n and 0 <= target < n) or home == target or home >= len(placements) or target >= len(placements):
        return None
    if side not in SIDES or min(size) <= 0: return None
    home_tile, target_tile = g['tiles'][home], g['tiles'][target]
    home_p, target_p = placements[home], placements[target]
    entry = entry_fraction(g, home_tile, home_p, target_tile, target_p, side, along)
    if entry is None: return None
    entry = round(entry * _TO_WIRE) / _TO_WIRE  # what the renderer will read
    exit_ = round(max(0.0, min(1.0, along)) * _TO_WIRE) / _TO_WIRE
    if overview:
        zoom, r = 0.0, overview_rate(g, home_tile, home_p, side, size)
        if r is None: return None
    else:
        zoom = round(pan_zoom(g, home_tile, home_p, target_tile, target_p, side, exit_, entry) * _TO_WIRE) / _TO_WIRE
        r = rate(g, home_tile, side, size)
    t, q = drawn_home(g, home_tile, home_p, zoom)
    e, p = point(g, t, q, side, exit_), point(g, target_tile, target_p, OPPOSITE[side], entry)
    frame, other = neighbors.frame(g, home_tile, home_p), neighbors.frame(g, target_tile, target_p)
    if e is None or p is None or frame is None or other is None: return None
    axis = {'right': frame[1], 'left': frame[1], 'up': frame[2], 'down': frame[2]}[side]
    sign = 1.0 if side in ('right', 'up') else -1.0
    ahead = sign * orbit._dot(tuple(a - b for a, b in zip(p, e)), axis)
    # Desktops that touch or overlap there have no way across: only the peek. Far round, the short way is the push's already.
    gap = _angle(e, p) / r if ahead > 0 or _angle(e, p) > math.pi / 2 else 0.0
    peek = max(PEEK_PX, _angle(frame[0], other[0]) * MEET_SHARE / r - gap)
    return Plan(home, target, side, exit_, entry, zoom, r, peek, gap)


def frame_bytes(run_id, session, request_id, payload):
    """One `edge_pan` frame for the renderer (ipc.hpp); `payload` None: the pan is over."""
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'edge_pan',
                        'payload': payload if payload is not None else {'active': False}}, separators=(',', ':')) + '\n').encode()
