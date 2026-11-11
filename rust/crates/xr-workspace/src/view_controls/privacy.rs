//! Privacy mode (Ctrl+Alt+D): `privacy.PrivacyDisplay` (tools/native_spike/privacy.py: `request`, `close`, the worker
//! and the watcher), as `managed.view_controls` builds it (`PrivacyDisplay(client, output, fallback)`, when the client
//! has `set_dpms`) and closes it (`control.privacy.close()`: the main display back on, before 3D and the binds).
//!
//! The decisions, the worker and the watcher are [`crate::supervisor::privacy`]'s, and the Lua is
//! [`crate::lua::privacy`]'s. This is the run's wiring: the worker and the watcher get a compositor client of their
//! own, pinned to the run's verified compositor and session (the Python shares its client; a Rust [`Client`] is not
//! shared across threads), and each change is told through the run's desktop notification
//! ([`crate::managed::real::notify`], `send.notify`). Recovery wakes the fallback again in every path
//! (`managed::recover_restoring`, `privacy::wake`), so a close that could not take the lock never leaves it dark.

use super::Run;
use crate::hyprland::Client;
use crate::supervisor::privacy::{DEADLINE, PrivacyDisplay, WATCH};

/// `privacy.PrivacyDisplay`.
pub struct Privacy {
    display: PrivacyDisplay<Client>,
}

impl Privacy {
    /// `PrivacyDisplay(client, output, fallback)`. `None` before any request verified the compositor: the worker's
    /// client is pinned to that process, never to whatever answers the socket later.
    pub fn open(run: &Run<'_>) -> Option<Privacy> {
        let identity = run.client.identity()?.clone();
        let client =
            Client::pinned(run.client.instance(), run.runtime, identity, run.session).ok()?;
        let fallback = Some(run.fallback).filter(|f| !f.is_empty());
        let display = PrivacyDisplay::new(
            client,
            run.output,
            fallback,
            Some(Box::new(crate::managed::real::notify)),
            DEADLINE,
            WATCH,
        );
        Some(Privacy { display })
    }

    /// `privacy.request()`: one toggle, off the loop.
    pub fn request(&mut self) {
        self.display.request(None);
    }

    /// `privacy.close()`: the main display back on; no toggle after this.
    pub fn close(&mut self) {
        self.display.close(None);
    }
}
