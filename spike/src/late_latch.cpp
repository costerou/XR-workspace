#include "xr/late_latch.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace xr {
namespace {
constexpr double kNsPerMs = 1e6;
double ms(std::uint64_t ns) { return static_cast<double>(ns) / kNsPerMs; }

double percentile(const std::array<std::uint64_t, LateLatch::kSlackBuckets>& buckets, double p) {
    std::uint64_t total = 0;
    for (auto n : buckets) total += n;
    if (total == 0) return 0;
    const auto want = static_cast<std::uint64_t>(std::ceil(p * static_cast<double>(total)));
    std::uint64_t seen = 0;
    for (std::size_t i = 0; i < buckets.size(); ++i) {
        seen += buckets[i];
        if (seen >= std::max<std::uint64_t>(want, 1)) return (static_cast<double>(i) + 0.5) * LateLatch::kSlackBucketMs;
    }
    return (static_cast<double>(buckets.size()) - 0.5) * LateLatch::kSlackBucketMs;
}
}

std::optional<double> LateLatch::parse_guard(const char* text) {
    if (!text || !*text) return std::nullopt;
    char* end = nullptr;
    const double value = std::strtod(text, &end);
    if (!end || *end || !std::isfinite(value) || value < 0 || value > kMaxGuardMs) return std::nullopt;
    return value;
}

void LateLatch::decay(std::uint64_t now_ns) {
    if (boost_ms_ <= 0) return;
    const auto quiet_from = last_miss_ns_ + static_cast<std::uint64_t>(kBoostQuietS * 1e9);
    if (now_ns <= quiet_from) return;
    const auto from = std::max(quiet_from, last_decay_ns_);
    boost_ms_ = std::max(0.0, boost_ms_ - kBoostDecayMsPerS * static_cast<double>(now_ns - from) / 1e9);
    last_decay_ns_ = now_ns;
}

void LateLatch::frame_done(std::uint64_t now_ns, std::uint64_t period_ns) {
    if (!enabled_) return;
    if (commit_ns_ && commit_scheduled_ && now_ns >= commit_ns_) {
        const auto after = now_ns - commit_ns_;
        if (period_ns && now_ns * 2 >= aim_ns_ * 2 + period_ns) {
            ++missed_;
            boost_ms_ = std::min(kMaxBoostMs, boost_ms_ + kMissBoostMs);
            last_miss_ns_ = now_ns;
            last_decay_ns_ = now_ns;
        } else {
            ++latched_;
            const auto bucket = static_cast<std::size_t>(ms(after) / kSlackBucketMs);
            ++slack_[std::min(bucket, kSlackBuckets - 1)];
        }
    }
    commit_ns_ = 0;
    commit_scheduled_ = false;
    callback_ns_ = now_ns;
    decay(now_ns);
}

std::optional<std::uint64_t> LateLatch::schedule(std::uint64_t now_ns, std::uint64_t period_ns) {
    if (!enabled_) return std::nullopt;
    if (!period_ns || !callback_ns_ || now_ns >= callback_ns_ + period_ns) {
        // No refresh period yet, or no callback to aim from (the first frame, a frame after the view sat idle, a
        // callback whose latch has already gone by): draw now, unscored.
        if (!scheduled_frame_ && !target_ns_) ++immediate_;
        callback_ns_ = 0;
        target_ns_ = 0;
        return std::nullopt;
    }
    const auto margin_ns = static_cast<std::uint64_t>(margin_ms(period_ns) * kNsPerMs);
    const auto target = callback_ns_ + period_ns - std::min(margin_ns, period_ns);
    if (now_ns < target) {
        target_ns_ = target;
        return target;
    }
    ++scheduled_;
    if (target_ns_) ++waited_;
    scheduled_frame_ = true;
    frame_aim_ns_ = callback_ns_ + period_ns;
    callback_ns_ = 0;
    return std::nullopt;
}

void LateLatch::committed(std::uint64_t start_ns, std::uint64_t commit_ns) {
    if (!enabled_) return;
    if (scheduled_frame_) {
        // From the planned start when the frame waited (a late wake-up counts), else from when it really started.
        const auto from = target_ns_ ? std::min(target_ns_, start_ns) : start_ns;
        const double cost = commit_ns > from ? ms(commit_ns - from) : 0;
        costs_ms_[cost_next_] = cost;
        draw_worst_ms_ = std::max(draw_worst_ms_, cost);
        ++draws_[std::min(static_cast<std::size_t>(cost / kSlackBucketMs), kSlackBuckets - 1)];
        cost_next_ = (cost_next_ + 1) % kCostWindow;
        cost_count_ = std::min(cost_count_ + 1, kCostWindow);
    }
    commit_ns_ = commit_ns;
    commit_scheduled_ = scheduled_frame_;
    aim_ns_ = frame_aim_ns_;
    scheduled_frame_ = false;
    target_ns_ = 0;
}

double LateLatch::margin_ms(std::uint64_t period_ns) const {
    // No draw measured yet: aim early (the widest margin) rather than guess, so the first frame does not miss.
    if (cost_count_ == 0 && period_ns) return kMaxMarginPeriods * ms(period_ns);
    double draw_max = 0;
    for (std::size_t i = 0; i < cost_count_; ++i) draw_max = std::max(draw_max, costs_ms_[i]);
    double margin = std::max(kMinMarginMs, draw_max + guard_ms_ + boost_ms_);
    if (period_ns) margin = std::min(margin, kMaxMarginPeriods * ms(period_ns));
    return margin;
}

LateLatchSummary LateLatch::summary(std::uint64_t period_ns) const {
    LateLatchSummary s;
    s.enabled = enabled_;
    if (!enabled_) return s;
    s.guard_ms = guard_ms_;
    s.margin_ms = margin_ms(period_ns);
    for (std::size_t i = 0; i < cost_count_; ++i) s.draw_max_ms = std::max(s.draw_max_ms, costs_ms_[i]);
    s.boost_ms = boost_ms_;
    s.scheduled = scheduled_;
    s.waited = waited_;
    s.latched = latched_;
    s.missed = missed_;
    s.immediate = immediate_;
    s.slack_p5_ms = percentile(slack_, 0.05);
    s.slack_p50_ms = percentile(slack_, 0.50);
    s.draw_p50_ms = percentile(draws_, 0.50);
    s.draw_p99_ms = percentile(draws_, 0.99);
    s.draw_worst_ms = draw_worst_ms_;
    return s;
}
}
