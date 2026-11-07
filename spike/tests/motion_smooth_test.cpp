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
    {   // A recentre is not motion: taken at once, counted.
        MotionSmoother m; Feed f;
        for (int k = 0; k < 8; ++k) { m.update(yaw(f.angle + 50), f.t, 20, f.t); f.advance(0); }
        const Quat shown = m.update(yaw(0), f.t, 20, f.t);
        CHECK(between(shown, yaw(0)) < 0.01f && m.snaps() == 1);
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
