#pragma once
// Glass pills: a frosted-glass capsule under each XR desktop in the overview, on that desktop's own surface (it faces you
// with its desktop, and a curved desktop curves its pill). Dragging a pill moves its desktop; clicking it (or Ctrl+Alt+L for
// the selected desktop) expands it, with a smooth morph, into that desktop's placement panel: the four placement sliders,
// the selected one highlighted. Only one panel is open at a time. The supervisor owns the state (ipc.hpp
// PlacementMenuPayload: open + desktop = the expanded pill) and the hit testing (tools/native_spike/free_pointer.py,
// which mirrors every geometry constant here; a contract test compares them). Pure and offline-testable, except the
// fragment shader source, which glass_shade() mirrors on the CPU.
//
// Look: a dark translucent tint in the theme's hue, lighter toward the top, a soft top gloss, a one-pixel
// hairline brighter at the top, rounded ends and a soft drop shadow. On the glasses' micro-OLED panels black is
// see-through, so the glass is mostly its own tint: nothing on it exceeds kCardMaxLevel (191/255), the panel is dark
// behind its text, and nothing moves on its own.
#include "xr/glass_theme.hpp"
#include "xr/hud.hpp"
#include "xr/view.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace xr {
// --- Geometry: canvas px on the desktop's surface, relative to its overview tile ----------------------------------------
// Collapsed pill: a share of the desktop's width (clamped), centred, kGlassPillGap below its bottom edge.
constexpr float kGlassPillShare = 0.24f, kGlassPillMinW = 64.f, kGlassPillMaxW = 160.f, kGlassPillH = 14.f, kGlassPillGap = 10.f;
FRect glass_pill(const FRect& tile);
// Expanded panel: kPanelW x kPanelH, centred under the desktop, its top where the pill's top is.
constexpr float kPanelW = 480.f, kPanelH = 258.f, kPanelRadius = 18.f;
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
// Under the sliders, the desktop row: kPanelActions buttons side by side, button i spanning
// [kPanelPad + i * (kPanelActionW + kPanelActionGap), + kPanelActionW) x [kPanelActionsTop, + kPanelActionH) (glass_panel_action):
// Ultrawide (toggles the desktop between 16:9 and ultrawide), Add (a new desktop), Remove (this desktop; asks for a second
// click). tools/native_spike/desktops.py does the work; free_pointer.py mirrors the geometry (glass_contract compares).
constexpr std::size_t kPanelActions = 3;
constexpr float kPanelActionsTop = kPanelRowsTop + float(kPlacementMenuFields) * kPanelRowH + 6.f;  // 188
constexpr float kPanelActionH = 32.f, kPanelActionW = 138.f, kPanelActionGap = 11.f;
static_assert(kPanelPad * 2 + kPanelActionW * float(kPanelActions) + kPanelActionGap * float(kPanelActions - 1) == kPanelW,
              "the desktop row spans the panel between its paddings");
constexpr float kPanelFooterTop = kPanelActionsTop + kPanelActionH + 8.f;  // 228
constexpr std::uint32_t kPanelTextPx = 24, kPanelHintPx = 18, kPanelActionPx = 20;  // em sizes with the system font
FRect glass_panel_action(std::size_t action);  // panel px; empty for action >= kPanelActions
// Button labels as drawn (the pixel font is ASCII; so are these). Remove reads "Remove?" while armed.
const char* glass_panel_action_label(std::size_t action, bool armed);
// Whether a button does anything now: nothing while busy; Add below kMaxDesktops desktops, Remove above one.
bool glass_panel_action_enabled(std::size_t action, const PlacementMenuState& state);
// A small grabber capsule at the top centre of an open panel (where the pill was).
constexpr float kGrabberW = 44.f, kGrabberH = 5.f, kGrabberTop = 8.f;
static_assert(kPanelFooterTop + kPanelHintPx + 6.f <= kPanelH, "footer fits the panel");
static_assert(kPanelH == kPanelFooterTop + 30.f, "the panel ends 30 px below its footer's top (free_pointer.py PANEL_H)");

// Roll-level icons (tools/native_spike/free_pointer.py roll_icon_rect / ROLL_*, compared by the glass_contract ctest): when
// a desktop is not level (view.hpp desktop_tilt, the supervisor deciding from the renderer's `roll_geometry` line), a
// curved arrow alone (no button behind it) sits kRollIconGap above its top corner on the side that is higher, on the
// desktop's own surface: above the top-left corner a counter-clockwise one, above the top-right a clockwise one, the way
// a click (level at once) or a drag (roll toward level, never past it) turns the desktop. Its square (the hit box, padded
// by free_pointer.py ROLL_PAD) is kRollIconPx across as you
// see it whatever the desktop's size: its tile px are scaled by the desktop's distance. Side 0 = top-left (counter-
// clockwise), 1 = top-right (clockwise).
constexpr float kRollIconPx = 22.f, kRollIconGap = 8.f;
FRect roll_icon(const FRect& tile, std::uint32_t side, float distance);
// The arrow filling the icon's square, canvas px: one plain curved arrow, a thick half-turn arc over the top (a triangle strip, outer
// then inner per step) and a large arrowhead (one triangle) at its end, clockwise for side 1, its mirror image (counter-
// clockwise) for side 0: the way a click or drag on it turns the desktop. Empty for an empty icon.
struct RollArrow { std::vector<std::array<float, 2>> band; std::array<std::array<float, 2>, 3> head{}; };
RollArrow roll_arrow(const FRect& icon, std::uint32_t side);
// The arrow's colour: the monitor border's and glow's, the theme accent itself (GlCompositor::set_accent, live with the
// Omarchy theme); hovered or dragged (level above kGlassIdle) it is lifted toward white, up to 30% at kGlassActive.
std::array<float, 3> roll_arrow_color(std::array<float, 3> accent, float level);

// --- Look ----------------------------------------------------------------------------------------------------------
// Pill states: brightness of the gloss, hairline and tint. Hover = the pointer is on the pill (or the open panel's
// header); active = it is being dragged.
constexpr float kGlassIdle = 1.f, kGlassHover = 1.4f, kGlassActive = 1.7f;
constexpr float kGlassCap = 191.f / 255.f;   // = kCardMaxLevel: the brightest any glass pixel may be
constexpr float kGlossLevel = 0.86f, kHairLevel = 0.88f;  // grey of the gloss, brightest hairline channel, before alpha
constexpr float kGlassShadowMargin = 32.f;   // canvas px drawn around the shape for its shadow
struct GlassLook {
    std::array<float, 3> tint{};             // base colour (lightened toward the top)
    std::array<float, 3> hair{kHairLevel, kHairLevel, kHairLevel};  // the hairline's colour before its alpha
    float fill = 0, gloss = 0, gloss_px = 1, level = kGlassIdle;
    float border_top = 0, border_bottom = 0, shadow = 0, shadow_sigma = 1, shadow_dy = 0;
};
// The look `t` (eased 0..1) of the way from pill to panel: the pill is a little brighter, the open panel darker behind
// its text. `accent` is the Omarchy theme colour: the glass takes its hue (glass_theme_tint).
GlassLook glass_look(std::array<float, 3> accent, float level, float t);
GlassLook grabber_look(float level);
// One pixel (canvas px centre px, py) of a glass shape `rect` with corner `radius` at `opacity`: premultiplied RGBA,
// every colour channel at most kGlassCap. The GLSL fragment shader below computes exactly this.
std::array<float, 4> glass_shade(const GlassLook& look, const FRect& rect, float radius, float px, float py, float opacity);
// GLSL ES 1.00 fragment shader. Varying `p` = canvas px; uniforms rect (centre x, y, half w, half h), radius, tint, hair,
// look (fill, gloss, gloss_px, level), edge (border_top, border_bottom, shadow, shadow_sigma), shadow_dy, opacity.
// Premultiplied output: blend with GL_ONE, GL_ONE_MINUS_SRC_ALPHA.
const char* glass_fragment_shader();

// --- Panel content -----------------------------------------------------------------------------------------------------
// The open panel's text and sliders for the selected desktop (state.desktop), kPanelW x kPanelH, premultiplied, on a
// transparent background (the glass is drawn under it): header "XR-n" / "Placement", one row per field (label, slider
// filled from the flat value, value with units; the selected row on a faint accent band), the desktop row (Ultrawide lit
// while the desktop is ultrawide, Remove lit while armed, buttons that do nothing now dimmed), and a key-hint footer.
// Text in the help card's dimmed colours, the band and the fills in `accent`'s (glass_theme_highlight), so nothing
// passes kCardMaxLevel.
HudImage render_glass_panel(const PlacementMenuState& state, const HudFont* font = nullptr,
                            std::array<float, 3> accent = kGlassThemeDefault);
// Footer hint as drawn (font: arrows; pixel font: ASCII).
std::string glass_panel_hint(bool ascii);

// --- Animation -----------------------------------------------------------------------------------------------------
constexpr double kGlassExpandMs = 260, kGlassCollapseMs = 200;
constexpr float kContentFrom = 0.75f;  // the panel's content fades in over the last quarter of the expansion

// What the compositor draws this frame (GlassPills::frame and wayland.cpp fill it; overview only).
struct GlassFrame {
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
    // Roll-level icons (drawn with the pills: `pills` true): per desktop -1 none, else the side (roll_icon); the hovered
    // or dragged one is drawn at `roll_level`.
    std::array<int, kPlacementMenuDesktopCount> roll_icon{-1, -1, -1, -1};
    int roll_hover = -1; float roll_level = kGlassIdle;
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
