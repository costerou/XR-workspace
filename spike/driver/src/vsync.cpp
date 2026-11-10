#include "luma/vsync.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <limits>

namespace luma {
void VsyncClock::Floor::add(std::int64_t value, std::uint64_t host_ns) {
    if (!have_current) { current = value; started = host_ns; have_current = true; return; }
    if (host_ns - started >= kFloorWindowNs) {
        previous = current; have_previous = true;
        current = value; started = host_ns;
        return;
    }
    current = std::min(current, value);
}
std::optional<std::int64_t> VsyncClock::Floor::value() const {
    if (!have_current) return std::nullopt;
    return have_previous ? std::min(current, previous) : current;
}

void VsyncClock::reset() {
    const std::uint64_t events = state_.events;
    const std::uint32_t resets = state_.resets;
    *this = VsyncClock{};
    state_.events = events;
    state_.resets = resets;
}

void VsyncClock::on_imu(std::uint64_t device_ns, std::uint64_t steady_ns, std::uint64_t host_ns) {
    host_minus_device_.add(std::int64_t(host_ns - device_ns), host_ns);
    host_minus_steady_.add(std::int64_t(host_ns - steady_ns), host_ns);
}

void VsyncClock::restart(std::uint64_t device_ns) {
    if (count_ >= 2) ++state_.resets;
    head_ = 0; count_ = 1; frame_ = 0;
    points_[0] = {0, device_ns};
    last_device_ns_ = device_ns;
    guess_ns_ = 0;
    fitted_device_ns_ = double(device_ns);
    state_.locked = false;
    state_.fit_rms_us = 0;
}

bool VsyncClock::on_event(std::uint64_t device_ns) {
    const bool was_locked = state_.locked;
    ++state_.events;
    if (count_ == 0 || device_ns <= last_device_ns_) { restart(device_ns); return was_locked; }
    const double dt = double(device_ns - last_device_ns_);
    if (guess_ns_ == 0) {
        if (dt < kMinPeriodNs || dt > kMaxPeriodNs) { restart(device_ns); return was_locked; }
        guess_ns_ = dt;
    }
    const double n = std::round(dt / guess_ns_);
    if (n < 1 || n > double(kMaxSkipped + 1) || std::abs(dt - n * guess_ns_) > kFitTolerance * guess_ns_) {
        restart(device_ns);
        return was_locked;
    }
    frame_ += std::uint64_t(n);
    last_device_ns_ = device_ns;
    if (count_ < kWindow) points_[(head_ + count_++) % kWindow] = {frame_, device_ns};
    else { points_[head_] = {frame_, device_ns}; head_ = (head_ + 1) % kWindow; }
    fit();
    return was_locked || state_.locked;
}

// Least squares through the window, frame number against device time, both relative to the oldest point so a double keeps
// the stamps' ns.
void VsyncClock::fit() {
    const Point& first = points_[head_];
    double sx = 0, sy = 0;
    for (std::size_t i = 0; i < count_; ++i) {
        const Point& p = points_[(head_ + i) % kWindow];
        sx += double(p.frame - first.frame);
        sy += double(p.device_ns - first.device_ns);
    }
    const double n = double(count_), mx = sx / n, my = sy / n;
    double sxx = 0, sxy = 0;
    for (std::size_t i = 0; i < count_; ++i) {
        const Point& p = points_[(head_ + i) % kWindow];
        const double x = double(p.frame - first.frame) - mx, y = double(p.device_ns - first.device_ns) - my;
        sxx += x * x; sxy += x * y;
    }
    if (!(sxx > 0)) return;
    const double slope = sxy / sxx;
    double sse = 0;
    for (std::size_t i = 0; i < count_; ++i) {
        const Point& p = points_[(head_ + i) % kWindow];
        const double r = double(p.device_ns - first.device_ns) - (my + slope * (double(p.frame - first.frame) - mx));
        sse += r * r;
    }
    const double rms_ns = std::sqrt(sse / n);
    guess_ns_ = slope;
    fitted_device_ns_ = double(first.device_ns) + my + slope * (double(frame_ - first.frame) - mx);
    state_.period_ns = slope;
    state_.fit_rms_us = float(rms_ns / 1e3);
    const auto to_host = host_minus_device_.value();
    const auto to_steady = host_minus_steady_.value();
    state_.locked = count_ >= kLockAfter && rms_ns < 300e3 && to_host && to_steady;
    if (state_.locked) {
        const double host = fitted_device_ns_ + double(*to_host);
        state_.anchor_host_ns = std::uint64_t(std::llround(host));
        state_.anchor_steady_ns = std::uint64_t(std::llround(host - double(*to_steady)));
    }
}

void write_vsync(VsyncWire& w, const VsyncState& s, double anchor_feed_ms) {
    const std::uint32_t lock = w.seq.load(std::memory_order_relaxed);
    w.seq.store(lock | 1u, std::memory_order_relaxed);  // odd, also after a killed writer
    std::atomic_thread_fence(std::memory_order_release);
    const auto period = std::bit_cast<std::uint64_t>(s.period_ns);
    const auto feed = std::bit_cast<std::uint64_t>(anchor_feed_ms);
    const std::array<std::uint32_t, 14> v{
        std::uint32_t(s.anchor_host_ns), std::uint32_t(s.anchor_host_ns >> 32), std::uint32_t(period), std::uint32_t(period >> 32),
        std::uint32_t(feed), std::uint32_t(feed >> 32), std::uint32_t(s.events), std::uint32_t(s.events >> 32),
        s.locked ? 1u : 0u, s.resets, std::bit_cast<std::uint32_t>(s.fit_rms_us), 0, 0, 0};
    for (std::size_t i = 0; i < v.size(); ++i) w.words[i].store(v[i], std::memory_order_relaxed);
    w.magic.store(kVsyncMagic, std::memory_order_relaxed);
    w.seq.store((lock | 1u) + 1u, std::memory_order_release);
}
}
