#pragma once
// VR video mode of the renderer (docs/vr-video.md): while active it replaces the desktops in the glasses with the
// head-tracked video and its controls card. Owns the decoder, the projection pass and the card; the supervisor owns
// what is shown (open/close, format, card visibility) and sends it as `video` control frames (ipc.hpp VideoPayload).
// GL context current for every call and for destruction.
#include "xr/video.hpp"
#include <cstdint>
#include <memory>
#include <string>

struct wl_display;

namespace xr {
class VideoPlayer;
class VideoSphere;
class VideoMode {
public:
    VideoMode();
    ~VideoMode();
    void set_font(std::unique_ptr<HudFont> font);
    // Start playing the file behind `fd` (ownership taken). A failure keeps the mode active in the error state, so the
    // card can say why; the supervisor then closes it.
    void open(int fd, const VideoFormat& format, std::string title, wl_display* display, double now_ms, bool audio = true);
    void close();
    bool active() const { return active_; }
    void toggle_pause();
    void seek(double relative_s);
    void set_format(const VideoFormat& format);
    void set_controls(bool visible, double now_ms);
    // Before drawing a frame: decode/update and refresh the card. False on a GL error.
    bool prepare(double now_ms);
    // Draw the video into the current framebuffer (`width` x `height`), one viewport per output eye.
    void draw(const Mat3& view, float diagonal_fov_deg, std::uint32_t width, std::uint32_t height);
    void after_swap();
    const HudImage* card(double now_ms, float& alpha, std::uint64_t& generation) const;
    bool animating(double now_ms) const;  // the card is fading
    VideoState state() const;
    std::string report() const;           // bounded JSON for the exit report
private:
    bool active_ = false;
    VideoFormat format_;
    std::string title_, why_;
    std::unique_ptr<VideoPlayer> player_;
    std::unique_ptr<VideoSphere> sphere_;
    VideoControlsOverlay card_;
    std::uint64_t opened_ = 0, frames_drawn_ = 0;
};
}
