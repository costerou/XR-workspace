//! Privacy mode (Ctrl+Alt+D): `privacy.PrivacyDisplay` (tools/native_spike/privacy.py: `request`, `close`, the worker
//! and the watcher), as `managed.view_controls` builds it (`PrivacyDisplay(client, output, fallback)`, when the client
//! has `set_dpms`) and closes it (`control.privacy.close()`: the main display back on, before 3D and the binds).
//!
//! The decisions and the Lua are ported (`crate::supervisor::privacy`, `crate::lua::privacy`; `privacy::wake` is
//! already recovery's). What is left is the run's worker and watcher threads on their own compositor client.
//!
//! Not ported: [`Privacy::open`] gives `None`; Ctrl+Alt+D does nothing.

use super::Run;

/// `privacy.PrivacyDisplay`.
pub struct Privacy {
    _private: (),
}

impl Privacy {
    /// `PrivacyDisplay(client, output, fallback)`.
    pub fn open(_run: &Run<'_>) -> Option<Privacy> {
        None
    }

    /// `privacy.request()`: one toggle, off the loop.
    pub fn request(&mut self) {}

    /// `privacy.close()`: the main display back on; no toggle after this.
    pub fn close(&mut self) {}
}
