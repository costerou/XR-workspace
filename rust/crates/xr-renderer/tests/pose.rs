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

/// The feed's counters (`xr::PoseReaderStats`): reads by source, the switch between them, the bounded wait, and the
/// seqlock's own sample served again while the mutex feed's lock is held.
#[test]
fn feed_stats_count_sources_switches_and_waits_and_serve_a_seqlock_sample_again() {
    let (scratch, mut fake) = fake_feed(private);
    let channel = Channel::new("stats");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    assert!(feed.stats() == PoseReaderStats::default());
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 500.0);
    let t = channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 0, 2);
    assert!(feed.read().unwrap().sample_ns == t);
    let s = feed.stats();
    assert!(s.seqlock_attached && s.seqlock_source && s.seqlock_reads == 1 && s.mutex_reads == 0);
    assert!(s.age_count == 1 && s.last_age_ns < 50_000_000 && s.age_max_ns == s.last_age_ns);
    assert!(
        s.retries == 0
            && s.waits == 0
            && s.switches == 0
            && s.read_max_ns > 0
            && s.read_p99_ns <= s.read_max_ns
    );
    // The channel's record goes stale and the driver holds its lock: the seqlock sample comes again, unchanged.
    channel.publish(axis_angle(0.0, 0.0, 1.0, 40.0), 200_000_000, 4);
    let mutex = mutex_of(&scratch.prefix());
    let (release, holder) = hold(mutex);
    let again = feed.read().unwrap();
    assert!(again.sample_ns == t && again.device_ms == 0.0);
    release.store(true, Ordering::SeqCst);
    holder.join().unwrap();
    let s = feed.stats();
    assert!(
        !s.seqlock_source
            && s.seqlock_reads == 1
            && s.mutex_reads == 1
            && s.switches == 1
            && s.waits == 1
    );
    assert!(s.age_count == 1); // ages are seqlock reads only
    // The mutex feed's own sample, then disabled: nothing, and no source.
    assert!(device_ms(&mut feed) == Some(500.0));
    assert!(feed.stats().mutex_reads == 2 && feed.stats().switches == 1);
    fake.set_disabled(true);
    assert!(feed.read().is_none() && !feed.stats().seqlock_source && feed.stats().mutex_reads == 2);
}

/// A writer stopped mid-record (its sequence held odd): every one of the 4,096 copies is retried and counted, as the
/// C++ counts them, and the mutex feed serves the read. Once the record is whole again a read retries nothing.
#[test]
fn feed_stats_count_seqlock_retries_against_a_writer_mid_record() {
    let (scratch, mut fake) = fake_feed(private);
    let channel = Channel::new("retries");
    let mut feed = PoseFeed::open(&scratch.prefix(), &channel.name).unwrap();
    write(&mut fake, axis_angle(0.0, 0.0, 1.0, 5.0), 500.0);
    channel.publish(axis_angle(0.0, 0.0, 1.0, 30.0), 0, 2);
    assert!(feed.read().is_some() && feed.stats().retries == 0);
    // The slot's sequence: offset 64 of the 128-byte object (xr_pose::seqlock's layout, checked against the C++).
    let c = CString::new(channel.name.as_str()).unwrap();
    // SAFETY: a NUL-terminated name; a fresh descriptor, closed below.
    let fd = unsafe { libc::shm_open(c.as_ptr(), libc::O_RDWR, 0) };
    assert!(fd >= 0);
    // SAFETY: a shared mapping of the whole object, unmapped below.
    let addr = unsafe {
        libc::mmap(
            std::ptr::null_mut(),
            128,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            fd,
            0,
        )
    };
    assert!(addr != libc::MAP_FAILED);
    // SAFETY: offset 64 is the 8-aligned u64 sequence, only touched atomically.
    let seq = unsafe {
        &*addr
            .cast::<u8>()
            .add(64)
            .cast::<std::sync::atomic::AtomicU64>()
    };
    let even = seq.load(Ordering::SeqCst);
    assert!(even != 0 && even & 1 == 0);
    seq.store(even + 1, Ordering::SeqCst);
    assert!(device_ms(&mut feed) == Some(500.0)); // the mutex feed's sample
    let s = feed.stats();
    assert!(s.retries == 4096 && s.seqlock_reads == 1 && s.mutex_reads == 1);
    seq.store(even, Ordering::SeqCst);
    assert!(feed.read().unwrap().sample_ns != 0 && feed.stats().retries == 4096);
    // SAFETY: mapped and opened above.
    unsafe {
        libc::munmap(addr, 128);
        libc::close(fd);
    }
}

/// A pose_vsync segment of the test's own, at the feed's prefix (spike/driver/include/luma/vsync.hpp VsyncWire).
struct VsyncSegment {
    id: libc::c_int,
    words: *mut u32,
}

impl VsyncSegment {
    fn new(prefix: &str) -> Self {
        let path = format!("{prefix}pose_vsync");
        std::fs::write(&path, b"").unwrap();
        let c = CString::new(path).unwrap();
        // SAFETY: plain system calls on a segment this test creates.
        unsafe {
            let id = libc::shmget(
                libc::ftok(c.as_ptr(), 0),
                64,
                libc::IPC_CREAT | libc::IPC_EXCL | 0o600,
            );
            assert!(id >= 0, "pose_vsync key in use");
            let addr = libc::shmat(id, std::ptr::null(), 0);
            assert!(addr as isize != -1);
            VsyncSegment {
                id,
                words: addr.cast(),
            }
        }
    }
    /// seq, magic, then the payload: anchor_host_ns, period_ns, anchor_feed_ms, events, flags, resets, fit_rms_us.
    fn publish(
        &self,
        seq: u32,
        magic: u32,
        anchor_ns: u64,
        period_ns: f64,
        feed_ms: f64,
        locked: bool,
    ) {
        let mut w = [0u32; 16];
        let split = |w: &mut [u32; 16], i: usize, v: u64| {
            w[i] = v as u32;
            w[i + 1] = (v >> 32) as u32;
        };
        w[0] = seq;
        w[1] = magic;
        split(&mut w, 2, anchor_ns);
        split(&mut w, 4, period_ns.to_bits());
        split(&mut w, 6, feed_ms.to_bits());
        split(&mut w, 8, 1234);
        w[10] = u32::from(locked);
        w[11] = 3;
        w[12] = 42.5f32.to_bits();
        for (i, v) in w.iter().enumerate() {
            // SAFETY: 16 words of an attached segment.
            unsafe { self.words.add(i).write_volatile(*v) };
        }
    }
}

impl Drop for VsyncSegment {
    fn drop(&mut self) {
        // SAFETY: the segment this test created.
        unsafe {
            libc::shmdt(self.words.cast());
            libc::shmctl(self.id, libc::IPC_RMID, std::ptr::null_mut());
        }
    }
}

#[test]
fn vsync_reads_a_locked_snapshot_and_nothing_else() {
    let (scratch, _fake) = fake_feed(private);
    assert!(
        PoseFeed::open(&scratch.prefix(), "")
            .unwrap()
            .vsync()
            .is_none()
    ); // XRLinuxDriver: no segment
    let segment = VsyncSegment::new(&scratch.prefix());
    let feed = PoseFeed::open(&scratch.prefix(), "").unwrap();
    const LVS1: u32 = 0x3153_564c;
    segment.publish(2, LVS1, 7_000_000_123, 11_111_111.5, 812.25, true);
    let v = feed.vsync().unwrap();
    assert!(
        v.anchor_host_ns == 7_000_000_123
            && v.period_ns == 11_111_111.5
            && v.anchor_feed_ms == 812.25
    );
    assert!(v.events == 1234 && v.resets == 3 && v.fit_rms_us == 42.5);
    for (seq, magic, period, locked) in [
        (2, LVS1, 11e6, false),    // not locked
        (2, LVS1 + 1, 11e6, true), // another layout
        (3, LVS1, 11e6, true),     // mid-write, every try
        (2, LVS1, 3.9e6, true),    // period out of range
        (2, LVS1, 50.1e6, true),
        (2, LVS1, f64::NAN, true),
    ] {
        segment.publish(seq, magic, 1, period, 0.0, locked);
        assert!(feed.vsync().is_none(), "{seq} {magic:x} {period} {locked}");
    }
    segment.publish(4, LVS1, 1, 4e6, f64::NAN, true); // the edges of the range; NaN feed time until a pose
    assert!(
        feed.vsync()
            .is_some_and(|v| v.period_ns == 4e6 && v.anchor_feed_ms.is_nan())
    );
    segment.publish(4, LVS1, 1, 50e6, 0.0, true);
    assert!(feed.vsync().is_some());
}

/// Twin of `spike/tests/focus_center_test.cpp` (regression 2026-11-03): follow and locked centre the focused desktop
/// (focus_direction / focus_weight -> aim_rotation -> ModeController::set_aim), an anchor switch stays on its target, a
/// cursor switch centres it, and a maximize holds the room until it lands. Placement maths (view.rs) and the head view
/// (pose.rs) together, projecting the focused desktop's centre.
mod focus_center {
    use super::{axis_angle, near};
    use std::f32::consts::PI;
    use xr_renderer::pose::{
        Change, Mat3, ModeController, Quat, ViewMode, aim_along, aim_rotation, apply, mat3_to_quat,
        quat_angle, quat_conj, quat_mul, quat_to_mat3, view_from,
    };
    use xr_renderer::view::{
        Canvas, FRect, Placement, Size, TargetOptions, ViewAnimator, blended_layout,
        focus_direction, focus_weight, look_turn, look_turn_from, overview_layout, place_point,
        view_centre, zoomed_placements,
    };

    fn rad(deg: f32) -> f32 {
        deg * PI / 180.0
    }

    fn clamp(v: f32, lo: f32, hi: f32) -> f32 {
        if v < lo {
            lo
        } else if hi < v {
            hi
        } else {
            v
        }
    }

    const SIZES: [Size; 3] = [
        Size::new(1920.0, 1080.0),
        Size::new(1920.0, 1080.0),
        Size::new(1920.0, 1080.0),
    ];

    fn canvas() -> Canvas {
        let half_w = rad(23.0).tan();
        Canvas::new(1920.0, 1200.0, half_w, half_w * 1200.0 / 1920.0)
    }

    fn placement(x: f32, y: f32, distance: f32, roll: f32) -> Placement {
        Placement {
            x,
            y,
            distance,
            curvature: 0.0,
            roll,
        }
    }

    /// Desktop 0 dragged 30 deg right and 12 deg up (now right of the middle one), 1 at its slot, 2 dragged 140 deg
    /// (behind you).
    fn placed() -> [Placement; 3] {
        [
            placement(rad(30.0), rad(12.0), 1.4, 0.0),
            Placement::default(),
            placement(rad(140.0), rad(-5.0), 1.0, 0.0),
        ]
    }

    /// placed() with a levelling roll on every desktop (focus_direction ignores it, so aim_for still applies).
    fn rolled() -> [Placement; 3] {
        [
            placement(rad(30.0), rad(12.0), 1.4, rad(20.0)),
            placement(0.0, 0.0, 1.0, rad(-35.0)),
            placement(rad(140.0), rad(-5.0), 1.0, rad(7.5)),
        ]
    }

    fn overview() -> Vec<FRect> {
        overview_layout(&SIZES, 1920.0, 1200.0, 40.0)
    }

    fn direction(i: usize) -> [f32; 2] {
        focus_direction(&canvas(), &overview(), &placed(), i)
    }

    /// Angle (degrees) between the view's centre and where desktop `focus`'s centre is drawn with `view` at `zoom`.
    fn off_centre(view: &Mat3, focus: usize, zoom: f32) -> f32 {
        let c = canvas();
        let tiles = blended_layout(&SIZES, 1920.0, 1200.0, 40.0, zoom, focus as f32);
        let placed = zoomed_placements(&c, &overview(), &tiles, &placed(), zoom, focus as f32);
        let t = &tiles[focus];
        let p = place_point(
            &c,
            t,
            &placed[focus],
            t.x + t.width / 2.0,
            t.y + t.height / 2.0,
        );
        let v = apply(view, [p.x, p.y, p.z]);
        let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        clamp(-v[2] / n, -1.0, 1.0).acos() * 180.0 / PI
    }

    fn aim_for(focus: usize, weight: f32) -> Quat {
        let d = direction(focus);
        aim_rotation(weight * d[0], weight * d[1])
    }

    /// How far (degrees) maximized desktop `focus` is from level and square to the glasses as `view` draws it: its
    /// left-right midline should run exactly along the view's x, its bottom-top midline along y.
    fn off_square(view: &Mat3, focus: usize) -> f32 {
        let c = canvas();
        let tiles = blended_layout(&SIZES, 1920.0, 1200.0, 40.0, 1.0, focus as f32);
        let placed = zoomed_placements(&c, &overview(), &tiles, &rolled(), 1.0, focus as f32);
        let t = &tiles[focus];
        let seen = |px: f32, py: f32| {
            let p = place_point(&c, t, &placed[focus], px, py);
            apply(view, [p.x, p.y, p.z])
        };
        let (cx, cy) = (t.x + t.width / 2.0, t.y + t.height / 2.0);
        let (l, r, b, u) = (
            seen(t.x, cy),
            seen(t.x + t.width, cy),
            seen(cx, t.y + t.height),
            seen(cx, t.y),
        );
        let across = [r[0] - l[0], r[1] - l[1], r[2] - l[2]];
        let up = [u[0] - b[0], u[1] - b[1], u[2] - b[2]];
        let off = |along: f32, a: f32, c: f32| a.hypot(c).atan2(along) * 180.0 / PI;
        let (x, y) = (
            off(across[0], across[1], across[2]),
            off(up[1], up[0], up[2]),
        );
        if x < y { y } else { x }
    }

    fn dir(az: f32, el: f32) -> Quat {
        aim_rotation(rad(az), rad(el))
    }

    /// hv * lt, as wayland.cpp draws an anchor switch.
    fn compose(hv: &Mat3, lt: &Mat3) -> Mat3 {
        let mut out = [0.0f32; 9];
        for r in 0..3 {
            for c in 0..3 {
                out[r * 3 + c] =
                    hv[r * 3] * lt[c] + hv[r * 3 + 1] * lt[3 + c] + hv[r * 3 + 2] * lt[6 + c];
            }
        }
        out
    }

    /// Where desktop i's overview direction (slot + orbit) is drawn: a unit vector in view space.
    fn dir_of(view: &Mat3, i: usize) -> [f32; 3] {
        let d = direction(i);
        let (ca, sa, ce, se) = (d[0].cos(), d[0].sin(), d[1].cos(), d[1].sin());
        apply(view, [sa * ce, se, -ca * ce])
    }

    fn angle(p: [f32; 3], q: [f32; 3]) -> f32 {
        clamp(p[0] * q[0] + p[1] * q[1] + p[2] * q[2], -1.0, 1.0).acos()
    }

    fn turn_between(a: [f32; 2], b: [f32; 2]) -> Quat {
        quat_mul(
            quat_conj(aim_rotation(a[0], a[1])),
            aim_rotation(b[0], b[1]),
        )
    }

    fn centred() -> TargetOptions {
        TargetOptions {
            center: true,
            ..TargetOptions::default()
        }
    }

    /// `for (float s = 0; s <= 1.f; s += 0.1f)`.
    fn tenths() -> Vec<f32> {
        let mut out = Vec::new();
        let mut s = 0.0f32;
        while s <= 1.0 {
            out.push(s);
            s += 0.1;
        }
        out
    }

    #[test]
    fn edge_pan_aim_turns_one_way_along_one_rotation() {
        {
            // One rotation from end to end: exactly each aim at its end, and each share of the way that share of the
            // angle.
            let (a, b) = (dir(-30.0, 10.0), dir(40.0, -20.0));
            let way = quat_angle(a, b);
            assert!(
                quat_angle(aim_along(a, b, way, 0.0, 0), a) < 1e-3
                    && quat_angle(aim_along(a, b, way, way, 0), b) < 1e-3
            );
            assert!(quat_angle(aim_along(a, b, way, way * 2.0, 0), b) < 1e-3);
            for s in [0.2f32, 0.5, 0.8] {
                let q = aim_along(a, b, way, way * s, 0);
                assert!(
                    (quat_angle(a, q) - way * s).abs() < 2e-3
                        && (quat_angle(q, b) - way * (1.0 - s)).abs() < 2e-3
                );
            }
        }
        {
            // Level neighbours: the view goes straight along, its height never moving.
            let (a, b) = (dir(-20.0, 0.0), dir(20.0, 0.0));
            let way = quat_angle(a, b);
            let mut was = -21.0;
            for s in tenths() {
                let c = view_centre(&view_from(
                    Quat::default(),
                    aim_along(a, b, way, way * s, 0),
                ));
                assert!(c[0] * 180.0 / PI > was && c[1].abs() < 1e-3);
                was = c[0] * 180.0 / PI;
            }
        }
        {
            // Raised neighbours: upright at both ends and hardly rolled between them, the centre going one way all
            // along.
            let (a, b) = (dir(-22.0, 9.0), dir(20.0, 9.0));
            let way = quat_angle(a, b);
            let mut was = -23.0;
            for s in tenths() {
                let c = view_centre(&view_from(
                    Quat::default(),
                    aim_along(a, b, way, way * s, 0),
                ));
                assert!(c[0] * 180.0 / PI > was && (c[1] * 180.0 / PI - 9.0).abs() < 1.5);
                was = c[0] * 180.0 / PI;
            }
        }
        {
            // The long way: past half a turn it goes on round the way it started, never back through the short way.
            let (a, b) = (dir(0.0, 0.0), dir(-160.0, 0.0)); // 160 to the left the short way: 200 to the right
            let way = 2.0 * PI - quat_angle(a, b);
            assert!(way > PI);
            let mut az = 0.0;
            for i in 1..=19 {
                let c = view_centre(&view_from(
                    Quat::default(),
                    aim_along(a, b, way, way * i as f32 / 20.0, 0),
                ));
                let mut now = c[0] * 180.0 / PI;
                if now < az - 90.0 {
                    now += 360.0; // past behind you
                }
                assert!(now > az);
                az = now;
            }
            assert!(quat_angle(aim_along(a, b, way, way, 0), b) < 1e-3);
        }
        {
            // Dead opposite there is no short way: the push says which way round.
            let (a, b) = (dir(0.0, 0.0), dir(180.0, 0.0));
            assert!(
                view_centre(&view_from(
                    Quat::default(),
                    aim_along(a, b, PI, rad(10.0), 0)
                ))[0]
                    > rad(5.0)
            ); // right
            assert!(
                view_centre(&view_from(
                    Quat::default(),
                    aim_along(a, b, PI, rad(10.0), 1)
                ))[0]
                    < -rad(5.0)
            ); // left
            assert!(
                quat_angle(aim_along(a, b, PI, PI, 0), b) < 1e-3
                    && quat_angle(aim_along(a, b, PI, PI, 1), b) < 1e-3
            );
        }
        {
            // A turn of no length is already there.
            let a = dir(10.0, 5.0);
            assert!(quat_angle(aim_along(a, a, 0.0, 0.0, 0), a) < 1e-4);
        }
        // Landing: the aim ends on the target exactly, in front of you in follow (head turned) and on the view in
        // locked.
        for mode in [ViewMode::Follow, ViewMode::Locked] {
            let (from, to) = (direction(1), direction(0));
            let (start, end) = (aim_rotation(from[0], from[1]), aim_rotation(to[0], to[1]));
            let mut m = ModeController::default();
            let head = axis_angle(0.0, 0.0, 1.0, 25.0);
            m.set_aim(aim_rotation(from[0], from[1]));
            m.set_mode(mode, 0.0, Change::Blend);
            let mut t = 0.0;
            while t < 3000.0 {
                m.update(head, t); // settled on the desktop being left
                t += 8.0;
            }
            assert!(m.settled());
            let way = quat_angle(start, end);
            for i in 0..=65 {
                m.set_aim(aim_along(start, end, way, way * i as f32 / 65.0, 1));
                m.update(head, t);
                t += 8.0;
            }
            for _ in 0..300 {
                m.set_aim(end);
                m.update(head, t);
                t += 8.0;
            }
            assert!(off_centre(&view_from(head, m.update(head, t)), 0, 1.0) < 0.05);
        }
    }

    /// The overview's edge pan turns the room: world points rotate by -turn about the path's axis, so the view turns
    /// toward the target; its landing slerps to look_turn_from at 1 through mat3_to_quat.
    #[test]
    fn overview_pan_turn_lands_on_look_turn_from() {
        for q in [
            Quat::default(),
            axis_angle(0.0, 1.0, 0.0, 30.0),
            axis_angle(0.6, 0.8, 0.0, -150.0),
            axis_angle(0.0, 0.0, 1.0, 179.0),
            axis_angle(1.0, 0.0, 0.0, 90.0),
        ] {
            let (a, b) = (
                quat_to_mat3(q),
                quat_to_mat3(mat3_to_quat(&quat_to_mat3(q))),
            );
            assert!((0..9).all(|i| near(a[i], b[i], 1e-5)));
        }
        {
            // E straight ahead, P 20 degrees right: the axis E x P is -y; world rotated by -turn brings P toward the
            // centre.
            let (e, p) = (
                [0.0f32, 0.0, -1.0],
                [rad(20.0).sin(), 0.0, -rad(20.0).cos()],
            );
            let axis = [0.0f32, -1.0, 0.0];
            let turn = rad(20.0);
            let s = (-turn / 2.0).sin();
            let m = quat_to_mat3(Quat {
                x: axis[0] * s,
                y: axis[1] * s,
                z: axis[2] * s,
                w: (-turn / 2.0).cos(),
            });
            let moved = apply(&m, p);
            assert!(near(moved[0], e[0], 1e-5) && near(moved[2], e[2], 1e-5)); // the target is where E was
        }
        {
            // The landing's end is look_turn_from at 1 exactly: the turn land_turns hands to the room.
            let fin = look_turn_from(
                &canvas(),
                &overview(),
                &placed(),
                [rad(-5.0), rad(2.0)],
                0,
                1.0,
            );
            let back = quat_to_mat3(mat3_to_quat(&fin));
            assert!((0..9).all(|i| near(fin[i], back[i], 1e-5)));
        }
    }

    #[test]
    fn focus_direction_is_slot_plus_orbit_and_aim_rotation_centres_it_upright() {
        let mid = direction(1);
        assert!(mid[0].abs() < 1e-5 && mid[1].abs() < 1e-5);
        let d0 = direction(0);
        assert!(
            d0[0] > rad(30.0) - rad(20.0) && d0[0] < rad(30.0) && (d0[1] - rad(12.0)).abs() < 1e-4
        ); // slot left, dragged right
        assert!(direction(9) == [0.0, 0.0]);
        // aim_rotation: identity at (0, 0); the view it makes puts direction (az, el) on the centre, upright.
        assert!((aim_rotation(0.0, 0.0).w - 1.0).abs() < 1e-6);
        for az in [-170.0f32, -40.0, 0.0, 25.0, 140.0] {
            for el in [-60.0f32, -5.0, 0.0, 12.0, 70.0] {
                let view = view_from(Quat::default(), aim_rotation(rad(az), rad(el)));
                let (ca, sa, ce, se) = (rad(az).cos(), rad(az).sin(), rad(el).cos(), rad(el).sin());
                let f = apply(&view, [sa * ce, se, -ca * ce]); // place_point's direction (az, el)
                assert!(f[0].abs() < 1e-4 && f[1].abs() < 1e-4 && f[2] < 0.0);
                let up = apply(&view, [-sa * se, ce, ca * se]); // its up along the meridian stays up
                assert!(up[0].abs() < 1e-4 && up[1] > 0.999);
            }
        }
    }

    fn heads() -> [Quat; 4] {
        [
            Quat::default(),
            axis_angle(0.0, 0.0, 1.0, 25.0),
            quat_mul(
                axis_angle(0.0, 0.0, 1.0, -50.0),
                axis_angle(0.0, 1.0, 0.0, 15.0),
            ),
            quat_mul(
                axis_angle(0.0, 0.0, 1.0, 170.0),
                axis_angle(1.0, 0.0, 0.0, 10.0),
            ),
        ]
    }

    #[test]
    fn locked_and_follow_centre_the_focused_desktop() {
        // Locked: zoomed into any desktop, however it was dragged and wherever the head points, it is dead centre.
        for focus in 0..3 {
            for head in heads() {
                let mut m = ModeController::default();
                let mut t = 0.0;
                m.set_mode(ViewMode::Locked, t, Change::Blend);
                m.set_aim(aim_for(focus, 1.0));
                t += 400.0;
                m.update(head, t);
                t += 8.0;
                let anchor = m.update(head, t);
                assert!(off_centre(&view_from(head, anchor), focus, 1.0) < 0.05);
                // Without the aim (the bug): every desktop but the middle one is off centre.
                if focus != 1 {
                    assert!(off_centre(&view_from(head, head), focus, 1.0) > 10.0);
                }
            }
        }
        // Follow: once the spring settles it is centred too, and a head turn is followed back onto it.
        for focus in 0..3 {
            let mut m = ModeController::default();
            let mut t = 0.0;
            m.set_mode(ViewMode::Follow, t, Change::Blend);
            m.set_aim(aim_for(focus, 1.0));
            for head in heads() {
                for _ in 0..300 {
                    t += 8.0;
                    m.update(head, t);
                }
                t += 8.0;
                assert!(off_centre(&view_from(head, m.update(head, t)), focus, 1.0) < 0.1);
                assert!(m.settled());
            }
        }
    }

    /// Maximized it sits level and square to the glasses: whatever roll and orbit it has in the overview, wherever the
    /// head points and however it is tipped, in locked, in follow once the spring settles, and across Ctrl+Alt+V.
    #[test]
    fn maximized_sits_level_and_square_to_the_glasses() {
        let tipped = [
            Quat::default(),
            axis_angle(1.0, 0.0, 0.0, 25.0),
            quat_mul(
                axis_angle(0.0, 0.0, 1.0, -50.0),
                axis_angle(1.0, 0.0, 0.0, -30.0),
            ),
            quat_mul(
                axis_angle(0.0, 0.0, 1.0, 170.0),
                axis_angle(0.0, 1.0, 0.0, 20.0),
            ),
        ];
        for focus in 0..3 {
            let mut m = ModeController::default();
            let mut t = 0.0;
            m.set_aim(aim_for(focus, 1.0));
            for mode in [ViewMode::Locked, ViewMode::Follow, ViewMode::Locked] {
                m.set_mode(mode, t, Change::Blend);
                for head in tipped {
                    for _ in 0..300 {
                        t += 8.0;
                        m.update(head, t);
                    }
                    t += 8.0;
                    assert!(off_square(&view_from(head, m.update(head, t)), focus) < 0.1);
                }
            }
        }
    }

    #[test]
    fn overview_is_unchanged_and_anchor_recentres_on_the_focus() {
        // The overview (weight 0) is unchanged in locked and follow: the canvas centre straight ahead.
        {
            let mut m = ModeController::default();
            m.set_mode(ViewMode::Locked, 0.0, Change::Blend);
            m.set_aim(aim_for(0, 0.0));
            let head = axis_angle(0.0, 0.0, 1.0, 33.0);
            m.update(head, 400.0);
            let anchor = m.update(head, 408.0);
            assert!(quat_angle(anchor, head) < 1e-4);
        }
        // Anchor: the room stays put (the aim moves nothing), and a recentre while zoomed puts the focused desktop
        // ahead.
        {
            let mut m = ModeController::default();
            let mut t = 0.0;
            m.set_aim(aim_for(0, 1.0));
            t += 8.0;
            assert!(
                quat_angle(
                    m.update(axis_angle(0.0, 0.0, 1.0, 40.0), t),
                    Quat::default()
                ) < 1e-5
            ); // world-locked
            m.recenter(); // PoseState's reference moves to the head too: the relative head is the identity again
            t += 8.0;
            let anchor = m.update(Quat::default(), t);
            assert!(off_centre(&view_from(Quat::default(), anchor), 0, 1.0) < 0.05);
            let turned = axis_angle(0.0, 0.0, 1.0, -20.0); // and then it is world-locked there
            t += 8.0;
            assert!(quat_angle(m.update(turned, t), anchor) < 1e-5);
            m.set_aim(aim_for(0, 0.0));
            m.recenter(); // in the overview the canvas centre comes straight ahead, as before
            t += 8.0;
            assert!(quat_angle(m.update(Quat::default(), t), Quat::default()) < 1e-5);
        }
    }

    /// Mode changes keep blending from where the canvas is: follow -> anchor pins it with the focused desktop ahead,
    /// and anchor -> follow starts there with no jump.
    #[test]
    fn mode_changes_blend_from_where_the_canvas_is() {
        let mut m = ModeController::default();
        let mut t = 0.0;
        let head = axis_angle(0.0, 0.0, 1.0, 15.0);
        m.set_mode(ViewMode::Follow, t, Change::Blend);
        m.set_aim(aim_for(2, 1.0));
        for _ in 0..300 {
            t += 8.0;
            m.update(head, t);
        }
        t += 8.0;
        let here = m.update(head, t);
        m.set_mode(ViewMode::Anchor, t, Change::Blend);
        for _ in 0..60 {
            t += 8.0;
            assert!(quat_angle(m.update(axis_angle(0.0, 0.0, 1.0, -30.0), t), here) < 1e-4);
        }
        m.set_mode(ViewMode::Follow, t, Change::Blend);
        t += 1.0;
        assert!(quat_angle(m.update(axis_angle(0.0, 0.0, 1.0, -30.0), t), here) < rad(2.0));
    }

    /// A switch (zoom out, turn, zoom in) keeps the aim continuous in every phase: it starts on the current desktop,
    /// ends on the target, never jumps; also when it is cancelled mid-turn.
    #[test]
    fn a_switch_keeps_the_aim_continuous() {
        for cancel in [false, true] {
            let mut v = ViewAnimator::new(220.0, 300.0);
            let mut t = 0.0;
            v.set_target(true, 0, 40, t);
            while t < 400.0 {
                v.advance(t);
                t += 1.0;
            }
            assert!(focus_weight(&v) == 1.0 && v.focus() == 0);
            v.set_target(true, 2, 40, t);
            let mut last = aim_for(v.focus() as usize, focus_weight(&v));
            assert!(quat_angle(last, aim_for(0, 1.0)) < 1e-5);
            let mut worst = 0.0f32;
            let end = t + 1200.0;
            while t < end {
                if cancel && t > 400.0 + 220.0 + 150.0 && t < 400.0 + 220.0 + 152.0 {
                    v.set_target(false, 2, 40, t);
                }
                v.advance(t);
                let now = aim_for(v.focus() as usize, focus_weight(&v));
                worst = worst.max(quat_angle(now, last));
                last = now;
                t += 1.0;
            }
            assert!(worst < rad(2.0)); // per ms
            assert!(!v.switching());
            assert!(
                quat_angle(
                    last,
                    if cancel {
                        Quat::default()
                    } else {
                        aim_for(2, 1.0)
                    }
                ) < 1e-5
            );
        }
    }

    /// Regression (2026-11-03): in anchor mode a switch turned the view to the target, then phase 3 eased the camera
    /// turn back to 0 with the room still pinned, so the view panned back to the desktop it left. Now the camera turn
    /// holds through phase 3 and the landed turn moves the anchor: the target is ahead, continuously, and stays.
    #[test]
    fn an_anchor_switch_stays_on_its_target() {
        for cancel in 0..3 {
            // 0: lands; 1: zoomed out mid-turn (eases back); 2: zoomed out in phase 3
            let mut v = ViewAnimator::new(220.0, 300.0);
            let mut m = ModeController::default();
            let mut t = 0.0;
            let head = axis_angle(0.0, 0.0, 1.0, 10.0);
            m.update(head, t);
            v.set_target(true, 0, 40, t);
            while t < 400.0 {
                v.advance(t);
                t += 1.0;
            }
            let drawn = |v: &mut ViewAnimator, m: &mut ModeController, now: f64| {
                v.advance(now);
                if let Some(l) = v.take_landed_turn() {
                    m.turn_anchor(turn_between(
                        direction(l[0] as usize),
                        direction(l[1] as usize),
                    ));
                }
                let hv = view_from(head, m.update(head, now));
                let lt = look_turn(
                    &canvas(),
                    &overview(),
                    &placed(),
                    v.look_from() as usize,
                    v.look_to() as usize,
                    v.turn(),
                );
                compose(&hv, &lt)
            };
            let ahead0 = dir_of(&drawn(&mut v, &mut m, t), 0); // where desktop 0 is seen before the switch
            v.set_target(true, 2, 40, t);
            let mut last = ahead0;
            let mut worst = 0.0f32;
            let mut done = false;
            let end = t + 1500.0;
            while t < end {
                if cancel == 1 && !done && t > 400.0 + 220.0 + 150.0 {
                    v.set_target(false, 2, 40, t);
                    done = true;
                }
                if cancel == 2 && !done && t > 400.0 + 220.0 + 300.0 + 100.0 {
                    v.set_target(false, 2, 40, t);
                    done = true;
                }
                let view = drawn(&mut v, &mut m, t);
                let now = dir_of(&view, 0);
                worst = worst.max(angle(now, last));
                last = now;
                // Through the zoom in (phase 3) and after it the target sits where desktop 0 was seen: no pan back.
                if cancel == 0 && t > 400.0 + 220.0 + 300.0 {
                    assert!(angle(dir_of(&view, 2), ahead0) < rad(0.05));
                }
                t += 1.0;
            }
            assert!(worst < rad(2.0)); // per ms: never a jump, also when cancelled or handed over to the room
            assert!(!v.switching() && v.take_landed_turn().is_none());
            let rest = drawn(&mut v, &mut m, t);
            if cancel == 1 {
                assert!(angle(dir_of(&rest, 0), ahead0) < rad(0.05)); // cancelled mid-turn: eased back to 0
            } else {
                assert!(angle(dir_of(&rest, 2), ahead0) < rad(0.05)); // landed (or zoomed out on it): stays
            }
            // ...and the room is world-locked there: turning the head does not bring the old desktop back.
            if cancel != 1 {
                t += 8.0;
                let turned_head = m.update(axis_angle(0.0, 0.0, 1.0, -35.0), t);
                t += 8.0;
                assert!(quat_angle(turned_head, m.update(head, t)) < 1e-5);
            }
        }
    }

    /// A switch back lands back where it started (the turns compose).
    #[test]
    fn a_switch_back_lands_where_it_started() {
        let mut v = ViewAnimator::new(220.0, 300.0);
        let mut m = ModeController::default();
        v.set_target(true, 1, 40, 0.0);
        v.advance(400.0);
        let mut t = 500.0;
        for to in [0u32, 2, 1] {
            v.set_target(true, to, 40, t);
            t += 1000.0;
            v.advance(t);
            let l = v.take_landed_turn();
            assert!(l.is_some_and(|l| l[1] == to));
            let l = l.unwrap();
            m.turn_anchor(turn_between(
                direction(l[0] as usize),
                direction(l[1] as usize),
            ));
        }
        assert!(quat_angle(m.update(Quat::default(), 5000.0), Quat::default()) < 1e-4);
    }

    /// Cursor switch (cursor-switch-no-anim, 2026-11-03): in anchor mode the cursor pushed onto another desktop pans
    /// the view straight to it and centres it, then it stays; pushing back pans to and centres the other one. The head
    /// looks off to the side of desktop 0, so a plain switch would leave the target off centre.
    #[test]
    fn a_cursor_switch_centres_its_target_in_one_move() {
        struct Centre {
            have: bool,
            serial: u32,
            centre: [f32; 2],
            last_drawn: Mat3,
        }
        impl Centre {
            fn of(&self, s: u32) -> [f32; 2] {
                if self.have && self.serial == s {
                    self.centre
                } else {
                    view_centre(&self.last_drawn)
                }
            }
        }
        for cancel in 0..2 {
            // 1: zoomed out in phase 3 (the turn lands at once, still centred)
            let mut v = ViewAnimator::new(220.0, 300.0);
            let mut m = ModeController::default();
            let mut t = 0.0;
            let head = quat_mul(
                axis_angle(0.0, 0.0, 1.0, 14.0),
                axis_angle(0.0, 1.0, 0.0, -6.0),
            ); // off desktop 0
            m.update(head, t);
            v.set_target(true, 0, 40, t);
            while t < 400.0 {
                v.advance(t);
                t += 1.0;
            }
            let mut c = Centre {
                have: false,
                serial: 0,
                centre: [0.0; 2],
                last_drawn: view_from(head, m.update(head, t)),
            };
            let drawn = |v: &mut ViewAnimator, m: &mut ModeController, c: &mut Centre, now: f64| {
                v.advance(now);
                for l in v.take_landed_turns() {
                    let a = if l.centred {
                        c.of(l.serial)
                    } else {
                        direction(l.from as usize)
                    };
                    m.turn_anchor(turn_between(a, direction(l.to as usize)));
                }
                if v.turn_centred() && !(c.have && c.serial == v.serial()) {
                    c.have = true;
                    c.serial = v.serial();
                    c.centre = view_centre(&c.last_drawn);
                }
                let hv = view_from(head, m.update(head, now));
                let lt = if v.turn_centred() {
                    look_turn_from(
                        &canvas(),
                        &overview(),
                        &placed(),
                        c.of(v.serial()),
                        v.look_to() as usize,
                        v.turn(),
                    )
                } else {
                    look_turn(
                        &canvas(),
                        &overview(),
                        &placed(),
                        v.look_from() as usize,
                        v.look_to() as usize,
                        v.turn(),
                    )
                };
                c.last_drawn = compose(&hv, &lt);
                c.last_drawn
            };
            // Degrees between the view's centre and desktop i's direction.
            let off =
                |view: &Mat3, i: usize| clamp(-dir_of(view, i)[2], -1.0, 1.0).acos() * 180.0 / PI;
            assert!(off(&drawn(&mut v, &mut m, &mut c, t), 0) > 5.0); // not looking straight at desktop 0
            for to in [2u32, 0, 2] {
                // there, back, there again
                let from = v.selected();
                v.set_target_with(true, to, 40, t, centred());
                let mut prev = drawn(&mut v, &mut m, &mut c, t);
                let (mut worst, mut closest) = (0.0f32, 180.0f32);
                let mut done = false;
                let end = t + 1500.0;
                while t < end {
                    if cancel == 1 && !done && t > end - 1500.0 + 220.0 + 300.0 + 100.0 {
                        v.set_target_with(false, to, 40, t, centred());
                        done = true;
                    }
                    let view = drawn(&mut v, &mut m, &mut c, t);
                    let (a, b) = (view_centre(&prev), view_centre(&view));
                    // How far the view moved this ms (its centre ray). std::remainder: ties to even.
                    let step = ieee_remainder(a[0] - b[0], 2.0 * PI) * a[1].cos();
                    let step = step.hypot(a[1] - b[1]);
                    worst = worst.max(step);
                    // One move: once the target is closest it never drifts off again (no pan back, no pan elsewhere).
                    let o = off(&view, to as usize);
                    assert!(o <= closest + 0.05);
                    closest = closest.min(o);
                    prev = view;
                    t += 1.0;
                }
                assert!(worst < rad(2.0)); // per ms: continuous, also when the turn hands over to the room
                assert!(!v.switching() && v.take_landed_turns().is_empty());
                let rest = drawn(&mut v, &mut m, &mut c, t);
                assert!(off(&rest, to as usize) < 0.05); // centred in view
                assert!(off(&rest, from as usize) > 5.0);
                // ...and world-locked there: the room does not move with the head (anchor).
                t += 8.0;
                let away = m.update(axis_angle(0.0, 0.0, 1.0, -35.0), t);
                t += 8.0;
                assert!(quat_angle(away, m.update(head, t)) < 1e-5);
                if cancel == 1 {
                    break;
                }
            }
        }
    }

    /// `std::remainder` for f32: x - n * y with n the nearest integer to x / y, ties to even.
    fn ieee_remainder(x: f32, y: f32) -> f32 {
        let (x, y) = (f64::from(x), f64::from(y));
        let n = (x / y).round_ties_even();
        (x - n * y) as f32
    }

    /// Follow and locked never see `center`: a cursor switch drives everything they read exactly as a key switch does,
    /// ms by ms, also retargeted, queued and zoomed out mid-switch.
    #[test]
    fn a_cursor_switch_is_a_key_switch_to_follow_and_locked() {
        let mut key = ViewAnimator::new(220.0, 300.0);
        let mut cursor = ViewAnimator::new(220.0, 300.0);
        for v in [&mut key, &mut cursor] {
            v.set_target(true, 0, 40, 0.0);
            v.advance(400.0);
        }
        let steps: [(f64, bool, u32); 7] = [
            (400.0, true, 2),
            (500.0, true, 1),
            (800.0, true, 0),
            (2500.0, true, 2),
            (2900.0, false, 2),
            (3600.0, true, 2),
            (4500.0, true, 1),
        ];
        let mut next = 0;
        let mut t = 400.0;
        while t < 6000.0 {
            while next < steps.len() && steps[next].0 <= t {
                let (_, zoomed, to) = steps[next];
                key.set_target(zoomed, to, 40, t);
                cursor.set_target_with(zoomed, to, 40, t, centred());
                next += 1;
            }
            key.advance(t);
            cursor.advance(t);
            assert!(
                key.zoom() == cursor.zoom()
                    && key.look() == cursor.look()
                    && key.focus() == cursor.focus()
                    && key.position() == cursor.position()
                    && key.switching() == cursor.switching()
            );
            let a = aim_for(key.focus() as usize, focus_weight(&key));
            let b = aim_for(cursor.focus() as usize, focus_weight(&cursor));
            assert!(a.x == b.x && a.y == b.y && a.z == b.z && a.w == b.w); // the follow / locked aim, bit for bit
            t += 1.0;
        }
    }

    /// A key switch (not centred) is unchanged: the target comes where the desktop it left was seen.
    #[test]
    fn a_key_switch_is_not_centred() {
        let mut v = ViewAnimator::new(220.0, 300.0);
        v.set_target(true, 0, 40, 0.0);
        v.advance(400.0);
        v.set_target(true, 2, 40, 400.0);
        assert!(!v.turn_centred());
        v.advance(2000.0);
        let l = v.take_landed_turns();
        assert!(l.len() == 1 && !l[0].centred && l[0].from == 0 && l[0].to == 2);
        v.set_target_with(true, 0, 40, 2000.0, centred());
        assert!(v.turn_centred() && v.serial() == 2);
        v.set_target(true, 1, 40, 2010.0); // retargeted while zooming out by a key: no longer centred
        assert!(!v.turn_centred());
    }

    /// view_centre: the identity view looks straight ahead; a view aimed at (az, el) is centred there.
    #[test]
    fn view_centre_is_where_the_view_is_aimed() {
        let c0 = view_centre(&view_from(Quat::default(), Quat::default()));
        assert!(c0[0].abs() < 1e-6 && c0[1].abs() < 1e-6);
        for az in [-120.0f32, -30.0, 15.0, 80.0] {
            for el in [-40.0f32, 0.0, 25.0] {
                let c = view_centre(&view_from(Quat::default(), aim_rotation(rad(az), rad(el))));
                assert!((c[0] - rad(az)).abs() < 1e-4 && (c[1] - rad(el)).abs() < 1e-4);
            }
        }
    }

    /// One frame as wayland.cpp drives it: the aim from the zoom, land_maximize once the zoom is in, then update.
    fn frame(v: &mut ViewAnimator, m: &mut ModeController, h: Quat, now: f64) -> Quat {
        v.advance(now);
        m.set_aim(aim_for(v.focus() as usize, focus_weight(v)));
        if m.holding() && !(v.zoom() < 1.0) {
            m.land_maximize(now);
        }
        m.update(h, now)
    }

    /// Maximize-follow (2026-11-03): a maximize keeps the room and holds the canvas in it while the desktop zooms in, so
    /// it grows in place; then follow takes over with no jump and centres it on the head. Un-maximizing goes back to
    /// the room exactly as it was left, wherever the head went meanwhile, from follow or locked.
    #[test]
    fn a_maximize_holds_the_room_follows_and_restores_it() {
        for focus in [0usize, 2] {
            for look_off in [0.0f32, 9.0] {
                // looking right at the desktop, or 9 degrees beside it
                for via_locked in [false, true] {
                    // Ctrl+Alt+V to locked while maximized
                    let mut v = ViewAnimator::new(220.0, 300.0);
                    let mut m = ModeController::default();
                    let mut t = 0.0;
                    m.turn_anchor(axis_angle(0.0, 0.0, 1.0, 20.0)); // the room, turned by earlier switches
                    v.set_target(false, focus as u32, 40, t);
                    let room = m.update(Quat::default(), t);
                    assert!(quat_angle(room, axis_angle(0.0, 0.0, 1.0, 20.0)) < 1e-5);
                    // The head looking at the desktop in the room (head * aim = room puts it straight ahead), maybe
                    // off it.
                    let head = quat_mul(
                        quat_mul(room, quat_conj(aim_for(focus, 1.0))),
                        axis_angle(0.0, 0.0, 1.0, look_off),
                    );
                    while t < 300.0 {
                        assert!(quat_angle(frame(&mut v, &mut m, head, t), room) < 1e-5); // the overview: world-locked
                        t += 1.0;
                    }
                    v.set_target(true, focus as u32, 40, t); // maximize: the zoom in, and follow
                    m.set_mode(ViewMode::Follow, t, Change::Maximize);
                    assert!(m.holding() && !m.settled() && m.mode() == ViewMode::Follow);
                    let mut last = room;
                    let mut worst = 0.0f32;
                    let end = t + 1500.0;
                    while t < end {
                        let held = m.holding();
                        let a = frame(&mut v, &mut m, head, t);
                        if held && m.holding() {
                            assert!(quat_angle(a, room) < 1e-5); // in place while it zooms in
                        }
                        if look_off == 0.0 {
                            assert!(quat_angle(a, room) < rad(0.05)); // looking at it: nothing moves at all
                        }
                        worst = worst.max(quat_angle(a, last));
                        last = a;
                        t += 1.0;
                    }
                    assert!(!m.holding() && v.zoom() == 1.0);
                    assert!(worst < rad(0.2)); // per ms: follow takes over without a jump
                    if look_off == 0.0 && !via_locked && focus == 0 {
                        // Without the hold (a plain blend into follow as the zoom starts) the same maximize swings the
                        // desktop toward the room's centre and back while the spring catches up: what the hold
                        // prevents.
                        let mut v2 = ViewAnimator::new(220.0, 300.0);
                        let mut m2 = ModeController::default();
                        m2.turn_anchor(axis_angle(0.0, 0.0, 1.0, 20.0));
                        v2.set_target(false, 0, 40, 0.0);
                        m2.update(head, 0.0);
                        v2.advance(300.0);
                        v2.set_target(true, 0, 40, 300.0);
                        m2.set_mode(ViewMode::Follow, 300.0, Change::Blend);
                        let mut swing = 0.0f32;
                        let mut u = 300.0;
                        while u < 1800.0 {
                            v2.advance(u);
                            m2.set_aim(aim_for(v2.focus() as usize, focus_weight(&v2)));
                            swing = swing.max(quat_angle(m2.update(head, u), room));
                            u += 1.0;
                        }
                        assert!(swing > rad(3.0));
                    }
                    assert!(off_centre(&view_from(head, last), focus, 1.0) < 0.1); // then centred on the head
                    if via_locked {
                        m.set_mode(ViewMode::Locked, t, Change::Blend); // a manual change: blends as before
                        let end = t + 400.0;
                        while t < end {
                            frame(&mut v, &mut m, head, t);
                            t += 1.0;
                        }
                        assert!(
                            off_centre(
                                &view_from(head, frame(&mut v, &mut m, head, t)),
                                focus,
                                1.0
                            ) < 0.05
                        );
                    }
                    // The head turns away; the maximized desktop comes along, the room does not move.
                    let away = quat_mul(head, axis_angle(0.0, 0.0, 1.0, -40.0));
                    let end = t + 1500.0;
                    while t < end {
                        last = frame(&mut v, &mut m, away, t);
                        t += 1.0;
                    }
                    assert!(off_centre(&view_from(away, last), focus, 1.0) < 0.1);
                    assert!(quat_angle(last, room) > rad(30.0)); // (pinning here, Change::Blend, would move every desktop)
                    // Un-maximize: the zoom out, and anchor back in the room as it was, wherever the head is.
                    v.set_target(false, focus as u32, 40, t);
                    m.set_mode(ViewMode::Anchor, t, Change::Restore);
                    worst = 0.0;
                    let end = t + 600.0;
                    while t < end {
                        let a = frame(&mut v, &mut m, away, t);
                        worst = worst.max(quat_angle(a, last));
                        last = a;
                        t += 1.0;
                    }
                    assert!(worst < rad(0.5)); // per ms: a blend, never a jump
                    assert!(quat_angle(last, room) < 1e-5 && m.settled()); // exactly the room
                    t += 8.0;
                    assert!(quat_angle(frame(&mut v, &mut m, head, t), room) < 1e-5); // and world-locked there again
                }
            }
        }
    }

    /// The follow / locked choice sticks for the session, so a maximize can go straight into locked: held in place
    /// while the desktop zooms in, then a blend (no jump) onto the head's view with the desktop dead centre;
    /// un-maximized, the room is exactly as it was.
    #[test]
    fn a_maximize_straight_into_locked_holds_then_blends_dead_centre() {
        for focus in [0usize, 2] {
            let mut v = ViewAnimator::new(220.0, 300.0);
            let mut m = ModeController::default();
            let mut t = 0.0;
            m.turn_anchor(axis_angle(0.0, 0.0, 1.0, -25.0));
            v.set_target(false, focus as u32, 40, t);
            let room = m.update(Quat::default(), t);
            let head = quat_mul(
                quat_mul(room, quat_conj(aim_for(focus, 1.0))),
                axis_angle(0.0, 0.0, 1.0, 7.0),
            ); // 7 deg off
            while t < 300.0 {
                frame(&mut v, &mut m, head, t);
                t += 1.0;
            }
            v.set_target(true, focus as u32, 40, t);
            m.set_mode(ViewMode::Locked, t, Change::Maximize);
            assert!(m.holding() && m.mode() == ViewMode::Locked);
            let mut last = room;
            let mut worst = 0.0f32;
            let end = t + 800.0;
            while t < end {
                let held = m.holding();
                let a = frame(&mut v, &mut m, head, t);
                if held && m.holding() {
                    assert!(quat_angle(a, room) < 1e-5); // in place while it zooms in
                }
                worst = worst.max(quat_angle(a, last));
                last = a;
                t += 1.0;
            }
            assert!(!m.holding() && m.settled());
            assert!(worst < rad(0.5)); // per ms: the blend onto the head, never a jump
            assert!(off_centre(&view_from(head, last), focus, 1.0) < 0.05); // locked: dead centre
            let away = quat_mul(head, axis_angle(0.0, 0.0, 1.0, 35.0));
            t += 1.0;
            assert!(
                off_centre(&view_from(away, frame(&mut v, &mut m, away, t)), focus, 1.0) < 0.05
            ); // and rigidly so
            v.set_target(false, focus as u32, 40, t);
            m.set_mode(ViewMode::Anchor, t, Change::Restore);
            let end = t + 600.0;
            while t < end {
                last = frame(&mut v, &mut m, away, t);
                t += 1.0;
            }
            assert!(quat_angle(last, room) < 1e-5);
        }
    }

    /// A recentre while maximized moves the room as any recentre does: un-maximized, the overview comes back with that
    /// desktop straight ahead of the new reference. A maximize again mid-way back starts from where the canvas is.
    #[test]
    fn a_recentre_while_maximized_moves_the_room_and_a_maximize_mid_way_back_holds() {
        let mut v = ViewAnimator::new(220.0, 300.0);
        let mut m = ModeController::default();
        let mut t = 0.0;
        v.set_target(true, 0, 40, t);
        m.set_mode(ViewMode::Follow, t, Change::Maximize);
        while t < 1500.0 {
            frame(&mut v, &mut m, axis_angle(0.0, 0.0, 1.0, 50.0), t);
            t += 1.0;
        }
        m.recenter(); // PoseState's reference moves to the head: the relative head is the identity again
        let end = t + 200.0;
        while t < end {
            frame(&mut v, &mut m, Quat::default(), t);
            t += 1.0;
        }
        v.set_target(false, 0, 40, t);
        m.set_mode(ViewMode::Anchor, t, Change::Restore);
        let mut last = frame(&mut v, &mut m, Quat::default(), t);
        let end = t + 120.0;
        while t < end {
            last = frame(&mut v, &mut m, Quat::default(), t); // part way back
            t += 1.0;
        }
        v.set_target(true, 0, 40, t);
        m.set_mode(ViewMode::Follow, t, Change::Maximize);
        t += 1.0;
        assert!(quat_angle(frame(&mut v, &mut m, Quat::default(), t), last) < rad(0.5)); // held where it was: no jump
        let end = t + 1500.0;
        while t < end {
            last = frame(&mut v, &mut m, Quat::default(), t);
            t += 1.0;
        }
        v.set_target(false, 0, 40, t);
        m.set_mode(ViewMode::Anchor, t, Change::Restore);
        let end = t + 600.0;
        while t < end {
            last = frame(&mut v, &mut m, axis_angle(0.0, 0.0, 1.0, -30.0), t);
            t += 1.0;
        }
        assert!(quat_angle(last, aim_for(0, 1.0)) < 1e-5);
        assert!(off_centre(&view_from(Quat::default(), last), 0, 0.0) < 0.05); // the overview, desktop 0 ahead
    }
}
