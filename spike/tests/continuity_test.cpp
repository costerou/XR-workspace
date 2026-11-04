#include "xr/continuity.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#define CHECK(x) do { if (!(x)) { std::cerr << #x << " failed\n"; return 1; } } while(0)
using namespace xr;
int main() {
    ViewContinuity a;
    a.pose.update(FeedSample{Quat{}, 1}, 100);
    a.pose.update(FeedSample{aim_rotation(.4f, .1f), 2}, 120);
    a.modes.turn_anchor(aim_rotation(.2f, .05f));
    a.modes.update(a.pose.relative(), 120);
    a.modes.set_mode(ViewMode::follow, 130, ModeController::Change::maximize);
    a.maximized = true;
    a.view.set_target(true, 1, 70, 100);
    a.view.advance(240);
    a.rolls = {1250, -650, 20, 0};
    a.saved_ms = 240;
    ViewContinuity b;
    CHECK(b.decode(a.encode()));
    b.resume(3240);
    CHECK(b.maximized && b.modes.holding());
    CHECK(b.rolls == a.rolls);
    CHECK(quat_angle(a.modes.anchor(), b.modes.anchor()) < .0001);
    CHECK(a.pose.reference() && b.pose.reference() && quat_angle(*a.pose.reference(), *b.pose.reference()) < .0001);
    CHECK(a.pose.view() == b.pose.view());
    b.view.advance(3240);
    CHECK(std::abs(a.view.zoom() - b.view.zoom()) < .0001);
    CHECK(!b.decode("bad snapshot"));
    auto corrupt = a.encode(); corrupt.back() ^= 1;
    CHECK(!b.decode(corrupt));
    CHECK(b.rolls == a.rolls);  // rejected snapshots do not partly replace state

    // The pending recenter flag, and its learned up/roll reference, survive.
    a.pose.recenter();
    CHECK(b.decode(a.encode())); b.resume(3240);
    const FeedSample head{aim_rotation(.7f, .2f), 3};
    a.pose.update(head, 250); b.pose.update(head, 3250);
    CHECK(a.pose.view() == b.pose.view());
    CHECK(quat_angle(*a.pose.reference(), *b.pose.reference()) < .0001);

    // Resume a moving follow spring, then unmaximize back to the yaw anchor
    // saved before maximize. Compare with an uninterrupted reference timeline.
    a.modes.land_maximize(240);
    for (double t = 240; t <= 400; t += 8) a.modes.update(aim_rotation(.8f, .1f), t);
    a.saved_ms = 400;
    CHECK(b.decode(a.encode())); b.resume(3400);
    for (double t = 408; t <= 800; t += 8) {
        const auto x = a.modes.update(aim_rotation(.9f, .1f), t);
        const auto y = b.modes.update(aim_rotation(.9f, .1f), t + 3000);
        CHECK(quat_angle(x, y) < .001);
    }
    a.modes.set_mode(ViewMode::anchor, 800, ModeController::Change::restore);
    b.modes.set_mode(ViewMode::anchor, 3800, ModeController::Change::restore);
    CHECK(quat_angle(a.modes.update(head.orientation, 1200), b.modes.update(head.orientation, 4200)) < .001);

    // Freeze an edge pan, including its easing speed and timeout clock.
    a.view = ViewAnimator{};
    a.view.set_target(true, 0, 40, 0); a.view.advance(1000);
    EdgePan pan; pan.active = true; pan.from = 0; pan.to = 1;
    pan.exit = pan.entry = .3f; pan.peek = .7f; pan.cross = .3f;
    pan.turn = .4f; pan.way = 2.f; pan.zoom = .2f;
    a.view.set_pan(pan, 1000); a.view.advance(1020); a.saved_ms = 1020;
    CHECK(b.decode(a.encode())); b.resume(4020);
    CHECK(b.view.pan_active());
    CHECK(a.view.pan_cross() == b.view.pan_cross());
    CHECK(a.view.pan_turn() == b.view.pan_turn());
    a.view.set_target(true, 1, 40, 1030, false, false, true);
    b.view.set_target(true, 1, 40, 4030, false, false, true);
    for (double t = 1030; t < 2200; t += 8) {
        a.view.advance(t); b.view.advance(t + 3000);
        CHECK(std::abs(a.view.zoom() - b.view.zoom()) < .0001);
        CHECK(std::abs(a.view.turn() - b.view.turn()) < .0001);
    }
    // A turn waiting to be handed to the world anchor must land once, in order.
    auto landed = a.view.take_landed_turns();
    auto resumed_landed = b.view.take_landed_turns();
    CHECK(!landed.empty() && landed.size() == resumed_landed.size());
    CHECK(landed[0].from == resumed_landed[0].from && landed[0].to == resumed_landed[0].to);
    a.saved_ms = 2200;
    // Capture another centred turn's serial and direction as well as its queue.
    a.view.set_target(true, 2, 40, 2200, true);
    a.view.advance(4000); a.saved_ms = 4000;
    a.centre_valid = true; a.centre_serial = a.view.serial(); a.centre_dir = {.2f, .3f};
    CHECK(b.decode(a.encode())); b.resume(7000);
    CHECK(b.centre_valid && b.centre_serial == a.centre_serial && b.centre_dir == a.centre_dir);
    CHECK(!b.view.take_landed_turns().empty());
    CHECK(b.view.take_landed_turns().empty());
    std::cout << "renderer continuity preserved\n";
}
