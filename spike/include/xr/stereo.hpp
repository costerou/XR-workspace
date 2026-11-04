#pragma once
// Global immersive 3D, the maths (docs/stereo-render.md). Every number the shaders in stereo_gl.cpp compute is defined here
// first, and the CPU mirrors at the end run the same steps on plain float images: that is what the render tests compare
// the GL passes against, and what a machine without float render targets falls back to in tests. No GL in this file.
//
// Units. A disparity is measured between the two eyes' views, in pixels of one eye's image, positive = nearer than the
// screen plane (crossed), so each eye's image moves half of it: the left eye's near pixels to the right, the right eye's to
// the left. The guide map (the disparity map the warp reads) stores v in -1..1: the fraction of the comfort limit in use,
// +1 the near limit and -1 the far limit, which makes it independent of the resolution it is shown at.
#include "xr/depth.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <array>
#include <optional>
#include <vector>

namespace xr {
struct StereoParams {
    float strength = 0.6f;     // 0 flat .. 2; 1 = the frame's nearest and farthest things reach the comfort limits
    float convergence = 0.4f;  // the nearness (0 far .. 1 near) that stays on the screen plane
    float near_deg = 1.0f;     // comfort limit: largest crossed disparity, degrees of visual angle
    float far_deg = 0.75f;     // comfort limit: largest uncrossed disparity; stays under the eyes' divergence point
    float text_keep = 0.15f;   // what is left of the parallax on small text, 0..1
};
// Hard ceilings: no setting, saved profile or wire value gets past these. 1.5 / 1.0 degrees: the usual 3D display
// comfort range; a virtual image at 4 m has about 0.93 degrees before the eyes must diverge (63 mm IPD).
inline constexpr float kStereoStrengthMax = 2.f, kStereoNearDegMax = 1.5f, kStereoFarDegMax = 1.0f;
StereoParams stereo_clamped(const StereoParams& p);  // in range; NaN or infinity: that field's default
StereoParams stereo_from_wire(const ImmersiveParams& w);  // the whole numbers of an `immersive` frame, clamped

// The whole view's comfort limits in pixels of an eye image eye_w wide, seen through tan(half FOV) half_w.
struct StereoLimits { float near_px = 0, far_px = 0; };
StereoLimits stereo_limits(const StereoParams& p, float eye_w, float half_w);

// Nearness (raw depth, already mapped through lo / hi to 0..1) -> v. A knee from |v| = kStereoKnee keeps peaks from clipping
// to a flat shelf. Non-finite depth: 0, on the screen plane. Mirrors kGuideFragment.
inline constexpr float kStereoKnee = 0.7f;
float stereo_v(float nearness, const StereoParams& p);
float stereo_nearness(float raw, float lo, float hi);  // lo..hi -> 0..1, clamped; non-finite stays non-finite for stereo_v
float stereo_smoothstep(float a, float b, float x);
// v is tapered to 0 over this fraction of a desktop's width (and the same pixels of its height) from each edge: nothing may
// cross the desktop's frame, whose border, level bar and pill stay on its plane.
inline constexpr float kStereoEdgeTaper = 0.05f;
float stereo_edge_gain(float u, float v, float aspect);  // aspect = width / height
// A change of v above kStereoCut within one depth frame is new content, not noise: the filter lets go of its history.
inline constexpr float kStereoCutLo = 0.1f, kStereoCut = 0.35f;
float stereo_temporal(float previous, float current, float alpha);
float stereo_alpha(float dt_ms);  // 60 ms time constant, so the filter does not depend on the depth frame rate

// Small text. Eight taps across each of three rows of a guide texel's footprint count how often luma turns back by more than
// kStereoTextEdge: strokes go up and down several times in a texel, a photo's edge goes one way, once. Returns 0 (none) .. 1
// (certainly text), from the mean over the rows.
inline constexpr int kStereoTextCols = 8, kStereoTextRows = 3;
inline constexpr float kStereoTextEdge = 0.1f, kStereoTextLo = 0.75f, kStereoTextHi = 1.5f;
float stereo_text_score(float mean_jumps_per_row);

// Desktops' real depth. The canvas plane z = -1 is the screen plane: the eyes sit `b` either side of the axis in canvas
// units and converge there, so a desktop at distance 1 has no disparity and one further away sits behind the screen. b is half
// the IPD over the physical distance of that plane: 31.5 mm / 4 m by default.
inline constexpr float kStereoHalfBaseline = 0.0079f;
float stereo_side(int eye);                 // 0 (left) -> +1, 1 (right) -> -1: the sign of a near pixel's shift
// Each eye's clip-space x is (q.x + side b (1 + q.z)) / half_w, q = view * point (XR_PROJECT in stereo_gl.hpp). The disparity of a
// point at view depth z (< 0):
float stereo_plane_disparity(float b, float z, float eye_w, float half_w);
// b, cut so that no desktop's centre lies beyond the limits. `depths` are view z of the centres.
float stereo_safe_baseline(float b, const std::vector<float>& depths, const StereoLimits& limits, float eye_w, float half_w);
// The background is at infinity, as far as the far limit lets it be: each eye's constant shift in NDC (the eye's view of a point at
// infinity moves by -side times this).
float stereo_infinity_shift(float b, const StereoLimits& limits, float eye_w, float half_w);

// What is left for the depth effect on one desktop: the sum of its plane's disparity and the warp's stays inside the limits.
// `scale`: a small desktop gets proportionally less, as if it were a smaller window on the same scene.
inline constexpr float kStereoMinScale = 0.35f;
float stereo_tile_scale(float tile_w, float canvas_w, float distance);
struct StereoBudget { float near_half = 0, far_half = 0; };  // half disparities: what one eye's image may move, pixels
StereoBudget stereo_budget(const StereoLimits& limits, float plane_px, float scale, float gain);
float stereo_half_disparity(float v, const StereoBudget& b);  // v -> this eye's shift in pixels, signed

// On / off and a depth source that stopped: the effect eases out instead of snapping flat, and in again.
inline constexpr double kStereoFadeMs = 300, kStereoStaleMs = 500;
class StereoFader {
public:
    float update(double now_ms, bool on);  // 0..1, smoothstep of the elapsed fade
    bool moving() const { return moving_; }
private:
    double last_ms_ = 0; float t_ = 0; bool started_ = false, moving_ = false;
};

// Where a ray from the eye (world space, any length) meets a desktop: tile uv and the world point. Used for the pointer, which
// must sit on the surface it points at. Empty if it misses.
struct SurfaceHit { float u = 0, v = 0; Vec3 world; };
std::optional<SurfaceHit> stereo_surface_hit(const Canvas& canvas, const FRect& tile, const Placement& placement, const Vec3& ray);

// CPU mirrors. Row 0 is the top row; samples are bilinear with clamped edges, like the GL textures.
struct PlaneF { int w = 0, h = 0; std::vector<float> px; float at(int x, int y) const { return px[std::size_t(y) * w + x]; } };
struct RgbF { int w = 0, h = 0; std::vector<float> px; };
float stereo_sample(const PlaneF& p, float u, float v);
std::array<float, 3> stereo_sample(const RgbF& p, float u, float v);
// Mirrors kTextFragment: the text mask at w x h.
PlaneF stereo_text_mask(const RgbF& colour, int w, int h);
// Mirrors kGuideFragment: depth -> v, text, edge taper, temporal filter. `text` / `previous` may be null.
PlaneF stereo_guide(const PlaneF& depth, float lo, float hi, const PlaneF* text, const PlaneF* previous, const StereoParams& p, float aspect,
                    float alpha);
// Mirrors the warp in stereo_gl.cpp: `eye` of a desktop filling out_w x out_h from its colour and guide map.
inline constexpr int kStereoMarchSteps = 12, kStereoBisectSteps = 3;
inline constexpr float kStereoHoleLo = 0.8f, kStereoHoleHi = 1.6f;  // how steeply the surface stretches before it counts as a hole
RgbF stereo_warp(const RgbF& colour, const PlaneF& guide, int out_w, int out_h, int eye, const StereoBudget& budget);
}
