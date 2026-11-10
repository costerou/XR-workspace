//! Pro 2 bounded supervised-test owner: the Rust port of `tools/pro2_runtime/bridge.cpp`, with the
//! same arguments, checks, status lines and exit codes, so `tools/pro2_runtime/launch.py` runs
//! either. SDK calls and Publisher writes happen on the main thread only; the SDK's callback
//! thread only fills a 256-entry ring and signals an eventfd, which the main thread waits on
//! (with the display pipe) instead of sleeping between polls.
//!
//! Order, all fail-closed: arguments (any fault: exit 2, nothing written); network namespace
//! with only `lo`; exactly one `35ca:1301` "VITURE PRO 2 XR Glasses" under sysfs; SHA-256 of the
//! SDK descriptor against the pin; dlopen of that descriptor; every function resolved; the SDK's
//! own capability answers; create, family, initialize, start, register, firmware; the private
//! feed; open. Then samples until `--seconds`, a signal or a fault, and cleanup in the SDK's
//! order. A `done` line always ends a run that got past the arguments.
//!
//! Unlike the C++ bridge, a run that was ready survives a USB drop: when the glasses leave sysfs
//! (or change bus address) or go silent for 100 ms, the SDK session is ended in its order, the
//! feed paused (readers hold the last pose) and a `lost` line written. When exactly one Pro 2 is
//! back, a fresh session is brought up (create .. open, retried every 250 ms), the last display
//! mode asked for again, and the first published sample writes a `reconnected` line. Nothing
//! comes back within 30 s, or the run ends while down: `device-lost`. Before ready, and for every
//! other fault, the run still fails closed as the C++ one does.
//!
//! Built with the `synthetic` feature it also takes `--fixture-root` (a fake sysfs) and
//! `--fixture-hash` (the fixture library's hash), for the QA suites. Without it those are
//! unknown arguments, and the run ends with one stderr line, `wake_us n=.. p50=.. p99=.. max=..`:
//! callback to published, per sample.

mod sdk;

use sha2::{Digest, Sha256};
use std::ffi::{CStr, c_int};
use std::fmt::Write as _;
use std::path::Path;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::time::{Duration, Instant};
use xr_pose::feed::WriterOptions;
use xr_pose::publish::{Pose, Publisher, PublisherOptions, Quat, history_step_for};

const SDK_HASH: &str = "8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e";
const PRODUCT_ID: c_int = 0x1301;
const FIRMWARE: &str = "30.0.00.002_20260804";
/// XRLinuxDriver look_ahead_cfg: constant ms, frametime multiplier, scanline ms, cap ms. The cap
/// bounds the prediction; the renderer refuses an unbounded one. No panel constant: the Pro 2's
/// is unmeasured.
const LOOK_AHEAD: [f32; 4] = [0.0, 0.0, 0.0, 40.0];
/// The SDK's set_display_mode values: 3D 3840x1080@60, 2D 1920x1080@120. The only bytes
/// `--display-fd` takes.
const DISPLAY_3D: u8 = 0x32;
const DISPLAY_2D_120: u8 = 0x34;
/// A panel session: Stop ends it first; the bound is the same fail-closed deadline.
const ROUTE_SECONDS: f64 = 12.0 * 3600.0;
const DEFAULT_MAX_SECONDS: f64 = 300.0;
const LIVE_PREFIX: &str = "/tmp/shader_runtime_";
/// How long a lost Pro 2 is waited for, unplug to the first sample of the new session. Kernel
/// re-enumeration takes 0.9 to 4.2 s (docs/rust-rebuild-plan.md); past this the run ends.
const RECONNECT_WINDOW: Duration = Duration::from_secs(30);
/// Sysfs rescan while no Pro 2 is present.
const WAIT_POLL: Duration = Duration::from_millis(10);
/// Pause after a failed bring-up of a returned device (its nodes may still be settling).
const RETRY_AFTER: Duration = Duration::from_millis(250);

/// The SDK's IMU frequency codes, not USB bytes. 60 Hz unless a run asks; each still has to pass
/// the SDK's capability query, and a refusal stops the run rather than dropping to 60.
fn rate_code(hz: i32) -> Option<u8> {
    match hz {
        60 => Some(0),
        90 => Some(1),
        120 => Some(2),
        240 => Some(3),
        _ => None,
    }
}

static STOPPED: AtomicBool = AtomicBool::new(false);
static ACCEPTING: AtomicBool = AtomicBool::new(false);
static OVERFLOW: AtomicBool = AtomicBool::new(false);
/// The eventfd the callback and the signal handlers bump when there is something to look at; -1
/// until `run` makes it.
static WAKE: AtomicI32 = AtomicI32::new(-1);
/// Longest wait without a wake: the identity, overflow and stop checks run at least this often.
const IDLE_CHECK_NS: u64 = 20_000_000;

/// Adds one to the wake counter. Async-signal-safe; the fd is non-blocking, and a counter at its
/// maximum (never, in practice) is already a pending wake.
fn wake() {
    let fd = WAKE.load(Ordering::SeqCst);
    if fd >= 0 {
        let one: u64 = 1;
        // SAFETY: an 8-byte write from a live value to our own eventfd.
        unsafe { libc::write(fd, (&raw const one).cast(), 8) };
    }
}

/// Blocks until the callback or a signal wakes it, the display pipe has a byte or EOF, or
/// `timeout_ns` passes, then clears the counter. A sample pushed between the last empty pop and
/// this call has already bumped the counter, so it returns at once: no wake is lost.
fn wait(timeout_ns: u64, display_fd: i32) {
    let fd = WAKE.load(Ordering::SeqCst);
    let mut fds = [
        libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        },
        libc::pollfd {
            fd: display_fd,
            events: libc::POLLIN,
            revents: 0,
        },
    ];
    let count = if display_fd == -1 { 1 } else { 2 };
    let ms = timeout_ns.div_ceil(1_000_000).min(i32::MAX as u64) as c_int;
    // SAFETY: poll over a live array of `count` entries; a read into a live u64 from our eventfd.
    unsafe {
        libc::poll(fds.as_mut_ptr(), count, ms);
        let mut counter = 0u64;
        libc::read(fd, (&raw mut counter).cast(), 8);
    }
}

#[derive(Clone, Copy)]
struct Sample {
    v: [f32; 7],
    ts: u64,
    host_ns: u64,
}

const RING: usize = 256;

struct Ring {
    items: [Sample; RING],
    head: usize,
    count: usize,
}

static QUEUE: Mutex<Ring> = Mutex::new(Ring {
    items: [Sample {
        v: [0.0; 7],
        ts: 0,
        host_ns: 0,
    }; RING],
    head: 0,
    count: 0,
});

fn monotonic_ns() -> u64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: t is a valid out pointer; CLOCK_MONOTONIC always exists.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) };
    t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64
}

/// The SDK's pose callback, on its thread. Never blocks: a held lock, a null pointer or a full
/// ring marks overflow, which ends the run.
extern "C" fn on_pose(v: *mut f32, ts: u64) {
    if !ACCEPTING.load(Ordering::SeqCst) {
        return;
    }
    let Ok(mut ring) = QUEUE.try_lock() else {
        OVERFLOW.store(true, Ordering::SeqCst);
        return;
    };
    if v.is_null() || ring.count == RING {
        OVERFLOW.store(true, Ordering::SeqCst);
        return;
    }
    let mut s = Sample {
        v: [0.0; 7],
        ts,
        host_ns: monotonic_ns(),
    };
    // SAFETY: the SDK passes 7 floats.
    unsafe { std::ptr::copy_nonoverlapping(v, s.v.as_mut_ptr(), 7) };
    let tail = (ring.head + ring.count) % RING;
    ring.items[tail] = s;
    ring.count += 1;
    drop(ring);
    wake();
}

/// Drops anything left from an ended SDK session, before a new one is accepted.
fn clear_queue() {
    let mut ring = QUEUE.lock().unwrap_or_else(|e| e.into_inner());
    ring.head = 0;
    ring.count = 0;
}

fn pop() -> Option<Sample> {
    let mut ring = QUEUE.lock().unwrap_or_else(|e| e.into_inner());
    if ring.count == 0 {
        return None;
    }
    let s = ring.items[ring.head];
    ring.head = (ring.head + 1) % RING;
    ring.count -= 1;
    Some(s)
}

extern "C" fn on_signal(_: c_int) {
    STOPPED.store(true, Ordering::SeqCst);
    wake();
}

/// Device-time gaps between samples, counted: under 7, 7..9.5, 9.5..13, 13..18, 18..25, 25 ms and
/// over. What the desk test reads.
fn note_gap(counts: &mut [u64; 6], ms: f64) {
    let i = [7.0, 9.5, 13.0, 18.0, 25.0]
        .iter()
        .position(|&edge| ms < edge)
        .unwrap_or(5);
    counts[i] += 1;
}

struct Args {
    sdk_fd: i32,
    status_fd: i32,
    display_fd: i32,
    pose_hz: i32,
    projection: f64,
    seconds: f64,
    mount_pitch: f64,
    prefix: String,
    root: String,
    expected: String,
}

fn parse_args(argv: &[String]) -> Option<Args> {
    let mut a = Args {
        sdk_fd: -1,
        status_fd: -1,
        display_fd: -1,
        pose_hz: 60,
        projection: 0.0,
        seconds: 0.0,
        mount_pitch: 0.0,
        prefix: String::new(),
        root: "/sys/bus/usb/devices".into(),
        expected: SDK_HASH.into(),
    };
    let (mut approved, mut max_seconds) = (false, DEFAULT_MAX_SECONDS);
    let mut it = argv.iter();
    while let Some(flag) = it.next() {
        match flag.as_str() {
            "--supervised-pro2-test" => approved = true,
            "--panel-route" => max_seconds = ROUTE_SECONDS,
            _ => {
                let v = it.next()?;
                match flag.as_str() {
                    "--sdk-fd" => a.sdk_fd = v.parse().ok()?,
                    "--status-fd" => a.status_fd = v.parse().ok()?,
                    "--display-fd" => a.display_fd = v.parse().ok()?,
                    "--prefix" => a.prefix = v.clone(),
                    "--projection" => a.projection = v.parse().ok()?,
                    "--seconds" => a.seconds = v.parse().ok()?,
                    "--pose-rate" => a.pose_hz = v.parse().ok()?,
                    "--mount-pitch" => a.mount_pitch = v.parse().ok()?,
                    #[cfg(feature = "synthetic")]
                    "--fixture-root" => a.root = v.clone(),
                    #[cfg(feature = "synthetic")]
                    "--fixture-hash" => a.expected = v.clone(),
                    _ => return None,
                }
            }
        }
    }
    let scoped = approved
        && a.sdk_fd >= 3
        && a.status_fd >= 3
        && a.projection.is_finite()
        && (44.5..=50.0).contains(&a.projection)
        && rate_code(a.pose_hz).is_some()
        && a.mount_pitch.is_finite()
        && a.mount_pitch.abs() <= 30.0
        && a.seconds.is_finite()
        && a.seconds > 0.0
        && a.seconds <= max_seconds
        && a.prefix.starts_with('/')
        && a.prefix != LIVE_PREFIX;
    if !scoped || a.sdk_fd == a.status_fd {
        return None;
    }
    let mode = |fd: i32| {
        // SAFETY: fstat into a zeroed out struct; a bad fd is an error, not UB.
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        (unsafe { libc::fstat(fd, &mut st) } == 0).then_some(st.st_mode & libc::S_IFMT)
    };
    if mode(a.sdk_fd) != Some(libc::S_IFREG)
        || !matches!(mode(a.status_fd), Some(libc::S_IFIFO | libc::S_IFREG))
    {
        return None;
    }
    if a.display_fd != -1
        && (a.display_fd < 3
            || a.display_fd == a.sdk_fd
            || a.display_fd == a.status_fd
            || mode(a.display_fd) != Some(libc::S_IFIFO)
            // SAFETY: plain fcntl on a descriptor we were handed.
            || unsafe { libc::fcntl(a.display_fd, libc::F_SETFL, libc::O_NONBLOCK) } != 0)
    {
        return None;
    }
    Some(a)
}

/// One status line, in one write. A gone reader is not this process's problem (SIGPIPE ignored).
fn status(fd: i32, line: &str) {
    let mut rest = line.as_bytes();
    while !rest.is_empty() {
        // SAFETY: writing a live buffer to a descriptor we were handed.
        let n = unsafe { libc::write(fd, rest.as_ptr().cast(), rest.len()) };
        if n <= 0 {
            return;
        }
        rest = &rest[n as usize..];
    }
}

/// Only `lo`: the SDK must not reach the network (docs/pro2/sdk-licence/README.md, section 8).
fn network_isolated() -> Result<(), &'static str> {
    let mut list: *mut libc::ifaddrs = std::ptr::null_mut();
    // SAFETY: getifaddrs fills list, freed below; names are NUL-terminated.
    unsafe {
        if libc::getifaddrs(&mut list) != 0 {
            return Err("network-namespace");
        }
        let mut isolated = true;
        let mut p = list;
        while !p.is_null() {
            if CStr::from_ptr((*p).ifa_name).to_bytes() != b"lo" {
                isolated = false;
            }
            p = (*p).ifa_next;
        }
        libc::freeifaddrs(list);
        if isolated {
            Ok(())
        } else {
            Err("network-not-isolated")
        }
    }
}

/// The first line of a sysfs attribute. Empty or unreadable: fault. `None`: the file is gone,
/// which is a device being unplugged while it is read.
fn first_line(path: &Path) -> Result<Option<String>, &'static str> {
    let text = match std::fs::read(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("identity-unreadable"),
    };
    if text.is_empty() {
        return Err("identity-unreadable");
    }
    let line = text.split(|&b| b == b'\n').next().unwrap_or_default();
    Ok(Some(String::from_utf8_lossy(line).into_owned()))
}

/// Exactly one VITURE device under `root`, and it is a Pro 2: `<name>:<busnum>:<devnum>`, which
/// changes if it is unplugged or another device takes its place. `None`: no VITURE device, or the
/// only one vanished while it was read (unplugged). Two, or one that is not a Pro 2: fault.
fn identity(root: &str) -> Result<Option<String>, &'static str> {
    let fs_error = |_| "identity-filesystem";
    let (mut fingerprint, mut found) = (None, 0);
    for entry in std::fs::read_dir(root).map_err(fs_error)? {
        let dir = entry.map_err(fs_error)?.path();
        let vendor = dir.join("idVendor");
        match std::fs::metadata(&vendor) {
            Ok(_) => {}
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
                ) =>
            {
                continue;
            }
            Err(_) => return Err("identity-filesystem"),
        }
        let attribute = |name: &str| first_line(&dir.join(name));
        let Some(vendor) = attribute("idVendor")? else {
            continue;
        };
        if vendor != "35ca" {
            continue;
        }
        let (Some(product_id), Some(product)) = (attribute("idProduct")?, attribute("product")?)
        else {
            continue; // went away mid-read
        };
        if product_id != "1301" || product != "VITURE PRO 2 XR Glasses" {
            return Err("identity-mismatch");
        }
        let (Some(bus), Some(dev)) = (attribute("busnum")?, attribute("devnum")?) else {
            continue;
        };
        let name = dir
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        found += 1;
        fingerprint = Some(format!("{name}:{bus}:{dev}"));
    }
    match found {
        0 => Ok(None),
        1 => Ok(fingerprint),
        _ => Err("exclusive-identity-required"),
    }
}

/// SHA-256 of the whole file behind `fd`, read from its start, lower-case hex.
fn hash_fd(fd: i32) -> Result<String, &'static str> {
    // SAFETY: plain system calls on a descriptor we were handed, into a live buffer.
    if unsafe { libc::lseek(fd, 0, libc::SEEK_SET) } < 0 {
        return Err("sdk-fd");
    }
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 8192];
    loop {
        let n = unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) };
        if n < 0 {
            return Err("hash-read");
        }
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n as usize]);
    }
    let mut hex = String::with_capacity(64);
    for b in hasher.finalize() {
        let _ = write!(hex, "{b:02x}");
    }
    Ok(hex)
}

/// What the run reports in its done line.
#[derive(Default)]
struct Run {
    ready: bool,
    samples: u64,
    gaps: [u64; 6],
    handle: Option<*mut std::ffi::c_void>,
    open_attempt: bool,
    publisher: Option<Publisher>,
    /// Callback to published, per sample, for the synthetic build's stderr report.
    #[cfg(feature = "synthetic")]
    wake_ns: Vec<u64>,
}

/// One SDK session's sample checks. A reconnect starts a new one: the device clock may restart,
/// and the first two samples are skipped again.
#[derive(Default)]
struct Session {
    prev: u64,
    have: bool,
    valid: u32,
}

/// Create, family, initialize, start, register, firmware, the feed (first time only), open: the
/// SDK's bring-up, on this thread. Every handle made is in `out` for [`teardown`].
///
/// # Safety
/// `sdk` is the resolved SDK; called on the thread that makes every SDK call.
unsafe fn connect(
    sdk: &sdk::Sdk,
    args: &Args,
    code: u8,
    out: &mut Run,
) -> Result<(), &'static str> {
    // SAFETY: every call below is the SDK's C API as the C++ bridge makes it, on this thread.
    unsafe {
        let handle = (sdk.create)(PRODUCT_ID);
        if handle.is_null() {
            return Err("create");
        }
        out.handle = Some(handle);
        if (sdk.get_device_type)(handle) != 1 {
            return Err("family");
        }
        if (sdk.initialize)(handle, std::ptr::null(), std::ptr::null()) != 0 {
            return Err("initialize");
        }
        if (sdk.start)(handle) != 0 {
            return Err("start");
        }
        if (sdk.register_imu_pose_callback)(handle, on_pose) != 0 {
            return Err("register");
        }
        let mut version = [0u8; 256];
        let mut length: c_int = 256;
        let rc = (sdk.get_glasses_version)(handle, version.as_mut_ptr().cast(), &mut length);
        let text = CStr::from_bytes_until_nul(&version).ok();
        let firmware_ok = rc == 0
            && (1..=256).contains(&length)
            && text.is_some_and(|t| {
                let n = t.to_bytes().len() as c_int;
                t.to_bytes() == FIRMWARE.as_bytes() && (length == n || length == n + 1)
            });
        if !firmware_ok {
            return Err("firmware");
        }
        if out.publisher.is_none() {
            let options = PublisherOptions {
                feed: WriterOptions {
                    prefix: args.prefix.clone(),
                    display_fov_deg: args.projection as f32,
                    look_ahead_cfg: LOOK_AHEAD,
                    private_segments: true,
                    ..WriterOptions::default()
                },
                history_step: history_step_for(args.pose_hz as u32),
                mount_pitch_deg: args.mount_pitch as f32,
                instant_start: true,
                fractional_ms: true,
            };
            out.publisher = Some(Publisher::open(&options).map_err(|_| "publisher-open")?);
        }
        clear_queue();
        ACCEPTING.store(true, Ordering::SeqCst);
        out.open_attempt = true;
        if (sdk.open_imu)(handle, 1, code) != 0 {
            return Err("open");
        }
    }
    Ok(())
}

/// Stops taking samples and ends the SDK session in its order: close (if open was tried), stop,
/// shutdown, destroy. Returns how many calls failed. Leaves the feed as it is.
fn teardown(sdk: &sdk::Sdk, out: &mut Run) -> u32 {
    ACCEPTING.store(false, Ordering::SeqCst);
    let mut errors = 0;
    if let Some(handle) = out.handle.take() {
        // SAFETY: the SDK's teardown, in its order, on the thread that made the handle.
        unsafe {
            if out.open_attempt && (sdk.close_imu)(handle, 1) != 0 {
                errors += 1;
            }
            for call in [sdk.stop, sdk.shutdown] {
                if call(handle) != 0 {
                    errors += 1;
                }
            }
            (sdk.destroy)(handle);
        }
    }
    out.open_attempt = false;
    errors
}

/// Between a lost device and the next published sample: when it began, and the bring-up
/// attempts so far.
struct Outage {
    since: Instant,
    attempts: u32,
}

/// The glasses are gone, or went silent: end the SDK session, pause the feed (readers hold the
/// last pose) and wait. A fresh session is brought up when exactly one Pro 2 is present again.
fn lose(sdk: &sdk::Sdk, status_fd: i32, out: &mut Run, outage: &mut Option<Outage>, reason: &str) {
    let errors = teardown(sdk, out);
    if let Some(p) = out.publisher.as_mut() {
        p.pause();
    }
    status(
        status_fd,
        &format!("{{\"event\":\"lost\",\"reason\":\"{reason}\",\"teardown_errors\":{errors}}}\n"),
    );
    outage.get_or_insert(Outage {
        since: Instant::now(),
        attempts: 0,
    });
}

fn run(
    args: &mut Args,
    sdk_slot: &mut Option<sdk::Sdk>,
    out: &mut Run,
) -> Result<(), &'static str> {
    network_isolated()?;
    let mut fingerprint = identity(&args.root)?.ok_or("exclusive-identity-required")?;
    if hash_fd(args.sdk_fd)? != args.expected {
        return Err("sdk-hash");
    }
    // SAFETY: plain eventfd creation; the fd lives until the process exits.
    let efd = unsafe { libc::eventfd(0, libc::EFD_CLOEXEC | libc::EFD_NONBLOCK) };
    if efd < 0 {
        return Err("eventfd");
    }
    WAKE.store(efd, Ordering::SeqCst);
    let library = sdk::load(args.sdk_fd)?;
    let sdk = &*sdk_slot.insert(sdk::Sdk::resolve(library, args.display_fd != -1)?);
    let code = rate_code(args.pose_hz).ok_or("arguments")?;
    // SAFETY: the SDK's capability queries, on this thread.
    unsafe {
        if (sdk.is_product_id_valid)(PRODUCT_ID) != 1
            || (sdk.is_product_support_imu_frequency)(PRODUCT_ID, 1, code.into()) != 1
        {
            return Err("capability");
        }
    }
    if identity(&args.root)?.as_ref() != Some(&fingerprint) {
        return Err("identity-changed");
    }
    // SAFETY: on this thread, with the resolved SDK.
    unsafe { connect(sdk, args, code, out)? };

    let start = Instant::now();
    let mut last_ns = monotonic_ns();
    let mut session = Session::default();
    let mut outage: Option<Outage> = None;
    let mut display_mode: Option<u8> = None;
    // Once the run was ready, a lost device is waited for instead of ending the run (see
    // [`lose`]). Before ready, every fault still ends the run.
    while !STOPPED.load(Ordering::SeqCst) && start.elapsed().as_secs_f64() < args.seconds {
        if outage
            .as_ref()
            .is_some_and(|o| o.since.elapsed() > RECONNECT_WINDOW)
        {
            return Err("device-lost");
        }
        let present = identity(&args.root)?;
        if out.handle.is_none() {
            // Down: wait for the glasses, then bring a session up.
            let Some(found) = present else {
                wait(WAIT_POLL.as_nanos() as u64, -1);
                continue;
            };
            fingerprint = found;
            let o = outage.as_mut().ok_or("device-lost")?;
            o.attempts += 1;
            // SAFETY: on this thread, with the resolved SDK.
            if unsafe { connect(sdk, args, code, out) }.is_err() {
                teardown(sdk, out);
                wait(RETRY_AFTER.as_nanos() as u64, -1);
                continue;
            }
            session = Session::default();
            last_ns = monotonic_ns();
            if let (Some(mode), Some(set), Some(handle)) =
                (display_mode, sdk.set_display_mode, out.handle)
            {
                // A drop can reset the glasses' display mode: ask again for the last one set.
                // SAFETY: the SDK's call, on this thread, with a live handle.
                let result = unsafe { set(handle, mode.into()) };
                status(
                    args.status_fd,
                    &format!("{{\"event\":\"display\",\"mode\":{mode},\"result\":{result}}}\n"),
                );
            }
            continue;
        }
        if present.as_ref() != Some(&fingerprint) {
            if !out.ready {
                return Err(if present.is_some() {
                    "identity-changed"
                } else {
                    "exclusive-identity-required"
                });
            }
            lose(sdk, args.status_fd, out, &mut outage, "unplugged");
            continue;
        }
        if OVERFLOW.load(Ordering::SeqCst) {
            return Err("queue-overflow-or-null");
        }
        let handle = out.handle.ok_or("device-lost")?;
        if args.display_fd != -1 {
            // The parent's 3D switch, on this thread like every SDK call. EOF: the parent
            // closed it, and no switch can come again.
            let mut mode = 0u8;
            // SAFETY: a one-byte read into a live byte from a descriptor we were handed.
            let n = unsafe { libc::read(args.display_fd, (&raw mut mode).cast(), 1) };
            if n == 0 {
                args.display_fd = -1;
            } else if n == 1 {
                if mode != DISPLAY_3D && mode != DISPLAY_2D_120 {
                    return Err("display-request");
                }
                let set = sdk.set_display_mode.ok_or("display-request")?;
                // SAFETY: the SDK's call, on this thread, with a live handle.
                let result = unsafe { set(handle, mode.into()) };
                if result == 0 {
                    display_mode = Some(mode);
                }
                status(
                    args.status_fd,
                    &format!("{{\"event\":\"display\",\"mode\":{mode},\"result\":{result}}}\n"),
                );
            } else {
                let errno = std::io::Error::last_os_error().raw_os_error();
                if !matches!(errno, Some(libc::EAGAIN | libc::EINTR)) {
                    return Err("display-read");
                }
            }
        }
        let Some(s) = pop() else {
            let silent = monotonic_ns().saturating_sub(last_ns);
            // Before ready, or reconnected but no sample yet: 3 s. A live session: 100 ms.
            let limit = if !out.ready || outage.is_some() {
                3_000_000_000
            } else {
                100_000_000
            };
            if silent > limit {
                if !out.ready {
                    return Err("no-pose");
                }
                let reason = if outage.is_some() { "no-pose" } else { "stale" };
                lose(sdk, args.status_fd, out, &mut outage, reason);
                continue;
            }
            // Wake just past the silence limit or the run's end, whichever is first, and at
            // least every IDLE_CHECK_NS so the identity check still runs while no pose comes.
            let left = (args.seconds - start.elapsed().as_secs_f64()).max(0.0);
            let until_end = (left * 1e9) as u64;
            wait(
                (limit - silent + 1).min(until_end).min(IDLE_CHECK_NS),
                args.display_fd,
            );
            continue;
        };
        last_ns = s.host_ns;
        if monotonic_ns().saturating_sub(s.host_ns) > 100_000_000 {
            return Err("queued-stale");
        }
        if !s.v.iter().all(|v| v.is_finite()) {
            return Err("nonfinite");
        }
        if session.have && s.ts <= session.prev {
            return Err("clock-reset-wrap-duplicate");
        }
        if session.have {
            note_gap(&mut out.gaps, (s.ts - session.prev) as f64 / 1e6);
        }
        session.prev = s.ts;
        session.have = true;
        let v = s.v.map(f64::from);
        let norm = (v[3] * v[3] + v[4] * v[4] + v[5] * v[5] + v[6] * v[6]).sqrt();
        if (norm - 1.0).abs() > 0.05 {
            return Err("quaternion-norm");
        }
        session.valid += 1;
        if session.valid < 3 {
            continue;
        }
        // SDK w, x, y, z to the publisher's x forward, y left, z up, as the C++ bridge.
        let q = Quat {
            w: (v[3] / norm) as f32,
            x: (-v[6] / norm) as f32,
            y: (-v[4] / norm) as f32,
            z: (v[5] / norm) as f32,
        };
        let publisher = out.publisher.as_mut().ok_or("publisher-open")?;
        // False here only for a reader holding the lock: that one write is skipped and its
        // sample kept, never the session's end.
        if !publisher.publish(&Pose {
            q,
            t_ns: s.ts,
            calibrated: true,
        }) {
            continue;
        }
        out.samples += 1;
        #[cfg(feature = "synthetic")]
        out.wake_ns.push(monotonic_ns().saturating_sub(s.host_ns));
        if let Some(o) = outage.take() {
            status(
                args.status_fd,
                &format!(
                    "{{\"event\":\"reconnected\",\"down_ms\":{:.1},\"attempts\":{}}}\n",
                    o.since.elapsed().as_secs_f64() * 1e3,
                    o.attempts
                ),
            );
        }
        if !out.ready {
            out.ready = true;
            status(
                args.status_fd,
                &format!(
                    "{{\"event\":\"ready\",\"projection_diagonal_approx\":{:.3},\"pose_rate_hz\":{},\"mount_pitch_deg\":{:.3}}}\n",
                    args.projection, args.pose_hz, args.mount_pitch
                ),
            );
        }
    }
    if outage.is_some() && !STOPPED.load(Ordering::SeqCst) {
        return Err("device-lost");
    }
    if !out.ready && !STOPPED.load(Ordering::SeqCst) {
        return Err("no-ready");
    }
    Ok(())
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some(mut args) = parse_args(&argv) else {
        std::process::exit(2)
    };
    // SAFETY: installing async-signal-safe handlers (an atomic store) before any thread exists.
    unsafe {
        libc::signal(
            libc::SIGINT,
            on_signal as extern "C" fn(c_int) as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGTERM,
            on_signal as extern "C" fn(c_int) as libc::sighandler_t,
        );
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
    let began = Instant::now();
    let mut sdk = None;
    let mut out = Run::default();
    let error = run(&mut args, &mut sdk, &mut out).err().unwrap_or("");

    ACCEPTING.store(false, Ordering::SeqCst);
    if let Some(p) = out.publisher.as_mut() {
        p.pause();
    }
    let cleanup_errors = sdk.as_ref().map_or(0, |sdk| teardown(sdk, &mut out));
    drop(out.publisher.take()); // removes the private feed before the run is reported done
    let g = out.gaps;
    status(
        args.status_fd,
        &format!(
            "{{\"event\":\"done\",\"ready\":{},\"samples\":{},\"seconds\":{:.3},\"pose_rate_hz\":{},\"gap_ms_counts\":[{},{},{},{},{},{}],\"cleanup_errors\":{},\"error\":\"{}\"}}\n",
            out.ready,
            out.samples,
            began.elapsed().as_secs_f64(),
            args.pose_hz,
            g[0],
            g[1],
            g[2],
            g[3],
            g[4],
            g[5],
            cleanup_errors,
            error
        ),
    );
    #[cfg(feature = "synthetic")]
    {
        let w = &mut out.wake_ns;
        w.sort_unstable();
        let at = |p: usize| {
            w.get((w.len().saturating_sub(1)) * p / 100)
                .map_or(0.0, |&n| n as f64 / 1e3)
        };
        eprintln!(
            "wake_us n={} p50={:.1} p99={:.1} max={:.1}",
            w.len(),
            at(50),
            at(99),
            at(100)
        );
    }
    std::process::exit(if error.is_empty() && cleanup_errors == 0 {
        0
    } else {
        1
    });
}
