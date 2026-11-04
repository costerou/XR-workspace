// Fake-transport tests for luma::DisplayControl. Nothing here opens a device.
// CHECK counts failures rather than aborting, so a failure never dumps core.
#include "luma/display.hpp"

#include <concepts>
#include <cstdio>
#include <vector>

using namespace luma;
using std::chrono::milliseconds;

namespace {

int failures = 0;
#define CHECK(cond)                                                     \
    do {                                                                \
        if (!(cond)) {                                                  \
            std::printf("FAIL %s:%d: %s\n", __func__, __LINE__, #cond); \
            ++failures;                                                 \
        }                                                               \
    } while (0)

// The bytes are a test marker, not any real device's frame.
class FakeWire final : public DisplayWire {
public:
    [[nodiscard]] std::optional<std::vector<std::uint8_t>> encode(const DisplayRequest& r) const override {
        ++encoded;
        return std::vector<std::uint8_t>{0xEE, static_cast<std::uint8_t>(r.command), r.value};
    }
    mutable int encoded = 0;
};

class FakeSink final : public DisplaySink {
public:
    bool write(std::span<const std::uint8_t> frame) override {
        frames.emplace_back(frame.begin(), frame.end());
        return ok;
    }
    std::vector<std::vector<std::uint8_t>> frames;
    bool ok = true;
};

template <class T>
concept CanRead = requires(T& t, std::span<std::uint8_t> b) { t.read(b); };
static_assert(!CanRead<DisplaySink>, "a reply can never be waited for");
static_assert(kDisplayCommandCount == 3, "adding a command needs the risk table in docs/display-control.md");

const DisplayTiming kTiming{.settle = milliseconds(30'000), .gap = milliseconds(2'000), .blame_window = milliseconds(10'000)};
constexpr milliseconds s(long long seconds) { return milliseconds(seconds * 1000); }

DisplayOptions all_three() {
    DisplayOptions o;
    o.mode = DisplayMode::wuxga_120;
    o.brightness = 5;
    return o;
}

void every_flag_is_off_by_default() {
    CHECK(!DisplayOptions{}.any());
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(DisplayOptions{}, wire, sink, kTiming);
    c.on_connected(s(0));
    for (int t = 0; t < 600; ++t) CHECK(!c.tick(s(t), true));
    CHECK(sink.frames.empty());
    CHECK(wire.encoded == 0);
    CHECK(!c.holds_stream());
}

void nothing_is_sent_while_the_link_settles() {
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(all_three(), wire, sink, kTiming);
    c.on_connected(s(100));
    CHECK(!c.tick(s(100), true));
    CHECK(!c.tick(s(129), true));
    CHECK(sink.frames.empty());
    CHECK(c.tick(s(130), true).has_value());
    CHECK(sink.frames.size() == 1);
}

void nothing_is_sent_without_a_connection() {
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(all_three(), wire, sink, kTiming);
    CHECK(!c.tick(s(1000), true));
    c.on_connected(s(0));
    c.on_disconnected(s(1));
    CHECK(!c.tick(s(1000), true));
    CHECK(sink.frames.empty());
}

void commands_go_out_in_order_and_spaced_apart() {
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(all_three(), wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(c.tick(s(30), true).has_value());
    CHECK(!c.tick(s(31), true));
    auto second = c.tick(s(32), true);
    CHECK(second && second->command == DisplayCommand::set_brightness);
    CHECK(sink.frames.size() == 2);
    if (sink.frames.size() != 2) return;
    CHECK((sink.frames[0] == std::vector<std::uint8_t>{0xEE, 1, 0x44}));
    CHECK((sink.frames[1] == std::vector<std::uint8_t>{0xEE, 2, 5}));
}

// The heart of it: however long a connection lasts, the count never moves again.
void never_polls_after_the_commands_are_done() {
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(all_three(), wire, sink, kTiming);
    c.on_connected(s(0));
    for (int t = 0; t < 7 * 24 * 3600; ++t) c.tick(s(t), true);
    CHECK(sink.frames.size() == 2);
    CHECK(wire.encoded == 2);
}

void a_failed_write_is_not_retried() {
    FakeWire wire;
    FakeSink sink;
    sink.ok = false;
    DisplayOptions o;
    o.brightness = 3;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    auto event = c.tick(s(30), true);
    CHECK(event && event->outcome == DisplayOutcome::write_failed);
    for (int t = 31; t < 3600; ++t) CHECK(!c.tick(s(t), true));
    CHECK(sink.frames.size() == 1);
}

void each_new_connection_gets_one_more_attempt() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.brightness = 3;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(c.tick(s(30), true).has_value());
    c.on_disconnected(s(100));
    c.on_connected(s(101));
    CHECK(!c.tick(s(130), true));
    CHECK(c.tick(s(131), true).has_value());
    CHECK(sink.frames.size() == 2);
}

void a_repeated_connect_event_cannot_double_send() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.brightness = 3;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(c.tick(s(30), true).has_value());
    c.on_connected(s(31));
    for (int t = 31; t < 200; ++t) c.tick(s(t), true);
    CHECK(sink.frames.size() == 1);
}

// Shape from the 2026-11-03 log: drops 5-20 s after the link came back.
void a_drop_loop_never_sees_a_command() {
    FakeWire wire;
    FakeSink sink;
    DisplayControl c(all_three(), wire, sink, kTiming);
    milliseconds t = s(0);
    for (int i = 0; i < 100; ++i) {
        c.on_connected(t);
        for (int j = 0; j < 20; ++j) c.tick(t + s(j), true);
        t += s(20);
        c.on_disconnected(t);
        t += s(1);
    }
    CHECK(sink.frames.empty());
}

void a_command_followed_by_a_drop_is_blocked_for_good() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.mode = DisplayMode::wuxga_120;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(c.tick(s(30), true).has_value());
    c.on_disconnected(s(40));
    for (int round = 0; round < 5; ++round) {
        milliseconds start = s(100 + round * 100);
        c.on_connected(start);
        auto event = c.tick(start + s(30), true);
        CHECK(event && event->outcome == DisplayOutcome::blocked);
        CHECK(!c.tick(start + s(60), true));
        c.on_disconnected(start + s(90));
    }
    CHECK(sink.frames.size() == 1);
}

void a_drop_long_after_a_command_blames_nothing() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.mode = DisplayMode::wuxga_120;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(c.tick(s(30), true).has_value());
    c.on_disconnected(s(41));
    c.on_connected(s(100));
    auto event = c.tick(s(130), true);
    CHECK(event && event->outcome == DisplayOutcome::sent);
}

void the_real_wire_refuses_everything_and_never_touches_the_device() {
    UnmappedWire wire;
    FakeSink sink;
    DisplayOptions o = all_three();
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    auto first = c.tick(s(30), true);
    auto second = c.tick(s(30), true);
    CHECK(first && first->outcome == DisplayOutcome::refused_unmapped);
    CHECK(second && second->outcome == DisplayOutcome::refused_unmapped);
    CHECK(!c.tick(s(31), true));
    CHECK(sink.frames.empty());
}

void exit_3d_holds_the_stream_and_is_never_sent_mid_stream() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.exit_3d = true;
    DisplayControl c(o, wire, sink, kTiming);
    CHECK(!c.holds_stream());
    c.on_connected(s(0));
    CHECK(c.holds_stream());
    CHECK(!c.tick(s(60), true));
    CHECK(sink.frames.empty());
    auto event = c.tick(s(61), false);
    CHECK(event && event->command == DisplayCommand::exit_3d && event->outcome == DisplayOutcome::sent);
    CHECK(!c.holds_stream());
}

void the_unmapped_wire_never_leaves_the_stream_held() {
    UnmappedWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.exit_3d = true;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    c.tick(s(30), false);
    CHECK(!c.holds_stream());
}

void a_mode_makes_exit_3d_redundant() {
    FakeWire wire;
    FakeSink sink;
    DisplayOptions o;
    o.exit_3d = true;
    o.mode = DisplayMode::wuxga_120;
    DisplayControl c(o, wire, sink, kTiming);
    c.on_connected(s(0));
    CHECK(!c.holds_stream());
    for (int t = 30; t < 100; ++t) c.tick(s(t), true);
    CHECK(sink.frames.size() == 1);
    if (sink.frames.size() != 1) return;
    CHECK(sink.frames[0][1] == static_cast<std::uint8_t>(DisplayCommand::set_mode));
}

void flags_parse_strictly() {
    DisplayOptions o;
    CHECK(parse_display_flag("--verbose", o) == DisplayFlag::not_ours);
    CHECK(!o.any());
    CHECK(parse_display_flag("--display-mode=1920x1200@120", o) == DisplayFlag::accepted);
    CHECK(o.mode == DisplayMode::wuxga_120);
    CHECK(parse_display_flag("--display-mode=1920x1080@60", o) == DisplayFlag::accepted);
    CHECK(o.mode == DisplayMode::fhd_60);
    CHECK(parse_display_flag("--display-exit-3d", o) == DisplayFlag::accepted);
    CHECK(o.exit_3d);
    CHECK(parse_display_flag("--display-brightness=0", o) == DisplayFlag::accepted);
    CHECK(o.brightness == 0);
    CHECK(parse_display_flag("--display-brightness=8", o) == DisplayFlag::accepted);
    CHECK(o.brightness == 8);

    DisplayOptions bad;
    CHECK(parse_display_flag("--display-mode=3840x1200@60", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-mode=1920x1080@120", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-mode=", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-brightness=9", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-brightness=-1", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-brightness=3x", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-brightness=", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-brightness=256", bad) == DisplayFlag::invalid);
    CHECK(parse_display_flag("--display-exit-3d=1", bad) == DisplayFlag::invalid);
    CHECK(!bad.any());
}

void events_format_as_one_structured_line() {
    CHECK(format_display_event({DisplayCommand::set_mode, 0x44, DisplayOutcome::sent}) ==
          "display command=set_mode value=68 outcome=sent");
    CHECK(format_display_event({DisplayCommand::exit_3d, 0, DisplayOutcome::refused_unmapped}) ==
          "display command=exit_3d value=0 outcome=refused_unmapped");
    CHECK(format_display_event({DisplayCommand::set_brightness, 5, DisplayOutcome::blocked}) ==
          "display command=set_brightness value=5 outcome=blocked");
    CHECK(format_display_event({DisplayCommand::set_brightness, 5, DisplayOutcome::write_failed}) ==
          "display command=set_brightness value=5 outcome=write_failed");
}

}  // namespace

int main() {
    every_flag_is_off_by_default();
    nothing_is_sent_while_the_link_settles();
    nothing_is_sent_without_a_connection();
    commands_go_out_in_order_and_spaced_apart();
    never_polls_after_the_commands_are_done();
    a_failed_write_is_not_retried();
    each_new_connection_gets_one_more_attempt();
    a_repeated_connect_event_cannot_double_send();
    a_drop_loop_never_sees_a_command();
    a_command_followed_by_a_drop_is_blocked_for_good();
    a_drop_long_after_a_command_blames_nothing();
    the_real_wire_refuses_everything_and_never_touches_the_device();
    exit_3d_holds_the_stream_and_is_never_sent_mid_stream();
    the_unmapped_wire_never_leaves_the_stream_held();
    a_mode_makes_exit_3d_redundant();
    flags_parse_strictly();
    events_format_as_one_structured_line();
    if (failures) std::printf("%d check(s) failed\n", failures);
    else std::printf("display_test: all checks passed\n");
    return failures ? 1 : 0;
}
