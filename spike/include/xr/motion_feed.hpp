#pragma once
// D3 of docs/rust-rebuild-plan.md: the renderer predicts from the Luma driver's PredictionWire (luma::PredictionWire in
// spike/driver/include/luma/predict.hpp, SysV segment <prefix>pose_prediction) instead of differencing the feed's q0 and q1.
// The wire holds the driver's newest pose with the gyro's filtered velocity and acceleration, body frame, in the feed's frame,
// and that sample's arrival on CLOCK_MONOTONIC: under a ms old, against about 8 ms for the difference. XRLinuxDriver has no
// such segment, so a renderer on it keeps predicting.hpp's finite difference.
// Kept apart from pose.cpp and predict.cpp, which other tools compile on their own (pose.cpp with spike/driver/src/rt.cpp
// and -Ispike/driver/include besides spike/include, for the seqlock channel).
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace xr {
struct FeedMotion { Quat q; std::uint64_t host_ns = 0; std::array<float, 3> omega_rad_s{}, alpha_rad_s2{}; };

class MotionFeed {
public:
    // The same prefix as PoseFeed::open. nullptr when the driver publishes no wire.
    static std::unique_ptr<MotionFeed> open(const std::string& prefix);
    ~MotionFeed();
    // The newest calibrated sample: a bounded seqlock read that never waits. nullopt when the wire holds none (disabled,
    // paused, uncalibrated, mid-write, the driver exited) or what it holds is not finite.
    std::optional<FeedMotion> read() const;
private:
    MotionFeed() = default;
    const void* wire_ = nullptr;
};

// The delta to apply on the right of the wire's pose (newest * delta, like prediction_delta): luma::extrapolate horizon_ms past
// the sample's arrival, with acceleration, the stop clamp, the still gate (a still head is not extrapolated) and max_deg.
// Identity for horizon <= 0.
Quat motion_delta(const FeedMotion& motion, float horizon_ms, float max_deg);
// The sample's age at now_ns (both CLOCK_MONOTONIC), or nullopt when it cannot be used: from the future, or older than
// kMaxMotionAgeMs (a stopped driver: its motion says nothing about now).
inline constexpr double kMaxMotionAgeMs = 50;
std::optional<double> motion_age_ms(const FeedMotion& motion, std::uint64_t now_ns);
}
