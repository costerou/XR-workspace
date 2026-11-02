// A closed help card or placement menu must leave nothing in the frame. Drives the real HelpOverlay /
// PlacementMenuOverlay fades and the real GlHud on a surfaceless EGL context (no display, no window, no
// compositor), composing each frame the way GlCompositor::draw_and_swap does: clear to opaque black, an
// opaque desktop tile, then help (hidden under an open menu) and menu at their fade alpha. After each close
// the read-back frame must equal the frame from before anything opened. Exit 77 (skipped) without EGL/GLES.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "xr/hud.hpp"
#include "hud_gl.hpp"

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

GLuint program(const char* vs, const char* fs) {
    auto compile = [](GLenum type, const char* src) { GLuint s = glCreateShader(type); glShaderSource(s, 1, &src, nullptr); glCompileShader(s); return s; };
    GLuint p = glCreateProgram(); glAttachShader(p, compile(GL_VERTEX_SHADER, vs)); glAttachShader(p, compile(GL_FRAGMENT_SHADER, fs));
    glLinkProgram(p); GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok); return ok ? p : 0;
}

bool make_context() {
    auto get_display = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
    if (!get_display) return false;
    EGLDisplay d = get_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    if (d == EGL_NO_DISPLAY || !eglInitialize(d, nullptr, nullptr) || !eglBindAPI(EGL_OPENGL_ES_API)) return false;
    const EGLint config_attribs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE}; EGLConfig config; EGLint n = 0;
    if (!eglChooseConfig(d, config_attribs, &config, 1, &n) || n < 1) return false;
    const EGLint context_attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 2, EGL_NONE};
    EGLContext c = eglCreateContext(d, config, EGL_NO_CONTEXT, context_attribs);
    return c != EGL_NO_CONTEXT && eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c);
}
}  // namespace

int main() {
    if (!make_context()) { std::puts("SKIP: no surfaceless EGL / GLES2 context"); return 77; }
    constexpr int W = 1536, H = 960;  // the 1920x1200 @1.25 glasses surface
    GLuint fb = 0, rb = 0;
    glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, W, H);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { std::puts("SKIP: no RGBA8 framebuffer"); return 77; }
    const GLuint tile = program("attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n",
                                "precision mediump float; void main() { gl_FragColor = vec4(0.25, 0.25, 0.25, 1.0); }\n");
    auto help_gl = GlHud::create(), menu_gl = GlHud::create(), pointer_gl = GlHud::create();
    CHECK(tile && help_gl && menu_gl && pointer_gl);
    if (!tile || !help_gl || !menu_gl || !pointer_gl) return 1;
    const GLint tile_pos = glGetAttribLocation(tile, "pos");

    HelpOverlay help; PlacementMenuOverlay menu; menu.set_viewport(W, H);
    std::string why; help.set_font(HudFont::load(kHudFontFamily, why)); menu.set_font(HudFont::load(kHudFontFamily, why));
    const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement menu (arrows adjust)"}};
    std::vector<std::uint8_t> pixels(std::size_t(W) * H * 4), before;

    auto frame = [&](double now) {
        glViewport(0, 0, W, H); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
        const GLfloat quad[] = {-0.9f, 0.9f, 0.9f, 0.9f, -0.9f, -0.9f, 0.9f, -0.9f};  // desktop tile; black border like the gaps
        glUseProgram(tile); glVertexAttribPointer(GLuint(tile_pos), 2, GL_FLOAT, GL_FALSE, 0, quad); glEnableVertexAttribArray(GLuint(tile_pos));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        // Same selection as GlCompositor: an image only while its alpha is > 0, and the menu replaces the help card.
        const float m = menu.alpha(now), h = help.alpha(now) * (m > 0.f ? 1.f - m : 1.f);
        if (h > 0.f) help_gl->draw(help.image(), help.generation(), h, W, H, true, help.offset_px(now));
        if (m > 0.f) menu_gl->draw(menu.image(), menu.generation(), m, W, H, true, menu.offset_px(now));
        if (m > 0.f && menu.pointer_visible()) {  // the mouse-mode pointer, placed as wayland.cpp does
            const auto& pt = menu.pointer_image();
            const auto o = centered_card_origin(W, H, menu.image().width, menu.image().height, menu.offset_px(now));
            pointer_gl->draw_at(pt, menu.pointer_generation(), m, W, H, o.x + float(menu.pointer_x()) - float(pt.width / 2),
                                o.y + float(menu.pointer_y()) - float(pt.height / 2));
        }
        glFinish(); glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
        CHECK(glGetError() == GL_NO_ERROR);
    };
    auto changed = [&] { std::size_t n = 0; for (std::size_t i = 0; i < pixels.size(); ++i) n += pixels[i] != before[i]; return n; };

    double now = 1000;  // one monotonic clock for both passes, like monotonic_ms() in the renderer
    for (const double hz : {120.0, 60.0}) {
        const double dt = 1000.0 / hz;
        auto run = [&](double ms) { for (double e = 0; e < ms; e += dt, now += dt) frame(now); };
        frame(now); before = pixels;  // nothing ever opened
        auto closed_clean = [&](const char* what) {
            const auto n = changed();
            if (n) std::fprintf(stderr, "%.0f Hz, %s: %zu bytes differ from the never-opened frame\n", hz, what, n);
            CHECK(n == 0);
        };
        const double settle = kHelpFadeOutMs + 2 * dt;  // the fade-out plus the frame that clears it

        help.set(true, rows, now); run(1000); CHECK(changed() > 0);  // the card is really drawn
        help.set(false, rows, now); run(settle); closed_clean("help card closed");

        PlacementMenuState state; state.open = true;
        menu.set(state, now); run(1000); CHECK(changed() > 0);
        for (int i = 0; i < 14; ++i) { state.field = i % kPlacementMenuFields; state.desktop = i % 3; menu.set(state, now); run(40); }  // live re-renders
        state.open = false; menu.set(state, now); run(settle); closed_clean("placement menu closed");

        help.set(true, rows, now); run(60); help.set(false, rows, now); run(settle); closed_clean("help card closed mid fade-in");

        state.open = true; menu.set(state, now); run(500); help.set(true, rows, now); run(300);  // help toggled on under the menu
        state.open = false; menu.set(state, now); run(500); help.set(false, rows, now); run(settle);
        closed_clean("menu closed over the help card, then help closed");
    }
    // Once faded in, an open card stays perfectly still: frames a minute apart are identical.
    {
        frame(now); before = pixels;  // nothing open
        PlacementMenuState state; state.open = true; menu.set(state, now); help.set(true, rows, now);
        frame(now + kHelpFadeInMs + 1); CHECK(changed() > 0); before = pixels;  // the menu is really drawn
        for (const double later : {2000.0, 7000.0, 31000.0, 60000.0}) {
            frame(now + kHelpFadeInMs + 1 + later);
            if (const auto n = changed()) std::fprintf(stderr, "open card moved after %.0f ms: %zu bytes differ\n", later, n);
            CHECK(changed() == 0);
        }
        state.open = false; menu.set(state, now); help.set(false, rows, now);
    }
    // Mouse-mode pointer: drawn exactly on the card point it names (a slider's knob, from the menu's own geometry),
    // moves 1:1 with no card re-render, and leaves nothing behind when hidden or when the menu closes.
    {
        frame(now); const auto empty = pixels;
        PlacementMenuState state; state.open = true; menu.set(state, now);
        const double open = now + kHelpFadeInMs + 1;
        frame(open); before = pixels;
        const auto& g = menu.geometry();
        CHECK(!g.sliders.empty());
        const auto o = centered_card_origin(W, H, menu.image().width, menu.image().height, 0);
        auto box = [&]() {  // bounding box of what differs from `before`, top-down output pixels
            int x0 = W, y0 = H, x1 = -1, y1 = -1;
            for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
                const std::size_t i = (std::size_t(H - 1 - y) * W + std::size_t(x)) * 4;  // GL rows are bottom-up
                if (pixels[i] != before[i] || pixels[i + 1] != before[i + 1] || pixels[i + 2] != before[i + 2]) {
                    x0 = std::min(x0, x); y0 = std::min(y0, y); x1 = std::max(x1, x); y1 = std::max(y1, y);
                }
            }
            return std::array<int, 4>{x0, y0, x1, y1};
        };
        const auto renders = menu.render_count();
        const auto& sl = g.sliders.front();
        const int px = int(std::lround(sl.lo_x)), py = (sl.top + sl.bottom) / 2;
        menu.set_pointer(true, px, py, true);
        frame(open + 10);
        const auto a = box();
        const int cx = int(o.x) + px, cy = int(o.y) + py;  // where the card point is in the output
        {  // exactly the pointer image's lit pixels, its middle on that point
            const auto& pt = menu.pointer_image();
            int x0 = 1 << 20, y0 = 1 << 20, x1 = -1, y1 = -1;
            for (int y = 0; y < int(pt.height); ++y) for (int x = 0; x < int(pt.width); ++x)
                if (pt.rgba[(std::size_t(y) * pt.width + std::size_t(x)) * 4 + 3]) { x0 = std::min(x0, x); y0 = std::min(y0, y); x1 = std::max(x1, x); y1 = std::max(y1, y); }
            const int left = cx - int(pt.width / 2), top = cy - int(pt.height / 2);
            if (a != std::array<int, 4>{left + x0, top + y0, left + x1, top + y1})
                std::fprintf(stderr, "pointer drawn at %d,%d..%d,%d, expected %d,%d..%d,%d\n", a[0], a[1], a[2], a[3], left + x0, top + y0, left + x1, top + y1);
            CHECK((a == std::array<int, 4>{left + x0, top + y0, left + x1, top + y1}));
        }
        {
            const std::size_t i = (std::size_t(H - 1 - cy) * W + std::size_t(cx)) * 4;  // the held pointer's middle: its grey
            CHECK(pixels[i] == pixels[i + 1] && pixels[i] >= 140 && pixels[i] <= kCardMaxLevel);
        }
        menu.set_pointer(true, px + 37, py + 11, true);
        frame(open + 20);
        const auto b = box();
        CHECK(b[0] == a[0] + 37 && b[2] == a[2] + 37 && b[1] == a[1] + 11 && b[3] == a[3] + 11);  // 1:1, nothing snaps
        CHECK(menu.render_count() == renders);  // the card was not rasterised again
        std::uint8_t brightest = 0;
        for (std::size_t i = 0; i < pixels.size(); i += 4) for (int c = 0; c < 3; ++c) brightest = std::max(brightest, pixels[i + std::size_t(c)]);
        CHECK(brightest <= kCardMaxLevel);
        menu.set_pointer(false, px + 37, py + 11, false);
        frame(open + 30);
        { const auto c = box(); CHECK(c[2] < 0); }  // hidden: the frame is the plain open menu again
        menu.set_pointer(true, px, py, false);
        state.open = false; menu.set(state, open + 40);
        frame(open + 40 + kHelpFadeOutMs + 1); before = empty;
        if (const auto n = changed()) std::fprintf(stderr, "menu closed with the pointer shown: %zu bytes differ\n", n);
        CHECK(changed() == 0);
        menu.set_pointer(false, 0, 0, false);
    }
    std::printf("%s\n", failures ? "overlay residue test FAILED" : "overlay residue test passed");
    return failures ? 1 : 0;
}
