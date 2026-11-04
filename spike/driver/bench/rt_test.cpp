// Offline tests of the driver's real-time layer. No hardware, no privileges needed. The real-time paths that this machine may
// not permit are driven through a fake SysOps; apply_real_thread shows what the real kernel gave.
#include "alloc_probe.hpp"
#include "luma/rt.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <optional>
#include <sched.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
#define CHECK(...) do { if (!(__VA_ARGS__)) throw std::runtime_error(std::string(__FILE__ ":") + std::to_string(__LINE__) + ": " #__VA_ARGS__); } while (0)
using namespace luma::rt;
using std::chrono_literals::operator""ms;

namespace {
std::string shm_name(const char* tag) { return "/luma-rt-test-" + std::to_string(getpid()) + "-" + tag; }
struct Unlink { std::string n; ~Unlink() { shm_unlink(n.c_str()); } };

void ring_holds_exactly_capacity_and_wraps() {
    SpscRing<int, 4> r;
    int v = 0;
    CHECK(!r.try_pop(v));
    for (int round = 0; round < 10; ++round) {  // several rounds cross the index wrap
        for (int i = 0; i < 4; ++i) CHECK(r.try_push(round * 10 + i));
        CHECK(!r.try_push(99));
        for (int i = 0; i < 4; ++i) { CHECK(r.try_pop(v)); CHECK(v == round * 10 + i); }
        CHECK(!r.try_pop(v));
    }
}
void ring_keeps_order_across_threads() {
    static SpscRing<std::uint64_t, 16> r;  // tiny, so both the full and the empty edge are hit constantly
    constexpr std::uint64_t kN = 2'000'000;
    std::thread producer([] { for (std::uint64_t i = 0; i < kN;) if (r.try_push(i)) ++i; });
    std::uint64_t want = 0, got = 0;
    while (want < kN) if (r.try_pop(got)) { CHECK(got == want); ++want; }
    producer.join();
}
// A lost wake-up would cost the full timeout on every occurrence, so a long ping-pong finishes in time only if none is lost.
void parker_loses_no_wakeups() {
    static Parker a, b;
    static std::atomic<std::uint64_t> ball{0};
    constexpr std::uint64_t kN = 100'000;
    constexpr int kTimeoutMs = 2000;
    const auto start = std::chrono::steady_clock::now();
    std::thread other([] {
        for (std::uint64_t i = 1; i <= kN; i += 2) {
            while (ball.load(std::memory_order_acquire) != i) a.wait([&] { return ball.load(std::memory_order_acquire) == i; }, kTimeoutMs);
            ball.store(i + 1, std::memory_order_release);
            b.notify();
        }
    });
    for (std::uint64_t i = 0; i < kN; i += 2) {
        ball.store(i + 1, std::memory_order_release);
        a.notify();
        while (ball.load(std::memory_order_acquire) != i + 2) b.wait([&] { return ball.load(std::memory_order_acquire) == i + 2; }, kTimeoutMs);
    }
    other.join();
    CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(kTimeoutMs / 1000));
}
void parker_wait_times_out_without_a_notify() {
    Parker p;
    const auto start = std::chrono::steady_clock::now();
    p.wait([] { return false; }, 20);
    const auto took = std::chrono::steady_clock::now() - start;
    CHECK(took >= 15ms && took < 2000ms);
}

struct Torn { std::array<std::uint64_t, 6> w{}; };
void seqlock_empty_slot_reads_nothing() {
    SeqlockSlot<Torn> s;
    Torn t;
    CHECK(!s.load(t));
}
void seqlock_never_tears() {
    static SeqlockSlot<Torn> s;
    static std::atomic<bool> done{false};
    std::thread writer([] {
        for (std::uint64_t i = 1; i < 3'000'000; ++i) { Torn t; t.w.fill(i); s.store(t); }
        done.store(true);
    });
    Torn t;
    std::uint64_t reads = 0, last = 0;
    while (!done.load()) {
        if (!s.load(t)) continue;
        for (auto w : t.w) CHECK(w == t.w[0]);
        CHECK(t.w[0] >= last);  // never older than what was already seen
        last = t.w[0];
        ++reads;
    }
    writer.join();
    CHECK(reads > 0);
}
// A writer that died mid-write leaves the sequence odd. Readers must give up instead of hanging, which is what a robust mutex
// needed a whole recovery protocol for.
void seqlock_gives_up_on_a_dead_writer() {
    SeqlockSlot<Torn> s;
    Torn in; in.w.fill(7);
    s.store(in);
    s.seq.store(s.seq.load() + 1);  // odd, as if killed between the two sequence stores
    Torn out;
    const auto start = std::chrono::steady_clock::now();
    CHECK(!s.load(out));
    CHECK(std::chrono::steady_clock::now() - start < 100ms);
}

// Across real processes: a writer killed at a random moment, a reader that keeps going, a new writer that takes over.
void shm_survives_a_killed_writer() {
    const auto name = shm_name("kill");
    Unlink guard{name};
    int ready[2];
    CHECK(pipe(ready) == 0);
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        std::string why;
        auto w = SeqlockWriter<Torn>::create(name, why);
        if (!w) _exit(3);
        Torn t;
        t.w.fill(1);
        w->publish(t);
        char c = 1;
        if (write(ready[1], &c, 1) != 1) _exit(4);
        for (std::uint64_t i = 2;; ++i) { t.w.fill(i); w->publish(t); }
    }
    char c = 0;
    CHECK(read(ready[0], &c, 1) == 1);
    close(ready[0]); close(ready[1]);
    std::string why;
    auto r = SeqlockReader<Torn>::open(name, why);
    CHECK(r.has_value());
    Torn t;
    std::uint64_t ok = 0;
    for (int i = 0; i < 200000; ++i) {
        if (!r->read(t)) continue;
        for (auto w : t.w) CHECK(w == t.w[0]);
        ++ok;
    }
    CHECK(ok > 0);
    kill(child, SIGKILL);
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child && WIFSIGNALED(status));
    r->read(t);  // whatever it finds, it returns
    auto w2 = SeqlockWriter<Torn>::create(name, why);
    CHECK(w2.has_value());
    Torn fresh; fresh.w.fill(0xABCDEF);
    w2->publish(fresh);
    CHECK(r->read(t));  // the reader from before the crash keeps working, without reopening
    CHECK(t.w[0] == 0xABCDEF && t.w[5] == 0xABCDEF);
}
// Killed between the two sequence stores: the next writer must repair it, or every later publish would leave it odd and no reader
// would ever read again. Forced here, because a real kill lands in that window only some of the time.
void shm_new_writer_repairs_a_sequence_left_odd() {
    const auto name = shm_name("odd");
    Unlink guard{name};
    std::string why;
    {
        auto w = SeqlockWriter<Torn>::create(name, why);
        CHECK(w.has_value());
        Torn t; t.w.fill(1);
        w->publish(t);
    }
    const int fd = shm_open(name.c_str(), O_RDWR, 0);
    CHECK(fd >= 0);
    void* base = mmap(nullptr, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(base != MAP_FAILED);
    close(fd);
    auto* seq = reinterpret_cast<std::atomic<std::uint64_t>*>(static_cast<char*>(base) + ShmMapping::kSlotOffset);
    CHECK(seq->load() == 2);  // one publish: the layout this test pokes at is the real one
    seq->store(3);
    munmap(base, 4096);
    auto w = SeqlockWriter<Torn>::create(name, why);
    CHECK(w.has_value());
    Torn t; t.w.fill(9);
    w->publish(t);
    auto r = SeqlockReader<Torn>::open(name, why);
    CHECK(r.has_value());
    Torn out;
    CHECK(r->read(out) && out.w[0] == 9);
}
void shm_refuses_a_second_writer() {
    const auto name = shm_name("two");
    Unlink guard{name};
    std::string why;
    auto first = SeqlockWriter<Torn>::create(name, why);
    CHECK(first.has_value());
    CHECK(!SeqlockWriter<Torn>::create(name, why).has_value());
    CHECK(!why.empty());
    first.reset();
    CHECK(SeqlockWriter<Torn>::create(name, why).has_value());
}
void shm_reader_refuses_a_different_layout() {
    const auto name = shm_name("layout");
    Unlink guard{name};
    std::string why;
    auto w = SeqlockWriter<std::array<std::uint64_t, 3>>::create(name, why);
    CHECK(w.has_value());
    CHECK(!SeqlockReader<Torn>::open(name, why).has_value());
    CHECK(SeqlockReader<std::array<std::uint64_t, 3>>::open(name, why).has_value());
}
void shm_writer_replaces_a_different_layout() {
    const auto name = shm_name("replace");
    Unlink guard{name};
    std::string why;
    { CHECK(SeqlockWriter<std::array<std::uint64_t, 3>>::create(name, why).has_value()); }
    auto w = SeqlockWriter<Torn>::create(name, why);
    CHECK(w.has_value());
    Torn t; t.w.fill(5);
    w->publish(t);
    auto r = SeqlockReader<Torn>::open(name, why);
    CHECK(r.has_value());
    Torn out;
    CHECK(r->read(out) && out.w[3] == 5);
}
void shm_reader_of_nothing_says_why() {
    std::string why;
    CHECK(!SeqlockReader<Torn>::open(shm_name("absent"), why).has_value());
    CHECK(!why.empty());
}
void shm_is_private_to_the_user() {
    const auto name = shm_name("mode");
    Unlink guard{name};
    std::string why;
    auto w = SeqlockWriter<Torn>::create(name, why);
    CHECK(w.has_value());
    struct stat st{};
    CHECK(stat(("/dev/shm" + name).c_str(), &st) == 0);
    CHECK((st.st_mode & 0777) == 0600);
}
// Offsets are the wire format; a reader in another language or an older build depends on them.
void pose_record_layout_is_fixed() {
    PoseRecord r{};
    const auto* base = reinterpret_cast<const char*>(&r);
    CHECK(sizeof(PoseRecord) == 40);
    CHECK(reinterpret_cast<const char*>(&r.w) - base == 0);
    CHECK(reinterpret_cast<const char*>(&r.z) - base == 12);
    CHECK(reinterpret_cast<const char*>(&r.t_raw_ns) - base == 16);
    CHECK(reinterpret_cast<const char*>(&r.raw_to_mono_ns) - base == 24);
    CHECK(reinterpret_cast<const char*>(&r.seq) - base == 32);
    CHECK(reinterpret_cast<const char*>(&r.flags) - base == 36);
}

struct FakeOps : SysOps {
    int fifo_rc = 0, nice_rc = 0, affinity_rc = 0, guard_rc = 0;
    int fifo_grant = -1;  // what a hard limit below the request would allow
    int fifo_calls = 0, nice_calls = 0, last_nice = 0, last_cpu = -1;
    int slice_rc = 0;
    std::uint64_t guard_us = 0, slice_ns = 0;
    int set_fifo(int& priority) const override {
        ++const_cast<FakeOps*>(this)->fifo_calls;
        if (fifo_grant >= 0) priority = fifo_grant;
        return fifo_rc;
    }
    int set_nice(int nice) const override {
        auto* self = const_cast<FakeOps*>(this);
        ++self->nice_calls; self->last_nice = nice;
        return nice_rc;
    }
    int set_affinity(int cpu) const override { const_cast<FakeOps*>(this)->last_cpu = cpu; return affinity_rc; }
    int arm_rttime_guard(std::uint64_t us) const override { const_cast<FakeOps*>(this)->guard_us = us; return guard_rc; }
    int set_short_slice(std::uint64_t ns) const override { const_cast<FakeOps*>(this)->slice_ns = ns; return slice_rc; }
};
void policy_uses_fifo_when_permitted() {
    FakeOps ops;
    ThreadPolicy p; p.fifo_priority = 12;
    const auto r = apply_thread_policy(p, ops);
    CHECK(r.sched == SchedClass::fifo && r.priority == 12);
    CHECK(ops.nice_calls == 0);
    CHECK(ops.guard_us > 0);  // a FIFO thread that spins forever must be killable by the kernel
    CHECK(!r.needs_setup);
}
void policy_reports_the_priority_a_limit_allowed() {
    FakeOps ops; ops.fifo_grant = 5;
    ThreadPolicy p; p.fifo_priority = 12;
    CHECK(apply_thread_policy(p, ops).priority == 5);
}
void policy_falls_back_to_nice() {
    FakeOps ops; ops.fifo_rc = EPERM;
    ThreadPolicy p; p.fallback_nice = -7;
    const auto r = apply_thread_policy(p, ops);
    CHECK(r.sched == SchedClass::nice && r.nice == -7 && ops.last_nice == -7);
    CHECK(r.needs_setup);
}
void policy_survives_having_no_privilege_at_all() {
    FakeOps ops; ops.fifo_rc = EPERM; ops.nice_rc = EACCES;
    const auto r = apply_thread_policy({}, ops);
    CHECK(r.sched == SchedClass::normal && r.needs_setup && !r.detail.empty());
}
// Refusing FIFO is the safe side: without the guard a hot-loop bug would freeze a core at real-time priority.
void policy_refuses_fifo_without_the_runaway_guard() {
    FakeOps ops; ops.guard_rc = EPERM;
    const auto r = apply_thread_policy({}, ops);
    CHECK(ops.fifo_calls == 0);
    CHECK(r.sched == SchedClass::nice);
}
// The slice is a normal-class setting: it applies when FIFO was refused, whether or not nice was, and needs no privilege.
void policy_asks_for_a_short_slice_only_as_a_fallback() {
    {
        FakeOps ops;
        CHECK(!apply_thread_policy({}, ops).short_slice);
        CHECK(ops.slice_ns == 0);
    }
    FakeOps refused; refused.fifo_rc = EPERM; refused.nice_rc = EACCES;
    const auto r = apply_thread_policy({}, refused);
    CHECK(r.short_slice && refused.slice_ns == kShortSliceNs && r.sched == SchedClass::normal);
    FakeOps nice; nice.fifo_rc = EPERM;
    CHECK(apply_thread_policy({}, nice).short_slice);
    FakeOps off; off.fifo_rc = EPERM;
    ThreadPolicy p; p.short_slice = false;
    CHECK(!apply_thread_policy(p, off).short_slice && off.slice_ns == 0);
}
void policy_survives_a_kernel_without_slices() {
    FakeOps ops; ops.fifo_rc = EPERM; ops.nice_rc = EPERM; ops.slice_rc = EINVAL;
    const auto r = apply_thread_policy({}, ops);
    CHECK(!r.short_slice && r.sched == SchedClass::normal);
}
void short_slice_reaches_the_real_kernel() {
    struct Attr { std::uint32_t size, policy; std::uint64_t flags; std::int32_t nice; std::uint32_t priority; std::uint64_t runtime, deadline, period; };
    std::uint64_t runtime = 1;
    bool asked = false;
    std::thread t([&] {
        asked = real_sys_ops().set_short_slice(kShortSliceNs) == 0;
        Attr a{};
        if (syscall(SYS_sched_getattr, 0, &a, sizeof a, 0) == 0) runtime = a.runtime;
    });
    t.join();
    if (asked) CHECK(runtime == kShortSliceNs);
}
void policy_realtime_off_never_asks() {
    FakeOps ops;
    ThreadPolicy p; p.realtime = false;
    const auto r = apply_thread_policy(p, ops);
    CHECK(ops.fifo_calls == 0 && ops.nice_calls == 0);
    CHECK(r.sched == SchedClass::normal && !r.needs_setup);
}
void policy_pins_only_when_asked_and_survives_a_bad_cpu() {
    {
        FakeOps ops;
        const auto r = apply_thread_policy({}, ops);
        CHECK(ops.last_cpu == -1 && r.cpu == -1);
    }
    FakeOps ops;
    ThreadPolicy p; p.cpu = 3;
    CHECK(apply_thread_policy(p, ops).cpu == 3);
    CHECK(ops.last_cpu == 3);
    FakeOps bad; bad.affinity_rc = EINVAL;
    const auto r = apply_thread_policy(p, bad);
    CHECK(r.cpu == -1 && r.sched == SchedClass::fifo && !r.detail.empty());
}
// What the real kernel does with this user's real limits: whatever it granted, the report must match the thread's actual state.
void apply_real_thread() {
    ThreadPolicyResult r;
    int policy = -1, nice = 99, cpu = -1;
    std::thread t([&] {
        ThreadPolicy p; p.cpu = sched_getcpu();
        r = apply_thread_policy(p);
        policy = sched_getscheduler(0) & ~SCHED_RESET_ON_FORK;
        errno = 0;
        nice = getpriority(PRIO_PROCESS, 0);
        cpu_set_t set;
        CPU_ZERO(&set);
        if (sched_getaffinity(0, sizeof set, &set) == 0 && CPU_COUNT(&set) == 1) cpu = sched_getcpu();
    });
    t.join();
    CHECK(r.cpu == cpu);
    if (r.sched == SchedClass::fifo) CHECK(policy == SCHED_FIFO);
    else CHECK(policy == SCHED_OTHER);
    if (r.sched == SchedClass::nice) CHECK(nice == r.nice);
}
void pinning_to_a_cpu_that_does_not_exist_is_reported() {
    ThreadPolicyResult r;
    std::thread t([&] { ThreadPolicy p; p.cpu = 100000; r = apply_thread_policy(p); });
    t.join();
    CHECK(r.cpu == -1 && !r.detail.empty());
}
// The commands must grant what the defaults ask for; Cody runs them once and they cannot be retried cheaply (a reboot).
void setup_commands_grant_what_the_defaults_ask() {
    const ThreadPolicy defaults;
    const std::string text = rt_setup_commands("cody");
    int rtprio = 0, nice = 0, memlock = 0;
    std::istringstream in(text);
    for (std::string line; std::getline(in, line);) {
        std::istringstream row(line);
        std::string user, dash, item;
        int value = 0;
        if (row >> user >> dash >> item >> value && user == "cody" && dash == "-") {
            if (item == "rtprio") rtprio = value;
            else if (item == "nice") nice = value;
            else if (item == "memlock") memlock = value;
        }
    }
    CHECK(rtprio >= defaults.fifo_priority);
    CHECK(nice <= defaults.fallback_nice && nice < 0);
    CHECK(std::uint64_t(memlock) * 1024 >= kMemlockWantedBytes);
    CHECK(text.find("sudo") != std::string::npos);
}
void memory_lock_only_under_a_limit_it_cannot_hit() {
    // Under a small limit MCL_FUTURE would make later mmaps fail: libudev on a reconnect, of all moments.
    CHECK(!memlock_is_safe(8 * 1024 * 1024));
    CHECK(memlock_is_safe(kMemlockWantedBytes));
    CHECK(memlock_is_safe(RLIM_INFINITY));
}

void clocks_are_raw_and_convertible() {
    timespec ts{};
    const std::uint64_t a = monotonic_raw_ns();
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    const std::uint64_t b = std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
    CHECK(b >= a && b - a < 50'000'000);
    clock_gettime(CLOCK_MONOTONIC, &ts);
    const std::uint64_t mono = std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
    const std::int64_t offset = raw_to_monotonic_offset_ns();
    const std::uint64_t converted = monotonic_raw_ns() + std::uint64_t(offset);
    CHECK(converted >= mono && converted - mono < 50'000'000);
}

void histogram_percentiles_are_within_three_percent() {
    LatencyHistogram h;
    for (std::uint64_t v = 1; v <= 100'000; ++v) h.record(v);  // 1..100 us uniform: pN is N us
    const auto within = [](double got, double want) { return std::fabs(got - want) <= want * 0.035; };
    CHECK(within(double(h.percentile(50)), 50'000));
    CHECK(within(double(h.percentile(99)), 99'000));
    CHECK(h.max() == 100'000 && h.min() == 1 && h.count() == 100'000);
    CHECK(std::fabs(h.mean() - 50'000.5) < 1);
    CHECK(std::fabs(h.stddev() - 28'867.5) < 1);  // (b-a)/sqrt(12)
}
void histogram_shows_a_lone_spike_in_max_not_in_p99() {
    LatencyHistogram h;
    for (int i = 0; i < 9999; ++i) h.record(1000);
    h.record(5'000'000);
    CHECK(h.percentile(99) <= 1032);
    CHECK(h.max() == 5'000'000);
}
void histogram_small_and_huge_values_stay_in_range() {
    LatencyHistogram h;
    h.record(0); h.record(63); h.record(64); h.record(~std::uint64_t{0});
    CHECK(h.count() == 4 && h.min() == 0 && h.max() == ~std::uint64_t{0});
    CHECK(h.percentile(25) == 0);
    CHECK(h.percentile(100) >= h.percentile(75));
}
void histogram_empty_reads_zero() {
    LatencyHistogram h;
    CHECK(h.percentile(99) == 0 && h.max() == 0 && h.min() == 0 && h.mean() == 0 && h.stddev() == 0);
}

struct Numbered { std::uint64_t n; };
struct CountingSource {
    std::uint64_t next_n = 0, end = 0;
    SourceStatus next(Numbered& out) {
        if (next_n >= end) return SourceStatus::stop;
        out.n = next_n++;
        return SourceStatus::item;
    }
};
struct OrderSink {
    std::atomic<std::uint64_t> seen{0};
    std::atomic<bool> out_of_order{false};
    int delay_us = 0;
    void operator()(const Numbered& i) noexcept {
        const auto s = seen.load(std::memory_order_relaxed);
        if (i.n != s) out_of_order.store(true);
        if (delay_us) std::this_thread::sleep_for(std::chrono::microseconds(delay_us));
        seen.store(s + 1, std::memory_order_release);
    }
};
template <class P> void wait_seen(const OrderSink& s, P& p, std::uint64_t want) {
    for (int i = 0; i < 5000 && s.seen.load() < want; ++i) std::this_thread::sleep_for(1ms);
    p.stop();
}
// No batching: one published pose per sample, in order, even when several wait in the ring. Stopping publishes what was read.
void pipeline_ring_publishes_every_item_in_order() {
    Pipeline<Numbered, 8> p;
    CountingSource src; src.end = 5000;
    OrderSink sink;
    sink.delay_us = 1;  // lets a backlog build up
    Options o;
    std::string why;
    // A source faster than the sink would overrun an 8-slot ring: pace it.
    struct Paced {
        CountingSource& c; OrderSink& s;
        SourceStatus next(Numbered& out) {
            while (c.next_n > s.seen.load(std::memory_order_acquire) + 6) std::this_thread::yield();
            return c.next(out);
        }
    } paced{src, sink};
    CHECK(p.start(paced, sink, o, why));
    wait_seen(sink, p, src.end);
    CHECK(sink.seen.load() == 5000 && !sink.out_of_order.load());
    const auto s = p.stats();
    CHECK(s.items_read == 5000 && s.items_published == 5000 && s.ring_overruns == 0);
    CHECK(s.max_backlog >= 2);  // several were waiting, and all were published individually
}
void pipeline_stop_drains_the_ring() {
    Pipeline<Numbered, 1024> p;
    CountingSource src; src.end = 800;
    OrderSink sink; sink.delay_us = 50;
    std::string why;
    CHECK(p.start(src, sink, {}, why));
    p.stop();  // the source is long done or nearly; nothing it produced may vanish
    CHECK(sink.seen.load() == p.stats().items_read);
}
// The narrow case: the last item lands just after fusion found the ring empty, and the reader finishes at once. Many short runs, so
// fusion's check keeps landing in that window.
void pipeline_stop_never_strands_the_last_item() {
    for (int run = 0; run < 3000; ++run) {
        Pipeline<Numbered> p;
        CountingSource src; src.end = 1 + std::uint64_t(run % 3);
        OrderSink sink;
        std::string why;
        CHECK(p.start(src, sink, {}, why));
        while (p.stats().items_read < src.end) std::this_thread::yield();
        p.stop();
        CHECK(sink.seen.load() == src.end);
    }
}
void pipeline_inline_mode_publishes_on_the_reader_thread() {
    Pipeline<Numbered> p;
    CountingSource src; src.end = 1000;
    struct Sink {
        OrderSink inner; std::atomic<pid_t> tid{0};
        void operator()(const Numbered& i) noexcept { tid.store(pid_t(gettid())); inner(i); }
    } sink;
    Options o; o.mode = PipelineMode::inline_;
    std::string why;
    CHECK(p.start(src, sink, o, why));
    wait_seen(sink.inner, p, 1000);
    CHECK(sink.inner.seen.load() == 1000);
    CHECK(sink.tid.load() == p.reader_tid());
    CHECK(p.fusion_tid() == 0);
}
void pipeline_ring_uses_a_separate_fusion_thread() {
    Pipeline<Numbered> p;
    CountingSource src; src.end = 10;
    struct Sink { std::atomic<pid_t> tid{0}; void operator()(const Numbered&) noexcept { tid.store(pid_t(gettid())); } } sink;
    std::string why;
    CHECK(p.start(src, sink, {}, why));
    for (int i = 0; i < 2000 && !sink.tid.load(); ++i) std::this_thread::sleep_for(1ms);
    const pid_t fusion = p.fusion_tid();
    p.stop();
    CHECK(sink.tid.load() == fusion && fusion != 0 && fusion != getpid());
}
// A stalled fusion must never block the USB reader. Losses are counted, not silent.
void pipeline_full_ring_counts_overruns_and_never_blocks_the_reader() {
    Pipeline<Numbered, 8> p;
    CountingSource src; src.end = 100;
    struct Sink {
        std::atomic<bool> go{false}; std::atomic<std::uint64_t> n{0};
        void operator()(const Numbered&) noexcept { while (!go.load()) std::this_thread::yield(); n.fetch_add(1); }
    } sink;
    std::string why;
    CHECK(p.start(src, sink, {}, why));
    for (int i = 0; i < 5000 && p.stats().items_read < 100; ++i) std::this_thread::sleep_for(1ms);
    CHECK(p.stats().items_read == 100);  // the reader finished although nothing was consumed
    sink.go.store(true);
    p.stop();
    const auto s = p.stats();
    CHECK(s.ring_overruns > 0);
    CHECK(s.items_published + s.ring_overruns == 100);
}
void pipeline_idle_source_is_stoppable() {
    Pipeline<Numbered> p;
    struct Idle { SourceStatus next(Numbered&) { std::this_thread::sleep_for(1ms); return SourceStatus::idle; } } src;
    struct Sink { void operator()(const Numbered&) noexcept {} } sink;
    std::string why;
    CHECK(p.start(src, sink, {}, why));
    p.stop();
    CHECK(p.stats().items_read == 0);
}
void pipeline_reports_its_thread_policies() {
    Pipeline<Numbered> p;
    CountingSource src; src.end = 1;
    struct Sink { void operator()(const Numbered&) noexcept {} } sink;
    Options o; o.reader.realtime = false; o.fusion.realtime = false;
    std::string why;
    CHECK(p.start(src, sink, o, why));
    CHECK(p.reader_policy().sched == SchedClass::normal && p.fusion_policy().sched == SchedClass::normal);
    p.stop();
}
void pipeline_restarts_after_stop() {
    Pipeline<Numbered> p;
    OrderSink sink;
    CountingSource a; a.end = 10;
    std::string why;
    CHECK(p.start(a, sink, {}, why));
    wait_seen(sink, p, 10);
    CHECK(sink.seen.load() == 10);
    CountingSource b; b.next_n = 10; b.end = 20;
    CHECK(p.start(b, sink, {}, why));
    wait_seen(sink, p, 20);
    CHECK(sink.seen.load() == 20 && !sink.out_of_order.load());
}
// The hot path, both modes: nothing on any thread may touch the heap once running.
void hot_path_never_allocates() {
#if LUMA_ALLOC_PROBE
    for (PipelineMode mode : {PipelineMode::ring, PipelineMode::inline_}) {
        Pipeline<Numbered> p;
        OrderSink sink;
        struct Src {
            OrderSink& sink;
            std::atomic<std::uint64_t> released{0}; std::uint64_t n = 0;
            SourceStatus next(Numbered& out) {
                // Paced by the sink: a burst of 20000 would overrun the ring, and an overrun item never reaches the sink.
                if (n >= released.load(std::memory_order_acquire) || n > sink.seen.load(std::memory_order_acquire) + 100) {
                    std::this_thread::yield();
                    return SourceStatus::idle;
                }
                out.n = n++;
                return SourceStatus::item;
            }
        } src{sink};
        Options o; o.mode = mode;
        std::string why;
        CHECK(p.start(src, sink, o, why));
        src.released.store(100);
        while (sink.seen.load() < 100) std::this_thread::yield();
        const auto before = luma::bench::alloc_count();
        src.released.store(20000);
        while (sink.seen.load() < 20000) std::this_thread::yield();
        const auto after = luma::bench::alloc_count();
        p.stop();
        CHECK(after == before);
    }
    // ...and the probe itself sees allocations, or the zero above means nothing.
    const auto before = luma::bench::alloc_count();
    auto volatile keep = std::make_unique<char[]>(64).release();
    delete[] keep;
    CHECK(luma::bench::alloc_count() > before);
#else
    std::cout << "(allocation probe off under a sanitizer)\n";
#endif
}
}  // namespace

int main() {
    const std::pair<const char*, void (*)()> tests[] = {
#define T(x) {#x, x}
        T(ring_holds_exactly_capacity_and_wraps), T(ring_keeps_order_across_threads), T(parker_loses_no_wakeups),
        T(parker_wait_times_out_without_a_notify), T(seqlock_empty_slot_reads_nothing), T(seqlock_never_tears),
        T(seqlock_gives_up_on_a_dead_writer), T(shm_survives_a_killed_writer), T(shm_new_writer_repairs_a_sequence_left_odd), T(shm_refuses_a_second_writer),
        T(shm_reader_refuses_a_different_layout), T(shm_writer_replaces_a_different_layout), T(shm_reader_of_nothing_says_why),
        T(shm_is_private_to_the_user), T(pose_record_layout_is_fixed), T(policy_uses_fifo_when_permitted),
        T(policy_reports_the_priority_a_limit_allowed), T(policy_falls_back_to_nice), T(policy_survives_having_no_privilege_at_all),
        T(policy_refuses_fifo_without_the_runaway_guard), T(policy_asks_for_a_short_slice_only_as_a_fallback),
        T(policy_survives_a_kernel_without_slices), T(short_slice_reaches_the_real_kernel), T(policy_realtime_off_never_asks),
        T(policy_pins_only_when_asked_and_survives_a_bad_cpu), T(apply_real_thread), T(pinning_to_a_cpu_that_does_not_exist_is_reported),
        T(setup_commands_grant_what_the_defaults_ask), T(memory_lock_only_under_a_limit_it_cannot_hit),
        T(clocks_are_raw_and_convertible), T(histogram_percentiles_are_within_three_percent),
        T(histogram_shows_a_lone_spike_in_max_not_in_p99), T(histogram_small_and_huge_values_stay_in_range), T(histogram_empty_reads_zero),
        T(pipeline_ring_publishes_every_item_in_order), T(pipeline_stop_drains_the_ring), T(pipeline_stop_never_strands_the_last_item), T(pipeline_inline_mode_publishes_on_the_reader_thread),
        T(pipeline_ring_uses_a_separate_fusion_thread), T(pipeline_full_ring_counts_overruns_and_never_blocks_the_reader),
        T(pipeline_idle_source_is_stoppable), T(pipeline_reports_its_thread_policies), T(pipeline_restarts_after_stop), T(hot_path_never_allocates),
#undef T
    };
    int failed = 0;
    for (const auto& [name, fn] : tests) {
        try { fn(); std::cout << "ok   " << name << "\n"; }
        catch (const std::exception& e) { std::cout << "FAIL " << name << ": " << e.what() << "\n"; ++failed; }
    }
    return failed ? 1 : 0;
}
