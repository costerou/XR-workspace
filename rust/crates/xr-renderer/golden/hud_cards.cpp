// Writes golden/hud_cards.txt from the C++ card rasterisers (spike/src/hud.cpp): render_hud_card (the pop-ups, the tracking
// hint, hand-made lines), render_help_card (every page at several surfaces, with its click targets and fit), and the
// cards StatusPopups, HelpOverlay and TrackingHint hold after a sequence of calls. One line per case and font:
//   "<case> <font> <width> <height> <FNV-1a of the RGBA bytes>[ <extra>]"
// font "pixel" (the built-in 5x7 font) or "real" (kHudFontFamily, when fontconfig finds it; the line "font <description>"
// comes first). tests/hud.rs replays the same cases and must print the same lines; its real-font lines are compared only
// when the Rust HudFont loads the same description (the hashes depend on the font file and FreeType).
// From the repository root, after `cmake -S spike -B $B && cmake --build $B --target xr_hud`:
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/hud_cards.cpp $B/libxr_hud.a $B/libxr_view.a \
//     $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/hud-cards-golden \
//     && /tmp/hud-cards-golden > rust/crates/xr-renderer/golden/hud_cards.txt
#include "xr/hud.hpp"
#include <cstdio>
#include <string>
#include <vector>
using namespace xr;
using Accent = std::array<float, 3>;

static std::uint64_t fnv(const HudImage& im) {
    std::uint64_t h = 0xcbf29ce484222325ull;
    for (auto b : im.rgba) { h ^= b; h *= 0x100000001b3ull; }
    return h;
}
static const char* g_font = "pixel";
static void put(const std::string& name, const HudImage& im, const std::string& extra = "") {
    std::printf("%s %s %u %u %llu%s\n", name.c_str(), g_font, im.width, im.height, (unsigned long long)fnv(im), extra.c_str());
}
static std::string rect(const HelpRect& r) {
    return " " + std::to_string(r.x) + "," + std::to_string(r.y) + "," + std::to_string(r.w) + "," + std::to_string(r.h);
}
static std::vector<HelpRow> help_rows() {  // as spike/tests/help_card_test.cpp
    return {{"Ctrl+Alt+Up", "Maximize the selected desktop"},
            {"Ctrl+Alt+Down", "Overview: any desktop; pills, bar, corners"},
            {"Ctrl+Alt+Left / Right", "Previous / next desktop"},
            {"Ctrl+Alt+[ / ]", "Less / more space between desktops"},
            {"Ctrl+Alt+Space / R", "Recenter / reset to 3 flat desktops"},
            {"Ctrl+Alt+V", "Maximized: follow / locked", true},
            {"Ctrl+Alt+P", "Toggle pose prediction"},
            {"Ctrl+Alt+T", "Typing mode: XR keys off / on"},
            {"Ctrl+Alt+D", "Privacy: main display off / on"},
            {"Ctrl+Alt+E / I", "Background / immersive 3D: on or off"},
            {"Ctrl+Alt+L", "Placement pill; R in it resets, levels", true},
            {"Ctrl+Alt+O / U / C", "VR video: file / web link / hide controls"},
            {"Ctrl+Alt+H / K", "Help/binds; Ctrl+Alt+G/B: window to XR/back", true},
            {"Ctrl+Alt+PgUp / PgDn", "Help: previous / next page"}};
}
static std::vector<HelpRow> many_rows(std::size_t n, std::size_t section) {
    std::vector<HelpRow> rows;
    for (std::size_t i = 0; i < n; ++i)
        rows.push_back({"Ctrl+Alt+" + std::to_string(i % 10), "Example action number " + std::to_string(i + 1), i > 0 && i % section == 0});
    return rows;
}

static void hud_cards(const HudFont* font) {
    const Accent accents[] = {kMdDefaultSeed, {0.5f, 0.6f, 0.9f}, {0.9f, 0.55f, 0.05f}, {1, 1, 1}, {0, 0, 0}};
    for (std::size_t a = 0; a < std::size(accents); ++a) {
        const std::string s = "_a" + std::to_string(a);
        for (std::uint8_t mode = 0; mode < 4; ++mode) put("view_mode" + std::to_string(mode) + s, render_hud_card(view_mode_lines(mode), accents[a], font));
        for (int on = 0; on < 2; ++on) {
            put("cursor" + std::to_string(on) + s, render_hud_card(cursor_follow_lines(on), accents[a], font));
            put("prediction" + std::to_string(on) + s, render_hud_card(prediction_lines(on), accents[a], font));
            put("typing" + std::to_string(on) + s, render_hud_card(typing_lines(on), accents[a], font));
        }
        put("tracking" + s, render_hud_card(tracking_hint_lines(), accents[a], font));
    }
    put("empty", render_hud_card({}, {1, 1, 1}, font));
    put("blank_line", render_hud_card({{"", 1, {0.46f, 0.46f, 0.46f}}}, kMdDefaultSeed, font));
    // Own colours (no role), every scale, px sizes (clamped at 6 and 160), and text the pixel font draws as '?'.
    put("own_colours", render_hud_card({{"VIEW MODE", 2, {1, 1, 1}}, {"FOLLOW", 4, {1, 1, 1}}, {"tiny 1", 1, {0.2f, 0.9f, 0.4f}},
                                        {"Bold 3", 3, {0.9f, 0.1f, 0.6f}, true}, {"huge", 8, {0.5f, 0.5f, 1}}},
                                       {0.5f, 0.6f, 0.9f}, font));
    put("px_sizes", render_hud_card({{"px 2", 1, {1, 1, 1}, false, 2}, {"px 13", 1, {1, 1, 1}, true, 13}, {"px 29", 1, {1, 1, 1}, false, 29},
                                     {"px 300", 1, {1, 1, 1}, false, 300, HudRole::primary}},
                                    kMdDefaultSeed, font));
    put("glyphs", render_hud_card({{"ABCDEFGHIJKLMNOPQRSTUVWXYZ", 1, {1, 1, 1}}, {"abcdefghijklmnopqrstuvwxyz 0123456789", 1, {1, 1, 1}},
                                   {" :-+./[]% ?!@#~,;'\"", 2, {1, 1, 1}, false, 0, HudRole::outline},
                                   {"60\xc2\xb0 caf\xc3\xa9 \xe2\x86\x90\xe2\x86\x92", 2, {1, 1, 1}, true, 0, HudRole::on_secondary_container}},
                                  {0.48f, 0.64f, 0.97f}, font));
    put("roles", render_hud_card({{"on surface", 2, {}, false, 0, HudRole::on_surface}, {"variant", 2, {}, false, 0, HudRole::on_surface_variant},
                                  {"outline", 2, {}, false, 0, HudRole::outline}, {"primary", 2, {}, true, 0, HudRole::primary},
                                  {"on secondary container", 2, {}, false, 0, HudRole::on_secondary_container}},
                                 {0.2f, 0.8f, 0.4f}, font));
}

static void help_cards(const HudFont* font) {
    struct Rows { const char* name; std::vector<HelpRow> rows; };
    const Rows sets[] = {{"none", {}}, {"one", {{"Ctrl+Alt+H", "Show / hide this help"}}}, {"real", help_rows()},
                         {"r16", many_rows(16, 5)}, {"r40", many_rows(40, 5)}, {"r64", many_rows(64, 7)}, {"s30", many_rows(30, 30)},
                         {"wide", {{"Ctrl+Alt+Shift+Super+F12", "An action described in far more words than any surface can hold in one line of text"}}}};
    const std::pair<std::uint32_t, std::uint32_t> surfaces[] = {{0, 0}, {1536, 960}, {1920, 1200}, {960, 600}, {1280, 800}, {1920, 400}, {300, 200}};
    for (const auto& set : sets)
        for (const auto& [w, h] : surfaces) {
            const auto fit = help_card_fit(set.rows, font, w, h);
            std::string pages;
            for (auto n : fit.page_rows) pages += (pages.empty() ? "" : ",") + std::to_string(n);
            const std::string name = std::string("help_") + set.name + "_" + std::to_string(w) + "x" + std::to_string(h);
            std::printf("%s_fit %s %u %u %u %d %s\n", name.c_str(), g_font, fit.width, fit.height, fit.row_px, int(fit.fits), pages.c_str());
            const std::uint32_t shown[] = {0, 1, fit.pages() - 1, fit.pages(), fit.pages() + 1};
            for (std::size_t i = 0; i < std::size(shown); ++i) {
                if (i > 0 && shown[i] == shown[i - 1]) continue;
                if (fit.pages() == 1 && i > 1) break;
                HelpCardControls c;
                const auto im = render_help_card(set.rows, {0.48f, 0.64f, 0.97f}, font, w, h, shown[i], &c);
                put(name + "_p" + std::to_string(shown[i]), im, rect(c.prev) + rect(c.indicator) + rect(c.next));
            }
        }
    put("help_accent_white", render_help_card(help_rows(), {1, 1, 1}, font, 1536, 960));
    put("help_accent_black", render_help_card(help_rows(), {0, 0, 0}, font, 960, 600, 1));
}

// The overlays' cards after each call (the C++ overlays draw them; the Rust ones keep the lines, rows and accent).
static void overlays(const HudFont* font, const std::string& why) {
    auto load = [&]() -> std::unique_ptr<HudFont> {
        if (!font) return nullptr;
        std::string w;
        return HudFont::load(kHudFontFamily, w);
    };
    (void)why;
    {
        StatusPopups p;
        p.set_font(load());
        int step = 0;
        auto shot = [&](bool changed) { put("popups" + std::to_string(step++), p.image(), " " + std::to_string(changed) + " " + std::to_string(p.generation())); };
        shot(p.update(0, false, 1000));
        shot(p.update(0, true, 5000));
        shot(p.update(2, true, 5100));
        shot(p.update(1, false, 9000));
        shot(p.set_accent({0.9f, 0.55f, 0.05f}, 9100));
        shot(p.update(1, false, true, 9200));
        shot(p.update(1, false, true, true, 9300));
        shot(p.update(0, true, false, false, 9400));
        shot(p.set_accent({0.2f, 0.8f, 0.4f}, 99999));  // gone: not redrawn
        shot(p.update(2, true, false, false, 100000));
    }
    {
        StatusPopups t;
        t.set_font(load());
        put("popups_typing_first", (t.update(0, false, true, true, 0), t.image()));
    }
    {
        HelpOverlay h;
        h.set_font(load());
        int step = 0;
        auto shot = [&](bool changed) {
            put("overlay" + std::to_string(step++), h.image(), " " + std::to_string(changed) + " " + std::to_string(h.page()) + " " +
                std::to_string(h.pages()) + rect(h.controls().prev) + rect(h.controls().indicator) + rect(h.controls().next));
        };
        shot(h.set_viewport(960, 600));
        shot(h.set(true, many_rows(40, 5), 0));
        shot(h.set(true, many_rows(40, 5), 10, 1));
        shot(h.set(true, many_rows(40, 5), 20, -1));
        shot(h.set(true, many_rows(40, 5), 30, -1));
        shot(h.set_accent({0.9f, 0.55f, 0.05f}));
        shot(h.set_viewport(1920, 1200));
        shot(h.set(true, help_rows(), 40));
        shot(h.set(false, help_rows(), 50));
        shot(h.set(true, help_rows(), 60));
    }
    {
        TrackingHint t;
        auto own = load();
        if (own) t.set_font(own.get());
        put("tracking_hint", t.image());
        t.set_accent({0.9f, 0.55f, 0.05f});
        put("tracking_hint_accent", t.image());
    }
}

int main() {
    std::string why;
    const auto real = HudFont::load(kHudFontFamily, why);
    for (int pass = 0; pass < 2; ++pass) {
        const HudFont* font = pass ? real.get() : nullptr;
        if (pass && !font) break;
        g_font = pass ? "real" : "pixel";
        if (pass) std::printf("font %s\n", real->description().c_str());
        hud_cards(font);
        help_cards(font);
        overlays(font, why);
    }
}
