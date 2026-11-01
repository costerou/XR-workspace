#include "xr/hud.hpp"
#include <iostream>
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
    StatusPopups bad; CHECK(bad.update(9, false, 0));  // out-of-range mode is treated as anchor

    // Help card: brackets have real pixel glyphs; card grows with rows, is premultiplied and
    // rounded; with the system font it also renders.
    CHECK(&hud_glyph('[') != &hud_glyph('?') && &hud_glyph(']') != &hud_glyph('?'));
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
    CHECK(h.offset_px(1000) == kHelpRisePx);
    for (double t = 1000; t < 1000 + kHelpFadeInMs; t += 10) CHECK(h.alpha(t + 10) >= h.alpha(t));
    CHECK(h.alpha(1000 + kHelpFadeInMs) == 1 && h.offset_px(1000 + kHelpFadeInMs) == 0 && !h.animating(1000 + kHelpFadeInMs));
    CHECK(h.alpha(1e9) == 1);  // stays up while visible
    CHECK(!h.set(true, rows, 2000) && h.generation() == 1);  // same rows: no re-render
    CHECK(h.set(false, rows, 3000) && h.animating(3000) && h.alpha(3000) == 1);
    for (double t = 3000; t < 3000 + kHelpFadeOutMs; t += 10) CHECK(h.alpha(t + 10) <= h.alpha(t));
    CHECK(h.alpha(3000 + kHelpFadeOutMs) == 0 && !h.animating(3000 + kHelpFadeOutMs));
    CHECK(h.set(true, rows, 5000));
    const float mid = h.alpha(5000 + kHelpFadeInMs / 2);
    CHECK(mid > 0.2f && mid < 0.8f);
    CHECK(h.set(false, rows, 5000 + kHelpFadeInMs / 2) && h.alpha(5000 + kHelpFadeInMs / 2) == mid);  // reverses, no jump
    CHECK(h.alpha(5000 + kHelpFadeInMs / 2 + kHelpFadeOutMs) == 0);
    CHECK(h.set(true, {rows[0]}, 7000) && h.generation() == 2);  // new rows: new card
    std::cout << "hud ok\n";
}
