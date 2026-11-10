//! Desktops anywhere round you (3DoF): the orbit placement, the pill drag that carries a desktop round you, and the
//! footprints that tell whether desktops overlap as you see them. A port of `tools/native_spike/orbit.py`; pure maths.
//!
//! Every function keeps the Python's operation order, so each result is the same double
//! (`rust/corpus/pointer/math.json` holds the Python's answers and `pointer_math_corpus_tests` replays them). The
//! Python's `sum()`, `min()` / `max()` and `math.hypot` are `super::python`'s `py_sum`, `py_min` / `py_max` and
//! `py_hypot`. The private helpers are public here, without the underscore: the free pointer and the unified cursor
//! build on them.
//!
//! `_strips` reads the handles' rectangles from `free_pointer.py`'s port ([`super::free_pointer`]).

use super::bump::{self, Pusher};
use super::free_pointer::{glass_to_desktop, level_bar_rect, pill_rect};
use super::python::{py_hypot, py_max, py_min, py_round, py_sum};
use crate::geometry::Placement;
pub use crate::geometry::{TURN, X_LIMIT, wrap};
use std::f64::consts::PI;
use std::sync::Arc;

/// A direction or point in the eye's frame (x right, y up, z behind you).
pub type Vec3 = [f64; 3];
/// A 3x3 matrix, by rows.
pub type Mat3 = [[f64; 3]; 3];
/// A desktop's slot on the overview's canvas: (x, y, width, height) px.
pub type Tile = [f64; 4];

/// Radians per wire unit of x / y (`TENTH`).
pub const TENTH: f64 = PI / 1800.0;
pub const Y_LIMIT: i64 = 900;
/// A drag within this many tenths of a degree of the slot (0) snaps to it, per axis.
pub const SNAP: i64 = 5;
/// reach() outside anchor mode: half a view past each edge (normalized canvas, 1 = edge).
pub const REACH: f64 = 2.0;
/// Kept past a desktop's edges (tile px), so neighbours stop 2 px apart.
pub const CLEARANCE_PX: f64 = 1.0;
/// Steradians: the "overlaps no more than now" tolerance.
pub const AREA_EPS: f64 = 1e-9;
const BEHIND: f64 = 1e-6;

/// The overview's geometry (`free_pointer.parse_overview_geometry`): the canvas `w` x `h` px, the view's half extents
/// `half` (tangents) and each desktop's slot. Calls that only need the canvas take one with no tiles.
#[derive(Clone, Debug, PartialEq)]
pub struct Geometry {
    pub w: i64,
    pub h: i64,
    pub half: [f64; 2],
    pub tiles: Vec<Tile>,
}

/// A placement as `place_point` reads it: the wire keys as numbers, plus the levelling `roll` (1/100 degree; 0 when
/// the Python dict has none).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub distance: f64,
    pub curvature: f64,
    pub roll: f64,
}

impl From<Placement> for Pose {
    fn from(p: Placement) -> Pose {
        Pose::rolled(p, 0.0)
    }
}

impl Pose {
    /// `{**p, 'roll': roll}`.
    pub fn rolled(p: Placement, roll: f64) -> Pose {
        Pose { x: p.x as f64, y: p.y as f64, distance: p.distance as f64, curvature: p.curvature as f64, roll }
    }
}

/// `p['distance'] / 100 if p['distance'] > 0 else 1.0`.
fn scale_of(distance: f64) -> f64 {
    if distance > 0.0 { distance / 100.0 } else { 1.0 }
}

/// `math.radians`.
fn radians(degrees: f64) -> f64 {
    degrees * (PI / 180.0)
}

// --- rotation ---------------------------------------------------------------------------------------------------------

/// `_diff`: the shortest signed turn from b to a, tenths of a degree.
pub fn diff(a: i64, b: i64) -> i64 {
    (a - b + X_LIMIT).rem_euclid(TURN) - X_LIMIT
}

/// `slot_azimuth`: the azimuth (radians, positive right) of the centre of a desktop's slot on the overview's plane.
pub fn slot_azimuth(g: &Geometry, tile: &Tile) -> f64 {
    ((2.0 * (tile[0] + tile[2] / 2.0) / g.w as f64 - 1.0) * g.half[0]).atan2(1.0)
}

/// `rotation`: the orbit Az(a0 + x) . El(y) . Az(-a0); x, y in tenths of a degree.
pub fn rotation(a0: f64, x: f64, y: f64) -> Mat3 {
    let az = |a: f64| {
        let (c, s) = (a.cos(), a.sin());
        [[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]]
    };
    let (c, s) = ((y * TENTH).cos(), (y * TENTH).sin());
    let el = [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]];
    mul(&az(a0 + x * TENTH), &mul(&el, &az(-a0)))
}

/// `_mul`: the matrix product, each entry Python's `sum()` of its three products.
pub fn mul(a: &Mat3, b: &Mat3) -> Mat3 {
    let mut out = [[0.0; 3]; 3];
    for (i, row) in out.iter_mut().enumerate() {
        for (j, v) in row.iter_mut().enumerate() {
            *v = py_sum((0..3).map(|k| a[i][k] * b[k][j]));
        }
    }
    out
}

/// `apply`: m . v.
pub fn apply(m: &Mat3, v: &Vec3) -> Vec3 {
    [
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    ]
}

/// `apply_t`: the inverse rotation (the transpose) applied to v.
pub fn apply_t(m: &Mat3, v: &Vec3) -> Vec3 {
    [
        m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
        m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
        m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
    ]
}

/// `place_point`: the world position of canvas pixel (px, py) on a desktop laid out at `tile` with placement `p`. The
/// desktop faces the eye, upright along its meridian, `distance` times as far as its slot's centre, bent by
/// `curvature` toward you and turned by its levelling roll. Mirrors spike/src/view.cpp place_point.
pub fn place_point(g: &Geometry, tile: &Tile, p: &Pose, px: f64, py: f64) -> Vec3 {
    place_point_on(g.w as f64, g.h as f64, g.half, tile, p, px, py)
}

/// [`place_point`] on a canvas given as numbers: `free_pointer.band_canvas` hands `orbit.place_point` the settled
/// Restore geometry's canvas, whose size may be a float.
pub fn place_point_on(w: f64, h: f64, half: [f64; 2], tile: &Tile, p: &Pose, px: f64, py: f64) -> Vec3 {
    let [hw, hh] = half;
    let (sx, sy) = (2.0 * hw / w, 2.0 * hh / h);
    let [tx, ty, tw_px, th_px] = *tile;
    let (cx, cy) = (tx + tw_px / 2.0, ty + th_px / 2.0);
    let tw = tw_px * sx;
    let d = scale_of(p.distance);
    let (wx, wy) = ((2.0 * cx / w - 1.0) * hw, (1.0 - 2.0 * cy / h) * hh);
    let (a0, e0) = (wx.atan2(1.0), wy.atan2((1.0 + wx * wx).sqrt()));
    let r = (1.0 + wx * wx + wy * wy).sqrt() * d;
    let (u, v) = ((px - cx) * sx, (cy - py) * sy);
    let mut local = [u, v, 0.0];
    let curvature = radians(p.curvature);
    if curvature > 1e-4 && tw > 0.0 {
        let rc = tw / curvature;
        let phi = u / rc;
        local = [rc * phi.sin(), v, rc * (1.0 - phi.cos())];
    }
    let roll = radians(p.roll / 100.0);
    if roll != 0.0 {
        let (cr, sr) = (roll.cos(), roll.sin());
        local = [cr * local[0] - sr * local[1], sr * local[0] + cr * local[1], local[2]];
    }
    let q = [local[0], local[1], local[2] - r];
    let (ce, se) = ((e0 + p.y * TENTH).cos(), (e0 + p.y * TENTH).sin());
    let q = [q[0], ce * q[1] - se * q[2], se * q[1] + ce * q[2]];
    let (ca, sa) = ((a0 + p.x * TENTH).cos(), (a0 + p.x * TENTH).sin());
    [ca * q[0] - sa * q[2], q[1], sa * q[0] + ca * q[2]]
}

// --- the dot's direction ----------------------------------------------------------------------------------------------

/// `pointer_direction`: (azimuth, elevation) radians of the dot at canvas point `pointer`, linear in the pointer;
/// the elevation stops at the poles.
pub fn pointer_direction(g: &Geometry, pointer: [f64; 2]) -> (f64, f64) {
    let (u, v) = (2.0 * pointer[0] / g.w as f64 - 1.0, 1.0 - 2.0 * pointer[1] / g.h as f64);
    let [hw, hh] = g.half;
    (u * hw, py_min(py_max(v * hh, -PI / 2.0), PI / 2.0))
}

/// `pointer_ray`: the unit direction of the dot at canvas point `pointer`.
pub fn pointer_ray(g: &Geometry, pointer: [f64; 2]) -> Vec3 {
    let (az, el) = pointer_direction(g, pointer);
    [el.cos() * az.sin(), el.sin(), -el.cos() * az.cos()]
}

/// `pointer_at`: the canvas point whose dot lies in direction `d` (pointer_ray's inverse).
pub fn pointer_at(g: &Geometry, d: &Vec3) -> [f64; 2] {
    let [hw, hh] = g.half;
    let (az, el) = (d[0].atan2(-d[2]), d[1].atan2(py_hypot(d[0], d[2])));
    [(az / hw + 1.0) * g.w as f64 / 2.0, (1.0 - el / hh) * g.h as f64 / 2.0]
}

/// `plane_at`: where direction `d` from the eye meets the overview's plane (z = -1), canvas px; None if it points away.
pub fn plane_at(g: &Geometry, d: &Vec3) -> Option<[f64; 2]> {
    if d[2] > -BEHIND {
        return None;
    }
    let [hw, hh] = g.half;
    Some([(d[0] / -d[2] / hw + 1.0) * g.w as f64 / 2.0, (1.0 - d[1] / -d[2] / hh) * g.h as f64 / 2.0])
}

/// `from_plane`: the canvas point whose dot looks at canvas point `point` on the overview's plane.
pub fn from_plane(g: &Geometry, point: [f64; 2]) -> [f64; 2] {
    let [hw, hh] = g.half;
    let d = [(2.0 * point[0] / g.w as f64 - 1.0) * hw, (1.0 - 2.0 * point[1] / g.h as f64) * hh, -1.0];
    pointer_at(g, &d)
}

/// `reach`: how far the pointer goes from the view's centre, normalized canvas: round to behind you and the poles in
/// anchor mode (`around`), else REACH.
pub fn reach(g: &Geometry, around: bool) -> (f64, f64) {
    let [hw, hh] = g.half;
    if around { (PI / hw, PI / 2.0 / hh) } else { (REACH, REACH) }
}

/// `unorbit_ray`: the dot's ray as the un-orbited desktop sees it, on the overview's plane; None if it points away.
pub fn unorbit_ray(g: &Geometry, tile: &Tile, p: &Pose, pointer: [f64; 2]) -> Option<[f64; 2]> {
    let mut d = pointer_ray(g, pointer);
    if p.x != 0.0 || p.y != 0.0 {
        d = apply_t(&rotation(slot_azimuth(g, tile), p.x, p.y), &d);
    }
    plane_at(g, &d)
}

// --- the pill drag ----------------------------------------------------------------------------------------------------

/// `_snap`: 0 within SNAP of the slot; x snaps to the nearest whole turn (it may be unwrapped).
pub fn snap(value: i64) -> i64 {
    if wrap(value).abs() <= SNAP { value - wrap(value) } else { value }
}

/// `slot_direction`: (azimuth, elevation) radians of a desktop's slot centre (place_point's a0 / e0).
pub fn slot_direction(g: &Geometry, tile: &Tile) -> (f64, f64) {
    let [hw, hh] = g.half;
    let wx = (2.0 * (tile[0] + tile[2] / 2.0) / g.w as f64 - 1.0) * hw;
    let wy = (1.0 - 2.0 * (tile[1] + tile[3] / 2.0) / g.h as f64) * hh;
    (wx.atan2(1.0), wy.atan2((1.0 + wx * wx).sqrt()))
}

/// `grab_point`: the dot's ray at the press turned back by the desktop's (x, y) `start`: the grabbed point in the
/// desktop's own frame.
pub fn grab_point(g: &Geometry, tile: &Tile, start: (f64, f64), press: [f64; 2]) -> Vec3 {
    let (a0, e0) = slot_direction(g, tile);
    let d = pointer_ray(g, press);
    let (a, e) = (a0 + start.0 * TENTH, e0 + start.1 * TENTH);
    let (c, s) = (a.cos(), a.sin());
    let d = [c * d[0] + s * d[2], d[1], -s * d[0] + c * d[2]];
    let (c, s) = (e.cos(), e.sin());
    [d[0], c * d[1] + s * d[2], -s * d[1] + c * d[2]]
}

/// `_nearest`: `angle` plus whole turns, as close to `near` as it gets.
pub fn nearest(angle: f64, near: f64) -> f64 {
    angle + 2.0 * PI * ((near - angle) / (2.0 * PI)).round_ties_even()
}

/// `solve`: the (x, y) placement, unrounded tenths, x unwrapped next to near.0, that puts the desktop-frame direction
/// `local` on the dot's ray at `pointer`.
pub fn solve(g: &Geometry, tile: &Tile, local: &Vec3, pointer: [f64; 2], near: (f64, f64)) -> (f64, f64) {
    let (a0, e0) = slot_direction(g, tile);
    let d = pointer_ray(g, pointer);
    let [lx, ly, lz] = *local;
    let r = py_hypot(ly, lz);
    let near_e = e0 + near.1 * TENTH;
    let e = if r < 1e-12 {
        near_e
    } else {
        let (phi, k) = (lz.atan2(ly), py_min(py_max(d[1] / r, -1.0), 1.0).acos());
        let (first, second) = (nearest(k - phi, near_e), nearest(-k - phi, near_e));
        if (second - near_e).abs() < (first - near_e).abs() { second } else { first }
    };
    let (c, s) = (e.cos(), e.sin());
    let (mx, mz) = (lx, s * ly + c * lz);
    let a = d[0].atan2(-d[2]) - mx.atan2(-mz);
    let a = nearest(a, a0 + near.0 * TENTH);
    ((a - a0) / TENTH, (e - e0) / TENTH)
}

/// `_target`: (the unrounded (x, y), the (x, y) a pill drag asks for): x unwrapped, y within the poles, each snapped
/// to the slot within SNAP. The unrounded y stopped at a pole is the Python's int `Y_LIMIT`.
pub fn target(g: &Geometry, tile: &Tile, local: &Vec3, near: (f64, f64), pointer: [f64; 2]) -> ((f64, f64), (i64, i64)) {
    let (x, y) = solve(g, tile, local, pointer, near);
    let limit = Y_LIMIT as f64;
    let y = py_min(py_max(y, -limit), limit);
    let ry = py_round(y);
    ((x, y), (snap(py_round(x)), if ry.abs() <= SNAP { 0 } else { ry }))
}

/// `drag_target`: the new (x, y) for a pill drag of the desktop at `tile`, `start` its (x, y) at the press: the
/// point grabbed at the press goes under the dot. No bumping (that is [`PillDrag`]).
pub fn drag_target(g: &Geometry, tile: &Tile, start: (i64, i64), press: [f64; 2], pointer: [f64; 2]) -> (i64, i64) {
    let start = (start.0 as f64, start.1 as f64);
    let (_, (x, y)) = target(g, tile, &grab_point(g, tile, start, press), start, pointer);
    (wrap(x), y)
}

/// `PillDrag`: one pill drag from the press to the release. The grabbed point is kept under the dot, x followed
/// unwrapped; the desktops it runs into are pushed out of its way (bump.py).
#[derive(Clone, Debug)]
pub struct PillDrag {
    pub g: Geometry,
    pub index: usize,
    pub start: (i64, i64),
    pub press: [f64; 2],
    pub rolls: Option<Vec<f64>>,
    pub tile: Tile,
    pub local: Vec3,
    /// Where the desktop got to, x unwrapped.
    pub at: (i64, i64),
    /// The unrounded (x, y) the drag last asked for (`_target`'s first half); the start until the first follow.
    pub near: (f64, f64),
    pub pusher: Option<Pusher>,
}

impl PillDrag {
    pub fn new(g: Geometry, index: usize, start: (i64, i64), press: [f64; 2], rolls: Option<Vec<f64>>) -> PillDrag {
        let tile = g.tiles[index];
        let near = (start.0 as f64, start.1 as f64);
        let local = grab_point(&g, &tile, near, press);
        PillDrag { g, index, start, press, rolls, tile, local, at: start, near, pusher: None }
    }

    /// `follow`: every desktop's wire placement for the pointer at canvas point `pointer`.
    pub fn follow(&mut self, pointer: [f64; 2], placements: &[Placement]) -> Vec<Placement> {
        let p = placements[self.index];
        if wrap(self.at.0) != p.x || self.at.1 != p.y {
            // changed meanwhile (a panel key): carry on from there
            self.at = (self.at.0 + diff(p.x, wrap(self.at.0)), p.y);
        }
        let (near, (tx, ty)) = target(&self.g, &self.tile, &self.local, self.near, pointer);
        self.near = near;
        let pusher = self
            .pusher
            .get_or_insert_with(|| Pusher::new(Some(self.g.clone()), self.index, self.rolls.clone()));
        let target = bump::Patch { x: Some(tx), y: Some(ty), ..bump::Patch::default() };
        let start = bump::Patch { x: Some(self.at.0), y: Some(self.at.1), ..bump::Patch::default() };
        let out = pusher.move_to(placements, &target, Some(&start));
        let reached = pusher.reached.expect("move_to sets reached");
        self.at = (reached.x, reached.y);
        out
    }
}

// --- footprints: what each desktop covers, seen from your eye --------------------------------------------------------

/// `_norm`: v made unit length; None for the zero vector.
pub fn norm(v: &Vec3) -> Option<Vec3> {
    let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if n > 0.0 { Some([v[0] / n, v[1] / n, v[2] / n]) } else { None }
}

/// `_dot`.
pub fn dot(a: &Vec3, b: &Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// `_cross`.
pub fn cross(a: &Vec3, b: &Vec3) -> Vec3 {
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

/// `_angle`: the angle between two directions, by atan2 (exact near 0 and pi).
pub fn angle(a: &Vec3, b: &Vec3) -> f64 {
    let c = cross(a, b);
    py_max(dot(&c, &c), 0.0).sqrt().atan2(dot(a, b))
}

/// `_outline`: points round a desktop and its CLEARANCE_PX on its placed surface (un-orbited): the corners and, when
/// curved, the top and bottom edges every few degrees of arc.
pub fn outline(g: &Geometry, tile: &Tile, p: &Pose) -> Vec<Vec3> {
    let [tx, ty, tw, th] = *tile;
    let (top, bottom) = (ty - CLEARANCE_PX, ty + th + CLEARANCE_PX);
    let columns = if p.curvature > 0.0 { (p.curvature / 6.0).ceil().clamp(4.0, 16.0) as i64 } else { 1 };
    let xs: Vec<f64> = (0..=columns)
        .map(|i| tx - CLEARANCE_PX + (tw + 2.0 * CLEARANCE_PX) * i as f64 / columns as f64)
        .collect();
    let mut out: Vec<Vec3> = xs.iter().map(|&x| place_point(g, tile, p, x, top)).collect();
    out.extend(xs.iter().rev().map(|&x| place_point(g, tile, p, x, bottom)));
    out
}

/// `_strips`: points round the strips a desktop's handles take on its placed surface (un-orbited): the level bar above
/// it and the pill under it, each as wide as the handle at its far side, sloping out at 45 degrees to the desktop's
/// edge. A handle that does not get past the margin takes no strip.
pub fn strips(g: &Geometry, tile: &Tile, p: &Pose) -> Vec<Vec<Vec3>> {
    let [tx, ty, tw, th] = *tile;
    let (lo, hi) = (tx - CLEARANCE_PX, tx + tw + CLEARANCE_PX);
    let mut found = Vec::new();
    let [x, y, w, _] = level_bar_rect(tile);
    if w > 0.0 && y < ty - CLEARANCE_PX {
        found.push((x, x + w, ty - CLEARANCE_PX, y));
    }
    let [x, y, w, h] = pill_rect(tile);
    let ((x0, _), (x1, y1)) = (glass_to_desktop(tile, p, x, y), glass_to_desktop(tile, p, x + w, y + h));
    if x1 > x0 && y1 > ty + th + CLEARANCE_PX {
        found.push((x0, x1, ty + th + CLEARANCE_PX, y1));
    }
    found
        .into_iter()
        .map(|(x0, x1, near, far)| {
            let slope = (far - near).abs();
            [(py_max(x0 - slope, lo), near), (py_min(x1 + slope, hi), near), (x1, far), (x0, far)]
                .iter()
                .map(|&(u, v)| place_point(g, tile, p, u, v))
                .collect()
        })
        .collect()
}

/// `Footprint`: a convex spherical polygon (unit directions, counter-clockwise as you see it), its edges' inward
/// great-circle normals and a bounding cone (centre, angular radius). `handles`: the footprints of the strips its
/// handles take; the cone holds them too. `vertices` None: too wide to be convex, only the cone is used. `normals` is
/// None unless there are vertices.
#[derive(Clone, Debug, PartialEq)]
pub struct Footprint {
    pub vertices: Option<Vec<Vec3>>,
    pub normals: Option<Vec<Vec3>>,
    pub centre: Vec3,
    pub radius: f64,
    pub handles: Vec<Footprint>,
}

impl Footprint {
    /// `Footprint(vertices, centre, radius, handles)`: the normals from the vertices, each turned toward the centre.
    pub fn new(vertices: Option<Vec<Vec3>>, centre: Vec3, radius: f64, handles: Vec<Footprint>) -> Footprint {
        let normals = vertices.as_ref().filter(|v| !v.is_empty()).map(|v| {
            let k = v.len();
            (0..k)
                .map(|i| {
                    let n = cross(&v[i], &v[(i + 1) % k]);
                    if dot(&n, &centre) >= 0.0 { n } else { [-n[0], -n[1], -n[2]] }
                })
                .collect()
        });
        Footprint { vertices, normals, centre, radius, handles }
    }

    /// `rotated`: the footprint turned by m (None: unchanged). One with no vertices keeps only its cone.
    pub fn rotated(&self, m: Option<&Mat3>) -> Footprint {
        let Some(m) = m else { return self.clone() };
        let mut out = Footprint::new(None, apply(m, &self.centre), self.radius, Vec::new());
        if let Some(vertices) = self.vertices.as_ref().filter(|v| !v.is_empty()) {
            out.vertices = Some(vertices.iter().map(|v| apply(m, v)).collect());
            out.normals = Some(self.normals.iter().flatten().map(|n| apply(m, n)).collect());
            out.handles = self.handles.iter().map(|h| h.rotated(Some(m))).collect();
        }
        out
    }

    /// `(a,) + a.handles`: the footprint's own polygon, then its handles'.
    pub fn parts(&self) -> impl Iterator<Item = &Footprint> {
        std::iter::once(self).chain(self.handles.iter())
    }
}

/// Python's `sorted()` order of two-number tuples.
fn tuple_order(a: &[f64; 2], b: &[f64; 2]) -> std::cmp::Ordering {
    let by = |x: f64, y: f64| x.partial_cmp(&y).unwrap_or(std::cmp::Ordering::Equal);
    if a[0] == b[0] { by(a[1], b[1]) } else { by(a[0], b[0]) }
}

/// `_hull`: Andrew's monotone chain, the indices of the convex hull of 2-D `points`, counter-clockwise, no collinear
/// points.
pub fn hull(points: &[[f64; 2]]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..points.len()).collect();
    order.sort_by(|&a, &b| tuple_order(&points[a], &points[b]));
    if order.len() < 3 {
        return order;
    }
    let turn = |o: usize, a: usize, b: usize| {
        let ([ox, oy], [ax, ay], [bx, by]) = (points[o], points[a], points[b]);
        (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)
    };
    let chain = |indices: &mut dyn Iterator<Item = usize>| {
        let mut out: Vec<usize> = Vec::new();
        for i in indices {
            while out.len() >= 2 && turn(out[out.len() - 2], out[out.len() - 1], i) <= 1e-12 {
                out.pop();
            }
            out.push(i);
        }
        out.pop();
        out
    };
    let mut lower = chain(&mut order.iter().copied());
    lower.extend(chain(&mut order.iter().rev().copied()));
    lower
}

/// `footprint`: what a desktop at `tile` with placement `p` covers as you see it, orbit and levelling roll included.
pub fn footprint(g: &Geometry, tile: &Tile, p: &Pose) -> Footprint {
    let base = unorbited_footprint(g, tile, p.distance, p.curvature, p.roll);
    if p.x == 0.0 && p.y == 0.0 {
        return base;
    }
    base.rotated(Some(&rotation(slot_azimuth(g, tile), p.x, p.y)))
}

/// `Footprint` shared between the callers that cache it ([`bump::Pusher`]).
pub type SharedFootprint = Arc<Footprint>;

/// `_footprint`: the footprint of a desktop in its slot (x = y = 0): only the canvas of `g` is read. Its polygon is the
/// convex hull of its outline as you see it; a desktop reaching 90 degrees from its centre keeps only its cone.
pub fn unorbited_footprint(g: &Geometry, tile: &Tile, distance: f64, curvature: f64, roll: f64) -> Footprint {
    let p = Pose { x: 0.0, y: 0.0, distance, curvature, roll };
    let dirs: Vec<Vec3> = outline(g, tile, &p).iter().filter_map(norm).collect();
    let Some(centre) = norm(&sum3(&dirs)) else {
        // a huge, close, curved desktop wrapping round the eye: it covers every direction
        let [tx, ty, tw, th] = *tile;
        let middle = norm(&place_point(g, tile, &p, tx + tw / 2.0, ty + th / 2.0)).unwrap_or([0.0, 0.0, -1.0]);
        return Footprint::new(None, middle, PI, Vec::new());
    };
    let strips: Vec<Vec<Vec3>> = strips(g, tile, &p).iter().map(|s| s.iter().filter_map(norm).collect()).collect();
    let every: Vec<&Vec3> = dirs.iter().chain(strips.iter().flatten()).collect();
    let radius = first_max(every.iter().map(|d| angle(&centre, d))) + 1e-9;
    if every.iter().any(|d| dot(d, &centre) < 0.02) {
        return Footprint::new(None, centre, radius, Vec::new());
    }
    let up = if centre[1].abs() < 0.9 { [0.0, 1.0, 0.0] } else { [0.0, 0.0, -1.0] };
    let right = norm(&cross(&centre, &up)).unwrap_or([f64::NAN; 3]);
    let top = cross(&right, &centre);
    let convex = |points: &[Vec3]| -> Vec<Vec3> {
        let flat: Vec<[f64; 2]> =
            points.iter().map(|d| [dot(d, &right) / dot(d, &centre), dot(d, &top) / dot(d, &centre)]).collect();
        hull(&flat).into_iter().map(|i| points[i]).collect()
    };
    let mut handles = Vec::new();
    for strip in &strips {
        let (outer, middle) = (convex(strip), norm(&sum3(strip)));
        if let Some(middle) = middle.filter(|_| outer.len() >= 3) {
            let radius = first_max(outer.iter().map(|d| angle(&middle, d))) + 1e-9;
            handles.push(Footprint::new(Some(outer), middle, radius, Vec::new()));
        }
    }
    Footprint::new(Some(convex(&dirs)), centre, radius, handles)
}

/// `tuple(sum(d[i] for d in points) for i in range(3))`.
fn sum3(points: &[Vec3]) -> Vec3 {
    [0, 1, 2].map(|i| py_sum(points.iter().map(|d| d[i])))
}

/// Python's `max()` of an iterable: the first largest.
fn first_max(values: impl Iterator<Item = f64>) -> f64 {
    values.reduce(py_max).unwrap_or(f64::NAN)
}

/// `_clip`: Sutherland-Hodgman, the part of a spherical polygon on the inner side of a great circle (normal . p >= 0).
pub fn clip(polygon: &[Vec3], normal: &Vec3) -> Vec<Vec3> {
    let mut out = Vec::new();
    let k = polygon.len();
    for i in 0..k {
        let (a, b) = (&polygon[i], &polygon[(i + 1) % k]);
        let (da, db) = (dot(normal, a), dot(normal, b));
        if da >= 0.0 {
            out.push(*a);
        }
        if (da >= 0.0) != (db >= 0.0) {
            let t = da / (da - db);
            if let Some(q) = norm(&[a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]) {
                out.push(q);
            }
        }
    }
    out
}

/// `_area`: the solid angle (steradians) of a convex spherical polygon (Van Oosterom-Strackee triangle fan).
pub fn area(polygon: &[Vec3]) -> f64 {
    let mut total = 0.0;
    for i in 1..polygon.len().saturating_sub(1) {
        let (a, b, c) = (&polygon[0], &polygon[i], &polygon[i + 1]);
        let det = dot(a, &cross(b, c));
        total += (2.0 * det.atan2(1.0 + dot(a, b) + dot(b, c) + dot(c, a))).abs();
    }
    total
}

/// `_shared`: the solid angle two convex polygons (a footprint's own, without its handles) share.
pub fn shared(a: &Footprint, b: &Footprint) -> f64 {
    let mut polygon = a.vertices.clone().unwrap_or_default();
    for n in b.normals.iter().flatten() {
        polygon = clip(&polygon, n);
        if polygon.len() < 3 {
            return 0.0;
        }
    }
    area(&polygon)
}

/// `overlap`: the solid angle footprints `a` and `b` share, their handles' strips included (0 if they only touch or are
/// apart). Footprints too wide to clip give the cones' overlap squared, a stand-in that grows with it.
pub fn overlap(a: &Footprint, b: &Footprint) -> f64 {
    let gap = angle(&a.centre, &b.centre) - a.radius - b.radius;
    if gap >= 0.0 {
        return 0.0;
    }
    if a.vertices.is_none() || b.vertices.is_none() {
        return gap * gap;
    }
    py_sum(a.parts().flat_map(|p| b.parts().map(move |q| shared(p, q))))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrap_and_diff_go_the_short_way_round() {
        assert_eq!(wrap(1800), 1800);
        assert_eq!(wrap(1801), -1799);
        assert_eq!(diff(-1790, 1790), 20);
        assert_eq!(snap(3601), 3600);
        assert_eq!(snap(-4), 0);
        assert_eq!(snap(6), 6);
    }

    #[test]
    fn hull_drops_collinear_points() {
        let points = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [1.0, 1.0]];
        assert_eq!(hull(&points), vec![0, 2, 3, 4]);
    }
}
