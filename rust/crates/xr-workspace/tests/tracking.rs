//! The tracking wrappers against fake drivers, as `tests/native_spike/test_tracking_luma.py` does:
//! real namespaces, real processes, a temporary tree for every path. Nothing here looks at the
//! real state file, `/proc`'s real drivers or the glasses.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[path = "support/script.rs"]
mod script;

use xr_workspace::supervisor::luma_watch::{self, End, LumaWatch};
use xr_workspace::supervisor::survive;
use xr_workspace::supervisor::tracking::{Error, Kind, Paths, Timing, Tracking, Want};

const CALIBRATES: &str = "trap 'exit 0' TERM\necho 'luma: starting' >&2\nsleep 0.2\necho 'luma: calibrated' >&2\nwhile :; do sleep 0.1; done\n";
const NEVER_CALIBRATES: &str = "trap 'exit 0' TERM\necho 'luma: calibrating' >&2\necho 'luma: waiting reason=imu' >&2\nwhile :; do sleep 0.1; done\n";
const EXITS: &str = "echo 'luma: no glasses' >&2\nexit 3\n";
const ARGS: &str = "trap 'exit 0' TERM\necho \"args: $*\" >&2\necho 'luma: calibrated' >&2\nwhile :; do sleep 0.1; done\n";
const ALWAYS_CRASHES: &str = "echo 'luma: calibrated restored=0' >&2\nsleep 0.5\nkill -9 $$\n";

struct Tree {
    root: PathBuf,
    tracking: Tracking,
}

impl Tree {
    fn new(name: &str) -> Self {
        let root = std::env::temp_dir().join(format!("xr-workspace-it-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        for d in ["rt", "proc", "sys", "dev", "data", "config", "state"] {
            fs::create_dir_all(root.join(d)).unwrap();
        }
        fs::set_permissions(root.join("rt"), fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(root.join("driver.log"), "").unwrap();
        fs::write(root.join("config.ini"), "disabled=false\noutput_mode=external_only\n").unwrap();
        let paths = Paths {
            runtime: root.join("rt"),
            driver: root.join("fake-xrdriver"),
            driver_log: root.join("driver.log"),
            driver_config: root.join("config.ini"),
            luma_log: root.join("state/luma-driver.log"),
            luma_flags: root.join("config/luma-driver-flags"),
            recovery_config: root.join("config/tracking.json"),
            quiet_settings: root.join("config/quiet-driver.json"),
            data_home: root.join("data"),
            sys_usb: root.join("sys"),
            dev_usb: root.join("dev"),
            proc: root.join("proc"),
        };
        let timing = Timing { start: 10.0, luma_timeout: 10.0, luma_settle: 0.5, reconnect_wait: 0.5, stop: 2.0 };
        Tree { root, tracking: Tracking { paths, timing } }
    }

    fn driver(&self, name: &str, body: &str) -> PathBuf {
        let p = self.root.join(name);
        script::write_script(&p, &format!("#!/bin/sh\n{body}"));
        p
    }

    fn state(&self) -> Option<String> {
        fs::read_to_string(self.tracking.paths.runtime.join("viture-workspace/tracking.json")).ok()
    }

    fn log(&self) -> String {
        fs::read_to_string(&self.tracking.paths.luma_log).unwrap_or_default()
    }

    /// Processes started from this tree.
    fn running(&self) -> Vec<i32> {
        let mut found = Vec::new();
        for entry in fs::read_dir("/proc").unwrap().flatten() {
            let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<i32>().ok()) else { continue };
            if self.started(pid) {
                found.push(pid);
            }
        }
        found
    }

    /// Whether `pid` is live and has an argument that is the root or a path under it. A bare
    /// prefix would also match a sibling run's `-{pid}N` root, and Drop would SIGKILL its drivers.
    fn started(&self, pid: i32) -> bool {
        let mine = self.root.as_os_str().as_encoded_bytes();
        let inside = |a: &[u8]| a.strip_prefix(mine).is_some_and(|rest| rest.is_empty() || rest[0] == b'/');
        let Ok(cmd) = fs::read(format!("/proc/{pid}/cmdline")) else { return false };
        let zombie = fs::read_to_string(format!("/proc/{pid}/stat")).is_ok_and(|s| s.rsplit_once(") ").is_some_and(|(_, r)| r.starts_with('Z')));
        !zombie && cmd.split(|&b| b == 0).any(inside)
    }
}

impl Drop for Tree {
    /// A driver that outlives its test is a lifecycle bug: fail, and leave nothing behind.
    fn drop(&mut self) {
        let _ = self.tracking.stop();
        let left = self.running();
        for &pid in &left {
            // Signal through a pidfd checked again after it is open: a listed driver that exited
            // since may have handed its pid to another run's process.
            let Some(fd) = rustix::process::Pid::from_raw(pid).and_then(|p| rustix::process::pidfd_open(p, rustix::process::PidfdFlags::empty()).ok()) else { continue };
            if self.started(pid) {
                let _ = rustix::process::pidfd_send_signal(&fd, rustix::process::Signal::KILL);
            }
        }
        let _ = fs::remove_dir_all(&self.root);
        if !std::thread::panicking() {
            assert_eq!(left, Vec::<i32>::new(), "drivers left running");
        }
    }
}

#[test]
fn a_luma_driver_starts_isolated_calibrates_and_stops() {
    let t = Tree::new("lifecycle");
    let luma = t.driver("fake-luma", CALIBRATES);
    let s = t.tracking.start(&Want::Luma(luma.clone()), 10.0).unwrap();
    assert!(s.running && s.calibrated && s.network_isolated, "{s:?}");
    assert_eq!((s.driver, s.binary.as_str()), (Kind::Luma, luma.to_str().unwrap()));
    // The frozen format, byte for byte what Python's json.dump writes.
    let tick = xr_workspace::supervisor::tracking::start_tick(s.pid).unwrap();
    assert_eq!(
        t.state().unwrap(),
        format!(r#"{{"pid": {}, "start_tick": "{tick}", "log_offset": 0, "driver": "luma", "binary": "{}"}}"#, s.pid, luma.display())
    );
    assert_eq!(t.tracking.active_driver(), Some(Kind::Luma));
    assert!(matches!(t.tracking.start(&Want::Luma(luma.clone()), 10.0), Err(Error::Refused(m)) if m == "tracking already running"));
    assert!(t.tracking.stop().unwrap());
    assert!(t.state().is_none());
    assert!(!t.tracking.status().running);
    assert!(!t.tracking.stop().unwrap());
    assert!(t.running().is_empty());
}

#[test]
fn ensure_ready_reuses_a_calibrated_driver_and_swaps_another() {
    let t = Tree::new("reuse");
    let a = t.driver("fake-luma-a", CALIBRATES);
    let b = t.driver("fake-luma-b", CALIBRATES);
    let first = t.tracking.ensure_ready(&Want::Luma(a.clone()), 10.0).unwrap();
    assert_eq!(t.tracking.ensure_ready(&Want::Luma(a), 10.0).unwrap().pid, first.pid);
    let swapped = t.tracking.ensure_ready(&Want::Luma(b.clone()), 10.0).unwrap();
    assert_ne!(swapped.pid, first.pid);
    assert_eq!(swapped.binary, b.to_str().unwrap());
    assert_eq!(t.running(), [swapped.pid]);
}

#[test]
fn a_driver_that_exits_or_never_calibrates_leaves_nothing() {
    let t = Tree::new("fails");
    let exits = t.driver("fake-exits", EXITS);
    let e = t.tracking.start(&Want::Luma(exits), 10.0).unwrap_err().to_string();
    assert!(e.contains("status 3"), "{e}");
    assert!(t.state().is_none());

    let never = t.driver("fake-never", NEVER_CALIBRATES);
    let e = t.tracking.start(&Want::Luma(never.clone()), 1.0).unwrap_err().to_string();
    assert_eq!(e, "tracking driver did not calibrate in time (keep the glasses still while it starts), last waiting on imu");
    assert!(t.state().is_none());
    assert!(t.running().is_empty());
    // ensure_ready_luma puts it in words for the person.
    let mut quick = Tree::new("fails-luma");
    quick.tracking.timing.luma_timeout = 1.0;
    let never = quick.driver("fake-never", NEVER_CALIBRATES);
    let e = quick.tracking.ensure_ready_luma(&never).unwrap_err().to_string();
    assert!(e.starts_with("Head tracking did not start: tracking driver did not calibrate in time"), "{e}");
}

#[test]
fn saved_flags_reach_the_driver_and_bad_ones_open_no_log() {
    let t = Tree::new("flags");
    let luma = t.driver("fake-args", ARGS);
    fs::write(&t.tracking.paths.luma_flags, "--start prepared\n--read-events\n").unwrap();
    t.tracking.start(&Want::Luma(luma.clone()), 10.0).unwrap();
    assert!(t.log().contains("args: --start prepared --read-events"), "{}", t.log());
    t.tracking.stop().unwrap();

    let t = Tree::new("badflags");
    fs::write(&t.tracking.paths.luma_flags, "--start sideways\n").unwrap();
    let luma = t.driver("fake-args", ARGS);
    assert!(t.tracking.start(&Want::Luma(luma), 10.0).is_err());
    assert!(!t.tracking.paths.luma_log.exists());
}

#[test]
fn another_driver_or_a_pro2_refuses_the_start() {
    let t = Tree::new("refuse");
    let luma = t.driver("fake-luma", CALIBRATES);
    let other = t.tracking.paths.proc.join("4242");
    fs::create_dir(&other).unwrap();
    fs::write(other.join("cmdline"), b"/usr/bin/xr-luma-driver\0--start\0stream\0").unwrap();
    let e = t.tracking.start(&Want::Luma(luma.clone()), 10.0).unwrap_err().to_string();
    assert!(e.starts_with("another head tracking driver is running"), "{e}");
    fs::remove_dir_all(&other).unwrap();

    let pro2 = t.tracking.paths.sys_usb.join("3-1");
    fs::create_dir(&pro2).unwrap();
    fs::write(pro2.join("idVendor"), "35ca\n").unwrap();
    fs::write(pro2.join("idProduct"), "1301\n").unwrap();
    assert!(matches!(t.tracking.start(&Want::Luma(luma), 10.0), Err(Error::Unsupported(_))));
    assert!(t.running().is_empty());
}

#[test]
fn the_standard_xrdriver_gets_the_pinned_quiet_shim_and_another_build_does_not() {
    let t = Tree::new("xrdriver");
    let log = t.tracking.paths.driver_log.clone();
    let body = format!(
        "trap 'exit 0' TERM\necho \"env: ${{LD_PRELOAD:-none}} ${{XR_QUIET_DOF:-}} ${{XR_QUIET_RECONNECT_HOLD_MS:-}} $(id -u)\" >> {0}\necho 'Device calibration complete' >> {0}\nwhile :; do sleep 0.1; done\n",
        log.display()
    );
    t.driver("fake-xrdriver", &body);
    let other = t.driver("fake-xrdriver-3dof", &body);
    let shim = t.root.join("libquiet.so");
    fs::write(&shim, b"not really a library").unwrap();
    let sha = xr_workspace::supervisor::tracking::sha256_file(&shim).unwrap();
    fs::create_dir_all(t.tracking.paths.data_home.join("viture-workspace")).unwrap();
    fs::write(
        t.tracking.paths.data_home.join("viture-workspace/quiet-driver.pin.json"),
        format!(r#"{{"path": "{}", "sha256": "{sha}"}}"#, shim.display()),
    )
    .unwrap();
    fs::write(&t.tracking.paths.quiet_settings, r#"{"reconnect_hold_ms": 5000, "quiet_polls": 1}"#).unwrap();

    // LD_PRELOAD of a non-library only warns, and the fake script still runs.
    let s = t.tracking.start(&Want::XrDriver(None), 10.0).unwrap();
    assert!(s.running && s.calibrated && s.network_isolated, "{s:?}");
    let uid = unsafe { libc::getuid() };
    let text = fs::read_to_string(&log).unwrap();
    assert!(text.contains(&format!("env: {} 3 5000 {uid}", shim.display())), "{text}");
    t.tracking.stop().unwrap();

    let s = t.tracking.start(&Want::XrDriver(Some(other.clone())), 10.0).unwrap();
    assert_eq!(s.binary, other.to_str().unwrap());
    let text = fs::read_to_string(&log).unwrap();
    assert!(text.lines().last().is_some_and(|l| l == "Device calibration complete"));
    assert!(text.contains(&format!("env: none   {uid}")), "{text}");
    t.tracking.stop().unwrap();

    // A shim changed after its pin: the plain driver.
    fs::write(&shim, b"changed").unwrap();
    assert!(t.tracking.driver_env().is_empty());
}

#[test]
fn luma_watch_brings_a_crash_back_three_times_in_five_minutes_then_says_so() {
    let t = Tree::new("watch");
    let luma = t.driver("fake-crashes", ALWAYS_CRASHES);
    let first = t.tracking.ensure_ready(&Want::Luma(luma.clone()), 10.0).unwrap();
    let notes = Arc::new(Mutex::new(Vec::new()));
    let seen = notes.clone();
    let watched = Tracking { paths: t.tracking.paths.clone(), timing: t.tracking.timing };
    let watch = LumaWatch::spawn(watched, luma, move |m: &str| seen.lock().unwrap().push(m.to_string()), Duration::from_millis(50));
    let begun = Instant::now();
    while notes.lock().unwrap().is_empty() && begun.elapsed() < Duration::from_secs(30) {
        std::thread::sleep(Duration::from_millis(100));
    }
    assert_eq!(watch.finish(), Some(End::GaveUp));
    assert_eq!(*notes.lock().unwrap(), [luma_watch::crash_message(&t.tracking.paths.luma_log.display().to_string())]);
    // The first start and three restarts.
    assert_eq!(t.log().matches("luma: calibrated").count(), 1 + luma_watch::RESTARTS);
    assert_ne!(t.tracking.read_state().unwrap().pid, first.pid);
}

#[test]
fn luma_watch_ends_quietly_on_a_stop() {
    let t = Tree::new("watch-stop");
    let luma = t.driver("fake-luma", CALIBRATES);
    t.tracking.start(&Want::Luma(luma.clone()), 10.0).unwrap();
    let watched = Tracking { paths: t.tracking.paths.clone(), timing: t.tracking.timing };
    let watch = LumaWatch::spawn(watched, luma, |_: &str| panic!("no message"), Duration::from_millis(50));
    std::thread::sleep(Duration::from_millis(200));
    t.tracking.stop().unwrap();
    std::thread::sleep(Duration::from_millis(200));
    assert_eq!(watch.finish(), Some(End::Gone));
}

#[test]
fn a_hold_revives_a_dead_driver_by_its_record_and_leaves_a_live_one() {
    let t = Tree::new("revive");
    let luma = t.driver("fake-luma", CALIBRATES);
    let first = t.tracking.start(&Want::Luma(luma), 10.0).unwrap();
    assert!(survive::revive_tracking(&t.tracking));
    assert_eq!(t.tracking.status().pid, first.pid);
    unsafe { libc::kill(first.pid, libc::SIGKILL) };
    unsafe { libc::waitpid(first.pid, std::ptr::null_mut(), 0) };
    assert!(survive::revive_tracking(&t.tracking));
    let now = t.tracking.status();
    assert!(now.running && now.calibrated && now.pid != first.pid);
    t.tracking.stop().unwrap();
    assert!(!survive::revive_tracking(&t.tracking)); // stopped on purpose: nothing starts
}

/// The other half of the frozen format: the shipped Python reads a driver this started.
#[test]
fn the_python_supervisor_reads_the_state_file() {
    if !Path::new("/usr/bin/python3").exists() {
        eprintln!("skipped: no /usr/bin/python3");
        return;
    }
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let t = Tree::new("python");
    let luma = t.driver("fake-luma", CALIBRATES);
    let s = t.tracking.start(&Want::Luma(luma.clone()), 10.0).unwrap();
    let script = "import sys\nfrom pathlib import Path\nfrom tools.native_spike import tracking\n\
                  tracking.LUMA_LOG = Path(sys.argv[2])\nprint(tracking.status(Path(sys.argv[1])))\n";
    let out = Command::new("/usr/bin/python3")
        .current_dir(&repo)
        .args(["-c", script])
        .arg(&t.tracking.paths.runtime)
        .arg(&t.tracking.paths.luma_log)
        .output()
        .unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert_eq!(
        text.trim(),
        format!("{{'running': True, 'pid': {}, 'network_isolated': True, 'calibrated': True, 'driver': 'luma', 'binary': '{}'}}", s.pid, luma.display())
    );
}
