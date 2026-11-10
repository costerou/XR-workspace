//! The incident monitor: `IncidentMonitor` in `tools/xr_controller/incident_monitor.py`, a thread in the controller
//! service that outlives runs (docs/technical.md, Incident reports).
//!
//! The controller's tick hands it one observation a second through `Feed` and never waits for it. The thread feeds
//! the pure detectors, reads the host for what the tick does not know, and writes reports, so a slow journal, core
//! dump or hook cannot hold up the panel. It only reads: no unit, driver or compositor is ever touched.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use indexmap::IndexMap;
use serde_json::{Map, Value, json};

use super::detect::{
    Correlator, DriverLog, End, Episode, KernelLog, RunWatch, Signal, classify_end, crash_expected,
    exit_metrics, parse_unit_exit,
};
use super::py::{as_num, as_str, float, get, opt_str, py_eq, py_str, truthy};
use super::report::{
    HOOK_TIMEOUT, RateLimit, Reports, Scrubber, amend_bundle, build_bundle, markdown, notify_text,
    pin_of, run_hook,
};
use super::sources::Sources;

pub const CONTROLLER_UNIT: &str = "viture-workspace-controller.service";
pub const LIVE: [&str; 4] = ["starting", "running", "stopping", "failed"];
pub const STRANDED: [&str; 2] = ["recovery-needed", "cli-recovery"];
/// Kernel and driver logs, while a session runs.
pub const POLL_S: f64 = 2.0;
/// systemd-coredump takes a moment; a crash report waits this long for it at most.
pub const CORE_WAIT_S: f64 = 20.0;
/// The last lines of each journal and of the driver log.
pub const LOG_LINES: usize = 200;
pub const KERNEL_BEFORE_S: f64 = 90.0;
pub const KERNEL_AFTER_S: f64 = 30.0;
pub const MAX_ENDED: usize = 64;
pub const QUEUE: usize = 256;
pub const INTERVAL: Duration = Duration::from_millis(250);

fn unit(run_id: &str) -> String {
    format!("viture-xr-{run_id}.service")
}

fn state_of(o: &Value) -> Option<&str> {
    as_str(get(o, "state"))
}

fn stranded(o: &Value) -> bool {
    state_of(o).is_some_and(|s| STRANDED.contains(&s))
}

/// 'cli-recovery' means the controller cannot act on a run: two journals, or a consent it cannot read. It says nothing
/// about the run, so only a unit known to be gone makes it stranded. Anything else is a run that may be fine.
fn held(o: &Value) -> bool {
    state_of(o) == Some("cli-recovery") && get(o, "unit_up") != &Value::Bool(false)
}

pub type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;
pub type Log = Arc<dyn Fn(&str) + Send + Sync>;

pub fn wall_clock() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

/// `profiles.log`: one line on stderr, the controller's journal.
pub fn stderr_log() -> Log {
    Arc::new(|text| eprintln!("xr-workspace: {text}"))
}

/// The controller's side: hands observations over, never blocks on the monitor's work, never fails.
#[derive(Clone)]
pub struct Feed {
    queue: Arc<Mutex<VecDeque<Value>>>,
    clock: Clock,
}

impl Feed {
    pub fn new(clock: Clock) -> Feed {
        Feed {
            queue: Arc::new(Mutex::new(VecDeque::with_capacity(QUEUE))),
            clock,
        }
    }

    /// `dict(observation, at=observation.get('at') or clock())`, dropping the oldest past QUEUE.
    pub fn observe(&self, observation: &Value) {
        let mut o = observation.as_object().cloned().unwrap_or_default();
        if !truthy(o.get("at").unwrap_or(&Value::Null)) {
            o.insert("at".into(), float((self.clock)()));
        }
        let Ok(mut queue) = self.queue.lock() else {
            return;
        };
        if queue.len() == QUEUE {
            queue.pop_front();
        }
        queue.push_back(Value::Object(o));
    }

    fn take(&self) -> Option<Value> {
        self.queue.lock().ok()?.pop_front()
    }
}

/// A run being watched: its detectors, where its logs had got to, and how it ended.
pub struct Live {
    pub watch: RunWatch,
    pub kernel: KernelLog,
    pub driver: DriverLog,
    pub armed: bool,
    pub cursor: Option<String>,
    pub offset: Option<u64>,
    pub next_poll: f64,
    pub instance: Option<String>,
    pub hypr_pid: Option<i64>,
    pub result: Value,
    pub unit: Value,
    pub report_ids: Vec<String>,
}

type Shared = Rc<RefCell<Live>>;

impl Live {
    fn new(run_id: &str, consent: &Value, at: f64) -> Live {
        Live {
            watch: RunWatch::new(run_id, consent, at),
            kernel: KernelLog::default(),
            driver: DriverLog::default(),
            armed: false,
            cursor: None,
            offset: None,
            next_poll: 0.0,
            instance: as_str(get(consent, "instance")).map(str::to_owned),
            hypr_pid: None,
            result: Value::Null,
            unit: Value::Null,
            report_ids: Vec::new(),
        }
    }

    fn saved(&self, boot: &Option<String>) -> Value {
        let w = &self.watch;
        json!({
            "boot": boot,
            "run_id": w.run_id,
            "instance": self.instance,
            "hypr_pid": self.hypr_pid,
            "running_since": w.running_since,
            "stopped": w.stopped,
            "ports": self.kernel.ports.iter().collect::<Vec<_>>(),
            "report_ids": self.report_ids,
        })
    }
}

/// Hands a written report on: by default, the user's on-incident hook on a thread of its own.
pub type HandOn = Box<dyn FnMut(&Path)>;
pub type Notify = Box<dyn FnMut(&str) -> Result<(), String>>;

pub struct IncidentMonitor {
    sources: Box<dyn Sources>,
    reports: Reports,
    scrub: Scrubber,
    notify: Notify,
    hand_on: HandOn,
    log: Log,
    feed: Feed,
    correlator: Correlator<Shared>,
    live: Option<Shared>,
    /// run_id: its state since it ended (a failed Recover).
    after: Map<String, Value>,
    resumed: bool,
    next_core_check: f64,
    said: Option<String>,
    limit: RateLimit,
    ended: IndexMap<String, Value>,
    saved_watch: Option<Value>,
    latest: Option<Map<String, Value>>,
}

impl IncidentMonitor {
    pub fn new(
        sources: Box<dyn Sources>,
        reports: Reports,
        feed: Feed,
        scrub: Scrubber,
        log: Log,
    ) -> IncidentMonitor {
        let saved = reports.load_state();
        let pick = |key: &str| saved.get(key).cloned().unwrap_or_default();
        let ended = match pick("ended") {
            Value::Object(ended) => ended.into_iter().collect(),
            _ => IndexMap::new(),
        };
        let latest = match pick("latest") {
            Value::Object(latest) => Some(latest),
            _ => None,
        };
        IncidentMonitor {
            sources,
            scrub,
            notify: Box::new(notify_send),
            hand_on: Box::new(|_| {}),
            log,
            feed,
            correlator: Correlator::default(),
            live: None,
            after: Map::new(),
            resumed: false,
            next_core_check: 0.0,
            said: None,
            limit: RateLimit::restore(&pick("limit")),
            ended,
            saved_watch: Some(pick("watch")).filter(Value::is_object),
            latest,
            reports,
        }
    }

    pub fn with_notify(mut self, notify: Notify) -> IncidentMonitor {
        self.notify = notify;
        self
    }

    pub fn with_hand_on(mut self, hand_on: HandOn) -> IncidentMonitor {
        self.hand_on = hand_on;
        self
    }

    /// The default hand-on: `hook` (when there is one) on a thread of its own, its outcome in the journal.
    pub fn with_hook(self, hook: Option<PathBuf>) -> IncidentMonitor {
        let (log, hook_log) = (self.log.clone(), self.reports.directory.join("hook.log"));
        self.with_hand_on(Box::new(move |path| {
            let Some(hook) = hook.clone() else { return };
            let (inner, hook_log, path) = (log.clone(), hook_log.clone(), path.to_owned());
            let name = path
                .file_name()
                .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
            let spawned = std::thread::Builder::new()
                .name("xr-incident-hook".into())
                .spawn(
                    move || match run_hook(&hook, &path, &hook_log, HOOK_TIMEOUT) {
                        Ok(Some(rc)) => {
                            inner(&format!("incident: on-incident hook for {name}: {rc}"))
                        }
                        Ok(None) => {}
                        Err(e) => inner(&format!(
                            "incident: on-incident hook for {name} failed: {e}"
                        )),
                    },
                );
            if let Err(e) = spawned {
                log(&format!("incident: on-incident hook failed: {e}"));
            }
        }))
    }

    /// Runs on a thread of its own until `Running::stop`. `build` makes the monitor there, as it is not `Send`.
    pub fn start(
        feed: Feed,
        build: impl FnOnce(Feed) -> IncidentMonitor + Send + 'static,
    ) -> std::io::Result<Running> {
        let halt = Arc::new(AtomicBool::new(false));
        let (stop, clock) = (halt.clone(), feed.clock.clone());
        let inner = feed.clone();
        let thread = std::thread::Builder::new()
            .name("xr-incidents".into())
            .spawn(move || {
                let mut monitor = build(inner);
                while !stop.load(Ordering::Relaxed) {
                    std::thread::sleep(INTERVAL);
                    monitor.step(clock());
                }
            })?;
        Ok(Running {
            feed,
            halt,
            thread: Some(thread),
        })
    }

    // --- one pass -----------------------------------------------------------------------------------------------

    pub fn step(&mut self, now: f64) {
        while let Some(o) = self.feed.take() {
            self.observed(&o);
        }
        self.flush(now);
    }

    fn say(&mut self, text: String) {
        if self.said.as_ref() != Some(&text) {
            (self.log)(&text); // a fault that repeats is said once
        }
        self.said = Some(text);
    }

    /// A source that fails costs its evidence, never the report.
    fn safe<T>(&mut self, name: &str, answer: Result<T, String>, default: T) -> T {
        answer.unwrap_or_else(|e| {
            self.say(format!("incident monitor: {name} failed: {e}"));
            default
        })
    }

    fn observed(&mut self, o: &Value) {
        if !self.resumed {
            self.resumed = true;
            self.resume(o);
        }
        let run_id = as_str(get(o, "run_id"));
        if let Some(live) = self.live.clone()
            && run_id != Some(live.borrow().watch.run_id.as_str())
        {
            self.finish(&live, o, false, false);
        }
        if self.live.is_none()
            && let Some(run_id) = run_id
        {
            if self.ended.contains_key(run_id) {
                self.after_end(run_id, o);
                return;
            }
            if held(o) {
                return;
            }
            self.begin(run_id, o);
        }
        let Some(live) = self.live.clone() else {
            return;
        };
        if stranded(o) && !held(o) {
            self.finish(&live, o, true, false)
        } else {
            self.feed_live(&live, o)
        }
    }

    fn at(o: &Value) -> f64 {
        as_num(get(o, "at")).unwrap_or(0.0)
    }

    fn context(&mut self, run_id: &str, at: f64) -> Shared {
        let consent = self.sources.consent(run_id);
        let consent = self.safe("consent", consent, Value::Null);
        let consent = if consent.is_object() {
            consent
        } else {
            Value::Null
        };
        Rc::new(RefCell::new(Live::new(run_id, &consent, at)))
    }

    fn begin(&mut self, run_id: &str, o: &Value) {
        let live = self.context(run_id, Self::at(o));
        let instance = {
            let mut l = live.borrow_mut();
            if l.instance.as_deref().is_none_or(str::is_empty) {
                l.instance = as_str(get(o, "instance")).map(str::to_owned);
            }
            l.instance.clone()
        };
        let pid = self.sources.hyprland_pid(instance.as_deref());
        live.borrow_mut().hypr_pid = self.safe("hyprland_pid", pid, None);
        self.live = Some(live);
        self.save();
    }

    /// The first observation of a controller start: what became of the run the last one was watching? A controller
    /// restart under a live session is no incident, and after a reboot there is nothing left to look at.
    fn resume(&mut self, o: &Value) {
        let run_id = as_str(get(o, "run_id"));
        let mut saved = self.saved_watch.take();
        if saved.is_some() {
            let boot = self.sources.boot_id();
            let boot = self.safe("boot_id", boot, None);
            if saved
                .as_ref()
                .is_some_and(|s| !py_eq(get(s, "boot"), &opt_str(boot.as_deref())))
            {
                saved = None;
            }
        }
        if let Some(saved) = saved
            && let Some(saved_id) = as_str(get(&saved, "run_id"))
        {
            let live = self.context(saved_id, Self::at(o));
            {
                let mut l = live.borrow_mut();
                l.instance = as_str(get(&saved, "instance")).map(str::to_owned);
                l.hypr_pid = super::py::as_int(get(&saved, "hypr_pid"));
                l.watch.running_since = get(&saved, "running_since").clone();
                l.watch.stopped = get(&saved, "stopped") == &Value::Bool(true);
                l.report_ids = get(&saved, "report_ids")
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(as_str)
                    .map(str::to_owned)
                    .collect();
                if let Some(ports) = get(&saved, "ports").as_array() {
                    l.kernel.note_ports(ports.iter().filter_map(as_str));
                }
            }
            let same = run_id == Some(saved_id);
            if same && (state_of(o).is_some_and(|s| LIVE.contains(&s)) || held(o)) {
                self.live = Some(live);
            } else {
                self.finish(&live, o, same && stranded(o), true);
            }
        } else if let Some(run_id) = run_id
            && stranded(o)
            && !held(o)
            && !self.ended.contains_key(run_id)
        {
            let live = self.context(run_id, Self::at(o));
            self.finish(&live, o, true, true);
        }
    }

    fn feed_live(&mut self, live: &Shared, o: &Value) {
        let at = Self::at(o);
        let state = state_of(o);
        let run_id = live.borrow().watch.run_id.clone();
        {
            let mut l = live.borrow_mut();
            let stopping = as_str(get(o, "stop_requested")) == Some(run_id.as_str())
                || state == Some("stopping");
            if stopping && !l.watch.stopped {
                l.watch.stopped = true;
                drop(l);
                self.save();
            }
        }
        let mut merged = o.as_object().cloned().unwrap_or_default();
        if matches!(state, Some("running" | "failed")) {
            let output = get(&live.borrow().watch.consent, "output").clone();
            let ports = self.sources.usb_ports();
            let ports = self.safe("usb_ports", ports, None);
            if let Some(ports) = ports.as_ref().filter(|p| !p.is_empty()) {
                live.borrow_mut()
                    .kernel
                    .note_ports(ports.iter().map(String::as_str));
            }
            let monitors = get(o, "monitors").as_array().cloned().unwrap_or_default();
            let monitor = monitors
                .iter()
                .find(|m| py_eq(get(m, "name"), &output))
                .cloned()
                .unwrap_or(Value::Null);
            let drm = match as_str(&output).filter(|_| truthy(&output)) {
                Some(name) => {
                    let drm = self.sources.drm(name);
                    opt_str(self.safe("drm", drm, None).as_deref())
                }
                None => Value::Null,
            };
            let pid = live.borrow().hypr_pid;
            let alive = self.sources.alive(pid);
            let alive = self.safe("alive", alive, None);
            let driver = self.sources.driver_alive().map(Some);
            let driver = self.safe("driver_alive", driver, None);
            merged.insert("monitor".into(), monitor);
            merged.insert("monitors_known".into(), (!monitors.is_empty()).into());
            merged.insert("drm".into(), drm);
            merged.insert(
                "usb_present".into(),
                ports.map_or(Value::Null, |p| (!p.is_empty()).into()),
            );
            merged.insert("hyprland_alive".into(), alive.into());
            merged.insert("driver_alive".into(), driver.into());
        }
        let (mut signals, first_running, switched) = {
            let mut l = live.borrow_mut();
            let first_running = state == Some("running") && l.watch.running_since.is_null();
            let switches = l.watch.mode_switches;
            let signals = l.watch.observe(&Value::Object(merged));
            let switched =
                (l.watch.mode_switches != switches).then(|| py_str(&l.watch.mode_text()));
            (signals, first_running, switched)
        };
        if let Some(mode) = switched {
            (self.log)(&format!("mode-switch: {run_id}: {mode}"));
        }
        if first_running {
            self.save();
        }
        if state == Some("running") {
            let (armed, next_poll) = {
                let l = live.borrow();
                (l.armed, l.next_poll)
            };
            if !armed {
                self.arm(live);
            } else if at >= next_poll {
                signals.extend(self.poll(live, at));
            }
        }
        for signal in signals {
            self.correlator
                .add(signal, &run_id, Some(live.clone()), Some(at));
        }
    }

    /// Logs count from here: what happened during the start is the start's business.
    fn arm(&mut self, live: &Shared) {
        let cursor = self.sources.kernel_start();
        let cursor = self.safe("kernel_start", cursor, None);
        let offset = self.sources.driver_start().map(Some);
        let offset = self.safe("driver_start", offset, None);
        let mut l = live.borrow_mut();
        (l.cursor, l.offset, l.armed, l.next_poll) = (cursor, offset, true, 0.0);
    }

    /// New log lines. With no position yet (a source that failed at the start) it only finds one: reading from
    /// nowhere would replay old drops as new.
    fn poll(&mut self, live: &Shared, at: f64) -> Vec<Signal> {
        live.borrow_mut().next_poll = at + POLL_S;
        let mut out = Vec::new();
        let cursor = live.borrow().cursor.clone();
        match cursor {
            None => {
                let start = self.sources.kernel_start();
                live.borrow_mut().cursor = self.safe("kernel_start", start, None);
            }
            Some(cursor) => {
                let read = self.sources.kernel_read(&cursor);
                let (lines, next) = self.safe("kernel_read", read, (Vec::new(), cursor));
                let mut l = live.borrow_mut();
                l.cursor = Some(next);
                out.extend(l.kernel.feed(&lines));
            }
        }
        let offset = live.borrow().offset;
        match offset {
            None => {
                let start = self.sources.driver_start().map(Some);
                live.borrow_mut().offset = self.safe("driver_start", start, None);
            }
            Some(offset) => {
                let read = self.sources.driver_read(offset);
                let (text, next) = self.safe("driver_read", read, (String::new(), offset));
                let mut l = live.borrow_mut();
                l.offset = Some(next);
                out.extend(l.driver.feed(&text, at));
            }
        }
        out
    }

    /// A run is over, or waits for Recover. Only an abnormal end reads the last unread log lines: after a clean one, a
    /// USB disconnect is someone putting the glasses away.
    fn finish(&mut self, live: &Shared, o: &Value, stranded: bool, away: bool) {
        let at = Self::at(o);
        self.live = None;
        let run_id = live.borrow().watch.run_id.clone();
        let result = self.sources.result(&run_id);
        let result = self.safe("result", result, Value::Null);
        let lines = self.sources.journal(&unit(&run_id), 60);
        let lines = self.safe("journal", lines, Vec::new());
        let unit_exit = parse_unit_exit(&lines);
        let (mut signals, armed, pid) = {
            let mut l = live.borrow_mut();
            l.result = result.clone();
            l.unit = unit_exit.clone();
            let end = End {
                started: !l.watch.running_since.is_null(),
                stopped: l.watch.stopped,
                stranded,
                unit: &unit_exit,
                log: &lines,
            };
            (classify_end(at, &result, end), l.armed, l.hypr_pid)
        };
        if !signals.is_empty() && armed {
            let mut polled = self.poll(live, at);
            polled.append(&mut signals);
            signals = polled;
        }
        if away && !signals.is_empty() {
            signals.push(Signal::new(
                at,
                "controller-restart",
                "controller",
                "the controller came back to a session that had ended while it was away",
            ));
            let alive = self.sources.alive(pid);
            if self.safe("alive", alive, None) == Some(false) {
                signals.push(Signal::new(
                    at,
                    "hyprland-crash",
                    "hyprland",
                    "the Hyprland process is gone",
                ));
            }
        }
        let any = !signals.is_empty();
        for signal in signals {
            self.correlator
                .add(signal, &run_id, Some(live.clone()), Some(at));
        }
        self.ended.insert(run_id.clone(), float(at));
        if self.ended.len() > MAX_ENDED {
            let mut order: Vec<(String, f64)> = self
                .ended
                .iter()
                .map(|(k, v)| (k.clone(), v.as_f64().unwrap_or(f64::NEG_INFINITY)))
                .collect();
            order.sort_by(|a, b| a.1.total_cmp(&b.1));
            for (old, _) in &order[..order.len() - MAX_ENDED] {
                self.ended.shift_remove(old);
            }
        }
        let hyprland = self.correlator.open.as_ref().is_some_and(|e| {
            e.run_id == run_id && e.signals.iter().any(|s| s.kind == "hyprland-crash")
        });
        let reason = get(&result, "stop_reason");
        let watched_stop = live.borrow().watch.stopped;
        // A later Stop cannot change why the supervisor already began shutdown.
        let stopped = as_str(reason) == Some("interrupted") || (watched_stop && !truthy(reason));
        if !live.borrow().report_ids.is_empty() {
            let kind = if any { "run-failed" } else { "renderer-exit" };
            let signal = Signal::new(
                at,
                kind,
                "controller",
                "the monitored run ended; see its recorded outcome",
            );
            self.correlator
                .add(signal, &run_id, Some(live.clone()), Some(at));
        }
        let hold = if crash_expected(&result) || hyprland {
            CORE_WAIT_S
        } else {
            0.0
        };
        self.correlator
            .run_ended(&run_id, at, if stopped { "stopped" } else { "ended" }, hold);
        self.save();
    }

    /// A Recover that came back to where it started is a failure of its own; one that finished is the normal way out.
    fn after_end(&mut self, run_id: &str, o: &Value) {
        let state = get(o, "state").clone();
        let before = self.after.insert(run_id.into(), state);
        if before.as_ref().and_then(as_str) == Some("starting") && stranded(o) {
            let at = Self::at(o);
            let recovery = self.sources.recovery(run_id);
            let recovery = self.safe("recovery", recovery, Value::Null);
            let reasons: Vec<String> = get(&recovery, "reasons")
                .as_array()
                .into_iter()
                .flatten()
                .map(py_str)
                .collect();
            let mut detail = "Recover ran, and the session still has unfinished work".to_owned();
            if !reasons.is_empty() {
                detail += &format!(": {}", reasons.join("; "));
            }
            let context = self.context(run_id, at);
            let result = self.sources.result(run_id);
            context.borrow_mut().result = self.safe("result", result, Value::Null); // how the run itself ended
            self.correlator.add(
                Signal::new(at, "recovery-failed", "controller", detail),
                run_id,
                Some(context),
                Some(at),
            );
            self.correlator.run_ended(run_id, at, "ended", 0.0);
        }
    }

    // --- reporting ----------------------------------------------------------------------------------------------

    fn cores(&mut self, episode: &Episode<Shared>) -> Vec<Value> {
        let (exes, pids) = match &episode.context {
            Some(live) => {
                let l = live.borrow();
                let path = get(pin_of(&l.watch.consent, "renderer"), "path");
                let exes: Vec<String> = as_str(path)
                    .filter(|_| truthy(path))
                    .map(str::to_owned)
                    .into_iter()
                    .collect();
                (
                    exes,
                    l.hypr_pid
                        .filter(|p| *p != 0)
                        .into_iter()
                        .collect::<Vec<_>>(),
                )
            }
            None => (Vec::new(), Vec::new()),
        };
        if exes.is_empty() && pids.is_empty() {
            return Vec::new();
        }
        let found = self.sources.core_dumps(episode.first - 30.0, &exes, &pids);
        self.safe("core_dumps", found, Vec::new())
    }

    fn flush(&mut self, now: f64) {
        if let Some(open) = self.correlator.open.take() {
            let mut open = open;
            if open.hold_until > now && now >= self.next_core_check {
                self.next_core_check = now + POLL_S;
                if !self.cores(&open).is_empty() {
                    open.hold_until = 0.0;
                }
            }
            self.correlator.open = Some(open);
        }
        for done in self.correlator.due(now) {
            if let Err(e) = self.report(&done, now) {
                self.say(format!("incident monitor: _report failed: {e}"));
            }
        }
    }

    fn evidence(&mut self, episode: &Episode<Shared>, suppressed: Value) -> Value {
        let live = match &episode.context {
            Some(live) => live.clone(),
            None => self.context(&episode.run_id, episode.first),
        };
        let run = self.sources.journal(&unit(&episode.run_id), LOG_LINES);
        let run = self.safe("journal", run, Vec::new());
        let last = episode.last.max(episode.ended_at.unwrap_or(episode.last));
        let kernel = self
            .sources
            .kernel_around(episode.first - KERNEL_BEFORE_S, last + KERNEL_AFTER_S);
        let kernel = self.safe("kernel_around", kernel, Vec::new());
        let source = self.sources.source();
        let source = self.safe("source", source, json!({}));
        let recovery = self.sources.recovery(&episode.run_id);
        let recovery = self.safe("recovery", recovery, Value::Null);
        let cores = self.cores(episode);
        let tracking = self.sources.tracking_driver();
        let tracking = self.safe("tracking_driver", tracking, Value::Null);
        let controller = self.sources.journal(CONTROLLER_UNIT, LOG_LINES);
        let controller = self.safe("journal", controller, Vec::new());
        let driver = self.sources.driver_tail(LOG_LINES);
        let driver = self.safe("driver_tail", driver, Vec::new());
        let l = live.borrow();
        let w = &l.watch;
        json!({
            "consent": w.consent,
            "result": l.result,
            "unit": if truthy(&l.unit) { l.unit.clone() } else { parse_unit_exit(&run) },
            "source": source,
            "recovery": recovery,
            "cores": cores,
            "tracking_driver": tracking,
            "status": w.last_status,
            "mode": w.mode_text(),
            "expected_mode": w.expected_mode_text(),
            "drm": w.drm,
            "metrics": exit_metrics(&run),
            "suppressed": suppressed,
            "logs": {
                "run": run,
                "kernel": super::sources::relevant_kernel(&kernel, &l.kernel.ports),
                "controller": controller,
                "driver": driver,
            },
        })
    }

    fn report(&mut self, episode: &Episode<Shared>, now: f64) -> Result<(), String> {
        let kind = episode.kind().to_owned();
        let live = episode.context.clone();
        let ids = live
            .as_ref()
            .map_or_else(Vec::new, |l| l.borrow().report_ids.clone());
        let terminal_update = episode.ended.is_some() && !ids.is_empty();
        let allowed = !terminal_update && self.limit.allow(&kind, episode.ended.is_some(), now);
        // Suppression limits notifications, not evidence. A terminal outcome belongs to every report from this exact
        // run, even when the last incident was much earlier.
        let targets: Vec<String> = if terminal_update {
            ids
        } else if !allowed {
            ids.last().cloned().into_iter().collect()
        } else {
            Vec::new()
        };
        let mut updated = false;
        for stem in targets {
            let previous = self
                .reports
                .read(&stem)
                .map(Some)
                .map_err(|e| e.to_string());
            let previous = self.safe("read", previous, None);
            let Some(previous) = previous.filter(|p| {
                p.is_object() && as_str(get(p, "run_id")) == Some(episode.run_id.as_str())
            }) else {
                continue;
            };
            let evidence = self.evidence(episode, json!({}));
            let bundle = amend_bundle(&previous, episode, &evidence, now, &self.scrub)?;
            let path = self
                .reports
                .update(&bundle)
                .map(Some)
                .map_err(|e| e.to_string());
            if self.safe("update", path, None).is_none() {
                continue;
            }
            updated = true;
            if let Some(latest) = self.latest.as_mut().filter(|l| !l.is_empty())
                && as_str(latest.get("id").unwrap_or(&Value::Null)) == Some(stem.as_str())
            {
                latest.insert("summary".into(), bundle["summary"].clone());
            }
        }
        if updated || (!allowed && !terminal_update) {
            self.save();
            return Ok(());
        }
        let suppressed = self.limit.take_suppressed();
        let evidence = self.evidence(episode, suppressed);
        let bundle = build_bundle(episode, &evidence, now, &self.scrub);
        let path = match self.reports.write(&bundle, &markdown(&bundle)) {
            Ok(path) => path,
            Err(e) => {
                self.say(format!("incident: could not save the {kind} report: {e}"));
                return Ok(());
            }
        };
        let stem = path
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        let name = path
            .file_name()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        if let Some(live) = &live {
            live.borrow_mut().report_ids.push(stem.clone());
        }
        let summary = py_str(&bundle["summary"]);
        let mut latest = Map::new();
        latest.insert("id".into(), stem.into());
        latest.insert("kind".into(), kind.into());
        latest.insert("summary".into(), summary.clone().into());
        latest.insert("at".into(), bundle["first_signal_at"].clone());
        latest.insert("path".into(), path.to_string_lossy().into_owned().into());
        self.latest = Some(latest);
        self.save();
        (self.log)(&format!("incident: {name}: {summary}"));
        let sent = (self.notify)(&notify_text(&summary));
        self.safe("notify", sent, ());
        (self.hand_on)(&path);
        Ok(())
    }

    fn save(&mut self) {
        let boot = self.sources.boot_id();
        let boot = self.safe("boot_id", boot, None);
        let watch = self
            .live
            .as_ref()
            .map_or(Value::Null, |l| l.borrow().saved(&boot));
        let mut state = Map::new();
        state.insert("schema".into(), 1.into());
        state.insert("watch".into(), watch);
        state.insert("ended".into(), Value::Object(self.ended.clone().into_iter().collect()));
        state.insert("limit".into(), self.limit.save());
        state.insert(
            "latest".into(),
            self.latest.clone().map_or(Value::Null, Value::Object),
        );
        let saved = self
            .reports
            .save_state(&Value::Object(state))
            .map_err(|e| e.to_string());
        self.safe("save_state", saved, ());
    }

    /// The newest report, as the panel shows it.
    pub fn latest(&self) -> Option<&Map<String, Value>> {
        self.latest.as_ref()
    }
}

/// A started monitor. Dropping it leaves the thread running with the service; `stop` ends it.
pub struct Running {
    pub feed: Feed,
    halt: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Running {
    pub fn stop(mut self) {
        self.halt.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// `send.notify`: a short desktop notification. Fixed argv, no shell.
pub fn notify_send(message: &str) -> Result<(), String> {
    let path = std::env::var("PATH")
        .ok()
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| "/usr/bin:/bin".into());
    let Some(binary) = path
        .split(':')
        .map(|dir| Path::new(dir).join("notify-send"))
        .find(|p| {
            use std::os::unix::fs::PermissionsExt;
            p.metadata()
                .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        })
    else {
        return Ok(());
    };
    let mut env = Vec::new();
    let runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_default();
    let bus = format!("unix:path={runtime}/bus");
    if std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_none()
        && runtime.starts_with('/')
        && Path::new(&runtime).join("bus").exists()
    {
        env.push(("DBUS_SESSION_BUS_ADDRESS", bus.as_str())); // a bare systemd unit may lack it
    }
    let text: String = message.chars().take(200).collect();
    let argv: Vec<String> = [
        binary.to_string_lossy().as_ref(),
        "--app-name",
        "XR Workspace",
        "--expire-time",
        "4000",
        "XR Workspace",
        &text,
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    super::sources::run_bounded(&argv, Duration::from_secs(2), &env);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::TestDir;

    #[test]
    fn the_feed_stamps_and_keeps_the_newest() {
        let feed = Feed::new(Arc::new(|| 7.5));
        feed.observe(&json!({"state": "idle"}));
        feed.observe(&json!({"state": "idle", "at": 3.0}));
        assert_eq!(feed.take().unwrap()["at"], json!(7.5));
        assert_eq!(feed.take().unwrap()["at"], json!(3.0));
        for n in 0..QUEUE + 10 {
            feed.observe(&json!({"n": n}));
        }
        assert_eq!(feed.queue.lock().unwrap().len(), QUEUE);
        assert_eq!(feed.take().unwrap()["n"], json!(10));
    }

    #[test]
    fn the_thread_runs_and_stops() {
        let dir = TestDir::new("incident-thread");
        let directory = dir.path().join("incidents");
        let feed = Feed::new(Arc::new(wall_clock));
        let said = Arc::new(Mutex::new(Vec::<String>::new()));
        let log: Log = {
            let said = said.clone();
            Arc::new(move |line: &str| said.lock().unwrap().push(line.to_owned()))
        };
        let running = IncidentMonitor::start(feed, move |feed| {
            let host = super::super::sources::HostSources::new(
                Path::new("/nonexistent-runtime"),
                Path::new("/nonexistent-state"),
            );
            IncidentMonitor::new(
                Box::new(host),
                Reports::new(directory),
                feed,
                Scrubber::new("/home/u", "u", "h"),
                log,
            )
            .with_notify(Box::new(|_| Ok(())))
        })
        .unwrap();
        running
            .feed
            .observe(&json!({"state": "idle", "run_id": null}));
        std::thread::sleep(INTERVAL * 3);
        running.stop();
        assert!(
            said.lock()
                .unwrap()
                .iter()
                .all(|l| !l.starts_with("incident:"))
        );
    }
}
