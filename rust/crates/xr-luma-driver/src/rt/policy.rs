//! Thread scheduling and memory locking for the hot threads. Never fails: tracking without
//! privileges is worse tracking, not no tracking.

use std::io;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SchedClass {
    Fifo,
    Nice,
    #[default]
    Normal,
}

#[derive(Debug, Clone, Copy)]
pub struct ThreadPolicy {
    /// false: leave the thread as it is.
    pub realtime: bool,
    /// Above every normal task, below the kernel's interrupt threads (50), so USB itself is never
    /// starved. The limits file grants exactly 20; a lower RLIMIT_RTPRIO lowers it to match.
    pub fifo_priority: i32,
    /// When real-time is not permitted.
    pub fallback_nice: i32,
    /// Pin to this CPU; `None` lets the scheduler choose.
    pub cpu: Option<usize>,
    /// Pin to this CPU only once SCHED_FIFO is granted: a pinned normal thread is the worst case
    /// measured.
    pub fifo_cpu: Option<usize>,
    /// Without real-time, ask for the shortest time slice ([`SHORT_SLICE_NS`]).
    pub short_slice: bool,
}

impl Default for ThreadPolicy {
    fn default() -> Self {
        Self {
            realtime: true,
            fifo_priority: 20,
            fallback_nice: -10,
            cpu: None,
            fifo_cpu: None,
            short_slice: true,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct ThreadPolicyResult {
    pub sched: SchedClass,
    /// The FIFO priority granted.
    pub priority: i32,
    /// The nice value granted.
    pub nice: i32,
    /// Pinned to this CPU.
    pub cpu: Option<usize>,
    /// The kernel accepted the short slice.
    pub short_slice: bool,
    /// Real-time was wanted and refused: show the one-time setup once.
    pub needs_setup: bool,
    /// One line for the log; empty when everything asked for was granted.
    pub detail: String,
}

/// A FIFO thread that stops blocking gets SIGXCPU after this much CPU, so a bug cannot freeze a
/// core until reboot. The hot path blocks about 1000 times a second, so only a real hang ever gets
/// near it.
pub const RT_TIME_GUARD_US: u64 = 200_000;

/// The guard never relaxes a tighter RLIMIT_RTTIME it inherits, but below this it refuses FIFO
/// instead. The kernel kills once a FIFO thread is caught running by more ticks than the limit
/// allows; with a limit under one tick (10 ms at HZ=100), the first tick that lands while it runs
/// is a SIGKILL. A sandbox that hands down RLIMIT_RTTIME 0 makes that random death on a busy
/// machine.
pub const RT_TIME_FLOOR_US: u64 = 10_000;

/// Since Linux 6.12 a normal thread may ask for a shorter EEVDF time slice, which brings its
/// deadline forward so it runs sooner after waking. No privilege, no limit. 100 us is the shortest
/// the kernel takes.
pub const SHORT_SLICE_NS: u64 = 100_000;

pub const MEMLOCK_WANTED_BYTES: u64 = 128 * 1024 * 1024;

/// The system calls behind [`apply_thread_policy`], separated so tests can play a machine that
/// permits what this one does not. Each returns `Err(errno)`.
pub trait SysOps {
    /// May lower `priority` to what RLIMIT_RTPRIO allows.
    fn set_fifo(&self, priority: &mut i32) -> Result<(), i32>;
    fn set_nice(&self, nice: i32) -> Result<(), i32>;
    fn set_affinity(&self, cpu: usize) -> Result<(), i32>;
    /// `Err(ERANGE)`, changing nothing: the inherited limit is under [`RT_TIME_FLOOR_US`].
    fn arm_rttime_guard(&self, us: u64) -> Result<(), i32>;
    fn set_short_slice(&self, ns: u64) -> Result<(), i32>;
}

/// The calling thread, on this kernel.
pub struct RealSysOps;

fn errno() -> i32 {
    io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(libc::EIO)
}
fn check(rc: libc::c_int) -> Result<(), i32> {
    if rc == 0 { Ok(()) } else { Err(errno()) }
}
fn errno_text(e: i32) -> String {
    let full = io::Error::from_raw_os_error(e).to_string();
    // "Operation not permitted (os error 1)" -> the C++ driver's strerror text.
    match full.rfind(" (os error") {
        Some(at) => full[..at].to_string(),
        None => full,
    }
}

fn getrlimit(resource: libc::__rlimit_resource_t) -> Option<libc::rlimit> {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: `limit` is a valid, writable rlimit.
    (unsafe { libc::getrlimit(resource, &mut limit) } == 0).then_some(limit)
}

/// Soft limits may be raised to the hard limit without privilege, and that is the whole trick:
/// unlike a capability, which is checked against the initial user namespace and so means nothing
/// inside tracking.py's `unshare --user`, RLIMIT_RTPRIO and RLIMIT_NICE work from inside the
/// namespace.
fn raise_soft_limit(resource: libc::__rlimit_resource_t, wanted: libc::rlim_t) {
    let Some(mut limit) = getrlimit(resource) else {
        return;
    };
    if limit.rlim_cur >= wanted {
        return;
    }
    limit.rlim_cur = wanted.min(limit.rlim_max);
    // SAFETY: `limit` is a valid rlimit.
    unsafe { libc::setrlimit(resource, &limit) };
}

impl SysOps for RealSysOps {
    fn set_fifo(&self, priority: &mut i32) -> Result<(), i32> {
        raise_soft_limit(libc::RLIMIT_RTPRIO, *priority as libc::rlim_t);
        if let Some(limit) = getrlimit(libc::RLIMIT_RTPRIO)
            && limit.rlim_cur != libc::RLIM_INFINITY
            && limit.rlim_cur >= 1
            && *priority as libc::rlim_t > limit.rlim_cur
        {
            *priority = limit.rlim_cur as i32;
        }
        let param = libc::sched_param {
            sched_priority: *priority,
        };
        // Reset on fork: nothing this process ever starts inherits real-time.
        // SAFETY: `param` is a valid sched_param; 0 is the calling thread.
        check(unsafe {
            libc::sched_setscheduler(0, libc::SCHED_FIFO | libc::SCHED_RESET_ON_FORK, &param)
        })
    }

    fn set_nice(&self, nice: i32) -> Result<(), i32> {
        raise_soft_limit(libc::RLIMIT_NICE, (20 - nice) as libc::rlim_t); // the kernel's encoding: 20 - nice
        // On Linux PRIO_PROCESS with 0 is the calling thread alone.
        // SAFETY: plain system call on the calling thread.
        check(unsafe { libc::setpriority(libc::PRIO_PROCESS, 0, nice) })
    }

    fn set_affinity(&self, cpu: usize) -> Result<(), i32> {
        if cpu >= libc::CPU_SETSIZE as usize {
            return Err(libc::EINVAL);
        }
        // SAFETY: cpu_set_t is plain bits; zeroed is the empty set and `cpu` is in range.
        unsafe {
            let mut set: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_SET(cpu, &mut set);
            check(libc::sched_setaffinity(
                0,
                std::mem::size_of::<libc::cpu_set_t>(),
                &set,
            ))
        }
    }

    fn arm_rttime_guard(&self, us: u64) -> Result<(), i32> {
        let mut limit = getrlimit(libc::RLIMIT_RTTIME).ok_or_else(errno)?;
        limit.rlim_cur = limit.rlim_cur.min(us);
        limit.rlim_max = limit.rlim_max.min(us * 5); // the soft limit's SIGXCPU first, SIGKILL if ignored
        if limit.rlim_cur < RT_TIME_FLOOR_US {
            return Err(libc::ERANGE);
        }
        // SAFETY: `limit` is a valid rlimit.
        check(unsafe { libc::setrlimit(libc::RLIMIT_RTTIME, &limit) })
    }

    fn set_short_slice(&self, ns: u64) -> Result<(), i32> {
        // The kernel's struct sched_attr; sched_runtime on SCHED_OTHER is the requested slice.
        // nice is part of the call, so keep it.
        #[repr(C)]
        #[derive(Default)]
        struct SchedAttr {
            size: u32,
            policy: u32,
            flags: u64,
            nice: i32,
            priority: u32,
            runtime: u64,
            deadline: u64,
            period: u64,
        }
        let attr = SchedAttr {
            size: std::mem::size_of::<SchedAttr>() as u32,
            policy: libc::SCHED_OTHER as u32,
            // SAFETY: plain system call on the calling thread.
            nice: unsafe { libc::getpriority(libc::PRIO_PROCESS, 0) },
            runtime: ns,
            ..Default::default()
        };
        // SAFETY: `attr` is a valid sched_attr of the size it states; 0 is the calling thread.
        let rc = unsafe { libc::syscall(libc::SYS_sched_setattr, 0, &attr as *const SchedAttr, 0) };
        if rc == 0 { Ok(()) } else { Err(errno()) }
    }
}

fn add_detail(detail: &mut String, line: &str) {
    if !detail.is_empty() {
        detail.push_str("; ");
    }
    detail.push_str(line);
}

/// Applies to the calling thread. Order of preference: SCHED_FIFO, nice, nothing. Never asks for
/// FIFO without the RLIMIT_RTTIME guard in place. Not hot.
pub fn apply_thread_policy(policy: &ThreadPolicy, ops: &dyn SysOps) -> ThreadPolicyResult {
    let mut result = ThreadPolicyResult::default();
    // A normal thread's timed sleeps otherwise end up to 50 us late.
    // SAFETY: plain prctl on the calling thread.
    unsafe { libc::prctl(libc::PR_SET_TIMERSLACK, 1 as libc::c_ulong) };
    if let Some(cpu) = policy.cpu {
        match ops.set_affinity(cpu) {
            Ok(()) => result.cpu = Some(cpu),
            Err(e) => add_detail(
                &mut result.detail,
                &format!("not pinned to cpu {cpu}: {}", errno_text(e)),
            ),
        }
    }
    if !policy.realtime {
        return result;
    }
    match ops.arm_rttime_guard(RT_TIME_GUARD_US) {
        Err(libc::ERANGE) => add_detail(
            &mut result.detail,
            &format!(
                "no SCHED_FIFO: the inherited RLIMIT_RTTIME is under {} ms, so the kernel would kill a FIFO thread at random",
                RT_TIME_FLOOR_US / 1000
            ),
        ),
        Err(e) => add_detail(
            &mut result.detail,
            &format!("no SCHED_FIFO: cannot arm RLIMIT_RTTIME: {}", errno_text(e)),
        ),
        Ok(()) => {
            let mut priority = policy.fifo_priority;
            match ops.set_fifo(&mut priority) {
                Ok(()) => {
                    result.sched = SchedClass::Fifo;
                    result.priority = priority;
                    if let (Some(cpu), None) = (policy.fifo_cpu, result.cpu) {
                        match ops.set_affinity(cpu) {
                            Ok(()) => result.cpu = Some(cpu),
                            Err(e) => add_detail(
                                &mut result.detail,
                                &format!("not pinned to cpu {cpu}: {}", errno_text(e)),
                            ),
                        }
                    }
                    return result;
                }
                Err(e) => add_detail(
                    &mut result.detail,
                    &format!("no SCHED_FIFO: {}", errno_text(e)),
                ),
            }
        }
    }
    result.needs_setup = true;
    match ops.set_nice(policy.fallback_nice) {
        Ok(()) => {
            result.sched = SchedClass::Nice;
            result.nice = policy.fallback_nice;
        }
        Err(e) => add_detail(
            &mut result.detail,
            &format!("no nice {}: {}", policy.fallback_nice, errno_text(e)),
        ),
    }
    if policy.short_slice {
        match ops.set_short_slice(SHORT_SLICE_NS) {
            Ok(()) => result.short_slice = true,
            Err(e) => add_detail(
                &mut result.detail,
                &format!("no short slice: {}", errno_text(e)),
            ),
        }
    }
    result
}

/// One line for the log: what was granted, as key=value pairs (`class=fifo priority=20 cpu=3`).
pub fn describe(r: &ThreadPolicyResult) -> String {
    let mut out = match r.sched {
        SchedClass::Fifo => format!("class=fifo priority={}", r.priority),
        SchedClass::Nice => format!("class=nice nice={}", r.nice),
        SchedClass::Normal => "class=normal".to_owned(),
    };
    if r.sched != SchedClass::Fifo {
        out += if r.short_slice {
            " short_slice=yes"
        } else {
            " short_slice=no"
        };
    }
    match r.cpu {
        Some(cpu) => out += &format!(" cpu={cpu}"),
        None => out += " cpu=any",
    }
    out
}

/// `mlockall(MCL_FUTURE)` makes later mmaps fail once the limit is reached, so it is only safe
/// when the limit is out of reach.
pub fn memlock_is_safe(rlimit_memlock: u64) -> bool {
    rlimit_memlock >= MEMLOCK_WANTED_BYTES
}

#[derive(Debug, Clone, Default)]
pub struct MemoryLockResult {
    pub locked: bool,
    pub detail: String,
}

/// Keeps the process out of swap, so a page fault cannot stall the hot path. Call once at startup.
/// Declines rather than risk [`memlock_is_safe`]'s failure mode.
pub fn lock_memory() -> MemoryLockResult {
    let declined = |detail: String| MemoryLockResult {
        locked: false,
        detail,
    };
    raise_soft_limit(libc::RLIMIT_MEMLOCK, MEMLOCK_WANTED_BYTES);
    let Some(limit) = getrlimit(libc::RLIMIT_MEMLOCK) else {
        return declined(format!("memory not locked: {}", errno_text(errno())));
    };
    if !memlock_is_safe(limit.rlim_cur) {
        return declined(format!(
            "memory not locked: RLIMIT_MEMLOCK is {} KiB (stacks are still prefaulted)",
            limit.rlim_cur / 1024
        ));
    }
    // SAFETY: plain system call; the limit check above keeps MCL_FUTURE from failing later mmaps.
    if unsafe { libc::mlockall(libc::MCL_CURRENT | libc::MCL_FUTURE) } != 0 {
        return declined(format!("memory not locked: {}", errno_text(errno())));
    }
    MemoryLockResult {
        locked: true,
        detail: String::new(),
    }
}

/// Touches this much of the stack now, so the hot path takes no faults growing it. `BYTES` is a
/// constant so the frame is a fixed size.
#[inline(never)]
pub fn prefault_stack<const BYTES: usize>() {
    let mut stack = [0u8; BYTES];
    for i in (0..BYTES).step_by(4096) {
        // SAFETY: `i` is in bounds; volatile so the writes are not optimised away.
        unsafe { std::ptr::write_volatile(stack.as_mut_ptr().add(i), 0) };
    }
    std::hint::black_box(&stack);
}
