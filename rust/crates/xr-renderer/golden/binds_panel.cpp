// Writes golden/binds_panel.txt from the C++ render_bind_panel (spike/src/binds_panel.cpp): one line per card,
//   <font> <case> <width> <height> <FNV-1a 64 of the premultiplied RGBA bytes>
// font "pixel" is the built-in 5x7 font; "font" is kHudFontFamily as fontconfig finds it (no such lines when it is not
// installed, and they hold only for the font files they were made with). From the repository root, with B a CMake
// build of spike/ that has built the target xr_hud (cmake --build $B --target xr_hud):
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/binds_panel.cpp $B/libxr_hud.a $B/libxr_view.a $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/binds-golden && /tmp/binds-golden > rust/crates/xr-renderer/golden/binds_panel.txt
// tests/binds_panel.rs (pixel_parity_with_cpp) renders the same cases and must give the same lines.
#include "xr/binds_panel.hpp"
#include <cstdio>
#include <string>
#include <vector>
using namespace xr;

// binds_panel_test.cpp's sample: keybinds.py's panel with two conflicts and one custom bind.
static BindPanelState sample(std::uint32_t selected, bool capture, bool conflicts) {
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
// tests/binds_panel.rs parity_sample: n rows of varied length, notes on every third, conflicts on rows whose index mod 7
// is under `conflicts`, custom binds on every fourth from 1, row `selected` capturing when asked.
static BindPanelState parity_sample(std::uint32_t n, std::uint32_t selected, bool capture, int conflicts) {
    BindPanelState s;
    s.visible = true;
    s.title = "Key binds";
    s.hint = "Up/Down pick   Enter change";
    s.status = "ok";
    s.selected = selected;
    for (std::uint32_t i = 0; i < n; ++i) {
        const auto state = conflicts > 0 && int(i % 7) < conflicts ? BindRowState::conflict
                         : i % 4 == 1                           ? BindRowState::custom
                                                                : BindRowState::normal;
        const std::string note = i % 3 ? "" : "Off: conflicts with " + std::to_string(i);
        s.rows.push_back({"Action " + std::to_string(i) + std::string(i % 9 * 4, 'a'), "Ctrl+Alt+" + std::string(1 + i % 5, 'K'),
                          note, state});
    }
    if (capture && selected < n) { s.rows[selected].state = BindRowState::capture; s.rows[selected].keys = "Press keys..."; }
    return s;
}

static void dump(const char* font_name, const HudFont* font, const std::string& what, const BindPanelState& s,
                 std::array<float, 3> accent, std::uint32_t w, std::uint32_t h, std::uint32_t first) {
    const auto im = render_bind_panel(s, accent, font, w, h, first);
    std::uint64_t f = 0xcbf29ce484222325ull;
    for (auto b : im.rgba) { f ^= b; f *= 0x100000001b3ull; }
    std::printf("%s %s %u %u %016llx\n", font_name, what.c_str(), im.width, im.height, (unsigned long long)f);
}

int main() {
    std::string why;
    const auto loaded = HudFont::load(kHudFontFamily, why);
    std::vector<std::pair<const char*, const HudFont*>> fonts{{"pixel", nullptr}};
    if (loaded) fonts.push_back({"font", loaded.get()});
    constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};
    const std::array<float, 3> accents[] = {kAccent, kMdDefaultSeed, {0.9f, 0.4f, 0.3f}, {0.f, 0.f, 0.f}, {1.f, 1.f, 1.f}};
    const std::uint32_t sizes[][2] = {{1536, 960}, {1920, 1200}, {1097, 685}, {960, 600}, {0, 0}, {300, 200}};
    const struct { std::uint32_t selected; bool capture, conflicts; } samples[] = {
        {4, false, true}, {4, false, false}, {0, false, true}, {12, false, true}, {19, false, true}, {24, false, true}, {2, true, true}};
    for (const auto& [name, font] : fonts) {
        for (std::size_t a = 0; a < std::size(accents); ++a)
            dump(name, font, "accent" + std::to_string(a), sample(4, false, true), accents[a], 1536, 960, 0);
        for (const auto& c : samples)
            for (const auto& sz : sizes) {
                const auto what = "sample" + std::to_string(c.selected) + (c.capture ? "c" : "") + (c.conflicts ? "x" : "") + "@" +
                                  std::to_string(sz[0]) + "x" + std::to_string(sz[1]);
                dump(name, font, what, sample(c.selected, c.capture, c.conflicts), kAccent, sz[0], sz[1], c.selected);
            }
        auto alert = sample(4, false, true);
        alert.title = std::string(48, 'W');
        alert.status = "";
        alert.alert = false;
        dump(name, font, "long-title", alert, kAccent, 1536, 960, 0);
        for (std::uint32_t n : {0u, 1u, 4u, 11u, 25u, 64u})
            for (int c : {0, 1, 3, 7})
                for (const auto& sz : {std::array<std::uint32_t, 2>{0, 0}, {1536, 960}, {900, 470}})
                    for (std::uint32_t first : {0u, n / 2}) {
                        const auto what = "parity" + std::to_string(n) + "c" + std::to_string(c) + "@" + std::to_string(sz[0]) +
                                          "x" + std::to_string(sz[1]) + "f" + std::to_string(first);
                        dump(name, font, what, parity_sample(n, n / 2, c == 1, c), kAccent, sz[0], sz[1], first);
                    }
    }
}
