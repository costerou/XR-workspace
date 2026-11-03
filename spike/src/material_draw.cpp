#include "xr/material_draw.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
float clamp01(float v) { return std::clamp(v, 0.f, 1.f); }
// Distance from p to the segment a-b, less r: a capsule (a stroke with round caps).
float capsule(float px, float py, float ax, float ay, float bx, float by, float r) {
    const float pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
    const float h = clamp01((pax * bax + pay * bay) / std::max(bax * bax + bay * bay, 1e-6f));
    return std::hypot(pax - bax * h, pay - bay * h) - r;
}
// Signed distance to a triangle (Inigo Quilez's exact form).
float triangle(float px, float py, float x0, float y0, float x1, float y1, float x2, float y2) {
    const float e0x = x1 - x0, e0y = y1 - y0, e1x = x2 - x1, e1y = y2 - y1, e2x = x0 - x2, e2y = y0 - y2;
    const float v0x = px - x0, v0y = py - y0, v1x = px - x1, v1y = py - y1, v2x = px - x2, v2y = py - y2;
    auto proj = [](float vx, float vy, float ex, float ey, float& qx, float& qy) {
        const float h = clamp01((vx * ex + vy * ey) / std::max(ex * ex + ey * ey, 1e-6f));
        qx = vx - ex * h; qy = vy - ey * h;
    };
    float q0x, q0y, q1x, q1y, q2x, q2y;
    proj(v0x, v0y, e0x, e0y, q0x, q0y); proj(v1x, v1y, e1x, e1y, q1x, q1y); proj(v2x, v2y, e2x, e2y, q2x, q2y);
    const float s = e0x * e2y - e0y * e2x > 0 ? 1.f : -1.f;
    const float d0 = q0x * q0x + q0y * q0y, d1 = q1x * q1x + q1y * q1y, d2 = q2x * q2x + q2y * q2y;
    const float c0 = s * (v0x * e0y - v0y * e0x), c1 = s * (v1x * e1y - v1y * e1x), c2 = s * (v2x * e2y - v2y * e2x);
    const float d = std::min({d0, d1, d2}), c = std::min({c0, c1, c2});
    return c > 0 ? -std::sqrt(d) : std::sqrt(d);  // inside (every edge's cross term positive): negative
}
}

void md_over(HudImage& im, int x, int y, MdRgb c, float a) {
    if (x < 0 || y < 0 || std::uint32_t(x) >= im.width || std::uint32_t(y) >= im.height || !(a > 0)) return;
    a = std::min(a, 1.f);
    auto* p = &im.rgba[(std::size_t(y) * im.width + std::size_t(x)) * 4];
    for (int i = 0; i < 3; ++i) p[i] = std::uint8_t(std::lround(std::clamp(c[std::size_t(i)] * a * 255 + p[i] * (1 - a), 0.f, 255.f)));
    p[3] = std::uint8_t(std::lround(std::clamp(a * 255 + p[3] * (1 - a), 0.f, 255.f)));
}

float md_rounded_distance(float px, float py, float cx, float cy, float hw, float hh, float r) {
    r = std::clamp(r, 0.f, std::min(hw, hh));
    const float dx = std::fabs(px - cx) - (hw - r), dy = std::fabs(py - cy) - (hh - r);
    const float ox = std::max(dx, 0.f), oy = std::max(dy, 0.f);
    return std::sqrt(ox * ox + oy * oy) + std::min(std::max(dx, dy), 0.f) - r;
}

void md_fill_rounded(HudImage& im, float x0, float y0, float x1, float y1, float radius, MdRgb c, float alpha) {
    const float cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hw = (x1 - x0) / 2, hh = (y1 - y0) / 2;
    if (!(hw > 0) || !(hh > 0) || !(alpha > 0)) return;
    for (int y = int(std::floor(y0)) - 1; y <= int(std::ceil(y1)); ++y)
        for (int x = int(std::floor(x0)) - 1; x <= int(std::ceil(x1)); ++x) {
            const float cov = clamp01(0.5f - md_rounded_distance(float(x) + 0.5f, float(y) + 0.5f, cx, cy, hw, hh, radius));
            if (cov > 0) md_over(im, x, y, c, cov * alpha);
        }
}

void md_stroke_rounded(HudImage& im, float x0, float y0, float x1, float y1, float radius, float width, MdRgb c, float alpha) {
    const float cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hw = (x1 - x0) / 2, hh = (y1 - y0) / 2;
    if (!(hw > 0) || !(hh > 0) || !(alpha > 0) || !(width > 0)) return;
    for (int y = int(std::floor(y0)) - 1; y <= int(std::ceil(y1)); ++y)
        for (int x = int(std::floor(x0)) - 1; x <= int(std::ceil(x1)); ++x) {
            const float d = md_rounded_distance(float(x) + 0.5f, float(y) + 0.5f, cx, cy, hw, hh, radius);
            const float cov = clamp01(0.5f - d) * clamp01(0.5f + d + width);  // inside the edge, within `width` of it
            if (cov > 0) md_over(im, x, y, c, cov * alpha);
        }
}

void md_disc(HudImage& im, float cx, float cy, float r, MdRgb c, float alpha) {
    md_fill_rounded(im, cx - r, cy - r, cx + r, cy + r, r, c, alpha);
}

float md_icon_distance(MdIcon icon, float x, float y, float size) {
    const float k = 24.f / std::max(size, 1e-3f);  // icon px -> grid units
    const float gx = x * k + 12, gy = y * k + 12;
    float d = 1e9f;
    switch (icon) {
    case MdIcon::add:
        d = std::min(capsule(gx, gy, 5, 12, 19, 12, 1), capsule(gx, gy, 12, 5, 12, 19, 1));
        break;
    case MdIcon::remove:
        d = capsule(gx, gy, 5, 12, 19, 12, 1);
        break;
    case MdIcon::width:  // |<->|
        d = std::min({capsule(gx, gy, 2.5f, 5, 2.5f, 19, 1), capsule(gx, gy, 21.5f, 5, 21.5f, 19, 1), capsule(gx, gy, 8, 12, 16, 12, 1),
                      triangle(gx, gy, 5.5f, 12, 11, 6.5f, 11, 17.5f), triangle(gx, gy, 18.5f, 12, 13, 17.5f, 13, 6.5f)});
        break;
    case MdIcon::arrow_drop_down:  // Material Symbols' arrow_drop_down: a filled triangle, point down
        d = triangle(gx, gy, 7, 10, 17, 10, 12, 15);
        break;
    case MdIcon::arrow_drop_up:
        d = triangle(gx, gy, 7, 14, 12, 9, 17, 14);
        break;
    }
    // Optical size (as Material Symbols' opsz): below kMdIconPx the 2 px strokes thicken, up to 3 grid px at 12 px, so a
    // small icon keeps its weight next to its label.
    d -= std::clamp((kMdIconPx - size) / 12.f, 0.f, 0.5f);
    return d / k;
}

void md_icon(HudImage& im, MdIcon icon, float cx, float cy, float size, MdRgb c, float alpha) {
    if (!(size > 0) || !(alpha > 0)) return;
    const float half = size / 2;
    for (int y = int(std::floor(cy - half)) - 1; y <= int(std::ceil(cy + half)); ++y)
        for (int x = int(std::floor(cx - half)) - 1; x <= int(std::ceil(cx + half)); ++x) {
            const float cov = clamp01(0.5f - md_icon_distance(icon, float(x) + 0.5f - cx, float(y) + 0.5f - cy, size));
            if (cov > 0) md_over(im, x, y, c, cov * alpha);
        }
}
}
