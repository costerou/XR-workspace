// D3 of docs/rust-rebuild-plan.md, the renderer's half: it predicts from PredictionWire instead of differencing the feed's q0 and q1.
//
// Replays tests/fixtures/luma_imu_real.bin (argv[1]: 8 s of a real Luma Ultra at 1 kHz, resting, turning about 60 degrees and
// back) through the real Daemon, Fusion and feed sink on private segments. After every published sample it reads the feed the
// way the renderer's frame loop does (spike/src/wayland.cpp): xr::PoseFeed::read() and xr::MotionFeed::read(), the Stabilizer's rest gate,
// then either the old path (angular_velocity of history_of, prediction_delta) or the new one (motion_delta).
// Luma Ultra pixels are about 0.03 deg. Each prediction,
// h = 10, 17 and 25 ms ahead of the sample, is scored against the feed's own pose h ms later.
//
// Checks:
//   - every calibrated sample reaches the renderer as a fresh wire sample, whose pose is the feed's q0;
//   - while moving, the wire's RMS error is at most half the finite difference's at every horizon (the gate: 17 ms);
//   - at rest, the wire's own still gate (luma::PredictConfig::still_gate) keeps the prediction under 0.002 deg on average;
//     the stabilizer's gain stays on the finite difference only (wayland.cpp says why).
// It never opens a device or the live feed.
#include "luma/daemon.hpp"
#include "luma/protocol.hpp"
#include "luma/wiring.hpp"
#include "xr/motion_feed.hpp"
#include "xr/predict.hpp"
#include "xr/stabilization.hpp"
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {
int g_failures = 0;
#define CHECK(x) do { if (!(x)) { ++g_failures; std::cerr << "  FAILED " << __FILE__ << ':' << __LINE__ << ": " #x "\n"; } } while (0)
constexpr std::uint64_t kMs = 1'000'000;
constexpr double kDeg = 180 / 3.14159265358979323846;
constexpr float kMaxDeg = 15;  // Presentation::Impl::kMaxPredictionDeg

double angle_deg(xr::Quat a, xr::Quat b) {  // atan2, not acos: acos near 1 turns float rounding into hundredths of a degree
    const xr::Quat d = xr::quat_mul(xr::quat_conj(a), b);
    return 2 * std::atan2(std::sqrt(double(d.x) * d.x + double(d.y) * d.y + double(d.z) * d.z), std::fabs(double(d.w))) * kDeg;
}
double rms(const std::vector<double>& v) {
    double s = 0;
    for (double x : v) s += x * x;
    return v.empty() ? 0 : std::sqrt(s / double(v.size()));
}

// One renderer read right after a sample was published.
struct Frame {
    std::optional<xr::FeedSample> sample;
    std::optional<xr::FeedMotion> motion;
    std::uint64_t read_ns = 0;
    float gain = 1;  // the Stabilizer's prediction_gain after this sample
};

class Probe final : public luma::Sink {
public:
    Probe(std::unique_ptr<luma::Sink> inner, std::string prefix) : inner_(std::move(inner)), prefix_(std::move(prefix)) {}
    void publish(const luma::PoseOut& pose, const luma::PoseSource& source) override {
        inner_->publish(pose, source);
        if (!feed_) { std::string why; feed_ = xr::PoseFeed::open(prefix_, why); if (!feed_) std::cerr << "  open: " << why << "\n"; }
        if (!motion_feed_) motion_feed_ = xr::MotionFeed::open(prefix_);
        Frame f;
        f.read_ns = source.host_ns;  // read at once: age 0, so both paths predict exactly h ahead of the sample
        if (feed_) f.sample = feed_->read();
        if (motion_feed_) f.motion = motion_feed_->read();
        if (f.sample) { stabilizer_.update(f.sample->orientation, f.sample->device_ms); f.gain = stabilizer_.prediction_gain(); }
        frames.push_back(f);
    }
    void set_stale(bool stale) override { inner_->set_stale(stale); }
    std::vector<Frame> frames;
private:
    std::unique_ptr<luma::Sink> inner_;
    std::string prefix_;
    std::unique_ptr<xr::PoseFeed> feed_;
    std::unique_ptr<xr::MotionFeed> motion_feed_;
    xr::Stabilizer stabilizer_;
};

std::vector<std::array<std::uint8_t, luma::kLumaImuPacketSize>> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<std::array<std::uint8_t, luma::kLumaImuPacketSize>> packets;
    std::array<std::uint8_t, luma::kLumaImuPacketSize> p{};
    while (in.read(reinterpret_cast<char*>(p.data()), std::streamsize(p.size()))) packets.push_back(p);
    return packets;
}

void test_replay(const std::string& fixture, const std::filesystem::path& dir) {
    const auto packets = load(fixture);
    CHECK(packets.size() > 7000);
    if (packets.size() < 7000) return;
    std::vector<luma::CaptureRecord> records;
    records.push_back({0, luma::CaptureKind::to_device, {0x01}});
    for (std::size_t i = 0; i < packets.size(); ++i)
        records.push_back({(i + 1) * kMs, luma::CaptureKind::from_device, {packets[i].begin(), packets[i].end()}});

    const std::string prefix = (dir / "xr_").string();
    std::string why;
    auto sink = luma::wiring::feed_sink(prefix, why, true);
    CHECK(sink);
    if (!sink) { std::cerr << "  feed_sink: " << why << "\n"; return; }
    Probe probe(std::move(sink), prefix);
    luma::ReplayLink link(std::move(records), false);
    std::atomic<bool> stop{false};
    const auto decoder = luma::wiring::decoder();
    luma::DaemonParts parts{link, *decoder, &luma::wiring::estimator, probe, [&] { return link.now_ns(); },
                            [](std::string_view) {}, luma::wiring::start_command(), luma::wiring::stop_command()};
    luma::Daemon daemon(std::move(parts));
    CHECK(daemon.run(stop) == luma::DaemonEnd::replay_ended);
    const auto& frames = probe.frames;
    std::printf("  replay: %zu renderer reads\n", frames.size());
    CHECK(frames.size() > 5000);

    // The renderer sees the wire on every sample, fresh, on the feed's own pose.
    std::size_t fresh = 0;
    double q_worst = 0;
    for (const Frame& f : frames) {
        if (!f.sample || !f.motion) continue;
        if (xr::motion_age_ms(*f.motion, f.read_ns)) ++fresh;
        q_worst = std::max(q_worst, angle_deg(f.motion->q, f.sample->orientation));
    }
    std::printf("  fresh wire samples: %zu of %zu; wire pose against the feed's q0: worst %.2e deg\n", fresh, frames.size(), q_worst);
    CHECK(fresh == frames.size());
    CHECK(q_worst < 1e-3);

    for (const int h : {10, 17, 25}) {
        std::vector<double> wire_err, diff_err, hold_err, gained_err;
        double wire_rest = 0, diff_rest = 0;
        std::size_t rest = 0;
        for (std::size_t i = 40; i + std::size_t(h) < frames.size(); ++i) {
            const Frame& f = frames[i];
            const Frame& later = frames[i + std::size_t(h)];
            if (!f.sample || !f.motion || !later.sample || !later.motion) continue;
            if (later.motion->host_ns - f.motion->host_ns != std::uint64_t(h) * kMs) continue;  // a gap: no truth for this one
            const auto omega = xr::angular_velocity(xr::history_of(*f.sample));
            if (!omega) continue;
            // As wayland.cpp does: the stabilizer's gain shortens only the finite difference's horizon; the wire gates itself.
            const xr::Quat q0 = f.sample->orientation, truth = later.sample->orientation;
            const xr::Quat by_wire = xr::quat_mul(q0, xr::motion_delta(*f.motion, float(h), kMaxDeg));
            const xr::Quat by_diff = xr::quat_mul(q0, xr::prediction_delta(*omega, float(h) * f.gain, kMaxDeg));
            const xr::Quat by_wire_gained = xr::quat_mul(q0, xr::motion_delta(*f.motion, float(h) * f.gain, kMaxDeg));
            const auto& w = f.motion->omega_rad_s;
            if (std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]) >= 0.2f) {  // moving: above about 11 deg/s
                wire_err.push_back(angle_deg(by_wire, truth));
                diff_err.push_back(angle_deg(by_diff, truth));
                hold_err.push_back(angle_deg(q0, truth));
                gained_err.push_back(angle_deg(by_wire_gained, truth));
            } else if (angle_deg(q0, truth) < 0.05) {  // resting: what is predicted is only jitter
                wire_rest += angle_deg(by_wire, q0); diff_rest += angle_deg(by_diff, q0); ++rest;
            }
        }
        std::printf("  predict %2d ms while moving (%zu): rms error %.3f deg from the wire, %.3f deg differencing q0 and q1, "
                    "%.3f deg not predicting (%.3f deg from the wire under the stabilizer's gain); at rest (%zu) mean %.4f / %.4f deg predicted\n",
                    h, wire_err.size(), rms(wire_err), rms(diff_err), rms(hold_err), rms(gained_err), rest,
                    rest ? wire_rest / double(rest) : 0.0, rest ? diff_rest / double(rest) : 0.0);
        CHECK(wire_err.size() > 500);
        CHECK(rest > 500);
        CHECK(rms(wire_err) <= 0.5 * rms(diff_err));  // the plan's D3 gate, at every horizon
        CHECK(wire_rest / double(rest) < 0.002);       // the wire's still gate holds a still head still: far under a pixel
    }
}
}

int main(int argc, char** argv) {
    if (argc < 2) { std::cerr << "usage: luma_renderer_prediction_test tests/fixtures/luma_imu_real.bin\n"; return 2; }
    std::string tmpl = (std::filesystem::temp_directory_path() / "luma_renderer_prediction_XXXXXX").string();
    const char* made = mkdtemp(tmpl.data());
    if (!made) { std::cerr << "mkdtemp failed\n"; return 1; }
    const std::filesystem::path dir(made);
    test_replay(argv[1], dir);
    std::error_code ignored;
    std::filesystem::remove_all(dir, ignored);
    if (g_failures) { std::cerr << g_failures << " failure(s)\n"; return 1; }
    std::cout << "luma_renderer_prediction: all passed\n";
    return 0;
}
