//! Twin of `spike/tests/pose_test.cpp` and `spike/tests/level_reference_test.cpp`: the pose maths, `PoseState` (with
//! the USB drop of 2026-11-04 and the level reference), the view modes, and `PoseFeed` on a private SysV feed and on a
//! seqlock channel of the test's own.

use std::f32::consts::PI;
use std::ffi::CString;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::time::{Duration, Instant};
use xr_pose::feed::{FeedError, FeedWriter, WriterOptions};
use xr_pose::seqlock::{POSE_CALIBRATED, PoseRecord, PoseWriter};
use xr_renderer::pose::*;

fn near(a: f32, b: f32, eps: f32) -> bool {
    (a - b).abs() <= eps
}

fn axis_angle(x: f32, y: f32, z: f32, deg: f32) -> Quat {
    let h = deg * PI / 360.0;
    let s = h.sin();
    Quat {
        x: x * s,
        y: y * s,
        z: z * s,
        w: h.cos(),
    }
}

fn sample(q: Quat, device_ms: f32) -> Option<FeedSample> {
    Some(FeedSample {
        orientation: q,
        device_ms,
        ..FeedSample::default()
    })
}

fn half_fov() -> (f32, f32) {
    let hw = (23.0f32 * PI / 180.0).tan();
    (hw, hw * 1200.0 / 1920.0)
}

const NO_TURN: Quat = Quat {
    x: 0.0,
    y: 0.0,
    z: 0.0,
    w: 1.0,
};

#[test]
fn quaternion_basics() {
    let q = axis_angle(0.0, 0.0, 1.0, 90.0);
    let r = quat_mul(q, quat_conj(q));
    assert!(near(r.w, 1.0, 1e-4) && near(r.x, 0.0, 1e-4) && near(r.z, 0.0, 1e-4));
    assert!(
        quat_normalized(Quat {
            x: f32::NAN,
            y: 0.0,
            z: 0.0,
            w: 1.0
        })
        .is_none()
    );
    assert!(
        quat_normalized(Quat {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            w: 0.0
        })
        .is_none()
    );
    assert!(near(
        quat_normalized(Quat {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            w: 2.0
        })
        .unwrap()
        .w,
        1.0,
        1e-4
    ));
}

#[test]
fn identity_view_reproduces_head_locked_ndc() {
    let (hw, hh) = half_fov();
    for (x, y) in [(0.0, 0.0), (0.5, -0.25), (-1.0, 1.0)] {
        let p = project(&IDENTITY, x, y, hw, hh).unwrap();
        assert!(near(p[0], x, 1e-4) && near(p[1], y, 1e-4));
    }
}

#[test]
fn pose_state_follows_recentres_and_holds_a_stale_view() {
    let (hw, hh) = half_fov();
    // Driver yaw (about its z/up axis) to the left: world-anchored content moves right in view.
    let mut s = PoseState::default();
    s.update(sample(NO_TURN, 1.0), 0.0); // first sample becomes the centre
    assert!(s.tracking() && s.samples() == 1);
    s.update(sample(axis_angle(0.0, 0.0, 1.0, 10.0), 2.0), 5.0); // head turns 10 deg left (+z, driver frame)
    let centre = project(&s.view(), 0.0, 0.0, hw, hh).unwrap();
    assert!(near(centre[0], (10.0f32 * PI / 180.0).tan() / hw, 1e-3) && near(centre[1], 0.0, 1e-3));
    // Head pitches up (driver -y): content moves down.
    let mut pitch = PoseState::default();
    pitch.update(sample(NO_TURN, 1.0), 0.0);
    pitch.update(sample(axis_angle(0.0, -1.0, 0.0, 8.0), 2.0), 5.0);
    let c2 = project(&pitch.view(), 0.0, 0.0, hw, hh).unwrap();
    assert!(c2[1] < -0.1 && near(c2[0], 0.0, 1e-3));
    // Content behind the viewer is not drawn.
    let mut back = PoseState::default();
    back.update(sample(NO_TURN, 1.0), 0.0);
    back.update(sample(axis_angle(0.0, 0.0, 1.0, 180.0), 2.0), 5.0);
    assert!(project(&back.view(), 0.0, 0.0, hw, hh).is_none());

    // Recenter: the current pose becomes the new centre.
    s.recenter();
    s.update(sample(axis_angle(0.0, 0.0, 1.0, 10.0), 3.0), 10.0);
    let recentred = project(&s.view(), 0.2, 0.1, hw, hh).unwrap();
    assert!(near(recentred[0], 0.2, 1e-3) && near(recentred[1], 0.1, 1e-3));
    // Staleness: no new device timestamp for > stale_ms -> lost, last pose held, counted once.
    let held = s.view();
    s.update(sample(axis_angle(0.0, 0.0, 1.0, 10.0), 3.0), 50.0);
    assert!(s.tracking());
    s.update(sample(axis_angle(0.0, 0.0, 1.0, 10.0), 3.0), 200.0);
    assert!(!s.tracking() && s.lost_events() == 1 && s.view() == held);
    s.update(None, 300.0);
    assert!(!s.tracking() && s.lost_events() == 1);
    s.update(sample(axis_angle(0.0, 0.0, 1.0, 10.0), 4.0), 310.0);
    assert!(s.tracking());
    s.update(
        sample(
            Quat {
                x: f32::NAN,
                y: 0.0,
                z: 0.0,
                w: 1.0,
            },
            5.0,
        ),
        320.0,
    ); // invalid sample ignored
    assert!(s.tracking() && s.view() == held);
}

/// A USB drop as the incident of 2026-11-04 00:57:58 had it, against a simulated 120 Hz renderer. The driver
/// publishes nothing (disabled, then a zeroed feed) for about 20 s with two disconnects, a few fresh samples get
/// through between them, and the feed comes back calibrated on a new reference: its timestamps restart and its frame
/// is not the old one.
mod drop {
    use super::*;

    pub const FRAME_MS: f64 = 1000.0 / 120.0;

    /// The rotation between two views.
    pub fn view_change_deg(a: &Mat3, b: &Mat3) -> f32 {
        let trace = a[0] * b[0]
            + a[3] * b[3]
            + a[6] * b[6]
            + a[1] * b[1]
            + a[4] * b[4]
            + a[7] * b[7]
            + a[2] * b[2]
            + a[5] * b[5]
            + a[8] * b[8];
        ((trace - 1.0) / 2.0).clamp(-1.0, 1.0).acos() * 180.0 / PI
    }

    pub struct Sim {
        pub s: PoseState,
        pub now: f64,
        pub device: f32,
    }

    impl Sim {
        pub fn new() -> Self {
            Sim {
                s: PoseState::default(),
                now: 0.0,
                device: 1.0,
            }
        }
        /// Fresh samples.
        pub fn live(&mut self, q: Quat, ms: f64) {
            let end = self.now + ms;
            while self.now < end {
                self.s.update(sample(q, self.device), self.now);
                self.device += 8.0;
                self.now += FRAME_MS;
            }
        }
        /// The driver disabled, or its feed zeroed: PoseFeed::read gives nothing.
        pub fn silent(&mut self, ms: f64) {
            let end = self.now + ms;
            while self.now < end {
                self.s.update(None, self.now);
                self.now += FRAME_MS;
            }
        }
    }

    /// The head before the drop, feed frame A.
    pub fn worn() -> Quat {
        quat_mul(
            axis_angle(0.0, 0.0, 1.0, 40.0),
            axis_angle(0.0, 1.0, 0.0, 12.0),
        )
    }

    /// The feed's reference after it: unrelated.
    pub fn new_frame() -> Quat {
        axis_angle(1.0, 0.0, 0.0, 70.0)
    }
}

use drop::{Sim, view_change_deg};

#[test]
fn drop_twenty_seconds_two_disconnects_holds_then_carries_on_without_a_jump() {
    let mut d = Sim::new();
    d.live(NO_TURN, 500.0); // the recentre reference
    d.live(drop::worn(), 2000.0);
    let held = d.s.view();
    assert!(d.s.tracking());
    d.silent(4000.0); // disconnect 1
    assert!(!d.s.tracking() && d.s.lost_events() == 1);
    assert!(view_change_deg(&d.s.view(), &held) < 1e-3);
    assert!(d.s.reconnecting(d.now) && d.s.reanchors() == 0);
    d.device = 1.0; // the driver reconnected: its clock starts again
    d.live(drop::new_frame(), 100.0); // a few samples between the disconnects are no recovery
    assert!(!d.s.tracking());
    assert!(view_change_deg(&d.s.view(), &held) < 1e-3);
    d.silent(6000.0); // disconnect 2
    d.silent(9900.0);
    assert!(!d.s.tracking() && d.s.lost_events() == 1);
    d.device = 1.0;
    let mut worst = 0.0f32;
    for _ in 0..90 {
        // calibrated again, the head still; it must not move the picture at any frame
        d.s.update(sample(drop::new_frame(), d.device), d.now);
        d.device += 8.0;
        d.now += drop::FRAME_MS;
        worst = worst.max(view_change_deg(&d.s.view(), &held));
    }
    assert!(
        d.s.tracking()
            && d.s.lost_events() == 1
            && d.s.reanchors() == 1
            && !d.s.reconnecting(d.now)
    );
    assert!(worst < 0.01);
    // And it tracks again: ten degrees of head are ten degrees of view, the world anchored where it was left.
    d.live(
        quat_mul(drop::new_frame(), axis_angle(0.0, 0.0, 1.0, 10.0)),
        100.0,
    );
    assert!(near(view_change_deg(&d.s.view(), &held), 10.0, 0.05));
}

#[test]
fn drop_keeps_the_level_across_the_change_of_frame() {
    let mut d = Sim::new();
    d.live(NO_TURN, 500.0);
    d.live(axis_angle(0.0, 0.0, 1.0, 25.0), 2000.0);
    let before = canvas_up(d.s.reference().unwrap(), NO_TURN);
    d.silent(5000.0);
    d.device = 1.0;
    d.live(drop::new_frame(), 1000.0);
    let after = canvas_up(d.s.reference().unwrap(), NO_TURN);
    assert!(
        near(before[0], after[0], 0.02)
            && near(before[1], after[1], 0.02)
            && near(before[2], after[2], 0.02)
    );
}

#[test]
fn drop_keeps_a_recentre_pressed_during_it() {
    let mut d = Sim::new();
    d.live(NO_TURN, 500.0);
    d.live(drop::worn(), 1000.0);
    d.silent(3000.0);
    d.s.recenter();
    d.silent(3000.0);
    d.device = 1.0;
    d.live(drop::new_frame(), 1000.0);
    assert!(d.s.tracking());
    assert!(view_change_deg(&d.s.view(), &IDENTITY) < 1e-2);
}

#[test]
fn drop_a_hiccup_is_believed_again_at_once() {
    let mut d = Sim::new();
    d.live(NO_TURN, 500.0);
    d.silent(200.0);
    assert!(!d.s.tracking() && !d.s.reconnecting(d.now)); // too short to mention
    let turned = axis_angle(0.0, 0.0, 1.0, 5.0);
    d.s.update(sample(turned, d.device), d.now);
    assert!(d.s.tracking());
    assert!(near(view_change_deg(&d.s.view(), &IDENTITY), 5.0, 0.05));
}

#[test]
fn drop_hint_waits_a_second_and_needs_tracking_first() {
    let mut never = PoseState::default();
    never.update(None, 60000.0);
    assert!(!never.reconnecting(60000.0));
    let mut d = Sim::new();
    d.live(NO_TURN, 500.0);
    let last = d.now;
    d.silent(1000.0);
    assert!(!d.s.reconnecting(d.now));
    d.silent(200.0);
    assert!(d.s.reconnecting(d.now) && d.now - last > PoseState::HINT_AFTER_MS);
}

#[test]
fn drop_a_feed_that_never_comes_back_keeps_the_view() {
    let mut d = Sim::new();
    d.live(drop::worn(), 500.0);
    let held = d.s.view();
    d.silent(90000.0);
    assert!(!d.s.tracking() && view_change_deg(&d.s.view(), &held) < 1e-3);
}

/// level_reference_test.cpp: the head turns about gravity; the glasses' frame starts pitched against it, so in that
/// frame the same turn has a roll in it. Feed frame: x forward, y left, z up, gravity level.
mod lean {
    use super::*;

    const RAD: f32 = PI / 180.0;

    pub fn about(x: f32, y: f32, z: f32, deg: f32) -> Quat {
        let h = deg * RAD / 2.0;
        let s = h.sin();
        Quat {
            x: x * s,
            y: y * s,
            z: z * s,
            w: h.cos(),
        }
    }

    pub struct Shown {
        pub yaw_deg: f32,
        pub roll_deg: f32,
    }

    /// spike/tests/view_lean.hpp: where the view's centre ray points, and the roll of a short horizontal stroke there.
    pub fn shown_by(view: &Mat3) -> Shown {
        let k_deg = (180.0 / std::f64::consts::PI) as f32;
        let ray = apply(&transpose(view), [0.0, 0.0, -1.0]);
        if ray[2] > -1e-4 {
            return Shown {
                yaw_deg: f32::NAN,
                roll_deg: f32::NAN,
            };
        }
        let at = [ray[0] / -ray[2], ray[1] / -ray[2], -1.0];
        let beside = [at[0] + 0.01, at[1], -1.0];
        let (a, b) = (apply(view, at), apply(view, beside));
        let dx = b[0] / -b[2] - a[0] / -a[2];
        let dy = b[1] / -b[2] - a[1] / -a[2];
        Shown {
            yaw_deg: ray[0].atan2(-ray[2]) * k_deg,
            roll_deg: dy.atan2(dx) * k_deg,
        }
    }

    pub struct Run {
        pub pose: PoseState,
        pub t: f32,
    }

    impl Run {
        pub fn new(level: bool) -> Self {
            let mut pose = PoseState::default();
            pose.level_reference(level);
            Run { pose, t: 0.0 }
        }
        pub fn shown(&mut self, head: Quat) -> Shown {
            self.t += 16.0;
            self.pose.update(sample(head, self.t), f64::from(self.t));
            shown_by(&view_from(self.pose.relative(), Quat::default()))
        }
    }

    /// Head turned `yaw` degrees to the right about gravity, from a start `pitch` degrees below level.
    pub fn head(pitch: f32, yaw: f32) -> Quat {
        quat_mul(about(0.0, 0.0, 1.0, -yaw), about(0.0, 1.0, 0.0, pitch))
    }
}

#[test]
fn unlevelled_turns_lean_against_the_turn() {
    use lean::*;
    let mut r = Run::new(false);
    r.shown(head(19.5, 0.0));
    let (right, left) = (r.shown(head(19.5, 20.0)), r.shown(head(19.5, -20.0)));
    println!(
        "unlevelled, 19.5 degree start: right {:.2} yaw {:.2} roll, left {:.2} yaw {:.2} roll",
        right.yaw_deg, right.roll_deg, left.yaw_deg, left.roll_deg
    );
    assert!(right.yaw_deg > 15.0 && left.yaw_deg < -15.0);
    assert!(right.roll_deg > 5.0 && left.roll_deg < -5.0); // right leans left, left leans right
    assert!((right.roll_deg + left.roll_deg).abs() < 0.1);
}

#[test]
fn levelled_turns_keep_their_yaw_without_roll() {
    use lean::*;
    for pitch in [19.5f32, 27.0, 8.0, -10.0] {
        let mut r = Run::new(true);
        let still = r.shown(head(pitch, 0.0));
        assert!(still.roll_deg.abs() < 0.01 && still.yaw_deg.abs() < 0.01); // nothing moves at the start
        for yaw in [5.0f32, 10.0, 20.0, 30.0, -5.0, -10.0, -20.0, -30.0] {
            let s = r.shown(head(pitch, yaw));
            assert!((s.yaw_deg - yaw).abs() < 0.05); // the turn is all still there
            assert!(s.roll_deg.abs() < 0.05);
        }
    }
}

#[test]
fn a_recentre_levels_again_at_the_new_pose() {
    use lean::*;
    let mut r = Run::new(true);
    r.shown(head(10.0, 0.0));
    r.pose.recenter();
    r.shown(head(25.0, 40.0));
    let s = r.shown(head(25.0, 55.0));
    assert!((s.yaw_deg - 15.0).abs() < 0.05 && s.roll_deg.abs() < 0.05);
}

#[test]
fn levelled_pitch_and_roll_still_show() {
    use lean::*;
    let mut r = Run::new(true);
    r.shown(head(19.5, 0.0));
    let up = r.shown(head(9.5, 0.0));
    assert!(up.roll_deg.abs() < 0.05);
    let tipped = quat_mul(about(1.0, 0.0, 0.0, 10.0), head(0.0, 0.0)); // ten degrees of roll about level forward
    let mut s = Run::new(true);
    s.shown(head(0.0, 0.0));
    assert!((s.shown(tipped).roll_deg.abs() - 10.0).abs() < 0.1);
}

#[test]
fn level_reference_off_is_the_plain_path_bit_for_bit() {
    use lean::*;
    let mut on = Run::new(false);
    let mut plain = PoseState::default();
    for (q, t) in [(head(19.5, 0.0), 16.0f32), (head(19.5, 12.0), 32.0)] {
        on.pose.update(sample(q, t), f64::from(t));
        plain.update(sample(q, t), f64::from(t));
    }
    let (x, y) = (on.pose.relative(), plain.relative());
    assert!(x.x.to_bits() == y.x.to_bits() && x.y.to_bits() == y.y.to_bits());
    assert!(x.z.to_bits() == y.z.to_bits() && x.w.to_bits() == y.w.to_bits());
    assert!(on.pose.levelled_deg() == 0.0 && on.pose.level_turn() == Quat::default());
}

#[test]
fn level_reference_records_what_it_took_out() {
    use lean::*;
    let mut r = Run::new(true);
    r.shown(about(1.0, 0.0, 0.0, 10.0)); // ten degrees of roll, nothing else
    assert!(near(r.pose.levelled_deg(), 10.0, 0.05));
    assert!(near(
        quat_angle(Quat::default(), r.pose.level_turn()) * 180.0 / PI,
        r.pose.levelled_deg(),
        1e-4
    ));
}

/// The angle between orientations in degrees.
fn deg(a: Quat, b: Quat) -> f32 {
    quat_angle(a, b) * 180.0 / PI
}

#[test]
fn level_drops_roll_and_view_from_head_is_identity() {
    let head = quat_mul(
        axis_angle(0.0, 0.0, 1.0, 30.0),
        axis_angle(1.0, 0.0, 0.0, 20.0),
    ); // yaw 30, roll 20
    // level() drops roll, keeps where the forward axis points.
    let f1 = apply(&quat_to_mat3(level(head)), [1.0, 0.0, 0.0]);
    let f2 = apply(&quat_to_mat3(head), [1.0, 0.0, 0.0]);
    assert!(near(f1[0], f2[0], 1e-4) && near(f1[1], f2[1], 1e-4) && near(f1[2], f2[2], 1e-4));
    assert!(near(
        apply(&quat_to_mat3(level(head)), [0.0, 1.0, 0.0])[2],
        0.0,
        1e-4
    )); // left axis stays horizontal
    // view_from(head, head) is identity (0DoF).
    let id = view_from(head, head);
    for (i, v) in id.iter().enumerate() {
        assert!(near(*v, if i % 4 == 0 { 1.0 } else { 0.0 }, 1e-5));
    }
}

#[test]
fn view_modes_blend_lock_follow_and_pin() {
    let head = quat_mul(
        axis_angle(0.0, 0.0, 1.0, 30.0),
        axis_angle(1.0, 0.0, 0.0, 20.0),
    );
    let mut m = ModeController::default();
    let mut t = 0.0f64;
    let mut at = |dt: f64| {
        t += dt;
        t
    };
    assert!(deg(m.update(head, at(0.0)), Quat::default()) < 1e-3); // anchor: world-locked
    m.set_mode(ViewMode::Locked, at(0.0), Change::Blend);
    let mid = m.update(head, at(150.0)); // halfway: between, no jump
    assert!(deg(mid, Quat::default()) > 5.0 && deg(mid, head) > 5.0);
    assert!(deg(m.update(head, at(200.0)), head) < 1e-3 && m.settled()); // 0DoF steady state is exact
    let moved = axis_angle(0.0, 0.0, 1.0, -40.0);
    assert!(deg(m.update(moved, at(8.0)), moved) < 1e-3); // rigidly locked, no lag
    m.set_mode(ViewMode::Follow, at(0.0), Change::Blend); // starts where the canvas is
    assert!(deg(m.update(moved, at(1.0)), moved) < 2.0);
    for _ in 0..200 {
        m.update(moved, at(8.0));
    }
    assert!(deg(m.update(moved, at(8.0)), moved) < 0.05 && m.settled()); // converges onto the head
    // No dead zone: even a 2 degree turn is followed (smoothly, all the way).
    let small = axis_angle(0.0, 0.0, 1.0, -40.0 + 2.0);
    for _ in 0..200 {
        m.update(small, at(8.0));
    }
    assert!(deg(m.update(small, at(8.0)), small) < 0.05);
    // A 30 degree yaw + 15 degree roll step: accelerates from rest, then decelerates into centre; monotonic
    // (critically damped: no overshoot or bounce), roll included.
    let far = quat_mul(
        axis_angle(0.0, 0.0, 1.0, -10.0),
        axis_angle(1.0, 0.0, 0.0, 15.0),
    );
    let before = m.update(small, at(8.0));
    let start = deg(before, far);
    assert!(deg(m.update(far, at(8.0)), before) < 0.2); // gentle start (accelerating)
    let (mut last, mut speed_peak, mut prev_step, mut at_100, mut at_500) =
        (start, 0.0f32, 0.0f32, 0.0f32, 0.0f32);
    let mut peak_frame = -1;
    for i in 0..150 {
        let now = deg(m.update(far, at(8.0)), far);
        assert!(now <= last + 1e-3); // never overshoots
        let step = last - now;
        if step > speed_peak {
            speed_peak = step;
            peak_frame = i;
        }
        if i == 11 {
            at_100 = 1.0 - now / start;
        }
        if i == 61 {
            at_500 = 1.0 - now / start;
        }
        prev_step = step;
        last = now;
    }
    assert!(peak_frame > 3 && peak_frame < 30); // speeds up first, then slows down
    assert!(prev_step < speed_peak * 0.05); // decelerates smoothly into centre
    assert!(at_100 > 0.1 && at_100 < 0.35 && at_500 > 0.85); // responsive but smooth
    assert!(last < 0.05 && m.settled());
    // Follow -> anchor pins the canvas where it is now, not at the old anchor position.
    let here = m.update(far, at(8.0));
    m.set_mode(ViewMode::Anchor, at(0.0), Change::Blend);
    let away = axis_angle(0.0, 0.0, 1.0, 35.0);
    for _ in 0..60 {
        assert!(deg(m.update(away, at(8.0)), here) < 1e-3);
    }
    assert!(m.settled());
    // 0DoF -> anchor likewise pins at the head pose at the moment of the switch.
    m.set_mode(ViewMode::Locked, at(0.0), Change::Blend);
    for _ in 0..60 {
        m.update(away, at(8.0));
    }
    m.set_mode(ViewMode::Anchor, at(0.0), Change::Blend);
    for _ in 0..60 {
        assert!(deg(m.update(far, at(8.0)), away) < 1e-3);
    }
    // A recentre while anchored brings the canvas straight ahead.
    m.recenter();
    assert!(deg(m.update(Quat::default(), at(8.0)), Quat::default()) < 1e-3);
    m.set_mode(ViewMode::Follow, at(0.0), Change::Blend);
    for _ in 0..200 {
        m.update(far, at(8.0));
    }
    m.recenter();
    m.set_mode(ViewMode::Anchor, at(0.0), Change::Blend);
    for _ in 0..60 {
        m.update(far, at(8.0));
    }
    assert!(deg(m.update(far, at(8.0)), Quat::default()) < 1e-3);
}

#[test]
fn a_maximize_holds_the_room_until_it_lands() {
    let room = axis_angle(0.0, 0.0, 1.0, 20.0);
    let head = axis_angle(0.0, 0.0, 1.0, -15.0);
    let mut m = ModeController::default();
    m.turn_anchor(room);
    let mut t = 0.0;
    for _ in 0..10 {
        t += 8.0;
        m.update(head, t);
    }
    assert!(m.settled() && m.mode() == ViewMode::Anchor && quat_angle(m.anchor(), room) < 1e-6);
    m.set_mode(ViewMode::Follow, t, Change::Maximize);
    assert!(m.holding() && !m.settled() && m.mode() == ViewMode::Follow);
    for _ in 0..30 {
        t += 8.0;
        assert!(quat_angle(m.update(head, t), room) < 1e-5); // in place while it zooms in
    }
    m.land_maximize(t);
    assert!(!m.holding());
    m.land_maximize(t); // only once
    for _ in 0..400 {
        t += 8.0;
        m.update(head, t);
    }
    assert!(m.settled() && deg(m.update(head, t + 8.0), head) < 0.05);
    // Restore: back to the room exactly, the pinned anchor untouched by the maximize.
    m.set_mode(ViewMode::Anchor, t, Change::Restore);
    for _ in 0..60 {
        t += 8.0;
        m.update(head, t);
    }
    assert!(quat_angle(m.update(head, t + 8.0), room) < 1e-5 && m.settled());
}

#[test]
fn set_aim_turns_locked_and_follow_but_not_anchor() {
    let aim = axis_angle(0.0, 1.0, 0.0, 10.0);
    let head = axis_angle(0.0, 0.0, 1.0, 30.0);
    let mut m = ModeController::new(Tuning {
        transition_ms: 100.0,
        follow_omega: 8.0,
    });
    m.set_aim(aim);
    assert!(m.aim() == aim);
    assert!(quat_angle(m.update(head, 8.0), Quat::default()) < 1e-6); // anchor: the room stays put
    m.set_mode(ViewMode::Locked, 8.0, Change::Blend);
    m.update(head, 200.0);
    assert!(quat_angle(m.update(head, 208.0), quat_mul(head, aim)) < 1e-6);
    // A turn of the anchor while locked waits for the next anchor.
    m.turn_anchor(axis_angle(0.0, 0.0, 1.0, 5.0));
    assert!(quat_angle(m.update(head, 216.0), quat_mul(head, aim)) < 1e-6);
}

// The SysV feed and the seqlock channel. Every feed lives under a private prefix in its own scratch directory, and
// every segment a test touches was created by the test with IPC_EXCL (as xr-pose's tests/common/mod.rs).

struct Scratch {
    dir: std::path::PathBuf,
}

impl Scratch {
    fn new() -> Self {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "xr-renderer-pose-test-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Scratch { dir }
    }
    fn prefix(&self) -> String {
        format!("{}/feed_", self.dir.display())
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn private(prefix: &str) -> WriterOptions {
    WriterOptions {
        prefix: prefix.to_owned(),
        private_segments: true,
        ..WriterOptions::default()
    }
}

/// ftok keys hold 16 bits of the inode, so a private open can refuse with "key in use"; the next try lands on new keys.
fn open_private(mut open: impl FnMut() -> Result<FeedWriter, FeedError>) -> FeedWriter {
    for _ in 0..1024 {
        match open() {
            Ok(feed) => return feed,
            Err(e) if e.0.starts_with("key in use: ") => continue,
            Err(e) => panic!("private feed: {e}"),
        }
    }
    panic!("no free SysV keys for a private feed")
}

fn fake_feed(options: impl Fn(&str) -> WriterOptions) -> (Scratch, FeedWriter) {
    let scratch = Scratch::new();
    let writer = open_private(|| FeedWriter::open(&options(&scratch.prefix())));
    writer.set_disabled(false); // a writer opens disabled; FakeFeed's starts enabled
    (scratch, writer)
}

/// FakeFeed::write: q in all three slots.
fn write(w: &mut FeedWriter, q: Quat, ms: f32) {
    assert!(w.write(&[
        q.x,
        q.y,
        q.z,
        q.w,
        q.x,
        q.y,
        q.z,
        q.w,
        q.x,
        q.y,
        q.z,
        q.w,
        ms,
        ms - 16.0,
        ms - 32.0,
        0.0
    ]));
}

fn mutex_of(prefix: &str) -> *mut libc::pthread_mutex_t {
    let c = CString::new(format!("{prefix}pose_orientation_mutex")).unwrap();
    // SAFETY: plain system calls on a segment this test created.
    unsafe {
        let id = libc::shmget(libc::ftok(c.as_ptr(), 0), 0, 0);
        assert!(id >= 0);
        let addr = libc::shmat(id, std::ptr::null(), 0);
        assert!(addr as isize != -1);
        addr.cast()
    }
}

/// A raw pointer another thread may lock.
#[derive(Clone, Copy)]
struct Shared(*mut libc::pthread_mutex_t);
// SAFETY: a process-shared mutex in a segment that outlives the threads.
unsafe impl Send for Shared {}

impl Shared {
    fn get(self) -> *mut libc::pthread_mutex_t {
        self.0
    }
}

static FORKS: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// A child process locks the mutex and exits holding it: the next locker gets EOWNERDEAD.
fn die_holding(mutex: *mut libc::pthread_mutex_t) {
    let _forks = FORKS.lock().unwrap_or_else(|e| e.into_inner());
    // SAFETY: the child only locks and exits.
    unsafe {
        let child = libc::fork();
        assert!(child >= 0);
        if child == 0 {
            libc::pthread_mutex_lock(mutex);
            libc::_exit(0);
        }
        let mut status = 0;
        assert_eq!(libc::waitpid(child, &mut status, 0), child);
    }
}

fn device_ms(feed: &mut PoseFeed) -> Option<f32> {
    feed.read().map(|s| s.device_ms)
}

#[test]
fn feed_absent_driver_is_no_feed() {
    let scratch = Scratch::new();
    let why = PoseFeed::open(&scratch.prefix(), "").err().unwrap();
    assert!(!why.is_empty());
}

#[test]
fn feed_reads_a_sample_its_fov_and_nothing_when_disabled_or_invalid() {
    let (scratch, mut fake) = fake_feed(private);
    let mut feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 30.0), 1234.0);
    let s = feed.read().unwrap();
    assert!(
        near(s.device_ms, 1234.0, 1e-4)
            && near(s.orientation.z, (15.0f32 * PI / 180.0).sin(), 1e-5)
    );
    assert!(s.sample_ns == 0 && s.older_ms == [1234.0 - 16.0, 1234.0 - 32.0]);
    assert!(near(feed.display_fov().unwrap(), 52.0, 1e-4));
    assert!(feed.look_ahead_cfg() == Some(WriterOptions::default().look_ahead_cfg));
    fake.set_disabled(true);
    assert!(feed.read().is_none());
    fake.set_disabled(false);
    write(
        &mut fake,
        Quat {
            x: f32::NAN,
            y: 0.0,
            z: 0.0,
            w: 1.0,
        },
        1235.0,
    );
    assert!(feed.read().is_none());
}

#[test]
fn feed_never_uses_an_invalid_fov() {
    for fov in [f32::NAN, 500.0] {
        let (scratch, _fake) = fake_feed(|p| WriterOptions {
            display_fov_deg: fov,
            ..private(p)
        });
        assert!(
            PoseFeed::open(&scratch.prefix(), "")
                .unwrap()
                .display_fov()
                .is_none()
        );
    }
}

/// The driver dies holding its (robust) lock: the feed makes it consistent and releases it, serving the last good
/// sample for that frame, then reads on.
#[test]
fn feed_recovers_a_lock_whose_holder_died() {
    let (scratch, mut fake) = fake_feed(private);
    let mut feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    let mutex = mutex_of(&scratch.prefix());
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 100.0);
    assert!(device_ms(&mut feed) == Some(100.0));
    die_holding(mutex);
    assert!(device_ms(&mut feed) == Some(100.0));
    for _ in 0..5 {
        assert!(device_ms(&mut feed) == Some(100.0));
    }
    let m = Shared(mutex);
    let rc = std::thread::spawn(move || {
        // SAFETY: a live process-shared mutex.
        unsafe {
            let rc = libc::pthread_mutex_trylock(m.get());
            if rc == 0 {
                libc::pthread_mutex_unlock(m.get());
            }
            rc
        }
    })
    .join()
    .unwrap();
    assert_eq!(rc, 0); // the driver (another thread here) can take the lock at once
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 6.0), 101.0);
    assert!(device_ms(&mut feed) == Some(101.0));
}

/// Not recoverable: a read gets no sample and leaves the lock word free.
#[test]
fn feed_gives_nothing_for_a_lock_that_is_not_recoverable() {
    let (scratch, mut fake) = fake_feed(private);
    let mut feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    let mutex = mutex_of(&scratch.prefix());
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 300.0);
    die_holding(mutex);
    let m = Shared(mutex);
    let driver_rc = std::thread::spawn(move || {
        // SAFETY: as above; unlocked without making it consistent, as the driver does.
        unsafe {
            let rc = libc::pthread_mutex_lock(m.get());
            libc::pthread_mutex_unlock(m.get());
            rc
        }
    })
    .join()
    .unwrap();
    assert_eq!(driver_rc, libc::EOWNERDEAD);
    for _ in 0..3 {
        assert!(feed.read().is_none());
    }
    let rc = std::thread::spawn(move || {
        // SAFETY: as above.
        unsafe {
            let mut deadline: libc::timespec = std::mem::zeroed();
            libc::clock_gettime(libc::CLOCK_REALTIME, &mut deadline);
            deadline.tv_sec += 1;
            libc::pthread_mutex_timedlock(m.get(), &deadline)
        }
    })
    .join()
    .unwrap();
    assert_eq!(rc, libc::ENOTRECOVERABLE);
}

/// A thread that holds the feed's lock until released.
fn hold(mutex: *mut libc::pthread_mutex_t) -> (Arc<AtomicBool>, std::thread::JoinHandle<()>) {
    let (locked, release) = (
        Arc::new(AtomicBool::new(false)),
        Arc::new(AtomicBool::new(false)),
    );
    let (l, r, m) = (locked.clone(), release.clone(), Shared(mutex));
    let holder = std::thread::spawn(move || {
        // SAFETY: a live process-shared mutex.
        unsafe { libc::pthread_mutex_lock(m.get()) };
        l.store(true, Ordering::SeqCst);
        while !r.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(1));
        }
        // SAFETY: locked above by this thread.
        unsafe { libc::pthread_mutex_unlock(m.get()) };
    });
    while !locked.load(Ordering::SeqCst) {
        std::thread::yield_now();
    }
    (release, holder)
}

/// A live driver holding the lock: one bounded wait, then the newest sample is served again, and later frames do
/// not wait at all. A fresh sample is read once the lock is free again; a disabled read drops the held-over sample.
#[test]
fn feed_serves_the_last_sample_while_the_lock_is_held() {
    let (scratch, mut fake) = fake_feed(private);
    let mut feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    let mutex = mutex_of(&scratch.prefix());
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 200.0);
    assert!(device_ms(&mut feed) == Some(200.0));
    let (release, holder) = hold(mutex);
    let start = Instant::now();
    let mut served = true;
    for _ in 0..10 {
        served = served && device_ms(&mut feed) == Some(200.0);
    }
    let waited = start.elapsed();
    release.store(true, Ordering::SeqCst);
    holder.join().unwrap();
    assert!(served);
    assert!(waited < Duration::from_millis(15)); // a 2 ms wait per read was 20 ms
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 7.0), 201.0);
    assert!(device_ms(&mut feed) == Some(201.0));
    fake.set_disabled(true);
    assert!(feed.read().is_none()); // disabled: nothing, not a held-over sample
    fake.set_disabled(false);
    let (release, holder) = hold(mutex);
    assert!(feed.read().is_none()); // the cache was cleared by the disabled read
    release.store(true, Ordering::SeqCst);
    holder.join().unwrap();
}

/// A driver publishing at ~1 kHz alongside: every read gets a sample (never a lock failure).
#[test]
fn feed_never_misses_under_a_1khz_writer() {
    let (scratch, mut fake) = fake_feed(private);
    let mut feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 1.0), 1.0);
    let stop = Arc::new(AtomicBool::new(false));
    let s = stop.clone();
    let writer = std::thread::spawn(move || {
        let mut ms = 2.0f32;
        while !s.load(Ordering::SeqCst) {
            write(&mut fake, axis_angle(0.0, 0.0, 1.0, ms * 0.01), ms);
            std::thread::sleep(Duration::from_micros(900));
            ms += 1.0;
        }
    });
    let mut got = 0;
    for _ in 0..300 {
        got += usize::from(feed.read().is_some());
        std::thread::sleep(Duration::from_micros(300));
    }
    stop.store(true, Ordering::SeqCst);
    writer.join().unwrap();
    assert_eq!(got, 300);
}

// The seqlock channel: a writer of the test's own under a name of its own.

struct Channel {
    name: String,
    writer: PoseWriter,
}

impl Channel {
    fn new(tag: &str) -> Self {
        let name = format!("/xr-renderer-pose-test-{}-{tag}", std::process::id());
        let writer = PoseWriter::create(&name).unwrap();
        Channel { name, writer }
    }
    /// A calibrated record taken `age_ns` ago (CLOCK_MONOTONIC; raw_to_mono_ns carries the difference).
    fn publish(&self, q: Quat, age_ns: u64, seq: u32) -> u64 {
        self.publish_at(q, monotonic_ns() - age_ns, seq)
    }
    fn publish_at(&self, q: Quat, t: u64, seq: u32) -> u64 {
        self.writer.publish(&PoseRecord {
            w: q.w,
            x: q.x,
            y: q.y,
            z: q.z,
            t_raw_ns: t - 1000,
            raw_to_mono_ns: 1000,
            seq,
            flags: POSE_CALIBRATED,
        });
        t
    }
}

impl Drop for Channel {
    fn drop(&mut self) {
        let c = CString::new(self.name.as_str()).unwrap();
        // SAFETY: a NUL-terminated name this test created.
        unsafe { libc::shm_unlink(c.as_ptr()) };
    }
}

fn monotonic_ns() -> u64 {
    // SAFETY: a valid out pointer.
    let mut ts: libc::timespec = unsafe { std::mem::zeroed() };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

#[test]
fn seqlock_serves_a_fresh_calibrated_pose_before_the_mutex_feed() {
    let (scratch, mut fake) = fake_feed(private);
    let channel = Channel::new("fresh");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 500.0);
    let q = axis_angle(0.0, 0.0, 1.0, 30.0);
    let t = channel.publish(q, 0, 2);
    let s = feed.read().unwrap();
    assert!(s.sample_ns == t && s.device_ms == 0.0); // the first sample starts the epoch
    assert!(near(s.orientation.z, q.z, 1e-6) && near(s.orientation.w, q.w, 1e-6));
    assert!(s.older == [s.orientation; 2] && s.older_ms == [0.0; 2]); // no history yet: itself
}

#[test]
fn seqlock_history_is_one_sample_at_least_16ms_older() {
    let (scratch, _fake) = fake_feed(private);
    let channel = Channel::new("history");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    let base = monotonic_ns();
    let q0 = axis_angle(0.0, 0.0, 1.0, 1.0);
    let t0 = channel.publish_at(q0, base - 40_000_000, 2);
    assert!(feed.read().unwrap().sample_ns == t0);
    let q1 = axis_angle(0.0, 0.0, 1.0, 2.0);
    let t1 = channel.publish_at(q1, base - 30_000_000, 4);
    feed.read().unwrap();
    let q2 = axis_angle(0.0, 0.0, 1.0, 3.0);
    let t2 = channel.publish_at(q2, base - 20_000_000, 6);
    let s = feed.read().unwrap();
    // t2 - t1 is under 16 ms, t2 - t0 is not: q0 is the history, both slots.
    assert!(s.sample_ns == t2 && t2 - t1 < 16_000_000 && t2 - t0 >= 16_000_000);
    assert!(near(s.older[0].z, q0.z, 1e-6) && s.older[0] == s.older[1]);
    assert!(
        s.older_ms[0] == 0.0
            && s.older_ms[1] == 0.0
            && s.device_ms == ((t2 - t0) as f64 / 1e6) as f32
    );
    // The same record again is no new history entry.
    let again = feed.read().unwrap();
    assert!(again.sample_ns == t2 && again.older == s.older);
}

#[test]
fn seqlock_that_is_stale_or_uncalibrated_falls_back_to_the_mutex_feed() {
    let (scratch, mut fake) = fake_feed(private);
    let channel = Channel::new("stale");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 700.0);
    channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 150_000_000, 2); // older than kSeqlockFreshMs
    assert!(device_ms(&mut feed) == Some(700.0));
    channel.writer.publish(&PoseRecord {
        t_raw_ns: monotonic_ns(),
        seq: 4,
        flags: 0,
        ..PoseRecord::default()
    });
    assert!(device_ms(&mut feed) == Some(700.0));
    let future = PoseRecord {
        t_raw_ns: monotonic_ns() + 5_000_000,
        seq: 6,
        flags: POSE_CALIBRATED,
        ..PoseRecord::default()
    };
    channel.writer.publish(&future);
    assert!(device_ms(&mut feed) == Some(700.0)); // from the future: not believed
    let invalid = PoseRecord {
        w: f32::NAN,
        t_raw_ns: monotonic_ns(),
        seq: 8,
        flags: POSE_CALIBRATED,
        ..PoseRecord::default()
    };
    channel.writer.publish(&invalid);
    assert!(device_ms(&mut feed) == Some(700.0));
    channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 0, 10);
    assert!(feed.read().unwrap().sample_ns != 0);
}

#[test]
fn seqlock_is_not_read_while_the_feed_is_disabled() {
    let (scratch, fake) = fake_feed(private);
    let channel = Channel::new("disabled");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 0, 2);
    assert!(feed.read().is_some());
    fake.set_disabled(true);
    channel.publish(axis_angle(0.0, 0.0, 1.0, 31.0), 0, 4);
    assert!(feed.read().is_none());
}

#[test]
fn seqlock_absent_is_looked_for_again_once_a_second() {
    let (scratch, mut fake) = fake_feed(private);
    let name = format!("/xr-renderer-pose-test-{}-late", std::process::id());
    let mut feed = PoseFeed::open(&scratch.prefix(), &name).unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 900.0);
    assert!(device_ms(&mut feed) == Some(900.0)); // no channel yet: the mutex feed
    let channel = Channel {
        writer: PoseWriter::create(&name).unwrap(),
        name,
    };
    channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 0, 2);
    assert!(device_ms(&mut feed) == Some(900.0)); // not looked for again yet
}
