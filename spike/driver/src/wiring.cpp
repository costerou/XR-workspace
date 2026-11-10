// The one file that knows the real Transport, parse_imu, Fusion and Publisher.
#include "luma/wiring.hpp"
#include "luma/features.hpp"
#include "luma/fusion.hpp"
#include "luma/latency_log.hpp"
#include "luma/protocol.hpp"
#include "luma/publish.hpp"
#include "luma/transport.hpp"
#include "luma/transport_link.hpp"
#include <algorithm>
#include <cerrno>
#include <ctime>
#include <fcntl.h>
#include <unistd.h>
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
    void publish(const PoseOut& pose, const PoseSource& source) override {
        publisher_->publish(pose);
        // Every sample, after the pose: the gyro's velocity, one sample old, and its acceleration on PredictionWire.
        predictor_.set_gyro_bias(source.gyro_bias_rad_s);
        predictor_.update(pose, source.sample, source.host_ns);
        publisher_->publish_motion(predictor_.state());
    }
    void set_stale(bool stale) override { if (stale) publisher_->pause(); }  // the next publish clears it
    void publish_vsync(const VsyncState& state) override { publisher_->publish_vsync(state); }
private:
    std::unique_ptr<Publisher> publisher_;
    Predictor predictor_;  // a gap longer than PredictConfig::gap_ms starts it over
};

class PoseLog final : public Sink {
public:
    PoseLog(std::unique_ptr<Sink> inner, std::FILE* file, unsigned every) : inner_(std::move(inner)), file_(file), every_(every ? every : 1) {}
    ~PoseLog() override { std::fclose(file_); }
    void publish(const PoseOut& pose, const PoseSource& source) override {
        inner_->publish(pose, source);
        if (count_++ % every_) return;
        const double row[5] = {double(pose.t_ns), pose.q.w, pose.q.x, pose.q.y, pose.q.z};
        std::fwrite(row, sizeof row, 1, file_);
    }
    void set_stale(bool stale) override { inner_->set_stale(stale); }
    void publish_vsync(const VsyncState& state) override { inner_->publish_vsync(state); }
private:
    std::unique_ptr<Sink> inner_;
    std::FILE* file_;
    unsigned every_;
    std::uint64_t count_ = 0;
};

std::uint64_t monotonic_now_ns() {  // the transport's clock
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}

class LatencyLog final : public Link {
public:
    LatencyLog(std::unique_ptr<Link> inner, int fd, Logger log) : inner_(std::move(inner)), fd_(fd), log_(std::move(log)) {
        pending_.reserve(kBlock);
    }
    ~LatencyLog() override { flush(); ::close(fd_); }
    bool open(std::string& why) override {
        const bool ok = inner_->open(why);
        if (ok) add({.flags = latency::kOpened});
        return ok;
    }
    ReadResult read(std::span<std::uint8_t> into, milliseconds timeout) override {
        const ReadResult r = inner_->read(into, timeout);
        if (r.status == ReadStatus::data)
            if (const auto s = parse_imu(into.first(std::min(r.size, into.size()))))
                add({.host_ns = r.t_ns ? r.t_ns : monotonic_now_ns(), .device_ns = s->t_ns, .seq = s->seq});
        return r;
    }
    bool write(std::span<const std::uint8_t> bytes) override { return inner_->write(bytes); }
    void close() override { inner_->close(); }
    bool wait_for_device(milliseconds timeout) override { return inner_->wait_for_device(timeout); }
    bool exhausted() const override { return inner_->exhausted(); }
private:
    static constexpr std::size_t kBlock = 4096;  // about 4 s at 1 kHz, 96 KiB: one write(2) to the page cache
    void add(const latency::Record& r) {
        if (failed_) return;
        pending_.push_back(r);
        if (pending_.size() >= kBlock) flush();
    }
    void flush() {
        const char* at = reinterpret_cast<const char*>(pending_.data());
        std::size_t left = pending_.size() * sizeof(latency::Record);
        while (left && !failed_) {
            const ssize_t n = ::write(fd_, at, left);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) {
                failed_ = true;
                if (log_) log_(std::string("luma: warning latency-log stopped reason=") + std::strerror(n < 0 ? errno : EIO));
                break;
            }
            at += n;
            left -= std::size_t(n);
        }
        pending_.clear();
    }
    std::unique_ptr<Link> inner_;
    int fd_;
    Logger log_;
    std::vector<latency::Record> pending_;
    bool failed_ = false;
};

class FusionEstimator final : public Estimator {
public:
    explicit FusionEstimator(FusionConfig config, BiasStore* store = nullptr) : fusion_(config), store_(store) {}
    ~FusionEstimator() override { note_bias(); }
    std::array<float, 3> gyro_bias_rad_s() const override { return fusion_.status().gyro_bias_rad_s; }
    PoseOut update(const ImuSample& sample) override {
        const PoseOut pose = fusion_.update(sample);
        if (++samples_ % kNoteEvery == 0) note_bias();  // the driver outlives its XR runs, so waiting for the end means never
        return pose;
    }
private:
    static constexpr std::uint64_t kNoteEvery = 60'000;  // a minute at 1 kHz
    void note_bias() {
        if (!store_) return;
        const FusionStatus s = fusion_.status();
        if (s.quality == FusionQuality::Good) store_->remember(s.gyro_bias_rad_s);
    }
    Fusion fusion_;
    BiasStore* store_;
    std::uint64_t samples_ = 0;
};

class RealDecoder final : public Decoder {
public:
    std::optional<ImuSample> decode(std::span<const std::uint8_t> packet) override {
        repeat_ = false;
        std::optional<ImuSample> sample = parse_imu(packet);
        if (!sample) return std::nullopt;
        // The stream outlives any reader, and the glasses keep the next packet back for whoever reads next, however long that
        // takes. Taken as live it is a false gap in `lost`, a false time jump, and with a saved bias a start tilted to wherever
        // the glasses were then: fusion's first sample sets its attitude. Measured 2026-11-04: 24 degrees off, settling over
        // 10 s. Skipping it costs 1 ms when it happens to be fresh. Capture: docs/capture-findings.md.
        if (first_of_connection_) {
            first_of_connection_ = false;
            repeat_ = true;
            return std::nullopt;
        }
        temperature_ = luma_temperature_c(packet);
        const std::uint32_t ticks = std::uint32_t(sample->t_ns / kLumaTickNs);  // the tracker puts its own time in t_ns
        if (tracker_.observe(*sample).verdict == SequenceTracker::Verdict::accepted) {
            device_ns_ = widen(ticks);
            return sample;
        }
        repeat_ = true;
        return std::nullopt;
    }
    void new_connection() override {
        tracker_.reset();
        first_of_connection_ = true;
        have_ticks_ = false;
        device_ns_.reset();
    }
    std::optional<std::uint64_t> device_ns() const override { return device_ns_; }
    std::optional<std::uint64_t> display_event_ns(std::span<const std::uint8_t> packet) override {
        const std::optional<DisplayEvent> event = parse_display_event(packet);
        if (!event) return std::nullopt;
        return widen(event->ticks);
    }
    std::uint64_t lost_samples() const override { return tracker_.lost_total(); }
    bool last_was_repeat() const override { return repeat_; }
    std::optional<float> temperature_c() const override { return temperature_; }
private:
    // The 32-bit 100 us count wraps every five days; IMU and events share it, and arrive within far less than half that apart.
    std::uint64_t widen(std::uint32_t ticks) {
        if (!have_ticks_) { have_ticks_ = true; wide_ticks_ = ticks; }
        else wide_ticks_ += std::uint64_t(std::int64_t(std::int32_t(ticks - std::uint32_t(wide_ticks_))));
        return wide_ticks_ * kLumaTickNs;
    }
    SequenceTracker tracker_ = luma_sequence_tracker();
    std::optional<float> temperature_;
    std::optional<std::uint64_t> device_ns_;
    std::uint64_t wide_ticks_ = 0;
    bool have_ticks_ = false;
    bool repeat_ = false, first_of_connection_ = true;
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
    const bool events = std::find(config.in_endpoints.begin(), config.in_endpoints.end(), kLumaEventEndpoint) != config.in_endpoints.end();
    auto link = std::make_unique<Live>(std::move(config), std::move(sys));
    link->set_stream_endpoint(kLumaImuEndpoint);
    if (events)
        link->add_stream_endpoint(kLumaEventEndpoint);  // --read-events: the display events reach the daemon (vsync.hpp)
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

std::unique_ptr<Sink> feed_sink(const std::string& ipc_prefix, std::string& why, bool private_segments, bool vsync,
                                const std::string& seqlock_name) {
    PublisherOptions options;
    options.vsync = vsync;
    options.prefix = ipc_prefix;
    options.seqlock_name = seqlock_name;
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

std::unique_ptr<Link> latency_log_link(std::unique_ptr<Link> inner, const std::string& path, Logger log, std::string& why) {
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) { why = "cannot write " + path + ": " + std::strerror(errno); return nullptr; }
    const latency::Header header{};
    if (::write(fd, &header, sizeof header) != ssize_t(sizeof header)) {
        why = "cannot write " + path + ": " + std::strerror(errno);
        ::close(fd);
        return nullptr;
    }
    return std::make_unique<LatencyLog>(std::move(inner), fd, std::move(log));
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
