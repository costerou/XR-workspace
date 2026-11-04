// Optional one-shot display command for the Luma Ultra, OFF by default.
//
// XR Workspace does not need any of these today: it reaches 1920x1200@120 by
// setting the DisplayPort mode. See spike/driver/docs/display-control.md, which
// also holds the evidence and the sources for every rule below.
//
// Rules this module enforces by construction:
// - At most one command is ever in play, and it sets something. No "get" exists
//   to call, so display state cannot be polled from here. MsgID 0x0007, the
//   get_display_mode request XRLinuxDriver makes once a second, is not sendable.
// - DisplaySink can only write. Nothing waits for a reply, so nothing can time
//   out and nothing retries. A command is judged by what the DisplayPort output
//   does afterwards, never by a USB response.
// - The command is attempted at most once per connection, even if the write fails.
// - Nothing is sent until the link has been up for `settle`. The default is five
//   minutes because 5 of the 11 sessions that dropped on 2026-11-03 fell within
//   that time, and a restarted process remembers nothing, so it always applies.
// - A command followed by a drop within `blame_window` is never sent again by
//   this process.
// - The Luma Ultra's set frames are not publicly known, so UnmappedWire refuses
//   every command. Replacing it needs a real capture, not a guess: the control
//   pipes also carry firmware flashing (docs/protocol.md, "Never send").
//
// Brightness and dimming belong to luma::features, so one gate guards them.
#pragma once

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

enum class DisplayCommand : std::uint8_t { exit_3d, set_mode };
inline constexpr std::size_t kDisplayCommandCount = 2;

// `value` is the mode byte, or 0 for exit_3d.
struct DisplayRequest {
    DisplayCommand command{};
    std::uint8_t value = 0;
};

struct DisplayOptions {
    std::optional<DisplayMode> mode;
    bool exit_3d = false;
    [[nodiscard]] bool any() const { return mode || exit_3d; }
};

enum class DisplayFlag { not_ours, accepted, invalid };

// Handles --display-mode=1920x1200@120 and --display-exit-3d.
DisplayFlag parse_display_flag(std::string_view arg, DisplayOptions& options);

struct DisplayTiming {
    std::chrono::milliseconds settle{300'000};
    std::chrono::milliseconds blame_window{60'000};
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
// monotonic time. tick() costs no USB traffic unless the command is due, and once
// it has been attempted it never does anything again this connection.
class DisplayControl {
public:
    DisplayControl(DisplayOptions options, const DisplayWire& wire, DisplaySink& sink, DisplayTiming timing = {});

    void on_connected(std::chrono::milliseconds now);
    void on_disconnected(std::chrono::milliseconds now);
    std::optional<DisplayEvent> tick(std::chrono::milliseconds now, bool stream_running);

    // True while exit_3d is still to come on this connection: the daemon must
    // hold the IMU stream back until this turns false. It is why exit_3d is the
    // last resort: tracking would wait out `settle` as well.
    [[nodiscard]] bool holds_stream() const;

private:
    std::optional<DisplayRequest> request_;
    const DisplayWire& wire_;
    DisplaySink& sink_;
    DisplayTiming timing_;
    bool connected_ = false;
    std::chrono::milliseconds due_{0};
    bool attempted_ = false;
    bool blocked_ = false;
    std::optional<std::chrono::milliseconds> written_at_;
};

}  // namespace luma
