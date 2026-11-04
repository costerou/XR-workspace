// Frames of the edge pan from the real compositor on surfaceless EGL, for any layout: spike/tests/edge_pan_run.hpp's commands on stdin,
// raw RGBA on stdout for each frame.
//
//   pan_clip render <rrggbb> <desktops dir>
//       desktop-N.bgra in the directory are the desktops' pixels, each its layout's source size. Besides edge_pan_run.hpp's:
//   frame <t ms> <a> <u> <v>    one step, one frame. The dot: during a pan the renderer's own (pan_dot); otherwise on desktop a at
//                               (u, v) of it, as a run shows the real cursor there. a < 0: no dot
#include <GLES2/gl2.h>
#include <fstream>
#include "edge_pan_run.hpp"
#include "gl.hpp"
#include "xr/glass.hpp"

using namespace xr;
using namespace xr::pan_run;

namespace {
bool parse_hex(const std::string& s, std::array<std::uint8_t, 3>& rgb) {
    if (s.size() != 6) return false;
    char* end = nullptr;
    const unsigned long v = std::strtoul(s.c_str(), &end, 16);
    if (*end) return false;
    rgb = {std::uint8_t(v >> 16), std::uint8_t(v >> 8), std::uint8_t(v)};
    return true;
}

std::vector<std::uint8_t> read_file(const std::string& path, std::size_t want) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::uint8_t> out(want);
    if (!f.read(reinterpret_cast<char*>(out.data()), std::streamsize(want))) { std::fprintf(stderr, "cannot read %zu bytes of %s\n", want, path.c_str()); std::exit(1); }
    return out;
}

bool send(std::uint32_t w, std::uint32_t h) {
    static std::vector<std::uint8_t> rgba, rows;
    const std::size_t row = std::size_t(w) * 4;
    rgba.resize(row * h); rows.resize(row * h);
    glFinish();
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, GLsizei(w), GLsizei(h), GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    if (glGetError() != GL_NO_ERROR) { std::fputs("gl error\n", stderr); return false; }
    for (std::size_t y = 0; y < h; ++y) std::copy_n(&rgba[(h - 1 - y) * row], row, &rows[y * row]);  // GL's first row is the bottom one
    return std::fwrite(rows.data(), 1, rows.size(), stdout) == rows.size() && std::fflush(stdout) == 0;
}

int render(const std::array<std::uint8_t, 3>& hex, const std::string& dir) {
    Layout layout;
    std::unique_ptr<Run> run;
    std::unique_ptr<GlCompositor> gl;
    Mat3 view{1, 0, 0, 0, 1, 0, 0, 0, 1};
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op.empty()) continue;
        if (op == "frame") {
            double t = 0; int a = -1; float u = 0, v = 0;
            in >> t >> a >> u >> v;
            view = run->step(t + 30000);
            const auto tiles = run->tiles();
            const auto drawn = run->drawn(tiles);
            const float w = layout.canvas.view_w, h = layout.canvas.view_h;
            constexpr float deg = float(180 / M_PI);
            if (const auto dot = run->pan_dot(tiles, drawn)) {
                gl->set_free_pointer(true, w / 2, h / 2, kFreePointerLevel, true, (*dot)[0] * deg, (*dot)[1] * deg, PointerLook::plain);
            } else if (a >= 0) {
                const FRect& r = tiles[std::size_t(a)];
                const Vec3 p = place_point(layout.canvas, r, drawn[std::size_t(a)], r.x + u * r.width, r.y + v * r.height);
                gl->set_free_pointer(true, w / 2, h / 2, kFreePointerLevel, true, std::atan2(p.x, -p.z) * deg, std::atan2(p.y, std::sqrt(p.x * p.x + p.z * p.z)) * deg, PointerLook::plain);
            } else {
                gl->set_free_pointer(false, 0, 0, 0);
            }
            if (!gl->draw_and_swap(tiles, drawn, run->highlight(), run->highlight_alpha(), view, layout.canvas.half_w, layout.canvas.half_h)) { std::fputs("draw failed\n", stderr); return 1; }
            if (!send(std::uint32_t(w), std::uint32_t(h))) return 1;
        } else if (control(op, in, layout, run, view)) {
            if (op != "layout") continue;
            const auto n = std::uint32_t(layout.sources.size());
            const auto w = std::uint32_t(layout.canvas.view_w), h = std::uint32_t(layout.canvas.view_h);
            std::string why;
            gl = GlCompositor::create_offscreen(w, h, n, why);
            if (!gl) { std::fprintf(stderr, "no offscreen compositor: %s\n", why.c_str()); return 1; }
            gl->resize(w, h);
            gl->set_accent(hex[0], hex[1], hex[2]);
            gl->set_glass(GlassFrame{});
            for (std::uint32_t i = 0; i < n; ++i) {
                const auto sw = std::uint32_t(layout.sources[i].w), sh = std::uint32_t(layout.sources[i].h);
                const auto pixels = read_file(dir + "/desktop-" + std::to_string(i) + ".bgra", std::size_t(sw) * sh * 4);
                if (!gl->upload(i, pixels.data(), sw, sh, sw * 4)) { std::fputs("upload failed\n", stderr); return 1; }
            }
        } else {
            std::fprintf(stderr, "unknown command %s\n", op.c_str());
            return 2;
        }
    }
    return 0;
}
}

int main(int argc, char** argv) {
    std::array<std::uint8_t, 3> hex{};
    if (argc == 4 && std::string(argv[1]) == "render" && parse_hex(argv[2], hex)) return render(hex, argv[3]);
    std::fputs("usage: pan_clip render <rrggbb> <desktops dir>   (commands on stdin)\n", stderr);
    return 2;
}
