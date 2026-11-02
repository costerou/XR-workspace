// Renderer side of the placement-menu contract: decode_control must accept every valid 'placement_menu' payload and
// reject every invalid one. Cases come from tests/native_spike/fixtures/placement_menu_payloads.json, the same file the
// Python reference validator checks, so the two sides cannot drift.
//
// Uses only the existing public API (decode_control -> ControlMessage | Error), so it compiles before MENU-UI lands.
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
bool accepted(const std::string& wire) { return std::holds_alternative<xr::ControlMessage>(xr::decode_control(wire)); }
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
    if (failures) return 1;
    std::cout << "placement_menu: " << ok << " valid accepted, " << cases.at("invalid").size() << " invalid rejected\n";
    return 0;
}
