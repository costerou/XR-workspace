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
// kPlaced with a levelling roll on every desktop (focus_direction ignores it, so aim_for still applies).
const Placement kRolled[] = {{rad(30), rad(12), 1.4f, 0, rad(20)}, {0, 0, 1, 0, rad(-35)}, {rad(140), rad(-5), 1, 0, rad(7.5f)}};
// How far (degrees) maximized desktop `focus` is from level and square to the glasses as `view` draws it: its left-right
// midline should run exactly along the view's x, its bottom-top midline along y.
float off_square(const Mat3& view, std::size_t focus) {
    const auto overview = overview_layout(kSizes, 1920, 1200, 40);
    const auto tiles = blended_layout(kSizes, 1920, 1200, 40, 1, float(focus));
    const auto placed = zoomed_placements(kCanvas, overview, tiles, kRolled, 1, float(focus));
    const FRect& t = tiles[focus];
    auto seen = [&](float px, float py) { const Vec3 p = place_point(kCanvas, t, placed[focus], px, py); return apply(view, {p.x, p.y, p.z}); };
    const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
    const auto l = seen(t.x, cy), r = seen(t.x + t.width, cy), b = seen(cx, t.y + t.height), u = seen(cx, t.y);
    const std::array<float, 3> across{r[0] - l[0], r[1] - l[1], r[2] - l[2]}, up{u[0] - b[0], u[1] - b[1], u[2] - b[2]};
    auto off = [](float along, float a, float c) { return std::atan2(std::hypot(a, c), along) * 180.f / kPi; };
    return std::max(off(across[0], across[1], across[2]), off(up[1], up[0], up[2]));
}
}

// The view's turn of an edge pan (view.hpp EdgePan, pose.hpp aim_along) and its landing, through the real head view.
void edge_pan_tests() {
    const auto overview = overview_layout(kSizes, 1920, 1200, 40);
    const auto dir = [](float az, float el) { return aim_rotation(rad(az), rad(el)); };
    {  // One rotation from end to end: exactly each aim at its end, and each share of the way that share of the angle.
        const Quat a = dir(-30, 10), b = dir(40, -20);
        const float way = quat_angle(a, b);
        CHECK(quat_angle(aim_along(a, b, way, 0, 0), a) < 1e-3f && quat_angle(aim_along(a, b, way, way, 0), b) < 1e-3f);
        CHECK(quat_angle(aim_along(a, b, way, way * 2, 0), b) < 1e-3f);
        for (const float s : {0.2f, 0.5f, 0.8f}) {
            const Quat q = aim_along(a, b, way, way * s, 0);
            CHECK(std::fabs(quat_angle(a, q) - way * s) < 2e-3f && std::fabs(quat_angle(q, b) - way * (1 - s)) < 2e-3f);
        }
    }
    {  // Level neighbours: the view goes straight along, its height never moving.
        const Quat a = dir(-20, 0), b = dir(20, 0);
        const float way = quat_angle(a, b);
        float was = -21;
        for (float s = 0; s <= 1.f; s += 0.1f) {
            const auto c = view_centre(view_from(Quat{}, aim_along(a, b, way, way * s, 0)));
            CHECK(c[0] * 180 / kPi > was && std::fabs(c[1]) < 1e-3f);
            was = c[0] * 180 / kPi;
        }
    }
    {  // Raised neighbours: upright at both ends and hardly rolled between them, the centre going one way all along.
        const Quat a = dir(-22, 9), b = dir(20, 9);
        const float way = quat_angle(a, b);
        float was = -23;
        for (float s = 0; s <= 1.f; s += 0.1f) {
            const auto c = view_centre(view_from(Quat{}, aim_along(a, b, way, way * s, 0)));
            CHECK(c[0] * 180 / kPi > was && std::fabs(c[1] * 180 / kPi - 9) < 1.5f);
            was = c[0] * 180 / kPi;
        }
    }
    {  // The long way: past half a turn it goes on round the way it started, never back through the short way.
        const Quat a = dir(0, 0), b = dir(-160, 0);  // 160 to the left the short way: 200 to the right
        const float way = 2 * kPi - quat_angle(a, b);
        CHECK(way > kPi);
        float az = 0;
        for (int i = 1; i <= 19; ++i) {
            const auto c = view_centre(view_from(Quat{}, aim_along(a, b, way, way * float(i) / 20, 0)));
            float now = c[0] * 180 / kPi;
            if (now < az - 90) now += 360;  // past behind you
            CHECK(now > az);
            az = now;
        }
        CHECK(quat_angle(aim_along(a, b, way, way, 0), b) < 1e-3f);
    }
    {  // Dead opposite there is no short way: the push says which way round.
        const Quat a = dir(0, 0), b = dir(180, 0);
        CHECK(view_centre(view_from(Quat{}, aim_along(a, b, kPi, rad(10), 0)))[0] > rad(5));   // right
        CHECK(view_centre(view_from(Quat{}, aim_along(a, b, kPi, rad(10), 1)))[0] < -rad(5));  // left
        CHECK(quat_angle(aim_along(a, b, kPi, kPi, 0), b) < 1e-3f && quat_angle(aim_along(a, b, kPi, kPi, 1), b) < 1e-3f);
    }
    {  // A turn of no length is already there.
        const Quat a = dir(10, 5);
        CHECK(quat_angle(aim_along(a, a, 0, 0, 0), a) < 1e-4f);
    }
    // Landing: the aim ends on the target exactly, in front of you in follow (head turned) and on the view in locked.
    for (const ViewMode mode : {ViewMode::follow, ViewMode::locked}) {
        const auto from = focus_direction(kCanvas, overview, kPlaced, 1), to = focus_direction(kCanvas, overview, kPlaced, 0);
        const Quat start = aim_rotation(from[0], from[1]), end = aim_rotation(to[0], to[1]);
        ModeController m;
        const Quat head = axis_angle(0, 0, 1, 25);
        m.set_aim(aim_rotation(from[0], from[1]));
        m.set_mode(mode, 0);
        double t = 0;
        for (; t < 3000; t += 8) m.update(head, t);  // settled on the desktop being left
        CHECK(m.settled());
        const float way = quat_angle(start, end);
        for (int i = 0; i <= 65; ++i, t += 8) { m.set_aim(aim_along(start, end, way, way * float(i) / 65, 1)); m.update(head, t); }
        for (int i = 0; i < 300; ++i, t += 8) { m.set_aim(end); m.update(head, t); }
        CHECK(off_centre(view_from(head, m.update(head, t)), 0, 1) < 0.05f);
    }
    std::cout << "edge pan aim passed\n";
}

// The overview's edge pan (overview-edge-pan, wayland.cpp overview_pan_turn) turns the room: world points rotate by -turn about
// the path's axis, so the view turns toward the target; its landing slerps to look_turn_from at 1 through mat3_to_quat.
void overview_pan_turn_tests() {
    for (const Quat q : {Quat{}, axis_angle(0, 1, 0, 30), axis_angle(0.6f, 0.8f, 0, -150), axis_angle(0, 0, 1, 179), axis_angle(1, 0, 0, 90)}) {
        const Mat3 a = quat_to_mat3(q), b = quat_to_mat3(mat3_to_quat(a));
        for (int i = 0; i < 9; ++i) CHECK(std::fabs(a[i] - b[i]) < 1e-5f);
    }
    {  // E straight ahead, P 20 degrees right: the axis E x P is -y; world rotated by -turn brings P toward the centre.
        const std::array<float, 3> e{0, 0, -1}, p{std::sin(rad(20)), 0, -std::cos(rad(20))};
        const std::array<float, 3> axis{0, -1, 0};
        const float turn = rad(20), s = std::sin(-turn / 2);
        const Mat3 m = quat_to_mat3(Quat{axis[0] * s, axis[1] * s, axis[2] * s, std::cos(-turn / 2)});
        const auto moved = apply(m, p);
        CHECK(std::fabs(moved[0] - e[0]) < 1e-5f && std::fabs(moved[2] - e[2]) < 1e-5f);  // the target is where E was
    }
    {  // The landing's end is look_turn_from at 1 exactly: the turn land_turns hands to the room.
        const auto overview = overview_layout(kSizes, 1920, 1200, 40);
        const Mat3 fin = look_turn_from(kCanvas, overview, kPlaced, {rad(-5), rad(2)}, 0, 1.f);
        const Mat3 back = quat_to_mat3(mat3_to_quat(fin));
        for (int i = 0; i < 9; ++i) CHECK(std::fabs(fin[i] - back[i]) < 1e-5f);
    }
    std::cout << "overview pan turn passed\n";
}

int main() {
    edge_pan_tests();
    overview_pan_turn_tests();
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
    // Maximized it sits level and square to the glasses (Cody, 2026-11-03): whatever roll and orbit it has in the overview,
    // wherever the head points and however the head is tipped, in locked, in follow once the spring settles, and across
    // Ctrl+Alt+V between them. The aim takes care of the orbit, zoomed_placements of the roll.
    const Quat tipped[] = {Quat{}, axis_angle(1, 0, 0, 25), quat_mul(axis_angle(0, 0, 1, -50), axis_angle(1, 0, 0, -30)),
                           quat_mul(axis_angle(0, 0, 1, 170), axis_angle(0, 1, 0, 20))};
    for (std::size_t focus = 0; focus < 3; ++focus) {
        ModeController m;
        double t = 0;
        m.set_aim(aim_for(focus, 1));
        for (const ViewMode mode : {ViewMode::locked, ViewMode::follow, ViewMode::locked}) {
            m.set_mode(mode, t);
            for (const Quat& head : tipped) {
                for (int i = 0; i < 300; ++i) m.update(head, t += 8);
                CHECK(off_square(view_from(head, m.update(head, t += 8)), focus) < 0.1f);
            }
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
    // Regression (2026-11-03): in anchor mode a switch (Ctrl+Alt+Left / Right) turned the view to the target, then phase 3
    // eased the camera turn back to 0 with the room still pinned, so the view panned back to the desktop it left. Now the
    // camera turn holds through phase 3 and the landed turn moves the anchor: the target is ahead, continuously, and stays.
    // Drawn as wayland.cpp does: view_from(head, anchor) * look_turn(look_from, look_to, turn) (anchor keeps sizes).
    for (int cancel = 0; cancel < 3; ++cancel) {  // 0: lands; 1: zoomed out mid-turn (eases back); 2: zoomed out in phase 3
        ViewAnimator v(220, 300);
        ModeController m;
        double t = 0;
        const Quat head = axis_angle(0, 0, 1, 10);
        m.update(head, t);
        v.set_target(true, 0, 40, t);
        for (; t < 400; t += 1) v.advance(t);
        auto drawn = [&](double now) {
            v.advance(now);
            if (const auto l = v.take_landed_turn()) {
                const auto a = focus_direction(kCanvas, overview, kPlaced, (*l)[0]), b = focus_direction(kCanvas, overview, kPlaced, (*l)[1]);
                m.turn_anchor(quat_mul(quat_conj(aim_rotation(a[0], a[1])), aim_rotation(b[0], b[1])));
            }
            const Mat3 hv = view_from(head, m.update(head, now));
            const auto lt = look_turn(kCanvas, overview, kPlaced, v.look_from(), v.look_to(), v.turn());
            Mat3 out{};
            for (int r = 0; r < 3; ++r)
                for (int c = 0; c < 3; ++c) out[r * 3 + c] = hv[r * 3] * lt[c] + hv[r * 3 + 1] * lt[3 + c] + hv[r * 3 + 2] * lt[6 + c];
            return out;
        };
        // Where desktop i's overview direction (slot + orbit) is drawn: a unit vector in view space.
        auto dir_of = [&](const Mat3& view, std::size_t i) {
            const auto d = focus_direction(kCanvas, overview, kPlaced, i);
            const float ca = std::cos(d[0]), sa = std::sin(d[0]), ce = std::cos(d[1]), se = std::sin(d[1]);
            return apply(view, {sa * ce, se, -ca * ce});
        };
        auto angle = [](std::array<float, 3> p, std::array<float, 3> q) {
            const float d = p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
            return std::acos(std::clamp(d, -1.f, 1.f));
        };
        const auto ahead0 = dir_of(drawn(t), 0);  // where desktop 0 is seen before the switch
        v.set_target(true, 2, 40, t);
        auto last = ahead0;
        float worst = 0;
        bool done = false;
        for (double end = t + 1500; t < end; t += 1) {
            if (cancel == 1 && !done && t > 400 + 220 + 150) { v.set_target(false, 2, 40, t); done = true; }
            if (cancel == 2 && !done && t > 400 + 220 + 300 + 100) { v.set_target(false, 2, 40, t); done = true; }
            const Mat3 view = drawn(t);
            const auto now = dir_of(view, 0);
            worst = std::max(worst, angle(now, last));
            last = now;
            // Through the zoom in (phase 3) and after it the target sits where desktop 0 was seen: no pan back.
            if (cancel == 0 && t > 400 + 220 + 300) CHECK(angle(dir_of(view, 2), ahead0) < rad(0.05f));
        }
        CHECK(worst < rad(2));  // per ms: never a jump, also when cancelled or when the turn hands over to the room
        CHECK(!v.switching() && !v.take_landed_turn());
        const Mat3 rest = drawn(t);
        if (cancel == 1) CHECK(angle(dir_of(rest, 0), ahead0) < rad(0.05f));  // cancelled mid-turn: eased back to 0
        else CHECK(angle(dir_of(rest, 2), ahead0) < rad(0.05f));  // landed (or zoomed out on it): stays on the target
        // ...and the room is world-locked there: turning the head does not bring the old desktop back.
        if (cancel != 1) {
            const Quat turned_head = m.update(axis_angle(0, 0, 1, -35), t += 8);
            CHECK(quat_angle(turned_head, m.update(head, t += 8)) < 1e-5f);
        }
    }
    // A switch back lands back where it started (the turns compose).
    {
        ViewAnimator v(220, 300);
        ModeController m;
        v.set_target(true, 1, 40, 0);
        v.advance(400);
        double t = 500;
        for (std::uint32_t to : {0u, 2u, 1u}) {
            v.set_target(true, to, 40, t);
            v.advance(t += 1000);
            const auto l = v.take_landed_turn();
            CHECK(l && (*l)[1] == to);
            const auto a = focus_direction(kCanvas, overview, kPlaced, (*l)[0]), b = focus_direction(kCanvas, overview, kPlaced, (*l)[1]);
            m.turn_anchor(quat_mul(quat_conj(aim_rotation(a[0], a[1])), aim_rotation(b[0], b[1])));
        }
        CHECK(quat_angle(m.update(Quat{}, 5000), Quat{}) < 1e-4f);
    }
    // Cursor switch (cursor-switch-no-anim, 2026-11-03): in anchor mode the cursor pushed onto another desktop pans the
    // view straight to it and centres it, then it stays; pushing back pans to and centres the other one. Always one move,
    // never back. The head looks off to the side of desktop 0 (not centred on it), so a plain switch (target where 0 was)
    // would leave the target off centre. Drawn as wayland.cpp does: the turn from the view's centre at the switch's start
    // (view_centre of the last drawn view; look_turn_from), its landed turn folded into the anchor from that direction.
    for (int cancel = 0; cancel < 2; ++cancel) {  // 1: zoomed out in phase 3 (the turn lands at once, still centred)
        ViewAnimator v(220, 300);
        ModeController m;
        double t = 0;
        const Quat head = quat_mul(axis_angle(0, 0, 1, 14), axis_angle(0, 1, 0, -6));  // yawed and pitched off desktop 0
        m.update(head, t);
        v.set_target(true, 0, 40, t);
        for (; t < 400; t += 1) v.advance(t);
        Mat3 last_drawn = view_from(head, m.update(head, t));
        bool have = false; std::uint32_t serial = 0; std::array<float, 2> centre{};
        auto centre_of = [&](std::uint32_t s) { return have && serial == s ? centre : view_centre(last_drawn); };
        auto drawn = [&](double now) {
            v.advance(now);
            for (const auto& l : v.take_landed_turns()) {
                const auto a = l.centred ? centre_of(l.serial) : focus_direction(kCanvas, overview, kPlaced, l.from);
                const auto b = focus_direction(kCanvas, overview, kPlaced, l.to);
                m.turn_anchor(quat_mul(quat_conj(aim_rotation(a[0], a[1])), aim_rotation(b[0], b[1])));
            }
            if (v.turn_centred() && !(have && serial == v.serial())) { have = true; serial = v.serial(); centre = view_centre(last_drawn); }
            const Mat3 hv = view_from(head, m.update(head, now));
            const auto lt = v.turn_centred() ? look_turn_from(kCanvas, overview, kPlaced, centre_of(v.serial()), v.look_to(), v.turn())
                                             : look_turn(kCanvas, overview, kPlaced, v.look_from(), v.look_to(), v.turn());
            Mat3 out{};
            for (int r = 0; r < 3; ++r)
                for (int c = 0; c < 3; ++c) out[r * 3 + c] = hv[r * 3] * lt[c] + hv[r * 3 + 1] * lt[3 + c] + hv[r * 3 + 2] * lt[6 + c];
            return last_drawn = out;
        };
        auto off = [&](const Mat3& view, std::size_t i) {  // degrees between the view's centre and desktop i's direction
            const auto d = focus_direction(kCanvas, overview, kPlaced, i);
            const float ca = std::cos(d[0]), sa = std::sin(d[0]), ce = std::cos(d[1]), se = std::sin(d[1]);
            const auto p = apply(view, {sa * ce, se, -ca * ce});
            return std::acos(std::clamp(-p[2], -1.f, 1.f)) * 180.f / kPi;
        };
        CHECK(off(drawn(t), 0) > 5.f);  // not looking straight at desktop 0
        for (const std::uint32_t to : {2u, 0u, 2u}) {  // there, back, there again
            const std::uint32_t from = v.selected();
            v.set_target(true, to, 40, t, true);
            Mat3 prev = drawn(t);
            float worst = 0, closest = 180;
            bool done = false;
            for (const double end = t + 1500; t < end; t += 1) {
                if (cancel && !done && t > end - 1500 + 220 + 300 + 100) { v.set_target(false, to, 40, t, true); done = true; }
                const Mat3 view = drawn(t);
                float step = 0;  // how far the view moved this ms (its centre ray)
                {
                    const auto a = view_centre(prev), b = view_centre(view);
                    step = std::hypot(std::remainder(a[0] - b[0], 2 * kPi) * std::cos(a[1]), a[1] - b[1]);
                }
                worst = std::max(worst, step);
                // One move: once the target is closest it never drifts off again (no pan back, no pan elsewhere).
                const float o = off(view, to);
                CHECK(o <= closest + 0.05f);
                closest = std::min(closest, o);
                prev = view;
            }
            CHECK(worst < rad(2));  // per ms: continuous, also when the turn hands over to the room
            CHECK(!v.switching() && v.take_landed_turns().empty());
            const Mat3 rest = drawn(t);
            CHECK(off(rest, to) < 0.05f);  // centred in view
            CHECK(off(rest, from) > 5.f);
            // ...and world-locked there: the room does not move with the head (anchor).
            const Quat away = m.update(axis_angle(0, 0, 1, -35), t += 8);
            CHECK(quat_angle(away, m.update(head, t += 8)) < 1e-5f);
            if (cancel) break;
        }
    }
    // Follow and locked (fit to the glasses, centred by the aim) never see `center`: a cursor switch drives everything they
    // read (zoom, look, focus, position: focus_aim, blended_layout, zoomed_placements) exactly as a key switch does, ms by
    // ms, also retargeted, queued and zoomed out mid-switch. The centring is anchor mode's camera turn only (wayland.cpp).
    {
        ViewAnimator key(220, 300), cursor(220, 300);
        for (auto* v : {&key, &cursor}) { v->set_target(true, 0, 40, 0); v->advance(400); }
        const struct { double at; bool zoomed; std::uint32_t to; } steps[] = {
            {400, true, 2}, {500, true, 1}, {800, true, 0}, {2500, true, 2}, {2900, false, 2}, {3600, true, 2}, {4500, true, 1}};
        std::size_t next = 0;
        for (double t = 400; t < 6000; t += 1) {
            for (; next < std::size(steps) && steps[next].at <= t; ++next) {
                key.set_target(steps[next].zoomed, steps[next].to, 40, t);
                cursor.set_target(steps[next].zoomed, steps[next].to, 40, t, true);
            }
            key.advance(t); cursor.advance(t);
            CHECK(key.zoom() == cursor.zoom() && key.look() == cursor.look() && key.focus() == cursor.focus() &&
                  key.position() == cursor.position() && key.switching() == cursor.switching());
            const Quat a = aim_for(key.focus(), focus_weight(key)), b = aim_for(cursor.focus(), focus_weight(cursor));
            CHECK(a.x == b.x && a.y == b.y && a.z == b.z && a.w == b.w);  // the follow / locked aim, bit for bit
        }
    }
    // A key switch (not centred) is unchanged: the target comes where the desktop it left was seen.
    {
        ViewAnimator v(220, 300);
        v.set_target(true, 0, 40, 0); v.advance(400);
        v.set_target(true, 2, 40, 400);
        CHECK(!v.turn_centred());
        v.advance(2000);
        const auto l = v.take_landed_turns();
        CHECK(l.size() == 1 && !l[0].centred && l[0].from == 0 && l[0].to == 2);
        v.set_target(true, 0, 40, 2000, true);
        CHECK(v.turn_centred() && v.serial() == 2);
        v.set_target(true, 1, 40, 2010);  // retargeted while zooming out by a key: no longer centred
        CHECK(!v.turn_centred());
    }
    // view_centre: the identity view looks straight ahead; a view aimed at (az, el) is centred there.
    {
        const auto c0 = view_centre(view_from(Quat{}, Quat{}));
        CHECK(std::fabs(c0[0]) < 1e-6f && std::fabs(c0[1]) < 1e-6f);
        for (float az : {-120.f, -30.f, 15.f, 80.f})
            for (float el : {-40.f, 0.f, 25.f}) {
                const auto c = view_centre(view_from(Quat{}, aim_rotation(rad(az), rad(el))));
                CHECK(std::fabs(c[0] - rad(az)) < 1e-4f && std::fabs(c[1] - rad(el)) < 1e-4f);
            }
    }
    // Maximize-follow (2026-11-03): the overview is always anchor and a maximized desktop follows you (or is locked by
    // choice). A maximize keeps the room and holds the canvas in it while the desktop zooms in, so it grows in place and
    // never swings toward the room's centre and back; then follow takes over with no jump and centres it on the head.
    // Un-maximizing goes back to the room exactly as it was left, wherever the head went meanwhile, from follow or locked.
    // Driven as wayland.cpp does: the aim from the zoom (focus_weight), land_maximize once the zoom is in, then update.
    for (const std::size_t focus : {std::size_t{0}, std::size_t{2}})
        for (const float look_off : {0.f, 9.f})          // looking right at the desktop, or 9 degrees beside it
            for (const bool via_locked : {false, true}) {  // Ctrl+Alt+V to locked while maximized
                ViewAnimator v(220, 300);
                ModeController m;
                double t = 0;
                m.turn_anchor(axis_angle(0, 0, 1, 20));  // the room, turned by earlier switches
                v.set_target(false, std::uint32_t(focus), 40, t);
                const Quat room = m.update(Quat{}, t);
                CHECK(quat_angle(room, axis_angle(0, 0, 1, 20)) < 1e-5f);
                // The head looking at the desktop in the room (head * aim = room puts it straight ahead), maybe off it.
                const Quat head = quat_mul(quat_mul(room, quat_conj(aim_for(focus, 1))), axis_angle(0, 0, 1, look_off));
                auto frame = [&](const Quat& h, double now) {
                    v.advance(now);
                    m.set_aim(aim_for(v.focus(), focus_weight(v)));
                    if (m.holding() && !(v.zoom() < 1.f)) m.land_maximize(now);
                    return m.update(h, now);
                };
                for (; t < 300; t += 1) CHECK(quat_angle(frame(head, t), room) < 1e-5f);  // the overview: world-locked
                v.set_target(true, std::uint32_t(focus), 40, t);  // maximize: the zoom in, and follow
                m.set_mode(ViewMode::follow, t, ModeController::Change::maximize);
                CHECK(m.holding() && !m.settled() && m.mode() == ViewMode::follow);
                Quat last = room;
                float worst = 0;
                for (const double end = t + 1500; t < end; t += 1) {
                    const bool held = m.holding();
                    const Quat a = frame(head, t);
                    if (held && m.holding()) CHECK(quat_angle(a, room) < 1e-5f);  // in place while it zooms in
                    if (look_off == 0.f) CHECK(quat_angle(a, room) < rad(0.05f));  // looking at it: nothing moves at all
                    worst = std::max(worst, quat_angle(a, last));
                    last = a;
                }
                CHECK(!m.holding() && v.zoom() == 1.f);
                CHECK(worst < rad(0.2f));  // per ms: follow takes over without a jump
                if (look_off == 0.f && !via_locked && focus == 0) {
                    // Without the hold (a plain blend into follow as the zoom starts) the same maximize swings the desktop
                    // toward the room's centre and back while the spring catches up: what the hold prevents.
                    ViewAnimator v2(220, 300);
                    ModeController m2;
                    m2.turn_anchor(axis_angle(0, 0, 1, 20));
                    v2.set_target(false, 0, 40, 0); m2.update(head, 0); v2.advance(300);
                    v2.set_target(true, 0, 40, 300);
                    m2.set_mode(ViewMode::follow, 300);
                    float swing = 0;
                    for (double u = 300; u < 1800; u += 1) {
                        v2.advance(u); m2.set_aim(aim_for(v2.focus(), focus_weight(v2)));
                        swing = std::max(swing, quat_angle(m2.update(head, u), room));
                    }
                    CHECK(swing > rad(3));
                }
                CHECK(off_centre(view_from(head, last), focus, 1) < 0.1f);  // then centred on the head
                if (via_locked) {
                    m.set_mode(ViewMode::locked, t);  // a manual change: blends as before
                    for (const double end = t + 400; t < end; t += 1) frame(head, t);
                    CHECK(off_centre(view_from(head, frame(head, t)), focus, 1) < 0.05f);
                }
                // The head turns away; the maximized desktop comes along, the room does not move.
                const Quat away = quat_mul(head, axis_angle(0, 0, 1, -40));
                for (const double end = t + 1500; t < end; t += 1) last = frame(away, t);
                CHECK(off_centre(view_from(away, last), focus, 1) < 0.1f);
                CHECK(quat_angle(last, room) > rad(30));  // (pinning here, Change::blend, would move every desktop)
                // Un-maximize: the zoom out, and anchor back in the room as it was, wherever the head is.
                v.set_target(false, std::uint32_t(focus), 40, t);
                m.set_mode(ViewMode::anchor, t, ModeController::Change::restore);
                worst = 0;
                for (const double end = t + 600; t < end; t += 1) {
                    const Quat a = frame(away, t);
                    worst = std::max(worst, quat_angle(a, last));
                    last = a;
                }
                CHECK(worst < rad(0.5f));  // per ms: a blend, never a jump
                CHECK(quat_angle(last, room) < 1e-5f && m.settled());  // exactly the room: every desktop where it was
                CHECK(quat_angle(frame(head, t += 8), room) < 1e-5f);    // and world-locked there again
            }
    // The follow / locked choice sticks for the session (the supervisor's maximized_mode), so a maximize can go straight
    // into locked: held in place while the desktop zooms in like any maximize, then a blend (no jump) onto the head's
    // view with the desktop dead centre; un-maximized, the room is exactly as it was.
    for (const std::size_t focus : {std::size_t{0}, std::size_t{2}}) {
        ViewAnimator v(220, 300);
        ModeController m;
        double t = 0;
        m.turn_anchor(axis_angle(0, 0, 1, -25));
        v.set_target(false, std::uint32_t(focus), 40, t);
        const Quat room = m.update(Quat{}, t);
        const Quat head = quat_mul(quat_mul(room, quat_conj(aim_for(focus, 1))), axis_angle(0, 0, 1, 7));  // 7 deg off it
        auto frame = [&](const Quat& h, double now) {
            v.advance(now);
            m.set_aim(aim_for(v.focus(), focus_weight(v)));
            if (m.holding() && !(v.zoom() < 1.f)) m.land_maximize(now);
            return m.update(h, now);
        };
        for (; t < 300; t += 1) frame(head, t);
        v.set_target(true, std::uint32_t(focus), 40, t);
        m.set_mode(ViewMode::locked, t, ModeController::Change::maximize);
        CHECK(m.holding() && m.mode() == ViewMode::locked);
        Quat last = room;
        float worst = 0;
        for (const double end = t + 800; t < end; t += 1) {
            const bool held = m.holding();
            const Quat a = frame(head, t);
            if (held && m.holding()) CHECK(quat_angle(a, room) < 1e-5f);  // in place while it zooms in
            worst = std::max(worst, quat_angle(a, last));
            last = a;
        }
        CHECK(!m.holding() && m.settled());
        CHECK(worst < rad(0.5f));  // per ms: the blend onto the head, never a jump
        CHECK(off_centre(view_from(head, last), focus, 1) < 0.05f);  // locked: dead centre
        const Quat away = quat_mul(head, axis_angle(0, 0, 1, 35));
        CHECK(off_centre(view_from(away, frame(away, t += 1)), focus, 1) < 0.05f);  // and rigidly so
        v.set_target(false, std::uint32_t(focus), 40, t);
        m.set_mode(ViewMode::anchor, t, ModeController::Change::restore);
        for (const double end = t + 600; t < end; t += 1) last = frame(away, t);
        CHECK(quat_angle(last, room) < 1e-5f);
    }
    // A recentre while maximized moves the room as any recentre does: un-maximized, the overview comes back with that
    // desktop straight ahead of the new reference. A maximize again mid-way back starts from where the canvas is.
    {
        ViewAnimator v(220, 300);
        ModeController m;
        double t = 0;
        auto frame = [&](const Quat& h, double now) {
            v.advance(now);
            m.set_aim(aim_for(v.focus(), focus_weight(v)));
            if (m.holding() && !(v.zoom() < 1.f)) m.land_maximize(now);
            return m.update(h, now);
        };
        v.set_target(true, 0, 40, t);
        m.set_mode(ViewMode::follow, t, ModeController::Change::maximize);
        for (; t < 1500; t += 1) frame(axis_angle(0, 0, 1, 50), t);
        m.recenter();  // PoseState's reference moves to the head: the relative head is the identity again
        for (const double end = t + 200; t < end; t += 1) frame(Quat{}, t);
        v.set_target(false, 0, 40, t);
        m.set_mode(ViewMode::anchor, t, ModeController::Change::restore);
        Quat last = frame(Quat{}, t);
        for (const double end = t + 120; t < end; t += 1) last = frame(Quat{}, t);  // part way back
        v.set_target(true, 0, 40, t);
        m.set_mode(ViewMode::follow, t, ModeController::Change::maximize);
        CHECK(quat_angle(frame(Quat{}, t += 1), last) < rad(0.5f));  // held where it was: no jump
        for (const double end = t + 1500; t < end; t += 1) last = frame(Quat{}, t);
        v.set_target(false, 0, 40, t);
        m.set_mode(ViewMode::anchor, t, ModeController::Change::restore);
        for (const double end = t + 600; t < end; t += 1) last = frame(axis_angle(0, 0, 1, -30), t);
        CHECK(quat_angle(last, aim_for(0, 1)) < 1e-5f);
        CHECK(off_centre(view_from(Quat{}, last), 0, 0) < 0.05f);  // the overview, desktop 0 straight ahead
    }
    std::cout << "focused desktop centred in locked and follow, anchor recentre, switches continuous, anchor switch stays, "
                 "cursor switch centres, maximize holds the room and un-maximize restores it\n";
}
