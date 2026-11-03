#pragma once
// VR video decode (docs/vr-video.md): libmpv renders each decoded frame into a GL texture the projection pass samples.
// mpv decodes with hardware where it can: VA-API on the render node of the GPU behind the current EGL context, each
// decoded surface imported into EGL as a dmabuf with no CPU copy (vaapi-copy on that GPU, then software, as fallbacks);
// audio goes to the default PipeWire/Pulse sink. Each frame is drawn on the display frame nearest the time mpv wants it
// on screen (A/V sync), not as soon as mpv hands it over (docs/vr-video.md#frame-timing).
// Everything here runs on the renderer thread with the compositor's EGL context current. Only asynchronous mpv
// commands and non-blocking event polls are used from that thread, so rendering never waits on the mpv core.
// mpv is locked down: no config files, scripts, ytdl, OSC, key bindings, sibling subtitle/audio files, shader or ICC
// cache (no disk writes), and nothing but the inherited file descriptor is ever opened.
// Built without libmpv (XR_HAVE_MPV unset), open() always fails with a reason and the run keeps its desktops.
#include "xr/video.hpp"
#include <cstdint>
#include <memory>
#include <string>

struct wl_display;

namespace xr {
class VideoPlayer {
public:
    static bool available();  // built with libmpv
    struct Options { bool audio = true; bool hwdec = true; wl_display* display = nullptr; };
    // Takes ownership of `fd` (a readable regular file opened by the supervisor), also on failure. `display` is only used
    // for VA-API when the EGL context does not name its render node.
    static std::unique_ptr<VideoPlayer> open(int fd, const Options& options, std::string& why);
    // Play the file behind `fd` in this player instead (a web video's seek restarts its stream; another video replaces
    // this one), keeping mpv's renderer, shaders and hardware decoder: the next first frame comes sooner and the
    // renderer thread never waits for mpv to tear its video output down. The previous file stops over the next few
    // update() calls; texture() is 0 until the new file's first frame, as for a new player. False, with `fd` untouched,
    // if this player cannot be reused (it failed, its file has not started, it has drawn about an hour of frames, or
    // `options` differ): open a new one.
    bool reopen(int fd, const Options& options);
    ~VideoPlayer();
    VideoPlayer(const VideoPlayer&) = delete;
    VideoPlayer& operator=(const VideoPlayer&) = delete;
    void toggle_pause();
    void seek(double relative_s);
    void set_deband(bool on);    // mpv's `deband` (async; applies from the next frame mpv renders, kept across reopen)
    // Once per output frame, before drawing: handle mpv events and, if mpv has a frame due on this display frame, render
    // it into texture(). Restores the GL state the compositor relies on afterwards and swallows (counts) GL errors mpv
    // leaves. Always true with libmpv; a playback failure shows as state() == error instead.
    bool update();
    void report_swap();          // after eglSwapBuffers (mpv's frame timing)
    unsigned texture() const;    // 0 until the first frame has been rendered
    bool flip_y() const;         // texture row 0 is the bottom of the picture
    std::uint32_t width() const; // texture size (the decoded size, scaled down to kVideoMaxTextureSide)
    std::uint32_t height() const;
    VideoState state() const;
    double position_s() const;
    double duration_s() const;
    std::string error() const;   // printable ASCII, bounded; empty unless state() == error
    std::string report() const;  // bounded JSON: hwdec in use, sizes, frames, frame timing; no pixel data
private:
    VideoPlayer() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
