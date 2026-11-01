#include "xr/view.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
constexpr std::uint32_t kMaxIndex = 2;  // XR-1..XR-3
float clampf(float v, float lo, float hi) { return std::max(lo, std::min(hi, v)); }
float lerp(float a, float b, float t) { return a + (b - a) * t; }
void fit(float view_h, float src_w, float src_h, float max_w, float& w, float& h) {
    w = std::max(0.f, max_w); h = src_w > 0 ? w * src_h / src_w : 0;
    if (h > view_h && src_h > 0) { h = view_h; w = h * src_w / src_h; }
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

std::vector<FRect> overview_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing) {
    std::vector<FRect> out;
    if (!count || view_w <= 0 || view_h <= 0 || src_w <= 0 || src_h <= 0) return out;
    const float s = clampf(spacing, 0, float(kMaxSpacing)), n = float(count);
    float w, h; fit(view_h, src_w, src_h, (view_w - (n + 1) * s) / n, w, h);
    const float x0 = (view_w - (n * w + (n - 1) * s)) / 2;  // centred; equals s when width-limited
    for (std::size_t i = 0; i < count; ++i) out.push_back({x0 + float(i) * (w + s), (view_h - h) / 2, w, h});
    return out;
}

std::vector<FRect> zoomed_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing, float position) {
    std::vector<FRect> out;
    if (!count || view_w <= 0 || view_h <= 0 || src_w <= 0 || src_h <= 0) return out;
    const float s = clampf(spacing, 0, float(kMaxSpacing));
    float w, h; fit(view_h, src_w, src_h, view_w, w, h);
    for (std::size_t i = 0; i < count; ++i) out.push_back({(view_w - w) / 2 + (float(i) - position) * (w + s), (view_h - h) / 2, w, h});
    return out;
}

std::vector<FRect> blended_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing,
                                  float zoom, float position) {
    auto a = overview_layout(count, view_w, view_h, src_w, src_h, spacing);
    auto b = zoomed_layout(count, view_w, view_h, src_w, src_h, spacing, position);
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
