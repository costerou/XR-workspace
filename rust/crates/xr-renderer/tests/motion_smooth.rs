//! Port of every check of ctest `motion_smooth` (spike/tests/motion_smooth_test.cpp), plus the replay golden: the
//! smoother driven by golden/motion_smooth.cpp's loop over its fixtures, byte-equal with the C++ (golden/motion_smooth.txt).

use xr_renderer::motion_smooth::{MotionSmoother, motion_smooth_params};
use xr_renderer::pose::{Quat, quat_angle};

const DEG: f32 = std::f32::consts::PI / 180.0; // 3.14159265f / 180.f
fn yaw(deg: f32) -> Quat {
    Quat { x: 0.0, y: 0.0, z: (deg * DEG / 2.0).sin(), w: (deg * DEG / 2.0).cos() }
}
fn yaw_of(q: Quat) -> f32 {
    2.0 * q.z.atan2(q.w) / DEG
}
fn between(a: Quat, b: Quat) -> f32 {
    quat_angle(a, b) / DEG
}
/// The measured cadence: GAPS[i % 3] ms between samples.
const GAPS: [f32; 3] = [15.0, 15.0, 20.0];
#[derive(Default)]
struct Feed {
    t: f32,
    angle: f32,
    i: usize,
}
impl Feed {
    fn advance(&mut self, dps: f32) {
        let gap = GAPS[self.i % 3];
        self.i += 1;
        self.t += gap;
        self.angle += dps * gap / 1000.0;
    }
}

#[test]
fn params_take_tau_5_to_80_ms() {
    assert!(
        motion_smooth_params(f32::NAN).is_none()
            && motion_smooth_params(4.9).is_none()
            && motion_smooth_params(80.5).is_none()
    );
    assert_eq!(motion_smooth_params(25.0).unwrap().tau_ms, 25.0);
}

#[test]
fn a_constant_speed_moves_every_frame_by_the_same_amount() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    let (dps, frame) = (30.0f32, 1000.0f32 / 120.0);
    for _ in 0..6 {
        m.update(yaw(f.angle), f.t, 20.0, f64::from(f.t), None);
        f.advance(dps);
    }
    let mut now = f64::from(f.t);
    let mut next = f.t;
    let (mut previous, mut last_step, mut checked) = (0.0f32, -1.0f32, 0);
    let mut before = m.update(yaw(f.angle), f.t, 20.0, now, None);
    for k in 0..240 {
        now += f64::from(frame);
        while f64::from(next + GAPS[f.i % 3]) <= now {
            f.advance(dps);
            next = f.t;
        }
        let age = now as f32 - f.t;
        let shown = m.update(yaw(f.angle), f.t, 20.0 + age, now, None);
        let step = between(before, shown);
        if k > 60 {
            assert!((step - dps * frame / 1000.0).abs() < 0.002, "frame {k}: step {step}");
            checked += 1;
        }
        before = shown;
        previous = step;
        last_step = step;
    }
    assert!(checked > 100 && previous == last_step);
    // and it sits on the prediction: newest sample plus the speed times 20 ms plus the age.
    assert!((yaw_of(before) - (f.angle + dps * (20.0 + (now as f32 - f.t)) / 1000.0)).abs() < 0.01);
}

#[test]
fn a_stop_overshoots_a_bounded_amount_and_comes_back() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    let mut now = 0.0f64;
    let mut peak = 0.0f32;
    for _ in 0..30 {
        now = f64::from(f.t);
        m.update(yaw(f.angle), f.t, 30.0, now, None);
        f.advance(40.0);
    }
    for _ in 0..30 {
        f.advance(0.0);
        for _ in 0..2 {
            now += 8.333;
            peak = peak.max(yaw_of(m.update(yaw(f.angle), f.t, 30.0, now, None)));
        }
    }
    assert!(peak - f.angle < 0.8, "{peak} {}", f.angle); // constant-velocity extrapolation of 30 ms at 40 deg/s would be 1.2
    assert!((yaw_of(m.update(yaw(f.angle), f.t, 30.0, now + 8.333, None)) - f.angle).abs() < 0.02);
}

#[test]
fn the_added_rotation_is_bounded() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    for _ in 0..20 {
        m.update(yaw(f.angle), f.t, 40.0, f64::from(f.t), None);
        f.advance(2000.0);
    }
    assert!(m.max_predicted_deg() <= 6.001);
}

#[test]
fn the_turn_axis_is_seen_either_way() {
    // A head turning both ways about an axis 7 degrees forward of this frame's up, seen as 7 forward.
    let mut m = MotionSmoother::default();
    let (mut t, mut now, mut angle, mut dps) = (0.0f32, 0.0f64, 0.0f32, 40.0f32);
    let a = 7.0 * DEG;
    for k in 0..400 {
        if k % 100 == 0 {
            dps = -dps;
        }
        let half = angle * DEG / 2.0;
        let s = half.sin();
        m.update(Quat { x: a.sin() * s, y: 0.0, z: a.cos() * s, w: half.cos() }, t, 20.0, now, None);
        t += GAPS[k % 3];
        now = f64::from(t);
        angle += dps * GAPS[k % 3] / 1000.0;
    }
    assert!((m.turn_axis_forward_deg() - 7.0).abs() < 0.2, "{}", m.turn_axis_forward_deg());
    assert!(m.turn_axis_left_deg().abs() < 0.2);
    assert!(m.turn_seconds() > 4.0);
}

#[test]
fn a_jump_is_taken_at_once_and_counted() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    for _ in 0..8 {
        m.update(yaw(f.angle + 50.0), f.t, 20.0, f64::from(f.t), None);
        f.advance(0.0);
    }
    let shown = m.update(yaw(0.0), f.t, 20.0, f64::from(f.t), None);
    assert!(between(shown, yaw(0.0)) < 0.01 && m.snaps() == 1);
}

#[test]
fn a_hard_turn_is_a_head_not_a_jump() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    let mut now = 0.0f64;
    let mut before = Quat::default();
    let mut still = 0;
    for k in 0..40 {
        f.advance(700.0);
        for _ in 0..2 {
            now += 8.333;
            let shown = m.update(yaw(f.angle), f.t, 30.0, now, None);
            if k > 2 && between(before, shown) < 1.0 {
                still += 1;
            }
            before = shown;
        }
    }
    assert!(m.snaps() == 0 && still == 0);
}

#[test]
fn a_stalled_feed_stops_carrying_speed() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    let mut now = 0.0f64;
    for _ in 0..12 {
        f.advance(200.0);
        now = f64::from(f.t);
        m.update(yaw(f.angle), f.t, 30.0, now, None);
    }
    let (mut furthest, mut settled) = (0.0f32, 0.0f32);
    for _ in 0..48 {
        now += 8.333;
        settled = between(m.update(yaw(f.angle), f.t, 40.0, now, None), yaw(f.angle));
        furthest = furthest.max(settled);
    }
    assert!(furthest <= 6.0 + 200.0 * 0.04); // the bound, then speed for stall_ms at the very most
    assert!(settled <= 6.01);
}

#[test]
fn a_long_gap_has_no_velocity_and_a_repeat_keeps_the_history() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    for _ in 0..6 {
        m.update(yaw(f.angle), f.t, 30.0, f64::from(f.t), None);
        f.advance(10.0);
    }
    let later = f.t + 500.0;
    m.update(yaw(f.angle), later, 30.0, f64::from(later), None);
    assert_eq!(m.last_predicted_deg(), 0.0);
    let same = m.update(yaw(f.angle), later, 30.0, f64::from(later + 8.0), None);
    assert!(between(same, yaw(f.angle)) < 0.2);
}

#[test]
fn a_new_clock_epoch_forgets_the_history() {
    let mut m = MotionSmoother::default();
    let mut f = Feed::default();
    for _ in 0..6 {
        m.update(yaw(f.angle), f.t, 30.0, f64::from(f.t), None);
        f.advance(10.0);
    }
    m.update(yaw(f.angle), 0.0, 30.0, f64::from(f.t + 8.0), None);
    assert_eq!(m.last_predicted_deg(), 0.0);
}

#[test]
fn a_measured_velocity_replaces_the_estimate() {
    let mut m = MotionSmoother::default();
    let (mut t, mut angle) = (0.0f32, 0.0f32);
    let dps = 30.0f32;
    let rad_per_ms = dps * DEG / 1000.0;
    for k in 0..40 {
        t += 4.1667;
        angle += dps * 4.1667 / 1000.0;
        if k % 2 != 0 {
            m.update(yaw(angle), t, 20.0, f64::from(t), Some([0.0, 0.0, rad_per_ms]));
        }
    }
    assert!((m.last_predicted_deg() - dps * 0.020).abs() < 0.001);
    t += 4.1667;
    angle += dps * 4.1667 / 1000.0;
    m.update(yaw(angle), t, 20.0, f64::from(t), Some([0.0, 0.0, 0.0]));
    assert_eq!(m.last_predicted_deg(), 0.0);
    t += 4.1667;
    angle += dps * 4.1667 / 1000.0;
    m.update(yaw(angle), t, 20.0, f64::from(t), Some([f32::NAN, 0.0, 0.0]));
    assert!((m.last_predicted_deg() - dps * 0.020).abs() < 0.05);
}

#[test]
fn non_finite_input_never_poisons_the_state() {
    let mut m = MotionSmoother::default();
    let q = m.update(yaw(5.0), f32::NAN, 20.0, 0.0, None);
    assert!(between(q, yaw(5.0)) < 0.01);
    m.update(yaw(5.0), 10.0, f32::NAN, 10.0, None);
    assert_eq!(m.last_horizon_ms(), 0.0);
}

// Replay parity: spike/tests/pro2_smooth_replay.cpp, line for line, over golden/motion_smooth.cpp's cases.

mod replay {
    use std::collections::BTreeMap;
    use std::fmt::Write as _;

    use xr_renderer::cfmt::g;
    use xr_renderer::motion_smooth::{MotionSmoothParams, MotionSmoother};
    use xr_renderer::pose::{
        FeedSample, Mat3, ModeController, PoseState, Quat, apply, quat_angle, quat_conj, quat_mul, transpose, view_from,
    };
    use xr_renderer::predict::{PoseAge, angular_velocity, history_of, look_ahead_from, prediction_delta, prediction_horizon};
    use xr_renderer::stabilization::Stabilizer;

    struct In {
        host: f64,
        device: f32,
        q: Quat,
        omega: [f32; 3],
    }

    /// spike/tests/view_lean.hpp's `shown_by`: (yaw_deg, roll_deg).
    fn shown_by(view: &Mat3) -> (f32, f32) {
        let deg = (180.0 / std::f64::consts::PI) as f32;
        let ray = apply(&transpose(view), [0.0, 0.0, -1.0]);
        if ray[2] > -1e-4 {
            return (f32::NAN, f32::NAN);
        }
        let at = [ray[0] / -ray[2], ray[1] / -ray[2], -1.0];
        let beside = [at[0] + 0.01, at[1], -1.0];
        let a = apply(view, at);
        let b = apply(view, beside);
        let dx = b[0] / -b[2] - a[0] / -a[2];
        let dy = b[1] / -b[2] - a[1] / -a[2];
        (ray[0].atan2(-ray[2]) * deg, dy.atan2(dx) * deg)
    }

    fn p12(out: &mut String, v: f64) {
        out.push_str(&g(v, 12));
    }

    /// The replay's stdout and its stderr line, and its exit code.
    pub fn run(flags: &[&str], input: &str) -> (String, String, i32) {
        let mut o: BTreeMap<&str, String> = [
            ("--mode", "smooth"), ("--period", "8.3333"), ("--r2p", "16.6"), ("--phase", "0"),
            ("--stabilization", "25"), ("--tau", "35"), ("--cap", "40"), ("--constant", "0"), ("--publish-delay", "1"),
            ("--max-predict", "6"), ("--snap", "30"), ("--brake", "3"), ("--brake-floor", "4"), ("--read-jitter", "0"),
            ("--level", "0"),
        ]
        .into_iter()
        .map(|(k, v)| (k, v.to_string()))
        .collect();
        for pair in flags.chunks_exact(2) {
            let slot = o.get_mut(pair[0]).expect("a flag the replay knows");
            *slot = pair[1].to_string();
        }
        let num = |k: &str| -> f64 { o[k].parse().expect("a number") };
        let mode = o["--mode"].clone();
        let (mut out, mut err) = (String::new(), String::new());
        if !["hold", "legacy", "smooth", "wire"].contains(&mode.as_str()) {
            return (out, err, 2);
        }
        let wire = mode == "wire";
        let mut words = input.split_ascii_whitespace();
        let mut ins = Vec::new();
        let mut omega = [0.0f32; 3];
        'read: loop {
            let mut next = || words.next();
            let (Some(h), Some(d), Some(x), Some(y), Some(z), Some(w)) = (next(), next(), next(), next(), next(), next())
            else {
                break 'read;
            };
            let f = |s: &str| s.parse::<f32>().unwrap();
            if wire {
                match (next(), next(), next()) {
                    (Some(a), Some(b), Some(c)) => omega = [f(a), f(b), f(c)],
                    _ => return (out, err, 2),
                }
            }
            ins.push(In { host: h.parse().unwrap(), device: f(d), q: Quat { x: f(x), y: f(y), z: f(z), w: f(w) }, omega });
        }
        if ins.len() < 4 {
            return (out, err, 2);
        }
        let (period, r2p) = (num("--period"), num("--r2p"));
        let mut stabilizer = Stabilizer::new();
        stabilizer.strength(num("--stabilization") as i32);
        let mut pose = PoseState::default();
        let mut age = PoseAge::new();
        pose.level_reference(num("--level") > 0.0);
        let params = MotionSmoothParams {
            tau_ms: num("--tau") as f32,
            max_predict_deg: num("--max-predict") as f32,
            snap_deg: num("--snap") as f32,
            brake_power: num("--brake") as f32,
            brake_floor_rad_per_ms: num("--brake-floor") as f32 * 3.14159265f32 / 180000.0,
            ..MotionSmoothParams::default()
        };
        let mut smoother = MotionSmoother::new(params);
        let mut modes = ModeController::default();
        let cfg = look_ahead_from(&[num("--constant") as f32, 0.0, 0.0, num("--cap") as f32]);
        if mode != "hold" && cfg.is_none() {
            return (out, err, 2);
        }
        let mut centred = false;
        let (mut newest, mut jitter_step) = (0usize, 0u64);
        let start = ins[3].host + period + num("--phase");
        let end = ins.last().unwrap().host;
        let mut grid = start;
        while grid < end {
            // A read lands a little off the grid; the present does not (the phase-aware estimate absorbs it).
            let hash = (jitter_step.wrapping_mul(2654435761) >> 8) & 255;
            jitter_step += 1;
            let read = grid + num("--read-jitter") * (hash as f64 / 255.0 - 0.5);
            while newest + 1 < ins.len() && ins[newest + 1].host + num("--publish-delay") <= read {
                newest += 1;
            }
            if newest < 2 {
                grid += period;
                continue;
            }
            let f = FeedSample {
                orientation: ins[newest].q,
                device_ms: ins[newest].device,
                older: [ins[newest - 1].q, ins[newest - 2].q],
                older_ms: [ins[newest - 1].device, ins[newest - 2].device],
                sample_ns: 0,
            };
            let raw_ms = f.device_ms;
            let mut stabilized = f;
            stabilized.orientation = stabilizer.update(stabilized.orientation, f64::from(stabilized.device_ms));
            pose.update(Some(stabilized), read);
            let mut relative = pose.relative();
            let a = age.observe(read, raw_ms);
            let (mut horizon_shown, mut predicted) = (0.0f32, 0.0f32);
            if pose.tracking() && mode != "hold" {
                // The wire's age is from the sample's arrival (motion_age_ms), the feed's from its device time (PoseAge).
                let sample_age = if wire { read - ins[newest].host } else { a.unwrap_or(0.0) };
                let horizon = prediction_horizon(cfg.as_ref(), Some(r2p - (read - grid)), sample_age, period);
                if mode == "legacy" {
                    if let (Some(omega), Some(horizon)) = (angular_velocity(&history_of(&f)), horizon) {
                        let delta = prediction_delta(&omega, horizon * stabilizer.prediction_gain(), 15.0);
                        relative = quat_mul(relative, delta);
                        horizon_shown = horizon;
                        predicted = quat_angle(Quat::default(), delta) * (180.0 / std::f64::consts::PI) as f32;
                    }
                } else if let Some(horizon) = horizon {
                    let o = ins[newest].omega;
                    let measured = wire.then(|| [o[0] / 1000.0, o[1] / 1000.0, o[2] / 1000.0]); // rad/s to rad/ms
                    relative = smoother.update(relative, f.device_ms, horizon, read, measured);
                    horizon_shown = smoother.last_horizon_ms();
                    predicted = smoother.last_predicted_deg();
                }
            }
            let (yaw, roll) = shown_by(&view_from(relative, modes.update(relative, read)));
            if !centred {
                let turn = pose.level_turn();
                let c = quat_mul(quat_mul(stabilized.orientation, turn), quat_conj(pose.relative()));
                out.push_str("center");
                for v in [c.x, c.y, c.z, c.w, turn.x, turn.y, turn.z, turn.w] {
                    out.push(' ');
                    p12(&mut out, f64::from(v));
                }
                out.push('\n');
                centred = true;
            }
            p12(&mut out, grid + r2p);
            for v in [raw_ms, relative.x, relative.y, relative.z, relative.w, horizon_shown, predicted, yaw, roll] {
                out.push(' ');
                p12(&mut out, f64::from(v));
            }
            out.push('\n');
            grid += period;
        }
        let s = |v: f32| g(f64::from(v), 6);
        writeln!(
            err,
            "snaps {} max_predicted_deg {} peak_speed_dps {} capped_frames {} turn_axis_forward_deg {} turn_axis_left_deg {} turn_seconds {}",
            smoother.snaps(),
            s(smoother.max_predicted_deg()),
            s(smoother.peak_speed_dps()),
            smoother.capped_frames(),
            s(smoother.turn_axis_forward_deg()),
            s(smoother.turn_axis_left_deg()),
            s(smoother.turn_seconds())
        )
        .unwrap();
        (out, err, 0)
    }
}

#[test]
fn the_replay_matches_the_cpp_byte_for_byte() {
    let golden = include_str!("../golden/motion_smooth.txt");
    let fixtures = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/");
    let mut produced = String::new();
    for header in golden.lines().filter(|l| l.starts_with("case ")) {
        let mut words = header.split(' ').skip(1);
        let fixture = words.next().unwrap();
        let flags: Vec<&str> = words.collect();
        let input = std::fs::read_to_string(format!("{fixtures}{fixture}")).unwrap();
        let (out, err, code) = replay::run(&flags, &input);
        produced.push_str(&format!("{header}\n{out}stderr {err}exit {code}\n"));
    }
    assert!(golden.lines().filter(|l| l.starts_with("case ")).count() >= 9);
    if produced != golden {
        let (a, b) = (golden.lines(), produced.lines());
        let (i, (c, r)) = a.zip(b).enumerate().find(|(_, (c, r))| c != r).unwrap_or((0, ("<length>", "<length>")));
        panic!("differs from golden/motion_smooth.txt at line {}:\n  C++:  {c}\n  Rust: {r}", i + 1);
    }
}
