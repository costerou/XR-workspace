// VsyncClock (vsync.hpp) against hand-made streams: lock, stamp rounding, missed events, a mode change, a clock that goes
// back, the floors, and the wire. luma_vsync_bench is the whole-daemon run with a noisy bus.
#include "luma/vsync.hpp"
#include <bit>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__func__) + ":" + std::to_string(__LINE__) + ": " #x); } while (0)
using namespace luma;

namespace {
constexpr std::uint64_t kMs = 1'000'000;
constexpr std::uint64_t kHostMinusDevice = 2'000'000 * kMs;  // the host's clock is 2000 s ahead of the device's
constexpr std::uint64_t kSteadyMinusHost = 777 * kMs;        // and the feed's 777 ms ahead of the host's
constexpr std::uint64_t kLink = 180'000;                     // the fastest an IMU packet comes through
std::uint64_t ticked(double device_ns) { return std::uint64_t(device_ns) / 100'000 * 100'000; }

// IMU at 1 kHz from `from` to `to` (device ns), the fastest arriving kLink after its stamp, the rest later.
void imu(VsyncClock& c, std::uint64_t from, std::uint64_t to) {
    for (std::uint64_t d = from; d < to; d += kMs) {
        const std::uint64_t host = d + kHostMinusDevice + kLink + (d / kMs % 7) * 60'000;
        c.on_imu(d, host + kSteadyMinusHost, host);
    }
}
// Events of a panel whose scan-outs are phase + k * period on the device clock, stamped with 100 us ticks.
void panel(VsyncClock& c, double phase, double period, int from, int to, int skip_every = 0) {
    for (int k = from; k < to; ++k) {
        if (skip_every && k % skip_every == 0) continue;
        c.on_event(ticked(phase + k * period));
    }
}
// The published anchor against the true scan-outs: how far off the nearest, ns.
double anchor_error(const VsyncState& s, double phase, double period) {
    const double truth_host = double(kHostMinusDevice) + phase;
    const double age = double(s.anchor_host_ns) - truth_host;
    return age - std::round(age / period) * period;
}

void locks_at_120_hz_through_tick_rounding() {
    VsyncClock c;
    const double period = 1e9 / 120, phase = 10'000.0 * kMs + 1'234'567;
    imu(c, 9'000 * kMs, 11'000 * kMs);
    panel(c, phase, period, 0, VsyncClock::kLockAfter - 1);
    CHECK(!c.state().locked);
    panel(c, phase, period, VsyncClock::kLockAfter - 1, 240);
    const VsyncState& s = c.state();
    CHECK(s.locked && s.events == 240 && s.resets == 0);
    CHECK(std::fabs(s.period_ns - period) < 200);  // 100 us rounding over a 1 s line
    CHECK(s.fit_rms_us > 15 && s.fit_rms_us < 45);
    // The fitted scan-out lags the true one by the link floor, and the rounding takes off about 50 us.
    const double e = anchor_error(s, phase, period);
    CHECK(e > double(kLink) - 120'000 && e < double(kLink) + 40'000);
    CHECK(s.anchor_steady_ns == s.anchor_host_ns + kSteadyMinusHost);
}

void bridges_missed_events() {
    VsyncClock c;
    const double period = 1e9 / 60, phase = 10'000.0 * kMs;
    imu(c, 9'000 * kMs, 13'000 * kMs);
    panel(c, phase, period, 0, 200, 7);  // every 7th lost
    CHECK(c.state().locked && c.state().resets == 0);
    CHECK(std::fabs(c.state().period_ns - period) < 200);
    VsyncClock gap;
    imu(gap, 9'000 * kMs, 13'000 * kMs);
    panel(gap, phase, period, 0, 50);
    panel(gap, phase, period, 50 + int(VsyncClock::kMaxSkipped), 100);  // kMaxSkipped in a row lost
    CHECK(gap.state().locked && gap.state().resets == 0);
}

void a_mode_change_starts_again() {
    VsyncClock c;
    imu(c, 9'000 * kMs, 16'000 * kMs);
    panel(c, 10'000.0 * kMs, 1e9 / 60, 0, 120);
    CHECK(c.state().locked);
    // 1.5 s without events, then 120 Hz at a new phase.
    const double phase = 13'500.0 * kMs + 3'300'000;
    CHECK(c.on_event(ticked(phase)));  // the unlock is news
    CHECK(!c.state().locked && c.state().resets == 1);
    panel(c, phase, 1e9 / 120, 1, 120);
    CHECK(c.state().locked && c.state().resets == 1);
    CHECK(std::fabs(c.state().period_ns - 1e9 / 120) < 300);
    CHECK(std::fabs(anchor_error(c.state(), phase, 1e9 / 120)) < 400'000);
    // One event far off the line (a third of a period) also starts it again.
    const double late = phase + 120 * (1e9 / 120) + (1e9 / 120) / 3;
    c.on_event(ticked(late));
    CHECK(!c.state().locked && c.state().resets == 2);
}

void a_clock_going_back_starts_again() {
    VsyncClock c;
    imu(c, 9'000 * kMs, 12'000 * kMs);
    panel(c, 10'000.0 * kMs, 1e9 / 120, 0, 60);
    CHECK(c.state().locked);
    c.on_event(5'000 * kMs);
    CHECK(!c.state().locked && c.state().resets == 1);
}

void needs_both_floors_and_a_sane_period() {
    VsyncClock none;  // no IMU yet: the line is good, the host time unknown
    panel(none, 10'000.0 * kMs, 1e9 / 120, 0, 60);
    CHECK(!none.state().locked && none.state().events == 60);
    VsyncClock fast;  // 1 kHz is no display
    imu(fast, 9'000 * kMs, 12'000 * kMs);
    panel(fast, 10'000.0 * kMs, 1e6, 0, 60);
    CHECK(!fast.state().locked);
}

void the_floor_follows_the_newest_second() {
    VsyncClock c;
    imu(c, 9'000 * kMs, 10'000 * kMs);
    // The link gets 300 us faster for good (say, a bus that was busy): the floor must follow within two windows.
    for (std::uint64_t d = 10'000 * kMs; d < 12'000 * kMs; d += kMs) {
        const std::uint64_t host = d + kHostMinusDevice + kLink - 100'000;
        c.on_imu(d, host + kSteadyMinusHost, host);
    }
    const double period = 1e9 / 120, phase = 11'500.0 * kMs;
    panel(c, phase, period, 0, 30);
    const double e = anchor_error(c.state(), phase, period);
    CHECK(c.state().locked && e > double(kLink) - 100'000 - 120'000 && e < double(kLink) - 100'000 + 40'000);
}

void reset_unlocks_and_keeps_the_counts() {
    VsyncClock c;
    imu(c, 9'000 * kMs, 12'000 * kMs);
    panel(c, 10'000.0 * kMs, 1e9 / 120, 0, 60);
    c.on_event(5'000 * kMs);  // one reset
    c.reset();
    CHECK(!c.state().locked && c.state().events == 61 && c.state().resets == 1);
    panel(c, 10'000.0 * kMs, 1e9 / 120, 100, 160);
    CHECK(!c.state().locked);  // the floors went with the connection
    imu(c, 10'800 * kMs, 11'500 * kMs);
    panel(c, 10'000.0 * kMs, 1e9 / 120, 160, 180);
    CHECK(c.state().locked && c.state().resets == 1);
}

void the_wire_carries_every_field() {
    VsyncWire w;
    VsyncState s;
    s.locked = true; s.period_ns = 1e9 / 120; s.anchor_host_ns = 0x0123456789abcdefull; s.events = 0x1'0000'0002ull;
    s.resets = 9; s.fit_rms_us = 28.25f;
    write_vsync(w, s, 1234.5);
    write_vsync(w, s, 1234.5);
    CHECK(w.seq.load() == 4 && w.magic.load() == kVsyncMagic);
    const auto word = [&](int i) { return std::uint64_t(w.words[std::size_t(i)].load()); };
    CHECK((word(0) | word(1) << 32) == s.anchor_host_ns);
    CHECK(std::bit_cast<double>(word(2) | word(3) << 32) == s.period_ns);
    CHECK(std::bit_cast<double>(word(4) | word(5) << 32) == 1234.5);
    CHECK((word(6) | word(7) << 32) == s.events);
    CHECK(word(8) == 1 && word(9) == 9 && std::bit_cast<float>(std::uint32_t(word(10))) == 28.25f);
    CHECK(word(11) == 0 && word(12) == 0 && word(13) == 0);
    w.seq.store(7);  // a writer killed mid-write: the next write still ends even
    write_vsync(w, s, 0);
    CHECK(w.seq.load() % 2 == 0);
}
}

int main() {
    const std::pair<const char*, void (*)()> tests[]{
#define T(name) {#name, name}
        T(locks_at_120_hz_through_tick_rounding), T(bridges_missed_events), T(a_mode_change_starts_again),
        T(a_clock_going_back_starts_again), T(needs_both_floors_and_a_sane_period), T(the_floor_follows_the_newest_second),
        T(reset_unlocks_and_keeps_the_counts), T(the_wire_carries_every_field),
#undef T
    };
    int failed = 0;
    for (const auto& [name, run] : tests) {
        try { run(); std::cout << "ok   " << name << "\n"; }
        catch (const std::exception& e) { std::cout << "FAIL " << name << ": " << e.what() << "\n"; ++failed; }
    }
    if (failed) { std::cout << failed << " failed\n"; return 1; }
    std::cout << "luma vsync passed\n";
}
