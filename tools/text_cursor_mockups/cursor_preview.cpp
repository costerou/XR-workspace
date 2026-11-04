// Text cursor mockups: frames from the real compositor with a design option drawn where the dot would be.
//
//   cursor_preview info <rrggbb>
//       JSON on stdout: the overview's tiles (real layout), the dot's constants and the M3 scheme of that theme colour.
//   cursor_preview render <rrggbb> <side desktop.bgra> <manifest> <scale>
//       One raw RGBA (top row first) per manifest line, at `scale` buffer px per canvas px.
//
// Every frame is GlCompositor::draw_and_swap on surfaceless EGL (create_offscreen): the same desktops, border, glow, pills,
// level bars and corner caps as a run, with the Luma Ultra profile's three 1920 x 1200 desktops at spacing 0. With no
// shape file the compositor draws the dot itself (set_free_pointer), any look, so a "today" frame is the renderer alone.
// With one, a design is drawn after the frame, in the compositor's context and framebuffer, the way gl.cpp draws the dot
// and its marks: flat grey triangles over black ones on the canvas plane, under the same view and FOV uniforms and with no
// anti-aliasing. Only a soft glow blends, as "the brighter of the two" like gl.cpp's glow over the grid (GL_MAX_EXT). A real
// frame's (az, el) is the point's own direction, which turns nothing (view.hpp direction_disc), so a shape sits on the
// canvas plane as it would in a run.
//
// A manifest line: out.rgba centre_desktop.bgra shape|- x y look level roll crop_x crop_y crop_w crop_h
//   x, y     cursor, canvas px; x < 0 for no cursor. May be fractional, to show a head that is not on a whole pixel.
//   look     view.hpp PointerLook as an int (no shape file only)
//   level    the dot's grey, 0..1 (no shape file only; a shape carries its own colours)
//   roll     head roll in degrees, about the view axis
//   crop     canvas px
// A shape file: `r g b a n` then n x,y pairs (three per triangle, canvas px from the cursor, y down), one such layer after
// the other, bottom first; r g b a straight, not premultiplied. A layer with a < 1 is glow: each pixel keeps the brighter of
// the two. A solid one paints over.
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
#include <sstream>
#include <string>
#include <vector>
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/view.hpp"

using namespace xr;

namespace {
constexpr std::uint32_t kCanvasW = 1920, kCanvasH = 1200, kDesktopW = 1920, kDesktopH = 1200, kDesktops = 3;
constexpr float kSpacing = 0.f;  // ~/.config/viture-workspace/profiles.json, luma-ultra
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
    std::string out = "{\"canvas\":[" + std::to_string(kCanvasW) + "," + std::to_string(kCanvasH) + "],\"desktop\":[" +
                      std::to_string(kDesktopW) + "," + std::to_string(kDesktopH) + "],\"tiles\":[";
    for (std::size_t i = 0; i < t.size(); ++i) out += (i ? "," : "") + rect(t[i]);
    char dot[220];
    std::snprintf(dot, sizeof dot, "],\"dot\":{\"level\":%.6f,\"held\":%.6f,\"radius\":%.3f,\"ring\":%.3f,\"segments\":%d,\"gap\":%.3f,\"len\":%.3f,\"half\":%.3f}",
                  kFreePointerLevel, kFreePointerHeldLevel, kFreePointerRadiusPx, kFreePointerRingPx, kFreePointerSegments,
                  kPointerMarkGapPx, kPointerMarkLenPx, kPointerMarkHalfPx);
    out += dot;
    out += ",\"scheme\":{";
    out += role("accent", accent) + role("primary", m.primary) + role("primary_container", m.primary_container) +
           role("surface", m.surface) + role("surface_container_low", m.surface_container_low) +
           role("surface_container", m.surface_container) + role("on_surface", m.on_surface) +
           role("on_surface_variant", m.on_surface_variant) + role("outline", m.outline, true);
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
// gl.cpp XR_PROJECT and kColorVertex.
constexpr const char* kColorVertex =
    "attribute vec3 pos;\n"
    "uniform mat3 view; uniform vec2 half_fov;\n"
    "vec4 project(vec3 p) { vec3 q = view * p;\n"
    "  return vec4(q.x / half_fov.x, q.y / half_fov.y, 0.0, -q.z); }\n"
    "void main() { gl_Position = project(pos); }\n";
constexpr const char* kColorFragment = "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";

struct Program {
    GLuint id = 0; GLint pos = -1, view = -1, half_fov = -1, color = -1;
    void use(const Mat3& v, const std::array<float, 2>& half) const {
        const std::array<float, 9> column_major{v[0], v[3], v[6], v[1], v[4], v[7], v[2], v[5], v[8]};  // gl.cpp's, Mat3 is row-major
        glUseProgram(id);
        glUniformMatrix3fv(view, 1, GL_FALSE, column_major.data()); glUniform2f(half_fov, half[0], half[1]);
    }
};
Program color_program() {
    Program p; p.id = glCreateProgram();
    glAttachShader(p.id, compile(GL_VERTEX_SHADER, kColorVertex)); glAttachShader(p.id, compile(GL_FRAGMENT_SHADER, kColorFragment));
    glLinkProgram(p.id);
    GLint ok = 0; glGetProgramiv(p.id, GL_LINK_STATUS, &ok);
    if (!ok) { std::fputs("link failed\n", stderr); std::exit(1); }
    p.pos = glGetAttribLocation(p.id, "pos"); p.view = glGetUniformLocation(p.id, "view");
    p.half_fov = glGetUniformLocation(p.id, "half_fov"); p.color = glGetUniformLocation(p.id, "color");
    return p;
}

std::vector<std::uint8_t> read_file(const std::string& path, std::size_t want) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::uint8_t> out(want);
    if (!f.read(reinterpret_cast<char*>(out.data()), std::streamsize(want))) { std::fprintf(stderr, "cannot read %zu bytes of %s\n", want, path.c_str()); std::exit(1); }
    return out;
}

struct Layer { float r = 0, g = 0, b = 0, a = 1; std::vector<float> xy; };
std::vector<Layer> read_shape(const std::string& path) {
    std::vector<Layer> layers;
    std::ifstream f(path);
    Layer l; std::size_t n = 0;
    while (f >> l.r >> l.g >> l.b >> l.a >> n) {
        l.xy.resize(n * 2);
        for (auto& v : l.xy) if (!(f >> v)) { std::fprintf(stderr, "short shape %s\n", path.c_str()); std::exit(1); }
        layers.push_back(l);
    }
    if (layers.empty()) { std::fprintf(stderr, "empty shape %s\n", path.c_str()); std::exit(1); }
    return layers;
}

void disable_attribs() {
    GLint n = 0; glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &n);
    for (GLint i = 0; i < n; ++i) glDisableVertexAttribArray(GLuint(i));
}

// view.cpp's `at` in direction_marks: a canvas px on the canvas plane.
void draw_shape(const Program& p, const Canvas& c, const std::vector<Layer>& layers, float x, float y) {
    glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    for (const auto& l : layers) {
        glBlendEquation(l.a < 1.f ? GL_MAX_EXT : GL_FUNC_ADD);
        std::vector<Vec3> points;
        points.reserve(l.xy.size() / 2);
        for (std::size_t i = 0; i + 1 < l.xy.size(); i += 2) {
            const float px = x + l.xy[i], py = y + l.xy[i + 1];
            points.push_back({(2 * px / c.view_w - 1) * c.half_w, (1 - 2 * py / c.view_h) * c.half_h, -1.f});
        }
        glUniform4f(p.color, l.r * l.a, l.g * l.a, l.b * l.a, l.a);
        glVertexAttribPointer(GLuint(p.pos), 3, GL_FLOAT, GL_FALSE, sizeof(Vec3), points.data());
        glEnableVertexAttribArray(GLuint(p.pos));
        glDrawArrays(GL_TRIANGLES, 0, GLsizei(points.size()));
        glDisableVertexAttribArray(GLuint(p.pos));
    }
    glBlendEquation(GL_FUNC_ADD);
    glDisable(GL_BLEND);
}

Mat3 roll_view(float degrees) {
    const float a = degrees * float(std::numbers::pi) / 180.f, c = std::cos(a), s = std::sin(a);
    return {c, -s, 0, s, c, 0, 0, 0, 1};
}

int render(const std::array<std::uint8_t, 3>& hex, const std::string& side, const std::string& manifest, int scale) {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, kDesktops, why);
    if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
    const std::uint32_t buffer_w = kCanvasW * std::uint32_t(scale), buffer_h = kCanvasH * std::uint32_t(scale);
    gl->resize(kCanvasW, kCanvasH, buffer_w, buffer_h);
    gl->set_accent(hex[0], hex[1], hex[2]);
    const std::size_t desktop_bytes = std::size_t(kDesktopW) * kDesktopH * 4;
    const auto side_px = read_file(side, desktop_bytes);
    for (std::uint32_t i = 0; i < kDesktops; ++i)
        if (!gl->upload(i, side_px.data(), kDesktopW, kDesktopH, kDesktopW * 4)) { std::fputs("upload failed\n", stderr); return 1; }
    const auto program = color_program();
    const auto half = half_fov();
    const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
    const auto layout = tiles();
    const std::vector<Placement> placements(kDesktops);

    GlassFrame glass;
    glass.pills = true;
    glass.bars = {true, true, true, false};
    gl->set_glass(glass);

    std::ifstream in(manifest);
    std::string line, centre_shown;
    int frames = 0;
    std::vector<std::uint8_t> rgba;
    while (std::getline(in, line)) {
        std::istringstream fields(line);
        std::string out, centre, shape;
        float x = 0, y = 0, level = 0, roll = 0;
        int look = 0, cx = 0, cy = 0, cw = 0, ch = 0;
        if (!(fields >> out >> centre >> shape >> x >> y >> look >> level >> roll >> cx >> cy >> cw >> ch)) continue;
        if (centre != centre_shown) {
            const auto px = read_file(centre, desktop_bytes);
            if (!gl->upload(kCentre, px.data(), kDesktopW, kDesktopH, kDesktopW * 4)) { std::fputs("upload failed\n", stderr); return 1; }
            centre_shown = centre;
        }
        const Mat3 view = roll_view(roll);
        if (x >= 0 && shape == "-") {
            // As a run's frame: always directed (free_pointer._pointer_frame), here at the point's own direction.
            const double wx = (2.0 * x / canvas.view_w - 1) * canvas.half_w, wy = (1 - 2.0 * y / canvas.view_h) * canvas.half_h;
            const float az = float(std::atan2(wx, 1.0) * 180 / std::numbers::pi), el = float(std::atan2(wy, std::hypot(wx, 1.0)) * 180 / std::numbers::pi);
            gl->set_free_pointer(true, x, y, level, true, az, el, PointerLook(look));
        } else {
            gl->set_free_pointer(false, 0, 0, 0);
        }
        if (!gl->draw_and_swap(layout, placements, kCentre, 1.f, view, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }

        // The compositor left its framebuffer bound and current (create_offscreen). Its programs and meshes are untouched.
        if (x >= 0 && shape != "-") {
            disable_attribs();
            glBindBuffer(GL_ARRAY_BUFFER, 0);
            program.use(view, half);
            draw_shape(program, canvas, read_shape(shape), x, y);
        }

        glFinish();
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        const GLsizei read_w = cw * scale, read_h = ch * scale;
        rgba.resize(std::size_t(read_w) * read_h * 4);
        glReadPixels(cx * scale, (int(kCanvasH) - cy - ch) * scale, read_w, read_h, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        if (glGetError() != GL_NO_ERROR) { std::fputs("gl error\n", stderr); return 1; }
        std::ofstream f(out, std::ios::binary);
        for (GLsizei row = read_h - 1; row >= 0; --row)  // GL's first row is the bottom one
            f.write(reinterpret_cast<const char*>(&rgba[std::size_t(row) * std::size_t(read_w) * 4]), std::streamsize(read_w) * 4);
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
    if (mode == "render" && argc == 6 && parse_hex(argv[2], hex)) return render(hex, argv[3], argv[4], std::atoi(argv[5]));
    std::fputs("usage: cursor_preview info <rrggbb>\n"
               "       cursor_preview render <rrggbb> <side desktop.bgra> <manifest> <scale>\n", stderr);
    return 2;
}
