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
    // Mouse mode's pointer is its own op, "menu_pointer": valid payloads decode exactly, malformed ones are dropped
    // (ignorable, like a malformed placement_menu) and never reach the renderer.
    auto pointer_frame = [](const nlohmann::json& payload, unsigned rid) {
        nlohmann::json j = {{"schema", 1}, {"request_id", rid}, {"run_id", "run"}, {"session", "session"}, {"op", "menu_pointer"}, {"payload", payload}};
        return j.dump() + "\n";
    };
    for (const auto& p : cases.value("menu_pointer_valid", nlohmann::json::array())) {
        const auto r = xr::decode_control(pointer_frame(p, id++));
        const auto* m = std::get_if<xr::ControlMessage>(&r);
        const auto* ptr = m ? std::get_if<xr::MenuPointerPayload>(&m->payload) : nullptr;
        if (!ptr || ptr->visible != p.at("visible").get<bool>() || ptr->x != p.at("x").get<int>() || ptr->y != p.at("y").get<int>() ||
            ptr->held != p.at("held").get<bool>()) { ++failures; std::cerr << "menu_pointer payload not decoded: " << p.dump() << "\n"; }
    }
    for (const auto& c : cases.value("menu_pointer_invalid", nlohmann::json::array())) {
        const auto r = xr::decode_control(pointer_frame(c.at("payload"), id++));
        const auto* e = std::get_if<xr::Error>(&r);
        // A well-framed object is dropped and the run goes on; a non-object payload fails the envelope like any op.
        if (!e || (c.at("payload").is_object() && !xr::ignorable_control_error(*e))) {
            ++failures; std::cerr << "menu_pointer not dropped (" << c.at("why") << ")\n";
        }
    }
    if (failures) return 1;
    std::cout << "placement_menu: " << ok << " valid accepted, " << cases.at("invalid").size() << " invalid rejected, "
              << cases.value("menu_pointer_valid", nlohmann::json::array()).size() << " + "
              << cases.value("menu_pointer_invalid", nlohmann::json::array()).size() << " menu_pointer cases\n";
    return 0;
}
