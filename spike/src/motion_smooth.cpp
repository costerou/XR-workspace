#include "xr/motion_smooth.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
constexpr float kPi = 3.14159265358979f;
constexpr float kRadToDeg = 180.f / kPi;
using Vec = std::array<float, 3>;
Vec log_of(Quat q) {  // rotation vector, radians
    if (q.w < 0) q = {-q.x, -q.y, -q.z, -q.w};
    const float s = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z);
    if (s < 1e-9f) return {2 * q.x, 2 * q.y, 2 * q.z};
    const float k = 2 * std::atan2(s, q.w) / s;
    return {q.x * k, q.y * k, q.z * k};
}
Quat exp_of(Vec v) {
    const float a = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    if (a < 1e-9f) return quat_normalized({v[0] / 2, v[1] / 2, v[2] / 2, 1}).value_or(Quat{});
    const float s = std::sin(a / 2) / a;
    return {v[0] * s, v[1] * s, v[2] * s, std::cos(a / 2)};
}
float norm(Vec v) { return std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }
Quat unit(Quat q) { return quat_normalized(q).value_or(Quat{}); }
}

std::optional<MotionSmoothParams> motion_smooth_params(float tau_ms) {
    if (!std::isfinite(tau_ms) || tau_ms < 5.f || tau_ms > 80.f) return std::nullopt;
    MotionSmoothParams p;
    p.tau_ms = tau_ms;
    return p;
}

void MotionSmoother::learn(Quat relative, float device_ms) {
    if (count_ && device_ms == t_[0]) return;
    // A new clock epoch, or a jump no head makes between samples (a recentre, a reanchor): nothing before it is comparable.
    const bool broken = count_ && (!(device_ms > t_[0]) || norm(log_of(quat_mul(quat_conj(q_[0]), relative))) * kRadToDeg > p_.snap_deg);
    if (broken) count_ = 0;
    q_[2] = q_[1]; t_[2] = t_[1];
    q_[1] = q_[0]; t_[1] = t_[0];
    q_[0] = relative; t_[0] = device_ms;
    if (count_ < 3) ++count_;
}

Vec MotionSmoother::velocity() const {
    if (count_ < 2) return {0, 0, 0};
    const std::size_t oldest = count_ >= 3 ? 2 : 1;
    const float dt = t_[0] - t_[oldest];
    if (!std::isfinite(dt) || dt <= 0) return {0, 0, 0};
    for (std::size_t i = 0; i < oldest; ++i)
        if (t_[i] - t_[i + 1] > p_.max_gap_ms) return {0, 0, 0};
    Vec v = log_of(quat_mul(quat_conj(q_[oldest]), q_[0]));
    for (auto& c : v) c /= dt;
    return v;
}

Quat MotionSmoother::update(Quat relative, float device_ms, float horizon_ms, double now_ms) {
    relative = unit(relative);
    if (!std::isfinite(device_ms) || !std::isfinite(now_ms)) { reset(); return relative; }
    learn(relative, device_ms);
    Vec v = velocity();
    // The newest interval against the window: 1 at a steady speed, towards 0 as the head stops, 0 when it turns back.
    float keep = 1;
    if (count_ == 3 && t_[0] > t_[1]) {
        Vec newest = log_of(quat_mul(quat_conj(q_[1]), q_[0]));
        for (auto& c : newest) c /= t_[0] - t_[1];
        const float e2 = p_.brake_floor_rad_per_ms * p_.brake_floor_rad_per_ms;
        const float share = (newest[0] * v[0] + newest[1] * v[1] + newest[2] * v[2] + e2) / (v[0] * v[0] + v[1] * v[1] + v[2] * v[2] + e2);
        keep = std::pow(std::clamp(share, 0.f, 1.f), p_.brake_power);
    }
    for (auto& c : v) c *= keep;
    float horizon = std::isfinite(horizon_ms) ? std::max(horizon_ms, 0.f) : 0.f;
    Vec ahead{v[0] * horizon, v[1] * horizon, v[2] * horizon};
    const float angle = norm(ahead), limit = p_.max_predict_deg / kRadToDeg;
    if (angle > limit) for (auto& c : ahead) c *= limit / angle;
    const Quat target = unit(quat_mul(q_[0], exp_of(ahead)));
    last_horizon_ms_ = horizon;
    last_predicted_deg_ = std::min(angle, limit) * kRadToDeg;
    max_predicted_deg_ = std::max(max_predicted_deg_, last_predicted_deg_);
    if (!valid_) {
        valid_ = true; output_ = target; last_ms_ = now_ms; last_correction_deg_ = 0;
        return output_;
    }
    const float dt = float(std::clamp(now_ms - last_ms_, 0.0, 100.0));
    last_ms_ = now_ms;
    const Quat carried = unit(quat_mul(output_, exp_of({v[0] * dt, v[1] * dt, v[2] * dt})));
    const Vec error = log_of(quat_mul(quat_conj(carried), target));
    const float size = norm(error);
    last_correction_deg_ = size * kRadToDeg;
    if (size * kRadToDeg > p_.snap_deg) {
        output_ = target; ++snaps_;
        return output_;
    }
    const float k = 1 - std::exp(-dt / p_.tau_ms);
    output_ = unit(quat_mul(carried, exp_of({error[0] * k, error[1] * k, error[2] * k})));
    return output_;
}
}
