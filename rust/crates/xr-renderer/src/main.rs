//! XR renderer on GLES and Wayland.
//!
//! So far the presentation shell: the supervised launch of `spike/src/main.cpp` with the generated
//! test pattern (static, or `--animate-pattern-ms`), the control stream (every op is decoded,
//! receipted and refused as the C++ does; only `stop` acts so far), the status samples and the
//! timing report. Capture, tracking and depth are refused at launch (exit 2) until they are ported.

mod metrics;
mod pattern;
mod shell;

use std::collections::{BTreeMap, HashSet};
use std::io::Write as _;
use std::os::fd::{AsFd, BorrowedFd, FromRawFd, OwnedFd};
use std::os::unix::net::UnixStream;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use rustix::event::{PollFd, PollFlags, Timespec, poll};
use rustix::io::Errno;
use rustix::net::{RecvFlags, SendFlags, recv, send};

use shell::{Presentation, monotonic_ms, monotonic_ms_f64};
use xr_renderer::ipc::{self, ControlStream, Descriptors, StatusExtras};
use xr_wire::control::{self, Message, Op, Payload, Refused, Video};
use xr_wire::status::{encode_applied, encode_refused};

const HELP: &str = "xr-renderer: native output-bound layer-shell renderer (no SDK/input).\n\
Launched only by the permission-gated Python supervisor with inherited capability sockets.\n\
Internal: --supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION\n\
Optional --status-fd FD: nonblocking measured status JSON, at most once per second.\n\
Optional presentation-time v1 feedback: bounded aggregate JSON on stderr at orderly exit/error.\n\
Optional --control-errors fatal, for tests: a bad control frame stops the renderer. Otherwise it is logged and dropped.\n\
Default static; optional --animate-pattern-ms 1..60000 (exact matching consent required).\n\
Not yet in this build: --pose-feed, --depth-fd, --continuity-fd. Control ops other than stop are receipted but not drawn.\n\
Generated SHM only; callback-paced redraw is not scanout or production GPU evidence.\n";

static STOPPING: AtomicBool = AtomicBool::new(false);

extern "C" fn stop(_: libc::c_int) {
    STOPPING.store(true, Ordering::Relaxed);
}

/// An inherited descriptor number: decimal, 3 or more.
fn number(value: &str) -> Option<i32> {
    if value.is_empty() || !value.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    value.parse::<i32>().ok().filter(|&n| n >= 3)
}

fn same_user(fd: BorrowedFd) -> bool {
    rustix::net::sockopt::socket_peercred(fd).is_ok_and(|c| c.uid == rustix::process::getuid())
}

/// The supervisor's start frame: one line of at most 16384 bytes, each byte within 250 ms.
fn read_start(control: BorrowedFd) -> Option<Vec<u8>> {
    let mut raw = Vec::new();
    while raw.len() < control::MAX_FRAME {
        let mut fds = [PollFd::new(&control, PollFlags::IN)];
        if poll(
            &mut fds,
            Some(&Timespec {
                tv_sec: 0,
                tv_nsec: 250_000_000,
            }),
        )
        .ok()?
            == 0
        {
            return None;
        }
        let mut c = [0u8];
        if recv(control, &mut c, RecvFlags::empty()).ok()?.0 != 1 {
            return None;
        }
        raw.push(c[0]);
        if c[0] == b'\n' {
            break;
        }
    }
    Some(raw)
}

fn send_line(fd: Option<BorrowedFd>, line: &str) {
    sent_line(fd, line);
}

/// Sends one datagram; false when it did not go out whole (the caller sends it again later).
fn sent_line(fd: Option<BorrowedFd>, line: &str) -> bool {
    let Some(fd) = fd else { return false };
    // Datagram socketpair: EAGAIN drops a whole line, never part of one.
    send(fd, line.as_bytes(), SendFlags::DONTWAIT | SendFlags::NOSIGNAL)
        .is_ok_and(|n| n == line.len())
}

/// What a delivered frame does in this build: `stop` stops; a `video` open takes its descriptor,
/// which is closed unread until the player is ported. Other ops are logged once each.
fn apply(m: &Message, descriptors: &mut Descriptors, unhandled: &mut HashSet<Op>) {
    match &m.payload {
        _ if m.op == Op::Stop => STOPPING.store(true, Ordering::Relaxed),
        Payload::Video(Video::Open { .. }) => match descriptors.take() {
            Some(_closed) => eprintln!("control: video open: no player in this build, file closed"),
            None => eprintln!("control: video open without a file descriptor"),
        },
        _ => {
            if unhandled.insert(m.op) {
                eprintln!("control: {} received; not drawn in this build", m.op.name());
            }
        }
    }
}

/// The journal line for a refused frame, bounded: a supervisor that keeps doing this must not fill
/// the journal. The frame is redacted to its protocol words.
fn refused(r: &Refused, refusals: &mut u64) {
    *refusals += 1;
    if *refusals <= 20 || *refusals % 1000 == 0 {
        let op = r.op.as_deref().unwrap_or("unreadable");
        eprintln!(
            "control: refused frame {} ({op}): {}: {} ({refusals} so far): {}",
            r.request_id, r.refusal.code, r.refusal.detail, r.frame
        );
    }
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.len() == 1 && argv[0] == "--help" {
        print!("{HELP}");
        return ExitCode::SUCCESS;
    }
    ExitCode::from(run(&argv))
}

fn run(argv: &[String]) -> u8 {
    if argv.len() < 10 || argv.len() > 30 || argv.len() % 2 != 0 {
        eprintln!("permission: supervised capability sockets required; see --help");
        return 2;
    }
    let mut args = BTreeMap::new();
    for pair in argv.chunks(2) {
        if args.insert(pair[0].as_str(), pair[1].as_str()).is_some() {
            return 2;
        }
    }
    const REQUIRED: [&str; 5] = [
        "--supervised-fd",
        "--wayland-fd",
        "--output",
        "--run-id",
        "--session",
    ];
    const OPTIONAL: [&str; 4] = ["--status-fd", "--control-errors", "--animate-pattern-ms", "--capture"];
    if !REQUIRED.iter().all(|k| args.contains_key(k))
        || !args
            .keys()
            .all(|k| REQUIRED.contains(k) || OPTIONAL.contains(k))
    {
        return 2;
    }
    let status = match args.get("--status-fd") {
        Some(v) => match number(v) {
            Some(n) => Some(n),
            None => return 2,
        },
        None => None,
    };
    if args.get("--control-errors").is_some_and(|v| *v != "fatal") {
        return 2;
    }
    let fatal = args.contains_key("--control-errors");
    let animation_ms = match args.get("--animate-pattern-ms") {
        Some(v) => match pattern::parse_pattern_duration(v) {
            Some(ms) => Some(ms),
            None => return 2,
        },
        None => None,
    };
    let captures = match args.get("--capture") {
        Some(v) => match xr_renderer::capture::parse_capture_list(v, args["--output"]) {
            Some(list) => list,
            None => return 2,
        },
        None => Vec::new(),
    };
    let (Some(control_fd), Some(wayland_fd)) = (
        number(args["--supervised-fd"]),
        number(args["--wayland-fd"]),
    ) else {
        return 2;
    };
    if control_fd == wayland_fd || status.is_some_and(|s| s == control_fd || s == wayland_fd) {
        return 2;
    }
    // SAFETY: the supervisor hands these descriptors over at exec for this process alone; each is
    // owned once here and checked to be a socket of this user before any use.
    let (control, wayland, status) = unsafe {
        (
            OwnedFd::from_raw_fd(control_fd),
            OwnedFd::from_raw_fd(wayland_fd),
            status.map(|fd| OwnedFd::from_raw_fd(fd)),
        )
    };
    if !same_user(control.as_fd())
        || !same_user(wayland.as_fd())
        || status.as_ref().is_some_and(|s| !same_user(s.as_fd()))
    {
        return 2;
    }
    let status = status.as_ref().map(AsFd::as_fd);
    // Startup is a validated start frame on a private inherited socket, never a public JSON grant.
    let Some(start) = read_start(control.as_fd()) else {
        return 2;
    };
    match control::decode(&start) {
        Ok(m)
            if m.op == Op::Start
                && m.run_id == args["--run-id"]
                && m.session == args["--session"] => {}
        _ => return 2,
    }
    // SAFETY: plain handlers that only store to an atomic.
    unsafe {
        libc::signal(libc::SIGTERM, stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, stop as *const () as libc::sighandler_t);
    }

    let mut result = 0;
    let mut presentation = match Presentation::open(UnixStream::from(wayland), args["--output"], &captures) {
        Ok(p) => Some(p),
        Err(e) => {
            eprintln!("{e}");
            result = 1;
            None
        }
    };
    if let (Some(p), Some(ms)) = (&mut presentation, animation_ms) {
        if let Err(e) = p.animate_pattern(ms) {
            eprintln!("{e}");
            result = 1;
        }
    }

    // Further supervisor frames on the same private socket, replay-protected. A bad one is dropped.
    let Some(mut stream) = ControlStream::new(args["--run-id"], args["--session"], fatal) else {
        return 2;
    };
    stream.start(&start);
    let mut descriptors = Descriptors::default();
    let mut unhandled = HashSet::new();
    let mut refusals = 0u64;
    let mut closed = String::from("EOF");
    let mut next_status = Instant::now();
    if let Some(p) = &mut presentation {
        while result == 0 && !STOPPING.load(Ordering::Relaxed) && !p.finished() {
            let drawn = if p.capturing() { p.draw_desktops() } else { p.draw_test_pattern() };
            let ready = match drawn.and_then(|()| p.dispatch(50, Some(control.as_fd())))
            {
                Ok(ready) => ready,
                Err(e) => {
                    eprintln!("{e}");
                    result = 1;
                    break;
                }
            };
            if ready {
                let mut buffer = [0u8; 4096];
                let open = loop {
                    match ipc::receive(&control, &mut buffer, &mut descriptors) {
                        Ok(0) => break false, // supervisor gone
                        Ok(n) => {
                            let more = stream.feed(&buffer[..n], &mut |verdict| match verdict {
                                Ok(m) => {
                                    apply(&m, &mut descriptors, &mut unhandled);
                                    send_line(status, &encode_applied(m.request_id));
                                }
                                Err(r) => {
                                    refused(r, &mut refusals);
                                    let op = r.op.as_deref().unwrap_or("");
                                    send_line(
                                        status,
                                        &encode_refused(r.request_id, op, &r.refusal.detail),
                                    );
                                    descriptors.refused(r);
                                }
                            });
                            if !more {
                                break false;
                            }
                        }
                        Err(Errno::AGAIN | Errno::INTR) => break true,
                        Err(e) => {
                            closed = format!("recv: {e}");
                            break false;
                        }
                    }
                };
                if !open {
                    if stream.stopped() {
                        eprintln!("control: stopping on a refused frame (--control-errors fatal)");
                    } else {
                        eprintln!("control: supervisor channel closed ({closed})");
                    }
                    result = 1;
                    break;
                }
            }
            if status.is_some() {
                // The overview layout for the supervisor's free-pointer hit testing, only when it changes.
                if let Some(line) = p.take_overview_geometry() {
                    if !sent_line(status, &format!("{line}\n")) {
                        p.resend_overview_geometry();
                    }
                }
                if let Some(line) = p.take_owner_readback(monotonic_ms()) {
                    if !sent_line(status, &format!("{line}\n")) {
                        p.resend_owner_readback();
                    }
                }
            }
            let now = Instant::now();
            if status.is_some() && now >= next_status {
                next_status = now + Duration::from_secs(1);
                let (capture_failure_run, capture_path) = p.capture_status();
                let extras = StatusExtras { capture_failure_run, capture_path, ..StatusExtras::default() };
                let sample = ipc::status_dict(&p.status_json(monotonic_ms_f64()), &extras);
                send_line(status, &format!("{sample}\n"));
            }
            if let Err(e) = send(&control, b"H", SendFlags::NOSIGNAL | SendFlags::DONTWAIT) {
                eprintln!("control: supervisor channel closed (heartbeat: {e})");
                result = 1;
                break;
            }
        }
    }
    // Pending feedback becomes abandoned, never an invented discard.
    let capture_report = presentation.as_ref().filter(|p| p.capturing()).map(Presentation::capture_report);
    let metrics = presentation.map(Presentation::close).unwrap_or_default();
    let mut err = std::io::stderr().lock();
    let _ = writeln!(err, "{}", metrics.report());
    if let Some(line) = capture_report {
        let _ = writeln!(err, "{line}");
    }
    let _ = writeln!(
        err,
        "{{\"kind\":\"control_stream\",\"accepted\":{},\"refused\":{}}}",
        stream.accepted(),
        stream.refused()
    );
    result
}
