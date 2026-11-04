#pragma once
// Synthetic IMU: a known head motion in, what a gyro, accelerometer and magnetometer would read
// out, with noise, bias, slow bias change and the lever arm of glasses that sit forward of the
// neck. Numbers are generic MEMS values on the pessimistic side, not measurements of the Luma
// Ultra: nobody has read this hardware's noise yet.
#include "luma/types.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <functional>
#include <random>
#include <vector>

namespace luma::sim {
constexpr double kPi = 3.14159265358979323846;
constexpr double kDeg = kPi / 180;
constexpr double kG = 9.80665;
using V = std::array<double, 3>;
struct Q { double w = 1, x = 0, y = 0, z = 0; };

inline Q mul(Q a, Q b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
inline Q conj(Q q) { return {q.w, -q.x, -q.y, -q.z}; }
inline Q from(Quatf q) { return {q.w, q.x, q.y, q.z}; }
inline Q normalized(Q q) {
    const double n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    return {q.w / n, q.x / n, q.y / n, q.z / n};
}
inline Q axis_angle(V a, double rad) {
    const double n = std::sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]), s = std::sin(rad / 2) / n;
    return {std::cos(rad / 2), a[0] * s, a[1] * s, a[2] * s};
}
inline Q exp_step(V w, double dt) {
    const double n = std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
    return n * dt < 1e-12 ? Q{} : axis_angle(w, n * dt);
}
inline V rotate(Q q, V v) {
    const Q r = mul(mul(q, Q{0, v[0], v[1], v[2]}), conj(q));
    return {r.x, r.y, r.z};
}
inline V cross(V a, V b) {
    return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}
// Angle in degrees between where two orientations put world up, seen from the body.
inline double tilt_deg(Q est, Q truth) {
    const V a = rotate(conj(est), {0, 0, 1}), b = rotate(conj(truth), {0, 0, 1});
    const V c = cross(a, b);
    return std::atan2(std::sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / kDeg;
}
inline double unwrap(double deg) {
    while (deg > 180) deg -= 360;
    while (deg < -180) deg += 360;
    return deg;
}
// Heading of est relative to truth about world up, degrees, in -180..180.
inline double yaw_deg(Q est, Q truth) {
    const Q d = mul(truth, conj(est));
    return unwrap(2 * std::atan2(d.z, d.w) / kDeg);
}

// Body-frame angular velocity (rad/s) at time t (s).
using Motion = std::function<V(double)>;
inline V hold(double) { return {0, 0, 0}; }

struct ImuModel {
    double rate_hz = 1000;
    double gyro_sigma = 0.005;   // rad/s per sample
    double accel_sigma = 0.05;   // m/s2 per sample
    V gyro_bias{0, 0, 0};
    V bias_change{0, 0, 0};      // Added to gyro_bias over bias_tau_s: warm-up.
    double bias_tau_s = 120;
    double bias_walk = 0;        // rad/s per sqrt(s)
    V accel_bias{0, 0, 0};
    V lever_m{0, 0, 0};          // Sensor offset from the rotation centre, body frame.
    Q start{};
    V mag_world{0.2, 0, -0.45};  // Gauss
    bool mag = false;
    std::uint64_t t0_ns = 5'000'000'000ULL;
    unsigned seed = 1;
};

class Imu {
public:
    explicit Imu(const ImuModel& m) : m_(m), truth_(m.start), rng_(m.seed), bias_(m.gyro_bias) {}
    // Advance one sample period under `motion`, return what the sensor reads then.
    ImuSample step(const Motion& motion) {
        const double dt = 1.0 / m_.rate_hz;
        const int sub = 4;
        for (int i = 0; i < sub; ++i) {
            const V w = motion(t_ + (i + 0.5) * dt / sub);
            truth_ = normalized(mul(truth_, exp_step(w, dt / sub)));
        }
        t_ += dt;
        ++n_;
        const V w = motion(t_);
        const double h = 1e-4;
        const V wa = motion(t_ - h), wb = motion(t_ + h);
        const V alpha{(wb[0] - wa[0]) / (2 * h), (wb[1] - wa[1]) / (2 * h), (wb[2] - wa[2]) / (2 * h)};
        const V centripetal = cross(w, cross(w, m_.lever_m));
        const V tangential = cross(alpha, m_.lever_m);
        const V up = rotate(conj(truth_), {0, 0, kG});
        const double warm = 1 - std::exp(-t_ / m_.bias_tau_s);
        ImuSample s{};
        for (std::size_t i = 0; i < 3; ++i) {
            bias_[i] += m_.bias_walk * std::sqrt(dt) * n01_(rng_);
            s.gyro_rad_s[i] = float(w[i] + bias_[i] + m_.bias_change[i] * warm + m_.gyro_sigma * n01_(rng_));
            s.accel_m_s2[i] = float(up[i] + centripetal[i] + tangential[i] + m_.accel_bias[i] + m_.accel_sigma * n01_(rng_));
        }
        if (m_.mag) {
            const V b = rotate(conj(truth_), m_.mag_world);
            s.mag = std::array<float, 3>{float(b[0]), float(b[1]), float(b[2])};
        }
        s.t_ns = m_.t0_ns + std::uint64_t(t_ * 1e9);
        s.seq = n_;
        return s;
    }
    Q truth() const { return truth_; }
    double t() const { return t_; }
    double dt() const { return 1.0 / m_.rate_hz; }
    V bias_now() const {
        const double warm = 1 - std::exp(-t_ / m_.bias_tau_s);
        return {bias_[0] + m_.bias_change[0] * warm, bias_[1] + m_.bias_change[1] * warm, bias_[2] + m_.bias_change[2] * warm};
    }
    std::mt19937& rng() { return rng_; }

private:
    ImuModel m_;
    Q truth_;
    std::mt19937 rng_;
    std::normal_distribution<double> n01_{0, 1};
    V bias_;
    double t_ = 0;
    std::uint32_t n_ = 0;
};

// Worn and resting: breathing, pulse and small corrections. Bounded angle, no net turn.
inline V worn_rest(double t) {
    return {0.006 * std::sin(2 * kPi * 0.27 * t + 0.7) + 0.004 * std::sin(2 * kPi * 3.1 * t),
            0.012 * std::sin(2 * kPi * 0.31 * t) + 0.008 * std::sin(2 * kPi * 1.2 * t + 1) + 0.005 * std::sin(2 * kPi * 4.3 * t),
            0.015 * std::sin(2 * kPi * 0.23 * t + 2) + 0.010 * std::sin(2 * kPi * 0.9 * t) + 0.005 * std::sin(2 * kPi * 3.7 * t + 1)};
}
// Minimum-jerk rate of a turn of `deg` degrees taking `len` seconds from `at`.
inline double turn_rate(double t, double at, double len, double deg) {
    const double u = (t - at) / len;
    if (u <= 0 || u >= 1) return 0;
    return deg * kDeg * 30 * u * u * (1 - u) * (1 - u) / len;
}
// Desk work: worn_rest plus a look left and back, and a look down and back, every 20 s.
inline V worn_working(double t) {
    V w = worn_rest(t);
    const double p = std::fmod(t, 20);
    w[2] += turn_rate(p, 2, 0.8, 35) + turn_rate(p, 5, 0.8, -35);
    w[1] += turn_rate(p, 10, 0.7, 20) + turn_rate(p, 13, 0.7, -20);
    return w;
}
// A restless head: random wander on all axes, pulled back to centre (20 s), smoothed to about half
// a second. rms_deg = 3 turns at about 1.8 deg/s rms with no calm moment: harsher than a head at rest.
class Wander {
public:
    Wander(double rms_deg, unsigned seed, double seconds) : rate_(std::size_t(seconds / kStep) + 2) {
        std::mt19937 rng(seed);
        std::normal_distribution<double> n01;
        const double tau = 20, sigma = rms_deg * kDeg * std::sqrt(2 / tau);
        V angle{};
        for (V& r : rate_)
            for (std::size_t i = 0; i < 3; ++i) {
                const double d = -angle[i] / tau * kStep + sigma * std::sqrt(kStep) * n01(rng);
                angle[i] += d;
                r[i] = d / kStep;
            }
        for (int pass = 0; pass < 40; ++pass)
            for (std::size_t k = 1; k < rate_.size(); ++k)
                for (std::size_t i = 0; i < 3; ++i) rate_[k][i] += (rate_[k - 1][i] - rate_[k][i]) * 0.5;
    }
    V at(double t) const { return rate_[std::min(std::size_t(t / kStep), rate_.size() - 1)]; }

private:
    static constexpr double kStep = 0.01;
    std::vector<V> rate_;
};
}  // namespace luma::sim
