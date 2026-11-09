// The whole 3D chain off screen, on a real GPU: pictures in as a desktop's captures, the real DepthLink, a real depth engine in
// its own process over the real shared memory, the real GlCompositor drawing both eyes (docs/immersive-3d.md). tools/immersive_proof
// runs this: it starts the engine and makes the pictures. Nothing here touches a display, the glasses or a compositor.
//
//   stereo_chain_bench --pictures LIST --desktops N --seconds S --hz HZ --capture-hz HZ --mode 3d|flat|2d [--depth-fd FD]
//                      [--layout overview|full] [--strength P --convergence P --near A --far A --text P --range-ms MS]
//                      [--settle S] [--record DIR --record-frames K]
//                      [--eye-height H] [--diagonal-fov DEG]
//
// LIST: one line per picture, `WIDTH HEIGHT FILE`, FILE raw RGBA; a desktop goes round the list from its own starting point.
// --mode 3d both eyes with depth; flat both eyes, no depth (glasses side by side, nothing 3D); 2d the normal 1920x1200 frame.
// --record: after --settle, K frames as DIR/NNNN.ppm, for the comfort scores. The run is then not a timing run.
// One line of JSON on stdout. Every time is CPU time on the render thread, and with glFinish, so GPU work is in the frame's time.
#include "depth_link.hpp"
#include "xr/capture.hpp"
#include "xr/video.hpp"
#include <GLES3/gl3.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace xr;
namespace {
constexpr Mat3 kIdentity{1, 0, 0, 0, 1, 0, 0, 0, 1};
constexpr float kHalfW = 0.44f, kHalfH = 0.275f;
constexpr std::uint32_t kEyeW = 1920;

std::uint64_t now_ns() {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
struct Picture { std::uint32_t w = 0, h = 0; std::vector<std::uint8_t> xrgb; };
Picture load(std::uint32_t w, std::uint32_t h, const std::string& file) {
    Picture p{w, h, std::vector<std::uint8_t>(std::size_t(w) * h * 4)};
    std::ifstream in(file, std::ios::binary);
    in.read(reinterpret_cast<char*>(p.xrgb.data()), std::streamsize(p.xrgb.size()));
    if (!in) { std::fprintf(stderr, "cannot read %s\n", file.c_str()); std::exit(2); }
    for (std::size_t i = 0; i < p.xrgb.size(); i += 4) std::swap(p.xrgb[i], p.xrgb[i + 2]);  // upload() takes B,G,R,X
    return p;
}
nlohmann::json stats(std::vector<double> ms) {
    if (ms.empty()) return nullptr;
    std::sort(ms.begin(), ms.end());
    const auto at = [&](double q) { return ms[std::min(ms.size() - 1, std::size_t(q * double(ms.size())))]; };
    const auto round2 = [](double v) { return std::round(v * 100) / 100; };
    double sum = 0; for (const double v : ms) sum += v;
    return {{"count", ms.size()}, {"mean", round2(sum / double(ms.size()))}, {"p50", round2(at(0.5))}, {"p99", round2(at(0.99))}, {"max", round2(ms.back())}};
}
}

int main(int argc, char** argv) {
    if ((argc - 1) % 2) { std::fprintf(stderr, "each argument needs a value\n"); return 2; }
    std::map<std::string, std::string> a;
    for (int i = 1; i + 1 < argc; i += 2) a[argv[i]] = argv[i + 1];
    const auto text = [&](const char* k, const char* d) { return a.contains(k) ? a[k] : std::string(d); };
    const auto number = [&](const char* k, double d) { return a.contains(k) ? std::stod(a[k]) : d; };
    std::uint32_t eye_h = 1200;
    std::array<float, 2> projection{kHalfW, kHalfH};
    try {
        const auto geometry_number = [&](const char* key, double fallback) {
            if (!a.contains(key)) return fallback;
            std::size_t consumed = 0;
            const double value = std::stod(a[key], &consumed);
            if (consumed != a[key].size() || !std::isfinite(value)) throw std::invalid_argument(key);
            return value;
        };
        const double height = geometry_number("--eye-height", 1200);
        if (height < 256 || height > 2160 || std::floor(height) != height) throw std::invalid_argument("--eye-height");
        eye_h = static_cast<std::uint32_t>(height);
        if (a.contains("--diagonal-fov")) {
            const double diagonal = geometry_number("--diagonal-fov", 0);
            const float diagonal_f = static_cast<float>(diagonal);
            if (diagonal_f <= 0 || diagonal_f >= 180) throw std::invalid_argument("--diagonal-fov");
            projection = half_fov_tan(diagonal_f, kEyeW, eye_h);
            if (!(projection[0] > 0) || !(projection[1] > 0)) throw std::invalid_argument("--diagonal-fov");
        }
    } catch (const std::exception&) {
        std::fprintf(stderr, "bad geometry: --eye-height must be an integer in 256..2160; --diagonal-fov must be finite and in (0, 180)\n");
        return 2;
    }
    const int desktops = int(number("--desktops", 1)), depth_fd = int(number("--depth-fd", -1)), record_frames = int(number("--record-frames", 0));
    const double seconds = number("--seconds", 5), hz = number("--hz", 90), capture_hz = number("--capture-hz", 60), settle = number("--settle", 2);
    const std::string mode = text("--mode", "3d"), layout = text("--layout", "overview"), record = text("--record", "");
    if (desktops < 1 || desktops > int(kMaxDesktops) || (mode != "3d" && mode != "flat" && mode != "2d") || (mode == "3d" && depth_fd < 0)) { std::fprintf(stderr, "bad arguments\n"); return 2; }
    if (mode != "2d" && 2 * kEyeW < 3 * eye_h) { std::fprintf(stderr, "stereo geometry needs a side-by-side output at least 3:1 wide\n"); return 2; }
    ImmersiveParams wire;
    wire.strength = int(number("--strength", wire.strength)); wire.convergence = int(number("--convergence", wire.convergence));
    wire.near_arcmin = int(number("--near", wire.near_arcmin)); wire.far_arcmin = int(number("--far", wire.far_arcmin));
    wire.text = int(number("--text", wire.text)); wire.range_ms = int(number("--range-ms", wire.range_ms));

    std::vector<Picture> pictures;
    {
        std::ifstream list(text("--pictures", ""));
        std::uint32_t w, h; std::string file;
        while (list >> w >> h >> file) pictures.push_back(load(w, h, file));
    }
    if (pictures.empty()) { std::fprintf(stderr, "no pictures\n"); return 2; }

    const bool wide = mode != "2d";
    std::string why;
    auto gl = GlCompositor::create_offscreen(wide ? 2 * kEyeW : kEyeW, eye_h, std::size_t(desktops), why);
    if (!gl) { std::printf("{\"skip\":\"%s\"}\n", why.c_str()); return 77; }
    if (wide) gl->resize(kEyeW, eye_h, 2 * kEyeW, eye_h);
    const auto count = static_cast<std::size_t>(desktops);
    std::vector<FRect> tiles; std::vector<Placement> placements(count);
    if (layout == "full" && desktops == 1) tiles = {FRect{0, 0, float(kEyeW), float(eye_h)}};
    else {
        std::vector<Size> sizes(count, Size{float(pictures[0].w), float(pictures[0].h)});
        for (const auto& r : side_by_side(sizes, kEyeW, eye_h)) tiles.push_back(FRect{float(r.x), float(r.y), float(r.width), float(r.height)});
    }
    StereoSettings set;
    set.enabled = mode == "3d"; set.params = stereo_from_wire(wire); set.desktops.assign(static_cast<std::size_t>(desktops), true);
    gl->set_stereo(set);
    std::unique_ptr<DepthLink> link;
    if (mode == "3d") {
        link = DepthLink::open(depth_fd, why);
        if (!link) { std::fprintf(stderr, "depth link: %s\n", why.c_str()); return 1; }
    }
    std::vector<std::size_t> shown(count);
    for (int i = 0; i < desktops; ++i) {
        shown[std::size_t(i)] = std::size_t(i) * pictures.size() / std::size_t(desktops);
        const auto& p = pictures[shown[std::size_t(i)]];
        gl->upload(std::size_t(i), p.xrgb.data(), p.w, p.h, p.w * 4);
    }
    std::vector<std::uint64_t> frames(count, 1);
    std::array<bool, kMaxDesktops> wanted{};
    for (int i = 0; i < desktops; ++i) wanted[std::size_t(i)] = true;

    std::vector<double> tick_ms, draw_ms, total_ms, late_ms;
    const std::uint64_t period = std::uint64_t(1e9 / hz), capture_period = capture_hz > 0 ? std::uint64_t(1e9 / capture_hz) : 0;
    const std::uint64_t start = now_ns(), measure_at = start + std::uint64_t(settle * 1e9);
    const std::uint64_t end_at = measure_at + std::uint64_t((record.empty() ? seconds : 1e6) * 1e9);
    std::uint64_t next = start, next_capture = start + capture_period;
    std::uint64_t over_budget = 0, frames_drawn = 0, draw_failures = 0;
    std::uint64_t maps_at_measure = 0, feeds_at_measure = 0;
    int recorded = 0;
    bool measuring = false;
    std::size_t picture_clock = 0;
    std::vector<std::uint8_t> readback(wide ? std::size_t(2 * kEyeW) * eye_h * 4 : 0);
    while (now_ns() < end_at && (record.empty() || recorded < record_frames)) {
        const std::uint64_t t = now_ns();
        if (t < next) { std::this_thread::sleep_for(std::chrono::nanoseconds(next - t)); continue; }
        if (!measuring && t >= measure_at) {
            measuring = true;
            if (link) { maps_at_measure = link->maps(); feeds_at_measure = link->feeds(); }
        }
        if (capture_period && t >= next_capture && pictures.size() > 1) {  // the desktops' new frames, all in the same frame
            next_capture += capture_period;
            ++picture_clock;
            for (int i = 0; i < desktops; ++i) {
                const auto at = (shown[std::size_t(i)] + picture_clock) % pictures.size();
                const auto& p = pictures[at];
                gl->upload(std::size_t(i), p.xrgb.data(), p.w, p.h, p.w * 4);
                ++frames[std::size_t(i)];
            }
        }
        const std::uint64_t t0 = now_ns();
        if (link) {
            DepthLink::Tick tick;
            tick.wanted = std::span<const bool>(wanted.data(), std::size_t(desktops)); tick.frames = frames; tick.focused = 0; tick.drawing = true;
            tick.convergence = float(wire.convergence); tick.range_ms = float(wire.range_ms); tick.now_ns = t0;
            link->tick(*gl, tick);
        }
        const std::uint64_t t1 = now_ns();
        if (!gl->draw_and_swap(tiles, placements, -1, 0, kIdentity, projection[0], projection[1])) ++draw_failures;
        glFinish();
        const std::uint64_t t2 = now_ns();
        ++frames_drawn;
        if (measuring) {
            tick_ms.push_back(double(t1 - t0) / 1e6); draw_ms.push_back(double(t2 - t1) / 1e6); total_ms.push_back(double(t2 - t0) / 1e6);
            late_ms.push_back(double(t0 - next) / 1e6);
            if (t2 - t0 > period) ++over_budget;
        }
        if (measuring && !record.empty() && recorded < record_frames) {
            glReadPixels(0, 0, 2 * kEyeW, eye_h, GL_RGBA, GL_UNSIGNED_BYTE, readback.data());
            char name[32]; std::snprintf(name, sizeof name, "/%04d.ppm", recorded++);
            std::ofstream out(record + name, std::ios::binary);
            out << "P6\n" << 2 * kEyeW << " " << eye_h << "\n255\n";
            std::vector<char> row(std::size_t(2 * kEyeW) * 3);
            for (int y = int(eye_h) - 1; y >= 0; --y) {
                const auto* p = &readback[std::size_t(y) * 2 * kEyeW * 4];
                for (std::size_t x = 0; x < 2 * kEyeW; ++x) { row[x * 3] = char(p[x * 4]); row[x * 3 + 1] = char(p[x * 4 + 1]); row[x * 3 + 2] = char(p[x * 4 + 2]); }
                out.write(row.data(), std::streamsize(row.size()));
            }
        }
        next += period;
        if (next < now_ns()) next = now_ns();  // a late frame is measured, not chased
    }
    const double measured_s = record.empty() ? seconds : 0;
    nlohmann::json out = {{"mode", mode}, {"desktops", desktops}, {"layout", layout}, {"hz", hz}, {"capture_hz", capture_hz}, {"gl", nlohmann::json::parse(gl->report())},
                          {"eye_width", kEyeW}, {"eye_height", eye_h}, {"half_fov_tan", projection},
                          {"frames_drawn", frames_drawn}, {"draw_failures", draw_failures}, {"over_budget", over_budget}, {"link_ms", stats(tick_ms)},
                          {"draw_ms", stats(draw_ms)}, {"frame_ms", stats(total_ms)}, {"start_late_ms", stats(late_ms)}};
    if (link) {
        out["depth"] = nlohmann::json::parse(link->report());
        if (measured_s > 0) {
            out["maps_per_desktop_per_s"] = std::round(double(link->maps() - maps_at_measure) / measured_s / desktops * 10) / 10;
            out["feeds_per_desktop_per_s"] = std::round(double(link->feeds() - feeds_at_measure) / measured_s / desktops * 10) / 10;
        }
        link->stop();
        link.reset();
    }
    std::printf("%s\n", out.dump().c_str());
    return draw_failures ? 1 : 0;
}
