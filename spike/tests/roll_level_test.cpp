// Roll-level icons, renderer side: the levelling roll (view.hpp Placement::roll) turns a desktop about its own line of
// sight and keeps it facing you; desktop_tilt measures a desktop's roll from the world's up (pose.hpp canvas_up); the
// icon (glass.hpp roll_icon / roll_arrow) is a lone arrow, a fixed size as you see it, above the higher top corner, in
// the theme's M3 primary; the `roll` control frame (ipc.hpp RollPayload) and its malformed-frame handling.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/pose.hpp"
#include "xr/view.hpp"
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

    // The icon: kRollIconPx square, kRollIconGap above the top edge, flush with the left (side 0) or right (side 1)
    // corner; the same size for a small and a big desktop; scaled by distance (so it looks the same size at any distance).
    for (const FRect& t : {FRect{100, 300, 459, 258}, FRect{0, 0, 4000, 2000}, FRect{10, 40, 120, 60}}) {
        const FRect l = roll_icon(t, 0, 1.f), r = roll_icon(t, 1, 1.f);
        CHECK(near(l.width, kRollIconPx) && near(l.height, kRollIconPx) && near(r.width, kRollIconPx));
        CHECK(near(l.x, t.x) && near(r.x + r.width, t.x + t.width));
        CHECK(near(l.y + l.height, t.y - kRollIconGap) && near(r.y, l.y));
        const FRect far = roll_icon(t, 1, 2.f);
        CHECK(near(far.width, 2 * kRollIconPx) && near(far.y + far.height, t.y - 2 * kRollIconGap) && near(far.x + far.width, t.x + t.width));
    }
    CHECK(roll_icon(FRect{}, 0, 1.f).width == 0 && roll_icon(FRect{0, 0, 10, 10}, 2, 1.f).width == 0);

    // Just the arrow, no button: it fills most of its fixed-size square (the hit box), at any distance, in surface px
    // (the square's own), so it stays kRollIconPx across as you see it and sits where the supervisor hit-tests.
    for (const float d : {0.5f, 1.f, 1.7f, 3.f})
        for (std::uint32_t side = 0; side < 2; ++side) {
            const FRect icon = roll_icon(FRect{300, 400, 430, 242}, side, d);
            const auto a = roll_arrow(icon, side);
            float x0 = 1e9f, x1 = -1e9f, y0 = 1e9f, y1 = -1e9f;
            auto grow = [&](const std::array<float, 2>& p) { x0 = std::min(x0, p[0]); x1 = std::max(x1, p[0]); y0 = std::min(y0, p[1]); y1 = std::max(y1, p[1]); };
            for (const auto& p : a.band) grow(p);
            for (const auto& p : a.head) grow(p);
            CHECK(x0 >= icon.x - 1e-3f && x1 <= icon.x + icon.width + 1e-3f && y0 >= icon.y - 1e-3f && y1 <= icon.y + icon.height + 1e-3f);
            CHECK(x1 - x0 >= 0.7f * icon.width && y1 - y0 >= 0.5f * icon.height);
        }

    // The arrow: one plain curved arrow inside its icon, the two sides mirror images of each other; side 1 turns
    // clockwise as you see it (its band runs clockwise, about half a turn, and its head points on that way), side 0
    // counter-clockwise.
    {
        const FRect icon{200, 100, 22, 22};
        const float cx = icon.x + 11, cy = icon.y + 11;
        for (std::uint32_t s = 0; s < 2; ++s) {
            const auto a = roll_arrow(icon, s);
            CHECK(a.band.size() >= 8 && a.band.size() % 2 == 0);
            for (const auto& p : a.band) CHECK(std::hypot(p[0] - cx, p[1] - cy) <= 11.f);
            for (const auto& p : a.head) CHECK(std::hypot(p[0] - cx, p[1] - cy) <= 11.f);
            // Signed sweep (y up) along the band's outer edge: negative = clockwise.
            float sweep = 0;
            for (std::size_t i = 2; i < a.band.size(); i += 2) {
                const float a0 = std::atan2(cy - a.band[i - 2][1], a.band[i - 2][0] - cx), a1 = std::atan2(cy - a.band[i][1], a.band[i][0] - cx);
                sweep += std::remainder(a1 - a0, 2 * kPi);
            }
            CHECK(s == 1 ? sweep < -rad(150) : sweep > rad(150));
            CHECK(std::fabs(sweep) < rad(240));  // a simple half-turn arc, not a near-full circle
            CHECK(a.band.size() <= 40);           // minimal detail
            // The tip lies further along that way than the head's base.
            const auto& tip = a.head[2];
            const float base_x = (a.head[0][0] + a.head[1][0]) / 2, base_y = (a.head[0][1] + a.head[1][1]) / 2;
            const float turn = std::remainder(std::atan2(cy - tip[1], tip[0] - cx) - std::atan2(cy - base_y, base_x - cx), 2 * kPi);
            CHECK(s == 1 ? turn < 0 : turn > 0);
        }
        const auto l = roll_arrow(icon, 0), r = roll_arrow(icon, 1);
        for (std::size_t i = 0; i < l.band.size(); ++i) CHECK(near(l.band[i][0] - cx, cx - r.band[i][0], 1e-3f) && near(l.band[i][1], r.band[i][1], 1e-3f));
        CHECK(roll_arrow(FRect{}, 0).band.empty());
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
