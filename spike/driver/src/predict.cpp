#include "luma/predict.hpp"
#include <algorithm>
#include <bit>
#include <cmath>

namespace luma {
namespace {
constexpr float kPi = 3.14159265358979f, kDegToRad = kPi / 180;
// Sample clock: how fast the smoothed grid follows the stamps. Whole-ms stamps and USB arrival jitter
// are a ms or two of noise; the crystal's drift is parts per million.
constexpr double kClockPhase = 0.02, kClockRate = 0.0001;  // critically damped
constexpr double kClockRange = 8;  // the learned period stays this close to nominal, whatever the stamps do

float dot(const Vec3f& a, const Vec3f& b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
float length(const Vec3f& v) { return std::sqrt(dot(v, v)); }
bool finite(const Vec3f& v) { return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]); }
bool finite(const Quatf& q) { return std::isfinite(q.w) && std::isfinite(q.x) && std::isfinite(q.y) && std::isfinite(q.z); }
Quatf mul(const Quatf& a, const Quatf& b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
Quatf exp_map(const Vec3f& r) {
    const float a = length(r);
    const float k = a < 1e-6f ? 0.5f : std::sin(a / 2) / a;
    return {std::cos(a / 2), r[0] * k, r[1] * k, r[2] * k};
}
Quatf normalized(const Quatf& q) {
    const float n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    return n > 1e-6f ? Quatf{q.w / n, q.x / n, q.y / n, q.z / n} : Quatf{};
}
float smoothstep(float lo, float hi, float v) {
    if (!(hi > lo)) return v >= hi ? 1.f : 0.f;
    const float u = std::clamp((v - lo) / (hi - lo), 0.f, 1.f);
    return u * u * (3 - 2 * u);
}
float within(float v, float fallback, float lo, float hi) { return std::isfinite(v) ? std::clamp(v, lo, hi) : fallback; }
// One step of a critically damped alpha-beta filter of memory tau: value and slope.
void track(float z, float dt_s, float tau_s, float& value, float& slope) {
    const float theta = std::exp(-dt_s / tau_s);
    const float predicted = value + slope * dt_s, residual = z - predicted;
    value = predicted + (1 - theta * theta) * residual;
    slope += (1 - theta) * (1 - theta) / dt_s * residual;
}
// The rotation vector over the next horizon_s, before the gate and the limit.
Vec3f path(const MotionState& s, float horizon_s, const PredictConfig& cfg) {
    float t = horizon_s;
    if (cfg.use_acceleration && cfg.stop_clamp) {
        const float speed = length(s.omega_rad_s);
        const float along = speed > 0 ? dot(s.omega_rad_s, s.alpha_rad_s2) / speed : 0;
        if (along < 0) t = std::min(t, -speed / along);  // braking: the head stops, it does not reverse
    }
    const float a = cfg.use_acceleration ? 0.5f * t * t : 0;
    Vec3f r;
    for (std::size_t i = 0; i < 3; ++i) r[i] = s.omega_rad_s[i] * t + s.alpha_rad_s2[i] * a;
    return r;
}
Vec3f gated_path(const MotionState& s, float horizon_ms, const PredictConfig& cfg, bool* clamped) {
    Vec3f r = path(s, horizon_ms / 1000, cfg);
    if (cfg.still_gate) {
        const float gain = smoothstep(cfg.still_lo_dps * kDegToRad, cfg.still_hi_dps * kDegToRad, length(s.omega_rad_s));
        for (float& v : r) v *= gain;
    }
    const float angle = length(r), limit = cfg.max_deg * kDegToRad;
    if (angle > limit) {
        for (float& v : r) v *= limit / angle;
        if (clamped) *clamped = true;
    }
    return r;
}
}

std::array<float, 4> look_ahead_cfg(const LookAhead& c) {
    const LookAhead d;
    const bool over = std::isfinite(c.override_ms) && c.override_ms != 0;
    return {over ? std::clamp(c.override_ms, 0.f, 50.f) : within(c.constant_ms, d.constant_ms, 0, 50),
            over ? 0.f : within(c.frametime_mult, d.frametime_mult, 0, 4),
            within(c.scanline_ms, d.scanline_ms, 0, 50), within(c.cap_ms, d.cap_ms, 1, 100)};
}

Predicted extrapolate(const MotionState& s, float horizon_ms, const PredictConfig& cfg) {
    Predicted out;
    out.q = s.q;
    out.horizon_ms = horizon_ms;
    out.target_ns = s.host_ns + (horizon_ms > 0 ? std::uint64_t(double(horizon_ms) * 1e6) : 0);
    if (!cfg.enabled || !s.valid || !s.calibrated || !(horizon_ms > 0)) return out;
    const Vec3f r = gated_path(s, horizon_ms, cfg, &out.clamped);
    out.q = normalized(mul(s.q, exp_map(r)));
    out.predicted_deg = length(r) / kDegToRad;
    return out;
}

std::uint64_t next_vsync_ns(std::uint64_t after_ns, std::uint64_t anchor_ns, std::uint64_t period_ns) {
    if (!period_ns) return 0;
    if (after_ns < anchor_ns) {
        const std::uint64_t ahead = (anchor_ns - after_ns) % period_ns;
        return after_ns + (ahead ? ahead : period_ns);
    }
    return after_ns + period_ns - (after_ns - anchor_ns) % period_ns;
}

Predictor::Predictor(const PredictConfig& cfg) : cfg_(cfg), look_ahead_(look_ahead_cfg(cfg.look_ahead)) {
    nominal_ns_ = period_ns_imu_ = 1e9 / double(cfg.nominal_imu_hz > 0 ? cfg.nominal_imu_hz : 1000.f);
}

float Predictor::horizon_ms(std::uint64_t now_ns) const {
    const auto [constant, mult, scanline, cap] = look_ahead_;
    const float frame_ms = 1000 / (cfg_.refresh_hz > 0 ? cfg_.refresh_hz : 120.f);
    const float ahead = period_ns_ ? float(next_vsync_ns(now_ns, anchor_ns_, period_ns_) - now_ns) / 1e6f + constant
                                   : constant + frame_ms * mult;
    return std::min(ahead, cap) + scanline / 2;
}

void Predictor::restart(const Vec3f& gyro) {
    for (std::size_t i = 0; i < 3; ++i) axes_[i] = {gyro[i], 0, gyro[i], 0, gyro[i], 0};
    spike_pending_ = false;
}

void Predictor::update(const PoseOut& pose, const ImuSample& imu, std::uint64_t host_ns) {
    if (started_ && imu.seq == last_seq_ && imu.t_ns == last_raw_ns_) { ++stats_.rejected; return; }  // the same sample again
    if (!finite(pose.q)) { ++stats_.rejected; return; }
    state_.q = pose.q;
    state_.host_ns = host_ns;
    state_.seq = imu.seq;
    state_.calibrated = pose.calibrated;
    state_.valid = true;
    if (!finite(imu.gyro_rad_s)) { ++stats_.rejected; return; }
    last_seq_ = imu.seq;
    last_raw_ns_ = imu.t_ns;
    const Vec3f z{imu.gyro_rad_s[0] - bias_[0], imu.gyro_rad_s[1] - bias_[1], imu.gyro_rad_s[2] - bias_[2]};
    if (!started_) {
        started_ = true;
        clock_ns_ = double(imu.t_ns);
        restart(z);
    } else {
        const double since = double(imu.t_ns) - clock_ns_, gap = double(cfg_.gap_ms) * 1e6;
        if (since > gap || since < -gap) {  // a drop, or a new clock after a reconnect
            clock_ns_ = double(imu.t_ns);
            ++stats_.resets;
            restart(z);
        } else {
            // Whole periods since the last sample, at least one: a stamp may repeat or arrive in a burst.
            const auto n = std::max<long long>(1, std::llround(since / period_ns_imu_));
            clock_ns_ += double(n) * period_ns_imu_;
            const double error = double(imu.t_ns) - clock_ns_;
            clock_ns_ += kClockPhase * error;
            period_ns_imu_ = std::clamp(period_ns_imu_ + kClockRate * error, nominal_ns_ / kClockRange, nominal_ns_ * kClockRange);
            const auto dt_s = float(double(n) * period_ns_imu_ * 1e-9);
            bool spike = false;
            if (cfg_.reject_spikes) {
                const Vec3f jump{z[0] - axes_[0].fast, z[1] - axes_[1].fast, z[2] - axes_[2].fast};
                spike = length(jump) > cfg_.spike_rad_s;
            }
            if (spike && !spike_pending_) {  // one reading: a glitch until a second agrees
                spike_pending_ = true;
                ++stats_.rejected;
            } else if (spike) {
                restart(z);
            } else {
                spike_pending_ = false;
                const float tau_w = cfg_.omega_tau_ms / 1000, tau_a = cfg_.alpha_tau_ms / 1000, tau_b = cfg_.brake_tau_ms / 1000;
                for (std::size_t i = 0; i < 3; ++i) {
                    track(z[i], dt_s, tau_w, axes_[i].fast, axes_[i].fast_slope);
                    track(z[i], dt_s, tau_a, axes_[i].slow, axes_[i].alpha);
                    track(z[i], dt_s, tau_b, axes_[i].quick, axes_[i].brake);
                }
            }
        }
    }
    for (std::size_t i = 0; i < 3; ++i) {
        const Axis& a = axes_[i];
        // Slowing down is read from the quick filter, the moment it says so more firmly: a late stop shows
        // as overshoot, while a late start only shows as the lag every prediction has.
        const bool braking = a.fast * a.brake < 0 && std::fabs(a.brake) > std::fabs(a.alpha);
        state_.omega_rad_s[i] = a.fast;
        state_.alpha_rad_s2[i] = cfg_.stop_clamp && braking ? a.brake : a.alpha;
    }
}

FeedHistory synthesize_history(const MotionState& s, float device_ms, float horizon_ms, const PredictConfig& cfg, float spacing_ms) {
    FeedHistory h;
    h.q = {s.q, s.q, s.q};
    h.device_ms = {device_ms, device_ms - spacing_ms, device_ms - 2 * spacing_ms};
    if (!cfg.enabled || !s.valid || !s.calibrated || !(horizon_ms > 0) || !(spacing_ms > 0)) return h;
    const Vec3f r = gated_path(s, horizon_ms, cfg, nullptr);
    const float k = spacing_ms / horizon_ms;  // the velocity that covers r over the horizon, walked backwards
    h.q[1] = normalized(mul(s.q, exp_map({-r[0] * k, -r[1] * k, -r[2] * k})));
    h.q[2] = normalized(mul(s.q, exp_map({-2 * r[0] * k, -2 * r[1] * k, -2 * r[2] * k})));
    return h;
}

void write_prediction(PredictionWire& w, const MotionState& s) {
    const std::uint32_t lock = w.seq.load(std::memory_order_relaxed);
    w.seq.store(lock | 1u, std::memory_order_relaxed);  // odd, also after a killed writer
    std::atomic_thread_fence(std::memory_order_release);
    const std::uint32_t flags = (s.valid ? 1u : 0u) | (s.calibrated ? 2u : 0u);
    const std::array<std::uint32_t, 14> v{
        std::uint32_t(s.host_ns), std::uint32_t(s.host_ns >> 32), s.seq,
        std::bit_cast<std::uint32_t>(s.q.x), std::bit_cast<std::uint32_t>(s.q.y), std::bit_cast<std::uint32_t>(s.q.z), std::bit_cast<std::uint32_t>(s.q.w),
        std::bit_cast<std::uint32_t>(s.omega_rad_s[0]), std::bit_cast<std::uint32_t>(s.omega_rad_s[1]), std::bit_cast<std::uint32_t>(s.omega_rad_s[2]),
        std::bit_cast<std::uint32_t>(s.alpha_rad_s2[0]), std::bit_cast<std::uint32_t>(s.alpha_rad_s2[1]), std::bit_cast<std::uint32_t>(s.alpha_rad_s2[2]),
        flags};
    for (std::size_t i = 0; i < v.size(); ++i) w.words[i].store(v[i], std::memory_order_relaxed);
    w.magic.store(kPredictionMagic, std::memory_order_relaxed);
    w.seq.store((lock | 1u) + 1u, std::memory_order_release);
}

std::optional<MotionState> read_prediction(const PredictionWire& w) {
    for (int attempt = 0; attempt < 4; ++attempt) {  // bounded: a frame must never spin on a stopped writer
        const std::uint32_t before = w.seq.load(std::memory_order_acquire);
        if (before & 1u) continue;
        std::array<std::uint32_t, 14> v;
        for (std::size_t i = 0; i < v.size(); ++i) v[i] = w.words[i].load(std::memory_order_relaxed);
        const std::uint32_t magic = w.magic.load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (w.seq.load(std::memory_order_relaxed) != before) continue;
        if (magic != kPredictionMagic || !(v[13] & 1u)) return std::nullopt;
        MotionState s;
        s.host_ns = std::uint64_t(v[0]) | std::uint64_t(v[1]) << 32;
        s.seq = v[2];
        s.q = {std::bit_cast<float>(v[6]), std::bit_cast<float>(v[3]), std::bit_cast<float>(v[4]), std::bit_cast<float>(v[5])};
        for (std::size_t i = 0; i < 3; ++i) {
            s.omega_rad_s[i] = std::bit_cast<float>(v[7 + i]);
            s.alpha_rad_s2[i] = std::bit_cast<float>(v[10 + i]);
        }
        s.valid = true;
        s.calibrated = v[13] & 2u;
        return s;
    }
    return std::nullopt;
}
}
