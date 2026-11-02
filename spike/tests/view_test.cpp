#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
bool near(float a, float b, float eps = 0.5f) { return std::fabs(a - b) <= eps; }
bool same(const FRect& r, FRect e) { return near(r.x, e.x, 1e-3f) && near(r.y, e.y, 1e-3f) && near(r.width, e.width, 1e-3f) && near(r.height, e.height, 1e-3f); }

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

    {  // Per-desktop placement (position, distance, yaw/pitch/roll, curvature).
    const Canvas cv{1920, 1200, 0.5f, 0.3125f};  // square canvas pixels: half_w/half_h == width/height
    const FRect tile{240, 300, 960, 540};
    auto plane = [&](float px, float py) { return Vec3{(2 * px / 1920 - 1) * 0.5f, (1 - 2 * py / 1200) * 0.3125f, -1}; };
    auto close3 = [](Vec3 a, Vec3 b, float eps = 1e-5f) { return near(a.x, b.x, eps) && near(a.y, b.y, eps) && near(a.z, b.z, eps); };
    auto dist = [](Vec3 a, Vec3 b) { return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) + (a.z - b.z) * (a.z - b.z)); };
    const float px_world = 2 * 0.5f / 1920;  // world units per canvas pixel
    // Default placement is the old flat canvas plane, exactly (same vertices as the pre-placement quads), one column.
    for (float px : {240.f, 720.f, 1200.f, 100.f}) for (float py : {300.f, 840.f, 10.f})
        CHECK(close3(place_point(cv, tile, {}, px, py), plane(px, py)));
    auto flat_mesh = tile_mesh(cv, tile, {}, tile);
    CHECK(flat_mesh.size() == 4 && close3(flat_mesh[0].world, plane(240, 300)) && close3(flat_mesh[3].world, plane(1200, 840)));
    CHECK(near(flat_mesh[0].u, 0, 1e-6f) && near(flat_mesh[0].v, 0, 1e-6f) && near(flat_mesh[3].u, 1, 1e-6f) && near(flat_mesh[3].v, 1, 1e-6f));
    CHECK(near(flat_mesh[1].py, 840, 1e-4f) && near(flat_mesh[2].px, 1200, 1e-4f));  // top, bottom per column
    // Curvature zero is flat; any curvature keeps the centre and its tangent frame in place.
    const float half_pi = float(M_PI) / 2;
    Placement curved; curved.curvature = half_pi;
    const Vec3 c0 = place_point(cv, tile, curved, 720, 570);
    CHECK(close3(c0, plane(720, 570)));
    const Vec3 c1 = place_point(cv, tile, curved, 720.01f, 570);
    CHECK(near((c1.x - c0.x) / (0.01f * px_world), 1, 1e-2f) && near(c1.z - c0.z, 0, 1e-6f));  // tangent along +x
    // Edges bend toward the viewer symmetrically, aspect not stretched: arc length == flat width, height unchanged.
    const Vec3 left = place_point(cv, tile, curved, 240, 570), right = place_point(cv, tile, curved, 1200, 570);
    CHECK(left.z > -1 && near(left.z, right.z, 1e-6f) && near(left.x - c0.x, -(right.x - c0.x), 1e-6f));
    const float tw = 960 * px_world;
    CHECK(near(right.x - left.x, 2 * (tw / half_pi) * std::sin(half_pi / 2), 1e-5f));  // chord of a 90 degree arc
    auto mesh = tile_mesh(cv, tile, curved, tile);
    CHECK(mesh.size() > 4 && mesh.size() % 2 == 0);
    float arc = 0;
    for (std::size_t i = 2; i < mesh.size(); i += 2) arc += dist(mesh[i].world, mesh[i - 2].world);
    CHECK(near(arc, tw, tw * 2e-3f));
    CHECK(near(dist(mesh[0].world, mesh[1].world), 540 * px_world, 1e-5f));  // vertical edges stay straight and full height
    for (std::size_t i = 2; i < mesh.size(); i += 2) CHECK(mesh[i].u > mesh[i - 2].u);  // texture runs monotonically along the arc
    // A frame/halo rect past the tile continues on the same cylinder.
    const Vec3 beyond = place_point(cv, tile, curved, 1240, 570);
    CHECK(beyond.z > right.z && beyond.x > right.x);
    // Position: offsets are fractions of the desktop's own size; the centre moves, the shape does not.
    Placement moved; moved.x = 0.5f; moved.y = -0.25f;
    const Vec3 m0 = place_point(cv, tile, moved, 720, 570);
    CHECK(close3(m0, Vec3{c0.x + 0.5f * tw, c0.y - 0.25f * 540 * px_world, -1}));
    CHECK(near(dist(place_point(cv, tile, moved, 240, 300), place_point(cv, tile, moved, 1200, 840)),
               dist(plane(240, 300), plane(1200, 840)), 1e-5f));
    // Distance: twice as far along the same direction, same physical size -> half the apparent size.
    Placement far; far.distance = 2;
    const Vec3 f0 = place_point(cv, tile, far, 720, 570), fr = place_point(cv, tile, far, 1200, 570);
    CHECK(close3(f0, Vec3{2 * c0.x, 2 * c0.y, -2}));
    CHECK(near((fr.x / -fr.z) - (f0.x / -f0.z), 0.5f * ((plane(1200, 570).x) - c0.x), 1e-5f));
    // Yaw > 0 faces right: the left edge comes toward you, the right edge goes away; size is preserved.
    Placement yawed; yawed.yaw = float(M_PI) / 6;
    const Vec3 yl = place_point(cv, tile, yawed, 240, 570), yr = place_point(cv, tile, yawed, 1200, 570);
    CHECK(yl.z > -1 && yr.z < -1 && near(dist(yl, yr), tw, 1e-5f) && close3(place_point(cv, tile, yawed, 720, 570), c0));
    // Pitch > 0 tilts the top edge toward you.
    Placement pitched; pitched.pitch = float(M_PI) / 8;
    CHECK(place_point(cv, tile, pitched, 720, 300).z > -1 && place_point(cv, tile, pitched, 720, 840).z < -1);
    // Roll > 0 is counter-clockwise: the right edge rises; still in the plane.
    Placement rolled; rolled.roll = float(M_PI) / 12;
    const Vec3 rr = place_point(cv, tile, rolled, 1200, 570);
    CHECK(rr.y > c0.y && near(rr.z, -1, 1e-6f) && near(dist(rr, c0), tw / 2, 1e-5f));
    // Combined placement: every corner keeps its distance from the centre (rigid motion).
    Placement all{0.2f, 0.1f, 1.3f, 0.4f, -0.2f, 0.1f, 0};
    const Vec3 ac = place_point(cv, tile, all, 720, 570);
    CHECK(near(dist(place_point(cv, tile, all, 240, 300), ac), dist(plane(240, 300), c0), 1e-5f));
    // Zooming into a desktop fades its placement to flat-in-front, keeping curvature; neighbours unaffected.
    CHECK(near(zoom_weight(1, 1, 1), 1, 1e-6f) && near(zoom_weight(1, 1, 0), 0, 1e-6f) && near(zoom_weight(0, 1, 1), 0, 1e-6f));
    CHECK(near(zoom_weight(1, 1.25f, 1), 0.75f, 1e-6f) && near(zoom_weight(0.5f, 1, 1), 0.5f, 1e-6f));
    Placement mixed{0.4f, -0.2f, 1.6f, 0.3f, 0.2f, -0.1f, 1.f};
    const Placement gone = faded(mixed, 1), half = faded(mixed, 0.5f), kept = faded(mixed, 0);
    CHECK(gone.x == 0 && gone.y == 0 && gone.distance == 1 && gone.yaw == 0 && gone.pitch == 0 && gone.roll == 0 && gone.curvature == 1.f);
    CHECK(near(half.x, 0.2f, 1e-6f) && near(half.distance, 1.3f, 1e-6f) && near(half.yaw, 0.15f, 1e-6f) && half.curvature == 1.f);
    CHECK(kept.x == mixed.x && kept.distance == mixed.distance && kept.roll == mixed.roll);
    // Draw order: farthest first; equal-distance ties keep layout order; missing placements are flat.
    const FRect row3[] = {{0, 330, 600, 540}, {660, 330, 600, 540}, {1320, 330, 600, 540}};
    const Placement pushed[] = {{}, {0, 0, 1.8f, 0, 0, 0, 0}};
    auto order = draw_order(cv, row3, pushed);
    CHECK(order.size() == 3 && order[0] == 1);
    const FRect same[] = {{660, 330, 600, 540}, {660, 330, 600, 540}};
    auto tied = draw_order(cv, same, std::span<const Placement>{});
    CHECK(tied[0] == 0 && tied[1] == 1);
    // Degenerate input yields nothing rather than NaNs.
    CHECK(tile_mesh(cv, {0, 0, 0, 10}, {}, tile).empty() && tile_mesh(cv, tile, {}, {0, 0, 0, 0}).empty());
    for (const auto& m : tile_mesh(cv, tile, Placement{0, 0, 1, 0, 0, 0, float(M_PI) * 2 / 3}, {190, 250, 1060, 640}))
        CHECK(std::isfinite(m.world.x) && std::isfinite(m.world.y) && std::isfinite(m.world.z) && m.world.z < 0);
    }
    std::cout << "view layout passed\n";
}
