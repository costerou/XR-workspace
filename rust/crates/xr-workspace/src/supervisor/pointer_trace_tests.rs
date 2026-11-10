//! Replays `rust/corpus/pointer/traces-*.json` (tests/native_spike/pointer_trace_corpus.py records them): every
//! recorded run of the Python's `OverviewPointer` and `UnifiedPointer` through the Rust ones, with the replay's exact
//! semantics (`pointer_trace_corpus.replay`). The control answers from the trace: each call the pointer makes must be the
//! recorded one, in order, with the same arguments, the state and control attributes it checked at that moment; its
//! answer and its side effects are the recorded ones. After each step: the result (frames byte for byte), the control
//! attributes and the pointer's probed fields.

use std::collections::{BTreeMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};

use super::edge_pan::PanFrame;
use super::free_pointer::{self as fp, Hit};
use super::orbit::Geometry;
use super::overview_pointer::{Control, Found, OverviewPointer};
use super::sampler::Clock;
use super::unified_pointer::UnifiedPointer;
use super::view::{self, Field, ViewOptions, ViewState};
use crate::geometry::{Desktop, Placement};
use crate::pyjson::Json;
use crate::resize::OutputRect;

// --- encoding -----------------------------------------------------------------------------------------------------

/// `same(a, b)`: floats to 1e-9 relative or absolute (libm), a bool never a number, everything else exactly (an int
/// and a bool differ).
fn same(a: &Json, b: &Json) -> bool {
    let number = |j: &Json| match *j {
        Json::Int(v) => Some(v as f64),
        Json::Float(v) => Some(v),
        _ => None,
    };
    if matches!(a, Json::Float(_)) || matches!(b, Json::Float(_)) {
        return match (number(a), number(b)) {
            (Some(x), Some(y)) => x == y || (x - y).abs() <= (1e-9 * x.abs().max(y.abs())).max(1e-9),
            _ => false,
        };
    }
    match (a, b) {
        (Json::Array(x), Json::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(a, b)| same(a, b)),
        (Json::Object(x), Json::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| b.get(k).is_some_and(|w| same(v, w)))
        }
        _ => a == b,
    }
}

fn get<'a>(j: &'a Json, k: &str) -> &'a Json {
    j.get(k).unwrap_or(&Json::Null)
}

fn obj(entries: Vec<(&str, Json)>) -> Json {
    Json::Object(entries.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn bytes(b: &[u8]) -> Json {
    obj(vec![("$b", Json::Str(String::from_utf8(b.to_vec()).expect("frames are UTF-8")))])
}

/// `dec(v)` for bytes: the frame's bytes.
fn frame_of(j: &Json) -> Vec<u8> {
    match j.get("$b") {
        Some(Json::Str(s)) => s.clone().into_bytes(),
        _ => Vec::new(),
    }
}

fn num(j: &Json) -> f64 {
    match *j {
        Json::Int(v) => v as f64,
        Json::Float(v) => v,
        _ => panic!("not a number: {j:?}"),
    }
}

fn int(j: &Json) -> i64 {
    match *j {
        Json::Int(v) => v,
        Json::Float(v) if v.fract() == 0.0 => v as i64,
        _ => panic!("not an int: {j:?}"),
    }
}

fn truthy(j: &Json) -> bool {
    super::overview_pointer::truthy(j)
}

fn placement_json(p: &Placement) -> Json {
    obj(vec![
        ("x", Json::Int(p.x)),
        ("y", Json::Int(p.y)),
        ("distance", Json::Int(p.distance)),
        ("curvature", Json::Int(p.curvature)),
    ])
}

fn placement(j: &Json) -> Placement {
    Placement {
        x: int(get(j, "x")),
        y: int(get(j, "y")),
        distance: int(get(j, "distance")),
        curvature: int(get(j, "curvature")),
    }
}

fn floats(v: &[f64]) -> Json {
    Json::Array(v.iter().map(|x| Json::Float(*x)).collect())
}

fn hit_json(h: &Hit) -> Json {
    let k = Json::Str(h.kind().into());
    let i = |at: usize| Json::Int(at as i64);
    let s = |v: &str| Json::Str(v.into());
    Json::Array(match *h {
        Hit::Pill(at)
        | Hit::Panel(at)
        | Hit::ResolutionBox(at)
        | Hit::ScaleBox(at)
        | Hit::BackgroundBox(at)
        | Hit::BackgroundSwitch(at)
        | Hit::Maximize(at)
        | Hit::OptionsGear(at) => vec![k, i(at)],
        Hit::Slider(at, field, x) => vec![k, i(at), s(field), Json::Float(x)],
        Hit::Scale(at, p) => vec![k, i(at), Json::Int(p)],
        Hit::Resolution(at, (w, h)) => vec![k, i(at), Json::Array(vec![Json::Int(w), Json::Int(h)])],
        Hit::Background(at, name)
        | Hit::Action(at, name)
        | Hit::Corner(at, name)
        | Hit::Edge(at, name)
        | Hit::Immersive(at, name)
        | Hit::Roll(at, name) => vec![k, i(at), s(name)],
        Hit::Desktop(at, q) => vec![k, i(at), floats(&q)],
        Hit::Help(name) => vec![k, s(name)],
        Hit::OptionsPanel(at, x, y) => vec![k, i(at), Json::Float(x), Json::Float(y)],
    })
}

fn opt<T>(v: Option<T>, f: impl Fn(T) -> Json) -> Json {
    v.map_or(Json::Null, f)
}

fn point_json(p: Option<(i64, i64)>) -> Json {
    opt(p, |(x, y)| Json::Array(vec![Json::Int(x), Json::Int(y)]))
}

fn output(j: &Json) -> OutputRect {
    OutputRect {
        index: int(get(j, "index")) as usize,
        x: int(get(j, "x")),
        y: int(get(j, "y")),
        width: num(get(j, "width")),
        height: num(get(j, "height")),
    }
}

/// A geometry as the tests hand it over (the renderer's parsed dict, which the Python stores as it is).
fn geometry(j: &Json) -> Option<Geometry> {
    if *j == Json::Null {
        return None;
    }
    let half = match get(j, "half") {
        Json::Array(h) => [num(&h[0]), num(&h[1])],
        _ => panic!("half"),
    };
    let tiles = match get(j, "tiles") {
        Json::Array(t) => t
            .iter()
            .map(|t| match t {
                Json::Array(v) => [num(&v[0]), num(&v[1]), num(&v[2]), num(&v[3])],
                _ => panic!("tile"),
            })
            .collect(),
        _ => panic!("tiles"),
    };
    Some(Geometry { w: int(get(j, "w")), h: int(get(j, "h")), half, tiles })
}

fn help_geometry(j: &Json) -> Option<fp::HelpGeometry> {
    if *j == Json::Null {
        return None;
    }
    let rect = |k: &str| match j.get(k) {
        Some(Json::Array(v)) => Some([num(&v[0]), num(&v[1]), num(&v[2]), num(&v[3])]),
        _ => None,
    };
    Some(fp::HelpGeometry { card: rect("card")?, prev: rect("prev"), indicator: rect("indicator"), next: rect("next") })
}

// --- the replay's control -----------------------------------------------------------------------------------------

/// `ReplayClock`: a step's `t`, or its recorded reads in order (a wall-clock run), the last one repeated.
#[derive(Default)]
struct ClockState {
    now: f64,
    reads: Option<VecDeque<f64>>,
}

struct ReplayClock(Arc<Mutex<ClockState>>);

impl Clock for ReplayClock {
    fn now(&mut self) -> f64 {
        let mut c = self.0.lock().unwrap();
        if let Some(t) = c.reads.as_mut().and_then(VecDeque::pop_front) {
            c.now = t;
        }
        c.now
    }
}

/// `ReplayControl`: answers the pointer from the trace. The first mismatch is kept (the Python raises it) and ends the
/// replay after the step.
struct Replay {
    state: ViewState,
    /// Every ViewState field as last written, in the Python's JSON (the snapshot overlays what the pointer writes).
    raw: Vec<(String, Json)>,
    attrs: BTreeMap<String, Json>,
    run_id: String,
    session: String,
    calls: Option<VecDeque<Json>>,
    err: Option<String>,
}

impl Replay {
    fn new(init: &Json) -> Replay {
        let names: Vec<String> = match get(init, "names") {
            Json::Array(n) => n.iter().map(|n| n.as_str().expect("a name").to_string()).collect(),
            _ => panic!("names"),
        };
        let refs: Vec<&str> = names.iter().map(String::as_str).collect();
        let state = ViewState::new(&refs, ViewOptions::default()).expect("a view");
        let mut r = Replay {
            state,
            raw: Vec::new(),
            attrs: BTreeMap::new(),
            run_id: String::new(),
            session: String::new(),
            calls: None,
            err: None,
        };
        r.apply_state(get(init, "state"));
        r.apply_attrs(get(init, "control"));
        r
    }

    fn fail(&mut self, why: String) {
        if self.err.is_none() {
            self.err = Some(why);
        }
    }

    fn apply_state(&mut self, fields: &Json) {
        for (k, v) in fields.as_object().unwrap_or(&[]) {
            match self.raw.iter_mut().find(|(n, _)| n == k) {
                Some(e) => e.1 = v.clone(),
                None => self.raw.push((k.clone(), v.clone())),
            }
            let s = &mut self.state;
            let b = || v.as_bool().unwrap_or_else(|| panic!("{k}: {v:?}"));
            match k.as_str() {
                "names" => {
                    s.names = match v {
                        Json::Array(n) => n.iter().map(|n| n.as_str().unwrap().to_string()).collect(),
                        _ => panic!("names"),
                    }
                }
                "zoomed" => s.zoomed = b(),
                "typing" => s.typing = b(),
                "menu_open" => s.menu_open = b(),
                "scale_picker" => s.scale_picker = b(),
                "resolution_picker" => s.resolution_picker = b(),
                "background_picker" => s.background_picker = b(),
                "selected" => s.selected = int(v) as usize,
                "menu_desktop" => s.menu_desktop = int(v) as usize,
                "placements" => {
                    s.placements = match v {
                        Json::Array(p) => p.iter().map(placement).collect(),
                        _ => panic!("placements"),
                    }
                }
                "menu_surface" => s.menu_surface = (*v != Json::Null).then(|| placement(v)),
                "menu_field" => {
                    if let Some(f) = v.as_str().and_then(Field::parse) {
                        s.menu_field = f
                    }
                }
                "maximized_mode" => s.maximized_mode = view::Mode::parse(v.as_str().unwrap()).expect("a mode"),
                "sizes" => {
                    s.sizes = match v {
                        Json::Array(d) => Some(
                            d.iter()
                                .map(|d| Desktop {
                                    width: int(get(d, "width")),
                                    height: int(get(d, "height")),
                                    scale: num(get(d, "scale")),
                                })
                                .collect(),
                        ),
                        _ => None,
                    }
                }
                _ => {}
            }
        }
    }

    fn apply_attrs(&mut self, fields: &Json) {
        for (k, v) in fields.as_object().unwrap_or(&[]) {
            match k.as_str() {
                "run_id" => self.run_id = v.as_str().unwrap_or("").into(),
                "session" => self.session = v.as_str().unwrap_or("").into(),
                _ => {}
            }
            self.attrs.insert(k.clone(), v.clone());
        }
    }

    fn state_snapshot(&self) -> Vec<(String, Json)> {
        let mut out = self.raw.clone();
        for (k, v) in out.iter_mut() {
            match k.as_str() {
                "selected" => *v = Json::Int(self.state.selected as i64),
                "placements" => *v = Json::Array(self.state.placements.iter().map(placement_json).collect()),
                _ => {}
            }
        }
        out
    }

    fn attr(&self, k: &str) -> Json {
        if k == "sender" {
            return Json::Bool(self.attrs.get(k).is_some_and(truthy));
        }
        self.attrs.get(k).cloned().unwrap_or(Json::Null)
    }

    /// `_check(where, fields, attrs)`.
    fn check(&mut self, at: &str, fields: Option<&Json>, attrs: Option<&Json>) {
        if let Some(fields) = fields {
            let now = self.state_snapshot();
            for (k, v) in fields.as_object().unwrap_or(&[]) {
                let got = now.iter().find(|(n, _)| n == k).map_or(Json::Null, |(_, v)| v.clone());
                if !same(v, &got) {
                    self.fail(format!("{at}: state.{k} {got:?} != {v:?}"));
                }
            }
        }
        if let Some(attrs) = attrs {
            for (k, v) in attrs.as_object().unwrap_or(&[]) {
                let got = self.attr(k);
                if !same(v, &got) {
                    self.fail(format!("{at}: control.{k} {got:?} != {v:?}"));
                }
            }
        }
    }

    /// `_answer(name, *args, **kwargs)`: the next recorded call must be this one; its callbacks run, its effects apply,
    /// its answer comes back.
    fn answer(&mut self, name: &str, args: Vec<Json>, kwargs: Vec<(&str, Json)>, mut cbs: Vec<Found>) -> Json {
        let Some(entry) = self.calls.as_mut().and_then(VecDeque::pop_front) else {
            self.fail(format!("unrecorded call {name}{args:?}"));
            return Json::Null;
        };
        let n = get(&entry, "n").as_str().unwrap_or("");
        if n != name {
            self.fail(format!("call {name} where the trace has {n}"));
            return Json::Null;
        }
        self.check(&format!("before {name}"), entry.get("chk"), entry.get("chk_c"));
        let want = get(&entry, "a");
        let got = Json::Array(args);
        // a callback is recorded by its number, sent as {'$cb': None} (pointer_trace_corpus's replay)
        let unnumbered = match want {
            Json::Array(a) => Json::Array(
                a.iter()
                    .map(|a| if a.get("$cb").is_some() { obj(vec![("$cb", Json::Null)]) } else { a.clone() })
                    .collect(),
            ),
            other => other.clone(),
        };
        if !same(&unnumbered, &got) {
            self.fail(format!("{name} args {got:?} != {want:?}"));
        }
        let k = entry.get("k").cloned().unwrap_or(Json::Object(Vec::new()));
        let kwargs = obj(kwargs);
        if !same(&k, &kwargs) {
            self.fail(format!("{name} keywords {kwargs:?} != {k:?}"));
        }
        if let Some(Json::Array(cb)) = entry.get("cb") {
            for pair in cb {
                let Json::Array(pair) = pair else { continue };
                let k = &pair[0];
                // the argument recorded as {'$cb': k} is the one called (each call here takes one callback)
                let named = matches!(want, Json::Array(a) if a.iter().any(|a| a.get("$cb") == Some(k)));
                assert!(named, "{name}: callback {k:?} is no argument");
                let f = if cbs.is_empty() { None } else { Some(cbs.remove(0)) };
                let cb_args = match &pair[1] {
                    Json::Array(a) => a.first().cloned().unwrap_or(Json::Null),
                    _ => Json::Null,
                };
                if let Some(f) = f {
                    f(cb_args);
                }
            }
        }
        self.apply_state(get(&entry, "s"));
        self.apply_attrs(get(&entry, "c"));
        get(&entry, "r").clone()
    }

    fn frames(&mut self, name: &str, args: Vec<Json>, kwargs: Vec<(&str, Json)>) -> Vec<u8> {
        let r = self.answer(name, args, kwargs, Vec::new());
        frame_of(&r)
    }

    fn call(&mut self, name: &str, args: Vec<Json>) {
        self.answer(name, args, Vec::new(), Vec::new());
    }
}

fn i(v: usize) -> Json {
    Json::Int(v as i64)
}

fn s(v: &str) -> Json {
    Json::Str(v.into())
}

impl Control for Replay {
    fn state(&self) -> &ViewState {
        &self.state
    }
    fn state_mut(&mut self) -> &mut ViewState {
        &mut self.state
    }
    fn video_active(&self) -> bool {
        self.attrs.get("video_active").is_some_and(truthy)
    }
    fn keys_open(&self) -> bool {
        self.attrs.get("keys_open").is_some_and(truthy)
    }
    fn help_visible(&self) -> bool {
        self.attrs.get("help_visible").is_some_and(truthy)
    }
    fn reloads(&self) -> i64 {
        self.attrs.get("reloads").map_or(0, int)
    }
    fn run_id(&self) -> &str {
        &self.run_id
    }
    fn session(&self) -> &str {
        &self.session
    }
    fn next_id(&self) -> u64 {
        self.attrs.get("next_id").map_or(0, int) as u64
    }
    fn set_next_id(&mut self, id: u64) {
        self.attrs.insert("next_id".into(), Json::Int(id as i64));
    }
    fn set_save_due(&mut self, at: f64) {
        self.attrs.insert("save_due".into(), Json::Float(at));
    }
    fn set_last_input(&mut self, at: f64) {
        self.attrs.insert("last_input".into(), Json::Float(at));
    }
    fn clock(&mut self) -> f64 {
        let r = self.answer("clock", Vec::new(), Vec::new(), Vec::new());
        match r {
            Json::Int(_) | Json::Float(_) => num(&r),
            _ => 0.0,
        }
    }
    fn placement_frame(&mut self) -> Vec<u8> {
        self.frames("_placement_frame", vec![], vec![])
    }
    fn menu_frame(&mut self) -> Vec<u8> {
        self.frames("_menu_frame", vec![], vec![])
    }
    fn frame(&mut self) -> Vec<u8> {
        self.frames("_frame", vec![], vec![])
    }
    fn follow_focus(&mut self) -> Vec<u8> {
        self.frames("follow_focus", vec![], vec![])
    }
    fn flush(&mut self) {
        self.call("flush", vec![]);
    }
    fn select_desktop(&mut self, n: usize, panned: Option<bool>) -> Vec<u8> {
        let k = panned.map(|p| vec![("panned", Json::Bool(p))]).unwrap_or_default();
        self.frames("select_desktop", vec![i(n)], k)
    }
    fn switch_desktop(&mut self, n: usize, panned: bool) -> Vec<u8> {
        self.frames("switch_desktop", vec![i(n)], vec![("panned", Json::Bool(panned))])
    }
    fn pill_click(&mut self, n: usize) -> Vec<u8> {
        self.frames("pill_click", vec![i(n)], vec![])
    }
    fn pill_release(&mut self) -> Vec<u8> {
        self.frames("pill_release", vec![], vec![])
    }
    fn pill_close(&mut self) -> Vec<u8> {
        self.frames("pill_close", vec![], vec![])
    }
    fn pill_slide(&mut self, n: usize, field: &str, fraction: f64, surface: Option<&Placement>) -> Vec<u8> {
        let k = surface.map(|p| vec![("surface", placement_json(p))]).unwrap_or_default();
        self.frames("pill_slide", vec![i(n), s(field), Json::Float(fraction)], k)
    }
    fn panel_keys_on(&mut self) {
        self.call("panel_keys_on", vec![]);
    }
    fn desktop_press(&mut self) -> Vec<u8> {
        self.frames("desktop_press", vec![], vec![])
    }
    fn immersive_pointer(&mut self, n: i64, tail: Option<bool>) -> Vec<u8> {
        let mut a = vec![Json::Int(n)];
        if let Some(t) = tail {
            a.push(Json::Bool(t));
        }
        self.frames("immersive_pointer", a, vec![])
    }
    fn immersive_click(&mut self, n: usize) -> Vec<u8> {
        self.frames("immersive_click", vec![i(n)], vec![])
    }
    fn immersive_tail_click(&mut self, n: usize) -> Vec<u8> {
        self.frames("immersive_tail_click", vec![i(n)], vec![])
    }
    fn desktop_action(&mut self, n: usize, name: &str) -> Vec<u8> {
        self.frames("desktop_action", vec![i(n), s(name)], vec![])
    }
    fn help_turn(&mut self, turn: i64) -> Vec<u8> {
        self.frames("help_turn", vec![Json::Int(turn)], vec![])
    }
    fn maximize_monitor(&mut self, name: &str) -> Vec<u8> {
        self.frames("maximize_monitor", vec![s(name)], vec![])
    }
    fn scale_close(&mut self) -> Vec<u8> {
        self.frames("scale_close", vec![], vec![])
    }
    fn resolution_close(&mut self) -> Vec<u8> {
        self.frames("resolution_close", vec![], vec![])
    }
    fn background_close(&mut self) -> Vec<u8> {
        self.frames("background_close", vec![], vec![])
    }
    fn scale_box(&mut self, n: usize) -> Vec<u8> {
        self.frames("scale_box", vec![i(n)], vec![])
    }
    fn scale_pick(&mut self, n: usize, percent: i64) -> Vec<u8> {
        self.frames("scale_pick", vec![i(n), Json::Int(percent)], vec![])
    }
    fn resolution_box(&mut self, n: usize) -> Vec<u8> {
        self.frames("resolution_box", vec![i(n)], vec![])
    }
    fn resolution_pick(&mut self, n: usize, size: (i64, i64)) -> Vec<u8> {
        self.frames("resolution_pick", vec![i(n), Json::Array(vec![Json::Int(size.0), Json::Int(size.1)])], vec![])
    }
    fn background_box(&mut self, n: usize) -> Vec<u8> {
        self.frames("background_box", vec![i(n)], vec![])
    }
    fn background_pick(&mut self, n: usize, name: &str) -> Vec<u8> {
        self.frames("background_pick", vec![i(n), s(name)], vec![])
    }
    fn background_switch(&mut self, n: usize) -> Vec<u8> {
        self.frames("background_switch", vec![i(n)], vec![])
    }
    fn pan_frame(&mut self, frame: Option<&PanFrame>) -> Vec<u8> {
        let a = match frame {
            Some(f) => Json::parse(&serde_json::to_string(f).expect("a pan frame")).expect("its JSON"),
            None => Json::Null,
        };
        self.frames("pan_frame", vec![a], vec![])
    }

    fn has_options(&self) -> bool {
        true
    }
    fn options_snapshot(&mut self) -> Json {
        self.answer("options.snapshot", vec![], vec![], vec![])
    }
    fn options_visible(&self) -> bool {
        self.attrs.get("options.visible").is_some_and(truthy)
    }
    fn options_capture(&self) -> Option<Json> {
        self.attrs.get("options.capture").filter(|c| **c != Json::Null).cloned()
    }
    fn set_options_capture(&mut self, capture: Option<Json>) {
        self.attrs.insert("options.capture".into(), capture.unwrap_or(Json::Null));
    }
    fn options_preview(&self) -> Json {
        self.attr("options.preview")
    }
    fn set_options_preview(&mut self, preview: Json) {
        self.attrs.insert("options.preview".into(), preview);
    }
    fn set_options_focused(&mut self, focused: bool) {
        self.attrs.insert("options.focused".into(), Json::Bool(focused));
    }
    fn options_keyboard_owned(&self) -> bool {
        self.attrs.get("options.keyboard_owned") == Some(&Json::Bool(true))
    }
    fn options_touch(&mut self) {
        self.call("options.touch", vec![]);
    }
    fn options_refresh(&mut self) {
        self.call("options.refresh", vec![]);
    }
    fn options_desktop_press(&mut self) {
        self.call("options.desktop_press", vec![]);
    }
    fn options_intent(&mut self, name: &str, kwargs: Vec<(&'static str, Json)>) -> Vec<u8> {
        self.frames("options_intent", vec![s(name)], kwargs)
    }
    fn options_frame(&mut self) -> Vec<u8> {
        self.frames("_options_frame", vec![], vec![])
    }
    fn options_activate(&mut self, row: i64) -> Vec<u8> {
        self.frames("options_activate", vec![Json::Int(row)], vec![])
    }

    fn has_sender(&self) -> bool {
        self.attrs.get("sender").is_some_and(truthy)
    }
    fn sender_lookup(&mut self, monitor: &str, point: (i64, i64), found: Found) {
        let cb = obj(vec![("$cb", Json::Null)]);
        self.answer("sender.lookup", vec![s(monitor), point_json(Some(point)), cb], vec![], vec![found]);
    }
    fn sender_under_cursor(&mut self, found: Found) {
        let cb = obj(vec![("$cb", Json::Null)]);
        self.answer("sender.under_cursor", vec![cb], vec![], vec![found]);
    }
    fn sender_land(&mut self, address: &str, monitor: &str) {
        self.call("sender.land", vec![s(address), s(monitor)]);
    }
    fn sender_drop(&mut self, window: &Json, from: &str, point: Option<(i64, i64)>, to: &str) {
        self.call("sender.drop", vec![window.clone(), s(from), point_json(point), s(to)]);
    }
}

// --- the replay ---------------------------------------------------------------------------------------------------

enum Pointer {
    Overview(OverviewPointer<Replay>),
    Unified(UnifiedPointer<Replay>),
}

impl Pointer {
    fn base(&mut self) -> &mut OverviewPointer<Replay> {
        match self {
            Pointer::Overview(p) => p,
            Pointer::Unified(p) => p,
        }
    }

    fn base_ref(&self) -> &OverviewPointer<Replay> {
        match self {
            Pointer::Overview(p) => p,
            Pointer::Unified(p) => p,
        }
    }
}

/// `probe(pointer)`: the checked fields, in the Python's JSON.
fn probe(p: &OverviewPointer<Replay>, k: &str) -> Json {
    match k {
        "mode" => s(p.mode.as_str()),
        "want" => Json::Array(vec![Json::Int(p.want.serial), s(p.want.kind.as_str()), point_json(p.want.point)]),
        "desktop" => opt(p.desktop, i),
        "pointer" => opt(p.pointer, |q| floats(&q)),
        "hover" => opt(p.hover.as_ref(), hit_json),
        "gain" => Json::Float(p.gain),
        "summoned" => Json::Bool(p.summoned),
        "rolls" => Json::Array(p.rolls.iter().map(|r| Json::Int(*r)).collect()),
        "levelling" => Json::Object(p.levelling.iter().map(|(n, t)| (n.clone(), Json::Float(*t))).collect()),
        "zoom_held" => opt(p.zoom_held, Json::Float),
        "shown" => p.shown.clone(),
        "roll_names" => Json::Array(p.roll_names.iter().map(|n| s(n)).collect()),
        "binds_dropped" => Json::Bool(p.binds_dropped),
        "window_drag" => Json::Bool(p.window_drag),
        _ => panic!("probe {k}"),
    }
}

fn arg(a: &[Json], n: usize) -> &Json {
    a.get(n).unwrap_or(&Json::Null)
}

fn cursor(j: &Json) -> Option<(i64, i64)> {
    match j {
        Json::Array(c) => Some((int(&c[0]), int(&c[1]))),
        _ => None,
    }
}

fn outputs(j: &Json) -> Vec<OutputRect> {
    match j {
        Json::Array(o) => o.iter().map(output).collect(),
        _ => Vec::new(),
    }
}

fn result_frames(r: Option<Vec<u8>>) -> Json {
    opt(r, |b| bytes(&b))
}

/// One step's call: `getattr(pointer, m)(*a, **k)`; Err(name) for the exception it raised.
fn call(p: &mut Pointer, m: &str, a: &[Json], k: &Json) -> Result<Json, String> {
    if let Pointer::Unified(u) = p
        && let Some(r) = super::unified_pointer::replay_call(u, m, a, k)
    {
        return r;
    }
    let b = p.base();
    Ok(match m {
        "poll" => bytes(&b.poll(num(arg(a, 0)))),
        "feed" => {
            let motion = match arg(a, 1) {
                Json::Array(v) => (num(&v[0]), num(&v[1])),
                _ => (0.0, 0.0),
            };
            b.feed_changed(cursor(arg(a, 0)), motion, &outputs(arg(a, 2)), int(arg(a, 3)))
        }
        "on_op" => result_frames(b.on_op(arg(a, 0).as_str().unwrap_or("")).map_err(|_| "ValueError".to_string())?),
        "close" => {
            b.close();
            Json::Null
        }
        "set_geometry" => {
            b.set_geometry(geometry(arg(a, 0)));
            Json::Null
        }
        "set_help_geometry" => {
            b.set_help_geometry(help_geometry(arg(a, 0)));
            Json::Null
        }
        "set_roll_geometry" => {
            let tilts = match arg(a, 0) {
                Json::Array(t) => t.iter().map(|t| (*t != Json::Null).then(|| int(t))).collect(),
                _ => Vec::new(),
            };
            b.set_roll_geometry(tilts);
            Json::Null
        }
        "set_maximize_geometry" => {
            b.set_maximize_geometry(arg(a, 0)).map_err(|_| "ValueError".to_string())?;
            Json::Null
        }
        "super_down" => result_frames(b.super_down()),
        "summon" => {
            b.summon();
            Json::Null
        }
        "drain" => {
            b.drain();
            Json::Null
        }
        "overview" => Json::Bool(b.overview()),
        "active" => Json::Bool(b.active()),
        "level" => bytes(&b.level(int(arg(a, 0)))),
        "desktops_changed" => {
            b.desktops_changed();
            Json::Null
        }
        "feed_shape" => {
            let serial = match (a.get(1), k.get("serial")) {
                (Some(v), _) | (None, Some(v)) if *v != Json::Null => Some(int(v)),
                _ => None,
            };
            b.feed_shape(arg(a, 0).as_str(), serial);
            Json::Null
        }
        "push_shape" => {
            b.push_shape(arg(a, 0).as_str());
            Json::Null
        }
        _ => return Err(format!("no such method {m}")),
    })
}

fn set_external(p: &mut OverviewPointer<Replay>, k: &str, v: &Json) {
    match k {
        "window_drag" => p.window_drag = truthy(v),
        "binds_dropped" => p.binds_dropped = truthy(v),
        "rolls" => {
            p.rolls = match v {
                Json::Array(r) => r.iter().map(int).collect(),
                _ => Vec::new(),
            }
        }
        "roll_names" => {
            p.roll_names = match v {
                Json::Array(r) => r.iter().map(|n| n.as_str().unwrap().to_string()).collect(),
                _ => Vec::new(),
            }
        }
        _ => panic!("pointer field {k}"),
    }
}

/// `replay(case)`: Err at the first difference, as "step n method: what differed".
fn replay(case: &Json) -> Result<(), String> {
    let init = get(case, "init");
    let control = Replay::new(init);
    let clock = Arc::new(Mutex::new(ClockState::default()));
    let reads: VecDeque<f64> = match init.get("ck") {
        Some(Json::Array(r)) => r.iter().map(num).collect(),
        _ => VecDeque::new(),
    };
    clock.lock().unwrap().reads = (!reads.is_empty()).then_some(reads);
    let boxed = Box::new(ReplayClock(Arc::clone(&clock)));
    let mut p = match get(init, "class").as_str() {
        Some("OverviewPointer") => Pointer::Overview(OverviewPointer::new(control, boxed).expect("a wake fd")),
        Some("UnifiedPointer") => Pointer::Unified(UnifiedPointer::new(control, boxed).expect("a wake fd")),
        c => return Err(format!("class {c:?}")),
    };
    let check_probe = |p: &Pointer, want: &Json, at: &str| -> Result<(), String> {
        for (k, v) in want.as_object().unwrap_or(&[]) {
            let got = probe(p.base_ref(), k);
            if !same(v, &got) {
                return Err(format!("{at}: pointer.{k} {got:?} != {v:?}"));
            }
        }
        Ok(())
    };
    check_probe(&p, get(init, "probe"), "init")?;
    let steps = match get(case, "steps") {
        Json::Array(s) => s.as_slice(),
        _ => &[],
    };
    for (n, step) in steps.iter().enumerate() {
        let m = get(step, "m").as_str().unwrap_or("");
        let at = format!("step {n} {m}");
        {
            let mut c = clock.lock().unwrap();
            c.now = num(get(step, "t"));
            c.reads = match step.get("ck") {
                Some(Json::Array(r)) => Some(r.iter().map(num).collect()),
                _ => None,
            };
        }
        let b = p.base();
        b.control.apply_state(get(step, "s"));
        b.control.apply_attrs(get(step, "c"));
        for (k, v) in get(step, "pf").as_object().unwrap_or(&[]) {
            set_external(b, k, v);
        }
        b.control.calls = Some(match step.get("calls") {
            Some(Json::Array(c)) => c.iter().cloned().collect(),
            _ => VecDeque::new(),
        });
        let args = match get(step, "a") {
            Json::Array(a) => a.clone(),
            _ => Vec::new(),
        };
        let result = call(&mut p, m, &args, get(step, "k"));
        let b = p.base();
        if let Some(e) = b.control.err.take() {
            return Err(format!("{at}: {e}"));
        }
        let result = match result {
            Err(e) if step.get("raise").and_then(Json::as_str) == Some(e.as_str()) => continue,
            Err(e) => return Err(format!("{at}: raised {e}")),
            Ok(r) => r,
        };
        if let Some(r) = step.get("raise") {
            return Err(format!("{at}: did not raise {r:?}"));
        }
        if let Some(left) = b.control.calls.take().filter(|c| !c.is_empty()) {
            let names: Vec<&str> = left.iter().map(|c| get(c, "n").as_str().unwrap_or("")).collect();
            return Err(format!("{at}: missing calls {names:?}"));
        }
        if !same(get(step, "r"), &result) {
            return Err(format!("{at}: result {result:?} != {:?}", get(step, "r")));
        }
        b.control.check(&at, step.get("post_s"), step.get("post_c"));
        if let Some(e) = b.control.err.take() {
            return Err(e);
        }
        check_probe(&p, get(step, "p"), &at)?;
    }
    Ok(())
}

fn corpus() -> Vec<(String, Json)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus/pointer");
    let mut files: Vec<_> = std::fs::read_dir(&dir)
        .expect("the corpus")
        .map(|e| e.expect("an entry").path())
        .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("traces-")))
        .collect();
    files.sort();
    let mut out = Vec::new();
    for f in files {
        let text = std::fs::read_to_string(&f).expect("a trace file");
        let Json::Array(cases) = Json::parse(&text).expect("trace JSON") else { panic!("{f:?}: not a list") };
        for c in cases {
            out.push((get(&c, "test").as_str().unwrap_or("?").to_string(), c));
        }
    }
    out
}

fn class(case: &Json) -> &str {
    get(get(case, "init"), "class").as_str().unwrap_or("?")
}

#[test]
fn every_recorded_trace_replays() {
    let cases = corpus();
    assert_eq!(cases.len(), 334, "the corpus's traces");
    let mut passed: BTreeMap<&str, (usize, usize)> = BTreeMap::new();
    let mut failures = Vec::new();
    for (name, case) in &cases {
        let e = passed.entry(class(case)).or_default();
        e.1 += 1;
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| replay(case))) {
            Ok(Ok(())) => e.0 += 1,
            Ok(Err(why)) => failures.push(format!("{name}: {why}")),
            Err(p) => {
                let why = p.downcast_ref::<String>().cloned().or_else(|| p.downcast_ref::<&str>().map(|s| s.to_string()));
                failures.push(format!("{name}: panicked: {}", why.unwrap_or_default()))
            }
        }
    }
    for (class, (ok, all)) in &passed {
        eprintln!("{class}: {ok}/{all}");
    }
    for f in failures.iter().take(40) {
        eprintln!("FAIL {f}");
    }
    assert!(failures.is_empty(), "{} traces differ", failures.len());
    assert_eq!(passed.get("OverviewPointer"), Some(&(156, 156)));
    assert_eq!(passed.get("UnifiedPointer"), Some(&(178, 178)));
}

#[test]
fn a_changed_frame_byte_fails_the_replay() {
    let cases = corpus();
    let (name, mut case) = cases
        .into_iter()
        .find(|(_, c)| class(c) == "OverviewPointer" && replay(c).is_ok())
        .expect("a passing trace");
    // the first frame the pointer itself sends (a poll's), one byte changed
    let Json::Object(top) = &mut case else { panic!() };
    let steps = top.iter_mut().find(|(k, _)| k == "steps").map(|(_, v)| v).unwrap();
    let Json::Array(steps) = steps else { panic!() };
    let step = steps
        .iter_mut()
        .find(|s| {
            get(s, "m").as_str() == Some("poll") && get(get(s, "r"), "$b").as_str().is_some_and(|b| b.contains("\"x\":"))
        })
        .expect("a poll with a frame");
    let Json::Object(fields) = step else { panic!() };
    let r = fields.iter_mut().find(|(k, _)| k == "r").map(|(_, v)| v).unwrap();
    let Json::Object(r) = r else { panic!() };
    let Json::Str(text) = &mut r[0].1 else { panic!() };
    let at = text.find("\"x\":").unwrap() + 4;
    let mut b = text.clone().into_bytes();
    b[at] = if b[at] == b'9' { b'8' } else { b'9' };
    *text = String::from_utf8(b).unwrap();
    let e = replay(&case).expect_err(&format!("{name}: a changed frame byte must fail"));
    assert!(e.contains("result"), "{e}");
}
