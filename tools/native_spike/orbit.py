"""Desktops anywhere around you (3DoF): the orbit placement, the pill drag that carries a desktop round you, and the
footprints that tell whether desktops overlap as you see them (bump.py pushes desktops out of each other's way with
them). Pure maths, no I/O.

Design (3dof-free-placement, 2026-11-02):
  * Where a desktop is. Its `x` / `y` placement (geometry.PLACEMENT_BOUNDS) is an orbit around your head in tenths of a
    degree: x turns it round you (positive to the right; +-1800, directly behind you, is one place), y lifts it along its
    own meridian (positive up; +-900 is straight overhead / underfoot). x = y = 0 is the desktop's own slot in the row.
  * Every desktop always faces you (a billboard toward the eye, which in 3DoF is where your head is): at its slot, while
    it is dragged and wherever it ends up, it is square to your line of sight and upright along its meridian. There is
    no setting for which way it faces (the yaw / pitch / roll settings were removed, 2026-11-02). place_point draws it
    straight ahead and turns it to its direction, Az(a0 + x) . El(e0 + y), a0 / e0 its slot's own direction; so moving
    it is the rigid rotation about the eye rotation(a0, x, y) = Az(a0 + x) . El(y) . Az(-a0), which unorbit_ray and
    footprint use. Mirrors spike/src/view.cpp place_point (the `placement_contract` ctest compares them).
    Until 2026-11-02 x / y were a flat offset in percent of the desktop's own size, at most one desktop away; a saved value
    reads as at most 10 degrees now, about where it was (a 16:9 desktop is ~13 degrees wide in the overview).
  * The dot's direction (pointer_direction, pointer_ray; inverse pointer_at) is linear in the pointer: the same angle
    per canvas pixel (so per mouse pixel) everywhere, the overview plane's own rate at the view's centre, in every mode.
    In anchor (3DoF) mode the pointer's reach (free_pointer.OverviewPointer._clamp, reach()) goes on to directly behind
    you and straight up / down, so the dot goes anywhere round you; in follow and locked modes the view turns with your
    head and the reach is REACH, half a view past each edge. The renderer always draws the dot in that direction (the
    frame's az / el, spike view.hpp direction_disc), and the hit test (free_pointer.hit, unorbit_ray), the desktop-mode
    cursor mapping (free_pointer.project) and the drag all read it: what the dot is drawn on is what it hits.
    Until 2026-11-02 (drag-speed-linear) the dot was the pointer's ray through the plane inside the view (atan) and,
    in anchor mode, a quadratic stretch from the view's edge to behind you over just half a view, so past the edge the
    dot and a dragged desktop sped up about twelvefold and stopped following the mouse's speed.
  * The pill drag (PillDrag, drag_target): the point grabbed at the press (grab_point: the dot's ray at the press, in
    the desktop's own frame) is kept exactly on the dot's ray (solve: the x / y orbit that turns that point onto it),
    so in anchor mode a desktop goes all the way round you and over you under the dot, within the wire's tenth of a
    degree and the slot's SNAP (and, with the dot at a pole, as near it as an upright desktop takes a point off its
    middle), at the dot's constant angle per mouse pixel. The drag's reach (anchor or not) is fixed when it starts
    (free_pointer), and its target is kept unwrapped, so a desktop held against a neighbour never flips round the other
    way when the target passes behind you.
  * Footprints (footprint, overlap): what a desktop covers is judged as you see it, as the set of directions from your
    eye that its surface covers, with a MARGIN_PX border and, under it, the strip holding its glass pill and the pill's
    grab padding (PILL_ZONE_PX): a convex spherical polygon from its true placed outline (distance, orbit and curvature
    included; a curved desktop's outline is replaced by its convex hull, so it errs early). Desktops that do not share a
    direction neither intersect nor hide each other.
  * No overlap (bump.py, monitor-collision-bump, 2026-11-03): a desktop moved or resized in the overview (a pill drag, a
    corner resize, its open panel's sliders and keys) pushes every desktop it runs into out of its way, cascading, and
    they stay where they were pushed; where there is no room it stops, as the guard before it did.
    Until then the pill drag stopped at a neighbour and slid along it (allowed_move), and everything else went
    through.
"""
import functools
import math

TENTH = math.pi / 1800       # radians per wire unit of x / y
TURN = 3600                  # wire units in a full turn of x
X_LIMIT, Y_LIMIT = 1800, 900
SNAP = 5                     # a drag within this many tenths of a degree of the slot (0) snaps to it, per axis
REACH = 2.0                  # reach() outside anchor mode: half a view past each edge (normalized canvas, 1 = edge)
PILL_ZONE_PX = 34.0          # free_pointer.PILL_GAP + PILL_THICKNESS + PILL_PAD_Y: the pill and its grab zone under a desktop
MARGIN_PX = 6.0              # spike/include/xr/view.hpp kBorderPx: the highlight frame round a desktop (tile px)
AREA_EPS = 1e-9              # steradians: "overlaps no more than now" tolerance
_BEHIND = 1e-6


# --- rotation -------------------------------------------------------------------------------------------------------
def wrap(x):
    """x (tenths of a degree) into -1800..1800 (both ends are the same place, behind you)."""
    return (x + X_LIMIT) % TURN - X_LIMIT if not -X_LIMIT <= x <= X_LIMIT else x


def _diff(a, b):
    """Shortest signed turn from b to a, tenths of a degree."""
    return (a - b + X_LIMIT) % TURN - X_LIMIT


def slot_azimuth(g, tile):
    """The azimuth (radians, positive right) of the centre of a desktop's slot on the overview's plane."""
    return math.atan2((2 * (tile[0] + tile[2] / 2) / g['w'] - 1) * g['half'][0], 1.0)


def rotation(a0, x, y):
    """3x3 (rows) of the orbit Az(a0 + x) . El(y) . Az(-a0); x, y in tenths of a degree."""
    def az(a):  # turns a point to the right about the vertical through the eye
        c, s = math.cos(a), math.sin(a)
        return ((c, 0.0, -s), (0.0, 1.0, 0.0), (s, 0.0, c))
    c, s = math.cos(y * TENTH), math.sin(y * TENTH)
    el = ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))  # lifts a point in front of you
    return _mul(az(a0 + x * TENTH), _mul(el, az(-a0)))


def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def apply(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2], m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def apply_t(m, v):
    """The inverse rotation (transpose)."""
    return (m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2], m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
            m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2])


def place_point(g, tile, p, px, py):
    """World position of canvas pixel (px, py) on a desktop laid out at `tile` with wire placement `p`
    (geometry.PLACEMENT_BOUNDS units, plus an optional 'roll': the renderer's levelling roll about the line of sight in
    hundredths of a degree, counter-clockwise as you see it, free_pointer.py's roll-level icons). The desktop always faces the eye, upright along its meridian (a billboard): its
    centre lies in its slot's direction turned by the orbit, `distance` times as far as the slot's centre, and its
    surface (bent by `curvature` toward you) is square to that line of sight: Az(a0 + x) . El(e0 + y) applied to the
    desktop drawn straight ahead, a0 / e0 the slot centre's azimuth / elevation. Mirrors spike/src/view.cpp
    place_point exactly (the `placement_contract` ctest)."""
    W, H = g['w'], g['h']
    hw, hh = g['half']
    sx, sy = 2 * hw / W, 2 * hh / H
    tx, ty, tw_px, th_px = tile
    cx, cy = tx + tw_px / 2, ty + th_px / 2
    tw = tw_px * sx
    d = p['distance'] / 100 if p['distance'] > 0 else 1.0
    wx, wy = (2 * cx / W - 1) * hw, (1 - 2 * cy / H) * hh  # the slot's centre on the overview's plane (z = -1)
    a0, e0 = math.atan2(wx, 1.0), math.atan2(wy, math.sqrt(1 + wx * wx))
    r = math.sqrt(1 + wx * wx + wy * wy) * d
    u, v = (px - cx) * sx, (cy - py) * sy
    local = (u, v, 0.0)
    curvature = math.radians(p['curvature'])
    if curvature > 1e-4 and tw > 0:
        rc = tw / curvature
        phi = u / rc
        local = (rc * math.sin(phi), v, rc * (1 - math.cos(phi)))  # toward you at the edges
    roll = math.radians(p.get('roll', 0) / 100)  # levelling roll (hundredths of a degree; not a wire placement key)
    if roll:
        cr, sr = math.cos(roll), math.sin(roll)
        local = (cr * local[0] - sr * local[1], sr * local[0] + cr * local[1], local[2])
    q = (local[0], local[1], local[2] - r)  # straight ahead, square to the line of sight
    ce, se = math.cos(e0 + p['y'] * TENTH), math.sin(e0 + p['y'] * TENTH)
    q = (q[0], ce * q[1] - se * q[2], se * q[1] + ce * q[2])
    ca, sa = math.cos(a0 + p['x'] * TENTH), math.sin(a0 + p['x'] * TENTH)
    return (ca * q[0] - sa * q[2], q[1], sa * q[0] + ca * q[2])


# --- the dot's direction ---------------------------------------------------------------------------------------------
def pointer_direction(g, pointer):
    """(azimuth, elevation) in radians of the dot at canvas point `pointer`: linear in the pointer, the same angle per
    canvas pixel everywhere (the plane's own rate at the view's centre, half / (canvas / 2)), all the way round you.
    Elevation stops at the poles. The renderer draws the dot in this direction (the frame's az / el), so the dot, the
    hit test and the drag all agree on where it is."""
    u, v = 2 * pointer[0] / g['w'] - 1, 1 - 2 * pointer[1] / g['h']
    hw, hh = g['half']
    return u * hw, min(max(v * hh, -math.pi / 2), math.pi / 2)


def pointer_ray(g, pointer):
    """The unit direction of the dot at canvas point `pointer` (pointer_direction)."""
    az, el = pointer_direction(g, pointer)
    return (math.cos(el) * math.sin(az), math.sin(el), -math.cos(el) * math.cos(az))


def pointer_at(g, d):
    """The canvas point whose dot lies in direction `d` (a world vector from the eye): pointer_ray's inverse, azimuth
    in -180..180 degrees."""
    hw, hh = g['half']
    az, el = math.atan2(d[0], -d[2]), math.atan2(d[1], math.hypot(d[0], d[2]))
    return ((az / hw + 1) * g['w'] / 2, (1 - el / hh) * g['h'] / 2)


def plane_at(g, d):
    """Where direction `d` from the eye meets the overview's plane (z = -1), canvas px; None if it points away from it."""
    if d[2] > -_BEHIND: return None
    hw, hh = g['half']
    return ((d[0] / -d[2] / hw + 1) * g['w'] / 2, (1 - d[1] / -d[2] / hh) * g['h'] / 2)


def from_plane(g, point):
    """The canvas point whose dot looks at canvas point `point` on the overview's plane (plane_at's inverse)."""
    hw, hh = g['half']
    return pointer_at(g, ((2 * point[0] / g['w'] - 1) * hw, (1 - 2 * point[1] / g['h']) * hh, -1.0))


def reach(g, around):
    """How far the pointer goes from the view's centre, (u, v) in normalized canvas (1 = the view's edge): in anchor
    mode (`around`) to directly behind you and straight up / down, else REACH (half a view past each edge)."""
    hw, hh = g['half']
    return (math.pi / hw, math.pi / 2 / hh) if around else (REACH, REACH)


def unorbit_ray(g, tile, p, pointer):
    """The dot's ray (pointer_ray) as the un-orbited desktop sees it, on the overview's plane (canvas px): the ray turned
    back by the desktop's orbit. None if that ray points away from the plane (it cannot meet the desktop)."""
    d = pointer_ray(g, pointer)
    if p['x'] or p['y']: d = apply_t(rotation(slot_azimuth(g, tile), p['x'], p['y']), d)
    return plane_at(g, d)


# --- the pill drag ----------------------------------------------------------------------------------------------------
def _snap(value):
    """0 within SNAP of the slot; x snaps to the nearest whole turn (it may be unwrapped)."""
    return value - wrap(value) if abs(wrap(value)) <= SNAP else value


def slot_direction(g, tile):
    """(azimuth, elevation) in radians of a desktop's slot centre: a0 / e0 of place_point."""
    W, H = g['w'], g['h']
    hw, hh = g['half']
    wx, wy = (2 * (tile[0] + tile[2] / 2) / W - 1) * hw, (1 - 2 * (tile[1] + tile[3] / 2) / H) * hh
    return math.atan2(wx, 1.0), math.atan2(wy, math.sqrt(1 + wx * wx))


def grab_point(g, tile, start, press):
    """The grabbed point of a pill drag, as a unit direction in the desktop's own frame (the desktop drawn straight
    ahead, before place_point's Az(a0 + x) . El(e0 + y)): the dot's ray at the press (pointer_ray) turned back by the
    desktop's placement `start` at the press."""
    a0, e0 = slot_direction(g, tile)
    d = pointer_ray(g, press)
    a, e = a0 + start[0] * TENTH, e0 + start[1] * TENTH
    c, s = math.cos(a), math.sin(a)
    d = (c * d[0] + s * d[2], d[1], -s * d[0] + c * d[2])  # Az(-a)
    c, s = math.cos(e), math.sin(e)
    return (d[0], c * d[1] + s * d[2], -s * d[1] + c * d[2])  # El(-e)


def _nearest(angle, near):
    """`angle` plus whole turns, as close to `near` as it gets."""
    return angle + 2 * math.pi * round((near - angle) / (2 * math.pi))


def solve(g, tile, local, pointer, near):
    """The (x, y) placement, unrounded tenths, x unwrapped next to near[0], that puts the desktop-frame direction
    `local` (grab_point) exactly on the dot's ray at canvas point `pointer` (pointer_ray):
    Az(a0 + x) . El(e0 + y) . local = ray.
    Az keeps a direction's height, so El(e0 + y) alone must lift `local` to the ray's: of its two solutions the one
    next to near[1]; then Az turns it onto the ray. Past where the lift can reach (a point well off the desktop's
    centre line, near a pole) it lifts as far as it goes."""
    a0, e0 = slot_direction(g, tile)
    d = pointer_ray(g, pointer)
    lx, ly, lz = local
    r = math.hypot(ly, lz)
    near_e = e0 + near[1] * TENTH
    if r < 1e-12: e = near_e
    else:
        phi, k = math.atan2(lz, ly), math.acos(min(max(d[1] / r, -1.0), 1.0))
        e = min((_nearest(k - phi, near_e), _nearest(-k - phi, near_e)), key=lambda t: abs(t - near_e))
    c, s = math.cos(e), math.sin(e)
    mx, mz = lx, s * ly + c * lz
    a = math.atan2(d[0], -d[2]) - math.atan2(mx, -mz)
    a = _nearest(a, a0 + near[0] * TENTH)
    return (a - a0) / TENTH, (e - e0) / TENTH


def _target(g, tile, local, near, pointer):
    """The (x, y) a pill drag asks for: x unwrapped, y within the poles, each snapped to the slot within SNAP."""
    x, y = solve(g, tile, local, pointer, near)
    y = min(max(y, -Y_LIMIT), Y_LIMIT)
    return (x, y), (_snap(round(x)), 0 if abs(round(y)) <= SNAP else round(y))


def drag_target(g, tile, start, press, pointer):
    """New (x, y) placement for a pill drag of the desktop laid out at `tile`: `start` its (x, y) at the press, `press`
    and `pointer` canvas px. The point grabbed at the press (grab_point) goes
    exactly under the dot: x wraps round, y stops at the poles, each snaps back to the slot within SNAP. No bumping:
    PillDrag pushes the desktops it runs into out of its way (bump.py)."""
    _, (x, y) = _target(g, tile, grab_point(g, tile, start, press), start, pointer)
    return wrap(x), y


class PillDrag:
    """One pill drag from the press to the release: the target the pointer asks for, where the desktop gets to and the
    desktops it pushes out of its way on the way there (bump.py). The grabbed point (grab_point, fixed at the press) is
    kept under the dot; x is followed unwrapped and only wrapped for the wire."""
    def __init__(self, g, index, start, press):
        self.g, self.index, self.start, self.press = g, index, (start[0], start[1]), press
        self.tile = g['tiles'][index]
        self.local = grab_point(g, self.tile, self.start, press)
        self.at = self.near = self.start
        self.pusher = None  # bump.Pusher, for the whole drag (it remembers which way it pushed each desktop)

    def follow(self, pointer, placements):
        """Every desktop's wire placement for the pointer at canvas point `pointer`: the dragged one with the grabbed point
        under the dot (or as near as there is room for), every desktop it runs into pushed out of its way (bump.py)."""
        from . import bump  # (bump.py builds on this module)
        p = placements[self.index]
        if wrap(self.at[0]) != p['x'] or self.at[1] != p['y']:  # changed meanwhile (a panel key): carry on from there
            self.at = (self.at[0] + _diff(p['x'], wrap(self.at[0])), p['y'])
        self.near, target = _target(self.g, self.tile, self.local, self.near, pointer)
        if self.pusher is None: self.pusher = bump.Pusher(self.g, self.index)
        out = self.pusher.move(placements, {'x': target[0], 'y': target[1]}, start={'x': self.at[0], 'y': self.at[1]})
        self.at = (self.pusher.reached['x'], self.pusher.reached['y'])
        return out


# --- footprints: what each desktop covers, seen from your eye ------------------------------------------------------
def _norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 0 else None


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _angle(a, b):
    return math.atan2(math.sqrt(max(_dot(_cross(a, b), _cross(a, b)), 0.0)), _dot(a, b))


def _outline(g, tile, p):
    """Points round a desktop, its MARGIN_PX border and the pill zone under it, on its placed surface (un-orbited): the
    corners and, when curved, the top and bottom edges every few degrees of arc."""
    tx, ty, tw, th = tile
    k = p['distance'] / 100 if p['distance'] > 0 else 1.0  # the pill keeps its size (free_pointer.glass_to_desktop)
    top, bottom = ty - MARGIN_PX, ty + th + PILL_ZONE_PX * k + MARGIN_PX
    columns = max(4, min(16, math.ceil(p['curvature'] / 6))) if p['curvature'] > 0 else 1
    xs = [tx - MARGIN_PX + (tw + 2 * MARGIN_PX) * i / columns for i in range(columns + 1)]
    return [place_point(g, tile, p, x, top) for x in xs] + [place_point(g, tile, p, x, bottom) for x in reversed(xs)]


class Footprint:
    """A convex spherical polygon: unit direction vectors (counter-clockwise as you see it), its edges' inward great-circle
    normals, and a bounding cone (centre, angular radius). `vertices` None: it reaches 90 degrees from its own centre or
    further, too wide to be convex; then only its cone is used (bumping errs early)."""
    __slots__ = ('vertices', 'normals', 'centre', 'radius')

    def __init__(self, vertices, centre, radius):
        self.vertices, self.centre, self.radius = vertices, centre, radius
        self.normals = None
        if vertices:
            k = len(vertices)
            normals = [_cross(vertices[i], vertices[(i + 1) % k]) for i in range(k)]
            self.normals = [n if _dot(n, centre) >= 0 else (-n[0], -n[1], -n[2]) for n in normals]

    def rotated(self, m):
        if m is None: return self
        out = Footprint(None, apply(m, self.centre), self.radius)
        if self.vertices:
            out.vertices = [apply(m, v) for v in self.vertices]
            out.normals = [apply(m, n) for n in self.normals]
        return out


def _hull(points):
    """Andrew's monotone chain: indices of the convex hull of 2-D `points`, counter-clockwise, no collinear points."""
    order = sorted(range(len(points)), key=lambda i: points[i])
    if len(order) < 3: return order
    def turn(o, a, b):
        (ox, oy), (ax, ay), (bx, by) = points[o], points[a], points[b]
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)
    lower, upper = [], []
    for i in order:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], i) <= 1e-12: lower.pop()
        lower.append(i)
    for i in reversed(order):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], i) <= 1e-12: upper.pop()
        upper.append(i)
    return lower[:-1] + upper[:-1]


def footprint(g, tile, p):
    """What a desktop at `tile` with placement `p` covers as you see it (orbit included)."""
    flat = {**p, 'x': 0, 'y': 0}
    key = (g['w'], g['h'], tuple(g['half']), tuple(tile), tuple(flat[k] for k in sorted(flat)))
    base = _footprint(key)
    if not p['x'] and not p['y']: return base
    return base.rotated(rotation(slot_azimuth(g, tile), p['x'], p['y']))


@functools.lru_cache(maxsize=64)
def _footprint(key):
    w, h, half, tile, values = key
    g = {'w': w, 'h': h, 'half': half}
    p = dict(zip(sorted(('x', 'y', 'distance', 'curvature')), values))
    dirs = [d for d in (_norm(q) for q in _outline(g, tile, p)) if d is not None]
    centre = _norm(tuple(sum(d[i] for d in dirs) for i in range(3)))
    if centre is None:  # a huge, close, curved desktop wrapping round the eye: it covers every direction
        tx, ty, tw, th = tile
        return Footprint(None, _norm(place_point(g, tile, p, tx + tw / 2, ty + th / 2)) or (0.0, 0.0, -1.0), math.pi)
    radius = max(_angle(centre, d) for d in dirs) + 1e-9
    if any(_dot(d, centre) < 0.02 for d in dirs): return Footprint(None, centre, radius)
    up = (0.0, 1.0, 0.0) if abs(centre[1]) < 0.9 else (0.0, 0.0, -1.0)
    right = _norm(_cross(centre, up))
    top = _cross(right, centre)
    flat2 = [(_dot(d, right) / _dot(d, centre), _dot(d, top) / _dot(d, centre)) for d in dirs]
    return Footprint([dirs[i] for i in _hull(flat2)], centre, radius)


def _clip(polygon, normal):
    """Sutherland-Hodgman: the part of a spherical polygon on the inner side of a great circle (normal . p >= 0)."""
    out = []
    k = len(polygon)
    for i in range(k):
        a, b = polygon[i], polygon[(i + 1) % k]
        da, db = _dot(normal, a), _dot(normal, b)
        if da >= 0: out.append(a)
        if (da >= 0) != (db >= 0):
            t = da / (da - db)
            q = _norm((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
            if q is not None: out.append(q)
    return out


def _area(polygon):
    """Solid angle (steradians) of a convex spherical polygon (Van Oosterom-Strackee triangle fan)."""
    total, a = 0.0, polygon[0] if polygon else None
    for i in range(1, len(polygon) - 1):
        b, c = polygon[i], polygon[i + 1]
        det = _dot(a, _cross(b, c))
        total += abs(2 * math.atan2(det, 1 + _dot(a, b) + _dot(b, c) + _dot(c, a)))
    return total


def overlap(a, b):
    """Solid angle that footprints `a` and `b` share (0 if they only touch or are apart)."""
    gap = _angle(a.centre, b.centre) - a.radius - b.radius
    if gap >= 0: return 0.0
    if a.vertices is None or b.vertices is None:  # too wide to clip: the cones' overlap, a stand-in that grows with it
        return gap * gap
    polygon = a.vertices
    for n in b.normals:
        polygon = _clip(polygon, n)
        if len(polygon) < 3: return 0.0
    return _area(polygon)
