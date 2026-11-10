//! runner.rs against the checkout's Python: `panel_consent._validate` on the same consents, `startup.summary` on the
//! same marks, both through `json.dumps(sort_keys=True)`.

use std::process::Command;

use serde_json::{Value, json};

use super::*;

/// `script` in the checkout's Python with `ARGS`; its stdout as JSON.
fn python(script: &str, args: &Value) -> Value {
    let repo = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");
    let prelude = "import json, sys\nsys.path.insert(0, sys.argv[1])\nARGS = json.loads(sys.argv[2])\n";
    let out = Command::new("/usr/bin/python3")
        .arg("-I")
        .arg("-c")
        .arg(format!("{prelude}{script}"))
        .arg(repo)
        .arg(args.to_string())
        .env("TMPDIR", "/tmp")
        .output()
        .unwrap();
    assert!(out.status.success(), "python failed:\n{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap()
}

const SHA: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn consent() -> Value {
    json!({
        "schema": 1, "gate": "P2-panel", "run_id": "r1", "instance": "inst", "output": "DP-1",
        "recovery_output": "eDP-1", "model": "beast", "allow": CAPTURE_ALLOW, "capture": CAPTURE_SCOPE,
        "tracking": false, "mode_switch": false,
        "plugin": {"path": "/p/plugin.so", "sha256": SHA}, "renderer": {"path": "/p/renderer", "sha256": SHA},
        "desktops": [{"width": 1920, "height": 1080, "scale": 1.0}], "input": false, "sdk": false,
    })
}

fn luma() -> Value {
    let mut c = consent();
    c["model"] = json!("luma-ultra");
    c["tracking"] = json!("luma");
    c["luma_driver"] = json!({"path": "/p/luma", "sha256": SHA});
    c
}

/// Each case: a base consent, then keys set and keys removed.
fn cases() -> Vec<Value> {
    let mode = json!({"width": 1920, "height": 1200, "refresh_hz": 120});
    let sbs = json!({"width": 3840, "height": 1200, "refresh_hz": [90, 60]});
    let set = |base: Value, changes: Value| {
        let mut c = base;
        for (k, v) in changes.as_object().unwrap() {
            c[k] = v.clone();
        }
        c
    };
    let del = |base: Value, key: &str| {
        let mut c = base;
        c.as_object_mut().unwrap().remove(key);
        c
    };
    vec![
        consent(),
        luma(),
        set(luma(), json!({"mode_switch": mode})),
        set(luma(), json!({"mode_switch": mode, "sbs_mode": sbs})),
        set(consent(), json!({"tracking": "xrlinuxdriver", "tracking_driver": "3dof"})),
        json!([]),
        del(consent(), "sdk"),
        set(consent(), json!({"extra": 1})),
        del(luma(), "luma_driver"),
        set(consent(), json!({"schema": 2})),
        set(consent(), json!({"schema": true})),
        set(consent(), json!({"schema": 1.0})),
        set(consent(), json!({"gate": "P1"})),
        set(consent(), json!({"run_id": "bad id"})),
        set(consent(), json!({"run_id": 7})),
        set(consent(), json!({"instance": "other"})),
        set(consent(), json!({"output": "DP-2"})),
        set(consent(), json!({"run_id": "r".repeat(81)})),
        set(consent(), json!({"recovery_output": "DP-1"})),
        set(consent(), json!({"model": "nope"})),
        set(consent(), json!({"model": "pro2"})),
        set(consent(), json!({"capture": "all"})),
        set(consent(), json!({"allow": ["headless-outputs"]})),
        set(consent(), json!({"tracking": "elsewhere"})),
        set(consent(), json!({"tracking": true})),
        set(consent(), json!({"tracking_driver": "3dof"})),
        set(consent(), json!({"tracking": "xrlinuxdriver", "tracking_driver": "6dof"})),
        set(consent(), json!({"mode_switch": mode})),
        set(luma(), json!({"mode_switch": {"width": 1920, "height": 1200, "refresh_hz": 60}})),
        set(luma(), json!({"mode_switch": {"width": 1920, "height": 1200, "refresh_hz": 120.0}})),
        set(luma(), json!({"mode_switch": 0})),
        set(luma(), json!({"sbs_mode": sbs})),
        set(luma(), json!({"mode_switch": mode, "sbs_mode": {"width": 3840, "height": 1200, "refresh_hz": [60]}})),
        set(consent(), json!({"plugin": {"path": "p", "sha256": SHA}})),
        set(consent(), json!({"plugin": {"path": "/p", "sha256": "ABC"}})),
        set(consent(), json!({"renderer": {"path": "/p"}})),
        set(luma(), json!({"luma_driver": "luma"})),
        set(consent(), json!({"desktops": []})),
        set(consent(), json!({"desktops": "one"})),
        set(consent(), json!({"input": true})),
        set(consent(), json!({"sdk": 0})),
    ]
}

#[test]
fn consent_validation_matches_panel_consent() {
    let cases = cases();
    let py = python(
        "from tools.native_spike.panel_consent import _validate\n\
         out = []\n\
         for c in ARGS:\n\
         \x20   try: _validate(c, 'inst', 'DP-1'); out.append('ok')\n\
         \x20   except ValueError as e: out.append(str(e))\n\
         print(json.dumps(out))",
        &json!(cases),
    );
    for (case, want) in cases.iter().zip(py.as_array().unwrap()) {
        let got = match validate_consent(case, "inst", "DP-1") {
            Ok(_) => "ok".to_string(),
            Err(e) => e,
        };
        assert_eq!(got, want.as_str().unwrap(), "consent {case}");
    }
}

#[test]
fn a_valid_consent_carries_its_scope() {
    let mut c = luma();
    c["mode_switch"] = json!({"width": 1920, "height": 1200, "refresh_hz": 120});
    let got = validate_consent(&c, "inst", "DP-1").unwrap();
    assert_eq!((got.run_id.as_str(), got.model.as_str(), got.tracking.as_deref()), ("r1", "luma-ultra", Some("luma")));
    assert!(got.mode_switch);
    assert_eq!(got.luma_driver.unwrap().path, Path::new("/p/luma"));
    assert_eq!(got.desktops.len(), 1);
    assert_eq!(validate_consent(&consent(), "bad id", "DP-1").err().unwrap(), "invalid identifier");
}

#[test]
fn a_pro2_route_is_refused_after_the_capture_check() {
    let mut c = consent();
    c["model"] = json!("pro2");
    c["pro2_route"] = json!({});
    assert_eq!(validate_consent(&c, "inst", "DP-1").err().unwrap(), PRO2_NOT_PORTED);
    c["capture"] = json!("all");
    assert_eq!(validate_consent(&c, "inst", "DP-1").err().unwrap(), "exact managed-output capture scope required");
}

#[test]
fn summary_matches_startup_summary_byte_for_byte() {
    let sets = [
        vec![("confirmed", 1_000_000_000i64), ("runner", 1_000_123_456), ("first_status", 1_250_000_001)],
        vec![("runner", 5), ("confirmed", 9), ("first_frame", 2_000_000_000), ("first_status", 1_999_999_999)],
        vec![("runner", 10), ("plugin", 1_000_000_500), ("tracking", 1_000_000_500)],
        vec![("first_frame", 1_000_000)],
    ];
    let mut expected = Vec::new();
    for marks in &sets {
        let mut s = Startup::default();
        for (name, at) in marks {
            s.mark(name, Some(*at));
        }
        s.mark(marks[0].0, Some(1)); // a name's first mark wins
        expected.push(dumps_sorted(&s.summary()));
    }
    let args: Vec<Value> = sets.iter().map(|m| Value::Object(m.iter().map(|(n, a)| (n.to_string(), json!(a))).collect())).collect();
    let py = python(
        "from tools.native_spike.startup import summary\n\
         print(json.dumps([json.dumps(summary(m), sort_keys=True) for m in ARGS]))",
        &json!(args),
    );
    let py: Vec<String> = py.as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
    assert_eq!(expected, py);
}

#[test]
fn dumps_sorted_sorts_nested_keys_as_python_does() {
    let v = json!({"b": {"z": 1, "a": [{"y": 1.5, "x": null}]}, "a": "é"});
    let py = python("print(json.dumps(json.dumps(ARGS, sort_keys=True)))", &v);
    assert_eq!(dumps_sorted(&v), py.as_str().unwrap());
}
