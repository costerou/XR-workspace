//! The Luma Ultra's IMU stream: the 28-byte packet, its temperature word, the sequence tracker
//! that turns per-packet counters into one timeline, and the decoder the daemon plugs in.
//! Port of the Luma Ultra path of `spike/driver/src/protocol.cpp` and of `RealDecoder` in
//! `spike/driver/src/wiring.cpp`. The layout was measured from a passive usbmon capture
//! (`spike/driver/docs/capture-findings.md`); its full story is in `luma/protocol.hpp`.
//!
//! Only the Luma Ultra's own framing is here. The C++ `parse_imu` also decodes the Pro 2's
//! protocol V2 frames, which start `10 00`, never `AA 8F`, so on Luma Ultra packets the two give the
//! same answer; the Rust Pro 2 bridge takes its pose from the SDK and needs no V2 parser.
//!
//! The stream packet, all little-endian, as measured:
//!
//! | Bytes  | Field |
//! |--------|-------|
//! | 0..2   | `AA 8F 00` |
//! | 3      | type: `0x88` IMU on `0x81` (`0x91` display event on `0x83`, others on camera `0x82`) |
//! | 4..5   | sequence, +1 per packet, wraps at 65536 (65.5 s at 1 kHz) |
//! | 6..7   | payload length, 16 for an IMU packet |
//! | 8..11  | device time, 100 µs per tick, from the device's boot |
//! | 12..17 | gyro x, y, z, `i16`, 16.4 LSB per °/s |
//! | 18..23 | accel x, y, z, `i16`, 8192 LSB per g |
//! | 24..27 | `u32`, most likely temperature: climbs 1760 to 2910 as the glasses warm |
//!
//! On the wire forward = -y, left = +x, up = +z; [`parse_imu`] returns the body frame Fusion
//! wants, x forward, y left, z up, accel as specific force (+9.8 on z when level).
//!
//! Every function here is total: no input panics, allocates or blocks, so all of it is safe on the
//! real-time thread. `tests/protocol.rs` fuzzes that.

use crate::daemon::Decoder;
use crate::types::ImuSample;

pub const LUMA_IMU_ENDPOINT: u8 = 0x81;
pub const LUMA_IMU_PACKET_SIZE: usize = 28;
pub const LUMA_IMU_TYPE: u8 = 0x88;
pub const LUMA_IMU_PAYLOAD: u16 = 16;
/// One device-time tick.
pub const LUMA_TICK_NS: u64 = 100_000;
/// One sample: the stream runs at 1 kHz.
pub const LUMA_SAMPLE_NS: u64 = 1_000_000;
pub const LUMA_GYRO_LSB_PER_DEG_S: f32 = 16.4;
pub const LUMA_ACCEL_LSB_PER_G: f32 = 8192.0;
pub const LUMA_SEQ_BITS: u32 = 16;
pub const STANDARD_GRAVITY: f32 = 9.80665;

// The C++ spells pi to 15 digits in a float; the same literal gives the same f32.
#[allow(clippy::approx_constant, clippy::excessive_precision)]
const DEG_TO_RAD: f32 = 3.14159265358979_f32 / 180.0;
const GYRO_SCALE: f32 = DEG_TO_RAD / LUMA_GYRO_LSB_PER_DEG_S;
const ACCEL_SCALE: f32 = STANDARD_GRAVITY / LUMA_ACCEL_LSB_PER_G;

fn le16(p: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([p[at], p[at + 1]])
}

fn le32(p: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([p[at], p[at + 1], p[at + 2], p[at + 3]])
}

/// Decodes one IMU packet, or nothing if it is too short, another message, has a length field
/// other than 16, or an accelerometer reading of exactly zero on every axis (a blank packet: no
/// accelerometer reads that). Trailing bytes past 28 are ignored.
///
/// `t_ns` is device time (ticks × 100 µs) and `seq` the 16-bit wire sequence: pass both through
/// a tracker made by [`luma_sequence_tracker`], as [`LumaDecoder`] does.
pub fn parse_imu(p: &[u8]) -> Option<ImuSample> {
    if p.len() < LUMA_IMU_PACKET_SIZE || p[..4] != [0xaa, 0x8f, 0x00, LUMA_IMU_TYPE] {
        return None;
    }
    if le16(p, 6) != LUMA_IMU_PAYLOAD {
        return None;
    }
    let axis = |at: usize| f32::from(le16(p, at) as i16);
    let (ax, ay, az) = (axis(18), axis(20), axis(22));
    if ax == 0.0 && ay == 0.0 && az == 0.0 {
        return None;
    }
    // The i16 range bounds every value well inside the C++ sanity limits (±70 rad/s, ±320 m/s²).
    Some(ImuSample {
        t_ns: u64::from(le32(p, 8)) * LUMA_TICK_NS,
        gyro_rad_s: [-axis(14) * GYRO_SCALE, axis(12) * GYRO_SCALE, axis(16) * GYRO_SCALE],
        accel_m_s2: [-ay * ACCEL_SCALE, ax * ACCEL_SCALE, az * ACCEL_SCALE],
        mag: None,
        seq: u32::from(le16(p, 4)),
    })
}

/// The packet's temperature word in °C, or nothing for a packet that is not IMU. The trend is
/// measured (38 to 47 over a warm-up); the formula is the one common to this class of sensor, so
/// the absolute value is not proven. Like the C++, it does not look at byte 2 or the length field.
pub fn luma_temperature_c(p: &[u8]) -> Option<f32> {
    if p.len() < LUMA_IMU_PACKET_SIZE || p[0] != 0xaa || p[1] != 0x8f || p[3] != LUMA_IMU_TYPE {
        return None;
    }
    Some(f32::from(le16(p, 24)) / 132.48 + 25.0)
}

/// The inverse of [`parse_imu`], for fixtures and fakes: the body frame goes back to wire axes,
/// values round to the nearest LSB and saturate at the `i16` range.
pub fn encode_luma_imu(s: &ImuSample, temperature_raw: u32) -> [u8; LUMA_IMU_PACKET_SIZE] {
    let mut b = [0u8; LUMA_IMU_PACKET_SIZE];
    b[..4].copy_from_slice(&[0xaa, 0x8f, 0x00, LUMA_IMU_TYPE]);
    let mut put16 = |at: usize, v: u16| b[at..at + 2].copy_from_slice(&v.to_le_bytes());
    // `round` is away from zero, as `lround`; `as` saturates where the C++ clamps first.
    let lsb = |v: f32| v.round() as i16 as u16;
    let gyro = LUMA_GYRO_LSB_PER_DEG_S / DEG_TO_RAD;
    let accel = LUMA_ACCEL_LSB_PER_G / STANDARD_GRAVITY;
    put16(4, s.seq as u16);
    put16(6, LUMA_IMU_PAYLOAD);
    let ticks = (s.t_ns / LUMA_TICK_NS) as u32;
    put16(8, ticks as u16);
    put16(10, (ticks >> 16) as u16);
    put16(12, lsb(s.gyro_rad_s[1] * gyro)); // wire x = left
    put16(14, lsb(-s.gyro_rad_s[0] * gyro)); // wire y = back
    put16(16, lsb(s.gyro_rad_s[2] * gyro)); // wire z = up
    put16(18, lsb(s.accel_m_s2[1] * accel));
    put16(20, lsb(-s.accel_m_s2[0] * accel));
    put16(22, lsb(s.accel_m_s2[2] * accel));
    b[24..28].copy_from_slice(&temperature_raw.to_le_bytes());
    b
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Accepted,
    Duplicate,
    Stale,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Observed {
    pub verdict: Verdict,
    /// Samples missing just before this one.
    pub lost: u32,
    /// The device's counter started over.
    pub restarted: bool,
}

impl Observed {
    const fn rejected(verdict: Verdict) -> Self {
        Self { verdict, lost: 0, restarted: false }
    }
}

/// Turns per-packet counters into one ordered timeline.
///
/// [`parse_imu`] is stateless, so it cannot see a wrap, a lost sample or a device that restarted.
/// Pass every sample through [`observe`](Self::observe) and drop what it does not accept. An
/// accepted sample gets a `t_ns` that never goes backwards, not across a wrap, a restart or
/// [`reset`](Self::reset). Below 32 bits the tracker also widens `seq` to a count that does not
/// wrap, which Fusion's gap check relies on.
#[derive(Clone, Debug)]
pub struct SequenceTracker {
    tick_ns: u64,
    mask: u32,
    ticks: u64,
    lost_total: u64,
    last: u32,
    have_last: bool,
    have_ticks: bool,
}

impl SequenceTracker {
    /// A backward step up to this size is a late packet; beyond it the device's counter
    /// restarted. At 1 kHz it is 64 ms of samples.
    pub const MAX_STALE_STEP: u32 = 64;

    /// `seq_bits`: how wide the device's counter is.
    pub fn new(tick_ns: u64, seq_bits: u32) -> Self {
        let mask = if seq_bits >= 32 { u32::MAX } else { (1u32 << seq_bits) - 1 };
        Self { tick_ns, mask, ticks: 0, lost_total: 0, last: 0, have_last: false, have_ticks: false }
    }

    /// Unsigned arithmetic wraps on purpose throughout, as in the C++: the counter's wrap is one
    /// ordinary step forward.
    pub fn observe(&mut self, sample: &mut ImuSample) -> Observed {
        let mut result = Observed::rejected(Verdict::Accepted);
        if !self.have_last {
            self.ticks = if self.have_ticks { self.ticks.wrapping_add(1) } else { u64::from(sample.seq) };
        } else {
            let forward = sample.seq.wrapping_sub(self.last) & self.mask;
            let backward = self.last.wrapping_sub(sample.seq) & self.mask;
            if forward == 0 {
                return Observed::rejected(Verdict::Duplicate);
            }
            if forward <= self.mask / 2 {
                result.lost = forward - 1;
                self.lost_total = self.lost_total.wrapping_add(u64::from(result.lost));
                self.ticks = self.ticks.wrapping_add(u64::from(forward));
            } else if backward <= Self::MAX_STALE_STEP {
                return Observed::rejected(Verdict::Stale);
            } else {
                result.restarted = true;
                self.ticks = self.ticks.wrapping_add(1);
            }
        }
        self.last = sample.seq;
        self.have_last = true;
        self.have_ticks = true;
        sample.t_ns = self.ticks.wrapping_mul(self.tick_ns);
        if self.mask != u32::MAX {
            sample.seq = self.ticks as u32;
        }
        result
    }

    /// Call after a reconnect: the next sample starts a fresh count.
    pub fn reset(&mut self) {
        self.have_last = false;
    }

    pub fn lost_total(&self) -> u64 {
        self.lost_total
    }
}

impl Default for SequenceTracker {
    /// A 32-bit counter, 1 ms per step.
    fn default() -> Self {
        Self::new(LUMA_SAMPLE_NS, 32)
    }
}

/// The tracker for the Luma Ultra's stream: one sample per millisecond, 16-bit sequence. Its
/// `t_ns` counts samples, not the device's 100 µs clock, on purpose: over the capture the packets
/// ran at 1000.00 Hz of host time within 8 ppm, while the device stamp ran 65 ppm fast and carries
/// ±100 µs of jitter.
pub fn luma_sequence_tracker() -> SequenceTracker {
    SequenceTracker::new(LUMA_SAMPLE_NS, LUMA_SEQ_BITS)
}

/// The device's 32-bit 100 µs clock, widened to 64 bits. It wraps every five days; the IMU and
/// the display events share it and arrive within far less than half that apart, so each new
/// stamp is a signed step from the last.
#[derive(Clone, Copy, Debug, Default)]
pub struct DeviceClock {
    wide_ticks: Option<u64>,
}

impl DeviceClock {
    pub fn widen(&mut self, ticks: u32) -> u64 {
        let wide = match self.wide_ticks {
            None => u64::from(ticks),
            Some(w) => w.wrapping_add(ticks.wrapping_sub(w as u32) as i32 as i64 as u64),
        };
        self.wide_ticks = Some(wide);
        wide.wrapping_mul(LUMA_TICK_NS)
    }

    pub fn reset(&mut self) {
        self.wide_ticks = None;
    }
}

/// The real decoder: [`parse_imu`], the first packet of each connection skipped, then the Luma
/// sequence tracker. Port of `RealDecoder` in `spike/driver/src/wiring.cpp`.
#[derive(Clone, Debug)]
pub struct LumaDecoder {
    tracker: SequenceTracker,
    clock: DeviceClock,
    first_of_connection: bool,
    repeat: bool,
    temperature: Option<f32>,
    device_ns: Option<u64>,
}

impl Default for LumaDecoder {
    fn default() -> Self {
        Self {
            tracker: luma_sequence_tracker(),
            clock: DeviceClock::default(),
            first_of_connection: true,
            repeat: false,
            temperature: None,
            device_ns: None,
        }
    }
}

impl LumaDecoder {
    /// Device time of the newest accepted sample on the widened clock, nothing before the first
    /// of a connection. The sample's own `t_ns` is the tracker's sample count instead.
    pub fn device_ns(&self) -> Option<u64> {
        self.device_ns
    }
}

impl Decoder for LumaDecoder {
    fn decode(&mut self, packet: &[u8]) -> Option<ImuSample> {
        self.repeat = false;
        let mut sample = parse_imu(packet)?;
        // The stream outlives any reader, and the glasses keep the next packet back for whoever
        // reads next, however long that takes. Taken as live it is a false gap in `lost`, a false
        // time jump, and with a saved bias a start tilted to wherever the glasses were then:
        // fusion's first sample sets its attitude. Measured 2026-11-04: 24 degrees off, settling
        // over 10 s. Skipping it costs 1 ms when it happens to be fresh.
        if self.first_of_connection {
            self.first_of_connection = false;
            self.repeat = true;
            return None;
        }
        self.temperature = luma_temperature_c(packet);
        let ticks = (sample.t_ns / LUMA_TICK_NS) as u32; // the tracker puts its own time in t_ns
        if self.tracker.observe(&mut sample).verdict == Verdict::Accepted {
            self.device_ns = Some(self.clock.widen(ticks));
            return Some(sample);
        }
        self.repeat = true;
        None
    }

    fn new_connection(&mut self) {
        self.tracker.reset();
        self.first_of_connection = true;
        self.clock.reset();
        self.device_ns = None;
    }

    fn lost_samples(&self) -> u64 {
        self.tracker.lost_total()
    }

    fn last_was_repeat(&self) -> bool {
        self.repeat
    }

    fn temperature_c(&self) -> Option<f32> {
        self.temperature
    }
}
