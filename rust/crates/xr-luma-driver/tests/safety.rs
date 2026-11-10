//! The safety audit, S1 to S10 of spike/driver/docs/acceptance.md, on the Rust driver: its sources, its link map, and the
//! built `xr-luma-driver` binary run under a seccomp filter and inside `unshare --user --net`. The C++ driver's offline half
//! of this is `spike/driver/tests/contract_test.cpp`; the source scan below follows it rule for rule where Rust has the
//! same thing to forbid.
//!
//! No test here touches a USB device. The replay runs never open one (`ReplayLink`), and the one live run
//! (`s10_live_run_is_unprivileged_and_waits_quietly`) is skipped when any VITURE device is plugged in, so a running XR
//! session is never disturbed. Every run uses its own `--ipc-prefix`, so the live feed is never written.

use std::fs;
use std::io::Read;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::{Duration, Instant};
use xr_luma_driver::capture::{CaptureKind, CaptureRecord, ReplayLink, save_capture};
use xr_luma_driver::daemon::{Daemon, DaemonConfig, DaemonParts, Estimator, Link, ReadResult, Sink};
use xr_luma_driver::fusion::{Fusion, FusionConfig};
use xr_luma_driver::options::Start;
use xr_luma_driver::protocol::LumaDecoder;
use xr_luma_driver::transport::allowed_mcu_frame;
use xr_luma_driver::types::PoseOut;
use xr_luma_driver::wiring;

const BIN: &str = env!("CARGO_BIN_EXE_xr-luma-driver");
const CRATE: &str = env!("CARGO_MANIFEST_DIR");

// ---------------------------------------------------------------------------------------------------------------------
// Helpers

fn scratch(name: &str) -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("safety-{}-{}-{name}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn fixture_packets() -> Vec<Vec<u8>> {
    let path = Path::new(CRATE).join("../../../spike/driver/tests/fixtures/luma_imu_real.bin");
    let bytes = fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    bytes.chunks_exact(28).map(<[u8]>::to_vec).collect()
}

/// The real recording as a capture, 1 ms a packet: about 8 s. `drop_at`: the device goes away there for 50 ms.
fn capture(dir: &Path, drop_at: Option<usize>) -> PathBuf {
    capture_with_silence(dir, drop_at, 0)
}

/// As [`capture`], with the device silent for `silent_ms` after each connection, as one that waits for its start is.
fn capture_with_silence(dir: &Path, drop_at: Option<usize>, silent_ms: u64) -> PathBuf {
    let mut t = 1_000_000_000u64;
    // A replay's clock starts at its first record, so a marker there makes the opening silence count.
    let mut records = vec![CaptureRecord { t_ns: t, kind: CaptureKind::DeviceBack, payload: vec![] }];
    t += silent_ms * 1_000_000;
    for (i, payload) in fixture_packets().into_iter().enumerate() {
        if drop_at == Some(i) {
            records.push(CaptureRecord { t_ns: t, kind: CaptureKind::DeviceGone, payload: vec![] });
            t += 50_000_000;
            records.push(CaptureRecord { t_ns: t, kind: CaptureKind::DeviceBack, payload: vec![] });
            t += silent_ms * 1_000_000;
        }
        records.push(CaptureRecord { t_ns: t, kind: CaptureKind::FromDevice, payload });
        t += 1_000_000;
    }
    let path = dir.join("capture.lcap");
    save_capture(&path, &records).unwrap();
    path
}

fn prefix(dir: &Path) -> String {
    format!("{}/feed_", dir.display())
}

fn replay(dir: &Path, cap: &Path, realtime: bool) -> Command {
    let mut c = Command::new(BIN);
    c.arg("--replay").arg(cap).arg("--ipc-prefix").arg(prefix(dir));
    if realtime {
        c.arg("--replay-realtime");
    }
    c.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    c
}

fn stderr_of(child: &mut Child) -> String {
    let mut s = String::new();
    child.stderr.take().unwrap().read_to_string(&mut s).unwrap();
    s
}

fn wait_for(what: &str, timeout: Duration, mut ready: impl FnMut() -> bool) {
    let until = Instant::now() + timeout;
    while !ready() {
        assert!(Instant::now() < until, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The feed is up once its files exist (the driver makes them before its first read).
fn wait_running(dir: &Path, child: &mut Child) {
    wait_for("the driver's lock", Duration::from_secs(5), || {
        assert!(child.try_wait().unwrap().is_none(), "the driver exited early");
        Path::new(&format!("{}luma-driver.lock", prefix(dir))).exists()
    });
    std::thread::sleep(Duration::from_millis(300));
}

fn exit_within(child: &mut Child, timeout: Duration) -> std::process::ExitStatus {
    let until = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        if Instant::now() > until {
            let _ = child.kill();
            panic!("the driver did not exit within {timeout:?}");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn signal(child: &Child, sig: i32) {
    // SAFETY: kill(2) on our own child's pid.
    assert_eq!(unsafe { libc::kill(child.id() as i32, sig) }, 0);
}

// ---------------------------------------------------------------------------------------------------------------------
// S1, S2, S5, S6 offline: the source scan. Comments are blanked first, because a comment saying "never USBDEVFS_RESET"
// must pass. Strings stay: a path or a library name in one still counts.

fn strip_comments(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    let blank = |out: &mut String, c: u8| out.push(if c == b'\n' { '\n' } else { ' ' });
    while i < b.len() {
        let c = b[i];
        let next = b.get(i + 1).copied().unwrap_or(0);
        if c == b'/' && next == b'/' {
            while i < b.len() && b[i] != b'\n' {
                out.push(' ');
                i += 1;
            }
        } else if c == b'/' && next == b'*' {
            // Rust block comments nest.
            let mut depth = 0;
            while i < b.len() {
                if b[i] == b'/' && b.get(i + 1) == Some(&b'*') {
                    depth += 1;
                    out.push_str("  ");
                    i += 2;
                } else if b[i] == b'*' && b.get(i + 1) == Some(&b'/') {
                    depth -= 1;
                    out.push_str("  ");
                    i += 2;
                    if depth == 0 {
                        break;
                    }
                } else {
                    blank(&mut out, b[i]);
                    i += 1;
                }
            }
        } else if c == b'r' && (next == b'"' || next == b'#') && (i == 0 || !is_ident(b[i - 1])) {
            // Raw string r#"..."#: copied whole, so nothing inside opens a comment.
            let mut j = i + 1;
            while j < b.len() && b[j] == b'#' {
                j += 1;
            }
            if b.get(j) != Some(&b'"') {
                out.push(c as char);
                i += 1;
                continue;
            }
            let close = format!("\"{}", "#".repeat(j - i - 1));
            let end = s[j + 1..].find(&close).map_or(b.len(), |e| j + 1 + e + close.len());
            out.push_str(&s[i..end]);
            i = end;
        } else if c == b'"' {
            let start = i;
            i += 1;
            while i < b.len() && b[i] != b'"' {
                i += if b[i] == b'\\' { 2 } else { 1 };
            }
            i = (i + 1).min(b.len());
            out.push_str(&s[start..i]);
        } else if c == b'\'' {
            // A char literal ('x', '\n', '"') is copied whole; a lifetime ('a) is just code.
            let lit = if next == b'\\' {
                s[i + 2..].find('\'').map(|e| i + 2 + e + 1)
            } else {
                let width = s[i + 1..].chars().next().map_or(1, char::len_utf8);
                (b.get(i + 1 + width) == Some(&b'\'')).then_some(i + 2 + width)
            };
            let end = lit.unwrap_or(i + 1);
            out.push_str(&s[i..end]);
            i = end;
        } else {
            let width = s[i..].chars().next().map_or(1, char::len_utf8);
            out.push_str(&s[i..i + width]);
            i += width;
        }
    }
    out
}

fn is_ident(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_'
}

struct Rule {
    text: &'static str,
    /// Whole identifier only, so AF_INET does not match AF_INET6.
    word: bool,
    why: &'static str,
}

const FORBIDDEN: &[Rule] = &[
    // S2: the driver may only claim, read and write. usbdevfs.rs names every request in a table by its bare name, and
    // `is_allowed_ioctl` passes five; these are the ways round that table.
    Rule { text: "libusb", word: false, why: "is a USB library, and a new dependency" },
    Rule { text: "rusb", word: true, why: "is a USB library, and a new dependency" },
    Rule { text: "hidapi", word: false, why: "is a new dependency" },
    Rule { text: "/authorized", word: false, why: "deauthorizes the device in sysfs" },
    Rule { text: "/unbind", word: false, why: "unbinds a kernel driver in sysfs" },
    Rule { text: "power/control", word: false, why: "changes autosuspend, which the usb-drop-fix thread owns" },
    // S1: no network, ever.
    Rule { text: "AF_INET", word: true, why: "opens a network socket" },
    Rule { text: "AF_INET6", word: true, why: "opens a network socket" },
    Rule { text: "AF_PACKET", word: true, why: "opens a raw network socket" },
    Rule { text: "getaddrinfo", word: true, why: "resolves a name over the network" },
    Rule { text: "gethostbyname", word: true, why: "resolves a name over the network" },
    Rule { text: "std::net", word: false, why: "is the network API" },
    Rule { text: "TcpStream", word: true, why: "is the network API" },
    Rule { text: "TcpListener", word: true, why: "is the network API" },
    Rule { text: "UdpSocket", word: true, why: "is the network API" },
    Rule { text: "ToSocketAddrs", word: true, why: "resolves a name over the network" },
    Rule { text: "libc::socket", word: true, why: "opens a socket; the driver needs none" },
    Rule { text: "libc::connect", word: true, why: "opens a connection" },
    Rule { text: "SYS_socket", word: true, why: "opens a socket around libc" },
    Rule { text: "SYS_connect", word: true, why: "opens a connection around libc" },
    // S5: no VITURE SDK (licence not accepted), nothing loaded the build cannot see.
    Rule { text: "libglasses", word: false, why: "is the VITURE SDK" },
    Rule { text: "libcarina", word: false, why: "is the VITURE SDK" },
    Rule { text: "viture_device", word: false, why: "is a VITURE SDK header" },
    Rule { text: "viture_glasses_provider", word: false, why: "is a VITURE SDK header" },
    Rule { text: "viture_protocol", word: false, why: "is a VITURE SDK header" },
    Rule { text: "xr_device_provider", word: false, why: "is the VITURE SDK API" },
    Rule { text: "dlopen", word: true, why: "loads a library the build cannot see" },
    Rule { text: "libloading", word: true, why: "loads a library the build cannot see" },
    // S3: this driver has no business asking the display mode.
    Rule { text: "get_display_mode", word: false, why: "is the command the drops point at" },
    // IMU only until 6DoF passes its own soak.
    Rule { text: "VIDIOC_STREAMON", word: true, why: "starts a camera stream" },
    // S6: it publishes a feed, and must not be able to move the mouse.
    Rule { text: "uinput", word: false, why: "can drive the mouse or keyboard" },
    Rule { text: "/dev/input", word: false, why: "reads or drives input devices" },
];

fn findings_in(file: &str, text: &str) -> Vec<String> {
    let code = strip_comments(text);
    let mut found = Vec::new();
    for (n, line) in code.lines().enumerate() {
        for rule in FORBIDDEN {
            let mut from = 0;
            while let Some(at) = line[from..].find(rule.text) {
                let at = from + at;
                let end = at + rule.text.len();
                let before = at.checked_sub(1).map(|k| line.as_bytes()[k]);
                let after = line.as_bytes().get(end).copied();
                let starts_ident = rule.text.as_bytes().first().is_some_and(|c| is_ident(*c));
                let ends_ident = rule.text.as_bytes().last().is_some_and(|c| is_ident(*c));
                let whole = (!starts_ident || before.is_none_or(|c| !is_ident(c)))
                    && (!ends_ident || after.is_none_or(|c| !is_ident(c)));
                if !rule.word || whole {
                    found.push(format!("{file}:{}: {} {}", n + 1, rule.text, rule.why));
                }
                from = end;
            }
        }
        // S2: one ioctl call site, the one `is_allowed_ioctl` guards. S1: raw syscalls only for the futex (the rt parker)
        // and sched_setattr (SCHED_DEADLINE in rt/policy.rs, which libc does not wrap).
        if line.contains("libc::ioctl") && file != "xr-luma-driver/src/usbdevfs.rs" {
            found.push(format!("{file}:{}: libc::ioctl outside usbdevfs.rs, past is_allowed_ioctl", n + 1));
        }
        let mut from = 0;
        while let Some(at) = line[from..].find("libc::SYS_") {
            let name: String = line[from + at + 6..].bytes().take_while(|c| is_ident(*c)).map(char::from).collect();
            if name != "SYS_futex" && name != "SYS_sched_setattr" {
                found.push(format!("{file}:{}: {name} is a raw syscall", n + 1));
            }
            from += at + 6;
        }
    }
    found
}

/// Everything linked into `xr-luma-driver`: this crate's library and binary, and xr-pose. `src/bin/` (the bench) is not.
fn driver_sources() -> Vec<(String, String)> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<(String, String)>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                if path.file_name().is_some_and(|n| n == "bin") {
                    continue;
                }
                walk(root, &path, out);
            } else if path.extension().is_some_and(|e| e == "rs") {
                let name = path.strip_prefix(root).unwrap().display().to_string();
                out.push((name, fs::read_to_string(&path).unwrap()));
            }
        }
    }
    let crates = Path::new(CRATE).parent().unwrap();
    let mut out = Vec::new();
    walk(crates, &crates.join("xr-luma-driver/src"), &mut out);
    walk(crates, &crates.join("xr-pose/src"), &mut out);
    out
}

#[test]
fn scanner_self_test() {
    let count = |s: &str| findings_in("f.rs", s).len();
    assert_eq!(count("let s = libc::socket(libc::AF_INET, 1, 0);"), 2, "a socket call is found");
    assert_eq!(count("// we never use AF_INET or uinput\n"), 0, "a line comment is not a finding");
    assert_eq!(count("/// never /dev/uinput\nfn f() {}"), 0, "a doc comment is not a finding");
    assert_eq!(count("/* outer /* AF_INET */ uinput */ let x = 1;"), 0, "a nested block comment is not a finding");
    assert_eq!(count("/* c */ let p = \"/dev/uinput\";"), 1, "code after a block comment is scanned, strings stay");
    assert_eq!(count("let s = \"// not a comment\"; let p = \"/dev/uinput\";"), 1, "// in a string is not a comment");
    assert_eq!(count("let s = \"a\\\"b // x\"; std::net::TcpStream::connect(a);"), 2, "an escaped quote ends no string");
    assert_eq!(count("let s = r#\"a \" // b\"#; dlopen(x);"), 1, "a raw string hides nothing after it");
    assert_eq!(count("let q = '\"'; dlopen(x);"), 1, "a quote char literal hides nothing");
    assert_eq!(count("fn f<'a>(x: &'a str) { dlopen(x) }"), 1, "a lifetime is not a char literal");
    assert_eq!(count("AF_INET6"), 1, "AF_INET and AF_INET6 are told apart");
    assert_eq!(count("MY_AF_INET_X"), 0, "a word rule needs the whole identifier");
    assert_eq!(count("unsafe { libc::ioctl(fd, 0x5514, 0) }"), 1, "an ioctl outside usbdevfs.rs is found");
    assert_eq!(count("libc::syscall(libc::SYS_futex, a)"), 0, "the futex syscall is allowed");
    assert_eq!(count("libc::syscall(libc::SYS_ioctl, a)"), 1, "another raw syscall is not");
    assert_eq!(findings_in("xr-luma-driver/src/usbdevfs.rs", "libc::ioctl(fd, code, a)").len(), 0);
}

/// S1 no network code, S2 no reset path, S5 no SDK, S6 no uinput: nothing in the driver's sources.
#[test]
fn s1_s2_s5_s6_sources_have_nothing_forbidden() {
    let sources = driver_sources();
    assert!(sources.len() >= 15, "the scan found {} files", sources.len());
    assert!(sources.iter().any(|(f, _)| f == "xr-luma-driver/src/main.rs"));
    let findings: Vec<String> = sources.iter().flat_map(|(f, t)| findings_in(f, t)).collect();
    assert!(findings.is_empty(), "forbidden in the driver's sources:\n{}", findings.join("\n"));
    let ioctl_sites: usize = sources
        .iter()
        .map(|(_, t)| strip_comments(t).matches("libc::ioctl(").count())
        .sum();
    assert_eq!(ioctl_sites, 1, "one ioctl call site, behind is_allowed_ioctl");
}

/// S5: no new dependencies. The plan allows libc and nothing else outside the workspace (no hidapi, no rusb).
#[test]
fn s5_dependencies_are_libc_and_xr_pose_only() {
    let crates = Path::new(CRATE).parent().unwrap();
    for (krate, allowed) in [("xr-luma-driver", &["libc", "xr-pose"][..]), ("xr-pose", &["libc"][..])] {
        let toml = fs::read_to_string(crates.join(krate).join("Cargo.toml")).unwrap();
        let mut section = String::new();
        for line in toml.lines().map(str::trim) {
            if line.starts_with('[') {
                section = line.to_owned();
                continue;
            }
            let Some((name, _)) = line.split_once('=') else { continue };
            let name = name.trim();
            if section == "[dependencies]" || section == "[build-dependencies]" {
                assert!(allowed.contains(&name), "{krate} depends on {name}");
            }
            if section.starts_with("[target") {
                panic!("{krate} has target-specific dependencies: {section}");
            }
        }
    }
}

/// S5: the binary needs only the C runtime (Rust links no C++ runtime at all).
#[test]
fn s5_ldd_shows_only_the_c_runtime() {
    let out = Command::new("ldd").arg(BIN).output().expect("ldd");
    assert!(out.status.success(), "ldd failed: {}", String::from_utf8_lossy(&out.stderr));
    let text = String::from_utf8(out.stdout).unwrap();
    let allowed = ["linux-vdso.so.1", "libc.so.6", "libm.so.6", "libgcc_s.so.1", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"];
    let mut libs = Vec::new();
    for line in text.lines() {
        let name = line.split_whitespace().next().unwrap_or_default();
        let base = name.rsplit('/').next().unwrap();
        assert!(allowed.contains(&base), "ldd lists {base}:\n{text}");
        libs.push(base.to_owned());
    }
    assert!(libs.iter().any(|l| l == "libc.so.6"), "{text}");
}

// ---------------------------------------------------------------------------------------------------------------------
// S3, S4, S7: what goes on the wire.

/// The replay link, with every write kept.
struct Recording<'a> {
    inner: ReplayLink,
    writes: &'a std::cell::RefCell<Vec<Vec<u8>>>,
}

impl Link for Recording<'_> {
    fn open(&mut self) -> Result<(), String> {
        self.inner.open()
    }
    fn read(&mut self, into: &mut [u8], timeout: Duration) -> ReadResult {
        self.inner.read(into, timeout)
    }
    fn write(&mut self, bytes: &[u8]) -> bool {
        self.writes.borrow_mut().push(bytes.to_vec());
        self.inner.write(bytes)
    }
    fn close(&mut self) {
        self.inner.close();
    }
    fn wait_for_device(&mut self, timeout: Duration) -> bool {
        self.inner.wait_for_device(timeout)
    }
    fn exhausted(&self) -> bool {
        self.inner.exhausted()
    }
}

struct NullSink;
impl Sink for NullSink {
    fn publish(&mut self, _: &PoseOut) {}
    fn set_stale(&mut self, _: bool) {}
}

/// The binary's parts for `mode`, over a capture with one drop: what did it send?
fn sent_over_a_drop(mode: Start) -> (Vec<Vec<u8>>, u64) {
    let dir = scratch("wire");
    // Silent for 700 ms after each connection, past `listen_first` and the settle wait after a drop: a streaming device gets no start.
    let records = xr_luma_driver::capture::load_capture(&capture_with_silence(&dir, Some(3000), 700)).unwrap();
    let writes = std::cell::RefCell::new(Vec::new());
    let replay = ReplayLink::new(records, false);
    let clock = replay.clock();
    let mut link = Recording { inner: replay, writes: &writes };
    let mut decoder = LumaDecoder::default();
    let mut sink = NullSink;
    let connections;
    {
        let parts = DaemonParts {
            link: &mut link,
            decoder: &mut decoder,
            make_estimator: Box::new(|| Box::new(Fusion::new(FusionConfig::default())) as Box<dyn Estimator>),
            sink: &mut sink,
            now_ns: Box::new(move || clock.get()),
            log: Box::new(|_| {}),
            start_command: wiring::start_for(mode),
            stop_command: wiring::stop_command(),
            before_start: wiring::before_start(mode),
            on_link: None,
            on_tick: None,
        };
        let mut daemon = Daemon::new(parts, DaemonConfig::default());
        daemon.run(&AtomicBool::new(false));
        connections = daemon.stats().connections;
    }
    (writes.into_inner(), connections)
}

/// S3: the start goes once per connection, and nothing else: no 0x0007, and no stop (none is known for the Ultra).
#[test]
fn s3_start_once_per_connection_and_nothing_else() {
    let (sent, connections) = sent_over_a_drop(Start::Stream);
    assert_eq!(connections, 2, "the capture's drop makes a second connection");
    assert_eq!(sent, vec![vec![0xfa, 0x55, 0x95, 0, 0]; 2]);
    assert!(wiring::stop_command().is_empty());
}

/// S4: every frame any start mode can send is one the transport lets through; the data channel's only request is 0x100B.
#[test]
fn s4_every_command_is_an_allowed_frame() {
    for mode in [Start::Listen, Start::Stream, Start::Prepared, Start::Full] {
        let mut all = wiring::before_start(mode);
        all.push(wiring::start_for(mode));
        for frame in all.into_iter().filter(|f| !f.is_empty()) {
            if frame[0] == 0xff {
                assert_eq!(u16::from_le_bytes([frame[14], frame[15]]), 0x100b, "{mode:?}");
                assert_eq!(mode, Start::Full);
            } else {
                assert!(allowed_mcu_frame(&frame), "{mode:?} sends {frame:02x?}");
            }
            let id = u16::from_le_bytes([frame[14 % frame.len()], frame[15 % frame.len()]]);
            assert!(frame[0] != 0xff || id != 0x0007, "MsgID 0x0007 (get_display_mode) is never sent");
        }
    }
    let (sent, _) = sent_over_a_drop(Start::Prepared);
    assert_eq!(sent.len(), 4, "0x97 then 0x95 per connection: {sent:02x?}");
}

/// S7: listen sends nothing at all, and optional features the Rust driver does not have yet are refused, not run.
#[test]
fn s7_listen_sends_nothing_and_unported_features_are_refused() {
    let (sent, connections) = sent_over_a_drop(Start::Listen);
    assert!(connections >= 1);
    assert!(sent.is_empty(), "listen sent {sent:02x?}");
    let dir = scratch("s7");
    let cap = capture(&dir, None);
    for (extra, code, said) in [
        (&["--feature-temperature"][..], 2, "luma: error reason=feature --feature-temperature is not in the Rust driver yet"),
        (&["--latency-log", "x.bin"][..], 4, "luma: error reason=latency-log is not in the Rust driver yet"),
    ] {
        let mut child = replay(&dir, &cap, false).args(extra).spawn().unwrap();
        let status = exit_within(&mut child, Duration::from_secs(5));
        assert_eq!(status.code(), Some(code), "{extra:?}");
        assert!(stderr_of(&mut child).contains(said), "{extra:?}");
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// S1, S2, S6 on the running binary. No strace here: instead a seccomp filter kills the process on the first network
// syscall or forbidden usbdevfs ioctl, which is stronger than reading a trace afterwards.

#[cfg(target_arch = "x86_64")]
mod filter {
    // <linux/seccomp.h>, <linux/filter.h>, <linux/audit.h>
    const AUDIT_ARCH_X86_64: u32 = 0xc000_003e;
    const RET_KILL_PROCESS: u32 = 0x8000_0000;
    const RET_ALLOW: u32 = 0x7fff_0000;
    const LD_W_ABS: u16 = 0x20;
    const JEQ_K: u16 = 0x15;
    const RET_K: u16 = 0x06;

    /// Every syscall that makes or uses a socket on x86_64.
    pub const NETWORK: &[(i64, &str)] = &[
        (libc::SYS_socket, "socket"),
        (libc::SYS_socketpair, "socketpair"),
        (libc::SYS_connect, "connect"),
        (libc::SYS_bind, "bind"),
        (libc::SYS_listen, "listen"),
        (libc::SYS_accept, "accept"),
        (libc::SYS_accept4, "accept4"),
        (libc::SYS_sendto, "sendto"),
        (libc::SYS_sendmsg, "sendmsg"),
        (libc::SYS_sendmmsg, "sendmmsg"),
        (libc::SYS_recvfrom, "recvfrom"),
        (libc::SYS_recvmsg, "recvmsg"),
        (libc::SYS_recvmmsg, "recvmmsg"),
    ];
    /// usbdevfs RESETEP, SETINTERFACE, SETCONFIGURATION, RESET, CLEAR_HALT, DISCONNECT, DISCONNECT_CLAIM.
    pub const RESETS: &[u32] = &[0x8004_5503, 0x8008_5504, 0x8004_5505, 0x5514, 0x8004_5515, 0x5516, 0x8108_551b];

    fn stmt(code: u16, k: u32) -> libc::sock_filter {
        libc::sock_filter { code, jt: 0, jf: 0, k }
    }
    fn jeq(k: u32, jt: u8) -> libc::sock_filter {
        libc::sock_filter { code: JEQ_K, jt, jf: 0, k }
    }

    pub fn program() -> Vec<libc::sock_filter> {
        let mut p = vec![stmt(LD_W_ABS, 4), libc::sock_filter { code: JEQ_K, jt: 1, jf: 0, k: AUDIT_ARCH_X86_64 }];
        p.push(stmt(RET_K, RET_KILL_PROCESS));
        p.push(stmt(LD_W_ABS, 0));
        for &(nr, _) in NETWORK {
            p.push(jeq(nr as u32, 0));
            p.last_mut().unwrap().jt = 0;
            p.last_mut().unwrap().jf = 1;
            p.push(stmt(RET_K, RET_KILL_PROCESS));
        }
        // ioctl: look at the request (args[1], low word at offset 24).
        p.push(libc::sock_filter { code: JEQ_K, jt: 0, jf: (2 * RESETS.len() + 1) as u8, k: libc::SYS_ioctl as u32 });
        p.push(stmt(LD_W_ABS, 24));
        for &code in RESETS {
            p.push(libc::sock_filter { code: JEQ_K, jt: 0, jf: 1, k: code });
            p.push(stmt(RET_K, RET_KILL_PROCESS));
        }
        p.push(stmt(RET_K, RET_ALLOW));
        let _ = jeq;
        p
    }

    /// For `pre_exec`: from here on, a forbidden call ends the process with SIGSYS.
    pub fn install(program: &[libc::sock_filter]) -> std::io::Result<()> {
        let prog = libc::sock_fprog { len: program.len() as u16, filter: program.as_ptr().cast_mut() };
        // SAFETY: prctl and seccomp with a valid program that outlives the calls.
        unsafe {
            if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0
                || libc::syscall(libc::SYS_seccomp, 1 /* SECCOMP_SET_MODE_FILTER */, 0, &prog) != 0
            {
                return Err(std::io::Error::last_os_error());
            }
        }
        Ok(())
    }
}

#[cfg(target_arch = "x86_64")]
fn filtered(mut c: Command) -> Command {
    let program = filter::program();
    // SAFETY: `install` makes two syscalls and allocates nothing, so it is safe between fork and exec.
    unsafe { c.pre_exec(move || filter::install(&program)) };
    c
}

/// The filter itself works: a process that opens a socket under it is killed.
#[cfg(target_arch = "x86_64")]
#[test]
fn seccomp_filter_kills_a_socket_call() {
    let mut c = Command::new("/usr/bin/python3");
    c.args(["-c", "import socket; socket.socket(); print('alive')"]).stdout(Stdio::piped()).stderr(Stdio::null());
    let out = filtered(c).output().unwrap();
    assert_eq!(out.status.signal(), Some(libc::SIGSYS), "{:?}", out);
    assert!(out.stdout.is_empty());
    let mut ok = Command::new("/usr/bin/true");
    ok.stdout(Stdio::null());
    assert!(filtered(ok).status().unwrap().success(), "an ordinary process is left alone");
}

/// S1, S2: a whole replay, with a drop, runs to its end under the filter: no socket call and no reset ioctl, ever.
#[cfg(target_arch = "x86_64")]
#[test]
fn s1_s2_replay_makes_no_network_or_reset_syscall() {
    let dir = scratch("seccomp");
    let cap = capture(&dir, Some(3000));
    let mut child = filtered(replay(&dir, &cap, false)).spawn().unwrap();
    let status = exit_within(&mut child, Duration::from_secs(60));
    let log = stderr_of(&mut child);
    assert_eq!(status.signal(), None, "killed by the seccomp filter:\n{log}");
    assert_eq!(status.code(), Some(0), "{log}");
    assert!(log.contains("luma: calibrated"), "{log}");
    assert!(log.contains("luma: replay process_us"), "{log}");
    assert!(!log.contains("luma: error"), "{log}");
}

/// S1, S6: inside `unshare --user --net` as the supervisor runs it, the process has only a down `lo`, no socket, no
/// uinput and no USB device open.
#[test]
fn s1_s6_running_process_has_no_network_and_no_input_device() {
    let dir = scratch("proc");
    let cap = capture(&dir, None);
    let mut c = Command::new("unshare");
    c.args(["--user", "--net", "--"]).arg(BIN);
    c.arg("--replay").arg(&cap).arg("--ipc-prefix").arg(prefix(&dir)).arg("--replay-realtime");
    c.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    let mut child = c.spawn().unwrap();
    wait_running(&dir, &mut child);
    // unshare execs the driver in place, so the child's pid is the driver's.
    let pid = child.id();
    let exe = fs::read_link(format!("/proc/{pid}/exe")).unwrap();
    assert_eq!(exe.file_name().unwrap(), "xr-luma-driver");

    let dev = fs::read_to_string(format!("/proc/{pid}/net/dev")).unwrap();
    let ifaces: Vec<&str> = dev.lines().skip(2).filter_map(|l| l.split(':').next()).map(str::trim).collect();
    assert_eq!(ifaces, ["lo"], "{dev}");
    for table in ["tcp", "tcp6", "udp", "udp6", "raw", "raw6"] {
        let t = fs::read_to_string(format!("/proc/{pid}/net/{table}")).unwrap();
        assert_eq!(t.lines().count(), 1, "{table} has entries in the driver's namespace:\n{t}");
    }
    let link = Command::new("nsenter")
        .args(["--preserve-credentials", "-U", "-n", "-t", &pid.to_string(), "ip", "-o", "link", "show", "lo"])
        .output()
        .unwrap();
    let link = String::from_utf8_lossy(&link.stdout).into_owned();
    assert!(link.contains("state DOWN") && !link.contains(",UP"), "lo is not down: {link}");

    let mut fds = Vec::new();
    for entry in fs::read_dir(format!("/proc/{pid}/fd")).unwrap() {
        if let Ok(target) = fs::read_link(entry.unwrap().path()) {
            fds.push(target.display().to_string());
        }
    }
    for fd in &fds {
        assert!(!fd.starts_with("socket:"), "an open socket: {fds:?}");
        assert!(!fd.contains("uinput") && !fd.starts_with("/dev/input"), "an input device: {fds:?}");
        assert!(!fd.starts_with("/dev/bus/usb"), "a replay holds a USB device: {fds:?}");
    }
    signal(&child, libc::SIGTERM);
    let status = exit_within(&mut child, Duration::from_secs(2));
    assert_eq!(status.code(), Some(0), "{}", stderr_of(&mut child));
}

// ---------------------------------------------------------------------------------------------------------------------
// S8, S9: one driver at a time; a clean stop; a kill -9 leaves nothing held.

/// S8: a second copy on the same feed refuses to start, exit 5, and never opens the feed.
#[test]
fn s8_second_copy_refuses_to_start() {
    let dir = scratch("s8");
    let cap = capture(&dir, None);
    let mut first = replay(&dir, &cap, true).spawn().unwrap();
    wait_running(&dir, &mut first);
    let mut second = replay(&dir, &cap, true).spawn().unwrap();
    let status = exit_within(&mut second, Duration::from_secs(5));
    let log = stderr_of(&mut second);
    assert_eq!(status.code(), Some(5), "{log}");
    assert!(log.starts_with("luma: error reason=already-running at="), "{log}");
    assert_eq!(log.lines().count(), 1, "the second copy did nothing else: {log}");
    assert!(first.try_wait().unwrap().is_none(), "the first copy runs on");
    signal(&first, libc::SIGTERM);
    assert_eq!(exit_within(&mut first, Duration::from_secs(2)).code(), Some(0));
}

/// S9: SIGTERM ends a run at once, with exit 0 and no error.
#[test]
fn s9_sigterm_stops_cleanly() {
    let dir = scratch("s9");
    let cap = capture(&dir, None);
    for sig in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
        let mut child = replay(&dir, &cap, true).spawn().unwrap();
        wait_running(&dir, &mut child);
        let sent = Instant::now();
        signal(&child, sig);
        let status = exit_within(&mut child, Duration::from_secs(1));
        let log = stderr_of(&mut child);
        assert_eq!(status.code(), Some(0), "signal {sig}: {log}");
        assert!(sent.elapsed() < Duration::from_millis(500), "took {:?}", sent.elapsed());
        assert!(!log.contains("luma: error"), "{log}");
    }
}

/// S9: after kill -9 nothing is left held: the lock comes free with the process, so the next copy starts.
#[test]
fn s9_kill_9_leaves_the_lock_free() {
    let dir = scratch("kill9");
    let cap = capture(&dir, None);
    let mut child = replay(&dir, &cap, true).spawn().unwrap();
    wait_running(&dir, &mut child);
    signal(&child, libc::SIGKILL);
    assert_eq!(exit_within(&mut child, Duration::from_secs(1)).signal(), Some(libc::SIGKILL));
    // A replay makes private segments and removes them as it closes; kill -9 leaves them (the live feed's are shared and
    // reused, so only a test prefix has this). Remove them, then the next copy goes on the same prefix: the lock is free.
    remove_private_segments(&dir);
    let mut again = replay(&dir, &cap, false).spawn().unwrap();
    let status = exit_within(&mut again, Duration::from_secs(60));
    let log = stderr_of(&mut again);
    assert!(!log.contains("already-running"), "{log}");
    assert_eq!(status.code(), Some(0), "{log}");
}

/// The SysV segments behind the feed files a killed replay left, keyed as xr-pose keys them (`ftok(path, 0)`).
fn remove_private_segments(dir: &Path) {
    for entry in fs::read_dir(dir).unwrap().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("feed_") || name.ends_with(".lock") {
            continue;
        }
        let path = std::ffi::CString::new(entry.path().as_os_str().as_encoded_bytes()).unwrap();
        // SAFETY: ftok, shmget and shmctl with a NUL-terminated path and checked results.
        unsafe {
            let key = libc::ftok(path.as_ptr(), 0);
            let id = if key == -1 { -1 } else { libc::shmget(key, 0, 0) };
            if id >= 0 {
                libc::shmctl(id, libc::IPC_RMID, std::ptr::null_mut());
            }
        }
        fs::remove_file(entry.path()).unwrap();
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// S10: unprivileged.

/// S10: the running driver holds no capability at all.
fn assert_no_capabilities(child: &Child) {
    let status = fs::read_to_string(format!("/proc/{}/status", child.id())).unwrap();
    for field in ["Uid:", "CapEff:", "CapPrm:", "CapAmb:"] {
        let line = status.lines().find(|l| l.starts_with(field)).unwrap();
        if field == "Uid:" {
            assert!(line.split_whitespace().skip(1).all(|u| u != "0"), "{line}");
        } else {
            assert!(line.ends_with("0000000000000000"), "{line}");
        }
    }
}

fn viture_plugged_in() -> bool {
    let Ok(entries) = fs::read_dir("/sys/bus/usb/devices") else { return true };
    entries.flatten().any(|e| fs::read_to_string(e.path().join("idVendor")).is_ok_and(|v| v.trim() == "35ca"))
}

/// S10: a replay, as the tests run it, has no root uid and no capability.
#[test]
fn s10_running_driver_has_no_privilege() {
    let dir = scratch("s10");
    let cap = capture(&dir, None);
    let mut child = replay(&dir, &cap, true).spawn().unwrap();
    wait_running(&dir, &mut child);
    assert_no_capabilities(&child);
    signal(&child, libc::SIGTERM);
    assert_eq!(exit_within(&mut child, Duration::from_secs(2)).code(), Some(0));
}

/// S10: a live run as this user, without sudo or capabilities: it scans for the glasses, says it is waiting, and stops on
/// SIGTERM. Skipped when glasses are plugged in, so a live session is never touched.
#[test]
fn s10_live_run_is_unprivileged_and_waits_quietly() {
    // SAFETY: geteuid has no preconditions.
    assert_ne!(unsafe { libc::geteuid() }, 0, "run the audit as a normal user");
    let caps = Command::new("getcap").arg(BIN).output().unwrap();
    assert!(caps.stdout.is_empty(), "the binary has file capabilities: {}", String::from_utf8_lossy(&caps.stdout));
    if viture_plugged_in() {
        eprintln!("skipped: a VITURE device is plugged in, and this test must not touch it");
        return;
    }
    let dir = scratch("live");
    let mut c = Command::new(BIN);
    // Its own state directory too, so the saved gyro bias of the real glasses is never read or rewritten.
    c.arg("--ipc-prefix").arg(prefix(&dir)).arg("--state-dir").arg(&dir).arg("--start").arg("listen");
    c.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    #[cfg(target_arch = "x86_64")]
    let mut c = filtered(c);
    let mut child = c.spawn().unwrap();
    wait_running(&dir, &mut child);
    assert_no_capabilities(&child);
    std::thread::sleep(Duration::from_millis(700));
    signal(&child, libc::SIGTERM);
    let status = exit_within(&mut child, Duration::from_secs(2));
    let log = stderr_of(&mut child);
    assert_eq!(status.code(), Some(0), "{log}");
    assert!(log.contains("luma: waiting"), "{log}");
    assert!(!log.contains("luma: error"), "{log}");
}
