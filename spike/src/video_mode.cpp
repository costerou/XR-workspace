#include "video_mode.hpp"
#include "video_gl.hpp"
#include "video_player.hpp"
#include <GLES2/gl2.h>
#include <cmath>
#include <unistd.h>

namespace xr {
VideoMode::VideoMode() = default;
VideoMode::~VideoMode() { close(); }

void VideoMode::set_font(std::unique_ptr<HudFont> font) { card_.set_font(std::move(font)); }

void VideoMode::open(int fd, const VideoFormat& format, std::string title, wl_display* display, double now_ms, bool audio) {
    if (active_) card_.set_visible(false, 0);  // as close(): the open fades the card in again
    active_ = true; format_ = format; title_ = std::move(title); why_.clear(); ++opened_;
    if (!sphere_) sphere_ = VideoSphere::create(why_);
    const VideoPlayer::Options options{audio, true, display};
    if (!sphere_) { if (fd >= 0) ::close(fd); player_.reset(); }
    // A web video's seek (a relay restart) or another video while one plays: the same player, without tearing mpv down.
    else if (!player_ || !player_->reopen(fd, options)) { player_.reset(); player_ = VideoPlayer::open(fd, options, why_); }
    card_.set_visible(true, now_ms);
    prepare(now_ms);
}

void VideoMode::close() {
    player_.reset();  // mpv render context first, while the GL context is current
    if (active_) card_.set_visible(false, 0);  // the next open fades it in again
    active_ = false;
}

void VideoMode::toggle_pause() { if (player_) player_->toggle_pause(); }
void VideoMode::seek(double s) { if (player_) player_->seek(s); }
void VideoMode::set_format(const VideoFormat& f) { format_ = f; }
void VideoMode::set_controls(bool visible, double now_ms) { card_.set_visible(visible, now_ms); }

VideoState VideoMode::state() const {
    if (!active_) return VideoState::off;
    if (!player_) return VideoState::error;
    return player_->state();
}

bool VideoMode::prepare(double now_ms) {
    if (!active_) return true;
    const bool ok = player_ ? player_->update() : true;
    VideoCardState c;
    c.title = title_; c.format = format_; c.state = state();
    if (player_) {
        c.position_s = std::uint32_t(std::max(0.0, player_->position_s()));
        c.duration_s = std::uint32_t(std::max(0.0, player_->duration_s()));
        if (c.state == VideoState::error) c.detail = player_->error();
    } else c.detail = why_;
    if (c.detail.size() > 64) c.detail.resize(64);
    card_.update(c, now_ms);
    return ok;
}

void VideoMode::draw(const Mat3& view, float diagonal_fov_deg, std::uint32_t width, std::uint32_t height) {
    glClearColor(0, 0, 0, 1);
    glViewport(0, 0, GLsizei(width), GLsizei(height)); glClear(GL_COLOR_BUFFER_BIT);
    const unsigned tex = player_ ? player_->texture() : 0;
    if (!active_ || !sphere_ || !tex) return;  // loading or failed: black behind the card
    for (const auto& e : eye_viewports(width, height)) {
        glViewport(GLint(e.x), GLint(e.y), GLsizei(e.width), GLsizei(e.height));
        const auto half = half_fov_tan(diagonal_fov_deg, e.width, e.height);
        sphere_->draw(tex, player_->flip_y(), format_, e.eye, view, half[0], half[1]);
    }
    glViewport(0, 0, GLsizei(width), GLsizei(height));
    ++frames_drawn_;
}

void VideoMode::after_swap() { if (player_) player_->report_swap(); }

const HudImage* VideoMode::card(double now_ms, float& alpha, std::uint64_t& generation) const {
    alpha = card_.alpha(now_ms); generation = card_.generation();
    return alpha > 0.f ? &card_.image() : nullptr;
}

bool VideoMode::animating(double now_ms) const { return card_.animating(now_ms); }

std::string VideoMode::report() const {
    return "{\"opened\":" + std::to_string(opened_) + ",\"frames_drawn\":" + std::to_string(frames_drawn_) + ",\"card_shown\":" +
           std::to_string(card_.shown_count()) + ",\"state\":\"" + video_state_name(state()) + "\",\"format\":\"" +
           video_format_label(format_, true) + "\",\"player\":" + (player_ ? player_->report() : std::string("null")) + "}";
}
}
