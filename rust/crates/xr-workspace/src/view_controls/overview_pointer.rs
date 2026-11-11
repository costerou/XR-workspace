//! The overview free pointer of a run: what `managed.view_controls` wires (tools/native_spike/managed.py:
//! `UnifiedPointer(control)`, the saved `rolls`, `control.wake = pointer.wake_reader`, `UnifiedSampler(client, capture,
//! pointer, bind_args, cursor_shape=...).start()`, `control.desktop_listeners.append(sampler.set_names)`, and in the
//! `finally` `sampler.stop()` before the binds go and `pointer.close()`), the broker's cursor-shape read
//! (`ownership.OwnershipBrokerClient.cursor_reader`, called in `_run_owned`), and the view's calls into it
//! (`ViewControl.on_message`: `resize_cancel`, `resize_press` / `resize_release`, the Super ops; `on_events`:
//! `push_shape`; `set_overview_geometry`, `set_roll_geometry`, `set_maximize_geometry`; `poll`).
//!
//! The pointer and the sampler are ported (`crate::supervisor::{overview_pointer, unified_pointer, pointer_host,
//! sampler}`; `pointer_host` already implements the loop's `LoopHook`). What is left is the run's: building them on
//! the run's view control, the sampler thread with its own compositor client, and the order of the teardown.
//!
//! Not ported: [`Pointer::open`] and [`cursor_reader`] give `None`.

use super::view::ViewControl;
use super::{Frames, Run};
use crate::supervisor::WakeFd;

/// The broker's read of the cursor an app asked for (`cursor_reader()`), for the dot's look over text.
pub struct CursorShape {
    _private: (),
}

/// `ownership.OwnershipBrokerClient.cursor_reader()`: `None` with a broker that has no such read.
pub fn cursor_reader(_broker: &crate::ownership::Broker) -> Option<CursorShape> {
    None
}

/// The run's `UnifiedPointer` and its `UnifiedSampler`.
pub struct Pointer {
    _private: (),
}

impl Pointer {
    /// `UnifiedPointer(control)`, the saved rolls, the sampler started.
    pub fn open(
        _run: &Run<'_>,
        _view: Option<&mut ViewControl>,
        _cursor_shape: Option<CursorShape>,
    ) -> Option<Pointer> {
        None
    }

    /// `pointer.wake_reader`: what the sampler signals.
    pub fn wake_fd(&self) -> Option<&WakeFd> {
        None
    }

    /// `overview_pointer.super_down()` (`resize_cancel`: Super went down).
    pub fn super_down(&mut self, _out: &mut Frames) {}

    /// `overview_pointer.on_op(op)` (`resize_press`, `resize_release`).
    pub fn on_op(&mut self, _op: &str, _out: &mut Frames) {}

    /// `overview_pointer.on_super(op)` (the Super drags).
    pub fn on_super(&mut self, _op: &str, _out: &mut Frames) {}

    /// `sampler.stop()`: its binds removed and the cursor shown; none after this.
    pub fn stop_sampler(&mut self) {}

    /// `pointer.close()`.
    pub fn close(&mut self) {}
}
