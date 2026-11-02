// A closed help card must leave nothing in the frame. Drives the real HelpOverlay fade and the real GlHud on a
// surfaceless EGL context (no display, no window, no compositor), composing each frame the way
// GlCompositor::draw_and_swap does: clear to opaque black, an opaque desktop tile, then the help card at its fade
// alpha. After each close the read-back frame must equal the frame from before anything opened. (The placement
// panels are world-space glass pills now: glass_render_test.) Exit 77 (skipped) without EGL/GLES.
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
    auto help_gl = GlHud::create();
    CHECK(tile && help_gl);
    if (!tile || !help_gl) return 1;
    const GLint tile_pos = glGetAttribLocation(tile, "pos");

    HelpOverlay help;
    std::string why; help.set_font(HudFont::load(kHudFontFamily, why));
    const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement pill of the selected desktop"}};
    std::vector<std::uint8_t> pixels(std::size_t(W) * H * 4), before;

    auto frame = [&](double now) {
        glViewport(0, 0, W, H); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
        const GLfloat quad[] = {-0.9f, 0.9f, 0.9f, 0.9f, -0.9f, -0.9f, 0.9f, -0.9f};  // desktop tile; black border like the gaps
        glUseProgram(tile); glVertexAttribPointer(GLuint(tile_pos), 2, GL_FLOAT, GL_FALSE, 0, quad); glEnableVertexAttribArray(GLuint(tile_pos));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        // Same selection as GlCompositor: an image only while its alpha is > 0.
        const float h = help.alpha(now);
        if (h > 0.f) help_gl->draw(help.image(), help.generation(), h, W, H, true, help.offset_px(now));
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

        help.set(true, rows, now); run(60); help.set(false, rows, now); run(settle); closed_clean("help card closed mid fade-in");
        help.set(true, rows, now); run(200); help.set(false, rows, now); run(80); help.set(true, rows, now); run(80);  // reversed mid fade
        help.set(false, rows, now); run(settle); closed_clean("help card reversed twice, then closed");
    }
    // Once faded in, an open card stays perfectly still: frames a minute apart are identical.
    {
        frame(now); before = pixels;  // nothing open
        help.set(true, rows, now);
        frame(now + kHelpFadeInMs + 1); CHECK(changed() > 0); before = pixels;  // the card is really drawn
        for (const double later : {2000.0, 7000.0, 31000.0, 60000.0}) {
            frame(now + kHelpFadeInMs + 1 + later);
            if (const auto n = changed()) std::fprintf(stderr, "open card moved after %.0f ms: %zu bytes differ\n", later, n);
            CHECK(changed() == 0);
        }
        help.set(false, rows, now);
    }
    std::printf("%s\n", failures ? "overlay residue test FAILED" : "overlay residue test passed");
    return failures ? 1 : 0;
}
