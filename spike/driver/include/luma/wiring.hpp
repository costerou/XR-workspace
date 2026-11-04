#pragma once
// Where the daemon meets the real pieces. Only src/wiring.cpp knows their APIs. Until they are on master it holds
// stand-ins, kStandIn says which, and the binary prints a `luma: warning` for each.
#include "luma/daemon.hpp"
#include <memory>
#include <string>

namespace luma::wiring {
extern const char* const kStandIn;  // empty once nothing is a stand-in
std::unique_ptr<Link> live_link(std::string& why);  // nullptr: no transport (why says so)
std::unique_ptr<Sink> feed_sink(const std::string& ipc_prefix, std::string& why);
std::unique_ptr<Estimator> estimator();
std::unique_ptr<Decoder> decoder();
std::vector<std::uint8_t> start_command();
std::vector<std::uint8_t> stop_command();
std::vector<std::uint8_t> stand_in_packet(const ImuSample& sample);  // what the stand-in parser reads; tests build captures with it
}
