"""Desktops anywhere around you (3DoF): the orbit placement, the pill drag that carries a desktop round you, and the guard
that keeps desktops from overlapping. Pure maths, no I/O.

Design (3dof-free-placement, 2026-11-02):
  * Where a desktop is. Its `x` / `y` placement (geometry.PLACEMENT_BOUNDS) is an orbit around your head in tenths of a
    degree: x turns it round you (positive to the right; +-1800, directly behind you, is one place), y lifts it along its
    own meridian (positive up; +-900 is straight overhead / underfoot). x = y = 0 is the desktop's own slot in the row.
  * Every desktop always faces you (a billboard toward the eye, which in 3DoF is where your head is): at its slot, while
    it is dragged and wherever it ends up, it is square to your line of sight and upright along its meridian. There is
    no setting for which way it faces (the yaw / pitch / roll settings were removed, 2026-11-02). place_point draws it
    straight ahead and turns it to its direction, Az(a0 + x) . El(e0 + y), a0 / e0 its slot's own direction; so moving
    it is the rigid rotation about the eye rotation(a0, x, y) = Az(a0 + x) . El(y) . Az(-a0), which unorbit_pointer and
    footprint use. Mirrors spike/src/view.cpp place_point (the `placement_contract` ctest compares them).
    Until 2026-11-02 x / y were a flat offset in percent of the desktop's own size, at most one desktop away; a saved value
    reads as at most 10 degrees now, about where it was (a 16:9 desktop is ~13 degrees wide in the overview).
  * The pill drag (PillDrag, drag_target): the desktop moves by the change in the pointer's direction since the press, so
    inside the view the pill stays under the dot. In anchor (3DoF) mode the free pointer's reach beyond the view (half a
    view past each edge, free_pointer.OverviewPointer._clamp; REACH here) is stretched smoothly (pointer_direction) so
    that its far left / right edge is directly behind you and its top / bottom edge straight up / down: any direction
    round you is one drag away. In anchor mode the pills are also hit-tested along that stretched direction
    (stretched_pointer, used by free_pointer.hit), so a pill placed anywhere round you lights up and can be grabbed
    again, and the grabbed pill then follows the stretched direction exactly. Inside the view the stretch is the
    identity, so nothing changes there; desktops, corners and panels are still only hit under the dot itself, and
    what is under the dot wins over a stretched pill. In follow and locked modes the view turns with your head, so
    there the desktop just follows the dot. The pointer and its
    clamp are not changed. The drag's reach (anchor or not) is fixed when it starts, and its target is kept unwrapped,
    so a desktop held against a neighbour never flips round the other way when the target passes behind you.
  * No overlap (allowed_move): what a desktop covers is judged as you see it, as the set of directions from your eye that
    its surface covers, with a MARGIN_PX border and, under it, the strip holding its glass pill and the pill's grab
    padding (PILL_ZONE_PX): a convex spherical polygon from its true placed outline (distance, orbit and curvature
    included; a curved desktop's outline is replaced by its convex hull, so the guard errs early). Desktops that do not
    share a direction neither intersect nor hide each other. A drag moves along the straight path toward where the
    pointer wants it and stops before the first step that would make it cover another desktop (no jumping through
    one); if that path is blocked it may slide along the neighbour (sideways first or vertically first, whichever ends
    closer; the slide's corner is returned first, so the renderer's straight-line easing never cuts through the
    neighbour). Each pair is judged on its own: a desktop that already overlaps another (set from the panel) may move
    anywhere it overlaps that one no more than now and every other one not at all, so it is never stuck. The panel's
    sliders and keys and the corner resize are not guarded.
"""
import functools
import math

TENTH = math.pi / 1800       # radians per wire unit of x / y
TURN = 3600                  # wire units in a full turn of x
X_LIMIT, Y_LIMIT = 1800, 900
SNAP = 5                     # a drag within this many tenths of a degree of the slot (0) snaps to it, per axis
REACH = 2.0                  # free_pointer.OverviewPointer._clamp: half a view past each edge (normalized canvas, 1 = edge)
PILL_ZONE_PX = 34.0          # free_pointer.PILL_GAP + PILL_THICKNESS + PILL_PAD_Y: the pill and its grab zone under a desktop
MARGIN_PX = 6.0              # spike/src/gl.cpp kBorderPx: the highlight frame round a desktop (canvas px)
AREA_EPS = 1e-9              # steradians: "overlaps no more than now" tolerance
MAX_CAREFUL_STEPS = 600      # a desktop already overlapping is moved a tenth at a time; at most this far per pointer update
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
    (geometry.PLACEMENT_BOUNDS units). The desktop always faces the eye, upright along its meridian (a billboard): its
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
    q = (local[0], local[1], local[2] - r)  # straight ahead, square to the line of sight
    ce, se = math.cos(e0 + p['y'] * TENTH), math.sin(e0 + p['y'] * TENTH)
    q = (q[0], ce * q[1] - se * q[2], se * q[1] + ce * q[2])
    ca, sa = math.cos(a0 + p['x'] * TENTH), math.sin(a0 + p['x'] * TENTH)
    return (ca * q[0] - sa * q[2], q[1], sa * q[0] + ca * q[2])


def unorbit_pointer(g, tile, p, pointer):
    """Canvas point `pointer` as the un-orbited desktop sees it: the pointer's ray turned back by the desktop's orbit, on
    the overview's plane (canvas px). None if that ray points away from the plane (it cannot meet the desktop)."""
    W, H = g['w'], g['h']
    hw, hh = g['half']
    ray = ((2 * pointer[0] / W - 1) * hw, (1 - 2 * pointer[1] / H) * hh, -1.0)
    x, y, z = apply_t(rotation(slot_azimuth(g, tile), p['x'], p['y']), ray)
    if z > -_BEHIND: return None
    return ((x / -z / hw + 1) * W / 2, (1 - y / -z / hh) * H / 2)


# --- the pill drag ----------------------------------------------------------------------------------------------------
def _ramp(edge, end, slope, t):
    """From `edge` (at t = 0, rising at `slope` per unit t) to `end` at t = 1: a quadratic, so the join is smooth."""
    a = min(max(slope / (end - edge), 0.0), 1.0) if end > edge else 1.0
    t = min(max(t, 0.0), 1.0)
    return edge + (end - edge) * (a * t + (1 - a) * t * t)


def _azimuth(u, hw, around):
    if not around or abs(u) <= 1: return math.atan(u * hw)
    edge = math.atan(hw)
    return math.copysign(_ramp(edge, math.pi, hw / (1 + hw * hw) * (REACH - 1), (abs(u) - 1) / (REACH - 1)), u)


def _elevation(u, v, hw, hh, around):
    c = math.hypot(1.0, (min(abs(u), 1.0) if around else u) * hw)
    if not around or abs(v) <= 1: return math.atan2(v * hh, c)
    edge = math.atan2(hh, c)
    return math.copysign(_ramp(edge, math.pi / 2, hh * c / (c * c + hh * hh) * (REACH - 1), (abs(v) - 1) / (REACH - 1)), v)


def pointer_direction(g, pointer, around=True):
    """(azimuth, elevation) in radians that a drag reads from canvas point `pointer`: the pointer's own direction inside
    the view; with `around` (anchor mode) the reach beyond the view stretches to the far side and the poles."""
    u, v = 2 * pointer[0] / g['w'] - 1, 1 - 2 * pointer[1] / g['h']
    hw, hh = g['half']
    return _azimuth(u, hw, around), _elevation(u, v, hw, hh, around)


def beyond_view(g, pointer):
    """True if canvas point `pointer` lies outside the view the overview was laid out in (where the stretch begins)."""
    return abs(2 * pointer[0] / g['w'] - 1) > 1 or abs(1 - 2 * pointer[1] / g['h']) > 1


def stretched_pointer(g, tile, p, pointer):
    """Anchor mode: where the stretched direction of canvas point `pointer` (pointer_direction) meets the overview's
    plane as the un-orbited desktop sees it (canvas px), or None if it points away from it. Inside the view this is the
    pointer itself (for an un-orbited desktop)."""
    az, el = pointer_direction(g, pointer, True)
    d = (math.cos(el) * math.sin(az), math.sin(el), -math.cos(el) * math.cos(az))
    if p['x'] or p['y']: d = apply_t(rotation(slot_azimuth(g, tile), p['x'], p['y']), d)
    if d[2] > -_BEHIND: return None
    hw, hh = g['half']
    return ((d[0] / -d[2] / hw + 1) * g['w'] / 2, (1 - d[1] / -d[2] / hh) * g['h'] / 2)


def _snap(value):
    """0 within SNAP of the slot; x snaps to the nearest whole turn (it may be unwrapped)."""
    return value - wrap(value) if abs(wrap(value)) <= SNAP else value


def _target(g, start, press, pointer, around):
    """The (x, y) a pill drag asks for, x unwrapped (start + the turn since the press)."""
    a0, e0 = pointer_direction(g, press, around)
    a1, e1 = pointer_direction(g, pointer, around)
    x = start[0] + round((a1 - a0) / TENTH)
    y = min(max(start[1] + round((e1 - e0) / TENTH), -Y_LIMIT), Y_LIMIT)
    return _snap(x), (0 if abs(y) <= SNAP else y)


def drag_target(g, start, press, pointer, around=True):
    """New (x, y) placement for a pill drag: `start` the desktop's (x, y) at the press, `press` and `pointer` canvas px.
    The desktop turns round you by the change in the pointer's direction (pointer_direction). x wraps round, y stops at
    the poles, each snaps back to the slot within SNAP. Unguarded: PillDrag adds the no-overlap guard."""
    x, y = _target(g, start, press, pointer, around)
    return wrap(x), y


class PillDrag:
    """One pill drag from the press to the release: the target the pointer asks for and where the guard lets the desktop
    go. `around` (anchor mode) is fixed for the whole drag; x is followed unwrapped and only wrapped for the wire."""
    def __init__(self, g, index, start, press, around):
        self.g, self.index, self.start, self.press, self.around = g, index, (start[0], start[1]), press, around
        self.at = self.start

    def follow(self, pointer, placements):
        """The desktop's new (x, y) wire placement for the pointer at canvas point `pointer`."""
        p = placements[self.index]
        if wrap(self.at[0]) != p['x'] or self.at[1] != p['y']:  # changed meanwhile (a panel key): carry on from there
            self.at = (self.at[0] + _diff(p['x'], wrap(self.at[0])), p['y'])
        self.at = allowed_move(self.g, self.index, self.at, _target(self.g, self.start, self.press, pointer, self.around),
                               placements)
        return wrap(self.at[0]), self.at[1]


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
    top, bottom = ty - MARGIN_PX, ty + th + PILL_ZONE_PX + MARGIN_PX
    columns = max(4, min(16, math.ceil(p['curvature'] / 6))) if p['curvature'] > 0 else 1
    xs = [tx - MARGIN_PX + (tw + 2 * MARGIN_PX) * i / columns for i in range(columns + 1)]
    return [place_point(g, tile, p, x, top) for x in xs] + [place_point(g, tile, p, x, bottom) for x in reversed(xs)]


class Footprint:
    """A convex spherical polygon: unit direction vectors (counter-clockwise as you see it), its edges' inward great-circle
    normals, and a bounding cone (centre, angular radius). `vertices` None: it reaches 90 degrees from its own centre or
    further, too wide to be convex; then only its cone is used (the guard errs early)."""
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


# --- the guard ------------------------------------------------------------------------------------------------------
def allowed_move(g, index, current, target, placements):
    """The (x, y) a pill drag of desktop `index` may take from `current` toward `target` (module docstring: No overlap).
    x is taken literally (unwrapped: the path runs from current x to target x, never the other way round) and the answer
    is in the same frame. `g` the overview geometry (free_pointer.parse_overview_geometry); without it, or alone, the
    target as asked."""
    current, target = (int(current[0]), int(current[1])), (int(target[0]), int(target[1]))
    if g is None: return target
    n = min(len(g['tiles']), len(placements))
    if not 0 <= index < n: return target
    others = [footprint(g, g['tiles'][j], placements[j]) for j in range(n) if j != index]
    if not others: return target
    tile, p = g['tiles'][index], placements[index]
    base = footprint(g, tile, {**p, 'x': 0, 'y': 0})
    a0 = slot_azimuth(g, tile)

    def at(xy):
        return base.rotated(rotation(a0, xy[0], xy[1]) if xy[0] or xy[1] else None)

    def overlaps(xy):
        f = at(xy)
        return [overlap(f, o) for o in others]

    now = overlaps(current)
    clear = [k for k, v in enumerate(now) if v <= AREA_EPS]  # pairs that must stay clear; the rest may not get deeper

    def ok(xy):
        f = at(xy)
        return all(overlap(f, o) <= limit + AREA_EPS for o, limit in zip(others, now))

    def clearance(xy):  # angle the desktop may turn from xy before its cone meets a clear neighbour's
        c = apply(rotation(a0, xy[0], xy[1]), base.centre)
        return min(_angle(c, others[k].centre) - base.radius - others[k].radius for k in clear)

    careful = len(clear) < len(others)  # an overlapping pair: its overlap must be checked every tenth

    def reach(a, b):
        """The farthest point from a toward b along the straight line before the first step that is not ok."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        steps = max(abs(dx), abs(dy))
        if steps == 0: return a
        per_step = (abs(dx) + abs(dy)) / steps * TENTH  # a point of the desktop moves at most this far per step

        def point(i):
            return (a[0] + round(dx * i / steps), a[1] + round(dy * i / steps))
        i, slow = 0, careful
        limit = min(steps, MAX_CAREFUL_STEPS) if careful else steps
        while i < limit:
            step = 1 if slow else max(1, int((clearance(point(i)) - 2 * TENTH) / per_step))
            j = min(limit, i + step)
            if ok(point(j)): i, slow = j, careful
            elif step > 1: slow = True
            else: return point(i)
        return point(i)

    direct = reach(current, target)
    if direct == target: return target
    cos_y = math.cos((current[1] + target[1]) / 2 * TENTH)

    def miss(xy):  # how far from the target, on the sphere
        return ((xy[0] - target[0]) * cos_y) ** 2 + (xy[1] - target[1]) ** 2
    best, corner = direct, None
    for first in ((target[0], current[1]), (current[0], target[1])):  # sideways first, vertically first
        leg = reach(current, first)
        end = reach(leg, target)
        if (miss(end), end != current) < (miss(best), best != current): best, corner = end, (leg if leg not in (current, end) else None)
    return corner or best  # an L-shaped slide: its corner now, the rest on the next pointer update
