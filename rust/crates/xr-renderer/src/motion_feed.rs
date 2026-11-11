//! D3 of docs/rust-rebuild-plan.md (port of `spike/include/xr/motion_feed.hpp` and `spike/src/motion_feed.cpp`): the
//! renderer predicts from the Luma driver's `PredictionWire` (SysV segment `<prefix>pose_prediction`) instead of
//! differencing the feed's q0 and q1. The wire holds the driver's newest pose with the gyro's filtered velocity and
//! acceleration, body frame, in the feed's frame, and that sample's arrival on CLOCK_MONOTONIC. XRLinuxDriver has no such
//! segment, so a renderer on it keeps the finite difference.
//!
//! The wire itself is `xr_pose::prediction`'s. `luma::extrapolate` (spike/driver/src/predict.cpp) has no Rust port yet,
//! so the part `motion_delta` needs (the default `PredictConfig` with its own `max_deg`) is mirrored here, privately.

use std::ffi::CString;
use std::ptr;

use xr_pose::prediction::{PREDICTION_IPC_NAME, PredictionWire};

use crate::pose::{Quat, quat_conj, quat_mul, quat_normalized};

/// `xr::FeedMotion`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct FeedMotion {
    pub q: Quat,
    pub host_ns: u64,
    pub omega_rad_s: [f32; 3],
    pub alpha_rad_s2: [f32; 3],
}

/// `xr::MotionFeed`: the driver's `PredictionWire`, attached read-only.
pub struct MotionFeed {
    wire: *const PredictionWire,
}

impl MotionFeed {
    /// The same prefix as the pose feed's. `None` when the driver publishes no wire.
    pub fn open(prefix: &str) -> Option<Self> {
        let path = CString::new(format!("{prefix}{PREDICTION_IPC_NAME}")).ok()?;
        // SAFETY: path is a valid C string; ftok only stats it. It fails when the driver made no file: no wire.
        let key = unsafe { libc::ftok(path.as_ptr(), 0) };
        // SAFETY: plain syscalls on a key and an id; info is a valid out-pointer.
        let id = if key == -1 { -1 } else { unsafe { libc::shmget(key, 0, 0) } };
        let mut info: libc::shmid_ds = unsafe { std::mem::zeroed() };
        if id < 0
            || unsafe { libc::shmctl(id, libc::IPC_STAT, &mut info) } != 0
            || info.shm_segsz < std::mem::size_of::<PredictionWire>()
        {
            return None;
        }
        // SAFETY: id is a live segment at least one PredictionWire long; attached read-only, detached in Drop.
        let addr = unsafe { libc::shmat(id, ptr::null(), libc::SHM_RDONLY) };
        if addr as isize == -1 {
            return None;
        }
        Some(Self { wire: addr.cast() })
    }

    /// The newest calibrated sample: a bounded seqlock read that never waits. `None` when the wire holds none (disabled,
    /// paused, uncalibrated, mid-write, the driver exited) or what it holds is not finite.
    pub fn read(&self) -> Option<FeedMotion> {
        // SAFETY: wire points at a mapped, page-aligned segment of at least size_of::<PredictionWire>() bytes, alive
        // until Drop; the wire is atomics only, so another process writing it is fine.
        let m = unsafe { &*self.wire }.read()?;
        if !m.calibrated {
            return None;
        }
        let [w, x, y, z] = m.q;
        let q = quat_normalized(Quat { x, y, z, w })?;
        for i in 0..3 {
            if !m.omega_rad_s[i].is_finite() || !m.alpha_rad_s2[i].is_finite() {
                return None;
            }
        }
        Some(FeedMotion { q, host_ns: m.host_ns, omega_rad_s: m.omega_rad_s, alpha_rad_s2: m.alpha_rad_s2 })
    }
}

// SAFETY: the mapping is read-only atomics, valid from any thread until Drop.
unsafe impl Send for MotionFeed {}

impl Drop for MotionFeed {
    fn drop(&mut self) {
        // SAFETY: wire came from shmat and is detached once.
        unsafe { libc::shmdt(self.wire.cast()) };
    }
}

// luma::PredictConfig's defaults, for extrapolate.
const LUMA_PI: f32 = std::f32::consts::PI; // kPi = 3.14159265358979f: the same f32
const DEG_TO_RAD: f32 = LUMA_PI / 180.0;
const STILL_LO_DPS: f32 = 2.5;
const STILL_HI_DPS: f32 = 8.0;

fn dot(a: [f32; 3], b: [f32; 3]) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn length(v: [f32; 3]) -> f32 {
    dot(v, v).sqrt()
}
/// `std::min`.
fn min_f(a: f32, b: f32) -> f32 {
    if b < a { b } else { a }
}
/// `std::clamp`.
fn clamp_f(v: f32, lo: f32, hi: f32) -> f32 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}
fn smoothstep(lo: f32, hi: f32, v: f32) -> f32 {
    if !(hi > lo) {
        return if v >= hi { 1.0 } else { 0.0 };
    }
    let u = clamp_f((v - lo) / (hi - lo), 0.0, 1.0);
    u * u * (3.0 - 2.0 * u)
}
/// luma's `exp_map`: `[w, x, y, z]` as a Quat.
fn exp_map(r: [f32; 3]) -> Quat {
    let a = length(r);
    let k = if a < 1e-6 { 0.5 } else { (a / 2.0).sin() / a };
    Quat { x: r[0] * k, y: r[1] * k, z: r[2] * k, w: (a / 2.0).cos() }
}
/// luma's `normalized`: summed w first, identity when degenerate (NaN included).
fn luma_normalized(q: Quat) -> Quat {
    let n = (q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z).sqrt();
    if n > 1e-6 { Quat { x: q.x / n, y: q.y / n, z: q.z / n, w: q.w / n } } else { Quat::default() }
}
/// luma's `gated_path` with acceleration, the stop clamp and the still gate on: the rotation vector over horizon_ms.
fn gated_path(m: &FeedMotion, horizon_ms: f32, max_deg: f32) -> [f32; 3] {
    let mut t = horizon_ms / 1000.0;
    let speed = length(m.omega_rad_s);
    let along = if speed > 0.0 { dot(m.omega_rad_s, m.alpha_rad_s2) / speed } else { 0.0 };
    if along < 0.0 {
        t = min_f(t, -speed / along); // braking: the head stops, it does not reverse
    }
    let a = 0.5 * t * t;
    let mut r = [0.0f32; 3];
    for i in 0..3 {
        r[i] = m.omega_rad_s[i] * t + m.alpha_rad_s2[i] * a;
    }
    let gain = smoothstep(STILL_LO_DPS * DEG_TO_RAD, STILL_HI_DPS * DEG_TO_RAD, length(m.omega_rad_s));
    for v in &mut r {
        *v *= gain;
    }
    let angle = length(r);
    let limit = max_deg * DEG_TO_RAD;
    if angle > limit {
        for v in &mut r {
            *v *= limit / angle;
        }
    }
    r
}

/// The delta to apply on the right of the wire's pose (newest * delta, like `prediction_delta`): luma's `extrapolate`
/// horizon_ms past the sample's arrival, with acceleration, the stop clamp, the still gate and max_deg. Identity for
/// horizon <= 0.
pub fn motion_delta(m: &FeedMotion, horizon_ms: f32, max_deg: f32) -> Quat {
    if !(horizon_ms > 0.0) {
        return Quat::default();
    }
    let r = gated_path(m, horizon_ms, max_deg);
    let p = luma_normalized(quat_mul(m.q, exp_map(r)));
    quat_normalized(quat_mul(quat_conj(m.q), p)).unwrap_or_default()
}

/// Older than this, a sample's motion says nothing about now (a stopped driver).
pub const MAX_MOTION_AGE_MS: f64 = 50.0;

/// The sample's age at now_ns (both CLOCK_MONOTONIC), or `None` when it cannot be used: from the future, or older than
/// `MAX_MOTION_AGE_MS`.
pub fn motion_age_ms(m: &FeedMotion, now_ns: u64) -> Option<f64> {
    if now_ns < m.host_ns {
        return None;
    }
    let age = (now_ns - m.host_ns) as f64 / 1e6;
    if age > MAX_MOTION_AGE_MS {
        return None;
    }
    Some(age)
}
