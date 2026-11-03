#include "xr/capture.hpp"
#include "../bench/reference_pixels.hpp"
#include <cmath>
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

    {  // Capture buffer lifecycle under constraint churn (Hyprland layout changes, config reloads)
        const ShmPlan p1080{1920, 1080, 7680, kShmXrgb8888, 7680u * 1080}, p1200{1920, 1200, 7680, kShmXrgb8888, 7680u * 1200};
        const ShmPlan p720{1280, 720, 5120, kShmXrgb8888, 5120u * 720}, argb{1920, 1080, 7680, kShmArgb8888, 7680u * 1080};
        CaptureBuffers b;
        CHECK(!b.may_capture() && !b.reallocation_pending() && !b.layout_size() && !b.settle(false));
        b.constraints(p1080);
        CHECK(b.target() && !b.allocated() && b.reallocation_pending() && !b.may_capture());
        CHECK(b.layout_size() && b.layout_size()->w == 1920 && b.layout_size()->h == 1080);  // before any frame: the plan
        CHECK(b.settle(false) && b.allocated()->height == 1080 && b.reallocations() == 1 && b.may_capture());
        CHECK(!b.settle(false));  // nothing more to do
        b.delivered();
        // An unchanged batch (a mode event that changes nothing) keeps the buffers.
        b.constraints(p1080);
        CHECK(!b.reallocation_pending() && b.may_capture() && !b.settle(false) && b.reallocations() == 1);
        // Reload: the default size and straight back in one dispatch re-creates nothing.
        b.constraints(p720);
        CHECK(b.reallocation_pending() && !b.may_capture());  // no frame into buffers that are about to go
        b.constraints(p1080);
        CHECK(!b.reallocation_pending() && b.may_capture() && !b.settle(false) && b.reallocations() == 1 && b.batches() == 4);
        // A real change waits for the frame in flight, then re-creates once; the layout keeps the size on screen
        // until a frame lands in the new buffers.
        b.constraints(p1200);
        CHECK(!b.settle(true) && b.reallocation_pending());
        CHECK(b.layout_size()->h == 1080);
        CHECK(b.settle(false) && b.reallocations() == 2 && b.allocated()->height == 1200 && b.may_capture());
        CHECK(b.layout_size()->h == 1080);  // the held 1080 frame is still what is shown
        b.delivered();
        CHECK(b.layout_size()->h == 1200);
        // A format change alone re-creates too.
        b.constraints(argb);
        CHECK(b.reallocation_pending() && b.settle(false) && b.allocated()->format == kShmArgb8888 && b.reallocations() == 3);
        b.delivered();
        // A buffer-constraints failure keeps the buffers and their plan, waits for the next batch, and an identical batch
        // resumes capture into the same buffers.
        b.constraints_failed();
        CHECK(!b.may_capture() && b.allocated() && !b.reallocation_pending() && !b.settle(false));
        CHECK(b.layout_size()->w == 1920 && b.layout_size()->h == 1080);  // no placeholder size while waiting
        b.constraints(argb);
        CHECK(b.may_capture() && b.reallocations() == 3);
        b.constraints_failed();
        b.constraints(p1200);  // or a different one re-creates
        CHECK(b.reallocation_pending() && b.settle(false) && b.reallocations() == 4 && b.may_capture());
        CHECK(CaptureBuffers::same_buffers(p1080, p1080) && !CaptureBuffers::same_buffers(p1080, argb) && !CaptureBuffers::same_buffers(p1080, p1200));
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

    const Size eq[] = {{1920, 1080}, {1920, 1080}, {1920, 1080}};
    auto tiles = side_by_side(eq, 1920, 1200);
    CHECK(tiles.size() == 3 && tiles[0].width == 640 && tiles[0].height == 360);
    CHECK(tiles[0].x == 0 && tiles[1].x == 640 && tiles[2].x == 1280 && tiles[0].y == 420);
    CHECK(side_by_side(std::span(eq, 1), 1920, 1200)[0].width == 1920);
    const Size unit[] = {{1, 1}, {1, 1}}, bad[] = {{1920, 1080}, {1920, 0}};
    CHECK(side_by_side(std::span<const Size>{}, 1920, 1200).empty() && side_by_side(unit, 0, 10).empty() && side_by_side(bad, 1920, 1200).empty());
    // side_by_side mixed: one shared height, no overlap, aspect within 1 px, inside dst.
    const Size mixed[] = {{1920, 1080}, {3840, 1080}, {3440, 1440}};
    auto m = side_by_side(mixed, 1920, 1200);
    CHECK(m.size() == 3 && m[0].height > 0);
    for (int i = 0; i < 3; ++i) {
        CHECK(m[i].height == m[0].height && m[i].x + m[i].width <= 1920 && m[i].y + m[i].height <= 1200);
        CHECK(std::fabs(float(m[i].width) - float(m[i].height) * mixed[i].w / mixed[i].h) <= 1.f);
        if (i) CHECK(m[i - 1].x + m[i - 1].width <= m[i].x);
    }

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

    // The divide-free blit_nearest and the single-write compose_tiles give exactly the previous pixels
    // (bench/reference_pixels.hpp: per-pixel division; fill everything, then blit).
    std::uint64_t seed = 0x9e3779b97f4a7c15ull;
    auto rnd = [&](std::uint32_t n) { seed = seed * 6364136223846793005ull + 1442695040888963407ull; return n ? std::uint32_t((seed >> 33) % n) : 0u; };
    auto noise = [&](std::size_t n) { std::vector<std::uint32_t> v(n); for (auto& p : v) p = rnd(0xffffffffu) ^ (rnd(2) << 31); return v; };
    for (int trial = 0; trial < 4000; ++trial) {  // small sizes: empty, clipped, off-surface, up- and downscaled rects, padded strides
        const std::uint32_t sw = 1 + rnd(40), sh = 1 + rnd(40), sstride = sw + rnd(3), dw = 1 + rnd(60), dh = 1 + rnd(60), dstride = dw + rnd(3);
        const Rect r{rnd(dw + 10), rnd(dh + 10), rnd(90), rnd(90)};
        const auto pixels = noise(std::size_t(sstride) * sh);
        std::vector<std::uint32_t> a(std::size_t(dstride) * dh, 0x12345678u), b = a;
        reference::blit_nearest(pixels.data(), sw, sh, sstride, a.data(), dw, dh, dstride, r);
        blit_nearest(pixels.data(), sw, sh, sstride, b.data(), dw, dh, dstride, r);
        CHECK(a == b);
    }
    blit_nearest(nullptr, 2, 2, 2, clip.data(), 2, 2, 2, {0, 0, 2, 2});  // no source: nothing written
    {  // uint32 edges: rect sizes above 2^31 (the column remainder needs 64 bits), rects ending past UINT32_MAX
        const std::uint32_t big[] = {0x80000001u, 0xF0000000u, 0xFFFFFFFFu, 3u};
        for (const std::uint32_t rw : big) for (const std::uint32_t sw : big) for (const std::uint32_t rx : {0u, 5u, 0xFFFFFFF0u}) {
            const std::uint32_t dw = 37, dh = 3;
            const Rect r{rx, 1, rw, 0xFFFFFFFEu};
            // Only columns floor(i * sw / rw), i < 37, are read: size the source row for those (skip what needs > 4096).
            const std::uint64_t reach = std::uint64_t(dw) * sw / rw + 1;
            if (reach > 4096) continue;
            const auto stride = std::uint32_t(reach);
            const auto pixels = noise(std::size_t(stride) * 2);
            std::vector<std::uint32_t> a(std::size_t(dw) * dh, 9u), b = a;
            reference::blit_nearest(pixels.data(), sw, 2, stride, a.data(), dw, dh, dw, r);
            blit_nearest(pixels.data(), sw, 2, stride, b.data(), dw, dh, dw, r);
            CHECK(a == b);
        }
    }
    CHECK(clip[0] == 7 && clip[3] == 0xff000001);
    for (int trial = 0; trial < 1500; ++trial) {  // compositions: overlapping tiles, missing sources, padded rows, > 8 tiles
        const std::uint32_t dw = 1 + rnd(70), dh = 1 + rnd(50), dstride = dw + rnd(3), count = trial % 50 == 0 ? 9 + rnd(3) : rnd(5);
        std::vector<std::vector<std::uint32_t>> frames;
        std::vector<TileSource> sources;
        std::vector<Rect> tiles;
        for (std::uint32_t i = 0; i < count; ++i) {
            const std::uint32_t sw = 1 + rnd(30), sh = 1 + rnd(30), sstride = sw + rnd(2);
            frames.push_back(noise(std::size_t(sstride) * sh));
            sources.push_back(rnd(4) ? TileSource{frames.back().data(), sw, sh, sstride} : TileSource{});
            switch (rnd(12)) {  // degenerate sources draw nothing: each term of the coverage test on its own
            case 0: sources.back().width = 0; break;
            case 1: sources.back().height = 0; break;
            case 2: sources.back().pixels = nullptr; break;  // null with a size
            default: break;
            }
            tiles.push_back({rnd(dw + 5), rnd(dh + 5), rnd(dw + 8), rnd(dh + 8)});
        }
        if (rnd(3) == 0 && !tiles.empty()) tiles.pop_back();  // fewer tiles than sources
        const std::uint32_t background = rnd(0xffffffffu);
        std::vector<std::uint32_t> a(std::size_t(dstride) * dh, 0x5a5a5a5au), b = a;
        reference::compose_tiles(a.data(), dw, dh, dstride, sources, tiles, background);
        compose_tiles(b.data(), dw, dh, dstride, sources, tiles, background);
        CHECK(a == b);
    }
    {  // renderer-sized frames: 3, 2 (one not captured yet) and 1 desktops at 1920x1080 on a 1920x1200 output
        const auto big = noise(std::size_t(1920) * 1080);
        const TileSource full{big.data(), 1920, 1080, 1920};
        for (int sources_n : {3, 2, 1}) {
            std::vector<Size> sizes(std::size_t(sources_n), Size{1920, 1080});
            const auto layout = side_by_side(sizes, 1920, 1200);
            std::vector<TileSource> sources(std::size_t(sources_n), full);
            if (sources_n == 2) sources[1] = {};
            std::vector<std::uint32_t> a(std::size_t(1920) * 1200, 1u), b(std::size_t(1920) * 1200, 2u);
            reference::compose_tiles(a.data(), 1920, 1200, 1920, sources, layout, 0xff000000u);
            compose_tiles(b.data(), 1920, 1200, 1920, sources, layout, 0xff000000u);
            CHECK(a == b);
        }
        const Size up[] = {{960, 540}};  // vertical and horizontal upscale (row reuse)
        const auto layout = side_by_side(up, 1920, 1200);
        const TileSource half{big.data(), 960, 540, 1920};
        std::vector<std::uint32_t> a(std::size_t(1920) * 1200, 1u), b(std::size_t(1920) * 1200, 2u);
        reference::compose_tiles(a.data(), 1920, 1200, 1920, std::span(&half, 1), layout, 0xff000000u);
        compose_tiles(b.data(), 1920, 1200, 1920, std::span(&half, 1), layout, 0xff000000u);
        CHECK(a == b);
    }
    std::cout << "capture helpers passed\n";
}
