#include "video_player.hpp"
#include <unistd.h>
#ifdef XR_HAVE_MPV
#include <mpv/client.h>
#include <mpv/render_gl.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <algorithm>
#include <atomic>
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
enum Prop : std::uint64_t { kPause = 1, kTime, kDuration, kEof, kWidth, kHeight, kHwdec };
}

struct VideoPlayer::Impl {
    mpv_handle* mpv = nullptr;
    mpv_render_context* render = nullptr;
    std::atomic<bool> update_pending{false};
    GLuint fbo = 0, tex = 0;
    std::uint32_t tex_w = 0, tex_h = 0, video_w = 0, video_h = 0;
    bool loaded = false, paused = false, eof = false, failed = false, rendered = false;
    double position = 0, duration = 0;
    std::string why, hwdec = "unknown", last_log;
    std::uint64_t frames = 0, reallocations = 0, gl_errors = 0;
    ~Impl() {
        if (render) mpv_render_context_free(render);  // GL context current (caller's contract)
        if (mpv) mpv_terminate_destroy(mpv);
        if (fbo) glDeleteFramebuffers(1, &fbo);
        if (tex) glDeleteTextures(1, &tex);
    }
    void fail(std::string reason) { if (!failed) { failed = true; why = reason.empty() ? std::string("playback failed") : ascii(reason.c_str()); } }
    bool allocate() {
        const auto size = video_texture_size(video_w, video_h);
        GLint max = 0; glGetIntegerv(GL_MAX_TEXTURE_SIZE, &max);
        const auto fitted = max > 0 ? video_texture_size(size[0], size[1], std::uint32_t(max)) : size;
        if (!fitted[0] || !fitted[1]) return false;
        if (fitted[0] == tex_w && fitted[1] == tex_h && tex) return true;
        if (!tex) glGenTextures(1, &tex);
        if (!fbo) glGenFramebuffers(1, &fbo);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, GLsizei(fitted[0]), GLsizei(fitted[1]), 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
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
        switch (e.event_id) {
        case MPV_EVENT_FILE_LOADED: loaded = true; break;
        case MPV_EVENT_END_FILE: {
            const auto* end = static_cast<const mpv_event_end_file*>(e.data);
            if (end && end->reason == MPV_END_FILE_REASON_ERROR)
                fail(std::string(mpv_error_string(end->error)) + (last_log.empty() ? "" : ": " + last_log));
            else if (!loaded) fail(last_log.empty() ? "no playable video" : last_log);
            break;
        }
        case MPV_EVENT_LOG_MESSAGE: {
            const auto* m = static_cast<const mpv_event_log_message*>(e.data);
            if (m && m->log_level <= MPV_LOG_LEVEL_ERROR) last_log = ascii(m->text);
            if (m && std::getenv("XR_MPV_LOG")) std::fprintf(stderr, "mpv[%s] %s", m->prefix, m->text);
            break;
        }
        case MPV_EVENT_PROPERTY_CHANGE: {
            const auto* p = static_cast<const mpv_event_property*>(e.data);
            if (!p || !p->data) break;
            if (p->format == MPV_FORMAT_FLAG) {
                const bool v = *static_cast<int*>(p->data) != 0;
                if (e.reply_userdata == kPause) paused = v; else if (e.reply_userdata == kEof) eof = v;
            } else if (p->format == MPV_FORMAT_DOUBLE) {
                const double v = *static_cast<double*>(p->data);
                if (!std::isfinite(v)) break;
                if (e.reply_userdata == kTime) position = std::max(0.0, v); else if (e.reply_userdata == kDuration) duration = std::max(0.0, v);
            } else if (p->format == MPV_FORMAT_INT64) {
                const auto v = *static_cast<std::int64_t*>(p->data);
                if (v > 0 && v <= 65536) { if (e.reply_userdata == kWidth) video_w = std::uint32_t(v); else if (e.reply_userdata == kHeight) video_h = std::uint32_t(v); }
            } else if (p->format == MPV_FORMAT_STRING && e.reply_userdata == kHwdec) {
                hwdec = ascii(*static_cast<char**>(p->data), 32);
            }
            break;
        }
        default: break;
        }
    }
    // mpv leaves its own bindings behind; put back what the compositor's client-array GLES2 drawing assumes.
    void restore_gl() {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glBindVertexArray(0);
        glBindBuffer(GL_ARRAY_BUFFER, 0); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0); glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
        GLint attribs = 0; glGetIntegerv(GL_MAX_VERTEX_ATTRIBS, &attribs);
        for (GLint i = 0; i < attribs; ++i) glDisableVertexAttribArray(GLuint(i));
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4); glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        glPixelStorei(GL_UNPACK_SKIP_ROWS, 0); glPixelStorei(GL_UNPACK_SKIP_PIXELS, 0);
        glDisable(GL_BLEND); glDisable(GL_SCISSOR_TEST); glDisable(GL_DEPTH_TEST); glDisable(GL_STENCIL_TEST); glDisable(GL_CULL_FACE);
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
        for (int unit = 7; unit >= 0; --unit) { glActiveTexture(GLenum(GL_TEXTURE0 + unit)); glBindTexture(GL_TEXTURE_2D, 0); }
        glUseProgram(0);
    }
};

bool VideoPlayer::available() { return true; }

std::unique_ptr<VideoPlayer> VideoPlayer::open(int fd, const Options& options, std::string& why) {
    if (fd < 0) { why = "no video file"; return nullptr; }
    std::unique_ptr<VideoPlayer> out(new VideoPlayer());
    out->impl_ = std::make_unique<Impl>();
    auto& s = *out->impl_;
    s.mpv = mpv_create();
    if (!s.mpv) { ::close(fd); why = "mpv_create failed"; return nullptr; }
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
    };
    for (const auto& o : opts)
        if (mpv_set_option_string(s.mpv, o.name, o.value) < 0 && o.required) {
            ::close(fd); why = std::string("mpv option ") + o.name; return nullptr;
        }
    mpv_request_log_messages(s.mpv, std::getenv("XR_MPV_LOG") ? std::getenv("XR_MPV_LOG") : "error");
    if (mpv_initialize(s.mpv) < 0) { ::close(fd); why = "mpv_initialize failed"; return nullptr; }
    mpv_observe_property(s.mpv, kPause, "pause", MPV_FORMAT_FLAG);
    mpv_observe_property(s.mpv, kTime, "time-pos", MPV_FORMAT_DOUBLE);
    mpv_observe_property(s.mpv, kDuration, "duration", MPV_FORMAT_DOUBLE);
    mpv_observe_property(s.mpv, kEof, "eof-reached", MPV_FORMAT_FLAG);
    mpv_observe_property(s.mpv, kWidth, "dwidth", MPV_FORMAT_INT64);
    mpv_observe_property(s.mpv, kHeight, "dheight", MPV_FORMAT_INT64);
    mpv_observe_property(s.mpv, kHwdec, "hwdec-current", MPV_FORMAT_STRING);
    mpv_opengl_init_params gl{proc_address, nullptr};
    const char* api = MPV_RENDER_API_TYPE_OPENGL;
    mpv_render_param params[] = {
        {MPV_RENDER_PARAM_API_TYPE, const_cast<char*>(api)},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl},
        {options.display ? MPV_RENDER_PARAM_WL_DISPLAY : MPV_RENDER_PARAM_INVALID, options.display},
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

void VideoPlayer::toggle_pause() {
    auto& s = *impl_;
    if (s.failed) return;
    const char* cmd[] = {"cycle", "pause", nullptr};
    mpv_command_async(s.mpv, 0, cmd);
}

void VideoPlayer::seek(double relative_s) {
    auto& s = *impl_;
    if (s.failed || !std::isfinite(relative_s)) return;
    const std::string amount = std::to_string(relative_s);
    const char* cmd[] = {"seek", amount.c_str(), "relative+exact", nullptr};  // exact: works between keyframes and past the last one
    mpv_command_async(s.mpv, 0, cmd);
}

bool VideoPlayer::update() {
    auto& s = *impl_;
    for (int i = 0; i < 256; ++i) {  // bounded per frame
        const mpv_event* e = mpv_wait_event(s.mpv, 0);
        if (!e || e->event_id == MPV_EVENT_NONE) break;
        s.event(*e);
    }
    if (s.video_w && s.video_h && !s.failed) s.allocate();
    s.update_pending = false;
    const auto flags = mpv_render_context_update(s.render);
    if (!(flags & MPV_RENDER_UPDATE_FRAME) || !s.fbo || !s.tex_w) return true;
    mpv_opengl_fbo target{int(s.fbo), int(s.tex_w), int(s.tex_h), GL_RGBA8};
    int flip = 0, block = 0;
    mpv_render_param params[] = {
        {MPV_RENDER_PARAM_OPENGL_FBO, &target}, {MPV_RENDER_PARAM_FLIP_Y, &flip},
        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block}, {MPV_RENDER_PARAM_INVALID, nullptr}};
    const int rc = mpv_render_context_render(s.render, params);
    s.restore_gl();
    // Errors mpv leaves behind are counted, never passed on: the compositor treats a GL error as fatal for the run.
    for (int i = 0; i < 16 && glGetError() != GL_NO_ERROR; ++i) ++s.gl_errors;
    if (rc < 0) { s.fail("mpv render failed"); return true; }
    s.rendered = true; ++s.frames;
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
    return "{\"decoder\":\"libmpv\",\"hwdec\":\"" + s.hwdec + "\",\"video\":[" + std::to_string(s.video_w) + "," + std::to_string(s.video_h) +
           "],\"texture\":[" + std::to_string(s.tex_w) + "," + std::to_string(s.tex_h) + "],\"frames\":" + std::to_string(s.frames) +
           ",\"reallocations\":" + std::to_string(s.reallocations) + ",\"gl_errors\":" + std::to_string(s.gl_errors) + ",\"state\":\"" + video_state_name(state()) + "\",\"error\":\"" + error() + "\"}";
}
#endif
}
