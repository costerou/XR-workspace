//! Orientation maths the view, HUD and dumps need (from `spike/src/pose.cpp`).
//!
//! Also `PoseState`, `ModeController` and `PoseFeed` (the seqlock channel first, the SysV mutex feed otherwise; the shared
//! memory itself is xr-pose's).
//! All maths is `f32`, in the same order as the C++, so dumps match it bit for bit.

use std::f32::consts::PI;

/// A unit quaternion in the driver frame (x forward, y left, z up).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quat {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub w: f32,
}

impl Default for Quat {
    fn default() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            w: 1.0,
        }
    }
}

/// Row-major 3x3 matrix.
pub type Mat3 = [f32; 9];

pub const IDENTITY: Mat3 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

/// gl = M * driver: right = -left, up = up, back = -forward. A proper rotation (det +1).
const DRIVER_TO_GL: Mat3 = [0.0, -1.0, 0.0, 0.0, 0.0, 1.0, -1.0, 0.0, 0.0];
/// Radians: this near half a turn, which way round is the push's to say.
const HALF_TURN_SLACK: f32 = 1e-2;

pub fn quat_mul(a: Quat, b: Quat) -> Quat {
    Quat {
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
    }
}

pub fn quat_conj(q: Quat) -> Quat {
    Quat {
        x: -q.x,
        y: -q.y,
        z: -q.z,
        w: q.w,
    }
}

/// `None` if non-finite or near zero length.
pub fn quat_normalized(q: Quat) -> Option<Quat> {
    let n = (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w).sqrt();
    if !n.is_finite() || n < 1e-6 {
        return None;
    }
    Some(Quat {
        x: q.x / n,
        y: q.y / n,
        z: q.z / n,
        w: q.w / n,
    })
}

pub fn quat_to_mat3(q: Quat) -> Mat3 {
    let (x, y, z, w) = (q.x, q.y, q.z, q.w);
    [
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y - w * z),
        2.0 * (x * z + w * y),
        2.0 * (x * y + w * z),
        1.0 - 2.0 * (x * x + z * z),
        2.0 * (y * z - w * x),
        2.0 * (x * z - w * y),
        2.0 * (y * z + w * x),
        1.0 - 2.0 * (x * x + y * y),
    ]
}

/// `quat_to_mat3`'s inverse for a rotation matrix (identity if degenerate).
pub fn mat3_to_quat(m: &Mat3) -> Quat {
    // Shepperd: from the largest of w, x, y, z, so no square root is taken of a small difference.
    let tr = m[0] + m[4] + m[8];
    let q = if tr > 0.0 {
        let s = (tr + 1.0).sqrt() * 2.0;
        Quat {
            x: (m[7] - m[5]) / s,
            y: (m[2] - m[6]) / s,
            z: (m[3] - m[1]) / s,
            w: s / 4.0,
        }
    } else if m[0] > m[4] && m[0] > m[8] {
        let s = (1.0 + m[0] - m[4] - m[8]).sqrt() * 2.0;
        Quat {
            x: s / 4.0,
            y: (m[1] + m[3]) / s,
            z: (m[2] + m[6]) / s,
            w: (m[7] - m[5]) / s,
        }
    } else if m[4] > m[8] {
        let s = (1.0 + m[4] - m[0] - m[8]).sqrt() * 2.0;
        Quat {
            x: (m[1] + m[3]) / s,
            y: s / 4.0,
            z: (m[5] + m[7]) / s,
            w: (m[2] - m[6]) / s,
        }
    } else {
        let s = (1.0 + m[8] - m[0] - m[4]).sqrt() * 2.0;
        Quat {
            x: (m[2] + m[6]) / s,
            y: (m[5] + m[7]) / s,
            z: s / 4.0,
            w: (m[3] - m[1]) / s,
        }
    };
    quat_normalized(q).unwrap_or_default()
}

pub fn mul(a: &Mat3, b: &Mat3) -> Mat3 {
    let mut r = [0.0f32; 9];
    for i in 0..3 {
        for j in 0..3 {
            for k in 0..3 {
                r[i * 3 + j] += a[i * 3 + k] * b[k * 3 + j];
            }
        }
    }
    r
}

pub fn transpose(m: &Mat3) -> Mat3 {
    [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]
}

pub fn driver_to_gl(driver: &Mat3) -> Mat3 {
    mul(&mul(&DRIVER_TO_GL, driver), &transpose(&DRIVER_TO_GL))
}

pub fn apply(m: &Mat3, v: [f32; 3]) -> [f32; 3] {
    [
        m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
        m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
        m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
    ]
}

pub fn project(view: &Mat3, ndc_x: f32, ndc_y: f32, hw: f32, hh: f32) -> Option<[f32; 2]> {
    let v = apply(view, [ndc_x * hw, ndc_y * hh, -1.0]);
    if v[2] > -1e-4 || hw <= 0.0 || hh <= 0.0 {
        return None;
    }
    Some([v[0] / -v[2] / hw, v[1] / -v[2] / hh])
}

/// Shortest arc.
pub fn quat_slerp(a: Quat, mut b: Quat, t: f32) -> Quat {
    let mut d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
    if d < 0.0 {
        b = Quat {
            x: -b.x,
            y: -b.y,
            z: -b.z,
            w: -b.w,
        };
        d = -d;
    }
    let (mut wa, mut wb) = (1.0 - t, t);
    if d < 0.9995 {
        let th = d.min(1.0).acos();
        let s = th.sin();
        wa = ((1.0 - t) * th).sin() / s;
        wb = (t * th).sin() / s;
    }
    quat_normalized(Quat {
        x: wa * a.x + wb * b.x,
        y: wa * a.y + wb * b.y,
        z: wa * a.z + wb * b.z,
        w: wa * a.w + wb * b.w,
    })
    .unwrap_or(b)
}

/// Radians between orientations (atan2 form: accurate near zero, where acos is not).
pub fn quat_angle(a: Quat, b: Quat) -> f32 {
    let d = quat_mul(quat_conj(a), b);
    2.0 * (d.x * d.x + d.y * d.y + d.z * d.z).sqrt().atan2(d.w.abs())
}

/// Same yaw and pitch of the forward axis, zero roll (driver frame).
pub fn level(q: Quat) -> Quat {
    let f = apply(&quat_to_mat3(q), [1.0, 0.0, 0.0]); // forward (x) in the reference frame; z up
    let yaw = f[1].atan2(f[0]);
    let pitch = f[2].clamp(-1.0, 1.0).asin();
    let qy = Quat {
        x: 0.0,
        y: 0.0,
        z: (yaw / 2.0).sin(),
        w: (yaw / 2.0).cos(),
    };
    let qp = Quat {
        x: 0.0,
        y: (-pitch / 2.0).sin(),
        z: 0.0,
        w: (-pitch / 2.0).cos(),
    }; // +pitch tips forward toward +z
    quat_mul(qy, qp)
}

/// Render rotation for a canvas anchored at `anchor`.
pub fn view_from(head: Quat, anchor: Quat) -> Mat3 {
    transpose(&driver_to_gl(&quat_to_mat3(quat_mul(
        quat_conj(anchor),
        head,
    ))))
}

pub fn canvas_up(reference: Quat, anchor: Quat) -> [f32; 3] {
    let v = apply(
        &transpose(&quat_to_mat3(quat_mul(reference, anchor))),
        [0.0, 0.0, 1.0],
    );
    apply(&DRIVER_TO_GL, v)
}

pub fn aim_rotation(az: f32, el: f32) -> Quat {
    // GL: the view must apply R^T = El(el)^T . Az(az)^T (place_point's R = Az(az) . El(el)); in the driver frame
    // (x forward, y left, z up) that is a pitch of el about +y after a yaw of az about +z.
    quat_mul(
        Quat {
            x: 0.0,
            y: (el / 2.0).sin(),
            z: 0.0,
            w: (el / 2.0).cos(),
        },
        Quat {
            x: 0.0,
            y: 0.0,
            z: (az / 2.0).sin(),
            w: (az / 2.0).cos(),
        },
    )
}

pub fn aim_along(from: Quat, to: Quat, way: f32, turned: f32, side: u8) -> Quat {
    let s = if way > 0.0 {
        (turned / way).clamp(0.0, 1.0)
    } else {
        1.0
    };
    if way < PI - HALF_TURN_SLACK {
        return quat_slerp(from, to, s);
    }
    let mut d = quat_mul(quat_conj(from), to);
    if d.w < 0.0 {
        d = Quat {
            x: -d.x,
            y: -d.y,
            z: -d.z,
            w: -d.w,
        };
    }
    let sn = (d.x * d.x + d.y * d.y + d.z * d.z).sqrt();
    #[allow(clippy::neg_cmp_op_on_partial_ord)] // true for NaN too, as in the C++
    if !(sn > 1e-6) {
        return if s < 1.0 { from } else { to };
    }
    // The push as a turn of the canvas: right about -y, left about +y, up about +x, down about -x
    // (GL frame, one quarter of `from`'s view).
    const PUSH: [[f32; 3]; 4] = [
        [0.0, -1.0, 0.0],
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
    ];
    let push = apply(&transpose(&DRIVER_TO_GL), PUSH[usize::from(side.min(3))]);
    let mut axis = [d.x / sn, d.y / sn, d.z / sn];
    let mut angle = 2.0 * sn.atan2(d.w);
    if axis[0] * push[0] + axis[1] * push[1] + axis[2] * push[2] > 0.0 {
        // a turn about +a of the aim is one about -a of the canvas
        for v in &mut axis {
            *v = -*v;
        }
        angle = 2.0 * PI - angle;
    }
    let half = s * angle / 2.0;
    let k = half.sin();
    quat_normalized(quat_mul(
        from,
        Quat {
            x: axis[0] * k,
            y: axis[1] * k,
            z: axis[2] * k,
            w: half.cos(),
        },
    ))
    .unwrap_or(to)
}

/// One read of the feed (`xr::FeedSample`). `older` and `older_ms` are the feed's history; `sample_ns` is the seqlock
/// record's CLOCK_MONOTONIC time, 0 from the mutex feed.
#[derive(Clone, Copy, Debug, PartialEq, Default)]
pub struct FeedSample {
    pub orientation: Quat,
    pub device_ms: f32,
    pub older: [Quat; 2],
    pub older_ms: [f32; 2],
    pub sample_ns: u64,
}

const DEG_TO_RAD: f32 = PI / 180.0;
const UP_REACH_DEG: f32 = 45.0;
const UP_WORN_DEG: f32 = 60.0;
const UP_FEED_DEG: f32 = 8.0;
const UP_HEAD_DEG: f32 = 20.0;
const UP_NOISE: f32 = 0.0167;
const UP_DRIFT: f32 = 5e-6;
const UP_MAX_STEP_MS: f64 = 100.0;
const UP_MIN_STEP_MS: f64 = 1.0;

/// The head pose against the recentre reference, held while the feed is gone and re-anchored after a drop
/// (`xr::PoseState`; see spike/include/xr/pose.hpp for the up estimate and the level reference).
#[derive(Clone, Debug, PartialEq)]
pub struct PoseState {
    pub(crate) stale_ms: f64,
    pub(crate) center: Option<Quat>,
    pub(crate) up: [f32; 3],
    pub(crate) up_cov: [f32; 9],
    pub(crate) last: Quat,
    pub(crate) last_device_ms: f32,
    pub(crate) last_change_ms: f64,
    pub(crate) lost_at_ms: f64,
    pub(crate) settling_from_ms: f64,
    pub(crate) level_reference: bool,
    pub(crate) level_turn: Quat,
    pub(crate) levelled_deg: f32,
    pub(crate) tracking: bool,
    pub(crate) recenter_pending: bool,
    pub(crate) dropped: bool,
    pub(crate) view: Mat3,
    pub(crate) relative: Quat,
    pub(crate) samples: u64,
    pub(crate) lost_events: u64,
    pub(crate) reanchors: u64,
}

impl Default for PoseState {
    fn default() -> Self {
        Self::new(100.0)
    }
}

impl PoseState {
    pub const REANCHOR_AFTER_MS: f64 = 500.0;
    pub const RESUME_SETTLE_MS: f64 = 250.0;
    pub const HINT_AFTER_MS: f64 = 1000.0;

    pub fn new(stale_ms: f64) -> Self {
        Self {
            stale_ms,
            center: None,
            up: [0.0, 0.0, 1.0],
            up_cov: [0.0; 9],
            last: Quat::default(),
            last_device_ms: -1.0,
            last_change_ms: 0.0,
            lost_at_ms: 0.0,
            settling_from_ms: -1.0,
            level_reference: false,
            level_turn: Quat::default(),
            levelled_deg: 0.0,
            tracking: false,
            recenter_pending: true,
            dropped: false,
            view: IDENTITY,
            relative: Quat::default(),
            samples: 0,
            lost_events: 0,
            reanchors: 0,
        }
    }

    pub fn update(&mut self, sample: Option<FeedSample>, now_ms: f64) {
        if let Some(sample) = sample
            && let Some(mut q) = quat_normalized(sample.orientation)
            && sample.device_ms != self.last_device_ms
        {
            if self.level_reference {
                if self.recenter_pending || self.center.is_none() {
                    let m = quat_to_mat3(q);
                    let half = (-m[1]).atan2(m[4]) / 2.0;
                    let turn = quat_mul(
                        quat_conj(q),
                        Quat {
                            x: 0.0,
                            y: 0.0,
                            z: half.sin(),
                            w: half.cos(),
                        },
                    );
                    self.level_turn = turn;
                    self.levelled_deg = quat_angle(Quat::default(), turn) * 180.0 / PI;
                }
                // C++ dereferences an empty optional here; a product of unit quaternions always normalises.
                q = quat_normalized(quat_mul(q, self.level_turn)).unwrap_or(q);
            }
            let since_ms = now_ms - self.last_change_ms;
            self.last_device_ms = sample.device_ms;
            self.last_change_ms = now_ms;
            self.samples += 1;
            if self.center.is_some()
                && !self.tracking
                && !self.dropped
                && since_ms > Self::REANCHOR_AFTER_MS
            {
                self.dropped = true;
            }
            if self.dropped {
                if self.settling_from_ms < 0.0 || since_ms > self.stale_ms {
                    self.settling_from_ms = now_ms;
                }
                if now_ms - self.settling_from_ms < Self::RESUME_SETTLE_MS {
                    return; // the view stays held
                }
                self.reanchor(q);
                self.dropped = false;
                self.settling_from_ms = -1.0;
                self.reanchors += 1;
            }
            if self.recenter_pending || self.center.is_none() {
                self.center = Some(q);
                self.recentre_up(q);
                self.recenter_pending = false;
            } else {
                self.learn_up(q, since_ms);
            }
            self.last = q;
            let relative = quat_mul(quat_conj(self.center.unwrap_or_default()), q);
            self.relative = relative;
            self.view = transpose(&driver_to_gl(&quat_to_mat3(relative)));
            self.tracking = true;
        }
        if self.tracking && now_ms - self.last_change_ms > self.stale_ms {
            self.tracking = false;
            self.lost_at_ms = now_ms;
            self.lost_events += 1;
        }
    }

    pub fn recenter(&mut self) {
        self.recenter_pending = true;
    }
    pub fn level_reference(&mut self, on: bool) {
        self.level_reference = on;
    }
    pub fn levelled_deg(&self) -> f32 {
        self.levelled_deg
    }
    pub fn level_turn(&self) -> Quat {
        self.level_turn
    }
    pub fn tracking(&self) -> bool {
        self.tracking
    }
    pub fn reconnecting(&self, now_ms: f64) -> bool {
        !self.tracking && self.center.is_some() && now_ms - self.lost_at_ms >= Self::HINT_AFTER_MS
    }
    pub fn view(&self) -> Mat3 {
        self.view
    }
    pub fn relative(&self) -> Quat {
        self.relative
    }
    pub fn reference(&self) -> Option<Quat> {
        let center = self.center?;
        let up = self.up;
        let turn = quat_normalized(Quat {
            x: up[1],
            y: -up[0],
            z: 0.0,
            w: 1.0 + up[2],
        })
        .unwrap_or(Quat {
            x: 1.0,
            y: 0.0,
            z: 0.0,
            w: 0.0,
        });
        Some(quat_mul(turn, center))
    }
    pub fn samples(&self) -> u64 {
        self.samples
    }
    pub fn lost_events(&self) -> u64 {
        self.lost_events
    }
    pub fn reanchors(&self) -> u64 {
        self.reanchors
    }

    fn reanchor(&mut self, head: Quat) {
        let frame = quat_mul(head, quat_conj(self.last));
        self.center = Some(quat_mul(frame, self.center.unwrap_or_default()));
        self.up = apply(&quat_to_mat3(frame), self.up);
        let loose = UP_HEAD_DEG * DEG_TO_RAD;
        for i in 0..3 {
            let c = self.up_cov[i * 4];
            let l = loose * loose;
            self.up_cov[i * 4] = if c < l { l } else { c };
        }
        self.last = head;
    }

    fn recentre_up(&mut self, head: Quat) {
        let m = quat_to_mat3(head);
        let reach = (UP_REACH_DEG * DEG_TO_RAD).cos();
        let started = self.up_cov[0] > 0.0;
        let up = self.up;
        if started && m[2] * up[0] + m[5] * up[1] + m[8] * up[2] >= reach {
            return;
        }
        let feed = m[8] >= reach;
        self.up = if feed {
            [0.0, 0.0, 1.0]
        } else {
            [m[2], m[5], m[8]]
        };
        let start = (if feed { UP_FEED_DEG } else { UP_HEAD_DEG }) * DEG_TO_RAD;
        let v = start * start;
        self.up_cov = [v, 0.0, 0.0, 0.0, v, 0.0, 0.0, 0.0, v];
    }

    fn learn_up(&mut self, head: Quat, ms: f64) {
        let m = quat_to_mat3(head);
        let up = self.up;
        if m[2] * up[0] + m[5] * up[1] + m[8] * up[2] < (UP_WORN_DEG * DEG_TO_RAD).cos() {
            return;
        }
        let left = [m[1], m[4], m[7]];
        let clamped = clamp_f64(ms, UP_MIN_STEP_MS, UP_MAX_STEP_MS);
        let seconds = clamped as f32 / 1000.0;
        let pl = apply(&self.up_cov, left);
        let s = left[0] * pl[0] + left[1] * pl[1] + left[2] * pl[2] + UP_NOISE / seconds;
        let off = (left[0] * up[0] + left[1] * up[1] + left[2] * up[2]) / s;
        let v = [
            up[0] - off * pl[0],
            up[1] - off * pl[1],
            up[2] - off * pl[2],
        ];
        let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        if n > 1e-6 {
            self.up = [v[0] / n, v[1] / n, v[2] / n];
        }
        for i in 0..3 {
            for j in 0..3 {
                self.up_cov[i * 3 + j] -=
                    pl[i] * pl[j] / s - if i == j { UP_DRIFT * seconds } else { 0.0 };
            }
        }
    }
}

/// T2 viewing modes (`xr::ViewMode`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
#[repr(u8)]
pub enum ViewMode {
    #[default]
    Anchor = 0,
    Locked = 1,
    Follow = 2,
}

/// How a mode change starts (`ModeController::Change`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
#[repr(u8)]
pub enum Change {
    #[default]
    Blend,
    Maximize,
    Restore,
}

/// `ModeController::Tuning`. follow_omega: the spring's natural frequency (rad/s).
#[derive(Clone, Copy, Debug, PartialEq)]
#[repr(C)]
pub struct Tuning {
    pub transition_ms: f32,
    pub follow_omega: f32,
}

impl Default for Tuning {
    fn default() -> Self {
        Self {
            transition_ms: 300.0,
            follow_omega: 8.0,
        }
    }
}

/// Where the canvas sits for each view mode, blended between them (`xr::ModeController`).
#[derive(Clone, Debug, PartialEq)]
pub struct ModeController {
    pub(crate) tuning: Tuning,
    pub(crate) mode: ViewMode,
    pub(crate) anchor: Quat,
    pub(crate) from: Quat,
    pub(crate) follow: Quat,
    pub(crate) pinned: Quat,
    pub(crate) held: Quat,
    pub(crate) aim: Quat,
    pub(crate) started_ms: f64,
    pub(crate) last_ms: f64,
    pub(crate) velocity: [f32; 3],
    pub(crate) transitioning: bool,
    pub(crate) holding: bool,
}

impl Default for ModeController {
    fn default() -> Self {
        Self::new(Tuning::default())
    }
}

impl ModeController {
    pub fn new(tuning: Tuning) -> Self {
        let i = Quat::default();
        Self {
            tuning,
            mode: ViewMode::Anchor,
            anchor: i,
            from: i,
            follow: i,
            pinned: i,
            held: i,
            aim: i,
            started_ms: -1.0,
            last_ms: -1.0,
            velocity: [0.0; 3],
            transitioning: false,
            holding: false,
        }
    }

    pub fn set_mode(&mut self, mode: ViewMode, now_ms: f64, how: Change) {
        if mode == self.mode {
            return;
        }
        self.holding = false;
        if how == Change::Maximize && self.mode == ViewMode::Anchor {
            self.held = self.anchor;
            self.holding = true;
            self.transitioning = false;
            self.velocity = [0.0; 3];
            self.mode = mode;
            return;
        }
        self.from = self.anchor;
        self.started_ms = now_ms;
        self.transitioning = true;
        if mode == ViewMode::Follow {
            self.follow = quat_mul(self.anchor, quat_conj(self.aim));
            self.velocity = [0.0; 3];
        }
        if mode == ViewMode::Anchor && how != Change::Restore {
            self.pinned = self.anchor;
        }
        self.mode = mode;
    }

    pub fn land_maximize(&mut self, now_ms: f64) {
        if !self.holding {
            return;
        }
        self.holding = false;
        self.from = self.anchor;
        self.started_ms = now_ms;
        self.transitioning = true;
        if self.mode == ViewMode::Follow {
            self.follow = quat_mul(self.anchor, quat_conj(self.aim));
            self.velocity = [0.0; 3];
        }
    }

    pub fn holding(&self) -> bool {
        self.holding
    }

    pub fn recenter(&mut self) {
        self.follow = Quat::default();
        self.anchor = self.aim;
        self.from = self.aim;
        self.pinned = self.aim;
        self.held = self.aim;
        self.transitioning = false;
        self.velocity = [0.0; 3];
    }

    pub fn set_aim(&mut self, aim: Quat) {
        self.aim = aim;
    }

    pub fn turn_anchor(&mut self, turn: Quat) {
        self.pinned = quat_normalized(quat_mul(self.pinned, turn)).unwrap_or(self.pinned);
        if self.mode == ViewMode::Anchor && !self.transitioning {
            self.anchor = self.pinned;
        }
    }

    pub fn aim(&self) -> Quat {
        self.aim
    }
    pub fn mode(&self) -> ViewMode {
        self.mode
    }
    pub fn anchor(&self) -> Quat {
        self.anchor
    }

    fn target(&self, head: Quat) -> Quat {
        if self.holding {
            return self.held;
        }
        match self.mode {
            ViewMode::Locked => quat_mul(head, self.aim),
            ViewMode::Follow => quat_mul(self.follow, self.aim),
            ViewMode::Anchor => self.pinned,
        }
    }

    pub fn update(&mut self, head: Quat, now_ms: f64) -> Quat {
        let dt = if self.last_ms < 0.0 {
            0.0
        } else {
            clamp_f64(now_ms - self.last_ms, 0.0, 100.0)
        };
        self.last_ms = now_ms;
        if self.mode == ViewMode::Follow && !self.holding && dt > 0.0 {
            let w = self.tuning.follow_omega;
            let mut left = dt;
            while left > 0.0 {
                let h = ((if 4.0 < left { 4.0 } else { left }) / 1000.0) as f32;
                left -= 4.0;
                let mut e = quat_mul(quat_conj(self.follow), head);
                if e.w < 0.0 {
                    e = Quat {
                        x: -e.x,
                        y: -e.y,
                        z: -e.z,
                        w: -e.w,
                    };
                }
                let sn = (e.x * e.x + e.y * e.y + e.z * e.z).sqrt();
                let angle = 2.0 * sn.atan2(e.w);
                let k = if sn > 1e-7 { angle / sn } else { 2.0 };
                let err = [e.x * k, e.y * k, e.z * k];
                for a in 0..3 {
                    self.velocity[a] += (w * w * err[a] - 2.0 * w * self.velocity[a]) * h;
                }
                let (vx, vy, vz) = (
                    self.velocity[0] * h,
                    self.velocity[1] * h,
                    self.velocity[2] * h,
                );
                let th = (vx * vx + vy * vy + vz * vz).sqrt();
                if th > 1e-9 {
                    let c = (th / 2.0).sin() / th;
                    self.follow = quat_normalized(quat_mul(
                        self.follow,
                        Quat {
                            x: vx * c,
                            y: vy * c,
                            z: vz * c,
                            w: (th / 2.0).cos(),
                        },
                    ))
                    .unwrap_or(self.follow);
                }
            }
        }
        let goal = self.target(head);
        if self.transitioning {
            let t = clamp_f64(
                (now_ms - self.started_ms) / f64::from(self.tuning.transition_ms),
                0.0,
                1.0,
            ) as f32;
            let s = t * t * (3.0 - 2.0 * t);
            self.anchor = quat_slerp(self.from, goal, s);
            if t >= 1.0 {
                self.transitioning = false;
            }
        } else {
            self.anchor = goal;
        }
        self.anchor
    }

    pub fn settled(&self) -> bool {
        let v = &self.velocity;
        let v = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        !self.transitioning && !self.holding && (self.mode != ViewMode::Follow || v < 0.01)
    }
}

/// std::clamp.
fn clamp_f64(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

#[derive(Clone, Copy, Default)]
struct Past {
    q: Quat,
    ns: u64,
    seq: u32,
}

const PAST_LEN: usize = 16;

/// How [`PoseFeed::read`] has gone (`xr::PoseReaderStats`). Times in ns, from the histograms; ages are read time minus
/// sample time, seqlock reads only.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PoseReaderStats {
    pub seqlock_attached: bool,
    /// The newest sample came from the channel (so last_age_ns is its age).
    pub seqlock_source: bool,
    /// Reads that returned a sample, by source.
    pub seqlock_reads: u64,
    pub mutex_reads: u64,
    /// Seqlock copies that met the writer mid-write and were made again (a spin, never a sleep).
    pub retries: u64,
    /// Mutex reads that found the lock held and blocked on it.
    pub waits: u64,
    /// The source changed between two samples.
    pub switches: u64,
    pub age_count: u64,
    pub age_p50_ns: u64,
    pub age_p95_ns: u64,
    pub age_p99_ns: u64,
    pub age_max_ns: u64,
    pub last_age_ns: u64,
    /// How long read() took, every call.
    pub read_p99_ns: u64,
    pub read_max_ns: u64,
}

/// `rt::LatencyHistogram` (spike/driver/include/luma/rt.hpp), the part PoseFeed uses: values below 64 have a bucket
/// each; above that every power of two is split into 32 buckets, and a percentile is the top of the bucket holding it.
/// The same as xr-luma-driver's, which the renderer does not depend on.
#[derive(Clone)]
struct LatencyHistogram {
    buckets: [u64; Self::BUCKETS],
    count: u64,
    max: u64,
}

impl LatencyHistogram {
    const LINEAR: usize = 64;
    const SUB: usize = 32;
    const BUCKETS: usize = Self::LINEAR + (64 - 6) * Self::SUB;

    fn new() -> Self {
        Self {
            buckets: [0; Self::BUCKETS],
            count: 0,
            max: 0,
        }
    }

    fn record(&mut self, ns: u64) {
        self.buckets[Self::index(ns)] += 1;
        self.count += 1;
        self.max = self.max.max(ns);
    }

    fn percentile(&self, p: f64) -> u64 {
        if self.count == 0 {
            return 0;
        }
        let target = ((self.count as f64 * p / 100.0).ceil() as u64).max(1);
        let mut seen = 0;
        for (i, n) in self.buckets.iter().enumerate() {
            seen += n;
            if seen >= target {
                return Self::bucket_top(i).min(self.max);
            }
        }
        self.max
    }

    fn index(v: u64) -> usize {
        if v < Self::LINEAR as u64 {
            return v as usize;
        }
        let msb = 63 - v.leading_zeros() as usize;
        Self::LINEAR + (msb - 6) * Self::SUB + ((v >> (msb - 5)) as usize - Self::SUB)
    }

    fn bucket_top(i: usize) -> u64 {
        if i < Self::LINEAR {
            return i as u64;
        }
        let octave = (i - Self::LINEAR) / Self::SUB;
        let sub = (i - Self::LINEAR) % Self::SUB;
        let msb = octave + 6;
        (((sub + Self::SUB) as u64 + 1) << (msb - 5)).wrapping_sub(1)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Source {
    None,
    Seqlock,
    Mutex,
}

/// The pose feed (`xr::PoseFeed`): the seqlock channel when it holds a fresh calibrated pose, the SysV mutex feed
/// otherwise. While the mutex feed's writer holds its lock (or stopped holding it) the newest sample of either source is
/// served again, its device time unchanged, so it ages and tracking is reported lost after [`PoseState`]'s stale_ms.
pub struct PoseFeed {
    feed: xr_pose::feed::FeedReader,
    /// Newest valid sample read: served again while the driver holds the lock.
    last: Option<FeedSample>,
    seqlock_name: String,
    seqlock: Option<xr_pose::seqlock::PoseReader>,
    seqlock_retry_ns: u64,
    seqlock_good_ns: u64,
    past: [Past; PAST_LEN],
    past_n: usize,
    past_head: usize,
    epoch_ns: u64,
    source: Source,
    counts: PoseReaderStats,
    age: LatencyHistogram,
    read_time: LatencyHistogram,
}

pub fn monotonic_ns() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    (ts.tv_sec as u64)
        .wrapping_mul(1_000_000_000)
        .wrapping_add(ts.tv_nsec as u64)
}

impl PoseFeed {
    pub const SEQLOCK_FRESH_MS: f64 = 100.0;
    pub const SEQLOCK_HISTORY_MS: f64 = 16.0;
    pub const SEQLOCK_POSE_NAME: &str = xr_pose::seqlock::POSE_SHM_NAME;

    /// `why` on failure. `seqlock_name` empty: no seqlock channel; one absent is looked for again once a second.
    pub fn open(prefix: &str, seqlock_name: &str) -> Result<Self, String> {
        let feed = xr_pose::feed::FeedReader::open(prefix).map_err(|e| e.0)?;
        Ok(Self {
            feed,
            last: None,
            seqlock_name: seqlock_name.to_owned(),
            seqlock: None,
            seqlock_retry_ns: 0,
            seqlock_good_ns: 0,
            past: [Past::default(); PAST_LEN],
            past_n: 0,
            past_head: 0,
            epoch_ns: 0,
            source: Source::None,
            counts: PoseReaderStats::default(),
            age: LatencyHistogram::new(),
            read_time: LatencyHistogram::new(),
        })
    }

    pub fn display_fov(&self) -> Option<f32> {
        self.feed.display_fov()
    }

    pub fn look_ahead_cfg(&self) -> Option<[f32; 4]> {
        self.feed.look_ahead_cfg()
    }

    /// When the glasses' panel scans out, from the Luma driver's pose_vsync segment (written with --read-events;
    /// XRLinuxDriver has none). `None`: no segment, not locked, mid-write on every try, or a period outside 4..50 ms.
    pub fn vsync(&self) -> Option<crate::predict::VsyncSnapshot> {
        let v = self.feed.vsync_words()?;
        let (magic, v) = (v[0], &v[1..]);
        if magic != 0x3153_564c || v[8] & 1 == 0 {
            return None; // "LVS1"; not locked
        }
        let u64_at = |i: usize| u64::from(v[i]) | u64::from(v[i + 1]) << 32;
        let out = crate::predict::VsyncSnapshot {
            anchor_host_ns: u64_at(0),
            period_ns: f64::from_bits(u64_at(2)),
            anchor_feed_ms: f64::from_bits(u64_at(4)),
            events: u64_at(6),
            resets: v[9],
            fit_rms_us: f32::from_bits(v[10]),
        };
        (out.period_ns >= 4e6 && out.period_ns <= 50e6).then_some(out)
    }

    pub fn stats(&self) -> PoseReaderStats {
        let mut r = self.counts;
        r.waits = self.feed.waits();
        r.seqlock_attached = self.seqlock.is_some();
        r.seqlock_source = self.source == Source::Seqlock;
        r.age_count = self.age.count;
        r.age_p50_ns = self.age.percentile(50.0);
        r.age_p95_ns = self.age.percentile(95.0);
        r.age_p99_ns = self.age.percentile(99.0);
        r.age_max_ns = self.age.max;
        r.read_p99_ns = self.read_time.percentile(99.0);
        r.read_max_ns = self.read_time.max;
        r
    }

    /// The newest sample. `None` while disabled (and the served-again sample is forgotten), for an invalid sample, or
    /// when the lock is not recoverable.
    pub fn read(&mut self) -> Option<FeedSample> {
        let start = monotonic_ns();
        let mut out = None;
        if self.feed.disabled() {
            self.last = None;
            self.past_n = 0;
            self.source = Source::None;
        } else {
            out = self.read_seqlock(start);
            if out.is_some() {
                if self.source == Source::Mutex {
                    self.counts.switches += 1;
                }
                self.source = Source::Seqlock;
                self.counts.seqlock_reads += 1;
                self.last = out;
            } else {
                out = self.read_mutex();
                if out.is_some() {
                    if self.source == Source::Seqlock {
                        self.counts.switches += 1;
                    }
                    self.source = Source::Mutex;
                    self.counts.mutex_reads += 1;
                } else {
                    self.source = Source::None;
                }
            }
        }
        self.read_time.record(monotonic_ns().wrapping_sub(start));
        out
    }

    fn read_mutex(&mut self) -> Option<FeedSample> {
        use xr_pose::feed::MutexRead;
        let s = match self.feed.read_mutex() {
            MutexRead::Sample(s) => s,
            MutexRead::Again => return self.last,
            MutexRead::Nothing => return None,
        };
        let q = |v: [f32; 4]| Quat {
            x: v[0],
            y: v[1],
            z: v[2],
            w: v[3],
        };
        self.last = Some(FeedSample {
            orientation: q(s.orientation),
            device_ms: s.device_ms,
            older: [q(s.older[0]), q(s.older[1])],
            older_ms: s.older_ms,
            sample_ns: 0,
        });
        self.last
    }

    fn read_seqlock(&mut self, mut now: u64) -> Option<FeedSample> {
        if self.seqlock_name.is_empty() {
            return None;
        }
        if self.seqlock.is_none() {
            if now < self.seqlock_retry_ns {
                return None;
            }
            self.seqlock_retry_ns = now.wrapping_add(1_000_000_000);
            self.seqlock = Some(xr_pose::seqlock::PoseReader::open(&self.seqlock_name).ok()?);
            self.seqlock_good_ns = now;
        }
        const FRESH_NS: u64 = (PoseFeed::SEQLOCK_FRESH_MS * 1e6) as u64;
        const FUTURE_NS: u64 = 1_000_000;
        // Still mid-record after 4,096 tries: a writer preempted while writing, or dead. The mutex feed serves this read.
        let (r, retries) = self
            .seqlock
            .as_ref()
            .map_or((None, 0), |s| s.read_counted());
        self.counts.retries += u64::from(retries);
        let none = |me: &mut Self, now: u64| {
            if now >= me.seqlock_good_ns.wrapping_add(FRESH_NS) && now >= me.seqlock_retry_ns {
                me.seqlock = None;
            }
            None
        };
        let Some(r) = r.filter(|r| r.flags & xr_pose::seqlock::POSE_CALIBRATED != 0) else {
            return none(self, now);
        };
        now = monotonic_ns(); // after the load: a reader preempted before it must not take the record for one from the future
        let q = quat_normalized(Quat {
            x: r.x,
            y: r.y,
            z: r.z,
            w: r.w,
        });
        let t = r.t_raw_ns.wrapping_add(r.raw_to_mono_ns as u64);
        let Some(q) =
            q.filter(|_| !(t > now.wrapping_add(FUTURE_NS) || now > t.wrapping_add(FRESH_NS)))
        else {
            return none(self, now);
        };
        self.seqlock_good_ns = now;
        let head = self.past[self.past_head];
        if self.past_n == 0 || r.seq != head.seq || t != head.ns {
            if self.past_n != 0 && t < head.ns {
                self.past_n = 0; // a new writer on an older clock: no history across it
            }
            self.past_head = (self.past_head + 1) % PAST_LEN;
            self.past[self.past_head] = Past {
                q,
                ns: t,
                seq: r.seq,
            };
            self.past_n = (self.past_n + 1).min(PAST_LEN);
        }
        const EPOCH_SPAN_NS: u64 = ((1u64 << 24) - 1000) * 1_000_000;
        if self.epoch_ns == 0 || t < self.epoch_ns || t - self.epoch_ns >= EPOCH_SPAN_NS {
            self.epoch_ns = t;
        }
        let epoch = self.epoch_ns;
        let ms = |ns: u64| ((ns - ns.min(epoch)) as f64 / 1e6) as f32;
        let mut f = FeedSample {
            orientation: q,
            device_ms: ms(t),
            older: [q, q],
            older_ms: [ms(t), ms(t)],
            sample_ns: t,
        };
        const HISTORY_NS: u64 = (PoseFeed::SEQLOCK_HISTORY_MS * 1e6) as u64;
        for i in 1..self.past_n {
            let p = self.past[(self.past_head + PAST_LEN - i) % PAST_LEN];
            if t.wrapping_sub(p.ns) >= HISTORY_NS {
                if p.ns >= epoch {
                    f.older = [p.q, p.q];
                    f.older_ms = [ms(p.ns), ms(p.ns)];
                }
                break;
            }
        }
        let age_ns = if now > t { now - t } else { 0 };
        self.age.record(age_ns);
        self.counts.last_age_ns = age_ns;
        Some(f)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mat3_round_trips_a_quat() {
        let q = quat_normalized(Quat {
            x: 0.1,
            y: -0.3,
            z: 0.2,
            w: 0.9,
        })
        .unwrap();
        let back = mat3_to_quat(&quat_to_mat3(q));
        assert!(quat_angle(q, back) < 1e-3);
    }

    #[test]
    fn aim_rotation_of_zero_is_identity() {
        assert_eq!(aim_rotation(0.0, 0.0), Quat::default());
        assert_eq!(view_from(Quat::default(), Quat::default()), IDENTITY);
    }
}
