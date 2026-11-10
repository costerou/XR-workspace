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
//   horizon = pose age + expected read->present + driver constant, capped by the driver's cap,
//   plus half the driver's scanline adjustment (we render one pose per frame; screen centre).
// Expected read->present is measured; once the refresh grid is learned it is estimated for this
// frame's read phase (LatencyTracker::expected_ms), so read timing jitter does not shake the view.
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
// samples are unusable: non-increasing or implausible spacing (outside 2..50 ms), non-finite. The
// spacing is t0 - t1, or the mean of t0 - t1 and t1 - t2 when they differ only by timestamp
// quantisation (whole, truncated ms): the driver's samples are a fixed number of IMU cycles apart.
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
    static constexpr std::size_t phase_bins = 16;  // read phase within the refresh period
    // The compositor's presentation clock; only CLOCK_MONOTONIC (1) is comparable with our reads.
    void clock(std::uint32_t id) { clock_ok_ = id == 1; }
    bool clock_ok() const { return clock_ok_; }
    void read(std::uint64_t ticket, std::uint64_t read_ns);  // pose read for this frame
    void presented(std::uint64_t ticket, std::uint64_t present_ns);
    void discarded(std::uint64_t ticket);
    void abandon();  // all pending frames lost
    // Expected read->present for a pose read at now_ns (CLOCK_MONOTONIC); nullopt until a few frames
    // have been measured. The smoothed average (EMA) until the refresh grid is learned from presented
    // timestamps and estimating per read phase on it has beaten the average on recent frames; then
    // the estimate for a read at this phase (a read later in the refresh period is presented sooner).
    // No allocation, no system call.
    std::optional<double> expected_ms(std::uint64_t now_ns) const;
    std::optional<double> expected_ms() const;  // for a read now (one vDSO clock read)
    bool phase_aware() const { return use_phase_; }  // the per-phase estimate is in use
    LatencySummary summary() const;
private:
    struct Pending { std::uint64_t ticket = 0, read_ns = 0; float by_phase_ms = -1, by_ema_ms = -1; bool used = false; };
    std::optional<double> by_phase(std::uint64_t now_ns) const;
    void learn_grid(std::uint64_t present_ns);
    std::array<Pending, capacity> pending_{};
    std::array<std::uint64_t, buckets> histogram_{};
    std::uint64_t count_ = 0, dropped_ = 0;
    double sum_ms_ = 0, max_ms_ = 0, ema_ms_ = 0;
    bool clock_ok_ = false;
    // Refresh grid: the newest presented timestamp and the period between presentations.
    std::uint64_t grid_ns_ = 0;
    double period_ms_ = 0;
    std::uint32_t off_period_ = 0;  // presentations in a row that were not one period apart
    // Per read-phase bin: smoothed whole periods from the grid point before the read to its presentation.
    std::array<float, phase_bins> periods_{};
    std::array<std::uint32_t, phase_bins> phase_count_{};
    double phase_err_ms_ = 0, ema_err_ms_ = 0;  // smoothed |error| of each estimate on presented frames
    std::uint32_t compared_ = 0;
    bool use_phase_ = false;
};

// Relative pose age: host read time minus device timestamp, less the smallest such value seen over
// the last few seconds of device time (device and host clocks have an unknown offset, and a device
// crystal that drifts against the host by tens of ppm would otherwise grow the age by ms per minute;
// a feed whose device clock stops keeps its floor, so its age keeps growing). At ~1 kHz the minimum
// approaches the transport floor, so this is age ABOVE the best recent case, not an absolute age.
class PoseAge {
public:
    static constexpr std::size_t floor_windows = 8;  // one-second windows of device time
    std::optional<double> observe(double host_ms, float device_ms);  // nullopt on reset/backward device clock
    LatencySummary summary() const;
    void reset() { *this = PoseAge{}; }
private:
    static constexpr std::int64_t kNoWindow = INT64_MIN;
    std::array<double, floor_windows> window_min_{};  // per device second: smallest host - device
    std::array<std::int64_t, floor_windows> window_s_ = [] { std::array<std::int64_t, floor_windows> a{}; a.fill(kNoWindow); return a; }();
    float last_device_ms_ = -1;
    std::uint64_t count_ = 0;
    double sum_ms_ = 0, max_ms_ = 0;
    std::array<std::uint64_t, 200> histogram_{};  // 0.25 ms buckets, 0..50 ms
};

// The driver's scan-out times against what the compositor says (rust-rebuild-plan D5), per presented frame:
//   horizon error    present - the driver's scan-out nearest it (wp_presentation's clock is CLOCK_MONOTONIC, as is the
//                    driver's anchor), wrapped into half a period either side. Its mean (as a phase) is the constant between the two:
//                    the compositor's vblank against the glasses' panel, and the link floor the driver cannot see
//                    (vsync.hpp). What the gate measures is the spread about that constant: how far a horizon taken from
//                    the driver's vsync is off, frame by frame.
//   sensor to vsync  that scan-out on the feed's clock minus the device_ms of the sample the frame drew: how old the pose
//                    is when the panel shows it, with the driver's own transport floor in it.
// Only with a locked snapshot whose anchor is under kMaxAnchorAgeNs from the present. The newest kFrames frames are kept.
struct VsyncSummary {
    std::uint64_t count = 0, skipped = 0;  // skipped: presented with no usable snapshot
    double offset_ms = 0;                   // the mean phase of the horizon error: panel and link, a constant
    double error_p50_ms = 0, error_p95_ms = 0, error_max_ms = 0;  // |horizon error - offset|
    double sensor_p50_ms = 0, sensor_p95_ms = 0, sensor_max_ms = 0;
    std::uint64_t sensor_count = 0;
    double period_ms = 0;  // the newest snapshot's
};
class VsyncMeter {
public:
    static constexpr std::size_t kFrames = 8192;  // about 70 s at 120 Hz
    static constexpr double kMaxAnchorAgeNs = 250e6;
    // sample_ms: the device_ms of the sample drawn; negative: none.
    void presented(const std::optional<VsyncSnapshot>& vsync, std::uint64_t present_ns, float sample_ms);
    VsyncSummary summary() const;
private:
    std::array<float, kFrames> error_ms_{}, sensor_ms_{};
    std::size_t errors_ = 0, sensors_ = 0;  // written in all; the newest kFrames are kept
    std::uint64_t skipped_ = 0;
    double period_ms_ = 0;
};

// Horizon (ms from the newest sample) for this frame, or nullopt if prediction cannot be bounded.
// frametime_ms is used only if no read->present measurement is available yet (driver formula).
std::optional<float> prediction_horizon(const std::optional<LookAhead>& cfg, std::optional<double> read_to_present_ms,
                                        double pose_age_ms, double frametime_ms);
}
