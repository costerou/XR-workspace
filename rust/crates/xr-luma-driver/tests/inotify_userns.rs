//! D8 feasibility: does inotify on `/dev/bus/usb` work inside the driver's namespaces?
//!
//! The driver runs as `unshare --user --map-current-user --net`. A network namespace owned by a
//! user namespace receives no kernel uevents (docs/technical.md), so the reconnect plan (D8 in
//! docs/rust-rebuild-plan.md) wants inotify on devtmpfs instead. `unshare --user --net` makes no
//! mount namespace, so the watcher sees the host's devtmpfs; inotify is tied to the inode, not to
//! the user or network namespace. These tests check that in practice.
//!
//! `events_from_outside_reach_a_namespaced_watcher` runs the watcher under the real `unshare`
//! flags and makes a devtmpfs event from outside: it opens a `/dev/bus/usb/BBB` directory, which
//! fsnotify reports as `IN_OPEN | IN_ISDIR` to a watch on `/dev/bus/usb`. Opening a directory makes
//! no USB traffic. A device node appearing is the same path in the kernel: devtmpfs creates nodes
//! with `vfs_mknod`, which calls `fsnotify_create`, giving `IN_CREATE` on the bus directory.
//!
//! `replug_is_seen_inside_namespace` is the hardware check of that create path. It is ignored by
//! default; run it and unplug and replug any USB device within 30 s:
//! `cargo test -p xr-luma-driver --test inotify_userns -- --ignored --nocapture`

use std::ffi::{CStr, CString, c_char, c_int};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::FromRawFd;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::time::{Duration, Instant};

const IN_OPEN: u32 = 0x0000_0020;
const IN_CREATE: u32 = 0x0000_0100;
const IN_DELETE: u32 = 0x0000_0200;
const IN_ISDIR: u32 = 0x4000_0000;
const IN_CLOEXEC: c_int = 0o2_000_000;

unsafe extern "C" {
    fn inotify_init1(flags: c_int) -> c_int;
    fn inotify_add_watch(fd: c_int, path: *const c_char, mask: u32) -> c_int;
}

const USB: &str = "/dev/bus/usb";
const CHILD_ENV: &str = "XR_D8_INOTIFY_CHILD";

/// Watcher side, run inside the namespaces. Watches `/dev/bus/usb` and every bus directory,
/// prints `ready`, then prints one `event <mask> <watched dir>/<name>` line per event that
/// matches `want` (a mask) and exits after the first.
fn watcher(want: u32) -> ! {
    let fd = unsafe { inotify_init1(IN_CLOEXEC) };
    assert!(
        fd >= 0,
        "inotify_init1: {}",
        std::io::Error::last_os_error()
    );
    let mut dirs = vec![USB.to_string()];
    for entry in std::fs::read_dir(USB).expect("read /dev/bus/usb") {
        dirs.push(entry.unwrap().path().to_string_lossy().into_owned());
    }
    let mut wds = Vec::new();
    for dir in &dirs {
        let c = CString::new(dir.as_str()).unwrap();
        let wd = unsafe { inotify_add_watch(fd, c.as_ptr(), IN_OPEN | IN_CREATE | IN_DELETE) };
        assert!(
            wd >= 0,
            "inotify_add_watch {dir}: {}",
            std::io::Error::last_os_error()
        );
        wds.push((wd, dir.clone()));
    }
    let ns = |kind: &str| std::fs::read_link(format!("/proc/self/ns/{kind}")).unwrap();
    println!("D8-ready user={:?} net={:?}", ns("user"), ns("net"));
    std::io::stdout().flush().unwrap();

    // SAFETY: `fd` is the inotify descriptor made above, owned only by this file from here on.
    let mut inotify = unsafe { std::fs::File::from_raw_fd(fd) };
    let mut buf = [0u8; 4096];
    loop {
        let n = inotify.read(&mut buf).expect("read inotify");
        assert!(n > 0, "inotify read returned 0");
        let mut off = 0usize;
        while off + 16 <= n {
            let word =
                |at: usize| u32::from_ne_bytes(buf[off + at..off + at + 4].try_into().unwrap());
            let (wd, mask, len) = (word(0) as c_int, word(4), word(12) as usize);
            let name = CStr::from_bytes_until_nul(&buf[off + 16..off + 16 + len])
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            off += 16 + len;
            let dir = wds
                .iter()
                .find(|(w, _)| *w == wd)
                .map_or("?", |(_, d)| d.as_str());
            if mask & want == want {
                println!("D8-event {mask:#x} {dir}/{name}");
                std::io::stdout().flush().unwrap();
                std::process::exit(0);
            }
        }
    }
}

/// Starts this test binary again as the watcher, under the driver's `unshare` flags. Its stdout
/// lines arrive on the returned channel, so a silent watcher cannot hang the test.
fn spawn_watcher(test: &str, want: u32) -> (Watcher, Receiver<String>) {
    let exe = std::env::current_exe().unwrap();
    let mut child = Command::new("unshare")
        .args(["--user", "--map-current-user", "--net", "--"])
        .arg(exe)
        .args([
            "--exact",
            test,
            "--include-ignored",
            "--nocapture",
            "--test-threads=1",
        ])
        .env(CHILD_ENV, want.to_string())
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn unshare (util-linux)");
    let stdout = BufReader::new(child.stdout.take().unwrap());
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        stdout
            .lines()
            .map_while(Result::ok)
            .try_for_each(|l| tx.send(l))
    });
    (Watcher(child), rx)
}

/// The watcher process, killed if a test fails before it exits, so no orphan holds the pipes.
struct Watcher(Child);

impl Watcher {
    fn exited_ok(mut self) -> bool {
        self.0.wait().unwrap().success()
    }
}

impl Drop for Watcher {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// The text from `marker` on, in the first line holding it, or a panic after `timeout`. The
/// marker may not start the line: libtest prints `test <name> ... ` before the test's output.
fn line_with(lines: &Receiver<String>, marker: &str, timeout: Duration) -> String {
    let deadline = Instant::now() + timeout;
    loop {
        match lines.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
            Ok(line) => {
                if let Some(at) = line.find(marker) {
                    return line[at..].to_string();
                }
            }
            Err(RecvTimeoutError::Disconnected) => {
                panic!("watcher exited before printing {marker:?}")
            }
            Err(RecvTimeoutError::Timeout) => panic!("no {marker:?} line within {timeout:?}"),
        }
    }
}

fn child_mode() -> Option<u32> {
    std::env::var(CHILD_ENV).ok().map(|v| v.parse().unwrap())
}

fn host_ns(kind: &str) -> String {
    format!(
        "{:?}",
        std::fs::read_link(format!("/proc/self/ns/{kind}")).unwrap()
    )
}

#[test]
fn events_from_outside_reach_a_namespaced_watcher() {
    if let Some(want) = child_mode() {
        watcher(want);
    }
    let bus = std::fs::read_dir(USB)
        .expect("this machine has /dev/bus/usb")
        .map(|e| e.unwrap().path())
        .min()
        .expect("at least one USB bus");

    let (child, out) = spawn_watcher(
        "events_from_outside_reach_a_namespaced_watcher",
        IN_OPEN | IN_ISDIR,
    );
    let ready = line_with(&out, "D8-ready", Duration::from_secs(10));
    println!("watcher: {ready}");
    assert!(
        !ready.contains(&host_ns("user")),
        "watcher must be in its own user namespace"
    );
    assert!(
        !ready.contains(&host_ns("net")),
        "watcher must be in its own network namespace"
    );

    // From the host's namespaces: open a bus directory. No USB traffic.
    drop(std::fs::read_dir(&bus).unwrap());
    let event = line_with(&out, "D8-event", Duration::from_secs(5));
    println!("watcher: {event}");
    let name = bus.file_name().unwrap().to_string_lossy().into_owned();
    assert!(
        event.ends_with(&format!("{USB}/{name}")),
        "unexpected event {event}"
    );
    assert!(child.exited_ok());
}

#[test]
#[ignore = "hardware: unplug and replug any USB device within 30 s"]
fn replug_is_seen_inside_namespace() {
    if let Some(want) = child_mode() {
        watcher(want);
    }
    let (child, out) = spawn_watcher("replug_is_seen_inside_namespace", IN_CREATE);
    println!(
        "watcher: {}",
        line_with(&out, "D8-ready", Duration::from_secs(10))
    );
    eprintln!("now unplug and replug a USB device");
    let event = line_with(&out, "D8-event", Duration::from_secs(30));
    println!("watcher: {event}");
    assert!(
        event.starts_with(&format!("D8-event {IN_CREATE:#x} {USB}")),
        "unexpected event {event}"
    );
    assert!(child.exited_ok());
}
