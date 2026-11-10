//! Immersive 3D: any desktop turned into stereo 3D by an AI depth map (tools/native_spike/immersive.py has the whole
//! story). This module holds the setting and its toggles, the renderer's `immersive` frame, the shared depth memory,
//! the depth engine process and the run's worker that brings the engine and the glasses up and down.
//!
//! The depth engine itself stays in Python (tools/depth_engine); the contract with it does not change:
//!
//! ```text
//! xr-depth-engine --desktops XR-1,XR-3 --shm-fd N
//! stdout: one line `ready` once the model is up and the shared memory attached (READY_TIMEOUT)
//! stdin:  one JSON line {"desktops": ["XR-1"]} per change; end of input means exit at once
//! ```
//!
//! The shared memory is an unnamed memfd of exactly the renderer's size (spike/include/xr/depth.hpp kDepthLinkShm),
//! made once per run and inherited by every renderer and the engine.
//!
//! Differences from the Python module, all deliberate:
//! - `is_valid` compares JSON values exactly, so `"global": 0` or `"strength": 100.0` are not valid settings (Python's
//!   `0 == False` and `100.0 == 100` let them through).
//! - An adapter that panics is a failure with the reason "an internal error" (Python names the exception's class).
//! - The glasses' own adapter (sbs.ButtonSbs, `make_sbs`) is not here: the `Sbs` trait is what it plugs into.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::error::{Error, Result};
use super::json as js;

/// tools/native_spike/geometry.py MAX_DESKTOPS.
pub const MAX_DESKTOPS: usize = 4;
pub const DESKTOP_NAMES: [&str; MAX_DESKTOPS] = ["XR-1", "XR-2", "XR-3", "XR-4"];

/// One look (docs/3d-comfort.md, tools/stereo_eval/presets.json). `near`, `far`: arcminutes either side of the plane;
/// `text`: percent of the parallax left on small text; `range_ms`: how slowly the plane follows the scene;
/// `convergence`: the preset's default plane.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Preset {
    pub name: &'static str,
    pub near: i64,
    pub far: i64,
    pub convergence: i64,
    pub text: i64,
    pub range_ms: i64,
}

pub const PRESETS: [Preset; 3] = [
    Preset { name: "desktop", near: 4, far: 14, convergence: 92, text: 0, range_ms: 3000 },
    Preset { name: "video", near: 14, far: 36, convergence: 70, text: 100, range_ms: 1000 },
    Preset { name: "games", near: 10, far: 30, convergence: 75, text: 0, range_ms: 800 },
];
pub const KEYS: [&str; 5] = ["global", "desktops", "preset", "strength", "convergence"];
/// The panel's Soft, Standard, Enhanced; anything from 0 to STRENGTH_MAX is valid.
pub const STRENGTHS: [i64; 3] = [50, 100, 150];
pub const STRENGTH_MAX: i64 = 200;
pub const TOGGLE_OP: &str = "immersive_toggle";
pub const SET_OP: &str = "immersive";
/// Ctrl+Alt+I: I for immersive (immersive.py has why it is free).
pub const TOGGLE_BIND: (&str, &str) = ("CTRL + ALT + I", TOGGLE_OP);
pub const ENGINE_NAME: &str = "xr-depth-engine";
/// The first start loads a model.
pub const READY_TIMEOUT: Duration = Duration::from_secs(30);
/// A running engine silent this long is hung, whatever its process says.
pub const HEARTBEAT_LIMIT_S: f64 = 3.0;
/// After end of input, then SIGTERM, then as long again, then SIGKILL.
pub const STOP_WAIT: Duration = Duration::from_secs(2);
/// How often the worker looks at a running engine.
pub const WATCH: Duration = Duration::from_millis(250);
/// A run's end waits this long for the worker.
pub const CLOSE_WAIT: Duration = Duration::from_secs(3);

/// The toast for a failure kind (`engine`, `died`, `sbs`, `leave`, `video`), `why` filled in.
pub fn toast(kind: &str, why: &str) -> Option<String> {
    Some(match kind {
        "engine" => format!("3D is off: the depth engine could not start ({why}). Back to 2D."),
        "died" => format!("3D is off: the depth engine stopped ({why}). Back to 2D."),
        "sbs" => format!("3D is off: the glasses could not switch to 3D mode ({why}). Back to 2D."),
        "leave" => {
            format!("The glasses did not leave 3D mode ({why}). Unplug them or use their button if the picture looks doubled.")
        }
        "video" => String::from("Saved: 3D applies to the desktops, once the VR video closes."),
        _ => return None,
    })
}

// --- the setting --------------------------------------------------------------------------------------------------

/// The cleaned setting. `global` is what every desktop does; `desktops` holds only the real exceptions, by XR-n name;
/// `strengths` (per-desktop strength, by name) is kept only if the saved setting had any.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Setting {
    pub global: bool,
    pub desktops: BTreeMap<String, bool>,
    pub preset: &'static Preset,
    pub strength: i64,
    pub convergence: i64,
    pub strengths: Option<BTreeMap<String, i64>>,
}

impl Default for Setting {
    /// All off, the Desktop look at Standard strength.
    fn default() -> Setting {
        Setting { global: false, desktops: BTreeMap::new(), preset: &PRESETS[0], strength: 100, convergence: 92, strengths: None }
    }
}

fn name_ok(name: &str) -> bool {
    DESKTOP_NAMES.contains(&name)
}

fn preset(name: &str) -> Option<&'static Preset> {
    PRESETS.iter().find(|p| p.name == name)
}

fn percent(value: Option<&Value>, top: i64, default: i64) -> i64 {
    value.and_then(js::int).filter(|v| (0..=top).contains(v)).unwrap_or(default)
}

/// Each field checked on its own; anything missing or invalid falls back to its default, and an exception that says
/// what `global` already says is dropped. Never fails.
pub fn clean(value: &Value) -> Setting {
    let empty = Map::new();
    let data = value.as_object().unwrap_or(&empty);
    let every = data.get("global").and_then(Value::as_bool).unwrap_or(false);
    let preset = data.get("preset").and_then(Value::as_str).and_then(preset).unwrap_or(&PRESETS[0]);
    let strengths = match data.get("strengths") {
        Some(Value::Object(s)) if !s.is_empty() => Some(
            s.iter()
                .filter_map(|(n, v)| js::int(v).filter(|v| name_ok(n) && (0..=STRENGTH_MAX).contains(v)).map(|v| (n.clone(), v)))
                .collect(),
        ),
        _ => None,
    };
    let desktops = match data.get("desktops") {
        Some(Value::Object(raw)) => {
            raw.iter().filter_map(|(n, v)| v.as_bool().filter(|v| name_ok(n) && *v != every).map(|v| (n.clone(), v))).collect()
        }
        _ => BTreeMap::new(),
    };
    Setting {
        global: every,
        desktops,
        preset,
        strength: percent(data.get("strength"), STRENGTH_MAX, 100),
        convergence: percent(data.get("convergence"), 100, preset.convergence),
        strengths,
    }
}

impl Setting {
    /// The setting as saved (view settings, a profile).
    pub fn to_value(&self) -> Value {
        let mut v = json!({
            "global": self.global, "desktops": self.desktops, "preset": self.preset.name,
            "strength": self.strength, "convergence": self.convergence,
        });
        if let Some(s) = &self.strengths {
            v["strengths"] = json!(s);
        }
        v
    }

    /// Normalised again after a change to `desktops` (the exceptions that say what `global` says go).
    fn renormalised(mut self) -> Setting {
        let every = self.global;
        self.desktops.retain(|n, v| name_ok(n) && *v != every);
        self
    }
}

pub fn is_valid(value: &Value) -> bool {
    let Value::Object(map) = value else { return false };
    let keys_ok = js::has_exact_keys(map, &KEYS) || (map.contains_key("strengths") && map.len() == KEYS.len() + 1 && KEYS.iter().all(|k| map.contains_key(*k)));
    keys_ok && clean(value).to_value() == *value
}

pub fn validate(value: &Value) -> Result<Setting> {
    if !is_valid(value) {
        return Err(Error::Invalid("invalid immersive"));
    }
    Ok(clean(value))
}

/// The cleaned setting from a settings object (its `immersive` entry). Missing or invalid: all off.
pub fn immersive(source: &Value) -> Setting {
    clean(source.get("immersive").unwrap_or(&Value::Null))
}

pub fn desktop_on(setting: &Setting, name: &str) -> bool {
    setting.desktops.get(name).copied().unwrap_or(setting.global)
}

/// Those of `names` that are 3D, in order.
pub fn on_names<'a>(setting: &Setting, names: &[&'a str]) -> Vec<&'a str> {
    names.iter().copied().filter(|n| desktop_on(setting, n)).collect()
}

/// Ctrl+Alt+I: anything 3D goes flat, all of it; nothing 3D, everything goes 3D. The exceptions go either way.
pub fn toggled_all(setting: &Setting, names: &[&str]) -> Setting {
    Setting { global: on_names(setting, names).is_empty(), desktops: BTreeMap::new(), ..setting.clone() }
}

/// The tail, which is lit when every desktop is 3D: lit, all flat; unlit, however many are on, all 3D.
pub fn toggled_every(setting: &Setting, names: &[&str]) -> Setting {
    Setting { global: on_names(setting, names).len() != names.len(), desktops: BTreeMap::new(), ..setting.clone() }
}

/// The 3D button: this desktop the other way, the rest as they are.
pub fn toggled_desktop(setting: &Setting, name: &str) -> Setting {
    let mut s = setting.clone();
    s.desktops.insert(name.to_owned(), !desktop_on(setting, name));
    s.renormalised()
}

/// Another preset, with its screen plane.
pub fn with_preset(setting: &Setting, name: &str) -> Result<Setting> {
    let p = preset(name).ok_or(Error::Invalid("invalid immersive preset"))?;
    Ok(Setting { preset: p, convergence: p.convergence, ..setting.clone() })
}

/// The `params` of an `immersive` frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Params {
    /// Per desktop in DESKTOP_NAMES order, only if the setting has per-desktop strengths.
    pub strengths: Option<[i64; MAX_DESKTOPS]>,
    pub strength: i64,
    pub convergence: i64,
    pub near: i64,
    pub far: i64,
    pub text: i64,
    pub range_ms: i64,
}

pub fn wire_params(setting: &Setting) -> Params {
    let p = setting.preset;
    let strengths = setting
        .strengths
        .as_ref()
        .filter(|s| !s.is_empty())
        .map(|s| DESKTOP_NAMES.map(|n| s.get(n).copied().unwrap_or(setting.strength)));
    Params { strengths, strength: setting.strength, convergence: setting.convergence, near: p.near, far: p.far, text: p.text, range_ms: p.range_ms }
}

impl Params {
    pub fn to_json(&self) -> String {
        let mut fields = Vec::new();
        if let Some(s) = self.strengths {
            fields.push(("strengths", js::array(s.iter().map(i64::to_string))));
        }
        fields.extend([
            ("strength", self.strength.to_string()),
            ("convergence", self.convergence.to_string()),
            ("near", self.near.to_string()),
            ("far", self.far.to_string()),
            ("text", self.text.to_string()),
            ("range_ms", self.range_ms.to_string()),
        ]);
        js::object(&fields)
    }
}

/// What an `immersive` frame shows (ipc.hpp ImmersivePayload).
#[derive(Debug, Clone, PartialEq)]
pub struct Frame<'a> {
    /// One per desktop in managed-output order (their 3D segments light); padded to MAX_DESKTOPS.
    pub flags: &'a [bool],
    /// The glasses are in SBS with depth flowing, so draw those desktops in 3D.
    pub stereo: bool,
    /// The desktop whose button is under the dot, -1 none.
    pub hover: i64,
    /// The dot is on that button's tail.
    pub tail: bool,
    /// All the run's desktops are 3D, so the tails light.
    pub every: bool,
    /// None: the default look.
    pub params: Option<Params>,
}

/// The `immersive` frame's payload.
pub fn renderer_payload(frame: &Frame) -> String {
    let desktops: Vec<bool> = (0..MAX_DESKTOPS).map(|i| frame.flags.get(i).copied().unwrap_or(false)).collect();
    let params = frame.params.clone().unwrap_or_else(|| wire_params(&Setting::default()));
    js::object(&[
        ("desktops", js::array(desktops.iter().map(bool::to_string))),
        ("global", frame.every.to_string()),
        ("stereo", frame.stereo.to_string()),
        ("hover", frame.hover.to_string()),
        ("tail", frame.tail.to_string()),
        ("params", params.to_json()),
    ])
}

/// Renderer `immersive` frame, one line.
pub fn renderer_frame(run_id: &str, session: &str, request_id: i64, frame: &Frame) -> Vec<u8> {
    js::frame(run_id, session, request_id, "immersive", &renderer_payload(frame))
}

// --- the shared depth memory --------------------------------------------------------------------------------------

/// The shared memory between the renderers and the engine (spike/include/xr/depth.hpp, tools/depth_engine/shm.py): a
/// memfd of the exact size, blank until the first renderer that needs it lays it out. No filesystem name, so a
/// desktop's pictures can only ever be in memory. The supervisor holds it for the run, so it outlives every renderer
/// and the engine sees one memory throughout.
pub struct DepthMemory {
    fd: OwnedFd,
}

impl DepthMemory {
    pub const CHANNELS: usize = MAX_DESKTOPS;
    pub const IN_W: usize = 420;
    pub const IN_H: usize = 266;
    pub const HEADER: usize = 4096;
    pub const CHANNEL_HEADER: usize = 320;
    pub const SLOTS: usize = 3;
    /// DepthShmHeader::engine_ns.
    pub const HEARTBEAT_AT: i64 = 48;

    pub const fn size() -> usize {
        let stride = (Self::CHANNEL_HEADER + 2 * Self::SLOTS * Self::IN_W * Self::IN_H * 4).div_ceil(4096) * 4096;
        Self::HEADER + stride * Self::CHANNELS
    }

    pub fn new() -> io::Result<DepthMemory> {
        // SAFETY: the name is a valid NUL-terminated string.
        let fd = unsafe { libc::memfd_create(c"xr-depth".as_ptr(), libc::MFD_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: memfd_create just made this descriptor and nothing else owns it.
        let fd = unsafe { OwnedFd::from_raw_fd(fd) };
        // SAFETY: ftruncate on a descriptor we own has no memory effects.
        if unsafe { libc::ftruncate(fd.as_raw_fd(), Self::size() as libc::off_t) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(DepthMemory { fd })
    }

    pub fn fd(&self) -> RawFd {
        self.fd.as_raw_fd()
    }

    /// Seconds since the engine last wrote its heartbeat, None before it first has. `now_ns`: CLOCK_MONOTONIC.
    pub fn heartbeat_age(&self, now_ns: Option<u64>) -> Option<f64> {
        let mut beat = [0u8; 8];
        // SAFETY: `beat` is valid for 8 bytes.
        let n = unsafe { libc::pread(self.fd(), beat.as_mut_ptr().cast(), 8, Self::HEARTBEAT_AT) };
        let beat = u64::from_le_bytes(beat);
        if n != 8 || beat == 0 {
            return None;
        }
        Some(now_ns.unwrap_or_else(monotonic_ns).saturating_sub(beat) as f64 / 1e9)
    }
}

/// CLOCK_MONOTONIC in nanoseconds (Python's time.monotonic_ns, what the engine writes).
pub fn monotonic_ns() -> u64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: `ts` is a valid timespec to fill.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// The engine beside the pinned renderer binary, or None: only an installed copy ever runs.
pub fn find_engine(renderer: Option<&Path>) -> Option<PathBuf> {
    let path = renderer?.with_file_name(ENGINE_NAME);
    super::process::executable(&path).then_some(path)
}

// --- the engine ---------------------------------------------------------------------------------------------------

/// The depth engine could not start, or stopped. Its text goes in a toast.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineError(pub String);

/// The glasses could not change mode (sbs.SbsError). Its text goes in a toast.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SbsError(pub String);

/// The depth engine, so the run never knows what is behind it. Called from the run's worker, and `stop` also from the
/// run's end if the worker hangs.
pub trait Engine: Send + Sync {
    /// Blocks until it is ready: true; false if `cancelled()` came true first (it is stopped again). An error leaves
    /// nothing running.
    fn start(&self, desktops: &[String], cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, EngineError>;
    fn set_desktops(&self, desktops: &[String]) -> std::result::Result<(), EngineError>;
    fn alive(&self) -> bool;
    /// Never fails; safe to call twice.
    fn stop(&self);
}

/// The glasses' side-by-side mode.
pub trait Sbs: Send + Sync {
    /// Blocks until the glasses are side by side: true; false if `cancelled()` came true first.
    fn enter(&self, cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, SbsError>;
    fn leave(&self) -> std::result::Result<(), SbsError>;
    /// leave() left the glasses wide, for a person to bring back.
    fn parked(&self) -> bool {
        false
    }
    fn settle(&self) -> std::result::Result<(), SbsError> {
        Ok(())
    }
    /// The glasses were taken back to 2D with their own button.
    fn left_by_hand(&self) -> bool {
        false
    }
    /// The glasses are changing mode, so the output going for a moment is expected.
    fn switching(&self) -> bool {
        false
    }
    fn follow_by_hand(&self) {}
    fn button(&self) -> &str {
        "R1"
    }
}

/// The Sbs of a run without the consent's sbs_mode scope: entering fails, and nothing happens to the glasses.
pub struct NoDeviceSbs;

impl Sbs for NoDeviceSbs {
    fn enter(&self, _cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, SbsError> {
        Err(SbsError(String::from("3D mode is not part of this run: stop and start it again")))
    }

    fn leave(&self) -> std::result::Result<(), SbsError> {
        Ok(())
    }
}

/// Recovery: glasses left in side-by-side by a crash, before anything else is put back. Never fails.
pub fn restore_2d(sbs: &dyn Sbs) {
    let _ = catch_unwind(AssertUnwindSafe(|| sbs.leave()));
}

/// Python's exception class name for a spawn error, as the toast showed it.
fn os_error_name(e: &io::Error) -> &'static str {
    match e.kind() {
        io::ErrorKind::NotFound => "FileNotFoundError",
        io::ErrorKind::PermissionDenied => "PermissionError",
        _ => "OSError",
    }
}

struct EngineChild {
    child: Child,
    stdin: Option<Arc<File>>,
    stdout: Option<ChildStdout>,
}

/// The depth engine as its own process (the contract is in the module comment). `binary` None: not installed.
/// `memory`: inherited by it.
pub struct ProcessEngine {
    binary: Option<PathBuf>,
    pub ready_timeout: Duration,
    pub stop_wait: Duration,
    memory: Option<Arc<DepthMemory>>,
    child: Mutex<Option<EngineChild>>,
    /// How the last stopped engine ended: exit code, or minus the signal.
    last_exit: Mutex<Option<i32>>,
}

impl ProcessEngine {
    pub fn new(binary: Option<PathBuf>, memory: Option<Arc<DepthMemory>>) -> ProcessEngine {
        ProcessEngine {
            binary,
            ready_timeout: READY_TIMEOUT,
            stop_wait: STOP_WAIT,
            memory,
            child: Mutex::new(None),
            last_exit: Mutex::new(None),
        }
    }

    /// The running engine's process id.
    pub fn pid(&self) -> Option<u32> {
        self.child.lock().unwrap().as_ref().map(|c| c.child.id())
    }

    pub fn last_exit(&self) -> Option<i32> {
        *self.last_exit.lock().unwrap()
    }

    fn spawn(&self, binary: &Path, desktops: &[String]) -> std::result::Result<ChildStdout, EngineError> {
        let mut cmd = Command::new(binary);
        cmd.arg("--desktops").arg(desktops.join(","));
        let shm = self.memory.as_ref().map(|m| m.fd());
        if let Some(fd) = shm {
            cmd.arg("--shm-fd").arg(fd.to_string());
        }
        // stderr is dropped: nothing may block on a full pipe.
        cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        let hook = move || {
            // Its own session, so stop() reaches anything it started.
            // SAFETY: setsid and fcntl on a descriptor number have no memory effects.
            if unsafe { libc::setsid() } < 0 {
                return Err(io::Error::last_os_error());
            }
            if let Some(fd) = shm {
                // SAFETY: as above.
                let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
                if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) } < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        };
        // SAFETY: the hook only calls setsid and fcntl, which are async-signal-safe.
        unsafe { cmd.pre_exec(hook) };
        let mut child = cmd.spawn().map_err(|e| EngineError(format!("it would not run: {}", os_error_name(&e))))?;
        let stdout = child.stdout.take().expect("piped stdout");
        let stdin = child.stdin.take().map(|s| Arc::new(File::from(OwnedFd::from(s))));
        *self.child.lock().unwrap() = Some(EngineChild { child, stdin, stdout: None });
        Ok(stdout)
    }

    /// Wait for `ready` on the engine's stdout (the lock is not held: stop() may come from another thread).
    fn wait_ready(&self, stdout: &ChildStdout, cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, EngineError> {
        let deadline = Instant::now() + self.ready_timeout;
        let mut seen: Vec<u8> = Vec::new();
        let mut buf = [0u8; 4096];
        loop {
            if cancelled() {
                return Ok(false);
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(EngineError(String::from("it took too long to load")));
            }
            let mut pfd = libc::pollfd { fd: stdout.as_raw_fd(), events: libc::POLLIN, revents: 0 };
            let ms = left.min(Duration::from_millis(100)).as_millis().max(1) as i32;
            // SAFETY: `pfd` is one valid pollfd.
            if unsafe { libc::poll(&mut pfd, 1, ms) } <= 0 {
                continue;
            }
            // SAFETY: `buf` is valid for its length and the descriptor is open (owned by `stdout`).
            let n = unsafe { libc::read(stdout.as_raw_fd(), buf.as_mut_ptr().cast(), buf.len()) };
            if n < 0 {
                let e = io::Error::last_os_error();
                if e.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(EngineError(format!("it could not be read: {}", os_error_name(&e))));
            }
            if n == 0 {
                return Err(EngineError(String::from("it quit while starting")));
            }
            seen.extend_from_slice(&buf[..n as usize]);
            let excess = seen.len().saturating_sub(4096);
            seen.drain(..excess);
            let lines: Vec<&[u8]> = seen.split(|b| *b == b'\n').collect();
            if lines[..lines.len() - 1].iter().any(|l| l.trim_ascii() == b"ready") {
                return Ok(true);
            }
        }
    }
}

impl Engine for ProcessEngine {
    fn start(&self, desktops: &[String], cancelled: &dyn Fn() -> bool) -> std::result::Result<bool, EngineError> {
        let Some(binary) = &self.binary else { return Err(EngineError(String::from("it is not installed"))) };
        let stdout = self.spawn(binary, desktops)?;
        match self.wait_ready(&stdout, cancelled) {
            Ok(true) => {
                if let Some(c) = self.child.lock().unwrap().as_mut() {
                    c.stdout = Some(stdout); // kept open for the engine's life, as Python's Popen does
                }
                Ok(true)
            }
            other => {
                drop(stdout);
                self.stop();
                other
            }
        }
    }

    fn set_desktops(&self, desktops: &[String]) -> std::result::Result<(), EngineError> {
        let stopped = || EngineError(String::from("it stopped listening"));
        let stdin = self.child.lock().unwrap().as_ref().and_then(|c| c.stdin.clone()).ok_or_else(stopped)?;
        // json.dumps's default separators, as the Python engine reads them either way.
        let line = format!("{{\"desktops\": [{}]}}\n", desktops.iter().map(|d| js::string(d)).collect::<Vec<_>>().join(", "));
        (&*stdin).write_all(line.as_bytes()).map_err(|_| stopped())
    }

    /// Running, and still writing its heartbeat: a hung model call leaves a live process that makes no maps.
    fn alive(&self) -> bool {
        let running = self.child.lock().unwrap().as_mut().is_some_and(|c| matches!(c.child.try_wait(), Ok(None)));
        running && self.memory.as_ref().and_then(|m| m.heartbeat_age(None)).is_none_or(|age| age < HEARTBEAT_LIMIT_S)
    }

    /// End of input, then SIGTERM, then SIGKILL, each waited for. Never fails; safe to call twice.
    fn stop(&self) {
        let Some(EngineChild { mut child, stdin, stdout }) = self.child.lock().unwrap().take() else { return };
        // End of input. Its stdout stays open until it is gone: an engine that writes on its way out must not fail.
        drop(stdin);
        let _stdout = stdout;
        let pid = child.id() as i32;
        for sig in [None, Some(libc::SIGTERM), Some(libc::SIGKILL)] {
            if let Some(sig) = sig {
                if matches!(child.try_wait(), Ok(None)) {
                    // SAFETY: killpg with a process group id has no memory effects.
                    unsafe { libc::killpg(pid, sig) };
                }
            }
            let deadline = Instant::now() + self.stop_wait;
            loop {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        *self.last_exit.lock().unwrap() = Some(status.code().unwrap_or_else(|| -status.signal().unwrap_or(0)));
                        return;
                    }
                    Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(5)),
                    _ => break,
                }
            }
        }
    }
}

impl Drop for ProcessEngine {
    fn drop(&mut self) {
        self.stop();
    }
}

// --- the run's worker ---------------------------------------------------------------------------------------------

/// What happened, for the loop (ViewControl.poll).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    /// Up and drawing these desktops.
    Active(Vec<String>),
    /// All down.
    Off,
    /// Fell back to 2D; `kind` is a toast kind (or `renderer`).
    Failed { kind: &'static str, why: String },
    /// The glasses would not leave SBS.
    Leave(String),
}

impl Event {
    /// Python's `': '.join(event[1:])` for a failure.
    fn detail(&self) -> String {
        match self {
            Event::Failed { kind, why } => format!("{kind}: {why}"),
            Event::Leave(why) => why.clone(),
            _ => String::new(),
        }
    }
}

/// A request's completion: (success, detail at most 256 bytes).
pub type Done = Box<dyn FnOnce(bool, String) + Send>;

/// Bounded completion, isolated from a callback that panics (send.complete).
fn complete(done: Done, success: bool, detail: &str) {
    let mut end = detail.len().min(256);
    while !detail.is_char_boundary(end) {
        end -= 1;
    }
    let detail = detail[..end].to_owned();
    let _ = catch_unwind(AssertUnwindSafe(move || done(success, detail)));
}

const VERIFIED: &str = "Engine, physical mode and native drawing verified";
const SAVED: &str = "Saved for next 3D";

struct Completion {
    names: Vec<String>,
    done: Done,
    setting: Option<Setting>,
}

#[derive(Default)]
struct State {
    /// What the loop wants; what is really up.
    target: Vec<String>,
    current: Vec<String>,
    events: Vec<Event>,
    stopping: bool,
    reassert: bool,
    failed: bool,
    /// Suppress stale requests until the loop takes the failure.
    failure_pending: bool,
    phase: &'static str,
    error: String,
    starts: u32,
    completions: Vec<Completion>,
    readback: Option<Value>,
    next_mode_probe: Option<Instant>,
}

struct Inner {
    engine: Arc<dyn Engine>,
    sbs: Arc<dyn Sbs>,
    state: Mutex<State>,
    cond: Condvar,
    emit: Mutex<()>,
}

/// Runs an adapter call; a panic is a failure, not a dead worker.
fn guarded<T>(f: impl FnOnce() -> T) -> Option<T> {
    catch_unwind(AssertUnwindSafe(f)).ok()
}

const INTERNAL: &str = "an internal error";

fn or_name(text: String, name: &str) -> String {
    if text.is_empty() { name.to_owned() } else { text }
}

/// A run's 3D: its worker takes the engine and the glasses to what the loop asked for. `request` and `take_events`
/// never block. On: engine first, then the glasses. Off: the glasses, then the engine. A failure at any point brings
/// down whatever came up and latches until an explicit off (or a new press after the loop took the failure).
pub struct ImmersiveRun {
    inner: Arc<Inner>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl ImmersiveRun {
    pub fn new(engine: Arc<dyn Engine>, sbs: Arc<dyn Sbs>) -> ImmersiveRun {
        let state = State { phase: "off", ..State::default() };
        ImmersiveRun {
            inner: Arc::new(Inner { engine, sbs, state: Mutex::new(state), cond: Condvar::new(), emit: Mutex::new(()) }),
            thread: Mutex::new(None),
        }
    }

    /// For the panel: `off`, `engine` (loading the model), `glasses` (waiting for the button) or `on`.
    pub fn phase(&self) -> &'static str {
        self.inner.lock().phase
    }

    pub fn error(&self) -> String {
        self.inner.lock().error.clone()
    }

    /// Diagnostics: engine starts.
    pub fn starts(&self) -> u32 {
        self.inner.lock().starts
    }

    /// What is really up.
    pub fn current(&self) -> Vec<String> {
        self.inner.lock().current.clone()
    }

    /// The worker exists and has not ended.
    pub fn worker_running(&self) -> bool {
        self.thread.lock().unwrap().as_ref().is_some_and(|t| !t.is_finished())
    }

    pub fn has_worker(&self) -> bool {
        self.thread.lock().unwrap().is_some()
    }

    /// Bring up these desktops ([]: all of it down). The worker starts here, so a run that never uses 3D never has
    /// one. `done` hears when the result is verified on the renderer; `setting`: the look that must be verified too.
    pub fn request(&self, desktops: &[String], done: Option<Done>, setting: Option<&Setting>) {
        let inner = &self.inner;
        let mut calls: Vec<(Done, bool, String)> = Vec::new();
        let mut st = inner.lock();
        let changed = desktops != st.target.as_slice()
            || setting.is_some_and(|s| st.completions.iter().any(|c| c.setting.as_ref() != Some(s)));
        if changed {
            for c in std::mem::take(&mut st.completions) {
                calls.push((c.done, false, String::from("3D request superseded by another owner action")));
            }
        }
        let mut finish = false;
        if let Some(done) = done {
            if st.stopping || (!desktops.is_empty() && (st.failure_pending || st.failed)) {
                let why = if st.error.is_empty() { "3D stopped or failure latched; explicit off/retry required".into() } else { st.error.clone() };
                calls.push((done, false, why));
                finish = true;
            } else if desktops == st.current.as_slice() && inner.verified(&st, desktops, setting) {
                let why = if desktops.is_empty() && setting.is_some() { SAVED } else { VERIFIED };
                calls.push((done, true, why.into()));
                finish = true;
            } else {
                for c in std::mem::take(&mut st.completions) {
                    calls.push((c.done, false, String::from("3D request superseded")));
                }
                st.completions = vec![Completion { names: desktops.to_vec(), done, setting: setting.cloned() }];
            }
        }
        if !finish {
            if desktops.is_empty() {
                st.failed = false;
            }
            let latched = !desktops.is_empty() && (st.failure_pending || st.failed);
            if !(st.stopping || desktops == st.target.as_slice() || latched) {
                st.target = desktops.to_vec();
                let mut thread = self.thread.lock().unwrap();
                if !desktops.is_empty() && thread.is_none() {
                    let worker = inner.clone();
                    *thread = Some(std::thread::Builder::new().name("xr-immersive".into()).spawn(move || worker.work()).expect("spawn the 3D worker"));
                }
                inner.cond.notify_all();
            }
        }
        drop(st);
        for (done, ok, why) in calls {
            complete(done, ok, &why);
        }
    }

    /// A config reload reset the output's mode: if the glasses are in SBS, say so again.
    pub fn reassert(&self) {
        let mut st = self.inner.lock();
        st.reassert = !st.current.is_empty();
        self.inner.cond.notify_all();
    }

    pub fn take_events(&self) -> Vec<Event> {
        let mut st = self.inner.lock();
        let events = std::mem::take(&mut st.events);
        if events.iter().any(|e| matches!(e, Event::Failed { .. })) {
            st.failure_pending = false;
        }
        events
    }

    /// The glasses are changing mode, so the output going for a moment is expected.
    pub fn switching(&self) -> bool {
        let now = Instant::now();
        let probe = {
            let mut st = self.inner.lock();
            let due = st.next_mode_probe.is_none_or(|t| now >= t);
            if due {
                st.next_mode_probe = Some(now + Duration::from_secs(1));
            }
            due
        };
        if probe {
            let _ = guarded(|| self.inner.sbs.follow_by_hand());
        }
        let pending = {
            let st = self.inner.lock();
            st.target != st.current
        };
        pending || guarded(|| self.inner.sbs.switching()).unwrap_or(false)
    }

    /// The supervisor's bounded readback of the renderer's 3D state, on its loop.
    pub fn observe_renderer(&self, report: Value) {
        let inner = &self.inner;
        let (failure, phase, current) = {
            let mut st = inner.lock();
            let failure = ["error", "engine_error"].iter().find_map(|k| report.get(*k).filter(|v| js::truthy(v)).map(js::text));
            st.readback = Some(report);
            (failure, st.phase, st.current.clone())
        };
        if let Some(why) = failure {
            inner.emit(Event::Failed { kind: "renderer", why });
        } else if phase == "on" {
            inner.emit(Event::Active(current));
        } else if phase == "off" {
            inner.emit(Event::Off);
        }
    }

    /// Run end: everything down, before recovery. Bounded: an adapter that hangs cannot hold recovery up, so the
    /// engine is then stopped from here and the worker abandoned.
    pub fn close(&self, timeout: Duration) {
        let calls = {
            let mut st = self.inner.lock();
            st.stopping = true;
            st.target.clear();
            self.inner.cond.notify_all();
            std::mem::take(&mut st.completions)
        };
        for c in calls {
            complete(c.done, false, "3D session stopping");
        }
        let mut thread = self.thread.lock().unwrap();
        let Some(t) = thread.as_ref() else { return };
        let deadline = Instant::now() + timeout;
        while !t.is_finished() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(1));
        }
        if t.is_finished() {
            let _ = thread.take().map(JoinHandle::join);
        } else {
            let _ = guarded(|| self.inner.engine.stop());
        }
    }
}

impl Drop for ImmersiveRun {
    fn drop(&mut self) {
        self.close(CLOSE_WAIT);
    }
}

fn is_true(v: Option<&Value>) -> bool {
    v == Some(&Value::Bool(true))
}

fn is_false(v: Option<&Value>) -> bool {
    v == Some(&Value::Bool(false))
}

impl Inner {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The renderer's readback shows `names` up (or all down), with `setting`'s look if given.
    fn verified(&self, st: &State, names: &[String], setting: Option<&Setting>) -> bool {
        let Some(report) = st.readback.as_ref().filter(|r| r.is_object()) else { return false };
        if report.get("error").is_some_and(js::truthy) || report.get("engine_error").is_some_and(js::truthy) {
            return false;
        }
        if names.is_empty() {
            return st.phase == "off"
                && st.current.is_empty()
                && is_false(report.get("enabled"))
                && is_false(report.get("physical_sbs"))
                && !guarded(|| self.sbs.parked()).unwrap_or(false);
        }
        if st.phase != "on" || st.current != names || !is_true(report.get("physical_sbs")) || !is_true(report.get("available")) {
            return false;
        }
        let rows: &[Value] = report.get("desktops").and_then(Value::as_array).map_or(&[], Vec::as_slice);
        let row = |name: &str, ok: &dyn Fn(&Value) -> bool| rows.iter().any(|r| r.get("monitor").and_then(Value::as_str) == Some(name) && ok(r));
        if !names.iter().all(|n| row(n, &|r| is_true(r.get("drawing")) && is_true(r.get("depth_ready")))) {
            return false;
        }
        if let Some(setting) = setting {
            let p = wire_params(setting);
            let empty = Map::new();
            let actual = report.get("params").and_then(Value::as_object).unwrap_or(&empty);
            let expected = [
                ("strength", p.strength as f64 / 100.0),
                ("convergence", p.convergence as f64 / 100.0),
                ("near_deg", p.near as f64 / 60.0),
                ("far_deg", p.far as f64 / 60.0),
                ("text_keep", p.text as f64 / 100.0),
            ];
            if expected.iter().any(|(k, v)| actual.get(*k).and_then(js::number).is_none_or(|a| (a - v).abs() > 0.0001)) {
                return false;
            }
            for name in names {
                let wanted = setting.strengths.as_ref().and_then(|s| s.get(name)).copied().unwrap_or(setting.strength) as f64 / 100.0;
                if !row(name, &|r| r.get("strength").and_then(js::number).is_some_and(|s| (s - wanted).abs() < 0.0001)) {
                    return false;
                }
            }
        }
        true
    }

    fn emit(&self, event: Event) {
        let _serial = self.emit.lock().unwrap_or_else(|e| e.into_inner());
        let calls = {
            let mut st = self.lock();
            match &event {
                Event::Failed { .. } | Event::Leave(_) => {
                    st.error = event.detail();
                    std::mem::take(&mut st.completions).into_iter().map(|c| (c.done, false, event.detail())).collect::<Vec<_>>()
                }
                Event::Active(_) | Event::Off => {
                    if let Event::Active(_) = event {
                        st.error.clear();
                    }
                    let target: &[String] = match &event {
                        Event::Active(names) => names,
                        _ => &[],
                    };
                    let (hit, keep): (Vec<Completion>, Vec<Completion>) = std::mem::take(&mut st.completions)
                        .into_iter()
                        .partition(|c| c.names == target && self.verified(&st, target, c.setting.as_ref()));
                    st.completions = keep;
                    let off = matches!(event, Event::Off);
                    hit.into_iter().map(|c| { let why = if off && c.setting.is_some() { SAVED } else { VERIFIED }; (c.done, true, why.to_owned()) }).collect()
                }
            }
        };
        for (done, ok, why) in calls {
            complete(done, ok, &why);
        }
        // Published only after the callbacks have queued their completion.
        self.lock().events.push(event);
    }

    fn cancelled(&self) -> bool {
        let st = self.lock();
        st.stopping || st.target.is_empty()
    }

    fn set_phase(&self, phase: &'static str) {
        self.lock().phase = phase;
    }

    fn parked(&self) -> bool {
        guarded(|| self.sbs.parked()).unwrap_or(false)
    }

    /// The glasses, then the engine. Never fails.
    fn down(&self) {
        match guarded(|| self.sbs.leave()) {
            Some(Ok(())) => {}
            Some(Err(SbsError(why))) => self.emit(Event::Leave(or_name(why, "SbsError"))),
            None => self.emit(Event::Leave(INTERNAL.into())), // the engine must still stop
        }
        let _ = guarded(|| self.engine.stop());
        let mut st = self.lock();
        st.current.clear();
        st.phase = "off";
    }

    /// Back to flat from any point. The target goes too: the loop latches this until a new press.
    fn fail(&self, kind: &'static str, why: String) {
        let was_up = {
            let mut st = self.lock();
            let was_up = !st.current.is_empty();
            st.target.clear();
            st.reassert = false;
            st.error = format!("{kind}: {why}");
            st.failure_pending = true;
            st.failed = true;
            was_up
        };
        if was_up || kind == "sbs" {
            self.down();
        } else {
            let _ = guarded(|| self.engine.stop());
        }
        self.set_phase("off");
        self.emit(Event::Failed { kind, why });
    }

    fn up(&self, target: Vec<String>) {
        {
            let mut st = self.lock();
            st.starts += 1;
            st.phase = "engine";
        }
        let cancelled = || self.cancelled();
        match guarded(|| self.engine.start(&target, &cancelled)) {
            Some(Ok(true)) => {}
            Some(Ok(false)) => return self.set_phase("off"),
            Some(Err(EngineError(why))) => return self.fail("engine", why),
            None => return self.fail("engine", INTERNAL.into()),
        }
        if self.cancelled() {
            let _ = guarded(|| self.engine.stop());
            return self.set_phase("off");
        }
        self.set_phase("glasses");
        match guarded(|| self.sbs.enter(&cancelled)) {
            Some(Ok(true)) => {}
            Some(Ok(false)) => {
                // The press was called off.
                let _ = guarded(|| self.engine.stop());
                return self.set_phase("off");
            }
            Some(Err(SbsError(why))) => return self.fail("sbs", or_name(why, "SbsError")),
            None => return self.fail("sbs", INTERNAL.into()),
        }
        if !guarded(|| self.engine.alive()).unwrap_or(false) {
            return self.fail("died", String::from("it quit")); // it had up to a minute alone
        }
        {
            let mut st = self.lock();
            st.current = target.clone();
            st.phase = "on";
        }
        self.emit(Event::Active(target));
    }

    fn change(&self, target: Vec<String>, current: &[String], reassert: bool) {
        if reassert {
            let cancelled = || self.cancelled();
            match guarded(|| self.sbs.enter(&cancelled)) {
                Some(Ok(_)) => {}
                Some(Err(SbsError(why))) => return self.fail("sbs", or_name(why, "SbsError")),
                None => return self.fail("sbs", INTERNAL.into()),
            }
        }
        if target == current {
            return;
        }
        match guarded(|| self.engine.set_desktops(&target)) {
            Some(Ok(())) => {}
            Some(Err(EngineError(why))) => return self.fail("died", or_name(why, "EngineError")),
            None => return self.fail("died", INTERNAL.into()),
        }
        self.lock().current = target.clone();
        self.emit(Event::Active(target));
    }

    fn work(&self) {
        loop {
            let (stopping, target, reassert, current) = {
                let mut st = self.lock();
                if !st.stopping && !st.reassert && st.target == st.current {
                    let watch = !st.current.is_empty() || self.parked();
                    st = if watch {
                        self.cond.wait_timeout(st, WATCH).unwrap_or_else(|e| e.into_inner()).0
                    } else {
                        self.cond.wait(st).unwrap_or_else(|e| e.into_inner())
                    };
                }
                let snapshot = (st.stopping, st.target.clone(), st.reassert, st.current.clone());
                st.reassert = false;
                snapshot
            };
            if stopping {
                if !current.is_empty() {
                    self.down();
                }
                return;
            }
            if !current.is_empty() && !guarded(|| self.engine.alive()).unwrap_or(false) {
                self.fail("died", String::from("it quit"));
            } else if !current.is_empty() && guarded(|| self.sbs.left_by_hand()).unwrap_or(false) {
                let button = guarded(|| self.sbs.button().to_owned()).unwrap_or_else(|| String::from("R1"));
                self.fail("sbs", format!("{button} took them back to 2D"));
            } else if target == current && !reassert {
                if current.is_empty() && self.parked() {
                    if let Some(Err(SbsError(why))) = guarded(|| self.sbs.settle()) {
                        self.emit(Event::Leave(or_name(why, "SbsError")));
                    }
                }
            } else if target.is_empty() {
                self.down();
                self.emit(Event::Off);
            } else if current.is_empty() {
                self.up(target);
            } else {
                self.change(target, &current, reassert);
            }
        }
    }
}

#[cfg(test)]
mod tests;
