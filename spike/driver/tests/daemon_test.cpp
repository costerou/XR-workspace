// The daemon against a simulated device and clock: every failure mode, in moments, with no USB and no IPC. The second half
// runs the real binary as a process (argv[1]) on captures in a private folder; it never opens a device or the live feed.
#include "luma/daemon.hpp"
#include "luma/transport_link.hpp"
#include "luma/wiring.hpp"
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <new>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#pragma GCC diagnostic ignored "-Wmismatched-new-delete"  // the counting pair below is malloc and free on both sides
static std::uint64_t g_allocated = 0;
void* operator new(std::size_t n) { ++g_allocated; if (void* p = std::malloc(n ? n : 1)) return p; throw std::bad_alloc(); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

using namespace luma;
namespace {
int g_failures = 0;
std::uint64_t allocations();
#define CHECK(x) do { if (!(x)) { ++g_failures; std::cerr << "  FAILED " << __FILE__ << ':' << __LINE__ << ": " #x "\n"; } } while (0)
constexpr std::uint64_t kMs = 1'000'000;

struct Span { std::uint64_t from_ms, to_ms; bool has(std::uint64_t t_ms) const { return t_ms >= from_ms && t_ms < to_ms; } };
bool within(const std::vector<Span>& spans, std::uint64_t t_ms) {
    for (const Span& s : spans) if (s.has(t_ms)) return true;
    return false;
}

// The glasses at 1 kHz. The script says when they are off the bus, up but silent, sending junk, or refusing commands.
struct Device {
    std::uint64_t now = 1'000 * kMs;  // clock, ns; simulated time starts at t=1 s
    std::uint64_t base = 1'000 * kMs;
    std::vector<Span> absent, silent, junk, refuse;  // ms since start of the test
    bool restart_clock_on_return = false;
    bool streaming = false;  // needs a start; a drop ends it, closing the link does not
    std::uint8_t stream_byte = 0x01;  // the command that starts it
    std::uint64_t clock_zero = 0;
    std::vector<std::uint64_t> stamp_override;  // raw device stamps for the first samples, then the clock
    std::uint64_t run_ms = 0;
    std::string why_absent = "absent";
    std::atomic<bool> stop{false};
    std::uint64_t ms() const { return (now - base) / kMs; }
    void tick() { if (ms() >= run_ms) stop = true; }
};

class FakeLink final : public Link {
public:
    explicit FakeLink(Device& d) : d_(d) {}
    std::vector<std::vector<std::uint8_t>> sent;
    std::vector<std::uint64_t> sent_ms;
    int opens = 0, closes = 0;
    bool open_now = false, hotplug = false;
    std::uint64_t allocations_in_window = 0, at_window_start = 0;
    std::uint64_t reopened_ms = 0;
    bool open(std::string& why) override {
        CHECK(!open_now);
        ++opens;
        d_.tick();
        if (within(d_.absent, d_.ms())) { why = d_.why_absent; d_.streaming = false; was_gone_ = true; return false; }
        if (was_gone_) { was_gone_ = false; if (d_.restart_clock_on_return) d_.clock_zero = d_.now; }
        open_now = true; reopened_ms = d_.ms();
        return true;
    }
    ReadResult read(std::span<std::uint8_t> into, milliseconds timeout) override {
        CHECK(open_now);
        const std::uint64_t next = d_.ms() + 1;
        if (within(d_.absent, next)) { d_.now += kMs; d_.tick(); was_gone_ = true; d_.streaming = false; return {ReadStatus::gone, 0}; }
        if (within(d_.silent, next) || !d_.streaming) {
            for (auto left = timeout.count(); left > 0; --left) {  // a wait ends the moment the device goes
                if (within(d_.absent, d_.ms() + 1)) break;
                d_.now += kMs; d_.tick();
            }
            return {ReadStatus::timeout, 0};
        }
        d_.now += kMs;
        d_.tick();
        if (d_.ms() == 2000) at_window_start = allocations();
        if (d_.ms() == 3000) allocations_in_window = allocations() - at_window_start;
        if (within(d_.junk, d_.ms())) { into[0] = 0xff; return {ReadStatus::data, 1}; }
        struct { std::uint64_t t; std::uint32_t seq; } packet{d_.now - d_.clock_zero, seq_++};
        if (stamp_next_ < d_.stamp_override.size()) packet.t = d_.stamp_override[stamp_next_++];
        std::memcpy(into.data(), &packet, sizeof packet);
        return {ReadStatus::data, sizeof packet};
    }
    bool write(std::span<const std::uint8_t> bytes) override {
        CHECK(open_now);
        sent.emplace_back(bytes.begin(), bytes.end());
        sent_ms.push_back(d_.ms());
        if (within(d_.refuse, d_.ms())) return false;
        if (bytes.size() == 1 && bytes[0] == d_.stream_byte) d_.streaming = true;
        return true;
    }
    void close() override { CHECK(open_now); open_now = false; ++closes; }
    bool wait_for_device(milliseconds timeout) override {
        // A real hotplug event would end this wait at the moment the device arrives.
        for (auto left = timeout.count(); left > 0; --left) {
            d_.now += kMs; d_.tick();
            if (hotplug && !within(d_.absent, d_.ms())) return true;
        }
        return false;
    }
private:
    Device& d_;
    std::uint32_t seq_ = 0;
    std::size_t stamp_next_ = 0;
    bool was_gone_ = false;
};

struct FakeDecoder final : Decoder {
    int connections = 0;
    std::optional<ImuSample> decode(std::span<const std::uint8_t> bytes) override {
        struct { std::uint64_t t; std::uint32_t seq; } packet;
        if (bytes.size() != sizeof packet) return std::nullopt;
        std::memcpy(&packet, bytes.data(), sizeof packet);
        return ImuSample{packet.t, {}, {}, std::nullopt, packet.seq};
    }
    void new_connection() override { ++connections; }
    std::uint64_t lost_samples() const override { return 7; }
};

struct FusionLog { int made = 0; std::vector<std::uint64_t> steps_ns; std::uint64_t last_t = 0; };
class FakeEstimator final : public Estimator {
public:
    FakeEstimator(FusionLog& log, int settle, int nan_at, int lose_at, Quatf q) : log_(log), settle_(settle), nan_at_(nan_at), lose_at_(lose_at), q_(q) {}
    PoseOut update(const ImuSample& s) override {
        if (log_.last_t) log_.steps_ns.push_back(s.t_ns - log_.last_t);
        log_.last_t = s.t_ns;
        ++count_;
        PoseOut out{q_, s.t_ns, s.seq, count_ >= settle_ && count_ != lose_at_};
        if (count_ == nan_at_) out.q.w = std::nanf("");
        return out;
    }
private:
    FusionLog& log_;
    int settle_, nan_at_, lose_at_, count_ = 0;
    Quatf q_;
};

struct FakeSink final : Sink {
    std::uint64_t published = 0, last_t = 0;
    Quatf last_q{};
    bool backwards = false, stale = false, published_while_stale = false;
    std::vector<bool> changes;
    void publish(const PoseOut& p) override {
        CHECK(p.calibrated);
        if (p.t_ns <= last_t) backwards = true;
        if (stale) published_while_stale = true;
        last_t = p.t_ns; last_q = p.q; ++published;
    }
    void set_stale(bool s) override { stale = s; changes.push_back(s); }
};

struct Rig {
    Device device;
    FakeLink link{device};
    FakeDecoder decoder;
    std::vector<Quatf> poses;  // what each new estimator reports, in order
    FakeSink sink;
    FusionLog fusion;
    std::vector<std::string> lines;
    int settle = 100, nan_at = -1, lose_at = -1;
    std::vector<std::uint8_t> stop_command{0x00}, start_command{0x01};
    std::vector<std::vector<std::uint8_t>> before_start;
    DaemonConfig config{};
    Stats stats{};
    DaemonEnd end{};
    void run(std::uint64_t ms) {
        device.run_ms = ms;
        DaemonParts parts{link, decoder,
            [this] {
                const Quatf q = std::size_t(fusion.made) < poses.size() ? poses[std::size_t(fusion.made)] : Quatf{};
                ++fusion.made;
                return std::make_unique<FakeEstimator>(fusion, settle, nan_at, lose_at, q);
            },
            sink, [this] { return device.now; }, [this](std::string_view l) { lines.emplace_back(l); }, start_command, stop_command, before_start};
        Daemon daemon(std::move(parts), config);
        end = daemon.run(device.stop);
        stats = daemon.stats();
    }
    int count(std::string_view prefix) const {
        int n = 0;
        for (const auto& l : lines) if (l.starts_with(prefix)) ++n;
        return n;
    }
    int index(std::string_view prefix, int from = 0) const {
        for (int i = from; i < int(lines.size()); ++i) if (lines[std::size_t(i)].starts_with(prefix)) return i;
        return -1;
    }
    int starts() const { int n = 0; for (const auto& c : link.sent) if (c == std::vector<std::uint8_t>{0x01}) ++n; return n; }
    // What tracking.py does: ready when the last `calibrated` comes after the last `disconnected` and `calibrating`.
    bool ready() const {
        int ready = -1, lost = -1;
        for (int i = 0; i < int(lines.size()); ++i) {
            if (lines[std::size_t(i)].find("luma: calibrated") != std::string::npos) ready = i;
            if (lines[std::size_t(i)].find("luma: disconnected") != std::string::npos || lines[std::size_t(i)].find("luma: calibrating") != std::string::npos) lost = i;
        }
        return ready > lost;
    }
    void dump() const { for (const auto& l : lines) std::cerr << "    " << l << '\n'; }
};

std::uint64_t allocations() { return g_allocated; }

void test_clean_run() {
    Rig r; r.run(3000);
    CHECK(r.count("luma: connected") == 1);
    CHECK(r.count("luma: calibrating") == 1);
    CHECK(r.count("luma: calibrated restored=0") == 1);
    CHECK(r.index("luma: connected") < r.index("luma: calibrating"));
    CHECK(r.index("luma: calibrating") < r.index("luma: calibrated"));
    CHECK(r.starts() == 1);           // once, in three thousand samples
    CHECK(r.link.sent.size() == 2);   // and the stop, and nothing else
    CHECK(r.link.sent.back() == std::vector<std::uint8_t>{0x00});
    CHECK(r.link.closes == 1 && !r.link.open_now);
    CHECK(r.fusion.made == 1);
    CHECK(r.ready());
    CHECK(r.stats.commands == 2 && r.stats.disconnects == 0);
    CHECK(r.sink.published > 2800 && !r.sink.backwards);
    CHECK(r.end == DaemonEnd::stop_requested);
    CHECK(r.count("luma: stopped reason=signal") == 1);
    CHECK(r.sink.changes.front() == true && r.sink.changes.back() == true);  // stale before any pose and after the last
    CHECK(!r.sink.published_while_stale);
}

void test_nothing_published_before_calibration() {
    Rig r; r.settle = 1000; r.run(500);
    CHECK(r.sink.published == 0);
    CHECK(r.count("luma: calibrated") == 0);
    CHECK(r.sink.changes.size() == 1 && r.sink.changes[0]);
    CHECK(!r.ready());
}

void test_short_drop_keeps_calibration() {
    Rig r; r.device.absent = {{3000, 5000}}; r.run(7500);
    CHECK(r.fusion.made == 1);  // the same estimator: calibration and orientation survive
    CHECK(r.count("luma: disconnected reason=gone") == 1);
    CHECK(r.count("luma: reconnected") == 1);
    CHECK(r.index("luma: reconnected gap_ms=2") >= 0);
    CHECK(r.index("luma: reconnected gap_ms=2") == r.index("luma: reconnected"));
    CHECK(r.lines[std::size_t(r.index("luma: reconnected"))].find("calibration=kept") != std::string::npos);
    CHECK(r.count("luma: calibrating") == 1);  // no second calibration
    CHECK(r.count("luma: calibrated restored=1") == 1);
    CHECK(r.index("luma: disconnected") < r.index("luma: reconnected"));
    CHECK(r.index("luma: reconnected") < r.index("luma: calibrated restored=1"));
    CHECK(r.ready());
    CHECK(r.starts() == 2);  // one per connection
    CHECK(r.link.reopened_ms >= 5000 && r.link.reopened_ms <= 5100);  // found within one rescan
    CHECK(r.link.sent_ms[1] >= 5200 && r.link.sent_ms[1] <= 5320);    // and left alone to settle first
    CHECK(r.decoder.connections == 2);
    CHECK(!r.sink.backwards);
}

void test_ready_goes_false_during_a_drop() {
    Rig r; r.device.absent = {{2000, 100000}}; r.run(3000);
    CHECK(!r.ready());
    CHECK(r.sink.stale);
}

void test_hotplug_event_ends_the_wait() {
    Rig r; r.link.hotplug = true; r.device.absent = {{1000, 8000}}; r.run(9000);
    CHECK(r.link.reopened_ms >= 8000 && r.link.reopened_ms <= 8002);
}

void test_long_drop_recalibrates() {
    Rig r; r.config.calibration_keep = milliseconds(5000); r.device.absent = {{2000, 9000}}; r.run(12000);
    CHECK(r.fusion.made == 2);
    CHECK(r.lines[std::size_t(r.index("luma: reconnected"))].find("calibration=lost") != std::string::npos);
    CHECK(r.count("luma: calibrating") == 2);
    CHECK(r.count("luma: calibrated restored=0") == 2);
    CHECK(r.count("luma: calibrated restored=1") == 0);
    CHECK(r.ready());
    CHECK(!r.sink.backwards);
}

void test_keep_window_boundary() {
    Rig kept; kept.config.calibration_keep = milliseconds(5000); kept.device.absent = {{2000, 6500}}; kept.run(8000);
    CHECK(kept.fusion.made == 1);
    Rig lost; lost.config.calibration_keep = milliseconds(5000); lost.device.absent = {{2000, 7500}}; lost.run(9000);
    CHECK(lost.fusion.made == 2);
    Rig standard; standard.device.absent = {{2000, 200000}}; standard.run(202000);  // over three minutes away: still kept
    CHECK(standard.fusion.made == 1 && standard.ready());
    Rig too_long; too_long.device.absent = {{2000, 400000}}; too_long.run(403000);
    CHECK(too_long.fusion.made == 2);
}

void test_drop_during_calibration_starts_it_over() {
    Rig r; r.settle = 1000; r.device.absent = {{500, 800}}; r.run(3000);
    CHECK(r.fusion.made == 2);
    CHECK(r.lines[std::size_t(r.index("luma: reconnected"))].find("calibration=none") != std::string::npos);
    CHECK(r.count("luma: calibrated restored=0") == 1);
    CHECK(r.ready());
}

void test_glasses_absent_at_start() {
    Rig r; r.device.absent = {{0, 3000}}; r.run(5000);
    CHECK(r.count("luma: connected") == 1);
    CHECK(r.count("luma: disconnected") == 0 && r.count("luma: reconnected") == 0);
    CHECK(r.starts() == 1);  // opening costs nothing on the wire; only the one that worked sent a command
    CHECK(r.link.opens > 20 && r.link.opens < 50);  // looking, not talking
    CHECK(r.count("luma: waiting reason=absent") == 1);  // said once, not thirty times
    CHECK(r.link.sent_ms[0] >= 3200);                    // it only just arrived: it settles too
    CHECK(r.ready());
}

void test_stale_then_fresh_without_reopening() {
    Rig r; r.device.silent = {{2000, 2300}}; r.run(4000);
    CHECK(r.count("luma: stale") == 1);
    CHECK(r.count("luma: fresh") == 1);
    CHECK(r.index("luma: stale") < r.index("luma: fresh"));
    CHECK(r.count("luma: disconnected") == 0);
    CHECK(r.starts() == 1 && r.link.opens == 1);
    CHECK(!r.sink.published_while_stale);
    const auto stale_at = [&] { int n = 0; for (bool c : r.sink.changes) n += c; return n; }();
    CHECK(stale_at == 3);  // start, the silence, exit
}

void test_stale_threshold_is_the_flag() {
    Rig quick; quick.config.stale_after = milliseconds(40); quick.device.silent = {{2000, 2060}}; quick.run(3000);
    CHECK(quick.count("luma: stale") == 1);
    Rig slow; slow.device.silent = {{2000, 2060}}; slow.run(3000);
    CHECK(slow.count("luma: stale") == 0);
}

void test_silent_device_is_reopened_and_recovers() {
    Rig r; r.device.silent = {{2000, 5000}}; r.run(8000);
    CHECK(r.count("luma: stale") == 1);
    CHECK(r.count("luma: disconnected reason=silent") == 1);
    CHECK(r.count("luma: reconnected") == 1);
    CHECK(r.fusion.made == 1);
    CHECK(r.ready());
    CHECK(r.starts() >= 1 && r.starts() <= 3);
    CHECK(r.stats.starts_skipped >= 1);  // it came back by itself: the last open needed no command
    CHECK(r.link.closes == r.link.opens);
}

void test_silent_device_gets_almost_no_commands() {
    Rig r; r.device.silent = {{2000, 1'000'000}}; r.run(302'000);
    // 10 in the first minute while it backs off, then one every 9 s: never polling a device that may be in trouble.
    CHECK(r.starts() >= 30 && r.starts() <= 45);
    CHECK(r.count("luma: disconnected") == 1);
    CHECK(r.count("luma: retry attempt=") == r.starts() - 1);  // each start after the first, and the silence that ended it
    const auto& at = r.link.sent_ms;
    CHECK(at[at.size() - 2] - at[at.size() - 3] >= 8900);  // the last two starts (the very last command is the stop)
}

void test_device_that_opens_but_never_streams() {
    Rig r; r.device.silent = {{0, 1'000'000}}; r.run(20000);
    CHECK(r.link.sent_ms[0] <= 40);  // nothing to wait for on a device that was already there
    CHECK(r.count("luma: connected") == 1);
    CHECK(r.count("luma: calibrating") == 0 && r.count("luma: calibrated") == 0);
    CHECK(r.count("luma: reconnected") == 0);
    CHECK(r.sink.published == 0 && r.sink.stale);
    CHECK(r.starts() <= 5);
    CHECK(!r.ready());
}

void test_junk_counts_as_silence() {
    Rig r; r.device.junk = {{2000, 3500}}; r.run(6000);
    CHECK(r.count("luma: stale") == 1);
    CHECK(r.count("luma: disconnected reason=silent") == 1);
    CHECK(r.stats.bad_packets > 500);
    CHECK(r.ready());
}

void test_one_bad_packet_is_nothing() {
    Rig r; r.device.junk = {{2000, 2003}}; r.run(3000);
    CHECK(r.stats.bad_packets == 3);
    CHECK(r.count("luma: stale") == 0 && r.count("luma: disconnected") == 0);
}

void test_refused_command_backs_off() {
    Rig r; r.device.refuse = {{0, 20000}}; r.run(25000);
    CHECK(r.count("luma: disconnected reason=write-failed") == 1);
    CHECK(r.stats.commands < 12);  // a device refusing commands is not asked again and again
    CHECK(r.ready());              // and it recovers when it takes one
    CHECK(r.link.closes == r.link.opens);
}

void test_flapping_device_is_rate_limited() {
    Rig r;
    for (std::uint64_t t = 1000; t < 61000; t += 700) r.device.absent.push_back({t + 400, t + 700});  // up 400 ms, gone 300 ms, for a minute
    r.run(65000);
    CHECK(r.starts() <= 4);  // the wait after a return outgrows its time up: a flapping device is not spoken to
    CHECK(!r.sink.backwards);
    CHECK(r.ready());        // and once it holds, it comes back
}

void test_flicker_too_short_to_settle_gets_no_command() {
    Rig r;
    for (std::uint64_t t = 3000; t < 8000; t += 300) r.device.absent.push_back({t + 100, t + 300});
    r.run(8050);
    CHECK(r.starts() == 1);  // only the first, before the flicker began
    CHECK(r.link.sent.size() == 1);  // and no stop into a link that is still settling
}

void test_settle_grows_after_a_quick_death_and_resets_when_healthy() {
    Rig r; r.device.absent = {{1000, 1100}, {1400, 1500}, {1900, 2000}, {9000, 9100}};
    r.run(12000);
    // The first drop comes 1 s in, unhealthy, so the wait grows from 200 ms to 400; gone again at 1400 it never reached a
    // command, and the same again at 1900. Once it holds, the start goes out 400 ms after the last return.
    const auto& at = r.link.sent_ms;
    CHECK(at.size() == 4);  // first start, the one after the flicker, the one after 9100, the stop
    CHECK(at.size() == 4 && at[1] >= 2400 && at[1] <= 2520);
    CHECK(at.size() == 4 && at[2] >= 9300 && at[2] <= 9420);  // healthy for 6 s: back to 200 ms
}

void test_settling_is_not_silence() {
    Rig r; r.config.settle_first = milliseconds(1500); r.device.absent = {{3000, 4000}}; r.run(7000);
    CHECK(r.count("luma: retry") == 0);  // longer than the watchdog's second, and nobody has asked the device for anything yet
    CHECK(r.link.sent_ms.size() == 3 && r.link.sent_ms[1] >= 5500 && r.link.sent_ms[1] <= 5620);
    CHECK(r.ready());
}

void test_stream_already_on_needs_no_command() {
    Rig r; r.device.streaming = true; r.run(2000);
    CHECK(r.starts() == 0);
    CHECK(r.stats.starts_skipped == 1);
    CHECK(r.stats.commands == 1);  // the stop
    CHECK(r.ready());
}

void test_no_stop_after_listening_only_when_settling() {
    Rig r; r.device.absent = {{1000, 1100}}; r.run(1200);  // the stop request lands inside the settling wait
    CHECK(r.link.sent.size() == 1);
    CHECK(!r.link.open_now);
}

void test_says_why_it_waits() {
    Rig r; r.device.why_absent = "busy held by another driver"; r.device.absent = {{0, 2000}}; r.run(3000);
    CHECK(r.count("luma: waiting reason=busy-held-by-another-driver") == 1);
    CHECK(r.count("luma: connected") == 1);
}

Quatf yawed(float rad) { return {std::cos(rad / 2), 0, 0, std::sin(rad / 2)}; }
float yaw_of(const Quatf& q) { return 2 * std::atan2(q.z, q.w); }

void test_new_calibration_keeps_the_heading() {
    Rig r; r.config.calibration_keep = milliseconds(500); r.poses = {yawed(0.7f), yawed(-1.1f)};
    r.device.absent = {{2000, 3000}}; r.run(5000);
    CHECK(r.fusion.made == 2);
    CHECK(std::abs(yaw_of(r.sink.last_q) - 0.7f) < 1e-3f);  // not -1.1: the desktops stay where they were
    CHECK(r.count("luma: calibrated restored=0 heading=kept") == 1);
    Rig off; off.config.calibration_keep = milliseconds(500); off.config.keep_heading = false; off.poses = {yawed(0.7f), yawed(-1.1f)};
    off.device.absent = {{2000, 3000}}; off.run(5000);
    CHECK(std::abs(yaw_of(off.sink.last_q) + 1.1f) < 1e-3f);
    CHECK(off.count("luma: calibrated restored=0") == 2 && off.count("luma: calibrated restored=0 heading=kept") == 0);
}

void test_heading_carries_through_two_recalibrations() {
    Rig r; r.config.calibration_keep = milliseconds(300); r.poses = {yawed(0.5f), yawed(2.f), yawed(-2.f)};
    r.device.absent = {{1000, 2000}, {4000, 5000}}; r.run(7000);
    CHECK(r.fusion.made == 3);
    CHECK(std::abs(yaw_of(r.sink.last_q) - 0.5f) < 1e-3f);
}

void test_heading_leaves_tilt_alone() {
    // Pitched 30 degrees about y, heading 0 then 1 rad: the second estimator's tilt is true gravity and must survive.
    const Quatf pitched{std::cos(0.2618f), 0, std::sin(0.2618f), 0};
    const Quatf turned{yawed(1.f).w * pitched.w, -yawed(1.f).z * pitched.y, yawed(1.f).w * pitched.y, yawed(1.f).z * pitched.w};
    Rig r; r.config.calibration_keep = milliseconds(300); r.poses = {pitched, turned}; r.device.absent = {{1000, 2000}}; r.run(3000);
    const Quatf& q = r.sink.last_q;
    CHECK(std::abs(q.w - pitched.w) < 1e-3f && std::abs(q.y - pitched.y) < 1e-3f && std::abs(q.z) < 1e-3f);
}

void test_no_heading_looking_straight_up() {
    const Quatf up{std::cos(0.7854f), 0, -std::sin(0.7854f), 0};  // forward is world up: heading means nothing
    Rig r; r.config.calibration_keep = milliseconds(300); r.poses = {up, yawed(1.f)}; r.device.absent = {{1000, 2000}}; r.run(3000);
    CHECK(std::abs(yaw_of(r.sink.last_q) - 1.f) < 1e-3f);
    CHECK(r.count("luma: calibrated restored=0 heading=kept") == 0);
}

// The steady per-sample path allocates nothing and costs next to nothing: 1 kHz with no room for surprises.
void test_hot_path_allocates_nothing() {
    Rig r; r.fusion.steps_ns.reserve(8000);  // the fake's own log must not be what is counted
    r.run(4000);
    CHECK(r.link.allocations_in_window == 0);
    CHECK(r.stats.samples > 3900);
}

void test_healthy_connection_resets_the_back_off() {
    Rig r;
    r.device.absent = {{1000, 3000}, {8000, 8010}};  // the long outage pushes the back-off to its cap; 5 s of samples clears it
    r.run(9000);
    CHECK(r.link.reopened_ms >= 8010 && r.link.reopened_ms <= 8100);
}

void test_stop_while_disconnected_sends_nothing() {
    Rig r; r.device.absent = {{1000, 100000}}; r.run(2000);
    CHECK(r.link.sent.size() == 1);  // only the start: there is no device to tell
    CHECK(!r.link.open_now);
    CHECK(r.count("luma: stopped") == 1);
    CHECK(r.sink.stale);
}

void test_no_stop_command_configured() {
    Rig r; r.stop_command.clear(); r.run(500);
    CHECK(r.link.sent.size() == 1);
}

void test_device_clock_restarts_after_a_drop() {
    Rig r; r.device.absent = {{3000, 4000}}; r.device.restart_clock_on_return = true; r.run(6000);
    CHECK(r.stats.time_jumps == 0);  // a reconnect's rebase is expected, not an anomaly
    CHECK(!r.sink.backwards);
    CHECK(r.stats.bad_poses == 0);
    // Time keeps running for the estimator and the feed alike: a gap is a gap (Fusion then skips it and re-acquires gravity).
    std::uint64_t largest = 0;
    for (std::uint64_t step : r.fusion.steps_ns) largest = std::max(largest, step);
    CHECK(largest >= 900 * kMs && largest <= 1400 * kMs);
    CHECK(r.sink.last_t > 5'900 * kMs + 1'000 * kMs);  // 6 s of real time on the published clock, not 5
}

void test_timestamp_glitches_mid_connection() {
    Rig r;
    r.device.stamp_override = {};
    for (std::uint64_t i = 0; i < 300; ++i) r.device.stamp_override.push_back(1'000 * kMs + i * kMs);
    r.device.stamp_override.push_back(500 * kMs);       // backwards
    r.device.stamp_override.push_back(500 * kMs);       // repeated
    r.device.stamp_override.push_back(90'000'000 * kMs);  // far ahead
    r.run(1000);
    CHECK(r.stats.time_jumps == 4);  // the three, and the step back down to normal stamps after the last
    CHECK(!r.sink.backwards && r.stats.bad_poses == 0);
}

void test_bad_pose_never_reaches_the_feed() {
    Rig r; r.nan_at = 500; r.run(1000);
    CHECK(r.stats.bad_poses == 1);
    CHECK(r.sink.published > 800);
}

void test_estimator_gives_up_calibration() {
    Rig r; r.lose_at = 500; r.run(1000);
    CHECK(r.count("luma: calibrating") == 2);
    CHECK(r.count("luma: calibrated") == 2);
    CHECK(r.fusion.made == 1);
    CHECK(r.ready());
    CHECK(!r.sink.published_while_stale);
}

void test_stats_line() {
    Rig r; r.config.stats_every = milliseconds(1000); r.run(3500);
    CHECK(r.count("luma: stats") >= 4);
    const std::string& line = r.lines[std::size_t(r.index("luma: stats", r.index("luma: stats") + 1))];  // the first holds the start-up
    CHECK(line.find("commands=1 ") != std::string::npos);
    CHECK(line.find("rate_hz=1000 ") != std::string::npos || line.find("rate_hz=999 ") != std::string::npos);
    CHECK(line.find("lost=7 ") != std::string::npos);
    CHECK(line.find("max_process_us=") != std::string::npos);
}

void test_every_line_is_parseable() {
    Rig r; r.device.absent = {{2000, 2500}}; r.device.silent = {{3000, 5000}}; r.run(6000);
    for (const std::string& l : r.lines) {
        CHECK(l.starts_with("luma: "));
        CHECK(l.find('\n') == std::string::npos);
        std::size_t at = l.find(' ', 6);
        while (at != std::string::npos) {  // after the event: key=value pairs only
            const std::size_t next = l.find(' ', at + 1);
            CHECK(l.substr(at + 1, next - at - 1).find('=') != std::string::npos);
            at = next;
        }
    }
}

// ---- TransportLink, against a stand-in with luma::Transport's shape ----------------------------------------------------
enum class Io { Ok, Timeout, Interrupted, Connected, Disconnected, Closed, Error };
struct ShapedTransport {
    struct Read { Io status = Io::Timeout; std::size_t bytes = 0; };
    std::vector<Read> script;
    std::size_t at = 0;
    int opens = 0, closes = 0;
    Io write_result = Io::Ok;
    milliseconds write_timeout{0}, last_read_timeout{-1};
    void open() { ++opens; }
    Read read(std::span<std::uint8_t>, milliseconds timeout) { last_read_timeout = timeout; return at < script.size() ? script[at++] : Read{}; }
    Io write(std::span<const std::uint8_t>, milliseconds timeout) { write_timeout = timeout; return write_result; }
    void close() { ++closes; }
    std::string last_error() const { return "EBUSY: claimed by another driver"; }
};
void test_transport_link() {
    using Shaped = TransportLink<ShapedTransport, Io>;
    std::uint8_t buffer[8];
    std::string why;
    Shaped link;
    auto& t = link.transport();
    CHECK(!link.open(why) && why.find("EBUSY") != std::string::npos);  // not up yet: nothing to wait for but the reason
    CHECK(t.opens == 1);
    t.script = {{Io::Timeout}, {Io::Connected}};
    CHECK(!link.wait_for_device(milliseconds(20)));
    CHECK(t.last_read_timeout == milliseconds(20));
    CHECK(link.wait_for_device(milliseconds(20)));
    CHECK(link.open(why));
    CHECK(t.opens == 1);  // Transport keeps its own link: open is not repeated

    t.script = {{Io::Ok, 8}, {Io::Timeout}, {Io::Interrupted}, {Io::Disconnected}};
    t.at = 0;
    CHECK(link.read(buffer, milliseconds(20)).status == ReadStatus::data);
    CHECK(link.read(buffer, milliseconds(20)).status == ReadStatus::timeout);
    CHECK(link.read(buffer, milliseconds(20)).status == ReadStatus::timeout);  // a signal: the daemon's loop looks at its flag
    CHECK(link.read(buffer, milliseconds(20)).status == ReadStatus::gone);
    link.close();
    CHECK(t.closes == 0);  // gone: Transport is already rescanning, and closing would stop it
    CHECK(!link.open(why));

    t.script = {{Io::Connected}};
    t.at = 0;
    CHECK(link.open(why));
    CHECK(link.write(std::span<const std::uint8_t>(buffer, 1)));
    CHECK(t.write_timeout == milliseconds(200));
    t.write_result = Io::Error;
    CHECK(!link.write(std::span<const std::uint8_t>(buffer, 1)));
    link.close();  // up but silent: let go of it, and start again next time
    CHECK(t.closes == 1);
    CHECK(!link.open(why) && t.opens == 2);
}

// ---- capture and replay ------------------------------------------------------------------------------------------
ImuSample sample_at(std::uint64_t t, std::uint32_t seq) { return ImuSample{t, {0, 0, 0.1f}, {0, 0, 9.8f}, std::nullopt, seq}; }
std::vector<CaptureRecord> capture(std::uint64_t ms, std::vector<Span> silent = {}, std::vector<Span> gone = {}) {
    std::vector<CaptureRecord> out;
    out.push_back({0, CaptureKind::to_device, {0x01}});
    bool away = false;
    for (std::uint64_t t = 1; t <= ms; ++t) {
        const bool in_gone = within(gone, t);
        if (in_gone != away) { away = in_gone; out.push_back({t * kMs, away ? CaptureKind::device_gone : CaptureKind::device_back, {}}); }
        if (in_gone || within(silent, t)) continue;
        out.push_back({t * kMs, CaptureKind::from_device, wiring::stand_in_packet(sample_at(t * kMs, std::uint32_t(t)))});
    }
    return out;
}
struct Replay { std::vector<std::string> lines; std::uint64_t commands = 0; Stats stats; FakeSink sink; DaemonEnd end{}; };
void replay(Replay& r, std::vector<CaptureRecord> records) {
    ReplayLink link(std::move(records), false);
    std::atomic<bool> stop{false};
    const auto decoder = wiring::decoder();
    DaemonParts parts{link, *decoder, &wiring::estimator, r.sink, [&] { return link.now_ns(); },
                      [&](std::string_view l) { r.lines.emplace_back(l); }, wiring::start_command(), wiring::stop_command()};
    Daemon d(std::move(parts));
    r.end = d.run(stop);
    r.stats = d.stats();
    r.commands = link.commands_swallowed();
}
int count(const std::vector<std::string>& lines, std::string_view prefix) {
    int n = 0;
    for (const auto& l : lines) if (l.starts_with(prefix)) ++n;
    return n;
}

void test_start_sequence_is_spaced_and_stops_when_samples_come() {
    { Rig r; r.stop_command.clear(); r.before_start = {{0x02}}; r.run(1000);
      CHECK(r.link.sent.size() == 2);
      CHECK(r.link.sent[0] == std::vector<std::uint8_t>{0x02} && r.link.sent[1] == std::vector<std::uint8_t>{0x01});
      CHECK(r.link.sent_ms[1] - r.link.sent_ms[0] >= 100); }
    { Rig r; r.stop_command.clear(); r.before_start = {{0x02}}; r.device.stream_byte = 0x02; r.run(1000);
      CHECK(r.link.sent.size() == 1);  // what came first was enough: nothing more goes out
      CHECK(r.stats.starts_skipped == 1); }
}

void test_listen_only_sends_nothing() {
    Rig r; r.start_command.clear(); r.stop_command.clear(); r.run(4000);
    CHECK(r.link.sent.empty());
    CHECK(r.stats.commands == 0);
    CHECK(r.count("luma: disconnected reason=silent") == 1);  // and it says so: silence on an open link is reported
}

void test_repeats_are_not_junk() {
    std::vector<CaptureRecord> records;
    for (std::uint64_t t = 1; t <= 3000; ++t) {
        auto packet = wiring::stand_in_packet(sample_at(t * kMs, std::uint32_t(t)));
        records.push_back({t * kMs, CaptureKind::from_device, packet});
        if (t <= 60) records.push_back({t * kMs + 500'000, CaptureKind::from_device, packet});  // the Ultra's first 60 or so come twice
    }
    Replay r; replay(r, records);
    CHECK(r.stats.samples == 3000);
    CHECK(r.stats.repeats == 60);
    CHECK(r.stats.bad_packets == 0);
}

void test_real_capture_format(const std::filesystem::path& dir) {
    // A hand-made LUMACAP1 file, as tools/luma_capture writes: the reader itself is proven against those in test_cpp_reader.py.
    const auto put = [](std::string& out, auto value) { out.append(reinterpret_cast<const char*>(&value), sizeof value); };
    std::string file("LUMACAP1");
    put(file, std::uint16_t(1)); put(file, std::uint16_t(32)); put(file, std::uint16_t(48)); put(file, std::uint16_t(0));
    put(file, std::uint64_t(0)); put(file, std::uint32_t(5)); put(file, std::uint32_t(0));
    const auto record = [&](std::uint8_t endpoint, std::uint8_t xfer, std::uint64_t t, const std::vector<std::uint8_t>& data) {
        put(file, std::uint32_t(48 + data.size())); file += char(1); file += 'C'; file += char(xfer); file += char(endpoint);
        put(file, t); put(file, std::uint64_t(1)); put(file, std::uint16_t(5)); file += char(13); file += char(0x02);
        put(file, std::int32_t(0)); put(file, std::uint32_t(data.size())); put(file, std::uint32_t(data.size())); put(file, std::uint64_t(0));
        file.append(reinterpret_cast<const char*>(data.data()), data.size());
    };
    for (std::uint64_t t = 1; t <= 100; ++t) {
        record(0x81, 1, t * kMs, wiring::stand_in_packet(sample_at(t * kMs, std::uint32_t(t))));
        record(0x83, 1, t * kMs, std::vector<std::uint8_t>(16, 7));  // the display's events are not played to the parser
    }
    const std::string path = dir / "real.lcap";
    { std::ofstream(path, std::ios::binary) << file; }
    std::string why;
    const auto records = load_capture(path, why);
    CHECK(records.has_value());
    if (!records) return;
    CHECK(records->size() == 100);
    for (const CaptureRecord& r : *records) CHECK(r.kind == CaptureKind::from_device && r.payload.size() == 28);
}

void test_bias_store(const std::filesystem::path& dir) {
    const std::string path = dir / "state" / "bias";
    wiring::BiasStore store(path);
    store.load();
    CHECK(!store.bias());                    // a first run: cold start
    CHECK(!store.save());                    // nothing learned, nothing written
    store.remember({0.01f, -0.02f, 0.03f});
    CHECK(store.save());
    wiring::BiasStore again(path);
    again.load();
    CHECK(again.bias() && std::fabs((*again.bias())[2] - 0.03f) < 1e-6f);
    { std::ofstream(path) << "garbage"; }
    wiring::BiasStore bad(path);
    bad.load();
    CHECK(!bad.bias());
    { std::ofstream(path) << "0 0 9"; }      // beyond anything a gyro could need: refused, not clamped
    wiring::BiasStore wild(path);
    wild.load();
    CHECK(!wild.bias());
}

void test_a_long_running_driver_hands_over_its_bias() {
    // The driver outlives its XR runs, so the bias must reach the store with the estimator still alive.
    wiring::BiasStore store("/nonexistent/bias");
    const auto estimator = wiring::estimator_with(store);
    for (std::uint32_t i = 1; i <= 70'000; ++i) estimator->update(ImuSample{i * kMs, {0.01f, -0.02f, 0.005f}, {0, 0, 9.8f}, std::nullopt, i});
    CHECK(store.bias());
    if (store.bias()) CHECK(std::fabs((*store.bias())[1] + 0.02f) < 1e-3f);
}

void test_a_poor_bias_is_not_kept() {
    // Moving from the first sample: Coarse at best, and that guess must not be saved as the truth for the next run.
    wiring::BiasStore store("/nonexistent/bias");
    { const auto estimator = wiring::estimator_with(store);
      for (std::uint32_t i = 1; i <= 3'000; ++i) {
          const float swing = 1.5f * std::sin(float(i) * 0.01f);
          estimator->update(ImuSample{i * kMs, {swing, swing, swing}, {swing, swing, 9.8f}, std::nullopt, i});
      } }
    CHECK(!store.bias());
}

void test_transport_link_routes_and_filters() {
    struct Routed {
        struct Read { Io status = Io::Timeout; std::size_t bytes = 0; std::uint8_t endpoint = 0; };
        std::vector<Read> script;
        std::size_t at = 0;
        std::vector<std::uint8_t> routed_to;
        void open() {}
        Read read(std::span<std::uint8_t>, milliseconds) { return at < script.size() ? script[at++] : Read{}; }
        Io write(std::span<const std::uint8_t>, milliseconds) { return Io::Error; }
        Io write_to(std::uint8_t endpoint, std::span<const std::uint8_t>, milliseconds) { routed_to.push_back(endpoint); return Io::Ok; }
        void close() {}
        std::string last_error() const { return {}; }
    };
    TransportLink<Routed, Io> link;
    std::vector<int> others;
    link.set_stream_endpoint(0x81);
    link.set_other_packet_handler([&](std::uint8_t endpoint, std::span<const std::uint8_t>) { others.push_back(endpoint); });
    link.set_route([](std::span<const std::uint8_t> bytes) { return bytes[0] == 0xff ? std::uint8_t(0x06) : std::uint8_t(0x04); });
    auto& t = link.transport();
    t.script = {{Io::Ok, 28, 0x81}, {Io::Ok, 6, 0x85}, {Io::Ok, 28, 0x81}};
    std::uint8_t buffer[32];
    CHECK(link.read(buffer, milliseconds(1)).status == ReadStatus::data);
    CHECK(link.read(buffer, milliseconds(1)).status == ReadStatus::timeout);  // the MCU's reply is not a sample
    CHECK(link.read(buffer, milliseconds(1)).status == ReadStatus::data);
    CHECK(others == std::vector<int>{0x85});
    const std::uint8_t mcu[] = {0xfa}, data[] = {0xff};
    CHECK(link.write(mcu) && link.write(data));
    CHECK(t.routed_to == (std::vector<std::uint8_t>{0x04, 0x06}));
}

void test_feature_host_refuses_anything_that_sends() {
    std::string why;
    std::vector<std::string> lines;
    const Logger log = [&](std::string_view l) { lines.emplace_back(l); };
    CHECK(wiring::FeatureHost::make({}, log, why));
    CHECK(wiring::FeatureHost::make({"--no-feature-temperature", "--feature-discovery"}, log, why));
    for (const char* flag : {"--feature-firmware-query", "--feature-wear-query", "--feature-control", "--feature-brightness=3", "--feature-dimming=1"}) {
        why.clear();
        CHECK(!wiring::FeatureHost::make({flag}, log, why));
        CHECK(why.find("no capture") != std::string::npos);
    }
    why.clear();
    CHECK(!wiring::FeatureHost::make({"--feature-nonsense"}, log, why));
    CHECK(!why.empty());
    const auto host = wiring::FeatureHost::make({}, log, why);
    host->tick(1, 41.5f);
    host->tick(2, 42.5f);  // once a minute is plenty
    host->link(true, 3);
    host->link(false, 4);
    CHECK(count(lines, "luma: feature {") >= 1);
    int temperatures = 0;
    for (const auto& l : lines) if (l.find("emperature") != std::string::npos) ++temperatures;
    CHECK(temperatures == 1);
}

void test_replay_clean() {
    Replay r; replay(r, capture(3000));
    CHECK(r.end == DaemonEnd::replay_ended);
    CHECK(r.stats.samples == 3000);
    CHECK(r.sink.published >= 1400 && r.sink.published <= 1510);  // real Fusion: 1.5 s still, then everything is published
    CHECK(r.commands == 0);           // samples from the first moment: listened, sent nothing. No stop when a replay runs out
    CHECK(count(r.lines, "luma: stopped reason=replay-end") == 1);
}

void test_replay_reproduces_a_drop_and_a_stall() {
    Replay r; replay(r, capture(8000, {{3000, 3200}}, {{5000, 5500}}));
    CHECK(count(r.lines, "luma: stale") == 1);
    CHECK(count(r.lines, "luma: fresh") == 1);
    CHECK(count(r.lines, "luma: disconnected reason=gone") == 1);
    CHECK(count(r.lines, "luma: reconnected") == 1);
    CHECK(count(r.lines, "luma: calibrated restored=1") == 1);
    CHECK(r.commands <= 2);
    CHECK(!r.sink.backwards);
}

void test_replay_ending_in_a_drop_ends() {
    Replay r; replay(r, capture(2000, {}, {{1500, 99999}}));
    CHECK(r.end == DaemonEnd::replay_ended);
    CHECK(count(r.lines, "luma: disconnected") == 1);
}

void test_replay_of_nothing_ends() {
    Replay r; replay(r, {});
    CHECK(r.end == DaemonEnd::replay_ended);
    CHECK(r.stats.samples == 0);
}

void test_capture_file(const std::filesystem::path& dir) {
    const std::string path = dir / "round.cap";
    const auto records = capture(50, {}, {{20, 25}});
    CHECK(save_capture(path, records));
    std::string why;
    const auto back = load_capture(path, why);
    CHECK(back && back->size() == records.size());
    if (back) for (std::size_t i = 0; i < records.size(); ++i) CHECK((*back)[i].t_ns == records[i].t_ns && (*back)[i].kind == records[i].kind && (*back)[i].payload == records[i].payload);

    CHECK(!load_capture((dir / "absent").string(), why));
    const auto write_bytes = [&](const std::string& name, std::string_view bytes) {
        const std::string p = dir / name;
        std::FILE* f = std::fopen(p.c_str(), "wb");
        std::fwrite(bytes.data(), 1, bytes.size(), f);
        std::fclose(f);
        return p;
    };
    CHECK(!load_capture(write_bytes("empty", ""), why));
    CHECK(!load_capture(write_bytes("wrong", "NOTACAPTURE....."), why));
    CHECK(load_capture(write_bytes("only-magic", "LUMACAP0"), why).has_value());
    std::filesystem::copy_file(path, dir / "cut.cap");
    std::filesystem::resize_file(dir / "cut.cap", std::filesystem::file_size(path) - 3);
    why.clear();
    CHECK(!load_capture((dir / "cut.cap").string(), why) && !why.empty());
    const std::string huge("LUMACAP0\0\0\0\0\0\0\0\0\xff\xff\xff\xff\0", 21);
    CHECK(!load_capture(write_bytes("huge", huge), why));
    const std::string bad_kind("LUMACAP0\0\0\0\0\0\0\0\0\0\0\0\0\x09", 21);
    CHECK(!load_capture(write_bytes("kind", bad_kind), why));
    std::string backwards("LUMACAP0", 8);
    backwards += std::string("\x05\0\0\0\0\0\0\0\0\0\0\0\0", 13);
    backwards += std::string("\x01\0\0\0\0\0\0\0\0\0\0\0\0", 13);
    CHECK(!load_capture(write_bytes("backwards", backwards), why));
}

// ---- command line -------------------------------------------------------------------------------------------------
std::optional<Options> parse(std::vector<const char*> args, std::string* out_why = nullptr) {
    std::string why;
    auto o = parse_options(args, why);
    if (out_why) *out_why = why;
    return o;
}
void test_options() {
    const auto plain = parse({});  // how tracking.py starts it
    CHECK(plain && plain->ipc_prefix == "/tmp/shader_runtime_" && !plain->replay);
    const auto all = parse({"--ipc-prefix", "/run/x_", "--replay", "f", "--replay-realtime", "--stale-ms", "50", "--reopen-ms", "700", "--keep-calibration-ms", "3000"});
    CHECK(all && all->ipc_prefix == "/run/x_" && all->replay == "f" && all->replay_realtime);
    CHECK(all && all->config.stale_after == milliseconds(50) && all->config.silent_reopen_after == milliseconds(700) && all->config.calibration_keep == milliseconds(3000));
    CHECK(parse({"--help"}) && parse({"--help"})->help);
    std::string why;
    CHECK(!parse({"--replay", "f"}, &why) && why.find("--ipc-prefix") != std::string::npos);  // would write the live feed
    CHECK(!parse({"--replay", "f", "--ipc-prefix", "/tmp/shader_runtime_"}));
    CHECK(!parse({"--replay-realtime"}));
    CHECK(!parse({"--ipc-prefix"}));
    CHECK(!parse({"--ipc-prefix", ""}));
    CHECK(!parse({"--stale-ms", "abc"}));
    CHECK(!parse({"--stale-ms", "0"}));
    CHECK(!parse({"--stale-ms", "-5"}));
    CHECK(!parse({"--stale-ms", "10x"}));
    CHECK(!parse({"--stale-ms", "2000"}));  // not shorter than the reopen
    CHECK(!parse({"--frobnicate"}));
    const auto flags = parse({"--no-keep-heading", "--no-stop-command", "--settle-ms", "5000"});
    CHECK(flags && !flags->config.keep_heading && !flags->stop_command && flags->config.settle_first == milliseconds(5000));
    CHECK(flags && flags->config.settle_max == milliseconds(5000));
    CHECK(plain && plain->config.keep_heading && plain->stop_command);
    CHECK(plain && plain->config.calibration_keep == milliseconds(300000));
    // What the live plan runs.
    CHECK(plain && plain->start == Options::Start::stream && !plain->read_events && !plain->drain_camera);  // tracking.py's default
    CHECK(parse({"--start", "listen"})->start == Options::Start::listen);
    CHECK(parse({"--start", "prepared"})->start == Options::Start::prepared);
    CHECK(parse({"--start", "full"})->start == Options::Start::full);
    CHECK(!parse({"--start", "guess"}));
    CHECK(!parse({"--start"}));
    const auto live = parse({"--start", "stream", "--read-events", "--drain-camera", "--no-rt", "--state-dir", "/x", "--feature-discovery", "--no-feature-temperature"});
    CHECK(live && live->read_events && live->drain_camera && live->no_rt && live->state_dir == "/x" && live->feature_flags.size() == 2);
    CHECK(!parse({"--pose-log", "f"}));  // writes to disk on the pose thread: replay only
    const auto logged = parse({"--replay", "f", "--ipc-prefix", "/run/x_", "--pose-log", "p", "--pose-log-every", "5"});
    CHECK(logged && logged->pose_log == "p" && logged->pose_log_every == 5);
}

// ---- the real binary -----------------------------------------------------------------------------------------------
struct Process { pid_t pid = -1; int err = -1; };
Process spawn(const std::string& binary, std::vector<std::string> args) {
    int pipefd[2];
    CHECK(pipe(pipefd) == 0);
    const pid_t pid = fork();
    if (pid == 0) {
        dup2(pipefd[1], 2);
        close(pipefd[0]); close(pipefd[1]);
        std::vector<char*> argv{const_cast<char*>(binary.c_str())};
        for (auto& a : args) argv.push_back(a.data());
        argv.push_back(nullptr);
        execv(binary.c_str(), argv.data());
        _exit(127);
    }
    close(pipefd[1]);
    return {pid, pipefd[0]};
}
std::string drain(int fd, std::string_view until = {}) {
    std::string out;
    char chunk[4096];
    for (;;) {
        const ssize_t n = read(fd, chunk, sizeof chunk);
        if (n <= 0) break;
        out.append(chunk, std::size_t(n));
        if (!until.empty() && out.find(until) != std::string::npos) break;
    }
    return out;
}
int wait_exit(pid_t pid) {
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
}
void test_binary(const std::string& binary, const std::filesystem::path& dir) {
    if (binary.empty()) { ++g_failures; std::cerr << "  FAILED no binary given\n"; return; }
    const std::string prefix = (dir / "ipc_").string();
    const std::string cap = dir / "run.cap";
    CHECK(save_capture(cap, capture(2000)));

    { auto p = spawn(binary, {"--replay", cap, "--ipc-prefix", prefix});  // a whole run, as fast as it goes
      const std::string log = drain(p.err);
      CHECK(wait_exit(p.pid) == 0);
      CHECK(log.find("luma: connected at=") != std::string::npos);
      CHECK(log.find("luma: calibrated restored=0 at=") != std::string::npos);
      CHECK(log.find("luma: stopped reason=replay-end at=") != std::string::npos); }

    { auto p = spawn(binary, {"--replay", cap});  // refuses to run on the live feed
      const std::string log = drain(p.err);
      CHECK(wait_exit(p.pid) == 2);
      CHECK(log.find("luma: error reason=usage") != std::string::npos); }

    { auto p = spawn(binary, {"--replay", (dir / "absent.cap").string(), "--ipc-prefix", prefix});
      const std::string log = drain(p.err);
      CHECK(wait_exit(p.pid) == 3);
      CHECK(log.find("luma: error reason=replay") != std::string::npos); }

    { auto p = spawn(binary, {"--replay", cap, "--ipc-prefix", (dir / "no-such-folder" / "x_").string()});
      const std::string log = drain(p.err);
      CHECK(wait_exit(p.pid) == 5);
      CHECK(log.find("reason=lock") != std::string::npos); }

    for (int sig : {SIGTERM, SIGINT, SIGHUP}) {  // a clean stop, at once, with a second instance refused meanwhile
        const std::string long_cap = dir / "long.cap";
        CHECK(save_capture(long_cap, capture(60000)));
        auto p = spawn(binary, {"--replay", long_cap, "--replay-realtime", "--ipc-prefix", prefix});
        std::string log = drain(p.err, "luma: connected");
        auto second = spawn(binary, {"--replay", cap, "--ipc-prefix", prefix});
        const std::string refused = drain(second.err);
        CHECK(wait_exit(second.pid) == 5);
        CHECK(refused.find("reason=already-running") != std::string::npos);
        close(second.err);
        kill(p.pid, sig);
        log += drain(p.err);
        CHECK(wait_exit(p.pid) == 0);
        CHECK(log.find("luma: stopped reason=signal") != std::string::npos);
        close(p.err);
    }
    // After all of that the lock is free again.
    { auto p = spawn(binary, {"--replay", cap, "--ipc-prefix", prefix});
      drain(p.err);
      CHECK(wait_exit(p.pid) == 0); }
}
}

int main(int argc, char** argv) {
    char tmpl[] = "/tmp/luma_daemon_test_XXXXXX";
    const char* dir = mkdtemp(tmpl);
    if (!dir) return 2;
    #define RUN(name, ...) do { const int before = g_failures; name(__VA_ARGS__); std::cerr << (g_failures == before ? "ok   " : "FAIL ") << #name << '\n'; } while (0)
    RUN(test_clean_run);
    RUN(test_nothing_published_before_calibration);
    RUN(test_short_drop_keeps_calibration);
    RUN(test_ready_goes_false_during_a_drop);
    RUN(test_hotplug_event_ends_the_wait);
    RUN(test_long_drop_recalibrates);
    RUN(test_keep_window_boundary);
    RUN(test_drop_during_calibration_starts_it_over);
    RUN(test_glasses_absent_at_start);
    RUN(test_stale_then_fresh_without_reopening);
    RUN(test_stale_threshold_is_the_flag);
    RUN(test_silent_device_is_reopened_and_recovers);
    RUN(test_silent_device_gets_almost_no_commands);
    RUN(test_device_that_opens_but_never_streams);
    RUN(test_junk_counts_as_silence);
    RUN(test_one_bad_packet_is_nothing);
    RUN(test_refused_command_backs_off);
    RUN(test_flapping_device_is_rate_limited);
    RUN(test_healthy_connection_resets_the_back_off);
    RUN(test_stop_while_disconnected_sends_nothing);
    RUN(test_no_stop_command_configured);
    RUN(test_device_clock_restarts_after_a_drop);
    RUN(test_timestamp_glitches_mid_connection);
    RUN(test_bad_pose_never_reaches_the_feed);
    RUN(test_estimator_gives_up_calibration);
    RUN(test_flicker_too_short_to_settle_gets_no_command);
    RUN(test_settle_grows_after_a_quick_death_and_resets_when_healthy);
    RUN(test_settling_is_not_silence);
    RUN(test_stream_already_on_needs_no_command);
    RUN(test_no_stop_after_listening_only_when_settling);
    RUN(test_says_why_it_waits);
    RUN(test_new_calibration_keeps_the_heading);
    RUN(test_heading_carries_through_two_recalibrations);
    RUN(test_heading_leaves_tilt_alone);
    RUN(test_no_heading_looking_straight_up);
    RUN(test_hot_path_allocates_nothing);
    RUN(test_transport_link);
    RUN(test_transport_link_routes_and_filters);
    RUN(test_start_sequence_is_spaced_and_stops_when_samples_come);
    RUN(test_listen_only_sends_nothing);
    RUN(test_repeats_are_not_junk);
    RUN(test_real_capture_format, dir);
    RUN(test_bias_store, dir);
    RUN(test_feature_host_refuses_anything_that_sends);
    RUN(test_a_long_running_driver_hands_over_its_bias);
    RUN(test_a_poor_bias_is_not_kept);
    RUN(test_stats_line);
    RUN(test_every_line_is_parseable);
    RUN(test_replay_clean);
    RUN(test_replay_reproduces_a_drop_and_a_stall);
    RUN(test_replay_ending_in_a_drop_ends);
    RUN(test_replay_of_nothing_ends);
    RUN(test_capture_file, dir);
    RUN(test_options);
    RUN(test_binary, argc > 1 ? argv[1] : "", dir);
    std::filesystem::remove_all(dir);
    std::cerr << (g_failures ? "FAILED\n" : "all passed\n");
    return g_failures ? 1 : 0;
}
