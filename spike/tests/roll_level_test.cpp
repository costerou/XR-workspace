// Roll brackets, renderer side: the levelling roll (view.hpp Placement::roll) turns a desktop about its own line of
// sight and keeps it facing you; desktop_tilt measures a desktop's roll from the world's up (pose.hpp canvas_up); the
// brackets (glass.hpp roll_arm / roll_bracket / roll_distance / on_roll_bracket) hug the top corners from outside, a
// share of the desktop in tile px, so they move, scale and roll with it; they turn the way they point, in the theme's M3
// primary; the `roll` control frame (ipc.hpp RollPayload) and its malformed-frame handling.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <string>
#include <variant>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
constexpr float kPi = 3.14159265358979f;
float rad(float deg) { return deg * kPi / 180.f; }
bool near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) <= eps; }
float dist(Vec3 a, Vec3 b) { return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)); }
Quat axis_angle(float x, float y, float z, float deg) {
    const float h = rad(deg) / 2, s = std::sin(h);
    return {x * s, y * s, z * s, std::cos(h)};
}
std::string frame(const std::string& payload) {
    return "{\"schema\":1,\"request_id\":7,\"run_id\":\"run\",\"session\":\"s\",\"op\":\"roll\",\"payload\":" + payload + "}\n";
}
}

int main() {
    const Canvas canvas{1920, 1080, 0.41421356f, 0.23300762f};
    const FRect centred{560, 315, 800, 450}, side{1400, 300, 459, 258};
    const Vec3 upright{0, 1, 0};

    // The roll turns the desktop about the line through the eye and its centre: the centre stays, every point keeps its
    // distance from the centre and from the eye, and a quarter turn counter-clockwise brings its right edge to the top.
    for (const FRect& t : {centred, side}) {
        const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
        for (const Placement base : {Placement{}, Placement{0.3f, -0.2f, 1.4f, 0.5f}}) {
            Placement rolled = base; rolled.roll = rad(37);
            const Vec3 c0 = place_point(canvas, t, base, cx, cy), c1 = place_point(canvas, t, rolled, cx, cy);
            CHECK(dist(c0, c1) < 1e-5f);
            for (const auto& [px, py] : {std::pair{t.x, t.y}, std::pair{t.x + t.width, t.y + t.height}, std::pair{cx + 40, cy - 7}}) {
                const Vec3 a = place_point(canvas, t, base, px, py), b = place_point(canvas, t, rolled, px, py);
                CHECK(near(dist(a, c0), dist(b, c0), 1e-4f));
                CHECK(near(dist(a, {}), dist(b, {}), 1e-4f));
                CHECK(dist(a, b) > 1e-3f);
            }
        }
        Placement quarter; quarter.roll = rad(90);
        const Vec3 right = place_point(canvas, t, quarter, cx + 100, cy), top = place_point(canvas, t, Placement{}, cx, cy - 100);
        // At its slot the unrolled desktop's point 100 px above the centre: the same as the rolled one's right point
        // (flat desktops; equal pixel scales in x and y on this canvas within float error).
        CHECK(dist(right, top) < 2e-3f);
    }

    // Tilt: level at every slot when the world's up is the canvas's; rolling the desktop adds its roll; the measured
    // tilt after the levelling roll is zero; straight overhead it is not measured.
    for (const FRect& t : {centred, side}) {
        const auto level = desktop_tilt(canvas, t, Placement{}, upright);
        CHECK(level && near(*level, 0, 1e-4f));
        for (const float deg : {-30.f, -3.f, 4.5f, 60.f}) {
            Placement p; p.roll = rad(deg);
            const auto tilt = desktop_tilt(canvas, t, p, upright);
            CHECK(tilt && near(*tilt, rad(deg), 2e-4f));
        }
    }
    {
        Placement overhead; overhead.y = rad(89.5f);
        CHECK(!desktop_tilt(canvas, centred, overhead, upright));
        CHECK(!desktop_tilt(canvas, centred, Placement{}, Vec3{0, 0, 0}));
        CHECK(!desktop_tilt(canvas, FRect{}, Placement{}, upright));
    }

    // The world's up in the canvas frame: identity is upright; a recentre with the head rolled 20 degrees to the right
    // (about the driver's forward axis) leaves the canvas turned clockwise by 20 as you see it, so a desktop at its slot
    // measures -20 and a +20 levelling roll levels it; a turn round the vertical changes nothing.
    {
        const auto up0 = canvas_up(Quat{}, Quat{});
        CHECK(near(up0[0], 0) && near(up0[1], 1) && near(up0[2], 0));
        const auto yawed = canvas_up(axis_angle(0, 0, 1, 70), Quat{});
        CHECK(near(yawed[0], 0) && near(yawed[1], 1) && near(yawed[2], 0));
        const auto up = canvas_up(axis_angle(1, 0, 0, 20), Quat{});
        const Vec3 u{up[0], up[1], up[2]};
        const auto tilt = desktop_tilt(canvas, centred, Placement{}, u);
        CHECK(tilt && near(*tilt, rad(-20), 2e-4f));
        Placement fixed; fixed.roll = rad(20);
        const auto after = desktop_tilt(canvas, centred, fixed, u);
        CHECK(after && near(*after, 0, 2e-4f));
        // A pitched reference (head 25 degrees down at the recentre) rolls a desktop orbited 80 degrees to the side.
        const auto pitched = canvas_up(axis_angle(0, 1, 0, 25), Quat{});
        Placement turned; turned.x = rad(80);
        const auto side_tilt = desktop_tilt(canvas, centred, turned, Vec3{pitched[0], pitched[1], pitched[2]});
        CHECK(side_tilt && std::fabs(*side_tilt) > rad(15));
        const auto ahead = desktop_tilt(canvas, centred, Placement{}, Vec3{pitched[0], pitched[1], pitched[2]});
        CHECK(ahead && near(*ahead, 0, 2e-4f));
    }

    // Both directions, checked against the world rather than desktop_tilt itself: after a recentre with the head tipped
    // left or right (and pitched), every desktop of a four-desktop overview measures a tilt of that side's sign, and its
    // levelling roll (-tilt, what the supervisor applies on a click) leaves its horizontal centre line level in the
    // world (driver z up), where before it was not.
    {
        const Canvas wide{1920, 1200, 0.41421356f, 0.25888f};
        const Size four[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}, {1920, 1080}};
        const auto tiles = overview_layout(four, 1920, 1200, 20);
        CHECK(tiles.size() == 4);
        const Mat3 gl_to_driver = transpose(Mat3{0, -1, 0, 0, 0, 1, -1, 0, 0});  // pose.cpp kDriverToGl
        for (const float head_roll : {-20.f, 20.f})
            for (const float pitch : {0.f, -20.f, 25.f}) {
                const Quat reference = quat_mul(axis_angle(0, 1, 0, pitch), axis_angle(1, 0, 0, head_roll));
                const auto up = canvas_up(reference, Quat{});
                const auto height = [&](Vec3 v) { return xr::apply(quat_to_mat3(reference), xr::apply(gl_to_driver, {v.x, v.y, v.z}))[2]; };
                for (const FRect& t : tiles) {
                    const auto tilt = desktop_tilt(wide, t, Placement{}, Vec3{up[0], up[1], up[2]});
                    CHECK(tilt && (head_roll > 0 ? *tilt < -rad(5) : *tilt > rad(5)));  // head tipped right: clockwise
                    if (!tilt) continue;
                    const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
                    Placement levelled; levelled.roll = -*tilt;
                    const float before = height(place_point(wide, t, Placement{}, cx + 100, cy)) - height(place_point(wide, t, Placement{}, cx - 100, cy));
                    const float after = height(place_point(wide, t, levelled, cx + 100, cy)) - height(place_point(wide, t, levelled, cx - 100, cy));
                    CHECK(std::fabs(before) > 5e-3f && std::fabs(after) < 1e-5f);
                }
            }
    }

    // Easing carries the roll and lands on it; same_placement sees it; zooming and fading keep it.
    {
        Placement from, to; to.roll = rad(12);
        CHECK(!same_placement(from, to));
        Placement p = from;
        for (int i = 0; i < 200; ++i) p = ease_toward(p, to, 8.3f, kPlacementEaseMs);
        CHECK(same_placement(p, to));
        CHECK(faded(to, 1.f).roll == to.roll);
        const Size sizes[] = {{1920, 1080}, {1920, 1080}};
        const auto overview = overview_layout(sizes, 1920, 1080, 40);
        const auto zoomed = blended_layout(sizes, 1920, 1080, 40, 1.f, 0.f);
        const Placement ps[] = {to, Placement{}};
        const auto z = zoomed_placements(canvas, overview, zoomed, ps, 1.f, 0.f);
        CHECK(z.size() == 2 && z[0].roll == to.roll && z[1].roll == 0);
    }

    // The bracket's size: arms kRollArmShare of the desktop's shorter side, at least kRollArmMin, never past
    // kRollArmMaxShare of it (the two brackets never meet). The stroke, gap and head keep their size down to kRollArmMin
    // arms and shrink with the arms below that. All tile px: nothing depends on the desktop's distance.
    {
        const FRect usual{40, 435, 586.67f, 330}, small{10, 40, 120, 60}, tiny{5, 5, 40, 30};
        CHECK(near(roll_arm(usual), kRollArmShare * 330, 1e-3f) && roll_scale(usual) == 1.f);
        CHECK(near(roll_arm(FRect{0, 0, 4000, 2000}), kRollArmShare * 2000, 1e-2f));
        CHECK(near(roll_arm(FRect{0, 0, 300, 160}), kRollArmMin) && roll_scale(FRect{0, 0, 300, 160}) == 1.f);  // 35.2 -> 36
        CHECK(near(roll_arm(small), kRollArmMaxShare * 60) && near(roll_scale(small), kRollArmMaxShare * 60 / kRollArmMin));
        CHECK(near(roll_arm(tiny), kRollArmMaxShare * 30) && roll_scale(tiny) < 1.f);
        CHECK(roll_arm(FRect{}) == 0 && roll_scale(FRect{}) == 0 && roll_bracket(FRect{}, 0).band.empty());
        CHECK(roll_bracket(usual, 2).band.empty() && roll_bracket(usual, 2).head.empty());
        CHECK(std::isinf(roll_distance(FRect{}, 0, 0, 0)) && std::isinf(roll_distance(usual, 2, 0, 0)) && !on_roll_bracket(usual, 2, 30, 430));
    }

    // Drawn: one stroke hugging its top corner from outside, over the top edge, round the corner and down the side to its
    // arrowhead. Every point is outside the desktop and the kRollEdgeClear band along its edges (the selected desktop's
    // 6 px border, the edge's resize zone), and inside the hit region; it spans from `arm` in along the top (the round cap) to `arm` down the side (the tip), kRollGap out; side 0
    // is side 1's mirror image; side 1 turns clockwise as you see it, side 0 counter-clockwise; the head points down the
    // side, the way that corner goes.
    for (const FRect& t : {FRect{40, 435, 586.67f, 330}, FRect{0, 0, 4000, 2000}, FRect{10, 40, 120, 60}, FRect{1400, 300, 459, 258},
                           FRect{-200.5f, 410.25f, 812, 457}}) {
        const float arm = roll_arm(t), s = roll_scale(t), gap = kRollGap * s, half = kRollStroke * s / 2, head = kRollHeadHalf * s;
        CHECK(2 * arm < t.width && arm < t.height);
        const float mid = t.x + t.width / 2, cy = t.y + t.height / 2;
        const auto l = roll_bracket(t, 0), r = roll_bracket(t, 1);
        for (std::uint32_t side = 0; side < 2; ++side) {
            const auto& b = side ? r : l;
            CHECK(b.band.size() >= 8 && b.band.size() % 2 == 0 && b.band.size() <= 40);
            CHECK(b.head.size() >= 3 && b.head.size() % 3 == 0);
            const float corner = side ? t.x + t.width : t.x, out = side ? 1.f : -1.f;
            float x0 = 1e9f, x1 = -1e9f, y0 = 1e9f, y1 = -1e9f;
            for (const auto* v : {&b.band, &b.head})
                for (const auto& p : *v) {
                    const float clear = std::max({t.x - p[0], p[0] - (t.x + t.width), t.y - p[1]});
                    CHECK(clear >= kRollEdgeClear * s - 1e-3f);
                    CHECK(on_roll_bracket(t, side, p[0], p[1]) || clear < kRollEdgeClear * s + 1e-3f);  // the head's inner corner: on the band's edge
                    CHECK(roll_distance(t, side, p[0], p[1]) <= std::max(head, half) + 1e-3f);
                    x0 = std::min(x0, p[0]); x1 = std::max(x1, p[0]); y0 = std::min(y0, p[1]); y1 = std::max(y1, p[1]);
                }
            const float inner = corner - out * (arm + half), outer = corner + out * (gap + head);
            CHECK(near(std::min(inner, outer), x0, 1e-2f) && near(std::max(inner, outer), x1, 1e-2f));
            CHECK(near(y0, t.y - gap - half, 1e-2f) && near(y1, t.y + arm, 1e-2f));
            // The head: its tip at the side arm's end, `arm` below the corner and kRollGap out; its base across the stroke.
            const auto& tip = b.head[2];
            CHECK(near(tip[0], corner + out * gap, 1e-3f) && near(tip[1], t.y + arm, 1e-3f));
            CHECK(b.head[0][1] < tip[1] && near(b.head[0][1], b.head[1][1]) && near((b.head[0][0] + b.head[1][0]) / 2, tip[0], 1e-3f));
            // The way it turns: from the top arm's end to the tip, round the desktop's centre (y up), clockwise for side 1.
            const float tail_x = (b.band[0][0] + b.band[1][0]) / 2, tail_y = (b.band[0][1] + b.band[1][1]) / 2;
            CHECK(near(tail_x, corner - out * arm, 1e-3f) && near(tail_y, t.y - gap, 1e-3f));
            const float sweep = std::remainder(std::atan2(cy - tip[1], tip[0] - mid) - std::atan2(cy - tail_y, tail_x - mid), 2 * kPi);
            CHECK(side ? sweep < 0 : sweep > 0);
            // The centre line: kRollGap round the corner, on the arms, padded by kRollPad past the head.
            CHECK(near(roll_distance(t, side, corner, t.y), gap, 1e-3f));
            CHECK(near(roll_distance(t, side, corner - out * arm / 2, t.y - gap), 0, 1e-3f));
            CHECK(near(roll_distance(t, side, corner + out * gap, t.y + arm / 2), 0, 1e-3f));
            const float reach = (kRollHeadHalf + kRollPad) * s;
            CHECK(on_roll_bracket(t, side, corner + out * (gap + reach - 0.25f * s), t.y + arm / 2));
            CHECK(!on_roll_bracket(t, side, corner + out * (gap + reach + 0.25f * s), t.y + arm / 2));
            CHECK(on_roll_bracket(t, side, corner + out * gap, t.y + arm + reach - 0.25f * s));   // past the tip
            CHECK(!on_roll_bracket(t, side, corner + out * gap, t.y + arm + reach + 0.25f * s));
            CHECK(on_roll_bracket(t, side, corner - out * (arm + reach - 0.25f * s), t.y - gap));  // past the cap
            CHECK(!on_roll_bracket(t, side, corner - out * (arm + reach + 0.25f * s), t.y - gap));
            // Never on the desktop itself, however close to the line: its own pixels stay with the desktop. Nor in the band
            // kRollEdgeClear wide just outside its edges (the edge's resize zone): only from there out.
            CHECK(!on_roll_bracket(t, side, corner - out * 1.f, t.y + arm / 2) && !on_roll_bracket(t, side, corner - out * arm / 2, t.y + 1.f));
            for (const float in_band : {0.5f, 3.f, kRollEdgeClear - 0.25f}) {
                CHECK(!on_roll_bracket(t, side, corner + out * in_band * s, t.y + arm / 2));    // beside the side arm
                CHECK(!on_roll_bracket(t, side, corner - out * arm / 2, t.y - in_band * s));    // under the top arm
            }
            CHECK(on_roll_bracket(t, side, corner + out * (kRollEdgeClear + 0.25f) * s, t.y + arm / 2));
            CHECK(on_roll_bracket(t, side, corner - out * arm / 2, t.y - (kRollEdgeClear + 0.25f) * s));
            CHECK(!on_roll_bracket(t, side, mid, t.y - gap));  // nothing in the middle of the top edge
        }
        for (std::size_t i = 0; i < l.band.size() && i < r.band.size(); ++i)
            CHECK(near(l.band[i][0] - mid, mid - r.band[i][0], 1e-2f) && near(l.band[i][1], r.band[i][1], 1e-3f));
        for (std::size_t i = 0; i < l.head.size() && i < r.head.size(); ++i)
            CHECK(near(l.head[i][0] - mid, mid - r.head[i][0], 1e-2f) && near(l.head[i][1], r.head[i][1], 1e-3f));
        CHECK(l.band.size() == r.band.size() && l.head.size() == r.head.size());
    }

    // It moves and scales with its desktop: on the desktop's own surface, a resize (distance), a move (orbit), curvature
    // and the levelling roll all carry it along, so as you see it the bracket's tip keeps its place at the corner and the
    // same share of the desktop's width (the angle from the corner to the tip over the angle across the top edge).
    {
        auto angle = [](Vec3 a, Vec3 b) {
            const float dot = a.x * b.x + a.y * b.y + a.z * b.z, n = std::sqrt((a.x * a.x + a.y * a.y + a.z * a.z) * (b.x * b.x + b.y * b.y + b.z * b.z));
            return std::acos(std::clamp(dot / n, -1.f, 1.f));
        };
        for (const FRect& t : {centred, side}) {
            for (std::uint32_t s = 0; s < 2; ++s) {
                const auto b = roll_bracket(t, s);
                const auto& tip = b.head[2];
                const float corner = s ? t.x + t.width : t.x;
                const float want = std::hypot(tip[0] - corner, tip[1] - t.y) / t.width;
                for (const float d : {0.5f, 1.f, 2.f})
                    for (Placement p : {Placement{}, Placement{0.25f, -0.15f, 1, 0, 0}, Placement{0, 0, 1, 0.6f, 0}, Placement{-0.4f, 0.2f, 1, 0.3f, rad(14)}}) {
                        p.distance = d;
                        const Vec3 c = place_point(canvas, t, p, corner, t.y), q = place_point(canvas, t, p, tip[0], tip[1]);
                        const Vec3 a = place_point(canvas, t, p, t.x, t.y), z = place_point(canvas, t, p, t.x + t.width, t.y);
                        const float got = angle(c, q) / angle(a, z);
                        CHECK(std::fabs(got - want) <= 0.03f * want);
                        if (std::fabs(got - want) > 0.03f * want) std::fprintf(stderr, "bracket share %g want %g (d %g x %g)\n", double(got), double(want), double(d), double(p.x));
                        // ...and it turns with a rolled desktop: the tip stays on the desktop's own side of its corner.
                        const Vec3 centre = place_point(canvas, t, p, t.x + t.width / 2, t.y + t.height / 2);
                        CHECK(angle(centre, q) > angle(centre, c) * 0.9f);
                    }
            }
        }
    }

    // Colour: an M3 icon in the theme's primary (the scheme of the accent, so it follows a live theme change), lifted
    // along the primary palette toward its tone 98 when hovered, all the way when dragged; under the OLED cap throughout.
    for (const auto accent : {std::array<float, 3>{1, 1, 1}, std::array<float, 3>{0.48f, 0.64f, 0.97f}, kGlassThemeDefault,
                              std::array<float, 3>{0, 0, 0}, std::array<float, 3>{0.9f, 0.2f, 0.1f}}) {
        const auto& m = glass_theme_scheme(accent);
        const auto idle = roll_arrow_color(accent, kGlassIdle), hover = roll_arrow_color(accent, kGlassHover),
                   hot = roll_arrow_color(accent, kGlassActive);
        CHECK(idle == m.primary && hot == m.primary_bright && roll_arrow_color(accent, 9.f) == hot);
        CHECK(glass_luma(idle) < glass_luma(hover) && glass_luma(hover) < glass_luma(hot));
        for (int c = 0; c < 3; ++c) CHECK(idle[c] <= kGlassCap + 1e-6f && hover[c] <= kGlassCap + 1e-6f && hot[c] <= kGlassCap + 1e-6f);
    }
    {
        const auto a = roll_arrow_color({0.9f, 0.2f, 0.1f}, kGlassIdle), b = roll_arrow_color({0.1f, 0.5f, 0.9f}, kGlassIdle);
        CHECK(!near(a[0], b[0], 0.1f) && !near(a[2], b[2], 0.1f));  // another theme, another arrow
    }

    // The `roll` frame: kMaxDesktops (four) rolls and icons ("both" on every desktop in the overview), a hover with its
    // side and active; anything else is dropped without stopping.
    {
        const auto good = decode_control(frame("{\"roll\":[-300,0,9000,-9000],\"icons\":[\"cw\",\"none\",\"ccw\",\"both\"],\"hover\":0,\"side\":\"cw\",\"active\":true}"));
        CHECK(std::holds_alternative<ControlMessage>(good));
        if (const auto* m = std::get_if<ControlMessage>(&good)) {
            CHECK(m->op == ControlOp::roll);
            const auto& r = std::get<RollPayload>(m->payload);
            CHECK(r.roll[0] == -300 && r.roll[1] == 0 && r.roll[2] == 9000 && r.roll[3] == -9000);
            CHECK(r.icons[0] == 1 && r.icons[1] == -1 && r.icons[2] == 0 && r.icons[3] == 2 && r.hover == 0 && r.side == 1 && r.active);
        }
        const auto none = decode_control(frame("{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}"));
        CHECK(std::holds_alternative<ControlMessage>(none));
        const auto left = decode_control(frame("{\"roll\":[0,0,0,0],\"icons\":[\"both\",\"both\",\"both\",\"both\"],\"hover\":3,\"side\":\"ccw\",\"active\":false}"));
        if (const auto* m = std::get_if<ControlMessage>(&left)) {
            const auto& r = std::get<RollPayload>(m->payload);
            CHECK(r.icons == (std::array<std::int8_t, kMaxDesktops>{2, 2, 2, 2}) && r.hover == 3 && r.side == 0);
        } else CHECK(false);
        for (const char* bad : {
                 "{\"roll\":[0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,9001],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,18446744073709551615],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0.5,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"left\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":4,\"side\":\"cw\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-2,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":0}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\"}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",  // no side (the old 4-key frame)
                 "{\"roll\":[0,0,0,0],\"icons\":[\"both\",\"both\",\"both\",\"both\"],\"hover\":0,\"side\":\"none\",\"active\":false}",  // hover without a side
                 "{\"roll\":[0,0,0,0],\"icons\":[\"both\",\"both\",\"both\",\"both\"],\"hover\":-1,\"side\":\"cw\",\"active\":false}",  // a side without a hover
                 "{\"roll\":[0,0,0,0],\"icons\":[\"both\",\"both\",\"both\",\"both\"],\"hover\":0,\"side\":\"up\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"both\",\"both\",\"both\",\"both\"],\"hover\":0,\"side\":1,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false,\"x\":1}"}) {
            const auto r = decode_control(frame(bad));
            const auto* e = std::get_if<Error>(&r);
            CHECK(e && e->code == kInvalidRollCode && ignorable_control_error(*e));
        }
    }

    // Which sides a desktop's icon entry shows: both arrows for "both" (every desktop in the overview), one for a side.
    CHECK(!roll_icon_shows(-1, 0) && !roll_icon_shows(-1, 1));
    CHECK(roll_icon_shows(0, 0) && !roll_icon_shows(0, 1) && !roll_icon_shows(1, 0) && roll_icon_shows(1, 1));
    CHECK(roll_icon_shows(2, 0) && roll_icon_shows(2, 1));

    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("roll_level: ok");
    return 0;
}
