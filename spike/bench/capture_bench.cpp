// capture_bench: times the renderer's CPU-side capture/frame work against the versions before the performance pass
// (reference_pixels.hpp) on renderer-sized inputs, and checks every timed pair gives identical pixels.
//   capture_bench [--iterations N]   prints one JSON object (median and p90 microseconds per call, speedup)
// The end-to-end numbers (frame callback -> commit, capture -> present, CPU, wakes) come from capture_e2e_bench.
#include "reference_pixels.hpp"
#include "xr/capture.hpp"
#include "xr/presentation.hpp"
#include "xr/view.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

namespace {
struct Timing { double median_us = 0, p90_us = 0; };
Timing time_it(int iterations, const std::function<void()>& f) {
    std::vector<double> us;
    f();  // warm caches and the column table
    for (int i = 0; i < iterations; ++i) {
        const auto t0 = std::chrono::steady_clock::now();
        f();
        us.push_back(std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count());
    }
    std::sort(us.begin(), us.end());
    return {us[us.size() / 2], us[std::min(us.size() - 1, us.size() * 9 / 10)]};
}
std::string row(const char* name, Timing before, Timing after, bool same) {
    char buf[512];
    std::snprintf(buf, sizeof buf,
                  "  \"%s\": {\"before_us\": {\"median\": %.1f, \"p90\": %.1f}, \"after_us\": {\"median\": %.1f, \"p90\": %.1f}, "
                  "\"speedup\": %.1f, \"identical\": %s}",
                  name, before.median_us, before.p90_us, after.median_us, after.p90_us,
                  after.median_us > 0 ? before.median_us / after.median_us : 0.0, same ? "true" : "false");
    return buf;
}
}

int main(int argc, char** argv) {
    int iterations = 60;
    for (int i = 1; i + 1 < argc; ++i) if (std::strcmp(argv[i], "--iterations") == 0) iterations = std::max(5, std::atoi(argv[i + 1]));
    constexpr std::uint32_t W = 1920, H = 1200, SW = 1920, SH = 1080;
    std::vector<std::uint32_t> source(std::size_t(SW) * SH);
    std::uint32_t seed = 12345;
    for (auto& p : source) { seed = seed * 1664525u + 1013904223u; p = seed; }
    std::vector<std::uint32_t> before(std::size_t(W) * H), after(std::size_t(W) * H);
    std::vector<std::string> rows;
    bool all_same = true;

    for (int desktops : {3, 1}) {  // CPU composition fallback: the frame wayland.cpp draws without GL
        std::vector<xr::Size> sizes(std::size_t(desktops), xr::Size{float(SW), float(SH)});
        const auto tiles = xr::side_by_side(sizes, W, H);
        std::vector<xr::TileSource> sources(std::size_t(desktops), xr::TileSource{source.data(), SW, SH, SW});
        const auto t_before = time_it(iterations, [&] { xr::reference::compose_tiles(before.data(), W, H, W, sources, tiles, 0xff000000u); });
        const auto t_after = time_it(iterations, [&] { xr::compose_tiles(after.data(), W, H, W, sources, tiles, 0xff000000u); });
        const bool same = before == after;
        all_same = all_same && same;
        rows.push_back(row(desktops == 3 ? "cpu_frame_3_desktops" : "cpu_frame_1_desktop", t_before, t_after, same));
    }
    {  // presentation-only animated test pattern frame
        std::uint64_t phase_a = 1, phase_b = 1;
        const auto t_before = time_it(iterations, [&] { xr::reference::render_animated_pattern(before.data(), W, H, W, phase_a++); });
        const auto t_after = time_it(iterations, [&] { xr::render_animated_pattern(after.data(), W, H, W, phase_b++); });
        const bool same = phase_a == phase_b && before == after;
        all_same = all_same && same;
        rows.push_back(row("pattern_frame", t_before, t_after, same));
    }
    {  // per main-loop wake: the overview geometry line (layout + JSON before; a model of wayland.cpp's input-key check
     // plus the compare with the line last sent after, when nothing changed)
        const std::array<xr::Size, 3> sizes{xr::Size{1920, 1080}, xr::Size{1920, 1080}, xr::Size{1920, 1080}};
        const float spacing = 40, half_w = 0.35f, half_h = 0.22f;
        std::string sent, sent_after, line_before, line_after;
        const auto t_before = time_it(iterations * 20, [&] {
            const auto tiles = xr::overview_layout(sizes, float(W), float(H), spacing);
            line_before = xr::overview_geometry_json(tiles, W, H, half_w, half_h);
            if (line_before != sent) sent = line_before;
        });
        std::array<std::uint32_t, 12> key{}, last{};
        bool valid = false;
        const auto t_after = time_it(iterations * 20, [&] {
            for (std::size_t i = 0; i < 3; ++i) { std::memcpy(&key[2 * i], &sizes[i].w, 4); std::memcpy(&key[2 * i + 1], &sizes[i].h, 4); }
            key[6] = 3; key[7] = W; key[8] = H;
            std::memcpy(&key[9], &spacing, 4); std::memcpy(&key[10], &half_w, 4); std::memcpy(&key[11], &half_h, 4);
            if (!valid || key != last) {
                last = key; valid = true;
                line_after = xr::overview_geometry_json(xr::overview_layout(sizes, float(W), float(H), spacing), W, H, half_w, half_h);
            }
            if (line_after != sent_after) sent_after = line_after;  // the renderer still compares with the line last sent
        });
        const bool same = line_before == line_after;
        all_same = all_same && same;
        rows.push_back(row("overview_geometry_per_wake", t_before, t_after, same));
    }
    std::printf("{\n");
    for (std::size_t i = 0; i < rows.size(); ++i) std::printf("%s%s\n", rows[i].c_str(), i + 1 < rows.size() ? "," : "");
    std::printf("}\n");
    return all_same ? 0 : 1;
}
