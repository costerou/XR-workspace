//! `PredictionWire`: the driver's motion side channel, one cache line with a 32-bit sequence lock.
//!
//! Mirrors `luma::PredictionWire`, `write_prediction` and `read_prediction` in
//! `spike/driver/include/luma/predict.hpp` and `src/predict.cpp`, word for word and ordering for
//! ordering. The driver never waits for a reader, a reader never blocks the driver, and a driver
//! killed mid-write leaves the sequence odd, which reads as no sample.
//!
//! In shared memory the line lives in the segment named prefix + [`PREDICTION_IPC_NAME`], like the
//! driver's other SysV segments; mapping it is the SysV feed's job. Anything that maps it can view
//! the bytes as a `PredictionWire`: the type is `repr(C)` atomics only, 64-byte aligned.

use core::mem::{align_of, offset_of, size_of};
use core::sync::atomic::{AtomicU32, Ordering, fence};

/// Segment name suffix, as `luma::kPredictionIpcName`.
pub const PREDICTION_IPC_NAME: &str = "pose_prediction";
/// "LPR1". A layout change is a new magic.
pub const PREDICTION_MAGIC: u32 = 0x4c50_5231;

const WORDS: usize = 14;
const FLAG_VALID: u32 = 1;
const FLAG_CALIBRATED: u32 = 2;
// A frame must never spin on a stopped writer.
const READ_TRIES: usize = 4;

/// One cache line: `seq` (odd while being written), `magic`, then 14 words: host_ns lo, hi, sample
/// seq, q x, y, z, w (the feed's order), omega xyz rad/s, alpha xyz rad/s², flags.
#[repr(C, align(64))]
#[derive(Debug, Default)]
pub struct PredictionWire {
    seq: AtomicU32,
    magic: AtomicU32,
    words: [AtomicU32; WORDS],
}

const _: () = {
    assert!(size_of::<PredictionWire>() == 64);
    assert!(align_of::<PredictionWire>() == 64);
    assert!(offset_of!(PredictionWire, seq) == 0);
    assert!(offset_of!(PredictionWire, magic) == 4);
    assert!(offset_of!(PredictionWire, words) == 8);
};

/// `luma::MotionState`: the newest pose, unpredicted, with filtered body-frame motion.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MotionState {
    /// Quaternion `[w, x, y, z]`.
    pub q: [f32; 4],
    /// CLOCK_MONOTONIC when that sample arrived.
    pub host_ns: u64,
    pub seq: u32,
    pub omega_rad_s: [f32; 3],
    pub alpha_rad_s2: [f32; 3],
    pub valid: bool,
    pub calibrated: bool,
}

impl Default for MotionState {
    fn default() -> Self {
        Self {
            q: [1.0, 0.0, 0.0, 0.0],
            host_ns: 0,
            seq: 0,
            omega_rad_s: [0.0; 3],
            alpha_rad_s2: [0.0; 3],
            valid: false,
            calibrated: false,
        }
    }
}

impl PredictionWire {
    /// One writer only. Never waits.
    pub fn write(&self, s: &MotionState) {
        let lock = self.seq.load(Ordering::Relaxed);
        self.seq.store(lock | 1, Ordering::Relaxed); // odd, also after a killed writer
        fence(Ordering::Release);
        let flags =
            if s.valid { FLAG_VALID } else { 0 } | if s.calibrated { FLAG_CALIBRATED } else { 0 };
        let [w, x, y, z] = s.q;
        let v: [u32; WORDS] = [
            s.host_ns as u32,
            (s.host_ns >> 32) as u32,
            s.seq,
            x.to_bits(),
            y.to_bits(),
            z.to_bits(),
            w.to_bits(),
            s.omega_rad_s[0].to_bits(),
            s.omega_rad_s[1].to_bits(),
            s.omega_rad_s[2].to_bits(),
            s.alpha_rad_s2[0].to_bits(),
            s.alpha_rad_s2[1].to_bits(),
            s.alpha_rad_s2[2].to_bits(),
            flags,
        ];
        for (word, value) in self.words.iter().zip(v) {
            word.store(value, Ordering::Relaxed);
        }
        self.magic.store(PREDICTION_MAGIC, Ordering::Relaxed);
        self.seq
            .store((lock | 1).wrapping_add(1), Ordering::Release);
    }

    /// `None`: nothing valid right now (never written, mid-write, another layout, or not valid).
    pub fn read(&self) -> Option<MotionState> {
        for _ in 0..READ_TRIES {
            let before = self.seq.load(Ordering::Acquire);
            if before & 1 != 0 {
                continue;
            }
            let v: [u32; WORDS] = core::array::from_fn(|i| self.words[i].load(Ordering::Relaxed));
            let magic = self.magic.load(Ordering::Relaxed);
            fence(Ordering::Acquire);
            if self.seq.load(Ordering::Relaxed) != before {
                continue;
            }
            if magic != PREDICTION_MAGIC || v[13] & FLAG_VALID == 0 {
                return None;
            }
            let f = |i: usize| f32::from_bits(v[i]);
            return Some(MotionState {
                q: [f(6), f(3), f(4), f(5)],
                host_ns: u64::from(v[0]) | u64::from(v[1]) << 32,
                seq: v[2],
                omega_rad_s: [f(7), f(8), f(9)],
                alpha_rad_s2: [f(10), f(11), f(12)],
                valid: true,
                calibrated: v[13] & FLAG_CALIBRATED != 0,
            });
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    fn state(i: u32) -> MotionState {
        let f = |k: u32| f32::from_bits(i.wrapping_mul(31).wrapping_add(k) & 0x3fff_ffff);
        MotionState {
            q: [f(0), f(1), f(2), f(3)],
            host_ns: u64::from(i) << 32 | u64::from(i),
            seq: i,
            omega_rad_s: [f(4), f(5), f(6)],
            alpha_rad_s2: [f(7), f(8), f(9)],
            valid: true,
            calibrated: i % 2 == 0,
        }
    }

    #[test]
    fn empty_reads_nothing() {
        assert_eq!(PredictionWire::default().read(), None);
    }

    #[test]
    fn round_trips_every_field() {
        let wire = PredictionWire::default();
        for i in [1, 2, 0xdead_beef] {
            wire.write(&state(i));
            assert_eq!(wire.read(), Some(state(i)));
        }
    }

    #[test]
    fn invalid_state_reads_nothing() {
        let wire = PredictionWire::default();
        wire.write(&MotionState {
            valid: false,
            ..state(3)
        });
        assert_eq!(wire.read(), None);
    }

    #[test]
    fn odd_sequence_reads_nothing_and_next_write_repairs_it() {
        let wire = PredictionWire::default();
        wire.write(&state(5));
        wire.seq
            .store(wire.seq.load(Ordering::Relaxed) | 1, Ordering::Relaxed); // killed mid-write
        assert_eq!(wire.read(), None);
        wire.write(&state(6));
        assert_eq!(wire.read(), Some(state(6)));
    }

    #[test]
    fn wrong_magic_reads_nothing() {
        let wire = PredictionWire::default();
        wire.write(&state(7));
        wire.magic.store(0x4c50_5232, Ordering::Relaxed);
        assert_eq!(wire.read(), None);
    }

    #[test]
    fn sequence_wraps() {
        let wire = PredictionWire::default();
        wire.seq.store(u32::MAX - 1, Ordering::Relaxed);
        wire.write(&state(8));
        assert_eq!(wire.seq.load(Ordering::Relaxed), 0);
        // 0 is even: a wrapped sequence still reads.
        assert_eq!(wire.read(), Some(state(8)));
    }

    // Torn-read stress: one writer at full speed, readers that check every sample they get is one
    // whole write.
    #[test]
    fn readers_never_see_a_torn_write() {
        let wire = Arc::new(PredictionWire::default());
        let stop = Arc::new(AtomicBool::new(false));
        wire.write(&state(1));
        let writer = {
            let (wire, stop) = (wire.clone(), stop.clone());
            std::thread::spawn(move || {
                let mut i = 1u32;
                while !stop.load(Ordering::Relaxed) {
                    i = i.wrapping_add(1);
                    wire.write(&state(i));
                }
                i
            })
        };
        let readers: Vec<_> = (0..3)
            .map(|_| {
                let wire = wire.clone();
                std::thread::spawn(move || {
                    let (mut ok, mut last) = (0u64, 0u32);
                    let deadline =
                        std::time::Instant::now() + std::time::Duration::from_millis(1500);
                    while std::time::Instant::now() < deadline {
                        if let Some(s) = wire.read() {
                            assert_eq!(s, state(s.seq), "torn read");
                            assert!(s.seq >= last, "went backwards");
                            last = s.seq;
                            ok += 1;
                        }
                    }
                    ok
                })
            })
            .collect();
        let reads: Vec<u64> = readers.into_iter().map(|r| r.join().unwrap()).collect();
        stop.store(true, Ordering::Relaxed);
        let writes = writer.join().unwrap();
        assert!(writes > 1000, "writer barely ran: {writes}");
        assert!(
            reads.iter().all(|&n| n > 0),
            "a reader never got a sample: {reads:?}"
        );
    }
}
