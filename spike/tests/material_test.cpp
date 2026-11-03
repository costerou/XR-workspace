// Material Design 3 (material.hpp, material_draw.hpp): the colour science (OKLCH hue and chroma, CIE L* tone, gamut
// mapping that keeps hue and tone), the dark fidelity scheme from every Omarchy theme colour (M3's tones, the seed's hue,
// greys staying grey, readable contrast between each role and the one it sits on, every role under the 191/255 OLED
// cap), the state layer, shape and elevation tokens, and the icons and shapes drawn on premultiplied images.
#include "xr/material.hpp"
#include "xr/material_draw.hpp"
#include "xr/glass_theme.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <string_view>
#include <utility>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
#define CHECK_THEME(c, name) do { if (!(c)) { std::fprintf(stderr, "%s:%d: [%s] CHECK(%s)\n", __FILE__, __LINE__, name, #c); ++failures; } } while (0)
bool near(float a, float b, float eps) { return std::fabs(a - b) <= eps; }
float hue_gap(float a, float b) { const float d = std::fabs(a - b); return std::min(d, 360 - d); }
MdRgb rgb(std::uint32_t v) { return {float((v >> 16) & 255) / 255.f, float((v >> 8) & 255) / 255.f, float(v & 255) / 255.f}; }
MdRgb undim(MdRgb c) { return {c[0] / kMdOledLevel, c[1] / kMdOledLevel, c[2] / kMdOledLevel}; }
bool grey(MdRgb c, float eps = 1.5f / 255) { return near(c[0], c[1], eps) && near(c[1], c[2], eps); }
float top(MdRgb c) { return std::max({c[0], c[1], c[2]}); }

// The first colour of general:col.active_border for each theme in /usr/share/omarchy/themes (as glass_theme_test),
// Omarchy's default border, and some extremes.
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
std::uint8_t brightest(const HudImage& im) {
    std::uint8_t m = 0;
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) m = std::max(m, im.rgba[i + std::size_t(c)]);
    return m;
}
bool premultiplied(const HudImage& im) {
    for (std::size_t i = 0; i < im.rgba.size(); i += 4) for (int c = 0; c < 3; ++c) if (im.rgba[i + std::size_t(c)] > im.rgba[i + 3]) return false;
    return true;
}
HudImage blank(std::uint32_t w, std::uint32_t h) { HudImage im; im.width = w; im.height = h; im.rgba.assign(std::size_t(w) * h * 4, 0); return im; }
std::uint8_t alpha_at(const HudImage& im, std::uint32_t x, std::uint32_t y) { return im.rgba[(std::size_t(y) * im.width + x) * 4 + 3]; }
}

int main() {
    // --- Colour science ----------------------------------------------------------------------------------------------
    // An sRGB colour comes back from its own hue, chroma and tone (to within rounding); tone is CIE L*.
    for (const auto& th : kThemes) {
        const MdRgb c = rgb(th.hex);
        const MdHct h = md_hct(c);
        const MdRgb back = md_color(h.hue, h.chroma, h.tone);
        for (std::size_t i = 0; i < 3; ++i) CHECK_THEME(near(back[i], c[i], 1.5f / 255), th.name);
        CHECK_THEME(near(md_tone(c), h.tone, 1e-3f) && h.tone >= 0 && h.tone <= 100.01f, th.name);
    }
    CHECK(near(md_tone({1, 1, 1}), 100, 0.01f) && near(md_tone({0, 0, 0}), 0, 0.01f) && near(md_tone({0.5f, 0.5f, 0.5f}), 53.39f, 0.05f));
    CHECK(near(md_hct({1, 1, 1}).chroma, 0, 1e-4f) && near(md_hct({0.3f, 0.3f, 0.3f}).chroma, 0, 1e-4f));
    // md_color: exactly the tone asked for, in the gamut, the hue kept (unless the chroma had to go), never more chroma
    // than asked; chroma 0 is grey; tone 0 and 100 are black and white; nonsense is clamped, not propagated.
    for (float hue = 0; hue < 360; hue += 15)
        for (float chroma : {0.f, 0.04f, 0.12f, 0.3f})
            for (float tone : {4.f, 17.f, 30.f, 50.f, 80.f, 98.f}) {
                const MdRgb c = md_color(hue, chroma, tone);
                for (float v : c) CHECK(v >= 0 && v <= 1);
                CHECK(near(md_tone(c), tone, 0.35f));
                const MdHct h = md_hct(c);
                CHECK(h.chroma <= chroma + 2e-3f);
                if (h.chroma > 0.02f) CHECK(hue_gap(h.hue, hue) < 2.5f);
                if (chroma == 0.f) CHECK(grey(c));
            }
    CHECK(md_color(120, 0.2f, 0) == (MdRgb{0, 0, 0}) && md_color(120, 0.2f, 100) == (MdRgb{1, 1, 1}));
    CHECK(md_color(120, 0.2f, 140) == (MdRgb{1, 1, 1}) && md_color(120, 0.2f, -5) == (MdRgb{0, 0, 0}));
    const float nan = std::numeric_limits<float>::quiet_NaN();
    for (float v : md_color(nan, nan, nan)) CHECK(v == 0.f);
    for (float v : md_color(nan, nan, 50)) CHECK(std::isfinite(v));
    CHECK(near(md_contrast({1, 1, 1}, {0, 0, 0}), 21.f, 0.01f) && near(md_contrast({0.3f, 0.6f, 0.1f}, {0.3f, 0.6f, 0.1f}), 1.f, 1e-5f));
    CHECK(md_contrast({1, 1, 1}, {0, 0, 0}) == md_contrast({0, 0, 0}, {1, 1, 1}));

    // --- The scheme, theme by theme ------------------------------------------------------------------------------------
    struct Role { const char* name; MdRgb MaterialScheme::* role; float tone; };  // M3 dark-scheme tones
    const Role kTones[] = {{"primary", &MaterialScheme::primary, 80}, {"on_primary", &MaterialScheme::on_primary, 20},
                           {"primary_bright", &MaterialScheme::primary_bright, 98},
                           {"secondary_container", &MaterialScheme::secondary_container, 30},
                           {"on_secondary_container", &MaterialScheme::on_secondary_container, 90},
                           {"error_container", &MaterialScheme::error_container, 30},
                           {"on_error_container", &MaterialScheme::on_error_container, 90},
                           {"surface", &MaterialScheme::surface, 6}, {"surface_container_low", &MaterialScheme::surface_container_low, 10},
                           {"surface_container", &MaterialScheme::surface_container, 12},
                           {"surface_container_high", &MaterialScheme::surface_container_high, 17},
                           {"surface_container_highest", &MaterialScheme::surface_container_highest, 22},
                           {"on_surface", &MaterialScheme::on_surface, 90}, {"on_surface_variant", &MaterialScheme::on_surface_variant, 80},
                           {"outline", &MaterialScheme::outline, 60}, {"outline_variant", &MaterialScheme::outline_variant, 30}};
    std::vector<std::pair<float, float>> pills;  // shipped themes: (seed tone, container tone)
    for (const auto& th : kThemes) {
        const MdRgb seed = rgb(th.hex);
        const MdHct s = md_hct(seed);
        const MaterialScheme m = material_scheme(seed);
        CHECK_THEME(m == material_scheme(seed) && glass_theme_scheme(seed) == m, th.name);  // pure; the cache agrees
        // Every role under the OLED cap (white would land exactly on it), and at M3's tone once the dimming is undone.
        for (const auto& r : kTones) {
            const MdRgb c = m.*r.role;
            for (float v : c) CHECK_THEME(v >= 0 && v <= kMdOledLevel + 1e-6f, th.name);
            if (!near(md_tone(undim(c)), r.tone, 0.6f)) std::fprintf(stderr, "[%s] %s: tone %.2f, want %.0f\n", th.name, r.name, double(md_tone(undim(c))), double(r.tone));
            CHECK_THEME(near(md_tone(undim(c)), r.tone, 0.6f), th.name);
        }
        for (float v : m.primary_container) CHECK_THEME(v <= kMdOledLevel + 1e-6f, th.name);
        for (float v : m.on_primary_container) CHECK_THEME(v <= kMdOledLevel + 1e-6f, th.name);
        // Fidelity: the primary container is the seed at its own tone, pressed into kMdContainerToneLo..Hi.
        CHECK_THEME(near(m.seed_tone, s.tone, 1e-3f), th.name);
        CHECK_THEME(near(m.container_tone, kMdContainerToneLo + (kMdContainerToneHi - kMdContainerToneLo) * s.tone / 100.f, 1e-3f), th.name);
        CHECK_THEME(near(md_tone(undim(m.primary_container)), m.container_tone, 0.6f), th.name);
        CHECK_THEME(m.container_tone >= kMdContainerToneLo && m.container_tone <= kMdContainerToneHi, th.name);
        // The seed's hue in the primary roles, the secondary calmer (less chroma), the neutrals nearly grey.
        const MdHct primary = md_hct(undim(m.primary)), container = md_hct(undim(m.primary_container));
        const MdHct secondary = md_hct(undim(m.secondary_container)), surface = md_hct(undim(m.surface_container_high));
        if (s.chroma > 0.03f) {
            CHECK_THEME(hue_gap(primary.hue, s.hue) < 2.5f && hue_gap(container.hue, s.hue) < 2.5f, th.name);
            CHECK_THEME(secondary.chroma < container.chroma + 1e-3f, th.name);
            CHECK_THEME(surface.chroma <= 0.017f, th.name);
        }
        if (s.chroma < 1e-3f)  // a grey theme (or black) gives a grey scheme
            for (const auto& r : kTones) if (std::string_view(r.name).find("error") == std::string_view::npos) CHECK_THEME(grey(m.*r.role), th.name);
        // The error roles are M3's red whatever the theme.
        CHECK_THEME(hue_gap(md_hct(undim(m.error_container)).hue, md_hct(rgb(0xb3261e)).hue) < 2.5f, th.name);
        // Contrast (M3's, measured before the dimming): text on its surface and the on-colours on their containers.
        CHECK_THEME(md_contrast(undim(m.on_surface), undim(m.surface_container_high)) >= 7.f, th.name);
        CHECK_THEME(md_contrast(undim(m.on_surface_variant), undim(m.surface_container_high)) >= 4.5f, th.name);
        CHECK_THEME(md_contrast(undim(m.on_primary), undim(m.primary)) >= 4.5f, th.name);
        CHECK_THEME(md_contrast(undim(m.on_secondary_container), undim(m.secondary_container)) >= 4.5f, th.name);
        CHECK_THEME(md_contrast(undim(m.on_error_container), undim(m.error_container)) >= 4.5f, th.name);
        CHECK_THEME(md_contrast(undim(m.on_primary_container), undim(m.primary_container)) >= 3.f, th.name);
        CHECK_THEME(md_contrast(undim(m.outline), undim(m.surface_container_high)) >= 3.f, th.name);
        if (th.shipped) pills.push_back({s.tone, md_tone(m.primary_container)});
    }
    // A lighter theme colour gives a lighter pill, theme for theme.
    std::sort(pills.begin(), pills.end());
    for (std::size_t i = 1; i < pills.size(); ++i) CHECK(pills[i].second >= pills[i - 1].second - 1e-3f);
    // Grey themes each their own grey; a near-black seed is still visible; the default seed is the compositor's grey.
    const auto vanta = material_scheme(rgb(0x8d8d8d)), white = material_scheme(rgb(0x6e6e6e)), lumon = material_scheme(rgb(0xf2fcff));
    CHECK(md_tone(vanta.primary_container) > md_tone(white.primary_container) + 3 && md_tone(lumon.primary_container) > md_tone(vanta.primary_container) + 5);
    CHECK(lumon.primary_container[2] > lumon.primary_container[0]);  // lumon's faint blue survives
    CHECK(top(material_scheme(rgb(0x010101)).primary_container) > 0.15f);
    CHECK(material_scheme(kMdDefaultSeed) == vanta && kGlassThemeDefault == kMdDefaultSeed);
    CHECK(material_scheme(rgb(0x7aa2f7)).primary != material_scheme(rgb(0xe68e0d)).primary);
    // Out-of-range and non-finite seeds are clamped (non-finite channels count as 0).
    CHECK(material_scheme({2.f, -1.f, 0.5f}) == material_scheme({1.f, 0.f, 0.5f}));
    CHECK(material_scheme({nan, 0.5f, 0.5f}) == material_scheme({0.f, 0.5f, 0.5f}));
    // The cache: more accents than it holds, then the first again: still the right scheme.
    for (const auto& th : kThemes) CHECK_THEME(glass_theme_scheme(rgb(th.hex)) == material_scheme(rgb(th.hex)), th.name);
    {
        const MaterialScheme& a = glass_theme_scheme(rgb(0x7aa2f7));
        const MaterialScheme& b = glass_theme_scheme(rgb(0x7aa2f7));
        CHECK(&a == &b);  // asked again: no recomputation
    }

    // --- Tokens --------------------------------------------------------------------------------------------------------
    CHECK(md_layer({0.1f, 0.2f, 0.3f}, {0.5f, 0.5f, 0.5f}, 0) == (MdRgb{0.1f, 0.2f, 0.3f}));
    CHECK(md_layer({0.1f, 0.2f, 0.3f}, {0.5f, 0.5f, 0.5f}, 1) == (MdRgb{0.5f, 0.5f, 0.5f}));
    CHECK(near(md_layer({0, 0, 0}, {1, 1, 1}, kMdStateHover)[0], 0.08f, 1e-6f) && md_layer({0, 0, 0}, {1, 1, 1}, 4.f)[1] == 1.f);
    CHECK(kMdStateHover < kMdStateFocus && kMdStateFocus == kMdStatePressed && kMdStatePressed < kMdStateDragged);
    CHECK(kMdDisabledContainer == 0.12f && kMdDisabledContent == 0.38f);
    CHECK(kMdShapeExtraSmall < kMdShapeSmall && kMdShapeSmall < kMdShapeMedium && kMdShapeMedium < kMdShapeLarge &&
          kMdShapeLarge < kMdShapeExtraLarge && kMdShapeExtraLarge == 28.f);
    CHECK(md_elevation(0).alpha == 0.f && md_elevation(-3).alpha == 0.f);
    float sigma = 0, dy = -1;
    for (float level = 0.5f; level <= 5.f; level += 0.5f) {  // higher: a softer, further shadow
        const MdShadow s = md_elevation(level);
        CHECK(s.alpha > 0.3f && s.alpha <= 0.6f && s.sigma > sigma && s.dy > dy);
        sigma = s.sigma; dy = s.dy;
    }
    CHECK(md_elevation(9).sigma == md_elevation(5).sigma && md_elevation(nan).alpha == 0.f);
    CHECK(md_elevation(1).sigma < md_elevation(kMdElevationHover).sigma && md_elevation(kMdElevationDialog).sigma < md_elevation(kMdElevationDragged).sigma);
    CHECK(near(md_elevation(1).dy, 0.5f, 1e-5f) && near(md_elevation(3).dy, 3.f, 1e-5f) && near(md_elevation(5).dy, 6.f, 1e-5f));  // 1, 6, 12 dp

    // --- Drawing -------------------------------------------------------------------------------------------------------
    const MdRgb ink = vanta.on_surface;
    for (const MdIcon icon : {MdIcon::add, MdIcon::remove, MdIcon::width, MdIcon::arrow_drop_down, MdIcon::arrow_drop_up}) {
        // Mirror-symmetric left to right, and inked at the centre (the plus, the minus, the double arrow's shaft, the
        // dropdown triangles).
        CHECK(md_icon_distance(icon, 0, 0, 24) < 0);
        for (float y = -11; y <= 11; y += 1.5f)
            for (float x = 0.5f; x <= 11; x += 1.5f) CHECK(near(md_icon_distance(icon, x, y, 24), md_icon_distance(icon, -x, y, 24), 1e-3f));
        CHECK(md_icon_distance(icon, 11, 11, 24) > 0 && md_icon_distance(icon, -11, -11, 24) > 0);  // corners empty
        // Size scales the icon (the same point of the grid, half the distance at half the size) down to kMdIconPx; below
        // it the strokes get heavier (Material Symbols' optical size), so a small icon keeps its weight.
        CHECK(near(md_icon_distance(icon, 6, 8, 48) / 2, md_icon_distance(icon, 3, 4, 24), 1e-3f));
        CHECK(md_icon_distance(icon, 1.5f, 2, 12) < md_icon_distance(icon, 3, 4, 24) / 2 - 0.05f);
        HudImage im = blank(40, 40);
        md_icon(im, icon, 20, 20, kMdIconPx, ink);
        CHECK(premultiplied(im) && brightest(im) <= std::uint8_t(std::lround(ink[0] * 255) + 1) && brightest(im) > 120);
        std::uint32_t x0 = 40, x1 = 0, y0 = 40, y1 = 0;
        for (std::uint32_t y = 0; y < 40; ++y)
            for (std::uint32_t x = 0; x < 40; ++x)
                if (alpha_at(im, x, y)) { x0 = std::min(x0, x); x1 = std::max(x1, x); y0 = std::min(y0, y); y1 = std::max(y1, y); }
        CHECK(x0 >= 20 - 9 - 1 && x1 <= 20 + 9 && y0 >= 20 - 9 - 1 && y1 <= 20 + 9);  // within its 18 px square
    }
    CHECK(md_icon_distance(MdIcon::add, 0, -6, 24) < 0 && md_icon_distance(MdIcon::remove, 0, -6, 24) > 0);  // the plus's upright
    CHECK(md_icon_distance(MdIcon::width, -9, -5, 24) < 0 && md_icon_distance(MdIcon::width, 9, 5, 24) < 0);   // the two bars
    CHECK(md_icon_distance(MdIcon::width, -2, -3, 24) < 0 && md_icon_distance(MdIcon::width, 0, -3, 24) > 0);  // a head, above the shaft
    // The dropdown arrows: down points down (inked below the centre, empty above its flat top), up is its mirror image.
    CHECK(md_icon_distance(MdIcon::arrow_drop_down, 0, 2.5f, 24) < 0 && md_icon_distance(MdIcon::arrow_drop_down, 0, -2.5f, 24) > 0);
    CHECK(md_icon_distance(MdIcon::arrow_drop_up, 0, -2, 24) < 0 && md_icon_distance(MdIcon::arrow_drop_up, 0, 2.5f, 24) > 0);
    for (float y = -4; y <= 4; y += 0.5f)
        for (float x = -6; x <= 6; x += 0.5f)
            CHECK(near(md_icon_distance(MdIcon::arrow_drop_up, x, y, 24), md_icon_distance(MdIcon::arrow_drop_down, x, -y, 24), 1e-3f));
    {
        HudImage im = blank(60, 30);
        md_fill_rounded(im, 5, 5, 55, 25, 10, ink, 1.f);
        CHECK(alpha_at(im, 30, 15) == 255 && alpha_at(im, 5, 5) < 64 && alpha_at(im, 0, 0) == 0 && premultiplied(im));
        HudImage ring = blank(60, 30);
        md_stroke_rounded(ring, 5, 5, 55, 25, 10, 1.f, ink, 1.f);
        CHECK(alpha_at(ring, 30, 15) == 0 && alpha_at(ring, 30, 5) > 200 && alpha_at(ring, 30, 7) == 0 && premultiplied(ring));
        HudImage dot = blank(20, 20);
        md_disc(dot, 10, 10, 6, ink, 0.5f);
        CHECK(near(float(alpha_at(dot, 10, 10)), 127.5f, 1.f) && alpha_at(dot, 10, 2) == 0);
        md_over(dot, -1, 3, ink, 1); md_over(dot, 20, 3, ink, 1); md_over(dot, 3, 3, ink, nan);  // outside / NaN: nothing
        CHECK(alpha_at(dot, 3, 3) == 0);
    }

    std::printf("%s\n", failures ? "material test FAILED" : "material test passed");
    return failures ? 1 : 0;
}
