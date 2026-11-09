#include <nlohmann/json.hpp>
#include "xr/options_geometry.hpp"
#include "gl.hpp"
#include <wayland-client.h>
#include <wayland-egl.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include "dmabuf.hpp"
#include "environment_gl.hpp"
#include "hud_gl.hpp"
#include "stereo_gl.hpp"
#include "video_gl.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>

namespace xr {
namespace {
std::uint64_t now_us() {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
// World point (view.hpp place_point: canvas plane at z=-1 spanning the FOV, each desktop turned to face the eye, then
// per-desktop placement) -> head rotation -> perspective. A centred desktop with the default placement and identity
// rotation reproduces the head-locked NDC exactly; an off-centre one keeps its centre there and faces you.
// XR_PROJECT itself is in stereo_gl.hpp: the warp's programs share it.
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
constexpr float kGlowRadiusPx = 40.f, kGlowSigmaPx = 12.f, kGlowIntensity = 0.18f;
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
    GLint tile_stereo = -1, color_stereo = -1, glow_stereo = -1;  // this eye's offset: only ever set in 3D (XR_PROJECT)
    struct { GLint pos = -1, px = -1, view = -1, half_fov = -1, rect = -1, radius = -1, style = -1, opacity = -1, stereo = -1; } glass_loc;
    struct { GLint pos = -1, uv = -1, view = -1, half_fov = -1, tex = -1, alpha = -1, stereo = -1; } panel_loc;
    GLuint panel_texture = 0; std::uint64_t panel_uploaded = 0;
    struct Texture { GLuint id = 0; std::uint32_t width = 0, height = 0; bool valid = false; };
    std::vector<Texture> textures;
    // Zero-copy: per source, per capture slot, an EGLImage-backed texture; `current` = slot to draw (-1 = SHM).
    // `held`: the last presented frame of buffers since re-created (hold_dmabuf), drawn until the new ones deliver one.
    struct Imported { std::array<GLuint, 3> tex{}; std::array<EGLImageKHR, 3> image{EGL_NO_IMAGE_KHR, EGL_NO_IMAGE_KHR, EGL_NO_IMAGE_KHR}; int current = -1;
                      GLuint held_tex = 0; EGLImageKHR held_image = EGL_NO_IMAGE_KHR; std::uint32_t width = 0, height = 0; };
    std::vector<Imported> imported;
    GLint swizzle = -1;
    PFNEGLCREATEIMAGEKHRPROC create_image = nullptr;
    PFNEGLDESTROYIMAGEKHRPROC destroy_image = nullptr;
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target = nullptr;
    PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_modifiers = nullptr;
    std::uint64_t imported_frames = 0;
    std::unique_ptr<GlHud> video_hud;    // VR video controls card (own texture); created with the others (retried on use)
    // The background (environment_gl.hpp): compiled at setup (not on the first frame it shows); absent if its shaders failed.
    std::unique_ptr<EnvironmentPass> environment;
    float env_level = 0, env_gain = 1, env_seconds = 0; Background env_background = Background::none; Mat3 env_view{1, 0, 0, 0, 1, 0, 0, 0, 1};
    bool env_visible() const { return environment && env_level > 0.f && env_background != Background::none; }
    // Global immersive 3D (stereo_gl.hpp), all empty until set_stereo first turns it on: a 2D run never compiles its shaders.
    std::unique_ptr<StereoPass> stereo; std::string stereo_why;
    StereoSettings stereo_set;
    struct StereoSource { StereoFader fader; float gain = 0; double depth_ms = -1; };
    std::vector<StereoSource> stereo_sources;
    StereoFader stereo_scene; float stereo_b = 0, env_eye = 0;  // this frame's half baseline; the background's eye shift (NDC)
    std::uint64_t stereo_frames = 0;
    static double now_ms() { return double(now_us()) / 1000.0; }
    std::vector<EyeViewport> stereo_eyes() const {  // the two eye viewports; none in 2D or until the buffer is 3:1 wide
        if (px_w < 3 * px_h) return {};  // every 2D frame ends here
        auto eyes = eye_viewports(px_w, px_h);
        if (eyes.size() != 2) eyes.clear();
        return eyes;
    }
    void stereo_frame(const std::vector<FRect>& tiles, const Mat3& view, const Canvas& canvas, float eye_w);
    StereoBudget stereo_tile_budget(std::size_t i, const Canvas& canvas, const FRect& tile, const Mat3& view, float eye_w) const;
    bool blend_max = false;  // GL_MAX blending (ES 3 or EXT_blend_minmax): the highlight's glow over the grid stays under the cap
    void drop(Imported& i) {
        for (auto& t : i.tex) if (t) { glDeleteTextures(1, &t); t = 0; }
        for (auto& im : i.image) if (im != EGL_NO_IMAGE_KHR && destroy_image) { destroy_image(display, im); im = EGL_NO_IMAGE_KHR; }
        i.current = -1;
    }
    void drop_held(Imported& i) {
        if (i.held_tex) { glDeleteTextures(1, &i.held_tex); i.held_tex = 0; }
        if (i.held_image != EGL_NO_IMAGE_KHR && destroy_image) { destroy_image(display, i.held_image); i.held_image = EGL_NO_IMAGE_KHR; }
    }
    std::uint32_t width = 0, height = 0;
    std::uint32_t px_w = 0, px_h = 0;  // the drawn buffer (glViewport); width x height stays the layout canvas
    std::string vendor, renderer, version;
    Stat upload_stat, draw_stat;
    std::uint64_t uploaded_bytes = 0;
    std::unique_ptr<GlHud> hud;           // status pop-ups; absent if its shaders failed
    const HudImage* popup = nullptr; std::uint64_t popup_generation = 0; float popup_alpha = 0;
    std::unique_ptr<GlHud> help_hud;      // keyboard help card (own texture); absent if its shaders failed
    const HudImage* help = nullptr; std::uint64_t help_generation = 0; float help_alpha = 0, help_drop = 0;
    std::unique_ptr<GlHud> binds_hud;     // key bind panel (own texture); absent if its shaders failed
    const HudImage* binds = nullptr; std::uint64_t binds_generation = 0; float binds_alpha = 0, binds_drop = 0;
    GlassFrame glass;                                                                  // glass pills and the open panel
    bool free_pointer = false; float free_x = 0, free_y = 0, free_level = 0;           // overview free pointer
    bool free_directed = false; float free_az = 0, free_el = 0;                        // ...drawn in this direction (degrees)
    PointerLook free_look = PointerLook::plain;                                        // ...with these marks round it
    float ghost_w = 0, ghost_h = 0; int drop_target = -1;                               // window drag: ghost and target
    GLuint offscreen_fb = 0, offscreen_rb = 0;  // create_offscreen: the framebuffer drawn instead of a window
    bool setup(std::size_t sources, std::string& why);  // shaders, textures, extensions (context current)
    // Vertex data: every strip the frame draws lives in its own buffer object, rebuilt (tile_mesh / canvas_disc) only when
    // the inputs it was built from change. Head rotation is a uniform, so a still layout re-uses every buffer as it is.
    // Interleaved per vertex: world xyz, then (u, v) on the tile, canvas (px, py), or (u, v) on the rect (panel content).
    enum class Attr { tile_uv, canvas_px, rect_uv, disc };
    struct Mesh { GLuint buffer = 0; GLsizei count = 0; bool built = false; std::array<float, 21> key{}; };
    Mesh glow_mesh, border_mesh, disc_mesh[2], mark_mesh[2], ghost_mesh, drop_mesh[4];
    std::vector<Mesh> tile_meshes, glass_meshes, grab_meshes, panel_meshes, bar_meshes, cap_meshes, button_meshes;  // per desktop (caps: and corner)
    // The level bars' and corner caps' pixels (glass.hpp render_handle_atlas): one image per theme colour.
    GLuint handle_texture = 0; std::array<float, 3> handle_accent{-1, -1, -1};
    std::unique_ptr<HudFont> handle_font; bool handle_font_tried = false;  // the 3D button's label; its own instance, as each user has
    void upload_handles(const std::array<float, 3>& accent);  // renders and uploads it; a no-op for the same colour
    std::vector<GLfloat> scratch;              // staging for a rebuilt mesh (capacity kept)
    std::vector<Placement> resolved;           // this frame's placement per tile (capacity kept)
    // glass: `rect` is glass px under the desktop (view.hpp glass_mesh: fixed size whatever the desktop's distance).
    // box: with Attr::rect_uv, the part of the image the rect shows (a handle atlas cell, glass.hpp AtlasBox).
    GLsizei build(Mesh& m, const Canvas& canvas, const FRect& tile, const Placement& on, const FRect& rect, Attr attr, bool glass = false,
                  const AtlasBox* box = nullptr);
    GLsizei build_disc(Mesh& m, const Canvas& canvas, float x, float y, float radius, bool directed, float az, float el);
    GLsizei build_ghost(Mesh& m, const Canvas& canvas, float x, float y, float w, float h, bool directed, float az, float el);
    GLsizei build_marks(Mesh& m, const Canvas& canvas, float x, float y, PointerLook look, float grow, bool directed, float az, float el);
    static std::pair<float, float> dot_direction(const Canvas& c, float x, float y, bool directed, float az, float el);
    void drop_meshes() {
        auto drop = [](Mesh& m) { if (m.buffer) glDeleteBuffers(1, &m.buffer); m = {}; };
        drop(glow_mesh); drop(border_mesh); for (auto& m : disc_mesh) drop(m); for (auto& m : mark_mesh) drop(m);
        drop(ghost_mesh); for (auto& m : drop_mesh) drop(m);
        for (auto* v : {&tile_meshes, &glass_meshes, &grab_meshes, &panel_meshes, &bar_meshes, &cap_meshes, &button_meshes}) {
            for (auto& m : *v) drop(m);
            v->clear();
        }
    }
    // Per frame: the program in use (0: not known, e.g. after a GlHud draw), and the head rotation / FOV each program was
    // last given (uniforms keep their values per program, and only this class sets these programs' uniforms).
    GLuint current = 0;
    void use(GLuint p) { if (p != current) { glUseProgram(p); current = p; } }
    struct Projection { bool set = false; Mat3 view{}; float half[2] = {0, 0}; float stereo = 0; };
    Projection tile_projection, color_projection, glow_projection, glass_projection, panel_projection, warp_projection;
    // The panel texture's pixels as last uploaded. A slider drag re-renders the panel up to once per frame but changes one
    // row of it: only the rows that differ from this copy are uploaded (none if a re-render left the pixels as they were).
    std::vector<std::uint8_t> panel_pixels; std::uint32_t panel_w = 0, panel_h = 0;
    void upload_panel(const HudImage& im, std::uint64_t generation);  // binds the panel texture on unit 0; uploads if new
    bool swap() { if (surface == EGL_NO_SURFACE) { glFlush(); return true; } return eglSwapBuffers(display, surface); }
    ~Impl() {
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, surface, surface, context);
            if (offscreen_fb) glDeleteFramebuffers(1, &offscreen_fb);
            if (offscreen_rb) glDeleteRenderbuffers(1, &offscreen_rb);
            drop_meshes();
            for (auto& t : textures) if (t.id) glDeleteTextures(1, &t.id);
            for (auto& i : imported) { drop(i); drop_held(i); }
            hud.reset(); help_hud.reset(); binds_hud.reset(); environment.reset(); stereo.reset();
            if (panel_texture) glDeleteTextures(1, &panel_texture);
            if (handle_texture) glDeleteTextures(1, &handle_texture);
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
    s.width = width; s.height = height; s.px_w = width; s.px_h = height;
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
    if (!s.setup(sources, why)) return nullptr;
    return out;
}

std::unique_ptr<GlCompositor> GlCompositor::create_offscreen(std::uint32_t width, std::uint32_t height, std::size_t sources, std::string& why) {
    std::unique_ptr<GlCompositor> out(new GlCompositor());
    out->impl_ = std::make_unique<Impl>();
    auto& s = *out->impl_;
    s.width = width; s.height = height; s.px_w = width; s.px_h = height;
    s.display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    if (s.display == EGL_NO_DISPLAY || !eglInitialize(s.display, nullptr, nullptr)) { why = "egl display"; return nullptr; }
    if (!eglBindAPI(EGL_OPENGL_ES_API)) { why = "gles api"; return nullptr; }
    const EGLint config_attribs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE};
    EGLConfig config; EGLint count = 0;
    if (!eglChooseConfig(s.display, config_attribs, &config, 1, &count) || count < 1) { why = "egl config"; return nullptr; }
    const EGLint context_attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 2, EGL_NONE};
    s.context = eglCreateContext(s.display, config, EGL_NO_CONTEXT, context_attribs);
    if (s.context == EGL_NO_CONTEXT || !eglMakeCurrent(s.display, EGL_NO_SURFACE, EGL_NO_SURFACE, s.context)) { why = "egl context"; return nullptr; }
    glGenFramebuffers(1, &s.offscreen_fb); glBindFramebuffer(GL_FRAMEBUFFER, s.offscreen_fb);
    glGenRenderbuffers(1, &s.offscreen_rb); glBindRenderbuffer(GL_RENDERBUFFER, s.offscreen_rb);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, GLsizei(width), GLsizei(height));
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, s.offscreen_rb);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) { why = "framebuffer"; return nullptr; }
    if (!s.setup(sources, why)) return nullptr;
    return out;
}

bool GlCompositor::Impl::setup(std::size_t sources, std::string& why) {
    auto& s = *this;
    s.vendor = escape(reinterpret_cast<const char*>(glGetString(GL_VENDOR)));
    s.renderer = escape(reinterpret_cast<const char*>(glGetString(GL_RENDERER)));
    s.version = escape(reinterpret_cast<const char*>(glGetString(GL_VERSION)));
    GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vs || !fs) { why = "shader compile"; return false; }
    s.program = glCreateProgram(); glAttachShader(s.program, vs); glAttachShader(s.program, fs); glLinkProgram(s.program);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(s.program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return false; }
    GLuint cvs = compile(GL_VERTEX_SHADER, kColorVertex), cfs = compile(GL_FRAGMENT_SHADER, kColorFragment);
    if (!cvs || !cfs) { why = "shader compile"; return false; }
    s.color_program = glCreateProgram(); glAttachShader(s.color_program, cvs); glAttachShader(s.color_program, cfs); glLinkProgram(s.color_program);
    glDeleteShader(cvs); glDeleteShader(cfs);
    glGetProgramiv(s.color_program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return false; }
    s.color_pos = glGetAttribLocation(s.color_program, "pos"); s.color = glGetUniformLocation(s.color_program, "color");
    s.color_view = glGetUniformLocation(s.color_program, "view"); s.color_half_fov = glGetUniformLocation(s.color_program, "half_fov");
    s.color_stereo = glGetUniformLocation(s.color_program, "stereo");
    GLuint gvs = compile(GL_VERTEX_SHADER, kGlowVertex), gfs = compile(GL_FRAGMENT_SHADER, kGlowFragment);
    if (!gvs || !gfs) { why = "shader compile"; return false; }
    s.glow_program = glCreateProgram(); glAttachShader(s.glow_program, gvs); glAttachShader(s.glow_program, gfs); glLinkProgram(s.glow_program);
    glDeleteShader(gvs); glDeleteShader(gfs);
    glGetProgramiv(s.glow_program, GL_LINK_STATUS, &linked);
    if (!linked) { why = "shader link"; return false; }
    s.glow_pos = glGetAttribLocation(s.glow_program, "pos"); s.glow_px = glGetAttribLocation(s.glow_program, "px");
    s.glow_rect = glGetUniformLocation(s.glow_program, "rect"); s.glow_color = glGetUniformLocation(s.glow_program, "color");
    s.glow_falloff = glGetUniformLocation(s.glow_program, "falloff");
    s.glow_view = glGetUniformLocation(s.glow_program, "view"); s.glow_half_fov = glGetUniformLocation(s.glow_program, "half_fov");
    s.glow_stereo = glGetUniformLocation(s.glow_program, "stereo");
    s.view = glGetUniformLocation(s.program, "view"); s.half_fov = glGetUniformLocation(s.program, "half_fov");
    s.tile_stereo = glGetUniformLocation(s.program, "stereo");
    s.pos = glGetAttribLocation(s.program, "pos"); s.uv = glGetAttribLocation(s.program, "uv");
    s.sampler = glGetUniformLocation(s.program, "tex");
    s.swizzle = glGetUniformLocation(s.program, "swizzle");
    s.hud = GlHud::create();  // optional: composition works without pop-ups
    s.help_hud = GlHud::create();
    s.binds_hud = GlHud::create();
    s.video_hud = GlHud::create();  // now, not on the first video frame (a shader compile there costs that frame)
    s.environment = EnvironmentPass::create();  // optional, and now for the same reason; the run works without the grid
    {
        const char* gl_ext = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
        s.blend_max = std::strncmp(s.version.c_str(), "OpenGL ES 3", 11) == 0 || (gl_ext && std::strstr(gl_ext, "GL_EXT_blend_minmax"));
    }
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
        g.rect = glGetUniformLocation(p, "rect"); g.radius = glGetUniformLocation(p, "radius");
        g.style = glGetUniformLocation(p, "style"); g.opacity = glGetUniformLocation(p, "opacity");  // style: glass_style()
        g.stereo = glGetUniformLocation(p, "stereo");
    }
    if ((s.panel_program = link(kVertex, kPanelFragment))) {
        auto& g = s.panel_loc; const GLuint p = s.panel_program;
        g.pos = glGetAttribLocation(p, "pos"); g.uv = glGetAttribLocation(p, "uv");
        g.view = glGetUniformLocation(p, "view"); g.half_fov = glGetUniformLocation(p, "half_fov");
        g.tex = glGetUniformLocation(p, "tex"); g.alpha = glGetUniformLocation(p, "alpha");
        g.stereo = glGetUniformLocation(p, "stereo");
        glGenTextures(1, &s.panel_texture); glBindTexture(GL_TEXTURE_2D, s.panel_texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glGenTextures(1, &s.handle_texture); glBindTexture(GL_TEXTURE_2D, s.handle_texture);
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
    if (glGetError() != GL_NO_ERROR) { why = "gl setup"; return false; }
    return true;
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
    im.tex[slot] = tex; im.image[slot] = image; im.width = b.width; im.height = b.height;
    return true;
}

void GlCompositor::present_dmabuf(std::size_t source, int slot) {
    auto& s = *impl_;
    if (source < s.imported.size() && slot >= 0 && slot < 3 && s.imported[source].tex[std::size_t(slot)]) {
        s.imported[source].current = slot; ++s.imported_frames;
        s.drop_held(s.imported[source]);
    }
}

void GlCompositor::set_popup(const HudImage* image, std::uint64_t generation, float alpha) {
    impl_->popup = image; impl_->popup_generation = generation; impl_->popup_alpha = alpha;
}

void GlCompositor::set_help(const HudImage* image, std::uint64_t generation, float alpha, float drop_px) {
    impl_->help = image; impl_->help_generation = generation; impl_->help_alpha = alpha; impl_->help_drop = drop_px;
}

void GlCompositor::set_binds(const HudImage* image, std::uint64_t generation, float alpha, float drop_px) {
    impl_->binds = image; impl_->binds_generation = generation; impl_->binds_alpha = alpha; impl_->binds_drop = drop_px;
}

void GlCompositor::set_glass(const GlassFrame& frame) { impl_->glass = frame; }

void GlCompositor::set_free_pointer(bool visible, float x, float y, float level, bool directed, float az, float el, PointerLook look) {
    impl_->free_pointer = visible; impl_->free_x = x; impl_->free_y = y; impl_->free_level = std::min(level, kFreePointerHeldLevel);
    impl_->free_directed = directed; impl_->free_az = az; impl_->free_el = el; impl_->free_look = look;
}

void GlCompositor::set_window_drag(float w, float h, int target) {
    impl_->ghost_w = w; impl_->ghost_h = h; impl_->drop_target = target;
}

void GlCompositor::drop_dmabuf(std::size_t source) { if (source < impl_->imported.size()) impl_->drop(impl_->imported[source]); }

bool GlCompositor::hold_dmabuf(std::size_t source) {
    auto& s = *impl_;
    if (source >= s.imported.size()) return false;
    auto& im = s.imported[source];
    if (im.current < 0 || !im.tex[std::size_t(im.current)]) { s.drop(im); return im.held_tex != 0; }
    s.drop_held(im);
    const auto c = std::size_t(im.current);
    im.held_tex = im.tex[c]; im.held_image = im.image[c]; im.tex[c] = 0; im.image[c] = EGL_NO_IMAGE_KHR;
    s.drop(im);
    return true;
}

void GlCompositor::release_held(std::size_t source) { if (source < impl_->imported.size()) impl_->drop_held(impl_->imported[source]); }

bool GlCompositor::holding(std::size_t source) const { return source < impl_->imported.size() && impl_->imported[source].held_tex; }

void GlCompositor::insert_source(std::size_t at) {
    auto& s = *impl_;
    at = std::min(at, s.textures.size());
    Impl::Texture t;
    glGenTextures(1, &t.id); glBindTexture(GL_TEXTURE_2D, t.id);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    s.textures.insert(s.textures.begin() + std::ptrdiff_t(at), t);
    s.imported.insert(s.imported.begin() + std::ptrdiff_t(std::min(at, s.imported.size())), Impl::Imported{});
    if (s.stereo) s.stereo->insert_source(at);
    s.stereo_sources.insert(s.stereo_sources.begin() + std::ptrdiff_t(std::min(at, s.stereo_sources.size())), Impl::StereoSource{});
}

void GlCompositor::remove_source(std::size_t at) {
    auto& s = *impl_;
    if (at < s.imported.size()) { s.drop(s.imported[at]); s.drop_held(s.imported[at]); s.imported.erase(s.imported.begin() + std::ptrdiff_t(at)); }
    if (at < s.textures.size()) {
        if (s.textures[at].id) glDeleteTextures(1, &s.textures[at].id);
        s.textures.erase(s.textures.begin() + std::ptrdiff_t(at));
    }
    if (s.stereo) s.stereo->remove_source(at);
    if (at < s.stereo_sources.size()) s.stereo_sources.erase(s.stereo_sources.begin() + std::ptrdiff_t(at));
}

void GlCompositor::set_stereo(const StereoSettings& settings) {
    auto& s = *impl_;
    const bool strength_changed = s.stereo_set.params.strength != stereo_clamped(settings.params).strength ||
                                  s.stereo_set.strengths != settings.strengths;
    s.stereo_set = settings; s.stereo_set.params = stereo_clamped(settings.params);
    s.stereo_set.half_baseline = std::isfinite(settings.half_baseline) ? std::clamp(settings.half_baseline, 0.f, 2.f * kStereoHalfBaseline) : kStereoHalfBaseline;
    if (settings.enabled && strength_changed && s.stereo) {
        for (std::size_t i = 0; i < s.stereo_sources.size(); ++i) if (auto frame = s.stereo->cached_frame(i)) {
            // A still desktop produces no new inference. Rebuild from its owned raw
            // upload immediately, without granting stale depth a new freshness timestamp.
            const double age = s.stereo_sources[i].depth_ms;
            frame->reset_history = true;
            set_depth(i, *frame);
            s.stereo_sources[i].depth_ms = age;
        }
    }
    if (!settings.enabled || s.stereo || !s.stereo_why.empty()) return;
    s.stereo = StereoPass::create(s.pos, s.uv, s.color_pos, s.stereo_why);  // the shaders, now and not on a later frame
    if (s.stereo) s.stereo->resize_sources(s.textures.size());
    s.current = 0;
}

bool GlCompositor::stereo_available() const { return impl_->stereo != nullptr; }

bool GlCompositor::set_depth(std::size_t source, const DepthInput& frame) {
    auto& s = *impl_;
    if (!s.stereo || source >= s.textures.size()) return false;
    if (s.stereo_sources.size() < s.textures.size()) s.stereo_sources.resize(s.textures.size());
    const auto& t = s.textures[source];
    const auto& im = s.imported[source];
    const bool imported = im.current >= 0 || im.held_tex;
    const GLuint colour = im.current >= 0 ? im.tex[std::size_t(im.current)] : im.held_tex ? im.held_tex : t.valid ? t.id : 0;
    const std::uint32_t w = imported ? im.width : t.width, h = imported ? im.height : t.height;
    if (!colour || !w || !h) return false;
    auto& state = s.stereo_sources[source];
    const double now = Impl::now_ms();
    auto params = s.stereo_set.params;
    if (source < s.stereo_set.strengths.size() && s.stereo_set.strengths[source] >= 0) params.strength = s.stereo_set.strengths[source] / 100.f;
    const bool ok = s.stereo->update(source, frame, colour, w, h, params, float(w) / float(h), !frame.reset_history && state.depth_ms >= 0 ? float(now - state.depth_ms) : 0.f);
    if (ok) state.depth_ms = now;
    s.current = 0;
    return ok;
}

void GlCompositor::keep_depth(std::size_t source) {
    auto& s = *impl_;
    if (source < s.stereo_sources.size() && s.stereo_sources[source].depth_ms >= 0) s.stereo_sources[source].depth_ms = Impl::now_ms();
}

GlCompositor::SourceTexture GlCompositor::source_texture(std::size_t source) const {
    const auto& s = *impl_;
    if (source >= s.textures.size()) return {};
    const auto& im = s.imported[source];
    if (im.current >= 0) return {im.tex[std::size_t(im.current)], im.width, im.height, false};
    if (im.held_tex) return {im.held_tex, im.width, im.height, false};
    return s.textures[source].valid ? SourceTexture{s.textures[source].id, s.textures[source].width, s.textures[source].height, true} : SourceTexture{};
}

// Once per frame, before the eyes: the fades, then the baseline that keeps every desktop's plane inside the limits.
void GlCompositor::Impl::stereo_frame(const std::vector<FRect>& tiles, const Mat3& rotation, const Canvas& canvas, float eye_w) {
    if (stereo_sources.size() < textures.size()) stereo_sources.resize(textures.size());
    const double now = now_ms();
    for (std::size_t i = 0; i < stereo_sources.size(); ++i) {
        auto& state = stereo_sources[i];
        const bool wanted = stereo_set.enabled && (i >= stereo_set.desktops.size() || stereo_set.desktops[i]);
        const bool fresh = stereo && stereo->ready(i) && state.depth_ms >= 0 && now - state.depth_ms < kStereoStaleMs;
        state.gain = state.fader.update(now, wanted && fresh);
    }
    const float scene = stereo_scene.update(now, stereo_set.enabled);
    const auto limits = stereo_limits(stereo_set.params, eye_w, canvas.half_w);
    std::vector<float> depths;
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        const auto c = place_point(canvas, tiles[i], resolved[i], tiles[i].x + tiles[i].width / 2, tiles[i].y + tiles[i].height / 2);
        depths.push_back(apply(rotation, {c.x, c.y, c.z})[2]);
    }
    stereo_b = stereo_safe_baseline(scene * stereo_set.half_baseline, depths, limits, eye_w, canvas.half_w);
    ++stereo_frames;
}

StereoBudget GlCompositor::Impl::stereo_tile_budget(std::size_t i, const Canvas& canvas, const FRect& tile, const Mat3& rotation, float eye_w) const {
    const auto c = place_point(canvas, tile, resolved[i], tile.x + tile.width / 2, tile.y + tile.height / 2);
    const float plane = stereo_plane_disparity(stereo_b, apply(rotation, {c.x, c.y, c.z})[2], eye_w, canvas.half_w);
    const float gain = i < stereo_sources.size() ? stereo_sources[i].gain : 0.f;
    return stereo_budget(stereo_limits(stereo_set.params, eye_w, canvas.half_w), plane, stereo_tile_scale(tile.width, canvas.view_w, resolved[i].distance), gain);
}

std::size_t GlCompositor::sources() const { return impl_->textures.size(); }

void GlCompositor::set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b) {
    impl_->accent[0] = r / 255.f; impl_->accent[1] = g / 255.f; impl_->accent[2] = b / 255.f;
}

bool GlCompositor::set_environment(float level, Background background, float brightness, float seconds, const Mat3& world) {
    auto& s = *impl_;
    s.env_level = std::isfinite(level) ? std::min(level, 1.f) : 0.f; s.env_background = background;
    s.env_gain = brightness / 100.f; s.env_seconds = seconds; s.env_view = world;
    return s.environment != nullptr;
}

bool GlCompositor::draw_environment(float half_w, float half_h) {
    auto& s = *impl_;
    if (!s.env_visible()) return false;
    s.environment->draw(s.env_background, s.env_view, half_w, half_h, s.env_level, s.env_gain,
                        environment_line_color({s.accent[0], s.accent[1], s.accent[2]}), s.env_seconds, s.env_eye);
    s.current = s.environment->program(s.env_background);
    return true;
}

void GlCompositor::resize(std::uint32_t width, std::uint32_t height, std::uint32_t px_width, std::uint32_t px_height) {
    auto& s = *impl_;
    if (!px_width || !px_height) { px_width = width; px_height = height; }
    s.width = width; s.height = height;
    if (px_width == s.px_w && px_height == s.px_h) return;
    s.px_w = px_width; s.px_h = px_height;
    if (s.window) wl_egl_window_resize(s.window, static_cast<int>(px_width), static_cast<int>(px_height), 0, 0);
    else if (s.offscreen_rb) { glBindRenderbuffer(GL_RENDERBUFFER, s.offscreen_rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, GLsizei(px_width), GLsizei(px_height)); }
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
    if (t.valid && source < s.imported.size()) s.drop_held(s.imported[source]);  // the new frame replaces a held one
    s.uploaded_bytes += std::uint64_t(stride) * height;
    s.upload_stat.add(now_us() - start);
    return t.valid;
}

static_assert(sizeof(Canvas) == 4 * sizeof(float) && sizeof(FRect) == 4 * sizeof(float) && sizeof(Placement) == 5 * sizeof(float),
              "a mesh key holds every field of tile_mesh's inputs: extend it with them");
GLsizei GlCompositor::Impl::build(Mesh& m, const Canvas& c, const FRect& tile, const Placement& on, const FRect& rect, Attr attr,
                                  bool glass, const AtlasBox* box) {
    const std::array<float, 21> key{c.view_w, c.view_h, c.half_w, c.half_h, tile.x, tile.y, tile.width, tile.height, on.x, on.y,
                                    on.distance, on.curvature, rect.x, rect.y, rect.width, rect.height,
                                    float(int(attr)), on.roll, glass ? 1.f : 0.f, box ? box->u0 : -1.f, box ? box->v0 : -1.f};  // a cell, by its corner
    if (!m.buffer) glGenBuffers(1, &m.buffer);
    glBindBuffer(GL_ARRAY_BUFFER, m.buffer);
    if (m.built && std::memcmp(m.key.data(), key.data(), sizeof key) == 0) return m.count;  // bitwise: same inputs, same mesh
    const auto vertices = glass ? glass_mesh(c, tile, on, rect) : tile_mesh(c, tile, on, rect);
    scratch.clear();
    for (const auto& v : vertices) {
        float a = v.u, b = v.v;
        if (attr == Attr::canvas_px) { a = v.px; b = v.py; }
        else if (attr == Attr::rect_uv) {
            a = (v.px - rect.x) / rect.width; b = (v.py - rect.y) / rect.height;
            if (box) { a = box->u0 + a * (box->u1 - box->u0); b = box->v0 + b * (box->v1 - box->v0); }
        }
        scratch.insert(scratch.end(), {v.world.x, v.world.y, v.world.z, a, b});
    }
    glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(scratch.size() * sizeof(GLfloat)), scratch.data(), GL_DYNAMIC_DRAW);
    m.key = key; m.count = GLsizei(vertices.size()); m.built = true;
    return m.count;
}

GLsizei GlCompositor::Impl::build_disc(Mesh& m, const Canvas& c, float x, float y, float radius, bool directed, float az, float el) {
    const std::array<float, 21> key{c.view_w, c.view_h, c.half_w, c.half_h, x, y, radius, float(int(Attr::disc)), directed ? 1.f : 0.f,
                                    az, el};
    if (!m.buffer) glGenBuffers(1, &m.buffer);
    glBindBuffer(GL_ARRAY_BUFFER, m.buffer);
    if (m.built && std::memcmp(m.key.data(), key.data(), sizeof key) == 0) return m.count;
    const auto fan = directed ? direction_disc(c, x, y, az * float(M_PI) / 180.f, el * float(M_PI) / 180.f, radius)
                              : canvas_disc(c, x, y, radius);
    scratch.clear();
    for (const auto& v : fan) scratch.insert(scratch.end(), {v.x, v.y, v.z});
    glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(scratch.size() * sizeof(GLfloat)), scratch.data(), GL_DYNAMIC_DRAW);
    m.key = key; m.count = GLsizei(fan.size()); m.built = true;
    return m.count;
}

// The dot's direction in radians. Undirected: its own plane point's (direction_disc is then canvas_disc exactly).
std::pair<float, float> GlCompositor::Impl::dot_direction(const Canvas& c, float x, float y, bool directed, float az, float el) {
    float a = az * float(M_PI) / 180.f, e = el * float(M_PI) / 180.f;
    if (!directed && c.view_w > 0 && c.view_h > 0) {
        const double wx = (2.0 * std::clamp(x, 0.f, c.view_w) / c.view_w - 1) * c.half_w, wy = (1 - 2.0 * std::clamp(y, 0.f, c.view_h) / c.view_h) * c.half_h;
        a = float(std::atan2(wx, 1.0)); e = float(std::atan2(wy, std::hypot(wx, 1.0)));
    }
    return {a, e};
}

GLsizei GlCompositor::Impl::build_marks(Mesh& m, const Canvas& c, float x, float y, PointerLook look, float grow, bool directed, float az, float el) {
    const std::array<float, 21> key{c.view_w, c.view_h, c.half_w, c.half_h, x, y, float(int(look)), grow, 13.f, directed ? 1.f : 0.f, az, el};
    if (!m.buffer) glGenBuffers(1, &m.buffer);
    glBindBuffer(GL_ARRAY_BUFFER, m.buffer);
    if (m.built && std::memcmp(m.key.data(), key.data(), sizeof key) == 0) return m.count;
    const auto [a, e] = dot_direction(c, x, y, directed, az, el);
    const auto tris = direction_marks(c, x, y, a, e, look, grow);
    scratch.clear();
    for (const auto& v : tris) scratch.insert(scratch.end(), {v.x, v.y, v.z});
    glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(scratch.size() * sizeof(GLfloat)), scratch.data(), GL_DYNAMIC_DRAW);
    m.key = key; m.count = GLsizei(tris.size()); m.built = true;
    return m.count;
}

GLsizei GlCompositor::Impl::build_ghost(Mesh& m, const Canvas& c, float x, float y, float w, float h, bool directed, float az, float el) {
    const std::array<float, 21> key{c.view_w, c.view_h, c.half_w, c.half_h, x, y, w, h, 11.f, directed ? 1.f : 0.f, az, el};
    if (!m.buffer) glGenBuffers(1, &m.buffer);
    glBindBuffer(GL_ARRAY_BUFFER, m.buffer);
    if (m.built && std::memcmp(m.key.data(), key.data(), sizeof key) == 0) return m.count;
    const auto [a, e] = dot_direction(c, x, y, directed, az, el);
    const auto strip = direction_outline(c, x, y, a, e, w, h, kGhostThicknessPx);
    scratch.clear();
    for (const auto& v : strip) scratch.insert(scratch.end(), {v.x, v.y, v.z});
    glBufferData(GL_ARRAY_BUFFER, GLsizeiptr(scratch.size() * sizeof(GLfloat)), scratch.data(), GL_DYNAMIC_DRAW);
    m.key = key; m.count = GLsizei(strip.size()); m.built = true;
    return m.count;
}

void GlCompositor::Impl::upload_handles(const std::array<float, 3>& colour) {
    if (colour == handle_accent) return;
    handle_accent = colour;
    if (!handle_font_tried) {  // once: a theme change never looks it up again. None: the 3D is the pixel font's
        handle_font_tried = true;
        std::string why;
        handle_font = HudFont::load(kHudFontFamily, why);
    }
    const HudImage atlas = render_handle_atlas(colour, handle_font.get());
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, handle_texture);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(atlas.width), GLsizei(atlas.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, atlas.rgba.data());
}

void GlCompositor::Impl::upload_panel(const HudImage& im, std::uint64_t generation) {
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, panel_texture);  // sampler `tex` = unit 0 (link-time value)
    if (generation == panel_uploaded) return;
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    const std::size_t row = std::size_t(im.width) * 4;
    if (im.width == panel_w && im.height == panel_h && panel_pixels.size() == im.rgba.size()) {
        std::uint32_t top = 0, bottom = im.height;  // the changed rows: [top, bottom)
        while (top < bottom && !std::memcmp(&im.rgba[top * row], &panel_pixels[top * row], row)) ++top;
        while (bottom > top && !std::memcmp(&im.rgba[(bottom - 1) * row], &panel_pixels[(bottom - 1) * row], row)) --bottom;
        if (top < bottom) {
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, GLint(top), GLsizei(im.width), GLsizei(bottom - top), GL_RGBA, GL_UNSIGNED_BYTE, &im.rgba[top * row]);
            std::memcpy(&panel_pixels[top * row], &im.rgba[top * row], (bottom - top) * row);
        }
    } else {
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(im.width), GLsizei(im.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, im.rgba.data());
        panel_pixels = im.rgba; panel_w = im.width; panel_h = im.height;
    }
    panel_uploaded = generation;
}

bool GlCompositor::draw_and_swap(const std::vector<FRect>& tiles, const std::vector<Placement>& placements, int highlight,
                                 float highlight_alpha, const Mat3& view, float half_w, float half_h, const PanLayers* pan) {
    auto& s = *impl_;
    const auto start = now_us();
    const auto eyes = s.stereo_eyes();
    if (eyes.empty()) {
        s.env_eye = 0;
        draw_view(tiles, placements, highlight, highlight_alpha, view, half_w, half_h, nullptr, pan);
    } else {
        glEnable(GL_SCISSOR_TEST);
        for (const auto& e : eyes) {
            glScissor(GLint(e.x), GLint(e.y), GLsizei(e.width), GLsizei(e.height));
            draw_view(tiles, placements, highlight, highlight_alpha, view, half_w, half_h, &e, pan);
        }
        glDisable(GL_SCISSOR_TEST);
        glViewport(0, 0, GLsizei(s.px_w), GLsizei(s.px_h));
    }
    const bool ok = glGetError() == GL_NO_ERROR && s.swap();
    s.draw_stat.add(now_us() - start);
    return ok;
}

void GlCompositor::draw_view(const std::vector<FRect>& tiles, const std::vector<Placement>& placements, int highlight,
                             float highlight_alpha, const Mat3& view, float half_w, float half_h, const EyeViewport* eye, const PanLayers* pan) {
    auto& s = *impl_;
    const Mat3 column_major = transpose(view);  // GLES2 has no transpose flag
    const float half[2] = {half_w, half_h};
    s.current = 0;  // whatever ran since the last frame (a video, GlHud) may have changed the program
    // Use program `p`; give it this frame's head rotation and FOV unless it still has them from an earlier draw.
    float eye_offset = 0;  // this eye's stereo.x; 0 in 2D, and no program then gets a call for it
    auto program = [&](GLuint p, Impl::Projection& last, GLint view_loc, GLint fov_loc, GLint stereo_loc) {
        s.use(p);
        if (last.stereo != eye_offset) { glUniform2f(stereo_loc, eye_offset, 0.f); last.stereo = eye_offset; }
        if (last.set && std::memcmp(last.view.data(), column_major.data(), sizeof column_major) == 0 &&
            std::memcmp(last.half, half, sizeof half) == 0) return;
        glUniformMatrix3fv(view_loc, 1, GL_FALSE, column_major.data()); glUniform2f(fov_loc, half_w, half_h);
        last.set = true; last.view = column_major; last.half[0] = half_w; last.half[1] = half_h;
    };
    const Canvas canvas{float(s.width), float(s.height), half_w, half_h};
    s.resolved.resize(tiles.size());
    for (std::size_t i = 0; i < tiles.size(); ++i) s.resolved[i] = i < placements.size() ? placements[i] : Placement{};
    const float eye_w = eye ? float(eye->width) : float(s.px_w);
    if (eye) {
        if (eye->eye == 0) s.stereo_frame(tiles, view, canvas, eye_w);
        eye_offset = stereo_side(eye->eye) * s.stereo_b;
        s.env_eye = stereo_side(eye->eye) * stereo_infinity_shift(s.stereo_b, stereo_limits(s.stereo_set.params, eye_w, half_w), eye_w, half_w);
    }
    for (auto* v : {&s.tile_meshes, &s.glass_meshes, &s.grab_meshes, &s.panel_meshes})
        if (v->size() < tiles.size()) v->resize(tiles.size());
    if (s.bar_meshes.size() < tiles.size()) s.bar_meshes.resize(tiles.size());
    if (s.button_meshes.size() < 2 * tiles.size()) s.button_meshes.resize(2 * tiles.size());
    if (s.cap_meshes.size() < 4 * tiles.size()) s.cap_meshes.resize(4 * tiles.size());  // 4 * desktop + corner
    // A vertex attribute from the mesh buffer bound by build(): `size` floats at `offset` in vertices of `stride` floats.
    auto attrib = [](GLint loc, int size, int stride, int offset) {
        glVertexAttribPointer(static_cast<GLuint>(loc), size, GL_FLOAT, GL_FALSE, stride * GLsizei(sizeof(GLfloat)),
                              reinterpret_cast<const void*>(std::uintptr_t(offset) * sizeof(GLfloat)));
        glEnableVertexAttribArray(static_cast<GLuint>(loc));
    };
    if (eye) glViewport(GLint(eye->x), GLint(eye->y), GLsizei(eye->width), GLsizei(eye->height));
    else glViewport(0, 0, static_cast<GLsizei>(s.px_w), static_cast<GLsizei>(s.px_h));
    if (s.glass_program && s.panel_program && s.glass.content >= 0 && s.glass.image && s.glass.generation != s.panel_uploaded) {
        // A new panel image is uploaded before anything is drawn (the driver flushes queued work before a large upload
        // from client memory), under exactly the conditions on which the glass loop below draws it.
        const auto& g = s.glass;
        const auto c = std::size_t(g.content);
        const auto& im = *g.image;
        if (c < tiles.size() && c < s.textures.size() && c < g.expand.size() && !(std::clamp(g.expand[c], 0.f, 1.f) <= 0.f) &&
            !(g.content_alpha <= 0.f) && im.width && im.height && im.rgba.size() == std::size_t(im.width) * im.height * 4)
            s.upload_panel(im, g.generation);
    }
    const std::array<float, 3> accent{s.accent[0], s.accent[1], s.accent[2]};
    const bool handles = (s.glass.pills || highlight_alpha < .5f) && s.panel_program && s.handle_texture;
    if (handles) s.upload_handles(accent);  // (the same reason: only on a theme change)
    // The background covers every pixel in place of the clear; with none (or level 0) the frame is drawn exactly as without it.
    const bool environment = draw_environment(half_w, half_h);
    if (!environment) { glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT); }
    auto frame_of = [&](std::size_t h, float highlight_alpha) {
        // Drawn first over black, so no blending is needed: glow, then border, then the tiles (back to front) cover
        // the interior. The frame lies on the highlighted desktop's own surface (same placement and curvature). Over the
        // background the glow is the brighter of the two and the border is laid over it (premultiplied), so neither blacks out a halo.
        const auto& t = tiles[h];
        const FRect framed{t.x - kBorderPx, t.y - kBorderPx, t.width + 2 * kBorderPx, t.height + 2 * kBorderPx};
        const FRect halo{framed.x - kGlowRadiusPx, framed.y - kGlowRadiusPx, framed.width + 2 * kGlowRadiusPx, framed.height + 2 * kGlowRadiusPx};
        if (environment) {  // the glow over the grid: the brighter of the two (else added)
            glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE);
            if (s.blend_max) glBlendEquation(GL_MAX_EXT);
        }
        program(s.glow_program, s.glow_projection, s.glow_view, s.glow_half_fov, s.glow_stereo);
        glUniform4f(s.glow_rect, framed.x + framed.width / 2, framed.y + framed.height / 2, framed.width / 2, framed.height / 2);
        glUniform3f(s.glow_color, s.accent[0], s.accent[1], s.accent[2]);
        glUniform2f(s.glow_falloff, kGlowIntensity * highlight_alpha, kGlowSigmaPx);
        GLsizei n = s.build(s.glow_mesh, canvas, t, s.resolved[h], halo, Impl::Attr::canvas_px);
        attrib(s.glow_pos, 3, 5, 0); attrib(s.glow_px, 2, 5, 3);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        glDisableVertexAttribArray(static_cast<GLuint>(s.glow_px));
        program(s.color_program, s.color_projection, s.color_view, s.color_half_fov, s.color_stereo);
        if (environment) { if (s.blend_max) glBlendEquation(GL_FUNC_ADD); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA); }
        glUniform4f(s.color, s.accent[0] * highlight_alpha, s.accent[1] * highlight_alpha, s.accent[2] * highlight_alpha,
                    environment ? highlight_alpha : 1.f);  // over black (or premultiplied over the grid)
        n = s.build(s.border_mesh, canvas, t, s.resolved[h], framed, Impl::Attr::tile_uv);
        attrib(s.color_pos, 3, 5, 0);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        if (environment) glDisable(GL_BLEND);
    };
    if (pan && pan->left < tiles.size() && pan->left_alpha > 0.f) frame_of(pan->left, pan->left_alpha);
    if (highlight >= 0 && std::size_t(highlight) < tiles.size() && highlight_alpha > 0.f) frame_of(std::size_t(highlight), highlight_alpha);
    // Zoomed in (highlight_alpha = 1 - zoom), the zoomed desktop is drawn on top of any desktop dragged nearer. An edge pan's two
    // are on top throughout.
    auto order = pan ? pan_draw_order(draw_order(canvas, tiles, s.resolved), *pan)
                 : highlight >= 0 ? zoom_draw_order(draw_order(canvas, tiles, s.resolved), 1.f - highlight_alpha, std::size_t(highlight))
                                  : draw_order(canvas, tiles, s.resolved);
    const auto& g = s.glass;
    auto maximize_image = [&](std::size_t i) {
        auto image=[&](const FRect& r,const AtlasBox& box){const GLsizei n=s.build(s.bar_meshes[i],canvas,tiles[i],s.resolved[i],r,Impl::Attr::rect_uv,false,&box);attrib(s.panel_loc.pos,3,5,0);attrib(s.panel_loc.uv,2,5,3);glDrawArrays(GL_TRIANGLE_STRIP,0,n);};
        const FRect r=options_maximize(tiles[i]);
        bool hot=false;
        if(s.free_pointer){auto [a,e]=Impl::dot_direction(canvas,s.free_x,s.free_y,s.free_directed,s.free_az,s.free_el);Vec3 ray{std::cos(e)*std::sin(a),std::sin(e),-std::cos(e)*std::cos(a)};
          auto hit=options_maximize_ray(canvas,tiles[i],s.resolved[i],ray);hot=bool(hit);
          if(hit)for(std::size_t j=0;j<tiles.size();++j){auto block=stereo_surface_hit(canvas,tiles[j],s.resolved[j],ray);if(block && std::sqrt(block->world.x*block->world.x+block->world.y*block->world.y+block->world.z*block->world.z)<*hit)hot=false;}
        }
        image(r,options_icon_box(hot));
        if(hot){float scale=level_bar_scale(tiles[i]);image({r.x,r.y-24*scale,84*scale,20*scale},options_hint_box());}
    };
    if (handles) {
        // Level bars and corner caps: drawn before every desktop (over the border and glow, back to front), so any desktop's
        // content covers them and the glass goes over them: they show only where nothing else is, which is where the
        // supervisor lets them take a press (free_pointer.hit: never on a desktop's content, a corner's zone or a pill).
        // Tile px on the desktop's own live surface (s.resolved), never a held panel's press-time one (GlassFrame::surface):
        // a resize, by a corner or by the Distance slider, moves and scales them with the desktop. In the theme's M3 colours
        // (the atlas, following the live accent), blended for their anti-aliased edges; only what the dot is on or holds
        // lights up.
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        program(s.panel_program, s.panel_projection, s.panel_loc.view, s.panel_loc.half_fov, s.panel_loc.stereo);
        glUniform1f(s.panel_loc.alpha, 1.f);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, s.handle_texture);  // sampler `tex` = unit 0 (link-time value)
        auto image = [&](Impl::Mesh& mesh, std::size_t i, const FRect& r, const AtlasBox& box, bool glass_space = false) {
            const GLsizei n = s.build(mesh, canvas, tiles[i], s.resolved[i], r, Impl::Attr::rect_uv, glass_space, &box);
            attrib(s.panel_loc.pos, 3, 5, 0); attrib(s.panel_loc.uv, 2, 5, 3);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        };
        for (const std::size_t i : order) {
            if (i >= s.textures.size() || i >= g.bars.size()) continue;
            if(g.pills && g.bars[i])maximize_image(i);
            if(!g.pills || !g.bars[i])continue;
            for (std::uint32_t corner = 0; corner < 4; ++corner) {
                const bool hot = int(i) == g.cap_hover && corner == g.cap_corner;
                image(s.cap_meshes[4 * i + corner], i, corner_cap_rect(tiles[i], corner), corner_cap_box(corner, hot, hot && g.cap_active));
            }
            image(s.bar_meshes[i],i,options_gear(tiles[i]),options_gear_box(),true);
            const bool hot = int(i) == g.bar_hover;
            image(s.bar_meshes[i], i, level_bar_rect(tiles[i]), level_bar_box(hot ? g.bar_segment : LevelSegment::none, hot && g.bar_active));
            const FRect main = immersive_main_rect(tiles[i]), tail = immersive_tail_rect(tiles[i]);  // the 3D button, joined to the bar
            const bool on_button = int(i) == g.immersive_hover;
            if (main.width > 0) {
                image(s.button_meshes[2 * i], i, main, immersive_main_box(i < g.immersive.size() && g.immersive[i], on_button && !g.immersive_tail));
                image(s.button_meshes[2 * i + 1], i, tail, immersive_tail_box(g.immersive_global, on_button && g.immersive_tail));
            }
        }
        glDisableVertexAttribArray(static_cast<GLuint>(s.panel_loc.uv));
        glDisable(GL_BLEND);
    }
    program(s.program, s.tile_projection, s.view, s.half_fov, s.tile_stereo); glActiveTexture(GL_TEXTURE0);  // sampler `tex` = unit 0 (its link-time value)
    float swizzle = -1;  // the value set this frame (-1: none yet)
    bool warping = false;  // in 3D: this tile goes through the warp program (same attribute locations)
    for (const std::size_t i : order) {
        if (i >= s.textures.size()) continue;
        const auto& im = s.imported[i];
        const bool zero_copy = im.current >= 0;
        const bool held = !zero_copy && im.held_tex;  // buffers being re-created: the last frame stays up
        if (!zero_copy && !held && !s.textures[i].valid) continue;  // off-screen tiles are clipped by GL (may rotate into view)
        const bool warp = eye && s.stereo && s.stereo->ready(i) && i < s.stereo_sources.size() && s.stereo_sources[i].gain > 0.f;
        if (warp != warping) {
            warping = warp; swizzle = -1;
            if (warp) program(s.stereo->warp_program(), s.warp_projection, s.stereo->warp_view(), s.stereo->warp_half_fov(), s.stereo->warp_stereo());
            else program(s.program, s.tile_projection, s.view, s.half_fov, s.tile_stereo);
        }
        const float tile_swizzle = zero_copy || held ? 0.f : 1.f;
        if (tile_swizzle != swizzle) { glUniform1f(warp ? s.stereo->warp_swizzle() : s.swizzle, tile_swizzle); swizzle = tile_swizzle; }
        if (warp) s.stereo->warp_uniforms(i, eye->eye, s.stereo_tile_budget(i, canvas, tiles[i], view, eye_w));
        glBindTexture(GL_TEXTURE_2D, zero_copy ? im.tex[std::size_t(im.current)] : held ? im.held_tex : s.textures[i].id);
        const GLsizei n = s.build(s.tile_meshes[i], canvas, tiles[i], s.resolved[i], tiles[i], Impl::Attr::tile_uv);
        attrib(s.pos, 3, 5, 0); attrib(s.uv, 2, 5, 3);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
    }
    // The Step-Back Band's Restore pill: above the picture, on its stepped surface, fading in with the step.
    if(handles && g.restore_alpha>0.f && highlight>=0 && std::size_t(highlight)<tiles.size()){
        const auto i=std::size_t(highlight);
        const float scale=options_step_scale(tiles[i]);const FRect r=options_restore_rect(tiles[i],scale);
        bool hot=false;
        if(s.free_pointer){auto [a,e]=Impl::dot_direction(canvas,s.free_x,s.free_y,s.free_directed,s.free_az,s.free_el);Vec3 ray{std::cos(e)*std::sin(a),std::sin(e),-std::cos(e)*std::cos(a)};hot=bool(options_mesh_ray(tile_mesh(canvas,tiles[i],s.resolved[i],options_restore_hit(r,scale)),ray));}
        if(s.uv>=0)glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        glEnable(GL_BLEND);glBlendFunc(GL_ONE,GL_ONE_MINUS_SRC_ALPHA);
        program(s.panel_program,s.panel_projection,s.panel_loc.view,s.panel_loc.half_fov,s.panel_loc.stereo);
        glUniform1f(s.panel_loc.alpha,g.restore_alpha);glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_2D,s.handle_texture);
        const AtlasBox box=options_restore_box(hot);const GLsizei n=s.build(s.bar_meshes[i],canvas,tiles[i],s.resolved[i],r,Impl::Attr::rect_uv,false,&box);
        attrib(s.panel_loc.pos,3,5,0);attrib(s.panel_loc.uv,2,5,3);glDrawArrays(GL_TRIANGLE_STRIP,0,n);
        glDisableVertexAttribArray(static_cast<GLuint>(s.panel_loc.uv));glDisable(GL_BLEND);
    }
    if (s.glass_program) {
        // Glass pills: blended over the desktops, each on its own desktop's surface (it faces you with its desktop),
        // back to front, except that the open panel's desktop comes last (glass_draw_order): the supervisor hit-tests
        // the open panel as the top layer. The open one morphs into its placement panel; its content fades in over the
        // glass. While a slider is held the open panel stays on its press-time surface (GlassFrame::surface). Glass keeps
        // its size whatever the desktop's (view.hpp glass_mesh): a resize moves a pill with the desktop, never scales it.
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        auto glass_on = [&](std::size_t i) -> const Placement& { return int(i) == g.surface_desktop && g.surface ? *g.surface : s.resolved[i]; };
        auto shape = [&](Impl::Mesh& mesh, std::size_t i, const FRect& r, float radius, const GlassLook& look, float opacity, float margin) {
            program(s.glass_program, s.glass_projection, s.glass_loc.view, s.glass_loc.half_fov, s.glass_loc.stereo);
            glUniform4f(s.glass_loc.rect, r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2);
            glUniform1f(s.glass_loc.radius, radius);
            glUniform4fv(s.glass_loc.style, kGlassStyleVecs, glass_style(look).data());  // every M3 style input at once
            glUniform1f(s.glass_loc.opacity, opacity);
            const GLsizei n = s.build(mesh, canvas, tiles[i], glass_on(i), {r.x - margin, r.y - margin, r.width + 2 * margin, r.height + 2 * margin},
                                      Impl::Attr::canvas_px, true);  // fixed size: a resize never scales the glass
            attrib(s.glass_loc.pos, 3, 5, 0); attrib(s.glass_loc.px, 2, 5, 3);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        };
        for (const std::size_t i : glass_draw_order(std::move(order), g.top)) {  // the tiles are drawn: hand their order over
            if (i >= s.textures.size() || i >= g.expand.size()) continue;
            const float e = std::clamp(g.expand[i], 0.f, 1.f);
            if (!g.pills && e <= 0.f) continue;
            const float level = int(i) == g.hover ? g.hover_level : kGlassIdle;
            const FRect r = g.options && int(i)==g.content ? options_panel(tiles[i]) : glass_morph(tiles[i], e);
            const float opacity = g.options && int(i)==g.content ? g.options_alpha : 1.f;
            shape(s.glass_meshes[i], i, r, glass_radius(tiles[i], e), glass_look(accent, level, e), opacity, kGlassShadowMargin);
            if (e <= 0.f) continue;
            // The grabber where the pill was, fading in with the panel.
            const FRect grab{r.x + (r.width - kGrabberW) / 2, r.y + kGrabberTop, kGrabberW, kGrabberH};
            shape(s.grab_meshes[i], i, grab, kGrabberH / 2, grabber_look(level), opacity * std::clamp((e - 0.5f) * 2.f, 0.f, 1.f), 2.f);
            if (int(i) != g.content || !g.image || g.content_alpha <= 0.f || !s.panel_program) continue;
            const auto& im = *g.image;
            if (!im.width || !im.height || im.rgba.size() != std::size_t(im.width) * im.height * 4) continue;
            glDisableVertexAttribArray(static_cast<GLuint>(s.glass_loc.px));
            program(s.panel_program, s.panel_projection, s.panel_loc.view, s.panel_loc.half_fov, s.panel_loc.stereo);
            s.upload_panel(im, g.generation);  // already uploaded before the first draw: binds it
            glUniform1f(s.panel_loc.alpha, std::min(g.content_alpha, 1.f));
            // The content rides on the morphing shape (it only shows over the last quarter, when the shape is near full size).
            const GLsizei n = s.build(s.panel_meshes[i], canvas, tiles[i], glass_on(i), r, Impl::Attr::rect_uv, true);
            attrib(s.panel_loc.pos, 3, 5, 0); attrib(s.panel_loc.uv, 2, 5, 3);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
            glDisableVertexAttribArray(static_cast<GLuint>(s.panel_loc.uv));
        }
        glDisableVertexAttribArray(static_cast<GLuint>(s.glass_loc.px));
        glDisable(GL_BLEND);
    }
    if (s.free_pointer && s.drop_target >= 0 && std::size_t(s.drop_target) < tiles.size()) {
        // Window drag: the desktop the window would land on, framed in the accent on its own surface, over its pixels.
        const auto d = std::size_t(s.drop_target);
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        program(s.color_program, s.color_projection, s.color_view, s.color_half_fov, s.color_stereo);
        glUniform4f(s.color, s.accent[0], s.accent[1], s.accent[2], 1.f);
        const auto bands = drop_frame(tiles[d]);
        for (std::size_t k = 0; k < bands.size(); ++k) {
            const GLsizei n = s.build(s.drop_mesh[k], canvas, tiles[d], s.resolved[d], bands[k], Impl::Attr::tile_uv);
            attrib(s.color_pos, 3, 5, 0);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        }
    }
    glBindBuffer(GL_ARRAY_BUFFER, 0);  // GlHud (and the video path) draw from client arrays
    // The help card, then the free pointer on top of it: the pointer clicks the card's page arrows.
    if (s.help_hud && s.help && s.help_alpha > 0.f) {
        s.help_hud->draw(*s.help, s.help_generation, s.help_alpha, s.width, s.height, true, s.help_drop);
        s.current = 0;
    }
    if (s.binds_hud && s.binds && s.binds_alpha > 0.f) {  // the key bind panel (the supervisor never shows both cards)
        s.binds_hud->draw(*s.binds, s.binds_generation, s.binds_alpha, s.width, s.height, true, s.binds_drop);
        s.current = 0;
    }
    if (s.free_pointer && s.free_level > 0.f) {
        // Overview free pointer: a black ring, then the grey dot, on the canvas plane (world-locked like the desktops' slots),
        // or turned to its direction round you when one is given (the supervisor always gives one).
        if (s.uv >= 0) glDisableVertexAttribArray(static_cast<GLuint>(s.uv));
        GLint dot_color = s.color;
        if (eye && s.stereo) {
            // In 3D the pointer sits on what it points at, a pixel in front of it: it gets the disparity of the surface under
            // it, the desktop's plane plus the depth under the dot, and none of its own (it is drawn at radius 1).
            const auto [a, e] = Impl::dot_direction(canvas, s.free_x, s.free_y, s.free_directed, s.free_az, s.free_el);
            const Vec3 ray{std::cos(e) * std::sin(a), std::sin(e), -std::cos(e) * std::cos(a)};
            int on = -1; float nearest = 1e9f, u = 0, v = 0, z = -1;
            for (std::size_t i = 0; i < tiles.size(); ++i) {
                const auto hit = stereo_surface_hit(canvas, tiles[i], s.resolved[i], ray);
                if (!hit) continue;
                const auto q = apply(view, {hit->world.x, hit->world.y, hit->world.z});
                const float d = -q[2];
                if (d < nearest) { nearest = d; on = int(i); u = hit->u; v = hit->v; z = q[2]; }
            }
            const auto budget = on >= 0 ? s.stereo_tile_budget(std::size_t(on), canvas, tiles[std::size_t(on)], view, eye_w) : StereoBudget{};
            const float side = stereo_side(eye->eye);
            const float shift = side * stereo_plane_disparity(s.stereo_b, z, eye_w, half_w) / eye_w + side / eye_w;
            s.use(s.stereo->pointer_program());
            glUniformMatrix3fv(s.stereo->pointer_view(), 1, GL_FALSE, column_major.data()); glUniform2f(s.stereo->pointer_half_fov(), half_w, half_h);
            s.stereo->pointer_uniforms(on, u, v, eye->eye, budget, eye_w, shift);
            dot_color = s.stereo->pointer_color();
        } else {
            program(s.color_program, s.color_projection, s.color_view, s.color_half_fov, s.color_stereo);
        }
        if (s.ghost_w > 0.f && s.ghost_h > 0.f) {  // window drag: the window's ghost round the dot, under it
            const GLsizei n = s.build_ghost(s.ghost_mesh, canvas, s.free_x, s.free_y, s.ghost_w, s.ghost_h, s.free_directed, s.free_az, s.free_el);
            glUniform4f(dot_color, kGhostLevel, kGhostLevel, kGhostLevel, 1.f);
            attrib(s.color_pos, 3, 3, 0);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, n);
        }
        if (s.free_look != PointerLook::plain) for (int k = 0; k < 2; ++k) {  // what a press would do: black outlines, then grey
            const float level = k ? s.free_level : 0.f;
            const GLsizei n = s.build_marks(s.mark_mesh[k], canvas, s.free_x, s.free_y, s.free_look, k ? 0.f : kFreePointerRingPx,
                                            s.free_directed, s.free_az, s.free_el);
            glUniform4f(dot_color, level, level, level, 1.f);
            attrib(s.color_pos, 3, 3, 0);
            glDrawArrays(GL_TRIANGLES, 0, n);
        }
        if (s.free_look != PointerLook::text) for (int k = 0; k < 2; ++k) {  // the Serif Beam stands in for the disc
            const float radius = k ? kFreePointerRadiusPx : kFreePointerRadiusPx + kFreePointerRingPx, level = k ? s.free_level : 0.f;
            const GLsizei n = s.build_disc(s.disc_mesh[k], canvas, s.free_x, s.free_y, radius, s.free_directed, s.free_az, s.free_el);
            glUniform4f(dot_color, level, level, level, 1.f);
            attrib(s.color_pos, 3, 3, 0);
            glDrawArrays(GL_TRIANGLE_FAN, 0, n);
        }
        glBindBuffer(GL_ARRAY_BUFFER, 0);
    }
    if (s.hud && s.popup) s.hud->draw(*s.popup, s.popup_generation, s.popup_alpha, s.width, s.height);  // head-locked, on top
}

bool GlCompositor::draw_scene_and_swap(const std::function<void(std::uint32_t, std::uint32_t)>& scene, const HudImage* card,
                                       std::uint64_t card_generation, float card_alpha) {
    auto& s = *impl_;
    const auto start = now_us();
    scene(s.px_w, s.px_h);  // the scene's own viewports are buffer pixels (video_mode.cpp eye_viewports)
    const auto overlays = [&] {
        if (card && card_alpha > 0.f) {
            if (!s.video_hud) s.video_hud = GlHud::create();
            if (s.video_hud) s.video_hud->draw(*card, card_generation, card_alpha, s.width, s.height);
        }
        if (s.hud && s.popup) s.hud->draw(*s.popup, s.popup_generation, s.popup_alpha, s.width, s.height);
    };
    const auto eyes = s.stereo_eyes();
    if (eyes.empty()) { glViewport(0, 0, static_cast<GLsizei>(s.px_w), static_cast<GLsizei>(s.px_h)); overlays(); }
    else {  // the same picture in each eye: on the screen plane
        for (const auto& e : eyes) { glViewport(GLint(e.x), GLint(e.y), GLsizei(e.width), GLsizei(e.height)); overlays(); }
        glViewport(0, 0, static_cast<GLsizei>(s.px_w), static_cast<GLsizei>(s.px_h));
    }
    const bool ok = glGetError() == GL_NO_ERROR && s.swap();
    s.draw_stat.add(now_us() - start);
    return ok;
}

std::string GlCompositor::stereo_readback()const{
 using J=nlohmann::json;const auto& s=*impl_;const auto& p=s.stereo_set.params;
 J desktops=J::array();
 const bool sbs=!s.stereo_eyes().empty();
 for(std::size_t i=0;i<s.textures.size();++i){bool configured=s.stereo_set.enabled&&(i>=s.stereo_set.desktops.size()||s.stereo_set.desktops[i]);bool ready=s.stereo&&s.stereo->ready(i);float gain=i<s.stereo_sources.size()?s.stereo_sources[i].gain:0;
 desktops.push_back({{"configured",configured},{"depth_ready",ready},{"gain",gain},{"drawing",sbs&&bool(s.stereo)&&ready&&gain>0},{"strength",i<s.stereo_set.strengths.size()&&s.stereo_set.strengths[i]>=0?s.stereo_set.strengths[i]/100.f:p.strength}});
 }
 return J{{"available",bool(s.stereo)},{"enabled",s.stereo_set.enabled},{"physical_sbs",sbs},{"error",s.stereo_why.substr(0,256)},{"frames",s.stereo_frames},{"depth_updates",s.stereo?s.stereo->updates():0},{"params",{{"strength",p.strength},{"convergence",p.convergence},{"near_deg",p.near_deg},{"far_deg",p.far_deg},{"text_keep",p.text_keep}}},{"desktops",desktops}}.dump();
}
std::string GlCompositor::report() const {
    const auto& s = *impl_;
    bool any = false; for (const auto& i : s.imported) any |= i.current >= 0;
    return std::string("{\"path\":\"") + (any ? "gles2-dmabuf-import" : "gles2-shm-upload") + "\",\"imported_frames\":" +
           std::to_string(s.imported_frames) + ",\"vendor\":\"" + s.vendor + "\",\"renderer\":\"" + s.renderer + "\",\"version\":\"" + s.version +
           "\",\"upload\":" + s.upload_stat.json() + ",\"draw_swap\":" + s.draw_stat.json() +
           ",\"uploaded_mib\":" + std::to_string(s.uploaded_bytes >> 20) +
           (s.stereo || !s.stereo_why.empty()
                ? ",\"stereo\":{\"available\":" + std::string(s.stereo ? "true" : "false") + ",\"why\":\"" + escape(s.stereo_why.c_str()) + "\",\"frames\":" +
                      std::to_string(s.stereo_frames) + ",\"depth_updates\":" + std::to_string(s.stereo ? s.stereo->updates() : 0) + "}"
                : std::string()) + "}";
}
}
