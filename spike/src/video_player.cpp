#include "video_player.hpp"
#include "video_readback.hpp"
#include "video_gl.hpp"
#include <unistd.h>
#ifdef XR_HAVE_MPV
#include <mpv/client.h>
#include <mpv/render_gl.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <fcntl.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#endif

namespace xr {
#ifndef XR_HAVE_MPV
struct VideoPlayer::Impl {};
bool VideoPlayer::available() { return false; }
std::unique_ptr<VideoPlayer> VideoPlayer::open(int fd, const Options&, std::string& why) {
    if (fd >= 0) ::close(fd);
    why = "renderer built without libmpv";
    return nullptr;
}
VideoPlayer::~VideoPlayer() = default;
void VideoPlayer::toggle_pause() {}
void VideoPlayer::seek(double) {}
void VideoPlayer::set_deband(bool) {}
bool VideoPlayer::update() { return true; }
void VideoPlayer::report_swap() {}
unsigned VideoPlayer::texture() const { return 0; }
bool VideoPlayer::flip_y() const { return false; }
std::uint32_t VideoPlayer::width() const { return 0; }
std::uint32_t VideoPlayer::height() const { return 0; }
VideoState VideoPlayer::state() const { return VideoState::error; }
double VideoPlayer::position_s() const { return 0; }
double VideoPlayer::duration_s() const { return 0; }
std::string VideoPlayer::error() const { return "renderer built without libmpv"; }
std::string VideoPlayer::report() const { return "{\"decoder\":\"none\"}"; }
bool VideoPlayer::reopen(int, const Options&) { return false; }
#else
namespace {
std::string ascii(const char* s, std::size_t limit = 120) {
    std::string out;
    for (const char* p = s ? s : ""; *p && out.size() < limit; ++p) {
        const unsigned char c = static_cast<unsigned char>(*p);
        if (c >= 0x20 && c < 0x7f && c != '"' && c != '\\') out += char(c);
    }
    while (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}
void* proc_address(void*, const char* name) { return reinterpret_cast<void*>(eglGetProcAddress(name)); }
// Observed properties (reply_userdata identifies them).
enum Prop : std::uint64_t { kPause = 1, kTime, kDuration, kEof, kWidth, kHeight, kHwdec, kDrops, kQuality, kDeband, kPauseCommand, kSeekCommand };
bool has_token(const char* list, const char* token) {
    const std::size_t n = std::strlen(token);
    for (const char* p = list ? list : ""; (p = std::strstr(p, token)); p += n)
        if ((p == list || p[-1] == ' ') && (p[n] == ' ' || p[n] == '\0')) return true;
    return false;
}
// The render node of the GPU the current GL context draws on, empty if that cannot be told for sure. VA-API on this
// node decodes on the GPU that samples the frames. The GPU is found by its UUID (GL_EXT_memory_object against
// EGL_EXT_device_persistent_id): the EGL display's own device is not it under PRIME (DRI_PRIME names another GPU than
// the one EGL reports). Without UUIDs, only a machine with one GPU is unambiguous.
std::string egl_render_node() {
    if (eglGetCurrentContext() == EGL_NO_CONTEXT) return {};
    const char* client = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
    if (!has_token(client, "EGL_EXT_device_enumeration") && !has_token(client, "EGL_EXT_device_base")) return {};
    const auto query_devices = reinterpret_cast<PFNEGLQUERYDEVICESEXTPROC>(eglGetProcAddress("eglQueryDevicesEXT"));
    const auto device_string = reinterpret_cast<PFNEGLQUERYDEVICESTRINGEXTPROC>(eglGetProcAddress("eglQueryDeviceStringEXT"));
    const auto device_binary = reinterpret_cast<PFNEGLQUERYDEVICEBINARYEXTPROC>(eglGetProcAddress("eglQueryDeviceBinaryEXT"));
    EGLDeviceEXT devices[16];
    EGLint count = 0;
    if (!query_devices || !device_string || !query_devices(16, devices, &count)) return {};
    auto node_of = [&](EGLDeviceEXT d) -> std::string {
        if (!has_token(device_string(d, EGL_EXTENSIONS), "EGL_EXT_device_drm_render_node")) return {};
        const char* node = device_string(d, EGL_DRM_RENDER_NODE_FILE_EXT);
        if (!node || std::strncmp(node, "/dev/dri/renderD", 16) != 0 || std::strlen(node) > 32) return {};
        return node;
    };
    const auto gl_uuid = reinterpret_cast<PFNGLGETUNSIGNEDBYTEI_VEXTPROC>(eglGetProcAddress("glGetUnsignedBytei_vEXT"));
    if (has_token(reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS)), "GL_EXT_memory_object") && gl_uuid && device_binary) {
        GLubyte uuid[GL_UUID_SIZE_EXT] = {};
        gl_uuid(GL_DEVICE_UUID_EXT, 0, uuid);
        bool error = false;
        while (glGetError() != GL_NO_ERROR) error = true;  // never leave a GL error for the compositor
        if (!error) {
            for (EGLint i = 0; i < count; ++i) {
                GLubyte id[GL_UUID_SIZE_EXT] = {};
                EGLint size = 0;
                if (has_token(device_string(devices[i], EGL_EXTENSIONS), "EGL_EXT_device_persistent_id") &&
                    device_binary(devices[i], EGL_DEVICE_UUID_EXT, GL_UUID_SIZE_EXT, id, &size) && size == GL_UUID_SIZE_EXT &&
                    std::memcmp(id, uuid, GL_UUID_SIZE_EXT) == 0)
                    return node_of(devices[i]);
            }
            return {};  // the context's GPU is not among EGL's devices: do not guess
        }
    }
    std::string only;
    int gpus = 0;
    for (EGLint i = 0; i < count; ++i)
        if (auto node = node_of(devices[i]); !node.empty()) { only = std::move(node); ++gpus; }
    return gpus == 1 ? only : std::string();
}
std::int64_t steady_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
// Closing waits at most this long for mpv to stop (longer than mpv's own 200 ms wait for a frame to be drawn).
constexpr std::int64_t kQuiesceNs = 300'000'000;
// A reopen loads the next file after this even if mpv never reports that the previous one has stopped.
constexpr std::int64_t kRestartNs = 2'000'000'000;
// mpv 0.41's libmpv GL path keeps one GL fence per rendered frame until the render context is freed (opengl/context.c:
// ra_gl_ctx_submit_frame appends, only swap_buffers, never called here, deletes): ~175 bytes each. A player is reused
// for at most about an hour of 60 fps frames, so that growth stays what one long file had before.
constexpr std::uint64_t kReuseMaxFrames = 216'000;
// mpv loads every hardware-decode interop with the render context; with DRM parameters but no DRM display (fd -1) the
// DRM-plane overlay one always fails, with two error lines that are not about the video.
bool interop_noise(const char* prefix) { return prefix && std::strncmp(prefix, "libmpv_render/drmprime-overlay", 30) == 0; }
}

struct VideoPlayer::Impl {
    mpv_handle* mpv = nullptr;
    mpv_render_context* render = nullptr;
    Options options;
    std::atomic<bool> update_pending{false};
    GLuint fbo = 0, tex = 0;
    std::uint32_t tex_w = 0, tex_h = 0, video_w = 0, video_h = 0;
    VideoReadback readback;
    bool loaded = false, paused = false, eof = false, failed = false, rendered = false, idle = false;
    double position = 0, duration = 0;
    std::string why, hwdec = "unknown", last_log, render_node;
    std::uint64_t frames = 0, reallocations = 0, gl_errors = 0, reopens = 0, discarded = 0;
    // GL limits, read once: a glGet per display frame is a driver round trip (a full sync with Mesa's glthread).
    GLint max_texture = 0, max_attribs = 0;
    std::uint32_t levels = 0;  // mip levels of `tex`
    float anisotropy = 1;      // applied to `tex` (video_texture_filtering)
    // VA-API zero copy on the EGL context's own GPU (mpv's DRM display parameters; mpv keeps a pointer to this).
    int render_fd = -1;
    mpv_opengl_drm_params_v2 drm{-1, -1, -1, nullptr, -1};
    // reopen(): the next file's descriptor, kept until mpv has stopped the previous file and its video output is gone.
    // mpv numbers playlist entries 1, 2, 3... per loadfile (common/playlist.c), so `requested` is the current file's id;
    // events about an older entry are ignored.
    int next_fd = -1;
    std::int64_t restart_since_ns = 0, requested = 1;
    bool started = false, old_ended = false, pause_next = false;
    // Frame timing (video.hpp; update() is called once per display frame) and mpv's render cost.
    VideoFramePacer pacer;
    double render_sum_ms = 0, render_max_ms = 0;
    std::int64_t drops = 0;
    std::uint64_t quality_set = 0, quality_errors = 0;  // quality settings mpv took / refused (set_deband)
    ~Impl() {
        quiesce();
        if (render) mpv_render_context_free(render);  // GL context current (caller's contract)
        if (mpv) mpv_terminate_destroy(mpv);
        if (next_fd >= 0) ::close(next_fd);
        if (render_fd >= 0) ::close(render_fd);  // after the VA display that uses it
        if (fbo) glDeleteFramebuffers(1, &fbo);
        if (tex) glDeleteTextures(1, &tex);
    }
    bool restarting() const { return next_fd >= 0; }
    void fail(std::string reason) { if (!failed) { failed = true; why = reason.empty() ? std::string("playback failed") : ascii(reason.c_str()); } }
    bool allocate() {
        const auto size = video_texture_size(video_w, video_h);
        const auto fitted = max_texture > 0 ? video_texture_size(size[0], size[1], std::uint32_t(max_texture)) : size;
        if (!fitted[0] || !fitted[1]) return false;
        if (fitted[0] == tex_w && fitted[1] == tex_h && tex) return true;
        // Immutable storage with the full mip chain (video_gl.hpp: the projection pass samples it trilinear and
        // anisotropic where the frame is minified): a new size is a new texture. RGBA8 stays: mpv dithers to the target
        // depth, and the panel path is 8-bit, so a 10-bit/half-float target would only drop that dither.
        if (tex) { glDeleteTextures(1, &tex); tex = 0; tex_w = tex_h = 0; rendered = false; }
        glGenTextures(1, &tex);
        if (!fbo) glGenFramebuffers(1, &fbo);
        glBindTexture(GL_TEXTURE_2D, tex);
        anisotropy = video_texture_filtering();
        levels = video_texture_levels(fitted[0], fitted[1]);
        glTexStorage2D(GL_TEXTURE_2D, GLsizei(levels), GL_RGBA8, GLsizei(fitted[0]), GLsizei(fitted[1]));
        glBindTexture(GL_TEXTURE_2D, 0);
        if (glGetError() != GL_NO_ERROR) {  // e.g. out of GPU memory: an error state, never a GL error left for the frame
            while (glGetError() != GL_NO_ERROR) {}
            fail("cannot allocate a " + std::to_string(fitted[0]) + "x" + std::to_string(fitted[1]) + " video texture");
            return false;
        }
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        const bool complete = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE;
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        if (!complete) { fail("video frame buffer incomplete"); return false; }
        tex_w = fitted[0]; tex_h = fitted[1]; rendered = false; ++reallocations;
        return true;
    }
    void event(const mpv_event& e) {
        // While a reopen waits for the previous file to stop, that file's own events are not about the next one.
        switch (e.event_id) {
        case MPV_EVENT_IDLE: idle = true; break;
        case MPV_EVENT_START_FILE: {
            const auto* start = static_cast<const mpv_event_start_file*>(e.data);
            idle = false;
            if (start && start->playlist_entry_id == requested) started = true;
            break;
        }
        case MPV_EVENT_FILE_LOADED: if (started && !restarting()) loaded = true; break;
        case MPV_EVENT_END_FILE: {
            const auto* end = static_cast<const mpv_event_end_file*>(e.data);
            if (!end || end->playlist_entry_id != requested) break;  // an older entry
            if (restarting()) { old_ended = true; break; }
            if (end->reason == MPV_END_FILE_REASON_ERROR)
                fail(std::string(mpv_error_string(end->error)) + (last_log.empty() ? "" : ": " + last_log));
            else if (!loaded) fail(last_log.empty() ? "no playable video" : last_log);
            break;
        }
        case MPV_EVENT_COMMAND_REPLY:
            if(e.reply_userdata==kPauseCommand || e.reply_userdata==kSeekCommand)readback.reply(e.error>=0,mpv_error_string(e.error));
            break;
        case MPV_EVENT_SET_PROPERTY_REPLY:
            if (e.reply_userdata == kQuality) { ++(e.error < 0 ? quality_errors : quality_set);readback.reply(e.error>=0,mpv_error_string(e.error)); }
            break;
        case MPV_EVENT_LOG_MESSAGE: {
            const auto* m = static_cast<const mpv_event_log_message*>(e.data);
            if (m && m->log_level <= MPV_LOG_LEVEL_ERROR && !interop_noise(m->prefix)) last_log = ascii(m->text);
            if (m && std::getenv("XR_MPV_LOG")) std::fprintf(stderr, "mpv[%s] %s", m->prefix, m->text);
            break;
        }
        case MPV_EVENT_PROPERTY_CHANGE: {
            const auto* p = static_cast<const mpv_event_property*>(e.data);
            if (!p || !p->data) break;
            const bool per_file = e.reply_userdata == kTime || e.reply_userdata == kDuration || e.reply_userdata == kEof;
            if (per_file && restarting()) break;
            if (p->format == MPV_FORMAT_FLAG) {
                const bool v = *static_cast<int*>(p->data) != 0;
                if (e.reply_userdata == kPause) {paused = v;readback.flag("pause",v);} else if(e.reply_userdata==kDeband)readback.flag("deband",v); else if (e.reply_userdata == kEof) eof = v;
            } else if (p->format == MPV_FORMAT_DOUBLE) {
                const double v = *static_cast<double*>(p->data);
                if (!std::isfinite(v)) break;
                if (e.reply_userdata == kTime) {position = std::max(0.0, v);readback.number("time-pos",v);} else if (e.reply_userdata == kDuration) {duration = std::max(0.0, v);readback.number("duration",v);}
            } else if (p->format == MPV_FORMAT_INT64) {
                const auto v = *static_cast<std::int64_t*>(p->data);
                if (e.reply_userdata == kDrops) { drops = std::max<std::int64_t>(0, v); break; }
                if (v > 0 && v <= 65536) { if (e.reply_userdata == kWidth) video_w = std::uint32_t(v); else if (e.reply_userdata == kHeight) video_h = std::uint32_t(v); }
            } else if (p->format == MPV_FORMAT_STRING && e.reply_userdata == kHwdec) {
                hwdec = ascii(*static_cast<char**>(p->data), 32);
            }
            break;
        }
        default: break;
        }
    }
    void events(double first_timeout = 0) {
        for (int i = 0; i < 256; ++i) {  // bounded per frame
            const mpv_event* e = mpv_wait_event(mpv, i ? 0 : first_timeout);
            if (!e || e->event_id == MPV_EVENT_NONE) break;
            event(*e);
        }
    }
    // mpv leaves its own bindings behind; put back what the compositor's client-array GLES2 drawing assumes.
    void restore_gl() {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glBindVertexArray(0);
        glBindBuffer(GL_ARRAY_BUFFER, 0); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0); glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
        for (GLint i = 0; i < max_attribs; ++i) glDisableVertexAttribArray(GLuint(i));
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4); glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        glPixelStorei(GL_UNPACK_SKIP_ROWS, 0); glPixelStorei(GL_UNPACK_SKIP_PIXELS, 0);
        glDisable(GL_BLEND); glDisable(GL_SCISSOR_TEST); glDisable(GL_DEPTH_TEST); glDisable(GL_STENCIL_TEST); glDisable(GL_CULL_FACE);
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
        for (int unit = 7; unit >= 0; --unit) { glActiveTexture(GLenum(GL_TEXTURE0 + unit)); glBindTexture(GL_TEXTURE_2D, 0); }
        glUseProgram(0);
    }
    // Draw the frame mpv has ready into the texture. `show`: it becomes texture()'s picture; otherwise it is only
    // handed back to mpv (the previous file's last frames while a reopen or close waits for mpv to stop).
    bool render_frame(bool show) {
        if (!fbo || !tex_w) return false;
        mpv_opengl_fbo target{int(fbo), int(tex_w), int(tex_h), GL_RGBA8};
        int flip = 0, block = 0;
        mpv_render_param params[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &target}, {MPV_RENDER_PARAM_FLIP_Y, &flip},
            {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block}, {MPV_RENDER_PARAM_INVALID, nullptr}};
        const std::int64_t start = steady_ns();
        const int rc = mpv_render_context_render(render, params);
        const double ms = double(steady_ns() - start) / 1e6;
        restore_gl();
        // Errors mpv leaves behind are counted, never passed on: the compositor treats a GL error as fatal for the run.
        for (int i = 0; i < 16 && glGetError() != GL_NO_ERROR; ++i) ++gl_errors;
        if (rc < 0) { fail("mpv render failed"); return false; }
        if (!show) { ++discarded; return true; }
        // The mip chain of the new picture (one 8K frame: well under a millisecond on the RX 7800 XT, once per video frame).
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, tex); glGenerateMipmap(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, 0);
        if (glGetError() != GL_NO_ERROR) { while (glGetError() != GL_NO_ERROR) {} ++gl_errors; }
        rendered = true; ++frames;
        render_sum_ms += ms; render_max_ms = std::max(render_max_ms, ms);
        return true;
    }
    // mpv_render_context_free has to destroy mpv's video output, whose thread can wait up to 200 ms in flip_page for us
    // to draw the frame it handed over. Stop playback and keep handing frames back until mpv is idle (it destroys the
    // video output first), so the free does not wait.
    void quiesce() {
        if (!mpv || !render || idle) return;
        const char* stop[] = {"stop", nullptr};
        if (mpv_command_async(mpv, 0, stop) < 0) return;
        const std::int64_t deadline = steady_ns() + kQuiesceNs;
        while (!idle && steady_ns() < deadline) {
            events(0.001);
            if (idle) break;
            if (mpv_render_context_update(render) & MPV_RENDER_UPDATE_FRAME) render_frame(false);
            mpv_render_context_report_swap(render);
        }
    }
    // The previous file has stopped (or never answered): load the next one, playing as a new player would (paused if
    // play/pause was pressed in the meantime: a new player takes that before its file loads).
    void start_next() {
        ++requested; started = old_ended = false;
        const std::string url = "fdclose://" + std::to_string(next_fd);
        const char* resume[] = {"set", "pause", pause_next ? "yes" : "no", nullptr};
        pause_next = false;
        const char* load[] = {"loadfile", url.c_str(), nullptr};
        const int fd = next_fd;
        next_fd = -1;
        if (mpv_command_async(mpv, 0, resume) < 0 || mpv_command_async(mpv, 0, load) < 0) { ::close(fd); fail("mpv loadfile failed"); }
    }
};

bool VideoPlayer::available() { return true; }

std::unique_ptr<VideoPlayer> VideoPlayer::open(int fd, const Options& options, std::string& why) {
    if (fd < 0) { why = "no video file"; return nullptr; }
    std::unique_ptr<VideoPlayer> out(new VideoPlayer());
    out->impl_ = std::make_unique<Impl>();
    auto& s = *out->impl_;
    s.options = options;
    s.mpv = mpv_create();
    if (!s.mpv) { ::close(fd); why = "mpv_create failed"; return nullptr; }
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &s.max_texture);
    glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &s.max_attribs);
    s.max_attribs = std::clamp<GLint>(s.max_attribs, 0, 64);
    // Hardware decode on the GPU that draws the video: mpv's vaapi interop exports each decoded surface as a dmabuf and
    // imports it into this context (zero copy), with or without a Wayland display. Without a render node it falls back
    // to the Wayland display (VA-API on the compositor's main device) as before.
    if (options.hwdec) {
        s.render_node = egl_render_node();
        if (!s.render_node.empty()) s.render_fd = ::open(s.render_node.c_str(), O_RDWR | O_CLOEXEC | O_NOCTTY);
        if (s.render_fd < 0) s.render_node.clear();
        s.drm.render_fd = s.render_fd;
    }
    struct Opt { const char* name; const char* value; bool required; };
    const Opt opts[] = {
        {"config", "no", true}, {"load-scripts", "no", true}, {"ytdl", "no", true}, {"osc", "no", false},
        {"input-default-bindings", "no", true}, {"input-builtin-bindings", "no", false}, {"input-vo-keyboard", "no", true},
        {"terminal", "no", true}, {"vo", "libmpv", true}, {"idle", "yes", true}, {"keep-open", "yes", true},
        {"force-window", "no", true}, {"sub-auto", "no", true}, {"audio-file-auto", "no", true}, {"cover-art-auto", "no", false},
        {"autoload-files", "no", false}, {"save-position-on-quit", "no", true}, {"resume-playback", "no", true},
        {"load-stats-overlay", "no", false}, {"load-osd-console", "no", false}, {"load-console", "no", false},
        {"load-auto-profiles", "no", false}, {"load-select", "no", false}, {"load-commands", "no", false},
        {"load-context-menu", "no", false}, {"load-positioning", "no", false}, {"osd-level", "0", true}, {"osd-bar", "no", true},
        {"gpu-shader-cache", "no", false}, {"icc-cache", "no", false}, {"keepaspect", "no", true}, {"audio-display", "no", true},
        {"audio-client-name", "viture-workspace", false}, {"hwdec", options.hwdec ? "vaapi,vaapi-copy,auto-safe" : "no", true},
        {"ao", options.audio ? "pipewire,pulse,alsa" : "null", true}, {"demuxer-lavf-o", "protocol_whitelist=[file,fd]", false},
        // mpv 0.40+ would otherwise open its own Wayland connection for the clipboard and bind wl_seat (never in this renderer).
        {"clipboard-backends", "", false}, {"clipboard-monitor", "no", false}, {"input-ipc-server", "", false},
        // vaapi-copy (the fallback when the interop fails) decodes on the same GPU instead of mpv's default renderD128.
        {"vaapi-device", s.render_node.empty() ? nullptr : s.render_node.c_str(), false},
    };
    for (const auto& o : opts)
        if (o.value && mpv_set_option_string(s.mpv, o.name, o.value) < 0 && o.required) {
            ::close(fd); why = std::string("mpv option ") + o.name; return nullptr;
        }
    mpv_request_log_messages(s.mpv, std::getenv("XR_MPV_LOG") ? std::getenv("XR_MPV_LOG") : "error");
    if (mpv_initialize(s.mpv) < 0) { ::close(fd); why = "mpv_initialize failed"; return nullptr; }
    mpv_observe_property(s.mpv, kDeband, "deband", MPV_FORMAT_FLAG);
    mpv_observe_property(s.mpv, kPause, "pause", MPV_FORMAT_FLAG);
    mpv_observe_property(s.mpv, kTime, "time-pos", MPV_FORMAT_DOUBLE);
    mpv_observe_property(s.mpv, kDuration, "duration", MPV_FORMAT_DOUBLE);
    mpv_observe_property(s.mpv, kEof, "eof-reached", MPV_FORMAT_FLAG);
    mpv_observe_property(s.mpv, kWidth, "dwidth", MPV_FORMAT_INT64);
    mpv_observe_property(s.mpv, kHeight, "dheight", MPV_FORMAT_INT64);
    mpv_observe_property(s.mpv, kHwdec, "hwdec-current", MPV_FORMAT_STRING);
    mpv_observe_property(s.mpv, kDrops, "frame-drop-count", MPV_FORMAT_INT64);
    mpv_opengl_init_params gl{proc_address, nullptr};
    const char* api = MPV_RENDER_API_TYPE_OPENGL;
    // The render node wins over the Wayland display: mpv would try the Wayland one first (hwdec_vaapi.c), and that one
    // opens the compositor's main device, which need not be this context's GPU.
    mpv_render_param params[] = {
        {MPV_RENDER_PARAM_API_TYPE, const_cast<char*>(api)},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl},
        s.render_fd >= 0 ? mpv_render_param{MPV_RENDER_PARAM_DRM_DISPLAY_V2, &s.drm}
                         : mpv_render_param{options.display ? MPV_RENDER_PARAM_WL_DISPLAY : MPV_RENDER_PARAM_INVALID, options.display},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    if (mpv_render_context_create(&s.render, s.mpv, params) < 0) { ::close(fd); why = "mpv OpenGL render context failed"; return nullptr; }
    s.restore_gl();
    mpv_render_context_set_update_callback(s.render, [](void* data) { static_cast<Impl*>(data)->update_pending = true; }, &s);
    // fdclose://: mpv owns and closes the descriptor; no path ever reaches mpv.
    const std::string url = "fdclose://" + std::to_string(fd);
    const char* cmd[] = {"loadfile", url.c_str(), nullptr};
    if (mpv_command_async(s.mpv, 0, cmd) < 0) { ::close(fd); why = "mpv loadfile failed"; return nullptr; }
    return out;
}

VideoPlayer::~VideoPlayer() = default;

bool VideoPlayer::reopen(int fd, const Options& options) {
    auto& s = *impl_;
    // Only once the current file has started: before that, mpv's own start-up events could pass for the stop's.
    if (fd < 0 || s.failed || !s.started || s.frames + s.discarded >= kReuseMaxFrames || options.audio != s.options.audio ||
        options.hwdec != s.options.hwdec || options.display != s.options.display)
        return false;
    if (s.idle) s.old_ended = true;  // nothing playing: nothing to wait for
    else {
        const char* stop[] = {"stop", nullptr};  // async: update() hands back its last frames until mpv is idle
        if (mpv_command_async(s.mpv, 0, stop) < 0) return false;
    }
    if (s.next_fd >= 0) ::close(s.next_fd);  // a reopen still waiting to load: superseded
    else s.pause_next = false;
    s.next_fd = fd;
    s.restart_since_ns = mpv_get_time_ns(s.mpv);
    ++s.reopens;
    // What a new player shows: nothing until the next file's first frame.
    s.loaded = s.eof = s.rendered = false;
    s.readback.restart();
    s.position = s.duration = 0;
    s.last_log.clear();
    s.pacer.restart();
    return true;
}

void VideoPlayer::toggle_pause() {
    auto& s = *impl_;
    if (s.failed) return;
    if (s.restarting()) { s.pause_next = !s.pause_next; return; }  // applied with the next file
    const char* cmd[] = {"cycle", "pause", nullptr};
    const int result=mpv_command_async(s.mpv, kPauseCommand, cmd);
    if(result<0)s.readback.reply(false,mpv_error_string(result));
}

void VideoPlayer::seek(double relative_s) {
    auto& s = *impl_;
    if (s.failed || s.restarting() || !std::isfinite(relative_s)) return;
    const std::string amount = std::to_string(relative_s);
    const char* cmd[] = {"seek", amount.c_str(), "relative+exact", nullptr};  // exact: works between keyframes and past the last one
    const int result=mpv_command_async(s.mpv, kSeekCommand, cmd);
    if(result<0)s.readback.reply(false,mpv_error_string(result));
}

void VideoPlayer::set_deband(bool on) {
    auto& s = *impl_;
    if (s.failed || !s.mpv) return;
    const char* value = on ? "yes" : "no";  // async: the renderer thread never waits on the mpv core
    const int result=mpv_set_property_async(s.mpv, kQuality, "deband", MPV_FORMAT_STRING, &value);
    if(result<0){++s.quality_errors;s.readback.reply(false,mpv_error_string(result));}
}

bool VideoPlayer::update() {
    auto& s = *impl_;
    s.events();
    const std::int64_t now = mpv_get_time_ns(s.mpv);  // the clock of mpv's target times
    s.pacer.tick(now);
    if (s.video_w && s.video_h && !s.failed) s.allocate();
    s.update_pending = false;
    const auto flags = mpv_render_context_update(s.render);
    if (s.restarting()) {  // reopen(): hand the previous file's frames back until mpv has stopped it, then load the next
        if (flags & MPV_RENDER_UPDATE_FRAME) s.render_frame(false);
        if ((s.idle && s.old_ended) || now - s.restart_since_ns >= kRestartNs) s.start_next();
        return true;
    }
    if (!(flags & MPV_RENDER_UPDATE_FRAME) || !s.fbo || !s.tex_w) return true;
    // mpv hands a frame over up to video-timing-offset (50 ms) before it is due and, with BLOCK_FOR_TARGET_TIME off,
    // leaves the timing to us. Drawn at once it would show about one video frame early (33 ms at 30 fps), ahead of
    // the sound. Hold it until its display frame (VideoFramePacer); it stays pending in mpv meanwhile.
    // target_time is in mpv_get_time_ns() units (vo_libmpv.c), whatever render.h says about microseconds.
    mpv_render_frame_info info{};
    mpv_render_param query{MPV_RENDER_PARAM_NEXT_FRAME_INFO, &info};
    const bool timed = mpv_render_context_get_info(s.render, query) >= 0 && (info.flags & MPV_RENDER_FRAME_INFO_PRESENT) &&
                       !(info.flags & MPV_RENDER_FRAME_INFO_REDRAW) && info.target_time > 0;
    if (timed && !s.pacer.due(info.target_time, now)) return true;
    if (!s.render_frame(true)) return true;
    if (timed) s.pacer.drawn(info.target_time, now); else s.pacer.untimed();  // a redraw: no cadence to keep
    return true;
}

void VideoPlayer::report_swap() { if (impl_->render) mpv_render_context_report_swap(impl_->render); }
unsigned VideoPlayer::texture() const { return impl_->rendered ? impl_->tex : 0; }
bool VideoPlayer::flip_y() const { return false; }  // mpv draws into an FBO top row first (FLIP_Y = 0): row 0 = top
std::uint32_t VideoPlayer::width() const { return impl_->tex_w; }
std::uint32_t VideoPlayer::height() const { return impl_->tex_h; }
VideoState VideoPlayer::state() const {
    const auto& s = *impl_;
    if (s.failed) return VideoState::error;
    if (!s.loaded || !s.rendered) return VideoState::loading;
    if (s.eof) return VideoState::ended;
    return s.paused ? VideoState::paused : VideoState::playing;
}
double VideoPlayer::position_s() const { return impl_->position; }
double VideoPlayer::duration_s() const { return impl_->duration; }
std::string VideoPlayer::error() const { return impl_->failed ? impl_->why : std::string(); }
std::string VideoPlayer::report() const {
    const auto& s = *impl_;
    auto num = [](double v) { char b[32]; std::snprintf(b, sizeof b, "%.2f", std::isfinite(v) ? v : 0.0); return std::string(b); };
    const auto t = s.pacer.stats();
    return "{\"readback\":"+s.readback.json().dump()+",\"decoder\":\"libmpv\",\"hwdec\":\"" + s.hwdec + "\",\"va_device\":\"" + (s.render_node.empty() ? std::string("default") : s.render_node) +
           "\",\"video\":[" + std::to_string(s.video_w) + "," + std::to_string(s.video_h) +
           "],\"texture\":[" + std::to_string(s.tex_w) + "," + std::to_string(s.tex_h) + "],\"frames\":" + std::to_string(s.frames) +
           ",\"mip_levels\":" + std::to_string(s.levels) + ",\"anisotropy\":" + num(s.anisotropy) +
           ",\"reallocations\":" + std::to_string(s.reallocations) + ",\"gl_errors\":" + std::to_string(s.gl_errors) +
           ",\"reopens\":" + std::to_string(s.reopens) + ",\"discarded\":" + std::to_string(s.discarded) +
           ",\"drops\":" + std::to_string(s.drops) + ",\"quality_set\":" + std::to_string(s.quality_set) +
           ",\"quality_errors\":" + std::to_string(s.quality_errors) + ",\"held\":" + std::to_string(t.held) + ",\"early\":" + std::to_string(t.early) +
           ",\"late\":" + std::to_string(t.late) + ",\"lead_ms\":[" + num(t.lead_min_ms) + "," + num(t.lead_mean_ms) + "," +
           num(t.lead_max_ms) + "],\"render_ms\":[" + num(s.render_sum_ms / double(s.frames ? s.frames : 1)) + "," + num(s.render_max_ms) +
           "],\"interval_ms\":" + num(double(s.pacer.interval_ns()) / 1e6) + ",\"state\":\"" + video_state_name(state()) + "\",\"error\":\"" + error() + "\"}";
}
#endif
}
