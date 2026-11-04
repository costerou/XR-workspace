#pragma once
// Head-locked status pop-ups in the glasses: the viewing mode (anchor / follow / locked) and
// cursor follow (on / off). CPU-only here: text rasterised into a small premultiplied RGBA card
// drawn 1:1 in output pixels, plus fade timing. Text uses an installed system font (HudFont:
// fontconfig + FreeType, local files only); if that is unavailable, a built-in 5x7 pixel font.
#include <array>
#include <functional>
#include <memory>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>
#include "xr/desktops.hpp"
#include "xr/material.hpp"

namespace xr {
// Premultiplied RGBA8, row 0 at the top, one texel per output pixel.
struct HudImage { std::uint32_t width = 0, height = 0; std::vector<std::uint8_t> rgba; };
// A line's colour can be a role of the card's Material scheme (material.hpp, from the theme accent the card is drawn with)
// instead of its own `color`: the cards follow the Omarchy theme and stay under the OLED cap.
enum class HudRole : std::uint8_t { none, on_surface, on_surface_variant, outline, primary, on_secondary_container };
// `scale` is a relative size step (1 small, 2 normal, 4 large); `bold` uses the font's bold face. `px`, when set,
// is the exact em size in pixels instead (the pixel font takes the nearest whole zoom, at least 1).
struct HudLine {
    std::string text; std::uint32_t scale = 1; std::array<float, 3> color{1, 1, 1}; bool bold = false; std::uint32_t px = 0;
    HudRole role = HudRole::none;  // none: `color`
};
// The colour a line is drawn in with `scheme`.
std::array<float, 3> hud_line_color(const HudLine& line, const MaterialScheme& scheme);

// Installed scalable font, looked up by exact family name. Not thread-safe; one per renderer.
class HudFont {
public:
    static std::unique_ptr<HudFont> load(const std::string& family, std::string& why);
    ~HudFont();
    struct Metrics { std::uint32_t ascent = 0, descent = 0; };
    using Plot = std::function<void(int x, int y, float coverage)>;
    Metrics metrics(std::uint32_t px, bool bold) const;
    std::uint32_t measure(std::string_view utf8, std::uint32_t px, bool bold) const;
    // Rasterise anti-aliased text with its baseline at `baseline`, starting at `left`.
    void draw(std::string_view utf8, std::uint32_t px, bool bold, int left, int baseline, const Plot& plot) const;
    const std::string& description() const;
private:
    HudFont() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
constexpr const char* kHudFontFamily = "JetBrainsMono Nerd Font";  // Omarchy's monospace font

constexpr std::uint32_t kGlyphW = 5, kGlyphH = 7;
// Width in texels of `text` at `scale` (one blank column between glyphs, none trailing).
std::uint32_t hud_text_width(std::string_view text, std::uint32_t scale);
// Material Design 3 card: an extra-large rounded surface container (high) with a 1 px outline-variant edge, in the M3
// scheme of theme colour `accent` (material.hpp, as the glass pills: glass_theme_scheme), and the lines centred, top to
// bottom, each in its role's colour (HudLine::role) or its own. With no `font`, the built-in pixel font is used.
HudImage render_hud_card(const std::vector<HudLine>& lines, std::array<float, 3> accent, const HudFont* font = nullptr);
// Uppercase-only glyph rows (bit 4 = leftmost column); lowercase maps to uppercase, unknown to '?'.
const std::array<std::uint8_t, kGlyphH>& hud_glyph(char c);

// Pop-up opacity `elapsed_ms` after it was shown: fade in, hold, fade out; 0 outside.
constexpr double kPopupFadeInMs = 120, kPopupHoldMs = 1500, kPopupFadeOutMs = 380;
constexpr double kPopupTotalMs = kPopupFadeInMs + kPopupHoldMs + kPopupFadeOutMs;
float popup_alpha(double elapsed_ms);

// mode: 0 anchor, 1 locked, 2 follow (the ViewPayload encoding). Each pop-up section: what it is (on-surface-variant),
// its new state (large, bold, the theme's primary), what that means (on-surface-variant) and its key (outline).
std::vector<HudLine> view_mode_lines(std::uint8_t mode);
std::vector<HudLine> cursor_follow_lines(bool on);
std::vector<HudLine> prediction_lines(bool on);
// Typing mode (Ctrl+Alt+T). Every pop-up is drawn in its M3 scheme's roles, under the OLED cap (kCardMaxLevel).
std::vector<HudLine> typing_lines(bool on);

class StatusPopups {
public:
    // Feed every view update. The first shows the viewing mode; afterwards a pop-up appears only
    // for what changed (both sections if both changed). Returns true if a pop-up (re)started.
    bool update(std::uint8_t mode, bool cursor_follow, double now_ms) { return update(mode, cursor_follow, prediction_, now_ms); }
    // Prediction toggles get their own section; the initial prediction state is not announced.
    bool update(std::uint8_t mode, bool cursor_follow, bool prediction, double now_ms) { return update(mode, cursor_follow, prediction, typing_, now_ms); }
    // Typing mode switches get their own section; every run starts in control mode, which is not announced. A card that
    // shows only the typing section uses the dimmed card accent too, so nothing on it exceeds kCardMaxLevel.
    bool update(std::uint8_t mode, bool cursor_follow, bool prediction, bool typing, double now_ms);
    // A viewing-mode change that is no news: a maximize switches to follow and an un-maximize back to anchor by themselves
    // (maximize-follow), so only Ctrl+Alt+V's changes get the mode card. Call before update(): the mode is taken without
    // a pop-up. Before the first update it does nothing (the first update still shows the starting mode).
    void track_mode(std::uint8_t mode) { if (seen_) mode_ = mode > 2 ? 0 : mode; }
    void set_font(std::unique_ptr<HudFont> font);  // applies to the next card; nullptr: pixel font
    const HudFont* font() const { return font_.get(); }
    // The theme colour (Presentation::set_accent) the cards are drawn in. A change redraws a card that is still showing,
    // in place (a new generation, the same fade), and returns true.
    bool set_accent(std::array<float, 3> accent, double now_ms);
    float alpha(double now_ms) const;
    bool active(double now_ms) const { return generation_ && now_ms - shown_ms_ < kPopupTotalMs; }
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }  // bumps when the card changes
private:
    bool seen_ = false, cursor_follow_ = false, prediction_ = false, typing_ = false;
    std::uint8_t mode_ = 0;
    double shown_ms_ = 0;
    std::uint64_t generation_ = 0;
    HudImage image_;
    std::vector<HudLine> lines_;  // the card shown last
    std::array<float, 3> accent_ = kMdDefaultSeed;
    std::unique_ptr<HudFont> font_;
};

// Keyboard help (Ctrl+Alt+H): a centred card listing every bind, toggled by the supervisor.
// Fades in/out with an eased curve and a small rise; toggling mid-fade reverses from the
// current opacity instead of jumping.
// Fit: the card keeps the menu's chrome (padding, border, radius, colours) but has its own, more compact text sizes and
// gaps. Given the output surface (`max_width` x `max_height`, 0 = unbounded) it takes the largest text size (row em
// kHelpRowPxMax down to kHelpRowPxMin) whose card, with every row on it, leaves kHudFitMargin free on every side.
// Pages: if even the smallest text does not fit every row, the card keeps that smallest text and splits the rows into
// pages instead (any number of rows): each page holds what fits the height, a section (rows from one `section` row up to
// the next) is kept on one page when it fits one, and every page has the same card size, a "2 / 3" indicator between a
// previous and a next arrow, and a hint naming the page keys. The supervisor turns pages (HelpOverlay::set `turn`); the
// arrows and the indicator are also clicked with the overview free pointer (HelpCardControls, help_geometry_json).
// fits=false only if a single row is wider or taller than the surface allows.
struct CardOrigin { float x = 0, y = 0; };  // a centred card's top-left, output px
struct HelpRow { std::string keys, action; bool section = false; };  // section: this row starts a new section
constexpr std::uint32_t kHudFitMargin = 34;  // free space the help card leaves on every side of the output (= its padding)
constexpr std::uint32_t kHelpRowPxMax = 30, kHelpRowPxMin = 18;  // row text em size in pixels, roomiest / smallest
// Page keys named by the hint on a paged card (tools/native_spike/view.py BINDS help_prev / help_next; a test compares).
constexpr const char* kHelpPageHint = "Ctrl+Alt+PgUp / PgDn or click an arrow for more";
struct HelpCardFit {
    std::uint32_t width = 0, height = 0, row_px = 0; bool fits = false;
    std::vector<std::uint32_t> page_rows;  // rows on each page, in order (one entry: everything on one page)
    std::uint32_t pages() const { return std::uint32_t(page_rows.size()); }
};
HelpCardFit help_card_fit(const std::vector<HelpRow>& rows, const HudFont* font = nullptr, std::uint32_t max_width = 0,
                          std::uint32_t max_height = 0);
// Clickable parts of a paged card, card pixels from its top-left (all empty on a one-page card). The indicator turns
// to the next page like the next arrow.
struct HelpRect { std::int32_t x = 0, y = 0, w = 0, h = 0; bool empty() const { return w <= 0 || h <= 0; } };
struct HelpCardControls { HelpRect prev, indicator, next; };
// `page` is taken modulo the page count.
HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font = nullptr,
                          std::uint32_t max_width = 0, std::uint32_t max_height = 0, std::uint32_t page = 0,
                          HelpCardControls* controls = nullptr);
// The supervisor's free-pointer hit testing for the help card: one status line
// {"help_geometry":{"card":[x,y,w,h],"prev":[...],"indicator":[...],"next":[...]}} (prev / indicator / next only on a
// paged card) or {"help_geometry":null}, canvas px (the overview's plane, as overview_geometry). The card is head-locked
// and the canvas world-locked, so each rect is where the card's rect (output px, top-left `origin`) lands on the canvas
// under head rotation `view` (pose.hpp, row-major), as its bounding box. Empty if the canvas is unusable.
std::string help_geometry_json(const std::array<float, 9>& view, std::uint32_t width, std::uint32_t height, float half_w,
                               float half_h, CardOrigin origin, std::uint32_t card_w, std::uint32_t card_h,
                               const HelpCardControls& controls);
constexpr double kHelpFadeInMs = 220, kHelpFadeOutMs = 260;
// Image-retention protection for the centred help card and the placement panels (glass.hpp) on the glasses' micro-OLED panels:
// Material roles scaled under the cap (material.hpp kMdOledLevel) and an idle auto-close. The cards never move while shown.
// The supervisor closes both cards after this many seconds without a run key or panel change. The value lives in
// tools/native_spike/view.py OVERLAY_IDLE_CLOSE_S; this copy is only for the help card's footer note (a test pins them).
constexpr std::uint32_t kOverlayIdleCloseS = 30;
// Brightest channel (0..255) any pixel of the help card or a placement panel may reach; full white is 255.
constexpr std::uint8_t kCardMaxLevel = 191;
constexpr float kHelpRisePx = 18;  // the card rises this far while fading in (and sinks fading out)

// Eased show/hide fade shared by the centred cards: toggling mid-fade reverses from the current
// opacity instead of jumping.
class CardFade {
public:
    CardFade(double in_ms, double out_ms) : in_ms_(in_ms), out_ms_(out_ms) {}
    bool set(bool visible, double now_ms);   // true if visibility changed
    bool visible() const { return visible_; }
    float level(double now_ms) const;        // linear 0..1
    float alpha(double now_ms) const;        // eased 0..1
    bool animating(double now_ms) const;     // mid-fade
    // The fade did not run from `since_ms` to `now_ms` (its card could not be seen): at now_ms it is where it was at
    // since_ms, or where its last toggle left it if that came later. Call it on every unseen frame to keep it still.
    void hold(double since_ms, double now_ms);
private:
    double in_ms_, out_ms_;
    bool visible_ = false;
    float from_ = 0;                         // level at changed_ms_ (the last toggle or hold)
    double changed_ms_ = 0;
};

// Top-left (from the top of the output) of a card drawn centred, lowered by `drop_px` while it fades in: the one
// formula GlHud uses for the help card (CardOrigin is declared with the help card above).
CardOrigin centered_card_origin(std::uint32_t output_w, std::uint32_t output_h, std::uint32_t card_w, std::uint32_t card_h, float drop_px);

class HelpOverlay {
public:
    // Show/hide; rows are rendered when shown with different rows (or the first time). Showing a hidden card starts it
    // on page 1. `turn` (-1 / +1) flips a shown card to the previous / next page, wrapping round; ignored when hidden.
    // Returns true if visibility changed or a shown card was redrawn (new page or new rows).
    bool set(bool visible, const std::vector<HelpRow>& rows, double now_ms, int turn = 0);
    void set_font(std::unique_ptr<HudFont> font);
    // The theme colour (Presentation::set_accent) the card is drawn in; a change re-renders a rendered card (true).
    bool set_accent(std::array<float, 3> accent);
    // Output surface size the card must fit (0 = unbounded); a rendered card is laid out again when it changes.
    bool set_viewport(std::uint32_t width, std::uint32_t height);
    bool visible() const { return fade_.visible(); }
    std::uint64_t shown_count() const { return shown_; }
    std::uint64_t hidden_count() const { return hidden_; }
    std::uint64_t turns() const { return turns_; }
    std::uint32_t page() const { return page_; }
    std::uint32_t pages() const { return pages_; }
    const HelpCardControls& controls() const { return controls_; }  // card px; empty on a one-page card
    float alpha(double now_ms) const;        // eased 0..1
    float offset_px(double now_ms) const;    // downward offset in output pixels, 0 when fully shown
    bool animating(double now_ms) const;     // mid-fade: redraw every frame
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }
private:
    CardFade fade_{kHelpFadeInMs, kHelpFadeOutMs};
    std::uint64_t shown_ = 0, hidden_ = 0, turns_ = 0;  // actual visibility changes and page turns, diagnostic only
    void render();                           // rows_ and page_ for the viewport, bumps generation_
    std::vector<HelpRow> rows_;
    std::uint32_t max_w_ = 0, max_h_ = 0, page_ = 0, pages_ = 1;
    HelpCardControls controls_;
    std::uint64_t generation_ = 0;
    HudImage image_;
    std::array<float, 3> accent_ = kMdDefaultSeed;
    std::unique_ptr<HudFont> font_;
};

// Placement state (supervisor-owned, ipc.hpp PlacementMenuPayload): which desktop's glass pill is expanded into its
// placement panel (`open`, `desktop`), the selected field, and every desktop's placement. The panel itself is drawn by
// glass.hpp; these are the field table and formatting it shares with the tests.
constexpr std::size_t kPlacementMenuFields = 4, kPlacementMenuDesktopCount = kMaxDesktops;
constexpr std::size_t kPlacementMenuBrightness = kPlacementMenuFields;  // `field` value: the background's Brightness row is selected
// {x, y, distance, curvature} in the wire units of ipc.hpp WirePlacement (every desktop always faces you: no facing field).
using MenuPlacement = std::array<std::int32_t, kPlacementMenuFields>;
struct PlacementMenuState {
    bool open = false;
    std::uint32_t desktop = 0, field = 0;  // 0..count-1, 0..3 or kPlacementMenuBrightness (out of range: drawn with nothing selected)
    std::array<MenuPlacement, kPlacementMenuDesktopCount> placements{};
    // The panel's desktop row (Ultrawide / Add / Remove): how many desktops the run has, each one's output number (the n
    // of XR-n, in capture order: a removed desktop leaves a gap), whether the open desktop is ultrawide, the Remove button
    // armed (a second click removes), and a desktop change in progress (every button dimmed).
    std::uint32_t count = 3;
    std::array<std::uint8_t, kPlacementMenuDesktopCount> numbers{1, 2, 3, 4};
    bool wide = false, armed = false, busy = false;
    // The panel's Scale dropdown (glass.hpp glass_scale_box): the open desktop's Hyprland output scale in percent (one
    // of glass.hpp kScalePercents; 0 = unknown: the box is drawn empty and dimmed), the steps it can take (bit i =
    // kScalePercents[i] divides its resolution exactly), and its list open.
    std::uint16_t scale = 0;
    std::uint8_t scales = 0;
    bool picker = false;
    // The Resolution dropdown above it (glass.hpp glass_resolution_box): the open desktop's pixel size (0 x 0 = unknown:
    // the box is drawn empty and dimmed) and its list open (never together with the Scale list).
    std::uint16_t resolution_w = 0, resolution_h = 0;
    bool resolution_picker = false;
    // The Background dropdown and Brightness slider (glass.hpp glass_background_box; xr/environment.hpp), the same in every
    // desktop's panel: the background (index into kBackgrounds), its brightness in percent, and the dropdown's list open.
    std::uint8_t background = 0;
    std::uint16_t brightness = 100;
    bool background_picker = false;
    bool operator==(const PlacementMenuState&) const = default;
};
// Slider range of each field, {lo, hi}: the same bounds the renderer accepts (ipc.cpp kPlacementBounds, tested).
std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> placement_menu_bounds();
// The flat (default) value of each field: sliders fill from it to the current value.
MenuPlacement placement_menu_flat();
struct PlacementMenuRow { std::string label, hint, value; bool selected = false; };
// Label, plain-English hint and formatted value ("100%", "60°"; " deg" with the pixel font, which is ASCII-only) for each
// field of the selected desktop. The orbit's x / y (tenths of a degree) show rounded to whole degrees, half away from zero,
// so they fit the value column: "+13°", "-180°", "0°" ("+13deg" with the pixel font).
std::vector<PlacementMenuRow> placement_menu_rows(const PlacementMenuState& state, bool ascii_units);
}
