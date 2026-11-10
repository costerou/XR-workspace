//! Replays `rust/corpus/pointer/sampler.json` (tests/native_spike/sampler_corpus.py): each scripted run through the
//! shipped Python samplers, step by step, through [`Sampler`]. Every step must make the same calls into the pointer, the
//! client and the shape reader, in the same order with the same arguments, wait as long, answer the same and end with
//! the same state. The pointer answers from the step's `p` as the Python's scripted pointer does; each client call and
//! shape read gets the outcome the Python's fake gave it, taken from the recording.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};

use super::columns::ColumnsClient;
use super::overview_cursor::CursorClient;
use super::sampler::{
    CallError, Clock, Mode, PanPlan, SampledPointer, Sampler, SamplerClient, ShapeReader, Side, Snapshot,
    Variant, Want, test_output,
};
use crate::hyprland::Query;
use crate::resize::OutputRect;

/// The step's calls: the recorded ones, and those made so far.
#[derive(Default)]
struct Rec {
    expected: Vec<Value>,
    actual: Vec<Value>,
}

type Shared = Arc<Mutex<Rec>>;

fn is_err(v: &Value) -> bool {
    v.get("$err") == Some(&Value::Bool(true))
}

/// A call into the client or the shape reader: logged, and answered with the recorded outcome of the call at this
/// place in the step (a failure when the recording has another call there; the comparison then reports it).
fn call(rec: &Shared, name: &str, args: Vec<Value>) -> Result<Value, CallError> {
    let mut r = rec.lock().unwrap();
    let out = r
        .expected
        .get(r.actual.len())
        .filter(|e| e["call"] == name)
        .map_or_else(|| json!({"$err": true}), |e| e["out"].clone());
    r.actual.push(json!({"call": name, "args": args, "out": out}));
    if is_err(&out) { Err(CallError::Failed) } else { Ok(out) }
}

/// A pointer call: logged with its arguments (and its answer, for the two that answer).
fn note(rec: &Shared, name: &str, args: Vec<Value>, out: Option<Value>) {
    let mut e = json!({"call": name, "args": args});
    if let Some(out) = out {
        e["out"] = out;
    }
    rec.lock().unwrap().actual.push(e);
}

#[derive(Clone)]
struct StepClock(Arc<Mutex<f64>>);

impl Clock for StepClock {
    fn now(&mut self) -> f64 {
        *self.0.lock().unwrap()
    }
}

/// sampler_corpus.ScriptPlan.
#[derive(Clone, Debug)]
struct Plan {
    home: i64,
    target: i64,
    side: Side,
    along: f64,
    total_px: f64,
}

impl PanPlan for Plan {
    type Frame = Value;
    fn home(&self) -> i64 {
        self.home
    }
    fn target(&self) -> i64 {
        self.target
    }
    fn side(&self) -> Side {
        self.side
    }
    fn exit(&self) -> f64 {
        self.along
    }
    fn entry(&self) -> f64 {
        self.along
    }
    fn total_px(&self) -> f64 {
        self.total_px
    }
    fn frame(&self, travel: f64) -> Value {
        json!({"target": self.target, "travel": travel})
    }
}

impl Snapshot for Plan {
    fn snapshot(&self) -> Value {
        json!({"home": self.home, "target": self.target, "side": self.side.as_str(), "exit": self.along,
               "entry": self.along, "total_px": self.total_px})
    }
}

/// sampler_corpus.ScriptPointer.
struct Pointer {
    s: Value,
    rec: Shared,
}

impl Pointer {
    fn flag(&self, k: &str) -> bool {
        self.s[k].as_bool().unwrap_or_else(|| panic!("pointer {k}"))
    }
    fn int(&self, k: &str) -> i64 {
        self.s[k].as_i64().unwrap_or_else(|| panic!("pointer {k}"))
    }
}

fn pair(p: Option<(i64, i64)>) -> Value {
    p.map_or(Value::Null, |(x, y)| json!([x, y]))
}

impl SampledPointer for Pointer {
    type Plan = Plan;
    fn overview(&mut self) -> bool {
        self.flag("overview")
    }
    fn restore_sampling(&mut self) -> bool {
        self.flag("restore_sampling")
    }
    fn restore_release(&mut self) {
        note(&self.rec, "restore_release", vec![], None);
    }
    fn want(&mut self) -> Want {
        let w = &self.s["want"];
        let point = w[2].as_array().map(|p| (p[0].as_i64().unwrap(), p[1].as_i64().unwrap()));
        Want { serial: w[0].as_i64().unwrap(), kind: Mode::parse(w[1].as_str().unwrap()), point }
    }
    fn binds_dropped(&mut self) -> bool {
        self.flag("binds_dropped")
    }
    fn clear_binds_dropped(&mut self) {
        note(&self.rec, "binds_dropped", vec![json!(false)], None);
        self.s["binds_dropped"] = json!(false);
    }
    fn mode(&mut self) -> Mode {
        Mode::parse(self.s["mode"].as_str().unwrap())
    }
    fn feed(&mut self, cursor: Option<(i64, i64)>, motion: (f64, f64), outputs: &[OutputRect], applied: i64) {
        let outputs: Vec<Value> = outputs.iter().map(test_output).collect();
        note(&self.rec, "feed", vec![pair(cursor), json!([motion.0, motion.1]), json!(outputs), json!(applied)], None);
    }
    fn handles_on(&mut self) -> bool {
        self.flag("handles_on")
    }
    fn sync(&mut self) -> i64 {
        self.int("sync")
    }
    fn shape_gated(&mut self) -> bool {
        self.flag("shape_gated")
    }
    fn shape_pushed(&mut self) -> bool {
        self.flag("shape_pushed")
    }
    fn shape_serial(&mut self) -> i64 {
        self.int("shape_serial")
    }
    fn feed_shape(&mut self, name: Option<&str>, serial: i64) {
        note(&self.rec, "feed_shape", vec![json!(name), json!(serial)], None);
    }
    fn set_shape_wired(&mut self, wired: bool) {
        note(&self.rec, "shape_wired", vec![json!(wired)], None);
    }
    fn reloads(&mut self) -> i64 {
        self.int("reloads")
    }
    fn selected(&mut self) -> i64 {
        self.int("selected")
    }
    fn set_pan(&mut self, frame: Option<&Value>) {
        note(&self.rec, "set_pan", vec![frame.cloned().unwrap_or(Value::Null)], None);
    }
    fn request_switch(&mut self, home: i64, target: i64, panned: bool) {
        note(&self.rec, "request_switch", vec![json!(home), json!(target), json!(panned)], None);
    }
    fn pan_plan(&mut self, target: i64, side: Side, along: f64, output: &OutputRect) -> Option<Plan> {
        let plan = self.s["plan_px"].as_f64().map(|total_px| Plan {
            home: self.int("selected"),
            target,
            side,
            along,
            total_px,
        });
        let out = plan.as_ref().map_or(Value::Null, Snapshot::snapshot);
        note(&self.rec, "pan_plan", vec![json!(target), json!(side.as_str()), json!(along), test_output(output)], Some(out));
        plan
    }
    fn neighbour(&mut self, side: Side, along: f64) -> Option<i64> {
        let out = self.s["neighbours"].get(side.as_str()).and_then(Value::as_i64);
        note(&self.rec, "neighbour", vec![json!(side.as_str()), json!(along)], Some(json!(out)));
        out
    }
    fn can_switch(&mut self) -> bool {
        self.flag("can_switch")
    }
    fn can_scroll(&mut self) -> bool {
        self.flag("can_scroll")
    }
}

/// sampler_corpus.ScriptClient, its outcomes from the recording.
struct Client {
    rec: Shared,
    scrolls: bool,
}

fn secs(d: Duration) -> Value {
    json!(d.as_secs_f64())
}

fn with_bind(bind: &[String], rest: &[Value]) -> Vec<Value> {
    bind.iter().map(|s| json!(s)).chain(rest.iter().cloned()).collect()
}

fn unit(r: Result<Value, CallError>) -> Result<(), CallError> {
    r.map(drop)
}

impl CursorClient for Client {
    fn hide_overview_cursor(&mut self) -> Result<(), CallError> {
        unit(call(&self.rec, "hide_overview_cursor", vec![]))
    }
    fn show_overview_cursor(&mut self) -> Result<(), CallError> {
        unit(call(&self.rec, "show_overview_cursor", vec![]))
    }
}

impl ColumnsClient for Client {
    fn query(&mut self, kind: Query, timeout: Duration) -> Result<Value, CallError> {
        let kind = match kind {
            Query::Monitors => "monitors",
            Query::Workspaces => "workspaces",
            Query::Clients => "clients",
            other => panic!("unexpected query {other:?}"),
        };
        call(&self.rec, "query", vec![json!(kind), secs(timeout)])
    }
    fn scroll_to_window(&mut self, address: &str) -> Result<(), CallError> {
        unit(call(&self.rec, "scroll_to_window", vec![json!(address)]))
    }
}

impl SamplerClient for Client {
    fn cursor_pos(&mut self, timeout: Duration) -> Result<Option<(f64, f64)>, CallError> {
        let reply = call(&self.rec, "query", vec![json!("cursorpos"), secs(timeout)])?;
        Ok(super::sampler::cursor_reply(&reply))
    }
    fn grab_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError> {
        unit(call(&self.rec, "grab_pointer", with_bind(bind_args, &[json!(x), json!(y)])))
    }
    fn lock_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError> {
        unit(call(&self.rec, "lock_pointer", with_bind(bind_args, &[json!(x), json!(y)])))
    }
    fn release_pointer(&mut self, x: Option<i64>, y: Option<i64>) -> Result<(), CallError> {
        let args = match (x, y) {
            (Some(x), Some(y)) => vec![json!(x), json!(y)],
            (None, None) => vec![],
            _ => panic!("half a release point"),
        };
        unit(call(&self.rec, "release_pointer", args))
    }
    fn warp_cursor(&mut self, x: i64, y: i64) -> Result<(), CallError> {
        unit(call(&self.rec, "warp_cursor", vec![json!(x), json!(y)]))
    }
    fn remove_resize_binds(&mut self) -> Result<(), CallError> {
        unit(call(&self.rec, "remove_resize_binds", vec![]))
    }
    fn enter_desktop(
        &mut self,
        bind_args: &[String],
        x: Option<i64>,
        y: Option<i64>,
        monitor: Option<&str>,
    ) -> Result<(), CallError> {
        let mut rest: Vec<Value> = [x, y].into_iter().flatten().map(|v| json!(v)).collect();
        rest.push(json!(monitor));
        unit(call(&self.rec, "enter_desktop", with_bind(bind_args, &rest)))
    }
    fn arm_click_binds(&mut self, bind_args: &[String]) -> Result<(), CallError> {
        unit(call(&self.rec, "arm_click_binds", with_bind(bind_args, &[])))
    }
    fn confine_cursor(&mut self, x: i64, y: i64, name: &str) -> Result<(), CallError> {
        unit(call(&self.rec, "confine_cursor", vec![json!(x), json!(y), json!(name)]))
    }
    fn scrolls(&self) -> bool {
        self.scrolls
    }
}

/// Equal as the Python's `same`: numbers by value (an int and a float alike), everything else exactly. serde_json
/// without its `float_roundtrip` feature can read a float a bit off the one Python wrote (`0.009267840593141797` reads
/// one ulp low), so floats agree within 2 ulps of the larger.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => match (x.as_i64(), y.as_i64()) {
            (Some(x), Some(y)) => x == y,
            _ => {
                let (x, y) = (x.as_f64().unwrap(), y.as_f64().unwrap());
                x == y || (x - y).abs() <= 2.0 * f64::EPSILON * x.abs().max(y.abs())
            }
        },
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(a, b)| same(a, b)),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
        }
        _ => a == b,
    }
}

fn output(v: &Value) -> OutputRect {
    OutputRect {
        index: v["index"].as_u64().unwrap() as usize,
        x: v["x"].as_i64().unwrap(),
        y: v["y"].as_i64().unwrap(),
        width: v["width"].as_f64().unwrap(),
        height: v["height"].as_f64().unwrap(),
    }
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_owned()).collect()
}

/// Replays one case; the differences found, one line each.
fn replay(case: &Value) -> Vec<String> {
    let name = case["name"].as_str().unwrap();
    let variant = match case["variant"].as_str().unwrap() {
        "plain" => Variant::Plain,
        "locked" => Variant::Locked,
        "unified" => Variant::Unified,
        v => panic!("variant {v}"),
    };
    let rec: Shared = Arc::default();
    let clock = StepClock(Arc::new(Mutex::new(0.0)));
    let pointer = Pointer { s: case["pointer"].clone(), rec: rec.clone() };
    let client = Client { rec: rec.clone(), scrolls: case["scrolls"].as_bool().unwrap() };
    let shape: Option<ShapeReader> = case["shape"].as_bool().unwrap().then(|| {
        let rec = rec.clone();
        Box::new(move || Ok(call(&rec, "read_shape", vec![])?.as_str().map(str::to_owned))) as ShapeReader
    });
    let mut sampler = Sampler::new(
        variant,
        client,
        strings(&case["names"]),
        pointer,
        strings(&case["bind_args"]),
        case["period"].as_f64().unwrap(),
        shape,
        clock.clone(),
    );
    let mut diffs = Vec::new();
    let check = |diffs: &mut Vec<String>, at: &str, what: &str, got: &Value, want: &Value| {
        if !same(got, want) {
            diffs.push(format!("{name} {at} {what}:\n  rust   {got}\n  python {want}"));
        }
    };
    let init = std::mem::take(&mut rec.lock().unwrap().actual);
    check(&mut diffs, "init", "events", &json!(init), &case["init"]);
    for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
        let at = format!("step {i}");
        *clock.0.lock().unwrap() = step["t"].as_f64().unwrap();
        if let Some(p) = step.get("p").and_then(Value::as_object) {
            for (k, v) in p {
                sampler.pointer.s[k] = v.clone();
            }
        }
        rec.lock().unwrap().expected = step["events"].as_array().unwrap().clone();
        let (mut wait, mut ret) = (Value::Null, Value::Null);
        match step["op"].as_str().unwrap() {
            "step" => wait = json!(sampler.step().secs),
            "sample_restore" => {
                let outputs: Vec<OutputRect> = step["outputs"].as_array().unwrap().iter().map(output).collect();
                ret = json!(sampler.sample_restore(&outputs));
            }
            "set_names" => sampler.set_names(strings(&step["names"])),
            "stop" => sampler.stop(),
            op => panic!("op {op}"),
        }
        let events = std::mem::take(&mut rec.lock().unwrap().actual);
        check(&mut diffs, &at, "events", &json!(events), &step["events"]);
        check(&mut diffs, &at, "wait", &wait, &step["wait"]);
        check(&mut diffs, &at, "ret", &ret, &step["ret"]);
        check(&mut diffs, &at, "state", &sampler.state(), &step["state"]);
        if diffs.len() > 3 {
            break;
        }
    }
    diffs
}

fn corpus() -> Vec<Value> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus/pointer/sampler.json");
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
}

#[test]
fn every_scripted_run_replays_as_the_python() {
    let cases = corpus();
    assert!(cases.len() >= 18, "{} cases", cases.len());
    let mut failures = Vec::new();
    let (mut steps, mut events) = (0, 0);
    for case in &cases {
        steps += case["steps"].as_array().unwrap().len();
        events += case["steps"].as_array().unwrap().iter().map(|s| s["events"].as_array().unwrap().len()).sum::<usize>();
        failures.extend(replay(case));
    }
    assert!(steps > 400 && events > 1000, "{steps} steps, {events} events");
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn a_changed_call_fails_the_replay() {
    let cases = corpus();
    let mut case = cases.into_iter().find(|c| c["name"] == "unified_pan").unwrap();
    let step = case["steps"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|s| s["events"].as_array().unwrap().iter().any(|e| e["call"] == "confine_cursor"))
        .unwrap();
    let e = step["events"].as_array_mut().unwrap().iter_mut().find(|e| e["call"] == "confine_cursor").unwrap();
    e["args"][0] = json!(e["args"][0].as_i64().unwrap() + 1);
    assert!(!replay(&case).is_empty());
}
