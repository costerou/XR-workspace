#include "xr/environment.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
// The theme colour as a neon: its own hue, at least this much OKLCH chroma (unless it is a grey), at this L* tone.
constexpr float kThemeMinChroma = 0.16f, kThemeGreyChroma = 0.03f, kThemeTone = 72.f;
MdRgb to_cap(MdRgb c) {
    for (auto& v : c) v = std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.f;
    const float most = std::max({c[0], c[1], c[2]});
    if (most <= 1e-6f) return {kEnvironmentCap, kEnvironmentCap, kEnvironmentCap};  // black theme: a grey grid
    for (auto& v : c) v = v / most * kEnvironmentCap;
    return c;
}
float ease(double t) { const float s = float(std::clamp(t, 0.0, 1.0)); return s * s * (3 - 2 * s); }
}

MdRgb environment_line_color(EnvironmentColor color, MdRgb accent) {
    if (color != EnvironmentColor::theme) return kEnvironmentCyan;
    for (auto& v : accent) v = std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.f;
    const MdHct hct = md_hct(accent);
    const float chroma = hct.chroma < kThemeGreyChroma ? hct.chroma : std::max(hct.chroma, kThemeMinChroma);
    return to_cap(md_color(hct.hue, chroma, kThemeTone));
}

Quat heading(Quat q) {
    const auto f = apply(quat_to_mat3(q), {1, 0, 0});  // forward (driver x) in the reference frame; z is up (gravity)
    if (std::hypot(f[0], f[1]) < 1e-4f) return Quat{};  // looking straight up or down: no heading to keep
    const float yaw = std::atan2(f[1], f[0]);
    return Quat{0, 0, std::sin(yaw / 2), std::cos(yaw / 2)};
}

Mat3 environment_view(Quat head_relative, const std::optional<Quat>& reference) {
    // The head relative to the levelled frame E = heading(reference) is E^-1 * reference * relative; view_from takes the
    // head relative to the recentre reference and an anchor A with A^-1 * relative equal to that: A = reference^-1 * E.
    const Quat anchor = reference ? quat_normalized(quat_mul(quat_conj(*reference), heading(*reference))).value_or(Quat{}) : Quat{};
    return view_from(head_relative, anchor);
}

bool EnvironmentFade::set(bool on, MdRgb color, double now_ms) {
    bool changed = false;
    if (on != on_) {
        from_level_ = level(now_ms); level_ms_ = now_ms; on_ = on;
        changed = true;
    }
    if (!seen_color_) {
        changed = changed || color != to_color_;
        from_color_ = to_color_ = color; seen_color_ = true;
    } else if (color != to_color_) {
        from_color_ = this->color(now_ms); to_color_ = color; color_ms_ = now_ms;
        changed = true;
    }
    return changed;
}

float EnvironmentFade::level(double now_ms) const {
    const float target = on_ ? 1.f : 0.f;
    return from_level_ + (target - from_level_) * ease((now_ms - level_ms_) / kEnvironmentFadeMs);
}

MdRgb EnvironmentFade::color(double now_ms) const {
    const float s = ease((now_ms - color_ms_) / kEnvironmentColorMs);
    return {from_color_[0] + (to_color_[0] - from_color_[0]) * s, from_color_[1] + (to_color_[1] - from_color_[1]) * s,
            from_color_[2] + (to_color_[2] - from_color_[2]) * s};
}

bool EnvironmentFade::animating(double now_ms) const {
    return now_ms - level_ms_ < kEnvironmentFadeMs || now_ms - color_ms_ < kEnvironmentColorMs;
}

bool EnvironmentFade::active(double now_ms) const { return level(now_ms) > 0.f || animating(now_ms); }
}
