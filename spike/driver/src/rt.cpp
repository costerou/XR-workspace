#include "luma/rt.hpp"
#include <algorithm>
#include <cmath>
#include <fcntl.h>
#include <linux/futex.h>
#include <sched.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>

namespace luma::rt {
namespace {
std::uint64_t ns_of(clockid_t clock) noexcept {
    timespec ts{};
    clock_gettime(clock, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
std::string errno_text(int e) { return std::strerror(e); }

// Soft limits may be raised to the hard limit without privilege, and that is the whole trick: unlike a capability, which is
// checked against the initial user namespace and so means nothing inside tracking.py's `unshare --user`, RLIMIT_RTPRIO and
// RLIMIT_NICE work from inside the namespace.
void raise_soft_limit(int resource, rlim_t wanted) noexcept {
    rlimit limit{};
    if (getrlimit(resource, &limit) != 0 || limit.rlim_cur >= wanted) return;
    limit.rlim_cur = std::min(wanted, limit.rlim_max);
    setrlimit(resource, &limit);
}
struct RealOps final : SysOps {
    int set_fifo(int& priority) const override {
        raise_soft_limit(RLIMIT_RTPRIO, rlim_t(priority));
        rlimit limit{};
        if (getrlimit(RLIMIT_RTPRIO, &limit) == 0 && limit.rlim_cur != RLIM_INFINITY && limit.rlim_cur >= 1 && rlim_t(priority) > limit.rlim_cur)
            priority = int(limit.rlim_cur);
        sched_param param{};
        param.sched_priority = priority;
        // Reset on fork: nothing this process ever starts inherits real-time.
        return sched_setscheduler(0, SCHED_FIFO | SCHED_RESET_ON_FORK, &param) == 0 ? 0 : errno;
    }
    int set_nice(int nice) const override {
        raise_soft_limit(RLIMIT_NICE, rlim_t(20 - nice));  // the kernel's encoding: 20 - nice
        // On Linux PRIO_PROCESS with 0 is the calling thread alone.
        return setpriority(PRIO_PROCESS, 0, nice) == 0 ? 0 : errno;
    }
    int set_affinity(int cpu) const override {
        if (cpu < 0 || cpu >= CPU_SETSIZE) return EINVAL;
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(cpu, &set);
        return sched_setaffinity(0, sizeof set, &set) == 0 ? 0 : errno;
    }
    int arm_rttime_guard(std::uint64_t us) const override {
        rlimit limit{};
        if (getrlimit(RLIMIT_RTTIME, &limit) != 0) return errno;
        limit.rlim_cur = std::min<rlim_t>(limit.rlim_cur, rlim_t(us));
        limit.rlim_max = std::min<rlim_t>(limit.rlim_max, rlim_t(us) * 5);  // the soft limit's SIGXCPU first, SIGKILL if ignored
        return setrlimit(RLIMIT_RTTIME, &limit) == 0 ? 0 : errno;
    }
    int set_short_slice(std::uint64_t ns) const override {
        // The kernel's struct sched_attr; sched_runtime on SCHED_OTHER is the requested slice. nice is part of the call, so keep it.
        struct { std::uint32_t size, policy; std::uint64_t flags; std::int32_t nice; std::uint32_t priority; std::uint64_t runtime, deadline, period; } attr{};
        attr.size = sizeof attr;
        attr.policy = SCHED_OTHER;
        errno = 0;
        attr.nice = getpriority(PRIO_PROCESS, 0);
        attr.runtime = ns;
        return syscall(SYS_sched_setattr, 0, &attr, 0) == 0 ? 0 : errno;
    }
};
void add_detail(std::string& detail, const std::string& line) {
    if (!detail.empty()) detail += "; ";
    detail += line;
}
}  // namespace

std::int64_t raw_to_monotonic_offset_ns() noexcept {
    // The tightest of a few raw readings between two monotonic ones: a preempted attempt just loses.
    std::uint64_t best = ~std::uint64_t{0};
    std::int64_t offset = 0;
    for (int i = 0; i < 16; ++i) {
        const std::uint64_t before = ns_of(CLOCK_MONOTONIC), raw = ns_of(CLOCK_MONOTONIC_RAW), after = ns_of(CLOCK_MONOTONIC);
        if (after - before < best) {
            best = after - before;
            offset = std::int64_t(before + (after - before) / 2) - std::int64_t(raw);
        }
    }
    return offset;
}

const SysOps& real_sys_ops() noexcept {
    static const RealOps ops;
    return ops;
}

ThreadPolicyResult apply_thread_policy(const ThreadPolicy& policy, const SysOps& ops) {
    ThreadPolicyResult result;
    prctl(PR_SET_TIMERSLACK, 1UL);  // a normal thread's timed sleeps otherwise end up to 50 us late
    if (policy.cpu >= 0) {
        if (const int rc = ops.set_affinity(policy.cpu); rc == 0) result.cpu = policy.cpu;
        else add_detail(result.detail, "not pinned to cpu " + std::to_string(policy.cpu) + ": " + errno_text(rc));
    }
    if (!policy.realtime) return result;
    if (const int guard = ops.arm_rttime_guard(kRtTimeGuardUs); guard != 0) {
        add_detail(result.detail, "no SCHED_FIFO: cannot arm RLIMIT_RTTIME: " + errno_text(guard));
    } else {
        int priority = policy.fifo_priority;
        if (const int rc = ops.set_fifo(priority); rc == 0) {
            result.sched = SchedClass::fifo;
            result.priority = priority;
            return result;
        } else {
            add_detail(result.detail, "no SCHED_FIFO: " + errno_text(rc));
        }
    }
    result.needs_setup = true;
    if (const int rc = ops.set_nice(policy.fallback_nice); rc == 0) {
        result.sched = SchedClass::nice;
        result.nice = policy.fallback_nice;
    } else {
        add_detail(result.detail, "no nice " + std::to_string(policy.fallback_nice) + ": " + errno_text(rc));
    }
    if (policy.short_slice) {
        if (const int rc = ops.set_short_slice(kShortSliceNs); rc == 0) result.short_slice = true;
        else add_detail(result.detail, "no short slice: " + errno_text(rc));
    }
    return result;
}

std::string rt_setup_commands(const std::string& user) {
    return "sudo tee /etc/security/limits.d/90-xr-luma-driver.conf >/dev/null <<'EOF'\n"
           + user + " - rtprio 20\n"
           + user + " - nice -10\n"
           + user + " - memlock " + std::to_string(kMemlockWantedBytes / 1024) + "\n"
           + "EOF\n"
             "# Then reboot: the limits reach services through the systemd user manager, which outlives a log-out when lingering is on.\n"
             "# Check afterwards. This must print 20, 30 (the kernel's way of writing nice -10) and 131072:\n"
             "systemd-run --user --wait --pipe -q sh -c 'ulimit -Hr; ulimit -He; ulimit -Hl'\n";
}

bool memlock_is_safe(std::uint64_t rlimit_memlock) noexcept { return rlimit_memlock >= kMemlockWantedBytes; }

MemoryLockResult lock_memory() {
    rlimit limit{};
    if (getrlimit(RLIMIT_MEMLOCK, &limit) != 0) return {false, "memory not locked: " + errno_text(errno)};
    raise_soft_limit(RLIMIT_MEMLOCK, rlim_t(kMemlockWantedBytes));
    if (getrlimit(RLIMIT_MEMLOCK, &limit) != 0) return {false, "memory not locked: " + errno_text(errno)};
    if (!memlock_is_safe(limit.rlim_cur))
        return {false, "memory not locked: RLIMIT_MEMLOCK is " + std::to_string(limit.rlim_cur / 1024) + " KiB (stacks are still prefaulted)"};
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) return {false, "memory not locked: " + errno_text(errno)};
    return {true, {}};
}

__attribute__((noinline)) void prefault_stack(std::size_t bytes) noexcept {
    auto* stack = static_cast<volatile char*>(__builtin_alloca(bytes));
    for (std::size_t i = 0; i < bytes; i += 4096) stack[i] = 0;
}

void futex_wait(std::atomic<std::uint32_t>* word, std::uint32_t expected, int timeout_ms) noexcept {
    static_assert(sizeof(std::atomic<std::uint32_t>) == sizeof(std::uint32_t) && std::atomic<std::uint32_t>::is_always_lock_free);
    const timespec timeout{time_t(timeout_ms / 1000), long(timeout_ms % 1000) * 1'000'000};
    syscall(SYS_futex, reinterpret_cast<std::uint32_t*>(word), FUTEX_WAIT_PRIVATE, expected, &timeout, nullptr, 0);
}
void futex_wake_one(std::atomic<std::uint32_t>* word) noexcept {
    syscall(SYS_futex, reinterpret_cast<std::uint32_t*>(word), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
}

namespace {
constexpr std::uint64_t kShmMagic = 0x315453524D554CULL;  // "LUMRST1": changes with the layout of the header
constexpr std::uint32_t kShmVersion = 1;
struct ShmHeader {
    std::atomic<std::uint64_t> magic;  // written last: an object without it was never finished
    std::uint32_t version;
    std::uint32_t payload_bytes;
};
static_assert(sizeof(ShmHeader) == 16 && sizeof(ShmHeader) <= ShmMapping::kSlotOffset);

struct HeaderBytes { std::uint64_t magic; std::uint32_t version, payload_bytes; };
bool header_matches(int fd, std::uint32_t payload_bytes) {
    HeaderBytes h{};
    return pread(fd, &h, sizeof h, 0) == ssize_t(sizeof h) && h.magic == kShmMagic && h.version == kShmVersion && h.payload_bytes == payload_bytes;
}
// Not somebody else's file planted under our name, and not a directory or a symlink target of another kind.
bool object_is_ours(int fd, off_t& size, std::string& why) {
    struct stat st{};
    if (fstat(fd, &st) != 0) { why = "shm stat: " + errno_text(errno); return false; }
    if (!S_ISREG(st.st_mode) || st.st_uid != geteuid()) { why = "shm object is not this user's"; return false; }
    size = st.st_size;
    return true;
}
}  // namespace

std::optional<ShmMapping> ShmMapping::writer(const std::string& name, std::uint32_t payload_bytes, std::size_t total, bool& fresh, std::string& why) {
    int fd = shm_open(name.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd < 0) { why = "shm_open " + name + ": " + errno_text(errno); return std::nullopt; }
    const auto fail = [&](std::string text) { why = std::move(text); close(fd); return std::nullopt; };
    off_t size = 0;
    if (!object_is_ours(fd, size, why)) { close(fd); return std::nullopt; }
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) return fail(name + " already has a writer");
    fresh = false;
    if (size != off_t(total) || !header_matches(fd, payload_bytes)) {
        if (size != 0) {
            // Another layout (or an unfinished one): a new object, so that readers still mapped to the old one see its last
            // value go stale instead of a SIGBUS or garbage.
            shm_unlink(name.c_str());
            close(fd);
            fd = shm_open(name.c_str(), O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (fd < 0) { why = "shm_open " + name + ": " + errno_text(errno); return std::nullopt; }
            if (flock(fd, LOCK_EX | LOCK_NB) != 0) return fail(name + " already has a writer");
        }
        if (ftruncate(fd, off_t(total)) != 0) return fail("shm size: " + errno_text(errno));
        fresh = true;
    }
    void* addr = mmap(nullptr, total, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd, 0);
    if (addr == MAP_FAILED) return fail("shm map: " + errno_text(errno));
    mlock(addr, total);  // best effort, and small enough for the default limit
    return ShmMapping(addr, total, fd);
}
std::optional<ShmMapping> ShmMapping::reader(const std::string& name, std::uint32_t payload_bytes, std::size_t total, std::string& why) {
    const int fd = shm_open(name.c_str(), O_RDONLY | O_CLOEXEC, 0);
    if (fd < 0) { why = "shm_open " + name + ": " + errno_text(errno); return std::nullopt; }
    const auto fail = [&](std::string text) { why = std::move(text); close(fd); return std::nullopt; };
    off_t size = 0;
    if (!object_is_ours(fd, size, why)) { close(fd); return std::nullopt; }
    if (size != off_t(total) || !header_matches(fd, payload_bytes)) return fail(name + " has another layout");
    void* addr = mmap(nullptr, total, PROT_READ, MAP_SHARED | MAP_POPULATE, fd, 0);
    if (addr == MAP_FAILED) return fail("shm map: " + errno_text(errno));
    close(fd);
    return ShmMapping(addr, total, -1);
}
ShmMapping::~ShmMapping() {
    if (addr_) munmap(addr_, size_);
    if (fd_ >= 0) close(fd_);  // and with it the writer's lock
}
void ShmMapping::mark_ready(std::uint32_t payload_bytes) noexcept {
    auto* header = new (addr_) ShmHeader{{0}, kShmVersion, payload_bytes};
    header->magic.store(kShmMagic, std::memory_order_release);
}

double LatencyHistogram::mean() const noexcept { return count_ ? sum_ / double(count_) : 0; }
double LatencyHistogram::stddev() const noexcept {
    if (!count_) return 0;
    const double m = mean();
    return std::sqrt(std::max(0.0, sum_sq_ / double(count_) - m * m));
}
std::uint64_t LatencyHistogram::bucket_top(std::size_t i) noexcept {
    if (i < kLinear) return i;
    const std::size_t octave = (i - kLinear) / kSub, sub = (i - kLinear) % kSub;
    const unsigned msb = unsigned(octave) + 6;
    return ((std::uint64_t(sub + kSub) + 1) << (msb - 5)) - 1;  // the very top bucket wraps to all ones, which is right
}
std::uint64_t LatencyHistogram::percentile(double p) const noexcept {
    if (!count_) return 0;
    const auto target = std::max<std::uint64_t>(1, std::uint64_t(std::ceil(double(count_) * p / 100.0)));
    std::uint64_t seen = 0;
    for (std::size_t i = 0; i < kBuckets; ++i) {
        seen += buckets_[i];
        if (seen >= target) return std::min(bucket_top(i), max_);
    }
    return max_;
}

bool spawn_thread(pthread_t& out, void (*entry)(void*), void* arg, const char* name, std::string& why) {
    struct Start { void (*entry)(void*); void* arg; char name[16]; };
    auto* start = new Start{entry, arg, {}};
    std::strncpy(start->name, name, sizeof start->name - 1);
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, kRtStackBytes);
    const int rc = pthread_create(&out, &attr, [](void* p) -> void* {
        const Start s = *static_cast<Start*>(p);
        delete static_cast<Start*>(p);
        pthread_setname_np(pthread_self(), s.name);
        s.entry(s.arg);
        return nullptr;
    }, start);
    pthread_attr_destroy(&attr);
    if (rc != 0) {
        delete start;
        why = std::string("cannot start ") + name + ": " + errno_text(rc);
        return false;
    }
    return true;
}
}  // namespace luma::rt
