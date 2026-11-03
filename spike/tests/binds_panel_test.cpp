// Key bind panel card (binds_panel.hpp; tools/native_spike/keybinds.py): checked on the rendered pixels with the pixel
// font and (when fontconfig finds it) the installed kHudFontFamily the renderer loads:
//   * at 1920x1200 @1.25 (1536x960 logical) ten rows fit with kHudFitMargin free on every side, and at every scale from 1
//     to 2 the card fits (fewer rows at the smallest text if it must), also during its fade-in drop;
//   * nothing passes the OLED cap (kCardMaxLevel) and every pixel is premultiplied;
//   * the row states use their M3 roles: the picked row's secondary-container band, a conflict's error-container band
//     with its note, a custom bind's primary-container chip, the capture chip's primary outline;
//   * the card's size depends on the conflicts only (not on the picked row or the scroll), and long text never runs into
//     the padding;
//   * bind_panel_first keeps the picked row in a window that moves only when it must, and BindPanelOverlay fades, keeps its
//     content while fading out, redraws only on change and follows the viewport and the theme colour.
#include "xr/binds_panel.hpp"
#include "xr/glass_theme.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(cond) do { if (!(cond)) { ++failures; std::cerr << __FILE__ << ":" << __LINE__ << ": " #cond "\n"; } } while (0)

constexpr std::uint32_t kSurfaceW = 1536, kSurfaceH = 960;  // 1920x1200 at scale 1.25, logical px
constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};
constexpr float kScales[] = {1.0f, 1.25f, 1.5f, 1.6f, 1.75f, 2.0f};

// The panel as keybinds.py sends it at a start with two conflicts, one custom bind, row 4 picked.
BindPanelState sample(std::uint32_t selected = 4, bool capture = false, bool conflicts = true) {
    const char* actions[] = {"Maximize the selected desktop", "Overview", "Previous desktop", "Next desktop",
                             "Less space between desktops", "More space between desktops", "Recenter the view",
                             "Maximized: follow / locked", "Toggle pose prediction", "Typing mode: XR keys off / on",
                             "Privacy: main display off / on", "Placement pill of the selected desktop", "Play a 180/360 VR video",
                             "Play the browser video you copied", "VR video: hide / show playback controls", "Help card (every key)",
                             "Previous help page", "Next help page", "Key binds (this panel)", "Send window to the selected desktop",
                             "Send window to XR-1", "Send window to XR-2", "Send window to XR-3", "Send window to XR-4",
                             "Send window back to the main display"};
    const char* keys[] = {"Ctrl+Alt+Up", "Ctrl+Alt+Down", "Ctrl+Alt+Left", "Ctrl+Alt+Right", "Ctrl+Alt+[", "Ctrl+Alt+]",
                          "Ctrl+Alt+Space", "Ctrl+Alt+V", "Ctrl+Alt+P", "Ctrl+Alt+T", "Ctrl+Alt+D", "Ctrl+Alt+L", "Ctrl+Alt+O",
                          "Ctrl+Alt+U", "Ctrl+Alt+C", "Ctrl+Alt+H", "Ctrl+Alt+PgUp", "Ctrl+Alt+PgDn", "Ctrl+Alt+K", "Ctrl+Alt+G",
                          "Ctrl+Alt+1", "Ctrl+Alt+2", "Ctrl+Alt+3", "Ctrl+Alt+4", "Ctrl+Alt+Shift+Super+bracketright"};
    BindPanelState s;
    s.visible = true;
    s.title = "Key binds";
    s.hint = "Up/Down pick   Enter change   R reset   Shift+R reset all   Esc close";
    s.status = conflicts ? "2 binds with conflicts (marked). Change them, or R to reset." : "No conflicts.";
    s.alert = conflicts;
    for (std::size_t i = 0; i < std::size(actions); ++i) s.rows.push_back({actions[i], keys[i], "", BindRowState::normal});
    s.rows[7].state = BindRowState::custom;
    if (conflicts) {
        s.rows[3] = {"Next desktop", "Ctrl+Alt+Delete", "Off: Hyprland binds Ctrl+Alt+Delete to \"Close all windows\"", BindRowState::conflict};
        s.rows[19] = {"Send window to the selected desktop", "Ctrl+Alt+Tab",
                      "Off: Hyprland binds Ctrl+Alt+Tab to \"Focus on next monitor\", and this note is long enough to be cut",
                      BindRowState::conflict};
    }
    s.selected = selected;
    if (capture) { s.rows[selected].state = BindRowState::capture; s.rows[selected].keys = "Press keys..."; }
    return s;
}

const std::uint8_t* at(const HudImage& im, std::uint32_t x, std::uint32_t y) { return &im.rgba[(std::size_t(y) * im.width + x) * 4]; }
// Pixels of exactly `c` (an opaque fill, +-2 per channel).
int count(const HudImage& im, MdRgb c) {
    int n = 0;
    for (std::uint32_t y = 0; y < im.height; ++y)
        for (std::uint32_t x = 0; x < im.width; ++x) {
            const auto* p = at(im, x, y);
            bool same = p[3] == 255;
            for (int i = 0; i < 3 && same; ++i) same = std::abs(int(p[i]) - int(std::lround(c[std::size_t(i)] * 255))) <= 2;
            n += same;
        }
    return n;
}
void check_cap(const HudImage& im, const std::string& what) {
    int over = 0, unpremultiplied = 0;
    for (std::size_t i = 0; i + 3 < im.rgba.size(); i += 4) {
        for (int c = 0; c < 3; ++c) {
            over += im.rgba[i + std::size_t(c)] > kCardMaxLevel;
            unpremultiplied += im.rgba[i + std::size_t(c)] > im.rgba[i + 3];
        }
    }
    if (over || unpremultiplied) std::cerr << what << ": " << over << " channels over the cap, " << unpremultiplied << " not premultiplied\n";
    CHECK(over == 0 && unpremultiplied == 0);
}
void check_fit(const HudImage& im, std::uint32_t w, std::uint32_t h, const std::string& what) {
    const bool ok = im.width + 2 * kHudFitMargin <= w && im.height + 2 * kHudFitMargin <= h && im.height + std::uint32_t(kHelpRisePx) <= h;
    if (!ok) std::cerr << what << ": card " << im.width << "x" << im.height << " does not fit " << w << "x" << h << "\n";
    CHECK(ok);
}
// The right padding (inside the card's edge, outside every row) holds nothing but the card's own surface.
void check_right_pad(const HudImage& im, const std::string& what) {
    const auto* ref = at(im, im.width - 12, im.height / 2);
    int bad = 0;
    for (std::uint32_t y = 40; y + 40 < im.height; ++y)
        for (std::uint32_t x = im.width - 28; x < im.width - 4; ++x) {
            const auto* p = at(im, x, y);
            for (int i = 0; i < 4; ++i) bad += std::abs(int(p[i]) - int(ref[i])) > 3;
        }
    if (bad) std::cerr << what << ": " << bad << " pixels of ink in the right padding\n";
    CHECK(bad == 0);
}
}

// XR_BINDS_PANEL_DUMP=dir: the sample cards as binary PPMs (composited on black, as the see-through OLED shows black).
void dump(const HudImage& im, const std::string& path) {
    const char* dir = std::getenv("XR_BINDS_PANEL_DUMP");
    if (!dir) return;
    FILE* out = std::fopen((std::string(dir) + "/" + path).c_str(), "wb");
    if (!out) return;
    std::fprintf(out, "P6\n%u %u\n255\n", im.width, im.height);
    for (std::size_t i = 0; i + 3 < im.rgba.size(); i += 4) std::fwrite(&im.rgba[i], 1, 3, out);  // premultiplied: over black
    std::fclose(out);
}

int main() {
    // The scroll window.
    CHECK(bind_panel_first(0, 0, 25, 10) == 0);
    CHECK(bind_panel_first(0, 9, 25, 10) == 0);
    CHECK(bind_panel_first(0, 10, 25, 10) == 1);   // one past the bottom: the window moves one row
    CHECK(bind_panel_first(1, 5, 25, 10) == 1);    // inside the window: it stays
    CHECK(bind_panel_first(11, 4, 25, 10) == 4);   // above it: the picked row becomes the first
    CHECK(bind_panel_first(30, 24, 25, 10) == 15); // never past the last full window
    CHECK(bind_panel_first(7, 3, 5, 10) == 0);     // everything fits: no scrolling
    CHECK(bind_panel_first(3, 2, 25, 0) == 0);

    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    if (!font) std::cout << "note: " << kHudFontFamily << " not available (" << why << "); real-font fit not checked\n";
    std::vector<const HudFont*> fonts{nullptr};
    if (font) fonts.push_back(font.get());
    const auto& m = glass_theme_scheme(kAccent);
    for (const HudFont* f : fonts) {
        const std::string name = f ? "installed font" : "pixel font";
        const auto s = sample();
        const auto fit = bind_panel_fit(s, f, kSurfaceW, kSurfaceH);
        const auto im = render_bind_panel(s, kAccent, f, kSurfaceW, kSurfaceH);
        std::cout << "key bind panel, " << name << ": " << im.width << "x" << im.height << " of " << kSurfaceW << "x" << kSurfaceH
                  << ", row text " << fit.row_px << " px, " << fit.slots << " rows shown\n";
        dump(im, f ? "binds_font.ppm" : "binds_pixel.ppm");
        CHECK(fit.fits && fit.slots == kBindPanelSlots && fit.width == im.width && fit.height == im.height);
        CHECK(fit.row_px >= kBindRowPxMin && fit.row_px <= kBindRowPxMax);
        check_fit(im, kSurfaceW, kSurfaceH, name);
        check_cap(im, name);
        check_right_pad(im, name);
        // Roles: a picked band, error bands for the conflicts in view (row 3; row 19 is scrolled out), a custom chip.
        CHECK(count(im, m.secondary_container) > 1000);
        CHECK(count(im, m.error_container) > 1000);
        CHECK(count(im, m.primary_container) > 100);
        // Without conflicts there is no error colour at all, and the card is shorter by the two note lines (or, with that
        // room, takes roomier text).
        const auto calm = render_bind_panel(sample(4, false, false), kAccent, f, kSurfaceW, kSurfaceH);
        const auto calm_fit = bind_panel_fit(sample(4, false, false), f, kSurfaceW, kSurfaceH);
        CHECK(count(calm, m.error_container) == 0);
        if (calm_fit.row_px == fit.row_px) CHECK(calm.width == im.width && calm.height < im.height);
        else CHECK(calm_fit.row_px > fit.row_px);
        check_cap(calm, name + ", no conflicts");
        // The picked row and the scroll do not change the size; a scrolled window still draws (row 19's long note: cut).
        for (const std::uint32_t sel : {0u, 12u, 19u, 24u}) {
            const auto first = bind_panel_first(0, sel, 25, fit.slots);
            const auto scrolled = render_bind_panel(sample(sel), kAccent, f, kSurfaceW, kSurfaceH, first);
            CHECK(scrolled.width == im.width && scrolled.height == im.height);
            check_cap(scrolled, name + ", picked " + std::to_string(sel));
            check_right_pad(scrolled, name + ", picked " + std::to_string(sel));
        }
        // Waiting for keys: the chip's primary outline.
        const auto capture = render_bind_panel(sample(2, true), kAccent, f, kSurfaceW, kSurfaceH);
        dump(capture, f ? "binds_capture_font.ppm" : "binds_capture_pixel.ppm");
        CHECK(count(capture, m.primary) > count(im, m.primary));
        check_cap(capture, name + ", capture");
        // Every scale from 1 to 2: it fits, with at least the minimum rows.
        for (const float scale : kScales) {
            const auto w = std::uint32_t(1920 / scale), h = std::uint32_t(1200 / scale);
            const auto at_scale = bind_panel_fit(s, f, w, h);
            const auto card = render_bind_panel(s, kAccent, f, w, h);
            if (scale == 2.0f) std::cout << "at " << w << "x" << h << " (" << name << "): " << at_scale.slots << " rows, "
                                         << at_scale.row_px << " px\n";
            CHECK(at_scale.fits && at_scale.slots >= kBindPanelMinSlots);
            check_fit(card, w, h, name + " at " + std::to_string(w) + "x" + std::to_string(h));
            check_cap(card, name + " at scale " + std::to_string(scale));
        }
        // A panel with fewer rows than slots shows them all.
        auto few = sample(1, false, false);
        few.rows.resize(4);
        CHECK(bind_panel_fit(few, f, kSurfaceW, kSurfaceH).slots == 4);
    }
    // The overlay: shown, unchanged, scrolled, hidden (content kept while it fades), re-laid out, recoloured.
    {
        BindPanelOverlay overlay;
        CHECK(!overlay.set_viewport(kSurfaceW, kSurfaceH));  // nothing rendered yet
        CHECK(overlay.set(sample(0), 0) && overlay.visible() && overlay.generation() == 1 && overlay.shown_count() == 1);
        CHECK(!overlay.set(sample(0), 10));                  // same content: nothing
        CHECK(overlay.set(sample(15), 20) && overlay.generation() == 2 && overlay.first() == 15 - overlay.slots() + 1);
        CHECK(overlay.slots() == kBindPanelSlots);
        CHECK(overlay.set(sample(14), 30) && overlay.first() == 15 - overlay.slots() + 1);  // still in the window: it stays
        CHECK(overlay.alpha(30) > 0.f && overlay.alpha(30 + kHelpFadeInMs) == 1.f && overlay.offset_px(30 + kHelpFadeInMs) == 0.f);
        const auto shown = overlay.generation();
        BindPanelState hidden;
        CHECK(overlay.set(hidden, 1000) && !overlay.visible() && overlay.hidden_count() == 1);
        CHECK(overlay.generation() == shown && overlay.image().width > 0);  // fades out with the same card
        CHECK(overlay.animating(1000 + kHelpFadeOutMs / 2) && overlay.alpha(1000 + kHelpFadeOutMs + 1) == 0.f);
        CHECK(overlay.set_viewport(960, 600) && overlay.generation() == shown + 1 && overlay.image().height + 2 * kHudFitMargin <= 600);
        CHECK(overlay.set_accent({0.9f, 0.4f, 0.3f}) && !overlay.set_accent({0.9f, 0.4f, 0.3f}));
        CHECK(overlay.set(sample(14), 2000) && overlay.visible() && overlay.shown_count() == 2);
    }
    if (failures) std::cerr << failures << " failure(s)\n";
    return failures ? 1 : 0;
}
