//! Continuity across a renderer restart: the view and pose restored from, and saved to, the `--continuity-fd` file
//! (wayland.cpp restore_continuity, save_continuity; `continuity.rs` holds the ported format). Not in this build:
//! `--continuity-fd` is refused at launch.

use std::os::fd::BorrowedFd;

use super::Presentation;

/// Whether this build takes `--continuity-fd`. False: the launch exits 2, as before the feature was wired.
pub fn continuity_fd_supported() -> bool {
    false
}

#[derive(Default)]
pub(super) struct ContinuityShell {}

impl Presentation {
    /// After open: the saved state, consumed once. True when something was restored (main logs "continuity: restored").
    pub fn restore_continuity(&mut self, _fd: Option<BorrowedFd>) -> bool {
        false
    }

    /// At an orderly exit, before close.
    pub fn save_continuity(&mut self, _fd: Option<BorrowedFd>) {}
}
