// Overview corner-resize handle and grab pills: the renderer side of tools/native_spike/resize.py. Display only; offline.
//  * the `resize_handle` control frame decodes strictly, and a malformed payload is the ignorable kind (dropped);
//  * corner_handle() puts an L-bracket inside the requested corner, at a fixed spot (no pixel orbit);
//  * its grey levels stay at or under the cards' 191/255 cap (micro-OLED image retention).
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
bool inside(const FRect& r, const FRect& t) {
    return r.x >= t.x - 1e-3f && r.y >= t.y - 1e-3f && r.x + r.width <= t.x + t.width + 1e-3f && r.y + r.height <= t.y + t.height + 1e-3f;
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
    for (const char* bad : {R"({})", R"({"visible":true,"desktop":3,"corner":"br","active":false})",
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

    // Geometry: each corner gets an L inside the tile, hugging that corner.
    const FRect tile{100, 200, 600, 340};
    for (std::uint32_t c = 0; c < 4; ++c) {
        const auto h = corner_handle(tile, c);
        CHECK(inside(h.horizontal, tile) && inside(h.vertical, tile));
        CHECK(std::fabs(h.horizontal.height - kHandleThicknessPx) < 1e-4f && std::fabs(h.vertical.width - kHandleThicknessPx) < 1e-4f);
        CHECK(std::fabs(h.horizontal.width - h.vertical.height) < 1e-4f);  // equal arms
        const bool right = c == 1 || c == 3, bottom = c >= 2;
        const float edge_x = right ? tile.x + tile.width - kHandleInsetPx : tile.x + kHandleInsetPx;
        const float edge_y = bottom ? tile.y + tile.height - kHandleInsetPx : tile.y + kHandleInsetPx;
        CHECK(std::fabs((right ? h.horizontal.x + h.horizontal.width : h.horizontal.x) - edge_x) < 1e-3f);
        CHECK(std::fabs((bottom ? h.vertical.y + h.vertical.height : h.vertical.y) - edge_y) < 1e-3f);
        CHECK(std::fabs((bottom ? h.horizontal.y + h.horizontal.height : h.horizontal.y) - edge_y) < 1e-3f);
        CHECK(std::fabs((right ? h.vertical.x + h.vertical.width : h.vertical.x) - edge_x) < 1e-3f);
    }
    // Arm length follows the tile, clamped; the same input always gives the same pixels (no orbit or drift).
    CHECK(std::fabs(corner_handle(tile, 3).horizontal.width - 34.f) < 1e-3f);  // 10% of 340
    CHECK(std::fabs(corner_handle({0, 0, 4000, 3000}, 0).horizontal.width - kHandleMaxArmPx) < 1e-3f);
    CHECK(std::fabs(corner_handle({0, 0, 60, 40}, 0).horizontal.width - kHandleMinArmPx) < 1e-3f);
    const auto once = corner_handle(tile, 1), again = corner_handle(tile, 1);
    CHECK(once.horizontal.x == again.horizontal.x && once.horizontal.y == again.horizontal.y && once.vertical.x == again.vertical.x &&
          once.vertical.y == again.vertical.y);
    // Degenerate input draws nothing.
    const auto none = corner_handle({0, 0, 0, 10}, 0), wrong = corner_handle(tile, 4);
    CHECK(none.horizontal.width == 0 && none.vertical.height == 0 && wrong.horizontal.width == 0);

    // OLED: both levels are dim greys at or under the cards' cap, and dragging is the brighter one.
    CHECK(kHandleLevelIdle > 0.f && kHandleLevelIdle < kHandleLevelActive);
    CHECK(std::lround(kHandleLevelActive * 255.f) <= 191 && std::lround(kHandleLevelIdle * 255.f) <= 191);
    // Grab pills (move_handle): strict decode, malformed payloads dropped, never fatal.
    auto pill = [](const std::string& payload) {
        return R"({"schema":1,"request_id":8,"run_id":"run","session":"session","op":"move_handle","payload":)" + payload + "}\n";
    };
    {
        auto d = decode_control(pill(R"({"visible":true,"desktop":1,"hover":true,"active":false})"));
        CHECK(std::holds_alternative<ControlMessage>(d) && std::get<ControlMessage>(d).op == ControlOp::move_handle);
        const auto* p = std::get_if<MoveHandlePayload>(&std::get<ControlMessage>(d).payload);
        CHECK(p && p->visible && p->desktop == 1 && p->hover && !p->active);
        for (const char* bad : {R"({})", R"({"visible":true,"desktop":3,"hover":false,"active":false})",
                                R"({"visible":true,"desktop":-1,"hover":false,"active":false})",
                                R"({"visible":"yes","desktop":0,"hover":false,"active":false})",
                                R"({"visible":true,"desktop":0,"hover":false})",
                                R"({"visible":true,"desktop":0,"hover":false,"active":false,"x":5})"}) {
            auto e = decode_control(pill(bad));
            CHECK(std::holds_alternative<Error>(e) && ignorable_control_error(std::get<Error>(e)));
            CHECK(std::get<Error>(e).code == std::string(kInvalidMoveHandleCode));
        }
    }
    // Pill geometry: centred just above the tile, a share of its width clamped, thin, fixed (no drift input at all).
    {
        const FRect p = grab_pill(tile);
        CHECK(std::fabs(p.x + p.width / 2 - (tile.x + tile.width / 2)) < 1e-3f);
        CHECK(std::fabs(p.y + p.height - (tile.y - kPillGapPx)) < 1e-3f && p.height == kPillThicknessPx);
        CHECK(std::fabs(p.width - 180.f) < 1e-3f);  // 30% of 600
        CHECK(std::fabs(grab_pill({0, 100, 4000, 2000}).width - kPillMaxPx) < 1e-3f);
        CHECK(std::fabs(grab_pill({0, 100, 50, 30}).width - kPillMinPx) < 1e-3f);
        CHECK(grab_pill({0, 100, 10, 30}).width <= 10.f);  // never wider than its desktop
        CHECK(grab_pill({0, 0, 0, 30}).width == 0);
        CHECK(p.height * 10 < p.width);  // long and thin
    }
    // Pill levels: dim greys, hover brighter, drag brightest, all at or under 191/255.
    CHECK(kPillLevelIdle > 0.f && kPillLevelIdle < kPillLevelHover && kPillLevelHover < kPillLevelActive);
    CHECK(std::lround(kPillLevelActive * 255.f) <= 191);
    // Placement easing: frame-rate independent, converges, lands exactly, no time = no change, no overshoot.
    {
        const Placement from{0, 0, 1, 0, 0, 0, 0}, to{0.3f, -0.2f, 0.6f, 0.1f, 0, 0, 0.5f};
        CHECK(same_placement(ease_toward(from, to, 1e9f, kPlacementEaseMs), to));
        CHECK(same_placement(ease_toward(from, to, 0, kPlacementEaseMs), from));
        const Placement half = ease_toward(from, to, kPlacementEaseMs, kPlacementEaseMs);
        CHECK(half.x > 0 && half.x < to.x && half.distance < 1 && half.distance > to.distance);
        Placement a = from, b = from;
        for (int i = 0; i < 8; ++i) a = ease_toward(a, to, 4.f, kPlacementEaseMs);  // 8 x 4 ms
        for (int i = 0; i < 2; ++i) b = ease_toward(b, to, 16.f, kPlacementEaseMs); // 2 x 16 ms
        CHECK(std::fabs(a.x - b.x) < 1e-4f && std::fabs(a.distance - b.distance) < 1e-4f);
        Placement c = from; int frames = 0;
        while (!same_placement(c, to) && frames < 1000) { c = ease_toward(c, to, 1000.f / 120, kPlacementEaseMs); ++frames; }
        CHECK(same_placement(c, to) && frames < 120);  // settles in well under a second
    }
    std::cout << "resize handle and grab pills: decode, ignorable drop, geometry, OLED levels, placement easing: PASS\n";
}
