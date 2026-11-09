#include "options_overlay.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;

int main() {
    CHECK(kHelpFadeInMs == kGlassExpandMs && kHelpFadeOutMs == kGlassCollapseMs);
    CHECK(kMenuFadeInMs == 180 && kMenuFadeOutMs == 120);
    OptionsOverlay overlay;
    OptionsMenuState state;
    CHECK(overlay.frame(true, 0) == 0 && !overlay.needs_frame(true, false, 0));
    CHECK(!overlay.set(state, 0));
    state.open = true; state.monitor = "XR-2"; state.scope = "monitor"; state.category = "display";
    CHECK(overlay.set(state, 100) && overlay.frame(true, 100) == 0);
    CHECK(overlay.needs_frame(true, false, 100));
    CHECK(std::fabs(overlay.frame(true, 190) - 0.875f) < 1e-5f);
    CHECK(overlay.frame(true, 280) == 1 && !overlay.needs_frame(true, true, 280));
    CHECK(overlay.image().width == 600 && overlay.renders() == 1);
    const auto first_generation = overlay.generation();
    CHECK(!overlay.set(state, 300) && overlay.generation() == first_generation);
    // A burst of accepted navigation/value changes renders only its final snapshot.
    for (int i = 1; i <= 20; ++i) { state.scroll = i; CHECK(overlay.set(state, 300)); }
    CHECK(overlay.renders() == 1 && overlay.frame(true, 300) == 1);
    const auto pixels = overlay.image().rgba;
    CHECK(overlay.renders() == 2);
    const auto closing_generation = overlay.generation();
    // Close payloads may omit all content: fade the previous snapshot, without re-rendering a blank menu.
    CHECK(overlay.set(OptionsMenuState{}, 400));
    CHECK(overlay.frame(true, 400) == 1 && overlay.needs_frame(true, true, 400));
    CHECK(overlay.state().monitor == "XR-2" && overlay.state().scroll == 20);
    CHECK(overlay.image().rgba == pixels && overlay.generation() == closing_generation && overlay.renders() == 2);
    const float mid = overlay.frame(true, 460);
    CHECK(std::fabs(mid - 0.125f) < 1e-5f);
    CHECK(overlay.set(state, 460) && overlay.frame(true, 460) == mid);
    CHECK(overlay.frame(true, 550) == 1 && !overlay.needs_frame(true, true, 550));
    CHECK(overlay.set(OptionsMenuState{}, 600));
    const float before_theme = overlay.frame(true, 610);
    CHECK(overlay.set_accent({0.8f, 0.2f, 0.1f}));
    CHECK(overlay.frame(true, 610) == before_theme && overlay.image().rgba != pixels);
    CHECK(overlay.renders() == 3);
    CHECK(overlay.frame(true, 720) == 0);
    CHECK(overlay.needs_frame(true, true, 720));  // one final clearing frame
    CHECK(!overlay.needs_frame(true, false, 720));
    // Opening from maximized waits for the overview, even after a long gap with no frames.
    OptionsOverlay hidden;
    hidden.frame(false, 0);
    CHECK(hidden.set(state, 5000));
    for (double t = 5010; t <= 6000; t += 10) CHECK(hidden.frame(false, t) == 0);
    CHECK(!hidden.needs_frame(false, false, 6000));
    CHECK(hidden.frame(true, 6000) == 0);
    CHECK(std::fabs(hidden.frame(true, 6090) - 0.875f) < 1e-5f);
    CHECK(hidden.frame(true, 6180) == 1 && !hidden.needs_frame(true, true, 6180));
    CHECK(hidden.needs_frame(false, true, 6200)); // clear a menu when the view leaves the overview
    std::cout << "options overlay: animation, reversal, cache, close and idle cadence passed\n";
}
