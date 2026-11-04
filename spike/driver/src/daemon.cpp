#include "luma/daemon.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <thread>

namespace luma {
namespace {
constexpr std::uint64_t kMs = 1'000'000;
std::uint64_t ns(milliseconds ms) { return std::uint64_t(ms.count()) * kMs; }
bool finite(const Quatf& q) { return std::isfinite(q.w) && std::isfinite(q.x) && std::isfinite(q.y) && std::isfinite(q.z); }
milliseconds doubled(milliseconds first, unsigned times, milliseconds cap) {
    auto delay = first;
    for (unsigned i = 0; i < times && delay < cap; ++i) delay *= 2;
    return std::min(delay, cap);
}
}

Daemon::Daemon(DaemonParts parts, DaemonConfig config) : parts_(std::move(parts)), config_(config) {}

void Daemon::logf(const char* format, ...) {
    char line[256];
    std::va_list args;
    va_start(args, format);
    const int n = std::vsnprintf(line, sizeof line, format, args);
    va_end(args);
    if (n > 0) parts_.log(std::string_view(line, std::min<std::size_t>(std::size_t(n), sizeof line - 1)));
}

void Daemon::log_stats() {
    logf("luma: stats samples=%llu published=%llu bad_packets=%llu bad_poses=%llu commands=%llu opens=%llu connections=%llu "
         "disconnects=%llu reconnects=%llu stale=%llu time_jumps=%llu",
         (unsigned long long)stats_.samples, (unsigned long long)stats_.published, (unsigned long long)stats_.bad_packets,
         (unsigned long long)stats_.bad_poses, (unsigned long long)stats_.commands, (unsigned long long)stats_.opens,
         (unsigned long long)stats_.connections, (unsigned long long)stats_.disconnects, (unsigned long long)stats_.reconnects,
         (unsigned long long)stats_.stale_events, (unsigned long long)stats_.time_jumps);
}

void Daemon::set_stale(bool stale) {
    if (stale == stale_) return;
    stale_ = stale;
    parts_.sink.set_stale(stale);
}

void Daemon::new_estimator() {
    estimator_ = parts_.make_estimator();
    ++stats_.fresh_calibrations;
    calibrated_ = announced_ = restored_ = false;
    announce_calibrating_ = true;
}

DaemonEnd Daemon::run(const std::atomic<bool>& stop) {
    parts_.sink.set_stale(true);  // nothing to trust until the first calibrated pose
    stale_ = true;
    new_estimator();
    const std::uint64_t started = parts_.now_ns();
    next_open_ = started;
    next_stats_ = started + ns(config_.stats_every);
    bool ended = false;
    while (!stop.load(std::memory_order_relaxed) && !ended) {
        const std::uint64_t now = parts_.now_ns();
        if (now >= next_stats_) { log_stats(); next_stats_ = now + ns(config_.stats_every); }
        if (up_) {
            const ReadResult r = parts_.link.read(buffer_, config_.read_timeout);
            const std::uint64_t after = parts_.now_ns();
            switch (r.status) {
            case ReadStatus::data: on_packet(std::span<const std::uint8_t>(buffer_).first(std::min(r.size, buffer_.size())), after); break;
            case ReadStatus::timeout: break;
            case ReadStatus::gone: go_down("gone", false, after); break;
            case ReadStatus::end: ended = true; break;
            }
            if (up_ && !ended) watchdog(after);
        } else {
            if (parts_.link.exhausted()) ended = true;
            else connect(now);
        }
    }
    // A stop asks the glasses to stop streaming, once, if they are still there. A replay that ran out has nobody to tell.
    if (up_) {
        if (!ended && !parts_.stop_command.empty()) { ++stats_.commands; parts_.link.write(parts_.stop_command); }
        parts_.link.close();
        up_ = false;
    }
    set_stale(true);
    log_stats();
    logf("luma: stopped reason=%s", ended ? "replay-end" : "signal");
    return ended ? DaemonEnd::replay_ended : DaemonEnd::stop_requested;
}

void Daemon::connect(std::uint64_t now) {
    if (now < next_open_) {
        if (parts_.link.wait_for_device(std::min(milliseconds((next_open_ - now + kMs - 1) / kMs), config_.read_timeout))) next_open_ = now;
        return;
    }
    std::string why;
    ++stats_.opens;
    if (!parts_.link.open(why)) {
        next_open_ = now + ns(doubled(config_.retry_first, retries_, config_.retry_max));
        ++retries_;
        return;
    }
    ++stats_.commands;  // counted even if it fails: it may have reached the wire
    if (!parts_.link.write(parts_.start_command)) {
        // The device is not taking commands: this is a retry of the same kind as a silent one, not a free one.
        parts_.link.close();
        const bool first = !outage_;
        if (first) { outage_ = true; down_since_ = have_time_ ? last_sample_now_ : now; ++stats_.disconnects; logf("luma: disconnected reason=write-failed"); }
        next_open_ = now + ns(doubled(config_.silent_retry_first, silent_retries_, config_.silent_retry_max));
        ++silent_retries_;
        return;
    }
    up_ = true;
    ++stats_.connections;
    connection_has_sample_ = false;
    watch_from_ = now;
    if (!ever_connected_) { ever_connected_ = true; logf("luma: connected"); }
}

void Daemon::go_down(const char* reason, bool silent, std::uint64_t now) {
    parts_.link.close();
    up_ = false;
    if (connection_has_sample_ && watch_from_ - connection_first_sample_ >= ns(config_.healthy_after)) retries_ = silent_retries_ = 0;
    if (!outage_) {
        outage_ = true;
        ++stats_.disconnects;
        down_since_ = connection_has_sample_ ? watch_from_ : now;
        logf("luma: disconnected reason=%s", reason);
    } else if (silent) {
        logf("luma: retry attempt=%u reason=silent", silent_retries_ + 1);
    }
    set_stale(true);
    announced_ = false;
    if (silent) {
        next_open_ = now + ns(doubled(config_.silent_retry_first, silent_retries_, config_.silent_retry_max));
        ++silent_retries_;
    } else {
        next_open_ = now + ns(doubled(config_.retry_first, retries_, config_.retry_max));
        ++retries_;
    }
}

void Daemon::watchdog(std::uint64_t now) {
    // Only a valid sample feeds it: a link that returns nothing but junk is as dead as a silent one.
    const std::uint64_t quiet = now - watch_from_;
    if (!stale_ && quiet >= ns(config_.stale_after)) {
        ++stats_.stale_events;
        logf("luma: stale ms=%llu", (unsigned long long)(quiet / kMs));
        set_stale(true);
    }
    if (quiet >= ns(config_.silent_reopen_after)) go_down("silent", true, now);
}

void Daemon::on_packet(std::span<const std::uint8_t> packet, std::uint64_t now) {
    if (std::optional<ImuSample> sample = parts_.parse(packet)) on_sample(*sample, now);
    else ++stats_.bad_packets;  // also the device's reply to the start command: not worth a log line
}

void Daemon::begin_after_outage(std::uint64_t now) {
    const std::uint64_t gap = now - down_since_;
    const char* calibration = "kept";
    if (!calibrated_) { calibration = "none"; new_estimator(); }
    else if (gap > ns(config_.calibration_keep)) { calibration = "lost"; new_estimator(); }
    else restored_ = true;
    ++stats_.reconnects;
    outage_ = false;
    resume_time_ = true;
    logf("luma: reconnected gap_ms=%llu calibration=%s", (unsigned long long)(gap / kMs), calibration);
}

// The device clock is not ours to trust across a drop: the glasses may restart it from zero, and a stream can resume
// seconds later. What each side needs differs:
//   - the estimator gets time that just carries on by one sample, so a gap is never integrated as one huge step of
//     whatever the gyro says first;
//   - the feed gets true elapsed time on top (published_gap_), never going backwards, so readers age and predict correctly.
// Arithmetic is modular on purpose: offset_ may "go negative".
ImuSample Daemon::continuous_time(ImuSample sample, std::uint64_t now) {
    const std::uint64_t raw = sample.t_ns;
    if (have_time_) {
        const bool jumped = raw <= last_raw_ || raw - last_raw_ > ns(config_.time_jump);
        if (resume_time_ || jumped) {
            if (!resume_time_) ++stats_.time_jumps;
            offset_ = last_continuous_ + nominal_dt_ - raw;
            const std::uint64_t host_gap = now - last_sample_now_;
            published_gap_ += host_gap > nominal_dt_ ? host_gap - nominal_dt_ : 0;
        } else {
            nominal_dt_ = std::clamp<std::uint64_t>(raw - last_raw_, 100'000, 20'000'000);
        }
    } else {
        offset_ = 0;
    }
    resume_time_ = false;
    have_time_ = true;
    last_raw_ = raw;
    last_sample_now_ = now;
    sample.t_ns = last_continuous_ = raw + offset_;
    return sample;
}

void Daemon::on_sample(ImuSample sample, std::uint64_t now) {
    ++stats_.samples;
    const bool was_quiet = stale_ && announced_;
    const std::uint64_t quiet = now - watch_from_;
    watch_from_ = now;
    if (!connection_has_sample_) { connection_has_sample_ = true; connection_first_sample_ = now; }
    if (outage_) begin_after_outage(now);
    if (announce_calibrating_) { announce_calibrating_ = false; logf("luma: calibrating"); }

    PoseOut pose = estimator_->update(continuous_time(sample, now));
    pose.t_ns += published_gap_;
    if (!pose.calibrated) {
        if (calibrated_) {  // the estimator gave up its calibration (a new one is its call, not ours)
            calibrated_ = announced_ = false;
            logf("luma: calibrating");
            set_stale(true);
        }
        return;  // a pose from before calibration would swing the desktops about: the feed holds until it is ready
    }
    calibrated_ = true;
    if (!finite(pose.q) || pose.t_ns <= last_published_t_) { ++stats_.bad_poses; return; }
    if (!announced_) {
        announced_ = true;
        logf("luma: calibrated restored=%d", restored_ ? 1 : 0);
        restored_ = false;
    } else if (was_quiet) {
        logf("luma: fresh gap_ms=%llu", (unsigned long long)(quiet / kMs));
    }
    set_stale(false);
    parts_.sink.publish(pose);
    last_published_t_ = pose.t_ns;
    ++stats_.published;
}

// ---- replay -------------------------------------------------------------------------------------------------------
namespace {
constexpr char kMagic[8] = {'L', 'U', 'M', 'A', 'C', 'A', 'P', '0'};
constexpr std::uint32_t kMaxPayload = 1 << 16;
template <std::size_t N> std::uint64_t little(const unsigned char (&b)[N]) {
    std::uint64_t v = 0;
    for (std::size_t i = N; i-- > 0;) v = v << 8 | b[i];
    return v;
}
}

std::optional<std::vector<CaptureRecord>> load_capture(const std::string& path, std::string& why) {
    std::ifstream in(path, std::ios::binary);
    if (!in) { why = "cannot open " + path; return std::nullopt; }
    char magic[sizeof kMagic]{};
    if (!in.read(magic, sizeof magic) || std::memcmp(magic, kMagic, sizeof magic) != 0) { why = "not a capture: " + path; return std::nullopt; }
    std::vector<CaptureRecord> records;
    for (;;) {
        unsigned char t[8], size[4], kind[1];
        if (!in.read(reinterpret_cast<char*>(t), sizeof t)) {
            if (in.gcount() == 0) return records;
            why = "capture cut short in a record header: " + path;
            return std::nullopt;
        }
        if (!in.read(reinterpret_cast<char*>(size), sizeof size) || !in.read(reinterpret_cast<char*>(kind), sizeof kind)) { why = "capture cut short in a record header: " + path; return std::nullopt; }
        const auto bytes = std::uint32_t(little(size));
        if (bytes > kMaxPayload || kind[0] > std::uint8_t(CaptureKind::device_back)) { why = "capture record invalid: " + path; return std::nullopt; }
        CaptureRecord r{little(t), CaptureKind(kind[0]), std::vector<std::uint8_t>(bytes)};
        if (bytes && !in.read(reinterpret_cast<char*>(r.payload.data()), bytes)) { why = "capture cut short in a payload: " + path; return std::nullopt; }
        if (!records.empty() && r.t_ns < records.back().t_ns) { why = "capture goes back in time: " + path; return std::nullopt; }
        records.push_back(std::move(r));
    }
}

bool save_capture(const std::string& path, const std::vector<CaptureRecord>& records) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(kMagic, sizeof kMagic);
    for (const CaptureRecord& r : records) {
        std::uint64_t t = r.t_ns;
        std::uint32_t size = std::uint32_t(r.payload.size());
        for (int i = 0; i < 8; ++i, t >>= 8) out.put(char(t & 0xff));
        for (int i = 0; i < 4; ++i, size >>= 8) out.put(char(size & 0xff));
        out.put(char(r.kind));
        out.write(reinterpret_cast<const char*>(r.payload.data()), std::streamsize(r.payload.size()));
    }
    return bool(out.flush());
}

ReplayLink::ReplayLink(std::vector<CaptureRecord> records, bool realtime) : records_(std::move(records)), realtime_(realtime) {
    if (!records_.empty()) now_ = records_.front().t_ns;
}
void ReplayLink::advance_to(std::uint64_t t) {
    if (t <= now_) return;
    if (realtime_) std::this_thread::sleep_for(std::chrono::nanoseconds(t - now_));
    now_ = t;
}
void ReplayLink::skip_commands() {
    while (next_ < records_.size() && records_[next_].kind == CaptureKind::to_device) ++next_;
}
bool ReplayLink::exhausted() const { return next_ >= records_.size(); }
bool ReplayLink::open(std::string& why) {
    skip_commands();
    if (absent_ || next_ >= records_.size()) { why = "the capture has the device away"; return false; }
    return true;
}
bool ReplayLink::write(std::span<const std::uint8_t>) { ++commands_; return true; }
ReadResult ReplayLink::read(std::span<std::uint8_t> into, milliseconds timeout) {
    skip_commands();
    if (next_ >= records_.size()) return {ReadStatus::end, 0};
    const CaptureRecord& r = records_[next_];
    const std::uint64_t deadline = now_ + ns(timeout);
    if (r.t_ns > deadline) { advance_to(deadline); return {ReadStatus::timeout, 0}; }
    advance_to(r.t_ns);
    ++next_;
    if (r.kind == CaptureKind::device_gone) { absent_ = true; return {ReadStatus::gone, 0}; }
    if (r.kind == CaptureKind::device_back) return {ReadStatus::timeout, 0};
    const std::size_t n = std::min(r.payload.size(), into.size());
    std::copy_n(r.payload.begin(), n, into.begin());
    return {ReadStatus::data, n};
}
bool ReplayLink::wait_for_device(milliseconds timeout) {
    const std::uint64_t deadline = now_ + ns(timeout);
    while (absent_) {
        skip_commands();
        if (next_ >= records_.size()) break;
        const CaptureRecord& r = records_[next_];
        if (r.t_ns > deadline) break;
        if (r.kind == CaptureKind::device_back) { advance_to(r.t_ns); ++next_; absent_ = false; return true; }
        ++next_;  // anything else recorded while the device was away cannot have come from it
    }
    advance_to(deadline);
    return false;
}

// ---- command line -------------------------------------------------------------------------------------------------
const char* const kUsage =
    "usage: xr-luma-driver [options]\n"
    "  --ipc-prefix PATH        where the pose feed goes (default /tmp/shader_runtime_, what the renderer reads)\n"
    "  --replay FILE            run a capture instead of the glasses; needs --ipc-prefix, touches no USB device\n"
    "  --replay-realtime        replay at recorded speed instead of as fast as it goes\n"
    "  --stale-ms N             no samples this long: mark the feed stale (default 100)\n"
    "  --reopen-ms N            no samples this long: close and reopen the device (default 1000)\n"
    "  --keep-calibration-ms N  back within this long after a drop: keep the calibration (default 5000)\n"
    "  --help\n";

namespace {
bool parse_ms(std::string_view text, milliseconds& out) {
    long value = 0;
    const auto [end, ec] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (ec != std::errc() || end != text.data() + text.size() || value <= 0 || value > 3'600'000) return false;
    out = milliseconds(value);
    return true;
}
}

std::optional<Options> parse_options(std::span<const char* const> args, std::string& why) {
    Options o;
    bool prefix_given = false;
    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string_view arg = args[i];
        const auto value = [&]() -> const char* {
            if (i + 1 >= args.size()) { why = std::string(arg) + " needs a value"; return nullptr; }
            return args[++i];
        };
        const auto number = [&](milliseconds& out) {
            const char* v = value();
            if (!v) return false;
            if (!parse_ms(v, out)) { why = std::string(arg) + " needs a whole number of ms, 1 to 3600000"; return false; }
            return true;
        };
        if (arg == "--help" || arg == "-h") o.help = true;
        else if (arg == "--replay-realtime") o.replay_realtime = true;
        else if (arg == "--ipc-prefix") { const char* v = value(); if (!v) return std::nullopt; o.ipc_prefix = v; prefix_given = true; }
        else if (arg == "--replay") { const char* v = value(); if (!v) return std::nullopt; o.replay = v; }
        else if (arg == "--stale-ms") { if (!number(o.config.stale_after)) return std::nullopt; }
        else if (arg == "--reopen-ms") { if (!number(o.config.silent_reopen_after)) return std::nullopt; }
        else if (arg == "--keep-calibration-ms") { if (!number(o.config.calibration_keep)) return std::nullopt; }
        else { why = "unknown option: " + std::string(arg); return std::nullopt; }
    }
    if (o.ipc_prefix.empty()) { why = "--ipc-prefix must not be empty"; return std::nullopt; }
    if (o.replay && !prefix_given) { why = "--replay needs its own --ipc-prefix: the default is the live feed"; return std::nullopt; }
    if (o.replay && o.ipc_prefix == kLiveIpcPrefix) { why = "--replay must not write the live feed"; return std::nullopt; }
    if (o.replay_realtime && !o.replay) { why = "--replay-realtime needs --replay"; return std::nullopt; }
    if (o.config.silent_reopen_after <= o.config.stale_after) { why = "--reopen-ms must be longer than --stale-ms"; return std::nullopt; }
    return o;
}
}
