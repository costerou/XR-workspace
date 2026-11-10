// The glass pills' theme colour (glass_theme.hpp): a Material Design 3 scheme from the border colour of every theme
// Omarchy ships. The collapsed pill is the scheme's primary container: the theme colour's own hue and chroma at its own
// tone (pressed into the range the see-through OLED shows), so pale themes give pale pills and the grey themes their own
// different greys; grey themes give grey glass. It stays under the 191/255 OLED cap in every state (hover and drag lay
// M3's state layer over it), and the open panel is the dark surface container behind its text. The panel's parts take
// the scheme's roles: the selected row's band the secondary container, the slider tracks and handles the primary, the
// text on-surface. A theme switch (Presentation::set_accent -> GlassPills::set_accent) re-renders an open panel once,
// under a new generation (so the compositor uploads it), without restarting its animation.
#include "xr/glass.hpp"
#include "xr/glass_theme.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
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
float hue_gap(float a, float b) { const float d = std::fabs(a - b); return std::min(d, 360 - d); }
Rgb colour(const std::array<float, 4>& px) { return {px[0], px[1], px[2]}; }  // premultiplied: as shown over black
Rgb unpremultiplied(const HudImage& im, std::uint32_t x, std::uint32_t y) {
    const auto* p = &im.rgba[(std::size_t(y) * im.width + x) * 4];
    const float a = float(p[3]);
    return a > 0 ? Rgb{float(p[0]) / a, float(p[1]) / a, float(p[2]) / a} : Rgb{};
}
// The selected slider row's band at (x, y), unpremultiplied: lit glass on the secondary container (glass.cpp's lit_band).
Rgb band_colour(Rgb accent, std::size_t row, float x, float y) {
    const auto& m = glass_theme_scheme(accent);
    const float h = kPanelRowH - 4;
    const auto look = glass_lit_look(m.secondary_container, md_layer(m.secondary_container, m.on_secondary_container, 0.55f), kGlassHover, h);
    const auto px = glass_shade(look, {10, kPanelRowsTop + float(row) * kPanelRowH + 2, kPanelW - 20, h}, h / 2, x + 0.5f, y + 0.5f, 1.f);
    return {px[0] / px[3], px[1] / px[3], px[2] / px[3]};
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
}

int main() {
    // The look, theme by theme, through glass_look and glass_shade (the CPU mirror of the shader the compositor runs).
    const FRect tile{100, 300, 459, 258};
    std::vector<std::pair<float, float>> pills;  // shipped themes: (border tone, shown centre tone)
    for (const auto& th : kThemes) {
        const Rgb a = rgb(th.hex);
        const auto& m = glass_theme_scheme(a);
        CHECK_THEME(m == material_scheme(a), th.name);
        for (float t : {0.f, 0.4f, 1.f}) {
            const FRect r = glass_morph(tile, t);
            const float radius = glass_radius(tile, t);
            const float cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            Rgb idle{};
            for (float level : {kGlassIdle, kGlassHover, kGlassActive}) {
                const auto look = glass_look(a, level, t);
                // Every pixel under the cap and premultiplied, in every state, in every theme.
                for (float y = r.y - 8; y < r.y + r.height + 8; y += 1.f)
                    for (float x = r.x - 4; x < r.x + r.width + 4; x += 5.f) {
                        const auto c = glass_shade(look, r, radius, x + 0.5f, y + 0.5f, 1.f);
                        for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(c[i] <= kGlassCap + 1e-5f && c[i] <= c[3] + 1e-5f, th.name);
                    }
                // The state layer: the open panel's header moves toward the content colour by M3's opacity for the
                // state; the pill's sheen fades down its top over state_soft. The cap's per-channel clamp never touches it.
                const float head_y = std::min(r.height / 2, 6.f);
                const Rgb head = colour(glass_shade(look, r, radius, cx, r.y + head_y, 1.f));
                const float sheen = look.state_alpha * std::clamp((look.state_px - head_y) / std::max(look.state_soft, 1.f) + 0.5f, 0.f, 1.f);
                if (t == 1.f) CHECK_THEME(near(sheen, glass_state_opacity(level), 1e-6f), th.name);
                const Rgb want = md_layer(look.container, look.state, sheen);
                for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(near(head[i], want[i] * look.fill, 1e-5f), th.name);
                if (level == kGlassIdle) idle = head;
                else if (t != 0.4f) CHECK_THEME(!near3(head, idle, 1e-3f), th.name);  // hover and drag show (mid-morph the on-colours cross)
                if (level != kGlassIdle || t != 0.f) continue;
                const Rgb centre = colour(glass_shade(look, r, radius, cx, cy, 1.f));
                // The collapsed pill is the primary container exactly (opaque, flat): it shows on the see-through OLED and
                // carries the theme colour's hue and nearly all of its saturation.
                CHECK_THEME(near3(centre, m.primary_container, 1e-5f) && top(centre) > 0.15f, th.name);
                const MdHct seed = md_hct(a), pill = md_hct({centre[0] / kMdOledLevel, centre[1] / kMdOledLevel, centre[2] / kMdOledLevel});
                if (seed.chroma > 0.03f) CHECK_THEME(hue_gap(pill.hue, seed.hue) < 2.5f, th.name);
                if (saturation(a) >= 0.25f && seed.tone > 30 && seed.tone < 75) CHECK_THEME(saturation(centre) >= 0.6f * saturation(a), th.name);
                if (saturation(a) == 0.f) CHECK_THEME(saturation(centre) < 0.02f, th.name);  // grey themes: grey glass
                if (th.shipped) pills.push_back({seed.tone, pill.tone});
            }
            if (t == 1.f) {  // the open panel is dark behind its text, its edge the outline variant
                const Rgb body = colour(glass_shade(glass_look(a, kGlassIdle, t), r, radius, cx, cy, 1.f));
                CHECK_THEME(glass_luma(body) < 0.2f && top(body) < 0.25f && near3(body, Rgb{m.surface_container_high[0] * kPanelFill,
                                                                                      m.surface_container_high[1] * kPanelFill,
                                                                                      m.surface_container_high[2] * kPanelFill}, 1e-5f), th.name);
            }
        }
    }
    // The pill's brightness follows the border's: a lighter border colour gives a lighter pill, theme for theme.
    std::sort(pills.begin(), pills.end());
    for (std::size_t i = 1; i < pills.size(); ++i) CHECK(pills[i].second >= pills[i - 1].second - 0.5f);  // L*, to the solver's precision
    auto centre_of = [&](std::uint32_t hex) {
        const FRect r = glass_morph(tile, 0);
        return colour(glass_shade(glass_look(rgb(hex), kGlassIdle, 0), r, glass_radius(tile, 0), r.x + r.width / 2, r.y + r.height / 2, 1.f));
    };
    // vantablack, white, lumon and kanagawa: four different pills (lumon and kanagawa light, white darker than vantablack).
    const Rgb vanta = centre_of(0x8d8d8d), white = centre_of(0x6e6e6e), lumon = centre_of(0xf2fcff), kanagawa = centre_of(0xdcd7ba);
    CHECK(glass_luma(vanta) > glass_luma(white) + 0.03f && glass_luma(lumon) > glass_luma(vanta) + 0.1f);
    CHECK(glass_luma(kanagawa) > glass_luma(vanta) + 0.08f && !near3(kanagawa, lumon, 0.01f));
    CHECK(lumon[2] > lumon[0] && kanagawa[0] > kanagawa[2]);  // lumon's faint blue, kanagawa's warm paper
    // Switching themes changes the glass (the compositor recomputes glass_look from its accent every frame).
    CHECK(glass_look(rgb(0x7aa2f7), kGlassIdle, 0).container != glass_look(rgb(0xe68e0d), kGlassIdle, 0).container);
    CHECK(glass_look(rgb(0x8d8d8d), kGlassIdle, 0).container == glass_look(kGlassThemeDefault, kGlassIdle, 0).container);

    // The level bar and the corner caps take the scheme too, in every theme: the bar is the pill's primary container, a lit
    // segment the primary (the primary bright while dragged), a cap the primary, a lit one the primary bright; nothing passes
    // the cap anywhere in the atlas.
    for (const auto& th : kThemes) {
        const Rgb a = rgb(th.hex);
        const auto& m = glass_theme_scheme(a);
        const HudImage atlas = render_handle_atlas(a);
        CHECK_THEME(atlas.rgba.size() == std::size_t(atlas.width) * atlas.height * 4, th.name);
        std::uint8_t brightest = 0;
        for (std::size_t i = 0; i < atlas.rgba.size(); i += 4) brightest = std::max({brightest, atlas.rgba[i], atlas.rgba[i + 1], atlas.rgba[i + 2]});
        CHECK_THEME(float(brightest) / 255.f <= kGlassCap + 1.5f / 255.f, th.name);
        auto px = [&](const AtlasBox& b, float u, float v) {  // u, v: 0..1 across the cell
            const auto x = std::size_t((b.u0 + (b.u1 - b.u0) * u) * float(atlas.width)), y = std::size_t((b.v0 + (b.v1 - b.v0) * v) * float(atlas.height));
            const auto* p = &atlas.rgba[(y * atlas.width + x) * 4];
            return Rgb{p[0] / 255.f, p[1] / 255.f, p[2] / 255.f};
        };
        auto cap_px = [&](const AtlasBox& b, float gap, float along) {  // a bottom-right cap's vertical leg, tile px from its corner
            const float size = kCapOuter + kCapInner;
            return px(b, (kCapInner + gap) / size, (kCapInner + along) / size);
        };
        const float third = 1.f / float(kLevelSegments);
        const AtlasBox idle = level_bar_box(LevelSegment::none, false), hot = level_bar_box(LevelSegment::cw, false), held = level_bar_box(LevelSegment::cw, true);
        CHECK_THEME(near3(px(idle, 0.5f, 0.12f), m.primary_container, 2.f / 255.f), th.name);             // the body, over the level icon
        CHECK_THEME(near3(px(hot, 2 * third + third / 8, 0.5f), m.primary, 2.f / 255.f), th.name);        // the lit segment, beside its icon
        CHECK_THEME(near3(px(held, 2 * third + third / 8, 0.5f), m.primary_bright, 2.f / 255.f), th.name);
        CHECK_THEME(near3(px(hot, third / 8, 0.5f), m.primary_container, 2.f / 255.f), th.name);          // the others stay as they were
        CHECK_THEME(glass_look(a, kGlassIdle, 0).container == m.primary_container, th.name);              // the pill below it
        CHECK_THEME(near3(cap_px(corner_cap_box(3, false, false), kCapGap, 0), m.primary, 2.f / 255.f), th.name);
        CHECK_THEME(near3(cap_px(corner_cap_box(3, true, false), kCapGap, 0), m.primary_bright, 2.f / 255.f), th.name);
        CHECK_THEME(near3(cap_px(corner_cap_box(3, true, true), kCapGap, 0), m.primary_bright, 2.f / 255.f), th.name);
    }

    // The panel content: every part in the theme's roles, under the cap.
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
        const auto fill_x = std::uint32_t((knob(placement_menu_flat()[3]) + knob(60) - kPanelKnobR) / 2 - 4);  // short of the halo
        const auto band_x = std::uint32_t(14), band_y = std::uint32_t(cy);  // the band's left end, clear of the label text
        const auto handle_x = std::uint32_t(knob(60));
        CHECK(render_glass_panel(st, f).rgba == render_glass_panel(st, f, kGlassThemeDefault).rgba);
        for (const auto& th : kThemes) {
            const Rgb a = rgb(th.hex);
            const auto& m = glass_theme_scheme(a);
            const HudImage im = render_glass_panel(st, f, a);
            CHECK_THEME(premultiplied(im) && brightest(im) <= kCardMaxLevel && brightest(im) >= 140, th.name);
            const Rgb band = unpremultiplied(im, band_x, band_y), fill = unpremultiplied(im, fill_x, band_y), handle = unpremultiplied(im, handle_x, band_y);
            const Rgb want_band = band_colour(a, 3, float(band_x), float(band_y));
            for (std::size_t i = 0; i < 3; ++i) {
                CHECK_THEME(near(band[i], want_band[i], 0.012f), th.name);
                CHECK_THEME(near(fill[i], m.primary[i], 0.012f) && near(handle[i], m.primary[i], 0.012f), th.name);
            }
            // The unselected rows' tracks: the primary at 24% on the transparent panel.
            const Rgb track = unpremultiplied(im, std::uint32_t(kPanelTrackX0 + 30), std::uint32_t(kPanelRowsTop + kPanelRowH / 2));
            CHECK_THEME(near3(track, m.primary, 0.02f), th.name);
            // The header's divider: the outline variant, opaque.
            const Rgb rule = unpremultiplied(im, std::uint32_t(kPanelW / 2), std::uint32_t(kPanelHeaderH) - 1);
            CHECK_THEME(near3(rule, m.outline_variant, 0.006f), th.name);
        }
        CHECK(render_glass_panel(st, f, rgb(0x7aa2f7)).rgba != render_glass_panel(st, f, rgb(0xe68e0d)).rgba);
    }

    // The live path: GlassPills::set_accent, as Presentation::set_accent calls it on every `style` op.
    {
        const Rgb tokyo = rgb(0x7aa2f7), matte = rgb(0xe68e0d), nord = rgb(0x81a1c1);
        const float cy = kPanelRowsTop + kPanelRowH / 2;  // the first row (selected by default)
        auto band = [&](const GlassPills& g) { return unpremultiplied(g.image(), 14, std::uint32_t(cy)); };
        auto matches = [&](Rgb got, Rgb accent) { return near3(got, band_colour(accent, 0, 14, std::floor(cy)), 0.012f); };
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
        const GlassFrame closing = g.frame(true, 5000 + kGlassCollapseMs / 20);  // before content gives way to the collapsing pill
        CHECK(closing.generation == 6 && closing.content_alpha > 0.f && matches(band(g), nord) && g.closed() == 1);
        // Reopened later: the content is drawn in the current theme.
        g.frame(true, 6000);
        CHECK(g.set(st, 6000) && matches(band(g), nord));
    }

    std::printf("%s\n", failures ? "glass theme test FAILED" : "glass theme test passed");
    return failures ? 1 : 0;
}
