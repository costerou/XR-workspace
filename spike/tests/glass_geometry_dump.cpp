// Prints the glass pill and roll bracket geometry constants and a few laid-out pills / panels / brackets as one JSON line,
// for glass_contract_test.py, which checks that tools/native_spike/free_pointer.py (the supervisor's hit testing) mirrors
// them. Brackets: their arm and scale, and probes round both top corners (the distance to the centre line and whether
// the point is in the hit region).
#include "xr/glass.hpp"
#include <algorithm>
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
        {"roll_gap", kRollGap}, {"roll_stroke", kRollStroke}, {"roll_head_len", kRollHeadLen}, {"roll_head_half", kRollHeadHalf},
        {"roll_pad", kRollPad}, {"roll_arm_share", kRollArmShare}, {"roll_arm_min", kRollArmMin}, {"roll_arm_max_share", kRollArmMaxShare},
        {"roll_edge_clear", kRollEdgeClear},
        {"actions_top", kPanelActionsTop}, {"action_h", kPanelActionH}, {"action_w", kPanelActionW}, {"action_gap", kPanelActionGap},
        {"actions", float(kPanelActions)}, {"max_desktops", float(kMaxDesktops)},
        {"scale_top", kPanelScaleTop}, {"scale_item_h", kScaleItemH}, {"scale_list_gap", kScaleListGap},
        {"resolution_top", kPanelResolutionTop}, {"resolution_item_h", kResItemH}, {"resolution_list_gap", kResListGap}};
    bool first = true;
    for (const auto& [name, value] : constants) {
        char b[96]; std::snprintf(b, sizeof b, "%s\"%s\":%.4f", first ? "" : ",", name, double(value)); out += b; first = false;
    }
    out += "},\"actions\":[";
    for (std::size_t a = 0; a < kPanelActions; ++a) out += std::string(a ? "," : "") + rect(glass_panel_action(a));
    out += "],\"scale_box\":" + rect(glass_scale_box()) + ",\"scale_items\":[";
    for (std::size_t i = 0; i < kScaleSteps; ++i) out += std::string(i ? "," : "") + rect(glass_scale_item(i));
    out += "],\"scale_percents\":[";
    for (std::size_t i = 0; i < kScaleSteps; ++i) out += std::string(i ? "," : "") + std::to_string(unsigned(kScalePercents[i]));
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
        char b[96]; std::snprintf(b, sizeof b, "{\"arm\":%.4f,\"scale\":%.4f,\"probes\":[", double(roll_arm(t)), double(roll_scale(t)));
        out += std::string(first ? "" : ",") + "{\"tile\":" + rect(t) + ",\"pill\":" + rect(glass_pill(t)) + ",\"panel\":" + rect(glass_panel(t)) +
               ",\"roll\":" + b;
        // A grid round each top corner, out to past the arms and the hit region, and into the desktop.
        bool first_probe = true;
        const float arm = roll_arm(t), step = std::max(1.f, arm / 9.f);
        for (std::uint32_t side = 0; side < 2; ++side) {
            const float corner = side ? t.x + t.width : t.x, out_dir = side ? 1.f : -1.f;
            for (float u = -arm - 3 * step; u <= 3 * step + 1e-3f; u += step / 2)      // along the top, past the corner
                for (float v = -3 * step; v <= arm + 3 * step + 1e-3f; v += step / 2) {  // down the side
                    const float px = corner + out_dir * u + 0.137f, py = t.y + v - 0.291f;  // off the grid's exact edges
                    const float d = roll_distance(t, side, px, py);
                    char p[160]; std::snprintf(p, sizeof p, "%s[%u,%.4f,%.4f,%.4f,%d]", first_probe ? "" : ",", side, double(px), double(py),
                                               std::isinf(d) ? -1.0 : double(d), on_roll_bracket(t, side, px, py) ? 1 : 0);
                    out += p; first_probe = false;
                }
        }
        out += "]}}";
        first = false;
    }
    std::printf("%s]}\n", out.c_str());
    return 0;
}
