#include "gl.hpp"
#include <wayland-client.h>
#include <wayland-egl.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <chrono>
#include <cstring>

namespace xr {
namespace {
std::uint64_t now_us() {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
const char* kVertex =
    "attribute vec2 pos; attribute vec2 uv; varying vec2 v;\n"
    "void main() { v = uv; gl_Position = vec4(pos, 0.0, 1.0); }\n";
// wl_shm XRGB8888 is little-endian B,G,R,X; uploaded as RGBA bytes, so swizzle back.
const char* kFragment =
    "precision mediump float; varying vec2 v; uniform sampler2D tex;\n"
    "void main() { vec4 c = texture2D(tex, v); gl_FragColor = vec4(c.b, c.g, c.r, 1.0); }\n";
const char* kColorVertex = "attribute vec2 pos; void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n";
const char* kColorFragment = "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";
GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) { glDeleteShader(shader); return 0; }
    return shader;
}
struct Stat { std::uint64_t count = 0, total_us = 0, max_us = 0;
    void add(std::uint64_t us) { ++count; total_us += us; if (us > max_us) max_us = us; }
    std::string json() const { return "{\"count\":" + std::to_string(count) + ",\"mean_us\":" + std::to_string(count ? total_us / count : 0) +
                                      ",\"max_us\":" + std::to_string(max_us) + "}"; } };
std::string escape(const char* s) {
    std::string out;
    for (const char* p = s ? s : ""; *p && out.size() < 120; ++p) if (*p >= 0x20 && *p < 0x7f && *p != '"' && *p != '\\') out += *p;
    return out;
}
}

struct GlCompositor::Impl {
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    wl_egl_window* window = nullptr;
    GLuint program = 0, color_program = 0;
    GLint pos = -1, uv = -1, sampler = -1, color_pos = -1, color = -1;
    struct Texture { GLuint id = 0; std::uint32_t width = 0, height = 0; bool valid = false; };
    std::vector<Texture> textures;
    std::uint32_t width = 0, height = 0;
    std::string vendor, renderer, version;
    Stat upload_stat, draw_stat;
    std::uint64_t uploaded_bytes = 0;
    ~Impl() {
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, surface, surface, context);
            for (auto& t : textures) if (t.id) glDeleteTextures(1, &t.id);
            if (program) glDeleteProgram(program);
            if (color_program) glDeleteProgram(color_program);
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            if (surface != EGL_NO_SURFACE) eglDestroySurface(display, surface);
            if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
            eglTerminate(display);
        }
        if (window) wl_egl_window_destroy(window);
    }
};

std::unique_ptr<GlCompositor> GlCompositor::create(wl_display* wl, wl_surface* wl_surf, std::uint32_t width, std::uint32_t height,
                                                   std::size_t sources, std::string& why) {
    std::unique_ptr<GlCompositor> out(new GlCompositor());
    out->impl_ = std::make_unique<Impl>();
    auto& s = *out->impl_;
    s.width = width; s.height = height;
    s.display = eglGetPlatformDisplay(EGL_PLATFORM_WAYLAND_KHR, wl, nullptr);
    if (s.display == EGL_NO_DISPLAY || !eglInitialize(s.display, nullptr, nullptr)) { why = "egl display"; return nullptr; }
    if (!eglBindAPI(EGL_OPENGL_ES_API)) { why = "gles api"; return nullptr; }
    const EGLint config_attribs[] = {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                                     EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 0, EGL_NONE};
    EGLConfig config; EGLint count = 0;
    if (!eglChooseConfig(s.display, config_attribs, &config, 1, &count) || count < 1) { why = "egl config"; return nullptr; }
    const EGLint context_attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 2, EGL_NONE};
    s.context = eglCreateContext(s.display, config, EGL_NO_CONTEXT, context_attribs);
    if (s.context == EGL_NO_CONTEXT) { why = "egl context"; return nullptr; }
    s.window = wl_egl_window_create(wl_surf, static_cast<int>(width), static_cast<int>(height));
    if (!s.window) { why = "wl_egl_window"; return nullptr; }
    s.surface = eglCreatePlatformWindowSurface(s.display, config, s.window, nullptr);
    if (s.surface == EGL_NO_SURFACE || !eglMakeCurrent(s.display, s.surface, s.surface, s.context)) { why = "egl surface"; return nullptr; }
    eglSwapInterval(s.display, 0);  // pacing is the caller's frame callback, never a blocking swap
    s.vendor = escape(reinterpret_cast<const char*>(glGetString(GL_VENDOR)));
    s.renderer = escape(reinterpret_cast<const char*>(glGetString(GL_RENDERER)));
    s.version = escape(reinterpret_cast<const char*>(glGetString(GL_VERSION)));
    GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vs || !fs) { why = "shader compile"; return nullptr; }
    s.program = glCreateProgram(); glAttachShader(s.program, vs); glAttachShader(s.program, fs); glLinkProgram(s.program);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(s.program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return nullptr; }
    GLuint cvs = compile(GL_VERTEX_SHADER, kColorVertex), cfs = compile(GL_FRAGMENT_SHADER, kColorFragment);
    if (!cvs || !cfs) { why = "shader compile"; return nullptr; }
    s.color_program = glCreateProgram(); glAttachShader(s.color_program, cvs); glAttachShader(s.color_program, cfs); glLinkProgram(s.color_program);
    glDeleteShader(cvs); glDeleteShader(cfs);
    glGetProgramiv(s.color_program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return nullptr; }
    s.color_pos = glGetAttribLocation(s.color_program, "pos"); s.color = glGetUniformLocation(s.color_program, "color");
    s.pos = glGetAttribLocation(s.program, "pos"); s.uv = glGetAttribLocation(s.program, "uv");
    s.sampler = glGetUniformLocation(s.program, "tex");
    s.textures.resize(sources);
    for (auto& t : s.textures) {
        glGenTextures(1, &t.id); glBindTexture(GL_TEXTURE_2D, t.id);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }
    if (glGetError() != GL_NO_ERROR) { why = "gl setup"; return nullptr; }
    return out;
}

GlCompositor::~GlCompositor() = default;

void GlCompositor::resize(std::uint32_t width, std::uint32_t height) {
    auto& s = *impl_;
    if (width == s.width && height == s.height) return;
    s.width = width; s.height = height;
    wl_egl_window_resize(s.window, static_cast<int>(width), static_cast<int>(height), 0, 0);
}

bool GlCompositor::upload(std::size_t source, const void* pixels, std::uint32_t width, std::uint32_t height, std::uint32_t stride) {
    auto& s = *impl_;
    if (source >= s.textures.size() || !pixels || stride != width * 4) return false;
    const auto start = now_us();
    auto& t = s.textures[source];
    glBindTexture(GL_TEXTURE_2D, t.id);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    if (t.width != width || t.height != height) {
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, static_cast<GLsizei>(width), static_cast<GLsizei>(height), 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
        t.width = width; t.height = height;
    } else {
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, static_cast<GLsizei>(width), static_cast<GLsizei>(height), GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    }
    t.valid = glGetError() == GL_NO_ERROR;
    s.uploaded_bytes += std::uint64_t(stride) * height;
    s.upload_stat.add(now_us() - start);
    return t.valid;
}

bool GlCompositor::draw_and_swap(const std::vector<FRect>& tiles, int highlight, float highlight_alpha) {
    auto& s = *impl_;
    const auto start = now_us();
    const float vw = float(s.width), vh = float(s.height);
    auto quad = [&](const FRect& r, GLfloat* out) {
        const float x0 = 2 * r.x / vw - 1, x1 = 2 * (r.x + r.width) / vw - 1, y0 = 1 - 2 * r.y / vh, y1 = 1 - 2 * (r.y + r.height) / vh;
        const GLfloat v[] = {x0, y0, x1, y0, x0, y1, x1, y1};
        std::memcpy(out, v, sizeof(v));
    };
    glViewport(0, 0, static_cast<GLsizei>(s.width), static_cast<GLsizei>(s.height));
    glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
    if (highlight >= 0 && std::size_t(highlight) < tiles.size() && highlight_alpha > 0.f) {
        const float border = 6.f;
        const auto& t = tiles[std::size_t(highlight)];
        GLfloat positions[8]; quad({t.x - border, t.y - border, t.width + 2 * border, t.height + 2 * border}, positions);
        glUseProgram(s.color_program);
        glUniform4f(s.color, 0.48f * highlight_alpha, 0.64f * highlight_alpha, 0.97f * highlight_alpha, 1.f);  // accent over black
        glVertexAttribPointer(static_cast<GLuint>(s.color_pos), 2, GL_FLOAT, GL_FALSE, 0, positions);
        glEnableVertexAttribArray(static_cast<GLuint>(s.color_pos));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    glUseProgram(s.program); glUniform1i(s.sampler, 0); glActiveTexture(GL_TEXTURE0);
    for (std::size_t i = 0; i < tiles.size() && i < s.textures.size(); ++i) {
        if (!s.textures[i].valid || tiles[i].x + tiles[i].width < 0 || tiles[i].x > vw) continue;  // fully off-screen
        GLfloat positions[8]; quad(tiles[i], positions);
        const GLfloat uvs[] = {0, 0, 1, 0, 0, 1, 1, 1};
        glBindTexture(GL_TEXTURE_2D, s.textures[i].id);
        glVertexAttribPointer(static_cast<GLuint>(s.pos), 2, GL_FLOAT, GL_FALSE, 0, positions);
        glVertexAttribPointer(static_cast<GLuint>(s.uv), 2, GL_FLOAT, GL_FALSE, 0, uvs);
        glEnableVertexAttribArray(static_cast<GLuint>(s.pos)); glEnableVertexAttribArray(static_cast<GLuint>(s.uv));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    const bool ok = glGetError() == GL_NO_ERROR && eglSwapBuffers(s.display, s.surface);
    s.draw_stat.add(now_us() - start);
    return ok;
}

std::string GlCompositor::report() const {
    const auto& s = *impl_;
    return "{\"path\":\"gles2-shm-upload\",\"vendor\":\"" + s.vendor + "\",\"renderer\":\"" + s.renderer + "\",\"version\":\"" + s.version +
           "\",\"upload\":" + s.upload_stat.json() + ",\"draw_swap\":" + s.draw_stat.json() +
           ",\"uploaded_mib\":" + std::to_string(s.uploaded_bytes >> 20) + "}";
}
}
