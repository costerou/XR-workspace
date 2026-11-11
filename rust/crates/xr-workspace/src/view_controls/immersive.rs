//! Immersive 3D: `immersive.find_engine`, `DepthMemory`, `ImmersiveRun(ProcessEngine(...), make_sbs(...))`
//! (tools/native_spike/immersive.py) as `managed.view_controls` builds them and closes them
//! (`immersive_run.close()`, `depth_memory.close()`), with what the run does with them in tools/native_spike/managed.py
//! (`depth_fd` to every `_launch`, `keep_renderer(switching=run_3d.switching)`, `after_reload`'s `run_3d.reassert()`)
//! and the view's 3D methods (tools/native_spike/view.py: `_immersive_wanted`, `_immersive_sync`, `_immersive_tick`,
//! `set_immersive`, `immersive_toggle`, `immersive_click`, `immersive_tail_click`, `immersive_pointer`,
//! `_immersive_status`), and `sbs.ButtonSbs` ([`sbs`]).
//!
//! The setting, the frame, the memory, the engine and the worker are `crate::supervisor::immersive`'s. This module is
//! the run's wiring and the view's side of 3D: what is wanted, what the renderer was last told, the failure latch and
//! the panel's status. The setting itself is the view's (`ViewState::immersive`), and so are the request ids and the
//! last input: each 3D method takes them as a [`Shown`], so the view (`view::ViewControl`, still a stub) only has to
//! lend them. Saving the view settings after a change is the view's too ([`Shown::changed`]).
//!
//! Differences from the Python, all deliberate:
//! - A depth memory that cannot be made leaves the run without one (the engine then starts without `--shm-fd`);
//!   the Python's `OSError` ends `view_controls` instead.
//! - A failure the renderer reported (`observe_renderer`, kind `renderer`) has no toast: the Python's
//!   `TOASTS['renderer']` is a `KeyError` out of `poll`.
//! - The worker's toasts go through [`crate::managed::real::notify`] on a thread, as `ViewControl._tell` does.

pub mod sbs;

use std::os::fd::BorrowedFd;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{Value, json};
use xr_wire::run_control::Frame;

use super::{Frames, Run};
use crate::hyprland::Client;
use crate::supervisor::immersive::{
    self as im, CLOSE_WAIT, DepthMemory, Done, Event, ImmersiveRun, MAX_DESKTOPS, NoDeviceSbs, Params,
    ProcessEngine, SET_OP, Sbs, Setting, TOGGLE_OP,
};

/// What the run's 3D worker does, so the view's side can be driven without one (`ImmersiveRun`'s methods).
pub trait Worker: Send {
    fn request(&self, desktops: &[String], done: Option<Done>, setting: Option<&Setting>);
    fn reassert(&self);
    fn take_events(&self) -> Vec<Event>;
    fn switching(&self) -> bool;
    fn observe_renderer(&self, report: Value);
    fn phase(&self) -> &'static str;
    fn close(&self, timeout: Duration);
}

impl Worker for ImmersiveRun {
    fn request(&self, desktops: &[String], done: Option<Done>, setting: Option<&Setting>) {
        ImmersiveRun::request(self, desktops, done, setting);
    }
    fn reassert(&self) {
        ImmersiveRun::reassert(self);
    }
    fn take_events(&self) -> Vec<Event> {
        ImmersiveRun::take_events(self)
    }
    fn switching(&self) -> bool {
        ImmersiveRun::switching(self)
    }
    fn observe_renderer(&self, report: Value) {
        ImmersiveRun::observe_renderer(self, report);
    }
    fn phase(&self) -> &'static str {
        ImmersiveRun::phase(self)
    }
    fn close(&self, timeout: Duration) {
        ImmersiveRun::close(self, timeout);
    }
}

/// The view's part of 3D, lent for one call: `state.immersive`, `state.names`, `video_active`, `state.zoomed`,
/// `state.typing`, `next_id` and `last_input` (at `now`, the view's clock).
pub struct Shown<'a> {
    pub setting: &'a mut Setting,
    pub names: &'a [String],
    pub video_active: bool,
    pub zoomed: bool,
    pub typing: bool,
    pub next_id: &'a mut i64,
    pub now: f64,
    pub last_input: &'a mut f64,
    /// Set when the setting changed: the view saves its settings (`self.settings.save(self.state.settings())`).
    pub changed: bool,
}

impl Shown<'_> {
    fn names(&self) -> Vec<&str> {
        self.names.iter().map(String::as_str).collect()
    }
}

/// What the renderer was last told (`_immersive_sent`): flags, stereo, hover, tail, every, look.
#[derive(Clone, PartialEq)]
struct Sent {
    flags: [bool; MAX_DESKTOPS],
    stereo: bool,
    hover: i64,
    tail: bool,
    every: bool,
    look: Params,
}

/// `IMMERSIVE_START`: what a new renderer knows (nothing on).
fn start() -> Sent {
    Sent {
        flags: [false; MAX_DESKTOPS],
        stereo: false,
        hover: -1,
        tail: false,
        every: false,
        look: im::wire_params(&Setting::default()),
    }
}

/// A toast, as `ViewControl._tell` gives one: on a thread of its own, never failing.
pub type Tell = Box<dyn Fn(&str) + Send + Sync>;

fn notify() -> Tell {
    Box::new(|text: &str| {
        let text = text.to_owned();
        std::thread::spawn(move || crate::managed::real::notify(&text));
    })
}

/// The run's `ImmersiveRun` and `DepthMemory`, and the view's 3D state.
pub struct Immersive {
    worker: Box<dyn Worker>,
    memory: Option<Arc<DepthMemory>>,
    /// `run.engine.binary is not None`.
    installed: bool,
    /// `run.sbs.button`.
    button: String,
    run_id: String,
    session: String,
    tell: Tell,
    /// `immersive_failed`: the run is held in 2D until the next press.
    failed: bool,
    /// `immersive_stereo`: the renderer is drawing 3D now.
    stereo: bool,
    hover: i64,
    on_tail: bool,
    sent: Sent,
    /// `options_immersive_done`: the options menu's completion, handed to the next request.
    done: Option<Done>,
    /// `immersive_failures`, diagnostics.
    failures: u32,
}

impl Immersive {
    /// `find_engine(renderer)`, `DepthMemory()` when there is an engine, `ImmersiveRun(ProcessEngine(...),
    /// make_sbs(client, output, {'sbs_mode': ..., 'model': ...}, tell=control._tell))`. Always built, as the Python
    /// builds it: without an engine, a press falls back to 2D with a toast.
    pub fn open(run: &Run<'_>) -> Option<Immersive> {
        let binary = im::find_engine(Some(run.renderer));
        let memory = binary.as_ref().and_then(|_| DepthMemory::new().ok()).map(Arc::new);
        let installed = binary.is_some();
        let engine = Arc::new(ProcessEngine::new(binary, memory.clone()));
        let sbs = make_sbs(run, notify());
        let button = sbs.button().to_owned();
        let worker = Box::new(ImmersiveRun::new(engine, sbs));
        let mut out = Immersive::with_worker(worker, run.run_id, run.session, notify());
        out.memory = memory;
        out.installed = installed;
        out.button = button;
        Some(out)
    }

    /// A run's 3D on `worker`, with nothing installed and the R1 button (the corpus replay's stand-in run).
    pub fn with_worker(worker: Box<dyn Worker>, run_id: &str, session: &str, tell: Tell) -> Immersive {
        Immersive {
            worker,
            memory: None,
            installed: false,
            button: "R1".into(),
            run_id: run_id.into(),
            session: session.into(),
            tell,
            failed: false,
            stereo: false,
            hover: -1,
            on_tail: false,
            sent: start(),
            done: None,
            failures: 0,
        }
    }

    /// `depth_memory.fd`, inherited by each renderer (`--depth-fd`).
    pub fn depth_fd(&self) -> Option<BorrowedFd<'_>> {
        // SAFETY: the memory is this run's and lives as long as `self` borrows it.
        self.memory
            .as_ref()
            .map(|m| unsafe { BorrowedFd::borrow_raw(m.fd()) })
    }

    /// `run_3d.switching()`: the glasses are changing between 2D and 3D (a renderer exit then is expected).
    pub fn switching(&self) -> bool {
        self.worker.switching()
    }

    /// `run_3d.reassert()`, after a config reload.
    pub fn reassert(&mut self) {
        self.worker.reassert();
    }

    /// `immersive_run.close()` and `depth_memory.close()`: the glasses back in 2D, the engine gone; bounded.
    pub fn close(&mut self) {
        self.worker.close(CLOSE_WAIT);
        self.memory = None;
    }

    /// The view's `on_message` arms: `immersive_toggle` (Ctrl+Alt+I) and `immersive` (the panel, absolute; an
    /// invalid setting is dropped).
    pub fn on_message(&mut self, message: &Frame, view: &mut Shown<'_>, out: &mut Frames) {
        if message.op == TOGGLE_OP {
            out.bytes.extend(self.toggle(view));
        } else if message.op == SET_OP {
            if let Ok(frames) = self.set(&to_serde(&message.payload), view) {
                out.bytes.extend(frames);
            }
        }
    }

    /// `_immersive_wanted`: the desktops 3D should be up for right now: none after a failure, or under a VR video.
    fn wanted(&self, view: &Shown<'_>) -> Vec<String> {
        if self.failed || view.video_active {
            return Vec::new();
        }
        im::on_names(view.setting, &view.names())
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    /// `_immersive_sync`: the worker told what is wanted, and the renderer if what it shows changed. Going flat is at
    /// once, here. The frames, empty if nothing changed. Also the start of a renderer's frames (`initial_frame`).
    pub fn sync(&mut self, view: &mut Shown<'_>) -> Vec<u8> {
        let wanted = self.wanted(view);
        if wanted.is_empty() {
            self.stereo = false;
        }
        let done = self.done.take();
        self.worker.request(&wanted, done, Some(view.setting));
        let on: Vec<bool> = view
            .names
            .iter()
            .map(|n| !self.failed && im::desktop_on(view.setting, n))
            .collect();
        let every = !on.is_empty() && on.iter().all(|&f| f);
        let mut flags = [false; MAX_DESKTOPS];
        for (slot, f) in flags.iter_mut().zip(&on) {
            *slot = *f;
        }
        // The look matters only while something is 3D: with nothing on, a saved strength sends nothing.
        let look = if on.iter().any(|&f| f) {
            im::wire_params(view.setting)
        } else {
            start().look
        };
        let sent = Sent {
            flags,
            stereo: self.stereo,
            hover: self.hover,
            tail: self.on_tail,
            every,
            look,
        };
        if sent == self.sent {
            return Vec::new();
        }
        let frame = im::renderer_frame(
            &self.run_id,
            &self.session,
            *view.next_id,
            &im::Frame {
                flags: &sent.flags,
                stereo: sent.stereo,
                hover: sent.hover,
                tail: sent.tail,
                every: sent.every,
                params: Some(sent.look.clone()),
            },
        );
        self.sent = sent;
        *view.next_id += 1;
        frame
    }

    /// `_immersive_tick` (from `poll`): what the worker did, then what is wanted now.
    pub fn tick(&mut self, view: &mut Shown<'_>) -> Vec<u8> {
        for event in self.worker.take_events() {
            match event {
                Event::Active(_) => self.stereo = !self.wanted(view).is_empty(),
                Event::Failed { kind, why } => {
                    self.failed = true;
                    self.stereo = false;
                    self.failures += 1;
                    if let Some(text) = im::toast(kind, &why) {
                        (self.tell)(&text);
                    }
                }
                Event::Leave(why) => {
                    if let Some(text) = im::toast("leave", &why) {
                        (self.tell)(&text);
                    }
                }
                Event::Off => {}
            }
        }
        self.sync(view)
    }

    /// `set_immersive`: every way of changing 3D comes here, applied live (and saved by the view). Any press is a
    /// new try after a failure, even with the setting unchanged. An invalid setting changes nothing.
    pub fn set(&mut self, setting: &Value, view: &mut Shown<'_>) -> crate::supervisor::error::Result<Vec<u8>> {
        let setting = im::validate(setting)?;
        Ok(self.apply(setting, view))
    }

    fn apply(&mut self, setting: Setting, view: &mut Shown<'_>) -> Vec<u8> {
        self.failed = false;
        if setting != *view.setting {
            *view.setting = setting;
            view.changed = true;
            if view.video_active && !im::on_names(view.setting, &view.names()).is_empty() {
                if let Some(text) = im::toast("video", "") {
                    (self.tell)(&text);
                }
            }
        }
        self.sync(view)
    }

    /// `immersive_toggle`, Ctrl+Alt+I: every desktop on or off; after a failure, the same desktops again.
    pub fn toggle(&mut self, view: &mut Shown<'_>) -> Vec<u8> {
        let names = view.names();
        let retry = self.failed && !im::on_names(view.setting, &names).is_empty();
        let setting = if retry {
            view.setting.clone()
        } else {
            im::toggled_all(view.setting, &names)
        };
        self.apply(setting, view)
    }

    fn stale(&self, desktop: i64, view: &Shown<'_>) -> bool {
        !(0..view.names.len() as i64).contains(&desktop) || view.zoomed || view.typing || view.video_active
    }

    /// `immersive_click`: a free-pointer press on `desktop`'s 3D button: that desktop on or off; after a failure, a
    /// lit-in-the-setting desktop tries again. Empty when stale (zoomed in, typing mode, a VR video, no such desktop).
    pub fn click(&mut self, desktop: i64, view: &mut Shown<'_>) -> Vec<u8> {
        if self.stale(desktop, view) {
            return Vec::new();
        }
        *view.last_input = view.now;
        let name = view.names[desktop as usize].clone();
        let retry = self.failed && im::desktop_on(view.setting, &name);
        let setting = if retry {
            view.setting.clone()
        } else {
            im::toggled_desktop(view.setting, &name)
        };
        self.apply(setting, view)
    }

    /// `immersive_tail_click`: a press on a 3D button's tail: every desktop, on or off; after a failure with all of
    /// them on in the setting, they try again. Empty when stale ([`Immersive::click`]).
    pub fn tail_click(&mut self, desktop: i64, view: &mut Shown<'_>) -> Vec<u8> {
        if self.stale(desktop, view) {
            return Vec::new();
        }
        *view.last_input = view.now;
        let names = view.names();
        let retry = self.failed && im::on_names(view.setting, &names).len() == names.len();
        let setting = if retry {
            view.setting.clone()
        } else {
            im::toggled_every(view.setting, &names)
        };
        self.apply(setting, view)
    }

    /// `immersive_pointer`: the desktop whose 3D button the dot is on (-1: none), and whether on its tail, lights.
    pub fn pointer(&mut self, desktop: i64, tail: bool, view: &mut Shown<'_>) -> Vec<u8> {
        if (desktop, tail) == (self.hover, self.on_tail) {
            return Vec::new();
        }
        self.hover = desktop;
        self.on_tail = tail;
        self.sync(view)
    }

    /// `_immersive_status`: for the panel, the setting, which desktops are 3D as shown, and whether it is drawing.
    pub fn status(&self, view: &Shown<'_>) -> Value {
        let names: Vec<&str> = if self.failed {
            Vec::new()
        } else {
            im::on_names(view.setting, &view.names())
        };
        json!({
            "setting": view.setting.to_value(), "names": names, "button": self.button,
            "switching": self.worker.switching(), "stereo": self.stereo, "failed": self.failed,
            "phase": self.worker.phase(), "installed": self.installed,
        })
    }

    /// `set_owner_readback`'s `observe_renderer(readback['immersive'])`.
    pub fn observe_renderer(&self, report: Value) {
        self.worker.observe_renderer(report);
    }

    /// `options_immersive_done`: the options menu's completion, for the next request.
    pub fn set_done(&mut self, done: Done) {
        self.done = Some(done);
    }

    /// `renderer_restarted`'s 3D part: no pending completion, and the new renderer knows nothing is on.
    pub fn renderer_restarted(&mut self) {
        self.done = None;
        self.sent = start();
    }

    /// `immersive_stereo`, `immersive_failed`, `immersive_failures`.
    pub fn stereo(&self) -> bool {
        self.stereo
    }
    pub fn failed(&self) -> bool {
        self.failed
    }
    pub fn failures(&self) -> u32 {
        self.failures
    }
}

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

/// `make_sbs`: `ButtonSbs` if this run's consent has the scope (checked against the model when the consent was
/// read), else `NoDeviceSbs`. Its compositor client is pinned to the run's compositor, as the other workers' are.
fn make_sbs(run: &Run<'_>, tell: sbs::Tell) -> Arc<dyn Sbs> {
    if !run.sbs_mode || run.output.is_empty() {
        return Arc::new(NoDeviceSbs);
    }
    let client = match run.client.identity() {
        Some(identity) => Client::pinned(run.client.instance(), run.runtime, identity.clone(), run.session),
        None => Client::new(run.client.instance(), run.runtime),
    };
    let Ok(client) = client else {
        return Arc::new(NoDeviceSbs);
    };
    match sbs::ButtonSbs::new(Box::new(client), run.output, tell, run.model) {
        Some(button) => Arc::new(button),
        None => Arc::new(NoDeviceSbs),
    }
}

