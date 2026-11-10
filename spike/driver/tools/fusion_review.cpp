// luma_fusion_review: D11 of docs/rust-rebuild-plan.md. Replays every fixture through luma::Fusion (Mahony, the reference)
// and the error-state Kalman filter in tools/eskf.hpp, sample for sample on the same stream, and scores both on tilt
// error p95 and heading drift. Prints one row per fixture and the plan's verdict: adopt only if the candidate improves
// tilt p95 on every fixture, or drift on every fixture. Results and reasoning: spike/driver/docs/fusion-eskf-review.md.
//
//   luma_fusion_review REAL.bin UPSIDE_DOWN.bin [--quick] [--set name=value ...]
//
// --set overrides a field of eskf::Config, for tuning the candidate; the reference always runs with its defaults.
//
// Two kinds of fixture:
// - simulated (tests/imu_sim.hpp), with the true orientation: tilt is the angle between true and estimated world up,
//   drift the heading error at the end against the heading error when scoring began, per minute;
// - recorded (tests/fixtures/*.bin), with no truth: tilt is the angle between the filter's up and the mean accelerometer
//   over 100 ms blocks at rest, drift the heading slope over unbroken rests of at least 0.5 s. Both are upper bounds:
//   gravity at rest is the only reference, and a resting head also wanders.
// Scoring starts once both filters call themselves calibrated, and no earlier than the fixture's settle time, so neither
// is judged on the other's start-up.
#include "eskf.hpp"
#include "imu_sim.hpp"
#include "luma/fusion.hpp"
#include "luma/protocol.hpp"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <vector>

using namespace luma;
using namespace luma::sim;
namespace {
struct Score { double tilt_p95 = 0, drift_deg_min = 0; std::size_t n = 0; };
struct Row { std::string name; Score mahony, eskf; };

double p95(std::vector<double> v) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, std::size_t(0.95 * double(v.size())))];
}
double yaw_of(Q q) { return std::atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)) / kDeg; }

// Both filters on one stream.
eskf::Config g_eskf;
struct Pair {
    Fusion mahony;
    eskf::Filter eskf{g_eskf};
};
bool apply_setting(eskf::Config& c, const std::string& text) {
    const auto eq = text.find('=');
    if (eq == text.npos) return false;
    const std::map<std::string, double*> fields{
        {"gyro_sigma", &c.gyro_sigma}, {"bias_walk", &c.bias_walk}, {"bias0_sigma", &c.bias0_sigma}, {"tilt0_sigma", &c.tilt0_sigma},
        {"accel_sigma", &c.accel_sigma}, {"accel_norm_k", &c.accel_norm_k}, {"accel_rate_k", &c.accel_rate_k},
        {"accel_rate_gate", &c.accel_rate_gate}, {"accel_gate", &c.accel_gate}, {"still_sigma", &c.still_sigma},
        {"quiet_sigma", &c.quiet_sigma}, {"zero_rate_chi2", &c.zero_rate_chi2}};
    const auto found = fields.find(text.substr(0, eq));
    if (found == fields.end()) return false;
    *found->second = std::strtod(text.c_str() + eq + 1, nullptr);
    return true;
}

// ---- simulated fixtures --------------------------------------------------------------------------------------------
struct SimScorer {
    explicit SimScorer(double settle) : settle_s(settle) {}
    double settle_s;
    bool started = false;
    double t0 = 0, yaw0 = 0, yaw_now = 0;
    std::vector<double> tilts;
    void add(double t, Q est, Q truth, bool both_calibrated, std::uint64_t i) {
        if (t < settle_s || !both_calibrated) return;
        const double y = yaw_deg(est, truth);
        if (!started) { started = true; t0 = t; yaw0 = y; }
        yaw_now = unwrap(y - yaw0);
        if (i % 10 == 0) tilts.push_back(tilt_deg(est, truth));
        last_t = t;
    }
    double last_t = 0;
    Score score() const { return {p95(tilts), std::fabs(yaw_now) / std::max(1e-6, (last_t - t0) / 60), tilts.size()}; }
};

Row simulate(const std::string& name, const ImuModel& model, const Motion& motion, double seconds, double settle_s) {
    Imu imu(model);
    Pair f;
    SimScorer sm(settle_s), se(settle_s);
    for (std::uint64_t i = 0; imu.t() < seconds; ++i) {
        const ImuSample s = imu.step(motion);
        const PoseOut a = f.mahony.update(s), b = f.eskf.update(s);
        const bool both = a.calibrated && b.calibrated;
        sm.add(imu.t(), from(a.q), imu.truth(), both, i);
        se.add(imu.t(), from(b.q), imu.truth(), both, i);
    }
    return {name, sm.score(), se.score()};
}

ImuModel typical() {
    ImuModel m;
    m.gyro_bias = {0.03, -0.02, 0.045};  // 1.7, -1.1, 2.6 deg/s: fusion_test.cpp's typical()
    return m;
}

// ---- recorded fixtures ---------------------------------------------------------------------------------------------
using Packet = std::array<std::uint8_t, kLumaImuPacketSize>;
std::vector<Packet> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<Packet> packets;
    Packet p{};
    while (in.read(reinterpret_cast<char*>(p.data()), std::streamsize(p.size()))) packets.push_back(p);
    return packets;
}

// 100 ms blocks; a block is at rest by luma::Fusion's desk test. Tilt per resting block; heading slope per rest run.
struct RestScorer {
    std::vector<double> tilts, slopes, weights;
    double block_s = 0;
    int n = 0;
    V g_sum{}, g_sq{}, a_sum{}, up_sum{};
    std::vector<double> run_t, run_yaw;
    double yaw_unwrapped = 0, last_yaw = 0;
    bool have_yaw = false;
    void close_run() {
        if (run_t.size() >= 5) {
            const double m = double(run_t.size());
            double mt = 0, my = 0, num = 0, den = 0;
            for (std::size_t i = 0; i < run_t.size(); ++i) { mt += run_t[i] / m; my += run_yaw[i] / m; }
            for (std::size_t i = 0; i < run_t.size(); ++i) { num += (run_t[i] - mt) * (run_yaw[i] - my); den += (run_t[i] - mt) * (run_t[i] - mt); }
            if (den > 0) { slopes.push_back(std::fabs(num / den) * 60); weights.push_back(run_t.back() - run_t.front()); }
        }
        run_t.clear();
        run_yaw.clear();
    }
    void add(double t, double dt, const ImuSample& s, Q est, std::array<float, 3> up, bool scoring) {
        const double y = yaw_of(est);
        if (have_yaw) yaw_unwrapped += unwrap(y - last_yaw);
        last_yaw = y;
        have_yaw = true;
        if (!scoring) return;
        block_s += dt;
        ++n;
        for (std::size_t i = 0; i < 3; ++i) {
            g_sum[i] += s.gyro_rad_s[i];
            g_sq[i] += double(s.gyro_rad_s[i]) * s.gyro_rad_s[i];
            a_sum[i] += s.accel_m_s2[i];
            up_sum[i] += up[i];
        }
        if (block_s < 0.1 - 1e-9) return;
        double gyro_var = 0;
        for (std::size_t i = 0; i < 3; ++i) gyro_var += std::max(0.0, g_sq[i] / n - g_sum[i] * g_sum[i] / (double(n) * n));
        const double an = std::hypot(a_sum[0], a_sum[1], a_sum[2]) / n, un = std::hypot(up_sum[0], up_sum[1], up_sum[2]);
        const bool rest = std::sqrt(gyro_var) < 0.015 && std::fabs(an / kG - 1) < 0.05;
        if (rest) {
            const double cosine = (a_sum[0] * up_sum[0] + a_sum[1] * up_sum[1] + a_sum[2] * up_sum[2]) / (an * n * un);
            tilts.push_back(std::acos(std::clamp(cosine, -1.0, 1.0)) / kDeg);
            run_t.push_back(t);
            run_yaw.push_back(yaw_unwrapped);
        } else {
            close_run();
        }
        block_s = 0;
        n = 0;
        g_sum = g_sq = a_sum = up_sum = V{};
    }
    Score score() {
        close_run();
        double sum = 0, w = 0;
        for (std::size_t i = 0; i < slopes.size(); ++i) { sum += slopes[i] * weights[i]; w += weights[i]; }
        return {p95(tilts), w > 0 ? sum / w : 0, tilts.size()};
    }
};

Row replay(const std::string& name, const std::string& path) {
    const std::vector<Packet> packets = load(path);
    if (packets.empty()) { std::fprintf(stderr, "no packets in %s\n", path.c_str()); std::exit(2); }
    auto tracker = luma_sequence_tracker();
    Pair f;
    RestScorer sm, se;
    double first = -1, last = 0;
    for (const Packet& p : packets) {
        auto s = parse_imu(p);
        if (!s || tracker.observe(*s).verdict != SequenceTracker::Verdict::accepted) continue;
        const PoseOut a = f.mahony.update(*s), b = f.eskf.update(*s);
        const double t = double(s->t_ns) * 1e-9;
        if (first < 0) first = last = t;
        const double dt = t - last;
        last = t;
        const bool both = a.calibrated && b.calibrated;
        sm.add(t - first, dt, *s, from(a.q), f.mahony.status().up_body, both);
        se.add(t - first, dt, *s, from(b.q), f.eskf.up_body(), both);
    }
    return {name, sm.score(), se.score()};
}
}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) { std::fputs("usage: luma_fusion_review REAL.bin UPSIDE_DOWN.bin [--quick] [--set name=value ...]\n", stderr); return 2; }
    bool quick = false;
    for (int i = 3; i < argc; ++i) {
        if (std::strcmp(argv[i], "--quick") == 0) quick = true;
        else if (std::strcmp(argv[i], "--set") == 0 && i + 1 < argc && apply_setting(g_eskf, argv[i + 1])) ++i;
        else { std::fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
    }
    const double minutes = quick ? 2 : 10;
    std::vector<Row> rows;

    rows.push_back(simulate("sim: still on desk", typical(), hold, minutes * 60, 3));
    {
        ImuModel m = typical();
        m.bias_change = {0.006, -0.004, 0.0087};  // 0.5 deg/s on yaw over 2 minutes
        m.bias_walk = 0.0001;
        rows.push_back(simulate("sim: still, warming up", m, hold, minutes * 60, 3));
    }
    {
        ImuModel m = typical();
        m.bias_change = {0.003, -0.002, 0.0044};
        rows.push_back(simulate("sim: worn at rest, warming", m, worn_rest, minutes * 60, 30));
    }
    {
        ImuModel m = typical();
        m.lever_m = {0.09, 0, 0.1};
        m.bias_change = {0.003, -0.002, 0.0044};
        rows.push_back(simulate("sim: worn desk work, warming", m, worn_working, minutes * 60, 30));
    }
    {
        const Wander wander(3, 1, 320);
        rows.push_back(simulate("sim: restless head", typical(),
                                [&](double t) { const V a = worn_working(t), b = wander.at(t); return V{a[0] + b[0], a[1] + b[1], a[2] + b[2]}; },
                                300, 30));
    }
    rows.push_back(simulate("sim: fast yaw 700 deg/s", typical(),
                            [](double t) {
                                if (t < 2) return V{0, 0, 0};
                                const double p = std::fmod(t - 2, 1.0);
                                return V{0, 0, turn_rate(p, 0, 0.25, 90) + turn_rate(p, 0.5, 0.25, -90)};
                            },
                            22, 2));
    rows.push_back(simulate("sim: three-axis coning", typical(),
                            [](double t) {
                                if (t < 2) return V{0, 0, 0};
                                return V{1.5 * std::sin(2 * kPi * 1.3 * t), 2.0 * std::cos(2 * kPi * 1.7 * t), 3.0 * std::sin(2 * kPi * 0.9 * t)};
                            },
                            22, 2));
    {
        ImuModel m = typical();
        m.lever_m = {0.09, 0, 0.1};
        rows.push_back(simulate("sim: head shake, lever arm", m,
                                [](double t) { return t < 2 ? V{0, 0, 0} : V{0, 0, 4.0 * std::sin(2 * kPi * 1.5 * t)}; }, 22, 2));
    }
    rows.push_back(replay("real: worn, 60 deg turn (8 s)", argv[1]));
    rows.push_back(replay("real: upside down on desk (2 s)", argv[2]));

    std::printf("%-34s %22s %22s\n", "", "tilt p95 deg", "drift deg/min");
    std::printf("%-34s %10s %11s %10s %11s %8s\n", "fixture", "mahony", "eskf", "mahony", "eskf", "n");
    bool tilt_all = true, drift_all = true, scored = true;
    for (const Row& r : rows) {
        std::printf("%-34s %10.3f %11.3f %10.3f %11.3f %8zu\n", r.name.c_str(), r.mahony.tilt_p95, r.eskf.tilt_p95, r.mahony.drift_deg_min,
                    r.eskf.drift_deg_min, r.mahony.n);
        tilt_all = tilt_all && r.eskf.tilt_p95 < r.mahony.tilt_p95;
        drift_all = drift_all && r.eskf.drift_deg_min < r.mahony.drift_deg_min;
        scored = scored && r.mahony.n > 0 && r.eskf.n > 0;
        for (const double v : {r.mahony.tilt_p95, r.eskf.tilt_p95, r.mahony.drift_deg_min, r.eskf.drift_deg_min}) scored = scored && std::isfinite(v);
    }
    std::printf("\nESKF better on tilt p95 on every fixture: %s\n", tilt_all ? "yes" : "no");
    std::printf("ESKF better on drift on every fixture: %s\n", drift_all ? "yes" : "no");
    std::printf("verdict: %s\n", tilt_all || drift_all ? "adopt" : "not adopted, Mahony stays");
    if (!scored) { std::fputs("a fixture produced no score\n", stderr); return 1; }
    return 0;
}
