//! The key bind panel (Ctrl+Alt+K): `keybinds.BindControl` (tools/native_spike/keybinds.py), as
//! `managed.view_controls` builds it (`keybinds.load(keys_path)`, `DesktopBinds.from_text(client.binds_text())`,
//! `BindControl(control, ..., client, bind_args, desktop, notify)`), installs the run binds with its keys
//! (`bind_kwargs()`, then `installed()`, `startup_toast()`) and stops it (`keys.stop()`, before the binds go).
//!
//! The panel itself is `crate::supervisor::keybinds` (`BindControl`, which queues `Job`s, and `BindWorker`, which runs
//! them). This is the run's side of it: `keybinds.json` beside the profile, the desktop's binds read first, the jobs
//! run in order on a worker thread with its own compositor client (the Python's `_sync` / `_read_desktop` threads),
//! each in its turn of the run's bind table lock (`_menu_lock`: [`MenuLock`], shared with the binds' worker, taken on
//! the loop when the job is sent), the results taken back on the loop (`poll`), and the toasts sent as desktop
//! notifications off the loop (`_tell`). A reinstall's flags (`_typing_installed` and the panel's and options menu's
//! binds) are set in the lock by the worker, in its turn, as the Python's `_work` sets them.
//!
//! What the panel needs from the view (typing mode, the help card and the placement panel it closes, the frame ids)
//! comes through [`Host`], which the view implements; the view calls [`Keys::on_message`], [`Keys::poll`] and
//! [`Keys::deadline`], and after a config reload [`Keys::after_reload`] (right after
//! [`Binds::after_reload`](super::binds::Binds::after_reload), whose reinstall the lock lands first).

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use xr_wire::run_control::Frame;

use super::binds::{Binds, KeyArgs, MenuLock, Table, Ticket};
use super::{Frames, Run};
use crate::hyprland::Client;
use crate::lua::BindCommand;
pub use crate::supervisor::keybinds::Host;
use crate::supervisor::keybinds::{
    self as panel, Bind, BindControl, BindWorker, DesktopBinds, Job, KeyClient, WorkResult,
};

/// How long the desktop's binds may take at the start (`client.binds_text(timeout=0.5)`).
const READ_TIMEOUT: Duration = Duration::from_millis(500);
/// `stop(timeout=2.0)`: how long the run's end waits for the panel's work.
const STOP_TIMEOUT: Duration = Duration::from_secs(2);
/// The loop's next look while compositor work is out (its result is taken on a tick), and while the panel is open
/// (its capture timeout, reset arm and idle close are checked on a tick).
const WORK_TICK: Duration = Duration::from_millis(20);
const OPEN_TICK: Duration = Duration::from_millis(100);

/// A toast (`notify`: a desktop notification, `send.notify` in a run).
pub type Notify = Arc<dyn Fn(&str) + Send + Sync>;

/// `keybinds.BindControl` with its threads.
pub struct Keys {
    control: BindControl,
    /// The worker thread's queue, and its results; `None`: no compositor client, so the jobs (state only) run here.
    jobs: Option<Sender<(Job, Ticket)>>,
    results: Option<Receiver<WorkResult>>,
    worker: Option<JoinHandle<()>>,
    local: BindWorker,
    /// Jobs sent whose results have not come back.
    outstanding: usize,
    /// The bind table's lock: each job sent takes its place in it (`self.control._menu_lock`).
    lock: MenuLock,
    notify: Notify,
    toasts: Vec<JoinHandle<()>>,
}

/// The worker's compositor: the run's bind helper and a client of its own.
struct Compositor {
    client: Client,
    bind_args: Vec<String>,
}

/// The bind command `bind_args` names.
fn command(bind_args: &[String]) -> Result<BindCommand<'_>, String> {
    match bind_args {
        [python, client, control_socket, run_id, session] => Ok(BindCommand {
            python,
            client,
            control_socket,
            run_id,
            session,
        }),
        _ => Err("invalid bind arguments".into()),
    }
}

fn borrowed(binds: &[Bind]) -> Vec<(&str, &str)> {
    binds.iter().map(|(c, op)| (c.as_str(), *op)).collect()
}

impl KeyClient for Compositor {
    fn reinstall_view_binds(
        &mut self,
        typing: bool,
        binds: Option<(&[Bind], &[Bind])>,
    ) -> Result<(), String> {
        let given = binds.map(|(run, send)| (borrowed(run), borrowed(send)));
        let cmd = command(&self.bind_args)?;
        let (run, send) = match &given {
            Some((run, send)) => (Some(run.as_slice()), Some(send.as_slice())),
            None => (None, None),
        };
        self.client
            .reinstall_view_binds(&cmd, typing, run, send)
            .map_err(|e| e.to_string())
    }

    fn open_key_panel(&mut self) -> Result<(), String> {
        let cmd = command(&self.bind_args)?;
        self.client.open_key_panel(&cmd).map_err(|e| e.to_string())
    }

    fn close_key_panel(&mut self) -> Result<(), String> {
        self.client.close_key_panel().map_err(|e| e.to_string())
    }

    fn binds_text(&mut self) -> Result<String, String> {
        self.client
            .binds_text(crate::hyprland::DEADLINE)
            .map_err(|e| e.to_string())
    }

    fn menu_binds(&mut self, on: bool) -> Result<(), String> {
        if !on {
            return self.client.remove_menu_binds().map_err(|e| e.to_string());
        }
        let cmd = command(&self.bind_args)?;
        self.client
            .install_menu_binds(&cmd)
            .map_err(|e| e.to_string())
    }

    fn options_binds(&mut self, on: bool) -> Result<(), String> {
        if !on {
            return self.client.remove_menu_binds().map_err(|e| e.to_string());
        }
        let cmd = command(&self.bind_args)?;
        self.client
            .install_options_binds(&cmd)
            .map_err(|e| e.to_string())
    }
}

/// `time.monotonic()`, the panel's clock (as the view's).
fn monotonic() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

/// A Frame as the panel reads a control message: `{"op": ..., "payload": ...}`.
fn message_value(frame: &Frame) -> serde_json::Value {
    fn plain(v: &xr_wire::json::Value) -> serde_json::Value {
        use serde_json::Value as S;
        use xr_wire::json::Value as V;
        match v {
            V::Null => S::Null,
            V::Bool(b) => S::Bool(*b),
            V::Number(n) => S::Number(n.clone()),
            V::String(s) => S::String(s.clone()),
            V::Array(a) => S::Array(a.iter().map(plain).collect()),
            V::Object(o) => S::Object(o.iter().map(|(k, v)| (k.clone(), plain(v))).collect()),
        }
    }
    serde_json::json!({"op": frame.op, "payload": plain(&frame.payload)})
}

impl Keys {
    /// `managed.view_controls`'s key bind block, up to the install: the saved keys (`keybinds.json` beside the
    /// profile, `keybinds.config_path()`), the desktop's own binds read first (unknown on an error: every bind is
    /// installed, as before the panel), and the panel's worker on a client of its own. `bind_args`: the bind helper's
    /// command line (`python, client, control_socket, run_id, session`); `None`: the run installs no binds, so there
    /// is no panel either.
    pub fn open(run: &mut Run<'_>, binds: Option<&Binds>) -> Option<Keys> {
        let binds = binds?;
        let notify: Notify = Arc::new(crate::managed::real::notify);
        Some(Keys::open_at(
            run,
            binds.bind_args(),
            binds.lock().clone(),
            &panel::config_path(),
            notify,
        ))
    }

    /// [`Keys::open`] with the saved file at `path` and the toasts to `notify`. The worker's client is pinned to the
    /// compositor the run's client verified (none before a verified request: the panel then cannot take the keyboard).
    /// `lock`: the run's bind table lock ([`MenuLock`]), which orders the worker's compositor work with the binds'.
    pub fn open_at(
        run: &mut Run<'_>,
        bind_args: Vec<String>,
        lock: MenuLock,
        path: &Path,
        notify: Notify,
    ) -> Keys {
        let desktop = run
            .client
            .binds_text(READ_TIMEOUT)
            .ok()
            .map(|text| DesktopBinds::from_text(&text, &[]));
        let worker_client = run.client.identity().cloned().and_then(|identity| {
            Client::pinned(run.client.instance(), run.runtime, identity, run.session).ok()
        });
        let control = BindControl::new(
            panel::load(path),
            Some(PathBuf::from(path)),
            worker_client.is_some(),
            desktop,
            true,
            monotonic(),
        );
        let mut keys = Keys {
            local: control.worker(),
            control,
            jobs: None,
            results: None,
            worker: None,
            outstanding: 0,
            notify,
            toasts: Vec::new(),
            lock,
        };
        if let Some(client) = worker_client {
            keys.start_worker(Compositor { client, bind_args });
        }
        keys
    }

    fn start_worker(&mut self, mut compositor: Compositor) {
        let (jobs, inbox) = mpsc::channel::<(Job, Ticket)>();
        let (results, outbox) = mpsc::channel();
        let mut worker = self.control.worker();
        let lock = self.lock.clone();
        let spawned = thread::Builder::new()
            .name("xr-keybinds".into())
            .spawn(move || {
                for (job, ticket) in inbox {
                    ticket.wait();
                    let result = worker.run(job, Some(&mut compositor));
                    if let Some((typing, menu, options)) = result.reinstalled {
                        // `c._typing_installed = typing`, the panel's and options menu's binds back or gone.
                        lock.set_table(|t| {
                            *t = Table {
                                typing,
                                menu,
                                options,
                            }
                        });
                    }
                    drop(ticket);
                    if results.send(result).is_err() {
                        break;
                    }
                }
            });
        if let Ok(handle) = spawned {
            self.jobs = Some(jobs);
            self.results = Some(outbox);
            self.worker = Some(handle);
        }
    }

    /// The panel's queued work out (to the worker, or run here without a client) and its toasts sent.
    fn dispatch(&mut self, host: Option<&mut dyn Host>) {
        let mut host = host;
        for job in self.control.take_jobs() {
            match &self.jobs {
                Some(jobs) if jobs.send((job.clone(), self.lock.ticket())).is_ok() => {
                    self.outstanding += 1
                }
                _ => {
                    let result = self.local.run(job, None);
                    if let Some(host) = host.as_deref_mut() {
                        self.control.deliver(result, host);
                    }
                }
            }
        }
        for text in self.control.take_toasts() {
            let notify = self.notify.clone();
            self.toasts.retain(|t| !t.is_finished());
            if let Ok(t) = thread::Builder::new()
                .name("xr-keybinds-toast".into())
                .spawn(move || notify(&text))
            {
                self.toasts.push(t);
            }
        }
    }

    /// The worker's results that came back, handed to the panel (taken on its next `poll`, as the Python's queue).
    fn collect(&mut self, host: &mut dyn Host) {
        let Some(results) = &self.results else { return };
        while let Ok(result) = results.try_recv() {
            self.outstanding = self.outstanding.saturating_sub(1);
            self.control.deliver(result, host);
        }
    }

    /// Waits until every job sent has come back, then hands the results over: the Python's join of the panel's
    /// threads (tests, and the run's end).
    pub fn join(&mut self, host: &mut dyn Host) {
        let deadline = Instant::now() + STOP_TIMEOUT;
        if let Some(results) = &self.results {
            while self.outstanding > 0 {
                let left = deadline.saturating_duration_since(Instant::now());
                match results.recv_timeout(left) {
                    Ok(result) => {
                        self.outstanding -= 1;
                        self.control.deliver(result, host);
                    }
                    Err(_) => break,
                }
            }
        }
        for t in self.toasts.drain(..) {
            while !t.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
        }
    }

    /// `bind_kwargs()`: the view and send binds `Client::install_view_binds` takes (`binds`, `send_binds`); `None`
    /// for either is its default table.
    pub fn bind_kwargs(&self) -> KeyArgs {
        let owned = |binds: Vec<Bind>| -> Vec<(String, String)> {
            binds
                .into_iter()
                .map(|(c, op)| (c, op.to_string()))
                .collect()
        };
        match self.control.bind_kwargs() {
            Some((run, send)) => (Some(owned(run)), Some(owned(send))),
            None => (None, None),
        }
    }

    /// `keys.installed()`.
    pub fn installed(&mut self) {
        self.control.installed();
        self.dispatch(None);
    }

    /// `keys.startup_toast()`: a conflict on load is said once.
    pub fn startup_toast(&mut self) {
        self.control.startup_toast();
        self.dispatch(None);
    }

    /// `keys.on_message(message)` (`binds_toggle`, `binds_key`): the panel's frames.
    pub fn on_message(&mut self, message: &Frame, host: &mut dyn Host, out: &mut Frames) {
        self.collect(host);
        let bytes = self.control.on_message(&message_value(message), host);
        out.bytes.extend_from_slice(&bytes);
        self.dispatch(Some(host));
    }

    /// `keys.poll(now)`: the worker's results, the capture timeout, the reset arm, the idle close.
    pub fn poll(&mut self, now: f64, host: &mut dyn Host, out: &mut Frames) {
        self.collect(host);
        let bytes = self.control.poll(now, host);
        out.bytes.extend_from_slice(&bytes);
        self.dispatch(Some(host));
    }

    /// `keys.close()`: an open panel closes (the help card, the placement panel or typing mode takes over).
    pub fn close(&mut self, host: &mut dyn Host, out: &mut Frames) {
        self.collect(host);
        let bytes = self.control.close(host);
        out.bytes.extend_from_slice(&bytes);
        self.dispatch(Some(host));
    }

    /// When [`Keys::poll`] is next due: soon while work is out or the panel is open.
    pub fn deadline(&self, now: Instant) -> Option<Instant> {
        if self.outstanding > 0 {
            return Some(now + WORK_TICK);
        }
        self.control.open.then_some(now + OPEN_TICK)
    }

    /// `keys.after_reload()`: the run binds were put back with this keymap; an open panel takes the keyboard again.
    pub fn after_reload(&mut self, host: &mut dyn Host) {
        self.control.after_reload(host);
        self.dispatch(Some(host));
    }

    /// The panel's state, for the view (`keys_open`) and the tests.
    pub fn panel(&self) -> &BindControl {
        &self.control
    }

    /// `keys.stop()`: no compositor change after this (the binds' removal closes an open panel); the work already
    /// out gets up to two seconds.
    pub fn stop(&mut self) {
        self.control.stop();
        self.jobs = None; // the worker ends after the jobs it has
        self.results = None;
        let deadline = Instant::now() + STOP_TIMEOUT;
        let handles = self.worker.take().into_iter().chain(self.toasts.drain(..));
        for t in handles {
            while !t.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if t.is_finished() {
                let _ = t.join();
            }
        }
    }
}

impl Drop for Keys {
    fn drop(&mut self) {
        self.control.stop();
    }
}
