#pragma once
// The driver's daemon: bytes from the glasses in, pose feed out, and what happens when the USB link misbehaves.
//
// It knows the other pieces only through the small interfaces below, so every failure can be replayed against fakes with
// a simulated clock (tests/daemon_test.cpp). src/wiring.cpp is the one file that plugs the real Transport, parse_imu,
// Fusion and Publisher into them.
//
// Zero USB drops comes first, so the command traffic is as small as it can be. Sessions with XRLinuxDriver drop the glasses
// and as a plain display they never do; what the evidence shows is in docs/upstream-findings.md, and nothing proves the cause
// yet. So the daemon sends at most one start per connection and nothing else while it runs: no polling, no keep-alive, no
// display-mode query, never a device reset. Three rules from those findings:
//   - Listen first. If samples are already arriving after an open, the stream is on and no command goes out at all.
//   - Let the glasses settle. 18 of 53 drops came within 30 s of the one before, and a reconnect may be what caused the
//     second, so a device that just came back is left alone for settle_first; if it dies again soon after a start, for
//     twice as long, up to settle_max. A device that drops during the wait never received a command.
//   - Leave a silent device alone. In some drops the IMU stopped 7-12 s before the hub left, so a stalled stream is not
//     pushed at: retries double up to one every 9 s.
// Stats::commands counts every command, and the `luma: stats` line shows it.
//
// stderr lines, one event per line, `luma: <event> key=value ...`:
//   luma: connected                         the device is open (first time only)
//   luma: waiting reason=WHY                the device cannot be opened, whenever WHY changes (absent; another driver has it)
//   luma: calibrating                       a new calibration began: hold the glasses still
//   luma: calibrated restored=0|1 [heading=kept]
//                                           the pose is usable; restored=1: a reconnect kept the earlier calibration;
//                                           heading=kept: a new calibration, turned to carry on from the last heading
//   luma: stale ms=N                        the link is up but no samples for N ms; the feed is marked disabled
//   luma: fresh gap_ms=N                    samples came back on the same connection
//   luma: disconnected reason=gone|silent|write-failed
//   luma: retry attempt=N reason=silent|write-failed
//                                           another try at a device that opens but stays silent or refuses the start
//   luma: reconnected gap_ms=N calibration=kept|lost|none
//   luma: stats samples=N ... commands=N    once a minute and at exit
//   luma: stopped reason=signal|replay-end
// Readiness is "the last `calibrated` comes after the last `disconnected` and `calibrating`", as tracking.py reads it:
// that is why a kept calibration logs `calibrated restored=1` again after its `reconnected`.
#include "luma/rt.hpp"
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
// t_ns: CLOCK_MONOTONIC when the transfer completed (the URB was reaped), or the record time in a replay. 0: not known.
struct ReadResult { ReadStatus status = ReadStatus::timeout; std::size_t size = 0; std::uint64_t t_ns = 0; };

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

// Bytes to samples. Stateful, because counting lost samples and spotting a restarted device counter needs history.
class Decoder {
public:
    virtual ~Decoder() = default;
    virtual std::optional<ImuSample> decode(std::span<const std::uint8_t> packet) = 0;
    virtual void new_connection() {}
    virtual std::uint64_t lost_samples() const { return 0; }
    // The last packet decode() turned down was not junk: the Ultra sends the first 60 or so twice, and the first of a connection
    // may be left over from whoever read last.
    virtual bool last_was_repeat() const { return false; }
    virtual std::optional<float> temperature_c() const { return std::nullopt; }  // from the newest packet, when it has one
};

using Logger = std::function<void(std::string_view)>;

struct DaemonConfig {
    milliseconds read_timeout{20};           // also how soon a stop request is noticed
    milliseconds stale_after{100};           // matches PoseState's default in spike/include/xr/pose.hpp
    milliseconds silent_reopen_after{1000};  // no samples this long on an open link: close and try again
    milliseconds retry_first{20}, retry_max{100};           // device gone: looking for it costs no USB traffic
    milliseconds silent_retry_first{2000}, silent_retry_max{8000};  // device silent: each try sends a command
    milliseconds listen_first{20};           // after an open, before any command: a running stream needs none (20 samples)
    milliseconds start_spacing{100};         // between the commands of before_start, as VITURE's own software spaces them
    milliseconds settle_first{200}, settle_max{3200};  // the same for a device that just came back: see the top of this file
    // Back within this long: same calibration, same orientation. Fusion needs no reset for a gap (FusionQuality::Recovering
    // re-acquires gravity and the bias still holds), while a new calibration means holding still. The window only guards
    // against a bias gone stale, which Fusion goes on correcting anyway. Drops cluster, so a short window would lose it for nothing.
    milliseconds calibration_keep{300000};
    milliseconds healthy_after{2000};        // a connection this long ends the back-off; a flapping one does not
    milliseconds time_jump{500};             // device time stepping further than this is a new time base
    milliseconds stats_every{60000};
    bool keep_heading = true;                // a new calibration carries on from the last published heading
};

struct Stats {
    std::uint64_t samples = 0, bad_packets = 0, bad_poses = 0, published = 0, repeats = 0;
    std::uint64_t commands = 0, opens = 0, connections = 0, disconnects = 0, reconnects = 0;
    std::uint64_t stale_events = 0, time_jumps = 0, fresh_calibrations = 0;
    std::uint64_t starts_skipped = 0;  // connections that found the stream already on, and sent nothing
    std::uint64_t max_process_ns = 0;  // the slowest packet, bytes in to pose out, since the last stats line
};

struct DaemonParts {
    Link& link;
    Decoder& decoder;
    std::function<std::unique_ptr<Estimator>()> make_estimator;  // a new one means a new calibration
    Sink& sink;
    std::function<std::uint64_t()> now_ns;  // monotonic
    Logger log;
    std::vector<std::uint8_t> start_command;
    std::vector<std::uint8_t> stop_command;  // empty: none is sent at exit
    // Sent in order ahead of start_command, start_spacing apart. A sample arriving meanwhile cancels the rest. An empty
    // start_command with nothing here only listens.
    std::vector<std::vector<std::uint8_t>> before_start{};
    // Off the hot path: link changes, and about ten times a second while running. Empty: nothing is called.
    std::function<void(bool up, std::uint64_t now)> on_link{};
    std::function<void(std::uint64_t now)> on_tick{};
};

enum class DaemonEnd { stop_requested, replay_ended };

class Daemon {
public:
    Daemon(DaemonParts parts, DaemonConfig config = {});
    // Until `stop` is set (a signal handler may set it; it is noticed within read_timeout) or a replay ends.
    DaemonEnd run(const std::atomic<bool>& stop);
    const Stats& stats() const { return stats_; }
    const rt::LatencyHistogram& process_times() const { return process_ns_; }  // the whole run, bytes in to pose out
private:
    void connect(std::uint64_t now);
    void send_start(std::uint64_t now);
    void on_packet(std::span<const std::uint8_t> packet, std::uint64_t now);
    void on_sample(ImuSample sample, std::uint64_t now);
    void begin_after_outage(std::uint64_t now);
    ImuSample steady_time(ImuSample sample, std::uint64_t now);
    void carry_heading(PoseOut& pose);
    void watchdog(std::uint64_t now);
    void go_down(const char* reason, bool silent, std::uint64_t now);
    void new_estimator();
    void set_stale(bool stale);
    void log_stats();
    void logf(const char* format, ...) __attribute__((format(printf, 2, 3)));

    DaemonParts parts_;
    DaemonConfig config_;
    Stats stats_{};
    rt::LatencyHistogram process_ns_;
    std::size_t before_start_sent_ = 0;
    std::unique_ptr<Estimator> estimator_;
    std::array<std::uint8_t, 1024> buffer_{};

    bool up_ = false;                // the link is open and the start command went out
    bool ever_connected_ = false;
    bool outage_ = false;            // between `disconnected` and the first sample after it
    std::uint64_t down_since_ = 0;   // the last sample before the outage
    std::uint64_t next_open_ = 0;
    unsigned retries_ = 0, silent_retries_ = 0, settle_level_ = 0;
    bool device_left_ = false;       // it went away since we last had it: it gets to settle
    bool start_pending_ = false;     // connected, listening, nothing sent yet
    bool start_sent_ = false;        // on this connection
    std::uint64_t start_at_ = 0;
    std::string waiting_for_;        // the last `waiting` reason logged
    std::uint64_t watch_from_ = 0;   // the last sample, or the moment this connection opened
    std::uint64_t connection_first_sample_ = 0;
    bool connection_has_sample_ = false;

    bool stale_ = true;
    bool announced_ = false;         // `calibrated` was logged since the last outage
    bool calibrated_ = false;        // the estimator has reported calibrated
    bool restored_ = false;          // the next `calibrated` is a kept calibration
    bool announce_calibrating_ = false;

    // Device time, made to run forward: see steady_time.
    bool have_time_ = false, resume_time_ = false;
    std::uint64_t last_raw_ = 0, last_steady_ = 0, last_sample_now_ = 0, offset_ = 0;
    std::uint64_t last_published_t_ = 0;
    // A new calibration's heading is the world it happened to face: see carry_heading.
    bool heading_pending_ = false, heading_fixed_ = false, heading_kept_ = false, have_last_q_ = false;
    Quatf heading_fix_{}, last_q_{};
    std::uint64_t next_stats_ = 0, stats_samples_ = 0, stats_since_ = 0, next_tick_ = 0;
};

// A recording to run instead of the glasses. load_capture reads a real capture, .lcap (docs/capture-format.md), keeping one
// endpoint's data. It also reads the plain "LUMACAP0" container the tests write: little-endian u64 t_ns, u32 size, u8 kind,
// payload.
enum class CaptureKind : std::uint8_t { from_device = 0, to_device = 1, device_gone = 2, device_back = 3 };
struct CaptureRecord { std::uint64_t t_ns = 0; CaptureKind kind = CaptureKind::from_device; std::vector<std::uint8_t> payload; };
inline constexpr std::uint8_t kReplayEndpoint = 0x81;  // protocol.hpp's kLumaImuEndpoint
std::optional<std::vector<CaptureRecord>> load_capture(const std::string& path, std::string& why, std::uint8_t endpoint = kReplayEndpoint);
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
    bool replay_realtime = false, help = false, stop_command = true;
    // listen: send nothing. stream: FA 55 95 alone. prepared: 97 then 95. full: 0x100B, 97, 95, the order VITURE's own
    // software uses. See spike/driver/docs/live-test-plan.md, the start ladder.
    enum class Start { listen, stream, prepared, full } start = Start::stream;
    bool read_events = false;    // also read 0x83, and throw it away
    bool drain_camera = false;   // also read 0x82 and throw it away: 19 MB/s, only if a live test shows the need
    bool no_rt = false;          // leave the thread's scheduling alone
    std::string state_dir;       // where the gyro bias is kept between runs; empty: $XDG_STATE_HOME or ~/.local/state
    std::optional<std::string> pose_log;  // --replay only: write every Nth pose here, for tools
    unsigned pose_log_every = 10;
    std::optional<std::string> latency_log;  // host and device time of every IMU packet, for luma_latency_report
    std::vector<std::string> feature_flags;  // --feature-* and --no-feature-*, passed on as they are
    DaemonConfig config{};
};
// nullopt: `why` says what is wrong with the command line. A replay must name its --ipc-prefix: run with the default it
// would overwrite the feed of a live session.
std::optional<Options> parse_options(std::span<const char* const> args, std::string& why);
extern const char* const kUsage;
}
