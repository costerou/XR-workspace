#include "xr/binds_panel.hpp"
#include "xr/glass_theme.hpp"
#include "xr/material_draw.hpp"
#include <algorithm>
#include <cmath>
#include <string_view>

namespace xr {
namespace {
// Text steps, roomiest first: headline, row, note (a conflicting row's second line), hint / status, and spacing.
struct BindStyle { std::uint32_t title_px, row_px, note_px, hint_px, row_pad, gap; };
constexpr BindStyle kBindStyles[] = {{30, 26, 19, 18, 7, 14}, {28, 24, 18, 17, 6, 13}, {26, 22, 17, 16, 6, 12},
                                     {24, 20, 16, 15, 5, 11}, {22, 18, 15, 14, 4, 10}};
static_assert(kBindStyles[0].row_px == kBindRowPxMax && kBindStyles[std::size(kBindStyles) - 1].row_px == kBindRowPxMin);
// Card chrome (output px; the card is drawn 1:1): padding, a row's inner inset, a chip's side padding and its vertical
// padding, the space between rows; the help card's background opacity and divider.
constexpr float kPad = 32, kInset = 16, kChipPad = 12, kRowSpacing = 4;
constexpr std::uint32_t kChipPadV = 4;
constexpr float kBackgroundAlpha = 0.9f, kDividerAlpha = 0.8f;
constexpr std::uint32_t kPixelZoomBase = 2, kEmPerZoom = 19;  // the built-in pixel font's size, as hud.cpp draws it

struct Text { std::uint32_t width = 0, ascent = 0, descent = 0; std::uint32_t height() const { return ascent + descent; } };
std::uint32_t zoom(std::uint32_t px) { return std::max<std::uint32_t>(1, (px * kPixelZoomBase + kEmPerZoom / 2) / kEmPerZoom); }
Text measure(const HudFont* font, std::string_view s, std::uint32_t px, bool bold) {
    if (!font) { const auto z = zoom(px); return {hud_text_width(s, z), kGlyphH * z, 0}; }
    const auto m = font->metrics(px, bold);
    return {font->measure(s, px, bold), m.ascent, m.descent};
}
Text line(const HudFont* font, std::uint32_t px, bool bold) { auto t = measure(font, "", px, bold); t.width = 0; return t; }
// `s` with its top-left at (left, top), in `c` (a scheme role: under the OLED cap).
void draw_text(HudImage& im, const HudFont* font, std::string_view s, std::uint32_t px, bool bold, int left, int top, MdRgb c) {
    if (font) {
        const auto m = font->metrics(px, bold);
        font->draw(s, px, bold, left, top + int(m.ascent), [&](int x, int y, float coverage) { md_over(im, x, y, c, coverage); });
        return;
    }
    const auto z = int(zoom(px));
    int x = left;
    for (char ch : s) {
        const auto& g = hud_glyph(ch);
        for (int row = 0; row < int(kGlyphH); ++row)
            for (int col = 0; col < int(kGlyphW); ++col)
                if (g[std::size_t(row)] & (0x10u >> col))
                    for (int sy = 0; sy < z; ++sy)
                        for (int sx = 0; sx < z; ++sx) md_over(im, x + col * z + sx, top + row * z + sy, c, 1.f);
        x += int(kGlyphW + 1) * z;
    }
}
// `s`, or as much of it as fits `max_w` with "..." after it.
std::string fit(const HudFont* font, const std::string& s, std::uint32_t px, bool bold, std::uint32_t max_w) {
    if (measure(font, s, px, bold).width <= max_w) return s;
    std::string t = s;
    while (!t.empty() && measure(font, t + "...", px, bold).width > max_w) t.pop_back();
    while (!t.empty() && t.back() == ' ') t.pop_back();
    return t + "...";
}
bool has_note(const BindRow& r) { return r.state == BindRowState::conflict && !r.note.empty(); }

struct Layout {
    BindStyle st{};
    std::uint32_t slots = 0, notes = 0;      // rows shown; note lines the height counts
    std::uint32_t width = 0, height = 0;
    Text title, row, note, hint;             // line boxes
    std::uint32_t action_w = 0, keys_w = 0;  // the columns (keys: the widest chip)
    std::uint32_t chip_h = 0, row_h = 0, note_h = 0, list_h = 0;
};
Layout layout(const BindPanelState& s, const HudFont* f, const BindStyle& st, std::uint32_t slots) {
    Layout L;
    L.st = st; L.slots = slots;
    L.title = line(f, st.title_px, true); L.row = line(f, st.row_px, false); L.note = line(f, st.note_px, false);
    L.hint = line(f, st.hint_px, false);
    L.action_w = measure(f, std::string(kBindActionChars, 'M'), st.row_px, false).width;
    L.keys_w = measure(f, std::string(kBindKeysChars, 'M'), st.row_px, true).width + 2 * std::uint32_t(kChipPad);
    L.chip_h = std::max(L.row.height(), line(f, st.row_px, true).height()) + 2 * kChipPadV;
    L.row_h = L.chip_h + 2 * st.row_pad;
    L.note_h = L.note.height() + st.row_pad;
    const auto conflicts = std::uint32_t(std::count_if(s.rows.begin(), s.rows.end(), has_note));
    L.notes = std::min(slots, conflicts);
    L.list_h = slots * L.row_h + (slots ? slots - 1 : 0) * std::uint32_t(kRowSpacing) + L.notes * L.note_h;
    L.width = 2 * std::uint32_t(kPad + kInset) + L.action_w + st.gap + L.keys_w;
    L.height = 2 * std::uint32_t(kPad) + L.title.height() + 2 * st.gap  // headline, divider
             + 2 * (L.hint.height() + st.gap / 2) + L.list_h          // "N more" above and below the rows
             + 2 * st.gap + L.hint.height() + st.gap / 2 + L.hint.height();  // divider, hint, status
    return L;
}
bool fits(const Layout& L, std::uint32_t max_w, std::uint32_t max_h) {
    return (!max_w || L.width + 2 * kHudFitMargin <= max_w) && (!max_h || L.height + 2 * kHudFitMargin <= max_h);
}
Layout choose(const BindPanelState& s, const HudFont* f, std::uint32_t max_w, std::uint32_t max_h) {
    const auto want = std::clamp<std::uint32_t>(std::uint32_t(s.rows.size()), 1, kBindPanelSlots);
    for (const auto& st : kBindStyles) {
        auto L = layout(s, f, st, want);
        if (fits(L, max_w, max_h)) return L;
    }
    const auto& smallest = kBindStyles[std::size(kBindStyles) - 1];
    for (std::uint32_t n = want; n > kBindPanelMinSlots;) {
        auto L = layout(s, f, smallest, --n);
        if (fits(L, max_w, max_h)) return L;
    }
    return layout(s, f, smallest, std::min(want, kBindPanelMinSlots));
}
// The card itself: the help card's surface (hud.cpp allocate_card), with the inside filled directly (only the corners and
// the edge are evaluated against the rounded shape).
void card(HudImage& im, const MaterialScheme& m) {
    const float w = float(im.width), h = float(im.height), r = kMdShapeExtraLarge;
    std::array<std::uint8_t, 4> fill{};
    for (std::size_t i = 0; i < 3; ++i) fill[i] = std::uint8_t(std::lround(m.surface_container_high[i] * kBackgroundAlpha * 255));
    fill[3] = std::uint8_t(std::lround(kBackgroundAlpha * 255));
    for (std::uint32_t y = 0; y < im.height; ++y)
        for (std::uint32_t x = 0; x < im.width; ++x) {
            const float cx = float(x) + 0.5f, cy = float(y) + 0.5f;
            const bool band = (cx >= r && cx <= w - r) || (cy >= r && cy <= h - r);
            if (band && std::min({cx, cy, w - cx, h - cy}) >= 1.5f) {
                std::copy(fill.begin(), fill.end(), &im.rgba[(std::size_t(y) * im.width + x) * 4]);
                continue;
            }
            const float d = md_rounded_distance(cx, cy, w / 2, h / 2, w / 2, h / 2, r);
            const float outer = std::clamp(0.5f - d, 0.f, 1.f), inner = std::clamp(0.5f - (d + 1.f), 0.f, 1.f);
            if (outer <= 0) continue;
            md_over(im, int(x), int(y), m.surface_container_high, kBackgroundAlpha * inner);
            md_over(im, int(x), int(y), m.outline_variant, outer * (1 - inner));  // the 1 px edge
        }
}
void divider(HudImage& im, float y, const MaterialScheme& m) {
    for (std::uint32_t x = std::uint32_t(kPad); x + std::uint32_t(kPad) < im.width; ++x) md_over(im, int(x), int(y), m.outline_variant, kDividerAlpha);
}
// "N more" with a drop arrow pointing the way the hidden rows are.
void more(HudImage& im, const HudFont* f, const Layout& L, std::uint32_t n, bool up, float top, const MaterialScheme& m) {
    if (!n) return;
    const float size = float(L.hint.height());
    const float left = kPad + kInset;
    md_icon(im, up ? MdIcon::arrow_drop_up : MdIcon::arrow_drop_down, left + size / 2, top + size / 2, size, m.on_surface_variant);
    draw_text(im, f, std::to_string(n) + " more", L.st.hint_px, false, int(left + size + 4), int(top), m.on_surface_variant);
}
}

std::uint32_t bind_panel_first(std::uint32_t previous, std::uint32_t selected, std::uint32_t rows, std::uint32_t slots) {
    if (!slots || rows <= slots) return 0;
    std::uint32_t first = std::min(previous, rows - slots);
    if (selected < first) first = selected;
    else if (selected >= first + slots) first = selected - slots + 1;
    return std::min(first, rows - slots);
}

BindPanelFit bind_panel_fit(const BindPanelState& state, const HudFont* font, std::uint32_t max_width, std::uint32_t max_height) {
    const auto L = choose(state, font, max_width, max_height);
    return {L.width, L.height, L.st.row_px, L.slots, fits(L, max_width, max_height)};
}

HudImage render_bind_panel(const BindPanelState& s, std::array<float, 3> accent, const HudFont* font, std::uint32_t max_width,
                           std::uint32_t max_height, std::uint32_t first) {
    const auto& m = glass_theme_scheme(accent);
    const auto L = choose(s, font, max_width, max_height);
    const auto& st = L.st;
    HudImage im;
    im.width = L.width; im.height = L.height;
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    card(im, m);
    const auto rows = std::uint32_t(s.rows.size());
    first = bind_panel_first(first, s.selected, rows, L.slots);
    const float W = float(im.width), right = W - kPad;
    // Headline, and the picked row's place on the right.
    float y = kPad;
    const std::string where = rows ? std::to_string(std::min(s.selected, rows - 1) + 1) + " / " + std::to_string(rows) : "";
    const auto where_box = measure(font, where, st.hint_px, false);
    draw_text(im, font, fit(font, s.title, st.title_px, true, L.width - 2 * std::uint32_t(kPad) - where_box.width - st.gap),
              st.title_px, true, int(kPad), int(y), m.on_surface);
    draw_text(im, font, where, st.hint_px, false, int(right - float(where_box.width)),
              int(y + float(L.title.height() - L.hint.height()) / 2), m.on_surface_variant);
    y += float(L.title.height() + st.gap);
    divider(im, y, m);
    y += float(st.gap);
    more(im, font, L, first, true, y, m);
    y += float(L.hint.height() + st.gap / 2);
    const float list_top = y;
    const float text_left = kPad + kInset, row_inner = W - 2 * (kPad + kInset);
    for (std::uint32_t i = first; i < std::min(rows, first + L.slots); ++i) {
        const auto& r = s.rows[i];
        const bool picked = i == s.selected, conflict = r.state == BindRowState::conflict, note = has_note(r);
        const float h = float(L.row_h + (note ? L.note_h : 0));
        // M3 list item: the picked one on a secondary-container band, a conflict on an error-container band (picked: with
        // a primary focus ring).
        if (conflict) md_fill_rounded(im, kPad, y, right, y + h, kMdShapeMedium, m.error_container, 1.f);
        else if (picked) md_fill_rounded(im, kPad, y, right, y + h, kMdShapeMedium, m.secondary_container, 1.f);
        if (conflict && picked) md_stroke_rounded(im, kPad, y, right, y + h, kMdShapeMedium, 2.f, m.primary, 1.f);
        const MdRgb text = conflict ? m.on_error_container : picked ? m.on_secondary_container : m.on_surface;
        // The keys chip, right-aligned: outlined, filled primary-container when not the default, primary-outlined (2 px)
        // while it waits for new keys.
        const std::string keys = fit(font, r.keys, st.row_px, true, L.keys_w - 2 * std::uint32_t(kChipPad));
        const auto keys_box = measure(font, keys, st.row_px, true);
        const float chip_x1 = right - kInset, chip_x0 = chip_x1 - float(keys_box.width) - 2 * kChipPad;
        const float chip_y0 = y + float(st.row_pad), chip_y1 = chip_y0 + float(L.chip_h);
        MdRgb chip_text = picked ? m.on_secondary_container : m.on_surface_variant;
        switch (r.state) {
        case BindRowState::custom:
            md_fill_rounded(im, chip_x0, chip_y0, chip_x1, chip_y1, kMdShapeSmall, m.primary_container, 1.f);
            chip_text = m.on_primary_container;
            break;
        case BindRowState::capture:
            md_stroke_rounded(im, chip_x0, chip_y0, chip_x1, chip_y1, kMdShapeSmall, 2.f, m.primary, 1.f);
            chip_text = m.primary;
            break;
        case BindRowState::conflict:
            md_stroke_rounded(im, chip_x0, chip_y0, chip_x1, chip_y1, kMdShapeSmall, 1.f, m.on_error_container, 1.f);
            chip_text = m.on_error_container;
            break;
        case BindRowState::normal:
            md_stroke_rounded(im, chip_x0, chip_y0, chip_x1, chip_y1, kMdShapeSmall, 1.f, m.outline, 1.f);
            break;
        }
        draw_text(im, font, keys, st.row_px, true, int(chip_x0 + kChipPad), int(chip_y0) + int(kChipPadV), chip_text);
        const auto action_w = std::uint32_t(std::max(0.f, chip_x0 - text_left - float(st.gap)));
        draw_text(im, font, fit(font, r.action, st.row_px, false, action_w), st.row_px, false, int(text_left),
                  int(chip_y0) + int(kChipPadV), text);
        if (note)  // what it conflicts with, as the item's supporting text
            draw_text(im, font, fit(font, r.note, st.note_px, false, std::uint32_t(row_inner)), st.note_px, false, int(text_left),
                      int(y + float(L.row_h) - float(st.row_pad) / 2), m.on_error_container);
        y += h + kRowSpacing;
    }
    y = list_top + float(L.list_h + st.gap / 2);
    more(im, font, L, rows > first + L.slots ? rows - first - L.slots : 0, false, y, m);
    // Footer at the bottom: divider, the key hint, the status line.
    y = float(im.height) - kPad - float(2 * L.hint.height() + st.gap / 2);
    divider(im, y - float(st.gap), m);
    draw_text(im, font, fit(font, s.hint, st.hint_px, false, std::uint32_t(W - 2 * kPad)), st.hint_px, false, int(kPad), int(y), m.outline);
    y += float(L.hint.height() + st.gap / 2);
    draw_text(im, font, fit(font, s.status, st.hint_px, false, std::uint32_t(W - 2 * kPad)), st.hint_px, false, int(kPad), int(y),
              s.alert ? m.on_error_container : m.on_surface_variant);
    return im;
}

bool BindPanelOverlay::set(const BindPanelState& state, double now_ms) {
    bool redraw = false;
    if (state.visible && (!generation_ || !(state == state_))) {  // hidden: the last content stays while it fades out
        state_ = state;
        render();
        redraw = true;
    }
    if (!fade_.set(state.visible, now_ms)) return redraw;
    if (state.visible) ++shown_; else ++hidden_;
    return true;
}
bool BindPanelOverlay::set_viewport(std::uint32_t width, std::uint32_t height) {
    if (width == max_w_ && height == max_h_) return false;
    max_w_ = width; max_h_ = height;
    if (!generation_) return false;
    render();
    return true;
}
void BindPanelOverlay::render() {
    slots_ = bind_panel_fit(state_, font_.get(), max_w_, max_h_).slots;
    first_ = bind_panel_first(first_, state_.selected, std::uint32_t(state_.rows.size()), slots_);
    image_ = render_bind_panel(state_, accent_, font_.get(), max_w_, max_h_, first_);
    ++generation_;
}
void BindPanelOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
bool BindPanelOverlay::set_accent(std::array<float, 3> accent) {
    if (accent == accent_) return false;
    accent_ = accent;
    if (!generation_) return false;
    render();
    return true;
}
float BindPanelOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
float BindPanelOverlay::offset_px(double now_ms) const { return (1 - alpha(now_ms)) * kHelpRisePx; }
bool BindPanelOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }
}
