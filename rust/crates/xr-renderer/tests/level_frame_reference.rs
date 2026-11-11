//! Twin of `spike/tests/level_frame_test.cpp` (the ctest `level_frame`), its PoseState::reference half: what "level"
//! is measured against. XRLinuxDriver publishes the head's pose relative to the pose it captured as calibration
//! finished, wherever the glasses were then, so the feed's z axis is not gravity: PoseState::reference has to give the
//! level bar (canvas_up, desktop_tilt) and the background's floor (environment_view) a frame that is level anyway.
//! Every check of the C++ test, with its tolerances. (`--dump`, the level contract's input, stays the C++ tool's.)

use xr_renderer::environment::environment_view;
use xr_renderer::pose::{FeedSample, PoseState, Quat, apply, canvas_up, quat_conj, quat_mul, quat_to_mat3, transpose};
use xr_renderer::view::{Canvas, Placement, Size, Vec3, desktop_tilt, overview_layout};

const PI: f32 = 3.14159265358979;

fn rad(deg: f32) -> f32 {
    deg * PI / 180.0
}

fn deg(r: f32) -> f32 {
    r * 180.0 / PI
}

/// `std::remainder` (IEEE: the quotient rounded to even).
fn remainder(x: f32, y: f32) -> f32 {
    let n = (f64::from(x) / f64::from(y)).round_ties_even();
    (f64::from(x) - n * f64::from(y)) as f32
}

fn axis_angle(x: f32, y: f32, z: f32, degrees: f32) -> Quat {
    let n = (x * x + y * y + z * z).sqrt();
    let h = rad(degrees) / 2.0;
    let s = h.sin() / n;
    Quat { x: x * s, y: y * s, z: z * s, w: h.cos() }
}

/// Driver frame: x forward, y left, z up. Pitch +down, tip +right (as worn).
fn head_at(yaw: f32, pitch: f32, tip: f32) -> Quat {
    quat_mul(quat_mul(axis_angle(0.0, 0.0, 1.0, yaw), axis_angle(0.0, 1.0, 0.0, pitch)), axis_angle(1.0, 0.0, 0.0, tip))
}

/// Someone at work: turns between desktops and looks a little up and down, the head never quite straight but straight
/// on average. Deterministic, so a failure repeats.
fn working(t: f32) -> Quat {
    head_at(
        25.0 * (0.21 * t).sin() + 12.0 * (0.53 * t + 1.0).sin(),
        -3.0 + 6.0 * (0.33 * t).sin(),
        2.5 * (0.7 * t).sin() + 1.5 * (1.9 * t + 1.0).sin(),
    )
}

/// The driver: a gravity-aligned pose comes out relative to the reference it captured.
struct Run {
    driver_reference: Quat,
    pose: PoseState,
    now: f64,
    device: f32,
}

impl Run {
    fn new(reference: Quat) -> Self {
        Self { driver_reference: reference, pose: PoseState::default(), now: 1000.0, device: 0.0 }
    }
    fn feed(&mut self, head: Quat) {
        self.now += 1000.0 / 120.0;
        self.device += 1000.0 / 120.0;
        let orientation = quat_mul(quat_conj(self.driver_reference), head);
        self.pose.update(Some(FeedSample { orientation, device_ms: self.device, ..FeedSample::default() }), self.now);
    }
    fn recentre(&mut self, head: Quat) {
        self.pose.recenter();
        self.feed(head);
    }
    fn hold(&mut self, head: Quat, seconds: f32) {
        let mut t = 0.0f32;
        while t < seconds {
            self.feed(head);
            t += 1.0 / 120.0;
        }
    }
    fn work(&mut self, seconds: f32, from: f32) {
        let mut t = 0.0f32;
        while t < seconds {
            self.feed(working(from + t));
            t += 1.0 / 120.0;
        }
    }
}

const CANVAS: Canvas = Canvas::new(1920.0, 1200.0, 0.41421356, 0.25888);

fn placed() -> [Placement; 3] {
    // Cody's desktops in the run that showed it (profiles.json, 2026-11-03).
    let p = |x: f32, y: f32, d: f32| Placement { x: rad(x), y: rad(y), distance: d, curvature: 0.0, roll: 0.0 };
    [p(0.7, 9.0, 0.33), p(34.5, 8.5, 0.31), p(-5.2, -15.7, 0.69)]
}

/// Radians; NaN where nothing is measured.
fn tilts(frame: Quat) -> Vec<f32> {
    let three = [Size { w: 1920.0, h: 1200.0 }; 3];
    let tiles = overview_layout(&three, 1920.0, 1200.0, 20.0);
    let up = canvas_up(frame, Quat::default());
    let placed = placed();
    tiles
        .iter()
        .zip(&placed)
        .map(|(t, p)| desktop_tilt(&CANVAS, t, p, &Vec3 { x: up[0], y: up[1], z: up[2] }).unwrap_or(f32::NAN))
        .collect()
}

/// The worst error of the tilts the renderer would report against those true gravity gives, in degrees.
fn worst_error(run: &Run, true_reference: Quat) -> f32 {
    let Some(reference) = run.pose.reference() else { return 1e9 };
    let (got, want) = (tilts(reference), tilts(true_reference));
    let mut worst = 0.0f32;
    for (g, w) in got.iter().zip(&want) {
        let d = remainder(g - w, 2.0 * PI);
        worst = worst.max(if d.is_nan() { 1e9 } else { deg(d).abs() });
    }
    worst
}

/// The floor: the angle between the world's up as the background draws it and the true one.
fn floor_error(run: &Run, head: Quat) -> f32 {
    let view = environment_view(run.pose.relative(), run.pose.reference());
    let got = apply(&view, [0.0, 1.0, 0.0]);
    let v = apply(&transpose(&quat_to_mat3(head)), [0.0, 0.0, 1.0]); // true up in the head's driver frame
    let want = [-v[1], v[2], -v[0]]; // driver -> GL
    let cross = got[0] * want[0] + got[1] * want[1] + got[2] * want[2];
    deg(cross.clamp(-1.0, 1.0).acos())
}

fn odd_references() -> [(&'static str, Quat); 5] {
    // The reference the live feed implied at 2026-11-03 19:45 with the glasses on; calibration finished with them
    // upside down, 165 degrees from how they were then worn.
    [
        ("live", live()),
        ("upside down on a desk", axis_angle(1.0, 0.0, 0.0, 180.0)),
        ("lying on one arm", axis_angle(1.0, 0.0, 0.0, 90.0)),
        ("hanging, lenses down", axis_angle(0.0, 1.0, 0.0, 88.0)),
        ("hanging, lenses up", axis_angle(0.0, 1.0, 0.0, -70.0)),
    ]
}

fn live() -> Quat {
    quat_conj(Quat { x: 0.539, y: 0.826, z: 0.163, w: -0.014 })
}

#[test]
fn the_feeds_z_is_not_up() {
    // The glasses are worn level and the view recentred: every desktop is level, whatever the feed's frame.
    for (name, reference) in odd_references() {
        let mut run = Run::new(reference);
        run.recentre(Quat::default());
        let error = worst_error(&run, Quat::default());
        assert!(error < 0.5, "{name}: tilts {error:.1} degrees wrong");
    }
}

#[test]
fn a_recentre_with_the_head_tipped_is_still_found() {
    // Tipped or pitched at the recentre, then the head goes back to how it is held: the tilts come to the truth.
    for (name, reference) in odd_references() {
        for tip in [-20.0, 20.0, 8.0] {
            for pitch in [0.0, 12.0, -10.0] {
                let mut run = Run::new(reference);
                let tipped = head_at(5.0, pitch, tip);
                run.recentre(tipped);
                run.work(45.0, 0.0);
                let error = worst_error(&run, tipped);
                assert!(error < 2.5, "{name} tip {tip} pitch {pitch}: tilts {error:.1} degrees wrong");
            }
        }
    }
}

#[test]
fn a_recentre_keeps_what_was_learned() {
    // Up lives in the feed's frame, which a recentre does not move: after a minute of use a tipped recentre reads true
    // at once.
    for (name, reference) in odd_references() {
        for tip in [-20.0, 20.0] {
            let mut run = Run::new(reference);
            let tipped = head_at(-15.0, 8.0, tip);
            run.recentre(Quat::default());
            run.work(60.0, 0.0);
            run.recentre(tipped);
            let error = worst_error(&run, tipped);
            assert!(error < 2.5, "{name}, recentred tipped {tip}: tilts {error:.1} degrees wrong");
        }
    }
    // A recentre further from up than a neck goes means up is not up any more (lying on your side): level as you see it.
    let mut run = Run::new(live());
    run.recentre(Quat::default());
    run.work(60.0, 0.0);
    run.recentre(head_at(0.0, 0.0, 80.0));
    let reference = run.pose.reference().expect("a reference");
    for t in tilts(reference) {
        assert!(deg(t).abs() < 2.0, "lying: tilt {}", deg(t));
    }
}

#[test]
fn glasses_worn_straight_at_the_start_keep_exact_gravity() {
    // Where the driver's frame is good it is used from the first sample, with no time to learn.
    for heading in [0.0, 70.0, -130.0] {
        for tip in [-20.0, 0.0, 20.0] {
            for pitch in [0.0, 25.0, -20.0] {
                let mut run = Run::new(axis_angle(0.0, 0.0, 1.0, heading));
                let tipped = head_at(-30.0, pitch, tip);
                run.recentre(tipped);
                let error = worst_error(&run, tipped);
                assert!(error < 0.05, "heading {heading} tip {tip} pitch {pitch}: {error}");
            }
        }
    }
}

#[test]
fn a_reference_that_looks_plausible_is_corrected_in_use() {
    // In a hand at the start, 30 degrees off: near enough to be believed, so wrong at first; the head shows it up.
    for reference in [axis_angle(1.0, 0.0, 0.0, 30.0), axis_angle(1.0, 0.0, 0.0, -35.0), head_at(0.0, 20.0, 25.0)] {
        let mut run = Run::new(reference);
        run.recentre(Quat::default());
        run.work(45.0, 0.0);
        let error = worst_error(&run, Quat::default());
        assert!(error < 2.5, "plausible reference: tilts {error:.1} degrees wrong");
    }
}

#[test]
fn normal_use_does_not_wander() {
    // Ten minutes. A start on the feed's z holds from the first moment; one on the head alone has up to 6 degrees for
    // its first half minute, then the same.
    for reference in [Quat::default(), live()] {
        let mut run = Run::new(reference);
        run.recentre(Quat::default());
        for i in 0..40 {
            run.work(15.0, i as f32 * 15.0);
            let error = worst_error(&run, Quat::default());
            let limit = if reference.w == 1.0 || i >= 2 { 2.5 } else { 6.0 };
            assert!(error < limit, "after {} s: tilts {error:.1} degrees wrong", (i + 1) * 15);
        }
    }
}

#[test]
fn putting_the_glasses_down_teaches_nothing() {
    // Off the head for a minute, in any position, and back on with no recentre: nothing learned from a desk.
    for desk in [
        axis_angle(1.0, 0.0, 0.0, 180.0),
        axis_angle(1.0, 0.0, 0.0, 90.0),
        axis_angle(0.0, 1.0, 0.0, 80.0),
        axis_angle(1.0, 0.0, 0.0, -100.0),
    ] {
        let mut run = Run::new(live());
        run.recentre(Quat::default());
        run.work(30.0, 0.0);
        run.hold(desk, 60.0);
        run.work(2.0, 30.0);
        let error = worst_error(&run, Quat::default());
        assert!(error < 2.5, "put down: tilts {error:.1} degrees wrong");
    }
}

#[test]
fn a_head_held_tipped_is_the_new_level() {
    // No gravity to go on, so a tilt held for minutes is taken as how you sit: "level" is level as you see it.
    let mut run = Run::new(live());
    run.recentre(Quat::default());
    run.work(30.0, 0.0);
    run.hold(head_at(0.0, 0.0, 15.0), 300.0);
    let reference = run.pose.reference().expect("a reference");
    let up = canvas_up(reference, Quat::default());
    let head = [rad(15.0).sin(), rad(15.0).cos(), 0.0]; // the head's up in the canvas: tipped right
    assert!((up[0] * head[0] + up[1] * head[1] + up[2] * head[2]).abs() > rad(1.5).cos());
}

#[test]
fn the_floor_is_down() {
    // A floor levelled to the feed's z is on the ceiling when calibration finished upside down.
    for (name, reference) in odd_references() {
        let mut run = Run::new(reference);
        run.recentre(Quat::default());
        let error = floor_error(&run, Quat::default());
        assert!(error < 0.5, "{name}: floor {error:.1} degrees off");
        run.work(30.0, 0.0);
        let settled = floor_error(&run, working(30.0));
        assert!(settled < 2.5, "{name}: floor {settled:.1} degrees off in use");
    }
    let mut good = Run::new(Quat::default());
    let tipped = head_at(10.0, 12.0, 20.0);
    good.recentre(tipped);
    assert!(floor_error(&good, tipped) < 0.05); // ... and level under a tipped recentre, as it always was
}

#[test]
fn reference_stays_a_rotation() {
    // Exactly opposite the feed's z (the shortest turn has no one axis), every case stays a unit quaternion that puts
    // up up.
    for reference in [
        axis_angle(1.0, 0.0, 0.0, 180.0),
        axis_angle(0.0, 1.0, 0.0, 180.0),
        axis_angle(1.0, 1.0, 0.0, 180.0),
        axis_angle(1.0, 0.0, 0.0, 179.99),
    ] {
        let mut run = Run::new(reference);
        run.recentre(Quat::default());
        let r = run.pose.reference().expect("a reference");
        assert!((r.x * r.x + r.y * r.y + r.z * r.z + r.w * r.w - 1.0).abs() < 1e-4);
        let up = canvas_up(r, Quat::default());
        assert!(up[0].abs() < 1e-3 && (up[1] - 1.0).abs() < 1e-3 && up[2].abs() < 1e-3, "{up:?}");
    }
    assert!(PoseState::default().reference().is_none()); // before the first sample there is none, as before
}
