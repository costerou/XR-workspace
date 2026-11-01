#pragma once
// Overview / zoom layout of the managed desktops in the glasses view. Pure and offline-testable.
// zoom 0 = overview (all desktops side by side with `spacing` gaps and margins);
// zoom 1 = the desktop at `position` fills the view, neighbours continue to either side.
// Sources may differ in size: all tiles share one height and keep their own aspect, so resolution
// affects sharpness, not apparent size.
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace xr {
struct FRect { float x = 0, y = 0, width = 0, height = 0; };
struct Size { float w, h; };  // a source desktop's pixel size
constexpr std::uint32_t kMaxSpacing = 400;

// Empty if there are no sources, the view is empty, or any source has a non-positive size.
// Overview: the largest shared height whose widths plus spacing gaps and margins fit view_w, <= view_h.
std::vector<FRect> overview_layout(std::span<const Size> sources, float view_w, float view_h, float spacing);
// Zoomed: the tile at `position` fits the view; neighbours share its height, `spacing` apart. A fractional
// position blends the height and puts lerp(center[floor p], center[ceil p], frac p) at the view centre.
std::vector<FRect> zoomed_layout(std::span<const Size> sources, float view_w, float view_h, float spacing, float position);
// Linear blend between the two (ViewAnimator supplies eased values); `zoom` clamped to [0,1].
std::vector<FRect> blended_layout(std::span<const Size> sources, float view_w, float view_h, float spacing, float zoom, float position);
float smoothstep(float t);
float smootherstep(float t);  // quintic: zero velocity and acceleration at both ends

// Time-based animation toward targets (no frame-rate dependence).
// Zoom and spacing ease with smoothstep over `duration_ms`. Switching desktops (position) has its
// own gentle ease-in/ease-out over `move_ms`; pressing again mid-move keeps the current speed
// (capped so it never overshoots) instead of stopping and restarting.
class ViewAnimator {
public:
    explicit ViewAnimator(float duration_ms = 220.f, float move_ms = 300.f) : duration_(duration_ms), move_(move_ms) {}
    void set_target(bool zoomed, std::uint32_t selected, std::uint32_t spacing, double now_ms);
    void advance(double now_ms);
    bool animating() const { return t_ < 1.f || move_t_ < 1.f; }
    float zoom() const;      // eased current zoom in [0,1]
    float position() const;  // eased current position
    float velocity() const;  // position units per ms
    float spacing() const;
    std::uint32_t selected() const { return to_selected_; }
private:
    float duration_, move_;
    double start_ms_ = 0, move_start_ms_ = 0;
    float t_ = 1.f, move_t_ = 1.f;
    float from_zoom_ = 0, to_zoom_ = 0, from_pos_ = 0, from_vel_ = 0, from_spacing_ = 40, to_spacing_ = 40;
    std::uint32_t to_selected_ = 0;
};
}
