// Level bar, renderer side: the levelling roll (view.hpp Placement::roll) turns a desktop about its own line of sight and
// keeps it facing you; desktop_tilt measures a desktop's roll from the world's up (pose.hpp canvas_up); the level bar
// (glass.hpp level_bar_scale / level_bar_rect / level_bar_segment) is a segmented capsule above the top edge and the corner
// caps (corner_cap_rect) wrap the corners, a share of the desktop in tile px, so they move, scale and roll with it; their
// pixels (render_handle_atlas) are in the theme's M3 roles and only what is hot lights; the `roll` control frame (ipc.hpp
// RollPayload) and its malformed-frame handling.
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

    // The bar's size: kLevelBarShare of the desktop's width, at most kLevelBarW, at least kLevelBarMinW (never wider than the
    // desktop), the whole design scaling with it. All tile px: nothing depends on the desktop's distance.
    {
        const FRect usual{40, 435, 586.67f, 330}, wide{0, 0, 4000, 2000}, four{20, 450, 440, 247.5f}, small{10, 40, 120, 60}, tiny{5, 5, 40, 30};
        CHECK(near(level_bar_scale(wide), 1.f) && near(level_bar_scale(FRect{0, 0, 592, 333}), 1.f));
        CHECK(near(level_bar_scale(usual), kLevelBarShare * 586.67f / kLevelBarW, 1e-3f));
        CHECK(near(level_bar_scale(four), kLevelBarMinW / kLevelBarW));  // 110 px asked: the floor
        CHECK(near(level_bar_scale(small), 120.f / kLevelBarW) && near(level_bar_scale(tiny), 40.f / kLevelBarW));
        CHECK(level_bar_scale(FRect{}) == 0 && level_bar_rect(FRect{}).width == 0 && level_bar_segment(FRect{}, 0, 0) == LevelSegment::none);
        CHECK(level_bar_scale(FRect{0, 0, 100, 0}) == 0 && level_bar_rect(FRect{0, 0, 100, 0}).width == 0);
        CHECK(corner_cap_rect(FRect{}, 0).width == 0 && corner_cap_rect(usual, 4).width == 0);
    }

    // Drawn centred above the top edge, kLevelBarGap off it, the same shape at every size, never wider than the desktop and
    // never on it. Hit: the drawn bar padded by kLevelBarPad, split at its thirds: left turns counter-clockwise, the middle
    // levels, right turns clockwise; the padding goes with the segment it borders; never in the band kLevelEdgeClear wide
    // along the edge (the selected desktop's border, the edge's resize zone), let alone on the desktop.
    for (const FRect& t : {FRect{40, 435, 586.67f, 330}, FRect{0, 0, 4000, 2000}, FRect{10, 40, 120, 60}, FRect{1400, 300, 459, 258},
                           FRect{-200.5f, 410.25f, 812, 457}, FRect{5, 5, 40, 30}}) {
        const float s = level_bar_scale(t), pad = kLevelBarPad * s;
        const FRect r = level_bar_rect(t);
        CHECK(near(r.width, kLevelBarW * s, 1e-3f) && near(r.height, kLevelBarH * s, 1e-3f));
        CHECK(near(r.x + r.width / 2, t.x + t.width / 2, 1e-2f) && near(r.y + r.height, t.y - kLevelBarGap * s, 1e-3f));
        CHECK(r.width <= t.width + 1e-3f && r.y + r.height < t.y);
        const float third = r.width / 3, mid_y = r.y + r.height / 2, eps = 0.05f * s;
        CHECK(level_bar_segment(t, r.x + third / 2, mid_y) == LevelSegment::ccw);
        CHECK(level_bar_segment(t, r.x + third + eps, mid_y) == LevelSegment::level && level_bar_segment(t, r.x + third - eps, mid_y) == LevelSegment::ccw);
        CHECK(level_bar_segment(t, r.x + r.width / 2, mid_y) == LevelSegment::level);
        CHECK(level_bar_segment(t, r.x + 2 * third - eps, mid_y) == LevelSegment::level && level_bar_segment(t, r.x + 2 * third + eps, mid_y) == LevelSegment::cw);
        CHECK(level_bar_segment(t, r.x + r.width - third / 2, mid_y) == LevelSegment::cw);
        CHECK(level_bar_segment(t, r.x - pad + eps, mid_y) == LevelSegment::ccw && level_bar_segment(t, r.x - pad - eps, mid_y) == LevelSegment::none);
        CHECK(level_bar_segment(t, r.x + r.width + pad - eps, mid_y) == LevelSegment::cw && level_bar_segment(t, r.x + r.width + pad + eps, mid_y) == LevelSegment::none);
        CHECK(level_bar_segment(t, r.x + r.width / 2, r.y - pad + eps) == LevelSegment::level && level_bar_segment(t, r.x + r.width / 2, r.y - pad - eps) == LevelSegment::none);
        const float bottom = r.y + r.height + pad;
        CHECK(level_bar_segment(t, r.x + r.width / 2, bottom - eps) == LevelSegment::level && level_bar_segment(t, r.x + r.width / 2, bottom + eps) == LevelSegment::none);
        CHECK(bottom <= t.y - kLevelEdgeClear * s + 1e-3f);                                      // the band along the edge stays the edge's
        for (const float x : {r.x, r.x + r.width / 2, r.x + r.width}) CHECK(level_bar_segment(t, x, t.y + 1) == LevelSegment::none);
        // The caps: a box round each corner holding every state, the cap past the corner on both axes.
        for (std::uint32_t c = 0; c < 4; ++c) {
            const bool right = c == 1 || c == 3, bottom_corner = c >= 2;
            const FRect box = corner_cap_rect(t, c);
            const float cx = right ? t.x + t.width : t.x, cy = bottom_corner ? t.y + t.height : t.y;
            CHECK(near(box.width, (kCapOuter + kCapInner) * s, 1e-3f) && near(box.height, box.width, 1e-3f));
            CHECK(near(right ? box.x + box.width - cx : cx - box.x, kCapOuter * s, 1e-2f) && near(bottom_corner ? box.y + box.height - cy : cy - box.y, kCapOuter * s, 1e-2f));
            CHECK(near(right ? cx - box.x : box.x + box.width - cx, kCapInner * s, 1e-2f) && near(bottom_corner ? cy - box.y : box.y + box.height - cy, kCapInner * s, 1e-2f));
        }
    }

    // It moves and scales with its desktop: on the desktop's own surface, a resize (distance), a move (orbit), curvature and
    // the levelling roll all carry the bar and caps along, so on that surface they keep their share of the desktop's width
    // (measured in space: how big a span looks falls off toward the view's edge) and the bar its place above the top edge's
    // middle, turned with it.
    {
        auto angle = [](Vec3 a, Vec3 b) {
            const float dot = a.x * b.x + a.y * b.y + a.z * b.z, n = std::sqrt((a.x * a.x + a.y * a.y + a.z * a.z) * (b.x * b.x + b.y * b.y + b.z * b.z));
            return std::acos(std::clamp(dot / n, -1.f, 1.f));
        };
        for (const FRect& t : {centred, side}) {
            const FRect r = level_bar_rect(t), cap = corner_cap_rect(t, 3);
            const float want = r.width / t.width, want_cap = cap.width / t.width, mid = t.x + t.width / 2;
            for (const float d : {0.5f, 1.f, 2.f})
                for (Placement p : {Placement{}, Placement{0.25f, -0.15f, 1, 0, 0}, Placement{0, 0, 1, 0.6f, 0}, Placement{-0.4f, 0.2f, 1, 0.3f, rad(14)}}) {
                    p.distance = d;
                    const float across = dist(place_point(canvas, t, p, t.x, t.y), place_point(canvas, t, p, t.x + t.width, t.y));
                    const float got = dist(place_point(canvas, t, p, r.x, r.y), place_point(canvas, t, p, r.x + r.width, r.y)) / across;
                    const float got_cap = dist(place_point(canvas, t, p, cap.x, cap.y), place_point(canvas, t, p, cap.x + cap.width, cap.y)) / across;
                    CHECK(std::fabs(got - want) <= 0.05f * want);
                    CHECK(std::fabs(got_cap - want_cap) <= 0.05f * want_cap);
                    if (std::fabs(got - want) > 0.05f * want) std::fprintf(stderr, "bar share %g want %g (d %g x %g)\n", double(got), double(want), double(d), double(p.x));
                    // ...and it stays beyond the top edge from the desktop's centre, however it is turned.
                    const Vec3 centre = place_point(canvas, t, p, mid, t.y + t.height / 2), top = place_point(canvas, t, p, mid, t.y);
                    CHECK(angle(centre, place_point(canvas, t, p, mid, r.y + r.height / 2)) > angle(centre, top));
                }
        }
    }

    // Its pixels: one image per theme colour. Bilinear filtering stays in a cell (a gutter of clear pixels round each), every
    // pixel is premultiplied and under the OLED cap, the same colour gives the same image and another colour another.
    for (const auto accent : {std::array<float, 3>{1, 1, 1}, std::array<float, 3>{0.48f, 0.64f, 0.97f}, kGlassThemeDefault,
                              std::array<float, 3>{0, 0, 0}, std::array<float, 3>{0.9f, 0.2f, 0.1f}}) {
        const auto& m = glass_theme_scheme(accent);
        const HudImage atlas = render_handle_atlas(accent);
        CHECK(atlas.rgba == render_handle_atlas(accent).rgba);
        CHECK(atlas.width > 0 && atlas.height > 0 && atlas.rgba.size() == std::size_t(atlas.width) * atlas.height * 4);
        for (std::size_t i = 0; i < atlas.rgba.size(); i += 4) {
            const auto* p = &atlas.rgba[i];
            CHECK(p[0] <= p[3] && p[1] <= p[3] && p[2] <= p[3]);
            CHECK(std::max({p[0], p[1], p[2]}) <= 192);  // 191/255, and a rounding where two anti-aliased edges meet
        }
        struct Cell { std::uint32_t x, y, w, h; };
        const auto cell_of = [&](const AtlasBox& b) {
            return Cell{std::uint32_t(std::lround(b.u0 * float(atlas.width))), std::uint32_t(std::lround(b.v0 * float(atlas.height))),
                        std::uint32_t(std::lround((b.u1 - b.u0) * float(atlas.width))), std::uint32_t(std::lround((b.v1 - b.v0) * float(atlas.height)))};
        };
        const auto at = [&](const Cell& c, std::uint32_t x, std::uint32_t y) { return &atlas.rgba[(std::size_t(c.y + y) * atlas.width + c.x + x) * 4]; };
        const auto gutter_clear = [&](const Cell& c) {
            for (std::uint32_t x = 0; x < c.w; ++x) {
                CHECK(atlas.rgba[(std::size_t(c.y - 1) * atlas.width + c.x + x) * 4 + 3] == 0 && atlas.rgba[(std::size_t(c.y + c.h) * atlas.width + c.x + x) * 4 + 3] == 0);
            }
            for (std::uint32_t y = 0; y < c.h; ++y) {
                CHECK(atlas.rgba[(std::size_t(c.y + y) * atlas.width + c.x - 1) * 4 + 3] == 0 && atlas.rgba[(std::size_t(c.y + y) * atlas.width + c.x + c.w) * 4 + 3] == 0);
            }
        };
        const Cell idle = cell_of(level_bar_box(LevelSegment::none, false));
        CHECK(idle.w == std::uint32_t(kLevelBarW) * kHandleAtlasScale && idle.h == std::uint32_t(kLevelBarH) * kHandleAtlasScale);
        gutter_clear(idle);
        // Only the hot segment lights: outside its third every pixel is the idle bar's. Hovered and dragged differ, in the
        // lit segment only. A third of the bar's own width is the seam: the hot capsule stays inside it.
        for (const auto seg : {LevelSegment::ccw, LevelSegment::level, LevelSegment::cw})
            for (const bool active : {false, true}) {
                const Cell hot = cell_of(level_bar_box(seg, active));
                gutter_clear(hot);
                CHECK(hot.w == idle.w && hot.h == idle.h);
                const std::uint32_t lo = std::uint32_t(seg) * idle.w / 3, hi = (std::uint32_t(seg) + 1) * idle.w / 3;
                int lit = 0, elsewhere = 0;
                for (std::uint32_t y = 0; y < idle.h; ++y)
                    for (std::uint32_t x = 0; x < idle.w; ++x) {
                        const bool same = std::equal(at(hot, x, y), at(hot, x, y) + 4, at(idle, x, y));
                        if (x >= lo && x < hi) lit += !same; else elsewhere += !same;
                    }
                CHECK(lit > int(idle.w * idle.h) / 12 && elsewhere == 0);
            }
        // The bar's silhouette: a capsule, the same in every state; its ends round, its middle solid.
        CHECK(at(idle, 0, 0)[3] == 0 && at(idle, idle.w - 1, idle.h - 1)[3] == 0 && at(idle, idle.w / 2, 1)[3] == 255);
        const std::array<std::uint32_t, 2> outer_right{1, 3}, bottom{2, 3};
        for (std::uint32_t corner = 0; corner < 4; ++corner) {
            const bool right = corner == outer_right[0] || corner == outer_right[1], down = corner == bottom[0] || corner == bottom[1];
            const Cell quiet = cell_of(corner_cap_box(corner, false, false)), lit = cell_of(corner_cap_box(corner, true, false)), held = cell_of(corner_cap_box(corner, true, true));
            gutter_clear(quiet); gutter_clear(lit); gutter_clear(held);
            const std::uint32_t k = kHandleAtlasScale, in = std::uint32_t(kCapInner) * k, n = quiet.w;
            // A point of the cell by its distance past the corner along x and y (tile px): the same L in every corner.
            const auto spot = [&](float out_x, float out_y) {
                const auto ax = std::uint32_t((float(in) + out_x * float(k)) + 0.5f), ay = std::uint32_t((float(in) + out_y * float(k)) + 0.5f);
                return std::pair{right ? ax : n - 1 - ax, down ? ay : n - 1 - ay};
            };
            const auto [lx, ly] = spot(kCapGap, 0);       // on its side leg, by the corner
            CHECK(at(quiet, lx, ly)[3] == 255 && near(at(quiet, lx, ly)[0] / 255.f, m.primary[0], 2.f / 255.f));
            CHECK(at(lit, lx, ly)[3] == 255 && near(at(lit, lx, ly)[0] / 255.f, m.primary_bright[0], 2.f / 255.f));
            const auto [tx, ty] = spot(kCapGap, kCapGap - kCapLegHot + 3);  // near the end of a lit leg, past a quiet one's
            CHECK(at(quiet, tx, ty)[3] == 0 && at(lit, tx, ty)[3] == 255 && at(held, tx, ty)[3] == 255);
            const auto [ix, iy] = spot(-3, -3);          // on the desktop's side of the corner: nothing, whatever the state
            CHECK(at(quiet, ix, iy)[3] == 0 && at(lit, ix, iy)[3] == 0);
        }
    }
    {
        const auto a = render_handle_atlas({0.9f, 0.2f, 0.1f}), b = render_handle_atlas({0.1f, 0.5f, 0.9f});
        CHECK(a.rgba != b.rgba);  // another theme, another bar
    }

    // The `roll` frame: kMaxDesktops (four) rolls and icons ("bar" on every desktop in the overview), a hover with its
    // segment and active; anything else is dropped without stopping.
    {
        const auto good = decode_control(frame("{\"roll\":[-300,0,9000,-9000],\"icons\":[\"bar\",\"none\",\"bar\",\"bar\"],\"hover\":0,\"side\":\"cw\",\"active\":true}"));
        CHECK(std::holds_alternative<ControlMessage>(good));
        if (const auto* m = std::get_if<ControlMessage>(&good)) {
            CHECK(m->op == ControlOp::roll);
            const auto& r = std::get<RollPayload>(m->payload);
            CHECK(r.roll[0] == -300 && r.roll[1] == 0 && r.roll[2] == 9000 && r.roll[3] == -9000);
            CHECK(r.bars[0] && !r.bars[1] && r.bars[2] && r.bars[3] && r.hover == 0 && r.side == int(LevelSegment::cw) && r.active);
        }
        const auto none = decode_control(frame("{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}"));
        CHECK(std::holds_alternative<ControlMessage>(none));
        if (const auto* m = std::get_if<ControlMessage>(&none)) CHECK(std::get<RollPayload>(m->payload).side == int(LevelSegment::none));
        for (const auto& [name, segment] : {std::pair{"ccw", LevelSegment::ccw}, std::pair{"level", LevelSegment::level}, std::pair{"cw", LevelSegment::cw}}) {
            const auto one = decode_control(frame(std::string("{\"roll\":[0,0,0,0],\"icons\":[\"bar\",\"bar\",\"bar\",\"bar\"],\"hover\":3,\"side\":\"") + name + "\",\"active\":false}"));
            if (const auto* m = std::get_if<ControlMessage>(&one)) {
                const auto& r = std::get<RollPayload>(m->payload);
                CHECK(r.bars == (std::array<bool, kMaxDesktops>{true, true, true, true}) && r.hover == 3 && r.side == int(segment));
            } else CHECK(false);
        }
        for (const char* bad : {
                 "{\"roll\":[0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,9001],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,18446744073709551615],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0.5,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"left\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"both\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",  // the brackets' old value
                 "{\"roll\":[0,0,0,0],\"icons\":[\"ccw\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":4,\"side\":\"cw\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-2,\"side\":\"none\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":0}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\"}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"active\":false}",  // no side (the old 4-key frame)
                 "{\"roll\":[0,0,0,0],\"icons\":[\"bar\",\"bar\",\"bar\",\"bar\"],\"hover\":0,\"side\":\"none\",\"active\":false}",  // hover without a side
                 "{\"roll\":[0,0,0,0],\"icons\":[\"bar\",\"bar\",\"bar\",\"bar\"],\"hover\":-1,\"side\":\"level\",\"active\":false}",  // a side without a hover
                 "{\"roll\":[0,0,0,0],\"icons\":[\"bar\",\"bar\",\"bar\",\"bar\"],\"hover\":0,\"side\":\"up\",\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"bar\",\"bar\",\"bar\",\"bar\"],\"hover\":0,\"side\":1,\"active\":false}",
                 "{\"roll\":[0,0,0,0],\"icons\":[\"none\",\"none\",\"none\",\"none\"],\"hover\":-1,\"side\":\"none\",\"active\":false,\"x\":1}"}) {
            const auto r = decode_control(frame(bad));
            const auto* e = std::get_if<Error>(&r);
            CHECK(e && e->code == kInvalidRollCode && ignorable_control_error(*e));
        }
    }

    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("roll_level: ok");
    return 0;
}
