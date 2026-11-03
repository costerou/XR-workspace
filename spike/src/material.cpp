#include "xr/material.hpp"
#include <algorithm>
#include <cmath>
#include <numbers>

namespace xr {
namespace {
float unit(float v) { return std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.f; }
double to_linear(double c) { return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4); }
double to_srgb(double c) { return c <= 0.0031308 ? 12.92 * c : 1.055 * std::pow(c, 1 / 2.4) - 0.055; }
struct Lin { double r = 0, g = 0, b = 0; };
struct Lab { double L = 0, a = 0, b = 0; };
Lin linear(MdRgb c) { return {to_linear(unit(c[0])), to_linear(unit(c[1])), to_linear(unit(c[2]))}; }
double luminance(const Lin& c) { return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; }
// OKLab (Björn Ottosson, 2020), from and to linear sRGB.
Lab oklab(const Lin& c) {
    const double l = std::cbrt(0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b);
    const double m = std::cbrt(0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b);
    const double s = std::cbrt(0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b);
    return {0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s};
}
Lin from_oklab(const Lab& c) {
    const double l = c.L + 0.3963377774 * c.a + 0.2158037573 * c.b, m = c.L - 0.1055613458 * c.a - 0.0638541728 * c.b,
                 s = c.L - 0.0894841775 * c.a - 1.2914855480 * c.b;
    const double l3 = l * l * l, m3 = m * m * m, s3 = s * s * s;
    return {4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3, -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
            -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3};
}
// CIE L* <-> relative luminance Y.
constexpr double kEpsilon = 216.0 / 24389.0, kKappa = 24389.0 / 27.0;
double y_from_tone(double t) { return t > kKappa * kEpsilon ? std::pow((t + 16) / 116, 3) : t / kKappa; }
double tone_from_y(double y) { return y > kEpsilon ? 116 * std::cbrt(y) - 16 : y * kKappa; }
bool in_gamut(const Lin& c) {
    constexpr double e = 1e-6;
    return c.r >= -e && c.r <= 1 + e && c.g >= -e && c.g <= 1 + e && c.b >= -e && c.b <= 1 + e;
}
// OKLab lightness that gives luminance `y` at chroma (a, b): luminance grows with L (bisection).
Lin at_luminance(double y, double a, double b) {
    double lo = 0, hi = 1.05;
    for (int i = 0; i < 30; ++i) {
        const double mid = (lo + hi) / 2;
        (luminance(from_oklab({mid, a, b})) < y ? lo : hi) = mid;
    }
    return from_oklab({(lo + hi) / 2, a, b});
}
MdRgb encode(const Lin& c) {
    auto e = [](double v) { return float(std::clamp(to_srgb(std::clamp(v, 0.0, 1.0)), 0.0, 1.0)); };
    return {e(c.r), e(c.g), e(c.b)};
}
MdRgb dim(MdRgb c) { return {c[0] * kMdOledLevel, c[1] * kMdOledLevel, c[2] * kMdOledLevel}; }
}

MdHct md_hct(MdRgb srgb) {
    const Lin c = linear(srgb);
    const Lab lab = oklab(c);
    double hue = std::atan2(lab.b, lab.a) * 180.0 / std::numbers::pi;
    if (hue < 0) hue += 360;
    return {float(hue), float(std::hypot(lab.a, lab.b)), float(tone_from_y(luminance(c)))};
}

float md_tone(MdRgb srgb) { return float(tone_from_y(luminance(linear(srgb)))); }

MdRgb md_color(float hue, float chroma, float tone) {
    const double t = std::isfinite(tone) ? std::clamp(double(tone), 0.0, 100.0) : 0.0;
    if (t <= 0) return {0, 0, 0};
    if (t >= 100) return {1, 1, 1};
    const double y = y_from_tone(t), h = (std::isfinite(hue) ? hue : 0.f) * std::numbers::pi / 180.0;
    const double ca = std::cos(h), sa = std::sin(h);
    double c = std::isfinite(chroma) ? std::max(0.0, double(chroma)) : 0.0;
    Lin best = at_luminance(y, c * ca, c * sa);
    if (in_gamut(best)) return encode(best);
    // Out of gamut: the most chroma that fits at this hue and tone (chroma 0, a grey, always does).
    double lo = 0, hi = c;
    best = at_luminance(y, 0, 0);
    for (int i = 0; i < 18; ++i) {
        const double mid = (lo + hi) / 2;
        const Lin m = at_luminance(y, mid * ca, mid * sa);
        if (in_gamut(m)) { lo = mid; best = m; } else hi = mid;
    }
    return encode(best);
}

float md_contrast(MdRgb a, MdRgb b) {
    const double la = luminance(linear(a)), lb = luminance(linear(b));
    return float((std::max(la, lb) + 0.05) / (std::min(la, lb) + 0.05));
}

MaterialScheme material_scheme(MdRgb seed) {
    const MdRgb s{unit(seed[0]), unit(seed[1]), unit(seed[2])};
    const MdHct hct = md_hct(s);
    const float hue = hct.hue, c = hct.chroma;
    // Fidelity: the primary palette is the seed's own chroma; the others are its hue at less (a grey seed: all grey).
    const float primary = c, secondary = c * 0.45f, neutral = std::min(c / 6.f, 0.016f), variant = std::min(c / 4.5f, 0.024f);
    auto P = [&](float t) { return md_color(hue, primary, t); };
    auto S = [&](float t) { return md_color(hue, secondary, t); };
    auto N = [&](float t) { return md_color(hue, neutral, t); };
    auto V = [&](float t) { return md_color(hue, variant, t); };
    static const MdHct error = md_hct({0xb3 / 255.f, 0x26 / 255.f, 0x1e / 255.f});  // M3's baseline error colour, #B3261E
    auto E = [&](float t) { return md_color(error.hue, error.chroma, t); };
    MaterialScheme m;
    m.seed_tone = hct.tone;
    m.container_tone = kMdContainerToneLo + (kMdContainerToneHi - kMdContainerToneLo) * std::clamp(hct.tone, 0.f, 100.f) / 100.f;
    m.primary = P(80); m.on_primary = P(20);
    m.primary_container = P(m.container_tone);
    // On the container: whichever of a light or a dark primary tone stands out more from it.
    const MdRgb light = P(92), dark = P(12);
    m.on_primary_container = md_contrast(light, m.primary_container) >= md_contrast(dark, m.primary_container) ? light : dark;
    m.primary_bright = P(98);
    m.secondary_container = S(30); m.on_secondary_container = S(90);
    m.error_container = E(30); m.on_error_container = E(90);
    m.surface = N(6); m.surface_container_low = N(10); m.surface_container = N(12); m.surface_container_high = N(17);
    m.surface_container_highest = N(22);
    m.on_surface = N(90); m.on_surface_variant = V(80); m.outline = V(60); m.outline_variant = V(30);
    for (MdRgb* r : {&m.primary, &m.on_primary, &m.primary_container, &m.on_primary_container, &m.primary_bright, &m.secondary_container,
                     &m.on_secondary_container, &m.error_container, &m.on_error_container, &m.surface, &m.surface_container_low,
                     &m.surface_container, &m.surface_container_high, &m.surface_container_highest, &m.on_surface,
                     &m.on_surface_variant, &m.outline, &m.outline_variant})
        *r = dim(*r);
    return m;
}

MdRgb md_layer(MdRgb container, MdRgb content, float opacity) {
    const float o = unit(opacity);  // exact at both ends: 0 is the container, 1 the content
    return {container[0] * (1 - o) + content[0] * o, container[1] * (1 - o) + content[1] * o, container[2] * (1 - o) + content[2] * o};
}

MdShadow md_elevation(float level) {
    constexpr float kDp[] = {0, 1, 3, 6, 8, 12};
    const float l = std::isfinite(level) ? std::clamp(level, 0.f, 5.f) : 0.f;
    const auto i = std::min<std::size_t>(std::size_t(l), 4);
    const float dp = kDp[i] + (kDp[i + 1] - kDp[i]) * (l - float(i));
    if (dp <= 0) return {0.f, 1.f, 0.f};
    return {std::min(0.6f, 0.4f + 0.02f * dp), 1.f + 0.75f * dp, 0.5f * dp};
}
}
