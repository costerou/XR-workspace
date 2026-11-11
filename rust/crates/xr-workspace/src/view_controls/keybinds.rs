//! The key bind panel (Ctrl+Alt+K): `keybinds.BindControl` (tools/native_spike/keybinds.py), as
//! `managed.view_controls` builds it (`keybinds.load(keys_path)`, `DesktopBinds.from_text(client.binds_text())`,
//! `BindControl(control, ..., client, bind_args, desktop, notify)`), installs the run binds with its keys
//! (`bind_kwargs()`, then `installed()`, `startup_toast()`) and stops it (`keys.stop()`, before the binds go).
//!
//! The panel is ported (`crate::supervisor::keybinds`, `bind_check`; its loop side queues `Job`s for a `BindWorker`).
//! What is left is the run's: the worker on its own compositor client, `keybinds.json` beside the profile, and the
//! panel's frames (`binds`).
//!
//! Not ported: [`Keys::open`] gives `None`; the binds install with their default keys.

use xr_wire::run_control::Frame;

use super::{Frames, Run};

/// `keybinds.BindControl`.
pub struct Keys {
    _private: (),
}

impl Keys {
    /// The saved keys and the desktop's own binds, read first.
    pub fn open(_run: &mut Run<'_>) -> Option<Keys> {
        None
    }

    /// `bind_kwargs()`: the view and send binds `Client::install_view_binds` takes (`binds`, `send_binds`); `None`
    /// for either is its default table.
    pub fn bind_kwargs(&self) -> (Option<Vec<(String, String)>>, Option<Vec<(String, String)>>) {
        (None, None)
    }

    /// `keys.installed()`.
    pub fn installed(&mut self) {}

    /// `keys.startup_toast()`: a conflict on load is said once.
    pub fn startup_toast(&mut self) {}

    /// `keys.on_message(message)` (`binds_toggle`, `binds_key`).
    pub fn on_message(&mut self, _message: &Frame, _out: &mut Frames) {}

    /// `keys.stop()`.
    pub fn stop(&mut self) {}
}
