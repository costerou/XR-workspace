#pragma once
// When the glasses' panel scans out, on the host's clock and on the pose feed's (rust-rebuild-plan D5).
//
// The Luma Ultra sends one display event per refresh on 0x83 (protocol.hpp parse_display_event), stamped with the same
// 100 us device clock as its IMU packets. The event's arrival on the host is late by USB polling, up to a poll interval and
// more under load, so its host time is not used for the phase. Instead:
//
//   period, phase  a straight line through the device stamps of the recent events (frame number against device time). The
//                  100 us stamp rounding averages out over the window; a missed event is a skipped frame number; anything
//                  that does not fit (a mode change, 60 to 120 Hz, which also stops events for 1.5 s; a clock that went
//                  back) starts the line again.
//   device to host the lowest host - device over the last second of IMU packets: 1000 a second, so the fastest of them came
//                  through at the link's floor (the method of D1, include/luma/latency_log.hpp). The same for the pose feed's
//                  clock (PoseOut.t_ns, which counts samples and so runs at its own rate) against the host.
//
// So the scan-out time on the host is the fitted device time plus the device-to-host floor. That floor holds the link's
// shortest transfer time, a constant of 100 to 200 us that nothing on the host can see: a compare against wp_presentation
// sees it as part of the constant offset between host and glasses vsync. The device clock runs about 65 ppm off the host's;
// over the one second windows used here that is under 70 us, and the period is left in device ns (0.5 us a frame).
//
// For one thread. No allocation, no system call.
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <optional>

namespace luma {
struct VsyncState {
    bool locked = false;                // period and anchor below are good
    double period_ns = 0;               // one refresh: 8.33e6 at 120 Hz
    std::uint64_t anchor_host_ns = 0;   // a scan-out, the newest event's, on CLOCK_MONOTONIC
    std::uint64_t anchor_steady_ns = 0; // the same moment on the pose feed's clock (PoseOut.t_ns)
    std::uint64_t events = 0;           // display events seen, all of them
    std::uint32_t resets = 0;           // times the line started again
    float fit_rms_us = 0;               // how far the stamps sit off the line; the 100 us rounding alone gives about 29
};

class VsyncClock {
public:
    static constexpr std::size_t kWindow = 120;      // events in the line: 1 s at 120 Hz, 2 s at 60
    static constexpr std::size_t kLockAfter = 8;     // events on one line before it is published as locked
    static constexpr std::uint64_t kFloorWindowNs = 500'000'000;  // the floors are the lowest of two of these
    static constexpr double kMinPeriodNs = 4e6, kMaxPeriodNs = 50e6;  // 20 to 250 Hz
    static constexpr double kFitTolerance = 0.15;    // of a period: an event further off the line starts it again
    static constexpr std::uint64_t kMaxSkipped = 4;  // missed events in a row the line bridges

    // A new connection or a new time base: the device clock may have started again, and the feed's clock moved.
    void reset();
    // Every accepted IMU sample: its device stamp, its time on the pose feed's clock, and when the host got it.
    void on_imu(std::uint64_t device_ns, std::uint64_t steady_ns, std::uint64_t host_ns);
    // Every display event. True when state() changed and should be published.
    bool on_event(std::uint64_t device_ns);
    const VsyncState& state() const { return state_; }

private:
    // The lowest value seen over the last one to two windows, by host time.
    struct Floor {
        std::int64_t current = 0, previous = 0;
        std::uint64_t started = 0;
        bool have_current = false, have_previous = false;
        void add(std::int64_t value, std::uint64_t host_ns);
        std::optional<std::int64_t> value() const;
    };
    struct Point { std::uint64_t frame; std::uint64_t device_ns; };
    void restart(std::uint64_t device_ns);
    void fit();

    Floor host_minus_device_, host_minus_steady_;
    std::array<Point, kWindow> points_{};
    std::size_t head_ = 0, count_ = 0;  // points_[head_] is the oldest
    std::uint64_t frame_ = 0, last_device_ns_ = 0;
    double guess_ns_ = 0;               // the period: from the fit, or the first interval
    double fitted_device_ns_ = 0;       // the newest event's place on the line
    VsyncState state_;
};

// The pose_vsync segment the publisher writes when display events are read (publish.hpp), 64 bytes of 32-bit words, native
// order, and spike/src/pose.cpp PoseFeed::vsync reads it:
//   0      seq: odd while being written
//   1      magic "LVS1"
//   2, 3   anchor_host_ns, CLOCK_MONOTONIC, low word first
//   4, 5   period_ns, a double
//   6, 7   anchor_feed_ms, a double: the anchor on the feed's own t (pose_orientation's ms), NaN before the first pose
//   8, 9   events
//   10     flags: 1 locked
//   11     resets
//   12     fit_rms_us, a float
//   13-15  zero
// A scan-out is anchor + k * period for a whole k. A reader takes it only when locked and its anchor is recent.
inline constexpr std::uint32_t kVsyncMagic = 0x3153564c;  // "LVS1" in memory
struct VsyncWire {
    std::atomic<std::uint32_t> seq{0};
    std::atomic<std::uint32_t> magic{0};
    std::array<std::atomic<std::uint32_t>, 14> words{};
};
static_assert(sizeof(VsyncWire) == 64 && std::atomic<std::uint32_t>::is_always_lock_free);
void write_vsync(VsyncWire& wire, const VsyncState& state, double anchor_feed_ms);
}
