// Handle mockups: frames from the real compositor with a design option drawn on the desktops' own surfaces.
//
//   handle_preview info <rrggbb>
//       JSON on stdout: the overview's tiles and pills (real layout) and the M3 scheme of that theme colour, from the same
//       glass_theme_scheme() the pills use, so a design takes its colours from the code that colours the pills.
//   handle_preview render <rrggbb> <desktops dir> <manifest> <scale> <crop x> <crop y> <crop w> <crop h>
//       One raw RGBA (top row first) per manifest line, cropped to the canvas rect, at `scale` buffer px per canvas px.
//
// Every frame is GlCompositor::draw_and_swap on surfaceless EGL (create_offscreen): the same glow, border, desktops and
// glass pills as a run. Its only preview-only part is what comes after it, in the compositor's context and framebuffer:
//   - a design's overlay (premultiplied RGBA in tile px) drawn through view.hpp tile_mesh with the view and FOV uniforms
//     of the compositor's own vertex shader, the path of the border and the glow, so it lies on the desktop's surface;
//   - the dot, redrawn on top with view.hpp direction_disc / direction_marks, since the overlay goes after the frame.
//
// A manifest line: out.rgba other.rgba|- centre.rgba|- overlay_w overlay_h margin dot_x dot_y look baseline
//   other    overlay for desktops 0 and 2, centre for desktop 1; both cover the tile plus `margin` tile px on every side
//   dot_x/y  canvas px, dot_x < 0 for no dot; look: view.hpp PointerLook as an int
//   baseline 0 none, 1 today's roll brackets, 2 today's roll brackets and corner bracket with one of each hot (reference)
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <numbers>
#include <string>
#include <vector>
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/view.hpp"

using namespace xr;

namespace {
constexpr std::uint32_t kCanvasW = 1920, kCanvasH = 1200, kDesktopW = 1920, kDesktopH = 1080, kDesktops = 3;
constexpr float kSpacing = 40.f;  // the overview's spacing, as gl_bench draws it
constexpr int kCentre = 1;

// gl_bench.cpp's camera: a 46 degree diagonal over the canvas, straight ahead.
std::array<float, 2> half_fov() {
    const double d = std::tan(46.0 * std::numbers::pi / 360.0), a = double(kCanvasW) / kCanvasH;
    return {float(d * a / std::sqrt(1 + a * a)), float(d / std::sqrt(1 + a * a))};
}
std::vector<FRect> tiles() {
    const std::vector<Size> sizes(kDesktops, Size{float(kDesktopW), float(kDesktopH)});
    return blended_layout(sizes, float(kCanvasW), float(kCanvasH), kSpacing, 0.f, 1.f);
}

bool parse_hex(const std::string& s, std::array<std::uint8_t, 3>& rgb) {
    if (s.size() != 6) return false;
    char* end = nullptr;
    const unsigned long v = std::strtoul(s.c_str(), &end, 16);
    if (*end) return false;
    rgb = {std::uint8_t(v >> 16), std::uint8_t(v >> 8), std::uint8_t(v)};
    return true;
}

std::string role(const char* name, const MdRgb& c, bool last = false) {
    char b[120];
    std::snprintf(b, sizeof b, "\"%s\":[%.6f,%.6f,%.6f]%s", name, c[0], c[1], c[2], last ? "" : ",");
    return b;
}
std::string rect(const FRect& r) {
    char b[100];
    std::snprintf(b, sizeof b, "[%.3f,%.3f,%.3f,%.3f]", r.x, r.y, r.width, r.height);
    return b;
}

int info(const std::array<std::uint8_t, 3>& hex) {
    const std::array<float, 3> accent{hex[0] / 255.f, hex[1] / 255.f, hex[2] / 255.f};
    const MaterialScheme& m = glass_theme_scheme(accent);
    const auto t = tiles();
    std::string out = "{\"canvas\":[" + std::to_string(kCanvasW) + "," + std::to_string(kCanvasH) + "],\"tiles\":[";
    for (std::size_t i = 0; i < t.size(); ++i) out += (i ? "," : "") + rect(t[i]);
    out += "],\"pills\":[";
    for (std::size_t i = 0; i < t.size(); ++i) out += (i ? "," : "") + rect(glass_pill(t[i]));
    out += "],\"roll_arm\":" + std::to_string(roll_arm(t[kCentre])) + ",\"scheme\":{";
    out += role("accent", accent) + role("primary", m.primary) + role("on_primary", m.on_primary) +
           role("primary_container", m.primary_container) + role("on_primary_container", m.on_primary_container) +
           role("primary_bright", m.primary_bright) + role("secondary_container", m.secondary_container) +
           role("on_secondary_container", m.on_secondary_container) + role("surface", m.surface) +
           role("surface_container_low", m.surface_container_low) + role("surface_container", m.surface_container) +
           role("surface_container_high", m.surface_container_high) + role("surface_container_highest", m.surface_container_highest) +
           role("on_surface", m.on_surface) + role("on_surface_variant", m.on_surface_variant) + role("outline", m.outline) +
           role("outline_variant", m.outline_variant, true);
    out += "}}\n";
    std::fputs(out.c_str(), stdout);
    return 0;
}

GLuint compile(GLenum type, const char* source) {
    const GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &source, nullptr); glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char log[1024]{}; glGetShaderInfoLog(s, sizeof log, nullptr, log); std::fprintf(stderr, "shader: %s\n", log); std::exit(1); }
    return s;
}
GLuint link(const char* vertex, const char* fragment) {
    const GLuint p = glCreateProgram();
    glAttachShader(p, compile(GL_VERTEX_SHADER, vertex)); glAttachShader(p, compile(GL_FRAGMENT_SHADER, fragment));
    glLinkProgram(p);
    GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) { std::fputs("link failed\n", stderr); std::exit(1); }
    return p;
}
// gl.cpp XR_PROJECT and kVertex / kColorVertex.
#define PROJECT \
    "uniform mat3 view; uniform vec2 half_fov;\n" \
    "vec4 project(vec3 p) { vec3 q = view * p;\n" \
    "  return vec4(q.x / half_fov.x, q.y / half_fov.y, 0.0, -q.z); }\n"
constexpr const char* kOverlayVertex =
    "attribute vec3 pos; attribute vec2 uv; varying vec2 v;\n" PROJECT "void main() { v = uv; gl_Position = project(pos); }\n";
constexpr const char* kOverlayFragment =
    "precision mediump float; varying vec2 v; uniform sampler2D tex; void main() { gl_FragColor = texture2D(tex, v); }\n";
constexpr const char* kColorVertex = "attribute vec3 pos;\n" PROJECT "void main() { gl_Position = project(pos); }\n";
constexpr const char* kColorFragment = "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";

struct Program {
    GLuint id = 0; GLint pos = -1, uv = -1, view = -1, half_fov = -1, color = -1;
    void use(const std::array<float, 2>& half) const {
        static constexpr float kIdentity[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
        glUseProgram(id);
        glUniformMatrix3fv(view, 1, GL_FALSE, kIdentity); glUniform2f(half_fov, half[0], half[1]);
    }
};
Program program(const char* vertex, const char* fragment) {
    Program p; p.id = link(vertex, fragment);
    p.pos = glGetAttribLocation(p.id, "pos"); p.uv = glGetAttribLocation(p.id, "uv");
    p.view = glGetUniformLocation(p.id, "view"); p.half_fov = glGetUniformLocation(p.id, "half_fov");
    p.color = glGetUniformLocation(p.id, "color");
    return p;
}

std::vector<std::uint8_t> read_file(const std::string& path, std::size_t want) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::uint8_t> out(want);
    if (!f.read(reinterpret_cast<char*>(out.data()), std::streamsize(want))) { std::fprintf(stderr, "cannot read %zu bytes of %s\n", want, path.c_str()); std::exit(1); }
    return out;
}

struct Overlay { GLuint tex = 0; };
Overlay load_overlay(const std::string& path, int w, int h) {
    Overlay o;
    if (path == "-") return o;
    const auto rgba = read_file(path, std::size_t(w) * std::size_t(h) * 4);
    glGenTextures(1, &o.tex); glBindTexture(GL_TEXTURE_2D, o.tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    return o;
}

void disable_attribs() {
    GLint n = 0; glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &n);
    for (GLint i = 0; i < n; ++i) glDisableVertexAttribArray(GLuint(i));
}

// The tile plus `margin` on its own surface: the rect_uv mesh gl.cpp builds for the panel content and the border.
void draw_overlay(const Program& p, const Overlay& o, const Canvas& c, const FRect& tile, const Placement& on, float margin) {
    if (!o.tex) return;
    const FRect r{tile.x - margin, tile.y - margin, tile.width + 2 * margin, tile.height + 2 * margin};
    std::vector<GLfloat> v;
    for (const auto& m : tile_mesh(c, tile, on, r))
        v.insert(v.end(), {m.world.x, m.world.y, m.world.z, (m.px - r.x) / r.width, (m.py - r.y) / r.height});
    glBindTexture(GL_TEXTURE_2D, o.tex);
    glVertexAttribPointer(GLuint(p.pos), 3, GL_FLOAT, GL_FALSE, 5 * sizeof(GLfloat), v.data());
    glVertexAttribPointer(GLuint(p.uv), 2, GL_FLOAT, GL_FALSE, 5 * sizeof(GLfloat), v.data() + 3);
    glEnableVertexAttribArray(GLuint(p.pos)); glEnableVertexAttribArray(GLuint(p.uv));
    glDrawArrays(GL_TRIANGLE_STRIP, 0, GLsizei(v.size() / 5));
    glDisableVertexAttribArray(GLuint(p.uv)); glDisableVertexAttribArray(GLuint(p.pos));
}

void draw_points(const Program& p, const std::vector<Vec3>& points, GLenum mode, float level) {
    glUniform4f(p.color, level, level, level, 1.f);
    glVertexAttribPointer(GLuint(p.pos), 3, GL_FLOAT, GL_FALSE, sizeof(Vec3), points.data());
    glEnableVertexAttribArray(GLuint(p.pos));
    glDrawArrays(mode, 0, GLsizei(points.size()));
    glDisableVertexAttribArray(GLuint(p.pos));
}

// gl.cpp's free pointer: black outlines, then grey marks, then the black ring and the grey disc.
void draw_dot(const Program& p, const Canvas& c, float x, float y, PointerLook look) {
    const double wx = (2.0 * x / c.view_w - 1) * c.half_w, wy = (1 - 2.0 * y / c.view_h) * c.half_h;
    const float az = float(std::atan2(wx, 1.0)), el = float(std::atan2(wy, std::hypot(wx, 1.0)));
    if (look != PointerLook::plain) {
        draw_points(p, direction_marks(c, x, y, az, el, look, kFreePointerRingPx), GL_TRIANGLES, 0.f);
        draw_points(p, direction_marks(c, x, y, az, el, look, 0.f), GL_TRIANGLES, kFreePointerLevel);
    }
    draw_points(p, direction_disc(c, x, y, az, el, kFreePointerRadiusPx + kFreePointerRingPx), GL_TRIANGLE_FAN, 0.f);
    draw_points(p, direction_disc(c, x, y, az, el, kFreePointerRadiusPx), GL_TRIANGLE_FAN, kFreePointerLevel);
}

int render(const std::array<std::uint8_t, 3>& hex, const std::string& desktops, const std::string& manifest, int scale, int cx, int cy, int cw, int ch) {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, kDesktops, why);
    if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
    const std::uint32_t buffer_w = kCanvasW * std::uint32_t(scale), buffer_h = kCanvasH * std::uint32_t(scale);
    gl->resize(kCanvasW, kCanvasH, buffer_w, buffer_h);
    gl->set_accent(hex[0], hex[1], hex[2]);
    for (std::uint32_t i = 0; i < kDesktops; ++i) {
        const auto px = read_file(desktops + "/desktop-" + std::to_string(i) + ".bgra", std::size_t(kDesktopW) * kDesktopH * 4);
        if (!gl->upload(i, px.data(), kDesktopW, kDesktopH, kDesktopW * 4)) { std::fputs("upload failed\n", stderr); return 1; }
    }
    const auto overlay_program = program(kOverlayVertex, kOverlayFragment), dot_program = program(kColorVertex, kColorFragment);
    const auto half = half_fov();
    const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
    const auto layout = tiles();
    const std::vector<Placement> placements(kDesktops);
    const Mat3 identity{1, 0, 0, 0, 1, 0, 0, 0, 1};

    std::ifstream in(manifest);
    std::string out, other_path, centre_path;
    int ow = 0, oh = 0, look = 0, baseline = 0, frames = 0;
    float margin = 0, dot_x = 0, dot_y = 0;
    std::vector<std::uint8_t> rgba(std::size_t(cw) * ch * std::size_t(scale) * std::size_t(scale) * 4), row;
    while (in >> out >> other_path >> centre_path >> ow >> oh >> margin >> dot_x >> dot_y >> look >> baseline) {
        GlassFrame glass;
        glass.pills = true;
        if (baseline) glass.roll_icon = {2, 2, 2, -1};
        if (baseline == 2) { glass.roll_hover = kCentre; glass.roll_hover_side = 1; glass.roll_level = kGlassHover; }
        gl->set_glass(glass);
        gl->set_corner_handle(baseline == 2 ? kCentre : -1, 3, kHandleLevelIdle);
        gl->set_free_pointer(false, 0, 0, 0);
        if (!gl->draw_and_swap(layout, placements, kCentre, 1.f, identity, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }

        // The compositor left its framebuffer bound and current (create_offscreen). Its programs and meshes are untouched.
        disable_attribs();
        glBindBuffer(GL_ARRAY_BUFFER, 0);
        glActiveTexture(GL_TEXTURE0);
        const auto other = load_overlay(other_path, ow, oh), centre = load_overlay(centre_path, ow, oh);
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        overlay_program.use(half);
        for (std::uint32_t i = 0; i < kDesktops; ++i)
            if (int(i) != kCentre) draw_overlay(overlay_program, other, canvas, layout[i], placements[i], margin);
        draw_overlay(overlay_program, centre, canvas, layout[kCentre], placements[kCentre], margin);
        glDisable(GL_BLEND);
        if (dot_x >= 0) {
            dot_program.use(half);
            draw_dot(dot_program, canvas, dot_x, dot_y, PointerLook(look));
        }
        if (other.tex) glDeleteTextures(1, &other.tex);
        if (centre.tex) glDeleteTextures(1, &centre.tex);

        glFinish();
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        const GLsizei read_w = cw * scale, read_h = ch * scale;
        glReadPixels(cx * scale, (int(kCanvasH) - cy - ch) * scale, read_w, read_h, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        if (glGetError() != GL_NO_ERROR) { std::fputs("gl error\n", stderr); return 1; }
        std::ofstream f(out, std::ios::binary);
        for (GLsizei y = read_h - 1; y >= 0; --y)  // GL's first row is the bottom one
            f.write(reinterpret_cast<const char*>(&rgba[std::size_t(y) * std::size_t(read_w) * 4]), std::streamsize(read_w) * 4);
        ++frames;
    }
    std::printf("%d frames\n%s\n", frames, gl->report().c_str());
    return 0;
}
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    std::array<std::uint8_t, 3> hex{};
    if (mode == "info" && argc == 3 && parse_hex(argv[2], hex)) return info(hex);
    if (mode == "render" && argc == 10 && parse_hex(argv[2], hex))
        return render(hex, argv[3], argv[4], std::atoi(argv[5]), std::atoi(argv[6]), std::atoi(argv[7]), std::atoi(argv[8]), std::atoi(argv[9]));
    std::fputs("usage: handle_preview info <rrggbb>\n"
               "       handle_preview render <rrggbb> <desktops dir> <manifest> <scale> <crop x> <crop y> <crop w> <crop h>\n", stderr);
    return 2;
}
