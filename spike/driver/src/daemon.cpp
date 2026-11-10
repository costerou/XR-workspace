#include "luma/daemon.hpp"
#include "luma/capture_file.hpp"
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
Quatf multiply(const Quatf& a, const Quatf& b) {
    return {a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}
// The heading of the forward axis (body x) in a world with z up. Looking straight up or down there is none.
std::optional<float> heading_of(const Quatf& q) {
    const float forward_x = 1 - 2 * (q.y * q.y + q.z * q.z), forward_y = 2 * (q.x * q.y + q.w * q.z);
    if (std::hypot(forward_x, forward_y) < 0.1f) return std::nullopt;
    return std::atan2(forward_y, forward_x);
}
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
    const std::uint64_t now = parts_.now_ns();
    const std::uint64_t window = now - stats_since_;
    const double rate = window ? double(stats_.samples - stats_samples_) * 1e9 / double(window) : 0;
    logf("luma: stats samples=%llu rate_hz=%.0f lost=%llu repeats=%llu published=%llu bad_packets=%llu bad_poses=%llu commands=%llu "
         "starts_skipped=%llu opens=%llu connections=%llu disconnects=%llu reconnects=%llu stale=%llu time_jumps=%llu max_process_us=%.1f p99_process_us=%.1f",
         (unsigned long long)stats_.samples, rate, (unsigned long long)parts_.decoder.lost_samples(), (unsigned long long)stats_.repeats,
         (unsigned long long)stats_.published,
         (unsigned long long)stats_.bad_packets, (unsigned long long)stats_.bad_poses, (unsigned long long)stats_.commands,
         (unsigned long long)stats_.starts_skipped, (unsigned long long)stats_.opens, (unsigned long long)stats_.connections,
         (unsigned long long)stats_.disconnects, (unsigned long long)stats_.reconnects, (unsigned long long)stats_.stale_events,
         (unsigned long long)stats_.time_jumps, double(stats_.max_process_ns) / 1000.0, double(process_ns_.percentile(0.99)) / 1000.0);
    stats_samples_ = stats_.samples;
    stats_since_ = now;
    stats_.max_process_ns = 0;
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
    announce_calibrating_ = heading_pending_ = true;
}

DaemonEnd Daemon::run(const std::atomic<bool>& stop) {
    parts_.sink.set_stale(true);  // nothing to trust until the first calibrated pose
    stale_ = true;
    new_estimator();
    const std::uint64_t started = parts_.now_ns();
    next_open_ = started;
    next_stats_ = started + ns(config_.stats_every);
    stats_since_ = started;
    bool ended = false;
    while (!stop.load(std::memory_order_relaxed) && !ended) {
        const std::uint64_t now = parts_.now_ns();
        if (now >= next_stats_) { log_stats(); next_stats_ = now + ns(config_.stats_every); }
        if (parts_.on_tick && now >= next_tick_) { parts_.on_tick(now); next_tick_ = now + 100 * kMs; }
        if (up_) {
            const ReadResult r = parts_.link.read(buffer_, config_.read_timeout);
            const std::uint64_t after = parts_.now_ns();
            const std::uint64_t work_began = rt::monotonic_raw_ns();  // a real clock: a replay's now_ns is simulated
            switch (r.status) {
            case ReadStatus::data:
                on_packet(std::span<const std::uint8_t>(buffer_).first(std::min(r.size, buffer_.size())), after);
                {
                    const std::uint64_t spent = rt::monotonic_raw_ns() - work_began;
                    stats_.max_process_ns = std::max(stats_.max_process_ns, spent);
                    process_ns_.record(spent);
                }
                break;
            case ReadStatus::timeout: break;
            case ReadStatus::gone: go_down("gone", false, after); break;
            case ReadStatus::end: ended = true; break;
            }
            if (up_ && !ended && start_pending_ && after >= start_at_) send_start(after);
            if (up_ && !ended && !start_pending_) watchdog(after);
        } else {
            if (parts_.link.exhausted()) ended = true;
            else connect(now);
        }
    }
    // A stop asks the glasses to stop streaming, once, if they are still there. A replay that ran out has nobody to tell.
    if (up_) {
        // Only if we know the link was in use: a stop during the settling wait would be the very traffic that wait avoids.
        if (!ended && !start_pending_ && !parts_.stop_command.empty()) { ++stats_.commands; parts_.link.write(parts_.stop_command); }
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
        device_left_ = true;
        next_open_ = now + ns(doubled(config_.retry_first, retries_, config_.retry_max));
        ++retries_;
        if (why != waiting_for_) {  // a device held by another driver would otherwise look like no device, for ever
            waiting_for_ = why;
            std::string reason = why.empty() ? "unknown" : why;
            std::replace(reason.begin(), reason.end(), ' ', '-');
            logf("luma: waiting reason=%s", reason.c_str());
        }
        return;
    }
    waiting_for_.clear();
    up_ = start_pending_ = true;
    start_sent_ = false;
    before_start_sent_ = 0;
    ++stats_.connections;
    connection_has_sample_ = false;
    watch_from_ = now;
    start_at_ = now + ns(device_left_ ? doubled(config_.settle_first, settle_level_, config_.settle_max) : config_.listen_first);
    device_left_ = false;
    parts_.decoder.new_connection();
    if (!ever_connected_) { ever_connected_ = true; logf("luma: connected"); }
    if (parts_.on_link) parts_.on_link(true, now);
}

void Daemon::send_start(std::uint64_t now) {
    if (before_start_sent_ < parts_.before_start.size()) {
        const auto& command = parts_.before_start[before_start_sent_++];
        start_sent_ = true;
        ++stats_.commands;
        start_at_ = now + ns(config_.start_spacing);
        if (!parts_.link.write(command)) go_down("write-failed", true, now);
        return;
    }
    start_pending_ = false;
    watch_from_ = now;  // the silence that counts starts here
    if (parts_.start_command.empty()) return;  // listening only
    start_sent_ = true;
    ++stats_.commands;  // counted even if it fails: it may have reached the wire
    if (!parts_.link.write(parts_.start_command)) go_down("write-failed", true, now);
}

void Daemon::go_down(const char* reason, bool silent, std::uint64_t now) {
    parts_.link.close();
    up_ = false;
    if (parts_.on_link) parts_.on_link(false, now);
    const bool healthy = connection_has_sample_ && watch_from_ - connection_first_sample_ >= ns(config_.healthy_after);
    if (healthy) retries_ = silent_retries_ = settle_level_ = 0;
    else if (!silent && start_sent_) ++settle_level_;  // it died soon after we spoke to it: speak later next time
    if (!silent) device_left_ = true;
    if (!outage_) {
        outage_ = true;
        ++stats_.disconnects;
        down_since_ = connection_has_sample_ ? watch_from_ : now;
        logf("luma: disconnected reason=%s", reason);
    } else if (silent) {
        logf("luma: retry attempt=%u reason=%s", silent_retries_ + 1, reason);
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
    if (std::optional<ImuSample> sample = parts_.decoder.decode(packet)) on_sample(*sample, now);
    else if (parts_.decoder.last_was_repeat()) ++stats_.repeats;
    else ++stats_.bad_packets;
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

// The device clock is not ours to trust across a drop: the glasses may restart it from zero. Everything downstream gets one
// clock that only runs forward. Across a reconnect or a jump it advances by the time that really passed, so the estimator
// sees the gap as a gap (Fusion then skips that motion and re-acquires gravity, where hiding it would keep it integrating as
// if nothing had happened) and readers of the feed age and predict correctly.
// Arithmetic is modular on purpose: offset_ may "go negative".
ImuSample Daemon::steady_time(ImuSample sample, std::uint64_t now) {
    const std::uint64_t raw = sample.t_ns;
    if (have_time_) {
        const bool jumped = raw <= last_raw_ || raw - last_raw_ > ns(config_.time_jump);
        if (resume_time_ || jumped) {
            if (!resume_time_) ++stats_.time_jumps;
            offset_ = last_steady_ + std::max<std::uint64_t>(now - last_sample_now_, 1) - raw;
        }
    }
    resume_time_ = false;
    have_time_ = true;
    last_raw_ = raw;
    last_sample_now_ = now;
    sample.t_ns = last_steady_ = raw + offset_;
    return sample;
}

// A new estimator's world x is wherever the glasses faced when it calibrated, so without this every desktop would jump to a
// new place. Turning its world about up, by the heading difference, carries on from the last pose published; tilt is left
// alone, as that is true gravity. Whatever the head really did while nothing was measured stays unknown, as with any gap.
void Daemon::carry_heading(PoseOut& pose) {
    if (heading_pending_) {
        heading_pending_ = heading_fixed_ = false;
        const std::optional<float> was = have_last_q_ ? heading_of(last_q_) : std::nullopt, is = heading_of(pose.q);
        if (config_.keep_heading && was && is) {
            const float half = (*was - *is) / 2;
            heading_fix_ = {std::cos(half), 0, 0, std::sin(half)};
            heading_fixed_ = heading_kept_ = true;
        }
    }
    if (heading_fixed_) pose.q = multiply(heading_fix_, pose.q);
}

void Daemon::on_sample(ImuSample sample, std::uint64_t now) {
    ++stats_.samples;
    const bool was_quiet = stale_ && announced_;
    const std::uint64_t quiet = now - watch_from_;
    watch_from_ = now;
    if (!connection_has_sample_) { connection_has_sample_ = true; connection_first_sample_ = now; }
    if (start_pending_) { start_pending_ = false; ++stats_.starts_skipped; }  // the stream is on: no command needed
    if (outage_) begin_after_outage(now);
    if (announce_calibrating_) { announce_calibrating_ = false; logf("luma: calibrating"); }

    const ImuSample steady = steady_time(sample, now);
    PoseOut pose = estimator_->update(steady);
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
    carry_heading(pose);
    if (!announced_) {
        announced_ = true;
        logf("luma: calibrated restored=%d%s", restored_ ? 1 : 0, heading_kept_ ? " heading=kept" : "");
        restored_ = heading_kept_ = false;
    } else if (was_quiet) {
        logf("luma: fresh gap_ms=%llu", (unsigned long long)(quiet / kMs));
    }
    set_stale(false);
    parts_.sink.publish(pose, PoseSource{steady, estimator_->gyro_bias_rad_s(), now});
    last_published_t_ = pose.t_ns;
    last_q_ = pose.q;
    have_last_q_ = true;
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

std::optional<std::vector<CaptureRecord>> load_capture(const std::string& path, std::string& why, std::uint8_t endpoint) {
    std::ifstream in(path, std::ios::binary);
    if (!in) { why = "cannot open " + path; return std::nullopt; }
    char magic[sizeof kMagic]{};
    if (!in.read(magic, sizeof magic)) { why = "not a capture: " + path; return std::nullopt; }
    if (std::memcmp(magic, "LUMACAP1", sizeof magic) == 0) {
        auto events = capture::load_replay(path, &why, endpoint);
        if (!events) return std::nullopt;
        std::vector<CaptureRecord> records;
        records.reserve(events->size());
        for (capture::ReplayEvent& e : *events) records.push_back({e.t_ns, CaptureKind(e.kind), std::move(e.payload)});
        return records;
    }
    if (std::memcmp(magic, kMagic, sizeof magic) != 0) { why = "not a capture: " + path; return std::nullopt; }
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
    return {ReadStatus::data, n, r.t_ns};
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
    "  --keep-calibration-ms N  back within this long after a drop: keep the calibration (default 300000)\n"
    "  --settle-ms N            leave a device that just came back alone this long before any command (default 200)\n"
    "  --no-keep-heading        after a new calibration, start from its own heading instead of the last one\n"
    "  --no-stop-command        leave the stream on at exit (the Ultra has no known stop, so this changes nothing there yet)\n"
    "  --start MODE             listen: send nothing. stream: the start alone (default). prepared: 0x97 then the start.\n"
    "                           full: 0x100B, 0x97, then the start, as VITURE's own software does\n"
    "  --read-events            also read endpoint 0x83, and throw it away\n"
    "  --drain-camera           also read camera endpoint 0x82 and throw it away: 19 MB/s, for a device that stalls without it\n"
    "  --no-rt                  leave scheduling alone\n"
    "  --state-dir PATH         where the gyro bias is kept between runs\n"
    "  --pose-log FILE          replay only: t_ns, then w x y z, as five doubles, for every Nth pose\n"
    "  --pose-log-every N       default 10\n"
    "  --latency-log FILE       host and device time of every IMU packet, live or replay; read it with luma_latency_report\n"
    "  --feature-NAME, --no-feature-NAME  optional features that send nothing; see spike/driver/docs/features.md\n"
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
        else if (arg == "--no-keep-heading") o.config.keep_heading = false;
        else if (arg == "--no-stop-command") o.stop_command = false;
        else if (arg == "--read-events") o.read_events = true;
        else if (arg == "--drain-camera") o.drain_camera = true;
        else if (arg == "--no-rt") o.no_rt = true;
        else if (arg == "--state-dir") { const char* v = value(); if (!v) return std::nullopt; o.state_dir = v; }
        else if (arg == "--pose-log") { const char* v = value(); if (!v) return std::nullopt; o.pose_log = v; }
        else if (arg == "--latency-log") { const char* v = value(); if (!v) return std::nullopt; o.latency_log = v; }
        else if (arg == "--pose-log-every") {
            milliseconds every{};
            if (!number(every)) return std::nullopt;
            o.pose_log_every = unsigned(every.count());
        }
        else if (arg == "--start") {
            const char* v = value();
            if (!v) return std::nullopt;
            const std::string_view mode = v;
            if (mode == "listen") o.start = Options::Start::listen;
            else if (mode == "stream") o.start = Options::Start::stream;
            else if (mode == "prepared") o.start = Options::Start::prepared;
            else if (mode == "full") o.start = Options::Start::full;
            else { why = "--start needs listen, stream, prepared or full"; return std::nullopt; }
        }
        else if (arg == "--ipc-prefix") { const char* v = value(); if (!v) return std::nullopt; o.ipc_prefix = v; prefix_given = true; }
        else if (arg == "--replay") { const char* v = value(); if (!v) return std::nullopt; o.replay = v; }
        else if (arg == "--stale-ms") { if (!number(o.config.stale_after)) return std::nullopt; }
        else if (arg == "--reopen-ms") { if (!number(o.config.silent_reopen_after)) return std::nullopt; }
        else if (arg == "--keep-calibration-ms") { if (!number(o.config.calibration_keep)) return std::nullopt; }
        else if (arg == "--settle-ms") { if (!number(o.config.settle_first)) return std::nullopt; }
        else if (arg.starts_with("--feature-") || arg.starts_with("--no-feature-")) o.feature_flags.emplace_back(arg);
        else { why = "unknown option: " + std::string(arg); return std::nullopt; }
    }
    if (o.ipc_prefix.empty()) { why = "--ipc-prefix must not be empty"; return std::nullopt; }
    if (o.replay && !prefix_given) { why = "--replay needs its own --ipc-prefix: the default is the live feed"; return std::nullopt; }
    if (o.replay && o.ipc_prefix == kLiveIpcPrefix) { why = "--replay must not write the live feed"; return std::nullopt; }
    if (o.replay_realtime && !o.replay) { why = "--replay-realtime needs --replay"; return std::nullopt; }
    if (o.pose_log && !o.replay) { why = "--pose-log needs --replay"; return std::nullopt; }
    o.config.settle_max = std::max(o.config.settle_max, o.config.settle_first);
    if (o.config.silent_reopen_after <= o.config.stale_after) { why = "--reopen-ms must be longer than --stale-ms"; return std::nullopt; }
    return o;
}
}
