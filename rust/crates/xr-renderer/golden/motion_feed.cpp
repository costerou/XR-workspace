// Writes golden/motion_feed.txt: xr::motion_delta (spike/src/motion_feed.cpp over luma::extrapolate, both linked
// unchanged) over a grid of poses, velocities, accelerations, horizons and limits. Every float is printed as its IEEE
// bits in hex, so the comparison is exact, NaN included. tests/motion_feed.rs reads the tables back, runs the Rust
// motion_delta over the same grid and compares lines.
// From the repository root (the g++ command is one line):
//   mkdir -p ~/.cache/xr-rr-cpp/rr72-c &&
//   g++ -std=c++20 -O1 -pthread -Ispike/include -Ispike/driver/include -o ~/.cache/xr-rr-cpp/rr72-c/motion-feed-golden
//       rust/crates/xr-renderer/golden/motion_feed.cpp spike/src/motion_feed.cpp spike/src/pose.cpp spike/driver/src/predict.cpp spike/driver/src/rt.cpp &&
//   ~/.cache/xr-rr-cpp/rr72-c/motion-feed-golden > rust/crates/xr-renderer/golden/motion_feed.txt
// Lines (q is x y z w, as xr::Quat):
//   q <i> <x y z w> | omega <i> <x y z> | alpha <i> <x y z> | horizon <i> <ms> | max <i> <deg>
//   d <q> <omega> <alpha> <horizon> <max> <delta x y z w>
#include "xr/motion_feed.hpp"
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
xr::Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * kDeg / 2, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}
using V3 = std::array<float, 3>;
void table(const char* name, const std::vector<V3>& v) {
    for (std::size_t i = 0; i < v.size(); ++i)
        std::printf("%s %zu %08x %08x %08x\n", name, i, bits(v[i][0]), bits(v[i][1]), bits(v[i][2]));
}
}

int main() {
    const std::vector<xr::Quat> qs{{}, axis_angle(0.6f, 0.8f, 0, 70), {0, 0, 2, 0}};  // the last not unit
    const float r = kDeg;
    const std::vector<V3> omegas{
        {0, 0, 0},           {0, 0, 1 * r},      {0, 0, 2.5f * r},   {0, 0, 5 * r},     {8 * r, 0, 0},
        {0, 0, 30 * r},      {0, 0, 100 * r},    {0.3f, -0.8f, 2.0f}, {0, 0, -200 * r}, {0, 0, 35},
        {kNan, 0, 1},        {0, kInf, 0},
    };
    const std::vector<V3> alphas{
        {0, 0, 0}, {0, 0, 30}, {0, 0, -2000 * r}, {0, 0, -100}, {0, 0, 1e4f}, {5, 0, 0}, {0, kNan, 0},
    };
    const std::vector<float> horizons{kNan, -5, 0, 1e-30f, 1e-3f, 5, 16.6f, 20, 40, 200, 1e4f};
    const std::vector<float> maxes{15, 5, 0, kNan, 90};
    const std::vector<std::size_t> few{7, 8, 9};  // 20, 40 and 200 ms, for every limit but 15 deg

    for (std::size_t i = 0; i < qs.size(); ++i)
        std::printf("q %zu %08x %08x %08x %08x\n", i, bits(qs[i].x), bits(qs[i].y), bits(qs[i].z), bits(qs[i].w));
    table("omega", omegas);
    table("alpha", alphas);
    for (std::size_t i = 0; i < horizons.size(); ++i) std::printf("horizon %zu %08x\n", i, bits(horizons[i]));
    for (std::size_t i = 0; i < maxes.size(); ++i) std::printf("max %zu %08x\n", i, bits(maxes[i]));
    const auto emit = [&](std::size_t q, std::size_t w, std::size_t a, std::size_t h, std::size_t m) {
        const xr::FeedMotion motion{qs[q], 5'000'000'000, omegas[w], alphas[a]};
        const xr::Quat d = xr::motion_delta(motion, horizons[h], maxes[m]);
        std::printf("d %zu %zu %zu %zu %zu %08x %08x %08x %08x\n", q, w, a, h, m, bits(d.x), bits(d.y), bits(d.z), bits(d.w));
    };
    for (std::size_t q = 0; q < qs.size(); ++q)
        for (std::size_t w = 0; w < omegas.size(); ++w)
            for (std::size_t a = 0; a < alphas.size(); ++a)
                for (std::size_t h = 0; h < horizons.size(); ++h) emit(q, w, a, h, 0);
    for (std::size_t m = 1; m < maxes.size(); ++m)
        for (std::size_t w = 0; w < omegas.size(); ++w)
            for (std::size_t a = 0; a < alphas.size(); ++a)
                for (std::size_t h : few) emit(1, w, a, h, m);
}
