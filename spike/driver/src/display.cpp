#include "luma/display.hpp"

#include <charconv>
#include <cstddef>
#include <utility>

namespace luma {
namespace {

using std::chrono::milliseconds;

constexpr std::array<DisplayCommand, kDisplayCommandCount> kSendOrder{
    DisplayCommand::exit_3d, DisplayCommand::set_mode, DisplayCommand::set_brightness};

constexpr std::size_t index_of(DisplayCommand command) { return static_cast<std::size_t>(command); }

struct NamedMode {
    std::string_view name;
    DisplayMode mode;
};
constexpr std::array<NamedMode, 5> kModes{{
    {"1920x1080@60", DisplayMode::fhd_60},
    {"1920x1080@90", DisplayMode::fhd_90},
    {"1920x1200@60", DisplayMode::wuxga_60},
    {"1920x1200@90", DisplayMode::wuxga_90},
    {"1920x1200@120", DisplayMode::wuxga_120},
}};

constexpr std::string_view kModeFlag = "--display-mode";
constexpr std::string_view kExit3dFlag = "--display-exit-3d";
constexpr std::string_view kBrightnessFlag = "--display-brightness";

// The part after `flag=`, or nullopt when `arg` is some other flag.
std::optional<std::string_view> value_after(std::string_view arg, std::string_view flag) {
    if (!arg.starts_with(flag) || arg.size() <= flag.size() || arg[flag.size()] != '=') return std::nullopt;
    return arg.substr(flag.size() + 1);
}

std::string_view name_of(DisplayCommand command) {
    switch (command) {
        case DisplayCommand::exit_3d: return "exit_3d";
        case DisplayCommand::set_mode: return "set_mode";
        case DisplayCommand::set_brightness: return "set_brightness";
    }
    return "unknown";
}

std::string_view name_of(DisplayOutcome outcome) {
    switch (outcome) {
        case DisplayOutcome::sent: return "sent";
        case DisplayOutcome::write_failed: return "write_failed";
        case DisplayOutcome::refused_unmapped: return "refused_unmapped";
        case DisplayOutcome::blocked: return "blocked";
    }
    return "unknown";
}

}  // namespace

DisplayFlag parse_display_flag(std::string_view arg, DisplayOptions& options) {
    if (arg == kExit3dFlag) {
        options.exit_3d = true;
        return DisplayFlag::accepted;
    }
    if (auto value = value_after(arg, kModeFlag)) {
        for (const auto& known : kModes) {
            if (known.name == *value) {
                options.mode = known.mode;
                return DisplayFlag::accepted;
            }
        }
        return DisplayFlag::invalid;
    }
    if (auto value = value_after(arg, kBrightnessFlag)) {
        unsigned level = 0;
        const char* end = value->data() + value->size();
        auto [ptr, error] = std::from_chars(value->data(), end, level);
        if (error != std::errc{} || ptr != end || level > kMaxBrightness) return DisplayFlag::invalid;
        options.brightness = static_cast<std::uint8_t>(level);
        return DisplayFlag::accepted;
    }
    if (value_after(arg, kExit3dFlag)) return DisplayFlag::invalid;
    return DisplayFlag::not_ours;
}

std::string format_display_event(const DisplayEvent& event) {
    std::string line = "display command=";
    line += name_of(event.command);
    line += " value=" + std::to_string(event.value) + " outcome=";
    line += name_of(event.outcome);
    return line;
}

DisplayControl::DisplayControl(DisplayOptions options, const DisplayWire& wire, DisplaySink& sink, DisplayTiming timing)
    : options_(options), wire_(wire), sink_(sink), timing_(timing) {
    // Every selectable mode is 2D, so setting one already leaves SBS. exit_3d would
    // also land on 1080p60 first and needs the stream stopped.
    if (options_.mode) options_.exit_3d = false;
}

bool DisplayControl::wanted(DisplayCommand command) const {
    switch (command) {
        case DisplayCommand::exit_3d: return options_.exit_3d;
        case DisplayCommand::set_mode: return options_.mode.has_value();
        case DisplayCommand::set_brightness: return options_.brightness.has_value();
    }
    return false;
}

std::uint8_t DisplayControl::value_of(DisplayCommand command) const {
    switch (command) {
        case DisplayCommand::exit_3d: return 0;
        case DisplayCommand::set_mode: return static_cast<std::uint8_t>(options_.mode.value_or(DisplayMode{}));
        case DisplayCommand::set_brightness: return options_.brightness.value_or(0);
    }
    return 0;
}

void DisplayControl::on_connected(milliseconds now) {
    if (connected_) return;  // a repeated event must not hand out a second attempt
    connected_ = true;
    due_ = now + timing_.settle;
    attempted_ = {};
}

void DisplayControl::on_disconnected(milliseconds now) {
    if (!connected_) return;
    connected_ = false;
    if (last_written_ && now - last_written_at_ <= timing_.blame_window) blocked_[index_of(*last_written_)] = true;
    last_written_.reset();
}

std::optional<DisplayEvent> DisplayControl::tick(milliseconds now, bool stream_running) {
    if (!connected_ || now < due_) return std::nullopt;
    for (DisplayCommand command : kSendOrder) {
        const std::size_t i = index_of(command);
        if (!wanted(command) || attempted_[i]) continue;
        if (command == DisplayCommand::exit_3d && stream_running) continue;
        // Spent before the write, so a failure cannot lead to a second try.
        attempted_[i] = true;
        const std::uint8_t value = value_of(command);
        if (blocked_[i]) return DisplayEvent{command, value, DisplayOutcome::blocked};
        const auto frame = wire_.encode(DisplayRequest{command, value});
        if (!frame) return DisplayEvent{command, value, DisplayOutcome::refused_unmapped};
        last_written_ = command;
        last_written_at_ = now;
        due_ = now + timing_.gap;
        const bool written = sink_.write(*frame);
        return DisplayEvent{command, value, written ? DisplayOutcome::sent : DisplayOutcome::write_failed};
    }
    return std::nullopt;
}

bool DisplayControl::holds_stream() const {
    return connected_ && options_.exit_3d && !attempted_[index_of(DisplayCommand::exit_3d)];
}

}  // namespace luma
