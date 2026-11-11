//! The real backend's managed run against the Python's, on one scripted compositor (tests/managed_real/).
//!
//! Each case starts a fake Hyprland with its ownership plugin (`compositor.py`, real sockets, every request logged with
//! its sender's pid) and a fake renderer (`renderer.py`, the supervised protocol only), then runs the run unit twice in
//! fresh folders: the Python runner (`oracle.py`, which only takes the view controls out, as the Rust run has none yet)
//! and `xr-workspace supervisor` on its real backend. The two must send the same requests in the same order from the
//! run's own process, leave the same journal, the same layout snapshot and the same result, and launch the renderer
//! with the same arguments. The guard worker's own requests (another process, on its own clock) are left out of the
//! comparison; that it ran and inspected is checked.
//!
//! The cases are the start, the stop and each failure point a compositor can cause: a renderer that ends by itself,
//! fails, falls silent or is stopped; a broker that refuses the hello, a create that does not go live, a removal it
//! refuses (then Python's Recover finishes the run the Rust supervisor started); the glasses missing at the start and
//! the fallback monitor lost mid-run.
//!
//! # What the Python side leaves out, and whose port takes each exclusion away
//!
//! The Rust run has the stubs of `xr_workspace::view_controls` only (rust/crates/xr-workspace/VIEW_CONTROLS.md), so
//! the Python side runs none of those features either. Each exclusion, by the module that removes it:
//!
//! - `oracle.py`, `managed.view_controls = no_view_controls`: all of `view_controls` (`socket`, `binds`, `view`,
//!   `overview_pointer`, `send`, `privacy`, `immersive`, `keybinds`, `video`, and the `desktops` and
//!   `window_memory` wiring), all but the window memory's last capture. It stays so until `socket`, `view` and
//!   `binds` are ported; then oracle.py runs the shipped `view_controls` with one patch per feature still stubbed, and each feature's port
//!   deletes its own patch (VIEW_CONTROLS.md, "Narrowing the oracle").
//! - `desktops`: `_run_owned` builds its `DesktopManager` on the Python side too, which sends nothing until a pill
//!   asks for a change through the view controls.
//! - `window_memory`: ported. `oracle.py`'s stand-in still takes the tracker's last capture in its `finally`, as
//!   `view_controls` does; a `Session` has no `window-memory.json` unless its case writes one
//!   (`managed_real/cases/window_memory.rs`), so `place` and `finish` send nothing in the other cases.
//! - `setup`: ported. A `Session` has no saved `profiles.json` unless its case writes one (`cases/setup.rs`).
//!
//! A case can start from more than the defaults (`Start`, `parity_from`): its consent's desktops, and files written
//! before the run, which `parity` compares as the runs left them (`cases/setup.rs`: a saved `profiles.json`).

use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::DirBuilderExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use xr_workspace::install::sha_hex;

const INSTANCE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_123_456";

fn here() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/managed_real")
}

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap()
}

/// An executable at `path`, written by a child process (an in-process write races sibling forks into ETXTBSY).
fn write_script(path: &Path, body: &str) {
    let status = Command::new("/bin/sh")
        .args([
            "-c",
            "printf '%s' \"$1\" > \"$2\" && chmod 700 \"$2\"",
            "sh",
            body,
        ])
        .arg(path)
        .status()
        .unwrap();
    assert!(status.success());
}

fn copy_executable(from: &Path, to: &Path) {
    let status = Command::new("/bin/cp").arg(from).arg(to).status().unwrap();
    assert!(status.success());
}

fn private(path: &Path, data: &[u8]) {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .unwrap();
    std::io::Write::write_all(&mut f, data).unwrap();
}

fn wait_for(path: &Path, limit: Duration) -> bool {
    let end = Instant::now() + limit;
    while Instant::now() < end {
        if path.exists() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    false
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Who {
    Python,
    Rust,
}

/// What a case starts from besides its compositor script: the consent's desktops, and files (relative to the
/// session's folder) written before the run. `parity` compares each file as the run left it.
struct Start {
    desktops: Value,
    files: Vec<(&'static str, String)>,
}

impl Default for Start {
    fn default() -> Start {
        let desktop = json!({"width": 1920, "height": 1080, "scale": 1.0});
        Start {
            desktops: json!([desktop, desktop]),
            files: Vec::new(),
        }
    }
}

/// One run's folders and its compositor, removed and stopped on drop.
struct Session {
    root: PathBuf,
    case: &'static str,
    compositor: Child,
    consent: PathBuf,
    files: Vec<&'static str>,
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.compositor.kill();
        let _ = self.compositor.wait();
        let _ = fs::remove_dir_all(&self.root);
    }
}

impl Session {
    /// A session from the defaults.
    fn new(case: &'static str, who: Who) -> Session {
        Session::start(case, who, &Start::default())
    }

    fn start(case: &'static str, who: Who, start: &Start) -> Session {
        // Numbered too: two tests may run the same case at once.
        static SESSIONS: AtomicUsize = AtomicUsize::new(0);
        let root = std::env::temp_dir().join(format!(
            "xr-managed-real-{case}-{who:?}-{}-{}",
            std::process::id(),
            SESSIONS.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&root);
        for sub in ["", "run", "state", "config", "home", "consents"] {
            fs::DirBuilder::new()
                .mode(0o700)
                .recursive(true)
                .create(root.join(sub))
                .unwrap();
        }
        let plugin_body = b"plugin build";
        let bin = root
            .join("data/viture-workspace/bin")
            .join(&sha_hex(plugin_body)[..12]);
        fs::create_dir_all(&bin).unwrap();
        fs::write(bin.join("libxr-ownership.so"), plugin_body).unwrap();
        let renderer = fs::read_to_string(here().join("renderer.py")).unwrap();
        write_script(&bin.join("xr-native-spike"), &renderer);
        let pin =
            |file: &str, body: &[u8]| json!({"path": bin.join(file), "sha256": sha_hex(body)});
        let pins = json!({"plugin": pin("libxr-ownership.so", plugin_body),
                          "renderer": pin("xr-native-spike", renderer.as_bytes())});
        fs::write(
            root.join("data/viture-workspace/installed.json"),
            pins.to_string(),
        )
        .unwrap();
        for (file, body) in &start.files {
            let path = root.join(file);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, body).unwrap();
        }
        let consent = json!({
            "schema": 1, "gate": "P2-panel", "run_id": "r1", "instance": INSTANCE, "output": "DP-2",
            "recovery_output": "DP-1", "model": "luma-ultra", "allow": ["headless-outputs", "capture-managed-outputs"],
            "capture": "managed-outputs", "tracking": false, "mode_switch": false, "plugin": pins["plugin"],
            "renderer": pins["renderer"], "desktops": start.desktops, "input": false, "sdk": false,
        });
        let consent_path = root.join("consents/r1.json");
        private(&consent_path, consent.to_string().as_bytes());
        let hyprland = root.join("Hyprland");
        copy_executable(Path::new("/usr/bin/python3"), &hyprland);
        let compositor = Command::new(&hyprland)
            .arg(here().join("compositor.py"))
            .arg(root.join("run"))
            .args([INSTANCE, case])
            .stdin(Stdio::null())
            .spawn()
            .unwrap();
        assert!(
            wait_for(&root.join("run/fake-ready"), Duration::from_secs(5)),
            "the fake compositor did not start"
        );
        Session {
            root,
            case,
            compositor,
            consent: consent_path,
            files: start.files.iter().map(|(f, _)| *f).collect(),
        }
    }

    fn command(&self, program: &Path) -> Command {
        let r = &self.root;
        let mut cmd = Command::new(program);
        cmd.env_clear()
            .env("HOME", r.join("home"))
            .env("XDG_DATA_HOME", r.join("data"))
            .env("XDG_CONFIG_HOME", r.join("config"))
            .env("XDG_STATE_HOME", r.join("state-home"))
            .env("XDG_RUNTIME_DIR", r.join("run"))
            .env("PATH", "/usr/bin:/bin")
            .env("TMPDIR", "/tmp")
            .env(
                "DBUS_SESSION_BUS_ADDRESS",
                format!("unix:path={}", r.join("no-bus").display()),
            )
            .env("FAKE_CASE", self.case)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        cmd
    }

    fn runner(&self, who: Who) -> Command {
        let state = self.root.join("state");
        match who {
            Who::Python => {
                let mut cmd = self.command(Path::new("/usr/bin/python3"));
                cmd.arg("-I")
                    .arg(here().join("oracle.py"))
                    .arg(repo())
                    .arg("runner");
                cmd.arg("--consent")
                    .arg(&self.consent)
                    .arg("--state-dir")
                    .arg(state);
                cmd
            }
            Who::Rust => {
                let mut cmd = self.command(Path::new(env!("CARGO_BIN_EXE_xr-workspace")));
                cmd.arg("supervisor")
                    .arg("--consent")
                    .arg(&self.consent)
                    .arg("--state-dir")
                    .arg(state);
                cmd
            }
        }
    }

    /// The run, with the case's action once the renderer is up: Stop (SIGINT) or the fallback unplugged.
    fn run(&self, who: Who) -> (i32, String) {
        let child = self.runner(who).spawn().unwrap();
        let started = self.root.join("run/renderer-started");
        match self.case {
            "stop" | "recovery-stuck" => {
                if wait_for(&started, Duration::from_secs(20)) {
                    std::thread::sleep(Duration::from_millis(300));
                    unsafe { libc::kill(child.id() as i32, libc::SIGINT) };
                }
            }
            "unplug" => {
                if wait_for(&started, Duration::from_secs(20)) {
                    std::thread::sleep(Duration::from_millis(300));
                    fs::write(self.root.join("run/fake-unplug"), "").unwrap();
                }
            }
            _ => {}
        }
        let out = wait_bounded(child, Duration::from_secs(40));
        (
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    }

    fn recover(&self) -> (i32, String) {
        let mut cmd = self.command(Path::new("/usr/bin/python3"));
        cmd.arg("-I")
            .arg(here().join("oracle.py"))
            .arg(repo())
            .arg("recover-run");
        cmd.arg("--consent")
            .arg(&self.consent)
            .arg("--journal")
            .arg(self.journal_path());
        let out = wait_bounded(cmd.spawn().unwrap(), Duration::from_secs(30));
        (
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    }

    fn journal_path(&self) -> PathBuf {
        self.root.join("run/xr-workspace-spike/r1/journal.json")
    }

    fn wire(&self) -> Vec<Value> {
        fs::read_to_string(self.root.join("run/wire.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}

fn wait_bounded(mut child: Child, limit: Duration) -> std::process::Output {
    let end = Instant::now() + limit;
    while Instant::now() < end {
        if child.try_wait().unwrap().is_some() {
            return child.wait_with_output().unwrap();
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = child.kill();
    let out = child.wait_with_output().unwrap();
    panic!("run did not end: {}", String::from_utf8_lossy(&out.stderr));
}

/// Names for what differs between two runs by nature: output and record UUIDs (by first appearance), the session id
/// (a hash of the compositor's pid), the run's folder, descriptor numbers.
struct Normal {
    root: String,
    uuids: HashMap<String, String>,
}

impl Normal {
    fn text(&mut self, s: &str) -> String {
        let mut out = s.replace(&self.root, "ROOT");
        let bytes: Vec<char> = out.chars().collect();
        let mut found = Vec::new();
        let hex = |c: char| c.is_ascii_hexdigit() && !c.is_ascii_uppercase();
        let mut i = 0;
        while i < bytes.len() {
            if i + 36 <= bytes.len()
                && bytes[i..i + 36].iter().enumerate().all(|(k, &c)| match k {
                    8 | 13 | 18 | 23 => c == '-',
                    _ => hex(c),
                })
            {
                found.push(bytes[i..i + 36].iter().collect::<String>());
                i += 36;
            } else if i + 64 <= bytes.len() && bytes[i..i + 64].iter().all(|&c| hex(c)) {
                found.push(bytes[i..i + 64].iter().collect::<String>());
                i += 64;
            } else {
                i += 1;
            }
        }
        for u in found {
            let n = self.uuids.len();
            let name = self
                .uuids
                .entry(u.clone())
                .or_insert_with(|| {
                    if u.len() == 64 {
                        "SESSION".into()
                    } else {
                        format!("U{n}")
                    }
                })
                .clone();
            out = out.replace(&u, &name);
        }
        out
    }
}

/// What one run left, normalized.
#[derive(Debug, PartialEq)]
struct Observed {
    trace: Vec<String>,
    journal: Option<String>,
    layout: Option<String>,
    result: Value,
    renderers: Vec<String>,
    marks: Vec<String>,
    /// The `Start`'s files as the run left them.
    files: Vec<Option<String>>,
}

fn observe(s: &Session) -> (Observed, Vec<Value>) {
    let mut n = Normal {
        root: s.root.display().to_string(),
        uuids: HashMap::new(),
    };
    let wire = s.wire();
    let errors: Vec<&Value> = wire
        .iter()
        .filter(|e| e.get("error").is_some() && e["error"] == "unhandled request")
        .collect();
    assert!(
        errors.is_empty(),
        "requests the fake does not know: {errors:?}"
    );
    let main = wire.iter().find_map(|e| e["pid"].as_i64());
    let trace: Vec<String> = wire
        .iter()
        .filter(|e| e["pid"].as_i64() == main && e.get("request").is_some())
        .map(|e| n.text(e["request"].as_str().unwrap()))
        .collect();
    let others: Vec<Value> = wire
        .iter()
        .filter(|e| e["pid"].as_i64() != main)
        .cloned()
        .collect();
    let journal = fs::read_to_string(s.journal_path())
        .ok()
        .map(|t| n.text(&t));
    let layout = fs::read_to_string(s.root.join("state-home/viture-workspace/layouts/r1.json"))
        .ok()
        .map(|t| n.text(&t));
    let mut result: Value =
        serde_json::from_str(&fs::read_to_string(s.root.join("state/r1.result.json")).unwrap())
            .unwrap();
    let marks: Vec<String> = result["startup"]["marks_ns"]
        .as_object()
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default();
    result.as_object_mut().unwrap().remove("startup");
    if ["success", "renderer-fail"].contains(&s.case) && result["stop_reason"] == "renderer-closed"
    {
        // A renderer that exits closes its heartbeat socket as it goes: which the watchdog sees first is a race.
        result.as_object_mut().unwrap().remove("stop_reason");
    }
    if let Some(faults) = result.get_mut("faults").and_then(Value::as_array_mut) {
        // Python keeps a traceback, Rust the error line alone (runner.rs `Faults`): the message is compared.
        for fault in faults {
            let last = fault["traceback"]
                .as_array()
                .and_then(|t| t.last())
                .and_then(Value::as_str)
                .unwrap_or("");
            let message = last.split_once(": ").map_or(last, |(_, m)| m).to_owned();
            fault["traceback"] = json!(message);
        }
    }
    let result: Value = serde_json::from_str(&n.text(&result.to_string())).unwrap();
    let renderers = fs::read_to_string(s.root.join("run/renderer.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|l| {
            let argv: Vec<String> = serde_json::from_str(l).unwrap();
            let mut out = Vec::new();
            for (i, a) in argv.iter().enumerate() {
                let fd = i > 0 && argv[i - 1].ends_with("-fd");
                out.push(if fd { "FD".to_owned() } else { n.text(a) });
            }
            out.join(" ")
        })
        .collect();
    let files = s
        .files
        .iter()
        .map(|f| fs::read_to_string(s.root.join(f)).ok().map(|t| n.text(&t)))
        .collect();
    (
        Observed {
            trace,
            journal,
            layout,
            result,
            renderers,
            marks,
            files,
        },
        others,
    )
}

fn diff(a: &[String], b: &[String]) -> String {
    let mut out = String::new();
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (a.get(i), b.get(i));
        if x != y {
            out += &format!(
                "{i}: python {:.160}\n{i}: rust   {:.160}\n",
                x.map_or("-", |s| s),
                y.map_or("-", |s| s)
            );
        }
    }
    out
}

/// Both supervisors on `case`; the Rust one's session is returned for more checks.
fn parity(case: &'static str) -> (Observed, Session) {
    parity_from(case, &Start::default())
}

/// `parity` from `start`.
fn parity_from(case: &'static str, start: &Start) -> (Observed, Session) {
    let py = Session::start(case, Who::Python, start);
    let (py_code, py_err) = py.run(Who::Python);
    let (python, _) = observe(&py);
    drop(py);
    let rs = Session::start(case, Who::Rust, start);
    let (rs_code, rs_err) = rs.run(Who::Rust);
    let (rust, others) = observe(&rs);
    assert_eq!(
        python.trace,
        rust.trace,
        "{case}: the requests differ\n{}\npython stderr:\n{py_err}\nrust stderr:\n{rs_err}",
        diff(&python.trace, &rust.trace)
    );
    let context = format!("{case}\npython stderr:\n{py_err}\nrust stderr:\n{rs_err}");
    assert_eq!(python.journal, rust.journal, "journal: {context}");
    assert_eq!(python.layout, rust.layout, "layout snapshot: {context}");
    assert_eq!(python.result, rust.result, "result: {context}");
    assert_eq!(
        python.renderers, rust.renderers,
        "renderer arguments: {context}"
    );
    assert_eq!(python.marks, rust.marks, "start-up marks: {context}");
    assert_eq!(
        python.files, rust.files,
        "the files it started with: {context}"
    );
    assert_eq!(py_code, rs_code, "{case}: exit status");
    if !rust.renderers.is_empty() {
        // The guard is a worker process of its own; it proved the outputs through the broker.
        let inspected = others.iter().any(|e| {
            e["request"]
                .as_str()
                .is_some_and(|r| r.starts_with("j/xrspike ") && r.contains("\"op\":\"inspect\""))
        });
        assert!(
            inspected,
            "{case}: no guard inspected the outputs: {others:?}"
        );
    }
    (rust, rs)
}

fn journal_states(text: &str) -> Vec<String> {
    let v: Value = serde_json::from_str(text).unwrap();
    v["operations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["state"].as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn a_renderer_that_ends_by_itself() {
    let (run, _) = parity("success");
    assert_eq!(run.result["complete"], true);
    assert_eq!(run.result["renderer_returncode"], 0);
    assert_eq!(
        journal_states(run.journal.as_deref().unwrap()),
        ["done", "done"]
    );
    assert_eq!(
        run.layout, None,
        "a complete recovery discards the layout snapshot"
    );
    assert!(
        run.renderers[0].contains("--capture XR-1,XR-2"),
        "{:?}",
        run.renderers
    );
    assert!(
        run.marks.contains(&"renderer".to_owned()),
        "{:?}",
        run.marks
    );
}

#[test]
fn stop() {
    let (run, _) = parity("stop");
    assert_eq!(run.result["stop_reason"], "interrupted");
    assert_eq!(run.result["complete"], true);
}

#[test]
fn a_renderer_that_fails() {
    let (run, _) = parity("renderer-fail");
    assert_eq!(run.result["renderer_returncode"], 9);
    assert_eq!(run.result["complete"], true);
}

#[test]
fn a_renderer_that_falls_silent() {
    let (run, _) = parity("heartbeat-stop");
    assert_eq!(run.result["stop_reason"], "heartbeat-timeout");
}

#[test]
fn the_fallback_lost_mid_run() {
    let (run, _) = parity("unplug");
    assert_eq!(run.result["stop_reason"], "guard-failed");
}

#[test]
fn a_broker_that_refuses_the_hello() {
    let (run, _) = parity("hello-refused");
    assert_eq!(run.result["error"], "broker handshake not qualified");
    assert_eq!(
        run.journal, None,
        "nothing is written before the broker owns the outputs"
    );
    assert!(run.renderers.is_empty());
}

#[test]
fn a_create_that_does_not_go_live() {
    let (run, _) = parity("create-fail");
    // The second output never went live; recovery removed both inside the run. A run that raised keeps the plugin
    // all the same, for an explicit Recover (runner.execute).
    assert_eq!(
        run.result["error"],
        "creation incomplete; ownership plugin retained for explicit recovery"
    );
    assert_eq!(
        journal_states(run.journal.as_deref().unwrap()),
        ["done", "done"]
    );
}

#[test]
fn the_glasses_missing_at_the_start() {
    let (run, _) = parity("no-glasses");
    assert!(run.result["error"].is_string());
    assert!(run.renderers.is_empty());
}

#[test]
fn python_recovers_a_run_the_rust_supervisor_left() {
    let (run, rs) = parity("recovery-stuck");
    assert_eq!(run.result["complete"], false);
    let states = journal_states(run.journal.as_deref().unwrap());
    assert!(states.iter().any(|s| s != "done"), "{states:?}");
    assert!(
        run.layout.is_some(),
        "an unfinished recovery keeps the layout snapshot for the next attempt"
    );
    let (code, stderr) = rs.recover();
    assert_eq!(code, 0, "Python's Recover of the Rust run: {stderr}");
    let journal = fs::read_to_string(rs.journal_path()).unwrap();
    assert!(
        journal_states(&journal).iter().all(|s| s == "done"),
        "{journal}"
    );
    assert!(
        !rs.root
            .join("state-home/viture-workspace/layouts/r1.json")
            .exists()
    );
    let wire = rs.wire();
    let last = wire
        .iter()
        .rev()
        .find_map(|e| e["request"].as_str())
        .unwrap_or("");
    assert!(
        wire.iter().any(|e| e["request"]
            .as_str()
            .is_some_and(|r| r.starts_with("/plugin unload "))),
        "Recover unloads the plugin it kept (last request: {last})"
    );
}

// Each view-controls feature's cases, in its own file (VIEW_CONTROLS.md): ports in parallel never edit the same lines.
#[path = "managed_real/cases/binds.rs"]
mod case_binds;
#[path = "managed_real/cases/desktops.rs"]
mod case_desktops;
#[path = "managed_real/cases/immersive.rs"]
mod case_immersive;
#[path = "managed_real/cases/keybinds.rs"]
mod case_keybinds;
#[path = "managed_real/cases/overview_pointer.rs"]
mod case_overview_pointer;
#[path = "managed_real/cases/privacy.rs"]
mod case_privacy;
#[path = "managed_real/cases/send.rs"]
mod case_send;
#[path = "managed_real/cases/setup.rs"]
mod case_setup;
#[path = "managed_real/cases/socket.rs"]
mod case_socket;
#[path = "managed_real/cases/video.rs"]
mod case_video;
#[path = "managed_real/cases/view.rs"]
mod case_view;
#[path = "managed_real/cases/window_memory.rs"]
mod case_window_memory;
