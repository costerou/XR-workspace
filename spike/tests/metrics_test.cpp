#include "xr/metrics.hpp"
#include <atomic>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <new>
#include <nlohmann/json.hpp>
#include <cmath>
#include <limits>
#include <time.h>

namespace { std::atomic<unsigned long> allocations{0}; }
// Counted allocations (noinline: GCC otherwise pairs the inlined malloc/free with new/delete and warns).
[[gnu::noinline]] void* operator new(std::size_t n) {
    allocations.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int failures = 0;
void check(bool ok, const char* name) { if (!ok) { std::cerr << name << '\n'; ++failures; } }
// The last-second presentation window as it was kept before the fixed ring (std::deque): the
// reference the ring must match byte for byte in status_json.
struct DequeWindow {
    std::deque<std::uint64_t> recent;
    std::uint64_t presented = 0;
    void push(std::uint64_t ts) {
        ++presented; recent.push_back(ts);
        while (ts - recent.front() > 1000000000ULL || recent.size() > 4096) recent.pop_front();
    }
    std::string status(double now_ms) const {
        auto j = nlohmann::json::object();
        if (presented) j["missed"] = 0;
        std::uint64_t first = 0, last = 0;
        std::size_t count = 0;
        for (auto ns : recent) {
            const double ms = double(ns) / 1e6;
            if (ms <= now_ms && ms >= now_ms - 1000) { if (!count) first = ns; last = ns; ++count; }
        }
        if (count >= 2) j["presented_hz"] = double(count - 1) * 1e9 / double(last - first);
        return j.dump();
    }
};
int main() {
    xr::Metrics live;
    check(nlohmann::json::parse(live.status_json(2000)).empty(), "status omits unmeasured");
    live.bind(1); live.clock(CLOCK_MONOTONIC);
    for (unsigned i=0; i<120; ++i) {
        auto ns = 1000000000ULL + i * 8333333ULL;
        live.presented(*live.submit(), 0, ns/1000000000, ns%1000000000, 0, 0, i+1, 0, true);
    }
    auto status = nlohmann::json::parse(live.status_json(2000));
    check(std::abs(status["presented_hz"].get<double>()-120)<0.5, "status rate from timestamps");
    check(!nlohmann::json::parse(live.status_json(4000)).contains("presented_hz"), "stale timestamps omitted");
    live.discarded(*live.submit());
    check(nlohmann::json::parse(live.status_json(4000))["missed"]==1, "missed is cumulative feedback");
    xr::Metrics window; window.bind(1); window.clock(CLOCK_MONOTONIC);
    check(window.report().find("\"observation_duration_ns\":null") != std::string::npos, "empty window has no denominator");
    window.presented(*window.submit(), 0, 1, 0, 1, 0, 1, 0, true);
    check(window.snapshot().valid_presented == 1 && window.snapshot().interval_count == 0, "one observation is not an interval");
    window.discarded(*window.submit());
    window.presented(*window.submit(), 0, 1, 20000000, 1, 0, 2, 0, true);
    check(window.snapshot().interval_count == 1 && window.snapshot().interval_sum_ns == 20000000, "discard gap is retained; prediction not denominator");
    window.presented(*window.submit(), 0, 1, 30000000, 1, 0, 2, 0, true); // invalid repeated sequence
    window.presented(*window.submit(), 0, 1, 40000000, 1, 0, 3, 0, true);
    window.presented(*window.submit(), 0, 1, 50000000, 1, 0, 4, 0, true);
    auto summary = window.snapshot();
    check(summary.valid_presented == 4 && summary.interval_count == 2 && summary.interval_sum_ns == 30000000,
          "invalid gap excluded from interval denominator");
    check(summary.interval_min_ns == 10000000 && summary.interval_max_ns == 20000000, "validated interval extrema");
    check(window.report().find("\"observation_duration_ns\":50000000") != std::string::npos &&
          window.report().find("\"interval_mean_ns\":15000000.0") != std::string::npos, "span and interval denominator remain distinct");
    xr::Metrics missing;
    check(!missing.submit(), "missing protocol does not invent feedback");
    check(missing.snapshot().submitted == 1 && missing.snapshot().presented == 0, "submission is not presentation");
    check(!missing.snapshot().clock_id, "missing clock stays unknown");
    xr::Metrics m;
    m.bind(99); m.clock(CLOCK_MONOTONIC);
    check(m.snapshot().protocol_version == 1, "bind capped to supported v1 semantics");
    auto a = *m.submit(); auto b = *m.submit();
    m.presented(a, 0, 1, 0, 8333333, 0, 100, 7, true);
    m.presented(b, 0, 1, 8333333, 8333333, 0, 101, 7, true);
    auto s = m.snapshot();
    check(s.submitted == 2 && s.presented == 2 && s.intervals[3] == 1, "only presentation timestamps make intervals");
    check(s.flags[0] == 2 && s.flags[1] == 2 && s.flags[2] == 2 && s.flags[3] == 0, "retain hardware flags independently");
    m.discarded(b); check(m.snapshot().discarded == 0, "duplicate terminal event cannot count twice");
    auto c = *m.submit(); m.discarded(c);
    check(m.snapshot().discarded == 1 && m.snapshot().presented == 2, "discard is not success");
    auto d = *m.submit(); m.presented(d, 0, 1, 16666666, 0, 0, 101, 0, true);
    check(m.snapshot().invalid_timing == 1, "duplicate retrace is not a cadence sample");
    auto e = *m.submit(); m.presented(e, 0, 1, 1000000000, 0, 0, 102, 0, true);
    check(m.snapshot().invalid_timing == 2, "invalid nanoseconds rejected");
    auto f = *m.submit(); m.presented(f, 0xffffffff, 0xffffffff, 0, 0, 0, 103, 0, true);
    check(m.snapshot().invalid_timing == 3, "timestamp conversion overflow rejected");
    for (auto clock : {std::uint32_t(CLOCK_REALTIME), 999999U}) {
        xr::Metrics other; other.bind(1); other.clock(clock);
        for (unsigned i=1; i<=2; ++i) other.presented(*other.submit(), 0, i, 0, 0, 0, i, 0, true);
        check(other.snapshot().presented == 2 && other.snapshot().invalid_timing == 2, "foreign clock counted but not mapped to monotonic");
        check(other.snapshot().clock_id == clock, "raw clock id retained");
    }
    xr::Metrics unknown; unknown.bind(1);
    unknown.presented(*unknown.submit(), 0, 1, 0, 0, 0, 1, 0, true);
    check(unknown.snapshot().invalid_timing == 1, "absent clock blocks timing");
    xr::Metrics bounded; bounded.bind(1);
    for (unsigned i=0; i<32; ++i) check(bool(bounded.submit()), "bounded pending slots available");
    check(!bounded.can_submit() && !bounded.submit() && bounded.snapshot().submitted == 32, "full queue backpressures before commit");
    bounded.abandon(); bounded.abandon();
    check(bounded.snapshot().abandoned == 32 && bounded.snapshot().pending == 0, "teardown is idempotent, not discarded");
    xr::Metrics trace; trace.bind(1); trace.clock(CLOCK_MONOTONIC);
    for (unsigned i=1; i<=10000; ++i) trace.presented(*trace.submit(), 0, i, 0, 0, 0, i, 0, true);
    check(trace.snapshot().intervals[7] == 9999 && trace.snapshot().pending == 0, "long stalls retained in fixed histogram without event log");
    auto wrong = *trace.submit(); trace.presented(wrong, 0, 10001, 0, 0, 0, 10001, 0, false);
    check(trace.snapshot().invalid_timing == 1, "unverified output blocks timing");
    xr::Metrics repeated; repeated.bind(1); repeated.clock(CLOCK_MONOTONIC);
    for (unsigned i=1; i<=4; ++i) repeated.presented(*repeated.submit(), 0, i, 0, 0, 0, 10, 0, true);
    check(repeated.snapshot().invalid_timing == 3, "repeated retraces remain invalid after rejection");
    xr::Metrics backwards; backwards.bind(1); backwards.clock(CLOCK_MONOTONIC);
    backwards.presented(*backwards.submit(), 0, 10, 0, 0, 0, 1, 0, true);
    backwards.presented(*backwards.submit(), 0, 9, 0, 0, 0, 2, 0, true);
    backwards.presented(*backwards.submit(), 0, 8, 0, 0, 0, 3, 0, true);
    check(backwards.snapshot().invalid_timing == 2, "timestamp high water survives repeated regressions");
    xr::Metrics changes; changes.bind(1); changes.clock(CLOCK_MONOTONIC);
    changes.clock(CLOCK_REALTIME); changes.clock(CLOCK_MONOTONIC);
    changes.presented(*changes.submit(), 0, 1, 0, 0, 0, 1, 0, true);
    check(changes.snapshot().invalid_timing == 1, "clock domain violation permanently invalidates timing");
    xr::Metrics late; late.bind(1);
    auto pending = *late.submit(); late.abandon(); late.discarded(pending);
    late.presented(pending, 0, 1, 0, 0, 0, 1, 0, true);
    check(late.snapshot().presented == 0 && late.snapshot().discarded == 0, "late terminal callbacks after teardown ignored");
    check(trace.report().find("\"sustained_cadence_qualified\":false") != std::string::npos, "report never promotes static pattern to cadence proof");
    // The fixed ring reproduces the deque window exactly: past its 4096 capacity (5 kHz), across the
    // one-second eviction, irregular gaps and pauses, at many query times.
    {
        xr::Metrics ring; ring.bind(1); ring.clock(CLOCK_MONOTONIC);
        DequeWindow ref;
        std::uint64_t ts = 5'000'000'000ULL, seq = 0;
        bool same = true;
        auto present = [&](std::uint64_t at) {
            ring.presented(*ring.submit(), std::uint32_t(at / 1000000000ULL >> 32), std::uint32_t(at / 1000000000ULL),
                           std::uint32_t(at % 1000000000ULL), 0, 0, std::uint32_t(++seq), 0, true);
            ref.push(at);
        };
        auto compare = [&](std::uint64_t at) {
            for (double back : {-0.3, 0.0, 1.0, 250.0, 999.9, 1000.0, 1500.0}) {
                const double now = double(at) / 1e6 - back;  // just after, at, and before the newest presentation
                same = same && ring.status_json(now) == ref.status(now);
            }
        };
        for (int i = 0; i < 12000; ++i) { ts += 200'000; present(ts); if (i % 97 == 0) compare(ts); }       // 5 kHz: capacity bound
        for (int i = 0; i < 3000; ++i) { ts += 8'333'333 + std::uint64_t(i % 7) * 13'001; present(ts); if (i % 31 == 0) compare(ts); }
        ts += 2'500'000'000ULL; present(ts); compare(ts);                                                   // a pause empties the window
        for (int i = 0; i < 500; ++i) { ts += (i % 5 ? 4'166'667 : 16'666'667); present(ts); compare(ts); }
        check(same, "fixed ring matches the deque window byte for byte");
        // Recording a presentation never allocates (the deque allocated a block every 64).
        xr::Metrics alone; alone.bind(1); alone.clock(CLOCK_MONOTONIC);
        const auto quiet = allocations.load();
        std::uint64_t t2 = 1'000'000'000ULL;
        for (unsigned i = 1; i <= 20000; ++i) {
            t2 += 8'333'333;
            alone.presented(*alone.submit(), 0, std::uint32_t(t2 / 1000000000ULL), std::uint32_t(t2 % 1000000000ULL), 8333333, 0, i, 0, true);
        }
        check(allocations.load() == quiet, "presentation feedback does not allocate");
    }
    return failures ? 1 : 0;
}
