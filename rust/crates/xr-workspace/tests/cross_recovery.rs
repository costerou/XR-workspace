//! The phase 6 journal gate: Rust recovers Python-started runs and Python recovers Rust-started runs, across every
//! journal fixture and fault-injection point in `rust/corpus/journal/cases.json` (made by
//! `tests/native_spike/journal_corpus.py`, which is also the live Python oracle used here).

use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use xr_workspace::journal::{Error, Journal, ManagedOwner, Observed, Operation};
use xr_workspace::recovery::{BackendError, OutputView, RecoveryBackend, WorkspaceView, recover};

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap()
}

fn corpus() -> Value {
    let raw = std::fs::read(repo().join("rust/corpus/journal/cases.json")).unwrap();
    serde_json::from_slice(&raw).unwrap()
}

/// A private (0700) scratch directory, removed on drop.
struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Scratch {
        static N: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "xr-journal-rs-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&path)
            .unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        Scratch(path)
    }
    fn journal(&self) -> PathBuf {
        self.0.join("journal.json")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::set_permissions(&self.0, std::fs::Permissions::from_mode(0o700));
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn write_private(path: &Path, raw: &[u8]) {
    use std::os::unix::fs::OpenOptionsExt;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .unwrap();
    file.write_all(raw).unwrap();
}

// ---- The scripted compositor, call for call the Python `journal_corpus.Compositor` ----

struct Compositor {
    state: Value,
    fault: Option<(usize, String)>,
    calls: Vec<String>,
    refusal: Option<String>,
}

enum Effect {
    None,
    Session,
    Refused,
    Ignored,
}

impl Compositor {
    fn new(state: &Value, fault: &Value) -> Compositor {
        let fault = fault.as_object().map(|f| {
            (
                f["at"].as_u64().unwrap() as usize,
                f["kind"].as_str().unwrap().into(),
            )
        });
        Compositor {
            state: state.clone(),
            fault,
            calls: Vec::new(),
            refusal: None,
        }
    }

    fn call(&mut self, kind: &str, text: String) -> Result<Effect, BackendError> {
        let n = self.calls.len();
        self.calls.push(text);
        let Some((at, fault)) = &self.fault else {
            return Ok(Effect::None);
        };
        if n < *at {
            return Ok(Effect::None);
        }
        match fault.as_str() {
            "disconnect" => Err(BackendError("compositor disconnected".into())),
            "session" => Ok(Effect::Session),
            _ if n != *at => Ok(Effect::None),
            "error" => Err(BackendError("injected fault".into())),
            "refused" if kind == "mutate" => Ok(Effect::Refused),
            "ignored" if kind == "mutate" || kind == "evacuate_remove" => Ok(Effect::Ignored),
            _ => Ok(Effect::None),
        }
    }

    fn query(&mut self, kind: &str) -> Result<Value, BackendError> {
        self.call("query", format!("query {kind}"))?;
        Ok(self.state[kind].clone())
    }
}

impl RecoveryBackend for Compositor {
    fn session(&mut self) -> Result<String, BackendError> {
        let effect = self.call("query", "query session".into())?;
        Ok(match effect {
            Effect::Session => "other-session".into(),
            _ => self.state["session"].as_str().unwrap().into(),
        })
    }
    fn outputs(&mut self) -> Result<Vec<OutputView>, BackendError> {
        Ok(self
            .query("outputs")?
            .as_array()
            .unwrap()
            .iter()
            .map(|o| OutputView {
                id: o["id"].as_str().unwrap().into(),
                name: o["name"].as_str().unwrap().into(),
                physical: o["physical"].as_bool().unwrap(),
            })
            .collect())
    }
    fn workspaces(&mut self) -> Result<Vec<WorkspaceView>, BackendError> {
        Ok(self
            .query("workspaces")?
            .as_array()
            .unwrap()
            .iter()
            .map(|w| WorkspaceView {
                id: w["id"].as_i64().unwrap(),
                output: w["output"].as_str().unwrap().into(),
            })
            .collect())
    }
    fn settings(&mut self) -> Result<BTreeMap<String, i64>, BackendError> {
        Ok(self
            .query("settings")?
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_i64().unwrap()))
            .collect())
    }
    fn mutate(&mut self, command: &str) -> Result<String, BackendError> {
        match self.call("mutate", format!("mutate {command}"))? {
            Effect::Refused => return Ok("refused".into()),
            Effect::Ignored => return Ok("ok".into()),
            _ => {}
        }
        let fields: Vec<&str> = command.split_whitespace().collect();
        match fields.as_slice() {
            ["keyword", key, value] => {
                self.state["settings"][*key] = value.parse::<i64>().unwrap().into();
            }
            ["dispatch", "moveworkspacetomonitor", ws, monitor] => {
                let ws: i64 = ws.parse().unwrap();
                for w in self.state["workspaces"].as_array_mut().unwrap() {
                    if w["id"] == ws {
                        w["output"] = (*monitor).into();
                    }
                }
            }
            _ => return Ok("unknown command".into()),
        }
        Ok("ok".into())
    }
    fn evacuate_remove(&mut self, id: &str, name: &str) -> Result<bool, BackendError> {
        self.refusal = None;
        if let Effect::Ignored =
            self.call("evacuate_remove", format!("evacuate_remove {id} {name}"))?
        {
            self.refusal = Some("injected refusal".into());
            return Ok(false);
        }
        let outputs = self.state["outputs"].as_array().unwrap();
        let Some(found) = outputs.iter().position(|o| o["id"] == id) else {
            return Ok(true);
        };
        if outputs[found]["name"] != name || outputs[found]["physical"] != false {
            self.refusal = Some("output name/UUID mismatch".into());
            return Ok(false);
        }
        let Some(fallback) = outputs
            .iter()
            .find(|o| o["physical"] == true)
            .map(|o| o["name"].clone())
        else {
            self.refusal = Some("no physical output can take its windows".into());
            return Ok(false);
        };
        for w in self.state["workspaces"].as_array_mut().unwrap() {
            if w["output"] == name {
                w["output"] = fallback.clone();
            }
        }
        self.state["outputs"].as_array_mut().unwrap().remove(found);
        Ok(true)
    }
    fn refusal(&self) -> Option<String> {
        self.refusal.clone()
    }
}

/// The Rust twin of `journal_corpus.recover_one`.
fn recover_one(request: &Value) -> Value {
    let dir = Scratch::new();
    write_private(
        &dir.journal(),
        request["journal"].as_str().unwrap().as_bytes(),
    );
    let mut compositor = Compositor::new(&request["compositor"], &request["fault"]);
    let result = Journal::open(
        dir.journal(),
        request["run_id"].as_str().unwrap(),
        request["session"].as_str().unwrap(),
        true,
    )
    .and_then(|mut journal| recover(&mut journal, &mut compositor));
    match result {
        Err(e) => json!({"error": e.to_string(), "calls": compositor.calls}),
        Ok(r) => json!({
            "complete": r.complete, "remaining": r.remaining, "reasons": r.reasons,
            "journal": std::fs::read_to_string(dir.journal()).unwrap(),
            "compositor": compositor.state, "calls": compositor.calls,
        }),
    }
}

fn request(scenario: &Value, journal: &Value, compositor: &Value, fault: &Value) -> Value {
    json!({"run_id": scenario["run_id"], "session": scenario["session"], "journal": journal,
           "compositor": compositor, "fault": fault})
}

/// Each case's first recovery, then a clean retry on what the first left, as `journal_corpus.scenario_cases` does.
fn cases(
    scenario: &Value,
    journal: &Value,
    recover: impl Fn(&Value) -> Value,
) -> Vec<(Value, Value, Value)> {
    scenario["cases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| {
            let first = recover(&request(
                scenario,
                journal,
                &scenario["compositor"],
                &case["fault"],
            ));
            let retry = recover(&request(
                scenario,
                &first["journal"],
                &first["compositor"],
                &Value::Null,
            ));
            (case["fault"].clone(), first, retry)
        })
        .collect()
}

/// The Rust supervisor writing a run: `journal_corpus.build` through the Rust API. Returns the file and the ids.
fn build(scenario: &Value) -> (String, Vec<String>) {
    let dir = Scratch::new();
    let mut journal = Journal::open(
        dir.journal(),
        scenario["run_id"].as_str().unwrap(),
        scenario["session"].as_str().unwrap(),
        false,
    )
    .unwrap();
    let mut ids = Vec::new();
    for step in scenario["steps"].as_array().unwrap() {
        let result = match step["call"].as_str().unwrap() {
            "bind_broker" => journal.bind_broker(&step["broker"]),
            "prepare" => Operation::from_value(&step["operation"])
                .and_then(|op| journal.prepare(&op))
                .map(|id| ids.push(id)),
            "confirm" => {
                let seen = &step["observed"];
                let observed = if seen.get("value").is_some() {
                    Observed::Setting {
                        value: seen["value"].as_i64().unwrap(),
                    }
                } else if seen.get("physical").is_some() {
                    Observed::Output {
                        id: seen["id"].as_str().unwrap().into(),
                        name: seen["name"].as_str().unwrap().into(),
                        physical: seen["physical"].as_bool().unwrap(),
                    }
                } else {
                    Observed::Workspace {
                        output: seen["output"].as_str().unwrap().into(),
                    }
                };
                journal.confirm(&ids[step["record"].as_u64().unwrap() as usize], &observed)
            }
            "output_removed" => journal.output_removed(step["id"].as_str().unwrap()),
            "compositor_ended" => journal.compositor_ended(),
            other => panic!("unknown step {other}"),
        };
        match (result, step.get("error")) {
            (Ok(()), None) => {}
            (Err(Error::Invalid(text)), Some(want)) => {
                assert_eq!(want, &Value::String(text), "{step}")
            }
            (result, want) => panic!("step {step}: {result:?}, wanted {want:?}"),
        }
    }
    (std::fs::read_to_string(dir.journal()).unwrap(), ids)
}

fn python(args: &[&str]) -> Command {
    let mut command = Command::new("/usr/bin/python3");
    command
        .arg("tests/native_spike/journal_corpus.py")
        .args(args)
        .current_dir(repo())
        .env("TMPDIR", "/tmp");
    command
}

/// `journal_corpus.py recover`: the shipped Python recovery on each request.
fn python_recover(requests: &[Value]) -> Vec<Value> {
    let mut child = python(&["recover"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(serde_json::to_string(requests).unwrap().as_bytes())
        .unwrap();
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success(), "python oracle failed");
    serde_json::from_slice(&out.stdout).unwrap()
}

#[test]
fn rust_recovers_python_runs() {
    let corpus = corpus();
    let mut checked = 0;
    for scenario in corpus["scenarios"].as_array().unwrap() {
        let name = &scenario["name"];
        let ran = cases(scenario, &scenario["journal"], recover_one);
        for (case, (fault, first, retry)) in scenario["cases"].as_array().unwrap().iter().zip(ran) {
            assert_eq!(
                first, case["first"],
                "{name}: first recovery, fault {fault}"
            );
            assert_eq!(retry, case["retry"], "{name}: retry, fault {fault}");
            checked += 1;
        }
    }
    for case in corpus["refused"].as_array().unwrap() {
        let request = json!({"run_id": case["run_id"], "session": case["session"], "journal": case["journal"],
                             "compositor": {}, "fault": null});
        assert_eq!(
            recover_one(&request),
            json!({"error": case["error"], "calls": []}),
            "refused {}",
            case["name"]
        );
        checked += 1;
    }
    assert!(checked > 300, "{checked} cases");
    eprintln!("rust recovered {checked} python cases");
}

#[test]
fn rust_writes_journals_as_python_does() {
    let corpus = corpus();
    for scenario in corpus["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| !s["steps"].is_null())
    {
        let (mut written, ids) = build(scenario);
        // The record ids are random; give them the Python's, by order.
        let python: Value = serde_json::from_str(scenario["journal"].as_str().unwrap()).unwrap();
        let theirs: Vec<&str> = python["operations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids.len(), theirs.len(), "{}", scenario["name"]);
        for (ours, theirs) in ids.iter().zip(theirs) {
            written = written.replace(ours.as_str(), theirs);
        }
        assert_eq!(
            written,
            scenario["journal"].as_str().unwrap(),
            "{}",
            scenario["name"]
        );
    }
}

#[test]
fn python_recovers_rust_runs() {
    let corpus = corpus();
    let mut requests = Vec::new();
    let mut expected = Vec::new();
    for scenario in corpus["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| !s["steps"].is_null())
    {
        let journal = Value::String(build(scenario).0);
        for (fault, first, retry) in cases(scenario, &journal, recover_one) {
            requests.push(request(scenario, &journal, &scenario["compositor"], &fault));
            expected.push(first.clone());
            requests.push(request(
                scenario,
                &first["journal"],
                &first["compositor"],
                &Value::Null,
            ));
            expected.push(retry);
        }
    }
    let got = python_recover(&requests);
    assert_eq!(got.len(), expected.len());
    for ((request, got), want) in requests.iter().zip(&got).zip(&expected) {
        assert_eq!(
            got, want,
            "python on rust journal, fault {}",
            request["fault"]
        );
    }
    eprintln!("python recovered {} rust-written cases", got.len());
}

#[test]
fn owner_lock_excludes_the_other_supervisor() {
    let dir = Scratch::new();
    let path = dir.journal();
    let path_text = path.to_str().unwrap();
    // Python holds it: Rust cannot take it, then can once Python lets go.
    let mut holder = python(&["hold-owner", path_text])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    BufReader::new(holder.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    assert_eq!(line.trim(), "held");
    match ManagedOwner::acquire(&path) {
        Err(Error::Io(e)) => assert_eq!(e.kind(), std::io::ErrorKind::WouldBlock),
        other => panic!("rust took a held owner lock: {:?}", other.err()),
    }
    drop(holder.stdin.take());
    assert!(holder.wait().unwrap().success());
    let owner = ManagedOwner::acquire(&path).unwrap();
    // Rust holds it: Python cannot take it, then can.
    let try_python =
        || String::from_utf8(python(&["try-owner", path_text]).output().unwrap().stdout).unwrap();
    assert_eq!(try_python().trim(), "busy");
    drop(owner);
    assert_eq!(try_python().trim(), "acquired");
}

#[test]
fn refuses_unsafe_files() {
    let dir = Scratch::new();
    assert!(matches!(
        Journal::open(dir.journal(), "run-1", "s-1", true),
        Err(Error::Missing)
    ));

    let big = format!(
        "{{\"schema\": 1, \"run_id\": \"run-1\", \"session\": \"s-1\", \"operations\": [], \"x\": \"{}\"}}",
        "a".repeat(1_048_576)
    );
    write_private(&dir.journal(), big.as_bytes());
    assert_eq!(
        Journal::open(dir.journal(), "run-1", "s-1", true)
            .err()
            .unwrap()
            .to_string(),
        "journal too large"
    );

    std::fs::set_permissions(dir.journal(), std::fs::Permissions::from_mode(0o644)).unwrap();
    let refused = Journal::open(dir.journal(), "run-1", "s-1", true)
        .err()
        .unwrap();
    assert_eq!(
        refused.to_string(),
        "journal/lock must be owned regular mode 0600 single-link file"
    );

    std::fs::remove_file(dir.journal()).unwrap();
    std::os::unix::fs::symlink("/etc/hostname", dir.journal()).unwrap();
    assert!(
        Journal::open(dir.journal(), "run-1", "s-1", true).is_err(),
        "followed a symlink"
    );

    std::fs::set_permissions(&dir.0, std::fs::Permissions::from_mode(0o755)).unwrap();
    let refused = Journal::open(dir.journal(), "run-1", "s-1", false)
        .err()
        .unwrap();
    assert_eq!(refused.to_string(), "directory must be owned and mode 0700");
}

/// A recovery that cannot write its progress fails; it never reports the desktop as restored.
#[test]
fn storage_failure_is_fatal() {
    struct ReadOnlyAfterRemove<'a>(Compositor, &'a Path);
    impl RecoveryBackend for ReadOnlyAfterRemove<'_> {
        fn session(&mut self) -> Result<String, BackendError> {
            self.0.session()
        }
        fn outputs(&mut self) -> Result<Vec<OutputView>, BackendError> {
            self.0.outputs()
        }
        fn workspaces(&mut self) -> Result<Vec<WorkspaceView>, BackendError> {
            self.0.workspaces()
        }
        fn settings(&mut self) -> Result<BTreeMap<String, i64>, BackendError> {
            self.0.settings()
        }
        fn mutate(&mut self, command: &str) -> Result<String, BackendError> {
            self.0.mutate(command)
        }
        fn evacuate_remove(&mut self, id: &str, name: &str) -> Result<bool, BackendError> {
            std::fs::set_permissions(self.1, std::fs::Permissions::from_mode(0o500)).unwrap();
            self.0.evacuate_remove(id, name)
        }
        fn refusal(&self) -> Option<String> {
            self.0.refusal()
        }
    }
    let corpus = corpus();
    let scenario = corpus["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["name"] == "output confirmed")
        .unwrap();
    let dir = Scratch::new();
    write_private(
        &dir.journal(),
        scenario["journal"].as_str().unwrap().as_bytes(),
    );
    let mut journal = Journal::open(dir.journal(), "run-0001", "session-0001", true).unwrap();
    let mut backend = ReadOnlyAfterRemove(
        Compositor::new(&scenario["compositor"], &Value::Null),
        &dir.0,
    );
    assert!(matches!(
        recover(&mut journal, &mut backend),
        Err(Error::Io(_))
    ));
    std::fs::set_permissions(&dir.0, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(
        std::fs::read_to_string(dir.journal()).unwrap(),
        scenario["journal"].as_str().unwrap()
    );
}
