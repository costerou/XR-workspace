// luma_replay_report: runs a recorded IMU stream through the real parser, tracker and Fusion and says how they did:
// rate, lost samples, calibration, gyro bias over time, tilt against gravity, and heading drift at rest. Offline, no
// device, no feed. A real recording has no heading truth, so "drift at rest" is the slope of heading over windows where
// the head barely moves: an upper bound, because a resting head also wanders.
//
//   luma_replay_report FILE.lcap|FILE.bin [--every SECONDS] [--set name=value ...] [--predict-ms N[,N...]]
//
// FILE.bin is 28-byte packets end to end, the form of tests/fixtures/luma_imu_real.bin. --set overrides a FusionConfig
// field, for tuning against a recording. --predict-ms 10,17,25 is the prediction replay bench (rust-rebuild-plan D3): at
// each horizon it predicts from every calibrated sample and scores the guess against the pose the recording reaches that
// many ms later (by device time, the nearest sample). Four ways: nothing, the renderer's finite difference over the
// feed's q0 and q1 16 samples back ("old", what runs today), a raw gyro step, and luma::Predictor, whose velocity and
// acceleration are what PredictionWire carries ("new", D3). RMS and p95 per horizon, over all calibrated samples and with
// the head moving; exit 1 if a horizon scored nothing.
#include "luma/capture_file.hpp"
#include "luma/fusion.hpp"
#include "luma/predict.hpp"
#include "luma/protocol.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <map>
#include <numeric>
#include <string>
#include <vector>

using namespace luma;
namespace {
constexpr double kDeg = 180.0 / 3.14159265358979323846;

struct Packet { std::vector<std::uint8_t> bytes; };

std::vector<Packet> load(const std::string& path) {
    std::vector<Packet> packets;
    if (path.size() > 5 && path.ends_with(".lcap")) {
        std::string why;
        auto events = capture::load_replay(path, &why, kLumaImuEndpoint);
        if (!events) { std::fprintf(stderr, "%s\n", why.c_str()); std::exit(2); }
        for (auto& e : *events) if (e.kind == capture::ReplayKind::from_device) packets.push_back({std::move(e.payload)});
        return packets;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) { std::fprintf(stderr, "cannot open %s\n", path.c_str()); std::exit(2); }
    std::vector<std::uint8_t> packet(kLumaImuPacketSize);
    while (in.read(reinterpret_cast<char*>(packet.data()), std::streamsize(packet.size()))) packets.push_back({packet});
    return packets;
}

bool apply_setting(FusionConfig& c, const std::string& text) {
    const auto eq = text.find('=');
    if (eq == text.npos) return false;
    const std::string name = text.substr(0, eq);
    const float value = std::strtof(text.c_str() + eq + 1, nullptr);
    const std::map<std::string, float*> fields{
        {"cal_still_s", &c.cal_still_s}, {"coarse_timeout_s", &c.coarse_timeout_s}, {"still_gyro_std", &c.still_gyro_std},
        {"still_accel_std", &c.still_accel_std}, {"still_mean_step", &c.still_mean_step}, {"quiet_gyro_std", &c.quiet_gyro_std},
        {"quiet_accel_std", &c.quiet_accel_std}, {"cal_max_rate", &c.cal_max_rate}, {"kp_moving", &c.kp_moving}, {"kp_still", &c.kp_still},
        {"ki", &c.ki}, {"accel_norm_gate", &c.accel_norm_gate}, {"accel_rate_gate", &c.accel_rate_gate}, {"still_tau_s", &c.still_tau_s},
        {"still_track_limit", &c.still_track_limit}, {"still_relearn_s", &c.still_relearn_s}, {"still_relearn_limit", &c.still_relearn_limit},
        {"quiet_max_rate", &c.quiet_max_rate}, {"quiet_window_s", &c.quiet_window_s}, {"quiet_tau_s", &c.quiet_tau_s},
        {"quiet_track_limit", &c.quiet_track_limit}, {"quiet_window_coarse_s", &c.quiet_window_coarse_s},
        {"quiet_tau_coarse_s", &c.quiet_tau_coarse_s}, {"quiet_limit_coarse", &c.quiet_limit_coarse}, {"coarse_to_good_s", &c.coarse_to_good_s}};
    const auto found = fields.find(name);
    if (found == fields.end()) return false;
    *found->second = value;
    return true;
}

double yaw_of(const Quatf& q) { return std::atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)); }
double pitch_of(const Quatf& q) { return std::asin(std::clamp(2.0 * (q.w * q.y - q.z * q.x), -1.0, 1.0)); }
double roll_of(const Quatf& q) { return std::atan2(2.0 * (q.w * q.x + q.y * q.z), 1.0 - 2.0 * (q.x * q.x + q.y * q.y)); }
const char* name_of(FusionQuality quality) {
    switch (quality) {
    case FusionQuality::Uncalibrated: return "uncalibrated";
    case FusionQuality::Coarse: return "coarse";
    case FusionQuality::Good: return "good";
    case FusionQuality::Recovering: return "recovering";
    }
    return "?";
}
double percentile(std::vector<double> values, double p) {
    if (values.empty()) return 0;
    std::sort(values.begin(), values.end());
    return values[std::min(values.size() - 1, std::size_t(p * double(values.size())))];
}

struct Dq { double w = 1, x = 0, y = 0, z = 0; };
Dq to_d(const Quatf& q) { return {q.w, q.x, q.y, q.z}; }
Dq conj(Dq q) { return {q.w, -q.x, -q.y, -q.z}; }
Dq mul(Dq a, Dq b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
double angle_deg(Dq a, Dq b) {
    const Dq d = mul(conj(a), b);
    return 2 * std::atan2(std::hypot(d.x, d.y, d.z), std::fabs(d.w)) * kDeg;
}
// a to b, scaled: where the same rotation lands after `factor` times as long, from b. What the renderer's finite difference does.
Dq extend(Dq a, Dq b, double factor) {
    Dq d = mul(conj(a), b);
    if (d.w < 0) d = {-d.w, -d.x, -d.y, -d.z};
    const double half = std::atan2(std::hypot(d.x, d.y, d.z), d.w), n = std::hypot(d.x, d.y, d.z);
    if (n < 1e-12) return b;
    const double scaled = half * factor, k = std::sin(scaled) / n;
    return mul(b, Dq{std::cos(scaled), d.x * k, d.y * k, d.z * k});
}

// How far off each way of predicting was, once the head has really got there.
struct Score {
    double sum_sq = 0;
    std::vector<double> errors;
    void add(double degrees) { sum_sq += degrees * degrees; errors.push_back(degrees); }
    double rms() const { return std::sqrt(sum_sq / double(std::max<std::size_t>(errors.size(), 1))); }
};
constexpr int kWays = 4, kOld = 1, kNew = 3;
constexpr const char* kWayNames[kWays] = {"no prediction", "old: renderer finite diff", "raw gyro step", "new: luma::Predictor"};
// Who is scored: every calibrated sample, the head moving over 20 deg/s, turning over 60 deg/s.
constexpr int kGroups = 3;
constexpr const char* kGroupNames[kGroups] = {"all", "moving >20 deg/s", "turning >60 deg/s"};
constexpr double kGroupMinDegS[kGroups] = {-1, 20, 60};
struct Guess { std::uint64_t target_ns; Dq way[kWays]; double rate_deg_s; };
// One horizon of the bench: guesses waiting for the recording to get there, and how they did.
struct Horizon {
    int ms = 0;
    std::deque<Guess> waiting;
    Score score[kGroups][kWays];
    std::uint64_t unscored = 0;  // the recording skipped past the target (lost samples)
    // Score every guess whose target the sample at t_ns reaches, to the nearest sample at 1 kHz.
    void reach(std::uint64_t t_ns, const Dq& now) {
        while (!waiting.empty() && waiting.front().target_ns <= t_ns + 500'000) {
            const Guess& g = waiting.front();
            if (t_ns > g.target_ns + 1'500'000) ++unscored;
            else for (int k = 0; k < kGroups; ++k) if (g.rate_deg_s > kGroupMinDegS[k])
                for (int w = 0; w < kWays; ++w) score[k][w].add(angle_deg(g.way[w], now));
            waiting.pop_front();
        }
    }
    void print() const {
        std::printf("\nprediction %d ms ahead (%llu guesses past a gap, unscored)\n", ms, (unsigned long long)unscored);
        std::printf("  %-28s", "deg");
        for (int k = 0; k < kGroups; ++k) std::printf(" | %-17s n %-6zu", kGroupNames[k], score[k][0].errors.size());
        std::printf("\n");
        for (int w = 0; w < kWays; ++w) {
            std::printf("  %-28s", kWayNames[w]);
            for (int k = 0; k < kGroups; ++k) std::printf(" | rms %6.3f p95 %6.3f      ", score[k][w].rms(), percentile(score[k][w].errors, 0.95));
            std::printf("\n");
        }
    }
};

// Mean and spread of one 5 s stretch at rest.
struct Window {
    std::vector<double> t, yaw;
    double pitch_sum = 0, pitch_sq = 0, roll_sum = 0, roll_sq = 0;
    std::array<double, 3> accel{}, up{};
    void clear() { t.clear(); yaw.clear(); pitch_sum = pitch_sq = roll_sum = roll_sq = 0; accel = up = {}; }
    double slope_deg_min() const {
        const double n = double(t.size()), mt = std::accumulate(t.begin(), t.end(), 0.0) / n, my = std::accumulate(yaw.begin(), yaw.end(), 0.0) / n;
        double num = 0, den = 0;
        for (std::size_t i = 0; i < t.size(); ++i) { num += (t[i] - mt) * (yaw[i] - my); den += (t[i] - mt) * (t[i] - mt); }
        return den > 0 ? num / den * 60 * kDeg : 0;
    }
};
}

int main(int argc, char** argv) {
    if (argc < 2) { std::fputs("usage: luma_replay_report FILE [--every SECONDS] [--set name=value ...] [--predict-ms N[,N...]]\n", stderr); return 2; }
    FusionConfig config;
    double every = 120;
    std::vector<Horizon> horizons;
    for (int i = 2; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--every" && i + 1 < argc) every = std::atof(argv[++i]);
        else if (arg == "--predict-ms" && i + 1 < argc) {
            for (const char* p = argv[++i]; *p;) {
                char* end = nullptr;
                const long ms = std::strtol(p, &end, 10);
                if (end == p || ms < 1 || ms > 200 || (*end && *end != ',')) { std::fprintf(stderr, "--predict-ms wants N[,N...], 1..200\n"); return 2; }
                horizons.push_back({});
                horizons.back().ms = int(ms);
                p = *end ? end + 1 : end;
            }
        }
        else if (arg == "--set" && i + 1 < argc) {
            if (!apply_setting(config, argv[++i])) { std::fprintf(stderr, "unknown setting %s\n", argv[i]); return 2; }
        } else { std::fprintf(stderr, "unknown option %s\n", arg.c_str()); return 2; }
    }
    const std::vector<Packet> packets = load(argv[1]);
    auto tracker = luma_sequence_tracker();
    Fusion fusion(config);
    std::uint64_t samples = 0, bad = 0, repeats = 0, largest_gap = 0, gaps = 0;
    FusionQuality quality = FusionQuality::Uncalibrated;
    double next_row = 0, first_t = -1, t = 0, unwrapped = 0, last_yaw = 0;
    bool have_yaw = false;
    Window window;
    std::vector<double> slopes, tilts;
    Predictor predictor;
    std::deque<Dq> history;  // newest first, for the renderer's q1 16 samples back (publish.hpp history_step at 1 kHz)
    std::printf("%8s %6s %-11s %24s %8s %7s %7s\n", "time_s", "temp_C", "quality", "bias_deg_s(f,l,u)", "yaw", "pitch", "roll");
    for (const Packet& p : packets) {
        auto sample = parse_imu(p.bytes);
        if (!sample) { ++bad; continue; }
        const auto seen = tracker.observe(*sample);
        if (seen.verdict != SequenceTracker::Verdict::accepted) { ++repeats; continue; }
        if (seen.lost) { ++gaps; largest_gap = std::max<std::uint64_t>(largest_gap, seen.lost); }
        ++samples;
        const PoseOut pose = fusion.update(*sample);
        t = double(sample->t_ns) * 1e-9;
        if (first_t < 0) first_t = t;
        t -= first_t;
        const FusionStatus status = fusion.status();
        if (!horizons.empty() && pose.calibrated) {
            predictor.set_gyro_bias(status.gyro_bias_rad_s);
            predictor.update(pose, *sample, sample->t_ns);
            const Dq now = to_d(pose.q);
            history.push_front(now);
            if (history.size() > 17) history.pop_back();
            const auto& omega = status.omega_body_rad_s;
            const double rate = std::hypot(omega[0], omega[1], omega[2]) * kDeg;
            for (Horizon& h : horizons) {
                h.reach(sample->t_ns, now);
                const Dq renderer = history.size() > 16 ? extend(history[16], now, double(h.ms) / 16.0) : now;
                h.waiting.push_back({sample->t_ns + std::uint64_t(h.ms) * 1'000'000,
                                     {now, renderer, to_d(fusion.predict(float(h.ms) * 1e-3f)), to_d(predictor.predict(float(h.ms)).q)}, rate});
            }
        }
        if (status.quality != quality) {
            std::printf("%8.2f quality %s -> %s\n", t, name_of(quality), name_of(status.quality));
            quality = status.quality;
        }
        const double y = yaw_of(pose.q);
        if (have_yaw) unwrapped += std::remainder(y - last_yaw, 2 * 3.14159265358979323846);
        last_yaw = y;
        have_yaw = true;
        if (t >= next_row) {
            next_row += every;
            const std::uint32_t temp = std::uint32_t(p.bytes[24]) | std::uint32_t(p.bytes[25]) << 8;
            const auto& b = status.gyro_bias_rad_s;
            std::printf("%8.1f %6.2f %-11s %7.3f %7.3f %7.3f   %8.2f %7.2f %7.2f\n", t, double(temp) / 132.48 + 25, name_of(status.quality),
                        b[0] * kDeg, b[1] * kDeg, b[2] * kDeg, unwrapped * kDeg, pitch_of(pose.q) * kDeg, roll_of(pose.q) * kDeg);
        }
        if (!pose.calibrated) continue;
        if (samples % 10 == 0) {
            const double pitch = pitch_of(pose.q), roll = roll_of(pose.q);
            window.t.push_back(t);
            window.yaw.push_back(unwrapped);
            window.pitch_sum += pitch; window.pitch_sq += pitch * pitch;
            window.roll_sum += roll; window.roll_sq += roll * roll;
            for (std::size_t i = 0; i < 3; ++i) { window.accel[i] += sample->accel_m_s2[i]; window.up[i] += status.up_body[i]; }
        }
        if (window.t.size() >= 500) {
            const double n = double(window.t.size());
            const double pitch_var = window.pitch_sq / n - window.pitch_sum * window.pitch_sum / (n * n);
            const double roll_var = window.roll_sq / n - window.roll_sum * window.roll_sum / (n * n);
            double yaw_var = 0, mean = std::accumulate(window.yaw.begin(), window.yaw.end(), 0.0) / n;
            for (const double v : window.yaw) yaw_var += (v - mean) * (v - mean) / n;
            const double limit = 0.05 / kDeg, yaw_limit = 0.12 / kDeg;
            if (pitch_var < limit * limit && roll_var < limit * limit && yaw_var < yaw_limit * yaw_limit) {
                slopes.push_back(window.slope_deg_min());
                const double an = std::hypot(window.accel[0], window.accel[1], window.accel[2]), un = std::hypot(window.up[0], window.up[1], window.up[2]);
                const double cosine = (window.accel[0] * window.up[0] + window.accel[1] * window.up[1] + window.accel[2] * window.up[2]) / (an * un);
                tilts.push_back(std::acos(std::clamp(cosine, -1.0, 1.0)) * kDeg);
            }
            window.clear();
        }
    }
    const FusionStatus end = fusion.status();
    std::vector<double> magnitudes;
    for (const double s : slopes) magnitudes.push_back(std::fabs(s));
    std::printf("\npackets %zu, samples %llu, repeats %llu, bad %llu, lost %llu in %llu gaps (largest %llu)\n", packets.size(),
                (unsigned long long)samples, (unsigned long long)repeats, (unsigned long long)bad, (unsigned long long)tracker.lost_total(),
                (unsigned long long)gaps, (unsigned long long)largest_gap);
    std::printf("span %.1f s, %.1f Hz; calibrated after %.2f s; fusion gaps %llu, time jumps %llu\n", t, double(samples) / std::max(t, 1e-9),
                double(end.calibrated_after_s), (unsigned long long)end.gaps, (unsigned long long)end.time_jumps);
    std::printf("at rest: %zu windows of 5 s; heading slope deg/min median %.2f, |median| %.2f, p95 |slope| %.2f; tilt against gravity median %.2f deg, p95 %.2f deg\n",
                slopes.size(), percentile(slopes, 0.5), percentile(magnitudes, 0.5), percentile(magnitudes, 0.95), percentile(tilts, 0.5), percentile(tilts, 0.95));
    std::printf("net heading change over the run %.1f deg\n", unwrapped * kDeg);
    bool empty = false;
    for (const Horizon& h : horizons) { h.print(); empty |= h.score[0][0].errors.empty(); }
    if (!horizons.empty()) {
        // The D3 target: new RMS at most half of old, head moving.
        std::printf("\nold vs new, head moving over 20 deg/s\n");
        for (const Horizon& h : horizons) {
            const Score &o = h.score[1][kOld], &n = h.score[1][kNew];
            std::printf("  %3d ms  old rms %6.3f p95 %6.3f  new rms %6.3f p95 %6.3f  new/old rms %5.2f\n", h.ms, o.rms(),
                        percentile(o.errors, 0.95), n.rms(), percentile(n.errors, 0.95), o.rms() > 0 ? n.rms() / o.rms() : 0.0);
        }
    }
    return bad || empty ? 1 : 0;
}
