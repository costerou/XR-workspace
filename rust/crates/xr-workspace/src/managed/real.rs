//! The [`Host`] of a real run: the managed order of [`super`] on the Hyprland client, the ownership broker, the journal
//! and the renderer's watchdog.
//!
//! A port of what `tools/native_spike/managed.py` (`_run_managed_impl`, `_run_owned`, `keep_renderer`,
//! `recover_restoring`, `ManagedGuard`) does at each step, with `supervisor.validate_physical_pair`,
//! `hyprland.plan_outputs` / `create_outputs`, `restore.take`, `adopt.adopt`, `privacy.wake`, `sbs.ButtonSbs.leave`,
//! `monitor_rules`, `survive` and `supervisor._launch` behind it. `tests/managed_real.rs` runs this and the Python
//! side by side on one scripted compositor and compares every request, the journal and the result.
//!
//! Not here yet, so a Rust run is a plain one (rust/README.md): the glasses' view controls (`view_controls`: the
//! private control socket, the Ctrl+Alt binds, the overview pointer, privacy mode, window sending, 3D, the key bind
//! panel, VR video), and with them the pills' desktop changes and the window memory's captures; putting remembered
//! windows back at the start (`WindowTracker.place`); resetting a saved setup the broker refused
//! (`profiles.discard_unusable`). Each has a stub in [`crate::view_controls`], called here where `_run_owned`,
//! `keep_renderer` and `_launch` call the Python's (rust/crates/xr-workspace/VIEW_CONTROLS.md).

use std::ffi::OsString;
use std::io::{self, Write};
use std::os::fd::{AsFd, FromRawFd, OwnedFd};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde_json::{Value as Json, json};
use xr_wire::json::{self as wire, Value};
use xr_wire::plugin::OutputSpec;

use super::{Error, Host, Recovery, Result, Scope, Spec};
use crate::adopt;
use crate::geometry::Desktop;
use crate::hyprland::{self, Client, Identity, Query};
use crate::install::Pin;
use crate::journal::{Journal, ManagedOwner};
use crate::monitor_rules;
use crate::ownership::{self, Broker, BrokerRecovery};
use crate::private_file::read_private_json;
use crate::recovery;
use crate::restore::{self, Snapshot};
use crate::runner::PanelConsent;
use crate::supervisor::survive::{self, Holds, Model};
use crate::supervisor::tracking::{Paths, Tracking};
use crate::supervisor::{self, Launch, LoopHook, privacy};
use crate::view_controls::{self, ViewControls, desktops, overview_pointer, setup, window_memory};

/// Stop: SIGINT, as the controller sends it to the run's main process (`systemctl kill --signal=SIGINT`).
pub static INTERRUPT: AtomicBool = AtomicBool::new(false);
/// The guard worker's arguments (an environment variable: never on a command line other users can read).
pub const GUARD_ENV: &str = "XR_WORKSPACE_GUARD";
pub const GUARD_COMMAND: &str = "supervisor-guard";
const SYS_USB: &str = "/sys/bus/usb/devices";
const APP: &str = "XR Workspace";
/// sbs.PROMPT_OFF.
const PROMPT_OFF: &str = "Hold R1 on the glasses to go back to 2D and 120 Hz.";
/// sbs.WIDE.
const WIDE: (i64, i64) = (3840, 1200);
/// supervisor.RENDERER_FLAG_CHOICES.
const RENDERER_FLAG_CHOICES: [(&str, &str); 1] = [("--late-latch-guard-ms", "decimal 0..8")];

extern "C" fn interrupted(_: libc::c_int) {
    INTERRUPT.store(true, Ordering::SeqCst);
}

/// SIGINT sets [`INTERRUPT`] instead of ending the process: the run's renderer stops and recovery runs, as the
/// Python's KeyboardInterrupt does.
pub fn catch_interrupt() {
    // SAFETY: the handler only stores to an atomic, which is async-signal-safe.
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = interrupted as extern "C" fn(libc::c_int) as usize;
        libc::sigemptyset(&mut action.sa_mask);
        action.sa_flags = libc::SA_RESTART;
        libc::sigaction(libc::SIGINT, &action, std::ptr::null_mut());
    }
}

fn err(e: impl std::fmt::Display) -> Error {
    Error(e.to_string())
}

fn to_serde(v: &Value) -> Json {
    match v {
        Value::Null => Json::Null,
        Value::Bool(b) => Json::Bool(*b),
        Value::Number(n) => Json::Number(n.clone()),
        Value::String(s) => Json::String(s.clone()),
        Value::Array(a) => Json::Array(a.iter().map(to_serde).collect()),
        Value::Object(o) => Json::Object(o.iter().map(|(k, v)| (k.clone(), to_serde(v))).collect()),
    }
}

fn wire_spec(spec: &OutputSpec) -> Json {
    json!({"id": spec.id, "name": spec.name, "index": spec.index, "width": spec.width, "height": spec.height,
           "refresh_millihz": spec.refresh_millihz, "scale": spec.scale, "x": spec.x, "y": spec.y})
}

fn spec_of(v: &Json) -> Option<OutputSpec> {
    Some(OutputSpec {
        id: v["id"].as_str()?.into(),
        name: v["name"].as_str()?.into(),
        index: v["index"].as_i64()?,
        width: v["width"].as_i64()?,
        height: v["height"].as_i64()?,
        refresh_millihz: v["refresh_millihz"].as_i64()?,
        scale: v["scale"].as_f64()?,
        x: v["x"].as_i64()?,
        y: v["y"].as_i64()?,
    })
}

fn monotonic() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

/// `sbs.trace`: one journal line per step of the renderer's relaunch loop.
fn trace(step: &str) {
    eprintln!("xr-workspace: switch: t={:.3} {step}", monotonic());
}

/// `send.notify`: a short desktop notification; best-effort, never fails.
pub fn notify(message: &str) {
    let path = std::env::var_os("PATH")
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| OsString::from("/usr/bin:/bin"));
    let Some(binary) = std::env::split_paths(&path)
        .map(|d| d.join("notify-send"))
        .find(|p| {
            use std::os::unix::fs::PermissionsExt;
            p.metadata()
                .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        })
    else {
        return;
    };
    let mut cmd = Command::new(binary);
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from);
    if std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_none()
        && let Some(runtime) = runtime.filter(|r| r.is_absolute() && r.join("bus").exists())
    {
        cmd.env(
            "DBUS_SESSION_BUS_ADDRESS",
            format!("unix:path={}/bus", runtime.display()),
        );
    }
    let text: String = message.chars().take(200).collect();
    cmd.args(["--app-name", APP, "--expire-time", "4000", APP, &text])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Ok(mut child) = cmd.spawn() {
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// `supervisor._renderer_flags`: the saved renderer trial flags as argv, none with no file.
pub fn renderer_flags() -> io::Result<Vec<String>> {
    let config = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
        });
    let path = config.join("viture-workspace/renderer-flags");
    let text = match std::fs::read_to_string(&path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        other => other?,
    };
    let words: Vec<&str> = text.split_whitespace().collect();
    let mut out: Vec<String> = Vec::new();
    let mut at = 0;
    while at < words.len() {
        let flag = words[at];
        let Some((_, kind)) = RENDERER_FLAG_CHOICES
            .iter()
            .find(|(f, _)| *f == flag)
            .filter(|_| !out.iter().any(|o| o == flag))
        else {
            return Err(crate::private_file::invalid(format!(
                "{} has an unknown or repeated flag: {flag}",
                path.display()
            )));
        };
        let value = words.get(at + 1).copied().unwrap_or("");
        let shape = value
            .split_once('.')
            .map_or((value, None), |(a, b)| (a, Some(b)));
        let digits = |s: &str, max: usize| {
            !s.is_empty() && s.len() <= max && s.bytes().all(|b| b.is_ascii_digit())
        };
        let ok = digits(shape.0, 2)
            && shape.1.is_none_or(|f| digits(f, 3))
            && value.parse::<f64>().is_ok_and(|v| v <= 8.0);
        if !ok {
            return Err(crate::private_file::invalid(format!(
                "{}: {flag} needs a {kind}",
                path.display()
            )));
        }
        out.extend([flag.to_owned(), value.to_owned()]);
        at += 2;
    }
    Ok(out)
}

/// `supervisor.validate_physical_pair`: the read-only connector check. `asleep_ok`: the run's guard lets the fallback
/// be DPMS-off (privacy mode); it must still be connected, enabled and not mirrored.
pub fn validate_physical_pair(
    client: &mut Client,
    session: &str,
    output: &str,
    fallback: &str,
    asleep_ok: bool,
) -> std::result::Result<(), String> {
    if !xr_wire::plugin::is_token(output) || !xr_wire::plugin::is_token(fallback) {
        return Err("invalid identifier".into());
    }
    if output == fallback {
        return Err("independent physical recovery output required".into());
    }
    if client.verified_session().map_err(|e| e.to_string())? != session {
        return Err("launcher session mismatch".into());
    }
    let raw = client
        .query(Query::Monitors, hyprland::DEADLINE)
        .map_err(|e| e.to_string())?;
    let snapshot = hyprland::monitors(&raw).map_err(|e| e.to_string())?;
    for name in [output, fallback] {
        let physical = ["DP-", "HDMI-A-", "eDP-", "DVI-D-"].iter().any(|p| {
            name.strip_prefix(p)
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        });
        if !physical {
            return Err("approved output must be a physical connector".into());
        }
        let asleep = asleep_ok && name == fallback;
        let matches = snapshot
            .iter()
            .filter(|m| {
                m.name == name && !m.disabled && (m.dpms || asleep) && m.mirror_of == "none"
            })
            .count();
        if matches != 1 {
            return Err("approved usable physical output missing".into());
        }
    }
    Ok(())
}

/// `hyprland.plan_outputs`: the run's desktops side by side, right of every monitor there is, at the topmost row.
fn plan_outputs(
    monitors: &Value,
    workspaces: &Value,
    run_id: &str,
    desktops: &[Desktop],
) -> Result<Vec<OutputSpec>> {
    if !xr_wire::plugin::is_token(run_id) {
        return Err(Error("invalid identifier".into()));
    }
    let existing = hyprland::monitors(monitors).map_err(err)?;
    let ok = matches!(workspaces, Value::Array(list) if list.iter().all(|w| {
        w.get("id").and_then(Value::as_int).is_some() && matches!(w.get("name"), Some(Value::String(_)))
    }));
    if !ok {
        return Err(Error("invalid workspace snapshot".into()));
    }
    let right = existing
        .iter()
        .map(|m| {
            let side = if m.transform % 2 != 0 {
                m.height
            } else {
                m.width
            };
            (m.x as f64 + side as f64 / m.scale).ceil() as i64
        })
        .max()
        .unwrap_or(0);
    let y = existing.iter().map(|m| m.y).min().unwrap_or(0);
    let mut specs = Vec::new();
    let mut x = right;
    for (index, d) in desktops.iter().enumerate() {
        let name = format!("XR-{}", index + 1);
        if existing.iter().any(|m| m.name == name) {
            return Err(Error("output name already exists".into()));
        }
        let width = d.logical_width();
        if x + width > 2147483647 {
            return Err(Error("layout overflow".into()));
        }
        specs.push(OutputSpec {
            id: crate::journal::uuid4().map_err(err)?,
            name,
            index: index as i64,
            width: d.width,
            height: d.height,
            refresh_millihz: 120000,
            scale: d.scale,
            x,
            y,
        });
        x += width;
    }
    Ok(specs)
}

/// One status line's `tracking`, and whether it says a frame was presented or discarded (`missed`), when it is a
/// valid measured status (`Supervisor.status_bytes`). The view control's
/// replies (`control_applied` and the rest) have no reader in a run without one.
fn status_tracking(line: &[u8]) -> (Option<bool>, bool) {
    let Ok(value) = wire::parse_strict(line) else {
        return (None, false);
    };
    let Value::Object(entries) = &value else {
        return (None, false);
    };
    const KEYS: [&str; 9] = [
        "presented_hz",
        "missed",
        "capture_path",
        "pose_age_ms",
        "video",
        "tracking",
        "capture_failure_run",
        "vsync_error_ms",
        "sensor_to_vsync_ms",
    ];
    let finite =
        |v: &Value| matches!(v, Value::Number(n) if n.as_f64().is_some_and(f64::is_finite));
    let one_key = |k: &str| {
        [
            "control_applied",
            "owner_readback",
            "control_refused",
            "maximize_geometry",
            "overview_geometry",
            "roll_geometry",
            "help_geometry",
        ]
        .contains(&k)
    };
    if entries.len() == 1 && one_key(&entries[0].0) {
        return (None, false);
    }
    for (k, v) in entries {
        let valid = match k.as_str() {
            "capture_path" => matches!(v.as_str(), Some("dmabuf" | "shm")),
            "video" => matches!(
                v.as_str(),
                Some("off" | "loading" | "playing" | "paused" | "ended" | "error")
            ),
            "tracking" => matches!(v, Value::Bool(_)),
            "missed" | "capture_failure_run" => v.as_int().is_some_and(|n| n >= 0),
            "sensor_to_vsync_ms" => finite(v),
            k if KEYS.contains(&k) => {
                finite(v) && matches!(v, Value::Number(n) if n.as_f64().is_some_and(|x| x >= 0.0))
            }
            _ => return (None, false),
        };
        if !valid {
            return (None, false);
        }
    }
    let tracking = match value.get("tracking") {
        Some(Value::Bool(b)) => Some(*b),
        _ => None,
    };
    (tracking, value.get("missed").is_some())
}

/// `str()` of a JSON value as Python formats it in an f-string.
fn py_str(v: Option<&Json>) -> String {
    match v {
        None | Some(Json::Null) => "None".into(),
        Some(Json::Bool(b)) => if *b { "True" } else { "False" }.into(),
        Some(Json::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

fn returncode(status: Option<ExitStatus>) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    match status {
        Some(s) => s.code().or_else(|| s.signal().map(|n| -n)).unwrap_or(-1),
        None => -1,
    }
}

/// The real run's host. Built per run by [`run`]; the runner keeps the client.
pub struct RealHost<'a> {
    client: &'a mut Client,
    consent: &'a PanelConsent,
    runtime: PathBuf,
    renderer: PathBuf,
    layouts: PathBuf,
    broker: Option<Broker>,
    journal: Option<Journal>,
    owner: Option<ManagedOwner>,
    specs: Vec<OutputSpec>,
    session: String,
    stop_reason: Option<String>,
    holds: Holds,
    continuity: Option<OwnedFd>,
    /// startup.py: the first renderer spawned, and its first status line with `missed` (0: none yet).
    renderer_ns: Option<i64>,
    first_status: std::sync::Arc<std::sync::atomic::AtomicI64>,
    /// `_run_owned`'s `manager` and `memory`, and the broker's cursor read, until `view_controls` takes them.
    desktops: Option<desktops::Manager>,
    windows: Option<window_memory::Tracker>,
    cursor_shape: Option<overview_pointer::CursorShape>,
    /// `view_controls`'s `control`, from its enter to its exit.
    controls: Option<ViewControls>,
}

/// The [`view_controls::Run`] of one call, from the host's fields (a macro, so the borrows stay per field).
macro_rules! view_run {
    ($host:expr, $names:expr) => {
        view_controls::Run {
            client: &mut *$host.client,
            runtime: &$host.runtime,
            run_id: &$host.consent.run_id,
            session: &$host.session,
            capture: $names,
            output: &$host.consent.output,
            fallback: &$host.consent.recovery_output,
            model: &$host.consent.model,
            renderer: &$host.renderer,
            sbs_mode: $host.consent.sbs_mode,
        }
    };
}

impl<'a> RealHost<'a> {
    pub fn new(
        client: &'a mut Client,
        consent: &'a PanelConsent,
        runtime: &Path,
        renderer: &Pin,
    ) -> Self {
        RealHost {
            client,
            consent,
            runtime: runtime.to_path_buf(),
            renderer: renderer.path.clone(),
            layouts: restore::layout_dir(),
            broker: None,
            journal: None,
            owner: None,
            specs: Vec::new(),
            session: String::new(),
            stop_reason: None,
            holds: Holds::default(),
            continuity: None,
            renderer_ns: None,
            first_status: Default::default(),
            desktops: None,
            windows: None,
            cursor_shape: None,
            controls: None,
        }
    }

    fn names(&self) -> Vec<String> {
        self.specs.iter().map(|s| s.name.clone()).collect()
    }

    /// survive.py's record of this run's holds.
    pub fn holds(&self) -> Vec<Json> {
        self.holds
            .record
            .iter()
            .map(|h| json!({"seconds": h.seconds, "resumed": h.resumed}))
            .collect()
    }

    fn journal_path(&self, run_id: &str) -> PathBuf {
        self.runtime
            .join("xr-workspace-spike")
            .join(run_id)
            .join("journal.json")
    }

    fn journal(&mut self) -> Result<&mut Journal> {
        self.journal
            .as_mut()
            .ok_or_else(|| Error("no journal".into()))
    }

    fn pose_feed(&self) -> bool {
        self.consent.tracking.is_some()
    }

    fn monitors_json(&mut self) -> Option<Json> {
        self.client
            .query(Query::Monitors, hyprland::DEADLINE)
            .ok()
            .map(|v| to_serde(&v))
    }

    /// survive.output_ok: the output is there and in use.
    fn output_ok(&mut self) -> std::result::Result<bool, String> {
        let raw = self
            .client
            .query(Query::Monitors, hyprland::DEADLINE)
            .map_err(|e| e.to_string())?;
        let monitors = hyprland::monitors(&raw).map_err(|e| e.to_string())?;
        Ok(monitors
            .iter()
            .any(|m| m.name == self.consent.output && !m.disabled))
    }

    /// `_restore_output`: the glasses back as the run's start left them (the session mode, parked), once they return.
    fn restore_output(&mut self) -> std::result::Result<(), String> {
        trace("hold: restore");
        let output = self.consent.output.clone();
        let mut wide = None;
        if self.consent.sbs_mode {
            let raw = self
                .client
                .query(Query::Monitors, hyprland::DEADLINE)
                .map_err(|e| e.to_string())?;
            let monitors = hyprland::monitors(&raw).map_err(|e| e.to_string())?;
            if let Some(m) = monitors.iter().find(|m| m.name == output) {
                wide = hyprland::best_wide(m, &self.consent.model).map_err(|e| e.to_string())?;
            }
        }
        if let Some(rate) = wide {
            self.client
                .apply_wide_mode(&output, rate, &self.consent.model)
                .map_err(|e| e.to_string())?;
        } else if self.consent.mode_switch {
            self.client
                .apply_session_mode(&output)
                .map_err(|e| e.to_string())?;
        }
        self.client
            .park_presentation(&output)
            .map(drop)
            .map_err(|e| e.to_string())
    }

    /// The run's guard (`ManagedGuard`), as a worker process of this binary.
    fn guard(&mut self, specs: &[OutputSpec]) -> Result<Command> {
        let identity = self
            .client
            .identity()
            .cloned()
            .ok_or_else(|| Error("no verified compositor peer yet".into()))?;
        let broker = self
            .broker
            .as_ref()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        let blob = json!({
            "instance": self.client.instance(), "runtime": self.runtime,
            "identity": {"pid": identity.pid, "uid": identity.uid, "gid": identity.gid, "start": identity.start},
            "session": self.session, "run_id": broker.run_id, "output": self.consent.output,
            "fallback_name": broker.approved_physical, "epoch": broker.epoch, "fallback": broker.fallback,
            "request_id": broker.request_id, "specs": specs.iter().map(wire_spec).collect::<Vec<_>>(),
        });
        let exe = std::env::current_exe().map_err(err)?;
        let mut command = Command::new(exe);
        command.arg(GUARD_COMMAND).env(GUARD_ENV, blob.to_string());
        Ok(command)
    }

    /// `_launch` of one renderer, until it is reaped: its return code.
    fn launch(&mut self, capture: bool) -> Result<i32> {
        self.stop_reason = None;
        let wayland = self.client.connect_wayland(&self.session).map_err(err)?;
        let peer = self
            .client
            .peer()
            .ok_or_else(|| Error("no verified compositor peer yet".into()))?;
        // A later renderer: the desktops as they are now, which a pill may have changed since the start.
        let live = self
            .controls
            .as_ref()
            .and_then(ViewControls::live_specs)
            .unwrap_or_else(|| self.specs.clone());
        let guard = self.guard(&live)?;
        let names: Vec<&str> = live.iter().map(|s| s.name.as_str()).collect();
        let renderer = self.renderer.clone();
        let run_id = self.consent.run_id.clone();
        let mut spec = Launch::new(
            &renderer,
            &self.consent.output,
            &run_id,
            &self.session,
            peer,
        );
        if capture {
            spec.capture = Some(&names);
        }
        if self.pose_feed() {
            spec.pose_feed = true;
            spec.renderer_flags = renderer_flags().map_err(err)?;
            spec.tracking_timeout = Some(Duration::from_secs_f64(
                Tracking::new(Paths::from_env(&self.runtime)).lost_timeout(),
            ));
        }
        spec.continuity_fd = self.continuity.as_ref().map(|fd| fd.as_fd());
        let depth = self
            .controls
            .as_ref()
            .and_then(ViewControls::depth_fd)
            .map(|fd| std::os::fd::AsRawFd::as_raw_fd(&fd));
        // SAFETY: the depth memory is the view controls', which live past this launch and close it only at their exit.
        spec.depth_fd = depth.map(|fd| unsafe { std::os::fd::BorrowedFd::borrow_raw(fd) });
        spec.guard = Some(guard);
        let first = self.first_status.clone();
        spec.status_handler = Some(Box::new(move |line| {
            let (tracking, missed) = status_tracking(line);
            if missed && first.load(Ordering::SeqCst) == 0 {
                first.store(crate::runner::monotonic_ns(), Ordering::SeqCst);
            }
            tracking
        }));
        self.renderer_ns
            .get_or_insert_with(crate::runner::monotonic_ns);
        if let Some(controls) = self.controls.as_mut() {
            spec.initial_frames = controls.initial_frame(); // saved spacing, overview
            spec.hook = Some(controls as &mut dyn LoopHook);
        }
        let outcome = supervisor::launch(spec, wayland, returncode, &INTERRUPT).map_err(err)?;
        self.stop_reason = outcome.stop_reason.map(|r| r.as_str().to_owned());
        Ok(outcome.result)
    }

    fn truncate_continuity(&self) {
        if let Some(fd) = &self.continuity {
            let _ = rustix::fs::ftruncate(fd, 0);
        }
    }

    fn revive(&self) -> bool {
        !self.pose_feed()
            || survive::revive_tracking(&Tracking::new(Paths::from_env(&self.runtime)))
    }

    fn usb(&self) -> Option<(i64, i64)> {
        survive::usb_identity(Path::new(SYS_USB), Model::LumaUltra)
    }
}

impl Host for RealHost<'_> {
    type Snapshot = Snapshot;

    fn verified_session(&mut self) -> Result<String> {
        self.client.verified_session().map_err(err)
    }

    fn validate_physical_pair(
        &mut self,
        session: &str,
        output: &str,
        fallback: &str,
        asleep_ok: bool,
    ) -> Result<()> {
        validate_physical_pair(self.client, session, output, fallback, asleep_ok).map_err(Error)
    }

    fn plan_outputs(&mut self, run_id: &str) -> Result<Vec<Spec>> {
        let workspaces = self
            .client
            .query(Query::Workspaces, hyprland::DEADLINE)
            .map_err(err)?;
        let monitors = self
            .client
            .query(Query::Monitors, hyprland::DEADLINE)
            .map_err(err)?;
        self.specs = plan_outputs(&monitors, &workspaces, run_id, &self.consent.desktops)?;
        Ok(self
            .specs
            .iter()
            .map(|s| Spec {
                id: s.id.clone(),
                name: s.name.clone(),
            })
            .collect())
    }

    fn broker_open(&mut self, run_id: &str, session: &str, fallback: &str) -> Result<()> {
        self.session = session.to_owned();
        self.broker = Some(Broker::new(run_id, session, fallback).map_err(Error)?);
        Ok(())
    }

    fn broker_from_journal(&mut self) -> Result<()> {
        let journal = self
            .journal
            .as_mut()
            .ok_or_else(|| Error("no journal".into()))?;
        self.broker = Some(Broker::from_journal(self.client, journal).map_err(Error)?);
        Ok(())
    }

    fn require_output_ownership(&mut self) -> Result<()> {
        let broker = self
            .broker
            .as_mut()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        broker.require_output_ownership(self.client).map_err(Error)
    }

    fn private_dir(&mut self, relative: &Path) -> Result<()> {
        xr_wire::files::PrivateDir::open(&self.runtime.join(relative), true)
            .map(drop)
            .map_err(err)
    }

    fn lock_owner(&mut self) -> Result<()> {
        let path = self.journal_path(&self.consent.run_id);
        self.owner = Some(ManagedOwner::acquire(path).map_err(err)?);
        Ok(())
    }

    fn unlock_owner(&mut self) {
        self.owner = None;
    }

    fn journal_open(&mut self, run_id: &str, session: &str, existing_only: bool) -> Result<()> {
        let path = self.journal_path(run_id);
        self.journal = Some(Journal::open(path, run_id, session, existing_only).map_err(err)?);
        Ok(())
    }

    fn journal_read_session(&mut self) -> Result<String> {
        let path = self.journal_path(&self.consent.run_id);
        let raw = read_private_json(&path, xr_wire::disk::JOURNAL_LIMIT as u64).map_err(err)?;
        raw["session"]
            .as_str()
            .map(str::to_owned)
            .ok_or_else(|| Error("invalid journal".into()))
    }

    fn journal_is_fresh(&mut self) -> Result<bool> {
        let journal = self.journal()?;
        journal.begin().map_err(err)?;
        Ok(journal.records.is_empty() && journal.broker.is_none())
    }

    fn journal_bind_broker(&mut self, scope: &Scope) -> Result<()> {
        let broker = self
            .broker
            .as_ref()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        let text = |s: &Option<String>| s.clone().map_or(Value::Null, Value::String);
        let context = Value::Object(vec![
            ("instance".into(), Value::String(scope.instance.clone())),
            ("output".into(), Value::String(scope.output.clone())),
            (
                "recovery_output".into(),
                Value::String(scope.recovery_output.clone()),
            ),
            ("epoch".into(), text(&broker.epoch)),
            ("fallback".into(), text(&broker.fallback)),
        ]);
        self.journal()?.bind_broker(&context).map_err(err)
    }

    fn journal_scope(&self) -> Option<Scope> {
        let broker = self.journal.as_ref()?.broker.as_ref()?;
        let field = |k: &str| broker.get(k).and_then(Value::as_str).map(str::to_owned);
        Some(Scope {
            instance: field("instance")?,
            output: field("output")?,
            recovery_output: field("recovery_output")?,
        })
    }

    fn journal_output_names(&mut self) -> Result<Vec<String>> {
        match self.journal.as_mut() {
            Some(journal) => journal.output_names().map_err(err),
            None => Ok(Vec::new()),
        }
    }

    fn journal_recover(&mut self) -> Result<Recovery> {
        let journal = self
            .journal
            .as_mut()
            .ok_or_else(|| Error("no journal".into()))?;
        let broker = self
            .broker
            .as_mut()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        let mut backend = BrokerRecovery {
            client: self.client,
            broker,
        };
        let done = recovery::recover(journal, &mut backend).map_err(err)?;
        Ok(Recovery {
            complete: done.complete,
            remaining: done.remaining,
            reasons: done.reasons,
        })
    }

    fn journal_compositor_ended(&mut self) -> Result<()> {
        self.journal()?.compositor_ended().map_err(err)
    }

    fn create_outputs(&mut self, _specs: &[Spec]) -> Result<()> {
        let specs = self.specs.clone();
        let journal = self
            .journal
            .as_mut()
            .ok_or_else(|| Error("no journal".into()))?;
        let broker = self
            .broker
            .as_mut()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        broker
            .require_output_ownership(self.client)
            .map_err(Error)?;
        broker
            .create_journaled(self.client, &specs, journal)
            .map(drop)
            .map_err(Error)
    }

    fn discard_unusable_setup(&mut self, error: &Error) -> Result<()> {
        // The runner never asks for it yet (RunOptions::saved_setup); view_controls::setup's port does.
        setup::discard_unusable(&self.consent.model, error).map_err(err)
    }

    fn desktop_manager(&mut self, _specs: &[Spec]) -> Result<()> {
        self.desktops = desktops::Manager::open(&self.specs); // the pills' Ultrawide / Add / Remove
        Ok(())
    }

    fn broker_verify(&mut self, spec: &Spec) -> Result<()> {
        let wanted = self
            .specs
            .iter()
            .find(|s| s.id == spec.id)
            .cloned()
            .ok_or_else(|| Error("unknown output".into()))?;
        let broker = self
            .broker
            .as_mut()
            .ok_or_else(|| Error("no ownership broker".into()))?;
        let observed = broker.inspect(self.client, &spec.id).map_err(Error)?;
        ownership::verify(&observed, &wanted).map_err(Error)
    }

    fn take_layout(&mut self) -> Option<Snapshot> {
        let fallback = self.consent.recovery_output.clone();
        let mut read = |q| {
            self.client
                .query(q, hyprland::DEADLINE)
                .ok()
                .map(|v| to_serde(&v))
        };
        let (workspaces, clients, monitors, active) = (
            read(Query::Workspaces)?,
            read(Query::Clients)?,
            read(Query::Monitors)?,
            read(Query::ActiveWindow)?,
        );
        let snapshot = restore::capture(
            &workspaces,
            &clients,
            &monitors,
            &active,
            &fallback,
            &self.consent.run_id,
        )
        .ok()?;
        let _ = restore::save(&snapshot, &self.layouts); // Stop restores from memory; only a crash recovery misses it
        Some(snapshot)
    }

    fn window_memory(&mut self, names: &[String]) -> Result<bool> {
        self.windows = window_memory::Tracker::open(&mut view_run!(self, names));
        Ok(self.windows.is_some())
    }

    fn window_memory_place(&mut self, names: &[String]) -> Result<()> {
        if let Some(mut windows) = self.windows.take() {
            windows.place(&mut view_run!(self, names));
            self.windows = Some(windows);
        }
        Ok(())
    }

    fn adopt(&mut self, names: &[String]) -> Result<()> {
        let fallback = self.consent.recovery_output.clone();
        let moved = (|| -> std::result::Result<(), String> {
            let workspaces = self
                .client
                .query(Query::Workspaces, hyprland::DEADLINE)
                .map_err(|e| e.to_string())?;
            let clients = self
                .client
                .query(Query::Clients, hyprland::DEADLINE)
                .map_err(|e| e.to_string())?;
            let targets: Vec<&str> = names.iter().map(String::as_str).collect();
            let moves = adopt::plan(
                &to_serde(&workspaces),
                &to_serde(&clients),
                &fallback,
                &targets,
            )
            .map_err(|e| e.0.to_string())?;
            if !moves.workspaces.is_empty() || !moves.windows.is_empty() {
                let w: Vec<(i64, &str)> = moves
                    .workspaces
                    .iter()
                    .map(|(i, o)| (*i, o.as_str()))
                    .collect();
                let c: Vec<(&str, &str)> = moves
                    .windows
                    .iter()
                    .map(|(a, o)| (a.as_str(), o.as_str()))
                    .collect();
                self.client
                    .adopt_windows(&w, &c)
                    .map_err(|e| e.to_string())?;
            }
            Ok(())
        })();
        if moved.is_err() {
            notify("Could not move your windows onto the glasses.");
        }
        Ok(())
    }

    fn window_memory_finish(&mut self) -> Result<()> {
        let names = self.names();
        if let Some(mut windows) = self.windows.take() {
            windows.finish(&mut view_run!(self, &names));
            self.windows = Some(windows);
        }
        Ok(())
    }

    fn cursor_reader(&mut self) -> Result<()> {
        self.cursor_shape = self
            .broker
            .as_ref()
            .and_then(overview_pointer::cursor_reader);
        Ok(())
    }

    fn view_controls_enter(&mut self) -> Result<()> {
        let names = self.names();
        let (desktops, windows, shape) = (
            self.desktops.take(),
            self.windows.take(),
            self.cursor_shape.take(),
        );
        let controls = ViewControls::enter(&mut view_run!(self, &names), desktops, windows, shape);
        self.controls = Some(controls.map_err(err)?);
        Ok(())
    }

    fn view_controls_exit(&mut self) -> Result<()> {
        let names = self.names();
        if let Some(controls) = self.controls.take() {
            controls.exit(&mut view_run!(self, &names));
        }
        Ok(())
    }

    fn continuity_open(&mut self) -> Result<()> {
        // SAFETY: a NUL-terminated name and valid flags; the returned descriptor is ours alone.
        let fd = unsafe { libc::memfd_create(c"xr-view-continuity".as_ptr(), libc::MFD_CLOEXEC) };
        if fd < 0 {
            return Err(err(io::Error::last_os_error()));
        }
        // SAFETY: fd is a fresh descriptor nothing else owns.
        self.continuity = Some(unsafe { OwnedFd::from_raw_fd(fd) });
        Ok(())
    }

    fn continuity_close(&mut self) -> Result<()> {
        self.continuity = None;
        Ok(())
    }

    /// `keep_renderer`: the renderer, and a new one after a USB drop of the glasses once they are back.
    fn keep_renderer(&mut self, capture: bool) -> Result<i32> {
        loop {
            let usb_before = self.usb();
            trace("renderer: launch");
            let code = self.launch(capture)?;
            // The glasses change mode only through the view controls' 3D (view_controls::immersive).
            let expected = self.controls.as_ref().is_some_and(ViewControls::switching);
            trace(&format!(
                "renderer: ended {code} {} expected={}",
                self.stop_reason.as_deref().unwrap_or("None"),
                if expected { "True" } else { "False" }
            ));
            let usb_now = self.usb();
            let Ok(output_ready) = self.output_ok() else {
                return Ok(code);
            };
            self.truncate_continuity(); // only a display-mode handover keeps pose continuity
            if !survive::should_hold(
                code,
                self.stop_reason.as_deref(),
                output_ready,
                usb_before,
                usb_now,
                expected,
            ) {
                return Ok(code);
            }
            if !self.holds.begin(monotonic()) {
                notify("The glasses keep dropping off USB, so XR stopped. Check the cable.");
                return Ok(code);
            }
            notify("The glasses dropped off USB. Your desktops are safe; XR comes back by itself.");
            let back = {
                let this = std::cell::RefCell::new(&mut *self);
                survive::wait_for_glasses(
                    || {
                        let mut host = this.borrow_mut();
                        host.output_ok().unwrap_or(false)
                            && (usb_before.is_none() || host.usb().is_some())
                    },
                    || this.borrow_mut().restore_output(),
                    |wait| {
                        if !INTERRUPT.load(Ordering::SeqCst) {
                            let mut host = this.borrow_mut();
                            match host.controls.as_mut() {
                                Some(controls) => controls.serve_without_renderer(wait),
                                None => std::thread::sleep(wait),
                            }
                        }
                    },
                    // Stop while waiting ends the wait at once.
                    || {
                        if INTERRUPT.load(Ordering::SeqCst) {
                            f64::INFINITY
                        } else {
                            monotonic()
                        }
                    },
                    survive::HOLD_LIMIT_S,
                    survive::SETTLE_S,
                )
            };
            if INTERRUPT.load(Ordering::SeqCst) {
                // Stop while waiting: the same end as Stop during a renderer.
                self.holds.finish(monotonic(), false);
                self.stop_reason = Some("interrupted".into());
                return Ok(code);
            }
            trace(&format!(
                "hold: output back={}",
                if back { "True" } else { "False" }
            ));
            if back {
                self.truncate_continuity();
                if let Some(controls) = self.controls.as_mut() {
                    controls.renderer_restarted();
                }
                if !self.revive() {
                    notify("XR is back without head tracking: the tracking driver did not start.");
                }
            }
            self.holds.finish(monotonic(), back);
            if !back {
                notify(
                    "The glasses did not come back, so XR stopped and your windows are back on the monitor.",
                );
                return Ok(code);
            }
            notify("The glasses are back. XR picks up where it was.");
        }
    }

    fn stop_reason(&self) -> Option<String> {
        self.stop_reason.clone()
    }

    fn wake(&mut self, output: &str) {
        privacy::wake(self.client, output, hyprland::DEADLINE);
    }

    /// `immersive.restore_2d(sbs.ButtonSbs(...))`: glasses a crash left side by side are asked back to 2D.
    fn restore_2d(&mut self, output: &str) {
        let Some(raw) = self.client.query(Query::Monitors, hyprland::DEADLINE).ok() else {
            return;
        };
        let Ok(monitors) = hyprland::monitors(&raw) else {
            return;
        };
        let found: Vec<_> = monitors.iter().filter(|m| m.name == output).collect();
        let [monitor] = found.as_slice() else { return };
        let Ok(wide) = hyprland::best_wide(monitor, "luma-ultra") else {
            return;
        };
        if wide.is_none() && (monitor.width, monitor.height) != WIDE {
            return;
        }
        notify(PROMPT_OFF);
    }

    fn clear_monitor_rules(&mut self, names: &[String]) -> Vec<String> {
        let path = self.client.monitors_config();
        let client = &mut *self.client;
        let tell: &dyn Fn(&str) = &notify;
        monitor_rules::clear(
            path.as_deref(),
            names,
            || client.reload_config(),
            Some(tell),
        )
    }

    fn named_monitor_rules(&mut self, names: &[String]) -> Vec<String> {
        monitor_rules::named(self.client.monitors_config().as_deref(), names)
    }

    fn unpin(&mut self) {
        let _ = self.client.unpin_xr_windows();
    }

    fn load_layout(&mut self) -> Result<Option<Snapshot>> {
        restore::load(&self.consent.run_id, &self.layouts).map_err(err)
    }

    fn restore_layout(&mut self, snapshot: &Snapshot) {
        if self.client.restore_layout(snapshot).is_err() {
            notify("Could not put your windows back where they were.");
        }
    }

    fn discard_layout(&mut self) -> Result<()> {
        restore::discard(&self.consent.run_id, &self.layouts).map_err(err)
    }

    fn notify(&mut self, text: &str) {
        notify(text);
    }

    /// `_left_on`: the windows still on `names`, by application class (never the title).
    fn left_on(&mut self, names: &[String]) -> Vec<String> {
        let Some(monitors) = self.monitors_json() else {
            return Vec::new();
        };
        let Some(clients) = self
            .client
            .query(Query::Clients, hyprland::DEADLINE)
            .ok()
            .map(|v| to_serde(&v))
        else {
            return Vec::new();
        };
        let (Some(monitors), Some(clients)) = (monitors.as_array(), clients.as_array()) else {
            return Vec::new();
        };
        let name_of = |id: &Json| {
            monitors
                .iter()
                .rev()
                .find(|m| m.get("id") == Some(id))
                .and_then(|m| m.get("name"))
                .and_then(Json::as_str)
        };
        clients
            .iter()
            .filter(|c| {
                c.get("monitor")
                    .and_then(name_of)
                    .is_some_and(|n| names.iter().any(|x| x == n))
                    && c.get("mapped") != Some(&Json::Bool(false))
            })
            .take(8)
            .map(|c| {
                let class = c
                    .get("class")
                    .and_then(Json::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or("a window");
                let space = match c.get("workspace") {
                    Some(Json::Object(w)) => py_str(w.get("id")),
                    _ => "?".into(),
                };
                let pinned = if c.get("pinned").is_some_and(|p| crate::py::truthy(Some(p))) {
                    " (pinned: unpin it first)"
                } else {
                    ""
                };
                format!(
                    "{class} is still on {}, workspace {space}{pinned}",
                    name_of(&c["monitor"]).unwrap_or("")
                )
            })
            .collect()
    }
}

/// `managed.run_managed` on the real host: the run, kept until its renderer ends, then recovered.
pub fn run(
    client: &mut Client,
    consent: &PanelConsent,
    runtime: &Path,
    renderer: &Pin,
) -> Result<crate::runner::Run> {
    if INTERRUPT.load(Ordering::SeqCst) {
        return Err(Error("KeyboardInterrupt".into()));
    }
    let order = super::Consent {
        run_id: consent.run_id.clone(),
        model: consent.model.clone(),
        instance: consent.instance.clone(),
        output: consent.output.clone(),
        recovery_output: consent.recovery_output.clone(),
        capture: true,
    };
    let mut host = RealHost::new(client, consent, runtime, renderer);
    let outcome = super::run_managed(&mut host, &order, super::RunOptions::default());
    let holds = host.holds();
    let mut marks: Vec<(String, i64)> = host
        .renderer_ns
        .map(|ns| ("renderer".to_owned(), ns))
        .into_iter()
        .collect();
    let first = host.first_status.load(Ordering::SeqCst);
    if first > 0 {
        marks.push(("first_status".into(), first));
    }
    let outcome = outcome?;
    Ok(crate::runner::Run {
        complete: outcome.recovery.complete,
        renderer_returncode: Some(outcome.renderer_returncode),
        remaining: outcome.recovery.remaining,
        stop_reason: outcome.stop_reason,
        holds,
        reasons: outcome.recovery.reasons,
        marks,
    })
}

/// `xr-workspace supervisor-guard`: the run's guard worker (`_guard_worker` with a `ManagedGuard`). One byte per check
/// on stdout, `V` or `F`; the reason for an `F` on stderr. Its arguments are in [`GUARD_ENV`].
pub fn guard_main() -> u8 {
    // SAFETY: ignoring a signal has no preconditions. Stop is the supervisor's; the worker is killed by it.
    unsafe { libc::signal(libc::SIGINT, libc::SIG_IGN) };
    let parsed = (|| -> Option<(Client, Broker, String, Vec<OutputSpec>)> {
        let blob: Json = serde_json::from_str(&std::env::var(GUARD_ENV).ok()?).ok()?;
        let id = &blob["identity"];
        let identity = Identity {
            pid: i32::try_from(id["pid"].as_i64()?).ok()?,
            uid: u32::try_from(id["uid"].as_u64()?).ok()?,
            gid: u32::try_from(id["gid"].as_u64()?).ok()?,
            start: id["start"].as_str()?.to_owned(),
        };
        let session = blob["session"].as_str()?;
        let client = Client::pinned(
            blob["instance"].as_str()?,
            Path::new(blob["runtime"].as_str()?),
            identity,
            session,
        )
        .ok()?;
        let mut broker = Broker::new(
            blob["run_id"].as_str()?,
            session,
            blob["fallback_name"].as_str()?,
        )
        .ok()?;
        broker.epoch = blob["epoch"].as_str().map(str::to_owned);
        broker.fallback = blob["fallback"].as_str().map(str::to_owned);
        broker.request_id = u32::try_from(blob["request_id"].as_u64()?).ok()?;
        let specs = blob["specs"]
            .as_array()?
            .iter()
            .map(spec_of)
            .collect::<Option<Vec<_>>>()?;
        Some((client, broker, blob["output"].as_str()?.to_owned(), specs))
    })();
    let Some((mut client, mut broker, output, specs)) = parsed else {
        eprintln!("xr-workspace: run guard: invalid {GUARD_ENV}");
        let _ = io::stdout().write_all(b"F");
        return 2;
    };
    let session = broker.session.clone();
    let fallback = broker.approved_physical.clone();
    let check = || -> std::result::Result<(), String> {
        validate_physical_pair(&mut client, &session, &output, &fallback, true)?;
        for spec in &specs {
            let observed = broker.inspect(&mut client, &spec.id)?;
            ownership::verify(&observed, spec)?;
        }
        Ok(())
    };
    supervisor::guard_worker(check, &mut io::stdout());
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_status_line_gives_its_tracking() {
        assert_eq!(
            status_tracking(br#"{"tracking": true, "missed": 0, "presented_hz": 120.0}"#),
            (Some(true), true)
        );
        assert_eq!(
            status_tracking(br#"{"tracking": false}"#),
            (Some(false), false)
        );
        assert_eq!(status_tracking(br#"{"missed": 0}"#), (None, true));
        assert_eq!(status_tracking(br#"{"tracking": 1}"#), (None, false));
        assert_eq!(
            status_tracking(br#"{"tracking": true, "missed": -1}"#),
            (None, false)
        );
        assert_eq!(
            status_tracking(br#"{"tracking": true, "other": 1}"#),
            (None, false)
        );
        assert_eq!(status_tracking(br#"{"control_applied": 3}"#), (None, false));
    }

    #[test]
    fn desktops_go_right_of_every_monitor() {
        let monitors = wire::parse_strict(
            br#"[{"name":"DP-1","id":0,"x":0,"y":-10,"width":2560,"height":1440,"refreshRate":60.0,"scale":1.5,
            "transform":0,"disabled":false,"dpmsStatus":true,"mirrorOf":"none","activeWorkspace":{"id":1,"name":"1"},
            "reserved":[0,0,0,0],"model":"m","description":"d","availableModes":[]}]"#,
        )
        .unwrap();
        let workspaces = wire::parse_strict(br#"[{"id":1,"name":"1"}]"#).unwrap();
        let d = Desktop {
            width: 1920,
            height: 1080,
            scale: 1.0,
        };
        let specs = plan_outputs(&monitors, &workspaces, "r1", &[d, d]).unwrap();
        assert_eq!(
            specs
                .iter()
                .map(|s| (s.name.as_str(), s.x, s.y))
                .collect::<Vec<_>>(),
            [("XR-1", 1707, -10), ("XR-2", 3627, -10)]
        );
        let bad = wire::parse_strict(br#"[{"id":true,"name":"1"}]"#).unwrap();
        assert_eq!(
            plan_outputs(&monitors, &bad, "r1", &[d]).unwrap_err().0,
            "invalid workspace snapshot"
        );
    }
}
