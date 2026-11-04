#pragma once
// Where the daemon meets the real pieces. Only src/wiring.cpp knows their APIs.
#include "luma/daemon.hpp"
#include <array>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

namespace luma::wiring {
extern const char* const kStandIn;  // empty: nothing is a stand-in, as acceptance F10 asks

// The gyro bias kept between runs (acceptance C5, C6). The file is read once at start and written once at exit, off the hot
// path. Estimators note their bias here when they are done. A missing or unreadable file means a normal cold start.
class BiasStore {
public:
    explicit BiasStore(std::string path) : path_(std::move(path)) {}
    void load();
    bool save() const;
    const std::optional<std::array<float, 3>>& bias() const { return bias_; }
    void remember(const std::array<float, 3>& bias) { bias_ = bias; }
    const std::string& path() const { return path_; }
private:
    std::string path_;
    std::optional<std::array<float, 3>> bias_;
};
std::string default_state_dir();

std::unique_ptr<Link> live_link(std::string& why, const Options& options = {}, Logger log = {});
// private_segments: create and remove our own segments and never attach to any that exist. A replay must set it: ftok keys
// hold only 16 bits of the inode, so even a private prefix can land on the live feed's key.
std::unique_ptr<Sink> feed_sink(const std::string& ipc_prefix, std::string& why, bool private_segments = false);
// A sink that also logs poses, for --pose-log.
std::unique_ptr<Sink> logging_sink(std::unique_ptr<Sink> inner, const std::string& path, unsigned every, std::string& why);
std::unique_ptr<Estimator> estimator();
std::unique_ptr<Estimator> estimator_with(BiasStore& store);
std::unique_ptr<Decoder> decoder();
std::vector<std::uint8_t> start_command();
std::vector<std::uint8_t> stop_command();
std::vector<std::vector<std::uint8_t>> before_start(Options::Start mode);
std::vector<std::uint8_t> start_for(Options::Start mode);
// A real wire packet for this sample, for tests and fakes.
std::vector<std::uint8_t> stand_in_packet(const ImuSample& sample);
}
