//! `luma::extrapolate`: the checks of ctest `luma_predict` (spike/driver/tests/predict_test.cpp) that
//! are about extrapolation, each on the state `Predictor` would hand it (the filters are not ported),
//! and the golden: golden/extrapolate.cpp runs the C++ over a grid of configs, states and horizons
//! and prints every float's bits, and the Rust port must print the same lines.

use xr_pose::extrapolate::{extrapolate, PredictConfig, Predicted};
use xr_pose::MotionState;

// spike/driver/tests/head_sim.hpp, in f64.
const DEG: f64 = std::f64::consts::PI / 180.0;
type Q = [f64; 4]; // [w, x, y, z]
fn mul(a: Q, b: Q) -> Q {
    [
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ]
}
fn conj(q: Q) -> Q {
    [q[0], -q[1], -q[2], -q[3]]
}
fn exp_map(r: [f64; 3]) -> Q {
    let a = (r[0] * r[0] + r[1] * r[1] + r[2] * r[2]).sqrt();
    let k = if a < 1e-12 { 0.5 } else { (a / 2.0).sin() / a };
    [(a / 2.0).cos(), r[0] * k, r[1] * k, r[2] * k]
}
fn of(q: [f32; 4]) -> Q {
    q.map(f64::from)
}
fn to_f(q: Q) -> [f32; 4] {
    q.map(|v| v as f32)
}
/// Rotation vector of conj(a) * b: b as seen from a, body frame, radians.
fn rotvec(a: Q, b: Q) -> [f64; 3] {
    let mut d = mul(conj(a), b);
    if d[0] < 0.0 {
        d = d.map(|v| -v);
    }
    let s = (d[1] * d[1] + d[2] * d[2] + d[3] * d[3]).sqrt();
    if s < 1e-14 {
        return [0.0; 3];
    }
    let k = 2.0 * s.atan2(d[0]) / s;
    [d[1] * k, d[2] * k, d[3] * k]
}
fn angle_deg(a: Q, b: Q) -> f64 {
    let r = rotvec(a, b);
    (r[0] * r[0] + r[1] * r[1] + r[2] * r[2]).sqrt() / DEG
}
fn close(a: f64, b: f64, eps: f64) -> bool {
    (a - b).abs() <= eps
}
fn moving(q: Q, omega: [f32; 3], alpha: [f32; 3]) -> MotionState {
    MotionState {
        q: to_f(q),
        host_ns: 1_200_000_000,
        omega_rad_s: omega,
        alpha_rad_s2: alpha,
        valid: true,
        calibrated: true,
        ..MotionState::default()
    }
}

#[test]
fn constant_rate_lands_on_the_future() {
    let w = [0.3f32, -0.8, 2.0]; // about 125 deg/s, tumbling
    let st = moving(exp_map([0.4, 0.9, -1.1]), w, [0.0; 3]);
    let truth = mul(of(st.q), exp_map(w.map(|v| f64::from(v) * 0.02)));
    let out = extrapolate(&st, 20.0, &PredictConfig::default());
    assert!(angle_deg(truth, of(out.q)) < 0.01);
    assert!(close(f64::from(out.horizon_ms), 20.0, 1e-4));
    assert_eq!(out.target_ns, st.host_ns + 20_000_000);
    assert!(close(
        f64::from(out.predicted_deg),
        (0.09f64 + 0.64 + 4.0).sqrt() * 0.02 / DEG,
        0.01
    ));
}

#[test]
fn constant_acceleration_is_followed() {
    let (w0, a, h) = (1.5f32, 30.0f32, 0.02f64);
    let st = moving(exp_map([0.4, 0.9, -1.1]), [0.0, 0.0, w0], [0.0, 0.0, a]);
    let (w0, a) = (f64::from(w0), f64::from(a));
    let z = rotvec(
        of(st.q),
        of(extrapolate(&st, 20.0, &PredictConfig::default()).q),
    )[2];
    assert!(close(z, w0 * h + 0.5 * a * h * h, 1e-5));
    let off = PredictConfig {
        use_acceleration: false,
        ..PredictConfig::default()
    };
    assert!(close(
        rotvec(of(st.q), of(extrapolate(&st, 20.0, &off).q))[2],
        w0 * h,
        1e-3
    ));
}

#[test]
fn prediction_ignores_the_recentre_reference() {
    // The feed publishes conj(reference) * pose. A body-frame delta must be the same either side of it.
    let reference = exp_map([0.7, -0.2, 1.3]);
    let q0 = exp_map([0.2, 0.2, 0.2]);
    let w = [1.0f32, 2.0, -1.0];
    let a = extrapolate(&moving(q0, w, [0.0; 3]), 25.0, &PredictConfig::default());
    let b = extrapolate(
        &moving(mul(conj(reference), q0), w, [0.0; 3]),
        25.0,
        &PredictConfig::default(),
    );
    assert!(angle_deg(mul(conj(reference), of(a.q)), of(b.q)) < 0.01);
}

#[test]
fn clamped_to_the_renderers_limit() {
    let st = moving([1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 35.0], [0.0; 3]); // 2000 deg/s
    let out = extrapolate(&st, 40.0, &PredictConfig::default());
    assert!(close(angle_deg(of(st.q), of(out.q)), 15.0, 0.02));
    assert!(out.clamped);
}

#[test]
fn stop_clamp_ends_the_path_where_velocity_reaches_zero() {
    let st = moving([1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, -100.0]); // stops in 10 ms
    let stopped = 0.005 / DEG; // omega * t / 2
    let id = [1.0, 0.0, 0.0, 0.0];
    assert!(close(
        rotvec(id, of(extrapolate(&st, 40.0, &PredictConfig::default()).q))[2] / DEG,
        stopped,
        1e-3
    ));
    let off = PredictConfig {
        stop_clamp: false,
        ..PredictConfig::default()
    };
    assert!(rotvec(id, of(extrapolate(&st, 40.0, &off).q))[2] < -0.03); // would run on backwards
}

#[test]
fn still_gate_flag() {
    let mut st = moving(
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, (1.5 * DEG) as f32],
        [0.0; 3],
    );
    assert_eq!(
        extrapolate(&st, 25.0, &PredictConfig::default()).predicted_deg,
        0.0
    );
    let off = PredictConfig {
        still_gate: false,
        ..PredictConfig::default()
    };
    assert!(close(
        f64::from(extrapolate(&st, 25.0, &off).predicted_deg),
        1.5 * 0.025,
        1e-4
    ));
    st.omega_rad_s = [0.0, 0.0, (8.0 * DEG) as f32]; // above the gate, all of it
    assert!(close(
        f64::from(extrapolate(&st, 25.0, &PredictConfig::default()).predicted_deg),
        0.2,
        1e-3
    ));
}

#[test]
fn flags_turn_prediction_off() {
    let st = moving([1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 3.0], [0.0; 3]);
    let out = extrapolate(
        &st,
        25.0,
        &PredictConfig {
            enabled: false,
            ..PredictConfig::default()
        },
    );
    assert_eq!(out.q, st.q);
    assert_eq!(out.predicted_deg, 0.0);
}

#[test]
fn uncalibrated_is_never_extrapolated() {
    let st = MotionState {
        calibrated: false,
        ..moving([1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 3.0], [0.0; 3])
    };
    assert_eq!(extrapolate(&st, 25.0, &PredictConfig::default()).q, st.q);
}

#[test]
fn before_any_sample_predict_is_identity() {
    let out = extrapolate(&MotionState::default(), 25.0, &PredictConfig::default());
    assert!(out.q[0] == 1.0 && out.predicted_deg == 0.0);
}

// The golden.

fn bits(v: f32) -> String {
    format!("{:08x}", v.to_bits())
}
fn float(word: &str) -> f32 {
    f32::from_bits(u32::from_str_radix(word, 16).expect("hex float bits"))
}
fn flag(c: u8) -> bool {
    c == b'1'
}

#[test]
fn matches_the_cpp_bit_for_bit() {
    let golden = include_str!("../golden/extrapolate.txt");
    let (mut cfgs, mut states, mut horizons) = (Vec::new(), Vec::new(), Vec::new());
    let mut checked = 0;
    for (n, line) in golden.lines().enumerate() {
        let w: Vec<&str> = line.split(' ').collect();
        match w[0] {
            "cfg" => {
                let f = w[2].as_bytes();
                cfgs.push(PredictConfig {
                    enabled: flag(f[0]),
                    use_acceleration: flag(f[1]),
                    stop_clamp: flag(f[2]),
                    still_gate: flag(f[3]),
                    still_lo_dps: float(w[3]),
                    still_hi_dps: float(w[4]),
                    max_deg: float(w[5]),
                });
            }
            "state" => {
                let f = w[2].as_bytes();
                states.push(MotionState {
                    valid: flag(f[0]),
                    calibrated: flag(f[1]),
                    q: [float(w[3]), float(w[4]), float(w[5]), float(w[6])],
                    host_ns: w[7].parse().unwrap(),
                    omega_rad_s: [float(w[8]), float(w[9]), float(w[10])],
                    alpha_rad_s2: [float(w[11]), float(w[12]), float(w[13])],
                    ..MotionState::default()
                });
            }
            "horizon" => horizons.push(float(w[2])),
            "e" => {
                let (c, s, h): (usize, usize, usize) = (
                    w[1].parse().unwrap(),
                    w[2].parse().unwrap(),
                    w[3].parse().unwrap(),
                );
                let p: Predicted = extrapolate(&states[s], horizons[h], &cfgs[c]);
                let produced = format!(
                    "e {c} {s} {h} {} {} {} {} {} {} {} {}",
                    bits(p.q[0]),
                    bits(p.q[1]),
                    bits(p.q[2]),
                    bits(p.q[3]),
                    p.target_ns,
                    bits(p.horizon_ms),
                    bits(p.predicted_deg),
                    u8::from(p.clamped)
                );
                assert_eq!(
                    produced,
                    line,
                    "golden/extrapolate.txt line {}: Rust, then C++",
                    n + 1
                );
                checked += 1;
            }
            other => panic!(
                "golden/extrapolate.txt line {}: unknown record {other}",
                n + 1
            ),
        }
    }
    assert!(checked > 5000, "only {checked} cases");
}
