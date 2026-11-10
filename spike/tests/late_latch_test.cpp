// LateLatch (xr/late_latch.hpp): the schedule, the adaptive margin and the miss count, on a simulated compositor.
#include "xr/late_latch.hpp"
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
namespace {
constexpr std::uint64_t kMs = 1'000'000;
constexpr std::uint64_t kPeriod = 8'333'333;  // 120 Hz
bool near(double a, double b, double eps = 1e-6) { return std::fabs(a - b) <= eps; }

// A compositor latching at k * period + phase and sending the frame callback `delay` after the latch; a renderer that
// takes `draw(k)` from start to commit and wakes `late` after the time it asked for. Returns read_to_latch of the
// last frame, and leaves the counts in the latch.
struct Sim {
    LateLatch latch;
    std::uint64_t delay = 200'000, late = 50'000;
    std::uint64_t now = 1000 * kMs, next_latch = 1000 * kMs + kPeriod - 200'000;  // the first callback came `delay` after a latch
    std::uint64_t latched_frames = 0, worst_read_to_latch = 0, best_read_to_latch = UINT64_MAX;
    explicit Sim(double guard) : latch(guard) {}
    template <class Draw> void run(int frames, Draw draw) {
        latch.frame_done(now, kPeriod);  // the first callback
        for (int k = 0; k < frames; ++k) {
            const std::uint64_t callback = now;
            if (auto at = latch.schedule(now, kPeriod)) {
                now = *at + late;
                CHECK(!latch.schedule(now, kPeriod));
            }
            const std::uint64_t start = now, commit = start + draw(k);
            latch.committed(start, commit);
            // The commit is taken at the first latch at or after it, the callback follows that latch.
            std::uint64_t at = next_latch;
            while (at < commit) at += kPeriod;
            if (k >= 10) {  // past the first frames, which start early on purpose (no draw measured yet)
                worst_read_to_latch = std::max(worst_read_to_latch, at - start);
                best_read_to_latch = std::min(best_read_to_latch, at - start);
            }
            now = at + delay;
            next_latch = at + kPeriod;
            CHECK(now > callback);
            latch.frame_done(now, kPeriod);
        }
    }
};
}

int main() {
    // The flag: a finite 0..8 ms.
    CHECK(LateLatch::parse_guard("1"));
    CHECK(near(*LateLatch::parse_guard("1.5"), 1.5));
    CHECK(LateLatch::parse_guard("0"));
    CHECK(!LateLatch::parse_guard("-0.1"));
    CHECK(!LateLatch::parse_guard("8.5"));
    CHECK(!LateLatch::parse_guard("nan"));
    CHECK(!LateLatch::parse_guard("1ms"));
    CHECK(!LateLatch::parse_guard(""));
    CHECK(!LateLatch::parse_guard(nullptr));

    // Off: never waits, counts nothing.
    {
        LateLatch off;
        off.frame_done(10 * kMs, kPeriod);
        CHECK(!off.schedule(10 * kMs, kPeriod));
        off.committed(10 * kMs, 11 * kMs);
        CHECK(!off.summary(kPeriod).enabled);
        CHECK(off.summary(kPeriod).scheduled == 0);
    }

    // No period or no callback yet: draw at once, unscored.
    {
        LateLatch l(1.0);
        CHECK(!l.schedule(5 * kMs, kPeriod));  // no callback yet
        l.committed(5 * kMs, 6 * kMs);
        l.frame_done(20 * kMs, kPeriod);       // 14 ms after the commit, but the frame was not on the schedule
        CHECK(!l.schedule(20 * kMs, 0));       // period unknown
        const auto s = l.summary(kPeriod);
        CHECK(s.immediate == 2 && s.scheduled == 0 && s.missed == 0 && s.latched == 0);
    }

    // The target: callback + period - margin, margin = slowest draw + guard (no draws yet: the widest margin).
    {
        LateLatch l(1.0);
        l.frame_done(100 * kMs, kPeriod);
        const auto at = l.schedule(100 * kMs, kPeriod);
        CHECK(at && *at == 100 * kMs + kPeriod - static_cast<std::uint64_t>(0.75 * kPeriod));
        CHECK(!l.schedule(*at, kPeriod));
        l.committed(*at, *at + 300'000);  // 0.3 ms draw
        CHECK(near(l.margin_ms(kPeriod), 1.3));
        // A wake-up 0.2 ms late counts against the draw: 0.5 ms from the target.
        l.frame_done(*at + 1 * kMs, kPeriod);
        const auto at2 = l.schedule(*at + 1 * kMs, kPeriod);
        CHECK(at2);
        CHECK(!l.schedule(*at2 + 200'000, kPeriod));
        l.committed(*at2 + 200'000, *at2 + 500'000);
        CHECK(near(l.margin_ms(kPeriod), 1.5));
        // The margin is capped at three quarters of a period and floored at kMinMarginMs.
        LateLatch big(LateLatch::kMaxGuardMs);
        CHECK(near(big.margin_ms(kPeriod), 0.75 * kPeriod / 1e6));
        CHECK(near(big.margin_ms(0), LateLatch::kMaxGuardMs));
        LateLatch zero(0.0);
        CHECK(near(zero.margin_ms(0), LateLatch::kMinMarginMs));
    }

    // A callback whose latch already went by (the renderer was busy past it) is not aimed from.
    {
        LateLatch l(1.0);
        l.frame_done(100 * kMs, kPeriod);
        CHECK(!l.schedule(100 * kMs + kPeriod, kPeriod));
        CHECK(l.summary(kPeriod).immediate == 1);
    }

    // Steady state: draws of 0.4..0.9 ms, a 1 ms guard. Every frame makes the latch it aims at, a pose read about 2 ms
    // before the latch instead of a period before it.
    {
        Sim sim(1.0);
        sim.run(3000, [](int k) { return std::uint64_t(400'000 + (k % 6) * 100'000); });
        const auto s = sim.latch.summary(kPeriod);
        CHECK(s.missed == 0);
        CHECK(s.latched == 3000 && s.scheduled == 3000 && s.waited == 3000);
        CHECK(sim.worst_read_to_latch < 2 * kMs + 500'000);
        CHECK(sim.best_read_to_latch > 500'000);
        CHECK(near(s.draw_max_ms, 0.95, 1e-9) && near(s.margin_ms, 1.95, 1e-9));
        CHECK(s.slack_p5_ms > 0.9 && s.slack_p50_ms < 2.0);
        CHECK(s.draw_p50_ms > 0.5 && s.draw_p50_ms < 0.8 && s.draw_p99_ms > 0.9 && s.draw_p99_ms < 1.0);
    }

    // A draw spike past the margin misses once; the boost and the cost window keep the next ones from missing.
    {
        Sim sim(0.5);
        sim.run(1000, [](int k) { return std::uint64_t(k == 900 ? 3 * kMs : 400'000); });
        const auto s = sim.latch.summary(kPeriod);
        CHECK(s.missed == 1);
        CHECK(near(s.boost_ms, LateLatch::kMissBoostMs, 0.01));  // under 10 s since: no decay yet
        CHECK(s.draw_max_ms >= 3.0 && s.draw_worst_ms >= 3.0);
        CHECK(s.latched == 999);
    }

    // The boost is capped, and drains only after kBoostQuietS without a miss.
    {
        LateLatch l(0.5);
        std::uint64_t t = 1000 * kMs;
        for (int i = 0; i < 10; ++i) {
            l.frame_done(t, kPeriod);
            const auto at = l.schedule(t, kPeriod);
            CHECK(at);
            l.schedule(*at, kPeriod);
            l.committed(*at, *at + 100'000);
            t = *at + 2 * kPeriod;  // a refresh after the one aimed for: missed
        }
        l.frame_done(t, kPeriod);
        auto s = l.summary(kPeriod);
        CHECK(s.missed == 10);
        CHECK(near(s.boost_ms, LateLatch::kMaxBoostMs));
        l.frame_done(t + 5'000 * kMs, kPeriod);  // 5 s quiet: no drain
        CHECK(near(l.summary(kPeriod).boost_ms, LateLatch::kMaxBoostMs));
        l.frame_done(t + 20'000 * kMs, kPeriod);  // 20 s: 10 s of drain at 0.1 ms/s
        CHECK(near(l.summary(kPeriod).boost_ms, LateLatch::kMaxBoostMs - 1.0, 1e-6));
        l.frame_done(t + 200'000 * kMs, kPeriod);
        CHECK(near(l.summary(kPeriod).boost_ms, 0));
    }

    std::cout << "late_latch ok\n";
    return 0;
}
