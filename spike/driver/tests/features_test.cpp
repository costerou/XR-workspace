// Offline tests for the optional device features. No test opens a USB device:
// the glasses are played by a stub, and the sysfs tree is a temporary folder.
#include "luma/features.hpp"

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <new>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__FILE__ ":") + std::to_string(__LINE__) + ": " #x); } while (0)

using namespace luma::features;
using namespace std::chrono_literals;

static std::atomic<std::uint64_t> g_allocations{0};
void* operator new(std::size_t size) {
    ++g_allocations;
    if (void* p = std::malloc(size)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

namespace {

constexpr Nanos kMs = 1'000'000;
constexpr Nanos kSec = 1'000'000'000;

using Bytes = std::vector<std::uint8_t>;

// Stand-in for protocol.hpp: the V2 frame from elasticjava/viture-v2 PROTOCOL.md.
Bytes encode_v2(std::uint16_t id, const Bytes& payload) {
    unsigned sum = 0;
    for (auto b : payload) sum += b;
    Bytes f{0x10, 0x00, std::uint8_t(id), std::uint8_t(id >> 8), std::uint8_t(payload.size()),
            std::uint8_t(payload.size() >> 8), std::uint8_t(sum), std::uint8_t(sum >> 8)};
    f.insert(f.end(), payload.begin(), payload.end());
    return f;
}

std::vector<Event> drain(Manager& m) {
    std::vector<Event> out;
    while (auto e = m.pop_event()) out.push_back(*e);
    return out;
}

int count(const std::vector<Event>& events, EventKind kind) {
    int n = 0;
    for (const auto& e : events) n += e.kind == kind;
    return n;
}

std::string json(const Event& e) {
    std::array<char, 300> buf{};
    const auto n = format_event(e, buf);
    return std::string(buf.data(), n);
}

// These tests are about the gate's logic, so they use the short windows they were written with. The defaults, which are
// much longer, have their own test.
void short_windows(Config& c) {
    c.settle = std::chrono::milliseconds{2000};
    c.drop_window = std::chrono::milliseconds{3000};
}

Config gen2() {
    Config c;
    short_windows(c);
    std::string err;
    CHECK(parse_flag(c, "--feature-profile=gen2", &err) == FlagResult::Ok);
    return c;
}

Config mapped_control() {
    Config c;
    short_windows(c);
    c.control = true;
    std::string err;
    CHECK(apply_map_override(c.map, "brightness_send=0x1234", &err));
    CHECK(apply_map_override(c.map, "brightness_reply=0x3234", &err));
    CHECK(apply_map_override(c.map, "dimming_send=0x1235", &err));
    c.map.reply_has_status = true;
    return c;
}

// Ticks at 10 Hz for the given span, writing whatever the Manager offers.
// Returns the requests written.
std::vector<OutRequest> run_for(Manager& m, Nanos& now, Nanos span, bool answer = false,
                                std::uint16_t reply_id = 0, const Bytes& reply = {}) {
    std::vector<OutRequest> sent;
    const Nanos end = now + span;
    for (; now < end; now += 100 * kMs) {
        m.tick(now);
        if (auto r = m.next_request(now)) {
            sent.push_back(*r);
            m.on_write_result(true, now);
            if (answer) m.on_message(reply_id, reply, now + kMs);
        }
    }
    return sent;
}

void test_defaults_send_nothing() {
    Config c;
    for (const auto& f : flag_table()) {
        if (f.sends_usb) CHECK(!f.default_on);
    }
    CHECK(!c.firmware_query && !c.wear_query && !c.control && !c.brightness && !c.dimming);
    CHECK(!c.discovery && !c.still_wear && !c.auto_pause && !c.oled_saver && !c.ipc);
    CHECK(c.state_events && c.wear_events && c.temperature && c.usb_identity);

    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    CHECK(run_for(m, now, 3600 * kSec).empty());
    CHECK(m.commands_sent_total() == 0);
}

void test_luma_map_has_no_guesses() {
    const auto map = default_map(WireProfile::CarinaV1);
    for (const auto& r : map.routes) CHECK(!r.send && !r.reply);
    CHECK(!map.ev_wear && !map.ev_film);

    Config c;
    c.firmware_query = c.wear_query = c.control = true;
    c.brightness = 3;
    c.dimming = 1;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    CHECK(run_for(m, now, 60 * kSec).empty());
    CHECK(count(drain(m), EventKind::Unavailable) == 4);
    CHECK(m.request_brightness(4) == RequestResult::Unavailable);
    CHECK(m.request_dimming(1) == RequestResult::Unavailable);
}

void test_published_v2_vectors() {
    // Worked examples in PROTOCOL.md.
    CHECK(encode_v2(0x3122, {}) == (Bytes{0x10, 0, 0x22, 0x31, 0, 0, 0, 0}));
    CHECK(encode_v2(0x5122, {0, 3}) == (Bytes{0x10, 0, 0x22, 0x51, 2, 0, 3, 0, 0, 3}));
    const auto map = default_map(WireProfile::Gen2V2);
    CHECK(map.routes[0].send == 0x3003 && map.routes[0].reply == 0x5003);
    CHECK(map.routes[1].send == 0x3321 && map.routes[1].reply == 0x5321);
    // Setters are not in any public source.
    CHECK(!map.routes[2].send && !map.routes[3].send);
}

void test_firmware_once_per_connection() {
    auto c = gen2();
    c.firmware_query = true;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    Bytes reply{0};
    for (char ch : std::string("30.0.00.002_20260804")) reply.push_back(std::uint8_t(ch));
    auto sent = run_for(m, now, 3600 * kSec, true, 0x5003, reply);
    CHECK(sent.size() == 1);
    CHECK(sent[0].id == 0x3003 && sent[0].payload_len == 0);
    CHECK(encode_v2(sent[0].id, {}) == (Bytes{0x10, 0, 0x03, 0x30, 0, 0, 0, 0}));
    CHECK(m.firmware() == "30.0.00.002_20260804");
    CHECK(count(drain(m), EventKind::Firmware) == 1);

    // A new connection may ask once more.
    m.on_disconnect(now);
    now += 60 * kSec;
    m.on_connect(now);
    CHECK(m.firmware().empty());
    sent = run_for(m, now, 3600 * kSec, true, 0x5003, reply);
    CHECK(sent.size() == 1);
    CHECK(m.commands_sent_total() == 2);
}

void test_default_windows_are_the_safer_ones() {
    const Config c;
    CHECK(c.settle == std::chrono::minutes{5});
    CHECK(c.drop_window == std::chrono::seconds{60});
}

void test_waits_for_a_settled_connection() {
    auto c = gen2();
    c.firmware_query = c.wear_query = true;
    Manager m(c);
    m.on_connect(5 * kSec);
    CHECK(!m.next_request(5 * kSec));
    CHECK(!m.next_request(6 * kSec + 999 * kMs));
    auto first = m.next_request(7 * kSec);
    CHECK(first && first->kind == Request::QueryFirmware);
    m.on_write_result(true, 7 * kSec);
    CHECK(!m.next_request(7 * kSec + 500 * kMs));  // one at a time
    m.on_message(0x5003, Bytes{0, 'v'}, 7 * kSec + 10 * kMs);
    CHECK(!m.next_request(7 * kSec + 100 * kMs));  // paced
    auto second = m.next_request(7 * kSec + 250 * kMs);
    CHECK(second && second->kind == Request::QueryWear);
}

void test_flapping_connection_sends_nothing() {
    auto c = gen2();
    c.firmware_query = true;
    Manager m(c);
    Nanos now = 0;
    for (int i = 0; i < 1000; ++i) {
        m.on_connect(now);
        now += kSec;  // shorter than the settle time
        CHECK(!m.next_request(now));
        m.on_disconnect(now);
        now += kSec;
    }
    CHECK(m.commands_sent_total() == 0);
}

void test_timeout_stops_commands_and_never_retries() {
    auto c = gen2();
    c.firmware_query = c.wear_query = true;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    const auto sent = run_for(m, now, 3600 * kSec);  // no answer, ever
    CHECK(sent.size() == 1);
    CHECK(m.commands_stopped() && !m.commands_latched_off());
    auto events = drain(m);
    CHECK(count(events, EventKind::TimedOut) == 1 && count(events, EventKind::Tripped) == 1);

    // A late reply changes nothing.
    m.on_message(0x5003, Bytes{0, 'v'}, now);
    CHECK(m.firmware().empty());
}

void test_write_failure_stops_commands() {
    auto c = gen2();
    c.firmware_query = c.wear_query = true;
    Manager m(c);
    m.on_connect(0);
    CHECK(m.next_request(3 * kSec));
    m.on_write_result(false, 3 * kSec);
    CHECK(m.commands_stopped());
    CHECK(!m.next_request(100 * kSec));
}

void test_drop_after_command_latches_until_restart() {
    auto c = gen2();
    c.firmware_query = true;
    {
        Manager m(c);
        m.on_connect(0);
        CHECK(m.next_request(3 * kSec));
        m.on_write_result(true, 3 * kSec);
        m.on_disconnect(5 * kSec);  // two seconds after the command
        CHECK(m.commands_latched_off());
        CHECK(count(drain(m), EventKind::LatchedOff) == 1);
        m.on_connect(10 * kSec);
        Nanos now = 10 * kSec;
        CHECK(run_for(m, now, 3600 * kSec).empty());
        CHECK(m.commands_sent_total() == 1);
    }
    {
        Manager m(c);
        m.on_connect(0);
        CHECK(m.next_request(3 * kSec));
        m.on_write_result(true, 3 * kSec);
        m.on_message(0x5003, Bytes{0, 'v'}, 3 * kSec);
        m.on_disconnect(60 * kSec);  // long after: unrelated
        CHECK(!m.commands_latched_off());
    }
}

void test_rejected_is_not_retried() {
    auto c = gen2();
    c.firmware_query = true;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    CHECK(run_for(m, now, 600 * kSec, true, 0x5003, Bytes{4}).size() == 1);
    const auto events = drain(m);
    CHECK(count(events, EventKind::Rejected) == 1);
    CHECK(!m.commands_stopped());
    CHECK(m.firmware().empty());
}

void test_brightness_control() {
    {
        Config off;
        Manager m(off);
        m.on_connect(0);
        CHECK(m.request_brightness(3) == RequestResult::Disabled);
    }
    auto c = mapped_control();
    Manager m(c);
    CHECK(m.request_brightness(3) == RequestResult::NotConnected);
    m.on_connect(0);
    CHECK(m.request_brightness(9) == RequestResult::OutOfRange);
    CHECK(m.request_brightness(-1) == RequestResult::OutOfRange);
    CHECK(m.request_dimming(2) == RequestResult::OutOfRange);  // Luma Series: [0, 1]

    // Latest wins while waiting; only one command goes out.
    CHECK(m.request_brightness(3) == RequestResult::Queued);
    CHECK(m.request_brightness(5) == RequestResult::Queued);
    auto r = m.next_request(3 * kSec);
    CHECK(r && r->id == 0x1234 && r->payload_len == 1 && r->payload[0] == 5);
    m.on_write_result(true, 3 * kSec);

    // A newer request while that one is on the wire must not corrupt the record.
    CHECK(m.request_brightness(6) == RequestResult::Queued);
    m.on_message(0x3234, Bytes{0}, 3 * kSec + kMs);
    CHECK(m.request_brightness(5) == RequestResult::AlreadySet);
    CHECK(!m.next_request(10 * kSec));
    CHECK(m.request_brightness(5) == RequestResult::AlreadySet);
    CHECK(m.commands_sent_total() == 1);

    // No reply route: fire and forget.
    CHECK(m.request_dimming(1) == RequestResult::Queued);
    r = m.next_request(11 * kSec);
    CHECK(r && r->id == 0x1235 && r->payload[0] == 1);
    m.on_write_result(true, 11 * kSec);
    CHECK(m.request_dimming(1) == RequestResult::AlreadySet);
    m.tick(100 * kSec);
    CHECK(!m.commands_stopped());
}

void test_startup_brightness_once() {
    auto c = mapped_control();
    c.control = false;
    c.brightness = 4;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 0;
    const auto sent = run_for(m, now, 3600 * kSec, true, 0x3234, Bytes{0});
    CHECK(sent.size() == 1 && sent[0].payload[0] == 4);
}

void test_command_budget() {
    auto c = mapped_control();
    c.max_commands_per_connection = 3;
    Manager m(c);
    m.on_connect(0);
    Nanos now = 3 * kSec;
    int level = 0;
    int sent = 0;
    for (int i = 0; i < 100; ++i) {
        (void)m.request_brightness(level = (level + 1) % 8);
        if (m.next_request(now)) {
            ++sent;
            m.on_write_result(true, now);
            m.on_message(0x3234, Bytes{0}, now);
        }
        now += kSec;
    }
    CHECK(sent == 3);
    CHECK(m.request_brightness((level + 1) % 8) == RequestResult::BudgetExhausted);

    // The cap also holds for commands queued at connect.
    auto start = mapped_control();
    std::string err;
    CHECK(apply_map_override(start.map, "firmware_send=0x1236", &err));
    CHECK(apply_map_override(start.map, "wear_send=0x1237", &err));
    start.firmware_query = start.wear_query = true;
    start.brightness = 1;
    start.dimming = 1;
    start.max_commands_per_connection = 2;
    Manager capped(start);
    capped.on_connect(0);
    Nanos t = 0;
    CHECK(run_for(capped, t, 600 * kSec).size() == 2);
}

void test_map_override() {
    WireMap map = default_map(WireProfile::CarinaV1);
    std::string err;
    CHECK(apply_map_override(map, "ev_wear=0x0305", &err) && map.ev_wear == 0x0305);
    CHECK(apply_map_override(map, "firmware_send=0x0007", &err));
    CHECK(apply_map_override(map, "firmware_reply=0", &err) && map.routes[0].reply == 0);
    CHECK(apply_map_override(map, "reply_status=0", &err) && !map.reply_has_status);
    CHECK(apply_map_override(map, "film_levels=8", &err) && map.film_levels == 8);
    for (const char* refused : {"wear_send=0x15", "brightness_send=0x0301", "dimming_send=0"}) {
        CHECK(!apply_map_override(map, refused, &err));
        CHECK(err.find("IMU") != std::string::npos);
    }
    for (const char* bad : {"nonsense", "nope=1", "ev_wear=", "ev_wear=0x10000", "ev_wear=zz", "firmware=1",
                            "wearing_send=1", "reply_status=2", "film_levels=0"})
        CHECK(!apply_map_override(map, bad, &err));
}

void test_parse_flags() {
    Config c;
    std::string err;
    CHECK(parse_flag(c, "--verbose", &err) == FlagResult::NotMine);
    CHECK(parse_flag(c, "--feature-discovery", &err) == FlagResult::Ok && c.discovery);
    CHECK(parse_flag(c, "--no-feature-wear-events", &err) == FlagResult::Ok && !c.wear_events);
    CHECK(parse_flag(c, "--feature-brightness=8", &err) == FlagResult::Ok && c.brightness == 8);
    CHECK(parse_flag(c, "--no-feature-brightness", &err) == FlagResult::Ok && !c.brightness);
    CHECK(parse_flag(c, "--feature-dimming=1", &err) == FlagResult::Ok && c.dimming == 1);
    CHECK(parse_flag(c, "--feature-wear-off-ms=500", &err) == FlagResult::Ok && c.wear_off_debounce == 500ms);
    CHECK(parse_flag(c, "--feature-saver-delay-ms=0", &err) == FlagResult::Ok && c.saver_delay == 0ms);
    CHECK(parse_flag(c, "--feature-still-off-s=30", &err) == FlagResult::Ok && c.still_off == 30s);
    for (const char* bad : {"--feature-brightness=9", "--feature-brightness=-1", "--feature-brightness",
                            "--feature-dimming=2", "--feature-discovery=1", "--feature-nope", "--feature-nope=1",
                            "--no-feature-nope", "--feature-profile=x", "--feature-map=x", "--feature-still-off-s=1",
                            "--feature-wear-off-ms=-1", "--feature-saver-delay-ms=-1"})
        CHECK(parse_flag(c, bad, &err) == FlagResult::Bad);
    CHECK(parse_flag(c, "--feature-map=film_levels=8", &err) == FlagResult::Ok);
    CHECK(parse_flag(c, "--feature-dimming=8", &err) == FlagResult::Ok);
    // Switching profile afterwards would silently lose the entry above.
    CHECK(parse_flag(c, "--feature-profile=gen2", &err) == FlagResult::Bad);
    const auto usage = flag_usage();
    for (const auto& f : flag_table()) CHECK(usage.find(f.name) != std::string::npos);
}

void test_state_events() {
    Manager m(Config{});
    m.on_connect(0);
    const auto press = [&](std::uint16_t id, std::uint8_t v, Nanos t) { m.on_message(id, Bytes{v}, t); };
    press(0x0301, 3, kMs);
    press(0x0301, 3, 2 * kMs);  // a repeat is not a press
    press(0x0301, 4, 3 * kMs);
    press(0x0304, 2, 4 * kMs);
    press(0x0302, 0x32, 5 * kMs);
    auto events = drain(m);
    CHECK(events.size() == 4);
    CHECK(events[0].kind == EventKind::Brightness && events[0].value == 3);
    CHECK(events[1].kind == EventKind::Brightness && events[1].value == 4);
    CHECK(events[2].kind == EventKind::Volume && events[2].value == 2);
    CHECK(events[3].kind == EventKind::DisplayMode && events[3].value == 0x32);

    // Bad input from the wire never becomes an event.
    m.on_message(0x0301, {}, 6 * kMs);
    press(0x0301, 200, 7 * kMs);
    CHECK(drain(m).empty());
    CHECK(m.malformed() == 2);

    // A film id, once somebody maps one.
    auto c = Config{};
    std::string err;
    CHECK(apply_map_override(c.map, "ev_film=0x0333", &err));
    c.state_events = false;
    Manager quiet(c);
    quiet.on_connect(0);
    quiet.on_message(0x0301, Bytes{3}, kMs);
    quiet.on_message(0x0333, Bytes{1}, kMs);
    CHECK(drain(quiet).empty());
    c.state_events = true;
    Manager film(c);
    film.on_connect(0);
    film.on_message(0x0333, Bytes{1}, kMs);
    film.on_message(0x0333, Bytes{9}, kMs);
    const auto film_events = drain(film);
    CHECK(film_events.size() == 1 && film_events[0].kind == EventKind::Dimming);
    CHECK(film.malformed() == 1);
}

Config wear_config() {
    Config c;
    std::string err;
    CHECK(apply_map_override(c.map, "ev_wear=0x0305", &err));
    c.auto_pause = true;
    c.oled_saver = true;
    return c;
}

void test_wear_debounce_and_actions() {
    Manager m(wear_config());
    m.on_connect(0);
    const auto wear = [&](std::uint8_t v, Nanos t) { m.on_message(0x0305, Bytes{v}, t); };

    wear(1, kSec);
    m.tick(kSec + 100 * kMs);
    CHECK(!m.worn());
    m.tick(kSec + 200 * kMs);
    CHECK(m.worn() == true);
    auto events = drain(m);
    CHECK(events.size() == 1 && events[0].kind == EventKind::Wear && events[0].value == 1);

    // Pushing the glasses up for a moment does nothing.
    wear(0, 10 * kSec);
    m.tick(11 * kSec);
    wear(1, 12 * kSec);
    m.tick(13 * kSec);
    m.tick(30 * kSec);
    CHECK(m.worn() == true && drain(m).empty());

    // Really off.
    wear(0, 40 * kSec);
    m.tick(43 * kSec);
    CHECK(m.worn() == false);
    events = drain(m);
    CHECK(count(events, EventKind::Wear) == 1 && count(events, EventKind::Pause) == 1);
    CHECK(count(events, EventKind::SaverOn) == 0);
    m.tick(72 * kSec);
    CHECK(drain(m).empty());
    m.tick(73 * kSec);
    CHECK(count(drain(m), EventKind::SaverOn) == 1);

    // Back on.
    wear(1, 80 * kSec);
    m.tick(81 * kSec);
    events = drain(m);
    CHECK(count(events, EventKind::Resume) == 1 && count(events, EventKind::SaverOff) == 1);

    // Off, back before the saver is due: it never fires.
    wear(0, 90 * kSec);
    m.tick(93 * kSec);
    wear(1, 95 * kSec);
    m.tick(96 * kSec);
    drain(m);
    m.tick(200 * kSec);
    CHECK(drain(m).empty());
    CHECK(m.commands_sent_total() == 0);  // none of this touched USB
}

void test_disconnect_never_leaves_xr_paused() {
    Manager m(wear_config());
    m.on_connect(0);
    m.on_message(0x0305, Bytes{0}, kSec);
    m.tick(4 * kSec);
    m.tick(40 * kSec);
    auto events = drain(m);
    CHECK(count(events, EventKind::Pause) == 1 && count(events, EventKind::SaverOn) == 1);
    m.on_disconnect(50 * kSec);
    events = drain(m);
    CHECK(count(events, EventKind::Resume) == 1 && count(events, EventKind::SaverOff) == 1);
    CHECK(!m.worn());

    // Same when the transport misses the disconnect and reconnects straight away.
    m.on_connect(60 * kSec);
    m.on_message(0x0305, Bytes{0}, 61 * kSec);
    m.tick(64 * kSec);
    drain(m);
    m.on_connect(70 * kSec);
    CHECK(count(drain(m), EventKind::Resume) == 1);
}

void test_wear_query_reply() {
    auto c = gen2();
    c.wear_query = true;
    Manager m(c);
    m.on_connect(0);
    CHECK(m.next_request(3 * kSec));
    m.on_write_result(true, 3 * kSec);
    m.on_message(0x5321, Bytes{0, 1}, 3 * kSec);
    m.tick(4 * kSec);
    CHECK(m.worn() == true);
}

void test_still_watch() {
    StillWatch watch;
    const Nanos dt = kMs;
    Nanos t = 1000 * kMs;
    unsigned seed = 7;
    const auto noise = [&](float amplitude) {  // repeatable, white-ish
        seed = seed * 1664525u + 1013904223u;
        return amplitude * (float(seed >> 8) / float(1u << 24) * 2.f - 1.f);
    };
    // 2 s of glasses on a desk: gyro bias plus noise, gravity plus noise.
    const auto still = [&](Nanos span) {
        for (const Nanos end = t + span; t < end; t += dt)
            watch.update(t, {0.02f + noise(0.01f), -0.03f + noise(0.01f), noise(0.01f)},
                         {noise(0.05f), 9.81f + noise(0.05f), noise(0.05f)});
    };
    still(2 * kSec);
    const auto first = watch.last_motion_ns();
    CHECK(first <= 1000 * kMs + 200 * kMs);  // bias and noise do not count

    // A slow head turn counts.
    for (const Nanos end = t + 200 * kMs; t < end; t += dt) {
        const float s = float(end - t) / float(200 * kMs);
        watch.update(t, {0.2f * s, 0.f, 0.f}, {0.f, 9.81f, 0.f});
    }
    const auto turned = watch.last_motion_ns();
    CHECK(turned > first + 2 * kSec);
    still(2 * kSec);
    const auto later = watch.last_motion_ns();
    CHECK(later <= turned + 200 * kMs);

    // A NaN from a bad packet reads as motion: fail towards "worn".
    const float nan = std::nanf("");
    for (const Nanos end = t + 100 * kMs; t < end; t += dt)
        watch.update(t, {nan, -0.03f, 0.f}, {0.f, 9.81f, 0.f});
    CHECK(watch.last_motion_ns() > later);
}

void test_stillness_as_wear_source() {
    Config c;
    std::string err;
    CHECK(apply_map_override(c.map, "ev_wear=0x0305", &err));
    c.still_wear = true;
    Manager m(c);
    m.on_connect(0);
    m.tick(kSec, Nanos{0});
    m.tick(2 * kSec, Nanos{0});
    CHECK(m.worn() == true);  // starts worn: nothing has been still long enough
    drain(m);
    m.tick(119 * kSec, 0);
    CHECK(m.worn() == true);
    m.tick(120 * kSec, 0);
    m.tick(122 * kSec, 0);
    CHECK(m.worn() == true);  // debounce still applies
    m.tick(123 * kSec, 0);
    CHECK(m.worn() == false);
    auto events = drain(m);
    CHECK(events.size() == 1 && events[0].wear_source == WearSource::Stillness);
    CHECK(json(events[0]).find("stillness") != std::string::npos);

    // Any motion means worn again.
    m.tick(130 * kSec, 129 * kSec);
    m.tick(131 * kSec, 129 * kSec);
    CHECK(m.worn() == true);

    // The real sensor outranks the guess for the rest of the connection.
    m.on_message(0x0305, Bytes{1}, 140 * kSec);
    drain(m);
    m.tick(1000 * kSec, 0);
    m.tick(1100 * kSec, 0);
    CHECK(m.worn() == true);

    // Off by default: stillness is ignored.
    Manager plain(Config{});
    plain.on_connect(0);
    plain.tick(1000 * kSec, 0);
    plain.tick(2000 * kSec, 0);
    CHECK(!plain.worn());
}

void test_discovery() {
    Config c;
    Manager off(c);
    off.on_connect(0);
    off.on_message(0x0999, Bytes{1}, 0);
    CHECK(drain(off).empty());

    c.discovery = true;
    Manager m(c);
    m.on_connect(0);
    m.on_message(0x0999, Bytes{1, 2}, 0);
    m.on_message(0x0999, Bytes{1, 2}, 1);  // unchanged
    auto events = drain(m);
    CHECK(events.size() == 1 && events[0].kind == EventKind::Discovered);
    const auto line = json(events[0]);
    CHECK(line.find(R"("id":"0x0999")") != std::string::npos && line.find(R"("data":"0102")") != std::string::npos);

    // A chatty id is capped; a flood of ids is capped.
    for (std::uint8_t i = 2; i < 100; ++i) m.on_message(0x0999, Bytes{i}, i);
    CHECK(drain(m).size() == 7);
    for (unsigned id = 0x2000; id < 0x2400; ++id) {
        m.on_message(std::uint16_t(id), Bytes{1}, id);
        if (id % 16 == 0) drain(m);
    }
    CHECK(drain(m).size() <= 32);

    // Long payloads are cut, not overrun.
    Manager big(c);
    big.on_connect(0);
    big.on_message(0x0123, Bytes(60, 0xAB), 0);
    const auto e = *big.pop_event();
    CHECK(e.value == 60 && e.data_len == 16);
}

void test_temperature() {
    Manager m(Config{});
    m.on_temperature(35.f, kSec);
    m.on_temperature(35.5f, 100 * kSec);  // under a degree of change
    m.on_temperature(40.f, 2 * kSec);     // too soon
    m.on_temperature(NAN, 300 * kSec);
    m.on_temperature(900.f, 300 * kSec);
    CHECK(drain(m).size() == 1);
    m.on_temperature(40.f, 300 * kSec);
    const auto events = drain(m);
    CHECK(events.size() == 1 && events[0].celsius == 40.f);
    CHECK(json(events[0]).find(R"("celsius":40.0)") != std::string::npos);

    Config c;
    c.temperature = false;
    Manager off(c);
    off.on_temperature(35.f, kSec);
    CHECK(drain(off).empty());
}

void test_event_ring_overflow() {
    Manager m(Config{});
    m.on_connect(0);
    for (int i = 0; i < 100; ++i) m.on_message(0x0304, Bytes{std::uint8_t(i)}, Nanos(i));
    const auto events = drain(m);
    CHECK(events.size() == 33);
    CHECK(events[0].value == 68 && events[31].value == 99);  // newest kept
    CHECK(events[32].kind == EventKind::EventsDropped && events[32].value == 68);
    CHECK(!m.pop_event());
}

void test_format_event() {
    auto c = gen2();
    c.firmware_query = true;
    Manager m(c);
    m.on_connect(0);
    CHECK(m.next_request(3 * kSec));
    m.on_write_result(true, 3 * kSec);
    // Bytes from the device cannot break out of the JSON string.
    m.on_message(0x5003, Bytes{0, '1', '"', ',', '\\', 0x07, 0xFF, '2', 0, 'x'}, 3 * kSec);
    CHECK(m.firmware() == "1,2");
    Event firmware;
    for (const auto& e : drain(m)) {
        if (e.kind == EventKind::Firmware) firmware = e;
    }
    CHECK(json(firmware) == R"({"src":"luma-feature","ev":"firmware","t_ns":3000000000,"version":"1,2"})");

    for (int k = 0; k <= int(EventKind::EventsDropped); ++k) {
        Event e;
        e.kind = EventKind(k);
        const auto line = json(e);
        CHECK(line.starts_with("{\"src\":") && line.ends_with("}"));
    }
    std::array<char, 16> tiny{};
    CHECK(format_event(firmware, tiny) == tiny.size() - 1 && tiny.back() == '\0');
    CHECK(format_event(firmware, std::span<char>{}) == 0);
}

struct TempDir {
    std::filesystem::path path;
    TempDir() {
        std::string name = (std::filesystem::temp_directory_path() / "luma-features-XXXXXX").string();
        CHECK(mkdtemp(name.data()));
        path = name;
    }
    ~TempDir() {
        std::error_code ec;
        std::filesystem::remove_all(path, ec);
    }
};

void write_text(const std::filesystem::path& file, const std::string& text) {
    std::ofstream(file) << text << '\n';
}

void test_usb_identity() {
    TempDir dir;
    const auto add = [&](const char* busid, const char* vid, const char* pid, const char* speed) {
        const auto d = dir.path / busid;
        std::filesystem::create_directories(d);
        write_text(d / "idVendor", vid);
        write_text(d / "idProduct", pid);
        write_text(d / "product", "VITURE \"Luma\" Ultra XR GLASSES");
        write_text(d / "bcdDevice", "0001");
        write_text(d / "speed", speed);
        write_text(d / "serial", "SECRET-SERIAL");
    };
    add("5-2.3", "35ca", "1102", "12");
    add("5-2.1", "35ca", "1104", "480");
    std::filesystem::create_directories(dir.path / "usb5");  // a root hub with no files

    const auto id = find_usb_identity(dir.path, 0x35ca, 0x1104);
    CHECK(id && id->busid == "5-2.1" && id->speed_mbps == 480 && id->bcd_device == 1);
    CHECK(!find_usb_identity(dir.path, 0x35ca, 0x9999));
    CHECK(!find_usb_identity(dir.path / "missing", 0x35ca, 0x1104));

    Manager m(Config{});
    m.on_identity(*id, kSec);
    auto events = drain(m);
    CHECK(events.size() == 1);
    const auto line = json(events[0]);
    CHECK(line.find("SECRET") == std::string::npos);
    CHECK(line.find(R"("bcd_device":"0001")") != std::string::npos);
    CHECK(line.find("Luma Ultra") != std::string::npos);  // the quotes were cleaned

    auto slow = *id;
    slow.speed_mbps = 12;
    m.on_identity(slow, kSec);
    CHECK(count(drain(m), EventKind::LinkDegraded) == 1);

    Config c;
    c.usb_identity = false;
    Manager off(c);
    off.on_identity(*id, kSec);
    CHECK(drain(off).empty());
}

void test_event_socket() {
    TempDir dir;
    // Relative, so a long TMPDIR cannot pass the 108-byte socket path limit.
    const auto home = std::filesystem::current_path();
    std::filesystem::current_path(dir.path);
    const std::filesystem::path path = "events.sock";

    EventSocket closed;
    CHECK(!closed.send("x") && closed.dropped() == 1);
    CHECK(!closed.open(std::filesystem::path(std::string(200, 'a'))));

    EventSocket sender;
    CHECK(sender.open(path));
    CHECK(!sender.send("nobody listening") && sender.dropped() == 1);

    const int listener = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK, 0);
    CHECK(listener >= 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strcpy(addr.sun_path, path.c_str());
    CHECK(::bind(listener, reinterpret_cast<const sockaddr*>(&addr), sizeof addr) == 0);

    CHECK(sender.send("hello"));
    char buf[32] = {};
    CHECK(::recv(listener, buf, sizeof buf, 0) == 5 && std::string_view(buf) == "hello");

    // A listener that stops reading must never block the sender.
    int accepted = 0;
    for (int i = 0; i < 100000; ++i) accepted += sender.send("flood") ? 1 : 0;
    CHECK(sender.dropped() > 0 && accepted < 100000);
    ::close(listener);
    std::filesystem::current_path(home);

    ::setenv("XDG_RUNTIME_DIR", "/run/user/1000", 1);
    CHECK(default_event_socket_path() == "/run/user/1000/xr-luma/events.sock");
    ::unsetenv("XDG_RUNTIME_DIR");
    CHECK(default_event_socket_path().empty());
}

void test_no_allocation_on_message_paths() {
    Config c = wear_config();
    c.discovery = true;
    Manager m(c);
    m.on_connect(0);
    StillWatch watch;
    const std::array<std::uint8_t, 8> payload{1, 2, 3};
    const auto before = g_allocations.load();
    for (Nanos i = 0; i < 100000; ++i) {
        const Nanos t = i * kMs;
        watch.update(t, {0.1f, 0.f, 0.f}, {0.f, 9.8f, 0.f});
        m.on_message(std::uint16_t(0x0300 + i % 8), payload, t);
        m.tick(t, watch.last_motion_ns());
        (void)m.next_request(t);
        while (m.pop_event()) {
        }
    }
    CHECK(g_allocations.load() == before);
}

void test_docs_cover_every_flag() {
    std::ifstream in(LUMA_FEATURES_DOC);
    CHECK(in.good());
    std::stringstream text;
    text << in.rdbuf();
    const auto doc = text.str();
    for (const auto& f : flag_table()) {
        CHECK(doc.find("`--feature-" + std::string(f.name)) != std::string::npos);
    }
}

}  // namespace

int main() {
    const struct { const char* name; void (*run)(); } tests[] = {
#define T(n) {#n, n}
        T(test_defaults_send_nothing), T(test_luma_map_has_no_guesses), T(test_published_v2_vectors),
        T(test_firmware_once_per_connection), T(test_default_windows_are_the_safer_ones), T(test_waits_for_a_settled_connection),
        T(test_flapping_connection_sends_nothing), T(test_timeout_stops_commands_and_never_retries),
        T(test_write_failure_stops_commands), T(test_drop_after_command_latches_until_restart),
        T(test_rejected_is_not_retried), T(test_brightness_control), T(test_startup_brightness_once),
        T(test_command_budget), T(test_map_override), T(test_parse_flags), T(test_state_events),
        T(test_wear_debounce_and_actions), T(test_disconnect_never_leaves_xr_paused), T(test_wear_query_reply),
        T(test_still_watch), T(test_stillness_as_wear_source), T(test_discovery), T(test_temperature),
        T(test_event_ring_overflow), T(test_format_event), T(test_usb_identity), T(test_event_socket),
        T(test_no_allocation_on_message_paths), T(test_docs_cover_every_flag),
#undef T
    };
    int failed = 0;
    for (const auto& t : tests) {
        try {
            t.run();
            std::printf("ok   %s\n", t.name);
        } catch (const std::exception& e) {
            std::printf("FAIL %s: %s\n", t.name, e.what());
            ++failed;
        }
    }
    return failed == 0 ? 0 : 1;
}
