#include "luma/display.hpp"

#include <array>

namespace luma {
namespace {

using std::chrono::milliseconds;

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

// The part after `flag=`, or nullopt when `arg` is some other flag.
std::optional<std::string_view> value_after(std::string_view arg, std::string_view flag) {
    if (!arg.starts_with(flag) || arg.size() <= flag.size() || arg[flag.size()] != '=') return std::nullopt;
    return arg.substr(flag.size() + 1);
}

std::string_view name_of(DisplayCommand command) {
    switch (command) {
        case DisplayCommand::exit_3d: return "exit_3d";
        case DisplayCommand::set_mode: return "set_mode";
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
    : wire_(wire), sink_(sink), timing_(timing) {
    // Every selectable mode is 2D, so setting one already leaves SBS. exit_3d would
    // also land on 1080p60 and needs the stream stopped, so the mode wins.
    if (options.mode) {
        request_ = DisplayRequest{DisplayCommand::set_mode, static_cast<std::uint8_t>(*options.mode)};
    } else if (options.exit_3d) {
        request_ = DisplayRequest{DisplayCommand::exit_3d, 0};
    }
}

void DisplayControl::on_connected(milliseconds now) {
    if (connected_) return;  // a repeated event must not hand out a second attempt
    connected_ = true;
    due_ = now + timing_.settle;
    attempted_ = false;
}

void DisplayControl::on_disconnected(milliseconds now) {
    if (!connected_) return;
    connected_ = false;
    if (written_at_ && now - *written_at_ <= timing_.blame_window) blocked_ = true;
    written_at_.reset();
}

std::optional<DisplayEvent> DisplayControl::tick(milliseconds now, bool stream_running) {
    if (!request_ || !connected_ || attempted_ || now < due_) return std::nullopt;
    if (request_->command == DisplayCommand::exit_3d && stream_running) return std::nullopt;
    // Spent before the write, so a failure cannot lead to a second try.
    attempted_ = true;
    const auto event = [this](DisplayOutcome outcome) {
        return DisplayEvent{request_->command, request_->value, outcome};
    };
    if (blocked_) return event(DisplayOutcome::blocked);
    const auto frame = wire_.encode(*request_);
    if (!frame) return event(DisplayOutcome::refused_unmapped);
    written_at_ = now;
    return event(sink_.write(*frame) ? DisplayOutcome::sent : DisplayOutcome::write_failed);
}

bool DisplayControl::holds_stream() const {
    return connected_ && !attempted_ && request_ && request_->command == DisplayCommand::exit_3d;
}

}  // namespace luma
