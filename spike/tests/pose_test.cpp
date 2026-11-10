#include "fake_feed.hpp"
#include "xr/pose.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * 3.14159265f / 360.f, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}

// Synthetic SysV feed laid out like XRLinuxDriver (robust mutex; fake_feed.hpp).
using test::FakeFeed;

// A USB drop as the incident of 2026-11-04 00:57:58 had it, against a simulated 120 Hz renderer. The driver publishes nothing
// (disabled, then a zeroed feed) for about 20 s with two disconnects, a few fresh samples get through between them, and the
// feed comes back calibrated on a new reference: its timestamps restart and its frame is not the old one.
namespace drop {
constexpr double kFrameMs = 1000.0 / 120;
float view_change_deg(const Mat3& a, const Mat3& b) {  // the rotation between two views
    const float trace = a[0] * b[0] + a[3] * b[3] + a[6] * b[6] + a[1] * b[1] + a[4] * b[4] + a[7] * b[7] + a[2] * b[2] + a[5] * b[5] + a[8] * b[8];
    return std::acos(std::clamp((trace - 1.f) / 2.f, -1.f, 1.f)) * 180.f / 3.14159265f;
}
struct Sim {
    PoseState s;
    double now = 0;
    float device = 1;
    void live(Quat q, double ms) {  // fresh samples
        for (double end = now + ms; now < end; now += kFrameMs) { s.update(FeedSample{q, device}, now); device += 8; }
    }
    void silent(double ms) {  // the driver disabled, or its feed zeroed: PoseFeed::read gives nothing
        for (double end = now + ms; now < end; now += kFrameMs) s.update(std::nullopt, now);
    }
};
void run() {
    const Quat worn = quat_mul(axis_angle(0, 0, 1, 40), axis_angle(0, 1, 0, 12));  // the head before the drop, feed frame A
    const Quat new_frame = axis_angle(1, 0, 0, 70);                                  // the feed's reference after it: unrelated
    {   // Twenty seconds, two disconnects: the view is held, then carries on from exactly there, with no jump.
        Sim d;
        d.live(Quat{}, 500);          // the recentre reference
        d.live(worn, 2000);
        const Mat3 held = d.s.view();
        CHECK(d.s.tracking());
        d.silent(4000);               // disconnect 1
        CHECK(!d.s.tracking() && d.s.lost_events() == 1);
        CHECK(view_change_deg(d.s.view(), held) < 1e-3f);
        CHECK(d.s.reconnecting(d.now) && d.s.reanchors() == 0);
        d.device = 1;                 // the driver reconnected: its clock starts again
        d.live(new_frame, 100);       // a few samples between the disconnects are no recovery
        CHECK(!d.s.tracking());
        CHECK(view_change_deg(d.s.view(), held) < 1e-3f);
        d.silent(6000);               // disconnect 2
        d.silent(9900);
        CHECK(!d.s.tracking() && d.s.lost_events() == 1);
        d.device = 1;
        float worst = 0;
        for (int i = 0; i < 90; ++i) {  // calibrated again, the head still; it must not move the picture at any frame
            d.s.update(FeedSample{new_frame, d.device}, d.now);
            d.device += 8; d.now += drop::kFrameMs;
            worst = std::max(worst, view_change_deg(d.s.view(), held));
        }
        CHECK(d.s.tracking() && d.s.lost_events() == 1 && d.s.reanchors() == 1 && !d.s.reconnecting(d.now));
        CHECK(worst < 0.01f);
        // And it tracks again: ten degrees of head are ten degrees of view, the world anchored where it was left.
        d.live(quat_mul(new_frame, axis_angle(0, 0, 1, 10)), 100);
        CHECK(near(view_change_deg(d.s.view(), held), 10.f, 0.05f));
    }
    {   // The level the desktops are measured against survives the change of frame.
        Sim d;
        d.live(Quat{}, 500);
        d.live(axis_angle(0, 0, 1, 25), 2000);
        const auto before = canvas_up(*d.s.reference(), Quat{});
        d.silent(5000);
        d.device = 1;
        d.live(new_frame, 1000);
        const auto after = canvas_up(*d.s.reference(), Quat{});
        CHECK(near(before[0], after[0], 0.02f) && near(before[1], after[1], 0.02f) && near(before[2], after[2], 0.02f));
    }
    {   // A recentre pressed during the drop is kept: the current head is straight ahead once tracking is back.
        Sim d;
        d.live(Quat{}, 500);
        d.live(worn, 1000);
        d.silent(3000);
        d.s.recenter();
        d.silent(3000);
        d.device = 1;
        d.live(new_frame, 1000);
        CHECK(d.s.tracking());
        CHECK(view_change_deg(d.s.view(), {1, 0, 0, 0, 1, 0, 0, 0, 1}) < 1e-2f);
    }
    {   // A hiccup is no drop: the same feed, a fifth of a second late, is simply believed again, at once.
        Sim d;
        d.live(Quat{}, 500);
        d.silent(200);
        CHECK(!d.s.tracking() && !d.s.reconnecting(d.now));  // too short to mention
        const Quat turned = axis_angle(0, 0, 1, 5);
        d.s.update(FeedSample{turned, d.device}, d.now);
        CHECK(d.s.tracking());
        CHECK(near(view_change_deg(d.s.view(), {1, 0, 0, 0, 1, 0, 0, 0, 1}), 5.f, 0.05f));
    }
    {   // The hint waits a second, and a session that never had tracking has nothing to reconnect.
        PoseState never;
        never.update(std::nullopt, 60000);
        CHECK(!never.reconnecting(60000));
        Sim d;
        d.live(Quat{}, 500);
        const double last = d.now;
        d.silent(1000);
        CHECK(!d.s.reconnecting(d.now));
        d.silent(200);
        CHECK(d.s.reconnecting(d.now) && d.now - last > PoseState::kHintAfterMs);
    }
    {   // A feed that never comes back keeps the view, however long.
        Sim d;
        d.live(worn, 500);
        const Mat3 held = d.s.view();
        d.silent(90000);
        CHECK(!d.s.tracking() && view_change_deg(d.s.view(), held) < 1e-3f);
    }
}
}  // namespace drop

// Interop with the Rust port of the feed (rust/crates/xr-pose/tests/cpp_interop.rs drives these; plain pose_test runs
// the tests below). Floats are printed with 9 significant digits, which round-trips every float exactly.
//   pose_test --feed-read <prefix> <n>  PoseFeed::open, then n reads. Prints "fov <v|none>", "look_ahead <4 floats|none>",
//                                        then per read "sample <16 floats as read: q0 xyzw normalised, q1, q2, t0 t1 t2 0>"
//                                        or "none". "open-failed <why>" if the feed cannot be opened.
//   pose_test --feed-write <prefix>     A FakeFeed (the driver's layout and locking) at prefix, "ready" once it exists, then
//                                        one command per stdin line, each answered with one line:
//                                          write <16 floats>  the driver's publish                   -> ok
//                                          disabled <0|1>                                            -> ok
//                                          die-holding        a child process locks and exits        -> ok
//                                          probe              pthread_mutex_timedlock, 1 s, unlocked -> rc <errno or 0>
//                                        EOF or "quit" removes the feed.
namespace interop {
void print_floats(const char* tag, const float* v, int n) {
    std::printf("%s", tag);
    for (int i = 0; i < n; ++i) std::printf(" %.9g", double(v[i]));
    std::printf("\n");
}
int read(const std::string& prefix, int n) {
    std::string why;
    auto feed = PoseFeed::open(prefix, why);
    if (!feed) { std::printf("open-failed %s\n", why.c_str()); return 0; }
    if (auto fov = feed->display_fov()) std::printf("fov %.9g\n", double(*fov)); else std::printf("fov none\n");
    if (auto la = feed->look_ahead_cfg()) print_floats("look_ahead", la->data(), 4); else std::printf("look_ahead none\n");
    for (int i = 0; i < n; ++i) {
        const auto s = feed->read();
        if (!s) { std::printf("none\n"); continue; }
        const float v[16] = {s->orientation.x, s->orientation.y, s->orientation.z, s->orientation.w,
                             s->older[0].x, s->older[0].y, s->older[0].z, s->older[0].w,
                             s->older[1].x, s->older[1].y, s->older[1].z, s->older[1].w,
                             s->device_ms, s->older_ms[0], s->older_ms[1], 0};
        print_floats("sample", v, 16);
    }
    return 0;
}
int write(const std::string& prefix) {
    test::FakeFeed fake(prefix);
    std::printf("ready\n");
    std::fflush(stdout);
    std::string line;
    while (std::getline(std::cin, line) && line != "quit") {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "write") {
            float v[16];
            for (float& f : v) in >> f;
            fake.write(v);
            std::printf("ok\n");
        } else if (cmd == "disabled") {
            int on = 0;
            in >> on;
            *fake.disabled = on != 0;
            std::printf("ok\n");
        } else if (cmd == "die-holding") {
            const pid_t child = fork();
            if (child == 0) { pthread_mutex_lock(fake.mutex); _exit(0); }
            int status = 0;
            waitpid(child, &status, 0);
            std::printf("ok\n");
        } else if (cmd == "probe") {
            timespec deadline{};
            clock_gettime(CLOCK_REALTIME, &deadline);
            deadline.tv_sec += 1;
            const int rc = pthread_mutex_timedlock(fake.mutex, &deadline);
            if (rc == 0) pthread_mutex_unlock(fake.mutex);
            std::printf("rc %d\n", rc);
        } else {
            std::printf("unknown %s\n", cmd.c_str());
        }
        std::fflush(stdout);
    }
    return 0;
}
int run(int argc, char** argv) {
    const std::string mode = argv[1];
    if (mode == "--feed-read" && argc == 4) return read(argv[2], std::atoi(argv[3]));
    if (mode == "--feed-write" && argc == 3) return write(argv[2]);
    std::fprintf(stderr, "usage: pose_test [--feed-read <prefix> <n> | --feed-write <prefix>]\n");
    return 2;
}
}  // namespace interop

int main(int argc, char** argv) {
    if (argc > 1) return interop::run(argc, argv);
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
    // The driver dies holding its (robust) lock: the next reader gets EOWNERDEAD and owns it. The feed
    // makes it consistent and releases it, serving the last good sample for that frame, then reads on.
    // It must never keep the lock: that stalled every later frame 2 ms and locked out a restarted driver.
    {
        FakeFeed fake(prefix);
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        fake.write(axis_angle(0, 0, 1, 5), 100);
        CHECK(feed->read() && feed->read()->device_ms == 100);
        const pid_t child = fork();
        if (child == 0) { pthread_mutex_lock(fake.mutex); _exit(0); }
        int status = 0;
        CHECK(waitpid(child, &status, 0) == child);
        const auto recovered = feed->read();
        CHECK(recovered && recovered->device_ms == 100);
        for (int i = 0; i < 5; ++i) CHECK(feed->read() && feed->read()->device_ms == 100);
        int rc = -1;  // the driver (another thread here) can take the lock at once
        std::thread([&] { rc = pthread_mutex_trylock(fake.mutex); if (rc == 0) pthread_mutex_unlock(fake.mutex); }).join();
        CHECK(rc == 0);
        fake.write(axis_angle(0, 0, 1, 6), 101);
        CHECK(feed->read() && feed->read()->device_ms == 101);
    }
    // Not recoverable: a process died holding the lock and the driver (which ignores EOWNERDEAD) unlocked
    // it without making it consistent. A read gets no sample, and must leave the lock word free: another
    // locker gets ENOTRECOVERABLE at once, not a lock held by the reader that would hang the driver.
    {
        FakeFeed fake(prefix);
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        fake.write(axis_angle(0, 0, 1, 5), 300);
        const pid_t child = fork();
        if (child == 0) { pthread_mutex_lock(fake.mutex); _exit(0); }
        int status = 0;
        CHECK(waitpid(child, &status, 0) == child);
        int driver_rc = -1;
        std::thread([&] { driver_rc = pthread_mutex_lock(fake.mutex); pthread_mutex_unlock(fake.mutex); }).join();
        CHECK(driver_rc == EOWNERDEAD);
        for (int i = 0; i < 3; ++i) CHECK(!feed->read());
        int rc = -1;
        std::thread([&] {
            timespec deadline{};
            clock_gettime(CLOCK_REALTIME, &deadline);
            deadline.tv_sec += 1;
            rc = pthread_mutex_timedlock(fake.mutex, &deadline);
        }).join();
        CHECK(rc == ENOTRECOVERABLE);
    }
    // A live driver holding the lock: one bounded wait, then the newest sample is served again (same
    // device timestamp, so it ages and is predicted) instead of no sample (an unpredicted frame), and
    // later frames do not wait at all. A fresh sample is read once the lock is free again.
    {
        using namespace std::chrono_literals;
        FakeFeed fake(prefix);
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        fake.write(axis_angle(0, 0, 1, 5), 200);
        CHECK(feed->read() && feed->read()->device_ms == 200);
        std::atomic<bool> locked{false}, release{false};
        std::thread holder([&] {
            pthread_mutex_lock(fake.mutex); locked = true;
            while (!release) std::this_thread::sleep_for(1ms);
            pthread_mutex_unlock(fake.mutex);
        });
        while (!locked) std::this_thread::yield();
        const auto start = std::chrono::steady_clock::now();
        bool served = true;
        for (int i = 0; i < 10; ++i) { const auto s = feed->read(); served = served && s && s->device_ms == 200; }
        const auto waited = std::chrono::steady_clock::now() - start;
        release = true; holder.join();
        CHECK(served);
        CHECK(waited < 15ms);  // a 2 ms wait per read was 20 ms
        fake.write(axis_angle(0, 0, 1, 7), 201);
        CHECK(feed->read() && feed->read()->device_ms == 201);
        *fake.disabled = true; CHECK(!feed->read());  // disabled: nothing, not a held-over sample
        *fake.disabled = false;
        std::atomic<bool> again{false}; release = false;
        std::thread holder2([&] { pthread_mutex_lock(fake.mutex); again = true; while (!release) std::this_thread::sleep_for(1ms); pthread_mutex_unlock(fake.mutex); });
        while (!again) std::this_thread::yield();
        CHECK(!feed->read());  // the cache was cleared by the disabled read
        release = true; holder2.join();
    }
    // A driver publishing at ~1 kHz alongside: every read gets a sample (never a lock failure).
    {
        FakeFeed fake(prefix);
        auto feed = PoseFeed::open(prefix, why);
        CHECK(feed);
        fake.write(axis_angle(0, 0, 1, 1), 1);
        std::atomic<bool> stop{false};
        std::thread writer([&] {
            for (float ms = 2; !stop; ms += 1) { fake.write(axis_angle(0, 0, 1, ms * 0.01f), ms); std::this_thread::sleep_for(std::chrono::microseconds(900)); }
        });
        int got = 0;
        for (int i = 0; i < 300; ++i) { got += feed->read().has_value(); std::this_thread::sleep_for(std::chrono::microseconds(300)); }
        stop = true; writer.join();
        CHECK(got == 300);
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
        // Follow -> anchor pins the canvas where it is now, not at the old anchor position.
        const Quat here = m.update(far, t += 8);
        m.set_mode(ViewMode::anchor, t);
        const Quat away = axis_angle(0, 0, 1, 35);
        for (int i = 0; i < 60; ++i) CHECK(deg(m.update(away, t += 8), here) < 1e-3f);
        CHECK(m.settled());
        // 0DoF -> anchor likewise pins at the head pose at the moment of the switch.
        m.set_mode(ViewMode::locked, t);
        for (int i = 0; i < 60; ++i) m.update(away, t += 8);
        m.set_mode(ViewMode::anchor, t);
        for (int i = 0; i < 60; ++i) CHECK(deg(m.update(far, t += 8), away) < 1e-3f);
        // A recentre while anchored brings the canvas straight ahead.
        m.recenter();
        CHECK(deg(m.update(Quat{}, t += 8), Quat{}) < 1e-3f);
        m.set_mode(ViewMode::follow, t);
        for (int i = 0; i < 200; ++i) m.update(far, t += 8);
        m.recenter();
        m.set_mode(ViewMode::anchor, t);
        for (int i = 0; i < 60; ++i) m.update(far, t += 8);
        CHECK(deg(m.update(far, t += 8), Quat{}) < 1e-3f);
    }
    drop::run();
    std::cout << "pose math, feed and view modes passed\n";
}
