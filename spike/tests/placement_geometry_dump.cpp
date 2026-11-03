// Prints world points of placed desktops (view.hpp place_point on glass.hpp placement_from_wire, so the wire units are part
// of it) as one JSON line, for placement_contract_test.py, which checks that tools/native_spike/orbit.py place_point (the
// supervisor's pill drag, hit testing and no-overlap guard) mirrors them: every canvas x tile x wire placement x point.
// Every desktop always faces you (a billboard toward the eye), so off-centre tiles are where the two could differ most.
#include "xr/glass.hpp"
#include "xr/view.hpp"
#include <cmath>
#include <cstdio>
#include <iterator>
#include <tuple>
#include <string>
#include <vector>

using namespace xr;
namespace {
std::string num(float v) { char b[32]; std::snprintf(b, sizeof b, "%.9g", double(v)); return b; }  // round-trips a float
}

int main() {
    // Canvases (px and the half-FOV tangents) and their tiles: the overview's real row of three, and hand-made ones off
    // the canvas's grid (fractional): partly off the left edge, up and right, down and left, centred, and far off to the
    // right and above (a zoomed layout's neighbour).
    const Canvas canvases[] = {{1920, 1080, 0.41421356f, 0.23300762f}, {3840, 1080, 0.8f, 0.225f}, {1280, 800, 0.5f, 0.3125f}};
    const Size sources[] = {{1920, 1080}, {2560, 1440}, {1080, 1920}};
    // Wire placements: the orbit's x / y (tenths of a degree) at the slot, small, large, both poles and behind you, combined
    // with the rest of the placement (distance and curvature).
    const std::int32_t xs[] = {0, 37, -450, 900, -900, 1800, -1800, 1234};
    const std::int32_t ys[] = {0, 300, -899, 900};
    const MenuPlacement rests[] = {{0, 0, 100, 0}, {0, 0, 50, 120}, {0, 0, 200, 0}, {0, 0, 137, 45}, {0, 0, 200, 120}, {0, 0, 73, 7}};
    std::string out = "{\"cases\":[";
    bool first = true;
    for (const Canvas& c : canvases) {
        std::vector<FRect> tiles = overview_layout(sources, c.view_w, c.view_h, 40);
        tiles.push_back({-200.5f, 410.25f, 812, 457});
        tiles.push_back({c.view_w * 0.6f + 0.5f, 20.25f, 300.75f, 168.5f});
        tiles.push_back({33.3f, c.view_h - 150.7f, 410.1f, 230.6f});
        tiles.push_back({c.view_w / 2 - 400, c.view_h / 2 - 225, 800, 450});
        tiles.push_back({c.view_w + 40.5f, -c.view_h * 0.4f, c.view_w, c.view_h * 0.9f});
        for (const FRect& t : tiles) {
            // Corners, centre, and points outside the tile (its frame / halo, the pill strip under it, far off).
            const std::pair<float, float> points[] = {
                {t.x, t.y}, {t.x + t.width, t.y}, {t.x, t.y + t.height}, {t.x + t.width, t.y + t.height},
                {t.x + t.width / 2, t.y + t.height / 2}, {t.x + t.width * 0.3f, t.y + t.height * 0.8f},
                {t.x - 50, t.y + t.height / 3}, {t.x + t.width + 30, t.y + t.height + 24}, {t.x + t.width / 4, t.y - 100}, {0, 0},
                {c.view_w, c.view_h}};
            // The levelling roll (hundredths of a degree, orbit.py's optional 'roll'): none for every rest, then a few on the
            // first two rests.
            const std::int32_t rolls[] = {0, 1500, -4250, 9000};
            for (const std::int32_t roll : rolls)
            for (std::size_t r = 0; r < std::size(rests); ++r)
                for (const std::int32_t x : xs)
                    for (const std::int32_t y : ys) {
                        if (roll && r > 1) continue;
                        MenuPlacement wire = rests[r];
                        wire[0] = x;
                        wire[1] = y;
                        Placement p = placement_from_wire(wire);
                        p.roll = float(roll) / 100.f * float(M_PI) / 180.f;
                        out += first ? "{" : ",{";
                        first = false;
                        out += "\"canvas\":[" + num(c.view_w) + "," + num(c.view_h) + "," + num(c.half_w) + "," + num(c.half_h) + "]";
                        out += ",\"tile\":[" + num(t.x) + "," + num(t.y) + "," + num(t.width) + "," + num(t.height) + "]";
                        out += ",\"placement\":{";
                        const char* keys[] = {"x", "y", "distance", "curvature"};
                        static_assert(std::size(keys) == std::tuple_size_v<MenuPlacement>);
                        for (std::size_t k = 0; k < wire.size(); ++k) out += (k ? ",\"" : "\"") + std::string(keys[k]) + "\":" + std::to_string(wire[k]);
                        if (roll) out += ",\"roll\":" + std::to_string(roll);
                        out += "},\"points\":[";
                        bool first_point = true;
                        for (const auto& [px, py] : points) {
                            const Vec3 w = place_point(c, t, p, px, py);
                            out += (first_point ? "[" : ",[") + num(px) + "," + num(py) + "," + num(w.x) + "," + num(w.y) + "," + num(w.z) + "]";
                            first_point = false;
                        }
                        out += "]}";
                    }
        }
    }
    std::printf("%s]}\n", out.c_str());
    return 0;
}
