#include "video_mode.hpp"
#include <nlohmann/json.hpp>
#include "video_gl.hpp"
#include "video_player.hpp"
#include <GLES2/gl2.h>
#include <algorithm>
#include <cmath>
#include <unistd.h>

namespace xr {
VideoMode::VideoMode() = default;
VideoMode::~VideoMode() { close(); }

void VideoMode::set_font(std::unique_ptr<HudFont> font) { card_.set_font(std::move(font)); }
bool VideoMode::set_accent(std::array<float, 3> accent) { return card_.set_accent(accent); }

void VideoMode::open(int fd, const VideoFormat& format, std::string title, wl_display* display, double now_ms, bool audio) {
    if (active_) card_.set_visible(false, 0);  // as close(): the open fades the card in again
    format_drawn_.reset();filter_drawn_.reset();
    active_ = true; format_ = format; title_ = std::move(title); why_.clear(); ++opened_;
    if (!sphere_) sphere_ = VideoSphere::create(why_);
    const VideoPlayer::Options options{audio, true, display};
    if (!sphere_) { if (fd >= 0) ::close(fd); player_.reset(); }
    // A web video's seek (a relay restart) or another video while one plays: the same player, without tearing mpv down.
    else if (!player_ || !player_->reopen(fd, options)) { player_.reset(); player_ = VideoPlayer::open(fd, options, why_); }
    menu_ = false;
    apply_quality();
    card_.set_visible(true, now_ms);
    prepare(now_ms);
}

void VideoMode::close() {
    player_.reset();  // mpv render context first, while the GL context is current
    if (active_) card_.set_visible(false, 0);  // the next open fades it in again
    active_ = false; menu_ = false;format_drawn_.reset();filter_drawn_.reset();
}

void VideoMode::set_quality(const VideoQuality& quality) { quality_ = quality; apply_quality(); }

// The player's settings now; the texture filter is set in draw(), on whatever texture holds the frame.
void VideoMode::apply_quality() { if (player_) player_->set_deband(quality().deband); }

void VideoMode::set_menu(bool visible, std::vector<VideoMenuRow> rows, std::uint32_t selected) {
    menu_ = visible && !rows.empty();
    menu_rows_ = std::move(rows);
    menu_selected_ = menu_rows_.empty() ? 0 : std::min<std::uint32_t>(selected, std::uint32_t(menu_rows_.size() - 1));
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
    if (menu_) { c.menu = true; c.menu_rows = menu_rows_; c.menu_selected = menu_selected_; }
    if (player_) {
        c.position_s = std::uint32_t(std::max(0.0, player_->position_s()));
        c.duration_s = std::uint32_t(std::max(0.0, player_->duration_s()));
        if (c.state == VideoState::error) c.detail = player_->error();
    } else c.detail = why_;
    if (c.detail.size() > 64) c.detail.resize(64);
    card_.update(c, now_ms);
    return ok;
}

void VideoMode::draw(const Mat3& view, float diagonal_fov_deg, std::uint32_t width, std::uint32_t height, const Behind& background) {
    glClearColor(0, 0, 0, 1);
    glViewport(0, 0, GLsizei(width), GLsizei(height)); glClear(GL_COLOR_BUFFER_BIT);
    const unsigned tex = player_ ? player_->texture() : 0;
    const bool picture = active_ && sphere_ && tex;  // loading or failed: only the background behind the card
    if (picture) {
        // Every frame: the player may replace its texture (a new size, a new player); two state calls cost nothing.
        // Minified areas keep the player's trilinear mipmaps (video_texture_filtering); only magnification changes.
        if (!sphere_->forced()) sphere_->set_filter(quality().filter);
        const GLint mag = sphere_->filter() == VideoFilter::nearest ? GL_NEAREST : GL_LINEAR;
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, mag);
    }
    const bool covered = picture && format_.projection == VideoProjection::equirect360;  // nothing of the background would show
    for (const auto& e : eye_viewports(width, height)) {
        glViewport(GLint(e.x), GLint(e.y), GLsizei(e.width), GLsizei(e.height));
        const auto half = half_fov_tan(diagonal_fov_deg, e.width, e.height);
        const bool behind = background && !covered && background(half[0], half[1]);
        if (picture) sphere_->draw(tex, player_->flip_y(), format_, e.eye, view, half[0], half[1], behind);
    }
    glViewport(0, 0, GLsizei(width), GLsizei(height));
    if (picture) {++frames_drawn_;format_drawn_=format_;filter_drawn_=sphere_->filter();}
}

void VideoMode::after_swap() { if (player_) player_->report_swap(); }

const HudImage* VideoMode::card(double now_ms, float& alpha, std::uint64_t& generation) const {
    alpha = card_.alpha(now_ms); generation = card_.generation();
    return alpha > 0.f ? &card_.image() : nullptr;
}

bool VideoMode::animating(double now_ms) const { return card_.animating(now_ms); }

std::string VideoMode::report() const {
    using J=nlohmann::json;
    auto format=[](const VideoFormat& f)->J{return {{"projection",f.projection==VideoProjection::equirect360?"360":f.projection==VideoProjection::equirect180?"180":"fisheye"},{"stereo",f.stereo==VideoStereo::mono?"mono":f.stereo==VideoStereo::sbs?"sbs":"tb"},{"fov",f.fov_deg}};};
    J player=player_?J::parse(player_->report()):J(nullptr);
    J observed=player.is_object()?player.value("readback",J::object()):J::object();
    J typed={{"format_state",format(format_)},{"format_applied",format_drawn_?format(*format_drawn_):J(nullptr)},
      {"playback",{{"state",video_state_name(state())},{"paused",observed.value("paused",J(nullptr))},{"position_s",observed.value("position_s",J(nullptr))},{"duration_s",observed.value("duration_s",J(nullptr))},{"revision",observed.value("revision",J(0))},{"command_replies",observed.value("command_replies",J(0))},{"command_errors",observed.value("command_errors",J(0))},{"command_error",observed.value("command_error",J(""))},{"error",(player_?player_->error():why_).substr(0,256)}}},
      {"quality_readback",{{"filter_requested",video_filter_name(quality_.filter)},{"deband_requested",quality_.deband},{"filter_applied",filter_drawn_?J(video_filter_name(*filter_drawn_)):J(nullptr)},{"deband_applied",observed.value("deband_applied",J(nullptr))}}}};
    const std::string prefix=typed.dump();
    return prefix.substr(0,prefix.size()-1)+",\"opened\":" + std::to_string(opened_) + ",\"frames_drawn\":" + std::to_string(frames_drawn_) + ",\"card_shown\":" +
           std::to_string(card_.shown_count()) + ",\"state\":\"" + video_state_name(state()) + "\",\"format\":\"" +
           video_format_label(format_, true) + "\",\"filter\":\"" + video_filter_name(sphere_ ? sphere_->filter() : quality_.filter) + "\",\"forced\":" + (sphere_ && sphere_->forced() ? "true" : "false") + ",\"setting\":\"" + video_filter_name(quality_.filter) +
           "\",\"deband\":" + (quality_.deband ? "true" : "false") + ",\"player\":" + (player_ ? player_->report() : std::string("null")) + "}";
}
}
