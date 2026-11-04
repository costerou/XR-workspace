// Switch animation mockups: frames of the real compositor, one command per line on stdin, raw RGBA on stdout.
//
//   switch_preview info <rrggbb>
//       JSON on stdout: canvas, FOV, the overview's tiles and the M3 scheme of that theme colour (glass_theme_scheme()).
//   switch_preview render <rrggbb> <desktops dir>
//       Reads desktop-N.bgra (1920 x 1080) as sources 0..2 and then these commands:
//
//   placements  x y distance curvature roll  (three times)   the desktops as placed (radians, factor)
//   start <selected> <t ms>                                  a new run: maximized on `selected`, follow mode, nothing moving
//   pan <from> <to> <side> <exit> <entry> <peek> <cross> <turn> <zoom>  <t ms>    ViewAnimator::set_pan, in EdgePan's units
//   target <selected> <center> <cut> <pan> <t ms>            ViewAnimator::set_target (maximized)
//   tick <t ms>                                              one step of everything, nothing drawn
//   frame <t ms> <a> <ua> <va> <b> <ub> <vb>                 one step, then one frame. The dot: during a pan the renderer's own
//                                                            (wayland.cpp pan_dot); otherwise on desktop a at (ua, va) of it,
//                                                            or on b once the zoom has moved on to b. a < 0: no dot
//   scene <highlight> <highlight alpha> <view: 9 floats> <dot x> <dot y> <n> { tile x y w h, placement x y distance curvature
//         roll, level } x n                                  one frame of exactly that: draw_and_swap's own arguments. dot x < 0:
//                                                            no dot. level scales that source's pixels
//
// `frame` is a run's frame loop: ViewAnimator, blended_layout, zoomed_placements and ModeController are the real ones, and
// Aim::focus / Aim::dot are wayland.cpp focus_aim / pan_dot word for word (they are private to its Impl), with the head still.
// `scene` is for designs that do not exist: what moves is scripted (make_animations.py), and how it is drawn is the compositor's.
#include <GLES2/gl2.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <numbers>
#include <optional>
#include <sstream>
#include <string>
#include <vector>
#include "gl.hpp"
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"

using namespace xr;

namespace {
constexpr std::uint32_t kCanvasW = 1920, kCanvasH = 1200, kDesktopW = 1920, kDesktopH = 1080, kDesktops = 3;
constexpr float kSpacing = 40.f;  // the overview's spacing, as gl_bench draws it

// gl_bench.cpp's camera: a 46 degree diagonal over the canvas, straight ahead.
std::array<float, 2> half_fov() {
    const double d = std::tan(46.0 * std::numbers::pi / 360.0), a = double(kCanvasW) / kCanvasH;
    return {float(d * a / std::sqrt(1 + a * a)), float(d / std::sqrt(1 + a * a))};
}
std::vector<Size> sizes() { return std::vector<Size>(kDesktops, Size{float(kDesktopW), float(kDesktopH)}); }

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

int info(const std::array<std::uint8_t, 3>& hex) {
    const std::array<float, 3> accent{hex[0] / 255.f, hex[1] / 255.f, hex[2] / 255.f};
    const MaterialScheme& m = glass_theme_scheme(accent);
    const auto half = half_fov();
    const auto s = sizes();
    const auto t = overview_layout(s, float(kCanvasW), float(kCanvasH), kSpacing);
    char head[120];
    std::snprintf(head, sizeof head, "{\"w\":%u,\"h\":%u,\"half\":[%.9f,%.9f],\"tiles\":[", kCanvasW, kCanvasH, half[0], half[1]);
    std::string out = head;
    for (std::size_t i = 0; i < t.size(); ++i) {
        char b[100];
        std::snprintf(b, sizeof b, "%s[%.3f,%.3f,%.3f,%.3f]", i ? "," : "", t[i].x, t[i].y, t[i].width, t[i].height);
        out += b;
    }
    out += "],\"scheme\":{";
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

std::vector<std::uint8_t> read_file(const std::string& path, std::size_t want) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::uint8_t> out(want);
    if (!f.read(reinterpret_cast<char*>(out.data()), std::streamsize(want))) { std::fprintf(stderr, "cannot read %zu bytes of %s\n", want, path.c_str()); std::exit(1); }
    return out;
}

// A run's view with a still head: maximized, follow mode. What wayland.cpp keeps in its Impl for the frame loop.
struct Run {
    ViewAnimator view;
    ModeController modes;
    std::array<Placement, kDesktops> placed{};
    Canvas canvas;
    explicit Run(const Canvas& c, const std::array<Placement, kDesktops>& p, std::uint32_t selected) : placed(p), canvas(c) {
        view.set_target(false, selected, std::uint32_t(kSpacing), 0);
        view.advance(10000);
        view.set_target(true, selected, std::uint32_t(kSpacing), 10000);
        view.advance(20000);
        modes.set_mode(ViewMode::follow, 20000);
        step(30000);
    }
    std::vector<FRect> overview() const { return overview_layout(sizes(), canvas.view_w, canvas.view_h, view.spacing()); }
    std::span<const Placement> placements() const { return {placed.data(), placed.size()}; }
    Quat focus_aim() const {  // wayland.cpp focus_aim
        const float w = focus_weight(view);
        if (!(w > 0.f) && !view.pan_active()) return Quat{};
        const auto over = overview();
        const auto ps = placements();
        if (view.pan_active()) {
            const auto from = focus_direction(canvas, over, ps, view.pan().from), to = focus_direction(canvas, over, ps, view.pan().to);
            Quat aim = aim_rotation(from[0], from[1]);
            const auto s = sizes();
            if (const auto path = edge_pan_path(canvas, s, view.spacing(), over, ps, view.pan()))
                aim = aim_turned(aim, {path->axis.x, path->axis.y, path->axis.z}, view.pan_turn());
            return view.pan_landing() ? quat_slerp(aim, aim_rotation(to[0], to[1]), view.turn()) : aim;
        }
        const auto d = focus_direction(canvas, over, ps, view.focus());
        return aim_rotation(w * d[0], w * d[1]);
    }
    std::optional<std::array<float, 2>> pan_dot(const std::vector<FRect>& tiles, const std::vector<Placement>& drawn) const {  // wayland.cpp pan_dot
        if (!view.pan_active() || view.pan_landing()) return std::nullopt;
        const auto over = overview();
        const auto s = sizes();
        const auto path = edge_pan_path(canvas, s, view.spacing(), over, placements(), view.pan());
        const auto& p = view.pan();
        if (!path || p.from >= tiles.size() || p.from >= drawn.size()) return std::nullopt;
        Vec3 start = path->from;
        if (view.zoom() > p.zoom) {
            const auto e = edge_point(tiles[p.from], p.side, p.exit);
            start = place_point(canvas, tiles[p.from], drawn[p.from], e[0], e[1]);
            const float len = std::sqrt(start.x * start.x + start.y * start.y + start.z * start.z);
            if (!(len > 0)) return std::nullopt;
            start = {start.x / len, start.y / len, start.z / len};
        }
        const Vec3 d = edge_pan_dot(*path, start, view.pan_cross());
        return std::array<float, 2>{std::atan2(d.x, -d.z), std::atan2(d.y, std::sqrt(d.x * d.x + d.z * d.z))};
    }
    Mat3 step(double t) {  // wayland.cpp's order: the view, its landed turns (anchor only, so dropped here), the aim, the modes
        view.advance(t);
        view.take_landed_turns();
        modes.set_aim(focus_aim());
        return view_from(Quat{}, modes.update(Quat{}, t));
    }
    std::vector<FRect> tiles() const { return blended_layout(sizes(), canvas.view_w, canvas.view_h, view.spacing(), view.zoom(), view.position()); }
    std::vector<Placement> drawn(const std::vector<FRect>& t) const {  // wayland.cpp resolved_placements: follow, so keep_size is false
        return zoomed_placements(canvas, overview(), t, placements(), view.zoom(), view.position(), false);
    }
};

class Output {
public:
    Output() : rgba_(std::size_t(kCanvasW) * kCanvasH * 4), rows_(rgba_.size()) {}
    bool send() {
        glFinish();
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        glReadPixels(0, 0, GLsizei(kCanvasW), GLsizei(kCanvasH), GL_RGBA, GL_UNSIGNED_BYTE, rgba_.data());
        if (glGetError() != GL_NO_ERROR) { std::fputs("gl error\n", stderr); return false; }
        const std::size_t row = std::size_t(kCanvasW) * 4;
        for (std::size_t y = 0; y < kCanvasH; ++y)  // GL's first row is the bottom one
            std::copy_n(&rgba_[(kCanvasH - 1 - y) * row], row, &rows_[y * row]);
        return std::fwrite(rows_.data(), 1, rows_.size(), stdout) == rows_.size() && std::fflush(stdout) == 0;
    }
private:
    std::vector<std::uint8_t> rgba_, rows_;
};

int render(const std::array<std::uint8_t, 3>& hex, const std::string& desktops) {
    std::string why;
    auto gl = GlCompositor::create_offscreen(kCanvasW, kCanvasH, kDesktops, why);
    if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
    gl->resize(kCanvasW, kCanvasH);
    gl->set_accent(hex[0], hex[1], hex[2]);
    const std::size_t bytes = std::size_t(kDesktopW) * kDesktopH * 4;
    std::array<std::vector<std::uint8_t>, kDesktops> pixels;
    std::array<float, kDesktops> levels{};
    auto upload = [&](std::size_t i, float level) {
        if (levels[i] == level) return true;
        levels[i] = level;
        if (level == 1.f) return gl->upload(i, pixels[i].data(), kDesktopW, kDesktopH, kDesktopW * 4);
        static std::vector<std::uint8_t> dim(bytes);
        const int scale = int(std::lround(level * 256));
        for (std::size_t p = 0; p < bytes; ++p) dim[p] = std::uint8_t((pixels[i][p] * scale) >> 8);
        return gl->upload(i, dim.data(), kDesktopW, kDesktopH, kDesktopW * 4);
    };
    for (std::size_t i = 0; i < kDesktops; ++i) {
        pixels[i] = read_file(desktops + "/desktop-" + std::to_string(i) + ".bgra", bytes);
        if (!upload(i, 1.f)) { std::fputs("upload failed\n", stderr); return 1; }
    }
    const auto half = half_fov();
    const Canvas canvas{float(kCanvasW), float(kCanvasH), half[0], half[1]};
    gl->set_glass(GlassFrame{});
    Output output;
    std::array<Placement, kDesktops> placed{};
    std::unique_ptr<Run> run;

    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op == "placements") {
            for (auto& p : placed) in >> p.x >> p.y >> p.distance >> p.curvature >> p.roll;
        } else if (op == "start") {
            std::uint32_t selected = 0; double t = 0;
            in >> selected >> t;
            for (std::size_t i = 0; i < kDesktops; ++i) upload(i, 1.f);
            run = std::make_unique<Run>(canvas, placed, selected);
            (void)t;
        } else if (op == "pan") {
            EdgePan p; p.active = true;
            int side = 0; double t = 0;
            in >> p.from >> p.to >> side >> p.exit >> p.entry >> p.peek >> p.cross >> p.turn >> p.zoom >> t;
            p.side = std::uint8_t(side);
            run->view.set_pan(p, t + 30000);
        } else if (op == "target") {
            std::uint32_t selected = 0; int center = 0, cut = 0, pan = 0; double t = 0;
            in >> selected >> center >> cut >> pan >> t;
            run->view.set_target(true, selected, std::uint32_t(kSpacing), t + 30000, center, cut, pan);
        } else if (op == "tick") {
            double t = 0;
            in >> t;
            run->step(t + 30000);
        } else if (op == "frame") {
            double t = 0; int a = -1, b = -1; float ua = 0, va = 0, ub = 0, vb = 0;
            in >> t >> a >> ua >> va >> b >> ub >> vb;
            const Mat3 view = run->step(t + 30000);
            const auto tiles = run->tiles();
            const auto drawn = run->drawn(tiles);
            if (const auto dot = run->pan_dot(tiles, drawn)) {
                gl->set_free_pointer(true, float(kCanvasW) / 2, float(kCanvasH) / 2, kFreePointerLevel, true, (*dot)[0] * float(180 / M_PI), (*dot)[1] * float(180 / M_PI), PointerLook::plain);
            } else if (a >= 0) {
                const bool on_b = b >= 0 && run->view.focus() == std::uint32_t(b);
                const auto d = std::size_t(on_b ? b : a);
                const FRect& r = tiles[d];
                const float u = on_b ? ub : ua, v = on_b ? vb : va;
                const Vec3 w = place_point(canvas, r, drawn[d], r.x + u * r.width, r.y + v * r.height);
                gl->set_free_pointer(true, float(kCanvasW) / 2, float(kCanvasH) / 2, kFreePointerLevel, true,
                                     std::atan2(w.x, -w.z) * float(180 / M_PI), std::atan2(w.y, std::sqrt(w.x * w.x + w.z * w.z)) * float(180 / M_PI), PointerLook::plain);
            } else {
                gl->set_free_pointer(false, 0, 0, 0);
            }
            if (!gl->draw_and_swap(tiles, drawn, int(run->view.focus()), 1.f - run->view.zoom(), view, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }
            if (!output.send()) return 1;
        } else if (op == "scene") {
            int highlight = -1, n = 0; float alpha = 0, dot_x = -1, dot_y = -1;
            Mat3 view{};
            in >> highlight >> alpha;
            for (auto& v : view) in >> v;
            in >> dot_x >> dot_y >> n;
            std::vector<FRect> tiles(static_cast<std::size_t>(n));
            std::vector<Placement> drawn(static_cast<std::size_t>(n));
            for (int i = 0; i < n; ++i) {
                auto& r = tiles[std::size_t(i)]; auto& p = drawn[std::size_t(i)];
                float level = 1;
                in >> r.x >> r.y >> r.width >> r.height >> p.x >> p.y >> p.distance >> p.curvature >> p.roll >> level;
                if (!upload(std::size_t(i), level)) { std::fputs("upload failed\n", stderr); return 1; }
            }
            gl->set_free_pointer(dot_x >= 0, dot_x, dot_y, kFreePointerLevel);
            if (!gl->draw_and_swap(tiles, drawn, highlight, alpha, view, half[0], half[1])) { std::fputs("draw failed\n", stderr); return 1; }
            if (!output.send()) return 1;
        } else if (!op.empty()) {
            std::fprintf(stderr, "unknown command %s\n", op.c_str());
            return 2;
        }
    }
    return 0;
}
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    std::array<std::uint8_t, 3> hex{};
    if (mode == "info" && argc == 3 && parse_hex(argv[2], hex)) return info(hex);
    if (mode == "render" && argc == 4 && parse_hex(argv[2], hex)) return render(hex, argv[3]);
    std::fputs("usage: switch_preview info <rrggbb>\n       switch_preview render <rrggbb> <desktops dir>   (commands on stdin)\n", stderr);
    return 2;
}
