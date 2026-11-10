//! IMU samples in, gravity-levelled orientation out. A straight port of
//! `spike/driver/src/fusion.cpp`, held to it bit for bit by `tests/fusion_parity.rs`.
//!
//! Written from the textbook: Mahony, Hamel, Pflimlin, "Nonlinear complementary filters on the
//! special orthogonal group", IEEE TAC 53(5), 2008 (the gravity correction and gyro-bias
//! integrator); Savage, "Strapdown inertial navigation integration algorithm design", JGCD 21(1),
//! 1998 (the rotation-vector step). No vendor code or SDK.
//!
//! Input frame: body x forward, y left, z up, right-handed. Accelerometer: specific force, so
//! level and still reads +9.8 on z.
//!
//! Output: [`PoseOut::q`] turns body vectors into world vectors, with world z exactly up (true
//! gravity). World x is the heading the glasses faced when calibration finished. Yaw has no
//! absolute reference without the magnetometer.
//!
//! [`Fusion::update`] allocates nothing and takes no lock. One thread owns a `Fusion`.
//!
//! Bit parity with the C++ rests on doing the same IEEE operations in the same order: every
//! expression below keeps the C++ evaluation order and its float-to-double promotions, and the
//! transcendental calls (`sin`, `cos`, `atan2`, `hypot`) go to the same libm. No FMA: neither
//! build targets above baseline x86-64. Do not "tidy" an expression here without re-running the
//! parity test.

use crate::types::{ImuSample, PoseOut, Quatf};

/// Double precision throughout: at 1 kHz a sample's rotation is around 1e-5 rad, which single
/// precision quaternion arithmetic rounds into a drift of its own.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct V([f64; 3]);

impl std::ops::Add for V {
    type Output = V;
    fn add(self, b: V) -> V {
        V([self.0[0] + b.0[0], self.0[1] + b.0[1], self.0[2] + b.0[2]])
    }
}
impl std::ops::Sub for V {
    type Output = V;
    fn sub(self, b: V) -> V {
        V([self.0[0] - b.0[0], self.0[1] - b.0[1], self.0[2] - b.0[2]])
    }
}
impl std::ops::Mul<f64> for V {
    type Output = V;
    fn mul(self, k: f64) -> V {
        V([self.0[0] * k, self.0[1] * k, self.0[2] * k])
    }
}

fn dot(a: V, b: V) -> f64 {
    a.0[0] * b.0[0] + a.0[1] * b.0[1] + a.0[2] * b.0[2]
}
fn norm(a: V) -> f64 {
    dot(a, a).sqrt()
}
fn cross(a: V, b: V) -> V {
    let (a, b) = (a.0, b.0);
    V([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])
}
fn to_v(a: [f32; 3]) -> V {
    V([f64::from(a[0]), f64::from(a[1]), f64::from(a[2])])
}
fn to_f(a: V) -> [f32; 3] {
    [a.0[0] as f32, a.0[1] as f32, a.0[2] as f32]
}
fn finite(a: &[f32; 3]) -> bool {
    a.iter().all(|v| v.is_finite())
}
/// `std::min`: the first unless the second is smaller.
fn min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}
/// `std::max`: the first unless it is smaller.
fn max(a: f64, b: f64) -> f64 {
    if a < b { b } else { a }
}
/// `std::clamp`.
fn clamp(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

#[derive(Clone, Copy, Debug)]
struct Q {
    w: f64,
    x: f64,
    y: f64,
    z: f64,
}

impl Default for Q {
    fn default() -> Self {
        Q { w: 1.0, x: 0.0, y: 0.0, z: 0.0 }
    }
}

fn mul(a: Q, b: Q) -> Q {
    Q {
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    }
}
fn normalized(q: Q) -> Q {
    let n = (q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z).sqrt();
    Q { w: q.w / n, x: q.x / n, y: q.y / n, z: q.z / n }
}
fn exp_map(phi: V) -> Q {
    let a = norm(phi);
    let k = if a < 1e-8 { 0.5 - a * a / 48.0 } else { (a / 2.0).sin() / a };
    Q { w: (a / 2.0).cos(), x: phi.0[0] * k, y: phi.0[1] * k, z: phi.0[2] * k }
}
/// World up in the body frame: third row of the rotation matrix.
fn up_in_body(q: Q) -> V {
    V([
        2.0 * (q.x * q.z - q.w * q.y),
        2.0 * (q.y * q.z + q.w * q.x),
        1.0 - 2.0 * (q.x * q.x + q.y * q.y),
    ])
}
fn to_world(q: Q, v: V) -> V {
    let u = V([q.x, q.y, q.z]);
    v + cross(u, cross(u, v) + v * q.w) * 2.0
}
fn to_quatf(q: Q) -> Quatf {
    Quatf { w: q.w as f32, x: q.x as f32, y: q.y as f32, z: q.z as f32 }
}

/// Attitude with `up` (unit, body frame) as world up and body forward facing world x. With the
/// nose straight up or down, forward is the direction the top of the glasses points.
fn attitude_from_up(up: V) -> Q {
    let mut fwd = V([1.0, 0.0, 0.0]);
    if up.0[0].abs() > 0.99 {
        fwd = V([0.0, 0.0, if up.0[0] > 0.0 { -1.0 } else { 1.0 }]);
    }
    let mut x = fwd - up * dot(fwd, up);
    x = x * (1.0 / norm(x));
    let y = cross(up, x);
    // Rotation matrix rows x, y, up (Shepperd's conversion).
    let [m00, m01, m02] = x.0;
    let [m10, m11, m12] = y.0;
    let [m20, m21, m22] = up.0;
    let t = m00 + m11 + m22;
    let q = if t > 0.0 {
        let s = (t + 1.0).sqrt() * 2.0;
        Q { w: s / 4.0, x: (m21 - m12) / s, y: (m02 - m20) / s, z: (m10 - m01) / s }
    } else if m00 >= m11 && m00 >= m22 {
        let s = (1.0 + m00 - m11 - m22).sqrt() * 2.0;
        Q { w: (m21 - m12) / s, x: s / 4.0, y: (m01 + m10) / s, z: (m02 + m20) / s }
    } else if m11 >= m22 {
        let s = (1.0 + m11 - m00 - m22).sqrt() * 2.0;
        Q { w: (m02 - m20) / s, x: (m01 + m10) / s, y: s / 4.0, z: (m12 + m21) / s }
    } else {
        let s = (1.0 + m22 - m00 - m11).sqrt() * 2.0;
        Q { w: (m10 - m01) / s, x: (m02 + m20) / s, y: (m12 + m21) / s, z: s / 4.0 }
    };
    normalized(q)
}
fn ramp_down(x: f64, zero_at: f64) -> f64 {
    clamp(1.0 - x / zero_at, 0.0, 1.0)
}
fn wrap_pi(mut a: f64) -> f64 {
    use std::f64::consts::PI;
    while a > PI {
        a -= 2.0 * PI;
    }
    while a < -PI {
        a += 2.0 * PI;
    }
    a
}
/// The middle value of each axis, as `std::nth_element` at `n / 2` picks it.
fn median<const N: usize>(blocks: &[V; N], n: usize) -> V {
    let mut median = V::default();
    let mut axis = [0.0f64; N];
    for i in 0..3 {
        for k in 0..n {
            axis[k] = blocks[k].0[i];
        }
        let (_, m, _) = axis[..n].select_nth_unstable_by(n / 2, |a, b| a.partial_cmp(b).expect("finite"));
        median.0[i] = *m;
    }
    median
}

const G: f64 = 9.80665;
/// Stillness is judged over blocks of this length.
const BLOCK_S: f64 = 0.1;
/// Fewer than this in start-up: no bias guess is better than one.
const MIN_QUIET_BLOCKS: usize = 5;
/// Half a second before a pause counts as still.
const STILL_RUN_BLOCKS: i32 = 5;
const MAX_START_BLOCKS: usize = 64;
/// 60 s: the longest window the median can hold, without allocating.
const MAX_QUIET_BLOCKS: usize = 600;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(u8)]
pub enum FusionQuality {
    /// `PoseOut::calibrated` false. Tilt is already right, yaw drifts at the raw bias.
    #[default]
    Uncalibrated,
    /// Usable. Never had a proper still window (worn or moving at start); bias is a best guess.
    Coarse,
    /// Bias measured while still.
    Good,
    /// Calibrated, but samples were lost or time jumped: gravity is being re-acquired.
    Recovering,
}

/// Field for field `luma::FusionConfig`; the defaults are the C++ ones.
#[derive(Clone, Debug, PartialEq)]
pub struct FusionConfig {
    // Start-up.
    /// Unbroken stillness needed for Good.
    pub cal_still_s: f32,
    /// No stillness by now: go Coarse. `calibrated` turns true.
    pub coarse_timeout_s: f32,
    /// Bias saved from an earlier session ([`FusionStatus::gyro_bias_rad_s`]). Bias barely moves
    /// between sessions, so this gives usable tracking from the first sample: Coarse at once, Good
    /// as usual.
    pub warm_start_bias: Option<[f32; 3]>,
    // Stillness, per 100 ms block. "still" = on a desk. "quiet" = worn, head resting.
    /// rad/s
    pub still_gyro_std: f32,
    /// m/s2
    pub still_accel_std: f32,
    /// rad/s a block's mean may move from the last one's, above noise.
    pub still_mean_step: f32,
    /// rad/s
    pub quiet_gyro_std: f32,
    /// m/s2
    pub quiet_accel_std: f32,
    /// rad/s; a "bias" above this (10 deg/s) is a slow real turn.
    pub cal_max_rate: f32,
    // Gravity correction gains (rad/s per rad of tilt error).
    pub kp_moving: f32,
    pub kp_still: f32,
    /// Before calibration and while Recovering.
    pub kp_recover: f32,
    /// Bias integrator from gravity. Sees only the two tilt axes.
    pub ki: f32,
    /// Fraction of g at which the correction has faded to nothing.
    pub accel_norm_gate: f32,
    /// rad/s at which the correction has faded to nothing.
    pub accel_rate_gate: f32,
    // Bias tracking in place of a temperature model.
    pub still_tau_s: f32,
    /// rad/s; a change above this needs `still_relearn_s` of stillness.
    pub still_track_limit: f32,
    pub still_relearn_s: f32,
    /// rad/s; above this a smooth "still" reading is a real slow turn.
    pub still_relearn_limit: f32,
    /// rad/s; a block turning faster than this is not a rest.
    pub quiet_max_rate: f32,
    /// Rest time averaged per bias update.
    pub quiet_window_s: f32,
    pub quiet_tau_s: f32,
    /// rad/s (1 deg/s)
    pub quiet_track_limit: f32,
    pub quiet_window_coarse_s: f32,
    pub quiet_tau_coarse_s: f32,
    pub quiet_limit_coarse: f32,
    /// Quiet time that promotes Coarse to Good.
    pub coarse_to_good_s: f32,
    /// rad/s
    pub max_bias: f32,
    // Timing.
    /// A longer gap is not integrated: that motion is lost.
    pub max_gap_s: f32,
    /// Least time in Recovering.
    pub recover_s: f32,
    /// Tilt error that ends Recovering.
    pub recover_tilt_rad: f32,
    /// Moving heads may never show that: give up this long after `recover_s`.
    pub recover_max_s: f32,
    /// Magnetometer: off by default. Hard-iron is uncorrected and a monitor or desk bends the field.
    pub use_mag: bool,
    pub kp_mag: f32,
    /// Fraction of the reference field strength allowed off.
    pub mag_gate: f32,
}

impl Default for FusionConfig {
    fn default() -> Self {
        Self {
            cal_still_s: 1.5,
            coarse_timeout_s: 2.5,
            warm_start_bias: None,
            still_gyro_std: 0.015,
            still_accel_std: 0.15,
            still_mean_step: 0.002,
            quiet_gyro_std: 0.05,
            quiet_accel_std: 0.4,
            cal_max_rate: 0.175,
            kp_moving: 0.1,
            kp_still: 0.3,
            kp_recover: 6.0,
            ki: 0.002,
            accel_norm_gate: 0.05,
            accel_rate_gate: 2.5,
            still_tau_s: 5.0,
            still_track_limit: 0.01,
            still_relearn_s: 20.0,
            still_relearn_limit: 0.03,
            quiet_max_rate: 0.05,
            quiet_window_s: 30.0,
            quiet_tau_s: 60.0,
            quiet_track_limit: 0.0175,
            quiet_window_coarse_s: 5.0,
            quiet_tau_coarse_s: 20.0,
            quiet_limit_coarse: 0.05,
            coarse_to_good_s: 40.0,
            max_bias: 0.35,
            max_gap_s: 0.25,
            recover_s: 0.5,
            recover_tilt_rad: 0.005,
            recover_max_s: 2.0,
            use_mag: false,
            kp_mag: 0.2,
            mag_gate: 0.25,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FusionStatus {
    pub quality: FusionQuality,
    pub still: bool,
    pub gyro_bias_rad_s: [f32; 3],
    /// Newest sample less bias, unfiltered: no added delay.
    pub omega_body_rad_s: [f32; 3],
    pub omega_world_rad_s: [f32; 3],
    /// World up seen from the glasses.
    pub up_body: [f32; 3],
    /// Stream time when `calibrated` turned true; -1 before.
    pub calibrated_after_s: f32,
    pub samples: u64,
    /// Non-finite or duplicate: not used.
    pub rejected: u64,
    /// Motion lost: not integrated.
    pub gaps: u64,
    /// Timestamp nonsense bridged by sequence number.
    pub time_jumps: u64,
}

struct State {
    c: FusionConfig,
    q: Q,
    bias: V,
    omega: V,
    last_gyro: V,
    last_t: u64,
    last_seq: u32,
    have_last: bool,
    period: f64,
    stream_s: f64,
    calibrated_after: f64,
    base: FusionQuality,
    recovering: bool,
    recover_left: f64,
    recover_error: V,
    g_ref: f64,
    // Stillness: the block being filled, and the block before it.
    block_s: f64,
    block_n: i32,
    g_sum: V,
    g_sq: V,
    a_sum: V,
    a_sq: V,
    prev_g_mean: V,
    have_prev_block: bool,
    /// Unbroken still blocks.
    run: i32,
    run_gyro: V,
    run_accel: V,
    quiet_n: usize,
    quiet_blocks: [V; MAX_QUIET_BLOCKS],
    quiet_total_s: f64,
    /// Worn rest time behind the current bias: its weight against the next window.
    quiet_learned_s: f64,
    quiet_disagreed: bool,
    /// Quiet block means seen in start-up: their median is the Coarse bias.
    start_blocks: [V; MAX_START_BLOCKS],
    start_n: usize,
    have_mag_ref: bool,
    mag_heading_ref: f64,
    mag_norm_ref: f64,
    out: PoseOut,
    samples: u64,
    rejected: u64,
    gaps: u64,
    time_jumps: u64,
}

impl State {
    fn new(c: FusionConfig) -> Self {
        let mut s = State {
            c,
            q: Q::default(),
            bias: V::default(),
            omega: V::default(),
            last_gyro: V::default(),
            last_t: 0,
            last_seq: 0,
            have_last: false,
            period: 1e-3,
            stream_s: 0.0,
            calibrated_after: -1.0,
            base: FusionQuality::Uncalibrated,
            recovering: false,
            recover_left: 0.0,
            recover_error: V::default(),
            g_ref: G,
            block_s: 0.0,
            block_n: 0,
            g_sum: V::default(),
            g_sq: V::default(),
            a_sum: V::default(),
            a_sq: V::default(),
            prev_g_mean: V::default(),
            have_prev_block: false,
            run: 0,
            run_gyro: V::default(),
            run_accel: V::default(),
            quiet_n: 0,
            quiet_blocks: [V::default(); MAX_QUIET_BLOCKS],
            quiet_total_s: 0.0,
            quiet_learned_s: 0.0,
            quiet_disagreed: false,
            start_blocks: [V::default(); MAX_START_BLOCKS],
            start_n: 0,
            have_mag_ref: false,
            mag_heading_ref: 0.0,
            mag_norm_ref: 0.0,
            out: PoseOut::default(),
            samples: 0,
            rejected: 0,
            gaps: 0,
            time_jumps: 0,
        };
        if let Some(b) = s.c.warm_start_bias {
            s.bias = s.clamp_bias(to_v(b));
            s.base = FusionQuality::Coarse;
        }
        s
    }
    fn clamp_bias(&self, b: V) -> V {
        let n = norm(b);
        let max_bias = f64::from(self.c.max_bias);
        if n > max_bias { b * (max_bias / n) } else { b }
    }
    fn new_block(&mut self) {
        self.block_s = 0.0;
        self.block_n = 0;
        self.g_sum = V::default();
        self.g_sq = V::default();
        self.a_sum = V::default();
        self.a_sq = V::default();
    }
    fn restart_stillness(&mut self) {
        self.new_block();
        self.have_prev_block = false;
        self.run = 0;
        self.run_gyro = V::default();
        self.run_accel = V::default();
        self.quiet_n = 0;
    }
    fn mark_calibrated(&mut self, to: FusionQuality) {
        self.base = to;
        if self.calibrated_after < 0.0 {
            self.calibrated_after = self.stream_s;
        }
    }
    fn start_recovering(&mut self) {
        self.recovering = true;
        self.recover_left = f64::from(self.c.recover_s);
        self.recover_error = V([1.0, 0.0, 0.0]);
        self.restart_stillness();
    }
    fn still(&self) -> bool {
        self.run >= STILL_RUN_BLOCKS
    }

    /// How long since the last sample, in seconds; 0 = do not integrate. Sequence numbers settle
    /// what a bad timestamp cannot: a jump with no samples missing was only the clock.
    fn step_time(&mut self, s: &ImuSample) -> f64 {
        let seq_step = s.seq.wrapping_sub(self.last_seq);
        let dt = s.t_ns.wrapping_sub(self.last_t) as i64 as f64 * 1e-9;
        let max_gap = f64::from(self.c.max_gap_s);
        if dt > 0.0 && dt <= max_gap {
            if seq_step == 1 && dt > self.period * 0.2 && dt < self.period * 5.0 {
                self.period += (dt - self.period) * 0.02;
            }
            return dt;
        }
        let by_count = f64::from(seq_step) * self.period;
        if seq_step >= 1 && by_count <= max_gap {
            self.time_jumps += 1;
            self.start_recovering();
            return by_count;
        }
        self.gaps += 1;
        self.start_recovering();
        0.0
    }

    fn integrate(&mut self, gyro: V, dt: f64) {
        let a = (self.last_gyro - self.bias) * dt;
        let b = (gyro - self.bias) * dt;
        self.q = normalized(mul(self.q, exp_map((a + b) * 0.5 + cross(a, b) * (1.0 / 12.0))));
    }

    fn correct_gravity(&mut self, accel: V, dt: f64) {
        let n = norm(accel);
        if n < 0.5 * G || n > 1.5 * G {
            return;
        }
        let fast = self.base == FusionQuality::Uncalibrated || self.recovering;
        let gate = ramp_down((n / self.g_ref - 1.0).abs(), if fast { 0.5 } else { f64::from(self.c.accel_norm_gate) })
            * if fast { 1.0 } else { ramp_down(norm(self.omega), f64::from(self.c.accel_rate_gate)) };
        if gate <= 0.0 {
            return;
        }
        let measured = accel * (1.0 / n);
        let estimated = up_in_body(self.q);
        let mut e = cross(measured, estimated);
        // Near a half turn the cross product vanishes though the error is at its largest.
        let sine = norm(e);
        if dot(measured, estimated) < 0.0 {
            e = if sine > 1e-6 {
                e * (1.0 / sine)
            } else {
                cross(measured, if measured.0[0].abs() < 0.9 { V([1.0, 0.0, 0.0]) } else { V([0.0, 1.0, 0.0]) })
            };
            if sine <= 1e-6 {
                e = e * (1.0 / norm(e));
            }
        }
        let kp = f64::from(if fast {
            self.c.kp_recover
        } else if self.still() {
            self.c.kp_still
        } else {
            self.c.kp_moving
        });
        self.q = normalized(mul(self.q, exp_map(e * (kp * gate * dt))));
        if !fast {
            self.bias = self.clamp_bias(self.bias - e * (f64::from(self.c.ki) * gate * dt));
        }
        // One sample's accelerometer noise can dip under the limit: judge the error smoothed.
        self.recover_error = self.recover_error + (e - self.recover_error) * min(1.0, dt / 0.05);
        if self.recovering
            && self.recover_left <= 0.0
            && (norm(self.recover_error) < f64::from(self.c.recover_tilt_rad)
                || self.recover_left <= f64::from(-self.c.recover_max_s))
        {
            self.recovering = false;
        }
    }

    fn correct_heading(&mut self, mag: [f32; 3], dt: f64) {
        let m = to_world(self.q, to_v(mag));
        let horizontal = m.0[0].hypot(m.0[1]);
        let n = norm(m);
        if horizontal < 0.2 * n || n <= 0.0 {
            return;
        }
        if !self.have_mag_ref {
            if self.base == FusionQuality::Uncalibrated {
                return;
            }
            self.have_mag_ref = true;
            self.mag_heading_ref = m.0[1].atan2(m.0[0]);
            self.mag_norm_ref = n;
            return;
        }
        if (n / self.mag_norm_ref - 1.0).abs() > f64::from(self.c.mag_gate) {
            return;
        }
        let error = wrap_pi(self.mag_heading_ref - m.0[1].atan2(m.0[0]));
        let e = up_in_body(self.q) * error; // World-up axis, body frame.
        self.q = normalized(mul(self.q, exp_map(e * (f64::from(self.c.kp_mag) * dt))));
        self.bias = self.clamp_bias(self.bias - e * (f64::from(self.c.ki) * dt));
    }

    fn finish_calibration(&mut self) {
        let run = f64::from(self.run);
        let g = self.run_gyro * (1.0 / run);
        let a = self.run_accel * (1.0 / run);
        self.bias = self.clamp_bias(g);
        self.g_ref = norm(a);
        // The first calibration sets the heading origin: world x is where the glasses face now.
        if self.base == FusionQuality::Uncalibrated {
            self.q = attitude_from_up(a * (1.0 / self.g_ref));
        }
        self.mark_calibrated(FusionQuality::Good);
    }
    fn go_coarse(&mut self) {
        if self.start_n >= MIN_QUIET_BLOCKS {
            self.bias = self.clamp_bias(median(&self.start_blocks, self.start_n));
        }
        self.mark_calibrated(FusionQuality::Coarse);
    }

    fn close_block(&mut self) {
        let n = f64::from(self.block_n);
        let g = self.g_sum * (1.0 / n);
        let a = self.a_sum * (1.0 / n);
        let std3 = |sum: V, sq: V| {
            let mut v = 0.0;
            for i in 0..3 {
                v += max(0.0, sq.0[i] / n - sum.0[i] * sum.0[i] / (n * n));
            }
            v.sqrt()
        };
        let gyro_std = std3(self.g_sum, self.g_sq);
        let accel_std = std3(self.a_sum, self.a_sq);
        let an = norm(a);
        let c = &self.c;
        let level_force = (an / G - 1.0).abs() < 0.15;
        // A desk's block means differ only by noise. A resting head's wander, however smooth: this
        // is what tells them apart, so the step is judged against what noise alone would give.
        let noise_step = 2.0 * 2.0f64.sqrt() * gyro_std / n.sqrt();
        let calm = !self.have_prev_block || norm(g - self.prev_g_mean) < f64::from(c.still_mean_step) + noise_step;
        let slow = norm(g) < f64::from(c.cal_max_rate);
        let is_still = gyro_std < f64::from(c.still_gyro_std)
            && accel_std < f64::from(c.still_accel_std)
            && level_force
            && calm
            && slow;
        let is_quiet =
            gyro_std < f64::from(c.quiet_gyro_std) && accel_std < f64::from(c.quiet_accel_std) && level_force && slow;
        let cal_blocks = (f64::from(c.cal_still_s) / BLOCK_S).round().max(1.0) as i32;
        let relearn_blocks = (f64::from(c.still_relearn_s) / BLOCK_S).round() as i32;
        self.prev_g_mean = g;
        self.have_prev_block = true;
        self.new_block();

        if is_still {
            self.run += 1;
            self.run_gyro = self.run_gyro + g;
            self.run_accel = self.run_accel + a;
        } else {
            self.run = 0;
            self.run_gyro = V::default();
            self.run_accel = V::default();
        }
        if self.base != FusionQuality::Good {
            if self.run >= cal_blocks {
                self.finish_calibration();
                return;
            }
            if self.base == FusionQuality::Uncalibrated {
                if is_quiet && self.start_n < MAX_START_BLOCKS {
                    self.start_blocks[self.start_n] = g;
                    self.start_n += 1;
                }
                if self.stream_s >= f64::from(self.c.coarse_timeout_s) {
                    self.go_coarse();
                }
                return;
            }
        } else if self.still() {
            let d = g - self.bias;
            if norm(d) < f64::from(self.c.still_track_limit) {
                self.bias = self.clamp_bias(self.bias + d * (BLOCK_S / f64::from(self.c.still_tau_s)));
            } else if self.run >= relearn_blocks && norm(d) < f64::from(self.c.still_relearn_limit) {
                self.bias = self.clamp_bias(self.run_gyro * (1.0 / f64::from(self.run)));
            }
            self.quiet_n = 0;
            return;
        }
        // Worn. Turns stay out (is_quiet), the rests between them count however they are spaced:
        // a resting head does not keep turning one way, so the window's middle value is the bias.
        // The median, not the mean: on a real session a slow turn that passes as quiet moved a 60 s
        // mean by up to 0.5 deg/s and the median by 0.05 (spike/driver/docs/capture-findings.md).
        // The turn-rate limit leans on the bias, so a Coarse one must not use it: it would keep only
        // the blocks that agree with the wrong bias and never learn the right one.
        let coarse = self.base == FusionQuality::Coarse;
        if !is_quiet || (!coarse && norm(g - self.bias) >= f64::from(self.c.quiet_max_rate)) {
            return;
        }
        if self.quiet_n < MAX_QUIET_BLOCKS {
            self.quiet_blocks[self.quiet_n] = g;
            self.quiet_n += 1;
        }
        let window_s = self.quiet_n as f64 * BLOCK_S;
        let wanted = min(
            f64::from(if coarse { self.c.quiet_window_coarse_s } else { self.c.quiet_window_s }),
            MAX_QUIET_BLOCKS as f64 * BLOCK_S,
        );
        if window_s < wanted - 1e-6 {
            return;
        }
        let d = median(&self.quiet_blocks, self.quiet_n) - self.bias;
        self.quiet_n = 0;
        let agrees = norm(d) < f64::from(self.c.quiet_track_limit);
        // Each window weighs what it measured against what the bias already rests on, so the first
        // ones correct a poor start in seconds. tau is only the floor, which lets it follow a warming
        // sensor.
        let tau = f64::from(if coarse { self.c.quiet_tau_coarse_s } else { self.c.quiet_tau_s });
        let gain = window_s / min(tau, self.quiet_learned_s + window_s);
        if coarse {
            if norm(d) >= f64::from(self.c.quiet_limit_coarse) {
                return;
            }
            self.bias = self.clamp_bias(self.bias + d * gain);
            self.quiet_learned_s += window_s;
            self.quiet_total_s = if agrees { self.quiet_total_s + window_s } else { 0.0 };
            if self.quiet_total_s >= f64::from(self.c.coarse_to_good_s) {
                self.mark_calibrated(FusionQuality::Good);
            }
            return;
        }
        if agrees {
            self.quiet_disagreed = false;
            self.bias = self.clamp_bias(self.bias + d * gain);
            self.quiet_learned_s += window_s;
        } else if self.quiet_disagreed {
            // A minute of rests saying the same: no head does that, the bias is wrong. Own up.
            self.quiet_disagreed = false;
            self.quiet_total_s = 0.0;
            self.quiet_learned_s = 0.0;
            self.base = FusionQuality::Coarse;
        } else {
            self.quiet_disagreed = true;
        }
    }

    fn observe(&mut self, gyro: V, accel: V, dt: f64) {
        self.block_s += dt;
        self.block_n += 1;
        self.g_sum = self.g_sum + gyro;
        self.a_sum = self.a_sum + accel;
        for i in 0..3 {
            self.g_sq.0[i] += gyro.0[i] * gyro.0[i];
            self.a_sq.0[i] += accel.0[i] * accel.0[i];
        }
        if self.block_s >= BLOCK_S - 1e-9 {
            self.close_block();
        }
    }

    fn update(&mut self, s: &ImuSample) -> PoseOut {
        self.samples += 1;
        self.out.seq = s.seq;
        if !finite(&s.gyro_rad_s) || !finite(&s.accel_m_s2) {
            self.rejected += 1;
            return self.out;
        }
        let gyro = to_v(s.gyro_rad_s);
        let accel = to_v(s.accel_m_s2);
        if self.have_last && s.t_ns == self.last_t && s.seq == self.last_seq {
            self.rejected += 1;
            return self.out;
        }
        let mut dt = 0.0;
        if !self.have_last {
            self.last_gyro = gyro;
            let n = norm(accel);
            if n > 0.5 * G && n < 1.5 * G {
                self.q = attitude_from_up(accel * (1.0 / n));
            }
            if self.base == FusionQuality::Coarse {
                self.calibrated_after = 0.0;
            }
        } else {
            dt = self.step_time(s);
        }
        self.have_last = true;
        self.last_t = s.t_ns;
        self.last_seq = s.seq;
        self.omega = gyro - self.bias;
        if dt > 0.0 {
            self.stream_s += dt;
            if self.recovering {
                self.recover_left -= dt;
            }
            self.integrate(gyro, dt);
            self.correct_gravity(accel, dt);
            if self.c.use_mag {
                if let Some(mag) = s.mag.filter(finite) {
                    self.correct_heading(mag, dt);
                }
            }
            self.observe(gyro, accel, dt);
        } else if self.recovering {
            self.correct_gravity(accel, self.period);
        }
        self.last_gyro = gyro;
        self.out.q = to_quatf(self.q);
        self.out.t_ns = s.t_ns;
        self.out.calibrated = self.base != FusionQuality::Uncalibrated;
        self.out
    }

    fn status(&self) -> FusionStatus {
        FusionStatus {
            quality: if self.recovering && self.base != FusionQuality::Uncalibrated {
                FusionQuality::Recovering
            } else {
                self.base
            },
            still: self.still(),
            gyro_bias_rad_s: to_f(self.bias),
            omega_body_rad_s: to_f(self.omega),
            omega_world_rad_s: to_f(to_world(self.q, self.omega)),
            up_body: to_f(up_in_body(self.q)),
            calibrated_after_s: self.calibrated_after as f32,
            samples: self.samples,
            rejected: self.rejected,
            gaps: self.gaps,
            time_jumps: self.time_jumps,
        }
    }
}

/// The filter. Boxed state, as the C++ keeps it behind a `unique_ptr`: about 16 KiB of block
/// history that should not sit on the caller's stack.
pub struct Fusion {
    s: Box<State>,
}

impl Default for Fusion {
    fn default() -> Self {
        Self::new(FusionConfig::default())
    }
}

impl Fusion {
    pub fn new(config: FusionConfig) -> Self {
        Self { s: Box::new(State::new(config)) }
    }
    pub fn update(&mut self, sample: &ImuSample) -> PoseOut {
        self.s.update(sample)
    }
    pub fn status(&self) -> FusionStatus {
        self.s.status()
    }
    /// Orientation `horizon_s` after the newest sample at its angular velocity: first-order
    /// prediction for the renderer. `horizon_s = 0` is [`PoseOut::q`].
    pub fn predict(&self, horizon_s: f32) -> Quatf {
        if horizon_s == 0.0 {
            return self.s.out.q;
        }
        let p = normalized(mul(self.s.q, exp_map(self.s.omega * f64::from(horizon_s))));
        to_quatf(p)
    }
    /// Back to start-up. A USB reconnect does not need it: see Recovering.
    pub fn reset(&mut self) {
        *self.s = State::new(self.s.c.clone());
    }
}
