// What the renderer would read from the bridge's private feed: the pose after the mount pitch. Private namespace harness only.
#include "xr/pose.hpp"
#include <chrono>
#include <cstdio>
#include <memory>
#include <string>
#include <thread>
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    using C = std::chrono::steady_clock;
    const auto end = C::now() + std::chrono::seconds(3);
    std::unique_ptr<xr::PoseFeed> feed; std::string why;
    while (C::now() < end) {
        if (!feed) feed = xr::PoseFeed::open(argv[1], why);
        if (feed) if (const auto s = feed->read()) {
            std::printf("{\"x\":%.6f,\"y\":%.6f,\"z\":%.6f,\"w\":%.6f}\n", s->orientation.x, s->orientation.y, s->orientation.z, s->orientation.w);
            return 0;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return 1;
}
