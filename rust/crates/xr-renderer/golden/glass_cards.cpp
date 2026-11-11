// Writes golden/glass_cards.txt from the C++ glass rasterisers (spike/src/glass.cpp): render_glass_panel,
// render_options_panel and render_handle_atlas. One line per case and font,
// "<case> <font> <width> <height> <FNV-1a of the RGBA bytes>", font "pixel" (the built-in 5x7 font) or "real"
// (kHudFontFamily, when fontconfig finds it). tests/card_raster.rs replays the same cases and must print the same lines.
// From the repository root, after `cmake -S spike -B $B && cmake --build $B --target xr_hud`:
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/glass_cards.cpp $B/libxr_hud.a $B/libxr_view.a \
//     $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/glass-cards-golden \
//     && /tmp/glass-cards-golden > rust/crates/xr-renderer/golden/glass_cards.txt
#include "xr/glass.hpp"
#include "xr/hud.hpp"
#include "xr/ipc.hpp"
#include <cstdio>
#include <string>
#include <vector>
using namespace xr;
namespace {
std::uint64_t fnv(const HudImage& im) {
    std::uint64_t h = 0xcbf29ce484222325ull;
    for (auto b : im.rgba) { h ^= b; h *= 0x100000001b3ull; }
    return h;
}
void line(const std::string& name, bool real, const HudImage& im) {
    std::printf("%s %s %u %u %llu\n", name.c_str(), real ? "real" : "pixel", im.width, im.height, (unsigned long long)fnv(im));
}
struct PanelCase { const char* name; PlacementMenuState s; std::array<float, 3> accent; };
std::vector<PanelCase> panel_cases() {
    std::vector<PanelCase> out;
    PlacementMenuState base;
    base.open = true;
    out.push_back({"panel_default", base, {}});
    auto with = [&](const char* name, auto edit, std::array<float, 3> accent = {}) {
        PlacementMenuState s = base; edit(s); out.push_back({name, s, accent});
    };
    with("panel_values", [](PlacementMenuState& s) { s.desktop = 1; s.field = 1; s.placements[1] = {-455, 123, 140, 45}; });
    with("panel_field2", [](PlacementMenuState& s) { s.field = 2; s.placements[0] = {1800, -900, 10, 120}; });
    with("panel_field3", [](PlacementMenuState& s) { s.field = 3; s.placements[0] = {5000, -5000, 999, -7}; });
    with("panel_nofield", [](PlacementMenuState& s) { s.field = 9; s.desktop = 7; s.numbers = {4, 9, 2, 200}; });
    with("panel_brightness", [](PlacementMenuState& s) { s.field = kPlacementMenuBrightness; s.background = 1; s.brightness = 37; });
    with("panel_smoke", [](PlacementMenuState& s) { s.background = 2; s.brightness = 200; });
    with("panel_bg_clamped", [](PlacementMenuState& s) { s.background = 9; s.brightness = 5; });
    with("panel_wide", [](PlacementMenuState& s) { s.wide = true; s.count = 4; });
    with("panel_armed", [](PlacementMenuState& s) { s.armed = true; s.count = 1; });
    with("panel_busy", [](PlacementMenuState& s) { s.busy = true; s.scale = 150; s.resolution_w = 1920; s.resolution_h = 1080; });
    with("panel_scale", [](PlacementMenuState& s) { s.scale = 125; s.scales = 0x7f; });
    with("panel_scale_picker", [](PlacementMenuState& s) { s.scale = 150; s.scales = 0x2d; s.picker = true; }, {0.9f, 0.3f, 0.2f});
    with("panel_scale_picker_busy", [](PlacementMenuState& s) { s.scale = 300; s.scales = 0x7f; s.picker = true; s.busy = true; });
    with("panel_res", [](PlacementMenuState& s) { s.resolution_w = 2560; s.resolution_h = 1440; });
    with("panel_res_picker", [](PlacementMenuState& s) { s.resolution_w = 3440; s.resolution_h = 1440; s.resolution_picker = true; s.field = 0; });
    with("panel_res_picker_odd", [](PlacementMenuState& s) { s.resolution_w = 1234; s.resolution_h = 567; s.resolution_picker = true; s.busy = true; });
    with("panel_bg_picker", [](PlacementMenuState& s) { s.background = 1; s.background_picker = true; }, {0.48f, 0.64f, 0.97f});
    with("panel_accent", [](PlacementMenuState& s) { s.placements[0] = {300, 200, 80, 30}; }, {0.1f, 0.8f, 0.3f});
    return out;
}
OptionsRow row(std::string id, std::string label, std::string kind, std::string value, std::string unit = "", double lo = 0,
               double hi = 0, bool enabled = true) {
    OptionsRow r;
    r.id = std::move(id); r.label = std::move(label); r.scope = "monitor"; r.kind = std::move(kind); r.value = std::move(value);
    r.unit = std::move(unit); r.minimum = lo; r.maximum = hi; r.step = 1; r.enabled = enabled;
    return r;
}
struct OptionsCase { std::string name; OptionsMenuState s; std::array<float, 3> accent; };
std::vector<OptionsCase> options_cases() {
    std::vector<OptionsCase> out;
    OptionsMenuState empty;
    out.push_back({"options_empty", empty, {.45f, .65f, .8f}});
    OptionsMenuState base;
    base.open = true; base.monitor = "XR-1"; base.scope = "monitor"; base.category = "3d"; base.status = "idle";
    base.rows = {
        row("placement_x", "Left / right", "range", "-455", "0.1 deg", -1800, 1800),
        row("placement_y", "Down / up", "range", "3", "0.1 deg", -900, 900),
        row("curve", "Curvature", "range", "45", "deg", 0, 120),
        row("distance", "Distance", "range", "140", "%", 10, 200),
        row("depth", "Depth", "toggle", "true"),
        row("stereo", "Stereo", "toggle", "false", "", 0, 0, false),
        row("scale", "Scale", "choice", "1.5"),
        row("filter", "Texture filter", "choice", "catmull rom"),
        row("resolution", "Resolution", "choice", "1920x1080"),
        row("remove_monitor", "Remove this monitor", "action", ""),
        row("version", "Version", "readonly", "0.9.1"),
    };
    out.push_back({"options_base", base, {.45f, .65f, .8f}});
    for (int focus : {-4, -3, -2, -1, 0, 2, 4, 6, 9}) {
        auto s = base; s.focus = focus; out.push_back({"options_focus" + std::to_string(focus), s, {.45f, .65f, .8f}});
    }
    { auto s = base; s.scroll = 57; s.focus = 3; out.push_back({"options_scroll57", s, {.45f, .65f, .8f}}); }
    { auto s = base; s.scroll = 114; out.push_back({"options_scroll_end", s, {.9f, .3f, .2f}}); }
    { auto s = base; s.scroll = 5000; out.push_back({"options_scroll_far", s, {.45f, .65f, .8f}}); }
    { auto s = base; s.scope = "workspace"; s.status = "applied"; s.message = "Saved"; s.category = "windows";
      out.push_back({"options_workspace", s, {.45f, .65f, .8f}}); }
    { auto s = base; s.status = "pending"; s.category = "session"; s.monitor = "a-very-long-monitor-name";
      out.push_back({"options_pending", s, {.48f, .64f, .97f}}); }
    { auto s = base; s.status = "error"; s.category = "nope"; s.message = "the backend refused this change because it is far too long to fit";
      out.push_back({"options_error", s, {.45f, .65f, .8f}}); }
    { auto s = base; s.monitor = ""; s.category = "privacy"; s.message = "Hello"; out.push_back({"options_message", s, {.45f, .65f, .8f}}); }
    {
        auto s = base; s.category = "video"; s.focus = 1;
        s.rows = {
            row("a", "Bad number", "range", "abc", "mm", 0, 10),
            row("b", "Leading space", "range", "  2.5e0xyz", "", 0, 10),
            row("c", "Ties", "range", "0.125", "", 0, 1),
            row("d", "No unit tie", "range", "-0.001", "", -1, 1),
            row("placement_x", "Ahead", "range", "0.2", "0.1 deg", -1800, 1800),
            row("placement_y", "Up", "range", "250", "0.1 deg", -900, 900),
            row("roll", "Roll", "range", "-37", "0.1 deg", -900, 900),
            row("flat", "Equal ends", "range", "5", "%", 3, 3),
            row("big", "Huge", "range", "1e400", "", 0, 1),
            row("inf", "Infinity", "range", "-Infinity", "deg", -1, 1),
            row("hex", "Hex", "range", "0x1.8p1", "", 0, 10),
            row("nan", "NaN", "range", "nan", "%", 0, 10),
        };
        out.push_back({"options_ranges", s, {.45f, .65f, .8f}});
    }
    {
        auto s = base; s.category = "input"; s.focus = 5;
        s.rows = {
            row("t", "Toggle odd", "toggle", "maybe"),
            row("scale", "Scale bad", "choice", "big"),
            row("scale", "Scale 2", "choice", "2"),
            row("m", "Mixed", "choice", "Mixed-Case", "px"),
            row("x", "Sizes", "choice", "x1080"),
            row("y", "Sizes 2", "choice", "1920x"),
            row("z", "Sizes 3", "choice", "12x34x5"),
            row("stop", "Stop the session", "action", ""),
            row("layout_reset", "Reset layout", "action", "now"),
            row("menu_reset", "A label that is far too long for the room it is given in a row", "action", "", "", 0, 0, false),
            row("ro", "Read only with a value that will not fit in the space", "readonly", "a value long enough to be cut short here"),
            row("u", "Unit", "readonly", "", "ms"),
            row("v", "Unicode \xC3\xA9t\xC3\xA9", "choice", "caf\xC3\xA9"),
        };
        out.push_back({"options_kinds", s, {.2f, .7f, .5f}});
    }
    return out;
}
}  // namespace
int main() {
    std::string why;
    const auto real = HudFont::load(kHudFontFamily, why);
    const auto panels = panel_cases();
    const auto options = options_cases();
    const std::array<float, 3> atlas_accents[] = {{.45f, .65f, .8f}, {.9f, .2f, .1f}, {.1f, .5f, .9f}};
    for (int pass = 0; pass < 2; ++pass) {
        const HudFont* font = pass ? real.get() : nullptr;
        if (pass && !font) break;
        for (const auto& c : panels) line(c.name, pass, render_glass_panel(c.s, font, c.accent));
        for (const auto& c : options) line(c.name, pass, render_options_panel(c.s, font, c.accent));
        for (std::size_t i = 0; i < std::size(atlas_accents); ++i)
            line("atlas" + std::to_string(i), pass, render_handle_atlas(atlas_accents[i], font));
    }
}
