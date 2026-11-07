// Independent QA of MotionSmoother, alone and in wayland.cpp's per-frame order (Stabilizer, PoseState, PoseAge, prediction_horizon).
// One case per run: `smoother_cases <name>`. Prints what it measured; exit 0 = holds, 1 = the claim failed, 2 = bad usage.
// Never aborts: no assert, no throw out of main.
#include "xr/motion_smooth.hpp"
#include "xr/predict.hpp"
#include "xr/stabilization.hpp"
#include <cmath>
#include <cstdio>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>
using namespace xr;
namespace {
constexpr float kDeg = 3.14159265f / 180.f;
constexpr double kFrame = 1000.0 / 120;
constexpr float kGaps[3] = {15, 15, 20};
Quat yaw(double deg) { return {0, 0, float(std::sin(deg * kDeg / 2)), float(std::cos(deg * kDeg / 2))}; }
double yaw_of(Quat q) { if (q.w < 0) { q.z = -q.z; q.w = -q.w; } return 2 * std::atan2(q.z, q.w) / kDeg; }
double between(Quat a, Quat b) { return quat_angle(a, b) / kDeg; }

// The renderer's frame, in wayland.cpp's order, on a head given as angle(device ms) on the measured cadence.
struct Sim {
    Stabilizer stabilizer; PoseState pose; PoseAge age; MotionSmoother smoother;
    std::optional<LookAhead> cfg = LookAhead{0, 0, 0, 40};
    bool prediction = true, smooth = true;
    double r2p = 16.6, now = 1000;
    float device = 0; int gap_index = 0;
    std::function<double(double)> head;
    double next_host = 1000;
    Quat base = yaw(0);
    FeedSample sample{};
    bool have = false;
    bool feed_on = true;
    void take_samples() {
        while (feed_on && next_host + 1 <= now) {
            FeedSample f{};
            f.older = {sample.orientation, sample.older[0]}; f.older_ms = {sample.device_ms, sample.older_ms[0]};
            f.orientation = quat_mul(base, yaw(head(device))); f.device_ms = device;
            sample = f; have = true;
            const float gap = kGaps[gap_index++ % 3];
            device += gap; next_host += gap;
        }
    }
    void recenter() { stabilizer.reset(); smoother.reset(); pose.recenter(); }  // as Presentation::recenter
    Quat frame() {
        now += kFrame;
        take_samples();
        std::optional<FeedSample> s; if (have) s = sample;
        auto stabilized = s;
        if (stabilized) stabilized->orientation = stabilizer.update(stabilized->orientation, stabilized->device_ms);
        else stabilizer.reset();
        pose.update(stabilized, now);
        Quat relative = pose.relative();
        const auto a = s ? age.observe(now, s->device_ms) : std::nullopt;
        if (prediction && pose.tracking()) {
            const auto omega = s ? angular_velocity(history_of(*s)) : std::nullopt;
            const auto horizon = prediction_horizon(cfg, r2p, a.value_or(0.0), kFrame);
            if (smooth && horizon && s) relative = smoother.update(relative, s->device_ms, *horizon, now);
            else if (omega && horizon) relative = quat_mul(relative, prediction_delta(*omega, *horizon * stabilizer.prediction_gain(), 15.f));
        } else if (smooth) smoother.reset();
        return relative;
    }
};
void settle(Sim& s, double ms) { for (double t = 0; t < ms; t += kFrame) s.frame(); }

int verdict(bool ok) { std::printf("%s\n", ok ? "HOLDS" : "BROKEN"); return ok ? 0 : 1; }

// A recentre with the head still, `deg` from the old reference. The raw view lands on the new reference on the next sample.
int recentre(double deg, double limit) {
    Sim s;
    s.head = [deg](double t) { return std::min(t / 1000, 1.0) * deg; };   // to `deg` in a second, then still
    settle(s, 2500);
    const double before = yaw_of(s.frame());
    s.recenter();
    // From the frame the new reference lands (the raw view is exactly on it from then on) the view should be there too.
    double past = 0, furthest = 0, first = 0; int late = 0, landed = -1; std::string path;
    for (int k = 0; k < 40; ++k) {
        const double shown = yaw_of(s.frame());
        if (landed < 0 && std::fabs(yaw_of(s.pose.relative())) < .01) { landed = k; first = shown; }
        if (landed < 0) continue;
        past = std::max(past, -shown * (deg > 0 ? 1 : -1)); furthest = std::max(furthest, std::fabs(shown));
        if (std::fabs(shown) > .1) ++late;
        if (k - landed < 8) { char b[16]; std::snprintf(b, sizeof b, " %.2f", shown); path += b; }
    }
    std::printf("recentre %.1f deg, shown before %.2f: at landing %.2f, furthest %.2f, past the new reference by %.2f deg, %d frames off by >0.1, snaps %llu; frames:%s\n",
                deg, before, first, furthest, past, late, (unsigned long long)s.smoother.snaps(), path.c_str());
    return verdict(past <= limit);
}

// A quick head turn: a bump of speed peaking at `peak` deg/s over `span` ms, in the middle of 2 s.
int whip(double peak, double span) {
    auto angle = [peak, span](double t) {
        const double u = (t - 1000) / span;
        if (u <= 0) return 0.0;
        if (u >= 1) return peak * span / 1000 * 2 / 3.14159265;
        return peak * span / 1000 * (1 - std::cos(3.14159265 * u)) / 3.14159265;
    };
    Sim s; s.head = angle;
    Quat last{}; double worst_excess = 0; int held = 0, moving = 0;
    for (double t = 0; t < 2400; t += kFrame) {
        const double device_now = s.now - 1000 + kFrame;
        const Quat shown = s.frame();
        const double ideal = std::fabs(angle(device_now + 16.6) - angle(device_now + 16.6 - kFrame));
        const double step = between(last, shown);
        if (ideal > peak / 2 * kFrame / 1000) { ++moving; if (step < .1 * ideal) ++held; worst_excess = std::max(worst_excess, step - ideal); }
        last = shown;
    }
    std::printf("peak %.0f deg/s: snaps %llu; in the fast part %d frames, %d held; biggest step past the ideal one %.2f deg (ideal at peak %.2f); max predicted %.2f\n",
                peak, (unsigned long long)s.smoother.snaps(), moving, held, worst_excess, peak * kFrame / 1000, s.smoother.max_predicted_deg());
    return verdict(s.smoother.snaps() == 0 && held == 0 && worst_excess < .5 * peak * kFrame / 1000);
}

// The feed goes quiet mid-turn for under the bridge's 100 ms stale limit. How far past the newest sample does the view go?
int stall(double dps, double quiet) {
    Sim s; s.head = [dps](double t) { return dps * t / 1000; };
    settle(s, 1000);
    s.feed_on = false;
    const double sample = yaw_of(quat_mul(quat_conj(s.pose.relative()), s.pose.relative())) + 0;
    (void)sample;
    double ahead = 0;
    for (double t = 0; t < quiet; t += kFrame) {
        const Quat shown = s.frame();
        const double held = yaw_of(s.pose.relative());
        ahead = std::max(ahead, yaw_of(shown) - held);
    }
    std::printf("%.0f deg/s, %.0f ms without a sample: view went %.2f deg past the newest sample (max_predict_deg 6, reported max %.2f)\n", dps, quiet, ahead, s.smoother.max_predicted_deg());
    return verdict(ahead <= 6.05 + std::max(0.0, (dps - 100) * .04));  // stall_ms of carry at speed: stated in docs/pro2/smoothness.md
}

int sign_flip() {
    MotionSmoother a, b; float t = 0; double worst = 0;
    for (int k = 0; k < 400; ++k) {
        Quat q = yaw(170 * std::sin(k * .01));   // up to +-170 degrees: well away from identity
        const Quat flipped = k % 2 ? Quat{-q.x, -q.y, -q.z, -q.w} : q;
        const double now = k * kFrame;
        if (k % 2 == 0) t += 15;
        worst = std::max(worst, between(a.update(q, t, 25, now), b.update(flipped, t, 25, now)));
    }
    std::printf("sign flips: worst difference %.5f deg\n", worst);
    return verdict(worst < .01);
}
int reset_is_fresh() {
    MotionSmoother used, fresh; float t = 0;
    for (int k = 0; k < 50; ++k) { used.update(yaw(k), t, 30, t); t += 15; }
    used.reset();
    double worst = 0;
    for (int k = 0; k < 50; ++k) { const double now = 1e4 + k * 15; const float d = 5000 + k * 15.f; worst = std::max(worst, between(used.update(yaw(-k), d, 30, now), fresh.update(yaw(-k), d, 30, now))); }
    std::printf("after reset vs a fresh one: %.6f deg, predicted %f\n", worst, used.last_predicted_deg());
    return verdict(worst == 0);
}
// Ctrl+Alt+P, off and on, mid-turn: no frame jumps by more than the prediction it brings back.
int toggle() {
    Sim s; s.head = [](double t) { return 40 * t / 1000; };
    settle(s, 1000);
    Quat last = s.frame(); double worst = 0;
    for (int k = 0; k < 480; ++k) {
        if (k % 96 == 48) s.prediction = !s.prediction;
        const Quat shown = s.frame();
        // The switch itself may step: off drops the prediction. Away from it nothing should.
        if (k % 96 < 48 || k % 96 >= 58) worst = std::max(worst, between(last, shown));
        last = shown;
    }
    std::printf("toggling at 40 deg/s: largest step away from a switch %.3f deg (one frame of motion 0.333)\n", worst);
    return verdict(worst < 1.0);
}
int clocks() {
    MotionSmoother m; bool finite = true; Quat q{};
    auto check = [&](Quat o) { finite = finite && std::isfinite(o.x) && std::isfinite(o.y) && std::isfinite(o.z) && std::isfinite(o.w) && std::fabs(o.x*o.x+o.y*o.y+o.z*o.z+o.w*o.w-1) < 1e-3f; q = o; };
    float t = 0;
    for (int k = 0; k < 30; ++k) { check(m.update(yaw(k), t, 30, k * 8.3)); t += 15; }
    const Quat before = q;
    check(m.update(yaw(30), t, 30, 0));              // the clock goes back: dt 0, nothing moves
    const double back = between(before, q);
    check(m.update(yaw(30), t, 30, 1e12));           // a huge frame gap: bounded
    check(m.update(yaw(31), t + 15, INFINITY, 1e12 + 8)); check(m.update(yaw(31), t + 15, -5, 1e12 + 16));
    check(m.update(Quat{0, 0, 0, 0}, t + 30, 30, 1e12 + 24));   // a zero quaternion: identity, not NaN
    check(m.update(Quat{NAN, 0, 0, 1}, t + 45, 30, 1e12 + 32));
    check(m.update(yaw(2), NAN, 30, 1e12 + 40)); check(m.update(yaw(2), t + 60, 30, NAN));
    std::printf("clock back moved %.5f deg, finite %d\n", back, finite);
    return verdict(finite && back < 1e-3);
}
// The publisher's t restarts mid-turn (kRestartMs: every 4.66 h, inside a 12 h session).
int epoch() {
    MotionSmoother m; float t = 16776000; double now = 0, a = 0, worst = 0; Quat last{};
    for (int k = 0; k < 400; ++k) {
        if (k == 200) t = 0;
        const double before = now;
        for (int j = 0; j < 2; ++j) { now += kFrame; const Quat o = m.update(yaw(a), t, 28, now); if (k > 10) worst = std::max(worst, between(last, o)); last = o; }
        (void)before;
        t += kGaps[k % 3]; a += 60 * kGaps[k % 3] / 1000;
    }
    std::printf("epoch restart at 60 deg/s: largest step %.3f deg (one frame of motion 0.5), snaps %llu\n", worst, (unsigned long long)m.snaps());
    return verdict(worst < 1.0 && m.snaps() == 0);
}
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::map<std::string, std::function<int()>> cases{
        {"recentre_2", [] { return recentre(2, .25); }}, {"recentre_5", [] { return recentre(5, .25); }}, {"recentre_6", [] { return recentre(6, .25); }}, {"recentre_7", [] { return recentre(7, .25); }}, {"recentre_20", [] { return recentre(20, .25); }},
        {"whip_150", [] { return whip(150, 300); }}, {"whip_250", [] { return whip(250, 300); }}, {"whip_350", [] { return whip(350, 300); }}, {"whip_500", [] { return whip(500, 300); }}, {"whip_700", [] { return whip(700, 250); }},
        {"stall_100", [] { return stall(100, 90); }}, {"stall_200", [] { return stall(200, 90); }}, {"stall_50", [] { return stall(50, 90); }},
        {"sign_flip", sign_flip}, {"reset_is_fresh", reset_is_fresh}, {"toggle", toggle}, {"clocks", clocks}, {"epoch", epoch}};
    const auto it = cases.find(argv[1]);
    return it == cases.end() ? 2 : it->second();
}
