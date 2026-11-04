// The renderer's end of the depth engine (src/depth_link.hpp), on a real GPU off screen: the real GlCompositor and DepthLink against
// a stand-in engine in this process, through the real shared memory in a real memfd. What the feature rests on:
//   * a blank memfd gets the layout, and the next renderer of a run takes it over with the engine still attached;
//   * nothing is fed or drawn in 3D before the engine's heartbeat, and a desktop gets depth only while it is asked for;
//   * the picture reaches the engine the right way up and in the right colours, from either kind of texture the compositor keeps;
//   * maps reach the compositor: the eyes differ; a still desktop stays 3D though no new map comes; a crash goes flat, by itself,
//     with every frame still drawn; a restarted engine brings 3D back;
//   * the budget: never more than kDepthPerTick shrinks a frame, however many desktops.
#include "depth_link.hpp"
#include <GLES3/gl3.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)
constexpr Mat3 kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1};
constexpr float kHalfW = 0.44f, kHalfH = 0.275f;
constexpr int W = 640, H = 400;

std::uint64_t now_ns() {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}

// Top half red, bottom half blue on a green stripe texture, as XRGB bytes, so the way up and the colours can both be read.
std::vector<std::uint8_t> picture(int shift = 0) {
    std::vector<std::uint8_t> px(std::size_t(W) * H * 4);
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
        auto* p = &px[(std::size_t(y) * W + x) * 4];
        const bool top = y < H / 2, line = ((x + shift) / 9 + y / 7) % 2 == 0;
        p[0] = top ? 20 : 220; p[1] = line ? 150 : 40; p[2] = top ? 220 : 20; p[3] = 255;
    }
    return px;
}

// The engine's half of the wire, in a few lines: a heartbeat, and a map of a near middle.
struct FakeEngine {
    DepthShm shm;
    std::uint64_t seq = 0;
    std::vector<std::uint16_t> map = [] {
        std::vector<std::uint16_t> m(std::size_t(kDepthLinkShm.out_w) * kDepthLinkShm.out_h, 0x0000);  // 0.0: far
        for (int y = kDepthLinkShm.out_h / 4; y < 3 * kDepthLinkShm.out_h / 4; ++y)
            for (int x = kDepthLinkShm.out_w / 4; x < 3 * kDepthLinkShm.out_w / 4; ++x) m[std::size_t(y) * kDepthLinkShm.out_w + x] = 0x3c00;  // 1.0: near
        return m;
    }();
    void beat() { shm.heartbeat(now_ns(), 4242); }
    void publish(std::uint32_t channel) {
        const auto bytes = std::as_bytes(std::span(map));
        DepthFrame f{kDepthLinkShm.out_w, kDepthLinkShm.out_h, ++seq, now_ns(), now_ns(), DepthFormat::R16F, 0, bytes};
        CHECK(shm.publish(DepthShm::Side::Output, channel, f));
    }
};

struct Rig {
    std::unique_ptr<GlCompositor> gl;
    std::vector<FRect> tiles;
    std::vector<Placement> placements;
    int fd = -1;
    void* base = nullptr;
    std::size_t bytes = depth_shm_bytes(kDepthLinkShm);
    std::array<std::uint64_t, 4> frames{1, 1, 1, 1};
    std::array<bool, 4> wanted{true, false, false, false};
    bool draw() {
        const bool ok = gl->draw_and_swap(tiles, placements, -1, 0, kIdentity, kHalfW, kHalfH);
        glFinish();
        return ok;
    }
    DepthLink::Tick tick(bool drawing = true) {
        DepthLink::Tick t;
        t.wanted = wanted; t.frames = frames; t.focused = 0; t.drawing = drawing; t.convergence = 50; t.range_ms = 0; t.now_ns = now_ns();
        return t;
    }
    // How far apart the eyes are, per pixel, in the middle of the view: 0 is a flat picture.
    float eyes_apart() {
        std::vector<std::uint8_t> px(std::size_t(2 * W) * H * 4);
        glReadPixels(0, 0, 2 * W, H, GL_RGBA, GL_UNSIGNED_BYTE, px.data());
        double sum = 0; std::size_t n = 0;
        for (int y = H / 3; y < 2 * H / 3; ++y) for (int x = W / 3; x < 2 * W / 3; ++x) {
            const auto* a = &px[(std::size_t(y) * 2 * W + x) * 4]; const auto* b = &px[(std::size_t(y) * 2 * W + x + W) * 4];
            sum += std::abs(int(a[0]) - int(b[0])) + std::abs(int(a[1]) - int(b[1])) + std::abs(int(a[2]) - int(b[2])); ++n;
        }
        return float(sum / double(n) / 3);
    }
};

void anchors() {
    std::vector<std::uint16_t> map(std::size_t(100) * 100, 0x0000);  // half 0.0
    for (int y = 0; y < 100; ++y) for (int x = 0; x < 100; ++x) if (y >= 50) map[std::size_t(y) * 100 + x] = 0x3c00;  // half 1.0
    CHECK(depth_half_to_float(0x3c00) == 1.f && depth_half_to_float(0x3800) == 0.5f && depth_half_to_float(0x0000) == 0.f);
    CHECK(std::fabs(depth_half_to_float(0x0001) - 5.9604645e-8f) < 1e-12f);  // the smallest subnormal
    CHECK(depth_anchor(map, 100, 100, 100) == 1.f);
    CHECK(depth_anchor(map, 100, 100, 0) == 0.f);
    std::vector<std::uint16_t> ramp(std::size_t(256) * 256);
    for (int y = 0; y < 256; ++y) for (int x = 0; x < 256; ++x) ramp[std::size_t(y) * 256 + x] = std::uint16_t(0x3400 + (y * 0x0400) / 256);  // 0.25 .. 0.5
    const float p = depth_anchor(ramp, 256, 256, 92);
    CHECK(p > 0.4f && p < 0.5f && depth_anchor(ramp, 256, 256, 10) < 0.3f);
    CHECK(depth_anchor({}, 0, 0, 50) == 0.5f);
    std::vector<std::uint16_t> odd(std::size_t(64) * 64, 0x7e00);  // NaN everywhere: a broken map, not a crash
    CHECK(depth_anchor(odd, 64, 64, 50) == 0.5f);
}
}

int main() {
    anchors();
    Rig r;
    std::string why;
    r.gl = GlCompositor::create_offscreen(2 * W, H, 4, why);
    if (!r.gl) { std::printf("skip: %s\n", why.c_str()); return failures ? 1 : 77; }
    r.gl->resize(W, H, 2 * W, H);
    r.fd = memfd_create("depth-link-test", MFD_CLOEXEC);
    CHECK(r.fd >= 0 && ftruncate(r.fd, off_t(r.bytes)) == 0);
    r.base = mmap(nullptr, r.bytes, PROT_READ | PROT_WRITE, MAP_SHARED, r.fd, 0);
    CHECK(r.base != MAP_FAILED);

    const auto px = picture();
    for (std::size_t i = 0; i < 4; ++i) CHECK(r.gl->upload(i, px.data(), W, H, W * 4));
    r.tiles = {FRect{0, 0, float(W), float(H)}, FRect{float(W), 0, float(W), float(H)}, FRect{2 * float(W), 0, float(W), float(H)}, FRect{3 * float(W), 0, float(W), float(H)}};
    r.placements.assign(4, Placement{});
    StereoSettings set; set.enabled = true; set.half_baseline = 0; set.params.near_deg = 1.5f; set.params.far_deg = 1.f; set.params.strength = 2.f; set.params.text_keep = 1.f;
    set.desktops = {true, false, false, false};
    r.gl->set_stereo(set);
    if (!r.gl->stereo_available()) { std::puts("skip: stereo not available on this GL"); return 77; }

    // A wrong-sized memory is refused, and no link means nothing happens.
    {
        const int small = memfd_create("small", MFD_CLOEXEC);
        CHECK(small >= 0 && ftruncate(small, 4096) == 0);
        std::string refused;
        CHECK(!DepthLink::open(small, refused) && !refused.empty());
        close(small);
    }
    auto link = DepthLink::open(r.fd, why);
    CHECK(link != nullptr);
    if (!link) return 1;
    auto view = DepthShm::attach(r.base, r.bytes);  // the engine's side of the same memory
    CHECK(view.has_value());
    if (!view) return 1;
    FakeEngine engine{*view};
    std::vector<std::byte> seen;

    // No heartbeat yet: the wish reaches the engine's side, nothing else moves.
    link->tick(*r.gl, r.tick());
    CHECK(view->control(0) == (kDepthEnabled | kDepthFocused) && view->control(1) == 0);
    CHECK(link->feeds() == 0 && !link->engine_alive());
    CHECK(r.draw());
    CHECK(r.eyes_apart() < 1.f);

    // The engine comes up: the picture reaches it, once for each new frame, right way up and in the right colours.
    engine.beat();
    link->tick(*r.gl, r.tick());
    CHECK(link->engine_alive() && link->feeds() == 1);
    glFinish();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    std::optional<DepthFrame> in;
    while (!in && std::chrono::steady_clock::now() < deadline) {
        engine.beat();
        link->tick(*r.gl, r.tick());
        in = view->fetch(DepthShm::Side::Input, 0, seen, 0);
    }
    CHECK(in.has_value());
    if (in) {
        CHECK(in->w == kDepthLinkShm.in_w && in->h == kDepthLinkShm.in_h);
        CHECK(in->format == DepthFormat::Bgra8);  // an SHM upload is B,G,R in the texture
        const auto* p = reinterpret_cast<const std::uint8_t*>(in->pixels.data());
        const auto* top = p + (std::size_t(in->h) / 8 * in->w + std::size_t(in->w) / 2) * 4, *bottom = p + (std::size_t(in->h) * 7 / 8 * in->w + std::size_t(in->w) / 2) * 4;
        CHECK(top[0] < 60 && top[2] > 180);        // in BGRA: the top is red, B low and R high
        CHECK(bottom[0] > 180 && bottom[2] < 60);  // and the bottom blue
    }
    CHECK(link->feeds() == 1);  // the frame count has not moved, so the same picture does not go twice

    // The budget: however many desktops have new pictures, kDepthPerTick shrinks a tick.
    std::this_thread::sleep_for(std::chrono::milliseconds(40));  // past the first desktop's rate limit
    r.wanted = {true, true, true, true};
    r.frames = {2, 2, 2, 2};
    engine.beat();
    const auto before = link->feeds();
    link->tick(*r.gl, r.tick());
    CHECK(link->feeds() == before + std::uint64_t(kDepthPerTick));
    glFinish();
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    engine.beat();
    link->tick(*r.gl, r.tick());  // the rest, in their turn
    CHECK(link->feeds() == before + 4);
    glFinish();
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    engine.beat();
    link->tick(*r.gl, r.tick());
    CHECK(link->feeds() == before + 4);  // the same frames again: nothing new to send, whatever the turn says
    r.wanted = {true, false, false, false};

    // A map arrives: 3D.
    engine.beat();
    engine.publish(0);
    link->tick(*r.gl, r.tick());
    CHECK(link->maps() == 1);
    engine.publish(0);
    const auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(450);
    do { engine.beat(); link->tick(*r.gl, r.tick()); CHECK(r.draw()); std::this_thread::sleep_for(std::chrono::milliseconds(16)); }
    while (std::chrono::steady_clock::now() < until);
    const float deep = r.eyes_apart();
    std::printf("eyes apart with depth: %.2f/255\n", double(deep));
    CHECK(deep > 3.f);

    // A still desktop: no new map for a good while past kStereoStaleMs, the engine alive. It stays 3D.
    const auto still = std::chrono::steady_clock::now() + std::chrono::milliseconds(800);
    do { engine.beat(); link->tick(*r.gl, r.tick()); CHECK(r.draw()); std::this_thread::sleep_for(std::chrono::milliseconds(16)); }
    while (std::chrono::steady_clock::now() < still);
    const float held = r.eyes_apart();
    std::printf("still desktop, no new maps for 800 ms: %.2f/255\n", double(held));
    CHECK(held > 0.8f * deep);

    // The engine crashes: no more heartbeats, and no warning. The frames keep coming, and the desktop eases flat by itself.
    int bad = 0, frames = 0;
    const auto crash = std::chrono::steady_clock::now() + std::chrono::milliseconds(1500);
    do { link->tick(*r.gl, r.tick()); if (!r.draw()) ++bad; ++frames; std::this_thread::sleep_for(std::chrono::milliseconds(8)); }
    while (std::chrono::steady_clock::now() < crash);
    const float flat = r.eyes_apart();
    std::printf("after a crash, %d frames drawn, %d failed: %.2f/255\n", frames, bad, double(flat));
    CHECK(!link->engine_alive() && bad == 0 && frames > 40);
    CHECK(flat < 0.5f);
    const auto feeds_dead = link->feeds();
    r.frames = {3, 3, 3, 3};
    link->tick(*r.gl, r.tick());
    CHECK(link->feeds() == feeds_dead);  // nothing is fed to a dead engine

    // The renderer starts again in the same run, the engine with it: the memory is taken over, not wiped.
    engine.beat();
    link->stop();
    CHECK(view->control(0) == 0);
    link.reset();
    link = DepthLink::open(r.fd, why);
    CHECK(link != nullptr);
    if (!link) return 1;
    CHECK(view->engine_alive(now_ns()));  // the heartbeat survived the takeover, so the file was not laid out again
    const auto back = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
    do { engine.beat(); engine.publish(0); link->tick(*r.gl, r.tick()); CHECK(r.draw()); std::this_thread::sleep_for(std::chrono::milliseconds(16)); }
    while (std::chrono::steady_clock::now() < back);
    const float again = r.eyes_apart();
    std::printf("after a restart: %.2f/255\n", double(again));
    CHECK(again > 0.8f * deep);

    // A desktop not asked for never gets depth, whatever the engine publishes for it, and 3D off goes flat.
    r.wanted = {false, false, false, false};
    engine.publish(0);
    const auto maps = link->maps();
    link->tick(*r.gl, r.tick());
    CHECK(link->maps() == maps && view->control(0) == 0);
    set.enabled = false; r.gl->set_stereo(set);
    const auto off = std::chrono::steady_clock::now() + std::chrono::milliseconds(450);
    do { CHECK(r.draw()); std::this_thread::sleep_for(std::chrono::milliseconds(16)); } while (std::chrono::steady_clock::now() < off);
    CHECK(r.eyes_apart() < 0.5f);  // a buffer still side by side: the same picture for each eye

    // The dmabuf-style texture, the common case in a real run: a plain GL_TEXTURE_2D, sampled as it is, not swapped.
    {
        DepthFeed feed;
        CHECK(feed.init(kDepthLinkShm.in_w, kDepthLinkShm.in_h));
        GLuint tex = 0; glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);  // as gl.cpp's
        std::vector<std::uint8_t> rgba(std::size_t(64) * 64 * 4);
        for (int y = 0; y < 64; ++y) for (int x = 0; x < 64; ++x) { auto* p = &rgba[(std::size_t(y) * 64 + x) * 4]; p[0] = y < 32 ? 240 : 10; p[1] = 30; p[2] = y < 32 ? 10 : 240; p[3] = 255; }
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 64, 64, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        CHECK(feed.begin_texture(tex));
        CHECK(glGetError() == GL_NO_ERROR);
        std::vector<std::byte> out;
        glFinish();
        const auto done = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!feed.collect(out) && std::chrono::steady_clock::now() < done) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        CHECK(!out.empty());
        if (!out.empty()) {
            const auto* p = reinterpret_cast<const std::uint8_t*>(out.data());
            CHECK(p[0] > 200 && p[2] < 50);  // row 0, the source's top: red
            const auto* q = p + (std::size_t(kDepthLinkShm.in_h - 1) * kDepthLinkShm.in_w) * 4;
            CHECK(q[0] < 50 && q[2] > 200);
        }
        glDeleteTextures(1, &tex);
    }

    link.reset();
    munmap(r.base, r.bytes);
    close(r.fd);
    if (failures) { std::fprintf(stderr, "depth_link_render: %d failure(s)\n", failures); return 1; }
    std::puts("depth_link_render: ok");
    return 0;
}
