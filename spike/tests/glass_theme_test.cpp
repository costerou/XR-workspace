// The glass pills' theme colour (glass_theme.hpp). For the border colour of every theme Omarchy ships, the pill's glass
// takes the theme's hue (much more of it than the old fifth) at one brightness, keeps its gloss and hairline, brightens
// on hover and drag, and stays under the 191/255 OLED cap in every state without clipping its body; grey themes give
// grey glass; the open panel stays dark behind its text. The panel's band and slider fills are the theme colour at the
// cards' dimmed level, and nothing else on the panel changes with the theme. A theme switch (Presentation::set_accent ->
// GlassPills::set_accent) re-renders an open panel once, under a new generation (so the compositor uploads it), without
// restarting its animation.
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
#define CHECK_THEME(c, name) do { if (!(c)) { std::fprintf(stderr, "%s:%d: [%s] CHECK(%s)\n", __FILE__, __LINE__, name, #c); ++failures; } } while (0)
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
using Rgb = std::array<float, 3>;
bool near3(const Rgb& a, const Rgb& b, float eps = 1e-5f) { return near(a[0], b[0], eps) && near(a[1], b[1], eps) && near(a[2], b[2], eps); }

// The first colour of general:col.active_border for each theme in /usr/share/omarchy/themes (its hyprland_active_border
// or active_border_color where it sets one, else its accent), Omarchy's default looknfeel border, and some extremes.
struct Theme { const char* name; std::uint32_t hex; bool shipped; };
constexpr Theme kThemes[] = {
    {"catppuccin", 0x89b4fa, true}, {"catppuccin-latte", 0x1e66f5, true}, {"ethereal", 0x7d82d9, true},
    {"everforest", 0x7fbbb3, true}, {"flexoki-light", 0x205ea6, true}, {"gruvbox", 0x7daea3, true},
    {"hackerman", 0x26a269, true}, {"jade-horse", 0x8bcbb1, true}, {"kanagawa", 0xdcd7ba, true},
    {"last-horizon", 0x8a8588, true}, {"lumon", 0xf2fcff, true}, {"lupine", 0x3264eb, true},
    {"matte-black", 0xe68e0d, true}, {"miasma", 0x78824b, true}, {"nord", 0x81a1c1, true},
    {"retro-82", 0xfaa968, true}, {"ristretto", 0xf38d70, true}, {"rose-pine", 0x56949f, true},
    {"solitude", 0x798186, true}, {"starship", 0xff9f0a, true}, {"tokyo-night", 0x7aa2f7, true},
    {"vantablack", 0x8d8d8d, true}, {"white", 0x6e6e6e, true}, {"omarchy-default", 0x33ccff, true},
    {"red", 0xff0000, false}, {"green", 0x00ff00, false}, {"blue", 0x0000ff, false}, {"black", 0x000000, false},
    {"full-white", 0xffffff, false}, {"navy", 0x101040, false}, {"near-black", 0x010101, false},
};
Rgb rgb(std::uint32_t v) { return {float((v >> 16) & 255) / 255.f, float((v >> 8) & 255) / 255.f, float(v & 255) / 255.f}; }
float top(Rgb c) { return std::max({c[0], c[1], c[2]}); }
float saturation(Rgb c) { const float m = top(c); return m > 0 ? (m - std::min({c[0], c[1], c[2]})) / m : 0.f; }
float hue(Rgb c) {  // HSV hue, degrees
    const float m = top(c), d = m - std::min({c[0], c[1], c[2]});
    if (d <= 0) return 0;
    float h = m == c[0] ? std::fmod((c[1] - c[2]) / d, 6.f) : m == c[1] ? (c[2] - c[0]) / d + 2 : (c[0] - c[1]) / d + 4;
    h *= 60; return h < 0 ? h + 360 : h;
}
float hue_gap(Rgb a, Rgb b) { const float d = std::fabs(hue(a) - hue(b)); return std::min(d, 360 - d); }
Rgb colour(const std::array<float, 4>& px) { return {px[0], px[1], px[2]}; }  // premultiplied: as shown over black
Rgb unpremultiplied(const HudImage& im, std::uint32_t x, std::uint32_t y) {
    const auto* p = &im.rgba[(std::size_t(y) * im.width + x) * 4];
    const float a = float(p[3]);
    return a > 0 ? Rgb{float(p[0]) / a, float(p[1]) / a, float(p[2]) / a} : Rgb{};
}
std::uint8_t brightest(const HudImage& im) {
    std::uint8_t m = 0;
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) m = std::max(m, im.rgba[i + std::size_t(c)]);
    return m;
}
bool premultiplied(const HudImage& im) {
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) if (im.rgba[i + std::size_t(c)] > im.rgba[i + 3]) return false;
    return true;
}
bool same_rows(const HudImage& a, const HudImage& b, std::uint32_t y0, std::uint32_t y1) {
    for (std::uint32_t y = y0; y < y1; ++y)
        for (std::uint32_t x = 0; x < a.width; ++x)
            for (int c = 0; c < 4; ++c)
                if (a.rgba[(std::size_t(y) * a.width + x) * 4 + std::size_t(c)] != b.rgba[(std::size_t(y) * b.width + x) * 4 + std::size_t(c)]) return false;
    return true;
}
// The old look's tint (a fifth of the accent mixed into slate), to show the new one carries far more of the theme.
Rgb old_tint(Rgb a) { Rgb out{}; for (std::size_t i = 0; i < 3; ++i) out[i] = kGlassSlate[i] + (std::clamp(a[i], 0.f, 1.f) - kGlassSlate[i]) * 0.2f; return out; }
}

int main() {
    // glass_theme_hue: hue and saturation kept, at the luma asked for unless the channel peak comes first; greys stay grey,
    // black (or anything under 1/255) counts as grey; out-of-range channels are clamped, non-finite ones count as 0.
    for (const auto& th : kThemes) {
        const Rgb a = rgb(th.hex);
        for (const auto& [luma, peak] : {std::pair{kGlassTintLuma, kGlassTintPeak}, std::pair{0.5f, 0.9f}, std::pair{1.f, kGlassHighlightPeak}}) {
            const Rgb h = glass_theme_hue(a, luma, peak);
            CHECK_THEME(top(h) <= peak + 1e-6f, th.name);
            CHECK_THEME(near(glass_luma(h), luma, 1e-4f) || near(top(h), peak, 1e-6f), th.name);  // whichever is reached first
            CHECK_THEME(glass_luma(h) <= luma + 1e-4f, th.name);
            if (top(a) >= 1.f / 255.f) {
                CHECK_THEME(near(saturation(h), saturation(a), 1e-4f), th.name);
                if (saturation(a) > 0.02f) CHECK_THEME(hue_gap(h, a) < 0.1f, th.name);
            } else {
                CHECK_THEME(saturation(h) == 0.f, th.name);  // black: grey
            }
        }
    }
    CHECK(near3(glass_theme_hue({0.5f, 0.5f, 0.5f}, 0.3f, 0.42f), {0.3f, 0.3f, 0.3f}));
    CHECK(near3(glass_theme_hue({0, 0, 0}, 0.3f, 0.42f), {0.3f, 0.3f, 0.3f}));
    CHECK(near3(glass_theme_hue({0.5f, 0.5f, 0.5f}, 0.6f, 0.42f), {0.42f, 0.42f, 0.42f}));  // the peak first
    CHECK(glass_theme_hue({2.f, -1.f, 0.5f}, 1.f, 0.8f) == glass_theme_hue({1.f, 0.f, 0.5f}, 1.f, 0.8f));
    const float nan = std::numeric_limits<float>::quiet_NaN(), inf = std::numeric_limits<float>::infinity();
    CHECK(glass_theme_hue({nan, inf, 0.5f}, 1.f, 0.8f) == glass_theme_hue({0.f, 0.f, 0.5f}, 1.f, 0.8f));
    CHECK(near3(glass_theme_hue({nan, nan, nan}, 0.3f, 0.42f), {0.3f, 0.3f, 0.3f}));
    CHECK(near(glass_luma({1, 1, 1}), 1.f) && near(glass_luma({0, 1, 0}), 0.7152f));

    // glass_theme_tint: the pill carries kGlassTintPill of the theme hue, the open panel kGlassTintPanel, in between a blend.
    {
        const Rgb a = rgb(0xe68e0d), h = glass_theme_hue(a, kGlassTintLuma, kGlassTintPeak);
        for (const auto& [t, share] : {std::pair{0.f, kGlassTintPill}, std::pair{1.f, kGlassTintPanel}, std::pair{-3.f, kGlassTintPill}, std::pair{4.f, kGlassTintPanel}}) {
            const Rgb tint = glass_theme_tint(a, t);
            for (std::size_t i = 0; i < 3; ++i) CHECK(near(tint[i], kGlassSlate[i] + (h[i] - kGlassSlate[i]) * share));
        }
        const Rgb mid = glass_theme_tint(a, 0.5f), p = glass_theme_tint(a, 0), q = glass_theme_tint(a, 1);
        for (std::size_t i = 0; i < 3; ++i) CHECK(near(mid[i], (p[i] + q[i]) / 2));
        CHECK(saturation(p) > saturation(mid) && saturation(mid) > saturation(q));  // the panel is calmer behind its text
    }

    // The look, theme by theme, through glass_look and glass_shade (the CPU mirror of the shader the compositor runs).
    const FRect tile{100, 300, 459, 258};
    float pill_luma_lo = 1, pill_luma_hi = 0, level_lo = 1, level_hi = 0;
    for (const auto& th : kThemes) {
        const Rgb a = rgb(th.hex);
        for (float t : {0.f, 0.4f, 1.f}) {
            const FRect r = glass_morph(tile, t);
            const float radius = glass_radius(tile, t);
            const float cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            float prev_centre = 0, prev_rim = 0;
            for (float level : {kGlassIdle, kGlassHover, kGlassActive}) {
                const auto look = glass_look(a, level, t);
                // Every pixel under the cap and premultiplied; the body itself never reaches the cap, so the cap's
                // per-channel clamp never shifts the theme's hue, even while dragged.
                for (float y = r.y - 8; y < r.y + r.height + 8; y += 1.f)
                    for (float x = r.x - 4; x < r.x + r.width + 4; x += 5.f) {
                        const auto c = glass_shade(look, r, radius, x + 0.5f, y + 0.5f, 1.f);
                        for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(c[i] <= kGlassCap + 1e-5f && c[i] <= c[3] + 1e-5f, th.name);
                    }
                const Rgb centre = colour(glass_shade(look, r, radius, cx, cy, 1.f));
                const Rgb body_top = colour(glass_shade(look, r, radius, cx, r.y + std::min(r.height / 2, 3.f), 1.f));
                for (float v : {centre[0], centre[1], centre[2]}) CHECK_THEME(v < kGlassCap - 0.02f, th.name);
                for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(look.tint[i] * 1.25f * look.fill * (1 + 0.25f * (look.level - 1)) < kGlassCap, th.name);
                // Gloss and hairline still show on every theme: the top edge is brighter than the body.
                const Rgb rim = colour(glass_shade(look, r, radius, cx, r.y + 0.5f, 1.f));
                CHECK_THEME(glass_luma(rim) > glass_luma(body_top) && glass_luma(body_top) > glass_luma(centre) * 0.99f, th.name);
                // Hover and drag brighten it.
                CHECK_THEME(glass_luma(centre) > prev_centre && glass_luma(rim) >= prev_rim, th.name);
                prev_centre = glass_luma(centre); prev_rim = glass_luma(rim);
                if (level != kGlassIdle) continue;
                if (t == 0.f) {
                    // The collapsed pill shows (black is see-through on the OLED panels) and has the theme's hue, with most
                    // of its saturation, and much more of it than the old fifth-of-the-accent tint had.
                    CHECK_THEME(top(centre) > 0.3f, th.name);
                    if (saturation(a) >= 0.25f) {
                        CHECK_THEME(hue_gap(centre, a) < 6.f, th.name);
                        CHECK_THEME(saturation(centre) >= 0.8f * saturation(a), th.name);
                        CHECK_THEME(saturation(centre) > 1.2f * saturation(old_tint(a)), th.name);
                    }
                    if (saturation(a) == 0.f) CHECK_THEME(saturation(centre) < 0.05f, th.name);  // grey themes: grey glass
                    if (th.shipped) { pill_luma_lo = std::min(pill_luma_lo, glass_luma(centre)); pill_luma_hi = std::max(pill_luma_hi, glass_luma(centre)); }
                    if (near(glass_luma(glass_theme_hue(a, kGlassTintLuma, kGlassTintPeak)), kGlassTintLuma)) {  // not capped first
                        level_lo = std::min(level_lo, glass_luma(centre)); level_hi = std::max(level_hi, glass_luma(centre));
                    }
                }
                if (t == 1.f) CHECK_THEME(glass_luma(centre) < 0.2f && top(centre) < 0.4f, th.name);  // dark behind the text
            }
        }
    }
    // One brightness: every theme whose hue reaches the luma target gives a pill just as bright, however light or dark its
    // border colour (the old tint followed it: kanagawa's pill was ~40% brighter than flexoki-light's). Deep blues, whose
    // brightest channel reaches the peak first, are a little darker, never dim.
    CHECK(level_hi - level_lo < 0.02f && level_lo > 0.25f);
    CHECK(pill_luma_lo > 0.18f && pill_luma_hi < 0.36f);
    // Switching themes changes the glass (the compositor recomputes glass_look from its accent every frame).
    CHECK(glass_look(rgb(0x7aa2f7), kGlassIdle, 0).tint != glass_look(rgb(0xe68e0d), kGlassIdle, 0).tint);
    CHECK(glass_look(rgb(0x8d8d8d), kGlassIdle, 0).tint == glass_look(kGlassThemeDefault, kGlassIdle, 0).tint);

    // glass_theme_highlight: the accent's hue with its brightest channel at the cards' dimmed accent level (hud.cpp
    // kCardAccent is the pop-up blue at 75%: the highlight of that blue is exactly it). Black gives grey.
    {
        const Rgb card = glass_theme_highlight({0.48f, 0.64f, 0.97f});
        CHECK(near(card[0], 0.48f * 0.75f) && near(card[1], 0.64f * 0.75f) && near(card[2], 0.97f * 0.75f));
        for (const auto& th : kThemes) {
            const Rgb a = rgb(th.hex), h = glass_theme_highlight(a);
            CHECK_THEME(near(top(h), kGlassHighlightPeak, 1e-6f) && top(h) <= kGlassCap, th.name);
            if (saturation(a) > 0.02f && top(a) >= 1.f / 255.f) CHECK_THEME(hue_gap(h, a) < 0.1f, th.name);
        }
        CHECK(glass_theme_highlight({0, 0, 0}) == (Rgb{kGlassHighlightPeak, kGlassHighlightPeak, kGlassHighlightPeak}));
    }

    // The panel content: band and fills in the theme colour, everything else the same for every theme, under the cap.
    std::string why;
    const auto real = HudFont::load(kHudFontFamily, why);
    std::vector<const HudFont*> fonts{nullptr};
    if (real) fonts.push_back(real.get());
    for (const HudFont* f : fonts) {
        PlacementMenuState st; st.open = true; st.desktop = 1; st.field = 3;  // curvature selected
        for (auto& p : st.placements) p = placement_menu_flat();
        st.placements[1][3] = 60;  // a curvature fill from the flat value up to the knob
        const auto bounds = placement_menu_bounds();
        const float cy = kPanelRowsTop + 3 * kPanelRowH + kPanelRowH / 2;
        auto knob = [&](std::int32_t v) {
            const auto [lo, hi] = bounds[3];
            return kPanelKnobLo + (kPanelKnobHi - kPanelKnobLo) * float(std::clamp(v, lo, hi) - lo) / float(hi - lo);
        };
        const auto fill_x = std::uint32_t((knob(placement_menu_flat()[3]) + knob(60) - kPanelKnobR) / 2);  // short of the knob
        const auto band_x = std::uint32_t(14), band_y = std::uint32_t(cy);  // the band's left end, clear of the label text
        CHECK(render_glass_panel(st, f).rgba == render_glass_panel(st, f, kGlassThemeDefault).rgba);
        const HudImage base = render_glass_panel(st, f, rgb(0x8d8d8d));
        for (const auto& th : kThemes) {
            const Rgb a = rgb(th.hex), h = glass_theme_highlight(a);
            const HudImage im = render_glass_panel(st, f, a);
            CHECK_THEME(premultiplied(im) && brightest(im) <= kCardMaxLevel && brightest(im) >= 140, th.name);
            const Rgb fill = unpremultiplied(im, fill_x, band_y), band = unpremultiplied(im, band_x, band_y);
            // (The fill is 90% over the track, so a little of the grey track shows through it.)
            for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(near(fill[i], h[i], 0.03f) && near(band[i], h[i], 0.02f), th.name);
            // Only the highlights follow the theme: the header, the unselected rows and the footer are unchanged.
            CHECK_THEME(same_rows(im, base, 0, std::uint32_t(kPanelRowsTop + 3 * kPanelRowH)), th.name);
            CHECK_THEME(same_rows(im, base, std::uint32_t(kPanelRowsTop + 4 * kPanelRowH), im.height), th.name);
        }
        CHECK(render_glass_panel(st, f, rgb(0x7aa2f7)).rgba != render_glass_panel(st, f, rgb(0xe68e0d)).rgba);
    }

    // The live path: GlassPills::set_accent, as Presentation::set_accent calls it on every `style` op.
    {
        const Rgb tokyo = rgb(0x7aa2f7), matte = rgb(0xe68e0d), nord = rgb(0x81a1c1);
        const float cy = kPanelRowsTop + kPanelRowH / 2;  // the first row (selected by default)
        auto band = [&](const GlassPills& g) { return unpremultiplied(g.image(), 14, std::uint32_t(cy)); };
        auto matches = [](Rgb got, Rgb accent) {
            const Rgb h = glass_theme_highlight(accent);
            return near(got[0], h[0], 0.04f) && near(got[1], h[1], 0.04f) && near(got[2], h[2], 0.04f);
        };
        GlassPills g;
        CHECK(!g.set_accent(tokyo) && g.generation() == 0 && g.renders() == 0 && g.content_desktop() == -1);  // nothing to redo
        PlacementMenuState st; st.open = true; st.desktop = 2;
        CHECK(g.set(st, 1000) && g.generation() == 1);
        CHECK(matches(band(g), tokyo) && g.renders() == 1);  // the first render already uses the theme set before it
        const double open = 1000 + kGlassExpandMs + 1;
        const GlassFrame before = g.frame(true, open);
        CHECK(before.generation == 1 && near(before.content_alpha, 1.f) && near(before.expand[2], 1.f));
        CHECK(!g.set_accent(tokyo) && g.generation() == 1);  // the same theme again: nothing
        CHECK(g.set_accent(matte) && g.generation() == 2 && g.renders() == 1);  // re-rendered lazily...
        const GlassFrame after = g.frame(true, open + 8);
        CHECK(after.generation == 2 && after.image && g.renders() == 2 && matches(band(g), matte));  // ...once, on the next frame
        CHECK(near(after.content_alpha, 1.f) && near(after.expand[2], 1.f) && !g.animating(open + 8));  // no animation restarts
        CHECK(g.opened() == 1 && g.closed() == 0 && g.open() && g.content_desktop() == 2);
        // Several switches before the next frame: one render, in the last theme.
        CHECK(g.set_accent(nord) && g.set_accent(tokyo) && g.set_accent(matte) && g.generation() == 5);
        g.frame(true, open + 16);
        CHECK(g.renders() == 3 && matches(band(g), matte));
        // A switch while the panel collapses recolours what is left of it; the collapse carries on.
        PlacementMenuState shut = st; shut.open = false;
        CHECK(g.set(shut, 5000) && !g.open());
        CHECK(g.set_accent(nord) && g.generation() == 6);
        const GlassFrame closing = g.frame(true, 5000 + kGlassCollapseMs / 4);
        CHECK(closing.generation == 6 && closing.content_alpha > 0.f && matches(band(g), nord) && g.closed() == 1);
        // Reopened later: the content is drawn in the current theme.
        g.frame(true, 6000);
        CHECK(g.set(st, 6000) && matches(band(g), nord));
    }

    std::printf("%s\n", failures ? "glass theme test FAILED" : "glass theme test passed");
    return failures ? 1 : 0;
}
