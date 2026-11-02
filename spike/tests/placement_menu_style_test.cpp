// Placement menu card vs the help card: same look, and it fits the glasses at 1920x1200 with scale 1.25.
//
// Style is checked on the rendered pixels, not on source text, so it holds whatever the code looks like:
//   * chrome: the card corners (radius, border, background, accent) and both pads are pixel-identical to the help card's;
//   * colours: every fully opaque pixel of the menu (pixel font, no anti-aliasing) is a colour the help card also uses
//     (text white, text dim, accent), and the menu uses both text colours;
//   * font: the pixel-font fallback has a real glyph for every character the menu prints (no '?' boxes); the installed
//     font is checked for fit and clipping when fontconfig finds it (wayland.cpp loads the same kHudFontFamily for both
//     cards; tests/native_spike/test_placement_menu.py checks that).
// Fit: the renderer draws cards 1:1 into the layer surface with no buffer scale and never shrinks them, so at
// 1920x1200 @1.25 a card has 1536x960 logical pixels, less kHelpRisePx for the fade-in drop. The menu is laid out
// for that surface the way the renderer does it (PlacementMenuOverlay::set_viewport, as wayland.cpp calls it on
// configure). Every desktop/field selection with extreme values (longest strings) must fit, all seven rows plus the
// desktop selector and every footer line must be drawn, and no ink may reach the card's padding (nothing clipped).
//
// Built only when hud.hpp declares placement_menu_fit (MENU-UI, a034eb7 or later); CMake otherwise registers it disabled.
#include "xr/hud.hpp"
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <set>
#include <string>
#include <tuple>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(cond) do { if (!(cond)) { ++failures; std::cerr << __FILE__ << ":" << __LINE__ << ": " #cond "\n"; } } while (0)

constexpr std::uint32_t kSurfaceW = 1536, kSurfaceH = 960;  // 1920x1200 output at scale 1.25, logical pixels
constexpr std::uint32_t kPad = 34, kBorder = 2, kRadius = 14;  // help card chrome (hud.cpp kHelpPad, kBorder, kRadius)
constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};    // the accent both overlays use

using Px = std::tuple<int, int, int, int>;
Px px(const HudImage& im, std::uint32_t x, std::uint32_t y) {
    const auto* p = &im.rgba[(std::size_t(y) * im.width + x) * 4];
    return {p[0], p[1], p[2], p[3]};
}
bool near(Px a, Px b, int tol) {
    return std::abs(std::get<0>(a) - std::get<0>(b)) <= tol && std::abs(std::get<1>(a) - std::get<1>(b)) <= tol &&
           std::abs(std::get<2>(a) - std::get<2>(b)) <= tol && std::abs(std::get<3>(a) - std::get<3>(b)) <= tol;
}
Px background(const HudImage& im) { return px(im, kPad / 2, im.height / 2); }  // left pad, mid height: plain card

PlacementMenuState state(std::uint32_t desktop, std::uint32_t field, int which) {
    // which: 0 all low bounds, 1 all high bounds, 2 defaults. Low bounds give the longest strings ("-100%", "-60°").
    static const MenuPlacement lo{-100, -100, 50, -60, -45, -45, 0}, hi{100, 100, 200, 60, 45, 45, 120}, def{0, 0, 100, 0, 0, 0, 0};
    PlacementMenuState s;
    s.open = true; s.desktop = desktop; s.field = field;
    for (auto& p : s.placements) p = which == 0 ? lo : which == 1 ? hi : def;
    s.placements[(desktop + 1) % 3] = hi;  // other desktops differ, which must not matter
    return s;
}

// Rows y (in the interior, between the pads) that carry ink: a fully opaque pixel that is not the border.
std::vector<std::pair<std::uint32_t, std::uint32_t>> ink_bands(const HudImage& im) {
    std::vector<std::pair<std::uint32_t, std::uint32_t>> bands;
    bool in = false;
    for (std::uint32_t y = kPad; y + kPad < im.height; ++y) {
        bool ink = false;
        for (std::uint32_t x = kPad; x + kPad < im.width && !ink; ++x) ink = std::get<3>(px(im, x, y)) == 255;
        if (ink && !in) bands.push_back({y, y});
        if (ink) bands.back().second = y;
        in = ink;
    }
    return bands;
}
std::uint32_t first_ink_row(const HudImage& im) { auto b = ink_bands(im); return b.empty() ? 0 : b.front().first; }
std::uint32_t last_ink_gap(const HudImage& im) { auto b = ink_bands(im); return b.empty() ? 0 : im.height - 1 - b.back().second; }

// No ink inside the padding: everything between the border and the content area matches the plain background.
void check_pads_clean(const HudImage& im, const char* what) {
    const auto bg = background(im);
    const std::uint32_t inner = kBorder + 2, outer = kPad - 4, corner = kRadius + kBorder + 2;
    int bad = 0;
    for (std::uint32_t y = inner; y < im.height - inner; ++y)
        for (std::uint32_t x = inner; x < im.width - inner; ++x) {
            const bool in_band = x < outer || y < outer || x >= im.width - outer || y >= im.height - outer;
            const bool in_corner = (x < corner || x >= im.width - corner) && (y < corner || y >= im.height - corner);
            if (in_band && !in_corner && !near(px(im, x, y), bg, 3)) ++bad;
        }
    if (bad) std::cerr << what << ": " << bad << " pixels of ink inside the card padding (clipped or overflowing content)\n";
    CHECK(bad == 0);
}

// Hard: the card fits at rest. The menu must also fit during its fade-in drop; for the help card (its size is not the
// menu's to choose, but it grows by the menu's toggle row) running off the edge mid-fade is reported as a warning.
void check_fit(const HudImage& im, const std::string& what, bool drop_is_hard = true) {
    const bool at_rest = im.width <= kSurfaceW && im.height <= kSurfaceH;
    const bool with_drop = at_rest && im.height + std::uint32_t(kHelpRisePx) <= kSurfaceH;
    if (!at_rest) std::cerr << what << ": card " << im.width << "x" << im.height << " does not fit " << kSurfaceW << "x" << kSurfaceH << " (1920x1200 @1.25)\n";
    else if (!with_drop) (drop_is_hard ? std::cerr : std::cout) << (drop_is_hard ? "" : "warning: ") << what << ": card " << im.width << "x" << im.height
                                     << " runs " << im.height + std::uint32_t(kHelpRisePx) - kSurfaceH << " px off the " << kSurfaceH
                                     << " px surface during its " << kHelpRisePx << " px fade-in drop\n";
    CHECK(at_rest);
    if (drop_is_hard) CHECK(with_drop);
}

std::vector<HelpRow> help_rows() {  // exactly view.help_rows() (12 rows; tests/native_spike/test_resize.py checks)
    return {{"Ctrl+Alt+Up", "Zoom into the selected desktop"}, {"Ctrl+Alt+Down", "Overview; drag a corner there to resize"},
            {"Ctrl+Alt+Left", "Previous desktop"}, {"Ctrl+Alt+Right", "Next desktop"}, {"Ctrl+Alt+[", "Less space between desktops"},
            {"Ctrl+Alt+]", "More space between desktops"}, {"Ctrl+Alt+Space", "Recenter the view"}, {"Ctrl+Alt+V", "Cycle viewing mode"},
            {"Ctrl+Alt+F", "Toggle cursor follow"}, {"Ctrl+Alt+P", "Toggle pose prediction"}, {"Ctrl+Alt+H", "Show / hide this help"},
            {"Ctrl+Alt+L", "Placement menu (Ctrl+Alt+M: mouse)"}};
}
}

int main() {
    const auto help = render_help_card(help_rows(), kAccent);
    const auto menu = render_placement_menu_card(state(0, 7, 0), kAccent);  // field 7: nothing selected

    // Chrome: corners and pads identical to the help card.
    const std::pair<std::uint32_t, std::uint32_t> corners[] = {{0, 0}, {1, 0}, {0, 1}, {1, 1}};
    for (auto [cx, cy] : corners) {
        int diff = 0;
        for (std::uint32_t y = 0; y < kPad; ++y)
            for (std::uint32_t x = 0; x < kPad; ++x)
                if (px(menu, cx ? menu.width - 1 - x : x, cy ? menu.height - 1 - y : y) !=
                    px(help, cx ? help.width - 1 - x : x, cy ? help.height - 1 - y : y)) ++diff;
        if (diff) std::cerr << "corner " << cx << "," << cy << ": " << diff << " pixels differ from the help card\n";
        CHECK(diff == 0);
    }
    CHECK(background(menu) == background(help));
    CHECK(first_ink_row(menu) == first_ink_row(help));  // same top padding to the title
    CHECK(last_ink_gap(menu) == last_ink_gap(help));    // same bottom padding under the footer

    // Colours: the menu's opaque pixels use only the help card's palette, and both text colours.
    std::set<Px> help_colours, menu_colours;
    for (std::uint32_t y = 0; y < help.height; ++y) for (std::uint32_t x = 0; x < help.width; ++x)
        if (std::get<3>(px(help, x, y)) == 255) help_colours.insert(px(help, x, y));
    for (std::uint32_t f = 0; f <= 7; ++f) {
        const auto m = render_placement_menu_card(state(f % 3, f, 0), kAccent);
        for (std::uint32_t y = 0; y < m.height; ++y) for (std::uint32_t x = 0; x < m.width; ++x)
            if (std::get<3>(px(m, x, y)) == 255) menu_colours.insert(px(m, x, y));
    }
    for (const auto& c : menu_colours)
        if (!help_colours.count(c)) { std::cerr << "menu colour not on the help card: " << std::get<0>(c) << "," << std::get<1>(c) << "," << std::get<2>(c) << "\n"; CHECK(false); }
    CHECK(help_colours.size() >= 3 && menu_colours.size() >= 3);  // white, dim, accent

    // Content: seven rows with labels and values, plain-English hints for yaw/pitch/roll, footer hints; ASCII glyphs.
    const auto& unknown = hud_glyph('\x01');
    auto all_glyphs = [&](const std::string& s) { return std::all_of(s.begin(), s.end(), [&](char c) { return c == '?' || &hud_glyph(c) != &unknown; }); };
    const auto rows = placement_menu_rows(state(1, 3, 0), true);
    CHECK(rows.size() == 7);
    std::set<std::string> labels;
    for (std::size_t i = 0; i < rows.size(); ++i) {
        CHECK(!rows[i].label.empty() && !rows[i].value.empty());
        CHECK(all_glyphs(rows[i].label) && all_glyphs(rows[i].hint) && all_glyphs(rows[i].value));
        CHECK(rows[i].selected == (i == 3));
        labels.insert(rows[i].label);
    }
    CHECK(labels.size() == 7);
    for (std::size_t i : {std::size_t(3), std::size_t(4), std::size_t(5)}) CHECK(rows.size() == 7 && !rows[i].hint.empty());  // yaw, pitch, roll explained
    CHECK(!placement_menu_hints().empty());
    for (bool short_wording : {false, true})
        for (const auto& h : placement_menu_hint_segments(short_wording)) CHECK(!h.empty() && all_glyphs(h));
    // Title + desktop selector (one band if beside the title) + 7 rows + every footer line, each a separate ink band.
    for (const auto& [mw, mh] : {std::pair<std::uint32_t, std::uint32_t>{0, 0}, {kSurfaceW, kSurfaceH}}) {
        const auto fit = placement_menu_fit(state(0, 7, 0), nullptr, mw, mh);
        const auto bands = ink_bands(render_placement_menu_card(state(0, 7, 0), kAccent, nullptr, mw, mh));
        const std::size_t want = (fit.tabs_beside_title ? 1 : 2) + 7 + fit.footer_lines;
        if (bands.size() < want) std::cerr << "only " << bands.size() << " text bands drawn of " << want << " (title, selector, 7 rows, footer)\n";
        CHECK(fit.footer_lines >= 1 && bands.size() >= want);
    }

    // Fit and clipping, pixel font and (if installed) the real font, every selection with extreme values.
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) std::cout << "note: " << kHudFontFamily << " not available (" << why << "); real-font fit not checked\n";
    std::vector<const HudFont*> fonts{nullptr};
    if (font) fonts.push_back(font.get());
    for (const HudFont* f : fonts) {
        const char* name = f ? "installed font" : "pixel font";
        std::uint32_t max_w = 0, max_h = 0;
        check_fit(render_help_card(help_rows(), kAccent, f), std::string("help card (12 rows), ") + name, false);
        for (int which = 0; which < 3; ++which)
            for (std::uint32_t d = 0; d < 3; ++d)
                for (std::uint32_t field = 0; field <= 7; ++field) {
                    const auto st = state(d, field, which);
                    const auto m = render_placement_menu_card(st, kAccent, f, kSurfaceW, kSurfaceH);
                    max_w = std::max(max_w, m.width); max_h = std::max(max_h, m.height);
                    const auto where = std::string("menu, ") + name + ", desktop " + std::to_string(d) + ", field " + std::to_string(field);
                    check_fit(m, where);
                    CHECK(placement_menu_fit(st, f, kSurfaceW, kSurfaceH).fits);
                    if (field == 7) check_pads_clean(m, name);
                }
        std::cout << name << ": largest menu card " << max_w << "x" << max_h << " of " << kSurfaceW << "x" << kSurfaceH << "\n";
        // The overlay the renderer actually draws, sized by set_viewport like wayland.cpp does on configure.
        PlacementMenuOverlay overlay;
        if (f) { std::string w; overlay.set_font(HudFont::load(kHudFontFamily, w)); }
        overlay.set_viewport(kSurfaceW, kSurfaceH);
        overlay.set(state(2, 3, 0), 1000);
        CHECK(overlay.visible() && overlay.fits());
        check_fit(overlay.image(), std::string("overlay, ") + name);
    }
    if (failures) { std::cerr << failures << " placement menu style check(s) failed\n"; return 1; }
    std::cout << "placement menu: help-card style and 1920x1200 @1.25 fit OK\n";
    return 0;
}
