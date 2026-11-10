//! The phase 2 gate for the IMU packet parser and the sequence / device-time handling.
//!
//! - Parity: both replay fixtures (`spike/driver/tests/fixtures/`), through `parse_imu` and the Luma
//!   sequence tracker, give exactly the sample stream the C++ gave: the inputs recorded in
//!   `rust/corpus/luma-fusion/` by `luma_fusion_golden` (parse, tracker, accepted only), every bit.
//! - The captured packets and tracker cases of `spike/driver/tests/protocol_test.cpp`, ported.
//! - Fuzz: random bytes, mutated real packets and random sequence streams, with no panic (overflow
//!   checks are on in a test build), values in range, and time that never goes backwards.

use std::path::PathBuf;
use xr_luma_driver::daemon::Decoder;
use xr_luma_driver::protocol::{
    DeviceClock, LUMA_IMU_PACKET_SIZE, LUMA_TICK_NS, LumaDecoder, SequenceTracker, Verdict, encode_luma_imu,
    luma_sequence_tracker, luma_temperature_c, parse_imu,
};
use xr_luma_driver::types::ImuSample;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn hex(s: &str) -> Vec<u8> {
    s.split_whitespace().map(|b| u8::from_str_radix(b, 16).unwrap()).collect()
}

fn packets(name: &str) -> Vec<[u8; LUMA_IMU_PACKET_SIZE]> {
    let path = root().join("spike/driver/tests/fixtures").join(name);
    let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    assert_eq!(bytes.len() % LUMA_IMU_PACKET_SIZE, 0, "{name}: not whole packets");
    bytes.chunks_exact(LUMA_IMU_PACKET_SIZE).map(|c| c.try_into().unwrap()).collect()
}

/// The sample inputs of a fusion golden file (format in rust/README.md): what the C++ parser and
/// tracker produced, accepted samples only.
fn golden_inputs(name: &str) -> Vec<ImuSample> {
    let path = root().join("rust/corpus/luma-fusion").join(name);
    let b = std::fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    assert_eq!(&b[..8], b"LUMAFUS1");
    let u32_at = |at: usize| u32::from_le_bytes(b[at..at + 4].try_into().unwrap());
    let f32_at = |at: usize| f32::from_le_bytes(b[at..at + 4].try_into().unwrap());
    let (count, every) = (u32_at(8) as usize, u32_at(12) as usize);
    const INPUT: usize = 8 + 4 + 12 + 12;
    const POSE: usize = 16 + 8 + 4 + 1;
    const STATUS: usize = 2 + 4 * 12 + 4 + 4 * 8 + 16;
    let mut at = 16;
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        out.push(ImuSample {
            t_ns: u64::from_le_bytes(b[at..at + 8].try_into().unwrap()),
            seq: u32_at(at + 8),
            gyro_rad_s: [f32_at(at + 12), f32_at(at + 16), f32_at(at + 20)],
            accel_m_s2: [f32_at(at + 24), f32_at(at + 28), f32_at(at + 32)],
            mag: None,
        });
        at += INPUT + POSE;
        if (i + 1) % every == 0 || i + 1 == count {
            at += STATUS;
        }
    }
    assert_eq!(at, b.len(), "{name}: golden file layout");
    out
}

fn bits(s: &ImuSample) -> (u64, u32, [u32; 3], [u32; 3], bool) {
    (s.t_ns, s.seq, s.gyro_rad_s.map(f32::to_bits), s.accel_m_s2.map(f32::to_bits), s.mag.is_some())
}

fn assert_same_stream(name: &str, ours: &[ImuSample], cpp: &[ImuSample]) {
    assert_eq!(ours.len(), cpp.len(), "{name}: sample count");
    for (i, (a, b)) in ours.iter().zip(cpp).enumerate() {
        assert_eq!(bits(a), bits(b), "{name}: sample {i}: Rust {a:?}, C++ {b:?}");
    }
}

fn parity(fixture: &str, golden: &str) {
    let packets = packets(fixture);
    let mut tracker = luma_sequence_tracker();
    let mut stream = Vec::new();
    for (i, p) in packets.iter().enumerate() {
        let mut s = parse_imu(p).unwrap_or_else(|| panic!("{fixture}: packet {i} does not parse"));
        if tracker.observe(&mut s).verdict == Verdict::Accepted {
            stream.push(s);
        }
    }
    let cpp = golden_inputs(golden);
    assert_same_stream(fixture, &stream, &cpp);
    assert!(tracker.lost_total() <= 2, "{fixture}: lost {}", tracker.lost_total()); // usbmon, not the glasses

    // The daemon's decoder: the same stream, less the connection's first packet, with device time
    // and temperature from each packet.
    let mut decoder = LumaDecoder::default();
    let mut decoded = Vec::new();
    for p in &packets {
        if let Some(s) = decoder.decode(p) {
            let ticks = u32::from_le_bytes(p[8..12].try_into().unwrap());
            assert_eq!(decoder.device_ns(), Some(u64::from(ticks) * LUMA_TICK_NS));
            assert_eq!(decoder.temperature_c(), luma_temperature_c(p));
            decoded.push(s);
        }
    }
    assert_same_stream(fixture, &decoded, &cpp[1..]);
    println!("{fixture}: {} packets, {} samples identical to the C++", packets.len(), stream.len());
}

#[test]
fn real_fixture_parses_to_the_cpp_stream() {
    parity("luma_imu_real.bin", "real.bin");
}

#[test]
fn upside_down_fixture_parses_to_the_cpp_stream() {
    parity("luma_imu_upside_down.bin", "upside_down.bin");
}

const FIRST: &str = "aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00";
#[allow(clippy::approx_constant, clippy::excessive_precision)]
const GYRO_LSB: f32 = 3.14159265358979_f32 / 180.0 / 16.4;
const ACCEL_LSB: f32 = 9.80665 / 8192.0;

fn close_to(a: f32, b: f32) -> bool {
    (a - b).abs() < 1e-5
}

#[test]
fn captured_imu_packet_decodes_to_the_body_frame() {
    // captured: the first IMU packet of the session, glasses in Cody's hands.
    let s = parse_imu(&hex(FIRST)).unwrap();
    assert_eq!(s.seq, 0x153c);
    assert_eq!(s.t_ns, 0x11edd * 100_000);
    // wire gyro -12, 5, 36 and accel -235, -2031, 8052. Forward = -y, left = +x, up = +z.
    assert!(close_to(s.gyro_rad_s[0], -5.0 * GYRO_LSB));
    assert!(close_to(s.gyro_rad_s[1], -12.0 * GYRO_LSB));
    assert!(close_to(s.gyro_rad_s[2], 36.0 * GYRO_LSB));
    assert!(close_to(s.accel_m_s2[0], 2031.0 * ACCEL_LSB));
    assert!(close_to(s.accel_m_s2[1], -235.0 * ACCEL_LSB));
    assert!(close_to(s.accel_m_s2[2], 8052.0 * ACCEL_LSB));
    assert!(s.mag.is_none());
    let g = s.accel_m_s2.iter().map(|v| v * v).sum::<f32>().sqrt();
    assert!(g > 9.5 && g < 10.2); // the check a wrong layout fails: about 9.8 at rest
}

#[test]
fn captured_mid_session_packets() {
    // captured: 506 s in, 1 ms apart; the temperature word has climbed.
    let a = parse_imu(&hex("aa 8f 00 88 eb b8 10 00 f0 89 4d 00 00 00 f4 ff 04 00 09 ff 96 02 17 20 98 0a 00 00")).unwrap();
    let b = parse_imu(&hex("aa 8f 00 88 ec b8 10 00 fa 89 4d 00 00 00 f3 ff 03 00 1e ff 8c 02 1b 20 98 0a 00 00")).unwrap();
    assert_eq!(a.seq + 1, b.seq);
    assert_eq!(b.t_ns - a.t_ns, 1_000_000);
}

#[test]
fn stream_packets_that_are_not_imu_are_refused() {
    // captured: a display event from 0x83, a camera tile header from 0x82, the MCU's start reply.
    assert!(parse_imu(&hex("aa 8f 00 91 00 00 00 00 58 1e 01 00 00 00 00 00")).is_none());
    assert!(parse_imu(&hex("aa 8f 00 12 01 00 f4 3f d2 20 01 00 11 0e 14 14 11 0f 10 14 15 0e 11 0d 10 12 0e 13")).is_none());
    assert!(parse_imu(&hex("fa 55 95 00 00 00")).is_none());
    let good = hex(FIRST);
    for n in 0..good.len() {
        assert!(parse_imu(&good[..n]).is_none(), "cut to {n}");
    }
    // Trailing bytes are padding.
    let mut padded = good.clone();
    padded.extend_from_slice(&[0x5a; 36]);
    assert_eq!(parse_imu(&padded).map(|s| bits(&s)), parse_imu(&good).map(|s| bits(&s)));
    // An all-zero accelerometer is a blank packet, and the length field must say 16.
    assert!(parse_imu(&hex("aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 00 00 00 00 00 00 e8 06 00 00")).is_none());
    assert!(parse_imu(&hex("aa 8f 00 88 3c 15 0f 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00")).is_none());
}

#[test]
fn temperature_word() {
    // captured: 0x06e8 at the start of the session, 0x0a98 after 8 minutes.
    let cold = luma_temperature_c(&hex(FIRST)).unwrap();
    let warm =
        luma_temperature_c(&hex("aa 8f 00 88 eb b8 10 00 f0 89 4d 00 00 00 f4 ff 04 00 09 ff 96 02 17 20 98 0a 00 00")).unwrap();
    assert!(cold > 38.0 && cold < 39.0, "{cold}");
    assert!(warm > 45.0 && warm < 46.0, "{warm}");
    assert_eq!(cold.to_bits(), (0x06e8 as f32 / 132.48 + 25.0).to_bits());
    assert!(luma_temperature_c(&hex("aa 8f 00 91 00 00 00 00 58 1e 01 00 00 00 00 00")).is_none());
}

#[test]
fn encoder_is_the_parser_inverse() {
    let real = hex(FIRST);
    let s = parse_imu(&real).unwrap();
    assert_eq!(encode_luma_imu(&s, 0x6e8).as_slice(), real.as_slice());
    // Every fixture packet goes back to its own bytes.
    for p in packets("luma_imu_real.bin").iter().chain(&packets("luma_imu_upside_down.bin")) {
        let temperature = u32::from_le_bytes(p[24..28].try_into().unwrap());
        assert_eq!(&encode_luma_imu(&parse_imu(p).unwrap(), temperature), p);
    }
}

fn at(seq: u32) -> ImuSample {
    ImuSample { seq, ..ImuSample::default() }
}

#[test]
fn tracker_widens_the_16_bit_sequence() {
    let mut tracker = luma_sequence_tracker();
    let mut first = ImuSample { seq: 65534, t_ns: 1, ..ImuSample::default() };
    assert_eq!(tracker.observe(&mut first).verdict, Verdict::Accepted);
    let base = first.seq;
    for i in 1..=5 {
        let mut s = at((65534 + i) & 0xffff);
        let r = tracker.observe(&mut s);
        assert!(r.verdict == Verdict::Accepted && r.lost == 0 && !r.restarted);
        assert_eq!(s.seq, base + i); // does not wrap
        assert_eq!(s.t_ns, u64::from(base + i) * 1_000_000);
    }
    // the session's first 63 packets come twice
    assert_eq!(tracker.observe(&mut at(3)).verdict, Verdict::Duplicate);
    assert_eq!(tracker.observe(&mut at(3 + 36)).lost, 35);
    assert_eq!(tracker.lost_total(), 35);
}

#[test]
fn tracker_in_order_and_gaps() {
    let mut t = SequenceTracker::default();
    let (mut a, mut b) = (at(100), at(101));
    assert_eq!(t.observe(&mut a).verdict, Verdict::Accepted);
    assert_eq!(a.t_ns, 100_000_000);
    let r = t.observe(&mut b);
    assert!(r.verdict == Verdict::Accepted && r.lost == 0 && !r.restarted);
    assert_eq!(b.t_ns, 101_000_000);

    let mut t = SequenceTracker::default();
    let mut c = at(16);
    t.observe(&mut at(10));
    assert_eq!(t.observe(&mut at(14)).lost, 3);
    assert_eq!(t.observe(&mut c).lost, 1);
    assert_eq!(t.lost_total(), 4);
    assert_eq!(c.t_ns, 16_000_000);
}

#[test]
fn tracker_rejects_duplicate_and_late_packets() {
    let mut t = SequenceTracker::default();
    let mut dup = ImuSample { t_ns: 777, ..at(1000) };
    t.observe(&mut at(1000));
    assert_eq!(t.observe(&mut dup).verdict, Verdict::Duplicate);
    assert_eq!(dup.t_ns, 777);
    assert_eq!(t.observe(&mut at(990)).verdict, Verdict::Stale);
    // A late packet must not move the reference: the next one is still in order.
    let r = t.observe(&mut at(1001));
    assert!(r.verdict == Verdict::Accepted && r.lost == 0);
}

#[test]
fn tracker_survives_32_bit_wrap() {
    let mut t = SequenceTracker::default();
    let (mut b, mut c) = (at(0), at(2));
    t.observe(&mut at(u32::MAX));
    let r = t.observe(&mut b);
    assert!(r.verdict == Verdict::Accepted && r.lost == 0 && !r.restarted);
    assert_eq!(b.t_ns, (1u64 << 32) * 1_000_000);
    assert_eq!(t.observe(&mut c).lost, 1);
    assert!(c.t_ns > b.t_ns);
}

#[test]
fn tracker_device_restart_keeps_time_moving_forward() {
    let mut t = SequenceTracker::default();
    let (mut a, mut b, mut c) = (at(5_000_000), at(3), at(4));
    t.observe(&mut a);
    let r = t.observe(&mut b);
    assert!(r.verdict == Verdict::Accepted && r.restarted && r.lost == 0);
    assert!(b.t_ns > a.t_ns);
    t.observe(&mut c);
    assert_eq!(c.t_ns, b.t_ns + 1_000_000);
}

#[test]
fn tracker_reset_for_a_reconnect() {
    let mut t = SequenceTracker::default();
    let (mut a, mut b, mut c) = (at(100), at(100), at(95));
    t.observe(&mut a);
    t.reset();
    // After a reconnect, even a repeat or a lower value is a fresh start.
    assert_eq!(t.observe(&mut b).verdict, Verdict::Accepted);
    assert!(b.t_ns > a.t_ns);
    t.reset();
    assert_eq!(t.observe(&mut c).verdict, Verdict::Accepted);
    assert!(c.t_ns > b.t_ns);
    assert_eq!(t.lost_total(), 0);
}

fn packet(t_ns: u64, seq: u32) -> [u8; LUMA_IMU_PACKET_SIZE] {
    encode_luma_imu(&ImuSample { t_ns, seq, accel_m_s2: [0.0, 0.0, 9.8], ..ImuSample::default() }, 2000)
}

/// Port of `test_display_events_ride_the_imu_clock` (daemon_test.cpp), on the IMU side: device
/// time widens across the 32-bit tick wrap and starts again on a new connection.
#[test]
fn decoder_device_time_widens_across_the_wrap() {
    let tick = LUMA_TICK_NS;
    let wrap = 1u64 << 32;
    let mut d = LumaDecoder::default();
    assert!(d.decode(&packet((wrap - 30) * tick, 0)).is_none()); // a connection's first is skipped
    assert!(d.last_was_repeat());
    assert_eq!(d.device_ns(), None);
    assert!(d.decode(&packet((wrap - 20) * tick, 1)).is_some());
    assert!(!d.last_was_repeat());
    assert_eq!(d.device_ns(), Some((wrap - 20) * tick));
    assert!(d.decode(&packet((wrap + 10) * tick, 2)).is_some());
    assert_eq!(d.device_ns(), Some((wrap + 10) * tick)); // past the wrap
    assert!(d.decode(&packet(5 * tick, 2)).is_none()); // a duplicate is a repeat, not junk
    assert!(d.last_was_repeat());
    assert!(d.decode(&[0u8; 28]).is_none());
    assert!(!d.last_was_repeat());
    d.new_connection();
    assert_eq!(d.device_ns(), None);
    assert!(d.decode(&packet(7 * tick, 9)).is_none());
    assert!(d.decode(&packet(100 * tick, 10)).is_some());
    assert_eq!(d.device_ns(), Some(100 * tick));

    let mut clock = DeviceClock::default();
    assert_eq!(clock.widen(u32::MAX - 2), u64::from(u32::MAX - 2) * tick);
    assert_eq!(clock.widen(5), (wrap + 5) * tick);
    assert_eq!(clock.widen(u32::MAX), u64::from(u32::MAX) * tick); // a little back: not a wrap
}

/// xorshift64*, so the fuzz is the same on every run.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

fn check_sane(s: Option<ImuSample>) -> bool {
    let Some(s) = s else { return false };
    assert!(s.gyro_rad_s.iter().all(|v| v.is_finite() && v.abs() <= 70.0), "{s:?}");
    assert!(s.accel_m_s2.iter().all(|v| v.is_finite() && v.abs() <= 320.0), "{s:?}");
    assert!(s.accel_m_s2.iter().any(|&v| v != 0.0), "{s:?}");
    true
}

#[test]
fn fuzz_random_bytes_and_headers() {
    let mut rng = Rng(0x1104);
    let mut buf = [0u8; 64];
    let mut decoded = 0;
    for _ in 0..1_000_000 {
        let n = rng.below(buf.len() as u64 + 1) as usize;
        buf[..n].iter_mut().for_each(|b| *b = rng.next() as u8);
        // Half get a valid header, so the decoding past it runs too.
        if rng.below(2) == 0 && n >= 8 {
            buf[..4].copy_from_slice(&[0xaa, 0x8f, 0x00, 0x88]);
            buf[6..8].copy_from_slice(&16u16.to_le_bytes());
        }
        let p = &buf[..n];
        decoded += usize::from(check_sane(parse_imu(p)));
        let _ = luma_temperature_c(p);
    }
    assert!(decoded > 100_000, "only {decoded} decoded: the fuzz barely reached the decoder");
}

#[test]
fn fuzz_mutations_of_real_packets() {
    let mut rng = Rng(0x35ca);
    let real = packets("luma_imu_real.bin");
    let mut decoder = LumaDecoder::default();
    let mut last_t = 0;
    for i in 0..1_000_000 {
        let mut p = real[rng.below(real.len() as u64) as usize].to_vec();
        for _ in 0..1 + rng.below(4) {
            let at = rng.below(p.len() as u64) as usize;
            p[at] = rng.next() as u8;
        }
        p.truncate(rng.below(p.len() as u64 + 8) as usize);
        check_sane(parse_imu(&p));
        let _ = luma_temperature_c(&p);
        if rng.below(20_000) == 0 {
            decoder.new_connection();
        }
        if let Some(s) = decoder.decode(&p) {
            assert!(s.t_ns >= last_t, "packet {i}: time went back");
            last_t = s.t_ns;
            assert!(decoder.device_ns().is_some());
        }
    }
    // Every single bit flip of a real packet either decodes sanely or is refused.
    let good = hex(FIRST);
    for bit in 0..good.len() * 8 {
        let mut p = good.clone();
        p[bit / 8] ^= 1 << (bit % 8);
        check_sane(parse_imu(&p));
    }
}

#[test]
fn fuzz_tracker_time_never_goes_backwards() {
    for (bits, seed) in [(32, 42), (16, 43)] {
        let mut rng = Rng(seed);
        let mut t = SequenceTracker::new(1_000_000, bits);
        let mut last = 0;
        let mut seq = 0u32;
        for _ in 0..1_000_000 {
            seq = seq.wrapping_add(rng.below(5) as u32).wrapping_sub(1);
            if rng.below(2000) == 0 {
                seq = rng.next() as u32;
            }
            if rng.below(5000) == 0 {
                t.reset();
            }
            let mut s = at(if bits == 16 { seq & 0xffff } else { seq });
            if t.observe(&mut s).verdict == Verdict::Accepted {
                assert!(s.t_ns >= last);
                last = s.t_ns;
            }
        }
    }
}

