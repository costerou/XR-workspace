//! `xr-workspace supervisor`: the run unit's command, end to end on the loopback backend (runner/fake.rs).
//!
//! Each test starts the real binary with the Python runner's arguments and environment, a real consent, real pinned
//! files and a real installed.json in a temporary data home, and reads back what a controller reads: the exit status,
//! `<run_id>.result.json` and the progress file. Only the compositor, the glasses and the renderer are the fake's.

use std::fs;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::{Value, json};
use xr_workspace::install::sha_hex;

struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Dir {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let path = base.join(format!("xr-supervisor-cli-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
        Dir(path)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn private(path: &Path, data: &[u8]) {
    let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).unwrap();
    std::io::Write::write_all(&mut f, data).unwrap();
}

struct Session {
    dir: Dir,
    consent: PathBuf,
    state: PathBuf,
    script: PathBuf,
    log: PathBuf,
}

/// A consented Beast run with one desktop, its pins installed, and the fake's `script` on top of the log path.
fn session(name: &str, consent_changes: Value, script: Value) -> Session {
    let dir = Dir::new(name);
    let root = &dir.0;
    let bin = root.join("data/viture-workspace/bin");
    fs::create_dir_all(&bin).unwrap();
    let mut pins = serde_json::Map::new();
    for (key, file) in [("plugin", "libxr-ownership.so"), ("renderer", "xr-native-spike")] {
        let body = format!("{key} build");
        fs::write(bin.join(file), &body).unwrap();
        pins.insert(key.into(), json!({"path": bin.join(file), "sha256": sha_hex(body.as_bytes())}));
    }
    fs::write(root.join("data/viture-workspace/installed.json"), Value::Object(pins.clone()).to_string()).unwrap();
    for sub in ["run", "config", "consents"] {
        fs::DirBuilder::new().mode(0o700).create(root.join(sub)).unwrap();
    }
    let mut consent = json!({
        "schema": 1, "gate": "P2-panel", "run_id": "r1", "instance": "inst", "output": "DP-1",
        "recovery_output": "eDP-1", "model": "beast", "allow": ["headless-outputs", "capture-managed-outputs"],
        "capture": "managed-outputs", "tracking": false, "mode_switch": false, "plugin": pins["plugin"],
        "renderer": pins["renderer"], "desktops": [{"width": 1920, "height": 1080, "scale": 1.0}],
        "input": false, "sdk": false,
    });
    for (k, v) in consent_changes.as_object().unwrap() {
        consent[k] = v.clone();
    }
    let consent_path = root.join("consents/r1.json");
    private(&consent_path, consent.to_string().as_bytes());
    let log = root.join("steps.log");
    let mut script = script;
    script["log"] = json!(log);
    let script_path = root.join("script.json");
    fs::write(&script_path, script.to_string()).unwrap();
    Session { state: root.join("state"), consent: consent_path, script: script_path, log, dir }
}

impl Session {
    fn command(&self) -> Command {
        let root = &self.dir.0;
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_xr-workspace"));
        cmd.env_clear()
            .env("HOME", root)
            .env("XDG_DATA_HOME", root.join("data"))
            .env("XDG_CONFIG_HOME", root.join("config"))
            .env("XDG_STATE_HOME", root.join("state-home"))
            .env("XDG_RUNTIME_DIR", root.join("run"))
            .env("XR_WORKSPACE_SUPERVISOR_FAKE", &self.script);
        cmd
    }

    fn run(&self) -> Output {
        self.command()
            .args(["supervisor", "--consent"])
            .arg(&self.consent)
            .arg("--state-dir")
            .arg(&self.state)
            .output()
            .unwrap()
    }

    fn result(&self, name: &str) -> (Value, String) {
        let path = self.state.join(name);
        let meta = fs::symlink_metadata(&path).unwrap();
        assert_eq!(meta.permissions().mode() & 0o7777, 0o600, "{name} must be 0600");
        let text = fs::read_to_string(&path).unwrap();
        (serde_json::from_str(&text).unwrap(), text)
    }

    fn steps(&self) -> Vec<String> {
        fs::read_to_string(&self.log).unwrap_or_default().lines().map(str::to_string).collect()
    }
}

fn status(out: &Output) -> i32 {
    out.status.code().unwrap()
}

fn index(steps: &[String], step: &str) -> usize {
    steps.iter().position(|s| s == step).unwrap_or_else(|| panic!("no {step} in {steps:?}"))
}

#[test]
fn a_complete_loopback_session_exits_0_with_the_runners_result() {
    let s = session("complete", json!({}), json!({"plugins": ["hyprbars"]}));
    let out = s.run();
    assert_eq!(status(&out), 0, "{}", String::from_utf8_lossy(&out.stderr));
    let (result, text) = s.result("r1.result.json");
    // json.dumps(result, sort_keys=True): sorted keys, Python separators.
    assert!(
        text.starts_with(
            r#"{"complete": true, "error": null, "phase": "done", "remaining": [], "renderer_returncode": 0, "run_id": "r1", "startup": {"clock": "CLOCK_MONOTONIC", "marks_ns": {"#
        ),
        "{text}"
    );
    let keys: Vec<&String> = result.as_object().unwrap().keys().collect();
    assert_eq!(keys, ["complete", "error", "phase", "remaining", "renderer_returncode", "run_id", "startup"]);
    let marks = result["startup"]["marks_ns"].as_object().unwrap();
    for mark in ["confirmed", "runner", "verify", "plugin", "desktops"] {
        assert!(marks.contains_key(mark), "{mark} in {marks:?}");
    }
    assert!(!marks.contains_key("tracking"));
    assert!(result["startup"]["phases_ms"]["runner"].is_f64());
    assert!(!s.state.join("r1.progress.json").exists(), "progress removed once the result is written");
    let fs_mode = fs::metadata(&s.state).unwrap().permissions().mode() & 0o7777;
    assert_eq!(fs_mode, 0o700);

    // The order: connect, the plugin loaded and read back, the layout, the whole managed order, then the unload.
    let steps = s.steps();
    let order = [
        "connect inst",
        "plugin_load",
        "park DP-1",
        "session.verify",
        "broker.open",
        "journal.bind_broker",
        "outputs.create",
        "adopt",
        "renderer.keep",
        "journal.recover",
        "plugin_unload",
    ];
    let at: Vec<usize> = order.iter().map(|step| index(&steps, step)).collect();
    assert!(at.windows(2).all(|w| w[0] < w[1]), "{steps:?}");
    assert!(!steps.iter().any(|s| s.starts_with("tracking")), "tracking false starts no driver");
    assert!(!steps.iter().any(|s| s.starts_with("session_mode")));
}

#[test]
fn a_luma_session_starts_tracking_and_the_mode_and_ends_the_watch() {
    let s = session("luma", json!({}), json!({}));
    // A Luma Ultra consent: tracking luma with its pinned driver, and the session mode.
    let bin = s.dir.0.join("data/viture-workspace/bin");
    fs::write(bin.join("xr-luma-driver"), "luma").unwrap();
    let luma = json!({"path": bin.join("xr-luma-driver"), "sha256": sha_hex(b"luma")});
    let installed_path = s.dir.0.join("data/viture-workspace/installed.json");
    let mut installed: Value = serde_json::from_str(&fs::read_to_string(&installed_path).unwrap()).unwrap();
    installed["luma_driver"] = luma.clone();
    fs::write(&installed_path, installed.to_string()).unwrap();
    let mut consent: Value = serde_json::from_str(&fs::read_to_string(&s.consent).unwrap()).unwrap();
    consent["model"] = json!("luma-ultra");
    consent["tracking"] = json!("luma");
    consent["luma_driver"] = luma;
    consent["mode_switch"] = json!({"width": 1920, "height": 1200, "refresh_hz": 120});
    fs::remove_file(&s.consent).unwrap();
    private(&s.consent, consent.to_string().as_bytes());

    let out = s.run();
    assert_eq!(status(&out), 0, "{}", String::from_utf8_lossy(&out.stderr));
    let steps = s.steps();
    let luma_step = format!("tracking.luma {}", bin.join("xr-luma-driver").display());
    let at: Vec<usize> = [luma_step.as_str(), "plugin_load", "session_mode DP-1", "park DP-1", "tracking.watch_finish"]
        .iter()
        .map(|step| index(&steps, step))
        .collect();
    assert!(at.windows(2).all(|w| w[0] < w[1]), "{steps:?}");
    let (result, _) = s.result("r1.result.json");
    assert!(result["startup"]["marks_ns"].get("tracking").is_some());
}

#[test]
fn an_unfinished_recovery_keeps_the_plugin_for_explicit_recovery() {
    let script = json!({"journal": true, "recovery": {"complete": false, "remaining": ["output XR-1"], "reasons": ["XR-1 still has windows"]}});
    let s = session("keep", json!({}), script);
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["phase"], "run");
    assert_eq!(result["complete"], false);
    assert_eq!(result["remaining"], json!(["output XR-1"]));
    assert_eq!(result["recovery"], json!(["XR-1 still has windows"]));
    assert_eq!(result["error"], "ownership plugin retained for explicit recovery: XR-1 still has windows");
    assert!(!s.steps().contains(&"plugin_unload".to_string()));
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("blocked: ownership plugin retained for explicit recovery"), "{stderr}");
}

#[test]
fn a_failure_before_the_journal_unloads_and_records_the_fault() {
    let s = session("plan", json!({}), json!({"journal": true, "fail": "outputs.plan", "error": "no room"}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["error"], "no room");
    assert_eq!(result["phase"], "run");
    assert!(s.steps().contains(&"plugin_unload".to_string()));
    assert_eq!(result["faults"][0]["where"], "the run");
    assert_eq!(result["faults"][0]["count"], 1);
}

#[test]
fn a_failure_after_the_journal_keeps_the_plugin() {
    let s = session("create", json!({}), json!({"journal": true, "fail": "outputs.create", "error": "broker refused"}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["error"], "broker refused; ownership plugin retained for explicit recovery");
    let steps = s.steps();
    assert!(index(&steps, "outputs.create") < index(&steps, "journal.recover"), "recovery runs: {steps:?}");
    assert!(!steps.contains(&"plugin_unload".to_string()));
}

#[test]
fn a_failure_before_the_run_unloads_the_plugin() {
    let s = session("park", json!({}), json!({"fail": "park DP-1", "error": "presentation not parked"}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["phase"], "layout");
    assert_eq!(result["error"], "presentation not parked");
    assert_eq!(result["renderer_returncode"], Value::Null);
    let steps = s.steps();
    assert!(index(&steps, "park DP-1") < index(&steps, "plugin_unload"));
    assert!(!steps.contains(&"session.verify".to_string()), "no managed step after a failed park");
}

#[test]
fn a_loaded_broker_blocks_the_start_with_nothing_loaded() {
    let s = session("loaded", json!({}), json!({"plugins": ["xr-ownership"]}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["error"], "ownership plugin already loaded");
    assert_eq!(result["phase"], "plugin");
    assert!(!s.steps().iter().any(|s| s == "plugin_load" || s == "plugin_unload"));
}

#[test]
fn a_changed_pin_is_refused_before_anything_moves() {
    let s = session("pin", json!({}), json!({}));
    fs::write(s.dir.0.join("data/viture-workspace/bin/xr-native-spike"), "changed").unwrap();
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["phase"], "verify");
    assert_eq!(result["error"], "pinned binary changed");
    assert!(s.steps().is_empty(), "{:?}", s.steps());
}

#[test]
fn an_invalid_run_id_writes_invalid_result() {
    let s = session("invalid", json!({"run_id": "../r1"}), json!({}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("invalid.result.json");
    assert_eq!(result["run_id"], Value::Null);
    assert_eq!(result["error"], "invalid identifier");
    assert!(result["startup"]["marks_ns"].get("runner").is_some());
}

#[test]
fn no_runtime_dir_blocks_at_the_plugin_phase() {
    let s = session("noruntime", json!({}), json!({}));
    let out = s
        .command()
        .env_remove("XDG_RUNTIME_DIR")
        .args(["supervisor", "--consent"])
        .arg(&s.consent)
        .arg("--state-dir")
        .arg(&s.state)
        .output()
        .unwrap();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["phase"], "plugin");
    assert_eq!(result["error"], "XDG_RUNTIME_DIR not set");
    assert!(s.steps().is_empty());
}

#[test]
fn a_pro2_consent_is_left_to_the_python_runner() {
    let s = session("pro2", json!({"model": "pro2"}), json!({}));
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("r1.result.json");
    assert_eq!(result["phase"], "run");
    assert_eq!(result["error"], xr_workspace::runner::PRO2_NOT_PORTED);
}

#[test]
fn usage_errors_exit_2_and_write_nothing() {
    let s = session("usage", json!({}), json!({}));
    for args in [&["supervisor"][..], &["supervisor", "--consent", "c"], &["supervisor", "--bogus", "x"]] {
        let out = s.command().args(args).output().unwrap();
        assert_eq!(status(&out), 2, "{args:?}");
        assert!(String::from_utf8_lossy(&out.stderr).contains("usage:"), "{args:?}");
    }
    assert!(!s.state.exists());
}

#[test]
fn a_missing_consent_reads_as_pythons_os_error() {
    let s = session("missing", json!({}), json!({}));
    fs::remove_file(&s.consent).unwrap();
    let out = s.run();
    assert_eq!(status(&out), 2);
    let (result, _) = s.result("invalid.result.json");
    let want = format!("[Errno 2] No such file or directory: '{}'", s.consent.display());
    assert_eq!(result["error"], want.as_str());
    assert_eq!(result["phase"], "verify");
    assert!(String::from_utf8_lossy(&out.stderr).contains(&format!("blocked: {want}")));
    assert!(!s.log.exists() || s.steps().is_empty(), "nothing ran: {:?}", s.steps());
}
