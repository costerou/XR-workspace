// Prints the placement menu's real geometry status lines (hud.cpp menu_geometry_json), one per font and output size,
// each followed by the card size and the knob centre of every slider at both bounds. menu_geometry_contract_test.py
// feeds them to the supervisor's parser (tools/native_spike/view.py parse_menu_geometry), so the two sides of the
// renderer -> supervisor status line cannot drift apart.
#include "xr/hud.hpp"
#include <iostream>
#include <string>

int main() {
    using namespace xr;
    std::string why;
    const auto font = HudFont::load(kHudFontFamily, why);
    for (const HudFont* f : {static_cast<const HudFont*>(nullptr), static_cast<const HudFont*>(font.get())}) {
        for (auto [w, h] : {std::pair<std::uint32_t, std::uint32_t>{1536, 960}, {1280, 720}, {960, 540}}) {
            PlacementMenuState st; st.open = true; st.field = 4;
            const auto g = placement_menu_geometry(st, f, w, h);
            const auto card = render_placement_menu_card(st, {0.36f, 0.48f, 0.73f}, f, w, h);
            std::cout << menu_geometry_json(g) << "\t" << card.width << "\t" << card.height << "\n";
        }
        if (!font) break;
    }
}
