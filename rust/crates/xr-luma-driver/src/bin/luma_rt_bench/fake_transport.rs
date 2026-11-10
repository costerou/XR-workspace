//! A pretend pair of glasses: a thread sends one packet per period through a real kernel object,
//! so the reader pays what it pays on the real device: a blocking poll(), a scheduler wake-up and
//! a read(). A packet-mode pipe with 64 slots stands in for the kernel's per-reader queue of 64
//! reports. The packet format is invented; only its size and the need to check a CRC stand for
//! the real one. The port of `spike/driver/bench/fake_transport.hpp`, byte for byte.

use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread::JoinHandle;
use xr_luma_driver::rt::{self, ThreadPolicy, apply_thread_policy, monotonic_raw_ns};

pub const PACKET_BYTES: usize = 64;

/// `{u32 seq, u32 reserved, u64 t_sent_ns, f32 gyro[3], f32 accel[3], u8 padding[22], u16 crc}`,
/// little-endian.
#[derive(Debug, Clone, Copy, Default)]
pub struct FakePacket {
    pub seq: u32,
    /// Stamped by the sender just before the write: what the bench measures from.
    pub t_sent_ns: u64,
    pub gyro: [f32; 3],
    pub accel: [f32; 3],
}

const CRC_TABLE: [u16; 256] = {
    let mut table = [0u16; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = (i as u16) << 8;
        let mut b = 0;
        while b < 8 {
            c = if c & 0x8000 != 0 {
                (c << 1) ^ 0x1021
            } else {
                c << 1
            };
            b += 1;
        }
        table[i] = c;
        i += 1;
    }
    table
};

/// CRC-16/CCITT-FALSE.
pub fn crc16(bytes: &[u8]) -> u16 {
    bytes.iter().fold(0xFFFF, |crc, &b| {
        (crc << 8) ^ CRC_TABLE[(((crc >> 8) ^ b as u16) & 0xFF) as usize]
    })
}

fn f32_at(b: &[u8], at: usize) -> f32 {
    f32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}

pub fn parse_fake(bytes: &[u8]) -> Option<FakePacket> {
    if bytes.len() != PACKET_BYTES {
        return None;
    }
    let crc = u16::from_le_bytes([bytes[62], bytes[63]]);
    if crc16(&bytes[..62]) != crc {
        return None;
    }
    Some(FakePacket {
        seq: u32::from_le_bytes(bytes[0..4].try_into().unwrap()),
        t_sent_ns: u64::from_le_bytes(bytes[8..16].try_into().unwrap()),
        gyro: [f32_at(bytes, 16), f32_at(bytes, 20), f32_at(bytes, 24)],
        accel: [f32_at(bytes, 28), f32_at(bytes, 32), f32_at(bytes, 36)],
    })
}

fn encode(p: &FakePacket) -> [u8; PACKET_BYTES] {
    let mut b = [0u8; PACKET_BYTES];
    b[0..4].copy_from_slice(&p.seq.to_le_bytes());
    b[8..16].copy_from_slice(&p.t_sent_ns.to_le_bytes());
    for k in 0..3 {
        b[16 + 4 * k..20 + 4 * k].copy_from_slice(&p.gyro[k].to_le_bytes());
        b[28 + 4 * k..32 + 4 * k].copy_from_slice(&p.accel[k].to_le_bytes());
    }
    let crc = crc16(&b[..62]);
    b[62..64].copy_from_slice(&crc.to_le_bytes());
    b
}

#[derive(Default)]
struct Counters {
    stop: AtomicBool,
    finished: AtomicBool,
    sent: AtomicU64,
    overflows: AtomicU64,
    late: AtomicU64,
}

pub struct FakeTransport {
    period_ns: u64,
    packets: u32,
    cpu: Option<usize>,
    rd: OwnedFd,
    wr: Arc<OwnedFd>,
    counters: Arc<Counters>,
    sender: Option<JoinHandle<()>>,
}

/// The reading end, for the reader thread. Holds the descriptor, not ownership: the transport must
/// outlive it.
pub struct FakeReader {
    fd: RawFd,
}

impl FakeReader {
    /// Bytes read, 0 on timeout, negative when the queue is closed and empty.
    pub fn read(&self, out: &mut [u8], timeout_ms: i32) -> i32 {
        let mut p = libc::pollfd {
            fd: self.fd,
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: `p` is one valid pollfd.
        if unsafe { libc::poll(&mut p, 1, timeout_ms) } <= 0 {
            return 0;
        }
        // SAFETY: `out` is writable for its length.
        let n = unsafe { libc::read(self.fd, out.as_mut_ptr().cast(), out.len()) };
        if n > 0 {
            n as i32
        } else if n == 0 {
            -1
        } else {
            0
        }
    }
}

impl FakeTransport {
    /// Opens the queue: before the reader's first read.
    pub fn open(rate_hz: u32, packets: u32, sender_cpu: Option<usize>) -> Result<Self, String> {
        let mut fds = [0; 2];
        // SAFETY: `fds` has room for the two descriptors.
        if unsafe {
            libc::pipe2(
                fds.as_mut_ptr(),
                libc::O_DIRECT | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        } != 0
        {
            return Err("pipe2".into());
        }
        // SAFETY: both are fresh descriptors this process now owns.
        let (rd, wr) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
        // One packet takes one 4 KiB slot.
        // SAFETY: plain fcntl on an owned descriptor.
        if unsafe { libc::fcntl(wr.as_raw_fd(), libc::F_SETPIPE_SZ, 64 * 4096) } < 0 {
            return Err("pipe size".into());
        }
        Ok(Self {
            period_ns: 1_000_000_000 / rate_hz as u64,
            packets,
            cpu: sender_cpu,
            rd,
            wr: Arc::new(wr),
            counters: Arc::default(),
            sender: None,
        })
    }

    pub fn reader(&self) -> FakeReader {
        FakeReader {
            fd: self.rd.as_raw_fd(),
        }
    }

    pub fn start(&mut self) {
        let (period_ns, packets, cpu) = (self.period_ns, self.packets, self.cpu);
        let wr = Arc::clone(&self.wr);
        let c = Arc::clone(&self.counters);
        self.sender = Some(std::thread::spawn(move || {
            send_loop(period_ns, packets, cpu, &wr, &c)
        }));
    }

    pub fn stop(&mut self) {
        self.counters.stop.store(true, Ordering::SeqCst);
        if let Some(sender) = self.sender.take() {
            let _ = sender.join();
        }
    }

    pub fn sent(&self) -> u64 {
        self.counters.sent.load(Ordering::SeqCst)
    }
    /// What the kernel's queue would have discarded.
    pub fn queue_overflows(&self) -> u64 {
        self.counters.overflows.load(Ordering::SeqCst)
    }
    /// The sender woke over one period late: our rig, not the driver.
    pub fn late_sends(&self) -> u64 {
        self.counters.late.load(Ordering::SeqCst)
    }
    pub fn finished(&self) -> bool {
        self.counters.finished.load(Ordering::SeqCst)
    }
}

impl Drop for FakeTransport {
    fn drop(&mut self) {
        self.stop();
    }
}

fn send_loop(period_ns: u64, packets: u32, cpu: Option<usize>, wr: &OwnedFd, c: &Counters) {
    let policy = ThreadPolicy {
        realtime: false,
        cpu,
        ..ThreadPolicy::default()
    };
    apply_thread_policy(&policy, &rt::RealSysOps);
    let mut target = monotonic_raw_ns() + period_ns;
    // Sleep most of the period, spin the rest: an exact cadence, whatever the scheduler does.
    const SPIN_NS: u64 = 50_000;
    let mut seq = 0u32;
    while seq < packets && !c.stop.load(Ordering::Relaxed) {
        let now = monotonic_raw_ns();
        if now + SPIN_NS < target {
            let nap = target - now - SPIN_NS;
            let ts = libc::timespec {
                tv_sec: 0,
                tv_nsec: nap as libc::c_long,
            };
            // SAFETY: `ts` is a valid timespec; the remainder is not wanted.
            unsafe { libc::clock_nanosleep(libc::CLOCK_MONOTONIC, 0, &ts, std::ptr::null_mut()) };
        }
        while monotonic_raw_ns() < target {
            std::hint::spin_loop();
        }
        let wobble = (seq % 1000) as f32 * 1e-3;
        let t = monotonic_raw_ns();
        if t > target + period_ns {
            c.late.fetch_add(1, Ordering::Relaxed);
        }
        let bytes = encode(&FakePacket {
            seq,
            t_sent_ns: t,
            gyro: [wobble, -wobble, 0.1],
            accel: [0.1, 0.2, 9.8],
        });
        c.sent.fetch_add(1, Ordering::Relaxed);
        // SAFETY: `bytes` is readable for its length.
        if unsafe { libc::write(wr.as_raw_fd(), bytes.as_ptr().cast(), PACKET_BYTES) }
            != PACKET_BYTES as isize
        {
            c.overflows.fetch_add(1, Ordering::Relaxed);
        }
        target += period_ns;
        seq += 1;
    }
    c.finished.store(true, Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packets_round_trip_and_a_flipped_bit_fails_the_crc() {
        let p = FakePacket {
            seq: 7,
            t_sent_ns: 123,
            gyro: [1.0, 2.0, 3.0],
            accel: [4.0, 5.0, 6.0],
        };
        let mut b = encode(&p);
        let q = parse_fake(&b).unwrap();
        assert_eq!(
            (q.seq, q.t_sent_ns, q.gyro, q.accel),
            (7, 123, p.gyro, p.accel)
        );
        b[20] ^= 1;
        assert!(parse_fake(&b).is_none());
        assert!(parse_fake(&b[..63]).is_none());
        // The standard check value of CRC-16/CCITT-FALSE.
        assert_eq!(crc16(b"123456789"), 0x29B1);
    }
}
