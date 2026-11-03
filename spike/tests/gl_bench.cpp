// GlCompositor frame harness on surfaceless EGL (no display, no window, no compositor): the real draw_and_swap /
// draw_scene_and_swap path into an offscreen framebuffer (GlCompositor::create_offscreen), with the GL calls of
// gl.cpp and hud_gl.cpp counted (gl_call_counts.hpp).
//   gl_bench            correctness (ctest): every scenario frame drawn by one long-lived compositor, in a mixed order,
//                       is pixel-identical to the same frame drawn by a fresh compositor (no stale caches, uploads or
//                       GL state carried between frames). Exit 77 (skipped) without EGL/GLES.
//   gl_bench --hashes   FNV-1a of every scenario frame (RGB) and of the CPU-rendered cards: compare before/after a change.
//   gl_bench --bench [--frames N] [--size WxH]
//                       per scenario: CPU submit time of the draw call, GPU time (EXT_disjoint_timer_query), frame time
//                       to glFinish, frames over the 120 Hz budget, GL calls per frame; then card/font CPU timings.
// The desktops are synthetic 1920x1080 patterns; nothing is captured.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>
#include "gl.hpp"
#define XR_GL_COUNT_NO_MACROS
#include "gl_call_counts.hpp"
#include "xr/glass.hpp"
#include "xr/hud.hpp"
#include "xr/view.hpp"

GlCallCounts g_gl_calls;

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

constexpr std::uint32_t kDesktopW = 1920, kDesktopH = 1080;
constexpr std::array<std::uint8_t, 3> kAccent8{0x7a, 0xa2, 0xf7};
const std::array<float, 3> kAccent{0x7a / 255.f, 0xa2 / 255.f, 0xf7 / 255.f};

double now_us() { return double(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()) / 1e3; }
double thread_us() { timespec t{}; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return double(t.tv_sec) * 1e6 + double(t.tv_nsec) / 1e3; }

std::uint64_t fnv(const std::uint8_t* p, std::size_t n, std::uint64_t h = 1469598103934665603ull) {
    for (std::size_t i = 0; i < n; ++i) { h ^= p[i]; h *= 1099511628211ull; }
    return h;
}

// A busy, non-repeating desktop (gradients, a grid and text-like noise) so that sampling differences show.
std::vector<std::uint8_t> desktop_pixels(int index, int variant) {
    std::vector<std::uint8_t> px(std::size_t(kDesktopW) * kDesktopH * 4);
    std::uint32_t seed = 0x9e3779b9u * std::uint32_t(index + 1) + 0x85ebca6bu * std::uint32_t(variant + 1);
    for (std::uint32_t y = 0; y < kDesktopH; ++y)
        for (std::uint32_t x = 0; x < kDesktopW; ++x) {
            seed = seed * 1664525u + 1013904223u;
            auto* p = &px[(std::size_t(y) * kDesktopW + x) * 4];
            const bool grid = (x % 64) < 2 || (y % 48) < 2;
            p[0] = std::uint8_t(grid ? 220 : (x * 255 / kDesktopW + std::uint32_t(variant) * 40) & 0xff);   // B
            p[1] = std::uint8_t(grid ? 220 : (y * 255 / kDesktopH + std::uint32_t(index) * 70) & 0xff);     // G
            p[2] = std::uint8_t(((seed >> 24) & 0x3f) + (index * 60));                                     // R
            p[3] = 0xff;
        }
    return px;
}

struct Resources {
    std::unique_ptr<HudFont> font;
    std::array<std::array<std::vector<std::uint8_t>, 2>, 3> desktops;  // [desktop][variant]
    HudImage popup, help, card, panel_static;
    std::vector<HudImage> panel_drag;  // one per frame of the drag (rendered on demand, kept)
};

PlacementMenuState panel_state(std::int32_t x) {
    PlacementMenuState st;
    st.open = true; st.desktop = 1; st.field = 0;
    st.placements[1] = {x, 0, 100, 0};
    return st;
}
std::int32_t drag_x(int frame) { return -40 + (frame * 7) % 81; }  // the x slider (tenths of a degree) swept back and forth

enum class Kind { desktops, video };
struct Scenario {
    const char* name;
    Kind kind = Kind::desktops;
    float zoom = 0, position = 1;
    std::array<Placement, 3> placements{};
    bool head_motion = false;
    bool pills = false; int hover = -1;
    float expand = 0; bool panel = false, panel_drag = false;
    bool help = false, popup = false, handle = false, pointer = false;
    bool upload_each_frame = false;
};

std::vector<Scenario> scenarios() {
    Placement curved{}; curved.curvature = 1.0f;
    Placement tilted_left{}; tilted_left.x = -0.05f; tilted_left.curvature = 0.7f; tilted_left.distance = 1.2f;   // orbited, billboarded
    Placement tilted_right{}; tilted_right.x = 0.05f; tilted_right.y = 0.08f; tilted_right.curvature = 0.7f;
    std::vector<Scenario> out;
    { Scenario s{"zoomed"}; s.zoom = 1; out.push_back(s); }
    { Scenario s{"zoomed_curved"}; s.zoom = 1; s.placements[1] = curved; out.push_back(s); }
    { Scenario s{"zoomed_popup"}; s.zoom = 1; s.popup = true; out.push_back(s); }
    { Scenario s{"zoomed_shm_upload"}; s.zoom = 1; s.upload_each_frame = true; out.push_back(s); }
    { Scenario s{"overview"}; out.push_back(s); }
    { Scenario s{"overview_pills"}; s.pills = true; s.hover = 1; s.pointer = true; s.handle = true; out.push_back(s); }
    { Scenario s{"overview_curved_pills"}; s.pills = true; s.pointer = true; s.placements = {tilted_left, curved, tilted_right}; out.push_back(s); }
    { Scenario s{"overview_head_motion"}; s.pills = true; s.pointer = true; s.head_motion = true; out.push_back(s); }
    { Scenario s{"panel_morph"}; s.pills = true; s.expand = 0.6f; s.pointer = true; out.push_back(s); }
    { Scenario s{"panel_open"}; s.pills = true; s.expand = 1; s.panel = true; s.pointer = true; out.push_back(s); }
    { Scenario s{"panel_slider_drag"}; s.pills = true; s.expand = 1; s.panel = true; s.panel_drag = true; s.pointer = true; out.push_back(s); }
    { Scenario s{"panel_curved_drag"}; s.pills = true; s.expand = 1; s.panel = true; s.panel_drag = true; s.pointer = true;
      s.placements = {tilted_left, curved, tilted_right}; out.push_back(s); }
    { Scenario s{"help_card"}; s.pills = true; s.help = true; s.pointer = true; out.push_back(s); }
    { Scenario s{"video_card"}; s.kind = Kind::video; s.popup = true; out.push_back(s); }
    return out;
}

Mat3 head(int frame, bool motion) {
    if (!motion) return {1, 0, 0, 0, 1, 0, 0, 0, 1};
    const float yaw = 0.06f * std::sin(float(frame) * 0.05f), pitch = 0.03f * std::cos(float(frame) * 0.04f);
    const float cy = std::cos(yaw), sy = std::sin(yaw), cp = std::cos(pitch), sp = std::sin(pitch);
    return {cy, sy * sp, sy * cp, 0, cp, -sp, -sy, cy * sp, cy * cp};  // Ry * Rx, row-major
}

const HudImage& panel_image(Resources& r, const Scenario& s, int frame, std::uint64_t& generation) {
    if (!s.panel_drag) { generation = 1; return r.panel_static; }
    while (int(r.panel_drag.size()) <= frame) r.panel_drag.push_back(render_glass_panel(panel_state(drag_x(int(r.panel_drag.size()))), r.font.get()));
    generation = 100 + std::uint64_t(frame);
    return r.panel_drag[std::size_t(frame)];
}

// The desktop content a scenario frame shows: upload scenarios alternate two captures, the rest show the first.
int variant(const Scenario& s, int frame) { return s.upload_each_frame ? frame & 1 : 0; }
// Everything wayland.cpp hands the compositor for one frame, then the draw. `upload` re-uploads the desktops
// (variant(s, frame)) first. Returns the CPU time of the draw call (and of the uploads in `upload_us`).
double draw_frame(GlCompositor& gl, Resources& r, const Scenario& s, int frame, std::uint32_t w, std::uint32_t h, bool upload,
                  double* upload_us = nullptr) {
    if (upload) {
        const double t = now_us();
        const auto v = std::size_t(variant(s, frame));
        for (int i = 0; i < 3; ++i) CHECK(gl.upload(std::size_t(i), r.desktops[std::size_t(i)][v].data(), kDesktopW, kDesktopH, kDesktopW * 4));
        if (upload_us) *upload_us = now_us() - t;
    }
    const double d = std::tan(46.0 * M_PI / 360.0), a = double(w) / h;
    const float half_w = float(d * a / std::sqrt(1 + a * a)), half_h = float(d / std::sqrt(1 + a * a));
    gl.set_popup(s.popup ? &r.popup : nullptr, 1, 1.f);
    if (s.kind == Kind::video) {
        const double t = now_us();
        CHECK(gl.draw_scene_and_swap([](std::uint32_t vw, std::uint32_t vh) {
            glViewport(0, 0, GLsizei(vw), GLsizei(vh)); glClearColor(26 / 255.f, 31 / 255.f, 38 / 255.f, 1); glClear(GL_COLOR_BUFFER_BIT);  // exact k/255: no rounding
        }, &r.card, 1, 1.f));
        return now_us() - t;
    }
    const std::vector<Size> sizes(3, Size{float(kDesktopW), float(kDesktopH)});
    const auto tiles = blended_layout(sizes, float(w), float(h), 40.f, s.zoom, s.position);
    const std::vector<Placement> placements(s.placements.begin(), s.placements.end());
    gl.set_help(s.help ? &r.help : nullptr, 1, 1.f, 0.f);
    gl.set_corner_handle(s.handle ? 1 : -1, 3, kHandleLevelIdle);
    GlassFrame glass;
    glass.pills = s.pills;
    if (s.hover >= 0) { glass.hover = s.hover; glass.hover_level = kGlassHover; }
    glass.expand[1] = s.expand;
    if (s.expand > 0) glass.top = 1;
    if (s.panel) { glass.content = 1; glass.image = &panel_image(r, s, frame, glass.generation); glass.content_alpha = 1.f; }
    gl.set_glass(glass);
    gl.set_free_pointer(s.pointer, float(w) * 0.55f, float(h) * 0.42f, kFreePointerLevel);
    const double t = now_us();
    CHECK(gl.draw_and_swap(tiles, placements, int(s.position), 1.f - s.zoom, head(frame, s.head_motion), half_w, half_h));
    return now_us() - t;
}

std::unique_ptr<GlCompositor> make(std::uint32_t w, std::uint32_t h, Resources& r, std::string& why) {
    auto gl = GlCompositor::create_offscreen(w, h, 3, why);
    if (!gl) return nullptr;
    gl->set_accent(kAccent8[0], kAccent8[1], kAccent8[2]);
    for (int i = 0; i < 3; ++i) gl->upload(std::size_t(i), r.desktops[std::size_t(i)][0].data(), kDesktopW, kDesktopH, kDesktopW * 4);
    return gl;
}

std::vector<std::uint8_t> read_rgb(std::uint32_t w, std::uint32_t h) {
    std::vector<std::uint8_t> rgba(std::size_t(w) * h * 4), rgb(std::size_t(w) * h * 3);
    glFinish(); glReadPixels(0, 0, GLsizei(w), GLsizei(h), GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    for (std::size_t i = 0, n = std::size_t(w) * h; i < n; ++i) std::memcpy(&rgb[i * 3], &rgba[i * 4], 3);
    return rgb;
}

Resources load_resources(std::uint32_t w, std::uint32_t h) {
    Resources r;
    std::string why;
    r.font = HudFont::load(kHudFontFamily, why);
    if (!r.font) std::fprintf(stderr, "note: %s; cards use the pixel font\n", why.c_str());
    for (int i = 0; i < 3; ++i) for (int v = 0; v < 2; ++v) r.desktops[std::size_t(i)][std::size_t(v)] = desktop_pixels(i, v);
    r.popup = render_hud_card(view_mode_lines(0), kAccent, r.font.get());
    r.card = render_hud_card(prediction_lines(true), kAccent, r.font.get());
    const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement pill of the selected desktop"},
                                    {"Ctrl+Alt+Z", "Zoom in / out"}, {"Ctrl+Alt+Left/Right", "Select a desktop"},
                                    {"Ctrl+Alt+R", "Recentre"}, {"Ctrl+Alt+M", "Viewing mode", true}, {"Ctrl+Alt+T", "Typing mode"}};
    r.help = render_help_card(rows, kAccent, r.font.get(), w, h);
    r.panel_static = render_glass_panel(panel_state(15), r.font.get());
    return r;
}

struct Pct { double mean = 0, p50 = 0, p99 = 0, max = 0; };
Pct pct(std::vector<double> v) {
    Pct p; if (v.empty()) return p;
    std::sort(v.begin(), v.end());
    double sum = 0; for (double x : v) sum += x;
    p.mean = sum / double(v.size()); p.p50 = v[v.size() / 2]; p.p99 = v[std::min(v.size() - 1, std::size_t(double(v.size()) * 0.99))]; p.max = v.back();
    return p;
}

int check(std::uint32_t w, std::uint32_t h) {
    Resources r = load_resources(w, h);
    const auto list = scenarios();
    constexpr int kFrames = 3;
    std::vector<std::vector<std::vector<std::uint8_t>>> ref(list.size());
    std::string why;
    for (std::size_t i = 0; i < list.size(); ++i)
        for (int f = 0; f < kFrames; ++f) {
            auto gl = make(w, h, r, why);
            if (!gl) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
            draw_frame(*gl, r, list[i], f, w, h, list[i].upload_each_frame);
            ref[i].push_back(read_rgb(w, h));
            CHECK(glGetError() == GL_NO_ERROR);
        }
    // One long-lived compositor: forward, then backward, then interleaved, so every scenario follows several others.
    std::vector<std::pair<std::size_t, int>> order;
    for (std::size_t i = 0; i < list.size(); ++i) for (int f = 0; f < kFrames; ++f) order.push_back({i, f});
    for (std::size_t i = list.size(); i-- > 0;) for (int f = 0; f < kFrames; ++f) order.push_back({i, f});
    for (int f = 0; f < kFrames; ++f) for (std::size_t i = 0; i < list.size(); i += 2) order.push_back({i, f});
    for (int f = 0; f < kFrames; ++f) for (std::size_t i = 1; i < list.size(); i += 2) order.push_back({i, f});
    auto gl = make(w, h, r, why);
    CHECK(gl != nullptr);
    if (!gl) return 1;
    int shown = 0;  // the desktop variant the long-lived compositor holds
    for (const auto& [i, f] : order) {
        // The reference drew its frame's variant; upload scenarios upload every frame (same size, new content), the
        // rest only when the variant held differs, so the inputs match.
        const bool upload = list[i].upload_each_frame || variant(list[i], f) != shown;
        draw_frame(*gl, r, list[i], f, w, h, upload);
        shown = variant(list[i], f);
        const auto px = read_rgb(w, h);
        if (px != ref[i][std::size_t(f)]) {
            std::size_t n = 0; for (std::size_t k = 0; k < px.size(); ++k) n += px[k] != ref[i][std::size_t(f)][k];
            std::fprintf(stderr, "%s frame %d: %zu bytes differ from a fresh compositor\n", list[i].name, f, n);
            if (std::getenv("GL_BENCH_DUMP")) {
                std::size_t first = 0; while (px[first] == ref[i][std::size_t(f)][first]) ++first;
                std::size_t last = px.size() - 1; while (px[last] == ref[i][std::size_t(f)][last]) --last;
                std::fprintf(stderr, "  first diff px (%zu,%zu) got %d,%d,%d want %d,%d,%d; last px (%zu,%zu)\n", (first / 3) % w, (first / 3) / w,
                             px[first / 3 * 3], px[first / 3 * 3 + 1], px[first / 3 * 3 + 2], ref[i][std::size_t(f)][first / 3 * 3],
                             ref[i][std::size_t(f)][first / 3 * 3 + 1], ref[i][std::size_t(f)][first / 3 * 3 + 2], (last / 3) % w, (last / 3) / w);
            }
            ++failures;
        }
    }
    // Resize: drawing at a new size then back matches fresh compositors at each size.
    gl->resize(w / 2, h / 2);
    draw_frame(*gl, r, list[5], 0, w / 2, h / 2, false);
    const auto small = read_rgb(w / 2, h / 2);
    gl->resize(w, h);
    draw_frame(*gl, r, list[5], 0, w, h, false);
    CHECK(read_rgb(w, h) == ref[5][0]);
    gl.reset();
    {
        auto fresh = make(w / 2, h / 2, r, why);
        draw_frame(*fresh, r, list[5], 0, w / 2, h / 2, false);
        CHECK(read_rgb(w / 2, h / 2) == small);
    }
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::printf("gl frame check passed: %zu scenarios, %zu frames vs fresh compositors\n", list.size(), order.size() + 2);
    return 0;
}

// Every plot call (x, y, coverage bits), measure and metrics result of a text corpus (ASCII, arrows, degrees, Nerd Font
// icons, a missing glyph, invalid / truncated / overlong UTF-8) at many sizes, regular and bold, three times: cold, after
// a flood of distinct glyphs past the cache bound, and warm.
std::uint64_t font_fingerprint(const HudFont& font) {
    const std::vector<std::string> corpus{"XR-2", "Placement", "Distance  100%", "Curvature  +12\xc2\xb0", "\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust",
                                          "\xef\x80\x81 \xee\x9c\x91 nerd", "\xe4\xb8\xad\xe6\x96\x87", "bad \xff\xfe end", "trunc \xe2\x86", "over \xc0\xaf \xf7\xbf\xbf\xbf",
                                          "\xed\xa0\x80 surrogate", "", " ", "Ctrl+Alt+PgUp / PgDn or click an arrow for more"};
    const std::uint32_t sizes[] = {6, 12, 18, 24, 30, 48, 160};
    std::uint64_t h = 1469598103934665603ull;
    auto mix = [&](std::uint64_t v) { h = fnv(reinterpret_cast<const std::uint8_t*>(&v), sizeof v, h); };
    auto pass = [&] {
        for (const auto& text : corpus)
            for (const auto px : sizes)
                for (const bool bold : {false, true}) {
                    const auto m = font.metrics(px, bold);
                    mix(m.ascent); mix(m.descent); mix(font.measure(text, px, bold));
                    font.draw(text, px, bold, 3, 40, [&](int x, int y, float c) { std::uint32_t bits; std::memcpy(&bits, &c, 4); mix(std::uint64_t(std::uint32_t(x)) << 32 | std::uint32_t(y)); mix(bits); });
                }
    };
    pass();
    for (std::uint32_t px = 8; px < 60; ++px) {  // ~95 glyphs x 52 sizes x 2: past kMaxCached (4096) for draw and measure
        std::string all; for (char c = 0x21; c < 0x7f; ++c) all += c;
        mix(font.measure(all, px, px & 1));
        font.draw(all, px, px & 1, 0, 0, [&](int x, int y, float c) { std::uint32_t bits; std::memcpy(&bits, &c, 4); mix(std::uint64_t(std::uint32_t(x)) << 32 | std::uint32_t(y)); mix(bits); });
    }
    pass();
    pass();
    return h;
}

int hashes(std::uint32_t w, std::uint32_t h) {
    Resources r = load_resources(w, h);
    if (r.font) std::printf("font fingerprint %016llx\n", (unsigned long long)font_fingerprint(*r.font));
    auto image_hash = [](const HudImage& im) { return fnv(im.rgba.data(), im.rgba.size(), fnv(reinterpret_cast<const std::uint8_t*>(&im.width), 8)); };
    std::printf("card popup %016llx\ncard video %016llx\ncard help %016llx\ncard panel %016llx\n", (unsigned long long)image_hash(r.popup),
                (unsigned long long)image_hash(r.card), (unsigned long long)image_hash(r.help), (unsigned long long)image_hash(r.panel_static));
    std::uint64_t drag = 1469598103934665603ull;
    const auto list = scenarios();
    for (int f = 0; f < 81; ++f) { std::uint64_t g; const auto& im = panel_image(r, list[10], f, g); drag = fnv(im.rgba.data(), im.rgba.size(), drag); }
    std::printf("card panel_drag81 %016llx\n", (unsigned long long)drag);
    std::string why;
    auto gl = make(w, h, r, why);
    if (!gl) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
    for (const auto& s : list) {
        std::uint64_t hash = 1469598103934665603ull;
        for (int f = 0; f < 6; ++f) {
            draw_frame(*gl, r, s, f, w, h, s.upload_each_frame || f == 0);  // f == 0: variant 0 again after an upload scenario
            const auto px = read_rgb(w, h);
            hash = fnv(px.data(), px.size(), hash);
        }
        std::printf("frame %-22s %016llx\n", s.name, (unsigned long long)hash);
    }
    return 0;
}

int bench(std::uint32_t w, std::uint32_t h, int frames) {
    Resources r = load_resources(w, h);
    std::string why;
    {
        // First frames on a fresh compositor, scenarios in order: one-time costs (lazy creation, first use of a
        // program or texture, shader variants) land on the first scenario that needs them. CPU call + glFinish.
        const double t0 = now_us();
        auto fresh = make(w, h, r, why);
        if (!fresh) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
        glFinish();
        std::printf("first frames (fresh compositor; create + 3 uploads %.0f us):", now_us() - t0);
        double worst = 0;
        for (const auto& s : scenarios()) {
            const double t = now_us();
            draw_frame(*fresh, r, s, 0, w, h, s.upload_each_frame);
            glFinish();
            const double us = now_us() - t; worst = std::max(worst, us);
            std::printf(" %s %.0f", s.name, us);
        }
        std::printf(" | worst %.0f us\n", worst);
    }
    auto gl = make(w, h, r, why);
    if (!gl) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
    const char* ext = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
    auto gen = reinterpret_cast<PFNGLGENQUERIESEXTPROC>(eglGetProcAddress("glGenQueriesEXT"));
    auto begin = reinterpret_cast<PFNGLBEGINQUERYEXTPROC>(eglGetProcAddress("glBeginQueryEXT"));
    auto end = reinterpret_cast<PFNGLENDQUERYEXTPROC>(eglGetProcAddress("glEndQueryEXT"));
    auto result = reinterpret_cast<PFNGLGETQUERYOBJECTUI64VEXTPROC>(eglGetProcAddress("glGetQueryObjectui64vEXT"));
    const bool timer = ext && std::strstr(ext, "GL_EXT_disjoint_timer_query") && gen && begin && end && result;
    GLuint query = 0; if (timer) gen(1, &query);
    std::printf("renderer: %s | %ux%u, %d frames per scenario, GPU timer %s\n", reinterpret_cast<const char*>(glGetString(GL_RENDERER)), w, h, frames,
                timer ? "on" : "off (frame time only)");
    // cpu: wall time of the draw call (draw_and_swap / draw_scene_and_swap, uploads excluded); gl = cpu minus the time
    // inside its final glFlush (command submission); thr = thread CPU time of the call; gpu = GPU time of the frame.
    std::printf("%-22s %7s %7s %7s %7s %7s %7s | %7s %7s | %7s %7s %4s | %5s %5s %5s %5s %5s %5s %5s %5s %7s\n", "scenario", "cpu_avg", "cpu_p50",
                "cpu_p99", "gl_p50", "thr_p50", "fls_p50", "gpu_avg", "gpu_p99", "frm_avg", "frm_p99", ">8.3", "calls", "draws", "progs", "unifs",
                "texs", "attrs", "bufs", "tgls", "upKiB");
    for (const auto& s : scenarios()) {
        std::vector<double> cpu, gl_only, thr, flush, gpu, frame, up;
        GlCallCounts per{};
        for (int f = -30; f < frames; ++f) {
            const int fi = f < 0 ? f + 30 : f;
            double upload_us = 0;
            const double t0 = now_us();
            g_gl_calls = {};
            if (timer) begin(GL_TIME_ELAPSED_EXT, query);
            if (s.upload_each_frame) draw_frame(*gl, r, s, fi, w, h, true, &upload_us);  // the uploads, timed apart ...
            const GlCallCounts uploads = g_gl_calls;
            const double c0 = thread_us();
            const double c = s.upload_each_frame ? 0 : draw_frame(*gl, r, s, s.panel_drag ? fi % 81 : fi, w, h, false);
            const double thread = thread_us() - c0;
            if (timer) end(GL_TIME_ELAPSED_EXT);
            GlCallCounts counts = g_gl_calls;
            glFinish();
            const double t1 = now_us();
            GLuint64 ns = 0; if (timer) result(query, GL_QUERY_RESULT_EXT, &ns);
            if (f < 0) continue;
            if (s.upload_each_frame) { up.push_back(upload_us); counts = uploads; continue; }  // ... (a frame per upload set)
            const double fl = double(counts.flush_ns) / 1e3;
            cpu.push_back(c); gl_only.push_back(c - fl); thr.push_back(thread); flush.push_back(fl);
            frame.push_back(t1 - t0); gpu.push_back(double(ns) / 1e3);
            per = counts;  // steady state: the last frame's counts (every frame of a scenario issues the same calls)
        }
        if (s.upload_each_frame) {  // upload throughput: 3 x 1920x1080 per frame, then the zoomed frame drawn as usual
            const Pct pu = pct(up);
            std::printf("%-22s 3 SHM uploads/frame: mean %.1f us, p50 %.1f, p99 %.1f, max %.1f (%llu KiB)\n", s.name, pu.mean, pu.p50, pu.p99, pu.max,
                        (unsigned long long)(per.upload_bytes >> 10));
            continue;
        }
        const Pct pc = pct(cpu), pl = pct(gl_only), pt = pct(thr), ps = pct(flush), pg = pct(gpu), pf = pct(frame);
        const auto over = std::count_if(frame.begin(), frame.end(), [](double v) { return v > 1e6 / 120; });
        std::printf("%-22s %7.1f %7.1f %7.1f %7.1f %7.1f %7.1f | %7.1f %7.1f | %7.1f %7.1f %4ld | %5llu %5llu %5llu %5llu %5llu %5llu %5llu %5llu %7llu",
                    s.name, pc.mean, pc.p50, pc.p99, pl.p50, pt.p50, ps.p50, pg.mean, pg.p99, pf.mean, pf.p99, long(over),
                    (unsigned long long)per.calls(), (unsigned long long)per.draws, (unsigned long long)per.programs,
                    (unsigned long long)per.uniforms, (unsigned long long)per.textures, (unsigned long long)per.attribs,
                    (unsigned long long)per.buffers, (unsigned long long)per.toggles, (unsigned long long)(per.upload_bytes >> 10));
        std::printf("\n");
    }
    std::printf("report: %s\n", gl->report().c_str());
    gl.reset();
    // Cards and the font on the CPU (what a slider drag or a help page turn costs before the upload).
    auto time = [](const char* name, int n, auto&& fn) {
        std::vector<double> v;
        for (int i = -3; i < n; ++i) { const double t = now_us(); fn(i < 0 ? 0 : i); if (i >= 0) v.push_back(now_us() - t); }
        const Pct p = pct(v);
        std::printf("%-26s n=%-6d mean %9.2f us  p50 %9.2f  p99 %9.2f\n", name, n, p.mean, p.p50, p.p99);
    };
    const HudFont* font = r.font.get();
    volatile float sink = 0;
    if (font) {  // the first card a freshly loaded font renders (what the first pop-up / panel / help frame pays)
        auto first = [&](const char* name, auto&& fn) {
            std::vector<double> v;
            for (int k = 0; k < 5; ++k) {
                std::string why_font;
                auto fresh = HudFont::load(kHudFontFamily, why_font);
                if (!fresh) return;
                const double t = now_us(); fn(fresh.get()); v.push_back(now_us() - t);
            }
            const Pct p = pct(v);
            std::printf("%-26s n=%-6d mean %9.2f us  p50 %9.2f  p99 %9.2f\n", name, 5, p.mean, p.p50, p.p99);
        };
        first("first.panel (fresh font)", [&](const HudFont* f) { auto im = render_glass_panel(panel_state(0), f); sink = sink + im.rgba[0]; });
        first("first.popup (fresh font)", [&](const HudFont* f) { auto im = render_hud_card(view_mode_lines(std::uint8_t(0)), kAccent, f); sink = sink + im.rgba[0]; });
    }
    if (font) {
        time("font.metrics x100", 2000, [&](int) { for (int k = 0; k < 100; ++k) sink = sink + float(font->metrics(18 + std::uint32_t(k % 3) * 6, k & 1).ascent); });
        time("font.measure x100", 2000, [&](int) { for (int k = 0; k < 100; ++k) sink = sink + float(font->measure("Distance  100%  \xe2\x86\x90\xe2\x86\x92", 24, k & 1)); });
        time("font.draw x10", 2000, [&](int) {
            for (int k = 0; k < 10; ++k) font->draw("Curvature  +12\xc2\xb0", 24, k & 1, 0, 24, [&](int, int, float c) { sink = sink + c; });
        });
        time("font.draw76 x4", 2000, [&](int) {  // the pop-up's big line (scale 4 = 76 px, bold)
            for (int k = 0; k < 4; ++k) font->draw("Anchor Locked", 76, true, 0, 80, [&](int, int, float c) { sink = sink + c; });
        });
    }
    time("card.panel (slider drag)", 300, [&](int i) { auto im = render_glass_panel(panel_state(drag_x(i)), font); sink = sink + im.rgba[0]; });
    time("card.popup", 300, [&](int) { auto im = render_hud_card(view_mode_lines(std::uint8_t(0)), kAccent, font); sink = sink + im.rgba[0]; });
    const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement pill of the selected desktop"},
                                    {"Ctrl+Alt+Z", "Zoom in / out"}, {"Ctrl+Alt+Left/Right", "Select a desktop"},
                                    {"Ctrl+Alt+R", "Recentre"}, {"Ctrl+Alt+M", "Viewing mode", true}, {"Ctrl+Alt+T", "Typing mode"}};
    time("card.help", 40, [&](int) { auto im = render_help_card(rows, kAccent, font, w, h); sink = sink + im.rgba[0]; });
    return 0;
}
}  // namespace

int main(int argc, char** argv) {
    std::uint32_t w = 1536, h = 960;  // the 1920x1200 glasses at scale 1.25 (check mode); --size for others
    int frames = 1000;
    enum { kCheck, kHashes, kBench } mode = kCheck;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--hashes") mode = kHashes;
        else if (a == "--bench") mode = kBench;
        else if (a == "--frames" && i + 1 < argc) frames = std::max(1, std::atoi(argv[++i]));
        else if (a == "--size" && i + 1 < argc) { unsigned ww = 0, hh = 0; if (std::sscanf(argv[++i], "%ux%u", &ww, &hh) == 2 && ww && hh) { w = ww; h = hh; } }
        else { std::fprintf(stderr, "usage: gl_bench [--hashes | --bench [--frames N]] [--size WxH]\n"); return 2; }
    }
    if (mode == kHashes) return hashes(w, h);
    if (mode == kBench) return bench(w, h, frames);
    return check(w, h);
}
