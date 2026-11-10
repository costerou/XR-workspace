//! The phase 2 gate for fusion: bit-identical output against the C++ `luma::Fusion` on both replay
//! fixtures, plus a faults run (warm start, lost samples, a gap, a clock jump, a duplicate).
//!
//! The golden files in `rust/corpus/luma-fusion/` hold the samples the C++ daemon would feed Fusion
//! and what Fusion returned for each. `luma_fusion_golden` (spike/driver/tools/fusion_golden.cpp)
//! writes them, and its ctests fail when fusion.cpp's output moves. Format: rust/README.md.

use std::path::PathBuf;
use xr_luma_driver::fusion::{Fusion, FusionConfig, FusionQuality, FusionStatus};
use xr_luma_driver::types::{ImuSample, Quatf};

struct Reader {
    bytes: Vec<u8>,
    at: usize,
}

impl Reader {
    fn take<const N: usize>(&mut self) -> [u8; N] {
        let out = self.bytes[self.at..self.at + N].try_into().expect("in bounds");
        self.at += N;
        out
    }
    fn u8(&mut self) -> u8 {
        self.take::<1>()[0]
    }
    fn u32(&mut self) -> u32 {
        u32::from_le_bytes(self.take())
    }
    fn u64(&mut self) -> u64 {
        u64::from_le_bytes(self.take())
    }
    fn f32(&mut self) -> f32 {
        f32::from_le_bytes(self.take())
    }
    fn v3(&mut self) -> [f32; 3] {
        [self.f32(), self.f32(), self.f32()]
    }
    fn q(&mut self) -> Quatf {
        Quatf { w: self.f32(), x: self.f32(), y: self.f32(), z: self.f32() }
    }
}

fn bits3(a: [f32; 3]) -> [u32; 3] {
    a.map(f32::to_bits)
}
fn bitsq(q: Quatf) -> [u32; 4] {
    [q.w, q.x, q.y, q.z].map(f32::to_bits)
}

/// Replays one golden file and returns the last status. Panics at the first bit that differs.
fn replay(name: &str, config: FusionConfig) -> FusionStatus {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../corpus/luma-fusion").join(name);
    let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut r = Reader { bytes, at: 0 };
    assert_eq!(&r.take::<8>(), b"LUMAFUS1", "{name}: not a fusion golden file");
    let count = r.u32() as usize;
    let status_every = r.u32() as usize;
    let mut fusion = Fusion::new(config);
    let mut last = None;
    for i in 0..count {
        let sample = ImuSample { t_ns: r.u64(), seq: r.u32(), gyro_rad_s: r.v3(), accel_m_s2: r.v3(), mag: None };
        let pose = fusion.update(&sample);
        let (q, t_ns, seq, calibrated) = (r.q(), r.u64(), r.u32(), r.u8() != 0);
        assert_eq!(bitsq(pose.q), bitsq(q), "{name}: sample {i} pose {:?}, C++ {:?}", pose.q, q);
        assert_eq!((pose.t_ns, pose.seq, pose.calibrated), (t_ns, seq, calibrated), "{name}: sample {i}");
        if (i + 1) % status_every != 0 && i + 1 != count {
            continue;
        }
        let st = fusion.status();
        assert_eq!(st.quality as u8, r.u8(), "{name}: sample {i} quality");
        assert_eq!(u8::from(st.still), r.u8(), "{name}: sample {i} still");
        assert_eq!(bits3(st.gyro_bias_rad_s), bits3(r.v3()), "{name}: sample {i} bias");
        assert_eq!(bits3(st.omega_body_rad_s), bits3(r.v3()), "{name}: sample {i} omega_body");
        assert_eq!(bits3(st.omega_world_rad_s), bits3(r.v3()), "{name}: sample {i} omega_world");
        assert_eq!(bits3(st.up_body), bits3(r.v3()), "{name}: sample {i} up_body");
        assert_eq!(st.calibrated_after_s.to_bits(), r.f32().to_bits(), "{name}: sample {i} calibrated_after");
        assert_eq!([st.samples, st.rejected, st.gaps, st.time_jumps], [r.u64(), r.u64(), r.u64(), r.u64()], "{name}: sample {i} counters");
        assert_eq!(bitsq(fusion.predict(0.017)), bitsq(r.q()), "{name}: sample {i} predict");
        last = Some(st);
    }
    assert_eq!(r.at, r.bytes.len(), "{name}: trailing bytes");
    last.expect("at least one status")
}

#[test]
fn real_fixture_is_bit_identical() {
    let st = replay("real.bin", FusionConfig::default());
    // The fixture's story (spike/driver/tests/real_capture_test.cpp): the head moved before 1.5 s of
    // stillness, so it calibrates Coarse at about 2.6 s.
    assert_ne!(st.quality, FusionQuality::Uncalibrated);
    assert!((st.calibrated_after_s - 2.6).abs() < 0.2);
}

#[test]
fn upside_down_fixture_is_bit_identical() {
    let st = replay("upside_down.bin", FusionConfig::default());
    assert_eq!(st.quality, FusionQuality::Good);
    assert!((st.calibrated_after_s - 1.5).abs() < 0.2);
}

#[test]
fn faults_run_is_bit_identical() {
    let config = FusionConfig { warm_start_bias: Some([0.004, -0.0025, 0.0015]), ..FusionConfig::default() };
    let st = replay("real_faults.bin", config);
    assert_eq!(st.calibrated_after_s, 0.0, "warm start is calibrated from the first sample");
    assert_eq!((st.rejected, st.gaps, st.time_jumps), (1, 1, 2), "one duplicate, one gap, and the clock jump out and back");
}
