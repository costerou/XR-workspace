#pragma once
// Glass pills: a Material Design 3 tonal capsule under each XR desktop in the overview, on that desktop's own surface (it faces you
// with its desktop, and a curved desktop curves its pill). Dragging a pill moves its desktop; clicking it (or Ctrl+Alt+L for
// the selected desktop) expands it, with a smooth morph, into that desktop's placement panel: the four placement sliders,
// the selected one highlighted. Only one panel is open at a time. The supervisor owns the state (ipc.hpp
// PlacementMenuPayload: open + desktop = the expanded pill) and the hit testing (tools/native_spike/free_pointer.py,
// which mirrors every geometry constant here; a contract test compares them). Pure and offline-testable, except the
// fragment shader source, which glass_shade() mirrors on the CPU.
//
// Look: Material Design 3 (material.hpp), in an M3 scheme seeded by the Omarchy theme colour (glass_theme.hpp). The pill
// is a flat tonal capsule in the primary container at elevation 1; hover and drag lay M3's state layer over it (8%, 16%)
// and lift it (elevation 2, 4). The open panel is a surface container (high) with an outline-variant edge at elevation 3,
// the state layer on its header strip only; its content (render_glass_panel) is M3's slider, list and button parts. On the
// glasses' micro-OLED panels black is see-through: every role sits under kCardMaxLevel (191/255), the panel is dark
// behind its text, and nothing moves on its own.
#include "xr/environment.hpp"
#include "xr/glass_theme.hpp"
#include "xr/hud.hpp"
#include "xr/view.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace xr {
constexpr double kGlassExpandMs = 260, kGlassCollapseMs = 200;  // the pill <-> panel morph
// --- Geometry: canvas px on the desktop's surface, relative to its overview tile ----------------------------------------
// Collapsed pill: a share of the desktop's width (clamped), centred, kGlassPillGap below its bottom edge as laid out, which
// is kGlassPillGap - kBorderPx below the selected desktop's border. That gap to the border is the one a resize keeps
// (view.hpp glass_to_desktop).
constexpr float kGlassPillShare = 0.24f, kGlassPillMinW = 64.f, kGlassPillMaxW = 160.f, kGlassPillH = 14.f, kGlassPillGap = 10.f;
static_assert(kGlassPillGap > kBorderPx, "the pill hangs clear of the selected desktop's border");
FRect glass_pill(const FRect& tile);
// Expanded panel: kPanelW x kPanelH, centred under the desktop, its top where the pill's top is.
constexpr float kPanelW = 480.f, kPanelH = 394.f, kPanelRadius = 18.f;
FRect glass_panel(const FRect& tile);
// The shape `t` (eased 0..1) of the way from pill to panel, and its corner radius (the pill is fully rounded).
FRect glass_morph(const FRect& tile, float t);
float glass_radius(const FRect& tile, float t);
// Panel layout (panel px from its top-left). The header strip is the pill's own: dragging it moves the desktop and
// clicking it collapses the panel. Row i (field i, ipc.hpp WirePlacement order) spans
// [kPanelRowsTop + i * kPanelRowH, + kPanelRowH); its slider's knob centre runs kPanelKnobLo..kPanelKnobHi.
constexpr float kPanelHeaderH = 42.f, kPanelRowsTop = 46.f, kPanelRowH = 34.f, kPanelPad = 22.f;
constexpr float kPanelTrackX0 = 184.f, kPanelTrackX1 = 374.f, kPanelKnobR = 7.f;
constexpr float kPanelKnobLo = kPanelTrackX0 + kPanelKnobR, kPanelKnobHi = kPanelTrackX1 - kPanelKnobR;
// Under the sliders, the Resolution row (one kPanelRowH row at kPanelResolutionTop): the label "Resolution" and an M3
// exposed dropdown menu (glass_resolution_box, the Scale box's twin) showing the desktop's pixel size ("1920x1080") and a
// drop-down arrow. A click on the box opens its list, an M3 menu kResListGap above the box, drawn over the slider rows
// and as wide as their selected band (glass_resolution_list): one column per desktops.hpp kResolutionGroups shape
// (16:9, 16:10, Ultrawide), its name on top (glass_resolution_heading) and its kResolutions sizes under it
// (glass_resolution_item); the current size on the secondary container, every size M3 disabled while busy. The box and
// the items use the desktop row's button font (M3 label large, kButtonLabelPx in glass.cpp). Picking one resizes that
// desktop's output live (tools/native_spike/desktops.py RESOLUTION); free_pointer.py mirrors the geometry
// (glass_contract compares).
constexpr float kPanelResolutionTop = kPanelRowsTop + float(kPlacementMenuFields) * kPanelRowH;  // 182
constexpr float kResBoxX = kPanelTrackX0, kResBoxH = 26.f, kResBoxW = kPanelW - kPanelPad - kResBoxX;  // 274 wide
constexpr float kResBoxY = kPanelResolutionTop + (kPanelRowH - kResBoxH) / 2;  // 186
constexpr float kResItemH = 23.f, kResListGap = 2.f;
constexpr float kResListX = 10.f, kResListW = kPanelW - 2 * kResListX;      // 460 wide: a column of 153 px per shape
constexpr float kResListH = float(kResolutionRows + 1) * kResItemH;         // the headings, then the largest group
constexpr float kResListY = kResBoxY - kResListGap - kResListH;             // 46
// The open list covers the four slider rows exactly (no row shows half hidden above it), under the header.
static_assert(kResListY == kPanelRowsTop && kResListY >= kPanelHeaderH, "the open list covers the slider rows");
FRect glass_resolution_box();                       // panel px
FRect glass_resolution_list();                      // panel px: the open list's whole area
FRect glass_resolution_heading(std::size_t group);  // panel px; empty for group >= kResolutionGroupCount
FRect glass_resolution_item(std::size_t i);         // panel px; empty for i >= kResolutionCount
// Whether size `i` can be picked now: nothing is busy (every listed size is one the desktop can take).
bool glass_resolution_enabled(std::size_t i, const PlacementMenuState& state);
// Text of a size as drawn: "1920x1080"; "--" for 0 x 0 (unknown).
std::string glass_resolution_label(std::uint16_t width, std::uint16_t height);
// Under that, the Scale row (one kPanelRowH row at kPanelScaleTop): the label "Scale" and an M3 exposed dropdown
// menu (glass_scale_box: a filled text field) showing the desktop's Hyprland output scale ("125%") and a drop-down arrow.
// A click on the box opens its list, an M3 menu: one item per desktops.hpp kScalePercents step (100..300%), top down
// (glass_scale_item), kScaleListGap above the box, drawn over the Resolution row and the slider rows; the current step on
// the secondary container, steps that do not divide the desktop's resolution (or any while busy) M3 disabled. The box and
// the items use the desktop row's button font (M3 label large, kButtonLabelPx in glass.cpp). Picking one reshapes that
// desktop's output (tools/native_spike/desktops.py SCALE); free_pointer.py mirrors the geometry (glass_contract compares).
constexpr float kPanelScaleTop = kPanelResolutionTop + kPanelRowH;  // 216
constexpr float kScaleBoxX = kPanelTrackX0, kScaleBoxH = kResBoxH, kScaleBoxW = kPanelW - kPanelPad - kScaleBoxX;  // 274 wide
constexpr float kScaleBoxY = kPanelScaleTop + (kPanelRowH - kScaleBoxH) / 2;  // 220
constexpr float kScaleItemH = 20.f, kScaleListGap = 2.f;  // seven steps fit between the header and the box
FRect glass_scale_box();                   // panel px
FRect glass_scale_item(std::size_t step);  // panel px; empty for step >= kScaleSteps
static_assert(kScaleBoxY - kScaleListGap - float(kScaleSteps) * kScaleItemH >= kPanelHeaderH, "the open list stays under the header");
// Whether Scale step `step` can be picked now: it divides the desktop's resolution (state.scales), nothing is busy.
bool glass_scale_enabled(std::size_t step, const PlacementMenuState& state);
// Text of a scale in percent as drawn: "125%"; "--" for 0 (unknown).
std::string glass_scale_label(std::uint16_t percent);
// Under that, the background's two rows (xr/environment.hpp), the same in every desktop's panel. The Background row (at
// kPanelBackgroundTop): the label, an M3 exposed dropdown (glass_background_box, the Scale box's look) showing the background,
// and an M3 switch (glass_background_switch) that turns it off and back to the last one used, as Ctrl+Alt+E does. A click on
// the box opens its list, one item per kBackgrounds row (glass_background_item), kBackgroundListGap above the box, drawn over
// the rows above it. The Brightness row (at kPanelBrightnessTop): a slider like the placement ones, on their knob range,
// filled from 100%, kBrightnessMin..kBrightnessMax, M3 disabled with no background. free_pointer.py mirrors the geometry
// (glass_contract compares).
constexpr float kPanelBackgroundTop = kPanelScaleTop + kPanelRowH;  // 250
constexpr float kBackgroundSwitchW = 46.f, kBackgroundSwitchH = 26.f, kBackgroundBoxGap = 12.f;
constexpr float kBackgroundSwitchX = kPanelW - kPanelPad - kBackgroundSwitchW;  // 412
constexpr float kBackgroundBoxX = kPanelTrackX0, kBackgroundBoxH = kScaleBoxH;
constexpr float kBackgroundBoxW = kBackgroundSwitchX - kBackgroundBoxGap - kBackgroundBoxX;  // 216 wide
constexpr float kBackgroundBoxY = kPanelBackgroundTop + (kPanelRowH - kBackgroundBoxH) / 2;  // 254
constexpr float kBackgroundItemH = kScaleItemH, kBackgroundListGap = kScaleListGap;
constexpr float kPanelBrightnessTop = kPanelBackgroundTop + kPanelRowH;  // 284
FRect glass_background_box();                    // panel px
FRect glass_background_switch();                 // panel px
FRect glass_background_item(std::size_t item);   // panel px; empty for item >= kBackgroundCount
static_assert(kBackgroundBoxY - kBackgroundListGap - float(kBackgroundCount) * kBackgroundItemH >= kPanelHeaderH,
              "the open list stays under the header");
// Under that, the desktop row: kPanelActions buttons side by side, button i spanning
// [kPanelPad + i * (kPanelActionW + kPanelActionGap), + kPanelActionW) x [kPanelActionsTop, + kPanelActionH) (glass_panel_action):
// Ultrawide (toggles the desktop between 16:9 and ultrawide), Add (a new desktop), Remove (this desktop; asks for a second
// click). tools/native_spike/desktops.py does the work; free_pointer.py mirrors the geometry (glass_contract compares).
constexpr std::size_t kPanelActions = 3;
constexpr float kPanelActionsTop = kPanelBrightnessTop + kPanelRowH + 6.f;  // 324
constexpr float kPanelActionH = 32.f, kPanelActionW = 138.f, kPanelActionGap = 11.f;
static_assert(kPanelPad * 2 + kPanelActionW * float(kPanelActions) + kPanelActionGap * float(kPanelActions - 1) == kPanelW,
              "the desktop row spans the panel between its paddings");
constexpr float kPanelFooterTop = kPanelActionsTop + kPanelActionH + 8.f;  // 364
constexpr std::uint32_t kPanelTextPx = 24, kPanelHintPx = 18, kPanelActionPx = 20;  // em sizes with the system font
FRect glass_panel_action(std::size_t action);  // panel px; empty for action >= kPanelActions
// Button labels as drawn (the pixel font is ASCII; so are these). Remove reads "Remove?" while armed.
const char* glass_panel_action_label(std::size_t action, bool armed);
// Whether a button does anything now: nothing while busy; Add below kMaxDesktops desktops, Remove above one.
bool glass_panel_action_enabled(std::size_t action, const PlacementMenuState& state);
// The open panel hangs below its desktop, further than the view reaches with your head level, so while it is open the view
// is pitched down (the desktops, pills and panel move up as you see them; the world-locked background does not), easing with
// the panel's morph and back when it closes. A camera offset at draw time: nothing moves on the canvas, so the supervisor's
// hit testing, which works on the canvas, needs no change. It does not depend on where your head is, so it stays put
// while you look round and does not change as the panel moves from desktop to desktop.
// glass_panel_shift: how far (radians, at most kPanelShiftMax) to pitch down so the panel's bottom edge ends
// kPanelShiftMarginPx inside the view, looking straight at the desktop, or level at its height from straight ahead (the row's
// outer desktops sit lower in the view then). 0 when it fits.
constexpr float kPanelShiftMarginPx = 12.f, kPanelShiftMax = 0.26f;  // ~15 degrees: a huge desktop's panel is left to your head
constexpr double kPanelShiftMs = kGlassExpandMs;
float glass_panel_shift(const Canvas& canvas, const FRect& tile, const Placement& placement, bool options = false);
// `view` with the camera pitched down by `angle` radians: the rotation R(angle) * view in the eye's frame. `angle` 0: `view`.
Mat3 glass_view_shift(const Mat3& view, float angle);
// The shift's ease, on the renderer's monotonic clock (ms): from wherever it is to the latest target, over kPanelShiftMs.
class PanelShift {
public:
    void set(float target, double now_ms);
    float value(double now_ms) const;
    bool animating(double now_ms) const;
private:
    float from_ = 0, to_ = 0;
    double start_ms_ = -1e9;
};
// A small grabber capsule at the top centre of an open panel (where the pill was).
constexpr float kGrabberW = 44.f, kGrabberH = 5.f, kGrabberTop = 8.f;
static_assert(kPanelFooterTop + kPanelHintPx + 6.f <= kPanelH, "footer fits the panel");
static_assert(kPanelH == kPanelFooterTop + 30.f, "the panel ends 30 px below its footer's top (free_pointer.py PANEL_H)");

// Level bar (tools/native_spike/free_pointer.py level_bar_scale / level_bar_rect / level_bar_segment / LEVEL_*, compared by
// the glass_contract ctest): in the overview every desktop has one segmented capsule centred above its top edge, the pill's
// counterpart: turn left, level, turn right. Its left and right segments turn the desktop counter-clockwise and clockwise:
// a click a step (or to level, when level lies that way), a drag either way. Its middle segment levels it. It is tile px on
// the desktop's own surface, unlike the glass, so it moves, scales and rolls with its desktop: a resize (its distance) or a
// move (its orbit) carries it along and it stays the same share of the desktop. Drawn kLevelBarW x kLevelBarH,
// kLevelBarGap above the edge on a desktop at least kLevelBarW / kLevelBarShare wide; on a narrower one the whole design
// scales down (level_bar_scale) to kLevelBarShare of its width, not under kLevelBarMinW, and on one narrower than that to
// its width.
constexpr float kLevelBarW = 148.f, kLevelBarH = 26.f, kLevelBarGap = 12.f, kLevelBarPad = 4.f;
constexpr float kLevelBarShare = 0.25f, kLevelBarMinW = 120.f;
// The hit-zone contract round a desktop (free_pointer.py LEVEL_EDGE_CLEAR, hit), from the inside out: its content (its
// apps); a band kLevelEdgeClear wide just outside every edge (the selected desktop's border) for the edge's resize zone, and
// the corners' resize zones round each corner; then the bar; then empty space.
constexpr float kLevelEdgeClear = 6.f;
static_assert(kLevelBarGap - kLevelBarPad >= kLevelEdgeClear, "the bar's hit region stays out of the band along the edge");
static_assert(kLevelBarGap >= kBorderPx, "the bar stays clear of the selected desktop's border");
// The bar's segments, left to right: what `side` is on the wire (ipc.hpp RollPayload) and GlassFrame::bar_hover_segment.
enum class LevelSegment : std::int8_t { none = -1, ccw = 0, level = 1, cw = 2 };
inline constexpr std::size_t kLevelSegments = 3;
// What the bar and the corner caps are scaled by: 1, less on a narrow desktop. 0 for an empty tile.
float level_bar_scale(const FRect& tile);
// The bar as drawn: tile px on the desktop's surface, centred above its top edge. Empty for an empty tile.
FRect level_bar_rect(const FRect& tile);
// The segment at tile point (px, py): the drawn bar padded by kLevelBarPad * level_bar_scale, split at its thirds (the
// padding belongs to the segment it borders). none outside it. Only where nothing else is: any desktop's content, a
// corner's resize zone and a pill all win over it (free_pointer.py hit), and the renderer draws the bar under them.
LevelSegment level_bar_segment(const FRect& tile, float px, float py);

// Corner caps: a quiet rounded L wrapped round each corner of every desktop in the overview, drawn with the bar and scaled
// with it, the resize corners made visible. kCapGap outside the edges, legs kCapLeg long and kCapStroke wide; the corner
// under the dot or held lights up, legs kCapLegHot long and kCapStrokeHot wide. kCapOuter past the corner to kCapInner in
// from it holds every state, the box each is drawn in (corner_cap_rect). Pressing one is the corner's resize zone
// (free_pointer.py border_handle); the glass_contract ctest checks that the zone covers the idle cap.
constexpr float kCapGap = 8.5f, kCapLeg = 12.f, kCapLegHot = 22.f, kCapStroke = 2.4f, kCapStrokeHot = 3.2f, kCapBend = 5.f;
constexpr float kCapOuter = 11.f, kCapInner = 16.f;
static_assert(kCapGap - kCapStrokeHot / 2 >= kLevelEdgeClear, "a cap stays clear of the selected desktop's 6 px border");
static_assert(kCapGap + kCapStrokeHot / 2 <= kCapOuter && kCapLegHot - kCapGap + kCapStrokeHot / 2 <= kCapInner, "every state fits the box");
// Corner `corner`'s box (0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right), tile px. Empty for an empty tile or
// corner > 3.
FRect corner_cap_rect(const FRect& tile, std::uint32_t corner);

// The 3D button, Connected Tail (Cody's pick, option 02 of 3d-button-mockups; tools/native_spike/immersive.py;
// free_pointer.py immersive_main_rect / immersive_tail_rect / immersive_part / IMMERSIVE_*, compared by the glass_contract
// ctest): two controls joined to the bar in M3's connected-button style. Tile px, scaled like the bar, on its line and as
// tall: kImmersiveGap right of the bar the "3D" segment, this desktop's toggle, kImmersiveGap right of that the tail, three
// small desktops, every desktop at once. Corners kImmersiveInnerR but the tail's outer end, which is the capsule's.
// Shown with the bar, under the same rules (never takes a press from content, a corner or a pill). A desktop too narrow to
// hold both kImmersiveClear short of its top-right corner has neither: the rects are empty.
constexpr float kImmersiveGap = 2.f, kImmersiveMainW = 40.f, kImmersiveTailW = 28.f, kImmersiveInnerR = 4.f, kImmersiveClear = kCapInner;
constexpr float kImmersiveTextPx = 14.5f;                                               // the 3D's em size, tile px
constexpr float kImmersiveTickW = 4.2f, kImmersiveTickH = 9.f, kImmersiveTickGap = 1.8f, kImmersiveTickR = 1.1f;  // the tail's desktops
FRect immersive_main_rect(const FRect& tile);
FRect immersive_tail_rect(const FRect& tile);
// Which control is at tile point (px, py). Above, below and past the tail's end the zone is the drawn shape padded by
// kLevelBarPad * level_bar_scale, like the bar. The gaps are too narrow for padding, so each is split down its middle: the
// bar's padding gives way to the button there (free_pointer.hit asks the button first) and a press goes to what it is next to.
enum class ImmersivePart : std::uint8_t { none, main, tail };
ImmersivePart immersive_part(const FRect& tile, float px, float py);

// The bar's, the caps' and the 3D button's pixels: one premultiplied image per theme colour (`accent`, glass_theme_scheme) holding every
// state of each, kHandleAtlasScale px per tile px, in cells kHandleAtlasGutter px apart so that bilinear filtering stays in
// its cell. The bar is the pill's primary container with on-primary-container icons and dividers; a hot segment is a
// capsule in the primary with on-primary icons, the primary bright while dragged. A cap is the primary, the primary bright
// when hot. Every role is under the OLED cap. GlCompositor uploads it once per theme colour.
inline constexpr std::uint32_t kHandleAtlasScale = 3, kHandleAtlasGutter = 2;
// `font` sets the button's 3D, bold (kHudFontFamily); none: the built-in pixel font.
HudImage render_handle_atlas(std::array<float, 3> accent, const HudFont* font = nullptr);
// Where a state is in that image (0..1 of it). A bar: `segment` hot (none: idle), `active` while it is dragged. A cap:
// corner `corner`, `hot` under the dot or held, `active` while it is dragged.
struct AtlasBox { float u0 = 0, v0 = 0, u1 = 0, v1 = 0; };
AtlasBox options_icon_box(bool restore,bool hot=false);
AtlasBox options_hint_box(bool restore);
AtlasBox options_gear_box();
AtlasBox level_bar_box(LevelSegment segment, bool active);
AtlasBox corner_cap_box(std::uint32_t corner, bool hot, bool active);
// The 3D button's two controls. `lit`: the segment's desktop is 3D, the tail's every desktop is: the primary with on-primary
// ink, the primary bright under the dot (`hot`). Unlit: the secondary container, and under the dot the primary container,
// each with its on-colour (a tonal off, told apart from the bar's own fill).
AtlasBox immersive_main_box(bool lit, bool hot);
AtlasBox immersive_tail_box(bool lit, bool hot);

// --- Look ----------------------------------------------------------------------------------------------------------
// Pill states (GlassFrame::hover_level). Hover = the pointer is on the pill (or the open panel's header); active = it is
// being dragged. In M3 terms: enabled, hovered (state layer kMdStateHover, elevation 2) and dragged (kMdStateDragged,
// elevation 4).
constexpr float kGlassIdle = 1.f, kGlassHover = 1.4f, kGlassActive = 1.7f;
constexpr float kGlassCap = 191.f / 255.f;   // = kCardMaxLevel: the brightest any glass pixel may be
constexpr float kGlassShadowMargin = 32.f;   // canvas px drawn around the shape for its shadow
constexpr float kPanelFill = 0.94f;          // the open panel's opacity (the pill is opaque): a hint of what is behind it
// One glass shape in M3 terms: a container colour at `fill` opacity, a 1 px outline just inside its edge, a state layer
// (the content colour at `state_alpha`) over its top `state_px`, and an elevation shadow below it.
struct GlassLook {
    std::array<float, 3> container{}, outline{}, state{};
    float fill = 0, outline_alpha = 0, state_alpha = 0, state_px = 0;
    float shadow = 0, shadow_sigma = 1, shadow_dy = 0;
};
// M3's state layer opacity for a pill state: 0 idle, kMdStateHover at kGlassHover, kMdStateDragged at kGlassActive.
float glass_state_opacity(float level);
// The look `t` (eased 0..1) of the way from pill to panel, `accent` the Omarchy theme colour (glass_theme_scheme): the
// primary container with its on-colour as the state layer over the whole pill, then the surface container (high) with an
// outline-variant edge and the on-surface state layer over its header strip (kPanelHeaderH), the elevation rising from
// the pill's (1, or 2 hovered / 4 dragged) to the panel's (3, or 4 while its header is dragged).
GlassLook glass_look(std::array<float, 3> accent, float level, float t);
// The open panel's drag handle (M3: on-surface-variant at 40%), more opaque while hovered or dragged. No shadow.
GlassLook grabber_look(float level);
// One pixel (canvas px centre px, py) of a glass shape `rect` with corner `radius` at `opacity`: premultiplied RGBA,
// every colour channel at most kGlassCap. The GLSL fragment shader below computes exactly this.
std::array<float, 4> glass_shade(const GlassLook& look, const FRect& rect, float radius, float px, float py, float opacity);
// The shader's `style` uniform, four vec4s: container + fill, outline + its alpha, state layer + its alpha,
// (state_px, shadow, shadow_sigma, shadow_dy). One glUniform4fv(style, kGlassStyleVecs, ...) sets every style input.
constexpr int kGlassStyleVecs = 4;
std::array<float, 4 * kGlassStyleVecs> glass_style(const GlassLook& look);
// GLSL ES 1.00 fragment shader. Varying `p` = canvas px; uniforms rect (centre x, y, half w, half h), radius,
// style[kGlassStyleVecs] (glass_style), opacity. Premultiplied output: blend with GL_ONE, GL_ONE_MINUS_SRC_ALPHA.
const char* glass_fragment_shader();

// --- Panel content -----------------------------------------------------------------------------------------------------
// The open panel's text and sliders for the selected desktop (state.desktop), kPanelW x kPanelH, premultiplied, on a
// transparent background (the glass is drawn under it): header "XR-n" / "Placement", one row per field (label, slider
// filled from the flat value, value with units; the selected row on a faint accent band), the Resolution row (and its
// list while state.resolution_picker: over the slider rows), the Scale row (and its list while state.picker: over the
// Resolution and slider rows), the Background row and the Brightness row (and the Background list while
// state.background_picker: over the rows above it), the desktop row (Ultrawide lit
// while the desktop is ultrawide, Remove lit while armed, buttons that do nothing now dimmed), and a key-hint footer.
// Material Design 3 parts in `accent`'s scheme (glass_theme_scheme): text in on-surface / on-surface-variant, the selected
// row on a fully rounded secondary-container band, sliders with a primary track, fill and handle (the selected one with
// a focus halo), filled tonal buttons with icons (primary while on, error container while armed, M3 disabled). Every
// role is under the OLED cap, so nothing passes kCardMaxLevel.
struct OptionsMenuState;
HudImage render_options_panel(const OptionsMenuState&, const HudFont* font = nullptr, std::array<float, 3> accent = {.45f, .65f, .8f});
HudImage render_glass_panel(const PlacementMenuState& state, const HudFont* font = nullptr,
                            std::array<float, 3> accent = kGlassThemeDefault);
// Footer hint as drawn (font: arrows; pixel font: ASCII).
std::string glass_panel_hint(bool ascii);
// A desktop-row button's M3 colours in `accent`'s scheme: filled tonal (secondary container), the primary while on
// (Ultrawide), the error container while armed (Remove?), M3 disabled when it does nothing now (on-surface at 12% / 38%).
struct PanelButtonStyle { std::array<float, 3> container{}, content{}; float container_alpha = 1, content_alpha = 1; };
PanelButtonStyle glass_panel_button_style(std::size_t action, const PlacementMenuState& state,
                                          std::array<float, 3> accent = kGlassThemeDefault);

// --- Animation -----------------------------------------------------------------------------------------------------
constexpr float kContentFrom = 0.75f;  // the panel's content fades in over the last quarter of the expansion

// What the compositor draws this frame (GlassPills::frame and wayland.cpp fill it; overview only).
struct GlassFrame {
    bool options = false, maximized = false;
    bool pills = false;                       // collapsed pills under every desktop
    int hover = -1; float hover_level = kGlassIdle;  // the desktop whose pill is hovered or dragged
    std::array<float, kPlacementMenuDesktopCount> expand{};  // eased expansion per desktop
    int content = -1; const HudImage* image = nullptr; std::uint64_t generation = 0; float content_alpha = 0;
    // Drawn last, over every other pill: the open panel's desktop (or, while collapsing, the most expanded one). The
    // supervisor's hit testing treats the open panel as the top layer (free_pointer.hit), so this is what keeps the two
    // in step where a moved desktop's pill overlaps it. -1: none.
    int top = -1;
    // The open panel's drawing surface while the supervisor holds one of its sliders: the desktop's placement at the
    // press (the desktop itself follows its live placement). The supervisor measures the drag on that same surface, so
    // the knob stays under the pointer. Empty: the panel rides on its desktop's live placement.
    int surface_desktop = -1; std::optional<Placement> surface;
    // The level bars and corner caps (drawn with the pills: `pills` true) of the desktops in `bars`. Only one thing lights
    // at a time, what the dot is on or holds: the segment `bar_segment` of desktop `bar_hover`, the cap on corner
    // `cap_corner` of desktop `cap_hover`; `bar_active` / `cap_active` while dragged. They are drawn before every desktop
    // (any desktop's content covers them, the glass goes over them), on their desktop's live placement, never on the held
    // panel's `surface`.
    std::array<bool, kPlacementMenuDesktopCount> bars{};
    int bar_hover = -1; LevelSegment bar_segment = LevelSegment::none; bool bar_active = false;
    int cap_hover = -1; std::uint32_t cap_corner = 0; bool cap_active = false;
    // The 3D buttons, next to the bars and only with them: the desktops that are 3D, whether every desktop is (the tails
    // light), and the one under the dot (-1: none), on its tail or its segment.
    std::array<bool, kPlacementMenuDesktopCount> immersive{};
    bool immersive_global = false, immersive_tail = false;
    int immersive_hover = -1;
};
// Renderer placement from wire units (ipc.hpp WirePlacement: x / y the orbit round you in tenths of a degree, distance in
// percent, curvature in degrees) to radians and a distance factor: what set_placements applies.
Placement placement_from_wire(const MenuPlacement& wire);
// The glass draw order: `back_to_front` (view.hpp draw_order) with desktop `top` moved last (drawn over the others).
std::vector<std::size_t> glass_draw_order(std::vector<std::size_t> back_to_front, int top);

class GlassPills {
public:
    // Apply the supervisor's state: the open desktop's pill expands (any other collapses), and the content is
    // re-rendered (lazily, on the next image()) when anything it shows changed. `surface`: the placement the open panel
    // is drawn on while a slider is held (GlassFrame::surface). True if anything to draw changed.
    bool set(const PlacementMenuState& state, double now_ms, const std::optional<MenuPlacement>& surface = std::nullopt);
    void set_font(std::unique_ptr<HudFont> font);
    // The theme colour (Presentation::set_accent) of the panel's highlights. A change re-renders existing content (lazily,
    // under a new generation) and returns true; the same colour, or no content yet, returns false.
    bool set_accent(std::array<float, 3> accent);
    // This frame's expansion, content and draw order. Pills and panels are drawn in the overview only: while `overview`
    // is false (zoomed in or still zooming out) an expanding pill is held where it is (CardFade::hold), so a panel
    // opened from a zoomed-in view (Ctrl+Alt+L zooms out first) still morphs from the pill once it can be seen.
    // Everything is 0 / -1 while not in the overview. Leaves pills / hover for the caller.
    GlassFrame frame(bool overview, double now_ms);
    float expand(std::size_t desktop, double now_ms) const;  // eased 0..1
    bool animating(double now_ms) const;
    bool any_expanded(double now_ms) const;
    int content_desktop() const { return generation_ ? int(rendered_.desktop) : -1; }
    float content_alpha(double now_ms) const;  // of content_desktop(), 0 until kContentFrom
    const HudImage& image() const { ensure(); return image_; }
    std::uint64_t generation() const { return generation_; }
    std::uint64_t opened() const { return opened_; }
    std::uint64_t closed() const { return closed_; }
    std::uint64_t renders() const { return renders_; }
    bool open() const { return rendered_.open; }
    const std::optional<MenuPlacement>& surface() const { return surface_; }
private:
    void ensure() const;
    std::array<CardFade, kPlacementMenuDesktopCount> fades_{CardFade{kGlassExpandMs, kGlassCollapseMs}, CardFade{kGlassExpandMs, kGlassCollapseMs},
                                                            CardFade{kGlassExpandMs, kGlassCollapseMs}, CardFade{kGlassExpandMs, kGlassCollapseMs}};
    static_assert(kPlacementMenuDesktopCount == 4, "one fade per desktop");
    PlacementMenuState rendered_;
    std::optional<MenuPlacement> surface_;  // only while open
    double hidden_ms_ = -1;                 // the last frame drawn outside the overview (-1: the last one was in it)
    std::uint64_t generation_ = 0, opened_ = 0, closed_ = 0;
    mutable std::uint64_t renders_ = 0;
    mutable bool dirty_ = false;
    mutable HudImage image_;
    std::unique_ptr<HudFont> font_;
    std::array<float, 3> accent_ = kGlassThemeDefault;
};
}
