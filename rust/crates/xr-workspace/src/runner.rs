//! `xr-workspace supervisor --consent <path> --state-dir <dir>`: one consented panel run per transient unit, as
//! `tools/xr_controller/runner.py` runs it. Verify the pins, load the ownership plugin, the optional session mode, the
//! managed run, then unload.
//!
//! Same arguments, environment, exit codes and files as the Python runner, so the controller can start either one as
//! the run's unit: `<state-dir>/<run_id>.result.json` (0600, atomic, `json.dumps(sort_keys=True)` bytes) on every exit
//! path once the arguments parse, and `<run_id>.progress.json` beside it while the run starts. Both carry the start-up
//! timeline (`startup`: `tools/native_spike/startup.py`).
//!
//! The steps that touch the compositor, the tracking driver and the managed run are a [`Backend`]'s. [`real`] wires the
//! ported modules. Its managed run refuses for now: the ownership broker client and the renderer host behind
//! `managed::Host` are not ported yet, so a Rust run stops before any output changes and unloads the plugin. The
//! loopback backend ([`fake`], debug builds only) runs the whole order through `managed::run_managed` for the tests.

use std::ffi::OsString;
use std::fs;
use std::io::{self, Write};
use std::os::fd::AsFd;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::geometry::{Desktop, MAX_DESKTOPS};
use crate::hyprland::{OWNERSHIP_PLUGIN_NAME, ownership_plugin_count};
use crate::install::{self, Pin};
use crate::private_file::{DEFAULT_LIMIT, read_private_json, token};
use crate::window_memory::MODELS;
use crate::{monitor_rules, pyjson};

#[cfg(debug_assertions)]
pub mod fake;
pub mod real;
#[cfg(test)]
mod tests;

pub const USAGE: &str = "usage: xr-workspace supervisor --consent CONSENT --state-dir STATE_DIR";
pub const NOTIFY_SEND: &str = "/usr/bin/notify-send";

// panel_consent.py
const PANEL_GATE: &str = "P2-panel";
const PANEL_KEYS: [&str; 16] = [
    "schema", "gate", "run_id", "instance", "output", "recovery_output", "model", "allow", "capture", "tracking",
    "mode_switch", "plugin", "renderer", "desktops", "input", "sdk",
];
pub const LUMA_PIN: &str = "luma_driver";
const DRIVER_KEY: &str = "tracking_driver";
const PRO2_KEY: &str = "pro2_route";
const SBS_KEY: &str = "sbs_mode";
// supervisor.py
const CAPTURE_SCOPE: &str = "managed-outputs";
const CAPTURE_ALLOW: [&str; 2] = ["headless-outputs", "capture-managed-outputs"];
// tracking.py
pub const XRDRIVER: &str = "xrdriver";
pub const DOF3: &str = "3dof";
pub const LUMA: &str = "luma";
const LUMA_CPP: &str = "luma-cpp";
const SCOPES: [&str; 2] = ["xrlinuxdriver", "luma"];
pub const STANDARD_DRIVER: &str = "/usr/bin/xrDriver";
// install.py
const RUST_LUMA_PIN: &str = "luma-driver-rust.pin.json";
const DRIVER_3DOF_VERSION: &str = "2.11.9-958908-pr147-pr148-3dof";
const DRIVER_3DOF_SHA256: &str = "623c3dd03d204a125b1cdc32307a985bd2597d408c27edf1770df052fc44dcb8";
// faults.py
const FAULT_RUN: &str = "the run";
const MAX_FAULTS: usize = 3;
const MAX_TEXT: usize = 160;

/// The pro2 route (`pro2_route.execute` and `pro2_test.run_session`) is not ported; the Python runner keeps it.
pub const PRO2_NOT_PORTED: &str = "the Pro 2 route is not ported to the Rust supervisor";

type Res<T> = Result<T, String>;

/// A consent `load_panel_consent` accepted: the fields the runner and the managed run read.
#[derive(Debug, Clone)]
pub struct PanelConsent {
    pub run_id: String,
    pub instance: String,
    pub output: String,
    pub recovery_output: String,
    pub model: String,
    /// `None` for `false`.
    pub tracking: Option<String>,
    pub tracking_driver: Option<String>,
    pub mode_switch: bool,
    pub plugin: Pin,
    pub renderer: Pin,
    pub luma_driver: Option<Pin>,
    pub desktops: Vec<Desktop>,
}

/// `install.load_installed`: installed.json's pins, with the Luma driver a start runs now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Installed {
    pub plugin: Pin,
    pub renderer: Pin,
    pub luma_driver: Option<Pin>,
}

/// What a managed run returned (`managed.run_managed`'s dict).
#[derive(Debug, Clone, Default)]
pub struct Run {
    pub complete: bool,
    pub renderer_returncode: Option<i32>,
    pub remaining: Vec<String>,
    pub stop_reason: Option<String>,
    /// survive.py: the USB drops this run outlived or waited out.
    pub holds: Vec<Value>,
    pub reasons: Vec<String>,
}

/// The steps of a run that act on the compositor, the tracking driver and the session. Everything else (the consent,
/// the pins, the monitor rules, the result files) the runner does itself, the same for every backend.
pub trait Backend {
    /// `HyprlandClient(instance, runtime)`.
    fn connect(&mut self, instance: &str, runtime: &Path) -> Res<()>;
    /// `tracking.ensure_ready_luma`, then a started `LumaWatch`. A refusal is toasted by the runner.
    fn tracking_luma(&mut self, runtime: &Path, luma: &Path) -> Res<()>;
    /// `tracking.ensure_ready(runtime, binary=...)`.
    fn tracking_driver(&mut self, runtime: &Path, binary: &Path) -> Res<()>;
    /// `watch.finish()`, when there is a watch.
    fn finish_watch(&mut self);
    fn plugins_loaded(&mut self) -> Res<Vec<String>>;
    fn plugin_load(&mut self, path: &str) -> Res<()>;
    fn plugin_unload(&mut self, path: &str, recovery_pin: &Pin, data_home: &Path) -> Res<()>;
    fn apply_session_mode(&mut self, output: &str) -> Res<()>;
    fn park_presentation(&mut self, output: &str) -> Res<()>;
    fn run_managed(&mut self, consent: &PanelConsent, runtime: &Path, renderer: &Pin) -> Res<Run>;
    fn toast(&mut self, body: &str);
}

/// `runner._toast`: the line in the run's journal and a desktop notification, best-effort.
pub fn toast(body: &str) {
    eprintln!("xr-workspace: {body}");
    let child = std::process::Command::new(NOTIFY_SEND)
        .args(["--app-name=XR Workspace", "XR Workspace", body])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Ok(mut child) = child {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while std::time::Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

// ---- start-up timeline (startup.py) ----

pub const CLOCK: &str = "CLOCK_MONOTONIC";

fn clock_ns(clock: libc::clockid_t) -> i64 {
    let mut t = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: `t` is a valid timespec to write; both clocks always exist on Linux.
    unsafe { libc::clock_gettime(clock, &mut t) };
    t.tv_sec * 1_000_000_000 + t.tv_nsec
}

pub fn monotonic_ns() -> i64 {
    clock_ns(libc::CLOCK_MONOTONIC)
}

/// `startup.written_ns`: when `path` was last written, moved onto the monotonic clock.
pub fn written_ns(path: &Path) -> Option<i64> {
    let meta = fs::symlink_metadata(path).ok()?;
    let mtime = meta.mtime() * 1_000_000_000 + meta.mtime_nsec();
    Some(monotonic_ns() - (clock_ns(libc::CLOCK_REALTIME) - mtime))
}

/// The run's marks: a name's first mark wins.
#[derive(Debug, Clone, Default)]
pub struct Startup {
    marks: Vec<(String, i64)>,
}

impl Startup {
    pub fn mark(&mut self, name: &str, ns: Option<i64>) {
        if !self.marks.iter().any(|(n, _)| n == name) {
            self.marks.push((name.into(), ns.unwrap_or_else(monotonic_ns)));
        }
    }

    /// `startup.summary`.
    pub fn summary(&self) -> Value {
        let mut order = self.marks.clone();
        order.sort_by_key(|(_, at)| *at); // stable, as Python's sorted
        let ms = |d: i64| -> f64 { format!("{:.3}", d as f64 / 1e6).parse().expect("a formatted float parses") };
        let phases: Map<String, Value> =
            order.windows(2).map(|w| (w[0].0.clone(), json!(ms(w[1].1 - w[0].1)))).collect();
        let marks: Map<String, Value> = self.marks.iter().map(|(n, at)| (n.clone(), json!(at))).collect();
        let mut out = json!({"clock": CLOCK, "marks_ns": marks, "phases_ms": phases});
        let get = |name: &str| self.marks.iter().find(|(n, _)| n == name).map(|(_, at)| *at);
        if let (Some(confirmed), Some(end)) = (get("confirmed"), get("first_frame").or_else(|| get("first_status"))) {
            out["confirm_to_first_frame_ms"] = json!(ms(end - confirmed));
            out["first_frame_from"] =
                json!(if get("first_frame").is_some() { "first_frame" } else { "first_status" });
        }
        out
    }
}

// ---- files ----

/// `json.dumps(value, sort_keys=True)`.
pub fn dumps_sorted(value: &Value) -> String {
    fn sort(v: &Value) -> Value {
        match v {
            Value::Object(m) => {
                let mut keys: Vec<&String> = m.keys().collect();
                keys.sort();
                Value::Object(keys.into_iter().map(|k| (k.clone(), sort(&m[k]))).collect())
            }
            Value::Array(a) => Value::Array(a.iter().map(sort).collect()),
            other => other.clone(),
        }
    }
    pyjson::spaced(&sort(value))
}

/// `runner._write_result`: `name` in the private `state_dir` (made if missing), written whole or not at all.
pub fn write_result(state_dir: &Path, name: &str, result: &Value) -> io::Result<()> {
    use rustix::fs::{AtFlags, Mode, OFlags};
    let state_dir = std::path::absolute(state_dir)?;
    match fs::DirBuilder::new().mode(0o700).create(&state_dir) {
        Err(e) if e.kind() != io::ErrorKind::AlreadyExists => return Err(e),
        _ => {}
    }
    let dir = crate::private_file::private_dir(&state_dir)?;
    let tmp = format!(".{name}.{}.tmp", random_hex16());
    let flags = OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    let fd = rustix::fs::openat(dir.as_fd(), tmp.as_str(), flags, Mode::from_raw_mode(0o600))?;
    let written = (|| -> io::Result<()> {
        rustix::fs::fchmod(&fd, Mode::from_raw_mode(0o600))?;
        let mut file = fs::File::from(fd);
        file.write_all(dumps_sorted(result).as_bytes())?;
        file.flush()?;
        file.sync_all()?;
        rustix::fs::renameat(dir.as_fd(), tmp.as_str(), dir.as_fd(), name)?;
        Ok(())
    })();
    if let Err(e) = written {
        let _ = rustix::fs::unlinkat(dir.as_fd(), tmp.as_str(), AtFlags::empty());
        return Err(e);
    }
    dir.sync_all()
}

fn random_hex16() -> String {
    let mut bytes = [0u8; 8];
    let _ = rustix::rand::getrandom(&mut bytes, rustix::rand::GetRandomFlags::empty());
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// ---- consent (panel_consent.py) ----

fn is_int(v: &Value) -> bool {
    v.is_i64() || v.is_u64()
}

fn artifact(value: &Value) -> Res<Pin> {
    let Some(m) = value.as_object().filter(|m| m.len() == 2 && m.contains_key("path") && m.contains_key("sha256"))
    else {
        return Err("invalid artifact".into());
    };
    let path = m["path"].as_str().filter(|p| p.starts_with('/') && !p.contains('\0'));
    let Some(path) = path else { return Err("artifact path must be absolute".into()) };
    let sha = m["sha256"].as_str().filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
    let Some(sha) = sha else { return Err("invalid sha256".into()) };
    Ok(Pin { path: path.into(), sha256: sha.into() })
}

fn expected_keys(data: &Map<String, Value>) -> Vec<&'static str> {
    let mut keys = PANEL_KEYS.to_vec();
    if data.get("tracking").and_then(Value::as_str) == Some(LUMA) {
        keys.push(LUMA_PIN);
    }
    for key in [DRIVER_KEY, PRO2_KEY, SBS_KEY] {
        if data.contains_key(key) {
            keys.push(key);
        }
    }
    keys
}

/// `sbs.scope_ok`: the consent's 3D scope is exactly the model's.
fn sbs_scope_ok(value: &Value, model: &str) -> bool {
    let expected = match model {
        "pro2" => json!({"width": 3840, "height": 1080, "refresh_hz": [60]}),
        "luma-ultra" => json!({"width": 3840, "height": 1200, "refresh_hz": [90, 60]}),
        _ => return false,
    };
    let ints = ["width", "height"].iter().all(|k| value.get(k).is_some_and(is_int))
        && value.get("refresh_hz").and_then(Value::as_array).is_some_and(|r| r.iter().all(is_int));
    ints && value == &expected
}

/// `panel_consent._validate` with the scope to check (`load_panel_consent`).
pub fn validate_consent(raw: &Value, instance: &str, output: &str) -> Res<PanelConsent> {
    let tok = |v: &Value| v.as_str().and_then(|s| token(s).ok()).map(str::to_string).ok_or("invalid identifier");
    tok(&json!(instance))?;
    tok(&json!(output))?;
    let Some(data) = raw.as_object() else { return Err("invalid consent fields".into()) };
    let mut want = expected_keys(data);
    want.sort_unstable();
    let mut have: Vec<&str> = data.keys().map(String::as_str).collect();
    have.sort_unstable();
    if have != want {
        return Err("invalid consent fields".into());
    }
    if !(is_int(&data["schema"]) && data["schema"] == 1) || data["gate"] != PANEL_GATE {
        return Err(format!("{PANEL_GATE} consent required"));
    }
    let run_id = tok(&data["run_id"])?;
    let c_instance = tok(&data["instance"])?;
    let c_output = tok(&data["output"])?;
    let recovery_output = tok(&data["recovery_output"])?;
    if c_instance != instance || c_output != output {
        return Err("consent scope mismatch".into());
    }
    if run_id.chars().count() > 80 {
        return Err("run id too long".into());
    }
    if recovery_output == c_output {
        return Err("independent physical recovery output required".into());
    }
    let model = data["model"].as_str().filter(|m| MODELS.contains(m)).ok_or("unknown model")?.to_string();
    if model == "pro2" && !data.contains_key(PRO2_KEY) {
        return Err("VITURE PRO 2 driver and display support unverified; start consent unavailable".into());
    }
    if data.contains_key(PRO2_KEY) && model != "pro2" {
        return Err("the Pro 2 route is the Pro 2 only".into());
    }
    if data["capture"] != CAPTURE_SCOPE || data["allow"] != json!(CAPTURE_ALLOW) {
        return Err("exact managed-output capture scope required".into());
    }
    if data.contains_key(PRO2_KEY) {
        return Err(PRO2_NOT_PORTED.into());
    }
    let tracking = match &data["tracking"] {
        Value::Bool(false) => None,
        Value::String(s) if SCOPES.contains(&s.as_str()) => Some(s.clone()),
        _ => return Err(r#"tracking scope must be false, "xrlinuxdriver" or "luma""#.into()),
    };
    let tracking_driver = match data.get(DRIVER_KEY) {
        None => None,
        Some(v) if v == DOF3 && tracking.as_deref() == Some("xrlinuxdriver") => Some(DOF3.to_string()),
        Some(_) => return Err(r#"tracking driver must be "3dof", on XRLinuxDriver tracking"#.into()),
    };
    let mode = &data["mode_switch"];
    let mode_switch = mode != &Value::Bool(false);
    if mode_switch {
        if model != "luma-ultra" {
            return Err("mode switch is Luma Ultra only".into());
        }
        let exact = mode.as_object().is_some_and(|m| {
            m.len() == 3
                && [("width", 1920), ("height", 1200), ("refresh_hz", 120)]
                    .iter()
                    .all(|(k, v)| m.get(*k).is_some_and(|x| is_int(x) && x == v))
        });
        if !exact {
            return Err("mode switch must be exactly 1920x1200@120".into());
        }
    }
    if let Some(sbs) = data.get(SBS_KEY) {
        if !sbs_scope_ok(sbs, &model) || (model != "pro2" && !mode_switch) {
            return Err("3D mode must match the verified glasses model and its display scope".into());
        }
    }
    let plugin = artifact(&data["plugin"])?;
    let renderer = artifact(&data["renderer"])?;
    let luma_driver = data.get(LUMA_PIN).map(artifact).transpose()?;
    let desktops = match data["desktops"].as_array() {
        Some(list) if (1..=MAX_DESKTOPS).contains(&list.len()) => {
            list.iter().map(|d| Desktop::from_value(d).map_err(|e| e.to_string())).collect::<Res<Vec<_>>>()?
        }
        _ => return Err(format!("1 to {MAX_DESKTOPS} desktops required")),
    };
    if data["input"] != false || data["sdk"] != false {
        return Err("input/SDK not authorized".into());
    }
    Ok(PanelConsent {
        run_id,
        instance: c_instance,
        output: c_output,
        recovery_output,
        model,
        tracking,
        tracking_driver,
        mode_switch,
        plugin,
        renderer,
        luma_driver,
        desktops,
    })
}

/// `load_panel_consent(path, instance, output)`.
pub fn load_panel_consent(path: &Path, instance: Option<&str>, output: Option<&str>) -> Res<PanelConsent> {
    let (Some(instance), Some(output)) = (instance, output) else { return Err("invalid identifier".into()) };
    if token(instance).is_err() || token(output).is_err() {
        return Err("invalid identifier".into());
    }
    let raw = read_private_json(path, DEFAULT_LIMIT).map_err(|e| py_os_error(&e, path))?;
    validate_consent(&raw, instance, output)
}

// ---- install (install.py) ----

fn check_entry(entry: &Value, bin_dir: &Path) -> Res<Pin> {
    let malformed = || "malformed installed.json".to_string();
    let m = entry.as_object().filter(|m| m.len() == 2).ok_or_else(malformed)?;
    let (Some(path), Some(sha)) = (m.get("path").and_then(Value::as_str), m.get("sha256").and_then(Value::as_str))
    else {
        return Err(malformed());
    };
    if !(sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
        return Err(malformed());
    }
    let p = Path::new(path);
    let normal = p.components().all(|c| !matches!(c, std::path::Component::CurDir | std::path::Component::ParentDir))
        && !path.contains("//")
        && (path == "/" || !path.ends_with('/'));
    if !p.is_absolute() || !normal || !p.starts_with(bin_dir) || p == bin_dir {
        return Err("pinned path outside install directory".into());
    }
    Ok(Pin { path: p.into(), sha256: sha.into() })
}

fn config_home() -> PathBuf {
    std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config"))
}

/// `tracking.load_override`: never raises, a broken file is no override.
fn load_override() -> Option<String> {
    let path = config_home().join("viture-workspace/tracking-override.json");
    if !path.exists() {
        return None;
    }
    let data = read_private_json(&path, DEFAULT_LIMIT).ok()?;
    let m = data.as_object().filter(|m| m.len() == 2 && data.get("schema") == Some(&json!(1)))?;
    let driver = m.get("driver")?.as_str()?;
    [XRDRIVER, DOF3, LUMA_CPP].contains(&driver).then(|| driver.to_string())
}

/// `install.load_installed(data_home)`.
pub fn load_installed(data_home: &Path) -> Res<Installed> {
    let root = install::root(data_home);
    let bin = root.join("bin");
    let path = root.join("installed.json");
    let text = fs::read(&path).map_err(|e| format!("installed.json unreadable: {}", py_os_error(&e, &path)))?;
    let data: Value =
        serde_json::from_slice(&text).map_err(|e| format!("installed.json unreadable: {e}"))?;
    let m = data.as_object().ok_or("malformed installed.json")?;
    let mut keys: Vec<&str> = m.keys().map(String::as_str).filter(|k| *k != LUMA_PIN).collect();
    keys.sort_unstable();
    if keys != ["plugin", "renderer"] {
        return Err("malformed installed.json".into());
    }
    let plugin = check_entry(&m["plugin"], &bin)?;
    let renderer = check_entry(&m["renderer"], &bin)?;
    let mut luma_driver = m.get(LUMA_PIN).map(|e| check_entry(e, &bin)).transpose()?;
    if luma_driver.is_some() && load_override().as_deref() != Some(LUMA_CPP) {
        let pin_path = root.join(RUST_LUMA_PIN);
        match fs::read(&pin_path) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("{RUST_LUMA_PIN} unreadable: {}", py_os_error(&e, &pin_path))),
            Ok(raw) => {
                let rust: Value =
                    serde_json::from_slice(&raw).map_err(|e| format!("{RUST_LUMA_PIN} unreadable: {e}"))?;
                luma_driver = Some(check_entry(&rust, &bin)?);
            }
        }
    }
    Ok(Installed { plugin, renderer, luma_driver })
}

/// `str(OSError)` for a failed open of `path`: `[Errno N] strerror: 'path'`; other errors as they read.
fn py_os_error(e: &io::Error, path: &Path) -> String {
    let Some(errno) = e.raw_os_error() else { return e.to_string() };
    let text = e.to_string();
    let strerror = text.strip_suffix(&format!(" (os error {errno})")).unwrap_or(&text);
    format!("[Errno {errno}] {strerror}: {}", crate::supervisor::parity::py_repr(&path.to_string_lossy()))
}

/// `install.verify_pinned`.
pub fn verify_pinned(pin: &Pin) -> Res<()> {
    if install::verify_pinned(pin) { Ok(()) } else { Err("pinned binary changed".into()) }
}

/// `install.tracking_driver_path(key, data_home)`: the XRLinuxDriver build a consent names, its pin checked now.
pub fn tracking_driver_path(key: &str, data_home: &Path) -> Res<PathBuf> {
    match key {
        XRDRIVER => Ok(STANDARD_DRIVER.into()),
        DOF3 => {
            let path = install::root(data_home).join("xrdriver-3dof").join(DRIVER_3DOF_VERSION).join("xrDriver");
            verify_pinned(&Pin { path: path.clone(), sha256: DRIVER_3DOF_SHA256.into() })?;
            Ok(path)
        }
        _ => Err("unknown tracking driver".into()),
    }
}

// ---- the run (runner.execute) ----

/// `faults.Faults`, for the runner's own two sites. Rust has no traceback: the short form is the error line itself.
#[derive(Default)]
struct Faults {
    kept: Vec<(&'static str, Value)>,
    more: u64,
}

impl Faults {
    fn record(&mut self, site: &'static str, error: &str) {
        if let Some((_, known)) = self.kept.iter_mut().find(|(s, _)| *s == site) {
            known["count"] = json!(known["count"].as_u64().unwrap_or(0) + 1);
            return;
        }
        if self.kept.len() >= MAX_FAULTS {
            self.more += 1;
        } else {
            let line: String = format!("Error: {error}").chars().take(MAX_TEXT).collect();
            self.kept.push((site, json!({"where": FAULT_RUN, "count": 1, "traceback": [site, line]})));
        }
        eprintln!("xr-workspace: error in {FAULT_RUN}:\n{error}");
    }

    fn report(&self) -> Vec<Value> {
        let mut out: Vec<Value> = self.kept.iter().map(|(_, f)| f.clone()).collect();
        if self.more > 0 {
            out.push(json!({"where": "other faults", "count": self.more, "traceback": []}));
        }
        out
    }
}

fn run_id_of(raw: &Value) -> Option<String> {
    raw.get("run_id")
        .and_then(Value::as_str)
        .filter(|r| token(r).is_ok() && r.chars().count() <= 80)
        .map(str::to_string)
}

fn base_result(run_id: Option<String>, phase: &str) -> Map<String, Value> {
    let mut r = Map::new();
    r.insert("run_id".into(), json!(run_id));
    r.insert("phase".into(), json!(phase));
    r.insert("complete".into(), json!(false));
    r.insert("renderer_returncode".into(), Value::Null);
    r.insert("remaining".into(), Value::Null);
    r.insert("error".into(), Value::Null);
    r
}

/// Where a run's start reports its phase: `progress(run_id, phase)`.
pub type Progress<'a> = &'a mut dyn FnMut(&str, &str, &Startup) -> io::Result<()>;

/// `runner.execute`: the run, recorded in the returned result whatever happens.
pub fn execute(
    backend: &mut dyn Backend,
    consent_path: &Path,
    data_home: &Path,
    runtime: Option<&Path>,
    startup: &mut Startup,
    progress: Progress,
) -> Map<String, Value> {
    if let Ok(first) = read_private_json(consent_path, DEFAULT_LIMIT) {
        if first.get("model").and_then(Value::as_str) == Some("pro2") {
            let mut result = base_result(run_id_of(&first), "run");
            startup.mark("verify", None);
            if let Some(run_id) = run_id_of(&first) {
                if let Err(e) = progress(&run_id, "tracking", startup) {
                    result.insert("error".into(), json!(e.to_string()));
                    return result;
                }
            }
            result.insert("error".into(), json!(PRO2_NOT_PORTED));
            return result;
        }
    }
    let mut result = base_result(None, "verify");
    let mut errors: Vec<String> = Vec::new();
    let mut faults = Faults::default();
    let mut st = State::default();
    startup.mark("verify", None);
    let ran = run_steps(backend, consent_path, data_home, runtime, startup, progress, &mut result, &mut st);
    if let Err(e) = ran {
        faults.record("runner.rs execute", &e);
        errors.push(e);
    }
    if st.watch {
        backend.finish_watch();
    }
    let preserve = st.managed_started
        && match &st.run {
            Some(run) => !run.complete,
            None => match (&st.runtime, &st.run_id) {
                (Some(rt), Some(id)) => rt.join("xr-workspace-spike").join(id).join("journal.json").exists(),
                _ => false,
            },
        };
    if preserve {
        let why = st.run.as_ref().map(|r| r.reasons.join("; ")).filter(|w| !w.is_empty());
        errors.push(format!(
            "ownership plugin retained for explicit recovery{}",
            why.map(|w| format!(": {w}")).unwrap_or_default()
        ));
    }
    if let (true, false, Some(installed)) = (st.attempted, preserve, &st.installed) {
        if errors.is_empty() {
            result.insert("phase".into(), json!("teardown"));
        }
        let plugin = installed.plugin.path.to_string_lossy().into_owned();
        let unloaded = backend.plugin_unload(&plugin, &installed.plugin, data_home).and_then(|()| {
            if backend.plugins_loaded()?.iter().any(|n| n == OWNERSHIP_PLUGIN_NAME) {
                Err("plugin still loaded after unload".into())
            } else {
                Ok(())
            }
        });
        if let Err(e) = unloaded {
            faults.record("runner.rs unload", &e);
            errors.push(format!("unload: {e}"));
        }
    }
    if errors.is_empty() {
        result.insert("phase".into(), json!("done"));
    }
    result.insert("error".into(), if errors.is_empty() { Value::Null } else { json!(errors.join("; ")) });
    let report = faults.report();
    if !report.is_empty() {
        result.insert("faults".into(), json!(report));
    }
    result
}

#[derive(Default)]
struct State {
    run_id: Option<String>,
    runtime: Option<PathBuf>,
    installed: Option<Installed>,
    attempted: bool,
    managed_started: bool,
    watch: bool,
    run: Option<Run>,
}

#[allow(clippy::too_many_arguments)]
fn run_steps(
    backend: &mut dyn Backend,
    consent_path: &Path,
    data_home: &Path,
    runtime: Option<&Path>,
    startup: &mut Startup,
    progress: Progress,
    result: &mut Map<String, Value>,
    st: &mut State,
) -> Res<()> {
    let set = |result: &mut Map<String, Value>, phase: &str| {
        result.insert("phase".into(), json!(phase));
    };
    let raw = read_private_json(consent_path, DEFAULT_LIMIT).map_err(|e| py_os_error(&e, consent_path))?;
    let run_id = run_id_of(&raw);
    result.insert("run_id".into(), json!(run_id));
    if !raw.is_object() {
        return Err("invalid consent fields".into());
    }
    let consent = load_panel_consent(
        consent_path,
        raw.get("instance").and_then(Value::as_str),
        raw.get("output").and_then(Value::as_str),
    )?;
    if Some(&consent.run_id) != run_id.as_ref() {
        return Err("consent changed while reading".into());
    }
    let run_id = consent.run_id.clone();
    st.run_id = Some(run_id.clone());
    let installed = load_installed(data_home)?;
    let pins = [("plugin", Some(&consent.plugin), Some(&installed.plugin)),
        ("renderer", Some(&consent.renderer), Some(&installed.renderer)),
        (LUMA_PIN, consent.luma_driver.as_ref(), installed.luma_driver.as_ref())];
    for (key, wanted, pinned) in pins {
        if key == LUMA_PIN && wanted.is_none() {
            continue;
        }
        match pinned {
            Some(pin) if wanted == Some(pin) => verify_pinned(pin)?,
            _ => return Err(format!("consent {key} is not the pinned install")),
        }
    }
    st.installed = Some(installed.clone());
    progress(&run_id, "verify", startup).map_err(|e| e.to_string())?;
    set(result, "plugin");
    let runtime = runtime.filter(|r| !r.as_os_str().is_empty()).ok_or("XDG_RUNTIME_DIR not set")?;
    st.runtime = Some(runtime.into());
    backend.connect(&consent.instance, runtime)?;
    // before the wait for tracking: up to 120 seconds to hear nothing can start
    let rules = monitor_rules::config_path(
        std::env::var_os("XDG_CONFIG_HOME").as_deref(),
        std::env::var_os("HOME").as_deref(),
    );
    monitor_rules::refuse_start(rules.as_deref())?;
    if let Some(tracking) = &consent.tracking {
        set(result, "tracking");
        startup.mark("tracking", None);
        progress(&run_id, "tracking", startup).map_err(|e| e.to_string())?;
        if tracking == LUMA {
            let luma = installed.luma_driver.as_ref().ok_or("consent luma_driver is not the pinned install")?;
            if let Err(e) = backend.tracking_luma(runtime, &luma.path) {
                backend.toast(&e);
                return Err(e);
            }
            st.watch = true;
        } else {
            let binary = tracking_driver_path(consent.tracking_driver.as_deref().unwrap_or(XRDRIVER), data_home)?;
            eprintln!("head tracking driver: {}", binary.display());
            backend.tracking_driver(runtime, &binary)?;
        }
    }
    set(result, "plugin");
    startup.mark("plugin", None);
    progress(&run_id, "plugin", startup).map_err(|e| e.to_string())?;
    // Other plugins (e.g. Omarchy's hyprbars) may stay loaded; only a broker of ours already present blocks.
    if ownership_plugin_count(&backend.plugins_loaded()?) != 0 {
        return Err("ownership plugin already loaded".into());
    }
    verify_pinned(&installed.plugin)?;
    st.attempted = true;
    backend.plugin_load(&installed.plugin.path.to_string_lossy())?;
    if ownership_plugin_count(&backend.plugins_loaded()?) != 1 {
        return Err("plugin load not confirmed by readback".into());
    }
    startup.mark("desktops", None);
    progress(&run_id, "desktops", startup).map_err(|e| e.to_string())?;
    if consent.mode_switch {
        set(result, "mode");
        backend.apply_session_mode(&consent.output)?;
    }
    set(result, "layout");
    backend.park_presentation(&consent.output)?;
    set(result, "run");
    verify_pinned(&installed.renderer)?;
    st.managed_started = true;
    let run = backend.run_managed(&consent, runtime, &installed.renderer)?;
    result.insert("complete".into(), json!(run.complete));
    result.insert("renderer_returncode".into(), json!(run.renderer_returncode));
    result.insert("remaining".into(), json!(run.remaining));
    if let Some(reason) = run.stop_reason.as_ref().filter(|r| !r.is_empty()) {
        result.insert("stop_reason".into(), json!(reason));
    }
    if !run.holds.is_empty() {
        result.insert("usb_holds".into(), json!(run.holds));
    }
    if !run.reasons.is_empty() {
        result.insert("recovery".into(), json!(run.reasons));
    }
    st.run = Some(run);
    Ok(())
}

/// `runner.main`: the exit status (0 only for a finished, complete run whose renderer returned 0).
pub fn main(args: &[OsString], backend: &mut dyn Backend, started_ns: i64) -> u8 {
    let (mut consent, mut state_dir) = (None, None);
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let slot = match flag.to_str() {
            Some("--consent") => &mut consent,
            Some("--state-dir") => &mut state_dir,
            _ => {
                eprintln!("{USAGE}");
                return 2;
            }
        };
        let Some(value) = it.next() else {
            eprintln!("{USAGE}");
            return 2;
        };
        *slot = Some(PathBuf::from(value));
    }
    let (Some(consent), Some(state_dir)) = (consent, state_dir) else {
        eprintln!("{USAGE}");
        return 2;
    };
    let data_home = std::env::var_os("XDG_DATA_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share"));
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from);
    let mut startup = Startup::default();
    if let Some(confirmed) = written_ns(&consent).filter(|c| *c <= started_ns) {
        startup.mark("confirmed", Some(confirmed));
    }
    startup.mark("runner", Some(started_ns));
    let mut progress = |run_id: &str, phase: &str, startup: &Startup| {
        let record = json!({"run_id": run_id, "phase": phase, "startup": startup.summary()});
        write_result(&state_dir, &format!("{run_id}.progress.json"), &record)
    };
    let mut result = execute(backend, &consent, &data_home, runtime.as_deref(), &mut startup, &mut progress);
    result.insert("startup".into(), startup.summary());
    let run_id = result["run_id"].as_str().map(str::to_string);
    let result = Value::Object(result);
    let name = format!("{}.result.json", run_id.as_deref().unwrap_or("invalid"));
    let written = write_result(&state_dir, &name, &result).and_then(|()| match &run_id {
        Some(id) => match fs::remove_file(state_dir.join(format!("{id}.progress.json"))) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        },
        None => Ok(()),
    });
    if let Err(e) = written {
        eprintln!("result not written: {e}");
        return 2;
    }
    if let Some(error) = result["error"].as_str() {
        eprintln!("blocked: {error}");
    }
    let ok = result["phase"] == "done" && result["complete"] == true && result["renderer_returncode"] == 0;
    if ok { 0 } else { 2 }
}
