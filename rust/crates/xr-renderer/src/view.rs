//! Overview / zoom layout of the managed desktops in the glasses view (`spike/include/xr/view.hpp`, `spike/src/view.cpp`).
//!
//! Pure and offline-testable. zoom 0 = overview (all desktops side by side with `spacing` gaps and margins);
//! zoom 1 = the desktop at `position` fills the view, neighbours continue to either side.
//! Sources may differ in size: all tiles share one height and keep their own aspect, so resolution
//! affects sharpness, not apparent size.
//!
//! All maths is `f32` (and `f64` where the C++ uses `double`) in the same order as the C++, so results match it bit
//! for bit. `std::min` / `std::max` / `std::clamp` are mirrored by [`cmin`] / [`cmax`] / [`cclamp`], which pick the
//! same operand as the C++ does when a value is NaN.

// `!(x > 0.0)` is the C++'s own test and is true for NaN, where `x <= 0.0` is not: kept as written.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use std::f32::consts::PI;
use std::ops::Deref;

use crate::desktops::MAX_DESKTOPS;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct FRect {
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
}

impl FRect {
    pub const fn new(x: f32, y: f32, width: f32, height: f32) -> Self {
        Self { x, y, width, height }
    }
}

/// A source desktop's pixel size.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Size {
    pub w: f32,
    pub h: f32,
}

impl Size {
    pub const fn new(w: f32, h: f32) -> Self {
        Self { w, h }
    }
}

pub const MAX_SPACING: u32 = 400;

// XR-1..XR-MAX_DESKTOPS; Presentation::set_view clamps to the run's own count.
const MAX_INDEX: u32 = MAX_DESKTOPS as u32 - 1;

/// `std::min(a, b)`: `b < a ? b : a`.
pub(crate) fn cmin(a: f32, b: f32) -> f32 {
    if b < a { b } else { a }
}

/// `std::max(a, b)`: `a < b ? b : a`.
pub(crate) fn cmax(a: f32, b: f32) -> f32 {
    if a < b { b } else { a }
}

/// `std::clamp(v, lo, hi)`: `v < lo ? lo : hi < v ? hi : v`.
pub(crate) fn cclamp(v: f32, lo: f32, hi: f32) -> f32 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

fn clampf(v: f32, lo: f32, hi: f32) -> f32 {
    cmax(lo, cmin(hi, v))
}

fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

/// `std::remainder` on floats (IEEE remainder, the quotient rounded to nearest, ties to even), exact like glibc's.
/// Worked in `f64` from fmod by 2|y| (exact), so no quotient is ever rounded: an `f32` one could round onto a tie (an
/// orbit just past +-pi) and pick the other way round, and an `f64` one is not exact either once |x / y| is large.
/// r is in [0, 2|y|); y / 2, 3|y| / 2, r - |y| and r - 2|y| are all exact in `f64`, and the result is an `f32`.
fn remainder(x: f32, y: f32) -> f32 {
    let (ax, ay) = (f64::from(x).abs(), f64::from(y).abs());
    let r = ax % (2.0 * ay);
    let r = if 2.0 * r <= ay {
        r // a tie at |y| / 2 takes the even quotient
    } else if 2.0 * r < 3.0 * ay {
        r - ay
    } else {
        r - 2.0 * ay // a tie at 3|y| / 2 takes the even quotient too
    };
    // remainder(-x, y) is -remainder(x, y), a zero included; NaN (y 0, x infinite or either NaN) stays NaN.
    if x.is_sign_negative() { -(r as f32) } else { r as f32 }
}

const TWO_PI: f32 = 2.0 * PI;

fn valid(src: &[Size], view_w: f32, view_h: f32) -> bool {
    !src.is_empty() && view_w > 0.0 && view_h > 0.0 && src.iter().all(|z| z.w > 0.0 && z.h > 0.0)
}

/// One row at a shared height h, `s` apart, first tile's left edge at x0, centred vertically.
fn row(src: &[Size], view_h: f32, h: f32, s: f32, mut x0: f32) -> Vec<FRect> {
    let mut out = Vec::with_capacity(src.len());
    for z in src {
        let w = h * z.w / z.h;
        out.push(FRect { x: x0, y: (view_h - h) / 2.0, width: w, height: h });
        x0 += w + s;
    }
    out
}

pub fn smoothstep(t: f32) -> f32 {
    let t = clampf(t, 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Quintic: zero velocity and acceleration at both ends.
pub fn smootherstep(t: f32) -> f32 {
    let t = clampf(t, 0.0, 1.0);
    t * t * t * (t * (6.0 * t - 15.0) + 10.0)
}

/// [`settle_curve`]'s top speed.
pub const SETTLE_MAX_SPEED: f32 = 2.5;

/// smootherstep starting at speed `v` instead of at rest (v in units of the whole way per the whole time, clamped to
/// 0..SETTLE_MAX_SPEED): 0 to 1 over t = 0..1, at rest with no acceleration at t = 1, never past 1 and never going back.
/// v = 0 is smootherstep exactly.
pub fn settle_curve(t: f32, v: f32) -> f32 {
    let t = clampf(t, 0.0, 1.0);
    let v = clampf(v, 0.0, SETTLE_MAX_SPEED);
    // The quintic with x(0) = 0, x'(0) = v, x''(0) = 0, x(1) = 1, x'(1) = x''(1) = 0. Its slope is
    // (1 - t)^2 (v (1 + 5t)(1 - 3t) + 30t^2), which never goes negative for v up to 2.5.
    t * (v + t * t * (10.0 - 6.0 * v + t * (8.0 * v - 15.0 + t * (6.0 - 3.0 * v))))
}

/// Empty if there are no sources, the view is empty, or any source has a non-positive size.
/// Overview: the largest shared height whose widths plus spacing gaps and margins fit view_w, <= view_h.
pub fn overview_layout(src: &[Size], view_w: f32, view_h: f32, spacing: f32) -> Vec<FRect> {
    if !valid(src, view_w, view_h) {
        return Vec::new();
    }
    let s = clampf(spacing, 0.0, MAX_SPACING as f32);
    let n = src.len() as f32;
    let mut aspects = 0.0f32;
    for z in src {
        aspects += z.w / z.h;
    }
    let h = cmin(view_h, cmax(0.0, view_w - (n + 1.0) * s) / aspects);
    let w = h * aspects;
    row(src, view_h, h, s, (view_w - (w + (n - 1.0) * s)) / 2.0) // centred; margin s when width-limited
}

/// Zoomed: the tile at `position` fits the view; neighbours share its height, `spacing` apart. A fractional
/// position blends the height and puts lerp(center[floor p], center[ceil p], frac p) at the view centre.
pub fn zoomed_layout(src: &[Size], view_w: f32, view_h: f32, spacing: f32, position: f32) -> Vec<FRect> {
    if !valid(src, view_w, view_h) {
        return Vec::new();
    }
    let s = clampf(spacing, 0.0, MAX_SPACING as f32);
    let fit_h = |z: Size| cmin(view_h, view_w * z.h / z.w);
    // Neighbouring pair around position (the end pair when outside the row; then t extrapolates).
    let last = src.len() - 1;
    let lo = clampf(position.floor(), 0.0, if last != 0 { (last - 1) as f32 } else { 0.0 }) as usize;
    let hi = (lo + 1).min(last);
    let t = position - lo as f32;
    let mut out = row(src, view_h, lerp(fit_h(src[lo]), fit_h(src[hi]), clampf(t, 0.0, 1.0)), s, 0.0);
    let centre = |out: &[FRect], i: usize| out[i].x + out[i].width / 2.0;
    let target =
        if lo == hi { centre(&out, lo) + t * (out[lo].width + s) } else { lerp(centre(&out, lo), centre(&out, hi), t) };
    for r in &mut out {
        r.x += view_w / 2.0 - target;
    }
    out
}

/// During an edge pan's landing, desktop `index` (the one being left) is on its own way out at `zoom`
/// (ViewAnimator::left_zoom). zoom 0: none.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Leaving {
    pub zoom: f32,
    pub index: usize,
}

/// Linear blend between the two (ViewAnimator supplies eased values); `zoom` clamped to [0,1].
pub fn blended_layout(src: &[Size], view_w: f32, view_h: f32, spacing: f32, zoom: f32, position: f32) -> Vec<FRect> {
    blended_layout_leaving(src, view_w, view_h, spacing, zoom, position, Leaving::default())
}

/// [`blended_layout`] with a desktop on its way out: its tile is as though the zoom were on it, and every other tile is
/// untouched.
pub fn blended_layout_leaving(
    src: &[Size],
    view_w: f32,
    view_h: f32,
    spacing: f32,
    zoom: f32,
    position: f32,
    leaving: Leaving,
) -> Vec<FRect> {
    let (left_zoom, left) = (leaving.zoom, leaving.index);
    let mut a = overview_layout(src, view_w, view_h, spacing);
    let b = zoomed_layout(src, view_w, view_h, spacing, position);
    let mix = |p: &FRect, q: &FRect, t: f32| FRect {
        x: lerp(p.x, q.x, t),
        y: lerp(p.y, q.y, t),
        width: lerp(p.width, q.width, t),
        height: lerp(p.height, q.height, t),
    };
    let t = clampf(zoom, 0.0, 1.0);
    let placed = if left < a.len() { a[left] } else { FRect::default() };
    for i in 0..a.len().min(b.len()) {
        a[i] = mix(&a[i], &b[i], t);
    }
    if left_zoom > 0.0 && left < a.len() && left_zoom > zoom_weight(t, position, left) {
        let own = zoomed_layout(src, view_w, view_h, spacing, left as f32);
        if left < own.len() {
            a[left] = mix(&placed, &own[left], clampf(left_zoom, 0.0, 1.0));
        }
    }
    a
}

/// Per-desktop placement in the glasses view (wire/profile units: tools/native_spike/geometry.py PLACEMENT_BOUNDS).
/// Relative to the desktop's slot in the overview/zoom layout; the default places it at its slot.
///   x, y       orbit round the eye, radians: x turns the placed desktop round you about the vertical (+x right, +-pi is
///              directly behind you), y lifts it along its own meridian (+y up, +-pi/2 straight overhead / underfoot).
///   distance   factor on the slot's distance (1 = as far as the slot, 2 = twice as far and half the apparent size)
///   curvature  horizontal cylindrical arc (radians) subtended by the desktop's width; 0 = flat. The arc length equals the
///              flat width and the centre tangent stays in place, so pixels bend toward the viewer and never stretch.
///   roll       levelling turn (radians, counter-clockwise as you see it) of the desktop about its own line of sight,
///              applied to the desktop drawn straight ahead before the orbit: it still faces you. Not a wire placement
///              setting: the supervisor sets it with the roll-level icons. A maximized desktop is drawn level.
/// Every desktop always faces you, upright along its meridian (a billboard toward the eye).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Placement {
    pub x: f32,
    pub y: f32,
    pub distance: f32,
    pub curvature: f32,
    pub roll: f32,
}

impl Default for Placement {
    fn default() -> Self {
        Self { x: 0.0, y: 0.0, distance: 1.0, curvature: 0.0, roll: 0.0 }
    }
}

impl Placement {
    pub const fn new(x: f32, y: f32, distance: f32, curvature: f32, roll: f32) -> Self {
        Self { x, y, distance, curvature, roll }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl Vec3 {
    pub const fn new(x: f32, y: f32, z: f32) -> Self {
        Self { x, y, z }
    }
}

/// Canvas pixels map onto the plane z = -1 spanning tan(half FOV) (GL frame: +x right, +y up, -z forward).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Canvas {
    pub view_w: f32,
    pub view_h: f32,
    pub half_w: f32,
    pub half_h: f32,
}

impl Canvas {
    pub const fn new(view_w: f32, view_h: f32, half_w: f32, half_h: f32) -> Self {
        Self { view_w, view_h, half_w, half_h }
    }
}

/// Where a ray from the eye (world space, any length) meets a desktop: tile uv and the world point (stereo.hpp). Used
/// for the pointer, which must sit on the surface it points at.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SurfaceHit {
    pub u: f32,
    pub v: f32,
    pub world: Vec3,
}

/// The nearest hit of `ray` on the desktop's drawn strip (Moeller-Trumbore from the eye at the origin), None for a miss.
pub fn stereo_surface_hit(
    canvas: &Canvas,
    tile: &FRect,
    placement: &Placement,
    ray: &Vec3,
) -> Option<SurfaceHit> {
    let mesh = tile_mesh(canvas, tile, placement, tile);
    let sub = |a: &Vec3, b: &Vec3| [a.x - b.x, a.y - b.y, a.z - b.z];
    let cross = |a: &[f32; 3], b: &[f32; 3]| {
        [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
    };
    let dot = |a: &[f32; 3], b: &[f32; 3]| a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    let d = [ray.x, ray.y, ray.z];
    let mut best = None;
    let mut nearest = f32::MAX;
    for w in mesh.windows(3) {
        let (a, b, c) = (&w[0], &w[1], &w[2]);
        let e1 = sub(&b.world, &a.world);
        let e2 = sub(&c.world, &a.world);
        let n = cross(&d, &e2);
        let det = dot(&e1, &n);
        if det.abs() < 1e-9 {
            continue;
        }
        let o = [-a.world.x, -a.world.y, -a.world.z];
        let s = dot(&o, &n) / det;
        if !(0.0..=1.0).contains(&s) {
            continue;
        }
        let q = cross(&o, &e1);
        let t = dot(&d, &q) / det;
        if t < 0.0 || s + t > 1.0 {
            continue;
        }
        let along = dot(&e2, &q) / det;
        if along <= 0.0 || along >= nearest {
            continue;
        }
        nearest = along;
        let r = 1.0 - s - t;
        best = Some(SurfaceHit {
            u: r * a.u + s * b.u + t * c.u,
            v: r * a.v + s * b.v + t * c.v,
            world: Vec3::new(ray.x * along, ray.y * along, ray.z * along),
        });
    }
    best
}

/// Below this the arc is numerically flat.
const MIN_CURVATURE: f32 = 1e-4;

/// World position of canvas pixel (px, py) on the surface of a desktop laid out at `tile` with `placement`.
/// Points outside the tile (its highlight frame/halo) continue on the same plane or cylinder. A centred tile at distance 1
/// with no orbit is exactly the canvas plane z = -1; an off-centre one turns to face you (its centre stays on the plane).
/// It is drawn straight ahead at the slot centre's distance times `distance`, then turned by Az(a0 + x) * El(e0 + y),
/// a0 / e0 the slot centre's azimuth / elevation (tools/native_spike/orbit.py place_point mirrors it exactly).
pub fn place_point(c: &Canvas, tile: &FRect, pl: &Placement, px: f32, py: f32) -> Vec3 {
    if c.view_w <= 0.0 || c.view_h <= 0.0 {
        return Vec3::default();
    }
    let sx = 2.0 * c.half_w / c.view_w; // world units per canvas pixel
    let sy = 2.0 * c.half_h / c.view_h;
    let cx = tile.x + tile.width / 2.0;
    let cy = tile.y + tile.height / 2.0;
    let tw = tile.width * sx;
    let d = if pl.distance > 0.0 { pl.distance } else { 1.0 };
    // The slot's centre on the canvas plane (z = -1): its direction (azimuth a0, elevation e0) and distance from the eye.
    let wx = (2.0 * cx / c.view_w - 1.0) * c.half_w;
    let wy = (1.0 - 2.0 * cy / c.view_h) * c.half_h;
    let a0 = wx.atan2(1.0);
    let e0 = wy.atan2((1.0 + wx * wx).sqrt());
    let r = (1.0 + wx * wx + wy * wy).sqrt() * d;
    let u = (px - cx) * sx;
    let v = (cy - py) * sy;
    let mut local = Vec3 { x: u, y: v, z: 0.0 };
    if pl.curvature > MIN_CURVATURE && tw > 0.0 {
        let rc = tw / pl.curvature;
        let phi = u / rc;
        local = Vec3 { x: rc * phi.sin(), y: v, z: rc * (1.0 - phi.cos()) }; // toward the viewer at the edges
    }
    if pl.roll != 0.0 {
        // levelling roll about the line of sight (the z axis here)
        let cr = pl.roll.cos();
        let sr = pl.roll.sin();
        local = Vec3 { x: cr * local.x - sr * local.y, y: sr * local.x + cr * local.y, z: local.z };
    }
    // Straight ahead, square to the line of sight; then up by e0 + y and round by a0 + x: it always faces the eye.
    let mut q = Vec3 { x: local.x, y: local.y, z: local.z - r };
    let ce = (e0 + pl.y).cos();
    let se = (e0 + pl.y).sin();
    q = Vec3 { x: q.x, y: ce * q.y - se * q.z, z: se * q.y + ce * q.z };
    let ca = (a0 + pl.x).cos();
    let sa = (a0 + pl.x).sin();
    Vec3 { x: ca * q.x - sa * q.z, y: q.y, z: sa * q.x + ca * q.z }
}

/// A [`tile_mesh`] vertex: `u`, `v` its position within the tile (0..1 on the tile itself), `px`, `py` canvas px.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct MeshVertex {
    pub world: Vec3,
    pub u: f32,
    pub v: f32,
    pub px: f32,
    pub py: f32,
}

/// Triangle-strip mesh drawing canvas rect `rect` on that surface: per column (left to right) a top then a bottom
/// vertex. One column when flat; more as curvature grows.
pub fn tile_mesh(c: &Canvas, tile: &FRect, pl: &Placement, rect: &FRect) -> Vec<MeshVertex> {
    if tile.width <= 0.0 || tile.height <= 0.0 || rect.width <= 0.0 || rect.height <= 0.0 {
        return Vec::new();
    }
    // ~3 degrees of arc per column across the tile; the strip covers `rect` with the same density.
    let span = rect.width / tile.width;
    let columns: i32 = if pl.curvature > MIN_CURVATURE {
        ((span * pl.curvature * 180.0 / PI / 3.0).ceil() as i32).clamp(4, 96)
    } else {
        1
    };
    let mut out = Vec::with_capacity((columns as usize + 1) * 2);
    for i in 0..=columns {
        let px = rect.x + rect.width * i as f32 / columns as f32;
        for py in [rect.y, rect.y + rect.height] {
            out.push(MeshVertex {
                world: place_point(c, tile, pl, px, py),
                u: (px - tile.x) / tile.width,
                v: (py - tile.y) / tile.height,
                px,
                py,
            });
        }
    }
    out
}

/// How far a placed desktop is rolled from level, as you see it: the signed angle (radians, counter-clockwise positive)
/// from `up` (the world's up as a vector in the canvas frame: pose canvas_up) projected onto the desktop's surface at its
/// centre, to the desktop's own up. Rolling the desktop by r adds r. None when the line of sight to its centre is within
/// TILT_MIN_DEG of straight up or down (level means nothing there) or anything is degenerate.
pub const TILT_MIN_DEG: f32 = 10.0;

/// The selected desktop's frame: this wide all round it, tile px on its surface, so it scales with the desktop.
pub const BORDER_PX: f32 = 6.0;

/// What glass hangs from: the middle of the border's outer edge under the desktop.
fn glass_anchor(tile: &FRect) -> [f32; 2] {
    [tile.x + tile.width / 2.0, tile.y + tile.height + BORDER_PX]
}

/// Glass under a desktop (its pill and the placement panel) keeps one apparent size whatever the desktop's size: a
/// resize (its `distance`) moves it with the desktop's bottom edge but never scales it. Glass point (gx, gy) is drawn at
/// the desktop surface point anchor + (g - anchor) * distance, so at distance 1 it is the tile's own px. The anchor is the
/// middle of the border's outer edge under the desktop, not of the desktop's own edge: the border is tile px and grows
/// with the desktop, so only from there does the glass keep one gap to it and stay off it at every size.
pub fn glass_to_desktop(tile: &FRect, pl: &Placement, gx: f32, gy: f32) -> [f32; 2] {
    let k = if pl.distance > 0.0 { pl.distance } else { 1.0 }; // as place_point reads it
    let [ax, ay] = glass_anchor(tile);
    [ax + (gx - ax) * k, ay + (gy - ay) * k]
}

/// tile_mesh for glass rect `rect`: world points through glass_to_desktop; px / py stay glass px (u, v as tile_mesh's).
pub fn glass_mesh(c: &Canvas, tile: &FRect, pl: &Placement, rect: &FRect) -> Vec<MeshVertex> {
    let k = if pl.distance > 0.0 { pl.distance } else { 1.0 };
    let [x, y] = glass_to_desktop(tile, pl, rect.x, rect.y);
    let mut out = tile_mesh(c, tile, pl, &FRect { x, y, width: rect.width * k, height: rect.height * k });
    let [ax, ay] = glass_anchor(tile);
    for v in &mut out {
        // back to glass px
        v.px = ax + (v.px - ax) / k;
        v.py = ay + (v.py - ay) / k;
    }
    out
}

/// Zooming into a desktop brings it to distance 1 (it fills the view): weight 1 removes its orbit and distance (curvature
/// is part of its shape and is kept). zoom_weight is zoom times closeness of `position` to `index`.
pub fn zoom_weight(zoom: f32, position: f32, index: usize) -> f32 {
    clampf(zoom, 0.0, 1.0) * cmax(0.0, 1.0 - (position - index as f32).abs())
}

pub fn faded(p: &Placement, weight: f32) -> Placement {
    let k = 1.0 - clampf(weight, 0.0, 1.0);
    Placement { x: p.x * k, y: p.y * k, distance: lerp(1.0, p.distance, k), curvature: p.curvature, roll: p.roll }
}

/// How much bigger a desktop is seen at zoom weight `w` than in the overview, `grown` being that at weight 1: g / lerp(g, 1,
/// w) with g = lerp(1, grown, w). One curve for every size, so a resized desktop moves just as a default one does, scaled
/// to its own two sizes. It only ever goes from 1 toward `grown`, whichever is bigger.
pub fn zoom_growth(w: f32, grown: f32) -> f32 {
    let k = clampf(w, 0.0, 1.0);
    let g = lerp(1.0, grown, k);
    g / lerp(g, 1.0, k)
}

/// Direction (azimuth, elevation) of a slot's centre from the eye, as place_point turns it.
#[derive(Clone, Copy, Default)]
struct Dir {
    az: f32,
    el: f32,
}

fn slot_direction(c: &Canvas, t: &FRect) -> Dir {
    if c.view_w <= 0.0 || c.view_h <= 0.0 {
        return Dir::default();
    }
    let wx = (2.0 * (t.x + t.width / 2.0) / c.view_w - 1.0) * c.half_w;
    let wy = (1.0 - 2.0 * (t.y + t.height / 2.0) / c.view_h) * c.half_h;
    Dir { az: wx.atan2(1.0), el: wy.atan2((1.0 + wx * wx).sqrt()) }
}

/// [`zoomed_placements_with`]'s options beyond the zoom itself.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct ZoomOptions {
    /// Anchor mode: a zoomed desktop resized bigger than the view (seen bigger than the fit) keeps its overview size, as
    /// every other one does, so it is never shrunk back to the fit. Any other zoomed desktop still grows to fill the view
    /// (zooming in must zoom: regression 2026-11-03, keeping every size made overview -> zoom a no-op). Follow and locked
    /// pass false: the zoomed desktop always fits the glasses.
    pub keep_size: bool,
    /// blended_layout_leaving's: that desktop is drawn as a zoomed one at that weight, whatever `zoom` and `position` say.
    pub leaving: Leaving,
}

/// The placements the desktops are drawn with at `zoom` / `position` (ViewAnimator's eased values), `overview` being the
/// overview layout (where the orbits were dragged) and `tiles` the blended layout being drawn. Zooming never moves a
/// desktop round you, in every viewing mode and however far it was dragged:
///   - every desktop keeps its overview direction (slot plus orbit) at every zoom: its orbit is corrected for its slot's
///     motion between the overview and `tiles`;
///   - the zoomed desktop's distance goes to 1 (zoom_weight), so it grows where it is until it fills the view there, its
///     seen size following zoom_growth all the way: never past either end, however it was resized;
///   - its roll fades to 0 with it, so maximized it sits level. Only what is drawn levels: `placements` keep the roll, so
///     the overview gets it back exactly and a saved layout never holds a levelled one. Every other desktop keeps its roll;
///   - every other desktop also keeps its overview size: its distance scales with its tile, so it is drawn exactly as in
///     the overview (a uniform scale about the eye), curvature included.
///
/// Nothing ever goes back to its default place or snaps straight ahead. zoom 0 (the overview): `placements` unchanged.
/// `placements` shorter than `tiles` leaves the rest at the default.
pub fn zoomed_placements(
    c: &Canvas,
    overview: &[FRect],
    tiles: &[FRect],
    placements: &[Placement],
    zoom: f32,
    position: f32,
) -> Vec<Placement> {
    zoomed_placements_with(c, overview, tiles, placements, zoom, position, ZoomOptions::default())
}

/// [`zoomed_placements`] with keep_size and a desktop on its way out.
pub fn zoomed_placements_with(
    c: &Canvas,
    overview: &[FRect],
    tiles: &[FRect],
    placements: &[Placement],
    zoom: f32,
    position: f32,
    options: ZoomOptions,
) -> Vec<Placement> {
    let (keep_size, left_zoom, left) = (options.keep_size, options.leaving.zoom, options.leaving.index);
    let n = tiles.len();
    let mut out = vec![Placement::default(); n];
    let m = n.min(placements.len());
    out[..m].copy_from_slice(&placements[..m]);
    let z = clampf(zoom, 0.0, 1.0);
    let away = if left < n { clampf(left_zoom, 0.0, 1.0) } else { 0.0 };
    if n == 0 || (z == 0.0 && away == 0.0) || overview.len() != n || c.view_w <= 0.0 || c.view_h <= 0.0 {
        return out;
    }
    // Distance from the eye of a slot's centre on the canvas plane (place_point's r at distance 1).
    let radius = |t: &FRect| {
        let wx = (2.0 * (t.x + t.width / 2.0) / c.view_w - 1.0) * c.half_w;
        let wy = (1.0 - 2.0 * (t.y + t.height / 2.0) / c.view_h) * c.half_h;
        (1.0 + wx * wx + wy * wy).sqrt()
    };
    for i in 0..n {
        let own = if i < placements.len() { placements[i] } else { Placement::default() };
        let was = &overview[i];
        let now = &tiles[i];
        let weight = cmax(if i == left { away } else { 0.0 }, zoom_weight(z, position, i));
        let roll = own.roll * (1.0 - weight);
        if !(was.height > 0.0) || !(now.height > 0.0) {
            out[i].roll = roll;
            continue;
        }
        // Held still round you: the orbit absorbs the slot's move from the overview to `tiles`.
        let ov = slot_direction(c, was);
        let cur = slot_direction(c, now);
        // Held at its overview size: every length (tile, radius, curvature's arc) scales by k about the eye.
        let d = if own.distance > 0.0 { own.distance } else { 1.0 };
        let rigid = d * (now.height / was.height) * radius(was) / radius(now);
        // Zoomed (weight 1): distance 1, it fills the view at the fit's height (centred, so no slot radius), `grown` times
        // what it was seen at. keep_size: only a desktop seen bigger than that (rigid < 1) keeps its size; anything
        // smaller still grows to fill the view (min: continuous where rigid crosses 1).
        let fit_h = cmin(c.view_h, if was.width > 0.0 { c.view_w * was.height / was.width } else { c.view_h });
        let grown = fit_h * d * radius(was) / was.height;
        let fit = if weight < 1.0 { rigid / zoom_growth(weight, grown) } else { 1.0 };
        out[i] = Placement {
            x: remainder(own.x + ov.az - cur.az, TWO_PI),
            y: own.y + ov.el - cur.el,
            distance: if keep_size { cmin(rigid, fit) } else { fit },
            curvature: own.curvature,
            roll,
        };
    }
    out
}

/// Where desktop `index` is round you: the direction [azimuth, elevation] (radians; +az right, +el up, place_point's) of
/// its centre as placed in the overview (its slot in `overview` plus its orbit), which zoomed_placements keeps at every
/// zoom. Follow and locked modes turn the canvas so the focused desktop's direction is straight ahead (pose aim_rotation).
/// [0, 0] if `index` is out of range or the canvas is empty.
pub fn focus_direction(c: &Canvas, overview: &[FRect], placements: &[Placement], index: usize) -> [f32; 2] {
    if index >= overview.len() || c.view_w <= 0.0 || c.view_h <= 0.0 {
        return [0.0, 0.0];
    }
    let s = slot_direction(c, &overview[index]);
    let p = if index < placements.len() { placements[index] } else { Placement::default() };
    [remainder(s.az + p.x, TWO_PI), s.el + p.y]
}

/// How far follow and locked modes centre the view on ViewAnimator::focus() (0 = the overview's own centre straight
/// ahead, 1 = the focused desktop straight ahead): zoom() + look(), at most 1. Continuous through zooms and every phase
/// of a switch. In anchor mode the room stays put and look_turn turns instead (by ViewAnimator::turn).
pub fn focus_weight(view: &ViewAnimator) -> f32 {
    clampf(view.zoom() + view.look(), 0.0, 1.0)
}

const IDENTITY: [f32; 9] = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

/// The view turn of a desktop switch at `look` (ViewAnimator::look): a rotation applied to world points before the head's
/// view (draw with view * look_turn), turning desktop `from`'s overview direction (slot plus its orbit) toward `to`'s. At
/// look 1 `to`'s direction comes exactly where `from`'s was, upright; at look 0 (and whenever an index is out of range) it
/// is the identity. Row-major, GL frame.
pub fn look_turn(c: &Canvas, overview: &[FRect], placements: &[Placement], from: usize, to: usize, look: f32) -> [f32; 9] {
    if from >= overview.len() || c.view_w <= 0.0 || c.view_h <= 0.0 {
        return IDENTITY;
    }
    let s = slot_direction(c, &overview[from]);
    let p = if from < placements.len() { placements[from] } else { Placement::default() };
    look_turn_from(c, overview, placements, [s.az + p.x, s.el + p.y], to, look)
}

/// The direction [azimuth, elevation] (focus_direction's) of the canvas point drawn at the centre of a view (a drawn
/// row-major GL view rotation: canvas -> eye, straight ahead -z).
pub fn view_centre(view: &[f32; 9]) -> [f32; 2] {
    // The canvas point the view puts straight ahead (0, 0, -1): view^T (0, 0, -1), minus the view's last row.
    let x = -view[6];
    let y = -view[7];
    let z = -view[8];
    [x.atan2(-z), y.atan2((x * x + z * z).sqrt())]
}

/// The same turn as [`look_turn`] from direction `from` ([azimuth, elevation], focus_direction's) instead of a desktop's:
/// a centred switch turns from view_centre(...) so at look 1 `to`'s direction is at the centre of the view it started from.
pub fn look_turn_from(
    c: &Canvas,
    overview: &[FRect],
    placements: &[Placement],
    from: [f32; 2],
    to: usize,
    look: f32,
) -> [f32; 9] {
    let mut m = IDENTITY;
    let u = clampf(look, 0.0, 1.0);
    if !(u > 0.0) || to >= overview.len() || c.view_w <= 0.0 || c.view_h <= 0.0 || !from[0].is_finite() || !from[1].is_finite()
    {
        return m;
    }
    let st = slot_direction(c, &overview[to]);
    let pt = if to < placements.len() { placements[to] } else { Placement::default() };
    let a = Dir { az: from[0], el: from[1] };
    let b = Dir { az: st.az + pt.x, el: st.el + pt.y };
    let l = Dir { az: a.az + u * remainder(b.az - a.az, TWO_PI), el: lerp(a.el, b.el, u) }; // the short way round
    // R(d) = Az(d.az) . El(d.el) carries straight ahead to direction d, upright (place_point's turn). m = R(a) . R(l)^T.
    let rot = |d: Dir| {
        let ca = d.az.cos();
        let sa = d.az.sin();
        let ce = d.el.cos();
        let se = d.el.sin();
        // Az: x' = ca x - sa z, z' = sa x + ca z. El: y' = ce y - se z, z' = se y + ce z.
        [ca, -sa * se, -sa * ce, 0.0, ce, -se, sa, ca * se, ca * ce]
    };
    let ra = rot(a);
    let rl = rot(l);
    for r in 0..3 {
        for col in 0..3 {
            let mut v = 0.0f32;
            for k in 0..3 {
                v += ra[r * 3 + k] * rl[col * 3 + k]; // R(l)^T[k][col] = R(l)[col][k]
            }
            m[r * 3 + col] = v;
        }
    }
    m
}

/// Moves `i`'s first occurrence to the end, the rest keeping their order (`std::rotate(it, it + 1, end)`).
fn to_back(order: &mut [usize], i: usize) {
    if let Some(at) = order.iter().position(|&x| x == i) {
        order[at..].rotate_left(1);
    }
}

/// Back-to-front draw order with the zoom target last (on top) once zoomed in past halfway (zoom >= 0.5): a desktop
/// dragged nearer than the zoomed one never covers it. Below that, `order` unchanged. `target` outside it: unchanged.
pub fn zoom_draw_order(mut order: Vec<usize>, zoom: f32, target: usize) -> Vec<usize> {
    if !(zoom >= 0.5) {
        return order;
    }
    to_back(&mut order, target);
    order
}

/// The two desktops of an edge pan in front of every other, whatever the zoom: `over` on top of `under`
/// (ViewAnimator::pan_layers). Their second border is `left` at `left_alpha`, its fade out as the other's comes up.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PanLayers {
    pub over: usize,
    pub under: usize,
    pub left: usize,
    pub left_alpha: f32,
}

pub fn pan_draw_order(mut order: Vec<usize>, pan: &PanLayers) -> Vec<usize> {
    for i in [pan.under, pan.over] {
        to_back(&mut order, i);
    }
    order
}

/// Back-to-front draw order by distance of each desktop's centre (painter's algorithm; intersecting desktops
/// are not split). `placements` shorter than `tiles` leaves the rest at the default.
pub fn draw_order(c: &Canvas, tiles: &[FRect], placements: &[Placement]) -> Vec<usize> {
    let depth: Vec<f32> = tiles
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let pl = if i < placements.len() { placements[i] } else { Placement::default() };
            let p = place_point(c, t, &pl, t.x + t.width / 2.0, t.y + t.height / 2.0);
            p.x * p.x + p.y * p.y + p.z * p.z
        })
        .collect();
    // std::stable_sort with `depth[a] > depth[b]`: a stable insertion sort on the same strict comparison (never
    // panics on a NaN, unlike a total-order sort).
    let mut order: Vec<usize> = (0..tiles.len()).collect();
    for i in 1..order.len() {
        let mut j = i;
        while j > 0 && depth[order[j]] > depth[order[j - 1]] {
            order.swap(j, j - 1);
            j -= 1;
        }
    }
    order
}

/// Overview free pointer (tools/native_spike/free_pointer.py): a small dim dot on the overview's plane (the canvas, z =
/// -1), or in its direction round you when the frame gives one (direction_disc; the supervisor always does). A grey disc
/// inside a black ring, so it reads on bright content without being bright itself. Levels at or under the cards' 191/255
/// cap (micro-OLED retention); the supervisor hides it after 30 s without pointer movement.
pub const FREE_POINTER_LEVEL: f32 = 170.0 / 255.0;
pub const FREE_POINTER_HELD_LEVEL: f32 = 184.0 / 255.0;
pub const FREE_POINTER_RADIUS_PX: f32 = 6.0;
pub const FREE_POINTER_RING_PX: f32 = 2.0;
pub const FREE_POINTER_SEGMENTS: i32 = 20;

fn canvas_at(c: &Canvas, px: f32, py: f32) -> Vec3 {
    Vec3 { x: (2.0 * px / c.view_w - 1.0) * c.half_w, y: (1.0 - 2.0 * py / c.view_h) * c.half_h, z: -1.0 }
}

/// Triangle fan (centre, then segments + 1 rim points, closed) of a disc of `radius` canvas px around canvas (x, y), on
/// the canvas plane. Empty if the canvas is empty, radius <= 0 or segments < 3.
pub fn canvas_disc(c: &Canvas, x: f32, y: f32, radius: f32, segments: i32) -> Vec<Vec3> {
    if c.view_w <= 0.0 || c.view_h <= 0.0 || !(radius > 0.0) || segments < 3 {
        return Vec::new();
    }
    let mut out = vec![canvas_at(c, x, y)];
    for i in 0..=segments {
        let a = 2.0 * PI * (i % segments) as f32 / segments as f32;
        out.push(canvas_at(c, x + radius * a.cos(), y + radius * a.sin()));
    }
    out
}

/// Points on the canvas plane drawn round canvas (cx, cy) (already clamped into the view), turned about the eye to
/// (az, el) by Az(az) . El(el - e) . Az(-a), (a, e) that point's own direction (direction_disc). In double, as the C++;
/// but cos / sin of `az` are the float functions there (a float argument picks std::cos(float)), so they are here too.
fn turn_to(c: &Canvas, cx: f32, cy: f32, az: f32, el: f32, fan: &mut [Vec3]) {
    let wx = (2.0 * f64::from(cx) / f64::from(c.view_w) - 1.0) * f64::from(c.half_w);
    let wy = (1.0 - 2.0 * f64::from(cy) / f64::from(c.view_h)) * f64::from(c.half_h);
    let a = wx.atan2(1.0);
    let e = wy.atan2(wx.hypot(1.0));
    let ca = (-a).cos();
    let sa = (-a).sin();
    let ce = (f64::from(el) - e).cos();
    let se = (f64::from(el) - e).sin();
    let cz = f64::from(az.cos());
    let sz = f64::from(az.sin());
    for v in fan.iter_mut() {
        let (vx, vy, vz) = (f64::from(v.x), f64::from(v.y), f64::from(v.z));
        let px = ca * vx - sa * vz; // Az(-a)
        let mut py = vy;
        let mut pz = sa * vx + ca * vz;
        let qy = ce * py - se * pz; // El(el - e)
        let qz = se * py + ce * pz;
        py = qy;
        pz = qz;
        *v = Vec3 { x: (cz * px - sz * pz) as f32, y: py as f32, z: (sz * px + cz * pz) as f32 }; // Az(az)
    }
}

/// The same disc drawn in direction (az, el) radians round you (az positive right, el positive up; the overview pointer
/// frame's az / el): canvas_disc at (x, y) clamped into the view, turned about the eye by Az(az) . El(el - e) . Az(-a),
/// (a, e) that clamped point's direction, so its centre lands on (az, el), it stays upright, and where (az, el) is that
/// point's own direction it is canvas_disc exactly. Empty under canvas_disc's conditions.
pub fn direction_disc(c: &Canvas, x: f32, y: f32, az: f32, el: f32, radius: f32, segments: i32) -> Vec<Vec3> {
    let cx = cclamp(x, 0.0, cmax(c.view_w, 0.0));
    let cy = cclamp(y, 0.0, cmax(c.view_h, 0.0));
    let mut fan = canvas_disc(c, cx, cy, radius, segments);
    if !fan.is_empty() {
        turn_to(c, cx, cy, az, el, &mut fan);
    }
    fan
}

/// The dot's look (free_pointer.py POINTER_LOOKS, the `overview_pointer` frame's `look`): it stays one cursor and shows
/// what a press would do there. plain over content, empty space and controls; two small arrowheads either side of the
/// disc along the resize direction on a desktop's edge or corner (ew: left / right edges, ns: top / bottom, nwse:
/// top-left / bottom-right corners, nesw: top-right / bottom-left); four on a pill (move); two chasing round the disc on
/// a level bar (rotate). text over a desktop's content where the app asks for a text cursor: the Serif Beam, which stands
/// in for the disc rather than going round it, centred on the click point.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(u8)]
pub enum PointerLook {
    #[default]
    Plain,
    ResizeEw,
    ResizeNs,
    ResizeNwse,
    ResizeNesw,
    Move,
    Rotate,
    Text,
}

/// Past the ring, canvas px.
pub const POINTER_MARK_GAP_PX: f32 = 2.5;
pub const POINTER_MARK_LEN_PX: f32 = 5.0;
pub const POINTER_MARK_HALF_PX: f32 = 3.5;
/// The Serif Beam (canvas px from the click point): a stem 2 px wide, so it keeps its width as the head moves (a width
/// like 2.5 would flip between 2 and 3 with no anti-aliasing), between two rounded serifs. With the ring it is 14 x 28 px;
/// the serifs' middles are on the stem's ends.
pub const BEAM_HALF_PX: f32 = 11.0;
pub const BEAM_STEM_HALF_PX: f32 = 1.0;
pub const BEAM_SERIF_HALF_PX: f32 = 4.0;
pub const BEAM_SERIF_RADIUS_PX: f32 = 1.0;

/// Triangles (three vertices each, GL_TRIANGLES) of look `look`'s marks round the dot at canvas (x, y), or of the Serif
/// Beam for text, turned to (az, el) exactly as direction_disc turns the dot, so they stay round it anywhere round you and
/// stay upright. `grow` px fattens each (the black outline drawn under them, as the ring is under the disc). Empty for
/// plain or an empty canvas.
pub fn direction_marks(c: &Canvas, x: f32, y: f32, az: f32, el: f32, look: PointerLook, grow: f32) -> Vec<Vec3> {
    if c.view_w <= 0.0 || c.view_h <= 0.0 || look == PointerLook::Plain {
        return Vec::new();
    }
    let cx = cclamp(x, 0.0, c.view_w);
    let cy = cclamp(y, 0.0, c.view_h);
    let at = |px: f32, py: f32| canvas_at(c, px, py);
    let start = FREE_POINTER_RADIUS_PX + FREE_POINTER_RING_PX + POINTER_MARK_GAP_PX; // from the dot's centre, past its ring
    // float(M_SQRT1_2): the double constant rounded to float.
    let d = std::f64::consts::FRAC_1_SQRT_2 as f32;
    let mut out: Vec<Vec3> = Vec::new();
    // One arrowhead: its base centred `base` px from the dot along (bx, by) (canvas px, y down), pointing along (ux, uy).
    let mark = |out: &mut Vec<Vec3>, bx: f32, by: f32, base: f32, ux: f32, uy: f32| {
        let ox = cx + bx * base - ux * grow;
        let oy = cy + by * base - uy * grow;
        let len = POINTER_MARK_LEN_PX + 2.0 * grow;
        let half = POINTER_MARK_HALF_PX + grow;
        out.push(at(ox + ux * len, oy + uy * len));
        out.push(at(ox - uy * half, oy + ux * half));
        out.push(at(ox + uy * half, oy - ux * half));
    };
    let pair = |out: &mut Vec<Vec3>, ux: f32, uy: f32| {
        mark(out, ux, uy, start, ux, uy);
        mark(out, -ux, -uy, start, -ux, -uy);
    };
    // The Serif Beam's stem and one serif, from the click point. A serif is a capsule lying across the stem's end, one
    // fan of FREE_POINTER_SEGMENTS + 2 rim points; the triangles are the mockup's (serif_beam_shape.json).
    let stem = |out: &mut Vec<Vec3>| {
        let l = cx - BEAM_STEM_HALF_PX - grow;
        let r = cx + BEAM_STEM_HALF_PX + grow;
        let t = cy - BEAM_HALF_PX - grow;
        let b = cy + BEAM_HALF_PX + grow;
        for (px, py) in [(l, t), (r, t), (r, b), (l, t), (r, b), (l, b)] {
            out.push(at(px, py));
        }
    };
    let serif = |out: &mut Vec<Vec3>, y: f32| {
        let half = FREE_POINTER_SEGMENTS / 2;
        let radius = BEAM_SERIF_RADIUS_PX + grow;
        let mut rim: Vec<(f32, f32)> = Vec::new(); // along the serif (x) and across it (y), from its middle
        for end in [-BEAM_SERIF_HALF_PX, BEAM_SERIF_HALF_PX] {
            for i in 0..=half {
                let a = PI * (i as f32 / half as f32 + if end < 0.0 { 1.0 } else { 0.0 });
                rim.push((end + radius * a.sin(), radius * a.cos()));
            }
        }
        for i in 0..rim.len() {
            let (ax, ay) = rim[i];
            let (bx, by) = rim[(i + 1) % rim.len()];
            out.push(at(cx, cy + y));
            out.push(at(cx + ax, cy + y + ay));
            out.push(at(cx + bx, cy + y + by));
        }
    };
    match look {
        PointerLook::ResizeEw => pair(&mut out, 1.0, 0.0),
        PointerLook::ResizeNs => pair(&mut out, 0.0, 1.0),
        PointerLook::ResizeNwse => pair(&mut out, d, d),
        PointerLook::ResizeNesw => pair(&mut out, d, -d),
        PointerLook::Move => {
            pair(&mut out, 1.0, 0.0);
            pair(&mut out, 0.0, 1.0);
        }
        PointerLook::Rotate => {
            // left and right of the dot, chasing each other clockwise
            mark(&mut out, -1.0, 0.0, start + POINTER_MARK_HALF_PX, 0.0, -1.0);
            mark(&mut out, 1.0, 0.0, start + POINTER_MARK_HALF_PX, 0.0, 1.0);
        }
        PointerLook::Text => {
            stem(&mut out);
            serif(&mut out, -BEAM_HALF_PX);
            serif(&mut out, BEAM_HALF_PX);
        }
        PointerLook::Plain => {}
    }
    turn_to(c, cx, cy, az, el, &mut out);
    out
}

/// Window drag (free_pointer.py): the dragged window's ghost, a w x h canvas px rectangle outline `thickness` px wide (on
/// the inside of w x h), centred on canvas (x, y) and turned to (az, el) exactly as direction_disc turns the dot. Its grey
/// stays under the cards' cap like the dot's.
pub const GHOST_THICKNESS_PX: f32 = 2.0;
pub const GHOST_LEVEL: f32 = 150.0 / 255.0;
/// The desktop a dragged window would land on: an accent frame DROP_FRAME_PX wide just inside its edge, on its surface.
pub const DROP_FRAME_PX: f32 = 4.0;

/// That frame: top, bottom, left and right bands (canvas px on the desktop's tile), DROP_FRAME_PX wide just inside its
/// edge (shrunk to fit a tile narrower than two bands).
pub fn drop_frame(t: &FRect) -> [FRect; 4] {
    let bx = cmin(DROP_FRAME_PX, cmax(t.width, 0.0) / 2.0);
    let by = cmin(DROP_FRAME_PX, cmax(t.height, 0.0) / 2.0);
    [
        FRect::new(t.x, t.y, t.width, by),
        FRect::new(t.x, t.y + t.height - by, t.width, by),
        FRect::new(t.x, t.y + by, bx, t.height - 2.0 * by),
        FRect::new(t.x + t.width - bx, t.y + by, bx, t.height - 2.0 * by),
    ]
}

/// The ghost: a triangle strip (outer / inner corner pairs, closed: 10 vertices), `size` the w x h above. Empty if the
/// canvas is empty, w or h <= 0, or thickness <= 0.
pub fn direction_outline(c: &Canvas, x: f32, y: f32, az: f32, el: f32, size: Size, thickness: f32) -> Vec<Vec3> {
    let Size { w, h } = size;
    if c.view_w <= 0.0 || c.view_h <= 0.0 || !(w > 0.0) || !(h > 0.0) || !(thickness > 0.0) {
        return Vec::new();
    }
    let cx = cclamp(x, 0.0, c.view_w);
    let cy = cclamp(y, 0.0, c.view_h);
    let ow = w / 2.0;
    let oh = h / 2.0;
    let iw = cmax(ow - thickness, 0.0);
    let ih = cmax(oh - thickness, 0.0);
    const SX: [f32; 4] = [-1.0, 1.0, 1.0, -1.0];
    const SY: [f32; 4] = [-1.0, -1.0, 1.0, 1.0];
    let mut strip = Vec::with_capacity(10);
    for k in 0..=4 {
        let i = k % 4;
        strip.push(canvas_at(c, cx + SX[i] * ow, cy + SY[i] * oh));
        strip.push(canvas_at(c, cx + SX[i] * iw, cy + SY[i] * ih));
    }
    turn_to(c, cx, cy, az, el, &mut strip);
    strip
}

/// The supervisor's free-pointer hit testing needs the overview's layout: one status line
/// {"overview_geometry":{"w":W,"h":H,"half":[half_w,half_h],"tiles":[[x,y,w,h],...]}} with canvas px to 2 decimals and
/// the half-FOV tangents to 5 (tools/native_spike/free_pointer.py parse_overview_geometry), one tile per desktop
/// (1..MAX_DESKTOPS). Empty if anything is unusable.
pub fn overview_geometry_json(tiles: &[FRect], width: u32, height: u32, half_w: f32, half_h: f32) -> String {
    // One slot per desktop, as many as the run has (1..MAX_DESKTOPS): the supervisor's dot, pills and level bars need it.
    if tiles.is_empty()
        || tiles.len() > MAX_DESKTOPS
        || width == 0
        || height == 0
        || width > 8192
        || height > 8192
        || !(half_w > 0.0)
        || !(half_h > 0.0)
        || !half_w.is_finite()
        || !half_h.is_finite()
    {
        return String::new();
    }
    // The C++ formats into char[96] with snprintf, which keeps at most 95 bytes: huge tangents are cut off there.
    const BUF: usize = 95;
    let cut = |mut s: String| {
        s.truncate(BUF);
        s
    };
    let mut out = cut(format!(
        "{{\"overview_geometry\":{{\"w\":{width},\"h\":{height},\"half\":[{:.5},{:.5}],\"tiles\":[",
        f64::from(half_w),
        f64::from(half_h)
    ));
    for (i, t) in tiles.iter().enumerate() {
        if !t.x.is_finite()
            || !t.y.is_finite()
            || !(t.width > 0.0)
            || !(t.height > 0.0)
            || !t.width.is_finite()
            || !t.height.is_finite()
            || t.x.abs() > 8192.0
            || t.y.abs() > 8192.0
            || t.width > 8192.0
            || t.height > 8192.0
        {
            return String::new();
        }
        out += &cut(format!(
            "{}[{:.2},{:.2},{:.2},{:.2}]",
            if i != 0 { "," } else { "" },
            f64::from(t.x),
            f64::from(t.y),
            f64::from(t.width),
            f64::from(t.height)
        ));
    }
    out + "]}}"
}

/// Placement easing: the drawn placement approaches the latest one with time constant `tau_ms` (exponential, frame-rate
/// independent) and lands on it exactly once within a hair, so drags at the pointer's ~30-60 Hz look smooth at 120 Hz.
/// The orbit's x eases the short way round, so a desktop crossing behind you never swings back through the front.
pub fn ease_toward(from: &Placement, to: &Placement, dt_ms: f32, tau_ms: f32) -> Placement {
    if tau_ms <= 0.0 {
        return *to;
    }
    if !(dt_ms > 0.0) {
        return *from;
    }
    let k = (-dt_ms / tau_ms).exp();
    let step = |a: f32, b: f32| {
        let v = b + (a - b) * k;
        if (v - b).abs() < 2e-4 { b } else { v }
    };
    // The orbit's turn round you: the short way (a desktop crossing behind you stays behind you).
    let turn = remainder(from.x - to.x, TWO_PI);
    Placement {
        x: step(to.x + turn, to.x),
        y: step(from.y, to.y),
        distance: step(from.distance, to.distance),
        curvature: step(from.curvature, to.curvature),
        roll: step(from.roll, to.roll),
    }
}

/// Exact equality of every field.
pub fn same_placement(a: &Placement, b: &Placement) -> bool {
    a.x == b.x && a.y == b.y && a.distance == b.distance && a.curvature == b.curvature && a.roll == b.roll
}

pub const PLACEMENT_EASE_MS: f32 = 35.0;

/// A glide (a `placement` frame's glide: a discrete edit that bumped desktops out of the way, bump.py): `t` of the way
/// (0..1, clamped) from `from` to `to` along smootherstep, so it starts and lands with no jump in speed, never overshoots
/// and is exactly `to` at t >= 1. Every desktop of the frame takes the same t, so they move out of each other's way
/// together. x the short way round, as ease_toward.
pub fn glide_toward(from: &Placement, to: &Placement, t: f32) -> Placement {
    if !(t < 1.0) {
        return *to;
    }
    let s = smootherstep(t);
    let at = |a: f32, b: f32| a + (b - a) * s;
    let turn = remainder(to.x - from.x, TWO_PI); // the short way round
    Placement {
        x: from.x + turn * s,
        y: at(from.y, to.y),
        distance: at(from.distance, to.distance),
        curvature: at(from.curvature, to.curvature),
        roll: at(from.roll, to.roll),
    }
}

/// The renderer's drawn placements' glide: while one is under way (ms > 0) every desktop is glide_toward(from[i],
/// targets[i], (now - start) / ms), and once it has landed the glide ends (ms = 0).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PlacementGlide {
    pub from: Vec<Placement>,
    pub start_ms: f64,
    pub ms: f32,
}

/// Begins a glide from what is drawn now (false, and any glide ends, when ms is 0 or nothing is drawn yet).
pub fn start_glide(glide: &mut PlacementGlide, drawn: &[Placement], now_ms: f64, ms: u32) -> bool {
    glide.ms = 0.0;
    if ms == 0 || drawn.is_empty() {
        return false;
    }
    glide.from = drawn.to_vec();
    glide.start_ms = now_ms;
    glide.ms = ms as f32;
    true
}

/// The renderer's drawn placements, one step to `now_ms` toward `targets` (resized to match, new ones at their target):
/// the glide while one is under way, otherwise each eases toward its target over `dt_ms` (ease_toward).
pub fn step_placements(
    drawn: &mut Vec<Placement>,
    targets: &[Placement],
    dt_ms: f32,
    now_ms: f64,
    glide: &mut PlacementGlide,
) {
    let had = drawn.len();
    drawn.resize(targets.len(), Placement::default());
    drawn[had..].copy_from_slice(&targets[had..]);
    if glide.ms > 0.0 {
        let t = ((now_ms - glide.start_ms) / f64::from(glide.ms)) as f32;
        for i in 0..drawn.len() {
            let from = if i < glide.from.len() { glide.from[i] } else { targets[i] };
            drawn[i] = glide_toward(&from, &targets[i], t);
        }
        if !(t < 1.0) {
            glide.ms = 0.0; // landed exactly
        }
        return;
    }
    for i in 0..drawn.len() {
        drawn[i] = ease_toward(&drawn[i], &targets[i], dt_ms, PLACEMENT_EASE_MS);
    }
}

/// Overview tiles through a desktop change the supervisor asks to ease (the `desktops` frame's `ease`): today only
/// Ctrl+Alt+R (tools/native_spike/view.py). The overview fits its row to the view, so a desktop more or less, or one new
/// aspect, changes every tile at once, and without this they jump. Armed for TILE_EASE_ARM_MS from the last such frame,
/// any change of the captures or their sizes takes every tile from where it is drawn to the new layout along one
/// smootherstep over TILE_EASE_MS (view.py RESET_ANIM_S); a further change starts again from where they are. A new
/// desktop grows out of its slot from TILE_EASE_BORN_SCALE of its size. Overview only: a zoom drops it, and an expired
/// arm leaves a later change to jump as usual. Unarmed it costs one comparison a frame.
pub const TILE_EASE_MS: f32 = 450.0;
pub const TILE_EASE_ARM_MS: f32 = 2000.0;
pub const TILE_EASE_BORN_SCALE: f32 = 0.02;

/// [`TileEase::tiles`]'s answer: the caller's own target layout, or the ease's buffer while on the way to it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Tiles<'a> {
    Target(&'a [FRect]),
    Eased(&'a [FRect]),
}

impl<'a> Tiles<'a> {
    pub fn is_target(&self) -> bool {
        matches!(self, Tiles::Target(_))
    }
    pub fn as_slice(&self) -> &'a [FRect] {
        match *self {
            Tiles::Target(s) | Tiles::Eased(s) => s,
        }
    }
}

impl Deref for Tiles<'_> {
    type Target = [FRect];
    fn deref(&self) -> &[FRect] {
        self.as_slice()
    }
}

#[derive(Clone, Debug)]
pub struct TileEase {
    from: [FRect; MAX_DESKTOPS], // by capture index; width 0: none yet (a new desktop)
    shown: [FRect; MAX_DESKTOPS],
    sizes: [Size; MAX_DESKTOPS],
    out: Vec<FRect>,
    count: usize,
    changed: bool,
    armed_until_ms: f64,
    start_ms: f64,
}

impl Default for TileEase {
    fn default() -> Self {
        Self {
            from: [FRect::default(); MAX_DESKTOPS],
            shown: [FRect::default(); MAX_DESKTOPS],
            sizes: [Size::default(); MAX_DESKTOPS],
            out: Vec::new(),
            count: 0,
            changed: false,
            armed_until_ms: -1.0,
            start_ms: -1.0,
        }
    }
}

impl TileEase {
    pub fn new() -> Self {
        Self::default()
    }

    /// The next changes ease. `sizes` and `drawn`: the captures' sizes and tiles now, before the change.
    pub fn arm(&mut self, sizes: &[Size], drawn: &[FRect], now_ms: f64) {
        if !self.armed(now_ms) && !self.active() {
            // already armed: it has been following the tiles
            let n = drawn.len().min(self.shown.len());
            self.shown = [FRect::default(); MAX_DESKTOPS];
            self.shown[..n].copy_from_slice(&drawn[..n]);
            self.keep(sizes);
            self.changed = false;
        }
        self.armed_until_ms = now_ms + f64::from(TILE_EASE_ARM_MS);
    }

    pub fn armed(&self, now_ms: f64) -> bool {
        now_ms <= self.armed_until_ms
    }

    /// On the way: the renderer keeps drawing.
    pub fn active(&self) -> bool {
        self.start_ms >= 0.0
    }

    /// The captures changed: `before[i]` is new capture i's index before the change, -1 for a new desktop.
    pub fn reindex(&mut self, before: &[i32]) {
        let was = self.shown;
        let mut same = before.len() == self.count; // the finished change's frame names the same desktops: the ease just goes on
        self.shown = [FRect::default(); MAX_DESKTOPS];
        for i in 0..before.len().min(self.shown.len()) {
            same = same && before[i] == i as i32;
            if before[i] >= 0 && (before[i] as usize) < was.len() {
                self.shown[i] = was[before[i] as usize];
            }
        }
        self.changed = self.changed || !same;
    }

    /// This frame's tiles: `target` (the layout) unless they are on their way to it.
    pub fn tiles<'a>(&'a mut self, sizes: &[Size], target: &'a [FRect], overview: bool, now_ms: f64) -> Tiles<'a> {
        if !self.active() && !self.armed(now_ms) {
            return Tiles::Target(target);
        }
        let n = target.len();
        if !overview || n > self.shown.len() {
            self.start_ms = -1.0;
            self.armed_until_ms = -1.0;
            return Tiles::Target(target);
        }
        if self.changed || !self.kept(sizes) {
            self.changed = false;
            self.keep(sizes);
            self.start_ms = if self.armed(now_ms) { now_ms } else { -1.0 };
            self.from = self.shown;
            for (from, t) in self.from.iter_mut().zip(target) {
                if from.width > 0.0 {
                    continue;
                }
                // a new desktop: out of the middle of its slot
                let k = TILE_EASE_BORN_SCALE;
                *from = FRect::new(
                    t.x + t.width * (1.0 - k) / 2.0,
                    t.y + t.height * (1.0 - k) / 2.0,
                    t.width * k,
                    t.height * k,
                );
            }
        }
        let t = if self.active() { ((now_ms - self.start_ms) / f64::from(TILE_EASE_MS)) as f32 } else { 1.0 };
        if !(t < 1.0) {
            self.start_ms = -1.0;
            self.shown[..n].copy_from_slice(target);
            return Tiles::Target(target);
        }
        let e = smootherstep(t);
        self.out.resize(n, FRect::default());
        for (i, &b) in target.iter().enumerate() {
            let a = self.from[i];
            let r = FRect::new(lerp(a.x, b.x, e), lerp(a.y, b.y, e), lerp(a.width, b.width, e), lerp(a.height, b.height, e));
            self.out[i] = r;
            self.shown[i] = r;
        }
        Tiles::Eased(&self.out)
    }

    fn keep(&mut self, sizes: &[Size]) {
        self.count = sizes.len().min(self.sizes.len());
        self.sizes[..self.count].copy_from_slice(&sizes[..self.count]);
    }

    fn kept(&self, sizes: &[Size]) -> bool {
        sizes.len() == self.count && sizes.iter().zip(&self.sizes).all(|(a, b)| a.w == b.w && a.h == b.h)
    }
}

/// See [`TILT_MIN_DEG`].
pub fn desktop_tilt(c: &Canvas, t: &FRect, pl: &Placement, up: &Vec3) -> Option<f32> {
    if c.view_w <= 0.0 || c.view_h <= 0.0 || !(t.width > 0.0) || !(t.height > 0.0) {
        return None;
    }
    let cx = t.x + t.width / 2.0;
    let cy = t.y + t.height / 2.0;
    let o = place_point(c, t, pl, cx, cy);
    let top = place_point(c, t, pl, cx, cy - 1.0);
    let right = place_point(c, t, pl, cx + 1.0, cy);
    let sub = |a: Vec3, b: Vec3| Vec3 { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
    let dot = |a: Vec3, b: Vec3| a.x * b.x + a.y * b.y + a.z * b.z;
    let on = dot(o, o).sqrt();
    let un = dot(*up, *up).sqrt();
    if !(on > 0.0) || !(un > 0.0) || !on.is_finite() || !un.is_finite() {
        return None;
    }
    let n = Vec3 { x: o.x / on, y: o.y / on, z: o.z / on };
    let g = Vec3 { x: up.x / un, y: up.y / un, z: up.z / un };
    let along = dot(g, n);
    let flat = Vec3 { x: g.x - along * n.x, y: g.y - along * n.y, z: g.z - along * n.z }; // up, on the surface
    if dot(flat, flat) < (TILT_MIN_DEG * PI / 180.0).sin().powf(2.0) {
        return None;
    }
    // A desktop turned counter-clockwise by r sees the world's up turned clockwise by r in its own frame: toward its right.
    let r = sub(right, o);
    let u = sub(top, o);
    let rn = dot(r, r).sqrt();
    let uln = dot(u, u).sqrt();
    if !(rn > 0.0) || !(uln > 0.0) {
        return None;
    }
    let a = (dot(flat, r) / rn).atan2(dot(flat, u) / uln);
    if a.is_finite() { Some(a) } else { None }
}

/// How long the view's own changes take: zoom, spacing (ViewAnimator) and a desktop's shape (SizeEase), all with
/// smoothstep.
pub const VIEW_EASE_MS: f32 = 220.0;

/// One desktop's size as the layout draws it. When its output is resized live to a new shape (a glass pill's Resolution
/// dropdown or Ultrawide) the frame at the new size appears at once, in a tile whose aspect eases to the new one, so the
/// tile widens or narrows smoothly and, every layout being computed from these sizes, its neighbours slide along with it.
/// The aspect is what eases (a tile's width in the row is proportional to it), so a new resolution of the same shape
/// starts nothing. A change that comes mid-way carries on from the aspect drawn then. advance() once per drawn frame;
/// with `animate` false (a desktop's first size) the new size just applies. Until the first advance() value() is None.
#[derive(Clone, Debug)]
pub struct SizeEase {
    duration: f32,
    t: f32,
    from: f32,
    start_ms: f64,
    to: Option<Size>,
    eases: u64,
}

impl Default for SizeEase {
    fn default() -> Self {
        Self::new(VIEW_EASE_MS)
    }
}

impl SizeEase {
    pub fn new(duration_ms: f32) -> Self {
        Self { duration: duration_ms, t: 1.0, from: 0.0, start_ms: 0.0, to: None, eases: 0 }
    }

    pub fn advance(&mut self, target: Size, now_ms: f64, animate: bool) {
        if self.to.is_none_or(|to| target.w != to.w || target.h != to.h) {
            let shown = self.value();
            let shaped = shown.is_some_and(|s| s.h > 0.0 && target.h > 0.0 && s.w / s.h != target.w / target.h);
            if animate && shaped && self.duration > 0.0 {
                let s = shown.expect("shaped implies shown");
                self.from = s.w / s.h;
                self.start_ms = now_ms;
                self.t = 0.0;
                self.eases += 1;
            } else {
                self.t = 1.0;
            }
            self.to = Some(target);
        }
        if self.t < 1.0 {
            self.t = clampf(((now_ms - self.start_ms) / f64::from(self.duration)) as f32, 0.0, 1.0);
        }
    }

    pub fn value(&self) -> Option<Size> {
        match self.to {
            Some(to) if self.t < 1.0 => Some(Size { w: to.h * lerp(self.from, to.w / to.h, smoothstep(self.t)), h: to.h }),
            other => other,
        }
    }

    pub fn animating(&self) -> bool {
        self.t < 1.0
    }

    /// How many started (diagnostic).
    pub fn eases(&self) -> u64 {
        self.eases
    }
}

/// The edge pan (tools/native_spike/edge_pan.py, the `edge_pan` frame): maximized, the hand pushing the cursor past
/// `side` of desktop `from` toward `to`. All of it is the supervisor's measure of the hand; this side only draws it.
///   turn   radians the view has turned toward `to`, 1:1 with the hand from start to end of the pan.
///   way    radians from the view on `from` to the view on `to`, the way the push goes (pose aim_along). The whole pan,
///          hand and landing, is this one turn.
///   peek   0..1 of the hand's whole way. The zoom falls from 1 to `zoom` with it.
///   cross  0..1: the dot, which has ridden the desktop's edge until now, leaves it at exit point E (`exit` along `side`
///          of `from`, drawn at zoom `zoom`) along the great circle to entry point P (`entry` along the opposite edge of
///          `to`, in its placed overview position), at the rate it had on the desktop.
/// `zoom` is as far as this pair needs (edge_pan.py pan_zoom), and that is as far as the hand takes it.
/// side: 0 right, 1 left, 2 up, 3 down.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct EdgePan {
    pub active: bool,
    pub from: u32,
    pub to: u32,
    pub side: u8,
    pub exit: f32,
    pub entry: f32,
    pub peek: f32,
    pub cross: f32,
    pub turn: f32,
    pub way: f32,
    pub zoom: f32,
}

/// edge_pan_path: two points closer than this give no direction of their own (a row's gap).
pub const PAN_AXIS_MIN_RAD: f32 = 0.1;
/// Time constant taking the hand's ~250 Hz samples to the display's frames.
pub const PAN_EASE_MS: f32 = 30.0;
/// This near it has arrived: well under one wire step, or a gentle hand's lag would count as arrived and the ease would
/// do nothing.
pub const PAN_EASE_REST: f32 = 1e-5;
/// No frame for this long (the supervisor stopped): the view goes back to the desktop.
pub const PAN_STALE_MS: f32 = 2000.0;
/// The landing, from the hand's last frame to the target maximized: the size of it, and the longest the view's turn takes.
pub const PAN_SETTLE_MS: f32 = 520.0;
/// Of the landing: the border goes over from the desktop being left to the target.
pub const PAN_BORDER_SHARE: f32 = 0.25;

/// The pixel `f` (0..1, left / top first) along `side` of `tile`.
pub fn edge_point(t: &FRect, side: u8, f: f32) -> [f32; 2] {
    let k = clampf(f, 0.0, 1.0);
    match side {
        0 => [t.x + t.width, t.y + t.height * k],
        1 => [t.x, t.y + t.height * k],
        2 => [t.x + t.width * k, t.y],
        _ => [t.x + t.width * k, t.y + t.height],
    }
}

/// The dot's way across (canvas frame, unit vectors): `from` E, `to` P, `axis` the turn from one to the other (the
/// overview pan's), `angle` how far apart, radians.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct EdgePanPath {
    pub from: Vec3,
    pub to: Vec3,
    pub axis: Vec3,
    pub angle: f32,
}

fn dot3(a: &Vec3, b: &Vec3) -> f32 {
    a.x * b.x + a.y * b.y + a.z * b.z
}

fn cross3(a: &Vec3, b: &Vec3) -> Vec3 {
    Vec3 { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }
}

fn unit(v: &Vec3) -> Option<Vec3> {
    let n = dot3(v, v).sqrt();
    if !(n > 1e-6) || !n.is_finite() {
        return None;
    }
    Some(Vec3 { x: v.x / n, y: v.y / n, z: v.z / n })
}

/// None if an index is out of range or the canvas is empty.
pub fn edge_pan_path(
    c: &Canvas,
    src: &[Size],
    spacing: f32,
    overview: &[FRect],
    placements: &[Placement],
    pan: &EdgePan,
) -> Option<EdgePanPath> {
    let n = overview.len();
    let (from_i, to_i) = (pan.from as usize, pan.to as usize);
    if from_i >= n || to_i >= n || pan.from == pan.to || src.len() != n || c.view_w <= 0.0 || c.view_h <= 0.0 {
        return None;
    }
    // The placements as the supervisor reads them: a maximized desktop is level, and it measures this same way across.
    let mut level = [Placement::default(); MAX_DESKTOPS];
    let count = n.min(level.len());
    for (i, l) in level.iter_mut().enumerate().take(count) {
        if i < placements.len() {
            *l = placements[i];
        }
        l.roll = 0.0;
    }
    let placed = &level[..count];
    let tiles = blended_layout(src, c.view_w, c.view_h, spacing, pan.zoom, pan.from as f32);
    let drawn = zoomed_placements(c, overview, &tiles, placed, pan.zoom, pan.from as f32);
    if tiles.len() != n || drawn.len() != n {
        return None;
    }
    let e = edge_point(&tiles[from_i], pan.side, pan.exit);
    let opposite = pan.side ^ 1; // right <-> left, up <-> down
    let p = edge_point(&overview[to_i], opposite, pan.entry);
    let from = unit(&place_point(c, &tiles[from_i], &drawn[from_i], e[0], e[1]));
    // `placed` is shorter than `overview` past MAX_DESKTOPS desktops: the C++ reads past it there; the default instead.
    let placed_at = |i: usize| placed.get(i).copied().unwrap_or_default();
    let to = unit(&place_point(c, &overview[to_i], &placed_at(to_i), p[0], p[1]));
    let (from, to) = (from?, to?);
    let normal = cross3(&from, &to);
    let angle = dot3(&normal, &normal).sqrt().atan2(dot3(&from, &to));
    // Under PAN_AXIS_MIN_RAD the two points are too close to give a direction: the axis from the opposite edge of home
    // instead.
    let mut axis = if angle >= PAN_AXIS_MIN_RAD { unit(&normal) } else { None };
    if axis.is_none() && angle < PAN_AXIS_MIN_RAD {
        let b = edge_point(&overview[from_i], opposite, pan.exit);
        if let Some(back) = unit(&place_point(c, &overview[from_i], &placed_at(from_i), b[0], b[1])) {
            axis = unit(&cross3(&back, &to));
        }
    }
    let axis = axis?;
    Some(EdgePanPath { from, to, axis, angle })
}

/// The unit direction `cross` of the way from `start` to the path's P, along the great circle.
pub fn edge_pan_dot(path: &EdgePanPath, start: &Vec3, cross: f32) -> Vec3 {
    let t = clampf(cross, 0.0, 1.0);
    if t >= 1.0 {
        return path.to;
    }
    let a = clampf(dot3(start, &path.to), -1.0, 1.0).acos();
    let s = a.sin();
    if !(s > 1e-5) {
        return *start;
    }
    let wa = ((1.0 - t) * a).sin() / s;
    let wb = (t * a).sin() / s;
    Vec3 {
        x: wa * start.x + wb * path.to.x,
        y: wa * start.y + wb * path.to.y,
        z: wa * start.z + wb * path.to.z,
    }
}

/// A switch that landed: {from, to}; `centred` with the serial() of the centred switch (its turn's start direction).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LandedTurn {
    pub from: u32,
    pub to: u32,
    pub centred: bool,
    pub serial: u32,
}

/// [`ViewAnimator::set_target_with`]'s kind of change.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TargetOptions {
    /// A centred switch (the cursor pushed past an edge): in anchor mode its turn starts from the direction at the centre
    /// of the view when it began rather than from the desktop being left.
    pub center: bool,
    /// Every switch between maximized desktops but a hand's push: zoomed in, the target is shown at once.
    pub cut: bool,
    /// Lands an edge pan that reached its target.
    pub pan: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct Landing {
    pub(crate) active: bool,
    pub(crate) start_ms: f64,
    pub(crate) left: u32,
    pub(crate) way_from: f32, // the view's turn: from, settle_curve's v, how long
    pub(crate) way_speed: f32,
    pub(crate) way_ms: f32,
    pub(crate) left_from: f32, // the desktop being left: its zoom's from, v, now
    pub(crate) left_speed: f32,
    pub(crate) left_now: f32,
    pub(crate) until: f32,  // it stays on top until it is down to this
    pub(crate) border: f32, // what the pan's border had reached; how far it has gone over (0..1)
    pub(crate) border_now: f32,
}

impl Default for Landing {
    fn default() -> Self {
        Self {
            active: false,
            start_ms: 0.0,
            left: 0,
            way_from: 0.0,
            way_speed: 0.0,
            way_ms: PAN_SETTLE_MS,
            left_from: 0.0,
            left_speed: 0.0,
            left_now: 0.0,
            until: 0.0,
            border: 0.0,
            border_now: 0.0,
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Switch {
    pub(crate) active: bool,
    pub(crate) queued: bool,
    pub(crate) start_ms: f64,
    pub(crate) zoom: f32,   // the zoom it started from
    pub(crate) out_ms: f32, // phase 1's length
    pub(crate) from: u32,
    pub(crate) to: u32,
    pub(crate) next: u32,
    pub(crate) center: bool,
    pub(crate) next_center: bool,
    pub(crate) zoom_now: f32,
    pub(crate) look_now: f32,
    pub(crate) turn_now: f32,
}

impl Default for Switch {
    fn default() -> Self {
        Self {
            active: false,
            queued: false,
            start_ms: 0.0,
            zoom: 1.0,
            out_ms: 0.0,
            from: 0,
            to: 0,
            next: 0,
            center: false,
            next_center: false,
            zoom_now: 1.0,
            look_now: 0.0,
            turn_now: 0.0,
        }
    }
}

/// Time-based animation toward targets (no frame-rate dependence). Zoom and spacing ease with smoothstep over
/// `duration_ms`; selecting another desktop in the overview just moves the highlight.
///
/// Switching desktops while zoomed in (Ctrl+Alt+Left / Right, cursor follow) animates in three phases, in every viewing
/// mode:
///   1. zoom out from the current desktop to the overview (duration_ms times the zoom it started from; position() stays
///      on it);
///   2. turn: look() eases 0 -> 1 over `turn_ms` with smootherstep, the view turning from look_from() toward look_to()
///      (look_turn), the overview held (zoom 0);
///   3. zoom into the target (duration_ms) while look() eases back to 0, so it lands where it was dragged.
///
/// The turn is a view (camera) rotation only: no placement ever changes, and at rest look() is 0. During phase 1 another
/// switch retargets it; later ones queue and run once this one lands. Zooming out mid-switch zooms out from where the
/// view is and eases any turn back to 0.
///
/// A centred switch (`center`: the cursor pushed past an edge, tools/native_spike/unified_cursor.py) animates the same,
/// but in anchor mode its turn starts from the direction at the centre of the view when it began rather than from the
/// desktop being left, so it pans straight to the target and lands with the target centred in view.
///
/// A cut (`cut`: every switch between maximized desktops but a hand's push) zoomed in skips all three: the target is shown
/// at once, fully zoomed in at its placement, the view never turned; any switch, turn or pan in flight is dropped unlanded
/// (nothing to hand over to the room). Spacing still eases.
///
/// An edge pan (set_pan, EdgePan) is the hand's: while maximized, the cursor pushed past an edge draws the zoom out and
/// the view's turn from the supervisor's frames, and pulling back undoes them. They are eased by PAN_EASE_MS so the
/// pointer's sample rate does not show, and the zoom moves no faster than the whole range in duration_ms. switching() is
/// true for all of it, so nothing of the overview's (pills, the dot's own frames) shows. `pan` lands one that reached its
/// target. That is the rest of one motion, not a switch: from where the hand left them, in PAN_SETTLE_MS, the view's turn
/// goes on to the target from the hand's own speed (settle_curve, sooner for a hand too fast for that to end at rest), the
/// desktop being left goes on down to its placed size (left_zoom) and the target comes up from its own (zoom), all at
/// once, none of it waiting for the rest. `pan` with no pan under way is a cut. A cut or a return to the overview drops a
/// pan where it is, but a desktop still on its way out finishes that.
///
/// The overview's edge pan (overview-edge-pan, 2026-11-07; edge_pan.py: Overview): the same frames with zoom 0, taken at
/// rest in the overview. Nothing zooms; the room (anchor mode) turns 1:1 with the hand by `turn` about the path's axis,
/// and the dot is drawn on its way across as maximized. Pulling back, resting or a stale feed eases it back. set_target
/// with `pan`, still in the overview, on `to` lands it: the highlight moves at once and over turn_ms (pan_land) the turn
/// eases on to the one that puts `to` where the centre of the view was when the pan began, a centred turn (its own
/// serial()), which then lands like a centred switch's (take_landed_turns -> turn_anchor) in the same frame. A maximize,
/// a cut or another desktop selected ends it; one cut short mid-landing hands the whole turn to the room. switching() is
/// true throughout, so the pills and the overview's own dot hide while it lasts.
///
/// Clone is the C++ checkpoint (same-build restart state); continuity.rs writes the fields in its byte layout.
#[derive(Clone, Debug)]
pub struct ViewAnimator {
    pub(crate) duration: f32,
    pub(crate) turn_ms: f32,
    pub(crate) pan: EdgePan,
    pub(crate) pan_on: bool,
    pub(crate) pan_landing: bool,
    pub(crate) pan_overview: bool,
    pub(crate) pan_peek: f32,
    pub(crate) pan_cross: f32,
    pub(crate) pan_turn: f32,
    pub(crate) pan_zoom: f32,
    pub(crate) pan_zoom_speed: f32, // per ms, falling positive
    pub(crate) pan_ms: f64,
    pub(crate) pan_seen_ms: f64,
    pub(crate) pan_land_ms: f64,
    pub(crate) land: Landing,
    pub(crate) start_ms: f64,
    pub(crate) spacing_start_ms: f64,
    pub(crate) look_start_ms: f64,
    pub(crate) t: f32,
    pub(crate) spacing_t: f32,
    pub(crate) look_t: f32,
    pub(crate) from_zoom: f32,
    pub(crate) to_zoom: f32,
    pub(crate) from_spacing: f32,
    pub(crate) to_spacing: f32,
    pub(crate) from_look: f32,
    pub(crate) from_turn: f32,
    pub(crate) landed: Vec<LandedTurn>,
    pub(crate) serial: u32,
    pub(crate) turn_centred: bool,
    pub(crate) to_selected: u32,
    pub(crate) shown: u32,
    pub(crate) look_from: u32,
    pub(crate) look_to: u32,
    pub(crate) switch: Switch,
}

impl Default for ViewAnimator {
    fn default() -> Self {
        Self::new(VIEW_EASE_MS, 300.0)
    }
}

impl ViewAnimator {
    pub fn new(duration_ms: f32, turn_ms: f32) -> Self {
        Self {
            duration: duration_ms,
            turn_ms,
            pan: EdgePan::default(),
            pan_on: false,
            pan_landing: false,
            pan_overview: false,
            pan_peek: 0.0,
            pan_cross: 0.0,
            pan_turn: 0.0,
            pan_zoom: 1.0,
            pan_zoom_speed: 0.0,
            pan_ms: 0.0,
            pan_seen_ms: 0.0,
            pan_land_ms: 0.0,
            land: Landing::default(),
            start_ms: 0.0,
            spacing_start_ms: 0.0,
            look_start_ms: 0.0,
            t: 1.0,
            spacing_t: 1.0,
            look_t: 1.0,
            from_zoom: 0.0,
            to_zoom: 0.0,
            from_spacing: 40.0,
            to_spacing: 40.0,
            from_look: 0.0,
            from_turn: 0.0,
            landed: Vec::new(),
            serial: 0,
            turn_centred: false,
            to_selected: 0,
            shown: 0,
            look_from: 0,
            look_to: 0,
            switch: Switch::default(),
        }
    }

    /// With the zoom duration only (turn 300 ms).
    pub fn with_duration(duration_ms: f32) -> Self {
        Self::new(duration_ms, 300.0)
    }

    /// Shifts every clock by `ms` (the renderer paused).
    pub fn pause_clock(&mut self, ms: f64) {
        self.pan_ms += ms;
        self.pan_seen_ms += ms;
        self.pan_land_ms += ms;
        self.land.start_ms += ms;
        self.start_ms += ms;
        self.spacing_start_ms += ms;
        self.look_start_ms += ms;
        self.switch.start_ms += ms;
    }

    /// Under way, easing back, or landing.
    pub fn pan_active(&self) -> bool {
        self.pan_on
    }
    pub fn pan_landing(&self) -> bool {
        self.pan_landing
    }
    /// The pan under way is the overview's.
    pub fn pan_overview(&self) -> bool {
        self.pan_on && self.pan_overview
    }
    pub fn pan(&self) -> &EdgePan {
        &self.pan
    }
    /// Eased EdgePan::cross.
    pub fn pan_cross(&self) -> f32 {
        self.pan_cross
    }
    /// Eased EdgePan::turn; through a landing, on to EdgePan::way.
    pub fn pan_turn(&self) -> f32 {
        self.pan_turn
    }
    /// A landing's desktop being left and its own zoom on the way out (blended_layout_leaving, zoomed_placements_with).
    /// left_zoom 0: none.
    pub fn left_zoom(&self) -> f32 {
        if self.land.active { self.land.left_now } else { 0.0 }
    }
    pub fn left(&self) -> u32 {
        self.land.left
    }
    pub fn animating(&self) -> bool {
        self.t < 1.0
            || self.spacing_t < 1.0
            || self.look_t < 1.0
            || self.switch.active
            || self.pan_on
            || self.land.active
    }
    pub fn switching(&self) -> bool {
        self.switch.active || self.pan_on || self.land.active
    }
    /// The desktop the zoom is on (the one being left in phase 1).
    pub fn position(&self) -> f32 {
        self.shown as f32
    }
    pub fn focus(&self) -> u32 {
        self.shown
    }
    /// The most recent switch (the one whose turn turn() reports, also while it eases back): its serial (one per switch
    /// started) and whether it is centred.
    pub fn serial(&self) -> u32 {
        self.serial
    }
    pub fn turn_centred(&self) -> bool {
        self.turn_centred
    }
    pub fn look_from(&self) -> u32 {
        self.look_from
    }
    pub fn look_to(&self) -> u32 {
        self.look_to
    }
    /// Where spacing() is heading (the overview's settled layout).
    pub fn target_spacing(&self) -> f32 {
        self.to_spacing
    }
    pub fn selected(&self) -> u32 {
        self.to_selected
    }

    fn start_switch(&mut self, from: u32, to: u32, zoom: f32, now_ms: f64, center: bool) {
        self.switch = Switch::default();
        self.switch.center = center;
        self.turn_centred = center;
        self.serial = self.serial.wrapping_add(1);
        self.switch.active = true;
        self.switch.start_ms = now_ms;
        self.switch.zoom = clampf(zoom, 0.0, 1.0);
        self.switch.out_ms = self.duration * self.switch.zoom;
        self.switch.from = from;
        self.switch.to = to;
        self.switch.zoom_now = self.switch.zoom;
        self.shown = from;
        self.look_from = from;
        self.look_to = to;
        self.look_t = 1.0;
        self.from_look = 0.0;
        self.from_turn = 0.0;
        self.from_zoom = 1.0; // the zoom tween waits at 1 for the switch to land
        self.to_zoom = 1.0;
        self.t = 1.0;
    }

    fn end_pan(&mut self) {
        self.pan_on = false;
        self.pan_landing = false;
        self.pan_overview = false;
        self.pan = EdgePan::default();
        self.pan_peek = 0.0;
        self.pan_cross = 0.0;
        self.pan_turn = 0.0;
        self.pan_zoom = 1.0;
        self.pan_zoom_speed = 0.0;
    }

    /// A frame of the hand's pan. Ignored unless at rest on p.from (maximized, or in the overview), or that pan is under
    /// way.
    pub fn set_pan(&mut self, p: &EdgePan, now_ms: f64) {
        self.advance(now_ms);
        if self.pan_landing {
            return;
        }
        if !p.active {
            // back to the desktop, eased
            self.pan.active = false;
            self.pan.peek = 0.0;
            self.pan.cross = 0.0;
            self.pan.turn = 0.0;
            return;
        }
        let same = self.pan_on && self.pan.from == p.from && self.pan.to == p.to && self.pan.side == p.side;
        // At rest on p.from, maximized or in the overview (to_zoom is only ever 1 or 0).
        if !same
            && (self.switch.active
                || self.land.active
                || self.t < 1.0
                || p.from != self.shown
                || p.to == p.from
                || p.to > MAX_INDEX)
        {
            return;
        }
        if !same {
            self.pan_overview = self.to_zoom == 0.0;
            // The overview's pan turns the room from the centre of the view as it is now, as a centred switch does
            // (wayland.cpp capture_centre): a serial of its own, so its landing can hand the turn over (land_turn).
            if self.pan_overview {
                self.serial = self.serial.wrapping_add(1);
                self.turn_centred = true;
            }
        }
        self.pan = *p;
        self.pan.peek = clampf(p.peek, 0.0, 1.0);
        self.pan.cross = clampf(p.cross, 0.0, 1.0);
        self.pan.zoom = clampf(p.zoom, 0.0, 1.0);
        self.pan.way = clampf(p.way, 0.0, TWO_PI);
        self.pan.turn = clampf(p.turn, 0.0, self.pan.way);
        if self.pan_overview {
            self.pan.zoom = 0.0;
        }
        self.pan_on = true;
        self.pan_seen_ms = now_ms;
    }

    /// The overview pan's landing, [0,1] smootherstep over turn_ms; 0 unless one is landing.
    pub fn pan_land(&self) -> f32 {
        if !self.pan_landing || !self.pan_overview {
            return 0.0;
        }
        if self.turn_ms > 0.0 {
            smootherstep(clampf(((self.pan_ms - self.pan_land_ms) / f64::from(self.turn_ms)) as f32, 0.0, 1.0))
        } else {
            1.0
        }
    }

    fn land_pan(&mut self, to: u32, now_ms: f64) {
        // Where the hand left things, and how fast: the ease's own slope is how fast the view is turning this very moment.
        let way_left = cmax(self.pan.way - self.pan_turn, 0.0);
        let speed = cmax(self.pan.turn - self.pan_turn, 0.0) / PAN_EASE_MS;
        // settle_curve takes the hand's speed up to SETTLE_MAX_SPEED: a faster hand ends sooner, so the speed still
        // carries straight on.
        let way_ms = if speed * PAN_SETTLE_MS > SETTLE_MAX_SPEED * way_left {
            cmax(SETTLE_MAX_SPEED * way_left / speed, 1.0)
        } else {
            PAN_SETTLE_MS
        };
        self.land = Landing::default();
        self.land.active = true;
        self.land.start_ms = now_ms;
        self.land.left = self.shown;
        self.land.way_from = self.pan_turn;
        self.land.way_ms = way_ms;
        self.land.way_speed = if way_left > 0.0 { speed * way_ms / way_left } else { 0.0 };
        self.land.left_from = self.pan_zoom;
        self.land.left_now = self.pan_zoom;
        self.land.left_speed = if self.pan_zoom > 0.0 { self.pan_zoom_speed * PAN_SETTLE_MS / self.pan_zoom } else { 0.0 };
        self.land.until = self.pan.zoom;
        self.land.border = 1.0 - self.pan_zoom;
        self.pan.active = false; // peek and cross stay as the last frame left them, the end of the hand's way
        self.pan_landing = true;
        self.serial = self.serial.wrapping_add(1);
        self.turn_centred = false;
        self.look_from = self.shown;
        self.look_to = to;
        self.shown = to;
        self.pan_zoom = 0.0; // the zoom is the target's now, from its placed size; the desktop left has left_zoom
        self.from_zoom = 1.0;
        self.to_zoom = 1.0;
        self.t = 1.0;
        self.to_selected = to;
    }

    fn step_landing(&mut self, now_ms: f64) {
        let t = ((now_ms - self.land.start_ms) / f64::from(PAN_SETTLE_MS)) as f32;
        self.land.left_now = self.land.left_from * (1.0 - settle_curve(t, self.land.left_speed));
        self.land.border_now = smoothstep(t / PAN_BORDER_SHARE);
        if self.pan_landing {
            let wt = ((now_ms - self.land.start_ms) / f64::from(self.land.way_ms)) as f32;
            self.pan_turn = lerp(self.land.way_from, self.pan.way, settle_curve(wt, self.land.way_speed));
            self.pan_zoom = smootherstep(t);
        }
        if t < 1.0 {
            return;
        }
        self.land.active = false;
        if self.pan_landing {
            self.land_turn(self.look_from, self.look_to, false);
            self.end_pan();
        }
    }

    fn step_pan(&mut self, now_ms: f64) {
        let dt = (now_ms - self.pan_ms).max(0.0);
        self.pan_ms = now_ms;
        if self.land.active {
            self.step_landing(now_ms);
        }
        if !self.pan_on || (self.pan_landing && !self.pan_overview) {
            return;
        }
        if !self.pan_landing && self.pan.active && now_ms - self.pan_seen_ms > f64::from(PAN_STALE_MS) {
            self.pan.active = false;
            self.pan.peek = 0.0;
            self.pan.cross = 0.0;
            self.pan.turn = 0.0;
        }
        let k = (-(dt as f32) / PAN_EASE_MS).exp();
        let ease = |v: f32, to: f32| {
            let e = to + (v - to) * k;
            if (e - to).abs() < PAN_EASE_REST { to } else { e }
        };
        self.pan_peek = ease(self.pan_peek, self.pan.peek);
        self.pan_cross = ease(self.pan_cross, self.pan.cross);
        self.pan_turn = ease(self.pan_turn, self.pan.turn);
        if self.pan_overview {
            // nothing zooms; landed, the room takes the turn in this same frame (land_turns)
            self.pan_zoom = 1.0;
            if self.pan_landing && self.pan_land() >= 1.0 {
                self.land_turn(self.pan.from, self.pan.to, true);
                self.end_pan();
            } else if !self.pan_landing
                && !self.pan.active
                && self.pan_peek == 0.0
                && self.pan_cross == 0.0
                && self.pan_turn == 0.0
            {
                self.end_pan();
            }
            return;
        }
        let want = lerp(1.0, self.pan.zoom, smoothstep(self.pan_peek));
        let step = if self.duration > 0.0 { dt as f32 / self.duration } else { 1.0 };
        let moved = clampf(want - self.pan_zoom, -step, step);
        self.pan_zoom += moved;
        if dt > 0.0 {
            self.pan_zoom_speed = -moved / dt as f32;
        }
        if !self.pan.active && self.pan_peek == 0.0 && self.pan_cross == 0.0 && self.pan_turn == 0.0 && self.pan_zoom == 1.0
        {
            self.end_pan();
        }
    }

    /// A plain target: no centring, no cut, no pan landing.
    pub fn set_target(&mut self, zoomed: bool, selected: u32, spacing: u32, now_ms: f64) {
        self.set_target_with(zoomed, selected, spacing, now_ms, TargetOptions::default());
    }

    fn tween_zoom(&mut self, to: f32, z: f32, now_ms: f64) {
        let look_now = self.look();
        let mut turn_now = self.turn();
        // phase 3: the target is ahead. (The C++ sums out_ms + turn in float before comparing with the double.)
        if self.switch.active && now_ms - self.switch.start_ms >= f64::from(self.switch.out_ms + self.turn_ms) {
            self.land_turn(self.switch.from, self.switch.to, self.switch.center);
            turn_now = 0.0;
        }
        self.switch.active = false;
        self.switch.queued = false;
        if look_now > 0.0 {
            // ease any turn back
            self.from_look = look_now;
            self.look_start_ms = now_ms;
            self.look_t = 0.0;
        }
        self.from_turn = turn_now; // >0 only mid-turn (phase 2), where it equals look_now
        self.from_zoom = z;
        self.to_zoom = to;
        self.start_ms = now_ms;
        self.t = 0.0;
    }

    pub fn set_target_with(&mut self, zoomed: bool, selected: u32, spacing: u32, now_ms: f64, options: TargetOptions) {
        let TargetOptions { center, mut cut, mut pan } = options;
        self.advance(now_ms);
        let selected = selected.min(MAX_INDEX);
        let spacing_target = spacing.min(MAX_SPACING) as f32;
        if spacing_target != self.to_spacing {
            self.from_spacing = self.spacing();
            self.to_spacing = spacing_target;
            self.spacing_start_ms = now_ms;
            self.spacing_t = 0.0;
        }
        let z = self.zoom();
        if pan && !self.pan_on {
            // the frames of the pan never came: nothing to land from
            pan = false;
            cut = true;
        }
        if self.pan_on && self.pan_overview {
            // the overview's pan: only its own landing, a maximize, a cut or another desktop end it
            if pan && !zoomed && !self.pan_landing && selected == self.pan.to && selected != self.shown {
                // the hand reached it
                self.pan.active = false; // cross and turn stay as the last frame left them; pan_land() eases on from there
                self.pan_landing = true;
                self.pan_land_ms = now_ms;
                self.shown = selected; // the highlight is there at once
                self.to_selected = selected;
                return;
            }
            if zoomed || cut || selected != self.to_selected {
                if self.pan_landing {
                    self.land_turn(self.pan.from, self.pan.to, true); // cut short: the room takes the whole turn now
                }
                self.end_pan();
            }
            pan = false; // nothing of the maximized pan's below
        }
        if pan && zoomed && !self.pan_landing && !self.switch.active && selected != self.shown {
            // the hand reached it: on from there
            self.land_pan(selected, now_ms);
            return;
        }
        if self.pan_on && !self.pan_overview && (cut || !zoomed || selected != self.to_selected) {
            // over: from_zoom takes up what the pan had drawn
            self.end_pan();
            self.from_zoom = z;
            self.to_zoom = z;
            self.t = 1.0;
            if zoomed {
                self.land.active = false; // a cut leaves nothing of it; un-maximizing, the desktop on its way out carries on
            }
        }
        if cut && zoomed {
            // straight onto the target at its placement: no zoom out, no turn, no zoom in
            self.switch = Switch::default();
            self.from_zoom = 1.0;
            self.to_zoom = 1.0;
            self.t = 1.0;
            self.from_look = 0.0;
            self.from_turn = 0.0;
            self.look_t = 1.0;
            self.look_from = selected;
            self.look_to = selected;
            self.turn_centred = false;
            self.shown = selected;
        } else if self.switch.active {
            let zooming_out = now_ms - self.switch.start_ms < f64::from(self.switch.out_ms);
            if !zoomed {
                self.tween_zoom(0.0, z, now_ms); // zoom out from here; shown keeps the desktop on screen until the overview
            } else if selected == self.switch.to {
                self.switch.queued = false;
                if zooming_out && center {
                    // the turn has not started
                    self.switch.center = true;
                    self.turn_centred = true;
                }
            } else if zooming_out && selected == self.switch.from {
                self.tween_zoom(1.0, z, now_ms); // back before the turn began: zoom straight back in
            } else if zooming_out {
                // the turn has not started: just aim it elsewhere
                self.switch.to = selected;
                self.look_to = selected;
                self.switch.center = center;
                self.turn_centred = center;
            } else {
                // runs once this switch lands
                self.switch.queued = true;
                self.switch.next = selected;
                self.switch.next_center = center;
            }
        } else if zoomed && selected != self.shown && (self.to_zoom == 1.0 || z > 0.0) {
            // zoomed (or partly): animate the switch
            self.start_switch(self.shown, selected, z, now_ms, center);
        } else {
            let want = if zoomed { 1.0 } else { 0.0 };
            if want != self.to_zoom {
                self.tween_zoom(want, z, now_ms);
            }
            // Zooming out, the desktop being left stays the zoom's until the overview; otherwise the selection is shown
            // at once.
            if !(self.to_zoom == 0.0 && z > 0.0) {
                self.shown = selected;
            }
        }
        self.to_selected = selected;
    }

    pub fn advance(&mut self, now_ms: f64) {
        self.step_pan(now_ms);
        let duration = f64::from(self.duration);
        if self.t < 1.0 {
            self.t = if self.duration > 0.0 { clampf(((now_ms - self.start_ms) / duration) as f32, 0.0, 1.0) } else { 1.0 };
        }
        if self.spacing_t < 1.0 {
            self.spacing_t =
                if self.duration > 0.0 { clampf(((now_ms - self.spacing_start_ms) / duration) as f32, 0.0, 1.0) } else { 1.0 };
        }
        if self.look_t < 1.0 {
            self.look_t =
                if self.duration > 0.0 { clampf(((now_ms - self.look_start_ms) / duration) as f32, 0.0, 1.0) } else { 1.0 };
        }
        if !self.switch.active && self.t >= 1.0 {
            self.shown = self.to_selected;
        }
        while self.switch.active {
            let turn_ms = self.turn_ms;
            let w = &mut self.switch;
            let e = now_ms - w.start_ms;
            let out = f64::from(w.out_ms);
            let turn = out + f64::from(turn_ms);
            let inn = turn + duration;
            if e < out {
                w.zoom_now = w.zoom * (1.0 - smoothstep((e / out) as f32));
                w.look_now = 0.0;
                w.turn_now = 0.0;
                self.shown = w.from;
                return;
            }
            self.shown = w.to;
            if e < turn {
                w.zoom_now = 0.0;
                let l = if turn_ms > 0.0 { smootherstep(((e - out) / f64::from(turn_ms)) as f32) } else { 1.0 };
                w.look_now = l;
                w.turn_now = l;
                return;
            }
            if e < inn {
                let s = smoothstep(((e - turn) / duration) as f32);
                w.zoom_now = s;
                w.look_now = 1.0 - s;
                w.turn_now = 1.0;
                return;
            }
            // Landed: zoomed into the target, the view untouched (anchor: the turn is the room's now). A queued switch
            // starts where this one ended.
            let (from_w, to_w, center_w) = (w.from, w.to, w.center);
            let again = w.queued && w.next != w.to;
            let from = w.to;
            let next = w.next;
            let next_center = w.next_center;
            let start = w.start_ms;
            w.active = false;
            w.zoom_now = 1.0;
            w.look_now = 0.0;
            w.turn_now = 0.0;
            self.land_turn(from_w, to_w, center_w);
            if !again {
                self.shown = from;
                break;
            }
            self.start_switch(from, next, 1.0, start + inn, next_center);
        }
    }

    /// Eased current zoom in [0,1]; during a landing, the target's.
    pub fn zoom(&self) -> f32 {
        if self.switch.active {
            self.switch.zoom_now
        } else {
            lerp(self.from_zoom, self.to_zoom, smoothstep(self.t)) * self.pan_zoom
        }
    }

    /// [0,1]: how far the view is turned from look_from() toward look_to().
    pub fn look(&self) -> f32 {
        if self.switch.active { self.switch.look_now } else { self.from_look * (1.0 - smoothstep(self.look_t)) }
    }

    /// During a pan, who is in front of whom and the second border; None otherwise. Until the desktop being left is down
    /// to the pair's EdgePan::zoom it stays on top: there, and only there, they do not overlap, so they change places
    /// unseen.
    pub fn pan_layers(&self) -> Option<PanLayers> {
        if (!self.pan_on && !self.land.active) || self.pan_overview() {
            return None; // the overview's pan draws the room as placed
        }
        if !self.land.active {
            // the hand: the desktop on top of the target, as maximized
            return Some(PanLayers { over: self.pan.from as usize, under: self.pan.to as usize, left: 0, left_alpha: 0.0 });
        }
        let over = self.land.left_now > self.land.until + 1e-4;
        let (left, shown) = (self.land.left as usize, self.shown as usize);
        Some(PanLayers {
            over: if over { left } else { shown },
            under: if over { shown } else { left },
            left,
            left_alpha: self.land.border * (1.0 - self.land.border_now),
        })
    }

    /// The border's strength on focus(). 1 - zoom() but for a landing, where it shares what the pan had drawn with the
    /// target's fade in.
    pub fn highlight_alpha(&self) -> f32 {
        let own = 1.0 - self.zoom();
        if self.pan_landing && !self.pan_overview { self.land.border * self.land.border_now * own } else { own }
    }

    /// Anchor mode's camera turn toward look_to(), [0,1]. Unlike look() it does not ease back in phase 3: it holds 1
    /// while zooming into the target, so the view stays on it. Once the switch lands the turn is handed over to the room
    /// (take_landed_turn -> ModeController::turn_anchor) and turn() drops to 0 in the same frame. Cancelled mid-turn
    /// (zooming out in phase 2) it eases back to 0 like look(); cancelled in phase 3 the turn lands at once.
    pub fn turn(&self) -> f32 {
        if self.switch.active { self.switch.turn_now } else { self.from_turn * (1.0 - smoothstep(self.look_t)) }
    }

    fn land_turn(&mut self, from: u32, to: u32, centred: bool) {
        if centred {
            // turned from the view's centre: its own fold
            self.landed.push(LandedTurn { from, to, centred: true, serial: self.serial });
            return;
        }
        if from == to {
            return;
        }
        match self.landed.last_mut() {
            Some(back) if !back.centred => {
                back.to = to;
                if back.from == back.to {
                    self.landed.pop(); // there and back again
                }
            }
            _ => self.landed.push(LandedTurn { from, to, centred: false, serial: 0 }),
        }
    }

    /// Every switch that landed since the last call, in order; consecutive uncentred ones merge (first from, last to:
    /// aim turns telescope). A centred one never merges: its turn starts from its own direction (the view's centre), not
    /// from a desktop.
    pub fn take_landed_turns(&mut self) -> Vec<LandedTurn> {
        std::mem::take(&mut self.landed)
    }

    /// The turn of the switch(es) that landed since the last call, as [from, to] (look_from / look_to). None if none.
    pub fn take_landed_turn(&mut self) -> Option<[u32; 2]> {
        let all = self.take_landed_turns();
        let (first, last) = (all.first()?, all.last()?);
        if first.from == last.to {
            return None;
        }
        Some([first.from, last.to])
    }

    pub fn spacing(&self) -> f32 {
        lerp(self.from_spacing, self.to_spacing, smoothstep(self.spacing_t))
    }
}
