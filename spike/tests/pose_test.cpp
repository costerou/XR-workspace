#include "xr/pose.hpp"
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
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * 3.14159265f / 360.f, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}

// Synthetic SysV feed laid out like XRLinuxDriver (16 floats, process-shared mutex, disabled flag).
struct FakeFeed {
    std::string prefix; int pose_id = -1, mutex_id = -1, disabled_id = -1, fov_id = -1;
    float* pose = nullptr; float* fov = nullptr; pthread_mutex_t* mutex = nullptr; bool* disabled = nullptr;
    template <class T> T* make(const char* name, std::size_t size, int& id) {
        const auto path = prefix + name;
        if (FILE* f = std::fopen(path.c_str(), "w")) std::fclose(f);
        id = shmget(ftok(path.c_str(), 0), size, 0600 | IPC_CREAT);
        CHECK(id >= 0);
        return static_cast<T*>(shmat(id, nullptr, 0));
    }
    explicit FakeFeed(std::string p) : prefix(std::move(p)) {
        pose = make<float>("pose_orientation", sizeof(float) * 16, pose_id);
        mutex = make<pthread_mutex_t>("pose_orientation_mutex", sizeof(pthread_mutex_t), mutex_id);
        disabled = make<bool>("disabled", sizeof(bool), disabled_id);
        fov = make<float>("display_fov", sizeof(float), fov_id);
        *fov = 52.f;
        pthread_mutexattr_t attr; pthread_mutexattr_init(&attr); pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
        pthread_mutex_init(mutex, &attr);
        *disabled = false;
    }
    void write(Quat q, float ms) {
        pthread_mutex_lock(mutex);
        float v[16] = {q.x, q.y, q.z, q.w, q.x, q.y, q.z, q.w, q.x, q.y, q.z, q.w, ms, ms - 16, ms - 32, 0};
        for (int i = 0; i < 16; ++i) pose[i] = v[i];
        pthread_mutex_unlock(mutex);
    }
    ~FakeFeed() {
        for (int id : {pose_id, mutex_id, disabled_id, fov_id}) if (id >= 0) shmctl(id, IPC_RMID, nullptr);
        for (const char* n : {"pose_orientation", "pose_orientation_mutex", "disabled", "display_fov"}) std::remove((prefix + n).c_str());
    }
};

int main() {
    // Quaternion basics.
    auto q = axis_angle(0, 0, 1, 90);
    auto r = quat_mul(q, quat_conj(q));
    CHECK(near(r.w, 1) && near(r.x, 0) && near(r.z, 0));
    CHECK(!quat_normalized({NAN, 0, 0, 1}) && !quat_normalized({0, 0, 0, 0}));
    CHECK(near(quat_normalized({0, 0, 0, 2})->w, 1));

    // Identity view reproduces head-locked NDC exactly.
    const float hw = std::tan(23.f * 3.14159265f / 180.f), hh = hw * 1200.f / 1920.f;
    Mat3 id{1, 0, 0, 0, 1, 0, 0, 0, 1};
    for (auto [x, y] : {std::pair{0.f, 0.f}, {0.5f, -0.25f}, {-1.f, 1.f}}) {
        auto p = project(id, x, y, hw, hh);
        CHECK(p && near((*p)[0], x) && near((*p)[1], y));
    }
    // Driver yaw (about its z/up axis) to the left: world-anchored content moves right in view.
    PoseState s;
    s.update(FeedSample{{0, 0, 0, 1}, 1}, 0);                    // first sample becomes the centre
    CHECK(s.tracking() && s.samples() == 1);
    s.update(FeedSample{axis_angle(0, 0, 1, 10), 2}, 5);          // head turns 10 deg left (+z, driver frame)
    auto centre = project(s.view(), 0, 0, hw, hh);
    CHECK(centre && near((*centre)[0], std::tan(10.f * 3.14159265f / 180.f) / hw, 1e-3f) && near((*centre)[1], 0, 1e-3f));
    // Head pitches up (driver -y): content moves down.
    PoseState pitch; pitch.update(FeedSample{{0, 0, 0, 1}, 1}, 0);
    pitch.update(FeedSample{axis_angle(0, -1, 0, 8), 2}, 5);
    auto c2 = project(pitch.view(), 0, 0, hw, hh);
    CHECK(c2 && (*c2)[1] < -0.1f && near((*c2)[0], 0, 1e-3f));
    // Content behind the viewer is not drawn.
    PoseState back; back.update(FeedSample{{0, 0, 0, 1}, 1}, 0); back.update(FeedSample{axis_angle(0, 0, 1, 180), 2}, 5);
    CHECK(!project(back.view(), 0, 0, hw, hh));

    // Recenter: the current pose becomes the new centre.
    s.recenter(); s.update(FeedSample{axis_angle(0, 0, 1, 10), 3}, 10);
    auto recentred = project(s.view(), 0.2f, 0.1f, hw, hh);
    CHECK(recentred && near((*recentred)[0], 0.2f, 1e-3f) && near((*recentred)[1], 0.1f, 1e-3f));
    // Staleness: no new device timestamp for > stale_ms -> lost, last pose held, counted once.
    const auto held = s.view();
    s.update(FeedSample{axis_angle(0, 0, 1, 10), 3}, 50); CHECK(s.tracking());
    s.update(FeedSample{axis_angle(0, 0, 1, 10), 3}, 200); CHECK(!s.tracking() && s.lost_events() == 1 && s.view() == held);
    s.update(std::nullopt, 300); CHECK(!s.tracking() && s.lost_events() == 1);
    s.update(FeedSample{axis_angle(0, 0, 1, 10), 4}, 310); CHECK(s.tracking());
    s.update(FeedSample{{NAN, 0, 0, 1}, 5}, 320); CHECK(s.tracking() && s.view() == held);  // invalid sample ignored

    // Real SysV feed path with a private prefix.
    const std::string prefix = "/tmp/xr-pose-test-" + std::to_string(getpid()) + "-";
    std::string why;
    CHECK(!PoseFeed::open(prefix, why) && !why.empty());  // absent driver: no feed
    {
        FakeFeed fake(prefix);
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        fake.write(axis_angle(0, 0, 1, 30), 1234);
        auto sample = feed->read();
        CHECK(sample && near(sample->device_ms, 1234) && near(sample->orientation.z, std::sin(15.f * 3.14159265f / 180.f), 1e-5f));
        CHECK(feed->display_fov() && near(*feed->display_fov(), 52.f));
        *fake.fov = NAN; CHECK(!feed->display_fov());     // invalid FOV is never used
        *fake.fov = 500.f; CHECK(!feed->display_fov());
        *fake.disabled = true; CHECK(!feed->read());
        *fake.disabled = false; fake.write({NAN, 0, 0, 1}, 1235); CHECK(!feed->read());
    }
    // T2 viewing modes. deg(a, b): angle between orientations in degrees.
    auto deg = [](Quat a, Quat b) { return quat_angle(a, b) * 180.f / 3.14159265f; };
    {
        const Quat head = quat_mul(axis_angle(0, 0, 1, 30), axis_angle(1, 0, 0, 20));  // yaw 30, roll 20
        // level() drops roll, keeps where the forward axis points.
        const auto f1 = apply(quat_to_mat3(level(head)), {1, 0, 0}), f2 = apply(quat_to_mat3(head), {1, 0, 0});
        CHECK(near(f1[0], f2[0], 1e-4f) && near(f1[1], f2[1], 1e-4f) && near(f1[2], f2[2], 1e-4f));
        CHECK(near(apply(quat_to_mat3(level(head)), {0, 1, 0})[2], 0, 1e-4f));  // no roll: left axis stays horizontal
        // view_from(head, head) is identity (0DoF); view_from(head, identity) is the 3DoF view.
        const Mat3 id = view_from(head, head);
        for (int i = 0; i < 9; ++i) CHECK(near(id[i], (i % 4 == 0) ? 1.f : 0.f, 1e-5f));
        ModeController m;
        double t = 0;
        CHECK(deg(m.update(head, t), Quat{}) < 1e-3f);                 // anchor: world-locked
        m.set_mode(ViewMode::locked, t);
        const Quat mid = m.update(head, t += 150);                      // halfway: between, no jump
        CHECK(deg(mid, Quat{}) > 5 && deg(mid, head) > 5);
        CHECK(deg(m.update(head, t += 200), head) < 1e-3f && m.settled());  // 0DoF steady state is exact
        const Quat moved = axis_angle(0, 0, 1, -40);
        CHECK(deg(m.update(moved, t += 8), moved) < 1e-3f);           // rigidly locked, no lag
        m.set_mode(ViewMode::follow, t);                                 // starts where the canvas is
        CHECK(deg(m.update(moved, t += 1), moved) < 2.f);
        for (int i = 0; i < 200; ++i) m.update(moved, t += 8);
        CHECK(deg(m.update(moved, t += 8), moved) < 0.05f && m.settled());  // converges onto the head
        // No dead zone: even a 2 degree turn is followed (smoothly, all the way).
        const Quat small = axis_angle(0, 0, 1, -40 + 2);
        for (int i = 0; i < 200; ++i) m.update(small, t += 8);
        CHECK(deg(m.update(small, t += 8), small) < 0.05f);
        // A 30 degree yaw + 15 degree roll step: accelerates from rest, then decelerates into
        // centre; monotonic (critically damped: no overshoot or bounce), roll included.
        const Quat far = quat_mul(axis_angle(0, 0, 1, -10), axis_angle(1, 0, 0, 15));
        const Quat before = m.update(small, t += 8);
        const float start = deg(before, far);
        CHECK(deg(m.update(far, t += 8), before) < 0.2f);               // gentle start (accelerating)
        float last = start, speed_peak = 0, prev_step = 0, at_100 = 0, at_500 = 0;
        int peak_frame = -1;
        for (int i = 0; i < 150; ++i) {
            const float now = deg(m.update(far, t += 8), far);
            CHECK(now <= last + 1e-3f);                                 // never overshoots
            const float step = last - now;
            if (step > speed_peak) { speed_peak = step; peak_frame = i; }
            if (i == 11) at_100 = 1 - now / start;
            if (i == 61) at_500 = 1 - now / start;
            prev_step = step; last = now;
        }
        CHECK(peak_frame > 3 && peak_frame < 30);                        // speeds up first, then slows down
        CHECK(prev_step < speed_peak * 0.05f);                           // decelerates smoothly into centre
        CHECK(at_100 > 0.1f && at_100 < 0.35f && at_500 > 0.85f);         // responsive but smooth
        CHECK(last < 0.05f && m.settled());
        m.recenter();
        m.set_mode(ViewMode::anchor, t);
        for (int i = 0; i < 60; ++i) m.update(far, t += 8);
        CHECK(deg(m.update(far, t += 8), Quat{}) < 1e-3f);
    }
    std::cout << "pose math, feed and view modes passed\n";
}
