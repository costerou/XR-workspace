// Prints the glass pill geometry constants and a few laid-out pills / panels as one JSON line, for
// glass_contract_test.py, which checks that tools/native_spike/free_pointer.py (the supervisor's hit testing) mirrors them.
#include "xr/glass.hpp"
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
        {"roll_icon_px", kRollIconPx}, {"roll_icon_gap", kRollIconGap},
        {"actions_top", kPanelActionsTop}, {"action_h", kPanelActionH}, {"action_w", kPanelActionW}, {"action_gap", kPanelActionGap},
        {"actions", float(kPanelActions)}, {"max_desktops", float(kMaxDesktops)}};
    bool first = true;
    for (const auto& [name, value] : constants) {
        char b[96]; std::snprintf(b, sizeof b, "%s\"%s\":%.4f", first ? "" : ",", name, double(value)); out += b; first = false;
    }
    out += "},\"actions\":[";
    for (std::size_t a = 0; a < kPanelActions; ++a) out += std::string(a ? "," : "") + rect(glass_panel_action(a));
    out += "],\"cases\":[";
    const FRect tiles[] = {{100, 300, 459, 258}, {0, 0, 4000, 2000}, {10, 20, 120, 60}, {5, 5, 40, 30}, {-200.5f, 410.25f, 812, 457}};
    first = true;
    for (const auto& t : tiles) {
        out += std::string(first ? "" : ",") + "{\"tile\":" + rect(t) + ",\"pill\":" + rect(glass_pill(t)) + ",\"panel\":" + rect(glass_panel(t)) +
               ",\"roll\":[" + rect(roll_icon(t, 0, 1.f)) + "," + rect(roll_icon(t, 1, 1.f)) + "," + rect(roll_icon(t, 0, 0.5f)) + "," +
               rect(roll_icon(t, 1, 2.f)) + "]}";
        first = false;
    }
    std::printf("%s]}\n", out.c_str());
    return 0;
}
