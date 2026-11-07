#pragma once
// Frame-rate motion from a slow, uneven pose feed. Off unless a run asks for it (--pro2-test-smooth-ms): today only the Pro 2.
//
// The Pro 2's SDK pose arrives at 60 Hz on average in a strict 15, 15, 20 ms cadence (docs/pro2/smoothness.md). Held for a
// 120 Hz display that is one still frame in two, and because the cadence beats against 8.33 ms a sample is sometimes held
// for one frame and sometimes for three: the "losing frames" feel. The legacy prediction (angular_velocity, then newest *
// delta, recomputed from scratch each frame) moves every frame, but each new sample shifts the prediction by its velocity
// error times the whole horizon, so the steps jump at the sample rate.
//
// So the output is a state of its own, stepped once per frame:
//     output = output * exp(velocity * dt)                  carry on at the measured speed: every frame moves
//     output = output * exp(error * (1 - exp(-dt / tau)))   then ease out what the newest prediction says is left over
// where error = output^-1 * target and target = newest sample * exp(velocity * horizon). The ease is the continuity filter:
// a new sample changes the slope, never the position. tau is short, so the output stays on the prediction, within a frame or
// two's worth of its error.
//
// velocity is the rotation between the newest and the second-oldest of the last three samples over their REAL device-time
// gap: two intervals, because one interval's rate is noisier, and real time, because the gaps differ by a third and speed
// computed on them is flat across the cadence (measured on the captures). Nothing here assumes a period.
//
// Bounds, in the order they apply:
//   - horizon: `prediction_horizon` already caps it (the feed's look_ahead_cfg); `max_predict_deg` caps the rotation.
//   - brake: the newest interval's speed along the window's velocity, as a share of it, scales the velocity: a head that is
//     stopping or turning back is not extrapolated past. A head speeding up is not scaled: nothing to overshoot yet.
//   - a gap in device time over `max_gap_ms` means the velocity is unknown: no prediction, it eases to the sample.
//   - no new sample for `stall_ms`: the speed stops being carried, so the output settles on the target, within `max_predict_deg`.
//   - a jump over `snap_deg` between samples, or an error that size, is not motion: the history is dropped and the output takes
//     the target at once. A recentre does not get that far: Presentation::recenter resets this.
// Allocation free, no clock reads, no I/O.
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <optional>

namespace xr {
struct MotionSmoothParams {
    float tau_ms = 35.f;            // continuity filter time constant
    float max_predict_deg = 6.f;    // rotation the prediction may add to the newest sample
    float snap_deg = 30.f;          // a larger error than this is taken at once: 1500 deg/s in a 20 ms gap, past any head
    float max_gap_ms = 60.f;        // a longer gap between samples has no usable velocity
    float stall_ms = 40.f;          // past the longest normal wait seen by a reader: the 20 ms gap, the 2 ms poll, a frame's 8 ms
    float brake_power = 3.f;        // velocity scale while slowing: (newest interval's share of the window's speed) ^ this
    float brake_floor_rad_per_ms = 4.f * 3.14159265f / 180000.f;  // 4 deg/s: speeds around this are noise, not a stop
};
std::optional<MotionSmoothParams> motion_smooth_params(float tau_ms);  // tau 5..80 ms, else nullopt

class MotionSmoother {
public:
    explicit MotionSmoother(MotionSmoothParams p = {}) : p_(p) {}
    void reset() { valid_ = false; count_ = 0; }
    // `relative`: the newest (stabilized) sample, recentred; it only changes when device_ms does. horizon_ms: from that sample
    // to the frame's presentation. now_ms: the frame's own clock (any clock that only goes forward). Returns the pose to show.
    Quat update(Quat relative, float device_ms, float horizon_ms, double now_ms);
    std::uint64_t snaps() const { return snaps_; }
    float last_correction_deg() const { return last_correction_deg_; }
    float last_predicted_deg() const { return last_predicted_deg_; }
    float last_horizon_ms() const { return last_horizon_ms_; }
    float max_predicted_deg() const { return max_predicted_deg_; }
    float peak_speed_dps() const { return peak_speed_dps_; }           // the fastest the head was measured going
    std::uint64_t capped_frames() const { return capped_frames_; }     // frames that wanted more than max_predict_deg
    std::uint64_t frames() const { return frames_; }
private:
    void learn(Quat relative, float device_ms);
    std::array<float, 3> velocity() const;
    MotionSmoothParams p_;
    bool valid_ = false;
    Quat output_{};
    double last_ms_ = 0, new_sample_ms_ = 0;
    std::array<Quat, 3> q_{};            // newest first, distinct device times
    std::array<float, 3> t_{};
    std::uint32_t count_ = 0;
    std::uint64_t snaps_ = 0, capped_frames_ = 0, frames_ = 0;
    float peak_speed_dps_ = 0;
    float last_correction_deg_ = 0, last_predicted_deg_ = 0, last_horizon_ms_ = 0, max_predicted_deg_ = 0;
};
}
