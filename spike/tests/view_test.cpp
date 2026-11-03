#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
bool near(float a, float b, float eps = 0.5f) { return std::fabs(a - b) <= eps; }
bool same(const FRect& r, FRect e) { return near(r.x, e.x, 1e-3f) && near(r.y, e.y, 1e-3f) && near(r.width, e.width, 1e-3f) && near(r.height, e.height, 1e-3f); }

static float lerp_test(float a, float b, float t) { return a + (b - a) * t; }

int main() {
    const Size eq[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
    // Overview: 3 x 16:9 desktops in 1920x1200 with 40 px gaps and margins.
    auto o = overview_layout(eq, 1920, 1200, 40);
    CHECK(o.size() == 3);
    const float w = (1920.f - 4 * 40.f) / 3.f;
    CHECK(near(o[0].x, 40) && near(o[0].width, w) && near(o[0].height, w * 1080.f / 1920.f));
    CHECK(near(o[1].x, 40 + w + 40) && near(o[2].x + o[2].width, 1880));
    CHECK(near(o[0].y, (1200 - o[0].height) / 2));
    auto wide = overview_layout(eq, 1920, 1200, 0);
    CHECK(near(wide[0].x, 0) && near(wide[0].width, 640));
    // Spacing is bounded; absurd spacing never yields negative tiles.
    for (auto& r : overview_layout(eq, 1920, 1200, 5000)) CHECK(r.width >= 0 && r.height >= 0);

    // Zoomed: selected fills (aspect-fit), neighbours offset by one view width plus spacing.
    auto z = zoomed_layout(eq, 1920, 1200, 40, 1);
    CHECK(near(z[1].x, 0) && near(z[1].width, 1920) && near(z[1].height, 1080) && near(z[1].y, 60));
    CHECK(near(z[0].x, -1960) && near(z[2].x, 1960));
    auto mid = zoomed_layout(eq, 1920, 1200, 40, 1.5f);  // sliding between 1 and 2
    CHECK(near(mid[1].x, -980) && near(mid[2].x, 980));

    // Blend endpoints and monotone easing.
    auto b0 = blended_layout(eq, 1920, 1200, 40, 0, 1), b1 = blended_layout(eq, 1920, 1200, 40, 1, 1);
    CHECK(near(b0[1].x, o[1].x) && near(b1[1].width, 1920));

    // three equal sources match old layout (outputs captured from the pre-span implementation).
    const FRect ov[] = {{40, 435, 586.6667f, 330}, {666.6667f, 435, 586.6667f, 330}, {1293.3334f, 435, 586.6667f, 330}};
    const struct { float pos; FRect r[3]; } zoomed_old[] = {
        {0, {{0, 60, 1920, 1080}, {1960, 60, 1920, 1080}, {3920, 60, 1920, 1080}}},
        {1, {{-1960, 60, 1920, 1080}, {0, 60, 1920, 1080}, {1960, 60, 1920, 1080}}},
        {1.5f, {{-2940, 60, 1920, 1080}, {-980.0001f, 60, 1920, 1080}, {980, 60, 1920, 1080}}}};
    for (const auto& c : zoomed_old) {
        auto z0 = blended_layout(eq, 1920, 1200, 40, 0, c.pos), z1 = blended_layout(eq, 1920, 1200, 40, 1, c.pos);
        CHECK(z0.size() == 3 && z1.size() == 3);
        for (int i = 0; i < 3; ++i) CHECK(same(z0[i], ov[i]) && same(z1[i], c.r[i]));
    }

    // mixed overview shares height: 16:9, 32:9 and 3440x1440 side by side within the view.
    const Size mixed[] = {{1920, 1080}, {3840, 1080}, {3440, 1440}};
    auto mo = blended_layout(mixed, 1920, 1200, 40, 0, 0);
    CHECK(mo.size() == 3 && mo[0].height > 0);
    for (int i = 0; i < 3; ++i) CHECK(near(mo[i].height, mo[0].height, 1e-3f) && near(mo[i].width, mo[i].height * mixed[i].w / mixed[i].h));
    CHECK(near(mo[1].width, 2 * mo[0].width) && mo[0].x >= 0 && mo[2].x + mo[2].width <= 1920 + 1e-3f);
    CHECK(mo[0].x + mo[0].width <= mo[1].x && mo[1].x + mo[1].width <= mo[2].x);
    float total = 0; for (auto& r : mo) total += r.width;
    CHECK(total <= 1920);

    // zoomed mixed centres selected: tile 1 (32:9) centred and fitted; neighbours share its height.
    auto mz = blended_layout(mixed, 1920, 1200, 40, 1, 1);
    CHECK(mz.size() == 3 && near(mz[1].x + mz[1].width / 2, 960));
    CHECK(mz[1].x >= -0.5f && mz[1].x + mz[1].width <= 1920.5f && mz[1].y >= -0.5f && mz[1].y + mz[1].height <= 1200.5f);
    CHECK(near(mz[1].width, 1920) && near(mz[0].height, mz[1].height, 1e-3f) && near(mz[2].height, mz[1].height, 1e-3f));
    CHECK(near(mz[0].x + mz[0].width + 40, mz[1].x) && near(mz[1].x + mz[1].width + 40, mz[2].x));
    // Fractional position: the lerp of the two centres sits at the view centre.
    auto mf = zoomed_layout(mixed, 1920, 1200, 40, 1.25f);
    const float c1 = mf[1].x + mf[1].width / 2, c2 = mf[2].x + mf[2].width / 2;
    CHECK(near(c1 + (c2 - c1) * 0.25f, 960));
    CHECK(overview_layout(std::span<const Size>{}, 1920, 1200, 40).empty());
    const Size bad[] = {{1920, 1080}, {0, 1080}};
    CHECK(blended_layout(bad, 1920, 1200, 40, 0.5f, 0).empty());

    CHECK(smoothstep(0) == 0 && smoothstep(1) == 1 && near(smoothstep(0.5f), 0.5f, 1e-3f) && smoothstep(-1) == 0 && smoothstep(2) == 1);
    for (float t = 0; t < 1; t += 0.1f) CHECK(smoothstep(t + 0.1f) >= smoothstep(t));

    // Animator: time-based, reaches target exactly, retargets from the current state.
    ViewAnimator a(200, 200);
    a.set_target(false, 0, 40, 0); a.advance(1000);
    CHECK(!a.animating() && a.zoom() == 0 && a.position() == 0);
    a.set_target(true, 2, 40, 1000);
    a.advance(1100); CHECK(a.animating() && a.zoom() > 0 && a.zoom() < 1 && a.position() > 0 && a.position() < 2);
    a.advance(1200); CHECK(!a.animating() && a.zoom() == 1 && a.position() == 2 && a.selected() == 2);
    a.set_target(true, 1, 120, 1200); a.advance(1300);
    const float halfway = a.position();
    CHECK(halfway > 1 && halfway < 2 && a.spacing() > 40 && a.spacing() < 120);
    a.set_target(false, 1, 120, 1300);  // retarget mid-flight: continues from current position
    a.advance(1300); CHECK(near(a.position(), halfway, 1e-3f));
    a.advance(2000); CHECK(a.zoom() == 0 && a.position() == 1 && a.spacing() == 120);
    a.set_target(true, 9, 9999, 2000); a.advance(3000);  // selection and spacing are clamped
    CHECK(a.selected() == 2 && a.spacing() == float(kMaxSpacing));

    // Desktop switch: gentle ease-in/out, momentum kept across quick presses, never overshoots.
    CHECK(smootherstep(0) == 0 && smootherstep(1) == 1 && near(smootherstep(0.5f), 0.5f, 1e-3f));
    ViewAnimator m;  // defaults: 220 ms zoom, 300 ms switch
    m.set_target(true, 0, 40, 0); m.advance(1000);
    m.set_target(true, 1, 40, 1000);
    m.advance(1030); CHECK(m.position() > 0 && m.position() < 0.02f);       // slow start
    m.advance(1150); CHECK(near(m.position(), 0.5f, 1e-3f));               // symmetric
    const float peak = m.velocity();
    CHECK(peak > 1.f / 300 && peak < 2.f / 300);                           // not exaggerated (1.875x mean)
    m.advance(1270); CHECK(m.position() > 0.98f && m.position() < 1.f);     // slow finish
    m.advance(1300); CHECK(!m.animating() && m.position() == 1 && m.velocity() == 0);
    // Second press mid-move: no velocity jump and monotone, ending exactly with no overshoot.
    m.set_target(true, 0, 40, 2000); m.advance(3000);
    m.set_target(true, 1, 40, 3000); m.advance(3150);
    const float v_before = m.velocity(), p_before = m.position();
    m.set_target(true, 2, 40, 3150);
    CHECK(near(m.velocity(), v_before, 1e-6f) && near(m.position(), p_before, 1e-6f));
    float last = m.position();
    for (double t = 3150; t <= 3500; t += 5) { m.advance(t); CHECK(m.position() >= last - 1e-5f && m.position() <= 2.f); last = m.position(); }
    CHECK(!m.animating() && m.position() == 2);
    // Reversal mid-move: bounded drift past the current position, then settles on the target.
    m.set_target(true, 1, 40, 4000); m.advance(4150);
    const float turn = m.position();
    m.set_target(true, 2, 40, 4150);
    float lo = turn;
    for (double t = 4150; t <= 4500; t += 5) { m.advance(t); lo = std::min(lo, m.position()); CHECK(m.position() <= 2.f); }
    CHECK(turn - lo < 0.1f && m.position() == 2);
    // Zoom/spacing changes alone do not restart an in-flight switch.
    m.set_target(true, 1, 40, 5000); m.advance(5100); const float mid_pos = m.position();
    m.set_target(true, 1, 80, 5100); m.advance(5100); CHECK(near(m.position(), mid_pos, 1e-6f));
    m.advance(5300); CHECK(m.position() == 1);

    {  // Per-desktop placement (orbit round you, distance, curvature). Every desktop always faces you.
    const Canvas cv{1920, 1200, 0.5f, 0.3125f};  // square canvas pixels: half_w/half_h == width/height
    const FRect centred{480, 330, 960, 540};      // its centre is the canvas centre: straight ahead
    const FRect tile{240, 300, 960, 540};         // left of centre and a little up
    auto plane = [&](float px, float py) { return Vec3{(2 * px / 1920 - 1) * 0.5f, (1 - 2 * py / 1200) * 0.3125f, -1}; };
    auto close3 = [](Vec3 a, Vec3 b, float eps = 1e-5f) { return near(a.x, b.x, eps) && near(a.y, b.y, eps) && near(a.z, b.z, eps); };
    auto sub = [](Vec3 a, Vec3 b) { return Vec3{a.x - b.x, a.y - b.y, a.z - b.z}; };
    auto dot = [](Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; };
    auto cross = [](Vec3 a, Vec3 b) { return Vec3{a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; };
    auto len = [&](Vec3 a) { return std::sqrt(dot(a, a)); };
    auto unit = [&](Vec3 a) { const float l = len(a); return Vec3{a.x / l, a.y / l, a.z / l}; };
    auto dist = [&](Vec3 a, Vec3 b) { return len(sub(a, b)); };
    auto azimuth = [](Vec3 a) { return std::atan2(a.x, -a.z); };
    auto elevation = [](Vec3 a) { return std::atan2(a.y, std::sqrt(a.x * a.x + a.z * a.z)); };
    const float px_world = 2 * 0.5f / 1920;  // world units per canvas pixel
    const float half_pi = float(M_PI) / 2;
    // A centred desktop with the default placement is the old flat canvas plane, exactly (same vertices as the
    // pre-placement quads), one column; so is anything past its edge (its frame / halo).
    for (float px : {480.f, 960.f, 1440.f, 100.f}) for (float py : {330.f, 870.f, 10.f})
        CHECK(close3(place_point(cv, centred, {}, px, py), plane(px, py), 1e-6f));
    auto flat_mesh = tile_mesh(cv, centred, {}, centred);
    CHECK(flat_mesh.size() == 4 && close3(flat_mesh[0].world, plane(480, 330)) && close3(flat_mesh[3].world, plane(1440, 870)));
    CHECK(near(flat_mesh[0].u, 0, 1e-6f) && near(flat_mesh[0].v, 0, 1e-6f) && near(flat_mesh[3].u, 1, 1e-6f) && near(flat_mesh[3].v, 1, 1e-6f));
    CHECK(near(flat_mesh[1].py, 870, 1e-4f) && near(flat_mesh[2].px, 1440, 1e-4f));  // top, bottom per column
    // The billboard: wherever it is, a desktop is square to the line of sight at its centre (its normal points at the
    // eye) and upright along its meridian (its right edge level and heading round you, its top up toward the zenith), its
    // centre in its slot's direction turned by the orbit, at the slot centre's distance times `distance`. Its shape never
    // changes: the corners keep their distances from its centre.
    const float tile_diagonal = dist(plane(240, 300), plane(1200, 840));
    auto check_facing = [&](const FRect& t, const Placement& p) {
        const float cx = t.x + t.width / 2, cy = t.y + t.height / 2;
        const float wx = (2 * cx / cv.view_w - 1) * cv.half_w, wy = (1 - 2 * cy / cv.view_h) * cv.half_h;
        const float A = std::atan2(wx, 1.f) + p.x, E = std::atan2(wy, std::sqrt(1 + wx * wx)) + p.y;
        const float r = std::sqrt(1 + wx * wx + wy * wy) * p.distance;
        const Vec3 c = place_point(cv, t, p, cx, cy);
        CHECK(close3(c, Vec3{r * std::cos(E) * std::sin(A), r * std::sin(E), -r * std::cos(E) * std::cos(A)}, 1e-5f * r));
        // Tangents from points symmetric about the centre (on a cylinder their chord is parallel to the centre's tangent),
        // far apart for precision: the surface goes on past the tile's edges.
        const float du = p.curvature > 0 ? t.width / 2 : 400, dv = 400;
        const Vec3 right = unit(sub(place_point(cv, t, p, cx + du, cy), place_point(cv, t, p, cx - du, cy)));
        const Vec3 up = unit(sub(place_point(cv, t, p, cx, cy - dv), place_point(cv, t, p, cx, cy + dv)));
        const Vec3 n = cross(right, up), eye = unit(Vec3{-c.x, -c.y, -c.z});
        CHECK(len(cross(n, eye)) < 3e-5f && dot(n, eye) > 0);                                          // faces the eye
        CHECK(close3(right, Vec3{std::cos(A), 0, std::sin(A)}, 3e-5f));                                 // level: never rolled
        CHECK(close3(up, Vec3{-std::sin(A) * std::sin(E), std::cos(E), std::cos(A) * std::sin(E)}, 3e-5f));  // its meridian
        if (p.curvature == 0) {
            CHECK(near(dist(place_point(cv, t, p, t.x, t.y), place_point(cv, t, p, t.x + t.width, t.y + t.height)),
                       std::hypot(t.width, t.height) * px_world, 1e-5f * r));
            for (const auto& [px, py] : {std::pair{t.x, t.y}, std::pair{t.x + t.width, t.y + t.height}, std::pair{t.x - 40, cy}})
                CHECK(near(dot(sub(place_point(cv, t, p, px, py), c), eye), 0, 1e-5f * r));  // flat: one plane, square to the eye
        } else {  // curved: the edges bend toward you, symmetrically
            const float l = dot(sub(place_point(cv, t, p, t.x, cy), c), eye), rr = dot(sub(place_point(cv, t, p, t.x + t.width, cy), c), eye);
            CHECK(l > 0 && near(l, rr, 1e-5f * r));
        }
    };
    const FRect tiles[] = {centred, tile, {1300.5f, 700.25f, 512.3f, 288.7f}, {0, 0, 300, 200}, {1700, 1100, 220, 100},
                           {-1960, 60, 1920, 1080}, {3900.75f, -400.5f, 640, 360}};
    const float pi = float(M_PI);
    const Placement placements[] = {{}, {pi, 0, 1, 0}, {-pi, 0, 1, 0}, {half_pi, 0, 1, 0}, {-2.2f, 0, 1, 0},
                                    {0, half_pi, 1, 0}, {0, -half_pi, 1, 0}, {0, 0.7f, 1, 0}, {pi, half_pi, 1, 0},
                                    {-0.7f, 0.3f, 1.6f, 1.f}, {2.9f, -1.2f, 0.5f, 2 * pi / 3}, {0.05f, -0.02f, 1.37f, 0.3f},
                                    {-pi, -0.6f, 2.f, 0}, {1.f, 1.4f, 0.75f, half_pi}};
    for (const FRect& t : tiles) for (const Placement& p : placements) check_facing(t, p);
    // An off-centre desktop at its slot already faces you: its centre stays where the plane put it (the same direction
    // and distance) and it turns toward you about it. This one is left of centre and a little up, so in depth its left
    // edge comes forward and its right edge goes back, and it tips to face down at you (top forward, bottom back).
    const Vec3 c0 = place_point(cv, tile, {}, 720, 570);
    CHECK(close3(c0, plane(720, 570)));
    CHECK(place_point(cv, tile, {}, 240, 570).z > -1 && place_point(cv, tile, {}, 1200, 570).z < -1);
    CHECK(place_point(cv, tile, {}, 720, 300).z > -1 && place_point(cv, tile, {}, 720, 840).z < -1);
    CHECK(!close3(place_point(cv, tile, {}, 240, 300), plane(240, 300), 1e-3f));  // so no longer on the canvas plane
    // Curvature zero is flat; any curvature keeps the centre and its tangent frame in place.
    Placement curved; curved.curvature = half_pi;
    const Vec3 k0 = place_point(cv, centred, curved, 960, 600);
    CHECK(close3(k0, plane(960, 600)));
    const Vec3 k1 = place_point(cv, centred, curved, 960.01f, 600);
    CHECK(near((k1.x - k0.x) / (0.01f * px_world), 1, 1e-2f) && near(k1.z - k0.z, 0, 1e-6f));  // tangent along +x
    CHECK(close3(place_point(cv, tile, curved, 720, 570), c0));  // off-centre too
    // Edges bend toward the viewer symmetrically, aspect not stretched: arc length == flat width, height unchanged.
    const Vec3 left = place_point(cv, centred, curved, 480, 600), right = place_point(cv, centred, curved, 1440, 600);
    CHECK(left.z > -1 && near(left.z, right.z, 1e-6f) && near(left.x - k0.x, -(right.x - k0.x), 1e-6f));
    const float tw = 960 * px_world;
    CHECK(near(right.x - left.x, 2 * (tw / half_pi) * std::sin(half_pi / 2), 1e-5f));  // chord of a 90 degree arc
    for (const FRect& t : {centred, tile}) {
        auto mesh = tile_mesh(cv, t, curved, t);
        CHECK(mesh.size() > 4 && mesh.size() % 2 == 0);
        float arc = 0;
        for (std::size_t i = 2; i < mesh.size(); i += 2) arc += dist(mesh[i].world, mesh[i - 2].world);
        CHECK(near(arc, tw, tw * 2e-3f));
        CHECK(near(dist(mesh[0].world, mesh[1].world), 540 * px_world, 1e-5f));  // vertical edges stay straight and full height
        for (std::size_t i = 2; i < mesh.size(); i += 2) CHECK(mesh[i].u > mesh[i - 2].u);  // texture runs monotonically along the arc
    }
    // A frame/halo rect past the tile continues on the same cylinder.
    const Vec3 beyond = place_point(cv, centred, curved, 1480, 600);
    CHECK(beyond.z > right.z && beyond.x > right.x);
    // Position: x / y orbit the desktop round the eye (a rigid rotation about it): x turns it round you about the
    // vertical, y lifts it along its own meridian; the shape and the distance never change.
    const float a0 = azimuth(c0);  // the slot's own azimuth (this tile is left of centre)
    CHECK(a0 < 0);
    Placement round; round.x = half_pi;  // a quarter turn to the right
    const Vec3 r0 = place_point(cv, tile, round, 720, 570);
    CHECK(near(azimuth(r0), a0 + half_pi, 1e-5f) && near(r0.y, c0.y, 1e-6f) && near(len(r0), len(c0), 1e-6f));
    CHECK(near(dist(place_point(cv, tile, round, 240, 300), place_point(cv, tile, round, 1200, 840)), tile_diagonal, 1e-5f));
    // It keeps facing you as at its slot: the angle between the view ray and the desktop's edges is unchanged.
    auto angle = [&](Vec3 a, Vec3 b) { return std::acos(std::clamp(dot(a, b) / (len(a) * len(b)), -1.f, 1.f)); };
    CHECK(near(angle(r0, place_point(cv, tile, round, 240, 570)), angle(c0, place_point(cv, tile, {}, 240, 570)), 1e-5f));
    Placement behind; behind.x = pi;  // directly behind you, whatever the slot
    CHECK(near(std::fabs(azimuth(place_point(cv, tile, behind, 720, 570))), pi - std::fabs(a0), 1e-5f) &&
          place_point(cv, tile, behind, 720, 570).z > 0);
    Placement lifted; lifted.y = pi / 4;  // up along its own meridian
    const Vec3 l0 = place_point(cv, tile, lifted, 720, 570);
    CHECK(near(elevation(l0), elevation(c0) + pi / 4, 1e-5f) && near(azimuth(l0), a0, 1e-5f) && near(len(l0), len(c0), 1e-6f));
    Placement overhead; overhead.y = half_pi - elevation(c0);  // straight up: still facing you (down at you), still level
    const Vec3 o0 = place_point(cv, tile, overhead, 720, 570);
    CHECK(near(o0.x, 0, 1e-5f) && near(o0.z, 0, 1e-5f) && near(o0.y, len(c0), 1e-5f));
    Placement both{-0.7f, 0.3f, 1, 1.f};  // the orbit is a rigid rotation about the eye of the whole (curved) desktop
    const Placement unmoved{0, 0, 1, 1.f};
    for (float px : {240.f, 720.f, 1200.f}) {
        const Vec3 a = place_point(cv, tile, unmoved, px, 300), b = place_point(cv, tile, both, px, 300);
        CHECK(near(len(a), len(b), 1e-5f));
        CHECK(near(dist(a, place_point(cv, tile, unmoved, 1200 - (px - 240), 840)), dist(b, place_point(cv, tile, both, 1200 - (px - 240), 840)), 1e-5f));
    }
    // Distance: twice as far along the same direction, same physical size -> half the apparent size.
    Placement far; far.distance = 2;
    const Vec3 f0 = place_point(cv, tile, far, 720, 570);
    CHECK(close3(f0, Vec3{2 * c0.x, 2 * c0.y, -2}));
    CHECK(near(dist(place_point(cv, tile, far, 240, 300), place_point(cv, tile, far, 1200, 840)), tile_diagonal, 1e-5f));
    const Vec3 g0 = place_point(cv, centred, far, 960, 600), gr = place_point(cv, centred, far, 1440, 600);
    CHECK(close3(g0, Vec3{0, 0, -2}) && near(gr.z, -2, 1e-6f));
    CHECK(near((gr.x / -gr.z) - (g0.x / -g0.z), 0.5f * (plane(1440, 600).x - plane(960, 600).x), 1e-6f));
    // Zooming into a desktop fades its placement to flat-in-front, keeping curvature; neighbours unaffected.
    CHECK(near(zoom_weight(1, 1, 1), 1, 1e-6f) && near(zoom_weight(1, 1, 0), 0, 1e-6f) && near(zoom_weight(0, 1, 1), 0, 1e-6f));
    CHECK(near(zoom_weight(1, 1.25f, 1), 0.75f, 1e-6f) && near(zoom_weight(0.5f, 1, 1), 0.5f, 1e-6f));
    Placement mixed{0.4f, -0.2f, 1.6f, 1.f};
    const Placement gone = faded(mixed, 1), half = faded(mixed, 0.5f), kept = faded(mixed, 0);
    CHECK(gone.x == 0 && gone.y == 0 && gone.distance == 1 && gone.curvature == 1.f);
    CHECK(near(half.x, 0.2f, 1e-6f) && near(half.y, -0.1f, 1e-6f) && near(half.distance, 1.3f, 1e-6f) && half.curvature == 1.f);
    CHECK(same_placement(kept, mixed));
    // Drawn while zooming (regression: in anchor mode, zooming in after a pill drag must not send the desktop back to its
    // default place). Anchor mode (keep_place) and within kZoomInPlaceMaxRad of straight ahead: the zoomed desktop keeps the
    // direction round you it was dragged to, all the way in, and comes to distance 1 there (filling the view); the others
    // follow it as its row, keeping their distance and curvature. Farther (side, behind) in anchor mode, and always in locked
    // and follow modes (keep_place false): it comes straight ahead, as before the fix.
    {
        const auto ov = overview_layout(eq, 1920, 1200, 40);
        const Placement dragged{0.5f, 0.2f, 1.6f, 1.f}, other{-0.4f, 0.1f, 1.3f, 0.5f};
        const std::vector<Placement> own{other, dragged, Placement{}};
        auto centre_of = [&](const FRect& t, const Placement& p) { return place_point(cv, t, p, t.x + t.width / 2, t.y + t.height / 2); };
        const Vec3 where = centre_of(ov[1], dragged);  // where the drag left it in the overview
        CHECK(std::acos(-unit(where).z) < kZoomInPlaceMaxRad && std::acos(-unit(where).z) > 0.4f);
        for (bool keep : {true, false}) {
            CHECK(zoomed_placements(cv, ov, ov, own, 0, 1, keep).size() == 3);
            for (std::size_t i = 0; i < 3; ++i) {
                CHECK(same_placement(zoomed_placements(cv, ov, ov, own, 0, 1, keep)[i], own[i]));      // overview: unchanged
                CHECK(same_placement(zoomed_placements(cv, ov, ov, own, -1, 0.4f, keep)[i], own[i]));  // clamped; mid-switch
            }
            CHECK(zoomed_placements(cv, ov, ov, std::span<const Placement>(own).first(1), 1, 1, keep).size() == 3);  // short list
        }
        for (float z : {0.f, 0.25f, 0.5f, 0.75f, 1.f}) {
            const auto tl = blended_layout(eq, 1920, 1200, 40, z, 1);
            const auto pl = zoomed_placements(cv, ov, tl, own, z, 1, true);
            const Vec3 c = centre_of(tl[1], pl[1]);
            CHECK(near(azimuth(c), azimuth(where), 1e-5f) && near(elevation(c), elevation(where), 1e-5f));  // never moves round you
            CHECK(near(pl[1].distance, lerp_test(1.6f, 1.f, z), 1e-5f) && pl[1].curvature == 1.f);
            check_facing(tl[1], pl[1]);
        }
        const auto zl = zoomed_layout(eq, 1920, 1200, 40, 1);
        const auto in = zoomed_placements(cv, ov, zl, own, 1, 1, true);
        CHECK(in[1].distance == 1 && in[1].curvature == 1.f);
        CHECK(!close3(centre_of(zl[1], in[1]), Vec3{0, 0, -1}, 1e-2f));  // not back at the default straight-ahead slot
        CHECK(near(len(centre_of(zl[1], in[1])), 1, 1e-5f));            // the canvas distance: it fills the view, there
        Placement flat = dragged; flat.curvature = 0;
        const std::vector<Placement> own_flat{other, flat, Placement{}};
        const auto in_flat = zoomed_placements(cv, ov, zl, own_flat, 1, 1, true);
        CHECK(near(dist(place_point(cv, zl[1], in_flat[1], 0, 60), place_point(cv, zl[1], in_flat[1], 1920, 1140)),
                   dist(plane(0, 60), plane(1920, 1140)), 1e-5f));  // full view size
        for (std::size_t i : {0u, 2u}) {  // the row turned with it: same orbit, own distance and curvature
            CHECK(near(in[i].x, in[1].x, 1e-6f) && near(in[i].y, in[1].y, 1e-6f));
            CHECK(in[i].distance == own[i].distance && in[i].curvature == own[i].curvature);
            check_facing(zl[i], in[i]);
        }
        // Straight ahead instead: dragged far to the side or behind you in anchor mode, or anywhere in locked / follow.
        // It moves from where it is to flat in front of you (the canvas centre), its direction blending there with the
        // zoom; the row is back at its slots.
        struct Case { Placement p; std::size_t index; bool keep; };
        for (const Case& k : {Case{{1.2f, 0.3f, 1.6f, 1.f}, 1, true}, Case{{3.0f, 0.f, 1.f, 0.f}, 2, true},
                              Case{{0.2f, 1.3f, 1.f, 0.f}, 0, true}, Case{dragged, 1, false}, Case{{3.0f, 0.f, 1.f, 0.f}, 2, false}}) {
            std::vector<Placement> o{other, Placement{}, Placement{}};
            o[k.index] = k.p;
            const Vec3 from = centre_of(ov[k.index], k.p);
            if (k.keep) CHECK(std::acos(-unit(from).z) > kZoomInPlaceMaxRad);
            for (float z : {0.f, 0.25f, 0.5f, 0.75f, 1.f}) {
                const auto tl = blended_layout(eq, 1920, 1200, 40, z, float(k.index));
                const auto pl = zoomed_placements(cv, ov, tl, o, z, float(k.index), k.keep);
                const Vec3 c = centre_of(tl[k.index], pl[k.index]);
                CHECK(near(azimuth(c), (1 - z) * azimuth(from), 1e-4f) && near(elevation(c), (1 - z) * elevation(from), 1e-4f));
                CHECK(near(pl[k.index].distance, lerp_test(k.p.distance, 1.f, z), 1e-5f));
                check_facing(tl[k.index], pl[k.index]);
            }
            const auto zk = zoomed_layout(eq, 1920, 1200, 40, float(k.index));
            const auto full = zoomed_placements(cv, ov, zk, o, 1, float(k.index), k.keep);
            CHECK(close3(centre_of(zk[k.index], full[k.index]), Vec3{0, 0, -1}, 1e-5f));
            for (std::size_t i = 0; i < 3; ++i) CHECK(near(full[i].x, 0, 1e-5f) && near(full[i].y, 0, 1e-5f));
        }
        // Switching while zoomed (position 1.5): the view's direction blends between the two desktops' places.
        const auto mid = zoomed_placements(cv, ov, zoomed_layout(eq, 1920, 1200, 40, 1.5f), own, 1, 1.5f, true);
        const float a1 = azimuth(centre_of(ov[1], dragged)), a2 = azimuth(centre_of(ov[2], Placement{}));
        CHECK(near(mid[1].x, mid[2].x, 1e-6f) && mid[1].x > std::min(a1, a2) - 0.2f && mid[1].x < std::max(a1, a2) + 0.2f);
        CHECK(near(mid[1].distance, 1.3f, 1e-5f) && near(mid[2].distance, 1.f, 1e-5f));
    }
    // The orbit's x eases the short way round: a desktop crossing behind you (+-pi) stays behind you.
    Placement from_behind; from_behind.x = 3.1f;
    Placement to_behind; to_behind.x = -3.1f;
    const Placement mid = ease_toward(from_behind, to_behind, kPlacementEaseMs, kPlacementEaseMs);
    CHECK(mid.x < -3.1f && mid.x > -3.1f - 0.1f);  // past -pi from below: the 0.08 rad gap, not the 6.2 rad way round
    CHECK(place_point(cv, tile, mid, 720, 570).z > 0);
    CHECK(ease_toward(from_behind, to_behind, 100 * kPlacementEaseMs, kPlacementEaseMs).x == -3.1f);  // lands exactly
    Placement near_front; near_front.x = 0.3f;
    CHECK(near(ease_toward(near_front, {}, kPlacementEaseMs, kPlacementEaseMs).x, 0.3f * std::exp(-1.f), 1e-6f));  // as before
    {  // Every field eases (and lands); same_placement compares all four.
        const Placement to{-0.4f, 0.2f, 1.5f, 0.8f};
        const Placement e = ease_toward({}, to, kPlacementEaseMs, kPlacementEaseMs);
        const float k = 1 - std::exp(-1.f);
        CHECK(near(e.x, -0.4f * k, 1e-6f) && near(e.y, 0.2f * k, 1e-6f) && near(e.distance, 1 + 0.5f * k, 1e-6f) && near(e.curvature, 0.8f * k, 1e-6f));
        CHECK(same_placement(ease_toward(e, to, 1e9f, kPlacementEaseMs), to));
        for (int f = 0; f < 4; ++f) {
            Placement other = to;
            (f == 0 ? other.x : f == 1 ? other.y : f == 2 ? other.distance : other.curvature) += 0.01f;
            CHECK(!same_placement(other, to));
        }
    }
    // Draw order: farthest first; equal-distance ties keep layout order; missing placements are flat.
    const FRect row3[] = {{0, 330, 600, 540}, {660, 330, 600, 540}, {1320, 330, 600, 540}};
    const Placement pushed[] = {{}, {0, 0, 1.8f, 0}};
    auto order = draw_order(cv, row3, pushed);
    CHECK(order.size() == 3 && order[0] == 1);
    const FRect same[] = {{660, 330, 600, 540}, {660, 330, 600, 540}};
    auto tied = draw_order(cv, same, std::span<const Placement>{});
    CHECK(tied[0] == 0 && tied[1] == 1);
    // Degenerate input yields nothing rather than NaNs.
    CHECK(tile_mesh(cv, {0, 0, 0, 10}, {}, tile).empty() && tile_mesh(cv, tile, {}, {0, 0, 0, 0}).empty());
    for (const auto& m : tile_mesh(cv, tile, Placement{0, 0, 1, float(M_PI) * 2 / 3}, {190, 250, 1060, 640}))
        CHECK(std::isfinite(m.world.x) && std::isfinite(m.world.y) && std::isfinite(m.world.z) && m.world.z < 0);
    CHECK(close3(place_point(cv, centred, Placement{0, 0, 0, 0}, 480, 330), plane(480, 330), 1e-6f));  // distance <= 0 reads as 1
    {  // Huge: the distance floor (wire 10, factor 0.1) shows a desktop ten times its size, far past the view.
        const Placement huge{0, 0, 0.1f, 0};
        auto seen = [](Vec3 a) { return std::pair{a.x / -a.z, a.y / -a.z}; };  // on the canvas plane, as projected
        for (const auto& [px, py] : {std::pair{480.f, 330.f}, std::pair{1440.f, 870.f}, std::pair{960.f, 600.f}}) {
            const auto [x, y] = seen(place_point(cv, centred, huge, px, py));
            const Vec3 flat = plane(px, py);
            CHECK(near(x, 10 * flat.x, 1e-4f) && near(y, 10 * flat.y, 1e-4f));
        }
        CHECK(near(seen(place_point(cv, centred, huge, 1440, 870)).first, 2.5f, 1e-4f));  // its right edge: 5x the view's
        check_facing(centred, huge);
        check_facing(tile, Placement{0.4f, -0.2f, 0.1f, 0});
        check_facing(tile, Placement{-2.f, 0.3f, 0.1f, float(M_PI) / 3});
        // The curved, closest desktop wraps round the eye (its edges may pass behind you: GL clips them); the mesh stays
        // finite and dense enough, and its centre stays straight ahead.
        const auto mesh = tile_mesh(cv, tile, Placement{0, 0, 0.1f, float(M_PI) * 2 / 3}, {190, 250, 1060, 640});
        CHECK(mesh.size() >= 10);
        for (const auto& m : mesh) CHECK(std::isfinite(m.world.x) && std::isfinite(m.world.y) && std::isfinite(m.world.z));
        CHECK(place_point(cv, centred, Placement{0, 0, 0.1f, float(M_PI) * 2 / 3}, 960, 600).z < 0);
        // The nearest is drawn last (over the others); zooming in still brings it to distance 1.
        const Placement mixed[] = {{}, huge, {0, 0, 2, 0}};
        const auto o = draw_order(cv, row3, mixed);
        CHECK(o.size() == 3 && o[0] == 2 && o[2] == 1);
        CHECK(near(faded(huge, 1).distance, 1, 1e-6f) && near(faded(huge, 0).distance, 0.1f, 1e-6f));
    }
    }
    std::cout << "view layout passed\n";
}
