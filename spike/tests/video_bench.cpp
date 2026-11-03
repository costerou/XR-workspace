// VR video playback timing harness (docs/vr-video.md#performance). Not a test: it is slow, needs a GPU and its numbers
// depend on machine load, so ctest never runs it. Run by hand:
//   build/native-spike/video_bench [--gpu /dev/dri/renderD128] [--seconds 6] [--hz 120] [--dir DIR] [--case NAME]...
//                                  [--restarts N] [--json]
// It drives the real VideoPlayer and VideoSphere the way the renderer's frame loop does (update, project into a
// 1920x1200 target, swap report) on a surfaceless EGL context (no display, window or compositor), at a simulated
// display rate, and reports per clip:
//  - hwdec: the decode path mpv took (vaapi = zero copy, vaapi-copy = through system memory, no = software);
//  - first: time from open to the first frame in the texture;
//  - update: CPU time of VideoPlayer::update() per display frame (mean / p99 / max), and GPU time of the ticks that
//    rendered a new video frame (timestamp queries; mpv's own passes run inside);
//  - busy: CPU + GPU time per display frame (glFinish each tick), and how many display frames went over the budget
//    (a missed vsync in the glasses: the head-tracked view stutters);
//  - shown / dropped: video frames that reached the texture, read back from a frame-number barcode burned into each
//    generated clip, so a dropped frame is one whose number never appeared;
//  - judder: intervals between new video frames that differ from display rate / video rate (2 at 60 fps, 4 at 30);
//  - lead: how early each frame was put on screen relative to mpv's target time (from the player's report, if it
//    has those fields).
// --restarts N: a web video's seek restarts the relay and reopens the player (tools/native_spike/web_video.py);
// measures the renderer-thread stall of each close + open and the time to the next first frame.
// Clips are generated here with ffmpeg (hardware encoder on the chosen GPU when it has one), never downloaded, and
// kept in --dir between runs.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <filesystem>
#include <map>
#include <nlohmann/json.hpp>
#include <numeric>
#include <optional>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>
#include "xr/video.hpp"
#include "video_gl.hpp"
#include "video_player.hpp"

using namespace xr;
namespace {
using Json = nlohmann::json;
std::uint64_t now_ns() {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
double ms(std::uint64_t ns) { return double(ns) / 1e6; }

PFNGLGENQUERIESEXTPROC gen_queries;
PFNGLQUERYCOUNTEREXTPROC query_counter;
PFNGLGETQUERYOBJECTUI64VEXTPROC query_u64;

std::string make_context(const std::string& gpu) {
    auto get_display = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
    auto query_devices = reinterpret_cast<PFNEGLQUERYDEVICESEXTPROC>(eglGetProcAddress("eglQueryDevicesEXT"));
    auto device_string = reinterpret_cast<PFNEGLQUERYDEVICESTRINGEXTPROC>(eglGetProcAddress("eglQueryDeviceStringEXT"));
    if (!get_display) return "no eglGetPlatformDisplayEXT";
    EGLDisplay d = EGL_NO_DISPLAY;
    if (!gpu.empty()) {
        if (!query_devices || !device_string) return "no EGL device enumeration";
        EGLDeviceEXT devices[16]; EGLint n = 0;
        query_devices(16, devices, &n);
        for (EGLint i = 0; i < n && d == EGL_NO_DISPLAY; ++i) {
            const char* node = device_string(devices[i], EGL_DRM_RENDER_NODE_FILE_EXT);
            if (node && gpu == node) d = get_display(EGL_PLATFORM_DEVICE_EXT, devices[i], nullptr);
        }
        if (d == EGL_NO_DISPLAY) return "no EGL device with render node " + gpu;
    } else {
        d = get_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    }
    if (d == EGL_NO_DISPLAY || !eglInitialize(d, nullptr, nullptr) || !eglBindAPI(EGL_OPENGL_ES_API)) return "eglInitialize";
    const EGLint context_attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 2, EGL_NONE};  // as the renderer asks (gl.cpp)
    const EGLint config_attribs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE};
    EGLConfig config = nullptr; EGLint n = 0;
    if (!eglChooseConfig(d, config_attribs, &config, 1, &n) || n < 1) config = EGL_NO_CONFIG_KHR;
    EGLContext c = eglCreateContext(d, config, EGL_NO_CONTEXT, context_attribs);
    if (c == EGL_NO_CONTEXT || !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c)) return "eglCreateContext/eglMakeCurrent";
    gen_queries = reinterpret_cast<PFNGLGENQUERIESEXTPROC>(eglGetProcAddress("glGenQueriesEXT"));
    query_counter = reinterpret_cast<PFNGLQUERYCOUNTEREXTPROC>(eglGetProcAddress("glQueryCounterEXT"));
    query_u64 = reinterpret_cast<PFNGLGETQUERYOBJECTUI64VEXTPROC>(eglGetProcAddress("glGetQueryObjectui64vEXT"));
    const char* ext = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
    if (!ext || !std::strstr(ext, "GL_EXT_disjoint_timer_query")) gen_queries = nullptr;
    return "";
}

struct Clip { std::string name, codec; int w, h, fps; std::string pix; int mbit; };
const Clip kClips[] = {
    {"4k60-h264", "h264", 3840, 1920, 60, "nv12", 40},
    {"5.7k60-hevc", "hevc", 5760, 2880, 60, "nv12", 60},
    {"8k30-hevc", "hevc", 8192, 4096, 30, "nv12", 60},
    {"8k60-hevc10", "hevc", 8192, 4096, 60, "p010", 100},
    {"8k30-av1", "av1", 8192, 4096, 30, "nv12", 60},
};
// Frame-number barcode: 12 blocks of 32x32 at the picture's top-left, white = bit set (frame number mod 4096).
constexpr int kBits = 12, kBlock = 32;
std::string run_quiet(const std::string& cmd) {
    std::string out;
    if (FILE* p = ::popen((cmd + " 2>&1").c_str(), "r")) {
        char buf[512];
        while (std::fgets(buf, sizeof buf, p)) if (out.size() < 4000) out += buf;
        if (::pclose(p) == 0) return "";
    }
    return out.empty() ? "failed" : out;
}
bool generate(const std::string& ffmpeg, const std::string& gpu, const Clip& c, double seconds, const std::string& path) {
    if (std::filesystem::exists(path) && std::filesystem::file_size(path) > 100000) return true;
    const std::string tmp = path + ".part.mp4";
    const std::string fps = std::to_string(c.fps), dur = std::to_string(int(std::ceil(seconds)));
    const std::string graph =
        "testsrc2=s=" + std::to_string(c.w) + "x" + std::to_string(c.h) + ":r=" + fps + ":d=" + dur + "[bg];"
        "nullsrc=s=" + std::to_string(kBits * kBlock) + "x" + std::to_string(kBlock) + ":r=" + fps + ":d=" + dur +
        ",format=yuv420p,geq=lum='if(bitand(N\\,pow(2\\,floor(X/" + std::to_string(kBlock) + ")))\\,235\\,16)':cb=128:cr=128[code];"
        "[bg][code]overlay=0:0:shortest=1";
    const std::string common = "'" + ffmpeg + "' -nostdin -hide_banner -loglevel error -y ";
    const std::string audio = " -f lavfi -i sine=frequency=440:duration=" + dur + " -c:a aac -b:a 128k -shortest ";
    const std::string node = gpu.empty() ? "/dev/dri/renderD128" : gpu;
    const std::string hw = common + "-vaapi_device " + node + " -filter_complex \"" + graph + ",format=" + c.pix + ",hwupload[v]\"" + audio +
                           "-map '[v]' -map 0:a -c:v " + c.codec + "_vaapi" + (c.pix == "p010" ? " -profile:v main10" : "") +
                           " -b:v " + std::to_string(c.mbit) + "M -g " + fps + " '" + tmp + "'";
    std::string err = run_quiet(hw);
    if (!err.empty()) {  // software fallback
        const std::string enc = c.codec == "h264" ? "libx264 -preset ultrafast" : c.codec == "hevc" ? "libx265 -preset ultrafast -x265-params log-level=error"
                                                                                                    : "libsvtav1 -preset 12";
        const std::string sw = common + "-filter_complex \"" + graph + ",format=" + (c.pix == "p010" ? "yuv420p10le" : "yuv420p") + "[v]\"" +
                               audio + "-map '[v]' -map 0:a -c:v " + enc + " -b:v " + std::to_string(c.mbit) + "M -g " + fps + " '" + tmp + "'";
        err = run_quiet(sw);
        if (!err.empty()) { std::fprintf(stderr, "  %s: cannot generate: %s\n", c.name.c_str(), err.c_str()); return false; }
    }
    std::filesystem::rename(tmp, path);
    return true;
}

struct Target { GLuint fb = 0, rb = 0; };
Target make_target(int w, int h) {
    Target t;
    glGenFramebuffers(1, &t.fb); glBindFramebuffer(GL_FRAMEBUFFER, t.fb);
    glGenRenderbuffers(1, &t.rb); glBindRenderbuffer(GL_RENDERBUFFER, t.rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, w, h);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, t.rb);
    return t;
}
std::optional<int> read_code(unsigned texture, GLuint read_fb) {
    glBindFramebuffer(GL_FRAMEBUFFER, read_fb);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    std::uint8_t px[kBits * kBlock * 4]{};
    glReadPixels(0, kBlock / 2, kBits * kBlock, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);  // row 0 = top of the picture
    int code = 0;
    for (int b = 0; b < kBits; ++b) {
        const int v = px[(b * kBlock + kBlock / 2) * 4];
        if (v > 96 && v < 160) return std::nullopt;  // blurred: not a clean frame
        if (v >= 160) code |= 1 << b;
    }
    return code;
}

struct Stat { double mean = 0, p50 = 0, p99 = 0, max = 0; std::size_t n = 0; };
Stat stat(std::vector<double> v) {
    Stat s; s.n = v.size();
    if (v.empty()) return s;
    std::sort(v.begin(), v.end());
    s.mean = std::accumulate(v.begin(), v.end(), 0.0) / double(v.size());
    s.p50 = v[v.size() / 2]; s.p99 = v[std::min(v.size() - 1, std::size_t(double(v.size()) * 0.99))]; s.max = v.back();
    return s;
}
Json js(const Stat& s) { return {{"mean", s.mean}, {"p50", s.p50}, {"p99", s.p99}, {"max", s.max}, {"n", s.n}}; }

Json player_report(const VideoPlayer& p) {
    try { return Json::parse(p.report()); } catch (...) { return Json::object(); }
}

struct Options { std::string gpu, dir = "video-bench-clips", ffmpeg = "/usr/bin/ffmpeg"; double seconds = 6; int hz = 120; int restarts = 0;
                 std::vector<std::string> cases; bool json = false; };

Json bench_clip(const Options& o, const Clip& c, const std::string& path, VideoSphere& sphere) {
    Json out = {{"clip", c.name}};
    const double period_ns = 1e9 / o.hz;
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) { out["error"] = "cannot open clip"; return out; }
    std::string why;
    const auto t_open = now_ns();
    auto player = VideoPlayer::open(fd, {false, true, nullptr}, why);
    const auto open_ns = now_ns() - t_open;
    if (!player) { out["error"] = why; return out; }
    const Target target = make_target(1920, 1200);
    GLuint read_fb = 0; glGenFramebuffers(1, &read_fb);
    GLuint queries[2] = {0, 0};
    if (gen_queries) gen_queries(2, queries);
    const Mat3 view{1, 0, 0, 0, 1, 0, 0, 0, 1};
    const VideoFormat format{};
    const auto half = half_fov_tan(46.f, 1920, 1200);

    struct Tick { std::uint64_t at = 0; double update_ms = 0, gpu_ms = -1, busy_ms = 0; bool fresh = false; std::optional<int> code; };
    std::vector<Tick> ticks;
    ticks.reserve(std::size_t((o.seconds + 30) * o.hz));
    std::uint64_t first_frame_ns = 0, measure_from = 0, measure_to = 0;
    std::uint64_t last_frames = 0, max_update_before_first = 0;
    std::uint64_t deadline = now_ns();
    const std::uint64_t give_up = deadline + std::uint64_t(30e9);
    int late_wakeups = 0;
    while (true) {
        const auto t0 = now_ns();
        if (gen_queries) query_counter(queries[0], GL_TIMESTAMP_EXT);
        player->update();
        if (gen_queries) query_counter(queries[1], GL_TIMESTAMP_EXT);
        const auto t1 = now_ns();
        glBindFramebuffer(GL_FRAMEBUFFER, target.fb);
        glViewport(0, 0, 1920, 1200); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
        if (player->texture()) sphere.draw(player->texture(), player->flip_y(), format, 0, view, half[0], half[1]);
        glFinish();  // the GPU work of this display frame is done (a swap would hand it to the compositor)
        const auto t2 = now_ns();
        player->report_swap();
        Tick tick; tick.at = t0; tick.update_ms = ms(t1 - t0); tick.busy_ms = ms(t2 - t0);
        const Json r = player_report(*player);
        const std::uint64_t frames = r.value("frames", std::uint64_t(0));
        tick.fresh = frames != last_frames; last_frames = frames;
        if (tick.fresh && gen_queries) {
            std::uint64_t a = 0, b = 0; query_u64(queries[0], GL_QUERY_RESULT_EXT, &a); query_u64(queries[1], GL_QUERY_RESULT_EXT, &b);
            tick.gpu_ms = b > a ? ms(b - a) : 0;
        }
        if (tick.fresh && player->texture()) tick.code = read_code(player->texture(), read_fb);
        if (!first_frame_ns) {
            max_update_before_first = std::max(max_update_before_first, t1 - t0);
            if (player->texture()) {
                first_frame_ns = t0 - t_open;
                measure_from = t0 + std::uint64_t(1e9);  // skip the start-up second
                measure_to = measure_from + std::uint64_t(o.seconds * 1e9);
            } else if (player->state() == VideoState::error || t0 > give_up) break;
        }
        if (measure_from && t0 >= measure_from && t0 < measure_to) ticks.push_back(tick);
        if (measure_to && t0 >= measure_to) break;
        deadline += std::uint64_t(period_ns);
        const auto after = now_ns();
        if (after > deadline) {  // missed: the next vsync is the next multiple of the period
            ++late_wakeups;
            while (deadline < after) deadline += std::uint64_t(period_ns);
        }
        timespec ts{time_t(deadline / 1'000'000'000ull), long(deadline % 1'000'000'000ull)};
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr);
    }
    const Json final_report = player_report(*player);
    out["hwdec"] = final_report.value("hwdec", std::string("?"));
    out["open_ms"] = ms(open_ns);
    out["first_frame_ms"] = first_frame_ns ? ms(first_frame_ns) : -1.0;
    out["max_update_before_first_ms"] = ms(max_update_before_first);
    if (!first_frame_ns) { out["error"] = player->error().empty() ? "no frame" : player->error(); }
    // Per-tick statistics over the measurement window.
    std::vector<double> upd, gpu, busy;
    int over_budget = 0;
    for (const auto& t : ticks) {
        upd.push_back(t.update_ms); busy.push_back(t.busy_ms);
        if (t.gpu_ms >= 0) gpu.push_back(t.gpu_ms);
        if (t.busy_ms * 1e6 > period_ns) ++over_budget;
    }
    // Frames shown: the barcode sequence (unwrapped mod 4096).
    std::vector<std::pair<std::size_t, int>> shown;  // (tick index, frame number)
    int unreadable = 0;
    for (std::size_t i = 0; i < ticks.size(); ++i) {
        if (!ticks[i].fresh) continue;
        if (!ticks[i].code) { ++unreadable; continue; }
        int n = *ticks[i].code;
        if (!shown.empty()) { const int prev = shown.back().second; while (n < prev - 2048) n += 4096; }
        shown.emplace_back(i, n);
    }
    int dropped = 0, repeated = 0, backwards = 0, judder = 0;
    std::map<int, int> intervals;
    const double ideal = double(o.hz) / c.fps;
    for (std::size_t k = 1; k < shown.size(); ++k) {
        const int dn = shown[k].second - shown[k - 1].second;
        if (dn > 1) dropped += dn - 1;
        if (dn == 0) ++repeated;
        if (dn < 0) ++backwards;
        if (dn == 1) {
            const int dt = int(shown[k].first - shown[k - 1].first);
            ++intervals[dt];
            if (std::fabs(dt - ideal) > 0.5) ++judder;
        }
    }
    const double window_s = ticks.empty() ? 0 : double(ticks.back().at - ticks.front().at) / 1e9;
    Json iv = Json::object();
    for (const auto& [k, v] : intervals) iv[std::to_string(k)] = v;
    out["window_s"] = window_s;
    out["ticks"] = ticks.size();
    out["update_ms"] = js(stat(upd));
    out["gpu_new_frame_ms"] = js(stat(gpu));
    out["busy_ms"] = js(stat(busy));
    out["over_budget"] = over_budget;
    out["late_wakeups"] = late_wakeups;
    out["expected_frames"] = int(std::lround(window_s * c.fps));
    out["shown"] = shown.size();
    out["dropped"] = dropped;
    out["repeated"] = repeated;
    out["backwards"] = backwards;
    out["unreadable"] = unreadable;
    out["judder"] = judder;
    out["intervals"] = iv;
    for (const char* k : {"lead_ms", "drops", "early", "late", "held", "render_ms", "va_device", "interval_ms"})
        if (final_report.contains(k)) out[k] = final_report[k];
    // Close: the renderer-thread stall of tearing the player down (VideoMode::close / a web seek).
    const auto t_close = now_ns();
    player.reset();
    out["close_ms"] = ms(now_ns() - t_close);
    glDeleteFramebuffers(1, &read_fb); glDeleteFramebuffers(1, &target.fb); glDeleteRenderbuffers(1, &target.rb);
    return out;
}

// A web video seek: the next stream into the playing player, as VideoMode::open does on every relay restart (reopen(),
// else a new player), then a full close as on Esc.
Json bench_restarts(const Options& o, const std::string& path, VideoSphere& sphere) {
    Json out = {{"restarts", o.restarts}};
    std::vector<double> close_ms, open_ms, first_ms, worst_tick_ms;
    int reused = 0;
    std::unique_ptr<VideoPlayer> player;
    const Target target = make_target(1920, 1200);
    const Mat3 view{1, 0, 0, 0, 1, 0, 0, 0, 1};
    const auto half = half_fov_tan(46.f, 1920, 1200);
    for (int i = 0; i <= o.restarts; ++i) {
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
        const auto t0 = now_ns();
        const bool reuse = player && player->reopen(fd, {false, true, nullptr});
        reused += reuse;
        if (!reuse) player.reset();
        const auto t1 = now_ns();
        std::string why;
        if (!reuse) player = VideoPlayer::open(fd, {false, true, nullptr}, why);
        const auto t2 = now_ns();
        if (!player) { out["error"] = why; break; }
        double worst = 0;
        std::uint64_t first = 0;
        while (!first && now_ns() - t2 < 20'000'000'000ull) {
            const auto a = now_ns();
            player->update();
            glBindFramebuffer(GL_FRAMEBUFFER, target.fb); glViewport(0, 0, 1920, 1200); glClear(GL_COLOR_BUFFER_BIT);
            if (player->texture()) sphere.draw(player->texture(), player->flip_y(), {}, 0, view, half[0], half[1]);
            glFinish();
            player->report_swap();
            worst = std::max(worst, ms(now_ns() - a));
            if (player->texture()) first = now_ns() - t2;
            else std::this_thread::sleep_for(std::chrono::microseconds(1'000'000 / o.hz));
        }
        if (i > 0) {  // the first open is not a restart
            close_ms.push_back(ms(t1 - t0)); open_ms.push_back(ms(t2 - t1)); first_ms.push_back(ms(first)); worst_tick_ms.push_back(worst);
        }
        // Play a little, as between seek key presses.
        for (int k = 0; k < o.hz / 2; ++k) {
            player->update(); glFinish(); player->report_swap();
            std::this_thread::sleep_for(std::chrono::microseconds(1'000'000 / o.hz));
        }
    }
    const auto t_close = now_ns();
    player.reset();
    out["final_close_ms"] = ms(now_ns() - t_close);
    out["reused"] = reused;
    out["close_ms"] = js(stat(close_ms));
    out["open_ms"] = js(stat(open_ms));
    out["first_frame_ms"] = js(stat(first_ms));
    out["worst_tick_until_first_ms"] = js(stat(worst_tick_ms));
    glDeleteFramebuffers(1, &target.fb); glDeleteRenderbuffers(1, &target.rb);
    return out;
}

void print_human(const Json& r) {
    if (r.contains("restarts")) {
        std::printf("restarts x%d (reused %d): close %.1f ms (max %.1f), open %.1f ms (max %.1f), first frame %.0f ms (max %.0f), worst tick %.1f ms (max %.1f), final close %.1f ms\n",
                    r["restarts"].get<int>(), r.value("reused", 0), r["close_ms"]["mean"].get<double>(), r["close_ms"]["max"].get<double>(),
                    r["open_ms"]["mean"].get<double>(), r["open_ms"]["max"].get<double>(), r["first_frame_ms"]["mean"].get<double>(),
                    r["first_frame_ms"]["max"].get<double>(), r["worst_tick_until_first_ms"]["mean"].get<double>(),
                    r["worst_tick_until_first_ms"]["max"].get<double>(), r.value("final_close_ms", Json(-1.0)).get<double>());
        return;
    }
    if (r.contains("error")) { std::printf("%-12s error: %s\n", r["clip"].get<std::string>().c_str(), r["error"].get<std::string>().c_str()); return; }
    std::printf("%-12s %-10s first %5.0f ms | update %.2f/%.2f/%.2f ms | gpu/new frame %.2f (p99 %.2f) ms | busy p99 %.2f max %.2f ms, over %d/%zu"
                " | shown %zu/%d dropped %d repeated %d judder %d | close %.1f ms",
                r["clip"].get<std::string>().c_str(), r["hwdec"].get<std::string>().c_str(), r["first_frame_ms"].get<double>(),
                r["update_ms"]["mean"].get<double>(), r["update_ms"]["p99"].get<double>(), r["update_ms"]["max"].get<double>(),
                r["gpu_new_frame_ms"]["mean"].get<double>(), r["gpu_new_frame_ms"]["p99"].get<double>(), r["busy_ms"]["p99"].get<double>(),
                r["busy_ms"]["max"].get<double>(), r["over_budget"].get<int>(), r["ticks"].get<std::size_t>(), r["shown"].get<std::size_t>(),
                r["expected_frames"].get<int>(), r["dropped"].get<int>(), r["repeated"].get<int>(), r["judder"].get<int>(), r["close_ms"].get<double>());
    if (r.contains("lead_ms"))
        std::printf(" | lead min/mean/max %s ms, early %s late %s held %s, mpv drops %s, render cpu %s ms", r["lead_ms"].dump().c_str(),
                    r.value("early", Json(0)).dump().c_str(), r.value("late", Json(0)).dump().c_str(), r.value("held", Json(0)).dump().c_str(),
                    r.value("drops", Json(0)).dump().c_str(), r.value("render_ms", Json(0)).dump().c_str());
    std::printf("\n");
}
}  // namespace

int main(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&] { return i + 1 < argc ? std::string(argv[++i]) : std::string(); };
        if (a == "--gpu") o.gpu = next();
        else if (a == "--seconds") o.seconds = std::atof(next().c_str());
        else if (a == "--hz") o.hz = std::atoi(next().c_str());
        else if (a == "--dir") o.dir = next();
        else if (a == "--ffmpeg") o.ffmpeg = next();
        else if (a == "--case") o.cases.push_back(next());
        else if (a == "--restarts") o.restarts = std::atoi(next().c_str());
        else if (a == "--json") o.json = true;
        else { std::printf("usage: %s [--gpu /dev/dri/renderDN] [--seconds S] [--hz HZ] [--dir DIR] [--ffmpeg PATH] [--case NAME]... [--restarts N] [--json]\n"
                           "cases:", argv[0]);
               for (const auto& c : kClips) std::printf(" %s", c.name.c_str());
               std::printf("\n"); return a == "--help" ? 0 : 2; }
    }
    if (o.hz < 10 || o.hz > 500 || o.seconds <= 0) { std::fprintf(stderr, "bad --hz or --seconds\n"); return 2; }
    if (!VideoPlayer::available()) { std::puts("built without libmpv"); return 77; }
    const std::string why = make_context(o.gpu);
    if (!why.empty()) { std::fprintf(stderr, "EGL: %s\n", why.c_str()); return 77; }
    std::printf("GL: %s / %s; %d Hz, %.1f s per clip\n", reinterpret_cast<const char*>(glGetString(GL_RENDERER)),
                reinterpret_cast<const char*>(glGetString(GL_VERSION)), o.hz, o.seconds);
    std::filesystem::create_directories(o.dir);
    std::string sphere_why;
    auto sphere = VideoSphere::create(sphere_why);
    if (!sphere) { std::fprintf(stderr, "sphere: %s\n", sphere_why.c_str()); return 1; }
    for (const auto& c : kClips) {
        if (!o.cases.empty() && std::find(o.cases.begin(), o.cases.end(), c.name) == o.cases.end()) continue;
        const std::string path = o.dir + "/" + c.name + ".mp4";
        if (!generate(o.ffmpeg, o.gpu, c, o.seconds + 6, path)) continue;
        const Json r = bench_clip(o, c, path, *sphere);
        if (o.json) std::printf("%s\n", r.dump().c_str()); else print_human(r);
        std::fflush(stdout);
    }
    if (o.restarts > 0) {
        const Clip& c = kClips[0];
        const std::string path = o.dir + "/" + c.name + ".mp4";
        if (generate(o.ffmpeg, o.gpu, c, o.seconds + 6, path)) {
            const Json r = bench_restarts(o, path, *sphere);
            if (o.json) std::printf("%s\n", r.dump().c_str()); else print_human(r);
        }
    }
    return 0;
}
