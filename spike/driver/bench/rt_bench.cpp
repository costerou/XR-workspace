// Sensor packet to published pose, measured: a fake 1 kHz transport drives the real luma::rt::Pipeline through a stand-in for
// fusion and the real seqlock. docs/performance.md explains what it does and does not prove, and has the results.
//   luma_rt_bench                       30 s, ring mode, seqlock, real-time if permitted
//   luma_rt_bench --check               the same, and exits 1 if a target is missed (p99 < 0.5 ms, < 1% of a core, nothing lost)
//   luma_rt_bench --quick               1 s; asserts only that nothing is lost or allocated (for ctest: no timing on a busy box)
//   --mode ring|inline  --publish seqlock|mutex  --seconds N  --rate HZ  --rt auto|off  --no-slice  --lock
//   --reader-cpu N  --fusion-cpu N  --sender-cpu N  --spin-reader  --load N (N busy threads)
#include "alloc_probe.hpp"
#include "fake_transport.hpp"
#include "luma/rt.hpp"
#include "stub_fusion.hpp"
#include <cinttypes>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <pthread.h>
#include <sched.h>
#include <string>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <vector>

using namespace luma;
using namespace luma::rt;
using Fusion = bench::StubFusion;  // luma::Fusion, once merged

namespace {
struct Args {
    double seconds = 30, warmup_s = 1;
    int rate = 1000, reader_cpu = -1, fusion_cpu = -1, sender_cpu = -1, load = 0;
    bool inline_mode = false, mutex_publish = false, rt = true, slice = true, lock = false, spin_reader = false, check = false, quick = false;
};
bool parse(int argc, char** argv, Args& a) {
    for (int i = 1; i < argc; ++i) {
        const std::string s = argv[i];
        const auto value = [&]() -> const char* { return i + 1 < argc ? argv[++i] : "0"; };
        if (s == "--seconds") a.seconds = std::atof(value());
        else if (s == "--rate") a.rate = std::atoi(value());
        else if (s == "--mode") a.inline_mode = std::string(value()) == "inline";
        else if (s == "--publish") a.mutex_publish = std::string(value()) == "mutex";
        else if (s == "--rt") a.rt = std::string(value()) != "off";
        else if (s == "--no-slice") a.slice = false;
        else if (s == "--load") a.load = std::atoi(value());
        else if (s == "--lock") a.lock = true;
        else if (s == "--spin-reader") a.spin_reader = true;
        else if (s == "--reader-cpu") a.reader_cpu = std::atoi(value());
        else if (s == "--fusion-cpu") a.fusion_cpu = std::atoi(value());
        else if (s == "--sender-cpu") a.sender_cpu = std::atoi(value());
        else if (s == "--check") a.check = true;
        else if (s == "--quick") { a.quick = true; a.seconds = 1; a.warmup_s = 0.2; }
        else { std::fprintf(stderr, "unknown option %s\n", s.c_str()); return false; }
    }
    return a.seconds > 0 && a.rate > 0;
}

struct Item { ImuSample s; std::uint64_t t_sent, t_read; };

struct ThreadUse { std::uint64_t cpu_ns = 0; long faults = 0, voluntary = 0, forced = 0; };
ThreadUse thread_use() noexcept {
    timespec ts{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    rusage ru{};
    getrusage(RUSAGE_THREAD, &ru);
    return {std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec), ru.ru_minflt + ru.ru_majflt, ru.ru_nvcsw, ru.ru_nivcsw};
}
ThreadUse operator-(const ThreadUse& a, const ThreadUse& b) { return {a.cpu_ns - b.cpu_ns, a.faults - b.faults, a.voluntary - b.voluntary, a.forced - b.forced}; }

// Today's feed: 16 floats under a process-shared robust mutex (XRLinuxDriver src/outputs.c, read by spike/src/pose.cpp), for comparison.
struct MutexFeed {
    pthread_mutex_t mutex;
    float pose[16];
    void init() {
        pthread_mutexattr_t attr;
        pthread_mutexattr_init(&attr);
        pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
        pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST);
        pthread_mutex_init(&mutex, &attr);
        pthread_mutexattr_destroy(&attr);
    }
    void publish(const PoseOut& p) noexcept {
        pthread_mutex_lock(&mutex);
        pose[0] = p.q.x; pose[1] = p.q.y; pose[2] = p.q.z; pose[3] = p.q.w;
        pose[12] = float(p.t_ns / 1'000'000);
        pthread_mutex_unlock(&mutex);
    }
};

// Everything the sink learns, on the thread that publishes.
struct Sink {
    Fusion fusion;
    SeqlockWriter<PoseRecord>* writer = nullptr;
    MutexFeed* mutex_feed = nullptr;
    std::int64_t raw_to_mono = 0;
    std::uint32_t first = 0, last = 0;
    bool separate_thread = true;
    std::uint64_t period_ns = 0;
    LatencyHistogram total, transport, handoff, work, interval_error;
    std::uint64_t gaps = 0, previous_publish = 0, window_start = 0, window_end = 0, allocs_start = 0, allocs_end = 0;
    std::uint32_t previous_seq = ~std::uint32_t{0};
    ThreadUse use_start, use_end;
    std::atomic<bool> finished{false};

    void operator()(const Item& i) noexcept {
        const std::uint32_t seq = i.s.seq;
        if (seq == first) { use_start = thread_use(); allocs_start = bench::alloc_count(); window_start = monotonic_raw_ns(); }
        const std::uint64_t t_start = monotonic_raw_ns();
        const PoseOut pose = fusion.update(i.s);
        if (mutex_feed) mutex_feed->publish(pose);
        else writer->publish(PoseRecord{pose.q.w, pose.q.x, pose.q.y, pose.q.z, i.t_sent, raw_to_mono, pose.seq, pose.calibrated ? kPoseCalibrated : 0});
        const std::uint64_t t_done = monotonic_raw_ns();
        if (seq != previous_seq + 1) ++gaps;
        previous_seq = seq;
        if (seq >= first && seq < last) {
            total.record(t_done - i.t_sent);
            transport.record(i.t_read - i.t_sent);
            handoff.record(t_start - i.t_read);
            work.record(t_done - t_start);
            if (seq > first) {
                const std::uint64_t gap = t_done - previous_publish;
                interval_error.record(gap > period_ns ? gap - period_ns : period_ns - gap);
            }
        }
        previous_publish = t_done;
        if (seq == last) {
            allocs_end = bench::alloc_count(); window_end = monotonic_raw_ns(); use_end = thread_use();
            finished.store(true, std::memory_order_release);
        }
    }
};
struct Source {
    bench::FakeTransport& transport;
    std::uint32_t first = 0, last = 0;
    std::uint64_t bad = 0;
    ThreadUse use_start, use_end;
    SourceStatus next(Item& item) noexcept {
        std::array<std::uint8_t, 64> buffer;
        const int n = transport.read(buffer, std::chrono::milliseconds(100));
        const std::uint64_t t_read = monotonic_raw_ns();
        if (n == 0) return SourceStatus::idle;
        if (n < 0) return SourceStatus::stop;
        const auto packet = bench::parse_fake(std::span(buffer).first(std::size_t(n)));
        if (!packet) { ++bad; return SourceStatus::idle; }
        if (packet->seq == first) use_start = thread_use();
        if (packet->seq == last) use_end = thread_use();
        item.t_sent = packet->t_sent_ns;
        item.t_read = t_read;
        item.s.t_ns = t_read;
        item.s.seq = packet->seq;
        for (int k = 0; k < 3; ++k) { item.s.gyro_rad_s[std::size_t(k)] = packet->gyro[k]; item.s.accel_m_s2[std::size_t(k)] = packet->accel[k]; }
        item.s.mag.reset();
        return SourceStatus::item;
    }
};

struct SpinShared { std::atomic<bool> stop; std::atomic<bool> up; LatencyHistogram seen; std::uint64_t torn_free_reads; };
// A separate process reads the seqlock as fast as it can: the proof that a pose is visible to another process, and how soon.
pid_t start_spin_reader(const std::string& name, SpinShared* shared, std::uint32_t first) {
    const pid_t pid = fork();
    if (pid != 0) return pid;
    std::string why;
    auto reader = SeqlockReader<PoseRecord>::open(name, why);
    if (!reader) _exit(2);
    shared->up.store(true);
    PoseRecord r;
    std::uint32_t last = ~std::uint32_t{0};
    while (!shared->stop.load(std::memory_order_relaxed)) {
        if (!reader->read(r) || r.seq == last) continue;
        const std::uint64_t now = monotonic_raw_ns();
        last = r.seq;
        ++shared->torn_free_reads;
        if (r.seq >= first) shared->seen.record(now - r.t_raw_ns);
    }
    _exit(0);
}
const char* sched_name(pid_t tid) {
    if (!tid) return "none";
    switch (sched_getscheduler(tid) & ~SCHED_RESET_ON_FORK) {
        case SCHED_FIFO: return "fifo";
        case SCHED_RR: return "rr";
        case SCHED_OTHER: return "other";
        default: return "?";
    }
}
void show(const char* name, const LatencyHistogram& h) {
    std::printf("  %-22s p50 %8.1f  p99 %8.1f  p99.9 %8.1f  max %9.1f  stddev %7.1f us\n", name, double(h.percentile(50)) / 1e3, double(h.percentile(99)) / 1e3,
                double(h.percentile(99.9)) / 1e3, double(h.max()) / 1e3, h.stddev() / 1e3);
}
}  // namespace

int main(int argc, char** argv) {
    Args a;
    if (!parse(argc, argv, a)) return 2;
    if (a.lock) {
        const auto m = lock_memory();
        if (!m.locked) std::printf("%s\n", m.detail.c_str());
    }
    const auto first = std::uint32_t(a.warmup_s * a.rate);
    const auto last = first + std::uint32_t(a.seconds * a.rate);
    const std::string shm = "/luma-rt-bench-" + std::to_string(getpid());
    std::string why;
    auto writer = SeqlockWriter<PoseRecord>::create(shm, why);
    if (!writer) { std::fprintf(stderr, "%s\n", why.c_str()); return 2; }

    auto* spin = static_cast<SpinShared*>(mmap(nullptr, sizeof(SpinShared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0));
    new (spin) SpinShared();
    pid_t spin_pid = 0;
    if (a.spin_reader) spin_pid = start_spin_reader(shm, spin, first);

    auto* feed = static_cast<MutexFeed*>(mmap(nullptr, sizeof(MutexFeed), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0));
    feed->init();

    static Pipeline<Item> pipeline;
    static Sink sink;
    sink.writer = &*writer;
    sink.mutex_feed = a.mutex_publish ? feed : nullptr;
    sink.raw_to_mono = raw_to_monotonic_offset_ns();
    sink.first = first; sink.last = last;
    sink.period_ns = 1'000'000'000ull / std::uint64_t(a.rate);
    // Its histograms are about 75 KiB of untouched memory: their first use must not be what the page-fault count below reports.
    madvise(reinterpret_cast<void*>(std::uintptr_t(&sink) & ~std::uintptr_t{4095}), sizeof sink + 4096, MADV_POPULATE_WRITE);

    std::atomic<bool> load_stop{false};
    std::vector<std::thread> load;
    for (int i = 0; i < a.load; ++i) load.emplace_back([&] { while (!load_stop.load(std::memory_order_relaxed)) __builtin_ia32_pause(); });

    bench::FakeTransport transport(a.rate, last + 20, a.sender_cpu);
    if (!transport.open(why)) { std::fprintf(stderr, "%s\n", why.c_str()); return 2; }
    Source source{transport, first, last, 0, {}, {}};
    Options o;
    o.mode = a.inline_mode ? PipelineMode::inline_ : PipelineMode::ring;
    o.reader.realtime = o.fusion.realtime = a.rt;
    o.reader.short_slice = o.fusion.short_slice = a.slice;
    o.reader.cpu = a.reader_cpu;
    o.fusion.cpu = a.fusion_cpu;
    if (!pipeline.start(source, sink, o, why)) { std::fprintf(stderr, "%s\n", why.c_str()); return 2; }
    const std::string reader_sched = sched_name(pipeline.reader_tid()), fusion_sched = sched_name(pipeline.fusion_tid());
    transport.start();
    if (a.spin_reader) while (!spin->up.load()) std::this_thread::yield();

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(a.seconds + a.warmup_s + 10);
    while (!sink.finished.load(std::memory_order_acquire) && !transport.finished() && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    transport.stop();
    load_stop.store(true);
    for (auto& t : load) t.join();
    if (!sink.finished.load()) std::printf("the last sample never arrived: overruns or a stall\n");
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    pipeline.stop();
    if (spin_pid) {
        spin->stop.store(true);
        int status = 0;
        waitpid(spin_pid, &status, 0);
    }
    shm_unlink(shm.c_str());

    const auto stats = pipeline.stats();
    const double wall_s = double(sink.window_end - sink.window_start) / 1e9;
    const ThreadUse reader = source.use_end - source.use_start, fusion = a.inline_mode ? ThreadUse{} : sink.use_end - sink.use_start;
    const double cpu_pct = wall_s > 0 ? double(reader.cpu_ns + fusion.cpu_ns) / 1e9 / wall_s * 100 : 0;
    const std::uint64_t allocs = sink.allocs_end - sink.allocs_start;
    const std::uint64_t lost = transport.sent() - stats.items_published;

    std::printf("%s, %s, %d Hz, %.1f s, reader %s%s, fusion %s, %d busy threads\n", a.inline_mode ? "inline" : "ring", a.mutex_publish ? "mutex" : "seqlock", a.rate, wall_s,
                reader_sched.c_str(), pipeline.reader_policy().short_slice ? " short-slice" : "", fusion_sched.c_str(), a.load);
    if (!pipeline.reader_policy().detail.empty()) std::printf("  reader: %s\n", pipeline.reader_policy().detail.c_str());
    if (pipeline.reader_policy().needs_setup && !a.quick) std::printf("  one-time setup for real-time: see docs/performance.md\n");
    show("sensor -> published", sink.total);
    show("  queue + wake reader", sink.transport);
    show("  hand-over to fusion", sink.handoff);
    show("  fusion + publish", sink.work);
    show("interval error", sink.interval_error);
    if (spin_pid) show("visible to a process", spin->seen);
    std::printf("  cpu %.3f%% of a core (reader %" PRIu64 " ns/sample, fusion %" PRIu64 " ns/sample)\n", cpu_pct, reader.cpu_ns / std::max<std::uint64_t>(1, sink.total.count()),
                fusion.cpu_ns / std::max<std::uint64_t>(1, sink.total.count()));
    std::printf("  sent %" PRIu64 ", published %" PRIu64 ", kernel queue overflows %" PRIu64 ", ring overruns %" PRIu64 ", gaps %" PRIu64 ", worst backlog %" PRIu64 "\n",
                transport.sent(), stats.items_published, transport.queue_overflows(), stats.ring_overruns, sink.gaps, stats.max_backlog);
    std::printf("  allocations %" PRIu64 ", page faults %ld, forced context switches %ld, late sends (rig) %" PRIu64 "\n", allocs, reader.faults + fusion.faults,
                reader.forced + fusion.forced, transport.late_sends());
    std::printf("json {\"mode\":\"%s\",\"publish\":\"%s\",\"reader_sched\":\"%s\",\"short_slice\":%s,\"load\":%d,\"p50_us\":%.1f,\"p99_us\":%.1f,\"p999_us\":%.1f,\"max_us\":%.1f,\"cpu_pct\":%.3f,\"lost\":%" PRIu64 ",\"allocs\":%" PRIu64 "}\n",
                a.inline_mode ? "inline" : "ring", a.mutex_publish ? "mutex" : "seqlock", reader_sched.c_str(), pipeline.reader_policy().short_slice ? "true" : "false", a.load, double(sink.total.percentile(50)) / 1e3, double(sink.total.percentile(99)) / 1e3,
                double(sink.total.percentile(99.9)) / 1e3, double(sink.total.max()) / 1e3, cpu_pct, lost, allocs);

    int failed = 0;
    const auto require = [&](bool ok, const char* what) { if (!ok) { std::printf("FAILED: %s\n", what); ++failed; } };
    require(sink.finished.load(), "the run reached its last sample");
    require(sink.total.count() == last - first, "every sample in the window was measured");
    require(source.bad == 0, "no packet failed its CRC");
    require(stats.items_published + stats.ring_overruns + transport.queue_overflows() == transport.sent(), "every packet is accounted for");
    require(allocs == 0 || !LUMA_ALLOC_PROBE, "no allocation in the hot path");
    if (a.check) {
        require(lost == 0, "nothing lost");
        require(sink.total.percentile(99) < 500'000, "p99 under 0.5 ms");
        require(cpu_pct < 1.0, "under 1% of a core");
    }
    return failed ? 1 : 0;
}
