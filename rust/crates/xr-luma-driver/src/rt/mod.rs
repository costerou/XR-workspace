//! The real-time layer of the Luma driver: how a sensor packet gets from the kernel to a published
//! pose as fast as the machine allows, without allocating, without blocking the reader and without
//! a lock a reader could hold up. A port of the C++ `luma::rt` (`spike/driver/include/luma/rt.hpp`);
//! `docs/performance.md` has the numbers and the reasoning. Nothing here knows about USB or the
//! protocol: the daemon plugs those in as a [`Source`] and a [`Sink`].
//!
//! Rules for everything marked "hot": no heap, no locks, no logging, no system call but the ones
//! named. `tests/rt.rs` and the `luma_rt_bench` binary prove the heap part by counting every
//! `malloc` in the process.

mod parker;
mod pipeline;
mod policy;
mod spsc;

pub use crate::latency::LatencyHistogram;
pub use parker::Parker;
pub use pipeline::{
    Options, Pipeline, PipelineMode, PipelineStats, RT_STACK_BYTES, RT_STACK_PREFAULT, Sink,
    Source, SourceStatus,
};
pub use policy::{
    MEMLOCK_WANTED_BYTES, MemoryLockResult, RT_TIME_GUARD_US, RealSysOps, SHORT_SLICE_NS,
    SchedClass, SysOps, ThreadPolicy, ThreadPolicyResult, apply_thread_policy, lock_memory,
    memlock_is_safe, prefault_stack,
};
pub use spsc::{Consumer, Producer, spsc_ring};

/// The size both sides of a hand-over keep their own counters apart by.
pub const CACHE_LINE: usize = 64;

/// Keeps a value on a cache line of its own, so two cores writing neighbours do not share one.
#[derive(Debug, Default)]
#[repr(align(64))]
pub struct CachePadded<T>(pub T);

impl<T> std::ops::Deref for CachePadded<T> {
    type Target = T;
    fn deref(&self) -> &T {
        &self.0
    }
}

fn clock_ns(clock: libc::clockid_t) -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `ts` is a valid, writable timespec; the clock ids used here always exist on Linux.
    unsafe { libc::clock_gettime(clock, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// `CLOCK_MONOTONIC_RAW`: the hardware counter, never slewed by NTP, so intervals between sensor
/// samples are the true ones. A vDSO call (no system call, about 20 ns) wherever the clocksource
/// is the TSC. Hot.
#[inline]
pub fn monotonic_raw_ns() -> u64 {
    clock_ns(libc::CLOCK_MONOTONIC_RAW)
}

/// Add this to a raw time to get `CLOCK_MONOTONIC`, the clock the renderer runs on. They drift
/// apart slowly, so take it again now and then (not hot).
pub fn raw_to_monotonic_offset_ns() -> i64 {
    // The tightest of a few raw readings between two monotonic ones: a preempted attempt just loses.
    let mut best = u64::MAX;
    let mut offset = 0i64;
    for _ in 0..16 {
        let before = clock_ns(libc::CLOCK_MONOTONIC);
        let raw = clock_ns(libc::CLOCK_MONOTONIC_RAW);
        let after = clock_ns(libc::CLOCK_MONOTONIC);
        if after - before < best {
            best = after - before;
            offset = (before + (after - before) / 2) as i64 - raw as i64;
        }
    }
    offset
}
