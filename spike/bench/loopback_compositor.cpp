// Loopback compositor: runs the real xr-native-spike binary end to end against a minimal in-process Wayland server
// and measures the capture and frame delivery path (wl_surface frame callbacks -> commit, capture ready -> commit and
// -> present, renderer CPU, main-loop wakes, allocations).
//
// Offline only: no display, no glasses, no Hyprland. The renderer gets the same inherited sockets as from the Python
// supervisor (supervised control stream, Wayland socket, status datagrams). The server advertises one presentation
// output (DP-2) and up to kMaxDesktops managed outputs (XR-1..XR-4) with ext-image-copy-capture sessions. Each captured
// frame carries a 24-bit stamp (a 64x64 block at the source centre for SHM targets, a GPU clear for dmabuf targets); the
// stamp read back from the renderer's committed buffer at each tile centre says which capture frame it shows, and so
// which desktop (--maximize checks that the desktop a maximize names is the one in the middle of the glasses).
//
// Modes: cpu (no EGL: SHM capture + CPU composition), gl-shm (GL composition, SHM capture + texture upload),
// gl-dmabuf (GL composition, zero-copy dmabuf capture), pattern (presentation-only animated test pattern).
// The GL modes use the GPU render node through EGL/GBM, like the surfaceless render tests; exit 77 when unavailable.
#include <wayland-server.h>
#include "presentation-time-server-protocol.h"
#include "ext-image-capture-source-v1-server-protocol.h"
#include "ext-image-copy-capture-v1-server-protocol.h"
#include "linux-dmabuf-v1-server-protocol.h"
#define namespace layer_namespace
#include "wlr-layer-shell-unstable-v1-server-protocol.h"
#include "fractional-scale-v1-server-protocol.h"
#include "viewporter-server-protocol.h"
#undef namespace
#include "xr/capture.hpp"
#include "xr/desktops.hpp"
#include "xr/ipc.hpp"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <gbm.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstdarg>
#include <cmath>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <climits>
#include <optional>
#include <poll.h>
#include <sstream>
#include <string>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <sys/wait.h>
#include <unistd.h>
#include <unordered_set>
#include <vector>

namespace {
constexpr std::uint32_t kOutW = 1920, kOutH = 1200, kSrcW = 1920, kSrcH = 1080;
constexpr std::uint32_t kDrmXr24 = 0x34325258, kDrmAr24 = 0x34325241;
constexpr std::uint64_t kModInvalid = 0x00ffffffffffffffULL, kModLinear = 0;

std::int64_t now_ns() {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::int64_t(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

struct Options {
    std::string renderer, mode = "gl-dmabuf", malloc_shim, malloc_trace;
    double seconds = 5, warmup = 1.5, hz = 120, capture_hz = 119.7;
    int outputs = 3;
    bool check = false, tracking = false;
    std::string environment;  // a background's name, sent as an `environment` frame at the start; empty: none
    bool open_panel = false;  // the first desktop's placement panel open from the start (its view shift reported and, with --check, required)
    // Constraint churn, as Hyprland sends it on monitor mode events (a layout change re-checks every monitor rule; a config
    // reload takes each XR-n output through the default rule and back): "bounce" resends each session's constraints as
    // another size and straight back in one flush, plus an unchanged batch; "resize" alternates the source size, failing
    // frames whose buffer no longer fits (buffer_constraints). Every kChurnMs of the measured window.
    std::string churn;
    // Fractional scale (120ths, 0: none): wp_viewporter and wp_fractional_scale_manager_v1 are advertised and the renderer's
    // surface is told this preferred scale, as Hyprland does for the glasses at 1.25 (150).
    std::uint32_t fractional_scale = 0;
    // Maximize XR-N (1..outputs, 0: never) as the measured window starts, with the frames the supervisor sends (N selected
    // in the overview, then Ctrl+Alt+Up). --check: once the zoom has landed, every frame has XR-N's own frame in the middle.
    // GL modes only: CPU composition has no zoom (always side_by_side). There is no pose feed, so no aim (set_aim runs only
    // when tracked) and the desktop fills the view from where it sits: SHM frames are stamped whole, as a dmabuf clear is.
    int maximize = 0;
    bool pointer_look = false;  // flip the dot and the Serif Beam and count the frames each flip takes to land
    bool edge_pan = false;  // --edge-pan, with --maximize N: get to desktop N from desktop 1 by the hand's pan, not a maximize of it
    // --feed-drop S, with --feed-script PATH: head tracking on a simulated driver feed (tests/feed_script.cpp) in the renderer's
    // private /tmp and IPC namespace, gone for S seconds with two disconnects. --check: the renderer stays up and resumes.
    std::string feed_script;
    double feed_drop = 0;
    bool close_control = false;  // --close-control: the supervisor's end closes in the window. --check: the renderer stops and says why.
    bool bad_control = false;  // --bad-control: every kind of bad frame in the window, with a good one after each kind
    std::string control_errors = "fatal";  // the renderer's rule (ipc.hpp ControlStream): tests run strict; `--control-errors log` is the shipped one
    double video_loss = 0;  // --video-loss S: S seconds in, DP-2 leaves as when the glasses' display link drops. --check: the renderer ends by itself.
};
constexpr double kChurnMs = 150;
constexpr double kMaximizeSettleMs = 500;  // well past the zoom (view.hpp kViewEaseMs)
constexpr double kEdgePanLandedMs = 2000;  // --edge-pan: the maximize, the hand's frames and the landing (0.74 s at most)
using Stamps = std::array<std::uint32_t, xr::kMaxDesktops>;  // which capture frame each tile of a buffer shows

// A wl_buffer reference that forgets the buffer when the client destroys it.
struct BufRef {
    wl_resource* r = nullptr;
    wl_listener l{};
    BufRef() { wl_list_init(&l.link); l.notify = gone; }
    BufRef(const BufRef&) = delete;
    BufRef& operator=(const BufRef&) = delete;
    ~BufRef() { reset(); }
    static void gone(wl_listener* listener, void*) {
        auto* self = reinterpret_cast<BufRef*>(reinterpret_cast<char*>(listener) - offsetof(BufRef, l));
        wl_list_remove(&self->l.link); wl_list_init(&self->l.link); self->r = nullptr;
    }
    void set(wl_resource* resource) {
        if (resource == r) return;
        reset();
        if (resource) { r = resource; wl_resource_add_destroy_listener(resource, &l); }
    }
    void reset() { if (r) { wl_list_remove(&l.link); wl_list_init(&l.link); r = nullptr; } }
};

// ---- GPU helper (GL modes): EGL on the render node, dmabuf import, clear, 1-row readback ----
struct Gpu {
    int fd = -1; gbm_device* gbm = nullptr; EGLDisplay dpy = EGL_NO_DISPLAY; EGLContext ctx = EGL_NO_CONTEXT;
    dev_t dev = 0;
    PFNEGLCREATEIMAGEKHRPROC create_image = nullptr; PFNEGLDESTROYIMAGEKHRPROC destroy_image = nullptr;
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target = nullptr; PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_mods = nullptr;
    bool init(std::string& why) {
        for (const char* node : {"/dev/dri/renderD128", "/dev/dri/renderD129"}) {
            struct stat st{};
            if (stat(node, &st) != 0 || !S_ISCHR(st.st_mode)) continue;
            fd = ::open(node, O_RDWR | O_CLOEXEC);
            if (fd < 0) continue;
            dev = st.st_rdev;
            break;
        }
        if (fd < 0) { why = "no render node"; return false; }
        gbm = gbm_create_device(fd);
        if (!gbm) { why = "gbm"; return false; }
        dpy = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gbm, nullptr);
        if (dpy == EGL_NO_DISPLAY || !eglInitialize(dpy, nullptr, nullptr)) { why = "egl display"; return false; }
        const char* ext = eglQueryString(dpy, EGL_EXTENSIONS);
        if (!ext || !std::strstr(ext, "EGL_EXT_image_dma_buf_import_modifiers") || !std::strstr(ext, "EGL_KHR_no_config_context")) {
            why = "egl extensions"; return false;
        }
        eglBindAPI(EGL_OPENGL_ES_API);
        const EGLint attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attribs);
        if (ctx == EGL_NO_CONTEXT || !eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { why = "egl context"; return false; }
        create_image = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
        destroy_image = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
        image_target = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(eglGetProcAddress("glEGLImageTargetTexture2DOES"));
        query_mods = reinterpret_cast<PFNEGLQUERYDMABUFMODIFIERSEXTPROC>(eglGetProcAddress("eglQueryDmaBufModifiersEXT"));
        if (!create_image || !destroy_image || !image_target || !query_mods) { why = "egl procs"; return false; }
        return true;
    }
    std::vector<std::uint64_t> modifiers(std::uint32_t fourcc) const {
        EGLint n = 0;
        if (!query_mods(dpy, EGLint(fourcc), 0, nullptr, nullptr, &n) || n <= 0) return {};
        std::vector<EGLuint64KHR> mods(static_cast<std::size_t>(n)); std::vector<EGLBoolean> external(static_cast<std::size_t>(n));
        if (!query_mods(dpy, EGLint(fourcc), n, mods.data(), external.data(), &n)) return {};
        std::vector<std::uint64_t> out;
        for (EGLint i = 0; i < n; ++i) if (!external[std::size_t(i)]) out.push_back(mods[std::size_t(i)]);
        return out;
    }
    ~Gpu() {
        if (dpy != EGL_NO_DISPLAY) {
            eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            if (ctx != EGL_NO_CONTEXT) eglDestroyContext(dpy, ctx);
            eglTerminate(dpy);
        }
        if (gbm) gbm_device_destroy(gbm);
        if (fd >= 0) ::close(fd);
    }
};
Gpu* g_gpu = nullptr;

struct Dmabuf {
    int planes = 0; std::array<int, 4> fds{-1, -1, -1, -1}; std::array<std::uint32_t, 4> offsets{}, strides{};
    std::uint64_t modifier = kModInvalid; std::uint32_t format = 0; std::int32_t width = 0, height = 0;
    EGLImageKHR image = EGL_NO_IMAGE_KHR; GLuint tex = 0, fbo = 0; bool imported = false, import_failed = false;
    ~Dmabuf() {
        if (g_gpu && g_gpu->dpy != EGL_NO_DISPLAY) {
            if (fbo) glDeleteFramebuffers(1, &fbo);
            if (tex) glDeleteTextures(1, &tex);
            if (image != EGL_NO_IMAGE_KHR) g_gpu->destroy_image(g_gpu->dpy, image);
        }
        for (int fd : fds) if (fd >= 0) ::close(fd);
    }
    bool import() {
        if (imported) return true;
        if (import_failed || !g_gpu) return false;
        import_failed = true;
        static const EGLint fd_k[] = {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE1_FD_EXT, EGL_DMA_BUF_PLANE2_FD_EXT, EGL_DMA_BUF_PLANE3_FD_EXT};
        static const EGLint off_k[] = {EGL_DMA_BUF_PLANE0_OFFSET_EXT, EGL_DMA_BUF_PLANE1_OFFSET_EXT, EGL_DMA_BUF_PLANE2_OFFSET_EXT, EGL_DMA_BUF_PLANE3_OFFSET_EXT};
        static const EGLint pitch_k[] = {EGL_DMA_BUF_PLANE0_PITCH_EXT, EGL_DMA_BUF_PLANE1_PITCH_EXT, EGL_DMA_BUF_PLANE2_PITCH_EXT, EGL_DMA_BUF_PLANE3_PITCH_EXT};
        static const EGLint lo_k[] = {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT};
        static const EGLint hi_k[] = {EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE2_MODIFIER_HI_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_HI_EXT};
        std::vector<EGLint> a = {EGL_WIDTH, width, EGL_HEIGHT, height, EGL_LINUX_DRM_FOURCC_EXT, EGLint(format)};
        for (int p = 0; p < planes; ++p) {
            a.insert(a.end(), {fd_k[p], fds[std::size_t(p)], off_k[p], EGLint(offsets[std::size_t(p)]), pitch_k[p], EGLint(strides[std::size_t(p)])});
            if (modifier != kModInvalid)
                a.insert(a.end(), {lo_k[p], EGLint(modifier & 0xffffffffu), hi_k[p], EGLint(modifier >> 32)});
        }
        a.push_back(EGL_NONE);
        image = g_gpu->create_image(g_gpu->dpy, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, a.data());
        if (image == EGL_NO_IMAGE_KHR) return false;
        glGenTextures(1, &tex); glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        g_gpu->image_target(GL_TEXTURE_2D, image);
        glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE || glGetError() != GL_NO_ERROR) return false;
        imported = true; import_failed = false;
        return true;
    }
    bool clear(std::uint32_t rgb) {
        if (!import()) return false;
        glBindFramebuffer(GL_FRAMEBUFFER, fbo); glViewport(0, 0, width, height);
        glClearColor(float((rgb >> 16) & 255) / 255.f, float((rgb >> 8) & 255) / 255.f, float(rgb & 255) / 255.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);
        glFlush();  // implicit dmabuf fencing orders the renderer's sampling after this clear; no CPU wait here
        return true;
    }
    // One row (memory row `y`, top-down) as XRGB values.
    bool read_row(std::int32_t y, std::vector<std::uint32_t>& out) {
        if (!import() || y < 0 || y >= height) return false;
        std::vector<std::uint8_t> rgba(std::size_t(width) * 4);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glReadPixels(0, y, width, 1, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        if (glGetError() != GL_NO_ERROR) return false;
        out.resize(std::size_t(width));
        for (std::size_t x = 0; x < out.size(); ++x)
            out[x] = (std::uint32_t(rgba[x * 4]) << 16) | (std::uint32_t(rgba[x * 4 + 1]) << 8) | rgba[x * 4 + 2];
        return true;
    }
};

// ---- Statistics ----
struct Dist {
    std::vector<double> v;
    void add(double x) { v.push_back(x); }
    nlohmann::json json(int digits = 3) const {
        if (v.empty()) return {{"count", 0}};
        auto s = v; std::sort(s.begin(), s.end());
        auto q = [&](double p) { return s[std::min(s.size() - 1, std::size_t(p * double(s.size() - 1) + 0.5))]; };
        double sum = 0; for (double x : s) sum += x;
        const double scale = std::pow(10.0, digits);
        auto r = [&](double x) { return std::round(x * scale) / scale; };
        return {{"count", s.size()}, {"mean", r(sum / double(s.size()))}, {"p50", r(q(0.5))}, {"p95", r(q(0.95))},
                {"p99", r(q(0.99))}, {"max", r(s.back())}};
    }
};

struct Server;
Server* g = nullptr;

struct Output { std::string name; std::uint32_t w, h; std::int32_t refresh_mhz; wl_global* global = nullptr; std::vector<wl_resource*> resources; };
struct Layer;
struct Surface {
    wl_resource* res = nullptr;
    BufRef pending_buf; bool pending_attached = false;
    std::vector<wl_resource*> pending_frames, pending_fb;
    bool has_commit = false, commit_has_buffer = false; BufRef commit_buf;
    std::vector<wl_resource*> commit_frames, commit_fb;
    Stamps commit_stamps{};  // SHM output: stamps read at commit
    bool commit_stamps_valid = false;
    BufRef shown;
    std::int64_t shown_ns = 0; bool shown_unread = false;  // GPU output: read back at the next latch, once rendered
    Layer* layer = nullptr;
};
struct Layer { wl_resource* res = nullptr; Surface* surface = nullptr; bool configured = false; std::uint32_t serial = 0; };
struct CapSession { wl_resource* res = nullptr; int output = -1; bool delivered = false; std::uint32_t w = kSrcW, h = kSrcH; };
struct CapFrame { wl_resource* res = nullptr; CapSession* session = nullptr; BufRef buf; bool captured = false; };
struct Params { wl_resource* res = nullptr; std::unique_ptr<Dmabuf> d = std::make_unique<Dmabuf>(); bool used = false; };
struct StampInfo { std::uint32_t stamp = 0; std::int64_t ready_ns = 0; int output = -1; };

struct Server {
    Options opt;
    wl_display* display = nullptr;
    wl_client* client = nullptr;
    std::vector<std::unique_ptr<Output>> outputs;
    std::vector<Surface*> surfaces;
    std::vector<CapSession*> sessions;
    std::vector<CapFrame*> captured;  // capture requested, waiting for the next source frame
    std::unordered_set<wl_resource*> live;  // callbacks and feedback still owned by the client
    bool dmabuf_capture = false, gl = false;
    std::vector<std::uint64_t> xr24_mods, ar24_mods;
    int format_table_fd = -1; std::size_t format_table_size = 0;
    std::vector<std::pair<std::uint32_t, std::uint64_t>> format_table;
    std::uint32_t next_serial = 1, next_stamp = 1;
    std::array<StampInfo, 1 << 16> stamps{};
    std::uint64_t vblank_seq = 0;
    std::int64_t last_done_ns = -1;  // frame callbacks sent at this time, no commit yet
    std::vector<xr::Rect> tiles;     // where each source lands in the committed buffer (stamp readback)
    bool measuring = false;
    // measured window
    std::uint64_t commits = 0, latched = 0, presented_fb = 0, discarded_fb = 0, callbacks = 0, missed_ticks = 0;
    std::array<std::uint64_t, xr::kMaxDesktops> captured_frames{}, shown_frames{};
    Stamps last_shown{};
    // --pointer-look: the dot sits just right of the middle of the first desktop, so the disc covers that pixel and the Serif
    // Beam's stem does not. look_sent: when the last flip went out and what had landed by then; look_frames[n]: flips that
    // first showed n frames later. A buffer already committed at the flip counts as frame 1, so 2 is "the very next draw".
    bool look_beam = false, look_waiting = false, look_at_least_one = false;
    std::int64_t look_sent_ns = 0;
    std::uint64_t look_sent_latched = 0;
    std::array<std::uint64_t, 8> look_frames{};
    void account_look(const Stamps& got, std::uint64_t serial) {
        const std::uint32_t px = got[0];
        const std::uint32_t r = (px >> 16) & 255, g = (px >> 8) & 255, b = px & 255;
        const bool disc = r == g && g == b && r > 0;  // the dot's grey; a capture stamp is coloured
        if (!look_waiting || disc == look_beam) return;
        look_waiting = false;
        ++look_frames[std::min<std::size_t>(serial - look_sent_latched, look_frames.size() - 1)];
    }
    // --maximize: once the zoom has landed (maximized_ns), frames with each desktop's frame in the middle, and with none.
    std::int64_t maximized_ns = 0;
    std::array<std::uint64_t, xr::kMaxDesktops> centre_frames{};
    std::uint64_t centre_none = 0;
    Dist callback_to_commit_us, capture_to_commit_ms, capture_to_present_ms, commit_interval_ms;
    std::int64_t last_commit_ns = -1;
    std::vector<std::uint32_t> row;
    std::uint64_t readback_failures = 0;
    std::uint64_t blank_frames = 0, churn_events = 0, constraint_failures = 0;  // blank: a tile that had shown a frame shows none
    std::int32_t buffer_w = 0, buffer_h = 0, dest_w = -1, dest_h = -1;  // the last committed buffer; the viewport destination

    StampInfo* lookup(std::uint32_t stamp) {
        auto& s = stamps[stamp & 0xffff];
        return s.stamp == stamp && stamp ? &s : nullptr;
    }
    wl_resource* output_resource(int index) {
        for (auto* r : outputs[std::size_t(index)]->resources) if (wl_resource_get_client(r) == client) return r;
        return nullptr;
    }
    // A canvas (surface-logical) coordinate in a buffer `buffer_side` px across a `logical_side` surface (a fractional scale).
    static std::uint32_t to_buffer(std::uint32_t v, std::uint32_t buffer_side, std::uint32_t logical_side) {
        return buffer_side == logical_side ? v : std::uint32_t(std::uint64_t(v) * buffer_side / logical_side);
    }
    // Which capture frame each tile of `buffer` shows.
    bool read_stamps(wl_resource* buffer, Stamps& out) {
        out = {};
        if (tiles.empty()) return false;
        if (auto* shm = wl_shm_buffer_get(buffer)) {
            const auto w = std::uint32_t(wl_shm_buffer_get_width(shm)), h = std::uint32_t(wl_shm_buffer_get_height(shm));
            const auto stride = std::uint32_t(wl_shm_buffer_get_stride(shm)) / 4;
            wl_shm_buffer_begin_access(shm);
            const auto* px = static_cast<const std::uint32_t*>(wl_shm_buffer_get_data(shm));
            for (std::size_t i = 0; i < tiles.size() && i < out.size(); ++i) {
                const auto x = to_buffer(tiles[i].x + tiles[i].width / 2, w, kOutW), y = to_buffer(tiles[i].y + tiles[i].height / 2, h, kOutH);
                if (x < w && y < h) out[i] = px[std::size_t(y) * stride + x] & 0xffffffu;
            }
            wl_shm_buffer_end_access(shm);
            return true;
        }
        auto* d = static_cast<Dmabuf*>(wl_resource_get_user_data(buffer));
        if (!d || !g_gpu) return false;
        std::int32_t y_last = -1;
        for (std::size_t i = 0; i < tiles.size() && i < out.size(); ++i) {
            const auto x = std::int32_t(to_buffer(tiles[i].x + tiles[i].width / 2, std::uint32_t(d->width), kOutW)),
                       y = std::int32_t(to_buffer(tiles[i].y + tiles[i].height / 2, std::uint32_t(d->height), kOutH));
            if (y != y_last && !d->read_row(y, row)) { ++readback_failures; return false; }
            y_last = y;
            if (x >= 0 && std::size_t(x) < row.size()) out[i] = row[std::size_t(x)];
        }
        return true;
    }
    void account_shown(const Stamps& got, std::int64_t at_ns, Dist& dist, bool count_shown) {
        for (std::size_t i = 0; i < tiles.size() && i < got.size(); ++i) {
            if (!got[i] && last_shown[i] && count_shown && measuring) ++blank_frames;
            if (!got[i] || got[i] == last_shown[i]) continue;
            if (const auto* info = lookup(got[i]); info && measuring) dist.add(double(at_ns - info->ready_ns) / 1e6);
            if (count_shown) { last_shown[i] = got[i]; if (measuring) ++shown_frames[i]; }
        }
        if (count_shown && measuring && maximized_ns && at_ns >= maximized_ns) {
            const auto* info = lookup(got[0]);
            if (info && info->output >= 0 && std::size_t(info->output) < centre_frames.size()) ++centre_frames[std::size_t(info->output)];
            else ++centre_none;
        }
    }
};

void destroy_resource(wl_client*, wl_resource* r) { wl_resource_destroy(r); }
void forget_live(wl_resource* r) { if (g) g->live.erase(r); }

// ---- wl_buffer for dmabufs ----
const struct wl_buffer_interface buffer_impl = {destroy_resource};
void dmabuf_buffer_destroyed(wl_resource* r) { delete static_cast<Dmabuf*>(wl_resource_get_user_data(r)); }

// ---- wl_region / wl_surface / wl_compositor ----
void region_noop(wl_client*, wl_resource*, std::int32_t, std::int32_t, std::int32_t, std::int32_t) {}
const struct wl_region_interface region_impl = {destroy_resource, region_noop, region_noop};

Surface* surface_of(wl_resource* r) { return static_cast<Surface*>(wl_resource_get_user_data(r)); }
void surface_attach(wl_client*, wl_resource* r, wl_resource* buffer, std::int32_t, std::int32_t) {
    auto* s = surface_of(r); s->pending_buf.set(buffer); s->pending_attached = true;
}
void surface_damage(wl_client*, wl_resource*, std::int32_t, std::int32_t, std::int32_t, std::int32_t) {}
void surface_frame(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* cb = wl_resource_create(client, &wl_callback_interface, 1, id);
    if (!cb) { wl_client_post_no_memory(client); return; }
    wl_resource_set_implementation(cb, nullptr, nullptr, forget_live);
    g->live.insert(cb);
    surface_of(r)->pending_frames.push_back(cb);
}
void surface_region(wl_client*, wl_resource*, wl_resource*) {}
void send_discarded(std::vector<wl_resource*>& fbs) {
    for (auto* fb : fbs) if (g->live.contains(fb)) {
        wp_presentation_feedback_send_discarded(fb); wl_resource_destroy(fb);
        if (g->measuring) ++g->discarded_fb;
    }
    fbs.clear();
}
void surface_commit(wl_client*, wl_resource* r) {
    auto* s = surface_of(r);
    const std::int64_t t = now_ns();
    if (s->layer && !s->layer->configured) {
        s->layer->configured = true; s->layer->serial = g->next_serial++;
        zwlr_layer_surface_v1_send_configure(s->layer->res, s->layer->serial, kOutW, kOutH);
    }
    if (s->pending_attached && s->pending_buf.r) {
        if (s->commit_has_buffer && s->commit_buf.r && s->commit_buf.r != s->pending_buf.r && s->commit_buf.r != s->shown.r)
            wl_buffer_send_release(s->commit_buf.r);  // replaced before it was ever shown
        s->commit_buf.set(s->pending_buf.r); s->commit_has_buffer = true;
        if (auto* shm = wl_shm_buffer_get(s->pending_buf.r)) { g->buffer_w = wl_shm_buffer_get_width(shm); g->buffer_h = wl_shm_buffer_get_height(shm); }
        else if (auto* d = static_cast<Dmabuf*>(wl_resource_get_user_data(s->pending_buf.r))) { g->buffer_w = d->width; g->buffer_h = d->height; }
        s->commit_stamps_valid = false;
        if (wl_shm_buffer_get(s->pending_buf.r)) {
            s->commit_stamps_valid = g->read_stamps(s->pending_buf.r, s->commit_stamps);
            if (s->commit_stamps_valid) g->account_shown(s->commit_stamps, t, g->capture_to_commit_ms, false);
        }
        if (g->measuring) {
            ++g->commits;
            if (g->last_done_ns >= 0) g->callback_to_commit_us.add(double(t - g->last_done_ns) / 1e3);
            if (g->last_commit_ns >= 0) g->commit_interval_ms.add(double(t - g->last_commit_ns) / 1e6);
        }
        g->last_done_ns = -1; g->last_commit_ns = t;
    }
    s->pending_buf.reset(); s->pending_attached = false;
    if (s->has_commit) send_discarded(s->commit_fb);  // superseded before the next vblank
    s->commit_fb.insert(s->commit_fb.end(), s->pending_fb.begin(), s->pending_fb.end()); s->pending_fb.clear();
    s->commit_frames.insert(s->commit_frames.end(), s->pending_frames.begin(), s->pending_frames.end()); s->pending_frames.clear();
    s->has_commit = true;
}
void surface_noop_i(wl_client*, wl_resource*, std::int32_t) {}
const struct wl_surface_interface surface_impl = {
    destroy_resource, surface_attach, surface_damage, surface_frame, surface_region, surface_region, surface_commit,
    surface_noop_i, surface_noop_i, surface_damage, nullptr, nullptr};
void surface_destroyed(wl_resource* r) {
    auto* s = surface_of(r);
    send_discarded(s->pending_fb); send_discarded(s->commit_fb);
    std::erase(g->surfaces, s);
    if (s->layer) s->layer->surface = nullptr;
    delete s;
}
void compositor_create_surface(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* res = wl_resource_create(client, &wl_surface_interface, wl_resource_get_version(r), id);
    auto* s = new Surface(); s->res = res;
    wl_resource_set_implementation(res, &surface_impl, s, surface_destroyed);
    g->surfaces.push_back(s);
}
void compositor_create_region(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* res = wl_resource_create(client, &wl_region_interface, wl_resource_get_version(r), id);
    wl_resource_set_implementation(res, &region_impl, nullptr, nullptr);
}
const struct wl_compositor_interface compositor_impl = {compositor_create_surface, compositor_create_region, nullptr};
void bind_compositor(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &wl_compositor_interface, int(version), id);
    wl_resource_set_implementation(r, &compositor_impl, nullptr, nullptr);
}

// ---- wl_output ----
const struct wl_output_interface output_impl = {destroy_resource};
void output_destroyed(wl_resource* r) {
    auto* o = static_cast<Output*>(wl_resource_get_user_data(r));
    std::erase(o->resources, r);
}
void bind_output(wl_client* client, void* data, std::uint32_t version, std::uint32_t id) {
    auto* o = static_cast<Output*>(data);
    auto* r = wl_resource_create(client, &wl_output_interface, int(version), id);
    wl_resource_set_implementation(r, &output_impl, o, output_destroyed);
    o->resources.push_back(r);
    wl_output_send_geometry(r, 0, 0, 0, 0, WL_OUTPUT_SUBPIXEL_UNKNOWN, "loopback", "bench", WL_OUTPUT_TRANSFORM_NORMAL);
    wl_output_send_mode(r, WL_OUTPUT_MODE_CURRENT | WL_OUTPUT_MODE_PREFERRED, std::int32_t(o->w), std::int32_t(o->h), o->refresh_mhz);
    if (version >= 2) wl_output_send_scale(r, 1);
    if (version >= 4) { wl_output_send_name(r, o->name.c_str()); wl_output_send_description(r, o->name.c_str()); }
    if (version >= 2) wl_output_send_done(r);
}

// ---- zwlr_layer_shell_v1 ----
void layer_u(wl_client*, wl_resource*, std::uint32_t) {}
void layer_uu(wl_client*, wl_resource*, std::uint32_t, std::uint32_t) {}
void layer_i(wl_client*, wl_resource*, std::int32_t) {}
void layer_margin(wl_client*, wl_resource*, std::int32_t, std::int32_t, std::int32_t, std::int32_t) {}
void layer_popup(wl_client*, wl_resource*, wl_resource*) {}
const struct zwlr_layer_surface_v1_interface layer_surface_impl = {
    layer_uu, layer_u, layer_i, layer_margin, layer_u, layer_popup, layer_u, destroy_resource, layer_u};
void layer_destroyed(wl_resource* r) {
    auto* l = static_cast<Layer*>(wl_resource_get_user_data(r));
    if (l->surface) l->surface->layer = nullptr;
    delete l;
}
void shell_get_layer_surface(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource* surface, wl_resource*, std::uint32_t,
                             const char*) {
    auto* res = wl_resource_create(client, &zwlr_layer_surface_v1_interface, wl_resource_get_version(r), id);
    auto* l = new Layer(); l->res = res; l->surface = surface_of(surface); l->surface->layer = l;
    wl_resource_set_implementation(res, &layer_surface_impl, l, layer_destroyed);
}
const struct zwlr_layer_shell_v1_interface layer_shell_impl = {shell_get_layer_surface, destroy_resource};
void bind_layer_shell(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &zwlr_layer_shell_v1_interface, int(version), id);
    wl_resource_set_implementation(r, &layer_shell_impl, nullptr, nullptr);
}

// ---- wp_viewporter / wp_fractional_scale_manager_v1 (--fractional-scale) ----
void viewport_source(wl_client*, wl_resource*, wl_fixed_t, wl_fixed_t, wl_fixed_t, wl_fixed_t) {}
void viewport_destination(wl_client*, wl_resource*, std::int32_t w, std::int32_t h) { g->dest_w = w; g->dest_h = h; }
const struct wp_viewport_interface viewport_impl = {destroy_resource, viewport_source, viewport_destination};
void viewporter_get_viewport(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource*) {
    auto* res = wl_resource_create(client, &wp_viewport_interface, wl_resource_get_version(r), id);
    wl_resource_set_implementation(res, &viewport_impl, nullptr, nullptr);
}
const struct wp_viewporter_interface viewporter_impl = {destroy_resource, viewporter_get_viewport};
void bind_viewporter(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &wp_viewporter_interface, int(version), id);
    wl_resource_set_implementation(r, &viewporter_impl, nullptr, nullptr);
}
const struct wp_fractional_scale_v1_interface fractional_impl = {destroy_resource};
void fractional_get(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource*) {
    auto* res = wl_resource_create(client, &wp_fractional_scale_v1_interface, wl_resource_get_version(r), id);
    wl_resource_set_implementation(res, &fractional_impl, nullptr, nullptr);
    wp_fractional_scale_v1_send_preferred_scale(res, g->opt.fractional_scale);
}
const struct wp_fractional_scale_manager_v1_interface fractional_manager_impl = {destroy_resource, fractional_get};
void bind_fractional(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &wp_fractional_scale_manager_v1_interface, int(version), id);
    wl_resource_set_implementation(r, &fractional_manager_impl, nullptr, nullptr);
}

// ---- wp_presentation ----
void presentation_feedback(wl_client* client, wl_resource*, wl_resource* surface, std::uint32_t id) {
    auto* fb = wl_resource_create(client, &wp_presentation_feedback_interface, 1, id);
    wl_resource_set_implementation(fb, nullptr, nullptr, forget_live);
    g->live.insert(fb);
    surface_of(surface)->pending_fb.push_back(fb);
}
const struct wp_presentation_interface presentation_impl = {destroy_resource, presentation_feedback};
void bind_presentation(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &wp_presentation_interface, int(version), id);
    wl_resource_set_implementation(r, &presentation_impl, nullptr, nullptr);
    wp_presentation_send_clock_id(r, CLOCK_MONOTONIC);
}

// ---- ext-image-capture-source / ext-image-copy-capture ----
const struct ext_image_capture_source_v1_interface source_impl = {destroy_resource};
void source_create(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource* output) {
    auto* res = wl_resource_create(client, &ext_image_capture_source_v1_interface, wl_resource_get_version(r), id);
    auto* o = static_cast<Output*>(wl_resource_get_user_data(output));
    int index = -1;
    for (std::size_t i = 0; i < g->outputs.size(); ++i) if (g->outputs[i].get() == o) index = int(i);
    wl_resource_set_implementation(res, &source_impl, reinterpret_cast<void*>(std::intptr_t(index)), nullptr);
}
const struct ext_output_image_capture_source_manager_v1_interface source_manager_impl = {source_create, destroy_resource};
void bind_source_manager(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &ext_output_image_capture_source_manager_v1_interface, int(version), id);
    wl_resource_set_implementation(r, &source_manager_impl, nullptr, nullptr);
}
void frame_destroyed(wl_resource* r) {
    auto* f = static_cast<CapFrame*>(wl_resource_get_user_data(r));
    std::erase(g->captured, f);
    delete f;
}
void frame_attach(wl_client*, wl_resource* r, wl_resource* buffer) { static_cast<CapFrame*>(wl_resource_get_user_data(r))->buf.set(buffer); }
void frame_damage(wl_client*, wl_resource*, std::int32_t, std::int32_t, std::int32_t, std::int32_t) {}
void frame_capture(wl_client*, wl_resource* r) {
    auto* f = static_cast<CapFrame*>(wl_resource_get_user_data(r));
    if (f->captured) return;
    f->captured = true; g->captured.push_back(f);
}
const struct ext_image_copy_capture_frame_v1_interface frame_impl = {destroy_resource, frame_attach, frame_damage, frame_capture};
void session_create_frame(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* res = wl_resource_create(client, &ext_image_copy_capture_frame_v1_interface, wl_resource_get_version(r), id);
    auto* f = new CapFrame(); f->res = res; f->session = static_cast<CapSession*>(wl_resource_get_user_data(r));
    wl_resource_set_implementation(res, &frame_impl, f, frame_destroyed);
}
const struct ext_image_copy_capture_session_v1_interface session_impl = {session_create_frame, destroy_resource};
void session_destroyed(wl_resource* r) {
    auto* s = static_cast<CapSession*>(wl_resource_get_user_data(r));
    for (auto* f : g->captured) if (f->session == s) f->session = nullptr;
    std::erase(g->sessions, s);
    delete s;
}
void send_constraints(CapSession* s, std::uint32_t w, std::uint32_t h);
void manager_create_session(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource* source, std::uint32_t) {
    auto* res = wl_resource_create(client, &ext_image_copy_capture_session_v1_interface, wl_resource_get_version(r), id);
    auto* s = new CapSession(); s->res = res; s->output = int(reinterpret_cast<std::intptr_t>(wl_resource_get_user_data(source)));
    wl_resource_set_implementation(res, &session_impl, s, session_destroyed);
    g->sessions.push_back(s);
    send_constraints(s, s->w, s->h);
}
void send_constraints(CapSession* s, std::uint32_t w, std::uint32_t h) {
    auto* res = s->res;
    ext_image_copy_capture_session_v1_send_buffer_size(res, w, h);
    ext_image_copy_capture_session_v1_send_shm_format(res, WL_SHM_FORMAT_XRGB8888);
    ext_image_copy_capture_session_v1_send_shm_format(res, WL_SHM_FORMAT_ARGB8888);
    if (g->dmabuf_capture && g_gpu) {
        wl_array dev; wl_array_init(&dev);
        std::memcpy(wl_array_add(&dev, sizeof g_gpu->dev), &g_gpu->dev, sizeof g_gpu->dev);
        ext_image_copy_capture_session_v1_send_dmabuf_device(res, &dev); wl_array_release(&dev);
        for (auto [fourcc, mods] : {std::pair{kDrmXr24, &g->xr24_mods}, std::pair{kDrmAr24, &g->ar24_mods}}) {
            wl_array a; wl_array_init(&a);
            for (auto m : *mods) std::memcpy(wl_array_add(&a, sizeof m), &m, sizeof m);
            ext_image_copy_capture_session_v1_send_dmabuf_format(res, fourcc, &a); wl_array_release(&a);
        }
    }
    ext_image_copy_capture_session_v1_send_done(res);
}
void manager_cursor_session(wl_client* client, wl_resource*, std::uint32_t, wl_resource*, wl_resource*) {
    wl_client_post_implementation_error(client, "cursor sessions are not offered");
}
const struct ext_image_copy_capture_manager_v1_interface copy_manager_impl = {manager_create_session, manager_cursor_session, destroy_resource};
void bind_copy_manager(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &ext_image_copy_capture_manager_v1_interface, int(version), id);
    wl_resource_set_implementation(r, &copy_manager_impl, nullptr, nullptr);
}

// ---- zwp_linux_dmabuf_v1 (v4 feedback for Mesa, v3 params for the renderer's capture targets) ----
void params_add(wl_client*, wl_resource* r, std::int32_t fd, std::uint32_t plane, std::uint32_t offset, std::uint32_t stride,
                std::uint32_t hi, std::uint32_t lo) {
    auto* p = static_cast<Params*>(wl_resource_get_user_data(r));
    if (plane >= 4 || p->d->fds[plane] >= 0) { ::close(fd); wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_PLANE_IDX, "plane"); return; }
    p->d->fds[plane] = fd; p->d->offsets[plane] = offset; p->d->strides[plane] = stride;
    p->d->modifier = (std::uint64_t(hi) << 32) | lo;
    p->d->planes = std::max(p->d->planes, int(plane) + 1);
}
wl_resource* params_buffer(wl_client* client, Params* p, std::uint32_t id, std::int32_t w, std::int32_t h, std::uint32_t format) {
    p->used = true; p->d->width = w; p->d->height = h; p->d->format = format;
    auto* b = wl_resource_create(client, &wl_buffer_interface, 1, id);
    wl_resource_set_implementation(b, &buffer_impl, p->d.release(), dmabuf_buffer_destroyed);
    return b;
}
void params_create(wl_client* client, wl_resource* r, std::int32_t w, std::int32_t h, std::uint32_t format, std::uint32_t) {
    auto* p = static_cast<Params*>(wl_resource_get_user_data(r));
    if (p->used) { wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_ALREADY_USED, "used"); return; }
    zwp_linux_buffer_params_v1_send_created(r, params_buffer(client, p, 0, w, h, format));
}
void params_create_immed(wl_client* client, wl_resource* r, std::uint32_t id, std::int32_t w, std::int32_t h, std::uint32_t format, std::uint32_t) {
    auto* p = static_cast<Params*>(wl_resource_get_user_data(r));
    if (p->used) { wl_resource_post_error(r, ZWP_LINUX_BUFFER_PARAMS_V1_ERROR_ALREADY_USED, "used"); return; }
    params_buffer(client, p, id, w, h, format);
}
void params_sampling(wl_client*, wl_resource*, wl_array*) {}
const struct zwp_linux_buffer_params_v1_interface params_impl = {destroy_resource, params_add, params_create, params_create_immed, params_sampling};
void params_destroyed(wl_resource* r) { delete static_cast<Params*>(wl_resource_get_user_data(r)); }
void dmabuf_create_params(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* res = wl_resource_create(client, &zwp_linux_buffer_params_v1_interface, wl_resource_get_version(r), id);
    auto* p = new Params(); p->res = res;
    wl_resource_set_implementation(res, &params_impl, p, params_destroyed);
}
const struct zwp_linux_dmabuf_feedback_v1_interface feedback_impl = {destroy_resource};
void send_feedback(wl_client* client, wl_resource* r, std::uint32_t id) {
    auto* fb = wl_resource_create(client, &zwp_linux_dmabuf_feedback_v1_interface, wl_resource_get_version(r), id);
    wl_resource_set_implementation(fb, &feedback_impl, nullptr, nullptr);
    zwp_linux_dmabuf_feedback_v1_send_format_table(fb, g->format_table_fd, std::uint32_t(g->format_table_size));
    wl_array dev; wl_array_init(&dev);
    const dev_t d = g_gpu ? g_gpu->dev : 0;
    std::memcpy(wl_array_add(&dev, sizeof d), &d, sizeof d);
    zwp_linux_dmabuf_feedback_v1_send_main_device(fb, &dev);
    zwp_linux_dmabuf_feedback_v1_send_tranche_target_device(fb, &dev);
    wl_array_release(&dev);
    wl_array idx; wl_array_init(&idx);
    for (std::uint16_t i = 0; i < g->format_table.size(); ++i) std::memcpy(wl_array_add(&idx, sizeof i), &i, sizeof i);
    zwp_linux_dmabuf_feedback_v1_send_tranche_formats(fb, &idx); wl_array_release(&idx);
    zwp_linux_dmabuf_feedback_v1_send_tranche_flags(fb, 0);
    zwp_linux_dmabuf_feedback_v1_send_tranche_done(fb);
    zwp_linux_dmabuf_feedback_v1_send_done(fb);
}
void dmabuf_default_feedback(wl_client* client, wl_resource* r, std::uint32_t id) { send_feedback(client, r, id); }
void dmabuf_surface_feedback(wl_client* client, wl_resource* r, std::uint32_t id, wl_resource*) { send_feedback(client, r, id); }
const struct zwp_linux_dmabuf_v1_interface dmabuf_impl = {destroy_resource, dmabuf_create_params, dmabuf_default_feedback, dmabuf_surface_feedback};
void bind_dmabuf(wl_client* client, void*, std::uint32_t version, std::uint32_t id) {
    auto* r = wl_resource_create(client, &zwp_linux_dmabuf_v1_interface, int(version), id);
    wl_resource_set_implementation(r, &dmabuf_impl, nullptr, nullptr);
    if (version < 4)
        for (auto [fourcc, mod] : g->format_table)
            if (version >= 3) zwp_linux_dmabuf_v1_send_modifier(r, fourcc, std::uint32_t(mod >> 32), std::uint32_t(mod & 0xffffffffu));
}

// ---- simulated display + managed outputs ----
void vblank(std::int64_t t) {
    ++g->vblank_seq;
    for (auto* s : std::vector<Surface*>(g->surfaces)) {
        if (!s->has_commit) continue;
        if (s->commit_has_buffer && s->commit_buf.r) {
            // The buffer shown until now has long finished rendering: read it before it goes back to the client, so the
            // readback never waits on the renderer's GPU work (a compositor samples without such a stall too).
            Stamps got{};
            if (s->shown.r && s->shown_unread && g->read_stamps(s->shown.r, got)) {
                g->account_shown(got, s->shown_ns, g->capture_to_present_ms, true);
                if (g->opt.pointer_look && g->measuring) g->account_look(got, g->latched);  // not counted yet: `latched` is this buffer's
            }
            s->shown_unread = false;
            if (s->shown.r && s->shown.r != s->commit_buf.r) wl_buffer_send_release(s->shown.r);
            s->shown.set(s->commit_buf.r); s->shown_ns = t;
            if (g->measuring) ++g->latched;
            if (s->commit_stamps_valid) {  // SHM output (the software renderer): already read, at its commit
                g->account_shown(s->commit_stamps, t, g->capture_to_present_ms, true);
                if (g->opt.pointer_look && g->measuring) g->account_look(s->commit_stamps, g->latched);
            }
            else s->shown_unread = true;
        }
        s->commit_buf.reset(); s->commit_has_buffer = false; s->commit_stamps_valid = false;
        wl_resource* sync = g->output_resource(0);
        const std::uint64_t sec = std::uint64_t(t / 1'000'000'000), nsec = std::uint64_t(t % 1'000'000'000);
        for (auto* fb : s->commit_fb) if (g->live.contains(fb)) {
            if (sync) wp_presentation_feedback_send_sync_output(fb, sync);
            wp_presentation_feedback_send_presented(fb, std::uint32_t(sec >> 32), std::uint32_t(sec & 0xffffffffu), std::uint32_t(nsec),
                                                    std::uint32_t(1e9 / g->opt.hz), std::uint32_t(g->vblank_seq >> 32),
                                                    std::uint32_t(g->vblank_seq & 0xffffffffu),
                                                    WP_PRESENTATION_FEEDBACK_KIND_VSYNC | WP_PRESENTATION_FEEDBACK_KIND_HW_CLOCK |
                                                    WP_PRESENTATION_FEEDBACK_KIND_HW_COMPLETION);
            wl_resource_destroy(fb);
            if (g->measuring) ++g->presented_fb;
        }
        s->commit_fb.clear();
        const auto ms = std::uint32_t(t / 1'000'000);
        bool any = false;
        for (auto* cb : s->commit_frames) if (g->live.contains(cb)) { wl_callback_send_done(cb, ms); wl_resource_destroy(cb); any = true; if (g->measuring) ++g->callbacks; }
        s->commit_frames.clear();
        s->has_commit = false;
        if (any) g->last_done_ns = -2;  // stamped right after the flush below
    }
    wl_display_flush_clients(g->display);
    if (g->last_done_ns == -2) g->last_done_ns = now_ns();
}
void capture_tick(std::int64_t t, bool first_only) {
    std::vector<CapFrame*> done;
    for (auto* f : std::vector<CapFrame*>(g->captured)) {
        if (!f->session || !f->buf.r) continue;
        if (first_only && f->session->delivered) continue;
        done.push_back(f);
    }
    for (auto* f : done) {
        std::uint32_t bw = 0, bh = 0;  // a buffer made for other constraints is refused, as by Hyprland
        if (auto* shm = wl_shm_buffer_get(f->buf.r)) { bw = std::uint32_t(wl_shm_buffer_get_width(shm)); bh = std::uint32_t(wl_shm_buffer_get_height(shm)); }
        else if (auto* d = static_cast<Dmabuf*>(wl_resource_get_user_data(f->buf.r))) { bw = std::uint32_t(d->width); bh = std::uint32_t(d->height); }
        if (bw != f->session->w || bh != f->session->h) {
            ext_image_copy_capture_frame_v1_send_failed(f->res, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS);
            ++g->constraint_failures;
            std::erase(g->captured, f); f->captured = false; continue;
        }
        const std::uint32_t stamp = g->next_stamp++ & 0xffffffu;
        if (!(g->next_stamp & 0xffffffu)) g->next_stamp = 1;
        if (auto* shm = wl_shm_buffer_get(f->buf.r)) {
            const auto w = std::uint32_t(wl_shm_buffer_get_width(shm)), h = std::uint32_t(wl_shm_buffer_get_height(shm));
            const auto stride = std::uint32_t(wl_shm_buffer_get_stride(shm)) / 4;
            wl_shm_buffer_begin_access(shm);
            auto* px = static_cast<std::uint32_t*>(wl_shm_buffer_get_data(shm));
            const std::uint32_t half_w = g->opt.maximize ? w / 2 : 32, half_h = g->opt.maximize ? h / 2 : 32;  // Options::maximize
            for (std::uint32_t y = h / 2 - half_h; y < h / 2 + half_h; ++y)
                for (std::uint32_t x = w / 2 - half_w; x < w / 2 + half_w; ++x) px[std::size_t(y) * stride + x] = 0xff000000u | stamp;
            wl_shm_buffer_end_access(shm);
        } else if (auto* d = static_cast<Dmabuf*>(wl_resource_get_user_data(f->buf.r)); !d || !d->clear(stamp)) {
            ext_image_copy_capture_frame_v1_send_failed(f->res, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_UNKNOWN);
            std::erase(g->captured, f); f->captured = false; continue;
        }
        const std::int64_t ready = now_ns();
        const auto out = f->session->output - 1;  // outputs[0] is DP-2
        g->stamps[stamp & 0xffff] = {stamp, ready, out};
        if (g->measuring && out >= 0 && std::size_t(out) < g->captured_frames.size()) ++g->captured_frames[std::size_t(out)];
        f->session->delivered = true;
        const std::uint64_t sec = std::uint64_t(t / 1'000'000'000), nsec = std::uint64_t(t % 1'000'000'000);
        ext_image_copy_capture_frame_v1_send_transform(f->res, WL_OUTPUT_TRANSFORM_NORMAL);
        ext_image_copy_capture_frame_v1_send_damage(f->res, 0, 0, std::int32_t(bw), std::int32_t(bh));
        ext_image_copy_capture_frame_v1_send_presentation_time(f->res, std::uint32_t(sec >> 32), std::uint32_t(sec & 0xffffffffu), std::uint32_t(nsec));
        ext_image_copy_capture_frame_v1_send_ready(f->res);
        std::erase(g->captured, f); f->captured = false;
    }
    wl_display_flush_clients(g->display);
}

void churn_tick() {
    if (!g->measuring || g->opt.churn.empty()) return;
    ++g->churn_events;
    for (auto* s : g->sessions) {
        if (g->opt.churn == "bounce") {
            send_constraints(s, 1280, 720);  // the default rule's size for a moment...
            send_constraints(s, s->w, s->h);  // ...and straight back (one flush, as in one reload)
            send_constraints(s, s->w, s->h);  // a mode event that changes nothing
        } else {
            s->h = s->h == kSrcH ? kOutH : kSrcH;
            send_constraints(s, s->w, s->h);
        }
    }
    wl_display_flush_clients(g->display);
}

int make_timer(double hz, double phase) {
    const int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    const auto period = std::int64_t(1e9 / hz);
    const std::int64_t start = now_ns() + std::int64_t(phase * double(period)) + period;
    itimerspec spec{};
    spec.it_interval = {period / 1'000'000'000, period % 1'000'000'000};
    spec.it_value = {start / 1'000'000'000, start % 1'000'000'000};
    timerfd_settime(fd, TFD_TIMER_ABSTIME, &spec, nullptr);
    return fd;
}

// Renderer process CPU (all threads) from schedstat, in ns.
std::uint64_t process_cpu_ns(pid_t pid) {
    std::uint64_t total = 0;
    const std::string dir = "/proc/" + std::to_string(pid) + "/task";
    if (DIR* d = opendir(dir.c_str())) {
        while (dirent* e = readdir(d)) {
            if (e->d_name[0] == '.') continue;
            std::ifstream in(dir + "/" + e->d_name + "/schedstat");
            std::uint64_t ns = 0; if (in >> ns) total += ns;
        }
        closedir(d);
    }
    return total;
}
// Under bwrap the forked process is bwrap's monitor; the renderer is its descendant.
pid_t renderer_pid(pid_t pid, const std::string& renderer) {
    const std::string want = renderer.substr(renderer.find_last_of('/') + 1);
    for (int depth = 0; depth < 4; ++depth) {
        char exe[4096] = {};
        const std::string link = "/proc/" + std::to_string(pid) + "/exe";
        const auto n = readlink(link.c_str(), exe, sizeof exe - 1);
        if (n > 0 && std::string(exe, std::size_t(n)).ends_with("/" + want)) return pid;
        std::ifstream in("/proc/" + std::to_string(pid) + "/task/" + std::to_string(pid) + "/children");
        pid_t child = 0;
        if (!(in >> child) || child <= 0) return pid;
        pid = child;
    }
    return pid;
}
std::uint64_t process_ctx_switches(pid_t pid) {
    std::uint64_t total = 0;
    const std::string dir = "/proc/" + std::to_string(pid) + "/task";
    if (DIR* d = opendir(dir.c_str())) {
        while (dirent* e = readdir(d)) {
            if (e->d_name[0] == '.') continue;
            std::ifstream in(dir + "/" + e->d_name + "/status");
            std::string line;
            while (std::getline(in, line))
                if (line.rfind("voluntary_ctxt_switches:", 0) == 0) total += std::strtoull(line.c_str() + 24, nullptr, 10);
        }
        closedir(d);
    }
    return total;
}

void usage() {
    std::fprintf(stderr, "capture_e2e_bench --renderer PATH [--mode cpu|gl-shm|gl-dmabuf|pattern] [--seconds S] [--warmup S]\n"
                         "                  [--hz HZ] [--capture-hz HZ (0: static desktops)] [--outputs 1..4] [--tracking (bwrap)]\n"
                         "                  [--malloc-shim LIB [--malloc-trace FILE]] [--constraint-churn bounce|resize] [--fractional-scale N120]\n"
                         "                  [--environment grid|smoke] [--open-panel] [--maximize 1..outputs] [--pointer-look] [--edge-pan] [--check]\n"
                         "                  [--feed-drop S --feed-script PATH] [--video-loss S] [--bad-control] [--close-control] [--control-errors fatal|log]\n");
}
}  // namespace

int main(int argc, char** argv) {
    Options opt;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string { if (i + 1 >= argc) { usage(); std::exit(2); } return argv[++i]; };
        if (a == "--renderer") opt.renderer = next();
        else if (a == "--mode") opt.mode = next();
        else if (a == "--seconds") opt.seconds = std::stod(next());
        else if (a == "--warmup") opt.warmup = std::stod(next());
        else if (a == "--hz") opt.hz = std::stod(next());
        else if (a == "--capture-hz") opt.capture_hz = std::stod(next());
        else if (a == "--outputs") opt.outputs = std::stoi(next());
        else if (a == "--malloc-shim") opt.malloc_shim = next();
        else if (a == "--malloc-trace") opt.malloc_trace = next();
        else if (a == "--check") opt.check = true;
        else if (a == "--constraint-churn") opt.churn = next();
        else if (a == "--tracking") opt.tracking = true;
        else if (a == "--environment") opt.environment = next();  // a background from the start: grid or smoke
        else if (a == "--open-panel") opt.open_panel = true;
        else if (a == "--fractional-scale") opt.fractional_scale = std::uint32_t(std::stoul(next()));
        else if (a == "--maximize") opt.maximize = std::stoi(next());
        else if (a == "--pointer-look") opt.pointer_look = true;
        else if (a == "--edge-pan") opt.edge_pan = true;
        else if (a == "--feed-script") opt.feed_script = next();
        else if (a == "--feed-drop") { opt.feed_drop = std::stod(next()); opt.tracking = true; }
        else if (a == "--video-loss") opt.video_loss = std::stod(next());
        else if (a == "--bad-control") opt.bad_control = true;
        else if (a == "--close-control") opt.close_control = true;
        else if (a == "--control-errors") opt.control_errors = next();
        else { usage(); return 2; }
    }
    if (!opt.churn.empty() && opt.churn != "bounce" && opt.churn != "resize") { usage(); return 2; }
    if ((opt.feed_drop > 0) != !opt.feed_script.empty()) { usage(); return 2; }
    if (opt.renderer.empty() || opt.outputs < 1 || opt.outputs > int(xr::kMaxDesktops) || !(opt.hz > 1) || opt.capture_hz < 0 ||
        opt.maximize < 0 || opt.maximize > opt.outputs || (opt.maximize && opt.mode != "gl-shm" && opt.mode != "gl-dmabuf") ||
        (opt.mode != "cpu" && opt.mode != "gl-shm" && opt.mode != "gl-dmabuf" && opt.mode != "pattern")) { usage(); return 2; }
    // The renderer's animation is at most 60 s and must outlast the measured window.
    const double pattern_ms = (opt.warmup + opt.seconds + 5) * 1000;
    if (opt.mode == "pattern" && pattern_ms > 60000) { std::fprintf(stderr, "pattern mode: warmup + seconds must be under 55 s\n"); return 2; }
    // Absolute paths: the --tracking sandbox has its own /tmp, so files there are bound in one by one (never /tmp itself).
    auto absolute = [](const std::string& path) {
        char resolved[PATH_MAX];
        return realpath(path.c_str(), resolved) ? std::string(resolved) : std::string();
    };
    opt.renderer = absolute(opt.renderer);
    if (opt.renderer.empty()) { std::fprintf(stderr, "renderer not found\n"); return 2; }
    if (!opt.malloc_shim.empty() && (opt.malloc_shim = absolute(opt.malloc_shim)).empty()) { std::fprintf(stderr, "malloc shim not found\n"); return 2; }
    if (!opt.malloc_trace.empty()) {
        if (opt.malloc_shim.empty()) { std::fprintf(stderr, "--malloc-trace needs --malloc-shim\n"); return 2; }
        const int fd = ::open(opt.malloc_trace.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);  // a file to bind into the sandbox
        if (fd < 0 || (opt.malloc_trace = absolute(opt.malloc_trace)).empty()) { std::fprintf(stderr, "cannot create the malloc trace file\n"); return 2; }
        ::close(fd);
    }
    std::signal(SIGPIPE, SIG_IGN);
    const bool gl = opt.mode == "gl-shm" || opt.mode == "gl-dmabuf", capture = opt.mode != "pattern";
    std::unique_ptr<Gpu> gpu;
    Server server; server.opt = opt; g = &server;
    server.gl = gl; server.dmabuf_capture = opt.mode == "gl-dmabuf";
    if (gl) {
        gpu = std::make_unique<Gpu>(); std::string why;
        if (!gpu->init(why)) { std::fprintf(stderr, "skip: GPU unavailable (%s)\n", why.c_str()); return 77; }
        g_gpu = gpu.get();
        server.xr24_mods = gpu->modifiers(kDrmXr24); server.ar24_mods = gpu->modifiers(kDrmAr24);
        // Like the compositor: offer the renderer every modifier the GPU renders and samples (the renderer prefers LINEAR).
        if (std::find(server.xr24_mods.begin(), server.xr24_mods.end(), kModLinear) == server.xr24_mods.end()) server.xr24_mods.push_back(kModLinear);
        if (std::find(server.ar24_mods.begin(), server.ar24_mods.end(), kModLinear) == server.ar24_mods.end()) server.ar24_mods.push_back(kModLinear);
    }
    for (auto [fourcc, mods] : {std::pair{kDrmXr24, &server.xr24_mods}, std::pair{kDrmAr24, &server.ar24_mods}}) {
        server.format_table.push_back({fourcc, kModInvalid});
        for (auto m : *mods) server.format_table.push_back({fourcc, m});
    }
    server.format_table_size = server.format_table.size() * 16;
    server.format_table_fd = memfd_create("dmabuf-format-table", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    {
        std::vector<std::uint8_t> table(server.format_table_size);
        for (std::size_t i = 0; i < server.format_table.size(); ++i) {
            std::memcpy(&table[i * 16], &server.format_table[i].first, 4);
            std::memcpy(&table[i * 16 + 8], &server.format_table[i].second, 8);
        }
        if (write(server.format_table_fd, table.data(), table.size()) != ssize_t(table.size())) return 1;
        fcntl(server.format_table_fd, F_ADD_SEALS, F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE | F_SEAL_SEAL);
    }

    // libwayland-server logs the renderer hanging up at exit ("failed to read client connection"): not news here.
    wl_log_set_handler_server([](const char*, va_list) {});
    server.display = wl_display_create();
    wl_display_init_shm(server.display);
    wl_global_create(server.display, &wl_compositor_interface, 4, nullptr, bind_compositor);
    wl_global_create(server.display, &zwlr_layer_shell_v1_interface, 4, nullptr, bind_layer_shell);
    wl_global_create(server.display, &wp_presentation_interface, 1, nullptr, bind_presentation);
    if (opt.fractional_scale) {
        wl_global_create(server.display, &wp_viewporter_interface, 1, nullptr, bind_viewporter);
        wl_global_create(server.display, &wp_fractional_scale_manager_v1_interface, 1, nullptr, bind_fractional);
    }
    if (capture) {
        wl_global_create(server.display, &ext_output_image_capture_source_manager_v1_interface, 1, nullptr, bind_source_manager);
        wl_global_create(server.display, &ext_image_copy_capture_manager_v1_interface, 1, nullptr, bind_copy_manager);
    }
    if (gl || capture) wl_global_create(server.display, &zwp_linux_dmabuf_v1_interface, 4, nullptr, bind_dmabuf);
    server.outputs.push_back(std::make_unique<Output>(Output{"DP-2", kOutW, kOutH, std::int32_t(opt.hz * 1000), nullptr, {}}));
    for (int i = 1; i <= opt.outputs && capture; ++i)
        server.outputs.push_back(std::make_unique<Output>(Output{"XR-" + std::to_string(i), kSrcW, kSrcH, std::int32_t(opt.capture_hz * 1000), nullptr, {}}));
    for (auto& o : server.outputs) o->global = wl_global_create(server.display, &wl_output_interface, 4, o.get(), bind_output);
    if (opt.mode == "cpu" && capture) {  // the renderer's CPU composition: side_by_side of the sources into the output
        std::vector<xr::Size> sizes(std::size_t(opt.outputs), xr::Size{float(kSrcW), float(kSrcH)});
        server.tiles = xr::side_by_side(sizes, kOutW, kOutH);
    }

    int wl_pair[2], control[2], status[2], err_pipe[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, wl_pair) || socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, control) ||
        socketpair(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0, status) || pipe2(err_pipe, O_CLOEXEC)) { std::perror("socketpair"); return 1; }
    int count_fd = -1; std::uint64_t* malloc_count = nullptr;
    if (!opt.malloc_shim.empty()) {
        count_fd = memfd_create("xr-malloc-count", MFD_CLOEXEC);
        if (count_fd < 0 || ftruncate(count_fd, 4096) != 0) return 1;
        void* p = mmap(nullptr, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, count_fd, 0);
        if (p == MAP_FAILED) return 1;
        malloc_count = static_cast<std::uint64_t*>(p);
    }
    server.client = wl_client_create(server.display, wl_pair[0]);
    const pid_t pid = fork();
    if (pid == 0) {
        // Move every source above the target numbers first: a source may already sit on 10..13.
        const int c = fcntl(control[1], F_DUPFD, 100), w = fcntl(wl_pair[1], F_DUPFD, 100), st = fcntl(status[1], F_DUPFD, 100),
                  e = fcntl(err_pipe[1], F_DUPFD, 100), m = count_fd >= 0 ? fcntl(count_fd, F_DUPFD, 100) : -1;
        dup2(c, 10); dup2(w, 11); dup2(st, 12); dup2(e, 2); dup2(e, 1);  // renderer stdout too: never into this tool's JSON
        std::vector<std::pair<std::string, std::string>> env = {{"MESA_SHADER_CACHE_DISABLE", "true"}};
        if (m >= 0) { dup2(m, 13); env.push_back({"XR_MALLOC_COUNT_FD", "13"}); env.push_back({"LD_PRELOAD", opt.malloc_shim}); }
        for (int fd : {c, w, st, e, m}) if (fd >= 0) ::close(fd);
        if (m >= 0 && !opt.malloc_trace.empty()) env.push_back({"XR_MALLOC_TRACE", opt.malloc_trace});
        if (opt.mode == "cpu") env.push_back({"__EGL_VENDOR_LIBRARY_FILENAMES", "/nonexistent/egl-vendor.json"});  // no EGL: CPU composition
        std::vector<std::string> args;
        if (opt.tracking) {
            // Head tracking on, as in glasses runs (every frame callback redraws), but never against the live XRLinuxDriver:
            // a private /tmp and SysV IPC namespace leave the pose feed absent, so the renderer holds the identity pose.
            args = {"/usr/bin/bwrap", "--dev-bind", "/", "/", "--tmpfs", "/tmp", "--unshare-ipc", "--die-with-parent"};
            for (const auto* file : {&opt.renderer, &opt.malloc_shim, &opt.feed_script})  // single files only: the real /tmp stays hidden
                if (file->rfind("/tmp/", 0) == 0) args.insert(args.end(), {"--ro-bind", *file, *file});
            if (!opt.malloc_trace.empty() && opt.malloc_trace.rfind("/tmp/", 0) == 0) args.insert(args.end(), {"--bind", opt.malloc_trace, opt.malloc_trace});
            for (const auto& [k, v] : env) args.insert(args.end(), {"--setenv", k, v});
            args.push_back("--");
        } else {
            for (const auto& [k, v] : env) setenv(k.c_str(), v.c_str(), 1);
        }
        if (opt.feed_drop > 0)  // it sets the feed up, then execs the renderer: the pid stays the one this bench watches
            args.insert(args.end(), {opt.feed_script, "--live-s", "3", "--gone-s", std::to_string(opt.feed_drop), "--"});
        args.insert(args.end(), {opt.renderer, "--supervised-fd", "10", "--wayland-fd", "11", "--output", "DP-2",
                                 "--run-id", "bench1", "--session", "s1", "--status-fd", "12"});
        if (opt.control_errors == "fatal") args.insert(args.end(), {"--control-errors", "fatal"});
        if (capture) {
            std::string list;
            for (int i = 1; i <= opt.outputs; ++i) list += (i > 1 ? ",XR-" : "XR-") + std::to_string(i);
            args.insert(args.end(), {"--capture", list});
            if (opt.tracking) args.insert(args.end(), {"--pose-feed", "xrld"});
        } else args.insert(args.end(), {"--animate-pattern-ms", std::to_string(int(pattern_ms))});
        std::vector<char*> cargs; for (auto& a : args) cargs.push_back(a.data()); cargs.push_back(nullptr);
        execv(cargs[0], cargs.data());
        _exit(127);
    }
    ::close(control[1]); ::close(wl_pair[1]); ::close(status[1]); ::close(err_pipe[1]);
    fcntl(control[0], F_SETFL, O_NONBLOCK); fcntl(status[0], F_SETFL, O_NONBLOCK); fcntl(err_pipe[0], F_SETFL, O_NONBLOCK);
    std::int64_t pan_started_ns = 0;  // --edge-pan
    int pan_frames = 0;
    auto send_frame = [&](std::uint64_t request, const char* op, const std::string& payload) {
        const std::string line = std::string("{\"schema\":1,\"request_id\":") + std::to_string(request) +
                                 ",\"run_id\":\"bench1\",\"session\":\"s1\",\"op\":\"" + op + "\",\"payload\":" + payload + "}\n";
        (void)!::send(control[0], line.data(), line.size(), MSG_NOSIGNAL);
    };
    send_frame(1, "start", "{}");
    if (capture)
        send_frame(2, "view", "{\"zoomed\":false,\"selected\":0,\"spacing\":40,\"mode\":\"anchor\",\"cursor_follow\":false,"
                              "\"prediction\":false,\"typing\":false}");
    std::uint64_t next_request = capture ? 3 : 2;
    if (capture && !opt.environment.empty()) send_frame(next_request++, "environment", "{\"background\":\"" + opt.environment + "\",\"brightness\":100}");
    if (capture && opt.open_panel) {
        std::string placements;
        for (int i = 0; i < opt.outputs; ++i) placements += std::string(i ? "," : "") + "{\"x\":0,\"y\":0,\"distance\":100,\"curvature\":0}";
        send_frame(next_request++, "placement_menu", "{\"open\":true,\"desktop\":0,\"field\":\"x\",\"placements\":[" + placements + "]}");
    }

    // What the renderer must survive (--bad-control), each frame as the socket would carry it, then frames that must still work.
    auto send_bad_control = [&] {
        const auto raw = [&](const std::string& text) { (void)!::send(control[0], text.data(), text.size(), MSG_NOSIGNAL); };
        const auto line = [](std::uint64_t id, const char* run, const char* op, const std::string& payload) {
            return "{\"schema\":1,\"request_id\":" + std::to_string(id) + ",\"run_id\":\"" + run + "\",\"session\":\"s1\",\"op\":\"" + op + "\",\"payload\":" + payload + "}\n";
        };
        const std::string roll = R"({"roll":[0,0,0,0],"icons":["none","none","none","none"],"hover":-1,"side":"none","active":false})";
        const std::string open = R"({"action":"open","projection":"360","stereo":"mono","fov":180,"title":"a private film"})";
        send_frame(next_request++, "roll", roll);  // the floor
        send_frame(next_request - 2, "roll", roll);  // 1. the 2026-11-04 crash: a good frame with an id already passed
        send_frame(next_request - 1, "roll", roll);  // 2. the same id again
        raw(line(next_request, "another-run", "roll", roll));  // 3. someone else's run
        raw("this is not a control frame\n");  // 4.
        raw(line(next_request, "bench1", "no_such_op", "{}"));  // 5.
        raw(line(next_request, "bench1", "view", "{}"));  // 6. a malformed payload that acts
        raw(line(next_request, "bench1", "roll", "{}"));  // 7. and one that is display only
        // 8. a video open refused with its descriptor: that file must go with it, or the next open plays it.
        const std::string replay = line(next_request - 1, "bench1", "video", open);
        int fd = ::open("/dev/null", O_RDONLY | O_CLOEXEC);
        iovec io{const_cast<char*>(replay.data()), replay.size()};
        alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int))];
        msghdr msg{}; msg.msg_iov = &io; msg.msg_iovlen = 1; msg.msg_control = ancillary; msg.msg_controllen = sizeof ancillary;
        cmsghdr* c = CMSG_FIRSTHDR(&msg);
        c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int));
        std::memcpy(CMSG_DATA(c), &fd, sizeof fd);
        (void)!::sendmsg(control[0], &msg, MSG_NOSIGNAL);
        ::close(fd);
        raw(std::string(xr::kMaxControlPending + 8192, 'x') + "\n");  // 9. a frame with no sensible end
        send_frame(next_request++, "video", open);  // good, and no descriptor waits for it: stderr says so
        std::string placements;
        for (int i = 0; i < opt.outputs; ++i) placements += std::string(i ? "," : "") + "{\"x\":0,\"y\":0,\"distance\":100,\"curvature\":0}";
        send_frame(next_request++, "placement_menu", "{\"open\":true,\"desktop\":0,\"field\":\"x\",\"placements\":[" + placements + "]}");  // good: the view shifts
    };
    constexpr int kBadControlFrames = 9;
    std::uint64_t heartbeats = 0, status_lines = 0, geometry_lines = 0;
    nlohmann::json last_status;  // the newest measured status (not the geometry lines the same socket carries)
    std::string stderr_text;
    // --feed-drop: the renderer's status through the drop, in order.
    bool status_tracked = false, status_lost = false, status_resumed = false;
    std::int64_t video_loss_at = 0;
    const int vblank_fd = make_timer(opt.hz, 0.0);
    const int capture_fd = capture ? make_timer(opt.capture_hz > 0 ? opt.capture_hz : opt.hz, 0.37) : -1;
    const std::int64_t start = now_ns();
    const std::int64_t measure_at = start + std::int64_t(opt.warmup * 1e9), end_at = measure_at + std::int64_t(opt.seconds * 1e9);
    std::uint64_t cpu0 = 0, cpu1 = 0, ctx0 = 0, ctx1 = 0, mallocs0 = 0, mallocs1 = 0, hb0 = 0, hb1 = 0;
    bool child_exited = false, window_complete = true; int child_status = 0;
    pid_t measured_pid = pid;
    bool stop_sent = false;
    std::int64_t stop_deadline = 0;
    auto drain = [&] {
        char buf[4096];
        for (ssize_t n; (n = ::recv(control[0], buf, sizeof buf, MSG_DONTWAIT)) > 0;)
            for (ssize_t i = 0; i < n; ++i) heartbeats += buf[i] == 'H';
        for (ssize_t n; (n = ::recv(status[0], buf, sizeof buf, MSG_DONTWAIT)) > 0;) {
            ++status_lines;
            const std::string line(buf, std::size_t(n));
            if (line.rfind("{\"overview_geometry\":", 0) == 0 && gl && !server.maximized_ns) {
                ++geometry_lines;
                try {
                    const auto j = nlohmann::json::parse(line)["overview_geometry"];
                    server.tiles.clear();
                    for (const auto& t : j["tiles"])
                        server.tiles.push_back({std::uint32_t(std::max(0.0, t[0].get<double>())), std::uint32_t(std::max(0.0, t[1].get<double>())),
                                                std::uint32_t(t[2].get<double>()), std::uint32_t(t[3].get<double>())});
                } catch (const std::exception&) {}
            }
            try {
                auto j = nlohmann::json::parse(line);
                if (j.is_object() && !j.contains("overview_geometry") && !j.contains("roll_geometry") && !j.contains("help_geometry")) {
                    if (j.contains("tracking") && j["tracking"].is_boolean()) {
                        const bool on = j["tracking"].get<bool>();
                        if (on && status_lost) status_resumed = true;
                        else if (on) status_tracked = true;
                        else if (status_tracked) status_lost = true;
                    }
                    last_status = std::move(j);
                }
            } catch (const std::exception&) {}
        }
        for (ssize_t n; (n = ::read(err_pipe[0], buf, sizeof buf)) > 0;) if (stderr_text.size() < (1 << 20)) stderr_text.append(buf, std::size_t(n));
    };
    const int churn_fd = capture && !opt.churn.empty() ? make_timer(1000.0 / kChurnMs, 0.5) : -1;
    pollfd fds[6] = {{wl_event_loop_get_fd(wl_display_get_event_loop(server.display)), POLLIN, 0}, {vblank_fd, POLLIN, 0},
                     {capture_fd, POLLIN, 0}, {control[0], POLLIN, 0}, {status[0], POLLIN, 0}, {churn_fd, POLLIN, 0}};
    std::int64_t next_look = 0;
    for (;;) {
        const std::int64_t t = now_ns();
        if (opt.pointer_look && server.measuring && t >= next_look && !server.tiles.empty() && !server.look_waiting) {
            const auto& tile = server.tiles[0];
            server.look_beam = !server.look_beam;
            send_frame(next_request++, "overview_pointer", "{\"visible\":true,\"x\":" + std::to_string(tile.x + tile.width / 2 + 4) +
                       ",\"y\":" + std::to_string(tile.y + tile.height / 2) + ",\"held\":false" + (server.look_beam ? ",\"look\":\"text\"" : "") + "}");
            server.look_sent_ns = t; server.look_sent_latched = server.latched; server.look_waiting = true;
            next_look = t + 40'000'000;
        }
        if (!server.measuring && t >= measure_at && !stop_sent) {
            drain();
            server.measuring = true; measured_pid = renderer_pid(pid, opt.renderer);
            cpu0 = process_cpu_ns(measured_pid); ctx0 = process_ctx_switches(measured_pid); hb0 = heartbeats;
            if (opt.bad_control) send_bad_control();
            if (opt.close_control) ::shutdown(control[0], SHUT_WR);
            if (malloc_count) { mallocs0 = std::atomic_ref<std::uint64_t>(malloc_count[0]).load(); std::atomic_ref<std::uint64_t>(malloc_count[1]).store(1); }
            if (opt.maximize) {
                const std::string selected = ",\"selected\":" + std::to_string(opt.edge_pan ? 0 : opt.maximize - 1) + ",\"spacing\":40,\"mode\":\"";
                const std::string rest = "\",\"cursor_follow\":false,\"prediction\":false,\"typing\":false}";
                send_frame(next_request++, "view", "{\"zoomed\":false" + selected + "anchor" + rest);
                send_frame(next_request++, "view", "{\"zoomed\":true" + selected + "follow" + rest);
                server.tiles = {xr::Rect{0, 0, kOutW, kOutH}};  // read the middle of the view from here on
                server.maximized_ns = t + std::int64_t((opt.edge_pan ? kEdgePanLandedMs : kMaximizeSettleMs) * 1e6);
                pan_started_ns = t;
            }
        }
        if (opt.video_loss > 0 && server.measuring && !video_loss_at) {
            video_loss_at = t + std::int64_t(opt.video_loss * 1e9);
        } else if (video_loss_at && t >= video_loss_at && !stop_sent) {
            video_loss_at = INT64_MAX;
            for (auto& o : server.outputs) if (o->name == "DP-2") wl_global_remove(o->global);
        }
        if (opt.edge_pan && pan_started_ns && !stop_sent) {  // as the supervisor does: frames as the hand goes, then the landing
            const double since = double(t - pan_started_ns) / 1e6;
            const int to = opt.maximize - 1;
            if (pan_frames < 10 && since >= kMaximizeSettleMs + pan_frames * 20.0) {
                const int share = (pan_frames + 1) * 1000;
                send_frame(next_request++, "edge_pan", "{\"active\":true,\"from\":0,\"to\":" + std::to_string(to) + ",\"side\":\"right\",\"exit\":5000,\"entry\":5000,\"peek\":" +
                           std::to_string(share) + ",\"cross\":" + std::to_string(share) + ",\"turn\":500,\"way\":1000,\"zoom\":2000}");
                ++pan_frames;
            } else if (pan_frames == 10 && since >= kMaximizeSettleMs + 500) {
                send_frame(next_request++, "view", "{\"zoomed\":true,\"selected\":" + std::to_string(to) +
                           ",\"spacing\":40,\"mode\":\"follow\",\"cursor_follow\":false,\"prediction\":false,\"typing\":false,\"pan\":true}");
                ++pan_frames;
            }
        }
        if (server.measuring && t >= end_at && !stop_sent) {
            drain();
            cpu1 = process_cpu_ns(measured_pid); ctx1 = process_ctx_switches(measured_pid); hb1 = heartbeats;
            if (malloc_count) { mallocs1 = std::atomic_ref<std::uint64_t>(malloc_count[0]).load(); std::atomic_ref<std::uint64_t>(malloc_count[1]).store(0); }
            server.measuring = false;
            send_frame(next_request, "stop", "{}");
            stop_sent = true; stop_deadline = t + 5'000'000'000;
        }
        if (!child_exited && waitpid(pid, &child_status, WNOHANG) == pid) child_exited = true;
        if (child_exited) {
            if (server.measuring) { server.measuring = false; window_complete = false; }  // the renderer ended inside the window
            break;
        }
        if (stop_sent && t > stop_deadline) { kill(pid, SIGKILL); waitpid(pid, &child_status, 0); child_exited = true; break; }
        if (::poll(fds, 6, 2) < 0 && errno != EINTR) break;
        wl_event_loop_dispatch(wl_display_get_event_loop(server.display), 0);
        std::uint64_t expirations = 0;
        if (::read(vblank_fd, &expirations, sizeof expirations) == sizeof expirations) {
            if (expirations > 1 && server.measuring) server.missed_ticks += expirations - 1;
            vblank(now_ns());
        }
        if (capture_fd >= 0 && ::read(capture_fd, &expirations, sizeof expirations) == sizeof expirations) capture_tick(now_ns(), opt.capture_hz <= 0);
        if (churn_fd >= 0 && ::read(churn_fd, &expirations, sizeof expirations) == sizeof expirations) churn_tick();
        drain();
        wl_display_flush_clients(server.display);
    }
    drain();
    const int rc = WIFEXITED(child_status) ? WEXITSTATUS(child_status) : 128 + (WIFSIGNALED(child_status) ? WTERMSIG(child_status) : 0);
    wl_display_destroy_clients(server.display);

    const double secs = opt.seconds;
    nlohmann::json out;
    out["mode"] = opt.mode; out["renderer"] = opt.renderer; out["seconds"] = secs; out["hz"] = opt.hz; out["capture_hz"] = opt.capture_hz;
    out["outputs"] = capture ? opt.outputs : 0; out["tracking"] = opt.tracking && capture;
    out["renderer_rc"] = rc;
    out["commits_per_s"] = std::round(double(server.commits) / secs * 10) / 10;
    out["latched_per_s"] = std::round(double(server.latched) / secs * 10) / 10;
    out["presented_feedback"] = server.presented_fb; out["discarded_feedback"] = server.discarded_fb;
    out["frame_callbacks"] = server.callbacks; out["harness_missed_vblanks"] = server.missed_ticks;
    out["callback_to_commit_us"] = server.callback_to_commit_us.json(1);
    out["commit_interval_ms"] = server.commit_interval_ms.json(3);
    if (capture) {
        out["capture_to_commit_ms"] = server.capture_to_commit_ms.json(3);
        out["capture_to_present_ms"] = server.capture_to_present_ms.json(3);
        std::uint64_t cap = 0, shown = 0;
        for (int i = 0; i < opt.outputs; ++i) { cap += server.captured_frames[std::size_t(i)]; shown += server.shown_frames[std::size_t(i)]; }
        out["captured_frames_per_s"] = std::round(double(cap) / secs * 10) / 10;
        out["shown_capture_frames_per_s"] = std::round(double(shown) / secs * 10) / 10;
        if (opt.pointer_look) out["pointer_look_frames"] = server.look_frames;
        if (opt.maximize) { out["maximize"] = opt.maximize; out["centre_frames"] = server.centre_frames; out["centre_none"] = server.centre_none; }
        out["readback_failures"] = server.readback_failures;
        out["blank_frames"] = server.blank_frames;
        if (!opt.churn.empty()) {
            out["constraint_churn"] = opt.churn; out["churn_events"] = server.churn_events;
            out["constraint_failures"] = server.constraint_failures;
        }
    }
    out["window_complete"] = window_complete && stop_sent;
    const bool counters = window_complete && stop_sent;  // otherwise the end samples were never taken
    out["renderer_cpu_ms_per_s"] = counters ? nlohmann::json(std::round(double(cpu1 - cpu0) / 1e6 / secs * 100) / 100) : nlohmann::json();
    out["renderer_voluntary_switches_per_s"] = counters ? nlohmann::json(std::round(double(ctx1 - ctx0) / secs * 10) / 10) : nlohmann::json();
    out["main_loop_wakes_per_s"] = counters ? nlohmann::json(std::round(double(hb1 - hb0) / secs * 10) / 10) : nlohmann::json();  // one 'H' per loop pass
    const bool shim_loaded = malloc_count && std::atomic_ref<std::uint64_t>(malloc_count[2]).load() == 0x786d616c6c6f63ull;  // "xmalloc"
    if (malloc_count && !shim_loaded) std::fprintf(stderr, "warning: the malloc shim did not load in the renderer; no allocation count\n");
    if (shim_loaded && counters) out["allocations_per_s"] = std::round(double(mallocs1 - mallocs0) / secs * 10) / 10;
    out["status_datagrams"] = status_lines; out["overview_geometry_lines"] = geometry_lines;
    out["last_status"] = last_status;
    // The renderer's own exit reports (timing JSON, capture JSON with path/fallback), bounded.
    std::istringstream lines(stderr_text); std::string line; std::vector<nlohmann::json> reports; std::vector<std::string> other;
    while (std::getline(lines, line)) {
        try { auto j = nlohmann::json::parse(line); if (j.is_object()) { reports.push_back(std::move(j)); continue; } } catch (const std::exception&) {}
        if (other.size() < 20) other.push_back(line.substr(0, 300));
    }
    for (const auto& j : reports) {
        if (j.contains("tracking") && j["tracking"].is_object()) out["tracking_report"] = j["tracking"];
        if (j.value("kind", "") == "control_stream") out["control_stream"] = j;
        if (j.contains("kind") && j["kind"] == "managed_output_capture") {
            nlohmann::json paths = nlohmann::json::array();
            for (const auto& o : j["outputs"]) paths.push_back({{"name", o["name"]}, {"path", o["path"]}, {"fallback", o["fallback"]}, {"frames", o["frames"]},
                                                                {"reallocations", o.value("reallocations", -1)}, {"constraint_batches", o.value("constraint_batches", -1)},
                                                                {"resize_settles", o.value("resize_settles", -1)},
                                                                {"shape_eases", o.value("shape_eases", -1)}});
            out["capture_paths"] = paths;
            if (j.contains("placement_menu") && j["placement_menu"].contains("view_shift_mrad")) out["view_shift_mrad"] = j["placement_menu"]["view_shift_mrad"];
            if (j.contains("composition") && j["composition"].is_object()) {
                const auto& c = j["composition"];
                out["composition"] = c.value("path", "");
                for (const char* k : {"upload", "draw_swap", "uploaded_mib", "imported_frames"}) if (c.contains(k)) out["gl_" + std::string(k)] = c[k];
            }
        }
    }
    if (!other.empty()) out["renderer_stderr"] = other;
    std::printf("%s\n", out.dump(1).c_str());

    int status_rc = rc == 0 ? 0 : 1;
    if (opt.check) {  // functional smoke for ctest: frames flowed and capture frames reached the screen
        auto fail = [&](const char* why) { std::fprintf(stderr, "capture_e2e check failed: %s\n", why); status_rc = 1; };
        if (opt.video_loss > 0) {  // the video went: the end of the session, as ever, and no hanging on for the feed
            status_rc = 0;  // exit status 1 is the point here
            if (rc != 1) fail("the renderer did not exit by itself when its output went");
            if (stderr_text.find("dispatch: closed, removed or disconnected") == std::string::npos) fail("the renderer ended some other way");
            if (stop_sent) fail("the renderer outlived its output");
            if (!status_tracked || !status_lost) fail("the feed was not gone when the video went");
            wl_display_destroy(server.display);
            g = nullptr; g_gpu = nullptr;
            return status_rc;
        }
        if (opt.close_control) {  // the one thing that still ends a session, and it says so
            status_rc = 0;  // exit status 1 is the point here
            if (rc != 1) fail("the renderer did not stop when the supervisor closed its end");
            if (stderr_text.find("control: supervisor channel closed (EOF)") == std::string::npos) fail("the renderer did not say why it stopped");
            wl_display_destroy(server.display);
            g = nullptr; g_gpu = nullptr;
            return status_rc;
        }
        if (opt.bad_control && opt.control_errors == "fatal") {  // what every other test relies on: a bad frame does stop this renderer
            status_rc = 0;  // exit status 1 is the point here
            if (rc != 1) fail("the renderer did not stop on a refused frame");
            if (stderr_text.find("control: stopping on a refused frame") == std::string::npos) fail("the renderer ended some other way");
            if (stderr_text.find("control: refused frame") == std::string::npos) fail("the refused frame was not logged");
            wl_display_destroy(server.display);
            g = nullptr; g_gpu = nullptr;
            return status_rc;
        }
        if (rc != 0) fail("renderer exit status");
        if (server.commits < std::uint64_t(opt.seconds * opt.hz * 0.25)) fail("too few commits");  // frames flow; not a speed check
        // Whatever else a run tests: the renderer took every frame it was sent. Even one it may ignore is a bug in the sender.
        if (!out.contains("control_stream")) fail("no control_stream report");
        else if (out["control_stream"].value("refused", -1) != (opt.bad_control ? kBadControlFrames : 0)) fail("the renderer refused other frames than it was meant to");
        if (opt.bad_control) {
            if (stderr_text.find("control: video open without a file descriptor") == std::string::npos) fail("a refused video open left its descriptor for the next one");
            if (stderr_text.find("control: refused frame") == std::string::npos) fail("the refused frames were not logged");
            if (out.value("view_shift_mrad", 0) <= 0) fail("a good frame after the bad ones did nothing");
            // The 2026-11-04 crash, as the journal would now tell it: which frame, why, and what it looked like.
            if (stderr_text.find("(roll): control_replay: request id ") == std::string::npos) fail("a replay was not named as one");
            if (stderr_text.find("(roll): control_wrong_run:") == std::string::npos) fail("another run's frame was not named as one");
            if (stderr_text.find("(no_such_op): invalid_control: unknown op") == std::string::npos) fail("an unknown op was not named as one");
            if (stderr_text.find("(view): invalid_control: bad view payload") == std::string::npos) fail("a bad payload was not named as one");
            if (stderr_text.find("\"op\":\"roll\"") == std::string::npos) fail("the log did not show the frame's shape");
            if (stderr_text.find("private film") != std::string::npos || stderr_text.find("another-run") != std::string::npos) fail("a frame's text reached the log");
        }
        if (!window_complete || !stop_sent) fail("renderer ended before the measured window did");
        if (opt.feed_drop > 0) {
            const auto tracking = out.value("tracking_report", nlohmann::json::object());
            if (!status_tracked) fail("head tracking never started");
            if (!status_lost) fail("the renderer never reported the feed gone");
            if (!status_resumed) fail("head tracking did not resume");
            if (tracking.value("lost_events", 0) != 1) fail("the drop was not one outage");
            if (tracking.value("reanchors", 0) != 1) fail("the view was not re-anchored");
            if (tracking.value("hints", 0) != 1) fail("the reconnecting hint did not come up once");
        }
        if (capture && server.capture_to_present_ms.v.empty()) fail("no capture frame shown");
        std::uint64_t captured_n = 0, shown_n = 0;
        for (int i = 0; capture && i < opt.outputs; ++i) {
            if (!opt.maximize && !server.shown_frames[std::size_t(i)]) fail("a desktop never showed a new capture frame");  // (maximized: one is read)
            captured_n += server.captured_frames[std::size_t(i)]; shown_n += server.shown_frames[std::size_t(i)];
        }
        if (capture && opt.capture_hz > 0 && !opt.maximize && shown_n * 4 < captured_n) fail("under a quarter of the captured frames were shown");
        if (opt.mode == "cpu" && out.value("composition", std::string()) != "cpu-blit") fail("not on the CPU composition path");
        if (opt.mode == "gl-dmabuf" && !(out.contains("capture_paths") && out["capture_paths"][0]["path"] == "dmabuf")) fail("not on the dmabuf path");
        if ((opt.mode == "gl-shm" || opt.mode == "cpu") && !(out.contains("capture_paths") && out["capture_paths"][0]["path"] == "shm")) fail("not on the shm path");
        // Constraint churn (layout changes): no desktop ever goes blank, and constraints that come back to the buffers' own
        // size re-create nothing (one allocation per desktop for the whole run).
        if (opt.fractional_scale) {  // drawn at the native pixels, shown on the logical surface (GL; CPU stays logical)
            const auto want = [&](std::uint32_t v) { return std::int32_t((std::uint64_t(v) * opt.fractional_scale + 60) / 120); };
            const bool scaled = gl ? server.buffer_w == want(kOutW) && server.buffer_h == want(kOutH)
                                   : server.buffer_w == std::int32_t(kOutW) && server.buffer_h == std::int32_t(kOutH);
            if (!scaled) fail("committed buffer not at the fractional scale's size");
            if (server.dest_w != std::int32_t(kOutW) || server.dest_h != std::int32_t(kOutH)) fail("viewport destination is not the logical size");
        }
        if (capture && !opt.maximize && server.blank_frames) fail("a desktop went blank (showed no frame) after showing one");
        // What the incident monitor reads while a session runs (incident_detect.py). The status needs a frame to measure.
        if (capture && !last_status.is_object()) fail("no status datagram");
        // A resize fails a capture or two, each ended by the next frame; only a stuck capture keeps failing (CAPTURE_STUCK there).
        if (capture && !(last_status.contains("capture_failure_run") && last_status["capture_failure_run"].is_number_unsigned())) fail("status has no count of consecutive capture failures");
        if (capture && opt.churn.empty() && last_status.value("capture_failure_run", 0) != 0) fail("a healthy run is failing captures");
        if (capture && last_status.value("capture_failure_run", 0) >= 30) fail("a healthy run looks like a stuck capture");
        if (opt.tracking && !(last_status.contains("tracking") && last_status["tracking"].is_boolean())) fail("a tracked run's status does not say if it is tracking");
        if (!opt.tracking && last_status.contains("tracking")) fail("an untracked run's status mentions tracking");
        if (opt.pointer_look) {  // [0] and [1] are the buffers that were on their way; a flip is in the first draw after them
            std::uint64_t landed = 0, late = 0;
            for (std::size_t i = 0; i < server.look_frames.size(); ++i) { landed += server.look_frames[i]; if (i > 2) late += server.look_frames[i]; }
            if (landed < 8) fail("too few dot and beam flips landed");
            if (late) fail("a dot or beam flip took more than the next draw to land");
        }
        if (opt.maximize) {  // the gap between the desktops crosses the middle while the zoom runs, so only after it landed
            const auto want = std::size_t(opt.maximize - 1);
            if (!server.centre_frames[want]) fail("the maximized desktop never reached the middle of the view");
            for (std::size_t i = 0; i < server.centre_frames.size(); ++i)
                if (i != want && server.centre_frames[i]) fail("another desktop is in the middle of the view");
            if (server.centre_none) fail("a frame had no desktop's frame in the middle of the view");
        }
        if (opt.open_panel && out.value("view_shift_mrad", 0) <= 0) fail("the open panel did not shift the view");
        if (!opt.open_panel && !opt.bad_control && capture && out.value("view_shift_mrad", 0) != 0) fail("the view stayed shifted with no panel open");
        if (capture && !opt.churn.empty() && server.churn_events < 3) fail("too few constraint churn events");
        if (capture && opt.churn == "bounce") {
            if (!out.contains("capture_paths")) fail("no capture report");
            else for (const auto& o : out["capture_paths"]) if (o["reallocations"] != 1) fail("constraints that came back re-created capture buffers");
            if (out.contains("capture_paths")) for (const auto& o : out["capture_paths"]) if (o["resize_settles"] != 0) fail("a bounce waited as a resize");
            if (out.contains("capture_paths")) for (const auto& o : out["capture_paths"]) if (o["shape_eases"] != 0) fail("a bounce changed a desktop's shape");
        }
        if (capture && opt.churn == "resize") {  // a real size change waits (ResizeSettle) before its buffers are re-created
            if (!out.contains("capture_paths")) fail("no capture report");
            else for (const auto& o : out["capture_paths"]) if (!(o["resize_settles"].is_number() && o["resize_settles"] >= 2)) fail("a resize did not settle first");
            // GPU composition eases the new shape (view.hpp SizeEase); the CPU fallback lays the new size out at once.
            if (gl && out.contains("capture_paths")) for (const auto& o : out["capture_paths"]) if (!(o["shape_eases"].is_number() && o["shape_eases"] >= 2)) fail("a new shape did not ease");
            if (!gl && out.contains("capture_paths")) for (const auto& o : out["capture_paths"]) if (o["shape_eases"] != 0) fail("the CPU path eased a shape");
        }
    }
    wl_display_destroy(server.display);
    g = nullptr; g_gpu = nullptr;
    return status_rc;
}
