#pragma once
// Key bind panel (Ctrl+Alt+K by default; tools/native_spike/keybinds.py): a head-locked Material Design 3 card listing
// every XR Workspace bind with its keys, display only (the supervisor owns the state, the keyboard and the binds).
//
// Look. An extra-large rounded surface container (high) with a 1 px outline-variant edge, like the help card, in the M3
// scheme of the Omarchy theme colour (material.hpp, glass_theme_scheme), every role under the OLED cap (kCardMaxLevel):
//   * a headline ("Key binds") with the picked row's place ("7 / 25") on the right;
//   * one M3 list item per bind: its action, and its keys in a chip at the right (outlined; filled primary-container when
//     the keys are not the default). The picked row is on a secondary-container band; a row with a conflict is on an
//     error-container band with its note (what it conflicts with) as a second line; the row being changed shows "Press
//     keys..." in a primary-outlined chip, like a focused text field;
//   * "N more" above / below when rows are scrolled out, a divider, the key hint and a status line (on-error-container when
//     it is an alert).
// Fit. The card takes the roomiest text step (kBindStyles) at which kBindPanelSlots rows (fewer if the panel has fewer)
// fit the output with kHudFitMargin free on every side; at the smallest it shows fewer rows, at least kBindPanelMinSlots.
// The rows scroll: the window keeps the picked row in view and moves only when it would leave it (bind_panel_first).
// Its height counts one note line for each conflict, up to one per row shown, so it changes only when conflicts do.
#include "xr/hud.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace xr {
enum class BindRowState : std::uint8_t { normal, custom, conflict, capture };
struct BindRow {
    std::string action, keys, note;  // note: what the keys conflict with (empty: nothing)
    BindRowState state = BindRowState::normal;
    bool operator==(const BindRow&) const = default;
};
// The `binds` control op's payload (ipc.hpp): {"visible":false}, or {"visible":true,"title":t,"rows":[{"action":a,
// "keys":k,"note":n,"state":"normal"|"custom"|"conflict"|"capture"},...],"selected":i,"hint":h,"status":s,"alert":b}.
// title, action and keys 1..kMaxBindLabel printable ASCII characters, hint 1..kMaxBindText, note and status
// 0..kMaxBindText; 1..kMaxBindRows rows; selected < rows. Malformed: rejected with kInvalidBindsCode and dropped.
constexpr std::size_t kMaxBindRows = 64, kMaxBindLabel = 48, kMaxBindText = 96;
struct BindPanelState {
    bool visible = false;
    std::string title, hint, status;
    std::vector<BindRow> rows;
    std::uint32_t selected = 0;
    bool alert = false;
    bool operator==(const BindPanelState&) const = default;
};

constexpr std::uint32_t kBindPanelSlots = 10, kBindPanelMinSlots = 3;  // rows shown at once
constexpr std::uint32_t kBindActionChars = 34, kBindKeysChars = 20;    // the columns' width, in characters (monospace)
constexpr std::uint32_t kBindRowPxMax = 26, kBindRowPxMin = 18;        // row text em size, roomiest / smallest
struct BindPanelFit {
    std::uint32_t width = 0, height = 0, row_px = 0, slots = 0;
    bool fits = false;  // false only if even the smallest text with kBindPanelMinSlots rows does not fit
};
BindPanelFit bind_panel_fit(const BindPanelState& state, const HudFont* font = nullptr, std::uint32_t max_width = 0,
                            std::uint32_t max_height = 0);
// The first row of the window of `slots` rows: `previous` moved only as far as keeps `selected` in it, within the rows.
std::uint32_t bind_panel_first(std::uint32_t previous, std::uint32_t selected, std::uint32_t rows, std::uint32_t slots);
// The card, rows from `first` (bind_panel_first). With no font, the built-in pixel font.
HudImage render_bind_panel(const BindPanelState& state, std::array<float, 3> accent, const HudFont* font = nullptr,
                           std::uint32_t max_width = 0, std::uint32_t max_height = 0, std::uint32_t first = 0);

// The panel as the renderer shows it: fades like the help card (CardFade, kHelpFadeInMs / kHelpFadeOutMs, rising
// kHelpRisePx), keeps its content while fading out, and scrolls with the picked row.
class BindPanelOverlay {
public:
    bool set(const BindPanelState& state, double now_ms);  // true if visibility or what it shows changed
    void set_font(std::unique_ptr<HudFont> font);
    bool set_accent(std::array<float, 3> accent);            // re-renders a rendered card (true)
    bool set_viewport(std::uint32_t width, std::uint32_t height);
    bool visible() const { return fade_.visible(); }
    float alpha(double now_ms) const;
    float offset_px(double now_ms) const;
    bool animating(double now_ms) const;
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }
    std::uint32_t first() const { return first_; }
    std::uint32_t slots() const { return slots_; }
    std::uint64_t shown_count() const { return shown_; }
    std::uint64_t hidden_count() const { return hidden_; }
private:
    void render();
    CardFade fade_{kHelpFadeInMs, kHelpFadeOutMs};
    BindPanelState state_;  // the content shown last (kept while fading out)
    std::uint32_t max_w_ = 0, max_h_ = 0, first_ = 0, slots_ = 0;
    std::uint64_t generation_ = 0, shown_ = 0, hidden_ = 0;
    HudImage image_;
    std::array<float, 3> accent_ = kMdDefaultSeed;
    std::unique_ptr<HudFont> font_;
};
}
