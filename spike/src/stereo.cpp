#include "xr/stereo.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace xr {
namespace {
float finite_or(float v, float fallback) { return std::isfinite(v) ? v : fallback; }
constexpr float kPi = 3.14159265358979f;
}

StereoParams stereo_clamped(const StereoParams& in) {
    const StereoParams d;
    StereoParams p;
    p.strength = std::clamp(finite_or(in.strength, d.strength), 0.f, kStereoStrengthMax);
    p.convergence = std::clamp(finite_or(in.convergence, d.convergence), 0.f, 1.f);
    p.near_deg = std::clamp(finite_or(in.near_deg, d.near_deg), 0.f, kStereoNearDegMax);
    p.far_deg = std::clamp(finite_or(in.far_deg, d.far_deg), 0.f, kStereoFarDegMax);
    p.text_keep = std::clamp(finite_or(in.text_keep, d.text_keep), 0.f, 1.f);
    return p;
}

StereoLimits stereo_limits(const StereoParams& in, float eye_w, float half_w) {
    const auto p = stereo_clamped(in);
    if (!(eye_w > 0) || !(half_w > 0)) return {};
    const float px_per_deg = eye_w / (2.f * std::atan(half_w) * 180.f / kPi);
    return {p.near_deg * px_per_deg, p.far_deg * px_per_deg};
}

StereoParams stereo_from_wire(const ImmersiveParams& w) {
    StereoParams p;
    p.strength = float(w.strength) / 100.f;
    p.near_deg = float(w.near_arcmin) / 60.f;
    p.far_deg = float(w.far_arcmin) / 60.f;
    p.text_keep = float(w.text) / 100.f;
    return stereo_clamped(p);
}

float stereo_smoothstep(float a, float b, float x) {
    const float t = std::clamp((x - a) / (b - a), 0.f, 1.f);
    return t * t * (3.f - 2.f * t);
}

float stereo_nearness(float raw, float lo, float hi) {
    if (!std::isfinite(raw)) return std::numeric_limits<float>::quiet_NaN();
    const float span = hi - lo;
    return std::clamp(std::fabs(span) > 1e-6f ? (raw - lo) / span : 0.5f, 0.f, 1.f);
}

float stereo_v(float nearness, const StereoParams& in) {
    if (!std::isfinite(nearness)) return 0.f;
    const auto p = stereo_clamped(in);
    const float x = 2.f * p.strength * (nearness - p.convergence), a = std::fabs(x);
    if (a <= kStereoKnee) return x;
    const float room = 1.f - kStereoKnee;
    return std::copysign(kStereoKnee + room * std::tanh((a - kStereoKnee) / room), x);
}

float stereo_edge_gain(float u, float v, float aspect) {
    return stereo_smoothstep(0.f, 1.f, std::min(u, 1.f - u) / kStereoEdgeTaper) *
           stereo_smoothstep(0.f, 1.f, std::min(v, 1.f - v) / (kStereoEdgeTaper * aspect));
}

float stereo_temporal(float previous, float current, float alpha) {
    const float a = alpha + (1.f - alpha) * stereo_smoothstep(kStereoCutLo, kStereoCut, std::fabs(current - previous));
    return previous + (current - previous) * a;
}

float stereo_alpha(float dt_ms) { return dt_ms > 0 ? 1.f - std::exp(-dt_ms / 60.f) : 1.f; }

float stereo_text_score(float jumps) { return stereo_smoothstep(kStereoTextLo, kStereoTextHi, jumps); }

float stereo_side(int eye) { return eye == 0 ? 1.f : -1.f; }

float stereo_plane_disparity(float b, float z, float eye_w, float half_w) {
    if (!(z < 0) || !(half_w > 0)) return 0.f;
    return eye_w * b * (1.f + z) / (half_w * -z);
}

float stereo_safe_baseline(float b, const std::vector<float>& depths, const StereoLimits& limits, float eye_w, float half_w) {
    float keep = 1.f;
    for (const float z : depths) {
        const float d = stereo_plane_disparity(b, z, eye_w, half_w);
        if (d > limits.near_px) keep = std::min(keep, limits.near_px / d);
        else if (-d > limits.far_px) keep = std::min(keep, limits.far_px / -d);
    }
    return b * keep;
}

float stereo_infinity_shift(float b, const StereoLimits& limits, float eye_w, float half_w) {
    if (!(half_w > 0) || !(eye_w > 0)) return 0.f;
    return std::min(b / half_w, limits.far_px / eye_w);
}

float stereo_tile_scale(float tile_w, float canvas_w, float distance) {
    if (!(canvas_w > 0) || !(distance > 0)) return kStereoMinScale;
    return std::clamp(tile_w / (canvas_w * distance), kStereoMinScale, 1.f);
}

StereoBudget stereo_budget(const StereoLimits& l, float plane_px, float scale, float gain) {
    const float k = 0.5f * std::clamp(scale, 0.f, 1.f) * std::clamp(gain, 0.f, 1.f);
    return {k * std::clamp(l.near_px - plane_px, 0.f, l.near_px), k * std::clamp(l.far_px + plane_px, 0.f, l.far_px)};
}

float stereo_half_disparity(float v, const StereoBudget& b) { return v > 0 ? v * b.near_half : v * b.far_half; }

float StereoFader::update(double now_ms, bool on) {
    const double dt = started_? now_ms - last_ms_ : 0;
    started_ = true; last_ms_ = now_ms;
    const float before = t_;
    t_ = std::clamp(t_ + float(dt / kStereoFadeMs) * (on ? 1.f : -1.f), 0.f, 1.f);
    if (dt <= 0 && on && t_ <= 0) moving_ = true;  // just switched on: the next frame moves
    else moving_ = t_ != before || (on && t_ < 1.f);
    return stereo_smoothstep(0.f, 1.f, t_);
}

std::optional<SurfaceHit> stereo_surface_hit(const Canvas& canvas, const FRect& tile, const Placement& placement, const Vec3& ray) {
    const auto mesh = tile_mesh(canvas, tile, placement, tile);
    using V = std::array<float, 3>;
    const auto sub = [](const Vec3& a, const Vec3& b) { return V{a.x - b.x, a.y - b.y, a.z - b.z}; };
    const auto cross = [](const V& a, const V& b) { return V{a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]}; };
    const auto dot = [](const V& a, const V& b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; };
    const V d{ray.x, ray.y, ray.z};
    std::optional<SurfaceHit> best;
    float nearest = std::numeric_limits<float>::max();
    for (std::size_t i = 0; i + 2 < mesh.size(); ++i) {  // Moeller-Trumbore on the strip, from the eye at the origin
        const auto& a = mesh[i]; const auto& b = mesh[i + 1]; const auto& c = mesh[i + 2];
        const V e1 = sub(b.world, a.world), e2 = sub(c.world, a.world), n = cross(d, e2);
        const float det = dot(e1, n);
        if (std::fabs(det) < 1e-9f) continue;
        const V o{-a.world.x, -a.world.y, -a.world.z};
        const float s = dot(o, n) / det; if (s < 0 || s > 1) continue;
        const V q = cross(o, e1);
        const float t = dot(d, q) / det; if (t < 0 || s + t > 1) continue;
        const float along = dot(e2, q) / det;
        if (along <= 0 || along >= nearest) continue;
        nearest = along;
        const float r = 1.f - s - t;
        best = SurfaceHit{r * a.u + s * b.u + t * c.u, r * a.v + s * b.v + t * c.v, {ray.x * along, ray.y * along, ray.z * along}};
    }
    return best;
}

float stereo_sample(const PlaneF& p, float u, float v) {
    const float x = u * p.w - 0.5f, y = v * p.h - 0.5f;
    const int x0 = int(std::floor(x)), y0 = int(std::floor(y));
    const float fx = x - x0, fy = y - y0;
    const auto at = [&](int a, int b) { return p.at(std::clamp(a, 0, p.w - 1), std::clamp(b, 0, p.h - 1)); };
    return (at(x0, y0) * (1 - fx) + at(x0 + 1, y0) * fx) * (1 - fy) + (at(x0, y0 + 1) * (1 - fx) + at(x0 + 1, y0 + 1) * fx) * fy;
}

std::array<float, 3> stereo_sample(const RgbF& p, float u, float v) {
    const float x = u * p.w - 0.5f, y = v * p.h - 0.5f;
    const int x0 = int(std::floor(x)), y0 = int(std::floor(y));
    const float fx = x - x0, fy = y - y0;
    std::array<float, 3> out{};
    for (int c = 0; c < 3; ++c) {
        const auto at = [&](int a, int b) { return p.px[(std::size_t(std::clamp(b, 0, p.h - 1)) * p.w + std::clamp(a, 0, p.w - 1)) * 3 + c]; };
        out[std::size_t(c)] = (at(x0, y0) * (1 - fx) + at(x0 + 1, y0) * fx) * (1 - fy) + (at(x0, y0 + 1) * (1 - fx) + at(x0 + 1, y0 + 1) * fx) * fy;
    }
    return out;
}

PlaneF stereo_text_mask(const RgbF& colour, int w, int h) {
    PlaneF out{w, h, std::vector<float>(std::size_t(w) * h)};
    const float step_x = std::max(1.f / float(colour.w), 1.f / (float(w) * kStereoTextCols));
    const float step_y = 1.f / (float(h) * kStereoTextRows);
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        const float cu = (float(x) + 0.5f) / float(w), cv = (float(y) + 0.5f) / float(h);
        float jumps = 0;
        for (int r = 0; r < kStereoTextRows; ++r) {
            const float v = cv + (float(r) - 1.f) * step_y;
            float before = 0, last = 0;
            for (int c = 0; c < kStereoTextCols; ++c) {
                const auto s = stereo_sample(colour, cu + (float(c) - 3.5f) * step_x, v);
                const float luma = 0.25f * s[0] + 0.5f * s[1] + 0.25f * s[2];  // the same whichever way round R and B are
                const float d = luma - before, way = d > 0 ? 1.f : -1.f;
                if (c && std::fabs(d) > kStereoTextEdge && way != last) { jumps += 1; last = way; }
                before = luma;
            }
        }
        out.px[std::size_t(y) * w + x] = stereo_text_score(jumps / float(kStereoTextRows));
    }
    return out;
}

PlaneF stereo_guide(const PlaneF& depth, float lo, float hi, const PlaneF* text, const PlaneF* previous, const StereoParams& p, float aspect,
                    float alpha) {
    const auto params = stereo_clamped(p);
    PlaneF out{depth.w, depth.h, std::vector<float>(depth.px.size())};
    for (int y = 0; y < depth.h; ++y) for (int x = 0; x < depth.w; ++x) {
        float v = stereo_v(stereo_nearness(depth.at(x, y), lo, hi), params);
        if (text) {
            const auto mask = [&](int a, int b) { return text->at(std::clamp(a, 0, text->w - 1), std::clamp(b, 0, text->h - 1)); };
            // Two rows up and down: the space between two lines of text is no reason for the plane to come back.
            float t = 0;
            for (int j = -3; j <= 3; ++j) for (int i = -2; i <= 2; ++i) {
                const float w = (std::abs(i) < 2 ? 1.f : 0.5f) * (std::abs(j) < 3 ? 1.f : 0.5f);
                t = std::max(t, mask(x + i, y + j) * w);
            }
            v *= 1.f + (params.text_keep - 1.f) * t;
        }
        v *= stereo_edge_gain((float(x) + 0.5f) / float(depth.w), (float(y) + 0.5f) / float(depth.h), aspect);
        if (previous) v = stereo_temporal(previous->at(x, y), v, alpha);
        out.px[std::size_t(y) * depth.w + x] = v;
    }
    return out;
}

RgbF stereo_warp(const RgbF& colour, const PlaneF& guide, int out_w, int out_h, int eye, const StereoBudget& budget) {
    RgbF out{out_w, out_h, std::vector<float>(std::size_t(out_w) * out_h * 3)};
    const float side = stereo_side(eye), gn = budget.near_half, gf = budget.far_half;
    const float span = gn + gf, margin = 1.5f * float(out_w) / float(guide.w), du = 1.f / float(out_w), dv = 1.f / float(out_h);
    for (int y = 0; y < out_h; ++y) for (int x = 0; x < out_w; ++x) {
        const float u = (float(x) + 0.5f) * du, v = (float(y) + 0.5f) * dv;
        const auto g_at = [&](float o) { return stereo_half_disparity(stereo_sample(guide, u - side * o * du, v), budget); };
        float a = gn, ga = g_at(a), b = a, gb = ga;
        const float step = span / float(kStereoMarchSteps);
        bool hit = false;
        for (int i = 1; i <= kStereoMarchSteps; ++i) {
            b = gn - step * float(i); gb = g_at(b);
            if (b - gb <= 0.f) { hit = true; break; }
            a = b; ga = gb;
        }
        if (!hit) { b = -gf; gb = g_at(b); }
        for (int i = 0; i < kStereoBisectSteps; ++i) {
            const float m = 0.5f * (a + b), gm = g_at(m);
            if (m - gm > 0.f) { a = m; ga = gm; } else { b = m; gb = gm; }
        }
        const float ha = a - ga, hb = b - gb, gap = a - b;
        const float o = gap > 1e-6f && ha - hb > 1e-6f ? a - gap * ha / (ha - hb) : b;
        auto c = stereo_sample(colour, u - side * o * du, v);
        const float steep = gap > 1e-6f ? stereo_smoothstep(kStereoHoleLo, kStereoHoleHi, (gb - ga) / gap) : 0.f;
        if (steep > 0.f) {
            // Search on both sides and keep only the farther surface. Vertical smoothing
            // taps must belong to that same background, never the foreground above/below.
            float q = o + margin, far = g_at(q);
            for (const float candidate : {o - margin, a, b}) {
                const float g = g_at(candidate);
                if (g < far) { q = candidate; far = g; }
            }
            const float fu = u - side * q * du;
            std::array<float, 3> fill{};
            float weight = 0;
            for (int j = -1; j <= 1; ++j) {
                const float fv = v + float(j) * 1.5f * dv;
                const float g = stereo_half_disparity(stereo_sample(guide, fu, fv), budget);
                const float w = (j == 0 ? 2.f : 1.f) * (1.f - stereo_smoothstep(0.02f * span, 0.06f * span, g - far));
                const auto sample = stereo_sample(colour, fu, fv);
                for (std::size_t k = 0; k < 3; ++k) fill[k] += sample[k] * w;
                weight += w;
            }
            const float band = steep * stereo_smoothstep(0.f, 1.f, std::fabs(g_at(o - margin) - g_at(o + margin)));
            for (std::size_t k = 0; k < 3; ++k) c[k] += (fill[k] / std::max(weight, 1e-6f) - c[k]) * band;
        }
        auto* p = &out.px[(std::size_t(y) * out_w + x) * 3];
        p[0] = c[0]; p[1] = c[1]; p[2] = c[2];
    }
    return out;
}
}
