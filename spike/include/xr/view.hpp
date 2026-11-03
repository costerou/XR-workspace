#pragma once
// Overview / zoom layout of the managed desktops in the glasses view. Pure and offline-testable.
// zoom 0 = overview (all desktops side by side with `spacing` gaps and margins);
// zoom 1 = the desktop at `position` fills the view, neighbours continue to either side.
// Sources may differ in size: all tiles share one height and keep their own aspect, so resolution
// affects sharpness, not apparent size.
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
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

// Per-desktop placement in the glasses view (wire/profile units: tools/native_spike/geometry.py PLACEMENT_BOUNDS).
// Relative to the desktop's slot in the overview/zoom layout; the default places it at its slot.
//   x, y       orbit round the eye, radians: x turns the placed desktop round you about the vertical (+x right, +-pi is
//              directly behind you), y lifts it along its own meridian (+y up, +-pi/2 straight overhead / underfoot).
//   distance   factor on the slot's distance (1 = as far as the slot, 2 = twice as far and half the apparent size)
//   curvature  horizontal cylindrical arc (radians) subtended by the desktop's width; 0 = flat. The arc length equals the
//              flat width and the centre tangent stays in place, so pixels bend toward the viewer and never stretch.
// Every desktop always faces you, upright along its meridian (a billboard toward the eye, which in 3DoF is where your head
// is): at its slot, while dragged and anywhere round you it is square to the line of sight. There is no facing setting
// (yaw / pitch / roll were removed on 2026-11-02). place_point draws it straight ahead at the slot centre's distance times
// `distance`, then turns it by Az(a0 + x) * El(e0 + y), a0 / e0 the slot centre's azimuth / elevation
// (tools/native_spike/orbit.py place_point mirrors it exactly; the placement_contract ctest compares them).
struct Placement { float x = 0, y = 0, distance = 1, curvature = 0; };
struct Vec3 { float x = 0, y = 0, z = 0; };
// Canvas pixels map onto the plane z = -1 spanning tan(half FOV) (GL frame: +x right, +y up, -z forward).
struct Canvas { float view_w = 0, view_h = 0, half_w = 0, half_h = 0; };
// World position of canvas pixel (px, py) on the surface of a desktop laid out at `tile` with `placement`.
// Points outside the tile (its highlight frame/halo) continue on the same plane or cylinder. A centred tile at distance 1
// with no orbit is exactly the canvas plane z = -1; an off-centre one turns to face you (its centre stays on the plane).
Vec3 place_point(const Canvas& canvas, const FRect& tile, const Placement& placement, float px, float py);
// Triangle-strip mesh drawing canvas rect `rect` on that surface: per column (left to right) a top then a bottom
// vertex. One column when flat; more as curvature grows. u,v = position within `tile` (0..1 on the tile itself).
struct MeshVertex { Vec3 world; float u = 0, v = 0, px = 0, py = 0; };
std::vector<MeshVertex> tile_mesh(const Canvas& canvas, const FRect& tile, const Placement& placement, const FRect& rect);
// Zooming into a desktop brings it to distance 1 (it fills the view): weight 1 removes its orbit and distance (curvature is
// part of its shape and is kept). zoom_weight is zoom times closeness of `position` to `index`.
float zoom_weight(float zoom, float position, std::size_t index);
Placement faded(const Placement& placement, float weight);
// The placements the desktops are drawn with at `zoom` / `position` (ViewAnimator's eased values), `overview` being the
// settled overview layout (where the orbits were dragged) and `tiles` the blended layout being drawn. Zooming goes to the
// desktop where it is: the zoomed desktop keeps the direction round you it has in the overview (its slot plus its orbit)
// all the way in, while its distance fades to 1 (faded(placement, zoom_weight(...))) so it grows to fill the view there.
// The others follow it as its row: at full zoom each is at its zoomed-row slot turned by the zoomed desktop's direction,
// keeping its distance and curvature. Between two desktops (fractional position) the direction blends the short way round.
// zoom 0 (the overview): `placements` unchanged. `placements` shorter than `tiles` leaves the rest at the default.
std::vector<Placement> zoomed_placements(const Canvas& canvas, std::span<const FRect> overview, std::span<const FRect> tiles,
                                         std::span<const Placement> placements, float zoom, float position);
// Back-to-front draw order by distance of each desktop's centre (painter's algorithm; intersecting desktops
// are not split). `placements` shorter than `tiles` leaves the rest at the default.
std::vector<std::size_t> draw_order(const Canvas& canvas, std::span<const FRect> tiles, std::span<const Placement> placements);

// Overview corner-resize handle (tools/native_spike/resize.py): an L-shaped bracket inside corner `corner` (0 tl, 1 tr,
// 2 bl, 3 br) of `tile`, as two canvas rects (horizontal arm, vertical arm) drawn on the desktop's own surface. No pixel
// orbit or drift. Empty tile: two empty rects. Levels are grey on 0..1; both stay at or under the cards' cap of 191/255
// (micro-OLED retention), and the supervisor hides the handle after 30 s without pointer movement.
struct CornerHandle { FRect horizontal, vertical; };
CornerHandle corner_handle(const FRect& tile, std::uint32_t corner);
constexpr float kHandleLevelIdle = 140.f / 255.f, kHandleLevelActive = 184.f / 255.f;
// The overview's drag handles are the glass pills under each desktop (glass.hpp glass_pill), which also open its placement panel.
// Placement easing: the drawn placement approaches the latest one with time constant `tau_ms` (exponential, frame-rate
// independent) and lands on it exactly once within a hair, so drags at the pointer's ~30-60 Hz look smooth at 120 Hz.
// The orbit's x eases the short way round, so a desktop crossing behind you never swings back through the front.
Placement ease_toward(const Placement& from, const Placement& to, float dt_ms, float tau_ms);
bool same_placement(const Placement& a, const Placement& b);
constexpr float kPlacementEaseMs = 35.f;
// Overview free pointer (tools/native_spike/free_pointer.py): a small dim dot on the overview's plane (the canvas, z = -1),
// world-locked like the desktops' slots, and the overview's only pointer: in a desktop too (overview_cursor.py hides
// the real one). A grey disc inside a black ring, so it reads on bright content without being bright itself. Levels at
// or under the cards' 191/255 cap (micro-OLED retention); the supervisor hides it after 30 s without pointer movement.
constexpr float kFreePointerLevel = 170.f / 255.f, kFreePointerHeldLevel = 184.f / 255.f;
constexpr float kFreePointerRadiusPx = 6.f, kFreePointerRingPx = 2.f;
constexpr int kFreePointerSegments = 20;
// Triangle fan (centre, then segments + 1 rim points, closed) of a disc of `radius` canvas px around canvas (x, y), on the
// canvas plane. Empty if the canvas is empty, radius <= 0 or segments < 3.
std::vector<Vec3> canvas_disc(const Canvas& canvas, float x, float y, float radius, int segments = kFreePointerSegments);
// The supervisor's free-pointer hit testing needs the overview's layout: one status line
// {"overview_geometry":{"w":W,"h":H,"half":[half_w,half_h],"tiles":[[x,y,w,h],...]}} with canvas px to 2 decimals and the
// half-FOV tangents to 5 (tools/native_spike/free_pointer.py parse_overview_geometry). Empty if anything is unusable.
std::string overview_geometry_json(std::span<const FRect> tiles, std::uint32_t width, std::uint32_t height, float half_w, float half_h);
constexpr float kHandleInsetPx = 3.f, kHandleThicknessPx = 3.f, kHandleMinArmPx = 8.f, kHandleMaxArmPx = 36.f, kHandleArmShare = 0.1f;

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
    float target_spacing() const { return to_spacing_; }  // where spacing() is heading (the overview's settled layout)
    std::uint32_t selected() const { return to_selected_; }
private:
    float duration_, move_;
    double start_ms_ = 0, move_start_ms_ = 0;
    float t_ = 1.f, move_t_ = 1.f;
    float from_zoom_ = 0, to_zoom_ = 0, from_pos_ = 0, from_vel_ = 0, from_spacing_ = 40, to_spacing_ = 40;
    std::uint32_t to_selected_ = 0;
};
}
