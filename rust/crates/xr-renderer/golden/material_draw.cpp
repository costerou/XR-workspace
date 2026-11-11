// Writes golden/material_draw.txt from the C++ Material drawing (spike/src/material_draw.cpp): one line per case,
// "<case> <width> <height> <FNV-1a of the RGBA bytes>" for the drawn images, and "<case> <count> <FNV-1a of the
// little-endian f32 bits>" for the signed distances sampled on a grid. tests/material_draw.rs replays the same cases and
// must print the same lines. Every image starts from a busy background, so the "over" blend is exercised too.
// From the repository root, after `cmake -S spike -B $B && cmake --build $B --target xr_hud`:
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/material_draw.cpp $B/libxr_hud.a $B/libxr_view.a \
//     $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/material-draw-golden \
//     && /tmp/material-draw-golden > rust/crates/xr-renderer/golden/material_draw.txt
#include "xr/material_draw.hpp"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <limits>
#include <string>
using namespace xr;
namespace {
std::uint64_t fnv(const std::uint8_t* p, std::size_t n, std::uint64_t h = 0xcbf29ce484222325ull) {
    for (std::size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 0x100000001b3ull; }
    return h;
}
HudImage background(std::uint32_t w, std::uint32_t h) {
    HudImage im{w, h, std::vector<std::uint8_t>(std::size_t(w) * h * 4)};
    for (std::size_t i = 0; i < im.rgba.size(); ++i) im.rgba[i] = std::uint8_t((i * 37 + (i >> 2) * 11) & 0xff);
    return im;
}
void image(const std::string& name, std::uint32_t w, std::uint32_t h, const std::function<void(HudImage&)>& draw) {
    auto im = background(w, h);
    draw(im);
    std::printf("%s %u %u %llu\n", name.c_str(), im.width, im.height, (unsigned long long)fnv(im.rgba.data(), im.rgba.size()));
}
// The distance at every point of the grid [-r, r] x [-r, r] in steps of `step`, row by row.
void grid(const std::string& name, float r, float step, const std::function<float(float, float)>& d) {
    std::uint64_t h = 0xcbf29ce484222325ull;
    std::size_t n = 0;
    for (int j = 0; -r + float(j) * step <= r; ++j)
        for (int i = 0; -r + float(i) * step <= r; ++i, ++n) {
            const float v = d(-r + float(i) * step, -r + float(j) * step);
            std::uint32_t bits; std::memcpy(&bits, &v, 4);
            const std::uint8_t le[4] = {std::uint8_t(bits), std::uint8_t(bits >> 8), std::uint8_t(bits >> 16), std::uint8_t(bits >> 24)};
            h = fnv(le, 4, h);
        }
    std::printf("%s %zu %llu\n", name.c_str(), n, (unsigned long long)h);
}
}  // namespace

int main() {
    const MdRgb ink{0.71f, 0.42f, 0.13f}, pale{0.62f, 0.66f, 0.7f};
    // Containers: whole and fractional edges, radius past the half size, square, off the image, translucent.
    const struct { const char* name; float x0, y0, x1, y1, r, alpha; } boxes[] = {
        {"whole", 5, 5, 55, 25, 10, 1}, {"fraction", 3.3f, 4.7f, 51.6f, 27.2f, 7.5f, 1}, {"pill", 2, 6, 58, 22, 40, 0.8f},
        {"square", 10, 8, 30, 20, 0, 1}, {"off", -12.5f, -6.2f, 20.4f, 18.9f, 9, 1}, {"faint", 6, 3, 50, 28, 12, 0.35f},
        {"thin", 4, 14.6f, 56, 15.4f, 3, 1}, {"empty", 20, 10, 20, 30, 4, 1}};
    for (const auto& b : boxes) {
        image(std::string("fill_") + b.name, 60, 32, [&](HudImage& im) { md_fill_rounded(im, b.x0, b.y0, b.x1, b.y1, b.r, ink, b.alpha); });
        for (float width : {1.f, 2.5f})
            image(std::string("stroke_") + b.name + "_" + std::to_string(width).substr(0, 3), 60, 32,
                  [&](HudImage& im) { md_stroke_rounded(im, b.x0, b.y0, b.x1, b.y1, b.r, width, pale, b.alpha); });
    }
    const struct { const char* name; float cx, cy, r, alpha; } discs[] = {
        {"dot", 10, 10, 6, 0.5f}, {"fraction", 9.4f, 11.3f, 4.25f, 1}, {"tiny", 5.5f, 5.5f, 0.7f, 1}, {"edge", 1, 18, 7, 0.9f}};
    for (const auto& d : discs)
        image(std::string("disc_") + d.name, 20, 20, [&](HudImage& im) { md_disc(im, d.cx, d.cy, d.r, ink, d.alpha); });
    // Icons: every one at sizes below, at and above kMdIconPx, whole and fractional centres.
    const struct { const char* name; MdIcon icon; } icons[] = {
        {"add", MdIcon::add}, {"remove", MdIcon::remove}, {"width", MdIcon::width}, {"arrow_drop_down", MdIcon::arrow_drop_down},
        {"arrow_drop_up", MdIcon::arrow_drop_up}, {"rotate_left", MdIcon::rotate_left}, {"rotate_right", MdIcon::rotate_right},
        {"level", MdIcon::level}};
    const struct { const char* name; float cx, cy, size, alpha; } placements[] = {
        {"12", 20, 20, 12, 1}, {"14.5", 19.3f, 20.6f, 14.5f, 1}, {"18", 20, 20, 18, 1}, {"24", 20.5f, 19.5f, 24, 0.6f}, {"37", 20.2f, 20.7f, 37, 1}};
    for (const auto& ic : icons) {
        for (const auto& p : placements)
            image(std::string("icon_") + ic.name + "_" + p.name, 40, 40,
                  [&](HudImage& im) { md_icon(im, ic.icon, p.cx, p.cy, p.size, ink, p.alpha); });
        for (float size : {12.f, 18.f, 24.f})
            grid(std::string("icon_distance_") + ic.name + "_" + std::to_string(int(size)), 13, 0.37f,
                 [&](float x, float y) { return md_icon_distance(ic.icon, x, y, size); });
    }
    // Corner caps (the glass panel's corner handles) and the rounded box distance.
    grid("corner_8.5_12_5_2.4", 14, 0.29f, [](float x, float y) { return md_corner_distance(x, y, 8.5f, 12, 5, 2.4f); });
    grid("corner_6_9_0_3", 14, 0.29f, [](float x, float y) { return md_corner_distance(x, y, 6, 9, 0, 3); });
    grid("corner_10_10_10_1", 14, 0.29f, [](float x, float y) { return md_corner_distance(x, y, 10, 10, 10, 1); });
    grid("rounded_distance", 20, 0.43f, [](float x, float y) { return md_rounded_distance(x, y, 1.5f, -2, 12, 7, 5); });
    grid("rounded_distance_clamped", 20, 0.43f, [](float x, float y) { return md_rounded_distance(x, y, 0, 0, 6, 9, 30); });
    // The blend itself: every coverage step on one pixel, outside the image and NaN drawing nothing.
    image("over", 16, 16, [&](HudImage& im) {
        for (int i = 0; i < 256; ++i) md_over(im, i % 16, i / 16, ink, float(i) / 200.f);
        md_over(im, -1, 3, pale, 1); md_over(im, 16, 3, pale, 1); md_over(im, 3, 16, pale, 1); md_over(im, 3, 3, pale, std::numeric_limits<float>::quiet_NaN());
    });
}
