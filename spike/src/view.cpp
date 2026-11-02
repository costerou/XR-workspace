#include "xr/view.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
constexpr std::uint32_t kMaxIndex = kMaxDesktops - 1;  // XR-1..XR-kMaxDesktops
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
