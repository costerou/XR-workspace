#include "xr/metrics.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <nlohmann/json.hpp>
#include <time.h>

namespace xr {
namespace {
void increment(std::uint64_t& n) { if (n != UINT64_MAX) ++n; }
}
void Metrics::bind(std::uint32_t advertised) { state_.protocol_version = std::min(advertised, 1U); }
void Metrics::clock(std::uint32_t id) {
    if (state_.clock_id && *state_.clock_id != id) {
        clock_conflict_ = true; previous_ns_.reset(); previous_sequence_.reset();
    }
    state_.clock_id = id;
}
bool Metrics::can_submit() const {
    return state_.submitted != UINT64_MAX && (!state_.protocol_version || state_.pending < capacity);
}
std::optional<std::uint64_t> Metrics::submit() {
    if (!can_submit()) return {};
    ++state_.submitted;
    if (!state_.protocol_version) return {};
    *std::find(pending_.begin(), pending_.end(), 0) = state_.submitted;
    ++state_.pending;
    return state_.submitted;
}
bool Metrics::finish(std::uint64_t ticket) {
    if (!ticket) return false;
    auto slot = std::find(pending_.begin(), pending_.end(), ticket);
    if (slot == pending_.end()) return false;
    *slot = 0; --state_.pending; return true;
}
void Metrics::presented(std::uint64_t ticket, std::uint32_t hi, std::uint32_t lo,
                        std::uint32_t ns, std::uint32_t refresh, std::uint32_t seq_hi,
                        std::uint32_t seq_lo, std::uint32_t flags, bool selected_output) {
    if (!finish(ticket)) return;
    increment(state_.presented);
    for (unsigned i=0; i<4; ++i) if (flags & (1U << i)) increment(state_.flags[i]);
    state_.last_refresh_prediction_ns = refresh;
    const auto seconds = (std::uint64_t(hi) << 32) | lo;
    const auto sequence = (std::uint64_t(seq_hi) << 32) | seq_lo;
    bool valid = selected_output && !clock_conflict_ && state_.clock_id == CLOCK_MONOTONIC &&
                 ns < 1000000000U && seconds <= (std::uint64_t(INT64_MAX) - ns) / 1000000000U && sequence != 0;
    const auto timestamp = valid ? seconds * 1000000000U + ns : 0;
    valid = valid && (!previous_ns_ || timestamp > *previous_ns_) &&
                    (!previous_sequence_ || sequence > *previous_sequence_);
    if (!valid) { increment(state_.invalid_timing); interval_anchor_ = false; return; }
    increment(state_.valid_presented);
    if (!first_valid_ns_) first_valid_ns_ = timestamp;
    last_valid_ns_ = timestamp;
    remember(timestamp);
    if (previous_ns_ && interval_anchor_) {
        constexpr std::array<std::uint64_t, 7> bounds{1000000, 4000000, 8000000, 9000000, 17000000, 34000000, 100000000};
        auto delta = timestamp - *previous_ns_;
        auto index = std::lower_bound(bounds.begin(), bounds.end(), delta) - bounds.begin();
        increment(state_.intervals[index]);
        increment(state_.interval_count);
        // Disjoint increasing timestamp intervals sum to at most INT64_MAX.
        state_.interval_sum_ns += delta;
        state_.interval_min_ns = std::min(state_.interval_min_ns.value_or(delta), delta);
        state_.interval_max_ns = std::max(state_.interval_max_ns.value_or(delta), delta);
    }
    previous_ns_ = timestamp; previous_sequence_ = sequence; interval_anchor_ = true;
}
void Metrics::remember(std::uint64_t timestamp) {
    constexpr std::size_t mask = recent_capacity - 1;
    if (recent_size_ == recent_capacity) { recent_head_ = (recent_head_ + 1) & mask; --recent_size_; }  // full: drop the oldest
    recent_[(recent_head_ + recent_size_) & mask] = timestamp; ++recent_size_;
    while (timestamp - recent_[recent_head_] > 1000000000ULL) { recent_head_ = (recent_head_ + 1) & mask; --recent_size_; }
}
void Metrics::discarded(std::uint64_t ticket) { if (finish(ticket)) increment(state_.discarded); }
void Metrics::abandon() {
    for (auto& ticket : pending_) if (ticket) { ticket = 0; increment(state_.abandoned); }
    state_.pending = 0;
}
std::string Metrics::status_json(double now_ms) const {
    auto j = nlohmann::json::object();
    if (state_.presented || state_.discarded) j["missed"] = state_.discarded;
    if (!clock_conflict_ && std::isfinite(now_ms) && now_ms >= 0) {
        std::uint64_t first = 0, last = 0;
        std::size_t count = 0;
        for (std::size_t i = 0; i < recent_size_; ++i) {
            const auto ns = recent_[(recent_head_ + i) & (recent_capacity - 1)];
            const double ms = double(ns) / 1e6;
            if (ms <= now_ms && ms >= now_ms - 1000) {
                if (!count) first = ns;
                last = ns; ++count;
            }
        }
        if (count >= 2) j["presented_hz"] = double(count - 1) * 1e9 / double(last - first);
    }
    return j.dump();
}
std::string Metrics::report() const {
    nlohmann::json j{{"schema", 1}, {"kind", "generated_pattern_presentation_timing"},
        {"protocol_version", state_.protocol_version}, {"clock_id", nullptr},
        {"clock_conflict", clock_conflict_}, {"submitted", state_.submitted}, {"presented", state_.presented},
        {"discarded", state_.discarded}, {"abandoned", state_.abandoned}, {"pending", state_.pending},
        {"invalid_timing", state_.invalid_timing}, {"interval_histogram", state_.intervals},
        {"interval_upper_bounds_ns", {1000000,4000000,8000000,9000000,17000000,34000000,100000000,nullptr}},
        {"flag_counts_vsync_hw_clock_hw_completion_zero_copy", state_.flags},
        {"last_refresh_prediction_ns", state_.last_refresh_prediction_ns},
        {"sustained_cadence_qualified", false}};
    j["valid_presented"] = state_.valid_presented;
    j["interval_count"] = state_.interval_count;
    j["interval_sum_ns"] = state_.interval_sum_ns;
    j["interval_min_ns"] = nullptr; j["interval_max_ns"] = nullptr; j["interval_mean_ns"] = nullptr;
    j["observation_duration_ns"] = nullptr;
    if (state_.interval_count) {
        j["interval_min_ns"] = *state_.interval_min_ns;
        j["interval_max_ns"] = *state_.interval_max_ns;
        j["interval_mean_ns"] = double(state_.interval_sum_ns) / double(state_.interval_count);
    }
    // First-to-last accepted presentation timestamps, not requested run length,
    // callback arrival time, refresh prediction, or an FPS denominator.
    if (state_.valid_presented >= 2) j["observation_duration_ns"] = *last_valid_ns_ - *first_valid_ns_;
    if (state_.clock_id) j["clock_id"] = *state_.clock_id;
    return j.dump();
}
}
