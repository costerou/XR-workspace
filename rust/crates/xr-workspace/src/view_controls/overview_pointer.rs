//! The overview free pointer of a run: what `managed.view_controls` wires (tools/native_spike/managed.py:
//! `UnifiedPointer(control)`, the saved `rolls`, `control.wake = pointer.wake_reader`, `UnifiedSampler(client, capture,
//! pointer, bind_args, cursor_shape=...).start()`, `control.desktop_listeners.append(sampler.set_names)`, and in the
//! `finally` `sampler.stop()` before the binds go and `pointer.close()`), the broker's cursor-shape read
//! (`ownership.OwnershipBrokerClient.cursor_reader`, called in `_run_owned`), and the view's calls into it
//! (`ViewControl.on_message`: `resize_cancel`, `resize_press` / `resize_release`, the Super ops; `on_events`:
//! `push_shape`; `set_overview_geometry`, `set_roll_geometry`, `set_maximize_geometry`; `poll`).
//!
//! The pointer and the sampler are `crate::supervisor::{overview_pointer, unified_pointer, pointer_host, sampler}`.
//! Here is the run's part: a [`UnifiedPointer`] shared with the sampler thread ([`SharedPointer`]), the loop's side of
//! it ([`PointerHook`]: the wake fd, the timers, the poll), the sampler on a compositor client of its own (pinned to the
//! run's compositor, as the run's other workers are; the Python's thread shares the run's client, and the requests on
//! the wire are the same), the broker's cursor read on that client, and the window sender's drags
//! (`control.sender`: [`Pointer::attach_sender`]).
//!
//! The renderer ops are the pointer's own frames: `overview_pointer`, `roll`, `edge_pan`, `restore_band`,
//! `resize_handle` and `move_handle`, all from [`Pointer::poll`], [`Pointer::on_op`], [`Pointer::on_super`] and
//! [`Pointer::super_down`].
//!
//! # The control
//!
//! The Python pointer holds the view control and reads `control.state` live. The view is not ported yet, so the pointer
//! holds its own [`Shell`]: the run's [`ViewState`] (the capture's desktops, the defaults: in the overview, the first
//! selected) and the run's ids, and every call the pointer makes on the view sends nothing but a selection. The view's
//! port either makes the shell its state (through [`Pointer::lock`]) or puts `view::ViewControl` in its place. The
//! window sender's calls (`has_sender`, `sender_*`) are the run's sender's worker already.
//!
//! The sampler thread blocks while nothing it watches can change (sampler.rs, "The loop is event-driven"); every call
//! here that may change what the pointer reports to it wakes it.

use std::sync::MutexGuard;
use std::time::Instant;

use super::send::WindowSender;
use super::{Frames, Run};
use crate::hyprland::Client;
use crate::ownership::Broker;
use crate::pyjson::Json;
use crate::supervisor::LoopHook;
use crate::supervisor::WakeFd;
use crate::supervisor::edge_pan::PanFrame;
use crate::supervisor::orbit::Geometry;
use crate::supervisor::overview_pointer::{Control, Found, OverviewPointer};
use crate::supervisor::pointer_host::{self, PointerHook, SharedPointer};
use crate::supervisor::sampler::live::Live;
use crate::supervisor::sampler::run::{self, Handle};
use crate::supervisor::sampler::{CallError, Clock, Monotonic, SAMPLE_S, Sampler, ShapeReader, Variant};
use crate::supervisor::unified_pointer::UnifiedPointer;
use crate::supervisor::view::{ViewOptions, ViewState};
use crate::geometry::Placement;

/// The broker's read of the cursor an app asked for (`cursor_reader()`), for the dot's look over text: a copy of the
/// run's broker client with its own request ids, read on the sampler thread through a compositor client of its own.
pub struct CursorShape {
    broker: Broker,
}

/// `ownership.OwnershipBrokerClient.cursor_reader()`: `None` without a handshake (the Rust run's broker speaks the
/// legacy schema, the only one with the read).
pub fn cursor_reader(broker: &Broker) -> Option<CursorShape> {
    broker.epoch.as_ref()?;
    let mut broker = broker.clone();
    broker.request_id = 0;
    Some(CursorShape { broker })
}

/// Where the sampler's own compositor client goes: the run's compositor, pinned to its process when the run's client
/// has met it.
struct Origin {
    instance: String,
    runtime: std::path::PathBuf,
    identity: Option<crate::hyprland::Identity>,
    session: String,
}

impl Origin {
    fn of(run: &Run<'_>) -> Origin {
        Origin {
            instance: run.client.instance().to_owned(),
            runtime: run.runtime.to_path_buf(),
            identity: run.client.identity().cloned(),
            session: run.session.to_owned(),
        }
    }

    fn client(&self) -> crate::hyprland::Result<Client> {
        match &self.identity {
            Some(identity) => Client::pinned(&self.instance, &self.runtime, identity.clone(), &self.session),
            None => Client::new(&self.instance, &self.runtime),
        }
    }
}

impl CursorShape {
    /// The sampler's `cursor_shape()`: a client opened on the first read, and again after a failed one.
    fn reader(mut self, origin: Origin) -> ShapeReader {
        let mut client: Option<Client> = None;
        Box::new(move || {
            if client.is_none() {
                client = Some(origin.client().map_err(|_| CallError::Failed)?);
            }
            let read = self.broker.cursor_shape(client.as_mut().expect("opened above"));
            if read.is_err() {
                client = None;
            }
            read.map_err(|_| CallError::Failed)
        })
    }
}

/// What the pointer drives in place of `view.ViewControl` (see the module doc): the run's view state and ids, and the
/// run's window sender.
pub struct Shell {
    state: ViewState,
    run_id: String,
    session: String,
    next_id: u64,
    clock: Monotonic,
    sender: Option<WindowSender>,
}

impl Shell {
    /// `view.ViewState(capture)` with the defaults; `None` for desktops a view would refuse.
    pub fn new(capture: &[String], run_id: &str, session: &str) -> Option<Shell> {
        let names: Vec<&str> = capture.iter().map(String::as_str).collect();
        Some(Shell {
            state: ViewState::new(&names, ViewOptions::default()).ok()?,
            run_id: run_id.to_owned(),
            session: session.to_owned(),
            next_id: 1,
            clock: Monotonic,
            sender: None,
        })
    }
}

/// A window lookup's answer as the pointer reads it (`send.window_at`'s dict, or null).
fn found_json(found: Found) -> super::send::Found {
    Box::new(move |v: serde_json::Value| found(serde_json::from_value(v).unwrap_or(Json::Null)))
}

impl Control for Shell {
    fn state(&self) -> &ViewState {
        &self.state
    }
    fn state_mut(&mut self) -> &mut ViewState {
        &mut self.state
    }
    fn reloads(&self) -> i64 {
        0
    }
    fn run_id(&self) -> &str {
        &self.run_id
    }
    fn session(&self) -> &str {
        &self.session
    }
    fn next_id(&self) -> u64 {
        self.next_id
    }
    fn set_next_id(&mut self, id: u64) {
        self.next_id = id;
    }
    fn set_save_due(&mut self, _: f64) {}
    fn set_last_input(&mut self, _: f64) {}
    fn clock(&mut self) -> f64 {
        self.clock.now()
    }
    fn placement_frame(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn menu_frame(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn frame(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn follow_focus(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn flush(&mut self) {}
    fn select_desktop(&mut self, i: usize, _: Option<bool>) -> Vec<u8> {
        if i < self.state.names.len() {
            self.state.selected = i;
        }
        Vec::new()
    }
    fn switch_desktop(&mut self, i: usize, panned: bool) -> Vec<u8> {
        self.select_desktop(i, Some(panned))
    }
    fn pill_click(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn pill_release(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn pill_close(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn pill_slide(&mut self, _: usize, _: &str, _: f64, _: Option<&Placement>) -> Vec<u8> {
        Vec::new()
    }
    fn panel_keys_on(&mut self) {}
    fn desktop_press(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn immersive_click(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn immersive_tail_click(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn desktop_action(&mut self, _: usize, _: &str) -> Vec<u8> {
        Vec::new()
    }
    fn help_turn(&mut self, _: i64) -> Vec<u8> {
        Vec::new()
    }
    fn maximize_monitor(&mut self, _: &str) -> Vec<u8> {
        Vec::new()
    }
    fn scale_close(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn resolution_close(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn background_close(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn scale_box(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn scale_pick(&mut self, _: usize, _: i64) -> Vec<u8> {
        Vec::new()
    }
    fn resolution_box(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn resolution_pick(&mut self, _: usize, _: (i64, i64)) -> Vec<u8> {
        Vec::new()
    }
    fn background_box(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn background_pick(&mut self, _: usize, _: &str) -> Vec<u8> {
        Vec::new()
    }
    fn background_switch(&mut self, _: usize) -> Vec<u8> {
        Vec::new()
    }
    fn pan_frame(&mut self, _: Option<&PanFrame>) -> Vec<u8> {
        Vec::new()
    }

    fn has_sender(&self) -> bool {
        self.sender.is_some()
    }
    fn sender_lookup(&mut self, monitor: &str, point: (i64, i64), found: Found) {
        if let Some(sender) = self.sender.as_mut() {
            sender.lookup(monitor, point, found_json(found));
        }
    }
    fn sender_under_cursor(&mut self, found: Found) {
        if let Some(sender) = self.sender.as_mut() {
            sender.under_cursor(found_json(found));
        }
    }
    fn sender_land(&mut self, address: &str, monitor: &str) {
        if let Some(sender) = self.sender.as_mut() {
            sender.land(address, monitor);
        }
    }
    fn sender_drop(&mut self, window: &Json, from: &str, point: Option<(i64, i64)>, to: &str) {
        if let Some(sender) = self.sender.as_mut() {
            let window = (*window != Json::Null).then(|| window.to_value());
            sender.drop(window, from, point, to);
        }
    }
}

type RunSampler = Handle<SharedPointer<Shell>, Live, Monotonic>;

/// The run's `UnifiedPointer` and its `UnifiedSampler`.
pub struct Pointer {
    shared: SharedPointer<Shell>,
    hook: PointerHook<Shell>,
    sampler: Option<RunSampler>,
}

impl Pointer {
    /// `UnifiedPointer(control)`, then the sampler started: `UnifiedSampler(client, capture, pointer, bind_args,
    /// cursor_shape=...)` on a client of its own. `bind_args`: the bind helper's command line (`binds::Binds::bind_args`);
    /// without it there is no sampler (the binds are not ported), as the Python has none with a client that cannot
    /// lock the pointer. `None`: desktops the view refuses, or no wake fd.
    pub fn open(
        run: &Run<'_>,
        bind_args: Option<Vec<String>>,
        cursor_shape: Option<CursorShape>,
    ) -> Option<Pointer> {
        let shell = Shell::new(run.capture, run.run_id, run.session)?;
        let pointer = UnifiedPointer::new(shell, pointer_host::monotonic()).ok()?;
        let mut p = Pointer::with(SharedPointer::unified(pointer));
        if let Some(bind_args) = bind_args {
            let origin = Origin::of(run);
            match origin.client() {
                Ok(client) => {
                    let shape = cursor_shape.map(|c| c.reader(Origin::of(run)));
                    p.start_sampler(Live::new(client), run.capture.to_vec(), bind_args, shape);
                }
                Err(e) => eprintln!("xr-workspace: overview pointer: no sampler: {e}"),
            }
        }
        Some(p)
    }

    /// A pointer with no sampler yet, for the tests and [`Pointer::open`].
    pub fn with(shared: SharedPointer<Shell>) -> Pointer {
        let hook = shared.hook();
        Pointer { shared, hook, sampler: None }
    }

    /// `UnifiedSampler(client, names, pointer, bind_args, cursor_shape=...).start()`.
    pub fn start_sampler(
        &mut self,
        client: Live,
        names: Vec<String>,
        bind_args: Vec<String>,
        cursor_shape: Option<ShapeReader>,
    ) {
        let sampler = Sampler::new(
            Variant::Unified,
            client,
            names,
            self.shared.clone(),
            bind_args,
            SAMPLE_S,
            cursor_shape,
            Monotonic,
        );
        self.sampler = Some(run::start(sampler));
    }

    /// The pointer, locked (the view's reads and writes of its state, the tests).
    pub fn lock(&self) -> MutexGuard<'_, OverviewPointer<Shell>> {
        self.shared.lock()
    }

    /// `control.sender = WindowSender(...)`: the overview's window drags reach the run's sender (a clone of it).
    pub fn attach_sender(&mut self, sender: Option<&WindowSender>) {
        self.lock().control.sender = sender.cloned();
    }

    /// `pointer.rolls[:len(rolls)] = rolls`: the saved levelling rolls.
    pub fn set_rolls(&mut self, rolls: &[i64]) {
        let mut p = self.lock();
        let n = rolls.len().min(p.rolls.len());
        p.rolls[..n].copy_from_slice(&rolls[..n]);
    }

    /// The sampler's idle wait ends: what the pointer reports to it may have changed.
    fn wake(&self) {
        if let Some(sampler) = &self.sampler {
            sampler.wake();
        }
    }

    /// `pointer.wake_reader`: what the sampler signals.
    pub fn wake_fd(&self) -> Option<&WakeFd> {
        Some(self.hook.wake_fd())
    }

    /// When the pointer's next poll is due though nothing signals it (its timers: `pointer_host::due`).
    pub fn deadline(&mut self) -> Option<Instant> {
        self.hook.deadline()
    }

    /// `overview_pointer.poll(now)`: the dot, the handles, the rolls, the pan, the band.
    pub fn poll(&mut self, now: Instant, out: &mut Frames) {
        self.hook.poll(now, &mut out.bytes);
        self.wake();
    }

    /// `set_overview_geometry`'s `overview_pointer.set_geometry(geometry)`.
    pub fn set_geometry(&mut self, geometry: Option<Geometry>) {
        self.lock().set_geometry(geometry);
        self.wake();
    }

    /// `set_roll_geometry`'s `overview_pointer.set_roll_geometry(tilts)`.
    pub fn set_roll_geometry(&mut self, tilts: Vec<Option<i64>>) {
        self.lock().set_roll_geometry(tilts);
    }

    /// `overview_pointer.set_maximize_geometry(geometry)` (`Json::Null`: none, as the `finally` clears it).
    pub fn set_maximize_geometry(&mut self, geometry: &Json) {
        let _ = self.lock().set_maximize_geometry(geometry);
        self.wake();
    }

    /// `on_events`'s `overview_pointer.push_shape(name)` (the `xrcursor` event).
    pub fn push_shape(&mut self, name: Option<&str>) {
        self.lock().push_shape(name);
        self.wake();
    }

    /// `sampler.set_names`: a pill's Add / Remove; the sampler reads their monitors again.
    pub fn set_names(&mut self, names: &[String]) {
        if let Some(sampler) = &self.sampler {
            sampler.set_names(names.to_vec());
        }
    }

    /// `overview_pointer.super_down()` (`resize_cancel`: Super went down).
    pub fn super_down(&mut self, out: &mut Frames) {
        if let Some(frames) = self.lock().super_down() {
            out.bytes.extend(frames);
        }
        self.wake();
    }

    /// `overview_pointer.on_op(op)` (`resize_press`, `resize_release`).
    pub fn on_op(&mut self, op: &str, out: &mut Frames) {
        if let Ok(Some(frames)) = self.lock().on_op(op) {
            out.bytes.extend(frames);
        }
        self.wake();
    }

    /// `overview_pointer.on_super(op)` (the Super drags).
    pub fn on_super(&mut self, op: &str, out: &mut Frames) {
        if let Ok(Some(frames)) = self.lock().on_super(op) {
            out.bytes.extend(frames);
        }
        self.wake();
    }

    /// `sampler.stop()`: the thread joined, its binds removed and the cursor shown; none after this.
    pub fn stop_sampler(&mut self) {
        if let Some(sampler) = self.sampler.take() {
            sampler.stop();
        }
    }

    /// `pointer.close()`: no window drag reaches the sender after this.
    pub fn close(&mut self) {
        self.stop_sampler();
        let mut p = self.lock();
        p.control.sender = None;
        p.close();
    }
}
