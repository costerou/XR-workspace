#pragma once
#include "xr/types.hpp"
#include "xr/video.hpp"
#include <array>
#include <deque>
#include <optional>
#include <string_view>
#include <string>
#include <utility>
#include <vector>

namespace xr {
enum class ControlOp { status, start, stop, select, recover, view, recenter, style, help, placement, placement_menu, resize_handle, video, move_handle,
                       overview_pointer };
struct SelectPayload { OutputId output_id; };
// Glasses view: overview vs zoomed into `selected` (0..2), gap `spacing` px (0..400).
// `mode` (T2): 0 anchor, 1 locked (0DoF), 2 follow; wire strings "anchor" | "locked" | "follow".
// `cursor_follow` is display-only for the renderer (mode/cursor pop-ups); the supervisor acts on it.
// `prediction`: renderer-side bounded head-pose prediction (predict.hpp), toggled at run time.
// `typing`: display-only typing mode (Ctrl+Alt+T: the supervisor released every run key but the toggle); a dim pop-up.
struct ViewPayload { bool zoomed = false; std::uint32_t selected = 0, spacing = 0; std::uint8_t mode = 0; bool cursor_follow = false; bool prediction = false;
                     bool typing = false; };
// Theme accent for the highlight border/glow: {"border":[r,g,b]}, each 0..255.
struct StylePayload { std::uint8_t r = 0, g = 0, b = 0; };
// Keyboard help card: {"visible":bool,"rows":[[keys,action],...]}; at most kMaxHelpRows rows,
// each string 1..kMaxHelpText printable ASCII characters (the pixel-font fallback is ASCII-only).
// Optional "sections":[n,...]: the rows' section sizes in order (each >= 1, summing to the row count; the card keeps a
// section on one page when it can). Optional "turn": -1 or 1, flip a shown card to the previous / next page.
constexpr std::size_t kMaxHelpRows = 64, kMaxHelpText = 48;
struct HelpPayload {
    bool visible = false; std::vector<std::pair<std::string, std::string>> rows;
    std::vector<std::uint32_t> sections; std::int32_t turn = 0;
};
// Per-desktop placement, absolute: {"desktops":[{x,y,distance,yaw,pitch,roll,curvature},...]}, 1..3 entries in managed-output
// order, every field an integer inside kPlacementBounds (mirrors tools/native_spike/geometry.py PLACEMENT_BOUNDS).
struct WirePlacement { std::int32_t x = 0, y = 0, distance = 100, yaw = 0, pitch = 0, roll = 0, curvature = 0; };
struct PlacementPayload { std::vector<WirePlacement> desktops; };
// Placement panel (a glass pill expanded: a pill click or Ctrl+Alt+L in the supervisor; Ctrl+Alt+P is pose prediction),
// display only (the supervisor owns the state and applies placements with `placement`):
// {"open":bool,"desktop":0..2,"field":"x"|"y"|"distance"|"yaw"|"pitch"|"roll"|"curvature","placements":[3 placements]}
// plus, only with open=true and only while the overview free pointer holds one of the panel's sliders, "surface":placement
// (the open desktop's placement at the press: the panel is drawn on it, while the desktop follows its live placement, so
// the knob stays under the pointer the supervisor measures on that same surface). Placements as above. `field` is the
// index into that key order. open=true expands desktop `desktop`'s pill into its panel (glass.hpp); open=false collapses
// it. A placement_menu frame whose envelope is valid but whose payload is malformed is rejected with kInvalidMenuCode;
// the renderer drops that frame and keeps running (a panel redraw is not worth ending the session for). Every other
// rejection still stops it.
constexpr const char* kInvalidMenuCode = "invalid_menu";
constexpr std::size_t kPlacementMenuDesktops = 3;
struct PlacementMenuPayload {
    bool open = false; std::uint32_t desktop = 0, field = 0; std::array<WirePlacement, kPlacementMenuDesktops> placements{};
    std::optional<WirePlacement> surface;
};
constexpr std::uint32_t kMaxPointerPx = 8192;
// Overview corner-resize handle (tools/native_spike/resize.py), display only:
// {"visible":bool,"desktop":0..2,"corner":"tl"|"tr"|"bl"|"br","active":bool}; active = a drag is in progress.
// `corner` is the index into that order. Like placement_menu, a malformed payload is rejected with kInvalidHandleCode and
// dropped without stopping the renderer.
constexpr const char* kInvalidHandleCode = "invalid_resize_handle";
struct ResizeHandlePayload { bool visible = false; std::uint32_t desktop = 0, corner = 0; bool active = false; };
// VR video (docs/vr-video.md, tools/native_spike/video.py), display only; the supervisor owns the state:
//   {"action":"open","projection":"360"|"180"|"fisheye","stereo":"mono"|"sbs"|"tb","fov":180..220,"title":text}
//     The file arrives with this frame as exactly one SCM_RIGHTS descriptor on the control socket; no path is ever sent.
//   {"action":"format", projection, stereo, fov}   {"action":"close"}   {"action":"pause"} (toggle)
//   {"action":"seek","seconds":-3600..3600, nonzero}   {"action":"controls","visible":bool}
// `title` is 1..kMaxHelpText printable ASCII characters. Anything else is rejected like any malformed frame.
enum class VideoAction : std::uint8_t { open, close, pause, seek, format, controls };
constexpr std::int32_t kMaxVideoSeekS = 3600;
struct VideoPayload { VideoAction action = VideoAction::close; VideoFormat format; std::string title; std::int32_t seconds = 0; bool visible = false; };
// Overview glass pills (tools/native_spike/resize.py pill_frame), display only: {"visible":bool,"desktop":0..2,"hover":bool,
// "active":bool}. visible = a pill under every desktop; `desktop` with hover = the brighter one under the pointer, with
// active = being dragged. Malformed: rejected with kInvalidMoveHandleCode and dropped, like resize_handle.
constexpr const char* kInvalidMoveHandleCode = "invalid_move_handle";
struct MoveHandlePayload { bool visible = false; std::uint32_t desktop = 0; bool hover = false, active = false; };
// Overview free pointer (tools/native_spike/free_pointer.py), display only: {"visible":bool,"x":int,"y":int,"held":bool}, x and
// y in canvas px, each -kMaxPointerPx..kMaxPointerPx (the pointer can go past the view's edge, beyond the desktops). held =
// a pill, corner or panel-slider press/drag is in progress. Malformed: rejected with kInvalidOverviewPointerCode and
// dropped, like move_handle.
constexpr const char* kInvalidOverviewPointerCode = "invalid_overview_pointer";
struct OverviewPointerPayload { bool visible = false; std::int32_t x = 0, y = 0; bool held = false; };
using ControlPayload = std::variant<std::monostate, SelectPayload, ViewPayload, StylePayload, HelpPayload, PlacementPayload, PlacementMenuPayload,
                                    ResizeHandlePayload, VideoPayload, MoveHandlePayload, OverviewPointerPayload>;
struct ControlMessage {
    std::uint32_t schema;
    std::uint64_t request_id;
    std::string run_id, session;
    ControlOp op;
    ControlPayload payload;
};
Result<ControlMessage> decode_control(std::string_view frame);
// A rejected frame the renderer may drop without stopping: only a malformed placement_menu, resize_handle, move_handle or
// overview_pointer payload. It
// carries no action, so its identity and request id are not checked and it does not advance the replay floor.
inline bool ignorable_control_error(const Error& e) {
    return e.code == kInvalidMenuCode || e.code == kInvalidHandleCode || e.code == kInvalidMoveHandleCode ||
           e.code == kInvalidOverviewPointerCode;
}
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
