// Reads the bridge's private feed as the renderer's draw does and says, field by field, whether a frame could be predicted.
// Runs only under the private namespace harness; no SDK, no GPU.
#include "xr/predict.hpp"
#include <chrono>
#include <cmath>
#include <iostream>
#include <memory>
#include <thread>
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    using C = std::chrono::steady_clock;
    const auto end = C::now() + std::chrono::seconds(3);
    std::unique_ptr<xr::PoseFeed> feed; std::string why;
    float last = -1; int distinct = 0, gaps_ok = 0, omega_ok = 0, horizon_ok = 0; bool cfg_valid = false; float cap = -1, horizon_ms = -1;
    while (C::now() < end) {
        if (!feed) feed = xr::PoseFeed::open(argv[1], why);
        if (feed) {
            const auto raw = feed->look_ahead_cfg();
            const auto cfg = raw ? xr::look_ahead_from(*raw) : std::nullopt;
            cfg_valid = cfg.has_value(); if (raw) cap = (*raw)[3];
            const auto sample = feed->read();
            if (sample && sample->device_ms != last) {
                last = sample->device_ms; ++distinct;
                const float gap = sample->device_ms - sample->older_ms[0];
                if (distinct > 3) {
                    if (gap >= 2 && gap <= 50) ++gaps_ok;
                    if (xr::angular_velocity(xr::history_of(*sample))) ++omega_ok;
                    if (const auto h = xr::prediction_horizon(cfg, 16.6, 8.0, 8.33)) { ++horizon_ok; horizon_ms = *h; }
                }
            }
            if (distinct > 40) break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    std::cout << "{\"distinct\":" << distinct << ",\"gaps_ok\":" << gaps_ok << ",\"omega_ok\":" << omega_ok << ",\"horizon_ok\":" << horizon_ok
              << ",\"look_ahead_valid\":" << (cfg_valid ? "true" : "false") << ",\"cap_ms\":" << cap << ",\"horizon_ms\":" << horizon_ms << "}\n";
    return 0;
}
