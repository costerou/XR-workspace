//! The checks of ctest `stabilization` (spike/tests/stabilization_test.cpp), and trace parity with
//! spike/tests/stabilization_trace.cpp.
//!
//! The trace golden is the C++ trace's output on the fixture. To regenerate it, from the repository root:
//!   cmake -S spike -B BUILD && cmake --build BUILD --target stabilization_trace
//!   BUILD/stabilization_trace < rust/crates/xr-renderer/tests/fixtures/stabilization_trace.in \
//!     > rust/crates/xr-renderer/golden/stabilization_trace.txt

use xr_renderer::cfmt;
use xr_renderer::pose::{quat_angle, quat_mul, FeedSample, PoseState, Quat};
use xr_renderer::predict::{angular_velocity, prediction_delta, PoseHistory};
use xr_renderer::stabilization::Stabilizer;

const PI: f64 = 3.141592653589793;

fn check(ok: bool, why: &str) {
    assert!(ok, "FAIL: {why}");
}

fn turn(deg: f64) -> Quat {
    let a = deg * PI / 360.;
    Quat { x: (a.sin() * 0.6) as f32, y: 0.0, z: (a.sin() * 0.8) as f32, w: a.cos() as f32 }
}

fn angle(q: Quat) -> f64 {
    2. * (f64::from(q.x) * 0.6 + f64::from(q.z) * 0.8).atan2(f64::from(q.w)) * 180. / PI
}

fn per_rate(dt: f64, strength: i32) {
    let mut f = Stabilizer::new();
    f.strength(strength);
    let (mut raw, mut filtered) = (0.0, 0.0);
    let mut i = 0;
    while f64::from(i) < 60000. / dt {
        let t = f64::from(i) * dt;
        let a = 0.025 * (t * 0.001 * 2. * PI * 8.).sin() + 0.008 * (t * 0.001 * 2. * PI * 19.).sin();
        let q = f.update(turn(a), t);
        if t > 1000. {
            raw += a * a;
            filtered += angle(q).powi(2);
        }
        i += 1;
    }
    let reduction = 1. - (filtered / raw).sqrt();
    let required = match strength {
        25 => 0.25,
        60 => 0.45,
        _ => 0.60,
    };
    println!("dt {dt:.3} strength {strength} jitter reduction {:.2}%", 100. * reduction);
    check(reduction >= required, "still tremor reduction");
    for speed in [30., 90., 300.] {
        for onset in [200., 200.1, 203., 207., 211.] {
            f.reset();
            let mut worst: f64 = 0.0;
            let mut i = 0;
            while f64::from(i) < 1000. / dt {
                let t = f64::from(i) * dt;
                let a = speed * 0f64.max((t - onset).min(300.)) / 1000.;
                let out = angle(f.update(turn(a), t));
                if t > onset && t <= onset + 300. {
                    worst = worst.max((a - out).abs() / speed * 1000.);
                }
                check(out <= a + 1e-4, "no overshoot");
                if t > onset + 300. + dt && (out - a).abs() >= 1e-4 {
                    panic!("no stop tail: dt={dt} strength={strength} speed={speed} onset={onset} t={t} target={a} output={out:.9}");
                }
                i += 1;
            }
            check(worst < 2., "deliberate turn lag under 2 ms, including onset");
        }
    }
    f.reset();
    let mut i = 0;
    while f64::from(i) < 600000. / dt {
        let q = f.update(turn(23.), f64::from(i) * dt);
        check((angle(q) - 23.).abs() < 1e-4, "ten minute stationary drift");
        i += 1;
    }
    f.reset();
    let (mut sum, mut count) = (0.0, 0);
    let mut i = 0;
    while f64::from(i) < 600000. / dt {
        let t = f64::from(i) * dt;
        let q = f.update(turn(23. + 0.025 * (t * 0.001 * 2. * PI * 8.).sin()), t);
        if t > 540000. {
            sum += angle(q) - 23.;
            count += 1;
        }
        i += 1;
    }
    check((sum / f64::from(count)).abs() < 0.0001, "ten minute noisy still data adds no bias");
    // Recenter removes all filter history before the reference is taken.
    let mut pose = PoseState::default();
    let mut sample = FeedSample { orientation: f.update(turn(24.), 600000.), device_ms: 600000.0, ..Default::default() };
    pose.update(Some(sample), 600000.);
    pose.recenter();
    f.reset();
    sample.orientation = f.update(turn(25.), 600001.);
    sample.device_ms = 600001.0;
    pose.update(Some(sample), 600001.);
    check(quat_angle(pose.relative(), Quat::default()) < 1e-5, "recenter is immediate");
    f.strength(0);
    check((angle(f.update(turn(42.), 600001.)) - 42.).abs() < 1e-4, "live Off is immediate");
    f.strength(100);
    check((angle(f.update(turn(43.), 600002.)) - 43.).abs() < 1e-4, "live setting reset");
    let q = f.update(turn(44.), 1.);
    check((angle(q) - 44.).abs() < 1e-4, "clock restart resets");
    let q = f.update(turn(50.), 1.);
    check((angle(q) - 44.).abs() < 1e-4, "duplicates do not advance");
    let q = turn(44.);
    let q = Quat { x: -q.x, y: -q.y, z: -q.z, w: -q.w };
    check((angle(f.update(q, 2.)).abs() - 44.).abs() < 1e-4, "quaternion sign continuity");
}

#[test]
fn filter_at_1khz() {
    for strength in [25, 60, 100] {
        per_rate(1., strength);
    }
}

#[test]
fn filter_at_120hz() {
    for strength in [25, 60, 100] {
        per_rate(1000. / 120., strength);
    }
}

#[test]
fn filter_at_60hz() {
    for strength in [25, 60, 100] {
        per_rate(1000. / 60., strength);
    }
}

#[test]
fn prediction_gain() {
    for strength in [25, 60, 100] {
        let mut f = Stabilizer::new();
        f.strength(strength);
        let (mut before, mut after) = (0.0, 0.0);
        let tremor = |ms: f64| turn(0.025 * (ms * 0.001 * 2. * PI * 8.).sin());
        for t in 32..10000 {
            let h = PoseHistory {
                q: [tremor(f64::from(t)), tremor(f64::from(t - 16)), tremor(f64::from(t - 32))],
                device_ms: [t as f32, (t - 16) as f32, (t - 32) as f32],
            };
            let q = f.update(h.q[0], f64::from(t));
            let omega = angular_velocity(&h);
            check(omega.is_some(), "valid prediction history");
            let omega = omega.unwrap();
            let raw = quat_mul(h.q[0], prediction_delta(&omega, 12., 5.));
            let filtered = quat_mul(q, prediction_delta(&omega, 12. * f.prediction_gain(), 5.));
            if t > 1000 {
                before += angle(raw).powi(2);
                after += angle(filtered).powi(2);
            }
        }
        check(after < before * 0.5, "prediction does not amplify stationary jitter back into the view");
        f.reset();
        f.update(turn(0.), 0.);
        f.update(turn(0.03), 1.);
        check(f.prediction_gain() == 1.0, "deliberate turn preserves prediction");
        f.update(turn(0.03), 2.);
        check(f.prediction_gain() == 0.0, "stop does not extrapolate old motion");
    }
}

/// `std::cout << std::setprecision(10) << v` for a double (a float is promoted first).
fn out(v: f64) -> String {
    cfmt::g(v, 10)
}

/// spike/tests/stabilization_trace.cpp: stdin `device_ms x y z w`; stdout the time, then x y z w and the prediction
/// gain for Off/Light/Medium/Strong.
fn trace(input: &str) -> String {
    let mut filters: [Stabilizer; 4] = Default::default();
    for (f, s) in filters.iter_mut().zip([0, 25, 60, 100]) {
        f.strength(s);
    }
    let mut text = String::new();
    let mut words = input.split_ascii_whitespace();
    loop {
        let (Some(t), Some(x), Some(y), Some(z), Some(w)) = (words.next(), words.next(), words.next(), words.next(), words.next()) else {
            assert!(words.next().is_none(), "trailing words");
            break;
        };
        let t: f64 = t.parse().unwrap();
        let q = Quat { x: x.parse().unwrap(), y: y.parse().unwrap(), z: z.parse().unwrap(), w: w.parse().unwrap() };
        text += &out(t);
        for f in &mut filters {
            let o = f.update(q, t);
            for v in [o.x, o.y, o.z, o.w, f.prediction_gain()] {
                text.push(' ');
                text += &out(f64::from(v));
            }
        }
        text.push('\n');
    }
    text
}

#[test]
fn trace_matches_cpp() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let input = std::fs::read_to_string(dir.join("tests/fixtures/stabilization_trace.in")).unwrap();
    let golden = std::fs::read_to_string(dir.join("golden/stabilization_trace.txt")).unwrap();
    let rust = trace(&input);
    for (n, (r, c)) in rust.lines().zip(golden.lines()).enumerate() {
        assert_eq!(r, c, "line {}", n + 1);
    }
    assert_eq!(rust.len(), golden.len());
    assert!(rust == golden);
}
