//! XR renderer on GLES and Wayland.
//!
//! So far the presentation shell: the supervised launch of `spike/src/main.cpp` with the generated
//! test pattern (static, or `--animate-pattern-ms`), the captured desktops (`--capture`), the control stream (every op
//! is decoded, receipted and routed to its Presentation call as the C++ does; see shell/scene.rs for what each
//! feature draws so far), the status samples and the timing report. `--pose-feed`, `--depth-fd` and `--continuity-fd`
//! are parsed and handed to their features, which refuse them at launch (exit 2) until they are ported.

mod metrics;
mod pattern;
mod shell;

use std::collections::BTreeMap;
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
use xr_renderer::ipc::{self, ControlStream, Descriptors};
use xr_renderer::late_latch::LateLatch;
use xr_renderer::view::PointerLook;
use xr_wire::control::{
    self, Corner, Ghost, Message, Op, Payload, PointerLook as WireLook, Refused, RollSide, Video,
};
use xr_wire::status::{encode_applied, encode_refused};

const HELP: &str = "xr-renderer: native output-bound layer-shell renderer (no SDK/input; capture only of managed outputs).\n\
Launched only by the permission-gated Python supervisor with inherited capability sockets.\n\
Internal: --supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION\n\
Optional --status-fd FD: nonblocking measured status JSON, at most once per second.\n\
Optional --depth-fd FD: the depth engine's shared memory, for immersive 3D.\n\
Optional --continuity-fd FD: a sealed regular file of this user (at most 64 KiB) the view is restored from and saved to.\n\
Optional presentation-time v1 feedback: bounded aggregate JSON on stderr at orderly exit/error.\n\
Optional --control-errors fatal, for tests: a bad control frame stops the renderer. Otherwise it is logged and dropped.\n\
Default static; optional --animate-pattern-ms 1..60000 (exact matching consent required).\n\
Or --capture XR-1[,XR-2[,XR-3[,XR-4]]]: show only the app's own managed outputs (managed-output capture consent),\n\
  optionally with --pose-feed xrld: world-anchored 3DoF from the network-blocked XRLinuxDriver feed.\n\
  With the feed, optional --late-latch-guard-ms 0..8: draw each head-tracked frame just before the compositor's\n\
  next latch (D4 trial, off by default; the status latency.late_latch object reports it).\n\
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

/// The continuity file as spike/src/main.cpp takes it: a sealable regular file of this user, at most 64 KiB.
fn continuity_file(fd: BorrowedFd) -> bool {
    use rustix::fs::{FileType, fcntl_get_seals, fstat};
    fstat(fd).is_ok_and(|st| {
        FileType::from_raw_mode(st.st_mode) == FileType::RegularFile
            && st.st_uid == rustix::process::getuid().as_raw()
            && st.st_size <= 65536
    }) && fcntl_get_seals(fd).is_ok()
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

/// Routes a delivered frame to its Presentation call, as spike/src/main.cpp's `apply` does. `stop` stops; a `video`
/// open takes the oldest descriptor. What each call draws is its feature module's (shell/*.rs, rust/README.md); one
/// not wired yet logs once that it is not drawn.
fn apply(m: &Message, p: &mut Presentation, descriptors: &mut Descriptors) {
    match &m.payload {
        _ if m.op == Op::Stop => STOPPING.store(true, Ordering::Relaxed),
        _ if m.op == Op::Recenter => p.recenter(),
        Payload::View(v) => p.set_view(v),
        Payload::Environment(e) => p.set_environment(e),
        Payload::Style { border: [r, g, b] } => p.set_accent(*r, *g, *b),
        Payload::Help(h) => p.set_help(h.visible, &h.rows, &h.sections, h.turn),
        Payload::Binds(b) => p.set_binds(b),
        Payload::OptionsMenu(menu) => p.set_options_menu(menu),
        Payload::PlacementMenu(menu) => p.set_placement_menu(menu),
        Payload::ResizeHandle(h) => p.set_resize_handle(h.visible, h.desktop, corner(h.corner), h.active),
        Payload::OverviewPointer(o) => {
            let (directed, (az, el)) = (o.direction.is_some(), o.direction.unwrap_or_default());
            let ghost = o.drag.unwrap_or(Ghost { w: 0, h: 0, target: -1 });
            let (x, y, look) = (o.x, o.y, look(o.look));
            p.set_overview_pointer(o.visible, x, y, o.held, directed, az, el, ghost.w, ghost.h, ghost.target, look);
        }
        Payload::EdgePan(pan) => p.set_edge_pan(pan.as_ref()),
        Payload::Roll(r) => p.set_roll(&r.roll, &r.bars, r.hover, side(r.side), r.active),
        Payload::Immersive(i) => p.set_immersive(&i.desktops, i.global, i.stereo, i.hover, i.tail, &i.params),
        Payload::RestoreBand { open } => p.set_restore_band(*open),
        Payload::MoveHandle(g) => p.set_move_handle(g.visible, g.desktop, g.hover, g.active, g.gear),
        Payload::Desktops(d) => {
            if d.ease {
                p.ease_tiles(); // before the captures change
            }
            p.set_desktops(&d.outputs, &d.renew);
        }
        Payload::Placement(placement) => {
            let placements: Vec<[i32; 4]> =
                placement.desktops.iter().map(|w| [w.x, w.y, w.distance, w.curvature]).collect();
            p.set_placements(&placements, placement.glide_ms);
        }
        Payload::Video(v) => match v {
            Video::Open { format, title } => match descriptors.take() {
                Some(fd) => p.video_open(fd, format, title),
                None => eprintln!("control: video open without a file descriptor"),
            },
            Video::Close => p.video_close(),
            Video::Pause => p.video_pause(),
            Video::Seek { seconds } => p.video_seek(*seconds),
            Video::Format(format) => p.video_format(format),
            Video::Controls { visible } => p.video_controls(*visible),
            Video::Quality { filter, deband } => p.video_quality(*filter, *deband),
            Video::Menu { visible, selected, rows } => p.video_menu(*visible, rows, *selected),
        },
        // select, recover and status: no Presentation call in the C++ either.
        Payload::Select { .. } | Payload::Empty => p.receipted_only(m.op),
    }
}

/// The C++ corner numbers: 0 top left, 1 top right, 2 bottom left, 3 bottom right.
fn corner(c: Corner) -> u32 {
    match c {
        Corner::TopLeft => 0,
        Corner::TopRight => 1,
        Corner::BottomLeft => 2,
        Corner::BottomRight => 3,
    }
}

/// The C++ RollPayload side (glass LevelSegment): -1 none, 0 ccw, 1 level, 2 cw.
fn side(s: RollSide) -> i8 {
    match s {
        RollSide::None => -1,
        RollSide::Ccw => 0,
        RollSide::Level => 1,
        RollSide::Cw => 2,
    }
}

fn look(l: WireLook) -> PointerLook {
    match l {
        WireLook::Plain => PointerLook::Plain,
        WireLook::ResizeEw => PointerLook::ResizeEw,
        WireLook::ResizeNs => PointerLook::ResizeNs,
        WireLook::ResizeNwse => PointerLook::ResizeNwse,
        WireLook::ResizeNesw => PointerLook::ResizeNesw,
        WireLook::Move => PointerLook::Move,
        WireLook::Rotate => PointerLook::Rotate,
        WireLook::Text => PointerLook::Text,
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
    const OPTIONAL: [&str; 8] = [
        "--status-fd",
        "--control-errors",
        "--animate-pattern-ms",
        "--capture",
        "--pose-feed",
        "--depth-fd",
        "--continuity-fd",
        "--late-latch-guard-ms",
    ];
    if !REQUIRED.iter().all(|k| args.contains_key(k))
        || !args
            .keys()
            .all(|k| REQUIRED.contains(k) || OPTIONAL.contains(k))
    {
        return 2;
    }
    // A launch flag goes to its feature (shell/tracking.rs, immersive.rs, continuity_shell.rs); one this build does
    // not take yet exits 2, as an unknown flag did.
    if (args.contains_key("--pose-feed") && !shell::pose_feed_supported())
        || (args.contains_key("--depth-fd") && !shell::depth_fd_supported())
        || (args.contains_key("--continuity-fd") && !shell::continuity_fd_supported())
    {
        return 2;
    }
    // As spike/src/main.cpp: the feed is XRLinuxDriver's, with capture only.
    let pose_feed = args.contains_key("--pose-feed");
    if pose_feed && (args["--pose-feed"] != "xrld" || !args.contains_key("--capture")) {
        return 2;
    }
    // D4 late latch (xr_renderer::late_latch): head-tracked frames only, off unless given.
    let late_latch_guard = match args.get("--late-latch-guard-ms") {
        Some(v) => match LateLatch::parse_guard(Some(v)) {
            Some(g) if pose_feed => Some(g),
            _ => return 2,
        },
        None => None,
    };
    let depth = match args.get("--depth-fd") {
        Some(v) => match number(v) {
            Some(n) => Some(n),
            None => return 2,
        },
        None => None,
    };
    let continuity = match args.get("--continuity-fd") {
        Some(v) => match number(v) {
            Some(n) => Some(n),
            None => return 2,
        },
        None => None,
    };
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
    let taken = [Some(control_fd), Some(wayland_fd), status];
    if depth.is_some_and(|d| taken.contains(&Some(d)))
        || continuity.is_some_and(|c| taken.contains(&Some(c)) || depth == Some(c))
    {
        return 2;
    }
    // SAFETY: the supervisor hands these descriptors over at exec for this process alone; each is
    // owned once here and checked before any use: the sockets to be this user's, the continuity file as the C++ does.
    let (control, wayland, status, depth, continuity) = unsafe {
        (
            OwnedFd::from_raw_fd(control_fd),
            OwnedFd::from_raw_fd(wayland_fd),
            status.map(|fd| OwnedFd::from_raw_fd(fd)),
            depth.map(|fd| OwnedFd::from_raw_fd(fd)),
            continuity.map(|fd| OwnedFd::from_raw_fd(fd)),
        )
    };
    if !same_user(control.as_fd())
        || !same_user(wayland.as_fd())
        || status.as_ref().is_some_and(|s| !same_user(s.as_fd()))
        || continuity.as_ref().is_some_and(|c| !continuity_file(c.as_fd()))
    {
        return 2;
    }
    let continuity = continuity.as_ref().map(AsFd::as_fd);
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
    // As spike/src/main.cpp: depth, tracking, late latch, continuity, then the animation.
    if let Some(p) = &mut presentation {
        if let Some(fd) = depth {
            p.set_depth_fd(fd);
        }
        if pose_feed {
            p.enable_tracking(shell::POSE_PREFIX, 0.0, 0.0, false); // XRLinuxDriver IPC (fixed path)
        }
        if let Some(guard) = late_latch_guard {
            p.enable_late_latch(guard);
        }
        if p.restore_continuity(continuity) {
            eprintln!("continuity: restored");
        }
    }
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
                                    apply(&m, p, &mut descriptors);
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
                // How far each desktop is from level, and the help card on the overview's canvas.
                if let Some(line) = p.take_roll_geometry(monotonic_ms_f64()) {
                    if !sent_line(status, &format!("{line}\n")) {
                        p.resend_roll_geometry();
                    }
                }
                if let Some(line) = p.take_help_geometry(monotonic_ms_f64()) {
                    if !sent_line(status, &format!("{line}\n")) {
                        p.resend_help_geometry();
                    }
                }
                if let Some(line) = p.take_maximize_geometry() {
                    if !sent_line(status, &format!("{line}\n")) {
                        p.resend_maximize_geometry();
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
                let sample = ipc::status_dict(&p.status_json(monotonic_ms_f64()), &p.status_extras());
                send_line(status, &format!("{sample}\n"));
            }
            if let Err(e) = send(&control, b"H", SendFlags::NOSIGNAL | SendFlags::DONTWAIT) {
                eprintln!("control: supervisor channel closed (heartbeat: {e})");
                result = 1;
                break;
            }
        }
    }
    if let Some(p) = &mut presentation {
        p.save_continuity(continuity);
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
