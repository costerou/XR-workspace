// Overview corner-resize handle: the renderer side of tools/native_spike/resize.py. Display only; offline.
//  * the `resize_handle` control frame decodes strictly, and a malformed payload is the ignorable kind (dropped);
//  * corner_handle() puts an L-bracket inside the requested corner, shifted by the cards' pixel orbit;
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
    // Arm length follows the tile, clamped; the orbit offset moves both arms by exactly (dx, dy).
    CHECK(std::fabs(corner_handle(tile, 3).horizontal.width - 34.f) < 1e-3f);  // 10% of 340
    CHECK(std::fabs(corner_handle({0, 0, 4000, 3000}, 0).horizontal.width - kHandleMaxArmPx) < 1e-3f);
    CHECK(std::fabs(corner_handle({0, 0, 60, 40}, 0).horizontal.width - kHandleMinArmPx) < 1e-3f);
    const auto still = corner_handle(tile, 1), moved = corner_handle(tile, 1, 3, -2);
    CHECK(std::fabs(moved.horizontal.x - still.horizontal.x - 3) < 1e-4f && std::fabs(moved.horizontal.y - still.horizontal.y + 2) < 1e-4f);
    CHECK(std::fabs(moved.vertical.x - still.vertical.x - 3) < 1e-4f && std::fabs(moved.vertical.y - still.vertical.y + 2) < 1e-4f);
    // Degenerate input draws nothing.
    const auto none = corner_handle({0, 0, 0, 10}, 0), wrong = corner_handle(tile, 4);
    CHECK(none.horizontal.width == 0 && none.vertical.height == 0 && wrong.horizontal.width == 0);

    // OLED: both levels are dim greys at or under the cards' cap, and dragging is the brighter one.
    CHECK(kHandleLevelIdle > 0.f && kHandleLevelIdle < kHandleLevelActive);
    CHECK(std::lround(kHandleLevelActive * 255.f) <= 191 && std::lround(kHandleLevelIdle * 255.f) <= 191);
    std::cout << "resize handle: decode, ignorable drop, corner geometry, orbit, OLED levels: PASS\n";
}
