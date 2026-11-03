// The TRON grid drawn by the real compositor (GlCompositor::draw_and_swap on surfaceless EGL, the glasses' 1920x1200
// buffer under scale 1.25) behind three synthetic desktops, the glass pills, the cursor dot and the highlight:
//   * off (level 0) the frame is bit for bit the frame of a compositor that never heard of it;
//   * on, every desktop's pixels are exactly as they are with it off (the desktops stay fully readable), and the
//     highlight's glow adds to the grid instead of blacking out a halo round the selected desktop;
//   * the grid alone, looking every way round: nothing passes the 191/255 OLED cap, most of the view stays black
//     (see-through), something is drawn (the horizon ahead, the floor below), and the sky straight up is black;
//   * world-locked: the horizon is where the head pitch puts it, a whole turn draws the same frame, and in locked mode
//     (desktops head-locked) the grid still turns with the head while the desktops do not;
//   * the fade scales it, the theme colour changes its hue, the pulses move and loop seamlessly (kEnvironmentLoopS).
// With a directory argument it also writes preview PNGs there (needs zlib): the overview ahead and looking round,
// maximized (follow / locked), the theme colour, half faded, and off. Exit 77 (skipped) without EGL/GLES.
#include "gl.hpp"
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
    const std::vector<std::string> page{"Toggle a TRON style grid behind your desktops.", "Ctrl+Alt+E     grid on / off",
                                        "Ctrl+Alt+Shift+E     cyan / theme colour", "Look round: it stays put in the room."};
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

struct Frame {
    bool desktops = true, pills = false, pointer = false;
    float zoom = 0;                 // 0 overview, 1 maximized (desktop 1)
    int highlight = 1;
    Mat3 view = kIdentity;          // the desktops' rotation (anchor: the head; locked / follow: identity here)
    bool environment = true; bool touch = true;  // touch false: never call set_environment (a compositor without it)
    float level = 1; MdRgb line = kEnvironmentCyan; float seconds = 3.25f; Mat3 world = kIdentity;
};
Image draw(GlCompositor& gl, const Frame& f) {
    const std::vector<Size> sizes(3, Size{float(kDesktopW), float(kDesktopH)});
    const auto tiles = f.desktops ? blended_layout(sizes, float(kCanvasW), float(kCanvasH), 40.f, f.zoom, 1.f) : std::vector<FRect>{};
    GlassFrame glass; glass.pills = f.pills && f.zoom < 0.01f;
    gl.set_glass(glass);
    gl.set_free_pointer(f.pointer && f.zoom < 0.01f, float(kCanvasW) * 0.58f, float(kCanvasH) * 0.47f, kFreePointerLevel);
    if (f.touch) gl.set_environment(f.environment ? f.level : 0.f, f.line, f.seconds, f.world);
    CHECK(gl.draw_and_swap(tiles, std::vector<Placement>(tiles.size()), f.desktops ? f.highlight : -1, 1.f - f.zoom, f.view, g_half_w, g_half_h));
    Image im{kPxW, kPxH, std::vector<std::uint8_t>(std::size_t(kPxW) * kPxH * 3)};
    std::vector<std::uint8_t> rgba(std::size_t(kPxW) * kPxH * 4);
    glFinish(); glReadPixels(0, 0, GLsizei(kPxW), GLsizei(kPxH), GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    for (std::uint32_t y = 0; y < kPxH; ++y)  // GL rows are bottom-up
        for (std::uint32_t x = 0; x < kPxW; ++x)
            std::memcpy(&im.rgb[(std::size_t(y) * kPxW + x) * 3], &rgba[(std::size_t(kPxH - 1 - y) * kPxW + x) * 4], 3);
    CHECK(glGetError() == GL_NO_ERROR);
    return im;
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
    CHECK(gl->set_environment(0.f, kEnvironmentCyan, 0.f, kIdentity));  // the pass compiled
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

    // 5. The fade scales it; the theme colour changes its hue; the pulses move and loop seamlessly.
    {
        Frame full = grid_only; full.world = world(0, -30);
        Frame half = full; half.level = 0.5f;
        const Image a = draw(*gl, full), b = draw(*gl, half);
        double sa = 0, sb = 0;
        for (std::size_t k = 0; k < a.rgb.size(); ++k) { sa += a.rgb[k]; sb += b.rgb[k]; }
        CHECK(sa > 0 && std::fabs(sb / sa - 0.5) < 0.03);
        Frame red = full; red.line = environment_line_color(EnvironmentColor::theme, {0xf7 / 255.f, 0x76 / 255.f, 0x8e / 255.f});
        const Image c = draw(*gl, red);
        double r = 0, bl = 0, ar = 0, ab = 0;
        for (std::size_t i = 0; i < c.rgb.size() / 3; ++i) { r += c.rgb[i * 3]; bl += c.rgb[i * 3 + 2]; ar += a.rgb[i * 3]; ab += a.rgb[i * 3 + 2]; }
        CHECK(r > bl && ab > 4 * ar);  // the red theme is red, cyan has (almost) no red
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

    // Previews: the environment with the monitors in front.
    if (argc > 1) {
#ifdef XR_HAVE_ZLIB
        const std::filesystem::path dir = argv[1];
        std::error_code ec; std::filesystem::create_directories(dir, ec);
        struct Shot { const char* file; Frame f; };
        std::vector<Shot> shots;
        { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6); shots.push_back({"overview-ahead.png", f}); }
        { Frame f = base; f.world = world(55, -10); f.view = anchor_view(55, -10); shots.push_back({"overview-look-left.png", f}); }
        { Frame f = base; f.world = world(-20, -42); f.view = anchor_view(-20, -42); f.seconds = 7.5f; shots.push_back({"overview-look-down.png", f}); }
        { Frame f = base; f.world = world(180, -8); f.view = anchor_view(180, -8); shots.push_back({"look-behind.png", f}); }
        { Frame f = base; f.zoom = 1; f.world = world(0, -9); shots.push_back({"maximized-follow.png", f}); }
        { Frame f = base; f.zoom = 1; f.world = world(-45, -12); shots.push_back({"maximized-locked-head-turned-right.png", f}); }
        { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6);
          f.line = environment_line_color(EnvironmentColor::theme, {kTokyo[0] / 255.f, kTokyo[1] / 255.f, kTokyo[2] / 255.f});
          shots.push_back({"overview-theme-tokyo-night.png", f}); }
        { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6);
          f.line = environment_line_color(EnvironmentColor::theme, {0xf7 / 255.f, 0x76 / 255.f, 0x8e / 255.f});
          shots.push_back({"overview-theme-rose.png", f}); }
        { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6); f.level = 0.5f; shots.push_back({"overview-fading-half.png", f}); }
        { Frame f = base; f.world = world(0, -6); f.view = anchor_view(0, -6); f.environment = false; shots.push_back({"overview-off.png", f}); }
        for (const auto& s : shots) {
            const bool ok = write_png(dir / s.file, draw(*gl, s.f));
            CHECK(ok);
            std::printf("preview %s %s\n", (dir / s.file).c_str(), ok ? "written" : "FAILED");
        }
#else
        std::printf("note: built without zlib; no previews written\n");
#endif
    }
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::puts("environment render test passed");
    return 0;
}
