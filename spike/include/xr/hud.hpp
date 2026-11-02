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
// Typing mode (Ctrl+Alt+T): drawn in the cards' dimmed colours (kCardMaxLevel), never full white.
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
    void set_font(std::unique_ptr<HudFont> font);  // applies to the next card; nullptr: pixel font
    const HudFont* font() const { return font_.get(); }
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
    std::unique_ptr<HudFont> font_;
};

// Keyboard help (Ctrl+Alt+H): a centred card listing every bind, toggled by the supervisor.
// Fades in/out with an eased curve and a small rise; toggling mid-fade reverses from the
// current opacity instead of jumping.
struct HelpRow { std::string keys, action; };
HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font = nullptr);
constexpr double kHelpFadeInMs = 220, kHelpFadeOutMs = 260;
// Image-retention protection for the centred help card and the placement panels (glass.hpp) on the glasses' micro-OLED panels:
// dimmed text and accent (see hud.cpp kCardText) and an idle auto-close. The cards never move while shown.
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
// formula GlHud uses for the help card.
struct CardOrigin { float x = 0, y = 0; };
CardOrigin centered_card_origin(std::uint32_t output_w, std::uint32_t output_h, std::uint32_t card_w, std::uint32_t card_h, float drop_px);

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

// Placement state (supervisor-owned, ipc.hpp PlacementMenuPayload): which desktop's glass pill is expanded into its
// placement panel (`open`, `desktop`), the selected field, and every desktop's placement. The panel itself is drawn by
// glass.hpp; these are the field table and formatting it shares with the tests.
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
// The flat (default) value of each field: sliders fill from it to the current value.
MenuPlacement placement_menu_flat();
struct PlacementMenuRow { std::string label, hint, value; bool selected = false; };
// Label, plain-English hint and formatted value ("+12%", "100%", "-5°"; " deg" with the pixel font,
// which is ASCII-only) for each field of the selected desktop.
std::vector<PlacementMenuRow> placement_menu_rows(const PlacementMenuState& state, bool ascii_units);
}
