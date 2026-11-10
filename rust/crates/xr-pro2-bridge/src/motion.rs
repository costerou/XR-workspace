//! Head velocity from the Pro 2's SDK pose, for the feed's motion channel (`pose_prediction`).
//!
//! The SDK in pose mode gives orientation only, no gyro. This is the velocity half of the
//! renderer's `xr::MotionSmoother` (`spike/src/motion_smooth.cpp`, rationale in
//! `docs/pro2/smoothness.md`), moved into the bridge, where every sample arrives: the renderer
//! sees only the newest one at each frame, so at 240 Hz it would miss every other one.
//!
//! - velocity: the rotation from the newest sample back to the newest one at least `WINDOW_MS`
//!   older (or the oldest kept), over their real device-time gap. At 60 Hz on the measured
//!   15, 15, 20 ms cadence that is the second-oldest of the last three, exactly the renderer's
//!   choice; at 240 Hz the same time span, so the same noise, and a sample 4 ms fresher.
//! - brake: the speed over the newest `BRAKE_MS` along that velocity, as a share of it, raised to
//!   `BRAKE_POWER`, scales it: a head that is stopping or turning back is not extrapolated past.
//!   At 60 Hz that is the newest interval, as in the renderer.
//! - still gate: below `STILL_LO_DPS` no velocity, all of it from `STILL_HI_DPS`. The renderer's
//!   own estimate differences the stabilized samples, which hold still at rest; this one sees
//!   the raw ones, so it gates itself.
//! - a gap over `MAX_GAP_MS` inside the window: no velocity. A jump over `SNAP_DEG` between
//!   samples, or device time going back: the history starts over.
//!
//! Body frame, radians per second: `q * exp(omega * t)` is where the head is going, which is what
//! `luma::extrapolate` and the renderer's smoother do with it. Allocation free.

const RING: usize = 64; // 25 ms at up to 2.5 kHz
const WINDOW_MS: f64 = 25.0;
const BRAKE_MS: f64 = 12.0;
const MAX_GAP_MS: f64 = 60.0;
const SNAP_DEG: f32 = 30.0;
const BRAKE_POWER: f32 = 3.0;
/// 4 deg/s in rad/ms: speeds around this are noise, not a stop (motion_smooth.hpp).
const BRAKE_FLOOR: f32 = 4.0 * std::f32::consts::PI / 180_000.0;
/// No velocity below this speed, all of it from `STILL_HI_DPS` up, smoothstep between: the
/// rest gate the renderer's own estimate gets from the stabilized samples it differences.
/// Chosen on smooth_acceptance's synthetic heads: rest jitter at the own path's, slow turns kept.
const STILL_LO_DPS: f32 = 1.5;
const STILL_HI_DPS: f32 = 4.0;

type Vec3 = [f32; 3];
/// w, x, y, z.
type Quat = [f32; 4];

fn mul(a: Quat, b: Quat) -> Quat {
    [
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ]
}

fn conj(q: Quat) -> Quat {
    [q[0], -q[1], -q[2], -q[3]]
}

/// Rotation vector, radians: `log_of` in motion_smooth.cpp.
fn log(mut q: Quat) -> Vec3 {
    if q[0] < 0.0 {
        q = q.map(|c| -c);
    }
    let s = (q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
    if s < 1e-9 {
        return [2.0 * q[1], 2.0 * q[2], 2.0 * q[3]];
    }
    let k = 2.0 * s.atan2(q[0]) / s;
    [q[1] * k, q[2] * k, q[3] * k]
}

fn dot(a: Vec3, b: Vec3) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// The rotation from `a` to `b` in `a`'s body frame, per ms.
fn rate(a: (Quat, f64), b: (Quat, f64)) -> Vec3 {
    let dt = (b.1 - a.1) as f32;
    log(mul(conj(a.0), b.0)).map(|c| c / dt)
}

#[derive(Debug)]
pub struct MotionEstimator {
    q: [Quat; RING],
    t_ms: [f64; RING],
    newest: usize,
    count: usize,
    resets: u64,
}

impl Default for MotionEstimator {
    fn default() -> Self {
        Self {
            q: [[1.0, 0.0, 0.0, 0.0]; RING],
            t_ms: [0.0; RING],
            newest: 0,
            count: 0,
            resets: 0,
        }
    }
}

impl MotionEstimator {
    /// The `back`th sample before the newest: 0 is the newest.
    fn at(&self, back: usize) -> (Quat, f64) {
        let i = (self.newest + RING - back) % RING;
        (self.q[i], self.t_ms[i])
    }

    /// Learns one sample (unit `q`, w x y z; `device_ns` on the SDK's clock) and returns the
    /// head's body-frame angular velocity in rad/s, already braked. Zero until there are two.
    pub fn update(&mut self, q: Quat, device_ns: u64) -> Vec3 {
        let t = device_ns as f64 / 1e6;
        if self.count > 0 {
            let (last_q, last_t) = self.at(0);
            if t == last_t {
                return self.velocity();
            }
            let jump = log(mul(conj(last_q), q));
            if t < last_t || dot(jump, jump).sqrt().to_degrees() > SNAP_DEG {
                self.count = 0;
                self.resets += 1;
            }
        }
        self.newest = (self.newest + 1) % RING;
        self.q[self.newest] = q;
        self.t_ms[self.newest] = t;
        self.count = (self.count + 1).min(RING);
        self.velocity()
    }

    /// Histories dropped on a jump or a clock going back.
    #[cfg(test)]
    fn resets(&self) -> u64 {
        self.resets
    }

    fn velocity(&self) -> Vec3 {
        if self.count < 2 {
            return [0.0; 3];
        }
        let newest = self.at(0);
        let mut oldest = 1;
        while oldest + 1 < self.count && newest.1 - self.at(oldest).1 < WINDOW_MS {
            oldest += 1;
        }
        for back in 0..oldest {
            if self.at(back).1 - self.at(back + 1).1 > MAX_GAP_MS {
                return [0.0; 3];
            }
        }
        let v = rate(self.at(oldest), newest);
        let mut brake = 1;
        while brake < oldest && newest.1 - self.at(brake).1 < BRAKE_MS {
            brake += 1;
        }
        let mut keep = 1.0;
        if brake < oldest {
            let recent = rate(self.at(brake), newest);
            let e2 = BRAKE_FLOOR * BRAKE_FLOOR;
            let share = (dot(recent, v) + e2) / (dot(v, v) + e2);
            keep = share.clamp(0.0, 1.0).powf(BRAKE_POWER);
        }
        // A still head is not extrapolated: the SDK's noise, differenced, is a few tenths of a degree
        // per second at rest, and the renderer's continuity filter would show it as a shimmer.
        let speed = (dot(v, v).sqrt() * keep * 1000.0).to_degrees();
        let x = ((speed - STILL_LO_DPS) / (STILL_HI_DPS - STILL_LO_DPS)).clamp(0.0, 1.0);
        let gain = x * x * (3.0 - 2.0 * x);
        v.map(|c| c * keep * gain * 1000.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn yaw(deg: f64) -> Quat {
        let h = deg.to_radians() / 2.0;
        [h.cos() as f32, 0.0, 0.0, h.sin() as f32]
    }

    /// A head turning about z, fed sample by sample.
    #[derive(Default)]
    struct Head {
        e: MotionEstimator,
        t: f64,
        angle: f64,
    }

    impl Head {
        /// `n` samples on the given gaps at `dps`; the last velocity.
        fn turn(&mut self, gaps: &[f64], n: usize, dps: f64) -> Vec3 {
            let mut v = [0.0; 3];
            for i in 0..n {
                let gap = gaps[i % gaps.len()];
                self.angle += dps * gap / 1000.0;
                self.t += gap;
                v = self.e.update(yaw(self.angle), (self.t * 1e6) as u64);
            }
            v
        }
    }

    #[test]
    fn steady_turn_reads_its_speed_at_any_rate_and_cadence() {
        for gaps in [
            &[15.0, 15.0, 20.0][..],
            &[4.1667],
            &[8.3333],
            &[3.0, 5.0, 4.5],
        ] {
            let v = Head::default().turn(gaps, 40, 30.0);
            assert!((v[2].to_degrees() - 30.0).abs() < 0.01, "{gaps:?}: {v:?}");
            assert!(v[0].abs() < 1e-6 && v[1].abs() < 1e-6);
        }
    }

    #[test]
    fn first_sample_has_no_velocity_and_two_do() {
        let mut e = MotionEstimator::default();
        assert_eq!(e.update(yaw(0.0), 0), [0.0; 3]);
        let v = e.update(yaw(0.5), 10_000_000);
        assert!((v[2].to_degrees() - 50.0).abs() < 0.01, "{v:?}");
        assert_eq!(
            e.update(yaw(0.5), 10_000_000),
            v,
            "a repeat changes nothing"
        );
    }

    /// At 60 Hz the window is the second-oldest of the last three, the brake the newest
    /// interval: the renderer's MotionSmoother exactly (above the still gate).
    #[test]
    fn at_60_hz_it_is_the_renderers_estimator() {
        let mut e = MotionEstimator::default();
        let samples = [(0.0, 0.0), (15.0, 0.6), (30.0, 1.0), (50.0, 1.4)];
        let mut v = [0.0; 3];
        for (t, a) in samples {
            v = e.update(yaw(a), (t * 1e6) as u64);
        }
        let window = (1.4f32 - 0.6) / 35.0; // deg per ms over 30..50 and 15..30
        let newest = (1.4f32 - 1.0) / 20.0;
        let floor = BRAKE_FLOOR.to_degrees();
        let keep = ((newest * window + floor * floor) / (window * window + floor * floor))
            .clamp(0.0, 1.0)
            .powi(3);
        let want = (window * keep * 1000.0).to_radians();
        assert!(
            want.to_degrees() > STILL_HI_DPS && keep < 0.95,
            "braked, not gated"
        );
        assert!(
            (v[2] - want).abs() < 1e-4 * want.abs().max(1.0),
            "{} vs {want}",
            v[2]
        );
    }

    #[test]
    fn a_stopping_head_is_braked_and_a_reversing_one_stopped() {
        let mut head = Head::default();
        let steady = head.turn(&[4.1667], 30, 40.0);
        let slowing = head.turn(&[4.1667], 3, 10.0);
        assert!(
            slowing[2] > 0.0 && slowing[2] < 0.5 * steady[2],
            "{slowing:?} {steady:?}"
        );
        let mut head = Head::default();
        head.turn(&[4.1667], 30, 40.0);
        let back = head.turn(&[4.1667], 3, -40.0);
        assert_eq!(back[2], 0.0, "turning back: nothing to extrapolate");
        assert_eq!(Head::default().turn(&[4.1667], 30, 0.0), [0.0; 3]);
    }

    #[test]
    fn a_still_head_is_gated_and_a_slow_turn_is_not() {
        let speed = |dps| Head::default().turn(&[4.1667], 30, dps)[2].to_degrees();
        assert_eq!(speed(1.0), 0.0);
        let partial = speed(2.75);
        assert!(partial > 0.0 && partial < 2.75, "{partial}");
        assert!((speed(5.0) - 5.0).abs() < 0.01);
    }

    #[test]
    fn long_gap_jump_and_clock_back_drop_the_velocity() {
        let mut head = Head::default();
        head.turn(&[4.1667], 20, 30.0);
        let (e, t) = (&mut head.e, head.t);
        // 70 ms with nothing: the newest pair spans the gap.
        assert_eq!(e.update(yaw(2.0), ((t + 70.0) * 1e6) as u64), [0.0; 3]);
        assert_eq!(e.resets(), 0);
        // 40 degrees in 4 ms is no head.
        assert_eq!(e.update(yaw(42.0), ((t + 74.0) * 1e6) as u64), [0.0; 3]);
        assert_eq!(e.resets(), 1);
        assert_eq!(e.update(yaw(42.0), ((t + 10.0) * 1e6) as u64), [0.0; 3]);
        assert_eq!(e.resets(), 2);
    }
}
