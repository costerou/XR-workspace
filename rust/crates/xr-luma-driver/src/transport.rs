//! The byte pipe to the glasses over raw usbdevfs, with no knowledge of what the bytes mean.
//!
//! A port of the usbfs path of `spike/driver/src/transport.cpp` (`luma::Transport`), call for call:
//! `rust/corpus/luma/transport_trace.txt` holds the opens, ioctls and closes of one scripted session, and
//! both drivers' tests must produce it exactly. The C++ header explains the choices; in short:
//!
//! - Every IN endpoint keeps `urbs_per_endpoint` reads queued (8 by default), each one packet long,
//!   resubmitted as it is reaped. Every reap drains all completed URBs before returning.
//! - Received packets go into a ring allocated once, at construction. The oldest is dropped when it is full.
//! - Any failed transfer ends the link: discard, release, close. Nothing here resets the device, clears
//!   a halt, sets a configuration or alternate setting, or detaches a kernel driver (`usbdevfs::Request`
//!   cannot even express those). A busy interface is waited for, never taken.
//! - Reconnecting happens inside `read()`: sysfs is rescanned every `rescan_interval`, with a hold after
//!   a drop that doubles while links keep dying, and a doubling retry for a device that will not open.
//! - The firmware channel of the Ultra (0x04/0x85) is refused unless asked for, and then only the two
//!   frames of `allowed_mcu_frame` may be written to it.
//!
//! Not ported: the hidraw backend, which only the 35ca:1102 microphone device uses, and only on request.
//!
//! Threading: one thread drives everything. Only `Interrupter::interrupt` may run on another thread or
//! in a signal handler (it is one `write(2)`).

use std::collections::VecDeque;
use std::fs;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::usbdevfs::{self, Errno, LibcSys, Request, Sys, Urb};

pub const VITURE_VENDOR_ID: u16 = 0x35ca;
pub const LUMA_ULTRA_PRODUCT_ID: u16 = 0x1104;
/// The Ultra's command channel: 512-byte frames.
pub const LUMA_ULTRA_DATA_OUT: u8 = 0x06;
pub const LUMA_ULTRA_DATA_IN: u8 = 0x87;
/// The Ultra's MCU channel, which also carries firmware updates.
pub const LUMA_ULTRA_FLASH_OUT: u8 = 0x04;
pub const LUMA_ULTRA_FLASH_IN: u8 = 0x85;

/// The flags every device node is opened with.
pub const OPEN_FLAGS: i32 = libc::O_RDWR | libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOCTTY;

/// What to open and how. The defaults are the C++ `TransportConfig`'s.
#[derive(Clone, Debug)]
pub struct TransportConfig {
    pub vendor_id: u16,
    pub product_id: u16,
    pub interface_number: u8,
    /// Addresses as the descriptor gives them (IN has 0x80 set). Every one is read at once.
    pub in_endpoints: Vec<u8>,
    /// 0 means no writes.
    pub out_endpoint: u8,
    /// More OUT endpoints, reached with `write_to`.
    pub extra_out_endpoints: Vec<u8>,
    /// Opens 0x04/0x85 on the Ultra; writes to 0x04 must then be an `allowed_mcu_frame`.
    pub allow_mcu_channel: bool,
    /// IN endpoints read and thrown away, `drain_urbs` URBs of `drain_urb_bytes` each.
    pub drain_endpoints: Vec<u8>,
    pub drain_urb_bytes: usize,
    pub drain_urbs: usize,
    /// Room for one packet. An endpoint larger than this is refused.
    pub packet_size: usize,
    /// Reads kept queued per IN endpoint, at least 2.
    pub urbs_per_endpoint: usize,
    /// Received packets held until `read()` takes them.
    pub queue_frames: usize,
    /// After a drop, how long to leave a returning device alone. Doubles per failed session, up to 16 times.
    pub arrival_settle: Duration,
    /// Payloads must span this long before failed-session history is forgotten (at least 1 ms).
    pub healthy_after: Duration,
    /// Longest wait between attempts on a device that is there but will not open.
    pub open_retry_max: Duration,
    /// Sysfs rescan period while disconnected (at least 1 ms).
    pub rescan_interval: Duration,
    pub sysfs_root: PathBuf,
    pub dev_root: PathBuf,
}

impl Default for TransportConfig {
    fn default() -> Self {
        TransportConfig {
            vendor_id: VITURE_VENDOR_ID,
            product_id: LUMA_ULTRA_PRODUCT_ID,
            interface_number: 0,
            in_endpoints: vec![LUMA_ULTRA_DATA_IN],
            out_endpoint: LUMA_ULTRA_DATA_OUT,
            extra_out_endpoints: Vec::new(),
            allow_mcu_channel: false,
            drain_endpoints: Vec::new(),
            drain_urb_bytes: 16384,
            drain_urbs: 6,
            packet_size: 1024,
            urbs_per_endpoint: 8,
            queue_frames: 256,
            arrival_settle: Duration::from_millis(2000),
            healthy_after: Duration::from_millis(2000),
            open_retry_max: Duration::from_millis(1000),
            rescan_interval: Duration::from_millis(25),
            sysfs_root: PathBuf::from("/sys"),
            dev_root: PathBuf::from("/dev"),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EndpointInfo {
    pub address: u8,
    /// Otherwise interrupt; control and isochronous endpoints are left out.
    pub bulk: bool,
    /// Bytes, from wMaxPacketSize. 0 if sysfs did not say.
    pub max_packet: usize,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FoundDevice {
    /// /dev/bus/usb/BBB/DDD
    pub node: PathBuf,
    /// 5-2.1
    pub sys_name: String,
    pub endpoints: Vec<EndpointInfo>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IoStatus {
    /// read: a packet is in the buffer. write: the device acknowledged it.
    Ok,
    /// Nothing within the timeout. The link is as it was.
    Timeout,
    /// `interrupt()` was called, or a signal arrived.
    Interrupted,
    /// read only: the link just came up. Reset any parser.
    Connected,
    /// read: the link just went down, reported once. write: there is no link now.
    Disconnected,
    /// `close()` was called, or `open()` never was.
    Closed,
    /// write only: not sent, wrong for the configuration (see `last_error()`). The link stands.
    Error,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TransportRead {
    pub status: IoStatus,
    /// Copied into the caller's buffer.
    pub bytes: usize,
    /// The IN endpoint it came from.
    pub endpoint: u8,
    /// CLOCK_MONOTONIC when it was received.
    pub t_ns: u64,
    /// The packet was longer than the buffer.
    pub truncated: bool,
}

impl TransportRead {
    fn status(status: IoStatus) -> Self {
        TransportRead { status, bytes: 0, endpoint: 0, t_ns: 0, truncated: false }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EventKind {
    Connected,
    Disconnected,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransportEvent {
    pub kind: EventKind,
    pub node: String,
    /// Disconnected: what gave it away.
    pub reason: String,
    /// CLOCK_MONOTONIC.
    pub t_ns: u64,
    /// Connected after a drop: how long the link was down.
    pub downtime_ms: u64,
    /// Disconnected: a stream that stopped before the drop reads high.
    pub ms_since_last_packet: u64,
    /// Disconnected: newest failed transfer this session (-EPROTO and so on), 0 if none.
    pub last_urb_status: i32,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TransportStats {
    pub connects: u64,
    pub disconnects: u64,
    /// Handed to `read()`.
    pub packets: u64,
    /// Pushed out of a full queue.
    pub dropped_packets: u64,
    /// Failed transfers, reads and writes. Any one of them ends the link.
    pub urb_errors: u64,
    /// Found the device but could not open or claim it.
    pub open_failures: u64,
    pub last_downtime_ms: u64,
    /// Why the device will not open now (EACCES, EBUSY); 0 if it opened or is absent.
    pub last_open_errno: i32,
}

/// The only bytes that may reach the Ultra's firmware channel, compared whole: FA 55 95 00 00 (starts the
/// stream) and FA 55 97 00 00 (sent just before it), both captured from VITURE's own software.
pub fn allowed_mcu_frame(frame: &[u8]) -> bool {
    const FRAMES: [[u8; 5]; 2] = [[0xfa, 0x55, 0x95, 0x00, 0x00], [0xfa, 0x55, 0x97, 0x00, 0x00]];
    FRAMES.iter().any(|known| frame == known)
}

// ------------------------------------------------------------------ sysfs

fn read_line(path: &Path) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    text.lines().next().unwrap_or("").to_string()
}

/// `strtoul`: leading blanks, an optional 0x in base 16, then as many digits as there are.
fn parse(text: &str, radix: u32) -> Option<u64> {
    let mut text = text.trim_start();
    if radix == 16 {
        text = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")).unwrap_or(text);
    }
    let digits = text.find(|c: char| !c.is_digit(radix)).unwrap_or(text.len());
    u64::from_str_radix(&text[..digits], radix).ok()
}

fn read_number(path: &Path, radix: u32) -> Option<u64> {
    parse(&read_line(path), radix)
}

/// Devices can vanish mid-walk; whatever goes missing just does not match.
fn sorted_entries(dir: &Path) -> Vec<PathBuf> {
    let mut entries: Vec<PathBuf> = match fs::read_dir(dir) {
        Ok(read) => read.filter_map(|entry| entry.ok().map(|entry| entry.path())).collect(),
        Err(_) => Vec::new(),
    };
    entries.sort();
    entries
}

fn read_endpoints(interface_dir: &Path) -> Vec<EndpointInfo> {
    let mut endpoints = Vec::new();
    for entry in sorted_entries(interface_dir) {
        let name = entry.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let Some(address) = name.strip_prefix("ep_").and_then(|a| parse(a, 16)) else { continue };
        let kind = read_line(&entry.join("type"));
        if kind != "Bulk" && kind != "Interrupt" {
            continue;
        }
        let max_packet = read_number(&entry.join("wMaxPacketSize"), 16).unwrap_or(0) as usize;
        endpoints.push(EndpointInfo { address: address as u8, bulk: kind == "Bulk", max_packet });
    }
    endpoints
}

/// Sysfs lookup only: opens no device node. The first match in name order wins.
pub fn find_device(config: &TransportConfig) -> Option<FoundDevice> {
    for entry in sorted_entries(&config.sysfs_root.join("bus/usb/devices")) {
        let vendor = read_number(&entry.join("idVendor"), 16);
        let product = read_number(&entry.join("idProduct"), 16);
        if vendor != Some(u64::from(config.vendor_id)) || product != Some(u64::from(config.product_id)) {
            continue;
        }
        let (Some(bus), Some(device)) = (read_number(&entry.join("busnum"), 10), read_number(&entry.join("devnum"), 10)) else {
            continue;
        };
        for child in sorted_entries(&entry) {
            if read_number(&child.join("bInterfaceNumber"), 16) != Some(u64::from(config.interface_number)) {
                continue;
            }
            return Some(FoundDevice {
                node: config.dev_root.join(format!("bus/usb/{bus:03}/{device:03}")),
                sys_name: entry.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
                endpoints: read_endpoints(&child),
            });
        }
    }
    None
}

// ------------------------------------------------------------------ time and text

fn now_ns() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: ts is a valid timespec; CLOCK_MONOTONIC always exists on Linux.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// Everything that reaches poll() comes through here: whole milliseconds, rounded up, at most 1000 s.
fn poll_ms(wait: Duration) -> i32 {
    wait.as_nanos().div_ceil(1_000_000).min(1_000_000) as i32
}

/// A year is as good as for ever, and cannot overflow an Instant.
fn deadline_after(timeout: Duration) -> Instant {
    Instant::now() + timeout.min(Duration::from_secs(365 * 24 * 3600))
}

fn hex(value: u8) -> String {
    format!("0x{value:02x}")
}

fn transfer_failure(endpoint: u8, status: i32) -> String {
    let endpoint = hex(endpoint);
    match -status {
        libc::EPIPE => format!("endpoint {endpoint} halted: reopening does not clear the halt"),
        libc::ENODEV | libc::ESHUTDOWN => format!("transfer shut down on endpoint {endpoint}: device gone or reset"),
        libc::ENOENT | libc::ECONNRESET => format!("transfer on endpoint {endpoint} cancelled by the kernel"),
        error => format!("transfer failed on endpoint {endpoint}: {}", Errno(error).message()),
    }
}

fn millis(duration: Duration) -> u64 {
    duration.as_millis() as u64
}

// ------------------------------------------------------------------ interrupt

/// Wakes a blocked `read()` with Interrupted. Cloneable, and safe from any thread or a signal handler.
#[derive(Clone, Debug)]
pub struct Interrupter {
    wake: Option<Arc<OwnedFd>>,
}

impl Interrupter {
    pub fn interrupt(&self) {
        if let Some(wake) = &self.wake {
            let one: u64 = 1;
            // SAFETY: wake is an open eventfd and one is 8 readable bytes. write(2) is async-signal-safe.
            unsafe { libc::write(wake.as_raw_fd(), (&raw const one).cast(), 8) };
        }
    }
}

// ------------------------------------------------------------------ URBs

/// One URB and its buffer, in memory the kernel holds pointers to while it is submitted. They are raw
/// allocations, never borrowed by Rust while the kernel may write them, and freed only once the file
/// that could write them is closed.
struct KernelUrb {
    urb: NonNull<Urb>,
    buffer: NonNull<u8>,
    capacity: usize,
    /// One packet: the most a transfer on this endpoint moves.
    length: i32,
    endpoint: u8,
    /// With the kernel.
    busy: bool,
    /// Host to device.
    out: bool,
    /// Read and thrown away.
    drain: bool,
}

impl KernelUrb {
    fn new(endpoint: u8, bulk: bool, length: usize, capacity: usize, index: usize) -> KernelUrb {
        let buffer = NonNull::new(Box::into_raw(vec![0u8; capacity].into_boxed_slice()).cast::<u8>()).expect("box is non-null");
        let urb = Urb {
            kind: if bulk { usbdevfs::URB_TYPE_BULK } else { usbdevfs::URB_TYPE_INTERRUPT },
            endpoint,
            buffer: buffer.as_ptr().cast(),
            usercontext: index as *mut libc::c_void, // Which URB this is, when the kernel hands it back.
            ..Urb::default()
        };
        KernelUrb {
            urb: NonNull::from(Box::leak(Box::new(urb))),
            buffer,
            capacity,
            length: length as i32,
            endpoint,
            busy: false,
            out: false,
            drain: false,
        }
    }

    fn status(&self) -> i32 {
        // SAFETY: urb is live; no reference to it exists, and the kernel writes it only inside an ioctl.
        unsafe { (*self.urb.as_ptr()).status }
    }

    fn actual_length(&self) -> i32 {
        // SAFETY: as in status().
        unsafe { (*self.urb.as_ptr()).actual_length }
    }

    fn prepare(&mut self, length: i32) {
        // SAFETY: as in status(); the URB is not with the kernel now.
        unsafe {
            let urb = self.urb.as_ptr();
            (*urb).status = 0;
            (*urb).actual_length = 0;
            (*urb).buffer_length = length;
        }
    }

    fn write_buffer(&mut self, data: &[u8]) {
        debug_assert!(data.len() <= self.capacity);
        // SAFETY: buffer holds capacity bytes and is not with the kernel now.
        unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), self.buffer.as_ptr(), data.len()) };
    }

    /// Only once the file is closed: the kernel then writes nothing more.
    fn free(self) {
        // SAFETY: both came from Box::into_raw/leak in new() with these sizes, and are freed once.
        unsafe {
            drop(Box::from_raw(self.urb.as_ptr()));
            drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(self.buffer.as_ptr(), self.capacity)));
        }
    }
}

#[derive(Clone, Copy, Default)]
struct Slot {
    bytes: usize,
    endpoint: u8,
    t_ns: u64,
}

// ------------------------------------------------------------------ the transport

/// The usbfs link to one device. See the module comment.
pub struct Transport {
    config: TransportConfig,
    sys: Arc<dyn Sys>,
    fd: Option<RawFd>,
    wake: Option<Arc<OwnedFd>>,
    opened: bool,
    current: Option<FoundDevice>,
    /// Link change that read() still has to report.
    pending: Option<IoStatus>,
    /// Set during reaping: the link ended; finish the batch first.
    lost: Option<String>,
    next_scan: Instant,
    /// When the device came back, for arrival_settle.
    first_seen: Option<Instant>,
    /// Failures in a row on a device that is there.
    open_attempts: u32,
    /// Links that ended before sustained traffic, at most 4.
    failed_sessions: u32,
    session_healthy: bool,
    healthy_since: Option<Instant>,
    down_since: Option<Instant>,
    last_error: String,
    events: VecDeque<TransportEvent>,
    stats: TransportStats,

    urbs: Vec<KernelUrb>,
    last_urb_status: i32,
    last_packet: Instant,
    out_urb: Option<usize>,
    extra_out_urbs: Vec<usize>,

    /// Received packets, allocated once so the hot path does not touch the heap.
    ring: Vec<u8>,
    slots: Vec<Slot>,
    head: usize,
    count: usize,
}

// SAFETY: the raw pointers in `urbs` point at memory only this Transport owns and touches; the kernel side
// is a file descriptor. Moving the whole Transport to another thread moves that ownership with it.
unsafe impl Send for Transport {}

impl Transport {
    /// A transport on the real kernel.
    pub fn new(config: TransportConfig) -> Transport {
        Transport::with_sys(config, Arc::new(LibcSys))
    }

    /// A transport on any `Sys`, for tests.
    pub fn with_sys(mut config: TransportConfig, sys: Arc<dyn Sys>) -> Transport {
        config.queue_frames = config.queue_frames.max(1);
        config.packet_size = config.packet_size.max(1);
        config.urbs_per_endpoint = config.urbs_per_endpoint.max(2);
        config.rescan_interval = config.rescan_interval.max(Duration::from_millis(1));
        config.healthy_after = config.healthy_after.max(Duration::from_millis(1));
        // SAFETY: eventfd has no pointer arguments; a non-negative result is a new descriptor we own.
        let wake = unsafe { libc::eventfd(0, libc::EFD_NONBLOCK | libc::EFD_CLOEXEC) };
        // SAFETY: wake is a fresh descriptor owned by nothing else.
        let wake = (wake >= 0).then(|| Arc::new(unsafe { OwnedFd::from_raw_fd(wake) }));
        let now = Instant::now();
        Transport {
            ring: vec![0; config.queue_frames * config.packet_size],
            slots: vec![Slot::default(); config.queue_frames],
            config,
            sys,
            fd: None,
            wake,
            opened: false,
            current: None,
            pending: None,
            lost: None,
            next_scan: now,
            first_seen: None,
            open_attempts: 0,
            failed_sessions: 0,
            session_healthy: false,
            healthy_since: None,
            down_since: None,
            last_error: String::new(),
            events: VecDeque::new(),
            stats: TransportStats::default(),
            urbs: Vec::new(),
            last_urb_status: 0,
            last_packet: now,
            out_urb: None,
            extra_out_urbs: Vec::new(),
            head: 0,
            count: 0,
        }
    }

    /// Starts the link. True if the device is up right now. False is not fatal: `read()` keeps looking.
    /// Either way `read()` reports Connected once the link is up. Once started, this only says whether
    /// the link is up.
    pub fn open(&mut self) -> bool {
        if self.opened {
            return self.fd.is_some();
        }
        self.opened = true;
        self.next_scan = Instant::now() + self.config.rescan_interval;
        self.try_open()
    }

    /// Waits up to `timeout` for a packet or a link change. A zero timeout polls.
    pub fn read(&mut self, buffer: &mut [u8], timeout: Duration) -> TransportRead {
        let deadline = deadline_after(timeout);
        loop {
            if !self.opened {
                return TransportRead::status(IoStatus::Closed);
            }
            if self.count > 0 {
                // A backed-up device is when the ring holds packets, and its queue must not run dry
                // while the caller works through them, so handing one over refills it too.
                if self.fd.is_some() {
                    self.reap_all();
                    self.finish_reap();
                }
                return self.dequeue(buffer);
            }
            if let Some(status) = self.pending.take() {
                return TransportRead::status(status);
            }
            let Some(fd) = self.fd else {
                if let Some(result) = self.wait_while_down(deadline) {
                    return result;
                }
                continue;
            };
            let mut fds = [
                libc::pollfd { fd, events: libc::POLLOUT, revents: 0 },
                libc::pollfd { fd: self.wake_fd(), events: libc::POLLIN, revents: 0 },
            ];
            let ready = match self.sys.poll(&mut fds, poll_ms(deadline.saturating_duration_since(Instant::now()))) {
                Ok(ready) => ready,
                Err(Errno(libc::EINTR)) => return TransportRead::status(IoStatus::Interrupted),
                Err(error) => {
                    self.lose_link(format!("poll: {}", error.message()));
                    continue;
                }
            };
            if fds[1].revents & libc::POLLIN != 0 && self.drain_wake() {
                return TransportRead::status(IoStatus::Interrupted);
            }
            if ready == 0 {
                if Instant::now() >= deadline {
                    return TransportRead::status(IoStatus::Timeout);
                }
                continue; // Only a very long wait, split up by poll_ms().
            }
            self.reap_all();
            if fds[0].revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
                self.mark_lost("poll: device removed".to_string());
            }
            self.finish_reap();
        }
    }

    /// Sends one packet to `out_endpoint`, once: no retries, nothing queued. Packets arriving meanwhile
    /// are kept for `read()`. Waits up to `timeout`, then cancels and returns Timeout with the link up.
    /// A failed transfer ends the link and returns Disconnected. Ok means the device acknowledged it.
    pub fn write(&mut self, data: &[u8], timeout: Duration) -> IoStatus {
        if !self.opened {
            return IoStatus::Closed;
        }
        if self.fd.is_none() {
            return IoStatus::Disconnected;
        }
        self.write_usbfs(self.out_urb, data, timeout)
    }

    /// The same, to `out_endpoint` or one of `extra_out_endpoints`. Any other endpoint is Error.
    pub fn write_to(&mut self, endpoint: u8, data: &[u8], timeout: Duration) -> IoStatus {
        if !self.opened {
            return IoStatus::Closed;
        }
        if self.fd.is_none() {
            return IoStatus::Disconnected;
        }
        let configured = self.out_urb.into_iter().chain(self.extra_out_urbs.iter().copied()).find(|&i| self.urbs[i].endpoint == endpoint);
        match configured {
            Some(index) => self.write_usbfs(Some(index), data, timeout),
            None => {
                self.last_error = format!("endpoint {} is not configured for writes", hex(endpoint));
                IoStatus::Error
            }
        }
    }

    /// Cancels transfers, releases the interface and closes. No reset. `open()` may be called again.
    pub fn close(&mut self) {
        self.teardown();
        self.opened = false;
        self.current = None;
        self.pending = None;
        self.lost = None;
        self.down_since = None;
        self.first_seen = None;
        self.open_attempts = 0;
        self.failed_sessions = 0;
        self.head = 0;
        self.count = 0;
    }

    /// Wakes a blocked `read()` with Interrupted.
    pub fn interrupt(&self) {
        self.interrupter().interrupt();
    }

    /// A handle for waking `read()` from another thread or a signal handler.
    pub fn interrupter(&self) -> Interrupter {
        Interrupter { wake: self.wake.clone() }
    }

    pub fn connected(&self) -> bool {
        self.fd.is_some()
    }

    /// What is open now.
    pub fn device(&self) -> Option<&FoundDevice> {
        self.current.as_ref()
    }

    /// Oldest unread link change.
    pub fn next_event(&mut self) -> Option<TransportEvent> {
        self.events.pop_front()
    }

    pub fn stats(&self) -> TransportStats {
        self.stats
    }

    /// Why the link is not up, or why the last write failed.
    pub fn last_error(&self) -> &str {
        &self.last_error
    }

    // -------------------------------------------------------------- link up

    fn ioctl(&self, request: Request<'_>) -> Result<(), Errno> {
        let Some(fd) = self.fd else { return Err(Errno(libc::EBADF)) };
        // SAFETY: every URB passed in lives in self.urbs, which is freed only after fd is closed (teardown).
        unsafe { self.sys.ioctl(fd, request) }
    }

    fn wake_fd(&self) -> RawFd {
        self.wake.as_ref().map_or(-1, |wake| wake.as_raw_fd())
    }

    fn push_event(&mut self, event: TransportEvent) {
        if self.events.len() >= 64 {
            self.events.pop_front();
        }
        self.events.push_back(event);
    }

    fn fail_open(&mut self, why: String) -> bool {
        self.last_error = why;
        false
    }

    fn is_ultra(&self) -> bool {
        self.config.vendor_id == VITURE_VENDOR_ID && self.config.product_id == LUMA_ULTRA_PRODUCT_ID
    }

    fn check_endpoint(&self, found: &FoundDevice, address: u8, input: bool) -> Result<(), String> {
        if self.is_ultra() && !self.config.allow_mcu_channel && (address == LUMA_ULTRA_FLASH_OUT || address == LUMA_ULTRA_FLASH_IN) {
            return Err(format!("refusing endpoint {}: it also carries firmware updates", hex(address)));
        }
        if (address & 0x80 != 0) != input {
            let kind = if input { "IN" } else { "OUT" };
            return Err(format!("endpoint {} is not an {kind} endpoint", hex(address)));
        }
        let Some(endpoint) = found.endpoints.iter().find(|e| e.address == address) else {
            return Err(format!("device has no bulk or interrupt endpoint {}", hex(address)));
        };
        // A packet larger than its URB is babble, an error on the wire, not a shorter read.
        if endpoint.max_packet > self.config.packet_size {
            return Err(format!("packet_size is below the {} bytes of endpoint {}", endpoint.max_packet, hex(address)));
        }
        Ok(())
    }

    fn check_endpoints(&mut self, found: &FoundDevice) -> bool {
        if self.config.in_endpoints.is_empty() {
            return self.fail_open("usbfs needs at least one IN endpoint in the configuration".to_string());
        }
        let c = &self.config;
        let all = c.in_endpoints.iter().chain(&c.drain_endpoints).map(|&a| (a, true));
        let all = all.chain(c.extra_out_endpoints.iter().map(|&a| (a, false)));
        let all: Vec<(u8, bool)> = all.chain((c.out_endpoint != 0).then_some((c.out_endpoint, false))).collect();
        for (address, input) in all {
            if let Err(why) = self.check_endpoint(found, address, input) {
                return self.fail_open(why);
            }
        }
        true
    }

    /// A URB of one packet for `endpoint`, typed and sized as sysfs says. A drain URB is
    /// `drain_urb_bytes` long instead.
    fn add_urb(&mut self, endpoint: u8, found: &FoundDevice, drain: bool) -> usize {
        let mut bulk = false;
        let mut length = self.config.packet_size;
        for e in found.endpoints.iter().filter(|e| e.address == endpoint) {
            bulk = e.bulk;
            if e.max_packet > 0 {
                length = e.max_packet;
            }
        }
        let mut capacity = self.config.packet_size;
        if drain {
            capacity = self.config.drain_urb_bytes.max(1);
            length = capacity;
        }
        let index = self.urbs.len();
        let mut urb = KernelUrb::new(endpoint, bulk, length, capacity, index);
        urb.drain = drain;
        self.urbs.push(urb);
        index
    }

    fn submit(&mut self, index: usize, length: i32) -> Result<(), Errno> {
        self.urbs[index].prepare(length);
        self.ioctl(Request::SubmitUrb(self.urbs[index].urb))?;
        self.urbs[index].busy = true;
        Ok(())
    }

    fn open_failed(&mut self, error: Errno, why: String) -> bool {
        self.stats.open_failures += 1;
        self.stats.last_open_errno = error.0;
        self.fail_open(why)
    }

    fn start_usbfs(&mut self, found: &FoundDevice) -> bool {
        if let Err(error) = self.ioctl(Request::ClaimInterface(u32::from(self.config.interface_number))) {
            let why = if error.0 == libc::EBUSY {
                "interface busy: another program or kernel driver holds it".to_string()
            } else {
                format!("claim failed: {}", error.message())
            };
            return self.open_failed(error, why);
        }
        for endpoint in self.config.in_endpoints.clone() {
            for _ in 0..self.config.urbs_per_endpoint {
                let index = self.add_urb(endpoint, found, false);
                if let Err(error) = self.submit(index, self.urbs[index].length) {
                    return self.open_failed(error, format!("URB submit failed: {}", error.message()));
                }
            }
        }
        for endpoint in self.config.drain_endpoints.clone() {
            for _ in 0..self.config.drain_urbs {
                let index = self.add_urb(endpoint, found, true);
                if let Err(error) = self.submit(index, self.urbs[index].length) {
                    return self.open_failed(error, format!("URB submit failed: {}", error.message()));
                }
            }
        }
        if self.config.out_endpoint != 0 {
            let index = self.add_urb(self.config.out_endpoint, found, false);
            self.urbs[index].out = true;
            self.out_urb = Some(index);
        }
        self.extra_out_urbs.clear();
        for endpoint in self.config.extra_out_endpoints.clone() {
            let index = self.add_urb(endpoint, found, false);
            self.urbs[index].out = true;
            self.extra_out_urbs.push(index);
        }
        true
    }

    fn retry_later(&mut self) -> bool {
        self.open_attempts += 1;
        let delay = self.config.rescan_interval * (1u32 << self.open_attempts.min(10));
        self.next_scan = Instant::now() + delay.min(self.config.open_retry_max).max(self.config.rescan_interval);
        false
    }

    /// Reopening cannot clear a halt, so a link that keeps dying with the device still present must not
    /// cycle: each such session doubles the hold. The device leaving and returning clears it.
    fn settling(&mut self) -> bool {
        if self.down_since.is_none() {
            return false;
        }
        let mut hold = self.config.arrival_settle * (1u32 << self.failed_sessions.min(4));
        if self.failed_sessions > 0 {
            hold = hold.max(self.config.open_retry_max);
        }
        if hold.is_zero() {
            return false;
        }
        let now = Instant::now();
        let first_seen = *self.first_seen.get_or_insert(now);
        now < first_seen + hold
    }

    fn try_open(&mut self) -> bool {
        let Some(found) = find_device(&self.config) else {
            self.first_seen = None;
            self.open_attempts = 0;
            self.failed_sessions = 0;
            self.stats.last_open_errno = 0;
            return self.fail_open("device not found".to_string());
        };
        if self.settling() {
            return false; // last_error() keeps saying why the link went down.
        }
        if !self.check_endpoints(&found) {
            return self.retry_later();
        }
        match self.sys.open(&found.node, OPEN_FLAGS) {
            Ok(fd) => self.fd = Some(fd),
            Err(error) => {
                // EACCES is normal for a moment: udev is still applying the ACL. Lasting EACCES means a
                // device present since boot with no user ACL (see spike/driver/docs/transport.md).
                let mut why = format!("open {}: {}", found.node.display(), error.message());
                if error.0 == libc::EACCES || error.0 == libc::EPERM {
                    why += " (no user access to the glasses: replug them, or install the udev rule)";
                }
                self.open_failed(error, why);
                return self.retry_later();
            }
        }
        if !self.start_usbfs(&found) {
            self.teardown();
            return self.retry_later();
        }
        self.first_seen = None;
        self.open_attempts = 0;
        self.stats.last_open_errno = 0;
        self.session_healthy = false;
        self.healthy_since = None;
        let now = Instant::now();
        self.last_packet = now;
        self.last_urb_status = 0;
        let mut event = TransportEvent {
            kind: EventKind::Connected,
            node: found.node.display().to_string(),
            reason: String::new(),
            t_ns: now_ns(),
            downtime_ms: 0,
            ms_since_last_packet: 0,
            last_urb_status: 0,
        };
        if let Some(down_since) = self.down_since.take() {
            event.downtime_ms = millis(now - down_since);
            self.stats.last_downtime_ms = event.downtime_ms;
        }
        self.push_event(event);
        self.stats.connects += 1;
        self.current = Some(found);
        self.pending = Some(IoStatus::Connected);
        self.last_error.clear();
        true
    }

    // -------------------------------------------------------------- link down

    /// Works whether the device is still there or not: on a removed device these ioctls fail harmlessly.
    fn teardown(&mut self) {
        if !self.urbs.is_empty() {
            // Only exists once the interface is claimed.
            for urb in self.urbs.iter().filter(|u| u.busy) {
                let _ = self.ioctl(Request::DiscardUrb(urb.urb));
            }
            for _ in 0..self.urbs.len() {
                // Collects what the discards just finished.
                let mut done: *mut Urb = std::ptr::null_mut();
                if self.ioctl(Request::ReapUrbNdelay(&mut done)).is_err() {
                    break;
                }
            }
            let _ = self.ioctl(Request::ReleaseInterface(u32::from(self.config.interface_number)));
        }
        if let Some(fd) = self.fd.take() {
            self.sys.close(fd); // Kills every URB of this file: their memory is ours again.
        }
        for urb in self.urbs.drain(..) {
            urb.free();
        }
        self.out_urb = None;
        self.extra_out_urbs.clear();
    }

    fn lose_link(&mut self, reason: String) {
        let node = self.current.as_ref().map(|c| c.node.display().to_string()).unwrap_or_default();
        self.teardown();
        self.current = None;
        let now = Instant::now();
        let event = TransportEvent {
            kind: EventKind::Disconnected,
            node,
            reason: reason.clone(),
            t_ns: now_ns(),
            downtime_ms: 0,
            ms_since_last_packet: millis(now - self.last_packet),
            last_urb_status: self.last_urb_status,
        };
        self.push_event(event);
        self.stats.disconnects += 1;
        self.down_since = Some(now);
        self.first_seen = None;
        if !self.session_healthy {
            self.failed_sessions = (self.failed_sessions + 1).min(4);
        }
        self.next_scan = now + self.config.rescan_interval;
        self.pending = Some(IoStatus::Disconnected);
        self.last_error = reason;
    }

    // -------------------------------------------------------------- packets

    fn note_packet(&mut self) {
        let now = Instant::now();
        // Receipt time, not device timestamps: a pair of packets separated by silence is not health.
        let gap = self.config.healthy_after.min(Duration::from_millis(100));
        if self.healthy_since.is_none() || now - self.last_packet > gap {
            self.healthy_since = Some(now);
        }
        self.last_packet = now;
        if self.healthy_since.is_some_and(|since| now - since >= self.config.healthy_after) {
            self.session_healthy = true;
            self.failed_sessions = 0;
        }
    }

    fn enqueue(&mut self, index: usize) {
        let frames = self.slots.len();
        let at = (self.head + self.count) % frames;
        if self.count == frames {
            // Newest data matters most for head tracking.
            self.head = (self.head + 1) % frames;
            self.count -= 1;
            self.stats.dropped_packets += 1;
        }
        let urb = &self.urbs[index];
        let bytes = (urb.actual_length().max(0) as usize).min(self.config.packet_size).min(urb.capacity);
        let offset = at * self.config.packet_size;
        // SAFETY: the URB was just reaped, so the kernel is done with its buffer, which holds capacity bytes.
        let data = unsafe { std::slice::from_raw_parts(urb.buffer.as_ptr(), bytes) };
        self.ring[offset..offset + bytes].copy_from_slice(data);
        let endpoint = urb.endpoint;
        self.note_packet();
        self.slots[at] = Slot { bytes, endpoint, t_ns: now_ns() };
        self.count += 1;
    }

    fn dequeue(&mut self, buffer: &mut [u8]) -> TransportRead {
        let slot = self.slots[self.head];
        let bytes = slot.bytes.min(buffer.len());
        let offset = self.head * self.config.packet_size;
        buffer[..bytes].copy_from_slice(&self.ring[offset..offset + bytes]);
        self.head = (self.head + 1) % self.slots.len();
        self.count -= 1;
        self.stats.packets += 1;
        TransportRead { status: IoStatus::Ok, bytes, endpoint: slot.endpoint, t_ns: slot.t_ns, truncated: slot.bytes > buffer.len() }
    }

    fn mark_lost(&mut self, why: String) {
        if self.lost.is_none() {
            self.lost = Some(why); // The first cause is the one worth keeping.
        }
    }

    /// Any failed read ends the link: the host has used its own retries, the other queued reads tend to
    /// fail with it, and nothing here may clear a halt or resynchronise a data toggle.
    fn handle_in(&mut self, index: usize) {
        let status = self.urbs[index].status();
        if status != 0 {
            self.stats.urb_errors += 1;
            self.last_urb_status = status;
            self.mark_lost(transfer_failure(self.urbs[index].endpoint, status));
            return;
        }
        if self.urbs[index].actual_length() > 0 && !self.urbs[index].drain {
            self.enqueue(index);
        }
        if self.lost.is_none() {
            if let Err(error) = self.submit(index, self.urbs[index].length) {
                self.mark_lost(format!("URB resubmit failed: {}", error.message()));
            }
        }
    }

    fn reap_all(&mut self) {
        loop {
            let mut done: *mut Urb = std::ptr::null_mut();
            match self.ioctl(Request::ReapUrbNdelay(&mut done)) {
                Ok(()) => {}
                Err(Errno(libc::EINTR)) => continue,
                Err(Errno(libc::EAGAIN)) => break,
                Err(error) => {
                    self.mark_lost(format!("reap: {}", error.message()));
                    break;
                }
            }
            // SAFETY: the kernel hands back one of our URBs, which are live until teardown.
            let index = unsafe { (*done).usercontext } as usize;
            let Some(urb) = self.urbs.get_mut(index) else { continue };
            urb.busy = false;
            if !urb.out {
                self.handle_in(index);
            }
        }
    }

    /// Everything reaped so far stays readable: read() hands it out before it reports the disconnect.
    fn finish_reap(&mut self) {
        if let Some(reason) = self.lost.take() {
            self.lose_link(reason);
        }
    }

    // -------------------------------------------------------------- waiting

    fn drain_wake(&self) -> bool {
        let mut value: u64 = 0;
        // SAFETY: value is 8 writable bytes; reading a closed or -1 fd just fails.
        unsafe { libc::read(self.wake_fd(), (&raw mut value).cast(), 8) > 0 }
    }

    /// One step of waiting for the device. None means look again; try_open() leaves Connected pending.
    fn wait_while_down(&mut self, deadline: Instant) -> Option<TransportRead> {
        let mut now = Instant::now();
        if now >= self.next_scan {
            self.next_scan = now + self.config.rescan_interval;
            if self.try_open() {
                return None;
            }
            now = Instant::now();
        }
        if now >= deadline {
            return Some(TransportRead::status(IoStatus::Timeout));
        }
        let wait = deadline.min(self.next_scan).saturating_duration_since(now);
        let mut fds = [libc::pollfd { fd: self.wake_fd(), events: libc::POLLIN, revents: 0 }];
        match self.sys.poll(&mut fds, poll_ms(wait)) {
            Err(Errno(libc::EINTR)) => Some(TransportRead::status(IoStatus::Interrupted)),
            Ok(ready) if ready > 0 && self.drain_wake() => Some(TransportRead::status(IoStatus::Interrupted)),
            _ => None,
        }
    }

    // -------------------------------------------------------------- write

    /// One poll-and-reap. Packets that arrive go to the queue.
    fn pump(&mut self, timeout_ms: i32) {
        let Some(fd) = self.fd else { return };
        let mut fds = [libc::pollfd { fd, events: libc::POLLOUT, revents: 0 }];
        match self.sys.poll(&mut fds, timeout_ms) {
            Err(Errno(libc::EINTR)) => {}
            Err(error) => self.mark_lost(format!("poll: {}", error.message())),
            Ok(0) => {}
            Ok(_) => {
                self.reap_all();
                if fds[0].revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
                    self.mark_lost("poll: device removed".to_string());
                }
            }
        }
        self.finish_reap();
    }

    fn write_usbfs(&mut self, out: Option<usize>, data: &[u8], timeout: Duration) -> IoStatus {
        let Some(index) = out else {
            self.last_error = "no OUT endpoint configured".to_string();
            return IoStatus::Error;
        };
        let endpoint = self.urbs[index].endpoint;
        if self.is_ultra() && endpoint == LUMA_ULTRA_FLASH_OUT && !allowed_mcu_frame(data) {
            self.last_error = "refusing a frame on 0x04 that is not on the allowlist: it also carries firmware updates".to_string();
            return IoStatus::Error;
        }
        let length = self.urbs[index].length;
        if data.len() > length as usize {
            self.last_error = format!("write is larger than one packet ({length} bytes)");
            return IoStatus::Error;
        }
        self.urbs[index].write_buffer(data);
        if let Err(error) = self.submit(index, data.len() as i32) {
            self.lose_link(format!("URB submit: {}", error.message()));
            return IoStatus::Disconnected;
        }
        // The URBs stay in place until teardown, and teardown sets fd to None, so index is good while fd is.
        let deadline = deadline_after(timeout);
        loop {
            self.pump(poll_ms(deadline.saturating_duration_since(Instant::now())));
            if self.fd.is_none() {
                return IoStatus::Disconnected;
            }
            if !self.urbs[index].busy || Instant::now() >= deadline {
                break;
            }
        }
        if self.urbs[index].busy {
            // A cancelled transfer is no reason to touch the link: the caller logs it and does not retry.
            let _ = self.ioctl(Request::DiscardUrb(self.urbs[index].urb));
            let give_up = Instant::now() + Duration::from_millis(100);
            while self.urbs[index].busy && Instant::now() < give_up {
                self.pump(10);
                if self.fd.is_none() {
                    return IoStatus::Disconnected;
                }
            }
            if self.urbs[index].busy {
                self.lose_link("write would not cancel".to_string());
                return IoStatus::Disconnected;
            }
        }
        let status = self.urbs[index].status();
        if status == 0 {
            // It may have finished just as the timeout fired. Calling that a failure invites a duplicate.
            if self.urbs[index].actual_length() == data.len() as i32 {
                return IoStatus::Ok;
            }
            self.last_error = "short write".to_string();
            return IoStatus::Error;
        }
        if status == -libc::ENOENT || status == -libc::ECONNRESET {
            self.last_error = "write timed out".to_string();
            return IoStatus::Timeout;
        }
        self.stats.urb_errors += 1;
        self.last_urb_status = status;
        self.lose_link(transfer_failure(endpoint, status));
        IoStatus::Disconnected
    }
}

impl Drop for Transport {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_reads_like_strtoul() {
        assert_eq!(parse("35ca", 16), Some(0x35ca));
        assert_eq!(parse("0x0400", 16), Some(0x400));
        assert_eq!(parse(" 101", 10), Some(101));
        assert_eq!(parse("12ab", 10), Some(12));
        assert_eq!(parse("", 16), None);
        assert_eq!(parse("zz", 16), None);
    }

    #[test]
    fn poll_ms_rounds_up_and_caps() {
        assert_eq!(poll_ms(Duration::ZERO), 0);
        assert_eq!(poll_ms(Duration::from_nanos(1)), 1);
        assert_eq!(poll_ms(Duration::from_micros(1500)), 2);
        assert_eq!(poll_ms(Duration::from_secs(1 << 40)), 1_000_000);
    }

    #[test]
    fn huge_timeouts_do_not_overflow() {
        let deadline = deadline_after(Duration::MAX);
        assert!(deadline > Instant::now() + Duration::from_secs(300 * 24 * 3600));
    }

    #[test]
    fn only_the_two_captured_frames_reach_the_mcu_channel() {
        assert!(allowed_mcu_frame(&[0xfa, 0x55, 0x95, 0x00, 0x00]));
        assert!(allowed_mcu_frame(&[0xfa, 0x55, 0x97, 0x00, 0x00]));
        assert!(!allowed_mcu_frame(&[0xfa, 0x55, 0x95, 0x00]));
        assert!(!allowed_mcu_frame(&[0xfa, 0x55, 0x95, 0x00, 0x00, 0x00]));
        assert!(!allowed_mcu_frame(&[0xfa, 0x55, 0x96, 0x00, 0x00]));
        assert!(!allowed_mcu_frame(&[]));
    }

    #[test]
    fn transfer_failures_say_what_happened() {
        assert_eq!(transfer_failure(0x81, -libc::EPIPE), "endpoint 0x81 halted: reopening does not clear the halt");
        assert_eq!(transfer_failure(0x82, -libc::ESHUTDOWN), "transfer shut down on endpoint 0x82: device gone or reset");
        assert_eq!(transfer_failure(0x82, -libc::ECONNRESET), "transfer on endpoint 0x82 cancelled by the kernel");
        assert_eq!(transfer_failure(0x81, -libc::EPROTO), "transfer failed on endpoint 0x81: Protocol error");
    }
}
