#include "xr/metrics.hpp"
#include <iostream>
#include <limits>
#include <time.h>

int failures = 0;
void check(bool ok, const char* name) { if (!ok) { std::cerr << name << '\n'; ++failures; } }
int main() {
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
    return failures ? 1 : 0;
}
