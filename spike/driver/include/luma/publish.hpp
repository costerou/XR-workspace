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
// Three deliberate differences, all invisible to a reader that works today:
//   - disabled stays true until the first real sample is written, and goes back to true while the glasses are away or
//     calibration is lost. The driver clears it as soon as it connects, which shows identity at t 0 until it has calibrated;
//     after a USB drop that would swing the view to the recentre pose and back.
//   - A reader that stopped holding the lock costs one sample, not the writer: publish waits at most 2 ms.
//   - A reader that died holding the lock is recovered. The driver ignores lock errors.
//
// Publisher is for one thread, and one writer per prefix: run it or xrDriver, never both. publish() does not allocate.
#include "luma/types.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <string>

namespace luma {
struct PublisherOptions {
    std::string prefix = "/tmp/shader_runtime_";  // tests pass a private one: see private_segments
    std::uint32_t history_step = 16;              // IMU rate / 60, at least 1
    float display_fov_deg = 52.f;
    std::array<float, 4> look_ahead_cfg{10.f, 0.6f, 10.f, 40.f};
    // false (production): reuse segments that exist, as the driver does, and leave them when closing, so a renderer stays
    //   attached across a driver restart.
    // true (tests): ftok keys hold 16 bits of the inode, so even a private prefix can land on a live driver's key. Then
    //   only ever create segments: open fails with "key in use" rather than attach, and closing removes them and the files.
    bool private_segments = false;
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
    // True when the feed was written. False while the history fills, for a non-finite or zero quaternion (dropped, the feed
    // is untouched), when the lock stayed busy for 2 ms, and for pose.calibrated == false, which pauses the feed.
    bool publish(const PoseOut& pose);
    // The glasses went away: readers get no sample until the history has filled again. The clock of t is kept.
    void pause();

    std::uint64_t written() const;
    std::uint64_t dropped() const;  // invalid quaternions and lock timeouts
private:
    Publisher();
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
