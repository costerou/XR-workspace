// 3D button mockups: frames from the real compositor with a button design drawn on each desktop's own surface.
//
//   button_preview info <rrggbb>
//       JSON on stdout: the overview's tiles, pills and level bars (real layout, Luma Ultra profile) and the M3 scheme of
//       that theme colour, from the same glass_theme_scheme() the pills use.
//   button_preview render <rrggbb> <desktops dir> <manifest> <scale>
//       One raw RGBA (top row first) per manifest line, at `scale` buffer px per canvas px.
//   button_preview help <rrggbb> <rows file> <out.rgba> <surface w> <surface h>
//       The real help card (render_help_card, the real font) of those rows; prints "w h row_px pages" for page 0.
//
// Every frame is GlCompositor::draw_and_swap on surfaceless EGL (create_offscreen): the same glow, border, desktops, glass
// pills, level bars, corner caps and open panel as a run. Its only preview-only part comes after that, in the compositor's
// context and framebuffer: each desktop's overlay (premultiplied RGBA in tile px) through view.hpp tile_mesh with the view
// and FOV uniforms of the compositor's own vertex shader, the path of the border and the glow, so a button lies on its
// desktop's surface and rolls with it; then the dot, from direction_disc and direction_marks.
//
// A manifest line:
//   out.rgba crop_x crop_y crop_w crop_h overlay0|- overlay1|- overlay2|- overlay_w overlay_h margin dot_x dot_y look roll panel
//     overlayN  covers desktop N's tile plus `margin` tile px on every side
//     dot_x/y   canvas px, dot_x < 0 for no dot; look: view.hpp PointerLook as an int
//     roll      desktop 1's levelling roll, degrees
//     panel     1: desktop 1's panel open, from the real render_glass_panel
// A rows file line: keys<TAB>action<TAB>1 if the row starts a section, else 0.
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <numbers>
#include <sstream>
#include <string>
#include <vector>
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/hud.hpp"
#include "xr/view.hpp"

using namespace xr;

namespace {
// The Luma Ultra profile: three 1920 x 1200 desktops on a 1920 x 1200 canvas, as a run lays them out.
constexpr std::uint32_t kCanvasW = 1920, kCanvasH = 1200, kDesktopW = 1920, kDesktopH = 1200, kDesktops = 3;
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
    out += "],\"level_bar\":" + rect(level_bar_rect(t[kCentre])) + ",\"level_bar_scale\":" + std::to_string(level_bar_scale(t[kCentre]));
    out += ",\"panel\":" + rect(glass_panel(t[kCentre])) + ",\"scheme\":{";
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

std::vector<std::string> split_tabs(const std::string& line) {
    std::vector<std::string> out;
    std::istringstream in(line);
    for (std::string part; std::getline(in, part, '\t');) out.push_back(part);
    return out;
}

int help(const std::array<std::uint8_t, 3>& hex, const std::string& rows_path, const std::string& out, std::uint32_t w, std::uint32_t h) {
    std::vector<HelpRow> rows;
    std::ifstream in(rows_path);
    for (std::string line; std::getline(in, line);) {
        const auto f = split_tabs(line);
        if (f.size() == 3) rows.push_back({f[0], f[1], f[2] == "1"});
    }
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::fprintf(stderr, "no font: %s\n", why.c_str()); return 1; }
    const std::array<float, 3> accent{hex[0] / 255.f, hex[1] / 255.f, hex[2] / 255.f};
    const auto fit = help_card_fit(rows, font.get(), w, h);
    const auto card = render_help_card(rows, accent, font.get(), w, h, 0, nullptr);
    std::ofstream(out, std::ios::binary).write(reinterpret_cast<const char*>(card.rgba.data()), std::streamsize(card.rgba.size()));
    std::printf("%u %u %u %zu\n", card.width, card.height, fit.row_px, std::size_t(fit.pages()));
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

GLuint load_overlay(const std::string& path, int w, int h) {
    if (path == "-") return 0;
    const auto rgba = read_file(path, std::size_t(w) * std::size_t(h) * 4);
    GLuint tex = 0;
    glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    return tex;
}

void disable_attribs() {
    GLint n = 0; glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &n);
    for (GLint i = 0; i < n; ++i) glDisableVertexAttribArray(GLuint(i));
}

// The tile plus `margin` on its own surface: the rect_uv mesh gl.cpp builds for the panel content and the border.
void draw_overlay(const Program& p, GLuint tex, const Canvas& c, const FRect& tile, const Placement& on, float margin) {
    if (!tex) return;
    const FRect r{tile.x - margin, tile.y - margin, tile.width + 2 * margin, tile.height + 2 * margin};
    std::vector<GLfloat> v;
    for (const auto& m : tile_mesh(c, tile, on, r))
        v.insert(v.end(), {m.world.x, m.world.y, m.world.z, (m.px - r.x) / r.width, (m.py - r.y) / r.height});
    glBindTexture(GL_TEXTURE_2D, tex);
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

int render(const std::array<std::uint8_t, 3>& hex, const std::string& desktops, const std::string& manifest, int scale) {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, kDesktops, why);
    if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
    gl->resize(kCanvasW, kCanvasH, kCanvasW * std::uint32_t(scale), kCanvasH * std::uint32_t(scale));
    gl->set_accent(hex[0], hex[1], hex[2]);
    for (std::uint32_t i = 0; i < kDesktops; ++i) {
        const auto px = read_file(desktops + "/desktop-" + std::to_string(i) + ".bgra", std::size_t(kDesktopW) * kDesktopH * 4);
        if (!gl->upload(i, px.data(), kDesktopW, kDesktopH, kDesktopW * 4)) { std::fputs("upload failed\n", stderr); return 1; }
    }
    const auto overlay_program = program(kOverlayVertex, kOverlayFragment), dot_program = program(kColorVertex, kColorFragment);
    const auto half = half_fov();
    const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
    const auto layout = tiles();
    std::vector<Placement> placements(kDesktops);
    const Mat3 identity{1, 0, 0, 0, 1, 0, 0, 0, 1};

    // The open panel, what a run sends for a desktop in the Luma Ultra profile with its values at rest.
    std::string font_why;
    const auto font = HudFont::load(kHudFontFamily, font_why);
    PlacementMenuState menu;
    menu.open = true; menu.desktop = std::uint32_t(kCentre); menu.field = 99;
    for (auto& p : menu.placements) p = placement_menu_flat();
    menu.resolution_w = kDesktopW; menu.resolution_h = kDesktopH; menu.scale = 100; menu.scales = 0x7f;
    const HudImage panel = render_glass_panel(menu, font.get(), {hex[0] / 255.f, hex[1] / 255.f, hex[2] / 255.f});

    std::ifstream in(manifest);
    std::string line;
    int frames = 0;
    while (std::getline(in, line)) {
        std::istringstream fields(line);
        std::string out;
        std::array<std::string, kDesktops> overlays;
        int cx = 0, cy = 0, cw = 0, ch = 0, ow = 0, oh = 0, look = 0, open = 0;
        float margin = 0, dot_x = 0, dot_y = 0, roll = 0;
        if (!(fields >> out >> cx >> cy >> cw >> ch >> overlays[0] >> overlays[1] >> overlays[2] >> ow >> oh >> margin >> dot_x >> dot_y >> look >> roll >> open)) continue;
        placements[kCentre].roll = roll * float(std::numbers::pi) / 180.f;
        GlassFrame glass;
        glass.pills = true;
        glass.bars = {true, true, true, false};
        if (open) {
            glass.expand[kCentre] = 1.f; glass.content = kCentre; glass.image = &panel; glass.generation = 1; glass.content_alpha = 1.f;
            glass.top = kCentre;
        }
        gl->set_glass(glass);
        gl->set_free_pointer(false, 0, 0, 0);
        if (!gl->draw_and_swap(layout, placements, kCentre, 1.f, identity, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }

        // The compositor left its framebuffer bound and current (create_offscreen). Its programs and meshes are untouched.
        disable_attribs();
        glBindBuffer(GL_ARRAY_BUFFER, 0);
        glActiveTexture(GL_TEXTURE0);
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        overlay_program.use(half);
        for (std::uint32_t i = 0; i < kDesktops; ++i) {
            const GLuint tex = load_overlay(overlays[i], ow, oh);
            draw_overlay(overlay_program, tex, canvas, layout[i], placements[i], margin);
            if (tex) glDeleteTextures(1, &tex);
        }
        glDisable(GL_BLEND);
        if (dot_x >= 0) {
            dot_program.use(half);
            draw_dot(dot_program, canvas, dot_x, dot_y, PointerLook(look));
        }

        glFinish();
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        const GLsizei read_w = cw * scale, read_h = ch * scale;
        std::vector<std::uint8_t> rgba(std::size_t(read_w) * std::size_t(read_h) * 4);
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
    if (mode == "render" && argc == 6 && parse_hex(argv[2], hex)) return render(hex, argv[3], argv[4], std::atoi(argv[5]));
    if (mode == "help" && argc == 7 && parse_hex(argv[2], hex)) return help(hex, argv[3], argv[4], std::uint32_t(std::atoi(argv[5])), std::uint32_t(std::atoi(argv[6])));
    std::fputs("usage: button_preview info <rrggbb>\n"
               "       button_preview render <rrggbb> <desktops dir> <manifest> <scale>\n"
               "       button_preview help <rrggbb> <rows file> <out.rgba> <surface w> <surface h>\n", stderr);
    return 2;
}
