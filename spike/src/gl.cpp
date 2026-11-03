#include "gl.hpp"
#include <wayland-client.h>
#include <wayland-egl.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include "dmabuf.hpp"
#include "hud_gl.hpp"
#include <algorithm>
#include <chrono>
#include <cstring>

namespace xr {
namespace {
std::uint64_t now_us() {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
// World point (view.hpp place_point: canvas plane at z=-1 spanning the FOV, each desktop turned to face the eye, then
// per-desktop placement) -> head rotation -> perspective. A centred desktop with the default placement and identity
// rotation reproduces the head-locked NDC exactly; an off-centre one keeps its centre there and faces you.
#define XR_PROJECT \
    "uniform mat3 view; uniform vec2 half_fov;\n" \
    "vec4 project(vec3 p) { vec3 q = view * p;\n" \
    "  return vec4(q.x / half_fov.x, q.y / half_fov.y, 0.0, -q.z); }\n"
const char* kVertex =
    "attribute vec3 pos; attribute vec2 uv; varying vec2 v;\n" XR_PROJECT
    "void main() { v = uv; gl_Position = project(pos); }\n";
// SHM XRGB bytes upload as RGBA and need a B/R swap; imported dmabufs are sampled natively.
const char* kFragment =
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float swizzle;\n"
    "void main() { vec4 c = texture2D(tex, v); gl_FragColor = vec4(mix(c.rgb, c.bgr, swizzle), 1.0); }\n";
const char* kColorVertex = "attribute vec3 pos;\n" XR_PROJECT "void main() { gl_Position = project(pos); }\n";
const char* kColorFragment = "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";
// Ambient glow: canvas-pixel coordinates are interpolated perspective-correctly, so the falloff
// stays even when the head rotation tilts the plane. Gaussian falloff outside the framed rect.
const char* kGlowVertex =
    "attribute vec3 pos; attribute vec2 px; varying vec2 p;\n" XR_PROJECT "void main() { p = px; gl_Position = project(pos); }\n";
const char* kGlowFragment =
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
    "varying vec2 p; uniform vec4 rect; uniform vec3 color; uniform vec2 falloff;\n"
    "void main() { vec2 d2 = max(abs(p - rect.xy) - rect.zw, 0.0); float d = length(d2);\n"
    "  float a = falloff.x * exp(-d * d / (2.0 * falloff.y * falloff.y));\n"
    "  gl_FragColor = vec4(color * a, 1.0); }\n";
// Glass pill content (glass.hpp render_glass_panel): a premultiplied image on the panel's own surface, faded uniformly.
const char* kPanelFragment =
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha;\n"
    "void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n";
constexpr float kBorderPx = 6.f, kGlowRadiusPx = 40.f, kGlowSigmaPx = 12.f, kGlowIntensity = 0.18f;
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
    GLuint program = 0, color_program = 0, glow_program = 0, glass_program = 0, panel_program = 0;
    // Highlight border + glow colour: Hyprland general:col.active_border (Omarchy theme), set by
    // the supervisor at start and on every config reload; this default is used until then.
    float accent[3] = {0x8d / 255.f, 0x8d / 255.f, 0x8d / 255.f};
    GLint pos = -1, uv = -1, sampler = -1, color_pos = -1, color = -1;
    GLint glow_pos = -1, glow_px = -1, glow_rect = -1, glow_color = -1, glow_falloff = -1, glow_view = -1, glow_half_fov = -1;
    GLint view = -1, half_fov = -1, color_view = -1, color_half_fov = -1;
    struct { GLint pos = -1, px = -1, view = -1, half_fov = -1, rect = -1, radius = -1, tint = -1, look = -1, edge = -1, shadow_dy = -1, opacity = -1; } glass_loc;
    struct { GLint pos = -1, uv = -1, view = -1, half_fov = -1, tex = -1, alpha = -1; } panel_loc;
    GLuint panel_texture = 0; std::uint64_t panel_uploaded = 0;
    struct Texture { GLuint id = 0; std::uint32_t width = 0, height = 0; bool valid = false; };
    std::vector<Texture> textures;
    // Zero-copy: per source, per capture slot, an EGLImage-backed texture; `current` = slot to draw (-1 = SHM).
    struct Imported { std::array<GLuint, 3> tex{}; std::array<EGLImageKHR, 3> image{EGL_NO_IMAGE_KHR, EGL_NO_IMAGE_KHR, EGL_NO_IMAGE_KHR}; int current = -1; };
    std::vector<Imported> imported;
    GLint swizzle = -1;
    PFNEGLCREATEIMAGEKHRPROC create_image = nullptr;
    PFNEGLDESTROYIMAGEKHRPROC destroy_image = nullptr;
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target = nullptr;
    PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_modifiers = nullptr;
    std::uint64_t imported_frames = 0;
    std::unique_ptr<GlHud> video_hud;    // VR video controls card (own texture), created on first use
    void drop(Imported& i) {
        for (auto& t : i.tex) if (t) { glDeleteTextures(1, &t); t = 0; }
        for (auto& im : i.image) if (im != EGL_NO_IMAGE_KHR && destroy_image) { destroy_image(display, im); im = EGL_NO_IMAGE_KHR; }
        i.current = -1;
    }
    std::uint32_t width = 0, height = 0;
    std::string vendor, renderer, version;
    Stat upload_stat, draw_stat;
    std::uint64_t uploaded_bytes = 0;
    std::unique_ptr<GlHud> hud;           // status pop-ups; absent if its shaders failed
    const HudImage* popup = nullptr; std::uint64_t popup_generation = 0; float popup_alpha = 0;
    std::unique_ptr<GlHud> help_hud;      // keyboard help card (own texture); absent if its shaders failed
    const HudImage* help = nullptr; std::uint64_t help_generation = 0; float help_alpha = 0, help_drop = 0;
    int handle_desktop = -1; std::uint32_t handle_corner = 0; float handle_level = 0;  // corner resize
    GlassFrame glass;                                                                  // glass pills and the open panel
    bool free_pointer = false; float free_x = 0, free_y = 0, free_level = 0;           // overview free pointer
    ~Impl() {
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, surface, surface, context);
            for (auto& t : textures) if (t.id) glDeleteTextures(1, &t.id);
            for (auto& i : imported) drop(i);
            hud.reset(); help_hud.reset();
            if (panel_texture) glDeleteTextures(1, &panel_texture);
            if (glass_program) glDeleteProgram(glass_program);
            if (panel_program) glDeleteProgram(panel_program);
            video_hud.reset();
            if (program) glDeleteProgram(program);
            if (color_program) glDeleteProgram(color_program);
            if (glow_program) glDeleteProgram(glow_program);
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
    s.color_view = glGetUniformLocation(s.color_program, "view"); s.color_half_fov = glGetUniformLocation(s.color_program, "half_fov");
    GLuint gvs = compile(GL_VERTEX_SHADER, kGlowVertex), gfs = compile(GL_FRAGMENT_SHADER, kGlowFragment);
    if (!gvs || !gfs) { why = "shader compile"; return nullptr; }
    s.glow_program = glCreateProgram(); glAttachShader(s.glow_program, gvs); glAttachShader(s.glow_program, gfs); glLinkProgram(s.glow_program);
    glDeleteShader(gvs); glDeleteShader(gfs);
    glGetProgramiv(s.glow_program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return nullptr; }
    s.glow_pos = glGetAttribLocation(s.glow_program, "pos"); s.glow_px = glGetAttribLocation(s.glow_program, "px");
    s.glow_rect = glGetUniformLocation(s.glow_program, "rect"); s.glow_color = glGetUniformLocation(s.glow_program, "color");
    s.glow_falloff = glGetUniformLocation(s.glow_program, "falloff");
    s.glow_view = glGetUniformLocation(s.glow_program, "view"); s.glow_half_fov = glGetUniformLocation(s.glow_program, "half_fov");
    s.view = glGetUniformLocation(s.program, "view"); s.half_fov = glGetUniformLocation(s.program, "half_fov");
    s.pos = glGetAttribLocation(s.program, "pos"); s.uv = glGetAttribLocation(s.program, "uv");
    s.sampler = glGetUniformLocation(s.program, "tex");
    s.swizzle = glGetUniformLocation(s.program, "swizzle");
    s.hud = GlHud::create();  // optional: composition works without pop-ups
    s.help_hud = GlHud::create();
    // Glass pills: optional too (composition works without them; the run reports no pills then).
    auto link = [](const char* vertex, const char* fragment) -> GLuint {
        GLuint v = compile(GL_VERTEX_SHADER, vertex), f = compile(GL_FRAGMENT_SHADER, fragment);
        if (!v || !f) { if (v) glDeleteShader(v); if (f) glDeleteShader(f); return 0; }
        GLuint p = glCreateProgram(); glAttachShader(p, v); glAttachShader(p, f); glLinkProgram(p);
        glDeleteShader(v); glDeleteShader(f);
        GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
        if (!ok) { glDeleteProgram(p); return 0; }
        return p;
    };
    if ((s.glass_program = link(kGlowVertex, glass_fragment_shader()))) {
        auto& g = s.glass_loc; const GLuint p = s.glass_program;
        g.pos = glGetAttribLocation(p, "pos"); g.px = glGetAttribLocation(p, "px");
        g.view = glGetUniformLocation(p, "view"); g.half_fov = glGetUniformLocation(p, "half_fov");
        g.rect = glGetUniformLocation(p, "rect"); g.radius = glGetUniformLocation(p, "radius"); g.tint = glGetUniformLocation(p, "tint");
        g.look = glGetUniformLocation(p, "look"); g.edge = glGetUniformLocation(p, "edge");
        g.shadow_dy = glGetUniformLocation(p, "shadow_dy"); g.opacity = glGetUniformLocation(p, "opacity");
    }
    if ((s.panel_program = link(kVertex, kPanelFragment))) {
        auto& g = s.panel_loc; const GLuint p = s.panel_program;
        g.pos = glGetAttribLocation(p, "pos"); g.uv = glGetAttribLocation(p, "uv");
        g.view = glGetUniformLocation(p, "view"); g.half_fov = glGetUniformLocation(p, "half_fov");
        g.tex = glGetUniformLocation(p, "tex"); g.alpha = glGetUniformLocation(p, "alpha");
        glGenTextures(1, &s.panel_texture); glBindTexture(GL_TEXTURE_2D, s.panel_texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }
    s.textures.resize(sources);
    s.imported.resize(sources);
    const char* extensions = eglQueryString(s.display, EGL_EXTENSIONS);
    if (extensions && std::strstr(extensions, "EGL_EXT_image_dma_buf_import_modifiers")) {
        s.create_image = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
        s.destroy_image = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
        s.image_target = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(eglGetProcAddress("glEGLImageTargetTexture2DOES"));
        s.query_modifiers = reinterpret_cast<PFNEGLQUERYDMABUFMODIFIERSEXTPROC>(eglGetProcAddress("eglQueryDmaBufModifiersEXT"));
    }
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

bool GlCompositor::dmabuf_importable(std::uint32_t fourcc, std::uint64_t modifier) const {
    const auto& s = *impl_;
    if (!s.create_image || !s.image_target || !s.query_modifiers) return false;
    EGLint count = 0;
    if (!s.query_modifiers(s.display, static_cast<EGLint>(fourcc), 0, nullptr, nullptr, &count) || count <= 0) return false;
    if (modifier == kModInvalid) return true;  // format supported; implicit modifier
    std::vector<EGLuint64KHR> mods(static_cast<std::size_t>(count)); std::vector<EGLBoolean> external(static_cast<std::size_t>(count));
    if (!s.query_modifiers(s.display, static_cast<EGLint>(fourcc), count, mods.data(), external.data(), &count)) return false;
    for (EGLint i = 0; i < count; ++i) if (mods[std::size_t(i)] == modifier && !external[std::size_t(i)]) return true;
    return false;
}

bool GlCompositor::import_dmabuf(std::size_t source, std::size_t slot, const DmabufBuffer& b) {
    auto& s = *impl_;
    if (source >= s.imported.size() || slot >= 3 || !s.create_image) return false;
    auto& im = s.imported[source];
    std::vector<EGLAttrib> a = {EGL_WIDTH, EGLAttrib(b.width), EGL_HEIGHT, EGLAttrib(b.height), EGL_LINUX_DRM_FOURCC_EXT, EGLAttrib(b.fourcc)};
    static const EGLint fd_k[] = {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE1_FD_EXT, EGL_DMA_BUF_PLANE2_FD_EXT, EGL_DMA_BUF_PLANE3_FD_EXT};
    static const EGLint off_k[] = {EGL_DMA_BUF_PLANE0_OFFSET_EXT, EGL_DMA_BUF_PLANE1_OFFSET_EXT, EGL_DMA_BUF_PLANE2_OFFSET_EXT, EGL_DMA_BUF_PLANE3_OFFSET_EXT};
    static const EGLint pitch_k[] = {EGL_DMA_BUF_PLANE0_PITCH_EXT, EGL_DMA_BUF_PLANE1_PITCH_EXT, EGL_DMA_BUF_PLANE2_PITCH_EXT, EGL_DMA_BUF_PLANE3_PITCH_EXT};
    static const EGLint lo_k[] = {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT};
    static const EGLint hi_k[] = {EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE2_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_HI_EXT};
    for (int p = 0; p < b.planes; ++p) {
        a.insert(a.end(), {fd_k[p], EGLAttrib(b.fds[std::size_t(p)]), off_k[p], EGLAttrib(b.offsets[std::size_t(p)]),
                           pitch_k[p], EGLAttrib(b.strides[std::size_t(p)])});
        if (b.modifier != kModInvalid)
            a.insert(a.end(), {lo_k[p], EGLAttrib(b.modifier & 0xffffffffu), hi_k[p], EGLAttrib(b.modifier >> 32)});
    }
    std::vector<EGLint> attribs(a.begin(), a.end()); attribs.push_back(EGL_NONE);
    EGLImageKHR image = s.create_image(s.display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, attribs.data());
    if (image == EGL_NO_IMAGE_KHR) return false;
    GLuint tex = 0; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    s.image_target(GL_TEXTURE_2D, image);
    if (glGetError() != GL_NO_ERROR) { glDeleteTextures(1, &tex); s.destroy_image(s.display, image); return false; }
    if (im.tex[slot]) glDeleteTextures(1, &im.tex[slot]);
    if (im.image[slot] != EGL_NO_IMAGE_KHR) s.destroy_image(s.display, im.image[slot]);
    im.tex[slot] = tex; im.image[slot] = image;
    return true;
}

void GlCompositor::present_dmabuf(std::size_t source, int slot) {
    auto& s = *impl_;
    if (source < s.imported.size() && slot >= 0 && slot < 3 && s.imported[source].tex[std::size_t(slot)]) {
        s.imported[source].current = slot; ++s.imported_frames;
    }
}

void GlCompositor::set_popup(const HudImage* image, std::uint64_t generation, float alpha) {
    impl_->popup = image; impl_->popup_generation = generation; impl_->popup_alpha = alpha;
}

void GlCompositor::set_help(const HudImage* image, std::uint64_t generation, float alpha, float drop_px) {
    impl_->help = image; impl_->help_generation = generation; impl_->help_alpha = alpha; impl_->help_drop = drop_px;
}

void GlCompositor::set_corner_handle(int desktop, std::uint32_t corner, float level) {
    impl_->handle_desktop = desktop; impl_->handle_corner = corner; impl_->handle_level = std::min(level, kHandleLevelActive);
}

void GlCompositor::set_glass(const GlassFrame& frame) { impl_->glass = frame; }

void GlCompositor::set_free_pointer(bool visible, float x, float y, float level) {
    impl_->free_pointer = visible; impl_->free_x = x; impl_->free_y = y; impl_->free_level = std::min(level, kFreePointerHeldLevel);
}

void GlCompositor::drop_dmabuf(std::size_t source) { if (source < impl_->imported.size()) impl_->drop(impl_->imported[source]); }

void GlCompositor::set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b) {
    impl_->accent[0] = r / 255.f; impl_->accent[1] = g / 255.f; impl_->accent[2] = b / 255.f;
}

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

bool GlCompositor::draw_and_swap(const std::vector<FRect>& tiles, const std::vector<Placement>& placements, int highlight,
                                 float highlight_alpha, const Mat3& view, float half_w, float half_h) {
    auto& s = *impl_;
    const auto start = now_us();
    const Mat3 column_major = transpose(view);  // GLES2 has no transpose flag
    auto uniforms = [&](GLint view_loc, GLint fov_loc) {
        glUniformMatrix3fv(view_loc, 1, GL_FALSE, column_major.data()); glUniform2f(fov_loc, half_w, half_h);
    };
    const Canvas canvas{float(s.width), float(s.height), half_w, half_h};
    auto placement = [&](std::size_t i) { return i < placements.size() ? placements[i] : Placement{}; };
    // Interleaved per vertex: world xyz, then u v (tiles) or canvas px py (glow).
    std::vector<GLfloat> buffer;
    auto mesh_on = [&](std::size_t i, const Placement& on, const FRect& rect, bool pixels) {  // desktop i's slot, placed `on`
        const auto vertices = tile_mesh(canvas, tiles[i], on, rect);
        buffer.clear();
        for (const auto& m : vertices) buffer.insert(buffer.end(), {m.world.x, m.world.y, m.world.z, pixels ? m.px : m.u, pixels ? m.py : m.v});
        return GLsizei(vertices.size());
    };
    auto mesh = [&](std::size_t i, const FRect& rect, bool pixels) { return mesh_on(i, placement(i), rect, pixels); };
    auto attrib = [&](GLint loc, int size, int offset) {
        glVertexAttribPointer(static_cast<GLuint>(loc), size, GL_FLOAT, GL_FALSE, 5 * sizeof(GLfloat), buffer.data() + offset);
        glEnableVertexAttribArray(static_cast<GLuint>(loc));
    };
    glViewport(0, 0, static_cast<GLsizei>(s.width), static_cast<GLsizei>(s.height));
    glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
    if (highlight >= 0 && std::size_t(highlight) < tiles.size() && highlight_alpha > 0.f) {
        // Drawn first over black, so no blending is needed: glow, then border, then the tiles (back to front) cover
        // the interior. The frame lies on the highlighted desktop's own surface (same placement and curvature).
        const auto h = std::size_t(highlight);
        const auto& t = tiles[h];
        const FRect framed{t.x - kBorderPx, t.y - kBorderPx, t.width + 2 * kBorderPx, t.height + 2 * kBorderPx};
        const FRect halo{framed.x - kGlowRadiusPx, framed.y - kGlowRadiusPx, framed.width + 2 * kGlowRadiusPx, framed.height + 2 * kGlowRadiusPx};
        glUseProgram(s.glow_program); uniforms(s.glow_view, s.glow_half_fov);
        glUniform4f(s.glow_rect, framed.x + framed.width / 2, framed.y + framed.height / 2, framed.width / 2, framed.height / 2);
        glUniform3f(s.glow_color, s.accent[0], s.accent[1], s.accent[2]);
        glUniform2f(s.glow_falloff, kGlowIntensity * highlight_alpha, kGlowSigmaPx);
        GLsizei n = mesh(h, halo, true);
        attrib(s.glow_pos, 3, 0); attrib(s.glow_px, 2, 3);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        glDisableVertexAttribArray(static_cast<GLuint>(s.glow_px));
        glUseProgram(s.color_program); uniforms(s.color_view, s.color_half_fov);
        glUniform4f(s.color, s.accent[0] * highlight_alpha, s.accent[1] * highlight_alpha, s.accent[2] * highlight_alpha, 1.f);  // over black
        n = mesh(h, framed, false);
        attrib(s.color_pos, 3, 0);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
    }
    glUseProgram(s.program); uniforms(s.view, s.half_fov); glUniform1i(s.sampler, 0); glActiveTexture(GL_TEXTURE0);
    std::vector<Placement> resolved(tiles.size());
    for (std::size_t i = 0; i < tiles.size(); ++i) resolved[i] = placement(i);
    for (const std::size_t i : draw_order(canvas, tiles, resolved)) {
        if (i >= s.textures.size()) continue;
        const auto& im = s.imported[i];
        const bool zero_copy = im.current >= 0;
        if (!zero_copy && !s.textures[i].valid) continue;  // off-screen tiles are clipped by GL (may rotate into view)
        glUniform1f(s.swizzle, zero_copy ? 0.f : 1.f);
        glBindTexture(GL_TEXTURE_2D, zero_copy ? im.tex[std::size_t(im.current)] : s.textures[i].id);
        const GLsizei n = mesh(i, tiles[i], false);
        attrib(s.pos, 3, 0); attrib(s.uv, 2, 3);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
    }
    if (s.glass_program) {
        // Glass pills: blended over the desktops, each on its own desktop's surface (it faces you with its desktop),
        // back to front, except that the open panel's desktop comes last (glass_draw_order): the supervisor hit-tests
        // the open panel as the top layer. The open one morphs into its placement panel; its content fades in over the
        // glass. While a slider is held the open panel stays on its press-time surface (GlassFrame::surface).
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        const auto& g = s.glass;
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        auto glass_on = [&](std::size_t i) { return int(i) == g.surface_desktop && g.surface ? *g.surface : placement(i); };
        auto shape = [&](std::size_t i, const FRect& r, float radius, const GlassLook& look, float opacity, float margin) {
            glUseProgram(s.glass_program); uniforms(s.glass_loc.view, s.glass_loc.half_fov);
            glUniform4f(s.glass_loc.rect, r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2);
            glUniform1f(s.glass_loc.radius, radius);
            glUniform3f(s.glass_loc.tint, look.tint[0], look.tint[1], look.tint[2]);
            glUniform4f(s.glass_loc.look, look.fill, look.gloss, look.gloss_px, look.level);
            glUniform4f(s.glass_loc.edge, look.border_top, look.border_bottom, look.shadow, look.shadow_sigma);
            glUniform1f(s.glass_loc.shadow_dy, look.shadow_dy); glUniform1f(s.glass_loc.opacity, opacity);
            const GLsizei n = mesh_on(i, glass_on(i), {r.x - margin, r.y - margin, r.width + 2 * margin, r.height + 2 * margin}, true);
            attrib(s.glass_loc.pos, 3, 0); attrib(s.glass_loc.px, 2, 3);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
            glDisableVertexAttribArray(static_cast<GLuint>(s.glass_loc.px));
        };
        const std::array<float, 3> accent{s.accent[0], s.accent[1], s.accent[2]};
        for (const std::size_t i : glass_draw_order(draw_order(canvas, tiles, resolved), g.top)) {
            if (i >= s.textures.size() || i >= g.expand.size()) continue;
            const float e = std::clamp(g.expand[i], 0.f, 1.f);
            if (!g.pills && e <= 0.f) continue;
            const float level = int(i) == g.hover ? g.hover_level : kGlassIdle;
            const FRect r = glass_morph(tiles[i], e);
            shape(i, r, glass_radius(tiles[i], e), glass_look(accent, level, e), 1.f, kGlassShadowMargin);
            if (e <= 0.f) continue;
            // The grabber where the pill was, fading in with the panel.
            const FRect grab{r.x + (r.width - kGrabberW) / 2, r.y + kGrabberTop, kGrabberW, kGrabberH};
            shape(i, grab, kGrabberH / 2, grabber_look(level), std::clamp((e - 0.5f) * 2.f, 0.f, 1.f), 2.f);
            if (int(i) != g.content || !g.image || g.content_alpha <= 0.f || !s.panel_program) continue;
            const auto& im = *g.image;
            if (!im.width || !im.height || im.rgba.size() != std::size_t(im.width) * im.height * 4) continue;
            glUseProgram(s.panel_program); uniforms(s.panel_loc.view, s.panel_loc.half_fov);
            glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, s.panel_texture);
            if (g.generation != s.panel_uploaded) {
                glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(im.width), GLsizei(im.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, im.rgba.data());
                s.panel_uploaded = g.generation;
            }
            glUniform1i(s.panel_loc.tex, 0); glUniform1f(s.panel_loc.alpha, std::min(g.content_alpha, 1.f));
            // The content rides on the morphing shape (it only shows over the last quarter, when the shape is near full size).
            const auto vertices = tile_mesh(canvas, tiles[i], glass_on(i), r);
            buffer.clear();
            for (const auto& m : vertices)
                buffer.insert(buffer.end(), {m.world.x, m.world.y, m.world.z, (m.px - r.x) / r.width, (m.py - r.y) / r.height});
            attrib(s.panel_loc.pos, 3, 0); attrib(s.panel_loc.uv, 2, 3);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, GLsizei(vertices.size()));
            glDisableVertexAttribArray(static_cast<GLuint>(s.panel_loc.uv));
        }
        glDisable(GL_BLEND);
    }
    if (s.handle_desktop >= 0 && std::size_t(s.handle_desktop) < tiles.size() && s.handle_level > 0.f) {
        // Corner-resize handle: an opaque grey bracket on the desktop's own surface, over its pixels (no blending needed).
        const auto h = std::size_t(s.handle_desktop);
        const auto arms = corner_handle(tiles[h], s.handle_corner);
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));  // the tiles' uv array points into `buffer`, rewritten below
        glUseProgram(s.color_program); uniforms(s.color_view, s.color_half_fov);
        glUniform4f(s.color, s.handle_level, s.handle_level, s.handle_level, 1.f);
        for (const FRect& arm : {arms.horizontal, arms.vertical}) {
            const GLsizei n = mesh(h, arm, false);
            attrib(s.color_pos, 3, 0);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        }
    }
    // The help card, then the free pointer on top of it: the pointer clicks the card's page arrows.
    if (s.help_hud && s.help && s.help_alpha > 0.f) s.help_hud->draw(*s.help, s.help_generation, s.help_alpha, s.width, s.height, true, s.help_drop);
    if (s.free_pointer && s.free_level > 0.f) {
        // Overview free pointer: a black ring, then the grey dot, on the canvas plane (world-locked like the desktops' slots).
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        glUseProgram(s.color_program); uniforms(s.color_view, s.color_half_fov);
        for (const auto& [radius, level] : {std::pair{kFreePointerRadiusPx + kFreePointerRingPx, 0.f}, std::pair{kFreePointerRadiusPx, s.free_level}}) {
            const auto fan = canvas_disc(canvas, s.free_x, s.free_y, radius);
            buffer.clear();
            for (const auto& v : fan) buffer.insert(buffer.end(), {v.x, v.y, v.z, 0.f, 0.f});
            glUniform4f(s.color, level, level, level, 1.f);
            attrib(s.color_pos, 3, 0);
            glDrawArrays(GL_TRIANGLE_FAN, 0, GLsizei(fan.size()));
        }
    }
    if (s.hud && s.popup) s.hud->draw(*s.popup, s.popup_generation, s.popup_alpha, s.width, s.height);  // head-locked, on top
    const bool ok = glGetError() == GL_NO_ERROR && eglSwapBuffers(s.display, s.surface);
    s.draw_stat.add(now_us() - start);
    return ok;
}

bool GlCompositor::draw_scene_and_swap(const std::function<void(std::uint32_t, std::uint32_t)>& scene, const HudImage* card,
                                       std::uint64_t card_generation, float card_alpha) {
    auto& s = *impl_;
    const auto start = now_us();
    scene(s.width, s.height);
    glViewport(0, 0, static_cast<GLsizei>(s.width), static_cast<GLsizei>(s.height));
    if (card && card_alpha > 0.f) {
        if (!s.video_hud) s.video_hud = GlHud::create();
        if (s.video_hud) s.video_hud->draw(*card, card_generation, card_alpha, s.width, s.height);
    }
    if (s.hud && s.popup) s.hud->draw(*s.popup, s.popup_generation, s.popup_alpha, s.width, s.height);
    const bool ok = glGetError() == GL_NO_ERROR && eglSwapBuffers(s.display, s.surface);
    s.draw_stat.add(now_us() - start);
    return ok;
}

std::string GlCompositor::report() const {
    const auto& s = *impl_;
    bool any = false; for (const auto& i : s.imported) any |= i.current >= 0;
    return std::string("{\"path\":\"") + (any ? "gles2-dmabuf-import" : "gles2-shm-upload") + "\",\"imported_frames\":" +
           std::to_string(s.imported_frames) + ",\"vendor\":\"" + s.vendor + "\",\"renderer\":\"" + s.renderer + "\",\"version\":\"" + s.version +
           "\",\"upload\":" + s.upload_stat.json() + ",\"draw_swap\":" + s.draw_stat.json() +
           ",\"uploaded_mib\":" + std::to_string(s.uploaded_bytes >> 20) + "}";
}
}
