//! Holds `xr_wire::controller` and `xr_wire::run_control` to `rust/corpus/sockets/frames.json`
//! (`tests/xr_controller/socket_corpus.py`): every frame the shipped panel, controller, key helper
//! and supervisor wrote round-trips byte for byte, and every frame wrong on purpose gets the shipped
//! verdict, except where the corpus marks the payload as another module's or records a decided
//! divergence.

use serde_json::Value as Json;
use xr_wire::controller::{self, Lines, Message, Request, SNAPSHOT_KEYS, is_snapshot};
use xr_wire::run_control::{self, Frame, Queue, StatusReply};

fn corpus() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/sockets/frames.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}

fn bytes(case: &Json) -> Vec<u8> {
    match (case["text"].as_str(), case["hex"].as_str()) {
        (Some(text), None) => text.as_bytes().to_vec(),
        (None, Some(h)) => hex(h),
        _ => panic!("case without text or hex: {case}"),
    }
}

fn texts<'a>(corpus: &'a Json, key: &str) -> Vec<&'a Json> {
    let list: Vec<&Json> = corpus[key].as_array().unwrap().iter().collect();
    assert!(!list.is_empty(), "{key} is empty");
    list
}

#[test]
fn limits_match_the_shipped_ones() {
    let corpus = corpus();
    assert_eq!(corpus["max_frame"], controller::MAX_FRAME);
    assert_eq!(corpus["max_frame"], run_control::MAX_FRAME);
    assert_eq!(corpus["max_clients"], controller::MAX_CLIENTS);
    assert_eq!(corpus["max_queued"], run_control::MAX_QUEUED);
    let keys: Vec<&str> = corpus["snapshot_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|k| k.as_str().unwrap())
        .collect();
    let without: Vec<&str> = SNAPSHOT_KEYS.into_iter().filter(|k| *k != "pro2").collect();
    assert_eq!(keys, without);
}

#[test]
fn every_panel_request_round_trips() {
    let corpus = corpus();
    for case in texts(&corpus, "panel_requests") {
        let text = case["text"].as_str().unwrap();
        let request = Request::decode(text.strip_suffix('\n').unwrap().as_bytes())
            .unwrap_or_else(|e| panic!("{e:?}: {text}"));
        assert_eq!(request.encode(), text);
    }
}

#[test]
fn every_controller_message_round_trips() {
    let corpus = corpus();
    let (mut replies, mut states) = (0, 0);
    for case in texts(&corpus, "controller_messages") {
        let text = case["text"].as_str().unwrap();
        let message = Message::decode(text.strip_suffix('\n').unwrap().as_bytes())
            .unwrap_or_else(|e| panic!("{e:?}: {text}"));
        match (&message, case["kind"].as_str().unwrap()) {
            (Message::Reply { .. }, "reply") => replies += 1,
            (Message::State { snapshot }, "state") => {
                assert!(is_snapshot(snapshot), "{text}");
                states += 1;
            }
            _ => panic!("wrong kind: {text}"),
        }
        assert_eq!(message.encode(), text);
    }
    assert!(replies > 0 && states > 0);
}

#[test]
fn every_run_frame_round_trips_in_its_writers_style() {
    let corpus = corpus();
    for case in texts(&corpus, "run_frames") {
        let text = case["text"].as_str().unwrap();
        let frame = Frame::decode(text.as_bytes()).unwrap_or_else(|e| panic!("{e:?}: {text}"));
        let ascii = match case["writer"].as_str().unwrap() {
            "controller" => true,
            "key_helper" => false,
            other => panic!("unknown writer {other}"),
        };
        assert_eq!(frame.encode(ascii), text);
    }
}

#[test]
fn every_status_reply_round_trips_and_answers_its_request() {
    let corpus = corpus();
    for case in texts(&corpus, "status_replies") {
        let request = Frame::decode(case["request"].as_str().unwrap().as_bytes()).unwrap();
        let text = case["text"].as_str().unwrap();
        let reply =
            StatusReply::decode(text.as_bytes()).unwrap_or_else(|e| panic!("{e:?}: {text}"));
        assert!(reply.answers(&request));
        assert_eq!(reply.encode(), text);
        assert_eq!(StatusReply::to(&request, reply.body.clone()), reply);
    }
}

#[test]
fn run_frames_get_the_shipped_verdict() {
    let corpus = corpus();
    let session = corpus["session"].as_str().unwrap();
    let mut wrong = Vec::new();
    for case in texts(&corpus, "run_cases") {
        let mut queue = Queue::new(case["run_id"].as_str().unwrap(), session).unwrap();
        for earlier in case["earlier"].as_array().unwrap() {
            queue.push(earlier.as_str().unwrap().as_bytes()).unwrap();
        }
        let got = match queue.push(&bytes(case)) {
            Ok(_) => "accepted",
            Err(_) => "refused",
        };
        // The payload is another module's to check: the wire passes it through.
        let want = if case.get("owner").is_some() {
            "accepted"
        } else {
            case["verdict"].as_str().unwrap()
        };
        if got != want {
            wrong.push(format!("{}: got {got}, want {want}", case["about"]));
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
}

#[test]
fn panel_lines_get_the_shipped_verdict() {
    let corpus = corpus();
    let mut wrong = Vec::new();
    for case in texts(&corpus, "panel_cases") {
        let mut lines = Lines::default();
        lines.extend(&bytes(case));
        let mut got = "accepted";
        loop {
            match lines.next_line() {
                Ok(Some(line)) if Request::decode(&line).is_ok() => continue,
                Ok(None) => break,
                _ => {
                    got = "refused";
                    break;
                }
            }
        }
        let want = if case.get("divergence").is_some() {
            "refused"
        } else {
            case["verdict"].as_str().unwrap()
        };
        if got != want {
            wrong.push(format!("{}: got {got}, want {want}", case["about"]));
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
}
