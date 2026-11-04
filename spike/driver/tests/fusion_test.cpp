// luma::Fusion against the synthetic IMU (imu_sim.hpp). Software only: these numbers say the
// filter does what it should with the modelled sensor, not how the real glasses behave.
// Plain run: every scenario at full length plus the report table. --quick: ctest length.
#include "imu_sim.hpp"
#include "luma/fusion.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

// Counts every allocation, so a test can show update() makes none.
static std::size_t g_allocs = 0;
void* operator new(std::size_t n) {
    ++g_allocs;
    if (void* p = std::malloc(n)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

using namespace luma;
using namespace luma::sim;
namespace {
bool g_quick = false;
double minutes(double full, double quick) { return g_quick ? quick : full; }

struct Failure : std::runtime_error { using std::runtime_error::runtime_error; };
#define CHECK(x) do { if (!(x)) throw Failure(std::string(__FILE__ ":") + std::to_string(__LINE__) + ": " #x); } while (0)
#define CHECK_LT(a, b) do { const double a_ = (a), b_ = (b); if (!(a_ < b_)) throw Failure(std::string(__FILE__ ":") + std::to_string(__LINE__) + ": " #a " = " + std::to_string(a_) + ", wanted < " + std::to_string(b_)); } while (0)

struct Row { std::string name; double usable_s, tilt_deg, drift_deg_min; };
std::vector<Row> g_rows;

// Tilt at the end, and the heading error's slope after the first `settle_s`. No estimate of
// heading can be judged at start-up, so the first calibrated heading is the reference.
struct Run {
    Fusion f;
    Imu imu;
    double yaw0 = 0, t0 = -1, tilt_max = 0, yaw_now = 0, yaw_peak = 0;
    PoseOut pose{};
    explicit Run(const ImuModel& m, const FusionConfig& c = {}) : f(c), imu(m) {}
    Q est() const { return from(pose.q); }
    void go(double seconds, const Motion& w, double settle_s = 3) {
        const double end = imu.t() + seconds;
        while (imu.t() < end) {
            pose = f.update(imu.step(w));
            if (imu.t() < settle_s) continue;
            const double y = yaw_deg(est(), imu.truth());
            if (t0 < 0) { t0 = imu.t(); yaw0 = y; }
            yaw_now = unwrap(y - yaw0);
            yaw_peak = std::max(yaw_peak, std::fabs(yaw_now));
            tilt_max = std::max(tilt_max, tilt_deg(est(), imu.truth()));
        }
    }
    double drift_deg_min() const { return std::fabs(yaw_now) / std::max(1e-6, (imu.t() - t0) / 60); }
    double tilt() const { return tilt_deg(est(), imu.truth()); }
    double bias_err_deg_s() const {
        const auto b = f.status().gyro_bias_rad_s;
        const V t = imu.bias_now();
        return std::sqrt(std::pow(b[0] - t[0], 2) + std::pow(b[1] - t[1], 2) + std::pow(b[2] - t[2], 2)) / kDeg;
    }
    void row(const std::string& name) { g_rows.push_back({name, double(f.status().calibrated_after_s), tilt_max, drift_deg_min()}); }
};
ImuModel typical() {
    ImuModel m;
    m.gyro_bias = {0.03, -0.02, 0.045};  // 1.7, -1.1, 2.6 deg/s
    return m;
}

void still_flat_calibrates_fast() {
    Run r(typical());
    r.go(1.0, hold, 99);
    CHECK(!r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Uncalibrated);
    r.go(1.0, hold, 99);
    CHECK(r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Good);
    CHECK_LT(r.f.status().calibrated_after_s, 1.8);
    CHECK_LT(r.bias_err_deg_s(), 0.05);
    r.go(minutes(120, 20), hold);
    CHECK_LT(r.drift_deg_min(), 0.5);
    CHECK_LT(r.tilt_max, 0.2);
    r.row("still on desk");
}
void tilt_is_right_from_first_samples() {
    ImuModel m = typical();
    m.start = axis_angle({1, 0.4, 0}, 40 * kDeg);
    Run r(m);
    r.go(0.02, hold, 99);
    CHECK(!r.pose.calibrated);
    CHECK_LT(r.tilt(), 1.0);
}
void warm_start_is_usable_on_first_sample() {
    ImuModel m = typical();
    FusionConfig c;
    c.warm_start_bias = std::array<float, 3>{0.031f, -0.021f, 0.044f};
    Run r(m, c);
    r.go(0.002, hold, 99);
    CHECK(r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Coarse);
    CHECK_LT(r.f.status().calibrated_after_s, 0.01);
    r.go(2.0, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
    r.go(minutes(60, 10), hold);
    CHECK_LT(r.drift_deg_min(), 0.5);
    r.row("warm start, still");
}
void tilted_starts_read_true_gravity() {
    struct C { const char* name; V axis; double deg; };
    for (const C& c : {C{"upside down", {1, 0, 0}, 180}, C{"upside down, nose flipped", {0, 1, 0}, 180},
                       C{"nose straight up", {0, 1, 0}, -90}, C{"nose straight down", {0, 1, 0}, 90},
                       C{"on one arm", {1, 0, 0}, 90}, C{"folded askew", {1, 2, 3}, 130}}) {
        ImuModel m = typical();
        m.start = axis_angle(c.axis, c.deg * kDeg);
        Run r(m);
        r.go(2, hold, 99);
        CHECK(r.pose.calibrated);
        CHECK_LT(r.tilt(), 0.3);
        CHECK(r.f.status().quality == FusionQuality::Good);
        r.go(minutes(30, 5), hold, 2);
        CHECK_LT(r.tilt_max, 0.3);
    }
}
void upside_down_start_then_worn_level_is_level() {
    // The level bar's 90 degree bug: XRLinuxDriver's feed z stays at the glasses' calibration up.
    ImuModel m = typical();
    m.start = axis_angle({1, 0, 0}, 180 * kDeg);
    Run r(m);
    r.go(2, hold, 99);
    // Picked up and put on over 2 s: a half roll.
    CHECK_LT(r.tilt(), 0.3);
    r.go(1, [](double t) { return V{turn_rate(t, 2, 2, 180), 0, 0}; }, 99);
    CHECK_LT(r.tilt(), 1.0);  // Halfway through the roll too.
    r.go(1, [](double t) { return V{turn_rate(t, 2, 2, 180), 0, 0}; }, 99);
    r.go(1, hold, 99);
    CHECK_LT(r.tilt(), 1.0);
    const auto up = r.f.status().up_body;
    CHECK_LT(std::fabs(up[2] - 1.f), 0.001);
}
void slow_yaw(double deg_s, double seconds, const char* name) {
    Run r(typical());
    r.go(2, hold, 99);
    const Q est_before = from(r.pose.q);
    r.go(seconds, [deg_s](double) { return V{0, 0, deg_s * kDeg}; }, 99);
    const double truly = unwrap(deg_s * seconds);
    const double seen = yaw_deg(est_before, from(r.pose.q));
    CHECK_LT(std::fabs(unwrap(seen - truly)), 0.4);
    g_rows.push_back({name, double(r.f.status().calibrated_after_s), r.tilt(), std::fabs(unwrap(seen - truly)) / (seconds / 60)});
}
void slow_yaw_5() { slow_yaw(5, 60, "slow yaw 5 deg/s"); }
void slow_yaw_1() { slow_yaw(1, 15, "slow yaw 1 deg/s"); }
void fast_yaw_keeps_heading() {
    Run r(typical());
    r.go(2, hold, 99);
    const Q start = from(r.pose.q);
    // 20 back-and-forth turns of +-90 degrees peaking near 700 deg/s, then the same with a tilted axis.
    auto w = [](double t) { const double p = std::fmod(t, 1.0); return V{0, 0, turn_rate(p, 0, 0.25, 90) + turn_rate(p, 0.5, 0.25, -90)}; };
    r.go(10.25, w, 99);  // Stopped at the far end of a turn.
    CHECK_LT(std::fabs(unwrap(yaw_deg(from(r.pose.q), start) - yaw_deg(r.imu.truth(), Q{}))), 0.5);
    r.go(9.75, w, 99);
    r.go(1, hold, 99);
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), start)), 0.5);
    CHECK_LT(r.tilt(), 0.5);
    g_rows.push_back({"fast yaw 700 deg/s", double(r.f.status().calibrated_after_s), r.tilt(), std::fabs(yaw_deg(from(r.pose.q), start)) / (21.0 / 60)});
}
void three_axis_motion_stays_true() {
    // Non-commuting motion: heading AND tilt must survive coning.
    ImuModel m = typical();
    m.gyro_sigma = 0;
    m.accel_sigma = 0;
    Run r(m);
    r.go(2, hold, 99);
    auto w = [](double t) { return V{1.5 * std::sin(2 * kPi * 1.3 * t), 2.0 * std::cos(2 * kPi * 1.7 * t), 3.0 * std::sin(2 * kPi * 0.9 * t)}; };
    r.go(20, w);
    CHECK_LT(r.tilt_max, 1.0);
    CHECK_LT(r.yaw_peak, 1.5);
}
void noise_leaves_pose_quiet() {
    ImuModel m = typical();
    m.gyro_sigma = 0.01;
    m.accel_sigma = 0.2;
    Run r(m);
    r.go(3, hold, 99);
    CHECK(r.pose.calibrated);
    double s = 0, n = 0;
    Q last = from(r.pose.q);
    for (int i = 0; i < 2000; ++i) {
        r.pose = r.f.update(r.imu.step(hold));
        const Q d = mul(conj(last), from(r.pose.q));
        s += 4 * (d.x * d.x + d.y * d.y + d.z * d.z);
        n += 1;
        last = from(r.pose.q);
    }
    CHECK_LT(std::sqrt(s / n) / kDeg, 0.002);  // Sample-to-sample jitter, degrees.
}
void bias_is_removed_on_every_axis() {
    for (const V b : {V{0.1, 0, 0}, V{0, -0.1, 0}, V{0, 0, 0.1}, V{0.08, 0.08, -0.08}}) {
        ImuModel m;
        m.gyro_bias = b;
        Run r(m);
        r.go(2, hold, 99);
        CHECK_LT(r.bias_err_deg_s(), 0.05);
    }
}
void warm_up_drift_is_tracked_on_desk() {
    ImuModel m = typical();
    m.bias_change = {0.006, -0.004, 0.0087};  // 0.5 deg/s on yaw over 2 minutes, no temperature input.
    m.bias_walk = 0.0001;
    Run r(m);
    r.go(minutes(600, 120), hold);
    CHECK_LT(r.drift_deg_min(), 1.0);
    CHECK_LT(r.bias_err_deg_s(), 0.05);
    r.row("still, warming up");
}
void worn_start_is_usable_by_timeout_then_good() {
    Run r(typical());
    r.go(2.4, worn_rest, 99);
    CHECK(!r.pose.calibrated);
    r.go(0.3, worn_rest, 99);
    CHECK(r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Coarse);
    CHECK_LT(r.f.status().calibrated_after_s, 2.7);
    CHECK_LT(r.bias_err_deg_s(), 1.0);
    r.go(30, worn_rest, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
    CHECK_LT(r.bias_err_deg_s(), 0.25);
}
void worn_working_drift() {
    ImuModel m = typical();
    m.lever_m = {0.09, 0, 0.1};
    m.bias_change = {0.003, -0.002, 0.0044};
    Run r(m);
    r.go(minutes(600, 90), worn_working, 30);
    CHECK_LT(r.tilt_max, 1.5);
    CHECK_LT(r.drift_deg_min(), 4);
    r.row("worn, desk work, warming");
}
void wrong_bias_on_a_worn_head_is_admitted_and_fixed() {
    // Calibrated on the desk, then the bias moves 2 deg/s and the glasses are worn from then on.
    ImuModel m = typical();
    m.bias_change = {0, 0, 0.035};
    m.bias_tau_s = 5;
    Run r(m);
    r.go(2, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
    bool demoted = false;
    for (int i = 0; i < 60; ++i) {
        r.go(5, worn_working, 99);
        demoted = demoted || r.f.status().quality == FusionQuality::Coarse;
    }
    CHECK(demoted);
    CHECK(r.f.status().quality == FusionQuality::Good);
    CHECK_LT(r.bias_err_deg_s(), 0.1);
}
void restless_head_still_finds_the_bias() {
    // No calm moment from the first sample: the worst start there is short of a magnetometer.
    ImuModel m = typical();
    const Wander wander(3, 1, 320);
    Run r(m);
    r.go(300, [&](double t) { const V a = worn_working(t), b = wander.at(t); return V{a[0] + b[0], a[1] + b[1], a[2] + b[2]}; }, 30);
    CHECK_LT(r.bias_err_deg_s(), 0.5);
    r.row("restless head, 5 min");
}
void desk_relearns_a_bias_that_moved_while_worn() {
    ImuModel m = typical();
    m.bias_change = {0, 0, 0.04};  // Leaves more error than still_track_limit lets through.
    m.bias_tau_s = 1;
    Run r(m);
    r.go(1.2, hold, 99);
    r.go(1, hold, 99);
    CHECK_LT(0.5, r.bias_err_deg_s());
    r.go(26, hold, 99);  // 20 s to re-learn, then a few for tracking to clear what that averaged in.
    CHECK_LT(r.bias_err_deg_s(), 0.05);
}
void slow_turn_blind_spot_is_measured() {
    // Known limit, not a target: without a magnetometer a turn slower than still_track_limit
    // (0.57 deg/s) and perfectly smooth cannot be told from bias, so the filter absorbs it.
    Run r(typical());
    r.go(2, hold, 99);
    const Q start = from(r.pose.q);
    r.go(60, [](double) { return V{0, 0, 0.3 * kDeg}; }, 99);
    const double lost = std::fabs(18 - yaw_deg(start, from(r.pose.q)));
    CHECK_LT(lost, 18);  // Part of it is seen.
    g_rows.push_back({"smooth 0.3 deg/s turn", double(r.f.status().calibrated_after_s), r.tilt(), lost});
}
void moving_at_start_never_claims_good() {
    Run r(typical());
    r.go(3, [](double t) { return V{0.3 * std::sin(5 * t), 0.5 * std::cos(4 * t), 0.8 * std::sin(3 * t)}; }, 99);
    CHECK(r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Coarse);
    CHECK_LT(r.tilt(), 2.0);
    r.go(2.5, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
    CHECK_LT(r.bias_err_deg_s(), 0.05);
}
void bump_during_calibration_restarts_it() {
    Run r(typical());
    r.go(0.9, hold, 99);
    r.go(0.1, [](double t) { return V{1.5 * std::sin(60 * t), 0, 0}; }, 99);
    r.go(1.0, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Uncalibrated);
    r.go(0.8, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
    CHECK_LT(r.bias_err_deg_s(), 0.05);
}
void dropped_samples_change_nothing() {
    for (const double p : {0.01, 0.2, 0.5}) {
        Run r(typical());
        r.go(2, hold, 99);
        std::uniform_real_distribution<double> u(0, 1);
        auto w = [](double t) { return V{0.4 * std::sin(3 * t), 0.5 * std::sin(2 * t), 1.5 * std::sin(1.5 * t)}; };
        const double t_end = r.imu.t() + 2 * kPi / 1.5 * 4;  // Whole cycles, zero net turn of a lone axis.
        while (r.imu.t() < t_end) {
            const ImuSample s = r.imu.step(w);
            if (u(r.imu.rng()) >= p) r.pose = r.f.update(s);
        }
            CHECK_LT(r.tilt(), 0.6);
        CHECK(r.f.status().gaps == 0);
    }
}
void burst_drop_is_bridged() {
    // A 60 ms hole at speed is shorter than max_gap_s and interpolates; heading stays.
    Run r(typical());
    r.go(2, hold, 99);
    const Q start = from(r.pose.q);
    auto w = [](double t) { return V{0, 0, 0.5 * std::sin(2 * t)}; };
    const double t0 = r.imu.t();
    double peak = 0;
    for (int i = 0; r.imu.t() < t0 + 2 * kPi / 2 * 3; ++i) {
        const ImuSample s = r.imu.step(w);
        if (i % 500 >= 60) r.pose = r.f.update(s);
        peak = std::max(peak, std::fabs(yaw_deg(from(r.pose.q), start)));
    }
    CHECK(peak > 10);
    CHECK(r.f.status().gaps == 0);
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), start)), 1.0);
    CHECK_LT(r.tilt(), 0.5);
}
ImuSample shifted(ImuSample s, std::int64_t ns) { s.t_ns = std::uint64_t(std::int64_t(s.t_ns) + ns); return s; }
void timestamp_jumps_do_not_move_pose() {
    for (const std::int64_t jump : {std::int64_t(10'000'000'000), std::int64_t(-3'000'000'000)}) {
        Run r(typical());
        r.go(3, hold, 99);
        CHECK(r.pose.calibrated);
        const Q before = from(r.pose.q);
        r.pose = r.f.update(shifted(r.imu.step(hold), jump));  // One bad stamp, next ones fine.
        CHECK_LT(tilt_deg(from(r.pose.q), before) + std::fabs(yaw_deg(from(r.pose.q), before)), 0.01);
        for (int i = 0; i < 500; ++i) r.pose = r.f.update(r.imu.step(hold));
        CHECK_LT(tilt_deg(from(r.pose.q), before) + std::fabs(yaw_deg(from(r.pose.q), before)), 0.1);
        CHECK(r.f.status().time_jumps >= 1);
        CHECK(r.f.status().gaps == 0);
    }
    // A clock that rebases for good: the stream continues from the new origin.
    Run r(typical());
    r.go(3, hold, 99);
    const Q before = from(r.pose.q);
    for (int i = 0; i < 2000; ++i) r.pose = r.f.update(shifted(r.imu.step(hold), -4'000'000'000));
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), before)), 0.1);
    CHECK(r.f.status().time_jumps == 1);
}
void yaw_rate_survives_a_time_jump() {
    // Moving through a bad stamp: the step uses the nominal period, so no heading is lost.
    Run r(typical());
    r.go(3, hold, 99);
    const Q before = from(r.pose.q);
    auto w = [](double) { return V{0, 0, 1.0}; };
    for (int i = 0; i < 1000; ++i) {
        ImuSample s = r.imu.step(w);
        r.pose = r.f.update(i == 400 ? shifted(s, 50'000'000'000) : s);
    }
    CHECK_LT(std::fabs(unwrap(yaw_deg(before, from(r.pose.q)) - 57.2958)), 0.3);
}
void duplicates_and_garbage_are_rejected() {
    Run r(typical());
    r.go(3, hold, 99);
    const Q before = from(r.pose.q);
    const ImuSample s = r.imu.step(hold);
    r.pose = r.f.update(s);
    const std::uint64_t rejected = r.f.status().rejected;
    for (int i = 0; i < 100; ++i) r.pose = r.f.update(s);
    ImuSample nan = r.imu.step(hold);
    nan.gyro_rad_s[1] = std::nanf("");
    r.pose = r.f.update(nan);
    ImuSample inf = r.imu.step(hold);
    inf.accel_m_s2[0] = 1.f / 0.f * 1.f;
    r.pose = r.f.update(inf);
    CHECK(r.f.status().rejected == rejected + 102);
    CHECK(std::isfinite(r.pose.q.w) && std::isfinite(r.pose.q.x));
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), before)), 0.02);
    r.go(1, hold, 99);
    CHECK_LT(r.tilt(), 0.2);
}
void usb_drop_recovers_within_a_second() {
    // Stream gone for 2 s, glasses flipped meanwhile, then the stream resumes with a restarted seq.
    for (const double deg : {25.0, 90.0, 180.0}) {
        Run r(typical());
        r.go(3, hold, 99);
        const auto bias = r.f.status().gyro_bias_rad_s;
        auto w = [deg](double t) { return V{turn_rate(std::fmod(t, 100), 3, 1, deg), 0, 0}; };
        for (int i = 0; i < 2000; ++i) (void)r.imu.step(w);
        ImuSample s = r.imu.step(hold);
        s.seq = 1;
        r.pose = r.f.update(s);
        CHECK(r.f.status().gaps == 1);
        CHECK(r.f.status().quality == FusionQuality::Recovering);
        CHECK(r.pose.calibrated);
        for (std::uint32_t i = 2; i < 1000; ++i) { s = r.imu.step(hold); s.seq = i; r.pose = r.f.update(s); }
        CHECK_LT(r.tilt(), deg > 170 ? 3.0 : 1.5);
        for (std::uint32_t i = 1000; i < 2000; ++i) { s = r.imu.step(hold); s.seq = i; r.pose = r.f.update(s); }
        CHECK_LT(r.tilt(), 0.5);
        CHECK(r.f.status().quality == FusionQuality::Good);
        const auto after = r.f.status().gyro_bias_rad_s;
        CHECK_LT(std::fabs(after[2] - bias[2]), 0.002);
    }
}
void reconnect_keeps_heading_when_nothing_moved() {
    ImuModel m = typical();
    m.start = axis_angle({1, 1, 0}, 35 * kDeg);
    Run r(m);
    r.go(3, hold, 99);
    CHECK_LT(r.tilt(), 0.3);
    const Q before = from(r.pose.q);
    for (int i = 0; i < 1000; ++i) (void)r.imu.step(hold);
    r.go(1, hold, 99);
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), before)), 0.05);
    CHECK_LT(tilt_deg(from(r.pose.q), before), 0.1);
}
void head_shake_lever_arm_barely_tilts() {
    ImuModel m = typical();
    m.lever_m = {0.09, 0, 0.1};
    Run r(m);
    r.go(2, hold, 99);
    r.go(20, [](double t) { return V{0, 0, 4.0 * std::sin(2 * kPi * 1.5 * t)}; }, 2);
    CHECK_LT(r.tilt_max, 1.0);
    CHECK_LT(r.yaw_peak, 2.0);
}
void accel_shock_is_ignored() {
    ImuModel m = typical();
    m.start = axis_angle({0, 1, 0}, 20 * kDeg);
    Run r(m);
    r.go(3, hold, 99);
    for (int i = 0; i < 200; ++i) {
        ImuSample s = r.imu.step(hold);
        s.accel_m_s2[0] += 30;
        r.pose = r.f.update(s);
    }
    CHECK_LT(r.tilt(), 0.1);
}
void all_rates_track() {
    for (const double hz : {60.0, 120.0, 250.0, 500.0, 1000.0}) {
        ImuModel m = typical();
        m.rate_hz = hz;
        Run r(m);
        r.go(2.2, hold, 99);
        CHECK(r.f.status().quality == FusionQuality::Good);
        const Q start = from(r.pose.q);
        r.go(10, [](double t) { const double p = std::fmod(t, 2.0); return V{0, 0, turn_rate(p, 0, 0.8, 60) + turn_rate(p, 1, 0.8, -60)}; }, 99);
        CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), start)), 2.5);
        CHECK_LT(r.tilt(), 0.5);
    }
}
void mag_is_ignored_by_default() {
    ImuModel m = typical();
    m.mag = true;
    Run with(m);
    ImuModel n = typical();
    Run without(n);
    for (int i = 0; i < 5000; ++i) {
        ImuSample a = with.imu.step(worn_rest);
        ImuSample b = without.imu.step(worn_rest);
        a.mag = std::array<float, 3>{float(i), 1e6f, -3.f};
        with.pose = with.f.update(a);
        without.pose = without.f.update(b);
    }
    CHECK(with.pose.calibrated);
    CHECK(std::memcmp(&with.pose.q, &without.pose.q, sizeof(Quatf)) == 0);
}
void mag_option_holds_heading_against_bias() {
    ImuModel m = typical();
    m.mag = true;
    m.bias_change = {0, 0, 0.0175};  // 1 deg/s of yaw bias appears after calibration and no stillness to learn it.
    m.bias_tau_s = 30;
    auto w = [](double t) { return worn_working(t); };
    Run off(m);
    FusionConfig c;
    c.use_mag = true;
    Run on(m, c);
    off.go(minutes(300, 120), w, 3);
    on.go(minutes(300, 120), w, 3);
    CHECK_LT(on.drift_deg_min(), 0.5);
    CHECK_LT(on.drift_deg_min() * 3, off.drift_deg_min());
    on.row("worn, big bias, mag on");
    off.row("worn, big bias, mag off");
}
void mag_disturbance_does_not_grab_heading() {
    ImuModel m = typical();
    m.mag = true;
    FusionConfig c;
    c.use_mag = true;
    Run r(m, c);
    r.go(3, hold, 99);
    CHECK(r.pose.calibrated);
    const Q before = from(r.pose.q);
    for (int i = 0; i < 3000; ++i) {
        ImuSample s = r.imu.step(hold);
        (*s.mag)[1] += 3.f;  // A speaker magnet held by the glasses.
        r.pose = r.f.update(s);
    }
    CHECK_LT(std::fabs(yaw_deg(from(r.pose.q), before)), 0.1);
}
void angular_velocity_is_prediction_ready() {
    ImuModel m = typical();
    Run r(m);
    r.go(2, hold, 99);
    const V w0{0.3, -0.5, 1.2};
    r.go(0.5, [w0](double) { return w0; }, 99);
    const FusionStatus st = r.f.status();
    for (std::size_t i = 0; i < 3; ++i) CHECK_LT(std::fabs(st.omega_body_rad_s[i] - w0[i]), 0.03);
    const V world = rotate(r.imu.truth(), w0);
    for (std::size_t i = 0; i < 3; ++i) CHECK_LT(std::fabs(st.omega_world_rad_s[i] - world[i]), 0.04);
    // Predicting 30 ms ahead turns the pose by what the head will turn, about the right axis.
    const Quatf p = r.f.predict(0.03f);
    const Quatf zero = r.f.predict(0.f);
    CHECK(std::memcmp(&zero, &r.pose.q, sizeof(Quatf)) == 0);
    const Q now = from(r.pose.q), est_step = mul(conj(now), from(p));
    const Q true_step = exp_step(w0, 0.03);
    const Q miss = mul(conj(true_step), est_step);
    CHECK_LT(2 * std::sqrt(miss.x * miss.x + miss.y * miss.y + miss.z * miss.z) / kDeg, 0.15);
    CHECK(2 * std::sqrt(true_step.x * true_step.x + true_step.y * true_step.y + true_step.z * true_step.z) / kDeg > 2);
}
double measured_lag_ms() {
    ImuModel m = typical();
    m.gyro_sigma = 0.002;
    Run r(m);
    r.go(2, hold, 99);
    const double f = 2.0, a = 30 * kDeg * 2 * kPi * f;
    auto w = [=](double t) { return V{0, 0, a * std::cos(2 * kPi * f * (t - 2))}; };
    const Q start = from(r.pose.q);
    const Q truth0 = r.imu.truth();
    double num = 0, den = 0, worst = 0;
    for (int i = 0; i < 4000; ++i) {
        r.pose = r.f.update(r.imu.step(w));
        const double est = yaw_deg(start, from(r.pose.q)), tru = yaw_deg(truth0, r.imu.truth());
        const double rate = w(r.imu.t())[2] / kDeg;
        num += (tru - est) * rate;
        den += rate * rate;
        worst = std::max(worst, std::fabs(unwrap(tru - est)));
    }
    CHECK_LT(worst, 1.0);
    return num / den * 1000;
}
double g_lag_ms = 0;
void adds_no_lag() {
    g_lag_ms = measured_lag_ms();
    CHECK_LT(std::fabs(g_lag_ms), 0.5);
}
double g_update_ns = 0;
void update_is_fast_enough_for_1khz_and_more() {
    ImuModel m = typical();
    Imu imu(m);
    std::vector<ImuSample> v;
    for (int i = 0; i < 20000; ++i) v.push_back(imu.step(worn_working));
    Fusion f;
    double sink = 0;
    const std::size_t allocs = g_allocs;
    const auto a = std::chrono::steady_clock::now();
    for (int k = 0; k < 10; ++k) for (const ImuSample& s : v) sink += f.update(s).q.w;
    const auto b = std::chrono::steady_clock::now();
    CHECK(g_allocs == allocs);
    CHECK(std::isfinite(sink));
    g_update_ns = std::chrono::duration<double, std::nano>(b - a).count() / (10.0 * double(v.size()));
    CHECK_LT(g_update_ns, 5000);
}
void quality_never_lies_after_reset() {
    Run r(typical());
    r.go(3, hold, 99);
    CHECK(r.pose.calibrated);
    r.f.reset();
    r.pose = r.f.update(r.imu.step(hold));
    CHECK(!r.pose.calibrated);
    CHECK(r.f.status().quality == FusionQuality::Uncalibrated);
    r.go(2, hold, 99);
    CHECK(r.f.status().quality == FusionQuality::Good);
}
}  // namespace

int main(int argc, char** argv) {
    g_quick = argc > 1 && std::strcmp(argv[1], "--quick") == 0;
    using Fn = void (*)();
    const std::vector<std::pair<const char*, Fn>> tests = {
#define T(n) {#n, n}
        T(still_flat_calibrates_fast), T(tilt_is_right_from_first_samples), T(warm_start_is_usable_on_first_sample),
        T(tilted_starts_read_true_gravity), T(upside_down_start_then_worn_level_is_level), T(slow_yaw_5), T(slow_yaw_1),
        T(fast_yaw_keeps_heading), T(three_axis_motion_stays_true), T(noise_leaves_pose_quiet),
        T(bias_is_removed_on_every_axis), T(warm_up_drift_is_tracked_on_desk), T(worn_start_is_usable_by_timeout_then_good),
        T(worn_working_drift), T(wrong_bias_on_a_worn_head_is_admitted_and_fixed), T(restless_head_still_finds_the_bias),
        T(desk_relearns_a_bias_that_moved_while_worn), T(slow_turn_blind_spot_is_measured), T(moving_at_start_never_claims_good), T(bump_during_calibration_restarts_it),
        T(dropped_samples_change_nothing), T(burst_drop_is_bridged), T(timestamp_jumps_do_not_move_pose),
        T(yaw_rate_survives_a_time_jump), T(duplicates_and_garbage_are_rejected), T(usb_drop_recovers_within_a_second),
        T(reconnect_keeps_heading_when_nothing_moved), T(head_shake_lever_arm_barely_tilts), T(accel_shock_is_ignored),
        T(all_rates_track), T(mag_is_ignored_by_default), T(mag_option_holds_heading_against_bias),
        T(mag_disturbance_does_not_grab_heading), T(angular_velocity_is_prediction_ready), T(adds_no_lag),
        T(update_is_fast_enough_for_1khz_and_more), T(quality_never_lies_after_reset),
#undef T
    };
    int failed = 0;
    for (const auto& [name, fn] : tests) {
        try {
            fn();
            std::printf("ok    %s\n", name);
        } catch (const std::exception& e) {
            ++failed;
            std::printf("FAIL  %s: %s\n", name, e.what());
        }
    }
    std::printf("\n%-28s %10s %10s %14s\n", "scenario", "usable s", "tilt deg", "yaw deg/min");
    for (const Row& r : g_rows) std::printf("%-28s %10.2f %10.3f %14.3f\n", r.name.c_str(), r.usable_s, r.tilt_deg, r.drift_deg_min);
    std::printf("\nfilter lag %.3f ms, update() %.0f ns, %d of %zu tests failed\n", g_lag_ms, g_update_ns, failed, tests.size());
    return failed ? 1 : 0;
}
