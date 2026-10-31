#include "xr/capture.hpp"
#include <iostream>
#include <stdexcept>
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
using namespace xr;

int main() {
    auto ok = parse_capture_list("XR-1,XR-2,XR-3", "DP-2");
    CHECK(ok && *ok == std::vector<std::string>({"XR-1", "XR-2", "XR-3"}));
    CHECK(parse_capture_list("XR-2", "DP-2") == std::vector<std::string>{"XR-2"});
    for (const char* bad : {"", "XR-1,", ",XR-1", "XR-1,XR-1", "XR-4", "XR-0", "XR-01", "DP-1", "DP-2", "XR-1,DP-1",
                            "XR-1 ,XR-2", "XR-1,XR-2,XR-3,XR-1", "xr-1", "XR-SPIKE-run-0", "XR-1;XR-2"})
        CHECK(!parse_capture_list(bad, "DP-2"));
    CHECK(!parse_capture_list("XR-1", "XR-1"));  // never capture our own output

    auto plan = plan_capture_buffer(1920, 1080, {kShmArgb8888, kShmXrgb8888});
    CHECK(plan && plan->format == kShmXrgb8888 && plan->stride == 7680 && plan->bytes == 7680u * 1080);
    CHECK(plan_capture_buffer(1920, 1080, {kShmArgb8888})->format == kShmArgb8888);
    CHECK(!plan_capture_buffer(1920, 1080, {0x34325258}));  // other formats (e.g. XBGR) refused
    CHECK(!plan_capture_buffer(0, 1080, {kShmXrgb8888}) && !plan_capture_buffer(8193, 1080, {kShmXrgb8888}));

    for (int order = 0; order < 2; ++order) {  // formats before size (Hyprland) and after
        SessionConstraints c;
        if (order == 0) { c.format(kShmArgb8888); c.format(kShmXrgb8888); c.size(1920, 1080); }
        else { c.size(1920, 1080); c.format(kShmXrgb8888); }
        auto p = c.done();
        CHECK(p && p->width == 1920 && p->format == kShmXrgb8888);
        CHECK(!c.done());  // a new batch starts empty
    }

    // Zero-copy choice: XRGB before ARGB, LINEAR preferred, only what EGL can import.
    auto all = [](std::uint32_t, std::uint64_t) { return true; };
    const std::uint64_t tiled = 0x0200000020867b04ULL;
    CHECK(choose_dmabuf({{kDrmXrgb8888, {tiled, kModLinear}}}, all)->modifier == kModLinear);
    CHECK(choose_dmabuf({{kDrmXrgb8888, {tiled}}}, all)->modifier == tiled);
    CHECK(choose_dmabuf({{kDrmArgb8888, {kModLinear}}, {kDrmXrgb8888, {tiled}}}, all)->fourcc == kDrmXrgb8888);
    auto linear_only = [](std::uint32_t, std::uint64_t m) { return m == kModLinear; };
    CHECK(!choose_dmabuf({{kDrmXrgb8888, {tiled}}}, linear_only));
    CHECK(!choose_dmabuf({{0x30334241, {kModLinear}}}, all));  // other formats refused
    SessionConstraints z;
    z.format(kShmXrgb8888); z.dmabuf_device(0xe280); z.dmabuf_format(kDrmXrgb8888, {kModLinear}); z.dmabuf_format(kDrmXrgb8888, {tiled});
    z.size(1920, 1080);
    auto batch = z.done_all();
    CHECK(batch.shm && batch.device == 0xe280u && batch.dmabuf.size() == 1 && batch.dmabuf[0].modifiers.size() == 2);
    CHECK(!z.done_all().device);  // reset after a complete batch

    auto tiles = side_by_side(3, 1920, 1200, 1920, 1080);
    CHECK(tiles.size() == 3 && tiles[0].width == 640 && tiles[0].height == 360);
    CHECK(tiles[0].x == 0 && tiles[1].x == 640 && tiles[2].x == 1280 && tiles[0].y == 420);
    CHECK(side_by_side(1, 1920, 1200, 1920, 1080)[0].width == 1920);
    CHECK(side_by_side(0, 1920, 1200, 1920, 1080).empty() && side_by_side(2, 0, 10, 1, 1).empty());

    auto q = clip_quad({0, 0, 1920, 1200}, 1920, 1200);
    CHECK(q.x0 == -1.f && q.x1 == 1.f && q.y0 == 1.f && q.y1 == -1.f);
    auto half = clip_quad({960, 600, 960, 600}, 1920, 1200);
    CHECK(half.x0 == 0.f && half.x1 == 1.f && half.y0 == 0.f && half.y1 == -1.f);
    CHECK(clip_quad({0, 0, 1, 1}, 0, 0).x0 == 0.f);  // degenerate viewport stays finite

    std::vector<std::uint32_t> src = {0x11000001, 0x22000002, 0x33000003, 0x44000004};  // 2x2
    std::vector<std::uint32_t> dst(16, 0);                                                // 4x4
    blit_nearest(src.data(), 2, 2, 2, dst.data(), 4, 4, 4, {0, 0, 4, 4});
    CHECK(dst[0] == 0xff000001 && dst[3] == 0xff000002 && dst[12] == 0xff000003 && dst[15] == 0xff000004);
    std::vector<std::uint32_t> clip(4, 7);
    blit_nearest(src.data(), 2, 2, 2, clip.data(), 2, 2, 2, {1, 1, 4, 4});  // clipped, no overrun
    CHECK(clip[0] == 7 && clip[1] == 7 && clip[2] == 7 && clip[3] == 0xff000001);
    std::cout << "capture helpers passed\n";
}
