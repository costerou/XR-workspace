// What the edge pan draws, frame by frame, with no GL: edge_pan_run.hpp's commands on stdin, and for each `frame <t ms>` one JSON
// line. tools/edge_pan_clips/motion.py reads it; ctest edge_pan_motion and the clips' metrics are built on it.
//
// For each desktop: `seen` its width and height as seen (canvas px at unit distance: tile over distance from the eye), `at` where its
// centre is in the view ({azimuth, elevation} radians from straight ahead), `roll` its turn about the line of sight as seen
// (radians, counter-clockwise), `own_roll` the levelling roll it is drawn with, `corners` its four corners in eye space. The view: `aim` the canvas direction straight ahead.
#include "edge_pan_run.hpp"

using namespace xr;
using namespace xr::pan_run;

namespace {
Vec3 eye(const Mat3& v, const Vec3& p) {
    return {v[0] * p.x + v[1] * p.y + v[2] * p.z, v[3] * p.x + v[4] * p.y + v[5] * p.z, v[6] * p.x + v[7] * p.y + v[8] * p.z};
}
float dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
Vec3 cross(const Vec3& a, const Vec3& b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
Vec3 minus(const Vec3& a, const Vec3& b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }

void desktop(const Run& run, const FRect& r, const Placement& p, const Mat3& view) {
    const Canvas& c = run.l.canvas;
    const float cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    const Vec3 middle = eye(view, place_point(c, r, p, cx, cy)), top = eye(view, place_point(c, r, p, cx, cy - 1));
    const float far = std::sqrt(dot(middle, middle));
    const Vec3 n{middle.x / far, middle.y / far, middle.z / far};
    // Its up against the view's, both on the plane square to the line of sight.
    const Vec3 up = minus(top, middle);
    const float along = dot(up, n), up_y = n.y;
    const Vec3 own = minus(up, Vec3{n.x * along, n.y * along, n.z * along});
    const Vec3 sky = minus(Vec3{0, 1, 0}, Vec3{n.x * up_y, n.y * up_y, n.z * up_y});
    const float roll = std::atan2(dot(cross(own, sky), n), dot(own, sky));
    std::printf("{\"seen\":[%.4f,%.4f],\"at\":[%.6f,%.6f],\"roll\":%.6f,\"tile\":[%.3f,%.3f,%.3f,%.3f],\"distance\":%.5f,\"own_roll\":%.6f,\"corners\":[", r.width / far, r.height / far,
                std::atan2(middle.x, -middle.z), std::atan2(middle.y, std::sqrt(middle.x * middle.x + middle.z * middle.z)), roll, r.x, r.y, r.width, r.height, p.distance, p.roll);
    const float xs[] = {r.x, r.x + r.width, r.x + r.width, r.x}, ys[] = {r.y, r.y, r.y + r.height, r.y + r.height};
    for (int i = 0; i < 4; ++i) {
        const Vec3 q = eye(view, place_point(c, r, p, xs[i], ys[i]));
        std::printf("%s[%.5f,%.5f,%.5f]", i ? "," : "", q.x, q.y, q.z);
    }
    std::printf("]}");
}

void frame(Run& run, double t, const Mat3& view) {
    const auto tiles = run.tiles();
    const auto drawn = run.drawn(tiles);
    const Canvas& c = run.l.canvas;
    const auto pan = run.view.pan_layers();  // gl.cpp draw_and_swap's order
    auto order = pan ? pan_draw_order(draw_order(c, tiles, drawn), *pan) : zoom_draw_order(draw_order(c, tiles, drawn), run.view.zoom(), run.view.focus());
    // The canvas direction straight ahead: the view's inverse on (0, 0, -1).
    const Vec3 ahead{-view[6], -view[7], -view[8]};
    std::printf("{\"t\":%.3f,\"zoom\":%.5f,\"focus\":%u,\"switching\":%d,\"pan\":%d,\"landing\":%d,\"animating\":%d,\"turn\":%.5f,\"left_zoom\":%.5f,\"alpha\":%.4f,\"left_alpha\":%.4f,\"aim\":[%.6f,%.6f,%.6f],\"order\":[",
                t, run.view.zoom(), run.view.focus(), run.view.switching(), run.view.pan_active(), run.view.pan_landing(), run.view.animating(), run.view.pan_turn(),
                run.view.left_zoom(), run.view.highlight_alpha(), pan ? pan->left_alpha : 0.f, ahead.x, ahead.y, ahead.z);
    for (std::size_t i = 0; i < order.size(); ++i) std::printf("%s%zu", i ? "," : "", order[i]);
    std::printf("],\"dot\":");
    if (const auto dot = run.pan_dot(tiles, drawn)) std::printf("[%.6f,%.6f]", (*dot)[0], (*dot)[1]); else std::printf("null");
    std::printf(",\"desktops\":[");
    for (std::size_t i = 0; i < tiles.size(); ++i) {
        if (i) std::printf(",");
        desktop(run, tiles[i], drawn[i], view);
    }
    std::printf("]}\n");
}
}

int main() {
    Layout layout;
    std::unique_ptr<Run> run;
    Mat3 view{1, 0, 0, 0, 1, 0, 0, 0, 1};
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string op;
        in >> op;
        if (op.empty()) continue;
        if (op == "frame") {
            double t = 0;
            in >> t;
            view = run->step(t + 30000);
            frame(*run, t, view);
        } else if (!control(op, in, layout, run, view)) {
            std::fprintf(stderr, "unknown command %s\n", op.c_str());
            return 2;
        }
    }
    return 0;
}
