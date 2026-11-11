//! The gate for the desktop manager (`view_controls::desktops::Manager`, VIEW_CONTROLS.md `desktops`, level 1): every
//! scenario in rust/corpus/desktops/manager.json (recorded from the Python `desktops.DesktopManager` by
//! tests/native_spike/desktops_corpus.py) runs against the same scripted Hyprland and ownership plugin (that file's
//! `serve`, a copy of the Python executable named `Hyprland`), and sends the same requests in the same order, ends
//! each action with the same outcome, leaves the same desktops, counts and journal, and guards the same outputs.
//!
//! Below the replay: the worker's own timing, which a corpus cannot show (test_desktops.ManagerTests): a change in
//! flight makes the manager busy, a second start is refused, and `close` lets the step in flight land and runs no
//! later one.

use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use xr_wire::plugin::OutputSpec;
use xr_workspace::desktops::Action;
use xr_workspace::geometry::DEFAULT_PLACEMENT;
use xr_workspace::hyprland::Client;
use xr_workspace::journal::Journal;
use xr_workspace::ownership::Broker;
use xr_workspace::view_controls::desktops::{Env, Manager, window_mover};

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/desktops/manager.json"
));

fn script() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/native_spike/desktops_corpus.py")
        .canonicalize()
        .unwrap()
}

/// `serve` in a private runtime directory, as a child named Hyprland.
struct Fake {
    root: PathBuf,
    child: Child,
    seen: usize,
}

impl Fake {
    fn start(name: &str, case: &str, windows: &Value) -> Fake {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let root = base.join(format!("xr-desktops-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        for d in [root.clone(), root.join("hypr"), root.join("journal")] {
            fs::create_dir_all(&d).unwrap();
            fs::set_permissions(&d, fs::Permissions::from_mode(0o700)).unwrap();
        }
        // Copied by a child process: an in-process write races sibling forks into ETXTBSY.
        let hyprland = root.join("Hyprland");
        let copied = Command::new("cp")
            .arg("/usr/bin/python3")
            .arg(&hyprland)
            .status()
            .unwrap();
        assert!(copied.success());
        let child = Command::new(&hyprland)
            .arg("-I")
            .arg(script())
            .arg("serve")
            .arg(&root)
            .arg(case)
            .arg(windows.to_string())
            .env("TMPDIR", "/tmp")
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while !root.join("fake-ready").exists() {
            assert!(
                Instant::now() < deadline,
                "the fake compositor did not start"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        Fake {
            root,
            child,
            seen: 0,
        }
    }

    /// The requests since the last call.
    fn requests(&mut self) -> Vec<String> {
        let text = fs::read_to_string(self.root.join("wire.jsonl")).unwrap_or_default();
        let lines: Vec<&str> = text.lines().collect();
        let out = lines[self.seen..]
            .iter()
            .map(|l| {
                let v: Value = serde_json::from_str(l).unwrap();
                v["request"].as_str().unwrap().to_owned()
            })
            .collect();
        self.seen = lines.len();
        out
    }
}

impl Drop for Fake {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = fs::remove_dir_all(&self.root);
    }
}

/// UUIDs and the session hex in `text`, named by first appearance (desktops_corpus.normalize).
fn normalize(text: &str, names: &mut HashMap<String, String>) -> String {
    let chars: Vec<char> = text.chars().collect();
    let hex = |c: char| c.is_ascii_digit() || ('a'..='f').contains(&c);
    let (mut out, mut i) = (String::new(), 0);
    while i < chars.len() {
        let uuid = i + 36 <= chars.len()
            && chars[i..i + 36].iter().enumerate().all(|(k, &c)| match k {
                8 | 13 | 18 | 23 => c == '-',
                _ => hex(c),
            });
        let session = !uuid && i + 64 <= chars.len() && chars[i..i + 64].iter().all(|&c| hex(c));
        let len = if uuid {
            36
        } else if session {
            64
        } else {
            0
        };
        if len == 0 {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        let found: String = chars[i..i + len].iter().collect();
        let n = names.len();
        let name = names.entry(found).or_insert_with(|| {
            if len == 64 {
                "SESSION".into()
            } else {
                format!("U{n}")
            }
        });
        out.push_str(name);
        i += len;
    }
    out
}

fn output_spec(v: &Value) -> OutputSpec {
    OutputSpec {
        id: v["id"].as_str().unwrap().into(),
        name: v["name"].as_str().unwrap().into(),
        index: v["index"].as_i64().unwrap(),
        width: v["width"].as_i64().unwrap(),
        height: v["height"].as_i64().unwrap(),
        refresh_millihz: v["refresh_millihz"].as_i64().unwrap(),
        scale: v["scale"].as_f64().unwrap(),
        x: v["x"].as_i64().unwrap(),
        y: v["y"].as_i64().unwrap(),
    }
}

fn spec_value(s: &OutputSpec) -> Value {
    json!({"id": s.id, "name": s.name, "index": s.index, "width": s.width, "height": s.height,
           "refresh_millihz": s.refresh_millihz, "scale": s.scale, "x": s.x, "y": s.y})
}

fn action(v: &Value) -> (Action, usize) {
    let index = v[1].as_u64().unwrap() as usize;
    let action = match v[0].as_str().unwrap() {
        "add" => Action::Add,
        "remove" => Action::Remove,
        "ultrawide" => Action::Ultrawide,
        "scale" => Action::Scale(v[2].as_f64().unwrap()),
        "resolution" => Action::Resolution(v[2][0].as_i64().unwrap(), v[2][1].as_i64().unwrap()),
        "reset" => Action::Reset,
        other => panic!("unknown action {other}"),
    };
    (action, index)
}

/// A run's start on the fake: the client verified, the broker's hello, the first desktops created; the manager.
fn open(fake: &Fake, first: &[OutputSpec]) -> Manager {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let (instance, run_id, fallback) = (
        corpus["instance"].as_str().unwrap(),
        corpus["run_id"].as_str().unwrap(),
        corpus["fallback"].as_str().unwrap(),
    );
    let mut client = Client::new(instance, &fake.root).unwrap();
    let session = client.verified_session().unwrap();
    let journal = fake.root.join("journal").join("journal.json");
    let mut broker = Broker::new(run_id, &session, fallback).unwrap();
    let mut j = Journal::open(&journal, run_id, &session, false).unwrap();
    broker.require_output_ownership(&mut client).unwrap();
    broker.create_journaled(&mut client, first, &mut j).unwrap();
    let env = Env {
        broker: Arc::new(Mutex::new(broker)),
        journal,
        run_id: run_id.into(),
        session,
        instance: instance.into(),
        runtime: fake.root.clone(),
        identity: client.identity().cloned().unwrap(),
        guard: Box::new(|live: &[OutputSpec]| {
            let mut c = Command::new("guard");
            c.args(live.iter().map(|s| s.name.as_str()));
            Some(c)
        }),
    };
    let mut manager = Manager::open(first, env).unwrap();
    manager.set_move_windows(Some(window_mover()));
    manager
}

fn replay(scenario: &Value) {
    let name = scenario["name"].as_str().unwrap();
    let mut fake = Fake::start(
        name,
        scenario["case"].as_str().unwrap(),
        &scenario["windows"],
    );
    let first: Vec<OutputSpec> = scenario["first"]
        .as_array()
        .unwrap()
        .iter()
        .map(output_spec)
        .collect();
    let mut manager = open(&fake, &first);
    let mut names = HashMap::new();
    for r in fake.requests() {
        normalize(&r, &mut names);
    }
    for (k, step) in scenario["steps"].as_array().unwrap().iter().enumerate() {
        let at = format!("{name}, step {k}");
        let (act, index) = action(&step["action"]);
        let placements = vec![DEFAULT_PLACEMENT; manager.names().len()];
        match manager.plan(&placements, act, index) {
            Err(e) => assert_eq!(step["plan_error"], json!(e), "{at}: the plan"),
            Ok(change) => {
                assert!(
                    step.get("plan_error").is_none(),
                    "{at}: planned, Python refused"
                );
                assert_eq!(step["started"], json!(manager.start(change, None)), "{at}");
                manager.join();
                let outcome = manager.take_result().expect("an outcome");
                assert_eq!(step["error"], json!(outcome.error), "{at}: the error");
                assert_eq!(step["done"], json!(outcome.done), "{at}: the steps done");
            }
        }
        let requests: Vec<String> = fake
            .requests()
            .iter()
            .map(|r| normalize(r, &mut names))
            .collect();
        let journal = fs::read_to_string(fake.root.join("journal/journal.json")).unwrap();
        assert_eq!(
            step["journal"].as_str().unwrap(),
            normalize(&journal, &mut names),
            "{at}: the journal"
        );
        let specs: Vec<Value> = manager.specs().unwrap().iter().map(spec_value).collect();
        let specs: Value =
            serde_json::from_str(&normalize(&Value::Array(specs).to_string(), &mut names)).unwrap();
        assert_eq!(step["desktops"], specs, "{at}: the desktops");
        let guard: Vec<String> = manager
            .guard()
            .unwrap()
            .get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        assert_eq!(step["guard"], json!(guard), "{at}: the guard");
        let c = manager.counts();
        assert_eq!(
            step["counts"],
            json!({"changes": c.changes, "failures": c.failures, "fitted": c.fitted, "fit_failures": c.fit_failures}),
            "{at}: the counts"
        );
        let want: Vec<&str> = step["requests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r.as_str().unwrap())
            .collect();
        assert_eq!(want, requests, "{at}: the requests");
    }
    manager.close();
}

#[test]
fn every_scenario_matches_the_python_manager() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 15);
    for scenario in scenarios {
        replay(scenario);
    }
}

#[test]
fn a_change_in_flight_is_busy_and_close_lets_it_land() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let three = &corpus["scenarios"][0];
    let mut fake = Fake::start("busy", "", &json!([]));
    let first: Vec<OutputSpec> = three["first"]
        .as_array()
        .unwrap()
        .iter()
        .map(output_spec)
        .collect();
    let mut manager = open(&fake, &first);
    fake.requests();
    let placements = vec![DEFAULT_PLACEMENT; 3];
    let change = manager.plan(&placements, Action::Add, 0).unwrap();
    assert!(manager.start(change.clone(), None));
    assert!(manager.busy(), "running, or done and not yet collected");
    let refused = Arc::new(Mutex::new(None));
    let r = Arc::clone(&refused);
    let done =
        Box::new(move |ok: bool, detail: &str| *r.lock().unwrap() = Some((ok, detail.to_owned())));
    assert!(!manager.start(change, Some(done)), "one change at a time");
    assert_eq!(
        *refused.lock().unwrap(),
        Some((false, "Output transaction busy or stopped".to_owned()))
    );
    manager.close(); // the step in flight lands; none starts after the stop
    let outcome = manager.take_result().expect("an outcome");
    match outcome.error.as_deref() {
        None => assert_eq!(manager.names(), ["XR-1", "XR-2", "XR-3", "XR-4"]),
        Some(e) => {
            assert!(e.starts_with("the session is ending; "), "{e}");
            assert_eq!(manager.names(), ["XR-1", "XR-2", "XR-3"]);
        }
    }
    let after = fake.requests().len();
    let n = manager.names().len();
    let change = manager
        .plan(&vec![DEFAULT_PLACEMENT; n], Action::Remove, n - 1)
        .unwrap();
    assert!(!manager.start(change, None), "nothing starts after close");
    std::thread::sleep(Duration::from_millis(50));
    assert_eq!(fake.requests().len(), 0, "{after} requests, then none");
}

// --- the view's side: desktop_action, _start_change, _desktops_done, layout_reset (test_glass_pills.py,
// test_layout_reset.py), through a stand-in for the view's port on the shipped ViewState -----------------------------

use xr_workspace::geometry::Placement;
use xr_workspace::supervisor::GuardChange;
use xr_workspace::supervisor::view::{
    DEFAULT_RESET_PLACEMENT, RESET_LEAVING_PLACEMENT, ViewOptions, ViewState,
};
use xr_workspace::view_controls::desktops::{RESET_ANIM_S, RESET_ARM_S, RESET_CONFIRM, View};

/// The view as the change flow sees it: frames as `{op, payload}` lines, toasts, and what else it was asked.
struct StandIn {
    state: ViewState,
    now: f64,
    next: u64,
    told: Vec<String>,
    rolls: Vec<i64>,
    calls: Vec<&'static str>,
}

impl StandIn {
    fn new(names: &[&str], placements: Vec<Placement>, spacing: i64) -> StandIn {
        let options = ViewOptions {
            spacing,
            placements,
            ..ViewOptions::default()
        };
        StandIn {
            state: ViewState::new(names, options).unwrap(),
            now: 100.0,
            next: 1,
            told: Vec::new(),
            rolls: vec![0; names.len()],
            calls: Vec::new(),
        }
    }

    fn line(op: &str, payload: Value) -> Vec<u8> {
        format!("{}\n", json!({"op": op, "payload": payload})).into_bytes()
    }
}

impl View for StandIn {
    fn state(&mut self) -> &mut ViewState {
        &mut self.state
    }
    fn now(&self) -> f64 {
        self.now
    }
    fn video_active(&self) -> bool {
        false
    }
    fn tell(&mut self, text: &str) {
        self.told.push(text.to_owned());
    }
    fn flush(&mut self) {}
    fn cancel_save(&mut self) {}
    fn sync_menu_binds(&mut self) {
        self.calls.push("menu_binds");
    }
    fn view_frame(&mut self) -> Vec<u8> {
        StandIn::line("view", json!({"spacing": self.state.spacing}))
    }
    fn placement_frame(&mut self) -> Vec<u8> {
        StandIn::line("placement", json!({"desktops": self.state.placements}))
    }
    fn menu_frame(&mut self) -> Vec<u8> {
        let s = &self.state;
        StandIn::line(
            "placement_menu",
            json!({"open": s.menu_open, "desktop": s.menu_desktop, "armed": s.menu_open && s.remove_armed,
                   "busy": s.desktops_busy}),
        )
    }
    fn next_id(&mut self) -> u64 {
        self.next += 1;
        self.next - 1
    }
    fn run_id(&self) -> &str {
        "run"
    }
    fn session(&self) -> &str {
        "session"
    }
    fn desktops_changed(&mut self) {
        self.calls.push("desktops_changed");
    }
    fn windows_moved(&mut self) {
        self.calls.push("windows_moved");
    }
    fn rolls(&self) -> Option<Vec<i64>> {
        Some(self.rolls.clone())
    }
    fn set_rolls(&mut self, rolls: &[i64]) {
        let n = rolls.len().min(self.rolls.len());
        self.rolls[..n].copy_from_slice(&rolls[..n]);
    }
    fn forget_rolls(&mut self) {}
    fn close_menu(&mut self) -> Vec<u8> {
        self.state.menu_open = false;
        self.menu_frame()
    }
    fn zoom_out(&mut self) -> Vec<u8> {
        self.state.zoomed = false;
        self.view_frame()
    }
    fn reset_store(
        &mut self,
        _: Option<&[xr_workspace::geometry::Desktop]>,
        _: &[Placement],
        _: &[i64],
        _: i64,
    ) {
        self.calls.push("reset_store");
    }
    fn save_settings(&mut self) {
        self.calls.push("save_settings");
    }
    fn save_setup(&mut self) {
        self.calls.push("save_setup");
    }
}

/// `{op, payload}` of each frame line (the desktops frame's own, and the stand-in's).
fn ops(bytes: &[u8]) -> Vec<(String, Value)> {
    String::from_utf8_lossy(bytes)
        .lines()
        .map(|l| {
            let v: Value = serde_json::from_str(l).unwrap();
            (v["op"].as_str().unwrap().to_owned(), v["payload"].clone())
        })
        .collect()
}

fn op_names(f: &[(String, Value)]) -> Vec<&str> {
    f.iter().map(|(o, _)| o.as_str()).collect()
}

fn guard_kind(g: Option<GuardChange>) -> Option<&'static str> {
    g.map(|g| match g {
        GuardChange::Pause => "pause",
        GuardChange::Replace(_) => "replace",
    })
}

fn first_desktops(sizes: &[(i64, i64, f64)]) -> Vec<OutputSpec> {
    let mut x = 3840;
    sizes
        .iter()
        .enumerate()
        .map(|(i, &(w, h, s))| {
            let spec = OutputSpec {
                id: format!("11111111-1111-4111-8111-11111111111{}", i + 1),
                name: format!("XR-{}", i + 1),
                index: i as i64,
                width: w,
                height: h,
                refresh_millihz: 120000,
                scale: s,
                x,
                y: 0,
            };
            x += (w as f64 / s) as i64;
            spec
        })
        .collect()
}

/// The worker done, then the view's poll that collects it.
fn landed(manager: &mut Manager, view: &mut StandIn) -> Vec<(String, Value)> {
    let deadline = Instant::now() + Duration::from_secs(20);
    while manager.busy() && Instant::now() < deadline {
        let out = manager.poll(view);
        if !out.is_empty() {
            return ops(&out);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    ops(&manager.poll(view))
}

#[test]
fn remove_asks_twice_then_the_renderer_lets_go_before_the_broker_removes_it() {
    let fake = Fake::start("pill-remove", "", &json!([]));
    let first = first_desktops(&[(1920, 1080, 1.0); 3]);
    let mut manager = open(&fake, &first);
    let mut view = StandIn::new(&["XR-1", "XR-2", "XR-3"], vec![DEFAULT_PLACEMENT; 3], 40);
    (view.state.wide, view.state.sizes) = (manager.wide(), Some(manager.desktops()));
    view.state.open_menu(1).unwrap();
    // The first press only arms it ("Remove?").
    let armed = ops(&manager.desktop_action(&mut view, 1, Action::Remove));
    assert_eq!(op_names(&armed), ["placement_menu"]);
    assert_eq!(armed[0].1["armed"], true);
    assert_eq!(guard_kind(manager.guard_change()), None);
    // The second: the renderer's new desktop set, the view, the panel busy; the guard paused.
    view.now += 1.0;
    let started = ops(&manager.desktop_action(&mut view, 1, Action::Remove));
    assert_eq!(
        op_names(&started),
        ["desktops", "view", "placement", "placement_menu"]
    );
    assert_eq!(started[0].1, json!({"outputs": ["XR-1", "XR-3"]}));
    assert_eq!(started[3].1["busy"], true);
    assert_eq!(guard_kind(manager.guard_change()), Some("pause"));
    let done = landed(&mut manager, &mut view);
    assert_eq!(
        op_names(&done),
        ["desktops", "view", "placement", "placement_menu"]
    );
    assert_eq!(
        done[0].1,
        json!({"outputs": ["XR-1", "XR-3"]}),
        "a slide keeps its capture"
    );
    assert_eq!(view.state.names, ["XR-1", "XR-3"]);
    assert!(!view.state.desktops_busy);
    assert!(
        !view.state.menu_open,
        "the removed desktop's panel collapsed"
    );
    let replaced = manager.guard_change();
    let Some(GuardChange::Replace(Some(guard))) = replaced else {
        panic!("the guard replaced: {replaced:?}");
    };
    let args: Vec<_> = guard
        .get_args()
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    assert_eq!(
        args,
        ["XR-1", "XR-3"],
        "the new guard holds the outputs as they are now"
    );
    assert!(view.told.is_empty());
    assert!(view.calls.contains(&"windows_moved"));
    manager.close();
}

#[test]
fn an_arm_older_than_four_seconds_asks_again() {
    let fake = Fake::start("pill-arm", "", &json!([]));
    let mut manager = open(&fake, &first_desktops(&[(1920, 1080, 1.0); 2]));
    let mut view = StandIn::new(&["XR-1", "XR-2"], vec![DEFAULT_PLACEMENT; 2], 40);
    view.state.open_menu(0).unwrap();
    manager.desktop_action(&mut view, 0, Action::Remove);
    view.now += 4.0;
    let again = ops(&manager.desktop_action(&mut view, 0, Action::Remove));
    assert_eq!(
        op_names(&again),
        ["placement_menu"],
        "too late: it only arms again"
    );
    // Left alone, the poll disarms it after REMOVE_ARM_S.
    view.now += 4.0;
    let disarmed = ops(&manager.poll(&mut view));
    assert_eq!(disarmed[0].1["armed"], false);
    manager.close();
}

#[test]
fn a_refused_create_says_why_and_the_guard_comes_back() {
    let fake = Fake::start("pill-refused", "create-fail", &json!([]));
    let mut manager = open(&fake, &first_desktops(&[(1920, 1080, 1.0); 3]));
    let mut view = StandIn::new(&["XR-1", "XR-2", "XR-3"], vec![DEFAULT_PLACEMENT; 3], 40);
    view.state.open_menu(0).unwrap();
    let started = ops(&manager.desktop_action(&mut view, 0, Action::Add));
    assert_eq!(op_names(&started), ["placement_menu"]);
    assert_eq!(guard_kind(manager.guard_change()), Some("pause"));
    let done = landed(&mut manager, &mut view);
    assert_eq!(done[0].1, json!({"outputs": ["XR-1", "XR-2", "XR-3"]}));
    assert_eq!(view.state.names, ["XR-1", "XR-2", "XR-3"]);
    assert_eq!(
        view.told,
        ["creation incomplete; recovery required for unverified outputs"]
    );
    assert_eq!(guard_kind(manager.guard_change()), Some("replace"));
    // Its journal record holds XR-4 until recovery: the next Add has no name to take.
    view.state.open_menu(0).unwrap();
    manager.desktop_action(&mut view, 0, Action::Add);
    assert_eq!(
        view.told.last().unwrap(),
        "no free desktop name until the session restarts"
    );
    manager.close();
}

#[test]
fn ctrl_alt_r_twice_eases_four_desktops_then_makes_them_the_default_three() {
    let windows = json!([{"address": "0x55aa04", "index": 3, "floating": false, "at": [9600, 0], "size": [1920, 1080]}]);
    let fake = Fake::start("reset", "", &windows);
    let first = first_desktops(&[
        (2560, 1080, 1.0),
        (1920, 1080, 1.0),
        (2560, 1440, 1.25),
        (2560, 1080, 1.0),
    ]);
    let mut manager = open(&fake, &first);
    let placed = Placement {
        x: 300,
        y: 900,
        distance: 110,
        curvature: 8,
    };
    let mut view = StandIn::new(&["XR-1", "XR-2", "XR-3", "XR-4"], vec![placed; 4], 120);
    view.rolls = vec![100, -200, 0, 50];
    view.state.open_menu(1).unwrap();
    // The first press only asks.
    assert!(manager.layout_reset(&mut view).is_empty());
    assert_eq!(view.told, [RESET_CONFIRM]);
    assert!(!manager.resetting());
    view.now += RESET_ARM_S - 0.01;
    let first_frames = ops(&manager.layout_reset(&mut view));
    assert_eq!(op_names(&first_frames), ["placement_menu", "view"]);
    assert_eq!(
        first_frames[0].1["open"], false,
        "the open panel collapsed first"
    );
    assert_eq!(
        first_frames[1].1["spacing"], 40,
        "the spacing back to its default"
    );
    assert!(view.calls.contains(&"reset_store") && view.calls.contains(&"save_settings"));
    assert!(view.state.desktops_busy);
    // Halfway: the fourth on its way down, from +90 up to -90 down: level at half.
    view.now += RESET_ANIM_S / 2.0;
    let half = ops(&manager.poll(&mut view));
    assert_eq!(op_names(&half), ["placement"]);
    assert_eq!(
        half[0].1["desktops"][3],
        json!({"x": 150, "y": 0, "distance": 155, "curvature": 4})
    );
    assert_eq!(
        guard_kind(manager.guard_change()),
        None,
        "the desktops wait for the ease"
    );
    // The ease ends: XR-4 lets go of its capture, then the broker steps run.
    view.now += RESET_ANIM_S;
    let end = ops(&manager.poll(&mut view));
    assert_eq!(
        end[0].1["desktops"],
        json!([
            DEFAULT_RESET_PLACEMENT,
            DEFAULT_RESET_PLACEMENT,
            DEFAULT_RESET_PLACEMENT,
            RESET_LEAVING_PLACEMENT
        ])
    );
    let desktops: Vec<&Value> = end
        .iter()
        .filter(|(o, _)| o == "desktops")
        .map(|(_, p)| p)
        .collect();
    assert_eq!(
        desktops,
        [&json!({"outputs": ["XR-1", "XR-2", "XR-3"], "ease": true})]
    );
    assert_eq!(guard_kind(manager.guard_change()), Some("pause"));
    let done = landed(&mut manager, &mut view);
    let desktops: Vec<&Value> = done
        .iter()
        .filter(|(o, _)| o == "desktops")
        .map(|(_, p)| p)
        .collect();
    assert_eq!(
        desktops,
        [&json!({"outputs": ["XR-1", "XR-2", "XR-3"], "renew": ["XR-1", "XR-3"], "ease": true})]
    );
    assert_eq!(manager.names(), ["XR-1", "XR-2", "XR-3"]);
    assert_eq!(view.state.placements, [DEFAULT_RESET_PLACEMENT; 3]);
    assert_eq!(view.rolls[..3], [0, 0, 0]);
    assert!(!view.state.desktops_busy && !manager.resetting());
    assert_eq!(guard_kind(manager.guard_change()), Some("replace"));
    assert_eq!(
        view.told.last().unwrap(),
        "All monitors reset to three flat 1920x1080 desktops at 100%. XR-4's windows are now on XR-3."
    );
    assert!(view.calls.contains(&"save_setup"));
    manager.close();
}
