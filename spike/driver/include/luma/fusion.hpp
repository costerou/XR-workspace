#pragma once
// luma::Fusion: IMU samples in, gravity-levelled orientation out.
//
// Written from the textbook: Mahony, Hamel, Pflimlin, "Nonlinear complementary filters on the
// special orthogonal group", IEEE TAC 53(5), 2008 (the gravity correction and gyro-bias
// integrator); Savage, "Strapdown inertial navigation integration algorithm design", JGCD 21(1),
// 1998 (the rotation-vector step). No vendor code or SDK.
//
// Input frame: body x forward, y left, z up, right-handed, the frame spike/include/xr/pose.hpp
// calls the driver frame. Accelerometer: specific force, so level and still reads +9.8 on z.
// The raw stream's frame is the protocol layer's business. XRLinuxDriver's src/devices/viture.c
// documents the raw triads as East-Down-North, so forward = z, left = -x, up = -y there.
//
// Output: PoseOut::q turns body vectors into world vectors, with world z exactly up (true
// gravity), which is what XRLinuxDriver's feed lacks: it publishes conj(reference) * pose, so its
// z is wherever the glasses' up was at calibration. World x is the heading the glasses faced when
// calibration finished. Yaw has no absolute reference without the magnetometer.
//
// update() allocates nothing and takes no lock. Not thread-safe: one thread owns a Fusion.
#include "luma/types.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>

namespace luma {

enum class FusionQuality : std::uint8_t {
    Uncalibrated,  // PoseOut::calibrated false. Tilt is already right, yaw drifts at the raw bias.
    Coarse,        // Usable. Never had a proper still window (worn or moving at start); bias is a best guess.
    Good,          // Bias measured while still.
    Recovering,    // Calibrated, but samples were lost or time jumped: gravity is being re-acquired.
};

struct FusionConfig {
    // Start-up.
    float cal_still_s = 1.5f;        // Unbroken stillness needed for Good.
    float coarse_timeout_s = 2.5f;   // No stillness by now: go Coarse. calibrated turns true.
    // Bias saved from an earlier session (FusionStatus::gyro_bias_rad_s). Bias barely moves between
    // sessions, so this gives usable tracking from the first sample: Coarse at once, Good as usual.
    std::optional<std::array<float, 3>> warm_start_bias;
    // Stillness, per 100 ms block. "still" = on a desk. "quiet" = worn, head resting.
    float still_gyro_std = 0.015f;   // rad/s
    float still_accel_std = 0.15f;   // m/s2
    float still_mean_step = 0.002f;  // rad/s a block's mean may move from the last one's, above noise
    float quiet_gyro_std = 0.05f;    // rad/s
    float quiet_accel_std = 0.4f;    // m/s2
    float cal_max_rate = 0.175f;     // rad/s; a "bias" above this (10 deg/s) is a slow real turn
    // Gravity correction gains (rad/s per rad of tilt error).
    float kp_moving = 0.1f;
    float kp_still = 0.3f;
    float kp_recover = 6.f;          // Before calibration and while Recovering.
    float ki = 0.002f;               // Bias integrator from gravity. Sees only the two tilt axes.
    float accel_norm_gate = 0.05f;   // Fraction of g at which the correction has faded to nothing.
    float accel_rate_gate = 2.5f;    // rad/s at which the correction has faded to nothing.
    // Bias tracking in place of a temperature model.
    float still_tau_s = 5.f;
    float still_track_limit = 0.01f; // rad/s; a change above this needs still_relearn_s of stillness
    float still_relearn_s = 20.f;
    float still_relearn_limit = 0.03f;   // rad/s; above this a smooth "still" reading is a real slow turn
    float quiet_max_rate = 0.05f;    // rad/s; a block turning faster than this is not a rest
    float quiet_window_s = 30.f;     // Rest time averaged per bias update.
    float quiet_tau_s = 60.f;
    float quiet_track_limit = 0.0175f;  // rad/s (1 deg/s)
    float quiet_window_coarse_s = 5.f;
    float quiet_tau_coarse_s = 10.f;
    float quiet_limit_coarse = 0.05f;
    float coarse_to_good_s = 20.f;   // Quiet time that promotes Coarse to Good.
    float max_bias = 0.35f;          // rad/s
    // Timing.
    float max_gap_s = 0.25f;         // A longer gap is not integrated: that motion is lost.
    float recover_s = 0.5f;          // Least time in Recovering.
    float recover_tilt_rad = 0.005f;  // Tilt error that ends Recovering.
    float recover_max_s = 2.f;       // Moving heads may never show that: give up this long after recover_s.
    // Magnetometer: off by default. Hard-iron is uncorrected and a monitor or desk bends the field.
    bool use_mag = false;
    float kp_mag = 0.2f;
    float mag_gate = 0.25f;          // Fraction of the reference field strength allowed off.
};

struct FusionStatus {
    FusionQuality quality = FusionQuality::Uncalibrated;
    bool still = false;
    std::array<float, 3> gyro_bias_rad_s{};
    std::array<float, 3> omega_body_rad_s{};   // Newest sample less bias, unfiltered: no added delay.
    std::array<float, 3> omega_world_rad_s{};
    std::array<float, 3> up_body{0, 0, 1};     // World up seen from the glasses.
    float calibrated_after_s = -1;             // Stream time when calibrated turned true; -1 before.
    std::uint64_t samples = 0;
    std::uint64_t rejected = 0;                // Non-finite or duplicate: not used.
    std::uint64_t gaps = 0;                    // Motion lost: not integrated.
    std::uint64_t time_jumps = 0;              // Timestamp nonsense bridged by sequence number.
};

class Fusion {
public:
    explicit Fusion(const FusionConfig& config = {});
    ~Fusion();
    Fusion(Fusion&&) noexcept;
    Fusion& operator=(Fusion&&) noexcept;
    PoseOut update(const ImuSample& sample);
    FusionStatus status() const;
    // Orientation horizon_s after the newest sample at its angular velocity: first-order
    // prediction for the renderer. horizon_s = 0 is PoseOut::q.
    Quatf predict(float horizon_s) const;
    void reset();  // Back to start-up. A USB reconnect does not need it: see Recovering.

private:
    struct State;
    std::unique_ptr<State> s_;
};

}  // namespace luma
