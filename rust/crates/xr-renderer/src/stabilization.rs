//! Port of spike/include/xr/stabilization.hpp (`xr::Stabilizer`).
//!
//! One shared, causal SO(3) low-pass. No Euler angles, integration, or extrapolation. Motion opens the cutoff
//! immediately; above 3 deg/s a 0.045 degree residual cap bounds extra lag to 1.5 ms at 30 deg/s even on a partially
//! sampled onset. At >=20 deg/s the pose and existing prediction pass through exactly. Device time, not frame time.
//!
//! The maths is the C++'s, in its order and precision (`f64` where it uses `double`), with `std::min` / `std::max` /
//! `std::clamp` and libstdc++'s three-argument `std::hypot` mirrored, so the output matches it bit for bit.

use crate::pose::{quat_conj, quat_mul, quat_normalized, Quat};

const PI: f64 = 3.141592653589793;

/// `std::max(a, b)`: `a` unless `a < b`.
fn dmax(a: f64, b: f64) -> f64 {
    if a < b {
        b
    } else {
        a
    }
}

/// `std::min(a, b)`: `a` unless `b < a`.
fn dmin(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `std::clamp(v, lo, hi)`.
fn dclamp(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

/// libstdc++'s `std::hypot(x, y, z)` (`__hypot3`): scaled by the largest, not libm's.
fn hypot3(x: f64, y: f64, z: f64) -> f64 {
    let (x, y, z) = (x.abs(), y.abs(), z.abs());
    let a = if x < y {
        if y < z {
            z
        } else {
            y
        }
    } else if x < z {
        z
    } else {
        x
    };
    if a != 0.0 {
        a * ((x / a) * (x / a) + (y / a) * (y / a) + (z / a) * (z / a)).sqrt()
    } else {
        0.0
    }
}

/// `xr::Stabilizer`. Fields are the C++ members, in its order (continuity.rs writes them in its byte layout).
#[derive(Clone, Debug, PartialEq)]
pub struct Stabilizer {
    pub(crate) strength: i32,
    pub(crate) valid: bool,
    pub(crate) ms: f64,
    pub(crate) speed: f64,
    pub(crate) prediction_gain: f32,
    pub(crate) previous: Quat,
    pub(crate) output: Quat,
    pub(crate) state: [f64; 4],
}

impl Default for Stabilizer {
    fn default() -> Self {
        Self {
            strength: 25,
            valid: false,
            ms: 0.0,
            speed: 0.0,
            prediction_gain: 1.0,
            previous: Quat::default(),
            output: Quat::default(),
            state: [0.0, 0.0, 0.0, 1.0],
        }
    }
}

impl Stabilizer {
    pub fn new() -> Self {
        Self::default()
    }

    /// 0 (off) to 100; a change resets the filter.
    pub fn strength(&mut self, value: i32) {
        let value = value.clamp(0, 100);
        if value != self.strength {
            self.strength = value;
            self.reset();
        }
    }

    pub fn reset(&mut self) {
        self.valid = false;
        self.prediction_gain = 1.0;
    }

    pub fn prediction_gain(&self) -> f32 {
        self.prediction_gain
    }

    pub fn update(&mut self, input: Quat, ms: f64) -> Quat {
        let Some(mut q) = quat_normalized(input).filter(|_| ms.is_finite()) else {
            return if self.valid { self.output } else { Quat::default() };
        };
        let p = self.previous;
        if self.valid && q.x * p.x + q.y * p.y + q.z * p.z + q.w * p.w < 0.0 {
            q = Quat { x: -q.x, y: -q.y, z: -q.z, w: -q.w };
        }
        let dt = (ms - self.ms) / 1000.;
        if !self.valid || dt < 0.0 || dt > 0.1 {
            self.valid = true;
            self.ms = ms;
            self.output = self.remember(q);
            self.previous = self.output;
            self.speed = 0.0;
            self.prediction_gain = 1.0;
            return if self.strength == 0 { input } else { self.output };
        }
        if dt == 0.0 {
            return if self.strength == 0 { input } else { self.output };
        }
        let speed = distance(self.previous, q) / dt;
        // Acceleration only opens a moving signal: noisy near-rest derivatives must not disable smoothing. There is no
        // delayed velocity low-pass.
        let acceleration = dmax(0., (speed - self.speed) / dt);
        let mut motion = dclamp((speed - 3.) / 17., 0., 1.);
        if speed > 3. {
            motion = dmax(motion, dclamp(acceleration / 4000., 0., 1.));
        }
        let amount = dmin(1., f64::from(self.strength) / 25.);
        self.prediction_gain = (1. - amount * (1. - motion)) as f32;
        let cutoff = 2. * 2f64.powf(-f64::from(self.strength) / 25.);
        let mut alpha = 1. - (-2. * PI * cutoff * dt).exp();
        alpha += (1. - alpha) * motion;
        alpha = 1. - amount * (1. - alpha);
        let residual = distance(self.output, q);
        // A still head may wander inside a small 0.2-degree envelope. Once movement begins, shrink that envelope
        // immediately to the lag bound.
        let cap = if speed > 3. { 0.045 } else { 0.2 };
        if residual > cap {
            alpha = dmax(alpha, 1. - cap / residual);
        }
        // Exact rest after a deliberate turn: no trailing filter memory. Re-interpolating an already identical float
        // quaternion can accumulate rounding error at tiny alpha. Keep exact rest exact, including after stops.
        if residual < 1e-6 || (speed < 1e-5 && self.speed > 0.05) {
            alpha = 1.0;
        }
        self.output = if self.strength == 0 || alpha >= 1.0 { self.remember(q) } else { self.blend(q, alpha) };
        self.previous = q;
        self.ms = ms;
        self.speed = speed;
        if self.strength == 0 {
            input
        } else {
            self.output
        }
    }

    fn remember(&mut self, q: Quat) -> Quat {
        self.state = [f64::from(q.x), f64::from(q.y), f64::from(q.z), f64::from(q.w)];
        let s = self.state;
        let n = s[0].hypot(s[1]).hypot(s[2].hypot(s[3]));
        for v in &mut self.state {
            *v /= n;
        }
        q
    }

    fn blend(&mut self, q: Quat, alpha: f64) -> Quat {
        // Keep the accumulator in double precision. A float accumulator at a 1 kHz input and very low cutoff can
        // otherwise acquire rounding bias.
        let mut target = [f64::from(q.x), f64::from(q.y), f64::from(q.z), f64::from(q.w)];
        let mut n = 0.0;
        for v in target {
            n += v * v;
        }
        n = f64::sqrt(n);
        let mut dot = 0.0;
        for i in 0..4 {
            target[i] /= n;
            dot += self.state[i] * target[i];
        }
        if dot < 0.0 {
            for v in &mut target {
                *v = -*v;
            }
            dot = -dot;
        }
        let (mut a, mut b) = (1. - alpha, alpha);
        if dot < 0.9995 {
            let theta = dclamp(dot, 0., 1.).acos();
            let sine = theta.sin();
            a = ((1. - alpha) * theta).sin() / sine;
            b = (alpha * theta).sin() / sine;
        }
        n = 0.0;
        for i in 0..4 {
            self.state[i] = a * self.state[i] + b * target[i];
            n += self.state[i] * self.state[i];
        }
        n = f64::sqrt(n);
        for v in &mut self.state {
            *v /= n;
        }
        let s = self.state;
        Quat { x: s[0] as f32, y: s[1] as f32, z: s[2] as f32, w: s[3] as f32 }
    }
}

/// Degrees between two orientations. atan2 retains micro-angle precision; acos(float dot) rounds tremor to zero.
fn distance(a: Quat, b: Quat) -> f64 {
    let d = quat_mul(quat_conj(a), b);
    2. * hypot3(f64::from(d.x), f64::from(d.y), f64::from(d.z)).atan2(f64::from(d.w).abs()) * 180. / PI
}
