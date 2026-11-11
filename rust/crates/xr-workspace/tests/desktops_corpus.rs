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
