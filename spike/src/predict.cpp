#include "xr/predict.hpp"
#include <algorithm>
#include <cmath>
#include <ctime>

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
// Read-phase model (LatencyTracker): smoothing of the per-phase periods, the error comparison and the period.
constexpr double kPhaseAlpha = 0.1, kErrAlpha = 0.05, kPeriodAlpha = 0.05;
constexpr std::uint32_t kMinCompared = 16;      // presented frames scored before the phase estimate may be used
constexpr double kMinPeriodMs = 2, kMaxPeriodMs = 100;
constexpr std::uint32_t kOffPeriodReset = 32;   // a refresh change: re-learn the grid from the latest interval
}

PoseHistory history_of(const FeedSample& s) {
    return {{s.orientation, s.older[0], s.older[1]}, {s.device_ms, s.older_ms[0], s.older_ms[1]}};
}

std::optional<LookAhead> look_ahead_from(const std::array<float, 4>& r) {
    if (!in(r[0], 0, 50) || !in(r[1], 0, 4) || !in(r[2], 0, 50) || !in(r[3], 1, 100)) return std::nullopt;
    return LookAhead{r[0], r[1], r[2], r[3]};
}

std::optional<std::array<float, 3>> angular_velocity(const PoseHistory& h) {
    float dt = h.device_ms[0] - h.device_ms[1];
    if (!std::isfinite(dt) || dt < 2.f || dt > 50.f) return std::nullopt;
    // Device timestamps are whole ms, truncated (and for the Luma Ultra's Carina path computed as
    // (ts - ts0) * 1000 in double, which lands a ms low about half the time), so t0 - t1 alone is off
    // by up to +-1 ms out of ~16: 15/16/17 ms for the same spacing. q0..q1 and q1..q2 are the same
    // number of IMU cycles apart (the driver's two N-sample buffers), so when the two gaps agree to
    // within that quantisation (two quanta: each gap can be one off either way) their mean is the
    // spacing with half the error. Unchanged when the gaps are equal; a larger mismatch is real.
    const float gap12 = h.device_ms[1] - h.device_ms[2];
    const float quantum = std::max(1.f, std::nextafter(h.device_ms[0], INFINITY) - h.device_ms[0]);  // float ulp past 2^24 ms
    if (std::isfinite(gap12) && gap12 > 0 && std::fabs(dt - gap12) <= 2 * quantum) dt = (h.device_ms[0] - h.device_ms[2]) / 2;
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
    // Both estimates as they stood for this read, scored when the frame is presented.
    const auto phase = by_phase(read_ns);
    *slot = {ticket, read_ns, phase ? float(*phase) : -1.f, count_ ? float(ema_ms_) : -1.f, true};
}
void LatencyTracker::presented(std::uint64_t ticket, std::uint64_t present_ns) {
    auto slot = std::find_if(pending_.begin(), pending_.end(), [&](const Pending& p) { return p.used && p.ticket == ticket; });
    if (slot == pending_.end()) { ++dropped_; return; }
    const Pending p = *slot;
    *slot = {};
    if (!clock_ok_ || present_ns < p.read_ns) { ++dropped_; return; }
    const double ms = double(present_ns - p.read_ns) / 1e6;
    add(histogram_, ms, bucket_ms);
    ema_ms_ = count_ ? ema_ms_ + 0.1 * (ms - ema_ms_) : ms;
    ++count_; sum_ms_ += ms; max_ms_ = std::max(max_ms_, ms);
    if (p.by_phase_ms >= 0 && p.by_ema_ms >= 0) {  // score both estimates; switch with hysteresis
        phase_err_ms_ += kErrAlpha * (std::fabs(ms - double(p.by_phase_ms)) - phase_err_ms_);
        ema_err_ms_ += kErrAlpha * (std::fabs(ms - double(p.by_ema_ms)) - ema_err_ms_);
        if (compared_ < UINT32_MAX) ++compared_;
        use_phase_ = compared_ >= kMinCompared && (use_phase_ ? phase_err_ms_ <= ema_err_ms_ : phase_err_ms_ < 0.8 * ema_err_ms_);
    }
    learn_grid(present_ns);
    // Whole periods from the grid point before the read, binned by the read's phase on this grid. A
    // stall (presented more than two periods later than the average said) is not a property of the
    // phase: the average absorbs it, the bins do not learn it.
    const bool stall = p.by_ema_ms >= 0 && ms > double(p.by_ema_ms) + 2 * period_ms_;
    if (period_ms_ > 0 && !stall) {
        const double k = std::ceil(ms / period_ms_);
        const double phase = k * period_ms_ - ms;
        const auto bin = std::min(phase_bins - 1, std::size_t(std::max(0.0, phase / period_ms_ * double(phase_bins))));
        periods_[bin] = phase_count_[bin] ? periods_[bin] + float(kPhaseAlpha * (k - double(periods_[bin]))) : float(k);
        if (phase_count_[bin] < UINT32_MAX) ++phase_count_[bin];
    }
}
void LatencyTracker::learn_grid(std::uint64_t present_ns) {
    if (grid_ns_ && present_ns <= grid_ns_) return;  // late or repeated feedback: keep the newest point
    if (grid_ns_) {
        const double d = double(present_ns - grid_ns_) / 1e6;
        const bool usable = d >= kMinPeriodMs && d <= kMaxPeriodMs;
        if (usable && (!(period_ms_ > 0) || d < 0.75 * period_ms_ || off_period_ >= kOffPeriodReset)) {
            period_ms_ = d; off_period_ = 0;  // first interval or a new refresh rate: earlier phases no longer apply
            periods_ = {}; phase_count_ = {}; phase_err_ms_ = ema_err_ms_ = 0; compared_ = 0; use_phase_ = false;
        } else if (usable && d < 1.25 * period_ms_) {
            period_ms_ += kPeriodAlpha * (d - period_ms_); off_period_ = 0;
        } else if (off_period_ < UINT32_MAX) ++off_period_;  // skipped refreshes, a pause, or no fixed grid
    }
    grid_ns_ = present_ns;
}
std::optional<double> LatencyTracker::by_phase(std::uint64_t now_ns) const {
    if (!(period_ms_ > 0) || !grid_ns_ || now_ns < grid_ns_) return std::nullopt;
    const double since = double(now_ns - grid_ns_) / 1e6;
    if (since > 1000) return std::nullopt;  // the grid is too old to extend
    const double phase = std::fmod(since, period_ms_);
    const long bins = long(phase_bins);
    const long bin = std::min(bins - 1, long(phase / period_ms_ * double(bins)));
    for (long d = 0; d <= bins / 2; ++d)  // nearest phase seen at least twice
        for (const long j : {bin - d, bin + d}) {
            const auto w = std::size_t((j % bins + bins) % bins);
            if (phase_count_[w] < 2) continue;
            // A neighbour across the wrap read on the other side of a grid point: shift this phase by a period.
            const double p = j < 0 ? phase + period_ms_ : j >= bins ? phase - period_ms_ : phase;
            return std::max(0.0, double(periods_[w]) * period_ms_ - p);
        }
    return std::nullopt;
}
void LatencyTracker::discarded(std::uint64_t ticket) {
    auto slot = std::find_if(pending_.begin(), pending_.end(), [&](const Pending& p) { return p.used && p.ticket == ticket; });
    if (slot != pending_.end()) *slot = {};
    ++dropped_;
}
void LatencyTracker::abandon() {
    for (auto& p : pending_) if (p.used) { p = {}; ++dropped_; }
}
std::optional<double> LatencyTracker::expected_ms(std::uint64_t now_ns) const {
    if (count_ < 8) return std::nullopt;
    if (use_phase_)
        if (const auto p = by_phase(now_ns)) return *p;
    return ema_ms_;
}
std::optional<double> LatencyTracker::expected_ms() const {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return expected_ms(std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec));
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
        window_s_.fill(kNoWindow); last_device_ms_ = device_ms;
        return std::nullopt;
    }
    last_device_ms_ = device_ms;
    const double offset = host_ms - double(device_ms);
    // Floor: the smallest offset over the last floor_windows seconds of DEVICE time (one minimum per
    // second). Device time, so a frozen feed (same sample read again and again) keeps its floor and its
    // age keeps growing, as with a global minimum, while drift against the host only spans the window.
    const auto second = std::int64_t(std::floor(double(device_ms) / 1000));
    const auto slot = std::size_t(((second % std::int64_t(floor_windows)) + std::int64_t(floor_windows)) % std::int64_t(floor_windows));
    if (window_s_[slot] != second) { window_s_[slot] = second; window_min_[slot] = offset; }
    else window_min_[slot] = std::min(window_min_[slot], offset);
    double floor = offset;
    for (std::size_t i = 0; i < floor_windows; ++i)
        if (window_s_[i] != kNoWindow && window_s_[i] <= second && second - window_s_[i] < std::int64_t(floor_windows))
            floor = std::min(floor, window_min_[i]);
    const double age = offset - floor;
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
