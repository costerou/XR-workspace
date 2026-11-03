// Head-tracking benchmark (offline; no glasses, no live driver feed). Measures, through the real
// library code:
//   cpu     per-frame tracking step (the calls wayland.cpp makes each frame), feedback callbacks,
//           the 1 Hz status line; median and p99 ns per call
//   alloc   heap allocations per 10k frames / feedback callbacks (operator new is counted)
//   stall   PoseFeed::read under a 1 kHz writer, after the driver died holding the lock (robust
//           mutex: EOWNERDEAD), and while a live driver holds the lock too long
//   predict prediction error and jitter on simulated head motion (tracking_sim.hpp)
// Usage: tracking_bench [--quick]   (--quick: shorter runs; the ctest smoke target)
#include "fake_feed.hpp"
#include "tracking_sim.hpp"
#include "xr/metrics.hpp"
#include "xr/predict.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <new>
#include <pthread.h>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
std::atomic<std::uint64_t> g_allocs{0};
}
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_allocs.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
// noinline (new and delete): GCC otherwise pairs the inlined malloc/free with new/delete and flags a mismatch.
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

using namespace xr;
namespace {
bool quick = false;
int failures = 0;
// The per-phase estimate is in use (libraries without it, such as the baseline for comparisons, pass).
template <class L> bool phase_engaged(const L& latency) {
    if constexpr (requires { latency.phase_aware(); }) return latency.phase_aware();
    else return true;
}
void require(bool ok, const char* what) { if (!ok) { std::fprintf(stderr, "FAILED: %s\n", what); ++failures; } }
std::uint64_t now_ns() {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
struct Stats { double p50 = 0, p99 = 0, max = 0, mean = 0; };
Stats stats(std::vector<double> v) {
    Stats s;
    if (v.empty()) return s;
    std::sort(v.begin(), v.end());
    s.p50 = v[v.size() / 2]; s.p99 = v[std::size_t(0.99 * double(v.size() - 1))]; s.max = v.back();
    for (double x : v) s.mean += x;
    s.mean /= double(v.size());
    return s;
}
double timer_overhead_ns() {
    std::vector<double> v;
    for (int i = 0; i < 20000; ++i) { const auto a = now_ns(); const auto b = now_ns(); v.push_back(double(b - a)); }
    return stats(v).p50;
}

// SysV feed laid out like XRLinuxDriver (robust mutex, IPC_EXCL segments: fake_feed.hpp).
struct Feed : test::FakeFeed {
    explicit Feed(const std::string& prefix) : FakeFeed(prefix, 46.f) { write(0, 1000); }
    using FakeFeed::write;
    void write(float yaw_rad, float ms) {  // q0, q1 (16 ms older), q2 (32 ms older), timestamps
        float v[16];
        for (int i = 0; i < 3; ++i) {
            const Quat q = sim::axis_angle(0, 0, 1, yaw_rad - 0.0032f * float(i));
            v[i * 4] = q.x; v[i * 4 + 1] = q.y; v[i * 4 + 2] = q.z; v[i * 4 + 3] = q.w;
        }
        v[12] = ms; v[13] = ms - 16; v[14] = ms - 32; v[15] = 0;
        write(v);
    }
};

// The renderer's per-frame tracking work, as in wayland.cpp draw_test_pattern (prediction on, follow mode).
struct Renderer {
    std::unique_ptr<PoseFeed> feed;
    PoseState pose; ModeController modes; LatencyTracker latency; PoseAge age; Metrics metrics;
    Mat3 head{}, world{};
    explicit Renderer(const std::string& prefix) {
        std::string why;
        feed = PoseFeed::open(prefix, why);
        if (!feed) { std::fprintf(stderr, "feed: %s\n", why.c_str()); std::exit(2); }
        metrics.bind(1); metrics.clock(CLOCK_MONOTONIC); latency.clock(1);
        modes.set_mode(ViewMode::follow, 0);
    }
    void frame(std::uint64_t read_ns, double now) {
        const auto sample = feed->read();
        pose.update(sample, now);
        Quat relative = pose.relative();
        const auto a = sample ? age.observe(double(read_ns) / 1e6, sample->device_ms) : std::nullopt;
        if (pose.tracking()) {
            const auto omega = sample ? angular_velocity(history_of(*sample)) : std::nullopt;
            const auto refresh = metrics.snapshot().last_refresh_prediction_ns;
            const auto horizon = prediction_horizon(look_ahead_from(feed->look_ahead_cfg().value_or(std::array<float, 4>{})),
                                                    sim::expected(latency, read_ns), a.value_or(0.0), refresh ? refresh / 1e6 : 1000.0 / 120);
            if (omega && horizon) {
                const Quat delta = prediction_delta(*omega, *horizon, 15.f);
                relative = quat_mul(relative, delta);
                volatile float deg = quat_angle(Quat{}, delta); (void)deg;
            }
        }
        head = view_from(relative, modes.update(relative, now));
        world = view_from(relative, Quat{});
    }
    // Presentation feedback as the compositor delivers it: frame i is presented on the 120 Hz vblank
    // grid two refreshes after its read, and that feedback arrives before the read that follows the
    // vblank. Reads land 1..3.5 ms after their vblank, so the per-phase latency estimate engages.
    static constexpr std::uint64_t kPeriodNs = 8'333'333;
    struct Inflight { std::uint64_t ticket, present_ns; };
    std::array<Inflight, 8> inflight{};
    std::size_t inflight_n = 0;
    std::uint32_t sequence = 0;
    static std::uint64_t read_time(std::uint64_t i) {
        return 1'000'000'000ull + i * kPeriodNs + 1'000'000ull + std::uint64_t(2'500'000.0 * std::fmod(double(i) * 0.618034, 1.0));
    }
    void deliver(std::uint64_t now_ns) {  // presented callbacks due before this read
        std::size_t kept = 0;
        for (std::size_t j = 0; j < inflight_n; ++j) {
            const auto& f = inflight[j];
            if (f.present_ns > now_ns) { inflight[kept++] = f; continue; }
            metrics.presented(f.ticket, std::uint32_t((f.present_ns / 1'000'000'000ull) >> 32), std::uint32_t(f.present_ns / 1'000'000'000ull),
                              std::uint32_t(f.present_ns % 1'000'000'000ull), std::uint32_t(kPeriodNs), 0, ++sequence, 0xf, true);
            latency.presented(f.ticket, f.present_ns);
        }
        inflight_n = kept;
    }
    void submit(std::uint64_t i, std::uint64_t read_ns) {  // at commit
        const auto ticket = *metrics.submit();
        latency.read(ticket, read_ns);
        inflight[inflight_n++] = {ticket, 1'000'000'000ull + (i + 2) * kPeriodNs};
    }
};

void bench_cpu(const std::string& prefix) {
    Feed f(prefix);
    Renderer r(prefix);
    const double overhead = timer_overhead_ns();
    const int n = quick ? 20000 : 200000;
    std::vector<double> read_v, frame_v, fb_v, status_v;
    read_v.reserve(std::size_t(n)); frame_v.reserve(std::size_t(n)); fb_v.reserve(std::size_t(n));
    std::uint64_t t = 0;
    for (int i = 0; i < n; ++i) {
        t = Renderer::read_time(std::uint64_t(i));
        f.write(0.001f * float(i % 1000), 1000.f + float(i % 100000));  // a new sample every frame
        const auto a = now_ns(); volatile bool ok = r.feed->read().has_value(); (void)ok;
        const auto b = now_ns(); r.frame(t, double(t) / 1e6);
        const auto c = now_ns(); r.submit(std::uint64_t(i), t); r.deliver(Renderer::read_time(std::uint64_t(i) + 1));
        const auto d = now_ns();
        read_v.push_back(double(b - a) - overhead); frame_v.push_back(double(c - b) - overhead); fb_v.push_back(double(d - c) - overhead);
    }
    require(phase_engaged(r.latency), "the per-phase latency estimate engaged in the cpu run");
    for (int i = 0; i < (quick ? 200 : 2000); ++i) {
        const auto a = now_ns(); volatile auto len = r.metrics.status_json(double(t) / 1e6).size(); (void)len;
        status_v.push_back(double(now_ns() - a) - overhead);
    }
    auto line = [](const char* what, const Stats& s) {
        std::printf("cpu   %-34s p50 %8.0f ns  p99 %8.0f ns  max %9.0f ns\n", what, s.p50, s.p99, s.max);
    };
    line("PoseFeed::read (uncontended)", stats(read_v));
    line("tracking frame (read..view_from x2)", stats(frame_v));
    line("feedback (submit+presented)", stats(fb_v));
    line("Metrics::status_json (1 Hz)", stats(status_v));
}

void bench_alloc(const std::string& prefix) {
    Feed f(prefix);
    Renderer r(prefix);
    // Frames as in production (frame, commit, later feedback); allocations counted separately for the
    // tracking step and the feedback path, after a warm-up that engages the per-phase estimate.
    std::uint64_t frame_allocs = 0, fb_allocs = 0, t = 0;
    for (std::uint64_t i = 0; i < 12000; ++i) {
        t = Renderer::read_time(i);
        f.write(0.001f * float(i % 1000), 1000.f + float(i));
        auto before = g_allocs.load();
        r.frame(t, double(t) / 1e6);
        if (i >= 2000) frame_allocs += g_allocs.load() - before;
        before = g_allocs.load();
        r.submit(i, t); r.deliver(Renderer::read_time(i + 1));
        if (i >= 2000) fb_allocs += g_allocs.load() - before;
    }
    require(phase_engaged(r.latency), "the per-phase latency estimate engaged in the alloc run");
    const auto before = g_allocs.load();
    for (int i = 0; i < 100; ++i) { volatile auto len = r.metrics.status_json(double(t) / 1e6).size(); (void)len; }
    const auto status_allocs = g_allocs.load() - before;
    std::printf("alloc tracking frames   %6llu per 10k frames\n", (unsigned long long)frame_allocs);
    std::printf("alloc feedback          %6llu per 10k presentations\n", (unsigned long long)fb_allocs);
    std::printf("alloc status_json       %6.1f per call (1 Hz)\n", double(status_allocs) / 100);
    require(frame_allocs == 0, "the per-frame tracking step does not allocate");
    require(fb_allocs == 0, "presentation feedback does not allocate");
}

void bench_stall(const std::string& prefix) {
    const double overhead = timer_overhead_ns();
    {  // a live driver writing at 1 kHz, holding the lock ~2 us per write (memcpy + gamescope uniform)
        Feed f(prefix);
        Renderer r(prefix);
        std::atomic<bool> stop{false};
        std::thread writer([&] {
            float ms = 2000;
            while (!stop.load()) {
                pthread_mutex_lock(f.mutex);
                const auto until = now_ns() + 2000;
                while (now_ns() < until) {}
                f.pose[12] = ms; ms += 1;
                pthread_mutex_unlock(f.mutex);
                std::this_thread::sleep_for(std::chrono::microseconds(1000));
            }
        });
        std::vector<double> v;
        for (int i = 0; i < (quick ? 400 : 4000); ++i) {
            const auto a = now_ns(); volatile bool ok = r.feed->read().has_value(); (void)ok;
            v.push_back(double(now_ns() - a) - overhead);
            std::this_thread::sleep_for(std::chrono::microseconds(250 + (i * 37) % 500));
        }
        stop = true; writer.join();
        const auto s = stats(v);
        std::printf("stall read, 1 kHz writer          p50 %8.0f ns  p99 %8.0f ns  max %9.0f ns\n", s.p50, s.p99, s.max);
    }
    {  // the driver dies while holding the lock (robust mutex: the next locker gets EOWNERDEAD)
        Feed f(prefix);
        Renderer r(prefix);
        const pid_t child = fork();
        if (child == 0) { pthread_mutex_lock(f.mutex); _exit(0); }
        int status = 0; waitpid(child, &status, 0);
        std::vector<double> v;
        int samples = 0;
        for (int i = 0; i < 20; ++i) {
            const auto a = now_ns(); const bool ok = r.feed->read().has_value();
            v.push_back(double(now_ns() - a) - overhead); samples += ok;
        }
        // Can the (restarted) driver publish again, or did the reader keep the lock?
        int free_rc = -1;
        std::thread probe([&] { free_rc = pthread_mutex_trylock(f.mutex); if (free_rc == 0 || free_rc == EOWNERDEAD) pthread_mutex_unlock(f.mutex); });
        probe.join();
        const auto s = stats(v);
        std::printf("stall read, driver died locked    mean %7.0f ns  max %9.0f ns  samples %d/20  lock free for driver: %s\n",
                    s.mean, s.max, samples, free_rc == 0 || free_rc == EOWNERDEAD ? "yes" : "NO");
        require(free_rc == 0, "a driver that died holding the lock leaves it free, not held by the reader");
        require(samples >= 19, "reads continue after the driver died holding the lock");
    }
    {  // a live driver holds the lock for 60 ms (stuck); the renderer keeps drawing frames
        Feed f(prefix);
        Renderer r(prefix);
        std::atomic<bool> locked{false};
        std::thread holder([&] { pthread_mutex_lock(f.mutex); locked = true; std::this_thread::sleep_for(std::chrono::milliseconds(60)); pthread_mutex_unlock(f.mutex); });
        while (!locked) std::this_thread::yield();
        std::vector<double> v;
        const auto start = now_ns();
        while (now_ns() - start < 50'000'000ull) {
            const auto a = now_ns(); volatile bool ok = r.feed->read().has_value(); (void)ok;
            v.push_back(double(now_ns() - a) - overhead);
            std::this_thread::sleep_for(std::chrono::microseconds(1000));
        }
        holder.join();
        double total = 0;
        for (double x : v) total += x;
        std::printf("stall read, lock held 60 ms       %zu reads, blocked %.2f ms total (%.0f us per read)\n", v.size(), total / 1e6, total / 1e3 / double(v.size()));
    }
    // 120 Hz frames against a driver that keeps the lock most of the time (98%: e.g. a slow consumer
    // inside the lock), and against one stopped while holding it (SIGSTOP, a debugger). Fresh: the frame
    // got a new device timestamp; longest stale run past 100 ms loses tracking (PoseState stale_ms).
    for (const bool stopped : {false, true}) {
        Feed f(prefix);
        Renderer r(prefix);
        r.feed->read();
        std::atomic<bool> stop{false}, started{false};
        std::thread holder([&] {
            float ms = 5000;
            while (!stop) {
                pthread_mutex_lock(f.mutex);
                started = true;
                f.pose[12] = ms; ms += 1;
                const auto until = now_ns() + (stopped ? 600'000'000ull : 980'000ull);
                while (now_ns() < until && !(stopped && stop)) {}
                pthread_mutex_unlock(f.mutex);
                const auto gap = now_ns() + 20'000;
                while (now_ns() < gap) {}
            }
        });
        while (!started) std::this_thread::yield();
        const int frames = quick ? 60 : 120;
        int fresh = 0, run = 0, longest = 0;
        float last_ms = -1;
        double blocked = 0;
        for (int i = 0; i < frames; ++i) {
            const auto a = now_ns();
            const auto sample = r.feed->read();
            blocked += double(now_ns() - a);
            const bool is_fresh = sample && sample->device_ms != last_ms;
            if (sample) last_ms = sample->device_ms;
            fresh += is_fresh; run = is_fresh ? 0 : run + 1; longest = std::max(longest, run);
            const auto next = a + 8'333'333ull;
            while (now_ns() < next) std::this_thread::sleep_for(std::chrono::microseconds(200));
        }
        stop = true; holder.join();
        std::printf("stall read, %s %3d/%d frames fresh, longest stale run %5.1f ms, blocked %4.0f us per frame\n",
                    stopped ? "driver stopped locked " : "driver busy (98% held)", fresh, frames, longest * 8.333, blocked / 1e3 / frames);
    }
}

void bench_predict() {
    // Device: "luma" = Luma Ultra (Carina path, 1 kHz, flip-prone ms timestamps); "gen" = other VITURE
    // models (whole-ms device timestamps at the given IMU rate). read: pose read jitter after vblank.
    struct Case { const char* device; sim::Motion m; double hz, boot, drift, read_jitter, late_p; };
    std::vector<Case> cases = {
        {"luma", sim::Motion::rest, 1000, 1e6, 0, 1.0, 0.1},  {"luma", sim::Motion::sine, 1000, 1e6, 0, 1.0, 0.1},
        {"luma", sim::Motion::turns, 1000, 1e6, 0, 1.0, 0.1}, {"luma", sim::Motion::fast, 1000, 1e6, 0, 1.0, 0.1},
        {"luma", sim::Motion::turns, 1000, 1e6, 0, 0.2, 0.0}, {"luma", sim::Motion::turns, 1000, 1e6, 0, 2.5, 0.3},
        {"luma", sim::Motion::turns, 1000, 1e6, -50, 1.0, 0.1}, {"luma", sim::Motion::turns, 1000, 1e6, 50, 1.0, 0.1},
        {"gen", sim::Motion::turns, 1000, 1e6, 0, 1.0, 0.1},  {"gen", sim::Motion::turns, 500, 1e6, 0, 1.0, 0.1},
        {"gen", sim::Motion::turns, 240, 1e6, 0, 1.0, 0.1},   {"gen", sim::Motion::turns, 120, 1e6, 0, 1.0, 0.1},
        {"gen", sim::Motion::turns, 90, 1e6, 0, 1.0, 0.1},    {"gen", sim::Motion::turns, 60, 1e6, 0, 1.0, 0.1},
        {"gen", sim::Motion::turns, 1000, 2e7, 0, 1.0, 0.1},  {"gen", sim::Motion::sine, 240, 2e7, 0, 1.0, 0.1},
    };
    std::printf("predict %-4s %-6s %5s %7s %4s %4s | %7s %7s %7s %7s | %8s %7s | %7s\n", "dev", "motion", "imu", "boot_ms", "ppm",
                "read", "err_rms", "err_p95", "err_max", "raw_rms", "jitter", "jit_px", "horizon");
    double sum_rms = 0, sum_jit = 0;
    for (const auto& c : cases) {
        sim::Config cfg; cfg.motion = c.m; cfg.imu_hz = c.hz; cfg.boot_ms = c.boot; cfg.drift_ppm = c.drift;
        cfg.carina = std::string(c.device) == "luma"; cfg.read_jitter_ms = c.read_jitter; cfg.late_p = c.late_p;
        cfg.seconds = quick ? 6 : (c.drift != 0 ? 300 : 30);
        const auto r = sim::run(cfg);
        sum_rms += r.err_rms; sum_jit += r.jitter_rms;
        std::printf("predict %-4s %-6s %5.0f %7.0e %4.0f %4.1f | %7.4f %7.4f %7.4f %7.4f | %8.5f %7.3f | %7.2f\n", c.device, sim::name(c.m),
                    c.hz, c.boot, c.drift, c.read_jitter, r.err_rms, r.err_p95, r.err_max, r.raw_rms, r.jitter_rms,
                    r.jitter_rms * sim::kPxPerDeg, r.horizon_mean);
    }
    std::printf("predict mean over cases: err_rms %.4f deg, jitter %.5f deg (%.3f px)\n", sum_rms / double(cases.size()),
                sum_jit / double(cases.size()), sum_jit / double(cases.size()) * sim::kPxPerDeg);
}
}

int main(int argc, char** argv) {
    std::vector<std::string> only;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--quick") quick = true;
        else only.emplace_back(argv[i]);
    }
    auto want = [&](const char* s) { return only.empty() || std::find(only.begin(), only.end(), s) != only.end(); };
    const std::string prefix = "/tmp/xr_tracking_bench_" + std::to_string(getpid()) + "_";
    if (want("cpu")) bench_cpu(prefix);
    if (want("alloc")) bench_alloc(prefix);
    if (want("stall")) bench_stall(prefix);
    if (want("predict")) bench_predict();
    return failures ? 1 : 0;
}
