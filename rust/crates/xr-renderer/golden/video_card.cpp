// Writes golden/video_card.txt from the C++ render_video_card (spike/src/hud.cpp): one line per case and font,
// "<case> <font> <width> <height> <FNV-1a of the RGBA bytes>", font "pixel" (the built-in 5x7 font) or "real"
// (kHudFontFamily, when fontconfig finds it). tests/video_card.rs replays the same cases and must print the same lines.
// From the repository root, after `cmake -S spike -B $B && cmake --build $B --target xr_hud`:
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/video_card.cpp $B/libxr_hud.a $B/libxr_view.a \
//     $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/video-card-golden \
//     && /tmp/video-card-golden > rust/crates/xr-renderer/golden/video_card.txt
#include "xr/hud.hpp"
#include "xr/video.hpp"
#include <cstdio>
#include <string>
#include <vector>
using namespace xr;
struct Case { const char* name; VideoCardState s; std::array<float, 3> accent; };
int main() {
    const VideoFormat sbs180{VideoProjection::equirect180, VideoStereo::sbs, 180};
    const VideoCardState base{"beach_360.mp4", sbs180, VideoState::playing, 75, 300, "", false, {}, 0};
    std::vector<Case> cases;
    cases.push_back({"default", VideoCardState{}, {}});
    const std::pair<const char*, VideoState> states[] = {{"off", VideoState::off}, {"loading", VideoState::loading},
        {"playing", VideoState::playing}, {"paused", VideoState::paused}, {"ended", VideoState::ended}, {"error", VideoState::error}};
    for (const auto& [name, st] : states) {
        auto s = base; s.state = st;
        if (st == VideoState::error) s.detail = "unrecognized file format";
        cases.push_back({name, s, {}});
    }
    { auto s = base; s.position_s = 3723; s.duration_s = 0; cases.push_back({"no_duration", s, {}}); }
    { auto s = base; s.position_s = 4000; s.duration_s = 3723; cases.push_back({"past_end", s, {}}); }
    { auto s = base; s.position_s = 1; s.duration_s = 7200; cases.push_back({"sliver", s, {}}); }
    { auto s = base; s.position_s = 300; cases.push_back({"full", s, {}}); }
    { auto s = base; s.title = "a much longer title for a video that widens the card past its minimum width.mkv";
      s.format = {VideoProjection::fisheye, VideoStereo::tb, 190}; cases.push_back({"long_fisheye", s, {}}); }
    { auto s = base; s.format = {}; cases.push_back({"accent", s, {0.48f, 0.64f, 0.97f}}); }
    auto m = base; m.menu = true; m.menu_rows = {{"Texture filter", "Catmull-Rom"}, {"Deband", "Off"}};
    cases.push_back({"menu0", m, {}});
    m.menu_selected = 1; cases.push_back({"menu1", m, {}});
    m.menu_rows.push_back({"Third", "On"}); cases.push_back({"menu3", m, {0.9f, 0.3f, 0.2f}});
    m.menu_selected = 7; cases.push_back({"menu_none", m, {}});
    m.detail = "network error"; m.state = VideoState::error; cases.push_back({"menu_error", m, {}});
    { auto e = base; e.menu = true; cases.push_back({"menu_empty", e, {}}); }
    std::string why;
    const auto real = HudFont::load(kHudFontFamily, why);
    for (int pass = 0; pass < 2; ++pass) {
        const HudFont* font = pass ? real.get() : nullptr;
        if (pass && !font) break;
        for (const auto& c : cases) {
            const auto im = render_video_card(c.s, c.accent, font);
            std::uint64_t h = 0xcbf29ce484222325ull;
            for (auto b : im.rgba) { h ^= b; h *= 0x100000001b3ull; }
            std::printf("%s %s %u %u %llu\n", c.name, pass ? "real" : "pixel", im.width, im.height, (unsigned long long)h);
        }
    }
}
