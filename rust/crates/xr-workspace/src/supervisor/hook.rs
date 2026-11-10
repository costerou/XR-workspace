//! What the run loop hosts beside the watchdog: the overview pointer, so far (later, anything else that turns events
//! into renderer frames). [`super::Supervisor::run_hosting`] waits on the hook's [`WakeFd`] with the heartbeat and
//! status sockets, and on nothing else of it.
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
use std::os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd};
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
