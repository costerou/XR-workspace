//! The one module that knows the real Transport, the commands, Fusion and the feed Publisher: a port of
//! `spike/driver/src/wiring.cpp` and `include/luma/transport_link.hpp`.
//!
//! Not ported yet: the VsyncState feed (`--read-events` still reads 0x83, and throws it away, as the usage says) and
//! PredictionWire's motion state (the daemon's `Sink` has no sample to give it). `--latency-log` and `--feature-*` are
//! refused by the binary rather than run differently. The saved gyro bias is `crate::bias`, wired in `src/main.rs`.

use crate::daemon::{Link, ReadResult, Sink};
use crate::options::{Options, Start};
use crate::protocol::LUMA_IMU_ENDPOINT;
use crate::transport::{
    IoStatus, LUMA_ULTRA_DATA_IN, LUMA_ULTRA_DATA_OUT, LUMA_ULTRA_FLASH_IN, LUMA_ULTRA_FLASH_OUT, Transport,
    TransportConfig,
};
use crate::types::PoseOut;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::time::Duration;
use xr_pose::feed::WriterOptions;
use xr_pose::publish::{Pose, Publisher, PublisherOptions, Quat, history_step_for};

/// Interrupt IN: 16-byte type 0x91 frames at the display rate.
pub const LUMA_EVENT_ENDPOINT: u8 = 0x83;
/// Bulk IN: 16 KiB tiles, 19 MB/s while the cameras run. Never read by default.
pub const LUMA_CAMERA_ENDPOINT: u8 = 0x82;
/// Sent 100 ms before 0x95 by the SDK; its meaning is unknown.
pub const MCU_BEFORE_START: u8 = 0x97;
pub const MCU_START_STREAM: u8 = 0x95;
/// The data channel's packet: every request is padded to this.
pub const DATA_PACKET_SIZE: usize = 512;

/// Reads kept queued on the IMU endpoint: eight 1 ms packets.
const IMU_BURST: usize = 8;
/// One short command; the pose thread must not sit in it for long.
const WRITE_TIMEOUT: Duration = Duration::from_millis(200);

/// `FA 55 <command> <length, 2 bytes big-endian> <payload>`, the MCU channel's frame.
pub fn mcu_request(command: u8) -> Vec<u8> {
    vec![0xfa, 0x55, command, 0x00, 0x00]
}

/// CRC-16/XMODEM, polynomial 0x1021, initial 0.
pub fn crc16_xmodem(bytes: &[u8]) -> u16 {
    let mut crc: u16 = 0;
    for &byte in bytes {
        crc ^= u16::from(byte) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

/// `FF FE <crc> <pklen> ... <msg id> ... <payload>`, padded to [`DATA_PACKET_SIZE`]. The CRC covers the length field
/// through the payload, not the padding after it.
pub fn data_request(msg_id: u16, payload: &[u8]) -> Option<Vec<u8>> {
    const CHECKED_FROM: usize = 4;
    const LENGTH_BASE: usize = 6;
    const MIN_PKLEN: usize = 12;
    const MSG_ID_OFFSET: usize = 14;
    const BODY_OFFSET: usize = 18;
    if payload.len() > DATA_PACKET_SIZE - BODY_OFFSET {
        return None;
    }
    let mut b = vec![0u8; DATA_PACKET_SIZE];
    let pklen = MIN_PKLEN + payload.len();
    b[0] = 0xff;
    b[1] = 0xfe;
    b[4..6].copy_from_slice(&(pklen as u16).to_le_bytes());
    b[MSG_ID_OFFSET..MSG_ID_OFFSET + 2].copy_from_slice(&msg_id.to_le_bytes());
    b[BODY_OFFSET..BODY_OFFSET + payload.len()].copy_from_slice(payload);
    let crc = crc16_xmodem(&b[CHECKED_FROM..LENGTH_BASE + pklen]);
    b[2..4].copy_from_slice(&crc.to_le_bytes());
    Some(b)
}

/// FA 55 95: the only command a stream needs.
pub fn start_command() -> Vec<u8> {
    mcu_request(MCU_START_STREAM)
}

/// Empty: no capture has shown a stop for the Luma Ultra, and sending a guess is what S3 forbids.
pub fn stop_command() -> Vec<u8> {
    Vec::new()
}

pub fn start_for(mode: Start) -> Vec<u8> {
    if mode == Start::Listen { Vec::new() } else { start_command() }
}

pub fn before_start(mode: Start) -> Vec<Vec<u8>> {
    let mut commands = Vec::new();
    if mode == Start::Full {
        commands.push(data_request(0x100b, &[0x01]).expect("one byte fits"));
    }
    if matches!(mode, Start::Full | Start::Prepared) {
        commands.push(mcu_request(MCU_BEFORE_START));
    }
    commands
}

/// What the live driver opens: the IMU and the MCU channel, and only what the options ask for besides.
pub fn live_config(options: &Options) -> TransportConfig {
    let mut config = TransportConfig {
        in_endpoints: vec![LUMA_IMU_ENDPOINT, LUMA_ULTRA_FLASH_IN],
        out_endpoint: LUMA_ULTRA_FLASH_OUT,
        allow_mcu_channel: true,
        urbs_per_endpoint: IMU_BURST,
        ..TransportConfig::default()
    };
    if options.read_events {
        config.in_endpoints.push(LUMA_EVENT_ENDPOINT);
    }
    if options.drain_camera {
        config.drain_endpoints.push(LUMA_CAMERA_ENDPOINT);
    }
    if options.start == Start::Full {
        config.extra_out_endpoints.push(LUMA_ULTRA_DATA_OUT);
        config.in_endpoints.push(LUMA_ULTRA_DATA_IN);
    }
    config
}

fn hex_of(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// [`Transport`] as the daemon's [`Link`]. Transport reconnects inside `read()` and reports Connected, while the daemon
/// wants to decide when a link is open, so:
/// - `open()` starts it (first time), and is Ok once Connected has been seen and not been followed by a loss;
/// - `wait_for_device()` is `read()` while down: that is where Transport's sysfs rescan runs, and where Connected arrives.
///
/// The daemon's `read()` sees only the IMU endpoint. Everything else (the MCU's reply to the start, say) goes to the log,
/// on the same thread; the display events on 0x83 are thrown away, 120 a second that say nothing.
pub struct TransportLink<'a> {
    transport: Transport,
    started: bool,
    link_up: bool,
    log: Box<dyn FnMut(&str) + 'a>,
}

impl<'a> TransportLink<'a> {
    pub fn new(transport: Transport, log: Box<dyn FnMut(&str) + 'a>) -> Self {
        TransportLink { transport, started: false, link_up: false, log }
    }

    pub fn transport(&self) -> &Transport {
        &self.transport
    }

    fn pump(&mut self, timeout: Duration) -> bool {
        let mut discard = [0u8; 1024]; // nothing arrives while down; a packet after Connected is one sample lost at most
        let r = self.transport.read(&mut discard, timeout);
        match r.status {
            IoStatus::Connected => self.link_up = true,
            IoStatus::Ok if !self.link_up => self.link_up = true,
            IoStatus::Disconnected | IoStatus::Closed => self.link_up = false,
            _ => {}
        }
        self.link_up
    }
}

impl Link for TransportLink<'_> {
    fn open(&mut self) -> Result<(), String> {
        if !self.started {
            self.started = true;
            self.transport.open();
        }
        if !self.link_up {
            self.pump(Duration::ZERO);
        }
        if self.link_up { Ok(()) } else { Err(self.transport.last_error().to_owned()) }
    }

    fn read(&mut self, into: &mut [u8], timeout: Duration) -> ReadResult {
        let r = self.transport.read(into, timeout);
        match r.status {
            IoStatus::Ok => {
                if r.endpoint != LUMA_IMU_ENDPOINT {
                    if r.endpoint != LUMA_EVENT_ENDPOINT {
                        let head = &into[..r.bytes.min(into.len()).min(16)];
                        (self.log)(&format!(
                            "luma: reply endpoint=0x{:02x} bytes={} head={}",
                            r.endpoint,
                            r.bytes,
                            hex_of(head)
                        ));
                    }
                    return ReadResult::TIMEOUT;
                }
                ReadResult::data(r.bytes, r.t_ns)
            }
            IoStatus::Disconnected | IoStatus::Closed => {
                self.link_up = false;
                ReadResult::GONE
            }
            _ => ReadResult::TIMEOUT, // Timeout, Interrupted, and a Connected that needs nothing from us
        }
    }

    fn write(&mut self, bytes: &[u8]) -> bool {
        let endpoint = if bytes.first() == Some(&0xff) { LUMA_ULTRA_DATA_OUT } else { LUMA_ULTRA_FLASH_OUT };
        self.transport.write_to(endpoint, bytes, WRITE_TIMEOUT) == IoStatus::Ok
    }

    fn close(&mut self) {
        // A silent device: let go of it entirely. A gone one: Transport is already looking for it, so keep that going.
        if self.link_up {
            self.transport.close();
            self.started = false;
            self.link_up = false;
        }
    }

    fn wait_for_device(&mut self, timeout: Duration) -> bool {
        self.pump(timeout)
    }
}

/// The SysV feed the renderer reads.
pub struct PublishSink {
    publisher: Publisher,
}

impl Sink for PublishSink {
    fn publish(&mut self, pose: &PoseOut) {
        self.publisher.publish(&Pose {
            q: Quat { w: pose.q.w, x: pose.q.x, y: pose.q.y, z: pose.q.z },
            t_ns: pose.t_ns,
            calibrated: pose.calibrated,
        });
    }

    fn set_stale(&mut self, stale: bool) {
        if stale {
            self.publisher.pause(); // the next publish clears it
        }
    }
}

/// Only the real feed is shared and left in place. Anything else, a replay or a bench run, makes its own segments and
/// removes them: ftok keys hold 16 bits of the inode, so attaching to whatever exists could land on the live feed.
pub fn feed_sink(ipc_prefix: &str, private_segments: bool) -> Result<PublishSink, String> {
    let options = PublisherOptions {
        feed: WriterOptions { prefix: ipc_prefix.to_owned(), private_segments, ..WriterOptions::default() },
        history_step: history_step_for(1000),
        ..PublisherOptions::default()
    };
    let publisher = Publisher::open(&options).map_err(|e| e.0)?;
    Ok(PublishSink { publisher })
}

/// `--pose-log`: t_ns, then w x y z, as five native-endian doubles, for every Nth pose.
pub struct PoseLog<S: Sink> {
    inner: S,
    file: BufWriter<File>,
    every: u64,
    count: u64,
}

impl<S: Sink> PoseLog<S> {
    pub fn create(inner: S, path: &str, every: u32) -> Result<Self, String> {
        let file = File::create(path).map_err(|e| format!("cannot write {path}: {}", crate::instance::reason(&e)))?;
        Ok(PoseLog { inner, file: BufWriter::new(file), every: u64::from(every.max(1)), count: 0 })
    }
}

impl<S: Sink> Sink for PoseLog<S> {
    fn publish(&mut self, pose: &PoseOut) {
        self.inner.publish(pose);
        let n = self.count;
        self.count += 1;
        if n % self.every != 0 {
            return;
        }
        let row = [pose.t_ns as f64, f64::from(pose.q.w), f64::from(pose.q.x), f64::from(pose.q.y), f64::from(pose.q.z)];
        for v in row {
            let _ = self.file.write_all(&v.to_ne_bytes());
        }
    }

    fn set_stale(&mut self, stale: bool) {
        self.inner.set_stale(stale);
    }
}

impl<S: Sink> Drop for PoseLog<S> {
    fn drop(&mut self) {
        let _ = self.file.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn start_ladder_matches_the_cpp_wiring() {
        assert_eq!(start_for(Start::Listen), Vec::<u8>::new());
        assert_eq!(start_for(Start::Stream), vec![0xfa, 0x55, 0x95, 0, 0]);
        assert!(before_start(Start::Stream).is_empty());
        assert_eq!(before_start(Start::Prepared), vec![vec![0xfa, 0x55, 0x97, 0, 0]]);
        let full = before_start(Start::Full);
        assert_eq!(full.len(), 2);
        assert_eq!(full[1], vec![0xfa, 0x55, 0x97, 0, 0]);
        assert!(stop_command().is_empty());
    }

    #[test]
    fn crc_is_xmodem() {
        assert_eq!(crc16_xmodem(b"123456789"), 0x31c3);
    }

    #[test]
    fn data_request_frames_msg_0x100b() {
        let b = data_request(0x100b, &[0x01]).unwrap();
        assert_eq!(b.len(), DATA_PACKET_SIZE);
        assert_eq!(&b[..2], &[0xff, 0xfe]);
        assert_eq!(u16::from_le_bytes([b[4], b[5]]), 13);
        assert_eq!(u16::from_le_bytes([b[14], b[15]]), 0x100b);
        assert_eq!(b[18], 0x01);
        assert_eq!(u16::from_le_bytes([b[2], b[3]]), crc16_xmodem(&b[4..19]));
        assert!(b[19..].iter().all(|&x| x == 0));
    }

    #[test]
    fn live_config_opens_only_what_is_asked() {
        let mut o = Options::default();
        let c = live_config(&o);
        assert_eq!(c.in_endpoints, vec![0x81, 0x85]);
        assert_eq!(c.out_endpoint, 0x04);
        assert!(c.allow_mcu_channel && c.extra_out_endpoints.is_empty() && c.drain_endpoints.is_empty());
        o.read_events = true;
        o.drain_camera = true;
        o.start = Start::Full;
        let c = live_config(&o);
        assert_eq!(c.in_endpoints, vec![0x81, 0x85, 0x83, 0x87]);
        assert_eq!(c.drain_endpoints, vec![0x82]);
        assert_eq!(c.extra_out_endpoints, vec![0x06]);
    }
}
