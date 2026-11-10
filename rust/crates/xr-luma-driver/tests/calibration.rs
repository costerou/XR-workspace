//! The phase 2 gate for calibration and the saved bias: the same calibration times as the C++
//! driver on both replay fixtures, and `luma-gyro-bias` in the same format.
//!
//! The replay path is `luma_replay_report`'s (spike/driver/tools/replay_report.cpp): parser, Luma
//! sequence tracker, then the estimator. On these fixtures it prints "calibrated after 2.60 s"
//! (real: the head moves before 1.5 s of stillness, so Coarse at the 2.5 s timeout's block) and
//! "calibrated after 1.50 s" (upside down, still on a desk: Good).
//!
//! The store tests port `test_bias_store` and the three estimator tests from
//! spike/driver/tests/daemon_test.cpp.

use std::cell::RefCell;
use std::path::PathBuf;

use xr_luma_driver::bias::{BiasStore, FusionEstimator, NOTE_EVERY, SAVE_EVERY_NS};
use xr_luma_driver::daemon::{Decoder, Estimator};
use xr_luma_driver::fusion::{FusionConfig, FusionQuality, FusionStatus};
use xr_luma_driver::protocol::{LUMA_IMU_PACKET_SIZE, LumaDecoder, Verdict, encode_luma_imu, luma_sequence_tracker, parse_imu};
use xr_luma_driver::types::ImuSample;

const MS: u64 = 1_000_000;

fn fixture(name: &str) -> Vec<u8> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../spike/driver/tests/fixtures").join(name);
    std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

/// Runs a fixture as `luma_replay_report` does and returns the estimator's last status.
fn replay(name: &str, estimator: &mut FusionEstimator<'_>) -> FusionStatus {
    let bytes = fixture(name);
    let mut tracker = luma_sequence_tracker();
    for packet in bytes.chunks_exact(LUMA_IMU_PACKET_SIZE) {
        let Some(mut sample) = parse_imu(packet) else { continue };
        if tracker.observe(&mut sample).verdict != Verdict::Accepted {
            continue;
        }
        estimator.update(&sample);
    }
    estimator.status()
}

/// `%.2f`, as the C++ report prints it.
fn seconds(status: &FusionStatus) -> String {
    format!("{:.2}", f64::from(status.calibrated_after_s))
}

#[test]
fn calibration_times_match_the_cpp_replay_report() {
    let real = replay("luma_imu_real.bin", &mut FusionEstimator::new(FusionConfig::default()));
    println!("luma_imu_real.bin: calibrated after {} s, {:?}", seconds(&real), real.quality);
    assert_eq!(seconds(&real), "2.60");
    assert_eq!(real.quality, FusionQuality::Coarse, "moving before 1.5 s of stillness");

    let desk = replay("luma_imu_upside_down.bin", &mut FusionEstimator::new(FusionConfig::default()));
    println!("luma_imu_upside_down.bin: calibrated after {} s, {:?}", seconds(&desk), desk.quality);
    assert_eq!(seconds(&desk), "1.50");
    assert_eq!(desk.quality, FusionQuality::Good);
}

#[test]
fn a_cold_store_calibrates_as_without_one() {
    // No saved bias: the store changes nothing about the calibration.
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    let real = replay("luma_imu_real.bin", &mut FusionEstimator::with_store(&store));
    assert_eq!(seconds(&real), "2.60");
}

#[test]
fn a_saved_bias_calibrates_from_the_first_sample() {
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    store.borrow_mut().remember([0.0, 0.0, 0.0]);
    let mut estimator = FusionEstimator::with_store(&store);
    let real = replay("luma_imu_real.bin", &mut estimator);
    println!("luma_imu_real.bin warm: calibrated after {} s, {:?}", seconds(&real), real.quality);
    assert_eq!(real.calibrated_after_s, 0.0);
    // On a desk the warm start is Coarse at once, then a full calibration still makes it Good.
    let mut estimator = FusionEstimator::with_store(&store);
    let desk = replay("luma_imu_upside_down.bin", &mut estimator);
    assert_eq!((desk.calibrated_after_s, desk.quality), (0.0, FusionQuality::Good));
}

fn scratch_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("xr-luma-bias-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}

#[test]
fn the_store_round_trips_and_refuses_bad_files() {
    let dir = scratch_dir("store");
    let path = dir.join("state").join("bias");
    let mut store = BiasStore::new(&path);
    store.load();
    assert_eq!(store.bias(), None, "a first run: cold start");
    assert!(!store.save(), "nothing learned, nothing written");
    store.remember([0.01, -0.02, 0.03]);
    assert!(store.save());
    // Byte for byte what the C++ BiasStore writes for the same floats.
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "0.00999999978 -0.0199999996 0.0299999993\n");
    assert!(!dir.join("state").join("bias.tmp").exists());
    let mut again = BiasStore::new(&path);
    again.load();
    assert_eq!(again.bias(), Some([0.01, -0.02, 0.03]));

    std::fs::write(&path, "garbage").unwrap();
    let mut bad = BiasStore::new(&path);
    bad.load();
    assert_eq!(bad.bias(), None);
    std::fs::write(&path, "0 0 9").unwrap(); // beyond anything a gyro could need: refused, not clamped
    let mut wild = BiasStore::new(&path);
    wild.load();
    assert_eq!(wild.bias(), None);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn open_uses_the_named_state_dir() {
    let dir = scratch_dir("open");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("luma-gyro-bias"), "0.004 -0.0025 0.0015\n").unwrap();
    let store = BiasStore::open(dir.to_str().unwrap()).expect("a directory was named");
    assert_eq!(store.path(), dir.join("luma-gyro-bias"));
    assert_eq!(store.bias(), Some([0.004, -0.0025, 0.0015]));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_file_is_rewritten_every_five_minutes() {
    let dir = scratch_dir("tick");
    let path = dir.join("luma-gyro-bias");
    let mut store = BiasStore::new(&path);
    store.remember([0.001, 0.002, 0.003]);
    let start = 7 * MS;
    store.tick(start); // the first tick only starts the clock
    assert!(!path.exists());
    store.tick(start + SAVE_EVERY_NS - 1);
    assert!(!path.exists());
    store.tick(start + SAVE_EVERY_NS);
    assert!(path.exists());
    std::fs::remove_file(&path).unwrap();
    store.tick(start + 2 * SAVE_EVERY_NS - 1);
    assert!(!path.exists(), "the next one is five minutes after the last");
    store.tick(start + 2 * SAVE_EVERY_NS);
    assert!(path.exists());
    let _ = std::fs::remove_dir_all(&dir);
}

fn still(i: u32, gyro: [f32; 3], accel: [f32; 3]) -> ImuSample {
    ImuSample { t_ns: u64::from(i) * MS, seq: i, gyro_rad_s: gyro, accel_m_s2: accel, mag: None }
}

#[test]
fn a_saved_bias_start_takes_its_tilt_from_the_live_stream() {
    // Fusion's first sample sets its attitude, and with a saved bias it is calibrated at once and
    // corrects slowly after that.
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    store.borrow_mut().remember([0.0, 0.0, 0.0]);
    let mut estimator = FusionEstimator::with_store(&store);
    let mut decoder = LumaDecoder::default();
    decoder.new_connection();
    let mut feed = |s: ImuSample| decoder.decode(&encode_luma_imu(&s, 0)).map(|d| estimator.update(&d));
    // Held since the glasses lay upside down.
    assert!(feed(ImuSample { t_ns: MS, seq: 100, gyro_rad_s: [0.0; 3], accel_m_s2: [0.0, 0.0, -9.8], mag: None }).is_none());
    // Now they are level.
    let first = feed(ImuSample { t_ns: 9 * MS, seq: 5000, gyro_rad_s: [0.0; 3], accel_m_s2: [0.0, 0.0, 9.8], mag: None })
        .expect("a live sample");
    assert!(first.calibrated);
    assert!(first.q.x.abs() < 0.01 && first.q.y.abs() < 0.01, "level from the first pose out: {:?}", first.q);
}

#[test]
fn a_long_running_driver_hands_over_its_bias() {
    // The driver outlives its XR runs, so the bias must reach the store with the estimator alive.
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    let mut estimator = FusionEstimator::with_store(&store);
    for i in 1..=70_000 {
        estimator.update(&still(i, [0.01, -0.02, 0.005], [0.0, 0.0, 9.8]));
        if u64::from(i) == NOTE_EVERY - 1 {
            assert_eq!(store.borrow().bias(), None, "noted once a minute, not every sample");
        }
    }
    let bias = store.borrow().bias().expect("noted after a minute");
    assert!((bias[1] + 0.02).abs() < 1e-3, "{bias:?}");
}

#[test]
fn the_bias_is_handed_over_when_the_estimator_goes() {
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    {
        let mut estimator = FusionEstimator::with_store(&store);
        for i in 1..=3_000 {
            estimator.update(&still(i, [0.01, -0.02, 0.005], [0.0, 0.0, 9.8]));
        }
        assert_eq!(estimator.status().quality, FusionQuality::Good);
        assert_eq!(store.borrow().bias(), None);
    }
    assert!(store.borrow().bias().is_some());
}

#[test]
fn a_poor_bias_is_not_kept() {
    // Moving from the first sample: Coarse at best, and that guess must not be saved as the truth
    // for the next run.
    let store = RefCell::new(BiasStore::new("/nonexistent/bias"));
    {
        let mut estimator = FusionEstimator::with_store(&store);
        for i in 1..=3_000u32 {
            let swing = 1.5 * (i as f32 * 0.01).sin();
            estimator.update(&still(i, [swing; 3], [swing, swing, 9.8]));
        }
    }
    assert_eq!(store.borrow().bias(), None);
}
