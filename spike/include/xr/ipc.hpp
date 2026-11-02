#pragma once
#include "xr/types.hpp"
#include <deque>
#include <string_view>
#include <string>
#include <utility>
#include <vector>

namespace xr {
enum class ControlOp { status, start, stop, select, recover, view, recenter, style, help };
struct SelectPayload { OutputId output_id; };
// Glasses view: overview vs zoomed into `selected` (0..kMaxDesktops-1), gap `spacing` px (0..400).
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
using ControlPayload = std::variant<std::monostate, SelectPayload, ViewPayload, StylePayload, HelpPayload>;
struct ControlMessage {
    std::uint32_t schema;
    std::uint64_t request_id;
    std::string run_id, session;
    ControlOp op;
    ControlPayload payload;
};
Result<ControlMessage> decode_control(std::string_view frame);
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
