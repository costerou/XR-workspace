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

namespace xr {
// Premultiplied RGBA8, row 0 at the top, one texel per output pixel.
struct HudImage { std::uint32_t width = 0, height = 0; std::vector<std::uint8_t> rgba; };
// `scale` is a relative size step (1 small, 2 normal, 4 large); `bold` uses the font's bold face.
struct HudLine { std::string text; std::uint32_t scale = 1; std::array<float, 3> color{1, 1, 1}; bool bold = false; };

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
// Rounded dark card with an `accent` border and the lines centred, top to bottom. With no
// `font`, the built-in pixel font is used.
HudImage render_hud_card(const std::vector<HudLine>& lines, std::array<float, 3> accent, const HudFont* font = nullptr);
// Uppercase-only glyph rows (bit 4 = leftmost column); lowercase maps to uppercase, unknown to '?'.
const std::array<std::uint8_t, kGlyphH>& hud_glyph(char c);

// Pop-up opacity `elapsed_ms` after it was shown: fade in, hold, fade out; 0 outside.
constexpr double kPopupFadeInMs = 120, kPopupHoldMs = 1500, kPopupFadeOutMs = 380;
constexpr double kPopupTotalMs = kPopupFadeInMs + kPopupHoldMs + kPopupFadeOutMs;
float popup_alpha(double elapsed_ms);

// mode: 0 anchor, 1 locked, 2 follow (the ViewPayload encoding).
std::vector<HudLine> view_mode_lines(std::uint8_t mode);
std::vector<HudLine> cursor_follow_lines(bool on);
std::vector<HudLine> prediction_lines(bool on);

class StatusPopups {
public:
    // Feed every view update. The first shows the viewing mode; afterwards a pop-up appears only
    // for what changed (both sections if both changed). Returns true if a pop-up (re)started.
    bool update(std::uint8_t mode, bool cursor_follow, double now_ms) { return update(mode, cursor_follow, prediction_, now_ms); }
    // Prediction toggles get their own section; the initial prediction state is not announced.
    bool update(std::uint8_t mode, bool cursor_follow, bool prediction, double now_ms);
    void set_font(std::unique_ptr<HudFont> font);  // applies to the next card; nullptr: pixel font
    const HudFont* font() const { return font_.get(); }
    float alpha(double now_ms) const;
    bool active(double now_ms) const { return generation_ && now_ms - shown_ms_ < kPopupTotalMs; }
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }  // bumps when the card changes
private:
    bool seen_ = false, cursor_follow_ = false, prediction_ = false;
    std::uint8_t mode_ = 0;
    double shown_ms_ = 0;
    std::uint64_t generation_ = 0;
    HudImage image_;
    std::unique_ptr<HudFont> font_;
};

// Keyboard help (Ctrl+Alt+H): a centred card listing every bind, toggled by the supervisor.
// Fades in/out with an eased curve and a small rise; toggling mid-fade reverses from the
// current opacity instead of jumping.
struct HelpRow { std::string keys, action; };
HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font = nullptr);
constexpr double kHelpFadeInMs = 220, kHelpFadeOutMs = 260;
// Image-retention protection for the centred cards (help card, placement menu) on the glasses' micro-OLED panels:
// dimmed text and accent (see hud.cpp kCardText) and an idle auto-close. The cards never move while shown.
// The supervisor closes both cards after this many seconds without a run key or panel change. The value lives in
// tools/native_spike/view.py OVERLAY_IDLE_CLOSE_S; this copy is only for the help card's footer note (a test pins them).
constexpr std::uint32_t kOverlayIdleCloseS = 30;
// Brightest channel (0..255) any pixel of the help card or menu may reach; full white is 255.
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
private:
    double in_ms_, out_ms_;
    bool visible_ = false;
    float from_ = 0;                         // level when the last toggle happened
    double changed_ms_ = 0;
};

class HelpOverlay {
public:
    // Show/hide; rows are rendered when shown with different rows (or the first time).
    // Returns true if visibility changed.
    bool set(bool visible, const std::vector<HelpRow>& rows, double now_ms);
    void set_font(std::unique_ptr<HudFont> font);
    bool visible() const { return fade_.visible(); }
    std::uint64_t shown_count() const { return shown_; }
    std::uint64_t hidden_count() const { return hidden_; }
    float alpha(double now_ms) const;        // eased 0..1
    float offset_px(double now_ms) const;    // downward offset in output pixels, 0 when fully shown
    bool animating(double now_ms) const;     // mid-fade: redraw every frame
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }
private:
    CardFade fade_{kHelpFadeInMs, kHelpFadeOutMs};
    std::uint64_t shown_ = 0, hidden_ = 0;  // actual visibility changes, diagnostic only
    std::vector<HelpRow> rows_;
    std::uint64_t generation_ = 0;
    HudImage image_;
    std::unique_ptr<HudFont> font_;
};

// Keyboard placement menu (supervisor-owned state, ipc.hpp PlacementMenuPayload): a centred card in the
// help card's style showing the selected desktop, the seven placement fields, each with its label, a slider and
// its value with units (highlighting the selected field), and a key-hint footer. Fades like the help card; value
// changes while open re-render the card without a fade. In mouse mode a pointer is drawn over the card, as its own
// small overlay: moving it never re-renders the card.
constexpr std::size_t kPlacementMenuFields = 7, kPlacementMenuDesktopCount = 3;
// {x, y, distance, yaw, pitch, roll, curvature} in the wire units of ipc.hpp WirePlacement.
using MenuPlacement = std::array<std::int32_t, kPlacementMenuFields>;
struct PlacementMenuState {
    bool open = false;
    std::uint32_t desktop = 0, field = 0;  // 0..2, 0..6 (out of range: drawn with nothing selected)
    std::array<MenuPlacement, kPlacementMenuDesktopCount> placements{};
    bool operator==(const PlacementMenuState&) const = default;
};
// Slider range of each field, {lo, hi}: the same bounds the renderer accepts (ipc.cpp kPlacementBounds, tested).
std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> placement_menu_bounds();
// Slider width in pixels: as wide as the output allows, between these (text sizes never change).
constexpr std::uint32_t kSliderMinW = 96, kSliderMaxW = 260;
struct PlacementMenuRow { std::string label, hint, value; bool selected = false; };
// Label, plain-English hint and formatted value ("+12%", "100%", "-5°"; " deg" with the pixel font,
// which is ASCII-only) for each field of the selected desktop.
std::vector<PlacementMenuRow> placement_menu_rows(const PlacementMenuState& state, bool ascii_units);
// Footer key-hint lines exactly as drawn on the card for this font and output (defaults: pixel font, unbounded),
// one string per footer line.
std::vector<std::string> placement_menu_hints(const HudFont* font = nullptr, std::uint32_t max_width = 0, std::uint32_t max_height = 0);
// The individual key hints ("keys  action"), full or short wording, that the footer packs into lines.
std::vector<std::string> placement_menu_hint_segments(bool short_wording = false);
// Fit. The card is built from the help card's parts (font, sizes, padding, spacing, colours) and sized
// to its content. Given the output surface (`max_width` x `max_height`, 0 = unbounded) it takes the
// roomiest arrangement whose card leaves kHudFitMargin free on every side: title above the desktop
// tabs; tabs beside the title; key hints allowed wider than the table (fewer lines); shorter hint
// wording; then (small outputs) a window of rows around the selected field with scroll arrows, and
// finally without the yaw/pitch/roll hint column. Text is never scaled. If even one row cannot fit,
// the most compact card is used (fits=false).
constexpr std::uint32_t kHudFitMargin = 34;  // = the card padding
struct PlacementMenuFit {
    std::uint32_t width = 0, height = 0;
    bool tabs_beside_title = false, wide_footer = false, short_hints = false;
    std::uint32_t footer_lines = 0;
    bool fits = false;
    std::uint32_t visible_rows = kPlacementMenuFields, first_row = 0;  // small outputs show a window of rows
    bool hint_column = true;
    std::uint32_t slider_w = 0;  // kSliderMinW..kSliderMaxW
};
PlacementMenuFit placement_menu_fit(const PlacementMenuState& state, const HudFont* font, std::uint32_t max_width, std::uint32_t max_height);
HudImage render_placement_menu_card(const PlacementMenuState& state, std::array<float, 3> accent, const HudFont* font = nullptr,
                                    std::uint32_t max_width = 0, std::uint32_t max_height = 0);
// Where things are on that card, in card pixels from its top-left: the supervisor hit-tests mouse-mode clicks with it
// (tools/native_spike/view.py MenuGeometry). Depends on the layout only (font, output, which rows show), never on values.
struct MenuGeometry {
    std::uint32_t width = 0, height = 0;
    std::array<std::array<std::int32_t, 4>, kPlacementMenuDesktopCount> tabs{};  // x0, y0, x1, y1 of each desktop tab
    struct Slider {
        std::uint32_t field = 0;
        std::int32_t top = 0, bottom = 0;  // the row's band, half a row gap above and below its text
        float lo_x = 0, hi_x = 0;          // knob centre at the field's low / high bound
        bool operator==(const Slider&) const = default;
    };
    std::vector<Slider> sliders;           // the rows shown, top to bottom
    bool operator==(const MenuGeometry&) const = default;
};
MenuGeometry placement_menu_geometry(const PlacementMenuState& state, const HudFont* font = nullptr, std::uint32_t max_width = 0,
                                     std::uint32_t max_height = 0);
// One status line for the supervisor: {"menu_geometry":{"w":..,"h":..,"tabs":[[x0,y0,x1,y1]x3],"sliders":[[field,top,bottom,lo_x,hi_x],..]}}
// (integers; lo_x/hi_x rounded).
std::string menu_geometry_json(const MenuGeometry& geometry);
// Mouse-mode pointer: a ring in the card's text colour (a filled disc while a slider is held), hard-edged, with its
// hot spot in the middle of the image. Nothing brighter than the card's text.
HudImage render_menu_pointer(bool held);
// Top-left (from the top of the output) of a card drawn centred, lowered by `drop_px` while it fades in: the one
// formula GlHud uses, so the pointer lands on the card exactly.
struct CardOrigin { float x = 0, y = 0; };
CardOrigin centered_card_origin(std::uint32_t output_w, std::uint32_t output_h, std::uint32_t card_w, std::uint32_t card_h, float drop_px);

class PlacementMenuOverlay {
public:
    // Apply the latest state. A new card is due when open and the state differs from the last one; it is rasterised
    // lazily, on the first image()/fits()/geometry() after the change, so several updates between two displayed
    // frames (a slider drag) cost one render. Returns true if anything to draw changed (visibility or card).
    bool set(const PlacementMenuState& state, double now_ms);
    void set_font(std::unique_ptr<HudFont> font);
    // Output surface size the card must fit (0 = unbounded); a shown card is laid out again when it changes.
    bool set_viewport(std::uint32_t width, std::uint32_t height);
    bool fits() const { ensure(); return fits_; }  // last card fits the viewport
    bool visible() const { return fade_.visible(); }
    std::uint64_t shown_count() const { return shown_; }
    std::uint64_t hidden_count() const { return hidden_; }
    std::uint64_t render_count() const { return renders_; }  // cards actually rasterised
    float alpha(double now_ms) const;
    float offset_px(double now_ms) const;
    bool animating(double now_ms) const;
    const HudImage& image() const { ensure(); return image_; }
    std::uint64_t generation() const { return generation_; }  // bumps when the card changes
    // Layout of the current card; geometry_generation() bumps only when it actually moves.
    const MenuGeometry& geometry() const { ensure(); return geometry_; }
    std::uint64_t geometry_generation() const { ensure(); return geometry_generation_; }
    // Mouse-mode pointer in card pixels (supervisor-owned). Moving it never touches the card. True if it changed.
    bool set_pointer(bool visible, std::int32_t x, std::int32_t y, bool held);
    bool pointer_visible() const { return pointer_on_; }
    std::int32_t pointer_x() const { return pointer_x_; }
    std::int32_t pointer_y() const { return pointer_y_; }
    const HudImage& pointer_image() const { return pointer_held_ ? pointer_held_image_ : pointer_image_; }
    std::uint64_t pointer_generation() const { return pointer_held_ ? 2 : 1; }  // which of the two images
private:
    CardFade fade_{kHelpFadeInMs, kHelpFadeOutMs};
    std::uint64_t shown_ = 0, hidden_ = 0, generation_ = 0;
    PlacementMenuState rendered_;
    std::uint32_t max_w_ = 0, max_h_ = 0;
    void ensure() const;
    mutable bool dirty_ = false, fits_ = true;
    mutable std::uint64_t renders_ = 0, geometry_generation_ = 0;
    mutable HudImage image_;
    mutable MenuGeometry geometry_;
    std::unique_ptr<HudFont> font_;
    bool pointer_on_ = false, pointer_held_ = false;
    std::int32_t pointer_x_ = 0, pointer_y_ = 0;
    HudImage pointer_image_ = render_menu_pointer(false), pointer_held_image_ = render_menu_pointer(true);
};
}
