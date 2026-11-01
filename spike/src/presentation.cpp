#include "xr/presentation.hpp"
#include <algorithm>
#include <charconv>

namespace xr {
std::optional<std::uint32_t> parse_pattern_duration(const std::string& text) {
    if (text.empty() || text[0] < '1' || text[0] > '9' || text.size() > 5) return {};
    std::uint32_t n = 0;
    auto result = std::from_chars(text.data(), text.data() + text.size(), n);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || n > 60000) return {};
    return n;
}
std::uint32_t animated_pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t w,
                                     std::uint32_t h, std::uint64_t phase) {
    auto base = pattern_pixel(x, y, w, h);
    // A moving bright vertical stripe; no image capture or pose input.
    if (phase && w && x < w && y < h && ((x + (phase % w) * 8) % w) < 32) return 0xffffffffU;
    return base;
}
void apply_presentation_policy(SurfaceRequests& surface) {
    surface.keyboard_interactivity(0); // ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE
    surface.exclusive_zone(-1);  // cover the whole output, including other surfaces' reserved zones (bar)
    surface.empty_input_region();
    surface.commit();
}
std::uint32_t layer_version(std::uint32_t advertised) { return std::min(advertised, 4U); } // Pinned XML v4; <= compositor v5.
bool valid_surface_size(std::uint32_t width, std::uint32_t height) {
    return width > 0 && height > 0 && width <= 8192 && height <= 8192 &&
           std::uint64_t(width) * height * 4 <= 64 * 1024 * 1024;
}
std::uint32_t pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t width, std::uint32_t height) {
    if (!valid_surface_size(width, height) || x >= width || y >= height) return 0xff000000U;
    constexpr std::uint32_t colors[] = {0xffff3030U, 0xff30ff30U, 0xff3030ffU};
    auto color = colors[std::min(2U, static_cast<std::uint32_t>(std::uint64_t(x) * 3 / width))];
    // Spatial grid distinguishes orientation/resize. No capture or external pixels.
    if (y > 0 && (y % 64 == 0 || x % 64 == 0)) color = 0xffeeeeeeU;
    return color;
}
}
