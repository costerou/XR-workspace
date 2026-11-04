// Global immersive 3D, the maths and its CPU mirrors (xr/stereo.hpp), no GL: mapping, comfort limits, text safety, the warp's
// known answers (a flat map changes nothing, a constant one shifts exactly, a hole never shows the foreground).
#include "fake_depth.hpp"
#include "xr/stereo.hpp"
#include "xr/depth.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
constexpr float kNan = std::numeric_limits<float>::quiet_NaN(), kInf = std::numeric_limits<float>::infinity();

void params_are_clamped() {
    StereoParams p; p.strength = 99; p.convergence = -3; p.near_deg = 40; p.far_deg = kInf; p.text_keep = kNan;
    const auto c = stereo_clamped(p), d = StereoParams{};
    CHECK(c.strength == kStereoStrengthMax); CHECK(c.convergence == 0); CHECK(c.near_deg == kStereoNearDegMax);
    CHECK(c.far_deg == d.far_deg); CHECK(c.text_keep == d.text_keep);
    const auto l = stereo_limits(p, 1920, 0.44f);
    const float ppd = 1920.f / (2.f * std::atan(0.44f) * 180.f / 3.14159265f);
    CHECK(std::fabs(l.near_px - 1.5f * ppd) < 0.01f); CHECK(l.far_px <= 1.f * ppd);
    CHECK(stereo_limits(p, 0, 0.44f).near_px == 0);
}

void depth_to_v() {
    StereoParams p; p.strength = 1; p.convergence = 0.5f;
    CHECK(stereo_v(0.5f, p) == 0);                                  // convergence: the screen plane
    CHECK(stereo_v(0.6f, p) > 0 && stereo_v(0.4f, p) < 0);          // nearer pops out
    float before = -2; bool monotonic = true, bounded = true;
    for (int s = 0; s <= 2; ++s) for (float c : {0.f, 0.4f, 1.f}) {
        StereoParams q; q.strength = float(s); q.convergence = c; before = -2;
        for (int i = 0; i <= 1000; ++i) {
            const float v = stereo_v(float(i) / 1000.f, q);
            monotonic &= v >= before - 1e-6f; before = v; bounded &= std::fabs(v) <= 1.f;
        }
    }
    CHECK(monotonic); CHECK(bounded);
    p.strength = 2; p.convergence = 0;
    CHECK(stereo_v(1.f, p) > 0.99f);                                // the knee reaches the limit
    CHECK(stereo_v(kNan, p) == 0); CHECK(stereo_v(kInf, p) == 0);   // broken depth: flat
    CHECK(!std::isfinite(stereo_nearness(kNan, 0, 1)));
    CHECK(stereo_nearness(5, 0, 1) == 1); CHECK(stereo_nearness(-5, 0, 1) == 0);
    CHECK(stereo_nearness(150, 100, 200) == 0.5f);
    CHECK(stereo_nearness(7, 3, 3) == 0.5f);                        // a blank frame: no range, no depth
    p = {}; p.strength = 0;
    for (int i = 0; i <= 10; ++i) CHECK(stereo_v(float(i) / 10.f, p) == 0);
}

void contract() {
    float px = 0;
    DepthFrame f; f.w = 512; f.h = 288; f.pixels = &px;
    CHECK(depth_frame_valid(f));
    DepthFrame low = f; low.h = 255; CHECK(!depth_frame_valid(low));
    DepthFrame none = f; none.pixels = nullptr; CHECK(!depth_frame_valid(none));
    DepthFrame tex = none; tex.texture = 7; CHECK(depth_frame_valid(tex));
    DepthFrame narrow = f; narrow.stride = 4; CHECK(!depth_frame_valid(narrow));
}

void comfort() {
    const StereoLimits l{48, 36};
    // The plane's disparity and the warp's together stay inside the limits, whatever the plane does.
    for (float plane : {-60.f, -36.f, -10.f, 0.f, 20.f, 48.f, 90.f}) {
        const auto b = stereo_budget(l, plane, 1, 1);
        const float near = plane + 2 * b.near_half, far = plane - 2 * b.far_half;
        if (std::fabs(plane) <= 36) { CHECK(near <= 48.01f); CHECK(far >= -36.01f); }
        CHECK(b.near_half >= 0 && b.far_half >= 0);
    }
    CHECK(stereo_budget(l, 0, 1, 0).near_half == 0);                // faded out
    CHECK(stereo_budget(l, 0, 0.5f, 1).near_half == 12);            // half a view wide: half the depth
    CHECK(stereo_tile_scale(1920, 1920, 1) == 1);
    CHECK(stereo_tile_scale(960, 1920, 1) == 0.5f);
    CHECK(stereo_tile_scale(1920, 1920, 2) == 0.5f);
    CHECK(stereo_tile_scale(100, 1920, 3) == kStereoMinScale);
    // Real depth: sign, zero on the plane, and the eyes' sum at infinity under the far limit.
    CHECK(stereo_side(0) == 1 && stereo_side(1) == -1);
    CHECK(stereo_plane_disparity(kStereoHalfBaseline, -1, 1920, 0.44f) == 0);
    CHECK(stereo_plane_disparity(kStereoHalfBaseline, -0.5f, 1920, 0.44f) > 0);
    const float at_infinity = stereo_plane_disparity(kStereoHalfBaseline, -1e6f, 1920, 0.44f);
    CHECK(at_infinity < 0);
    const auto far_limit = stereo_limits({}, 1920, 0.44f);
    CHECK(stereo_infinity_shift(kStereoHalfBaseline, far_limit, 1920, 0.44f) * 1920 <= far_limit.far_px + 0.01f);  // the background: inside too
    CHECK(stereo_infinity_shift(0, far_limit, 1920, 0.44f) == 0);
    // The baseline gives way for a desktop that would break the limits.
    const auto limits = stereo_limits({}, 1920, 0.44f);
    const float b = stereo_safe_baseline(0.03f, {-1, -0.3f, -5}, limits, 1920, 0.44f);
    CHECK(b < 0.03f);
    CHECK(stereo_plane_disparity(b, -0.3f, 1920, 0.44f) <= limits.near_px * 1.001f);
    CHECK(-stereo_plane_disparity(b, -5, 1920, 0.44f) <= limits.far_px * 1.001f);
    CHECK(stereo_safe_baseline(0.001f, {-1, -2}, limits, 1920, 0.44f) == 0.001f);
    CHECK(stereo_half_disparity(1, {24, 18}) == 24); CHECK(stereo_half_disparity(-1, {24, 18}) == -18);
}

void edges_and_time() {
    CHECK(stereo_edge_gain(0, 0.5f, 1.6f) == 0); CHECK(stereo_edge_gain(1, 0.5f, 1.6f) == 0);
    CHECK(stereo_edge_gain(0.5f, 0, 1.6f) == 0); CHECK(stereo_edge_gain(0.5f, 0.5f, 1.6f) == 1);
    CHECK(stereo_alpha(0) == 1); CHECK(stereo_alpha(33) > 0.3f && stereo_alpha(33) < 0.5f);
    // Two 16 ms steps close to one 33 ms step: the filter does not depend on the depth rate.
    const float slow = stereo_temporal(0, 0.05f, stereo_alpha(33));
    const float fast = stereo_temporal(stereo_temporal(0, 0.05f, stereo_alpha(16.5f)), 0.05f, stereo_alpha(16.5f));
    CHECK(std::fabs(slow - fast) < 0.002f);
    CHECK(stereo_temporal(0.1f, 0.9f, stereo_alpha(33)) == 0.9f);   // a scene cut does not smear
    StereoFader f;
    CHECK(f.update(0, true) == 0);
    CHECK(f.update(kStereoFadeMs / 2, true) == 0.5f); CHECK(f.moving());
    CHECK(f.update(kStereoFadeMs, true) == 1); CHECK(!f.moving() || f.update(kStereoFadeMs + 1, true) == 1);
    CHECK(f.update(kStereoFadeMs * 3, false) == 0);
}

void text_mask() {
    // Text flagged, a photo-like gradient and a single hard edge not.
    constexpr int W = 960, H = 600, GW = 240, GH = 150;
    RgbF c = fake::box_picture(W, H);
    fake::stamp_text(c, 100, 40, 400, 180);
    const auto mask = stereo_text_mask(c, GW, GH);
    float inside = 0, outside = 0; int n_in = 0, n_out = 0;
    for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x) {
        const int px = x * 4 + 2, py = y * 4 + 2;
        if (px > 110 && px < 390 && py > 50 && py < 170) { inside += mask.at(x, y); ++n_in; }
        if (px > 500 || py > 220) { outside += mask.at(x, y); ++n_out; }
    }
    inside /= float(n_in); outside /= float(n_out);
    std::printf("text mask: %.2f on text, %.3f elsewhere\n", double(inside), double(outside));
    CHECK(inside > 0.6f); CHECK(outside < 0.02f);  // inside: the gaps between lines are not text; the guide widens the mask over them
    // Text keeps its parallax small; the same depth elsewhere keeps all of it.
    const auto depth = fake::flat_depth(GW, GH, 1.f);
    StereoParams p; p.strength = 1; p.convergence = 0.5f;
    const auto guide = stereo_guide(depth, 0, 1, &mask, nullptr, p, 1.6f, 1);
    const float on_text = guide.at(60, 25), free = guide.at(180, 120);
    CHECK(std::fabs(on_text) <= p.text_keep * free + 0.01f); CHECK(free > 0.9f);
}

RgbF channel_copy(const RgbF& c) { return c; }

void warp_answers() {
    constexpr int W = 320, H = 200;
    const auto colour = channel_copy(fake::textured(W, H));
    const StereoBudget budget{10, 8};
    // v = 0 everywhere: the picture, unchanged.
    const auto flat = stereo_warp(colour, fake::flat_depth(80, 50, 0.f), W, H, 0, budget);
    float worst = 0;
    for (std::size_t i = 0; i < colour.px.size(); ++i) worst = std::max(worst, std::fabs(flat.px[i] - colour.px[i]));
    CHECK(worst < 1e-3f);
    // v constant: both eyes shift by exactly the half disparity, opposite ways.
    for (const float v : {0.5f, -0.5f}) {
        const auto map = fake::flat_depth(80, 50, v);
        const auto left = stereo_warp(colour, map, W, H, 0, budget), right = stereo_warp(colour, map, W, H, 1, budget);
        const float g = stereo_half_disparity(v, budget);
        float err = 0; int n = 0;
        for (int y = 40; y < 160; y += 3) for (int x = 40; x < 280; x += 3) {
            const float expect_l = (float(x) + 0.5f - g) / float(W), expect_r = (float(x) + 0.5f + g) / float(W), t = (float(y) + 0.5f) / float(H);
            const auto a = stereo_sample(colour, expect_l, t), b = stereo_sample(colour, expect_r, t);
            for (std::size_t k = 0; k < 3; ++k) {
                err = std::max(err, std::fabs(left.px[(std::size_t(y) * W + x) * 3 + k] - a[k]));
                err = std::max(err, std::fabs(right.px[(std::size_t(y) * W + x) * 3 + k] - b[k]));
            }
            ++n;
        }
        CHECK(err < 0.02f); CHECK(n > 1000);
    }
    // A near box: it moves right in the left eye and left in the right eye, and where it left a hole there is only wall.
    const auto picture = fake::box_picture(W, H);
    auto map = fake::flat_depth(80, 50, -0.5f);
    for (int y = 50 / 3; y < 2 * 50 / 3; ++y) for (int x = 80 / 3; x < 2 * 80 / 3; ++x) map.px[std::size_t(y) * 80 + x] = 1.f;
    for (int eye = 0; eye < 2; ++eye) {
        const auto out = stereo_warp(picture, map, W, H, eye, budget);
        float brightest = 0; int red_lo = W, red_hi = -1;
        for (int x = 0; x < W; ++x) {
            const auto* p = &out.px[(std::size_t(H / 2) * W + x) * 3];
            brightest = std::max({brightest, p[0], p[1], p[2]});
            if (p[0] > 0.7f && p[1] < 0.3f) { red_lo = std::min(red_lo, x); red_hi = std::max(red_hi, x); }
        }
        const float shift = stereo_side(eye) * budget.near_half;
        CHECK(std::fabs(float(red_lo) - (float(W / 3) + shift)) <= 5.f);
        CHECK(std::fabs(float(red_hi) - (float(2 * W / 3) + shift)) <= 5.f);
        // The strip the box left: the wall's green, never red, on the trailing side (hole).
        const int hole_from = eye == 0 ? W / 3 - 6 : 2 * W / 3 + 6, hole_to = eye == 0 ? W / 3 + 2 : 2 * W / 3 - 2;
        for (int x = std::min(hole_from, hole_to); x <= std::max(hole_from, hole_to); ++x) {
            const auto* p = &out.px[(std::size_t(H / 2) * W + x) * 3];
            CHECK(p[0] < 0.25f); CHECK(p[1] > 0.3f);
        }
        CHECK(brightest <= 1.f);  // resampling adds no light
    }
    // Brighter than the source is impossible: the warp takes what is there.
    float source_max = 0, out_max = 0;
    const auto bright = stereo_warp(colour, fake::bump_depth(80, 50, 3), W, H, 1, budget);
    for (const float v : colour.px) source_max = std::max(source_max, v);
    for (const float v : bright.px) { out_max = std::max(out_max, v); CHECK(std::isfinite(v)); }
    CHECK(out_max <= source_max + 1e-5f);
}

void surface_hit() {
    const Canvas c{1920, 1200, 0.44f, 0.275f};
    const FRect tile{480, 300, 960, 600};
    const auto hit = stereo_surface_hit(c, tile, {}, {0, 0, -1});
    CHECK(hit.has_value());
    if (hit) { CHECK(std::fabs(hit->u - 0.5f) < 0.01f); CHECK(std::fabs(hit->v - 0.5f) < 0.01f); CHECK(std::fabs(hit->world.z + 1) < 0.01f); }
    Placement far; far.distance = 2;
    const auto further = stereo_surface_hit(c, tile, far, {0, 0, -1});
    CHECK(further && std::fabs(further->world.z + 2) < 0.01f);
    CHECK(!stereo_surface_hit(c, tile, {}, {0.9f, 0, -1}));
    CHECK(!stereo_surface_hit(c, tile, {}, {0, 0, 1}));   // behind you
    Placement curved; curved.curvature = 0.6f;
    CHECK(stereo_surface_hit(c, tile, curved, {0.05f, 0.02f, -1}).has_value());
}
}

int main() {
    params_are_clamped(); depth_to_v(); contract(); comfort(); edges_and_time(); text_mask(); warp_answers(); surface_hit();
    if (failures) { std::fprintf(stderr, "stereo: %d failure(s)\n", failures); return 1; }
    std::puts("stereo: ok");
    return 0;
}
