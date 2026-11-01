#pragma once
// Software latency measurement and bounded head-pose prediction.
//
// Latency here is SOFTWARE timing only: from the moment the renderer reads the pose to the
// compositor's wp_presentation "presented" timestamp (same CLOCK_MONOTONIC). The glasses' own
// panel/scanout delay is not visible to software; it is covered by the driver's per-device
// look-ahead constant, not measured. Never report any of this as motion-to-photon latency.
//
// Prediction extrapolates the driver's newest orientation by the angular velocity between its
// two newest timestamped samples (~16 ms apart), over a bounded horizon:
//   horizon = pose age + measured read->present + driver constant, capped by the driver's cap,
//   plus half the driver's scanline adjustment (we render one pose per frame; screen centre).
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <optional>
#include <string>

namespace xr {
// Newest first: XRLinuxDriver's three quaternions and their device timestamps (ms).
struct PoseHistory { std::array<Quat, 3> q{}; std::array<float, 3> device_ms{}; };
PoseHistory history_of(const FeedSample& s);
// XRLinuxDriver look_ahead_cfg: {constant ms, frametime multiplier, scanline adjust ms, cap ms}.
struct LookAhead { float constant_ms = 0, frametime_mult = 0, scanline_ms = 0, cap_ms = 0; };
// nullopt unless all four are finite and in sane ranges (constant/scanline 0..50, mult 0..4, cap 1..100).
std::optional<LookAhead> look_ahead_from(const std::array<float, 4>& raw);

// Body-frame rotation per ms between the two newest samples (axis * rad/ms), or nullopt when the
// samples are unusable: non-increasing or implausible spacing (outside 2..50 ms), non-finite.
std::optional<std::array<float, 3>> angular_velocity(const PoseHistory& h);
// Body-frame delta to apply on the right of the newest orientation: newest * delta = predicted.
// Rotation angle is clamped to max_deg. Identity for horizon <= 0.
Quat prediction_delta(const std::array<float, 3>& omega_rad_per_ms, float horizon_ms, float max_deg);

// Per-frame software latency record keyed by the presentation-feedback ticket.
struct LatencySummary {
    std::uint64_t count = 0, dropped = 0;  // dropped: discarded/abandoned or unusable clock
    double mean_ms = 0, p50_ms = 0, p95_ms = 0, max_ms = 0;
};
class LatencyTracker {
public:
    static constexpr std::size_t capacity = 32;
    static constexpr double bucket_ms = 0.25;
    static constexpr std::size_t buckets = 400;  // 0..100 ms; larger values land in the last bucket
    // The compositor's presentation clock; only CLOCK_MONOTONIC (1) is comparable with our reads.
    void clock(std::uint32_t id) { clock_ok_ = id == 1; }
    bool clock_ok() const { return clock_ok_; }
    void read(std::uint64_t ticket, std::uint64_t read_ns);  // pose read for this frame
    void presented(std::uint64_t ticket, std::uint64_t present_ns);
    void discarded(std::uint64_t ticket);
    void abandon();  // all pending frames lost
    // Smoothed read->present (EMA); nullopt until a few frames have been measured.
    std::optional<double> expected_ms() const;
    LatencySummary summary() const;
private:
    struct Pending { std::uint64_t ticket = 0, read_ns = 0; bool used = false; };
    std::array<Pending, capacity> pending_{};
    std::array<std::uint64_t, buckets> histogram_{};
    std::uint64_t count_ = 0, dropped_ = 0;
    double sum_ms_ = 0, max_ms_ = 0, ema_ms_ = 0;
    bool clock_ok_ = false;
};

// Relative pose age: host read time minus device timestamp, less the smallest such value seen
// (device and host clocks have an unknown fixed offset). At ~1 kHz the minimum approaches the
// transport floor, so this is age ABOVE the best observed case, not an absolute age.
class PoseAge {
public:
    std::optional<double> observe(double host_ms, float device_ms);  // nullopt on reset/backward device clock
    LatencySummary summary() const;
    void reset() { *this = PoseAge{}; }
private:
    std::optional<double> floor_ms_;
    float last_device_ms_ = -1;
    std::uint64_t count_ = 0;
    double sum_ms_ = 0, max_ms_ = 0;
    std::array<std::uint64_t, 200> histogram_{};  // 0.25 ms buckets, 0..50 ms
};

// Horizon (ms from the newest sample) for this frame, or nullopt if prediction cannot be bounded.
// frametime_ms is used only if no read->present measurement is available yet (driver formula).
std::optional<float> prediction_horizon(const std::optional<LookAhead>& cfg, std::optional<double> read_to_present_ms,
                                        double pose_age_ms, double frametime_ms);
}
