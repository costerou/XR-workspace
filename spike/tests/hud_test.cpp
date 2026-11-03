#include "xr/hud.hpp"
#include <algorithm>
#include <cmath>
#include <tuple>
#include <iostream>
#include <set>
#include <string>
#include <stdexcept>
#include <type_traits>
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
    // Maximize-follow (2026-11-03): a maximize switches to follow and an un-maximize back to anchor by themselves; only a
    // Ctrl+Alt+V change gets the mode card. track_mode takes the mode quietly; before the first update it changes nothing.
    {
        StatusPopups q;
        q.track_mode(2);
        CHECK(q.update(0, false, true, false, 0.0) && q.generation() == 1);  // the start: anchor, shown
        q.track_mode(2);
        CHECK(!q.update(2, false, true, false, 3000.0) && q.generation() == 1);  // maximized: follow, quietly
        CHECK(q.update(1, false, true, false, 3100.0) && q.generation() == 2);   // Ctrl+Alt+V: locked, shown
        q.track_mode(0);
        CHECK(!q.update(0, false, true, false, 6000.0) && q.generation() == 2);  // un-maximized: anchor, quietly
        q.track_mode(2);
        CHECK(q.update(2, false, false, false, 9000.0) && q.generation() == 3);  // a prediction toggle still shows its card
    }
    // The mode card says what each mode means now: the overview anchored, a maximized screen following or locked.
    CHECK(joined(view_mode_lines(0)).find("maximized screen follows you") != std::string::npos);
    CHECK(joined(view_mode_lines(2)).find("Maximized screen") != std::string::npos);
    CHECK(joined(view_mode_lines(2)).find("Ctrl+Alt+V for locked") != std::string::npos);
    CHECK(joined(view_mode_lines(1)).find("Ctrl+Alt+V for follow") != std::string::npos);

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

    // Placement menu rows: four settings (every desktop always faces you: no yaw / pitch / roll), friendly labels, signed
    // values with units, the selected field highlighted, values from the selected desktop; ASCII units for the pixel font.
    static_assert(kPlacementMenuFields == 4 && std::tuple_size_v<MenuPlacement> == 4);
    PlacementMenuState ms;
    ms.open = true; ms.desktop = 1; ms.field = 3;
    ms.placements[0] = {0, 0, 100, 0};
    ms.placements[1] = {125, -70, 150, 90};
    ms.placements[2] = {-1800, 900, 200, 120};
    auto mr = placement_menu_rows(ms, false);
    CHECK(mr.size() == kPlacementMenuFields);
    CHECK(mr[0].label == "Left / right" && mr[1].label == "Down / up" && mr[2].label == "Distance" && mr[3].label == "Curvature");
    for (const auto& r : mr) CHECK(r.hint.empty());
    // The orbit's x / y are tenths of a degree, shown as whole degrees rounded half away from zero (12.5 -> 13).
    CHECK(mr[0].value == "+13\xc2\xb0" && mr[1].value == "-7\xc2\xb0" && mr[2].value == "150%" && mr[3].value == "90\xc2\xb0");
    {
        PlacementMenuState orbit = ms;
        auto shown = [&](std::int32_t x, bool ascii_units) { orbit.placements[1][0] = x; return placement_menu_rows(orbit, ascii_units)[0].value; };
        CHECK(shown(0, false) == "0\xc2\xb0" && shown(4, false) == "0\xc2\xb0" && shown(-4, false) == "0\xc2\xb0");  // no "+0" / "-0"
        CHECK(shown(5, false) == "+1\xc2\xb0" && shown(-5, false) == "-1\xc2\xb0" && shown(-124, false) == "-12\xc2\xb0");
        CHECK(shown(1795, false) == "+180\xc2\xb0" && shown(-1799, false) == "-180\xc2\xb0" && shown(1794, false) == "+179\xc2\xb0");
        CHECK(shown(-1795, true) == "-180deg" && shown(4, true) == "0deg" && shown(15, true) == "+2deg");
    }
    for (std::size_t i = 0; i < mr.size(); ++i) CHECK(mr[i].selected == (i == 3));
    auto ascii = placement_menu_rows(ms, true);
    CHECK(ascii[3].value == "90 deg" && ascii[2].value == "150%" && ascii[0].value == "+13deg" && ascii[1].value == "-7deg");  // the orbit: no space, to fit
    for (const auto& r : ascii) for (unsigned char c : r.label + r.hint + r.value) CHECK(c >= 0x20 && c < 0x7f);
    ms.desktop = 2; CHECK(placement_menu_rows(ms, true)[0].value == "-180deg" && placement_menu_rows(ms, true)[3].value == "120 deg");
    CHECK(placement_menu_rows(ms, false)[0].value == "-180\xc2\xb0" && placement_menu_rows(ms, false)[1].value == "+90\xc2\xb0" &&
          placement_menu_rows(ms, false)[2].value == "200%" && placement_menu_rows(ms, false)[3].value == "120\xc2\xb0");
    ms.desktop = 0;
    CHECK(placement_menu_rows(ms, false)[2].value == "100%" && placement_menu_rows(ms, false)[3].value == "0\xc2\xb0");
    {  // out of range: clamped to the last of kPlacementMenuDesktopCount, no crash
        PlacementMenuState last = ms; last.desktop = std::uint32_t(kPlacementMenuDesktopCount - 1);
        ms.desktop = 9; CHECK(placement_menu_rows(ms, true)[0].value == placement_menu_rows(last, true)[0].value);
    }
    ms.field = 4; for (const auto& r : placement_menu_rows(ms, true)) CHECK(!r.selected);  // past the last field: none
    ms.desktop = 1; ms.field = 3;
    const auto flat = placement_menu_flat();
    CHECK((flat == MenuPlacement{0, 0, 100, 0}));


    // Image-retention protection. The help card stays well below full white everywhere (text, accent, border), with
    // the pixel font and with the installed font; the pop-ups keep their full brightness. (Placement panels: glass_test.)
    {
        auto brightest = [](const HudImage& im) {
            std::uint8_t top = 0;
            for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) top = std::max(top, im.rgba[i + std::size_t(c)]);
            return top;
        };
        std::string why;
        const std::vector<HelpRow> rows{{"Ctrl+Alt+H", "Show / hide this help"}, {"Ctrl+Alt+L", "Placement pill of the selected desktop"}};
        for (bool real_font : {false, true}) {
            HelpOverlay help;
            if (real_font) help.set_font(HudFont::load(kHudFontFamily, why));
            help.set(true, rows, 0);
            CHECK(brightest(help.image()) <= kCardMaxLevel);
            CHECK(brightest(help.image()) >= 140);  // still clearly readable
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
    // Slider bounds (the panel's sliders end at these) and the card origin GlHud centres the help card with.
    {
        const auto bounds = placement_menu_bounds();
        static_assert(std::tuple_size_v<std::remove_const_t<decltype(bounds)>> == 4);  // x, y, distance, curvature: no yaw / pitch / roll
        CHECK(bounds[0] == std::make_pair(-1800, 1800) && bounds[1] == std::make_pair(-900, 900) && bounds[2] == std::make_pair(10, 200) &&
              bounds[3] == std::make_pair(0, 120));
        // Card origin: the formula GlHud draws centred cards with (bottom edge floor((H - h) / 2 - drop) from below).
        for (auto [W, H, w, h, drop] : {std::tuple<std::uint32_t, std::uint32_t, std::uint32_t, std::uint32_t, float>{1536, 960, 1229, 733, 0},
                                        {1536, 960, 1228, 734, 18}, {1536, 960, 1229, 733, 7.5f}, {1280, 720, 1212, 640, 3}}) {
            const auto o = centered_card_origin(W, H, w, h, drop);
            CHECK(o.x == std::floor((float(W) - float(w)) / 2));
            CHECK(float(H) - o.y - float(h) == std::floor((float(H) - float(h)) / 2 - drop));
            if (drop == 0) CHECK(std::abs(o.y - (float(H) - float(h)) / 2) <= 1);
        }
    }
    std::cout << "hud ok\n";
}
