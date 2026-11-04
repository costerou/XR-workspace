// A driver for capture_e2e_bench --feed-drop: publishes a simulated head pose feed, drops it the way the glasses' USB does, and
// execs the renderer on the rest of its command line. It runs inside the bench's bwrap (private /tmp and SysV IPC), so it can
// never touch a real XRLinuxDriver's segments.
//
//   feed_script --live-s L --gone-s G -- renderer args...
//
// Timeline, after the incident of 2026-11-04 00:57:58 and XRLinuxDriver's src/driver.c: L seconds live; disconnect 1 (disabled
// set); the driver reconnects, disabled clear and the feed zeroed while it calibrates (reset_pose_data); 100 ms of samples get
// through; disconnect 2; reconnect and calibrate; at L + G live again on a new reference, the clock restarted.
#include "fake_feed.hpp"
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstring>
#include <string>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

namespace {
using Clock = std::chrono::steady_clock;
xr::Quat yaw(float deg) { const float h = deg * 3.14159265f / 360.f; return {0, 0, std::sin(h), std::cos(h)}; }

[[noreturn]] void play(xr::test::FakeFeed& feed, double live_s, double gone_s) {
    const auto start = Clock::now();
    auto seconds = [&] { return std::chrono::duration<double>(Clock::now() - start).count(); };
    auto publish = [&](double until, double epoch, xr::Quat q) {
        while (seconds() < until) {
            feed.write(q, float((seconds() - epoch) * 1000) + 1);
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    };
    auto quiet = [&](double until) { while (seconds() < until) std::this_thread::sleep_for(std::chrono::milliseconds(5)); };
    auto zero = [&] { const float none[16] = {}; feed.write(none); };
    const xr::Quat reference = yaw(70);
    publish(live_s, 0, yaw(20));
    *feed.disabled = true;                 // disconnect 1
    quiet(live_s + gone_s * 0.3);
    zero(); *feed.disabled = false;        // reconnected, calibrating
    quiet(live_s + gone_s * 0.55);
    publish(live_s + gone_s * 0.55 + 0.1, live_s, reference);  // a moment of life
    zero(); *feed.disabled = true;         // disconnect 2
    quiet(live_s + gone_s * 0.8);
    *feed.disabled = false;                // reconnected, calibrating
    quiet(live_s + gone_s);
    publish(1e9, live_s + gone_s, reference);
    _exit(0);
}
}

int main(int argc, char** argv) {
    double live = 0, gone = 0;
    int i = 1;
    for (; i + 1 < argc && std::strcmp(argv[i], "--") != 0; i += 2) {
        if (!std::strcmp(argv[i], "--live-s")) live = std::stod(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--gone-s")) gone = std::stod(argv[i + 1]);
        else return 2;
    }
    if (i + 1 >= argc || std::strcmp(argv[i], "--") != 0 || live <= 0 || gone <= 0) return 2;
    static xr::test::FakeFeed feed("/tmp/shader_runtime_");
    feed.write(yaw(20), 1);
    const pid_t renderer = getpid();
    const pid_t writer = fork();
    if (writer < 0) return 1;
    if (writer == 0) {
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        if (getppid() != renderer) _exit(0);
        syscall(SYS_close_range, 3u, ~0u, 0u);  // the renderer's sockets stay the renderer's alone
        play(feed, live, gone);
    }
    execv(argv[i + 1], argv + i + 1);
    _exit(127);
}
