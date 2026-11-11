//! What the run loop hosts beside the watchdog: the overview pointer, and in a managed run the glasses' view controls
//! around it (`crate::view_controls`, rust/crates/xr-workspace/VIEW_CONTROLS.md). [`super::Supervisor::run_hosting`]
//! waits on the hook's [`WakeFd`] and its [`LoopHook::sources`] with the heartbeat and status sockets, and hands it
//! each status datagram.
//!
//! The Python loop (`supervisor.Supervisor.run`) calls `control.poll(now)` after every pass: each heartbeat (120 Hz),
//! each status datagram, each sampler poke (up to 250 Hz) and each 50 ms timeout, overview open or not. What it needs
//! is narrower: the newest cursor sample applied once the sampler has fed one (`OverviewPointer.feed` pokes
//! `wake_reader`), and the pointer's timed steps (the hold, the dwell, the idle close) run when they are due. So here
//! the loop calls [`LoopHook::poll`] only when the hook's fd was signalled or its [`LoopHook::deadline`] has come.
//! A hook that is idle (the overview closed: no feed, no deadline) costs the loop nothing; one in use costs one poll
//! per event, never a fixed tick. The frames it appends go out on the renderer's bounded send path
//! ([`super::Supervisor::send`]).
//!
//! The hook is a trait so the run loop does not depend on `ViewControl` (not ported) or on the pointer's state machine:
//! the overview pointer's port implements it on the loop's side of the pointer, and its sampler side
//! ([`super::sampler::SampledPointer`]) calls [`WakeFd::signal`] from `feed` when the sample changed anything.

use std::io;
use std::os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd, RawFd};
use std::time::Instant;

/// An eventfd the hook's producers signal from any thread and the loop waits on. Signals before the loop drains it
/// coalesce into one poll.
#[derive(Debug)]
pub struct WakeFd(OwnedFd);

impl WakeFd {
    pub fn new() -> io::Result<Self> {
        let fd = unsafe { libc::eventfd(0, libc::EFD_NONBLOCK | libc::EFD_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(WakeFd(unsafe { OwnedFd::from_raw_fd(fd) }))
    }

    /// Wake the loop (the Python's `_poke`). Never blocks; a counter at its limit is already signalled.
    pub fn signal(&self) {
        let one = 1u64;
        unsafe { libc::write(self.0.as_raw_fd(), (&raw const one).cast(), 8) };
    }

    /// Clear the signal; true if there was one (the Python's `drain`).
    pub fn drain(&self) -> bool {
        let mut count = 0u64;
        let n = unsafe { libc::read(self.0.as_raw_fd(), (&raw mut count).cast(), 8) };
        n == 8 && count > 0
    }
}

impl AsFd for WakeFd {
    fn as_fd(&self) -> BorrowedFd<'_> {
        self.0.as_fd()
    }
}

/// The loop's side of whatever it hosts (the overview pointer's `poll`).
pub trait LoopHook {
    /// Signalled when [`LoopHook::poll`] has work: the loop drains it, then polls.
    fn wake_fd(&self) -> &WakeFd;
    /// When the hook must be polled though nothing signalled it (a hold, a dwell, an idle close); `None`: only when
    /// signalled. Read once per loop pass, before the loop waits.
    fn deadline(&mut self) -> Option<Instant> {
        None
    }
    /// Run once per signal or deadline. Frames for the renderer are appended to `out` (empty: none); the loop sends
    /// them in one bounded send.
    fn poll(&mut self, now: Instant, out: &mut Vec<u8>);
    /// More descriptors the loop waits on (the run's control socket, the Hyprland event stream: the Python's
    /// `selector.register` of `control.server.socket` and `control.events`). Read once, when the loop starts; none by
    /// default.
    fn sources(&self) -> Vec<RawFd> {
        Vec::new()
    }
    /// `sources()[index]` is readable (`_control_ready`, `_events_ready`): read what is there, or the loop wakes
    /// again at once. Frames for the renderer go to `out`. False: the source ended and is waited on no more (the
    /// event stream's EOF). Called while the run stops too, as the Python's selector does; the frames are then dropped.
    fn ready(&mut self, _index: usize, _now: Instant, _out: &mut Vec<u8>) -> bool {
        true
    }
    /// One datagram from the renderer's status socket, after the watchdog's own handling (`Supervisor.status_bytes`:
    /// the measured status the panel's `status` reply carries, and the view control's geometry and receipts).
    fn status(&mut self, _datagram: &[u8], _now: Instant) {}
    /// Descriptors to send with the frames just appended (a VR video's file: `video.FdFrame`); none by default.
    fn take_fds(&mut self) -> Vec<OwnedFd> {
        Vec::new()
    }
    /// A change to the run's guard that the hook's last call asked for: a pill's desktop change pauses it and then
    /// replaces it for the outputs as they are now (`ViewControl.supervisor.pause_guard()` / `replace_guard(...)`).
    fn guard_change(&mut self) -> Option<GuardChange> {
        None
    }
}

/// What [`LoopHook::guard_change`] asks of the watchdog.
#[derive(Debug)]
pub enum GuardChange {
    /// `Supervisor.pause_guard`.
    Pause,
    /// `Supervisor.replace_guard`: a new worker of this command, or none.
    Replace(Option<std::process::Command>),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signals_coalesce_and_drain() {
        let w = WakeFd::new().unwrap();
        assert!(!w.drain());
        w.signal();
        w.signal();
        assert!(w.drain());
        assert!(!w.drain());
    }
}
