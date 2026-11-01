#include "xr/predict.hpp"
#include <cmath>
#include <cstdio>
#include <iostream>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <unistd.h>
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
        const char* names[] = {"pose_orientation", "pose_orientation_mutex", "disabled", "look_ahead_cfg"};
        const std::size_t sizes[] = {sizeof(float) * 16, sizeof(pthread_mutex_t), sizeof(bool), sizeof(float) * 4};
        int ids[4]; void* addr[4];
        for (int i = 0; i < 4; ++i) {
            const auto path = prefix + names[i];
            if (FILE* f = std::fopen(path.c_str(), "w")) std::fclose(f);
            ids[i] = shmget(ftok(path.c_str(), 0), sizes[i], 0600 | IPC_CREAT);
            CHECK(ids[i] >= 0);
            addr[i] = shmat(ids[i], nullptr, 0);
        }
        pthread_mutexattr_t attr; pthread_mutexattr_init(&attr); pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
        pthread_mutex_init(static_cast<pthread_mutex_t*>(addr[1]), &attr);
        *static_cast<bool*>(addr[2]) = false;
        const float cfg[4] = {10, 0.6f, 10, 40};
        for (int i = 0; i < 4; ++i) static_cast<float*>(addr[3])[i] = cfg[i];
        const auto h = yawing(0.2f, 5000.f, base);
        float* pose = static_cast<float*>(addr[0]);
        for (int i = 0; i < 3; ++i) {
            const Quat q = h.q[std::size_t(i)];
            pose[i * 4 + 0] = q.x; pose[i * 4 + 1] = q.y; pose[i * 4 + 2] = q.z; pose[i * 4 + 3] = q.w;
            pose[12 + i] = h.device_ms[std::size_t(i)];
        }
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
        feed.reset();
        for (int i = 0; i < 4; ++i) { shmdt(addr[i]); shmctl(ids[i], IPC_RMID, nullptr); std::remove((prefix + names[i]).c_str()); }
    }
    std::cout << "predict ok\n";
    return 0;
}
