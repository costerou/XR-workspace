#pragma once
// Overview / zoom layout of the managed desktops in the glasses view. Pure and offline-testable.
// zoom 0 = overview (all desktops side by side with `spacing` gaps and margins);
// zoom 1 = the desktop at `position` fills the view, neighbours continue to either side.
// Sources may differ in size: all tiles share one height and keep their own aspect, so resolution
// affects sharpness, not apparent size.
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <vector>
#include "xr/desktops.hpp"

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
//   roll       levelling turn (radians, counter-clockwise as you see it) of the desktop about its own line of sight
//              (the eye to its centre), applied to the desktop drawn straight ahead before the orbit: it still faces you.
//              Not a wire placement setting: the supervisor sets it with the roll-level icons (ipc.hpp RollPayload).
struct Placement { float x = 0, y = 0, distance = 1, curvature = 0, roll = 0; };
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
// How far a placed desktop is rolled from level, as you see it: the signed angle (radians, counter-clockwise positive)
// from `up` (the world's up, gravity's opposite, as a vector in the canvas frame: pose.hpp canvas_up) projected onto the
// desktop's surface at its centre, to the desktop's own up. Rolling the desktop by r adds r. nullopt when the line of
// sight to its centre is within kTiltMinDeg of straight up or down (level means nothing there) or anything is degenerate.
constexpr float kTiltMinDeg = 10.f;
std::optional<float> desktop_tilt(const Canvas& canvas, const FRect& tile, const Placement& placement, const Vec3& up);
// Glass under a desktop (its pill and the placement panel, glass.hpp; canvas px laid out against the tile) keeps one
// apparent size whatever the desktop's size: a resize (its `distance`) moves it with the desktop's bottom edge but never
// scales it. Glass point (gx, gy) is drawn at the desktop surface point anchor + (g - anchor) * distance, anchor the
// tile's bottom-centre, so at distance 1 it is the tile's own px. tools/native_spike/free_pointer.py glass_to_desktop
// mirrors it (the placement_contract ctest compares them).
std::array<float, 2> glass_to_desktop(const FRect& tile, const Placement& placement, float gx, float gy);
// tile_mesh for glass rect `rect`: world points through glass_to_desktop; px / py stay glass px (u, v as tile_mesh's).
std::vector<MeshVertex> glass_mesh(const Canvas& canvas, const FRect& tile, const Placement& placement, const FRect& rect);
// Zooming into a desktop brings it to distance 1 (it fills the view): weight 1 removes its orbit and distance (curvature is
// part of its shape and is kept). zoom_weight is zoom times closeness of `position` to `index`.
float zoom_weight(float zoom, float position, std::size_t index);
Placement faded(const Placement& placement, float weight);
// The placements the desktops are drawn with at `zoom` / `position` (ViewAnimator's eased values), `overview` being the
// overview layout (where the orbits were dragged) and `tiles` the blended layout being drawn. Zooming never moves a
// desktop round you, in every viewing mode (anchor, locked, follow) and however far it was dragged:
//   - every desktop keeps its overview direction (slot plus orbit) at every zoom: its orbit is corrected for its slot's
//     motion between the overview and `tiles`;
//   - the zoomed desktop's distance fades to 1 (zoom_weight), so it grows where it is until it fills the view there;
//   - every other desktop also keeps its overview size: its distance scales with its tile, so it is drawn exactly as in
//     the overview (a uniform scale about the eye), curvature included.
// Nothing ever goes back to its default place or snaps straight ahead. zoom 0 (the overview): `placements` unchanged.
// `placements` shorter than `tiles` leaves the rest at the default.
std::vector<Placement> zoomed_placements(const Canvas& canvas, std::span<const FRect> overview, std::span<const FRect> tiles,
                                         std::span<const Placement> placements, float zoom, float position);
// Back-to-front draw order with the zoom target last (on top) once zoomed in past halfway (zoom >= 0.5): a desktop
// dragged nearer than the zoomed one never covers it. Below that, `order` unchanged. `target` outside it: unchanged.
std::vector<std::size_t> zoom_draw_order(std::vector<std::size_t> order, float zoom, std::size_t target);
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
// or in its direction round you when the frame gives one (direction_disc; the supervisor always does), world-locked like the desktops' slots, and the overview's only pointer: in a desktop too (overview_cursor.py hides
// the real one). A grey disc inside a black ring, so it reads on bright content without being bright itself. Levels at
// or under the cards' 191/255 cap (micro-OLED retention); the supervisor hides it after 30 s without pointer movement.
constexpr float kFreePointerLevel = 170.f / 255.f, kFreePointerHeldLevel = 184.f / 255.f;
constexpr float kFreePointerRadiusPx = 6.f, kFreePointerRingPx = 2.f;
constexpr int kFreePointerSegments = 20;
// Triangle fan (centre, then segments + 1 rim points, closed) of a disc of `radius` canvas px around canvas (x, y), on the
// canvas plane. Empty if the canvas is empty, radius <= 0 or segments < 3.
std::vector<Vec3> canvas_disc(const Canvas& canvas, float x, float y, float radius, int segments = kFreePointerSegments);
// The same disc drawn in direction (az, el) radians round you (az positive right, el positive up; the overview pointer
// frame's az / el): canvas_disc at (x, y) clamped into the view, turned about the eye by
// Az(az) . El(el - e) . Az(-a), (a, e) that clamped point's direction, so its centre lands on (az, el), it stays upright,
// and where (az, el) is that point's own direction it is canvas_disc exactly. Empty under canvas_disc's conditions.
std::vector<Vec3> direction_disc(const Canvas& canvas, float x, float y, float az, float el, float radius,
                                 int segments = kFreePointerSegments);
// The supervisor's free-pointer hit testing needs the overview's layout: one status line
// {"overview_geometry":{"w":W,"h":H,"half":[half_w,half_h],"tiles":[[x,y,w,h],...]}} with canvas px to 2 decimals and the
// half-FOV tangents to 5 (tools/native_spike/free_pointer.py parse_overview_geometry), one tile per desktop (1..kMaxDesktops).
// Empty if anything is unusable.
std::string overview_geometry_json(std::span<const FRect> tiles, std::uint32_t width, std::uint32_t height, float half_w, float half_h);
constexpr float kHandleInsetPx = 3.f, kHandleThicknessPx = 3.f, kHandleMinArmPx = 8.f, kHandleMaxArmPx = 36.f, kHandleArmShare = 0.1f;

// Time-based animation toward targets (no frame-rate dependence). Zoom and spacing ease with smoothstep over
// `duration_ms`; selecting another desktop in the overview just moves the highlight.
// Switching desktops while zoomed in (Ctrl+Alt+Left / Right, cursor follow) animates in three phases, in every viewing mode:
//   1. zoom out from the current desktop to the overview (duration_ms times the zoom it started from; position() stays on it);
//   2. turn: look() eases 0 -> 1 over `turn_ms` with smootherstep, the view turning from look_from() toward look_to()
//      (look_turn), the overview held (zoom 0);
//   3. zoom into the target (duration_ms) while look() eases back to 0, so it lands where it was dragged.
// The turn is a view (camera) rotation only: no placement ever changes, and at rest look() is 0. During phase 1 another
// switch retargets it; later ones queue and run once this one lands. Zooming out mid-switch zooms out from where the view
// is and eases any turn back to 0.
class ViewAnimator {
public:
    explicit ViewAnimator(float duration_ms = 220.f, float turn_ms = 300.f) : duration_(duration_ms), turn_(turn_ms) {}
    void set_target(bool zoomed, std::uint32_t selected, std::uint32_t spacing, double now_ms);
    void advance(double now_ms);
    bool animating() const { return t_ < 1.f || spacing_t_ < 1.f || look_t_ < 1.f || switch_.active; }
    bool switching() const { return switch_.active; }
    float zoom() const;      // eased current zoom in [0,1]
    float position() const { return float(shown_); }  // the desktop the zoom is on (the one being left in phase 1)
    std::uint32_t focus() const { return shown_; }
    float look() const;      // [0,1]: how far the view is turned from look_from() toward look_to()
    std::uint32_t look_from() const { return look_from_; }
    std::uint32_t look_to() const { return look_to_; }
    float spacing() const;
    float target_spacing() const { return to_spacing_; }  // where spacing() is heading (the overview's settled layout)
    std::uint32_t selected() const { return to_selected_; }
private:
    void start_switch(std::uint32_t from, std::uint32_t to, float zoom, double now_ms);
    float duration_, turn_;
    double start_ms_ = 0, spacing_start_ms_ = 0, look_start_ms_ = 0;
    float t_ = 1.f, spacing_t_ = 1.f, look_t_ = 1.f;
    float from_zoom_ = 0, to_zoom_ = 0, from_spacing_ = 40, to_spacing_ = 40, from_look_ = 0;
    std::uint32_t to_selected_ = 0, shown_ = 0, look_from_ = 0, look_to_ = 0;
    struct Switch {
        bool active = false, queued = false;
        double start_ms = 0;
        float zoom = 1, out_ms = 0;  // the zoom it started from; phase 1's length
        std::uint32_t from = 0, to = 0, next = 0;
        float zoom_now = 1, look_now = 0;
    } switch_;
};
// Where desktop `index` is round you: the direction {azimuth, elevation} (radians; +az right, +el up, place_point's) of
// its centre as placed in the overview (its slot in `overview` plus its orbit), which zoomed_placements keeps at every zoom.
// Follow and locked modes turn the canvas so the focused desktop's direction is straight ahead (pose.hpp aim_rotation,
// ModeController::set_aim). {0, 0} if `index` is out of range or the canvas is empty.
std::array<float, 2> focus_direction(const Canvas& canvas, std::span<const FRect> overview, std::span<const Placement> placements,
                                     std::size_t index);
// How far follow and locked modes centre the view on ViewAnimator::focus() (0 = the overview's own centre straight ahead,
// 1 = the focused desktop straight ahead): zoom() + look(), at most 1. Continuous through zooms and every phase of a
// switch: zooming out of the current desktop it eases to the overview's centre, the turn pans to the target (look 0 -> 1)
// and zooming in keeps the target centred (zoom + look = 1). In anchor mode the room stays put and look_turn turns instead.
float focus_weight(const ViewAnimator& view);
// The view turn of a desktop switch at `look` (ViewAnimator::look): a rotation applied to world points before the head's
// view (draw with view * look_turn), turning desktop `from`'s overview direction (slot in `overview` plus its orbit) toward
// `to`'s. At look 1 `to`'s direction comes exactly where `from`'s was, upright; at look 0 (and whenever an index is out of
// range) it is the identity. Row-major, GL frame.
std::array<float, 9> look_turn(const Canvas& canvas, std::span<const FRect> overview, std::span<const Placement> placements,
                               std::size_t from, std::size_t to, float look);
}
