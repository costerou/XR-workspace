//! What goes into the feed's 16 floats: the C++ `luma::Publisher`
//! (`spike/driver/include/luma/publish.hpp`, `spike/driver/src/publish.cpp`) on top of
//! [`FeedWriter`]. The history ring, the millisecond clock, the mount pitch and the
//! `disabled` handling are the same, so a reader cannot tell the two apart.
//!
//! - q1 is the sample `history_step` samples back, q2 twice that; `history_step` is
//!   [`history_step_for`] the IMU rate.
//! - instant_start: a sample is published from the first one on. Until there is a sample
//!   `history_step` back, q1 and q2 repeat q0 and its t, which turns the reader's prediction off.
//! - t is milliseconds from the first sample, with its fraction, restarting at 0 a second short
//!   of 2^24 and whenever the clock goes back.
//! - `disabled` stays true until the first real write, and goes back to true on [`Publisher::pause`]
//!   or an uncalibrated pose.
//! - publish never waits: a held lock skips that write, the sample stays in the history.
//!
//! For one thread. `publish` does not allocate.

use crate::feed::{FeedError, FeedWriter, WriterOptions};
use crate::prediction::MotionState;

const MAX_HISTORY_STEP: u32 = 1024;
const NS_PER_MS: u64 = 1_000_000;
/// XRLinuxDriver `src/devices/viture.c` VITURE_FLOAT_EXACT_MS_LIMIT - VITURE_FLOAT_RESET_MARGIN_MS.
const RESTART_MS: u64 = (1 << 24) - 1000;
/// The C++ 3.14159265358979f rounds to this same float.
const DEG_TO_RAD: f32 = std::f32::consts::PI / 180.0;

/// XRLinuxDriver `src/devices/viture.c viture_apply_imu_rate`.
pub const fn history_step_for(imu_hz: u32) -> u32 {
    if imu_hz < 120 { 1 } else { imu_hz / 60 }
}

/// A unit quaternion, w x y z as the C++ `Quatf`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Quat {
    pub w: f32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

/// One pose to publish. `q`: x forward, y left, z up, body to reference. `t_ns`: any clock that
/// only goes forward.
#[derive(Debug, Clone, Copy)]
pub struct Pose {
    pub q: Quat,
    pub t_ns: u64,
    pub calibrated: bool,
}

/// Options for [`Publisher::open`]. The feed's own options live in `feed`.
#[derive(Debug, Clone)]
pub struct PublisherOptions {
    pub feed: WriterOptions,
    /// [`history_step_for`] the IMU rate; 1..=1024.
    pub history_step: u32,
    /// Tilt of the display against the pose's body frame, turned out of every pose as q * pitch.
    /// 0 = none.
    pub mount_pitch_deg: f32,
    /// false = silent until the history is full, exactly as XRLinuxDriver.
    pub instant_start: bool,
    /// false = t truncated to whole ms, exactly as XRLinuxDriver.
    pub fractional_ms: bool,
}

impl Default for PublisherOptions {
    fn default() -> Self {
        Self {
            feed: WriterOptions::default(),
            history_step: 16,
            mount_pitch_deg: -8.5,
            instant_start: true,
            fractional_ms: true,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PublisherStats {
    pub written: u64,
    /// Quaternions dropped as non-finite or zero.
    pub invalid: u64,
    /// Writes skipped on a held lock, their samples kept.
    pub busy: u64,
    /// Motion states written to `pose_prediction`.
    pub motion: u64,
}

#[derive(Clone, Copy, Default)]
struct Entry {
    q: [f32; 4], // the feed's order, xyzw
    ms: f32,
}

pub struct Publisher {
    writer: FeedWriter,
    step: usize,
    mount: Option<Quat>,
    instant_start: bool,
    fractional_ms: bool,
    ring: Vec<Entry>, // 2 * step + 1, ring[head] the oldest once full
    head: usize,
    count: usize,
    epoch_ns: u64,
    last_ns: u64,
    clock_started: bool,
    live: bool,
    stats: PublisherStats,
}

/// What the reader's quaternion normalisation accepts.
fn usable(q: &Quat) -> bool {
    let n = (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w).sqrt();
    n.is_finite() && n >= 1e-6
}

fn conjugate(q: &Quat) -> Quat {
    Quat { w: q.w, x: -q.x, y: -q.y, z: -q.z }
}

/// A body-frame vector of the pose, seen from the frame `q * m` puts it in.
fn seen_from(m: &Quat, v: [f32; 3]) -> [f32; 3] {
    let r = multiply(&multiply(&conjugate(m), &Quat { w: 0.0, x: v[0], y: v[1], z: v[2] }), m);
    [r.x, r.y, r.z]
}

fn multiply(a: &Quat, b: &Quat) -> Quat {
    Quat {
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    }
}

impl Publisher {
    /// Opens the feed (left disabled) with the given history and mount.
    pub fn open(options: &PublisherOptions) -> Result<Self, FeedError> {
        if !(1..=MAX_HISTORY_STEP).contains(&options.history_step) {
            return Err(FeedError(format!(
                "history_step must be 1..{MAX_HISTORY_STEP}"
            )));
        }
        let mount = (options.mount_pitch_deg != 0.0).then(|| {
            // XRLinuxDriver src/imu.c device_pitch_adjustment
            let half = options.mount_pitch_deg * DEG_TO_RAD / 2.0;
            Quat {
                w: half.cos(),
                x: 0.0,
                y: half.sin(),
                z: 0.0,
            }
        });
        let step = options.history_step as usize;
        Ok(Self {
            writer: FeedWriter::open(&options.feed)?,
            step,
            mount,
            instant_start: options.instant_start,
            fractional_ms: options.fractional_ms,
            ring: vec![Entry::default(); 2 * step + 1],
            head: 0,
            count: 0,
            epoch_ns: 0,
            last_ns: 0,
            clock_started: false,
            live: false,
            stats: PublisherStats::default(),
        })
    }

    fn device_ms(&mut self, t_ns: u64) -> f32 {
        if !self.clock_started || t_ns < self.last_ns {
            self.epoch_ns = t_ns;
            self.clock_started = true;
        }
        self.last_ns = t_ns;
        let ms = (t_ns - self.epoch_ns) / NS_PER_MS; // truncated, like the driver's (uint32_t) cast
        if ms >= RESTART_MS {
            self.epoch_ns = t_ns;
            return 0.0;
        }
        if self.fractional_ms {
            ((t_ns - self.epoch_ns) as f64 / NS_PER_MS as f64) as f32
        } else {
            ms as f32
        }
    }

    /// True when the feed was written. False for a non-finite or zero quaternion (dropped), on a
    /// held lock, for an uncalibrated pose (which pauses the feed), and without instant_start
    /// while the history fills.
    pub fn publish(&mut self, pose: &Pose) -> bool {
        if !pose.calibrated {
            if self.live || self.count != 0 {
                self.pause();
            }
            return false;
        }
        if !usable(&pose.q) {
            self.stats.invalid += 1;
            return false;
        }
        let q = match &self.mount {
            Some(m) => multiply(&pose.q, m),
            None => pose.q,
        };
        let size = self.ring.len();
        let slot = (self.head + self.count) % size;
        let ms = self.device_ms(pose.t_ns);
        self.ring[slot] = Entry {
            q: [q.x, q.y, q.z, q.w],
            ms,
        };
        if self.count < size {
            self.count += 1;
        } else {
            self.head = (self.head + 1) % size;
        }
        if self.count < size && !self.instant_start {
            return false;
        }
        // Newest, step back, twice that; until those exist, the nearest that does.
        let back1 = if self.count > self.step { self.step } else { 0 };
        let back2 = if self.count > 2 * self.step {
            2 * self.step
        } else {
            back1
        };
        let q0 = self.ring[slot];
        let q1 = self.ring[(slot + size - back1) % size];
        let q2 = self.ring[(slot + size - back2) % size];
        let mut v = [0f32; 16];
        v[0..4].copy_from_slice(&q0.q);
        v[4..8].copy_from_slice(&q1.q);
        v[8..12].copy_from_slice(&q2.q);
        v[12] = q0.ms;
        v[13] = q1.ms;
        v[14] = q2.ms;
        if !self.writer.write(&v) {
            self.stats.busy += 1;
            return false;
        }
        if !self.live {
            // after the write: a reader's first sample is a real one
            self.writer.set_disabled(false);
            self.live = true;
        }
        self.stats.written += 1;
        true
    }

    /// The newest pose's motion, for `pose_prediction`: `state.q` is the pose as given to
    /// [`publish`](Self::publish), and omega and alpha are in its body frame. The mount is turned
    /// out of all three, as the 16 floats have it. An invalid, uncalibrated or unusable state
    /// clears the channel. `host_ns` is CLOCK_MONOTONIC when the sample arrived: readers time
    /// their prediction from it.
    pub fn publish_motion(&mut self, state: &MotionState) {
        let [w, x, y, z] = state.q;
        let q = Quat { w, x, y, z };
        if !state.valid || !state.calibrated || !usable(&q) {
            self.writer.write_motion(&MotionState::default());
            return;
        }
        let mut out = *state;
        if let Some(m) = &self.mount {
            let r = multiply(&q, m);
            out.q = [r.w, r.x, r.y, r.z];
            out.omega_rad_s = seen_from(m, state.omega_rad_s);
            out.alpha_rad_s2 = seen_from(m, state.alpha_rad_s2);
        }
        self.writer.write_motion(&out);
        self.stats.motion += 1;
    }

    /// Stale or gone: readers get no sample until the next publish that writes. The history
    /// starts over; the clock of t is kept. The motion channel is cleared.
    pub fn pause(&mut self) {
        self.writer.set_disabled(true);
        self.writer.write_motion(&MotionState::default());
        self.live = false;
        self.head = 0;
        self.count = 0;
    }

    pub fn stats(&self) -> PublisherStats {
        self.stats
    }
}
