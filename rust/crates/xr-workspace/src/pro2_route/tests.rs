//! pro2_route.rs against `tools/xr_controller/pro2_route.py` and `tools/pro2_runtime/launch.py`, on the same files:
//! a set of installed, broken and tampered bundles, and a run of settings changes made by each side.

use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{Value, json};

use super::*;
use crate::testdir::{TestDir, write_private};

/// `script` in the checkout's Python with `args`; its stdout as JSON.
fn python(script: &str, args: &Value) -> Value {
    let repo = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");
    let prelude =
        "import json, sys\nsys.path.insert(0, sys.argv[1])\nARGS = json.loads(sys.argv[2])\n";
    let out = Command::new("/usr/bin/python3")
        .arg("-I")
        .arg("-c")
        .arg(format!("{prelude}{script}"))
        .arg(repo)
        .arg(args.to_string())
        .env("TMPDIR", "/tmp")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "python failed:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}

const BRIDGE: &str = "bin/pro2-sdk-bridge";

/// A bundle under `data_home`'s pro2 folder with the three required files; returns its path.
fn bundle(data_home: &Path, name: &str, files: &[(&str, &[u8])], sdk: &str) -> PathBuf {
    let root = install::root(data_home).join(PRO2_DIR).join(name);
    fs::create_dir_all(root.join("bin")).unwrap();
    let mut pins = serde_json::Map::new();
    for (file, data) in files {
        fs::write(root.join(file), data).unwrap();
        pins.insert((*file).to_owned(), json!(install::sha_hex(data)));
    }
    let manifest = json!({"schema": 1, "sdk_sha256": sdk, "files": pins});
    fs::write(root.join("manifest.json"), manifest.to_string()).unwrap();
    root
}

fn standard(data_home: &Path, name: &str) -> PathBuf {
    bundle(
        data_home,
        name,
        &[
            (BRIDGE, b"bridge"),
            (RENDERER_FILE, b"renderer"),
            (PLUGIN_FILE, b"plugin"),
        ],
        PRO2_SDK_SHA,
    )
}

fn pin(bundle: &str, sha: &str) -> Value {
    json!({"schema": 1, "model": "pro2", "bundle": bundle, "manifest_sha256": sha})
}

fn write_pin(data_home: &Path, value: &Value) {
    write_private(&install::root(data_home).join(PRO2_PIN), value);
}

fn sha_of(root: &Path) -> String {
    manifest_sha(root).unwrap()
}

fn path_str(p: &Path) -> String {
    p.to_str().unwrap().to_owned()
}

/// Each scenario: a data folder and the bundle path `verify_manifest` is asked about.
fn scenarios(top: &Path) -> Vec<(String, PathBuf, String)> {
    let mut out = vec![];
    let mut add = |name: &str, make: &dyn Fn(&Path) -> String| {
        let data = top.join(name);
        fs::create_dir_all(install::root(&data)).unwrap();
        let verify = make(&data);
        out.push((name.to_owned(), data, verify));
    };
    let good = |d: &Path| {
        let root = standard(d, "abc123");
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    };
    add("good", &good);
    add("missing", &|d: &Path| path_str(&standard(d, "abc123")));
    add("schema-true", &|d: &Path| {
        let root = standard(d, "abc123");
        let mut p = pin(&path_str(&root), &sha_of(&root));
        p["schema"] = json!(true);
        write_pin(d, &p);
        path_str(&root)
    });
    for (name, key, value) in [
        ("schema-two", "schema", json!(2)),
        ("model-luma", "model", json!("luma")),
        ("sha-upper", "manifest_sha256", json!("A".repeat(64))),
        ("sha-short", "manifest_sha256", json!("a")),
        ("extra-key", "extra", json!(1)),
    ] {
        add(name, &move |d: &Path| {
            let root = standard(d, "abc123");
            let mut p = pin(&path_str(&root), &sha_of(&root));
            p[key] = value.clone();
            write_pin(d, &p);
            path_str(&root)
        });
    }
    add("not-a-dict", &|d: &Path| {
        write_pin(d, &json!([1]));
        String::from("/nonexistent")
    });
    add("open-mode", &|d: &Path| {
        let root = good(d);
        fs::set_permissions(
            install::root(d).join(PRO2_PIN),
            fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        root
    });
    add("outside", &|d: &Path| {
        let other = install::root(d).join("other");
        fs::create_dir_all(&other).unwrap();
        let root = standard(d, "abc123");
        let moved = other.join("abc123");
        fs::rename(&root, &moved).unwrap();
        write_pin(d, &pin(&path_str(&moved), &sha_of(&moved)));
        path_str(&moved)
    });
    add("dot-components", &|d: &Path| {
        let root = standard(d, "abc123");
        let dotted = format!("{}/./{}", root.parent().unwrap().display(), "abc123");
        write_pin(d, &pin(&dotted, &sha_of(&root)));
        dotted
    });
    add("parent-component", &|d: &Path| {
        let root = standard(d, "abc123");
        let up = format!("{}/../{PRO2_DIR}/abc123", root.parent().unwrap().display());
        write_pin(d, &pin(&up, &sha_of(&root)));
        up
    });
    add("space-in-name", &|d: &Path| {
        let root = standard(d, "abc 123");
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    });
    add("bundle-symlink", &|d: &Path| {
        let real = standard(d, "real");
        let link = real.with_file_name("abc123");
        symlink(&real, &link).unwrap();
        write_pin(d, &pin(&path_str(&link), &sha_of(&real)));
        path_str(&link)
    });
    add("manifest-changed", &|d: &Path| {
        let root = good(d);
        let manifest = fs::read_to_string(Path::new(&root).join("manifest.json")).unwrap();
        fs::write(
            Path::new(&root).join("manifest.json"),
            format!("{manifest} "),
        )
        .unwrap();
        root
    });
    add("no-plugin-pin", &|d: &Path| {
        let root = bundle(
            d,
            "abc123",
            &[(BRIDGE, b"b"), (RENDERER_FILE, b"r")],
            PRO2_SDK_SHA,
        );
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    });
    add("no-bridge", &|d: &Path| {
        let root = bundle(
            d,
            "abc123",
            &[(RENDERER_FILE, b"r"), (PLUGIN_FILE, b"p")],
            PRO2_SDK_SHA,
        );
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    });
    add("wrong-sdk", &|d: &Path| {
        let files: [(&str, &[u8]); 3] =
            [(BRIDGE, b"b"), (RENDERER_FILE, b"r"), (PLUGIN_FILE, b"p")];
        let root = bundle(d, "abc123", &files, &"0".repeat(64));
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    });
    add("artifact-tampered", &|d: &Path| {
        let root = good(d);
        fs::write(Path::new(&root).join(RENDERER_FILE), b"other").unwrap();
        root
    });
    add("plugin-tampered", &|d: &Path| {
        let root = good(d);
        fs::write(Path::new(&root).join(PLUGIN_FILE), b"other").unwrap();
        root
    });
    add("artifact-symlink", &|d: &Path| {
        let root = good(d);
        let target = Path::new(&root).join("bin/renderer-real");
        fs::rename(Path::new(&root).join(RENDERER_FILE), &target).unwrap();
        symlink(&target, Path::new(&root).join(RENDERER_FILE)).unwrap();
        root
    });
    add("absolute-entry", &|d: &Path| {
        let root = standard(d, "abc123");
        let mut manifest: Value =
            serde_json::from_slice(&fs::read(root.join("manifest.json")).unwrap()).unwrap();
        manifest["files"]["/etc/hostname"] = json!("0".repeat(64));
        fs::write(root.join("manifest.json"), manifest.to_string()).unwrap();
        write_pin(d, &pin(&path_str(&root), &sha_of(&root)));
        path_str(&root)
    });
    out
}

const ROUTE_SCRIPT: &str = r#"
from pathlib import Path
from tools.xr_controller import pro2_route
from tools.pro2_runtime.launch import verify_manifest
out = {}
for name, data, bundle in ARGS:
    route, why = pro2_route.probe(Path(data))
    try:
        root, manifest = verify_manifest(bundle)
        verify = ['ok', str(root)]
    except Exception as exc:
        verify = ['error', str(exc)]
    out[name] = {'route': route, 'why': why, 'verify': verify}
print(json.dumps(out))
"#;

/// Error texts that come from the OS or the JSON parser word differently (module docs): only that it failed is held.
fn comparable(text: &str) -> String {
    if text.contains("[Errno") || text.contains("No such file") {
        return "<os error>".into();
    }
    text.to_owned()
}

#[test]
fn every_bundle_reads_as_the_python_reads_it() {
    let dir = TestDir::new("pro2-route");
    let all = scenarios(dir.path());
    let answers = python(
        ROUTE_SCRIPT,
        &json!(
            all.iter()
                .map(|(n, d, b)| json!([n, path_str(d), b]))
                .collect::<Vec<_>>()
        ),
    );
    let mut wrong = vec![];
    for (name, data, bundle) in &all {
        let (route, why) = probe(data);
        let verify = match verify_manifest(bundle) {
            Ok((root, _)) => json!(["ok", path_str(&root)]),
            Err(e) => json!(["error", comparable(&e)]),
        };
        let rust = json!({"route": route.map(|r| r.to_json()), "why": why, "verify": verify});
        let mut py = answers[name].clone();
        // The Python echoes the pin's schema as written (`true` passes there as 1); the Rust route says 1.
        if py["route"].is_object() {
            py["route"]["schema"] = json!(1);
        }
        if let Some(e) = py["verify"][1]
            .as_str()
            .filter(|_| py["verify"][0] == "error")
        {
            py["verify"][1] = json!(comparable(e));
        }
        if rust != py {
            wrong.push(format!("{name}\n  python: {py}\n  rust:   {rust}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} differences:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
    let good = &answers["good"];
    assert!(
        good["route"].is_object() && good["verify"][0] == "ok",
        "{good}"
    );
    // (scenario, the pin gives a route, the bundle verifies): the cases that hold the two checks apart.
    for (name, route, verifies) in [
        ("missing", false, true),
        ("outside", false, true),
        ("artifact-tampered", true, false),
        ("plugin-tampered", true, false),
        ("no-bridge", true, false),
    ] {
        assert_eq!(answers[name]["route"].is_object(), route, "{name}");
        assert_eq!(answers[name]["verify"][0] == "ok", verifies, "{name}");
    }
}

#[test]
fn require_holds_the_start_to_the_pinned_bundle() {
    let dir = TestDir::new("pro2-require");
    let data = dir.path();
    fs::create_dir_all(install::root(data)).unwrap();
    assert_eq!(
        require(&json!({PRO2_KEY: {}}), data).unwrap_err(),
        "the Pro 2 bridge bundle is not installed"
    );
    let root = standard(data, "abc123");
    write_pin(data, &pin(&path_str(&root), &sha_of(&root)));
    let route = load_route(data).unwrap().unwrap();
    assert_eq!(route.plugin.path, path_str(&root.join(PLUGIN_FILE)));
    assert_eq!(route.renderer.sha256, install::sha_hex(b"renderer"));
    let settings = Settings {
        projection: 46.5,
        mount_pitch_deg: -11.0,
        pose_rate_hz: 120,
    };
    let block = consent_block(&route, &settings);
    assert_eq!(
        block,
        json!({"bundle": path_str(&root), "manifest_sha256": sha_of(&root), "projection": 46.5,
               "pose_rate_hz": 120, "mount_pitch_deg": -11.0})
    );
    let consent = json!({PRO2_KEY: block, "plugin": route.plugin.to_json(), "renderer": route.renderer.to_json()});
    let (verified, manifest) = require(&consent, data).unwrap();
    assert_eq!(verified, root);
    assert_eq!(manifest["sdk_sha256"], PRO2_SDK_SHA);

    let mut other = consent.clone();
    other[PRO2_KEY]["manifest_sha256"] = json!("0".repeat(64));
    assert_eq!(
        require(&other, data).unwrap_err(),
        "the Pro 2 route changed since this start was confirmed"
    );
    let mut other = consent.clone();
    other["plugin"]["sha256"] = json!("0".repeat(64));
    assert_eq!(
        require(&other, data).unwrap_err(),
        "Pro 2 consent is not the pinned bundle"
    );
    assert_eq!(require(&json!({}), data).unwrap_err(), "'pro2_route'");
    fs::write(root.join(BRIDGE), b"swapped").unwrap();
    assert_eq!(
        require(&consent, data).unwrap_err(),
        "artifact hash mismatch"
    );

    // The Python agrees on the same consent.
    let script = r#"
from pathlib import Path
from tools.xr_controller import pro2_route
consent, data = ARGS
try: pro2_route.require(consent, Path(data)); print(json.dumps('ok'))
except ValueError as exc: print(json.dumps(str(exc)))
"#;
    assert_eq!(
        python(script, &json!([consent, path_str(data)])),
        json!("artifact hash mismatch")
    );
    fs::write(root.join(BRIDGE), b"bridge").unwrap();
    assert_eq!(
        python(script, &json!([consent, path_str(data)])),
        json!("ok")
    );
}

const SETTINGS_SCRIPT: &str = r#"
from pathlib import Path
from tools.xr_controller import pro2_route
folder, steps = ARGS
out = []
for changes in steps:
    try: result = pro2_route.save_settings(changes, Path(folder))
    except ValueError as exc: result = str(exc)
    out.append({'result': result, 'file': json.loads((Path(folder) / pro2_route.SETTINGS_FILE).read_text()),
                'loaded': pro2_route.load_settings(Path(folder))})
print(json.dumps(out))
"#;

fn settings_json(s: &Settings) -> Value {
    json!({"projection": s.projection, "mount_pitch_deg": s.mount_pitch_deg, "pose_rate_hz": s.pose_rate_hz})
}

#[test]
fn settings_save_and_load_as_the_python_does() {
    let dir = TestDir::new("pro2-settings");
    let steps = json!([
        {"projection": 50}, {"mount_pitch_deg": -11}, {"pose_rate_hz": 240}, {"pose_rate_hz": 60.0},
        {"projection": 46}, {"projection": true}, {"mount_pitch_deg": 31}, {"mount_pitch_deg": true}, {"x": 1}, {},
        {"projection": 44.5, "pose_rate_hz": 7}, {"pose_rate_hz": 90, "projection": 47.5}, {"mount_pitch_deg": 30}
    ]);
    let start = json!({"schema": 1, "projection": "wide", "other": 5, "pose_rate_hz": 90});
    let py_dir = dir.path().join("python");
    let rust_dir = dir.path().join("rust");
    for d in [&py_dir, &rust_dir] {
        fs::create_dir_all(d).unwrap();
        write_private(&d.join(SETTINGS_FILE), &start);
    }
    assert_eq!(
        settings_json(&load_settings(&rust_dir)),
        json!({"projection": 44.5, "mount_pitch_deg": -8.5, "pose_rate_hz": 90}),
        "each value on its own"
    );
    let saved_by_python = python(SETTINGS_SCRIPT, &json!([path_str(&py_dir), steps]));
    let mut rust = vec![];
    for changes in steps.as_array().unwrap() {
        let changes: Vec<(&str, Value)> = changes
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.as_str(), v.clone()))
            .collect();
        let result = match save_settings(&changes, &rust_dir) {
            Ok(s) => settings_json(&s),
            Err(e) => json!(e),
        };
        let file: Value =
            serde_json::from_slice(&fs::read(rust_dir.join(SETTINGS_FILE)).unwrap()).unwrap();
        rust.push(json!({"result": result, "file": file, "loaded": settings_json(&load_settings(&rust_dir))}));
    }
    assert_eq!(Value::Array(rust), saved_by_python);
    let mode = fs::metadata(rust_dir.join(SETTINGS_FILE))
        .unwrap()
        .permissions()
        .mode()
        & 0o7777;
    assert_eq!(mode, 0o600);
    // Each side reads what the other wrote.
    assert_eq!(
        settings_json(&load_settings(&py_dir)),
        settings_json(&load_settings(&rust_dir))
    );
    let read_back = python(
        "from pathlib import Path\nfrom tools.xr_controller import pro2_route\nprint(json.dumps(pro2_route.load_settings(Path(ARGS))))\n",
        &json!(path_str(&rust_dir)),
    );
    assert_eq!(read_back, settings_json(&load_settings(&rust_dir)));
    // An unreadable file is the defaults, and a save starts it over.
    let fresh = dir.path().join("fresh");
    assert_eq!(
        load_settings(&fresh),
        Settings {
            projection: 44.5,
            mount_pitch_deg: -8.5,
            pose_rate_hz: 60
        }
    );
    save_settings(&[("pose_rate_hz", json!(120))], &fresh).unwrap();
    assert_eq!(
        fs::read_to_string(fresh.join(SETTINGS_FILE)).unwrap(),
        r#"{"schema": 1, "pose_rate_hz": 120}"#
    );
}
