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
    CHECK(a.selected() == kMaxDesktops - 1 && a.spacing() == float(kMaxSpacing));

    // Any desktop count: one desktop fills the view; six stay in one row inside it.
    const Size one[] = {{1920, 1080}};
    auto single = overview_layout(one, 1920, 1200, 40);
    CHECK(single.size() == 1 && near(single[0].x + single[0].width / 2, 960) && single[0].width <= 1920);
    auto single_zoom = zoomed_layout(one, 1920, 1200, 40, 0);
    CHECK(single_zoom.size() == 1 && near(single_zoom[0].x, 0) && near(single_zoom[0].width, 1920));
    const Size many[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}, {1920, 1080}, {1920, 1080}, {1920, 1080}};
    auto row6 = overview_layout(many, 1920, 1200, 40);
    CHECK(row6.size() == 6 && row6[0].x >= 0 && row6[5].x + row6[5].width <= 1920.5f);
    for (std::size_t i = 1; i < row6.size(); ++i) CHECK(near(row6[i].x - (row6[i - 1].x + row6[i - 1].width), 40));
    auto zoom6 = zoomed_layout(many, 1920, 1200, 40, 5);
    CHECK(near(zoom6[5].x, 0) && near(zoom6[5].width, 1920) && near(zoom6[4].x, -1960));
    ViewAnimator last6(200, 200);
    last6.set_target(true, 5, 40, 0); last6.advance(1000);
    CHECK(last6.selected() == 5 && last6.position() == 5);

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
    std::cout << "view layout passed\n";
}
