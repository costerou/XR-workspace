#pragma once
#include "xr/types.hpp"
#include "xr/video.hpp"
#include "xr/view.hpp"
#include "xr/desktops.hpp"
#include <array>
#include <deque>
#include <optional>
#include <string_view>
#include <string>
#include <utility>
#include <vector>

namespace xr {
enum class ControlOp { status, start, stop, select, recover, view, recenter, style, help, placement, placement_menu, resize_handle, video, move_handle,
                       overview_pointer, roll, desktops };
struct SelectPayload { OutputId output_id; };
// Glasses view: overview vs zoomed into `selected` (0..kMaxDesktops-1), gap `spacing` px (0..400).
// `mode` (T2): 0 anchor, 1 locked (0DoF), 2 follow; wire strings "anchor" | "locked" | "follow".
// `cursor_follow` is display-only for the renderer (mode/cursor pop-ups); the supervisor acts on it.
// `prediction`: renderer-side bounded head-pose prediction (predict.hpp), toggled at run time.
// `typing`: display-only typing mode (Ctrl+Alt+T: the supervisor released every run key but the toggle); a dim pop-up.
// Optional `center` (bool, default false): this change of `selected` is a cursor switch (the cursor pushed past an edge,
// tools/native_spike/unified_cursor.py); in anchor mode the switch lands with the target centred in view (view.hpp).
// Optional `cut` (bool, default false): zoomed in, the new desktop is shown at once, with no zoom out / turn / zoom in and
// no turn of the view. The controller sends it only for an anchor-mode cursor switch with its animation off
// (view.py ViewControl.cursor_switch_flags), so the view stays wherever the head looks.
struct ViewPayload { bool zoomed = false; std::uint32_t selected = 0, spacing = 0; std::uint8_t mode = 0; bool cursor_follow = false; bool prediction = false;
                     bool typing = false; bool center = false; bool cut = false; };
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
// Per-desktop placement, absolute: {"desktops":[{x,y,distance,curvature},...]}, 1..kMaxDesktops entries in managed-output order, each
// object exactly those four keys (the removed yaw / pitch / roll are rejected), every field an integer inside
// kPlacementBounds (mirrors tools/native_spike/geometry.py PLACEMENT_BOUNDS): x / y the orbit round you in tenths of a
// degree (view.hpp Placement), distance in percent, curvature in degrees. Every desktop always faces you.
// Optional "glide": 1..kMaxPlacementGlideMs milliseconds (tools/native_spike/view.py GLIDE_MS): every desktop slides to its
// new placement together along one smootherstep curve over that time (view.hpp glide_toward), instead of the quick
// exponential easing; the supervisor asks for it when a discrete edit bumped other desktops out of the way (bump.py).
struct WirePlacement { std::int32_t x = 0, y = 0, distance = 100, curvature = 0; };
constexpr std::uint32_t kMaxPlacementGlideMs = 1000;
struct PlacementPayload { std::vector<WirePlacement> desktops; std::uint32_t glide_ms = 0; };
// Placement panel (a glass pill expanded: a pill click or Ctrl+Alt+L in the supervisor; Ctrl+Alt+P is pose prediction),
// display only (the supervisor owns the state and applies placements with `placement`):
// {"open":bool,"desktop":0..n-1,"field":"x"|"y"|"distance"|"curvature","placements":[n placements]}, n = 1..kMaxDesktops
// desktops, plus the panel's desktop row (all optional): "names":[n output numbers, each 1..kMaxDesktops, increasing] (the
// n of each desktop's XR-n; default 1..n), "wide":bool (the open desktop is ultrawide), "armed":bool (Remove asks for a
// second click), "busy":bool (a desktop change is in progress);
// plus, only with open=true and only while the overview free pointer holds one of the panel's sliders, "surface":placement
// (the open desktop's placement at the press: the panel is drawn on it, while the desktop follows its live placement, so
// the knob stays under the pointer the supervisor measures on that same surface). Placements as above. `field` is the
// index into that key order. open=true expands desktop `desktop`'s pill into its panel (glass.hpp); open=false collapses
// it. A placement_menu frame whose envelope is valid but whose payload is malformed is rejected with kInvalidMenuCode;
// the renderer drops that frame and keeps running (a panel redraw is not worth ending the session for). Every other
// rejection still stops it.
constexpr const char* kInvalidMenuCode = "invalid_menu";
constexpr std::size_t kPlacementMenuDesktops = kMaxDesktops;
struct PlacementMenuPayload {
    bool open = false; std::uint32_t desktop = 0, field = 0; std::array<WirePlacement, kPlacementMenuDesktops> placements{};
    std::optional<WirePlacement> surface;
    std::uint32_t count = 0;  // placements in use
    std::array<std::uint8_t, kPlacementMenuDesktops> numbers{1, 2, 3, 4};
    bool wide = false, armed = false, busy = false;
    // The Scale dropdown (optional): "scale" percent (0 if absent), "scales" percents increasing (bit i set for
    // glass.hpp kScalePercents[i]), "picker" (its list open; open panels only).
    std::uint16_t scale = 0; std::uint8_t scales = 0; bool picker = false;
};
constexpr std::uint32_t kMaxPointerPx = 8192;
// Overview corner-resize handle (tools/native_spike/resize.py), display only:
// {"visible":bool,"desktop":0..kMaxDesktops-1,"corner":"tl"|"tr"|"bl"|"br","active":bool}; active = a drag is in progress.
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
enum class VideoAction : std::uint8_t { open, close, pause, seek, format, controls, quality, menu };
constexpr std::int32_t kMaxVideoSeekS = 3600;
struct VideoPayload {
    VideoAction action = VideoAction::close; VideoFormat format; std::string title; std::int32_t seconds = 0; bool visible = false;
    VideoQuality quality;                    // quality: the settings to apply (video.hpp)
    std::vector<VideoMenuRow> rows;          // menu: its rows (1..kMaxQualityRows) and the highlighted one
    std::uint32_t selected = 0;
};
// Overview glass pills (tools/native_spike/resize.py pill_frame), display only: {"visible":bool,"desktop":0..kMaxDesktops-1,"hover":bool,
// "active":bool}. visible = a pill under every desktop; `desktop` with hover = the brighter one under the pointer, with
// active = being dragged. Malformed: rejected with kInvalidMoveHandleCode and dropped, like resize_handle.
constexpr const char* kInvalidMoveHandleCode = "invalid_move_handle";
struct MoveHandlePayload { bool visible = false; std::uint32_t desktop = 0; bool hover = false, active = false; };
// Overview free pointer (tools/native_spike/free_pointer.py), display only: {"visible":bool,"x":int,"y":int,"held":bool}, x and
// y in canvas px, each -kMaxPointerPx..kMaxPointerPx (the pointer can go past the view's edge, beyond the desktops). held =
// a pill, corner or panel-slider press/drag is in progress. Optionally also "az":int,"el":int (both or neither): the dot's
// direction in hundredths of a degree (az -kMaxPointerAz..kMaxPointerAz, positive right; el -kMaxPointerEl..kMaxPointerEl,
// positive up). The supervisor sends it whenever the dot is shown (the dot's direction is linear in the pointer, orbit.py
// pointer_direction, and goes round you in anchor mode): the dot is then drawn in that direction (view.hpp direction_disc).
// Optionally also "drag":{"w":int,"h":int,"target":int} while a window is being dragged to another desktop
// (free_pointer.py window drag): the ghost, a w x h canvas px outline (1..kMaxGhostPx each) centred on the dot, and the
// desktop it would land on (-1..kMaxDesktops-1, -1: none), framed in the accent colour.
// Optionally also "look":string over a handle (free_pointer.py POINTER_LOOKS, view.hpp PointerLook): "resize_ew", "resize_ns",
// "resize_nwse", "resize_nesw", "move" or "rotate"; absent: the plain dot.
// Malformed: rejected with kInvalidOverviewPointerCode and dropped, like move_handle.
constexpr const char* kInvalidOverviewPointerCode = "invalid_overview_pointer";
constexpr std::int32_t kMaxPointerAz = 18000, kMaxPointerEl = 9000, kMaxGhostPx = 512;
struct OverviewPointerPayload {
    bool visible = false; std::int32_t x = 0, y = 0; bool held = false;
    bool directed = false; std::int32_t az = 0, el = 0;  // az / el given (1/100 degree)
    bool drag = false; std::int32_t ghost_w = 0, ghost_h = 0, target = -1;  // a window drag in progress
    PointerLook look = PointerLook::plain;
};
// Roll brackets (tools/native_spike/free_pointer.py roll_frame), display and levelling roll; the supervisor owns both:
// {"roll":[r,...],"icons":[i,...],"hover":-1..kMaxDesktops-1,"side":s,"active":bool}. roll: each desktop's levelling roll
// (view.hpp Placement::roll) in hundredths of a degree, counter-clockwise as you see it, -kMaxRoll..kMaxRoll. icons: "none",
// "ccw" (the arrow bracket round the top-left corner), "cw" (round the top-right; glass.hpp roll_bracket) or "both". hover:
// the desktop whose bracket is under the pointer or held (-1: none), side: which of them ("ccw" / "cw"; "none" exactly when hover is
// -1); active: it is being dragged. Exactly kMaxDesktops entries each, in managed-output order
// (desktops beyond this run's outputs are ignored). Malformed: rejected with kInvalidRollCode and dropped, like move_handle.
constexpr const char* kInvalidRollCode = "invalid_roll";
constexpr std::int32_t kMaxRoll = 9000;
struct RollPayload {
    std::array<std::int32_t, kMaxDesktops> roll{}; std::array<std::int8_t, kMaxDesktops> icons{-1, -1, -1, -1};  // icon: -1 none, 0 ccw, 1 cw, 2 both
    std::int32_t hover = -1; std::int8_t side = -1; bool active = false;  // side: -1 none, 0 ccw, 1 cw
};
// The run's managed desktops changed (a glass pill's Add / Remove / Ultrawide, tools/native_spike/desktops.py):
// {"outputs":["XR-1","XR-3",...], "renew":[...]}. `outputs` is the whole set to capture from now on, 1..kMaxDesktops
// distinct XR-1..XR-kMaxDesktops names (never the presentation output; parse_capture_list rules), in any order: a name
// not captured yet starts a capture once Hyprland advertises that output, a captured name left out stops its capture
// (before the supervisor removes the output, so its going is expected). `renew` (optional, names from `outputs`): restart
// those captures (the supervisor reshaped the output). Malformed: rejected like any malformed frame (the renderer stops).
struct DesktopsPayload { std::vector<std::string> outputs, renew; };
using ControlPayload = std::variant<std::monostate, SelectPayload, ViewPayload, StylePayload, HelpPayload, PlacementPayload, PlacementMenuPayload,
                                    ResizeHandlePayload, VideoPayload, MoveHandlePayload, OverviewPointerPayload, RollPayload, DesktopsPayload>;
struct ControlMessage {
    std::uint32_t schema;
    std::uint64_t request_id;
    std::string run_id, session;
    ControlOp op;
    ControlPayload payload;
};
Result<ControlMessage> decode_control(std::string_view frame);
// A rejected frame the renderer may drop without stopping: only a malformed placement_menu, resize_handle, move_handle,
// overview_pointer or roll payload. It
// carries no action, so its identity and request id are not checked and it does not advance the replay floor.
inline bool ignorable_control_error(const Error& e) {
    return e.code == kInvalidMenuCode || e.code == kInvalidHandleCode || e.code == kInvalidMoveHandleCode ||
           e.code == kInvalidOverviewPointerCode || e.code == kInvalidRollCode;
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
