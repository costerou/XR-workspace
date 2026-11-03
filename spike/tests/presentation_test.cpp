#include "xr/presentation.hpp"
#include "../bench/reference_pixels.hpp"
#include <initializer_list>
#include <iostream>
#include <vector>

namespace {
struct Requests : xr::SurfaceRequests {
    std::vector<int> keyboard, exclusive;
    int empty_regions = 0, commits = 0;
    void keyboard_interactivity(std::uint32_t value) override { keyboard.push_back(static_cast<int>(value)); }
    void exclusive_zone(std::int32_t value) override { exclusive.push_back(value); }
    void empty_input_region() override { ++empty_regions; }
    void commit() override { ++commits; }
};
int failures = 0;
void check(bool ok, const char* name) { if (!ok) { std::cerr << name << '\n'; ++failures; } }
}
int main() {
    check(xr::parse_pattern_duration("1") == 1 && xr::parse_pattern_duration("60000") == 60000, "finite duration bounds");
    for (const auto* bad : {"0", "60001", "-1", "01", "+1", "1.0", "inf", "", "999999999999999"})
        check(!xr::parse_pattern_duration(bad), "reject invalid duration");
    xr::PatternCadence still;
    check(still.ready(0, true, true), "static initial commit");
    still.committed(); still.frame_done();
    check(!still.ready(100, true, true), "static callback does not redraw");
    still.frame_done();
    check(!still.ready(100, true, true), "duplicate callback does not grant redraw");
    still.invalidate(); check(still.ready(100, true, true), "static configure redraw");
    xr::PatternCadence animated(1000, 10);
    check(animated.ready(10, true, true), "animated initial commit");
    animated.committed(); animated.invalidate();
    check(!animated.ready(11, true, true), "no submission without frame callback");
    animated.frame_done();
    check(!animated.ready(12, false, true), "busy buffers backpressure");
    check(!animated.ready(12, true, false), "feedback backpressure");
    check(animated.ready(13, true, true), "release resumes pending callback draw");
    animated.committed(); animated.frame_done();
    check(animated.ready(1009, true, true) && animated.finished(1010), "finite deadline independent of callbacks");
    check(!animated.ready(1010, true, true), "deadline stops submissions");
    animated.stop(); animated.stop(); animated.frame_done(); animated.invalidate();
    check(!animated.ready(15, true, true), "cancellation ignores late callbacks");
    check(xr::animated_pattern_pixel(0, 0, 300, 100, 1) != xr::pattern_pixel(0, 0, 300, 100), "generated animation changes pixels");
    {  // the row-template frame equals animated_pattern_pixel at every pixel (bench/reference_pixels.hpp), padding untouched
        struct Case { std::uint32_t w, h; std::size_t pad; };
        const Case cases[] = {{1, 1, 0}, {1, 65, 1}, {65, 1, 0}, {2, 2, 3}, {31, 64, 0}, {64, 64, 0}, {65, 65, 2}, {130, 129, 0},
                              {300, 100, 0}, {97, 200, 5}, {1920, 1200, 0}, {1920, 1080, 16}, {8192, 2, 0}, {0, 10, 0}, {10, 0, 0},
                              {8193, 2, 0}, {33, 3, 1}};  // the last ones: empty and over-wide (per-pixel fallback)
        bool same = true;
        for (const auto& c : cases)
            for (const std::uint64_t phase : std::initializer_list<std::uint64_t>{0, 1, 7, 63, c.w, std::uint64_t(c.w) * 3 + 5, 12345678901}) {
                const std::size_t stride = std::size_t(c.w) + c.pad;
                std::vector<std::uint32_t> a(stride * c.h + 1, 0xdeadbeefU), b = a;
                xr::reference::render_animated_pattern(a.data(), c.w, c.h, stride, phase);
                xr::render_animated_pattern(b.data(), c.w, c.h, stride, phase);
                same = same && a == b;
            }
        check(same, "render_animated_pattern matches animated_pattern_pixel everywhere");
    }
    Requests requests;
    xr::apply_presentation_policy(requests);
    check(requests.keyboard == std::vector<int>{0}, "test_presentation_never_requests_keyboard");
    check(requests.exclusive == std::vector<int>{-1}, "exclusive zone must be -1 (cover the bar's reserved area)");
    check(requests.empty_regions == 1 && requests.commits == 1, "empty input region before first commit");
    check(xr::layer_version(9) == 4 && xr::layer_version(2) == 2 && xr::layer_version(0) == 0, "version never exceeds pinned XML v4");
    check(xr::pattern_pixel(0, 0, 300, 100) == 0xffff3030U, "left red desktop");
    check(xr::pattern_pixel(150, 0, 300, 100) == 0xff30ff30U, "middle green desktop");
    check(xr::pattern_pixel(299, 0, 300, 100) == 0xff3030ffU, "right blue desktop");
    check(!xr::valid_surface_size(0, 1080) && !xr::valid_surface_size(8192, 8192), "bounded allocation");
    check(xr::valid_surface_size(1920, 1080), "1080p surface");
    using Px = std::array<std::uint32_t, 2>;
    check(xr::scaled_surface_size(1536, 960, 150) == Px{1920, 1200}, "glasses at 1.25: native 1920x1200 buffer");
    check(xr::scaled_surface_size(1536, 960, 0) == Px{1536, 960}, "no fractional scale: logical size");
    check(xr::scaled_surface_size(1920, 1080, 120) == Px{1920, 1080}, "scale 1: unchanged");
    check(xr::scaled_surface_size(1366, 768, 180) == Px{2049, 1152}, "1.5: rounded half up");
    check(xr::scaled_surface_size(4096, 4096, 240) == Px{4096, 4096}, "over the allocation bound: unscaled");
    xr::Presentation presentation;
    check(std::holds_alternative<xr::Error>(presentation.open("../unsafe")), "invalid target rejected before connect");
    check(std::holds_alternative<xr::Error>(presentation.draw_test_pattern()), "draw before configure rejected");
    check(std::holds_alternative<xr::Error>(presentation.animate_pattern(1000)), "animation before open rejected offline");
    presentation.close(); presentation.close();
    return failures ? 1 : 0;
}
