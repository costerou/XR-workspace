// The renderer's own control checks, for bytes the real supervisor sent (tests/native_spike/test_control_wire.py, ctest
// control_wire). Reads everything on stdin; one JSON line per frame refused, then a last line with the totals.
//
// usage: control_wire_check RUN_ID SESSION < frames
#include "xr/ipc.hpp"
#include <iostream>
#include <iterator>
#include <nlohmann/json.hpp>
#include <string>

int main(int argc, char** argv) {
    const bool each = argc == 4 && std::string(argv[3]) == "--each";
    if (argc != 3 && !each) { std::cerr << "usage: control_wire_check RUN_ID SESSION [--each] < frames\n"; return 2; }
    xr::ControlStream stream(argv[1], argv[2], false, [each](xr::ControlMessage& m) {
        if (each) std::cout << nlohmann::json{{"accepted", m.request_id}}.dump() << '\n';
    }, [](const xr::ControlRefusal& r) {
        std::cout << nlohmann::json{{"code", r.error.code}, {"detail", r.error.detail}, {"op", r.op}, {"id", r.request_id}}.dump() << '\n';
    });
    const std::string input{std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>()};
    stream.feed(input);
    std::cout << nlohmann::json{{"accepted", stream.accepted()}, {"refused", stream.refused()}}.dump() << '\n';
    return 0;
}
