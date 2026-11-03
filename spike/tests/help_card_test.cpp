// Help card (Ctrl+Alt+H) fit and pages: it is laid out from the output surface so it never clips, with any number of rows.
//
// The renderer draws the card 1:1 into the layer surface with no buffer scale, so at 1920x1200 @1.25 it has 1536x960
// logical pixels, less kHelpRisePx for the fade-in drop. Checked on the rendered pixels, with the pixel font and (when
// fontconfig finds it) the installed kHudFontFamily the renderer loads:
//   * at 1920x1200 @1.25 the real rows sit on one page at the roomiest text, and with three more rows still on one page,
//     leaving kHudFitMargin free on every side, every row its own text band and no ink in the card's padding;
//   * any row count (1 to kMaxHelpRows-sized lists) at every 1920x1200 scale from 1 to 2: the card fits, rows that do not
//     fit flow onto further pages (every row on exactly one page, in order, none empty), every page is the same size
//     and none puts ink in the padding (nothing clipped), and a paged card keeps the smallest text (the current size);
//   * a section stays on one page when it fits one; a section taller than a page is split;
//   * HelpOverlay turns pages (wrapping round), starts a reopened card on page 1 and re-lays it out when set_viewport
//     changes the size (as wayland.cpp does on configure);
//   * the page arrows and indicator are drawn inside their click rects, and help_geometry_json maps them onto the canvas;
//   * text gets smaller only when it has to: the row size never grows as the surface gets shorter.
// help_rows() must equal tools/native_spike/view.py help_rows(), `true` marking view.help_sections() starts
// (tests/native_spike/test_resize.py checks).
#include "xr/hud.hpp"
#include "xr/pose.hpp"
#include <cmath>
#include <cstdio>
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
constexpr std::uint32_t kPad = 34, kBorder = 1, kRadius = 28;  // help card chrome (hud.cpp kHelpPad, kBorder, kRadius: M3 extra large)
constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};
constexpr float kScales[] = {1.0f, 1.25f, 1.5f, 1.6f, 1.75f, 2.0f};

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
// Opaque (ink) pixels inside card rect r.
int ink_in(const HudImage& im, const HelpRect& r) {
    int n = 0;
    for (std::int32_t y = std::max(r.y, 0); y < std::min<std::int32_t>(r.y + r.h, std::int32_t(im.height)); ++y)
        for (std::int32_t x = std::max(r.x, 0); x < std::min<std::int32_t>(r.x + r.w, std::int32_t(im.width)); ++x)
            n += std::get<3>(px(im, std::uint32_t(x), std::uint32_t(y))) == 255;
    return n;
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

// The card fits the surface at rest and during its fade-in drop.
void check_fit(const HudImage& im, std::uint32_t w, std::uint32_t h, const std::string& what) {
    const bool at_rest = im.width + 2 * kHudFitMargin <= w && im.height + 2 * kHudFitMargin <= h;
    const bool with_drop = at_rest && im.height + std::uint32_t(kHelpRisePx) <= h;
    if (!at_rest) std::cerr << what << ": card " << im.width << "x" << im.height << " does not fit " << w << "x" << h << " with its margin\n";
    else if (!with_drop) std::cerr << what << ": card runs off the surface during its " << kHelpRisePx << " px fade-in drop\n";
    CHECK(at_rest && with_drop);
}

std::vector<HelpRow> help_rows() {  // exactly view.help_rows(), sections marked (tests/native_spike/test_resize.py checks)
    return {{"Ctrl+Alt+Up", "Maximize the selected desktop"},
            {"Ctrl+Alt+Down", "Overview: use any desktop; pills, corners"},
            {"Ctrl+Alt+Left", "Previous desktop"},
            {"Ctrl+Alt+Right", "Next desktop"},
            {"Ctrl+Alt+[ / ]", "Less / more space between desktops"},
            {"Ctrl+Alt+Space", "Recenter the view"},
            {"Ctrl+Alt+V", "Maximized: follow / locked", true},
            {"Ctrl+Alt+P", "Toggle pose prediction"},
            {"Ctrl+Alt+T", "Typing mode: XR keys off / on"},
            {"Ctrl+Alt+D", "Privacy: main display off / on"},
            {"Ctrl+Alt+L", "Placement pill of the selected desktop", true},
            {"Ctrl+Alt+O / U / C", "VR video: file / web link / hide controls"},
            {"Ctrl+Alt+H", "Help; Ctrl+Alt+G/B: window to glasses/back", true},
            {"Ctrl+Alt+PgUp / PgDn", "Help: previous / next page"}};
}
// `n` rows in sections of `section` rows, distinct text per row.
std::vector<HelpRow> many_rows(std::size_t n, std::size_t section = 5) {
    std::vector<HelpRow> rows;
    for (std::size_t i = 0; i < n; ++i)
        rows.push_back({"Ctrl+Alt+" + std::to_string(i % 10), "Example action number " + std::to_string(i + 1), i > 0 && i % section == 0});
    return rows;
}

// Every page of `rows` at w x h: fits, same size, clean pads, every row once (pixel font: one ink band per row).
void check_pages(const std::vector<HelpRow>& rows, const HudFont* f, std::uint32_t w, std::uint32_t h, const std::string& what) {
    const auto fit = help_card_fit(rows, f, w, h);
    if (!fit.fits) std::cerr << what << ": does not fit " << w << "x" << h << "\n";
    CHECK(fit.fits && fit.pages() >= 1);
    std::uint32_t total = 0;
    for (const auto n : fit.page_rows) { CHECK(n >= 1 || rows.empty()); total += n; }
    CHECK(total == rows.size());
    if (fit.pages() > 1) CHECK(fit.row_px == kHelpRowPxMin);  // paging keeps the smallest text; it never shrinks further
    for (std::uint32_t page = 0; page < fit.pages(); ++page) {
        HelpCardControls controls;
        const auto hc = render_help_card(rows, kAccent, f, w, h, page, &controls);
        const auto name = what + " page " + std::to_string(page + 1) + "/" + std::to_string(fit.pages());
        CHECK(hc.width == fit.width && hc.height == fit.height);
        check_fit(hc, w, h, name);
        check_pads_clean(hc, name);
        CHECK(controls.prev.empty() == (fit.pages() == 1) && controls.next.empty() == (fit.pages() == 1));
        if (!f) {
            const std::size_t bands = ink_bands(hc).size(), want = 1 + fit.page_rows[page] + (fit.pages() > 1 ? 2 : 0) + 1;
            if (bands != want) std::cerr << name << ": " << bands << " text bands, want " << want << "\n";
            CHECK(bands == want);  // title, the page's rows, (indicator line, hint), footer: each its own band
        }
        if (fit.pages() > 1) {  // the arrows are drawn inside their click rects, inside the card
            for (const auto* r : {&controls.prev, &controls.indicator, &controls.next}) {
                CHECK(r->x >= 0 && r->y >= 0 && r->x + r->w <= std::int32_t(hc.width) && r->y + r->h <= std::int32_t(hc.height));
                CHECK(ink_in(hc, *r) > 0);
            }
            CHECK(controls.prev.x + controls.prev.w <= controls.next.x);
        }
    }
    // The page number wraps: page `pages` is page 1 again.
    CHECK(render_help_card(rows, kAccent, f, w, h, fit.pages()).rgba == render_help_card(rows, kAccent, f, w, h, 0).rgba);
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
        // The real rows, and three more, on one page at full size at 1920x1200 @1.25.
        auto real = help_rows();
        auto more_rows = real;
        for (int i = 0; i < 3; ++i) more_rows.push_back({"Ctrl+Alt+" + std::to_string(i + 1), "Send the focused window to XR-" + std::to_string(i + 1)});
        for (const auto* rs : {&real, &more_rows}) {
            const auto rows_name = "help card (" + std::to_string(rs->size()) + " rows), " + name;
            const auto fit = help_card_fit(*rs, f, kSurfaceW, kSurfaceH);
            const auto hc = render_help_card(*rs, kAccent, f, kSurfaceW, kSurfaceH);
            std::cout << rows_name << ": " << hc.width << "x" << hc.height << " of " << kSurfaceW << "x" << kSurfaceH << ", row text "
                      << fit.row_px << " px, " << fit.pages() << " page(s)\n";
            CHECK(fit.fits && fit.pages() == 1 && fit.width == hc.width && fit.height == hc.height);
            if (rs == &real) CHECK(fit.row_px == kHelpRowPxMax);  // the real rows keep the roomiest text
            check_pages(*rs, f, kSurfaceW, kSurfaceH, rows_name);
        }
        // Any row count, every scale from 1 to 2: pages, never clipped.
        for (const float scale : kScales) {
            const auto w = std::uint32_t(1920 / scale), h = std::uint32_t(1200 / scale);
            for (const std::size_t n : {std::size_t(1), std::size_t(5), std::size_t(16), std::size_t(24), std::size_t(40), std::size_t(64)})
                check_pages(many_rows(n), f, w, h, std::to_string(n) + " rows at " + std::to_string(w) + "x" + std::to_string(h) + ", " + name);
            check_pages(help_rows(), f, w, h, "real rows at " + std::to_string(w) + "x" + std::to_string(h) + ", " + name);
            const auto fit64 = help_card_fit(many_rows(64), f, w, h);
            if (scale == 2.0f) std::cout << "64 rows at " << w << "x" << h << " (" << name << "): " << fit64.pages() << " pages\n";
            CHECK(fit64.pages() > 1);
            // As wayland.cpp drives it: viewport on configure, re-laid out when it changes.
            HelpOverlay overlay;
            if (f) { std::string why_help; overlay.set_font(HudFont::load(kHudFontFamily, why_help)); }
            overlay.set(true, help_rows(), 0);
            const auto before = overlay.generation();
            CHECK(overlay.set_viewport(w, h) && overlay.generation() > before);
            const auto hc = render_help_card(help_rows(), kAccent, f, w, h);
            CHECK(overlay.image().width == hc.width && overlay.image().height == hc.height);
        }
        // Sections: with room for about one and a half sections per page, every page starts at a section; a section
        // taller than a page is split across pages.
        {
            const auto rows = many_rows(30, 5);
            const auto one = help_card_fit(std::vector<HelpRow>(rows.begin(), rows.begin() + 5), f, 0, 0);
            const auto seven = help_card_fit(std::vector<HelpRow>(rows.begin(), rows.begin() + 7), f, 0, 0);
            // A surface holding a 7-row card (incl. its paged footer, about two lines more) but not 10 rows.
            const std::uint32_t h = seven.height + 2 * kHudFitMargin + 2 * (one.height / 7);
            const auto fit = help_card_fit(rows, f, 1920, h);
            CHECK(fit.fits && fit.pages() > 1);
            std::uint32_t start = 0;
            for (const auto n : fit.page_rows) {
                if (start % 5 != 0) std::cerr << "a page starts mid-section at row " << start << " (" << name << ")\n";
                CHECK(start % 5 == 0);
                start += n;
            }
            const auto split = help_card_fit(many_rows(30, 30), f, 1920, h);  // one 30-row section: split, pages full
            CHECK(split.pages() > 1 && split.page_rows.front() > 5);
        }
        std::uint32_t last_px = kHelpRowPxMax;
        for (std::uint32_t h = 1200; h >= 500; h -= 20) {
            const auto px_now = help_card_fit(help_rows(), f, 1920, h).row_px;
            CHECK(px_now <= last_px && px_now >= kHelpRowPxMin);
            last_px = px_now;
        }
    }
    // HelpOverlay pages: turn, wrap, reopen on page 1; nothing while hidden.
    {
        HelpOverlay overlay;
        const auto rows = many_rows(64);
        CHECK(overlay.set_viewport(960, 600) == false);  // nothing rendered yet (scale 2)
        CHECK(overlay.set(true, rows, 0));
        const auto pages = overlay.pages();
        CHECK(pages > 2 && overlay.page() == 0);
        CHECK(!overlay.controls().next.empty());
        auto generation = overlay.generation();
        CHECK(overlay.set(true, rows, 10, 1) && overlay.page() == 1 && overlay.generation() > generation);
        CHECK(overlay.set(true, rows, 20, -1) && overlay.page() == 0);
        CHECK(overlay.set(true, rows, 30, -1) && overlay.page() == pages - 1);  // wraps backwards
        CHECK(overlay.set(true, rows, 40, 1) && overlay.page() == 0);           // and forwards
        CHECK(overlay.set(true, rows, 50, 1) && overlay.page() == 1 && overlay.turns() == 5);
        CHECK(!overlay.set(true, rows, 60));                                    // same rows, no turn: nothing
        CHECK(overlay.set(false, rows, 70) && !overlay.visible());
        CHECK(!overlay.set(false, rows, 80, 1) && overlay.page() == 1);         // hidden: no turn
        generation = overlay.generation();
        CHECK(overlay.set(true, rows, 90) && overlay.page() == 0 && overlay.generation() > generation);  // reopens on page 1
        const auto one_page = help_rows();
        CHECK(overlay.set(true, one_page, 100) && overlay.pages() == 1 && overlay.page() == 0);  // new rows: redrawn, fewer pages
        CHECK(!overlay.set(true, one_page, 110, 1) && overlay.page() == 0 && overlay.controls().next.empty());
        overlay.set(true, rows, 120); overlay.set(true, rows, 130, -1);
        CHECK(overlay.page() == overlay.pages() - 1);
        CHECK(overlay.set_viewport(1920, 1200) && overlay.page() < overlay.pages());  // a bigger surface: fewer pages
    }
    // help_geometry_json: identity pose puts the card where it is drawn; a turned head moves it the other way.
    {
        const std::array<float, 9> identity{1, 0, 0, 0, 1, 0, 0, 0, 1};
        HelpCardControls c; c.prev = {10, 200, 30, 20}; c.indicator = {50, 200, 60, 20}; c.next = {120, 200, 30, 20};
        const float hw = 0.36f, hh = 0.225f;
        const auto line = help_geometry_json(identity, 1920, 1200, hw, hh, {560, 300}, 800, 600, c);
        std::cout << "help geometry at rest: " << line << "\n";
        CHECK(line == R"({"help_geometry":{"card":[560,300,800,600],"prev":[570,500,30,20],"indicator":[610,500,60,20],"next":[680,500,30,20]}})");
        const auto one = help_geometry_json(identity, 1920, 1200, hw, hh, {560, 300}, 800, 600, HelpCardControls{});
        CHECK(one == R"({"help_geometry":{"card":[560,300,800,600]}})");
        // Head turned (yaw 5 degrees): the head-locked card lies elsewhere on the world-locked canvas.
        const float a = 5.f * float(M_PI) / 180.f;
        const Mat3 yaw = quat_to_mat3({0, std::sin(a / 2), 0, std::cos(a / 2)});
        const auto turned = help_geometry_json(yaw, 1920, 1200, hw, hh, {560, 300}, 800, 600, c);
        std::cout << "help geometry, head yawed: " << turned << "\n";
        CHECK(turned != line && turned.rfind(R"({"help_geometry":{"card":[)", 0) == 0);
        {  // the reported card (a bounding box) drawn through the same projection as the desktops (gl.cpp) is centred on the card
            int x = 0, y = 0, w = 0, h = 0;
            CHECK(std::sscanf(turned.c_str(), R"({"help_geometry":{"card":[%d,%d,%d,%d])", &x, &y, &w, &h) == 4);
            const float cx = float(x) + float(w) / 2, cy = float(y) + float(h) / 2;
            const auto s = project(yaw, 2 * cx / 1920 - 1, 1 - 2 * cy / 1200, hw, hh);
            CHECK(s.has_value());
            if (s) {
                const float sx = ((*s)[0] + 1) / 2 * 1920, sy = (1 - (*s)[1]) / 2 * 1200;
                if (std::fabs(sx - 960) > 8 || std::fabs(sy - 600) > 8) std::cerr << "card centre lands at " << sx << "," << sy << "\n";
                CHECK(std::fabs(sx - 960) <= 8 && std::fabs(sy - 600) <= 8);  // not 470 px off: the inverse is right
            }
        }
        CHECK(help_geometry_json(identity, 0, 1200, hw, hh, {0, 0}, 10, 10, c).empty());
    }
    if (failures) { std::cerr << failures << " help card check(s) failed\n"; return 1; }
    std::cout << "help card: one page at 1.25 (3 rows to spare); 1-64 rows page without clipping at scales 1-2, OK\n";
    return 0;
}
