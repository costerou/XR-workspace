#include "xr/presentation.hpp"
#include <algorithm>

namespace xr {
void apply_presentation_policy(SurfaceRequests& surface) {
    surface.keyboard_interactivity(0); // ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE
    surface.exclusive_zone(0);
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
