// luma_vsync_bench: the offline proof of rust-rebuild-plan D5. It makes a recording of glasses whose panel scan-out times
// are known, plays it through the real daemon (ReplayLink, the real decoder with its display event parser, VsyncClock) and
// scores what the daemon publishes the way the renderer does against wp_presentation (xr::VsyncMeter, spike/src/predict.cpp),
// and also against the truth, which only a simulation has.
//
//   luma_vsync_bench [--seconds N] [--seed N] [--write FILE.lcap] [--quiet]
//
// The recording, a LUMACAP0 file (daemon.hpp load_capture; --write keeps it for xr-luma-driver --replay --read-events):
//   device clock   100 us ticks, 65 ppm fast against the host, booted long before the host's clock origin
//   IMU            1 kHz on the device clock
//   panel          60 Hz for the first quarter, then 1.52 s with no events (a mode change), then 120 Hz; at half time the
//                  glasses drop off the bus for 400 ms and come back with their clock started again and a new phase;
//                  0.5% of events are lost
//   arrival        each packet reaches the host 150 us after it was stamped, plus 0 to 500 us, plus a tail (one in ten,
//                  exponential, mean 300 us), plus a stall of 2 to 5 ms one time in 200; a stall holds back what follows,
//                  as on a real bus
// The renderer is presented at every true scan-out on the host clock with 20 us of jitter (a DRM timestamp's), and reads the
// newest published state. Pass: the horizon error p95 about its offset (what the live check measures) is at most 1 ms, the
// error against the truth also, and the clock locked again after the mode change and after the reconnect.
#include "luma/daemon.hpp"
#include "luma/protocol.hpp"
#include "luma/vsync.hpp"
#include "luma/wiring.hpp"
#include "xr/pose.hpp"
#include "xr/predict.hpp"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

using namespace luma;
namespace {
constexpr std::uint64_t kMs = 1'000'000;
constexpr double kPpm = 65e-6;
constexpr std::uint64_t kHostStart = 5'000 * kMs;  // the recording starts 5 s after the host's clock origin

// One run of the device clock: device = start_device + (host - start_host) * (1 + ppm).
struct DeviceClock {
    std::uint64_t start_host, start_device;
    double device_at(double host) const { return double(start_device) + (host - double(start_host)) * (1 + kPpm); }
    double host_at(double device) const { return double(start_host) + (device - double(start_device)) / (1 + kPpm); }
};

struct Bus {
    std::mt19937_64 rng;
    double last_imu = 0, last_event = 0;
    double latency() {
        std::uniform_real_distribution<double> u(0, 1);
        double ns = 150e3 + u(rng) * 500e3;
        if (u(rng) < 0.1) ns += -std::log(1 - u(rng)) * 300e3;
        if (u(rng) < 0.005) ns += 2e6 + u(rng) * 3e6;
        return ns;
    }
    double arrive(double sent, double& last) { return last = std::max(sent + latency(), last + 10e3); }
};

struct Truth { double host_ns; };
struct Recording { std::vector<CaptureRecord> records; std::vector<Truth> scanouts; };

Recording make(std::uint64_t seconds, std::uint64_t seed) {
    Recording out;
    Bus bus{std::mt19937_64(seed)};
    std::uniform_real_distribution<double> u(0, 1);
    const std::uint64_t end = kHostStart + seconds * 1'000 * kMs;
    const std::uint64_t mode_change = kHostStart + seconds * 250 * kMs, mode_back = mode_change + 1'520 * kMs;
    const std::uint64_t gone = kHostStart + seconds * 500 * kMs, back = gone + 400 * kMs;
    const auto ticks = [](double device_ns) { return std::uint32_t(std::uint64_t(device_ns) / kLumaTickNs); };
    const auto packet = [&](double at, CaptureKind kind, auto bytes) {
        out.records.push_back({std::uint64_t(at), kind, std::vector<std::uint8_t>(bytes.begin(), bytes.end())});
    };

    for (int run = 0; run < 2; ++run) {
        const DeviceClock clock = run == 0 ? DeviceClock{kHostStart, 3'600'000 * kMs} : DeviceClock{back, 250 * kMs};
        const std::uint64_t from = run == 0 ? kHostStart : back, to = run == 0 ? gone : end;
        bus.last_imu = bus.last_event = double(from);
        // IMU at 1 kHz of device time.
        std::uint32_t seq = 0;
        for (double d = clock.device_at(double(from)); clock.host_at(d) < double(to); d += 1e6) {
            ImuSample s{std::uint64_t(d), {0, 0, 0}, {0, 0, 9.81f}, std::nullopt, seq++};
            packet(bus.arrive(clock.host_at(d), bus.last_imu), CaptureKind::from_device, encode_luma_imu(s));
        }
        // The panel, on the device clock: 60 Hz then (first run) a silent mode change and 120 Hz.
        double phase = clock.device_at(double(from)) + (run == 0 ? 3.1e6 : 5.7e6);
        double period = 1e9 / (run == 0 ? 60.0 : 120.0);
        for (double d = phase; clock.host_at(d) < double(to); d += period) {
            const double host = clock.host_at(d);
            if (run == 0 && host >= double(mode_change) && host < double(mode_back)) continue;
            if (run == 0 && host >= double(mode_back) && period > 1e7) {
                period = 1e9 / 120.0;
                d = clock.device_at(double(mode_back)) + 1.3e6 - period;
                continue;
            }
            out.scanouts.push_back({host});
            if (u(bus.rng) < 0.005) continue;  // lost on the bus
            packet(bus.arrive(host, bus.last_event), CaptureKind::from_device, encode_luma_display_event({0, ticks(d)}));
        }
        if (run == 0) {
            out.records.push_back({gone, CaptureKind::device_gone, {}});
            out.records.push_back({back, CaptureKind::device_back, {}});
        }
    }
    std::stable_sort(out.records.begin(), out.records.end(), [](const CaptureRecord& a, const CaptureRecord& b) { return a.t_ns < b.t_ns; });
    std::sort(out.scanouts.begin(), out.scanouts.end(), [](Truth a, Truth b) { return a.host_ns < b.host_ns; });
    return out;
}

struct Published { std::uint64_t at_ns; VsyncState state; };
class CaptureSink final : public Sink {
public:
    explicit CaptureSink(const ReplayLink& link) : link_(link) {}
    void publish(const PoseOut&, const PoseSource&) override {}
    void set_stale(bool) override {}
    void publish_vsync(const VsyncState& s) override { published.push_back({link_.now_ns(), s}); }
    std::vector<Published> published;
private:
    const ReplayLink& link_;
};

double percentile(std::vector<double> v, double q) {
    if (v.empty()) return NAN;
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, std::size_t(q * double(v.size())))];
}
}

int main(int argc, char** argv) {
    std::uint64_t seconds = 30, seed = 1;
    std::string write;
    bool quiet = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--seconds" && i + 1 < argc) seconds = std::strtoull(argv[++i], nullptr, 10);
        else if (a == "--seed" && i + 1 < argc) seed = std::strtoull(argv[++i], nullptr, 10);
        else if (a == "--write" && i + 1 < argc) write = argv[++i];
        else if (a == "--quiet") quiet = true;
        else { std::fprintf(stderr, "usage: luma_vsync_bench [--seconds N] [--seed N] [--write FILE.lcap] [--quiet]\n"); return 2; }
    }
    if (seconds < 8) { std::fprintf(stderr, "luma_vsync_bench: --seconds must be at least 8\n"); return 2; }
    Recording rec = make(seconds, seed);
    if (!write.empty() && !save_capture(write, rec.records)) { std::fprintf(stderr, "cannot write %s\n", write.c_str()); return 2; }

    ReplayLink link(std::move(rec.records), false);
    CaptureSink sink(link);
    const auto decoder = wiring::decoder();
    std::vector<std::string> lines;
    DaemonParts parts{link, *decoder, &wiring::estimator, sink, [&] { return link.now_ns(); },
                      [&](std::string_view l) { lines.emplace_back(l); }, wiring::start_command(), wiring::stop_command()};
    DaemonConfig config;
    config.vsync = true;
    Stats stats;
    {
        Daemon daemon(std::move(parts), config);
        std::atomic<bool> stop{false};
        daemon.run(stop);
        stats = daemon.stats();
    }
    if (!quiet) for (const std::string& l : lines) std::printf("%s\n", l.c_str());

    // The renderer at every scan-out, reading the newest state published before it.
    std::mt19937_64 rng(seed ^ 0x5eed);
    std::normal_distribution<double> drm(0, 20e3);
    xr::VsyncMeter meter;
    std::vector<double> truth_us;
    std::size_t next = 0;
    const VsyncState* newest = nullptr;
    std::uint64_t relocks = 0;
    bool was_locked = false;
    for (const Truth& t : rec.scanouts) {
        const std::uint64_t present = std::uint64_t(t.host_ns + drm(rng));
        while (next < sink.published.size() && sink.published[next].at_ns <= present) {
            newest = &sink.published[next++].state;
            if (newest->locked && !was_locked) ++relocks;
            was_locked = newest->locked;
        }
        std::optional<xr::VsyncSnapshot> snap;
        if (newest && newest->locked)
            snap = xr::VsyncSnapshot{newest->anchor_host_ns, newest->period_ns, NAN, newest->events, newest->resets, newest->fit_rms_us};
        meter.presented(snap, present, -1);
        if (snap) {
            const double age = double(std::int64_t(std::uint64_t(t.host_ns) - snap->anchor_host_ns));
            if (std::fabs(age) < xr::VsyncMeter::kMaxAnchorAgeNs)
                truth_us.push_back(std::fabs(age - std::round(age / snap->period_ns) * snap->period_ns) / 1e3);
        }
    }
    const xr::VsyncSummary s = meter.summary();
    const double truth_p95_ms = percentile(truth_us, 0.95) / 1e3, truth_max_ms = percentile(truth_us, 1.0) / 1e3;
    const VsyncState last = sink.published.empty() ? VsyncState{} : sink.published.back().state;
    std::printf("vsync bench: %llu s, seed %llu, %zu scan-outs, %llu display events read, %zu states published, %llu locks, %u resets\n",
                (unsigned long long)seconds, (unsigned long long)seed, rec.scanouts.size(), (unsigned long long)stats.display_events,
                sink.published.size(), (unsigned long long)relocks, last.resets);
    std::printf("  scored %llu frames, %llu without a usable state; last period %.4f ms, fit rms %.1f us\n",
                (unsigned long long)s.count, (unsigned long long)s.skipped, s.period_ms, double(last.fit_rms_us));
    std::printf("  horizon error about offset %.3f ms: p50 %.3f p95 %.3f max %.3f ms\n", s.offset_ms, s.error_p50_ms, s.error_p95_ms, s.error_max_ms);
    std::printf("  against truth: p95 %.3f max %.3f ms\n", truth_p95_ms, truth_max_ms);
    // Three locks: at 60 Hz, at 120 Hz after the mode change, and after the reconnect. Most frames scored: the gaps are
    // the mode change, the outage and the few frames each lock takes.
    const bool pass = s.count > rec.scanouts.size() * 8 / 10 && relocks >= 3 && s.error_p95_ms <= 1.0 && truth_p95_ms <= 1.0;
    std::printf("vsync bench: %s (gate: horizon error p95 <= 1 ms)\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
