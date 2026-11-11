//! The pills' desktop changes (Ultrawide, Add, Remove, Scale, Resolution) and Ctrl+Alt+R's reset:
//! `desktops.DesktopManager` (tools/native_spike/desktops.py: the broker steps off the loop, `specs`, `guard()`,
//! `wide()`, `desktops()`, `held()`, `take_result()`, `save()`, `close()`), made in `managed._run_owned` (before the
//! guard's first check, a capture run with the ownership broker) and wired in `managed.view_controls`
//! (`desktops.move_windows = _window_mover(client)`, `desktops.leaving = windows.leaving`); and the view's side
//! (tools/native_spike/view.py: `desktop_action`, `_start_change`, `_desktops_frame`, `_desktops_changed`,
//! `_desktops_done`, `layout_reset`, `_reset_tick`, `_reset_desktops`, `_reset_done`) as [`Manager::desktop_action`],
//! [`Manager::poll`] and [`Manager::layout_reset`] over a [`View`]. A later renderer captures [`Manager::specs`]
//! under [`Manager::guard`].
//!
//! The plan is `crate::desktops`, the fit `crate::fit_windows`, and the broker's steps `crate::ownership`'s
//! (`create_live`, `reshape`, `remove_live`). The worker is a thread with its own client, pinned to the run's
//! compositor (`Client::pinned`), and its own handle on the run's journal (each transaction reloads it under its
//! lock); the broker client is the run's own, shared, so its request ids stay one sequence as in Python.
//!
//! A guard change goes to the loop through [`Manager::guard_change`]: `pause_guard()` as a change starts,
//! `replace_guard(manager.guard())` once it is done (or did not start). Building that guard cannot fail once the run
//! has a renderer (the first launch built the same command); if it did, the change is replaced by no guard, as the
//! Python's `_contained` keeps a failed `replace_guard` from ending the run.
//!
//! Not here: the view itself. [`View`] is what the view's port implements on `ViewControl` (its state, frames and
//! listeners); until then the run's view is the stub and no change starts. The profile store
//! (`profiles.PlacementStore.save_desktops`) is the view's too, handed in as [`Manager::store`].

use std::path::PathBuf;
use std::process::Command;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::Duration;

use xr_wire::plugin::OutputSpec;

use super::Run;
use super::view::ViewControl;
use super::window_memory::{Leaving, to_serde};
use crate::adopt::output_number;
use crate::desktops::{self as plan, Action, Plan, Spec, Step};
use crate::fit_windows;
use crate::geometry::{DEFAULT_PLACEMENT, Desktop, Placement};
use crate::hyprland::{Client, DEADLINE, Identity, Query};
use crate::journal::Journal;
use crate::ownership::Broker;
use crate::supervisor::GuardChange;
use crate::supervisor::view::{
    self as frames, DEFAULT_RESET_PLACEMENT, DEFAULT_SPACING, RESET_LEAVING_PLACEMENT, ViewState,
    eased_to, smootherstep,
};

/// `view.RESET_ARM_S`: the reset's second press must come this soon after the first.
pub const RESET_ARM_S: f64 = 3.0;
/// `view.RESET_ANIM_S`: the reset's ease.
pub const RESET_ANIM_S: f64 = 0.45;
/// `view.RESET_TOASTS`.
pub const RESET_CONFIRM: &str = "Press Ctrl+Alt+R again to reset all monitors";
pub const RESET_DONE: &str = "All monitors reset to three flat 1920x1080 desktops at 100%.";
pub const RESET_VIDEO: &str =
    "Close the VR video first (Esc), then Ctrl+Alt+R resets the desktops.";
pub const RESET_BUSY: &str =
    "A desktop change is still running; press Ctrl+Alt+R again in a moment.";

/// Where the worker reaches the run: its broker, its journal, its compositor, and the guard for a set of outputs.
pub struct Env {
    pub broker: Arc<Mutex<Broker>>,
    /// The run's journal file (`journal.json`).
    pub journal: PathBuf,
    pub run_id: String,
    pub session: String,
    pub instance: String,
    pub runtime: PathBuf,
    /// The compositor the run's client verified.
    pub identity: Identity,
    /// `guard_factory(specs)`: the run's guard command for these outputs (`make_guard`), None if it cannot be built.
    pub guard: Box<dyn Fn(&[OutputSpec]) -> Option<Command> + Send>,
}

/// `move_windows(source, target)`: one output's windows to another before a reset's removal, on the worker.
pub type MoveWindows = Arc<dyn Fn(&mut Client, &str, &str) + Send + Sync>;
/// `store.save_desktops(desktops, placements, rolls)`: the profile takes the run's desktops (best effort).
pub type Store = Box<dyn FnMut(&[Desktop], &[Placement], Option<&[i64]>) + Send>;
/// `done(success, detail)` of a change (`send.complete`): the options menu's output change waits on it.
pub type Done = Box<dyn FnOnce(bool, &str) + Send>;

/// What one change came to: `take_result()`'s `(plan, error, steps done)`.
#[derive(Clone, Debug, PartialEq)]
pub struct Outcome {
    pub change: Plan,
    pub error: Option<String>,
    pub done: Vec<&'static str>,
}

/// Diagnostics (`changes`, `failures`, `fitted`, `fit_failures`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub changes: usize,
    pub failures: usize,
    pub fitted: usize,
    pub fit_failures: usize,
}

/// What the worker and the loop share.
struct Shared {
    specs: Vec<Spec>,
    result: Option<Outcome>,
    stop: bool,
    counts: Counts,
}

/// What the worker needs, cloned for each change.
#[derive(Clone)]
struct Worker {
    broker: Arc<Mutex<Broker>>,
    journal: PathBuf,
    run_id: String,
    session: String,
    instance: String,
    runtime: PathBuf,
    identity: Identity,
    move_windows: Option<MoveWindows>,
    leaving: Option<Leaving>,
    fit: bool,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

/// A broker create spec of the wire.
pub fn wire_spec(spec: &Spec) -> OutputSpec {
    OutputSpec {
        id: spec.id.clone(),
        name: spec.name.clone(),
        index: spec.index,
        width: spec.desktop.width,
        height: spec.desktop.height,
        refresh_millihz: spec.refresh_millihz,
        scale: spec.desktop.scale,
        x: spec.x,
        y: spec.y,
    }
}

/// A wire spec as the plan's; None if it is not a valid desktop.
pub fn plan_spec(spec: &OutputSpec) -> Option<Spec> {
    Some(Spec {
        id: spec.id.clone(),
        name: spec.name.clone(),
        index: spec.index,
        desktop: Desktop::new(spec.width, spec.height, spec.scale).ok()?,
        refresh_millihz: spec.refresh_millihz,
        x: spec.x,
        y: spec.y,
    })
}

fn sorted(mut specs: Vec<Spec>) -> Vec<Spec> {
    specs.sort_by_key(|s| output_number(&s.name));
    specs
}

/// `_window_mover(client)`: a reset's removed desktop's windows to `target` first, whole workspaces, as adoption
/// moves them; what cannot move goes to the fallback with the removal. Never fails.
pub fn window_mover() -> MoveWindows {
    Arc::new(|client: &mut Client, source: &str, target: &str| {
        if crate::managed::real::adopt_onto(client, source, &[target]).is_err() {
            crate::managed::real::notify(&format!(
                "Could not move {source}'s windows to {target}: they go to your normal monitor."
            ));
        }
    })
}

impl Worker {
    fn broker(&self) -> MutexGuard<'_, Broker> {
        lock(&self.broker)
    }

    /// `_run`: the steps in order; on a failure, the ones whose ownership can be proven again are reversed.
    fn run(&self, shared: &Mutex<Shared>, change: &Plan) -> (Option<String>, Vec<&'static str>) {
        let original: Vec<Spec> = lock(shared).specs.clone();
        let mut attempted: Vec<Step<'_>> = Vec::new();
        let mut done = Vec::new();
        let client = Client::pinned(
            &self.instance,
            &self.runtime,
            self.identity.clone(),
            &self.session,
        )
        .map_err(|e| e.to_string());
        let journal = Journal::open(&self.journal, &self.run_id, &self.session, true)
            .map_err(|e| e.to_string());
        let (mut client, mut journal) = match (client, journal) {
            (Ok(c), Ok(j)) => (c, j),
            (Err(e), _) | (_, Err(e)) => {
                return (
                    Some(format!("{e}; previous output specs restored and verified")),
                    done,
                );
            }
        };
        let steps = (|| -> Result<(), String> {
            for step in change.steps() {
                if lock(shared).stop {
                    return Err("the session is ending".into());
                }
                attempted.push(step.clone());
                match step {
                    Step::Remove(spec) => {
                        if let Some(leaving) = &self.leaving {
                            leaving.call(&mut client, &spec.name, change.windows_to.as_deref());
                        }
                        if let (Some(to), Some(mover)) = (&change.windows_to, &self.move_windows) {
                            mover(&mut client, &spec.name, to); // best effort: the removal evacuates what stays
                        }
                        if !self.broker().remove_live(
                            &mut client,
                            &spec.id,
                            &spec.name,
                            &mut journal,
                        )? {
                            return Err(format!(
                                "{} could not be removed (a pinned window, or a window that would not move); it stays",
                                spec.name
                            ));
                        }
                        lock(shared).specs.retain(|s| s.id != spec.id);
                        done.push("remove");
                    }
                    Step::Reshape(spec) => {
                        // Recorded before the call: if it fails, the output may hold either geometry and is untrusted
                        // either way; the guard checks the new one (and ends the run if it does not hold).
                        replace(&mut lock(shared).specs, spec);
                        self.broker().reshape(&mut client, &wire_spec(spec))?;
                        done.push("reshape");
                    }
                    Step::Create(spec) => {
                        self.broker()
                            .create_live(&mut client, &wire_spec(spec), &mut journal)?;
                        let mut s = lock(shared);
                        let mut specs = std::mem::take(&mut s.specs);
                        specs.push(spec.clone());
                        s.specs = sorted(specs);
                        done.push("create");
                    }
                }
            }
            Ok(())
        })();
        let Err(mut error) = steps else {
            self.fit_windows(shared, change, &mut client);
            return (None, done);
        };
        if error.is_empty() {
            error = "Error".into();
        }
        // Only reverse operations whose ownership can be proven again. Removed workspaces and windows cannot be
        // silently reconstructed; recovery owns them.
        let mut recovery = false;
        for step in attempted.iter().rev() {
            let undone = (|| -> Result<bool, String> {
                match step {
                    Step::Reshape(spec) => {
                        let Some(previous) = original.iter().find(|s| s.id == spec.id) else {
                            return Ok(true);
                        };
                        self.broker().reshape(&mut client, &wire_spec(previous))?;
                        replace(&mut lock(shared).specs, previous);
                        Ok(true)
                    }
                    Step::Create(spec) => {
                        let observed = self.broker().inspect(&mut client, &spec.id)?;
                        let live = observed.get("state").and_then(xr_wire::json::Value::as_str)
                            == Some("live");
                        if live
                            && self.broker().remove_live(
                                &mut client,
                                &spec.id,
                                &spec.name,
                                &mut journal,
                            )?
                        {
                            lock(shared).specs.retain(|s| s.id != spec.id);
                            Ok(true)
                        } else {
                            Ok(false)
                        }
                    }
                    Step::Remove(_) => Ok(false),
                }
            })();
            if !matches!(undone, Ok(true)) {
                recovery = true;
            }
        }
        error.push_str(if recovery {
            "; recovery required for unverified outputs"
        } else {
            "; previous output specs restored and verified"
        });
        (Some(error), done)
    }

    /// `_fit_windows`: the change went through, and floating windows that would stick out of a desktop it shrank go
    /// back inside it. Best effort: a window left sticking out is no reason to fail a change that worked.
    fn fit_windows(&self, shared: &Mutex<Shared>, change: &Plan, client: &mut Client) {
        if !self.fit || lock(shared).stop || change.shrunk.is_empty() {
            return;
        }
        let fitted = (|| -> Result<usize, String> {
            let mut read = |q| {
                client
                    .query(q, DEADLINE)
                    .map(|v| to_serde(&v))
                    .map_err(|e| e.to_string())
            };
            let (clients, workspaces, monitors) = (
                read(Query::Clients)?,
                read(Query::Workspaces)?,
                read(Query::Monitors)?,
            );
            let mut moves = Vec::new();
            for name in &change.shrunk {
                if let Some(s) = change.specs.iter().find(|s| &s.name == name) {
                    let output = fit_windows::Output {
                        name: &s.name,
                        x: s.x,
                        y: s.y,
                        desktop: s.desktop,
                    };
                    moves.extend(
                        fit_windows::plan(&clients, &workspaces, &monitors, output)
                            .map_err(|e| e.0.to_owned())?,
                    );
                }
            }
            if !moves.is_empty() && !lock(shared).stop {
                let lua: Vec<crate::lua::fit_windows::Move<'_>> = moves
                    .iter()
                    .map(|m| crate::lua::fit_windows::Move {
                        address: &m.address,
                        x: m.x,
                        y: m.y,
                        w: m.w,
                        h: m.h,
                    })
                    .collect();
                client.fit_windows(&lua).map_err(|e| e.to_string())?;
            }
            Ok(moves.len())
        })();
        let mut s = lock(shared);
        match fitted {
            Ok(n) => s.counts.fitted += n,
            Err(_) => s.counts.fit_failures += 1,
        }
    }
}

fn replace(specs: &mut Vec<Spec>, spec: &Spec) {
    let mut out: Vec<Spec> = specs.drain(..).filter(|s| s.id != spec.id).collect();
    out.push(spec.clone());
    *specs = sorted(out);
}

/// `ViewControl.reset`: Ctrl+Alt+R in progress.
struct Reset {
    at: f64,
    names: Vec<String>,
    from: Vec<Placement>,
    to: Vec<Placement>,
    rolls: Vec<i64>,
    easing: bool,
    /// The removed desktop's windows: (from, to).
    moved: Option<(String, String)>,
}

/// `desktops.DesktopManager`, and the view's change state (`remove_armed_at`, `reset`, `reset_armed_at`,
/// `_desktops_before`).
pub struct Manager {
    shared: Arc<Mutex<Shared>>,
    worker: Worker,
    thread: Option<JoinHandle<()>>,
    guard: Box<dyn Fn(&[OutputSpec]) -> Option<Command> + Send>,
    /// The guard change the loop has still to make.
    pending: Option<GuardChange>,
    /// `desktops.store`: the profile store (the view's).
    pub store: Option<Store>,
    remove_armed_at: Option<f64>,
    reset: Option<Reset>,
    reset_armed_at: Option<f64>,
    before: Option<(Vec<String>, Vec<Placement>)>,
    /// `resets`: Ctrl+Alt+R resets started (diagnostics).
    pub resets: usize,
}

/// What the change flow needs of the view (`view.ViewControl`): its state, its frames, its toasts and the other
/// features' listeners. The view's port implements it on `ViewControl`.
pub trait View {
    fn state(&mut self) -> &mut ViewState;
    /// `self.clock()`, seconds.
    fn now(&self) -> f64;
    fn video_active(&self) -> bool;
    /// `_tell`: a toast.
    fn tell(&mut self, text: &str);
    /// `flush()`: a pending panel change saved first.
    fn flush(&mut self);
    /// `save_due = None`: the default replaces anything pending.
    fn cancel_save(&mut self);
    /// `_sync_menu_binds()`: the panel's modal keys follow it.
    fn sync_menu_binds(&mut self);
    /// `_frame()`, `_placement_frame()`, `_menu_frame()`.
    fn view_frame(&mut self) -> Vec<u8>;
    fn placement_frame(&mut self) -> Vec<u8>;
    fn menu_frame(&mut self) -> Vec<u8>;
    /// The next frame's request id (`next_id`, then incremented).
    fn next_id(&mut self) -> u64;
    fn run_id(&self) -> &str;
    fn session(&self) -> &str;
    /// `_desktops_changed()`: the overview pointer, the sender and the desktop listeners (the sampler's
    /// `set_names`, the window memory's [`super::window_memory::Tracker::set_names`]) take the new names.
    fn desktops_changed(&mut self);
    /// `windows.mark()`: a shrink pulled windows in, a removal moved them.
    fn windows_moved(&mut self);
    /// `_rolls()` and `_set_rolls(rolls)`: the levelling rolls, in `state.names` order (None without a pointer).
    fn rolls(&self) -> Option<Vec<i64>>;
    fn set_rolls(&mut self, rolls: &[i64]);
    /// `_rolls_seen = None`.
    fn forget_rolls(&mut self);
    /// `_on_menu('menu_toggle')` on an open panel (saved first, panel keys released) and `_zoom_out()`.
    fn close_menu(&mut self) -> Vec<u8>;
    fn zoom_out(&mut self) -> Vec<u8>;
    /// `placements.reset(previous)` (`desktops`, `placements`, `rolls`, `spacing`): the saved setup replaced now.
    fn reset_store(
        &mut self,
        desktops: Option<&[Desktop]>,
        placements: &[Placement],
        rolls: &[i64],
        spacing: i64,
    );
    /// `settings.save(state.settings())`, best effort.
    fn save_settings(&mut self);
    /// `save_setup()`: the monitor setup for the next start.
    fn save_setup(&mut self);
    /// `options_output_done`, taken: the options menu's output change waiting on this one.
    fn take_output_done(&mut self) -> Option<Done> {
        None
    }
}

impl Manager {
    /// `DesktopManager(broker, journal, specs, guard_factory=make_guard, windows=client)`. None without outputs (a
    /// run has at least one).
    pub fn open(specs: &[OutputSpec], env: Env) -> Option<Manager> {
        let specs = sorted(specs.iter().map(plan_spec).collect::<Option<Vec<_>>>()?);
        specs.first()?;
        Some(Manager {
            shared: Arc::new(Mutex::new(Shared {
                specs,
                result: None,
                stop: false,
                counts: Counts::default(),
            })),
            worker: Worker {
                broker: env.broker,
                journal: env.journal,
                run_id: env.run_id,
                session: env.session,
                instance: env.instance,
                runtime: env.runtime,
                identity: env.identity,
                move_windows: None,
                leaving: None,
                fit: true,
            },
            thread: None,
            guard: env.guard,
            pending: None,
            store: None,
            remove_armed_at: None,
            reset: None,
            reset_armed_at: None,
            before: None,
            resets: 0,
        })
    }

    /// The `view_controls` wiring: the reset's window mover (`desktops.move_windows = _window_mover(client)`). The
    /// view's port takes `wide()` and `desktops()` into its state (`control.state.wide`, `control.state.sizes`).
    pub fn attach(mut self, _run: &Run<'_>, _view: Option<&mut ViewControl>) -> Option<Manager> {
        self.worker.move_windows = Some(window_mover());
        Some(self)
    }

    /// `desktops.leaving = windows.leaving`.
    pub fn set_leaving(&mut self, leaving: Leaving) {
        self.worker.leaving = Some(leaving);
    }

    /// `move_windows`, for a test's own mover (or none).
    pub fn set_move_windows(&mut self, mover: Option<MoveWindows>) {
        self.worker.move_windows = mover;
    }

    /// `windows=None`: no floating window is pulled in after a shrink.
    pub fn set_fit(&mut self, fit: bool) {
        self.worker.fit = fit;
    }

    /// `manager.specs`: the live desktops, which a pill may have changed since the start.
    pub fn specs(&self) -> Option<Vec<OutputSpec>> {
        Some(lock(&self.shared).specs.iter().map(wire_spec).collect())
    }

    fn plan_specs(&self) -> Vec<Spec> {
        lock(&self.shared).specs.clone()
    }

    pub fn names(&self) -> Vec<String> {
        lock(&self.shared)
            .specs
            .iter()
            .map(|s| s.name.clone())
            .collect()
    }

    /// Each desktop's size and scale.
    pub fn desktops(&self) -> Vec<Desktop> {
        lock(&self.shared).specs.iter().map(|s| s.desktop).collect()
    }

    pub fn wide(&self) -> Vec<bool> {
        self.desktops().iter().map(|d| d.is_ultrawide()).collect()
    }

    pub fn counts(&self) -> Counts {
        lock(&self.shared).counts
    }

    /// `held()`: every name the run's journal still holds (its outputs, and a failed create's record until
    /// recovery), which `create_live` refuses to create again.
    pub fn held(&self) -> Result<Vec<String>, String> {
        let w = &self.worker;
        Journal::open(&w.journal, &w.run_id, &w.session, true)
            .and_then(|mut j| j.live_outputs())
            .map(|o| o.into_iter().map(|o| o.name).collect())
            .map_err(|e| format!("the run journal could not be read: {e}"))
    }

    /// `busy()`: a change running, or done and not yet collected.
    pub fn busy(&self) -> bool {
        self.thread.as_ref().is_some_and(|t| !t.is_finished())
            || lock(&self.shared).result.is_some()
    }

    /// `guard()`: the run's guard for the outputs as they are now.
    pub fn guard(&self) -> Option<Command> {
        (self.guard)(&self.specs().unwrap_or_default())
    }

    /// The guard change a change in flight needs, once: `pause_guard()` as it starts, `replace_guard(guard())` once
    /// it is done.
    pub fn guard_change(&mut self) -> Option<GuardChange> {
        self.pending.take()
    }

    fn pause_guard(&mut self) {
        self.pending = Some(GuardChange::Pause);
    }

    fn replace_guard(&mut self) {
        self.pending = Some(GuardChange::Replace(self.guard()));
    }

    /// `plan(manager.specs, placements, action, index, held=manager.held())`, laid out from the first desktop's
    /// corner as the view's call does.
    pub fn plan(
        &self,
        placements: &[Placement],
        action: Action,
        index: usize,
    ) -> Result<Plan, String> {
        let held = self.held()?;
        let held: Vec<&str> = held.iter().map(String::as_str).collect();
        let mut fresh = || plan::random_id().unwrap_or_default();
        plan::plan(
            &self.plan_specs(),
            placements,
            action,
            index,
            None,
            &held,
            &mut fresh,
        )
        .map_err(|e| e.0.to_owned())
    }

    /// `start(change, done)`: `change` on the worker. False if one is still running or not yet collected, or the
    /// run is ending.
    pub fn start(&mut self, change: Plan, done: Option<Done>) -> bool {
        if lock(&self.shared).stop || self.busy() {
            if let Some(done) = done {
                done(false, "Output transaction busy or stopped");
            }
            return false;
        }
        let (worker, shared) = (self.worker.clone(), Arc::clone(&self.shared));
        self.thread = Some(std::thread::spawn(move || {
            let (error, steps) = worker.run(&shared, &change);
            let ok = error.is_none();
            let detail = error
                .clone()
                .unwrap_or_else(|| "Broker identities/specs verified".into());
            lock(&shared).result = Some(Outcome {
                change,
                error,
                done: steps,
            });
            if let Some(done) = done {
                done(ok, &detail);
            }
        }));
        true
    }

    /// `wait_idle`: the worker done (not collected).
    pub fn join(&mut self) {
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }

    /// `take_result()`: the change's outcome once the worker finished, collected once.
    pub fn take_result(&mut self) -> Option<Outcome> {
        if self.thread.as_ref().is_some_and(|t| !t.is_finished()) {
            return None;
        }
        self.join();
        let mut s = lock(&self.shared);
        let result = s.result.take()?;
        if result.error.is_none() {
            s.counts.changes += 1;
        } else {
            s.counts.failures += 1;
        }
        Some(result)
    }

    /// `save(placements, rolls)`: the profile takes the run's desktops now (best effort).
    pub fn save(&mut self, placements: &[Placement], rolls: Option<&[i64]>) {
        let desktops = self.desktops();
        let Some(store) = self.store.as_mut() else {
            return;
        };
        if placements.len() != desktops.len() {
            return;
        }
        let rolls = rolls.filter(|r| r.len() == desktops.len());
        store(&desktops, placements, rolls);
    }

    /// `desktops.close()`: no change starts now; the step in flight lands before recovery (each is bounded by the
    /// broker's deadline), and no later one runs.
    pub fn close(&mut self) {
        lock(&self.shared).stop = true;
        self.join();
    }

    // --- the view's side --------------------------------------------------------------------------------------------

    /// `desktop_action(desktop, action)`: Ultrawide / Add / Remove pressed on `desktop`'s open panel (or U / N /
    /// Shift+Delete), a Scale step or a Resolution picked. Remove's first press only arms it. The frames that go
    /// before the change: the panel showing it busy and, for a removal, the renderer's new desktop set. Empty if
    /// nothing starts.
    pub fn desktop_action(
        &mut self,
        view: &mut dyn View,
        desktop: usize,
        action: Action,
    ) -> Vec<u8> {
        if action == Action::Reset || desktop >= view.state().names.len() {
            return Vec::new();
        }
        let video = view.video_active();
        let s = view.state();
        if s.typing || video || !s.menu_open || s.menu_desktop != desktop {
            return Vec::new();
        }
        let now = view.now();
        if view.state().desktops_busy || self.busy() {
            return Vec::new();
        }
        let s = view.state();
        (s.scale_picker, s.resolution_picker, s.background_picker) = (false, false, false);
        let armed = s.remove_armed
            && self
                .remove_armed_at
                .is_some_and(|at| now - at < plan::REMOVE_ARM_S);
        if action == Action::Remove && !armed {
            s.remove_armed = true; // "Remove?": a second press removes
            self.remove_armed_at = Some(now);
            return view.menu_frame();
        }
        s.remove_armed = false;
        let placements = s.placements.clone();
        match self.plan(&placements, action, desktop) {
            Ok(change) => self.start_change(view, change),
            Err(e) => {
                view.tell(&e);
                view.menu_frame()
            }
        }
    }

    fn desktops_frame(&self, view: &mut dyn View, names: &[String], renew: &[String]) -> Vec<u8> {
        let id = view.next_id();
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let renew: Vec<&str> = renew.iter().map(String::as_str).collect();
        frames::desktops_frame(
            view.run_id(),
            view.session(),
            id,
            &names,
            &renew,
            self.reset.is_some(),
        )
        .map(String::into_bytes)
        .unwrap_or_default()
    }

    /// `_start_change(change)`: a panel button's change, or Ctrl+Alt+R's: the frames that go before it.
    fn start_change(&mut self, view: &mut dyn View, change: Plan) -> Vec<u8> {
        view.flush(); // a pending panel change is saved first
        let s = view.state();
        self.before = Some((s.names.clone(), s.placements.clone()));
        let (wide_before, selected_before) = (s.wide.clone(), s.selected);
        let menu_before = (s.menu_open, s.menu_desktop);
        let sizes_before = s.sizes.clone();
        let mut out = Vec::new();
        if let Some(removed) = &change.remove {
            // The renderer lets go of that output before the broker removes it.
            let s = view.state();
            let gone = s
                .names
                .iter()
                .position(|n| *n == removed.name)
                .unwrap_or(usize::MAX);
            let keep: Vec<String> = s
                .names
                .iter()
                .filter(|n| **n != removed.name)
                .cloned()
                .collect();
            let skip = |i: usize| i != gone;
            let placements: Vec<Placement> = s
                .placements
                .iter()
                .enumerate()
                .filter(|(i, _)| skip(*i))
                .map(|(_, p)| *p)
                .collect();
            let wide: Vec<bool> = s
                .wide
                .iter()
                .enumerate()
                .filter(|(i, _)| skip(*i))
                .map(|(_, w)| *w)
                .collect();
            let sizes: Option<Vec<Desktop>> = s.sizes.as_ref().map(|d| {
                d.iter()
                    .enumerate()
                    .filter(|(i, _)| skip(*i))
                    .map(|(_, d)| *d)
                    .collect()
            });
            out.extend(self.desktops_frame(view, &keep, &[]));
            set_desktops(
                view.state(),
                &keep,
                &placements,
                &wide,
                None,
                sizes.as_deref(),
            );
            view.sync_menu_binds(); // its panel collapsed
            out.extend(view.view_frame());
            out.extend(view.placement_frame());
            view.desktops_changed();
        }
        self.pause_guard();
        view.state().desktops_busy = true;
        let done = view.take_output_done();
        let removing = change.remove.is_some();
        if !self.start(change, done) {
            view.state().desktops_busy = false;
            if removing {
                let (names, placements) = self.before.clone().unwrap_or_default();
                let s = view.state();
                set_desktops(
                    s,
                    &names,
                    &placements,
                    &wide_before,
                    None,
                    sizes_before.as_deref(),
                );
                s.selected = selected_before;
                (s.menu_open, s.menu_desktop) = menu_before;
                view.sync_menu_binds();
                out.extend(self.desktops_frame(view, &names, &[]));
                out.extend(view.view_frame());
                out.extend(view.placement_frame());
                view.desktops_changed();
            }
            self.before = None;
            self.replace_guard();
        }
        out.extend(view.menu_frame());
        out
    }

    /// `_desktops_done(change, error, done)`: the worker finished. The run's outputs are what the broker reports
    /// now: the renderer, the view, the panel, the guard and the profile follow them.
    fn desktops_done(&mut self, view: &mut dyn View, outcome: Outcome) -> Vec<u8> {
        let Outcome { change, error, .. } = outcome;
        let (names_before, placements_before) = self
            .before
            .take()
            .unwrap_or_else(|| (view.state().names.clone(), view.state().placements.clone()));
        let mut by_name: Vec<(String, Placement)> =
            names_before.into_iter().zip(placements_before).collect();
        if error.is_none() {
            for (s, p) in change.specs.iter().zip(&change.placements) {
                match by_name.iter_mut().find(|(n, _)| *n == s.name) {
                    Some(entry) => entry.1 = *p,
                    None => by_name.push((s.name.clone(), *p)),
                }
            }
        }
        let names = self.names();
        let placements: Vec<Placement> = names
            .iter()
            .map(|n| {
                by_name
                    .iter()
                    .find(|(m, _)| m == n)
                    .map_or(DEFAULT_PLACEMENT, |(_, p)| *p)
            })
            .collect();
        let focus = match (&error, change.focus) {
            (None, Some(f)) => names.iter().position(|n| *n == change.specs[f].name),
            _ => None,
        };
        let was_open = view.state().menu_open;
        let (wide, sizes) = (self.wide(), self.desktops());
        set_desktops(
            view.state(),
            &names,
            &placements,
            &wide,
            focus,
            Some(&sizes),
        );
        view.state().desktops_busy = false;
        let mut resized: Vec<String> = change
            .resized
            .iter()
            .filter(|n| names.contains(n))
            .cloned()
            .collect();
        resized.sort(); // a slide along the row keeps its capture
        let mut out = self.desktops_frame(view, &names, &resized);
        out.extend(view.view_frame());
        out.extend(view.placement_frame());
        out.extend(view.menu_frame());
        if was_open != view.state().menu_open {
            view.sync_menu_binds();
        }
        self.replace_guard();
        let (placements, rolls) = (view.state().placements.clone(), view.rolls());
        self.save(&placements, rolls.as_deref());
        view.desktops_changed();
        view.windows_moved();
        if change.action == Action::Reset && self.reset.is_some() {
            out.extend(self.reset_done(view, error.as_deref())); // it says how it went
            return out;
        }
        if let Some(error) = error {
            view.tell(&error);
        }
        out
    }

    /// The desktops' share of `ViewControl.poll(now)`: the reset's ease, a change done, Remove disarmed after a
    /// while.
    pub fn poll(&mut self, view: &mut dyn View, now: f64) -> Vec<u8> {
        let mut out = self.reset_tick(view, now);
        if let Some(outcome) = self.take_result() {
            out.extend(self.desktops_done(view, outcome));
        }
        let expired = self
            .remove_armed_at
            .is_none_or(|at| now - at >= plan::REMOVE_ARM_S);
        if view.state().remove_armed && expired {
            view.state().remove_armed = false;
            out.extend(view.menu_frame());
        }
        out
    }

    /// `ViewControl.layout_reset()` (Ctrl+Alt+R). The first press only asks (a toast); a second within
    /// [`RESET_ARM_S`] resets: an open panel collapses, a maximized desktop goes back to the overview, the saved
    /// setup is replaced by the default (the layout it replaces kept as a backup), the spacing goes back to its
    /// default, then every desktop eases flat and level ([`Manager::poll`], [`RESET_ANIM_S`]), one the plan will
    /// remove dropping away, before the desktops change. Empty when it only asks or is ignored (a toast says why).
    pub fn layout_reset(&mut self, view: &mut dyn View) -> Vec<u8> {
        if self.reset.is_some() || view.state().typing {
            return Vec::new(); // one at a time; in typing mode its key is released (stale)
        }
        if view.video_active() {
            view.tell(RESET_VIDEO);
            return Vec::new();
        }
        if view.state().desktops_busy || self.busy() {
            view.tell(RESET_BUSY);
            return Vec::new();
        }
        let now = view.now();
        if self.reset_armed_at.is_none_or(|at| now - at >= RESET_ARM_S) {
            self.reset_armed_at = Some(now);
            view.tell(RESET_CONFIRM);
            return Vec::new();
        }
        self.reset_armed_at = None;
        let mut out = if view.state().menu_open {
            view.close_menu() // saved first, panel keys released
        } else {
            Vec::new()
        };
        if view.state().zoomed {
            out.extend(view.zoom_out()); // the reset is seen in the overview
        }
        view.cancel_save(); // the default replaces anything pending
        let names = view.state().names.clone();
        let rolls = view.rolls().unwrap_or_else(|| vec![0; names.len()]);
        let mut order = names.clone();
        order.sort_by_key(|n| output_number(n));
        let leaving: Vec<String> = order.into_iter().skip(plan::RESET_COUNT).collect();
        let from = view.state().placements.clone();
        let to = names
            .iter()
            .map(|n| {
                if leaving.contains(n) {
                    RESET_LEAVING_PLACEMENT
                } else {
                    DEFAULT_RESET_PLACEMENT
                }
            })
            .collect();
        self.reset = Some(Reset {
            at: now,
            names,
            from: from.clone(),
            to,
            rolls: rolls.clone(),
            easing: true,
            moved: None,
        });
        self.resets += 1;
        let spacing = view.state().spacing;
        let live = self.desktops();
        view.reset_store(Some(&live), &from, &rolls, spacing);
        if spacing != DEFAULT_SPACING {
            view.state().spacing = DEFAULT_SPACING;
            out.extend(view.view_frame());
            view.save_settings();
        }
        view.state().desktops_busy = true; // the panel's desktop row waits for it
        out.extend(self.reset_tick(view, now));
        out
    }

    /// `_reset_tick(now)`: every desktop's placement and roll [`RESET_ANIM_S`] along a smootherstep to flat and
    /// level; at its end the desktops change.
    fn reset_tick(&mut self, view: &mut dyn View, now: f64) -> Vec<u8> {
        let Some(r) = self.reset.as_ref().filter(|r| r.easing) else {
            return Vec::new();
        };
        let same = view.state().names == r.names;
        let t = if !same || RESET_ANIM_S <= 0.0 {
            1.0
        } else {
            (now - r.at) / RESET_ANIM_S
        };
        let e = smootherstep(t);
        let mut out = Vec::new();
        if same {
            let placements: Vec<Placement> = r
                .from
                .iter()
                .zip(&r.to)
                .map(|(p, to)| eased_to(p, to, e))
                .collect();
            let rolls: Vec<i64> = r
                .rolls
                .iter()
                .map(|x| (*x as f64 * (1.0 - e)).round_ties_even() as i64)
                .collect();
            if placements != view.state().placements {
                view.state().placements = placements;
                out.extend(view.placement_frame());
                if view.state().menu_open {
                    out.extend(view.menu_frame());
                }
            }
            view.set_rolls(&rolls); // the free pointer's `roll` frame sends them
        }
        if t >= 1.0 {
            out.extend(self.reset_desktops(view));
        }
        out
    }

    /// `_reset_desktops()`: the ease is done, and the desktops themselves become the default three (one RESET plan
    /// through the panel buttons' path), or the reset is done if they already are.
    fn reset_desktops(&mut self, view: &mut dyn View) -> Vec<u8> {
        if let Some(r) = self.reset.as_mut() {
            r.easing = false;
        }
        let zeros = vec![0; view.state().names.len()];
        view.set_rolls(&zeros);
        if view.video_active() {
            return self.reset_done(view, Some("a VR video opened"));
        }
        let placements = view.state().placements.clone();
        let change = match self.plan(&placements, Action::Reset, 0) {
            Ok(change) => change,
            Err(e) => return self.reset_done(view, Some(&e)),
        };
        if change.steps().is_empty() {
            return self.reset_done(view, None);
        }
        if let (Some(removed), Some(to), Some(r)) =
            (&change.remove, &change.windows_to, self.reset.as_mut())
        {
            r.moved = Some((removed.name.clone(), to.clone()));
        }
        view.state().desktops_busy = false; // start_change sets it while the change runs
        let mut out = self.start_change(view, change);
        if !view.state().desktops_busy {
            out.extend(self.reset_done(view, Some("the desktops could not be changed now")));
        }
        out
    }

    /// `_reset_done(error)`: the live setup is saved for the next start (the default, or what a failed step left)
    /// and a toast says how it went.
    fn reset_done(&mut self, view: &mut dyn View, error: Option<&str>) -> Vec<u8> {
        let moved = self.reset.take().and_then(|r| r.moved);
        view.state().desktops_busy = false;
        let zeros = vec![0; view.state().names.len()];
        view.set_rolls(&zeros);
        view.forget_rolls();
        let flat = vec![DEFAULT_RESET_PLACEMENT; zeros.len()]; // also a desktop left underfoot by a refused removal
        let mut out = Vec::new();
        if view.state().placements != flat {
            view.state().placements = flat;
            out.extend(view.placement_frame());
        }
        view.save_setup();
        match (error, moved) {
            (Some(e), _) => view.tell(&format!("Desktop reset stopped: {e}")),
            (None, None) => view.tell(RESET_DONE),
            (None, Some((source, target))) => view.tell(&format!(
                "{RESET_DONE} {source}'s windows are now on {target}."
            )),
        }
        if view.state().menu_open {
            out.extend(view.menu_frame());
        }
        out
    }

    /// True while Ctrl+Alt+R runs (`control.reset is not None`): `save_setup` saves the default then.
    pub fn resetting(&self) -> bool {
        self.reset.is_some()
    }

    /// How soon [`Manager::poll`] has work, for the view's deadline: the reset's ease every frame, a change in
    /// flight collected soon (the Python's loop polls every pass).
    pub fn poll_in(&self) -> Option<Duration> {
        self.reset
            .as_ref()
            .filter(|r| r.easing)
            .map(|_| Duration::from_millis(16))
            .or_else(|| self.thread.as_ref().map(|_| Duration::from_millis(20)))
    }
}

/// `state.set_desktops(...)` on names the manager gave: always valid; a refusal leaves the state as it was.
fn set_desktops(
    s: &mut ViewState,
    names: &[String],
    placements: &[Placement],
    wide: &[bool],
    focus: Option<usize>,
    sizes: Option<&[Desktop]>,
) {
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    let focus = focus.and_then(|f| i64::try_from(f).ok());
    let _ = s.set_desktops(&names, placements, wide, focus, sizes);
}
