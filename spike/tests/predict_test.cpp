#include "fake_feed.hpp"
#include "xr/predict.hpp"
#include <cmath>
#include <cstdio>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <utility>
#include <vector>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
namespace {
bool near(double a, double b, double eps = 1e-3) { return std::fabs(a - b) <= eps; }
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * 3.14159265f / 360.f, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}
float deg(Quat a, Quat b) { return quat_angle(a, b) * 180.f / 3.14159265f; }
// Constant yaw rate (deg/ms) about z, sampled like the driver: now, -16 ms, -32 ms.
PoseHistory yawing(float rate, float now_ms = 1000.f, Quat base = {}) {
    PoseHistory h;
    for (int i = 0; i < 3; ++i) {
        const float t = now_ms - 16.f * float(i);
        h.q[std::size_t(i)] = quat_mul(base, axis_angle(0, 0, 1, rate * t));
        h.device_ms[std::size_t(i)] = t;
    }
    return h;
}
}

int main() {
    // Driver config: Luma values validate; garbage does not.
    CHECK(look_ahead_from({10, 0.6f, 10, 40}).has_value());
    CHECK(!look_ahead_from({NAN, 0.6f, 10, 40}));
    CHECK(!look_ahead_from({10, 0.6f, 10, 0}));       // no cap: unbounded
    CHECK(!look_ahead_from({10, 9.f, 10, 40}));
    CHECK(!look_ahead_from({-1, 0.6f, 10, 40}));

    // Angular velocity: constant yaw of 0.2 deg/ms (200 deg/s), with a non-trivial base pose.
    const Quat base = axis_angle(0.6f, 0.8f, 0, 70);
    {
        const auto w = angular_velocity(yawing(0.2f, 1000.f));
        CHECK(w && near((*w)[2], 0.2 * 3.14159265 / 180, 1e-5) && near((*w)[0], 0, 1e-6) && near((*w)[1], 0, 1e-6));
    }
    // Extrapolation from a rotated base lands on the true future orientation.
    {
        const auto h = yawing(0.2f, 1000.f, base);
        const auto w = angular_velocity(h);
        CHECK(w);
        const Quat predicted = quat_mul(h.q[0], prediction_delta(*w, 20.f, 15.f));
        const Quat truth = quat_mul(base, axis_angle(0, 0, 1, 0.2f * 1020.f));
        CHECK(deg(predicted, truth) < 0.01f);
        CHECK(deg(predicted, h.q[0]) > 3.9f);          // 4 degrees ahead
    }
    // Bounded: a 2000 deg/s spike over 40 ms is clamped to max_deg.
    {
        const auto w = angular_velocity(yawing(2.f));
        CHECK(w && near(deg(prediction_delta(*w, 40.f, 15.f), Quat{}), 15.f, 0.01));
    }
    CHECK(deg(prediction_delta({0, 0, 0.01f}, 0.f, 15.f), Quat{}) < 1e-4f);
    CHECK(deg(prediction_delta({0, 0, 0.01f}, -5.f, 15.f), Quat{}) < 1e-4f);
    // At rest: no motion predicted.
    {
        const auto w = angular_velocity(yawing(0.f, 1000.f, base));
        CHECK(w && deg(prediction_delta(*w, 30.f, 15.f), Quat{}) < 1e-3f);
    }
    // Unusable timestamps: equal, backward, too far apart, non-finite.
    {
        auto h = yawing(0.2f); h.device_ms[1] = h.device_ms[0]; CHECK(!angular_velocity(h));
        h = yawing(0.2f); h.device_ms[1] = h.device_ms[0] + 16; CHECK(!angular_velocity(h));
        h = yawing(0.2f); h.device_ms[1] = h.device_ms[0] - 80; CHECK(!angular_velocity(h));
        h = yawing(0.2f); h.device_ms[0] = NAN; CHECK(!angular_velocity(h));
        h = yawing(0.2f); h.q[1] = {NAN, 0, 0, 1}; CHECK(!angular_velocity(h));
    }

    // Whole-ms timestamps: the Luma Ultra's (ts - ts0) * 1000 truncation reads a ms low about half the
    // time, so 16 ms spacings show up as 15/16/17. The samples are N IMU cycles apart either way, so
    // when the two gaps differ only by that, their mean is the spacing (half the error, often exact).
    {
        const double truth = 0.1 * 3.14159265 / 180;  // rad/ms: 100 deg/s, samples truly 16 ms apart
        auto rate = [&](float t0, float t1, float t2) {
            auto h = yawing(0.1f, 1000.f, base);  // orientations exactly 16 and 32 ms back
            h.device_ms = {t0, t1, t2};
            const auto w = angular_velocity(h);
            CHECK(w);
            return (*w)[2] / truth;  // yaw about the body z axis (base is a fixed left factor)
        };
        CHECK(near(rate(1000, 984, 968), 1.0, 1e-4));       // exact stamps: unchanged
        CHECK(near(rate(999, 984, 968), 16.0 / 15.5, 1e-4)); // t0 a ms low: was 16/15 (+6.7%), now +3.2%
        CHECK(near(rate(1000, 983, 968), 1.0, 1e-4));       // t1 a ms low: was 16/17 (-5.9%), now exact
        CHECK(near(rate(999, 984, 967), 1.0, 1e-4));        // t0 and t2 low: exact
        CHECK(near(rate(1000, 984, 965), 1.0, 1e-4));       // gaps 16 vs 19: a real mismatch, t0 - t1 alone
        CHECK(near(rate(1000, 984, NAN), 1.0, 1e-4));       // unusable t2: t0 - t1 alone
    }

    // Latency tracker: only a monotonic presentation clock is comparable.
    {
        LatencyTracker t;
        t.read(1, 1'000'000); t.presented(1, 9'000'000);
        CHECK(t.summary().count == 0 && t.summary().dropped == 1 && !t.expected_ms());
        t.clock(1);
        for (std::uint64_t i = 0; i < 20; ++i) { t.read(10 + i, i * 8'333'333); t.presented(10 + i, i * 8'333'333 + 12'000'000); }
        const auto s = t.summary();
        CHECK(s.count == 20 && near(s.mean_ms, 12.0) && near(s.p50_ms, 12.0, 0.25) && near(s.p95_ms, 12.0, 0.25) && near(s.max_ms, 12.0));
        CHECK(t.expected_ms() && near(*t.expected_ms(), 12.0, 0.01));
        // Discarded and unknown tickets count as dropped, never as measurements.
        t.read(100, 0); t.discarded(100); t.presented(999, 5);
        CHECK(t.summary().count == 20 && t.summary().dropped == 3);
        // Presented before read (clock mismatch) is dropped, not a negative latency.
        t.read(101, 50'000'000); t.presented(101, 40'000'000);
        CHECK(t.summary().count == 20 && t.summary().dropped == 4);
        // p95 reflects a slow tail.
        for (std::uint64_t i = 0; i < 20; ++i) { t.read(200 + i, 0); t.presented(200 + i, 30'000'000); }
        CHECK(t.summary().p95_ms > 29.0 && t.summary().max_ms > 29.9);
        t.read(300, 0); t.abandon(); t.presented(300, 1'000'000);
        CHECK(t.summary().count == 40);
        // A full pending table recycles the oldest entry instead of growing.
        for (std::uint64_t i = 0; i < 100; ++i) t.read(1000 + i, 0);
        t.presented(1099, 8'000'000);
        CHECK(t.summary().count == 41);
    }
    // Before enough samples the EMA is not trusted.
    { LatencyTracker t; t.clock(1); t.read(1, 0); t.presented(1, 10'000'000); CHECK(!t.expected_ms()); }

    // Pose age relative to the best observed transport.
    {
        PoseAge a;
        CHECK(a.observe(5000.0, 1000.f) && near(*a.observe(5000.0, 1000.f), 0));
        CHECK(near(*a.observe(5003.0, 1001.f), 2.0));   // 2 ms worse than the floor
        CHECK(near(*a.observe(5002.5, 1003.f), 0.0));   // new floor
        CHECK(near(*a.observe(5004.0, 1003.f), 1.5));   // same sample, read later: older
        CHECK(!a.observe(6000.0, 10.f));                  // device clock went backwards: reset
        CHECK(near(*a.observe(6000.0, 10.f), 0));
    }

    // Read phase on the refresh grid: a read later in the refresh period is presented sooner. Frame k
    // reads 1.0..3.5 ms after vblank k and is presented at vblank k + 2; its feedback arrives before the
    // read of frame k + 2. Once the per-phase estimate beats the average, it is exact for any phase.
    auto ns = [](double ms) { return std::uint64_t(std::llround(ms * 1e6)); };
    auto spread = [](int k, double step) { return std::fmod(double(k) * step, 1.0); };  // deterministic 0..1
    {
        LatencyTracker t; t.clock(1);
        const double P = 1000.0 / 120;
        int k = 0;
        for (; k < 300; ++k) {
            if (k >= 2) t.presented(std::uint64_t(k - 1), ns(k * P));  // frame k - 2 at vblank k
            t.read(std::uint64_t(k + 1), ns(k * P + 1.0 + 2.5 * spread(k, 0.618034)));
        }
        CHECK(t.phase_aware());
        for (double phase : {1.2, 2.0, 3.3}) {
            const double now = k * P + phase;
            CHECK(near(*t.expected_ms(ns(now)), (k + 2) * P - now, 0.05));
        }
        const double ema = *t.expected_ms(0);  // before the grid: always the average
        CHECK(std::fabs(*t.expected_ms(ns(k * P + 1.2)) - *t.expected_ms(ns(k * P + 3.3)) - 2.1) < 0.05 && ema > 13 && ema < 15.5);
        CHECK(near(*t.expected_ms(ns(k * P + 5000)), ema, 1e-9));  // grid too old to extend: the average
        // The refresh rate halves (120 -> 60 Hz): the grid is re-learnt, then the estimate is exact again.
        const double P2 = 2 * P, base_ms = (k + 1) * P;
        for (int j = 0; j < 300; ++j, ++k) {
            if (j >= 2) t.presented(std::uint64_t(k - 1), ns(base_ms + j * P2));
            t.read(std::uint64_t(k + 1), ns(base_ms + j * P2 + 1.0 + 2.5 * spread(j, 0.618034)));
        }
        CHECK(t.phase_aware());
        const double now = base_ms + 300 * P2 + 2.0;
        CHECK(near(*t.expected_ms(ns(now)), base_ms + 302 * P2 - now, 0.05));
    }
    // Compositor stalls (frames presented ~150 ms late) read at a phase not read otherwise must not
    // become that phase's estimate (two of them, so only the stall gate keeps them out of the bin):
    // reads there afterwards get their real latency, not ~150 ms.
    {
        LatencyTracker t; t.clock(1);
        const double P = 1000.0 / 120;
        std::vector<std::pair<std::uint64_t, double>> presents;
        for (int k = 0; k < 2000; ++k) {
            const bool stall = k == 600 || k == 900;
            double phase = 1.0 + spread(k, 0.618034);
            if (k % 97 == 50) phase = 4.6;  // occasional reads at a rarer phase
            if (stall) phase = 6.5;
            const double read = k * P + phase;
            for (auto it = presents.begin(); it != presents.end();)
                if (it->second <= read) { t.presented(it->first, ns(it->second)); it = presents.erase(it); } else ++it;
            t.read(std::uint64_t(k + 1), ns(read));
            presents.push_back({std::uint64_t(k + 1), (stall ? k + 20 : k + 2) * P});
        }
        CHECK(t.phase_aware());
        const int k = 2000;
        CHECK(near(*t.expected_ms(ns(k * P + 6.5)), 2 * P - 6.5, 0.05));  // from the nearest learnt phase
        CHECK(near(*t.expected_ms(ns(k * P + 4.6)), 2 * P - 4.6, 0.05));
        CHECK(near(*t.expected_ms(ns(k * P + 1.5)), 2 * P - 1.5, 0.05));
    }
    // No fixed grid (presented when ready, e.g. variable refresh): the per-phase estimate never beats
    // the average, so the average is used, as before.
    {
        LatencyTracker t; t.clock(1);
        std::vector<std::pair<std::uint64_t, double>> presents;
        double now = 0;
        for (int k = 0; k < 400; ++k) {
            const double read = k * 9.0 + 0.7 * spread(k, 0.414214);
            for (auto it = presents.begin(); it != presents.end();)
                if (it->second <= read) { t.presented(it->first, ns(it->second)); it = presents.erase(it); } else ++it;
            t.read(std::uint64_t(k + 1), ns(read));
            presents.push_back({std::uint64_t(k + 1), read + 6.0 + 5.0 * spread(k, 0.618034)});
            now = read;
        }
        CHECK(!t.phase_aware());
        CHECK(near(*t.expected_ms(ns(now + 2)), *t.expected_ms(0), 1e-9));
    }

    // Pose age floor over a sliding window: a device clock 50 ppm slow against the host must not
    // inflate the age (a global minimum grew it by 3 ms a minute, and with it the horizon).
    {
        PoseAge a;
        double worst = 0;
        for (int i = 0; i < 120 * 120; ++i) {  // two minutes at 120 Hz
            const double host = 10000 + i * 8.333;
            const float device = float(std::floor(1000 + i * 8.333 * (1 - 50e-6)));
            const auto age = a.observe(host, device);
            CHECK(age);
            if (i > 1200) worst = std::max(worst, *age);
        }
        CHECK(worst < 1.5);
        CHECK(a.summary().max_ms < 1.5);
        // The feed then freezes (the same sample read for 30 s): its age keeps growing, as before.
        const double frozen_host = 10000 + 120 * 120 * 8.333;
        const float frozen_device = float(std::floor(1000 + 120 * 120 * 8.333 * (1 - 50e-6)));
        double age = 0;
        for (int i = 1; i <= 120 * 30; ++i) age = *a.observe(frozen_host + i * 8.333, frozen_device);
        CHECK(age > 29900 && a.summary().max_ms > 29900);
    }

    // Horizon: measured + driver constant, capped, plus half the scanline adjustment.
    {
        const auto luma = look_ahead_from({10, 0.6f, 10, 40});
        CHECK(near(*prediction_horizon(luma, 9.0, 0.5, 8.33), 0.5 + 9 + 10 + 5));
        CHECK(near(*prediction_horizon(luma, 60.0, 0.5, 8.33), 40 + 5));      // capped
        CHECK(near(*prediction_horizon(luma, std::nullopt, 0, 8.33), 10 + 0.6 * 8.33 + 5, 1e-3));  // driver formula
        CHECK(!prediction_horizon(std::nullopt, 9.0, 0.5, 8.33));             // no driver bounds: no prediction
    }
    // Feed round trip: the driver's three samples and look-ahead config reach the predictor.
    {
        const std::string prefix = "/tmp/xr_predict_test_" + std::to_string(getpid()) + "_";
        test::FakeFeed fake(prefix);
        const auto h = yawing(0.2f, 5000.f, base);
        float v[16]{};
        for (int i = 0; i < 3; ++i) {
            const Quat q = h.q[std::size_t(i)];
            v[i * 4 + 0] = q.x; v[i * 4 + 1] = q.y; v[i * 4 + 2] = q.z; v[i * 4 + 3] = q.w;
            v[12 + i] = h.device_ms[std::size_t(i)];
        }
        fake.write(v);
        std::string why;
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        const auto sample = feed->read();
        CHECK(sample);
        const auto got = history_of(*sample);
        for (int i = 0; i < 3; ++i)
            CHECK(deg(got.q[std::size_t(i)], h.q[std::size_t(i)]) < 1e-3f && got.device_ms[std::size_t(i)] == h.device_ms[std::size_t(i)]);
        const auto w = angular_velocity(got);
        CHECK(w && near((*w)[2], 0.2 * 3.14159265 / 180, 1e-5));
        CHECK(feed->look_ahead_cfg() && look_ahead_from(*feed->look_ahead_cfg()));
    }
    std::cout << "predict ok\n";
    return 0;
}
