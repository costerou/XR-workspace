#include "xr/presentation.hpp"
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
    Requests requests;
    xr::apply_presentation_policy(requests);
    check(requests.keyboard == std::vector<int>{0}, "test_presentation_never_requests_keyboard");
    check(requests.exclusive == std::vector<int>{0}, "exclusive zone must be zero");
    check(requests.empty_regions == 1 && requests.commits == 1, "empty input region before first commit");
    check(xr::layer_version(9) == 4 && xr::layer_version(2) == 2 && xr::layer_version(0) == 0, "version never exceeds pinned XML v4");
    check(xr::pattern_pixel(0, 0, 300, 100) == 0xffff3030U, "left red desktop");
    check(xr::pattern_pixel(150, 0, 300, 100) == 0xff30ff30U, "middle green desktop");
    check(xr::pattern_pixel(299, 0, 300, 100) == 0xff3030ffU, "right blue desktop");
    check(!xr::valid_surface_size(0, 1080) && !xr::valid_surface_size(8192, 8192), "bounded allocation");
    check(xr::valid_surface_size(1920, 1080), "1080p surface");
    xr::Presentation presentation;
    check(std::holds_alternative<xr::Error>(presentation.open("../unsafe")), "invalid target rejected before connect");
    check(std::holds_alternative<xr::Error>(presentation.draw_test_pattern()), "draw before configure rejected");
    presentation.close(); presentation.close();
    return failures ? 1 : 0;
}
