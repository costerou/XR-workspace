// Roll-level icons, renderer side: the levelling roll (view.hpp Placement::roll) turns a desktop about its own line of
// sight and keeps it facing you; desktop_tilt measures a desktop's roll from the world's up (pose.hpp canvas_up); the
// icon (glass.hpp roll_icon / roll_arrow) is a fixed size as you see it, above the right corner, in the theme's colours
// under the OLED cap; the `roll` control frame (ipc.hpp RollPayload) and its malformed-frame handling.
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

    // The arrow: inside its icon, the two sides mirror images of each other; side 1 turns clockwise as you see it (its
    // band runs clockwise round the centre and its head points on that way), side 0 counter-clockwise.
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
            CHECK(s == 1 ? sweep < -rad(200) : sweep > rad(200));
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

    // Colour: the theme's highlight hue, brighter when hovered or dragged, never over the cap.
    for (const auto accent : {std::array<float, 3>{1, 1, 1}, std::array<float, 3>{0.48f, 0.64f, 0.97f}, kGlassThemeDefault,
                              std::array<float, 3>{0, 0, 0}, std::array<float, 3>{0.9f, 0.2f, 0.1f}}) {
        const auto idle = roll_arrow_color(accent, kGlassIdle), hot = roll_arrow_color(accent, kGlassActive);
        for (int c = 0; c < 3; ++c) CHECK(idle[c] <= kGlassCap && hot[c] <= kGlassCap && idle[c] <= hot[c] + 1e-6f);
        const auto h = glass_theme_highlight(accent);
        for (int c = 0; c < 3; ++c) CHECK(near(hot[c], std::min(h[c], kGlassCap), 1e-5f));
    }

    // The `roll` frame: kMaxDesktops (four) rolls and icons, a hover and active; anything else is dropped without stopping.
    {
        const auto good = decode_control(frame("{\"roll\":[-300,0,9000,-9000],\"icons\":[\"cw\",\"none\",\"ccw\",\"cw\"],\"hover\":0,\"active\":true}"));
        CHECK(std::holds_alternative<ControlMessage>(good));
        if (const auto* m = std::get_if<ControlMessage>(&good)) {
            CHECK(m->op == ControlOp::roll);
            const auto& r = std::get<RollPayload>(m->payload);
            CHECK(r.roll[0] == -300 && r.roll[1] == 0 && r.roll[2] == 9000 && r.roll[3] == -9000);
            CHECK(r.icons[0] == 1 && r.icons[1] == -1 && r.icons[2] == 0 && r.icons[3] == 1 && r.hover == 0 && r.active);
        }
        const auto none = decode_control(frame("{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}"));
        CHECK(std::holds_alternative<ControlMessage>(none));
        for (const char* bad : {
                 "{\"roll\":[0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",
                 "{\"roll\":[0,0,0,9001],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",
                 "{\"roll\":[0,0,0,18446744073709551615],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",
                 "{\"roll\":[0,0.5,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"left\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":4,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-2,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":0}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false,\"x\":1}"}) {
            const auto r = decode_control(frame(bad));
            const auto* e = std::get_if<Error>(&r);
            CHECK(e && e->code == kInvalidRollCode && ignorable_control_error(*e));
        }
    }

    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("roll_level: ok");
    return 0;
}
