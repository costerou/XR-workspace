#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>

namespace xr {
struct TimingSnapshot {
    std::uint32_t protocol_version = 0;
    std::optional<std::uint32_t> clock_id;
    std::uint64_t submitted = 0, presented = 0, discarded = 0, abandoned = 0;
    std::uint64_t pending = 0, invalid_timing = 0;
    std::array<std::uint64_t, 8> intervals{};
    std::array<std::uint64_t, 4> flags{};
    std::uint32_t last_refresh_prediction_ns = 0;
};
// No event log, host receipt timestamps, or implicit clock-domain conversion.
class Metrics {
public:
    static constexpr std::size_t capacity = 32;
    void bind(std::uint32_t advertised);
    void clock(std::uint32_t id);
    bool can_submit() const;
    // Call only immediately before the associated content commit. Missing
    // protocol counts submission but returns no feedback ticket; full => no commit.
    std::optional<std::uint64_t> submit();
    void presented(std::uint64_t ticket, std::uint32_t sec_hi, std::uint32_t sec_lo,
                   std::uint32_t ns, std::uint32_t refresh, std::uint32_t seq_hi,
                   std::uint32_t seq_lo, std::uint32_t flags, bool selected_output);
    void discarded(std::uint64_t ticket);
    void abandon();
    TimingSnapshot snapshot() const { return state_; }
    std::string report() const;
private:
    bool finish(std::uint64_t ticket);
    TimingSnapshot state_;
    std::array<std::uint64_t, capacity> pending_{};
    std::optional<std::uint64_t> previous_ns_, previous_sequence_;
    bool clock_conflict_ = false, interval_anchor_ = false;
};
}
