//! The glasses' view controls of a managed capture run: `managed.view_controls` in `tools/native_spike/managed.py`,
//! and the run-loop calls the Python makes into them (`supervisor.Supervisor.run`, `_launch`, `managed.keep_renderer`).
//!
//! A scaffold. Every feature has its own submodule with the Python's entry points as stubs, and this module calls
//! them at the Python's points, in the Python's order; until a feature is ported its `open` gives `None` and the run
//! behaves as before (a plain run: rust/README.md). A feature's port fills in its own submodule only.
//! rust/crates/xr-workspace/VIEW_CONTROLS.md is the hand-off: per module, the Python to port, the renderer ops, the
//! parity tests and the shared edit it owns.
//!
//! The order of [`ViewControls::enter`] is `view_controls`'s body; the order of [`ViewControls::exit`] is its
//! `finally`. Outside them, [`managed::real`](crate::managed::real) calls [`setup`] (a refused saved setup),
//! [`desktops::Manager::open`] (before the guard), [`window_memory::Tracker`] (around adoption) and
//! [`overview_pointer::cursor_reader`] (the broker's cursor read) where `_run_owned` does.

pub mod binds;
pub mod desktops;
pub mod immersive;
pub mod keybinds;
pub mod overview_pointer;
pub mod privacy;
pub mod send;
pub mod setup;
pub mod socket;
pub mod video;
pub mod view;
pub mod window_memory;

use std::os::fd::{OwnedFd, RawFd};
use std::path::Path;
use std::time::{Duration, Instant};

use xr_wire::run_control::Frame;

use crate::hyprland::Client;
use crate::supervisor::{GuardChange, LoopHook, WakeFd};

/// What `view_controls` is called with, borrowed from the run for one call (open or close). Loop-time work that
/// needs the compositor opens its own client (`Client::pinned`, `Client::lane`), as the Python's worker threads do.
pub struct Run<'a> {
    pub client: &'a mut Client,
    pub runtime: &'a Path,
    pub run_id: &'a str,
    pub session: &'a str,
    /// The run's desktops (`capture`): the XR outputs' names.
    pub capture: &'a [String],
    /// Their outputs as the broker created them (`manager.specs`); empty before the create.
    pub specs: &'a [xr_wire::plugin::OutputSpec],
    /// The glasses' output (`output`) and the physical recovery output (`fallback`).
    pub output: &'a str,
    pub fallback: &'a str,
    /// The glasses profile's model (`store.model`): `luma-ultra` or `pro2`.
    pub model: &'a str,
    /// The renderer binary (`renderer`): where `immersive.find_engine` looks for the depth engine.
    pub renderer: &'a Path,
    /// The consent's 3D scope (`sbs_mode`).
    pub sbs_mode: bool,
}

/// Every view control of one run. A field is `None` while its feature is not ported (or not wanted, as in Python).
pub struct ViewControls {
    /// The loop's wake when no overview pointer has one of its own (never signalled then).
    wake: WakeFd,
    /// Descriptors of the frames handed to the loop, for its next [`LoopHook::take_fds`].
    fds: Vec<OwnedFd>,
    pub socket: Option<socket::Server>,
    pub binds: Option<binds::Binds>,
    pub view: Option<view::ViewControl>,
    pub pointer: Option<overview_pointer::Pointer>,
    pub send: Option<send::Sender>,
    pub privacy: Option<privacy::Privacy>,
    pub desktops: Option<desktops::Manager>,
    pub immersive: Option<immersive::Immersive>,
    pub windows: Option<window_memory::Tracker>,
    pub keys: Option<keybinds::Keys>,
    pub video: Option<video::Video>,
}

/// The features [`view::ViewControl::on_message`] routes to: every field of [`ViewControls`] but the view itself.
pub struct Features<'a> {
    pub pointer: Option<&'a mut overview_pointer::Pointer>,
    pub send: Option<&'a mut send::Sender>,
    pub privacy: Option<&'a mut privacy::Privacy>,
    pub desktops: Option<&'a mut desktops::Manager>,
    pub immersive: Option<&'a mut immersive::Immersive>,
    pub windows: Option<&'a mut window_memory::Tracker>,
    pub keys: Option<&'a mut keybinds::Keys>,
    pub binds: Option<&'a mut binds::Binds>,
}

/// Frames for the renderer and the descriptors that travel with them (`video.FdFrame`).
#[derive(Default)]
pub struct Frames {
    pub bytes: Vec<u8>,
    pub fds: Vec<OwnedFd>,
}

impl ViewControls {
    /// `view_controls`'s body, before its `yield`. `desktops` and `windows` are the run's (`_run_owned` made them);
    /// they are closed in [`ViewControls::exit`].
    pub fn enter(
        run: &mut Run<'_>,
        desktops: Option<desktops::Manager>,
        windows: Option<window_memory::Tracker>,
        cursor_shape: Option<overview_pointer::CursorShape>,
    ) -> std::io::Result<ViewControls> {
        let mut controls = ViewControls {
            wake: WakeFd::new()?,
            fds: Vec::new(),
            socket: None,
            binds: None,
            view: None,
            pointer: None,
            send: None,
            privacy: None,
            desktops: None,
            immersive: None,
            windows: None,
            keys: None,
            video: None,
        };
        // `store.load()`, then the private control socket (`PrivateServer`).
        controls.socket = socket::Server::open(run)?;
        // bind_args, the session mode, after_reload / menu_binds / typing_binds.
        controls.binds = binds::Binds::open(run, controls.socket.as_ref());
        // The placements the last session left (`_restored`, `_initial_placements`), then `ViewControl`.
        controls.view = view::ViewControl::open(run);
        // The overview free pointer and its sampler (`UnifiedPointer`, `UnifiedSampler`).
        controls.pointer =
            overview_pointer::Pointer::open(run, controls.view.as_mut(), cursor_shape);
        // `WindowSender` (Ctrl+Alt+G, 1-4, B).
        controls.send = send::Sender::open(run);
        // `PrivacyDisplay` (Ctrl+Alt+D).
        controls.privacy = privacy::Privacy::open(run);
        // The pills' `DesktopManager`, given to the view.
        controls.desktops = desktops.and_then(|m| m.attach(run, controls.view.as_mut()));
        // `immersive.find_engine`, `DepthMemory`, `ImmersiveRun`.
        controls.immersive = immersive::Immersive::open(run);
        // The window memory's tracker, given to the view.
        controls.windows = windows.and_then(|w| w.attach(controls.view.as_mut()));
        // `windows.context = control.window_context`.
        if let (Some(windows), Some(view)) = (controls.windows.as_mut(), controls.view.as_ref()) {
            windows.set_context(view.window_context());
        }
        // `client.event_stream()` (config reloads, window changes, `xrcursor`).
        if let Some(view) = controls.view.as_mut() {
            view.open_events(run);
        }
        // The key bind panel (`keybinds.BindControl`), then `client.install_view_binds` with its keys,
        // `keys.installed()`, `keys.startup_toast()`.
        let bind_args = controls.binds.as_ref().map(binds::Binds::bind_args);
        controls.keys = keybinds::Keys::open(run, bind_args);
        if let Some(binds) = controls.binds.as_mut() {
            binds.install(run, controls.keys.as_ref());
        }
        if let Some(keys) = controls.keys.as_mut() {
            keys.installed();
            keys.startup_toast();
        }
        // `VideoControl` around the view, then `RuntimeText` and the options binds.
        controls.video = video::Video::open(run);
        if let Some(view) = controls.view.as_mut() {
            view.open_options(run);
        }
        Ok(controls)
    }

    /// `view_controls`'s `finally`, in its order: nothing of the controls may act on the compositor after this, and
    /// recovery runs next. Never fails, as the Python's never stops recovery.
    pub fn exit(mut self, run: &mut Run<'_>) {
        if let Some(view) = self.view.as_mut() {
            view.close_options(run, self.pointer.as_mut());
        }
        if let Some(desktops) = self.desktops.as_mut() {
            desktops.close();
        }
        if let Some(video) = self.video.as_mut() {
            video.close(run);
        }
        if let Some(send) = self.send.as_mut() {
            send.close();
        }
        if let Some(privacy) = self.privacy.as_mut() {
            privacy.close();
        }
        if let Some(immersive) = self.immersive.as_mut() {
            immersive.close();
        }
        if let Some(view) = self.view.as_mut() {
            view.flush();
            view.save_setup();
        }
        if let Some(windows) = self.windows.as_mut() {
            windows.close(run);
        }
        if let Some(binds) = self.binds.as_mut() {
            binds.stop_menu_binds();
        }
        if let Some(keys) = self.keys.as_mut() {
            keys.stop();
        }
        if let Some(pointer) = self.pointer.as_mut() {
            pointer.stop_sampler();
        }
        if let Some(binds) = self.binds.as_mut() {
            binds.remove(run);
        }
        if let Some(view) = self.view.as_mut() {
            view.close_events();
        }
        if let Some(pointer) = self.pointer.as_mut() {
            pointer.close();
        }
        if let Some(socket) = self.socket.as_mut() {
            socket.close();
        }
    }

    fn features(&mut self) -> (Option<&mut view::ViewControl>, Features<'_>) {
        (
            self.view.as_mut(),
            Features {
                pointer: self.pointer.as_mut(),
                send: self.send.as_mut(),
                privacy: self.privacy.as_mut(),
                desktops: self.desktops.as_mut(),
                immersive: self.immersive.as_mut(),
                windows: self.windows.as_mut(),
                keys: self.keys.as_mut(),
                binds: self.binds.as_mut(),
            },
        )
    }

    /// One user op from the control socket (`self.control.on_message`): VR video first (`VideoControl` wraps the
    /// view), then the view, which routes the other features' ops.
    pub fn on_message(&mut self, message: &Frame, out: &mut Frames) {
        if let Some(video) = self.video.as_mut()
            && video.on_message(message, self.view.as_mut(), out)
        {
            return;
        }
        let (view, mut features) = self.features();
        if let Some(view) = view {
            view.on_message(message, &mut features, out);
        }
    }

    /// `_launch`: what goes to the renderer after the start frame (`control.initial_frame()`).
    pub fn initial_frame(&mut self) -> Vec<u8> {
        self.view
            .as_mut()
            .map(|v| v.initial_frame())
            .unwrap_or_default()
    }

    /// `keep_renderer`: true while the glasses change between 2D and 3D (`ImmersiveRun.switching`).
    pub fn switching(&self) -> bool {
        self.immersive.as_ref().is_some_and(|i| i.switching())
    }

    /// `keep_renderer`: a renderer started again after a hold (`control.renderer_restarted`).
    pub fn renderer_restarted(&mut self) {
        if let Some(view) = self.view.as_mut() {
            view.renderer_restarted();
        }
    }

    /// `supervisor.serve_without_renderer`: a hold's wait. The panel still gets its status; a view op is dropped.
    pub fn serve_without_renderer(&mut self, seconds: Duration) {
        let view = self.view.as_ref();
        match self.socket.as_mut() {
            Some(socket) => socket.serve_without_renderer(seconds, || status_reply(view, true)),
            None => std::thread::sleep(seconds),
        }
    }

    /// The run's live desktops (`manager.specs`): a later renderer captures them, and its guard holds them.
    pub fn live_specs(&self) -> Option<Vec<xr_wire::plugin::OutputSpec>> {
        self.desktops.as_ref().and_then(|d| d.specs())
    }

    /// The depth engine's shared memory (`depth_memory.fd`), inherited by each renderer.
    pub fn depth_fd(&self) -> Option<std::os::fd::BorrowedFd<'_>> {
        self.immersive.as_ref().and_then(|i| i.depth_fd())
    }
}

/// A `status` answer's body: the view's (`control.status_reply(status)`); `held`: during a hold, which has no
/// measured status (`serve_without_renderer`'s `{}`). Without a view, the four header keys alone.
fn status_reply(view: Option<&view::ViewControl>, held: bool) -> socket::Body {
    view.map(|v| v.status_reply(held)).unwrap_or_default()
}

/// The order of [`LoopHook::sources`]: the control socket, then the event stream (the Python's `selector.register`).
const SOURCE_SOCKET: usize = 0;

impl ViewControls {
    fn source_fds(&self) -> [Option<RawFd>; 2] {
        [
            self.socket.as_ref().map(socket::Server::fd),
            self.view.as_ref().and_then(view::ViewControl::events_fd),
        ]
    }
}

impl LoopHook for ViewControls {
    fn wake_fd(&self) -> &WakeFd {
        self.pointer
            .as_ref()
            .and_then(overview_pointer::Pointer::wake_fd)
            .unwrap_or(&self.wake)
    }

    fn deadline(&mut self) -> Option<Instant> {
        let (view, mut features) = self.features();
        view.and_then(|v| v.deadline(&mut features))
    }

    /// `control.poll(now)`: the free pointer, the debounced panel save, the overlay's idle close.
    fn poll(&mut self, now: Instant, out: &mut Vec<u8>) {
        let mut frames = Frames::default();
        let (view, mut features) = self.features();
        if let Some(view) = view {
            view.poll(now, &mut features, &mut frames);
        }
        self.queue(frames, out);
    }

    fn sources(&self) -> Vec<RawFd> {
        self.source_fds().into_iter().flatten().collect()
    }

    /// `_control_ready` (one bounded op, a `status` answered on the socket) or `_events_ready`.
    fn ready(&mut self, index: usize, _now: Instant, out: &mut Vec<u8>) -> bool {
        let socket_first = self.socket.is_some();
        let mut frames = Frames::default();
        let keep = if socket_first && index == SOURCE_SOCKET {
            let view = self.view.as_ref();
            let request = self
                .socket
                .as_mut()
                .and_then(|s| s.control_request(|| status_reply(view, false)));
            if let Some(message) = request {
                self.on_message(&message, &mut frames);
            }
            true
        } else {
            let (view, mut features) = self.features();
            view.is_none_or(|v| v.events_ready(&mut features, &mut frames))
        };
        self.queue(frames, out);
        keep
    }

    /// `Supervisor.status_bytes`'s view side: the measured status for `status` replies and the view's datagrams.
    fn status(&mut self, datagram: &[u8], _now: Instant) {
        let (view, mut features) = self.features();
        if let Some(view) = view {
            view.status(datagram, &mut features);
        }
        if let Some(video) = self.video.as_mut() {
            video.status(datagram);
        }
    }

    fn take_fds(&mut self) -> Vec<OwnedFd> {
        std::mem::take(&mut self.fds)
    }

    /// A desktop change's `pause_guard` / `replace_guard(manager.guard())`.
    fn guard_change(&mut self) -> Option<GuardChange> {
        self.desktops
            .as_mut()
            .and_then(desktops::Manager::guard_change)
    }
}

impl ViewControls {
    /// Frames for the loop: the bytes now, the descriptors on the next [`LoopHook::take_fds`].
    fn queue(&mut self, frames: Frames, out: &mut Vec<u8>) {
        out.extend_from_slice(&frames.bytes);
        self.fds.extend(frames.fds);
    }
}
