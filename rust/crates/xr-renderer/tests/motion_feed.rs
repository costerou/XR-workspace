//! The motion feed (D3): the MotionFeed checks of ctest `predict` (spike/tests/predict_test.cpp), `motion_delta`
//! bit-equal with the C++ over golden/motion_feed.cpp's grid (golden/motion_feed.txt), and MotionFeed reading a
//! PredictionWire that xr-pose's FeedWriter publishes under a private prefix (every segment created by this test with
//! IPC_EXCL, so a live driver's is never touched).

use std::sync::atomic::{AtomicU32, Ordering};

use xr_pose::MotionState;
use xr_pose::feed::{FeedError, FeedWriter, WriterOptions};
use xr_renderer::motion_feed::{FeedMotion, MotionFeed, motion_age_ms, motion_delta};
use xr_renderer::pose::{Quat, quat_angle, quat_mul};

fn near(a: f64, b: f64, eps: f64) -> bool {
    (a - b).abs() <= eps
}
fn axis_angle(x: f32, y: f32, z: f32, deg: f32) -> Quat {
    let h = deg * 3.14159265f32 / 360.0;
    let s = h.sin();
    Quat { x: x * s, y: y * s, z: z * s, w: h.cos() }
}
fn deg(a: Quat, b: Quat) -> f32 {
    quat_angle(a, b) * 180.0 / 3.14159265f32
}

#[test]
fn the_delta_from_the_drivers_gyro_motion() {
    // On a rotated base, 200 deg/s of yaw.
    let base = axis_angle(0.6, 0.8, 0.0, 70.0);
    let mut m = FeedMotion {
        q: base,
        host_ns: 5_000_000_000,
        omega_rad_s: [0.0, 0.0, (200.0 * 3.14159265f64 / 180.0) as f32],
        ..FeedMotion::default()
    };
    let predicted = quat_mul(m.q, motion_delta(&m, 20.0, 15.0));
    assert!(deg(predicted, quat_mul(base, axis_angle(0.0, 0.0, 1.0, 4.0))) < 0.01);
    assert!(near(f64::from(deg(motion_delta(&m, 200.0, 15.0), Quat::default())), 15.0, 0.01)); // clamped like prediction_delta
    assert!(deg(motion_delta(&m, 0.0, 15.0), Quat::default()) < 1e-4);
    assert!(deg(motion_delta(&m, -5.0, 15.0), Quat::default()) < 1e-4);
    m.omega_rad_s = [0.0, 0.0, (1.0 * 3.14159265f64 / 180.0) as f32]; // 1 deg/s: a still head's gyro noise, not extrapolated
    assert!(deg(motion_delta(&m, 30.0, 15.0), Quat::default()) < 1e-4);
    assert!(near(motion_age_ms(&m, m.host_ns + 3_500_000).unwrap(), 3.5, 1e-3));
    assert!(motion_age_ms(&m, m.host_ns + 50_000_000).is_some());
    assert!(motion_age_ms(&m, m.host_ns + 50_000_001).is_none()); // a stopped driver
    assert!(motion_age_ms(&m, m.host_ns - 1).is_none()); // from the future: not this clock
}

#[test]
fn braking_stops_the_extrapolation_where_the_head_stops() {
    // 100 deg/s slowing at 2000 deg/s^2 stops in 50 ms, 2.5 deg on: a longer horizon goes no further.
    let r = |d: f64| (d * 3.14159265f64 / 180.0) as f32;
    let m = FeedMotion { omega_rad_s: [0.0, 0.0, r(100.0)], alpha_rad_s2: [0.0, 0.0, r(-2000.0)], ..FeedMotion::default() };
    let at = |h: f32| deg(motion_delta(&m, h, 15.0), Quat::default());
    assert!(near(f64::from(at(50.0)), 2.5, 1e-3) && near(f64::from(at(200.0)), 2.5, 1e-3));
    assert!(near(f64::from(at(20.0)), 2.0 - 0.4, 1e-3));
}

/// golden/motion_feed.cpp prints its tables and every delta as IEEE bits; the Rust delta must print the same.
#[test]
fn motion_delta_matches_the_cpp_bit_for_bit() {
    let golden = include_str!("../golden/motion_feed.txt");
    let float = |w: &str| f32::from_bits(u32::from_str_radix(w, 16).expect("hex float bits"));
    let bits = |v: f32| format!("{:08x}", v.to_bits());
    let (mut qs, mut omegas, mut alphas, mut horizons, mut maxes) = (vec![], vec![], vec![], vec![], vec![]);
    let mut checked = 0;
    for (n, line) in golden.lines().enumerate() {
        let w: Vec<&str> = line.split(' ').collect();
        let v3 = || [float(w[2]), float(w[3]), float(w[4])];
        match w[0] {
            "q" => qs.push(Quat { x: float(w[2]), y: float(w[3]), z: float(w[4]), w: float(w[5]) }),
            "omega" => omegas.push(v3()),
            "alpha" => alphas.push(v3()),
            "horizon" => horizons.push(float(w[2])),
            "max" => maxes.push(float(w[2])),
            "d" => {
                let i: Vec<usize> = w[1..6].iter().map(|v| v.parse().unwrap()).collect();
                let m = FeedMotion { q: qs[i[0]], host_ns: 5_000_000_000, omega_rad_s: omegas[i[1]], alpha_rad_s2: alphas[i[2]] };
                let d = motion_delta(&m, horizons[i[3]], maxes[i[4]]);
                let produced = format!("d {} {} {} {} {}", w[1..6].join(" "), bits(d.x), bits(d.y), bits(d.z), bits(d.w));
                assert_eq!(produced, line, "golden/motion_feed.txt line {}: Rust, then C++", n + 1);
                checked += 1;
            }
            other => panic!("golden/motion_feed.txt line {}: unknown record {other}", n + 1),
        }
    }
    assert!(checked > 3000, "only {checked} cases");
}

struct Scratch(std::path::PathBuf);
impl Scratch {
    fn new() -> Self {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "xr-renderer-motion-feed-test-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Scratch(dir)
    }
    fn prefix(&self) -> String {
        format!("{}/feed_", self.0.display())
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// ftok keys hold 16 bits of the inode, so a private open can refuse with "key in use"; the next try lands on new keys.
fn private_writer(prefix: &str) -> FeedWriter {
    let options = WriterOptions { prefix: prefix.to_owned(), private_segments: true, ..WriterOptions::default() };
    for _ in 0..1024 {
        match FeedWriter::open(&options) {
            Ok(feed) => return feed,
            Err(FeedError(e)) if e.starts_with("key in use: ") => continue,
            Err(FeedError(e)) => panic!("private feed: {e}"),
        }
    }
    panic!("no free SysV keys for a private feed")
}

#[test]
fn no_wire_is_no_feed() {
    // XRLinuxDriver publishes no PredictionWire: the renderer differences q0 and q1.
    let scratch = Scratch::new();
    assert!(MotionFeed::open(&scratch.prefix()).is_none());
}

#[test]
fn reads_what_the_driver_publishes() {
    let scratch = Scratch::new();
    let writer = private_writer(&scratch.prefix());
    let feed = MotionFeed::open(&scratch.prefix()).expect("the writer made the wire");
    assert_eq!(feed.read(), None); // invalid until a motion is written
    let state = MotionState {
        q: [0.0, 0.0, 0.0, 2.0], // [w, x, y, z], not unit: read normalises
        host_ns: 123_456_789_012,
        seq: 7,
        omega_rad_s: [0.5, -0.25, 1.5],
        alpha_rad_s2: [3.0, 0.0, -1.0],
        valid: true,
        calibrated: true,
    };
    writer.write_motion(&state);
    assert_eq!(
        feed.read(),
        Some(FeedMotion {
            q: Quat { x: 0.0, y: 0.0, z: 1.0, w: 0.0 },
            host_ns: 123_456_789_012,
            omega_rad_s: [0.5, -0.25, 1.5],
            alpha_rad_s2: [3.0, 0.0, -1.0],
        })
    );
    writer.write_motion(&MotionState { calibrated: false, ..state });
    assert_eq!(feed.read(), None); // uncalibrated
    writer.write_motion(&MotionState { omega_rad_s: [f32::NAN, 0.0, 0.0], ..state });
    assert_eq!(feed.read(), None); // not finite
    writer.write_motion(&MotionState { q: [0.0; 4], ..state });
    assert_eq!(feed.read(), None); // no orientation
}
