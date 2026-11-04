// Pose prediction, against simulated head motion and against the renderer's own predictor code
// (xr_predict is linked in, so a change on either side that breaks the match fails here).
// `luma_predict_test --report` also prints the error-versus-look-ahead table.
#include "head_sim.hpp"
#include "luma/predict.hpp"
#include "xr/predict.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__func__) + ":" + std::to_string(__LINE__) + ": " #x); } while (0)
using namespace headsim;
using luma::Predictor;
using luma::PredictConfig;

namespace {
constexpr double kPxPerDeg = 46.5;  // tests/tracking_sim.hpp: Luma Ultra at screen centre
bool close(double a, double b, double eps) { return std::fabs(a - b) <= eps; }
xr::Quat to_xr(luma::Quatf q) { return {q.x, q.y, q.z, q.w}; }
Q of_xr(xr::Quat q) { return {q.w, q.x, q.y, q.z}; }

luma::ImuSample imu_at(std::uint64_t t_ns, std::array<float, 3> gyro, std::uint32_t seq) {
    return {t_ns, gyro, {0, 0, 9.81f}, std::nullopt, seq};
}
luma::PoseOut pose_at(Q q, std::uint64_t t_ns, std::uint32_t seq) { return {to_f(q), t_ns, seq, true}; }
// A steady body rate for `ms` samples at 1 kHz; returns the orientation reached.
Q spin(Predictor& p, Q q, std::array<float, 3> w, int ms, std::uint64_t& t_ns, std::uint32_t& seq) {
    for (int i = 0; i < ms; ++i) {
        q = mul(q, exp_map({w[0] * 1e-3, w[1] * 1e-3, w[2] * 1e-3}));
        t_ns += 1'000'000; ++seq;
        p.update(pose_at(q, t_ns, seq), imu_at(t_ns, w, seq), t_ns);
    }
    return q;
}

// The renderer today, fed by a perfect XRLinuxDriver-shaped feed: velocity from q0 and q1, 16 ms apart.
Q renderer_today(const Truth& truth, double t, double horizon_ms) {
    xr::PoseHistory h;
    for (int i = 0; i < 3; ++i) {
        const double ti = t - 0.016 * double(i);
        h.q[std::size_t(i)] = to_xr(to_f(truth.q(ti)));
        h.device_ms[std::size_t(i)] = float(std::floor(ti * 1000));
    }
    const auto w = xr::angular_velocity(h);
    const xr::Quat out = w ? xr::quat_mul(h.q[0], xr::prediction_delta(*w, float(horizon_ms), 15.f)) : h.q[0];
    return of_xr(out);
}

struct Score { double rms = 0, p95 = 0, max = 0; };
Score score(std::vector<double> e) {
    Score s;
    if (e.empty()) return s;
    double sq = 0;
    for (double v : e) sq += v * v;
    std::sort(e.begin(), e.end());
    s.rms = std::sqrt(sq / double(e.size())); s.p95 = e[std::size_t(0.95 * double(e.size() - 1))]; s.max = e.back();
    return s;
}
enum class Model { hold, today, gyro, full, feed };
const char* name(Model m) {
    switch (m) {
    case Model::hold: return "no prediction";
    case Model::today: return "renderer today";
    case Model::gyro: return "gyro only";
    case Model::full: return "gyro+accel";
    case Model::feed: return "via feed";
    }
    return "?";
}
PredictConfig gyro_only() { PredictConfig c; c.use_acceleration = false; c.stop_clamp = false; return c; }

// The orientation a model shows `horizon_ms` after a sample read at a 120 Hz frame.
struct Bench {
    Motion motion; double seconds; Sensor sensor; PredictConfig cfg;
    template <class Visit> void frames(Visit&& visit) const {
        Truth truth(motion, seconds);
        Run run(truth, sensor);
        Predictor p(cfg);
        double next_frame = 0.2;  // warm-up
        run.samples([&](const Sample& s) {
            p.update(s.pose, s.imu, s.host_ns);
            if (s.t < next_frame) return;
            next_frame += 1.0 / 120;
            visit(s, p, truth);
        });
    }
};
Score model_error(Model m, Motion motion, double horizon_ms, double seconds = 12, Sensor sensor = {}) {
    PredictConfig cfg = m == Model::gyro ? gyro_only() : PredictConfig{};
    std::vector<double> e;
    Bench{motion, seconds, sensor, cfg}.frames([&](const Sample& s, const Predictor& p, const Truth& truth) {
        const double target = s.t + horizon_ms / 1000;
        if (target >= truth.end()) return;
        Q shown = truth.q(s.t);
        if (m == Model::today) shown = renderer_today(truth, s.t, horizon_ms);
        else if (m == Model::gyro || m == Model::full) shown = of(p.predict(float(horizon_ms)).q);
        else if (m == Model::feed) {
            const auto h = luma::synthesize_history(p.state(), 1000.f, float(horizon_ms));
            xr::PoseHistory ph;
            for (std::size_t i = 0; i < 3; ++i) { ph.q[i] = to_xr(h.q[i]); ph.device_ms[i] = h.device_ms[i]; }
            const auto w = xr::angular_velocity(ph);
            shown = of_xr(xr::quat_mul(ph.q[0], xr::prediction_delta(*w, float(horizon_ms), 15.f)));
        }
        e.push_back(angle_deg(truth.q(target), shown));
    });
    return score(std::move(e));
}
// How far past the place the head came to rest a model shows it, degrees; 0 if it never does.
double stop_overshoot(Model m, Motion motion, double horizon_ms) {
    PredictConfig cfg = m == Model::gyro ? gyro_only() : PredictConfig{};
    double worst = 0;
    Truth final_pose(motion, kStopAt + 0.2);
    const Q rest = final_pose.q(kStopAt + 0.1);
    Bench{motion, kStopAt + 0.4, Sensor{}, cfg}.frames([&](const Sample& s, const Predictor& p, const Truth& truth) {
        const Q shown = m == Model::today ? renderer_today(truth, s.t, horizon_ms)
                      : m == Model::hold  ? truth.q(s.t) : of(p.predict(float(horizon_ms)).q);
        worst = std::max(worst, rotvec(rest, shown)[2] / kDeg);  // the motion is +z, in the body frame
    });
    return worst;
}

void test_constant_rate_lands_on_the_future() {
    Predictor p;
    std::uint64_t t = 1'000'000'000; std::uint32_t seq = 0;
    const std::array<float, 3> w{0.3f, -0.8f, 2.0f};  // about 125 deg/s, tumbling
    const Q q = spin(p, exp_map({0.4, 0.9, -1.1}), w, 200, t, seq);
    const Q truth = mul(q, exp_map({w[0] * 0.02, w[1] * 0.02, w[2] * 0.02}));
    const auto out = p.predict(20.f);
    CHECK(angle_deg(truth, of(out.q)) < 0.01);
    CHECK(close(out.horizon_ms, 20.f, 1e-4));
    CHECK(out.target_ns == t + 20'000'000);
    CHECK(close(out.predicted_deg, std::sqrt(0.09 + 0.64 + 4) * 0.02 / kDeg, 0.01));
}
void test_constant_acceleration_is_followed() {
    Predictor p;
    std::uint64_t t = 1'000'000'000; std::uint32_t seq = 0;
    Q q = exp_map({0.4, 0.9, -1.1});
    const float a = 30.f;  // rad/s^2, about 1700 deg/s^2: a brisk start
    for (int i = 0; i < 100; ++i) {
        const float w = a * float(i) * 1e-3f + 1.f;
        q = mul(q, exp_map({0, 0, double(w) * 1e-3}));
        t += 1'000'000; ++seq;
        p.update(pose_at(q, t, seq), imu_at(t, {0, 0, w}, seq), t);
    }
    const auto st = p.state();
    CHECK(close(st.alpha_rad_s2[2], a, 1.0));
    const double w0 = st.omega_rad_s[2], h = 0.02;
    CHECK(close(rotvec(of(st.q), of(p.predict(20.f).q))[2], w0 * h + 0.5 * a * h * h, 0.15 * kDeg));
    PredictConfig off; off.use_acceleration = false;
    CHECK(close(rotvec(of(st.q), of(luma::extrapolate(st, 20.f, off).q))[2], w0 * h, 1e-3));
}
void test_prediction_ignores_the_recentre_reference() {
    // The feed publishes conj(reference) * pose. A body-frame delta must be the same either side of it.
    Predictor a, b;
    const Q ref = exp_map({0.7, -0.2, 1.3});
    std::uint64_t ta = 1'000'000, tb = 1'000'000; std::uint32_t sa = 0, sb = 0;
    const std::array<float, 3> w{1.f, 2.f, -1.f};
    const Q q0 = exp_map({0.2, 0.2, 0.2});
    const Q qa = spin(a, q0, w, 100, ta, sa);
    const Q qb = spin(b, mul(conj(ref), q0), w, 100, tb, sb);
    (void)qa; (void)qb;
    CHECK(angle_deg(mul(conj(ref), of(a.predict(25.f).q)), of(b.predict(25.f).q)) < 0.01);
}
void test_clamped_to_the_renderers_limit() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 35.f}, 100, t, seq);  // 2000 deg/s
    const auto out = p.predict(40.f);
    CHECK(close(angle_deg(q, of(out.q)), 15, 0.02));
    CHECK(out.clamped);
}
void test_stop_clamp_ends_the_path_where_velocity_reaches_zero() {
    luma::MotionState st; st.valid = st.calibrated = true;
    st.omega_rad_s = {0, 0, 1.f}; st.alpha_rad_s2 = {0, 0, -100.f};  // stops in 10 ms
    const double stopped = 0.005 / kDeg;                             // omega * t / 2
    CHECK(close(rotvec(Q{}, of(luma::extrapolate(st, 40.f, PredictConfig{}).q))[2] / kDeg, stopped, 1e-3));
    PredictConfig off; off.stop_clamp = false;
    CHECK(rotvec(Q{}, of(luma::extrapolate(st, 40.f, off).q))[2] < -0.03);  // would run on backwards
    // Off, the braking filter is off too.
    Predictor a, b(off);
    std::uint64_t ta = 1'000'000, tb = 1'000'000; std::uint32_t sa = 0, sb = 0;
    Q qa, qb;
    qa = spin(a, qa, {0, 0, 3.f}, 100, ta, sa); qb = spin(b, qb, {0, 0, 3.f}, 100, tb, sb);
    for (int i = 1; i <= 6; ++i) {
        const float w = 3.f - 0.1f * float(i);
        qa = spin(a, qa, {0, 0, w}, 1, ta, sa); qb = spin(b, qb, {0, 0, w}, 1, tb, sb);
    }
    CHECK(a.state().alpha_rad_s2[2] < b.state().alpha_rad_s2[2] - 5);
}
void test_still_gate_flag() {
    luma::MotionState st; st.valid = st.calibrated = true;
    st.omega_rad_s = {0, 0, float(1.5 * kDeg)};
    CHECK(luma::extrapolate(st, 25.f, PredictConfig{}).predicted_deg == 0.f);
    PredictConfig off; off.still_gate = false;
    CHECK(close(luma::extrapolate(st, 25.f, off).predicted_deg, 1.5 * 0.025, 1e-4));
    st.omega_rad_s = {0, 0, float(8 * kDeg)};  // above the gate, all of it
    CHECK(close(luma::extrapolate(st, 25.f, PredictConfig{}).predicted_deg, 0.2, 1e-3));
}
void test_reject_spikes_flag() {
    PredictConfig off; off.reject_spikes = false;
    Predictor p(off);
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 1.f}, 100, t, seq);
    spin(p, q, {0, 34.f, 1.f}, 1, t, seq);
    CHECK(p.stats().rejected == 0);
    CHECK(p.state().omega_rad_s[1] > 5);
}
void test_flags_turn_prediction_off() {
    PredictConfig off; off.enabled = false;
    Predictor p(off);
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    CHECK(angle_deg(q, of(p.predict(25.f).q)) < 1e-3);
    CHECK(p.predict(25.f).predicted_deg == 0.f);
}
void test_uncalibrated_is_never_extrapolated() {
    Predictor p;
    std::uint64_t t = 1'000'000; Q q; 
    for (std::uint32_t i = 0; i < 100; ++i) {
        t += 1'000'000;
        q = mul(q, exp_map({0, 0, 3e-3}));
        auto pose = pose_at(q, t, i); pose.calibrated = false;
        p.update(pose, imu_at(t, {0, 0, 3.f}, i), t);
    }
    CHECK(!p.state().calibrated);
    CHECK(angle_deg(q, of(p.predict(25.f).q)) < 1e-3);
}
void test_before_any_sample_predict_is_identity() {
    Predictor p;
    const auto out = p.predict(25.f);
    CHECK(!p.state().valid);
    CHECK(out.q.w == 1.f && out.predicted_deg == 0.f);
}

// Prediction error versus look-ahead.
void test_error_grows_slowly_with_look_ahead_and_beats_todays_renderer() {
    for (Motion m : {Motion::wander, Motion::turns, Motion::fast}) {
        double last = 0;
        for (double h : {8.33, 16.67, 25.0, 33.3}) {
            const Score hold = model_error(Model::hold, m, h), today = model_error(Model::today, m, h),
                        full = model_error(Model::full, m, h);
            CHECK(full.rms < today.rms);
            CHECK(full.rms < hold.rms / 2);
            CHECK(full.rms >= last - 1e-3);  // further ahead is never easier
            last = full.rms;
        }
    }
    // The bar: at the Luma Ultra's usual horizon, natural head turns are shown within 0.25 deg (RMS).
    CHECK(model_error(Model::full, Motion::turns, 20).rms < 0.25);
}
void test_acceleration_helps_on_natural_motion() {
    for (Motion m : {Motion::wander, Motion::turns, Motion::fast, Motion::shake})
        for (double h : {16.67, 25.0})
            CHECK(model_error(Model::full, m, h).rms < model_error(Model::gyro, m, h).rms);
}
// Overshoot on stops.
// The frame before braking starts cannot know: it must assume the speed carries on. That part of the
// overshoot is physics, speed * (horizon - half the braking time), and no predictor can beat it. What a
// predictor owes is nothing on top of it, and to stop overshooting once the gyro has seen the stop.
double brake_floor(double horizon_ms) { return std::max(0.0, 200 * (horizon_ms - 15) / 1000); }
// The worst the view stands past the resting place, `after_ms` or later after the stop began.
double overshoot_after(Model m, Motion motion, double horizon_ms, double after_ms) {
    double worst = 0;
    const Q rest = Truth(motion, kStopAt + 0.2).q(kStopAt + 0.1);
    Bench{motion, kStopAt + 0.4, Sensor{}, PredictConfig{}}.frames([&](const Sample& s, const Predictor& p, const Truth& truth) {
        if (s.t < kStopAt + after_ms / 1000) return;
        const Q shown = m == Model::today ? renderer_today(truth, s.t, horizon_ms) : of(p.predict(float(horizon_ms)).q);
        worst = std::max(worst, rotvec(rest, shown)[2] / kDeg);
    });
    return worst;
}
void test_a_braking_stop_overshoots_no_more_than_physics_demands() {
    for (double h : {16.67, 25.0, 33.3}) {
        CHECK(stop_overshoot(Model::full, Motion::brake, h) <= brake_floor(h) + 0.25);
        CHECK(stop_overshoot(Model::today, Motion::brake, h) > brake_floor(h) + 0.8);
    }
}
void test_a_braking_stop_stops_overshooting_once_seen() {
    for (double h : {16.67, 25.0, 33.3}) {
        // 12 ms into the 30 ms stop, the prediction has come down to where the head will end up.
        CHECK(overshoot_after(Model::full, Motion::brake, h, 12) < 0.15);
        CHECK(overshoot_after(Model::today, Motion::brake, h, 12) > 0.6);
    }
}
void test_a_dead_stop_settles_at_once() {
    // Nothing foresees a dead stop: the gyro has to see it. 8 ms later the view is back where the head
    // is, while the 16 ms finite difference is still running on.
    const double h = 25;
    CHECK(overshoot_after(Model::full, Motion::slam, h, 8) < 0.1);
    CHECK(overshoot_after(Model::today, Motion::slam, h, 8) > 1);
}
void test_fusions_bias_is_taken_off() {
    // Fusion allows a bias up to 20 deg/s, and knows it. Left in, a still head would read as moving: past
    // the gate, with its noise extrapolated.
    Truth truth(Motion::rest, 3);
    Sensor biased; biased.residual_bias_dps = 12; biased.noise_dps = 0.6;
    Run run(truth, biased);
    Predictor p;
    p.set_gyro_bias({float(12 * kDeg), float(12 * kDeg), float(12 * kDeg)});
    run.samples([&](const Sample& s) {
        p.update(s.pose, s.imu, s.host_ns);
        CHECK(p.predict(25.f).predicted_deg == 0.f);
    });
    CHECK(close(p.state().omega_rad_s[0], 0, 0.03));
}
void test_a_new_bias_is_not_a_jolt() {
    // Fusion refines its bias while running; a step in it must not read as an acceleration.
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    p.set_gyro_bias({0, 0, 0.02f});
    Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    p.set_gyro_bias({0, 0, 0.0f});
    q = spin(p, q, {0, 0, 3.f}, 1, t, seq);
    CHECK(close(p.state().alpha_rad_s2[2], 0, 1));
}
void test_still_head_does_not_shake() {
    // Worn and still, with a noisy gyro: the frame-to-frame move of the prediction stays far below a pixel.
    double worst = 0;
    Q last{};
    bool first = true;
    Sensor noisy; noisy.noise_dps = 0.6;
    Bench{Motion::rest, 3, noisy, PredictConfig{}}.frames([&](const Sample&, const Predictor& p, const Truth&) {
        const Q q = of(p.predict(25.f).q);
        if (!first) worst = std::max(worst, angle_deg(last, q));
        last = q; first = false;
    });
    CHECK(worst * kPxPerDeg < 0.1);
}

// Sample handling.
void test_truncated_ms_stamps_do_not_shake_acceleration() {
    Sensor stamps; stamps.ms_stamps = true;
    const Score clean = model_error(Model::full, Motion::turns, 25), rough = model_error(Model::full, Motion::turns, 25, 12, stamps);
    CHECK(rough.rms < clean.rms * 1.1);
}
void test_host_stamp_jitter_does_not_shake_acceleration() {
    // Stamps taken on arrival, with USB jitter of several ms and bursts.
    Truth truth(Motion::turns, 12);
    Sensor plain;
    Run run(truth, plain);
    std::mt19937 rng(3);
    std::uniform_real_distribution<double> late(0, 3);
    Predictor p;
    std::vector<double> e;
    double next = 0.2;
    run.samples([&](const Sample& s) {
        auto imu = s.imu; auto pose = s.pose;
        const auto stamp = std::uint64_t((s.t + late(rng) * 1e-3) * 1e9);
        imu.t_ns = stamp; pose.t_ns = stamp;
        p.update(pose, imu, stamp);
        if (s.t < next) return;
        next += 1.0 / 120;
        e.push_back(angle_deg(truth.q(s.t + 0.025), of(p.predict(25.f).q)));
    });
    CHECK(score(e).rms < model_error(Model::full, Motion::turns, 25).rms * 1.1);
}
void test_sample_rate_is_learned() {
    for (double hz : {500.0, 2000.0}) {
        Sensor s; s.hz = hz;
        CHECK(model_error(Model::full, Motion::turns, 25, 12, s).rms < model_error(Model::today, Motion::turns, 25, 12, s).rms);
    }
}
void test_a_stuck_counter_falls_back_on_time() {
    // A protocol with no usable counter: every sample says seq 0.
    Predictor p;
    Q q;
    std::uint64_t t = 1'000'000;
    for (int i = 0; i < 200; ++i) {
        q = mul(q, exp_map({0, 0, 3e-3}));
        t += 1'000'000;
        p.update(pose_at(q, t, 0), imu_at(t, {0, 0, 3.f}, 0), t);
    }
    CHECK(std::isfinite(p.state().omega_rad_s[2]) && std::isfinite(p.state().alpha_rad_s2[2]));
    CHECK(close(p.state().omega_rad_s[2], 3.f, 0.01));
    CHECK(close(p.state().alpha_rad_s2[2], 0.f, 1.f));
}
void test_a_frozen_clock_cannot_poison_the_filters() {
    // The device clock stops while samples keep coming; then it runs again.
    Predictor p;
    std::uint64_t t = 1'000'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    for (int i = 0; i < 20000; ++i) {
        ++seq;
        p.update(pose_at(q, t, seq), imu_at(t, {0, 0, i < 19500 ? 3.f : 1.f}, seq), t);
        CHECK(std::isfinite(p.state().omega_rad_s[2]) && std::isfinite(p.state().alpha_rad_s2[2]));
    }
    CHECK(close(p.state().omega_rad_s[2], 1.f, 0.05));  // the filters have not been slowed to a halt
    t += 5'000'000'000ull;
    q = spin(p, q, {0, 0, 3.f}, 300, t, seq);
    CHECK(close(p.state().omega_rad_s[2], 3.f, 0.05));
    CHECK(close(p.state().alpha_rad_s2[2], 0.f, 2.f));
}
void test_lost_samples_are_bridged() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    const std::uint32_t resets = p.stats().resets;
    t += 9'000'000; seq += 9;  // 9 lost
    q = spin(p, q, {0, 0, 3.f}, 5, t, seq);
    CHECK(p.stats().resets == resets);
    CHECK(close(p.state().omega_rad_s[2], 3.f, 0.05));
    CHECK(close(p.state().alpha_rad_s2[2], 0.f, 5.f));
}
void test_a_gap_starts_again_from_the_next_sample() {
    // A USB drop: whatever was moving before says nothing about after.
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 5.f}, 100, t, seq);
    t += 800'000'000; seq += 800;
    q = spin(p, q, {0, 0, 0.f}, 1, t, seq);
    CHECK(p.stats().resets == 1);
    CHECK(angle_deg(q, of(p.predict(25.f).q)) < 0.01);
}
void test_a_reconnect_with_a_new_clock_starts_again() {
    Predictor p;
    std::uint64_t t = 5'000'000'000; std::uint32_t seq = 500;
    Q q = spin(p, Q{}, {0, 0, 5.f}, 100, t, seq);
    t = 1'000'000; seq = 0;  // clock and counter restart
    q = spin(p, q, {0, 0, 0.f}, 1, t, seq);
    CHECK(p.stats().resets == 1);
    CHECK(angle_deg(q, of(p.predict(25.f).q)) < 0.01);
}
void test_a_repeated_sample_is_ignored() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    const auto before = p.state();
    p.update(pose_at(q, t, seq), imu_at(t, {0, 0, 9.f}, seq), t);
    CHECK(p.state().omega_rad_s[2] == before.omega_rad_s[2]);
    CHECK(p.stats().rejected == 1);
}
void test_a_one_sample_glitch_is_ignored() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 1.f}, 100, t, seq);
    t += 1'000'000; ++seq;
    p.update(pose_at(q, t, seq), imu_at(t, {0, 34.f, 1.f}, seq), t);  // 2000 deg/s for one sample
    CHECK(p.stats().rejected == 1);
    CHECK(close(p.state().omega_rad_s[1], 0, 0.01));
    const Q after = spin(p, q, {0, 0, 1.f}, 1, t, seq);
    CHECK(angle_deg(after, of(p.predict(0.f).q)) < 0.01);
    CHECK(close(p.state().alpha_rad_s2[1], 0, 1));
}
void test_separate_glitches_are_each_ignored() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = spin(p, Q{}, {0, 0, 1.f}, 100, t, seq);
    for (int i = 0; i < 2; ++i) {
        q = spin(p, q, {0, 34.f, 1.f}, 1, t, seq);
        q = spin(p, q, {0, 0, 1.f}, 1, t, seq);
    }
    CHECK(p.stats().rejected == 2);
    CHECK(close(p.state().omega_rad_s[1], 0, 0.01));
}
void test_a_real_jolt_is_accepted_on_its_second_sample() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 1.f}, 100, t, seq);
    spin(p, q, {0, 0, 20.f}, 2, t, seq);
    CHECK(close(p.state().omega_rad_s[2], 20.f, 1.f));
}
void test_garbage_is_ignored() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    t += 1'000'000; ++seq;
    p.update(pose_at(q, t, seq), imu_at(t, {NAN, 0, 0}, seq), t);
    CHECK(std::isfinite(p.state().omega_rad_s[0]));
    t += 1'000'000; ++seq;
    auto bad = pose_at(q, t, seq); bad.q.w = INFINITY;
    p.update(bad, imu_at(t, {0, 0, 3.f}, seq), t);
    CHECK(std::isfinite(p.state().q.w));
    CHECK(p.stats().rejected == 2);
}

// The display.
void test_next_vsync() {
    const std::uint64_t period = 8'333'333;
    CHECK(luma::next_vsync_ns(1'000'000, 0, period) == period);
    CHECK(luma::next_vsync_ns(period, 0, period) == 2 * period);          // strictly after
    CHECK(luma::next_vsync_ns(period - 1, 0, period) == period);
    CHECK(luma::next_vsync_ns(100, 5 * period, period) == 100 + (5 * period - 100) % period + 0);  // an anchor in the future
    CHECK(luma::next_vsync_ns(0, 0, 0) == 0);  // no period: no grid
}
void test_horizon_matches_the_renderers_formula() {
    // Unanchored, the driver's horizon is what the renderer computes when it has no measurement.
    const luma::LookAhead cfg;
    const auto raw = luma::look_ahead_cfg(cfg);
    const auto parsed = xr::look_ahead_from(raw);
    CHECK(parsed.has_value());
    CHECK(raw == (std::array<float, 4>{10, 0.6f, 10, 40}));  // XRLinuxDriver's Luma Ultra row (src/devices/viture.c)
    PredictConfig c;
    Predictor p(c);
    for (float refresh : {60.f, 90.f, 120.f}) {
        c.refresh_hz = refresh;
        Predictor q(c);
        const auto xr_horizon = xr::prediction_horizon(parsed, std::nullopt, 0.0, 1000.0 / refresh);
        CHECK(close(q.horizon_ms(1'000'000), *xr_horizon, 1e-3));
    }
}
void test_look_ahead_is_configurable() {
    // XRLinuxDriver's look_ahead override (src/plugins/virtual_display.c): replaces the constant, drops the frametime term.
    luma::LookAhead o; o.override_ms = 22;
    CHECK(luma::look_ahead_cfg(o) == (std::array<float, 4>{22, 0, 10, 40}));
    CHECK(xr::look_ahead_from(luma::look_ahead_cfg(o)).has_value());
    PredictConfig c; c.look_ahead = o;
    CHECK(close(Predictor(c).horizon_ms(1'000'000), 27.f, 1e-3));
    // An out-of-range setting cannot reach the renderer or an unbounded horizon.
    o.override_ms = 500;
    CHECK(xr::look_ahead_from(luma::look_ahead_cfg(o)).has_value());
    luma::LookAhead junk; junk.constant_ms = NAN; junk.cap_ms = 1e9f;
    CHECK(xr::look_ahead_from(luma::look_ahead_cfg(junk)).has_value());
}
void test_horizon_reaches_the_next_vsync_when_anchored() {
    PredictConfig c;
    Predictor p(c);
    const std::uint64_t period = 8'333'333;
    p.set_vsync(100 * period, period);
    // 3 ms after a vsync: 5.33 ms to wait, + constant 10, + half the scanline adjust.
    CHECK(close(p.horizon_ms(100 * period + 3'000'000), 5.333f + 10 + 5, 1e-3));
    std::uint64_t t = 100 * period + 3'000'000 - 200'000'000; std::uint32_t seq = 0;
    spin(p, Q{}, {0, 0, 2.f}, 200, t, seq);
    CHECK(t == 100 * period + 3'000'000);
    const auto out = p.predict_next_vsync();
    CHECK(close(out.horizon_ms, 20.333f, 1e-3));
}
void test_horizon_is_capped() {
    luma::LookAhead o; o.override_ms = 100;
    PredictConfig c; c.look_ahead = o;
    CHECK(close(Predictor(c).horizon_ms(1'000'000), 40.f + 5, 1e-3));
}

// The renderer.
void test_units_and_direction_match_the_renderer() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const std::array<float, 3> w{0.3f, -0.8f, 2.0f};
    const Q q = spin(p, exp_map({0.4, 0.9, -1.1}), w, 200, t, seq);
    const auto ours = luma::omega_rad_per_ms(p.state());
    const xr::Quat delta = xr::prediction_delta(ours, 20.f, 15.f);
    const Q theirs = of_xr(xr::quat_mul(to_xr(to_f(q)), delta));
    CHECK(angle_deg(theirs, of(p.predict(20.f).q)) < 0.01);
}
void test_history_makes_the_unchanged_renderer_land_on_the_prediction() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    Q q = exp_map({0.4, 0.9, -1.1});
    for (int i = 0; i < 150; ++i) {
        const float w = 1.f + 0.02f * float(i);
        q = mul(q, exp_map({0, 0, double(w) * 1e-3}));
        t += 1'000'000; ++seq;
        p.update(pose_at(q, t, seq), imu_at(t, {0.5f, 0, w}, seq), t);
    }
    const auto h = luma::synthesize_history(p.state(), 12345.f, 25.f);
    CHECK(angle_deg(q, of(h.q[0])) < 1e-3);  // the newest pose itself is never predicted: the renderer predicts from it
    CHECK(h.device_ms[0] == 12345.f && h.device_ms[1] == 12329.f && h.device_ms[2] == 12313.f);
    xr::PoseHistory ph;
    for (std::size_t i = 0; i < 3; ++i) { ph.q[i] = to_xr(h.q[i]); ph.device_ms[i] = h.device_ms[i]; }
    const auto w = xr::angular_velocity(ph);
    CHECK(w.has_value());
    const Q shown = of_xr(xr::quat_mul(ph.q[0], xr::prediction_delta(*w, 25.f, 15.f)));
    CHECK(angle_deg(shown, of(p.predict(25.f).q)) < 0.02);
}
void test_history_beats_todays_feed_with_no_renderer_change() {
    for (Motion m : {Motion::wander, Motion::turns, Motion::fast})
        for (double h : {16.67, 25.0}) CHECK(model_error(Model::feed, m, h).rms < model_error(Model::today, m, h).rms * 0.8);
}
void test_history_steps_evenly() {
    // q1..q2 is the same rotation as q0..q1, as on a real feed, for anything that reads the older pair.
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    spin(p, exp_map({0.4, 0.9, -1.1}), {0.3f, -0.8f, 2.0f}, 200, t, seq);
    const auto h = luma::synthesize_history(p.state(), 1000.f, 25.f);
    const V3 near_step = rotvec(of(h.q[1]), of(h.q[0])), far_step = rotvec(of(h.q[2]), of(h.q[1]));
    for (std::size_t i = 0; i < 3; ++i) CHECK(close(near_step[i], far_step[i], 1e-4));
    CHECK(angle_deg(of(h.q[1]), of(h.q[0])) > 0.5);
}
void test_history_survives_a_wrong_guess_of_the_renderers_horizon() {
    // The driver guesses 20 ms; the renderer's own horizon, which it does not share, is 30. Still ahead of today.
    Truth truth(Motion::turns, 12);
    Run run(truth, Sensor{});
    Predictor p;
    std::vector<double> ours, today;
    double next = 0.2;
    run.samples([&](const Sample& s) {
        p.update(s.pose, s.imu, s.host_ns);
        if (s.t < next) return;
        next += 1.0 / 120;
        if (s.t + 0.03 >= truth.end()) return;
        const auto h = luma::synthesize_history(p.state(), 1000.f, 20.f);
        xr::PoseHistory ph;
        for (std::size_t i = 0; i < 3; ++i) { ph.q[i] = to_xr(h.q[i]); ph.device_ms[i] = h.device_ms[i]; }
        ours.push_back(angle_deg(truth.q(s.t + 0.03), of_xr(xr::quat_mul(ph.q[0], xr::prediction_delta(*xr::angular_velocity(ph), 30.f, 15.f)))));
        today.push_back(angle_deg(truth.q(s.t + 0.03), renderer_today(truth, s.t, 30)));
    });
    CHECK(score(ours).rms < score(today).rms);
}
void test_history_is_plain_when_prediction_is_off() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 0;
    const Q q = spin(p, Q{}, {0, 0, 3.f}, 100, t, seq);
    PredictConfig off; off.enabled = false;
    const auto h = luma::synthesize_history(p.state(), 1000.f, 25.f, off);
    (void)q;
    CHECK(std::memcmp(&h.q[0], &h.q[1], sizeof h.q[0]) == 0 && std::memcmp(&h.q[0], &h.q[2], sizeof h.q[0]) == 0);
    CHECK(!PredictConfig{}.synthesize_feed_history);  // the publisher's flag starts off
}
void test_history_before_any_sample_is_still_a_valid_feed() {
    Predictor p;
    const auto h = luma::synthesize_history(p.state(), 1000.f, 25.f);
    CHECK(h.q[0].w == 1.f && h.q[1].w == 1.f && h.q[2].w == 1.f);
}

// The side channel.
void test_wire_layout_is_fixed() {
    static_assert(sizeof(luma::PredictionWire) == 64);
    static_assert(std::is_standard_layout_v<luma::PredictionWire>);
    CHECK(std::string(luma::kPredictionIpcName) == "pose_prediction");
    luma::PredictionWire w{};
    CHECK(!luma::read_prediction(w));  // zeroed memory is no sample
}
void test_wire_round_trip() {
    Predictor p;
    std::uint64_t t = 1'000'000; std::uint32_t seq = 41;
    spin(p, exp_map({0.1, 0.2, 0.3}), {0.3f, -0.8f, 2.0f}, 100, t, seq);
    const auto st = p.state();
    luma::PredictionWire w{};
    luma::write_prediction(w, st);
    const auto got = luma::read_prediction(w);
    CHECK(got.has_value());
    CHECK(got->host_ns == st.host_ns && got->seq == st.seq && got->valid && got->calibrated);
    CHECK(std::memcmp(&got->q, &st.q, sizeof st.q) == 0);
    CHECK(got->omega_rad_s == st.omega_rad_s && got->alpha_rad_s2 == st.alpha_rad_s2);
}
void test_wire_torn_write_is_refused() {
    // A driver killed halfway through a write leaves the sequence odd: never read as a sample.
    luma::PredictionWire w{};
    luma::MotionState st; st.valid = true;
    luma::write_prediction(w, st);
    w.seq.fetch_add(1);
    CHECK(!luma::read_prediction(w));
}
void test_wire_refuses_another_layout() {
    luma::PredictionWire w{};
    luma::MotionState st; st.valid = true;
    luma::write_prediction(w, st);
    w.magic.store(luma::kPredictionMagic + 1);
    CHECK(!luma::read_prediction(w));
}
void test_wire_reader_never_sees_a_torn_sample() {
    luma::PredictionWire w{};
    std::atomic<bool> stop{false};
    std::thread writer([&] {
        luma::MotionState st; st.valid = true;
        for (std::uint32_t i = 1; !stop.load(std::memory_order_relaxed); ++i) {
            st.seq = i; st.host_ns = i;
            const float v = float(i & 0xffff);
            st.omega_rad_s = {v, v, v}; st.alpha_rad_s2 = {v, v, v}; st.q = {v, v, v, v};
            luma::write_prediction(w, st);
        }
    });
    std::uint64_t seen = 0;
    const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(200);
    while (std::chrono::steady_clock::now() < until) {
        const auto got = luma::read_prediction(w);
        if (!got) continue;
        const float v = float(got->seq & 0xffff);
        CHECK(got->host_ns == got->seq && got->omega_rad_s[2] == v && got->alpha_rad_s2[0] == v && got->q.w == v && got->q.z == v);
        ++seen;
    }
    stop = true;
    writer.join();
    CHECK(seen > 100);
}

void report() {
    const Motion motions[] = {Motion::wander, Motion::turns, Motion::fast, Motion::shake};
    const double horizons[] = {8.33, 16.67, 20, 25, 33.3, 40};
    std::printf("Prediction error, degrees RMS / worst (1 deg = about %.0f px), 120 frames/s, 1 kHz gyro\n", kPxPerDeg);
    for (Motion m : motions) {
        std::printf("\n%-16s", name(m));
        for (double h : horizons) std::printf("  %5.1f ms    ", h);
        std::printf("\n");
        for (Model model : {Model::hold, Model::today, Model::feed, Model::gyro, Model::full}) {
            std::printf("%-16s", name(model));
            for (double h : horizons) { const Score s = model_error(model, m, h); std::printf("  %5.2f /%5.2f", s.rms, s.max); }
            std::printf("\n");
        }
    }
    std::printf("\nOvershoot past the resting place on a stop from 200 deg/s, degrees\n%-16s", "");
    for (double h : horizons) std::printf("  %5.1f ms", h);
    for (Motion m : {Motion::brake, Motion::slam}) {
        std::printf("\n%s\n", name(m));
        for (Model model : {Model::today, Model::gyro, Model::full}) {
            std::printf("%-16s", name(model));
            for (double h : horizons) std::printf("  %8.2f", stop_overshoot(model, m, h));
            std::printf("\n");
        }
    }
    // Cost of one update, the part that runs at IMU rate in the driver's hot loop.
    Predictor p;
    std::uint64_t t = 1'000'000;
    const auto begin = std::chrono::steady_clock::now();
    const int n = 2'000'000;
    for (int i = 0; i < n; ++i) { t += 1'000'000; p.update(pose_at(Q{}, t, std::uint32_t(i)), imu_at(t, {0.1f, 0.2f, float(i % 7) * 0.01f}, std::uint32_t(i)), t); }
    const double ns = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - begin).count() / n;
    std::printf("\nupdate(): %.0f ns per sample\n", ns);
}
}

int main(int argc, char** argv) {
    if (argc > 1 && std::string(argv[1]) == "--report") { report(); return 0; }
    using Test = void (*)();
    const Test tests[] = {
        test_constant_rate_lands_on_the_future, test_constant_acceleration_is_followed, test_prediction_ignores_the_recentre_reference,
        test_clamped_to_the_renderers_limit, test_stop_clamp_ends_the_path_where_velocity_reaches_zero, test_still_gate_flag,
        test_reject_spikes_flag, test_flags_turn_prediction_off, test_uncalibrated_is_never_extrapolated,
        test_before_any_sample_predict_is_identity,
        test_error_grows_slowly_with_look_ahead_and_beats_todays_renderer, test_acceleration_helps_on_natural_motion,
        test_a_braking_stop_overshoots_no_more_than_physics_demands, test_a_braking_stop_stops_overshooting_once_seen, test_a_dead_stop_settles_at_once, test_fusions_bias_is_taken_off, test_a_new_bias_is_not_a_jolt, test_still_head_does_not_shake,
        test_truncated_ms_stamps_do_not_shake_acceleration, test_host_stamp_jitter_does_not_shake_acceleration, test_sample_rate_is_learned,
        test_a_stuck_counter_falls_back_on_time, test_a_frozen_clock_cannot_poison_the_filters, test_lost_samples_are_bridged, test_a_gap_starts_again_from_the_next_sample, test_a_reconnect_with_a_new_clock_starts_again,
        test_a_repeated_sample_is_ignored, test_a_one_sample_glitch_is_ignored, test_separate_glitches_are_each_ignored, test_a_real_jolt_is_accepted_on_its_second_sample,
        test_garbage_is_ignored,
        test_next_vsync, test_horizon_matches_the_renderers_formula, test_look_ahead_is_configurable,
        test_horizon_reaches_the_next_vsync_when_anchored, test_horizon_is_capped,
        test_units_and_direction_match_the_renderer, test_history_makes_the_unchanged_renderer_land_on_the_prediction,
        test_history_beats_todays_feed_with_no_renderer_change, test_history_steps_evenly, test_history_survives_a_wrong_guess_of_the_renderers_horizon,
        test_history_is_plain_when_prediction_is_off, test_history_before_any_sample_is_still_a_valid_feed,
        test_wire_layout_is_fixed, test_wire_round_trip, test_wire_torn_write_is_refused, test_wire_refuses_another_layout, test_wire_reader_never_sees_a_torn_sample,
    };
    int failed = 0;
    for (Test t : tests) {
        try { t(); }
        catch (const std::exception& e) { std::cout << "FAIL " << e.what() << "\n"; ++failed; }
    }
    if (failed) { std::cout << failed << " failed\n"; return 1; }
    std::cout << "ok\n";
    return 0;
}
