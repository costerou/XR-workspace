//! Replays `rust/corpus/supervisor/run_order.json` (tests/native_spike/run_order_corpus.py): for every path and every
//! failure point, the ordered steps and the result must be the Python's.

use super::*;
use serde_json::{Value, json};
use std::collections::HashMap;

const RUNTIME: &str = "/run/user/1000";
const SESSION: &str = "s1";
const INSTANCE: &str = "inst";
const OUTPUT: &str = "DP-1";
const FALLBACK: &str = "HDMI-A-1";

/// The Python recorder's fakes, read from the same config.
struct Recorder {
    cfg: Value,
    fallible: Vec<String>,
    trace: Vec<String>,
    seen: HashMap<String, u64>,
    scope: Option<Scope>,
    stop_reason: Option<String>,
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .expect("a list")
        .iter()
        .map(|v| v.as_str().expect("a string").to_owned())
        .collect()
}

fn line(name: &str, parts: &[&str]) -> String {
    std::iter::once(name)
        .chain(parts.iter().copied().filter(|p| !p.is_empty()))
        .collect::<Vec<_>>()
        .join(" ")
}

impl Recorder {
    fn flag(&self, key: &str) -> bool {
        self.cfg[key].as_bool().expect(key)
    }

    fn list(&self, key: &str) -> Vec<String> {
        strings(&self.cfg[key])
    }

    fn step(&mut self, name: &str, parts: &[&str]) -> Result<()> {
        self.trace.push(line(name, parts));
        let count = self.seen.entry(name.to_owned()).or_insert(0);
        let n = *count;
        *count += 1;
        let failing = self.cfg["fail"]
            .as_array()
            .expect("fail")
            .iter()
            .any(|pair| *pair == json!([name, n]));
        if failing && self.fallible.iter().any(|f| f == name) {
            Err(Error(format!("{name} failed")))
        } else {
            Ok(())
        }
    }

    fn note(&mut self, name: &str, parts: &[&str]) {
        self.trace.push(line(name, parts));
    }

    fn saved_scope(&self) -> Option<Scope> {
        let kind = self.cfg["saved_scope"].as_str().expect("saved_scope");
        (kind != "none").then(|| Scope {
            instance: INSTANCE.into(),
            output: if kind == "match" { OUTPUT } else { "DP-9" }.into(),
            recovery_output: FALLBACK.into(),
        })
    }
}

fn joined(names: &[String]) -> Vec<&str> {
    names.iter().map(String::as_str).collect()
}

impl Host for Recorder {
    type Snapshot = &'static str;

    fn verified_session(&mut self) -> Result<String> {
        self.step("session.verify", &[])?;
        Ok(SESSION.into())
    }
    fn validate_physical_pair(
        &mut self,
        session: &str,
        output: &str,
        fallback: &str,
        asleep_ok: bool,
    ) -> Result<()> {
        assert_eq!(session, SESSION);
        self.step(
            "pair.validate",
            &[output, fallback, if asleep_ok { "asleep-ok" } else { "" }],
        )
    }
    fn plan_outputs(&mut self, _run_id: &str) -> Result<Vec<Spec>> {
        self.step("outputs.plan", &[])?;
        Ok([("u1", "XR-1"), ("u2", "XR-2")]
            .map(|(id, name)| Spec {
                id: id.into(),
                name: name.into(),
            })
            .to_vec())
    }
    fn broker_open(&mut self, _run_id: &str, session: &str, fallback: &str) -> Result<()> {
        assert_eq!((session, fallback), (SESSION, FALLBACK));
        self.step("broker.open", &[])
    }
    fn broker_from_journal(&mut self) -> Result<()> {
        self.step("broker.from_journal", &[])
    }
    fn require_output_ownership(&mut self) -> Result<()> {
        self.step("broker.require_ownership", &[])
    }
    fn private_dir(&mut self, relative: &Path) -> Result<()> {
        self.step("private_dir", &[relative.to_str().expect("utf-8")])
    }
    fn lock_owner(&mut self) -> Result<()> {
        self.step("owner.lock", &[])
    }
    fn unlock_owner(&mut self) {
        self.note("owner.unlock", &[]);
    }
    fn journal_open(&mut self, _run_id: &str, session: &str, existing_only: bool) -> Result<()> {
        assert_eq!(session, SESSION);
        self.step(
            "journal.open",
            &[if existing_only { "existing" } else { "" }],
        )?;
        self.scope = if existing_only {
            self.saved_scope()
        } else {
            None
        };
        Ok(())
    }
    fn journal_read_session(&mut self) -> Result<String> {
        self.step("journal.read_session", &[])?;
        Ok(SESSION.into())
    }
    fn journal_is_fresh(&mut self) -> Result<bool> {
        self.step("journal.check_fresh", &[])?;
        Ok(self.flag("fresh"))
    }
    fn journal_bind_broker(&mut self, scope: &Scope) -> Result<()> {
        self.step("journal.bind_broker", &[])?;
        self.scope = Some(scope.clone());
        Ok(())
    }
    fn journal_scope(&self) -> Option<Scope> {
        self.scope.clone()
    }
    fn journal_output_names(&mut self) -> Result<Vec<String>> {
        self.step("journal.output_names", &[])?;
        Ok(self.list("output_names"))
    }
    fn journal_recover(&mut self) -> Result<Recovery> {
        self.step("journal.recover", &[])?;
        Ok(Recovery {
            complete: self.flag("complete"),
            remaining: self.list("remaining"),
            reasons: self.list("reasons"),
        })
    }
    fn journal_compositor_ended(&mut self) -> Result<()> {
        self.step("journal.compositor_ended", &[])
    }
    fn create_outputs(&mut self, specs: &[Spec]) -> Result<()> {
        let names: Vec<&str> = specs.iter().map(|s| s.name.as_str()).collect();
        self.step("outputs.create", &names)
    }
    fn discard_unusable_setup(&mut self, _error: &Error) -> Result<()> {
        self.step("setup.discard_unusable", &[])
    }
    fn desktop_manager(&mut self, _specs: &[Spec]) -> Result<()> {
        self.step("desktops.manager", &[])
    }
    fn broker_verify(&mut self, spec: &Spec) -> Result<()> {
        self.step("broker.verify", &[&spec.name])
    }
    fn take_layout(&mut self) -> Option<Self::Snapshot> {
        self.note("layout.take", &[]);
        self.flag("take").then_some("taken")
    }
    fn window_memory(&mut self, _names: &[String]) -> Result<bool> {
        self.step("window_memory.open", &[])?;
        Ok(self.flag("memory"))
    }
    fn window_memory_place(&mut self, names: &[String]) -> Result<()> {
        self.step("window_memory.place", &joined(names))
    }
    fn adopt(&mut self, names: &[String]) -> Result<()> {
        self.step("adopt", &joined(names))
    }
    fn window_memory_finish(&mut self) -> Result<()> {
        self.step("window_memory.finish", &[])
    }
    fn cursor_reader(&mut self) -> Result<()> {
        self.step("broker.cursor_reader", &[])
    }
    fn view_controls_enter(&mut self) -> Result<()> {
        self.step("view_controls.enter", &[])
    }
    fn view_controls_exit(&mut self) -> Result<()> {
        self.step("view_controls.exit", &[])
    }
    fn continuity_open(&mut self) -> Result<()> {
        self.step("continuity.open", &[])
    }
    fn continuity_close(&mut self) -> Result<()> {
        self.step("continuity.close", &[])
    }
    fn keep_renderer(&mut self, capture: bool) -> Result<i32> {
        self.step("renderer.keep", &[])?;
        let guard = if capture {
            "guard=desktops"
        } else {
            "guard=base"
        };
        self.note(
            "renderer.launch",
            &[
                guard,
                if capture {
                    "capture=XR-1 XR-2"
                } else {
                    "capture=none"
                },
            ],
        );
        self.stop_reason = self.cfg["stop_reason"].as_str().map(str::to_owned);
        Ok(0)
    }
    fn stop_reason(&self) -> Option<String> {
        self.stop_reason.clone()
    }
    fn wake(&mut self, output: &str) {
        self.note("privacy.wake", &[output]);
    }
    fn restore_2d(&mut self, output: &str) {
        self.note("sbs.restore_2d", &[output]);
    }
    fn clear_monitor_rules(&mut self, names: &[String]) -> Vec<String> {
        self.note("monitor_rules.clear", &joined(names));
        self.list("cleared")
    }
    fn named_monitor_rules(&mut self, names: &[String]) -> Vec<String> {
        self.note("monitor_rules.named", &joined(names));
        self.list("named")
    }
    fn unpin(&mut self) {
        self.note("layout.unpin", &[]);
    }
    fn load_layout(&mut self) -> Result<Option<Self::Snapshot>> {
        self.step("layout.load", &[])?;
        Ok(self.flag("saved").then_some("saved"))
    }
    fn restore_layout(&mut self, snapshot: &Self::Snapshot) {
        self.note("layout.restore", &[snapshot]);
    }
    fn discard_layout(&mut self) -> Result<()> {
        self.step("layout.discard", &[])
    }
    fn notify(&mut self, text: &str) {
        self.note("notify", &[text]);
    }
    fn left_on(&mut self, names: &[String]) -> Vec<String> {
        self.note("left_on", &joined(names));
        self.list("left_on")
    }
}

fn recovery_json(recovery: &Recovery) -> serde_json::Map<String, Value> {
    let mut map = serde_json::Map::new();
    map.insert("complete".into(), json!(recovery.complete));
    map.insert("remaining".into(), json!(recovery.remaining));
    map.insert("reasons".into(), json!(recovery.reasons));
    map
}

fn outcome<T>(
    result: Result<T>,
    fields: impl FnOnce(T) -> serde_json::Map<String, Value>,
) -> Value {
    match result {
        Ok(value) => json!({ "ok": fields(value) }),
        Err(error) => json!({ "error": error.0 }),
    }
}

fn replay(defaults: &Value, fallible: &[String], case: &Value) -> (Vec<String>, Value) {
    let mut cfg = defaults.clone();
    for (key, value) in case["config"].as_object().expect("config") {
        cfg[key] = value.clone();
    }
    let consent = Consent {
        run_id: "run-1".into(),
        model: cfg["model"].as_str().expect("model").into(),
        instance: INSTANCE.into(),
        output: OUTPUT.into(),
        recovery_output: FALLBACK.into(),
        capture: cfg["capture"].as_bool().expect("capture"),
    };
    let runtime = Path::new(RUNTIME);
    let journal = if cfg["canonical"].as_bool().expect("canonical") {
        runtime.join("xr-workspace-spike/run-1/journal.json")
    } else {
        runtime.join("elsewhere/journal.json")
    };
    let mut host = Recorder {
        cfg,
        fallible: fallible.to_vec(),
        trace: vec![],
        seen: HashMap::new(),
        scope: None,
        stop_reason: None,
    };
    let result = match case["entry"].as_str().expect("entry") {
        "run" => {
            let options = RunOptions {
                saved_setup: host.flag("saved_setup"),
            };
            outcome(run_managed(&mut host, &consent, options), |out| {
                let mut map = recovery_json(&out.recovery);
                map.insert("renderer_returncode".into(), json!(out.renderer_returncode));
                map.insert("stop_reason".into(), json!(out.stop_reason));
                map
            })
        }
        "recover" => {
            let clear_rules = host.list("clear_rules");
            outcome(
                recover_managed(&mut host, &consent, runtime, &journal, &clear_rules),
                |r| recovery_json(&r),
            )
        }
        "ended" => outcome(recover_ended(&mut host, &consent, runtime, &journal), |r| {
            recovery_json(&r)
        }),
        entry => panic!("unknown entry {entry}"),
    };
    (host.trace, result)
}

#[test]
fn run_order_equals_the_python_on_every_path_and_failure_point() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/supervisor/run_order.json"
    );
    let corpus: Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("corpus")).expect("json");
    let fallible = strings(&corpus["fallible"]);
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(cases.len() > 200, "{} cases", cases.len());
    let mut wrong = vec![];
    for case in cases {
        let (trace, result) = replay(&corpus["defaults"], &fallible, case);
        let name = case["name"].as_str().expect("name");
        if json!(trace) != case["trace"] {
            wrong.push(format!(
                "{name}: trace\n  python: {}\n  rust:   {}",
                case["trace"],
                json!(trace)
            ));
        }
        if result != case["outcome"] {
            wrong.push(format!(
                "{name}: outcome\n  python: {}\n  rust:   {result}",
                case["outcome"]
            ));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} cases differ:\n{}",
        wrong.len(),
        cases.len(),
        wrong.join("\n")
    );
}

/// The gate of privacy mode (docs/rust-rebuild-plan.md, "Privacy mode wakes the fallback display before anything
/// else during recovery"), checked on the Rust's own trace rather than the corpus: in every path and every fault that
/// reaches recovery, the fallback is woken before any recovery step touches the compositor, the broker or the windows,
/// and nothing that changes them comes between the run's end and the wake. A run whose compositor has exited
/// (`recover_ended`) has no display to wake and only closes its journal.
#[test]
fn recovery_wakes_the_fallback_first_in_every_fault() {
    const RECOVERY: [&str; 8] = [
        "sbs.restore_2d",
        "journal.output_names",
        "broker.from_journal",
        "monitor_rules.clear",
        "monitor_rules.named",
        "layout.unpin",
        "layout.restore",
        "journal.recover",
    ];
    // Everything a trace may hold before the wake: the run's set-up, the run itself and its teardown. None of it is a
    // recovery step, so a wake after any other step would be late.
    const BEFORE_WAKE: [&str; 26] = [
        "session.verify",
        "pair.validate",
        "outputs.plan",
        "broker.open",
        "broker.require_ownership",
        "private_dir",
        "owner.lock",
        "journal.open",
        "journal.check_fresh",
        "journal.bind_broker",
        "layout.take",
        "outputs.create",
        "broker.verify",
        "broker.cursor_reader",
        "setup.discard_unusable",
        "adopt",
        "desktops.manager",
        "window_memory.open",
        "window_memory.place",
        "window_memory.finish",
        "view_controls.enter",
        "continuity.open",
        "renderer.keep",
        "renderer.launch",
        "continuity.close",
        "view_controls.exit",
    ];
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/supervisor/run_order.json"
    );
    let corpus: Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("corpus")).expect("json");
    let fallible = strings(&corpus["fallible"]);
    let wake = format!("privacy.wake {FALLBACK}");
    let (mut recovering, mut faults) = (0, 0);
    let mut wrong = vec![];
    for case in corpus["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().expect("name");
        let (trace, _) = replay(&corpus["defaults"], &fallible, case);
        let step = |s: &String| s.split(' ').next().unwrap_or("").to_owned();
        let Some(first) = trace
            .iter()
            .position(|s| RECOVERY.contains(&step(s).as_str()))
        else {
            continue;
        };
        recovering += 1;
        faults += usize::from(name.contains("/fail "));
        match trace.iter().position(|s| *s == wake) {
            Some(at) if at < first => {
                for other in &trace[..at] {
                    let s = step(other);
                    if !BEFORE_WAKE.contains(&s.as_str()) {
                        wrong.push(format!("{name}: {other} before the wake"));
                    }
                }
            }
            _ => wrong.push(format!("{name}: {} before {wake}", trace[first])),
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
    assert!(recovering > 100 && faults > 50, "{recovering} recovering cases, {faults} faults");
    eprintln!("fallback woken first in {recovering} recovering cases, {faults} of them faults");
}

#[test]
fn a_run_id_outside_the_token_alphabet_is_not_a_journal_path() {
    let consent = |run_id: &str| Consent {
        run_id: run_id.into(),
        model: "luma-ultra".into(),
        instance: INSTANCE.into(),
        output: OUTPUT.into(),
        recovery_output: FALLBACK.into(),
        capture: false,
    };
    let runtime = Path::new(RUNTIME);
    for bad in ["", "../x", ".x", "a/b", &"a".repeat(129)] {
        let path = runtime
            .join("xr-workspace-spike")
            .join(bad)
            .join("journal.json");
        assert_eq!(
            canonical_journal(runtime, &consent(bad), &path),
            Err(Error::new(INVALID_IDENTIFIER)),
            "{bad:?}"
        );
    }
    let good = "a".repeat(128);
    let path = runtime
        .join("xr-workspace-spike")
        .join(&good)
        .join("journal.json");
    assert_eq!(
        canonical_journal(runtime, &consent(&good), &path),
        Ok(path.clone())
    );
}
