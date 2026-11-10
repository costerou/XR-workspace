// Overview corner-resize handle and grab pills: the renderer side of tools/native_spike/resize.py. Display only; offline.
//  * the `resize_handle` control frame decodes strictly, and a malformed payload is the ignorable kind (dropped);
//  * corner_cap_rect() puts a cap's box round the requested corner, at a fixed spot (no pixel orbit), outside the desktop
//    but for what the desktop's content covers. Their pixels and colours: roll_level_test, glass_theme_test.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/view.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;

namespace {
std::string frame(const std::string& payload, std::uint64_t id = 7) {
    return R"({"schema":1,"request_id":)" + std::to_string(id) + R"(,"run_id":"run","session":"session","op":"resize_handle","payload":)" + payload + "}\n";
}
}

int main() {
    const char* corners[] = {"tl", "tr", "bl", "br"};
    for (std::uint32_t c = 0; c < 4; ++c) {
        auto d = decode_control(frame(std::string(R"({"visible":true,"desktop":2,"corner":")") + corners[c] + R"(","active":false})"));
        CHECK(std::holds_alternative<ControlMessage>(d));
        const auto& m = std::get<ControlMessage>(d);
        CHECK(m.op == ControlOp::resize_handle);
        const auto* p = std::get_if<ResizeHandlePayload>(&m.payload);
        CHECK(p && p->visible && p->desktop == 2 && p->corner == c && !p->active);
    }
    auto hidden = decode_control(frame(R"({"visible":false,"desktop":0,"corner":"br","active":true})"));
    CHECK(std::holds_alternative<ControlMessage>(hidden));
    const auto* hp = std::get_if<ResizeHandlePayload>(&std::get<ControlMessage>(hidden).payload);
    CHECK(hp && !hp->visible && hp->active && hp->corner == 3);
    for (const char* bad : {R"({})", R"({"visible":true,"desktop":4,"corner":"br","active":false})",
                            R"({"visible":true,"desktop":-1,"corner":"br","active":false})",
                            R"({"visible":true,"desktop":0,"corner":"center","active":false})",
                            R"({"visible":1,"desktop":0,"corner":"br","active":false})",
                            R"({"visible":true,"desktop":0,"corner":"br","active":"no"})",
                            R"({"visible":true,"desktop":0,"corner":"br"})",
                            R"({"visible":true,"desktop":0,"corner":"br","active":false,"size":3})",
                            R"({"visible":true,"desktop":0.5,"corner":"br","active":false})"}) {
        auto d = decode_control(frame(bad));
        CHECK(std::holds_alternative<Error>(d));
        CHECK(ignorable_control_error(std::get<Error>(d)));
        CHECK(std::get<Error>(d).code == std::string(kInvalidHandleCode));
    }
    {  // Bad framing around the op is still fatal, and a dropped frame does not use up its request id.
        std::string unframed = frame(R"({"visible":true,"desktop":0,"corner":"br","active":false})");
        unframed.pop_back();
        auto d = decode_control(unframed);
        CHECK(std::holds_alternative<Error>(d) && !ignorable_control_error(std::get<Error>(d)));
        ControlQueue q("run", "session");
        auto dropped = q.push(frame(R"({"visible":true,"desktop":9,"corner":"br","active":false})", 9));
        CHECK(std::holds_alternative<Error>(dropped) && ignorable_control_error(std::get<Error>(dropped)));
        CHECK(std::holds_alternative<std::monostate>(q.push(frame(R"({"visible":true,"desktop":1,"corner":"tl","active":false})", 9))));
        CHECK(q.pop().has_value());
    }

    // Geometry: each corner gets its box, reaching kCapOuter past that corner on both axes; the same input always gives the
    // same pixels (no orbit or drift); it scales with the desktop like the level bar.
    const FRect tile{100, 200, 600, 340};
    for (std::uint32_t c = 0; c < 4; ++c) {
        const auto box = corner_cap_rect(tile, c);
        const bool right = c == 1 || c == 3, bottom = c >= 2;
        const float cx = right ? tile.x + tile.width : tile.x, cy = bottom ? tile.y + tile.height : tile.y;
        CHECK(box.x < cx && cx < box.x + box.width && box.y < cy && cy < box.y + box.height);
        CHECK(std::fabs((right ? box.x + box.width - cx : cx - box.x) - kCapOuter) < 1e-3f);
        CHECK(std::fabs((bottom ? box.y + box.height - cy : cy - box.y) - kCapOuter) < 1e-3f);
        const auto again = corner_cap_rect(tile, c);
        CHECK(box.x == again.x && box.y == again.y && box.width == again.width && box.height == again.height);
    }
    CHECK(std::fabs(corner_cap_rect({0, 0, 240, 135}, 0).width - (kCapOuter + kCapInner) * level_bar_scale({0, 0, 240, 135})) < 1e-3f);
    CHECK(level_bar_scale({0, 0, 240, 135}) < 1.f);
    // Degenerate input draws nothing.
    CHECK(corner_cap_rect({0, 0, 0, 10}, 0).width == 0 && corner_cap_rect(tile, 4).width == 0);

    // Grab pills (move_handle): strict decode, malformed payloads dropped, never fatal.
    auto pill = [](const std::string& payload) {
        return R"({"schema":1,"request_id":8,"run_id":"run","session":"session","op":"move_handle","payload":)" + payload + "}\n";
    };
    {
        auto d = decode_control(pill(R"({"visible":true,"desktop":1,"hover":true,"active":false,"gear":false})"));
        CHECK(std::holds_alternative<ControlMessage>(d) && std::get<ControlMessage>(d).op == ControlOp::move_handle);
        const auto* p = std::get_if<MoveHandlePayload>(&std::get<ControlMessage>(d).payload);
        CHECK(p && p->visible && p->desktop == 1 && p->hover && !p->active && !p->gear);
        auto g = decode_control(pill(R"({"visible":true,"desktop":2,"hover":false,"active":false,"gear":true})"));
        const auto* q = std::get_if<MoveHandlePayload>(&std::get<ControlMessage>(g).payload);
        CHECK(q && q->desktop == 2 && q->gear && !q->hover);
        // Regression: the four-key frame (no "gear") is what resize.py sends whenever the gear is not lit, and what a
        // pre-gear supervisor sends. Refusing it dropped every pill, the gear and Maximize live.
        auto legacy = decode_control(pill(R"({"visible":true,"desktop":3,"hover":false,"active":true})"));
        CHECK(std::holds_alternative<ControlMessage>(legacy));
        const auto* l = std::get_if<MoveHandlePayload>(&std::get<ControlMessage>(legacy).payload);
        CHECK(l && l->visible && l->desktop == 3 && l->active && !l->hover && !l->gear);
        for (const char* bad : {R"({})", R"({"visible":true,"desktop":4,"hover":false,"active":false,"gear":false})",
                                R"({"visible":true,"desktop":-1,"hover":false,"active":false,"gear":false})",
                                R"({"visible":"yes","desktop":0,"hover":false,"active":false,"gear":false})",
                                R"({"visible":true,"desktop":0,"hover":false,"gear":false})",
                                R"({"visible":true,"desktop":0,"hover":false,"active":false,"x":5})",
                                R"({"visible":true,"desktop":0,"hover":false,"active":false,"gear":1})",
                                R"({"visible":true,"desktop":0,"hover":false,"active":false,"gear":false,"x":5})"}) {
            auto e = decode_control(pill(bad));
            CHECK(std::holds_alternative<Error>(e) && ignorable_control_error(std::get<Error>(e)));
            CHECK(std::get<Error>(e).code == std::string(kInvalidMoveHandleCode));
        }
    }
    // Pill geometry and look: glass_test (the pills are glass.hpp glass_pill, under each desktop).
    // Placement easing: frame-rate independent, converges, lands exactly, no time = no change, no overshoot.
    {
        const Placement from{0, 0, 1, 0}, to{0.3f, -0.2f, 0.6f, 0.5f};
        CHECK(same_placement(ease_toward(from, to, 1e9f, kPlacementEaseMs), to));
        CHECK(same_placement(ease_toward(from, to, 0, kPlacementEaseMs), from));
        const Placement half = ease_toward(from, to, kPlacementEaseMs, kPlacementEaseMs);
        CHECK(half.x > 0 && half.x < to.x && half.distance < 1 && half.distance > to.distance && half.y < 0 && half.y > to.y &&
              half.curvature > 0 && half.curvature < to.curvature);
        Placement a = from, b = from;
        for (int i = 0; i < 8; ++i) a = ease_toward(a, to, 4.f, kPlacementEaseMs);  // 8 x 4 ms
        for (int i = 0; i < 2; ++i) b = ease_toward(b, to, 16.f, kPlacementEaseMs); // 2 x 16 ms
        CHECK(std::fabs(a.x - b.x) < 1e-4f && std::fabs(a.distance - b.distance) < 1e-4f);
        Placement c = from; int frames = 0;
        while (!same_placement(c, to) && frames < 1000) { c = ease_toward(c, to, 1000.f / 120, kPlacementEaseMs); ++frames; }
        CHECK(same_placement(c, to) && frames < 120);  // settles in well under a second
    }
    std::cout << "resize handle and grab pills: decode, ignorable drop, geometry, placement easing: PASS\n";
}
