// D2's gate: the renderer's PoseFeed (spike/src/pose.cpp, compiled in) reading a luma::Publisher that writes 1,000 poses a
// second, as the live driver does with --pose-seqlock. Each scenario runs the writer, the reader and, with contention, a third
// thread that holds the mutex feed's lock 3 ms in every 10 (a slow or stopped XRLinuxDriver-style reader, the case the mutex
// path waits on). Everything lives under a private prefix and a channel named for this process: the live feed is never touched.
//
//   luma_pose_channel_bench [--seconds N]   10 s a scenario; the gate, timing included
//   luma_pose_channel_bench --quick         2 s; for ctest: asserts zero waits and the fallback, nothing about timing
//
// Pose age is the reader's: read time minus the sample's CLOCK_MONOTONIC time (PoseFeed::stats, from the seqlock reads). The
// gate: p99 <= 1.5 ms and zero reader waits on the seqlock scenarios, and the mutex feed still served when the channel is absent.
// The writer thread takes the driver's own thread policy (rt::apply_thread_policy: FIFO if permitted, else nice and the short
// slice), as the daemon's does. The reader is left as the renderer leaves its thread. Age is the writer's lateness plus the
// read's place between two samples, so on a busy machine without real-time the timing can miss: the line says which policy ran,
// and how late the writer stamped against its 1 ms deadlines. "Behind" counts reads that got an older sample than one the writer
// had finished publishing before the read began: the handoff's own lag, which must be 0 whatever the load.
#include "luma/publish.hpp"
#include "luma/rt.hpp"
#include "xr/pose.hpp"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <pthread.h>
#include <string>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <thread>
#include <unistd.h>

namespace {
namespace fs = std::filesystem;
using luma::rt::LatencyHistogram;
constexpr std::uint64_t kMs = 1'000'000;

std::uint64_t mono_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
void sleep_until(std::uint64_t ns) {
    const timespec at{time_t(ns / 1'000'000'000ull), long(ns % 1'000'000'000ull)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &at, nullptr) == EINTR) {}
}

struct Scenario { const char* name; bool channel; unsigned reader_hz; bool contend; bool gated; };
struct Result {
    std::uint64_t reads = 0, empty = 0, written = 0, busy = 0, behind = 0;
    LatencyHistogram writer_late;  // stamp minus deadline
    std::string writer_sched;
    xr::PoseReaderStats reader;
    LatencyHistogram mutex_age;  // the mutex-only scenario's age, from the publisher's known clock epoch
};

Result run(const Scenario& sc, double seconds) {
    const std::string dir = [] {
        std::string name = (fs::temp_directory_path() / "luma_pose_bench_XXXXXX").string();
        if (!mkdtemp(name.data())) { std::perror("mkdtemp"); std::exit(2); }
        return name;
    }();
    const std::string channel = "/luma-pose-bench-" + std::to_string(getpid());
    luma::PublisherOptions options;  // the driver's own, private segments
    options.private_segments = true;
    options.history_step = 16;
    if (sc.channel) options.seqlock_name = channel;
    std::unique_ptr<luma::Publisher> publisher;
    std::string why;
    for (int attempt = 0; attempt < 1024 && !publisher; ++attempt) {
        options.prefix = dir + "/" + std::to_string(attempt) + "_";
        publisher = luma::Publisher::open(options, why);
        if (!publisher && why.find("key in use") == std::string::npos) { std::fprintf(stderr, "open: %s\n", why.c_str()); std::exit(2); }
    }
    if (!publisher) { std::fprintf(stderr, "no free key\n"); std::exit(2); }
    auto feed = xr::PoseFeed::open(options.prefix, why, channel);  // the renderer always asks for the channel
    if (!feed) { std::fprintf(stderr, "feed: %s\n", why.c_str()); std::exit(2); }

    Result r;
    std::atomic<bool> stop{false};
    std::atomic<std::uint64_t> first_t{0}, published_t{0};
    std::thread writer([&] {
        const auto policy = luma::rt::apply_thread_policy({});
        r.writer_sched = policy.sched == luma::rt::SchedClass::fifo   ? "fifo " + std::to_string(policy.priority)
                         : policy.sched == luma::rt::SchedClass::nice ? "nice " + std::to_string(policy.nice)
                                                                      : std::string("normal");
        if (policy.short_slice) r.writer_sched += " short-slice";
        std::uint64_t next = mono_ns();
        for (std::uint32_t seq = 0; !stop.load(std::memory_order_relaxed); ++seq) {
            next += kMs;
            sleep_until(next);
            const std::uint64_t t = mono_ns();  // as the daemon stamps a reaped URB
            r.writer_late.record(t - next);
            if (!first_t.load(std::memory_order_relaxed)) first_t.store(t);
            const float a = 0.001f * float(seq % 6283);
            publisher->publish({luma::Quatf{std::cos(a / 2), 0, 0, std::sin(a / 2)}, t, seq, true});
            published_t.store(t, std::memory_order_release);
        }
    });
    std::thread holder;
    if (sc.contend) {
        const int id = shmget(ftok((options.prefix + "pose_orientation_mutex").c_str(), 0), 0, 0);
        void* addr = id < 0 ? reinterpret_cast<void*>(-1) : shmat(id, nullptr, 0);
        if (addr == reinterpret_cast<void*>(-1)) { std::perror("shmat mutex"); std::exit(2); }
        holder = std::thread([&stop, addr] {
            auto* mutex = static_cast<pthread_mutex_t*>(addr);
            std::uint64_t next = mono_ns();
            while (!stop.load(std::memory_order_relaxed)) {
                pthread_mutex_lock(mutex);
                sleep_until(mono_ns() + 3 * kMs);
                pthread_mutex_unlock(mutex);
                next += 10 * kMs;
                sleep_until(next);
            }
            shmdt(addr);
        });
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));  // the history fills (instant start serves at once anyway)
    const std::uint64_t end = mono_ns() + std::uint64_t(seconds * 1e9);
    std::uint64_t next = mono_ns();
    while (mono_ns() < end) {
        if (sc.reader_hz) { next += 1'000'000'000ull / sc.reader_hz; sleep_until(next); }
        const std::uint64_t done = published_t.load(std::memory_order_acquire);
        const auto sample = feed->read();
        const std::uint64_t now = mono_ns();
        ++r.reads;
        if (!sample) { ++r.empty; continue; }
        if (sample->sample_ns && sample->sample_ns < done) ++r.behind;
        if (!sample->sample_ns && first_t.load()) {
            const double t = double(first_t.load()) + double(sample->device_ms) * 1e6;
            r.mutex_age.record(now > t ? std::uint64_t(double(now) - t) : 0);
        }
    }
    stop = true;
    writer.join();
    if (holder.joinable()) holder.join();
    r.reader = feed->stats();
    r.written = publisher->stats().seqlock_written;
    r.busy = publisher->stats().busy;
    feed.reset();
    publisher.reset();
    shm_unlink(channel.c_str());
    std::error_code ignored;
    fs::remove_all(dir, ignored);
    return r;
}
double ms(std::uint64_t ns) { return double(ns) / 1e6; }
}

int main(int argc, char** argv) {
    double seconds = 10;
    bool quick = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--quick") { seconds = 2; quick = true; }
        else if (arg == "--seconds" && i + 1 < argc) seconds = std::atof(argv[++i]);
        else { std::fprintf(stderr, "usage: %s [--seconds N] [--quick]\n", argv[0]); return 2; }
    }
    if (!(seconds > 0)) return 2;
    const Scenario scenarios[] = {
        {"seqlock, 120 Hz reader, mutex held 3 ms in 10", true, 120, true, true},
        {"seqlock, reader flat out, mutex held 3 ms in 10", true, 0, true, true},
        {"seqlock, 120 Hz reader, no contention", true, 120, false, true},
        {"channel absent (mutex feed), 120 Hz reader, mutex held 3 ms in 10", false, 120, true, false},
    };
    bool pass = true;
    std::printf("writer 1000 Hz, %.1f s per scenario%s; pose age = read time - sample CLOCK_MONOTONIC time\n", seconds,
                quick ? ", timing not gated (--quick)" : "");
    for (const auto& sc : scenarios) {
        const Result r = run(sc, seconds);
        const auto& s = r.reader;
        std::printf("\n%s\n  reads %llu (seqlock %llu, mutex %llu, none %llu), reader waits %llu, seqlock retries %llu, writer busy-skips %llu, writer %s\n",
                    sc.name, (unsigned long long)r.reads, (unsigned long long)s.seqlock_reads, (unsigned long long)s.mutex_reads,
                    (unsigned long long)r.empty, (unsigned long long)s.waits, (unsigned long long)s.retries, (unsigned long long)r.busy, r.writer_sched.c_str());
        std::printf("  read() p99 %.1f us, max %.1f us; writer late p50 %.3f ms, p99 %.3f ms, max %.3f ms\n", double(s.read_p99_ns) / 1e3,
                    double(s.read_max_ns) / 1e3, ms(r.writer_late.percentile(50)), ms(r.writer_late.percentile(99)), ms(r.writer_late.max()));
        if (sc.channel)
            std::printf("  pose age (seqlock) n=%llu p50 %.3f ms, p95 %.3f ms, p99 %.3f ms, max %.3f ms; behind %llu\n",
                        (unsigned long long)s.age_count, ms(s.age_p50_ns), ms(s.age_p95_ns), ms(s.age_p99_ns), ms(s.age_max_ns),
                        (unsigned long long)r.behind);
        else
            std::printf("  pose age (mutex feed) n=%llu p50 %.3f ms, p95 %.3f ms, p99 %.3f ms, max %.3f ms\n",
                        (unsigned long long)r.mutex_age.count(), ms(r.mutex_age.percentile(50)), ms(r.mutex_age.percentile(95)),
                        ms(r.mutex_age.percentile(99)), ms(r.mutex_age.max()));
        bool ok;
        if (sc.gated) ok = s.age_count > 0 && s.seqlock_reads == r.reads && s.waits == 0 && r.behind == 0 && (quick || ms(s.age_p99_ns) <= 1.5);
        else ok = !s.seqlock_attached && s.seqlock_reads == 0 && s.mutex_reads > 0;  // the fallback serves samples
        std::printf("  %s\n", ok ? "PASS" : "FAIL");
        pass = pass && ok;
    }
    std::printf("\nGATE %s: pose age p99 <= 1.5 ms at 1 kHz, zero reader waits, mutex feed served when the channel is absent\n",
                pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
