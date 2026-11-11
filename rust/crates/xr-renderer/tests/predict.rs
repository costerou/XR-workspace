//! Port of ctest `predict` (spike/tests/predict_test.cpp): the driver's look-ahead config, angular velocity and the
//! bounded delta, the latency tracker and its read-phase model, pose age, the horizon and a feed round trip. The D3
//! checks (`motion_delta`, `motion_age_ms`, `MotionFeed::open`) belong to `motion_feed` and are tested with it.

use std::sync::atomic::{AtomicU32, Ordering};
use xr_pose::feed::{FeedError, FeedWriter, WriterOptions};
use xr_renderer::pose::{PoseFeed, Quat, quat_angle, quat_mul};
use xr_renderer::predict::*;

fn near(a: f64, b: f64, eps: f64) -> bool {
    (a - b).abs() <= eps
}
fn near3(a: f64, b: f64) -> bool {
    near(a, b, 1e-3)
}
fn axis_angle(x: f32, y: f32, z: f32, deg: f32) -> Quat {
    let h = deg * 3.14159265f32 / 360.0;
    let s = h.sin();
    Quat {
        x: x * s,
        y: y * s,
        z: z * s,
        w: h.cos(),
    }
}
fn deg(a: Quat, b: Quat) -> f32 {
    quat_angle(a, b) * 180.0 / 3.14159265f32
}
/// Constant yaw rate (deg/ms) about z, sampled like the driver: now, -16 ms, -32 ms.
fn yawing(rate: f32, now_ms: f32, base: Quat) -> PoseHistory {
    let mut h = PoseHistory::default();
    for i in 0..3 {
        let t = now_ms - 16.0 * i as f32;
        h.q[i] = quat_mul(base, axis_angle(0.0, 0.0, 1.0, rate * t));
        h.device_ms[i] = t;
    }
    h
}
fn yaw(rate: f32) -> PoseHistory {
    yawing(rate, 1000.0, Quat::default())
}
fn base() -> Quat {
    axis_angle(0.6, 0.8, 0.0, 70.0)
}
fn ns(ms: f64) -> u64 {
    (ms * 1e6).round() as u64 // llround
}
/// Deterministic 0..1.
fn spread(k: i32, step: f64) -> f64 {
    (k as f64 * step) % 1.0
}

#[test]
fn look_ahead_config() {
    // Driver config: Luma values validate; garbage does not.
    assert!(look_ahead_from(&[10.0, 0.6, 10.0, 40.0]).is_some());
    assert!(look_ahead_from(&[f32::NAN, 0.6, 10.0, 40.0]).is_none());
    assert!(look_ahead_from(&[10.0, 0.6, 10.0, 0.0]).is_none()); // no cap: unbounded
    assert!(look_ahead_from(&[10.0, 9.0, 10.0, 40.0]).is_none());
    assert!(look_ahead_from(&[-1.0, 0.6, 10.0, 40.0]).is_none());
}

#[test]
fn angular_velocity_and_delta() {
    // Angular velocity: constant yaw of 0.2 deg/ms (200 deg/s).
    {
        let w = angular_velocity(&yaw(0.2)).unwrap();
        assert!(
            near(w[2] as f64, 0.2 * 3.14159265 / 180.0, 1e-5)
                && near(w[0] as f64, 0.0, 1e-6)
                && near(w[1] as f64, 0.0, 1e-6)
        );
    }
    // Extrapolation from a rotated base lands on the true future orientation.
    {
        let h = yawing(0.2, 1000.0, base());
        let w = angular_velocity(&h).unwrap();
        let predicted = quat_mul(h.q[0], prediction_delta(&w, 20.0, 15.0));
        let truth = quat_mul(base(), axis_angle(0.0, 0.0, 1.0, 0.2 * 1020.0));
        assert!(deg(predicted, truth) < 0.01);
        assert!(deg(predicted, h.q[0]) > 3.9); // 4 degrees ahead
    }
    // Bounded: a 2000 deg/s spike over 40 ms is clamped to max_deg.
    {
        let w = angular_velocity(&yaw(2.0)).unwrap();
        assert!(near(
            deg(prediction_delta(&w, 40.0, 15.0), Quat::default()) as f64,
            15.0,
            0.01
        ));
    }
    assert!(
        deg(
            prediction_delta(&[0.0, 0.0, 0.01], 0.0, 15.0),
            Quat::default()
        ) < 1e-4
    );
    assert!(
        deg(
            prediction_delta(&[0.0, 0.0, 0.01], -5.0, 15.0),
            Quat::default()
        ) < 1e-4
    );
    // At rest: no motion predicted.
    {
        let w = angular_velocity(&yawing(0.0, 1000.0, base())).unwrap();
        assert!(deg(prediction_delta(&w, 30.0, 15.0), Quat::default()) < 1e-3);
    }
    // Unusable timestamps: equal, backward, too far apart, non-finite.
    let mut h = yaw(0.2);
    h.device_ms[1] = h.device_ms[0];
    assert!(angular_velocity(&h).is_none());
    h = yaw(0.2);
    h.device_ms[1] = h.device_ms[0] + 16.0;
    assert!(angular_velocity(&h).is_none());
    h = yaw(0.2);
    h.device_ms[1] = h.device_ms[0] - 80.0;
    assert!(angular_velocity(&h).is_none());
    h = yaw(0.2);
    h.device_ms[0] = f32::NAN;
    assert!(angular_velocity(&h).is_none());
    h = yaw(0.2);
    h.q[1] = Quat {
        x: f32::NAN,
        y: 0.0,
        z: 0.0,
        w: 1.0,
    };
    assert!(angular_velocity(&h).is_none());
}

#[test]
fn whole_ms_timestamps() {
    // The Luma Ultra's (ts - ts0) * 1000 truncation reads a ms low about half the time, so 16 ms spacings show up as
    // 15/16/17. The samples are N IMU cycles apart either way, so when the two gaps differ only by that, their mean is
    // the spacing (half the error, often exact).
    let truth = 0.1 * 3.14159265 / 180.0; // rad/ms: 100 deg/s, samples truly 16 ms apart
    let rate = |t0: f32, t1: f32, t2: f32| {
        let mut h = yawing(0.1, 1000.0, base()); // orientations exactly 16 and 32 ms back
        h.device_ms = [t0, t1, t2];
        let w = angular_velocity(&h).unwrap();
        w[2] as f64 / truth // yaw about the body z axis (base is a fixed left factor)
    };
    assert!(near(rate(1000.0, 984.0, 968.0), 1.0, 1e-4)); // exact stamps: unchanged
    assert!(near(rate(999.0, 984.0, 968.0), 16.0 / 15.5, 1e-4)); // t0 a ms low: was 16/15 (+6.7%), now +3.2%
    assert!(near(rate(1000.0, 983.0, 968.0), 1.0, 1e-4)); // t1 a ms low: was 16/17 (-5.9%), now exact
    assert!(near(rate(999.0, 984.0, 967.0), 1.0, 1e-4)); // t0 and t2 low: exact
    assert!(near(rate(1000.0, 984.0, 965.0), 1.0, 1e-4)); // gaps 16 vs 19: a real mismatch, t0 - t1 alone
    assert!(near(rate(1000.0, 984.0, f32::NAN), 1.0, 1e-4)); // unusable t2: t0 - t1 alone
}

#[test]
fn latency_tracker() {
    // Only a monotonic presentation clock is comparable.
    let mut t = LatencyTracker::new();
    t.read(1, 1_000_000);
    t.presented(1, 9_000_000);
    assert!(t.summary().count == 0 && t.summary().dropped == 1 && t.expected_ms().is_none());
    t.clock(1);
    for i in 0..20u64 {
        t.read(10 + i, i * 8_333_333);
        t.presented(10 + i, i * 8_333_333 + 12_000_000);
    }
    let s = t.summary();
    assert!(
        s.count == 20
            && near3(s.mean_ms, 12.0)
            && near(s.p50_ms, 12.0, 0.25)
            && near(s.p95_ms, 12.0, 0.25)
            && near3(s.max_ms, 12.0)
    );
    assert!(near(t.expected_ms().unwrap(), 12.0, 0.01));
    // Discarded and unknown tickets count as dropped, never as measurements.
    t.read(100, 0);
    t.discarded(100);
    t.presented(999, 5);
    assert!(t.summary().count == 20 && t.summary().dropped == 3);
    // Presented before read (clock mismatch) is dropped, not a negative latency.
    t.read(101, 50_000_000);
    t.presented(101, 40_000_000);
    assert!(t.summary().count == 20 && t.summary().dropped == 4);
    // p95 reflects a slow tail.
    for i in 0..20u64 {
        t.read(200 + i, 0);
        t.presented(200 + i, 30_000_000);
    }
    assert!(t.summary().p95_ms > 29.0 && t.summary().max_ms > 29.9);
    t.read(300, 0);
    t.abandon();
    t.presented(300, 1_000_000);
    assert!(t.summary().count == 40);
    // A full pending table recycles the oldest entry instead of growing.
    for i in 0..100u64 {
        t.read(1000 + i, 0);
    }
    t.presented(1099, 8_000_000);
    assert!(t.summary().count == 41);

    // Before enough samples the EMA is not trusted.
    let mut t = LatencyTracker::new();
    t.clock(1);
    t.read(1, 0);
    t.presented(1, 10_000_000);
    assert!(t.expected_ms().is_none());
}

#[test]
fn pose_age() {
    // Pose age relative to the best observed transport.
    let mut a = PoseAge::new();
    assert!(a.observe(5000.0, 1000.0).is_some() && near3(a.observe(5000.0, 1000.0).unwrap(), 0.0));
    assert!(near3(a.observe(5003.0, 1001.0).unwrap(), 2.0)); // 2 ms worse than the floor
    assert!(near3(a.observe(5002.5, 1003.0).unwrap(), 0.0)); // new floor
    assert!(near3(a.observe(5004.0, 1003.0).unwrap(), 1.5)); // same sample, read later: older
    assert!(a.observe(6000.0, 10.0).is_none()); // device clock went backwards: reset
    assert!(near3(a.observe(6000.0, 10.0).unwrap(), 0.0));
}

#[test]
fn read_phase_on_the_refresh_grid() {
    // A read later in the refresh period is presented sooner. Frame k reads 1.0..3.5 ms after vblank k and is presented
    // at vblank k + 2; its feedback arrives before the read of frame k + 2. Once the per-phase estimate beats the
    // average, it is exact for any phase.
    let mut t = LatencyTracker::new();
    t.clock(1);
    let p = 1000.0 / 120.0;
    let mut k = 0i32;
    while k < 300 {
        if k >= 2 {
            t.presented((k - 1) as u64, ns(k as f64 * p)); // frame k - 2 at vblank k
        }
        t.read(
            (k + 1) as u64,
            ns(k as f64 * p + 1.0 + 2.5 * spread(k, 0.618034)),
        );
        k += 1;
    }
    assert!(t.phase_aware());
    for phase in [1.2, 2.0, 3.3] {
        let now = k as f64 * p + phase;
        assert!(near(
            t.expected_ms_at(ns(now)).unwrap(),
            (k + 2) as f64 * p - now,
            0.05
        ));
    }
    let ema = t.expected_ms_at(0).unwrap(); // before the grid: always the average
    assert!(
        (t.expected_ms_at(ns(k as f64 * p + 1.2)).unwrap()
            - t.expected_ms_at(ns(k as f64 * p + 3.3)).unwrap()
            - 2.1)
            .abs()
            < 0.05
            && ema > 13.0
            && ema < 15.5
    );
    assert!(near(
        t.expected_ms_at(ns(k as f64 * p + 5000.0)).unwrap(),
        ema,
        1e-9
    )); // grid too old: the average
    // The refresh rate halves (120 -> 60 Hz): the grid is re-learnt, then the estimate is exact again.
    let p2 = 2.0 * p;
    let base_ms = (k + 1) as f64 * p;
    for j in 0..300i32 {
        if j >= 2 {
            t.presented((k - 1) as u64, ns(base_ms + j as f64 * p2));
        }
        t.read(
            (k + 1) as u64,
            ns(base_ms + j as f64 * p2 + 1.0 + 2.5 * spread(j, 0.618034)),
        );
        k += 1;
    }
    assert!(t.phase_aware());
    let now = base_ms + 300.0 * p2 + 2.0;
    assert!(near(
        t.expected_ms_at(ns(now)).unwrap(),
        base_ms + 302.0 * p2 - now,
        0.05
    ));
}

/// Presents every queued frame due by `read`, in queue order, as the C++ test's erase loop.
fn present_due(t: &mut LatencyTracker, presents: &mut Vec<(u64, f64)>, read: f64) {
    presents.retain(|&(ticket, at)| {
        if at <= read {
            t.presented(ticket, ns(at));
            false
        } else {
            true
        }
    });
}

#[test]
fn stalls_do_not_teach_a_phase() {
    // Compositor stalls (frames presented ~150 ms late) read at a phase not read otherwise must not become that phase's
    // estimate (two of them, so only the stall gate keeps them out of the bin): reads there afterwards get their real
    // latency, not ~150 ms.
    let mut t = LatencyTracker::new();
    t.clock(1);
    let p = 1000.0 / 120.0;
    let mut presents: Vec<(u64, f64)> = Vec::new();
    for k in 0..2000i32 {
        let stall = k == 600 || k == 900;
        let mut phase = 1.0 + spread(k, 0.618034);
        if k % 97 == 50 {
            phase = 4.6; // occasional reads at a rarer phase
        }
        if stall {
            phase = 6.5;
        }
        let read = k as f64 * p + phase;
        present_due(&mut t, &mut presents, read);
        t.read((k + 1) as u64, ns(read));
        presents.push((
            (k + 1) as u64,
            (if stall { k + 20 } else { k + 2 }) as f64 * p,
        ));
    }
    assert!(t.phase_aware());
    let k = 2000.0;
    assert!(near(
        t.expected_ms_at(ns(k * p + 6.5)).unwrap(),
        2.0 * p - 6.5,
        0.05
    )); // from the nearest learnt phase
    assert!(near(
        t.expected_ms_at(ns(k * p + 4.6)).unwrap(),
        2.0 * p - 4.6,
        0.05
    ));
    assert!(near(
        t.expected_ms_at(ns(k * p + 1.5)).unwrap(),
        2.0 * p - 1.5,
        0.05
    ));
}

#[test]
fn no_fixed_grid_keeps_the_average() {
    // Presented when ready (e.g. variable refresh): the per-phase estimate never beats the average, so the average is
    // used, as before.
    let mut t = LatencyTracker::new();
    t.clock(1);
    let mut presents: Vec<(u64, f64)> = Vec::new();
    let mut now = 0.0;
    for k in 0..400i32 {
        let read = k as f64 * 9.0 + 0.7 * spread(k, 0.414214);
        present_due(&mut t, &mut presents, read);
        t.read((k + 1) as u64, ns(read));
        presents.push(((k + 1) as u64, read + 6.0 + 5.0 * spread(k, 0.618034)));
        now = read;
    }
    assert!(!t.phase_aware());
    assert!(near(
        t.expected_ms_at(ns(now + 2.0)).unwrap(),
        t.expected_ms_at(0).unwrap(),
        1e-9
    ));
}

#[test]
fn pose_age_floor_slides() {
    // A device clock 50 ppm slow against the host must not inflate the age (a global minimum grew it by 3 ms a minute,
    // and with it the horizon).
    let mut a = PoseAge::new();
    let mut worst: f64 = 0.0;
    for i in 0..120 * 120 {
        // two minutes at 120 Hz
        let host = 10000.0 + i as f64 * 8.333;
        let device = (1000.0 + i as f64 * 8.333 * (1.0 - 50e-6)).floor() as f32;
        let age = a.observe(host, device).unwrap();
        if i > 1200 {
            worst = worst.max(age);
        }
    }
    assert!(worst < 1.5);
    assert!(a.summary().max_ms < 1.5);
    // The feed then freezes (the same sample read for 30 s): its age keeps growing, as before.
    let frozen_host = 10000.0 + (120 * 120) as f64 * 8.333;
    let frozen_device = (1000.0 + (120 * 120) as f64 * 8.333 * (1.0 - 50e-6)).floor() as f32;
    let mut age = 0.0;
    for i in 1..=120 * 30 {
        age = a
            .observe(frozen_host + i as f64 * 8.333, frozen_device)
            .unwrap();
    }
    assert!(age > 29900.0 && a.summary().max_ms > 29900.0);
}

#[test]
fn horizon() {
    // Measured + driver constant, capped, plus half the scanline adjustment.
    let luma = look_ahead_from(&[10.0, 0.6, 10.0, 40.0]);
    assert!(near3(
        prediction_horizon(luma.as_ref(), Some(9.0), 0.5, 8.33).unwrap() as f64,
        0.5 + 9.0 + 10.0 + 5.0
    ));
    assert!(near3(
        prediction_horizon(luma.as_ref(), Some(60.0), 0.5, 8.33).unwrap() as f64,
        40.0 + 5.0
    )); // capped
    assert!(near(
        prediction_horizon(luma.as_ref(), None, 0.0, 8.33).unwrap() as f64,
        10.0 + 0.6 * 8.33 + 5.0,
        1e-3
    )); // driver formula
    assert!(prediction_horizon(None, Some(9.0), 0.5, 8.33).is_none()); // no driver bounds: no prediction
}

/// A private SysV feed in its own scratch directory, removed with it (as tests/pose.rs).
struct Scratch(std::path::PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn feed_round_trip() {
    // The driver's three samples and look-ahead config reach the predictor.
    static N: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "xr-renderer-predict-test-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).unwrap();
    let scratch = Scratch(dir);
    let prefix = format!("{}/feed_", scratch.0.display());
    let options = WriterOptions {
        prefix: prefix.clone(),
        private_segments: true,
        ..WriterOptions::default()
    };
    // ftok keys hold 16 bits of the inode, so a private open can refuse with "key in use"; the next try lands on new keys.
    let mut writer = (0..1024)
        .find_map(|_| match FeedWriter::open(&options) {
            Ok(w) => Some(w),
            Err(FeedError(e)) if e.starts_with("key in use: ") => None,
            Err(FeedError(e)) => panic!("private feed: {e}"),
        })
        .expect("no free SysV keys for a private feed");
    writer.set_disabled(false);
    let h = yawing(0.2, 5000.0, base());
    let mut v = [0f32; 16];
    for i in 0..3 {
        let q = h.q[i];
        v[i * 4..i * 4 + 4].copy_from_slice(&[q.x, q.y, q.z, q.w]);
        v[12 + i] = h.device_ms[i];
    }
    assert!(writer.write(&v));
    let mut feed = PoseFeed::open(&prefix, "").unwrap();
    let sample = feed.read().unwrap();
    let got = history_of(&sample);
    for i in 0..3 {
        assert!(deg(got.q[i], h.q[i]) < 1e-3 && got.device_ms[i] == h.device_ms[i]);
    }
    let w = angular_velocity(&got).unwrap();
    assert!(near(w[2] as f64, 0.2 * 3.14159265 / 180.0, 1e-5));
    assert!(look_ahead_from(&feed.look_ahead_cfg().unwrap()).is_some());
}

#[test]
fn vsync_meter() {
    // Not in predict_test.cpp: the meter's skip rule, horizon error about its offset and sensor-to-vsync.
    let mut m = VsyncMeter::new();
    m.presented(None, 1_000_000_000, 5.0);
    let v = VsyncSnapshot {
        anchor_host_ns: 1_000_000_000,
        period_ns: 1e9 / 90.0,
        anchor_feed_ms: 500.0,
        ..VsyncSnapshot::default()
    };
    m.presented(Some(&v), 1_000_000_000 + 300_000_000, 5.0); // anchor too old
    for k in 0..10u64 {
        // 2 ms after each scan-out; the frame drew a sample 12 ms before it.
        let at = v.anchor_host_ns + (k as f64 * v.period_ns) as u64 + 2_000_000;
        m.presented(
            Some(&v),
            at,
            (500.0 + k as f64 * v.period_ns / 1e6 - 12.0) as f32,
        );
    }
    let s = m.summary();
    assert!(s.count == 10 && s.skipped == 2 && s.sensor_count == 10);
    assert!(near(s.offset_ms, 2.0, 1e-3) && s.error_max_ms < 1e-3);
    assert!(near(s.sensor_p50_ms, 12.0, 1e-3) && near(s.period_ms, 1000.0 / 90.0, 1e-9));
}
