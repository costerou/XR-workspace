#pragma once
// What a view looks like to the wearer. yaw_deg: how far right the view has turned. roll_deg: how far a horizontal line on the
// canvas leans at the screen's centre, positive when it leans left (counter-clockwise).
#include "xr/pose.hpp"
#include <cmath>

namespace xr {
struct Shown { float yaw_deg, roll_deg; };
inline Shown shown_by(const Mat3& view) {
    constexpr float kDeg = float(180 / M_PI);
    const auto ray = apply(transpose(view), {0, 0, -1});  // the screen's centre, on the canvas plane z = -1
    if (ray[2] > -1e-4f) return {NAN, NAN};
    const std::array<float, 3> at{ray[0] / -ray[2], ray[1] / -ray[2], -1}, beside{at[0] + 0.01f, at[1], -1};
    const auto a = apply(view, at), b = apply(view, beside);
    const float dx = b[0] / -b[2] - a[0] / -a[2], dy = b[1] / -b[2] - a[1] / -a[2];
    return {std::atan2(ray[0], -ray[2]) * kDeg, std::atan2(dy, dx) * kDeg};
}
}
