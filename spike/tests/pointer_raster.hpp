// CPU mirror of how the compositor fills the dot's flat triangles (gl.cpp, no anti-aliasing): a pixel is covered when its
// centre is inside a triangle. Shared by the geometry test (free_pointer_test) and the pixel test (gl_bench), so what the
// GL draws is compared with the same model the geometry is measured on.
#pragma once
#include "xr/view.hpp"
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace xr {
struct PointerMask {
    int x0 = 0, y0 = 0, w = 0, h = 0;      // canvas px of the mask's top-left corner, and its size
    std::vector<std::uint8_t> covered;     // w * h, top row first
    bool at(int x, int y) const {
        return x >= x0 && y >= y0 && x < x0 + w && y < y0 + h && covered[std::size_t(y - y0) * std::size_t(w) + std::size_t(x - x0)];
    }
    std::size_t count() const { return std::size_t(std::count(covered.begin(), covered.end(), std::uint8_t(1))); }
};

// `tris`: three points per triangle in view space, projected as the compositor's XR_PROJECT does for an unturned view.
inline PointerMask pointer_mask(const Canvas& c, std::span<const Vec3> tris, int x0, int y0, int w, int h) {
    PointerMask m{x0, y0, w, h, std::vector<std::uint8_t>(std::size_t(w) * std::size_t(h), 0)};
    auto project = [&](const Vec3& v) {
        const double u = double(v.x) / -double(v.z) / c.half_w, t = double(v.y) / -double(v.z) / c.half_h;
        return std::pair{(u + 1) * c.view_w / 2, (1 - t) * c.view_h / 2};
    };
    for (std::size_t i = 0; i + 2 < tris.size(); i += 3) {
        const auto [ax, ay] = project(tris[i]);
        const auto [bx, by] = project(tris[i + 1]);
        const auto [cx, cy] = project(tris[i + 2]);
        const double area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
        if (area == 0) continue;
        const double sign = area > 0 ? 1 : -1;
        for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
            const double px = x0 + x + 0.5, py = y0 + y + 0.5;
            const double e0 = ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) * sign, e1 = ((cx - bx) * (py - by) - (cy - by) * (px - bx)) * sign,
                         e2 = ((ax - cx) * (py - cy) - (ay - cy) * (px - cx)) * sign;
            if (e0 >= 0 && e1 >= 0 && e2 >= 0) m.covered[std::size_t(y) * std::size_t(w) + std::size_t(x)] = 1;
        }
    }
    return m;
}
}
