// The depth contract (xr/depth.hpp): input sizes, the shared layout, and that a reader never sees a torn frame.
// `--layout` prints the layout for tests/depth_engine/test_contract.py to compare with the Python mirror;
// `--peer FILE` plays the renderer against the real Python engine (same test file).
#include "xr/depth.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstddef>
#include <fcntl.h>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <time.h>
#include <unistd.h>

using namespace xr;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); std::exit(1); } } while (0)

static void sizes() {
    CHECK((depth_input_size(1920, 1200, kDepthLongHigh).w == 518 && depth_input_size(1920, 1200, kDepthLongHigh).h == 322));
    CHECK((depth_input_size(1920, 1200, kDepthLongBalanced).w == 420 && depth_input_size(1920, 1200, kDepthLongBalanced).h == 266));
    CHECK((depth_input_size(1200, 1920, kDepthLongHigh).w == 322 && depth_input_size(1200, 1920, kDepthLongHigh).h == 518));
    const DepthSize wide = depth_input_size(3840, 1200, kDepthLongBalanced);  // Ultrawide: the height floor wins.
    CHECK(wide.h == kDepthMinTall && wide.w % kDepthPatch == 0 && wide.w == 854);
    for (int w : {640, 1280, 1920, 2560, 3840}) for (int h : {480, 720, 1080, 1200, 1600}) for (int l : {252, 420, 518}) {
        const DepthSize s = depth_input_size(w, h, l);
        CHECK(s.w % kDepthPatch == 0 && s.h % kDepthPatch == 0 && std::min(s.w, s.h) >= 14);
        if (w >= h) CHECK(s.h >= kDepthMinTall);
    }
    CHECK(depth_input_size(0, 1200, 518).w == 0 && depth_input_size(1920, 0, 518).h == 0 && depth_input_size(1920, 1200, 0).w == 0);
}

static std::vector<std::byte> blank(const DepthShmConfig& c) { return std::vector<std::byte>(depth_shm_bytes(c)); }

static void attach_rules() {
    DepthShmConfig cfg{2, 28, 14, 28, 14};
    auto mem = blank(cfg);
    CHECK(!DepthShm::attach(mem.data(), mem.size()));                     // No magic yet.
    CHECK(!DepthShm::create(mem.data(), mem.size() - 1, cfg));            // Too small.
    CHECK(!DepthShm::create(mem.data(), mem.size(), DepthShmConfig{0, 28, 14, 28, 14}));
    CHECK(DepthShm::create(mem.data(), mem.size(), cfg));
    CHECK(DepthShm::attach(mem.data(), mem.size()));
    CHECK(!DepthShm::attach(mem.data(), mem.size() - 1));                 // Short mapping.
    reinterpret_cast<DepthShmHeader*>(mem.data())->version = 99;
    CHECK(!DepthShm::attach(mem.data(), mem.size()));                     // A file from another version.
}

static void round_trip() {
    DepthShmConfig cfg{2, 28, 14, 28, 14};
    auto mem = blank(cfg);
    auto shm = *DepthShm::create(mem.data(), mem.size(), cfg);
    std::vector<std::byte> out;
    CHECK(!shm.fetch(DepthShm::Side::Output, 0, out));                    // Nothing published.
    std::vector<std::byte> px(14 * 28 * 2, std::byte{7});
    DepthFrame f{28, 14, 5, 100, 200, DepthFormat::R16F, kDepthSceneCut, px};
    CHECK(shm.publish(DepthShm::Side::Output, 1, f));
    CHECK(!shm.fetch(DepthShm::Side::Output, 0, out));                    // Channels are separate.
    CHECK(!shm.fetch(DepthShm::Side::Input, 1, out));                     // Directions are separate.
    auto got = shm.fetch(DepthShm::Side::Output, 1, out);
    CHECK(got && got->w == 28 && got->h == 14 && got->source_seq == 5 && got->source_ns == 100 && got->done_ns == 200);
    CHECK(got->format == DepthFormat::R16F && got->flags == kDepthSceneCut && got->pixels.size() == px.size() && got->pixels[3] == std::byte{7});
    CHECK(!shm.fetch(DepthShm::Side::Output, 1, out, 5));                 // Already seen.
    f.source_seq = 6;
    CHECK(shm.publish(DepthShm::Side::Output, 1, f));
    CHECK(shm.fetch(DepthShm::Side::Output, 1, out, 5)->source_seq == 6);
    // Refused without touching the file.
    DepthFrame big = f; big.w = 42;
    CHECK(!shm.publish(DepthShm::Side::Output, 1, big));
    DepthFrame shortf = f; shortf.pixels = std::span<const std::byte>(px).first(10);
    CHECK(!shm.publish(DepthShm::Side::Output, 1, shortf));
    CHECK(!shm.publish(DepthShm::Side::Output, 2, f));
    DepthFrame none = f; none.format = DepthFormat::None;
    CHECK(!shm.publish(DepthShm::Side::Output, 1, none));
    shm.set_control(1, kDepthEnabled | kDepthFocused);
    CHECK(shm.control(1) == 3 && shm.control(0) == 0 && shm.control(9) == 0);
    CHECK(!shm.engine_alive(1000));                                       // Never started.
    shm.heartbeat(1000, 42);
    CHECK(shm.engine_alive(1000 + 400'000'000) && !shm.engine_alive(1000 + 600'000'000));
    CHECK(shm.engine_alive(10));                                          // A beat just after our clock read.
}

// A writer rewriting every byte against a reader: each frame must be one value throughout.
static void no_tearing() {
    DepthShmConfig cfg{1, 140, 140, 140, 140};
    auto mem = blank(cfg);
    auto shm = *DepthShm::create(mem.data(), mem.size(), cfg);
    std::atomic<bool> stop{false};
    std::thread writer([&] {
        std::vector<std::byte> px(140 * 140 * 4);
        for (std::uint64_t i = 1; !stop.load(); ++i) {
            std::memset(px.data(), static_cast<int>(i % 251), px.size());
            shm.publish(DepthShm::Side::Output, 0, {140, 140, i, 0, 0, DepthFormat::R32F, 0, px});
        }
    });
    std::vector<std::byte> out;
    std::uint64_t seen = 0, frames = 0;
    const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
    while (std::chrono::steady_clock::now() < end) {
        auto f = shm.fetch(DepthShm::Side::Output, 0, out, seen);
        if (!f) continue;
        CHECK(f->source_seq > seen);
        seen = f->source_seq;
        ++frames;
        const auto v = static_cast<std::byte>(f->source_seq % 251);
        for (std::byte b : f->pixels) CHECK(b == v);
    }
    stop = true;
    writer.join();
    CHECK(frames > 10);
}

static std::uint64_t now_ns() { timespec t{}; clock_gettime(CLOCK_MONOTONIC, &t); return static_cast<std::uint64_t>(t.tv_sec) * 1'000'000'000ull + t.tv_nsec; }

// Plays the renderer: makes the file, then publishes a frame until the engine answers for it.
static int peer(const char* path) {
    const DepthShmConfig cfg{2, 42, 28, 42, 28};
    const int fd = ::open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    CHECK(fd >= 0 && ftruncate(fd, static_cast<off_t>(depth_shm_bytes(cfg))) == 0);
    void* base = mmap(nullptr, depth_shm_bytes(cfg), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(base != MAP_FAILED);
    auto shm = *DepthShm::create(base, depth_shm_bytes(cfg), cfg);
    shm.set_control(1, kDepthEnabled);
    std::vector<std::byte> px(42 * 28 * 4), out;
    for (int y = 0; y < 28; ++y) for (int x = 0; x < 42; ++x) for (int c = 0; c < 3; ++c) px[(y * 42 + x) * 4 + c] = static_cast<std::byte>(y * 9);
    const auto end = now_ns() + 15'000'000'000ull;
    std::uint64_t seq = 1;
    while (now_ns() < end) {
        CHECK(shm.publish(DepthShm::Side::Input, 1, {42, 28, seq, now_ns(), 0, DepthFormat::Rgba8, 0, px}));
        if (auto f = shm.fetch(DepthShm::Side::Output, 1, out)) {
            CHECK(f->w == 42 && f->h == 28 && f->format == DepthFormat::R16F && f->source_seq <= seq && f->source_seq >= 1);
            CHECK(f->source_ns != 0 && f->done_ns >= f->source_ns && shm.engine_alive(now_ns()));
            std::printf("depth %dx%d seq %llu\n", f->w, f->h, static_cast<unsigned long long>(f->source_seq));
            return 0;
        }
        ++seq;
        usleep(5000);
    }
    std::fprintf(stderr, "no answer from the engine\n");
    return 2;
}

int main(int argc, char** argv) {
    if (argc > 1 && std::string(argv[1]) == "--layout") {
        const DepthShmConfig cfg{4, 518, 322, 518, 322};
        std::printf("{\"header\":%zu,\"meta\":%zu,\"channel\":%zu,\"page\":%zu,\"slots\":%u,\"magic\":%u,\"version\":%u,"
                    "\"latest_out\":%zu,\"control\":%zu,\"in\":%zu,\"out\":%zu,\"engine_ns\":%zu,\"engine_pid\":%zu,"
                    "\"source_seq\":%zu,\"flags\":%zu,\"stride\":%zu,\"bytes\":%zu}\n",
                    sizeof(DepthShmHeader), sizeof(DepthSlotMeta), sizeof(DepthChannelHeader), kDepthHeaderBytes, kDepthSlots, kDepthMagic, kDepthVersion,
                    offsetof(DepthChannelHeader, latest_out), offsetof(DepthChannelHeader, control), offsetof(DepthChannelHeader, in), offsetof(DepthChannelHeader, out),
                    offsetof(DepthShmHeader, engine_ns), offsetof(DepthShmHeader, engine_pid),
                    offsetof(DepthSlotMeta, source_seq), offsetof(DepthSlotMeta, flags), depth_channel_stride(cfg), depth_shm_bytes(cfg));
        return 0;
    }
    if (argc > 2 && std::string(argv[1]) == "--peer") return peer(argv[2]);
    sizes();
    attach_rules();
    round_trip();
    no_tearing();
    std::puts("depth contract ok");
}
