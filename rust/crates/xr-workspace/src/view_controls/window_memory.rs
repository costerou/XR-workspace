//! Window memory in a run: `window_memory.tracker_for` and `WindowTracker` (tools/native_spike/window_memory.py:
//! `place`, `finish`, `set_names`, `mark`, `leaving`, `context`, `close`), used by `managed._run_owned` around
//! adoption (`tracker_for(client, store, fallback, names)`, `memory.place(names)`, adoption, `memory.finish()`) and
//! by `managed.view_controls` (`control.windows`, `windows.context = control.window_context`, `set_names` on a
//! desktop change, `desktops.leaving = windows.leaving`; in the `finally`, after the setup is saved,
//! `windows.close()`: the last capture, while the XR outputs still hold the windows).
//!
//! The capture, the file, the pairing and the plan are ported (`crate::window_memory`, `crate::lua::window_memory`).
//! What is left is the tracker: putting the remembered windows back at the start, keeping the memory current on
//! Hyprland's window events and Super drags, and the last capture.
//!
//! Not ported: [`Tracker::open`] gives `None` (adoption takes every window, as on a first run; no capture is taken).

use super::Run;
use super::view::ViewControl;

/// `window_memory.WindowTracker`.
pub struct Tracker {
    _private: (),
}

impl Tracker {
    /// `tracker_for(client, store, fallback, names)`: `None` without a glasses profile.
    pub fn open(_run: &mut Run<'_>) -> Option<Tracker> {
        None
    }

    /// `memory.place(names)`: the windows XR last held go back where they were, before adoption.
    pub fn place(&mut self, _run: &mut Run<'_>) {}

    /// `memory.finish()`: after adoption, whatever needs every window in place.
    pub fn finish(&mut self, _run: &mut Run<'_>) {}

    /// The `view_controls` wiring (`control.windows`, its context and listeners).
    pub fn attach(self, _view: Option<&mut ViewControl>) -> Option<Tracker> {
        Some(self)
    }

    /// `windows.mark()`: Hyprland is read again for where the windows are now.
    pub fn mark(&mut self) {}

    /// `windows.close()`: the last capture.
    pub fn close(&mut self, _run: &mut Run<'_>) {}
}
