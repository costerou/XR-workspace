// D3 of docs/rust-rebuild-plan.md: the driver publishes the gyro's angular velocity and acceleration on PredictionWire
// (predict.hpp, segment pose_prediction) beside the XRLinuxDriver feed, on every sample.
//
// The first half is Publisher::publish_motion on its own. The second half replays tests/fixtures/luma_imu_real.bin (argv[1]:
// 8 s of a real Luma Ultra at 1 kHz, the head resting, turning about 60 degrees and coming back) through the real Daemon, the
// real Fusion and the real feed sink on private segments, reads the wire back through SysV after every sample as a renderer
// would, and checks it against the recording itself, decoded here on its own with parse_imu:
//   - filled at 1 kHz: one valid write per published pose, with that sample's seq and arrival time;
//   - q is the feed's newest pose (mount pitch applied);
//   - omega is the recording's gyro, less fusion's bias, turned into the feed's frame, a couple of ms of filtering behind;
//   - alpha follows the recording's own rate of change of the gyro;
//   - and what it is for: extrapolating the wire 10, 17 and 25 ms ahead lands nearer the orientation the recording reaches
//     than the renderer's finite difference of q0 and the pose 16 ms older does.
// It never opens a device or the live feed.
#include "luma/daemon.hpp"
#include "luma/predict.hpp"
#include "luma/protocol.hpp"
#include "luma/publish.hpp"
#include "luma/wiring.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <unistd.h>
#include <vector>

using namespace luma;
namespace {
int g_failures = 0;
#define CHECK(x) do { if (!(x)) { ++g_failures; std::cerr << "  FAILED " << __FILE__ << ':' << __LINE__ << ": " #x "\n"; } } while (0)
constexpr std::uint64_t kMs = 1'000'000;
constexpr double kPi = 3.14159265358979323846, kDeg = 180 / kPi;
constexpr float kMountDeg = -8.5f;  // PublisherOptions::mount_pitch_deg, what feed_sink uses

Quatf mul(const Quatf& a, const Quatf& b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
Quatf conj(const Quatf& q) { return {q.w, -q.x, -q.y, -q.z}; }
Quatf mount() { const float h = kMountDeg * float(kPi / 180) / 2; return {std::cos(h), 0, std::sin(h), 0}; }
Vec3f rotate(const Quatf& q, const Vec3f& v) {  // q v conj(q)
    const Quatf r = mul(mul(q, Quatf{0, v[0], v[1], v[2]}), conj(q));
    return {r.x, r.y, r.z};
}
double angle_deg(const Quatf& a, const Quatf& b) {  // atan2, not acos: acos near 1 turns float rounding into 0.05 degrees
    const Quatf d = mul(conj(a), b);
    const double v = std::sqrt(double(d.x) * d.x + double(d.y) * d.y + double(d.z) * d.z);
    return 2 * std::atan2(v, std::fabs(double(d.w))) * kDeg;
}
double norm(const Vec3f& v) { return std::sqrt(double(v[0]) * v[0] + double(v[1]) * v[1] + double(v[2]) * v[2]); }
Vec3f minus(const Vec3f& a, const Vec3f& b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
// The rotation vector of q, and back: what the renderer's finite difference works with.
Vec3f log_map(const Quatf& q0) {
    const Quatf q = q0.w < 0 ? Quatf{-q0.w, -q0.x, -q0.y, -q0.z} : q0;
    const double s = std::sqrt(double(q.x) * q.x + double(q.y) * q.y + double(q.z) * q.z);
    const double k = s < 1e-9 ? 2.0 : 2 * std::atan2(s, double(q.w)) / s;
    return {float(q.x * k), float(q.y * k), float(q.z * k)};
}
Quatf exp_map(const Vec3f& r) {
    const double a = norm(r), k = a < 1e-9 ? 0.5 : std::sin(a / 2) / a;
    return {float(std::cos(a / 2)), float(r[0] * k), float(r[1] * k), float(r[2] * k)};
}

// The wire as a renderer reaches it: by name, through SysV, read-only.
struct WireView {
    const PredictionWire* wire = nullptr;
    explicit WireView(const std::string& prefix) {
        const key_t key = ftok((prefix + kPredictionIpcName).c_str(), 0);
        const int id = key == -1 ? -1 : shmget(key, sizeof(PredictionWire), 0);
        void* addr = id < 0 ? reinterpret_cast<void*>(-1) : shmat(id, nullptr, SHM_RDONLY);
        if (addr != reinterpret_cast<void*>(-1)) wire = static_cast<const PredictionWire*>(addr);
    }
    ~WireView() { if (wire) shmdt(wire); }
    std::optional<MotionState> read() const { return wire ? read_prediction(*wire) : std::nullopt; }
};

std::string private_prefix(const std::filesystem::path& dir) { return (dir / "xr_").string(); }

// ---- Publisher::publish_motion ------------------------------------------------------------------------------------------
void test_publish_motion(const std::filesystem::path& dir) {
    PublisherOptions options;
    options.prefix = private_prefix(dir / "unit");
    options.private_segments = true;
    std::filesystem::create_directories(dir / "unit");
    std::string why;
    auto publisher = Publisher::open(options, why);
    CHECK(publisher);
    if (!publisher) { std::cerr << "  open: " << why << "\n"; return; }
    WireView view(options.prefix);
    CHECK(view.wire);
    CHECK(!view.read());  // open writes "no sample"

    MotionState s;
    s.q = {std::cos(0.3f), 0, 0, std::sin(0.3f)};  // 34 degrees of yaw
    s.host_ns = 123'456'789'012ull;
    s.seq = 4242;
    s.omega_rad_s = {0.1f, -0.2f, 1.5f};
    s.alpha_rad_s2 = {-3, 4, 20};
    s.valid = s.calibrated = true;
    publisher->publish_motion(s);
    const auto got = view.read();
    CHECK(got);
    if (got) {
        CHECK(got->seq == 4242 && got->host_ns == 123'456'789'012ull && got->calibrated);
        const Quatf m = mount();
        CHECK(angle_deg(got->q, mul(s.q, m)) < 1e-3);  // the feed's q0
        CHECK(norm(minus(rotate(m, got->omega_rad_s), s.omega_rad_s)) < 1e-5);
        CHECK(norm(minus(rotate(m, got->alpha_rad_s2), s.alpha_rad_s2)) < 1e-4);
        // The point of the turn: a reader extrapolating the wire lands where the body frame's motion, mounted, does.
        const PredictConfig cfg;
        const Quatf want = mul(extrapolate(s, 17, cfg).q, m);
        CHECK(angle_deg(extrapolate(*got, 17, cfg).q, want) < 1e-3);
    }
    CHECK(publisher->stats().motion == 1);

    publisher->pause();
    CHECK(!view.read());  // stale or away: no motion either
    publisher->publish_motion(s);
    CHECK(view.read());
    MotionState uncalibrated = s;
    uncalibrated.calibrated = false;
    publisher->publish_motion(uncalibrated);
    CHECK(!view.read());
    MotionState broken = s;
    broken.q = {0, 0, 0, 0};
    publisher->publish_motion(s);
    publisher->publish_motion(broken);
    CHECK(!view.read());
    publisher->publish_motion(s);
    publisher.reset();
    CHECK(!view.read());  // closing leaves "no sample", not the last motion
}

// ---- the recording through the real daemon -----------------------------------------------------------------------------
struct Seen { PoseOut pose; PoseSource source; std::optional<MotionState> wire; };

// The real feed sink, read back after every write.
class Probe final : public Sink {
public:
    Probe(std::unique_ptr<Sink> inner, const std::string& prefix) : inner_(std::move(inner)), view_(prefix) {}
    void publish(const PoseOut& pose, const PoseSource& source) override {
        inner_->publish(pose, source);
        seen.push_back({pose, source, view_.read()});
    }
    void set_stale(bool stale) override {
        inner_->set_stale(stale);
        if (stale && view_.read()) stale_but_valid = true;
    }
    std::vector<Seen> seen;
    bool stale_but_valid = false;
    bool attached() const { return view_.wire; }
private:
    std::unique_ptr<Sink> inner_;
    WireView view_;
};

std::vector<std::array<std::uint8_t, kLumaImuPacketSize>> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<std::array<std::uint8_t, kLumaImuPacketSize>> packets;
    std::array<std::uint8_t, kLumaImuPacketSize> p{};
    while (in.read(reinterpret_cast<char*>(p.data()), std::streamsize(p.size()))) packets.push_back(p);
    return packets;
}

double rms(const std::vector<double>& v) {
    double s = 0;
    for (double x : v) s += x * x;
    return v.empty() ? 0 : std::sqrt(s / double(v.size()));
}

void test_replay(const std::string& fixture, const std::filesystem::path& dir) {
    const auto packets = load(fixture);
    CHECK(packets.size() > 7000);
    if (packets.size() < 7000) return;

    // The recording, decoded on its own: gyro by sequence number. Host time is one record per ms, as the glasses send them.
    std::map<std::uint32_t, Vec3f> gyro_of;
    std::vector<CaptureRecord> records;
    records.push_back({0, CaptureKind::to_device, {0x01}});
    for (std::size_t i = 0; i < packets.size(); ++i) {
        if (const auto s = parse_imu(packets[i])) gyro_of[s->seq] = s->gyro_rad_s;
        records.push_back({(i + 1) * kMs, CaptureKind::from_device, {packets[i].begin(), packets[i].end()}});
    }

    std::filesystem::create_directories(dir / "replay");
    const std::string prefix = private_prefix(dir / "replay");
    std::string why;
    auto feed = wiring::feed_sink(prefix, why, true);
    CHECK(feed);
    if (!feed) { std::cerr << "  feed_sink: " << why << "\n"; return; }
    Probe probe(std::move(feed), prefix);
    CHECK(probe.attached());

    ReplayLink link(std::move(records), false);
    std::atomic<bool> stop{false};
    const auto decoder = wiring::decoder();
    DaemonParts parts{link, *decoder, &wiring::estimator, probe, [&] { return link.now_ns(); },
                      [](std::string_view) {}, wiring::start_command(), wiring::stop_command()};
    Daemon daemon(std::move(parts));
    CHECK(daemon.run(stop) == DaemonEnd::replay_ended);
    const auto& seen = probe.seen;
    std::cout << "  replay: " << daemon.stats().samples << " samples, " << daemon.stats().published << " poses published, "
              << seen.size() << " wire reads\n";

    // Filled at 1 kHz: every published pose, at once, with its own sample.
    CHECK(seen.size() == daemon.stats().published);
    CHECK(seen.size() > 5000);  // calibration takes about 2.6 s of the 8 s while moving
    std::size_t valid = 0, matched = 0, one_ms = 0;
    for (std::size_t i = 0; i < seen.size(); ++i) {
        const Seen& s = seen[i];
        if (!s.wire) continue;
        ++valid;
        if (s.wire->seq == s.pose.seq && s.wire->host_ns == s.source.host_ns && s.wire->calibrated) ++matched;
        if (i && seen[i - 1].wire && s.wire->host_ns - seen[i - 1].wire->host_ns == kMs) ++one_ms;
    }
    CHECK(valid == seen.size());
    CHECK(matched == seen.size());
    CHECK(one_ms + 5 >= seen.size());  // the stream is whole (real_capture_test): at most a couple of gaps
    if (!seen.empty() && seen.back().wire && seen.front().wire) {
        const double span_s = double(seen.back().wire->host_ns - seen.front().wire->host_ns) * 1e-9;
        const double hz = double(seen.size() - 1) / span_s;
        std::printf("  wire rate: %.1f Hz over %.2f s\n", hz, span_s);
        CHECK(hz > 995 && hz < 1005);
    }
    CHECK(!probe.stale_but_valid);

    // Values against the recording.
    const Quatf m = mount();
    double q_worst = 0;
    std::vector<double> omega_err, omega_sig, alpha_err, alpha_sig;
    std::vector<Vec3f> gyro_seq;  // the recording's gyro less bias, in order of seen
    for (const Seen& s : seen) {
        const auto it = gyro_of.find(s.pose.seq);
        CHECK(it != gyro_of.end());
        if (it == gyro_of.end() || !s.wire) return;
        q_worst = std::max(q_worst, angle_deg(s.wire->q, mul(s.pose.q, m)));
        gyro_seq.push_back(minus(it->second, s.source.gyro_bias_rad_s));
    }
    std::printf("  q against the feed's q0: worst %.2e deg\n", q_worst);
    CHECK(q_worst < 1e-3);
    // alpha: the recording's own rate of change, a central difference over +-4 ms, so neither side is behind.
    constexpr std::size_t kHalf = 4;
    for (std::size_t i = 2 * kHalf + 20; i + kHalf < seen.size(); ++i) {
        const Vec3f omega = rotate(m, seen[i].wire->omega_rad_s), alpha = rotate(m, seen[i].wire->alpha_rad_s2);
        const Vec3f& g = gyro_seq[i];
        if (norm(g) < 0.2) continue;  // moving: above about 11 deg/s
        omega_err.push_back(norm(minus(omega, g)));
        omega_sig.push_back(norm(g));
        // The wire's alpha is the head's about alpha_tau_ms (8 ms) ago: compare with the difference centred there.
        const std::size_t c = i - 2 * kHalf;
        Vec3f diff;
        for (std::size_t k = 0; k < 3; ++k) diff[k] = float((gyro_seq[c + kHalf][k] - gyro_seq[c - kHalf][k]) / (2 * kHalf * 1e-3));
        alpha_err.push_back(norm(minus(alpha, diff)));
        alpha_sig.push_back(norm(diff));
    }
    std::printf("  omega while moving (%zu samples): rms %.4f rad/s off the recording's gyro, of rms %.4f rad/s\n",
                omega_err.size(), rms(omega_err), rms(omega_sig));
    std::printf("  alpha while moving: rms %.2f rad/s^2 off the recording's difference, of rms %.2f rad/s^2\n",
                rms(alpha_err), rms(alpha_sig));
    CHECK(omega_err.size() > 500);  // the turn is in the recording
    CHECK(rms(omega_err) < 0.01 * rms(omega_sig));
    CHECK(rms(alpha_err) < 0.25 * rms(alpha_sig));

    // What the wire is for. The orientation the recording reaches h ms after each sample is the feed's pose then.
    const PredictConfig cfg;
    for (const int h : {10, 17, 25}) {
        std::vector<double> wire_err, diff_err, hold_err;
        for (std::size_t i = 16; i + std::size_t(h) < seen.size(); ++i) {
            const Seen& s = seen[i];
            const std::size_t j = i + std::size_t(h);
            if (seen[j].pose.seq != ((s.pose.seq + std::uint32_t(h)) & 0xffff)) continue;  // a gap: no truth for this one
            if (seen[i - 16].pose.seq != ((s.pose.seq - 16) & 0xffff)) continue;
            if (norm(gyro_seq[i]) < 0.2) continue;  // the head is moving; still, every method is right
            const Quatf truth = seen[j].wire->q, q0 = s.wire->q, q16 = seen[i - 16].wire->q;
            wire_err.push_back(angle_deg(extrapolate(*s.wire, float(h), cfg).q, truth));
            // The renderer today (spike/include/xr/predict.hpp): velocity from q0 and the pose 16 ms older, body frame.
            const Vec3f r = log_map(mul(conj(q16), q0));
            const float k = float(h) / 16;
            diff_err.push_back(angle_deg(mul(q0, exp_map({r[0] * k, r[1] * k, r[2] * k})), truth));
            hold_err.push_back(angle_deg(q0, truth));
        }
        std::printf("  predict %2d ms while moving (%zu): rms error %.3f deg from the wire, %.3f deg differencing q0 and q16, "
                    "%.3f deg not predicting\n", h, wire_err.size(), rms(wire_err), rms(diff_err), rms(hold_err));
        CHECK(wire_err.size() > 500);
        CHECK(rms(wire_err) < 0.5 * rms(diff_err));  // the plan's D3 target, at every horizon
        CHECK(rms(wire_err) < 0.5 * rms(hold_err));
    }
}
}

int main(int argc, char** argv) {
    if (argc < 2) { std::cerr << "usage: luma_prediction_wire_test tests/fixtures/luma_imu_real.bin\n"; return 2; }
    std::string tmpl = (std::filesystem::temp_directory_path() / "luma_prediction_wire_XXXXXX").string();
    const char* made = mkdtemp(tmpl.data());
    if (!made) { std::cerr << "mkdtemp failed\n"; return 1; }
    const std::filesystem::path dir(made);
    test_publish_motion(dir);
    test_replay(argv[1], dir);
    std::error_code ignored;
    std::filesystem::remove_all(dir, ignored);
    if (g_failures) { std::cerr << g_failures << " failure(s)\n"; return 1; }
    std::cout << "luma_prediction_wire: all passed\n";
    return 0;
}
