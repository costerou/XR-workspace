#include "xr/hud.hpp"
#include <algorithm>
#include <cmath>
#include <tuple>
#include <iostream>
#include <set>
#include <string>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;

namespace {
bool lit(const HudImage& im, std::uint32_t x, std::uint32_t y) { return im.rgba[(std::size_t(y) * im.width + x) * 4 + 3] != 0; }
bool premultiplied(const HudImage& im) {
    for (std::size_t i = 0; i < im.rgba.size(); i += 4)
        for (int c = 0; c < 3; ++c) if (im.rgba[i + std::size_t(c)] > im.rgba[i + 3]) return false;
    return true;
}
std::string joined(const std::vector<HudLine>& lines) { std::string s; for (auto& l : lines) s += l.text + "|"; return s; }
}

int main() {
    // Glyphs: case-insensitive; unknown characters render '?' instead of nothing.
    CHECK(&hud_glyph('a') == &hud_glyph('A') && &hud_glyph('~') == &hud_glyph('?'));
    CHECK(hud_glyph(' ') == (std::array<std::uint8_t, kGlyphH>{}));
    for (char c = 'A'; c <= 'Z'; ++c) { bool any = false; for (auto row : hud_glyph(c)) { CHECK(row < 0x20); any |= row != 0; } CHECK(any); }
    CHECK(hud_text_width("", 3) == 0 && hud_text_width("A", 1) == 5 && hud_text_width("AB", 2) == 22);

    // Pixel-font card (1:1 output pixels, font texels doubled): sized to the widest line plus
    // padding, rounded (transparent corners), opaque accent edge, premultiplied, text present.
    auto card = render_hud_card({{"VIEW MODE", 2, {1, 1, 1}}, {"FOLLOW", 4, {1, 1, 1}}}, {0.5f, 0.6f, 0.9f});
    CHECK(card.width == hud_text_width("FOLLOW", 8) + 44 && card.height == 7 * 4 + 8 + 7 * 8 + 44);
    CHECK(card.rgba.size() == std::size_t(card.width) * card.height * 4);
    CHECK(!lit(card, 0, 0) && !lit(card, card.width - 1, card.height - 1));
    CHECK(card.rgba[(std::size_t(card.height / 2) * card.width) * 4 + 3] == 255);  // left edge, mid height
    CHECK(premultiplied(card));
    auto text_row = [](const HudImage& im, std::uint32_t y) {
        for (std::uint32_t x = 22; x + 22 < im.width; ++x) if (im.rgba[(std::size_t(y) * im.width + x) * 4] > 200) return true;
        return false;
    };
    CHECK(text_row(card, 22 + 28 + 8 + 3 * 8));
    CHECK(render_hud_card({}, {1, 1, 1}).width == 44);

    // System font (JetBrainsMono Nerd Font) when installed: anti-aliased (partial coverage), bold
    // value wider than regular, wider than tall, premultiplied. Absent font: reported, not fatal.
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    if (font) {
        CHECK(!font->description().empty());
        auto fc = render_hud_card(view_mode_lines(2), {0.5f, 0.6f, 0.9f}, font.get());
        CHECK(fc.width > fc.height && fc.height > 100 && premultiplied(fc));
        bool partial = false;
        for (std::size_t i = 0; i < fc.rgba.size(); i += 4) partial |= fc.rgba[i] > 30 && fc.rgba[i] < 200 && fc.rgba[i + 3] > 200;
        CHECK(partial);
        CHECK(font->measure("Follow", 76, true) >= font->measure("Follow", 76, false) && font->measure("", 76, false) == 0);
        CHECK(font->measure("\xff\xfe", 38, false) > 0);  // invalid UTF-8 -> replacement glyphs, no crash
        std::cout << "hud font: " << font->description() << '\n';
    } else {
        CHECK(!why.empty());
        std::cout << "hud font unavailable (pixel fallback): " << why << '\n';
    }
    std::string missing;
    CHECK(!HudFont::load("No Such Font Family XYZ", missing) && missing.find("not installed") != std::string::npos);

    // Fade: in, hold at exactly 1, out, then gone.
    CHECK(popup_alpha(-1) == 0 && popup_alpha(0) == 0 && popup_alpha(kPopupFadeInMs / 2) > 0.4f && popup_alpha(kPopupFadeInMs / 2) < 0.6f);
    CHECK(popup_alpha(kPopupFadeInMs) == 1 && popup_alpha(kPopupFadeInMs + kPopupHoldMs - 1) == 1);
    for (double t = kPopupFadeInMs + kPopupHoldMs; t + 10 < kPopupTotalMs; t += 10) CHECK(popup_alpha(t + 10) <= popup_alpha(t));
    CHECK(popup_alpha(kPopupTotalMs) == 0 && popup_alpha(1e9) == 0);

    // Text for each mode and cursor state.
    CHECK(joined(view_mode_lines(0)).find("|Anchor|") != std::string::npos);
    CHECK(joined(view_mode_lines(1)).find("|Locked|") != std::string::npos);
    CHECK(joined(view_mode_lines(2)).find("|Follow|") != std::string::npos && view_mode_lines(2)[1].bold);
    CHECK(joined(cursor_follow_lines(true)).find("Cursor follow|On|") == 0);
    CHECK(joined(cursor_follow_lines(false)).find("Cursor follow|Off|") == 0);
    CHECK(joined(prediction_lines(true)).find("Prediction|On|") == 0);
    CHECK(joined(prediction_lines(false)).find("Prediction|Off|") == 0);

    // Pop-up triggers: first update shows the viewing mode; repeats (zoom/select/spacing frames)
    // do not re-show it; a mode change or cursor toggle shows a new card.
    StatusPopups p;
    CHECK(!p.active(0) && p.alpha(0) == 0 && p.generation() == 0);
    CHECK(p.update(0, false, 1000) && p.generation() == 1 && p.active(1000));
    CHECK(p.alpha(1000 + kPopupFadeInMs) == 1);
    const auto mode_card = p.image().width;
    CHECK(!p.update(0, false, 1200) && p.generation() == 1);
    CHECK(!p.active(1000 + kPopupTotalMs) && p.alpha(1000 + kPopupTotalMs) == 0);
    CHECK(p.update(0, true, 5000) && p.generation() == 2 && p.image().width != mode_card);  // cursor card
    CHECK(p.alpha(5000 + kPopupFadeInMs) == 1);
    CHECK(p.update(2, true, 5100) && p.generation() == 3 && p.alpha(5100 + kPopupFadeInMs) == 1);  // restarts the fade
    const auto single = p.image().height;
    CHECK(p.update(1, false, 9000) && p.image().height > single);  // both changed: both sections
    CHECK(!p.update(1, false, 9001));
    // Prediction toggles show their own card; unchanged prediction does not re-show anything.
    CHECK(p.update(1, false, true, 12000) && p.generation() == 5);
    CHECK(!p.update(1, false, true, 12001));
    CHECK(p.update(1, false, false, 13000) && p.generation() == 6);
    StatusPopups quiet; CHECK(quiet.update(0, false, true, 0) && quiet.generation() == 1);  // first: mode card only
    // Typing mode (Ctrl+Alt+T): its own short card on every switch, nothing for unchanged frames; the 3- and 4-argument
    // updates keep the current typing state. It auto-hides like every pop-up (kPopupTotalMs).
    CHECK(joined(typing_lines(true)).find("Keys|Typing|") == 0 && typing_lines(true)[1].bold);
    CHECK(joined(typing_lines(false)).find("Keys|XR control|") == 0);
    CHECK(joined(typing_lines(true)).find("Ctrl+Alt+T") != std::string::npos);
    {
        StatusPopups t;
        CHECK(t.update(0, false, true, false, 0.0) && t.generation() == 1);
        CHECK(t.update(0, false, true, true, 3000.0) && t.generation() == 2 && t.active(3000.0));
        CHECK(!t.active(3000.0 + kPopupTotalMs) && t.alpha(3000.0 + kPopupTotalMs) == 0);  // gone by itself
        CHECK(!t.update(0, false, true, true, 4000.0) && !t.update(0, false, true, 4001.0) && !t.update(0, false, 4002.0));
        CHECK(t.update(0, false, true, false, 6000.0) && t.generation() == 3);
        StatusPopups started_typing; CHECK(started_typing.update(0, false, true, true, 0.0));
        const auto both = started_typing.image().height;
        CHECK(both > t.image().height);  // first update in typing mode: mode card plus the typing section
    }
    StatusPopups bad; CHECK(bad.update(9, false, 0));  // out-of-range mode is treated as anchor

    // Help card: brackets have real pixel glyphs; card grows with rows, is premultiplied and
    // rounded; with the system font it also renders.
    CHECK(&hud_glyph('[') != &hud_glyph('?') && &hud_glyph(']') != &hud_glyph('?'));
    CHECK(&hud_glyph('%') != &hud_glyph('?'));  // placement menu units
    const std::vector<HelpRow> rows{{"Ctrl+Alt+Up", "Zoom in"}, {"Ctrl+Alt+[", "Less space"}, {"Ctrl+Alt+H", "Show / hide this help"}};
    auto hc = render_help_card(rows, {0.5f, 0.6f, 0.9f});
    auto hc1 = render_help_card({rows[0]}, {0.5f, 0.6f, 0.9f});
    CHECK(hc.height > hc1.height && hc.width >= hc1.width && premultiplied(hc) && !lit(hc, 0, 0));
    CHECK(render_help_card({}, {1, 1, 1}).width > 0);
    if (font) { auto fh = render_help_card(rows, {0.5f, 0.6f, 0.9f}, font.get()); CHECK(fh.width > fh.height && premultiplied(fh)); }

    // Help fade: hidden until shown; eases in, holds while visible, eases out; monotonic both ways;
    // toggling mid-fade reverses from the current opacity (no jump); rows re-render only on change.
    HelpOverlay h;
    CHECK(h.alpha(0) == 0 && !h.animating(0) && h.generation() == 0 && !h.set(false, rows, 0));
    CHECK(h.set(true, rows, 1000) && h.visible() && h.generation() == 1 && h.animating(1000) && h.alpha(1000) == 0);
    CHECK(h.shown_count() == 1 && h.hidden_count() == 0);
    CHECK(h.offset_px(1000) == kHelpRisePx);
    for (double t = 1000; t < 1000 + kHelpFadeInMs; t += 10) CHECK(h.alpha(t + 10) >= h.alpha(t));
    CHECK(h.alpha(1000 + kHelpFadeInMs) == 1 && h.offset_px(1000 + kHelpFadeInMs) == 0 && !h.animating(1000 + kHelpFadeInMs));
    CHECK(h.alpha(1e9) == 1);  // stays up while visible
    CHECK(!h.set(true, rows, 2000) && h.generation() == 1);  // same rows: no re-render
    CHECK(h.set(false, rows, 3000) && h.animating(3000) && h.alpha(3000) == 1);
    for (double t = 3000; t < 3000 + kHelpFadeOutMs; t += 10) CHECK(h.alpha(t + 10) <= h.alpha(t));
    CHECK(h.alpha(3000 + kHelpFadeOutMs) == 0 && !h.animating(3000 + kHelpFadeOutMs));
    CHECK(h.shown_count() == 1 && h.hidden_count() == 1);
    CHECK(h.set(true, rows, 5000));
    const float mid = h.alpha(5000 + kHelpFadeInMs / 2);
    CHECK(mid > 0.2f && mid < 0.8f);
    CHECK(h.set(false, rows, 5000 + kHelpFadeInMs / 2) && h.alpha(5000 + kHelpFadeInMs / 2) == mid);  // reverses, no jump
    CHECK(h.alpha(5000 + kHelpFadeInMs / 2 + kHelpFadeOutMs) == 0);
    CHECK(h.shown_count() == 2 && h.hidden_count() == 2 && !h.visible());
    CHECK(h.set(true, {rows[0]}, 7000) && h.generation() == 2);  // new rows: new card

    // Placement menu rows: friendly labels and hints, signed values with units, the selected field
    // highlighted, values from the selected desktop; ASCII units for the pixel font.
    PlacementMenuState ms;
    ms.open = true; ms.desktop = 1; ms.field = 3;
    ms.placements[0] = {0, 0, 100, 0, 0, 0, 0};
    ms.placements[1] = {12, -7, 150, -30, 45, 0, 90};
    ms.placements[2] = {-100, 100, 200, 60, -45, -45, 120};
    auto mr = placement_menu_rows(ms, false);
    CHECK(mr.size() == kPlacementMenuFields);
    CHECK(mr[0].label == "Left / right" && mr[1].label == "Down / up" && mr[2].label == "Distance" && mr[6].label == "Curvature");
    CHECK(mr[3].label == "Yaw" && mr[3].hint == "turn left or right" && mr[4].label == "Pitch" && mr[4].hint == "tilt up or down" &&
          mr[5].label == "Roll" && mr[5].hint == "tilt sideways");
    CHECK(mr[0].value == "+12%" && mr[1].value == "-7%" && mr[2].value == "150%" && mr[3].value == "-30\xc2\xb0" &&
          mr[4].value == "+45\xc2\xb0" && mr[5].value == "0\xc2\xb0" && mr[6].value == "90\xc2\xb0");
    for (std::size_t i = 0; i < mr.size(); ++i) CHECK(mr[i].selected == (i == 3));
    auto ascii = placement_menu_rows(ms, true);
    CHECK(ascii[3].value == "-30 deg" && ascii[0].value == "+12%");
    for (const auto& r : ascii) for (unsigned char c : r.label + r.hint + r.value) CHECK(c >= 0x20 && c < 0x7f);
    ms.desktop = 2; CHECK(placement_menu_rows(ms, true)[0].value == "-100%" && placement_menu_rows(ms, true)[6].value == "120 deg");
    ms.desktop = 9; CHECK(placement_menu_rows(ms, true)[0].value == "-100%");  // out of range: clamped, no crash
    ms.field = 7; for (const auto& r : placement_menu_rows(ms, true)) CHECK(!r.selected);
    ms.desktop = 1; ms.field = 3;
    // Footer names the adjust keys; ASCII so the pixel fallback can draw it.
    std::string hint_text; for (const auto& h : placement_menu_hints()) hint_text += h + "\n";
    CHECK(hint_text.find("Left / Right") != std::string::npos && hint_text.find("Shift") != std::string::npos &&
          hint_text.find("R ") != std::string::npos);
    for (unsigned char c : hint_text) CHECK(c == '\n' || (c >= 0x20 && c < 0x7f));

    // Menu card: help-card chrome (rounded, premultiplied, opaque accent edge); the selected row's band
    // tints the left marker column in the accent colour; the card changes with the selected field.
    auto mc = render_placement_menu_card(ms, {0.5f, 0.6f, 0.9f});
    CHECK(mc.width > 0 && mc.height > hc.height && premultiplied(mc) && !lit(mc, 0, 0) && !lit(mc, mc.width - 1, mc.height - 1));
    CHECK(mc.rgba[(std::size_t(mc.height / 2) * mc.width) * 4 + 3] == 255);
    auto marker_rows = [](const HudImage& im) {  // rows whose leftmost table pixels are the solid accent marker
        std::vector<std::uint32_t> ys;
        for (std::uint32_t y = 4; y + 4 < im.height; ++y)  // inside the accent border
            for (std::uint32_t x = 34; x < im.width / 2; ++x) {
                const auto* px = &im.rgba[(std::size_t(y) * im.width + x) * 4];
                // The 2 px marker is followed by the translucent wash (an underline or scroll arrow is solid or ends).
                if (x + 6 >= im.width) break;
                const auto* wash = px + 4 * 3, * further = px + 4 * 6;
                if (px[2] > 200 && px[0] < 150 && wash[2] < 200 && wash[2] > 40 && further[2] > 40 && further[2] < 200) { ys.push_back(y); break; }
            }
        return ys;
    };
    auto field3 = marker_rows(mc);
    PlacementMenuState other = ms; other.field = 0;
    auto mc0 = render_placement_menu_card(other, {0.5f, 0.6f, 0.9f});
    auto field0 = marker_rows(mc0);
    CHECK(!field3.empty() && !field0.empty() && field0.back() < field3.back());  // highlight moves up with the field
    CHECK(mc0.width == mc.width && mc0.height == mc.height);  // same layout, only the highlight moves
    if (font) { auto fm = render_placement_menu_card(ms, {0.5f, 0.6f, 0.9f}, font.get()); CHECK(fm.height > 200 && premultiplied(fm)); }

    // Uniform with the help card: identical corner chrome (radius, border, background alpha, accent),
    // and the same row pitch (rows are drawn with the help card's sizes and gaps).
    auto same_corner = [](const HudImage& a, const HudImage& b) {
        for (std::uint32_t y = 0; y < 30; ++y)
            for (std::uint32_t x = 0; x < 30; ++x)
                for (int c = 0; c < 4; ++c)
                    if (a.rgba[(std::size_t(y) * a.width + x) * 4 + std::size_t(c)] != b.rgba[(std::size_t(y) * b.width + x) * 4 + std::size_t(c)]) return false;
        return true;
    };
    CHECK(same_corner(render_placement_menu_card(ms, {0.5f, 0.6f, 0.9f}), render_help_card(rows, {0.5f, 0.6f, 0.9f})));
    if (font) CHECK(same_corner(render_placement_menu_card(ms, {0.5f, 0.6f, 0.9f}, font.get()), render_help_card(rows, {0.5f, 0.6f, 0.9f}, font.get())));

    // Fit: the widest values on every desktop; the card always matches the reported size, keeps the
    // fit margin inside the output where it fits, and never changes text size (same highlight band
    // height in every arrangement). 1920x1200 @1.25 (1536x960) uses the roomiest arrangement.
    PlacementMenuState wide = ms;
    for (auto& pl : wide.placements) pl = {-100, -100, 200, -60, -45, -45, 120};
    auto band_height = [&](const HudImage& im) { return marker_rows(im).size(); };
    struct Output { std::uint32_t w, h; bool must_fit; };
    std::vector<const HudFont*> faces{nullptr};  // pixel fallback, and the system font when installed
    if (font) faces.push_back(font.get());
    for (const HudFont* f : faces) {
        const auto roomy = placement_menu_fit(wide, f, 0, 0);
        CHECK(roomy.fits && !roomy.tabs_beside_title && !roomy.wide_footer && !roomy.short_hints && roomy.footer_lines >= 1);
        const auto band = band_height(render_placement_menu_card(wide, {0.5f, 0.6f, 0.9f}, f));
        CHECK(band > 20);  // one text row plus the row gap
        const auto big = placement_menu_fit(wide, f, 1536, 960);
        CHECK(big.fits && big.width == roomy.width && big.height == roomy.height && !big.short_hints && !big.tabs_beside_title);
        for (Output o : {Output{1920, 1200, true}, Output{1536, 960, true}, Output{1536, 864, true}, Output{1280, 800, true},
                         Output{1280, 720, true}, Output{1200, 750, true}, Output{1200, 675, true}, Output{960, 600, true},
                         Output{960, 540, true}, Output{640, 400, false}}) {
            const auto fit = placement_menu_fit(wide, f, o.w, o.h);
            const auto im = render_placement_menu_card(wide, {0.5f, 0.6f, 0.9f}, f, o.w, o.h);
            CHECK(im.width == fit.width && im.height == fit.height && premultiplied(im));
            CHECK(band_height(im) == band);  // text and row spacing unchanged
            if (o.must_fit) CHECK(fit.fits);
            if (fit.fits) CHECK(fit.width + 2 * kHudFitMargin <= o.w && fit.height + 2 * kHudFitMargin <= o.h);
            CHECK(fit.width >= roomy.width || fit.footer_lines < roomy.footer_lines || fit.wide_footer || fit.visible_rows < kPlacementMenuFields ||
                  fit.slider_w < roomy.slider_w);  // the sliders give up width before anything else changes
            CHECK(fit.slider_w >= kSliderMinW && fit.slider_w <= kSliderMaxW);
            if (o.w >= 1280 && o.h >= 720) CHECK(fit.visible_rows == kPlacementMenuFields && fit.hint_column);  // everything at once
        }
        // Scale 2 (960x540): a window of at least three rows; every field, when selected, is in the window and
        // highlighted; scrolling never resizes the card.
        for (std::uint32_t field = 0; field < kPlacementMenuFields; ++field) {
            PlacementMenuState sel = wide; sel.field = field;
            const auto fit = placement_menu_fit(sel, f, 960, 540);
            CHECK(fit.fits && fit.visible_rows >= 3 && fit.first_row <= field && field < fit.first_row + fit.visible_rows);
            const auto first = placement_menu_fit(wide, f, 960, 540);
            CHECK(fit.width == first.width && fit.height == first.height && fit.visible_rows == first.visible_rows);
            CHECK(band_height(render_placement_menu_card(sel, {0.5f, 0.6f, 0.9f}, f, 960, 540)) == band);
        }
    }
    // Scroll arrows: drawn only when rows are hidden (solid accent left of the table, none in the full layout).
    // An arrow row is a short solid accent run with dark card background on both sides; the selection marker
    // is followed by the wash and the tab underline is a long run.
    auto has_cue = [](const HudImage& im) {
        auto at = [&](std::uint32_t x, std::uint32_t y) { return &im.rgba[(std::size_t(y) * im.width + x) * 4]; };
        auto solid = [&](std::uint32_t x, std::uint32_t y) { return at(x, y)[2] > 200 && at(x, y)[0] < 150; };
        auto dark = [&](std::uint32_t x, std::uint32_t y) { return at(x, y)[2] < 40; };
        for (std::uint32_t y = kHudFitMargin; y + kHudFitMargin < im.height; ++y)
            for (std::uint32_t x = 8; x + 24 < im.width; ++x) {
                if (!solid(x, y) || !dark(x - 1, y)) continue;
                std::uint32_t run = 0;
                while (solid(x + run, y)) ++run;
                if (run >= 2 && run <= 18 && dark(x + run, y)) return true;
                x += run;
            }
        return false;
    };
    {
        PlacementMenuState top_sel = wide; top_sel.field = 0;
        const auto im = render_placement_menu_card(top_sel, {0.5f, 0.6f, 0.9f}, nullptr, 960, 400);
        const auto fit = placement_menu_fit(top_sel, nullptr, 960, 400);
        CHECK(fit.fits && fit.visible_rows < kPlacementMenuFields && fit.first_row == 0);
        CHECK(has_cue(im));  // rows hidden below
        CHECK(!has_cue(render_placement_menu_card(wide, {0.5f, 0.6f, 0.9f})));  // full layout: no arrows
        CHECK(!has_cue(render_placement_menu_card(wide, {0.5f, 0.6f, 0.9f}, font.get())));
    }
    // placement_menu_hints(): the footer lines as drawn, one per line, covering every hint segment in order.
    for (const HudFont* f : faces)
        for (auto [w, h] : {std::pair<std::uint32_t, std::uint32_t>{0, 0}, {1536, 960}, {1280, 720}, {960, 540}}) {
            const auto lines = placement_menu_hints(f, w, h);
            CHECK(lines.size() == placement_menu_fit(wide, f, w, h).footer_lines);
            const auto fit = placement_menu_fit(wide, f, w, h);
            std::string joined; for (const auto& l : lines) joined += l + "|";
            for (const auto& seg : placement_menu_hint_segments(fit.short_hints)) CHECK(joined.find(seg) != std::string::npos);
        }
    CHECK(placement_menu_hints().size() == 2);  // pixel font, unbounded: two footer lines
    // Values never resize the card: flat and extreme placements give the same size at every output.
    PlacementMenuState flat_menu; flat_menu.open = true;
    for (const HudFont* f : faces)
        for (auto [w, h] : {std::pair<std::uint32_t, std::uint32_t>{0, 0}, {1536, 960}, {1280, 720}, {960, 540}}) {
            const auto a = placement_menu_fit(flat_menu, f, w, h), b = placement_menu_fit(wide, f, w, h);
            CHECK(a.width == b.width && a.height == b.height && a.footer_lines == b.footer_lines);
        }
    // Hints keep every key in both wordings.
    for (bool shortw : {false, true}) {
        std::string all; for (const auto& h : placement_menu_hint_segments(shortw)) all += h + "|";
        for (const char* key : {"Up", "Down", "Left", "Right", "Shift", "Tab", "R ", "Esc"}) CHECK(all.find(key) != std::string::npos);
    }

    // Menu overlay: hidden until opened; re-renders on any change while open (no fade restart), not on
    // repeats; closing fades the last card out; changes while closed do not render.
    PlacementMenuOverlay m;
    CHECK(m.alpha(0) == 0 && !m.animating(0) && m.generation() == 0 && !m.visible());
    PlacementMenuState closed = ms; closed.open = false;
    CHECK(!m.set(closed, 0) && m.generation() == 0);
    CHECK(m.set(ms, 1000) && m.visible() && m.generation() == 1 && m.shown_count() == 1 && m.alpha(1000) == 0);
    CHECK(m.offset_px(1000) == kHelpRisePx && m.animating(1000));
    CHECK(m.alpha(1000 + kHelpFadeInMs) == 1 && !m.animating(1000 + kHelpFadeInMs) && m.offset_px(1000 + kHelpFadeInMs) == 0);
    CHECK(!m.set(ms, 1500) && m.generation() == 1);
    PlacementMenuState bumped = ms; bumped.placements[1][3] = -29;
    CHECK(m.set(bumped, 1600) && m.generation() == 2 && m.alpha(1600) == 1 && m.shown_count() == 1);
    PlacementMenuState next = bumped; next.desktop = 2;
    CHECK(m.set(next, 1700) && m.generation() == 3);
    PlacementMenuState shut = next; shut.open = false;
    CHECK(m.set(shut, 2000) && !m.visible() && m.hidden_count() == 1 && m.generation() == 3 && m.alpha(2000) == 1);
    CHECK(m.alpha(2000 + kHelpFadeOutMs) == 0 && !m.animating(2000 + kHelpFadeOutMs));
    shut.placements[0][0] = 5;
    CHECK(!m.set(shut, 3000) && m.generation() == 3);  // closed: no render
    CHECK(m.set(next, 4000) && m.generation() == 3 && m.shown_count() == 2);  // reopened unchanged: same card
    // The output size picks the arrangement: a new viewport re-renders the card; the same one does not.
    PlacementMenuOverlay fitted;
    CHECK(!fitted.set_viewport(1536, 960));  // nothing rendered yet
    CHECK(fitted.set(wide, 0) && fitted.generation() == 1 && fitted.fits());
    CHECK(!fitted.set_viewport(1536, 960) && fitted.generation() == 1);
    CHECK(fitted.set_viewport(400, 300) && fitted.generation() == 2 && !fitted.fits());
    CHECK(fitted.set_viewport(1920, 1200) && fitted.fits() && fitted.image().width == placement_menu_fit(wide, nullptr, 1920, 1200).width);

    // Image-retention protection. Both centred cards stay well below full white everywhere (text, accent, highlight
    // band, border), with the pixel font and with the installed font; the pop-ups keep their full brightness.
    {
        auto brightest = [](const HudImage& im) {
            std::uint8_t top = 0;
            for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) top = std::max(top, im.rgba[i + std::size_t(c)]);
            return top;
        };
        std::string why;
        const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement menu (arrows adjust)"}};
        for (bool real_font : {false, true}) {
            HelpOverlay help; PlacementMenuOverlay menu;
            if (real_font) { help.set_font(HudFont::load(kHudFontFamily, why)); menu.set_font(HudFont::load(kHudFontFamily, why)); }
            menu.set_viewport(1536, 960);
            help.set(true, rows, 0);
            PlacementMenuState st; st.open = true;
            for (std::uint32_t f = 0; f < kPlacementMenuFields; ++f) {  // every selection: the band moves
                st.field = f; menu.set(st, 0);
                CHECK(brightest(menu.image()) <= kCardMaxLevel);
            }
            CHECK(brightest(help.image()) <= kCardMaxLevel);
            CHECK(brightest(help.image()) >= 140 && brightest(menu.image()) >= 140);  // still clearly readable
            // Typing mode pop-up (Ctrl+Alt+T): a switch alone draws only dimmed card colours, either way.
            StatusPopups typing;
            if (real_font) typing.set_font(HudFont::load(kHudFontFamily, why));
            CHECK(typing.update(0, false, true, false, 0.0));                   // first: the viewing mode card
            CHECK(typing.update(0, false, true, true, 5000.0));
            CHECK(brightest(typing.image()) <= kCardMaxLevel && brightest(typing.image()) >= 140);
            CHECK(typing.update(0, false, true, false, 9000.0));
            CHECK(brightest(typing.image()) <= kCardMaxLevel);
        }
        CHECK(kCardMaxLevel <= 0.75 * 255);
        CHECK(kOverlayIdleCloseS >= 10 && kOverlayIdleCloseS <= 300);  // the footer note's value (tests/native_spike pins it)

    }
    // Sliders, the geometry the supervisor hit-tests with, and the mouse-mode pointer overlay.
    {
        std::string why;
        const auto real = HudFont::load(kHudFontFamily, why);
        std::vector<const HudFont*> fonts{nullptr};  // pixel fallback, and the installed font when present
        if (real) fonts.push_back(real.get());
        const auto bounds = placement_menu_bounds();
        CHECK(bounds[0] == std::make_pair(-100, 100) && bounds[1] == std::make_pair(-100, 100) && bounds[2] == std::make_pair(50, 200) &&
              bounds[3] == std::make_pair(-60, 60) && bounds[4] == std::make_pair(-45, 45) && bounds[5] == std::make_pair(-45, 45) &&
              bounds[6] == std::make_pair(0, 120));
        const std::array<float, 3> accent{0.36f, 0.48f, 0.7275f};  // the cards' dimmed accent
        auto changed_columns = [](const HudImage& a, const HudImage& b) {  // contiguous runs of columns that differ
            std::vector<std::pair<std::uint32_t, std::uint32_t>> runs;
            for (std::uint32_t x = 0; x < a.width; ++x) {
                bool diff = false;
                for (std::uint32_t y = 0; y < a.height && !diff; ++y)
                    for (int c = 0; c < 4; ++c) diff |= a.rgba[(std::size_t(y) * a.width + x) * 4 + std::size_t(c)] != b.rgba[(std::size_t(y) * b.width + x) * 4 + std::size_t(c)];
                if (!diff) continue;
                if (!runs.empty() && runs.back().second + 1 == x) runs.back().second = x; else runs.push_back({x, x});
            }
            return runs;
        };
        for (const HudFont* f : fonts)
            for (auto [w, h] : {std::pair<std::uint32_t, std::uint32_t>{0, 0}, {1536, 960}, {1280, 720}, {960, 540}}) {
                PlacementMenuState st; st.open = true; st.desktop = 1; st.field = 3;
                const auto g = placement_menu_geometry(st, f, w, h);
                const auto fit = placement_menu_fit(st, f, w, h);
                const auto card = render_placement_menu_card(st, accent, f, w, h);
                CHECK(g.width == card.width && g.height == card.height);
                // One slider per visible row, in order, bands stacked without overlap, knob travel inside the card.
                CHECK(g.sliders.size() == fit.visible_rows);
                for (std::size_t i = 0; i < g.sliders.size(); ++i) {
                    const auto& sl = g.sliders[i];
                    CHECK(sl.field == fit.first_row + i && sl.top < sl.bottom && sl.lo_x < sl.hi_x);
                    CHECK(sl.lo_x > 0 && sl.hi_x < float(g.width) && sl.top >= 0 && sl.bottom <= std::int32_t(g.height));
                    CHECK(sl.hi_x - sl.lo_x >= float(kSliderMinW) - 2 * 12 - 1);  // the knob's travel: the slider less the pointer radius each end
                    if (i) CHECK(g.sliders[i - 1].bottom <= sl.top);
                }
                // Three tabs, left to right, above the first slider, inside the card.
                for (std::size_t d = 0; d < 3; ++d) {
                    const auto& t = g.tabs[d];
                    CHECK(t[0] < t[2] && t[1] < t[3] && t[0] >= 0 && t[2] <= std::int32_t(g.width) && t[3] <= g.sliders.front().top);
                    if (d) CHECK(g.tabs[d - 1][2] <= t[0]);
                }
                // The layout never depends on values: extreme values give the same geometry.
                PlacementMenuState extreme = st;
                for (auto& pl : extreme.placements) pl = {-100, -100, 200, -60, -45, -45, 120};
                CHECK(placement_menu_geometry(extreme, f, w, h) == g);
                const auto json = menu_geometry_json(g);
                CHECK(json.rfind("{\"menu_geometry\":{\"w\":" + std::to_string(g.width) + ",\"h\":" + std::to_string(g.height) + ",\"tabs\":[[", 0) == 0);
                CHECK(json.find("\"sliders\":[[" + std::to_string(g.sliders.front().field) + ",") != std::string::npos && json.back() == '}');
            }
        // A knob at a field's low or high bound is centred on that slider's lo_x / hi_x (what the supervisor maps
        // clicks with), for every field and both fonts.
        for (const HudFont* f : fonts)
            for (std::uint32_t field = 0; field < kPlacementMenuFields; ++field) {
                PlacementMenuState lo; lo.open = true; lo.desktop = 1; lo.field = field;
                PlacementMenuState hi = lo;
                lo.placements[1][field] = bounds[field].first; hi.placements[1][field] = bounds[field].second;
                const auto g = placement_menu_geometry(lo, f, 1536, 960);
                const auto sl = *std::find_if(g.sliders.begin(), g.sliders.end(), [&](const auto& x) { return x.field == field; });
                const auto knobs = changed_columns(render_placement_menu_card(lo, accent, f, 1536, 960), render_placement_menu_card(hi, accent, f, 1536, 960));
                CHECK(knobs.size() >= 2);  // knob travel + filled track, then the value text
                const float left_centre = float(knobs[0].first) + 7, right_centre = float(knobs[0].second) + 1 - 7;  // kKnobR 7
                CHECK(std::abs(left_centre - sl.lo_x) <= 1.f && std::abs(right_centre - sl.hi_x) <= 1.f);
            }
        // The pointer: a small image of its own, in the card's text colour only (nothing above kCardMaxLevel), with a
        // clear middle as a ring and a filled one while a slider is held.
        for (bool held : {false, true}) {
            const auto im = render_menu_pointer(held);
            CHECK(im.width == im.height && im.width >= 16 && im.width <= 40 && premultiplied(im));
            std::uint8_t top = 0;
            for (std::size_t i = 0; i < im.rgba.size(); i += 4) {
                for (int c = 0; c < 3; ++c) top = std::max(top, im.rgba[i + std::size_t(c)]);
                if (im.rgba[i + 3]) CHECK(im.rgba[i + 3] == 255 && im.rgba[i] == im.rgba[i + 1] && im.rgba[i] == im.rgba[i + 2]);  // hard-edged grey
            }
            CHECK(top <= kCardMaxLevel && top >= 140);
            CHECK(!lit(im, 0, 0) && lit(im, im.width / 2, 2));  // round, not a square; the ring reaches the top middle
            CHECK(lit(im, im.width / 2, im.height / 2) == held);
        }
        // Card origin: the formula GlHud draws centred cards with (bottom edge floor((H - h) / 2 - drop) from below).
        for (auto [W, H, w, h, drop] : {std::tuple<std::uint32_t, std::uint32_t, std::uint32_t, std::uint32_t, float>{1536, 960, 1229, 733, 0},
                                        {1536, 960, 1228, 734, 18}, {1536, 960, 1229, 733, 7.5f}, {1280, 720, 1212, 640, 3}}) {
            const auto o = centered_card_origin(W, H, w, h, drop);
            CHECK(o.x == std::floor((float(W) - float(w)) / 2));
            CHECK(float(H) - o.y - float(h) == std::floor((float(H) - float(h)) / 2 - drop));
            if (drop == 0) CHECK(std::abs(o.y - (float(H) - float(h)) / 2) <= 1);
        }
        // Overlay: pointer moves never touch the card; card changes are rasterised once however many arrive before
        // the next frame; the geometry generation moves only when the layout does.
        PlacementMenuOverlay overlay;
        overlay.set_viewport(1536, 960);
        PlacementMenuState st; st.open = true;
        CHECK(overlay.set(st, 0));
        const auto renders = (overlay.image(), overlay.render_count());
        const auto generation = overlay.generation(), geometry = overlay.geometry_generation();
        CHECK(renders == 1 && geometry == 1);
        for (int i = 0; i < 50; ++i) CHECK(overlay.set_pointer(true, 10 + i, 20 + i, i % 2 == 0));
        CHECK(!overlay.set_pointer(true, 59, 69, false));  // the same place again: nothing to redraw
        CHECK(overlay.render_count() == renders && overlay.generation() == generation && overlay.pointer_x() == 59 && overlay.pointer_y() == 69);
        CHECK(overlay.pointer_visible() && &overlay.pointer_image() != nullptr && overlay.pointer_generation() == 1);
        overlay.set_pointer(true, 59, 69, true);
        CHECK(overlay.pointer_generation() == 2 && lit(overlay.pointer_image(), overlay.pointer_image().width / 2, overlay.pointer_image().height / 2));
        for (int v = -50; v < -40; ++v) { st.placements[0][3] = v; CHECK(overlay.set(st, 1)); }  // a drag: ten values in one frame
        CHECK(overlay.render_count() == renders);  // nothing rasterised yet
        overlay.image();
        CHECK(overlay.render_count() == renders + 1 && overlay.geometry_generation() == geometry);
        CHECK(overlay.set_viewport(1280, 720) && overlay.geometry_generation() == geometry + 1 && overlay.image().width <= 1280);
        PlacementMenuState shut = st; shut.open = false;
        CHECK(overlay.set(shut, 2));
        CHECK(!overlay.set_pointer(true, 59, 69, true));  // unchanged; the renderer draws no pointer without the menu anyway
    }
    std::cout << "hud ok\n";
}
