//! The cases of `spike/tests/ownership_pair_wire_test.py`, encoded by `xr_wire::plugin` and fed to
//! the plugin's own `parse_request` (`ownership_test --validate`, built here from
//! `spike/ownership/registry.cpp`). Each case must get the same verdict from C++ and from
//! `plugin::parse_request`, and every case the plugin takes must come from `Request::encode`.

use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use xr_wire::json::Value;
use xr_wire::plugin::{self, Op, OutputSpec, Request, Schema};

static BIN: OnceLock<PathBuf> = OnceLock::new();

fn validator() -> &'static PathBuf {
    BIN.get_or_init(|| {
        let spike = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../spike");
        let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("ownership_test");
        let status = Command::new(std::env::var("CXX").unwrap_or_else(|_| "c++".into()))
            .args(["-std=c++23", "-O0", "-Wall", "-Wextra", "-Werror", "-I"])
            .arg(spike.join("ownership"))
            .arg(spike.join("tests/ownership_test.cpp"))
            .arg(spike.join("ownership/registry.cpp"))
            .arg("-o")
            .arg(&out)
            .status()
            .expect("run the C++ compiler");
        assert!(status.success(), "ownership_test did not build");
        out
    })
}

/// `ownership_test --validate`: true when the plugin takes the request (exit 0), false on exit 2.
fn plugin_takes(wire: &str) -> bool {
    let mut child = Command::new(validator())
        .arg("--validate")
        .stdin(Stdio::piped())
        .spawn()
        .expect("run ownership_test");
    child.stdin.take().unwrap().write_all(wire.as_bytes()).unwrap();
    match child.wait().unwrap().code() {
        Some(0) => true,
        Some(2) => false,
        other => panic!("ownership_test exited {other:?}"),
    }
}

/// `dict(base, key=value)`: replace `key`, or add it last.
fn with(base: &Value, key: &str, value: Value) -> Value {
    let Value::Object(mut entries) = base.clone() else { panic!("not an object") };
    match entries.iter_mut().find(|(k, _)| k == key) {
        Some(entry) => entry.1 = value,
        None => entries.push((key.to_owned(), value)),
    }
    Value::Object(entries)
}

fn s(v: &str) -> Value {
    Value::String(v.to_owned())
}

fn n(v: i64) -> Value {
    Value::Number(v.into())
}

fn payload_with(base: &Value, key: &str, value: Value) -> Value {
    with(base, "payload", with(base.get("payload").unwrap(), key, value))
}

const UUID: &str = "12345678-1234-1234-1234-123456789abc";

fn spec() -> OutputSpec {
    OutputSpec {
        id: UUID.into(),
        name: "XR-1".into(),
        index: 0,
        width: 1920,
        height: 1080,
        refresh_millihz: 120000,
        scale: 1.0,
        x: 1280,
        y: 0,
    }
}

/// A case: the request, whether the plugin must take it, and whether it was built by the typed
/// encoder (`Request`) or by editing an encoded envelope.
enum Case {
    Typed(Request),
    Edited(Value, bool),
}

fn cases() -> Vec<Case> {
    let hello = Request {
        schema: Schema::Isolated,
        session: "guest".into(),
        run_id: "run".into(),
        request_id: 1,
        epoch: None,
        op: Op::PairHello { target_name: "Virtual-1".into(), fallback_name: "Virtual-2".into() },
    };
    let observe = Request {
        epoch: Some("a".repeat(48)),
        op: Op::PairObserve { target: "b".repeat(48), fallback: "c".repeat(48) },
        ..hello.clone()
    };
    let legacy = Request { schema: Schema::Legacy, op: Op::Hello { fallback_name: "DP-1".into() }, ..hello.clone() };
    let iso = Request { op: Op::Create(spec()), ..observe.clone() };
    let iso_inspect = Request { op: Op::Inspect { id: UUID.into() }, ..observe.clone() };
    let iso_remove = Request {
        op: Op::EvacuateRemove { id: UUID.into(), name: "XR-1".into(), fallback: "c".repeat(48) },
        ..observe.clone()
    };
    let cursor = Request { schema: Schema::Legacy, epoch: Some("a".repeat(48)), op: Op::CursorShape, ..hello.clone() };
    let (h, o, l) = (hello.envelope(), observe.envelope(), legacy.envelope());
    let (i, ii, ir, c) = (iso.envelope(), iso_inspect.envelope(), iso_remove.envelope(), cursor.envelope());
    // `dict(iso, payload=spec)`: the create payload without its mode.
    let bare_spec = Request { schema: Schema::Legacy, ..iso.clone() }.envelope().get("payload").unwrap().clone();
    let spec_with = |key: &str, value: Value| with(&i, "payload", with(i.get("payload").unwrap(), key, value));
    let edited = |v: Value| Case::Edited(v, false);
    vec![
        Case::Typed(hello),
        Case::Typed(observe),
        Case::Typed(legacy),
        edited(with(&h, "schema", n(1))),
        edited(with(&l, "schema", n(2))),
        edited(payload_with(&o, "target", s(&"b".repeat(49)))),
        edited(with(&h, "request_id", Value::Number(u64::MAX.into()))),
        edited(payload_with(&h, "mode", s("normal"))),
        Case::Typed(iso.clone()),
        Case::Typed(iso_inspect),
        Case::Typed(iso_remove),
        edited(with(&i, "schema", n(1))),
        edited(with(&i, "payload", bare_spec)),
        edited(with(&ii, "payload", Value::Object(vec![("id".into(), s(UUID)), ("mode".into(), s("normal"))]))),
        edited(payload_with(&ir, "fallback", s(&"C".repeat(48)))),
        edited(with(
            &with(&o, "op", s("hello")),
            "payload",
            Value::Object(vec![("mode".into(), s(plugin::MODE)), ("fallback_name".into(), s("Virtual-2"))]),
        )),
        edited(spec_with("name", s("XR-SPIKE-run-0"))),
        edited(spec_with("workspace", n(11))),
        edited(spec_with("index", n(1))),
        Case::Typed(cursor),
        edited(with(&c, "schema", n(2))),
        edited(with(&with(&c, "schema", n(2)), "payload", Value::Object(vec![("mode".into(), s(plugin::MODE))]))),
        edited(with(&c, "payload", Value::Object(vec![("id".into(), s(UUID))]))),
        edited(with(&c, "payload", Value::Object(vec![("shape".into(), s("text"))]))),
        edited(with(&c, "payload", Value::Array(vec![]))),
        edited(with(&c, "epoch", s("not a token"))),
    ]
}

#[test]
fn the_plugin_takes_exactly_the_requests_rust_encodes() {
    let cases = cases();
    assert_eq!(cases.len(), 26);
    for (index, case) in cases.into_iter().enumerate() {
        let (wire, expected) = match case {
            Case::Typed(request) => (request.encode().unwrap_or_else(|e| panic!("case {index}: {e}")), true),
            Case::Edited(envelope, expected) => (plugin::encode_envelope(&envelope), expected),
        };
        assert_eq!(plugin_takes(&wire), expected, "case {index}, C++: {wire}");
        assert_eq!(plugin::parse_request(wire.as_bytes()).is_ok(), expected, "case {index}, Rust: {wire}");
    }
}

/// The geometry rules are the plugin's: every size step and scale near the edges gets the same
/// verdict from both sides.
#[test]
fn create_geometry_matches_the_plugin() {
    let base = Request {
        schema: Schema::Legacy,
        session: "guest".into(),
        run_id: "run".into(),
        request_id: 7,
        epoch: Some("e".into()),
        op: Op::Create(spec()),
    };
    let mut wires = Vec::new();
    for (width, height) in [(640, 480), (638, 480), (7680, 4320), (7682, 4320), (1921, 1080), (1366, 768), (2560, 1440)] {
        for scale in [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 0.75, 1.1] {
            for (index, name) in [(0, "XR-1"), (3, "XR-4"), (4, "XR-5"), (1, "XR-1")] {
                let op = Op::Create(OutputSpec { index, name: name.into(), width, height, scale, ..spec() });
                wires.push(plugin::encode_envelope(&Request { op, ..base.clone() }.envelope()));
            }
        }
    }
    let taken = wires.iter().filter(|w| plugin::parse_request(w.as_bytes()).is_ok()).count();
    assert!(taken > 0 && taken < wires.len());
    for wire in &wires {
        assert_eq!(plugin_takes(wire), plugin::parse_request(wire.as_bytes()).is_ok(), "{wire}");
    }
}

/// The bounds and the envelope's own JSON rules: size, prefix, nesting and repeated keys.
#[test]
fn bounds_match_the_plugin() {
    let legacy = Request {
        schema: Schema::Legacy,
        session: "guest".into(),
        run_id: "run".into(),
        request_id: 1,
        epoch: None,
        op: Op::Hello { fallback_name: "DP-1".into() },
    }
    .encode()
    .unwrap();
    let pad = |total: usize| {
        let body = &legacy[plugin::COMMAND.len()..];
        format!("{}{}{body}", plugin::COMMAND, " ".repeat(total - legacy.len()))
    };
    let deep = legacy.replace(r#""DP-1"}"#, r#""DP-1","x":[[[1]]]}"#);
    let shallow = legacy.replace(r#""DP-1"}"#, r#""DP-1","x":[[1]]}"#);
    let wires = [
        pad(plugin::MAX_REQUEST_BYTES),
        pad(plugin::MAX_REQUEST_BYTES + 1),
        legacy.replacen("xrspike ", "xrspike", 1),
        legacy.replacen("xrspike ", "XRSPIKE ", 1),
        legacy.replacen(r#""schema":1,"#, r#""schema":1,"schema":1,"#, 1),
        legacy.replacen(r#""request_id":1,"#, r#""request_id":1.0,"#, 1),
        legacy.replacen(r#""request_id":1,"#, r#""request_id":2147483647,"#, 1),
        legacy.replacen(r#""request_id":1,"#, r#""request_id":2147483648,"#, 1),
        legacy.replacen(r#""request_id":1,"#, r#""request_id":-1,"#, 1),
        legacy.replacen(r#""schema":1,"#, r#""schema":1.0,"#, 1),
        legacy.replacen(r#""schema":1,"#, r#""schema":true,"#, 1),
        deep,
        shallow,
    ];
    let verdicts: Vec<bool> = wires.iter().map(|w| plugin::parse_request(w.as_bytes()).is_ok()).collect();
    assert_eq!(verdicts, [true, false, false, false, false, false, true, false, false, false, false, false, false]);
    for (wire, rust) in wires.iter().zip(verdicts) {
        assert_eq!(plugin_takes(wire), rust, "{wire}");
    }
}
