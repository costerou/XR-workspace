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
    DepthInput f; f.w = 512; f.h = 288; f.pixels = &px;
    CHECK(depth_input_valid(f));
    DepthInput low = f; low.h = 255; CHECK(!depth_input_valid(low));
    DepthInput none = f; none.pixels = nullptr; CHECK(!depth_input_valid(none));
    DepthInput tex = none; tex.texture = 7; CHECK(depth_input_valid(tex));
    DepthInput narrow = f; narrow.stride = 4; CHECK(!depth_input_valid(narrow));
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

void text_band() {
    auto depth = fake::flat_depth(64, 64, 1.f), mask = fake::flat_depth(64, 64, 0.f);
    mask.px[32 * 64 + 32] = 1.f;
    StereoParams p; p.text_keep = 0;
    const auto out = stereo_guide(depth, 0, 1, &mask, nullptr, p, 1, 1);
    CHECK(std::fabs(out.at(32, 34)) < 1e-6f); // Retain the original two-row protected core.
    CHECK(out.at(32, 35) > 0 && out.at(32, 35) < out.at(32, 36)); // Feather outside it.
}

void background_fill() {
    constexpr int W = 320, H = 200;
    RgbF colour{W, H, std::vector<float>(W * H * 3)};
    auto guide = fake::flat_depth(W, H, -0.5f);
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
        const bool near = (x >= 90 && x < 100) || (x >= 120 && x < 160) || (y != 100 && x >= 100 && x < 120);
        guide.px[y * W + x] = near ? 1.f : -0.5f;
        colour.px[(y * W + x) * 3] = near ? 1.f : 0.f;
        colour.px[(y * W + x) * 3 + 1] = near ? 0.f : 0.6f;
    }
    const auto out = stereo_warp(colour, guide, W, H, 0, {10, 8});
    // A second foreground object must never be copied into the newly exposed wall.
    for (int x = 117; x <= 128; ++x) CHECK(out.px[(100 * W + x) * 3] < 0.25f);
}

void pro2_high_strength_edges() {
    // A centre-row probe at the real Pro 2 pixel/disparity scale. The model grid is
    // 420x266; using three output rows keeps this known-answer test inexpensive.
    // The red rectangle has uniform near depth, against a uniform green wall.
    constexpr int W = 1920, H = 3, GW = 420, GH = 266, L = 160, R = 250;
    constexpr int left = L * W / GW, right = R * W / GW;
    RgbF colour{W, H, std::vector<float>(W * H * 3)};
    auto depth = fake::flat_depth(GW, GH, 0.f);
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
        const bool near = x >= left && x < right;
        colour.px[(y * W + x) * 3] = near ? 1.f : 0.f;
        colour.px[(y * W + x) * 3 + 1] = near ? 0.f : 0.6f;
    }
    for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x) {
        // Exact symmetric .3-step envelope of a binary rectangle, as published
        // by the engine: .05,.20,.35 | .65,.80,.95,1 at each boundary.
        const bool near = x >= L && x < R;
        const int distance = near ? std::min(x - L + 1, R - x) : (x < L ? L - x : x - R + 1);
        const float shoulder = std::max(0.f, 1.f - 0.3f * float(distance)) * 0.5f;
        depth.px[y * GW + x] = near ? 1.f - shoulder : shoulder;
    }
    struct Preset { const char* name; float convergence, near_arcmin, far_arcmin; };
    for (const auto p : {Preset{"desktop", .92f, 4, 14}, Preset{"video", .7f, 14, 36}, Preset{"games", .75f, 10, 30}}) {
        StereoParams params;
        params.strength = 2; params.convergence = p.convergence; params.text_keep = 0;
        params.near_deg = p.near_arcmin / 60; params.far_deg = p.far_arcmin / 60;
        const auto guide = stereo_guide(depth, 0, 1, nullptr, nullptr, params, 16.f / 9, 1);
        const auto budget = stereo_budget(stereo_limits(params, W, .3565709591f), 0, 1, 1);
        for (int eye = 0; eye < 2; ++eye) {
            const auto out = stereo_warp(colour, guide, W, H, eye, budget);
            // Ground truth is the solid foreground translated by its plane's
            // disparity. Score excess foreground and lost foreground separately:
            // filling more pixels with wall cannot hide erosion behind less halo.
            const float shift = stereo_side(eye) * stereo_half_disparity(stereo_v(1, params), budget);
            float leakage = 0, missing = 0;
            for (int x = left - 40; x < right + 40; ++x) {
                const float red = out.px[(W + x) * 3];
                // One output pixel at each expected edge belongs to antialiasing.
                if (x < left + shift - 1 || x >= right + shift + 1) leakage += red;
                if (x > left + shift + 1 && x < right + shift - 1) missing += 1 - red;
            }
            std::printf("Pro 2 %s eye %d: excess %.3f, missing %.3f foreground pixels\n", p.name, eye,
                        double(leakage), double(missing));
            CHECK(leakage < 2.5f); CHECK(missing < 2.5f);
        }
    }
}

void smooth_surface_disparity() {
    // Colour edges alone must not flatten a broad depth gradient. Its inverse
    // projection has a closed-form solution independent of the warp algorithm.
    constexpr int W = 1920, H = 3, GW = 420, GH = 266;
    const auto colour = fake::textured(W, H);
    auto guide = fake::flat_depth(GW, GH, 0.f);
    for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x)
        guide.px[y * GW + x] = .2f + .6f * (float(x) + .5f) / float(GW);
    const StereoBudget budget{6, 14};
    for (int eye = 0; eye < 2; ++eye) {
        const auto out = stereo_warp(colour, guide, W, H, eye, budget);
        float error = 0;
        for (int x = 40; x < W - 40; ++x) {
            const float side = stereo_side(eye);
            const float source_x = (float(x) + .5f - side * .2f * budget.near_half) /
                                   (1 + side * .6f * budget.near_half / float(W));
            const auto expected = stereo_sample(colour, source_x / float(W), .5f);
            for (std::size_t c = 0; c < 3; ++c)
                error = std::max(error, std::fabs(out.px[(W + x) * 3 + c] - expected[c]));
        }
        CHECK(error < .002f);
    }
}

void diagonal_edges() {
    // Same source/model pixel ratio as Pro 2, with both signs of an oblique
    // silhouette: the reconstruction must follow the edge in two dimensions.
    constexpr int W = 640, H = 360, GW = 140, GH = 89;
    for (const float slope : {-.5f, .5f}) {
        const auto foreground = [=](float u, float v) { return std::fabs(u - .5f - slope * (v - .5f)) < .1f; };
        RgbF colour{W, H, std::vector<float>(W * H * 3)};
        auto depth = fake::flat_depth(GW, GH, 0.f);
        for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
            const bool near = foreground((x + .5f) / W, (y + .5f) / H);
            colour.px[(y * W + x) * 3] = near ? 1.f : 0.f;
            colour.px[(y * W + x) * 3 + 1] = near ? 0.f : .6f;
        }
        for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x)
            depth.px[y * GW + x] = foreground((x + .5f) / GW, (y + .5f) / GH) ? 1.f : 0.f;
        // Symmetric .3-step envelope of the binary depth, including both axes.
        auto low = depth.px, high = depth.px;
        for (int pass = 0; pass < 4; ++pass) {
            const auto before_low = low, before_high = high;
            for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x) for (int d = 0; d < 4; ++d) {
                const int nx = std::clamp(x + (d == 0 ? 1 : d == 1 ? -1 : 0), 0, GW - 1);
                const int ny = std::clamp(y + (d == 2 ? 1 : d == 3 ? -1 : 0), 0, GH - 1);
                low[y * GW + x] = std::min(low[y * GW + x], before_low[ny * GW + nx] + .3f);
                high[y * GW + x] = std::max(high[y * GW + x], before_high[ny * GW + nx] - .3f);
            }
        }
        for (std::size_t i = 0; i < depth.px.size(); ++i) depth.px[i] = .5f * (low[i] + high[i]);
        StereoParams p; p.strength = 2; p.convergence = .7f; p.near_deg = 14.f / 60; p.far_deg = 36.f / 60;
        const auto guide = stereo_guide(depth, 0, 1, nullptr, nullptr, p, float(W) / H, 1);
        const auto budget = stereo_budget(stereo_limits(p, W, .3565709591f), 0, 1, 1);
        for (int eye = 0; eye < 2; ++eye) {
            const auto out = stereo_warp(colour, guide, W, H, eye, budget);
            const float shift = stereo_side(eye) * stereo_half_disparity(stereo_v(1, p), budget);
            float leakage = 0, missing = 0;
            for (int y = 50; y < H - 50; ++y) for (int x = 10; x < W - 10; ++x) {
                const float left = (.4f + slope * ((y + .5f) / H - .5f)) * W + shift, right = left + .2f * W;
                const float red = out.px[(y * W + x) * 3];
                if (x + .5f < left - 1 || x + .5f >= right + 1) leakage += red;
                if (x + .5f > left + 1 && x + .5f < right - 1) missing += 1 - red;
            }
            // Average error per row, allowing one pixel of antialiasing at each edge.
            CHECK(leakage / (H - 100) < 1.f); CHECK(missing / (H - 100) < 1.f);
        }
    }
}

void guide_protected_regions() {
    constexpr int W = 256, H = 160, GW = 64, GH = 40;
    for (const bool horizontal : {false, true}) {
        const int extent = horizontal ? GW : GH;
        for (const int edge : {1, extent / 2, extent - 2}) {
            const float location = (edge + .5f) / extent;
            const float u = horizontal ? location : .5f, v = horizontal ? .5f : location;
            RgbF colour{W, H, std::vector<float>(W * H * 3)};
            auto guide = fake::flat_depth(GW, GH, 0.f);
            for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x) {
                const int coordinate = horizontal ? x : y;
                guide.px[y * GW + x] = coordinate < edge ? -.8f : coordinate == edge ? .2f : .8f;
            }
            for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
                const float coordinate = horizontal ? (x + .5f) / W : (y + .5f) / H;
                const bool near = coordinate >= location - 1.f / (horizontal ? W : H);
                colour.px[(y * W + x) * 3] = near ? 1.f : 0.f;
                colour.px[(y * W + x) * 3 + 1] = near ? 0.f : .6f;
            }
            if (edge == extent / 2) {
                // Positive control for both edge orientations, then fully pin the
                // same sample. Neighbouring colour/depth must not unpin text.
                CHECK(stereo_sample_guide(colour, guide, u, v) > .7f);
                for (int y = 0; y < GH; ++y) for (int x = 0; x < GW; ++x)
                    if ((horizontal ? x : y) == edge) guide.px[y * GW + x] = 0;
                CHECK(stereo_sample_guide(colour, guide, u, v) == 0);
            } else {
                // All four image borders retain the original tapered guide.
                CHECK(stereo_sample_guide(colour, guide, u, v) == stereo_sample(guide, u, v));
            }
        }
    }
}

void guide_spatial_resolve() {
    constexpr int W = 64, H = 40, X = W / 2, Y = H / 2;
    RgbF colour{W, H, std::vector<float>(W * H * 3, .3f)};
    auto guide = fake::flat_depth(W, H, .2f);
    guide.px[Y * W + X] = .8f;
    // Isolated nonzero depth survives reconstruction, then the median removes it.
    CHECK(stereo_sample_guide(colour, guide, (X + .5f) / W, (Y + .5f) / H) > .79f);
    const std::array<std::array<int, 2>, 4> borders{{{{1, Y}}, {{W - 2, Y}}, {{X, 1}}, {{X, H - 2}}}};
    for (const auto& p : borders) guide.px[p[1] * W + p[0]] = .8f;
    auto resolved = stereo_resolve_guide(colour, guide);
    CHECK(resolved.w == W && resolved.h == H);
    CHECK(std::fabs(resolved.at(X, Y) - .2f) < 1e-6f);
    for (const auto& p : borders) CHECK(resolved.at(p[0], p[1]) == guide.at(p[0], p[1]));
    // A zero core has a different meaning from noise: preserve even one pinned pixel.
    guide.px[Y * W + X] = 0;
    resolved = stereo_resolve_guide(colour, guide);
    CHECK(resolved.at(X, Y) == 0);
    // A real straight silhouette remains a step, without erosion or a widened slope.
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
        guide.px[y * W + x] = x < X ? -.4f : .6f;
        for (int c = 0; c < 3; ++c) colour.px[(y * W + x) * 3 + c] = x < X ? 0.f : 1.f;
    }
    resolved = stereo_resolve_guide(colour, guide);
    float error = 0;
    for (std::size_t i = 0; i < guide.px.size(); ++i) error = std::max(error, std::fabs(resolved.px[i] - guide.px[i]));
    CHECK(error < 1e-6f);
}

void guide_correction_history() {
    constexpr int W = 64, H = 40, X = W / 2, Y = H / 2;
    const RgbF colour{W, H, std::vector<float>(W * H * 3, .3f)};
    auto before_guide = fake::flat_depth(W, H, .2f), guide = fake::flat_depth(W, H, .4f);
    // A moving broad surface must arrive immediately: history belongs to the
    // small reconstruction correction, not to the already-filtered base depth.
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
        const float slope = .1f * (x + .5f) / W + .05f * (y + .5f) / H;
        before_guide.px[y * W + x] += slope;
        guide.px[y * W + x] += slope;
    }
    auto before = stereo_resolve_guide(colour, before_guide);
    const auto current = stereo_resolve_guide(colour, guide);
    auto temporal = stereo_resolve_guide(colour, guide, &before, &before_guide, .1f);
    float error = 0;
    for (std::size_t i = 0; i < current.px.size(); ++i) error = std::max(error, std::fabs(temporal.px[i] - current.px[i]));
    CHECK(error < 1e-6f);
    // A small correction change advances only a quarter of the way, even while
    // the base plane moves by .2. A large correction change releases immediately.
    before_guide = fake::flat_depth(W, H, .2f); guide = fake::flat_depth(W, H, .4f);
    before = fake::flat_depth(W, H, .24f);
    temporal = stereo_resolve_guide(colour, guide, &before, &before_guide, .25f);
    CHECK(std::fabs(temporal.at(X, Y) - .43f) < 1e-6f);
    CHECK(temporal.at(1, Y) == guide.at(1, Y)); // Frame guards never inherit a correction.
    const auto large = fake::flat_depth(W, H, .8f);
    temporal = stereo_resolve_guide(colour, guide, &large, &before_guide, .01f);
    CHECK(std::fabs(temporal.at(X, Y) - .4f) < 1e-6f);
    CHECK(stereo_resolve_alpha(0) == 1 && stereo_resolve_alpha(-1) == 1);
    CHECK(stereo_resolve_alpha(33) > .23f && stereo_resolve_alpha(33) < .25f);
    // A reset or either resized history map uses the current result directly.
    temporal = stereo_resolve_guide(colour, guide, &before, &before_guide, stereo_resolve_alpha(0));
    CHECK(temporal.at(X, Y) == guide.at(X, Y));
    const auto resized = fake::flat_depth(W / 2, H, .24f);
    CHECK(stereo_resolve_guide(colour, guide, &resized, &before_guide, .25f).at(X, Y) == guide.at(X, Y));
    CHECK(stereo_resolve_guide(colour, guide, &before, &resized, .25f).at(X, Y) == guide.at(X, Y));
    guide.px[Y * W + X] = 0;
    CHECK(stereo_resolve_guide(colour, guide, &before, &before_guide, .25f).at(X, Y) == 0);
    guide = fake::flat_depth(W, H, .98f);
    CHECK(stereo_resolve_guide(colour, guide, &before, &before_guide, .25f).at(X, Y) == 1.f);
}

void protected_texture_is_not_geometry() {
    constexpr int W = 320, H = 200, GW = 80, GH = 50;
    auto colour = fake::textured(W, H);
    fake::stamp_text(colour, 24, 40, 112, 160);
    // A small textured picture can trigger parts of the text mask too. Its
    // protected transitions must not become arbitrary reconstructed depth edges.
    for (int y = 44; y < 156; ++y) for (int x = 160; x < 272; ++x) {
        const float phase = x * .9f + 4.f * std::sin(y * .17f);
        for (int c = 0; c < 3; ++c)
            colour.px[(y * W + x) * 3 + c] = .5f + .4f * std::sin(phase + c * .2f) * std::cos(y * .31f);
    }
    const auto mask = stereo_text_mask(colour, GW, GH), depth = fake::flat_depth(GW, GH, 1.f);
    StereoParams params; params.strength = 2; params.convergence = .92f; params.text_keep = 0;
    PlaneF protection;
    const auto guide = stereo_guide(depth, 0, 1, &mask, nullptr, params, float(W) / H, 1, &protection);
    const auto unguarded = stereo_resolve_guide(colour, guide);
    const auto resolved = stereo_resolve_guide(colour, guide, nullptr, nullptr, 1, &protection);
    auto old = resolved;
    for (auto& value : old.px) value += .04f;
    const auto temporal = stereo_resolve_guide(colour, guide, &old, &guide, .1f, &protection);
    float before_error = 0, error = 0; int transitions = 0;
    for (int y = 20; y < H - 20; ++y) for (int x = 20; x < W - 20; ++x) {
        const float u = (x + .5f) / W, v = (y + .5f) / H, gain = stereo_sample(protection, u, v);
        if (gain >= .999f) continue;
        if (gain > .02f && gain < .98f) ++transitions;
        const float expected = stereo_sample(guide, u, v);
        before_error = std::max(before_error, std::fabs(unguarded.at(x, y) - expected));
        error = std::max({error, std::fabs(resolved.at(x, y) - expected), std::fabs(temporal.at(x, y) - expected)});
    }
    CHECK(transitions > 100); CHECK(before_error > .02f); CHECK(error < 1e-6f);
    // Protection describes this frame, even when the base guide has history.
    PlaneF current_protection;
    stereo_guide(depth, 0, 1, &mask, &guide, params, float(W) / H, .01f, &current_protection);
    CHECK(current_protection.px == protection.px);
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
    params_are_clamped(); depth_to_v(); contract(); comfort(); edges_and_time(); text_mask(); warp_answers(); background_fill();
    pro2_high_strength_edges(); smooth_surface_disparity(); diagonal_edges(); guide_protected_regions(); guide_spatial_resolve();
    guide_correction_history(); protected_texture_is_not_geometry(); text_band(); surface_hit();
    if (failures) { std::fprintf(stderr, "stereo: %d failure(s)\n", failures); return 1; }
    std::puts("stereo: ok");
    return 0;
}
