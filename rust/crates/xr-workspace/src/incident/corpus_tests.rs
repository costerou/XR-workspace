//! The incident corpus (rust/corpus/incidents/scenarios.json, made by tests/xr_controller/incident_corpus.py from the
//! shipped Python monitor): each scenario's events replayed against the same fake host must leave the same report
//! files, byte for byte, the same .monitor-state, toasts, hook hand-offs and journal lines.

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::rc::Rc;
use std::sync::{Arc, Mutex};

use serde_json::{Map, Value, json};

use super::monitor::{Feed, IncidentMonitor};
use super::py::{as_int, as_str, get, splitlines};
use super::report::{Reports, Scrubber};
use super::sources::{Answer, Sources};
use crate::testdir::TestDir;

const CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../corpus/incidents/scenarios.json"
));
const REPORTS: &str = "<reports>";

/// The fake host of test_incident_monitor.py, over the corpus's JSON fields.
#[derive(Clone)]
struct Host(Rc<RefCell<Map<String, Value>>>);

impl Host {
    fn field(&self, name: &str) -> Value {
        self.0.borrow().get(name).cloned().unwrap_or(Value::Null)
    }

    fn maybe(&self, name: &str) -> Answer<()> {
        let broken = self.field("broken");
        if broken
            .as_array()
            .is_some_and(|b| b.iter().any(|n| n == name))
        {
            Err(format!("{name} broke"))
        } else {
            Ok(())
        }
    }

    fn lookup(&self, map: &str, key: &str) -> Value {
        get(&self.field(map), key).clone()
    }

    fn lines(&self, name: &str) -> Vec<String> {
        self.field(name)
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(as_str)
            .map(str::to_owned)
            .collect()
    }

    fn text(&self, name: &str) -> String {
        as_str(&self.field(name)).unwrap_or_default().to_owned()
    }
}

fn tail(lines: Vec<String>, n: usize) -> Vec<String> {
    let skip = lines.len().saturating_sub(n);
    lines.into_iter().skip(skip).collect()
}

impl Sources for Host {
    fn boot_id(&mut self) -> Answer<Option<String>> {
        Ok(as_str(&self.field("boot")).map(str::to_owned))
    }
    fn consent(&mut self, run_id: &str) -> Answer<Value> {
        self.maybe("consent")?;
        Ok(self.lookup("consents", run_id))
    }
    fn result(&mut self, run_id: &str) -> Answer<Value> {
        self.maybe("result")?;
        Ok(self.lookup("results", run_id))
    }
    fn recovery(&mut self, run_id: &str) -> Answer<Value> {
        self.maybe("recovery")?;
        Ok(self.lookup("recoveries", run_id))
    }
    fn kernel_start(&mut self) -> Answer<Option<String>> {
        self.maybe("kernel")?;
        Ok(Some(self.lines("kernel").len().to_string()))
    }
    fn kernel_read(&mut self, cursor: &str) -> Answer<(Vec<String>, String)> {
        self.maybe("kernel")?;
        let lines = self.lines("kernel");
        let from = cursor.parse::<usize>().unwrap_or(0).min(lines.len());
        Ok((lines[from..].to_vec(), lines.len().to_string()))
    }
    fn kernel_around(&mut self, _first: f64, _last: f64) -> Answer<Vec<String>> {
        self.maybe("kernel")?;
        Ok(self.lines("kernel"))
    }
    fn journal(&mut self, unit: &str, lines: usize) -> Answer<Vec<String>> {
        self.maybe("journal")?;
        let all = self
            .lookup("journals", unit)
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(as_str)
            .map(str::to_owned)
            .collect();
        Ok(tail(all, lines))
    }
    fn driver_start(&mut self) -> Answer<u64> {
        self.maybe("driver")?;
        Ok(self.text("driver").len() as u64)
    }
    fn driver_read(&mut self, offset: u64) -> Answer<(String, u64)> {
        self.maybe("driver")?;
        let text = self.text("driver");
        let from = (offset as usize).min(text.len());
        Ok((text[from..].to_owned(), text.len() as u64))
    }
    fn driver_tail(&mut self, lines: usize) -> Answer<Vec<String>> {
        self.maybe("driver")?;
        Ok(tail(splitlines(&self.text("driver")), lines))
    }
    fn drm(&mut self, _output: &str) -> Answer<Option<String>> {
        self.maybe("drm")?;
        Ok(as_str(&self.field("drm_state")).map(str::to_owned))
    }
    fn usb_ports(&mut self) -> Answer<Option<BTreeSet<String>>> {
        self.maybe("usb")?;
        Ok(Some(self.lines("ports").into_iter().collect()))
    }
    fn hyprland_pid(&mut self, _instance: Option<&str>) -> Answer<Option<i64>> {
        Ok(as_int(&self.field("hypr_pid")))
    }
    fn alive(&mut self, pid: Option<i64>) -> Answer<Option<bool>> {
        Ok((pid == as_int(&self.field("hypr_pid")))
            .then(|| self.field("hypr_up").as_bool())
            .flatten())
    }
    fn driver_alive(&mut self) -> Answer<bool> {
        Ok(self.field("driver_up").as_bool().unwrap_or(false))
    }
    fn tracking_driver(&mut self) -> Answer<Value> {
        Ok("luma".into())
    }
    fn core_dumps(&mut self, _since: f64, exes: &[String], pids: &[i64]) -> Answer<Vec<Value>> {
        self.maybe("core")?;
        let cores = self.field("cores");
        Ok(cores
            .as_array()
            .into_iter()
            .flatten()
            .filter(|c| {
                as_str(get(c, "exe_path")).is_some_and(|e| exes.iter().any(|x| x == e))
                    || as_int(get(c, "pid")).is_some_and(|p| pids.contains(&p))
            })
            .cloned()
            .collect())
    }
    fn source(&mut self) -> Answer<Value> {
        Ok(json!({"sha": format!("13c8d5e{}", "0".repeat(33)), "dirty": false}))
    }
}

fn initial_host() -> Map<String, Value> {
    let pin = "/home/testuser/.local/share/viture-workspace/bin/f0de154899b9/";
    let (run, other) = (
        "2298c7a18df736b9b6c4bde5b5877031",
        "549bcebd15b354c2c168fabd58cc1e0c",
    );
    let consent = |run_id: &str| {
        json!({"run_id": run_id, "instance": "sig", "output": "DP-2", "recovery_output": "DP-1", "tracking": "xrlinuxdriver",
               "model": "luma-ultra", "renderer": {"path": format!("{pin}xr-native-spike"), "sha256": "a".repeat(64)},
               "plugin": {"path": format!("{pin}libxr-ownership.so"), "sha256": "b".repeat(64)}})
    };
    let host = json!({"boot": "boot-1", "consents": {run: consent(run), other: consent(other)}, "results": {}, "recoveries": {},
                      "kernel": [], "driver": "", "drm_state": "connected", "ports": ["5-2.1", "5-2.3"], "hypr_pid": 1690,
                      "hypr_up": true, "driver_up": true, "cores": [], "journals": {}, "broken": []});
    host.as_object().cloned().unwrap()
}

#[derive(Debug, PartialEq)]
struct Outcome {
    files: Map<String, Value>,
    toasts: Vec<String>,
    handed: Vec<String>,
    log: Vec<String>,
}

fn replay(events: &[Value]) -> Outcome {
    let dir = TestDir::new("incident-corpus");
    let directory = dir.path().join("incidents");
    let host = Host(Rc::new(RefCell::new(initial_host())));
    let toasts = Rc::new(RefCell::new(Vec::new()));
    let handed = Rc::new(RefCell::new(Vec::new()));
    let said = Arc::new(Mutex::new(Vec::<String>::new()));
    let fresh = || {
        let feed = Feed::new(Arc::new(|| 0.0));
        let log = said.clone();
        let (toasts, handed) = (toasts.clone(), handed.clone());
        let monitor = IncidentMonitor::new(
            Box::new(host.clone()),
            Reports::new(&directory),
            feed.clone(),
            Scrubber::new("/home/testuser", "testuser", "testhost"),
            Arc::new(move |line: &str| log.lock().unwrap().push(line.to_owned())),
        )
        .with_notify(Box::new(move |text| {
            toasts.borrow_mut().push(text.to_owned());
            Ok(())
        }))
        .with_hand_on(Box::new(move |path| {
            handed
                .borrow_mut()
                .push(path.file_name().unwrap().to_string_lossy().into_owned());
        }));
        (feed, monitor)
    };
    let (mut feed, mut monitor) = fresh();
    for event in events {
        match as_str(get(event, "op")) {
            Some("set") => {
                host.0.borrow_mut().insert(
                    as_str(get(event, "field")).unwrap().to_owned(),
                    get(event, "value").clone(),
                );
            }
            Some("fresh") => (feed, monitor) = fresh(),
            Some("observe") => feed.observe(get(event, "observation")),
            Some("step") => monitor.step(get(event, "now").as_f64().unwrap()),
            op => panic!("unknown corpus event {op:?}"),
        }
    }
    let mut files = Map::new();
    if directory.exists() {
        let mut names: Vec<_> = std::fs::read_dir(&directory)
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect();
        names.sort();
        for path in names {
            let text = std::fs::read_to_string(&path)
                .unwrap()
                .replace(&directory.to_string_lossy().into_owned(), REPORTS);
            files.insert(
                path.file_name().unwrap().to_string_lossy().into_owned(),
                text.into(),
            );
        }
    }
    let log = said
        .lock()
        .unwrap()
        .iter()
        .filter(|l| l.starts_with("incident:") || l.starts_with("mode-switch:"))
        .cloned()
        .collect();
    Outcome {
        files,
        toasts: toasts.take(),
        handed: handed.take(),
        log,
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn every_scenario_leaves_what_the_python_monitor_leaves() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let scenarios = corpus["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 12);
    let mut failed = Vec::new();
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let expect = &scenario["expect"];
        let want = Outcome {
            files: expect["files"].as_object().unwrap().clone(),
            toasts: strings(&expect["toasts"]),
            handed: strings(&expect["handed"]),
            log: strings(&expect["log"]),
        };
        let got = replay(scenario["events"].as_array().unwrap());
        if got != want {
            for (file, text) in &want.files {
                if got.files.get(file) != Some(text) {
                    eprintln!(
                        "--- {name}: {file}\nwant:\n{}\ngot:\n{}",
                        text.as_str().unwrap(),
                        got.files
                            .get(file)
                            .and_then(Value::as_str)
                            .unwrap_or("<missing>")
                    );
                }
            }
            eprintln!(
                "{name}: files {:?} vs {:?}",
                want.files.keys().collect::<Vec<_>>(),
                got.files.keys().collect::<Vec<_>>()
            );
            eprintln!(
                "{name}: toasts {:?} vs {:?}\nhanded {:?} vs {:?}\nlog {:?} vs {:?}",
                want.toasts, got.toasts, want.handed, got.handed, want.log, got.log
            );
            failed.push(name.to_owned());
        }
    }
    assert!(
        failed.is_empty(),
        "scenarios that differ from the Python monitor: {failed:?}"
    );
}

#[test]
fn the_gate_scenario_is_an_injected_renderer_crash_with_its_core_dump() {
    let corpus: Value = serde_json::from_str(CORPUS).unwrap();
    let crash = corpus["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["name"] == "renderer_crash")
        .unwrap();
    let report = crash["expect"]["files"]
        .as_object()
        .unwrap()
        .iter()
        .find(|(n, _)| n.ends_with(".json") && !n.starts_with('.'))
        .unwrap()
        .1;
    let report: Value = serde_json::from_str(report.as_str().unwrap()).unwrap();
    assert_eq!(report["kind"], "renderer-crash");
    assert_eq!(report["core_dumps"][0]["signal"], "SIGSEGV");
}
