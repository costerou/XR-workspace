// Help card (Ctrl+Alt+H) fit: it is laid out from the output surface so it never clips.
//
// The renderer draws the card 1:1 into the layer surface with no buffer scale, so at 1920x1200 @1.25 it has 1536x960
// logical pixels, less kHelpRisePx for the fade-in drop. Checked on the rendered pixels, with the pixel font and (when
// fontconfig finds it) the installed kHudFontFamily the renderer loads:
//   * at 1920x1200 @1.25 the real rows, and those plus three more, use the roomiest text, leave kHudFitMargin free on
//     every side, draw every row as its own text band and put no ink in the card's padding (nothing clipped);
//   * at every 1920x1200 scale from 1 to 2 it fits, and HelpOverlay re-lays it out when set_viewport changes the size
//     (as wayland.cpp does on configure);
//   * text gets smaller only when it has to: the row size never grows as the surface gets shorter.
// help_rows() must equal tools/native_spike/view.py help_rows() (tests/native_spike/test_resize.py checks).
// Moved from placement_menu_style_test.cpp, which went with the placement menu card (feature/glass-pills).
#include "xr/hud.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
#include <tuple>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(cond) do { if (!(cond)) { ++failures; std::cerr << __FILE__ << ":" << __LINE__ << ": " #cond "\n"; } } while (0)

constexpr std::uint32_t kSurfaceW = 1536, kSurfaceH = 960;  // 1920x1200 output at scale 1.25, logical pixels
constexpr std::uint32_t kPad = 34, kBorder = 2, kRadius = 14;  // help card chrome (hud.cpp kHelpPad, kBorder, kRadius)
constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};

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

// No ink inside the padding: everything between the border and the content area matches the plain background.
void check_pads_clean(const HudImage& im, const std::string& what) {
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

// The card fits the 1920x1200 @1.25 surface at rest and during its fade-in drop.
void check_fit(const HudImage& im, const std::string& what) {
    const bool at_rest = im.width <= kSurfaceW && im.height <= kSurfaceH;
    const bool with_drop = at_rest && im.height + std::uint32_t(kHelpRisePx) <= kSurfaceH;
    if (!at_rest) std::cerr << what << ": card " << im.width << "x" << im.height << " does not fit " << kSurfaceW << "x" << kSurfaceH << " (1920x1200 @1.25)\n";
    else if (!with_drop) std::cerr << what << ": card runs off the surface during its " << kHelpRisePx << " px fade-in drop\n";
    CHECK(at_rest && with_drop);
}

std::vector<HelpRow> help_rows() {  // exactly view.help_rows() (12 rows; tests/native_spike/test_resize.py checks)
    return {{"Ctrl+Alt+Up", "Zoom into the selected desktop"},
            {"Ctrl+Alt+Down", "Overview: free mouse, pills, corners"}, {"Ctrl+Alt+Left", "Previous desktop"},
            {"Ctrl+Alt+Right", "Next desktop"}, {"Ctrl+Alt+[ / ]", "Less / more space between desktops"},
            {"Ctrl+Alt+Space", "Recenter the view"}, {"Ctrl+Alt+V", "Cycle viewing mode"},
            {"Ctrl+Alt+F / P", "Toggle cursor follow / prediction"},
            {"Ctrl+Alt+H", "Help; Ctrl+Alt+G/B: window to glasses/back"}, {"Ctrl+Alt+T", "Typing mode: XR keys off / on"},
            {"Ctrl+Alt+L", "Placement pill of the selected desktop"},
            {"Ctrl+Alt+O / U", "VR video: file / copied web link"}};
}
}

int main() {
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) std::cout << "note: " << kHudFontFamily << " not available (" << why << "); real-font fit not checked\n";
    std::vector<const HudFont*> fonts{nullptr};
    if (font) fonts.push_back(font.get());
    for (const HudFont* f : fonts) {
        const std::string name = f ? "installed font" : "pixel font";
        auto twelve = help_rows();
        auto more_rows = twelve;
        for (int i = 0; i < 3; ++i) more_rows.push_back({"Ctrl+Alt+" + std::to_string(i + 1), "Send the focused window to XR-" + std::to_string(i + 1)});
        for (const auto* rs : {&twelve, &more_rows}) {
            const auto rows_name = "help card (" + std::to_string(rs->size()) + " rows), " + name;
            const auto fit = help_card_fit(*rs, f, kSurfaceW, kSurfaceH);
            const auto hc = render_help_card(*rs, kAccent, f, kSurfaceW, kSurfaceH);
            std::cout << rows_name << ": " << hc.width << "x" << hc.height << " of " << kSurfaceW << "x" << kSurfaceH << ", row text " << fit.row_px << " px\n";
            CHECK(fit.fits && fit.row_px == kHelpRowPxMax && fit.width == hc.width && fit.height == hc.height);
            CHECK(hc.height + 2 * kHudFitMargin <= kSurfaceH && hc.width + 2 * kHudFitMargin <= kSurfaceW);
            check_fit(hc, rows_name);
            check_pads_clean(hc, rows_name);
            if (!f) CHECK(ink_bands(hc).size() >= 1 + rs->size() + 1);  // title, every row, footer: each its own band
        }
        for (const float scale : {1.0f, 1.25f, 1.5f, 1.6f, 1.75f, 2.0f}) {
            const auto w = std::uint32_t(1920 / scale), h = std::uint32_t(1200 / scale);
            const auto fit = help_card_fit(help_rows(), f, w, h);
            const auto hc = render_help_card(help_rows(), kAccent, f, w, h);
            if (!fit.fits) std::cerr << "help card " << hc.width << "x" << hc.height << " does not fit " << w << "x" << h << " (" << name << ")\n";
            CHECK(fit.fits && fit.row_px >= kHelpRowPxMin && hc.height + 2 * kHudFitMargin <= h && hc.width + 2 * kHudFitMargin <= w);
            check_pads_clean(hc, name);
            HelpOverlay overlay;  // as wayland.cpp drives it: viewport on configure, re-laid out when it changes
            if (f) { std::string why_help; overlay.set_font(HudFont::load(kHudFontFamily, why_help)); }
            overlay.set(true, help_rows(), 0);
            const auto before = overlay.generation();
            CHECK(overlay.set_viewport(w, h) && overlay.generation() > before);
            CHECK(overlay.image().width == hc.width && overlay.image().height == hc.height);
        }
        std::uint32_t last_px = kHelpRowPxMax;
        for (std::uint32_t h = 1200; h >= 500; h -= 20) {
            const auto px_now = help_card_fit(help_rows(), f, 1920, h).row_px;
            CHECK(px_now <= last_px && px_now >= kHelpRowPxMin);
            last_px = px_now;
        }
    }
    if (failures) { std::cerr << failures << " help card check(s) failed\n"; return 1; }
    std::cout << "help card: fits 1920x1200 at scales 1-2, 15 rows at 1.25, OK\n";
    return 0;
}
