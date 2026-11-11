//! The renderer setting at Start, in `xr-workspace supervisor` (the run unit's command), end to end on the loopback
//! backend (runner/fake.rs): which pinned renderer a run hands to `run_managed`, and that a Rust choice with no
//! trustworthy Rust renderer fails before anything is touched, in the Python runner's words (renderer_switch.py).
//!
//! Each test starts the real binary on a temporary data, config and runtime tree: real pinned files, a real
//! installed.json and `renderer-rust.pin.json`, a real setting and consent. Only the compositor, the glasses and the
//! renderer are the fake's; no renderer is started.

use std::fs;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::{Value, json};
use xr_workspace::install::{self, sha_hex};
use xr_workspace::runner::PRO2_NOT_PORTED;

const BACK: &str = "switch back with `python -m tools.xr_controller renderer set cpp`";

struct Dir(PathBuf);

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn private(path: &Path, data: &[u8]) {
    let _ = fs::remove_file(path);
    let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).unwrap();
    std::io::Write::write_all(&mut f, data).unwrap();
}

struct Tree {
    dir: Dir,
    cpp: Value,
    rust: Value,
}

/// An install with both renderers pinned (the C++ one in installed.json, the Rust one in its own pin file) and no
/// setting yet.
fn tree(name: &str) -> Tree {
    let base = std::env::temp_dir().canonicalize().unwrap();
    let root = base.join(format!("xr-renderer-switch-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
    let dir = Dir(root.clone());
    let bin = root.join("data/viture-workspace/bin/0123456789ab");
    fs::create_dir_all(bin.join(install::RUST_DIR)).unwrap();
    let pin = |file: &Path, body: &str| {
        fs::write(file, body).unwrap();
        json!({"path": file, "sha256": sha_hex(body.as_bytes())})
    };
    let plugin = pin(&bin.join(install::PLUGIN), "plugin build");
    let cpp = pin(&bin.join(install::RENDERER), "renderer build");
    let rust = pin(&bin.join(install::RUST_DIR).join(install::RUST_RENDERER), "rust renderer build");
    let data = root.join("data/viture-workspace");
    fs::write(data.join("installed.json"), json!({"renderer": cpp, "plugin": plugin}).to_string()).unwrap();
    fs::write(data.join(install::RUST_RENDERER_PIN), rust.to_string()).unwrap();
    for sub in ["run", "config", "config/viture-workspace", "consents"] {
        fs::DirBuilder::new().mode(0o700).create(root.join(sub)).unwrap();
    }
    Tree { dir, cpp, rust }
}

impl Tree {
    fn root(&self) -> &Path {
        &self.dir.0
    }

    fn setting(&self) -> PathBuf {
        self.root().join("config/viture-workspace/renderer.json")
    }

    fn set(&self, choice: &str) {
        private(&self.setting(), json!({"schema": 1, "renderer": choice}).to_string().as_bytes());
    }

    fn rust_pin(&self) -> PathBuf {
        self.root().join("data/viture-workspace").join(install::RUST_RENDERER_PIN)
    }

    fn log(&self) -> PathBuf {
        self.root().join("steps.log")
    }

    /// A consented Beast run naming `renderer`, then the supervisor on the fake.
    fn start(&self, renderer: &Value, changes: Value) -> Output {
        let installed: Value =
            serde_json::from_slice(&fs::read(self.root().join("data/viture-workspace/installed.json")).unwrap()).unwrap();
        let mut consent = json!({
            "schema": 1, "gate": "P2-panel", "run_id": "r1", "instance": "inst", "output": "DP-1",
            "recovery_output": "eDP-1", "model": "beast", "allow": ["headless-outputs", "capture-managed-outputs"],
            "capture": "managed-outputs", "tracking": false, "mode_switch": false, "plugin": installed["plugin"],
            "renderer": renderer, "desktops": [{"width": 1920, "height": 1080, "scale": 1.0}],
            "input": false, "sdk": false,
        });
        for (k, v) in changes.as_object().unwrap() {
            consent[k] = v.clone();
        }
        let consent_path = self.root().join("consents/r1.json");
        private(&consent_path, consent.to_string().as_bytes());
        let script = self.root().join("script.json");
        fs::write(&script, json!({"log": self.log()}).to_string()).unwrap();
        let _ = fs::remove_file(self.log());
        let root = self.root();
        Command::new(env!("CARGO_BIN_EXE_xr-workspace"))
            .env_clear()
            .env("HOME", root)
            .env("XDG_DATA_HOME", root.join("data"))
            .env("XDG_CONFIG_HOME", root.join("config"))
            .env("XDG_STATE_HOME", root.join("state-home"))
            .env("XDG_RUNTIME_DIR", root.join("run"))
            .env("XR_WORKSPACE_SUPERVISOR_FAKE", &script)
            .args(["supervisor", "--consent"])
            .arg(&consent_path)
            .arg("--state-dir")
            .arg(root.join("state"))
            .output()
            .unwrap()
    }

    fn result(&self) -> Value {
        serde_json::from_slice(&fs::read(self.root().join("state/r1.result.json")).unwrap()).unwrap()
    }

    fn steps(&self) -> Vec<String> {
        fs::read_to_string(self.log()).unwrap_or_default().lines().map(str::to_string).collect()
    }

    /// The renderer `run_managed` was handed, from the fake's log.
    fn launched(&self) -> Vec<String> {
        self.steps().iter().filter_map(|s| s.strip_prefix("renderer.launch ").map(str::to_string)).collect()
    }

    /// A refused Start: exit 2, `error` in the result, and nothing connected, loaded or launched.
    fn refused(&self, out: &Output) -> String {
        assert_eq!(out.status.code(), Some(2), "{}", String::from_utf8_lossy(&out.stderr));
        assert!(self.steps().is_empty(), "nothing ran: {:?}", self.steps());
        let result = self.result();
        assert_eq!(result["phase"], "verify", "{result}");
        result["error"].as_str().unwrap().to_string()
    }

    /// What the Python runner says for the same tree and consent renderer: `renderer_switch.start_renderer`'s pin, or
    /// its ValueError's text.
    fn python(&self, renderer: &Value) -> String {
        let repo = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");
        let script = "import json, os, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n\
            from tools.xr_controller import install, renderer_switch\n\
            data = Path(os.environ['XDG_DATA_HOME'])\n\
            try: print(json.dumps(renderer_switch.start_renderer(json.loads(sys.argv[2]), install.load_installed(data), data)))\n\
            except ValueError as e: print(e)\n";
        let out = Command::new("/usr/bin/python3")
            .arg("-I")
            .arg("-c")
            .arg(script)
            .arg(repo)
            .arg(renderer.to_string())
            .env_clear()
            .env("HOME", self.root())
            .env("XDG_DATA_HOME", self.root().join("data"))
            .env("XDG_CONFIG_HOME", self.root().join("config"))
            .env("TMPDIR", "/tmp")
            .output()
            .unwrap();
        assert!(out.status.success(), "python failed:\n{}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim_end().to_string()
    }
}

fn ok(out: &Output) {
    assert_eq!(out.status.code(), Some(0), "{}", String::from_utf8_lossy(&out.stderr));
}

fn path(pin: &Value) -> String {
    pin["path"].as_str().unwrap().to_string()
}

#[test]
fn no_setting_or_cpp_launches_the_cpp_renderer_and_never_reads_the_rust_pin() {
    let t = tree("cpp");
    ok(&t.start(&t.cpp, json!({})));
    assert_eq!(t.launched(), [path(&t.cpp)]);
    // A broken Rust pin changes nothing while the setting is cpp: it is not read.
    fs::write(t.rust_pin(), "not json").unwrap();
    t.set("cpp");
    ok(&t.start(&t.cpp, json!({})));
    assert_eq!(t.launched(), [path(&t.cpp)]);
    // A consent naming the Rust renderer is refused while the setting is cpp, as the Python runner refuses it.
    let error = t.refused(&t.start(&t.rust, json!({})));
    assert_eq!(error, "consent renderer is not the pinned install");
    assert_eq!(t.python(&t.rust), error);
}

#[test]
fn rust_launches_the_pinned_rust_renderer() {
    let t = tree("rust");
    t.set("rust");
    let out = t.start(&t.rust, json!({}));
    ok(&out);
    assert_eq!(t.launched(), [path(&t.rust)], "{:?}", t.steps());
    assert_eq!(t.result()["phase"], "done");
    assert_eq!(serde_json::from_str::<Value>(&t.python(&t.rust)).unwrap(), t.rust);
}

#[test]
fn rust_with_no_trustworthy_rust_renderer_fails_naming_the_way_back() {
    let t = tree("refused");
    t.set("rust");
    let rust_path = path(&t.rust);
    let pin = t.rust_pin();
    let cases: Vec<(&str, Box<dyn Fn()>, String)> = vec![
        (
            "a consent naming the C++ renderer (confirmed before the switch)",
            Box::new(|| {}),
            format!("consent renderer is not the pinned Rust renderer; {BACK}"),
        ),
        (
            "no Rust renderer installed",
            Box::new(|| fs::remove_file(&pin).unwrap()),
            format!(
                "the Rust renderer is selected but not installed (no renderer-rust.pin.json: \
                 run `install --build <dir> --rust-renderer <xr-renderer>`); {BACK}"
            ),
        ),
        (
            "the pinned binary changed",
            Box::new(|| {
                fs::write(&pin, t.rust.to_string()).unwrap();
                fs::write(&rust_path, "another renderer").unwrap();
            }),
            format!("the Rust renderer cannot start: {rust_path} changed since it was installed; {BACK}"),
        ),
        (
            "a pin outside the install",
            Box::new(|| fs::write(&pin, json!({"path": "/usr/bin/true", "sha256": "0".repeat(64)}).to_string()).unwrap()),
            format!("the Rust renderer cannot start: renderer-rust.pin.json: pinned path outside install directory; {BACK}"),
        ),
        (
            "a malformed pin",
            Box::new(|| fs::write(&pin, json!({"path": rust_path}).to_string()).unwrap()),
            format!("the Rust renderer cannot start: renderer-rust.pin.json: malformed installed.json; {BACK}"),
        ),
    ];
    for (i, (what, change, want)) in cases.iter().enumerate() {
        change();
        let consent = if i == 0 { &t.cpp } else { &t.rust };
        let error = t.refused(&t.start(consent, json!({})));
        assert_eq!(&error, want, "{what}");
        assert!(error.contains("renderer set cpp"), "{what}: {error}");
        assert_eq!(t.python(consent), error, "{what}: the Python runner's words");
    }
    // Not JSON: the reader's own words differ between the two (serde, json), the rest does not.
    fs::write(&pin, "not json").unwrap();
    let error = t.refused(&t.start(&t.rust, json!({})));
    assert!(error.starts_with("the Rust renderer cannot start: renderer-rust.pin.json unreadable: "), "{error}");
    assert!(error.ends_with(BACK), "{error}");
}

#[test]
fn a_setting_that_cannot_be_trusted_refuses_as_the_python_runner_does() {
    let t = tree("setting");
    let setting = t.setting();
    let invalid = format!("renderer.json is invalid; {BACK} or clear it");
    let unreadable = format!("renderer.json unreadable: consent must be owned regular single-link mode 0600; {BACK}");
    let cases: Vec<(&str, u32, String)> = vec![
        (r#"{"schema": 1, "renderer": "rust", "extra": 1}"#, 0o600, invalid.clone()),
        (r#"{"schema": true, "renderer": "rust"}"#, 0o600, invalid.clone()),
        (r#"{"schema": 1.0, "renderer": "rust"}"#, 0o600, invalid.clone()),
        (r#"{"schema": 2, "renderer": "rust"}"#, 0o600, invalid.clone()),
        (r#"{"schema": 1, "renderer": "Rust"}"#, 0o600, invalid.clone()),
        (r#"{"schema": 1, "renderer": null}"#, 0o600, invalid.clone()),
        (r#"{"schema": 1}"#, 0o600, invalid.clone()),
        (r#"{"schema": 1, "renderer": "rust", "renderer": "cpp"}"#, 0o600, invalid.clone()),
        ("[]", 0o600, invalid.clone()),
        ("rust", 0o600, invalid.clone()),
        (r#"{"schema": 1, "renderer": "cpp"}"#, 0o644, unreadable.clone()),
    ];
    for (text, mode, want) in &cases {
        private(&setting, text.as_bytes());
        fs::set_permissions(&setting, fs::Permissions::from_mode(*mode)).unwrap();
        let error = t.refused(&t.start(&t.cpp, json!({})));
        assert_eq!(&error, want, "{text} {mode:o}");
        assert_eq!(t.python(&t.cpp), error, "{text} {mode:o}: the Python runner's words");
    }
    // A link to a good setting is not followed.
    let elsewhere = t.root().join("elsewhere.json");
    private(&elsewhere, br#"{"schema": 1, "renderer": "cpp"}"#);
    fs::remove_file(&setting).unwrap();
    std::os::unix::fs::symlink(&elsewhere, &setting).unwrap();
    let error = t.refused(&t.start(&t.cpp, json!({})));
    assert_eq!(
        error,
        format!("renderer.json unreadable: [Errno 40] Too many levels of symbolic links: '{}'; {BACK}", setting.display())
    );
    assert_eq!(t.python(&t.cpp), error, "a link: the Python runner's words");
    fs::remove_file(&setting).unwrap();
    // Keys in either order, and no file at all, are accepted.
    private(&setting, br#"{"renderer": "cpp", "schema": 1}"#);
    ok(&t.start(&t.cpp, json!({})));
    fs::remove_file(&setting).unwrap();
    ok(&t.start(&t.cpp, json!({})));
    assert_eq!(t.launched(), [path(&t.cpp)]);
}

#[test]
fn a_pro2_start_never_reads_the_setting() {
    let t = tree("pro2");
    private(&t.setting(), b"not a setting");
    let out = t.start(&t.cpp, json!({"model": "pro2", "pro2_route": {}}));
    assert_eq!(out.status.code(), Some(2), "{}", String::from_utf8_lossy(&out.stderr));
    assert!(t.steps().is_empty(), "nothing ran: {:?}", t.steps());
    // Refused as not ported, in the Pro 2 route's phase (runner.execute_pro2), not as a broken renderer setting.
    let result = t.result();
    assert_eq!(result["phase"], "run", "{result}");
    assert_eq!(result["error"], PRO2_NOT_PORTED);
}
