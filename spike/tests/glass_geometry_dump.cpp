// Prints the glass pill and level bar geometry constants and a few laid-out pills / panels / bars as one JSON line, for
// glass_contract_test.py, which checks that tools/native_spike/free_pointer.py (the supervisor's hit testing) mirrors them.
// Bars: their scale and rect, and probes all round and across them (the segment at each point).
#include "xr/glass.hpp"
#include <cmath>
#include <cstdio>
#include <string>

using namespace xr;
namespace {
std::string rect(const FRect& r) {
    char b[128]; std::snprintf(b, sizeof b, "[%.4f,%.4f,%.4f,%.4f]", double(r.x), double(r.y), double(r.width), double(r.height)); return b;
}
}

int main() {
    std::string out = "{\"constants\":{";
    const std::pair<const char*, float> constants[] = {
        {"pill_share", kGlassPillShare}, {"pill_min_w", kGlassPillMinW}, {"pill_max_w", kGlassPillMaxW}, {"pill_h", kGlassPillH},
        {"pill_gap", kGlassPillGap}, {"panel_w", kPanelW}, {"panel_h", kPanelH}, {"header_h", kPanelHeaderH},
        {"rows_top", kPanelRowsTop}, {"row_h", kPanelRowH}, {"knob_lo", kPanelKnobLo}, {"knob_hi", kPanelKnobHi},
        {"bar_w", kLevelBarW}, {"bar_h", kLevelBarH}, {"bar_gap", kLevelBarGap}, {"bar_pad", kLevelBarPad},
        {"bar_share", kLevelBarShare}, {"bar_min_w", kLevelBarMinW}, {"edge_clear", kLevelEdgeClear},
        {"actions_top", kPanelActionsTop}, {"action_h", kPanelActionH}, {"action_w", kPanelActionW}, {"action_gap", kPanelActionGap},
        {"actions", float(kPanelActions)}, {"max_desktops", float(kMaxDesktops)},
        {"scale_top", kPanelScaleTop}, {"scale_item_h", kScaleItemH}, {"scale_list_gap", kScaleListGap},
        {"resolution_top", kPanelResolutionTop}, {"resolution_item_h", kResItemH}, {"resolution_list_gap", kResListGap},
        {"background_top", kPanelBackgroundTop}, {"brightness_top", kPanelBrightnessTop}, {"background_item_h", kBackgroundItemH},
        {"background_list_gap", kBackgroundListGap}, {"brightness_min", float(kBrightnessMin)}, {"brightness_max", float(kBrightnessMax)},
        {"brightness_default", float(kBrightnessDefault)}};
    bool first = true;
    for (const auto& [name, value] : constants) {
        char b[96]; std::snprintf(b, sizeof b, "%s\"%s\":%.4f", first ? "" : ",", name, double(value)); out += b; first = false;
    }
    {
        char b[96]; std::snprintf(b, sizeof b, "},\"cap\":{\"gap\":%.4f,\"leg\":%.4f,\"stroke\":%.4f", double(kCapGap), double(kCapLeg), double(kCapStroke));
        out += b;
    }
    out += "},\"actions\":[";
    for (std::size_t a = 0; a < kPanelActions; ++a) out += std::string(a ? "," : "") + rect(glass_panel_action(a));
    out += "],\"scale_box\":" + rect(glass_scale_box()) + ",\"scale_items\":[";
    for (std::size_t i = 0; i < kScaleSteps; ++i) out += std::string(i ? "," : "") + rect(glass_scale_item(i));
    out += "],\"scale_percents\":[";
    for (std::size_t i = 0; i < kScaleSteps; ++i) out += std::string(i ? "," : "") + std::to_string(unsigned(kScalePercents[i]));
    out += "],\"background_box\":" + rect(glass_background_box()) + ",\"background_switch\":" + rect(glass_background_switch()) + ",\"background_items\":[";
    for (std::size_t i = 0; i < kBackgroundCount; ++i) out += std::string(i ? "," : "") + rect(glass_background_item(i));
    out += "],\"backgrounds\":[";
    for (std::size_t i = 0; i < kBackgroundCount; ++i)
        out += std::string(i ? "," : "") + "[\"" + std::string(kBackgrounds[i].name) + "\",\"" + std::string(kBackgrounds[i].label) + "\"]";
    out += "],\"resolution_box\":" + rect(glass_resolution_box()) + ",\"resolution_list\":" + rect(glass_resolution_list()) +
           ",\"resolution_items\":[";
    for (std::size_t i = 0; i < kResolutionCount; ++i) out += std::string(i ? "," : "") + rect(glass_resolution_item(i));
    out += "],\"resolutions\":[";
    for (std::size_t i = 0; i < kResolutionCount; ++i)
        out += std::string(i ? "," : "") + "[" + std::to_string(unsigned(kResolutions[i].width)) + "," +
               std::to_string(unsigned(kResolutions[i].height)) + "]";
    out += "],\"resolution_groups\":[";
    for (std::size_t g = 0; g < kResolutionGroupCount; ++g)
        out += std::string(g ? "," : "") + "[\"" + kResolutionGroups[g].name + "\"," + std::to_string(kResolutionGroups[g].count) + "]";
    out += "],\"cases\":[";
    const FRect tiles[] = {{100, 300, 459, 258}, {0, 0, 4000, 2000}, {10, 20, 120, 60}, {5, 5, 40, 30}, {-200.5f, 410.25f, 812, 457}};
    first = true;
    for (const auto& t : tiles) {
        char b[96]; std::snprintf(b, sizeof b, "{\"scale\":%.4f,\"rect\":", double(level_bar_scale(t)));
        out += std::string(first ? "" : ",") + "{\"tile\":" + rect(t) + ",\"pill\":" + rect(glass_pill(t)) + ",\"panel\":" + rect(glass_panel(t)) +
               ",\"bar\":" + b + rect(level_bar_rect(t)) + ",\"probes\":[";
        // A grid over the bar and its padding, out past them on every side, and down onto the desktop.
        bool first_probe = true;
        const FRect r = level_bar_rect(t);
        const float s = level_bar_scale(t), step_x = r.width / 23.f, step_y = r.height / 7.f;
        for (float u = -3 * step_x; u <= r.width + 3 * step_x + 1e-3f; u += step_x / 2)
            for (float v = -3 * step_y; v <= r.height + (kLevelBarGap + 8) * s + 1e-3f; v += step_y / 2) {
                const float px = r.x + u + 0.137f * s, py = r.y + v - 0.291f * s;  // off the grid's exact edges
                char p[120]; std::snprintf(p, sizeof p, "%s[%.4f,%.4f,%d]", first_probe ? "" : ",", double(px), double(py), int(level_bar_segment(t, px, py)));
                out += p; first_probe = false;
            }
        out += "]}}";
        first = false;
    }
    std::printf("%s]}\n", out.c_str());
    return 0;
}
