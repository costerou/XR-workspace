//! The head-tracking driver's lifecycle: a port of `tools/native_spike/tracking.py`.
//!
//! Two drivers run the same way. `xr-luma-driver` (ours, the Luma Ultra's) runs in its own user
//! and network namespaces. XRLinuxDriver (`/usr/bin/xrDriver`, other glasses, or a Luma Ultra
//! through the hidden `tracking-override`) also gets a mount namespace that hides the resolver,
//! a made-up MAC on a `lo` that stays down, and the quiet shim preloaded. Both publish the same
//! SysV feed and share one state file, `$XDG_RUNTIME_DIR/viture-workspace/tracking.json`, whose
//! format is frozen: either supervisor stops, reuses or watches a driver the other started.
//!
//! Only the exact process this app started (pid and kernel start tick) is ever signalled. A Pro 2
//! never runs a driver from here: it has its own SDK bridge (`super::pro2`).

use std::ffi::{CString, OsStr};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read as _, Write as _};
use std::os::unix::ffi::OsStrExt as _;
use std::os::unix::fs::{DirBuilderExt as _, OpenOptionsExt as _};
use std::os::unix::process::{CommandExt as _, ExitStatusExt as _};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

use sha2::{Digest as _, Sha256};

use serde_json::Value;

use crate::json::Json;
use crate::private_file::strict_json;

pub const DRIVER: &str = "/usr/bin/xrDriver";
pub const LUMA_BINARY: &str = "xr-luma-driver";
pub const READY: &str = "Device calibration complete";
/// Readiness belongs to the latest connection, not any earlier calibration.
pub const LOST: [&str; 3] = ["Retrying driver connection", "Waiting on device calibration", "Failed to send USB command"];
pub const LUMA_READY: &str = "luma: calibrated";
pub const LUMA_LOST: [&str; 2] = ["luma: disconnected", "luma: calibrating"];
/// Bytes; a start moves a bigger log aside, keeping one.
pub const LUMA_LOG_MAX: u64 = 1 << 20;
/// Flags the Luma driver may be given, from `luma-driver-flags`. `None`: a flag with no value.
pub const LUMA_FLAG_CHOICES: [(&str, Option<&[&str]>); 3] =
    [("--start", Some(&["stream", "prepared", "full"])), ("--read-events", None), ("--drain-camera", None)];
pub const LOST_TIMEOUT_S: f64 = 60.0;
pub const LOST_TIMEOUT_MAX_S: f64 = 3600.0;
pub const FAKE_MAC: &str = "02:00:00:00:00:01";
pub const FAKE_ADDRESS: &str = "192.0.2.1/32";
pub const RESOLVED_DIR: &str = "/run/systemd/resolve";
pub const GLASSES_USB: (&str, &str) = ("35ca", "1104");
pub const SHIM_PIN: &str = "quiet-driver.pin.json";

/// Shown to Cody as written, in the panel and as an incident report's likely cause.
pub const CRASHED: &str = "The glasses' head tracking couldn't connect and the tracking driver crashed. \
                           Unplug the glasses' USB, wait 10 seconds, plug it back in and try again.";
pub const USB_DENIED: &str =
    "XR can't open the glasses (USB permission). Unplug and replug the glasses' USB, then try again.";
const LUMA_FAILED: &str = "Head tracking did not start: {reason}. XR stopped here and your windows stayed where they were. \
                           Keep the glasses still and press Start again. If it fails again, unplug the glasses' USB, wait 10 seconds and plug it back in.";
/// What the driver logs when it dies trying to reach the glasses; any one of them is enough.
pub const CRASH_LINES: [&str; 4] =
    ["Cannot open device", "jcx start failed", "Failed to send USB command - MsgID: 0x100B", "Segmentation fault occurred"];

/// The two drivers. A state file from before Luma has no `driver` and reads as XRLinuxDriver.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    XrDriver,
    Luma,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::XrDriver => "xrdriver",
            Kind::Luma => "luma",
        }
    }
}

/// What a start asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Want {
    /// The pinned Luma driver at this path.
    Luma(PathBuf),
    /// XRLinuxDriver: the standard one (`None`) or another build, such as the 3DoF test one.
    XrDriver(Option<PathBuf>),
}

#[derive(Debug)]
pub enum Error {
    /// The attached glasses must not run a driver from here (a Pro 2). Never a reason to fall back.
    Unsupported(String),
    /// A refusal or failure with words for the person (Python's ValueError).
    Refused(String),
    Os(io::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Unsupported(s) | Error::Refused(s) => f.write_str(s),
            Error::Os(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Os(e)
    }
}

fn refused(s: impl Into<String>) -> Error {
    Error::Refused(s.into())
}

/// Every place the module reads or writes. `from_env` gives the real ones; tests point them at a
/// temporary tree.
#[derive(Debug, Clone)]
pub struct Paths {
    /// `$XDG_RUNTIME_DIR`: the state file lives in its `viture-workspace`.
    pub runtime: PathBuf,
    /// The standard XRLinuxDriver binary.
    pub driver: PathBuf,
    pub driver_log: PathBuf,
    pub driver_config: PathBuf,
    pub luma_log: PathBuf,
    pub luma_flags: PathBuf,
    /// `tracking.json` in the config dir: the optional `{"lost_timeout_s": seconds}`.
    pub recovery_config: PathBuf,
    pub quiet_settings: PathBuf,
    /// `$XDG_DATA_HOME`, where the installer keeps the quiet shim's pin.
    pub data_home: PathBuf,
    pub sys_usb: PathBuf,
    pub dev_usb: PathBuf,
    /// Searched for other running drivers.
    pub proc: PathBuf,
}

fn xdg(var: &str, home: &Path, fallback: &str) -> PathBuf {
    match std::env::var_os(var) {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => home.join(fallback),
    }
}

impl Paths {
    pub fn from_env(runtime: impl Into<PathBuf>) -> Self {
        let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
        let config = xdg("XDG_CONFIG_HOME", &home, ".config");
        let state = xdg("XDG_STATE_HOME", &home, ".local/state");
        Paths {
            runtime: runtime.into(),
            driver: DRIVER.into(),
            driver_log: home.join(".local/state/xr_driver/driver.log"),
            driver_config: config.join("xr_driver/config.ini"),
            luma_log: state.join("viture-workspace/luma-driver.log"),
            luma_flags: config.join("viture-workspace/luma-driver-flags"),
            recovery_config: config.join("viture-workspace/tracking.json"),
            quiet_settings: config.join("viture-workspace/quiet-driver.json"),
            data_home: xdg("XDG_DATA_HOME", &home, ".local/share"),
            sys_usb: "/sys/bus/usb/devices".into(),
            dev_usb: "/dev/bus/usb".into(),
            proc: "/proc".into(),
        }
    }
}

/// The waits, in seconds. Tests shorten them.
#[derive(Debug, Clone, Copy)]
pub struct Timing {
    /// The default start timeout (XRLinuxDriver can wait for its licence).
    pub start: f64,
    /// Luma has no licence wait: it calibrates in seconds or is not going to.
    pub luma_timeout: f64,
    /// A Luma driver found uncalibrated may be mid-reconnect, which keeps its calibration.
    pub luma_settle: f64,
    /// An uncalibrated XRLinuxDriver gets this long to reconnect (about 3 s) and calibrate (about 15 s).
    pub reconnect_wait: f64,
    pub stop: f64,
}

impl Default for Timing {
    fn default() -> Self {
        Timing { start: 120.0, luma_timeout: 30.0, luma_settle: 5.0, reconnect_wait: 30.0, stop: 5.0 }
    }
}

/// The state file's record of the driver this app started.
#[derive(Debug, Clone, PartialEq)]
pub struct Record {
    pub pid: i32,
    pub start_tick: String,
    pub log_offset: u64,
    /// `None`: a file from before Luma.
    pub driver: Option<String>,
    /// `None`: a file from before the binary was recorded.
    pub binary: Option<String>,
}

impl Record {
    pub fn kind(&self) -> Kind {
        if self.driver.as_deref() == Some("luma") { Kind::Luma } else { Kind::XrDriver }
    }

    fn parse(raw: &[u8]) -> Option<Record> {
        let v = strict_json(raw).ok()?;
        let pid = i32::try_from(v.get("pid")?.as_i64()?).ok()?;
        let start_tick = v.get("start_tick")?.as_str()?.to_string();
        let log_offset = v.get("log_offset").and_then(Value::as_u64).unwrap_or(0);
        let text = |k| v.get(k).and_then(Value::as_str).map(str::to_string);
        Some(Record { pid, start_tick, log_offset, driver: text("driver"), binary: text("binary") })
    }
}

/// What `status` says about the recorded driver.
#[derive(Debug, Clone, PartialEq)]
pub struct Status {
    pub running: bool,
    pub pid: i32,
    pub network_isolated: bool,
    pub calibrated: bool,
    pub driver: Kind,
    pub binary: String,
}

impl Status {
    fn stopped() -> Self {
        Status { running: false, pid: 0, network_isolated: false, calibrated: false, driver: Kind::XrDriver, binary: String::new() }
    }
}

/// The kernel start tick of a process (field 22 of `/proc/<pid>/stat`), which tells a process
/// from a later one given the same pid.
pub fn start_tick(pid: i32) -> Option<String> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(')')?.1.split_whitespace().nth(19).map(str::to_string)
}

fn zombie(pid: i32) -> bool {
    fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()
        .and_then(|s| s.rsplit_once(')').and_then(|(_, rest)| rest.split_whitespace().next().map(|state| state == "Z")))
        .unwrap_or(false)
}

/// `ipc.private_dir(path, create=True)`: the leaf created 0700 if missing, then every component
/// opened no-follow and the leaf checked to be ours and 0700.
fn private_dir(path: &Path) -> io::Result<()> {
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Err(e) if e.kind() != io::ErrorKind::AlreadyExists => return Err(e),
        _ => {}
    }
    crate::private_file::private_dir(&std::path::absolute(path)?).map(drop)
}

fn uid() -> u32 {
    // SAFETY: getuid has no preconditions and cannot fail.
    unsafe { libc::getuid() }
}

fn gid() -> u32 {
    // SAFETY: getgid has no preconditions and cannot fail.
    unsafe { libc::getgid() }
}

fn read_bytes_from(path: &Path, offset: u64) -> String {
    let bytes = fs::read(path).unwrap_or_default();
    let from = usize::try_from(offset).unwrap_or(usize::MAX).min(bytes.len());
    String::from_utf8_lossy(&bytes[from..]).into_owned()
}

fn file_size(path: &Path) -> u64 {
    fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

/// Seconds since `t`.
fn since(t: Instant) -> f64 {
    t.elapsed().as_secs_f64()
}

/// Why a driver that is gone failed, from what it logged since this start.
pub fn exit_failure(offset: u64, log: &Path, code: Option<i32>) -> String {
    let current = read_bytes_from(log, offset);
    if CRASH_LINES.iter().any(|line| current.contains(line)) {
        return CRASHED.into();
    }
    match code {
        Some(code) => format!("tracking driver exited during startup (status {code}); see {}", log.display()),
        None => "tracking driver exited during startup".into(),
    }
}

/// Python's `Popen.returncode`: the exit code, or minus the signal.
fn returncode(status: ExitStatus) -> Option<i32> {
    status.code().or_else(|| status.signal().map(|s| -s))
}

pub struct Tracking {
    pub paths: Paths,
    pub timing: Timing,
}

impl Tracking {
    pub fn new(paths: Paths) -> Self {
        Tracking { paths, timing: Timing::default() }
    }

    fn state_dir(&self) -> PathBuf {
        self.paths.runtime.join("viture-workspace")
    }

    pub fn state_path(&self) -> io::Result<PathBuf> {
        let dir = self.state_dir();
        private_dir(&dir)?;
        Ok(dir.join("tracking.json"))
    }

    pub fn read_state(&self) -> Option<Record> {
        Record::parse(&fs::read(self.state_path().ok()?).ok()?)
    }

    fn write_state(&self, record: &Record) -> io::Result<()> {
        // json.dump(dict(...)) in tracking.start, byte for byte.
        let text = Json::obj([
            ("pid", Json::Int(record.pid.into())),
            ("start_tick", Json::str(record.start_tick.as_str())),
            ("log_offset", Json::Int(i64::try_from(record.log_offset).unwrap_or(i64::MAX))),
            ("driver", Json::str(record.driver.as_deref().unwrap_or("xrdriver"))),
            ("binary", Json::str(record.binary.as_deref().unwrap_or(DRIVER))),
        ])
        .compact();
        let mut f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(self.state_path()?)?;
        f.write_all(text.as_bytes())
    }

    fn remove_state(&self) {
        if let Ok(path) = self.state_path() {
            let _ = fs::remove_file(path);
        }
    }

    /// The recorded process is still the exact driver we started (pid and start tick), and not a corpse.
    /// The runner that started a driver is its parent and is busy with the session, so a crash
    /// leaves a zombie, tick intact.
    pub fn ours(record: Option<&Record>) -> bool {
        record.is_some_and(|r| start_tick(r.pid).as_deref() == Some(r.start_tick.as_str()) && !zombie(r.pid))
    }

    /// Is the driver this app started still that process? (The incident monitor's check.)
    pub fn driver_alive(&self) -> bool {
        Self::ours(self.read_state().as_ref())
    }

    /// Which driver is really running, or None.
    pub fn active_driver(&self) -> Option<Kind> {
        let record = self.read_state();
        Self::ours(record.as_ref()).then(|| record.map(|r| r.kind())).flatten()
    }

    /// The log of the driver we last started, running or not.
    pub fn log_path(&self) -> &Path {
        match self.read_state() {
            Some(r) if r.kind() == Kind::Luma => &self.paths.luma_log,
            _ => &self.paths.driver_log,
        }
    }

    fn network_isolated(&self, pid: i32) -> bool {
        let ns = |p: &str| fs::read_link(p).ok();
        let theirs = ns(&format!("/proc/{pid}/ns/net"));
        if theirs.is_none() || theirs == ns("/proc/self/ns/net") {
            return false;
        }
        let Ok(dev) = fs::read_to_string(format!("/proc/{pid}/net/dev")) else { return false };
        let names: Vec<&str> = dev.lines().skip(2).map(|l| l.split(':').next().unwrap_or("").trim()).collect();
        names == ["lo"]
    }

    pub fn status(&self) -> Status {
        let Some(record) = self.read_state().filter(|r| Self::ours(Some(r))) else { return Status::stopped() };
        let driver = record.kind();
        let (log, ready, lost): (&Path, &str, &[&str]) = match driver {
            Kind::Luma => (&self.paths.luma_log, LUMA_READY, &LUMA_LOST),
            Kind::XrDriver => (&self.paths.driver_log, READY, &LOST),
        };
        let current = read_bytes_from(log, record.log_offset);
        // None sorts below Some, as Python's -1 from rfind.
        let calibrated = current.rfind(ready) > lost.iter().map(|m| current.rfind(m)).max().flatten();
        Status {
            running: true,
            pid: record.pid,
            network_isolated: self.network_isolated(record.pid),
            calibrated,
            driver,
            binary: record.binary.unwrap_or_else(|| DRIVER.into()),
        }
    }

    /// Any head tracking driver, wherever it was started from, but `exclude`.
    pub fn other_drivers(&self, exclude: Option<i32>) -> Vec<i32> {
        let Ok(entries) = fs::read_dir(&self.paths.proc) else { return Vec::new() };
        let mut found = Vec::new();
        for entry in entries.flatten() {
            let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<i32>().ok()) else { continue };
            let Ok(cmdline) = fs::read(entry.path().join("cmdline")) else { continue };
            let argv0 = cmdline.split(|&b| b == 0).next().unwrap_or_default();
            let base = Path::new(OsStr::from_bytes(argv0)).file_name().map(OsStr::as_bytes).unwrap_or_default();
            if Some(pid) != exclude && (base == b"xrDriver" || base == LUMA_BINARY.as_bytes()) {
                found.push(pid);
            }
        }
        found.sort_unstable();
        found
    }

    /// XRLinuxDriver: set the namespaces up as their root, then become ourselves in a child user
    /// namespace. Each step execs in place, so the recorded pid and tick are the driver's.
    pub fn driver_argv(&self, binary: Option<&Path>) -> Vec<String> {
        let setup = format!(
            "mount -t tmpfs tmpfs {RESOLVED_DIR} && ip link set lo address {FAKE_MAC} && ip address add {FAKE_ADDRESS} dev lo\n\
             exec unshare --user --map-user={} --map-group={} -- \"$0\"",
            uid(),
            gid()
        );
        let binary = binary.unwrap_or(&self.paths.driver).to_string_lossy().into_owned();
        ["unshare", "--user", "--map-root-user", "--net", "--mount", "--", "sh", "-c"]
            .into_iter()
            .map(String::from)
            .chain([setup, binary])
            .collect()
    }

    /// The saved Luma flags as argv, empty with no file. Anything but `LUMA_FLAG_CHOICES` is refused.
    pub fn luma_flags(&self) -> Result<Vec<String>, Error> {
        let text = match fs::read_to_string(&self.paths.luma_flags) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
            other => other?,
        };
        let shown = self.paths.luma_flags.display();
        let words: Vec<&str> = text.split_whitespace().collect();
        let mut out = Vec::new();
        let mut at = 0;
        while at < words.len() {
            let flag = words[at];
            let Some((_, choices)) = LUMA_FLAG_CHOICES.iter().find(|(f, _)| *f == flag) else {
                return Err(refused(format!("{shown} has an unknown flag: {flag}")));
            };
            out.push(flag.to_string());
            at += 1;
            if let Some(choices) = choices {
                match words.get(at) {
                    Some(word) if choices.contains(word) => out.push(word.to_string()),
                    _ => return Err(refused(format!("{shown}: {flag} needs one of {}", choices.join(", ")))),
                }
                at += 1;
            }
        }
        Ok(out)
    }

    /// Own user and network namespaces, as ourselves; unshare execs in place.
    pub fn luma_argv(&self, binary: &Path) -> Result<Vec<String>, Error> {
        let mut argv: Vec<String> = ["unshare", "--user", "--map-current-user", "--net", "--"].map(String::from).into();
        argv.push(binary.to_string_lossy().into_owned());
        argv.extend(self.luma_flags()?);
        Ok(argv)
    }

    /// (where this start begins in the log, the log open for appending).
    fn open_luma_log(&self) -> io::Result<(u64, File)> {
        let log = &self.paths.luma_log;
        if let Some(parent) = log.parent() {
            fs::DirBuilder::new().recursive(true).mode(0o700).create(parent)?;
        }
        if fs::metadata(log).is_ok_and(|m| m.len() > LUMA_LOG_MAX) {
            let mut aside = log.as_os_str().to_owned();
            aside.push(".1");
            fs::rename(log, aside)?;
        }
        let f = OpenOptions::new()
            .append(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(log)?;
        Ok((f.metadata()?.len(), f))
    }

    /// The quiet shim's settings (docs/usb-drops.md), each one undoable alone. A broken file is the
    /// defaults, not the end. A saved value counts only with the default's exact type.
    pub fn shim_settings(&self) -> ShimSettings {
        let mut s = ShimSettings::default();
        let Ok(raw) = fs::read(&self.paths.quiet_settings) else { return s };
        let Ok(saved @ Value::Object(_)) = strict_json(&raw) else { return s };
        if let Some(b) = saved.get("enabled").and_then(Value::as_bool) {
            s.enabled = b;
        }
        if let Some(b) = saved.get("dof3").and_then(Value::as_bool) {
            s.dof3 = b;
        }
        if let Some(b) = saved.get("quiet_polls").and_then(Value::as_bool) {
            s.quiet_polls = b;
        }
        if let Some(n) = saved.get("reconnect_hold_ms").and_then(Value::as_i64) {
            s.reconnect_hold_ms = n;
        }
        s
    }

    /// The installed quiet shim's path, verified against its pin (`install.load_shim`), or None.
    /// A pin whose binary changed is an error.
    pub fn load_shim(&self) -> Result<Option<String>, Error> {
        let pin = self.paths.data_home.join("viture-workspace").join(SHIM_PIN);
        let raw = match fs::read(&pin) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            other => other?,
        };
        let entry = strict_json(&raw).map_err(|e| refused(e.to_string()))?;
        let path = entry.get("path").and_then(Value::as_str);
        let sha = entry.get("sha256").and_then(Value::as_str);
        match (path, sha) {
            (Some(path), Some(sha)) if sha256_file(Path::new(path)).is_ok_and(|got| got == sha) => Ok(Some(path.to_string())),
            _ => Err(refused("pinned binary changed")),
        }
    }

    /// What XRLinuxDriver gets on top of our environment: the quiet shim, if the install carries
    /// one that still matches its pin. No shim or a changed one: the plain driver.
    pub fn driver_env(&self) -> Vec<(String, String)> {
        let settings = self.shim_settings();
        if !settings.enabled {
            return Vec::new();
        }
        let Ok(Some(shim)) = self.load_shim() else { return Vec::new() };
        let mut env = vec![("LD_PRELOAD".to_string(), shim)];
        if settings.dof3 {
            env.push(("XR_QUIET_DOF".into(), "3".into()));
        }
        if settings.quiet_polls {
            env.push(("XR_QUIET_POLLS".into(), "1".into()));
        }
        if settings.reconnect_hold_ms > 0 {
            env.push(("XR_QUIET_RECONNECT_HOLD_MS".into(), settings.reconnect_hold_ms.to_string()));
        }
        env
    }

    /// The usbfs node of each attached 35ca:1104.
    fn glasses_nodes(&self) -> Vec<PathBuf> {
        let Ok(entries) = fs::read_dir(&self.paths.sys_usb) else { return Vec::new() };
        let mut nodes = Vec::new();
        for device in entries.flatten().map(|e| e.path()) {
            let read = |name: &str| fs::read_to_string(device.join(name)).map(|s| s.trim().to_lowercase());
            let (Ok(vendor), Ok(product)) = (read("idVendor"), read("idProduct")) else { continue };
            if (vendor.as_str(), product.as_str()) != GLASSES_USB {
                continue;
            }
            let (Ok(Ok(bus)), Ok(Ok(dev))) = (read("busnum").map(|s| s.parse::<u32>()), read("devnum").map(|s| s.parse::<u32>()))
            else {
                continue;
            };
            nodes.push(self.paths.dev_usb.join(format!("{bus:03}")).join(format!("{dev:03}")));
        }
        nodes
    }

    /// A Pro 2 runs its own pinned SDK bridge, never a driver from here. Sysfs only: nothing is opened.
    pub fn check_provider_compatibility(&self) -> Result<(), Error> {
        let Ok(entries) = fs::read_dir(&self.paths.sys_usb) else { return Ok(()) };
        for device in entries.flatten().map(|e| e.path()) {
            let read = |name: &str| fs::read_to_string(device.join(name)).map(|s| s.trim().to_string());
            match read("idVendor") {
                Ok(v) if v.to_lowercase() == "35ca" => {}
                _ => continue,
            }
            let product_id = read("idProduct").map(|s| s.to_lowercase()).unwrap_or_default();
            let product = read("product").unwrap_or_default();
            if product_id == "1301" || product == "VITURE PRO 2 XR Glasses" {
                return Err(Error::Unsupported(
                    "VITURE PRO 2 XR Glasses (35ca:1301): this tracking driver is not for these glasses. \
                     They run their own pinned SDK bridge, or nothing."
                        .into(),
                ));
            }
        }
        Ok(())
    }

    /// Refuse before the driver crashes on a node we may not open. access(2) only, so nothing is
    /// sent to the glasses. Glasses that are not attached are left to the driver.
    pub fn check_usb_access(&self) -> Result<(), Error> {
        let ok = |node: &Path| {
            CString::new(node.as_os_str().as_bytes())
                // SAFETY: `path` is a valid NUL-terminated string for the call's duration.
                .is_ok_and(|path| unsafe { libc::access(path.as_ptr(), libc::R_OK | libc::W_OK) } == 0)
        };
        if self.glasses_nodes().iter().all(|n| ok(n)) { Ok(()) } else { Err(refused(USB_DENIED)) }
    }

    pub fn check_config(&self) -> Result<(), Error> {
        let text = fs::read_to_string(&self.paths.driver_config).map_err(|_| refused("XRLinuxDriver config missing"))?;
        let mut lines = std::collections::HashMap::new();
        for line in text.lines().filter(|l| l.contains('=') && !l.starts_with('#')) {
            let (k, v) = line.split_once('=').unwrap_or_default();
            lines.insert(k, v);
        }
        let get = |k| lines.get(k).map(|v: &&str| v.trim()).unwrap_or("");
        if get("output_mode") != "external_only" || get("disabled") != "false" {
            return Err(refused("XRLinuxDriver config must be disabled=false and output_mode=external_only (no mouse/joystick)"));
        }
        Ok(())
    }

    /// What the Luma driver last said it was waiting for since `offset`: ", last waiting on X", or "".
    pub fn luma_waiting(&self, offset: u64) -> String {
        const MARK: &str = "luma: waiting reason=";
        let text = read_bytes_from(&self.paths.luma_log, offset);
        let last = text
            .match_indices(MARK)
            .filter_map(|(at, _)| {
                let rest = &text[at + MARK.len()..];
                let word = &rest[..rest.find(char::is_whitespace).unwrap_or(rest.len())];
                (!word.is_empty()).then_some(word)
            })
            .last();
        last.map(|w| format!(", last waiting on {w}")).unwrap_or_default()
    }

    /// `recovery_config`'s limit in seconds, else `LOST_TIMEOUT_S`. Never fails: a typo must not cost a session.
    pub fn lost_timeout(&self) -> f64 {
        let path = &self.paths.recovery_config;
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        let raw = match fs::read(path) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => return LOST_TIMEOUT_S,
            Err(e) => {
                eprintln!("xr-workspace: tracking: {name} not read: {e}");
                return LOST_TIMEOUT_S;
            }
            Ok(raw) => raw,
        };
        let value = strict_json(&raw)
            .ok()
            .filter(|v| v.as_object().is_some_and(|o| o.len() == 1))
            .and_then(|v| v.get("lost_timeout_s").and_then(Value::as_f64));
        if let Some(n) = value.filter(|n| *n > 0.0 && *n <= LOST_TIMEOUT_MAX_S) {
            return n;
        }
        eprintln!(
            "xr-workspace: tracking: {name} needs {{\"lost_timeout_s\": 1..{LOST_TIMEOUT_MAX_S:.0}}}; using {LOST_TIMEOUT_S:.0} s"
        );
        LOST_TIMEOUT_S
    }

    /// A driver that lost the glasses gets them back by itself, so leave it to. The newest status.
    fn wait_for_reconnect(&self, seconds: f64) -> Status {
        let begun = Instant::now();
        loop {
            let now = self.status();
            if !(now.running && now.network_isolated) || now.calibrated || since(begun) >= seconds {
                return now;
            }
            sleep(Duration::from_millis(200));
        }
    }

    /// Reuse a calibrated isolated driver of the kind and binary asked for, give a reconnecting one
    /// time to finish, or restart ours. Only the driver this app recorded is ever stopped; any
    /// other makes `start` refuse.
    pub fn ensure_ready(&self, want: &Want, timeout: f64) -> Result<Status, Error> {
        self.check_provider_compatibility()?;
        let (kind, target) = match want {
            Want::Luma(path) => (Kind::Luma, path.to_string_lossy().into_owned()),
            Want::XrDriver(binary) => (Kind::XrDriver, binary.as_deref().unwrap_or(&self.paths.driver).to_string_lossy().into_owned()),
        };
        let wanted = |s: &Status| s.network_isolated && s.driver == kind && s.binary == target;
        let mut now = self.status();
        if now.running && wanted(&now) && !now.calibrated {
            let settle = if kind == Kind::Luma { self.timing.luma_settle } else { self.timing.reconnect_wait };
            now = self.wait_for_reconnect(settle);
        }
        if now.running && wanted(&now) && now.calibrated {
            return Ok(now);
        }
        if now.running {
            self.stop()?;
        }
        self.start(want, timeout)
    }

    /// The driver that was running when it died, by the record it left, so a run never changes
    /// driver by itself. No record means someone stopped it, so nothing starts.
    pub fn restart_same(&self) -> Result<Status, Error> {
        let record = self.read_state().ok_or_else(|| refused("no tracking driver on record"))?;
        let binary = record.binary.as_ref().map(PathBuf::from);
        match (record.kind(), binary) {
            (Kind::Luma, Some(path)) => self.ensure_ready(&Want::Luma(path), self.timing.start),
            // A Luma record names its binary; without one there is nothing safe to start.
            (Kind::Luma, None) => Err(refused("no Luma driver binary on record")),
            (Kind::XrDriver, binary) => self.ensure_ready(&Want::XrDriver(binary), self.timing.start),
        }
    }

    /// The Luma driver, calibrated, or an error that says why in words for Cody. Nothing falls back.
    pub fn ensure_ready_luma(&self, luma: &Path) -> Result<Status, Error> {
        self.check_provider_compatibility()?;
        match self.ensure_ready(&Want::Luma(luma.into()), self.timing.luma_timeout) {
            Ok(s) => Ok(s),
            Err(e @ Error::Unsupported(_)) => Err(e),
            Err(e) => {
                let reason = match e.to_string() {
                    r if r.is_empty() => "Error".to_string(),
                    r => r,
                };
                if reason == CRASHED || reason == USB_DENIED {
                    return Err(e);
                }
                Err(refused(LUMA_FAILED.replace("{reason}", &reason)))
            }
        }
    }

    /// Start a driver and wait up to `timeout` s for it to calibrate. A failure stops it again
    /// and leaves no record.
    pub fn start(&self, want: &Want, timeout: f64) -> Result<Status, Error> {
        self.check_provider_compatibility()?;
        if Self::ours(self.read_state().as_ref()) {
            return Err(refused("tracking already running"));
        }
        if matches!(want, Want::XrDriver(_)) {
            self.check_config()?;
        }
        if !self.other_drivers(None).is_empty() {
            return Err(refused("another head tracking driver is running; refusing to compete for the glasses"));
        }
        self.check_usb_access()?;
        let (argv, offset, output, log, record_binary) = match want {
            Want::XrDriver(binary) => {
                let path = binary.as_deref().unwrap_or(&self.paths.driver);
                (self.driver_argv(Some(path)), file_size(&self.paths.driver_log), None, &self.paths.driver_log, path.to_owned())
            }
            Want::Luma(path) => {
                let argv = self.luma_argv(path)?; // first: a bad flags file should open no log
                let (offset, f) = self.open_luma_log()?;
                (argv, offset, Some(f), &self.paths.luma_log, path.clone())
            }
        };
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]).stdin(Stdio::null());
        match output {
            Some(f) => command.stdout(f.try_clone()?).stderr(f),
            None => command.stdout(Stdio::null()).stderr(Stdio::null()),
        };
        // The quiet shim is the standard driver's alone: the Luma driver has no SDK for it to hold
        // back, and in the 3DoF test it would change what is being measured.
        if matches!(want, Want::XrDriver(None)) || matches!(want, Want::XrDriver(Some(p)) if *p == self.paths.driver) {
            command.envs(self.driver_env());
        }
        // SAFETY: setsid is async-signal-safe, as pre_exec requires; it detaches the driver from our
        // session the way Popen(start_new_session=True) does.
        unsafe {
            command.pre_exec(|| if libc::setsid() < 0 { Err(io::Error::last_os_error()) } else { Ok(()) });
        }
        let mut child = command.spawn()?;
        let pid = i32::try_from(child.id()).map_err(|_| refused("pid out of range"))?;
        let begun = Instant::now();
        let mut tick = None;
        while tick.is_none() && since(begun) < 2.0 {
            // unshare execs the driver in the same pid
            tick = start_tick(pid);
            sleep(Duration::from_millis(20));
        }
        // No tick: it was gone before it could be recorded. Python writes null there, which no
        // check ever matches, so nothing could stop it; say how it ended instead.
        let Some(tick) = tick else {
            let _ = child.kill();
            let code = child.wait().ok().and_then(returncode);
            return Err(refused(exit_failure(offset, log, code)));
        };
        let record = Record {
            pid,
            start_tick: tick,
            log_offset: offset,
            driver: Some(match want {
                Want::Luma(_) => "luma".into(),
                Want::XrDriver(_) => "xrdriver".into(),
            }),
            binary: Some(record_binary.to_string_lossy().into_owned()),
        };
        if let Err(e) = self.write_state(&record) {
            // No record, so stop() could not find it: end the child here.
            let _ = child.kill();
            let _ = child.wait();
            return Err(e.into());
        }
        let result = self.wait_calibrated(&mut child, want, offset, log, timeout);
        if result.is_err() {
            // A failed start must not leave a driver behind, nor a record of one that is gone.
            let _ = self.stop();
            self.remove_state();
        }
        result
    }

    fn wait_calibrated(&self, child: &mut Child, want: &Want, offset: u64, log: &Path, timeout: f64) -> Result<Status, Error> {
        let begun = Instant::now();
        let isolation = timeout.min(2.0);
        while since(begun) < timeout {
            // First: a crashed driver is a zombie until reaped, so what follows would say only
            // "exited". This says how it ended.
            if let Some(status) = child.try_wait()? {
                return Err(refused(exit_failure(offset, log, returncode(status))));
            }
            let now = self.status();
            if !now.running {
                let code = child.try_wait()?.and_then(returncode);
                return Err(refused(exit_failure(offset, log, code)));
            }
            if !now.network_isolated {
                // unshare may still be setting up its namespace: never accept calibration until
                // isolated, but allow the launcher a bounded start.
                if since(begun) < isolation {
                    sleep(Duration::from_millis(20));
                    continue;
                }
                // Its namespaces go just before it can be waited on, so a dying process gets a moment.
                let caught = Instant::now();
                while since(caught) < 0.2 {
                    if let Some(status) = child.try_wait()? {
                        return Err(refused(exit_failure(offset, log, returncode(status))));
                    }
                    sleep(Duration::from_millis(10));
                }
                return Err(refused("tracking driver is not network-isolated; stopped"));
            }
            if now.calibrated {
                return Ok(now);
            }
            sleep(Duration::from_millis(200));
        }
        let waiting = if matches!(want, Want::Luma(_)) { self.luma_waiting(offset) } else { String::new() };
        Err(refused(format!("tracking driver did not calibrate in time (keep the glasses still while it starts){waiting}")))
    }

    /// Stop the driver this app started, if it is still that process. Returns whether it was.
    pub fn stop(&self) -> Result<bool, Error> {
        let Some(record) = self.read_state().filter(|r| Self::ours(Some(r))) else { return Ok(false) };
        let pid = record.pid;
        let still_there = || {
            // Our own child is a zombie, tick intact, until reaped.
            // SAFETY: waitpid with WNOHANG and a null status pointer only reaps; ECHILD is fine.
            unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) };
            start_tick(pid).as_deref() == Some(record.start_tick.as_str())
        };
        // Before the signal: to LumaWatch, a dead driver whose state file is still there is a crash.
        self.remove_state();
        // SAFETY: kill only sends a signal; the pid and start tick were just checked.
        unsafe { libc::kill(pid, libc::SIGTERM) };
        let begun = Instant::now();
        while still_there() && since(begun) < self.timing.stop {
            sleep(Duration::from_millis(50));
        }
        if still_there() {
            // SAFETY: as above.
            unsafe { libc::kill(pid, libc::SIGKILL) };
        }
        still_there();
        Ok(true)
    }
}

/// `quiet-driver.json`, with `QUIET_DEFAULTS` for anything missing or of the wrong type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ShimSettings {
    pub enabled: bool,
    pub dof3: bool,
    pub quiet_polls: bool,
    pub reconnect_hold_ms: i64,
}

impl Default for ShimSettings {
    fn default() -> Self {
        ShimSettings { enabled: true, dof3: true, quiet_polls: true, reconnect_hold_ms: 20000 }
    }
}

/// Lower-case hex SHA-256 of a regular file, opened no-follow (`install._sha`).
pub fn sha256_file(path: &Path) -> io::Result<String> {
    let mut f = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC).open(path)?;
    if !f.metadata()?.is_file() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "not a regular file"));
    }
    let mut digest = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        digest.update(&buf[..n]);
    }
    Ok(digest.finalize().iter().map(|b| format!("{b:02x}")).collect())
}
