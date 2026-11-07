// Cody, first smoothing run: "when I turned right the picture leaned left, and when I turned left it leaned right". The head turns
// about gravity; the glasses' frame starts pitched against it, so in that frame the same turn has a roll in it. Feed frame: x forward,
// y left, z up, as the Pro 2 bridge publishes it, gravity level.
#include "view_lean.hpp"
#include <cmath>
#include <cstdio>
#include <iostream>
#include <stdexcept>
#include <string>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x " at line " + std::to_string(__LINE__)); } while (0)
using namespace xr;
namespace {
constexpr float kRad = 3.14159265f / 180.f;
Quat about(float x, float y, float z, float deg) { const float h = deg * kRad / 2, s = std::sin(h); return {x * s, y * s, z * s, std::cos(h)}; }
struct Run {
    PoseState pose; float t = 0;
    explicit Run(bool level) { pose.level_reference(level); }
    Shown shown(Quat head) { t += 16; FeedSample s{}; s.orientation = head; s.device_ms = t; pose.update(s, t); return shown_by(view_from(pose.relative(), Quat{})); }
};
// Head turned `yaw` degrees to the right about gravity, from a start `pitch` degrees below level (+y turns forward down).
Quat head(float pitch, float yaw) { return quat_mul(about(0, 0, 1, -yaw), about(0, 1, 0, pitch)); }
}
int run() {
    {   // What the renderer did with the Pro 2's 19.5 degrees: the roll opposes the turn and is symmetric, 3.3 degrees per 10 of yaw.
        Run r(false); r.shown(head(19.5f, 0));
        const Shown right = r.shown(head(19.5f, 20)), left = r.shown(head(19.5f, -20));
        std::printf("unlevelled, 19.5 degree start: right %.2f yaw %.2f roll, left %.2f yaw %.2f roll\n", right.yaw_deg, right.roll_deg, left.yaw_deg, left.roll_deg);
        CHECK(right.yaw_deg > 15 && left.yaw_deg < -15);
        CHECK(right.roll_deg > 5 && left.roll_deg < -5);             // right leans left, left leans right
        CHECK(std::fabs(right.roll_deg + left.roll_deg) < 0.1f);
    }
    for (const float pitch : {19.5f, 27.f, 8.f, -10.f}) {   // the same head, levelled at the recentre, whatever its pitch there
        Run r(true);
        const Shown still = r.shown(head(pitch, 0));
        CHECK(std::fabs(still.roll_deg) < 0.01f && std::fabs(still.yaw_deg) < 0.01f);  // nothing moves at the start
        for (const float yaw : {5.f, 10.f, 20.f, 30.f, -5.f, -10.f, -20.f, -30.f}) {
            const Shown s = r.shown(head(pitch, yaw));
            CHECK(std::fabs(s.yaw_deg - yaw) < 0.05f);   // the turn is all still there
            CHECK(std::fabs(s.roll_deg) < 0.05f);
        }
    }
    {   // A recentre levels again, at the new pose.
        Run r(true); r.shown(head(10, 0));
        r.pose.recenter(); r.shown(head(25, 40));
        const Shown s = r.shown(head(25, 55));
        CHECK(std::fabs(s.yaw_deg - 15) < 0.05f && std::fabs(s.roll_deg) < 0.05f);
    }
    {   // Looking up and down still reads as that, and a roll of the head still shows, once the frame is level.
        Run r(true); r.shown(head(19.5f, 0));
        const Shown up = r.shown(head(9.5f, 0));
        CHECK(std::fabs(up.roll_deg) < 0.05f);
        const Quat tipped = quat_mul(about(1, 0, 0, 10), head(0, 0));   // ten degrees of roll about level forward
        Run s(true); s.shown(head(0, 0));
        CHECK(std::fabs(std::fabs(s.shown(tipped).roll_deg) - 10) < 0.1f);
    }
    {   // Off (every other glasses' feed): the pose path is exactly the old one.
        Run on(false); PoseState plain;
        FeedSample a{}; a.orientation = head(19.5f, 0); a.device_ms = 16; on.pose.update(a, 16); plain.update(a, 16);
        a.orientation = head(19.5f, 12); a.device_ms = 32; on.pose.update(a, 32); plain.update(a, 32);
        const Quat x = on.pose.relative(), y = plain.relative();
        CHECK(x.x == y.x && x.y == y.y && x.z == y.z && x.w == y.w);
    }
    std::cout << "ok\n";
    return 0;
}
// A failed check returns non-zero: an abort would raise a desktop crash notification on the machine that runs it.
int main() {
    try { return run(); }
    catch (const std::exception& e) { std::cerr << "FAILED: " << e.what() << '\n'; return 1; }
}
