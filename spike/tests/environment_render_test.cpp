// The backgrounds drawn by the real compositor (GlCompositor::draw_and_swap on surfaceless EGL, the glasses' 1920x1200
// buffer under scale 1.25) behind three synthetic desktops, the glass pills, the cursor dot and the highlight:
//   * None (or level 0) the frame is bit for bit the frame of a compositor that never heard of it;
//   * on, every desktop's pixels are exactly as they are with None, and the highlight's glow adds to the grid instead of
//     blacking out a halo round the selected desktop;
//   * the grid alone, looking every way round: nothing passes the 191/255 OLED cap, most of the view stays black
//     (see-through), something is drawn (the horizon ahead, the floor below), and the sky straight up is black;
//   * world-locked: the horizon is where the head pitch puts it, a whole turn draws the same frame, and in locked mode
//     (desktops head-locked) the grid still turns with the head while the desktops do not;
//   * its colour is the theme's, the accent the highlight border is drawn in: every lit pixel has that hue, and a theme
//     switch recolours the very next frame; the fade and the brightness scale it, black stays black at any brightness and
//     nothing passes the cap at the brightest; the pulses move and loop seamlessly (kEnvironmentLoopS);
//   * behind a 180 degree video (VideoSphere over draw_environment, in a draw_scene_and_swap): the picture exactly as
//     without it, the rest of the view exactly the grid.
// With a directory argument it also writes preview PNGs there (needs zlib): the overview ahead and looking round, maximized
// (follow / locked), theme colours, half faded, brightness, None, the placement panel with the Background list open and with
// the Brightness slider held, and a 180 degree video, and the same for Smoke (smoke-*.png). `DIR --smoke-frames N STEP` writes
// only N numbered Smoke frames STEP seconds apart there, for an animation. Exit 77 (skipped) without EGL/GLES.
//
// Smoke has its own section: the desktops untouched over it, under the cap every way round with clear air exactly black at
// any brightness, the theme's hue, no seam anywhere on the sphere, world-locked, and motion that is slow and loops.
#include "gl.hpp"
#include "video_gl.hpp"
#include "xr/environment.hpp"
#include "xr/glass.hpp"
#include "xr/hud.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <GLES2/gl2.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>
#ifdef XR_HAVE_ZLIB
#include <zlib.h>
#endif

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

constexpr std::uint32_t kCanvasW = 1536, kCanvasH = 960, kPxW = 1920, kPxH = 1200;  // the glasses at scale 1.25
constexpr std::uint32_t kDesktopW = 1920, kDesktopH = 1080;
constexpr std::array<std::uint8_t, 3> kTokyo{0x7a, 0xa2, 0xf7};  // an Omarchy theme accent (Tokyo Night)
constexpr std::uint8_t kCap = 191;

struct Image { std::uint32_t w = 0, h = 0; std::vector<std::uint8_t> rgb; };  // top-down

// --- synthetic desktops: a wallpaper and a window of text, as B,G,R,X (wl_shm XRGB) --------------------------------
struct Desktop {
    std::vector<std::uint8_t> px = std::vector<std::uint8_t>(std::size_t(kDesktopW) * kDesktopH * 4);
    void fill(int x0, int y0, int x1, int y1, std::array<int, 3> rgb) {
        for (int y = std::max(y0, 0); y < std::min(y1, int(kDesktopH)); ++y)
            for (int x = std::max(x0, 0); x < std::min(x1, int(kDesktopW)); ++x) {
                auto* p = &px[(std::size_t(y) * kDesktopW + std::size_t(x)) * 4];
                p[0] = std::uint8_t(rgb[2]); p[1] = std::uint8_t(rgb[1]); p[2] = std::uint8_t(rgb[0]); p[3] = 255;
            }
    }
    void text(const HudFont* font, const std::string& s, int x, int baseline, std::uint32_t size, std::array<int, 3> rgb, bool bold = false) {
        if (!font) { fill(x, baseline - int(size) * 2 / 3, x + int(s.size() * size * 6 / 10), baseline, {rgb[0] / 3, rgb[1] / 3, rgb[2] / 3}); return; }
        font->draw(s, size, bold, x, baseline, [&](int px_x, int px_y, float a) {
            if (px_x < 0 || px_y < 0 || px_x >= int(kDesktopW) || px_y >= int(kDesktopH)) return;
            auto* p = &px[(std::size_t(px_y) * kDesktopW + std::size_t(px_x)) * 4];
            const float c = std::clamp(a, 0.f, 1.f);
            p[0] = std::uint8_t(float(p[0]) * (1 - c) + float(rgb[2]) * c);
            p[1] = std::uint8_t(float(p[1]) * (1 - c) + float(rgb[1]) * c);
            p[2] = std::uint8_t(float(p[2]) * (1 - c) + float(rgb[0]) * c);
        });
    }
};
std::array<Desktop, 3> desktops(const HudFont* font) {
    std::array<Desktop, 3> d;
    const std::array<std::array<int, 3>, 3> wall{{{16, 18, 30}, {20, 16, 28}, {14, 20, 26}}};
    for (std::size_t i = 0; i < 3; ++i) {
        auto& k = d[i];
        for (std::uint32_t y = 0; y < kDesktopH; ++y) {  // a soft vertical wallpaper gradient
            const float t = float(y) / kDesktopH;
            k.fill(0, int(y), int(kDesktopW), int(y) + 1, {int(wall[i][0] * (1.4f - t * 0.6f)), int(wall[i][1] * (1.4f - t * 0.6f)), int(wall[i][2] * (1.4f - t * 0.6f))});
        }
        k.fill(0, 0, int(kDesktopW), 34, {22, 22, 30});  // the bar
        k.text(font, i == 0 ? "1  2  3" : i == 1 ? "1  2" : "1", 20, 24, 18, {150, 160, 190});
        k.text(font, "XR-" + std::to_string(i + 1), int(kDesktopW) / 2 - 30, 24, 18, {200, 205, 220}, true);
        k.text(font, "15:19", int(kDesktopW) - 90, 24, 18, {150, 160, 190});
        k.fill(60, 80, int(kDesktopW) - 60, int(kDesktopH) - 50, {kTokyo[0], kTokyo[1], kTokyo[2]});  // the window border
        k.fill(62, 82, int(kDesktopW) - 62, int(kDesktopH) - 52, {26, 27, 38});
    }
    const std::vector<std::string> shell{"cody@omarchy ~/Projects/viture-workspace $ git log --oneline -4",
                                         "e8c44e5 Merge origin/placement-scale-dropdown",
                                         "0f89db5 Merge origin/pill-material-design", "4ff0a84 Merge origin/prestart-simple-ui",
                                         "1f4a608 Merge origin/layout-switch-seamless",
                                         "cody@omarchy ~/Projects/viture-workspace $ ctest --test-dir build/release",
                                         "100% tests passed, 0 tests failed out of 71", "cody@omarchy ~/Projects/viture-workspace $ _"};
    for (std::size_t l = 0; l < shell.size(); ++l)
        d[0].text(font, shell[l], 100, 150 + int(l) * 44, 28, l == 0 || l >= 5 ? std::array<int, 3>{158, 206, 106} : std::array<int, 3>{192, 202, 245});
    const std::vector<std::string> code{"#include \"xr/environment.hpp\"", "", "namespace xr {",
                                        "Mat3 environment_view(Quat head, const std::optional<Quat>& ref) {",
                                        "    // world-locked, levelled to gravity", "    const Quat a = ref ? anchor(*ref) : Quat{};",
                                        "    return view_from(head, a);", "}", "}  // namespace xr"};
    for (std::size_t l = 0; l < code.size(); ++l)
        d[1].text(font, code[l], 100, 150 + int(l) * 44, 28, l == 4 ? std::array<int, 3>{86, 95, 137} : std::array<int, 3>{187, 154, 247});
    d[2].text(font, "XR Workspace", 100, 190, 64, {200, 210, 235}, true);
    const std::vector<std::string> page{"Choose a background behind your desktops.", "Ctrl+Alt+E     background on / off",
                                        "Its colour is your theme's.", "Look round: it stays put in the room."};
    for (std::size_t l = 0; l < page.size(); ++l) d[2].text(font, page[l], 100, 300 + int(l) * 56, 34, {170, 180, 210});
    return d;
}

// --- the compositor ------------------------------------------------------------------------------------------------
float g_half_w = 0, g_half_h = 0;
std::unique_ptr<GlCompositor> make(const std::array<Desktop, 3>& d, std::string& why) {
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, 3, why);
    if (!gl) return nullptr;
    gl->resize(kCanvasW, kCanvasH, kPxW, kPxH);
    gl->set_accent(kTokyo[0], kTokyo[1], kTokyo[2]);
    for (std::size_t i = 0; i < 3; ++i) CHECK(gl->upload(i, d[i].px.data(), kDesktopW, kDesktopH, kDesktopW * 4));
    const double t = std::tan(46.0 * M_PI / 360.0), a = double(kCanvasW) / kCanvasH;  // the renderer's FOV fallback
    g_half_w = float(t * a / std::sqrt(1 + a * a)); g_half_h = float(t / std::sqrt(1 + a * a));
    return gl;
}
// Head orientation (driver frame, relative to a level recentre reference): turned `yaw` degrees left, `pitch` up.
Quat look(float yaw, float pitch) {
    const float y = yaw * float(M_PI) / 360.f, p = -pitch * float(M_PI) / 360.f;
    return quat_mul(Quat{0, 0, std::sin(y), std::cos(y)}, Quat{0, std::sin(p), 0, std::cos(p)});
}
const Mat3 kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1};

Image read() {
    Image im{kPxW, kPxH, std::vector<std::uint8_t>(std::size_t(kPxW) * kPxH * 3)};
    std::vector<std::uint8_t> rgba(std::size_t(kPxW) * kPxH * 4);
    glFinish(); glReadPixels(0, 0, GLsizei(kPxW), GLsizei(kPxH), GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    for (std::uint32_t y = 0; y < kPxH; ++y)  // GL rows are bottom-up
        for (std::uint32_t x = 0; x < kPxW; ++x)
            std::memcpy(&im.rgb[(std::size_t(y) * kPxW + x) * 3], &rgba[(std::size_t(kPxH - 1 - y) * kPxW + x) * 4], 3);
    CHECK(glGetError() == GL_NO_ERROR);
    return im;
}

struct Frame {
    bool desktops = true, pills = false, pointer = false;
    bool bars = false;              // every desktop's level bar and corner caps (drawn with the pills: overview only)
    float zoom = 0;                 // 0 overview, 1 maximized (desktop 1)
    int highlight = 1;
    Mat3 view = kIdentity;          // the desktops' rotation (anchor: the head; locked / follow: identity here)
    bool environment = true; bool touch = true;  // touch false: never call set_environment (a compositor without it)
    Background scene = Background::grid;
    float level = 1, brightness = 100, seconds = 3.25f; Mat3 world = kIdentity;
    const HudImage* panel = nullptr;  // desktop 1's pill open into its placement panel, showing this
};
Image draw(GlCompositor& gl, const Frame& f) {
    const std::vector<Size> sizes(3, Size{float(kDesktopW), float(kDesktopH)});
    const auto tiles = f.desktops ? blended_layout(sizes, float(kCanvasW), float(kCanvasH), 40.f, f.zoom, 1.f) : std::vector<FRect>{};
    GlassFrame glass; glass.pills = f.pills && f.zoom < 0.01f;
    if (f.bars) glass.bars.fill(true);
    static std::uint64_t generation = 0;
    if (f.panel) { glass.expand[1] = 1; glass.top = 1; glass.content = 1; glass.image = f.panel; glass.generation = ++generation; glass.content_alpha = 1; }
    gl.set_glass(glass);
    gl.set_free_pointer(f.pointer && f.zoom < 0.01f, float(kCanvasW) * 0.58f, float(kCanvasH) * 0.47f, kFreePointerLevel);
    if (f.touch) gl.set_environment(f.level, f.environment ? f.scene : Background::none, f.brightness, f.seconds, f.world);
    CHECK(gl.draw_and_swap(tiles, std::vector<Placement>(tiles.size()), f.desktops ? f.highlight : -1, 1.f - f.zoom, f.view, g_half_w, g_half_h));
    return read();
}
// A 180 degree video as wayland.cpp and VideoMode::draw do it: the background, then the picture over it where it reaches.
Image draw_video(GlCompositor& gl, VideoSphere& sphere, GLuint picture, const Frame& f) {
    VideoFormat format; format.projection = VideoProjection::equirect180;
    gl.set_environment(f.level, f.environment ? f.scene : Background::none, f.brightness, f.seconds, f.world);
    CHECK(gl.draw_scene_and_swap([&](std::uint32_t w, std::uint32_t h) {
        glViewport(0, 0, GLsizei(w), GLsizei(h)); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
        const bool behind = gl.draw_environment(g_half_w, g_half_h);
        sphere.draw(picture, false, format, 0, f.world, g_half_w, g_half_h, behind);
    }, nullptr, 0, 0.f));
    return read();
}
GLuint picture_texture() {  // a video frame, nowhere black: a dusk sky and a low sun; an RGBA texture with mipmaps, as the player makes
    constexpr int W = 1024, H = 512;
    std::vector<std::uint8_t> rgba(std::size_t(W) * H * 4);
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) {
            const float t = float(y) / H, sun = std::exp(-(std::pow((float(x) - 900.f) / 90.f, 2.f) + std::pow((float(y) - 300.f) / 60.f, 2.f)));
            auto* p = &rgba[(std::size_t(y) * W + std::size_t(x)) * 4];
            p[0] = std::uint8_t(std::min(255.f, 40 + 190 * t + 90 * sun)); p[1] = std::uint8_t(std::min(255.f, 24 + 90 * t + 150 * sun));
            p[2] = std::uint8_t(std::min(255.f, 90 - 40 * t + 60 * sun)); p[3] = 255;
        }
    GLuint tex = 0; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    glGenerateMipmap(GL_TEXTURE_2D);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    return tex;
}

std::uint8_t channel_max(const Image& im, std::size_t i) { return std::max({im.rgb[i * 3], im.rgb[i * 3 + 1], im.rgb[i * 3 + 2]}); }
// Desktop `i`'s rect in buffer px (overview, head straight ahead: the canvas maps 1:1 onto the view), `inset` px inside.
struct PxRect { int x0, y0, x1, y1; bool in(int x, int y) const { return x >= x0 && x < x1 && y >= y0 && y < y1; } };
PxRect tile_px(std::size_t i, float zoom, int inset) {
    const std::vector<Size> sizes(3, Size{float(kDesktopW), float(kDesktopH)});
    const auto t = blended_layout(sizes, float(kCanvasW), float(kCanvasH), 40.f, zoom, 1.f)[i];
    const float s = float(kPxW) / kCanvasW;
    return {int(std::ceil(t.x * s)) + inset, int(std::ceil(t.y * s)) + inset, int(std::floor((t.x + t.width) * s)) - inset,
            int(std::floor((t.y + t.height) * s)) - inset};
}

#ifdef XR_HAVE_ZLIB
void put32(std::vector<std::uint8_t>& out, std::uint32_t v) { for (int s = 24; s >= 0; s -= 8) out.push_back(std::uint8_t(v >> s)); }
void chunk(std::vector<std::uint8_t>& out, const char* type, const std::vector<std::uint8_t>& data) {
    put32(out, std::uint32_t(data.size()));
    const std::size_t start = out.size();
    out.insert(out.end(), type, type + 4); out.insert(out.end(), data.begin(), data.end());
    put32(out, std::uint32_t(crc32(0, out.data() + start, uInt(out.size() - start))));
}
bool write_png(const std::filesystem::path& path, const Image& im) {
    std::vector<std::uint8_t> raw;
    raw.reserve(std::size_t(im.h) * (im.w * 3 + 1));
    for (std::uint32_t y = 0; y < im.h; ++y) {  // filter "sub" per row: mostly black and flat, it compresses well
        raw.push_back(1);
        const auto* row = &im.rgb[std::size_t(y) * im.w * 3];
        for (std::uint32_t x = 0; x < im.w * 3; ++x) raw.push_back(std::uint8_t(row[x] - (x >= 3 ? row[x - 3] : 0)));
    }
    uLongf size = compressBound(uLong(raw.size()));
    std::vector<std::uint8_t> z(size);
    if (compress2(z.data(), &size, raw.data(), uLong(raw.size()), 9) != Z_OK) return false;
    z.resize(size);
    std::vector<std::uint8_t> png{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}, header;
    put32(header, im.w); put32(header, im.h); header.insert(header.end(), {8, 2, 0, 0, 0});  // 8-bit RGB
    chunk(png, "IHDR", header); chunk(png, "IDAT", z); chunk(png, "IEND", {});
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    const bool ok = std::fwrite(png.data(), 1, png.size(), f) == png.size();
    return std::fclose(f) == 0 && ok;
}
#endif
}  // namespace

int main(int argc, char** argv) {
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    if (!font) std::printf("note: %s; desktops use blocks for text\n", why.c_str());
    const auto content = desktops(font.get());
    auto world = [](float yaw, float pitch) { return environment_view(look(yaw, pitch), Quat{}); };
    auto anchor_view = [](float yaw, float pitch) { return view_from(look(yaw, pitch), Quat{}); };

    // 1. Off is exactly the frame without it: a compositor that never had set_environment called, then (only one compositor
    // at a time: they would share the EGL display) the same frame after the grid has been on and gone off again.
    Frame base; base.pills = true; base.pointer = true;
    Frame never = base; never.touch = false;
    Frame off = base; off.environment = false;
    std::unique_ptr<GlCompositor> gl;
    Image plain;
    {
        auto fresh = make(content, why);
        if (!fresh) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
        plain = draw(*fresh, never);
    }
    gl = make(content, why);
    if (!gl) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
    CHECK(gl->set_environment(0.f, Background::none, 100.f, 0.f, kIdentity));  // the pass compiled
#ifdef XR_HAVE_ZLIB
    if (argc > 4 && std::string(argv[2]) == "--smoke-frames") {  // N stills, STEP seconds apart, for an animation; no checks
        std::filesystem::create_directories(argv[1]);
        const int n = std::atoi(argv[3]);
        const float step = float(std::atof(argv[4]));
        for (int i = 0; i < n; ++i) {
            Frame f = base; f.scene = Background::smoke; f.world = world(0, -6); f.seconds = 20.f + step * float(i);
            char name[32]; std::snprintf(name, sizeof name, "%04d.png", i);
            CHECK(write_png(std::filesystem::path(argv[1]) / name, draw(*gl, f)));
        }
        return failures != 0;
    }
#endif
    (void)draw(*gl, base);
    {
        const Image b = draw(*gl, off);
        CHECK(plain.rgb == b.rgb);
        if (plain.rgb != b.rgb) std::fprintf(stderr, "off frame differs from a compositor without the grid\n");
    }

    // 2. On: the desktops' own pixels are untouched; the highlight's glow adds to the grid (no black halo).
    Frame on = base; on.world = world(0, -6); on.view = kIdentity;
    const Image with = draw(*gl, on), without = draw(*gl, off);
    Frame grid_only = on; grid_only.desktops = false; grid_only.pills = grid_only.pointer = false;
    const Image alone = draw(*gl, grid_only);
    {
        std::size_t differ = 0, checked = 0;
        for (std::size_t i = 0; i < 3; ++i) {
            const PxRect r = tile_px(i, 0.f, i == 1 ? 3 : 24);  // the side desktops turn to face you: not quite this rect
            for (int y = r.y0; y < r.y1; ++y)
                for (int x = r.x0; x < r.x1; ++x) {
                    const std::size_t k = (std::size_t(y) * kPxW + std::size_t(x)) * 3;
                    ++checked; differ += std::memcmp(&with.rgb[k], &without.rgb[k], 3) != 0;
                }
        }
        if (differ) {
            std::fprintf(stderr, "%zu of %zu desktop pixels changed under the grid:", differ, checked);
            int shown = 0;
            for (std::size_t i = 0; i < 3 && shown < 8; ++i) {
                const PxRect r = tile_px(i, 0.f, i == 1 ? 3 : 24);
                for (int y = r.y0; y < r.y1 && shown < 8; ++y)
                    for (int x = r.x0; x < r.x1 && shown < 8; ++x) {
                        const std::size_t k = (std::size_t(y) * kPxW + std::size_t(x)) * 3;
                        if (std::memcmp(&with.rgb[k], &without.rgb[k], 3)) { ++shown; std::fprintf(stderr, " (%d,%d %d/%d/%d vs %d/%d/%d)", x, y, with.rgb[k], with.rgb[k + 1], with.rgb[k + 2], without.rgb[k], without.rgb[k + 1], without.rgb[k + 2]); }
                    }
            }
            std::fprintf(stderr, "\n");
        }
        CHECK(checked > 400'000 && differ == 0);
        // Round the highlighted desktop (outside its border, inside the glow; not below it, where its pill is): at least the
        // grid, and at least the glow.
        const PxRect outer = tile_px(1, 0.f, -40), inner = tile_px(1, 0.f, -10);
        std::size_t darker = 0, band = 0;
        for (int y = std::max(outer.y0, 0); y < std::min(inner.y1, int(kPxH)); ++y)  // above its bottom edge: not its pill
            for (int x = std::max(outer.x0, 0); x < std::min(outer.x1, int(kPxW)); ++x) {
                if (inner.in(x, y) || tile_px(0, 0.f, -12).in(x, y) || tile_px(2, 0.f, -12).in(x, y)) continue;
                const std::size_t k = (std::size_t(y) * kPxW + std::size_t(x)) * 3;
                ++band;
                for (int c = 0; c < 3; ++c)
                    if (with.rgb[k + std::size_t(c)] + 1 < alone.rgb[k + std::size_t(c)] || with.rgb[k + std::size_t(c)] + 1 < without.rgb[k + std::size_t(c)]) { ++darker; break; }
            }
        if (darker) std::fprintf(stderr, "glow band: %zu of %zu px darker than the grid or the glow alone\n", darker, band);
        CHECK(band > 10000 && darker == 0);
    }

    // 3. The grid alone, every way round: under the cap, mostly black, something drawn; the sky straight up is black.
    struct Dir { const char* name; float yaw, pitch; };
    for (const Dir d : {Dir{"ahead", 0, 0}, Dir{"down", 0, -50}, Dir{"left", 80, -5}, Dir{"behind", 180, 0}, Dir{"right-down", -120, -25},
                        Dir{"up", 0, 60}, Dir{"tilted", 30, -15}}) {
        Frame g = grid_only; g.world = world(d.yaw, d.pitch);
        const Image im = draw(*gl, g);
        std::size_t dark = 0, lit = 0, over = 0;
        for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
            const auto m = channel_max(im, i);
            dark += m <= 8; lit += m >= 24; over += m > kCap;
        }
        const double n = double(kPxW) * kPxH;
        std::printf("grid %-10s dark %.1f%%  lit %.2f%%  over cap %zu\n", d.name, 100.0 * double(dark) / n, 100.0 * double(lit) / n, over);
        CHECK(over == 0);
        CHECK(double(dark) / n >= 0.6);
        if (d.pitch < 40) CHECK(lit > 2000); else CHECK(lit == 0);  // straight up: black sky
    }
    // The floor seen from above is mostly black between its lines too.
    {
        Frame g = grid_only; g.world = world(0, -89);
        const Image im = draw(*gl, g);
        std::size_t dark = 0;
        for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) dark += channel_max(im, i) <= 8;
        CHECK(double(dark) / (double(kPxW) * kPxH) >= 0.6);
    }

    // 4. World-locked. The horizon is where the pitch puts it.
    auto horizon_row = [](const Image& im) {  // from the top: the sky is dark, the horizon as bright as anything below it
        std::vector<double> sum(kPxH);
        double most = 0;
        for (std::uint32_t y = 0; y < kPxH; ++y) {
            for (std::uint32_t x = 0; x < kPxW; ++x) sum[y] += im.rgb[(std::size_t(y) * kPxW + x) * 3 + 2];
            most = std::max(most, sum[y]);
        }
        std::uint32_t y = 0;
        while (y + 1 < kPxH && sum[y] < 0.8 * most) ++y;
        return y;
    };
    for (const float pitch : {0.f, 5.f, -7.f}) {
        Frame g = grid_only; g.world = world(0, pitch);
        const auto row = horizon_row(draw(*gl, g));
        const float expect = float(kPxH) / 2 * (1 + std::tan(pitch * float(M_PI) / 180.f) / g_half_h);
        if (std::fabs(float(row) - expect) > 4) std::fprintf(stderr, "pitch %.0f: horizon row %u, expected %.1f\n", pitch, row, expect);
        CHECK(std::fabs(float(row) - expect) <= 4);
    }
    {
        Frame a = grid_only; a.world = world(37, -12);
        Frame b = grid_only; b.world = world(37 + 360, -12);
        const Image ia = draw(*gl, a), ib = draw(*gl, b);
        std::size_t off_by_more = 0;
        for (std::size_t k = 0; k < ia.rgb.size(); ++k) off_by_more += std::abs(int(ia.rgb[k]) - int(ib.rgb[k])) > 2;
        CHECK(off_by_more < ia.rgb.size() / 2000);  // float rounding of the turn only
        Frame c = grid_only; c.world = world(47, -12);
        CHECK(draw(*gl, c).rgb != ia.rgb);  // turning the head turns the grid
    }
    {  // Locked (desktops head-locked: view identity): the grid follows the head, the desktops do not.
        Frame l1 = base; l1.zoom = 1; l1.view = kIdentity; l1.world = world(0, -5);
        Frame l2 = l1; l2.world = world(-40, -5);
        const Image a = draw(*gl, l1), b = draw(*gl, l2);
        const PxRect r = tile_px(1, 1.f, 3);
        std::size_t same = 0, total = 0, changed_outside = 0;
        for (int y = 0; y < int(kPxH); ++y)
            for (int x = 0; x < int(kPxW); ++x) {
                const std::size_t k = (std::size_t(y) * kPxW + std::size_t(x)) * 3;
                if (r.in(x, y)) { ++total; same += std::memcmp(&a.rgb[k], &b.rgb[k], 3) == 0; }
                else changed_outside += std::memcmp(&a.rgb[k], &b.rgb[k], 3) != 0;
            }
        CHECK(total > 0 && same == total);
        CHECK(changed_outside > 1000);
    }

    // 5. Its colour is the theme's; the fade and the brightness scale it; the pulses move and loop seamlessly.
    {
        Frame full = grid_only; full.world = world(0, -30);
        Frame half = full; half.level = 0.5f;
        const Image a = draw(*gl, full), b = draw(*gl, half);
        auto sum = [](const Image& im) { double t = 0; for (const auto v : im.rgb) t += v; return t; };
        const double sa = sum(a);
        CHECK(sa > 0 && std::fabs(sum(b) / sa - 0.5) < 0.03);
        // Every lit pixel of the grid has the highlight border's own colour: the same channels in the same proportion, under
        // the cap, for a blue, a rose and a green theme, and the very next frame after a theme switch.
        for (const std::array<std::uint8_t, 3> accent : {kTokyo, std::array<std::uint8_t, 3>{0xf7, 0x76, 0x8e}, std::array<std::uint8_t, 3>{0x9e, 0xce, 0x6a}}) {
            gl->set_accent(accent[0], accent[1], accent[2]);
            Frame wide = grid_only; wide.world = world(30, -14);
            const Image im = draw(*gl, wide);
            const float top = float(std::max({accent[0], accent[1], accent[2]}));
            std::size_t lit = 0, off = 0;
            for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
                const auto m = channel_max(im, i);
                if (m < 100) continue;
                ++lit;
                for (std::size_t c = 0; c < 3; ++c) off += std::fabs(float(im.rgb[i * 3 + c]) / float(m) - float(accent[c]) / top) > 0.03f;
            }
            if (off) std::fprintf(stderr, "theme %02x%02x%02x: %zu channels of %zu lit pixels off its colour\n", accent[0], accent[1], accent[2], off, lit);
            CHECK(lit > 500 && off == 0);
        }
        gl->set_accent(kTokyo[0], kTokyo[1], kTokyo[2]);
        // ... and the border itself is that colour, uncapped: the same frame, on the highlighted desktop's edge.
        {
            const PxRect t = tile_px(1, 0.f, 0);
            const auto* p = &with.rgb[(std::size_t(t.y0 + (t.y1 - t.y0) / 2) * kPxW + std::size_t(t.x0 - 4)) * 3];
            CHECK(p[0] == kTokyo[0] && p[1] == kTokyo[1] && p[2] == kTokyo[2]);
        }
        // Brightness: it scales the scene, black stays exactly black at both ends, and the brightest stays under the cap.
        Frame dim = full; dim.brightness = 50;
        Frame most = full; most.brightness = 200;
        Frame least = full; least.brightness = 10;
        const Image id = draw(*gl, dim), im = draw(*gl, most), il = draw(*gl, least);
        CHECK(std::fabs(sum(id) / sa - 0.5) < 0.04);
        CHECK(sum(il) < 0.16 * sa && sum(il) > 0);
        CHECK(sum(im) > 1.3 * sa);
        std::size_t over = 0, black = 0, black_most = 0;
        for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
            over += channel_max(im, i) > kCap;
            black += channel_max(a, i) == 0; black_most += channel_max(im, i) == 0;
        }
        CHECK(over == 0);
        // (Only the far floor's faint average of its lines, under half a level at 100%, comes up to one.)
        if (double(black_most) <= 0.93 * double(black)) std::fprintf(stderr, "black pixels: %zu at 100%%, %zu at 200%%\n", black, black_most);
        CHECK(black > std::size_t(kPxW) * kPxH / 2 && double(black_most) > 0.93 * double(black));
        for (const float percent : {10.f, 100.f, 200.f}) {  // the sky, at any brightness
            Frame sky = grid_only; sky.world = world(0, 60); sky.brightness = percent;
            const Image is = draw(*gl, sky);
            CHECK(std::all_of(is.rgb.begin(), is.rgb.end(), [](std::uint8_t v) { return v == 0; }));
        }
        // Over the floor out to the horizon something is always running somewhere: the frame changes with the clock...
        Frame wide = grid_only; wide.world = world(20, -12);
        const Image w0 = draw(*gl, wide);
        std::size_t moved = 0;
        for (const float dt : {1.f, 2.5f, 7.f}) { Frame l = wide; l.seconds = wide.seconds + dt; moved += draw(*gl, l).rgb != w0.rgb; }
        CHECK(moved == 3);
        // ... and comes back to the same frame after a whole loop (only float rounding of the clock).
        Frame looped = wide; looped.seconds = wide.seconds + float(kEnvironmentLoopS);
        const Image e = draw(*gl, looped);
        std::size_t off_by_more = 0;
        for (std::size_t k = 0; k < w0.rgb.size(); ++k) off_by_more += std::abs(int(w0.rgb[k]) - int(e.rgb[k])) > 2;
        CHECK(off_by_more < w0.rgb.size() / 2000);
    }

    // 5b. Smoke. The desktops' pixels are as they are with None.
    Frame smoke = grid_only; smoke.scene = Background::smoke;
    {
        Frame over = on; over.scene = Background::smoke;
        const Image im = draw(*gl, over);
        std::size_t differ = 0, checked = 0;
        for (std::size_t i = 0; i < 3; ++i) {
            const PxRect r = tile_px(i, 0.f, i == 1 ? 3 : 24);
            for (int y = r.y0; y < r.y1; ++y)
                for (int x = r.x0; x < r.x1; ++x) {
                    const std::size_t k = (std::size_t(y) * kPxW + std::size_t(x)) * 3;
                    ++checked; differ += std::memcmp(&im.rgb[k], &without.rgb[k], 3) != 0;
                }
        }
        CHECK(checked > 400'000 && differ == 0);
    }
    // Every way round: under the cap, smoke somewhere, clear air exactly black, and no seam or pole: neighbouring pixels are
    // close everywhere, ahead, behind (where atan-based mapping would tear) and straight up and down.
    for (const Dir d : {Dir{"ahead", 0, 0}, Dir{"left", 80, -5}, Dir{"behind", 180, 0}, Dir{"right-down", -120, -25}, Dir{"up", 0, 60},
                        Dir{"zenith", 0, 89.5f}, Dir{"nadir", 0, -89.5f}}) {
        Frame g = smoke; g.world = world(d.yaw, d.pitch);
        const Image im = draw(*gl, g);
        std::size_t lit = 0, black = 0, over = 0;
        int jump = 0;
        for (std::uint32_t y = 0; y < kPxH; ++y)
            for (std::uint32_t x = 0; x < kPxW; ++x) {
                const std::size_t i = std::size_t(y) * kPxW + x;
                const auto m = channel_max(im, i);
                lit += m >= 24; black += m == 0; over += m > kCap;
                if (x + 1 < kPxW) jump = std::max(jump, std::abs(int(m) - int(channel_max(im, i + 1))));
                if (y + 1 < kPxH) jump = std::max(jump, std::abs(int(m) - int(channel_max(im, i + kPxW))));
            }
        const double n = double(kPxW) * kPxH;
        std::printf("smoke %-10s black %.1f%%  lit %.1f%%  over cap %zu  biggest step %d\n", d.name, 100.0 * double(black) / n, 100.0 * double(lit) / n, over, jump);
        CHECK(over == 0 && jump <= 12);
        if (std::fabs(d.pitch) < 70) CHECK(double(lit) / n > 0.02);  // (it thins out toward straight up)
    }
    {
        Frame ahead_smoke = smoke; ahead_smoke.world = world(0, -6);
        const Image a = draw(*gl, ahead_smoke);
        auto sum = [](const Image& im) { double t = 0; for (const auto v : im.rgb) t += v; return t; };
        const double sa = sum(a);
        // The theme's colour, every lit pixel, and the very next frame after a switch.
        for (const std::array<std::uint8_t, 3> accent : {kTokyo, std::array<std::uint8_t, 3>{0xf7, 0x76, 0x8e}, std::array<std::uint8_t, 3>{0x9e, 0xce, 0x6a}}) {
            gl->set_accent(accent[0], accent[1], accent[2]);
            const Image im = draw(*gl, ahead_smoke);
            const float top = float(std::max({accent[0], accent[1], accent[2]}));
            std::size_t lit = 0, off = 0;
            for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
                const auto m = channel_max(im, i);
                if (m < 60) continue;
                ++lit;
                for (std::size_t c = 0; c < 3; ++c) off += std::fabs(float(im.rgb[i * 3 + c]) / float(m) - float(accent[c]) / top) > 0.04f;
            }
            if (off) std::fprintf(stderr, "smoke, theme %02x%02x%02x: %zu channels of %zu lit pixels off its colour\n", accent[0], accent[1], accent[2], off, lit);
            CHECK(lit > 500 && off == 0);
        }
        gl->set_accent(kTokyo[0], kTokyo[1], kTokyo[2]);
        // The fade and the brightness scale it; clear air stays exactly black at both ends; the brightest stays under the cap.
        Frame half = ahead_smoke; half.level = 0.5f;
        CHECK(std::fabs(sum(draw(*gl, half)) / sa - 0.5) < 0.03);
        Frame dim = ahead_smoke; dim.brightness = 50;
        CHECK(std::fabs(sum(draw(*gl, dim)) / sa - 0.5) < 0.04);
        Frame most = ahead_smoke; most.brightness = 200;
        const Image m2 = draw(*gl, most);
        CHECK(sum(m2) > 1.5 * sa);
        std::size_t over = 0, black = 0, raised = 0;
        for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
            over += channel_max(m2, i) > kCap;
            black += channel_max(m2, i) == 0;
            raised += channel_max(m2, i) == 0 && channel_max(a, i) != 0;  // black at 200%, lit at 100%: impossible
        }
        std::printf("smoke black at 200%%: %.1f%%\n", 100.0 * double(black) / (double(kPxW) * kPxH));
        CHECK(over == 0 && raised == 0);
        CHECK(double(black) / (double(kPxW) * kPxH) > 0.03);
        // World-locked: a whole turn is the same frame, a small turn is not.
        Frame t0 = smoke; t0.world = world(37, -12);
        Frame t1 = smoke; t1.world = world(37 + 360, -12);
        Frame t2 = smoke; t2.world = world(47, -12);
        const Image i0 = draw(*gl, t0), i1 = draw(*gl, t1);
        std::size_t off_by_more = 0;
        for (std::size_t k = 0; k < i0.rgb.size(); ++k) off_by_more += std::abs(int(i0.rgb[k]) - int(i1.rgb[k])) > 2;
        CHECK(off_by_more < i0.rgb.size() / 2000);
        CHECK(draw(*gl, t2).rgb != i0.rgb);
        // Slow: a frame at 120 Hz changes nothing you could see; ten seconds is another shape, and a whole loop the same one.
        auto mean_change = [](const Image& p, const Image& q) { double t = 0; for (std::size_t k = 0; k < p.rgb.size(); ++k) t += std::abs(int(p.rgb[k]) - int(q.rgb[k])); return t / double(p.rgb.size()); };
        Frame later = ahead_smoke; later.seconds = ahead_smoke.seconds + 1.f / 120.f;
        Frame ten = ahead_smoke; ten.seconds = ahead_smoke.seconds + 10.f;
        Frame sixty = ahead_smoke; sixty.seconds = ahead_smoke.seconds + 60.f;
        const double step = mean_change(a, draw(*gl, later)), far = mean_change(a, draw(*gl, ten)), farther = mean_change(a, draw(*gl, sixty));
        std::printf("smoke mean change: %.3f a frame, %.2f in 10 s, %.2f in 60 s (levels)\n", step, far, farther);
        CHECK(step < 0.6 && far > 1.5 && farther > far);
        Frame looped = ahead_smoke; looped.seconds = ahead_smoke.seconds + float(kEnvironmentLoopS);
        Frame edge0 = ahead_smoke; edge0.seconds = 0.f;
        Frame edge1 = ahead_smoke; edge1.seconds = float(kEnvironmentLoopS) - 1.f / 120.f;
        CHECK(mean_change(a, draw(*gl, looped)) < 0.6);
        CHECK(mean_change(draw(*gl, edge0), draw(*gl, edge1)) < 0.6);  // the clock's wrap is one more frame
    }

    // Level bars and corner caps over the background (blended, premultiplied, after it and before the desktops): the marks
    // they leave over black they leave over every scene, and a maximized frame has none.
    {
        auto changed = [](const Image& a, const Image& b) {  // pixels apart by more than rounding
            std::size_t n = 0;
            for (std::size_t i = 0; i + 2 < a.rgb.size(); i += 3)
                n += std::abs(a.rgb[i] - b.rgb[i]) + std::abs(a.rgb[i + 1] - b.rgb[i + 1]) + std::abs(a.rgb[i + 2] - b.rgb[i + 2]) > 6;
            return n;
        };
        Frame black; black.pills = true; black.environment = false;
        Frame black_bars = black; black_bars.bars = true;
        const std::size_t shown = changed(draw(*gl, black_bars), draw(*gl, black));
        CHECK(shown > 500);
        for (const Background scene : {Background::grid, Background::smoke}) {
            Frame bare = black; bare.environment = true; bare.scene = scene;
            Frame barred = bare; barred.bars = true;
            const std::size_t over = changed(draw(*gl, barred), draw(*gl, bare));
            CHECK(over * 10 > shown * 8 && over * 10 < shown * 12);
            Frame bare_zoomed = bare, barred_zoomed = barred;
            bare_zoomed.zoom = barred_zoomed.zoom = 1;
            CHECK(changed(draw(*gl, barred_zoomed), draw(*gl, bare_zoomed)) == 0);
        }
    }

    // 6. Behind a 180 degree video: the picture exactly as without it, the rest exactly the background, looking past its edge.
    std::string sphere_why;
    auto sphere = VideoSphere::create(sphere_why);
    CHECK(sphere != nullptr);
    const GLuint picture = sphere ? picture_texture() : 0;
    if (sphere) for (const Background scene : {Background::grid, Background::smoke}) {
        Frame v = grid_only; v.scene = scene; v.world = world(90, -6);  // the picture's left edge down the middle of the view
        const Image grid = draw(*gl, v);
        Frame none = v; none.environment = false;
        const Image plain_video = draw_video(*gl, *sphere, picture, none), both = draw_video(*gl, *sphere, picture, v);
        std::size_t inside = 0, outside = 0, wrong = 0;
        for (std::size_t i = 0; i < std::size_t(kPxW) * kPxH; ++i) {
            const bool on_picture = channel_max(plain_video, i) != 0;  // the picture is nowhere black
            (on_picture ? inside : outside) += 1;
            wrong += std::memcmp(&both.rgb[i * 3], on_picture ? &plain_video.rgb[i * 3] : &grid.rgb[i * 3], 3) != 0;
        }
        if (wrong) std::fprintf(stderr, "behind a video: %zu wrong pixels (%zu on the picture, %zu off it)\n", wrong, inside, outside);
        CHECK(inside > 100000 && outside > 100000 && wrong == 0);
    }

    // Previews: the background with the monitors in front.
    if (argc > 1) {
#ifdef XR_HAVE_ZLIB
        const std::filesystem::path dir = argv[1];
        std::error_code ec; std::filesystem::create_directories(dir, ec);
        struct Shot { const char* file; Frame f; std::array<std::uint8_t, 3> accent = kTokyo; std::string name{}; };
        std::vector<Shot> shots;
        const Frame ahead = [&] { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6); return f; }();
        shots.push_back({"overview-ahead.png", ahead});
        { Frame f = base; f.world = world(55, -10); f.view = anchor_view(55, -10); shots.push_back({"overview-look-left.png", f}); }
        { Frame f = base; f.world = world(-20, -42); f.view = anchor_view(-20, -42); f.seconds = 7.5f; shots.push_back({"overview-look-down.png", f}); }
        { Frame f = base; f.world = world(180, -8); f.view = anchor_view(180, -8); shots.push_back({"look-behind.png", f}); }
        { Frame f = base; f.zoom = 1; f.world = world(0, -9); shots.push_back({"maximized-follow.png", f}); }
        { Frame f = base; f.zoom = 1; f.world = world(-45, -12); shots.push_back({"maximized-locked-head-turned-right.png", f}); }
        shots.push_back({"overview-theme-rose.png", ahead, {0xf7, 0x76, 0x8e}});
        shots.push_back({"overview-theme-green.png", ahead, {0x9e, 0xce, 0x6a}});
        shots.push_back({"overview-theme-amber.png", ahead, {0xe0, 0xaf, 0x68}});
        { Frame f = ahead; f.level = 0.5f; shots.push_back({"overview-fading-half.png", f}); }
        { Frame f = ahead; f.brightness = 40; shots.push_back({"overview-brightness-40.png", f}); }
        { Frame f = ahead; f.brightness = 200; shots.push_back({"overview-brightness-200.png", f}); }
        { Frame f = ahead; f.environment = false; shots.push_back({"overview-none.png", f}); }
        // The placement panel: Grid chosen with the Background list open, and the Brightness row selected at 40%.
        PlacementMenuState menu; menu.open = true; menu.desktop = 1; menu.background = std::uint8_t(Background::grid);
        menu.placements[0] = menu.placements[1] = menu.placements[2] = {0, 0, 100, 0};
        menu.scale = 125; menu.scales = 0x7f;
        PlacementMenuState list = menu; list.background_picker = true;
        PlacementMenuState slider = menu; slider.field = kPlacementMenuBrightness; slider.brightness = 40;
        const std::array<float, 3> tokyo{kTokyo[0] / 255.f, kTokyo[1] / 255.f, kTokyo[2] / 255.f};
        const HudImage list_image = render_glass_panel(list, font.get(), tokyo), slider_image = render_glass_panel(slider, font.get(), tokyo);
        // Head level, the panel open: the view shifted as the renderer shifts it (glass.hpp glass_panel_shift).
        const std::vector<Size> sizes(3, Size{float(kDesktopW), float(kDesktopH)});
        const auto tiles = blended_layout(sizes, float(kCanvasW), float(kCanvasH), 40.f, 0.f, 1.f);
        const Canvas canvas{float(kCanvasW), float(kCanvasH), g_half_w, g_half_h};
        const Frame low = [&] { Frame f = base; f.view = glass_view_shift(kIdentity, glass_panel_shift(canvas, tiles[1], Placement{})); return f; }();
        { Frame f = low; f.panel = &list_image; shots.push_back({"panel-background-dropdown.png", f}); }
        { Frame f = low; f.panel = &slider_image; f.brightness = 40; shots.push_back({"panel-brightness-slider.png", f}); }
        // Smoke: over time, looking round, themes, brightness, maximized and the panel.
        Frame sm = ahead; sm.scene = Background::smoke;
        for (const int s : {20, 32, 44, 56}) { Frame f = sm; f.seconds = float(s); shots.push_back({nullptr, f}); shots.back().name = "smoke-over-time-" + std::to_string(s) + "s.png"; }
        { Frame f = sm; f.world = world(55, -10); f.view = anchor_view(55, -10); shots.push_back({"smoke-look-left.png", f}); }
        { Frame f = sm; f.world = world(180, -8); f.view = anchor_view(180, -8); shots.push_back({"smoke-look-behind.png", f}); }
        { Frame f = sm; f.world = world(-20, -42); f.view = anchor_view(-20, -42); shots.push_back({"smoke-look-down.png", f}); }
        { Frame f = sm; f.world = world(20, 30); f.view = anchor_view(20, 30); shots.push_back({"smoke-look-up.png", f}); }
        { Frame f = sm; f.zoom = 1; f.world = world(0, -9); shots.push_back({"smoke-maximized.png", f}); }
        shots.push_back({"smoke-theme-rose.png", sm, {0xf7, 0x76, 0x8e}});
        shots.push_back({"smoke-theme-green.png", sm, {0x9e, 0xce, 0x6a}});
        shots.push_back({"smoke-theme-amber.png", sm, {0xe0, 0xaf, 0x68}});
        { Frame f = sm; f.brightness = 40; shots.push_back({"smoke-brightness-40.png", f}); }
        { Frame f = sm; f.brightness = 200; shots.push_back({"smoke-brightness-200.png", f}); }
        PlacementMenuState smoke_list = list; smoke_list.background = std::uint8_t(Background::smoke);
        const HudImage smoke_list_image = render_glass_panel(smoke_list, font.get(), tokyo);
        { Frame f = low; f.scene = Background::smoke; f.panel = &smoke_list_image; shots.push_back({"smoke-panel-dropdown.png", f}); }
        for (const auto& s : shots) {
            gl->set_accent(s.accent[0], s.accent[1], s.accent[2]);
            const std::filesystem::path path = dir / (s.file ? std::string(s.file) : s.name);
            const bool ok = write_png(path, draw(*gl, s.f));
            CHECK(ok);
            std::printf("preview %s %s\n", path.c_str(), ok ? "written" : "FAILED");
        }
        gl->set_accent(kTokyo[0], kTokyo[1], kTokyo[2]);
        if (sphere) {
            Frame v = grid_only; v.world = world(84, -8);
            const bool ok = write_png(dir / "video-180-grid-behind.png", draw_video(*gl, *sphere, picture, v));
            CHECK(ok);
            std::printf("preview %s %s\n", (dir / "video-180-grid-behind.png").c_str(), ok ? "written" : "FAILED");
            v.scene = Background::smoke;
            const bool ok2 = write_png(dir / "smoke-video-180-behind.png", draw_video(*gl, *sphere, picture, v));
            CHECK(ok2);
            std::printf("preview %s %s\n", (dir / "smoke-video-180-behind.png").c_str(), ok2 ? "written" : "FAILED");
        }
#else
        std::printf("note: built without zlib; no previews written\n");
#endif
    }
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::puts("environment render test passed");
    return 0;
}
