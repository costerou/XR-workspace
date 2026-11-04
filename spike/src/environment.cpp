#include "xr/environment.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
float ease(double t) { const float s = float(std::clamp(t, 0.0, 1.0)); return s * s * (3 - 2 * s); }
}

MdRgb environment_line_color(MdRgb accent) {
    for (auto& v : accent) v = std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.f;
    const float most = std::max({accent[0], accent[1], accent[2]});
    if (most < 1e-3f) return {kEnvironmentFloor, kEnvironmentFloor, kEnvironmentFloor};  // black: no hue to keep
    const float scale = most > kEnvironmentCap ? kEnvironmentCap / most : most < kEnvironmentFloor ? kEnvironmentFloor / most : 1.f;
    for (auto& v : accent) v *= scale;
    return accent;
}

Quat heading(Quat q) {
    const auto f = apply(quat_to_mat3(q), {1, 0, 0});  // forward (driver x) in the level frame; z is up
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

bool EnvironmentFade::set(bool on, double now_ms) {
    if (on == on_) return false;
    from_level_ = level(now_ms); level_ms_ = now_ms; on_ = on;
    return true;
}

float EnvironmentFade::level(double now_ms) const {
    const float target = on_ ? 1.f : 0.f;
    return from_level_ + (target - from_level_) * ease((now_ms - level_ms_) / kEnvironmentFadeMs);
}

bool EnvironmentFade::animating(double now_ms) const { return now_ms - level_ms_ < kEnvironmentFadeMs; }
}
