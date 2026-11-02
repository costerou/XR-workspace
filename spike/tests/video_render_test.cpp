// VR video on a surfaceless EGL context (no display, window or compositor):
//  1. the projection pass (VideoSphere) against the CPU maths (video_uv) for every projection/stereo layout, both eyes
//     and a turned head, on a gradient texture;
//  2. the controls card drawn by the real GlHud never exceeds the OLED limit;
//  3. with libmpv and ffmpeg: clips generated here (never downloaded) in several codecs and layouts are decoded by the
//     real VideoPlayer, checked for orientation, pause, seek and end, and projected through VideoSphere;
//  4. a web video as the run relays it (tools/native_spike/web_video.py, docs/delightxr.md): ffmpeg copying a clip into
//     NUT on a pipe, whose read end goes to the same VideoPlayer (no seeking on a pipe: the run restarts the relay).
// Exit 77 (skipped) without EGL/GLES. argv: [ffmpeg path] [scratch dir].
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <filesystem>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>
#include "xr/video.hpp"
#include "hud_gl.hpp"
#include "video_gl.hpp"
#include "video_player.hpp"

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

bool make_context() {
    auto get_display = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
    if (!get_display) return false;
    EGLDisplay d = get_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
    if (d == EGL_NO_DISPLAY || !eglInitialize(d, nullptr, nullptr) || !eglBindAPI(EGL_OPENGL_ES_API)) return false;
    const EGLint config_attribs[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_NONE}; EGLConfig config; EGLint n = 0;
    if (!eglChooseConfig(d, config_attribs, &config, 1, &n) || n < 1) return false;
    const EGLint context_attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 2, EGL_NONE};  // as the renderer asks (gl.cpp)
    EGLContext c = eglCreateContext(d, config, EGL_NO_CONTEXT, context_attribs);
    return c != EGL_NO_CONTEXT && eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c);
}
struct Target { GLuint fb = 0, rb = 0; int w = 0, h = 0; };
Target target(int w, int h) {
    Target t{0, 0, w, h};
    glGenFramebuffers(1, &t.fb); glBindFramebuffer(GL_FRAMEBUFFER, t.fb);
    glGenRenderbuffers(1, &t.rb); glBindRenderbuffer(GL_RENDERBUFFER, t.rb); glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8_OES, w, h);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, t.rb);
    return t;
}
std::vector<std::uint8_t> read(int w, int h) {
    std::vector<std::uint8_t> px(std::size_t(w) * h * 4);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px.data());
    return px;
}
const std::uint8_t* at(const std::vector<std::uint8_t>& px, int w, int x, int y) { return &px[(std::size_t(y) * w + x) * 4]; }

// 1. Gradient texture: R = u, G = v (v = 0 at the top: texture row 0 is uploaded first = t 0).
void projection_matches_cpu() {
    constexpr int T = 256;
    std::vector<std::uint8_t> tex(std::size_t(T) * T * 4);
    for (int y = 0; y < T; ++y) for (int x = 0; x < T; ++x) {
        auto* p = &tex[(std::size_t(y) * T + x) * 4]; p[0] = std::uint8_t(x); p[1] = std::uint8_t(y); p[2] = 200; p[3] = 255;
    }
    GLuint id = 0; glGenTextures(1, &id); glBindTexture(GL_TEXTURE_2D, id);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, T, T, 0, GL_RGBA, GL_UNSIGNED_BYTE, tex.data());
    std::string why;
    auto sphere = VideoSphere::create(why);
    CHECK(sphere);
    if (!sphere) { std::fprintf(stderr, "  %s\n", why.c_str()); return; }
    constexpr int W = 240, H = 150;
    [[maybe_unused]] const auto t = target(W, H);
    const float c = std::cos(0.6f), s = std::sin(0.6f);
    const Mat3 views[] = {{1, 0, 0, 0, 1, 0, 0, 0, 1}, {c, 0, s, 0, 1, 0, -s, 0, c}, {1, 0, 0, 0, c, -s, 0, s, c}};
    const VideoFormat formats[] = {{}, {VideoProjection::equirect360, VideoStereo::tb, 180}, {VideoProjection::equirect360, VideoStereo::sbs, 180},
                                   {VideoProjection::equirect180, VideoStereo::sbs, 180}, {VideoProjection::equirect180, VideoStereo::mono, 180},
                                   {VideoProjection::fisheye, VideoStereo::sbs, 190}, {VideoProjection::fisheye, VideoStereo::tb, 220}};
    const float hw = 3.0f, hh = 2.0f;  // ±72° x ±63°: with the turned heads, 180° edges and fisheye circles fall inside the view
    int compared = 0, outside = 0;
    for (const auto& f : formats) for (int eye = 0; eye < 2; ++eye) for (const auto& view : views) {
        glViewport(0, 0, W, H); glClearColor(1, 0, 1, 1); glClear(GL_COLOR_BUFFER_BIT);
        sphere->draw(id, false, f, eye, view, hw, hh);
        const auto px = read(W, H);
        for (int y = 3; y < H; y += 9) for (int x = 3; x < W; x += 9) {
            const float nx = (x + 0.5f) / W * 2 - 1, ny = (y + 0.5f) / H * 2 - 1;  // read row 0 = bottom = ndc -1
            const auto uv = video_uv(f, view_ray(view, nx, ny, hw, hh), eye);
            const auto* p = at(px, W, x, y);
            if (!uv) {
                // Outside the picture: black (allow a sliver at the boundary).
                bool inside_near = false;
                for (const auto& [dx, dy] : {std::pair{0.03f, 0.f}, {-0.03f, 0.f}, {0.f, 0.03f}, {0.f, -0.03f}})
                    inside_near |= video_uv(f, view_ray(view, nx + dx, ny + dy, hw, hh), eye).has_value();
                if (!inside_near) { CHECK(p[0] == 0 && p[1] == 0 && p[2] == 0); ++outside; }
                continue;
            }
            const auto rect = eye_rect(f.stereo, eye);
            const float lu = ((*uv)[0] - rect.x) / rect.w, lv = ((*uv)[1] - rect.y) / rect.h;
            if (lu < 0.03f || lu > 0.97f || lv < 0.03f || lv > 0.97f) continue;  // texel boundaries, the 360° seam
            const float eu = (*uv)[0] * T - 0.5f, ev = (*uv)[1] * T - 0.5f;
            const bool ok = std::fabs(p[0] - eu) <= 1.6f && std::fabs(p[1] - ev) <= 1.6f && p[2] == 200;
            if (!ok) std::fprintf(stderr, "  format %d/%d eye %d at (%d,%d): got %d %d, expected %.1f %.1f\n", int(f.projection), int(f.stereo), eye, x, y, p[0], p[1], eu, ev);
            CHECK(ok);
            ++compared;
        }
    }
    CHECK(compared > 2000 && outside > 100);
    std::printf("projection: %d samples match the CPU maths, %d outside-picture samples black\n", compared, outside);
    glDeleteTextures(1, &id);
}

// 2. Controls card through the real GlHud over black: nothing brighter than kCardMaxLevel.
void card_is_dim() {
    constexpr int W = 1536, H = 960;
    [[maybe_unused]] const auto t = target(W, H);
    auto hud = GlHud::create();
    CHECK(hud);
    if (!hud) return;
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    VideoControlsOverlay overlay; overlay.set_font(std::move(font));
    overlay.set_visible(true, 0);
    VideoCardState s{"Concert_VR180_LR.mp4", {VideoProjection::equirect180, VideoStereo::sbs, 180}, VideoState::playing, 61, 3725, ""};
    overlay.update(s, 0);
    glViewport(0, 0, W, H); glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT);
    hud->draw(overlay.image(), overlay.generation(), overlay.alpha(1000), W, H);
    const auto px = read(W, H);
    std::uint8_t brightest = 0; std::size_t lit = 0;
    for (std::size_t i = 0; i < px.size(); i += 4) {
        for (int k = 0; k < 3; ++k) brightest = std::max(brightest, px[i + std::size_t(k)]);
        lit += px[i] || px[i + 1] || px[i + 2];
    }
    CHECK(brightest <= kCardMaxLevel && brightest > 80);
    CHECK(lit > 10000);
    std::printf("card: brightest channel %d (limit %d), %zu lit pixels\n", brightest, kCardMaxLevel, lit);
}

#ifdef XR_HAVE_MPV
struct Clip { const char* name; const char* encoder; const char* extra; int w, h; VideoFormat format; };
// Quadrants (picture coordinates): top-left yellow, top-right green, bottom-left red, bottom-right dark blue.
std::string quadrants(int w, int h) {
    return "nullsrc=s=" + std::to_string(w) + "x" + std::to_string(h) + ":r=30:d=3,format=yuv444p,"
           "geq=lum='if(lt(Y,H/2),if(lt(X,W/2),210,145),if(lt(X,W/2),81,29))':"
           "cb='if(lt(Y,H/2),if(lt(X,W/2),16,54),if(lt(X,W/2),90,240))':"
           "cr='if(lt(Y,H/2),if(lt(X,W/2),146,34),if(lt(X,W/2),240,110))'";
}
bool generate(const std::string& ffmpeg, const Clip& c, const std::string& out) {
    const std::string cmd = "'" + ffmpeg + "' -nostdin -hide_banner -loglevel error -y -f lavfi -i \"" + quadrants(c.w, c.h) +
                            "\" -f lavfi -i \"sine=frequency=440:duration=3\" -shortest -pix_fmt yuv420p -g 30 -c:v " + c.encoder + " " + c.extra +
                            " -c:a " + (out.ends_with(".webm") ? "libopus" : "aac") + " '" + out + "' >/dev/null 2>&1";
    return std::system(cmd.c_str()) == 0 && std::filesystem::exists(out);
}
enum Colour { yellow, green, red, blue, other };
Colour classify(const std::uint8_t* p) {
    const int r = p[0], g = p[1], b = p[2];
    if (r > 170 && g > 170 && b < 90) return yellow;
    if (r < 90 && g > 170 && b < 90) return green;
    if (r > 170 && g < 90 && b < 90) return red;
    if (r < 90 && g < 90 && b > 90) return blue;
    return other;
}
const char* name(Colour c) { return c == yellow ? "yellow" : c == green ? "green" : c == red ? "red" : c == blue ? "blue" : "other"; }
template <class F> bool wait_for(VideoPlayer& p, F done, double seconds) {
    const auto end = std::chrono::steady_clock::now() + std::chrono::duration<double>(seconds);
    while (std::chrono::steady_clock::now() < end) {
        if (!p.update()) return false;
        if (done()) return true;
        if (std::getenv("XR_VIDEO_DEBUG")) std::fprintf(stderr, "    pos %.2f dur %.2f state %s %s\n", p.position_s(), p.duration_s(), video_state_name(p.state()), p.report().c_str());
        std::this_thread::sleep_for(std::chrono::milliseconds(8));
    }
    return false;
}

void decode(const std::string& ffmpeg, const std::string& dir) {
    if (ffmpeg.empty() || !std::filesystem::exists(ffmpeg)) { std::puts("decode: SKIP (no ffmpeg to generate clips)"); return; }
    std::filesystem::create_directories(dir);
    const Clip clips[] = {
        {"h264-360-mono.mp4", "libx264", "-preset ultrafast", 1024, 512, {}},
        {"hevc-180-sbs.mkv", "libx265", "-preset ultrafast -x265-params log-level=error", 1024, 512, {VideoProjection::equirect180, VideoStereo::sbs, 180}},
        {"vp9-360-tb.webm", "libvpx-vp9", "-deadline realtime -cpu-used 8", 512, 512, {VideoProjection::equirect360, VideoStereo::tb, 180}},
        {"av1-fisheye-sbs.mkv", "libsvtav1", "-preset 12", 1024, 512, {VideoProjection::fisheye, VideoStereo::sbs, 190}},
        {"h264-8k-360-mono.mp4", "libx264", "-preset ultrafast", 7680, 3840, {}},  // beyond H.264 hardware limits: software
        {"hevc-8k-180-sbs.mp4", "libx265", "-preset ultrafast -tag:v hvc1 -x265-params log-level=error", 8192, 4096,
         {VideoProjection::equirect180, VideoStereo::sbs, 180}},
    };
    int decoded = 0;
    for (const auto& c : clips) {
        const std::string path = dir + "/" + c.name;
        if (!generate(ffmpeg, c, path)) { std::printf("decode %s: SKIP (encoder %s unavailable)\n", c.name, c.encoder); continue; }
        const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
        CHECK(fd >= 0);
        std::string why;
        auto player = VideoPlayer::open(fd, {false, true, nullptr}, why);
        CHECK(player);
        if (!player) { std::fprintf(stderr, "  %s: %s\n", c.name, why.c_str()); continue; }
        const bool playing = wait_for(*player, [&] { return player->texture() && player->state() == VideoState::playing; }, 20);
        CHECK(playing);
        if (!playing) { std::fprintf(stderr, "  %s: state %s %s\n", c.name, video_state_name(player->state()), player->error().c_str()); continue; }
        const auto tw = int(player->width()), th = int(player->height());
        CHECK(tw == std::min(c.w, 8192) && th == std::min(c.h, 8192));
        // Read the decoded frame back: texture row t = 0 must be the picture's top (flip_y() false).
        GLuint fb = 0; glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, player->texture(), 0);
        auto probe = [&](float u, float v) {
            std::uint8_t p[4]{};
            const int y = player->flip_y() ? int((1 - v) * th) : int(v * th);
            glReadPixels(int(u * tw), y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, p);
            return classify(p);
        };
        const Colour tl = probe(0.25f, 0.25f), tr = probe(0.75f, 0.25f), bl = probe(0.25f, 0.75f), br = probe(0.75f, 0.75f);
        const bool upright = tl == yellow && tr == green && bl == red && br == blue;
        if (!upright) std::fprintf(stderr, "  %s: tl %s tr %s bl %s br %s\n", c.name, name(tl), name(tr), name(bl), name(br));
        CHECK(upright);
        glBindFramebuffer(GL_FRAMEBUFFER, 0); glDeleteFramebuffers(1, &fb);
        // Through the projection pass: looking up-left lands in the top-left quadrant of the eye's image for 360° video.
        if (c.format.projection == VideoProjection::equirect360) {
            std::string sphere_why; auto sphere = VideoSphere::create(sphere_why);
            const auto t = target(64, 64);
            glViewport(0, 0, 64, 64);
            const Mat3 identity{1, 0, 0, 0, 1, 0, 0, 0, 1};
            sphere->draw(player->texture(), player->flip_y(), c.format, 0, identity, 1.f, 1.f);
            std::uint8_t p[4]{};
            glReadPixels(12, 52, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, p);  // upper left of the view
            const Colour seen = classify(p);
            const Colour expected = c.format.stereo == VideoStereo::tb ? yellow : yellow;  // TB left eye: the top half's top-left
            if (seen != expected) std::fprintf(stderr, "  %s projected: %s\n", c.name, name(seen));
            CHECK(c.format.stereo == VideoStereo::tb ? (seen == yellow || seen == green) : seen == yellow);
            glBindFramebuffer(GL_FRAMEBUFFER, 0); glDeleteFramebuffers(1, &t.fb); glDeleteRenderbuffers(1, &t.rb);
        }
        // Controls: pause, seek, end.
        player->toggle_pause();
        CHECK(wait_for(*player, [&] { return player->state() == VideoState::paused; }, 5));
        player->toggle_pause();
        CHECK(wait_for(*player, [&] { return player->state() == VideoState::playing; }, 5));
        CHECK(player->duration_s() > 2.5 && player->duration_s() < 3.5);
        const double before = player->position_s();
        player->seek(1);
        CHECK(wait_for(*player, [&] { return player->position_s() >= before + 0.9; }, 5));
        // Plays to the end: keep-open holds the last frame and reports ended; seeking back plays again.
        CHECK(wait_for(*player, [&] { return player->state() == VideoState::ended; }, 10));
        CHECK(player->texture() != 0);
        player->seek(-10);
        CHECK(wait_for(*player, [&] { return player->position_s() < 1.0 && player->state() != VideoState::ended; }, 10));
        std::printf("decode %s: %dx%d -> %dx%d texture, %s\n", c.name, c.w, c.h, tw, th, player->report().c_str());
        ++decoded;
    }
    // A web video as the run relays it: `ffmpeg -i <stream> -c copy -f nut pipe:1` (web_video.relay_argv) into a pipe.
    {
        const std::string clip = dir + "/h264-360-mono.mp4";
        int p[2];
        CHECK(::pipe2(p, O_CLOEXEC) == 0);
        posix_spawn_file_actions_t fa; posix_spawn_file_actions_init(&fa);
        posix_spawn_file_actions_adddup2(&fa, p[1], 1);
        posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
        const char* argv[] = {ffmpeg.c_str(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", clip.c_str(), "-sn", "-dn",
                              "-c", "copy", "-f", "nut", "pipe:1", nullptr};
        pid_t pid = -1;
        const bool spawned = std::filesystem::exists(clip) &&
                             posix_spawn(&pid, ffmpeg.c_str(), &fa, nullptr, const_cast<char* const*>(argv), environ) == 0;
        posix_spawn_file_actions_destroy(&fa);
        ::close(p[1]);
        if (!spawned) { ::close(p[0]); std::puts("decode relay-pipe: SKIP (no clip or ffmpeg)"); }
        else {
            std::string why;
            auto player = VideoPlayer::open(p[0], {false, true, nullptr}, why);  // the player owns the read end now
            CHECK(player);
            if (player) {
                const bool playing = wait_for(*player, [&] { return player->texture() && player->state() == VideoState::playing; }, 20);
                CHECK(playing);
                CHECK(player->width() == 1024 && player->height() == 512);
                GLuint fb = 0; glGenFramebuffers(1, &fb); glBindFramebuffer(GL_FRAMEBUFFER, fb);
                glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, player->texture(), 0);
                std::uint8_t px[4]{};
                glReadPixels(256, 128, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
                CHECK(classify(px) == yellow);  // upright: the picture's top-left quadrant
                glBindFramebuffer(GL_FRAMEBUFFER, 0); glDeleteFramebuffers(1, &fb);
                player->toggle_pause();
                CHECK(wait_for(*player, [&] { return player->state() == VideoState::paused; }, 5));
                player->toggle_pause();
                CHECK(wait_for(*player, [&] { return player->state() == VideoState::ended; }, 15));  // end of the pipe: held
                CHECK(player->texture() != 0 && player->position_s() > 2.0);
                std::printf("decode relay-pipe (nut): %s\n", player->report().c_str());
            }
            int status = 0; ::waitpid(pid, &status, 0);
            CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        }
    }
    // Not a video: reported as an error, never a crash.
    {
        const std::string junk = dir + "/not-a-video.mp4";
        if (FILE* f = std::fopen(junk.c_str(), "wb")) { std::fputs("this is not a video file\n", f); std::fclose(f); }
        std::string why;
        auto player = VideoPlayer::open(::open(junk.c_str(), O_RDONLY | O_CLOEXEC), {false, true, nullptr}, why);
        CHECK(player);
        if (player) {
            CHECK(wait_for(*player, [&] { return player->state() == VideoState::error; }, 10));
            CHECK(!player->error().empty() && player->texture() == 0);
            std::printf("decode not-a-video: error \"%s\"\n", player->error().c_str());
        }
    }
    CHECK(decoded >= 1);
}
#endif
}  // namespace

int main(int argc, char** argv) {
    if (!make_context()) { std::puts("SKIP: no surfaceless EGL / GLES2 context"); return 77; }
    std::printf("GL: %s / %s\n", reinterpret_cast<const char*>(glGetString(GL_RENDERER)), reinterpret_cast<const char*>(glGetString(GL_VERSION)));
    projection_matches_cpu();
    card_is_dim();
#ifdef XR_HAVE_MPV
    decode(argc > 1 ? argv[1] : "", argc > 2 ? argv[2] : "video-samples");
#else
    (void)argc; (void)argv;
    std::puts("decode: SKIP (built without libmpv)");
    std::string why;
    CHECK(!VideoPlayer::open(-1, {}, why) && !why.empty());
#endif
    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("video_render: ok");
    return 0;
}
