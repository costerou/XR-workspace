#include "xr/motion_smooth.hpp"
#include "xr/predict.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <vector>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x " at line " + std::to_string(__LINE__)); } while (0)
using namespace xr;
namespace {
constexpr float kDeg = 3.14159265f / 180.f;
Quat yaw(float deg) { return {0, 0, std::sin(deg * kDeg / 2), std::cos(deg * kDeg / 2)}; }
float yaw_of(Quat q) { return 2 * std::atan2(q.z, q.w) / kDeg; }
float between(Quat a, Quat b) { return quat_angle(a, b) / kDeg; }
// The measured cadence: gaps[i % 3] ms between samples.
constexpr float kGaps[3] = {15, 15, 20};
struct Feed {
    float t = 0, angle = 0; int i = 0;
    void advance(float dps) { const float gap = kGaps[i++ % 3]; t += gap; angle += dps * gap / 1000; }
};
}
int run() {
    CHECK(!motion_smooth_params(NAN) && !motion_smooth_params(4.9f) && !motion_smooth_params(80.5f));
    CHECK(motion_smooth_params(25.f)->tau_ms == 25.f);

    {   // A held sample at a constant speed: every frame moves by the same amount, through the uneven cadence.
        MotionSmoother m; Feed f; const float dps = 30, frame = 1000.f / 120;
        for (int k = 0; k < 6; ++k) { m.update(yaw(f.angle), f.t, 20, f.t); f.advance(dps); }
        double now = f.t; float next = f.t, previous = 0, last_step = -1; int checked = 0;
        Quat before = m.update(yaw(f.angle), f.t, 20, now);
        for (int k = 0; k < 240; ++k) {
            now += frame;
            while (next + kGaps[f.i % 3] <= now) { f.advance(dps); next = f.t; }
            const float age = float(now) - f.t;
            const Quat shown = m.update(yaw(f.angle), f.t, 20 + age, now);
            const float step = between(before, shown);
            if (k > 60) { CHECK(std::fabs(step - dps * frame / 1000) < 0.002f); ++checked; }
            before = shown; previous = step; last_step = step;
        }
        CHECK(checked > 100 && previous == last_step);
        // and it sits on the prediction: newest sample plus the speed times 20 ms plus the age.
        CHECK(std::fabs(yaw_of(before) - (f.angle + dps * (20 + (float(now) - f.t)) / 1000)) < 0.01f);
    }
    {   // A stop: the view must not run on past the head by more than a bounded amount, and must come back to it.
        MotionSmoother m; Feed f; double now = 0; float peak = 0;
        for (int k = 0; k < 30; ++k) { m.update(yaw(f.angle), f.t, 30, now = f.t); f.advance(40); }
        for (int k = 0; k < 30; ++k) {
            f.advance(0);
            for (int j = 0; j < 2; ++j) {
                now += 8.333;
                peak = std::max(peak, yaw_of(m.update(yaw(f.angle), f.t, 30, now)));
            }
        }
        CHECK(peak - f.angle < 0.8f);  // constant-velocity extrapolation of 30 ms at 40 deg/s would be 1.2
        CHECK(std::fabs(yaw_of(m.update(yaw(f.angle), f.t, 30, now + 8.333)) - f.angle) < 0.02f);
    }
    {   // Rotation the prediction adds is bounded however fast the feed claims to go.
        MotionSmoother m; Feed f;
        for (int k = 0; k < 20; ++k) { m.update(yaw(f.angle), f.t, 40, f.t); f.advance(2000); }
        CHECK(m.max_predicted_deg() <= 6.001f);
    }
    {   // The turn axis: a head turning both ways about an axis 7 degrees forward of this frame's up, seen as 7 forward, either way.
        MotionSmoother m; float t = 0; double now = 0; float angle = 0, dps = 40;
        const float a = 7 * kDeg;
        for (int k = 0; k < 400; ++k) {
            if (k % 100 == 0) dps = -dps;
            const float half = angle * kDeg / 2, s = std::sin(half);
            m.update(Quat{std::sin(a) * s, 0, std::cos(a) * s, std::cos(half)}, t, 20, now);
            t += kGaps[k % 3]; now = t; angle += dps * kGaps[k % 3] / 1000;
        }
        CHECK(std::fabs(m.turn_axis_forward_deg() - 7) < 0.2f);
        CHECK(std::fabs(m.turn_axis_left_deg()) < 0.2f);
        CHECK(m.turn_seconds() > 4);
    }
    {   // A jump no head makes is not motion: taken at once, counted. (A recentre resets instead: Presentation::recenter.)
        MotionSmoother m; Feed f;
        for (int k = 0; k < 8; ++k) { m.update(yaw(f.angle + 50), f.t, 20, f.t); f.advance(0); }
        const Quat shown = m.update(yaw(0), f.t, 20, f.t);
        CHECK(between(shown, yaw(0)) < 0.01f && m.snaps() == 1);
    }
    {   // A hard turn at 700 deg/s is a head, not a jump: no snaps, and the view moves every frame.
        MotionSmoother m; Feed f; double now = 0; Quat before{}; int still = 0;
        for (int k = 0; k < 40; ++k) {
            f.advance(700);
            for (int j = 0; j < 2; ++j) {
                now += 8.333;
                const Quat shown = m.update(yaw(f.angle), f.t, 30, now);
                if (k > 2 && between(before, shown) < 1.f) ++still;
                before = shown;
            }
        }
        CHECK(m.snaps() == 0 && still == 0);
    }
    {   // A stalled feed: speed stops being carried after stall_ms, so the view stops short of running on, then settles within the bound.
        MotionSmoother m; Feed f; double now = 0;
        for (int k = 0; k < 12; ++k) { f.advance(200); m.update(yaw(f.angle), f.t, 30, now = f.t); }
        float furthest = 0;
        float settled = 0;
        for (int k = 0; k < 48; ++k) { now += 8.333; settled = between(m.update(yaw(f.angle), f.t, 40, now), yaw(f.angle)); furthest = std::max(furthest, settled); }
        CHECK(furthest <= 6.f + 200.f * 0.04f);  // the bound, then speed for stall_ms at the very most
        CHECK(settled <= 6.01f);
    }
    {   // A gap too long for a velocity: no prediction, and a repeated device time does not disturb the history.
        MotionSmoother m; Feed f;
        for (int k = 0; k < 6; ++k) { m.update(yaw(f.angle), f.t, 30, f.t); f.advance(10); }
        const float later = f.t + 500;
        m.update(yaw(f.angle), later, 30, later);
        CHECK(m.last_predicted_deg() == 0.f);
        const Quat same = m.update(yaw(f.angle), later, 30, later + 8);
        CHECK(between(same, yaw(f.angle)) < 0.2f);
    }
    {   // A new clock epoch (the publisher's t restarts) forgets the old history instead of reading a negative gap.
        MotionSmoother m; Feed f;
        for (int k = 0; k < 6; ++k) { m.update(yaw(f.angle), f.t, 30, f.t); f.advance(10); }
        m.update(yaw(f.angle), 0, 30, f.t + 8);
        CHECK(m.last_predicted_deg() == 0.f);
    }
    {   // A measured velocity (the bridge's wire) replaces the estimate: a 240 Hz feed read once a frame predicts exactly that speed,
        // a measured stop predicts nothing though the samples seen are moving, and a non-finite one falls back to the estimate.
        MotionSmoother m; float t = 0, angle = 0; const float dps = 30, rad_per_ms = dps * kDeg / 1000;
        for (int k = 0; k < 40; ++k) { t += 4.1667f; angle += dps * 4.1667f / 1000; if (k % 2) m.update(yaw(angle), t, 20, t, std::array<float, 3>{0, 0, rad_per_ms}); }
        CHECK(std::fabs(m.last_predicted_deg() - dps * 0.020f) < 0.001f);
        t += 4.1667f; angle += dps * 4.1667f / 1000;
        m.update(yaw(angle), t, 20, t, std::array<float, 3>{0, 0, 0});
        CHECK(m.last_predicted_deg() == 0.f);
        t += 4.1667f; angle += dps * 4.1667f / 1000;
        m.update(yaw(angle), t, 20, t, std::array<float, 3>{NAN, 0, 0});
        CHECK(std::fabs(m.last_predicted_deg() - dps * 0.020f) < 0.05f);
    }
    {   // Non-finite input never poisons the state; reset forgets everything.
        MotionSmoother m;
        const Quat q = m.update(yaw(5), NAN, 20, 0);
        CHECK(between(q, yaw(5)) < 0.01f);
        m.update(yaw(5), 10, NAN, 10);
        CHECK(m.last_horizon_ms() == 0.f);
    }
    std::cout << "ok\n";
    return 0;
}
// A failed check returns non-zero: an abort would raise a desktop crash notification on the machine that runs it.
int main() {
    try { return run(); }
    catch (const std::exception& e) { std::cerr << "FAILED: " << e.what() << '\n'; return 1; }
}
