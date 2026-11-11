//! Immersive 3D: `immersive.find_engine`, `DepthMemory`, `ImmersiveRun(ProcessEngine(...), make_sbs(...))`
//! (tools/native_spike/immersive.py) as `managed.view_controls` builds them and closes them
//! (`immersive_run.close()`, `depth_memory.close()`), with what the run does with them in tools/native_spike/managed.py
//! (`depth_fd` to every `_launch`, `keep_renderer(switching=run_3d.switching)`, `after_reload`'s `run_3d.reassert()`)
//! and the view's 3D methods (tools/native_spike/view.py: `_immersive_wanted`, `_immersive_sync`, `_immersive_tick`,
//! `set_immersive`, `immersive_toggle`, `immersive_click`, `immersive_tail_click`, `immersive_pointer`,
//! `_immersive_status`).
//!
//! The setting, the frame, the memory, the engine and the worker are ported (`crate::supervisor::immersive`), and
//! `sbs.ButtonSbs.leave` is recovery's (`managed::real`'s `restore_2d`).
//!
//! Not ported: [`Immersive::open`] gives `None`; no depth memory, no switch, 3D toggles do nothing.

use std::os::fd::BorrowedFd;

use xr_wire::run_control::Frame;

use super::{Frames, Run};

/// The run's `ImmersiveRun` and `DepthMemory`.
pub struct Immersive {
    _private: (),
}

impl Immersive {
    /// `find_engine(renderer)`, `DepthMemory()` when there is an engine, `ImmersiveRun(...)`.
    pub fn open(_run: &Run<'_>) -> Option<Immersive> {
        None
    }

    /// `depth_memory.fd`, inherited by each renderer (`--depth-fd`).
    pub fn depth_fd(&self) -> Option<BorrowedFd<'_>> {
        None
    }

    /// `run_3d.switching()`: the glasses are changing between 2D and 3D (a renderer exit then is expected).
    pub fn switching(&self) -> bool {
        false
    }

    /// `run_3d.reassert()`, after a config reload.
    pub fn reassert(&mut self) {}

    /// `immersive_toggle` (Ctrl+Alt+I) and `set_immersive` (the panel's `immersive`).
    pub fn on_message(&mut self, _message: &Frame, _out: &mut Frames) {}

    /// `immersive_run.close()` and `depth_memory.close()`: the glasses back in 2D, the engine gone; bounded.
    pub fn close(&mut self) {}
}
