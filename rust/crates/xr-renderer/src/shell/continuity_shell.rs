//! Continuity across a renderer restart: the view and pose restored from, and saved to, the `--continuity-fd` file
//! (wayland.cpp restore_continuity, save_continuity; `continuity.rs` holds the ported format).
//!
//! The blob carries state other modules own: the view (`self.state.capture.view`), tracking's pose, stabilization,
//! viewing modes, maximized flag, last drawn head and landed-turn centre, and roll's per-desktop rolls. Those reach
//! this module only through [`carry_out`] and [`carry_in`].

use std::os::fd::BorrowedFd;

use xr_renderer::continuity::{self, ViewContinuity};

use super::scene::Features;
use super::{Presentation, monotonic_ms};

/// Whether this build takes `--continuity-fd`.
pub fn continuity_fd_supported() -> bool {
    true
}

#[derive(Default)]
pub(super) struct ContinuityShell {
    /// Tracking's and roll's share of the last restored blob, until those modules hold it themselves (see
    /// [`carry_out`]). Saved back as restored, so a restart without tracking hands the pose on unchanged, as the C++
    /// does with a pose nothing updated.
    held: ViewContinuity,
}

/// Tracking's and roll's state into `c`: `pose`, `stabilization`, `modes`, `maximized`, `last_drawn`, `centre_*`
/// (tracking) and `rolls` (roll).
///
/// At merge this becomes `f.tracking.save_continuity(c)` and `c.rolls = f.roll.rolls()`, with `Tracking` providing
/// `fn save_continuity(&self, c: &mut ViewContinuity)` (its `PoseState`, `Stabilizer`, `ModeController`, maximized
/// flag, last drawn `Mat3` and centre valid / serial / dir) and `RollState` providing `fn rolls(&self) -> [i32; 4]`.
/// `held` then goes.
fn carry_out(f: &Features, c: &mut ViewContinuity) {
    let h = &f.continuity.held;
    c.pose = h.pose.clone();
    c.stabilization = h.stabilization.clone();
    c.modes = h.modes.clone();
    c.maximized = h.maximized;
    c.last_drawn = h.last_drawn;
    c.centre_valid = h.centre_valid;
    c.centre_serial = h.centre_serial;
    c.centre_dir = h.centre_dir;
    c.rolls = h.rolls;
}

/// The reverse of [`carry_out`], as the C++ assigns them (no other reset: the roll's glide is not touched).
///
/// At merge: `f.tracking.restore_continuity(c)` (`fn restore_continuity(&mut self, c: &ViewContinuity)`, taking the
/// same fields) and `f.roll.set_rolls(c.rolls)` (`fn set_rolls(&mut self, rolls: [i32; 4])`, a plain assignment).
fn carry_in(f: &mut Features, c: &ViewContinuity) {
    f.continuity.held = c.clone();
}

impl Presentation {
    /// After open: the saved state, consumed once. True when something was restored (main logs "continuity: restored").
    pub fn restore_continuity(&mut self, fd: Option<BorrowedFd>) -> bool {
        let Some(c) = fd.and_then(|fd| continuity::restore(fd, monotonic_ms() as f64)) else {
            return false;
        };
        let s = &mut self.state;
        carry_in(&mut s.features, &c);
        s.capture.view = c.view;
        s.capture.invalidate();
        s.cadence.invalidate();
        true
    }

    /// At an orderly exit, before close.
    pub fn save_continuity(&mut self, fd: Option<BorrowedFd>) {
        let Some(fd) = fd else { return };
        let s = &self.state;
        let mut c = ViewContinuity { view: s.capture.view.clone(), ..ViewContinuity::default() };
        carry_out(&s.features, &mut c);
        c.saved_ms = monotonic_ms() as f64;
        continuity::save(fd, &c);
    }
}
