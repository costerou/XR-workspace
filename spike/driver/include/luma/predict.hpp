#pragma once
// Head-pose prediction for the Luma driver: from the gyro's newest sample to when the frame will be seen.
//
// What the renderer does today (spike/include/xr/predict.hpp), which all of this is built to fit:
//   - it reads q0, q1, q2 from the feed and takes velocity from q0 and q1, 16 ms apart: that velocity
//     is the head's about 8 ms ago, and it still reports motion for 16 ms after a stop;
//   - horizon = min(pose age + measured read->present + constant, cap) + scanline / 2, with
//     look_ahead_cfg = {constant, frametime multiplier, scanline adjust, cap};
//   - newest * delta = predicted, delta in the body frame, velocity in rad/ms, clamped to 15 degrees.
// The feed's newest pose must therefore stay UNPREDICTED: predicting it here would predict twice.
//
// This code has the gyro, one sample old, so it can do better, in two ways. Both leave the horizon to
// the renderer: only it knows when a frame will be presented.
//   1. synthesize_history(): no renderer change at all. q0 stays the real pose; q1 and q2 are rebuilt
//      so the renderer's own finite difference yields this predictor's velocity. Behind a flag in the
//      publisher: PredictConfig::synthesize_feed_history.
//   2. PredictionWire: a lock-free side channel with velocity and acceleration, for a renderer change
//      later, which would call extrapolate() with its own horizon.
//
// Motion is estimated per axis by critically damped alpha-beta filters (the fading-memory polynomial
// filter), which do not lag on a steady acceleration: a short one for velocity, a longer one for
// acceleration, and a shorter one again for slowing down. Stops are the danger of adding acceleration,
// so extrapolation ends where the velocity reaches zero instead of running on backwards.
//
// All frames are the fusion's body frame: q(t + dt) = q(t) * exp(omega * dt), x forward, y left, z up,
// the same as the feed. conj(reference) * q changes nothing here, so a recentre needs no handling.
// Sources: XRLinuxDriver src/outputs.c, src/devices/viture.c (Luma Ultra row: 10 ms, 0.6, 10 ms, 40 ms),
// src/plugins/virtual_display.c (look_ahead override). Fading-memory filter: Brookner, Tracking and
// Kalman Filtering Made Easy, ch. 1. No SDK involved.
#include "luma/types.hpp"
#include <array>
#include <atomic>
#include <cstdint>
#include <optional>

namespace luma {
using Vec3f = std::array<float, 3>;

// The look_ahead_cfg the feed publishes. Defaults: XRLinuxDriver's Luma Ultra values. override_ms != 0
// does what XRLinuxDriver's `look_ahead` setting does: replaces constant, drops the frametime term.
struct LookAhead {
    float constant_ms = 10, frametime_mult = 0.6f, scanline_ms = 10, cap_ms = 40, override_ms = 0;
};
// {constant, frametime multiplier, scanline adjust, cap}, always inside what xr::look_ahead_from accepts
// (a value outside it would turn the renderer's prediction off): NaN takes the default, the rest are clamped.
std::array<float, 4> look_ahead_cfg(const LookAhead& cfg);

struct PredictConfig {
    bool enabled = true;                  // off: filters still run, predictions are the newest pose
    bool use_acceleration = true;
    bool stop_clamp = true;               // extrapolation ends where the velocity reaches zero
    bool still_gate = true;               // a still head is not extrapolated, so gyro noise cannot shake it
    bool reject_spikes = true;            // a single impossible gyro reading is dropped
    bool synthesize_feed_history = false; // for the publisher; see synthesize_history
    float refresh_hz = 120;               // the display: Luma Ultra at 120 Hz
    float nominal_imu_hz = 1000;          // a start; the real rate is learned from the stamps
    float omega_tau_ms = 2;               // velocity filter memory
    float alpha_tau_ms = 8;               // acceleration filter memory
    float brake_tau_ms = 3;               // ... and while slowing down, when stop_clamp is on
    float still_lo_dps = 2.5f, still_hi_dps = 8;  // no prediction below lo, all of it above hi
    float spike_rad_s = 6;                // jump from the expected velocity in one sample: about 340 deg/s
    float gap_ms = 50;                    // longer without samples: earlier motion says nothing
    float max_deg = 15;                   // the renderer's kMaxPredictionDeg
    LookAhead look_ahead;
};

// The newest pose with its motion: what is published alongside it.
struct MotionState {
    Quatf q;                     // newest pose, unpredicted
    std::uint64_t host_ns = 0;   // CLOCK_MONOTONIC when that sample arrived
    std::uint32_t seq = 0;
    Vec3f omega_rad_s{};         // body frame, filtered
    Vec3f alpha_rad_s2{};        // body frame, filtered
    bool valid = false, calibrated = false;
};
inline Vec3f omega_rad_per_ms(const MotionState& s) {  // what xr::prediction_delta takes
    return {s.omega_rad_s[0] / 1000, s.omega_rad_s[1] / 1000, s.omega_rad_s[2] / 1000};
}

struct Predicted {
    Quatf q;
    std::uint64_t target_ns = 0;
    float horizon_ms = 0, predicted_deg = 0;
    bool clamped = false;        // the max_deg limit was reached
};
// Pure: anyone holding a MotionState can call it with their own horizon (ms from state.host_ns).
Predicted extrapolate(const MotionState& state, float horizon_ms, const PredictConfig& cfg);

// First point of the grid anchor + k * period strictly after after_ns; 0 with no period.
std::uint64_t next_vsync_ns(std::uint64_t after_ns, std::uint64_t anchor_ns, std::uint64_t period_ns);

struct PredictStats { std::uint32_t rejected = 0, resets = 0; };

// update() is for the driver's IMU loop: no allocation, no system call, no lock.
class Predictor {
public:
    Predictor() : Predictor(PredictConfig{}) {}
    explicit Predictor(const PredictConfig& cfg);
    // imu and pose are the same sample. imu.t_ns times the filters (device or host clock, either works);
    // host_ns is CLOCK_MONOTONIC on arrival, the clock every horizon is measured on.
    void update(const PoseOut& pose, const ImuSample& imu, std::uint64_t host_ns);
    const MotionState& state() const { return state_; }
    const PredictStats& stats() const { return stats_; }
    // Fusion's current estimate (FusionStatus::gyro_bias_rad_s), taken off every sample from now on.
    void set_gyro_bias(const Vec3f& bias_rad_s) { bias_ = bias_rad_s; }
    // Where the vsync grid is, on CLOCK_MONOTONIC, when something can tell. Without one, the horizon is
    // XRLinuxDriver's: constant + multiplier * frame time.
    void set_vsync(std::uint64_t anchor_ns, std::uint64_t period_ns) { anchor_ns_ = anchor_ns; period_ns_ = period_ns; }
    // ms from now_ns to the middle of the display showing the next vsync's frame, same shape as the
    // renderer's prediction_horizon: min(wait + constant, cap) + scanline / 2.
    float horizon_ms(std::uint64_t now_ns) const;
    Predicted predict(float horizon_ms) const { return extrapolate(state_, horizon_ms, cfg_); }
    Predicted predict_next_vsync() const { return predict(horizon_ms(state_.host_ns)); }
private:
    struct Axis { float fast = 0, fast_slope = 0, slow = 0, alpha = 0, quick = 0, brake = 0; };
    void restart(const Vec3f& gyro);
    PredictConfig cfg_;
    std::array<float, 4> look_ahead_;
    MotionState state_;
    PredictStats stats_;
    std::array<Axis, 3> axes_{};
    Vec3f bias_{};
    // Sample clock: stamps may be whole ms or arrival times, so the filters run on a smoothed grid.
    double clock_ns_ = 0, period_ns_imu_ = 0, nominal_ns_ = 0;
    std::uint64_t last_raw_ns_ = 0;
    std::uint32_t last_seq_ = 0;
    bool started_ = false, spike_pending_ = false;
    std::uint64_t anchor_ns_ = 0, period_ns_ = 0;
};

// What the publisher writes as q0..q2 and their stamps, when synthesize_feed_history is on. q[0] is the
// real pose. q[1], q[2] sit where a head on the predicted path would have been spacing_ms and twice that
// ago, so the renderer's finite difference, run over horizon_ms, lands on predict(horizon_ms).
// Stamps are exact, and spacing inside the renderer's 2..50 ms.
struct FeedHistory { std::array<Quatf, 3> q; std::array<float, 3> device_ms{}; };
FeedHistory synthesize_history(const MotionState& state, float device_ms, float horizon_ms,
                               const PredictConfig& cfg = {}, float spacing_ms = 16);

// Side channel: one cache line in shared memory, named prefix + kPredictionIpcName like the driver's other
// segments (/tmp/shader_runtime_pose_prediction). A sequence lock: the driver never waits for a reader, a
// reader never blocks the driver, and a driver killed mid-write leaves it odd, which reads as no sample.
inline constexpr const char* kPredictionIpcName = "pose_prediction";
inline constexpr std::uint32_t kPredictionMagic = 0x4c505231;  // "LPR1"; a layout change is a new magic
struct alignas(64) PredictionWire {
    std::atomic<std::uint32_t> seq{0};     // odd while being written
    std::atomic<std::uint32_t> magic{0};
    // host_ns lo, hi, sample seq, q x, y, z, w (the feed's order), omega xyz rad/s, alpha xyz rad/s^2, flags
    std::array<std::atomic<std::uint32_t>, 14> words{};
};
static_assert(sizeof(PredictionWire) == 64);
static_assert(std::atomic<std::uint32_t>::is_always_lock_free);
void write_prediction(PredictionWire& wire, const MotionState& state);
std::optional<MotionState> read_prediction(const PredictionWire& wire);  // nullopt: nothing valid right now
}
