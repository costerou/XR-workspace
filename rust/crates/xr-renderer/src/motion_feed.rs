//! D3 of docs/rust-rebuild-plan.md (port of `spike/include/xr/motion_feed.hpp` and `spike/src/motion_feed.cpp`): the
//! renderer predicts from the Luma driver's `PredictionWire` (SysV segment `<prefix>pose_prediction`) instead of
//! differencing the feed's q0 and q1. The wire holds the driver's newest pose with the gyro's filtered velocity and
//! acceleration, body frame, in the feed's frame, and that sample's arrival on CLOCK_MONOTONIC. XRLinuxDriver has no such
//! segment, so a renderer on it keeps the finite difference.
//!
//! The wire itself is `xr_pose::prediction`'s and `luma::extrapolate` is `xr_pose::extrapolate`'s, so `motion_delta` is
//! bit-equal with the C++ (tests/motion_feed.rs against golden/motion_feed.txt).

use std::ffi::CString;
use std::ptr;

use xr_pose::MotionState;
use xr_pose::extrapolate::{PredictConfig, extrapolate};
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

/// The delta to apply on the right of the wire's pose (newest * delta, like `prediction_delta`): `luma::extrapolate`
/// horizon_ms past the sample's arrival, with the default config but max_deg. Identity for horizon <= 0.
pub fn motion_delta(m: &FeedMotion, horizon_ms: f32, max_deg: f32) -> Quat {
    if !(horizon_ms > 0.0) {
        return Quat::default();
    }
    let state = MotionState {
        q: [m.q.w, m.q.x, m.q.y, m.q.z],
        host_ns: m.host_ns,
        omega_rad_s: m.omega_rad_s,
        alpha_rad_s2: m.alpha_rad_s2,
        valid: true,
        calibrated: true,
        ..MotionState::default()
    };
    let [w, x, y, z] = extrapolate(&state, horizon_ms, &PredictConfig { max_deg, ..PredictConfig::default() }).q;
    quat_normalized(quat_mul(quat_conj(m.q), Quat { x, y, z, w })).unwrap_or_default()
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
