// Optional one-shot display commands for the Luma Ultra, all OFF by default.
//
// XR Workspace does not need any of these today: it reaches 1920x1200@120 by
// setting the DisplayPort mode. See spike/driver/docs/display-control.md, which
// also holds the evidence and the sources for every rule below.
//
// Rules this module enforces by construction:
// - There are three commands and all of them set something. No "get" exists to
//   call, so display state cannot be polled from here. MsgID 0x0007, the
//   get_display_mode request XRLinuxDriver makes once a second, is not sendable.
// - DisplaySink can only write. Nothing waits for a reply, so nothing can time
//   out and nothing retries. A command is judged by what the DisplayPort output
//   does afterwards, never by a USB response.
// - Each command is attempted at most once per connection, even if the write
//   fails.
// - Nothing is sent until the link has been up for `settle`, and commands are at
//   least `gap` apart.
// - A command followed by a drop within `blame_window` is never sent again by
//   this process.
// - The Luma Ultra's frames are not publicly documented, so UnmappedWire refuses
//   every command. Replacing it needs a real capture, not a guess.
#pragma once

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace luma {

// Values are VITURE_DISPLAY_MODE_* from include/sdks/viture_protocol_public.h in
// github.com/wheaney/XRLinuxDriver at 9589084. Only the 2D modes the glasses
// advertise over DisplayPort are listed, so every selectable mode also leaves SBS.
enum class DisplayMode : std::uint8_t {
    fhd_60 = 0x31,
    fhd_90 = 0x33,
    wuxga_60 = 0x41,
    wuxga_90 = 0x43,
    wuxga_120 = 0x44,
};

// In send order. exit_3d comes first because the SDK refuses it mid-stream.
enum class DisplayCommand : std::uint8_t { exit_3d, set_mode, set_brightness };
inline constexpr std::size_t kDisplayCommandCount = 3;

// `value` is the mode byte, the brightness level, or 0 for exit_3d.
struct DisplayRequest {
    DisplayCommand command{};
    std::uint8_t value = 0;
};

inline constexpr std::uint8_t kMaxBrightness = 8;  // Luma series, per the same header

struct DisplayOptions {
    std::optional<DisplayMode> mode;
    bool exit_3d = false;
    std::optional<std::uint8_t> brightness;
    [[nodiscard]] bool any() const { return mode || exit_3d || brightness; }
};

enum class DisplayFlag { not_ours, accepted, invalid };

// Handles --display-mode=1920x1200@120, --display-exit-3d and --display-brightness=N.
DisplayFlag parse_display_flag(std::string_view arg, DisplayOptions& options);

struct DisplayTiming {
    std::chrono::milliseconds settle{30'000};
    std::chrono::milliseconds gap{2'000};
    std::chrono::milliseconds blame_window{10'000};
};

// Turns a request into bytes, or nullopt when those bytes are not known.
class DisplayWire {
public:
    virtual ~DisplayWire() = default;
    [[nodiscard]] virtual std::optional<std::vector<std::uint8_t>> encode(const DisplayRequest&) const = 0;
};

// The production wire until a capture proves real frames.
class UnmappedWire final : public DisplayWire {
public:
    [[nodiscard]] std::optional<std::vector<std::uint8_t>> encode(const DisplayRequest&) const override {
        return std::nullopt;
    }
};

// The daemon adapts luma::Transport::write to this. Deliberately no read.
class DisplaySink {
public:
    virtual ~DisplaySink() = default;
    virtual bool write(std::span<const std::uint8_t> frame) = 0;
};

enum class DisplayOutcome {
    sent,
    write_failed,      // not retried
    refused_unmapped,  // the wire has no frame for it; the device was not touched
    blocked,           // an earlier send was followed by a drop; the device was not touched
};

struct DisplayEvent {
    DisplayCommand command{};
    std::uint8_t value = 0;
    DisplayOutcome outcome{};
};

// One structured log line, without the trailing newline.
std::string format_display_event(const DisplayEvent& event);

// Single-threaded: the daemon calls all of this from its own loop. `now` is any
// monotonic time. tick() costs no USB traffic unless a command is due, and once
// every command has been attempted it never does anything again this connection.
class DisplayControl {
public:
    DisplayControl(DisplayOptions options, const DisplayWire& wire, DisplaySink& sink, DisplayTiming timing = {});

    void on_connected(std::chrono::milliseconds now);
    void on_disconnected(std::chrono::milliseconds now);
    std::optional<DisplayEvent> tick(std::chrono::milliseconds now, bool stream_running);

    // True while exit_3d is still to come on this connection: the daemon must
    // hold the IMU stream back until this turns false.
    [[nodiscard]] bool holds_stream() const;

private:
    [[nodiscard]] bool wanted(DisplayCommand command) const;
    [[nodiscard]] std::uint8_t value_of(DisplayCommand command) const;

    DisplayOptions options_;
    const DisplayWire& wire_;
    DisplaySink& sink_;
    DisplayTiming timing_;
    bool connected_ = false;
    std::chrono::milliseconds due_{0};
    std::array<bool, kDisplayCommandCount> attempted_{};
    std::array<bool, kDisplayCommandCount> blocked_{};
    std::optional<DisplayCommand> last_written_;
    std::chrono::milliseconds last_written_at_{0};
};

}  // namespace luma
