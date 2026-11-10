//! Sleep until told, for one waiter, on a futex. A system call only when the other side really
//! sleeps.

use super::CachePadded;
use std::sync::atomic::{AtomicU32, Ordering};

const IDLE: u32 = 0;
const PARKED: u32 = 1;
const NOTIFIED: u32 = 2;

/// A wake-up cannot be lost: both sides exchange the state and whichever comes second sees the
/// first; the waiter checks `ready()` after announcing itself, so an item pushed before that is
/// seen by `ready()`, and a notify after it finds `PARKED` and calls `futex_wake`.
#[derive(Default)]
pub struct Parker {
    state: CachePadded<AtomicU32>,
}

impl Parker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Hot. A system call only if the waiter is asleep.
    #[inline]
    pub fn notify(&self) {
        if self.state.swap(NOTIFIED, Ordering::AcqRel) == PARKED {
            futex_wake_one(&self.state);
        }
    }

    /// Returns at once if notified since the last wait or `ready()` holds; otherwise sleeps until
    /// a notify or `timeout_ms`. May also return early (a spurious wake): callers loop.
    #[inline]
    pub fn wait(&self, ready: impl FnOnce() -> bool, timeout_ms: u32) {
        if self.state.swap(PARKED, Ordering::AcqRel) != NOTIFIED && !ready() {
            futex_wait(&self.state, PARKED, timeout_ms);
        }
        self.state.swap(IDLE, Ordering::AcqRel);
    }
}

fn futex_wait(word: &AtomicU32, expected: u32, timeout_ms: u32) {
    let timeout = libc::timespec {
        tv_sec: (timeout_ms / 1000) as libc::time_t,
        tv_nsec: (timeout_ms % 1000) as libc::c_long * 1_000_000,
    };
    // SAFETY: `word` is a live, aligned u32 for the whole call; the kernel only reads it.
    unsafe {
        libc::syscall(
            libc::SYS_futex,
            word.as_ptr(),
            libc::FUTEX_WAIT | libc::FUTEX_PRIVATE_FLAG,
            expected,
            &timeout as *const libc::timespec,
            std::ptr::null::<u32>(),
            0u32,
        );
    }
}

fn futex_wake_one(word: &AtomicU32) {
    // SAFETY: as above; FUTEX_WAKE does not touch the word's value.
    unsafe {
        libc::syscall(
            libc::SYS_futex,
            word.as_ptr(),
            libc::FUTEX_WAKE | libc::FUTEX_PRIVATE_FLAG,
            1u32,
            std::ptr::null::<libc::timespec>(),
            std::ptr::null::<u32>(),
            0u32,
        );
    }
}
