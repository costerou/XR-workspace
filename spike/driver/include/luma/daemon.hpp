#pragma once
// The driver's daemon: bytes from the glasses in, pose feed out, and what happens when the USB link misbehaves.
//
// It knows the other pieces only through the small interfaces below, so every failure can be replayed against fakes with
// a simulated clock (tests/daemon_test.cpp). src/wiring.cpp is the one file that plugs the real Transport, parse_imu,
// Fusion and Publisher into them.
//
// Why the command traffic is so strict: XRLinuxDriver's sessions drop the glasses off USB while its driver is talking to
// them (`get_display_mode ... returned -7`, `Failed to send USB command - MsgID: 0x0007`, then `USB disconnect`), and as a
// plain display they never drop. So the daemon sends the start command once per connection and nothing else while it runs:
// no polling, no keep-alive, no display-mode query, and never a device reset. A retry against a silent device is itself a
// command, so those back off to one every 9 s (Stats::commands counts them; the `luma: stats` line shows it).
//
// stderr lines, one event per line, `luma: <event> key=value ...`:
//   luma: connected                         the device is open and the start command went out (first time only)
//   luma: calibrating                       a new calibration began: hold the glasses still
//   luma: calibrated restored=0|1           the pose is usable; restored=1: a reconnect kept the earlier calibration
//   luma: stale ms=N                        the link is up but no samples for N ms; the feed is marked disabled
//   luma: fresh gap_ms=N                    samples came back on the same connection
//   luma: disconnected reason=gone|silent|write-failed
//   luma: retry attempt=N reason=silent     another try at a device that opens but stays silent
//   luma: reconnected gap_ms=N calibration=kept|lost|none
//   luma: stats samples=N ... commands=N    once a minute and at exit
//   luma: stopped reason=signal|replay-end
// Readiness is "the last `calibrated` comes after the last `disconnected` and `calibrating`", as tracking.py reads it:
// that is why a kept calibration logs `calibrated restored=1` again after its `reconnected`.
#include "luma/types.hpp"
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace luma {
using std::chrono::milliseconds;

enum class ReadStatus {
    data,     // size bytes arrived
    timeout,  // nothing in time, or a signal cut the wait short
    gone,     // the device went away (read error or removal)
    end,      // a replay ran out; the live device never says this
};
struct ReadResult { ReadStatus status = ReadStatus::timeout; std::size_t size = 0; };

// The USB side. open() must not send anything to the device: the daemon decides what goes out.
class Link {
public:
    virtual ~Link() = default;
    virtual bool open(std::string& why) = 0;
    virtual ReadResult read(std::span<std::uint8_t> into, milliseconds timeout) = 0;
    virtual bool write(std::span<const std::uint8_t> bytes) = 0;
    virtual void close() = 0;
    // Wait at most `timeout` for the device to appear. True: it did (a hotplug event), so try now, whatever the back-off says.
    virtual bool wait_for_device(milliseconds timeout) = 0;
    virtual bool exhausted() const { return false; }  // a replay with nothing left: stop waiting for the device
};

class Estimator {
public:
    virtual ~Estimator() = default;
    virtual PoseOut update(const ImuSample& sample) = 0;
};

class Sink {
public:
    virtual ~Sink() = default;
    virtual void publish(const PoseOut& pose) = 0;
    // Stale: readers must not trust the feed (the `disabled` flag of XRLinuxDriver's layout; spike/src/pose.cpp then holds
    // the last good pose).
    virtual void set_stale(bool stale) = 0;
};

using Parser = std::optional<ImuSample> (*)(std::span<const std::uint8_t>);
using Logger = std::function<void(std::string_view)>;

struct DaemonConfig {
    milliseconds read_timeout{20};           // also how soon a stop request is noticed
    milliseconds stale_after{100};           // matches PoseState's default in spike/include/xr/pose.hpp
    milliseconds silent_reopen_after{1000};  // no samples this long on an open link: close and try again
    milliseconds retry_first{50}, retry_max{500};           // device gone: opening costs no USB traffic
    milliseconds silent_retry_first{500}, silent_retry_max{8000};  // device silent: each try sends a command
    milliseconds calibration_keep{5000};     // back within this long: same calibration, same orientation
    milliseconds healthy_after{2000};        // a connection this long ends the back-off; a flapping one does not
    milliseconds time_jump{500};             // device time stepping further than this is a new time base
    milliseconds stats_every{60000};
};

struct Stats {
    std::uint64_t samples = 0, bad_packets = 0, bad_poses = 0, published = 0;
    std::uint64_t commands = 0, opens = 0, connections = 0, disconnects = 0, reconnects = 0;
    std::uint64_t stale_events = 0, time_jumps = 0, fresh_calibrations = 0;
};

struct DaemonParts {
    Link& link;
    Parser parse;
    std::function<std::unique_ptr<Estimator>()> make_estimator;  // a new one means a new calibration
    Sink& sink;
    std::function<std::uint64_t()> now_ns;  // monotonic
    Logger log;
    std::vector<std::uint8_t> start_command;
    std::vector<std::uint8_t> stop_command;  // empty: none is sent at exit
};

enum class DaemonEnd { stop_requested, replay_ended };

class Daemon {
public:
    Daemon(DaemonParts parts, DaemonConfig config = {});
    // Until `stop` is set (a signal handler may set it; it is noticed within read_timeout) or a replay ends.
    DaemonEnd run(const std::atomic<bool>& stop);
    const Stats& stats() const { return stats_; }
private:
    void connect(std::uint64_t now);
    void on_packet(std::span<const std::uint8_t> packet, std::uint64_t now);
    void on_sample(ImuSample sample, std::uint64_t now);
    void begin_after_outage(std::uint64_t now);
    ImuSample continuous_time(ImuSample sample, std::uint64_t now);
    void watchdog(std::uint64_t now);
    void go_down(const char* reason, bool silent, std::uint64_t now);
    void new_estimator();
    void set_stale(bool stale);
    void log_stats();
    void logf(const char* format, ...) __attribute__((format(printf, 2, 3)));

    DaemonParts parts_;
    DaemonConfig config_;
    Stats stats_{};
    std::unique_ptr<Estimator> estimator_;
    std::array<std::uint8_t, 1024> buffer_{};

    bool up_ = false;                // the link is open and the start command went out
    bool ever_connected_ = false;
    bool outage_ = false;            // between `disconnected` and the first sample after it
    std::uint64_t down_since_ = 0;   // the last sample before the outage
    std::uint64_t next_open_ = 0;
    unsigned retries_ = 0, silent_retries_ = 0;
    std::uint64_t watch_from_ = 0;   // the last sample, or the moment this connection opened
    std::uint64_t connection_first_sample_ = 0;
    bool connection_has_sample_ = false;

    bool stale_ = true;
    bool announced_ = false;         // `calibrated` was logged since the last outage
    bool calibrated_ = false;        // the estimator has reported calibrated
    bool restored_ = false;          // the next `calibrated` is a kept calibration
    bool announce_calibrating_ = false;

    // Device time, made continuous: see continuous_time.
    bool have_time_ = false, resume_time_ = false;
    std::uint64_t last_raw_ = 0, last_continuous_ = 0, last_sample_now_ = 0;
    std::uint64_t offset_ = 0, published_gap_ = 0, nominal_dt_ = 1'000'000;
    std::uint64_t last_published_t_ = 0;
    std::uint64_t next_stats_ = 0;
};

// A recording to run instead of the glasses. This is a provisional container until docs/capture-format.md (drv-recorder)
// lands; only load_capture needs to change. "LUMACAP0", then records of little-endian u64 t_ns, u32 size, u8 kind, payload.
enum class CaptureKind : std::uint8_t { from_device = 0, to_device = 1, device_gone = 2, device_back = 3 };
struct CaptureRecord { std::uint64_t t_ns = 0; CaptureKind kind = CaptureKind::from_device; std::vector<std::uint8_t> payload; };
std::optional<std::vector<CaptureRecord>> load_capture(const std::string& path, std::string& why);
bool save_capture(const std::string& path, const std::vector<CaptureRecord>& records);

// Plays a capture on a simulated clock (now_ns), so its silences and drops trip the same watchdog as a real one and a
// long capture runs in moments. `realtime` also sleeps through the recorded gaps. Commands are counted and go nowhere:
// a replay never touches a USB device.
class ReplayLink final : public Link {
public:
    ReplayLink(std::vector<CaptureRecord> records, bool realtime);
    bool open(std::string& why) override;
    ReadResult read(std::span<std::uint8_t> into, milliseconds timeout) override;
    bool write(std::span<const std::uint8_t> bytes) override;
    void close() override {}
    bool wait_for_device(milliseconds timeout) override;
    bool exhausted() const override;
    std::uint64_t now_ns() const { return now_; }
    std::uint64_t commands_swallowed() const { return commands_; }
private:
    void advance_to(std::uint64_t t);
    void skip_commands();
    std::vector<CaptureRecord> records_;
    std::size_t next_ = 0;
    std::uint64_t now_ = 0, commands_ = 0;
    bool realtime_ = false, absent_ = false;
};

inline constexpr const char* kLiveIpcPrefix = "/tmp/shader_runtime_";  // what spike/src renderer opens today

struct Options {
    std::string ipc_prefix = kLiveIpcPrefix;
    std::optional<std::string> replay;
    bool replay_realtime = false, help = false;
    DaemonConfig config{};
};
// nullopt: `why` says what is wrong with the command line. A replay must name its --ipc-prefix: run with the default it
// would overwrite the feed of a live session.
std::optional<Options> parse_options(std::span<const char* const> args, std::string& why);
extern const char* const kUsage;
}
