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
    // Every interactive menu uses a quick ease-out on both entrance and exit. A reversal or an unseen
    // interval must preserve the opacity, and a settled card must stop requesting animation frames.
    {
        CardFade fade{180, 120};
        CHECK(fade.set(true, 100));
        CHECK(std::fabs(fade.alpha(190) - 0.875f) < 1e-5f);
        CHECK(fade.alpha(280) == 1 && !fade.animating(280));
        CHECK(fade.set(false, 300));
        CHECK(std::fabs(fade.alpha(360) - 0.125f) < 1e-5f);
        const float mid = fade.alpha(360);
        CHECK(fade.set(true, 360) && fade.alpha(360) == mid);
        CHECK(fade.alpha(450) == 1 && !fade.animating(450));
        CHECK(fade.set(false, 500));
        const float held = fade.alpha(530);
        fade.hold(530, 1000);
        CHECK(fade.alpha(1000) == held);
        CHECK(fade.alpha(1090) == 0 && !fade.animating(1090));
        CardFade instant{0, 0};
        CHECK(instant.set(true, 0) && instant.alpha(0) == 1 && !instant.animating(0));
        CHECK(instant.set(false, 0) && instant.alpha(0) == 0 && !instant.animating(0));
    }
    // Glyphs: case-insensitive; unknown characters render '?' instead of nothing.
    CHECK(&hud_glyph('a') == &hud_glyph('A') && &hud_glyph('~') == &hud_glyph('?'));
    CHECK(hud_glyph(' ') == (std::array<std::uint8_t, kGlyphH>{}));
    for (char c = 'A'; c <= 'Z'; ++c) { bool any = false; for (auto row : hud_glyph(c)) { CHECK(row < 0x20); any |= row != 0; } CHECK(any); }
    CHECK(hud_text_width("", 3) == 0 && hud_text_width("A", 1) == 5 && hud_text_width("AB", 2) == 22);

    // Pixel-font card (1:1 output pixels, font texels doubled): sized to the widest line plus
    // padding, rounded (transparent corners), an opaque outline-variant edge, premultiplied, text present.
    auto card = render_hud_card({{"VIEW MODE", 2, {1, 1, 1}}, {"FOLLOW", 4, {1, 1, 1}}}, {0.5f, 0.6f, 0.9f});
    CHECK(card.width == hud_text_width("FOLLOW", 8) + 44 && card.height == 7 * 4 + 8 + 7 * 8 + 44);
    CHECK(card.rgba.size() == std::size_t(card.width) * card.height * 4);
    CHECK(!lit(card, 0, 0) && !lit(card, card.width - 1, card.height - 1));
    CHECK(card.rgba[(std::size_t(card.height / 2) * card.width) * 4 + 3] == 255);  // left edge, mid height
    CHECK(premultiplied(card));
    {  // the edge is the scheme's outline variant (Material Design 3, from the theme colour the card is drawn with)
        const MaterialScheme m = material_scheme({0.5f, 0.6f, 0.9f});
        const auto* edge = &card.rgba[(std::size_t(card.height / 2) * card.width) * 4];
        for (int c = 0; c < 3; ++c) CHECK(std::abs(int(edge[c]) - int(std::lround(m.outline_variant[std::size_t(c)] * 255))) <= 1);
    }
    auto text_row = [](const HudImage& im, std::uint32_t y) {
        for (std::uint32_t x = 22; x + 22 < im.width; ++x) if (im.rgba[(std::size_t(y) * im.width + x) * 4] > 200) return true;
        return false;
    };
    CHECK(text_row(card, 22 + 28 + 8 + 3 * 8));  // a line without a role keeps its own colour (white here)
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
    // Each section in Material roles: what it is, the new state in the theme's primary, what it means, its key.
    const std::vector<std::vector<HudLine>> sections{view_mode_lines(0), cursor_follow_lines(true), prediction_lines(false), typing_lines(true)};
    for (const auto& lines : sections) {
        CHECK(lines.size() == 4 && lines[0].role == HudRole::on_surface_variant && lines[1].role == HudRole::primary &&
              lines[2].role == HudRole::on_surface_variant && lines[3].role == HudRole::outline);
        const MaterialScheme m = material_scheme({0.9f, 0.55f, 0.05f});
        CHECK(hud_line_color(lines[1], m) == m.primary && hud_line_color(lines[3], m) == m.outline);
    }
    CHECK(hud_line_color(HudLine{"x", 1, {0.2f, 0.3f, 0.4f}}, material_scheme({1, 0, 0})) == (std::array<float, 3>{0.2f, 0.3f, 0.4f}));
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
    // A theme switch (Presentation::set_accent): a card still showing is redrawn in place (new generation, same fade);
    // a gone one is not; the next card is drawn in the new theme either way.
    {
        StatusPopups t;
        CHECK(!t.set_accent({0.48f, 0.64f, 0.97f}, 0.0) && t.generation() == 0);  // nothing shown yet
        CHECK(t.update(0, false, 1000.0) && t.generation() == 1);
        const auto before = t.image().rgba;
        const float a = t.alpha(1000.0 + kPopupFadeInMs + 100);
        CHECK(t.set_accent({0.9f, 0.55f, 0.05f}, 1000.0 + kPopupFadeInMs + 100) && t.generation() == 2 && t.image().rgba != before);
        CHECK(t.alpha(1000.0 + kPopupFadeInMs + 100) == a);
        CHECK(!t.set_accent({0.9f, 0.55f, 0.05f}, 1200.0) && t.generation() == 2);  // the same colour: nothing
        CHECK(!t.set_accent({0.2f, 0.8f, 0.4f}, 1000.0 + kPopupTotalMs) && t.generation() == 2);  // gone: not redrawn
        CHECK(t.update(1, false, 9000.0) && t.image().rgba != before);
        HelpOverlay h;
        const std::vector<HelpRow> one{{"Ctrl+Alt+H", "Help"}};
        CHECK(!h.set_accent({0.9f, 0.55f, 0.05f}) && h.generation() == 0);
        CHECK(h.set(true, one, 0) && h.generation() == 1);
        const auto help = h.image().rgba;
        CHECK(h.set_accent({0.2f, 0.8f, 0.4f}) && h.generation() == 2 && h.image().rgba != help && h.visible());
        CHECK(!h.set_accent({0.2f, 0.8f, 0.4f}) && h.generation() == 2);
    }

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
    CHECK(std::fabs(mid - 0.875f) < 1e-5f);
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


    // Image-retention protection. The help card and every pop-up stay well below full white everywhere (text, edge,
    // dividers: Material roles under the cap), with the pixel font and with the installed font, in any theme colour.
    // (Placement panels: glass_test.)
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
            for (const auto accent : {std::array<float, 3>{1, 1, 1}, std::array<float, 3>{0.48f, 0.64f, 0.97f}, std::array<float, 3>{1, 0, 0}}) {
                StatusPopups p;
                if (real_font) p.set_font(HudFont::load(kHudFontFamily, why));
                p.set_accent(accent, 0.0);
                CHECK(p.update(2, true, true, false, 0.0) && brightest(p.image()) <= kCardMaxLevel && brightest(p.image()) >= 120);
                HelpOverlay themed;
                themed.set_accent(accent);
                themed.set(true, rows, 0);
                CHECK(brightest(themed.image()) <= kCardMaxLevel && brightest(themed.image()) >= 140);
            }
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
    {   // The tracking hint: one small line, up only while tracking is being reconnected, and never mistaken for a pop-up card.
        CHECK(joined(tracking_hint_lines()) == "Reconnecting tracking...|");
        TrackingHint hint;
        CHECK(hint.alpha(0) == 0.f);
        CHECK(!hint.update(false, 0));
        CHECK(hint.update(true, 100));
        CHECK(!hint.update(true, 110));
        CHECK(hint.alpha(100 + kTrackingHintFadeInMs) == 1.f);
        CHECK(hint.image().height > 0 && hint.image().rgba.size() == std::size_t(hint.image().width) * hint.image().height * 4);
        const HudImage card = render_hud_card(view_mode_lines(0), kMdDefaultSeed);
        CHECK(hint.image().height * 2 < card.height);
        StatusPopups popups;
        popups.update(0, false, 0);
        CHECK(hint.generation() != popups.generation());  // they share the renderer's pop-up texture
        const auto before = hint.generation();
        CHECK(hint.set_accent({0.2f, 0.8f, 0.3f}));
        CHECK(hint.generation() != before);
        CHECK(hint.update(false, 5000));
        CHECK(hint.alpha(5000 + kTrackingHintFadeOutMs + 1) == 0.f);
        CHECK(!hint.animating(5000 + kTrackingHintFadeOutMs + 1));
    }
    std::cout << "hud ok\n";
}
