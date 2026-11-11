//! Window sending (Ctrl+Alt+G, Ctrl+Alt+1-4, Ctrl+Alt+B): `send.WindowSender` (tools/native_spike/send.py:
//! `request`, `close`, the worker that runs one move at a time off the loop, and the overview drag's `lookup`,
//! `under_cursor`, `land` and `drop`) and `send.notify`, as `managed.view_controls` builds it
//! (`WindowSender(client, capture, fallback)`, `control.notify = notify_text`) and closes it (`control.sender.close()`,
//! before privacy, 3D and the binds).
//!
//! The plan and the Lua are `crate::send` and `crate::lua::send`. Here is the worker: every call from the loop starts
//! a thread and returns at once. A move (a send key, a drag's `land` or `drop`) holds the sender's lock for the whole
//! move, so moves run one at a time, and [`WindowSender::close`] waits (bounded) for the one in flight: no move starts
//! after it, and one already sent lands before recovery evacuates the XR outputs. Refusals and failures are a desktop
//! notification and never reach the run.
//!
//! One difference from the Python: its threads share the run's client; here each job opens its own client of the same
//! compositor ([`Origin`]: pinned to the run's compositor process when the run's client knows it), as the run's other
//! workers do. The requests on the wire are the same (rust/corpus/send/sender.json, replayed by tests/send_corpus.rs).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, TryLockError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::Value as Json;
use xr_wire::json::Value;

use super::Run;
use crate::hyprland::{Client, Identity, Query};
use crate::lua::send::SEND_OPS;
use crate::py;
use crate::send as plan;
use crate::supervisor::immersive::{Done, complete};
use crate::supervisor::privacy::Notify;

/// `send.notify`: a short desktop notification; best-effort, never fails.
pub use crate::managed::real::notify;

/// The compositor deadline of each query (`hyprland.DEADLINE`).
pub const DEADLINE: Duration = Duration::from_secs(2);

/// What a lookup hands back (on its worker thread): `send.window_at`'s dict, or null.
pub type Found = Box<dyn FnOnce(Json) + Send>;

const COULD_NOT_MOVE: &str = "Could not move the window.";

/// Where a job's client goes: the run's compositor, pinned to its process when the run's client has met it.
#[derive(Clone, Debug)]
pub struct Origin {
    pub instance: String,
    pub runtime: PathBuf,
    pub identity: Option<Identity>,
    pub session: String,
}

impl Origin {
    /// The run's client's compositor.
    pub fn of(run: &Run<'_>) -> Origin {
        Origin {
            instance: run.client.instance().to_owned(),
            runtime: run.runtime.to_path_buf(),
            identity: run.client.identity().cloned(),
            session: run.session.to_owned(),
        }
    }

    fn client(&self) -> Result<Client, String> {
        match &self.identity {
            Some(identity) => Client::pinned(
                &self.instance,
                &self.runtime,
                identity.clone(),
                &self.session,
            ),
            None => Client::new(&self.instance, &self.runtime),
        }
        .map_err(|e| e.to_string())
    }
}

fn to_serde(v: &Value) -> Json {
    match v {
        Value::Null => Json::Null,
        Value::Bool(b) => Json::Bool(*b),
        Value::Number(n) => Json::Number(n.clone()),
        Value::String(s) => Json::String(s.clone()),
        Value::Array(a) => Json::Array(a.iter().map(to_serde).collect()),
        Value::Object(o) => Json::Object(o.iter().map(|(k, v)| (k.clone(), to_serde(v))).collect()),
    }
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// One job's compositor client, opened on its first request.
struct Hypr<'a> {
    origin: &'a Origin,
    deadline: Duration,
    client: Option<Client>,
}

impl Hypr<'_> {
    fn client(&mut self) -> Result<&mut Client, String> {
        if self.client.is_none() {
            self.client = Some(self.origin.client()?);
        }
        Ok(self.client.as_mut().expect("opened above"))
    }

    fn query(&mut self, kind: Query) -> Result<Json, String> {
        let deadline = self.deadline;
        let reply = self
            .client()?
            .query(kind, deadline)
            .map_err(|e| e.to_string())?;
        Ok(to_serde(&reply))
    }

    fn mutate(&mut self, command: &str) -> Result<String, String> {
        self.client()?.mutate(command).map_err(|e| e.to_string())
    }

    fn land_window(
        &mut self,
        address: &str,
        workspace: i64,
        x: Option<i64>,
        y: Option<i64>,
    ) -> Result<(), String> {
        self.client()?
            .land_window(address, workspace, x, y)
            .map_err(|e| e.to_string())
    }
}

/// `x.get('workspace', {}).get('id')`: missing is None; anything but an object has no `.get` (AttributeError).
fn workspace_id(window: &Json) -> Result<Option<&Json>, String> {
    match window.get("workspace") {
        None => Ok(None),
        Some(Json::Object(w)) => Ok(w.get("id")),
        Some(_) => Err("workspace has no attribute 'get'".into()),
    }
}

/// `for m in v`: a list's items; an object's keys and a string's characters (never a dict, so never a match);
/// anything else is not iterable (TypeError).
fn items(v: &Json) -> Result<&[Json], String> {
    match v {
        Json::Array(a) => Ok(a),
        Json::Object(_) | Json::String(_) => Ok(&[]),
        _ => Err("not iterable".into()),
    }
}

fn refused(e: crate::lua::Refused) -> String {
    e.0.to_owned()
}

struct Shared {
    origin: Origin,
    names: Mutex<Vec<String>>,
    fallback: Option<String>,
    notify: Option<Notify>,
    deadline: Duration,
    /// `_lock`: held for a whole move; [`WindowSender::close`] waits on it.
    lock: Mutex<()>,
    closed: AtomicBool,
    moves: AtomicUsize,
}

impl Shared {
    fn hypr(&self) -> Hypr<'_> {
        Hypr {
            origin: &self.origin,
            deadline: self.deadline,
            client: None,
        }
    }

    fn names(&self) -> Vec<String> {
        lock(&self.names).clone()
    }

    fn closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    fn tell(&self, message: Option<&str>) {
        if let (Some(message), Some(notify)) = (message, self.notify.as_ref()) {
            notify(message);
        }
    }

    /// `move`: do it now (the caller holds the lock). A message for the user, or None if it moved or had nothing to do.
    fn send(&self, op: &str, selected: &str, verify: bool) -> Result<Option<String>, String> {
        let names = self.names();
        let target =
            plan::target_for(op, Some(selected), self.fallback.as_deref()).map_err(refused)?;
        if op != "send_back" && !target.as_ref().is_some_and(|t| names.contains(t)) {
            return Ok(Some(format!(
                "{} is not part of this session.",
                target.unwrap_or_default()
            )));
        }
        let Some(target) = target else {
            return Ok(Some(
                "This session has no physical monitor to send windows back to.".into(),
            ));
        };
        let mut hypr = self.hypr();
        let window = hypr.query(Query::ActiveWindow)?;
        if !window.is_object() || !py::truthy(window.get("address")) {
            return Ok(Some("No focused window to send.".into()));
        }
        let Some(address) = py::text(window.get("address")).filter(|a| py::is_address(a)) else {
            return Ok(Some("Could not identify the focused window.".into()));
        };
        if !py::is_false(window.get("pinned")) {
            return Ok(Some(
                "Pinned windows stay where they are: unpin it first.".into(),
            ));
        }
        let monitors = hypr.query(Query::Monitors)?;
        let Json::Array(list) = &monitors else {
            return Err("invalid monitors".into());
        };
        let current = list
            .iter()
            .find(|m| {
                m.is_object()
                    && py::int(m.get("id")).is_some()
                    && py::eq(m.get("id"), window.get("monitor"))
            })
            .and_then(|m| py::text(m.get("name")));
        if op == "send_back" && !current.is_some_and(|c| names.iter().any(|n| n == c)) {
            return Ok(Some("That window is not on the glasses.".into()));
        }
        if current == Some(target.as_str()) {
            let workspace = workspace_id(&window)?;
            if verify && !py::int(workspace).is_some_and(|id| id > 0) {
                return Ok(Some("Target window is not on a regular workspace".into()));
            }
            return Ok(None); // the authoritative activewindow and monitor identity already match
        }
        let found: Vec<&Json> = list
            .iter()
            .filter(|m| m.is_object() && py::text(m.get("name")) == Some(&target))
            .collect();
        let [found] = found[..] else {
            return Ok(Some(format!("{target} is not available.")));
        };
        let workspace = found
            .get("activeWorkspace")
            .filter(|a| a.is_object())
            .and_then(|a| py::int(a.get("id")));
        let Some(workspace) = workspace.filter(|id| *id > 0) else {
            return Ok(Some(format!(
                "{target} is not showing a regular workspace."
            )));
        };
        let command = plan::move_command(address, workspace, true).map_err(refused)?;
        if hypr.mutate(&command)? != "ok" {
            return Ok(Some("Hyprland did not move the window.".into()));
        }
        if verify {
            let clients = hypr.query(Query::Clients)?;
            let after = hypr.query(Query::Monitors)?;
            let targets: Vec<&Json> = items(&after)?
                .iter()
                .filter(|m| m.is_object() && py::text(m.get("name")) == Some(&target))
                .collect();
            let windows: Vec<&Json> = items(&clients)?
                .iter()
                .filter(|w| w.is_object() && py::text(w.get("address")) == Some(address))
                .collect();
            let mismatch = match (&targets[..], &windows[..]) {
                ([t], [w]) => {
                    !py::eq(w.get("monitor"), t.get("id"))
                        || !py::eq_int(workspace_id(w)?, workspace)
                        || !py::is_false(w.get("pinned"))
                }
                _ => true,
            };
            if mismatch {
                return Ok(Some(
                    "Window target/workspace readback mismatch; window remains under compositor authority."
                        .into(),
                ));
            }
        }
        self.moves.fetch_add(1, Ordering::SeqCst);
        Ok(None)
    }

    /// `land_now`: the window a Super drag ended on, onto output `target` if Hyprland's own rule did not take it there.
    fn land_now(&self, address: &str, target: &str) -> Result<Option<String>, String> {
        if !self.names().iter().any(|n| n == target) {
            return Ok(Some(format!("{target} is not part of this session.")));
        }
        let mut hypr = self.hypr();
        let monitors = hypr.query(Query::Monitors)?;
        let Some((found, Some(workspace))) = plan::monitor(&monitors, target).map_err(refused)?
        else {
            return Ok(None);
        };
        let active = hypr.query(Query::ActiveWindow)?;
        if !active.is_object()
            || py::text(active.get("address")) != Some(address)
            || py::truthy(active.get("pinned"))
        {
            return Ok(None);
        }
        if let Some(on) = active.get("workspace").filter(|w| w.is_object())
            && py::eq_int(on.get("id"), workspace)
        {
            return Ok(None); // it is there: Hyprland's own rule took it
        }
        let (x, y) = if py::is_true(active.get("floating")) {
            let (x, y) = plan::landing(&active, found).map_err(refused)?;
            (Some(x), Some(y))
        } else {
            (None, None) // a tiled one tiles there
        };
        hypr.land_window(address, workspace, x, y)?;
        self.moves.fetch_add(1, Ordering::SeqCst);
        Ok(None)
    }

    /// `drop_now`: the dragged window (or whatever is at `point` on `source` now) onto output `target`, not followed.
    fn drop_now(
        &self,
        window: Option<&Json>,
        source: &str,
        point: Option<(i64, i64)>,
        target: &str,
    ) -> Result<Option<String>, String> {
        if !self.names().iter().any(|n| n == target) {
            return Ok(Some(format!("{target} is not part of this session.")));
        }
        let mut hypr = self.hypr();
        let monitors = hypr.query(Query::Monitors)?;
        let looked;
        let window = match window {
            Some(w) => w,
            None => {
                let clients = hypr.query(Query::Clients)?;
                let (x, y) = point.ok_or("no point")?; // `*point` of None, after the query
                match plan::window_at(&clients, &monitors, source, x as f64, y as f64)
                    .map_err(refused)?
                {
                    None => return Ok(None), // nothing under the press: nothing to move
                    Some(w) => {
                        looked = w;
                        &looked
                    }
                }
            }
        };
        if py::truthy(window.get("pinned")) {
            return Ok(Some(
                "Pinned windows stay where they are: unpin it first.".into(),
            ));
        }
        let Some(address) = py::text(window.get("address")).filter(|a| py::is_address(a)) else {
            return Ok(Some("Could not identify the window.".into()));
        };
        let workspace = match plan::monitor(&monitors, target).map_err(refused)? {
            None => return Ok(Some(format!("{target} is not available."))),
            Some((_, None)) => {
                return Ok(Some(format!(
                    "{target} is not showing a regular workspace."
                )));
            }
            Some((_, Some(workspace))) => workspace,
        };
        let command = plan::move_command(address, workspace, false).map_err(refused)?;
        if hypr.mutate(&command)? != "ok" {
            return Ok(Some("Hyprland did not move the window.".into()));
        }
        self.moves.fetch_add(1, Ordering::SeqCst);
        Ok(None)
    }

    /// `lookup`'s work: the window shown at `point` on output `name`, or null on any failure.
    fn lookup(&self, name: &str, (x, y): (i64, i64)) -> Json {
        let mut hypr = self.hypr();
        let found = (|| {
            let clients = hypr.query(Query::Clients)?;
            let monitors = hypr.query(Query::Monitors)?;
            plan::window_at(&clients, &monitors, name, x as f64, y as f64).map_err(refused)
        })();
        found.ok().flatten().unwrap_or(Json::Null)
    }

    /// `under_cursor`'s work: the window under the cursor if it is on one of the run's desktops, or null.
    fn under_cursor(&self) -> Json {
        let mut hypr = self.hypr();
        let found = (|| -> Result<Option<Json>, String> {
            let pos = hypr.query(Query::CursorPos)?;
            let monitors = hypr.query(Query::Monitors)?;
            let num = |v: Option<&Json>| py::number(v).ok_or_else(|| "not a number".to_string());
            let mut found = None;
            for name in self.names() {
                let Some((m, _)) = plan::monitor(&monitors, &name).map_err(refused)? else {
                    continue;
                };
                // `m['x'] <= pos['x'] < m['x'] + m['width'] / m['scale'] and ...`, evaluated as Python chains it.
                let within = |at: &str, size: &str| -> Result<bool, String> {
                    let (lo, v) = (num(m.get(at))?, num(pos.get(at))?);
                    if !(lo <= v) {
                        return Ok(false);
                    }
                    let (length, scale) = (num(m.get(size))?, num(m.get("scale"))?);
                    if scale == 0.0 {
                        return Err("division by zero".into());
                    }
                    Ok(v < lo + length / scale)
                };
                if within("x", "width")? && within("y", "height")? {
                    let (x, y) = (num(pos.get("x"))?, num(pos.get("y"))?);
                    let clients = hypr.query(Query::Clients)?;
                    found = plan::window_at(&clients, &monitors, &name, x, y).map_err(refused)?;
                }
            }
            Ok(found)
        })();
        found.ok().flatten().unwrap_or(Json::Null)
    }
}

/// `send.WindowSender`: one op, at most one window move, on a worker thread, serialized, stoppable before recovery.
pub struct WindowSender {
    shared: Arc<Shared>,
    threads: Vec<JoinHandle<()>>,
}

impl WindowSender {
    /// `WindowSender(client, names, fallback, notify, deadline)`.
    pub fn new(
        origin: Origin,
        names: &[String],
        fallback: Option<&str>,
        notify: Option<Notify>,
        deadline: Duration,
    ) -> WindowSender {
        WindowSender {
            shared: Arc::new(Shared {
                origin,
                names: Mutex::new(names.to_vec()),
                fallback: fallback.map(str::to_owned),
                notify,
                deadline,
                lock: Mutex::new(()),
                closed: AtomicBool::new(false),
                moves: AtomicUsize::new(0),
            }),
            threads: Vec::new(),
        }
    }

    /// `closed`: no move starts after [`WindowSender::close`].
    pub fn closed(&self) -> bool {
        self.shared.closed()
    }

    /// The moves Hyprland accepted (diagnostic).
    pub fn moves(&self) -> usize {
        self.shared.moves.load(Ordering::SeqCst)
    }

    /// The run's desktops changed (a pill's Add / Remove): later sends go by the new set.
    pub fn set_names(&self, names: &[String]) {
        *lock(&self.shared.names) = names.to_vec();
    }

    fn start(&mut self, work: impl FnOnce(&Shared) + Send + 'static) {
        let shared = Arc::clone(&self.shared);
        let thread = thread::spawn(move || work(&shared));
        self.threads.retain(|t| !t.is_finished());
        self.threads.push(thread);
    }

    /// From the supervisor loop: never blocks on the compositor. `done(success, detail)`, given, also reads the move
    /// back (the options menu's owner path).
    pub fn request(&mut self, op: &str, selected: &str, done: Option<Done>) {
        if !SEND_OPS.contains(&op) || self.closed() {
            if let Some(done) = done {
                complete(done, false, "Window sender is closed or request invalid");
            }
            return;
        }
        let (op, selected) = (op.to_owned(), selected.to_owned());
        self.start(move |shared| {
            let guard = lock(&shared.lock);
            if shared.closed() {
                if let Some(done) = done {
                    complete(done, false, "Window sender closed");
                }
                return;
            }
            let (message, detail) = match shared.send(&op, &selected, done.is_some()) {
                Ok(message) => (message, None),
                Err(why) => (
                    Some(COULD_NOT_MOVE.to_owned()),
                    Some(why).filter(|w| !w.is_empty()),
                ),
            };
            drop(guard);
            if let Some(done) = done {
                let detail = detail.as_deref().or(message.as_deref());
                complete(
                    done,
                    message.is_none(),
                    detail.unwrap_or("Window target verified"),
                );
            }
            shared.tell(message.as_deref());
        });
    }

    /// The overview's window drag: the window shown at layout `point` on output `name`, handed to `found` from the
    /// worker. Nothing once closed.
    pub fn lookup(&mut self, name: &str, point: (i64, i64), found: Found) {
        if self.closed() {
            return;
        }
        let name = name.to_owned();
        self.start(move |shared| found(shared.lookup(&name, point)));
    }

    /// A Super drag's press: the window under the cursor now, if the cursor is on one of the run's desktops.
    pub fn under_cursor(&mut self, found: Found) {
        if self.closed() {
            return;
        }
        self.start(move |shared| found(shared.under_cursor()));
    }

    /// A Super drag that crossed desktops was dropped with the cursor on output `target`: see that window `address` is
    /// there. Serialized with the send keys.
    pub fn land(&mut self, address: &str, target: &str) {
        if self.closed() {
            return;
        }
        let (address, target) = (address.to_owned(), target.to_owned());
        self.start(move |shared| {
            let message = {
                let _guard = lock(&shared.lock);
                if shared.closed() {
                    return;
                }
                shared
                    .land_now(&address, &target)
                    .unwrap_or_else(|_| Some(COULD_NOT_MOVE.to_owned()))
            };
            shared.tell(message.as_deref());
        });
    }

    /// The dragged window (`window_at`'s dict, or None: whatever is at layout `point` on `source` now) onto the
    /// workspace output `target` shows, not followed. Serialized with the send keys.
    pub fn drop(
        &mut self,
        window: Option<Json>,
        source: &str,
        point: Option<(i64, i64)>,
        target: &str,
    ) {
        if self.closed() {
            return;
        }
        let (source, target) = (source.to_owned(), target.to_owned());
        self.start(move |shared| {
            let message = {
                let _guard = lock(&shared.lock);
                if shared.closed() {
                    return;
                }
                shared
                    .drop_now(window.as_ref(), &source, point, &target)
                    .unwrap_or_else(|_| Some(COULD_NOT_MOVE.to_owned()))
            };
            shared.tell(message.as_deref());
        });
    }

    /// Run end, before the binds go and recovery starts: no move starts after this; waits (`timeout`, default three
    /// compositor deadlines and half a second) for one in flight, then a tenth of a second for each other job.
    pub fn close(&mut self, timeout: Option<Duration>) {
        let shared = &self.shared;
        shared.closed.store(true, Ordering::SeqCst);
        let until =
            Instant::now() + timeout.unwrap_or(shared.deadline * 3 + Duration::from_millis(500));
        loop {
            match shared.lock.try_lock() {
                Ok(_) | Err(TryLockError::Poisoned(_)) => break,
                Err(TryLockError::WouldBlock) if Instant::now() < until => {
                    thread::sleep(Duration::from_millis(5));
                }
                Err(TryLockError::WouldBlock) => break,
            }
        }
        for thread in std::mem::take(&mut self.threads) {
            let until = Instant::now() + Duration::from_millis(100);
            while !thread.is_finished() && Instant::now() < until {
                thread::sleep(Duration::from_millis(2));
            }
            if thread.is_finished() {
                let _ = thread.join();
            }
        }
    }

    /// Wait for every job started so far (tests compare after each call, as the corpus was recorded).
    pub fn settle(&mut self) {
        for thread in std::mem::take(&mut self.threads) {
            let _ = thread.join();
        }
    }
}

/// The run's sender (`control.sender`), or a recording stand-in for the routing tests.
pub struct Sender {
    /// What [`Sender::request`] was given, in a [`Sender::recording`] sender (the routing tests).
    recorded: Option<Vec<(String, String)>>,
    live: Option<WindowSender>,
}

impl Sender {
    /// `WindowSender(client, capture, fallback)`, notifying through [`notify`].
    pub fn open(run: &Run<'_>) -> Option<Sender> {
        let fallback = Some(run.fallback).filter(|f| !f.is_empty());
        let notify: Notify = Box::new(|text: &str| notify(text));
        Some(Sender {
            recorded: None,
            live: Some(WindowSender::new(
                Origin::of(run),
                run.capture,
                fallback,
                Some(notify),
                DEADLINE,
            )),
        })
    }

    /// A sender that only records its requests.
    pub fn recording() -> Sender {
        Sender {
            recorded: Some(Vec::new()),
            live: None,
        }
    }

    /// The requests a [`Sender::recording`] sender took.
    pub fn requests(&self) -> &[(String, String)] {
        self.recorded.as_deref().unwrap_or_default()
    }

    /// The worker, for the overview drag's calls and `set_names` (none in a recording sender).
    pub fn worker(&mut self) -> Option<&mut WindowSender> {
        self.live.as_mut()
    }

    /// `sender.request(op, selected)`: queued for the worker; never blocks the loop.
    pub fn request(&mut self, op: &str, selected: &str) {
        if let Some(recorded) = self.recorded.as_mut() {
            recorded.push((op.to_owned(), selected.to_owned()));
        }
        if let Some(live) = self.live.as_mut() {
            live.request(op, selected, None);
        }
    }

    /// `sender.close()`: no move starts after this; one in flight lands first.
    pub fn close(&mut self) {
        if let Some(live) = self.live.as_mut() {
            live.close(None);
        }
    }
}
