//! The run's watchdog: the renderer's heartbeat, the guard worker, the bounded sends and the renderer's launch.
//!
//! A port of `tools/native_spike/supervisor.py` (`Supervisor` and `_launch`). The timings are the Python ones and the
//! fault tests in `tests/supervisor_faults.rs` mirror `tests/native_spike/test_supervisor.py`:
//!
//! - No `H` on the heartbeat socket for [`HEARTBEAT_TIMEOUT`] ends the run (`heartbeat-timeout`). Any other byte ends it
//!   at once (`heartbeat-invalid`), and so does the renderer closing its end (`renderer-closed`).
//! - The guard runs in its own process, so a hung check never stalls the heartbeat or the reaping. It writes one byte per
//!   check: `V` (valid) every [`GUARD_PERIOD`], or `F` and exit. No verdict within [`GUARD_DEADLINE`] ends the run
//!   (`guard-timeout`); `F`, a crash or a closed pipe ends it at once (`guard-failed`). The guard's deadline never
//!   extends the heartbeat's, and the reverse.
//! - Stopping sends SIGTERM, then SIGKILL after the grace. Recovery runs once the renderer has been reaped.
//!
//! Not here yet (later phase 6 tasks): the run control socket, Hyprland events, the overview pointer's `poll`, the status
//! dict's parsing (each status line goes to a caller's handler), the journal recovery and the start-up marks.

// The tracking side: the driver wrappers, the Luma crash watch and the USB-drop hold.
pub mod luma_watch;
pub mod pro2;
pub mod survive;
pub mod tracking;

use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, BorrowedFd, OwnedFd, RawFd};
use std::os::unix::net::{UnixDatagram, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

/// Two sequential IPC requests, each bounded at two seconds, plus scheduling allowance. Separate from (and never
/// extending) the heartbeat's two seconds.
pub const GUARD_DEADLINE: Duration = Duration::from_millis(4250);
/// The guard worker's pause between two checks.
pub const GUARD_PERIOD: Duration = Duration::from_millis(500);
/// The longest gap between two heartbeats.
pub const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(2);
/// SIGTERM to SIGKILL.
pub const DEFAULT_GRACE: Duration = Duration::from_millis(500);
/// The loop's longest wait between two passes.
pub const POLL_INTERVAL: Duration = Duration::from_millis(50);
/// What does not fit the socket buffer at once gets this long.
pub const SEND_TIMEOUT: Duration = Duration::from_millis(250);
/// One status line from the renderer, newline included; a longer one is dropped whole.
pub const STATUS_LINE_LIMIT: usize = 16384;
/// The run's managed outputs are `XR-1` to `XR-<MAX_DESKTOPS>` (`geometry.MAX_DESKTOPS`).
pub const MAX_DESKTOPS: usize = 4;

/// Why the supervisor ended the renderer, for the run's result (`stop_reason`; `incident_detect` reads these names).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StopReason {
    HeartbeatTimeout,
    HeartbeatInvalid,
    RendererClosed,
    GuardTimeout,
    GuardFailed,
    TrackingTimeout,
    SendFailed,
    Interrupted,
}

impl StopReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::HeartbeatTimeout => "heartbeat-timeout",
            Self::HeartbeatInvalid => "heartbeat-invalid",
            Self::RendererClosed => "renderer-closed",
            Self::GuardTimeout => "guard-timeout",
            Self::GuardFailed => "guard-failed",
            Self::TrackingTimeout => "tracking-timeout",
            Self::SendFailed => "send-failed",
            Self::Interrupted => "interrupted",
        }
    }
}

/// The renderer process as the watchdog sees it: `std::process::Child`, or a stand-in in tests.
pub trait Renderer {
    /// The exit status once it has been reaped, `None` while it runs.
    fn poll(&mut self) -> Option<ExitStatus>;
    fn terminate(&mut self);
    fn kill(&mut self);
}

impl Renderer for Child {
    fn poll(&mut self) -> Option<ExitStatus> {
        self.try_wait().ok().flatten()
    }
    fn terminate(&mut self) {
        // Only called while poll() says it runs, so the pid is still ours (not reaped, not reused).
        let pid = self.id() as libc::pid_t;
        unsafe { libc::kill(pid, libc::SIGTERM) };
    }
    fn kill(&mut self) {
        let _ = Child::kill(self);
    }
}

/// The worker side of a guard: check, write `V`, wait [`GUARD_PERIOD`], again. The first failed check writes `F` and
/// returns, with its reason on stderr: the verdict is one byte, and without the reason `guard-failed` says nothing.
pub fn guard_worker<E: std::fmt::Display>(
    mut check: impl FnMut() -> Result<(), E>,
    out: &mut impl Write,
) {
    loop {
        if let Err(error) = check() {
            eprintln!("xr-workspace: run guard: {error}");
            let _ = out.write_all(b"F").and_then(|()| out.flush());
            return;
        }
        if out.write_all(b"V").and_then(|()| out.flush()).is_err() {
            return;
        }
        std::thread::sleep(GUARD_PERIOD);
    }
}

/// One running guard worker and the read end of its verdict pipe (its stdout).
struct GuardWorker {
    child: Child,
    verdicts: ChildStdout,
}

impl GuardWorker {
    /// The worker gets none of the supervisor's descriptors (renderer, control, Wayland): only stdin (null), its stdout
    /// pipe and the run's stderr.
    fn spawn(command: &mut Command) -> io::Result<Self> {
        command.stdin(Stdio::null()).stdout(Stdio::piped());
        unsafe { command.pre_exec(|| close_inherited(&[])) };
        let mut child = command.spawn()?;
        let verdicts = child.stdout.take().expect("piped stdout");
        Ok(Self { child, verdicts })
    }

    /// Kill the exact worker, reap it and close its pipe. No orphans.
    fn stop(mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Bounded line framing of the renderer's status datagrams (`Supervisor.status_bytes`).
#[derive(Default)]
struct StatusLines {
    buffer: Vec<u8>,
    discard: bool,
}

impl StatusLines {
    fn feed(&mut self, data: &[u8], mut line: impl FnMut(&[u8])) {
        for chunk in data.split_inclusive(|&b| b == b'\n') {
            if !self.discard {
                self.buffer.extend_from_slice(chunk);
            }
            if self.buffer.len() > STATUS_LINE_LIMIT {
                self.buffer.clear();
                self.discard = true;
            }
            if !chunk.ends_with(b"\n") {
                continue;
            }
            if !self.discard {
                line(&self.buffer);
            }
            self.buffer.clear();
            self.discard = false;
        }
    }
}

/// A status line's handler. It returns the line's `tracking` value when it had one.
pub type StatusHandler = Box<dyn FnMut(&[u8]) -> Option<bool>>;

/// The watchdog of one renderer. `recovery` runs once the renderer has been reaped and gets its exit status.
pub struct Supervisor<C: Renderer, R> {
    child: C,
    recovery: Box<dyn FnMut(Option<ExitStatus>) -> R>,
    pub last_heartbeat: Instant,
    grace: Duration,
    stopping_at: Option<Instant>,
    finished: bool,
    kill_sent: bool,
    result: Option<R>,
    /// Why the supervisor ended the renderer; `None` if it ended by itself.
    pub stop_reason: Option<StopReason>,
    /// A USB drop stops the pose feed and leaves the video up. Tracking gone this long ends the run; `None` (no head
    /// tracking): never. Only after it has worked: a run that never had it has lost nothing.
    tracking_timeout: Option<Duration>,
    tracked: bool,
    tracking_lost_at: Option<Instant>,
    guard: Option<Command>,
    worker: Option<GuardWorker>,
    guard_deadline: Instant,
    pub guard_paused: bool,
    pub guard_restarts: u32,
    status_lines: StatusLines,
    on_status: Option<StatusHandler>,
}

impl<C: Renderer, R> Supervisor<C, R> {
    pub fn new(
        child: C,
        recovery: impl FnMut(Option<ExitStatus>) -> R + 'static,
        now: Instant,
    ) -> Self {
        Self {
            child,
            recovery: Box::new(recovery),
            last_heartbeat: now,
            grace: DEFAULT_GRACE,
            stopping_at: None,
            finished: false,
            kill_sent: false,
            result: None,
            stop_reason: None,
            tracking_timeout: None,
            tracked: false,
            tracking_lost_at: None,
            guard: None,
            worker: None,
            guard_deadline: now,
            guard_paused: false,
            guard_restarts: 0,
            status_lines: StatusLines::default(),
            on_status: None,
        }
    }

    pub fn with_grace(mut self, grace: Duration) -> Self {
        self.grace = grace;
        self
    }

    /// The guard worker's command (see [`guard_worker`] for what it writes). Started by [`Supervisor::run`].
    pub fn with_guard(mut self, guard: Command) -> Self {
        self.guard = Some(guard);
        self
    }

    pub fn with_tracking_timeout(mut self, timeout: Option<Duration>) -> Self {
        self.tracking_timeout = timeout;
        self
    }

    pub fn with_status_handler(mut self, handler: StatusHandler) -> Self {
        self.on_status = Some(handler);
        self
    }

    pub fn finished(&self) -> bool {
        self.finished
    }

    /// The recovery's result once the run is over.
    pub fn take_result(&mut self) -> Option<R> {
        self.result.take()
    }

    pub fn heartbeat(&mut self, now: Instant) {
        if self.stopping_at.is_none() && now >= self.last_heartbeat {
            self.last_heartbeat = now;
        }
    }

    /// Start stopping the renderer. The first reason stands; a renderer already gone was stopped by no one.
    pub fn disconnect(&mut self, now: Instant, reason: Option<StopReason>) {
        if self.finished || self.stopping_at.is_some() {
            return;
        }
        self.stopping_at = Some(now);
        if self.child.poll().is_none() {
            self.stop_reason = reason;
            self.child.terminate();
        }
    }

    pub fn tick(&mut self, now: Instant) {
        if self.finished {
            return;
        }
        if self.child.poll().is_none() {
            if self.stopping_at.is_none()
                && now.saturating_duration_since(self.last_heartbeat) >= HEARTBEAT_TIMEOUT
            {
                self.disconnect(now, Some(StopReason::HeartbeatTimeout));
            }
            if let (Some(timeout), Some(lost)) = (self.tracking_timeout, self.tracking_lost_at)
                && now.saturating_duration_since(lost) >= timeout
            {
                self.disconnect(now, Some(StopReason::TrackingTimeout));
            }
            if let Some(stopping) = self.stopping_at
                && !self.kill_sent
                && now.saturating_duration_since(stopping) >= self.grace
            {
                self.kill_sent = true;
                self.child.kill();
            }
            if self.child.poll().is_none() {
                return;
            }
        }
        self.result = Some((self.recovery)(self.child.poll()));
        self.finished = true;
    }

    /// The renderer's `tracking` status value (`Supervisor._tracking_status`).
    pub fn tracking_status(&mut self, tracking: bool, now: Instant) {
        if tracking {
            self.tracked = true;
            self.tracking_lost_at = None;
        } else if self.tracked && self.tracking_lost_at.is_none() {
            self.tracking_lost_at = Some(now);
        }
    }

    /// Bytes from the status socket: complete lines go to the status handler.
    pub fn status_bytes(&mut self, data: &[u8], now: Instant) {
        let mut tracking = Vec::new();
        let handler = &mut self.on_status;
        self.status_lines.feed(data, |line| {
            if let Some(handler) = handler.as_mut()
                && let Some(value) = handler(line)
            {
                tracking.push(value);
            }
        });
        for value in tracking {
            self.tracking_status(value, now);
        }
    }

    // --- the guard worker -------------------------------------------------------------------------------------------

    /// Own exactly one worker and one verdict pipe for the current guard.
    fn start_guard(&mut self) -> io::Result<()> {
        if self.child.poll().is_some() || self.stopping_at.is_some() {
            return Ok(());
        }
        let Some(command) = self.guard.as_mut() else {
            return Ok(());
        };
        self.guard_deadline = Instant::now() + GUARD_DEADLINE;
        self.worker = Some(GuardWorker::spawn(command)?);
        Ok(())
    }

    fn stop_guard(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.stop();
        }
    }

    /// A desktop change starts: the guard's output list is about to stop being true, so its worker goes.
    pub fn pause_guard(&mut self) {
        self.stop_guard();
        self.guard_paused = true;
    }

    /// The change is done: a new guard for the run's outputs as they are now (`None` keeps none).
    pub fn replace_guard(&mut self, guard: Option<Command>) -> io::Result<()> {
        self.stop_guard();
        self.guard_paused = false;
        self.guard = guard;
        self.guard_restarts += 1;
        self.start_guard()
    }

    fn check_guard(&mut self, now: Instant) {
        if self.stopping_at.is_some() {
            return;
        }
        let Some(worker) = self.worker.as_mut() else {
            return;
        };
        if now >= self.guard_deadline {
            self.disconnect(now, Some(StopReason::GuardTimeout));
        } else if !matches!(worker.child.try_wait(), Ok(None)) {
            self.disconnect(now, Some(StopReason::GuardFailed));
        } else if readable(worker.verdicts.as_raw_fd()) {
            let mut verdict = [0u8; 1];
            let valid = matches!(worker.verdicts.read(&mut verdict), Ok(1)) && verdict == *b"V";
            if valid {
                self.guard_deadline = now + GUARD_PERIOD + GUARD_DEADLINE;
            } else {
                self.disconnect(now, Some(StopReason::GuardFailed));
            }
        }
    }

    // --- sending to the renderer ------------------------------------------------------------------------------------

    /// One frame to the renderer, its descriptors with the first byte (SCM_RIGHTS). Our copies of `fds` are closed
    /// afterwards, sent or not. A send that fails, or does not finish within [`SEND_TIMEOUT`], stops the run.
    pub fn send(&mut self, socket: &UnixStream, frame: &[u8], fds: Vec<OwnedFd>) {
        if frame.is_empty() || self.stopping_at.is_some() {
            return;
        }
        if send_frame(socket, frame, &fds).is_err() {
            self.disconnect(Instant::now(), Some(StopReason::SendFailed));
        }
    }

    // --- the loop ---------------------------------------------------------------------------------------------------

    /// Run until the renderer is reaped and recovered. EOF on the heartbeat socket means disconnect; only `H` is
    /// valid. `interrupt` set (Stop, SIGINT) stops the renderer and still waits for its reaping. The guard worker is
    /// gone when this returns, whatever happened.
    pub fn run(
        &mut self,
        heartbeat: &UnixStream,
        status: Option<&UnixDatagram>,
        interrupt: &AtomicBool,
        interval: Duration,
    ) -> io::Result<R> {
        if interval.is_zero() || interval > Duration::from_millis(100) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid poll interval",
            ));
        }
        let outcome = self.run_inner(heartbeat, status, interrupt, interval);
        // Reaping the renderer is never delayed by waiting on IPC. Whatever ended the loop, the worker is killed and
        // reaped and its pipe closed.
        self.stop_guard();
        outcome
    }

    fn run_inner(
        &mut self,
        heartbeat: &UnixStream,
        status: Option<&UnixDatagram>,
        interrupt: &AtomicBool,
        interval: Duration,
    ) -> io::Result<R> {
        self.start_guard()?;
        if let Some(status) = status {
            status.set_nonblocking(true)?;
        }
        let mut heartbeat_open = true;
        let mut status_open = status.is_some();
        let timeout = interval.as_millis() as libc::c_int;
        while !self.finished {
            let now = Instant::now();
            if interrupt.load(Ordering::Relaxed) {
                self.disconnect(now, Some(StopReason::Interrupted));
            }
            // Expiry precedes reading queued heartbeats: delayed bytes cannot revive an expired watchdog.
            self.tick(now);
            if self.finished {
                break;
            }
            self.check_guard(now);
            let mut fds = Vec::with_capacity(2);
            if heartbeat_open {
                fds.push(libc::pollfd {
                    fd: heartbeat.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                });
            }
            if let (true, Some(status)) = (status_open, status) {
                fds.push(libc::pollfd {
                    fd: status.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                });
            }
            if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout) } < 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            for ready in fds.iter().filter(|p| p.revents != 0) {
                let now = Instant::now();
                self.tick(now);
                if self.finished {
                    break;
                }
                if let Some(status) = status
                    && ready.fd == status.as_raw_fd()
                {
                    let mut data = [0u8; STATUS_LINE_LIMIT + 1];
                    match status.recv(&mut data) {
                        Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
                        Ok(n) if n > 0 => self.status_bytes(&data[..n], now),
                        _ => status_open = false,
                    }
                    continue;
                }
                let mut data = [0u8; 256];
                // A reset (renderer gone with unread frames) is a disconnect.
                let n = (&*heartbeat).read(&mut data).unwrap_or(0);
                if n > 0 && data[..n].iter().all(|&b| b == b'H') {
                    self.heartbeat(now);
                } else {
                    let reason = if n > 0 {
                        StopReason::HeartbeatInvalid
                    } else {
                        StopReason::RendererClosed
                    };
                    self.disconnect(now, Some(reason));
                    heartbeat_open = false;
                }
            }
            self.tick(Instant::now());
        }
        Ok(self
            .result
            .take()
            .expect("a finished run has its recovery's result"))
    }

    /// Stop the renderer (no reason) and wait for its reaping and recovery, 50 ms at a time.
    fn reap(&mut self) {
        if !self.finished && self.child.poll().is_none() {
            self.disconnect(Instant::now(), None);
        }
        while !self.finished {
            self.tick(Instant::now());
            if !self.finished {
                std::thread::sleep(POLL_INTERVAL);
            }
        }
    }
}

fn readable(fd: RawFd) -> bool {
    let mut pollfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    unsafe { libc::poll(&mut pollfd, 1, 0) > 0 && pollfd.revents != 0 }
}

/// Send `frame` with `fds` attached to its first byte. A frame without descriptors first gets one non-blocking send:
/// it almost always fits the socket buffer. Only what does not fit takes the bounded blocking send.
fn send_frame(socket: &UnixStream, frame: &[u8], fds: &[OwnedFd]) -> io::Result<()> {
    let mut sent = 0;
    if fds.is_empty() {
        let n = unsafe {
            libc::send(
                socket.as_raw_fd(),
                frame.as_ptr().cast(),
                frame.len(),
                libc::MSG_DONTWAIT | libc::MSG_NOSIGNAL,
            )
        };
        if n < 0 {
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::WouldBlock {
                return Err(error);
            }
        } else {
            sent = n as usize;
        }
        if sent == frame.len() {
            return Ok(());
        }
    }
    socket.set_write_timeout(Some(SEND_TIMEOUT))?;
    let outcome = (|| {
        if !fds.is_empty() {
            sent = send_with_fds(socket, frame, fds)?;
        }
        (&*socket).write_all(&frame[sent..])
    })();
    let reset = socket.set_write_timeout(None);
    outcome.and(reset)
}

fn send_with_fds(socket: &UnixStream, data: &[u8], fds: &[OwnedFd]) -> io::Result<usize> {
    let raw: Vec<RawFd> = fds.iter().map(AsRawFd::as_raw_fd).collect();
    let payload = std::mem::size_of_val(raw.as_slice()) as u32;
    let space = unsafe { libc::CMSG_SPACE(payload) } as usize;
    // u64 words keep the control buffer aligned for cmsghdr.
    let mut control = vec![0u64; space.div_ceil(8)];
    let mut iov = libc::iovec {
        iov_base: data.as_ptr() as *mut _,
        iov_len: data.len(),
    };
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = space as _;
    unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        (*header).cmsg_level = libc::SOL_SOCKET;
        (*header).cmsg_type = libc::SCM_RIGHTS;
        (*header).cmsg_len = libc::CMSG_LEN(payload) as _;
        std::ptr::copy_nonoverlapping(
            raw.as_ptr(),
            libc::CMSG_DATA(header).cast::<RawFd>(),
            raw.len(),
        );
        let n = libc::sendmsg(socket.as_raw_fd(), &message, libc::MSG_NOSIGNAL);
        if n < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(n as usize)
        }
    }
}

/// In a child between fork and exec: everything from 3 up closes on exec (Python's `close_fds=True`) except `keep`,
/// which stays open at the same numbers (`pass_fds`). Only async-signal-safe calls.
fn close_inherited(keep: &[RawFd]) -> io::Result<()> {
    if unsafe {
        libc::close_range(
            3,
            libc::c_uint::MAX,
            libc::CLOSE_RANGE_CLOEXEC as libc::c_int,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    for &fd in keep {
        if unsafe { libc::fcntl(fd, libc::F_SETFD, 0) } != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

// --- validation (ipc.token, validate_pattern_duration, capture_arguments) ---------------------------------------------

fn invalid(detail: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, detail.to_owned())
}

/// `ipc.TOKEN`: `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. Nothing in one needs JSON escaping.
pub fn token(value: &str) -> io::Result<&str> {
    let bytes = value.as_bytes();
    let ok = (1..=128).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes[1..]
            .iter()
            .all(|&b| b.is_ascii_alphanumeric() || b"_.:-".contains(&b));
    if ok {
        Ok(value)
    } else {
        Err(invalid("invalid identifier"))
    }
}

/// The renderer's argv for capture of the run's own managed outputs only (`XR-1`..`XR-4`), never `output`.
pub fn capture_arguments(names: Option<&[&str]>, output: &str) -> io::Result<Vec<String>> {
    let Some(names) = names else {
        return Ok(Vec::new());
    };
    let managed = |n: &&str| {
        let b = n.as_bytes();
        b.len() == 4
            && b.starts_with(b"XR-")
            && (b'1'..=b'0' + MAX_DESKTOPS as u8).contains(&b[3])
            && *n != output
    };
    let mut sorted: Vec<&str> = names.to_vec();
    sorted.sort_unstable();
    sorted.dedup();
    if !(1..=MAX_DESKTOPS).contains(&names.len())
        || sorted.len() != names.len()
        || !names.iter().all(managed)
    {
        return Err(invalid(
            "capture is limited to the run's own managed outputs",
        ));
    }
    Ok(vec!["--capture".into(), sorted.join(",")])
}

/// Python's `str(float)` for the values the renderer's arguments carry: `46.0`, `0.5`, `12.25`.
fn python_float(value: f64) -> String {
    if value.fract() == 0.0 && value.abs() < 1e16 {
        format!("{value:.1}")
    } else {
        format!("{value}")
    }
}

// --- the launch -------------------------------------------------------------------------------------------------------

/// The Pro 2's test pose feed (`pro2_runtime` scope), checked healthy by the caller.
pub struct Pro2Pose {
    pub prefix: String,
    pub projection: f64,
    pub smooth_ms: f64,
    pub level_gravity: bool,
}

/// The Wayland socket's peer: the compositor the Hyprland client verified.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Peer {
    pub pid: i32,
    pub uid: u32,
    pub gid: u32,
}

impl Peer {
    pub fn of(socket: &UnixStream) -> io::Result<Self> {
        let mut cred = libc::ucred {
            pid: 0,
            uid: 0,
            gid: 0,
        };
        let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
        let rc = unsafe {
            libc::getsockopt(
                socket.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                (&mut cred as *mut libc::ucred).cast(),
                &mut len,
            )
        };
        if rc != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            pid: cred.pid,
            uid: cred.uid,
            gid: cred.gid,
        })
    }
}

/// Everything one renderer launch needs (`supervisor._launch`). Authorisation (consent, ownership, the physical pair
/// check) stays with the callers.
pub struct Launch<'a> {
    pub renderer: &'a Path,
    pub output: &'a str,
    pub run_id: &'a str,
    pub session: &'a str,
    /// The peer the Hyprland client verified; the Wayland socket must have the same one, and our UID.
    pub expected_peer: Peer,
    pub animate_pattern_ms: Option<u32>,
    pub capture: Option<&'a [&'a str]>,
    pub pose_feed: bool,
    pub pro2: Option<Pro2Pose>,
    /// The depth engine's shared memory and the continuity memfd, inherited at their own numbers.
    pub depth_fd: Option<BorrowedFd<'a>>,
    pub continuity_fd: Option<BorrowedFd<'a>>,
    pub guard: Option<Command>,
    pub grace: Duration,
    pub tracking_timeout: Option<Duration>,
    /// Sent after the start frame (saved spacing, overview).
    pub initial_frames: Vec<u8>,
    pub status_handler: Option<StatusHandler>,
}

impl<'a> Launch<'a> {
    pub fn new(
        renderer: &'a Path,
        output: &'a str,
        run_id: &'a str,
        session: &'a str,
        expected_peer: Peer,
    ) -> Self {
        Self {
            renderer,
            output,
            run_id,
            session,
            expected_peer,
            animate_pattern_ms: None,
            capture: None,
            pose_feed: false,
            pro2: None,
            depth_fd: None,
            continuity_fd: None,
            guard: None,
            grace: DEFAULT_GRACE,
            tracking_timeout: None,
            initial_frames: Vec::new(),
            status_handler: None,
        }
    }

    /// The renderer's arguments, in `_launch`'s order. Refuses what `_launch` refuses.
    fn arguments(
        &self,
        supervised: RawFd,
        wayland: RawFd,
        status: RawFd,
    ) -> io::Result<Vec<String>> {
        if let Some(ms) = self.animate_pattern_ms
            && !(1..=60000).contains(&ms)
        {
            return Err(invalid("animation duration must be integer 1..60000 ms"));
        }
        if self.capture.is_some() && self.animate_pattern_ms.is_some() {
            return Err(invalid("capture and generated animation are exclusive"));
        }
        let mut capture = capture_arguments(self.capture, self.output)?;
        if self.pose_feed {
            if capture.is_empty() {
                return Err(invalid("head tracking requires managed-output capture"));
            }
            capture.extend(["--pose-feed".into(), "xrld".into()]);
            if let Some(pro2) = &self.pro2 {
                capture.extend([
                    "--pro2-test-pose-prefix".into(),
                    pro2.prefix.clone(),
                    "--pro2-test-projection".into(),
                    python_float(pro2.projection),
                    "--pro2-test-smooth-ms".into(),
                    python_float(pro2.smooth_ms),
                ]);
                if pro2.level_gravity {
                    capture.extend(["--pro2-test-level-gravity".into(), "1".into()]);
                }
            }
        }
        token(self.output)?;
        token(self.run_id)?;
        token(self.session)?;
        let mut argv: Vec<String> = vec![
            "--supervised-fd".into(),
            supervised.to_string(),
            "--wayland-fd".into(),
            wayland.to_string(),
            "--output".into(),
            self.output.into(),
            "--run-id".into(),
            self.run_id.into(),
            "--session".into(),
            self.session.into(),
            "--status-fd".into(),
            status.to_string(),
        ];
        if let Some(fd) = self.continuity_fd {
            argv.extend(["--continuity-fd".into(), fd.as_raw_fd().to_string()]);
        }
        if let Some(fd) = self.depth_fd {
            argv.extend(["--depth-fd".into(), fd.as_raw_fd().to_string()]);
        }
        if let Some(ms) = self.animate_pattern_ms {
            argv.extend(["--animate-pattern-ms".into(), ms.to_string()]);
        }
        argv.extend(capture);
        Ok(argv)
    }
}

/// The start frame, byte for byte as Python's `json.dumps` writes it (`fixtures/control_frames.json`, op `start`).
/// `run_id` and `session` are tokens, so nothing needs escaping.
pub fn start_frame(run_id: &str, session: &str) -> Vec<u8> {
    format!(
        "{{\"schema\": 1, \"request_id\": 1, \"run_id\": \"{run_id}\", \"session\": \"{session}\", \"op\": \"start\", \"payload\": {{}}}}\n"
    )
    .into_bytes()
}

/// What one supervised renderer run ended with.
#[derive(Debug)]
pub struct Outcome<R> {
    pub result: R,
    pub stop_reason: Option<StopReason>,
}

/// Start the renderer on its capability sockets and supervise it until it is reaped and recovered. Consumes the
/// Wayland socket even on refusal: no parent reference may keep compositor surfaces after the renderer's exit.
pub fn launch<R>(
    mut spec: Launch<'_>,
    wayland: UnixStream,
    recovery: impl FnMut(Option<ExitStatus>) -> R + 'static,
    interrupt: &AtomicBool,
) -> io::Result<Outcome<R>> {
    let (parent, child_socket) = UnixStream::pair()?;
    let (status_parent, status_child) = UnixDatagram::pair()?;
    let argv = spec.arguments(
        child_socket.as_raw_fd(),
        wayland.as_raw_fd(),
        status_child.as_raw_fd(),
    )?;
    let peer = Peer::of(&wayland)?;
    if peer.uid != unsafe { libc::getuid() } || peer != spec.expected_peer {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Wayland/IPC peer mismatch",
        ));
    }
    let mut keep = vec![
        child_socket.as_raw_fd(),
        wayland.as_raw_fd(),
        status_child.as_raw_fd(),
    ];
    keep.extend(spec.depth_fd.map(|fd| fd.as_raw_fd()));
    keep.extend(spec.continuity_fd.map(|fd| fd.as_raw_fd()));
    let mut command = Command::new(spec.renderer);
    // No renderer disk writes: GPU drivers would otherwise persist a shader cache.
    command
        .args(&argv)
        .stdin(Stdio::null())
        .env("MESA_SHADER_CACHE_DISABLE", "true");
    unsafe { command.pre_exec(move || close_inherited(&keep)) };
    let spawned = command.spawn();
    drop(wayland);
    drop(child_socket);
    drop(status_child);
    let child = spawned?;

    let mut watchdog = Supervisor::new(child, recovery, Instant::now())
        .with_grace(spec.grace)
        .with_tracking_timeout(spec.tracking_timeout);
    if let Some(guard) = spec.guard.take() {
        watchdog = watchdog.with_guard(guard);
    }
    if let Some(handler) = spec.status_handler.take() {
        watchdog = watchdog.with_status_handler(handler);
    }
    let mut first = start_frame(spec.run_id, spec.session);
    first.extend_from_slice(&spec.initial_frames);
    let sent = parent
        .set_write_timeout(Some(SEND_TIMEOUT))
        .and_then(|()| (&parent).write_all(&first))
        .and_then(|()| parent.set_write_timeout(None));
    if sent.is_err() {
        watchdog.disconnect(Instant::now(), Some(StopReason::SendFailed));
    }
    let outcome = watchdog.run(&parent, Some(&status_parent), interrupt, POLL_INTERVAL);
    // Also reap on loop errors.
    watchdog.reap();
    let stop_reason = watchdog.stop_reason;
    Ok(Outcome {
        result: outcome?,
        stop_reason,
    })
}
