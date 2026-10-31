#include "xr/view.hpp"
#include <algorithm>

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
    from_zoom_ = zoom(); from_pos_ = position(); from_spacing_ = this->spacing();
    to_zoom_ = zoomed ? 1.f : 0.f;
    to_selected_ = std::min(selected, kMaxIndex);
    to_spacing_ = float(std::min(spacing, kMaxSpacing));
    start_ms_ = now_ms; t_ = 0.f;
}
void ViewAnimator::advance(double now_ms) {
    if (t_ >= 1.f) return;
    t_ = duration_ > 0 ? clampf(float((now_ms - start_ms_) / duration_), 0, 1) : 1.f;
}
float ViewAnimator::zoom() const { return lerp(from_zoom_, to_zoom_, smoothstep(t_)); }
float ViewAnimator::position() const { return lerp(from_pos_, float(to_selected_), smoothstep(t_)); }
float ViewAnimator::spacing() const { return lerp(from_spacing_, to_spacing_, smoothstep(t_)); }
}
