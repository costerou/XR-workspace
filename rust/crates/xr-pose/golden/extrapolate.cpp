// Writes golden/extrapolate.txt: luma::extrapolate (spike/driver/src/predict.cpp, linked unchanged) over a grid of
// configs, motion states and horizons. Every float is printed as its IEEE bits in hex, so the comparison is exact,
// NaN included. tests/extrapolate.rs reads the tables back, runs the Rust port over the same grid and compares lines.
// From the repository root (the g++ command is one line):
//   mkdir -p ~/.cache/xr-rr-cpp/rr72-c &&
//   g++ -std=c++20 -O1 -Ispike/driver/include -o ~/.cache/xr-rr-cpp/rr72-c/extrapolate-golden
//       rust/crates/xr-pose/golden/extrapolate.cpp spike/driver/src/predict.cpp &&
//   ~/.cache/xr-rr-cpp/rr72-c/extrapolate-golden > rust/crates/xr-pose/golden/extrapolate.txt
// Lines:
//   cfg <i> <enabled><use_acceleration><stop_clamp><still_gate> <still_lo_dps> <still_hi_dps> <max_deg>
//   state <i> <valid><calibrated> <q w x y z> <host_ns> <omega x y z> <alpha x y z>
//   horizon <i> <ms>
//   e <cfg> <state> <horizon> <q w x y z> <target_ns> <horizon_ms> <predicted_deg> <clamped>
#include "luma/predict.hpp"
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <vector>

namespace {
constexpr float kDeg = 3.14159265358979f / 180;
const float kNan = std::numeric_limits<float>::quiet_NaN(), kInf = std::numeric_limits<float>::infinity();
unsigned bits(float v) { return std::bit_cast<std::uint32_t>(v); }
luma::Quatf axis_angle(float x, float y, float z, float deg) {
    const float h = deg * kDeg / 2, s = std::sin(h);
    return {std::cos(h), x * s, y * s, z * s};
}
}

int main() {
    std::vector<luma::PredictConfig> cfgs;
    const auto with = [&](auto change) { luma::PredictConfig c; change(c); cfgs.push_back(c); };
    with([](auto&) {});
    with([](auto& c) { c.use_acceleration = false; });
    with([](auto& c) { c.stop_clamp = false; });
    with([](auto& c) { c.still_gate = false; });
    with([](auto& c) { c.enabled = false; });
    with([](auto& c) { c.max_deg = 5; });
    with([](auto& c) { c.max_deg = 0; });
    with([](auto& c) { c.max_deg = kNan; });
    with([](auto& c) { c.still_lo_dps = 8; c.still_hi_dps = 2.5f; });  // hi <= lo: a step at hi
    with([](auto& c) { c.still_lo_dps = 5; c.still_hi_dps = 5; });
    with([](auto& c) { c.still_lo_dps = kNan; });
    with([](auto& c) { c.use_acceleration = false; c.still_gate = false; c.max_deg = 90; });

    const std::vector<luma::Quatf> qs{{}, axis_angle(0.6f, 0.8f, 0, 70)};
    const float r = kDeg;
    const std::vector<luma::Vec3f> omegas{
        {0, 0, 0},
        {0, 0, 1e-7f},                      // below exp_map's small-angle branch
        {0, 0, 1 * r},                      // a still head's gyro noise
        {0, 0, 2.5f * r},                   // the gate's low edge
        {0, 0, 5 * r},                      // inside the gate's ramp
        {0, 8 * r, 0},                      // the gate's high edge
        {0, 0, 30 * r},                     // slow
        {0, 0, 100 * r},
        {0.3f, -0.8f, 2.0f},                // about 125 deg/s, tumbling
        {0, 0, -200 * r},                   // fast, the other way
        {0, 0, 35},                         // 2000 deg/s: clamped
        {kNan, 0, 1},
        {0, kInf, 0},
    };
    const std::vector<luma::Vec3f> alphas{
        {0, 0, 0},
        {0, 0, 30},                         // speeding up along +z
        {0, 0, -2000 * r},                  // braking a +z turn: 100 deg/s stops in 50 ms
        {0, 0, -100},                       // braking hard: 1 rad/s stops in 10 ms
        {0, 0, 1e4f},                       // turning back a -z turn within a ms
        {5, 0, 0},                          // across the turn
        {0, kNan, 0},
    };
    std::vector<luma::MotionState> states;
    for (const auto& q : qs)
        for (const auto& w : omegas)
            for (const auto& a : alphas) {
                luma::MotionState s;
                s.q = q; s.host_ns = 5'000'000'000; s.seq = 1; s.omega_rad_s = w; s.alpha_rad_s2 = a;
                s.valid = s.calibrated = true;
                states.push_back(s);
            }
    const std::size_t crossed = states.size();
    luma::MotionState special;
    special.q = axis_angle(0.6f, 0.8f, 0, 70); special.host_ns = 5'000'000'000;
    special.omega_rad_s = {0.3f, -0.8f, 2.0f}; special.alpha_rad_s2 = {0, 0, 30};
    special.valid = true; special.calibrated = false; states.push_back(special);     // uncalibrated
    special.valid = false; special.calibrated = true; states.push_back(special);     // invalid
    special.valid = true; special.q = {2, 0, 0, 2}; states.push_back(special);       // not unit
    special.q = {0, 0, 0, 0}; states.push_back(special);                            // no orientation
    special.q = {kNan, 0, 0, 1}; states.push_back(special);
    special.q = axis_angle(0, 0, 1, 30); special.host_ns = ~std::uint64_t(0) - 1000; states.push_back(special);  // wraps

    const std::vector<float> horizons{kNan, -5, 0, 1e-30f, 1e-3f, 5, 16.6f, 20, 25, 40, 200, 1e4f};
    const std::vector<std::size_t> few{7, 9, 10};  // 20, 40 and 200 ms, for every config but the default

    for (std::size_t i = 0; i < cfgs.size(); ++i) {
        const auto& c = cfgs[i];
        std::printf("cfg %zu %d%d%d%d %08x %08x %08x\n", i, c.enabled, c.use_acceleration, c.stop_clamp, c.still_gate,
                    bits(c.still_lo_dps), bits(c.still_hi_dps), bits(c.max_deg));
    }
    for (std::size_t i = 0; i < states.size(); ++i) {
        const auto& s = states[i];
        std::printf("state %zu %d%d %08x %08x %08x %08x %llu %08x %08x %08x %08x %08x %08x\n", i, s.valid, s.calibrated,
                    bits(s.q.w), bits(s.q.x), bits(s.q.y), bits(s.q.z), (unsigned long long)s.host_ns,
                    bits(s.omega_rad_s[0]), bits(s.omega_rad_s[1]), bits(s.omega_rad_s[2]),
                    bits(s.alpha_rad_s2[0]), bits(s.alpha_rad_s2[1]), bits(s.alpha_rad_s2[2]));
    }
    for (std::size_t i = 0; i < horizons.size(); ++i) std::printf("horizon %zu %08x\n", i, bits(horizons[i]));
    const auto emit = [&](std::size_t c, std::size_t s, std::size_t h) {
        const auto p = luma::extrapolate(states[s], horizons[h], cfgs[c]);
        std::printf("e %zu %zu %zu %08x %08x %08x %08x %llu %08x %08x %d\n", c, s, h, bits(p.q.w), bits(p.q.x),
                    bits(p.q.y), bits(p.q.z), (unsigned long long)p.target_ns, bits(p.horizon_ms),
                    bits(p.predicted_deg), p.clamped);
    };
    for (std::size_t s = 0; s < states.size(); ++s)
        for (std::size_t h = 0; h < horizons.size(); ++h) emit(0, s, h);
    // The other configs on the rotated base and the special states.
    for (std::size_t c = 1; c < cfgs.size(); ++c)
        for (std::size_t s = crossed / 2; s < states.size(); ++s)
            for (std::size_t h : few) emit(c, s, h);
}
