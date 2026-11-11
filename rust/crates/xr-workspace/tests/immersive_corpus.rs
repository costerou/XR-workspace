//! The gate for immersive 3D's run side (`view_controls::immersive`, VIEW_CONTROLS.md `immersive`, level 1), against
//! two corpora recorded from the shipped Python:
//!
//! - rust/corpus/immersive/sbs.json (tests/native_spike/sbs_corpus.py): `sbs.ButtonSbs` on a recording stand-in client
//!   and a stand-in clock. `ButtonSbs` gets the same monitor answers through its `Display` and must make the same
//!   calls in the same order, give the same results, toast the same text and leave the clock where the Python did. The
//!   mode helpers (`offered`, `best_wide`, `is_wide`, `narrow_rate`, `pro2_2d_rate`) per monitor and model too.
//! - rust/corpus/immersive/view.json (tests/native_spike/immersive_view_corpus.py): the view's 3D methods on a
//!   stand-in `ImmersiveRun`. `Immersive` on a stand-in `Worker`, the view's part lent as a `Shown`, must send the same
//!   frames, ask the worker for the same desktops and look, toast and save the same, and answer the same status.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use xr_wire::run_control::Frame;
use xr_workspace::hyprland::{self, Monitor};
use xr_workspace::supervisor::immersive::{self as im, Done, Event, Sbs, Setting};
use xr_workspace::view_controls::Frames;
use xr_workspace::view_controls::immersive::sbs::{self, ButtonSbs, Display};
use xr_workspace::view_controls::immersive::{Immersive, Shown, Worker};

const SBS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/immersive/sbs.json"
));
const VIEW: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/immersive/view.json"
));

fn dp1() -> Value {
    json!({"name": "DP-1", "id": 2, "width": 1920, "height": 1200, "x": -3000, "y": 9000, "scale": 1.25,
           "refreshRate": 120.0, "transform": 0, "mirrorOf": "none", "disabled": false, "dpmsStatus": true,
           "availableModes": ["1920x1080@60.00Hz", "1920x1200@120.00Hz", "1920x1200@90.00Hz", "1920x1200@60.00Hz",
                              "1920x1080@90.00Hz"]})
}

/// `hyprland.monitors` of a reply, as the client reads it.
fn checked(list: &Value) -> Option<Vec<Monitor>> {
    let raw = xr_wire::json::parse_strict(list.to_string().as_bytes()).ok()?;
    hyprland::monitors(&raw).ok()
}

fn one(m: &Value) -> Option<Monitor> {
    if m.is_null() {
        return None;
    }
    Some(
        checked(&json!([m]))
            .expect("a corpus monitor the client accepts")
            .remove(0),
    )
}

/// The stand-in client: the scenario's monitor answers in turn (the last for ever), each call recorded.
struct Script {
    answers: Vec<Value>,
    fail: HashMap<String, String>,
    calls: Arc<Mutex<Vec<Value>>>,
}

impl Script {
    fn call(&mut self, call: Value) -> Result<(), String> {
        let name = call[0].as_str().unwrap().to_owned();
        self.calls.lock().unwrap().push(call);
        match self.fail.get(&name) {
            Some(text) if text.is_empty() => Err("ValueError".into()),
            Some(text) => Err(text.clone()),
            None => Ok(()),
        }
    }
}

impl Display for Script {
    fn monitors(&mut self) -> Option<Vec<Monitor>> {
        self.calls.lock().unwrap().push(json!(["query"]));
        let now = if self.answers.len() > 1 {
            self.answers.remove(0)
        } else {
            self.answers[0].clone()
        };
        if now == "error" {
            return None;
        }
        let list: Vec<Value> = [now, dp1()].into_iter().filter(|m| !m.is_null()).collect();
        checked(&Value::Array(list))
    }
    fn apply_wide_mode(&mut self, output: &str, rate: f64, model: &str) -> Result<(), String> {
        self.call(json!(["apply_wide_mode", output, rate, model]))
    }
    fn apply_pro2_mode(&mut self, output: &str, rate: f64) -> Result<(), String> {
        self.call(json!(["apply_pro2_mode", output, rate]))
    }
    fn apply_session_mode(&mut self, output: &str) -> Result<(), String> {
        self.call(json!(["apply_session_mode", output]))
    }
    fn park_presentation(&mut self, output: &str) -> Result<(), String> {
        self.call(json!(["park_presentation", output]))
    }
}

/// Numbers as floats: the Python's `pro2_2d_rate` gives an `int` rate from its table, the same rate as Rust's `f64`.
fn floats(v: &Value) -> Value {
    match v {
        Value::Number(n) => json!(n.as_f64().unwrap()),
        Value::Array(a) => Value::Array(a.iter().map(floats).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), floats(v))).collect()),
        other => other.clone(),
    }
}

fn result<T: Into<Value>>(r: Result<T, im::SbsError>) -> Value {
    match r {
        Ok(v) => v.into(),
        Err(e) => json!({"error": e.0}),
    }
}

#[test]
fn the_mode_helpers_read_monitors_as_the_python_does() {
    let corpus: Value = serde_json::from_str(SBS).unwrap();
    for row in corpus["modes"].as_array().unwrap() {
        let m = one(&row["monitor"]);
        let m = m.as_ref();
        let offered: Vec<Value> = sbs::offered(m)
            .into_iter()
            .map(|(w, h, hz)| json!([w, h, hz]))
            .collect();
        assert_eq!(Value::Array(offered), row["offered"], "{}", row["monitor"]);
        for model in ["luma-ultra", "pro2", "other"] {
            let got = json!({"best_wide": sbs::best_wide(m, model), "is_wide": sbs::is_wide(m, model),
                             "narrow_rate": sbs::narrow_rate(m, model)});
            assert_eq!(got, row[model], "{model} {}", row["monitor"]);
        }
        for (key, prefer) in [
            ("None", None),
            ("60.0", Some(60.0)),
            ("90.0", Some(90.0)),
            ("144.0", Some(144.0)),
        ] {
            let got = json!(sbs::pro2_2d_rate(m, prefer));
            assert_eq!(
                floats(&got),
                floats(&row["pro2_2d_rate"][key]),
                "{key} {}",
                row["monitor"]
            );
        }
    }
}

#[test]
fn button_sbs_matches_the_python() {
    let corpus: Value = serde_json::from_str(SBS).unwrap();
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 40);
    for s in scenarios {
        let name = s["name"].as_str().unwrap();
        let calls = Arc::new(Mutex::new(Vec::new()));
        let told = Arc::new(Mutex::new(Vec::<String>::new()));
        let clock = Arc::new(Mutex::new(0.0f64));
        let script = Script {
            answers: s["script"].as_array().unwrap().clone(),
            fail: serde_json::from_value(s["fail"].clone()).unwrap(),
            calls: calls.clone(),
        };
        let (t1, t2, t3) = (told.clone(), clock.clone(), clock.clone());
        let button = ButtonSbs::with_clock(
            Box::new(script),
            "DP-2",
            Box::new(move |text| t1.lock().unwrap().push(text.to_owned())),
            s["model"].as_str().unwrap(),
            Box::new(move || *t2.lock().unwrap()),
            Box::new(move |secs| *t3.lock().unwrap() += secs),
            s["enter_wait"].as_f64().unwrap(),
        )
        .unwrap();
        let mut results = Vec::new();
        for step in s["steps"].as_array().unwrap() {
            results.push(match step[0].as_str().unwrap() {
                "enter" => {
                    let limit = step[1].as_u64();
                    let asked = Mutex::new(0u64);
                    let cancelled = || {
                        let mut n = asked.lock().unwrap();
                        *n += 1;
                        limit.is_some_and(|l| *n > l)
                    };
                    result(button.enter(&cancelled))
                }
                "leave" => result(button.leave().map(|()| Value::Null)),
                "settle" => result(button.settle_now()),
                "left_by_hand" => json!(button.left_by_hand()),
                "follow_by_hand" => {
                    button.follow_by_hand();
                    Value::Null
                }
                "switching" => json!(button.switching()),
                "parked" => json!(button.parked()),
                "advance" => {
                    *clock.lock().unwrap() += step[1].as_f64().unwrap();
                    Value::Null
                }
                other => panic!("{name}: unknown step {other}"),
            });
        }
        assert_eq!(Value::Array(results), s["results"], "{name}: results");
        let made = Value::Array(calls.lock().unwrap().clone());
        assert_eq!(floats(&made), floats(&s["calls"]), "{name}: calls");
        assert_eq!(json!(*told.lock().unwrap()), s["told"], "{name}: toasts");
        assert_eq!(json!(*clock.lock().unwrap()), s["clock"], "{name}: clock");
    }
}

// --- the view's side -------------------------------------------------------------------------------------------------

#[derive(Default)]
struct Run3d {
    requests: Vec<Value>,
    pending: Vec<Event>,
    phase: &'static str,
    switch: bool,
}

struct FakeWorker(Arc<Mutex<Run3d>>);

impl Worker for FakeWorker {
    fn request(&self, desktops: &[String], _done: Option<Done>, setting: Option<&Setting>) {
        let setting = setting.map_or(Value::Null, Setting::to_value);
        self.0
            .lock()
            .unwrap()
            .requests
            .push(json!([desktops, setting]));
    }
    fn reassert(&self) {}
    fn take_events(&self) -> Vec<Event> {
        std::mem::take(&mut self.0.lock().unwrap().pending)
    }
    fn switching(&self) -> bool {
        self.0.lock().unwrap().switch
    }
    fn observe_renderer(&self, _report: Value) {}
    fn phase(&self) -> &'static str {
        self.0.lock().unwrap().phase
    }
    fn close(&self, _timeout: Duration) {}
}

fn event(e: &Value) -> Event {
    let text = |i: usize| e[i].as_str().unwrap().to_owned();
    match e[0].as_str().unwrap() {
        "active" => Event::Active(serde_json::from_value(e[1].clone()).unwrap()),
        "off" => Event::Off,
        "leave" => Event::Leave(text(1)),
        "failed" => {
            let kind = match e[1].as_str().unwrap() {
                "engine" => "engine",
                "died" => "died",
                "sbs" => "sbs",
                other => panic!("kind {other}"),
            };
            Event::Failed { kind, why: text(2) }
        }
        other => panic!("event {other}"),
    }
}

fn phase(name: &str) -> &'static str {
    ["off", "engine", "glasses", "on"]
        .into_iter()
        .find(|p| *p == name)
        .unwrap()
}

#[test]
fn the_views_3d_matches_the_python() {
    let corpus: Value = serde_json::from_str(VIEW).unwrap();
    let (run_id, session) = (
        corpus["run_id"].as_str().unwrap(),
        corpus["session"].as_str().unwrap(),
    );
    for s in corpus["scenarios"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        let run3d = Arc::new(Mutex::new(Run3d {
            phase: "off",
            ..Run3d::default()
        }));
        let toasts = Arc::new(Mutex::new(Vec::<String>::new()));
        let t = toasts.clone();
        let mut immersive = Immersive::with_worker(
            Box::new(FakeWorker(run3d.clone())),
            run_id,
            session,
            Box::new(move |text| t.lock().unwrap().push(text.to_owned())),
        );
        let mut setting = im::clean(&s["setting"]);
        let mut names: Vec<String> = serde_json::from_value(s["names"].clone()).unwrap();
        let (mut video, mut zoomed, mut typing) = (false, false, false);
        let mut next_id = corpus["first_request_id"].as_u64().unwrap();
        let mut last_input = 0.0;
        for (i, (step, want)) in s["steps"]
            .as_array()
            .unwrap()
            .iter()
            .zip(s["results"].as_array().unwrap())
            .enumerate()
        {
            let now = (i + 1) as f64;
            let before = run3d.lock().unwrap().requests.len();
            let toasted = toasts.lock().unwrap().len();
            let mut view = Shown {
                setting: &mut setting,
                names: &names,
                video_active: video,
                zoomed,
                typing,
                next_id: &mut next_id,
                now,
                last_input: &mut last_input,
                changed: false,
            };
            let mut out = Value::Null;
            let arg_i = |k: usize| step[k].as_i64().unwrap();
            let frames = match step[0].as_str().unwrap() {
                "sync" => immersive.sync(&mut view),
                "tick" => {
                    run3d.lock().unwrap().pending =
                        step[1].as_array().unwrap().iter().map(event).collect();
                    immersive.tick(&mut view)
                }
                "toggle" => immersive.toggle(&mut view),
                "set" => match immersive.set(&step[1], &mut view) {
                    Ok(frames) => frames,
                    Err(e) => {
                        out = json!({"error": e.to_string()});
                        Vec::new()
                    }
                },
                "click" => immersive.click(arg_i(1), &mut view),
                "tail" => immersive.tail_click(arg_i(1), &mut view),
                "pointer" => immersive.pointer(arg_i(1), step[2].as_bool().unwrap(), &mut view),
                "status" => {
                    out = immersive.status(&view);
                    Vec::new()
                }
                "restarted" => {
                    immersive.renderer_restarted();
                    Vec::new()
                }
                _ => Vec::new(),
            };
            let saved = u64::from(view.changed);
            match step[0].as_str().unwrap() {
                "video" => video = step[1].as_bool().unwrap(),
                "typing" => typing = step[1].as_bool().unwrap(),
                "zoomed" => zoomed = step[1].as_bool().unwrap(),
                "names" => names = serde_json::from_value(step[1].clone()).unwrap(),
                "switch" => run3d.lock().unwrap().switch = step[1].as_bool().unwrap(),
                "phase" => run3d.lock().unwrap().phase = phase(step[1].as_str().unwrap()),
                _ => {}
            }
            let text = String::from_utf8(frames).unwrap();
            let lines: Vec<&str> = text.lines().collect();
            let got = json!({
                "frames": lines, "requests": run3d.lock().unwrap().requests[before..].to_vec(),
                "toasts": toasts.lock().unwrap()[toasted..].to_vec(), "saved": saved,
                "touched": last_input == now, "result": out,
            });
            assert_eq!(&got, want, "{name}, step {i} {step}");
        }
    }
}

/// The view's two ops reach the 3D methods through `on_message` (the panel's `immersive` absolute, a bad one dropped).
#[test]
fn the_ops_reach_it() {
    let run3d = Arc::new(Mutex::new(Run3d {
        phase: "off",
        ..Run3d::default()
    }));
    let mut immersive = Immersive::with_worker(
        Box::new(FakeWorker(run3d.clone())),
        "r1",
        "s",
        Box::new(|_| {}),
    );
    let (mut setting, names, mut next_id, mut last) =
        (Setting::default(), vec!["XR-1".to_owned()], 2, 0.0);
    let frame = |op: &str, payload: &str| Frame {
        request_id: 1,
        run_id: "r1".into(),
        session: "s".into(),
        op: op.into(),
        payload: xr_wire::json::parse_strict(payload.as_bytes()).unwrap(),
    };
    let mut out = Frames::default();
    let mut send = |f: Frame, setting: &mut Setting, out: &mut Frames| {
        let mut view = Shown {
            setting,
            names: &names,
            video_active: false,
            zoomed: false,
            typing: false,
            next_id: &mut next_id,
            now: 1.0,
            last_input: &mut last,
            changed: false,
        };
        immersive.on_message(&f, &mut view, out);
        view.changed
    };
    assert!(send(frame(im::TOGGLE_OP, "{}"), &mut setting, &mut out));
    assert!(setting.global && out.bytes.ends_with(b"\n"));
    let bad =
        r#"{"global": 0, "desktops": {}, "preset": "desktop", "strength": 100, "convergence": 92}"#;
    assert!(!send(frame(im::SET_OP, bad), &mut setting, &mut out));
    let off = r#"{"global": false, "desktops": {}, "preset": "desktop", "strength": 100, "convergence": 92}"#;
    assert!(send(frame(im::SET_OP, off), &mut setting, &mut out));
    assert_eq!(setting, Setting::default());
    let asked: Vec<Value> = run3d
        .lock()
        .unwrap()
        .requests
        .iter()
        .map(|r| r[0].clone())
        .collect();
    assert_eq!(asked, [json!(["XR-1"]), json!([])]);
}
