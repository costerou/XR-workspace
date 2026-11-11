//! The head pose as XRLinuxDriver publishes it: writer (the C++ `luma::Publisher` segments,
//! `spike/driver/src/publish.cpp`) and reader (`xr::PoseFeed`, `spike/src/pose.cpp`).
//!
//! One SysV segment per value, each keyed `ftok(<prefix><name>, 0)` on an empty file, created 0666:
//!
//! | name                     | contents                                                        |
//! |--------------------------|-----------------------------------------------------------------|
//! | `pose_orientation`       | 16 floats: q0 xyzw, q1 xyzw, q2 xyzw, t0, t1, t2, 0             |
//! | `pose_orientation_mutex` | glibc `pthread_mutex_t`, process-shared and robust, guards them |
//! | `disabled`               | bool: true = readers get no sample                              |
//! | `display_fov`            | float, degrees                                                  |
//! | `look_ahead_cfg`         | 4 floats: constant ms, frametime multiplier, scanline ms, cap ms |
//! | `pose_prediction`        | [`PredictionWire`]: the newest pose with its body-frame motion  |
//!
//! `pose_prediction` is the Luma driver's side channel; XRLinuxDriver has none, and its readers
//! never look for it.
//!
//! The mutex stays the glibc one because XRLinuxDriver-compatible readers share it (plan, section
//! 2). What goes into the 16 floats (history, timestamps, mount pitch) is the driver's business;
//! this module moves them.
//!
//! Locking rules, the same on both sides as in C++:
//! - Never `pthread_mutex_trylock`: on a not-recoverable robust mutex glibc's trylock leaves the
//!   caller's thread id in the lock word, which hangs the other side's next lock forever.
//!   `pthread_mutex_clocklock` with an already-passed deadline is the same atomic when the lock is
//!   free, and releases the word on `ENOTRECOVERABLE`.
//! - `EOWNERDEAD`: the other side died holding the lock. It is made consistent and used; nothing
//!   in the 16 floats needs repair beyond the next write.
//! - The writer never waits: a held lock skips that one write.
//! - The reader waits at most 2 ms, then serves the newest sample again, and backs off from a
//!   lock that stays held.

use crate::prediction::{MotionState, PREDICTION_IPC_NAME, PredictionWire};
use std::ffi::{CString, c_int, c_void};
use std::fmt;
use std::fs;
use std::io;
use std::os::fd::{FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::atomic::{AtomicU8, AtomicU32, Ordering, fence};

/// The live feed's prefix, shared with XRLinuxDriver and the C++ stack.
pub const DEFAULT_PREFIX: &str = "/tmp/shader_runtime_";

const POSE: &str = "pose_orientation";
const MUTEX: &str = "pose_orientation_mutex";
const DISABLED: &str = "disabled";
const FOV: &str = "display_fov";
const LOOK_AHEAD: &str = "look_ahead_cfg";
/// The Luma driver's scan-out times (spike/driver/include/luma/vsync.hpp VsyncWire): 16 words.
const VSYNC: &str = "pose_vsync";
const VSYNC_BYTES: usize = 64;
const POSE_BYTES: usize = 16 * size_of::<f32>();
const LOOK_AHEAD_BYTES: usize = 4 * size_of::<f32>();

/// XRLinuxDriver `src/imu.c pose_orientation_reset_data`: four identities.
const RESET_POSE: [f32; 16] = [
    0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1.,
];

unsafe extern "C" {
    // glibc 2.30+. Not in the libc crate. x86_64 and aarch64 have a 64-bit time_t, so no
    // time64 redirect applies.
    fn pthread_mutex_clocklock(
        mutex: *mut libc::pthread_mutex_t,
        clock: libc::clockid_t,
        abstime: *const libc::timespec,
    ) -> c_int;
}

/// Why a feed could not be opened.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedError(pub String);

impl fmt::Display for FeedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for FeedError {}

fn fail<T>(why: String) -> Result<T, FeedError> {
    Err(FeedError(why))
}

fn errno() -> io::Error {
    io::Error::last_os_error()
}

fn c_path(path: &str) -> Result<CString, FeedError> {
    CString::new(path).map_err(|_| FeedError(format!("path has a NUL: {path}")))
}

fn open_file(path: &CString, flags: c_int, mode: libc::mode_t) -> io::Result<OwnedFd> {
    // SAFETY: path is NUL-terminated; the fd is owned from here on.
    let fd = unsafe { libc::open(path.as_ptr(), flags | libc::O_CLOEXEC, mode as libc::c_uint) };
    if fd < 0 {
        return Err(errno());
    }
    // SAFETY: fd is a fresh descriptor nobody else owns.
    Ok(unsafe { OwnedFd::from_raw_fd(fd) })
}

fn ftok(path: &CString) -> Option<libc::key_t> {
    // SAFETY: path is NUL-terminated.
    let key = unsafe { libc::ftok(path.as_ptr(), 0) };
    (key != -1).then_some(key)
}

fn monotonic_ns() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: ts is a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

fn timespec_of(ns: u64) -> libc::timespec {
    libc::timespec {
        tv_sec: (ns / 1_000_000_000) as libc::time_t,
        tv_nsec: (ns % 1_000_000_000) as _,
    }
}

/// Lock with a deadline on the monotonic clock, which wall-clock steps cannot stretch. `None` =
/// an already-passed deadline: no wait, no system call when the lock is free.
fn clocklock(mutex: *mut libc::pthread_mutex_t, until_ns: Option<u64>) -> c_int {
    let deadline = until_ns.map_or(
        libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        },
        timespec_of,
    );
    // SAFETY: mutex points into an attached segment of at least pthread_mutex_t's size.
    unsafe { pthread_mutex_clocklock(mutex, libc::CLOCK_MONOTONIC, &deadline) }
}

/// One attached segment. Detached on drop, and removed too when this process created it for a
/// private feed.
struct Segment {
    addr: *mut c_void,
    id: c_int,
    remove: bool,
}

impl Drop for Segment {
    fn drop(&mut self) {
        // SAFETY: addr came from shmat and is detached once.
        unsafe { libc::shmdt(self.addr) };
        if self.remove {
            // SAFETY: id is a segment this process created.
            unsafe { libc::shmctl(self.id, libc::IPC_RMID, ptr::null_mut()) };
        }
    }
}

fn shmat(id: c_int, flags: c_int, remove: bool, path: &str) -> Result<Segment, FeedError> {
    // SAFETY: plain system call; the result is checked.
    let addr = unsafe { libc::shmat(id, ptr::null(), flags) };
    if addr as isize == -1 {
        let err = errno();
        if remove {
            // SAFETY: id is a segment this process just created.
            unsafe { libc::shmctl(id, libc::IPC_RMID, ptr::null_mut()) };
        }
        return fail(format!("shmat failed: {path}: {err}"));
    }
    Ok(Segment { addr, id, remove })
}

/// Options for [`FeedWriter::open`].
#[derive(Debug, Clone)]
pub struct WriterOptions {
    /// [`DEFAULT_PREFIX`] in production; tests pass a private one with `private_segments`.
    pub prefix: String,
    pub display_fov_deg: f32,
    pub look_ahead_cfg: [f32; 4],
    /// false (production): reuse segments that exist, as the driver does, and leave them when
    ///   closing, so a renderer stays attached across a driver restart.
    /// true (tests): ftok keys hold 16 bits of the inode, so even a private prefix can land on a
    ///   live driver's key. Then only ever create segments: open fails with "key in use" rather
    ///   than attach, closing removes them and their files, and the writer checks are skipped.
    pub private_segments: bool,
    /// Two writers garble the pose and one would re-initialise the mutex under the other. Without
    /// `private_segments`, open fails with "another publisher" while one holds
    /// `<prefix>writer.lock` (released even if it crashes) and with "xrDriver is running" when a
    /// live process has that name under this directory. Checked at open only.
    pub proc_root: PathBuf,
}

impl Default for WriterOptions {
    fn default() -> Self {
        Self {
            prefix: DEFAULT_PREFIX.to_owned(),
            display_fov_deg: 52.0,
            look_ahead_cfg: [10.0, 0.6, 10.0, 40.0],
            private_segments: false,
            proc_root: PathBuf::from("/proc"),
        }
    }
}

/// The single writer of a feed. For one thread.
pub struct FeedWriter {
    pose: *mut f32,
    mutex: *mut libc::pthread_mutex_t,
    disabled: *mut u8,
    /// `<prefix>pose_prediction`: the motion side channel, invalid until a motion is written.
    prediction: *const PredictionWire,
    segments: Vec<Segment>,
    /// private_segments: the files this writer created, removed on drop.
    files: Vec<PathBuf>,
    /// Held for the writer's life; the flock goes with the open file, so a crash releases it.
    _writer_lock: Option<OwnedFd>,
}

// SAFETY: the pointers are into process-shared memory that other processes use concurrently
// anyway; every access goes through &mut self or an atomic.
unsafe impl Send for FeedWriter {}

/// A live process named `xrDriver` under `proc_root`: its pid. In a stat line the name runs to
/// the last ')' and the state follows; a zombie keeps its name until reaped and writes nothing.
fn running_xrdriver(proc_root: &Path) -> Option<String> {
    for entry in fs::read_dir(proc_root).ok()?.flatten() {
        let pid = entry.file_name();
        let pid = pid.as_bytes();
        if pid.is_empty() || !pid.iter().all(u8::is_ascii_digit) {
            continue;
        }
        let Ok(line) = fs::read(entry.path().join("stat")) else {
            continue;
        };
        let (Some(open), Some(close)) = (
            line.iter().position(|&c| c == b'('),
            line.iter().rposition(|&c| c == b')'),
        ) else {
            continue;
        };
        if close < open || close + 2 >= line.len() {
            continue;
        }
        let state = line[close + 2];
        if &line[open + 1..close] == b"xrDriver" && state != b'Z' && state != b'X' {
            return Some(String::from_utf8_lossy(pid).into_owned());
        }
    }
    None
}

/// One writer per feed: `flock` on `<prefix>writer.lock`, then no live `xrDriver`.
fn claim(prefix: &str, proc_root: &Path) -> Result<OwnedFd, FeedError> {
    let path = format!("{prefix}writer.lock");
    let fd = open_file(&c_path(&path)?, libc::O_CREAT | libc::O_RDONLY, 0o666)
        .or_else(|e| fail(format!("cannot create {path}: {e}")))?;
    use std::os::fd::AsRawFd;
    // SAFETY: fd is open.
    if unsafe { libc::flock(fd.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return fail(format!("another publisher is running on {prefix}"));
    }
    if let Some(pid) = running_xrdriver(proc_root) {
        return fail(format!("xrDriver is running (pid {pid})"));
    }
    Ok(fd)
}

/// Over whatever is there, as XRLinuxDriver's src/ipc.c does: the bytes may be zeroes, or a lock
/// a dead process held.
fn init_mutex(mutex: *mut libc::pthread_mutex_t) -> bool {
    // SAFETY: mutex points into an attached, writable segment of pthread_mutex_t's size; attr is
    // initialised before use and destroyed once.
    unsafe {
        libc::pthread_mutex_destroy(mutex);
        let mut attr: libc::pthread_mutexattr_t = std::mem::zeroed();
        if libc::pthread_mutexattr_init(&mut attr) != 0 {
            return false;
        }
        let ok = libc::pthread_mutexattr_setpshared(&mut attr, libc::PTHREAD_PROCESS_SHARED) == 0
            && libc::pthread_mutexattr_setrobust(&mut attr, libc::PTHREAD_MUTEX_ROBUST) == 0
            && libc::pthread_mutex_init(mutex, &attr) == 0;
        libc::pthread_mutexattr_destroy(&mut attr);
        ok
    }
}

impl FeedWriter {
    /// Creates the files and segments, re-initialises the mutex, stores identity, FOV and
    /// look-ahead, and leaves the feed disabled.
    pub fn open(options: &WriterOptions) -> Result<Self, FeedError> {
        let writer_lock = if options.private_segments {
            None
        } else {
            Some(claim(&options.prefix, &options.proc_root)?)
        };
        let mut writer = FeedWriter {
            pose: ptr::null_mut(),
            mutex: ptr::null_mut(),
            disabled: ptr::null_mut(),
            prediction: ptr::null(),
            segments: Vec::new(),
            files: Vec::new(),
            _writer_lock: writer_lock,
        };
        // disabled first: a reader still attached from before stops looking at the rest while it
        // changes.
        writer.disabled = writer.attach(options, DISABLED, size_of::<bool>())?.cast();
        writer.set_disabled(true);
        writer.pose = writer.attach(options, POSE, POSE_BYTES)?.cast();
        writer.mutex = writer
            .attach(options, MUTEX, size_of::<libc::pthread_mutex_t>())?
            .cast();
        let fov: *mut f32 = writer.attach(options, FOV, size_of::<f32>())?.cast();
        let look_ahead: *mut f32 = writer.attach(options, LOOK_AHEAD, LOOK_AHEAD_BYTES)?.cast();
        // As the C++ publisher: one writer (the claim above), and a reader retries on any change
        // of seq, so the line is written over whatever a killed writer left.
        writer.prediction = writer
            .attach(options, PREDICTION_IPC_NAME, size_of::<PredictionWire>())?
            .cast();
        writer.write_motion(&MotionState::default());
        if !init_mutex(writer.mutex) {
            return fail("cannot initialise the pose mutex".into());
        }
        // SAFETY: each pointer is into a segment of at least the size written; readers are kept
        // off by disabled.
        unsafe {
            ptr::copy_nonoverlapping(RESET_POSE.as_ptr(), writer.pose, 16);
            fov.write_volatile(options.display_fov_deg);
            ptr::copy_nonoverlapping(options.look_ahead_cfg.as_ptr(), look_ahead, 4);
        }
        Ok(writer)
    }

    fn attach(
        &mut self,
        options: &WriterOptions,
        name: &str,
        size: usize,
    ) -> Result<*mut c_void, FeedError> {
        let path = format!("{}{name}", options.prefix);
        let cpath = c_path(&path)?;
        let own = options.private_segments;
        let mut fd = None;
        if own {
            fd = open_file(&cpath, libc::O_CREAT | libc::O_EXCL | libc::O_RDONLY, 0o600).ok();
            if fd.is_some() {
                self.files.push(PathBuf::from(&path));
            }
        }
        let fd = match fd {
            Some(fd) => fd,
            None => open_file(
                &cpath,
                libc::O_CREAT | libc::O_RDONLY,
                if own { 0o600 } else { 0o666 },
            )
            .or_else(|e| fail(format!("cannot create {path}: {e}")))?,
        };
        if !own {
            // src/ipc.c sets umask 0 for this; umask is per process, and it may be someone
            // else's file. Best effort, as in C++.
            use std::os::fd::AsRawFd;
            // SAFETY: fd is open.
            unsafe { libc::fchmod(fd.as_raw_fd(), 0o666) };
        }
        drop(fd);
        let Some(key) = ftok(&cpath) else {
            return fail(format!("ftok failed: {path}"));
        };
        // SAFETY (all shmget/shmctl below): plain system calls; results are checked.
        let id = if own {
            let id = unsafe { libc::shmget(key, size, 0o600 | libc::IPC_CREAT | libc::IPC_EXCL) };
            if id < 0 && errno().raw_os_error() == Some(libc::EEXIST) {
                return fail(format!("key in use: {path}"));
            }
            id
        } else {
            let mut id = unsafe { libc::shmget(key, size, 0o666 | libc::IPC_CREAT) };
            if id < 0 && errno().raw_os_error() == Some(libc::EINVAL) {
                // Left over at another size (src/ipc.c setup_ipc_value).
                let stale = unsafe { libc::shmget(key, 0, 0) };
                if stale >= 0 {
                    unsafe { libc::shmctl(stale, libc::IPC_RMID, ptr::null_mut()) };
                }
                id = unsafe { libc::shmget(key, size, 0o666 | libc::IPC_CREAT) };
            }
            id
        };
        if id < 0 {
            return fail(format!("shmget failed: {path}: {}", errno()));
        }
        let segment = shmat(id, 0, own, &path)?;
        let addr = segment.addr;
        self.segments.push(segment);
        Ok(addr)
    }

    /// Never waits: on a held lock the write is skipped. A dead holder's lock is made
    /// consistent; a not-recoverable one is rebuilt.
    fn lock(&mut self) -> bool {
        let mut rc = clocklock(self.mutex, None);
        if rc == libc::ENOTRECOVERABLE && init_mutex(self.mutex) {
            rc = clocklock(self.mutex, None);
        }
        if rc == libc::EOWNERDEAD {
            // A reader only copies: nothing to repair.
            // SAFETY: this thread owns the mutex now.
            unsafe { libc::pthread_mutex_consistent(self.mutex) };
            rc = 0;
        }
        rc == 0
    }

    /// Writes the 16 floats under the lock. False when the lock was held (the write is skipped).
    /// Does not touch `disabled`: call [`set_disabled`](Self::set_disabled)`(false)` after the
    /// first real write, so a reader's first sample is a real one.
    pub fn write(&mut self, values: &[f32; 16]) -> bool {
        if !self.lock() {
            return false;
        }
        // SAFETY: pose is a 16-float segment, guarded by the mutex this thread holds.
        unsafe {
            ptr::copy_nonoverlapping(values.as_ptr(), self.pose, 16);
            libc::pthread_mutex_unlock(self.mutex);
        }
        true
    }

    /// The motion side channel. An invalid state reads as no motion.
    pub fn write_motion(&self, state: &MotionState) {
        // SAFETY: prediction points at a page-aligned segment of PredictionWire's size, whose
        // fields are atomics only; this is its one writer.
        unsafe { &*self.prediction }.write(state);
    }

    /// true: readers get no sample until it is cleared.
    pub fn set_disabled(&self, value: bool) {
        // SAFETY: disabled is a byte in an attached, writable segment; the C++ writer also stores
        // it atomically (std::atomic_ref<bool>).
        unsafe { AtomicU8::from_ptr(self.disabled) }.store(value as u8, Ordering::Release);
    }
}

impl Drop for FeedWriter {
    fn drop(&mut self) {
        if !self.disabled.is_null() {
            self.set_disabled(true);
        }
        if !self.prediction.is_null() {
            self.write_motion(&MotionState::default());
        }
        self.segments.clear();
        for path in &self.files {
            let _ = fs::remove_file(path);
        }
    }
}

/// One read of the feed. Quaternions are xyzw; `orientation` is normalised.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FeedSample {
    pub orientation: [f32; 4],
    pub device_ms: f32,
    /// q1 and q2 as published, not normalised.
    pub older: [[f32; 4]; 2],
    pub older_ms: [f32; 2],
}

/// What `xr::quat_normalized` accepts.
fn normalized(q: [f32; 4]) -> Option<[f32; 4]> {
    let n = (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
    (n.is_finite() && n >= 1e-6).then(|| [q[0] / n, q[1] / n, q[2] / n, q[3] / n])
}

/// A reader of a feed someone else writes. For one thread.
pub struct FeedReader {
    pose: *const f32,
    mutex: *mut libc::pthread_mutex_t,
    disabled: *mut u8,
    fov: Option<*const f32>,
    look_ahead: Option<*const f32>,
    prediction: Option<*const PredictionWire>,
    vsync: Option<*const u32>,
    _segments: Vec<Segment>,
    /// Newest valid sample read: served again while the writer holds the lock.
    last: Option<FeedSample>,
    /// After two 2 ms waits in a row time out (a stopped writer, not a busy one), reads only try
    /// the lock until retry_ns, then wait once more; while the holder never lets go the gap
    /// doubles, 50 ms up to 1 s.
    retry_ns: u64,
    backoff_ns: u64,
    timeouts: u32,
    /// Reads that found the lock held and blocked on it.
    waits: u64,
}

/// What one try of the mutex feed gave (`xr::PoseFeed::Impl::read_mutex`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MutexRead {
    /// A new valid sample.
    Sample(FeedSample),
    /// The writer holds the lock, or died holding it: serve the newest sample again.
    Again,
    /// An invalid sample, or a lock that is not recoverable.
    Nothing,
}

// SAFETY: as for FeedWriter.
unsafe impl Send for FeedReader {}

fn attach_existing(
    path: &str,
    min_size: usize,
    writable: bool,
    segments: &mut Vec<Segment>,
) -> Result<*mut c_void, FeedError> {
    if fs::metadata(path).is_err() {
        return fail(format!("feed absent: {path}"));
    }
    let invalid = || FeedError(format!("feed segment invalid: {path}"));
    let key = ftok(&c_path(path)?).ok_or_else(invalid)?;
    // SAFETY: plain system calls; info is a valid out pointer.
    let id = unsafe { libc::shmget(key, 0, 0) };
    let mut info: libc::shmid_ds = unsafe { std::mem::zeroed() };
    if id < 0
        || unsafe { libc::shmctl(id, libc::IPC_STAT, &mut info) } != 0
        || info.shm_segsz < min_size
    {
        return Err(invalid());
    }
    let segment = shmat(id, if writable { 0 } else { libc::SHM_RDONLY }, false, path)
        .map_err(|_| FeedError(format!("feed attach failed: {path}")))?;
    let addr = segment.addr;
    segments.push(segment);
    Ok(addr)
}

impl FeedReader {
    /// Attaches to the feed at `prefix` (see [`DEFAULT_PREFIX`]). FOV, look-ahead and the motion
    /// channel are optional.
    pub fn open(prefix: &str) -> Result<Self, FeedError> {
        let mut segments = Vec::new();
        let pose = attach_existing(&format!("{prefix}{POSE}"), POSE_BYTES, false, &mut segments)?;
        // A process-shared mutex must be writable to lock; the pose itself is never written.
        let mutex = attach_existing(
            &format!("{prefix}{MUTEX}"),
            size_of::<libc::pthread_mutex_t>(),
            true,
            &mut segments,
        )?;
        let disabled = attach_existing(
            &format!("{prefix}{DISABLED}"),
            size_of::<bool>(),
            false,
            &mut segments,
        )?;
        let fov = attach_existing(
            &format!("{prefix}{FOV}"),
            size_of::<f32>(),
            false,
            &mut segments,
        )
        .ok();
        let look_ahead = attach_existing(
            &format!("{prefix}{LOOK_AHEAD}"),
            LOOK_AHEAD_BYTES,
            false,
            &mut segments,
        )
        .ok();
        let prediction = attach_existing(
            &format!("{prefix}{PREDICTION_IPC_NAME}"),
            size_of::<PredictionWire>(),
            false,
            &mut segments,
        )
        .ok();
        let vsync = attach_existing(
            &format!("{prefix}{VSYNC}"),
            VSYNC_BYTES,
            false,
            &mut segments,
        )
        .ok();
        Ok(FeedReader {
            pose: pose.cast(),
            mutex: mutex.cast(),
            disabled: disabled.cast(),
            fov: fov.map(|p| p.cast_const().cast()),
            look_ahead: look_ahead.map(|p| p.cast_const().cast()),
            prediction: prediction.map(|p| p.cast_const().cast()),
            vsync: vsync.map(|p| p.cast_const().cast()),
            _segments: segments,
            last: None,
            retry_ns: 0,
            backoff_ns: 0,
            timeouts: 0,
            waits: 0,
        })
    }

    /// Reads that found the lock held and blocked on it for up to 2 ms.
    pub fn waits(&self) -> u64 {
        self.waits
    }

    /// One consistent copy of the pose_vsync words 1..16 (the magic, then the 14 payload words), as
    /// `xr::PoseFeed::vsync` takes them; the caller checks and decodes them. `None`: no segment, or
    /// the writer was mid-write on each of 4 tries (bounded: a frame must never spin on a stopped
    /// writer).
    pub fn vsync_words(&self) -> Option<[u32; 15]> {
        let w = self.vsync?;
        // SAFETY: 16 aligned words in an attached segment; the writer stores them atomically.
        let word = |i: usize, order: Ordering| {
            unsafe { AtomicU32::from_ptr(w.add(i).cast_mut()) }.load(order)
        };
        for _ in 0..4 {
            let before = word(0, Ordering::Acquire);
            if before & 1 != 0 {
                continue;
            }
            let mut v = [0u32; 15];
            for i in 0..14 {
                v[1 + i] = word(2 + i, Ordering::Relaxed);
            }
            v[0] = word(1, Ordering::Relaxed);
            fence(Ordering::Acquire);
            if word(0, Ordering::Relaxed) != before {
                continue;
            }
            return Some(v);
        }
        None
    }

    /// The published diagonal FOV in degrees, if sane (10..=120).
    pub fn display_fov(&self) -> Option<f32> {
        // SAFETY: a float in an attached segment.
        let v = unsafe { self.fov?.read_volatile() };
        (v.is_finite() && (10.0..=120.0).contains(&v)).then_some(v)
    }

    /// The newest motion state, as `xr::MotionFeed::read` sees it before its own checks. `None`:
    /// no channel (XRLinuxDriver) or nothing valid on it.
    pub fn motion(&self) -> Option<MotionState> {
        // SAFETY: a read-only, page-aligned segment of PredictionWire's size; its reads are atomic
        // loads only.
        unsafe { &*self.prediction? }.read()
    }

    /// The published look-ahead config, raw.
    pub fn look_ahead_cfg(&self) -> Option<[f32; 4]> {
        let p = self.look_ahead?;
        // SAFETY: four floats in an attached segment.
        Some(std::array::from_fn(|i| unsafe { p.add(i).read_volatile() }))
    }

    /// Whether the driver has disabled the feed. When it has, the sample `read` serves while the
    /// writer holds the lock is forgotten too, as `read` does.
    pub fn disabled(&mut self) -> bool {
        // SAFETY: a byte in an attached segment; the writer stores it atomically.
        let off = unsafe { AtomicU8::from_ptr(self.disabled) }.load(Ordering::Acquire) != 0;
        if off {
            self.last = None;
        }
        off
    }

    /// The newest sample. `None` while disabled, for an invalid sample, or when the lock is not
    /// recoverable. While the writer holds the lock, the previous sample again: its device time
    /// is unchanged, so it ages and is predicted like any other (no sample would leave the frame
    /// unpredicted).
    pub fn read(&mut self) -> Option<FeedSample> {
        if self.disabled() {
            return None;
        }
        match self.read_mutex() {
            MutexRead::Sample(sample) => {
                self.last = Some(sample);
                self.last
            }
            MutexRead::Again => self.last,
            MutexRead::Nothing => None,
        }
    }

    /// One try of the lock, without the disabled check or the served-again sample of [`Self::read`]:
    /// for a caller that keeps its own newest sample (`xr::PoseFeed`, whose seqlock samples are served
    /// again too).
    pub fn read_mutex(&mut self) -> MutexRead {
        const WAIT_NS: u64 = 2_000_000;
        const FIRST_BACKOFF_NS: u64 = 50_000_000;
        const MAX_BACKOFF_NS: u64 = 1_000_000_000;
        let mut rc = clocklock(self.mutex, None);
        if rc == libc::ETIMEDOUT {
            let now = monotonic_ns();
            if self.retry_ns != 0 && now < self.retry_ns {
                return MutexRead::Again; // backing off: no waiting
            }
            self.waits += 1;
            rc = clocklock(self.mutex, Some(now + WAIT_NS));
            if rc == libc::ETIMEDOUT {
                self.timeouts += 1;
                if self.timeouts >= 2 {
                    self.backoff_ns = if self.retry_ns != 0 {
                        (2 * self.backoff_ns).min(MAX_BACKOFF_NS)
                    } else {
                        FIRST_BACKOFF_NS
                    };
                    self.retry_ns = monotonic_ns() + self.backoff_ns;
                }
                return MutexRead::Again;
            }
        }
        if rc == libc::EOWNERDEAD {
            // The writer died mid-write: this thread owns the lock. Make it consistent and release
            // it, or every later read (and a restarted writer) would wait on a lock nobody frees.
            // This read gets the last good sample, not the possibly half-written one.
            // SAFETY: this thread owns the mutex.
            unsafe {
                libc::pthread_mutex_consistent(self.mutex);
                libc::pthread_mutex_unlock(self.mutex);
            }
            self.retry_ns = 0;
            self.timeouts = 0;
            return MutexRead::Again;
        }
        if rc != 0 {
            return MutexRead::Nothing; // ENOTRECOVERABLE (lock word released by glibc) or not a mutex
        }
        self.retry_ns = 0;
        self.timeouts = 0;
        let mut v = [0f32; 16];
        // SAFETY: 16 floats guarded by the mutex this thread holds.
        unsafe {
            ptr::copy_nonoverlapping(self.pose, v.as_mut_ptr(), 16);
            libc::pthread_mutex_unlock(self.mutex);
        }
        let Some(orientation) = normalized([v[0], v[1], v[2], v[3]]) else {
            return MutexRead::Nothing;
        };
        if !v[12].is_finite() {
            return MutexRead::Nothing;
        }
        MutexRead::Sample(FeedSample {
            orientation,
            device_ms: v[12],
            older: [[v[4], v[5], v[6], v[7]], [v[8], v[9], v[10], v[11]]],
            older_ms: [v[13], v[14]],
        })
    }
}
