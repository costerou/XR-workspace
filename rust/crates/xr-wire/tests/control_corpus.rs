//! Holds `xr_wire::control` to the renderer's own verdicts: `tests/native_spike/fixtures/control_frames.json`
//! (every frame the Python senders write, mutated frames and envelope cases, each decided by the C++
//! `decode_control` through `control_wire_check`) and the `*_payloads.json` fixtures the C++ ipc tests
//! read. Every encoded frame must also come back byte for byte from `control::encode`.

use serde_json::Value as Json;
use xr_wire::control::{self, Op, Payload, Stream};
use xr_wire::json::{self, Limits, Value};

const FIXTURES: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../tests/native_spike/fixtures/"
);

fn load(name: &str) -> Json {
    serde_json::from_str(&std::fs::read_to_string(format!("{FIXTURES}{name}")).unwrap()).unwrap()
}

/// What a fresh stream makes of the last of `frames`, the earlier ones taken first, as
/// `control_corpus.verdict` asks of `control_wire_check`.
fn verdict(frames: &[&str]) -> Json {
    let mut stream = Stream::new("run", "session").unwrap();
    let got = stream.feed(frames.concat().as_bytes());
    assert_eq!(
        got.len(),
        frames.len(),
        "one verdict per frame for {frames:?}"
    );
    let (last, earlier) = got.split_last().unwrap();
    assert!(
        earlier.iter().all(Result::is_ok),
        "an earlier frame was refused: {earlier:?}"
    );
    match last {
        Ok(_) => Json::from("accepted"),
        Err(r) => serde_json::json!({"code": r.refusal.code, "detail": r.refusal.detail}),
    }
}

#[test]
fn every_corpus_verdict_is_the_renderers() {
    let corpus = load("control_frames.json");
    assert_eq!(
        (corpus["run_id"].as_str(), corpus["session"].as_str()),
        (Some("run"), Some("session"))
    );
    let ops = corpus["ops"].as_object().unwrap();
    let mut names: Vec<&str> = ops.keys().map(String::as_str).collect();
    let mut ours: Vec<&str> = Op::ALL.iter().map(|op| op.name()).collect();
    names.sort();
    ours.sort();
    assert_eq!(names, ours, "the corpus covers exactly the 23 ops");

    let (mut encoded, mut mutated, mut wrong) = (0, 0, Vec::new());
    for (op, cases) in ops {
        for frame in cases["encoded"].as_array().unwrap() {
            let frame = frame.as_str().unwrap();
            encoded += 1;
            if verdict(&[frame]) != "accepted" {
                wrong.push(format!("{op}: an encoded frame is refused: {frame}"));
                continue;
            }
            let message = control::decode(frame.as_bytes()).unwrap();
            let again = control::encode_message(&message);
            if again != frame {
                wrong.push(format!("{op}: re-encoded\n  {frame}  as\n  {again}"));
            }
        }
        for case in cases["mutated"].as_array().unwrap() {
            mutated += 1;
            let got = verdict(&[case["frame"].as_str().unwrap()]);
            if got != case["verdict"] {
                wrong.push(format!(
                    "{op} / {}: expected {}, got {got}",
                    case["what"], case["verdict"]
                ));
            }
        }
    }
    let envelope = corpus["envelope"].as_array().unwrap();
    for case in envelope {
        let frames: Vec<&str> = case["frames"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f.as_str().unwrap())
            .collect();
        let got = verdict(&frames);
        if got != case["verdict"] {
            wrong.push(format!(
                "envelope / {}: expected {}, got {got}",
                case["what"], case["verdict"]
            ));
        }
    }
    println!(
        "{encoded} encoded frames, {mutated} mutated frames, {} envelope cases",
        envelope.len()
    );
    assert!(encoded >= 92 && mutated >= 270 && envelope.len() >= 25);
    assert!(
        wrong.is_empty(),
        "{} cases differ:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

/// The payload fixture's entry as our `Value`, wrapped in a frame its op's sender would write.
fn frame_for(op: Op, payload: &Json) -> String {
    let payload = json::parse(
        payload.to_string().as_bytes(),
        Limits {
            max_bytes: 1 << 20,
            max_depth: 64,
        },
    )
    .unwrap();
    control::encode(op, 2, "run", "session", &payload)
}

/// (valid entries, invalid entries) of a fixture: each valid one as its payload and the whole entry
/// (whose other fields are what the payload decodes to), each invalid one as its payload.
fn entries(name: &str) -> (Vec<(Json, Json)>, Vec<Json>) {
    let fixture = load(name);
    // A bare payload, or {"payload": ..., what it decodes to} / {"why": ..., "payload": ...}.
    let unwrap = |e: &Json| e.get("payload").cloned().unwrap_or_else(|| e.clone());
    let valid = fixture["valid"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| (unwrap(e), e.clone()))
        .collect();
    let invalid = fixture["invalid"]
        .as_array()
        .unwrap()
        .iter()
        .map(unwrap)
        .collect();
    (valid, invalid)
}

fn check_fixture(name: &str, op: Op, check: impl Fn(&Payload, &Json)) {
    let (valid, invalid) = entries(name);
    assert!(!valid.is_empty() && !invalid.is_empty());
    let mut wrong = Vec::new();
    for (payload, entry) in &valid {
        match control::decode(frame_for(op, payload).as_bytes()) {
            Ok(message) => check(&message.payload, entry),
            Err(r) => wrong.push(format!("valid {payload} refused: {r:?}")),
        }
    }
    for payload in &invalid {
        if let Ok(m) = control::decode(frame_for(op, payload).as_bytes()) {
            wrong.push(format!("invalid {payload} accepted as {:?}", m.payload));
        }
    }
    println!("{name}: {} valid, {} invalid", valid.len(), invalid.len());
    assert!(
        wrong.is_empty(),
        "{name}: {} entries differ:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

#[test]
fn the_environment_fixture() {
    check_fixture("environment_payloads.json", Op::Environment, |p, e| {
        let Payload::Environment(env) = p else {
            panic!("{p:?}")
        };
        assert_eq!(Json::from(env.background), e["background"]);
        assert_eq!(Json::from(env.brightness), e["brightness"]);
    });
}

#[test]
fn the_immersive_fixture() {
    check_fixture("immersive_payloads.json", Op::Immersive, |p, e| {
        let Payload::Immersive(i) = p else {
            panic!("{p:?}")
        };
        for key in ["hover", "tail", "stereo"] {
            if let Some(want) = e.get(key) {
                let got = match key {
                    "hover" => Json::from(i.hover),
                    "tail" => Json::from(i.tail),
                    _ => Json::from(i.stereo),
                };
                assert_eq!(&got, want, "{key} of {e}");
            }
        }
    });
}

#[test]
fn the_overview_pointer_fixture() {
    check_fixture(
        "overview_pointer_payloads.json",
        Op::OverviewPointer,
        |p, _| {
            assert!(matches!(p, Payload::OverviewPointer(_)));
        },
    );
}

#[test]
fn the_placement_menu_fixture() {
    check_fixture("placement_menu_payloads.json", Op::PlacementMenu, |p, _| {
        assert!(matches!(p, Payload::PlacementMenu(_)));
    });
}

#[test]
fn the_video_fixture() {
    check_fixture("video_payloads.json", Op::Video, |p, _| {
        assert!(matches!(p, Payload::Video(_)));
    });
}

#[test]
fn values_keep_their_frame_order() {
    // encode writes the payload as given; a Value round trip must not reorder it.
    let v: Value = json::parse(
        br#"{"b":1,"a":2}"#,
        Limits {
            max_bytes: 64,
            max_depth: 4,
        },
    )
    .unwrap();
    assert_eq!(
        control::encode(Op::Style, 1, "r", "s", &v),
        "{\"schema\":1,\"request_id\":1,\"run_id\":\"r\",\"session\":\"s\",\"op\":\"style\",\"payload\":{\"b\":1,\"a\":2}}\n"
    );
}
