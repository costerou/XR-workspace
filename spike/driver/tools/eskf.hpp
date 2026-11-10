#pragma once
// D11 candidate, replay only: an error-state Kalman filter with the gyro bias in the state, judged against luma::Fusion
// (Mahony) by tools/fusion_review.cpp. Not built into the driver. See spike/driver/docs/fusion-eskf-review.md.
//
// Written from the textbook: Solà, "Quaternion kinematics for the error-state Kalman filter", 2017, sections 5 and 7
// (local error angle, right-multiplied). Nominal state: orientation q (body to world, world z up) and gyro bias b. Error
// state: dtheta (body frame, q_true = q * exp(dtheta)) and db. Six states, no magnetometer, so the bias about world up is
// seen only when the head is still: gravity alone cannot observe heading.
//
// Measurements:
// - gravity, every sample: the unit accelerometer reading against world up seen from the body, with a noise that grows
//   with the reading's distance from 1 g and with the turn rate, so linear and centripetal acceleration count for little;
// - zero rate, once per 100 ms block that is still (desk) or quiet (worn, head resting): the block's mean gyro is the bias,
//   with a looser noise for a resting head, refused by a chi-square gate when it disagrees, as a slow turn would.
//
// Same frames and stillness tests as luma::Fusion, so a difference in the replay comes from the filter, not the plumbing.
#include "luma/types.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

namespace luma::eskf {
inline std::array<double, 3> operator*(std::array<double, 3> a, double k) { return {a[0] * k, a[1] * k, a[2] * k}; }

struct Config {
    double gyro_sigma = 0.005;        // rad/s per sample: white gyro noise
    double bias_walk = 1e-5;          // rad/s per sqrt(s): how fast the bias may move (warm-up, temperature)
    double bias0_sigma = 0.05;        // rad/s: bias uncertainty at start (3 deg/s)
    double tilt0_sigma = 0.05;        // rad: tilt uncertainty after the first sample's gravity
    double accel_sigma = 0.08;        // unit-vector noise of a still accelerometer reading
    double accel_norm_k = 20;         // extra noise per fraction of g the reading is off 1 g
    double accel_rate_k = 0.1;        // extra noise per rad/s of turn rate
    double accel_rate_gate = 1.0;     // rad/s: the noise grows without bound toward this rate, as Fusion's gain fades
    double accel_gate = 0.1;          // fraction of g off 1 g at which the reading is not used
    double still_sigma = 0.0005;      // rad/s: a desk-still block mean's spread about the bias, above sensor noise
    double quiet_sigma = 0.03;        // rad/s: a resting head's block mean about the bias (wander, not noise)
    double zero_rate_chi2 = 11.3;     // 3 dof, 1 %: a block that disagrees more is a real turn
    double calibrated_sigma = 0.0087; // rad/s (0.5 deg/s) on every bias axis: calibrated
    double calibrated_timeout_s = 2.5;
    double max_gap_s = 0.25;
};

class Filter {
public:
    explicit Filter(const Config& c = {}) : c_(c) {}

    PoseOut update(const ImuSample& s) {
        const V gyro = to_v(s.gyro_rad_s), accel = to_v(s.accel_m_s2);
        if (!have_last_) {
            have_last_ = true;
            last_t_ = s.t_ns;
            last_gyro_ = gyro;
            const double n = norm(accel);
            if (n > 0.5 * kG && n < 1.5 * kG) q_ = attitude_from_up(accel * (1 / n));
            for (int i = 0; i < 2; ++i) P_[i][i] = c_.tilt0_sigma * c_.tilt0_sigma;
            for (int i = 3; i < 6; ++i) P_[i][i] = c_.bias0_sigma * c_.bias0_sigma;
            return out(s);
        }
        const double dt = double(std::int64_t(s.t_ns - last_t_)) * 1e-9;
        last_t_ = s.t_ns;
        if (dt > 0 && dt <= c_.max_gap_s) {
            stream_s_ += dt;
            predict(gyro, dt);
            correct_gravity(accel, sub(gyro, b_));
            observe(gyro, accel, dt);
        }
        last_gyro_ = gyro;
        return out(s);
    }
    std::array<float, 3> bias() const { return {float(b_[0]), float(b_[1]), float(b_[2])}; }
    std::array<float, 3> up_body() const { const V u = up_in_body(q_); return {float(u[0]), float(u[1]), float(u[2])}; }
    double calibrated_after_s() const { return calibrated_after_; }

private:
    using V = std::array<double, 3>;
    using M3 = std::array<V, 3>;
    using M6 = std::array<std::array<double, 6>, 6>;
    struct Q { double w = 1, x = 0, y = 0, z = 0; };
    static constexpr double kG = 9.80665, kBlockS = 0.1;
    static constexpr int kStillRunBlocks = 5;

    static V to_v(const std::array<float, 3>& a) { return {a[0], a[1], a[2]}; }
    static V add(V a, V b) { return {a[0] + b[0], a[1] + b[1], a[2] + b[2]}; }
    static V sub(V a, V b) { return {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }
    static double dot(V a, V b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    static double norm(V a) { return std::sqrt(dot(a, a)); }
    static V cross(V a, V b) { return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]}; }
    static M3 skew(V v) { return {V{0, -v[2], v[1]}, V{v[2], 0, -v[0]}, V{-v[1], v[0], 0}}; }
    static Q mul(Q a, Q b) {
        return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
                a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
    }
    static Q normalized(Q q) {
        const double n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
        return {q.w / n, q.x / n, q.y / n, q.z / n};
    }
    static Q exp_map(V phi) {
        const double a = norm(phi);
        const double k = a < 1e-8 ? 0.5 - a * a / 48 : std::sin(a / 2) / a;
        return {std::cos(a / 2), phi[0] * k, phi[1] * k, phi[2] * k};
    }
    static V up_in_body(Q q) {
        return {2 * (q.x * q.z - q.w * q.y), 2 * (q.y * q.z + q.w * q.x), 1 - 2 * (q.x * q.x + q.y * q.y)};
    }
    // Same convention as luma::Fusion: body forward faces world x.
    static Q attitude_from_up(V up) {
        V fwd{1, 0, 0};
        if (std::fabs(up[0]) > 0.99) fwd = {0, 0, up[0] > 0 ? -1.0 : 1.0};
        V x = sub(fwd, up * dot(fwd, up));
        x = x * (1 / norm(x));
        const V y = cross(up, x);
        const double m00 = x[0], m01 = x[1], m02 = x[2], m10 = y[0], m11 = y[1], m12 = y[2], m20 = up[0], m21 = up[1], m22 = up[2];
        const double t = m00 + m11 + m22;
        Q q;
        if (t > 0) {
            const double s = std::sqrt(t + 1) * 2;
            q = {s / 4, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s};
        } else if (m00 >= m11 && m00 >= m22) {
            const double s = std::sqrt(1 + m00 - m11 - m22) * 2;
            q = {(m21 - m12) / s, s / 4, (m01 + m10) / s, (m02 + m20) / s};
        } else if (m11 >= m22) {
            const double s = std::sqrt(1 + m11 - m00 - m22) * 2;
            q = {(m02 - m20) / s, (m01 + m10) / s, s / 4, (m12 + m21) / s};
        } else {
            const double s = std::sqrt(1 + m22 - m00 - m11) * 2;
            q = {(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, s / 4};
        }
        return normalized(q);
    }
    static bool invert3(const M3& a, M3& inv) {
        const double det = a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
                           a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]);
        if (!(std::fabs(det) > 1e-300)) return false;
        const double k = 1 / det;
        inv = {V{(a[1][1] * a[2][2] - a[1][2] * a[2][1]) * k, (a[0][2] * a[2][1] - a[0][1] * a[2][2]) * k, (a[0][1] * a[1][2] - a[0][2] * a[1][1]) * k},
               V{(a[1][2] * a[2][0] - a[1][0] * a[2][2]) * k, (a[0][0] * a[2][2] - a[0][2] * a[2][0]) * k, (a[0][2] * a[1][0] - a[0][0] * a[1][2]) * k},
               V{(a[1][0] * a[2][1] - a[1][1] * a[2][0]) * k, (a[0][1] * a[2][0] - a[0][0] * a[2][1]) * k, (a[0][0] * a[1][1] - a[0][1] * a[1][0]) * k}};
        return true;
    }

    // Phi = [[I - [w dt]x, -I dt], [0, I]] on the error state. The nominal step is luma::Fusion's: Savage's rotation
    // vector over two gyro samples with the coning term, so both filters integrate the gyro identically.
    void predict(V gyro, double dt) {
        const V a = sub(last_gyro_, b_) * dt, b = sub(gyro, b_) * dt;
        q_ = normalized(mul(q_, exp_map(add(add(a, b) * 0.5, cross(a, b) * (1.0 / 12)))));
        const V w = add(a, b) * (0.5 / dt);
        M6 F{};
        for (int i = 0; i < 6; ++i) F[i][i] = 1;
        const M3 s = skew(w * dt);
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) F[i][j] -= s[i][j];
            F[i][i + 3] = -dt;
        }
        M6 FP{};
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) {
                double v = 0;
                for (int k = 0; k < 6; ++k) v += F[i][k] * P_[k][j];
                FP[i][j] = v;
            }
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) {
                double v = 0;
                for (int k = 0; k < 6; ++k) v += FP[i][k] * F[j][k];
                P_[i][j] = v;
            }
        const double qa = c_.gyro_sigma * dt, qb = c_.bias_walk * c_.bias_walk * dt;
        for (int i = 0; i < 3; ++i) { P_[i][i] += qa * qa; P_[i + 3][i + 3] += qb; }
    }

    // One 3-row update with H = [Ht, Hb] (each 3x3), innovation y and noise r on every row. False if gated out.
    bool update3(const M3& Ht, const M3& Hb, V y, double r, double chi2_gate) {
        std::array<std::array<double, 3>, 6> PHt{};  // P H^T, 6x3
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 3; ++j) {
                double v = 0;
                for (int k = 0; k < 3; ++k) v += P_[i][k] * Ht[j][k] + P_[i][k + 3] * Hb[j][k];
                PHt[i][j] = v;
            }
        M3 S{}, Si{};
        for (int i = 0; i < 3; ++i)
            for (int j = 0; j < 3; ++j) {
                double v = 0;
                for (int k = 0; k < 3; ++k) v += Ht[i][k] * PHt[k][j] + Hb[i][k] * PHt[k + 3][j];
                S[i][j] = v + (i == j ? r : 0);
            }
        if (!invert3(S, Si)) return false;
        if (chi2_gate > 0) {
            double m = 0;
            for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j) m += y[i] * Si[i][j] * y[j];
            if (m > chi2_gate) return false;
        }
        std::array<std::array<double, 3>, 6> K{};
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 3; ++j) K[i][j] = PHt[i][0] * Si[0][j] + PHt[i][1] * Si[1][j] + PHt[i][2] * Si[2][j];
        std::array<double, 6> dx{};
        for (int i = 0; i < 6; ++i) dx[std::size_t(i)] = K[i][0] * y[0] + K[i][1] * y[1] + K[i][2] * y[2];
        // Joseph form: P = (I - KH) P (I - KH)^T + K R K^T.
        M6 A{};
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) {
                double kh = 0;
                for (int k = 0; k < 3; ++k) kh += K[i][k] * (j < 3 ? Ht[k][j] : Hb[k][j - 3]);
                A[i][j] = (i == j ? 1 : 0) - kh;
            }
        M6 AP{}, out{};
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) {
                double v = 0;
                for (int k = 0; k < 6; ++k) v += A[i][k] * P_[k][j];
                AP[i][j] = v;
            }
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) {
                double v = 0;
                for (int k = 0; k < 6; ++k) v += AP[i][k] * A[j][k];
                for (int k = 0; k < 3; ++k) v += K[i][k] * r * K[j][k];
                out[i][j] = v;
            }
        for (int i = 0; i < 6; ++i)
            for (int j = 0; j < 6; ++j) P_[i][j] = 0.5 * (out[i][j] + out[j][i]);
        q_ = normalized(mul(q_, exp_map({dx[0], dx[1], dx[2]})));
        b_ = add(b_, {dx[3], dx[4], dx[5]});
        return true;
    }

    // z = a / |a| against h = up seen from the body; h(q * exp(dtheta)) = u + [u]x dtheta.
    void correct_gravity(V accel, V omega) {
        const double n = norm(accel);
        const double off = std::fabs(n / kG - 1);
        if (off > c_.accel_gate) return;
        const V u = up_in_body(q_);
        const V y = sub(accel * (1 / n), u);
        const double rate = norm(omega);
        if (rate >= c_.accel_rate_gate) return;
        const double sigma = (c_.accel_sigma + c_.accel_norm_k * off + c_.accel_rate_k * rate) / (1 - rate / c_.accel_rate_gate);
        update3(skew(u), M3{}, y, sigma * sigma, 0);
    }

    // 100 ms blocks, the same tests luma::Fusion uses for "still" and "quiet".
    void observe(V gyro, V accel, double dt) {
        block_s_ += dt;
        ++block_n_;
        g_sum_ = add(g_sum_, gyro);
        a_sum_ = add(a_sum_, accel);
        for (int i = 0; i < 3; ++i) { g_sq_[i] += gyro[i] * gyro[i]; a_sq_[i] += accel[i] * accel[i]; }
        if (block_s_ < kBlockS - 1e-9) return;
        const double n = block_n_;
        const V g = g_sum_ * (1 / n), a = a_sum_ * (1 / n);
        const auto std3 = [n](V sum, V sq) {
            double v = 0;
            for (int i = 0; i < 3; ++i) v += std::max(0.0, sq[i] / n - sum[i] * sum[i] / (n * n));
            return std::sqrt(v);
        };
        const double gyro_std = std3(g_sum_, g_sq_), accel_std = std3(a_sum_, a_sq_);
        const bool level_force = std::fabs(norm(a) / kG - 1) < 0.15;
        const double noise_step = 2 * std::sqrt(2.0) * gyro_std / std::sqrt(n);
        const bool calm = !have_prev_ || norm(sub(g, prev_g_)) < 0.002 + noise_step;
        const bool slow = norm(g) < 0.175;
        const bool still = gyro_std < 0.015 && accel_std < 0.15 && level_force && calm && slow;
        const bool quiet = gyro_std < 0.05 && accel_std < 0.4 && level_force && slow;
        prev_g_ = g;
        have_prev_ = true;
        block_s_ = 0;
        block_n_ = 0;
        g_sum_ = g_sq_ = a_sum_ = a_sq_ = V{};
        run_ = still ? run_ + 1 : 0;
        const double sensor = gyro_std * gyro_std / n;
        if (run_ >= kStillRunBlocks) {
            update3(M3{}, identity(), sub(g, b_), sensor + c_.still_sigma * c_.still_sigma, c_.zero_rate_chi2);
        } else if (quiet) {
            update3(M3{}, identity(), sub(g, b_), sensor + c_.quiet_sigma * c_.quiet_sigma, c_.zero_rate_chi2);
        }
        if (calibrated_after_ < 0) {
            const bool sure = P_[3][3] < sq(c_.calibrated_sigma) && P_[4][4] < sq(c_.calibrated_sigma) && P_[5][5] < sq(c_.calibrated_sigma);
            if (sure || stream_s_ >= c_.calibrated_timeout_s) calibrated_after_ = stream_s_;
        }
    }
    static M3 identity() { return {V{1, 0, 0}, V{0, 1, 0}, V{0, 0, 1}}; }
    static double sq(double x) { return x * x; }

    PoseOut out(const ImuSample& s) const {
        return {{float(q_.w), float(q_.x), float(q_.y), float(q_.z)}, s.t_ns, s.seq, calibrated_after_ >= 0};
    }

    Config c_;
    Q q_;
    V b_{}, last_gyro_{};
    M6 P_{};
    bool have_last_ = false;
    std::uint64_t last_t_ = 0;
    double stream_s_ = 0, calibrated_after_ = -1;
    double block_s_ = 0;
    int block_n_ = 0, run_ = 0;
    V g_sum_{}, g_sq_{}, a_sum_{}, a_sq_{}, prev_g_{};
    bool have_prev_ = false;
};

}  // namespace luma::eskf
