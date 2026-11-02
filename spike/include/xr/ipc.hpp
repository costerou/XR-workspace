#pragma once
#include "xr/types.hpp"
#include <array>
#include <deque>
#include <string_view>
#include <string>
#include <utility>
#include <vector>

namespace xr {
enum class ControlOp { status, start, stop, select, recover, view, recenter, style, help, placement, placement_menu, resize_handle };
struct SelectPayload { OutputId output_id; };
// Glasses view: overview vs zoomed into `selected` (0..2), gap `spacing` px (0..400).
// `mode` (T2): 0 anchor, 1 locked (0DoF), 2 follow; wire strings "anchor" | "locked" | "follow".
// `cursor_follow` is display-only for the renderer (mode/cursor pop-ups); the supervisor acts on it.
// `prediction`: renderer-side bounded head-pose prediction (predict.hpp), toggled at run time.
struct ViewPayload { bool zoomed = false; std::uint32_t selected = 0, spacing = 0; std::uint8_t mode = 0; bool cursor_follow = false; bool prediction = false; };
// Theme accent for the highlight border/glow: {"border":[r,g,b]}, each 0..255.
struct StylePayload { std::uint8_t r = 0, g = 0, b = 0; };
// Keyboard help card: {"visible":bool,"rows":[[keys,action],...]}; at most kMaxHelpRows rows,
// each string 1..kMaxHelpText printable ASCII characters (the pixel-font fallback is ASCII-only).
constexpr std::size_t kMaxHelpRows = 24, kMaxHelpText = 48;
struct HelpPayload { bool visible = false; std::vector<std::pair<std::string, std::string>> rows; };
// Per-desktop placement, absolute: {"desktops":[{x,y,distance,yaw,pitch,roll,curvature},...]}, 1..3 entries in managed-output
// order, every field an integer inside kPlacementBounds (mirrors tools/native_spike/geometry.py PLACEMENT_BOUNDS).
struct WirePlacement { std::int32_t x = 0, y = 0, distance = 100, yaw = 0, pitch = 0, roll = 0, curvature = 0; };
struct PlacementPayload { std::vector<WirePlacement> desktops; };
// Keyboard placement menu (Ctrl+Alt+L toggles it in the supervisor; Ctrl+Alt+P is pose prediction), display only
// (the supervisor owns the state and applies placements with `placement`):
// {"open":bool,"desktop":0..2,"field":"x"|"y"|"distance"|"yaw"|"pitch"|"roll"|"curvature","placements":[3 placements]},
// placements as above. `field` is the index into that key order. open=false hides the menu. A placement_menu frame
// whose envelope is valid but whose payload is malformed is rejected with kInvalidMenuCode; the renderer drops that
// frame and keeps running (a menu redraw is not worth ending the session for). Every other rejection still stops it.
// Mouse mode adds an optional fifth key, only with open=true: "pointer":{"row":-1..6,"x":0..1000,"drag":bool} (row -1 is
// the desktop selector, 0..6 the fields; x is per mille along the slider). Without it there is no pointer.
constexpr const char* kInvalidMenuCode = "invalid_menu";
constexpr std::size_t kPlacementMenuDesktops = 3;
struct PlacementMenuPayload {
    bool open = false; std::uint32_t desktop = 0, field = 0; std::array<WirePlacement, kPlacementMenuDesktops> placements{};
    bool pointer = false; std::int32_t pointer_row = 0; std::uint32_t pointer_x = 0; bool pointer_drag = false;
};
// Overview corner-resize handle (tools/native_spike/resize.py), display only:
// {"visible":bool,"desktop":0..2,"corner":"tl"|"tr"|"bl"|"br","active":bool}; active = a drag is in progress.
// `corner` is the index into that order. Like placement_menu, a malformed payload is rejected with kInvalidHandleCode and
// dropped without stopping the renderer.
constexpr const char* kInvalidHandleCode = "invalid_resize_handle";
struct ResizeHandlePayload { bool visible = false; std::uint32_t desktop = 0, corner = 0; bool active = false; };
using ControlPayload = std::variant<std::monostate, SelectPayload, ViewPayload, StylePayload, HelpPayload, PlacementPayload, PlacementMenuPayload,
                                    ResizeHandlePayload>;
struct ControlMessage {
    std::uint32_t schema;
    std::uint64_t request_id;
    std::string run_id, session;
    ControlOp op;
    ControlPayload payload;
};
Result<ControlMessage> decode_control(std::string_view frame);
// A rejected frame the renderer may drop without stopping: only a malformed placement_menu or resize_handle payload. It
// carries no action, so its identity and request id are not checked and it does not advance the replay floor.
inline bool ignorable_control_error(const Error& e) { return e.code == kInvalidMenuCode || e.code == kInvalidHandleCode; }
class ControlQueue {
public:
    ControlQueue(std::string run_id, std::string session);
    Status push(std::string_view frame);
    std::optional<ControlMessage> pop();
private:
    std::string run_id_, session_;
    std::uint64_t last_ = 0;
    std::deque<ControlMessage> messages_;
};
} // namespace xr
