//! The XR controller's state machine and consent scope: a port of `tools/xr_controller/state.py`.
//!
//! The controller owns the one decision that needs the user: what a run may touch. `begin_start`
//! (or `begin_recover`) builds a scope and hands it to the panel with a fresh `scope_id`; only a
//! `confirm_*` that names that same `scope_id` spawns anything, and the confirmation is consumed by
//! the spawn whether it works or not. A confirmation may only shrink the scope (`optional` can turn
//! `tracking` or `mode_switch` off, never on), so what is written to the consent is always a subset
//! of what the panel showed. Any change in what the probe sees while a scope is pending (glasses,
//! outputs, plugins, journals, ...) drops the pending scope, so a stale click cannot confirm a scope
//! built for another machine state.
//!
//! Everything with a side effect (probing the system, the systemd units, the run's control socket,
//! the consent and result files, the settings store, the model-specific scope) sits behind
//! [`ControllerHost`], so this module is pure and its tests can drive thousands of sequences.
//!
//! # The socket server's contract
//!
//! The server (a separate module) owns the listener and the clients. Per accepted request line it
//! calls [`Controller::handle`] and writes the returned [`Message::Reply`]. On its timer it calls
//! [`Controller::tick`], then [`Controller::take_changed`], and pushes any returned snapshot to every
//! client as a [`Message::State`]. The wire format is `xr_wire::controller`, shared with the Python
//! panel, so the two stacks stay interchangeable.

use std::collections::BTreeSet;

use xr_wire::controller::{Message, Op, Request};
use xr_wire::json::Value;

use crate::supervisor::view::{SPACING_STEP, USER_OPS};
use crate::supervisor::video::KEY_OPS as VIDEO_KEY_OPS;

/// The pinned head-tracking binary a consent with Luma tracking runs (`panel_consent.LUMA_PIN`).
pub const LUMA_PIN: &str = "luma_driver";
/// The consent's tracking-driver line (`panel_consent.DRIVER_KEY`).
pub const DRIVER_KEY: &str = "tracking_driver";
/// The Pro 2's route block (`panel_consent.PRO2_KEY`).
pub const PRO2_KEY: &str = crate::pro2_route::PRO2_KEY;
/// The 3D (side-by-side) line of a consent (`sbs.SCOPE_KEY`).
pub const SBS_SCOPE_KEY: &str = "sbs_mode";
/// The scope entries a confirmation may turn off; nothing else is optional.
pub const OPTIONAL_KEYS: [&str; 2] = ["tracking", "mode_switch"];
/// The settings a running session takes live (`view.DEFAULT_SETTINGS`'s keys).
pub const LIVE_SETTINGS: [&str; 7] = [
    "spacing",
    "mode",
    "prediction",
    "stabilization",
    "video_quality",
    "environment",
    "immersive",
];
/// The modes a maximized desktop cycles through with `mode_next` (`view.MAXIMIZED_MODES`).
pub const MAXIMIZED_MODES: [&str; 2] = ["follow", "locked"];
/// What a status reply may carry into the snapshot.
const LIVE_KEYS: [&str; 8] = [
    "capabilities",
    "view",
    "status",
    "placements",
    "video",
    "desktops",
    "names",
    "immersive",
];
/// The probe's keys whose change drops a pending scope.
const SCOPE_INPUTS: [&str; 11] = [
    "model",
    "output",
    "instance",
    "monitors",
    "installed",
    "plugins",
    "journals",
    "external",
    "tracking_drivers",
    "tracking_override",
    "pro2_route",
];
/// How long a just-started (or just-stopped) run may go without a journal or unit before it counts.
const GRACE: f64 = 5.0;

/// The controller's states, by their wire names.
pub mod state {
    pub const NO_GLASSES: &str = "no-glasses";
    pub const IDLE: &str = "idle";
    pub const EXTERNAL_RUN: &str = "external-run";
    pub const CONFIRMING: &str = "confirming";
    pub const STARTING: &str = "starting";
    pub const RUNNING: &str = "running";
    pub const STOPPING: &str = "stopping";
    pub const FAILED: &str = "failed";
    pub const RECOVERY_NEEDED: &str = "recovery-needed";
    pub const CLI_RECOVERY: &str = "cli-recovery";
}

/// Everything the state machine needs from the system. Errors are messages for the panel.
pub trait ControllerHost {
    /// A monotonic clock in seconds.
    fn now(&mut self) -> f64;
    /// What the system looks like now (`detect.probe`): an object with `model`, `output`,
    /// `instance`, `monitors`, `installed`, `plugins`, `journals`, `external`, `config_rules`, ...
    fn probe(&mut self) -> Result<Value, String>;
    /// Whether the run's systemd unit is up.
    fn alive(&mut self, run_id: &str) -> Result<bool, String>;
    /// Send `op` (with `payload`) on the run's scoped control socket. `op: None` only proves a
    /// listener. `Ok(None)` (or a falsy reply) means control is unavailable.
    fn control(
        &mut self,
        run_id: &str,
        session: &Value,
        op: Option<&str>,
        payload: Option<&Value>,
    ) -> Result<Option<Value>, String>;
    /// Stop the run's unit.
    fn stop(&mut self, run_id: &str) -> Result<(), String>;
    /// Spawn the runner: `action` is `start` or `recover`, `consent` the consent file's path.
    fn spawn(
        &mut self,
        action: &str,
        consent: &str,
        journal: Option<&str>,
        rules: &[String],
    ) -> Result<(), String>;
    /// The run's consent, checked against its run id (`state._consent`).
    fn read_consent(&mut self, run_id: &str) -> Result<Value, String>;
    /// Validate a scope as a consent (`panel_consent.build_consent`).
    fn build_consent(&mut self, scope: &Value) -> Result<Value, String>;
    /// Write the consent privately; returns its file name.
    fn write_consent(&mut self, consent: &Value) -> Result<String, String>;
    /// The full path of a file in the state directory.
    fn state_path(&self, name: &str) -> String;
    /// The runner's result for this run, `None` when there is none yet.
    fn read_result(&mut self, run_id: &str) -> Result<Option<Value>, String>;
    /// Why the last Recover of this run stopped, `None` before a first one.
    fn recovery_reason(&mut self, run_id: &str) -> Option<String>;
    /// A fresh unguessable token (`secrets.token_hex(16)`).
    fn token(&mut self) -> String;
    /// The settings stored for `model`.
    fn load_settings(&mut self, model: &str) -> Value;
    /// Check merged settings (`state._check`).
    fn check_settings(&mut self, settings: &Value) -> Result<Value, String>;
    /// Store checked settings for `model`.
    fn save_settings(&mut self, model: &str, settings: &Value) -> Result<(), String>;
    /// The tracking driver these glasses always run.
    fn default_tracking_driver(&mut self, model: &str) -> Value;
    /// The fallback output (`pick_fallback`): a name, `"choose"`, or null.
    fn pick_fallback(&mut self, monitors: &Value, output: &Value, preferred: &Value) -> Value;
    /// Whether an ownership plugin is loaded already.
    fn ownership_plugin_loaded(&mut self, observed: &Value) -> bool;
    /// The model-specific start scope (`run_id` included): the Pro 2 route, tracking, the
    /// installed artifacts and the 3D line.
    fn start_scope(
        &mut self,
        observed: &Value,
        profile: &Value,
        fallback: &Value,
    ) -> Result<Value, String>;
    /// Seconds a start waits for head-tracking calibration.
    fn tracking_wait(&mut self, journal: &Value) -> i64;
    /// What head tracking these glasses run, for the panel (`state._driver_in_use`).
    fn tracking_in_use(&mut self, observed: &Value, running: Option<&Value>) -> Value;
    /// The Pro 2's panel row, `None` for anything else.
    fn pro2_snapshot(&mut self, observed: &Value) -> Option<Value>;
    /// Save the Pro 2 projection.
    fn save_projection(&mut self, projection: Option<&Value>) -> Result<(), String>;
    /// Validate placements for the `placement` op.
    fn validate_placements(&mut self, placements: &Value) -> Result<Value, String>;
    /// Validate a `video_open` request.
    fn validate_video(&mut self, request: &Value) -> Result<Value, String>;
    /// The incident monitor's latest incident, or null.
    fn incident(&mut self) -> Value;
}

/// The state machine. Fields mirror `state.Controller`.
pub struct Controller<H: ControllerHost> {
    pub host: H,
    pub state: &'static str,
    pub pending: Option<Value>,
    /// The run's journal as probed, or `{run_id}` between a confirm and its first journal.
    pub run: Option<Value>,
    pub error: Option<String>,
    pub observed: Value,
    pub started: Option<f64>,
    pub last_failure: Option<String>,
    live: Vec<(String, Value)>,
    last_status: Option<f64>,
    status_run: Option<String>,
    pub stop_run: Option<String>,
    pub unit_up: Option<bool>,
    pushed: Option<Value>,
}

type Fields = Vec<(String, Value)>;

fn s(v: &str) -> Value {
    Value::String(v.into())
}

fn opt_s(v: Option<&str>) -> Value {
    v.map_or(Value::Null, s)
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(x)) => !x.is_empty(),
        Some(Value::Array(x)) => !x.is_empty(),
        Some(Value::Object(x)) => !x.is_empty(),
        Some(Value::Bool(true)) => true,
    }
}

fn fields(v: &Value) -> Fields {
    match v {
        Value::Object(f) => f.clone(),
        _ => Vec::new(),
    }
}

/// Set `key` in an object, in place when present, appended otherwise.
pub fn set(v: &mut Value, key: &str, value: Value) {
    if let Value::Object(f) = v {
        match f.iter_mut().find(|(k, _)| k == key) {
            Some(entry) => entry.1 = value,
            None => f.push((key.into(), value)),
        }
    }
}

/// Remove `key` from an object.
pub fn remove(v: &mut Value, key: &str) {
    if let Value::Object(f) = v {
        f.retain(|(k, _)| k != key);
    }
}

fn merge(base: &Value, over: &Value) -> Value {
    let mut out = match base {
        Value::Object(_) => base.clone(),
        _ => Value::Object(Vec::new()),
    };
    for (k, v) in fields(over) {
        set(&mut out, &k, v);
    }
    out
}

fn run_id(run: &Value) -> String {
    run.get("run_id").and_then(Value::as_str).unwrap_or("").to_string()
}

fn unavailable(reply: Result<Option<Value>, String>) -> Result<(), String> {
    match reply {
        Ok(r) if truthy(r.as_ref()) => Ok(()),
        Ok(_) => Err("run control unavailable".into()),
        Err(e) => Err(e),
    }
}

impl<H: ControllerHost> Controller<H> {
    /// A controller as the service starts it: never with a run of its own, then one tick.
    pub fn new(host: H) -> Self {
        let mut c = Controller {
            host,
            state: state::NO_GLASSES,
            pending: None,
            run: None,
            error: None,
            observed: Value::Object(Vec::new()),
            started: None,
            last_failure: None,
            live: Vec::new(),
            last_status: None,
            status_run: None,
            stop_run: None,
            unit_up: None,
            pushed: None,
        };
        c.tick();
        c
    }

    fn obs(&self, key: &str) -> Option<&Value> {
        self.observed.get(key)
    }

    fn model(&self) -> Option<String> {
        self.obs("model").and_then(Value::as_str).map(str::to_string)
    }

    fn live(&self, key: &str) -> Option<&Value> {
        self.live.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    fn set_state(&mut self, next: &'static str) {
        if self.state != next {
            self.pending = None;
        }
        self.state = next;
        if next != state::RUNNING {
            self.live.clear();
            self.last_status = None;
            self.status_run = None;
        }
    }

    fn at_rest(&self) -> &'static str {
        if truthy(self.obs("external")) {
            state::EXTERNAL_RUN
        } else if truthy(self.obs("model")) {
            state::IDLE
        } else {
            state::NO_GLASSES
        }
    }

    fn cli_recovery(&mut self) {
        self.unit_up = match &self.run {
            Some(run) => self.host.alive(&run_id(run)).ok(),
            None => None,
        };
        self.set_state(state::CLI_RECOVERY);
    }

    fn poll_status(&mut self, journal: &Value) {
        let now = self.host.now();
        let id = run_id(journal);
        if self.status_run.as_deref() != Some(&id) {
            self.live.clear();
            self.last_status = None;
            self.status_run = Some(id.clone());
        }
        if matches!(self.last_status, Some(t) if now - t < 1.0) {
            return;
        }
        self.last_status = Some(now);
        let session = journal.get("session").cloned().unwrap_or(Value::Null);
        let reply = self.host.control(&id, &session, Some("status"), None).ok().flatten();
        self.live = match reply {
            Some(Value::Object(f)) => f.into_iter().filter(|(k, _)| LIVE_KEYS.contains(&k.as_str())).collect(),
            _ => Vec::new(),
        };
    }

    /// One look at the system. A fault fails the controller visibly instead of guessing.
    pub fn tick(&mut self) {
        if let Err(e) = self.tick_inner() {
            self.error = Some(e);
            self.pending = None;
            self.set_state(state::FAILED);
        }
    }

    fn tick_inner(&mut self) -> Result<(), String> {
        let old = std::mem::replace(&mut self.observed, Value::Object(Vec::new()));
        self.observed = self.host.probe()?;
        self.error = None;
        self.unit_up = None;
        if self.pending.is_some() && SCOPE_INPUTS.iter().any(|k| old.get(k) != self.observed.get(k)) {
            self.pending = None;
            self.state = state::IDLE;
        }
        let journals = match self.obs("journals") {
            Some(Value::Array(j)) => j.clone(),
            _ => Vec::new(),
        };
        if journals.len() > 1 {
            self.error = Some("multiple unfinished journals require CLI recovery".into());
            self.cli_recovery();
            return Ok(());
        }
        if let Some(j) = journals.into_iter().next() {
            let id = run_id(&j);
            self.run = Some(j.clone());
            if self.host.read_consent(&id).is_err() {
                self.cli_recovery();
                return Ok(());
            }
            if self.host.alive(&id)? {
                if self.state == state::STOPPING {
                    return Ok(());
                }
                let session = j.get("session").cloned().unwrap_or(Value::Null);
                if truthy(Some(&session)) && truthy(self.host.control(&id, &session, None, None)?.as_ref()) {
                    self.set_state(state::RUNNING);
                    self.poll_status(&j);
                    return Ok(());
                }
                if !truthy(Some(&session)) {
                    self.error = Some(if j.get("phase").and_then(Value::as_str) == Some("tracking") {
                        format!(
                            "Starting head tracking and waiting for calibration (up to {} seconds). Keep the glasses still.",
                            self.host.tracking_wait(&j)
                        )
                    } else {
                        "Preparing XR session…".into()
                    });
                    self.set_state(state::STARTING);
                } else {
                    self.error = Some("XR renderer control unavailable. Stop this session before starting again.".into());
                    self.set_state(state::FAILED);
                }
                return Ok(());
            }
            let now = self.host.now();
            let young = matches!(self.started, Some(t) if now - t < GRACE);
            if (self.state == state::STARTING || self.state == state::STOPPING) && young {
                return Ok(());
            }
            if self.state != state::CONFIRMING && self.state != state::STARTING {
                self.set_state(state::RECOVERY_NEEDED);
            } else if self.state == state::STARTING {
                self.set_state(state::RECOVERY_NEEDED);
            }
            if self.state == state::RECOVERY_NEEDED {
                self.error = self.host.recovery_reason(&id);
            }
            return Ok(());
        }
        if let Some(run) = self.run.clone() {
            let id = run_id(&run);
            if self.host.alive(&id)? {
                return Ok(());
            }
            if let Some(result) = self.host.read_result(&id)? {
                if result.get("run_id").and_then(Value::as_str) != Some(id.as_str()) {
                    return Err("runner result identity mismatch".into());
                }
                let rc_ok = matches!(result.get("renderer_returncode"), None | Some(Value::Null))
                    || result.get("renderer_returncode").and_then(Value::as_int) == Some(0);
                let error = result.get("error").filter(|e| truthy(Some(e)));
                if error.is_some() || !rc_ok || result.get("complete") != Some(&Value::Bool(true)) {
                    self.last_failure = Some(match error {
                        Some(Value::String(e)) => e.clone(),
                        Some(other) => xr_wire::json::encode(other, xr_wire::json::Style::COMPACT),
                        None => {
                            let phase = match result.get("phase") {
                                None => "run".to_string(),
                                Some(Value::String(p)) => p.clone(),
                                Some(other) => xr_wire::json::encode(other, xr_wire::json::Style::COMPACT),
                            };
                            format!("runner failed during {phase}")
                        }
                    });
                }
                self.run = None;
                self.started = None;
                let rest = self.at_rest();
                self.set_state(rest);
                return Ok(());
            }
        }
        let now = self.host.now();
        if (self.state == state::STARTING || self.state == state::STOPPING)
            && matches!(self.started, Some(t) if now - t < GRACE)
        {
            return Ok(());
        }
        if self.state == state::STARTING {
            self.error = Some("runner exited before creating a journal".into());
            self.set_state(state::FAILED);
            return Ok(());
        }
        self.run = None;
        if self.state != state::CONFIRMING {
            let rest = self.at_rest();
            self.set_state(rest);
        }
        Ok(())
    }

    fn start_progress(&mut self) -> (Value, Value) {
        let Some(run) = self.run.clone().filter(|_| self.state == state::STARTING) else {
            return (Value::Null, Value::Null);
        };
        let consent = self.host.read_consent(&run_id(&run)).ok();
        let steps: Vec<&str> = match &consent {
            Some(c) if c.get("model").and_then(Value::as_str) == Some("pro2") => vec!["tracking", "desktops"],
            _ => {
                let tracking = consent.as_ref().is_none_or(|c| c.get("tracking") != Some(&Value::Bool(false)));
                let mut steps = vec!["verify"];
                if tracking {
                    steps.push("tracking");
                }
                steps.extend(["plugin", "desktops"]);
                steps
            }
        };
        let phase = match run.get("phase") {
            None | Some(Value::Null) => Some(steps[0]),
            Some(p) if p.as_str() == Some("verify") => Some(steps[1]),
            Some(p) => p.as_str().and_then(|p| steps.iter().copied().find(|s| *s == p)),
        };
        (Value::Array(steps.into_iter().map(s).collect()), opt_s(phase))
    }

    /// The snapshot every reply and state event carries, in `SNAPSHOT_KEYS` order.
    pub fn snapshot(&mut self) -> Value {
        let monitors = self.obs("monitors").cloned().unwrap_or(Value::Array(Vec::new()));
        let output = self.obs("output").cloned().unwrap_or(Value::Null);
        let preferred = match self.model() {
            Some(m) => self.host.load_settings(&m).get("fallback").cloned().unwrap_or(Value::Null),
            None => Value::Null,
        };
        let fallback = self.host.pick_fallback(&monitors, &output, &preferred);
        let mut candidates = Vec::new();
        if let Value::Array(ms) = &monitors {
            for m in ms {
                let one = Value::Array(vec![m.clone()]);
                if self.host.pick_fallback(&one, &output, &Value::Null) != Value::Null {
                    candidates.push(m.get("name").cloned().unwrap_or(Value::Null));
                }
            }
        }
        let running = self.state == state::RUNNING;
        let live = |key: &str, idle: Value| if running { self.live(key).cloned().unwrap_or(idle) } else { idle };
        let journal = match &self.run {
            Some(run) => run.get("path").cloned().unwrap_or(Value::Null),
            None => match self.obs("external") {
                Some(Value::Array(e)) => e.first().and_then(|j| j.get("path")).cloned().unwrap_or(Value::Null),
                _ => Value::Null,
            },
        };
        let reason = match self.error.as_ref().or(self.last_failure.as_ref()) {
            Some(r) => s(r),
            None => self.obs("reason").cloned().unwrap_or(Value::Null),
        };
        let capabilities = if running {
            self.live("capabilities").cloned().unwrap_or_else(|| Value::Array(USER_OPS.iter().map(|o| s(o)).collect()))
        } else {
            Value::Array(Vec::new())
        };
        let mut out: Fields = vec![
            ("fallback".into(), fallback),
            ("fallback_candidates".into(), Value::Array(candidates)),
            ("pending".into(), self.pending.clone().unwrap_or(Value::Null)),
            ("state".into(), s(self.state)),
            ("model".into(), opt_s(self.model().as_deref())),
            ("output".into(), output),
            ("reason".into(), reason),
            ("run_id".into(), self.run.as_ref().and_then(|r| r.get("run_id")).cloned().unwrap_or(Value::Null)),
            ("journal".into(), journal),
            ("capabilities".into(), capabilities),
            ("view".into(), live("view", Value::Object(Vec::new()))),
            ("status".into(), live("status", Value::Object(Vec::new()))),
            ("placements".into(), live("placements", Value::Null)),
            ("desktops".into(), live("desktops", Value::Null)),
            ("names".into(), live("names", Value::Null)),
            ("video".into(), live("video", Value::Null)),
        ];
        let immersive = live("immersive", Value::Null);
        let consent = match (&self.run, running) {
            (Some(run), true) => Some(self.host.read_consent(&run_id(run)).unwrap_or(Value::Null)),
            _ => None,
        };
        let observed = self.observed.clone();
        out.push(("tracking_in_use".into(), self.host.tracking_in_use(&observed, consent.as_ref())));
        let (steps, phase) = self.start_progress();
        out.push(("start_steps".into(), steps));
        out.push(("start_phase".into(), phase));
        if let Some(pro2) = self.host.pro2_snapshot(&observed) {
            out.push(("pro2".into(), pro2));
        }
        out.push(("immersive".into(), immersive));
        out.push(("incident".into(), self.host.incident()));
        Value::Object(out)
    }

    /// The snapshot when it differs from the last one this returned: the server's state event.
    pub fn take_changed(&mut self) -> Option<Value> {
        let now = self.snapshot();
        if self.pushed.as_ref() == Some(&now) {
            return None;
        }
        self.pushed = Some(now.clone());
        Some(now)
    }

    /// Answer one request line as `state.Controller.request` does: `ok`, the snapshot, then the
    /// op's result (`ok: true`) or `error`.
    pub fn request(&mut self, msg: &Value) -> Fields {
        let result = match msg {
            Value::Object(_) => self.request_inner(msg),
            _ => Err("invalid request".into()),
        };
        let snapshot = fields(&self.snapshot());
        match result {
            Ok(extra) => {
                let mut out = Value::Object(snapshot);
                for (k, v) in extra {
                    set(&mut out, &k, v);
                }
                remove(&mut out, "ok");
                let mut body = vec![("ok".to_string(), Value::Bool(true))];
                body.extend(fields(&out));
                body
            }
            Err(e) => {
                let mut body = vec![("ok".to_string(), Value::Bool(false))];
                body.extend(snapshot);
                body.push(("error".into(), s(&e)));
                body
            }
        }
    }

    /// A decoded request in, the reply out, its `request_id` carried back.
    pub fn handle(&mut self, request: &Request) -> Message {
        Message::Reply {
            request_id: request.request_id,
            body: self.request(&request.message),
        }
    }

    fn running_model(&mut self) -> Result<String, String> {
        let run = self.run.clone().ok_or("no run")?;
        let consent = self.host.read_consent(&run_id(&run))?;
        Ok(consent.get("model").and_then(Value::as_str).unwrap_or("unknown").to_string())
    }

    fn run_control(&mut self, op: &str, payload: Option<&Value>) -> Result<(), String> {
        let run = self.run.clone().ok_or("run control unavailable")?;
        let session = run.get("session").cloned().unwrap_or(Value::Null);
        unavailable(self.host.control(&run_id(&run), &session, Some(op), payload))
    }

    fn request_inner(&mut self, msg: &Value) -> Result<Fields, String> {
        let op = msg.get("op").and_then(Value::as_str).and_then(Op::from_name).ok_or("unknown operation")?;
        match op {
            Op::Status => Ok(Vec::new()),
            Op::GetSettings => {
                let model = self.model().unwrap_or_else(|| "unknown".into());
                Ok(vec![("settings".into(), self.host.load_settings(&model))])
            }
            Op::Cancel => {
                if self.state != state::CONFIRMING {
                    return Err("busy".into());
                }
                self.pending = None;
                let next = if self.run.is_some() { state::RECOVERY_NEEDED } else { state::IDLE };
                self.set_state(next);
                Ok(Vec::new())
            }
            Op::SetSettings => self.set_settings(msg),
            Op::SetPro2Projection => {
                if self.state != state::IDLE || self.model().as_deref() != Some("pro2") || !truthy(self.obs("pro2_route")) {
                    return Err("busy".into());
                }
                self.host.save_projection(msg.get("projection"))?;
                Ok(Vec::new())
            }
            Op::BeginStart => self.begin_start(),
            Op::BeginRecover => self.begin_recover(),
            Op::ConfirmStart => self.confirm(msg, "start"),
            Op::ConfirmRecover => self.confirm(msg, "recover"),
            Op::VideoOpen => {
                if self.state != state::RUNNING {
                    return Err("start an XR session first".into());
                }
                let request = Value::Object(
                    ["path", "url", "web", "format"]
                        .iter()
                        .filter_map(|k| msg.get(k).map(|v| (k.to_string(), v.clone())))
                        .collect(),
                );
                let payload = self.host.validate_video(&request)?;
                self.run_control("video_open", Some(&payload))?;
                self.last_status = None;
                Ok(vec![("forwarded".into(), Value::Bool(true))])
            }
            Op::Stop | Op::ViewOp => {
                let stop = op == Op::Stop;
                let stoppable = stop && self.run.is_some() && (self.state == state::STARTING || self.state == state::FAILED);
                if self.state != state::RUNNING && !stoppable {
                    return Err("busy".into());
                }
                if stop {
                    let id = run_id(self.run.as_ref().unwrap());
                    self.stop_run = Some(id.clone());
                    self.host.stop(&id)?;
                    self.set_state(state::STOPPING);
                    self.started = Some(self.host.now());
                    return Ok(Vec::new());
                }
                let mut operation = msg.get("operation").or_else(|| msg.get("view_op")).cloned();
                if let Some(Value::Object(_)) = operation {
                    operation = operation.and_then(|o| o.get("op").cloned());
                }
                let name = operation.as_ref().and_then(Value::as_str).unwrap_or("");
                if !USER_OPS.contains(&name) && !VIDEO_KEY_OPS.contains(&name) {
                    return Err("invalid view operation".into());
                }
                let name = name.to_string();
                self.run_control(&name, None)?;
                Ok(Vec::new())
            }
        }
    }

    fn set_settings(&mut self, msg: &Value) -> Result<Fields, String> {
        if self.state != state::IDLE && self.state != state::RUNNING {
            return Err("busy".into());
        }
        let mut settings = msg.get("settings").cloned().ok_or("'settings'")?;
        if !matches!(settings, Value::Object(_)) {
            return Err("invalid settings".into());
        }
        let running = self.state == state::RUNNING;
        if let Some(over @ Value::Object(_)) = settings.get("immersive").cloned() {
            let model = if running { self.running_model()? } else { self.model().ok_or("'model'")? };
            let stored = self.host.load_settings(&model).get("immersive").cloned().unwrap_or(Value::Object(Vec::new()));
            set(&mut settings, "immersive", merge(&stored, &over));
        }
        let keys: BTreeSet<String> = fields(&settings).into_iter().map(|(k, _)| k).collect();
        if !running {
            let model = self.model().ok_or("'model'")?;
            let profile = self.host.load_settings(&model);
            let default = self.host.default_tracking_driver(&model);
            if settings.get("tracking_driver").is_some_and(|d| *d != default) {
                return Err("the tracking driver follows the glasses and is not a choice".into());
            }
            let checked = self.host.check_settings(&merge(&profile, &settings))?;
            self.host.save_settings(&model, &checked)?;
            return Ok(Vec::new());
        }
        if keys.iter().any(|k| k != "placements" && !LIVE_SETTINGS.contains(&k.as_str())) {
            return Err("resolution/settings locked while running".into());
        }
        let model = self.running_model()?;
        let current = self.host.load_settings(&model);
        let target = self.host.check_settings(&merge(&current, &settings))?;
        let forwarded = vec![("forwarded".to_string(), Value::Bool(true))];
        if keys.contains("placements") {
            if keys.len() != 1 {
                return Err("send placements on their own".into());
            }
            let desktops = self.host.validate_placements(settings.get("placements").unwrap())?;
            self.run_control("placement", Some(&Value::Object(vec![("desktops".into(), desktops)])))?;
            self.last_status = None;
            return Ok(forwarded);
        }
        let mut operations: Vec<&str> = Vec::new();
        if keys.contains("mode") {
            let view = self.live("view").cloned().unwrap_or(Value::Object(Vec::new()));
            let want = target.get("mode").and_then(Value::as_str);
            let have = view.get("mode").and_then(Value::as_str);
            if want != have {
                if !truthy(view.get("zoomed")) {
                    return Err("the overview is always anchored: maximize a desktop to choose follow or locked".into());
                }
                let to = want.and_then(|w| MAXIMIZED_MODES.iter().position(|m| *m == w));
                let to = to.ok_or("anchor is the overview: un-maximize (Overview) to go back to it")?;
                let from = have.and_then(|h| MAXIMIZED_MODES.iter().position(|m| *m == h)).ok_or("'mode'")?;
                let n = MAXIMIZED_MODES.len();
                operations.extend(std::iter::repeat_n("mode_next", (to + n - from) % n));
            }
        }
        if keys.contains("spacing") {
            let want = target.get("spacing").and_then(Value::as_int).ok_or("'spacing'")?;
            let have = current.get("spacing").and_then(Value::as_int).ok_or("'spacing'")?;
            let delta = want - have;
            if delta % i128::from(SPACING_STEP) != 0 {
                return Err("live spacing must use 20 pixel steps".into());
            }
            let step = if delta > 0 { "spacing_up" } else { "spacing_down" };
            operations.extend(std::iter::repeat_n(step, (delta.unsigned_abs() / SPACING_STEP as u128) as usize));
        }
        if keys.contains("prediction") && target.get("prediction") != current.get("prediction") {
            operations.push("prediction");
        }
        if operations.len() > 8 {
            return Err("too many live operations; use smaller steps".into());
        }
        if keys.contains("stabilization") {
            let strength = target.get("stabilization").cloned().unwrap_or(Value::Null);
            self.run_control("stabilization", Some(&Value::Object(vec![("strength".into(), strength)])))?;
        }
        for operation in operations {
            self.run_control(operation, None)?;
        }
        if keys.contains("environment") && target.get("environment") != current.get("environment") {
            let env = target.get("environment").cloned().unwrap_or(Value::Null);
            self.run_control(crate::supervisor::environment::SET_OP, Some(&env))?;
        }
        let failed = self.live("immersive").is_some_and(|i| truthy(i.get("failed")));
        if keys.contains("immersive") && (target.get("immersive") != current.get("immersive") || failed) {
            let imm = target.get("immersive").cloned().unwrap_or(Value::Null);
            self.run_control(crate::supervisor::immersive::SET_OP, Some(&imm))?;
        }
        Ok(forwarded)
    }

    fn begin_start(&mut self) -> Result<Fields, String> {
        self.tick();
        if self.state != state::IDLE {
            return Err("busy".into());
        }
        let model = self.model().ok_or("'model'")?;
        let profile = self.host.load_settings(&model);
        let monitors = self.obs("monitors").cloned().unwrap_or(Value::Array(Vec::new()));
        let output = self.obs("output").cloned().unwrap_or(Value::Null);
        let preferred = profile.get("fallback").cloned().unwrap_or(Value::Null);
        let fallback = self.host.pick_fallback(&monitors, &output, &preferred);
        if !truthy(Some(&output)) || fallback == Value::Null || fallback.as_str() == Some("choose") {
            return Err(match self.obs("reason").filter(|r| truthy(Some(r))) {
                Some(Value::String(r)) => r.clone(),
                _ => "choose a physical fallback output".into(),
            });
        }
        let observed = self.observed.clone();
        if self.host.ownership_plugin_loaded(&observed) {
            return Err("ownership plugin already loaded".into());
        }
        let scope = self.host.start_scope(&observed, &profile, &fallback)?;
        self.host.build_consent(&scope)?;
        self.last_failure = None;
        self.set_state(state::CONFIRMING);
        let pending = Value::Object(vec![
            ("action".into(), s("start")),
            ("scope".into(), scope),
            ("scope_id".into(), s(&self.host.token())),
        ]);
        self.pending = Some(pending.clone());
        Ok(fields(&pending))
    }

    fn begin_recover(&mut self) -> Result<Fields, String> {
        if self.state != state::RECOVERY_NEEDED {
            return Err("busy or CLI recovery required".into());
        }
        let run = self.run.clone().ok_or("busy or CLI recovery required")?;
        let consent = self.host.read_consent(&run_id(&run))?;
        let names: Vec<Value> = match run.get("operations") {
            Some(Value::Array(ops)) => ops
                .iter()
                .map(|r| r.get("operation").and_then(|o| o.get("name")).cloned().unwrap_or(Value::Null))
                .collect(),
            _ => Vec::new(),
        };
        let rules: Vec<Value> = match self.obs("config_rules") {
            Some(Value::Array(rules)) => rules
                .iter()
                .filter(|r| {
                    r.get("name").is_some_and(|n| names.contains(n)) && truthy(r.get("removable"))
                })
                .cloned()
                .collect(),
            _ => Vec::new(),
        };
        self.set_state(state::CONFIRMING);
        let pending = Value::Object(vec![
            ("action".into(), s("recover")),
            ("scope".into(), consent),
            ("scope_id".into(), s(&self.host.token())),
            ("journal".into(), run.get("path").cloned().unwrap_or(Value::Null)),
            ("rules".into(), Value::Array(rules)),
        ]);
        self.pending = Some(pending.clone());
        Ok(fields(&pending))
    }

    fn confirm(&mut self, msg: &Value, action: &str) -> Result<Fields, String> {
        self.tick();
        let pending = match &self.pending {
            Some(p)
                if self.state == state::CONFIRMING
                    && msg.get("scope_id") == p.get("scope_id")
                    && p.get("action").and_then(Value::as_str) == Some(action) =>
            {
                p.clone()
            }
            _ => return Err("stale scope".into()),
        };
        let mut scope = pending.get("scope").cloned().unwrap_or(Value::Null);
        let name = if action == "start" {
            let optional = msg.get("optional").cloned().unwrap_or(Value::Object(Vec::new()));
            let Value::Object(items) = optional else {
                return Err("invalid optional scope".into());
            };
            if items.iter().any(|(k, v)| !OPTIONAL_KEYS.contains(&k.as_str()) || !matches!(v, Value::Bool(_))) {
                return Err("invalid optional scope".into());
            }
            let items = if scope.get(PRO2_KEY).is_some() { Vec::new() } else { items };
            for (k, v) in items {
                if v == Value::Bool(false) {
                    set(&mut scope, &k, Value::Bool(false));
                } else if scope.get(&k) == Some(&Value::Bool(false)) {
                    return Err("cannot expand confirmed scope".into());
                }
            }
            if scope.get("tracking") == Some(&Value::Bool(false)) {
                remove(&mut scope, LUMA_PIN);
                remove(&mut scope, DRIVER_KEY);
            }
            if scope.get("mode_switch") == Some(&Value::Bool(false))
                && scope.get("model").and_then(Value::as_str) != Some("pro2")
            {
                remove(&mut scope, SBS_SCOPE_KEY);
            }
            let consent = self.host.build_consent(&scope)?;
            let name = self.host.write_consent(&consent)?;
            self.run = Some(Value::Object(vec![("run_id".into(), scope.get("run_id").cloned().unwrap_or(Value::Null))]));
            name
        } else {
            format!("{}.json", run_id(&scope))
        };
        // A Recover is on the way out, like a Stop: its unit is up while the dead run's control socket refuses, which
        // as 'starting' read as a failed start, with a Stop button that interrupted the recovery.
        self.set_state(if action == "recover" { state::STOPPING } else { state::STARTING });
        self.pending = None;
        self.started = Some(self.host.now());
        let rules: Vec<String> = match pending.get("rules") {
            Some(Value::Array(r)) => r.iter().filter_map(|r| r.get("name").and_then(Value::as_str)).map(str::to_string).collect(),
            _ => Vec::new(),
        };
        let path = self.host.state_path(&name);
        let journal = pending.get("journal").and_then(Value::as_str).map(str::to_string);
        if let Err(e) = self.host.spawn(action, &path, journal.as_deref(), &rules) {
            self.error = Some(e.clone());
            self.set_state(state::FAILED);
            return Err(e);
        }
        Ok(Vec::new())
    }
}
