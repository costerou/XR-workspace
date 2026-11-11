//! Window memory in a run: `window_memory.tracker_for` and `WindowTracker` (tools/native_spike/window_memory.py:
//! `place`, `finish`, `set_names`, `mark`, `leaving`, `context`, `close`), used by `managed._run_owned` around
//! adoption (`tracker_for(client, store, fallback, names)`, `memory.place(names)`, adoption, `memory.finish()`) and
//! by `managed.view_controls` (`control.windows`, `windows.context = control.window_context`, `set_names` on a
//! desktop change, `desktops.leaving = windows.leaving`; in the `finally`, after the setup is saved,
//! `windows.close()`: the last capture, while the XR outputs still hold the windows).
//!
//! The capture, the file, the pairing and the plan are `crate::window_memory` and `crate::lua::window_memory`; this
//! is the tracker around them. [`Tracker::place`] puts the remembered windows back before adoption, with the run's
//! client; [`Tracker::finish`] settles them after it and parks the windows whose desktop is missing.
//! [`Tracker::mark`] and [`Tracker::poll`] run on the supervisor loop and never touch the compositor: a capture
//! [`CAPTURE_DELAY`] after a mark runs on a worker thread with its own client, pinned to the run's compositor
//! (`Client::pinned`), one at a time. [`Tracker::close`] joins it and takes the last capture with the run's client.
//! Everything fails open, as in Python: a failure is a log line, and adoption or recovery goes on.
//!
//! Not ported: `set_names` and `leaving` (a desktop removed and added again in a run, with `_come_home`); the
//! `desktops` port calls them. `context` is [`Tracker::set_context`], for the view's `window_context`.

use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::Run;
use super::view::ViewControl;
use crate::hyprland::{self, Client, Identity, Phase, Query};
use crate::window_memory::{self as memory, MAX_WINDOWS, Plan, Store};

/// A window change is read this long after it: a burst of moves is one read.
pub const CAPTURE_DELAY: Duration = Duration::from_secs(1);
/// Each read while the run is live or ending: recovery never waits long on this.
pub const QUERY_TIMEOUT: Duration = Duration::from_millis(500);

/// The view's context for each desktop (`view.ViewControl.window_context`): `{name: {"placement", "roll"}}`.
pub type Context = Arc<dyn Fn() -> Option<Value> + Send + Sync>;

/// What the worker and the run share, under one lock: one reader of the compositor and writer of the file at a time.
struct Memory {
    store: Store,
    instance: String,
    names: Vec<String>,
    context: Option<Context>,
    /// The memory as it is on disk.
    saved: Option<Value>,
    /// Records of windows waiting for their desktop.
    parked: Vec<Value>,
    /// Those desktops' context.
    parked_desktops: Map<String, Value>,
    closed: bool,
    counts: Counts,
}

/// Diagnostics (`captures`, `writes`, `failures`, `restored`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub captures: usize,
    pub writes: usize,
    pub failures: usize,
    pub restored: usize,
}

/// `window_memory.WindowTracker`.
pub struct Tracker {
    memory: Arc<Mutex<Memory>>,
    fallback: String,
    runtime: PathBuf,
    session: String,
    /// The compositor the run's client verified: the worker's client is pinned to it.
    identity: Option<Identity>,
    /// `delay`: [`CAPTURE_DELAY`], shorter in tests.
    pub delay: Duration,
    /// When the next capture is due.
    due: Option<Instant>,
    worker: Option<JoinHandle<()>>,
    closed: bool,
    /// `place`'s plan, for `finish`.
    placement: Option<Plan>,
}

fn log(message: &str) {
    eprintln!("xr-workspace: {message}");
}

fn lock(memory: &Mutex<Memory>) -> std::sync::MutexGuard<'_, Memory> {
    memory.lock().unwrap_or_else(PoisonError::into_inner)
}

/// A strict reply as serde_json holds it (the window memory reads serde_json).
fn to_serde(v: &xr_wire::json::Value) -> Value {
    use xr_wire::json::Value as W;
    match v {
        W::Null => Value::Null,
        W::Bool(b) => Value::Bool(*b),
        W::Number(n) => Value::Number(n.clone()),
        W::String(s) => Value::String(s.clone()),
        W::Array(a) => Value::Array(a.iter().map(to_serde).collect()),
        W::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), to_serde(v))).collect()),
    }
}

fn query(client: &mut Client, kind: Query, timeout: Duration) -> Result<Value, String> {
    client
        .query(kind, timeout)
        .map(|v| to_serde(&v))
        .map_err(|e| e.to_string())
}

/// `_read`: clients, workspaces, monitors, in that order.
fn read(client: &mut Client, timeout: Duration) -> Result<(Value, Value, Value), String> {
    Ok((
        query(client, Query::Clients, timeout)?,
        query(client, Query::Workspaces, timeout)?,
        query(client, Query::Monitors, timeout)?,
    ))
}

/// `_active`: the focused window and monitor, to hand focus back to.
fn active(client: &mut Client, monitors: &Value, timeout: Duration) -> Result<Value, String> {
    let window = query(client, Query::ActiveWindow, timeout)?;
    let focused = monitors
        .as_array()
        .into_iter()
        .flatten()
        .find(|m| m.get("focused") == Some(&Value::Bool(true)))
        .and_then(|m| m.get("name").cloned())
        .unwrap_or(Value::Null);
    let address = window.get("address").cloned().unwrap_or(Value::Null);
    Ok(json!({"address": address, "monitor": focused}))
}

fn refs(names: &[String]) -> Vec<&str> {
    names.iter().map(String::as_str).collect()
}

/// `profiles.profiles_path()`: `$XDG_CONFIG_HOME/viture-workspace/profiles.json`.
fn profiles_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
        });
    base.join("viture-workspace").join("profiles.json")
}

impl Memory {
    /// The view's context; a failure only loses the context.
    fn view(&self) -> Option<Value> {
        let context = self.context.clone()?;
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| context()))
            .ok()
            .flatten()
    }

    /// `_capture`: read Hyprland and save the memory if it changed. The memory read, or None if it could not be read.
    fn capture(&mut self, client: &mut Client, timeout: Duration) -> Option<Value> {
        self.counts.captures += 1;
        let fresh = (|| -> Result<Value, String> {
            let (clients, workspaces, monitors) = read(client, timeout)?;
            let context = self.view();
            let fresh = memory::capture(
                &clients,
                &workspaces,
                &monitors,
                &refs(&self.names),
                &self.instance,
                context.as_ref(),
            )
            .map_err(|e| e.to_string())?;
            let workspaces = workspaces.as_array().cloned().unwrap_or_default();
            let monitor_of = |id: Option<&Value>| {
                workspaces
                    .iter()
                    .filter(|w| w.is_object())
                    .rev()
                    .find(|w| w.get("id") == id)
                    .and_then(|w| w.get("monitor"))
            };
            let clients = clients.as_array().cloned().unwrap_or_default();
            let at = |address: &Value| {
                clients
                    .iter()
                    .rev()
                    .filter(|c| c.get("workspace").is_some_and(Value::is_object))
                    .find(|c| c.get("address") == Some(address))
                    .and_then(|c| monitor_of(c["workspace"].get("id")))
            };
            // A parked window stays parked only where the removal put it: anywhere else it was moved on purpose.
            self.parked
                .retain(|r| at(&r["address"]) == Some(&r["landing"]));
            let waiting: Vec<&Value> = self.parked.iter().map(|r| &r["address"]).collect();
            let mut windows: Vec<Value> = fresh["windows"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|w| !waiting.contains(&&w["address"]))
                .cloned()
                .collect();
            windows.extend(self.parked.iter().cloned());
            windows.truncate(MAX_WINDOWS);
            let mut desktops = Map::new();
            for r in &self.parked {
                if let Some(name) = r["output"].as_str()
                    && let Some(d) = self.parked_desktops.get(name)
                {
                    desktops.insert(name.to_owned(), d.clone());
                }
            }
            if let Some(own) = fresh["desktops"].as_object() {
                desktops.extend(own.clone());
            }
            memory::validate(
                json!({"instance": fresh["instance"], "desktops": desktops, "windows": windows}),
            )
            .map_err(|e| e.to_string())
        })();
        let fresh = match fresh {
            Ok(fresh) => fresh,
            Err(e) => {
                self.counts.failures += 1;
                log(&format!(
                    "run: could not read the windows for the window memory: {e}"
                ));
                return None;
            }
        };
        // An empty capture never replaces a memory.
        let some = fresh["windows"].as_array().is_some_and(|w| !w.is_empty());
        if some && self.saved.as_ref() != Some(&fresh) {
            match self.store.save(fresh.clone()) {
                Ok(()) => {
                    self.saved = Some(fresh.clone());
                    self.counts.writes += 1;
                }
                Err(e) => {
                    self.counts.failures += 1;
                    log(&format!("run: could not save the window memory: {e}"));
                }
            }
        }
        Some(fresh)
    }
}

impl Tracker {
    /// `tracker_for(client, store, fallback, names)`: the run's glasses profile is `profiles.json` under
    /// `$XDG_CONFIG_HOME`, and its memory `window-memory.json` beside it. `None` for a model without a profile.
    pub fn open(run: &mut Run<'_>) -> Option<Tracker> {
        let store = Store::new(memory::memory_path(&profiles_path()), run.model).ok()?;
        Some(Tracker::new(store, run))
    }

    /// A tracker on `store` for `run`'s compositor and desktops.
    pub fn new(store: Store, run: &mut Run<'_>) -> Tracker {
        Tracker {
            memory: Arc::new(Mutex::new(Memory {
                store,
                instance: run.client.instance().to_owned(),
                names: run.capture.to_vec(),
                context: None,
                saved: None,
                parked: Vec::new(),
                parked_desktops: Map::new(),
                closed: false,
                counts: Counts::default(),
            })),
            fallback: run.fallback.to_owned(),
            runtime: run.runtime.to_path_buf(),
            session: run.session.to_owned(),
            identity: run.client.identity().cloned(),
            delay: CAPTURE_DELAY,
            due: None,
            worker: None,
            closed: false,
            placement: None,
        }
    }

    fn pin(&mut self, client: &Client) {
        if let Some(identity) = client.identity() {
            self.identity = Some(identity.clone());
        }
    }

    /// `memory.place(names)`: the windows XR last held go back where they were, before adoption. Never fails:
    /// adoption then takes them as it always has.
    pub fn place(&mut self, run: &mut Run<'_>) {
        let mut m = lock(&self.memory);
        m.names = run.capture.to_vec();
        let placed = (|| -> Result<Option<Plan>, String> {
            m.saved = m.store.load();
            let Some(saved) = m.saved.clone() else {
                return Ok(None);
            };
            let (clients, workspaces, monitors) = read(run.client, hyprland::DEADLINE)?;
            let active = active(run.client, &monitors, hyprland::DEADLINE)?;
            let plan = memory::plan(
                &saved,
                &clients,
                &workspaces,
                &monitors,
                &refs(&m.names),
                &self.fallback,
                &m.instance,
                Some(&active),
                false,
            )
            .map_err(|e| e.to_string())?;
            m.parked_desktops = saved["desktops"].as_object().cloned().unwrap_or_default();
            if memory::empty(&plan.moves) {
                return Ok(None);
            }
            run.client
                .place_windows(&plan.moves, Phase::Moves)
                .map_err(|e| e.to_string())?;
            Ok(Some(plan))
        })();
        drop(m);
        self.pin(run.client);
        match placed {
            Ok(plan) => self.placement = plan,
            Err(e) => log(&format!(
                "run: could not put the remembered windows back: {e}"
            )),
        }
    }

    /// `memory.finish()`: after adoption, whatever needs every window in place (the settling evals), then the first
    /// capture is marked.
    pub fn finish(&mut self, run: &mut Run<'_>) {
        if let Some(plan) = self.placement.take() {
            let mut m = lock(&self.memory);
            m.parked = plan
                .placed
                .iter()
                .filter(|p| p.moved)
                .map(|p| {
                    let mut record = p.record.as_object().cloned().unwrap_or_default();
                    record.insert("address".into(), json!(p.address));
                    record.insert("parked".into(), json!(true));
                    record.insert("landing".into(), json!(p.output));
                    Value::Object(record)
                })
                .collect();
            let n = plan.placed.len();
            m.counts.restored += n;
            drop(m);
            match run.client.place_windows(&plan.moves, Phase::Settle) {
                Ok(()) => log(&format!(
                    "run: {n} remembered window{} back on their desktops",
                    if n == 1 { "" } else { "s" }
                )),
                Err(e) => log(&format!(
                    "run: could not settle the remembered windows: {e}"
                )),
            }
        }
        self.pin(run.client);
        self.mark();
    }

    /// The `view_controls` wiring (`control.windows`). The view's `window_context` comes by [`Tracker::set_context`].
    pub fn attach(self, _view: Option<&mut ViewControl>) -> Option<Tracker> {
        Some(self)
    }

    /// `windows.context = control.window_context`.
    pub fn set_context(&mut self, context: Context) {
        lock(&self.memory).context = Some(context);
    }

    /// `windows.mark()`: something moved, capture soon. Marks inside the delay are one capture.
    pub fn mark(&mut self) {
        if self.due.is_none() {
            self.due = Some(Instant::now() + self.delay);
        }
    }

    /// When [`Tracker::poll`] has work: the loop's deadline.
    pub fn deadline(&self) -> Option<Instant> {
        self.due.filter(|_| !self.closed)
    }

    /// `windows.poll(now)`: a capture due starts on the worker, one at a time; one still running keeps it due.
    pub fn poll(&mut self, now: Instant) {
        if self.closed || self.due.is_none_or(|due| now < due) {
            return;
        }
        if self.worker.as_ref().is_some_and(|w| !w.is_finished()) {
            return;
        }
        self.due = None;
        let memory = Arc::clone(&self.memory);
        let (runtime, session, identity) = (
            self.runtime.clone(),
            self.session.clone(),
            self.identity.clone(),
        );
        self.worker = Some(std::thread::spawn(move || {
            let mut m = lock(&memory);
            if m.closed {
                return;
            }
            let client = identity
                .ok_or_else(|| "no verified compositor".to_owned())
                .and_then(|identity| {
                    Client::pinned(&m.instance, &runtime, identity, &session)
                        .map_err(|e| e.to_string())
                });
            match client {
                Ok(mut client) => {
                    m.capture(&mut client, QUERY_TIMEOUT);
                }
                Err(e) => {
                    m.counts.failures += 1;
                    log(&format!(
                        "run: could not read the windows for the window memory: {e}"
                    ));
                }
            }
        }));
    }

    /// `join()`: the capture in flight, if any, done.
    pub fn join(&mut self) {
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }

    /// The diagnostics.
    pub fn counts(&self) -> Counts {
        lock(&self.memory).counts
    }

    /// The memory file.
    pub fn path(&self) -> PathBuf {
        lock(&self.memory).store.path.clone()
    }

    /// `windows.close()`: run end, before recovery, the last capture while the XR outputs still hold the windows.
    /// Nothing runs after it.
    pub fn close(&mut self, run: &mut Run<'_>) {
        if self.closed {
            return;
        }
        self.join();
        self.closed = true;
        let mut m = lock(&self.memory);
        m.closed = true;
        m.capture(run.client, QUERY_TIMEOUT);
    }
}
