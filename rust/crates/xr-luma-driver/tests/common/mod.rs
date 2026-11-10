//! A fake sysfs tree and a fake usbfs kernel, ported from `spike/driver/tests/transport_test.cpp` so the
//! Rust transport is tested against the same model of the kernel as the C++ one.
//!
//! What `FakeUsbfs` copies from the kernel, and where (drivers/usb/core/devio.c, driver.c, message.c and
//! xhci-ring.c):
//!  - A transfer ends on a short packet or a full buffer; a packet larger than the URB is babble.
//!  - Status, length and data reach user memory only at reap (processcompl).
//!  - A disconnect has stages: connected() goes false first, so POLLHUP shows and reap gives ENODEV
//!    with URBs still pending, and DISCARD gives ENODEV. URBs then flush, often with -EPROTO first, and
//!    sysfs and the node go last (usb_disconnect). A hang-up wakes poll with nothing in flight.
//!  - A failed bulk URB leaves the others queued (cancel_bulk_urbs does nothing without CONTINUATION).
//!  - One fault fails every queued URB: xHCI's soft retries are shared, so after the first they run out.
//!  - A stall outlives a reopen: nothing on the close or claim path sends CLEAR_FEATURE(HALT).
//!  - Another program's reset flushes URBs with -ESHUTDOWN and drops our claim, with no hang-up and the
//!    same device number (usb_reset_device forces an unbind; usbfs has no pre_reset).
//!  - DISCARD on a URB that already completed gives EINVAL.

#![allow(dead_code)] // Each test binary uses its own part of this.

use std::collections::{BTreeSet, VecDeque};
use std::ffi::c_int;
use std::fs;
use std::os::fd::RawFd;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use xr_luma_driver::transport::{IoStatus, Transport, TransportConfig, TransportRead};
use xr_luma_driver::usbdevfs::{self, Errno, Request, Sys, Urb};

pub const ENDPOINTS: [(&str, &str); 6] =
    [("81", "Interrupt"), ("82", "Bulk"), ("87", "Bulk"), ("04", "Bulk"), ("06", "Bulk"), ("85", "Bulk")];

pub fn ms(n: u64) -> Duration {
    Duration::from_millis(n)
}

pub fn ms_since(start: Instant) -> u128 {
    start.elapsed().as_millis()
}

pub fn open_fd_count() -> usize {
    fs::read_dir("/proc/self/fd").map(|d| d.count()).unwrap_or(0)
}

/// Timing tests share one machine: run them one at a time.
pub fn serial() -> MutexGuard<'static, ()> {
    static SERIAL: Mutex<()> = Mutex::new(());
    SERIAL.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Symbolic errno names, as strace prints them. Only the ones a session can produce.
pub fn errno_name(error: i32) -> String {
    let name = match error {
        libc::EPERM => "EPERM",
        libc::ENOENT => "ENOENT",
        libc::EINTR => "EINTR",
        libc::EAGAIN => "EAGAIN",
        libc::EACCES => "EACCES",
        libc::EBUSY => "EBUSY",
        libc::ENODEV => "ENODEV",
        libc::EINVAL => "EINVAL",
        libc::EPIPE => "EPIPE",
        libc::ENOTTY => "ENOTTY",
        libc::EOVERFLOW => "EOVERFLOW",
        libc::ECONNRESET => "ECONNRESET",
        libc::ESHUTDOWN => "ESHUTDOWN",
        libc::ETIMEDOUT => "ETIMEDOUT",
        libc::EPROTO => "EPROTO",
        _ => return format!("E{error}"),
    };
    name.to_string()
}

pub fn write_file(path: &Path, text: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, format!("{text}\n")).unwrap();
}

/// Builds the same shape as the real /sys and /dev. Paths inside are the ones seen on the machine.
pub struct FakeTree {
    root: PathBuf,
}

impl FakeTree {
    pub fn new() -> FakeTree {
        static NEXT: AtomicU32 = AtomicU32::new(0);
        let name = format!("luma-transport-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed));
        let root = std::env::temp_dir().join(name);
        let _ = fs::remove_dir_all(&root);
        let tree = FakeTree { root };
        fs::create_dir_all(tree.sysfs().join("bus/usb/devices")).unwrap();
        fs::create_dir_all(tree.dev()).unwrap();
        tree
    }

    pub fn sysfs(&self) -> PathBuf {
        self.root.join("sys")
    }

    pub fn dev(&self) -> PathBuf {
        self.root.join("dev")
    }

    fn usb_dir(&self, name: &str) -> PathBuf {
        self.sysfs().join("devices/usb5/5-2").join(name)
    }

    pub fn add_usb(&self, name: &str, vid: &str, pid: &str, devnum: u32, endpoints: &[(&str, &str)], interface_number: u32) {
        let d = self.usb_dir(name);
        write_file(&d.join("idVendor"), vid);
        write_file(&d.join("idProduct"), pid);
        write_file(&d.join("busnum"), "5");
        write_file(&d.join("devnum"), &devnum.to_string());
        let intf = d.join(format!("{name}:1.{interface_number}"));
        write_file(&intf.join("bInterfaceNumber"), &format!("{interface_number:02x}"));
        for (address, kind) in endpoints {
            let ep = intf.join(format!("ep_{address}"));
            write_file(&ep.join("type"), kind);
            write_file(&ep.join("wMaxPacketSize"), if *address == "81" { "0400" } else { "0200" }); // As on the glasses.
        }
        let devices = self.sysfs().join("bus/usb/devices");
        std::os::unix::fs::symlink(&d, devices.join(name)).unwrap();
        std::os::unix::fs::symlink(&intf, devices.join(intf.file_name().unwrap())).unwrap();
        write_file(&self.dev().join(format!("bus/usb/005/{devnum:03}")), "");
    }

    pub fn remove_usb(&self, name: &str, devnum: u32) {
        if let Ok(entries) = fs::read_dir(self.sysfs().join("bus/usb/devices")) {
            for entry in entries.flatten() {
                if entry.file_name().to_string_lossy().starts_with(name) {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
        let _ = fs::remove_dir_all(self.usb_dir(name));
        let _ = fs::remove_file(self.dev().join(format!("bus/usb/005/{devnum:03}")));
    }
}

impl Drop for FakeTree {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

pub fn usb_config(tree: &FakeTree) -> TransportConfig {
    TransportConfig {
        in_endpoints: vec![0x81, 0x82],
        out_endpoint: xr_luma_driver::transport::LUMA_ULTRA_DATA_OUT,
        urbs_per_endpoint: 2,
        rescan_interval: ms(10),
        arrival_settle: ms(0),
        open_retry_max: ms(20),
        sysfs_root: tree.sysfs(),
        dev_root: tree.dev(),
        ..TransportConfig::default()
    }
}

/// The stream start lives on the firmware channel, so it opens on request.
pub fn live_config(tree: &FakeTree) -> TransportConfig {
    TransportConfig { in_endpoints: vec![0x81, 0x85], out_endpoint: 0x04, allow_mcu_channel: true, ..usb_config(tree) }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
struct UrbPtr(*mut Urb);
// SAFETY: the pointers name URBs of the transport under test; the fake reads and writes them only under
// its mutex, while the transport has handed them to the "kernel".
unsafe impl Send for UrbPtr {}

impl UrbPtr {
    fn get(self) -> &'static mut Urb {
        // SAFETY: as above. The URB stays allocated until the transport closes the file, which clears
        // every pointer the fake holds.
        unsafe { &mut *self.0 }
    }
}

struct Done {
    urb: UrbPtr,
    status: i32,
    actual: i32,
    data: Vec<u8>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SeenUrb {
    pub endpoint: u8,
    pub kind: u8,
    pub length: i32,
    pub flags: u32,
}

struct State {
    event: RawFd,
    devnum: u32,
    gone: bool,
    busy: bool,
    hold_out: bool,
    finish_on_discard: bool,
    recording: bool,
    claimed_now: bool,
    out_status: i32,
    open_failures: u32,
    open_errno: i32,
    open_flags: c_int,
    opens: u32,
    claims: u32,
    releases: u32,
    auto_claims: u32,
    claimed: i64,
    missed: u32,
    inflight: VecDeque<UrbPtr>,
    done: VecDeque<Done>,
    stalled: BTreeSet<u8>,
    sent: Vec<Vec<u8>>,
    submitted: Vec<u8>,
    seen: Vec<SeenUrb>,
    claimed_list: Vec<u32>,
    requests: Vec<u64>,
    trace: Vec<String>,
    tracing: bool,
}

/// A stand-in for usbfs. See the module comment.
pub struct FakeUsbfs {
    tree: FakeTree,
    state: Mutex<State>,
}

impl FakeUsbfs {
    pub fn new() -> Arc<FakeUsbfs> {
        Arc::new(FakeUsbfs {
            tree: FakeTree::new(),
            state: Mutex::new(State {
                event: -1,
                devnum: 0,
                gone: true,
                busy: false,
                hold_out: false,
                finish_on_discard: false,
                recording: true,
                claimed_now: false,
                out_status: 0,
                open_failures: 0,
                open_errno: 0,
                open_flags: 0,
                opens: 0,
                claims: 0,
                releases: 0,
                auto_claims: 0,
                claimed: -1,
                missed: 0,
                inflight: VecDeque::with_capacity(64),
                done: VecDeque::with_capacity(64),
                stalled: BTreeSet::new(),
                sent: Vec::new(),
                submitted: Vec::new(),
                seen: Vec::new(),
                claimed_list: Vec::new(),
                requests: Vec::new(),
                trace: Vec::new(),
                tracing: false,
            }),
        })
    }

    pub fn tree(&self) -> &FakeTree {
        &self.tree
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    // Test controls -----------------------------------------------------

    pub fn arrive(&self, devnum: u32) {
        let mut s = self.lock();
        s.devnum = devnum;
        s.gone = false;
        s.stalled.clear(); // Re-enumeration is what clears a halt.
        self.tree.add_usb("5-2.1", "35ca", "1104", devnum, &ENDPOINTS, 0);
    }

    pub fn begin_disconnect(&self) {
        let mut s = self.lock();
        s.gone = true;
        signal(&s);
    }

    pub fn flush(&self, status: i32) {
        let mut s = self.lock();
        let inflight: Vec<UrbPtr> = s.inflight.drain(..).collect();
        for urb in inflight {
            finish(&mut s, urb, status, 0, Vec::new());
        }
    }

    pub fn remove_nodes(&self) {
        let s = self.lock();
        self.tree.remove_usb("5-2.1", s.devnum);
    }

    pub fn yank(&self) {
        self.yank_with(-libc::ESHUTDOWN);
    }

    pub fn yank_with(&self, status: i32) {
        self.begin_disconnect();
        self.flush(status);
        self.remove_nodes();
    }

    pub fn deliver(&self, endpoint: u8, bytes: &[u8]) -> bool {
        self.deliver_status(endpoint, bytes, 0)
    }

    pub fn deliver_status(&self, endpoint: u8, bytes: &[u8], mut status: i32) -> bool {
        let mut s = self.lock();
        let Some(at) = s.inflight.iter().position(|u| u.get().endpoint == endpoint) else {
            s.missed += 1;
            return false;
        };
        let urb = s.inflight[at];
        let n = bytes.len() as i32;
        let max_packet = if endpoint == 0x81 { 1024 } else { 512 };
        if status == 0 && n > urb.get().buffer_length {
            status = -libc::EOVERFLOW;
        } else if status == 0 && n > 0 && n % max_packet == 0 && n < urb.get().buffer_length {
            return true; // A full-size packet into a bigger buffer: the URB waits for more.
        }
        s.inflight.remove(at);
        let (actual, data) = if status == 0 { (n, bytes.to_vec()) } else { (0, Vec::new()) };
        finish(&mut s, urb, status, actual, data);
        true
    }

    /// Everything queued on one endpoint fails in one go.
    pub fn fail_all(&self, endpoint: u8, status: i32) {
        let mut s = self.lock();
        fail_queued(&mut s, endpoint, status);
    }

    /// Halted on the device: every transfer fails until a re-enumeration.
    pub fn stall(&self, endpoint: u8) {
        let mut s = self.lock();
        s.stalled.insert(endpoint);
        fail_queued(&mut s, endpoint, -libc::EPIPE);
    }

    pub fn foreign_reset(&self) {
        let mut s = self.lock();
        let inflight: Vec<UrbPtr> = s.inflight.drain(..).collect();
        for urb in inflight {
            finish(&mut s, urb, -libc::ESHUTDOWN, 0, Vec::new());
        }
        s.claimed_now = false;
    }

    pub fn set_busy(&self, busy: bool) {
        self.lock().busy = busy;
    }
    pub fn fail_opens(&self, count: u32, error: i32) {
        let mut s = self.lock();
        s.open_failures = count;
        s.open_errno = error;
    }
    pub fn hold_out_urbs(&self, hold: bool) {
        self.lock().hold_out = hold;
    }
    pub fn out_status(&self, status: i32) {
        self.lock().out_status = status;
    }
    pub fn finish_held_urb_on_discard(&self) {
        self.lock().finish_on_discard = true;
    }
    pub fn stop_recording(&self) {
        self.lock().recording = false;
    }

    // Inspection --------------------------------------------------------

    pub fn seen(&self) -> Vec<SeenUrb> {
        self.lock().seen.clone()
    }
    pub fn claimed_list(&self) -> Vec<u32> {
        self.lock().claimed_list.clone()
    }
    pub fn opens(&self) -> u32 {
        self.lock().opens
    }
    pub fn sent(&self) -> Vec<Vec<u8>> {
        self.lock().sent.clone()
    }
    pub fn submitted_endpoints(&self) -> Vec<u8> {
        self.lock().submitted.clone()
    }
    pub fn requests(&self) -> Vec<u64> {
        self.lock().requests.clone()
    }
    pub fn request_count(&self, code: u64) -> usize {
        self.lock().requests.iter().filter(|&&r| r == code).count()
    }
    pub fn inflight(&self) -> usize {
        self.lock().inflight.len()
    }
    pub fn claims(&self) -> u32 {
        self.lock().claims
    }
    pub fn releases(&self) -> u32 {
        self.lock().releases
    }
    pub fn auto_claims(&self) -> u32 {
        self.lock().auto_claims
    }
    pub fn claimed_interface(&self) -> i64 {
        self.lock().claimed
    }
    pub fn open_flags(&self) -> c_int {
        self.lock().open_flags
    }
    pub fn missed(&self) -> u32 {
        self.lock().missed
    }
    pub fn descriptor_open(&self) -> bool {
        self.lock().event >= 0
    }
    /// Every open, close of the device and ioctl, in order, in the form rust/corpus/luma/transport_trace.txt
    /// keeps. Recording allocates, so it is off unless a test asks for it.
    pub fn trace(&self) -> Vec<String> {
        self.lock().trace.clone()
    }
    pub fn start_trace(&self) {
        self.lock().tracing = true;
    }

    fn traced_fail(&self, s: &mut State, call: String, error: i32) -> Result<RawFd, Errno> {
        if s.tracing {
            s.trace.push(format!("{call} = -1 {}", errno_name(error)));
        }
        Err(Errno(error))
    }
}

fn signal(s: &State) {
    if s.event >= 0 {
        let one: u64 = 1;
        // SAFETY: event is an open eventfd; one is 8 bytes.
        assert_eq!(unsafe { libc::write(s.event, (&raw const one).cast(), 8) }, 8);
    }
}

fn drain(s: &State) {
    if s.event >= 0 {
        let mut value: u64 = 0;
        // SAFETY: as in signal().
        unsafe { libc::read(s.event, (&raw mut value).cast(), 8) };
    }
}

fn finish(s: &mut State, urb: UrbPtr, status: i32, actual: i32, data: Vec<u8>) {
    s.done.push_back(Done { urb, status, actual, data });
    signal(s);
}

fn fail_queued(s: &mut State, endpoint: u8, status: i32) {
    let mut kept = VecDeque::with_capacity(64);
    let inflight: Vec<UrbPtr> = s.inflight.drain(..).collect();
    for urb in inflight {
        if urb.get().endpoint == endpoint {
            finish(s, urb, status, 0, Vec::new());
        } else {
            kept.push_back(urb);
        }
    }
    s.inflight = kept;
}

fn open_flag_names(mut flags: c_int) -> String {
    let mut out = String::new();
    for (bit, name) in [(libc::O_NONBLOCK, "O_NONBLOCK"), (libc::O_CLOEXEC, "O_CLOEXEC"), (libc::O_NOCTTY, "O_NOCTTY")] {
        if flags & bit == 0 {
            continue;
        }
        out += "|";
        out += name;
        flags &= !bit;
    }
    let access = flags & libc::O_ACCMODE;
    flags &= !libc::O_ACCMODE;
    let mut head = match access {
        libc::O_RDWR => "O_RDWR".to_string(),
        libc::O_WRONLY => "O_WRONLY".to_string(),
        _ => "O_RDONLY".to_string(),
    };
    if flags != 0 {
        head += &format!("|{flags}");
    }
    head + &out
}

fn urb_type_name(kind: u8) -> &'static str {
    match kind {
        usbdevfs::URB_TYPE_ISO => "iso",
        usbdevfs::URB_TYPE_INTERRUPT => "interrupt",
        usbdevfs::URB_TYPE_CONTROL => "control",
        usbdevfs::URB_TYPE_BULK => "bulk",
        _ => "?",
    }
}

fn describe_call(request: &Request<'_>) -> String {
    match request {
        Request::ClaimInterface(n) => format!("ioctl CLAIMINTERFACE {n}"),
        Request::ReleaseInterface(n) => format!("ioctl RELEASEINTERFACE {n}"),
        Request::SubmitUrb(urb) => {
            let u = UrbPtr(urb.as_ptr()).get();
            format!(
                "ioctl SUBMITURB {{type={}, endpoint=0x{:02x}, flags=0x{:x}, buffer_length={}}}",
                urb_type_name(u.kind),
                u.endpoint,
                u.flags,
                u.buffer_length
            )
        }
        Request::DiscardUrb(urb) => {
            let u = UrbPtr(urb.as_ptr()).get();
            format!("ioctl DISCARDURB {{endpoint=0x{:02x}, buffer_length={}}}", u.endpoint, u.buffer_length)
        }
        Request::ReapUrbNdelay(_) => "ioctl REAPURBNDELAY".to_string(),
    }
}

fn describe_reaped(urb: &Urb) -> String {
    let status = if urb.status == 0 { "0".to_string() } else { format!("-{}", errno_name(-urb.status)) };
    format!("{{endpoint=0x{:02x}, status={status}, actual_length={}}}", urb.endpoint, urb.actual_length)
}

fn fail(error: i32) -> Result<(), Errno> {
    Err(Errno(error))
}

fn ioctl_locked(s: &mut State, request: &mut Request<'_>) -> Result<(), Errno> {
    if s.recording {
        s.requests.push(request.code());
    }
    match request {
        Request::ClaimInterface(n) => {
            if s.gone {
                return fail(libc::ENODEV);
            }
            if s.busy {
                return fail(libc::EBUSY);
            }
            s.claims += 1;
            s.claimed = i64::from(*n);
            s.claimed_list.push(*n);
            s.claimed_now = true;
            Ok(())
        }
        Request::ReleaseInterface(_) => {
            s.releases += 1;
            if s.gone {
                return fail(libc::ENODEV);
            }
            if !s.claimed_now {
                return fail(libc::EINVAL);
            }
            s.claimed_now = false;
            Ok(())
        }
        Request::SubmitUrb(urb) => {
            if s.gone {
                return fail(libc::ENODEV);
            }
            if !s.claimed_now {
                s.auto_claims += 1; // checkintf() would claim it behind our back, with a warning.
            }
            let urb = UrbPtr(urb.as_ptr());
            let u = urb.get();
            if s.recording {
                s.submitted.push(u.endpoint);
                s.seen.push(SeenUrb { endpoint: u.endpoint, kind: u.kind, length: u.buffer_length, flags: u.flags });
            }
            if s.stalled.contains(&u.endpoint) {
                finish(s, urb, -libc::EPIPE, 0, Vec::new());
            } else if u.endpoint & 0x80 != 0 || s.hold_out {
                s.inflight.push_back(urb);
            } else {
                // SAFETY: buffer holds buffer_length bytes the transport wrote before submitting.
                let data = unsafe { std::slice::from_raw_parts(u.buffer.cast::<u8>(), u.buffer_length as usize) }.to_vec();
                let length = u.buffer_length;
                s.sent.push(data);
                let status = s.out_status;
                finish(s, urb, status, if status == 0 { length } else { 0 }, Vec::new());
            }
            Ok(())
        }
        Request::DiscardUrb(urb) => {
            if s.gone {
                return fail(libc::ENODEV);
            }
            let urb = UrbPtr(urb.as_ptr());
            let Some(at) = s.inflight.iter().position(|&u| u == urb) else { return fail(libc::EINVAL) };
            s.inflight.remove(at);
            if s.finish_on_discard {
                // It finished just before the discard reached it.
                let length = urb.get().buffer_length;
                finish(s, urb, 0, length, Vec::new());
                return fail(libc::EINVAL);
            }
            finish(s, urb, -libc::ENOENT, 0, Vec::new());
            Ok(())
        }
        Request::ReapUrbNdelay(slot) => {
            let Some(done) = s.done.pop_front() else {
                drain(s);
                return fail(if s.gone { libc::ENODEV } else { libc::EAGAIN });
            };
            let u = done.urb.get();
            u.status = done.status; // processcompl: user memory changes here, not at completion.
            u.actual_length = done.actual;
            if !done.data.is_empty() {
                // SAFETY: deliver() checked the data fits the URB's buffer.
                unsafe { std::ptr::copy_nonoverlapping(done.data.as_ptr(), u.buffer.cast::<u8>(), done.data.len()) };
            }
            **slot = done.urb.0;
            if s.done.is_empty() {
                drain(s);
            }
            Ok(())
        }
    }
}

impl Sys for FakeUsbfs {
    fn open(&self, path: &Path, flags: c_int) -> Result<RawFd, Errno> {
        let mut s = self.lock();
        let call = if s.tracing {
            let relative = path.strip_prefix(self.tree.dev()).unwrap_or(path);
            format!("open {} {}", relative.display(), open_flag_names(flags))
        } else {
            String::new()
        };
        if !path.exists() {
            return self.traced_fail(&mut s, call, libc::ENOENT);
        }
        s.opens += 1;
        if s.gone {
            return self.traced_fail(&mut s, call, libc::ENODEV); // The node outlives the device for a moment.
        }
        if s.open_failures > 0 {
            s.open_failures -= 1;
            let error = s.open_errno;
            return self.traced_fail(&mut s, call, error);
        }
        s.open_flags = flags;
        // SAFETY: no pointer arguments.
        s.event = unsafe { libc::eventfd(0, libc::EFD_NONBLOCK | libc::EFD_CLOEXEC) };
        if s.tracing {
            s.trace.push(format!("{call} = fd"));
        }
        Ok(s.event)
    }

    fn close(&self, fd: RawFd) {
        let mut s = self.lock();
        // SAFETY: fd is one this fake handed out.
        unsafe { libc::close(fd) };
        if fd != s.event {
            return;
        }
        // usbdev_release() destroys every URB of this file.
        if s.tracing {
            s.trace.push("close = 0".to_string());
        }
        s.event = -1;
        s.inflight.clear();
        s.done.clear();
        s.claimed_now = false;
    }

    fn poll(&self, fds: &mut [libc::pollfd], mut timeout_ms: c_int) -> Result<usize, Errno> {
        assert!(fds.len() <= 4);
        let mut real = [libc::pollfd { fd: -1, events: 0, revents: 0 }; 4];
        real[..fds.len()].copy_from_slice(fds);
        let (event, gone) = {
            let s = self.lock();
            (s.event, s.gone)
        };
        for p in &mut real[..fds.len()] {
            if p.fd == event && gone {
                timeout_ms = 0; // A hang-up never blocks.
            }
            if p.fd == event && p.events & libc::POLLOUT != 0 {
                p.events = libc::POLLIN; // The eventfd stands for "a URB completed".
            }
        }
        // SAFETY: real holds fds.len() valid entries.
        let result = unsafe { libc::poll(real.as_mut_ptr(), fds.len() as libc::nfds_t, timeout_ms) };
        if result < 0 {
            return Err(Errno::last());
        }
        let gone = self.lock().gone;
        let mut ready = 0;
        for (out, p) in fds.iter_mut().zip(&real) {
            out.revents = p.revents;
            if out.fd == event {
                if p.revents & libc::POLLIN != 0 {
                    out.revents = (out.revents & !libc::POLLIN) | libc::POLLOUT;
                }
                if gone {
                    out.revents |= libc::POLLHUP | libc::POLLERR;
                }
            }
            if out.revents != 0 {
                ready += 1;
            }
        }
        Ok(ready)
    }

    unsafe fn ioctl(&self, _fd: RawFd, mut request: Request<'_>) -> Result<(), Errno> {
        let mut s = self.lock();
        if !s.tracing {
            return ioctl_locked(&mut s, &mut request);
        }
        let call = describe_call(&request);
        let result = ioctl_locked(&mut s, &mut request);
        let tail = match (&result, &request) {
            (Err(error), _) => format!("-1 {}", errno_name(error.0)),
            (Ok(()), Request::ReapUrbNdelay(slot)) => describe_reaped(UrbPtr(**slot).get()),
            (Ok(()), _) => "0".to_string(),
        };
        s.trace.push(format!("{call} = {tail}"));
        result
    }
}

/// A transport on a fake kernel whose glasses are plugged in as device 101.
pub fn connected_usb(config: impl FnOnce(&FakeTree) -> TransportConfig) -> (Arc<FakeUsbfs>, Transport) {
    let fake = FakeUsbfs::new();
    fake.arrive(101);
    let transport = Transport::with_sys(config(fake.tree()), fake.clone());
    (fake, transport)
}

pub type Buffer = [u8; 2048];

/// Reads until the status appears or the budget runs out; skips Timeouts. Returns the matching result.
pub fn read_until(t: &mut Transport, buffer: &mut [u8], wanted: IoStatus, budget: Duration) -> TransportRead {
    let end = Instant::now() + budget;
    let mut r = TransportRead { status: IoStatus::Timeout, bytes: 0, endpoint: 0, t_ns: 0, truncated: false };
    while Instant::now() < end {
        r = t.read(buffer, ms(20));
        if r.status == wanted {
            return r;
        }
    }
    TransportRead { status: IoStatus::Timeout, ..r }
}
