//! Fault tests for the supervisor's watchdog: a port of `tests/native_spike/test_supervisor.py` with the same timing
//! windows (heartbeat 2 s, guard deadline 4.25 s, grace), on harmless `sh` children and local socketpairs.

use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::AsFd;
use std::os::unix::net::{UnixDatagram, UnixStream};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

#[path = "support/script.rs"]
mod script;

use xr_workspace::supervisor::{
    GUARD_DEADLINE, GUARD_PERIOD, HEARTBEAT_TIMEOUT, Launch, POLL_INTERVAL, Peer, Renderer,
    StopReason, Supervisor, capture_arguments, guard_worker, launch, start_frame, token,
};

const SIGKILL: i32 = 9;

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("supervisor-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// A renderer that ignores SIGTERM, so the watchdog must also escalate to SIGKILL and reap.
fn stubborn_child() -> Child {
    let mut child = Command::new("sh")
        .args(["-c", "trap '' TERM; echo ready; exec sleep 30"])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    BufReader::new(child.stdout.as_mut().unwrap())
        .read_line(&mut line)
        .unwrap();
    assert_eq!(line, "ready\n");
    child
}

/// The Python suite's `Guard` modes, as worker processes. Each writes its pid first, for `assert_clean`.
fn guard(pid_file: &Path, mode: &str) -> Command {
    let body = match mode {
        "ok" => "while :; do printf V; sleep 0.5; done",
        "fail" => "echo 'xr-workspace: run guard: synthetic invalid outputs' >&2; printf F",
        "crash" => "exit 1",
        "slow" => "while :; do sleep 4; printf V; sleep 0.5; done",
        "hung" => "exec sleep 6",
        _ => unreachable!(),
    };
    let mut command = Command::new("sh");
    // Written whole and then renamed, so a file that exists always holds the pid.
    let pid_file = pid_file.display();
    command.args([
        "-c",
        &format!("echo $$ > '{pid_file}.tmp'; mv '{pid_file}.tmp' '{pid_file}'; {body}"),
    ]);
    command
}

/// The worker ran in its own process, and it has been reaped: no orphan, no zombie.
fn assert_clean(pid_file: &Path) {
    let Ok(text) = std::fs::read_to_string(pid_file) else {
        return;
    };
    let pid: i32 = text.trim().parse().unwrap();
    assert_ne!(
        pid as u32,
        std::process::id(),
        "guard must not execute in the watchdog process"
    );
    let rc = unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) };
    assert_eq!(rc, -1, "worker {pid} must already be reaped");
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ECHILD)
    );
}

fn never() -> AtomicBool {
    AtomicBool::new(false)
}

fn watchdog(child: Child) -> Supervisor<Child, Option<ExitStatus>> {
    Supervisor::new(child, |status| status, Instant::now()).with_grace(Duration::from_millis(100))
}

fn killed(result: Option<ExitStatus>) -> bool {
    result.and_then(|s| s.signal()) == Some(SIGKILL)
}

// --- guard faults --------------------------------------------------------------------------------------------------

#[test]
fn slow_or_hung_guard_does_not_suspend_heartbeat_or_reaping() {
    for mode in ["slow", "hung"] {
        let dir = scratch(&format!("guard-{mode}"));
        let pid_file = dir.join("guard.pid");
        let (parent, mut peer) = UnixStream::pair().unwrap();
        // A queued heartbeat is the last one: the heartbeat's 2 s run out long before the guard's 4.25 s.
        peer.write_all(b"H").unwrap();
        let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, mode));
        let started = Instant::now();
        let result = w.run(&parent, None, &never(), POLL_INTERVAL).unwrap();
        let elapsed = started.elapsed();
        assert!(killed(result), "{mode}: {result:?}");
        assert_eq!(w.stop_reason, Some(StopReason::HeartbeatTimeout), "{mode}");
        assert!(elapsed >= HEARTBEAT_TIMEOUT, "{mode}: {elapsed:?}");
        assert!(elapsed < Duration::from_millis(2800), "{mode}: {elapsed:?}");
        assert_clean(&pid_file);
    }
}

#[test]
fn guard_failure_and_crash_reap_child_and_worker() {
    for mode in ["fail", "crash"] {
        let dir = scratch(&format!("guard-{mode}"));
        let pid_file = dir.join("guard.pid");
        let (parent, _peer) = UnixStream::pair().unwrap();
        let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, mode));
        let started = Instant::now();
        let result = w.run(&parent, None, &never(), POLL_INTERVAL).unwrap();
        assert!(killed(result), "{mode}: {result:?}");
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "{mode}: {:?}",
            started.elapsed()
        );
        assert_eq!(w.stop_reason, Some(StopReason::GuardFailed), "{mode}");
        assert_clean(&pid_file);
    }
}

#[test]
fn hung_guard_fails_closed_even_with_fresh_heartbeats() {
    let dir = scratch("guard-hung-fresh");
    let pid_file = dir.join("guard.pid");
    let (parent, peer) = UnixStream::pair().unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let feeder = {
        let stop = stop.clone();
        let mut peer = peer.try_clone().unwrap();
        std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                if peer.write_all(b"H").is_err() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        })
    };
    let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, "hung"));
    let started = Instant::now();
    let result = w.run(&parent, None, &never(), POLL_INTERVAL).unwrap();
    let elapsed = started.elapsed();
    stop.store(true, Ordering::Relaxed);
    feeder.join().unwrap();
    assert!(killed(result), "{result:?}");
    assert_eq!(w.stop_reason, Some(StopReason::GuardTimeout));
    // Exactly the guard's deadline: not earlier (the heartbeats are fresh), not much later (one loop pass + grace).
    assert!(elapsed >= GUARD_DEADLINE, "{elapsed:?}");
    assert!(elapsed < Duration::from_secs(5), "{elapsed:?}");
    assert_clean(&pid_file);
}

#[test]
fn a_healthy_guard_keeps_the_run_past_its_deadline() {
    // Valid verdicts every 0.5 s move the deadline on: the run outlives 4.25 s, then ends by itself.
    let dir = scratch("guard-ok");
    let pid_file = dir.join("guard.pid");
    let (parent, peer) = UnixStream::pair().unwrap();
    let feeder = {
        let mut peer = peer.try_clone().unwrap();
        std::thread::spawn(move || {
            let until = Instant::now() + Duration::from_millis(5500);
            while Instant::now() < until {
                if peer.write_all(b"H").is_err() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = peer.shutdown(std::net::Shutdown::Write);
        })
    };
    let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, "ok"));
    let started = Instant::now();
    let result = w.run(&parent, None, &never(), POLL_INTERVAL).unwrap();
    feeder.join().unwrap();
    assert!(killed(result));
    assert_eq!(w.stop_reason, Some(StopReason::RendererClosed));
    assert!(
        started.elapsed() >= Duration::from_millis(5400),
        "{:?}",
        started.elapsed()
    );
    assert_clean(&pid_file);
}

#[test]
fn shutdown_cleans_the_worker() {
    let dir = scratch("guard-shutdown");
    let pid_file = dir.join("guard.pid");
    let (parent, peer) = UnixStream::pair().unwrap();
    drop(peer);
    let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, "hung"));
    let result = w.run(&parent, None, &never(), POLL_INTERVAL).unwrap();
    assert!(killed(result));
    assert_eq!(w.stop_reason, Some(StopReason::RendererClosed));
    assert_clean(&pid_file);
}

#[test]
fn an_interrupt_is_named_and_still_reaps() {
    let dir = scratch("guard-interrupt");
    let pid_file = dir.join("guard.pid");
    let (parent, _peer) = UnixStream::pair().unwrap();
    let interrupt = AtomicBool::new(true);
    let mut w = watchdog(stubborn_child()).with_guard(guard(&pid_file, "hung"));
    let result = w.run(&parent, None, &interrupt, POLL_INTERVAL).unwrap();
    assert!(killed(result));
    assert_eq!(w.stop_reason, Some(StopReason::Interrupted));
    assert_clean(&pid_file);
}

#[test]
fn replace_guard_counts_and_pause_stops_the_worker() {
    let dir = scratch("guard-replace");
    let first = dir.join("first.pid");
    let second = dir.join("second.pid");
    let wait_for = |path: &Path| {
        let deadline = Instant::now() + Duration::from_secs(1);
        while !path.exists() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(path.exists());
    };
    let mut w = watchdog(stubborn_child());
    w.replace_guard(Some(guard(&first, "hung"))).unwrap();
    wait_for(&first);
    w.replace_guard(Some(guard(&second, "hung"))).unwrap();
    assert_eq!(w.guard_restarts, 2);
    assert_clean(&first);
    wait_for(&second);
    w.pause_guard();
    assert!(w.guard_paused);
    assert_clean(&second);
    w.disconnect(Instant::now(), None);
    while !w.finished() {
        w.tick(Instant::now());
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn the_worker_loop_writes_one_byte_per_check() {
    let mut out = Vec::new();
    let mut checks = 0;
    guard_worker(
        || {
            checks += 1;
            if checks < 3 {
                Ok(())
            } else {
                Err("outputs changed")
            }
        },
        &mut out,
    );
    assert_eq!(out, b"VVF");
}

// --- heartbeat and stop reasons (Python StopReasonTests) ------------------------------------------------------------

/// A renderer that is up until told otherwise.
struct Up(Option<ExitStatus>);

impl Renderer for Up {
    fn poll(&mut self) -> Option<ExitStatus> {
        self.0
    }
    fn terminate(&mut self) {
        self.0 = Some(ExitStatus::from_raw(0));
    }
    fn kill(&mut self) {
        self.0 = Some(ExitStatus::from_raw(SIGKILL));
    }
}

fn fake(code: Option<i32>) -> (Supervisor<Up, &'static str>, Instant) {
    let t0 = Instant::now();
    (
        Supervisor::new(
            Up(code.map(|c| ExitStatus::from_raw(c << 8))),
            |_| "done",
            t0,
        ),
        t0,
    )
}

#[test]
fn no_heartbeat_for_two_seconds() {
    let (mut w, t0) = fake(None);
    w.tick(t0 + Duration::from_millis(1999));
    assert_eq!(w.stop_reason, None);
    w.tick(t0 + HEARTBEAT_TIMEOUT);
    assert_eq!(w.stop_reason, Some(StopReason::HeartbeatTimeout));
}

#[test]
fn a_heartbeat_moves_the_two_seconds() {
    let (mut w, t0) = fake(None);
    w.heartbeat(t0 + Duration::from_millis(1500));
    w.tick(t0 + Duration::from_millis(3499));
    assert_eq!(w.stop_reason, None);
    w.tick(t0 + Duration::from_millis(3500));
    assert_eq!(w.stop_reason, Some(StopReason::HeartbeatTimeout));
}

#[test]
fn a_healthy_run_has_no_reason() {
    let (mut w, t0) = fake(None);
    w.tick(t0 + Duration::from_secs(1));
    assert_eq!(w.stop_reason, None);
}

#[test]
fn the_first_reason_stands() {
    let (mut w, t0) = fake(None);
    w.disconnect(t0, Some(StopReason::Interrupted));
    w.disconnect(t0, Some(StopReason::HeartbeatTimeout));
    assert_eq!(w.stop_reason, Some(StopReason::Interrupted));
}

#[test]
fn a_renderer_already_gone_was_not_stopped_by_anyone() {
    let (mut w, t0) = fake(Some(1));
    w.disconnect(t0, Some(StopReason::SendFailed));
    assert_eq!(w.stop_reason, None);
}

#[test]
fn a_disconnect_with_no_reason_still_works() {
    let (mut w, t0) = fake(None);
    w.disconnect(t0, None);
    assert_eq!(w.stop_reason, None);
    w.tick(t0);
    assert!(w.finished());
    assert_eq!(w.take_result(), Some("done"));
}

#[test]
fn tracking_lost_ends_the_run_only_after_it_worked() {
    let t0 = Instant::now();
    let mut w =
        Supervisor::new(Up(None), |_| (), t0).with_tracking_timeout(Some(Duration::from_secs(3)));
    w.tracking_status(false, t0);
    w.heartbeat(t0 + Duration::from_secs(5));
    w.tick(t0 + Duration::from_secs(5));
    assert_eq!(w.stop_reason, None, "never tracked: nothing lost");
    w.tracking_status(true, t0 + Duration::from_secs(5));
    w.tracking_status(false, t0 + Duration::from_secs(6));
    w.heartbeat(t0 + Duration::from_millis(8900));
    w.tick(t0 + Duration::from_millis(8999));
    assert_eq!(w.stop_reason, None);
    w.tick(t0 + Duration::from_secs(9));
    assert_eq!(w.stop_reason, Some(StopReason::TrackingTimeout));
}

#[test]
fn the_grace_comes_before_the_kill() {
    struct Stubborn(Option<ExitStatus>, bool);
    impl Renderer for Stubborn {
        fn poll(&mut self) -> Option<ExitStatus> {
            self.0
        }
        fn terminate(&mut self) {
            self.1 = true;
        }
        fn kill(&mut self) {
            self.0 = Some(ExitStatus::from_raw(SIGKILL));
        }
    }
    let t0 = Instant::now();
    let mut w = Supervisor::new(Stubborn(None, false), |s| s, t0);
    w.tick(t0 + HEARTBEAT_TIMEOUT);
    w.tick(t0 + HEARTBEAT_TIMEOUT + Duration::from_millis(499));
    assert!(!w.finished());
    w.tick(t0 + HEARTBEAT_TIMEOUT + Duration::from_millis(500));
    assert!(w.finished());
    assert!(killed(w.take_result().unwrap()));
}

fn run_on(data: &[u8]) -> (Option<StopReason>, bool) {
    let (parent, mut peer) = UnixStream::pair().unwrap();
    let (mut w, _) = fake(None);
    let before = w.last_heartbeat;
    if !data.is_empty() {
        peer.write_all(data).unwrap();
    }
    peer.shutdown(std::net::Shutdown::Write).unwrap();
    assert_eq!(
        w.run(&parent, None, &never(), POLL_INTERVAL).unwrap(),
        "done"
    );
    (w.stop_reason, w.last_heartbeat > before)
}

#[test]
fn the_heartbeat_channel_is_h_only() {
    assert_eq!(run_on(b"H"), (Some(StopReason::RendererClosed), true));
    assert_eq!(run_on(b"HHX"), (Some(StopReason::HeartbeatInvalid), false));
    assert_eq!(
        run_on(b"{\"missed\":1}\n"),
        (Some(StopReason::HeartbeatInvalid), false)
    );
    assert_eq!(run_on(b""), (Some(StopReason::RendererClosed), false));
}

#[test]
fn a_failed_send() {
    let (parent, peer) = UnixStream::pair().unwrap();
    drop(peer);
    let (mut w, _) = fake(None);
    w.send(&parent, b"xxxxxxxx", Vec::new());
    assert_eq!(w.stop_reason, Some(StopReason::SendFailed));
}

#[test]
fn a_send_carries_its_descriptors() {
    let (parent, peer) = UnixStream::pair().unwrap();
    let (mut w, _) = fake(None);
    let (shared, mut other) = UnixStream::pair().unwrap();
    w.send(
        &parent,
        b"{\"op\":\"video\"}\n",
        vec![shared.as_fd().try_clone_to_owned().unwrap()],
    );
    drop(shared);
    assert_eq!(w.stop_reason, None);
    // Receive with SCM_RIGHTS and prove the descriptor is the other end's peer.
    let mut data = [0u8; 64];
    let mut control = [0u64; 8];
    let mut iov = libc::iovec {
        iov_base: data.as_mut_ptr().cast(),
        iov_len: data.len(),
    };
    let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
    message.msg_iov = &mut iov;
    message.msg_iovlen = 1;
    message.msg_control = control.as_mut_ptr().cast();
    message.msg_controllen = std::mem::size_of_val(&control) as _;
    use std::os::fd::AsRawFd;
    let n = unsafe { libc::recvmsg(peer.as_raw_fd(), &mut message, 0) };
    assert_eq!(&data[..n as usize], b"{\"op\":\"video\"}\n");
    let fd = unsafe {
        let header = libc::CMSG_FIRSTHDR(&message);
        assert_eq!((*header).cmsg_type, libc::SCM_RIGHTS);
        *libc::CMSG_DATA(header).cast::<i32>()
    };
    let mut received = unsafe { <UnixStream as std::os::fd::FromRawFd>::from_raw_fd(fd) };
    received.write_all(b"ok").unwrap();
    let mut back = [0u8; 2];
    other.read_exact(&mut back).unwrap();
    assert_eq!(&back, b"ok");
}

// --- status --------------------------------------------------------------------------------------------------------

#[test]
fn status_socket_is_separate_and_does_not_refresh_heartbeat() {
    let (parent, peer) = UnixStream::pair().unwrap();
    let (status, writer) = UnixDatagram::pair().unwrap();
    let lines = Arc::new(std::sync::Mutex::new(Vec::<Vec<u8>>::new()));
    let seen = lines.clone();
    let (w, _) = fake(None);
    let mut w = w.with_status_handler(Box::new(move |line| {
        seen.lock().unwrap().push(line.to_vec());
        None
    }));
    let before = w.last_heartbeat;
    writer.send(b"{\"missed\":3}\n").unwrap();
    let closer = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(100));
        drop(peer);
    });
    assert_eq!(
        w.run(&parent, Some(&status), &never(), POLL_INTERVAL)
            .unwrap(),
        "done"
    );
    closer.join().unwrap();
    assert_eq!(*lines.lock().unwrap(), vec![b"{\"missed\":3}\n".to_vec()]);
    assert_eq!(w.last_heartbeat, before);
}

#[test]
fn status_lines_are_bounded() {
    let lines = Arc::new(std::sync::Mutex::new(Vec::<Vec<u8>>::new()));
    let seen = lines.clone();
    let (w, t0) = fake(None);
    let mut w = w.with_status_handler(Box::new(move |line| {
        seen.lock().unwrap().push(line.to_vec());
        Some(true)
    }));
    let long = vec![b'x'; 16384];
    w.status_bytes(&long, t0); // exactly the limit: kept, not yet a line
    w.status_bytes(b"y\n{\"a\":1}\n{\"b\"", t0); // one byte over: dropped whole; the next line is fine
    w.status_bytes(b":2}\n", t0);
    assert_eq!(
        *lines.lock().unwrap(),
        vec![b"{\"a\":1}\n".to_vec(), b"{\"b\":2}\n".to_vec()]
    );
}

// --- launch ----------------------------------------------------------------------------------------------------------

#[test]
fn tokens_and_capture_are_checked() {
    assert!(token("run-1.a:b_c").is_ok());
    assert!(
        token("").is_err()
            && token("-x").is_err()
            && token("a b").is_err()
            && token(&"a".repeat(129)).is_err()
    );
    assert_eq!(
        capture_arguments(None, "DP-1").unwrap(),
        Vec::<String>::new()
    );
    assert_eq!(
        capture_arguments(Some(&["XR-2", "XR-1"]), "DP-1").unwrap(),
        vec!["--capture", "XR-1,XR-2"]
    );
    for bad in [
        &[][..],
        &["XR-1", "XR-1"],
        &["XR-5"],
        &["DP-1"],
        &["XR-1", "XR-2", "XR-3", "XR-4", "XR-0"],
    ] {
        assert!(capture_arguments(Some(bad), "DP-1").is_err(), "{bad:?}");
    }
    assert!(capture_arguments(Some(&["XR-1"]), "XR-1").is_err());
}

#[test]
fn the_start_frame_matches_the_corpus() {
    let corpus = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/native_spike/fixtures/control_frames.json");
    let text = std::fs::read_to_string(corpus).unwrap();
    let frame = String::from_utf8(start_frame("run", "session")).unwrap();
    let escaped = frame.replace('"', "\\\"").replace('\n', "\\n");
    let start = &text[text.find("\"start\": {").unwrap()..];
    let encoded = &start[start.find("\"encoded\": [").unwrap()..];
    assert!(
        encoded[..encoded.find(']').unwrap()].contains(&format!("\"{escaped}\"")),
        "{frame}"
    );
}

/// A stand-in renderer: records its argv, its open descriptors and its environment, reads the start frame from its
/// supervised socket, beats three times and exits 7.
fn fake_renderer(dir: &Path, body: &str) -> PathBuf {
    let path = dir.join("renderer.sh");
    let out = dir.display();
    script::write_script(
        &path,
        &format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{out}/argv'\nls -l /proc/$$/fd > '{out}/fds'\n\
             printf '%s' \"$MESA_SHADER_CACHE_DISABLE\" > '{out}/mesa'\n\
             sup=; while [ $# -gt 0 ]; do [ \"$1\" = --supervised-fd ] && sup=$2; shift; done\n{body}\n"
        ),
    );
    path
}

#[test]
fn launch_hands_the_renderer_exactly_its_descriptors() {
    let dir = scratch("launch");
    let renderer = fake_renderer(
        &dir,
        &format!(
            "eval \"IFS= read -r line <&$sup\"; printf '%s\\n' \"$line\" > '{}/start'\n\
             for i in 1 2 3; do eval \"printf H >&$sup\"; sleep 0.2; done; exit 7",
            dir.display()
        ),
    );
    let (wayland, compositor) = UnixStream::pair().unwrap();
    let peer = Peer::of(&wayland).unwrap();
    let (depth, _depth_peer) = UnixStream::pair().unwrap();
    let mut spec = Launch::new(&renderer, "DP-2", "run", "session", peer);
    spec.capture = Some(&["XR-1", "XR-2"]);
    spec.pose_feed = true;
    spec.renderer_flags = vec!["--late-latch-guard-ms".into(), "2".into()];
    spec.depth_fd = Some(depth.as_fd());
    // Any other descriptor of ours must not reach the renderer.
    let stray = std::fs::File::open("/dev/null").unwrap();
    use std::os::fd::AsRawFd;
    unsafe { libc::fcntl(stray.as_raw_fd(), libc::F_SETFD, 0) };
    let started = Instant::now();
    let outcome = launch(spec, wayland, |status| status, &never()).unwrap();
    assert!(
        started.elapsed() < HEARTBEAT_TIMEOUT,
        "the heartbeats kept it up, its own exit ended it"
    );
    assert_eq!(outcome.result.and_then(|s| s.code()), Some(7));
    // Its exit closes the heartbeat socket, which the loop can see before the reaping (as in Python): either way the
    // exit status is the renderer's own.
    assert!(
        matches!(outcome.stop_reason, None | Some(StopReason::RendererClosed)),
        "{:?}",
        outcome.stop_reason
    );

    let argv: Vec<String> = std::fs::read_to_string(dir.join("argv"))
        .unwrap()
        .lines()
        .map(String::from)
        .collect();
    let value = |key: &str| argv[argv.iter().position(|a| a == key).unwrap() + 1].clone();
    let fd = |key: &str| value(key).parse::<i32>().unwrap();
    assert_eq!(
        argv.iter()
            .filter(|a| a.starts_with("--"))
            .cloned()
            .collect::<Vec<_>>(),
        [
            "--supervised-fd",
            "--wayland-fd",
            "--output",
            "--run-id",
            "--session",
            "--status-fd",
            "--depth-fd",
            "--capture",
            "--pose-feed",
            "--late-latch-guard-ms"
        ]
    );
    assert_eq!(
        (
            value("--output"),
            value("--capture"),
            value("--pose-feed"),
            value("--late-latch-guard-ms")
        ),
        ("DP-2".into(), "XR-1,XR-2".into(), "xrld".into(), "2".into())
    );
    let fds = std::fs::read_to_string(dir.join("fds")).unwrap();
    let mut open: Vec<i32> = fds
        .lines()
        .filter(|l| !l.contains("renderer.sh"))
        .filter_map(|l| l.split(" -> ").next()?.rsplit(' ').next()?.parse().ok())
        .filter(|&n| n > 2)
        .collect();
    open.sort();
    let mut expected = vec![
        fd("--supervised-fd"),
        fd("--wayland-fd"),
        fd("--status-fd"),
        fd("--depth-fd"),
    ];
    expected.sort();
    assert_eq!(open, expected, "{fds}");
    assert_eq!(std::fs::read_to_string(dir.join("mesa")).unwrap(), "true");
    assert_eq!(
        std::fs::read(dir.join("start")).unwrap(),
        start_frame("run", "session")
    );
    // Our Wayland reference is gone with the run.
    compositor
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    assert_eq!((&compositor).read(&mut [0u8; 1]).unwrap(), 0);
}

#[test]
fn a_silent_renderer_is_stopped_after_two_seconds() {
    let dir = scratch("launch-silent");
    let renderer = fake_renderer(&dir, "trap '' TERM; exec sleep 30");
    let (wayland, _compositor) = UnixStream::pair().unwrap();
    let peer = Peer::of(&wayland).unwrap();
    let mut spec = Launch::new(&renderer, "DP-2", "run", "session", peer);
    spec.grace = Duration::from_millis(100);
    let started = Instant::now();
    let outcome = launch(spec, wayland, |status| status, &never()).unwrap();
    let elapsed = started.elapsed();
    assert_eq!(outcome.stop_reason, Some(StopReason::HeartbeatTimeout));
    assert!(killed(outcome.result));
    assert!(
        elapsed >= HEARTBEAT_TIMEOUT && elapsed < Duration::from_millis(2800),
        "{elapsed:?}"
    );
}

#[test]
fn launch_interrupt_reaps_renderer_and_worker() {
    let dir = scratch("launch-interrupt");
    let pid_file = dir.join("guard.pid");
    let ready = dir.join("ready");
    let renderer = fake_renderer(
        &dir,
        &format!("trap '' TERM; : > '{}'; exec sleep 30", ready.display()),
    );
    let (wayland, _compositor) = UnixStream::pair().unwrap();
    let peer = Peer::of(&wayland).unwrap();
    let mut spec = Launch::new(&renderer, "DP-2", "run", "session", peer);
    spec.grace = Duration::from_millis(100);
    spec.guard = Some(guard(&pid_file, "hung"));
    let interrupt = Arc::new(AtomicBool::new(false));
    let trigger = {
        let interrupt = interrupt.clone();
        let pid_file = pid_file.clone();
        let ready = ready.clone();
        std::thread::spawn(move || {
            let until = Instant::now() + Duration::from_secs(1);
            // Both the worker and the renderer's TERM trap must be in place, or a TERM could end it unkilled.
            while !(pid_file.exists() && ready.exists()) && Instant::now() < until {
                std::thread::sleep(Duration::from_millis(10));
            }
            interrupt.store(true, Ordering::Relaxed);
        })
    };
    let outcome = launch(spec, wayland, |status| status, &interrupt).unwrap();
    trigger.join().unwrap();
    assert!(
        pid_file.exists(),
        "worker must be running before the interrupt"
    );
    assert_eq!(outcome.stop_reason, Some(StopReason::Interrupted));
    assert!(killed(outcome.result));
    assert_clean(&pid_file);
}

#[test]
fn launch_refuses_a_foreign_peer_and_still_closes_wayland() {
    let dir = scratch("launch-peer");
    let renderer = fake_renderer(&dir, "exit 0");
    let (wayland, compositor) = UnixStream::pair().unwrap();
    let mut peer = Peer::of(&wayland).unwrap();
    peer.pid += 1;
    let error = launch(
        Launch::new(&renderer, "DP-2", "run", "session", peer),
        wayland,
        |s| s,
        &never(),
    )
    .unwrap_err();
    assert_eq!(error.kind(), std::io::ErrorKind::PermissionDenied);
    assert!(!dir.join("argv").exists(), "no renderer started");
    assert_eq!((&compositor).read(&mut [0u8; 1]).unwrap(), 0);
}

#[test]
fn launch_refuses_what_python_refuses() {
    let dir = scratch("launch-refuse");
    let renderer = fake_renderer(&dir, "exit 0");
    let cases: [fn(&mut Launch); 4] = [
        |s| s.pose_feed = true,                 // head tracking without capture
        |s| s.animate_pattern_ms = Some(60001), // animation too long
        |s| {
            s.animate_pattern_ms = Some(10);
            s.capture = Some(&["XR-1"])
        }, // both
        |s| s.run_id = "bad id",
    ];
    for case in cases {
        let (wayland, _compositor) = UnixStream::pair().unwrap();
        let peer = Peer::of(&wayland).unwrap();
        let mut spec = Launch::new(&renderer, "DP-2", "run", "session", peer);
        case(&mut spec);
        assert_eq!(
            launch(spec, wayland, |s| s, &never()).unwrap_err().kind(),
            std::io::ErrorKind::InvalidInput
        );
    }
    assert!(!dir.join("argv").exists());
}

#[test]
fn the_constants_are_pythons() {
    let source = std::fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tools/native_spike/supervisor.py"),
    )
    .unwrap();
    assert!(source.contains("GUARD_DEADLINE = 4.25\n") && source.contains("GUARD_PERIOD = 0.5\n"));
    assert!(source.contains("now - self.last_heartbeat >= 2:"));
    assert_eq!(
        (
            GUARD_DEADLINE.as_secs_f64(),
            GUARD_PERIOD.as_secs_f64(),
            HEARTBEAT_TIMEOUT.as_secs_f64()
        ),
        (4.25, 0.5, 2.0)
    );
}
