#pragma once
// Overview / zoom layout of the managed desktops in the glasses view. Pure and offline-testable.
// zoom 0 = overview (all desktops side by side with `spacing` gaps and margins);
// zoom 1 = the desktop at `position` fills the view, neighbours continue to either side.
#include <cstddef>
#include <cstdint>
#include <vector>

namespace xr {
struct FRect { float x = 0, y = 0, width = 0, height = 0; };
constexpr std::uint32_t kMaxSpacing = 400;

std::vector<FRect> overview_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing);
std::vector<FRect> zoomed_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing, float position);
// Linear blend between the two (ViewAnimator supplies eased values); `zoom` clamped to [0,1].
std::vector<FRect> blended_layout(std::size_t count, float view_w, float view_h, float src_w, float src_h, float spacing,
                                  float zoom, float position);
float smoothstep(float t);

// Time-based animation toward targets (no frame-rate dependence).
class ViewAnimator {
public:
    explicit ViewAnimator(float duration_ms = 220.f) : duration_(duration_ms) {}
    void set_target(bool zoomed, std::uint32_t selected, std::uint32_t spacing, double now_ms);
    void advance(double now_ms);
    bool animating() const { return t_ < 1.f; }
    float zoom() const;      // eased current zoom in [0,1]
    float position() const;  // eased current position
    float spacing() const;
    std::uint32_t selected() const { return to_selected_; }
private:
    float duration_;
    double start_ms_ = 0;
    float t_ = 1.f;
    float from_zoom_ = 0, to_zoom_ = 0, from_pos_ = 0, from_spacing_ = 40, to_spacing_ = 40;
    std::uint32_t to_selected_ = 0;
};
}
