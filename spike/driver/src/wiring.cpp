// The one file that knows the real Transport, parse_imu, Fusion and Publisher.
#include "luma/wiring.hpp"
#include "luma/features.hpp"
#include "luma/fusion.hpp"
#include "luma/protocol.hpp"
#include "luma/publish.hpp"
#include "luma/transport.hpp"
#include "luma/transport_link.hpp"
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace luma::wiring {
const char* const kStandIn = "";

namespace {
constexpr std::size_t kImuBurst = 8;
using Live = TransportLink<Transport, IoStatus>;

std::string hex_of(std::span<const std::uint8_t> bytes) {
    std::string out;
    char pair[3];
    for (const std::uint8_t b : bytes) {
        std::snprintf(pair, sizeof pair, "%02x", b);
        out += pair;
    }
    return out;
}

class PublishSink final : public Sink {
public:
    explicit PublishSink(std::unique_ptr<Publisher> publisher) : publisher_(std::move(publisher)) {}
    void publish(const PoseOut& pose) override { publisher_->publish(pose); }
    void set_stale(bool stale) override { if (stale) publisher_->pause(); }  // the next publish clears it
private:
    std::unique_ptr<Publisher> publisher_;
};

class PoseLog final : public Sink {
public:
    PoseLog(std::unique_ptr<Sink> inner, std::FILE* file, unsigned every) : inner_(std::move(inner)), file_(file), every_(every ? every : 1) {}
    ~PoseLog() override { std::fclose(file_); }
    void publish(const PoseOut& pose) override {
        inner_->publish(pose);
        if (count_++ % every_) return;
        const double row[5] = {double(pose.t_ns), pose.q.w, pose.q.x, pose.q.y, pose.q.z};
        std::fwrite(row, sizeof row, 1, file_);
    }
    void set_stale(bool stale) override { inner_->set_stale(stale); }
private:
    std::unique_ptr<Sink> inner_;
    std::FILE* file_;
    unsigned every_;
    std::uint64_t count_ = 0;
};

class FusionEstimator final : public Estimator {
public:
    explicit FusionEstimator(FusionConfig config, BiasStore* store = nullptr) : fusion_(config), store_(store) {}
    ~FusionEstimator() override {
        if (!store_) return;
        const FusionStatus s = fusion_.status();
        if (s.quality == FusionQuality::Good || s.quality == FusionQuality::Recovering) store_->remember(s.gyro_bias_rad_s);
    }
    PoseOut update(const ImuSample& sample) override { return fusion_.update(sample); }
private:
    Fusion fusion_;
    BiasStore* store_;
};

class RealDecoder final : public Decoder {
public:
    std::optional<ImuSample> decode(std::span<const std::uint8_t> packet) override {
        repeat_ = false;
        std::optional<ImuSample> sample = parse_imu(packet);
        if (!sample) return std::nullopt;
        temperature_ = luma_temperature_c(packet);
        if (tracker_.observe(*sample).verdict == SequenceTracker::Verdict::accepted) return sample;
        repeat_ = true;
        return std::nullopt;
    }
    void new_connection() override { tracker_.reset(); }
    std::uint64_t lost_samples() const override { return tracker_.lost_total(); }
    bool last_was_repeat() const override { return repeat_; }
    std::optional<float> temperature_c() const override { return temperature_; }
private:
    SequenceTracker tracker_ = luma_sequence_tracker();
    std::optional<float> temperature_;
    bool repeat_ = false;
};
}

struct FeatureHost::Impl {
    features::Manager manager;
    Logger log;
    std::uint64_t next_temperature = 0;
    explicit Impl(features::Config config, Logger logger) : manager(std::move(config)), log(std::move(logger)) {}
    void drain() {
        while (const auto event = manager.pop_event()) {
            std::array<char, 256> line{};
            const std::size_t n = features::format_event(*event, line);
            if (log) log("luma: feature " + std::string(line.data(), n));
        }
    }
};

FeatureHost::FeatureHost() = default;
FeatureHost::~FeatureHost() = default;

std::unique_ptr<FeatureHost> FeatureHost::make(const std::vector<std::string>& flags, Logger log, std::string& why) {
    features::Config config;
    for (const std::string& flag : flags) {
        if (features::parse_flag(config, flag, &why) != features::FlagResult::Ok) {
            if (why.empty()) why = "unknown feature flag " + flag;
            return nullptr;
        }
        const std::string_view name = std::string_view(flag).substr(flag.starts_with("--no-") ? 13 : 10);
        for (const features::FlagInfo& info : features::flag_table()) {
            if (!info.sends_usb || !name.starts_with(info.name) || flag.starts_with("--no-")) continue;
            why = flag + " would send a command, and no capture has shown its id";
            return nullptr;
        }
    }
    std::unique_ptr<FeatureHost> host(new FeatureHost());
    host->impl_ = std::make_unique<Impl>(std::move(config), std::move(log));
    return host;
}

void FeatureHost::link(bool up, std::uint64_t now) {
    if (up) {
        impl_->manager.on_connect(now);
        if (const auto identity = features::find_usb_identity(features::kSysfsUsbDevices, kVitureVendorId, kLumaUltraProductId))
            impl_->manager.on_identity(*identity, now);
    } else {
        impl_->manager.on_disconnect(now);
    }
    impl_->drain();
}

void FeatureHost::tick(std::uint64_t now, std::optional<float> temperature_c) {
    if (temperature_c && now >= impl_->next_temperature) {
        impl_->next_temperature = now + 60'000'000'000ull;
        impl_->manager.on_temperature(*temperature_c, now);
    }
    impl_->manager.tick(now);
    impl_->drain();
}

void BiasStore::load() {
    std::ifstream in(path_);
    std::array<float, 3> b{};
    if (in >> b[0] >> b[1] >> b[2]) {
        for (const float v : b) if (!std::isfinite(v) || std::fabs(v) > 0.35f) return;
        bias_ = b;
    }
}

bool BiasStore::save() const {
    if (!bias_) return false;
    std::error_code ignored;
    std::filesystem::create_directories(std::filesystem::path(path_).parent_path(), ignored);
    const std::string temporary = path_ + ".tmp";
    {
        std::ofstream out(temporary, std::ios::trunc);
        out.precision(9);
        out << (*bias_)[0] << ' ' << (*bias_)[1] << ' ' << (*bias_)[2] << '\n';
        if (!out.flush()) return false;
    }
    return std::rename(temporary.c_str(), path_.c_str()) == 0;
}

std::string default_state_dir() {
    if (const char* state = std::getenv("XDG_STATE_HOME"); state && *state) return std::string(state) + "/viture-workspace";
    if (const char* home = std::getenv("HOME"); home && *home) return std::string(home) + "/.local/state/viture-workspace";
    return {};
}

TransportConfig live_config(const Options& options) {
    TransportConfig config;
    config.in_endpoints = {kLumaImuEndpoint, kMcuEndpointIn};
    config.out_endpoint = kMcuEndpointOut;
    config.allow_mcu_channel = true;
    config.urbs_per_endpoint = kImuBurst;
    if (options.read_events) config.in_endpoints.push_back(kLumaEventEndpoint);
    if (options.drain_camera) config.drain_endpoints.push_back(kLumaCameraEndpoint);
    if (options.start == Options::Start::full) {
        config.extra_out_endpoints.push_back(kDataEndpointOut);
        config.in_endpoints.push_back(kDataEndpointIn);
    }
    return config;
}

std::unique_ptr<Link> link_with(TransportConfig config, std::shared_ptr<Sys> sys, Logger log) {
    auto link = std::make_unique<Live>(std::move(config), std::move(sys));
    link->set_stream_endpoint(kLumaImuEndpoint);
    link->set_route([](std::span<const std::uint8_t> bytes) { return !bytes.empty() && bytes[0] == 0xff ? kDataEndpointOut : kMcuEndpointOut; });
    link->set_other_packet_handler([log = std::move(log)](std::uint8_t endpoint, std::span<const std::uint8_t> bytes) {
        if (endpoint == kLumaEventEndpoint || !log) return;  // 120 of these a second say nothing
        char line[96];
        std::snprintf(line, sizeof line, "luma: reply endpoint=0x%02x bytes=%zu head=", endpoint, bytes.size());
        log(std::string(line) + hex_of(bytes.first(std::min<std::size_t>(bytes.size(), 16))));
    });
    return link;
}

std::unique_ptr<Link> live_link(const Options& options, Logger log) { return link_with(live_config(options), nullptr, std::move(log)); }

std::unique_ptr<Sink> feed_sink(const std::string& ipc_prefix, std::string& why, bool private_segments) {
    PublisherOptions options;
    options.prefix = ipc_prefix;
    options.history_step = history_step_for(1000);
    options.private_segments = private_segments;
    auto publisher = Publisher::open(options, why);
    if (!publisher) return nullptr;
    return std::make_unique<PublishSink>(std::move(publisher));
}

std::unique_ptr<Sink> logging_sink(std::unique_ptr<Sink> inner, const std::string& path, unsigned every, std::string& why) {
    std::FILE* file = std::fopen(path.c_str(), "wb");
    if (!file) {
        why = "cannot write " + path + ": " + std::strerror(errno);
        return nullptr;
    }
    return std::make_unique<PoseLog>(std::move(inner), file, every);
}

std::unique_ptr<Estimator> estimator() { return std::make_unique<FusionEstimator>(FusionConfig{}); }

std::unique_ptr<Estimator> estimator_with(BiasStore& store) {
    FusionConfig config;
    config.warm_start_bias = store.bias();
    return std::make_unique<FusionEstimator>(config, &store);
}

std::unique_ptr<Decoder> decoder() { return std::make_unique<RealDecoder>(); }

std::vector<std::uint8_t> start_command() { return start_imu(Device::luma_ultra)->bytes; }
std::vector<std::uint8_t> stop_command() {
    const auto stop = stop_imu(Device::luma_ultra);
    return stop ? stop->bytes : std::vector<std::uint8_t>{};
}

std::vector<std::vector<std::uint8_t>> before_start(Options::Start mode) {
    std::vector<std::vector<std::uint8_t>> commands;
    if (mode == Options::Start::full) commands.push_back(data_request(0x100B, std::array<std::uint8_t, 1>{0x01})->bytes);
    if (mode == Options::Start::full || mode == Options::Start::prepared) commands.push_back(mcu_request(kMcuBeforeStart)->bytes);
    return commands;
}

std::vector<std::uint8_t> start_for(Options::Start mode) { return mode == Options::Start::listen ? std::vector<std::uint8_t>{} : start_command(); }

std::vector<std::uint8_t> stand_in_packet(const ImuSample& s) {
    const auto packet = encode_luma_imu(s);
    return {packet.begin(), packet.end()};
}
}
