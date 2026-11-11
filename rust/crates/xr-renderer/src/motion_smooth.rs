//! Frame-rate motion from a slow, uneven pose feed (port of `spike/include/xr/motion_smooth.hpp` and
//! `spike/src/motion_smooth.cpp`). Off unless a run asks for it (`--pro2-test-smooth-ms`): today only the Pro 2.
//!
//! The output is a state of its own, stepped once per frame:
//!
//! ```text
//! output = output * exp(velocity * dt)                  carry on at the measured speed: every frame moves
//! output = output * exp(error * (1 - exp(-dt / tau)))   then ease out what the newest prediction says is left over
//! ```
//!
//! where `error = output^-1 * target` and `target = newest sample * exp(velocity * horizon)`. velocity is the rotation
//! between the newest and the second-oldest of the last three samples over their real device-time gap, braked by the
//! newest interval's share of it. The bounds (horizon cap, brake, max gap, stall, snap) are the C++ header's.
//! Allocation free, no clock reads, no I/O. All maths is `f32` (and `f64` where the C++ uses `double`) in the C++ order.

use std::f32::consts::PI;

use crate::pose::{Quat, quat_conj, quat_mul, quat_normalized};

// kPi = 3.14159265358979f: the same f32 as PI.
const RAD_TO_DEG: f32 = 180.0 / PI;
const TURN_MIN_DPS: f32 = 15.0;

type Vec3 = [f32; 3];

/// `std::max`: the first unless it is less than the second.
fn max_f(a: f32, b: f32) -> f32 {
    if a < b { b } else { a }
}
/// `std::min`: the first unless the second is less.
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
fn clamp_d(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

/// Rotation vector, radians.
fn log_of(mut q: Quat) -> Vec3 {
    if q.w < 0.0 {
        q = Quat {
            x: -q.x,
            y: -q.y,
            z: -q.z,
            w: -q.w,
        };
    }
    let s = (q.x * q.x + q.y * q.y + q.z * q.z).sqrt();
    if s < 1e-9 {
        return [2.0 * q.x, 2.0 * q.y, 2.0 * q.z];
    }
    let k = 2.0 * s.atan2(q.w) / s;
    [q.x * k, q.y * k, q.z * k]
}

fn exp_of(v: Vec3) -> Quat {
    let a = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if a < 1e-9 {
        return quat_normalized(Quat {
            x: v[0] / 2.0,
            y: v[1] / 2.0,
            z: v[2] / 2.0,
            w: 1.0,
        })
        .unwrap_or_default();
    }
    let s = (a / 2.0).sin() / a;
    Quat {
        x: v[0] * s,
        y: v[1] * s,
        z: v[2] * s,
        w: (a / 2.0).cos(),
    }
}

fn norm(v: Vec3) -> f32 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

fn unit(q: Quat) -> Quat {
    quat_normalized(q).unwrap_or_default()
}

/// `xr::MotionSmoothParams`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MotionSmoothParams {
    /// Continuity filter time constant.
    pub tau_ms: f32,
    /// Rotation the prediction may add to the newest sample.
    pub max_predict_deg: f32,
    /// A larger error than this is taken at once: 1500 deg/s in a 20 ms gap, past any head.
    pub snap_deg: f32,
    /// A longer gap between samples has no usable velocity.
    pub max_gap_ms: f32,
    /// Past the longest normal wait seen by a reader: the 20 ms gap, the 2 ms poll, a frame's 8 ms.
    pub stall_ms: f32,
    /// Velocity scale while slowing: (newest interval's share of the window's speed) ^ this.
    pub brake_power: f32,
    /// 4 deg/s: speeds around this are noise, not a stop.
    pub brake_floor_rad_per_ms: f32,
}

impl Default for MotionSmoothParams {
    fn default() -> Self {
        Self {
            tau_ms: 35.0,
            max_predict_deg: 6.0,
            snap_deg: 30.0,
            max_gap_ms: 60.0,
            stall_ms: 40.0,
            brake_power: 3.0,
            brake_floor_rad_per_ms: 4.0 * PI / 180000.0, // 4.f * 3.14159265f / 180000.f
        }
    }
}

/// tau 5..80 ms, else `None`.
pub fn motion_smooth_params(tau_ms: f32) -> Option<MotionSmoothParams> {
    if !tau_ms.is_finite() || tau_ms < 5.0 || tau_ms > 80.0 {
        return None;
    }
    Some(MotionSmoothParams {
        tau_ms,
        ..MotionSmoothParams::default()
    })
}

/// `xr::MotionSmoother`.
#[derive(Clone, Debug)]
pub struct MotionSmoother {
    p: MotionSmoothParams,
    valid: bool,
    output: Quat,
    last_ms: f64,
    new_sample_ms: f64,
    /// Newest first, distinct device times.
    q: [Quat; 3],
    t: [f32; 3],
    count: u32,
    snaps: u64,
    capped_frames: u64,
    frames: u64,
    peak_speed_dps: f32,
    turn_sum: [f64; 3],
    turn_ms: f64,
    last_correction_deg: f32,
    last_predicted_deg: f32,
    last_horizon_ms: f32,
    max_predicted_deg: f32,
}

impl Default for MotionSmoother {
    fn default() -> Self {
        Self::new(MotionSmoothParams::default())
    }
}

impl MotionSmoother {
    pub fn new(p: MotionSmoothParams) -> Self {
        Self {
            p,
            valid: false,
            output: Quat::default(),
            last_ms: 0.0,
            new_sample_ms: 0.0,
            q: [Quat::default(); 3],
            t: [0.0; 3],
            count: 0,
            snaps: 0,
            capped_frames: 0,
            frames: 0,
            peak_speed_dps: 0.0,
            turn_sum: [0.0; 3],
            turn_ms: 0.0,
            last_correction_deg: 0.0,
            last_predicted_deg: 0.0,
            last_horizon_ms: 0.0,
            max_predicted_deg: 0.0,
        }
    }

    pub fn reset(&mut self) {
        self.valid = false;
        self.count = 0;
    }

    pub fn snaps(&self) -> u64 {
        self.snaps
    }
    pub fn last_correction_deg(&self) -> f32 {
        self.last_correction_deg
    }
    pub fn last_predicted_deg(&self) -> f32 {
        self.last_predicted_deg
    }
    pub fn last_horizon_ms(&self) -> f32 {
        self.last_horizon_ms
    }
    pub fn max_predicted_deg(&self) -> f32 {
        self.max_predicted_deg
    }
    /// The fastest the head was measured going.
    pub fn peak_speed_dps(&self) -> f32 {
        self.peak_speed_dps
    }
    /// Frames that wanted more than `max_predict_deg`.
    pub fn capped_frames(&self) -> u64 {
        self.capped_frames
    }
    pub fn frames(&self) -> u64 {
        self.frames
    }
    /// The axis the head actually turned about, in this feed's frame, from the fast yaw of the whole run: 0 means this
    /// frame's up is it. Positive leans its top forward.
    pub fn turn_axis_forward_deg(&self) -> f32 {
        self.turn_axis(0)
    }
    pub fn turn_axis_left_deg(&self) -> f32 {
        self.turn_axis(1)
    }
    pub fn turn_seconds(&self) -> f32 {
        (self.turn_ms / 1000.0) as f32
    }

    fn turn_axis(&self, component: usize) -> f32 {
        if !(self.turn_sum[2] > 0.0) {
            return 0.0;
        }
        (self.turn_sum[component].atan2(self.turn_sum[2]) as f32) * RAD_TO_DEG
    }

    fn learn(&mut self, relative: Quat, device_ms: f32) {
        if self.count != 0 && device_ms == self.t[0] {
            return;
        }
        // A new clock epoch, or a jump no head makes between samples (a recentre, a reanchor): nothing before it is comparable.
        let broken = self.count != 0
            && (!(device_ms > self.t[0])
                || norm(log_of(quat_mul(quat_conj(self.q[0]), relative))) * RAD_TO_DEG
                    > self.p.snap_deg);
        if broken {
            self.count = 0;
        }
        self.q[2] = self.q[1];
        self.t[2] = self.t[1];
        self.q[1] = self.q[0];
        self.t[1] = self.t[0];
        self.q[0] = relative;
        self.t[0] = device_ms;
        if self.count < 3 {
            self.count += 1;
        }
    }

    fn velocity(&self) -> Vec3 {
        if self.count < 2 {
            return [0.0; 3];
        }
        let oldest = if self.count >= 3 { 2 } else { 1 };
        let dt = self.t[0] - self.t[oldest];
        if !dt.is_finite() || dt <= 0.0 {
            return [0.0; 3];
        }
        for i in 0..oldest {
            if self.t[i] - self.t[i + 1] > self.p.max_gap_ms {
                return [0.0; 3];
            }
        }
        let mut v = log_of(quat_mul(quat_conj(self.q[oldest]), self.q[0]));
        for c in &mut v {
            *c /= dt;
        }
        v
    }

    /// `relative`: the newest (stabilized) sample, recentred; it only changes when `device_ms` does. `horizon_ms`: from
    /// that sample to the frame's presentation. `now_ms`: the frame's own clock (any clock that only goes forward).
    /// `measured`: body-frame velocity in rad/ms, already braked (the bridge's pose_prediction wire); it replaces the
    /// estimate from the samples seen here. Returns the pose to show.
    pub fn update(
        &mut self,
        relative: Quat,
        device_ms: f32,
        horizon_ms: f32,
        now_ms: f64,
        measured: Option<[f32; 3]>,
    ) -> Quat {
        let relative = unit(relative);
        if !device_ms.is_finite() || !now_ms.is_finite() {
            self.reset();
            return relative;
        }
        let fresh_sample = self.count == 0 || device_ms != self.t[0];
        if fresh_sample {
            self.new_sample_ms = now_ms;
        }
        self.learn(relative, device_ms);
        let measured = measured.filter(|m| (m[0] + m[1] + m[2]).is_finite());
        let braked = measured.is_some();
        let mut v = measured.unwrap_or_else(|| self.velocity());
        self.peak_speed_dps = max_f(self.peak_speed_dps, norm(v) * RAD_TO_DEG * 1000.0);
        if fresh_sample && self.count == 3 {
            // One vote per sample, and only for a real turn: fast, and mostly about up.
            let speed = norm(v);
            let gap = self.t[0] - self.t[1];
            if speed * RAD_TO_DEG * 1000.0 > TURN_MIN_DPS && v[2].abs() > 0.8 * speed {
                let way = if v[2] > 0.0 { gap } else { -gap };
                for i in 0..3 {
                    self.turn_sum[i] += f64::from(v[i]) * f64::from(way);
                }
                self.turn_ms += f64::from(gap);
            }
        }
        self.frames += 1;
        // The newest interval against the window: 1 at a steady speed, towards 0 as the head stops, 0 when it turns back.
        let mut keep = 1.0f32;
        if !braked && self.count == 3 && self.t[0] > self.t[1] {
            let mut newest = log_of(quat_mul(quat_conj(self.q[1]), self.q[0]));
            for c in &mut newest {
                *c /= self.t[0] - self.t[1];
            }
            let e2 = self.p.brake_floor_rad_per_ms * self.p.brake_floor_rad_per_ms;
            let share = (newest[0] * v[0] + newest[1] * v[1] + newest[2] * v[2] + e2)
                / (v[0] * v[0] + v[1] * v[1] + v[2] * v[2] + e2);
            keep = clamp_f(share, 0.0, 1.0).powf(self.p.brake_power);
        }
        for c in &mut v {
            *c *= keep;
        }
        let horizon = if horizon_ms.is_finite() {
            max_f(horizon_ms, 0.0)
        } else {
            0.0
        };
        let mut ahead = [v[0] * horizon, v[1] * horizon, v[2] * horizon];
        let angle = norm(ahead);
        let limit = self.p.max_predict_deg / RAD_TO_DEG;
        if angle > limit {
            for c in &mut ahead {
                *c *= limit / angle;
            }
            self.capped_frames += 1;
        }
        let target = unit(quat_mul(self.q[0], exp_of(ahead)));
        self.last_horizon_ms = horizon;
        self.last_predicted_deg = min_f(angle, limit) * RAD_TO_DEG;
        self.max_predicted_deg = max_f(self.max_predicted_deg, self.last_predicted_deg);
        if !self.valid {
            self.valid = true;
            self.output = target;
            self.last_ms = now_ms;
            self.last_correction_deg = 0.0;
            return self.output;
        }
        let dt = clamp_d(now_ms - self.last_ms, 0.0, 100.0) as f32;
        self.last_ms = now_ms;
        // Carried only while the feed is alive: a stalled one still has its last speed in the history, and must not
        // coast past the bound.
        let carry = if now_ms - self.new_sample_ms > f64::from(self.p.stall_ms) {
            0.0
        } else {
            dt
        };
        let carried = unit(quat_mul(
            self.output,
            exp_of([v[0] * carry, v[1] * carry, v[2] * carry]),
        ));
        let error = log_of(quat_mul(quat_conj(carried), target));
        let size = norm(error);
        self.last_correction_deg = size * RAD_TO_DEG;
        if size * RAD_TO_DEG > self.p.snap_deg {
            self.output = target;
            self.snaps += 1;
            return self.output;
        }
        let k = 1.0 - (-dt / self.p.tau_ms).exp();
        self.output = unit(quat_mul(
            carried,
            exp_of([error[0] * k, error[1] * k, error[2] * k]),
        ));
        self.output
    }
}
