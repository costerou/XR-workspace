// Optional device features for xr-luma-driver: state/button events, wear
// detection, one-shot brightness and dimming, firmware and temperature readout.
//
// Rules this file enforces (docs/features.md has the sources and risk notes):
//  - Nothing here polls. A command goes out once per connection, or when
//    explicitly requested, and is never retried.
//  - A message id nobody has published is left empty. An empty id reports
//    "unavailable"; it is never guessed at the glasses.
//  - A command that times out stops all commands for that connection. A USB
//    drop right after a command stops them until the daemon restarts.
//  - Wire framing stays in protocol.hpp. This code sees decoded (id, payload)
//    messages and hands back (id, payload) requests.
//
// Threads: Manager has one owner (the daemon's control thread). on_message(),
// tick() and pop_event() do no I/O and no allocation, so they can never stall
// a pose. StillWatch belongs to the IMU thread and shares one atomic.
#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace luma::features {

using Nanos = std::uint64_t;  // CLOCK_MONOTONIC, nanoseconds

inline constexpr int kBrightnessMax = 8;  // viture_protocol_public.h: Luma Series [0, 8]

enum class WireProfile : std::uint8_t {
    CarinaV1,  // Luma Ultra: libglasses logs "protocol version: V1" on this device
    Gen2V2,    // Pro 2 and similar: elasticjava/viture-v2 PROTOCOL.md
};

enum class Request : std::uint8_t { QueryFirmware, QueryWear, SetBrightness, SetDimming };
inline constexpr std::size_t kRequestKinds = 4;

struct Route {
    std::optional<std::uint16_t> send;
    std::optional<std::uint16_t> reply;  // empty: fire and forget
};

struct WireMap {
    std::array<Route, kRequestKinds> routes{};
    std::optional<std::uint16_t> ev_brightness, ev_display_mode, ev_volume, ev_film, ev_wear;
    bool reply_has_status = true;  // V2: first reply byte is a status, 0 = success
    int film_levels = 1;           // viture_protocol_public.h: Luma Series [0, 1]
};

// Only what a public source states. Everything else stays empty.
WireMap default_map(WireProfile profile);

// "key=value", e.g. "ev_wear=0x0305". Lets a capture fill the map without a
// rebuild. Ids that would touch the IMU stream are refused as send ids.
bool apply_map_override(WireMap& map, std::string_view entry, std::string* error);

struct Config {
    WireProfile profile = WireProfile::CarinaV1;
    WireMap map = default_map(WireProfile::CarinaV1);
    bool map_overridden = false;

    // Listening only, no USB traffic: on by default.
    bool state_events = true;
    bool wear_events = true;
    bool temperature = true;
    bool usb_identity = true;

    // No USB traffic, but they change behaviour or add output: off by default.
    bool discovery = false;
    bool still_wear = false;
    bool auto_pause = false;
    bool oled_saver = false;
    bool ipc = false;

    // These send commands: off by default.
    bool firmware_query = false;
    bool wear_query = false;
    bool control = false;  // allows request_brightness()/request_dimming()
    std::optional<int> brightness;
    std::optional<int> dimming;

    // Timing.
    // The same five minutes and 60 s as display.hpp's DisplayTiming, the safer of the two values each: 5 of the 11 dropped
    // sessions on 2026-11-03 fell at 18 to 135 s, and a daemon restarted by a drop cannot remember it.
    std::chrono::milliseconds settle{300'000};  // a connection must last this long first
    std::chrono::milliseconds min_gap{250};
    std::chrono::milliseconds reply_timeout{1000};
    std::chrono::milliseconds drop_window{60'000};
    std::chrono::milliseconds wear_off_debounce{3000};
    std::chrono::milliseconds wear_on_debounce{200};
    std::chrono::milliseconds saver_delay{30000};
    std::chrono::seconds still_off{120};
    int max_commands_per_connection = 16;
};

struct FlagInfo {
    std::string_view name;   // after "--feature-"
    bool default_on;
    bool sends_usb;
    std::string_view risk;
};
std::span<const FlagInfo> flag_table();

enum class FlagResult { NotMine, Ok, Bad };
// Takes --feature-NAME, --no-feature-NAME and --feature-NAME=VALUE.
FlagResult parse_flag(Config& config, std::string_view arg, std::string* error);
std::string flag_usage();

enum class EventKind : std::uint8_t {
    Brightness, Volume, DisplayMode, Dimming, Wear,
    Pause, Resume, SaverOn, SaverOff,
    Firmware, Temperature, Identity, LinkDegraded,
    Unavailable, Sent, Rejected, TimedOut, Tripped, LatchedOff,
    Discovered, EventsDropped,
};

enum class WearSource : std::uint8_t { Hardware, Stillness };

struct Event {
    EventKind kind = EventKind::Brightness;
    Nanos t_ns = 0;
    std::int32_t value = 0;
    float celsius = 0;
    Request request = Request::QueryFirmware;
    WearSource wear_source = WearSource::Hardware;
    std::uint16_t msg_id = 0;
    std::uint8_t data_len = 0;
    std::array<char, 40> text{};
    std::array<std::uint8_t, 16> data{};
};

// One line of JSON, no newline. Truncates to the buffer; returns the length.
std::size_t format_event(const Event& event, std::span<char> out);

struct OutRequest {
    Request kind{};
    std::uint16_t id = 0;
    std::uint8_t payload_len = 0;
    std::array<std::uint8_t, 1> payload{};
};

enum class RequestResult {
    Queued, AlreadySet, Disabled, OutOfRange, Unavailable, NotConnected, Stopped, BudgetExhausted,
};

struct UsbIdentity {
    std::string busid, product;
    std::uint16_t vendor = 0, product_id = 0, bcd_device = 0;
    int speed_mbps = 0;
};

class Manager {
public:
    explicit Manager(Config config);

    // Connection edges, from the transport's hotplug events.
    void on_connect(Nanos now);
    void on_disconnect(Nanos now);

    // A decoded non-IMU frame from the glasses.
    void on_message(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now);

    // Passive inputs, when something supplies them.
    void on_temperature(float celsius, Nanos now);
    void on_identity(const UsbIdentity& identity, Nanos now);

    // The clock for debounce, saver and timeouts. Sends nothing by itself.
    // last_motion is StillWatch::last_motion_ns() when --feature-still-wear is on.
    void tick(Nanos now, std::optional<Nanos> last_motion = std::nullopt);

    // Explicit requests. Latest value wins while one is waiting.
    RequestResult request_brightness(int level);
    RequestResult request_dimming(int level);

    // At most one at a time: write it, then report on_write_result().
    std::optional<OutRequest> next_request(Nanos now);
    void on_write_result(bool ok, Nanos now);

    std::optional<Event> pop_event();

    [[nodiscard]] std::string_view firmware() const { return firmware_.data(); }
    [[nodiscard]] std::optional<bool> worn() const { return worn_; }
    [[nodiscard]] bool commands_stopped() const { return tripped_ || latched_; }
    [[nodiscard]] bool commands_latched_off() const { return latched_; }
    [[nodiscard]] int commands_sent_total() const { return sent_total_; }
    [[nodiscard]] std::uint64_t malformed() const { return malformed_; }

private:
    struct Seen {
        std::uint16_t id = 0;
        std::uint8_t emitted = 0;
        std::uint32_t hash = 0;
    };

    Event& push(EventKind kind, Nanos now);
    bool take_reply(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now);
    void feed_worn(bool worn, WearSource source, Nanos now);
    void commit_wear(Nanos now);
    void end_wear_actions(Nanos now);
    void state_changed(EventKind kind, std::optional<int>& known, int value, Nanos now);
    void discover(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now);
    void stop_connection(Nanos now);
    RequestResult request_set(Request kind, int level, int max, const std::optional<int>& known);

    Config cfg_;

    static constexpr std::size_t kEventSlots = 32;
    std::array<Event, kEventSlots> events_{};
    std::size_t event_head_ = 0, event_count_ = 0;
    std::uint32_t events_dropped_ = 0;

    // Commands.
    bool connected_ = false, tripped_ = false, latched_ = false;
    Nanos connected_at_ = 0, last_sent_ = 0;
    bool ever_sent_ = false;
    std::array<bool, kRequestKinds> pending_{};
    std::array<std::uint8_t, kRequestKinds> pending_value_{};
    std::optional<Request> inflight_;
    std::uint8_t inflight_value_ = 0;
    bool awaiting_write_ = false;
    int sent_this_connection_ = 0, sent_total_ = 0;

    // State last seen on the glasses.
    std::optional<int> brightness_, volume_, display_mode_, dimming_;
    std::array<char, 40> firmware_{};

    // Wear.
    std::optional<bool> worn_;
    bool have_candidate_ = false, candidate_ = false, hw_wear_seen_ = false;
    Nanos candidate_since_ = 0;
    WearSource candidate_source_ = WearSource::Hardware;
    bool paused_ = false, saver_on_ = false, saver_armed_ = false;
    Nanos saver_due_ = 0;

    // Temperature.
    std::optional<float> last_celsius_;
    Nanos last_celsius_at_ = 0;

    static constexpr std::size_t kSeenSlots = 32;
    std::array<Seen, kSeenSlots> seen_{};
    std::size_t seen_count_ = 0;
    std::uint64_t malformed_ = 0;
};

// Is anything moving these glasses? Averages samples into 50 ms blocks, so
// sensor noise does not count as motion, and compares block to block, so gyro
// bias does not either. update() is O(1), allocates nothing and belongs to the
// IMU thread. last_motion_ns() may be read from any thread.
class StillWatch {
public:
    struct Thresholds {
        float gyro_rad_s = 0.01f;
        float accel_m_s2 = 0.03f;
    };
    StillWatch() = default;
    explicit StillWatch(Thresholds thresholds) : thr_(thresholds) {}

    void update(Nanos t_ns, const std::array<float, 3>& gyro_rad_s,
                const std::array<float, 3>& accel_m_s2);
    [[nodiscard]] Nanos last_motion_ns() const { return last_motion_.load(std::memory_order_relaxed); }

private:
    static constexpr Nanos kBlockNs = 50'000'000;
    Thresholds thr_{};
    std::array<float, 6> sum_{}, prev_{};
    std::uint32_t count_ = 0;
    Nanos block_start_ = 0;
    bool started_ = false, have_prev_ = false;
    std::atomic<Nanos> last_motion_{0};
};

// Reads the kernel's cached copy of the USB descriptors. Opens no device node
// and causes no USB traffic. The serial number is deliberately not read.
std::optional<UsbIdentity> find_usb_identity(const std::filesystem::path& root,
                                             std::uint16_t vendor, std::uint16_t product);
inline constexpr std::string_view kSysfsUsbDevices = "/sys/bus/usb/devices";

// Non-blocking datagrams to a path socket, which still works from inside the
// tracking network namespace. No listener means the line is dropped, never queued.
class EventSocket {
public:
    EventSocket() = default;
    ~EventSocket();
    EventSocket(const EventSocket&) = delete;
    EventSocket& operator=(const EventSocket&) = delete;

    bool open(const std::filesystem::path& path);
    bool send(std::string_view line);
    [[nodiscard]] std::uint64_t dropped() const { return dropped_; }

private:
    int fd_ = -1;
    std::string path_;
    std::uint64_t dropped_ = 0;
};

// $XDG_RUNTIME_DIR/xr-luma/events.sock, or empty when that is unset.
std::filesystem::path default_event_socket_path();

}  // namespace luma::features
