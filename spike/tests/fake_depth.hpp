#pragma once
// A stand-in for the depth engine (xr/depth.hpp): synthetic pictures and depth maps with known answers. Test-only.
#include "xr/stereo.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace xr::fake {
// Depth: far wall (0) with a near box (1) in the middle third.
inline PlaneF box_depth(int w, int h, float far = 0.f, float near = 1.f) {
    PlaneF d{w, h, std::vector<float>(std::size_t(w) * h, far)};
    for (int y = h / 3; y < 2 * h / 3; ++y) for (int x = w / 3; x < 2 * w / 3; ++x) d.px[std::size_t(y) * w + x] = near;
    return d;
}
inline PlaneF ramp_depth(int w, int h) {  // far at the left, near at the right
    PlaneF d{w, h, std::vector<float>(std::size_t(w) * h)};
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) d.px[std::size_t(y) * w + x] = (float(x) + 0.5f) / float(w);
    return d;
}
inline PlaneF flat_depth(int w, int h, float value) { return {w, h, std::vector<float>(std::size_t(w) * h, value)}; }
// Smooth, unrepeating noise of many scales: the hard case for matching and the depth maps of real pictures.
inline float bumps(float x, float y, unsigned seed) {
    float v = 0, k = 1;
    for (int i = 0; i < 4; ++i) {
        const float a = float(seed * 13u + unsigned(i) * 7u) * 0.37f;
        v += k * std::sin(x * (3.f + float(i) * 4.f) + a) * std::cos(y * (2.f + float(i) * 5.f) - a);
        k *= 0.5f;
    }
    return 0.5f + 0.27f * v;
}
inline PlaneF bump_depth(int w, int h, unsigned seed) {
    PlaneF d{w, h, std::vector<float>(std::size_t(w) * h)};
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) d.px[std::size_t(y) * w + x] = std::clamp(bumps(float(x) / float(w) * 6.f, float(y) / float(h) * 6.f, seed), 0.f, 1.f);
    return d;
}
// Picture: a colourful texture, different everywhere, so a shift can be measured. `flat`: no fine detail, not text.
inline RgbF textured(int w, int h) {
    RgbF c{w, h, std::vector<float>(std::size_t(w) * h * 3)};
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        auto* p = &c.px[(std::size_t(y) * w + x) * 3];
        const float u = float(x) / float(w), v = float(y) / float(h);
        p[0] = 0.5f + 0.4f * std::sin(u * 31.f + v * 7.f); p[1] = 0.5f + 0.4f * std::sin(u * 13.f - v * 19.f + 1.f); p[2] = 0.5f + 0.4f * std::cos(u * 47.f + v * 3.f);
    }
    return c;
}
// A red box (matching box_depth at this size) on a green wall.
inline RgbF box_picture(int w, int h, float red = 1.f) {
    RgbF c{w, h, std::vector<float>(std::size_t(w) * h * 3)};
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        const bool box = y >= h / 3 && y < 2 * h / 3 && x >= w / 3 && x < 2 * w / 3;
        auto* p = &c.px[(std::size_t(y) * w + x) * 3];
        p[0] = box ? red : 0.05f; p[1] = box ? 0.05f : 0.6f + 0.2f * std::sin(float(x) * 0.31f) * std::sin(float(y) * 0.23f); p[2] = 0.05f;
    }
    return c;
}
// Rows of small "letters" (1-2 px strokes on a dark ground) inside the box x0..x1, y0..y1; the rest stays as it was.
inline void stamp_text(RgbF& c, int x0, int y0, int x1, int y1) {
    for (int y = y0; y < y1; ++y) for (int x = x0; x < x1; ++x) {
        const int line = (y - y0) % 14, col = (x - x0) % 8, letter = (x - x0) / 8;
        bool ink = false;
        if (line >= 2 && line < 11) {
            const unsigned shape = (unsigned(letter) * 2654435761u + unsigned((y - y0) / 14) * 40503u) >> 13;
            ink = col == 1 + int(shape & 1u) || col == 5 || (line == 2 + int(shape >> 3 & 3u) * 2 && col < 6);
        }
        auto* p = &c.px[(std::size_t(y) * c.w + x) * 3];
        p[0] = p[1] = p[2] = ink ? 0.9f : 0.08f;
    }
}
inline std::vector<std::uint8_t> rgba_bytes(const RgbF& c) {
    std::vector<std::uint8_t> out(std::size_t(c.w) * c.h * 4);
    for (std::size_t i = 0; i < std::size_t(c.w) * c.h; ++i) {
        for (std::size_t k = 0; k < 3; ++k) out[i * 4 + k] = std::uint8_t(std::lround(std::clamp(c.px[i * 3 + k], 0.f, 1.f) * 255.f));
        out[i * 4 + 3] = 255;
    }
    return out;
}
}
