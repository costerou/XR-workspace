// Options menu and Maximize / Restore restyle mockups: frames from the real compositor, with a design laid on them.
//
//   restyle_preview info <rrggbb>       JSON: the theme's M3 scheme (glass_theme_scheme) and the family's real constants.
//   restyle_preview measure <file>      JSON array of widths. One line per text: "<px> <bold 0|1> <text>" (HudFont::measure).
//   restyle_preview layout <job.json>   JSON: each frame's tiles, pills, bars and 3D buttons from the real layout code.
//   restyle_preview raster <job.json>   Each frame's overlays alone, as raw premultiplied RGBA (no GL): what a panel's pixels are.
//   restyle_preview render <job.json>   Each frame as raw RGBA (top row first), and one JSON line per frame on stdout.
//
// A frame is GlCompositor::draw_and_swap on surfaceless EGL (create_offscreen): the real overview or maximized layout, surfaces,
// border, glow, pills, and the real placement panel or real options panel when asked for. Only what comes after it is
// preview-only, in the compositor's context and framebuffer: an overlay is an image rasterised with the renderer's own parts
// (material_draw.hpp md_*, HudFont, glass_look / glass_shade for the shell) and drawn on the surface through view.hpp tile_mesh or
// glass_mesh, with the uniforms of the compositor's own vertex shader. `handles` redraws the bar, caps, 3D button and gear from
// render_handle_atlas through the same meshes, because a mock Maximize replaces the one the compositor draws with the bar.
// Nothing here starts, stops or talks to a run, and `render` is meant for llvmpipe: see README.md.
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>
#include <numbers>
#include <sstream>
#include <string>
#include <vector>
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/ipc.hpp"
#include "xr/material_draw.hpp"
#include "xr/options_geometry.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"

using namespace xr;
using nlohmann::json;

namespace {
constexpr std::uint32_t kCanvasW = 1920, kCanvasH = 1200;

// gl_bench.cpp's camera: a 46 degree diagonal over the canvas, straight ahead.
std::array<float, 2> half_fov() {
    const double d = std::tan(46.0 * std::numbers::pi / 360.0), a = double(kCanvasW) / kCanvasH;
    return {float(d * a / std::sqrt(1 + a * a)), float(d / std::sqrt(1 + a * a))};
}
std::array<float, 3> parse_hex(const std::string& s) {
    if (s.size() != 6) { std::fputs("accent: rrggbb\n", stderr); std::exit(2); }
    const unsigned long v = std::strtoul(s.c_str(), nullptr, 16);
    return {float((v >> 16) & 255) / 255.f, float((v >> 8) & 255) / 255.f, float(v & 255) / 255.f};
}
json rect_json(const FRect& r) { return json::array({r.x, r.y, r.width, r.height}); }
json rgb_json(const MdRgb& c) { return json::array({c[0], c[1], c[2]}); }

std::map<std::string, MdRgb> roles(const MaterialScheme& m) {
    return {{"primary", m.primary}, {"on_primary", m.on_primary}, {"primary_container", m.primary_container},
            {"on_primary_container", m.on_primary_container}, {"primary_bright", m.primary_bright},
            {"secondary_container", m.secondary_container}, {"on_secondary_container", m.on_secondary_container},
            {"surface", m.surface}, {"surface_container_low", m.surface_container_low}, {"surface_container", m.surface_container},
            {"surface_container_high", m.surface_container_high}, {"surface_container_highest", m.surface_container_highest},
            {"on_surface", m.on_surface}, {"on_surface_variant", m.on_surface_variant}, {"outline", m.outline},
            {"outline_variant", m.outline_variant}, {"error_container", m.error_container}, {"on_error_container", m.on_error_container}};
}

int info(const std::string& hex) {
    const auto accent = parse_hex(hex);
    json out;
    for (const auto& [name, c] : roles(glass_theme_scheme(accent))) out["scheme"][name] = rgb_json(c);
    out["canvas"] = {kCanvasW, kCanvasH};
    out["constants"] = {
        {"panel_w", kPanelW}, {"panel_h", kPanelH}, {"panel_radius", kPanelRadius}, {"panel_header_h", kPanelHeaderH},
        {"panel_rows_top", kPanelRowsTop}, {"panel_row_h", kPanelRowH}, {"panel_pad", kPanelPad}, {"track_x0", kPanelTrackX0},
        {"track_x1", kPanelTrackX1}, {"knob_r", kPanelKnobR}, {"box_h", kScaleBoxH}, {"switch_w", kBackgroundSwitchW},
        {"switch_h", kBackgroundSwitchH}, {"action_w", kPanelActionW}, {"action_h", kPanelActionH}, {"action_gap", kPanelActionGap},
        {"text_px", kPanelTextPx}, {"hint_px", kPanelHintPx}, {"action_px", kPanelActionPx}, {"grabber_w", kGrabberW},
        {"grabber_h", kGrabberH}, {"grabber_top", kGrabberTop}, {"pill_h", kGlassPillH}, {"pill_gap", kGlassPillGap},
        {"bar_w", kLevelBarW}, {"bar_h", kLevelBarH}, {"bar_gap", kLevelBarGap}, {"bar_pad", kLevelBarPad},
        {"immersive_gap", kImmersiveGap}, {"immersive_main_w", kImmersiveMainW}, {"immersive_tail_w", kImmersiveTailW},
        {"immersive_inner_r", kImmersiveInnerR}, {"cap_inner", kCapInner}, {"cap_outer", kCapOuter}, {"edge_clear", kLevelEdgeClear},
        {"glass_cap", kGlassCap}, {"panel_fill", kPanelFill}, {"shadow_margin", kGlassShadowMargin},
        {"options_w", kOptionsW}, {"options_h", kOptionsH}, {"options_row_h", kOptionsRowH}};
    std::cout << out.dump() << "\n";
    return 0;
}

int measure(const std::string& path) {
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::fprintf(stderr, "no font: %s\n", why.c_str()); return 1; }
    std::ifstream in(path);
    json out = json::array();
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream fields(line);
        unsigned px = 0, bold = 0;
        fields >> px >> bold;
        std::string text;
        std::getline(fields, text);
        if (!text.empty()) text.erase(0, 1);
        out.push_back(font->measure(text, px, bold != 0));
    }
    std::cout << out.dump() << "\n";
    return 0;
}

// --- Scenes --------------------------------------------------------------------------------------------------------
struct Scene {
    std::vector<Size> sizes;
    std::vector<Placement> placements, placed;  // resolved for this zoom; as saved
    std::vector<FRect> tiles, overview;
    float zoom = 0, spacing = 0;
    int selected = 0;
    bool look = false;
};
Placement placement_of(const json& p) {
    Placement out = placement_from_wire(MenuPlacement{p.at("x").get<std::int32_t>(), p.at("y").get<std::int32_t>(),
                                                      p.at("distance").get<std::int32_t>(), p.at("curvature").get<std::int32_t>()});
    out.roll = p.value("roll", 0.f) / 100.f * std::numbers::pi_v<float> / 180.f;  // 1/100 degree, as the saved `rolls`
    return out;
}
Scene scene_of(const json& frame) {
    Scene s;
    const auto half = half_fov();
    for (const auto& d : frame.at("desktops")) {
        s.sizes.push_back({d.at("w").get<float>(), d.at("h").get<float>()});
        s.placements.push_back(placement_of(d.at("placement")));
    }
    s.zoom = frame.value("zoom", 0.f); s.spacing = frame.value("spacing", 40.f); s.selected = frame.value("selected", 0);
    s.look = frame.value("look", false);
    s.placed = s.placements;
    s.overview = overview_layout(s.sizes, float(kCanvasW), float(kCanvasH), s.spacing);
    s.tiles = blended_layout(s.sizes, float(kCanvasW), float(kCanvasH), s.spacing, s.zoom, float(s.selected));
    s.placements = zoomed_placements(Canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]}, s.overview, s.tiles, s.placements, s.zoom, float(s.selected));
    return s;
}
// The view a frame is seen through: maximized, follow and locked centre it on the desktop (wayland.cpp focus_aim at full weight);
// `look` is the head turned to that desktop in the overview, Cody's saved desktops being too close to all fit straight ahead.
Mat3 scene_view(const Scene& s) {
    if (s.zoom <= 0.f && !s.look) return Mat3{1, 0, 0, 0, 1, 0, 0, 0, 1};
    const auto half = half_fov();
    const auto d = focus_direction(Canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]}, s.overview, s.placed, std::size_t(s.selected));
    return view_from(Quat{}, aim_rotation(d[0], d[1]));  // the aim is the anchor (pose.hpp ModeController)
}
int layout(const std::string& path) {
    const json job = json::parse(std::ifstream(path));
    json out = json::array();
    for (const auto& frame : job.at("frames")) {
        const Scene s = scene_of(frame);
        json f = {{"tiles", json::array()}, {"pills", json::array()}, {"bars", json::array()}, {"main", json::array()},
                  {"tail", json::array()}, {"scale", json::array()}, {"maximize", json::array()}, {"gear", json::array()}, {"restore", json::array()}};
        for (const auto& t : s.tiles) {
            f["tiles"].push_back(rect_json(t)); f["pills"].push_back(rect_json(glass_pill(t))); f["bars"].push_back(rect_json(level_bar_rect(t)));
            f["main"].push_back(rect_json(immersive_main_rect(t))); f["tail"].push_back(rect_json(immersive_tail_rect(t)));
            f["scale"].push_back(level_bar_scale(t)); f["maximize"].push_back(rect_json(options_maximize(t))); f["gear"].push_back(rect_json(options_gear(t)));
            f["restore"].push_back(rect_json(options_maximize(t, true)));
        }
        const auto half = half_fov();
        const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
        const Mat3 view = scene_view(s);
        f["canvas"] = {canvas.view_w, canvas.view_h, canvas.half_w, canvas.half_h};
        f["view"] = view;
        const auto d = std::size_t(s.selected);
        const auto& on = s.placements[d];
        f["placement"] = {on.x, on.y, on.distance, on.curvature, on.roll};
        f["real_restore"] = rect_json(options_restore_rect(canvas, s.tiles[d], on, view));
        for (const auto& r : frame.value("meshes", json::array())) {
            json mesh = json::array();
            for (const auto& v : tile_mesh(canvas, s.tiles[d], on, FRect{r.at(0).get<float>(), r.at(1).get<float>(), r.at(2).get<float>(), r.at(3).get<float>()}))
                mesh.push_back({v.world.x, v.world.y, v.world.z});
            f["meshes"].push_back(mesh);
        }
        out.push_back(f);
    }
    std::cout << out.dump() << "\n";
    return 0;
}

// --- Overlays ------------------------------------------------------------------------------------------------------
MdIcon icon_of(const std::string& n) {
    if (n == "add") return MdIcon::add;
    if (n == "remove") return MdIcon::remove;
    if (n == "width") return MdIcon::width;
    if (n == "arrow_drop_up") return MdIcon::arrow_drop_up;
    if (n == "rotate_left") return MdIcon::rotate_left;
    if (n == "rotate_right") return MdIcon::rotate_right;
    if (n == "level") return MdIcon::level;
    return MdIcon::arrow_drop_down;
}
// glass.cpp's text(): baseline from the same 0.36 em, left edge at `left`.
void draw_text(HudImage& im, const HudFont& font, const std::string& s, std::uint32_t px, bool bold, float left, float cy, MdRgb c, float alpha) {
    const int baseline = int(std::lround(cy + 0.36f * float(px)));
    font.draw(s, px, bold, int(std::lround(left)), baseline, [&](int x, int y, float cov) { md_over(im, x, y, c, cov * alpha); });
}
// The app's arrow cursor, for frames where the pointer is still the real one: the renderer has no such part, the capture does.
void draw_arrow(HudImage& im, float x, float y, float size, const MdRgb& fill, const MdRgb& edge) {
    const std::array<std::array<float, 2>, 7> p{{{0, 0}, {0, .95f}, {.25f, .72f}, {.42f, 1.1f}, {.58f, 1.03f}, {.42f, .67f}, {.72f, .67f}}};
    auto inside = [&](float px, float py) {
        bool in = false;
        for (std::size_t i = 0, j = p.size() - 1; i < p.size(); j = i++) {
            const float xi = x + p[i][0] * size, yi = y + p[i][1] * size, xj = x + p[j][0] * size, yj = y + p[j][1] * size;
            if ((yi > py) != (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) in = !in;
        }
        return in;
    };
    for (int py = int(y) - 3; py < int(y + 1.2f * size) + 3; ++py)
        for (int px = int(x) - 3; px < int(x + size) + 3; ++px) {
            int fills = 0, near = 0;
            for (int sy = 0; sy < 4; ++sy)
                for (int sx = 0; sx < 4; ++sx) {
                    const float qx = float(px) + (float(sx) + .5f) / 4, qy = float(py) + (float(sy) + .5f) / 4;
                    fills += inside(qx, qy);
                    near += inside(qx, qy) || inside(qx - 1.5f, qy) || inside(qx + 1.5f, qy) || inside(qx, qy - 1.5f) || inside(qx, qy + 1.5f);
                }
            if (near) md_over(im, px, py, edge, float(near) / 16.f);
            if (fills) md_over(im, px, py, fill, float(fills) / 16.f);
        }
}

// One overlay image: `ops` in px of a w x h box, at `density` image px per box px (the handle atlas's is kHandleAtlasScale).
HudImage raster(const json& o, const std::array<float, 3>& accent, const HudFont& font) {
    const auto all = roles(glass_theme_scheme(accent));
    const float d = o.value("density", 1.f);
    const int w = int(std::ceil(o.at("w").get<float>() * d)), h = int(std::ceil(o.at("h").get<float>() * d));
    HudImage im{std::uint32_t(w), std::uint32_t(h), std::vector<std::uint8_t>(std::size_t(w) * h * 4, 0)};
    auto colour = [&](const json& v) { return all.at(v.get<std::string>()); };
    for (const auto& op : o.at("ops")) {
        const std::string k = op.at(0);
        auto f = [&](std::size_t i) { return op.at(i).get<float>() * d; };
        if (k == "fill") md_fill_rounded(im, f(1), f(2), f(3), f(4), f(5), colour(op.at(6)), op.at(7).get<float>());
        else if (k == "stroke") md_stroke_rounded(im, f(1), f(2), f(3), f(4), f(5), f(6), colour(op.at(7)), op.at(8).get<float>());
        else if (k == "disc") md_disc(im, f(1), f(2), f(3), colour(op.at(4)), op.at(5).get<float>());
        else if (k == "icon") md_icon(im, icon_of(op.at(1)), f(2), f(3), f(4), colour(op.at(5)), op.at(6).get<float>());
        else if (k == "text") draw_text(im, font, op.at(1).get<std::string>(), std::uint32_t(std::lround(f(2))), op.at(3).get<bool>(), f(4), f(5), colour(op.at(6)), op.at(7).get<float>());
        else if (k == "arrow") draw_arrow(im, f(1), f(2), f(3), colour(op.at(4)), colour(op.at(5)));
        else if (k == "shell" || k == "grabber") {
            // The real shell: glass_shade is the CPU copy of the fragment shader that draws the pill and the panel.
            const FRect r{op.at(1).get<float>(), op.at(2).get<float>(), op.at(3).get<float>(), op.at(4).get<float>()};
            const float radius = op.at(5).get<float>();
            const float level = op.at(6).get<float>(), t = op.at(7).get<float>();
            const GlassLook look = k == "shell" ? glass_look(accent, level, t) : grabber_look(level);
            for (int y = 0; y < h; ++y)
                for (int x = 0; x < w; ++x) {
                    const auto px = glass_shade(look, r, radius, (float(x) + .5f) / d, (float(y) + .5f) / d, 1.f);
                    auto* out = &im.rgba[(std::size_t(y) * w + x) * 4];
                    const float a = 1.f - float(out[3]) / 255.f;
                    for (int c = 0; c < 3; ++c) out[c] = std::uint8_t(std::lround(std::min(1.f, float(out[c]) / 255.f + px[std::size_t(c)] * a) * 255.f));
                    out[3] = std::uint8_t(std::lround(std::min(1.f, float(out[3]) / 255.f + px[3] * a) * 255.f));
                }
        } else { std::fprintf(stderr, "unknown op %s\n", k.c_str()); std::exit(1); }
    }
    return im;
}

// The real placement panel's pixels in several states: compared across a change that must not move one of them.
int placement_pixels(const std::string& path, const std::string& hex) {
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::fprintf(stderr, "no font: %s\n", why.c_str()); return 1; }
    std::ofstream f(path, std::ios::binary);
    for (int n = 0; n < 8; ++n) {
        PlacementMenuState s;
        s.open = true; s.desktop = 1; s.field = std::uint32_t(n % 4); s.count = 3;
        s.placements[1] = MenuPlacement{160, 16, 45, n * 10};
        s.scale = 125; s.scales = 0xff; s.resolution_w = 1920; s.resolution_h = 1200; s.background = std::uint8_t(n % 3); s.brightness = std::uint16_t(100 + n);
        s.picker = n == 1; s.resolution_picker = n == 2; s.background_picker = n == 3; s.wide = n == 4; s.armed = n == 5; s.busy = n == 6;
        for (const HudFont* use : std::array<const HudFont*, 2>{font.get(), nullptr}) {
            const HudImage im = render_glass_panel(s, use, parse_hex(hex));
            f.write(reinterpret_cast<const char*>(im.rgba.data()), std::streamsize(im.rgba.size()));
        }
    }
    return 0;
}

int raster_only(const std::string& path) {
    const json job = json::parse(std::ifstream(path));
    const auto accent = parse_hex(job.at("accent"));
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::fprintf(stderr, "no font: %s\n", why.c_str()); return 1; }
    int n = 0;
    for (const auto& o : job.at("images")) {
        const HudImage im = raster(o, accent, *font);
        std::ofstream f(o.at("out").get<std::string>(), std::ios::binary);
        f.write(reinterpret_cast<const char*>(im.rgba.data()), std::streamsize(im.rgba.size()));
        ++n;
    }
    std::printf("%d images\n", n);
    return 0;
}

// --- GL ------------------------------------------------------------------------------------------------------------
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
    "precision mediump float; varying vec2 v; uniform sampler2D tex; uniform float alpha; void main() { gl_FragColor = texture2D(tex, v) * alpha; }\n";
constexpr const char* kColorVertex = "attribute vec3 pos;\n" PROJECT "void main() { gl_Position = project(pos); }\n";
constexpr const char* kColorFragment = "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }\n";

struct Program {
    GLuint id = 0; GLint pos = -1, uv = -1, view = -1, half_fov = -1, color = -1, alpha = -1;
    void use(const std::array<float, 9>& v, const std::array<float, 2>& half) const {
        const std::array<float, 9> column_major{v[0], v[3], v[6], v[1], v[4], v[7], v[2], v[5], v[8]};  // Mat3 is row-major
        glUseProgram(id);
        glUniformMatrix3fv(view, 1, GL_FALSE, column_major.data()); glUniform2f(half_fov, half[0], half[1]);
    }
};
Program program(const char* vertex, const char* fragment) {
    Program p; p.id = link(vertex, fragment);
    p.pos = glGetAttribLocation(p.id, "pos"); p.uv = glGetAttribLocation(p.id, "uv");
    p.view = glGetUniformLocation(p.id, "view"); p.half_fov = glGetUniformLocation(p.id, "half_fov");
    p.color = glGetUniformLocation(p.id, "color"); p.alpha = glGetUniformLocation(p.id, "alpha");
    return p;
}
std::vector<std::uint8_t> read_file(const std::string& path, std::size_t want) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::uint8_t> out(want);
    if (!f.read(reinterpret_cast<char*>(out.data()), std::streamsize(want))) { std::fprintf(stderr, "cannot read %zu bytes of %s\n", want, path.c_str()); std::exit(1); }
    return out;
}
GLuint texture_of(const HudImage& im) {
    GLuint tex = 0;
    glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, GLsizei(im.width), GLsizei(im.height), 0, GL_RGBA, GL_UNSIGNED_BYTE, im.rgba.data());
    return tex;
}
void disable_attribs() {
    GLint n = 0; glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &n);
    for (GLint i = 0; i < n; ++i) glDisableVertexAttribArray(GLuint(i));
}
// `r` on the surface: the tile's mesh, or the glass's (it keeps its size and ignores the roll). `box`: part of the bound image.
void draw_mesh(const Program& p, const std::vector<MeshVertex>& mesh, const FRect& r, GLuint tex, const AtlasBox& box, float alpha) {
    std::vector<GLfloat> v;
    for (const auto& m : mesh) {
        const float s = (m.px - r.x) / r.width, t = (m.py - r.y) / r.height;
        v.insert(v.end(), {m.world.x, m.world.y, m.world.z, box.u0 + s * (box.u1 - box.u0), box.v0 + t * (box.v1 - box.v0)});
    }
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1f(p.alpha, alpha);
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
void draw_dot(const Program& p, const Canvas& c, float x, float y) {
    const double wx = (2.0 * x / c.view_w - 1) * c.half_w, wy = (1 - 2.0 * y / c.view_h) * c.half_h;
    const float az = float(std::atan2(wx, 1.0)), el = float(std::atan2(wy, std::hypot(wx, 1.0)));
    draw_points(p, direction_disc(c, x, y, az, el, kFreePointerRadiusPx + kFreePointerRingPx), GL_TRIANGLE_FAN, 0.f);
    draw_points(p, direction_disc(c, x, y, az, el, kFreePointerRadiusPx), GL_TRIANGLE_FAN, kFreePointerLevel);
}
std::array<float, 3> eye_of(const Mat3& m, const Vec3& p) {
    return {m[0] * p.x + m[1] * p.y + m[2] * p.z, m[3] * p.x + m[4] * p.y + m[5] * p.z, m[6] * p.x + m[7] * p.y + m[8] * p.z};
}
// Where a mesh lands on the canvas under `view`: bounding box in canvas px, and whether every vertex is inside the view.
struct Bounds { float x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f; bool behind = false; };
Bounds project(const Canvas& c, const std::vector<MeshVertex>& mesh, const Mat3& view) {
    Bounds b;
    for (const auto& v : mesh) {
        const auto e = eye_of(view, v.world);
        if (e[2] >= -1e-6f) { b.behind = true; continue; }
        const float x = (e[0] / -e[2] / c.half_w + 1) * c.view_w / 2, y = (1 - e[1] / -e[2] / c.half_h) * c.view_h / 2;
        b.x0 = std::min(b.x0, x); b.y0 = std::min(b.y0, y); b.x1 = std::max(b.x1, x); b.y1 = std::max(b.y1, y);
    }
    return b;
}
OptionsMenuState options_state(const json& j) {
    OptionsMenuState s;
    s.open = true; s.monitor = j.at("monitor"); s.scope = j.at("scope"); s.category = j.at("category");
    s.status = j.at("status"); s.message = j.at("message"); s.focus = j.at("focus"); s.scroll = j.at("scroll");
    for (const auto& r : j.at("rows")) {
        OptionsRow o;
        o.id = r.at("id"); o.label = r.at("label"); o.scope = r.at("scope"); o.kind = r.at("kind"); o.value = r.at("value");
        o.unit = r.value("unit", ""); o.minimum = r.value("minimum", 0.0); o.maximum = r.value("maximum", 0.0); o.step = r.value("step", 0.0);
        o.enabled = r.at("enabled");
        s.rows.push_back(o);
    }
    return s;
}
PlacementMenuState placement_state(const json& j, const std::vector<json>& desktops) {
    PlacementMenuState s;
    s.open = true; s.desktop = j.at("desktop"); s.field = j.value("field", 0u); s.count = std::uint32_t(desktops.size());
    for (std::size_t i = 0; i < desktops.size() && i < s.placements.size(); ++i) {
        const auto& p = desktops[i].at("placement");
        s.placements[i] = MenuPlacement{p.at("x").get<std::int32_t>(), p.at("y").get<std::int32_t>(), p.at("distance").get<std::int32_t>(), p.at("curvature").get<std::int32_t>()};
    }
    s.scale = j.value("scale_percent", std::uint16_t(125)); s.scales = 0xff;
    s.resolution_w = j.value("res_w", std::uint16_t(1920)); s.resolution_h = j.value("res_h", std::uint16_t(1200));
    s.background = j.value("background", std::uint8_t(0));
    return s;
}

int render(const std::string& path) {
    const json job = json::parse(std::ifstream(path));
    const auto accent = parse_hex(job.at("accent"));
    const std::string hex = job.at("accent");
    const int scale = job.value("scale", 2);
    const auto half = half_fov();
    const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::fprintf(stderr, "no font: %s\n", why.c_str()); return 1; }
    const std::size_t sources = job.at("sources").size();
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, sources, why);
    if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
    const std::uint32_t buffer_w = kCanvasW * std::uint32_t(scale), buffer_h = kCanvasH * std::uint32_t(scale);
    gl->resize(kCanvasW, kCanvasH, buffer_w, buffer_h);
    gl->set_accent(std::uint8_t(std::lround(accent[0] * 255)), std::uint8_t(std::lround(accent[1] * 255)), std::uint8_t(std::lround(accent[2] * 255)));
    const auto overlay_program = program(kOverlayVertex, kOverlayFragment), dot_program = program(kColorVertex, kColorFragment);
    const HudImage atlas = render_handle_atlas(accent, font.get());
    const GLuint atlas_tex = texture_of(atlas);
    const AtlasBox whole{0, 0, 1, 1};
    std::uint64_t generation = 2'000'000;
    for (const auto& frame : job.at("frames")) {
        // Sources per frame: desktops are the same files across a job, so each upload is a real upload of that frame's sizes.
        const auto& desktops = frame.at("desktops");
        for (std::size_t i = 0; i < desktops.size(); ++i) {
            const std::uint32_t w = desktops[i].at("w"), h = desktops[i].at("h");
            const auto px = read_file(desktops[i].at("bgra"), std::size_t(w) * h * 4);
            if (!gl->upload(i, px.data(), w, h, w * 4)) { std::fputs("upload failed\n", stderr); return 1; }
        }
        const Scene s = scene_of(frame);
        Mat3 view = scene_view(s);
        const int selected = s.selected;
        GlassFrame glass;
        glass.pills = frame.value("pills", true);
        glass.maximized = frame.value("real_restore", false);
        for (std::size_t i = 0; i < desktops.size(); ++i) glass.bars[i] = frame.value("real_handles", false);
        if (frame.contains("hover_pill")) { glass.hover = frame["hover_pill"]; glass.hover_level = kGlassHover; }
        std::vector<json> desktop_list(desktops.begin(), desktops.end());
        HudImage content;
        int panel = -1;
        float pitch = 0;
        if (frame.contains("real_placement")) {
            panel = frame["real_placement"].at("desktop");
            content = render_glass_panel(placement_state(frame["real_placement"], desktop_list), font.get(), accent);
            glass.expand[std::size_t(panel)] = 1.f;
            pitch = glass_panel_shift(canvas, s.tiles[std::size_t(panel)], s.placements[std::size_t(panel)]);
        }
        if (frame.contains("real_options")) {
            panel = frame["real_options"].at("desktop");
            content = render_options_panel(options_state(frame["real_options"]));  // as Presentation::set_options_menu: no font
            glass.options = true; glass.expand.fill(0); glass.expand[std::size_t(panel)] = 1.f;
            pitch = glass_panel_shift(canvas, s.tiles[std::size_t(panel)], s.placements[std::size_t(panel)], true);
        }
        if (panel >= 0) {
            glass.top = glass.content = panel;
            glass.image = &content; glass.generation = ++generation; glass.content_alpha = 1.f;
        }
        // A mock panel pitches the view as the real one does (glass_panel_shift's rule), for its own height.
        json report = {{"out", frame.at("out")}, {"overlays", json::array()}};
        if (frame.contains("pitch_for")) {
            const auto& pf = frame["pitch_for"];
            const FRect r{pf.at(0).get<float>(), pf.at(1).get<float>(), pf.at(2).get<float>(), pf.at(3).get<float>()};
            const auto d = std::size_t(frame.at("pitch_desktop").get<int>());
            const auto mesh = glass_mesh(canvas, s.tiles[d], s.placements[d], r);
            const Mat3 aimed = view;
            for (float a = 0; a <= kPanelShiftMax + 1e-4f; a += 0.002f) {
                pitch = a;
                const Bounds b = project(canvas, mesh, glass_view_shift(aimed, a));
                if (b.y1 <= canvas.view_h - kPanelShiftMarginPx) break;
            }
        }
        if (pitch > 0) view = glass_view_shift(view, pitch);
        report["pitch"] = pitch;
        gl->set_glass(glass);
        const bool dot = frame.contains("dot");
        if (frame.contains("real_dot")) {
            const auto& d = frame["real_dot"];
            gl->set_free_pointer(true, d.at(0).get<float>(), d.at(1).get<float>(), kFreePointerLevel);
        } else gl->set_free_pointer(false, 0, 0, 0);
        const float highlight_alpha = 1.f - s.zoom;
        if (!gl->draw_and_swap(s.tiles, s.placements, selected, frame.value("highlight", highlight_alpha), view, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }

        // The compositor left its framebuffer bound and current (create_offscreen). Its programs and meshes are untouched.
        disable_attribs();
        glBindBuffer(GL_ARRAY_BUFFER, 0);
        glActiveTexture(GL_TEXTURE0);
        glEnable(GL_BLEND); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        overlay_program.use(view, half);
        // The bar, caps, 3D button and gear: the compositor's own atlas and meshes, without the Maximize it draws with them.
        if (frame.value("handles", false))
            for (std::size_t i = 0; i < s.tiles.size(); ++i) {
                const FRect& t = s.tiles[i];
                auto image = [&](const FRect& r, const AtlasBox& box, bool on_glass = false) {
                    draw_mesh(overlay_program, on_glass ? glass_mesh(canvas, t, s.placements[i], r) : tile_mesh(canvas, t, s.placements[i], r), r, atlas_tex, box, 1.f);
                };
                for (std::uint32_t corner = 0; corner < 4; ++corner) image(corner_cap_rect(t, corner), corner_cap_box(corner, false, false));
                if (frame.value("gear", true)) image(options_gear(t), options_gear_box(), true);
                image(level_bar_rect(t), level_bar_box(LevelSegment::none, false));
                const FRect main = immersive_main_rect(t), tail = immersive_tail_rect(t);
                if (main.width > 0) image(main, immersive_main_box(false, false));
                if (tail.width > 0) image(tail, immersive_tail_box(false, false));
            }
        for (const auto& o : frame.value("overlays", json::array())) {
            const HudImage im = raster(o, accent, *font);
            const GLuint tex = texture_of(im);
            const auto d = std::size_t(o.value("desktop", selected));
            // `rect` lays the image out at another size than it was drawn, as the bar is scaled with its desktop.
            const FRect r = o.contains("rect") ? FRect{o["rect"][0].get<float>(), o["rect"][1].get<float>(), o["rect"][2].get<float>(), o["rect"][3].get<float>()}
                                               : FRect{o.at("x").get<float>(), o.at("y").get<float>(), o.at("w").get<float>(), o.at("h").get<float>()};
            const auto mesh = o.value("space", "tile") == "glass" ? glass_mesh(canvas, s.tiles[d], s.placements[d], r) : tile_mesh(canvas, s.tiles[d], s.placements[d], r);
            draw_mesh(overlay_program, mesh, r, tex, whole, o.value("alpha", 1.f));
            glDeleteTextures(1, &tex);
            const Bounds b = project(canvas, mesh, view);
            report["overlays"].push_back({{"name", o.value("name", "")}, {"box", {b.x0, b.y0, b.x1, b.y1}}, {"behind", b.behind}});
        }
        glDisable(GL_BLEND);
        if (dot) {
            const auto& d = frame["dot"];
            dot_program.use(std::array<float, 9>{1, 0, 0, 0, 1, 0, 0, 0, 1}, half);  // the dot is on the canvas, not on the desktops
            draw_dot(dot_program, canvas, d.at(0).get<float>(), d.at(1).get<float>());
        }
        glFinish();
        const auto crop = frame.value("crop", std::vector<int>{0, 0, int(kCanvasW), int(kCanvasH)});
        const int cx = crop[0], cy = crop[1], cw = crop[2], ch = crop[3];
        const GLsizei read_w = cw * scale, read_h = ch * scale;
        std::vector<std::uint8_t> rgba(std::size_t(read_w) * read_h * 4);
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        glReadPixels(cx * scale, (int(kCanvasH) - cy - ch) * scale, read_w, read_h, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        if (glGetError() != GL_NO_ERROR) { std::fputs("gl error\n", stderr); return 1; }
        std::ofstream f(frame.at("out").get<std::string>(), std::ios::binary);
        for (GLsizei y = read_h - 1; y >= 0; --y)  // GL's first row is the bottom one
            f.write(reinterpret_cast<const char*>(&rgba[std::size_t(y) * std::size_t(read_w) * 4]), std::streamsize(read_w) * 4);
        std::cout << report.dump() << "\n";
    }
    std::cout << "{\"report\":" << json(gl->report()).dump() << "}\n";
    return 0;
}
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    if (argc == 3) {
        if (mode == "info") return info(argv[2]);
        if (mode == "measure") return measure(argv[2]);
        if (mode == "layout") return layout(argv[2]);
        if (mode == "raster") return raster_only(argv[2]);
        if (mode == "render") return render(argv[2]);
    }
    if (argc == 4 && mode == "placement") return placement_pixels(argv[2], argv[3]);
    std::fputs("usage: restyle_preview info <rrggbb> | measure <file> | layout <job> | raster <job> | render <job>\n", stderr);
    return 2;
}
