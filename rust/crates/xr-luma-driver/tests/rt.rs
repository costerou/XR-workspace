//! Offline tests of the driver's real-time layer, ported from `spike/driver/bench/rt_test.cpp`.
//! No hardware, no privileges needed. The real-time paths this machine may not permit are driven
//! through a fake `SysOps`; `apply_real_thread` shows what the real kernel gave. The seqlock and
//! shared-memory cases belong to xr-pose, the setup commands to the daemon; the allocation check
//! is in `tests/rt_alloc.rs`, alone, because it counts every allocation in the process.

use std::cell::Cell;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};
use xr_luma_driver::rt::*;

#[test]
fn ring_holds_exactly_capacity_and_wraps() {
    let (mut tx, mut rx) = spsc_ring::<i32, 4>();
    assert_eq!(rx.try_pop(), None);
    for round in 0..10 {
        // several rounds cross the index wrap
        for i in 0..4 {
            assert!(tx.try_push(round * 10 + i));
        }
        assert!(!tx.try_push(99));
        for i in 0..4 {
            assert_eq!(rx.try_pop(), Some(round * 10 + i));
        }
        assert_eq!(rx.try_pop(), None);
        assert!(rx.is_empty());
    }
}

#[test]
fn ring_keeps_order_across_threads() {
    // Tiny, so both the full and the empty edge are hit constantly.
    let (mut tx, mut rx) = spsc_ring::<u64, 16>();
    const N: u64 = 2_000_000;
    let producer = thread::spawn(move || {
        let mut i = 0;
        while i < N {
            if tx.try_push(i) {
                i += 1;
            }
        }
    });
    let mut want = 0;
    while want < N {
        if let Some(got) = rx.try_pop() {
            assert_eq!(got, want);
            want += 1;
        }
    }
    producer.join().unwrap();
}

/// A lost wake-up would cost the full timeout on every occurrence, so a long ping-pong finishes in
/// time only if none is lost.
#[test]
fn parker_loses_no_wakeups() {
    let a = Arc::new(Parker::new());
    let b = Arc::new(Parker::new());
    let ball = Arc::new(AtomicU64::new(0));
    const N: u64 = 100_000;
    const TIMEOUT_MS: u32 = 2000;
    let start = Instant::now();
    let other = {
        let (a, b, ball) = (Arc::clone(&a), Arc::clone(&b), Arc::clone(&ball));
        thread::spawn(move || {
            let mut i = 1;
            while i <= N {
                while ball.load(Ordering::Acquire) != i {
                    a.wait(|| ball.load(Ordering::Acquire) == i, TIMEOUT_MS);
                }
                ball.store(i + 1, Ordering::Release);
                b.notify();
                i += 2;
            }
        })
    };
    let mut i = 0;
    while i < N {
        ball.store(i + 1, Ordering::Release);
        a.notify();
        while ball.load(Ordering::Acquire) != i + 2 {
            b.wait(|| ball.load(Ordering::Acquire) == i + 2, TIMEOUT_MS);
        }
        i += 2;
    }
    other.join().unwrap();
    assert!(start.elapsed() < Duration::from_millis(TIMEOUT_MS as u64));
}

#[test]
fn parker_wait_times_out_without_a_notify() {
    let p = Parker::new();
    let start = Instant::now();
    p.wait(|| false, 20);
    let took = start.elapsed();
    assert!(took >= Duration::from_millis(15) && took < Duration::from_millis(2000));
}

/// A machine that permits what this one may not. Each `*_rc` is the errno to fail with, 0 for ok.
#[derive(Default)]
struct FakeOps {
    fifo_rc: i32,
    nice_rc: i32,
    affinity_rc: i32,
    guard_rc: i32,
    slice_rc: i32,
    /// What a hard limit below the request would allow.
    fifo_grant: Option<i32>,
    fifo_calls: Cell<u32>,
    nice_calls: Cell<u32>,
    last_nice: Cell<i32>,
    last_cpu: Cell<Option<usize>>,
    guard_us: Cell<u64>,
    slice_ns: Cell<u64>,
}

fn rc(e: i32) -> Result<(), i32> {
    if e == 0 { Ok(()) } else { Err(e) }
}

impl SysOps for FakeOps {
    fn set_fifo(&self, priority: &mut i32) -> Result<(), i32> {
        self.fifo_calls.set(self.fifo_calls.get() + 1);
        if let Some(grant) = self.fifo_grant {
            *priority = grant;
        }
        rc(self.fifo_rc)
    }
    fn set_nice(&self, nice: i32) -> Result<(), i32> {
        self.nice_calls.set(self.nice_calls.get() + 1);
        self.last_nice.set(nice);
        rc(self.nice_rc)
    }
    fn set_affinity(&self, cpu: usize) -> Result<(), i32> {
        self.last_cpu.set(Some(cpu));
        rc(self.affinity_rc)
    }
    fn arm_rttime_guard(&self, us: u64) -> Result<(), i32> {
        self.guard_us.set(us);
        rc(self.guard_rc)
    }
    fn set_short_slice(&self, ns: u64) -> Result<(), i32> {
        self.slice_ns.set(ns);
        rc(self.slice_rc)
    }
}

fn defaults() -> ThreadPolicy {
    ThreadPolicy::default()
}

#[test]
fn policy_uses_fifo_when_permitted() {
    let ops = FakeOps::default();
    let p = ThreadPolicy {
        fifo_priority: 12,
        ..defaults()
    };
    let r = apply_thread_policy(&p, &ops);
    assert!(r.sched == SchedClass::Fifo && r.priority == 12);
    assert_eq!(ops.nice_calls.get(), 0);
    // A FIFO thread that spins forever must be killable by the kernel.
    assert_eq!(ops.guard_us.get(), RT_TIME_GUARD_US);
    assert!(!r.needs_setup && r.detail.is_empty());
}

#[test]
fn policy_reports_the_priority_a_limit_allowed() {
    let ops = FakeOps {
        fifo_grant: Some(5),
        ..Default::default()
    };
    let p = ThreadPolicy {
        fifo_priority: 12,
        ..defaults()
    };
    assert_eq!(apply_thread_policy(&p, &ops).priority, 5);
}

#[test]
fn policy_falls_back_to_nice() {
    let ops = FakeOps {
        fifo_rc: libc::EPERM,
        ..Default::default()
    };
    let p = ThreadPolicy {
        fallback_nice: -7,
        ..defaults()
    };
    let r = apply_thread_policy(&p, &ops);
    assert!(r.sched == SchedClass::Nice && r.nice == -7 && ops.last_nice.get() == -7);
    assert!(r.needs_setup);
    assert_eq!(r.detail, "no SCHED_FIFO: Operation not permitted");
}

#[test]
fn policy_survives_having_no_privilege_at_all() {
    let ops = FakeOps {
        fifo_rc: libc::EPERM,
        nice_rc: libc::EACCES,
        ..Default::default()
    };
    let r = apply_thread_policy(&defaults(), &ops);
    assert!(r.sched == SchedClass::Normal && r.needs_setup);
    assert_eq!(
        r.detail,
        "no SCHED_FIFO: Operation not permitted; no nice -10: Permission denied"
    );
}

/// Refusing FIFO is the safe side: without the guard a hot-loop bug would freeze a core at
/// real-time priority.
#[test]
fn policy_refuses_fifo_without_the_runaway_guard() {
    let ops = FakeOps {
        guard_rc: libc::EPERM,
        ..Default::default()
    };
    let r = apply_thread_policy(&defaults(), &ops);
    assert_eq!(ops.fifo_calls.get(), 0);
    assert_eq!(r.sched, SchedClass::Nice);
}

/// An inherited RLIMIT_RTTIME under one tick would let the kernel kill a FIFO thread at random;
/// the guard reports ERANGE and the policy falls back instead.
#[test]
fn policy_refuses_fifo_under_a_near_zero_rttime_limit() {
    let ops = FakeOps {
        guard_rc: libc::ERANGE,
        ..Default::default()
    };
    let r = apply_thread_policy(&defaults(), &ops);
    assert_eq!(ops.fifo_calls.get(), 0);
    assert!(r.sched == SchedClass::Nice && r.needs_setup);
    assert!(
        r.detail.contains("RLIMIT_RTTIME is under 10 ms"),
        "{}",
        r.detail
    );
}

/// Pinning helps a FIFO thread keep its cache and hurts a normal one most of all
/// (docs/performance.md), so fifo_cpu waits for FIFO.
#[test]
fn policy_pins_fifo_cpu_only_once_fifo_is_granted() {
    let p = ThreadPolicy {
        fifo_cpu: Some(5),
        ..defaults()
    };
    let granted = FakeOps::default();
    let r = apply_thread_policy(&p, &granted);
    assert!(r.sched == SchedClass::Fifo && r.cpu == Some(5) && granted.last_cpu.get() == Some(5));
    let refused = FakeOps {
        fifo_rc: libc::EPERM,
        ..Default::default()
    };
    let fallback = apply_thread_policy(&p, &refused);
    assert!(
        fallback.sched == SchedClass::Nice
            && fallback.cpu.is_none()
            && refused.last_cpu.get().is_none()
    );
    let bad = FakeOps {
        affinity_rc: libc::EINVAL,
        ..Default::default()
    };
    let unpinned = apply_thread_policy(&p, &bad);
    assert!(
        unpinned.sched == SchedClass::Fifo && unpinned.cpu.is_none() && !unpinned.detail.is_empty()
    );
}

#[test]
fn the_default_priority_is_20() {
    assert_eq!(ThreadPolicy::default().fifo_priority, 20);
}

#[test]
fn describe_names_what_was_granted() {
    let fifo = ThreadPolicyResult {
        sched: SchedClass::Fifo,
        priority: 20,
        cpu: Some(3),
        ..Default::default()
    };
    assert_eq!(describe(&fifo), "class=fifo priority=20 cpu=3");
    let normal = ThreadPolicyResult {
        short_slice: true,
        ..Default::default()
    };
    assert_eq!(describe(&normal), "class=normal short_slice=yes cpu=any");
    let nice = ThreadPolicyResult {
        sched: SchedClass::Nice,
        nice: -10,
        ..Default::default()
    };
    assert_eq!(
        describe(&nice),
        "class=nice nice=-10 short_slice=no cpu=any"
    );
}

/// The slice is a normal-class setting: it applies when FIFO was refused, whether or not nice was,
/// and needs no privilege.
#[test]
fn policy_asks_for_a_short_slice_only_as_a_fallback() {
    let ops = FakeOps::default();
    assert!(!apply_thread_policy(&defaults(), &ops).short_slice);
    assert_eq!(ops.slice_ns.get(), 0);

    let refused = FakeOps {
        fifo_rc: libc::EPERM,
        nice_rc: libc::EACCES,
        ..Default::default()
    };
    let r = apply_thread_policy(&defaults(), &refused);
    assert!(r.short_slice && refused.slice_ns.get() == SHORT_SLICE_NS);
    assert_eq!(r.sched, SchedClass::Normal);

    let nice = FakeOps {
        fifo_rc: libc::EPERM,
        ..Default::default()
    };
    assert!(apply_thread_policy(&defaults(), &nice).short_slice);

    let off = FakeOps {
        fifo_rc: libc::EPERM,
        ..Default::default()
    };
    let p = ThreadPolicy {
        short_slice: false,
        ..defaults()
    };
    assert!(!apply_thread_policy(&p, &off).short_slice && off.slice_ns.get() == 0);
}

#[test]
fn policy_survives_a_kernel_without_slices() {
    let ops = FakeOps {
        fifo_rc: libc::EPERM,
        nice_rc: libc::EPERM,
        slice_rc: libc::EINVAL,
        ..Default::default()
    };
    let r = apply_thread_policy(&defaults(), &ops);
    assert!(!r.short_slice && r.sched == SchedClass::Normal);
}

#[test]
fn short_slice_reaches_the_real_kernel() {
    #[repr(C)]
    #[derive(Default)]
    struct Attr {
        size: u32,
        policy: u32,
        flags: u64,
        nice: i32,
        priority: u32,
        runtime: u64,
        deadline: u64,
        period: u64,
    }
    let (asked, runtime) = thread::spawn(|| {
        let asked = RealSysOps.set_short_slice(SHORT_SLICE_NS).is_ok();
        let mut a = Attr::default();
        // SAFETY: `a` is a writable sched_attr of the size passed.
        let rc = unsafe {
            libc::syscall(
                libc::SYS_sched_getattr,
                0,
                &mut a as *mut Attr,
                std::mem::size_of::<Attr>() as u32,
                0u32,
            )
        };
        (asked, if rc == 0 { a.runtime } else { 1 })
    })
    .join()
    .unwrap();
    if asked {
        assert_eq!(runtime, SHORT_SLICE_NS);
    }
}

#[test]
fn policy_realtime_off_never_asks() {
    let ops = FakeOps::default();
    let p = ThreadPolicy {
        realtime: false,
        ..defaults()
    };
    let r = apply_thread_policy(&p, &ops);
    assert!(ops.fifo_calls.get() == 0 && ops.nice_calls.get() == 0);
    assert!(r.sched == SchedClass::Normal && !r.needs_setup);
}

#[test]
fn policy_pins_only_when_asked_and_survives_a_bad_cpu() {
    let ops = FakeOps::default();
    let r = apply_thread_policy(&defaults(), &ops);
    assert!(ops.last_cpu.get().is_none() && r.cpu.is_none());

    let ops = FakeOps::default();
    let p = ThreadPolicy {
        cpu: Some(3),
        ..defaults()
    };
    assert_eq!(apply_thread_policy(&p, &ops).cpu, Some(3));
    assert_eq!(ops.last_cpu.get(), Some(3));

    let bad = FakeOps {
        affinity_rc: libc::EINVAL,
        ..Default::default()
    };
    let r = apply_thread_policy(&p, &bad);
    assert!(r.cpu.is_none() && r.sched == SchedClass::Fifo && !r.detail.is_empty());
}

fn current_cpu() -> usize {
    // SAFETY: plain query.
    unsafe { libc::sched_getcpu() as usize }
}

/// What the real kernel does with this user's real limits: whatever it granted, the report must
/// match the thread's actual state.
#[test]
fn apply_real_thread() {
    let (r, policy, nice, cpu) = thread::spawn(|| {
        let p = ThreadPolicy {
            cpu: Some(current_cpu()),
            ..defaults()
        };
        let r = apply_thread_policy(&p, &RealSysOps);
        // SAFETY: plain queries on the calling thread; the set is plain bits.
        unsafe {
            let policy = libc::sched_getscheduler(0) & !libc::SCHED_RESET_ON_FORK;
            let nice = libc::getpriority(libc::PRIO_PROCESS, 0);
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            let pinned =
                libc::sched_getaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &mut set) == 0
                    && libc::CPU_COUNT(&set) == 1;
            (r, policy, nice, pinned.then(current_cpu))
        }
    })
    .join()
    .unwrap();
    assert_eq!(r.cpu, cpu);
    if r.sched == SchedClass::Fifo {
        assert_eq!(policy, libc::SCHED_FIFO);
    } else {
        assert_eq!(policy, libc::SCHED_OTHER);
    }
    if r.sched == SchedClass::Nice {
        assert_eq!(nice, r.nice);
    }
}

#[test]
fn pinning_to_a_cpu_that_does_not_exist_is_reported() {
    let r = thread::spawn(|| {
        let p = ThreadPolicy {
            cpu: Some(100_000),
            ..defaults()
        };
        apply_thread_policy(&p, &RealSysOps)
    })
    .join()
    .unwrap();
    assert!(r.cpu.is_none() && !r.detail.is_empty());
}

#[test]
fn memory_lock_only_under_a_limit_it_cannot_hit() {
    // Under a small limit MCL_FUTURE would make later mmaps fail: libudev on a reconnect, of all
    // moments.
    assert!(!memlock_is_safe(8 * 1024 * 1024));
    assert!(memlock_is_safe(MEMLOCK_WANTED_BYTES));
    assert!(memlock_is_safe(libc::RLIM_INFINITY));
}

/// In a child, because both change the process for good. The child only makes system calls and
/// leaves with `_exit`, so forking a threaded test binary is safe.
fn in_child(f: fn() -> bool) {
    // SAFETY: see above.
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        // SAFETY: leaves the child without running anything inherited.
        unsafe { libc::_exit(if f() { 0 } else { 1 }) };
    }
    let mut status = 0;
    // SAFETY: waits for our own child.
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0);
}

fn rlimit(resource: libc::__rlimit_resource_t) -> Option<libc::rlimit> {
    let mut l = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: `l` is writable.
    (unsafe { libc::getrlimit(resource, &mut l) } == 0).then_some(l)
}

#[test]
fn real_rttime_guard_lands_in_the_kernel() {
    in_child(|| {
        let Some(mut expected) = rlimit(libc::RLIMIT_RTTIME) else {
            return false;
        };
        // Sandboxed launchers may already impose a tighter (even zero) limit. The guard must land
        // without relaxing that limit, including when a later caller asks for longer.
        for us in [RT_TIME_GUARD_US, RT_TIME_GUARD_US / 2, RT_TIME_GUARD_US * 2] {
            // Under the floor the guard refuses FIFO and leaves the limit alone.
            if expected.rlim_cur.min(us) < RT_TIME_FLOOR_US {
                if RealSysOps.arm_rttime_guard(us) != Err(libc::ERANGE) {
                    return false;
                }
            } else {
                expected.rlim_cur = expected.rlim_cur.min(us);
                expected.rlim_max = expected.rlim_max.min(us * 5);
                if RealSysOps.arm_rttime_guard(us).is_err() {
                    return false;
                }
            }
            match rlimit(libc::RLIMIT_RTTIME) {
                Some(l) if l.rlim_cur == expected.rlim_cur && l.rlim_max == expected.rlim_max => {}
                _ => return false,
            }
        }
        true
    });
}

#[test]
fn real_memory_lock_matches_the_limit() {
    in_child(|| {
        let r = lock_memory();
        let Some(limit) = rlimit(libc::RLIMIT_MEMLOCK) else {
            return false;
        };
        if !memlock_is_safe(limit.rlim_max) {
            return !r.locked && !r.detail.is_empty();
        }
        r.locked
    });
}

fn clock(id: libc::clockid_t) -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `ts` is writable.
    unsafe { libc::clock_gettime(id, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

#[test]
fn clocks_are_raw_and_convertible() {
    let a = monotonic_raw_ns();
    let b = clock(libc::CLOCK_MONOTONIC_RAW);
    assert!(b >= a && b - a < 50_000_000);
    let mono = clock(libc::CLOCK_MONOTONIC);
    let offset = raw_to_monotonic_offset_ns();
    let converted = monotonic_raw_ns().wrapping_add(offset as u64);
    assert!(converted >= mono && converted - mono < 50_000_000);
}

#[test]
fn histogram_percentiles_are_within_three_percent() {
    let mut h = LatencyHistogram::new();
    for v in 1..=100_000 {
        h.record(v); // 1..100 us uniform: pN is N us
    }
    let within = |got: u64, want: f64| (got as f64 - want).abs() <= want * 0.035;
    assert!(within(h.percentile(50.0), 50_000.0));
    assert!(within(h.percentile(99.0), 99_000.0));
    assert!(h.max() == 100_000 && h.min() == 1 && h.count() == 100_000);
    assert!((h.mean() - 50_000.5).abs() < 1.0);
    assert!((h.stddev() - 28_867.5).abs() < 1.0); // (b-a)/sqrt(12)
}

#[test]
fn histogram_shows_a_lone_spike_in_max_not_in_p99() {
    let mut h = LatencyHistogram::new();
    for _ in 0..9999 {
        h.record(1000);
    }
    h.record(5_000_000);
    assert!(h.percentile(99.0) <= 1032);
    assert_eq!(h.max(), 5_000_000);
}

#[test]
fn histogram_small_and_huge_values_stay_in_range() {
    let mut h = LatencyHistogram::new();
    for v in [0, 63, 64, u64::MAX] {
        h.record(v);
    }
    assert!(h.count() == 4 && h.min() == 0 && h.max() == u64::MAX);
    assert_eq!(h.percentile(25.0), 0);
    assert!(h.percentile(100.0) >= h.percentile(75.0));
}

#[test]
fn histogram_empty_reads_zero() {
    let h = LatencyHistogram::new();
    assert!(h.percentile(99.0) == 0 && h.max() == 0 && h.min() == 0);
    assert!(h.mean() == 0.0 && h.stddev() == 0.0);
}

// --- Pipeline -----------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Default)]
struct Numbered {
    n: u64,
}

struct CountingSource {
    next_n: u64,
    end: u64,
}

impl CountingSource {
    fn new(next_n: u64, end: u64) -> Self {
        Self { next_n, end }
    }
}

impl Source for CountingSource {
    type Item = Numbered;
    fn next(&mut self, out: &mut Numbered) -> SourceStatus {
        if self.next_n >= self.end {
            return SourceStatus::Stop;
        }
        out.n = self.next_n;
        self.next_n += 1;
        SourceStatus::Item
    }
}

/// What the test thread watches while the sink lives on a pipeline thread.
#[derive(Default)]
struct Seen {
    seen: AtomicU64,
    out_of_order: AtomicBool,
    tid: AtomicI32,
}

#[derive(Default, Clone)]
struct OrderSink {
    state: Arc<Seen>,
    delay_us: u64,
}

impl Sink<Numbered> for OrderSink {
    fn publish(&mut self, i: &Numbered) {
        let s = &self.state;
        // SAFETY: plain system call.
        s.tid.store(unsafe { libc::gettid() }, Ordering::Relaxed);
        let seen = s.seen.load(Ordering::Relaxed);
        if i.n != seen {
            s.out_of_order.store(true, Ordering::Relaxed);
        }
        if self.delay_us != 0 {
            thread::sleep(Duration::from_micros(self.delay_us));
        }
        s.seen.store(seen + 1, Ordering::Release);
    }
}

fn wait_for(mut done: impl FnMut() -> bool) {
    for _ in 0..5000 {
        if done() {
            return;
        }
        thread::sleep(Duration::from_millis(1));
    }
}

/// No batching: one published pose per sample, in order, even when several wait in the ring.
#[test]
fn pipeline_ring_publishes_every_item_in_order() {
    let sink = OrderSink {
        delay_us: 1, // lets a backlog build up
        ..Default::default()
    };
    let seen = Arc::clone(&sink.state);
    // A source faster than the sink would overrun an 8-slot ring: pace it.
    struct Paced(CountingSource, Arc<Seen>);
    impl Source for Paced {
        type Item = Numbered;
        fn next(&mut self, out: &mut Numbered) -> SourceStatus {
            while self.0.next_n > self.1.seen.load(Ordering::Acquire) + 6 {
                thread::yield_now();
            }
            self.0.next(out)
        }
    }
    let src = Paced(CountingSource::new(0, 5000), Arc::clone(&seen));
    let p = Pipeline::<_, _, 8>::start(src, sink, &Options::default()).unwrap();
    wait_for(|| seen.seen.load(Ordering::Acquire) >= 5000);
    let (_, _, s) = p.stop();
    assert_eq!(seen.seen.load(Ordering::Acquire), 5000);
    assert!(!seen.out_of_order.load(Ordering::Relaxed));
    assert!(s.items_read == 5000 && s.items_published == 5000 && s.ring_overruns == 0);
    assert!(s.max_backlog >= 2); // several were waiting, and all were published individually
}

#[test]
fn pipeline_stop_drains_the_ring() {
    let sink = OrderSink {
        delay_us: 50,
        ..Default::default()
    };
    let seen = Arc::clone(&sink.state);
    let p = Pipeline::<_, _, 1024>::start(CountingSource::new(0, 800), sink, &Options::default())
        .unwrap();
    // The source is long done or nearly; nothing it produced may vanish.
    let (_, _, s) = p.stop();
    assert_eq!(seen.seen.load(Ordering::Acquire), s.items_read);
    assert_eq!(s.items_read, s.items_published);
}

/// The narrow case: the last item lands just after fusion found the ring empty, and the reader
/// finishes at once. Many short runs, so fusion's check keeps landing in that window.
#[test]
fn pipeline_stop_never_strands_the_last_item() {
    for run in 0..3000u64 {
        let end = 1 + run % 3;
        let sink = OrderSink::default();
        let seen = Arc::clone(&sink.state);
        let p: Pipeline<_, _> =
            Pipeline::start(CountingSource::new(0, end), sink, &Options::default()).unwrap();
        while p.stats().items_read < end {
            thread::yield_now();
        }
        p.stop();
        assert_eq!(seen.seen.load(Ordering::Acquire), end);
    }
}

#[test]
fn pipeline_inline_mode_publishes_on_the_reader_thread() {
    let sink = OrderSink::default();
    let seen = Arc::clone(&sink.state);
    let o = Options {
        mode: PipelineMode::Inline,
        ..Options::default()
    };
    let p: Pipeline<_, _> = Pipeline::start(CountingSource::new(0, 1000), sink, &o).unwrap();
    wait_for(|| seen.seen.load(Ordering::Acquire) >= 1000);
    let reader = p.reader_tid();
    assert_eq!(p.fusion_tid(), 0);
    let (_, _, s) = p.stop();
    assert_eq!(seen.seen.load(Ordering::Acquire), 1000);
    assert_eq!(seen.tid.load(Ordering::Relaxed), reader);
    assert!(s.items_published == 1000 && s.fusion_sleeps == 0);
}

#[test]
fn pipeline_ring_uses_a_separate_fusion_thread() {
    let sink = OrderSink::default();
    let seen = Arc::clone(&sink.state);
    let p: Pipeline<_, _> =
        Pipeline::start(CountingSource::new(0, 10), sink, &Options::default()).unwrap();
    wait_for(|| seen.tid.load(Ordering::Relaxed) != 0);
    let (fusion, reader) = (p.fusion_tid(), p.reader_tid());
    p.stop();
    assert_eq!(seen.tid.load(Ordering::Relaxed), fusion);
    assert!(fusion != 0 && fusion != reader && fusion != std::process::id() as i32);
}

/// A stalled fusion must never block the USB reader. Losses are counted, not silent.
#[test]
fn pipeline_full_ring_counts_overruns_and_never_blocks_the_reader() {
    struct Stalled {
        go: Arc<AtomicBool>,
    }
    impl Sink<Numbered> for Stalled {
        fn publish(&mut self, _: &Numbered) {
            while !self.go.load(Ordering::Acquire) {
                thread::yield_now();
            }
        }
    }
    let go = Arc::new(AtomicBool::new(false));
    let p = Pipeline::<_, _, 8>::start(
        CountingSource::new(0, 100),
        Stalled {
            go: Arc::clone(&go),
        },
        &Options::default(),
    )
    .unwrap();
    wait_for(|| p.stats().items_read >= 100);
    assert_eq!(p.stats().items_read, 100); // the reader finished although nothing was consumed
    assert!(p.reader_done());
    go.store(true, Ordering::Release);
    let (_, _, s) = p.stop();
    assert!(s.ring_overruns > 0);
    assert_eq!(s.items_published + s.ring_overruns, 100);
}

#[test]
fn pipeline_idle_source_is_stoppable() {
    struct Idle;
    impl Source for Idle {
        type Item = Numbered;
        fn next(&mut self, _: &mut Numbered) -> SourceStatus {
            thread::sleep(Duration::from_millis(1));
            SourceStatus::Idle
        }
    }
    let p: Pipeline<_, _> =
        Pipeline::start(Idle, OrderSink::default(), &Options::default()).unwrap();
    let (_, _, s) = p.stop();
    assert_eq!(s.items_read, 0);
}

#[test]
fn pipeline_reports_its_thread_policies() {
    let mut o = Options::default();
    o.reader.realtime = false;
    o.fusion.realtime = false;
    let p: Pipeline<_, _> =
        Pipeline::start(CountingSource::new(0, 1), OrderSink::default(), &o).unwrap();
    assert_eq!(p.reader_policy().sched, SchedClass::Normal);
    assert_eq!(p.fusion_policy().sched, SchedClass::Normal);
    assert!(p.reader_tid() != 0 && p.fusion_tid() != 0);
    p.stop();
}

#[test]
fn pipeline_restarts_after_stop() {
    let sink = OrderSink::default();
    let seen = Arc::clone(&sink.state);
    let p: Pipeline<_, _> =
        Pipeline::start(CountingSource::new(0, 10), sink, &Options::default()).unwrap();
    wait_for(|| seen.seen.load(Ordering::Acquire) >= 10);
    let (_, sink, _) = p.stop();
    assert_eq!(seen.seen.load(Ordering::Acquire), 10);
    let p: Pipeline<_, _> =
        Pipeline::start(CountingSource::new(10, 20), sink, &Options::default()).unwrap();
    wait_for(|| seen.seen.load(Ordering::Acquire) >= 20);
    p.stop();
    assert_eq!(seen.seen.load(Ordering::Acquire), 20);
    assert!(!seen.out_of_order.load(Ordering::Relaxed));
}

/// Dropping a running pipeline stops it rather than leaving threads behind.
#[test]
fn pipeline_drop_stops_the_threads() {
    struct Idle(Arc<AtomicBool>);
    impl Source for Idle {
        type Item = Numbered;
        fn next(&mut self, _: &mut Numbered) -> SourceStatus {
            thread::sleep(Duration::from_millis(1));
            SourceStatus::Idle
        }
    }
    impl Drop for Idle {
        fn drop(&mut self) {
            self.0.store(true, Ordering::SeqCst);
        }
    }
    let dropped = Arc::new(AtomicBool::new(false));
    let p: Pipeline<_, _> = Pipeline::start(
        Idle(Arc::clone(&dropped)),
        OrderSink::default(),
        &Options::default(),
    )
    .unwrap();
    drop(p);
    assert!(dropped.load(Ordering::SeqCst)); // the reader was joined and gave its source back
}
