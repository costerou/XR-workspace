#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>

namespace xr {
namespace {
constexpr std::uint32_t kMaxIndex = 2;  // XR-1..XR-3
float clampf(float v, float lo, float hi) { return std::max(lo, std::min(hi, v)); }
float lerp(float a, float b, float t) { return a + (b - a) * t; }
bool valid(std::span<const Size> src, float view_w, float view_h) {
    return !src.empty() && view_w > 0 && view_h > 0 && std::all_of(src.begin(), src.end(), [](Size z) { return z.w > 0 && z.h > 0; });
}
// One row at a shared height h, `s` apart, first tile's left edge at x0, centred vertically.
std::vector<FRect> row(std::span<const Size> src, float view_h, float h, float s, float x0) {
    std::vector<FRect> out;
    for (auto z : src) { const float w = h * z.w / z.h; out.push_back({x0, (view_h - h) / 2, w, h}); x0 += w + s; }
    return out;
}
}
float smoothstep(float t) { t = clampf(t, 0, 1); return t * t * (3 - 2 * t); }
float smootherstep(float t) { t = clampf(t, 0, 1); return t * t * t * (t * (6 * t - 15) + 10); }
namespace {
float smootherstep_d(float t) { t = clampf(t, 0, 1); return 30 * t * t * (1 - t) * (1 - t); }
// Quintic Hermite basis carrying an initial velocity: value 0 at both ends, slope 1 at 0, slope and
// curvature 0 at 1. Equals s(1-s)^3(1+3s), non-negative on [0,1].
float carry(float t) { t = clampf(t, 0, 1); const float u = 1 - t; return t * u * u * u * (1 + 3 * t); }
float carry_d(float t) { t = clampf(t, 0, 1); const float u = 1 - t; return u * u * (1 - 3 * t) * (1 + 5 * t); }
}

std::vector<FRect> overview_layout(std::span<const Size> src, float view_w, float view_h, float spacing) {
    if (!valid(src, view_w, view_h)) return {};
    const float s = clampf(spacing, 0, float(kMaxSpacing)), n = float(src.size());
    float aspects = 0; for (auto z : src) aspects += z.w / z.h;
    const float h = std::min(view_h, std::max(0.f, view_w - (n + 1) * s) / aspects);
    const float w = h * aspects;
    return row(src, view_h, h, s, (view_w - (w + (n - 1) * s)) / 2);  // centred; margin s when width-limited
}

std::vector<FRect> zoomed_layout(std::span<const Size> src, float view_w, float view_h, float spacing, float position) {
    if (!valid(src, view_w, view_h)) return {};
    const float s = clampf(spacing, 0, float(kMaxSpacing));
    auto fit_h = [&](Size z) { return std::min(view_h, view_w * z.h / z.w); };
    // Neighbouring pair around position (the end pair when outside the row; then t extrapolates).
    const std::size_t last = src.size() - 1;
    const std::size_t lo = std::size_t(clampf(std::floor(position), 0, float(last ? last - 1 : 0)));
    const std::size_t hi = std::min(lo + 1, last);
    const float t = position - float(lo);
    auto out = row(src, view_h, lerp(fit_h(src[lo]), fit_h(src[hi]), clampf(t, 0, 1)), s, 0);
    auto centre = [&](std::size_t i) { return out[i].x + out[i].width / 2; };
    const float target = lo == hi ? centre(lo) + t * (out[lo].width + s) : lerp(centre(lo), centre(hi), t);
    for (auto& r : out) r.x += view_w / 2 - target;
    return out;
}

std::vector<FRect> blended_layout(std::span<const Size> src, float view_w, float view_h, float spacing, float zoom, float position) {
    auto a = overview_layout(src, view_w, view_h, spacing);
    auto b = zoomed_layout(src, view_w, view_h, spacing, position);
    const float t = clampf(zoom, 0, 1);
    for (std::size_t i = 0; i < a.size() && i < b.size(); ++i)
        a[i] = {lerp(a[i].x, b[i].x, t), lerp(a[i].y, b[i].y, t), lerp(a[i].width, b[i].width, t), lerp(a[i].height, b[i].height, t)};
    return a;
}

namespace {
constexpr float kMinCurvature = 1e-4f;  // below this the arc is numerically flat
}
Vec3 place_point(const Canvas& c, const FRect& tile, const Placement& pl, float px, float py) {
    if (c.view_w <= 0 || c.view_h <= 0) return {};
    const float sx = 2 * c.half_w / c.view_w, sy = 2 * c.half_h / c.view_h;  // world units per canvas pixel
    const float cx = tile.x + tile.width / 2, cy = tile.y + tile.height / 2;
    const float tw = tile.width * sx, th = tile.height * sy;
    const float d = pl.distance > 0 ? pl.distance : 1.f;
    const Vec3 centre{((2 * cx / c.view_w - 1) * c.half_w + pl.x * tw) * d, ((1 - 2 * cy / c.view_h) * c.half_h + pl.y * th) * d, -d};
    const float u = (px - cx) * sx, v = (cy - py) * sy;
    Vec3 local{u, v, 0};
    if (pl.curvature > kMinCurvature && tw > 0) {
        const float r = tw / pl.curvature, phi = u / r;
        local = {r * std::sin(phi), v, r * (1 - std::cos(phi))};  // toward the viewer at the edges
    }
    // R = Ry(yaw) * Rx(pitch) * Rz(roll)
    const float cr = std::cos(pl.roll), sr = std::sin(pl.roll);
    Vec3 a{cr * local.x - sr * local.y, sr * local.x + cr * local.y, local.z};
    const float cp = std::cos(pl.pitch), sp = std::sin(pl.pitch);
    Vec3 b{a.x, cp * a.y - sp * a.z, sp * a.y + cp * a.z};
    const float cyaw = std::cos(pl.yaw), syaw = std::sin(pl.yaw);
    Vec3 w{cyaw * b.x + syaw * b.z, b.y, -syaw * b.x + cyaw * b.z};
    return {centre.x + w.x, centre.y + w.y, centre.z + w.z};
}

std::vector<MeshVertex> tile_mesh(const Canvas& c, const FRect& tile, const Placement& pl, const FRect& rect) {
    if (tile.width <= 0 || tile.height <= 0 || rect.width <= 0 || rect.height <= 0) return {};
    // ~3 degrees of arc per column across the tile; the strip covers `rect` with the same density.
    const float span = rect.width / tile.width;
    const int columns = pl.curvature > kMinCurvature
        ? std::clamp(int(std::ceil(span * pl.curvature * 180.f / float(M_PI) / 3.f)), 4, 96) : 1;
    std::vector<MeshVertex> out;
    out.reserve(std::size_t(columns + 1) * 2);
    for (int i = 0; i <= columns; ++i) {
        const float px = rect.x + rect.width * float(i) / float(columns);
        for (const float py : {rect.y, rect.y + rect.height})
            out.push_back({place_point(c, tile, pl, px, py), (px - tile.x) / tile.width, (py - tile.y) / tile.height, px, py});
    }
    return out;
}

float zoom_weight(float zoom, float position, std::size_t index) {
    return clampf(zoom, 0, 1) * std::max(0.f, 1 - std::abs(position - float(index)));
}

Placement faded(const Placement& p, float weight) {
    const float k = 1 - clampf(weight, 0, 1);
    return {p.x * k, p.y * k, lerp(1.f, p.distance, k), p.yaw * k, p.pitch * k, p.roll * k, p.curvature};
}

std::vector<std::size_t> draw_order(const Canvas& c, std::span<const FRect> tiles, std::span<const Placement> placements) {
    std::vector<std::size_t> order(tiles.size());
    std::vector<float> depth(tiles.size());
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        order[i] = i;
        const auto& t = tiles[i];
        const Vec3 p = place_point(c, t, i < placements.size() ? placements[i] : Placement{}, t.x + t.width / 2, t.y + t.height / 2);
        depth[i] = p.x * p.x + p.y * p.y + p.z * p.z;
    }
    std::stable_sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) { return depth[a] > depth[b]; });
    return order;
}

CornerHandle corner_handle(const FRect& t, std::uint32_t corner) {
    if (t.width <= 0 || t.height <= 0 || corner > 3) return {};
    const float arm = clampf(kHandleArmShare * std::min(t.width, t.height), kHandleMinArmPx, kHandleMaxArmPx);
    const bool right = corner == 1 || corner == 3, bottom = corner >= 2;
    const float x0 = right ? t.x + t.width - kHandleInsetPx - arm : t.x + kHandleInsetPx;          // horizontal arm's left
    const float y0 = bottom ? t.y + t.height - kHandleInsetPx - arm : t.y + kHandleInsetPx;        // vertical arm's top
    const float hy = bottom ? t.y + t.height - kHandleInsetPx - kHandleThicknessPx : t.y + kHandleInsetPx;
    const float vx = right ? t.x + t.width - kHandleInsetPx - kHandleThicknessPx : t.x + kHandleInsetPx;
    return {{x0, hy, arm, kHandleThicknessPx}, {vx, y0, kHandleThicknessPx, arm}};
}

std::vector<Vec3> canvas_disc(const Canvas& c, float x, float y, float radius, int segments) {
    if (c.view_w <= 0 || c.view_h <= 0 || !(radius > 0) || segments < 3) return {};
    auto at = [&](float px, float py) { return Vec3{(2 * px / c.view_w - 1) * c.half_w, (1 - 2 * py / c.view_h) * c.half_h, -1.f}; };
    std::vector<Vec3> out{at(x, y)};
    for (int i = 0; i <= segments; ++i) {
        const float a = 2.f * float(M_PI) * float(i % segments) / float(segments);
        out.push_back(at(x + radius * std::cos(a), y + radius * std::sin(a)));
    }
    return out;
}

std::string overview_geometry_json(std::span<const FRect> tiles, std::uint32_t width, std::uint32_t height, float half_w, float half_h) {
    if (tiles.empty() || tiles.size() > 3 || !width || !height || width > 8192 || height > 8192 || !(half_w > 0) || !(half_h > 0) ||
        !std::isfinite(half_w) || !std::isfinite(half_h)) return {};
    char buf[96];
    std::snprintf(buf, sizeof buf, "{\"overview_geometry\":{\"w\":%u,\"h\":%u,\"half\":[%.5f,%.5f],\"tiles\":[", width, height,
                  double(half_w), double(half_h));
    std::string out = buf;
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        const auto& t = tiles[i];
        if (!std::isfinite(t.x) || !std::isfinite(t.y) || !(t.width > 0) || !(t.height > 0) || !std::isfinite(t.width) ||
            !std::isfinite(t.height) || std::fabs(t.x) > 8192 || std::fabs(t.y) > 8192 || t.width > 8192 || t.height > 8192) return {};
        std::snprintf(buf, sizeof buf, "%s[%.2f,%.2f,%.2f,%.2f]", i ? "," : "", double(t.x), double(t.y), double(t.width), double(t.height));
        out += buf;
    }
    return out + "]}}";
}

Placement ease_toward(const Placement& from, const Placement& to, float dt_ms, float tau_ms) {
    if (tau_ms <= 0) return to;
    if (!(dt_ms > 0)) return from;
    const float k = std::exp(-dt_ms / tau_ms);
    auto step = [&](float a, float b) { const float v = b + (a - b) * k; return std::fabs(v - b) < 2e-4f ? b : v; };
    return {step(from.x, to.x), step(from.y, to.y), step(from.distance, to.distance), step(from.yaw, to.yaw),
            step(from.pitch, to.pitch), step(from.roll, to.roll), step(from.curvature, to.curvature)};
}

bool same_placement(const Placement& a, const Placement& b) {
    return a.x == b.x && a.y == b.y && a.distance == b.distance && a.yaw == b.yaw && a.pitch == b.pitch && a.roll == b.roll &&
           a.curvature == b.curvature;
}

void ViewAnimator::set_target(bool zoomed, std::uint32_t selected, std::uint32_t spacing, double now_ms) {
    advance(now_ms);
    from_zoom_ = zoom(); from_spacing_ = this->spacing();
    to_zoom_ = zoomed ? 1.f : 0.f;
    selected = std::min(selected, kMaxIndex);
    if (selected != to_selected_) {  // only a desktop switch restarts the move; keep its momentum
        const float carried = velocity() * move_;  // read both before touching the curve
        from_pos_ = position();
        const float d = float(selected) - from_pos_;
        // Same direction: keep speed up to 2x the remaining distance (overshoot needs > 2.5x).
        // Reversing: bleed it off with at most a small drift past the current position.
        const float cap = (carried * d > 0 ? 2.f : 0.5f) * std::abs(d);
        from_vel_ = move_ > 0 ? std::clamp(carried, -cap, cap) / move_ : 0.f;
        to_selected_ = selected; move_start_ms_ = now_ms; move_t_ = 0.f;
    }
    to_spacing_ = float(std::min(spacing, kMaxSpacing));
    start_ms_ = now_ms; t_ = 0.f;
}
void ViewAnimator::advance(double now_ms) {
    if (t_ < 1.f) t_ = duration_ > 0 ? clampf(float((now_ms - start_ms_) / duration_), 0, 1) : 1.f;
    if (move_t_ < 1.f) move_t_ = move_ > 0 ? clampf(float((now_ms - move_start_ms_) / move_), 0, 1) : 1.f;
}
float ViewAnimator::zoom() const { return lerp(from_zoom_, to_zoom_, smoothstep(t_)); }
float ViewAnimator::position() const {
    if (move_t_ >= 1.f) return float(to_selected_);
    return lerp(from_pos_, float(to_selected_), smootherstep(move_t_)) + from_vel_ * move_ * carry(move_t_);
}
float ViewAnimator::velocity() const {
    if (move_t_ >= 1.f || move_ <= 0) return 0.f;
    return (float(to_selected_) - from_pos_) * smootherstep_d(move_t_) / move_ + from_vel_ * carry_d(move_t_);
}
float ViewAnimator::spacing() const { return lerp(from_spacing_, to_spacing_, smoothstep(t_)); }
}
