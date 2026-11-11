//! Material Design 3 drawing on the renderer's premultiplied RGBA images (`spike/include/xr/material_draw.hpp`,
//! `spike/src/material_draw.cpp`): anti-aliased rounded containers, outlines and discs, and the few Material icons the
//! chrome uses, drawn from signed distances so they stay crisp at any size. Colours are [`crate::material`] roles
//! (already under the OLED cap), and the "over" here never brightens past what it paints, so nothing drawn with roles
//! passes [`crate::hud::CARD_MAX_LEVEL`].
//!
//! The maths is `f32` in the C++ order with the same libm calls, so every pixel matches the C++ byte for byte
//! (`tests/card_raster.rs`). `cmin`, `cmax` and `clampf` are `std::min`, `std::max` and `std::clamp`, NaN and signed
//! zeros included.

// `!(a > 0.0)` is the C++ guard: true for NaN too.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use crate::hud::HudImage;
use crate::material::{MdRgb, clampf};

/// `std::min(a, b)`: `b < a ? b : a`.
pub(crate) fn cmin(a: f32, b: f32) -> f32 {
    if b < a { b } else { a }
}
/// `std::max(a, b)`: `a < b ? b : a`.
pub(crate) fn cmax(a: f32, b: f32) -> f32 {
    if a < b { b } else { a }
}
fn clamp01(v: f32) -> f32 {
    clampf(v, 0.0, 1.0)
}

/// Distance from p to the segment a-b, less r: a capsule (a stroke with round caps).
fn capsule(px: f32, py: f32, ax: f32, ay: f32, bx: f32, by: f32, r: f32) -> f32 {
    let (pax, pay, bax, bay) = (px - ax, py - ay, bx - ax, by - ay);
    let h = clamp01((pax * bax + pay * bay) / cmax(bax * bax + bay * bay, 1e-6));
    (pax - bax * h).hypot(pay - bay * h) - r
}

/// Signed distance to a triangle (Inigo Quilez's exact form).
#[allow(clippy::too_many_arguments)]
fn triangle(px: f32, py: f32, x0: f32, y0: f32, x1: f32, y1: f32, x2: f32, y2: f32) -> f32 {
    let (e0x, e0y, e1x, e1y, e2x, e2y) = (x1 - x0, y1 - y0, x2 - x1, y2 - y1, x0 - x2, y0 - y2);
    let (v0x, v0y, v1x, v1y, v2x, v2y) = (px - x0, py - y0, px - x1, py - y1, px - x2, py - y2);
    let proj = |vx: f32, vy: f32, ex: f32, ey: f32| {
        let h = clamp01((vx * ex + vy * ey) / cmax(ex * ex + ey * ey, 1e-6));
        (vx - ex * h, vy - ey * h)
    };
    let (q0x, q0y) = proj(v0x, v0y, e0x, e0y);
    let (q1x, q1y) = proj(v1x, v1y, e1x, e1y);
    let (q2x, q2y) = proj(v2x, v2y, e2x, e2y);
    let s = if e0x * e2y - e0y * e2x > 0.0 { 1.0 } else { -1.0 };
    let (d0, d1, d2) = (q0x * q0x + q0y * q0y, q1x * q1x + q1y * q1y, q2x * q2x + q2y * q2y);
    let (c0, c1, c2) = (s * (v0x * e0y - v0y * e0x), s * (v1x * e1y - v1y * e1x), s * (v2x * e2y - v2y * e2x));
    let (d, c) = (cmin(cmin(d0, d1), d2), cmin(cmin(c0, c1), c2));
    if c > 0.0 { -d.sqrt() } else { d.sqrt() } // inside (every edge's cross term positive): negative
}

/// A stroke of half width `half` with round caps along the arc of radius r about (cx, cy), from angle a0 up to a1
/// (radians, a1 - a0 under a full turn, y down).
#[allow(clippy::too_many_arguments)]
fn arc_stroke(px: f32, py: f32, cx: f32, cy: f32, r: f32, a0: f32, a1: f32, half: f32) -> f32 {
    const TURN: f32 = 2.0 * std::f32::consts::PI;
    let (x, y) = (px - cx, py - cy);
    let mut a = y.atan2(x);
    while a < a0 {
        a += TURN;
    }
    while a >= a0 + TURN {
        a -= TURN;
    }
    if a <= a1 {
        return (x.hypot(y) - r).abs() - half;
    }
    cmin((x - r * a0.cos()).hypot(y - r * a0.sin()), (x - r * a1.cos()).hypot(y - r * a1.sin())) - half
}

/// The circular arrow on the 24 grid, clockwise: about 3/4 of a turn, open at the upper right, its head at the open end
/// following the arc.
fn rotate_right(gx: f32, gy: f32) -> f32 {
    const R: f32 = 8.2;
    const HALF: f32 = 1.25;
    const HEAD: f32 = 6.4;
    const HEAD_HALF: f32 = 4.6;
    const DEG: f32 = std::f32::consts::PI / 180.0;
    const END: f32 = -32.0 * DEG;
    const START: f32 = END - 285.0 * DEG;
    let (ex, ey, tx, ty) = (12.0 + R * END.cos(), 12.0 + R * END.sin(), -END.sin(), END.cos());
    let (bx, by, fx, fy) = (ex - tx * HEAD * 0.3, ey - ty * HEAD * 0.3, ex + tx * HEAD * 0.7, ey + ty * HEAD * 0.7);
    cmin(
        arc_stroke(gx, gy, 12.0, 12.0, R, START, END - 0.2, HALF),
        triangle(gx, gy, fx, fy, bx - ty * HEAD_HALF, by + tx * HEAD_HALF, bx + ty * HEAD_HALF, by - tx * HEAD_HALF),
    )
}

/// Premultiplied "over": colour `c` with coverage `a` (clamped to 1) on top of pixel (x, y); outside the image: nothing.
pub fn md_over(im: &mut HudImage, x: i32, y: i32, c: MdRgb, a: f32) {
    if x < 0 || y < 0 || x as u32 >= im.width || y as u32 >= im.height || !(a > 0.0) {
        return;
    }
    let a = cmin(a, 1.0);
    let at = (y as usize * im.width as usize + x as usize) * 4;
    let p = &mut im.rgba[at..at + 4];
    for i in 0..3 {
        p[i] = clampf(c[i] * a * 255.0 + f32::from(p[i]) * (1.0 - a), 0.0, 255.0).round() as u8;
    }
    p[3] = clampf(a * 255.0 + f32::from(p[3]) * (1.0 - a), 0.0, 255.0).round() as u8;
}

/// Signed distance (px, negative inside) from (px, py) to a rounded rectangle centred at (cx, cy) with half sizes
/// hw x hh and corner radius r (at most the smaller half size).
pub fn md_rounded_distance(px: f32, py: f32, cx: f32, cy: f32, hw: f32, hh: f32, r: f32) -> f32 {
    let r = clampf(r, 0.0, cmin(hw, hh));
    let (dx, dy) = ((px - cx).abs() - (hw - r), (py - cy).abs() - (hh - r));
    let (ox, oy) = (cmax(dx, 0.0), cmax(dy, 0.0));
    (ox * ox + oy * oy).sqrt() + cmin(cmax(dx, dy), 0.0) - r
}

/// Calls `f(x, y, px_centre_x, px_centre_y)` for every pixel of the box [x0, x1] x [y0, y1] grown by one, in the C++
/// order (rows, then columns).
fn each_pixel(x0: f32, y0: f32, x1: f32, y1: f32, mut f: impl FnMut(i32, i32, f32, f32)) {
    for y in y0.floor() as i32 - 1..=y1.ceil() as i32 {
        for x in x0.floor() as i32 - 1..=x1.ceil() as i32 {
            f(x, y, x as f32 + 0.5, y as f32 + 0.5);
        }
    }
}

/// A rounded container [x0, x1) x [y0, y1), its edge anti-aliased, `alpha` opaque.
#[allow(clippy::too_many_arguments)]
pub fn md_fill_rounded(im: &mut HudImage, x0: f32, y0: f32, x1: f32, y1: f32, radius: f32, c: MdRgb, alpha: f32) {
    let (cx, cy, hw, hh) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0);
    if !(hw > 0.0) || !(hh > 0.0) || !(alpha > 0.0) {
        return;
    }
    each_pixel(x0, y0, x1, y1, |x, y, sx, sy| {
        let cov = clamp01(0.5 - md_rounded_distance(sx, sy, cx, cy, hw, hh, radius));
        if cov > 0.0 {
            md_over(im, x, y, c, cov * alpha);
        }
    });
}

/// The same shape's outline, `width` px wide just inside its edge (M3 outlined containers: 1 px of outline / outline
/// variant).
#[allow(clippy::too_many_arguments)]
pub fn md_stroke_rounded(im: &mut HudImage, x0: f32, y0: f32, x1: f32, y1: f32, radius: f32, width: f32, c: MdRgb, alpha: f32) {
    let (cx, cy, hw, hh) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0);
    if !(hw > 0.0) || !(hh > 0.0) || !(alpha > 0.0) || !(width > 0.0) {
        return;
    }
    each_pixel(x0, y0, x1, y1, |x, y, sx, sy| {
        let d = md_rounded_distance(sx, sy, cx, cy, hw, hh, radius);
        let cov = clamp01(0.5 - d) * clamp01(0.5 + d + width); // inside the edge, within `width` of it
        if cov > 0.0 {
            md_over(im, x, y, c, cov * alpha);
        }
    });
}

pub fn md_disc(im: &mut HudImage, cx: f32, cy: f32, r: f32, c: MdRgb, alpha: f32) {
    md_fill_rounded(im, cx - r, cy - r, cx + r, cy + r, r, c, alpha);
}

/// Signed distance (px, negative inside) from (px, py) to a stroke `width` px wide with round caps along an L: from
/// (gap - leg, gap) to the elbow, rounded with radius `bend`, and on to (gap, gap - leg). An L wrapped round the
/// origin's outer quadrant, x and y pointing away from what it wraps.
pub fn md_corner_distance(px: f32, py: f32, gap: f32, leg: f32, bend: f32, width: f32) -> f32 {
    let (half, e) = (width / 2.0, gap - bend); // the elbow's centre, on the diagonal
    let mut d = 1e9f32;
    if px >= e && py >= e {
        d = ((px - e).hypot(py - e) - bend).abs() - half; // the quarter circle
    }
    d = cmin(d, capsule(px, py, gap - leg, gap, e, gap, half));
    cmin(d, capsule(px, py, gap, gap - leg, gap, e, half))
}

/// Material Symbols-style icons, outlined on M3's 24 px grid with 2 px strokes (heavier below [`MD_ICON_PX`], as
/// Material Symbols' optical size).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MdIcon {
    /// A plus.
    Add,
    /// A minus.
    Remove,
    /// A double arrow between two bars: a wider desktop.
    Width,
    /// A dropdown's filled triangle, closed.
    ArrowDropDown,
    /// A dropdown's filled triangle, open.
    ArrowDropUp,
    /// A circular arrow, counter-clockwise.
    RotateLeft,
    /// A circular arrow, clockwise.
    RotateRight,
    /// A dot on a broken horizon line.
    Level,
}

/// Icons in buttons (M3: 18 dp next to a label).
pub const MD_ICON_PX: f32 = 18.0;

/// The icon's signed distance (icon px, negative inside) at (x, y) relative to its centre, at `size`: what [`md_icon`]
/// fills.
pub fn md_icon_distance(icon: MdIcon, x: f32, y: f32, size: f32) -> f32 {
    let k = 24.0 / cmax(size, 1e-3); // icon px -> grid units
    let (gx, gy) = (x * k + 12.0, y * k + 12.0);
    let mut d = match icon {
        MdIcon::Add => cmin(capsule(gx, gy, 5.0, 12.0, 19.0, 12.0, 1.0), capsule(gx, gy, 12.0, 5.0, 12.0, 19.0, 1.0)),
        MdIcon::Remove => capsule(gx, gy, 5.0, 12.0, 19.0, 12.0, 1.0),
        MdIcon::Width => [
            capsule(gx, gy, 21.5, 5.0, 21.5, 19.0, 1.0),
            capsule(gx, gy, 8.0, 12.0, 16.0, 12.0, 1.0),
            triangle(gx, gy, 5.5, 12.0, 11.0, 6.5, 11.0, 17.5),
            triangle(gx, gy, 18.5, 12.0, 13.0, 17.5, 13.0, 6.5),
        ]
        .into_iter()
        .fold(capsule(gx, gy, 2.5, 5.0, 2.5, 19.0, 1.0), cmin),
        // Material Symbols' arrow_drop_down: a filled triangle, point down.
        MdIcon::ArrowDropDown => triangle(gx, gy, 7.0, 10.0, 17.0, 10.0, 12.0, 15.0),
        MdIcon::ArrowDropUp => triangle(gx, gy, 7.0, 14.0, 12.0, 9.0, 17.0, 14.0),
        MdIcon::RotateRight => rotate_right(gx, gy),
        MdIcon::RotateLeft => rotate_right(24.0 - gx, gy), // its mirror image
        // -o-: a dot on a horizon line, broken round it.
        MdIcon::Level => cmin(
            cmin(capsule(gx, gy, 2.0, 12.0, 7.4, 12.0, 1.0), capsule(gx, gy, 16.6, 12.0, 22.0, 12.0, 1.0)),
            capsule(gx, gy, 12.0, 12.0, 12.0, 12.0, 2.4),
        ),
    };
    // Optical size (as Material Symbols' opsz): below MD_ICON_PX the 2 px strokes thicken, up to 3 grid px at 12 px, so
    // a small icon keeps its weight next to its label.
    d -= clampf((MD_ICON_PX - size) / 12.0, 0.0, 0.5);
    d / k
}

/// `icon` drawn `size` px across, centred on (cx, cy).
#[allow(clippy::too_many_arguments)]
pub fn md_icon(im: &mut HudImage, icon: MdIcon, cx: f32, cy: f32, size: f32, c: MdRgb, alpha: f32) {
    if !(size > 0.0) || !(alpha > 0.0) {
        return;
    }
    let half = size / 2.0;
    each_pixel(cx - half, cy - half, cx + half, cy + half, |x, y, sx, sy| {
        let cov = clamp01(0.5 - md_icon_distance(icon, sx - cx, sy - cy, size));
        if cov > 0.0 {
            md_over(im, x, y, c, cov * alpha);
        }
    });
}
