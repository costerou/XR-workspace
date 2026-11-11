//! The run's Ctrl+Alt binds: what `managed.view_controls` builds around `client.install_view_binds` (tools/native_spike/
//! managed.py: `bind_args`, `view.session_mode`, `after_reload`, `menu_binds`, `typing_binds`, and in the `finally`
//! `control.stop_menu_binds()` and `client.remove_view_binds()`), with `ViewControl._reload_work`,
//! `_sync_menu_binds` / `_menu_bind_work` and `_typing_toggle` / `_typing_bind_work` (tools/native_spike/view.py).
//!
//! The Lua is ported (`crate::lua::view`, the `Client::install_view_binds` family in `crate::hyprland`); what is left is
//! the run's use of it: the bind helper's command line (`xr-control CONTROL_SOCKET RUN_ID SESSION`, the socket's path),
//! the install with the key bind panel's keys, the reinstall after a config reload (typing mode kept, the presentation
//! parked, the session or wide mode put back, 3D reasserted), the placement panel's and typing mode's swaps (off the
//! loop, in order), and the removal last, which never blocks recovery.
//!
//! Not ported: [`Binds::open`] gives `None`; no bind is installed or removed.

use super::Run;
use super::keybinds::Keys;
use super::socket::Server;

/// The bind helper's arguments and the run's session mode, for the install and every reinstall.
pub struct Binds {
    _private: (),
}

impl Binds {
    /// `bind_args` (the helper, the socket's path, run id, session) and `mode = view.session_mode(...)` (none on a
    /// Pro 2).
    pub fn open(_run: &Run<'_>, _socket: Option<&Server>) -> Option<Binds> {
        None
    }

    /// `client.install_view_binds(*bind_args, **control.keys.bind_kwargs())`.
    pub fn install(&mut self, _run: &mut Run<'_>, _keys: Option<&Keys>) {}

    /// `after_reload`: a config reload dropped the runtime binds and reset the session mode.
    pub fn after_reload(&mut self, _typing: bool, _keys: Option<&Keys>) {}

    /// `menu_binds(open_)`: the placement panel's bare keys while one is open.
    pub fn menu_binds(&mut self, _open: bool) {}

    /// `typing_binds(on)`: typing mode holds only Ctrl+Alt+T.
    pub fn typing_binds(&mut self, _on: bool, _keys: Option<&Keys>) {}

    /// `control.stop_menu_binds()`: no modal panel bind lands after the removal.
    pub fn stop_menu_binds(&mut self) {}

    /// `client.remove_view_binds()` (an open panel's modal binds too); errors ignored.
    pub fn remove(&mut self, _run: &mut Run<'_>) {}
}
