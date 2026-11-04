// DepthFeed on a real GPU, off screen: a dmabuf made here (no compositor involved) goes in, the right shrunk picture
// comes out, the right way up, without collect() ever waiting; the result then fits the depth contract.
#include "depth_feed.hpp"
#include "xr/depth.hpp"
#include <EGL/eglext.h>
#include <chrono>
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <gbm.h>
#include <string>
#include <thread>
#include <unistd.h>

using namespace xr;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); std::exit(1); } } while (0)

int main() {
    // On this machine renderD128 is the RX 7800 XT that drives the glasses, so the iGPU's node is tried first.
    const char* node = std::getenv("XR_DEPTH_TEST_NODE");
    int fd = open(node ? node : "/dev/dri/renderD129", O_RDWR | O_CLOEXEC);
    if (fd < 0 && !node) fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (fd < 0) { std::puts("skip: no render node"); return 0; }
    gbm_device* gbm = gbm_create_device(fd);
    CHECK(gbm);
    auto platform = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
    EGLDisplay display = platform(EGL_PLATFORM_GBM_KHR, gbm, nullptr);
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, nullptr, nullptr)) { std::puts("skip: no EGL"); return 0; }
    eglBindAPI(EGL_OPENGL_ES_API);
    const EGLint attrs[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 2, EGL_NONE};
    EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attrs);
    CHECK(context != EGL_NO_CONTEXT && eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context));

    // A 1920x1200 desktop: top half red, bottom half blue, a green band at the left, and a white line 2 px tall.
    constexpr int W = 1920, H = 1200;
    const std::array<std::uint64_t, 1> linear{0};  // Linear, so the test can write it from the CPU.
    gbm_bo* bo = gbm_bo_create_with_modifiers2(gbm, W, H, GBM_FORMAT_XRGB8888, linear.data(), 1, GBM_BO_USE_RENDERING);
    if (!bo) bo = gbm_bo_create(gbm, W, H, GBM_FORMAT_XRGB8888, GBM_BO_USE_LINEAR | GBM_BO_USE_RENDERING);
    CHECK(bo);
    std::uint32_t stride = 0;
    void* map_data = nullptr;
    auto* px = static_cast<std::uint8_t*>(gbm_bo_map(bo, 0, 0, W, H, GBM_BO_TRANSFER_WRITE, &stride, &map_data));
    CHECK(px);
    for (int y = 0; y < H; ++y) {
        auto* row = reinterpret_cast<std::uint32_t*>(px + y * stride);
        for (int x = 0; x < W; ++x) row[x] = x < 200 ? 0xff00ff00u : (y == 301 || y == 302) ? 0xffffffffu : y < H / 2 ? 0xffff0000u : 0xff0000ffu;
    }
    gbm_bo_unmap(bo, map_data);
    const int dmabuf = gbm_bo_get_fd(bo);
    CHECK(dmabuf >= 0);

    const DepthSize size = depth_input_size(W, H, kDepthLongHigh);
    DepthFeed feed;
    CHECK(feed.init(size.w, size.h));
    const DepthDmabuf buffer{dmabuf, W, H, GBM_FORMAT_XRGB8888, gbm_bo_get_stride(bo), gbm_bo_get_offset(bo, 0), gbm_bo_get_modifier(bo)};
    std::vector<std::byte> rgba;
    CHECK(!feed.collect(rgba));          // Nothing queued.
    const auto start = std::chrono::steady_clock::now();
    CHECK(feed.begin_dmabuf(display, buffer));
    CHECK(feed.busy() && !feed.begin_dmabuf(display, buffer));  // One at a time.
    long polls = 0;
    while (!feed.collect(rgba)) {
        CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(3));
        ++polls;
        std::this_thread::sleep_for(std::chrono::microseconds(200));
    }
    CHECK(!feed.busy() && rgba.size() == static_cast<std::size_t>(size.w) * size.h * 4);
    auto at = [&](int x, int y, int c) { return static_cast<int>(rgba[(static_cast<std::size_t>(y) * size.w + x) * 4 + c]); };
    // RGBA order, and the top of the desktop in row 0.
    CHECK(at(size.w - 20, 20, 0) > 240 && at(size.w - 20, 20, 2) < 15);
    CHECK(at(size.w - 20, size.h - 20, 2) > 240 && at(size.w - 20, size.h - 20, 0) < 15);
    CHECK(at(10, 20, 1) > 240 && at(10, 20, 0) < 15);
    // The shrink averages: a line thinner than one output pixel survives, dimmed. One tap would lose it or keep it whole.
    int line = 0;
    for (int y = 70; y < 90; ++y) line = std::max(line, at(size.w - 20, y, 2));
    CHECK(line > 40 && line < 200);

    // A second frame on the same objects, and the result fits the contract.
    CHECK(feed.begin_dmabuf(display, buffer));
    while (!feed.collect(rgba)) CHECK(std::chrono::steady_clock::now() - start < std::chrono::seconds(3));
    DepthShmConfig cfg{1, size.w, size.h, size.w, size.h};
    std::vector<std::byte> mem(depth_shm_bytes(cfg)), out;
    auto shm = *DepthShm::create(mem.data(), mem.size(), cfg);
    CHECK(shm.publish(DepthShm::Side::Input, 0, {size.w, size.h, 1, 1, 0, DepthFormat::Rgba8, 0, rgba}));
    CHECK(shm.fetch(DepthShm::Side::Input, 0, out)->w == size.w);

    close(dmabuf);
    gbm_bo_destroy(bo);
    std::printf("depth feed ok on %s, modifier %llx, %ld polls\n", glGetString(GL_RENDERER), static_cast<unsigned long long>(buffer.modifier), polls);
}
