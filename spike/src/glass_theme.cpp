#include "xr/glass_theme.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
float unit(float v) { return std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.f; }
}

float glass_luma(std::array<float, 3> c) { return 0.2126f * c[0] + 0.7152f * c[1] + 0.0722f * c[2]; }

std::array<float, 3> glass_theme_hue(std::array<float, 3> accent, float luma, float peak) {
    std::array<float, 3> a{unit(accent[0]), unit(accent[1]), unit(accent[2])};
    if (std::max({a[0], a[1], a[2]}) < 1.f / 255.f) a = {1.f, 1.f, 1.f};
    const float top = std::max({a[0], a[1], a[2]});
    const float scale = std::min(std::max(luma, 0.f) / std::max(glass_luma(a), 1e-4f), std::max(peak, 0.f) / top);
    return {a[0] * scale, a[1] * scale, a[2] * scale};
}

std::array<float, 3> glass_theme_tint(std::array<float, 3> accent, float t) {
    t = unit(t);
    const auto hue = glass_theme_hue(accent, kGlassTintLuma, kGlassTintPeak);
    const float share = kGlassTintPill + (kGlassTintPanel - kGlassTintPill) * t;
    std::array<float, 3> out{};
    for (std::size_t i = 0; i < 3; ++i) out[i] = kGlassSlate[i] + (hue[i] - kGlassSlate[i]) * share;
    return out;
}

std::array<float, 3> glass_theme_highlight(std::array<float, 3> accent) {
    return glass_theme_hue(accent, 1.f, kGlassHighlightPeak);  // a luma of 1 is never reached first: the peak sets it
}
}
