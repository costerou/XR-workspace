#include "xr/view.hpp"
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
    ViewAnimator a(200);
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
    std::cout << "view layout passed\n";
}
