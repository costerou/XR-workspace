//! Bumping in the overview: a desktop you move or resize pushes the desktops it runs into out of its way, and they stay
//! where they were pushed. A port of `tools/native_spike/bump.py` (its module docstring has the design); pure maths.
//!
//! The Python's mover states are `(x, y, distance, curvature)` tuples whose distance and curvature turn into floats part
//! way through a resize: [`State`] keeps each as a [`Num`], an int or a float that compares by value like Python's. The
//! Python's caches (`_shapes`, `_prints`) give the same footprint whether or not they hit, so here they only save time.
//! `rust/corpus/pointer/math.json` holds the Python's answers; `pointer_math_corpus_tests` replays them.

use super::orbit::{self, AREA_EPS, Footprint, Geometry, Mat3, Pose, TENTH, Y_LIMIT};
use super::python::{py_max, py_min, py_round, py_square};
use crate::geometry::{DEFAULT_PLACEMENT, Placement, X_LIMIT, wrap};
use std::cell::RefCell;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::f64::consts::PI;
use std::sync::Arc;

/// A push: turn round you (x: right, left) or lift (y: up, down).
pub const DIRECTIONS: [(i64, i64); 4] = [(1, 0), (-1, 0), (0, 1), (0, -1)];
/// The mover's steps: at most this much edge travel (tenths of a degree) while anything is near...
pub const STEP_TENTHS: f64 = 4.0;
/// ...in at most this many steps: a long edit (a slider click across the row) takes longer ones.
pub const MAX_WALK_STEPS: i64 = 160;
/// A push is at most this many times the step's own travel, plus PUSH_SLACK tenths: never a fling.
pub const PUSH_REACH: f64 = 3.0;
pub const PUSH_SLACK: i64 = 4;
/// Tenths of a degree: a desktop keeps the way it was last pushed while it costs at most this more.
pub const HYSTERESIS: i64 = 2;
/// Pushes per step (a cascade through every desktop a few times over); more is a loop: no room.
pub const MAX_PUSHES: usize = 12;
/// Near a pole a turn round you moves a desktop little: x pushes reach at most 1 / POLE_COS further.
pub const POLE_COS: f64 = 0.25;
/// A push direction closing on the pusher by more than this (normalized) is never tried.
pub const TOWARD: f64 = -0.25;
/// Tenths of a degree: how coarsely settle looks for a clear spot (no desktop is under ~6 degrees wide).
pub const SETTLE_STEP: i64 = 15;
const EPS: f64 = 1e-12;

/// A Python number: an int, or a float. Equal (and hashed, in the caches) by value, as Python's are.
#[derive(Clone, Copy, Debug)]
pub enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    pub fn get(self) -> f64 {
        match self {
            Num::Int(v) => v as f64,
            Num::Float(v) => v,
        }
    }

    /// `float(v).is_integer()`.
    pub fn is_integral(self) -> bool {
        match self {
            Num::Int(_) => true,
            Num::Float(v) => v.fract() == 0.0,
        }
    }

    /// `int(v)`: toward zero.
    pub fn trunc(self) -> i64 {
        match self {
            Num::Int(v) => v,
            Num::Float(v) => v as i64,
        }
    }

    /// A cache key: the value's bits, 0.0 and -0.0 as one.
    fn key(self) -> u64 {
        (self.get() + 0.0).to_bits()
    }
}

impl PartialEq for Num {
    fn eq(&self, other: &Num) -> bool {
        match (*self, *other) {
            (Num::Int(a), Num::Int(b)) => a == b,
            (a, b) => a.get() == b.get(),
        }
    }
}

/// The mover's (or any desktop's) `(x, y, distance, curvature)`: x and y whole tenths (x may be unwrapped), distance
/// and curvature whole until a resize stops part way.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct State {
    pub x: i64,
    pub y: i64,
    pub distance: Num,
    pub curvature: Num,
}

impl From<Placement> for State {
    fn from(p: Placement) -> State {
        State { x: p.x, y: p.y, distance: Num::Int(p.distance), curvature: Num::Int(p.curvature) }
    }
}

impl State {
    /// `{**self, **patch}`.
    pub fn patched(self, patch: &Patch) -> State {
        State {
            x: patch.x.unwrap_or(self.x),
            y: patch.y.unwrap_or(self.y),
            distance: patch.distance.map_or(self.distance, Num::Int),
            curvature: patch.curvature.map_or(self.curvature, Num::Int),
        }
    }

    /// `s[2:] == t[2:]`: the same distance and curvature (the same shape).
    pub fn same_shape(&self, other: &State) -> bool {
        self.distance == other.distance && self.curvature == other.curvature
    }

    fn is_integral(&self) -> bool {
        self.distance.is_integral() && self.curvature.is_integral()
    }

    fn moved(self, dx: i64, dy: i64) -> State {
        State { x: self.x + dx, y: self.y + dy, ..self }
    }
}

/// A placement's keys that change (a move's `target`, or a drag's `start`): None keeps the key as it is.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Patch {
    pub x: Option<i64>,
    pub y: Option<i64>,
    pub distance: Option<i64>,
    pub curvature: Option<i64>,
}

/// `turn`: orbit.rotation(a0, x, y), Az(a0 + x) . El(y) . Az(-a0), in closed form (it runs for every footprint tried).
pub fn turn(a0: f64, x: f64, y: f64) -> Mat3 {
    let (a, b, e) = (a0 + x * TENTH, -a0, y * TENTH);
    let (ca, sa, cb, sb, ce, se) = (a.cos(), a.sin(), b.cos(), b.sin(), e.cos(), e.sin());
    [
        [ca * cb - sa * ce * sb, -sa * se, -ca * sb - sa * ce * cb],
        [-se * sb, ce, -se * cb],
        [sa * cb + ca * ce * sb, ca * se, -sa * sb + ca * ce * cb],
    ]
}

/// `apart`: true if footprints `a` and `b` share no direction (they may touch): orbit.overlap(a, b) is 0. Their cones
/// first; then, part by part, a separating great circle through an edge of either; footprints too wide for that are
/// judged by orbit.overlap.
pub fn apart(a: &Footprint, b: &Footprint) -> bool {
    let theta = orbit::angle(&a.centre, &b.centre);
    if theta >= a.radius + b.radius {
        return true;
    }
    if a.vertices.is_none() || b.vertices.is_none() || theta + a.radius + b.radius >= PI {
        return orbit::overlap(a, b) <= AREA_EPS;
    }
    a.parts().all(|p| b.parts().all(|q| separated(p, q)))
}

/// `_separated`: true if a great circle through an edge of either convex polygon has the other on its far side.
pub fn separated(a: &Footprint, b: &Footprint) -> bool {
    [(a, b), (b, a)].iter().any(|(p, q)| {
        p.normals.iter().flatten().any(|n| {
            q.vertices.iter().flatten().all(|v| n[0] * v[0] + n[1] * v[1] + n[2] * v[2] <= EPS)
        })
    })
}

/// `_least`: the least whole t in 1..most with ok(t), ok taken as false up to some t and true from there; None if
/// ok(most) is false. Doubling, then halving: a few calls for a short push.
pub fn least(mut ok: impl FnMut(i64) -> bool, most: i64) -> Option<i64> {
    if most < 1 {
        return None;
    }
    let (mut lo, mut t) = (0, 1);
    while !ok(t) {
        if t >= most {
            return None;
        }
        (lo, t) = (t, (2 * t).min(most));
    }
    while t - lo > 1 {
        let mid = (lo + t).div_euclid(2);
        if ok(mid) {
            t = mid;
        } else {
            lo = mid;
        }
    }
    Some(t)
}

/// A footprint that two callers may hold.
type Shared = Arc<Footprint>;
/// A desktop's footprint at a placement: (desktop, x, y, distance bits, curvature bits).
type PrintKey = (usize, i64, i64, u64, u64);

/// `Pusher`: bumping for one edit of desktop `index` in the overview geometry `g`: a whole drag, press to release, or
/// one key, slider or panel change. It remembers which way each desktop was last pushed (HYSTERESIS), so a drag keeps
/// one Pusher to its end. After each [`Pusher::move_to`]: `reached`, the mover's placement it got to (x unwrapped, in
/// the frame of the move's `start`); `pushed`, the desktops that move pushed; `blocked`, true if it stopped short of
/// its target (no room). `rolls`: each desktop's levelling roll, 1/100 degree (none: all level).
#[derive(Clone, Debug)]
pub struct Pusher {
    pub g: Option<Geometry>,
    pub index: usize,
    pub rolls: Vec<f64>,
    /// Desktop -> index into DIRECTIONS it was last pushed along.
    pub memory: BTreeMap<usize, usize>,
    pub reached: Option<State>,
    pub pushed: Vec<usize>,
    pub blocked: bool,
    /// Diagnostics: walking steps taken by the last move.
    pub steps: u64,
    limit: HashMap<(usize, usize), f64>,
    a0: Vec<f64>,
    shapes: RefCell<HashMap<(usize, u64, u64), Shared>>,
    prints: RefCell<HashMap<PrintKey, Shared>>,
}

impl Pusher {
    pub fn new(g: Option<Geometry>, index: usize, rolls: Option<Vec<f64>>) -> Pusher {
        let a0 = g.as_ref().map_or_else(Vec::new, |g| g.tiles.iter().map(|t| orbit::slot_azimuth(g, t)).collect());
        Pusher {
            g,
            index,
            rolls: rolls.unwrap_or_default(),
            memory: BTreeMap::new(),
            reached: None,
            pushed: Vec::new(),
            blocked: false,
            steps: 0,
            limit: HashMap::new(),
            a0,
            shapes: RefCell::default(),
            prints: RefCell::default(),
        }
    }

    // --- footprints -----------------------------------------------------------------------------------------------

    /// `_shape`: desktop i's footprint un-orbited (x = y = 0).
    pub fn shape(&self, i: usize, distance: Num, curvature: Num) -> Shared {
        let key = (i, distance.key(), curvature.key());
        if let Some(f) = self.shapes.borrow().get(&key) {
            return f.clone();
        }
        let g = self.g.as_ref().expect("a Pusher with footprints has a geometry");
        let roll = self.rolls.get(i).copied().unwrap_or(0.0);
        let pose = Pose { x: 0.0, y: 0.0, distance: distance.get(), curvature: curvature.get(), roll };
        let f = Arc::new(orbit::footprint(g, &g.tiles[i], &pose));
        self.shapes.borrow_mut().insert(key, f.clone());
        f
    }

    /// `_print`: desktop i's footprint at state s.
    pub fn print(&self, i: usize, s: State) -> Shared {
        let key = (i, s.x, s.y, s.distance.key(), s.curvature.key());
        if let Some(f) = self.prints.borrow().get(&key) {
            return f.clone();
        }
        let base = self.shape(i, s.distance, s.curvature);
        let f = if s.x != 0 || s.y != 0 {
            Arc::new(base.rotated(Some(&turn(self.a0[i], s.x as f64, s.y as f64))))
        } else {
            base
        };
        self.prints.borrow_mut().insert(key, f.clone());
        f
    }

    /// `_hits`: true if desktops a and b at states sa, sb overlap more than they did when this move began (limit).
    pub fn hits(&self, a: usize, sa: State, b: usize, sb: State) -> bool {
        let (fa, fb) = (self.print(a, sa), self.print(b, sb));
        match self.limit.get(&if a < b { (a, b) } else { (b, a) }) {
            Some(limit) => orbit::overlap(&fa, &fb) > limit + AREA_EPS,
            None => !apart(&fa, &fb),
        }
    }

    // --- one move -------------------------------------------------------------------------------------------------

    /// `move`: every desktop's wire placement (x wrapped) after the mover goes toward `target` (just the keys that
    /// change; its x may be unwrapped), each desktop it runs into pushed out of its way. `start`: where the mover is
    /// now, x unwrapped (any keys missing come from `placements`); without it the mover starts at its placement and
    /// goes the short way round to `target`'s x. Without a geometry with one slot per desktop, or alone, the target is
    /// taken as asked.
    pub fn move_to(&mut self, placements: &[Placement], target: &Patch, start: Option<&Patch>) -> Vec<Placement> {
        let m = self.index;
        let cur = State::from(placements[m]).patched(&start.copied().unwrap_or_default());
        let mut aim = cur.patched(target);
        if start.is_none() {
            aim.x = cur.x + orbit::diff(aim.x, cur.x);
        }
        let n = placements.len();
        (self.pushed, self.blocked, self.steps) = (Vec::new(), false, 0);
        if !self.g.as_ref().is_some_and(|g| g.tiles.len() == n) || n < 2 || m >= n {
            self.reached = Some(aim);
            return placements.iter().enumerate().map(|(i, p)| if i == m { wire(&aim) } else { *p }).collect();
        }
        let mut first: Vec<State> = placements.iter().map(|&p| State::from(p)).collect();
        let a = cur;
        first[m] = a;
        self.limit.clear();
        for i in 0..n {
            // pairs overlapping already (a layout change, the settings panel): they may not get deeper
            for j in i + 1..n {
                let (fi, fj) = (self.print(i, first[i]), self.print(j, first[j]));
                if !apart(&fi, &fj) {
                    self.limit.insert((i, j), py_max(orbit::overlap(&fi, &fj), AREA_EPS));
                }
            }
        }
        let mut ways = vec![aim.x];
        if start.is_none() && (aim.x - cur.x).abs() == X_LIMIT {
            // half a turn: both ways are as short
            ways.push(cur.x - (aim.x - cur.x));
        }
        type Best = ((bool, i64), Vec<State>, State, BTreeMap<usize, usize>, bool);
        let mut best: Option<Best> = None;
        for x in ways {
            // the way that gets there, then pushes the others least
            let b = State { x, ..aim };
            let memory = self.memory.clone();
            self.blocked = false;
            let state = self.go(&first, a, b);
            let moved: i64 = (state.iter().zip(&first).enumerate())
                .filter(|(k, _)| *k != m)
                .map(|(_, (s, f))| (s.x - f.x).abs() + (s.y - f.y).abs())
                .sum();
            let score = (self.blocked, moved);
            if best.as_ref().is_none_or(|best| score < best.0) {
                best = Some((score, state, b, std::mem::replace(&mut self.memory, memory), self.blocked));
            } else {
                self.memory = memory;
            }
        }
        let (_, state, b, memory, blocked) = best.expect("at least one way");
        (self.memory, self.blocked) = (memory, blocked);
        self.pushed = (0..n).filter(|&k| k != m && state[k] != first[k]).collect();
        self.reached = Some(state[m]);
        let mut out: Vec<Placement> = state.iter().map(wire).collect();
        if let (None, true, Some(x)) = (start, state[m] == b, target.x) {
            out[m].x = wrap(x); // got there: the x it was given (+1800 and -1800 are one place)
        }
        out
    }

    /// `_go`: the state after the mover goes from a to b with the others at `first`: walked, and slid along what
    /// cannot give way if an orbit move runs out of room.
    pub fn go(&mut self, first: &[State], a: State, b: State) -> Vec<State> {
        let (state, done) = self.walk(first.to_vec(), a, b);
        if !done && a.same_shape(&b) { self.slide(first, a, b, state) } else { state }
    }

    /// `_slide`: the mover's orbit move from a to b ran out of room at `direct`: try the two L-shaped paths (sideways
    /// first, up / down first) and keep whichever ends closer to b. A slide's corner comes back first.
    pub fn slide(&mut self, first: &[State], a: State, b: State, direct: Vec<State>) -> Vec<State> {
        let m = self.index;
        let cos_y = ((a.y + b.y) as f64 / 2.0 * TENTH).cos();
        let miss = |s: &[State]| py_square((s[m].x - b.x) as f64 * cos_y) + ((s[m].y - b.y).pow(2)) as f64;
        let (mut best, mut corner) = (direct, None);
        for leg in [State { x: b.x, y: a.y, ..a }, State { x: a.x, y: b.y, ..a }] {
            let (s1, _) = self.walk(first.to_vec(), a, leg);
            let (s2, _) = self.walk(s1.clone(), s1[m], b);
            let (new, old) = (miss(&s2), miss(&best));
            let closer = if new != old { new < old } else { s2[m] == a && best[m] != a };
            if closer {
                corner = if s1[m] != a && s1[m] != s2[m] { Some(s1) } else { None };
                best = s2;
            }
        }
        let out = corner.unwrap_or(best);
        self.blocked = out[m] != b; // (a corner handed back first is not there yet either)
        out
    }

    /// `_walk`: walk the mover from a to b with the others in `state`, pushing as it goes. Returns (the state at its
    /// last step that fitted, true if it got to b). The mover's placement in that state is whole numbers.
    pub fn walk(&mut self, mut state: Vec<State>, a: State, b: State) -> (Vec<State>, bool) {
        let m = self.index;
        let (ra, rb) = (self.shape(m, a.distance, a.curvature).radius, self.shape(m, b.distance, b.curvature).radius);
        let travel = ((b.x - a.x).abs() + (b.y - a.y).abs()) as f64 + (rb - ra).abs() / TENTH;
        if a == b {
            return (state, true);
        }
        let steps = ((travel / STEP_TENTHS).ceil() as i64).clamp(1, MAX_WALK_STEPS);
        let per = travel / steps as f64;
        let orbit_only = a.same_shape(&b); // its shape stays: a stride's bound below is exact
        // The last state that fitted with the mover in whole numbers; None: `state` itself (the Python's identity).
        let mut whole: Option<Vec<State>> = None;
        let (mut i, mut stride) = (0, orbit_only && per > 0.0);
        while i < steps {
            let mut j = i + 1;
            if stride {
                // clear of every other desktop's cone: stride as far as it surely stays clear
                let clear = self.clearance(&state);
                if clear > 0.0 {
                    j = steps.min(i + 1.max((clear / TENTH / per) as i64 - 1));
                }
            }
            let q = between(a, b, j as f64 / steps as f64);
            let mut trial = state.clone();
            trial[m] = q;
            self.steps += 1;
            let travel = self.travel(state[m], q);
            if !self.settle(&mut trial, travel) {
                if j > i + 1 {
                    // (a stride never meets anything; should one, take a single step instead)
                    stride = false;
                    continue;
                }
                self.blocked = true;
                if whole.is_some() && !q.same_shape(&a) {
                    // stopped part way through a resize: whole numbers, toward a
                    let mut back = state.clone();
                    let s = state[m];
                    back[m] = State {
                        distance: Num::Int(toward(s.distance, a.distance)),
                        curvature: Num::Int(toward(s.curvature, a.curvature)),
                        ..s
                    };
                    let travel = self.travel(s, back[m]);
                    if self.settle(&mut back, travel) {
                        return (back, false);
                    }
                }
                return (whole.unwrap_or(state), false);
            }
            let old = std::mem::replace(&mut state, trial);
            whole.get_or_insert(old);
            (i, stride) = (j, orbit_only && per > 0.0);
            if q.is_integral() {
                whole = None;
            }
        }
        (state, true)
    }

    /// `_travel`: how far (tenths of a degree) the mover's edges travel from a to b at most: its turn and lift, and
    /// how much its outline grows or shrinks (its cone's radius).
    pub fn travel(&self, a: State, b: State) -> f64 {
        let m = self.index;
        let grow = (self.shape(m, b.distance, b.curvature).radius - self.shape(m, a.distance, a.curvature).radius)
            .abs()
            / TENTH;
        ((b.x - a.x).abs() + (b.y - a.y).abs()) as f64 + grow
    }

    /// `_clearance`: radians the mover can still travel before its bounding cone meets any other desktop's (negative:
    /// they meet).
    pub fn clearance(&self, state: &[State]) -> f64 {
        let m = self.index;
        let fm = self.print(m, state[m]);
        (0..state.len())
            .filter(|&k| k != m)
            .map(|k| {
                let fk = self.print(k, state[k]);
                orbit::angle(&fm.centre, &fk.centre) - fm.radius - fk.radius
            })
            .reduce(py_min)
            .expect("a Pusher walks among two desktops or more")
    }

    /// `_settle`: push every desktop the mover (state[m], just moved) runs into out of its way, then every desktop
    /// those run into, and so on. `state` is changed in place. False: no room (state then half done: throw it away).
    pub fn settle(&mut self, state: &mut [State], travel: f64) -> bool {
        let m = self.index;
        let reach = PUSH_REACH * travel + PUSH_SLACK as f64;
        let (mut queue, mut pushes) = (VecDeque::from([m]), 0);
        while let Some(p) = queue.pop_front() {
            for k in 0..state.len() {
                if k == p || k == m || !self.hits(p, state[p], k, state[k]) {
                    continue;
                }
                pushes += 1;
                if pushes > MAX_PUSHES {
                    return false;
                }
                let Some(to) = self.push(state, k, p, reach) else { return false };
                state[k] = to;
                if !queue.contains(&k) {
                    queue.push_back(k);
                }
            }
        }
        true
    }

    /// `cos_el`: how much a turn of x moves desktop k per tenth (POLE_COS at least).
    fn cos_el(&self, k: usize, sk: State) -> f64 {
        let c = self.print(k, sk).centre;
        py_max(py_max(0.0, 1.0 - py_square(c[1])).sqrt(), POLE_COS)
    }

    /// `_push`: where desktop k goes when desktop p runs into it: the cheapest of the four DIRECTIONS (least travel,
    /// whole tenths, at most `reach` tenths of travel) that clears it from p without running it into the mover or
    /// past a pole. The way it was last pushed wins unless another is more than HYSTERESIS cheaper. None if none
    /// clears.
    pub fn push(&mut self, state: &[State], k: usize, p: usize, reach: f64) -> Option<State> {
        let (m, sk) = (self.index, state[k]);
        let cos_el = self.cos_el(k, sk);
        let ok = |d: usize, t: i64| {
            let (dx, dy) = DIRECTIONS[d];
            let to = sk.moved(dx * t, dy * t);
            if to.y.abs() > Y_LIMIT {
                return false;
            }
            !self.hits(k, to, p, state[p]) && (p == m || !self.hits(k, to, m, state[m]))
        };
        let remembered = self.memory.get(&k).copied();
        let slack = |best: (f64, usize, i64)| if Some(best.1) == remembered { HYSTERESIS as f64 } else { 0.0 };
        // (travel, direction, t): travel in tenths of a degree of the desktop's own motion
        let mut best: Option<(f64, usize, i64)> = None;
        for d in self.order(state, k, p, remembered) {
            let per = if DIRECTIONS[d].0 != 0 { cos_el } else { 1.0 };
            let mut ceiling = reach; // a later direction is only tried as far as it could still win
            if let Some(b) = best {
                ceiling = py_min(ceiling, b.0 - slack(b));
            }
            let Some(t) = least(|t| ok(d, t), (ceiling / per + 1e-9).floor() as i64) else { continue };
            if best.is_none_or(|b| t as f64 * per < b.0 - slack(b)) {
                // ties go to the one tried first: the remembered one, else the most directly away
                best = Some((t as f64 * per, d, t));
            }
        }
        let (_, d, t) = best?;
        self.memory.insert(k, d);
        let (dx, dy) = DIRECTIONS[d];
        Some(sk.moved(dx * t, dy * t))
    }

    /// `_order`: the push directions worth trying for k pushed by p: the remembered one first, then the rest from the
    /// most directly away from p; never one that closes on p (TOWARD).
    pub fn order(&self, state: &[State], k: usize, p: usize, remembered: Option<usize>) -> Vec<usize> {
        let sk = state[k];
        let cp = self.print(p, state[p]).centre;
        let ck = self.print(k, sk).centre;
        let sin_t = py_max(EPS, 1.0 - py_square(py_min(1.0, orbit::dot(&ck, &cp)))).sqrt();
        let mut away = Vec::with_capacity(4);
        for (dx, dy) in [DIRECTIONS[0], DIRECTIONS[2]] {
            // +x and +y; -x and -y go the other way
            let c = self.print(k, sk.moved(dx, dy)).centre;
            let step = [c[0] - ck[0], c[1] - ck[1], c[2] - ck[2]];
            let size = orbit::dot(&step, &step).sqrt();
            let a = if size > 0.0 { -orbit::dot(&step, &cp) / (size * sin_t) } else { 0.0 };
            away.extend([a, -a]);
        }
        let mut out: Vec<usize> = (0..DIRECTIONS.len()).filter(|&d| away[d] >= TOWARD).collect();
        out.sort_by(|&a, &b| {
            (Some(a) != remembered)
                .cmp(&(Some(b) != remembered))
                .then((-away[a]).partial_cmp(&-away[b]).unwrap_or(std::cmp::Ordering::Equal))
                .then(a.cmp(&b))
        });
        out
    }

    /// `_clear_spot`: the nearest place for desktop k, straight along x or y (the turn at most half way round, the
    /// lift to the poles), that covers none of the desktops `others`. None if there is none.
    pub fn clear_spot(&self, state: &[State], k: usize, others: &[usize]) -> Option<State> {
        let sk = state[k];
        let cos_el = self.cos_el(k, sk);
        let clear = |d: usize, t: i64| {
            let (dx, dy) = DIRECTIONS[d];
            let to = sk.moved(dx * t, dy * t);
            to.y.abs() <= Y_LIMIT && !others.iter().any(|&i| self.hits(k, to, i, state[i]))
        };
        let mut best: Option<(f64, usize, i64)> = None; // (travel, direction, t)
        for (d, &(dx, _)) in DIRECTIONS.iter().enumerate() {
            let per = if dx != 0 { cos_el } else { 1.0 };
            let (mut lo, mut t, most) = (0, SETTLE_STEP, if dx != 0 { X_LIMIT } else { 2 * Y_LIMIT });
            while t <= most && best.is_none_or(|b| lo as f64 * per < b.0) {
                if clear(d, t) {
                    t = lo + least(|s| clear(d, lo + s), t - lo).expect("clear at t, so clear by t");
                    if best.is_none_or(|b| t as f64 * per < b.0) {
                        best = Some((t as f64 * per, d, t));
                    }
                    break;
                }
                (lo, t) = (t, t + SETTLE_STEP);
            }
        }
        let (_, d, t) = best?;
        let (dx, dy) = DIRECTIONS[d];
        Some(sk.moved(dx * t, dy * t))
    }
}

/// `settle`: (placements, the desktops moved) with no two desktops overlapping, for a layout that changed under them.
/// `first`: the desktops to keep where they are ahead of the others (the rest in order). `rolls`: as for a Pusher.
/// Without a geometry with one slot per desktop, or alone: unchanged.
pub fn settle(
    g: Option<&Geometry>,
    placements: &[Placement],
    first: &[i64],
    rolls: Option<Vec<f64>>,
) -> (Vec<Placement>, Vec<usize>) {
    let n = placements.len();
    let mut out = placements.to_vec();
    let Some(g) = g.filter(|g| g.tiles.len() == n && n >= 2) else { return (out, Vec::new()) };
    let pusher = Pusher::new(Some(g.clone()), 0, rolls);
    let mut state: Vec<State> = placements.iter().map(|&p| State::from(p)).collect();
    let mut flat: Vec<bool> = placements.iter().map(|p| *p == DEFAULT_PLACEMENT).collect();
    let mut order: Vec<usize> = Vec::new();
    let kept = first.iter().filter_map(|&i| usize::try_from(i).ok().filter(|&i| i < n));
    for k in kept.chain(0..n) {
        if !order.contains(&k) {
            order.push(k);
        }
    }
    let (mut placed, mut moved): (Vec<usize>, Vec<usize>) = (Vec::new(), Vec::new());
    for k in order {
        let others: Vec<usize> = placed.iter().copied().filter(|&i| !(flat[i] && flat[k])).collect();
        if others.iter().any(|&i| pusher.hits(k, state[k], i, state[i]))
            && let Some(to) = pusher.clear_spot(&state, k, &others)
        {
            (state[k], flat[k]) = (to, false);
            moved.push(k);
        }
        placed.push(k);
    }
    for &k in &moved {
        out[k] = wire(&State { x: state[k].x, y: state[k].y, ..State::from(out[k]) });
    }
    moved.sort_unstable();
    (out, moved)
}

/// `_between`: the mover's placement a fraction t of the way from a to b: x and y in whole tenths, curvature exact,
/// distance so that the apparent size (100 / distance) changes evenly; fractional part way, b's own at the end.
pub fn between(a: State, b: State, t: f64) -> State {
    if t >= 1.0 {
        return b;
    }
    let distance = if a.distance == b.distance {
        a.distance
    } else {
        let (ad, bd) = (a.distance.get(), b.distance.get());
        Num::Float(1.0 / (1.0 / ad + (1.0 / bd - 1.0 / ad) * t))
    };
    let curvature = if a.curvature == b.curvature {
        a.curvature
    } else {
        Num::Float(a.curvature.get() + (b.curvature.get() - a.curvature.get()) * t)
    };
    State {
        x: a.x + py_round((b.x - a.x) as f64 * t),
        y: a.y + py_round((b.y - a.y) as f64 * t),
        distance,
        curvature,
    }
}

/// `_toward`: `value` rounded to a whole number on the side of `start` (a resize stopped part way keeps what still
/// fits).
pub fn toward(value: Num, start: Num) -> i64 {
    if value.is_integral() {
        return value.trunc();
    }
    let v = value.get();
    (if start.get() < v { v.floor() } else { v.ceil() }) as i64
}

/// `_wire`: a placement for the wire: whole numbers, x wrapped round (orbit.wrap).
pub fn wire(s: &State) -> Placement {
    Placement { x: wrap(s.x), y: s.y, distance: s.distance.trunc(), curvature: s.curvature.trunc() }
}

/// `push`: one edit with bumping (a key, a slider, an Apply): every desktop's placement after desktop `index` goes to
/// `target` (x the short way round), and the Pusher that did it (its `pushed`, `blocked`, `reached`).
pub fn push(
    g: Option<Geometry>,
    index: usize,
    placements: &[Placement],
    target: &Patch,
    rolls: Option<Vec<f64>>,
) -> (Vec<Placement>, Pusher) {
    let mut pusher = Pusher::new(g, index, rolls);
    (pusher.move_to(placements, target, None), pusher)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn least_finds_the_first_true() {
        for edge in 1..=40 {
            let mut calls = 0;
            let got = least(
                |t| {
                    calls += 1;
                    t >= edge
                },
                40,
            );
            assert_eq!(got, Some(edge));
            assert!(calls <= 12, "{edge}: {calls} calls");
        }
        assert_eq!(least(|_| false, 40), None);
        assert_eq!(least(|_| true, 0), None);
        assert_eq!(least(|t| t >= 3, 1), None);
    }

    #[test]
    fn numbers_compare_by_value() {
        assert_eq!(Num::Int(40), Num::Float(40.0));
        assert_ne!(Num::Int(40), Num::Float(40.5));
        assert_eq!(toward(Num::Float(40.5), Num::Int(30)), 40);
        assert_eq!(toward(Num::Float(40.5), Num::Int(50)), 41);
        assert_eq!(toward(Num::Float(-3.0), Num::Int(50)), -3);
    }
}
