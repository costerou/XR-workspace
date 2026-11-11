//! Software latency measurement and bounded head-pose prediction (from `spike/include/xr/predict.hpp` and
//! `spike/src/predict.cpp`).
//!
//! Latency here is SOFTWARE timing only: from the moment the renderer reads the pose to the compositor's
//! wp_presentation "presented" timestamp (same CLOCK_MONOTONIC). The glasses' own panel/scanout delay is not visible
//! to software; it is covered by the driver's per-device look-ahead constant, not measured. Never report any of this as
//! motion-to-photon latency.
//!
//! Prediction extrapolates the driver's newest orientation by the angular velocity between its two newest timestamped
//! samples (~16 ms apart), over a bounded horizon:
//!   horizon = pose age + expected read->present + driver constant, capped by the driver's cap,
//!   plus half the driver's scanline adjustment (we render one pose per frame; screen centre).
//! Expected read->present is measured; once the refresh grid is learned it is estimated for this frame's read phase
//! (`LatencyTracker::expected_ms_at`), so read timing jitter does not shake the view.
//!
//! The predictor from the Luma driver's `PredictionWire` (D3) is `motion_feed`'s; this file differences the feed's
//! samples, as the C++ does. All maths is in the C++ types and order, `std::min` / `std::max` / `std::clamp` mirrored.

#![allow(clippy::neg_cmp_op_on_partial_ord)] // `!(x > 0)` is true for NaN too, as in the C++

/// Declared with `PoseFeed::vsync`, as `xr::VsyncSnapshot` is in pose.hpp; still reachable here.
pub use crate::pose::VsyncSnapshot;
use crate::pose::{FeedSample, Quat, monotonic_ns, quat_conj, quat_mul, quat_normalized};

use std::f64::consts::PI;

// std::min / std::max / std::clamp, NaN behaviour included.
fn min_f32(a: f32, b: f32) -> f32 {
    if b < a { b } else { a }
}
fn max_f32(a: f32, b: f32) -> f32 {
    if a < b { b } else { a }
}
fn min_f64(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}
fn max_f64(a: f64, b: f64) -> f64 {
    if a < b { b } else { a }
}
fn clamp_f64(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}
/// `std::nextafter(x, INFINITY)` for a float (`f32::next_up` needs Rust 1.86).
fn next_up(x: f32) -> f32 {
    if x.is_nan() || x == f32::INFINITY {
        return x;
    }
    if x == 0.0 {
        return f32::from_bits(1);
    }
    let bits = x.to_bits();
    f32::from_bits(if x > 0.0 { bits + 1 } else { bits - 1 })
}

fn within(v: f32, lo: f32, hi: f32) -> bool {
    v.is_finite() && v >= lo && v <= hi
}
fn percentile(histogram: &[u64], count: u64, q: f64, bucket_ms: f64) -> f64 {
    if count == 0 {
        return 0.0;
    }
    let rank = (q * count as f64).ceil() as u64;
    let mut seen = 0u64;
    for (i, &n) in histogram.iter().enumerate() {
        seen += n;
        if seen >= rank {
            return (i as f64 + 0.5) * bucket_ms; // bucket centre
        }
    }
    histogram.len() as f64 * bucket_ms
}
fn add(histogram: &mut [u64], ms: f64, bucket_ms: f64) {
    let last = histogram.len() - 1;
    histogram[((max_f64(ms, 0.0) / bucket_ms) as usize).min(last)] += 1;
}

// Read-phase model (LatencyTracker): smoothing of the per-phase periods, the error comparison and the period.
const PHASE_ALPHA: f64 = 0.1;
const ERR_ALPHA: f64 = 0.05;
const PERIOD_ALPHA: f64 = 0.05;
/// Presented frames scored before the phase estimate may be used.
const MIN_COMPARED: u32 = 16;
const MIN_PERIOD_MS: f64 = 2.0;
const MAX_PERIOD_MS: f64 = 100.0;
/// A refresh change: re-learn the grid from the latest interval.
const OFF_PERIOD_RESET: u32 = 32;

/// Newest first: XRLinuxDriver's three quaternions and their device timestamps (ms).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PoseHistory {
    pub q: [Quat; 3],
    pub device_ms: [f32; 3],
}

pub fn history_of(s: &FeedSample) -> PoseHistory {
    PoseHistory {
        q: [s.orientation, s.older[0], s.older[1]],
        device_ms: [s.device_ms, s.older_ms[0], s.older_ms[1]],
    }
}

/// XRLinuxDriver look_ahead_cfg: {constant ms, frametime multiplier, scanline adjust ms, cap ms}.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct LookAhead {
    pub constant_ms: f32,
    pub frametime_mult: f32,
    pub scanline_ms: f32,
    pub cap_ms: f32,
}

/// `None` unless all four are finite and in sane ranges (constant/scanline 0..50, mult 0..4, cap 1..100).
pub fn look_ahead_from(r: &[f32; 4]) -> Option<LookAhead> {
    if !within(r[0], 0.0, 50.0)
        || !within(r[1], 0.0, 4.0)
        || !within(r[2], 0.0, 50.0)
        || !within(r[3], 1.0, 100.0)
    {
        return None;
    }
    Some(LookAhead {
        constant_ms: r[0],
        frametime_mult: r[1],
        scanline_ms: r[2],
        cap_ms: r[3],
    })
}

/// Body-frame rotation per ms between the two newest samples (axis * rad/ms), or `None` when the samples are unusable:
/// non-increasing or implausible spacing (outside 2..50 ms), non-finite. The spacing is t0 - t1, or the mean of t0 - t1
/// and t1 - t2 when they differ only by timestamp quantisation (whole, truncated ms): the driver's samples are a fixed
/// number of IMU cycles apart.
pub fn angular_velocity(h: &PoseHistory) -> Option<[f32; 3]> {
    let mut dt = h.device_ms[0] - h.device_ms[1];
    if !dt.is_finite() || dt < 2.0 || dt > 50.0 {
        return None;
    }
    // Device timestamps are whole ms, truncated (and for the Luma Ultra's Carina path computed as (ts - ts0) * 1000 in
    // double, which lands a ms low about half the time), so t0 - t1 alone is off by up to +-1 ms out of ~16: 15/16/17 ms
    // for the same spacing. q0..q1 and q1..q2 are the same number of IMU cycles apart (the driver's two N-sample
    // buffers), so when the two gaps agree to within that quantisation (two quanta: each gap can be one off either way)
    // their mean is the spacing with half the error. Unchanged when the gaps are equal; a larger mismatch is real.
    let gap12 = h.device_ms[1] - h.device_ms[2];
    let quantum = max_f32(1.0, next_up(h.device_ms[0]) - h.device_ms[0]); // float ulp past 2^24 ms
    if gap12.is_finite() && gap12 > 0.0 && (dt - gap12).abs() <= 2.0 * quantum {
        dt = (h.device_ms[0] - h.device_ms[2]) / 2.0;
    }
    let q0 = quat_normalized(h.q[0])?;
    let q1 = quat_normalized(h.q[1])?;
    let mut d = quat_mul(quat_conj(q1), q0); // body frame: q0 = q1 * d
    if d.w < 0.0 {
        d = Quat {
            x: -d.x,
            y: -d.y,
            z: -d.z,
            w: -d.w,
        };
    }
    let s = (d.x * d.x + d.y * d.y + d.z * d.z).sqrt();
    if s < 1e-9 {
        return Some([0.0, 0.0, 0.0]);
    }
    let rate = 2.0 * s.atan2(d.w) / dt / s; // (angle / dt) / |axis component|
    Some([d.x * rate, d.y * rate, d.z * rate])
}

/// Body-frame delta to apply on the right of the newest orientation: newest * delta = predicted. Rotation angle is
/// clamped to max_deg. Identity for horizon <= 0.
pub fn prediction_delta(w: &[f32; 3], horizon_ms: f32, max_deg: f32) -> Quat {
    if !(horizon_ms > 0.0) {
        return Quat::default();
    }
    let speed = (w[0] * w[0] + w[1] * w[1] + w[2] * w[2]).sqrt();
    if !speed.is_finite() || speed < 1e-12 {
        return Quat::default();
    }
    let angle = min_f32(speed * horizon_ms, (max_deg as f64 * PI / 180.0) as f32);
    let s = (angle / 2.0).sin() / speed;
    Quat {
        x: w[0] * s,
        y: w[1] * s,
        z: w[2] * s,
        w: (angle / 2.0).cos(),
    }
}

/// Per-frame software latency record keyed by the presentation-feedback ticket.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct LatencySummary {
    pub count: u64,
    /// Discarded/abandoned or unusable clock.
    pub dropped: u64,
    pub mean_ms: f64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub max_ms: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Pending {
    ticket: u64,
    read_ns: u64,
    by_phase_ms: f32,
    by_ema_ms: f32,
    used: bool,
}

impl Default for Pending {
    fn default() -> Self {
        Self {
            ticket: 0,
            read_ns: 0,
            by_phase_ms: -1.0,
            by_ema_ms: -1.0,
            used: false,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct LatencyTracker {
    pending: [Pending; LatencyTracker::CAPACITY],
    histogram: [u64; LatencyTracker::BUCKETS],
    count: u64,
    dropped: u64,
    sum_ms: f64,
    max_ms: f64,
    ema_ms: f64,
    clock_ok: bool,
    // Refresh grid: the newest presented timestamp and the period between presentations.
    grid_ns: u64,
    period_ms: f64,
    /// Presentations in a row that were not one period apart.
    off_period: u32,
    // Per read-phase bin: smoothed whole periods from the grid point before the read to its presentation.
    periods: [f32; LatencyTracker::PHASE_BINS],
    phase_count: [u32; LatencyTracker::PHASE_BINS],
    // Smoothed |error| of each estimate on presented frames.
    phase_err_ms: f64,
    ema_err_ms: f64,
    compared: u32,
    use_phase: bool,
}

impl Default for LatencyTracker {
    fn default() -> Self {
        Self {
            pending: [Pending::default(); Self::CAPACITY],
            histogram: [0; Self::BUCKETS],
            count: 0,
            dropped: 0,
            sum_ms: 0.0,
            max_ms: 0.0,
            ema_ms: 0.0,
            clock_ok: false,
            grid_ns: 0,
            period_ms: 0.0,
            off_period: 0,
            periods: [0.0; Self::PHASE_BINS],
            phase_count: [0; Self::PHASE_BINS],
            phase_err_ms: 0.0,
            ema_err_ms: 0.0,
            compared: 0,
            use_phase: false,
        }
    }
}

impl LatencyTracker {
    pub const CAPACITY: usize = 32;
    pub const BUCKET_MS: f64 = 0.25;
    /// 0..100 ms; larger values land in the last bucket.
    pub const BUCKETS: usize = 400;
    /// Read phase within the refresh period.
    pub const PHASE_BINS: usize = 16;

    pub fn new() -> Self {
        Self::default()
    }

    /// The compositor's presentation clock; only CLOCK_MONOTONIC (1) is comparable with our reads.
    pub fn clock(&mut self, id: u32) {
        self.clock_ok = id == 1;
    }
    pub fn clock_ok(&self) -> bool {
        self.clock_ok
    }

    /// Pose read for this frame.
    pub fn read(&mut self, ticket: u64, read_ns: u64) {
        let slot = match self.pending.iter().position(|p| !p.used) {
            Some(i) => i,
            // Full: recycle the oldest frame (its feedback never arrived). The first smallest ticket, as min_element.
            None => {
                let mut best = 0;
                for i in 1..Self::CAPACITY {
                    if self.pending[i].ticket < self.pending[best].ticket {
                        best = i;
                    }
                }
                best
            }
        };
        // Both estimates as they stood for this read, scored when the frame is presented.
        let phase = self.by_phase(read_ns);
        self.pending[slot] = Pending {
            ticket,
            read_ns,
            by_phase_ms: phase.map_or(-1.0, |p| p as f32),
            by_ema_ms: if self.count != 0 {
                self.ema_ms as f32
            } else {
                -1.0
            },
            used: true,
        };
    }

    pub fn presented(&mut self, ticket: u64, present_ns: u64) {
        let Some(slot) = self
            .pending
            .iter()
            .position(|p| p.used && p.ticket == ticket)
        else {
            self.dropped += 1;
            return;
        };
        let p = self.pending[slot];
        self.pending[slot] = Pending::default();
        if !self.clock_ok || present_ns < p.read_ns {
            self.dropped += 1;
            return;
        }
        let ms = (present_ns - p.read_ns) as f64 / 1e6;
        add(&mut self.histogram, ms, Self::BUCKET_MS);
        self.ema_ms = if self.count != 0 {
            self.ema_ms + 0.1 * (ms - self.ema_ms)
        } else {
            ms
        };
        self.count += 1;
        self.sum_ms += ms;
        self.max_ms = max_f64(self.max_ms, ms);
        if p.by_phase_ms >= 0.0 && p.by_ema_ms >= 0.0 {
            // Score both estimates; switch with hysteresis.
            self.phase_err_ms +=
                ERR_ALPHA * ((ms - p.by_phase_ms as f64).abs() - self.phase_err_ms);
            self.ema_err_ms += ERR_ALPHA * ((ms - p.by_ema_ms as f64).abs() - self.ema_err_ms);
            self.compared = self.compared.saturating_add(1);
            self.use_phase = self.compared >= MIN_COMPARED
                && if self.use_phase {
                    self.phase_err_ms <= self.ema_err_ms
                } else {
                    self.phase_err_ms < 0.8 * self.ema_err_ms
                };
        }
        self.learn_grid(present_ns);
        // Whole periods from the grid point before the read, binned by the read's phase on this grid. A stall
        // (presented more than two periods later than the average said) is not a property of the phase: the average
        // absorbs it, the bins do not learn it.
        let stall = p.by_ema_ms >= 0.0 && ms > p.by_ema_ms as f64 + 2.0 * self.period_ms;
        if self.period_ms > 0.0 && !stall {
            let k = (ms / self.period_ms).ceil();
            let phase = k * self.period_ms - ms;
            let bin = (Self::PHASE_BINS - 1)
                .min(max_f64(0.0, phase / self.period_ms * Self::PHASE_BINS as f64) as usize);
            self.periods[bin] = if self.phase_count[bin] != 0 {
                self.periods[bin] + (PHASE_ALPHA * (k - self.periods[bin] as f64)) as f32
            } else {
                k as f32
            };
            self.phase_count[bin] = self.phase_count[bin].saturating_add(1);
        }
    }

    fn learn_grid(&mut self, present_ns: u64) {
        if self.grid_ns != 0 && present_ns <= self.grid_ns {
            return; // late or repeated feedback: keep the newest point
        }
        if self.grid_ns != 0 {
            let d = (present_ns - self.grid_ns) as f64 / 1e6;
            let usable = (MIN_PERIOD_MS..=MAX_PERIOD_MS).contains(&d);
            if usable
                && (!(self.period_ms > 0.0)
                    || d < 0.75 * self.period_ms
                    || self.off_period >= OFF_PERIOD_RESET)
            {
                // First interval or a new refresh rate: earlier phases no longer apply.
                self.period_ms = d;
                self.off_period = 0;
                self.periods = [0.0; Self::PHASE_BINS];
                self.phase_count = [0; Self::PHASE_BINS];
                self.phase_err_ms = 0.0;
                self.ema_err_ms = 0.0;
                self.compared = 0;
                self.use_phase = false;
            } else if usable && d < 1.25 * self.period_ms {
                self.period_ms += PERIOD_ALPHA * (d - self.period_ms);
                self.off_period = 0;
            } else {
                self.off_period = self.off_period.saturating_add(1); // skipped refreshes, a pause, or no fixed grid
            }
        }
        self.grid_ns = present_ns;
    }

    fn by_phase(&self, now_ns: u64) -> Option<f64> {
        if !(self.period_ms > 0.0) || self.grid_ns == 0 || now_ns < self.grid_ns {
            return None;
        }
        let since = (now_ns - self.grid_ns) as f64 / 1e6;
        if since > 1000.0 {
            return None; // the grid is too old to extend
        }
        let phase = since % self.period_ms;
        let bins = Self::PHASE_BINS as i64;
        let bin = (bins - 1).min((phase / self.period_ms * bins as f64) as i64);
        for d in 0..=bins / 2 {
            // Nearest phase seen at least twice.
            for j in [bin - d, bin + d] {
                let w = ((j % bins + bins) % bins) as usize;
                if self.phase_count[w] < 2 {
                    continue;
                }
                // A neighbour across the wrap read on the other side of a grid point: shift this phase by a period.
                let p = if j < 0 {
                    phase + self.period_ms
                } else if j >= bins {
                    phase - self.period_ms
                } else {
                    phase
                };
                return Some(max_f64(0.0, self.periods[w] as f64 * self.period_ms - p));
            }
        }
        None
    }

    pub fn discarded(&mut self, ticket: u64) {
        if let Some(slot) = self
            .pending
            .iter_mut()
            .find(|p| p.used && p.ticket == ticket)
        {
            *slot = Pending::default();
        }
        self.dropped += 1;
    }

    /// All pending frames lost.
    pub fn abandon(&mut self) {
        for p in self.pending.iter_mut().filter(|p| p.used) {
            *p = Pending::default();
            self.dropped += 1;
        }
    }

    /// Expected read->present for a pose read at now_ns (CLOCK_MONOTONIC); `None` until a few frames have been
    /// measured. The smoothed average (EMA) until the refresh grid is learned from presented timestamps and estimating
    /// per read phase on it has beaten the average on recent frames; then the estimate for a read at this phase (a read
    /// later in the refresh period is presented sooner). No allocation, no system call.
    pub fn expected_ms_at(&self, now_ns: u64) -> Option<f64> {
        if self.count < 8 {
            return None;
        }
        if self.use_phase {
            if let Some(p) = self.by_phase(now_ns) {
                return Some(p);
            }
        }
        Some(self.ema_ms)
    }

    /// For a read now (one vDSO clock read).
    pub fn expected_ms(&self) -> Option<f64> {
        self.expected_ms_at(monotonic_ns())
    }

    /// The per-phase estimate is in use.
    pub fn phase_aware(&self) -> bool {
        self.use_phase
    }

    pub fn summary(&self) -> LatencySummary {
        let mut s = LatencySummary {
            count: self.count,
            dropped: self.dropped,
            ..LatencySummary::default()
        };
        if self.count != 0 {
            s.mean_ms = self.sum_ms / self.count as f64;
            s.max_ms = self.max_ms;
            s.p50_ms = percentile(&self.histogram, self.count, 0.50, Self::BUCKET_MS);
            s.p95_ms = percentile(&self.histogram, self.count, 0.95, Self::BUCKET_MS);
        }
        s
    }
}

const NO_WINDOW: i64 = i64::MIN;

/// Relative pose age: host read time minus device timestamp, less the smallest such value seen over the last few seconds
/// of device time (device and host clocks have an unknown offset, and a device crystal that drifts against the host by
/// tens of ppm would otherwise grow the age by ms per minute; a feed whose device clock stops keeps its floor, so its age
/// keeps growing). At ~1 kHz the minimum approaches the transport floor, so this is age ABOVE the best recent case, not
/// an absolute age.
#[derive(Clone, Debug, PartialEq)]
pub struct PoseAge {
    /// Per device second: smallest host - device.
    window_min: [f64; PoseAge::FLOOR_WINDOWS],
    window_s: [i64; PoseAge::FLOOR_WINDOWS],
    last_device_ms: f32,
    count: u64,
    sum_ms: f64,
    max_ms: f64,
    /// 0.25 ms buckets, 0..50 ms.
    histogram: [u64; 200],
}

impl Default for PoseAge {
    fn default() -> Self {
        Self {
            window_min: [0.0; Self::FLOOR_WINDOWS],
            window_s: [NO_WINDOW; Self::FLOOR_WINDOWS],
            last_device_ms: -1.0,
            count: 0,
            sum_ms: 0.0,
            max_ms: 0.0,
            histogram: [0; 200],
        }
    }
}

impl PoseAge {
    /// One-second windows of device time.
    pub const FLOOR_WINDOWS: usize = 8;

    pub fn new() -> Self {
        Self::default()
    }

    /// `None` on reset/backward device clock.
    pub fn observe(&mut self, host_ms: f64, device_ms: f32) -> Option<f64> {
        if !device_ms.is_finite() || !host_ms.is_finite() {
            return None;
        }
        if self.last_device_ms >= 0.0 && device_ms < self.last_device_ms {
            // Driver/device restart: new clock epoch.
            self.window_s = [NO_WINDOW; Self::FLOOR_WINDOWS];
            self.last_device_ms = device_ms;
            return None;
        }
        self.last_device_ms = device_ms;
        let offset = host_ms - device_ms as f64;
        // Floor: the smallest offset over the last FLOOR_WINDOWS seconds of DEVICE time (one minimum per second).
        // Device time, so a frozen feed (same sample read again and again) keeps its floor and its age keeps growing, as
        // with a global minimum, while drift against the host only spans the window.
        let windows = Self::FLOOR_WINDOWS as i64;
        let second = (device_ms as f64 / 1000.0).floor() as i64;
        let slot = ((second % windows + windows) % windows) as usize;
        if self.window_s[slot] != second {
            self.window_s[slot] = second;
            self.window_min[slot] = offset;
        } else {
            self.window_min[slot] = min_f64(self.window_min[slot], offset);
        }
        let mut floor = offset;
        for i in 0..Self::FLOOR_WINDOWS {
            if self.window_s[i] != NO_WINDOW
                && self.window_s[i] <= second
                && second - self.window_s[i] < windows
            {
                floor = min_f64(floor, self.window_min[i]);
            }
        }
        let age = offset - floor;
        add(&mut self.histogram, age, 0.25);
        self.count += 1;
        self.sum_ms += age;
        self.max_ms = max_f64(self.max_ms, age);
        Some(age)
    }

    pub fn summary(&self) -> LatencySummary {
        let mut s = LatencySummary {
            count: self.count,
            ..LatencySummary::default()
        };
        if self.count != 0 {
            s.mean_ms = self.sum_ms / self.count as f64;
            s.max_ms = self.max_ms;
            s.p50_ms = percentile(&self.histogram, self.count, 0.50, 0.25);
            s.p95_ms = percentile(&self.histogram, self.count, 0.95, 0.25);
        }
        s
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

/// The driver's scan-out times against what the compositor says (rust-rebuild-plan D5), per presented frame; see
/// `VsyncMeter`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct VsyncSummary {
    pub count: u64,
    /// Presented with no usable snapshot.
    pub skipped: u64,
    /// The mean phase of the horizon error: panel and link, a constant.
    pub offset_ms: f64,
    /// |horizon error - offset|.
    pub error_p50_ms: f64,
    pub error_p95_ms: f64,
    pub error_max_ms: f64,
    pub sensor_p50_ms: f64,
    pub sensor_p95_ms: f64,
    pub sensor_max_ms: f64,
    pub sensor_count: u64,
    /// The newest snapshot's.
    pub period_ms: f64,
}

/// Per presented frame:
///   horizon error    present - the driver's scan-out nearest it, wrapped into half a period either side. Its mean (as
///                    a phase) is the constant between the two: the compositor's vblank against the glasses' panel, and
///                    the link floor the driver cannot see. What the gate measures is the spread about that constant.
///   sensor to vsync  that scan-out on the feed's clock minus the device_ms of the sample the frame drew.
/// Only with a snapshot whose anchor is under MAX_ANCHOR_AGE_NS from the present. The newest FRAMES frames are kept.
#[derive(Clone, Debug, PartialEq)]
pub struct VsyncMeter {
    error_ms: Box<[f32]>,
    sensor_ms: Box<[f32]>,
    // Written in all; the newest FRAMES are kept.
    errors: usize,
    sensors: usize,
    skipped: u64,
    period_ms: f64,
}

impl Default for VsyncMeter {
    fn default() -> Self {
        Self {
            error_ms: vec![0.0; Self::FRAMES].into_boxed_slice(),
            sensor_ms: vec![0.0; Self::FRAMES].into_boxed_slice(),
            errors: 0,
            sensors: 0,
            skipped: 0,
            period_ms: 0.0,
        }
    }
}

impl VsyncMeter {
    /// About 70 s at 120 Hz.
    pub const FRAMES: usize = 8192;
    pub const MAX_ANCHOR_AGE_NS: f64 = 250e6;

    pub fn new() -> Self {
        Self::default()
    }

    /// sample_ms: the device_ms of the sample drawn; negative: none.
    pub fn presented(&mut self, vsync: Option<&VsyncSnapshot>, present_ns: u64, sample_ms: f32) {
        let age = vsync.map_or(0.0, |v| {
            present_ns.wrapping_sub(v.anchor_host_ns) as i64 as f64
        });
        let Some(v) = vsync.filter(|_| age.abs() < Self::MAX_ANCHOR_AGE_NS) else {
            self.skipped += 1;
            return;
        };
        let k = (age / v.period_ns).round();
        let error_ms = (age - k * v.period_ns) / 1e6;
        self.error_ms[self.errors % Self::FRAMES] = error_ms as f32;
        self.errors += 1;
        self.period_ms = v.period_ns / 1e6;
        if sample_ms >= 0.0 && v.anchor_feed_ms.is_finite() {
            let scanout_ms = v.anchor_feed_ms + k * self.period_ms;
            self.sensor_ms[self.sensors % Self::FRAMES] = (scanout_ms - sample_ms as f64) as f32;
            self.sensors += 1;
        }
    }

    pub fn summary(&self) -> VsyncSummary {
        let mut out = VsyncSummary {
            count: self.errors as u64,
            skipped: self.skipped,
            period_ms: self.period_ms,
            ..VsyncSummary::default()
        };
        let at = |v: &[f32], q: f64| v[(v.len() - 1).min((q * v.len() as f64) as usize)] as f64;
        let sort = |v: &mut Vec<f32>| {
            v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        };
        if self.errors != 0 {
            // The offset is circular (half a period either way is the same), so it is taken as the mean phase, and the
            // errors measured about it, wrapped again.
            let mut e = self.error_ms[..self.errors.min(Self::FRAMES)].to_vec();
            let p = if self.period_ms > 0.0 {
                self.period_ms
            } else {
                1.0
            };
            let (mut c, mut s) = (0.0f64, 0.0f64);
            for &x in &e {
                c += (2.0 * PI * x as f64 / p).cos();
                s += (2.0 * PI * x as f64 / p).sin();
            }
            out.offset_ms = s.atan2(c) * p / (2.0 * PI);
            for x in e.iter_mut() {
                let mut d = *x as f64 - out.offset_ms;
                d -= (d / p).round() * p;
                *x = d.abs() as f32;
            }
            sort(&mut e);
            out.error_p50_ms = at(&e, 0.5);
            out.error_p95_ms = at(&e, 0.95);
            out.error_max_ms = *e.last().unwrap() as f64;
        }
        if self.sensors != 0 {
            let mut e = self.sensor_ms[..self.sensors.min(Self::FRAMES)].to_vec();
            sort(&mut e);
            out.sensor_count = self.sensors as u64;
            out.sensor_p50_ms = at(&e, 0.5);
            out.sensor_p95_ms = at(&e, 0.95);
            out.sensor_max_ms = *e.last().unwrap() as f64;
        }
        out
    }
}

/// Horizon (ms from the newest sample) for this frame, or `None` if prediction cannot be bounded. frametime_ms is used
/// only if no read->present measurement is available yet (driver formula).
pub fn prediction_horizon(
    cfg: Option<&LookAhead>,
    read_to_present_ms: Option<f64>,
    pose_age_ms: f64,
    frametime_ms: f64,
) -> Option<f32> {
    let cfg = cfg?;
    let age = clamp_f64(
        if pose_age_ms.is_finite() {
            pose_age_ms
        } else {
            0.0
        },
        0.0,
        cfg.cap_ms as f64,
    );
    let ahead = match read_to_present_ms {
        Some(r) => age + r + cfg.constant_ms as f64,
        None => cfg.constant_ms as f64 + max_f64(frametime_ms, 0.0) * cfg.frametime_mult as f64,
    };
    Some((min_f64(ahead, cfg.cap_ms as f64) + (cfg.scanline_ms / 2.0) as f64) as f32)
}
