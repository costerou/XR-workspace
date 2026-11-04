#pragma once
// The head pose as XRLinuxDriver publishes it, so spike/src/pose.cpp reads our driver with no change. Writer side, from
// github.com/wheaney/XRLinuxDriver (src/ipc.c, src/outputs.c, src/buffer.c, src/driver.c, src/devices/viture.c):
//
//   One SysV segment per value, each keyed ftok(<prefix><name>, 0) on an empty file, created 0666:
//     pose_orientation        16 floats: q0 xyzw, q1 xyzw, q2 xyzw, t0, t1, t2, 0
//     pose_orientation_mutex  pthread_mutex_t, process-shared and robust, guards the 16 floats
//     disabled                bool: true = readers get no sample
//     display_fov             float, degrees (Luma Ultra 52)
//     look_ahead_cfg          4 floats: constant ms, frametime multiplier, scanline adjust ms, cap ms (Luma Ultra 10, 0.6, 10, 40)
//   q1 is the sample history_step samples back, q2 twice that; history_step = IMU rate / 60, so 16 at 1 kHz. Every sample
//   is written from the 2 * history_step + 1st on. t are whole milliseconds as floats (exact below 2^24), truncated, from
//   the first sample, and restart at 0 a second short of 2^24.
//
// The rest of the driver's segments (pose_position, keepalive_date, display_resolution, ...) have no reader here.
//
// Where it differs from the driver on purpose, none of it visible to a reader that works today:
//   - disabled stays true until the first real sample is written, and goes back to true while the glasses are away or
//     calibration is lost. The driver clears it as soon as it connects, which shows identity at t 0 until it has calibrated;
//     after a USB drop that would swing the view to the recentre pose and back.
//   - instant_start: a sample is published from the first one on, not the 2 * history_step + 1st, so the 32 ms after every
//     start, reconnect and stale gap are not spent blind. While there is no sample history_step back, q1 and q2 repeat q0
//     and t: pose.cpp reads a valid pose and predict.cpp's spacing check turns prediction off, rather than deriving a speed
//     from a couple of ms. From history_step on, q1 is real and prediction runs on it; from 2 * history_step on, every
//     write is the driver's.
//   - publish never waits. The driver blocks on the mutex for as long as a reader holds it. Here a busy lock skips that one
//     write, whose sample stays in the history: a stopped reader cannot slow the thread that drains USB, and a reader
//     preempted under the lock cannot invert the priority of a real-time one.
//   - fractional_ms: t keeps its fraction of a ms.
//   - mount_pitch_deg is applied here; the driver applies it on the way in.
//   - A reader that died holding the lock is recovered. The driver ignores lock errors.
//   - open refuses when another writer is running: see PublisherOptions::proc_root.
//
// Publisher is for one thread. publish() does not allocate.
#include "luma/types.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <string>

namespace luma {
struct PublisherOptions {
    std::string prefix = "/tmp/shader_runtime_";  // tests pass a private one: see private_segments
    std::uint32_t history_step = 16;              // history_step_for(IMU rate)
    float display_fov_deg = 52.f;
    std::array<float, 4> look_ahead_cfg{10.f, 0.6f, 10.f, 40.f};
    // Tilt of the display against the body frame the pose is in, turned out of every pose as q * pitch: XRLinuxDriver's
    // viture_pitch_adjustments (src/driver.c driver_handle_pose). That number is for the SDK's pose; that our IMU frame
    // has the same tilt is an assumption until it is checked on the glasses. 0 = none.
    float mount_pitch_deg = -8.5f;
    bool instant_start = true;   // see above; false = silent until the history is full, exactly as the driver
    // t keeps the fraction of a ms. Same floats to a reader; through predict.cpp, at 1 kHz with 0.02 to 0.5 ms of stamp
    // jitter, a 20 ms prediction during a fast turn was 0.004 to 0.18 degrees off rms, against 0.1 to 0.21 truncated.
    // false = truncated, exactly as the driver.
    bool fractional_ms = true;
    // false (production): reuse segments that exist, as the driver does, and leave them when closing, so a renderer stays
    //   attached across a driver restart.
    // true (tests): ftok keys hold 16 bits of the inode, so even a private prefix can land on a live driver's key. Then
    //   only ever create segments: open fails with "key in use" rather than attach, and closing removes them and the files.
    bool private_segments = false;
    // Two writers garble the pose and one would re-initialise the mutex under the other. Without private_segments, open
    // fails with "another publisher" while one holds <prefix>writer.lock (released even if it crashes) and with "xrDriver
    // is running" when a live process has that name under this /proc. Checked at open only.
    std::string proc_root = "/proc";
};

// src/devices/viture.c viture_apply_imu_rate.
constexpr std::uint32_t history_step_for(std::uint32_t imu_hz) { return imu_hz < 120 ? 1 : imu_hz / 60; }

struct PublisherStats {
    std::uint64_t written = 0;
    std::uint64_t invalid = 0;  // quaternions dropped
    std::uint64_t busy = 0;     // writes skipped on a held lock, their samples kept
};

class Publisher {
public:
    // Creates the files and segments, re-initialises the mutex, stores identity, FOV and look-ahead, and leaves the feed
    // disabled. nullptr with why set on failure.
    static std::unique_ptr<Publisher> open(const PublisherOptions& options, std::string& why);
    ~Publisher();  // disables the feed
    Publisher(const Publisher&) = delete;
    Publisher& operator=(const Publisher&) = delete;

    // pose.q: the glasses' orientation with x forward, y left, z up (the frame xr::driver_to_gl assumes), body to reference.
    // It is published as given, with no recentring: PoseState takes its own centre, and a gravity-aligned feed is what its
    // up estimate wants. pose.t_ns may be any clock that only goes forward; if it goes back, t restarts at 0. pose.seq has
    // no place in the feed.
    // True when the feed was written. False for a non-finite or zero quaternion (dropped, the feed is untouched), when the
    // lock is held, for pose.calibrated == false, which pauses the feed, and without instant_start while the history fills.
    bool publish(const PoseOut& pose);
    // The feed is stale or the glasses went away: readers get no sample until the next publish that writes. History starts
    // over, the clock of t is kept. (Daemon's set_stale(true); its set_stale(false) needs nothing, publish clears it.)
    void pause();

    PublisherStats stats() const;
private:
    Publisher();
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
