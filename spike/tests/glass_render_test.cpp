// The glass fragment shader (glass.hpp glass_fragment_shader) on a real GLES2 context matches its CPU mirror
// glass_shade() pixel for pixel (within rounding) over black and over a bright (0.9) desktop; over black the glass alone,
// and the glass with the panel content composited on top, stays at or under the 191/255 OLED cap (over a bright desktop
// the desktop itself is brighter than that, so the cap is not checked there). Surfaceless EGL: no display, window or
// compositor. Exit 77 (skipped) without EGL/GLES.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>
#include "xr/glass.hpp"

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

GLuint program(const char* vs, const char* fs) {
    auto compile = [](GLenum type, const char* src) {
        GLuint s = glCreateShader(type); glShaderSource(s, 1, &src, nullptr); glCompileShader(s);
        GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
        if (!ok) { char log[1024] = {}; glGetShaderInfoLog(s, sizeof log, nullptr, log); std::fprintf(stderr, "shader: %s\n", log); }
        return s;
    };
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
    constexpr int W = 640, H = 480;
    GLuint fb = 0, rb = 0;
    glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
    glGenRenderbuffers(1, &rb); glBindRenderbuffer(GL_RENDERBUFFER, rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, W, H);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { std::puts("SKIP: no RGBA8 framebuffer"); return 77; }
    // Canvas px = framebuffer px here (top-down), so the shader sees exactly the pixel centres glass_shade() gets.
    const GLuint glass = program("attribute vec2 pos; attribute vec2 px; varying vec2 p; void main() { p = px; gl_Position = vec4(pos, 0.0, 1.0); }\n",
                                 glass_fragment_shader());
    const GLuint flat = program("attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n",
                                "precision mediump float; uniform vec4 c; void main() { gl_FragColor = c; }\n");
    const GLuint panel = program("attribute vec2 pos; attribute vec2 uv; varying vec2 v; void main() { v = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n",
                                 "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\n"
                                 "void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n");
    CHECK(glass && flat && panel);
    if (!glass || !flat || !panel) return 1;
    std::vector<std::uint8_t> pixels(std::size_t(W) * H * 4);
    auto read = [&] { glFinish(); glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data()); };
    auto at = [&](int x, int y) { return &pixels[(std::size_t(H - 1 - y) * W + std::size_t(x)) * 4]; };  // top-down
    const GLfloat full[] = {-1, 1, 1, 1, -1, -1, 1, -1};
    const GLfloat px[] = {0, 0, float(W), 0, 0, float(H), float(W), float(H)};
    auto background = [&](float level) {
        glViewport(0, 0, W, H); glDisable(GL_BLEND); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
        if (level <= 0) return;
        glUseProgram(flat); glUniform4f(glGetUniformLocation(flat, "c"), level, level, level, 1);
        const GLint pos = glGetAttribLocation(flat, "pos");
        glVertexAttribPointer(GLuint(pos), 2, GL_FLOAT, GL_FALSE, 0, full); glEnableVertexAttribArray(GLuint(pos));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    };
    auto shape = [&](const GlassLook& k, const FRect& r, float radius, float opacity) {
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        glUseProgram(glass);
        glUniform4f(glGetUniformLocation(glass, "rect"), r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2);
        glUniform1f(glGetUniformLocation(glass, "radius"), radius);
        glUniform3f(glGetUniformLocation(glass, "tint"), k.tint[0], k.tint[1], k.tint[2]);
        glUniform4f(glGetUniformLocation(glass, "look"), k.fill, k.gloss, k.gloss_px, k.level);
        glUniform4f(glGetUniformLocation(glass, "edge"), k.border_top, k.border_bottom, k.shadow, k.shadow_sigma);
        glUniform1f(glGetUniformLocation(glass, "shadow_dy"), k.shadow_dy); glUniform1f(glGetUniformLocation(glass, "opacity"), opacity);
        const GLint pos = glGetAttribLocation(glass, "pos"), pxl = glGetAttribLocation(glass, "px");
        glVertexAttribPointer(GLuint(pos), 2, GL_FLOAT, GL_FALSE, 0, full); glEnableVertexAttribArray(GLuint(pos));
        glVertexAttribPointer(GLuint(pxl), 2, GL_FLOAT, GL_FALSE, 0, px); glEnableVertexAttribArray(GLuint(pxl));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        glDisableVertexAttribArray(GLuint(pxl));
    };
    const FRect tile{80, 20, 480, 120};
    for (float t : {0.f, 0.5f, 1.f})
        for (float level : {kGlassIdle, kGlassHover, kGlassActive})
            for (float bg : {0.f, 0.9f}) {
                const auto look = glass_look({0.55f, 0.6f, 0.9f}, level, t);
                const FRect r = glass_morph(tile, t);
                const float radius = glass_radius(tile, t);
                background(bg); shape(look, r, radius, 1.f); read();
                int worst = 0, brightest = 0;
                for (int y = 0; y < H; ++y)
                    for (int x = 0; x < W; ++x) {
                        const auto c = glass_shade(look, r, radius, float(x) + 0.5f, float(y) + 0.5f, 1.f);
                        const auto* p = at(x, y);
                        for (int i = 0; i < 3; ++i) {
                            const float want = c[std::size_t(i)] + bg * (1 - c[3]);  // premultiplied over the background
                            worst = std::max(worst, int(std::lround(std::fabs(float(p[i]) - want * 255.f))));
                            if (bg == 0.f) brightest = std::max(brightest, int(p[i]));
                        }
                    }
                if (worst > 3) std::fprintf(stderr, "t %.1f level %.1f bg %.1f: shader differs from glass_shade by %d/255\n", t, level, bg, worst);
                CHECK(worst <= 3);
                CHECK(brightest <= kCardMaxLevel);
                if (bg == 0.f) CHECK(brightest > 40);  // really drawn
            }
    // The panel content over the open glass: still under the cap, and it shows.
    {
        const FRect r = glass_panel({80, -120, 480, 120});  // the panel at the top of the framebuffer
        PlacementMenuState st; st.open = true; st.field = 2;
        for (auto& p : st.placements) p = placement_menu_flat();
        std::string why;
        const auto font = HudFont::load(kHudFontFamily, why);
        const auto im = render_glass_panel(st, font.get());
        background(0); shape(glass_look({1, 1, 1}, kGlassActive, 1), r, kPanelRadius, 1.f);
        GLuint tex = 0; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(im.width), GLsizei(im.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, im.rgba.data());
        const float l = 2 * r.x / W - 1, rr = 2 * (r.x + r.width) / W - 1, t = 1 - 2 * r.y / H, b = 1 - 2 * (r.y + r.height) / H;
        const GLfloat quad[] = {l, t, rr, t, l, b, rr, b}, uv[] = {0, 0, 1, 0, 0, 1, 1, 1};
        glUseProgram(panel); glUniform1i(glGetUniformLocation(panel, "tex"), 0); glUniform1f(glGetUniformLocation(panel, "alpha"), 1.f);
        const GLint pos = glGetAttribLocation(panel, "pos"), uvl = glGetAttribLocation(panel, "uv");
        glVertexAttribPointer(GLuint(pos), 2, GL_FLOAT, GL_FALSE, 0, quad); glEnableVertexAttribArray(GLuint(pos));
        glVertexAttribPointer(GLuint(uvl), 2, GL_FLOAT, GL_FALSE, 0, uv); glEnableVertexAttribArray(GLuint(uvl));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        read();
        int brightest = 0;
        for (std::size_t i = 0; i < pixels.size(); i += 4) for (int c = 0; c < 3; ++c) brightest = std::max(brightest, int(pixels[i + std::size_t(c)]));
        CHECK(brightest <= kCardMaxLevel && brightest >= 140);
        glDeleteTextures(1, &tex);
    }
    CHECK(glGetError() == GL_NO_ERROR);
    std::printf("%s\n", failures ? "glass render test FAILED" : "glass render test passed");
    return failures ? 1 : 0;
}
