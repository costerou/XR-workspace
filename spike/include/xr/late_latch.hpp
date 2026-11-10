#pragma once
// Late latch (D4, docs/rust-rebuild-plan.md): a head-tracked frame is drawn as late as the compositor's next latch allows,
// so the pose it reads is that much younger when the frame is shown. Off unless a guard is given (--late-latch-guard-ms).
//
// The compositor sends our frame callback when it takes our last commit for the next refresh (its latch point), so the
// next latch is one refresh period after that callback. Instead of drawing at the callback (about one period of waiting
// in front of the latch), the renderer waits until
//     target = callback + period - margin,   margin = the slowest draw of the last kCostWindow frames + guard + boost
// and then reads the pose and draws. "Draw" is from the target (or the draw start when it was later) to the commit, so a
// late wake-up counts against the margin too. Before any draw is measured the margin is the widest one (three quarters
// of a period), so the first frames start early instead of guessing.
//
// A late frame is worse than a slow one, so the margin adapts and never probes downwards on purpose: a frame drawn on
// this schedule whose own callback comes back half a period or more after the one it aimed for (the callback it
// followed + a period) missed its latch (it is shown one refresh late, and the refresh before it repeats the old frame).
// Each miss adds kMissBoostMs to the margin, up to kMaxBoostMs; the boost only drains (kBoostDecayMsPerS) after
// kBoostQuietS without a miss, so the margin never goes below the slowest recent draw plus the guard.
// The guard is the one tuning value: what the compositor needs between our commit and its latch beyond our own draw
// (its own latch-to-callback delay, the GPU finishing our frame).
//
// Pure: times are CLOCK_MONOTONIC ns passed in, no system calls and no allocation.
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>

namespace xr {
struct LateLatchSummary {
    bool enabled = false;
    double guard_ms = 0, margin_ms = 0, draw_max_ms = 0, boost_ms = 0;
    std::uint64_t scheduled = 0;  // frames drawn on the late-latch schedule (a fresh frame callback before them)
    std::uint64_t waited = 0;     // of those, frames that had time left to wait for
    std::uint64_t latched = 0;    // ... whose callback came back in time: they made the latch they aimed at
    std::uint64_t missed = 0;     // ... whose callback came back half a period or more after the one aimed for
    std::uint64_t immediate = 0;  // frames drawn with no fresh callback (the first, after an idle view): drawn at once
    double slack_p5_ms = 0, slack_p50_ms = 0;  // commit to callback on latched frames: the room left (0.1 ms buckets)
    double draw_p50_ms = 0, draw_p99_ms = 0, draw_worst_ms = 0;  // every scheduled frame's draw (what the margin covers), whole run
};

class LateLatch {
public:
    static constexpr std::size_t kCostWindow = 256;  // about 2 s at 120 Hz
    static constexpr double kMinMarginMs = 0.25, kMaxMarginPeriods = 0.75;
    static constexpr double kMissBoostMs = 1.0, kMaxBoostMs = 4.0, kBoostQuietS = 10.0, kBoostDecayMsPerS = 0.1;
    static constexpr double kMaxGuardMs = 8.0;
    static constexpr double kSlackBucketMs = 0.1;
    static constexpr std::size_t kSlackBuckets = 100;  // 0..10 ms; more lands in the last bucket

    // The guard as given on the command line: finite, 0..kMaxGuardMs. nullopt for anything else.
    static std::optional<double> parse_guard(const char* text);

    LateLatch() = default;  // off
    explicit LateLatch(double guard_ms) : enabled_(true), guard_ms_(guard_ms) {}
    bool enabled() const { return enabled_; }

    // A frame callback arrived at now_ns, the compositor refreshing every period_ns (0: unknown). Scores the frame it
    // belongs to, if that one was drawn on the schedule.
    void frame_done(std::uint64_t now_ns, std::uint64_t period_ns);
    // About to draw a head-tracked frame at now_ns: the time to start it at if that is later than now (wait, then call
    // again), nullopt to draw now. Always nullopt when off, with no period, or with no fresh callback.
    std::optional<std::uint64_t> schedule(std::uint64_t now_ns, std::uint64_t period_ns);
    // The frame started at start_ns (the time schedule() said draw now) was committed at commit_ns.
    void committed(std::uint64_t start_ns, std::uint64_t commit_ns);

    double margin_ms(std::uint64_t period_ns) const;
    LateLatchSummary summary(std::uint64_t period_ns) const;

private:
    void decay(std::uint64_t now_ns);
    bool enabled_ = false;
    double guard_ms_ = 0, boost_ms_ = 0;
    std::uint64_t last_miss_ns_ = 0, last_decay_ns_ = 0;
    std::uint64_t callback_ns_ = 0;  // the newest frame callback, 0 none or used
    std::uint64_t target_ns_ = 0;    // the start time schedule() gave the frame being drawn, 0 none
    bool scheduled_frame_ = false;   // the frame being drawn follows a fresh callback
    std::uint64_t commit_ns_ = 0;    // the last commit, while its callback is due
    bool commit_scheduled_ = false;
    std::uint64_t frame_aim_ns_ = 0;  // the callback the frame being drawn aims for: the one it follows + a period
    std::uint64_t aim_ns_ = 0;        // the same for the last commit
    std::array<double, kCostWindow> costs_ms_{};
    std::size_t cost_next_ = 0, cost_count_ = 0;
    double draw_worst_ms_ = 0;
    std::array<std::uint64_t, kSlackBuckets> slack_{}, draws_{};
    std::uint64_t scheduled_ = 0, waited_ = 0, latched_ = 0, missed_ = 0, immediate_ = 0;
};
}
