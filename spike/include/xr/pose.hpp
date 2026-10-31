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
    std::uint64_t samples_ = 0, lost_events_ = 0;
};
}
