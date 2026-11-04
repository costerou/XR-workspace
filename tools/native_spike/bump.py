"""Bumping in the overview: a desktop you move or resize pushes the desktops it runs into out of its way, and they stay
where they were pushed (monitor-collision-bump, 2026-11-03). Pure maths, no I/O.

Design:
  * What collides is what you see: each desktop's footprint (orbit.footprint), the directions from your eye that its
    true placed outline covers (orbit, distance and curvature included; a curved one by its convex hull), widened by
    orbit.CLEARANCE_PX, so neighbours stop a couple of pixels apart (the highlight frame, on one desktop at a time and drawn
    under every picture, needs no room), with the strips its level bar and pill really take above and below it, each
    only as wide as the handle (bump-closer, 2026-11-03): right above each other a pill and a bar both fit, and past
    them desktops come just as close. Two desktops collide when any part of one shares a direction with any part of the
    other (apart: a separating great circle through an edge of either, the spherical form of the separating-axis test,
    part by part; orbit.overlap for footprints too wide for it). Touching is allowed.
  * One desktop is the mover: a pill drag, a corner resize, or a slider or key of its open panel changes one desktop's
    placement (the settings panel's Apply does not bump: it applies exactly as given). It walks the straight path from
    where it is to where it was asked to go (x / y, distance and curvature together) in steps of at most STEP_TENTHS of
    edge travel (longer ones on a path too long for MAX_WALK_STEPS, and bigger strides while its bounding cone is clear
    of every other desktop's), so however far one update takes it, it never jumps through a desktop.
  * Each step pushes. Every desktop the mover now covers is moved straight out of its way by the least turn round you
    (x) or lift (y), in whole tenths of a degree, that clears it: the minimum translation of a physics engine, on the
    sphere. A desktop pushed into another pushes that one the same way, and so on (cascading); one may be pushed more
    than once in a step. A pair that already overlapped before the edit (an Apply, or no room to settle it) is left as it
    is: that pair may not get deeper, so nothing is ever stuck.
  * The layout changing settles (settle): a new overview layout, or a restored setup, can leave desktops overlapping
    with no mover to push them. Desktop by desktop, those to keep first, each one that covers a desktop already gone
    through goes to the nearest clear spot, straight along x or y; the rest stay exactly where they are. Once clear
    nothing moves, so it never jitters. The row itself is no overlap (two desktops both flat in their slots: at spacing
    0 they touch, closer than a drag leaves them), and a desktop with no clear spot anywhere stays where it is.
  * Stable. A push is never longer than PUSH_REACH times the step's own travel (plus PUSH_SLACK), so a desktop is never
    flung across the room; a desktop keeps the way it was last pushed while that costs at most HYSTERESIS tenths more
    than the cheapest (no flip-flopping between sideways and up / down at a corner); and a pushed desktop never springs
    back: it stays where it was pushed until something moves it again. The same input always gives the same result.
  * No room. A push may not take a desktop past straight overhead or underfoot (orbit.Y_LIMIT: the placement bounds,
    where the pointer's reach ends too) or back into the mover, and a cascade may make at most MAX_PUSHES pushes per
    step (more is a loop round you). Then the mover stops at its last step that fitted, as the guard before bumping
    did, and everything pushed so far stays where it got to. An orbit move (a pill drag, the Left/right and Down/up
    keys) then slides along whatever cannot give way, sideways first or up / down first, whichever ends closer; the
    slide's corner is handed back first, so the renderer's straight easing never cuts through the neighbour.
  * Smooth. The pushed desktops move with the mover, step for step, and go to the renderer in the same `placement`
    frame, which eases every desktop alike (spike view.hpp ease_toward; a discrete edit that pushes, or a settle, asks
    for a glide, view.GLIDE_MS: every desktop of the frame slides along one smootherstep curve together, glide_toward),
    so they move out of the way together and never cross on screen.
  * Saved like any move. A pushed desktop is a placement like any other: it is in the placements the run's normal save
    path writes (ViewControl.flush, the debounce, at once on release), so it comes back with the layout.
  * The levelling roll (free_pointer.py's level bar) is part of the footprint: what collides is the desktop as drawn. It
    was ignored until bump-closer as "a few degrees", which is not little on a big desktop: at distance 31, 5.8 degrees
    up, levelling takes four degrees of lean out of two neighbours, so unrolled their top corners met while on screen they were
    100 px apart all the way down. A Pusher takes the rolls as they are when it starts. A roll is not a move: turning or
    levelling a desktop pushes nothing, and a corner it swings over a neighbour is left, like an Apply's overlap, until the
    next move of the two or layout change.
"""
import math

from . import orbit
from .geometry import DEFAULT_PLACEMENT

DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))  # a push: turn round you (x: right, left) or lift (y: up, down)
STEP_TENTHS = 4.0     # the mover's steps: at most this much edge travel (tenths of a degree) while anything is near
MAX_WALK_STEPS = 160  # ...in at most this many steps: a long edit (a slider click across the row) takes longer ones
PUSH_REACH = 3.0      # a push is at most this many times the step's own travel, plus PUSH_SLACK tenths: never a fling
PUSH_SLACK = 4
HYSTERESIS = 2        # tenths of a degree: a desktop keeps the way it was last pushed while it costs at most this more
MAX_PUSHES = 12       # pushes per step (a cascade through every desktop a few times over); more is a loop: no room
POLE_COS = 0.25       # near a pole a turn round you moves a desktop little: x pushes reach at most 1 / POLE_COS further
TOWARD = -0.25        # a push direction closing on the pusher by more than this (normalized) is never tried
SETTLE_STEP = 15      # tenths of a degree: how coarsely settle looks for a clear spot (no desktop is under ~6 degrees wide)
_EPS = 1e-12


def turn(a0, x, y):
    """orbit.rotation(a0, x, y), Az(a0 + x) . El(y) . Az(-a0), in closed form (it runs for every footprint tried)."""
    a, b, e = a0 + x * orbit.TENTH, -a0, y * orbit.TENTH
    ca, sa, cb, sb, ce, se = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(e), math.sin(e)
    return ((ca * cb - sa * ce * sb, -sa * se, -ca * sb - sa * ce * cb),
            (-se * sb, ce, -se * cb),
            (sa * cb + ca * ce * sb, ca * se, -sa * sb + ca * ce * cb))


def apart(a, b):
    """True if footprints `a` and `b` share no direction (they may touch): orbit.overlap(a, b) is 0. Their cones first;
    then, for each part of one against each part of the other (a desktop and its handles' strips), a separating great
    circle through an edge of either (two convex spherical polygons in one hemisphere are apart exactly when one exists);
    footprints too wide for that are judged by orbit.overlap, as the guard did."""
    theta = orbit._angle(a.centre, b.centre)
    if theta >= a.radius + b.radius: return True
    if a.vertices is None or b.vertices is None or theta + a.radius + b.radius >= math.pi:
        return orbit.overlap(a, b) <= orbit.AREA_EPS
    return all(_separated(p, q) for p in (a,) + a.handles for q in (b,) + b.handles)


def _separated(a, b):
    """True if a great circle through an edge of either convex polygon has the other on its far side."""
    for p, q in ((a, b), (b, a)):
        for n in p.normals:
            if all(n[0] * v[0] + n[1] * v[1] + n[2] * v[2] <= _EPS for v in q.vertices): return True
    return False


def _least(ok, most):
    """The least whole t in 1..most with ok(t), ok taken as False up to some t and True from there; None if ok(most) is
    False. Doubling, then halving: a few calls for a short push."""
    most = int(most)
    if most < 1: return None
    lo, t = 0, 1
    while not ok(t):
        if t >= most: return None
        lo, t = t, min(2 * t, most)
    while t - lo > 1:
        mid = (lo + t) // 2
        if ok(mid): t = mid
        else: lo = mid
    return t


class Pusher:
    """Bumping for one edit of desktop `index` in the overview geometry `g` (free_pointer.parse_overview_geometry): a
    whole drag, press to release, or one key, slider or panel change. It remembers which way each desktop was last
    pushed (HYSTERESIS), so a drag keeps one Pusher to its end. After each move(): `reached`, the mover's placement it
    got to (x unwrapped, in the frame of the move's `start`); `pushed`, the desktops that move pushed; `blocked`, True
    if it stopped short of its target (no room). `rolls`: each desktop's levelling roll, 1/100 degree (none: all level)."""
    def __init__(self, g, index, rolls=None):
        self.g, self.index, self.rolls = g, index, list(rolls or ())
        self.memory = {}   # desktop -> index into DIRECTIONS it was last pushed along
        self.reached, self.pushed, self.blocked = None, (), False
        self.steps = 0     # diagnostics: walking steps taken by the last move()
        self._shapes, self._prints = {}, {}
        self._a0 = [orbit.slot_azimuth(g, t) for t in g['tiles']] if g is not None else []

    # --- footprints -------------------------------------------------------------------------------------------------
    def _shape(self, i, distance, curvature):
        """Desktop i's footprint un-orbited (x = y = 0)."""
        key = (i, distance, curvature)
        f = self._shapes.get(key)
        if f is None:
            if len(self._shapes) > 256: self._shapes.clear()
            roll = self.rolls[i] if i < len(self.rolls) else 0
            f = self._shapes[key] = orbit.footprint(self.g, self.g['tiles'][i], {'x': 0, 'y': 0, 'distance': distance,
                                                                                   'curvature': curvature, 'roll': roll})
        return f

    def _print(self, i, s):
        """Desktop i's footprint at state s = (x, y, distance, curvature)."""
        key = (i, s[0], s[1], s[2], s[3])
        f = self._prints.get(key)
        if f is None:
            if len(self._prints) > 4096: self._prints.clear()
            base = self._shape(i, s[2], s[3])
            f = self._prints[key] = base.rotated(turn(self._a0[i], s[0], s[1]) if s[0] or s[1] else None)
        return f

    def _hits(self, a, sa, b, sb):
        """True if desktops a and b at states sa, sb overlap more than they did when this move began (limit)."""
        fa, fb = self._print(a, sa), self._print(b, sb)
        limit = self._limit.get((a, b) if a < b else (b, a))
        if limit is not None: return orbit.overlap(fa, fb) > limit + orbit.AREA_EPS
        return not apart(fa, fb)

    # --- one move -----------------------------------------------------------------------------------------------------
    def move(self, placements, target, start=None):
        """Every desktop's wire placement (x wrapped) after the mover goes toward `target` (a placement, or just the keys
        that change; its x may be unwrapped), each desktop it runs into pushed out of its way. `start`: where the mover
        is now, x unwrapped (a drag's own; any keys missing come from `placements`); without it the mover starts at its
        placement in `placements` and goes the short way round to `target`'s x. Without a geometry with one slot per
        desktop (none yet, or one stale after a desktop change), or alone, the target is taken as asked."""
        m, g = self.index, self.g
        cur = {**placements[m], **(start or {})}
        aim = {**cur, **target}
        if start is None: aim['x'] = cur['x'] + orbit._diff(aim['x'], cur['x'])
        n = len(placements)
        self.pushed, self.blocked, self.steps = (), False, 0
        if g is None or len(g['tiles']) != n or n < 2 or not 0 <= m < n:
            self.reached = aim
            return [_wire(aim) if i == m else dict(p) for i, p in enumerate(placements)]
        first = [(p['x'], p['y'], p['distance'], p['curvature']) for p in placements]
        a = (cur['x'], cur['y'], cur['distance'], cur['curvature'])
        first[m] = a
        self._limit = {}
        for i in range(n):  # pairs overlapping already (a layout change, the settings panel): they may not get deeper
            for j in range(i + 1, n):
                fi, fj = self._print(i, first[i]), self._print(j, first[j])
                if not apart(fi, fj): self._limit[(i, j)] = max(orbit.overlap(fi, fj), orbit.AREA_EPS)
        ways = [aim['x']]
        if start is None and abs(aim['x'] - cur['x']) == orbit.X_LIMIT:  # half a turn: both ways are as short
            ways.append(cur['x'] - (aim['x'] - cur['x']))
        best = None
        for x in ways:  # the way that gets there, then pushes the others least
            b = (x, aim['y'], aim['distance'], aim['curvature'])
            memory, self.blocked = dict(self.memory), False
            state = self._go(first, a, b)
            score = (self.blocked, sum(abs(s[0] - f[0]) + abs(s[1] - f[1]) for k, (s, f) in enumerate(zip(state, first)) if k != m))
            if best is None or score < best[0]: best = (score, state, b, self.memory, self.blocked)
            self.memory = memory
        _, state, b, self.memory, self.blocked = best
        self.pushed = tuple(k for k in range(n) if k != m and state[k] != first[k])
        self.reached = {**cur, 'x': state[m][0], 'y': state[m][1], 'distance': state[m][2], 'curvature': state[m][3]}
        out = [_wire({**placements[i], 'x': s[0], 'y': s[1], 'distance': s[2], 'curvature': s[3]}) for i, s in enumerate(state)]
        if start is None and state[m] == b and 'x' in target:
            out[m]['x'] = orbit.wrap(int(target['x']))  # got there: the x it was given (+1800 and -1800 are one place)
        return out

    def _go(self, first, a, b):
        """The state after the mover goes from a to b with the others at `first`: walked, and slid along what cannot
        give way if an orbit move runs out of room."""
        state, done = self._walk(list(first), a, b)
        if not done and a[2:] == b[2:]: state = self._slide(first, a, b, state)
        return state

    def _slide(self, first, a, b, direct):
        """The mover's orbit move from a to b ran out of room at `direct`: try the two L-shaped paths (sideways first, up /
        down first) and keep whichever ends closer to b. A slide's corner comes back first (the rest next update)."""
        m = self.index
        cos_y = math.cos((a[1] + b[1]) / 2 * orbit.TENTH)

        def miss(s):
            return ((s[m][0] - b[0]) * cos_y) ** 2 + (s[m][1] - b[1]) ** 2
        best, corner = direct, None
        for leg in ((b[0], a[1]) + a[2:], (a[0], b[1]) + a[2:]):
            s1, _ = self._walk(list(first), a, leg)
            s2, _ = self._walk(list(s1), s1[m], b)
            if (miss(s2), s2[m] != a) < (miss(best), best[m] != a):
                best, corner = s2, (s1 if s1[m] not in (a, s2[m]) else None)
        out = corner or best
        self.blocked = out[m] != b  # (a corner handed back first is not there yet either)
        return out

    def _walk(self, state, a, b):
        """Walk the mover from a to b (each (x, y, distance, curvature)) with the others in `state`, pushing as it goes.
        Returns (the state at its last step that fitted, True if it got to b). The mover's placement in that state is
        whole numbers (a resize stopped part way rounds back toward a, where it still fits)."""
        m = self.index
        ra, rb = self._shape(m, a[2], a[3]).radius, self._shape(m, b[2], b[3]).radius
        travel = abs(b[0] - a[0]) + abs(b[1] - a[1]) + abs(rb - ra) / orbit.TENTH
        if a == b: return state, True
        steps = max(1, min(MAX_WALK_STEPS, math.ceil(travel / STEP_TENTHS)))
        per = travel / steps
        orbit_only = a[2:] == b[2:]  # its shape stays: a stride's bound below is exact
        whole = state  # the last state that fitted with the mover in whole numbers
        i, stride = 0, orbit_only and per > 0
        while i < steps:
            j = i + 1
            if stride:  # clear of every other desktop's cone: stride as far as it surely stays clear
                clear = self._clearance(state)
                if clear > 0: j = min(steps, i + max(1, int(clear / orbit.TENTH / per) - 1))
            q = _between(a, b, j / steps)
            trial = list(state)
            trial[m] = q
            self.steps += 1
            if not self._settle(trial, self._travel(state[m], q)):
                if j > i + 1:  # (a stride never meets anything; should one, take a single step instead)
                    stride = False
                    continue
                self.blocked = True
                if whole is not state and q[2:] != a[2:]:  # stopped part way through a resize: whole numbers, toward a
                    back = list(state)
                    back[m] = state[m][:2] + tuple(_toward(v, w) for v, w in zip(state[m][2:], a[2:]))
                    if self._settle(back, self._travel(state[m], back[m])): return back, False
                return whole, False
            state, i, stride = trial, j, orbit_only and per > 0
            if all(float(v).is_integer() for v in q): whole = state
        return state, True

    def _travel(self, a, b):
        """How far (tenths of a degree) the mover's edges travel from a to b at most: its turn and lift, and how much its
        outline grows or shrinks (its cone's radius)."""
        m = self.index
        grow = abs(self._shape(m, b[2], b[3]).radius - self._shape(m, a[2], a[3]).radius) / orbit.TENTH
        return abs(b[0] - a[0]) + abs(b[1] - a[1]) + grow

    def _clearance(self, state):
        """Radians the mover can still travel before its bounding cone meets any other desktop's (negative: they meet)."""
        m = self.index
        fm = self._print(m, state[m])
        return min(orbit._angle(fm.centre, self._print(k, state[k]).centre) - fm.radius - self._print(k, state[k]).radius
                   for k in range(len(state)) if k != m)

    def _settle(self, state, travel):
        """Push every desktop the mover (state[m], just moved) runs into out of its way, then every desktop those run
        into, and so on. `state` is changed in place. False: no room (state then half done: throw it away)."""
        m = self.index
        reach = PUSH_REACH * travel + PUSH_SLACK
        queue, pushes = [m], 0
        while queue:
            p = queue.pop(0)
            for k in range(len(state)):
                if k in (p, m) or not self._hits(p, state[p], k, state[k]): continue
                pushes += 1
                if pushes > MAX_PUSHES: return False
                to = self._push(state, k, p, reach)
                if to is None: return False
                state[k] = to
                if k not in queue: queue.append(k)
        return True

    def _push(self, state, k, p, reach):
        """Where desktop k goes when desktop p runs into it: the cheapest of the four DIRECTIONS (least travel, whole
        tenths, at most `reach` tenths of travel) that clears it from p without running it into the mover or past a pole.
        The way it was last pushed wins unless another is more than HYSTERESIS cheaper. None if none clears."""
        m, sk = self.index, state[k]
        fk = self._print(k, sk)
        cos_el = max(math.sqrt(max(0.0, 1 - fk.centre[1] ** 2)), POLE_COS)  # a turn of x moves it this much per tenth

        def ok(d, t):
            dx, dy = DIRECTIONS[d]
            to = (sk[0] + dx * t, sk[1] + dy * t) + sk[2:]
            if abs(to[1]) > orbit.Y_LIMIT: return False
            return not self._hits(k, to, p, state[p]) and (p == m or not self._hits(k, to, m, state[m]))

        remembered = self.memory.get(k)
        best = None  # (travel, direction, t): travel in tenths of a degree of the desktop's own motion
        for d in self._order(state, k, p, remembered):
            per = cos_el if DIRECTIONS[d][0] else 1.0
            ceiling = reach  # a later direction is only tried as far as it could still win
            if best is not None: ceiling = min(ceiling, best[0] - (HYSTERESIS if best[1] == remembered else 0))
            t = _least(lambda t, d=d: ok(d, t), math.floor(ceiling / per + 1e-9))
            if t is None: continue
            if best is None or t * per < best[0] - (HYSTERESIS if best[1] == remembered else 0):
                best = (t * per, d, t)  # ties go to the one tried first: the remembered one, else the most directly away
        if best is None: return None
        self.memory[k] = best[1]
        dx, dy = DIRECTIONS[best[1]]
        return (sk[0] + dx * best[2], sk[1] + dy * best[2]) + sk[2:]

    def _order(self, state, k, p, remembered):
        """The push directions worth trying for k pushed by p: the remembered one first, then the rest from the most
        directly away from p; never one that closes on p (TOWARD)."""
        sk, cp = state[k], self._print(p, state[p]).centre
        ck = self._print(k, sk).centre
        sin_t = math.sqrt(max(_EPS, 1 - min(1.0, orbit._dot(ck, cp)) ** 2))
        away = []
        for dx, dy in DIRECTIONS[::2]:  # +x and +y; -x and -y go the other way
            c = self._print(k, (sk[0] + dx, sk[1] + dy) + sk[2:]).centre
            step = (c[0] - ck[0], c[1] - ck[1], c[2] - ck[2])
            size = math.sqrt(orbit._dot(step, step))
            a = -orbit._dot(step, cp) / (size * sin_t) if size > 0 else 0.0
            away += [a, -a]
        return sorted((d for d in range(len(DIRECTIONS)) if away[d] >= TOWARD), key=lambda d: (d != remembered, -away[d], d))

    def _clear_spot(self, state, k, others):
        """The nearest place for desktop k, straight along x or y (the turn at most half way round, the lift to the poles),
        that covers none of the desktops `others`. None if there is none."""
        sk = state[k]
        cos_el = max(math.sqrt(max(0.0, 1 - self._print(k, sk).centre[1] ** 2)), POLE_COS)

        def clear(d, t):
            dx, dy = DIRECTIONS[d]
            to = (sk[0] + dx * t, sk[1] + dy * t) + sk[2:]
            return abs(to[1]) <= orbit.Y_LIMIT and not any(self._hits(k, to, i, state[i]) for i in others)
        best = None  # (travel, direction, t)
        for d, (dx, _) in enumerate(DIRECTIONS):
            per = cos_el if dx else 1.0
            lo, t, most = 0, SETTLE_STEP, orbit.X_LIMIT if dx else 2 * orbit.Y_LIMIT
            while t <= most and (best is None or lo * per < best[0]):
                if clear(d, t):
                    t = lo + _least(lambda s, lo=lo, d=d: clear(d, lo + s), t - lo)
                    if best is None or t * per < best[0]: best = (t * per, d, t)
                    break
                lo, t = t, t + SETTLE_STEP
        if best is None: return None
        dx, dy = DIRECTIONS[best[1]]
        return (sk[0] + dx * best[2], sk[1] + dy * best[2]) + sk[2:]


def settle(g, placements, first=(), rolls=None):
    """(placements, the desktops moved) with no two desktops overlapping, for a layout that changed under them: module
    docstring, the layout changing settles. `first`: the desktops to keep where they are ahead of the others (the rest in
    order). `rolls`: as for a Pusher. Without a geometry with one slot per desktop, or alone: unchanged."""
    n = len(placements)
    out = [dict(p) for p in placements]
    if g is None or len(g['tiles']) != n or n < 2: return out, ()
    pusher = Pusher(g, 0, rolls)
    pusher._limit = {}
    state = [(p['x'], p['y'], p['distance'], p['curvature']) for p in placements]
    flat = [p == DEFAULT_PLACEMENT for p in placements]
    placed, moved = [], []
    for k in dict.fromkeys(tuple(i for i in first if 0 <= i < n) + tuple(range(n))):
        others = [i for i in placed if not (flat[i] and flat[k])]
        if any(pusher._hits(k, state[k], i, state[i]) for i in others):
            to = pusher._clear_spot(state, k, others)
            if to is not None:
                state[k], flat[k] = to, False
                moved.append(k)
        placed.append(k)
    for k in moved: out[k] = _wire({**out[k], 'x': state[k][0], 'y': state[k][1]})
    return out, tuple(sorted(moved))


def _between(a, b, t):
    """The mover's placement a fraction t of the way from a to b: x and y in whole tenths, curvature exact, distance so
    that the apparent size (100 / distance) changes evenly (a resize near ten times its size would otherwise leap at the
    end); fractional part way, b's own at the end."""
    if t >= 1: return b
    d = a[2] if a[2] == b[2] else 1 / (1 / a[2] + (1 / b[2] - 1 / a[2]) * t)
    return (a[0] + round((b[0] - a[0]) * t), a[1] + round((b[1] - a[1]) * t), d,
            a[3] if a[3] == b[3] else a[3] + (b[3] - a[3]) * t)


def _toward(value, start):
    """`value` rounded to a whole number on the side of `start` (a resize stopped part way keeps what still fits)."""
    return int(math.floor(value) if start < value else math.ceil(value)) if not float(value).is_integer() else int(value)


def _wire(p):
    """A placement for the wire: whole numbers, x wrapped round (orbit.wrap)."""
    return {**p, 'x': orbit.wrap(int(p['x'])), 'y': int(p['y']), 'distance': int(p['distance']), 'curvature': int(p['curvature'])}


def push(g, index, placements, target, rolls=None):
    """One edit with bumping (a key, a slider, an Apply): every desktop's placement after desktop `index` goes to
    `target` (x the short way round), and the Pusher that did it (its `pushed`, `blocked`, `reached`)."""
    pusher = Pusher(g, index, rolls)
    return pusher.move(placements, target), pusher
