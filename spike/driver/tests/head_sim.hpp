#pragma once
// Simulated head motion for the predictor's tests. Motion is defined as body-frame angular rate and
// integrated finely, so the gyro the predictor reads and the orientation it is scored against can
// never disagree. No hardware; nothing here opens a device.
#include "luma/types.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <functional>
#include <random>
#include <vector>

namespace headsim {
constexpr double kPi = 3.14159265358979323846, kDeg = kPi / 180;
using V3 = std::array<double, 3>;
struct Q { double w = 1, x = 0, y = 0, z = 0; };
inline Q mul(Q a, Q b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
inline Q conj(Q q) { return {q.w, -q.x, -q.y, -q.z}; }
inline Q exp_map(V3 r) {  // rotation vector (rad) to quaternion
    const double a = std::sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]);
    const double k = a < 1e-12 ? 0.5 : std::sin(a / 2) / a;
    return {std::cos(a / 2), r[0] * k, r[1] * k, r[2] * k};
}
inline Q of(luma::Quatf q) { return {q.w, q.x, q.y, q.z}; }
inline luma::Quatf to_f(Q q) { return {float(q.w), float(q.x), float(q.y), float(q.z)}; }
// Rotation vector of conj(a) * b: b as seen from a, body frame, radians.
inline V3 rotvec(Q a, Q b) {
    Q d = mul(conj(a), b);
    if (d.w < 0) d = {-d.w, -d.x, -d.y, -d.z};
    const double s = std::sqrt(d.x * d.x + d.y * d.y + d.z * d.z);
    if (s < 1e-14) return {0, 0, 0};
    const double k = 2 * std::atan2(s, d.w) / s;
    return {d.x * k, d.y * k, d.z * k};
}
inline double angle_deg(Q a, Q b) {
    const V3 r = rotvec(a, b);
    return std::sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]) / kDeg;
}

// Speed along a minimum-jerk move of `amp` degrees over `dur` seconds, deg/s, u = t / dur.
inline double min_jerk_rate(double amp, double dur, double t) {
    if (t <= 0 || t >= dur) return 0;
    const double u = t / dur;
    return amp / dur * 30 * u * u * (1 - u) * (1 - u);
}

enum class Motion { rest, wander, turns, fast, shake, brake, slam };
inline const char* name(Motion m) {
    switch (m) {
    case Motion::rest: return "rest";     // worn, still
    case Motion::wander: return "wander"; // slow drifting look-around
    case Motion::turns: return "turns";   // natural head turns with holds, up to ~225 deg/s
    case Motion::fast: return "fast";     // 90 deg in 0.3 s and back, ~560 deg/s
    case Motion::shake: return "shake";   // 2.5 Hz shake of +-15 deg: constant reversals
    case Motion::brake: return "brake";   // steady 200 deg/s, then brakes to a stop over 30 ms
    case Motion::slam: return "slam";     // steady 200 deg/s, then stops dead within 1 ms
    }
    return "?";
}
constexpr double kStopAt = 1.0;  // brake, slam: when the stop begins

// Body-frame rate at t (s), rad/s.
inline V3 rate(Motion m, double t) {
    switch (m) {
    case Motion::rest: return {0, 0, 0};
    case Motion::wander:
        return {8 * kDeg * std::sin(2 * kPi * 0.31 * t + 1), 14 * kDeg * std::sin(2 * kPi * 0.47 * t),
                35 * kDeg * std::sin(2 * kPi * 0.73 * t) + 18 * kDeg * std::sin(2 * kPi * 1.3 * t + 2)};
    case Motion::turns: {
        static constexpr double amp[] = {30, -45, 60, -20, 40, -65}, dur[] = {0.35, 0.45, 0.5, 0.3, 0.4, 0.55};
        static constexpr double hold = 0.3;
        double cycle = 0;
        for (double d : dur) cycle += d + hold;
        double c = std::fmod(t, cycle), t0 = 0;
        for (int i = 0; i < 6; ++i) {
            if (c < t0 + dur[i]) return {0, 0, min_jerk_rate(amp[i], dur[i], c - t0) * kDeg};
            t0 += dur[i] + hold;
        }
        return {0, 0, 0};
    }
    case Motion::fast: {
        const double c = std::fmod(t, 1.6);
        return {0, 0, (min_jerk_rate(90, 0.3, c - 0.2) - min_jerk_rate(90, 0.3, c - 1.0)) * kDeg};
    }
    case Motion::shake: return {0, 0, 15 * 2 * kPi * 2.5 * std::cos(2 * kPi * 2.5 * t) * kDeg};
    case Motion::brake:
    case Motion::slam: {
        const double ramp = m == Motion::brake ? 0.030 : 0.001;
        const double k = t < kStopAt ? 1 : t >= kStopAt + ramp ? 0 : 1 - (t - kStopAt) / ramp;
        return {0, 0, 200 * k * kDeg};
    }
    }
    return {0, 0, 0};
}

// Exact orientation and rate on a fine grid, from a tilted start pose so nothing lines up by luck.
class Truth {
public:
    Truth(Motion m, double seconds) {
        std::size_t n = std::size_t(seconds / kStep) + 2;
        q_.reserve(n); w_.reserve(n);
        Q q = exp_map({20 * kDeg, -35 * kDeg, 50 * kDeg});
        for (std::size_t i = 0; i < n; ++i) {
            const double t = double(i) * kStep;
            q_.push_back(q); w_.push_back(rate(m, t));
            const V3 mid = rate(m, t + kStep / 2);
            q = mul(q, exp_map({mid[0] * kStep, mid[1] * kStep, mid[2] * kStep}));
        }
    }
    Q q(double t) const { return q_[index(t)]; }
    V3 w(double t) const { return w_[index(t)]; }
    double end() const { return double(q_.size() - 2) * kStep; }
private:
    static constexpr double kStep = 2e-5;  // 0.02 ms: at 600 deg/s, 0.012 deg
    std::size_t index(double t) const { return std::min(q_.size() - 1, std::size_t(std::max(t, 0.0) / kStep + 0.5)); }
    std::vector<Q> q_;
    std::vector<V3> w_;
};

struct Sensor {
    double hz = 1000;
    double noise_dps = 0.3;      // white noise per sample, deg/s: pessimistic for a MEMS gyro at 1 kHz
    double lsb_dps = 0.061;      // 16 bit at +-2000 deg/s
    double residual_bias_dps = 0.05;
    double lag_ms = 1;           // the sensor's own filter: the mean rate over this long
    bool ms_stamps = false;      // device stamps truncated to whole ms, a ms low about half the time
    double host_jitter_ms = 0.2; // USB arrival jitter on the host stamp (|gaussian|)
    std::uint32_t seed = 7;
};
struct Sample { double t; luma::ImuSample imu; luma::PoseOut pose; std::uint64_t host_ns; };

// Each sample is handed to `each`, then the frame reads due at 120 Hz just after it call `frame`.
class Run {
public:
    Run(const Truth& truth, Sensor s) : truth_(truth), s_(s), rng_(s.seed) {}
    template <class Each> void samples(Each&& each) {
        std::normal_distribution<double> gauss(0, 1);
        std::uint32_t seq = 0;
        for (double t = 0.05; t < truth_.end(); t += 1 / s_.hz) {
            const int steps = std::max(1, int(s_.lag_ms * 2));
            V3 mean{0, 0, 0};
            for (int k = 0; k < steps; ++k) {
                const V3 w = truth_.w(t - s_.lag_ms * 1e-3 * double(k) / double(steps));
                for (int a = 0; a < 3; ++a) mean[std::size_t(a)] += w[std::size_t(a)] / double(steps);
            }
            std::array<float, 3> gyro{};
            for (int a = 0; a < 3; ++a) {
                double dps = mean[std::size_t(a)] / kDeg + gauss(rng_) * s_.noise_dps + s_.residual_bias_dps;
                if (s_.lsb_dps > 0) dps = std::round(dps / s_.lsb_dps) * s_.lsb_dps;
                gyro[std::size_t(a)] = float(dps * kDeg);
            }
            auto stamp = std::uint64_t(t * 1e9 + 0.5);
            if (s_.ms_stamps) {  // Carina: (uint32)((ts - ts0) * 1000), see XRLinuxDriver viture_device_carina.h
                const double ms = std::floor(std::floor(t * 1000 + 0.5) - ((seq * 2654435761u) >> 31 ? 0.5 : 0.0));
                stamp = std::uint64_t(ms) * 1'000'000ull;
            }
            const auto host = std::uint64_t((t + (1 + std::fabs(gauss(rng_)) * s_.host_jitter_ms) * 1e-3) * 1e9);
            luma::ImuSample imu{stamp, gyro, {0, 0, 9.81f}, std::nullopt, seq};
            each(Sample{t, imu, luma::PoseOut{to_f(truth_.q(t)), stamp, seq, true}, host});
            ++seq;
        }
    }
private:
    const Truth& truth_;
    Sensor s_;
    std::mt19937 rng_;
};
}
