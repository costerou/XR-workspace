// Regression (2026-11-03): in the glasses, follow and locked modes did not centre the focused desktop. Since zooming keeps a
// dragged desktop's direction round you (view.hpp zoomed_placements), the zoomed desktop sits at its slot's direction plus
// its orbit relative to the canvas, and follow / locked glued the canvas centre to the head, so a desktop left or right
// of the middle slot (or dragged anywhere) stayed off centre however you turned. Now the canvas is aimed at the focused
// desktop (focus_direction / focus_weight -> aim_rotation -> ModeController::set_aim). Placements are untouched.
// End to end: placement maths (view.cpp) and the head view (pose.cpp) together, projecting the focused desktop's centre.
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string("line ") + std::to_string(__LINE__) + ": " #x); } while (0)
using namespace xr;
namespace {
constexpr float kPi = 3.14159265f;
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = deg * kPi / 360.f, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}
float rad(float deg) { return deg * kPi / 180.f; }
const Size kSizes[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
const float kHalfW = std::tan(rad(23.f)), kHalfH = kHalfW * 1200.f / 1920.f;
const Canvas kCanvas{1920, 1200, kHalfW, kHalfH};
// Desktop 0 dragged 30 deg right and 12 deg up (now right of the middle one), 1 at its slot, 2 dragged 140 deg (behind you).
const Placement kPlaced[] = {{rad(30), rad(12), 1.4f, 0, 0}, {}, {rad(140), rad(-5), 1, 0, 0}};

// Angle (degrees) between the view's centre and where desktop `focus`'s centre is drawn with `view` at `zoom`.
float off_centre(const Mat3& view, std::size_t focus, float zoom) {
    const auto overview = overview_layout(kSizes, 1920, 1200, 40);
    const auto tiles = blended_layout(kSizes, 1920, 1200, 40, zoom, float(focus));
    const auto placed = zoomed_placements(kCanvas, overview, tiles, kPlaced, zoom, float(focus));
    const FRect& t = tiles[focus];
    const Vec3 p = place_point(kCanvas, t, placed[focus], t.x + t.width / 2, t.y + t.height / 2);
    const auto v = apply(view, {p.x, p.y, p.z});
    const float n = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return std::acos(std::clamp(-v[2] / n, -1.f, 1.f)) * 180.f / kPi;
}
Quat aim_for(std::size_t focus, float weight) {
    const auto overview = overview_layout(kSizes, 1920, 1200, 40);
    const auto d = focus_direction(kCanvas, overview, kPlaced, focus);
    return aim_rotation(weight * d[0], weight * d[1]);
}
}

int main() {
    const auto overview = overview_layout(kSizes, 1920, 1200, 40);
    // focus_direction is the slot's direction plus the orbit; the middle slot is straight ahead.
    {
        const auto mid = focus_direction(kCanvas, overview, kPlaced, 1);
        CHECK(std::fabs(mid[0]) < 1e-5f && std::fabs(mid[1]) < 1e-5f);
        const auto d0 = focus_direction(kCanvas, overview, kPlaced, 0);
        CHECK(d0[0] > rad(30) - rad(20) && d0[0] < rad(30) && std::fabs(d0[1] - rad(12)) < 1e-4f);  // slot left, dragged right
        const auto out = focus_direction(kCanvas, overview, kPlaced, 9);
        CHECK(out[0] == 0 && out[1] == 0);
        // aim_rotation: identity at (0, 0); the view it makes puts direction (az, el) on the centre, upright.
        const Quat id = aim_rotation(0, 0);
        CHECK(std::fabs(id.w - 1) < 1e-6f);
        for (float az : {-170.f, -40.f, 0.f, 25.f, 140.f})
            for (float el : {-60.f, -5.f, 0.f, 12.f, 70.f}) {
                const Mat3 view = view_from(Quat{}, aim_rotation(rad(az), rad(el)));
                const float ca = std::cos(rad(az)), sa = std::sin(rad(az)), ce = std::cos(rad(el)), se = std::sin(rad(el));
                const auto f = apply(view, {sa * ce, se, -ca * ce});  // place_point's direction (az, el)
                CHECK(std::fabs(f[0]) < 1e-4f && std::fabs(f[1]) < 1e-4f && f[2] < 0);
                const auto up = apply(view, {-sa * se, ce, ca * se});  // its up along the meridian stays up
                CHECK(std::fabs(up[0]) < 1e-4f && up[1] > 0.999f);
            }
    }
    const Quat heads[] = {Quat{}, axis_angle(0, 0, 1, 25), quat_mul(axis_angle(0, 0, 1, -50), axis_angle(0, 1, 0, 15)),
                          quat_mul(axis_angle(0, 0, 1, 170), axis_angle(1, 0, 0, 10))};
    // Locked: zoomed into any desktop, however it was dragged and wherever the head points, it is dead centre.
    for (std::size_t focus = 0; focus < 3; ++focus)
        for (const Quat& head : heads) {
            ModeController m;
            double t = 0;
            m.set_mode(ViewMode::locked, t);
            m.set_aim(aim_for(focus, 1));
            m.update(head, t += 400);
            const Quat anchor = m.update(head, t += 8);
            CHECK(off_centre(view_from(head, anchor), focus, 1) < 0.05f);
            // Without the aim (the bug): every desktop but the middle one is off centre.
            if (focus != 1) CHECK(off_centre(view_from(head, head), focus, 1) > 10.f);
        }
    // Follow: once the spring settles it is centred too, and a head turn is followed back onto it.
    for (std::size_t focus = 0; focus < 3; ++focus) {
        ModeController m;
        double t = 0;
        m.set_mode(ViewMode::follow, t);
        m.set_aim(aim_for(focus, 1));
        for (const Quat& head : heads) {
            for (int i = 0; i < 300; ++i) m.update(head, t += 8);
            CHECK(off_centre(view_from(head, m.update(head, t += 8)), focus, 1) < 0.1f);
            CHECK(m.settled());
        }
    }
    // The overview (weight 0) is unchanged in locked and follow: the canvas centre straight ahead.
    {
        ModeController m;
        m.set_mode(ViewMode::locked, 0);
        m.set_aim(aim_for(0, 0));
        const Quat head = axis_angle(0, 0, 1, 33);
        m.update(head, 400);
        const Quat anchor = m.update(head, 408);
        CHECK(quat_angle(anchor, head) < 1e-4f);
    }
    // Anchor: the room stays put (the aim moves nothing), and a recentre while zoomed puts the focused desktop ahead.
    {
        ModeController m;
        double t = 0;
        m.set_aim(aim_for(0, 1));
        CHECK(quat_angle(m.update(axis_angle(0, 0, 1, 40), t += 8), Quat{}) < 1e-5f);  // world-locked, as before
        m.recenter();  // PoseState's reference moves to the head too: the relative head is the identity again
        const Quat anchor = m.update(Quat{}, t += 8);
        CHECK(off_centre(view_from(Quat{}, anchor), 0, 1) < 0.05f);
        const Quat turned = axis_angle(0, 0, 1, -20);  // and then it is world-locked there
        CHECK(quat_angle(m.update(turned, t += 8), anchor) < 1e-5f);
        m.set_aim(aim_for(0, 0));
        m.recenter();  // in the overview the canvas centre comes straight ahead, as before
        CHECK(quat_angle(m.update(Quat{}, t += 8), Quat{}) < 1e-5f);
    }
    // Mode changes keep blending from where the canvas is: follow -> anchor pins it with the focused desktop ahead, and
    // anchor -> follow starts there with no jump.
    {
        ModeController m;
        double t = 0;
        const Quat head = axis_angle(0, 0, 1, 15);
        m.set_mode(ViewMode::follow, t);
        m.set_aim(aim_for(2, 1));
        for (int i = 0; i < 300; ++i) m.update(head, t += 8);
        const Quat here = m.update(head, t += 8);
        m.set_mode(ViewMode::anchor, t);
        for (int i = 0; i < 60; ++i) CHECK(quat_angle(m.update(axis_angle(0, 0, 1, -30), t += 8), here) < 1e-4f);
        m.set_mode(ViewMode::follow, t);
        CHECK(quat_angle(m.update(axis_angle(0, 0, 1, -30), t += 1), here) < rad(2));
    }
    // A switch (zoom out, turn, zoom in) keeps the aim continuous in every phase: it starts on the current desktop, ends on
    // the target, never jumps; also when it is cancelled mid-turn.
    for (bool cancel : {false, true}) {
        ViewAnimator v(220, 300);
        double t = 0;
        v.set_target(true, 0, 40, t);
        for (; t < 400; t += 1) v.advance(t);
        CHECK(focus_weight(v) == 1.f && v.focus() == 0);
        v.set_target(true, 2, 40, t);
        Quat last = aim_for(v.focus(), focus_weight(v));
        CHECK(quat_angle(last, aim_for(0, 1)) < 1e-5f);
        float worst = 0;
        for (double end = t + 1200; t < end; t += 1) {
            if (cancel && t > 400 + 220 + 150 && t < 400 + 220 + 152) v.set_target(false, 2, 40, t);
            v.advance(t);
            const Quat now = aim_for(v.focus(), focus_weight(v));
            worst = std::max(worst, quat_angle(now, last));
            last = now;
        }
        CHECK(worst < rad(2));  // per ms
        CHECK(!v.switching());
        CHECK(quat_angle(last, cancel ? Quat{} : aim_for(2, 1)) < 1e-5f);
    }
    std::cout << "focused desktop centred in locked and follow, anchor recentre, switches continuous\n";
}
