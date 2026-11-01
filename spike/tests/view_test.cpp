#include "xr/view.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;
bool near(float a, float b, float eps = 0.5f) { return std::fabs(a - b) <= eps; }

int main() {
    // Overview: 3 x 16:9 desktops in 1920x1200 with 40 px gaps and margins.
    auto o = overview_layout(3, 1920, 1200, 1920, 1080, 40);
    CHECK(o.size() == 3);
    const float w = (1920.f - 4 * 40.f) / 3.f;
    CHECK(near(o[0].x, 40) && near(o[0].width, w) && near(o[0].height, w * 1080.f / 1920.f));
    CHECK(near(o[1].x, 40 + w + 40) && near(o[2].x + o[2].width, 1880));
    CHECK(near(o[0].y, (1200 - o[0].height) / 2));
    auto wide = overview_layout(3, 1920, 1200, 1920, 1080, 0);
    CHECK(near(wide[0].x, 0) && near(wide[0].width, 640));
    // Spacing is bounded; absurd spacing never yields negative tiles.
    for (auto& r : overview_layout(3, 1920, 1200, 1920, 1080, 5000)) CHECK(r.width >= 0 && r.height >= 0);

    // Zoomed: selected fills (aspect-fit), neighbours offset by one view width plus spacing.
    auto z = zoomed_layout(3, 1920, 1200, 1920, 1080, 40, 1);
    CHECK(near(z[1].x, 0) && near(z[1].width, 1920) && near(z[1].height, 1080) && near(z[1].y, 60));
    CHECK(near(z[0].x, -1960) && near(z[2].x, 1960));
    auto mid = zoomed_layout(3, 1920, 1200, 1920, 1080, 40, 1.5f);  // sliding between 1 and 2
    CHECK(near(mid[1].x, -980) && near(mid[2].x, 980));

    // Blend endpoints and monotone easing.
    auto b0 = blended_layout(3, 1920, 1200, 1920, 1080, 40, 0, 1), b1 = blended_layout(3, 1920, 1200, 1920, 1080, 40, 1, 1);
    CHECK(near(b0[1].x, o[1].x) && near(b1[1].width, 1920));
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
    std::cout << "view layout passed\n";
}
