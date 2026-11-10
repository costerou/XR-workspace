//! The controller's state machine against a fake host: no autostart, consent only from a confirmed
//! `scope_id`, stale clicks refused, a scope that only ever shrinks (also as a property over random
//! sequences), and the request/reply path over a real Unix socket pair in the shared wire format.

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;

use xr_wire::controller::{is_snapshot, Message, Request};
use xr_wire::json::{parse_strict, Value};
use xr_workspace::controller::{set, state, Controller, ControllerHost, PRO2_KEY};

fn j(text: &str) -> Value {
    parse_strict(text.as_bytes()).unwrap()
}

fn s(text: &str) -> Value {
    Value::String(text.into())
}

/// Everything the controller touches, as plain fields a test sets and reads.
struct FakeHost {
    observed: Value,
    probe_err: Option<String>,
    alive: HashSet<String>,
    control_ok: bool,
    controls: Vec<(String, Option<String>, Option<Value>)>,
    spawned: Vec<(String, String, Option<String>, Vec<String>)>,
    spawn_err: Option<String>,
    stopped: Vec<String>,
    consents: HashMap<String, Value>,
    written: Vec<Value>,
    results: HashMap<String, Value>,
    recovery: Option<String>,
    settings: Value,
    clock: f64,
    tokens: u64,
    runs: u64,
    ownership: bool,
    status: Value,
    /// What `start_scope` returns, with a fresh `run_id` filled in.
    scope: Value,
}

impl FakeHost {
    fn new() -> FakeHost {
        FakeHost {
            observed: j(r#"{"model":"one","output":"DP-1","monitors":[{"name":"eDP-1"}],"journals":[],"external":[]}"#),
            probe_err: None,
            alive: HashSet::new(),
            control_ok: true,
            controls: Vec::new(),
            spawned: Vec::new(),
            spawn_err: None,
            stopped: Vec::new(),
            consents: HashMap::new(),
            written: Vec::new(),
            results: HashMap::new(),
            recovery: None,
            settings: j(r#"{"spacing":100,"prediction":true,"mode":"follow","fallback":"eDP-1"}"#),
            clock: 1000.0,
            tokens: 0,
            runs: 0,
            ownership: false,
            status: j(r#"{"view":{"mode":"follow","zoomed":false},"capabilities":["recenter"],"bogus":1}"#),
            scope: j(
                r#"{"model":"one","tracking":true,"mode_switch":true,"luma_driver":"/pin/luma","tracking_driver":"luma","sbs_mode":"half"}"#,
            ),
        }
    }
}

impl ControllerHost for FakeHost {
    fn now(&mut self) -> f64 {
        self.clock
    }
    fn probe(&mut self) -> Result<Value, String> {
        match &self.probe_err {
            Some(e) => Err(e.clone()),
            None => Ok(self.observed.clone()),
        }
    }
    fn alive(&mut self, run_id: &str) -> Result<bool, String> {
        Ok(self.alive.contains(run_id))
    }
    fn control(&mut self, run_id: &str, _session: &Value, op: Option<&str>, payload: Option<&Value>) -> Result<Option<Value>, String> {
        self.controls.push((run_id.into(), op.map(str::to_string), payload.cloned()));
        if !self.control_ok {
            return Ok(None);
        }
        Ok(Some(if op == Some("status") { self.status.clone() } else { Value::Bool(true) }))
    }
    fn stop(&mut self, run_id: &str) -> Result<(), String> {
        self.stopped.push(run_id.into());
        Ok(())
    }
    fn spawn(&mut self, action: &str, consent: &str, journal: Option<&str>, rules: &[String]) -> Result<(), String> {
        self.spawned.push((action.into(), consent.into(), journal.map(str::to_string), rules.to_vec()));
        match &self.spawn_err {
            Some(e) => Err(e.clone()),
            None => Ok(()),
        }
    }
    fn read_consent(&mut self, run_id: &str) -> Result<Value, String> {
        self.consents.get(run_id).cloned().ok_or_else(|| "missing consent".into())
    }
    fn build_consent(&mut self, scope: &Value) -> Result<Value, String> {
        Ok(scope.clone())
    }
    fn write_consent(&mut self, consent: &Value) -> Result<String, String> {
        let id = consent.get("run_id").and_then(Value::as_str).unwrap().to_string();
        self.written.push(consent.clone());
        self.consents.insert(id.clone(), consent.clone());
        Ok(format!("{id}.json"))
    }
    fn state_path(&self, name: &str) -> String {
        format!("/state/{name}")
    }
    fn read_result(&mut self, run_id: &str) -> Result<Option<Value>, String> {
        Ok(self.results.get(run_id).cloned())
    }
    fn recovery_reason(&mut self, _run_id: &str) -> Option<String> {
        self.recovery.clone()
    }
    fn token(&mut self) -> String {
        self.tokens += 1;
        format!("tok-{}", self.tokens)
    }
    fn load_settings(&mut self, _model: &str) -> Value {
        self.settings.clone()
    }
    fn check_settings(&mut self, settings: &Value) -> Result<Value, String> {
        Ok(settings.clone())
    }
    fn save_settings(&mut self, _model: &str, settings: &Value) -> Result<(), String> {
        self.settings = settings.clone();
        Ok(())
    }
    fn default_tracking_driver(&mut self, _model: &str) -> Value {
        s("luma")
    }
    fn pick_fallback(&mut self, monitors: &Value, _output: &Value, _preferred: &Value) -> Value {
        match monitors {
            Value::Array(m) => m.first().and_then(|m| m.get("name")).cloned().unwrap_or(Value::Null),
            _ => Value::Null,
        }
    }
    fn ownership_plugin_loaded(&mut self, _observed: &Value) -> bool {
        self.ownership
    }
    fn start_scope(&mut self, _observed: &Value, _profile: &Value, _fallback: &Value) -> Result<Value, String> {
        self.runs += 1;
        let mut scope = self.scope.clone();
        set(&mut scope, "run_id", s(&format!("run-{}", self.runs)));
        Ok(scope)
    }
    fn tracking_wait(&mut self, _journal: &Value) -> i64 {
        30
    }
    fn tracking_in_use(&mut self, _observed: &Value, _running: Option<&Value>) -> Value {
        Value::Null
    }
    fn pro2_snapshot(&mut self, _observed: &Value) -> Option<Value> {
        None
    }
    fn save_projection(&mut self, _projection: Option<&Value>) -> Result<(), String> {
        Ok(())
    }
    fn validate_placements(&mut self, placements: &Value) -> Result<Value, String> {
        Ok(placements.clone())
    }
    fn validate_video(&mut self, request: &Value) -> Result<Value, String> {
        Ok(request.clone())
    }
    fn incident(&mut self) -> Value {
        Value::Null
    }
}

type C = Controller<FakeHost>;

fn call(c: &mut C, msg: &str) -> Value {
    Value::Object(c.request(&j(msg)))
}

fn ok(reply: &Value) -> bool {
    reply.get("ok") == Some(&Value::Bool(true))
}

fn error(reply: &Value) -> Option<&str> {
    reply.get("error").and_then(Value::as_str)
}

fn begin(c: &mut C) -> String {
    let reply = call(c, r#"{"op":"begin_start"}"#);
    assert!(ok(&reply), "{reply:?}");
    reply.get("scope_id").and_then(Value::as_str).unwrap().to_string()
}

fn confirm(c: &mut C, scope_id: &str, optional: &str) -> Value {
    call(c, &format!(r#"{{"op":"confirm_start","scope_id":"{scope_id}","optional":{optional}}}"#))
}

fn set_obs(c: &mut C, key: &str, value: Value) {
    set(&mut c.host.observed, key, value);
}

/// A controller that has found a live run `r9` with a session it can reach.
fn running() -> C {
    let mut host = FakeHost::new();
    set(&mut host.observed, "journals", j(r#"[{"run_id":"r9","session":{"socket":"/run/r9"},"path":"/j/r9.json"}]"#));
    host.consents.insert("r9".into(), j(r#"{"run_id":"r9","model":"one","tracking":true}"#));
    host.alive.insert("r9".into());
    let c = Controller::new(host);
    assert_eq!(c.state, state::RUNNING);
    c
}

#[test]
fn starting_the_service_never_starts_a_run() {
    let c = Controller::new(FakeHost::new());
    assert_eq!(c.state, state::IDLE);
    assert!(c.pending.is_none() && c.run.is_none());
    assert!(c.host.spawned.is_empty() && c.host.written.is_empty());
}

#[test]
fn begin_shows_the_scope_and_only_confirm_spawns() {
    let mut c = Controller::new(FakeHost::new());
    let reply = call(&mut c, r#"{"op":"begin_start"}"#);
    assert!(ok(&reply));
    assert_eq!(reply.get("state"), Some(&s(state::CONFIRMING)));
    assert_eq!(reply.get("pending").and_then(|p| p.get("scope_id")), reply.get("scope_id"));
    let shown = reply.get("scope").cloned().unwrap();
    assert!(c.host.spawned.is_empty() && c.host.written.is_empty(), "begin alone must not touch anything");
    c.tick();
    assert!(c.host.spawned.is_empty(), "a tick must not confirm");

    let id = reply.get("scope_id").and_then(Value::as_str).unwrap().to_string();
    let reply = confirm(&mut c, &id, "{}");
    assert!(ok(&reply), "{reply:?}");
    assert_eq!(c.state, state::STARTING);
    assert_eq!(c.host.written, vec![shown]);
    assert_eq!(c.host.spawned, vec![("start".into(), "/state/run-1.json".into(), None, vec![])]);
    assert!(c.pending.is_none());

    // The confirmation is consumed: the same click again does nothing.
    let again = confirm(&mut c, &id, "{}");
    assert_eq!(error(&again), Some("stale scope"));
    assert_eq!(c.host.spawned.len(), 1);
}

#[test]
fn stale_or_foreign_scope_ids_are_refused() {
    let mut c = Controller::new(FakeHost::new());
    assert_eq!(error(&confirm(&mut c, "tok-1", "{}")), Some("stale scope"), "nothing pending");
    let id = begin(&mut c);
    assert_eq!(error(&confirm(&mut c, "tok-999", "{}")), Some("stale scope"));
    assert_eq!(error(&call(&mut c, r#"{"op":"confirm_start"}"#)), Some("stale scope"));
    let wrong_action = call(&mut c, &format!(r#"{{"op":"confirm_recover","scope_id":"{id}"}}"#));
    assert_eq!(error(&wrong_action), Some("stale scope"));
    assert_eq!(c.state, state::CONFIRMING, "a refused click leaves the scope pending");
    assert!(c.host.spawned.is_empty() && c.host.written.is_empty());
    assert!(ok(&confirm(&mut c, &id, "{}")));
}

#[test]
fn a_new_begin_while_confirming_is_busy() {
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("busy"));
    assert_eq!(c.pending.as_ref().and_then(|p| p.get("scope_id")), Some(&s(&id)));
}

#[test]
fn any_change_the_probe_sees_drops_the_pending_scope() {
    for (key, value) in [
        ("output", s("DP-2")),
        ("monitors", j(r#"[{"name":"HDMI-A-1"}]"#)),
        ("plugins", j(r#"["other"]"#)),
        ("installed", Value::Bool(false)),
        ("tracking_override", s("none")),
        ("pro2_route", j(r#"{"x":1}"#)),
    ] {
        let mut c = Controller::new(FakeHost::new());
        let id = begin(&mut c);
        set_obs(&mut c, key, value);
        assert_eq!(error(&confirm(&mut c, &id, "{}")), Some("stale scope"), "{key}");
        assert_eq!(c.state, state::IDLE, "{key}");
        assert!(c.pending.is_none(), "{key}");
        assert!(c.host.spawned.is_empty(), "{key}");
    }
    // Unplugged while confirming: back to no-glasses, nothing spawned.
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    set_obs(&mut c, "model", Value::Null);
    assert_eq!(error(&confirm(&mut c, &id, "{}")), Some("stale scope"));
    assert_eq!(c.state, state::NO_GLASSES);
    assert!(c.host.spawned.is_empty());
    // A probe key outside the scope's inputs does not.
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    set_obs(&mut c, "reason", s("noise"));
    assert!(ok(&confirm(&mut c, &id, "{}")));
}

#[test]
fn cancel_returns_to_idle_without_spawning() {
    let mut c = Controller::new(FakeHost::new());
    assert_eq!(error(&call(&mut c, r#"{"op":"cancel"}"#)), Some("busy"));
    let id = begin(&mut c);
    assert!(ok(&call(&mut c, r#"{"op":"cancel"}"#)));
    assert_eq!(c.state, state::IDLE);
    assert_eq!(error(&confirm(&mut c, &id, "{}")), Some("stale scope"));
    assert!(c.host.spawned.is_empty());
}

#[test]
fn begin_refuses_without_glasses_output_or_with_an_owner_loaded() {
    let mut host = FakeHost::new();
    set(&mut host.observed, "model", Value::Null);
    let mut c = Controller::new(host);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("busy"));

    let mut host = FakeHost::new();
    set(&mut host.observed, "output", Value::Null);
    set(&mut host.observed, "reason", s("glasses output not found"));
    let mut c = Controller::new(host);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("glasses output not found"));

    let mut host = FakeHost::new();
    set(&mut host.observed, "monitors", j("[]"));
    let mut c = Controller::new(host);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("choose a physical fallback output"));

    let mut host = FakeHost::new();
    host.ownership = true;
    let mut c = Controller::new(host);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("ownership plugin already loaded"));
    assert!(c.pending.is_none());
}

#[test]
fn a_spawn_failure_is_visible_and_consumes_the_confirmation() {
    let mut host = FakeHost::new();
    host.spawn_err = Some("systemd-run failed".into());
    let mut c = Controller::new(host);
    let id = begin(&mut c);
    let reply = confirm(&mut c, &id, "{}");
    assert_eq!(error(&reply), Some("systemd-run failed"));
    assert_eq!(reply.get("state"), Some(&s(state::FAILED)));
    assert_eq!(reply.get("reason"), Some(&s("systemd-run failed")));
    assert_eq!(error(&confirm(&mut c, &id, "{}")), Some("stale scope"));
    assert_eq!(c.host.spawned.len(), 1);
}

#[test]
fn a_runner_failure_shows_in_the_reason() {
    for (result, reason) in [
        (r#"{"run_id":"run-1","complete":false,"phase":"verify"}"#, "runner failed during verify"),
        (r#"{"run_id":"run-1","complete":true,"error":"glasses unplugged"}"#, "glasses unplugged"),
        (r#"{"run_id":"run-1","complete":true,"renderer_returncode":3}"#, "runner failed during run"),
    ] {
        let mut c = Controller::new(FakeHost::new());
        let id = begin(&mut c);
        assert!(ok(&confirm(&mut c, &id, "{}")));
        c.host.results.insert("run-1".into(), j(result));
        c.tick();
        assert_eq!(c.state, state::IDLE, "{result}");
        assert!(c.run.is_none());
        let snap = c.snapshot();
        assert_eq!(snap.get("reason"), Some(&s(reason)), "{result}");
        // A new begin clears it.
        begin(&mut c);
        assert_eq!(c.snapshot().get("reason"), Some(&Value::Null));
    }
    // A clean finish leaves no reason.
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    confirm(&mut c, &id, "{}");
    c.host.results.insert("run-1".into(), j(r#"{"run_id":"run-1","complete":true}"#));
    c.tick();
    assert_eq!((c.state, c.snapshot().get("reason").cloned()), (state::IDLE, Some(Value::Null)));
}

#[test]
fn a_result_for_another_run_fails_the_controller() {
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    confirm(&mut c, &id, "{}");
    c.host.results.insert("run-1".into(), j(r#"{"run_id":"run-2","complete":true}"#));
    c.tick();
    assert_eq!(c.state, state::FAILED);
    assert_eq!(c.error.as_deref(), Some("runner result identity mismatch"));
}

#[test]
fn a_runner_that_never_writes_a_journal_fails_after_the_grace() {
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    confirm(&mut c, &id, "{}");
    c.host.clock += 4.0;
    c.tick();
    assert_eq!(c.state, state::STARTING);
    c.host.clock += 2.0;
    c.tick();
    assert_eq!(c.state, state::FAILED);
    assert_eq!(c.error.as_deref(), Some("runner exited before creating a journal"));
}

#[test]
fn a_probe_fault_fails_visibly() {
    let mut c = Controller::new(FakeHost::new());
    c.host.probe_err = Some("hyprctl: no instance".into());
    c.tick();
    assert_eq!(c.state, state::FAILED);
    assert_eq!(c.snapshot().get("reason"), Some(&s("hyprctl: no instance")));
}

#[test]
fn a_live_journal_is_running_and_takes_only_live_keys_from_status() {
    let mut c = running();
    let snap = c.snapshot();
    assert!(is_snapshot(&snap));
    assert_eq!(snap.get("run_id"), Some(&s("r9")));
    assert_eq!(snap.get("journal"), Some(&s("/j/r9.json")));
    assert_eq!(snap.get("view"), Some(&j(r#"{"mode":"follow","zoomed":false}"#)));
    assert_eq!(snap.get("capabilities"), Some(&j(r#"["recenter"]"#)));
    assert!(snap.get("bogus").is_none());
    assert!(c.host.spawned.is_empty(), "adopting a run spawns nothing");
}

#[test]
fn a_live_unit_without_a_session_is_starting_and_without_control_is_failed() {
    let mut host = FakeHost::new();
    set(&mut host.observed, "journals", j(r#"[{"run_id":"r9","phase":"tracking"}]"#));
    host.consents.insert("r9".into(), j(r#"{"run_id":"r9","model":"one"}"#));
    host.alive.insert("r9".into());
    let mut c = Controller::new(host);
    assert_eq!(c.state, state::STARTING);
    assert!(c.error.as_deref().unwrap().contains("up to 30 seconds"));

    set_obs(&mut c, "journals", j(r#"[{"run_id":"r9","session":{"socket":"/run/r9"}}]"#));
    c.host.control_ok = false;
    c.tick();
    assert_eq!(c.state, state::FAILED);
    assert!(ok(&call(&mut c, r#"{"op":"stop"}"#)), "a failed run can still be stopped");
    assert_eq!(c.host.stopped, vec!["r9".to_string()]);
}

fn dead_journal() -> C {
    let mut host = FakeHost::new();
    set(
        &mut host.observed,
        "journals",
        j(r#"[{"run_id":"r9","path":"/j/r9.json","operations":[{"operation":{"name":"rule-a"}},{"operation":{"name":"rule-b"}}]}]"#),
    );
    set(
        &mut host.observed,
        "config_rules",
        j(r#"[{"name":"rule-a","removable":true},{"name":"rule-b","removable":false},{"name":"rule-c","removable":true}]"#),
    );
    host.consents.insert("r9".into(), j(r#"{"run_id":"r9","model":"one","tracking":false}"#));
    host.recovery = Some("previous run did not restore".into());
    Controller::new(host)
}

#[test]
fn a_dead_journal_needs_recovery_and_recovery_needs_confirm() {
    let mut c = dead_journal();
    assert_eq!(c.state, state::RECOVERY_NEEDED);
    assert_eq!(c.snapshot().get("reason"), Some(&s("previous run did not restore")));
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("busy"));

    let reply = call(&mut c, r#"{"op":"begin_recover"}"#);
    assert!(ok(&reply), "{reply:?}");
    assert_eq!(reply.get("action"), Some(&s("recover")));
    assert_eq!(reply.get("scope"), Some(&j(r#"{"run_id":"r9","model":"one","tracking":false}"#)));
    assert_eq!(reply.get("journal"), Some(&s("/j/r9.json")));
    assert!(c.host.spawned.is_empty());

    let id = reply.get("scope_id").and_then(Value::as_str).unwrap().to_string();
    assert_eq!(error(&confirm(&mut c, &id, "{}")), Some("stale scope"), "confirm_start cannot confirm a recovery");
    assert!(ok(&call(&mut c, r#"{"op":"cancel"}"#)));
    assert_eq!(c.state, state::RECOVERY_NEEDED, "cancelling a recovery keeps it needed");

    let reply = call(&mut c, r#"{"op":"begin_recover"}"#);
    let id = reply.get("scope_id").and_then(Value::as_str).unwrap().to_string();
    assert!(ok(&call(&mut c, &format!(r#"{{"op":"confirm_recover","scope_id":"{id}"}}"#))));
    assert_eq!(
        c.host.spawned,
        vec![("recover".into(), "/state/r9.json".into(), Some("/j/r9.json".into()), vec!["rule-a".into()])]
    );
    assert!(c.host.written.is_empty(), "recovery reuses the run's own consent");
    assert_eq!(c.state, state::STARTING);
}

#[test]
fn several_journals_or_an_unreadable_consent_need_the_cli() {
    let mut host = FakeHost::new();
    set(&mut host.observed, "journals", j(r#"[{"run_id":"a"},{"run_id":"b"}]"#));
    let mut c = Controller::new(host);
    assert_eq!(c.state, state::CLI_RECOVERY);
    assert_eq!(c.snapshot().get("reason"), Some(&s("multiple unfinished journals require CLI recovery")));
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_recover"}"#)), Some("busy or CLI recovery required"));
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("busy"));

    let mut host = FakeHost::new();
    set(&mut host.observed, "journals", j(r#"[{"run_id":"a"}]"#));
    let c = Controller::new(host);
    assert_eq!(c.state, state::CLI_RECOVERY);
    assert!(c.host.spawned.is_empty());
}

#[test]
fn a_confirmation_can_only_shrink_the_scope() {
    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    assert!(ok(&confirm(&mut c, &id, r#"{"tracking":false}"#)));
    let consent = &c.host.written[0];
    assert_eq!(consent.get("tracking"), Some(&Value::Bool(false)));
    assert!(consent.get("luma_driver").is_none() && consent.get("tracking_driver").is_none());
    assert_eq!(consent.get("sbs_mode"), Some(&s("half")));

    let mut c = Controller::new(FakeHost::new());
    let id = begin(&mut c);
    assert!(ok(&confirm(&mut c, &id, r#"{"mode_switch":false,"tracking":true}"#)));
    let consent = &c.host.written[0];
    assert_eq!(consent.get("mode_switch"), Some(&Value::Bool(false)));
    assert!(consent.get("sbs_mode").is_none());
    assert_eq!(consent.get("tracking_driver"), Some(&s("luma")));

    let mut host = FakeHost::new();
    host.scope = j(r#"{"model":"one","tracking":false,"mode_switch":true}"#);
    let mut c = Controller::new(host);
    let id = begin(&mut c);
    assert_eq!(error(&confirm(&mut c, &id, r#"{"tracking":true}"#)), Some("cannot expand confirmed scope"));
    assert!(c.host.written.is_empty() && c.host.spawned.is_empty());
}

#[test]
fn a_malformed_optional_is_refused() {
    for optional in ["[]", r#""tracking""#, r#"{"model":false}"#, r#"{"tracking":1}"#, r#"{"run_id":false}"#] {
        let mut c = Controller::new(FakeHost::new());
        let id = begin(&mut c);
        assert_eq!(error(&confirm(&mut c, &id, optional)), Some("invalid optional scope"), "{optional}");
        assert!(c.host.written.is_empty() && c.host.spawned.is_empty());
    }
}

#[test]
fn a_pro2_scope_ignores_optional() {
    let mut host = FakeHost::new();
    host.scope = j(r#"{"model":"pro2","tracking":true,"mode_switch":true,"sbs_mode":"full"}"#);
    set(&mut host.scope, PRO2_KEY, j(r#"{"output":"DP-3"}"#));
    let mut c = Controller::new(host);
    let id = begin(&mut c);
    assert!(ok(&confirm(&mut c, &id, r#"{"tracking":false,"mode_switch":false}"#)));
    let consent = &c.host.written[0];
    assert_eq!(consent.get("tracking"), Some(&Value::Bool(true)));
    assert_eq!(consent.get("sbs_mode"), Some(&s("full")));
}

#[test]
fn settings_while_running_are_live_only_and_bounded() {
    let mut c = running();
    let refusals = [
        (r#"{"resolution":"3840x1080"}"#, "resolution/settings locked while running"),
        (r#"{"tracking_driver":"luma"}"#, "resolution/settings locked while running"),
        (r#"{"placements":[],"spacing":120}"#, "send placements on their own"),
        (r#"{"spacing":110}"#, "live spacing must use 20 pixel steps"),
        (r#"{"spacing":300}"#, "too many live operations; use smaller steps"),
        (r#"{"mode":"locked"}"#, "the overview is always anchored: maximize a desktop to choose follow or locked"),
    ];
    for (settings, message) in refusals {
        let before = c.host.controls.len();
        let reply = call(&mut c, &format!(r#"{{"op":"set_settings","settings":{settings}}}"#));
        assert_eq!(error(&reply), Some(message), "{settings}");
        assert_eq!(c.host.controls.len(), before, "{settings} must send nothing");
    }
    let sent = |c: &C| c.host.controls.iter().filter_map(|(_, op, _)| op.clone()).filter(|op| op != "status").collect::<Vec<_>>();

    let reply = call(&mut c, r#"{"op":"set_settings","settings":{"spacing":140,"prediction":false}}"#);
    assert!(ok(&reply) && reply.get("forwarded") == Some(&Value::Bool(true)), "{reply:?}");
    assert_eq!(sent(&c), ["spacing_up", "spacing_up", "prediction"]);

    c.host.controls.clear();
    assert!(ok(&call(&mut c, r#"{"op":"set_settings","settings":{"placements":[{"desktop":1}]}}"#)));
    assert_eq!(sent(&c), ["placement"]);
    assert_eq!(c.host.controls.last().unwrap().2, Some(j(r#"{"desktops":[{"desktop":1}]}"#)));
    assert_eq!(c.host.settings.get("spacing"), Some(&j("100")), "live settings are not saved by the controller");
}

#[test]
fn settings_at_rest_are_saved_and_the_driver_is_not_a_choice() {
    let mut c = Controller::new(FakeHost::new());
    let reply = call(&mut c, r#"{"op":"set_settings","settings":{"tracking_driver":"other"}}"#);
    assert_eq!(error(&reply), Some("the tracking driver follows the glasses and is not a choice"));
    assert!(ok(&call(&mut c, r#"{"op":"set_settings","settings":{"spacing":160,"tracking_driver":"luma"}}"#)));
    assert_eq!(c.host.settings.get("spacing"), Some(&j("160")));
    let id = begin(&mut c);
    assert_eq!(error(&call(&mut c, r#"{"op":"set_settings","settings":{"spacing":180}}"#)), Some("busy"));
    assert!(c.pending.as_ref().is_some_and(|p| p.get("scope_id") == Some(&s(&id))));
}

#[test]
fn stop_and_unknown_requests() {
    let mut c = Controller::new(FakeHost::new());
    assert_eq!(error(&call(&mut c, r#"{"op":"stop"}"#)), Some("busy"));
    assert_eq!(error(&call(&mut c, r#"{"op":"rm -rf"}"#)), Some("unknown operation"));
    assert_eq!(error(&Value::Object(c.request(&j("[1]")))), Some("invalid request"));
    let reply = call(&mut c, r#"{"op":"status"}"#);
    let mut body = reply.clone();
    xr_workspace::controller::remove(&mut body, "ok");
    assert!(is_snapshot(&body), "{reply:?}");

    let mut c = running();
    assert!(ok(&call(&mut c, r#"{"op":"stop"}"#)));
    assert_eq!(c.state, state::STOPPING);
    assert_eq!(c.host.stopped, vec!["r9".to_string()]);
    assert_eq!(error(&call(&mut c, r#"{"op":"begin_start"}"#)), Some("busy"));
}

#[test]
fn take_changed_reports_each_snapshot_once() {
    let mut c = Controller::new(FakeHost::new());
    assert!(c.take_changed().is_some());
    assert!(c.take_changed().is_none());
    begin(&mut c);
    let changed = c.take_changed().unwrap();
    assert_eq!(changed.get("state"), Some(&s(state::CONFIRMING)));
    assert!(c.take_changed().is_none());
}

/// xorshift64*: deterministic, no dependency.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
    fn coin(&mut self) -> bool {
        self.below(2) == 0
    }
}

fn random_scope(rng: &mut Rng) -> Value {
    let mut scope = j(r#"{"model":"one"}"#);
    set(&mut scope, "tracking", Value::Bool(rng.coin()));
    set(&mut scope, "mode_switch", Value::Bool(rng.coin()));
    for key in ["luma_driver", "tracking_driver", "sbs_mode", "fallback"] {
        if rng.coin() {
            set(&mut scope, key, s("x"));
        }
    }
    if rng.below(5) == 0 {
        set(&mut scope, "model", s("pro2"));
        set(&mut scope, PRO2_KEY, j(r#"{"output":"DP-3"}"#));
    }
    scope
}

fn random_optional(rng: &mut Rng) -> String {
    match rng.below(10) {
        0 => "[]".into(),
        1 => r#"{"run_id":false}"#.into(),
        _ => {
            let mut parts = Vec::new();
            for key in ["tracking", "mode_switch"] {
                match rng.below(3) {
                    0 => parts.push(format!(r#""{key}":true"#)),
                    1 => parts.push(format!(r#""{key}":false"#)),
                    _ => {}
                }
            }
            format!("{{{}}}", parts.join(","))
        }
    }
}

#[test]
fn property_the_consent_is_never_wider_than_the_scope_shown() {
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let mut confirms = 0;
    let mut shrinks = 0;
    for _ in 0..3000 {
        let mut c = Controller::new(FakeHost::new());
        // run_id -> the scope the panel was shown for it.
        let mut shown: HashMap<String, Value> = HashMap::new();
        let mut ids: Vec<String> = Vec::new();
        for _ in 0..40 {
            match rng.below(9) {
                0 | 1 => {
                    c.host.scope = random_scope(&mut rng);
                    let reply = call(&mut c, r#"{"op":"begin_start"}"#);
                    if ok(&reply) {
                        let scope = reply.get("scope").cloned().unwrap();
                        let run = scope.get("run_id").and_then(Value::as_str).unwrap().to_string();
                        shown.insert(run, scope);
                        ids.push(reply.get("scope_id").and_then(Value::as_str).unwrap().to_string());
                    }
                }
                2..=4 => {
                    let id = match rng.below(4) {
                        0 => "forged".to_string(),
                        _ if ids.is_empty() => "none".to_string(),
                        1 => ids[rng.below(ids.len() as u64) as usize].clone(),
                        _ => ids.last().unwrap().clone(),
                    };
                    let pending = c.pending.clone();
                    let before = c.host.spawned.len();
                    let reply = confirm(&mut c, &id, &random_optional(&mut rng));
                    if c.host.spawned.len() > before {
                        confirms += 1;
                        let p = pending.expect("a spawn without a pending scope");
                        assert_eq!(p.get("scope_id"), Some(&s(&id)), "a spawn for a scope_id that was not pending");
                    } else {
                        assert!(!ok(&reply));
                    }
                }
                5 => {
                    call(&mut c, r#"{"op":"cancel"}"#);
                }
                6 => match rng.below(4) {
                    0 => set_obs(&mut c, "output", s(if rng.coin() { "DP-1" } else { "DP-2" })),
                    1 => set_obs(&mut c, "model", if rng.coin() { s("one") } else { Value::Null }),
                    2 => set_obs(&mut c, "plugins", j(if rng.coin() { "[]" } else { r#"["p"]"# })),
                    _ => c.host.ownership = rng.coin(),
                },
                7 => {
                    // The runner finishes, one way or another.
                    if let Some(run) = c.run.as_ref().and_then(|r| r.get("run_id")).and_then(Value::as_str) {
                        let result = format!(r#"{{"run_id":"{run}","complete":{}}}"#, rng.coin());
                        c.host.results.insert(run.to_string(), j(&result));
                    }
                    c.tick();
                }
                _ => {
                    c.host.clock += rng.below(4) as f64;
                    c.tick();
                }
            }
        }
        let mut seen = HashSet::new();
        for consent in &c.host.written {
            let run = consent.get("run_id").and_then(Value::as_str).unwrap();
            assert!(seen.insert(run.to_string()), "run {run} consented twice");
            let scope = &shown[run];
            let Value::Object(entries) = consent else { panic!() };
            for (key, value) in entries {
                let in_scope = scope.get(key).unwrap_or_else(|| panic!("{key} added to the consent: {consent:?} vs {scope:?}"));
                if *in_scope == Value::Bool(false) {
                    assert_eq!(*value, Value::Bool(false), "{key} grew: {consent:?} vs {scope:?}");
                }
                if value != in_scope {
                    assert_eq!(*value, Value::Bool(false), "{key} changed other than off: {consent:?} vs {scope:?}");
                    shrinks += 1;
                }
            }
            if consent.get("tracking") == Some(&Value::Bool(false)) {
                assert!(consent.get("luma_driver").is_none() && consent.get("tracking_driver").is_none());
            }
        }
        assert_eq!(c.host.written.len(), c.host.spawned.len());
    }
    assert!(confirms > 1000 && shrinks > 100, "the sequences must exercise the confirm path: {confirms} {shrinks}");
}

/// One request line over a real socket pair: the server side decodes, handles and replies in the
/// wire format the Python panel speaks.
fn exchange(c: &mut C, client: &mut UnixStream, server: &mut BufReader<UnixStream>, fields: Vec<(String, Value)>, id: u64) -> Message {
    client.write_all(Request::new(fields, id).encode().as_bytes()).unwrap();
    let mut line = String::new();
    server.read_line(&mut line).unwrap();
    let request = Request::decode(line.trim_end_matches('\n').as_bytes()).unwrap();
    let reply = c.handle(&request);
    server.get_mut().write_all(reply.encode().as_bytes()).unwrap();
    let mut back = BufReader::new(client.try_clone().unwrap());
    let mut line = String::new();
    back.read_line(&mut line).unwrap();
    let decoded = Message::decode(line.trim_end_matches('\n').as_bytes()).unwrap();
    assert_eq!(decoded, reply, "the reply survives the wire unchanged");
    decoded
}

fn body(message: Message, id: u64) -> Value {
    match message {
        Message::Reply { request_id, body } => {
            assert_eq!(request_id, Some(id));
            Value::Object(body)
        }
        other => panic!("not a reply: {other:?}"),
    }
}

#[test]
fn begin_and_confirm_over_a_socket_pair() {
    let (mut client, server) = UnixStream::pair().unwrap();
    let mut server = BufReader::new(server);
    let mut c = Controller::new(FakeHost::new());
    let op = |name: &str| vec![("op".to_string(), s(name))];

    let status = body(exchange(&mut c, &mut client, &mut server, op("status"), 1), 1);
    assert!(ok(&status));
    let mut snap = status.clone();
    xr_workspace::controller::remove(&mut snap, "ok");
    assert!(is_snapshot(&snap), "{status:?}");

    let begun = body(exchange(&mut c, &mut client, &mut server, op("begin_start"), 2), 2);
    let scope_id = begun.get("scope_id").cloned().unwrap();
    assert!(c.host.spawned.is_empty());

    let mut forged = op("confirm_start");
    forged.push(("scope_id".into(), s("forged")));
    let refused = body(exchange(&mut c, &mut client, &mut server, forged, 3), 3);
    assert_eq!(error(&refused), Some("stale scope"));

    let mut confirm = op("confirm_start");
    confirm.push(("scope_id".into(), scope_id));
    confirm.push(("optional".into(), j(r#"{"tracking":false}"#)));
    let confirmed = body(exchange(&mut c, &mut client, &mut server, confirm, u64::from(u32::MAX) + 5), u64::from(u32::MAX) + 5);
    assert!(ok(&confirmed), "{confirmed:?}");
    assert_eq!(confirmed.get("state"), Some(&s(state::STARTING)));
    assert_eq!(c.host.spawned.len(), 1);
    assert_eq!(c.host.written[0].get("tracking"), Some(&Value::Bool(false)));

    // A state push for the change, in the event framing.
    let pushed = c.take_changed().unwrap();
    let line = Message::State { snapshot: pushed.clone() }.encode();
    assert_eq!(Message::decode(line.trim_end().as_bytes()).unwrap(), Message::State { snapshot: pushed });
}
