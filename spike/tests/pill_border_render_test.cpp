// The pill and its morph as the real compositor draws them (GlCompositor::draw_and_swap on surfaceless EGL, one canvas px
// per pixel): at every size, curvature and roll, round you, in the last moment of a zoom and all along the pill <-> panel
// morph, the glass sits a small fixed gap clear of the selected desktop's border and never on it (glass.hpp glass_pill,
// view.hpp glass_to_desktop). The border grows with its desktop and the glass does not, which is what put a bigger
// desktop's pill on its border (Cody, in the glasses, 2026-11-03).
//
// The border alone and the glass alone are drawn on a black desktop, so a lit pixel is that and nothing else (the glass's
// shadow is black: it draws nothing here), and the two masks are compared. With a directory argument the frames holding
// both are also written there as binary PPM. Exit 77 (skipped) without EGL/GLES.
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/view.hpp"
#include <GLES2/gl2.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

constexpr int kW = 1536, kH = 960;
constexpr std::uint32_t kDesktopW = 64, kDesktopH = 36;
constexpr std::array<std::uint8_t, 3> kAccent{0x7a, 0xa2, 0xf7};  // an Omarchy theme accent (Tokyo Night)
const Mat3 kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1};
// The empty rows between the border and the glass, laid out as kGlassPillGap - kBorderPx. Square to the view that is what
// is drawn at every size, less the row the glass's anti-aliased edge takes when the edge lands between pixels (glass_test
// pins the gap itself to 0.05 px). Rolled, both edges rasterize as stairs, which takes up to 1.5 px off it.
constexpr float kRows = kGlassPillGap - kBorderPx, kSquareFewest = kRows - 1.f, kRolledFewest = kRows - 1.5f, kMostRows = kRows + 1.f;
constexpr float kNoLimit = std::numeric_limits<float>::max();

struct Pose { const char* name; Placement placement; bool square = false; };
const Pose kPoses[] = {
    {"flat", {0, 0, 1, 0, 0}, true},
    {"curved", {0, 0, 1, 1.f, 0}},
    {"most-curved", {0, 0, 1, 2.0944f, 0}},
    {"rolled", {0, 0, 1, 0, 0.3f}},
    {"rolled-back", {0, 0, 1, 0, -0.74f}},
    {"on-its-side", {0, 0, 1, 0, 1.5708f}},
    {"turned", {0.15f, 0.1f, 1, 0, 0}},
    {"everything", {-0.1f, 0.08f, 1, 0.8f, -0.4f}},
};

struct Scene { FRect tile; Placement placement; float expand = 0; };
using Pixels = std::vector<std::uint8_t>;  // RGB, top-down

Pixels draw(GlCompositor& gl, const Scene& s, bool border, bool glass, float half_w, float half_h) {
    GlassFrame frame;
    frame.pills = glass;
    frame.expand[0] = glass ? s.expand : 0.f;
    frame.top = glass && s.expand > 0.f ? 0 : -1;
    gl.set_glass(frame);
    gl.set_free_pointer(false, 0, 0, 0);
    CHECK(gl.draw_and_swap({s.tile}, {s.placement}, border ? 0 : -1, 1.f, kIdentity, half_w, half_h));
    Pixels rgba(std::size_t(kW) * kH * 4), rgb(std::size_t(kW) * kH * 3);
    glFinish();
    glReadPixels(0, 0, kW, kH, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    for (int y = 0; y < kH; ++y)  // GL rows are bottom-up
        for (int x = 0; x < kW; ++x)
            for (int c = 0; c < 3; ++c) rgb[(std::size_t(y) * kW + std::size_t(x)) * 3 + std::size_t(c)] = rgba[(std::size_t(kH - 1 - y) * kW + std::size_t(x)) * 4 + std::size_t(c)];
    CHECK(glGetError() == GL_NO_ERROR);
    return rgb;
}

// The border's pixels: the accent itself (its glow, under it, is a fifth as bright at most).
std::vector<char> border_mask(const Pixels& f) {
    std::vector<char> mask(std::size_t(kW) * kH);
    for (std::size_t i = 0; i < mask.size(); ++i)
        mask[i] = std::abs(int(f[i * 3]) - kAccent[0]) <= 8 && std::abs(int(f[i * 3 + 1]) - kAccent[1]) <= 8 && std::abs(int(f[i * 3 + 2]) - kAccent[2]) <= 8;
    return mask;
}
// The glass's pixels: anything lit on the black desktop, its faintest anti-aliased edge included.
std::vector<char> glass_mask(const Pixels& f) {
    std::vector<char> mask(std::size_t(kW) * kH);
    for (std::size_t i = 0; i < mask.size(); ++i) mask[i] = std::max({f[i * 3], f[i * 3 + 1], f[i * 3 + 2]}) > 8;
    return mask;
}

// Empty pixels on the shortest way from any glass pixel to the border (a chamfer distance, a hair over the true one on a
// diagonal). < 0: they overlap.
float clearance(const std::vector<char>& border, const std::vector<char>& glass) {
    constexpr float kFar = std::numeric_limits<float>::max() / 2, kDiagonal = 1.41421356f;
    std::vector<float> d(border.size());
    for (std::size_t i = 0; i < d.size(); ++i) d[i] = border[i] ? 0.f : kFar;
    auto at = [&](int x, int y) -> float& { return d[std::size_t(y) * kW + std::size_t(x)]; };
    for (int y = 0; y < kH; ++y)
        for (int x = 0; x < kW; ++x) {
            float& v = at(x, y);
            if (x > 0) v = std::min(v, at(x - 1, y) + 1);
            if (y > 0) {
                v = std::min(v, at(x, y - 1) + 1);
                if (x > 0) v = std::min(v, at(x - 1, y - 1) + kDiagonal);
                if (x + 1 < kW) v = std::min(v, at(x + 1, y - 1) + kDiagonal);
            }
        }
    for (int y = kH - 1; y >= 0; --y)
        for (int x = kW - 1; x >= 0; --x) {
            float& v = at(x, y);
            if (x + 1 < kW) v = std::min(v, at(x + 1, y) + 1);
            if (y + 1 < kH) {
                v = std::min(v, at(x, y + 1) + 1);
                if (x + 1 < kW) v = std::min(v, at(x + 1, y + 1) + kDiagonal);
                if (x > 0) v = std::min(v, at(x - 1, y + 1) + kDiagonal);
            }
        }
    float nearest = kFar;
    for (std::size_t i = 0; i < d.size(); ++i) if (glass[i]) nearest = std::min(nearest, d[i]);
    return nearest - 1;  // border next to glass: nothing between them, 0
}

void write_ppm(const std::string& path, const Pixels& f) {
    std::ofstream out(path, std::ios::binary);
    out << "P6\n" << kW << ' ' << kH << "\n255\n";
    out.write(reinterpret_cast<const char*>(f.data()), std::streamsize(f.size()));
}
}  // namespace

int main(int argc, char** argv) {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kW, kH, 1, why);
    if (!gl) { std::printf("SKIP: no offscreen compositor: %s\n", why.c_str()); return 77; }
    gl->resize(kW, kH, kW, kH);
    gl->set_accent(kAccent[0], kAccent[1], kAccent[2]);
    const std::vector<std::uint8_t> black(std::size_t(kDesktopW) * kDesktopH * 4);
    CHECK(gl->upload(0, black.data(), kDesktopW, kDesktopH, kDesktopW * 4));
    const double t = std::tan(46.0 * M_PI / 360.0), a = double(kW) / kH;  // the renderer's FOV fallback
    const float half_w = float(t * a / std::sqrt(1 + a * a)), half_h = float(t / std::sqrt(1 + a * a));
    const std::string dir = argc > 1 ? argv[1] : "";

    int scenes = 0, overlapping = 0;
    float least = std::numeric_limits<float>::max(), most = -least;
    auto scene = [&](const std::string& name, const Scene& s, float fewest, float most_allowed) {
        const auto border = border_mask(draw(*gl, s, true, false, half_w, half_h));
        const auto glass = glass_mask(draw(*gl, s, false, true, half_w, half_h));
        long on_it = 0, lit = 0, framed = 0;
        for (std::size_t i = 0; i < glass.size(); ++i) { lit += glass[i]; framed += border[i]; on_it += glass[i] && border[i]; }
        if (!lit || !framed) { std::fprintf(stderr, "%s: nothing drawn to compare (%ld glass, %ld border)\n", name.c_str(), lit, framed); ++failures; return; }
        const float rows = on_it ? -1.f : clearance(border, glass);
        ++scenes; overlapping += on_it > 0;
        least = std::min(least, rows); most = std::max(most, rows);
        if (on_it) std::fprintf(stderr, "%s: %ld of %ld glass pixels on the border\n", name.c_str(), on_it, lit);
        else if (rows < fewest || rows > most_allowed) { std::fprintf(stderr, "%s: %.1f rows between the border and the glass\n", name.c_str(), rows); ++failures; }
        CHECK(on_it == 0);
        if (!dir.empty()) write_ppm(dir + "/" + name + ".ppm", draw(*gl, s, true, true, half_w, half_h));
    };
    auto label = [](const char* tile, const char* pose, float d, float e) {
        char b[96]; std::snprintf(b, sizeof b, "%s-%s-d%03d-e%03d", tile, pose, int(std::lround(d * 100)), int(std::lround(e * 100)));
        return std::string(b);
    };

    // A small desktop, to see it grown ten times, and one the size of a real overview tile, as far as it stays in view.
    const FRect small{718, 452, 100, 56}, real{475, 315, 586, 330};
    // The closed pill: every size, every pose. It is 14 px tall and laid out straight under the border, so the gap is
    // the same all along it.
    for (const Pose& pose : kPoses) {
        const float fewest = pose.square ? kSquareFewest : kRolledFewest, most_allowed = pose.square ? kRows : kMostRows;
        for (const float d : {2.f, 1.5f, 1.f, 0.8f, 0.61f, 0.59f, 0.5f, 0.35f, 0.25f, 0.15f, 0.1f}) {
            Placement p = pose.placement; p.distance = d;
            scene(label("small", pose.name, d, 0), {small, p, 0}, fewest, most_allowed);
        }
        for (const float d : {2.f, 1.f, 0.8f, 0.59f, 0.5f, 0.4f}) {
            Placement p = pose.placement; p.distance = d;
            scene(label("real", pose.name, d, 0), {real, p, 0}, fewest, most_allowed);
        }
    }
    // The morph, and the open panel: it shares the pill's top all the way. It is wide, so on a turned or curved desktop its
    // corners are not as square to the view as its middle: only how close it comes is bounded.
    for (const Pose& pose : kPoses) {
        if (pose.placement.curvature > 1.5f) continue;  // the panel would wrap round the desktop's narrow arc
        for (const float e : {0.25f, 0.5f, 0.75f, 1.f})
            for (const float d : {1.f, 0.8f, 0.59f, 0.5f, 0.4f}) {
                Placement p = pose.placement; p.distance = d;
                scene(label("real", pose.name, d, e), {real, p, e}, kRolledFewest, kNoLimit);
            }
    }
    // The last moment of a zoom: the pills show below 1% (wayland.cpp `overview`), on the blended layout and placements.
    {
        const Canvas canvas{float(kW), float(kH), half_w, half_h};
        const std::vector<Size> sizes{{1920, 1080}};  // kept small enough by its spacing to stay in view, made bigger
        const auto overview = overview_layout(sizes, float(kW), float(kH), 400.f);
        const auto now = blended_layout(sizes, float(kW), float(kH), 400.f, 0.009f, 0.f);
        const std::vector<Placement> resized{{0, 0, 0.6f, 0, 0.2f}};
        const auto drawn = zoomed_placements(canvas, overview, now, resized, 0.009f, 0.f);
        scene("zooming", {now[0], drawn[0], 0}, kRolledFewest, kMostRows);
    }
    std::printf("pill border render: %d scenes, %d with glass on the border, %.1f..%.1f rows clear\n", scenes, overlapping, double(least), double(most));
    std::printf("%s\n", failures ? "pill border render test FAILED" : "pill border render test passed");
    return failures ? 1 : 0;
}
