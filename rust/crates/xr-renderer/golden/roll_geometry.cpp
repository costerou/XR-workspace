// Writes golden/roll_geometry.txt: the line wayland.cpp Presentation::take_roll_geometry hands the supervisor, built the
// way it builds it (overview_layout of the settled sizes at the target spacing, canvas_up of the level reference and the
// anchor, desktop_tilt of each target placement with its roll taken out), over fixed references, anchors, desktops and
// placements. Per case: "case <name>" and the line. src/shell/roll.rs's golden test builds the same and compares bytes.
// The references are fixed (the pose half, PoseState::reference, is the tracking module's): level_frame_test.cpp's odd
// driver references and tipped heads, roll_level_test.cpp's tipped and pitched recentres, and a turned anchor.
// From the repository root:
//   g++ -std=c++20 -O1 -Ispike/include rust/crates/xr-renderer/golden/roll_geometry.cpp spike/src/view.cpp spike/src/pose.cpp \
//       -o /tmp/roll-geometry-golden && /tmp/roll-geometry-golden > rust/crates/xr-renderer/golden/roll_geometry.txt
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <optional>
#include <string>
#include <vector>

using namespace xr;
namespace {
constexpr float kPi = 3.14159265358979f;
float rad(float deg) { return deg * kPi / 180.f; }
Quat axis_angle(float x, float y, float z, float degrees) {
    const float n = std::sqrt(x * x + y * y + z * z), h = rad(degrees) / 2, s = std::sin(h) / n;
    return {x * s, y * s, z * s, std::cos(h)};
}
Quat head_at(float yaw, float pitch, float tip) {
    return quat_mul(quat_mul(axis_angle(0, 0, 1, yaw), axis_angle(0, 1, 0, pitch)), axis_angle(1, 0, 0, tip));
}

// take_roll_geometry's body past its rate limit; `reference` empty: not measured (every tilt null).
std::string line(const Canvas& canvas, std::size_t desktops, const std::vector<Size>& settled, float spacing,
                 const std::vector<Placement>& placements, std::optional<Quat> reference, Quat anchor) {
    const bool measured = reference.has_value();
    std::string line = "{\"roll_geometry\":{\"tilt\":[";
    const auto tiles = measured ? overview_layout(settled, canvas.view_w, canvas.view_h, spacing) : std::vector<FRect>{};
    const auto up = measured ? canvas_up(*reference, anchor) : std::array<float, 3>{0, 1, 0};
    for (std::size_t i = 0; i < desktops; ++i) {
        std::optional<float> tilt;
        if (measured && i < tiles.size()) {
            Placement p = i < placements.size() ? placements[i] : Placement{};
            p.roll = 0;  // the supervisor adds its own roll
            tilt = desktop_tilt(canvas, tiles[i], p, Vec3{up[0], up[1], up[2]});
        }
        line += i ? "," : "";
        line += tilt ? std::to_string(std::clamp(int(std::lround(*tilt * 180.f / float(M_PI) * 100.f)), -18000, 18000)) : "null";
    }
    line += "]}}";
    return line;
}
void emit(const std::string& name, const std::string& l) { std::printf("case %s\n%s\n", name.c_str(), l.c_str()); }
}

int main() {
    const Canvas frame_canvas{1920, 1200, 0.41421356f, 0.25888f};
    const Canvas hd{1920, 1080, 0.41421356f, 0.23300762f};
    const std::vector<Size> three(3, Size{1920, 1200}), four(4, Size{1920, 1080});
    const std::vector<Size> mixed{{2560, 1440}, {1920, 1080}, {1080, 1920}};
    const std::vector<Placement> placed{{rad(0.7f), rad(9.0f), 0.33f, 0, 0}, {rad(34.5f), rad(8.5f), 0.31f, 0, 0}, {rad(-5.2f), rad(-15.7f), 0.69f, 0, 0}};
    const std::vector<Placement> rolled{{rad(30), rad(12), 1.4f, 0.5f, rad(20)}, {0, 0, 1, 0, rad(-35)}, {rad(140), rad(-5), 1, 0, rad(7.5f)}};
    const Quat level{};

    for (std::size_t n = 0; n <= 4; ++n) emit("unmeasured " + std::to_string(n), line(hd, n, four, 20, {}, std::nullopt, level));

    const Quat live = quat_conj(Quat{0.539f, 0.826f, 0.163f, -0.014f});
    const struct { const char* name; Quat reference; } odd[] = {
        {"level", level},
        {"live", live},
        {"upside down on a desk", axis_angle(1, 0, 0, 180)},
        {"lying on one arm", axis_angle(1, 0, 0, 90)},
        {"hanging, lenses down", axis_angle(0, 1, 0, 88)},
        {"hanging, lenses up", axis_angle(0, 1, 0, -70)},
        {"tipped", head_at(5, 0, 12)},
        {"tipped and pitched", head_at(-30, 25, -20)},
        {"plausible", head_at(0, 20, 25)},
    };
    for (const auto& c : odd) {
        emit(std::string("placed, ") + c.name, line(frame_canvas, 3, three, 20, placed, c.reference, level));
        emit(std::string("rolled, ") + c.name, line(hd, 3, mixed, 40, rolled, c.reference, level));
        emit(std::string("anchored, ") + c.name, line(frame_canvas, 3, three, 20, placed, c.reference, head_at(35, -8, 0)));
    }
    for (const float head_roll : {-20.f, 20.f})
        for (const float pitch : {0.f, -20.f, 25.f}) {
            const Quat reference = quat_mul(axis_angle(0, 1, 0, pitch), axis_angle(1, 0, 0, head_roll));
            emit("four, roll " + std::to_string(int(head_roll)) + " pitch " + std::to_string(int(pitch)),
                 line(Canvas{1920, 1200, 0.41421356f, 0.25888f}, 4, four, 20, {}, reference, level));
        }
    // More desktops than placements (the rest flat), the widest spacing (tiles of no height: not measured), one straight
    // overhead (not measured), and the tilt clamped where a desktop sits upside down.
    emit("fewer placements", line(hd, 4, four, 20, {placed[1]}, head_at(0, 0, 9), level));
    emit("widest spacing", line(hd, 4, four, 400, rolled, head_at(0, 0, 9), level));
    emit("overhead", line(hd, 2, four, 20, {Placement{0, rad(89.5f), 1, 0, 0}, Placement{}}, head_at(0, 0, 9), level));
    emit("upside down", line(hd, 2, four, 20, {}, axis_angle(1, 0, 0, 179.99f), level));
    emit("half turn", line(hd, 2, four, 20, {}, axis_angle(1, 0, 0, -180), level));
    return 0;
}
