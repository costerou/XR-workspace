//! Replays the `controls` section of `rust/corpus/view/view.json` (`tests/native_spike/view_corpus.py`,
//! `control_cases`): the Python `view.ViewControl`, built as `managed.view_controls` builds it from a glasses profile,
//! driven op by op on a fixed clock with no other feature attached. `view_controls::view::ViewControl` must send the
//! same frames at each step, answer the same status, leave the same `profiles.json` byte for byte, and focus and tell
//! the same.

use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use serde_json::Value;
use xr_wire::run_control::Frame;
use xr_workspace::geometry::Desktop;
use xr_workspace::pyjson::Json;
use xr_workspace::view_controls::view::{Store, ViewControl};
use xr_workspace::view_controls::{Features, Frames};

const CORPUS: &str = include_str!("../../../corpus/view/view.json");

fn features<'a>() -> Features<'a> {
    Features {
        pointer: None,
        send: None,
        privacy: None,
        desktops: None,
        immersive: None,
        windows: None,
        keys: None,
        binds: None,
    }
}

fn wire(v: &Value) -> xr_wire::json::Value {
    xr_wire::json::parse_strict(v.to_string().as_bytes()).unwrap()
}

fn text(frames: &Frames) -> String {
    String::from_utf8(frames.bytes.clone()).unwrap()
}

#[test]
fn view_control_scripts_match_the_python_step_by_step() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let scripts = corpus["controls"].as_array().expect("controls");
    assert!(scripts.len() >= 5, "{} scripts", scripts.len());
    let mut steps = 0;
    for script in scripts {
        let name = script["name"].as_str().unwrap();
        let dir = std::env::temp_dir().join(format!(
            "xr-view-control-{}-{}",
            std::process::id(),
            name.replace(' ', "-")
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("profiles.json");
        if let Some(t) = script["profile"].as_str() {
            fs::write(&path, t).unwrap();
        }
        let names: Vec<String> = serde_json::from_value(script["names"].clone()).unwrap();
        let live: Option<Vec<Desktop>> = script["live"]
            .as_array()
            .map(|l| l.iter().map(|d| Desktop::from_value(d).unwrap()).collect());
        let now = Arc::new(AtomicU64::new(0f64.to_bits()));
        let clock = {
            let now = Arc::clone(&now);
            Box::new(move || f64::from_bits(now.load(Ordering::SeqCst)))
        };
        let store = Store::Profile {
            path: path.clone(),
            model: "luma-ultra".into(),
        };
        let env = &corpus["envelope"];
        let mut view = ViewControl::new(
            &names,
            env["run_id"].as_str().unwrap(),
            env["session"].as_str().unwrap(),
            Some(store),
            live,
            clock,
        )
        .unwrap_or_else(|| panic!("{name}: a view"));
        let (focused, toasts) = (
            Arc::new(Mutex::new(Vec::<String>::new())),
            Arc::new(Mutex::new(Vec::<String>::new())),
        );
        let seen = Arc::clone(&focused);
        view.set_focus(Some(Arc::new(move |n: &str| {
            seen.lock().unwrap().push(n.to_owned())
        })));
        let told = Arc::clone(&toasts);
        view.notify = Some(Arc::new(move |t: &str| {
            told.lock().unwrap().push(t.to_owned())
        }));
        if !script["border"].is_null() {
            let reply = Json::parse(&script["border"].to_string()).unwrap();
            view.set_border(Some(Box::new(move || Some(reply.clone()))));
        }
        let mut pending = Vec::new();
        for (i, step) in script["steps"].as_array().unwrap().iter().enumerate() {
            let set = |t: &Value| now.store(t.as_f64().unwrap().to_bits(), Ordering::SeqCst);
            if let Some(t) = step.get("at") {
                set(t);
            }
            let mut out = Frames::default();
            let got = if let Some(op) = step.get("op") {
                let frame = Frame {
                    request_id: 1,
                    run_id: "run-1".into(),
                    session: "a1b2c3".into(),
                    op: op.as_str().unwrap().into(),
                    payload: wire(&step["payload"]),
                };
                view.on_message(&frame, &mut features(), &mut out);
                text(&out)
            } else if let Some(t) = step.get("poll") {
                set(t);
                view.poll(Instant::now(), &mut features(), &mut out);
                text(&out)
            } else if step.get("initial").is_some() {
                String::from_utf8(view.initial_frame()).unwrap()
            } else if step.get("status").is_some() {
                let body = xr_wire::json::Value::Object(view.status_reply(false));
                xr_wire::json::encode(&body, xr_wire::json::Style::COMPACT)
            } else if let Some(events) = step.get("events") {
                pending.extend_from_slice(events.as_str().unwrap().as_bytes());
                let end = pending
                    .iter()
                    .rposition(|&b| b == b'\n')
                    .map_or(0, |i| i + 1);
                let done: Vec<u8> = pending.drain(..end).collect();
                let lines: Vec<Vec<u8>> = done
                    .split(|&b| b == b'\n')
                    .filter(|l| !l.is_empty())
                    .map(<[u8]>::to_vec)
                    .collect();
                view.on_events(&lines, &mut features(), &mut out);
                text(&out)
            } else if step.get("flush").is_some() {
                view.flush();
                String::new()
            } else if step.get("save_setup").is_some() {
                view.save_setup();
                String::new()
            } else if step.get("at").is_some() {
                String::new() // the clock moved, nothing else
            } else {
                panic!("{name}: unknown step {step}");
            };
            assert_eq!(
                got,
                step["out"].as_str().unwrap(),
                "{name} step {i}: {step}"
            );
            steps += 1;
        }
        drop(view); // the focus and toast threads joined
        let saved = fs::read_to_string(&path).ok();
        assert_eq!(
            saved.as_deref(),
            script["saved"].as_str(),
            "{name}: profiles.json"
        );
        let want = |k: &str| -> Vec<String> { serde_json::from_value(script[k].clone()).unwrap() };
        assert_eq!(*focused.lock().unwrap(), want("focused"), "{name}: focus");
        assert_eq!(*toasts.lock().unwrap(), want("toasts"), "{name}: toasts");
        let _ = fs::remove_dir_all(&dir);
    }
    assert!(steps > 80, "{steps} steps");
}
