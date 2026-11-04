#pragma once
// The real-time layer of the Luma driver: how a sensor packet gets from the kernel to a published pose as fast as the machine
// allows, without allocating, without blocking the reader and without a lock a reader could hold up. docs/performance.md has
// the numbers and the reasoning. Nothing here knows about USB or the protocol; main.cpp plugs those in as a Source and a Sink.
//
// Rules for everything marked "hot": no heap, no locks, no logging, no system call but the ones named. bench/rt_test.cpp and
// bench/rt_bench.cpp prove the heap part by counting every malloc in the process.
#include <array>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <new>
#include <optional>
#include <pthread.h>
#include <string>
#include <sys/types.h>
#include <type_traits>
#include <unistd.h>
#include <utility>

namespace luma::rt {
inline constexpr std::size_t kCacheLine = 64;

// CLOCK_MONOTONIC_RAW: the hardware counter, never slewed by NTP, so intervals between sensor samples are the true ones. It is a
// vDSO call (no system call, about 20 ns) wherever the clocksource is the TSC. Hot.
inline std::uint64_t monotonic_raw_ns() noexcept {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
// Add this to a raw time to get CLOCK_MONOTONIC, the clock the renderer runs on. They drift apart slowly, so take it again now
// and then (not hot: tens of ns, but a daemon has no need to do it per sample).
std::int64_t raw_to_monotonic_offset_ns() noexcept;

// --- Thread scheduling -----------------------------------------------------------------------------------------------------
enum class SchedClass { fifo, nice, normal };

struct ThreadPolicy {
    bool realtime = true;     // false: leave the thread as it is
    int fifo_priority = 10;   // above every normal task, below the kernel's interrupt threads (50), so USB itself is never starved
    int fallback_nice = -10;  // when real-time is not permitted
    int cpu = -1;             // pin to this CPU, -1 to let the scheduler choose
    bool short_slice = true;  // without real-time, ask for the shortest time slice (below)
};
struct ThreadPolicyResult {
    SchedClass sched = SchedClass::normal;
    int priority = 0;          // the FIFO priority granted
    int nice = 0;              // the nice value granted
    int cpu = -1;              // pinned to this CPU, -1 if not pinned
    bool short_slice = false;  // the kernel accepted the short slice
    bool needs_setup = false;  // real-time was wanted and refused: show rt_setup_commands() once
    std::string detail;        // one line for the log; empty when everything asked for was granted
};
// A FIFO thread that stops blocking gets SIGXCPU after this much CPU, so a bug cannot freeze a core until reboot. The hot path
// blocks about 1000 times a second, so only a real hang ever gets near it.
inline constexpr std::uint64_t kRtTimeGuardUs = 200'000;

// Since Linux 6.12 a normal thread may ask for a shorter EEVDF time slice, which brings its deadline forward so it runs sooner after
// waking. No privilege, no limit. 100 us is the shortest the kernel takes. It is what stands between the driver and a busy desktop
// until the real-time limits are installed: docs/performance.md has what it buys.
inline constexpr std::uint64_t kShortSliceNs = 100'000;

// The system calls behind apply_thread_policy, separated so tests can play a machine that permits what this one does not. Each
// returns 0 or an errno.
struct SysOps {
    virtual ~SysOps() = default;
    virtual int set_fifo(int& priority) const = 0;  // may lower priority to what RLIMIT_RTPRIO allows
    virtual int set_nice(int nice) const = 0;
    virtual int set_affinity(int cpu) const = 0;
    virtual int arm_rttime_guard(std::uint64_t us) const = 0;
    virtual int set_short_slice(std::uint64_t ns) const = 0;
};
const SysOps& real_sys_ops() noexcept;
// Applies to the calling thread. Order of preference: SCHED_FIFO, nice, nothing. Never fails: tracking without privileges is
// worse tracking, not no tracking. Never asks for FIFO without the RLIMIT_RTTIME guard in place.
ThreadPolicyResult apply_thread_policy(const ThreadPolicy& policy, const SysOps& ops = real_sys_ops());

// The exact one-time commands that permit SCHED_FIFO, nice and locked memory for `user`.
std::string rt_setup_commands(const std::string& user);

inline constexpr std::uint64_t kMemlockWantedBytes = 128ull * 1024 * 1024;
// mlockall(MCL_FUTURE) makes later mmaps fail once the limit is reached, so it is only safe when the limit is out of reach.
bool memlock_is_safe(std::uint64_t rlimit_memlock) noexcept;
struct MemoryLockResult { bool locked = false; std::string detail; };
// Keeps the process out of swap, so a page fault cannot stall the hot path. Call once at startup. Declines rather than risk
// memlock_is_safe's failure mode.
MemoryLockResult lock_memory();
void prefault_stack(std::size_t bytes) noexcept;  // touches the stack now, so the hot path takes no faults growing it

// --- Lock-free hand-over from the reader to fusion -----------------------------------------------------------------------------
// One producer, one consumer, a fixed array, no system calls. Hot. Each side keeps a private copy of the other's index, so the
// two cores only exchange a cache line when that copy has run out.
template <class T, std::size_t Capacity>
class SpscRing {
    static_assert(std::is_trivially_copyable_v<T>, "copied by assignment in the hot path, and must not allocate");
    static_assert(Capacity != 0 && (Capacity & (Capacity - 1)) == 0, "power of two");
public:
    bool try_push(const T& value) noexcept {
        const std::size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail - cached_head_ == Capacity) {
            cached_head_ = head_.load(std::memory_order_acquire);
            if (tail - cached_head_ == Capacity) return false;
        }
        slots_[tail & (Capacity - 1)] = value;
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }
    bool try_pop(T& out) noexcept {
        const std::size_t head = head_.load(std::memory_order_relaxed);
        if (head == cached_tail_) {
            cached_tail_ = tail_.load(std::memory_order_acquire);
            if (head == cached_tail_) return false;
        }
        out = slots_[head & (Capacity - 1)];
        head_.store(head + 1, std::memory_order_release);
        return true;
    }
    bool empty() const noexcept { return head_.load(std::memory_order_acquire) == tail_.load(std::memory_order_acquire); }
private:
    alignas(kCacheLine) std::atomic<std::size_t> tail_{0};  // producer's line
    std::size_t cached_head_ = 0;
    alignas(kCacheLine) std::atomic<std::size_t> head_{0};  // consumer's line
    std::size_t cached_tail_ = 0;
    alignas(kCacheLine) std::array<T, Capacity> slots_{};
};

void futex_wait(std::atomic<std::uint32_t>* word, std::uint32_t expected, int timeout_ms) noexcept;
void futex_wake_one(std::atomic<std::uint32_t>* word) noexcept;

// Sleep until told, for one waiter. A system call only when the other side really sleeps. A wake-up cannot be lost: both sides
// exchange state_ and whichever comes second sees the first; the waiter checks ready() after announcing itself, so an item pushed
// before that is seen by ready(), and a notify after it finds kParked and calls futex_wake.
class Parker {
public:
    void notify() noexcept {
        if (state_.exchange(kNotified, std::memory_order_acq_rel) == kParked) futex_wake_one(&state_);
    }
    template <class Ready>
    void wait(Ready&& ready, int timeout_ms) noexcept {
        if (state_.exchange(kParked, std::memory_order_acq_rel) != kNotified && !ready()) futex_wait(&state_, kParked, timeout_ms);
        state_.exchange(kIdle, std::memory_order_acq_rel);
    }
private:
    static constexpr std::uint32_t kIdle = 0, kParked = 1, kNotified = 2;
    alignas(kCacheLine) std::atomic<std::uint32_t> state_{kIdle};
};

// --- Publishing without a lock ---------------------------------------------------------------------------------------------
// One writer, any number of readers, in shared memory. The writer never waits for anyone: a reader that is preempted or killed
// mid-read holds nothing, so it cannot stall the real-time thread (a shared mutex can, and needs robust-mutex recovery besides).
// Readers retry on the rare collision. Every word is an atomic, so this is defined behaviour and TSan-clean: release on the word
// stores keeps the odd sequence ahead of them, acquire on the word loads keeps the second sequence read after them.
template <class T>
struct SeqlockSlot {
    static_assert(std::is_trivially_copyable_v<T>);
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free, "must work between processes");
    static constexpr std::size_t kWords = (sizeof(T) + 7) / 8;

    alignas(kCacheLine) std::atomic<std::uint64_t> seq{0};  // 0 never written, odd write in progress
    std::array<std::atomic<std::uint64_t>, kWords> words{};

    void store(const T& value) noexcept {  // Hot.
        std::array<std::uint64_t, kWords> raw{};
        std::memcpy(raw.data(), &value, sizeof(T));
        const std::uint64_t s = seq.load(std::memory_order_relaxed);
        seq.store(s + 1, std::memory_order_relaxed);
        for (std::size_t i = 0; i < kWords; ++i) words[i].store(raw[i], std::memory_order_release);
        seq.store(s + 2, std::memory_order_release);
    }
    // False if nothing was ever written, or the writer died mid-write (a sequence that stays odd). Readers never hang.
    bool load(T& out, unsigned max_tries = 4096) const noexcept {
        std::array<std::uint64_t, kWords> raw;
        for (unsigned attempt = 0; attempt < max_tries; ++attempt) {
            const std::uint64_t before = seq.load(std::memory_order_acquire);
            if (before == 0) return false;
            if (before & 1) { __builtin_ia32_pause(); continue; }
            for (std::size_t i = 0; i < kWords; ++i) raw[i] = words[i].load(std::memory_order_acquire);
            if (seq.load(std::memory_order_relaxed) == before) {
                std::memcpy(static_cast<void*>(&out), raw.data(), sizeof(T));
                return true;
            }
        }
        return false;
    }
};

// A POSIX shared memory object, /dev/shm/<name>, mode 0600. It works from inside tracking.py's network namespace, because that
// namespace does not hide the filesystem.
class ShmMapping {
public:
    static constexpr std::size_t kSlotOffset = 64;
    // Opens or creates, holding an exclusive lock for life: two drivers writing one seqlock would corrupt it, so the second is
    // refused. `fresh`: a new zeroed object that the caller must initialise and then mark_ready(). An object of another layout is
    // replaced, not reused.
    static std::optional<ShmMapping> writer(const std::string& name, std::uint32_t payload_bytes, std::size_t total, bool& fresh, std::string& why);
    static std::optional<ShmMapping> reader(const std::string& name, std::uint32_t payload_bytes, std::size_t total, std::string& why);
    ShmMapping(ShmMapping&& other) noexcept : addr_(std::exchange(other.addr_, nullptr)), size_(other.size_), fd_(std::exchange(other.fd_, -1)) {}
    ShmMapping& operator=(ShmMapping&&) = delete;
    ~ShmMapping();
    void mark_ready(std::uint32_t payload_bytes) noexcept;
    void* slot() const noexcept { return static_cast<char*>(addr_) + kSlotOffset; }
private:
    ShmMapping(void* addr, std::size_t size, int fd) noexcept : addr_(addr), size_(size), fd_(fd) {}
    void* addr_;
    std::size_t size_;
    int fd_;
};

template <class T>
class SeqlockWriter {
public:
    static std::optional<SeqlockWriter> create(const std::string& name, std::string& why) {
        bool fresh = false;
        auto mapping = ShmMapping::writer(name, sizeof(T), ShmMapping::kSlotOffset + sizeof(SeqlockSlot<T>), fresh, why);
        if (!mapping) return std::nullopt;
        auto* slot = static_cast<SeqlockSlot<T>*>(mapping->slot());
        if (fresh) {
            new (slot) SeqlockSlot<T>();
            mapping->mark_ready(sizeof(T));
        } else if (const auto s = slot->seq.load(std::memory_order_acquire); s & 1) {
            slot->seq.store(s + 1, std::memory_order_release);  // the last writer died mid-write
        }
        return SeqlockWriter(std::move(*mapping), slot);
    }
    void publish(const T& value) noexcept { slot_->store(value); }  // Hot.
private:
    SeqlockWriter(ShmMapping&& mapping, SeqlockSlot<T>* slot) noexcept : mapping_(std::move(mapping)), slot_(slot) {}
    ShmMapping mapping_;
    SeqlockSlot<T>* slot_;
};

template <class T>
class SeqlockReader {
public:
    static std::optional<SeqlockReader> open(const std::string& name, std::string& why) {
        auto mapping = ShmMapping::reader(name, sizeof(T), ShmMapping::kSlotOffset + sizeof(SeqlockSlot<T>), why);
        if (!mapping) return std::nullopt;
        auto* slot = static_cast<const SeqlockSlot<T>*>(mapping->slot());
        return SeqlockReader(std::move(*mapping), slot);
    }
    bool read(T& out) const noexcept { return slot_->load(out); }
private:
    SeqlockReader(ShmMapping&& mapping, const SeqlockSlot<T>* slot) noexcept : mapping_(std::move(mapping)), slot_(slot) {}
    ShmMapping mapping_;
    const SeqlockSlot<T>* slot_;
};

// The clean feed (a seqlock slot of these at /dev/shm/xr-luma-pose); the XRLinuxDriver-compatible one is publish.hpp's. A fixed wire
// format: bench/rt_test.cpp pins the offsets.
inline constexpr char kPoseShmName[] = "/xr-luma-pose";
struct PoseRecord {
    float w = 1, x = 0, y = 0, z = 0;
    std::uint64_t t_raw_ns = 0;        // sample time, CLOCK_MONOTONIC_RAW
    std::int64_t raw_to_mono_ns = 0;   // add to t_raw_ns for CLOCK_MONOTONIC
    std::uint32_t seq = 0;
    std::uint32_t flags = 0;
};
inline constexpr std::uint32_t kPoseCalibrated = 1u << 0;
static_assert(sizeof(PoseRecord) == 40 && std::is_trivially_copyable_v<PoseRecord>);

// --- Measuring ---------------------------------------------------------------------------------------------------------------
// Log-linear buckets, 3% resolution, exact min/max and mean, fixed size. record() is hot: a daemon can keep one running.
class LatencyHistogram {
public:
    void record(std::uint64_t ns) noexcept {
        ++buckets_[index(ns)];
        ++count_;
        const double v = double(ns);
        sum_ += v;
        sum_sq_ += v * v;
        if (ns < min_) min_ = ns;
        if (ns > max_) max_ = ns;
    }
    std::uint64_t count() const noexcept { return count_; }
    std::uint64_t min() const noexcept { return count_ ? min_ : 0; }
    std::uint64_t max() const noexcept { return max_; }
    double mean() const noexcept;
    double stddev() const noexcept;
    std::uint64_t percentile(double p) const noexcept;  // the top of the bucket holding it: rounds up, never flatters
private:
    static constexpr std::size_t kLinear = 64, kSub = 32, kBuckets = kLinear + (64 - 6) * kSub;
    static std::size_t index(std::uint64_t v) noexcept {
        if (v < kLinear) return std::size_t(v);
        const unsigned msb = 63u - unsigned(__builtin_clzll(v));
        return kLinear + (msb - 6) * kSub + std::size_t((v >> (msb - 5)) - kSub);
    }
    static std::uint64_t bucket_top(std::size_t i) noexcept;
    std::array<std::uint64_t, kBuckets> buckets_{};
    std::uint64_t count_ = 0, min_ = ~std::uint64_t{0}, max_ = 0;
    double sum_ = 0, sum_sq_ = 0;
};

// --- The pipeline ------------------------------------------------------------------------------------------------------------
enum class SourceStatus { item, idle, stop };
enum class PipelineMode {
    ring,     // reader thread -> SpscRing -> fusion thread. A slow sink can never delay the reader.
    inline_,  // the reader thread runs the sink itself: one thread, no hand-over, no second wake-up.
};
struct Options {
    PipelineMode mode = PipelineMode::ring;
    ThreadPolicy reader, fusion;
    int fusion_wait_ms = 100;  // how long fusion sleeps at most; only matters for noticing a stop
};
struct PipelineStats {
    std::uint64_t items_read = 0, items_published = 0;
    std::uint64_t ring_overruns = 0;  // items the ring had no room for: lost, and the reason to look at fusion
    std::uint64_t max_backlog = 0;    // most items fusion found waiting in one wake-up
    std::uint64_t fusion_sleeps = 0;
};
inline constexpr std::size_t kRtStackBytes = 512 * 1024, kRtStackPrefault = 256 * 1024;

// Starts a thread with a small stack and a name for ps. Not hot.
bool spawn_thread(pthread_t& out, void (*entry)(void*), void* arg, const char* name, std::string& why);

// Source: `SourceStatus next(Item&)` on the reader thread. Blocks in the transport with a timeout of its own (a stop waits for it)
// and stamps the item with monotonic_raw_ns() as soon as the read returns. idle: nothing yet. stop: the source is finished.
// Sink: `void operator()(const Item&) noexcept`, called once per item, in order, on the fusion thread (or the reader thread when
// inline_). It must not block: this is where fusion runs and the seqlock is written, so each sample is published at once; nothing
// here ever batches them. A stop publishes everything already read.
template <class Item, std::size_t Capacity = 256>
class Pipeline {
    static_assert(std::is_trivially_copyable_v<Item>);
public:
    Pipeline() = default;
    Pipeline(const Pipeline&) = delete;
    Pipeline& operator=(const Pipeline&) = delete;
    ~Pipeline() { stop(); }

    template <class Source, class Sink>
    bool start(Source& source, Sink& sink, const Options& options, std::string& why) {
        if (running_) { why = "already running"; return false; }
        options_ = options;
        source_ = &source;
        sink_ = &sink;
        stop_.store(false);
        reader_done_.store(false);
        ready_.store(0);
        read_.store(0); published_.store(0); overruns_.store(0); backlog_.store(0); sleeps_.store(0);
        reader_tid_.store(0); fusion_tid_.store(0);
        const bool ring = options.mode == PipelineMode::ring;
        if (ring && !spawn_thread(fusion_thread_, &Pipeline::fusion_main<Sink>, this, "luma-fusion", why)) return false;
        if (!spawn_thread(reader_thread_, &Pipeline::reader_main<Source, Sink>, this, "luma-reader", why)) {
            if (ring) { reader_done_.store(true); parker_.notify(); pthread_join(fusion_thread_, nullptr); }
            return false;
        }
        running_ = true;
        const int threads = ring ? 2 : 1;
        for (int seen = ready_.load(); seen < threads; seen = ready_.load()) ready_.wait(seen);
        return true;
    }
    void stop() {
        if (!running_) return;
        stop_.store(true, std::memory_order_release);
        pthread_join(reader_thread_, nullptr);
        if (options_.mode == PipelineMode::ring) pthread_join(fusion_thread_, nullptr);
        running_ = false;
    }
    PipelineStats stats() const noexcept {
        return {read_.load(std::memory_order_relaxed), published_.load(std::memory_order_relaxed), overruns_.load(std::memory_order_relaxed),
                backlog_.load(std::memory_order_relaxed), sleeps_.load(std::memory_order_relaxed)};
    }
    const ThreadPolicyResult& reader_policy() const noexcept { return reader_policy_; }
    const ThreadPolicyResult& fusion_policy() const noexcept { return fusion_policy_; }
    pid_t reader_tid() const noexcept { return reader_tid_.load(); }
    pid_t fusion_tid() const noexcept { return fusion_tid_.load(); }

private:
    static void bump(std::atomic<std::uint64_t>& counter) noexcept {  // one writer: no locked instruction needed
        counter.store(counter.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
    }
    void thread_ready() noexcept {
        ready_.fetch_add(1);
        ready_.notify_all();
    }
    template <class Source, class Sink>
    static void reader_main(void* self) {
        auto& p = *static_cast<Pipeline*>(self);
        auto& source = *static_cast<Source*>(p.source_);
        auto& sink = *static_cast<Sink*>(p.sink_);
        p.reader_policy_ = apply_thread_policy(p.options_.reader);
        prefault_stack(kRtStackPrefault);
        p.reader_tid_.store(pid_t(gettid()));
        p.thread_ready();
        const bool ring = p.options_.mode == PipelineMode::ring;
        Item item{};
        while (!p.stop_.load(std::memory_order_acquire)) {
            const SourceStatus status = source.next(item);
            if (status == SourceStatus::stop) break;
            if (status == SourceStatus::idle) continue;
            bump(p.read_);
            if (!ring) { sink(item); bump(p.published_); }
            else if (p.ring_.try_push(item)) p.parker_.notify();
            else bump(p.overruns_);  // never wait here: the reader's only job is to keep draining the kernel's queue
        }
        p.reader_done_.store(true, std::memory_order_release);
        p.parker_.notify();
    }
    template <class Sink>
    static void fusion_main(void* self) {
        auto& p = *static_cast<Pipeline*>(self);
        auto& sink = *static_cast<Sink*>(p.sink_);
        p.fusion_policy_ = apply_thread_policy(p.options_.fusion);
        prefault_stack(kRtStackPrefault);
        p.fusion_tid_.store(pid_t(gettid()));
        p.thread_ready();
        Item item{};
        for (;;) {
            // Read before draining: once the reader is done nothing more arrives, so this drain gets everything.
            const bool done = p.reader_done_.load(std::memory_order_acquire);
            std::uint64_t drained = 0;
            while (p.ring_.try_pop(item)) {
                sink(item);
                bump(p.published_);
                ++drained;
            }
            if (drained > p.backlog_.load(std::memory_order_relaxed)) p.backlog_.store(drained, std::memory_order_relaxed);
            if (done) return;
            bump(p.sleeps_);
            p.parker_.wait([&] { return !p.ring_.empty() || p.reader_done_.load(std::memory_order_acquire); }, p.options_.fusion_wait_ms);
        }
    }

    Options options_;
    void* source_ = nullptr;
    void* sink_ = nullptr;
    pthread_t reader_thread_{}, fusion_thread_{};
    bool running_ = false;
    ThreadPolicyResult reader_policy_, fusion_policy_;
    std::atomic<pid_t> reader_tid_{0}, fusion_tid_{0};
    std::atomic<int> ready_{0};
    std::atomic<bool> stop_{false}, reader_done_{false};
    Parker parker_;
    alignas(kCacheLine) std::atomic<std::uint64_t> read_{0}, overruns_{0};  // the reader's line
    alignas(kCacheLine) std::atomic<std::uint64_t> published_{0}, backlog_{0}, sleeps_{0};  // fusion's line
    SpscRing<Item, Capacity> ring_;
};
}  // namespace luma::rt
