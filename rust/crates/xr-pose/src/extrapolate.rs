//! `luma::extrapolate`: the driver's motion carried forward over a horizon.
//!
//! A port of `extrapolate` and the helpers it uses (`path`, `gated_path`, `exp_map`, `mul`,
//! `normalized`, `smoothstep`) in `spike/driver/src/predict.cpp`. The f32 maths runs in the C++
//! order, and `std::min` and `std::clamp` are mirrored, so NaN takes the same branch. The results
//! are bit-equal with the C++: `tests/extrapolate.rs` compares them with `golden/extrapolate.txt`,
//! which `golden/extrapolate.cpp` writes from the C++ itself.
//!
//! The frame is the fusion's body frame: q(t + dt) = q(t) * exp(omega * dt). Quaternions are
//! `[w, x, y, z]`, as in [`MotionState`].

use crate::prediction::MotionState;

const PI: f32 = std::f32::consts::PI; // kPi = 3.14159265358979f: the same f32
const DEG_TO_RAD: f32 = PI / 180.0;

/// The fields of `luma::PredictConfig` that `extrapolate` reads, with the C++ defaults. The rest
/// (filter memories, spike and gap limits, rates, look-ahead) belong to `luma::Predictor`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PredictConfig {
    /// Off: predictions are the newest pose.
    pub enabled: bool,
    pub use_acceleration: bool,
    /// Extrapolation ends where the velocity reaches zero.
    pub stop_clamp: bool,
    /// A still head is not extrapolated, so gyro noise cannot shake it.
    pub still_gate: bool,
    /// No prediction below `still_lo_dps`, all of it above `still_hi_dps`.
    pub still_lo_dps: f32,
    pub still_hi_dps: f32,
    /// The renderer's `kMaxPredictionDeg`.
    pub max_deg: f32,
}

impl Default for PredictConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            use_acceleration: true,
            stop_clamp: true,
            still_gate: true,
            still_lo_dps: 2.5,
            still_hi_dps: 8.0,
            max_deg: 15.0,
        }
    }
}

/// `luma::Predicted`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Predicted {
    /// `[w, x, y, z]`.
    pub q: [f32; 4],
    pub target_ns: u64,
    pub horizon_ms: f32,
    pub predicted_deg: f32,
    /// The `max_deg` limit was reached.
    pub clamped: bool,
}

fn dot(a: [f32; 3], b: [f32; 3]) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn length(v: [f32; 3]) -> f32 {
    dot(v, v).sqrt()
}
/// `std::min`: `b < a ? b : a`.
fn min_f(a: f32, b: f32) -> f32 {
    if b < a {
        b
    } else {
        a
    }
}
/// `std::clamp`: `v < lo ? lo : hi < v ? hi : v`.
fn clamp_f(v: f32, lo: f32, hi: f32) -> f32 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}
fn mul(a: [f32; 4], b: [f32; 4]) -> [f32; 4] {
    let [aw, ax, ay, az] = a;
    let [bw, bx, by, bz] = b;
    [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]
}
fn exp_map(r: [f32; 3]) -> [f32; 4] {
    let a = length(r);
    let k = if a < 1e-6 { 0.5 } else { (a / 2.0).sin() / a };
    [(a / 2.0).cos(), r[0] * k, r[1] * k, r[2] * k]
}
/// Identity when degenerate, NaN included.
fn normalized(q: [f32; 4]) -> [f32; 4] {
    let [w, x, y, z] = q;
    let n = (w * w + x * x + y * y + z * z).sqrt();
    if n > 1e-6 {
        [w / n, x / n, y / n, z / n]
    } else {
        [1.0, 0.0, 0.0, 0.0]
    }
}
fn smoothstep(lo: f32, hi: f32, v: f32) -> f32 {
    if !(hi > lo) {
        return if v >= hi { 1.0 } else { 0.0 };
    }
    let u = clamp_f((v - lo) / (hi - lo), 0.0, 1.0);
    u * u * (3.0 - 2.0 * u)
}

/// The rotation vector over the next horizon_s, before the gate and the limit.
fn path(s: &MotionState, horizon_s: f32, cfg: &PredictConfig) -> [f32; 3] {
    let mut t = horizon_s;
    if cfg.use_acceleration && cfg.stop_clamp {
        let speed = length(s.omega_rad_s);
        let along = if speed > 0.0 {
            dot(s.omega_rad_s, s.alpha_rad_s2) / speed
        } else {
            0.0
        };
        if along < 0.0 {
            t = min_f(t, -speed / along); // braking: the head stops, it does not reverse
        }
    }
    let a = if cfg.use_acceleration {
        0.5 * t * t
    } else {
        0.0
    };
    let mut r = [0.0f32; 3];
    for i in 0..3 {
        r[i] = s.omega_rad_s[i] * t + s.alpha_rad_s2[i] * a;
    }
    r
}

fn gated_path(
    s: &MotionState,
    horizon_ms: f32,
    cfg: &PredictConfig,
    clamped: &mut bool,
) -> [f32; 3] {
    let mut r = path(s, horizon_ms / 1000.0, cfg);
    if cfg.still_gate {
        let gain = smoothstep(
            cfg.still_lo_dps * DEG_TO_RAD,
            cfg.still_hi_dps * DEG_TO_RAD,
            length(s.omega_rad_s),
        );
        for v in &mut r {
            *v *= gain;
        }
    }
    let angle = length(r);
    let limit = cfg.max_deg * DEG_TO_RAD;
    if angle > limit {
        for v in &mut r {
            *v *= limit / angle;
        }
        *clamped = true;
    }
    r
}

/// `luma::extrapolate`: the state's pose horizon_ms past its `host_ns`. The newest pose unchanged
/// when prediction is off, the state is invalid or uncalibrated, or the horizon is not positive.
///
/// `target_ns` is `host_ns + horizon in ns`, wrapping like the C++ `uint64_t` sum. The one place it
/// can differ: a horizon of 2^64 ns or more, where the C++ conversion is undefined and Rust's
/// saturates.
pub fn extrapolate(s: &MotionState, horizon_ms: f32, cfg: &PredictConfig) -> Predicted {
    let ahead = if horizon_ms > 0.0 {
        (f64::from(horizon_ms) * 1e6) as u64
    } else {
        0
    };
    let mut out = Predicted {
        q: s.q,
        target_ns: s.host_ns.wrapping_add(ahead),
        horizon_ms,
        predicted_deg: 0.0,
        clamped: false,
    };
    if !cfg.enabled || !s.valid || !s.calibrated || !(horizon_ms > 0.0) {
        return out;
    }
    let r = gated_path(s, horizon_ms, cfg, &mut out.clamped);
    out.q = normalized(mul(s.q, exp_map(r)));
    out.predicted_deg = length(r) / DEG_TO_RAD;
    out
}
