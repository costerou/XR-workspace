// Renderer side of the placement-menu contract: decode_control must accept every valid 'placement_menu' payload and
// reject every invalid one. Cases come from tests/native_spike/fixtures/placement_menu_payloads.json, the same file the
// Python reference validator checks, so the two sides cannot drift.
//
// "Rejected" means decode_control returns an Error, or a ControlMessage whose payload is empty (std::monostate): MENU-UI
// decodes a well-framed placement_menu message with a malformed payload that way so the renderer ignores it instead of
// ending the session. Either way the renderer never draws it. Only the existing public API is used, so this compiles
// before MENU-UI lands.
// Until the renderer knows the op, every valid payload is rejected: the test then exits 77, which CMake maps to SKIP.
// Accepting some valid payloads and rejecting others, or accepting any invalid one, is a failure.
#include "xr/ipc.hpp"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <string>
#include <variant>

namespace {
std::string frame(const nlohmann::json& payload, unsigned id) {
    nlohmann::json j = {{"schema", 1}, {"request_id", id}, {"run_id", "run"}, {"session", "session"}, {"op", "placement_menu"},
                        {"payload", payload}};
    return j.dump() + "\n";
}
bool accepted(const std::string& wire) {
    const auto r = xr::decode_control(wire);
    const auto* m = std::get_if<xr::ControlMessage>(&r);
    return m && !std::holds_alternative<std::monostate>(m->payload);
}
}

int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: placement_menu_ipc_test FIXTURE.json\n"; return 2; }
    std::ifstream in(argv[1]);
    if (!in) { std::cerr << "cannot open " << argv[1] << "\n"; return 2; }
    const auto cases = nlohmann::json::parse(in);
    unsigned id = 1, ok = 0, failures = 0;
    const auto& valid = cases.at("valid");
    for (const auto& p : valid) {
        if (accepted(frame(p, id++))) ++ok;
        else std::cerr << "rejected valid payload: " << p.dump() << "\n";
    }
    for (const auto& c : cases.at("invalid")) {
        // Non-object payloads are dumped as-is (null, []); the envelope check must reject them too.
        if (accepted(frame(c.at("payload"), id++))) { ++failures; std::cerr << "accepted invalid payload (" << c.at("why") << ")\n"; }
    }
    if (ok == 0 && failures == 0) {
        std::cout << "SKIP: renderer does not decode op 'placement_menu' yet (needs MENU-UI)\n";
        return 77;
    }
    if (ok != valid.size()) { std::cerr << ok << "/" << valid.size() << " valid payloads accepted\n"; ++failures; }
    // A held slider's "surface" arrives as the placement the open panel is drawn on; without it there is none.
    unsigned surfaces = 0;
    for (const auto& p : valid) {
        const auto r = xr::decode_control(frame(p, id++));
        const auto* m = std::get_if<xr::ControlMessage>(&r);
        const auto* menu = m ? std::get_if<xr::PlacementMenuPayload>(&m->payload) : nullptr;
        if (!menu) continue;
        if (menu->surface.has_value() != p.contains("surface")) { ++failures; std::cerr << "surface lost or invented: " << p.dump() << "\n"; continue; }
        if (!menu->surface) continue;
        ++surfaces;
        const auto& w = *menu->surface; const auto& j = p.at("surface");
        if (j.size() != 4 || w.x != j.at("x") || w.y != j.at("y") || w.distance != j.at("distance") || w.curvature != j.at("curvature")) {
            ++failures; std::cerr << "surface decoded wrong: " << p.dump() << "\n";
        }
    }
    if (ok && surfaces == 0) { ++failures; std::cerr << "the fixture has no valid payload with a surface\n"; }
    // The old head-locked menu's mouse-mode pointer op ("menu_pointer") is gone with the menu card: rejected outright.
    {
        nlohmann::json j = {{"schema", 1}, {"request_id", id++}, {"run_id", "run"}, {"session", "session"}, {"op", "menu_pointer"},
                            {"payload", {{"visible", true}, {"x", 1}, {"y", 2}, {"held", false}}}};
        if (!std::holds_alternative<xr::Error>(xr::decode_control(j.dump() + "\n"))) { ++failures; std::cerr << "menu_pointer still decoded\n"; }
    }
    if (failures) return 1;
    std::cout << "placement_menu: " << ok << " valid accepted, " << cases.at("invalid").size() << " invalid rejected, menu_pointer retired\n";
    return 0;
}
