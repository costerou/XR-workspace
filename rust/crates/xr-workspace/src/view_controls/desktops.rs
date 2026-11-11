//! The pills' desktop changes (Ultrawide, Add, Remove, Scale, Resolution) and Ctrl+Alt+R's reset:
//! `desktops.DesktopManager` (tools/native_spike/desktops.py: the broker steps off the loop, `specs`, `guard()`,
//! `wide()`, `desktops()`, `close()`), made in `managed._run_owned` (before the guard's first check, a capture run with
//! the ownership broker) and wired in `managed.view_controls` (`control.desktops`, `desktops.store = placements`,
//! `desktops.move_windows = _window_mover(client)`, `control.state.wide`, `control.state.sizes`,
//! `desktops.leaving = windows.leaving`); and the view's side (tools/native_spike/view.py: `desktop_action`,
//! `_start_change`, `_desktops_frame`, `_desktops_changed`, `_desktops_done`, `layout_reset`, `_reset_tick`,
//! `_reset_desktops`, `_reset_done`). A later renderer captures `manager.specs` under `manager.guard()`.
//!
//! The plan is ported (`crate::desktops`, `crate::fit_windows`); the broker's create and remove are
//! `crate::ownership`'s.
//!
//! Not ported: [`Manager::open`] gives `None`; the run keeps the desktops it started with.

use xr_wire::plugin::OutputSpec;

use super::view::ViewControl;
use super::{Frames, Run};
use crate::supervisor::GuardChange;

/// `desktops.DesktopManager`.
pub struct Manager {
    _private: (),
}

impl Manager {
    /// `DesktopManager(broker, journal, specs, guard_factory=make_guard, windows=client)`.
    pub fn open(_specs: &[OutputSpec]) -> Option<Manager> {
        None
    }

    /// The `view_controls` wiring: the view's desktops and sizes, the window mover, the profile store.
    pub fn attach(self, _run: &Run<'_>, _view: Option<&mut ViewControl>) -> Option<Manager> {
        Some(self)
    }

    /// `manager.specs`: the live desktops, which a pill may have changed since the start.
    pub fn specs(&self) -> Option<Vec<OutputSpec>> {
        None
    }

    /// The guard change a change in flight needs: `pause_guard()` as it starts, `replace_guard(manager.guard())` once
    /// it is done (a `ManagedGuard` of the live desktops: `RealHost`'s guard command for [`Manager::specs`]).
    pub fn guard_change(&mut self) -> Option<GuardChange> {
        None
    }

    /// `ViewControl.layout_reset()` (Ctrl+Alt+R): armed, then confirmed.
    pub fn layout_reset(&mut self, _out: &mut Frames) {}

    /// `desktops.close()`: no change starts now; one in flight lands before recovery.
    pub fn close(&mut self) {}
}
