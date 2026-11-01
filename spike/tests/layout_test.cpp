// Synthetic offline layouts. Checks remain active in Release (no assert/NDEBUG).
#include "xr/types.hpp"
#include <iostream>
#include <limits>
#include <vector>

bool check(bool condition, const char* message) {
    if (!condition) std::cerr << "FAIL: " << message << '\n';
    return condition;
}

int main() {
    using namespace xr;
    std::vector<OutputSpec> outputs{
        {"71ed078f-d0aa-49a7-b8b2-000000000001", "synthetic-a", 1920, 1080, 120000, 1.0, 0, 0},
        {"71ed078f-d0aa-49a7-b8b2-000000000002", "synthetic-b", 1920, 1080, 120000, 1.0, 1920, 0},
        {"71ed078f-d0aa-49a7-b8b2-000000000003", "synthetic-c", 1920, 1080, 120000, 1.0, 3840, 0}};
    bool ok = check(std::holds_alternative<std::monostate>(validate_layout(outputs)), "valid layout rejected");
    outputs.erase(outputs.begin() + 1);
    ok &= check(std::holds_alternative<std::monostate>(validate_layout(outputs)), "removal invalidated layout");
    ok &= check(outputs[0].id == "71ed078f-d0aa-49a7-b8b2-000000000001" &&
                outputs[1].id == "71ed078f-d0aa-49a7-b8b2-000000000003", "IDs renumbered after middle removal");
    const auto valid = outputs;
    auto rejected = [&] { return std::holds_alternative<Error>(validate_layout(outputs)); };
    outputs[1].id = outputs[0].id;
    ok &= check(rejected(), "duplicate IDs accepted");
    outputs = valid; outputs[0].id.clear();
    ok &= check(rejected(), "empty ID accepted");
    outputs = valid; outputs[0].width = 0;
    ok &= check(rejected(), "zero width accepted");
    outputs = valid; outputs[0].height = 0;
    ok &= check(rejected(), "zero height accepted");
    outputs = valid; outputs[0].refresh_millihz = 0;
    ok &= check(rejected(), "zero refresh accepted");
    for (double scale : {0.0, -1.0, std::numeric_limits<double>::infinity(),
                         std::numeric_limits<double>::quiet_NaN()}) {
        outputs = valid; outputs[0].scale = scale;
        ok &= check(rejected(), "invalid scale accepted");
    }
    outputs = valid; outputs[0].scale = 1.5;
    ok &= check(std::holds_alternative<std::monostate>(validate_layout(outputs)), "fractional scale rejected");
    PoseSample pose{};
    ok &= check(!pose.sample_mono_ns && !pose.device_tick && !pose.valid, "default pose claims mapped clock/validity");
    if (ok) std::cout << "PASS: layout validation, stable IDs, unmapped pose defaults\n";
    return ok ? 0 : 1;
}
