#include <nlohmann/json.hpp>
// Global immersive 3D on a surfaceless EGL context (no display, window or compositor), through the real GlCompositor:
//  1. the guide map pass and the warp against their CPU mirrors (xr/stereo.hpp), on the fake depth source; NaN depth stays finite;
//  2. golden images of the side-by-side frame (tests/golden, tolerance for different GPUs);
//  3. disparity measured from the pixels, by block matching the two eyes: exact on flat depth, inside the comfort limits at the
//     harshest settings, no pixel brighter than the source;
//  4. the whole view: in 3D with no depth each eye is the 2D frame, pills, level bars, border, pointer and cards included (screen
//     depth); a desktop's real distance gives the plane's disparity and its pill goes with it; the pointer sits on the surface under
//     it; the background is at infinity; a desktop turned off and a depth source that stopped both ease flat; 2D is untouched;
//  5. frame time at 3840 x 1200 with depth arriving at 30 Hz on three desktops. Printed on any GPU, enforced (120 Hz) on the RX 7800 XT.
// Exit 77 (skipped) without EGL/GLES. argv: golden dir, or --write-goldens DIR.
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include "fake_depth.hpp"
#include "gl.hpp"
#include "stereo_gl.hpp"
#include "xr/video.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>
#include <thread>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
constexpr Mat3 kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1};
constexpr float kHalfW = 0.44f, kHalfH = 0.275f;

struct Image { int w = 0, h = 0; std::vector<std::uint8_t> rgba; const std::uint8_t* at(int x, int y) const { return &rgba[(std::size_t(y) * w + x) * 4]; } };
Image read(int x, int y, int w, int h) {  // top row first
    Image out{w, h, std::vector<std::uint8_t>(std::size_t(w) * h * 4)};
    std::vector<std::uint8_t> row(std::size_t(w) * h * 4);
    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, row.data());
    for (int r = 0; r < h; ++r) std::memcpy(&out.rgba[std::size_t(r) * w * 4], &row[std::size_t(h - 1 - r) * w * 4], std::size_t(w) * 4);
    return out;
}
Image half(const Image& sbs, int eye) {
    Image out{sbs.w / 2, sbs.h, std::vector<std::uint8_t>(std::size_t(sbs.w / 2) * sbs.h * 4)};
    for (int y = 0; y < sbs.h; ++y) std::memcpy(&out.rgba[std::size_t(y) * out.w * 4], sbs.at(eye * out.w, y), std::size_t(out.w) * 4);
    return out;
}
// upload() takes XRGB bytes: B, G, R, X.
std::vector<std::uint8_t> bgrx(const RgbF& c) {
    auto px = fake::rgba_bytes(c);
    for (std::size_t i = 0; i < px.size(); i += 4) std::swap(px[i], px[i + 2]);
    return px;
}
DepthInput frame_of(const PlaneF& d, std::uint64_t seq = 1) { DepthInput f; f.w = d.w; f.h = d.h; f.pixels = d.px.data(); f.source_seq = seq; return f; }

// Draw until the fades are over, depth arriving all the while, and leave the settled frame in the buffer.
void settle(GlCompositor& gl, const std::vector<FRect>& tiles, const std::vector<Placement>& placements, const std::vector<PlaneF>& depth, int ms = 360) {
    const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
    do {
        for (std::size_t i = 0; i < depth.size(); ++i) if (depth[i].w) gl.set_depth(i, frame_of(depth[i]));
        gl.draw_and_swap(tiles, placements, -1, 0, kIdentity, kHalfW, kHalfH);
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    } while (std::chrono::steady_clock::now() < end);
    glFinish();
}

// Best horizontal shift of `b` against `a` for a block of `a` at (x, y): positive when the block sits further right in `a`,
// which is a near thing's disparity with a = left eye, b = right eye. -1000: no texture, no answer. SAD with a parabola on top.
float match(const Image& a, const Image& b, int x, int y, int half_block, int range) {
    float best = 1e18f, second_lo = 0, second_hi = 0; int at = 0;
    std::vector<float> cost(std::size_t(2 * range + 1));
    for (int s = -range; s <= range; ++s) {
        float sad = 0;
        for (int j = -half_block; j <= half_block; ++j) for (int i = -half_block; i <= half_block; ++i) {
            const auto* p = a.at(x + i, y + j); const auto* q = b.at(x + i - s, y + j);
            for (int k = 0; k < 3; ++k) sad += float(std::abs(int(p[k]) - int(q[k])));
        }
        cost[std::size_t(s + range)] = sad;
        if (sad < best) { best = sad; at = s; }
    }
    if (at == -range || at == range) return float(at);
    second_lo = cost[std::size_t(at + range - 1)]; second_hi = cost[std::size_t(at + range + 1)];
    const float d = second_lo + second_hi - 2 * best;
    return d > 1e-3f ? float(at) + 0.5f * (second_lo - second_hi) / d : float(at);
}

void cpu_mirror_guide(GLuint fb_after) {
    std::string why;
    auto pass = StereoPass::create(-1, -1, -1, why);
    CHECK(pass);
    if (!pass) return;
    pass->resize_sources(1);
    constexpr int CW = 640, CH = 400, DW = 320, DH = 256;
    auto colour = fake::box_picture(CW, CH);
    fake::stamp_text(colour, 60, 40, 300, 150);
    const auto bytes = fake::rgba_bytes(colour);
    GLuint tex = 0; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, CW, CH, 0, GL_RGBA, GL_UNSIGNED_BYTE, bytes.data());
    StereoParams params; params.strength = 1.5f; params.convergence = 0.3f; params.text_keep = 0.2f;
    auto depth = fake::bump_depth(DW, DH, 5);
    for (int i = 0; i < 40; ++i) depth.px[std::size_t(i) * 37 + 900] = std::numeric_limits<float>::quiet_NaN();
    depth.px[12345] = std::numeric_limits<float>::infinity();
    const auto read_guide = [&] {
        GLuint fb = 0; glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, pass->guide(0), 0);
        PlaneF out{DW, DH, std::vector<float>(std::size_t(DW) * DH)};
        std::vector<float> rgba(std::size_t(DW) * DH * 4);
        glReadPixels(0, 0, DW, DH, GL_RGBA, GL_FLOAT, rgba.data());
        for (std::size_t i = 0; i < out.px.size(); ++i) out.px[i] = rgba[i * 4];
        glBindFramebuffer(GL_FRAMEBUFFER, fb_after); glDeleteFramebuffers(1, &fb);
        return out;
    };
    const float aspect = float(CW) / float(CH);
    CHECK(pass->update(0, frame_of(depth, 7), tex, CW, params, aspect, 0));
    CHECK(pass->ready(0)); CHECK(pass->seq(0) == 7);
    const auto mask = stereo_text_mask(colour, DW, DH);
    auto expected = stereo_guide(depth, 0, 1, &mask, nullptr, params, aspect, 1);
    auto got = read_guide();
    const auto compare = [&](const PlaneF& a, const PlaneF& b, const char* what) {
        float worst = 0; int bad = 0, nonfinite = 0;
        for (std::size_t i = 0; i < a.px.size(); ++i) {
            if (!std::isfinite(b.px[i])) { ++nonfinite; continue; }
            const float e = std::fabs(a.px[i] - b.px[i]); worst = std::max(worst, e); bad += e > 0.03f;
        }
        std::printf("guide (%s): GPU vs CPU worst %.4f, %d of %zu off by more than 0.03, %d non-finite\n", what, double(worst), bad, a.px.size(), nonfinite);
        CHECK(nonfinite == 0); CHECK(bad <= int(a.px.size() / 500));
    };
    compare(expected, got, "first frame");
    // Temporal step: a second frame with moved depth, and the filter's history.
    const auto next = fake::bump_depth(DW, DH, 6);
    CHECK(pass->update(0, frame_of(next, 8), tex, CW, params, aspect, 33));
    expected = stereo_guide(next, 0, 1, &mask, &got, params, aspect, stereo_alpha(33));
    compare(expected, read_guide(), "with history");
    // A stride, and a range other than 0..1.
    std::vector<float> wide(std::size_t(DW + 3) * DH, 0.f);
    for (int y = 0; y < DH; ++y) for (int x = 0; x < DW; ++x) wide[std::size_t(y) * (DW + 3) + x] = 100.f + 50.f * depth.at(x, y);
    DepthInput strided; strided.w = DW; strided.h = DH; strided.pixels = wide.data(); strided.stride = std::size_t(DW + 3) * sizeof(float); strided.lo = 100; strided.hi = 150; strided.source_seq = 9;
    CHECK(pass->update(0, strided, tex, CW, params, aspect, 0));
    compare(stereo_guide(depth, 0, 1, &mask, nullptr, params, aspect, 1), read_guide(), "stride and range");
    DepthInput bad = strided; bad.h = 100;
    CHECK(!pass->update(0, bad, tex, CW, params, aspect, 0)); CHECK(pass->seq(0) == 9);
    // A GL texture source (the dmabuf path).
    GLuint raw = 0; glGenTextures(1, &raw); glBindTexture(GL_TEXTURE_2D, raw);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, DW, DH, 0, GL_RED, GL_FLOAT, depth.px.data());
    DepthInput by_texture; by_texture.w = DW; by_texture.h = DH; by_texture.texture = raw; by_texture.source_seq = 10;
    CHECK(pass->update(0, by_texture, tex, CW, params, aspect, 0));
    compare(stereo_guide(depth, 0, 1, &mask, nullptr, params, aspect, 1), read_guide(), "GL texture source");
    glDeleteTextures(1, &raw); glDeleteTextures(1, &tex);
    pass.reset();
}

constexpr int kEyeW = 640, kEyeH = 400;
void warp_mirror() {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kEyeW, kEyeH, 1, why);
    if (!gl) return;
    gl->resize(kEyeW, kEyeH, 2 * kEyeW, kEyeH);
    StereoSettings set; set.enabled = true; set.params.strength = 1; set.params.convergence = 0.5f; set.half_baseline = 0;
    gl->set_stereo(set);
    const auto colour = fake::box_picture(kEyeW, kEyeH);
    const auto bytes = bgrx(colour);
    gl->upload(0, bytes.data(), kEyeW, kEyeH, kEyeW * 4);
    const auto depth = fake::box_depth(320, 256);
    const std::vector<FRect> tiles{{0, 0, float(kEyeW), float(kEyeH)}};
    const std::vector<Placement> at{{}};
    settle(*gl, tiles, at, {depth});
    const auto sbs = read(0, 0, 2 * kEyeW, kEyeH);
    const auto mask = stereo_text_mask(colour, 320, 256);
    const auto guide = stereo_guide(depth, 0, 1, &mask, nullptr, set.params, 1.6f, 1);
    const auto budget = stereo_budget(stereo_limits(set.params, kEyeW, kHalfW), 0, 1, 1);
    for (int eye = 0; eye < 2; ++eye) {
        const auto view = half(sbs, eye);
        const auto cpu = stereo_warp(colour, guide, kEyeW, kEyeH, eye, budget);
        int off = 0, on = 0;
        for (int y = 2; y < kEyeH - 2; ++y) for (int x = 2; x < kEyeW - 2; ++x) {
            const auto* p = view.at(x, y); const auto* c = &cpu.px[(std::size_t(y) * kEyeW + x) * 3];
            bool close = false;  // edges of the box land a fraction of a pixel apart: compare with the neighbours too
            for (int dx = -2; dx <= 2 && !close; ++dx) {
                const auto* n = &cpu.px[(std::size_t(y) * kEyeW + x + dx) * 3];
                close = std::abs(p[0] - n[0] * 255.f) < 24 && std::abs(p[1] - n[1] * 255.f) < 24 && std::abs(p[2] - n[2] * 255.f) < 24;
            }
            off += !close; ++on; (void)c;
        }
        std::printf("warp eye %d: %d of %d pixels differ from the CPU mirror\n", eye, off, on);
        CHECK(off <= on / 500);
    }
}

// Golden scenes: a small side-by-side frame, stored as binary PPM.
struct Golden { const char* name; RgbF colour; PlaneF depth; StereoParams params; };
bool write_ppm(const std::string& path, const Image& im) {
    std::ofstream f(path, std::ios::binary);
    f << "P6\n" << im.w << " " << im.h << "\n255\n";
    for (std::size_t i = 0; i < std::size_t(im.w) * im.h; ++i) f.write(reinterpret_cast<const char*>(&im.rgba[i * 4]), 3);
    return bool(f);
}
bool read_ppm(const std::string& path, Image& im) {
    std::ifstream f(path, std::ios::binary); std::string magic; int max = 0;
    if (!(f >> magic >> im.w >> im.h >> max) || magic != "P6" || max != 255) return false;
    f.get();
    im.rgba.assign(std::size_t(im.w) * im.h * 4, 255);
    for (std::size_t i = 0; i < std::size_t(im.w) * im.h; ++i) if (!f.read(reinterpret_cast<char*>(&im.rgba[i * 4]), 3)) return false;
    return true;
}
void goldens(const std::string& dir, bool write) {
    constexpr int W = 240, H = 150;
    StereoParams strong; strong.strength = 1.6f; strong.convergence = 0.4f;
    RgbF text = fake::textured(W, H);
    fake::stamp_text(text, 20, 12, 120, 70);
    std::vector<Golden> scenes;
    scenes.push_back({"stereo_box", fake::box_picture(W, H), fake::box_depth(320, 256), strong});
    scenes.push_back({"stereo_ramp", fake::textured(W, H), fake::ramp_depth(320, 256), strong});
    scenes.push_back({"stereo_text", text, fake::bump_depth(320, 256, 2), strong});
    for (const auto& scene : scenes) {
        std::string why;
        auto gl = GlCompositor::create_offscreen(W, H, 1, why);
        if (!gl) return;
        gl->resize(W, H, 2 * W, H);
        StereoSettings set; set.enabled = true; set.params = scene.params; set.half_baseline = 0;
        gl->set_stereo(set);
        const auto bytes = bgrx(scene.colour);
        gl->upload(0, bytes.data(), W, H, W * 4);
        settle(*gl, {{0, 0, float(W), float(H)}}, {{}}, {scene.depth});
        const auto got = read(0, 0, 2 * W, H);
        const auto path = dir + "/" + scene.name + ".ppm";
        if (write) { CHECK(write_ppm(path, got)); continue; }
        Image want;
        CHECK(read_ppm(path, want));
        if (want.w != got.w || want.h != got.h) { CHECK(false); continue; }
        int off = 0; int worst = 0;
        for (std::size_t i = 0; i < got.rgba.size(); i += 4) {
            int e = 0; for (std::size_t k = 0; k < 3; ++k) e = std::max(e, std::abs(int(got.rgba[i + k]) - int(want.rgba[i + k])));
            worst = std::max(worst, e); off += e > 16;
        }
        std::printf("golden %s: %d of %d pixels off by more than 16 (worst %d)\n", scene.name, off, W * 2 * H, worst);
        CHECK(off <= W * 2 * H / 100);
    }
}

void measured_disparity() {
    constexpr int W = 960, H = 600;
    std::string why;
    auto gl = GlCompositor::create_offscreen(W, H, 1, why);
    if (!gl) return;
    gl->resize(W, H, 2 * W, H);
    const auto colour = fake::textured(W, H);
    const auto bytes = bgrx(colour);
    gl->upload(0, bytes.data(), W, H, W * 4);
    const std::vector<FRect> tiles{{0, 0, float(W), float(H)}};
    const std::vector<Placement> at{{}};
    float source_max = 0; for (const float v : colour.px) source_max = std::max(source_max, v);
    // Exact on a flat depth: pushed in and out by a known amount.
    for (const float d : {0.75f, 0.25f, 0.5f}) {
        StereoSettings set; set.enabled = true; set.params.strength = 1; set.params.convergence = 0.5f; set.half_baseline = 0;
        gl->set_stereo(set);
        settle(*gl, tiles, at, {fake::flat_depth(320, 256, d)});
        const auto sbs = read(0, 0, 2 * W, H);
        const auto left = half(sbs, 0), right = half(sbs, 1);
        const auto limits = stereo_limits(set.params, W, kHalfW);
        const float v = stereo_v(d, set.params), want = 2 * stereo_half_disparity(v, stereo_budget(limits, 0, 1, 1));
        float got = 0; int n = 0;
        for (int y = 150; y < 450; y += 50) for (int x = 300; x < 660; x += 60) { got += match(left, right, x, y, 7, 40); ++n; }
        got /= float(n);
        std::printf("flat depth %.2f: disparity %.2f px, expected %.2f\n", double(d), double(got), double(want));
        CHECK(std::fabs(got - want) < 0.6f);
    }
    // The harshest settings on rough depth: never beyond the limits, never brighter than the source.
    float most_near = 0, most_far = 0; int brightest = 0;
    for (const float convergence : {0.f, 1.f}) {
        StereoSettings set; set.enabled = true; set.params.strength = kStereoStrengthMax; set.params.convergence = convergence;
        set.params.near_deg = kStereoNearDegMax; set.params.far_deg = kStereoFarDegMax; set.half_baseline = 0;
        gl->set_stereo(set);
        settle(*gl, tiles, at, {fake::bump_depth(320, 256, 9)}, 340);
        const auto sbs = read(0, 0, 2 * W, H);
        const auto left = half(sbs, 0), right = half(sbs, 1);
        for (int y = 40; y < H - 40; y += 16) for (int x = 90; x < W - 90; x += 16) {
            const float s = match(left, right, x, y, 5, 64);
            most_near = std::max(most_near, s); most_far = std::min(most_far, s);
        }
        for (std::size_t i = 0; i < sbs.rgba.size(); i += 4) for (std::size_t k = 0; k < 3; ++k) brightest = std::max(brightest, int(sbs.rgba[i + k]));
    }
    StereoParams top; top.near_deg = kStereoNearDegMax; top.far_deg = kStereoFarDegMax;
    const auto limits = stereo_limits(top, W, kHalfW);
    std::printf("harshest settings: measured %.1f near / %.1f far px, limits %.1f / %.1f; brightest %d (source %d)\n", double(most_near), double(most_far),
                double(limits.near_px), double(limits.far_px), brightest, int(source_max * 255.f + 0.5f));
    CHECK(most_near <= limits.near_px + 1.5f); CHECK(-most_far <= limits.far_px + 1.5f);
    CHECK(most_near > limits.near_px * 0.5f); CHECK(-most_far > limits.far_px * 0.5f);  // and the test really reached them
    CHECK(brightest <= int(source_max * 255.f + 0.5f) + 1);
}

struct View { std::vector<FRect> tiles; std::vector<Placement> placements; };
View three(float distance1 = 1) {
    View v;
    v.tiles = {{20, 200, 420, 262}, {460, 200, 420, 262}, {900, 200, 420, 262}};
    v.placements = {{}, {0, 0, distance1, 0, 0}, {}};
    return v;
}
// `skip`: a box left out (x0, y0, x1, y1).
float image_error(const Image& a, const Image& b, std::array<int, 4> skip = {0, 0, 0, 0}) {
    int worst = 0;
    for (int y = 0; y < a.h; ++y) for (int x = 0; x < a.w; ++x) {
        if (x >= skip[0] && y >= skip[1] && x < skip[2] && y < skip[3]) continue;
        const auto* p = a.at(x, y); const auto* q = b.at(x, y);
        for (int k = 0; k < 3; ++k) worst = std::max(worst, std::abs(int(p[k]) - int(q[k])));
    }
    return float(worst);
}

void whole_view() {
    constexpr int W = 1340, H = 700;
    std::string why;
    auto gl = GlCompositor::create_offscreen(W, H, 3, why);
    if (!gl) return;
    std::vector<std::vector<std::uint8_t>> pictures;
    for (int i = 0; i < 3; ++i) {
        pictures.push_back(bgrx(fake::textured(420 + i * 8, 262)));
        gl->upload(std::size_t(i), pictures.back().data(), 420 + std::uint32_t(i) * 8, 262, (420 + std::uint32_t(i) * 8) * 4);
    }
    gl->set_accent(0x80, 0xa0, 0xc0);
    GlassFrame glass; glass.pills = true; glass.bars.fill(true); glass.hover = 1; glass.hover_level = 0.6f;
    gl->set_glass(glass);
    gl->set_free_pointer(true, 670, 330, 0.7f);
    HudImage card; card.width = 200; card.height = 60; card.rgba.assign(200 * 60 * 4, 0);
    for (std::size_t i = 0; i < card.rgba.size(); i += 4) { card.rgba[i] = 90; card.rgba[i + 1] = 90; card.rgba[i + 2] = 90; card.rgba[i + 3] = 200; }
    gl->set_popup(&card, 1, 1.f);
    const auto scene = three();
    const auto draw = [&](const View& v, int highlight = 1) { gl->draw_and_swap(v.tiles, v.placements, highlight, 0.8f, kIdentity, kHalfW, kHalfH); glFinish(); };
    // 2D, as it has always been: the reference frame; and the same again after 3D has been on and off.
    draw(scene);
    const auto mono = read(0, 0, W, H);
    // 3D without any depth or baseline: both eyes are that frame, so everything is at screen depth, pills and handles included.
    gl->resize(W, H, 2 * W, H);
    StereoSettings set; set.enabled = true; set.half_baseline = 0;
    gl->set_stereo(set);
    CHECK(gl->stereo_available());
    draw(scene);
    auto sbs = read(0, 0, 2 * W, H);
    const std::array<int, 4> dot_box{655, 315, 685, 345};  // the dot is half a pixel off in each eye: a pixel in front of the surface
    const float el = image_error(half(sbs, 0), mono, dot_box), er = image_error(half(sbs, 1), mono, dot_box);
    std::printf("no depth, no baseline: left eye differs from the 2D frame by %.0f/255, right by %.0f/255 (dot apart)\n", double(el), double(er));
    const auto stray = [&](const Image& a) {  // software rasterisers round an odd edge pixel differently at a viewport offset
        int n = 0;
        for (int y = 0; y < a.h; ++y) for (int x = 0; x < a.w; ++x) {
            if (x >= dot_box[0] && y >= dot_box[1] && x < dot_box[2] && y < dot_box[3]) continue;
            for (int k = 0; k < 3; ++k) if (std::abs(int(a.at(x, y)[k]) - int(mono.at(x, y)[k])) > 2) { ++n; break; }
        }
        return n;
    };
    const int stray_l = stray(half(sbs, 0)), stray_r = stray(half(sbs, 1));
    std::printf("  %d and %d pixels differ by more than 2\n", stray_l, stray_r);
    CHECK(stray_l <= 8); CHECK(stray_r <= 8);
    // Real distance: the desktop at distance 2 sits behind the screen by the plane's disparity, and its pill goes with it.
    // Distance 1 desktops stay at zero.
    set.half_baseline = kStereoHalfBaseline;
    gl->set_stereo(set);
    settle(*gl, scene.tiles, scene.placements, {}, 340);
    const auto far = three(2.f);
    settle(*gl, far.tiles, far.placements, {}, 60);
    sbs = read(0, 0, 2 * W, H);
    auto left = half(sbs, 0), right = half(sbs, 1);
    const auto limits = stereo_limits(set.params, W, kHalfW);
    const float want = stereo_plane_disparity(set.half_baseline, -2.f, float(W), kHalfW);
    const float on_near = match(left, right, 230, 330, 8, 44), on_far = match(left, right, 640, 330, 8, 44);
    std::printf("real depth: desktop at 1: %.2f px; at 2: %.2f px, expected %.2f (limit %.1f)\n", double(on_near), double(on_far), double(want), double(limits.far_px));
    CHECK(std::fabs(on_near) < 0.6f); CHECK(std::fabs(on_far - want) < 1.f);
    // Depth on the middle desktop only, then the pointer on it: the dot sits on the surface under it.
    set.half_baseline = 0; set.params.strength = 1; set.params.convergence = 0.5f;
    gl->set_stereo(set);
    gl->set_popup(nullptr, 0, 0);
    gl->set_glass({});
    const std::vector<PlaneF> depth{{}, fake::flat_depth(320, 256, 0.9f), {}};
    settle(*gl, scene.tiles, scene.placements, depth);
    {auto observed=nlohmann::json::parse(gl->stereo_readback());CHECK(observed["physical_sbs"]==true);CHECK(observed["available"]==true);CHECK(observed["desktops"][1]["depth_ready"]==true);CHECK(observed["desktops"][1]["drawing"]==true);CHECK(observed["desktops"][1]["gain"].get<float>()>0);CHECK(observed["depth_updates"].get<unsigned>()>0);CHECK(observed["params"]["strength"]==set.params.strength);}
    sbs = read(0, 0, 2 * W, H); left = half(sbs, 0); right = half(sbs, 1);
    const auto dot_centre = [](const Image& im) {  // the dot: the only neutral grey with a black ring in this frame
        double sum = 0; int n = 0;
        for (int y = 300; y < 360; ++y) for (int x = 600; x < 740; ++x) {
            const auto* p = im.at(x, y);
            if (p[0] == 0 && p[1] == 0 && p[2] == 0) { sum += x; ++n; }
        }
        return n ? float(sum / n) : -1.f;
    };
    const float dot = dot_centre(left) - dot_centre(right);
    const StereoParams params = stereo_clamped(set.params);
    const float tile_scale = stereo_tile_scale(420, float(W), 1);
    const auto budget = stereo_budget(stereo_limits(params, float(W), kHalfW), 0, tile_scale, 1);
    const float surface = 2 * stereo_half_disparity(stereo_v(0.9f, params), budget);
    std::printf("pointer: %.2f px of disparity on a surface at %.2f px\n", double(dot), double(surface));
    CHECK(dot_centre(left) > 0); CHECK(std::fabs(dot - (surface + 1)) < 1.f);
    // That desktop turned off: it eases flat, others untouched. Depth stopping does the same, and frames returning bring it back.
    const float deep = match(left, right, 500, 260, 8, 44);
    CHECK(deep > surface - 2);
    gl->set_free_pointer(false, 0, 0, 0);
    set.desktops = {true, false, true};
    gl->set_stereo(set);
    settle(*gl, scene.tiles, scene.placements, depth);
    {auto observed=nlohmann::json::parse(gl->stereo_readback());CHECK(observed["physical_sbs"]==true);CHECK(observed["available"]==true);CHECK(observed["desktops"][1]["depth_ready"]==true);CHECK(observed["desktops"][1]["drawing"]==true);CHECK(observed["desktops"][1]["gain"].get<float>()>0);CHECK(observed["depth_updates"].get<unsigned>()>0);CHECK(observed["params"]["strength"]==set.params.strength);}
    sbs = read(0, 0, 2 * W, H); left = half(sbs, 0); right = half(sbs, 1);
    CHECK(std::fabs(match(left, right, 640, 330, 8, 44)) < 0.6f);
    set.desktops.clear();
    gl->set_stereo(set);
    settle(*gl, scene.tiles, scene.placements, depth);
    {auto observed=nlohmann::json::parse(gl->stereo_readback());CHECK(observed["physical_sbs"]==true);CHECK(observed["available"]==true);CHECK(observed["desktops"][1]["depth_ready"]==true);CHECK(observed["desktops"][1]["drawing"]==true);CHECK(observed["desktops"][1]["gain"].get<float>()>0);CHECK(observed["depth_updates"].get<unsigned>()>0);CHECK(observed["params"]["strength"]==set.params.strength);}
    sbs = read(0, 0, 2 * W, H); left = half(sbs, 0); right = half(sbs, 1);
    CHECK(std::fabs(match(left, right, 640, 330, 8, 44) - surface) < 1.2f);
    std::this_thread::sleep_for(std::chrono::milliseconds(int(kStereoStaleMs) + 20));
    settle(*gl, scene.tiles, scene.placements, {}, 340);
    sbs = read(0, 0, 2 * W, H); left = half(sbs, 0); right = half(sbs, 1);
    CHECK(std::fabs(match(left, right, 640, 330, 8, 44)) < 0.6f);
    // The background goes to infinity: its parallax is the baseline's, the same at every distance on the view.
    set.half_baseline = kStereoHalfBaseline;
    gl->set_stereo(set);
    gl->set_environment(1.f, Background::grid, 100.f, 0.f, kIdentity);
    settle(*gl, scene.tiles, scene.placements, {}, 340);
    sbs = read(0, 0, 2 * W, H); left = half(sbs, 0); right = half(sbs, 1);
    float grid = 0, grid_cost = 1e18f;  // a repeating pattern: over a wide strip, where only the true shift lines everything up
    for (int shift = -40; shift <= 40; ++shift) {
        float cost = 0;
        for (int y = 594; y < 606; ++y) for (int x = 500; x < 840; ++x) cost += float(std::abs(int(left.at(x, y)[1]) - int(right.at(x - shift, y)[1])));
        if (cost < grid_cost) { grid_cost = cost; grid = float(shift); }
    }
    const float expect_grid = -stereo_infinity_shift(kStereoHalfBaseline, stereo_limits(set.params, float(W), kHalfW), float(W), kHalfW) * float(W);
    std::printf("background: %.2f px, expected %.2f (infinity)\n", double(grid), double(expect_grid));
    CHECK(std::fabs(grid - expect_grid) < 1.f);
    gl->set_environment(0.f, Background::none, 100.f, 0.f, kIdentity);
    // Off: fades out, the buffer goes back to one eye, and the frame is the 2D one to the bit.
    set.enabled = false;
    gl->set_stereo(set);
    settle(*gl, scene.tiles, scene.placements, {}, 340);
    gl->resize(W, H, W, H);
    gl->set_popup(&card, 2, 1.f);
    gl->set_glass(glass);
    gl->set_free_pointer(true, 670, 330, 0.7f);
    draw(scene);
    const auto after = read(0, 0, W, H);
    const float e = image_error(after, mono);
    std::printf("2D after 3D: %.0f/255 from the first 2D frame\n", double(e));
    CHECK(e == 0);
    {auto observed=nlohmann::json::parse(gl->stereo_readback());CHECK(observed["physical_sbs"]==false);CHECK(observed["enabled"]==false);for(const auto& d:observed["desktops"])CHECK(d["drawing"]==false);}
    // A buffer that is not 3:1 stays 2D even with 3D on (the glasses were not switched).
    set.enabled = true;
    gl->set_stereo(set);
    draw(scene);
    CHECK(image_error(read(0, 0, W, H), mono) == 0);
    {auto observed=nlohmann::json::parse(gl->stereo_readback());CHECK(observed["enabled"]==true);CHECK(observed["physical_sbs"]==false);for(const auto& d:observed["desktops"])CHECK(d["drawing"]==false);}
    set.enabled = false; gl->set_stereo(set);
    const auto report = gl->report();
    CHECK(report.find("\"stereo\":{\"available\":true") != std::string::npos);
    CHECK(glGetError() == GL_NO_ERROR);
}

void live_strength() {
    constexpr int W = 640, H = 400;
    std::string why;
    auto gl = GlCompositor::create_offscreen(2 * W, H, 1, why); CHECK(gl != nullptr);
    if (!gl) return;
    gl->resize(W, H, 2 * W, H);
    StereoSettings set; set.enabled = true; set.params.strength = 1; set.params.convergence = .5f; set.half_baseline = 0;
    gl->set_stereo(set);
    const auto bytes = bgrx(fake::box_picture(W, H));
    gl->upload(0, bytes.data(), W, H, W * 4);
    const std::vector<FRect> tiles{{0, 0, W, H}};
    const std::vector<Placement> at{{}};
    settle(*gl, tiles, at, {fake::box_depth(320, 256)});
    const auto difference = [&] {
        gl->draw_and_swap(tiles, at, -1, 0, kIdentity, kHalfW, kHalfH); glFinish();
        const auto sbs = read(0, 0, 2 * W, H); int changed = 0;
        for (int y = 20; y < H - 20; ++y) for (int x = 20; x < W - 20; ++x)
            changed += std::memcmp(sbs.at(x, y), sbs.at(x + W, y), 3) != 0;
        return changed;
    };
    CHECK(difference() > 100);
    set.params.strength = 0; gl->set_stereo(set);
    CHECK(difference() == 0); // No fresh depth frame: the desktop is completely still.
    set.strengths = {100}; gl->set_stereo(set);
    CHECK(difference() > 100); // Per-desktop override is live too.
    set.strengths = {0}; gl->set_stereo(set);
    CHECK(difference() == 0);
}

void frame_time() {
    constexpr int W = 1920, H = 1200, DW = 518, DH = 324;
    std::string why;
    auto gl = GlCompositor::create_offscreen(W, H, 3, why);
    if (!gl) return;
    gl->resize(W, H, 2 * W, H);
    StereoSettings set; set.enabled = true;
    gl->set_stereo(set);
    auto colour = fake::textured(W, H);
    fake::stamp_text(colour, 100, 100, 800, 500);
    const auto bytes = bgrx(colour);
    for (std::size_t i = 0; i < 3; ++i) gl->upload(i, bytes.data(), W, H, W * 4);
    std::vector<PlaneF> depth{fake::bump_depth(DW, DH, 1), fake::bump_depth(DW, DH, 2), fake::bump_depth(DW, DH, 3)};
    // Worst case: a maximized desktop, every eye pixel warped; the other two behind it get their depth frames all the same.
    const std::vector<FRect> tiles{{0, 0, float(W), float(H)}, {0, 0, float(W), float(H)}, {0, 0, float(W), float(H)}};
    const std::vector<Placement> at{{0, 0, 1.2f, 0, 0}, {0, 0, 1.1f, 0, 0}, {}};
    settle(*gl, tiles, at, depth, 340);
    using clock = std::chrono::steady_clock;
    const auto ms = [](clock::time_point a, clock::time_point b) { return std::chrono::duration<double, std::milli>(b - a).count(); };
    std::vector<double> frames;
    double depth_ms = 0; int depth_n = 0;
    for (int f = 0; f < 240; ++f) {
        const auto t0 = clock::now();
        if (f % 4 == 0) {  // 30 Hz at 120
            for (std::size_t i = 0; i < 3; ++i) gl->set_depth(i, frame_of(depth[i], std::uint64_t(f)));
            glFinish();
            depth_ms += ms(t0, clock::now()); ++depth_n;
        }
        gl->draw_and_swap(tiles, at, -1, 0, kIdentity, kHalfW, kHalfH);
        glFinish();
        frames.push_back(ms(t0, clock::now()));
    }
    std::sort(frames.begin(), frames.end());
    const char* renderer = reinterpret_cast<const char*>(glGetString(GL_RENDERER));
    const double median = frames[frames.size() / 2], p99 = frames[frames.size() * 99 / 100];
    std::printf("3840x1200 on %s: frame median %.2f ms, p99 %.2f ms, depth update of 3 desktops %.2f ms\n", renderer, median, p99, depth_ms / depth_n);
    if (renderer && std::strstr(renderer, "7800")) { CHECK(p99 < 1000.0 / 120.0); }
}
}

int main(int argc, char** argv) {
    bool write = argc > 2 && !std::strcmp(argv[1], "--write-goldens");
    const std::string dir = argc > 2 ? argv[2] : argc > 1 ? argv[1] : ".";
    std::string why;
    auto probe = GlCompositor::create_offscreen(64, 64, 1, why);
    if (!probe) { std::printf("skip: %s\n", why.c_str()); return 77; }
    StereoSettings on; on.enabled = true;
    probe->set_stereo(on);
    if (!probe->stereo_available()) { std::printf("skip: stereo not available on this GL\n"); return 77; }
    GLint fb = 0; glGetIntegerv(GL_FRAMEBUFFER_BINDING, &fb);
    if (!write) cpu_mirror_guide(GLuint(fb));
    probe.reset();
    if (!write) warp_mirror();
    goldens(dir, write);
    if (!write) { measured_disparity(); whole_view(); live_strength(); frame_time(); }
    if (failures) { std::fprintf(stderr, "stereo_render: %d failure(s)\n", failures); return 1; }
    std::puts("stereo_render: ok");
    return 0;
}
