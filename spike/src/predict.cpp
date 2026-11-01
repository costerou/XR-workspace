#include "xr/predict.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
constexpr double kPi = 3.14159265358979323846;
bool in(float v, float lo, float hi) { return std::isfinite(v) && v >= lo && v <= hi; }
double percentile(const std::uint64_t* histogram, std::size_t buckets, std::uint64_t count, double q, double bucket_ms) {
    if (!count) return 0;
    const auto rank = std::uint64_t(std::ceil(q * double(count)));
    std::uint64_t seen = 0;
    for (std::size_t i = 0; i < buckets; ++i) {
        seen += histogram[i];
        if (seen >= rank) return (double(i) + 0.5) * bucket_ms;  // bucket centre
    }
    return double(buckets) * bucket_ms;
}
template <std::size_t N> void add(std::array<std::uint64_t, N>& histogram, double ms, double bucket_ms) {
    histogram[std::min<std::size_t>(std::size_t(std::max(ms, 0.0) / bucket_ms), N - 1)]++;
}
}

PoseHistory history_of(const FeedSample& s) {
    return {{s.orientation, s.older[0], s.older[1]}, {s.device_ms, s.older_ms[0], s.older_ms[1]}};
}

std::optional<LookAhead> look_ahead_from(const std::array<float, 4>& r) {
    if (!in(r[0], 0, 50) || !in(r[1], 0, 4) || !in(r[2], 0, 50) || !in(r[3], 1, 100)) return std::nullopt;
    return LookAhead{r[0], r[1], r[2], r[3]};
}

std::optional<std::array<float, 3>> angular_velocity(const PoseHistory& h) {
    const float dt = h.device_ms[0] - h.device_ms[1];
    if (!std::isfinite(dt) || dt < 2.f || dt > 50.f) return std::nullopt;
    const auto q0 = quat_normalized(h.q[0]), q1 = quat_normalized(h.q[1]);
    if (!q0 || !q1) return std::nullopt;
    Quat d = quat_mul(quat_conj(*q1), *q0);  // body frame: q0 = q1 * d
    if (d.w < 0) d = {-d.x, -d.y, -d.z, -d.w};
    const float s = std::sqrt(d.x * d.x + d.y * d.y + d.z * d.z);
    if (s < 1e-9f) return std::array<float, 3>{0, 0, 0};
    const float rate = 2 * std::atan2(s, d.w) / dt / s;  // (angle / dt) / |axis component|
    return std::array<float, 3>{d.x * rate, d.y * rate, d.z * rate};
}

Quat prediction_delta(const std::array<float, 3>& w, float horizon_ms, float max_deg) {
    if (!(horizon_ms > 0)) return {};
    const float speed = std::sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
    if (!std::isfinite(speed) || speed < 1e-12f) return {};
    const float angle = std::min(speed * horizon_ms, float(max_deg * kPi / 180));
    const float s = std::sin(angle / 2) / speed;
    return {w[0] * s, w[1] * s, w[2] * s, std::cos(angle / 2)};
}

void LatencyTracker::read(std::uint64_t ticket, std::uint64_t read_ns) {
    auto slot = std::find_if(pending_.begin(), pending_.end(), [](const Pending& p) { return !p.used; });
    if (slot == pending_.end())  // full: recycle the oldest frame (its feedback never arrived)
        slot = std::min_element(pending_.begin(), pending_.end(), [](const Pending& a, const Pending& b) { return a.ticket < b.ticket; });
    *slot = {ticket, read_ns, true};
}
void LatencyTracker::presented(std::uint64_t ticket, std::uint64_t present_ns) {
    auto slot = std::find_if(pending_.begin(), pending_.end(), [&](const Pending& p) { return p.used && p.ticket == ticket; });
    if (slot == pending_.end()) { ++dropped_; return; }
    const std::uint64_t read_ns = slot->read_ns;
    *slot = {};
    if (!clock_ok_ || present_ns < read_ns) { ++dropped_; return; }
    const double ms = double(present_ns - read_ns) / 1e6;
    add(histogram_, ms, bucket_ms);
    ema_ms_ = count_ ? ema_ms_ + 0.1 * (ms - ema_ms_) : ms;
    ++count_; sum_ms_ += ms; max_ms_ = std::max(max_ms_, ms);
}
void LatencyTracker::discarded(std::uint64_t ticket) {
    auto slot = std::find_if(pending_.begin(), pending_.end(), [&](const Pending& p) { return p.used && p.ticket == ticket; });
    if (slot != pending_.end()) *slot = {};
    ++dropped_;
}
void LatencyTracker::abandon() {
    for (auto& p : pending_) if (p.used) { p = {}; ++dropped_; }
}
std::optional<double> LatencyTracker::expected_ms() const {
    if (count_ < 8) return std::nullopt;
    return ema_ms_;
}
LatencySummary LatencyTracker::summary() const {
    LatencySummary s;
    s.count = count_; s.dropped = dropped_;
    if (count_) {
        s.mean_ms = sum_ms_ / double(count_); s.max_ms = max_ms_;
        s.p50_ms = percentile(histogram_.data(), buckets, count_, 0.50, bucket_ms);
        s.p95_ms = percentile(histogram_.data(), buckets, count_, 0.95, bucket_ms);
    }
    return s;
}

std::optional<double> PoseAge::observe(double host_ms, float device_ms) {
    if (!std::isfinite(device_ms) || !std::isfinite(host_ms)) return std::nullopt;
    if (last_device_ms_ >= 0 && device_ms < last_device_ms_) {  // driver/device restart: new clock epoch
        floor_ms_.reset(); last_device_ms_ = device_ms;
        return std::nullopt;
    }
    last_device_ms_ = device_ms;
    const double offset = host_ms - double(device_ms);
    if (!floor_ms_ || offset < *floor_ms_) floor_ms_ = offset;
    const double age = offset - *floor_ms_;
    add(histogram_, age, 0.25);
    ++count_; sum_ms_ += age; max_ms_ = std::max(max_ms_, age);
    return age;
}
LatencySummary PoseAge::summary() const {
    LatencySummary s;
    s.count = count_;
    if (count_) {
        s.mean_ms = sum_ms_ / double(count_); s.max_ms = max_ms_;
        s.p50_ms = percentile(histogram_.data(), histogram_.size(), count_, 0.50, 0.25);
        s.p95_ms = percentile(histogram_.data(), histogram_.size(), count_, 0.95, 0.25);
    }
    return s;
}

std::optional<float> prediction_horizon(const std::optional<LookAhead>& cfg, std::optional<double> read_to_present_ms,
                                        double pose_age_ms, double frametime_ms) {
    if (!cfg) return std::nullopt;
    const double age = std::clamp(std::isfinite(pose_age_ms) ? pose_age_ms : 0.0, 0.0, double(cfg->cap_ms));
    const double ahead = read_to_present_ms ? age + *read_to_present_ms + cfg->constant_ms
                                            : cfg->constant_ms + std::max(frametime_ms, 0.0) * cfg->frametime_mult;
    return float(std::min(ahead, double(cfg->cap_ms)) + cfg->scanline_ms / 2);
}
}
