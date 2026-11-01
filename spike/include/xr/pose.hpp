#pragma once
// 3DoF head pose from XRLinuxDriver's SysV IPC feed (network-blocked driver; user grant 2026-10-31)
// and the world-anchored view math. Offline-testable: the IPC path prefix is injectable.
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace xr {
struct Quat { float x = 0, y = 0, z = 0, w = 1; };
Quat quat_mul(Quat a, Quat b);
Quat quat_conj(Quat q);
std::optional<Quat> quat_normalized(Quat q);  // nullopt if non-finite or near zero length
using Mat3 = std::array<float, 9>;            // row-major
Mat3 quat_to_mat3(Quat q);
// Driver frame (x forward, y left, z up) to GL eye frame (x right, y up, z backward).
Mat3 driver_to_gl(const Mat3& driver);
Mat3 transpose(const Mat3& m);
std::array<float, 3> apply(const Mat3& m, std::array<float, 3> v);

// Where a canvas point (NDC at the centre pose) lands after the head rotates by `view` (the
// transpose of the head's relative rotation in GL frame). hw/hh = tan(half FOV). nullopt = behind.
std::optional<std::array<float, 2>> project(const Mat3& view, float ndc_x, float ndc_y, float hw, float hh);

struct FeedSample { Quat orientation; float device_ms = 0; };

class PoseFeed {
public:
    // prefix "/tmp/shader_runtime_" in production; tests pass a private prefix.
    static std::unique_ptr<PoseFeed> open(const std::string& prefix, std::string& why);
    ~PoseFeed();
    std::optional<FeedSample> read();  // under the driver's process-shared mutex; nullopt if disabled/invalid
    std::optional<float> display_fov() const;  // driver-published diagonal FOV (deg), if sane (10..120)
private:
    PoseFeed() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Tracking state: recenter, staleness (lost -> hold last good pose), view rotation for rendering.
class PoseState {
public:
    explicit PoseState(double stale_ms = 100.0) : stale_ms_(stale_ms) {}
    void update(const std::optional<FeedSample>& sample, double now_ms);
    void recenter() { recenter_pending_ = true; }
    bool tracking() const { return tracking_; }
    Mat3 view() const { return view_; }  // identity until the first valid sample
    Quat relative() const { return relative_; }  // head vs recentre reference, driver frame
    std::uint64_t samples() const { return samples_; }
    std::uint64_t lost_events() const { return lost_events_; }
private:
    double stale_ms_;
    std::optional<Quat> center_;
    Quat last_{};
    float last_device_ms_ = -1;
    double last_change_ms_ = 0;
    bool tracking_ = false, recenter_pending_ = true;
    Mat3 view_{1, 0, 0, 0, 1, 0, 0, 0, 1};
    Quat relative_{};
    std::uint64_t samples_ = 0, lost_events_ = 0;
};

Quat quat_slerp(Quat a, Quat b, float t);  // shortest arc
float quat_angle(Quat a, Quat b);          // radians between orientations
Quat level(Quat q);                        // same yaw/pitch of the forward axis, zero roll (driver frame)
Mat3 view_from(Quat head_relative, Quat anchor);  // render rotation for a canvas anchored at `anchor`

// T2 viewing modes. Where the canvas sits, relative to the recentre reference (driver frame):
//   anchor: fixed in space (3DoF world-locked)
//   locked: 0DoF, rigidly attached to the display (head-locked, includes roll)
//   follow: always following, no dead zone: a critically damped rotational spring pulls the
//           canvas toward the full head orientation (roll included). From rest it accelerates,
//           then decelerates smoothly into centre without overshoot (like the Beast's smooth follow)
// Mode changes blend smoothly from wherever the canvas currently is; steady states are exact.
enum class ViewMode : std::uint8_t { anchor = 0, locked = 1, follow = 2 };
class ModeController {
public:
    // follow_omega: spring natural frequency (rad/s); higher = snappier. 8 covers ~90% of a turn in 0.5 s.
    struct Tuning { float transition_ms = 300, follow_omega = 8.f; };
    ModeController() = default;
    explicit ModeController(Tuning t) : tuning_(t) {}
    void set_mode(ViewMode mode, double now_ms);
    void recenter();  // the reference moved to the current head: anchors return to straight ahead
    ViewMode mode() const { return mode_; }
    // Advance with the head's relative orientation; returns the canvas anchor for this frame.
    Quat update(Quat head, double now_ms);
    bool settled() const;  // nothing animating (no mode blend; follow spring at rest)
private:
    Quat target(Quat head) const;
    Tuning tuning_{};
    ViewMode mode_ = ViewMode::anchor;
    Quat anchor_{}, from_{}, follow_{};
    double started_ms_ = -1, last_ms_ = -1;
    std::array<float, 3> velocity_{};  // follow angular velocity (rad/s, canvas frame)
    bool transitioning_ = false;
};
}
