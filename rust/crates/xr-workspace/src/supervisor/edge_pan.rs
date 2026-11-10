//! The edge pan: pushing past an edge of the selected desktop turns the view across to the neighbour that way, the
//! renderer drawing the peek, the gap and the turn from one plan. A port of `tools/native_spike/edge_pan.py`; pure
//! maths, and the `edge_pan` control frame.

use super::neighbors::{self, Direction};
use super::orbit::{self, Footprint, Geometry, Pose, TENTH, Tile, Vec3};
use super::python::{py_max, py_min, py_round, py_sum};
use serde::Serialize;
use std::f64::consts::PI;

/// Logical px of push before anything shows.
pub const START_PX: f64 = 24.0;
/// The peek is at least this long.
pub const PEEK_PX: f64 = 48.0;
/// The view turns at least this share of the way between the two desktops' centres by hand.
pub const MEET_SHARE: f64 = 0.5;
/// A hand this long without moving: the pan ends and the view goes back.
pub const REST_S: f64 = 0.6;
/// The zoom never stays higher than this during a pan, so the dot is inside the view.
pub const MAX_ZOOM: f64 = 0.8;
/// Of the view's width (or height): the most the two desktops may span.
pub const KEEP_IN_VIEW: f64 = 0.96;
const BISECT: usize = 24;
const TO_WIRE: f64 = 10000.0;

unsafe extern "C" {
    /// C99 `remainder`, which CPython's `math.remainder` gives the same answer as (both are exact).
    safe fn remainder(x: f64, y: f64) -> f64;
}

/// `_unit`: v made unit length; None if (nearly) zero.
pub fn unit(v: &Vec3) -> Option<Vec3> {
    let n = orbit::dot(v, v).sqrt();
    if n < 1e-12 { None } else { Some([v[0] / n, v[1] / n, v[2] / n]) }
}

/// `_angle`: the angle between two unit directions, by acos.
pub fn angle(a: &Vec3, b: &Vec3) -> f64 {
    py_max(-1.0, py_min(1.0, orbit::dot(a, b))).acos()
}

/// `edge_pixel`: the canvas point `f` (0..1, left / top first) of the way along edge `side` of `tile`.
pub fn edge_pixel(tile: &Tile, side: Direction, f: f64) -> (f64, f64) {
    let [x, y, w, h] = *tile;
    let f = py_max(0.0, py_min(1.0, f));
    match side {
        Direction::Right => (x + w, y + h * f),
        Direction::Left => (x, y + h * f),
        Direction::Up => (x + w * f, y),
        Direction::Down => (x + w * f, y + h),
    }
}

/// `point`: the direction of that edge point as the desktop is placed.
pub fn point(g: &Geometry, tile: &Tile, p: &Pose, side: Direction, f: f64) -> Option<Vec3> {
    let (x, y) = edge_pixel(tile, side, f);
    unit(&orbit::place_point(g, tile, p, x, y))
}

/// `_slot`: (azimuth, elevation, distance) of a slot's centre on the overview's plane.
pub fn slot(g: &Geometry, tile: &Tile) -> (f64, f64, f64) {
    let [hw, hh] = g.half;
    let wx = (2.0 * (tile[0] + tile[2] / 2.0) / g.w as f64 - 1.0) * hw;
    let wy = (1.0 - 2.0 * (tile[1] + tile[3] / 2.0) / g.h as f64) * hh;
    (wx.atan2(1.0), wy.atan2((1.0 + wx * wx).sqrt()), (1.0 + wx * wx + wy * wy).sqrt())
}

/// `fit_tile`: the tile scaled to fill the canvas, centred (the zoomed-in view). Its height is the Python's int `h`
/// of `g` when the tile is no wider than the canvas.
pub fn fit_tile(g: &Geometry, tile: &Tile) -> Tile {
    let (w_canvas, h_canvas) = (g.w as f64, g.h as f64);
    let h = py_min(h_canvas, w_canvas * tile[3] / tile[2]);
    let w = h * tile[2] / tile[3];
    [(w_canvas - w) / 2.0, (h_canvas - h) / 2.0, w, h]
}

/// `zoom_growth`: how much of its growth a desktop has at zoom `w`, its apparent size changing evenly.
pub fn zoom_growth(w: f64, grown: f64) -> f64 {
    let k = py_max(0.0, py_min(1.0, w));
    let a = 1.0 + (grown - 1.0) * k;
    a / (a + (1.0 - a) * k)
}

/// `grown`: how many times its overview size a desktop is when zoomed in.
pub fn grown(g: &Geometry, tile: &Tile, p: &Pose) -> f64 {
    let d = if p.distance > 0.0 { p.distance / 100.0 } else { 1.0 };
    fit_tile(g, tile)[3] * d * slot(g, tile).2 / tile[3]
}

/// `drawn_home`: (tile, placement) home is drawn at, zoom `zoom` of the way in. At zoom 0 or less, as given; otherwise
/// a placement with no roll (the callers give it one).
pub fn drawn_home(g: &Geometry, tile: &Tile, p: &Pose, zoom: f64) -> (Tile, Pose) {
    if zoom <= 0.0 {
        return (*tile, *p);
    }
    let z = py_min(zoom, 1.0);
    let fit = fit_tile(g, tile);
    let now: Tile = [0, 1, 2, 3].map(|i| tile[i] + (fit[i] - tile[i]) * z);
    let (a0, e0, r0) = slot(g, tile);
    let (a1, e1, r1) = slot(g, &now);
    let d = if p.distance > 0.0 { p.distance / 100.0 } else { 1.0 };
    let rigid = d * (now[3] / tile[3]) * r0 / r1;
    let pose = Pose {
        x: remainder(p.x * TENTH + a0 - a1, 2.0 * PI) / TENTH,
        y: (p.y * TENTH + e0 - e1) / TENTH,
        distance: if z < 1.0 { rigid / zoom_growth(z, grown(g, tile, p)) * 100.0 } else { 100.0 },
        curvature: p.curvature,
        roll: 0.0,
    };
    (now, pose)
}

/// `way`: (how far the view turns from home to target, radians; how much faster than the hand it turns there), or
/// None if either is degenerate.
pub fn way(
    g: &Geometry,
    home_tile: &Tile,
    home_p: &Pose,
    target_tile: &Tile,
    target_p: &Pose,
    side: Direction,
) -> Option<(f64, f64)> {
    let (a, b) = (neighbors::frame(g, home_tile, home_p)?, neighbors::frame(g, target_tile, target_p)?);
    let back = |v: &Vec3| [-v[0], -v[1], -v[2]];
    let pairs = [(back(&a.d), back(&b.d)), (a.r, b.r), (a.u, b.u)];
    let trace = py_sum(pairs.iter().map(|(p, q)| orbit::dot(p, q)));
    let angle = py_max(-1.0, py_min(1.0, (trace - 1.0) / 2.0)).acos();
    let axis = if side.sideways() { a.r } else { a.u };
    let sign = if matches!(side, Direction::Right | Direction::Up) { 1.0 } else { -1.0 };
    // the centre goes round a circle about the rotation's axis: sin(seen / 2) = sin(tilt) sin(angle / 2)
    let seen = self::angle(&a.d, &b.d);
    let spin = if angle < 1e-3 || seen < 1e-3 { 1.0 } else { py_max(1.0, (angle / 2.0).sin() / (seen / 2.0).sin()) };
    // dead opposite: pi, and the push says which way
    let turn = if sign * orbit::dot(&b.d, &axis) < 0.0 && angle > PI / 2.0 { 2.0 * PI - angle } else { angle };
    Some((turn, spin))
}

/// `overview_rate`: radians of turn per logical px of push in the overview: the desktop's span over the output's.
pub fn overview_rate(g: &Geometry, tile: &Tile, p: &Pose, side: Direction, size: (f64, f64)) -> Option<f64> {
    let (a, b, n) =
        if side.sideways() { (Direction::Left, Direction::Right, size.0) } else { (Direction::Up, Direction::Down, size.1) };
    let (e0, e1) = (point(g, tile, p, a, 0.5), point(g, tile, p, b, 0.5));
    let (Some(e0), Some(e1)) = (e0, e1) else { return None };
    if n <= 0.0 {
        return None;
    }
    let r = angle(&e0, &e1) / n;
    if r > 0.0 { Some(r) } else { None }
}

/// `rate`: radians of turn per logical px of push zoomed in: the view's span over the output's.
pub fn rate(g: &Geometry, tile: &Tile, side: Direction, size: (f64, f64)) -> f64 {
    let [hw, hh] = g.half;
    let [_, _, w, h] = fit_tile(g, tile);
    if side.sideways() {
        2.0 * (w / g.w as f64 * hw).atan() / size.0
    } else {
        2.0 * (h / g.h as f64 * hh).atan() / size.1
    }
}

/// `entry_fraction`: how far along the target's near edge (0..1) the pan comes in: level with where the cursor left
/// home. None if degenerate.
pub fn entry_fraction(
    g: &Geometry,
    home_tile: &Tile,
    home_p: &Pose,
    target_tile: &Tile,
    target_p: &Pose,
    side: Direction,
    along: f64,
) -> Option<f64> {
    let frame = neighbors::frame(g, home_tile, home_p);
    let e = point(g, home_tile, home_p, side, along);
    let (frame, e) = (frame?, e?);
    let axis = if side.sideways() { frame.u } else { frame.r };
    let want = orbit::dot(&e, &axis);
    let near = side.opposite();
    let off = |f: f64| point(g, target_tile, target_p, near, f).map(|q| orbit::dot(&q, &axis) - want);
    let (lo, hi) = (off(0.0)?, off(1.0)?);
    if lo * hi > 0.0 {
        return Some(if lo.abs() <= hi.abs() { 0.0 } else { 1.0 });
    }
    let (mut a, mut b) = (0.0, 1.0);
    for _ in 0..BISECT {
        let m = (a + b) / 2.0;
        if off(m)? * lo > 0.0 {
            a = m;
        } else {
            b = m;
        }
    }
    Some((a + b) / 2.0)
}

/// `_apart`: true if footprints `a` and `b` share no direction, their own polygons only (no handles); one too wide to
/// clip errs toward falling further.
pub fn apart(a: &Footprint, b: &Footprint) -> bool {
    if orbit::angle(&a.centre, &b.centre) >= a.radius + b.radius {
        return true;
    }
    if a.vertices.is_none() || b.vertices.is_none() {
        return false;
    }
    orbit::shared(a, b) <= orbit::AREA_EPS
}

/// `Pose` with its levelling roll set.
fn with_roll(p: &Pose, roll: f64) -> Pose {
    Pose { roll, ..*p }
}

/// `pan_zoom`: the highest zoom (up to MAX_ZOOM) at which the pan keeps both desktops in view, home drawn off the
/// target; 1.0 for a desktop that does not grow zoomed in; 0.0 if degenerate or nothing fits.
#[allow(clippy::too_many_arguments)]
pub fn pan_zoom(
    g: &Geometry,
    home_tile: &Tile,
    home_p: &Pose,
    target_tile: &Tile,
    target_p: &Pose,
    side: Direction,
    along: f64,
    entry: f64,
    home_roll: f64,
    target_roll: f64,
) -> f64 {
    let (near, far) = (side.opposite(), side);
    if grown(g, home_tile, home_p) <= 1.0 {
        return 1.0;
    }
    let centre = neighbors::frame(g, home_tile, home_p);
    let p = point(g, target_tile, target_p, near, entry);
    let p_far = point(g, target_tile, target_p, far, entry);
    let placed = point(g, home_tile, home_p, side, along);
    let (Some(centre), Some(p), Some(p_far), Some(placed)) = (centre, p, p_far, placed) else { return 0.0 };
    let [hw, hh] = g.half;
    let view = 2.0 * (if side.sideways() { hw } else { hh }).atan() * KEEP_IN_VIEW;
    let (edge, reach) = (angle(&centre.d, &placed), angle(&centre.d, &p));
    let reach = edge + py_max(reach - edge, 0.0) / 2.0 + 1e-9; // half way across the gap
    let target = orbit::footprint(g, target_tile, &with_roll(target_p, target_roll));
    // home, its roll coming back as it leaves, is off the target, and its drawn edge is not over the entry point
    let clear = |z: f64| {
        let (t, q) = drawn_home(g, home_tile, home_p, z);
        point(g, &t, &q, side, along).is_some_and(|e| {
            angle(&centre.d, &e) <= reach
                && apart(&orbit::footprint(g, &t, &with_roll(&q, home_roll * (1.0 - z))), &target)
        })
    };
    // the two desktops, far edge to far edge, are within the view
    let fits = |z: f64| {
        let (t, q) = drawn_home(g, home_tile, home_p, z);
        let e = point(g, &t, &q, near, along);
        clear(z) && e.is_some_and(|e| angle(&e, &p_far) <= view)
    };
    // the highest zoom up to MAX_ZOOM that passes; every test only gets easier going down
    let top = |ok: &dyn Fn(f64) -> bool| {
        if ok(MAX_ZOOM) {
            return Some(MAX_ZOOM);
        }
        if !ok(0.0) {
            return None;
        }
        let (mut a, mut b) = (0.0, MAX_ZOOM);
        for _ in 0..BISECT {
            let m = (a + b) / 2.0;
            if ok(m) {
                a = m;
            } else {
                b = m;
            }
        }
        Some(a)
    };
    top(&fits).or_else(|| top(&clear)).unwrap_or(0.0)
}

/// `Plan`: one edge pan, home to target past edge `side`: where it leaves and comes in, the zoom it holds, the turn per
/// px of push (`rate`), the whole turn (`way`) and how much faster than the hand it goes (`spin`), and the push's
/// lengths: the peek, then the gap.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Plan {
    pub home: usize,
    pub target: usize,
    pub side: Direction,
    pub exit: f64,
    pub entry: f64,
    pub zoom: f64,
    pub rate: f64,
    pub way: f64,
    pub spin: f64,
    pub peek_px: f64,
    pub gap_px: f64,
    pub total_px: f64,
}

/// `Plan.frame`'s payload: the `edge_pan` frame's, every share in 1/10000.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct PanFrame {
    pub active: bool,
    pub from: usize,
    pub to: usize,
    pub side: Direction,
    pub exit: i64,
    pub entry: i64,
    pub peek: i64,
    pub cross: i64,
    pub turn: i64,
    pub way: i64,
    pub zoom: i64,
}

/// A share in 1/10000, as the renderer reads it.
fn to_wire(v: f64) -> i64 {
    py_round(py_max(0.0, py_min(1.0, v)) * TO_WIRE)
}

/// `round(v * _TO_WIRE) / _TO_WIRE`: what the renderer will read back.
fn as_read(v: f64) -> f64 {
    py_round(v * TO_WIRE) as f64 / TO_WIRE
}

impl Plan {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        home: usize,
        target: usize,
        side: Direction,
        exit: f64,
        entry: f64,
        zoom: f64,
        rate: f64,
        way: f64,
        spin: f64,
        peek_px: f64,
        gap_px: f64,
    ) -> Plan {
        Plan { home, target, side, exit, entry, zoom, rate, way, spin, peek_px, gap_px, total_px: peek_px + gap_px }
    }

    /// `frame`: the pan's frame `travel` logical px into the push.
    pub fn frame(&self, travel: f64) -> PanFrame {
        let cross =
            if self.gap_px > 0.0 { py_min(py_max(travel - self.peek_px, 0.0) / self.gap_px, 1.0) } else { 0.0 };
        PanFrame {
            active: true,
            from: self.home,
            to: self.target,
            side: self.side,
            exit: to_wire(self.exit),
            entry: to_wire(self.entry),
            peek: to_wire(travel / self.total_px),
            cross: to_wire(cross),
            turn: to_wire(py_min(travel * self.rate * self.spin, self.way) / PI),
            way: to_wire(self.way / (2.0 * PI)),
            zoom: to_wire(self.zoom),
        }
    }
}

/// `plan`: the pan from desktop `home` past its `side` edge, the cursor `along` (0..1) of the way along it, to
/// `target`, for an output of `size` logical px; `rolls` the desktops' levelling rolls, `overview` the overview's
/// (no zoom). None if it cannot be planned.
#[allow(clippy::too_many_arguments)]
pub fn plan(
    g: &Geometry,
    placements: &[Pose],
    home: usize,
    target: usize,
    side: Direction,
    along: f64,
    size: (f64, f64),
    rolls: Option<&[f64]>,
    overview: bool,
) -> Option<Plan> {
    let n = g.tiles.len();
    if home >= n || target >= n || home == target || home >= placements.len() || target >= placements.len() {
        return None;
    }
    if py_min(size.0, size.1) <= 0.0 {
        return None;
    }
    let (home_tile, target_tile) = (&g.tiles[home], &g.tiles[target]);
    let (home_p, target_p) = (&placements[home], &placements[target]);
    let entry = as_read(entry_fraction(g, home_tile, home_p, target_tile, target_p, side, along)?);
    let exit = as_read(py_max(0.0, py_min(1.0, along)));
    let roll = |i: usize| rolls.and_then(|r| r.get(i).copied()).unwrap_or(0.0);
    let (zoom, r) = if overview {
        (0.0, overview_rate(g, home_tile, home_p, side, size)?)
    } else {
        let zoom = pan_zoom(g, home_tile, home_p, target_tile, target_p, side, exit, entry, roll(home), roll(target));
        (as_read(zoom), rate(g, home_tile, side, size))
    };
    let (t, q) = drawn_home(g, home_tile, home_p, zoom);
    let e = point(g, &t, &q, side, exit);
    let p = point(g, target_tile, target_p, side.opposite(), entry);
    let frame = neighbors::frame(g, home_tile, home_p);
    let other = neighbors::frame(g, target_tile, target_p);
    let (e, p, frame, other) = (e?, p?, frame?, other?);
    let axis = if side.sideways() { frame.r } else { frame.u };
    let sign = if matches!(side, Direction::Right | Direction::Up) { 1.0 } else { -1.0 };
    let ahead = sign * orbit::dot(&neighbors::sub(&p, &e), &axis);
    let (turn, spin) = way(g, home_tile, home_p, target_tile, target_p, side)?;
    let across = angle(&e, &p);
    let gap = if ahead > 0.0 || across > PI / 2.0 { across / r } else { 0.0 };
    let gap = py_min(gap, turn / spin / r); // the view is there by then
    let peek = py_max(PEEK_PX, angle(&frame.d, &other.d) * MEET_SHARE / r - gap);
    Some(Plan::new(home, target, side, exit, entry, zoom, r, turn, spin, peek, gap))
}

/// `{'active': False}`: the pan's off frame.
#[derive(Serialize)]
struct Off {
    active: bool,
}

#[derive(Serialize)]
#[serde(untagged)]
enum Payload<'a> {
    On(&'a PanFrame),
    Off(Off),
}

/// `frame_bytes`: the `edge_pan` control frame line; no payload is the off frame.
pub fn frame_bytes(run_id: &str, session: &str, request_id: u64, payload: Option<&PanFrame>) -> Vec<u8> {
    let payload = payload.map_or(Payload::Off(Off { active: false }), Payload::On);
    super::view::frame(run_id, session, request_id, "edge_pan", payload).into_bytes()
}
