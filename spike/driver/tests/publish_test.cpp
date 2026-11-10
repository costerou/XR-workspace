// luma::Publisher against the real reader: spike/src/pose.cpp and predict.cpp are compiled into this test. Every feed lives
// under a private prefix, in segments this test created (private_segments, or FakeFeed), so the live IPC is never touched.
#include "fake_feed.hpp"
#include "luma/publish.hpp"
#include "xr/pose.hpp"
#include "xr/predict.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <pthread.h>
#include <stdexcept>
#include <string>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__FILE__ ":") + std::to_string(__LINE__) + ": " #x); } while (0)
using namespace luma;
namespace fs = std::filesystem;

static std::uint64_t g_allocations = 0;
void* operator new(std::size_t size) {
    ++g_allocations;
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

namespace {
constexpr std::uint64_t kMs = 1'000'000;

struct Scratch {
    std::string dir;
    Scratch() {
        std::string name = (fs::temp_directory_path() / "luma_publish_XXXXXX").string();
        CHECK(mkdtemp(name.data()));
        dir = name;
    }
    ~Scratch() { std::error_code ignored; fs::remove_all(dir, ignored); }
};

// A private feed: a new name (so new inodes, so new keys) whenever a key is taken by someone else's segment.
std::unique_ptr<Publisher> open_private(const Scratch& scratch, PublisherOptions options = {}) {
    options.private_segments = true;
    std::string why;
    for (int attempt = 0; attempt < 1024; ++attempt) {
        options.prefix = scratch.dir + "/" + std::to_string(attempt) + "_";
        auto publisher = Publisher::open(options, why);
        if (publisher) return publisher;
        if (why.find("key in use") == std::string::npos) throw std::runtime_error("open: " + why);
    }
    throw std::runtime_error("no free key");
}
// The prefix open_private settled on: the only attempt whose files exist.
std::string prefix_of(const Scratch& scratch) {
    for (const auto& entry : fs::directory_iterator(scratch.dir)) {
        const std::string path = entry.path().string(), tail = "pose_orientation";
        if (path.size() > tail.size() && path.compare(path.size() - tail.size(), tail.size(), tail) == 0)
            return path.substr(0, path.size() - tail.size());
    }
    throw std::runtime_error("no feed files");
}
int segment(const std::string& path) { return shmget(ftok(path.c_str(), 0), 0, 0); }
// The bytes as a reader would map them.
std::array<float, 16> raw_pose(const std::string& prefix) {
    const int id = segment(prefix + "pose_orientation");
    CHECK(id >= 0);
    void* addr = shmat(id, nullptr, SHM_RDONLY);
    CHECK(addr != reinterpret_cast<void*>(-1));
    std::array<float, 16> out{};
    std::memcpy(out.data(), addr, sizeof(float) * 16);
    shmdt(addr);
    return out;
}
struct Attached {  // the feed's mutex, attached separately: what another process holds
    pthread_mutex_t* mutex = nullptr;
    explicit Attached(const std::string& prefix) {
        const int id = segment(prefix + "pose_orientation_mutex");
        CHECK(id >= 0);
        void* addr = shmat(id, nullptr, 0);
        CHECK(addr != reinterpret_cast<void*>(-1));
        mutex = static_cast<pthread_mutex_t*>(addr);
    }
    ~Attached() { shmdt(mutex); }
};
void die_holding(pthread_mutex_t* mutex) {
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) { pthread_mutex_lock(mutex); _exit(0); }
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
}

Quatf distinct(int i) {  // a different unit quaternion per i, x != y != z != w
    const float a = 0.05f + 0.01f * float(i), s = std::sin(a), c = std::cos(a);
    return {c, s * 0.6f, s * 0.48f, s * 0.64f};
}
PoseOut pose(int i, std::uint64_t t0_ns = 0, std::uint64_t step_ns = kMs) {
    return {distinct(i), t0_ns + std::uint64_t(i) * step_ns, std::uint32_t(i), true};
}
bool same(xr::Quat a, Quatf b) {  // equal up to the reader's normalising
    constexpr float eps = 1e-5f;
    return std::fabs(a.x - b.x) < eps && std::fabs(a.y - b.y) < eps && std::fabs(a.z - b.z) < eps && std::fabs(a.w - b.w) < eps;
}
// What the driver itself does: the options that make the feed comparable with buffer.c.
PublisherOptions step(std::uint32_t n) {
    PublisherOptions o;
    o.history_step = n;
    o.instant_start = false;
    o.mount_pitch_deg = 0;
    o.fractional_ms = false;
    return o;
}
// Without private_segments, for feeds FakeFeed created: no live process can make these tests depend on what is running.
PublisherOptions shared(const std::string& prefix, const Scratch& scratch) {
    PublisherOptions o = step(1);
    o.prefix = prefix;
    o.proc_root = scratch.dir + "/no_proc";
    return o;
}
void fake_process(const Scratch& scratch, const std::string& stat_line) {
    const auto pid = stat_line.substr(0, stat_line.find(' '));
    const auto dir = fs::path(scratch.dir) / "proc" / pid;
    fs::create_directories(dir);
    std::ofstream(dir / "stat") << stat_line << "\n";
}

void feed_is_disabled_until_the_history_has_filled() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(2));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    CHECK(!feed->read());
    for (int i = 0; i < 4; ++i) { CHECK(!publisher->publish(pose(i))); CHECK(!feed->read()); }  // 2N samples: not yet
    CHECK(publisher->publish(pose(4)));                                                         // the driver's 2N + 1st
    CHECK(feed->read());
    CHECK(publisher->stats().written == 1);
}

// Byte for byte what src/buffer.c hands src/outputs.c, and the same seen through the reader.
void bytes_match_the_drivers_layout() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(2));
    const std::string prefix = prefix_of(scratch);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed);
    for (int i = 0; i < 12; ++i) {
        const bool written = publisher->publish(pose(i, 5 * kMs));
        CHECK(written == (i >= 4));
        if (!written) continue;
        const Quatf q0 = distinct(i), q1 = distinct(i - 2), q2 = distinct(i - 4);
        const std::array<float, 16> want{q0.x, q0.y, q0.z, q0.w, q1.x, q1.y, q1.z, q1.w, q2.x, q2.y, q2.z, q2.w,
                                         float(i), float(i - 2), float(i - 4), 0.f};
        const auto got = raw_pose(prefix);
        CHECK(std::memcmp(got.data(), want.data(), sizeof want) == 0);

        const auto sample = feed->read();
        CHECK(sample);
        CHECK(same(sample->orientation, q0) && same(sample->older[0], q1) && same(sample->older[1], q2));
        CHECK(sample->device_ms == float(i) && sample->older_ms[0] == float(i - 2) && sample->older_ms[1] == float(i - 4));
    }
}

void fov_and_look_ahead_read_back() {
    Scratch scratch;
    PublisherOptions options;
    auto luma_ultra = open_private(scratch, options);  // the defaults are the Luma Ultra's, src/devices/viture.c
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    CHECK(feed->display_fov() == 52.f);
    const auto cfg = feed->look_ahead_cfg();
    CHECK(cfg && *cfg == (std::array<float, 4>{10.f, 0.6f, 10.f, 40.f}));
    CHECK(xr::look_ahead_from(*cfg));

    Scratch other;
    options.display_fov_deg = 48.5f;
    options.look_ahead_cfg = {20, 0.6f, 10, 40};
    auto changed = open_private(other, options);
    auto feed2 = xr::PoseFeed::open(prefix_of(other), why);
    CHECK(feed2 && feed2->display_fov() == 48.5f && (*feed2->look_ahead_cfg())[0] == 20.f);
}

// The driver's truncation, and t as whole floats however long the host's clock has been running.
void timestamps_are_whole_ms_from_the_first_sample() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    const std::uint64_t t0 = 3'000'000'000ull * kMs + 123'456;  // 35 days of uptime, not on a ms boundary
    const std::uint64_t stamps[] = {t0, t0 + kMs - 1, t0 + 2 * kMs + 999'999};
    CHECK(!publisher->publish({distinct(0), stamps[0], 0, true}));
    CHECK(!publisher->publish({distinct(1), stamps[1], 1, true}));
    CHECK(publisher->publish({distinct(2), stamps[2], 2, true}));
    const auto v = raw_pose(prefix);
    CHECK(v[12] == 2.f && v[13] == 0.f && v[14] == 0.f);  // 2.999999 and 0.999999 ms both truncate
}

// viture.c restarts a second short of 2^24 ms; the older samples keep the old clock, and predict.cpp refuses that spacing.
void clock_restarts_before_float_loses_whole_ms() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    const std::uint64_t limit = (1ull << 24) - 1000;
    CHECK(!publisher->publish({distinct(0), 0, 0, true}));
    CHECK(!publisher->publish({distinct(1), (limit - 2) * kMs, 1, true}));
    CHECK(publisher->publish({distinct(2), (limit - 1) * kMs, 2, true}));
    CHECK(raw_pose(prefix)[12] == float(limit - 1));
    CHECK(publisher->publish({distinct(3), limit * kMs, 3, true}));
    auto v = raw_pose(prefix);
    CHECK(v[12] == 0.f && v[13] == float(limit - 1));
    CHECK(publisher->publish({distinct(4), (limit + 7) * kMs, 4, true}));
    v = raw_pose(prefix);
    CHECK(v[12] == 7.f && v[13] == 0.f);
}

// A device clock that restarted after a reconnect must not underflow into a huge t.
void clock_going_back_restarts_t() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    for (int i = 0; i < 3; ++i) publisher->publish(pose(i, 1000 * kMs));
    CHECK(raw_pose(prefix)[12] == 2.f);
    CHECK(publisher->publish({distinct(3), 5 * kMs, 3, true}));
    CHECK(raw_pose(prefix)[12] == 0.f);
    CHECK(publisher->publish({distinct(4), 8 * kMs, 4, true}));
    CHECK(raw_pose(prefix)[12] == 3.f);
    // A smaller step back that stays past the start of the clock restarts it too: t must not run on a clock that jumped.
    for (int ms = 20; ms < 23; ++ms) publisher->publish({distinct(ms), std::uint64_t(ms) * kMs, std::uint32_t(ms), true});
    CHECK(raw_pose(prefix)[12] == 17.f);
    CHECK(publisher->publish({distinct(5), 10 * kMs, 5, true}));
    CHECK(raw_pose(prefix)[12] == 0.f);
}

void uncalibrated_poses_never_reach_the_feed() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    const auto before = raw_pose(prefix);
    for (int i = 0; i < 10; ++i) { auto p = pose(i); p.calibrated = false; CHECK(!publisher->publish(p)); }
    const auto after = raw_pose(prefix);
    CHECK(std::memcmp(before.data(), after.data(), sizeof before) == 0);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed && !feed->read());
}

// The identity swing a reconnect would otherwise cause: PoseState takes any new t as head motion.
void reader_sees_nothing_between_a_pause_and_real_samples() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    int i = 0;
    for (; i < 5; ++i) publisher->publish(pose(i));
    CHECK(feed->read());
    publisher->pause();
    CHECK(!feed->read());
    // History starts over: a sample from before the gap is never served as an older one.
    i += 500;
    CHECK(!publisher->publish(pose(i)));
    CHECK(!feed->read());
    CHECK(!publisher->publish(pose(i + 1)));
    CHECK(!feed->read());
    CHECK(publisher->publish(pose(i + 2)));
    const auto sample = feed->read();
    CHECK(sample && same(sample->orientation, distinct(i + 2)) && same(sample->older[1], distinct(i)));
    CHECK(sample->device_ms == float(i + 2));  // t's clock ran on through the gap
}

void losing_calibration_pauses_the_feed() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    for (int i = 0; i < 5; ++i) publisher->publish(pose(i));
    CHECK(feed->read());
    auto lost = pose(5); lost.calibrated = false;
    CHECK(!publisher->publish(lost));
    CHECK(!feed->read());
    CHECK(!publisher->publish(pose(6)) && !publisher->publish(pose(7)));
    CHECK(publisher->publish(pose(8)));
    CHECK(feed->read());
}

void invalid_quaternions_are_dropped() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    for (int i = 0; i < 3; ++i) publisher->publish(pose(i));
    const auto good = raw_pose(prefix);
    const float nan = std::nanf(""), inf = INFINITY;
    for (Quatf q : {Quatf{nan, 0, 0, 0}, Quatf{1, nan, 0, 0}, Quatf{1, 0, inf, 0}, Quatf{0, 0, 0, 0}})
        CHECK(!publisher->publish({q, 10 * kMs, 3, true}));
    CHECK(publisher->stats().invalid == 4);
    const auto after = raw_pose(prefix);
    CHECK(std::memcmp(good.data(), after.data(), sizeof good) == 0);
    // ... and not kept in the history either.
    CHECK(publisher->publish(pose(11)));
    const auto next = raw_pose(prefix);
    CHECK(next[13] == 2.f && next[14] == 1.f);
}

// All the way into the renderer's maths at the Luma Ultra's rate: 90 deg/s of yaw.
void prediction_and_tracking_work_on_the_feed() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(16));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    constexpr float kRadPerMs = 3.14159265f / 2000.f;
    xr::PoseState state;
    double spacing_checked = 0;
    for (int ms = 0; ms < 200; ++ms) {
        const float half = kRadPerMs * float(ms) / 2;
        if (!publisher->publish({{std::cos(half), 0, 0, std::sin(half)}, std::uint64_t(ms) * kMs, std::uint32_t(ms), true})) {
            CHECK(ms < 32);
            continue;
        }
        const auto sample = feed->read();
        state.update(sample, ms);
        CHECK(sample && sample->device_ms - sample->older_ms[0] == 16.f);
        const auto omega = xr::angular_velocity(xr::history_of(*sample));
        CHECK(omega);
        CHECK(std::fabs((*omega)[2] - kRadPerMs) < 1e-4f && std::fabs((*omega)[0]) < 1e-5f && std::fabs((*omega)[1]) < 1e-5f);
        spacing_checked += 1;
    }
    CHECK(spacing_checked == 168);
    CHECK(state.tracking() && state.samples() == 168);
    const auto r = state.relative();  // 167 ms of turn since the centre, about +z
    CHECK(std::fabs(2 * std::atan2(r.z, r.w) - kRadPerMs * 167) < 1e-3f);
}

// The renderer attached first, and stays attached across a driver restart: nothing is removed and the same segments are
// used. FakeFeed created them the way XRLinuxDriver does, and removes them itself.
void restart_keeps_an_attached_reader() {
    Scratch scratch;
    const std::string prefix = scratch.dir + "/shared_";
    xr::test::FakeFeed driver(prefix, 40.f, {1, 1, 1, 1});
    const int id = segment(prefix + "pose_orientation");
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed);
    const PublisherOptions options = shared(prefix, scratch);
    {
        auto first = Publisher::open(options, why);
        CHECK(first);
        CHECK(*driver.disabled);
        CHECK(feed->display_fov() == 52.f);  // written over the old driver's
        for (int i = 0; i < 3; ++i) first->publish(pose(i));
        CHECK(feed->read());
    }
    CHECK(*driver.disabled);
    CHECK(!feed->read());
    CHECK(segment(prefix + "pose_orientation") == id);
    auto second = Publisher::open(options, why);
    CHECK(second);
    for (int i = 10; i < 13; ++i) second->publish(pose(i));
    const auto sample = feed->read();
    CHECK(sample && same(sample->orientation, distinct(12)));
    CHECK(segment(prefix + "pose_orientation") == id);
}

// A stale segment of another size: src/ipc.c deletes it and then gives up, so it needs a second run. We go on.
void undersized_segment_is_replaced() {
    Scratch scratch;
    const std::string prefix = scratch.dir + "/small_";
    xr::test::FakeFeed driver(prefix);  // every key is this test's; then one segment is swapped for a short one
    const std::string path = prefix + "pose_orientation";
    CHECK(shmctl(segment(path), IPC_RMID, nullptr) == 0);
    const int small = shmget(ftok(path.c_str(), 0), 8, 0600 | IPC_CREAT | IPC_EXCL);
    CHECK(small >= 0);
    std::string why;
    auto publisher = Publisher::open(shared(prefix, scratch), why);
    const int id = segment(path);
    shmid_ds info{};
    const bool replaced = publisher && id >= 0 && id != small && shmctl(id, IPC_STAT, &info) == 0 && info.shm_segsz >= sizeof(float) * 16;
    publisher.reset();
    if (id >= 0) shmctl(id, IPC_RMID, nullptr);  // FakeFeed knows only the one it made
    CHECK(replaced);
}

// The safety of the tests themselves: a taken key is refused, not reused, and nothing in it changes.
void private_open_refuses_a_key_in_use() {
    Scratch scratch;
    const std::string prefix = scratch.dir + "/taken_";
    xr::test::FakeFeed live(prefix, 52.f);
    live.write(xr::Quat{0, 0, 0, 1}, 1234.f);
    *live.disabled = false;
    PublisherOptions options;
    options.prefix = prefix;
    options.private_segments = true;
    std::string why;
    CHECK(!Publisher::open(options, why));
    CHECK(why.find("key in use") != std::string::npos);
    CHECK(live.pose[12] == 1234.f && !*live.disabled);
    CHECK(pthread_mutex_lock(live.mutex) == 0);  // still a working mutex, not re-initialised under its owner
    CHECK(pthread_mutex_unlock(live.mutex) == 0);
}

void private_feed_cleans_up() {
    Scratch scratch;
    {
        auto publisher = open_private(scratch);
        const std::string prefix = prefix_of(scratch);
        CHECK(segment(prefix + "pose_orientation") >= 0);
        publisher.reset();
        CHECK(segment(prefix + "pose_orientation") < 0 && segment(prefix + "disabled") < 0);
    }
    CHECK(fs::is_empty(scratch.dir));
}

void bad_options_are_refused() {
    PublisherOptions options;
    options.history_step = 0;
    std::string why;
    CHECK(!Publisher::open(options, why));
    CHECK(!why.empty());
    CHECK(PublisherOptions{}.prefix == "/tmp/shader_runtime_");  // what spike/src/main.cpp reads; never opened here
}

void reader_that_died_holding_the_lock_costs_nothing() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    Attached lock(prefix);
    die_holding(lock.mutex);
    int i = 0;
    for (; i < 3; ++i) publisher->publish(pose(i));
    CHECK(publisher->stats().written == 1);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed && feed->read());
    CHECK(publisher->publish(pose(i)));
}

// EOWNERDEAD unlocked without pthread_mutex_consistent: every lock now fails, for good, unless the writer starts it over.
void unrecoverable_lock_is_rebuilt() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    for (int i = 0; i < 3; ++i) publisher->publish(pose(i));
    Attached lock(prefix);
    die_holding(lock.mutex);
    CHECK(pthread_mutex_lock(lock.mutex) == EOWNERDEAD);
    CHECK(pthread_mutex_unlock(lock.mutex) == 0);
    CHECK(pthread_mutex_lock(lock.mutex) == ENOTRECOVERABLE);
    CHECK(publisher->publish(pose(3)));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed && feed->read());
}

// A reader stopped under the debugger holds the lock for as long as it likes. A 1 kHz writer must not notice: a wait of any
// length per sample is what would back up the USB read loop.
void held_lock_never_slows_the_writer() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    const std::string prefix = prefix_of(scratch);
    for (int i = 0; i < 3; ++i) publisher->publish(pose(i));
    Attached lock(prefix);
    std::atomic<bool> held{false}, release{false};
    std::thread holder([&] {
        pthread_mutex_lock(lock.mutex);
        held = true;
        while (!release) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        pthread_mutex_unlock(lock.mutex);
    });
    while (!held) std::this_thread::yield();
    constexpr int kSkipped = 200;
    const auto start = std::chrono::steady_clock::now();
    int written = 0;
    for (int i = 0; i < kSkipped; ++i) written += publisher->publish(pose(3 + i));
    const auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    release = true;
    holder.join();
    CHECK(written == 0);
    CHECK(ms < kSkipped * 0.5);  // far under the 1 ms a sample has; a 1 ms wait each would take 200
    CHECK(publisher->stats().busy == kSkipped);
    CHECK(publisher->publish(pose(3 + kSkipped)));
    const auto v = raw_pose(prefix);
    // The skipped samples stayed in the history: a busy lock does not disturb the reader's spacing.
    CHECK(v[12] == float(3 + kSkipped) && v[13] == float(2 + kSkipped) && v[14] == float(1 + kSkipped));
}

void reader_never_tears_under_a_full_rate_writer() {
    Scratch scratch;
    auto publisher = open_private(scratch, step(1));
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    std::atomic<bool> done{false};
    std::thread writer([&] {
        for (int i = 0; i < 200000; ++i) publisher->publish(pose(i % 1000));
        done = true;
    });
    int reads = 0, torn = 0;
    while (!done) {
        const auto sample = feed->read();
        if (!sample) continue;
        ++reads;
        // t says which sample this is; its quaternion must be that sample's.
        if (sample->device_ms >= 0 && !same(sample->orientation, distinct(int(sample->device_ms)))) ++torn;
    }
    writer.join();
    CHECK(reads > 0);
    CHECK(torn == 0);
}

// A reconnect must not stay blind for 2N samples, nor predict from a speed measured over a couple of ms.
void instant_start_serves_the_first_sample_without_prediction() {
    Scratch scratch;
    PublisherOptions options = step(4);
    options.instant_start = true;
    auto publisher = open_private(scratch, options);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    CHECK(!feed->read());
    for (int i = 0; i < 4; ++i) {  // no sample N back yet
        CHECK(publisher->publish(pose(i)));
        const auto sample = feed->read();
        CHECK(sample && same(sample->orientation, distinct(i)) && same(sample->older[0], distinct(i)) && same(sample->older[1], distinct(i)));
        CHECK(sample->device_ms == float(i) && sample->older_ms[0] == float(i) && sample->older_ms[1] == float(i));
        CHECK(!xr::angular_velocity(xr::history_of(*sample)));
    }
    CHECK(publisher->publish(pose(4)));  // q1 is real, and N ms is a spacing predict.cpp takes; q2 is still q1
    auto sample = feed->read();
    CHECK(sample && same(sample->older[0], distinct(0)) && same(sample->older[1], distinct(0)));
    CHECK(sample->older_ms[0] == 0.f && sample->older_ms[1] == 0.f);
    CHECK(xr::angular_velocity(xr::history_of(*sample)));
    for (int i = 5; i < 8; ++i) CHECK(publisher->publish(pose(i)));
    sample = feed->read();
    CHECK(sample && same(sample->older[0], distinct(3)) && same(sample->older[1], distinct(3)));
    CHECK(publisher->publish(pose(8)));  // from here on, the driver's write
    sample = feed->read();
    CHECK(sample && same(sample->older[0], distinct(4)) && same(sample->older[1], distinct(0)));
}

void instant_start_is_the_default_and_recovers_at_once_after_a_pause() {
    Scratch scratch;
    CHECK(PublisherOptions{}.instant_start);
    PublisherOptions options = step(16);
    options.instant_start = true;
    auto publisher = open_private(scratch, options);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix_of(scratch), why);
    CHECK(feed);
    for (int i = 0; i < 100; ++i) CHECK(publisher->publish(pose(i)));
    publisher->pause();
    CHECK(!feed->read());
    CHECK(publisher->publish(pose(600)));
    const auto sample = feed->read();
    CHECK(sample && sample->device_ms == 600.f && same(sample->older[1], distinct(600)));  // nothing from before the gap
}

void mount_pitch_is_turned_out_on_the_right() {
    Scratch scratch;
    PublisherOptions options = step(1);
    options.mount_pitch_deg = -8.5f;
    auto publisher = open_private(scratch, options);
    const std::string prefix = prefix_of(scratch);
    CHECK(PublisherOptions{}.mount_pitch_deg == -8.5f);
    for (int i = 0; i < 3; ++i) publisher->publish(pose(i));
    const float half = -8.5f * 3.14159265f / 360.f;
    const xr::Quat turned = xr::quat_mul({distinct(2).x, distinct(2).y, distinct(2).z, distinct(2).w}, {0, std::sin(half), 0, std::cos(half)});
    const auto v = raw_pose(prefix);
    CHECK(std::fabs(v[0] - turned.x) < 1e-6f && std::fabs(v[1] - turned.y) < 1e-6f && std::fabs(v[2] - turned.z) < 1e-6f && std::fabs(v[3] - turned.w) < 1e-6f);
    const Quatf q1 = distinct(1);  // the older samples too, or the speed would hold the tilt
    const xr::Quat older = xr::quat_mul({q1.x, q1.y, q1.z, q1.w}, {0, std::sin(half), 0, std::cos(half)});
    CHECK(std::fabs(v[4] - older.x) < 1e-6f && std::fabs(v[7] - older.w) < 1e-6f);
}

void fractional_ms_is_the_default_and_keeps_the_fraction() {
    CHECK(PublisherOptions{}.fractional_ms);
    Scratch scratch;
    PublisherOptions options = step(1);
    options.fractional_ms = true;
    auto publisher = open_private(scratch, options);
    const std::string prefix = prefix_of(scratch);
    const std::uint64_t t0 = 3'000'000'000ull * kMs + 123'456;
    publisher->publish({distinct(0), t0, 0, true});
    publisher->publish({distinct(1), t0 + 1'250'000, 1, true});
    CHECK(publisher->publish({distinct(2), t0 + 2'500'000, 2, true}));
    const auto v = raw_pose(prefix);
    CHECK(v[12] == 2.5f && v[13] == 1.25f && v[14] == 0.f);
}

void history_step_is_the_drivers() {
    CHECK(history_step_for(1000) == 16 && history_step_for(500) == 8 && history_step_for(240) == 4);
    CHECK(history_step_for(120) == 2 && history_step_for(90) == 1 && history_step_for(60) == 1 && history_step_for(30) == 1);
}

void second_publisher_is_refused_until_the_first_is_gone() {
    Scratch scratch;
    const std::string prefix = scratch.dir + "/shared_";
    xr::test::FakeFeed driver(prefix);
    const auto options = shared(prefix, scratch);
    std::string why;
    auto first = Publisher::open(options, why);
    CHECK(first);
    CHECK(!Publisher::open(options, why));
    CHECK(why.find("another publisher") != std::string::npos);
    for (int i = 0; i < 3; ++i) first->publish(pose(i));
    CHECK(driver.pose[12] == 2.f);  // the refused one touched nothing
    first.reset();
    CHECK(Publisher::open(options, why));
    // A crash lets go as well: the lock belongs to the process.
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) { std::string ignored; auto held = Publisher::open(options, ignored); _exit(held ? 0 : 1); }
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(Publisher::open(options, why));
}

void running_xrdriver_is_refused_and_a_dead_one_is_not() {
    Scratch scratch;
    const std::string prefix = scratch.dir + "/shared_";
    xr::test::FakeFeed driver(prefix);
    driver.write(xr::Quat{0, 0, 0, 1}, 77.f);
    *driver.disabled = false;
    PublisherOptions options = shared(prefix, scratch);
    options.proc_root = scratch.dir + "/proc";
    fake_process(scratch, "100 (bash) S 1 100 100 0 -1");
    fake_process(scratch, "200 ((sd-pam)) S 1 200 200 0 -1");
    fake_process(scratch, "300 (xrDriver) Z 1 300 300 0 -1");  // the defunct one a parent has not reaped
    std::string why;
    CHECK(Publisher::open(options, why));
    fake_process(scratch, "400 (xrDriver) S 1 400 400 0 -1");
    *driver.disabled = false;
    driver.write(xr::Quat{0, 0, 0, 1}, 77.f);
    CHECK(!Publisher::open(options, why));
    CHECK(why.find("xrDriver is running") != std::string::npos);
    CHECK(driver.pose[12] == 77.f && !*driver.disabled);  // its feed and its mutex untouched
    CHECK(pthread_mutex_lock(driver.mutex) == 0);
    CHECK(pthread_mutex_unlock(driver.mutex) == 0);
}

void private_feeds_skip_the_writer_checks() {  // a test feed is not the live writer's business
    Scratch scratch;
    auto a = open_private(scratch);
    Scratch other;
    PublisherOptions options;
    options.proc_root = scratch.dir + "/proc";
    fake_process(scratch, "400 (xrDriver) S 1 400 400 0 -1");
    auto b = open_private(other, options);
    CHECK(b);
    CHECK(!fs::exists(prefix_of(other) + "writer.lock"));
}

void publish_does_not_allocate() {
    Scratch scratch;
    auto publisher = open_private(scratch);
    for (int i = 0; i < 100; ++i) publisher->publish(pose(i));
    const std::uint64_t before = g_allocations;
    for (int i = 100; i < 10000; ++i) CHECK(publisher->publish(pose(i)));
    publisher->pause();
    for (int i = 10000; i < 10100; ++i) CHECK(publisher->publish(pose(i)));
    auto lost = pose(10100); lost.calibrated = false;
    publisher->publish(lost);
    CHECK(g_allocations == before);
}
}

// pose_vsync (rust-rebuild-plan D5): what the driver writes is what the renderer reads, the anchor lands on the feed's own ms
// clock, and anything not good to use reads as nothing.
VsyncState locked_vsync(std::uint64_t steady_ns) {
    VsyncState v;
    v.locked = true; v.period_ns = 1e9 / 120; v.anchor_host_ns = 7'000'000'123'456ull; v.anchor_steady_ns = steady_ns;
    v.events = 5'000'000'000ull; v.resets = 3; v.fit_rms_us = 27.5f;
    return v;
}
void vsync_segment_round_trips() {
    Scratch scratch;
    PublisherOptions options;
    options.vsync = true;
    auto publisher = open_private(scratch, options);
    const std::string prefix = prefix_of(scratch);
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed);
    CHECK(!feed->vsync());  // made unlocked
    publisher->publish_vsync(locked_vsync(1'000 * kMs));
    auto early = feed->vsync();
    CHECK(early && std::isnan(early->anchor_feed_ms));  // no pose yet, so no feed clock
    for (int i = 0; i < 40; ++i) publisher->publish(pose(i, 1'000 * kMs));
    const auto sample = feed->read();
    CHECK(sample);
    publisher->publish_vsync(locked_vsync(1'000 * kMs + 39 * kMs + 3'500'000));
    const auto v = feed->vsync();
    CHECK(v);
    CHECK(v->anchor_host_ns == 7'000'000'123'456ull && v->period_ns == 1e9 / 120 && v->events == 5'000'000'000ull);
    CHECK(v->resets == 3 && v->fit_rms_us == 27.5f);
    CHECK(std::fabs(v->anchor_feed_ms - (double(sample->device_ms) + 3.5)) < 1e-3);
    VsyncState unlocked = locked_vsync(0);
    unlocked.locked = false;
    publisher->publish_vsync(unlocked);
    CHECK(!feed->vsync());
    VsyncState wild = locked_vsync(0);
    wild.period_ns = 1e9;  // 1 Hz: no display
    publisher->publish_vsync(wild);
    CHECK(!feed->vsync());
    publisher->publish_vsync(locked_vsync(0));
    publisher.reset();  // a writer that goes leaves it unlocked
    CHECK(!feed->vsync());
}
void vsync_segment_is_only_made_when_asked() {
    Scratch scratch;
    auto publisher = open_private(scratch);
    const std::string prefix = prefix_of(scratch);
    CHECK(!fs::exists(prefix + "pose_vsync"));
    publisher->publish_vsync(locked_vsync(0));  // nowhere to go, and harmless
    std::string why;
    auto feed = xr::PoseFeed::open(prefix, why);
    CHECK(feed && !feed->vsync());
}
// The renderer's score against wp_presentation: a constant offset is reported, not counted, also across the half-period
// wrap; scan-out minus drawn sample is measured on the feed's clock; stale and missing states are skipped.
void vsync_meter_scores_presents() {
    const double period = 1e9 / 120;
    for (const double offset_ms : {0.3, 4.1, -4.1}) {
        xr::VsyncMeter meter;
        const xr::VsyncSnapshot snap{1'000'000'000ull, period, 1000.0, 1, 0, 27};
        for (int k = -20; k < 20; ++k) {
            const double jitter_ms = ((k + 100) % 5) * 0.05 - 0.1;  // -0.1 to +0.1
            const double at = 1e9 + k * period + (offset_ms + jitter_ms) * 1e6;
            const float sample_ms = float(1000.0 + k * period / 1e6 - 12.0);
            meter.presented(snap, std::uint64_t(at), sample_ms);
        }
        const xr::VsyncSummary s = meter.summary();
        CHECK(s.count == 40 && s.skipped == 0);
        CHECK(std::fabs(s.offset_ms - offset_ms) < 0.02);
        CHECK(s.error_max_ms <= 0.12 && s.error_p95_ms <= 0.12);
        CHECK(s.sensor_count == 40);
        // Against the scan-out nearest the present, so a phase near half a period may pick the one either side.
        if (offset_ms == 0.3) CHECK(std::fabs(s.sensor_p50_ms - 12.0) < 1e-3 && std::fabs(s.sensor_max_ms - 12.0) < 1e-3);
        CHECK(std::fabs(s.period_ms - period / 1e6) < 1e-9);
    }
    xr::VsyncMeter meter;
    meter.presented(std::nullopt, 1'000'000'000ull, 5);
    meter.presented(xr::VsyncSnapshot{1'000'000'000ull, period, NAN, 1, 0, 27}, 1'300'000'000ull, 5);  // 300 ms old
    meter.presented(xr::VsyncSnapshot{1'000'000'000ull, period, NAN, 1, 0, 27}, 1'000'100'000ull, 5);  // no feed clock
    const xr::VsyncSummary s = meter.summary();
    CHECK(s.count == 1 && s.skipped == 2 && s.sensor_count == 0);
}

int main() {
    const std::vector<std::pair<const char*, void (*)()>> tests{
#define T(name) {#name, name}
        T(feed_is_disabled_until_the_history_has_filled), T(bytes_match_the_drivers_layout), T(fov_and_look_ahead_read_back),
        T(timestamps_are_whole_ms_from_the_first_sample), T(clock_restarts_before_float_loses_whole_ms),
        T(clock_going_back_restarts_t), T(uncalibrated_poses_never_reach_the_feed),
        T(reader_sees_nothing_between_a_pause_and_real_samples), T(losing_calibration_pauses_the_feed),
        T(invalid_quaternions_are_dropped), T(prediction_and_tracking_work_on_the_feed), T(restart_keeps_an_attached_reader),
        T(undersized_segment_is_replaced), T(private_open_refuses_a_key_in_use), T(private_feed_cleans_up),
        T(bad_options_are_refused), T(reader_that_died_holding_the_lock_costs_nothing), T(unrecoverable_lock_is_rebuilt),
        T(held_lock_never_slows_the_writer), T(reader_never_tears_under_a_full_rate_writer),
        T(instant_start_serves_the_first_sample_without_prediction), T(instant_start_is_the_default_and_recovers_at_once_after_a_pause),
        T(mount_pitch_is_turned_out_on_the_right), T(fractional_ms_is_the_default_and_keeps_the_fraction),
        T(history_step_is_the_drivers), T(second_publisher_is_refused_until_the_first_is_gone),
        T(running_xrdriver_is_refused_and_a_dead_one_is_not), T(private_feeds_skip_the_writer_checks), T(publish_does_not_allocate),
        T(vsync_segment_round_trips), T(vsync_segment_is_only_made_when_asked), T(vsync_meter_scores_presents),
#undef T
    };
    int failed = 0;
    for (const auto& [name, run] : tests) {
        try { run(); std::cout << "ok   " << name << "\n"; }
        catch (const std::exception& e) { std::cout << "FAIL " << name << ": " << e.what() << "\n"; ++failed; }
    }
    if (failed) { std::cout << failed << " failed\n"; return 1; }
    std::cout << "luma publisher passed\n";
}
