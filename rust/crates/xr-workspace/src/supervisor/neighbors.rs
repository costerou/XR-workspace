//! Which desktop lies past an edge of the selected one, as placed round you in 3D. A port of
//! `tools/native_spike/neighbors.py` (its module docstring has the design); pure maths.
//!
//! Each desktop is a direction from your eye, its centre as drawn, with its own right and up axes there ([`Frame`]).
//! A candidate's bearing from home is the great-circle direction toward its centre in home's axes; the nearest within
//! the cone round the push wins, an off-axis one counting as further. Without an overview layout only the row order
//! is known ([`row_fallback`]).

use super::orbit::{self, Geometry, Pose, Tile, Vec3};
use super::python::{py_hypot, py_max, py_min};
use std::f64::consts::PI;

/// Half-angle round the push direction a neighbour must lie in (60: a 45-degree diagonal is reachable from both of
/// its sides).
pub const CONE: f64 = 60.0 * (PI / 180.0);
/// pick_at's half-angle: a 45-degree diagonal is still reachable from both of its sides.
pub const PAN_CONE: f64 = 45.0 * (PI / 180.0);
/// A desktop within this of directly behind home's direction has no usable bearing.
pub const BEHIND: f64 = PI / 180.0;
const EPS: f64 = 1e-9;

/// A push past an edge: the Python's 'right', 'left', 'up' and 'down' (view.hpp EdgePan::side too).
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    Right,
    Left,
    Up,
    Down,
}

impl Direction {
    pub const ALL: [Direction; 4] = [Direction::Right, Direction::Left, Direction::Up, Direction::Down];

    /// The Python's name; None for any other (where the Python raises `ValueError('invalid direction')`).
    pub fn parse(name: &str) -> Option<Direction> {
        Direction::ALL.into_iter().find(|d| d.name() == name)
    }

    pub fn name(self) -> &'static str {
        match self {
            Direction::Right => "right",
            Direction::Left => "left",
            Direction::Up => "up",
            Direction::Down => "down",
        }
    }

    /// `DIRECTIONS[direction]`: the push in home's (right, up) axes.
    pub fn vector(self) -> (f64, f64) {
        match self {
            Direction::Right => (1.0, 0.0),
            Direction::Left => (-1.0, 0.0),
            Direction::Up => (0.0, 1.0),
            Direction::Down => (0.0, -1.0),
        }
    }

    /// edge_pan.OPPOSITE.
    pub fn opposite(self) -> Direction {
        match self {
            Direction::Right => Direction::Left,
            Direction::Left => Direction::Right,
            Direction::Up => Direction::Down,
            Direction::Down => Direction::Up,
        }
    }

    /// Right or left: along the desktop's width.
    pub fn sideways(self) -> bool {
        matches!(self, Direction::Right | Direction::Left)
    }
}

/// (centre direction, right axis, up axis) of a desktop as drawn, unit vectors in the eye's frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Frame {
    pub d: Vec3,
    pub r: Vec3,
    pub u: Vec3,
}

/// `_sub`.
pub fn sub(a: &Vec3, b: &Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// `_scale`.
pub fn scale(v: &Vec3, k: f64) -> Vec3 {
    [v[0] * k, v[1] * k, v[2] * k]
}

/// `max(0.0, min(1.0, v))`.
fn unit_interval(v: f64) -> f64 {
    py_max(0.0, py_min(1.0, v))
}

/// The axes `r`, `u` taken to direction `d`: each made square to it, u square to r too. None if degenerate.
fn axes(d: Vec3, r: &Vec3, u: &Vec3) -> Option<Frame> {
    let r = orbit::norm(&sub(r, &scale(&d, orbit::dot(r, &d))))?;
    let u = sub(u, &scale(&d, orbit::dot(u, &d)));
    let u = orbit::norm(&sub(&u, &scale(&r, orbit::dot(&u, &r))))?;
    Some(Frame { d, r, u })
}

/// `frame`: the frame of a desktop laid out at `tile` with placement `p` (orbit.place_point's, roll included), or
/// None if degenerate.
pub fn frame(g: &Geometry, tile: &Tile, p: &Pose) -> Option<Frame> {
    let [tx, ty, tw, th] = *tile;
    let (cx, cy) = (tx + tw / 2.0, ty + th / 2.0);
    let c = orbit::place_point(g, tile, p, cx, cy);
    let d = orbit::norm(&c)?;
    let r = sub(&orbit::place_point(g, tile, p, cx + 1.0, cy), &c);
    let u = sub(&orbit::place_point(g, tile, p, cx, cy - 1.0), &c); // canvas y grows down
    axes(d, &r, &u)
}

/// `frames`: frame() for every desktop that has a slot in overview layout `g`.
pub fn frames(g: &Geometry, placements: &[Pose]) -> Vec<Option<Frame>> {
    placements.iter().enumerate().map(|(i, p)| g.tiles.get(i).and_then(|tile| frame(g, tile, p))).collect()
}

/// `bearing`: (angle between the centres in radians, unit 2D bearing in home's (right, up) axes) of `other` seen from
/// `home`, or None when it has no bearing (the same direction, or within BEHIND of exactly opposite).
pub fn bearing(home: &Frame, other: &Frame) -> Option<(f64, (f64, f64))> {
    let (c, d) = (&home.d, &other.d);
    let cos = py_max(-1.0, py_min(1.0, orbit::dot(c, d)));
    let t = sub(d, &scale(c, cos));
    let (a, b) = (orbit::dot(&t, &home.r), orbit::dot(&t, &home.u));
    let n = py_hypot(a, b);
    let angle = cos.acos();
    if n < EPS || angle > PI - BEHIND {
        return None;
    }
    Some((angle, (a / n, b / n)))
}

/// `pick`: the index of the desktop past home's `direction` edge among frames `fs`, or None. `cone`: CONE.
pub fn pick(fs: &[Option<Frame>], home: usize, direction: Direction, cone: f64) -> Option<usize> {
    let origin = fs.get(home).copied().flatten()?;
    let (px, py) = direction.vector();
    let (mut best, mut best_score) = (None, f64::INFINITY);
    for (j, f) in fs.iter().enumerate() {
        let Some(f) = f.filter(|_| j != home) else { continue };
        let Some((angle, (bx, by))) = bearing(&origin, &f) else { continue };
        let off = py_max(-1.0, py_min(1.0, bx * px + by * py)).acos();
        if off > cone + 1e-12 {
            continue;
        }
        let score = angle / py_max(off.cos(), EPS);
        if score < best_score - 1e-12 {
            (best, best_score) = (Some(j), score);
        }
    }
    best
}

/// `edge_frame`: frame() of the point of edge `direction` `along` (0..1, left / top first) of the way along it: its
/// direction and home's right and up axes taken to that point. None if degenerate.
pub fn edge_frame(g: &Geometry, tile: &Tile, p: &Pose, direction: Direction, along: f64) -> Option<Frame> {
    let [x, y, w, h] = *tile;
    let f = unit_interval(along);
    let (px, py) = match direction {
        Direction::Right => (x + w, y + h * f),
        Direction::Left => (x, y + h * f),
        Direction::Up => (x + w * f, y),
        Direction::Down => (x + w * f, y + h),
    };
    let (base, e) = (frame(g, tile, p), orbit::norm(&orbit::place_point(g, tile, p, px, py)));
    let (base, e) = (base?, e?);
    axes(e, &base.r, &base.u)
}

/// `pick_at`: pick() for a cursor `along` (0..1) of the way along home's `direction` edge: the bearings are from that
/// point of the edge, on the desktops as placed, within `cone` (PAN_CONE).
pub fn pick_at(
    g: &Geometry,
    placements: &[Pose],
    home: usize,
    direction: Direction,
    along: f64,
    cone: f64,
) -> Option<usize> {
    if home >= placements.len() || home >= g.tiles.len() {
        return None;
    }
    let origin = edge_frame(g, &g.tiles[home], &placements[home], direction, along)?;
    let mut fs = frames(g, placements);
    fs[home] = Some(origin);
    pick(&fs, home, direction, cone)
}

/// `row_fallback`: no layout: right is the next desktop in the row, left the previous one; up / down nothing.
pub fn row_fallback(count: usize, home: usize, direction: Direction) -> Option<usize> {
    let target = match direction {
        Direction::Right => home.checked_add(1),
        Direction::Left => home.checked_sub(1),
        Direction::Up | Direction::Down => None,
    };
    target.filter(|&t| t < count)
}
