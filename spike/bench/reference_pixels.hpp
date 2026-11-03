#pragma once
// The CPU pixel paths as they were before the capture-pipeline performance pass (renderer 0072a09), kept verbatim as
// the reference: capture_test/presentation_test check the faster versions against them pixel for pixel, and
// capture_bench times both.
#include "xr/capture.hpp"
#include "xr/presentation.hpp"
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>

namespace xr::reference {
// capture.cpp blit_nearest: one 64-bit division per pixel.
inline void blit_nearest(const std::uint32_t* src, std::uint32_t src_width, std::uint32_t src_height, std::uint32_t src_stride_px,
                         std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px, Rect r) {
    if (!src || !dst || !src_width || !src_height || !r.width || !r.height) return;
    const std::uint32_t x_end = std::min<std::uint64_t>(std::uint64_t(r.x) + r.width, dst_width);
    const std::uint32_t y_end = std::min<std::uint64_t>(std::uint64_t(r.y) + r.height, dst_height);
    for (std::uint32_t y = r.y; y < y_end; ++y) {
        const std::uint32_t sy = static_cast<std::uint32_t>(std::uint64_t(y - r.y) * src_height / r.height);
        const std::uint32_t* row = src + std::size_t(sy) * src_stride_px;
        std::uint32_t* out = dst + std::size_t(y) * dst_stride_px;
        for (std::uint32_t x = r.x; x < x_end; ++x)
            out[x] = row[std::uint64_t(x - r.x) * src_width / r.width] | 0xff000000u;
    }
}
// wayland.cpp CPU composition: the whole frame filled, then every captured source blitted into its tile. (The renderer's
// rows are `dst_width` apart, where one std::fill of width*height is the same as this per-row fill.)
inline void compose_tiles(std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px,
                          std::span<const TileSource> sources, std::span<const Rect> tiles, std::uint32_t background) {
    for (std::uint32_t y = 0; y < dst_height; ++y) std::fill_n(dst + std::size_t(y) * dst_stride_px, dst_width, background);
    for (std::size_t i = 0; i < sources.size() && i < tiles.size(); ++i) {
        if (!sources[i].pixels) continue;  // not yet captured: stays black
        reference::blit_nearest(sources[i].pixels, sources[i].width, sources[i].height, sources[i].stride_px, dst, dst_width, dst_height,
                     dst_stride_px, tiles[i]);
    }
}
// wayland.cpp test pattern: animated_pattern_pixel for every pixel.
inline void render_animated_pattern(std::uint32_t* pixels, std::uint32_t width, std::uint32_t height, std::size_t stride_px,
                                    std::uint64_t phase) {
    for (std::uint32_t y = 0; y < height; ++y)
        for (std::uint32_t x = 0; x < width; ++x) pixels[std::size_t(y) * stride_px + x] = animated_pattern_pixel(x, y, width, height, phase);
}
}
