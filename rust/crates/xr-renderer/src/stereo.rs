//! Global immersive 3D, the maths (spike/include/xr/stereo.hpp, spike/src/stereo.cpp; docs/stereo-render.md). Every number
//! the shaders in [`crate::stereo_gl`] compute is defined here first. No GL in this module.
//!
//! Units. A disparity is measured between the two eyes' views, in pixels of one eye's image, positive = nearer than the
//! screen plane (crossed), so each eye's image moves half of it: the left eye's near pixels to the right, the right eye's
//! to the left. The guide map (the disparity map the warp reads) stores v in -1..1: the fraction of the comfort limit in
//! use, +1 the near limit and -1 the far limit, which makes it independent of the resolution it is shown at.
//!
//! Not ported: the CPU mirrors of the GL passes (stereo_sample, stereo_guide, stereo_warp...), which only the C++ render
//! tests compare against; the Rust passes are checked against the C++ goldens instead (tests/stereo_render.rs).

use xr_wire::control::ImmersiveParams;

/// What 3D looks like. Out-of-range values are clamped wherever they are used ([`stereo_clamped`]).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StereoParams {
    /// 0 flat .. 2; 1 = the frame's nearest and farthest things reach the comfort limits.
    pub strength: f32,
    /// The nearness (0 far .. 1 near) that stays on the screen plane.
    pub convergence: f32,
    /// Comfort limit: largest crossed disparity, degrees of visual angle.
    pub near_deg: f32,
    /// Comfort limit: largest uncrossed disparity; stays under the eyes' divergence point.
    pub far_deg: f32,
    /// What is left of the parallax on small text, 0..1.
    pub text_keep: f32,
}

impl Default for StereoParams {
    fn default() -> Self {
        Self {
            strength: 0.6,
            convergence: 0.4,
            near_deg: 1.0,
            far_deg: 0.75,
            text_keep: 0.15,
        }
    }
}

/// Hard ceilings: no setting, saved profile or wire value gets past these. 1.5 / 1.0 degrees: the usual 3D display
/// comfort range; a virtual image at 4 m has about 0.93 degrees before the eyes must diverge (63 mm IPD).
pub const STEREO_STRENGTH_MAX: f32 = 2.0;
pub const STEREO_NEAR_DEG_MAX: f32 = 1.5;
pub const STEREO_FAR_DEG_MAX: f32 = 1.0;
/// From |v| = this a knee keeps peaks from clipping to a flat shelf.
pub const STEREO_KNEE: f32 = 0.7;
/// v is tapered to 0 over this fraction of a desktop's width (and the same pixels of its height) from each edge.
pub const STEREO_EDGE_TAPER: f32 = 0.05;
/// A change of v above STEREO_CUT within one depth frame is new content, not noise: the filter lets go of its history.
pub const STEREO_CUT_LO: f32 = 0.1;
pub const STEREO_CUT: f32 = 0.35;
/// Small text: taps across and rows of a guide texel's footprint, and the luma turn that counts.
pub const STEREO_TEXT_COLS: i32 = 8;
pub const STEREO_TEXT_ROWS: i32 = 3;
pub const STEREO_TEXT_EDGE: f32 = 0.1;
pub const STEREO_TEXT_LO: f32 = 0.75;
pub const STEREO_TEXT_HI: f32 = 1.5;
/// Half the IPD over the physical distance of the canvas plane: 31.5 mm / 4 m.
pub const STEREO_HALF_BASELINE: f32 = 0.0079;
/// The smallest share of the depth budget a small desktop gets.
pub const STEREO_MIN_SCALE: f32 = 0.35;
/// On / off and a depth source that stopped ease over this, and depth older than STALE draws flat.
pub const STEREO_FADE_MS: f64 = 300.0;
pub const STEREO_STALE_MS: f64 = 500.0;
pub const STEREO_MARCH_STEPS: i32 = 12;
pub const STEREO_BISECT_STEPS: i32 = 3;
pub const STEREO_HOLE_LO: f32 = 0.8;
pub const STEREO_HOLE_HI: f32 = 1.6;

const PI: f32 = 3.141_592_653_589_79;

/// std::clamp: NaN passes through, and lo > hi never panics.
fn clamp(v: f32, lo: f32, hi: f32) -> f32 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

fn finite_or(v: f32, fallback: f32) -> f32 {
    if v.is_finite() { v } else { fallback }
}

/// In range; NaN or infinity: that field's default.
pub fn stereo_clamped(p: &StereoParams) -> StereoParams {
    let d = StereoParams::default();
    StereoParams {
        strength: clamp(finite_or(p.strength, d.strength), 0.0, STEREO_STRENGTH_MAX),
        convergence: clamp(finite_or(p.convergence, d.convergence), 0.0, 1.0),
        near_deg: clamp(finite_or(p.near_deg, d.near_deg), 0.0, STEREO_NEAR_DEG_MAX),
        far_deg: clamp(finite_or(p.far_deg, d.far_deg), 0.0, STEREO_FAR_DEG_MAX),
        text_keep: clamp(finite_or(p.text_keep, d.text_keep), 0.0, 1.0),
    }
}

/// The whole numbers of an `immersive` frame, clamped. The convergence keeps its default: on the wire it is a
/// percentile the depth link applies to each map, not a nearness.
pub fn stereo_from_wire(w: &ImmersiveParams) -> StereoParams {
    stereo_clamped(&StereoParams {
        strength: w.strength as f32 / 100.0,
        near_deg: w.near_arcmin as f32 / 60.0,
        far_deg: w.far_arcmin as f32 / 60.0,
        text_keep: w.text as f32 / 100.0,
        ..StereoParams::default()
    })
}

/// The whole view's comfort limits in pixels of an eye image eye_w wide, seen through tan(half FOV) half_w.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct StereoLimits {
    pub near_px: f32,
    pub far_px: f32,
}

pub fn stereo_limits(p: &StereoParams, eye_w: f32, half_w: f32) -> StereoLimits {
    let p = stereo_clamped(p);
    if !(eye_w > 0.0) || !(half_w > 0.0) {
        return StereoLimits::default();
    }
    let px_per_deg = eye_w / (2.0 * half_w.atan() * 180.0 / PI);
    StereoLimits {
        near_px: p.near_deg * px_per_deg,
        far_px: p.far_deg * px_per_deg,
    }
}

pub fn stereo_smoothstep(a: f32, b: f32, x: f32) -> f32 {
    let t = clamp((x - a) / (b - a), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// lo..hi -> 0..1, clamped; non-finite stays non-finite for stereo_v.
pub fn stereo_nearness(raw: f32, lo: f32, hi: f32) -> f32 {
    if !raw.is_finite() {
        return f32::NAN;
    }
    let span = hi - lo;
    clamp(
        if span.abs() > 1e-6 {
            (raw - lo) / span
        } else {
            0.5
        },
        0.0,
        1.0,
    )
}

/// Nearness -> v. Non-finite depth: 0, on the screen plane. Mirrors the guide shader.
pub fn stereo_v(nearness: f32, p: &StereoParams) -> f32 {
    if !nearness.is_finite() {
        return 0.0;
    }
    let p = stereo_clamped(p);
    let x = 2.0 * p.strength * (nearness - p.convergence);
    let a = x.abs();
    if a <= STEREO_KNEE {
        return x;
    }
    let room = 1.0 - STEREO_KNEE;
    (STEREO_KNEE + room * ((a - STEREO_KNEE) / room).tanh()).copysign(x)
}

/// aspect = width / height.
pub fn stereo_edge_gain(u: f32, v: f32, aspect: f32) -> f32 {
    stereo_smoothstep(0.0, 1.0, u.min(1.0 - u) / STEREO_EDGE_TAPER)
        * stereo_smoothstep(0.0, 1.0, v.min(1.0 - v) / (STEREO_EDGE_TAPER * aspect))
}

pub fn stereo_temporal(previous: f32, current: f32, alpha: f32) -> f32 {
    let a = alpha
        + (1.0 - alpha) * stereo_smoothstep(STEREO_CUT_LO, STEREO_CUT, (current - previous).abs());
    previous + (current - previous) * a
}

/// 60 ms time constant, so the filter does not depend on the depth frame rate.
pub fn stereo_alpha(dt_ms: f32) -> f32 {
    if dt_ms > 0.0 {
        1.0 - (-dt_ms / 60.0).exp()
    } else {
        1.0
    }
}

/// The resolved surface's correction history: 120 ms.
pub fn stereo_resolve_alpha(dt_ms: f32) -> f32 {
    if dt_ms > 0.0 {
        1.0 - (-dt_ms / 120.0).exp()
    } else {
        1.0
    }
}

/// Mean luma turns per row -> 0 (no text) .. 1 (certainly text).
pub fn stereo_text_score(jumps: f32) -> f32 {
    stereo_smoothstep(STEREO_TEXT_LO, STEREO_TEXT_HI, jumps)
}

/// 0 (left) -> +1, 1 (right) -> -1: the sign of a near pixel's shift.
pub fn stereo_side(eye: u32) -> f32 {
    if eye == 0 { 1.0 } else { -1.0 }
}

/// The disparity of a point at view depth z (< 0) for half baseline b: each eye's clip x is
/// (q.x + side b (1 + q.z)) / half_w.
pub fn stereo_plane_disparity(b: f32, z: f32, eye_w: f32, half_w: f32) -> f32 {
    if !(z < 0.0) || !(half_w > 0.0) {
        return 0.0;
    }
    eye_w * b * (1.0 + z) / (half_w * -z)
}

/// b, cut so that no desktop's centre lies beyond the limits. `depths` are view z of the centres.
pub fn stereo_safe_baseline(
    b: f32,
    depths: &[f32],
    limits: &StereoLimits,
    eye_w: f32,
    half_w: f32,
) -> f32 {
    let mut keep = 1.0f32;
    for &z in depths {
        let d = stereo_plane_disparity(b, z, eye_w, half_w);
        if d > limits.near_px {
            keep = keep.min(limits.near_px / d);
        } else if -d > limits.far_px {
            keep = keep.min(limits.far_px / -d);
        }
    }
    b * keep
}

/// The background at infinity, as far as the far limit lets it be: each eye's constant shift in NDC.
pub fn stereo_infinity_shift(b: f32, limits: &StereoLimits, eye_w: f32, half_w: f32) -> f32 {
    if !(half_w > 0.0) || !(eye_w > 0.0) {
        return 0.0;
    }
    (b / half_w).min(limits.far_px / eye_w)
}

/// A small desktop gets proportionally less, as if it were a smaller window on the same scene.
pub fn stereo_tile_scale(tile_w: f32, canvas_w: f32, distance: f32) -> f32 {
    if !(canvas_w > 0.0) || !(distance > 0.0) {
        return STEREO_MIN_SCALE;
    }
    clamp(tile_w / (canvas_w * distance), STEREO_MIN_SCALE, 1.0)
}

/// Half disparities: what one eye's image may move, pixels.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct StereoBudget {
    pub near_half: f32,
    pub far_half: f32,
}

/// What is left for the depth effect on one desktop: the sum of its plane's disparity and the warp's stays inside the
/// limits.
pub fn stereo_budget(l: &StereoLimits, plane_px: f32, scale: f32, gain: f32) -> StereoBudget {
    let k = 0.5 * clamp(scale, 0.0, 1.0) * clamp(gain, 0.0, 1.0);
    StereoBudget {
        near_half: k * clamp(l.near_px - plane_px, 0.0, l.near_px),
        far_half: k * clamp(l.far_px + plane_px, 0.0, l.far_px),
    }
}

/// v -> this eye's shift in pixels, signed.
pub fn stereo_half_disparity(v: f32, b: &StereoBudget) -> f32 {
    if v > 0.0 { v * b.near_half } else { v * b.far_half }
}

/// On / off and a depth source that stopped: the effect eases out instead of snapping flat, and in again.
#[derive(Clone, Copy, Debug, Default)]
pub struct StereoFader {
    last_ms: f64,
    t: f32,
    started: bool,
    moving: bool,
}

impl StereoFader {
    /// 0..1, smoothstep of the elapsed fade.
    pub fn update(&mut self, now_ms: f64, on: bool) -> f32 {
        let dt = if self.started {
            now_ms - self.last_ms
        } else {
            0.0
        };
        self.started = true;
        self.last_ms = now_ms;
        let before = self.t;
        self.t = clamp(
            self.t + (dt / STEREO_FADE_MS) as f32 * if on { 1.0 } else { -1.0 },
            0.0,
            1.0,
        );
        self.moving = if dt <= 0.0 && on && self.t <= 0.0 {
            true // just switched on: the next frame moves
        } else {
            self.t != before || (on && self.t < 1.0)
        };
        stereo_smoothstep(0.0, 1.0, self.t)
    }
    pub fn moving(&self) -> bool {
        self.moving
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamps_and_wire() {
        let p = stereo_clamped(&StereoParams {
            strength: f32::NAN,
            convergence: 3.0,
            near_deg: 9.0,
            far_deg: -1.0,
            text_keep: f32::INFINITY,
        });
        assert_eq!(p.strength, 0.6);
        assert_eq!(p.convergence, 1.0);
        assert_eq!(p.near_deg, STEREO_NEAR_DEG_MAX);
        assert_eq!(p.far_deg, 0.0);
        assert_eq!(p.text_keep, 0.15);
        let w = ImmersiveParams {
            strength: 150,
            convergence: 92,
            near_arcmin: 4,
            far_arcmin: 14,
            text: 0,
            range_ms: 3000,
            strengths: None,
        };
        let s = stereo_from_wire(&w);
        assert_eq!(s.strength, 1.5);
        assert_eq!(s.convergence, 0.4);
        assert!((s.near_deg - 4.0 / 60.0).abs() < 1e-7 && (s.far_deg - 14.0 / 60.0).abs() < 1e-7);
        assert_eq!(s.text_keep, 0.0);
    }

    #[test]
    fn v_knee_and_limits() {
        let p = StereoParams {
            strength: 1.0,
            convergence: 0.5,
            ..StereoParams::default()
        };
        assert_eq!(stereo_v(0.6, &p), 2.0 * 1.0 * (0.6 - 0.5));
        assert!(stereo_v(1.0, &p) < 1.0 && stereo_v(1.0, &p) > STEREO_KNEE);
        assert_eq!(stereo_v(0.0, &p), -stereo_v(1.0, &p));
        assert_eq!(stereo_v(f32::NAN, &p), 0.0);
        assert!(stereo_nearness(f32::INFINITY, 0.0, 1.0).is_nan());
        assert_eq!(stereo_nearness(5.0, 5.0, 5.0), 0.5);
        let l = stereo_limits(&StereoParams::default(), 1920.0, 1.0);
        // 90 degrees across 1920 px: 21.33 px per degree.
        assert!((l.near_px - 1920.0 / 90.0).abs() < 1e-3);
        assert!((l.far_px - 0.75 * 1920.0 / 90.0).abs() < 1e-3);
        assert_eq!(stereo_limits(&p, 0.0, 1.0), StereoLimits::default());
    }

    #[test]
    fn plane_baseline_budget() {
        assert_eq!(stereo_plane_disparity(0.01, -1.0, 1920.0, 1.0), 0.0);
        assert!(stereo_plane_disparity(0.01, -0.5, 1920.0, 1.0) > 0.0); // nearer than the plane: crossed
        assert!(stereo_plane_disparity(0.01, -2.0, 1920.0, 1.0) < 0.0);
        let l = StereoLimits {
            near_px: 10.0,
            far_px: 5.0,
        };
        let b = stereo_safe_baseline(0.01, &[-0.2], &l, 1920.0, 1.0);
        assert!((stereo_plane_disparity(b, -0.2, 1920.0, 1.0) - 10.0).abs() < 1e-3);
        let budget = stereo_budget(&l, 4.0, 1.0, 1.0);
        assert_eq!(budget, StereoBudget { near_half: 3.0, far_half: 2.5 });
        assert_eq!(stereo_half_disparity(-1.0, &budget), -2.5);
        assert_eq!(stereo_tile_scale(100.0, 0.0, 1.0), STEREO_MIN_SCALE);
        assert_eq!(stereo_tile_scale(1920.0, 1920.0, 0.5), 1.0);
    }

    #[test]
    fn fader_eases() {
        let mut f = StereoFader::default();
        assert_eq!(f.update(0.0, true), 0.0);
        assert!(f.moving());
        assert_eq!(f.update(150.0, true), 0.5);
        assert_eq!(f.update(400.0, true), 1.0);
        assert!(!f.moving());
        assert_eq!(f.update(550.0, false), 0.5);
        assert!(f.moving());
    }
}
