#include "xr/video.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
constexpr float kPi = 3.14159265358979f;
}

std::string video_format_label(const VideoFormat& f, bool ascii) {
    const std::string deg = ascii ? " deg" : "°";
    std::string out;
    switch (f.projection) {
    case VideoProjection::equirect360: out = "360" + deg; break;
    case VideoProjection::equirect180: out = "180" + deg; break;
    case VideoProjection::fisheye: out = "Fisheye " + std::to_string(f.fov_deg) + deg; break;
    }
    switch (f.stereo) {
    case VideoStereo::mono: out += " mono"; break;
    case VideoStereo::sbs: out += " side-by-side 3D"; break;
    case VideoStereo::tb: out += " top-bottom 3D"; break;
    }
    return out;
}

UvRect eye_rect(VideoStereo stereo, int eye) {
    const float e = eye ? 1.f : 0.f;
    switch (stereo) {
    case VideoStereo::sbs: return {0.5f * e, 0, 0.5f, 1};
    case VideoStereo::tb: return {0, 0.5f * e, 1, 0.5f};
    case VideoStereo::mono: break;
    }
    return {0, 0, 1, 1};
}

std::optional<std::array<float, 2>> video_uv(const VideoFormat& f, std::array<float, 3> d, int eye) {
    const float n = std::sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (!(n > 1e-9f) || !std::isfinite(n)) return std::nullopt;
    for (auto& c : d) c /= n;
    float u = 0, v = 0;
    if (f.projection == VideoProjection::fisheye) {
        const float fov = float(std::clamp(f.fov_deg, kFisheyeFovMinDeg, kFisheyeFovMaxDeg)) * kPi / 180.f;
        const float theta = std::acos(std::clamp(-d[2], -1.f, 1.f));  // angle from straight ahead
        const float r = theta / (0.5f * fov);
        if (r > 1.f) return std::nullopt;
        const float l = std::hypot(d[0], d[1]);
        const float cx = l > 1e-7f ? d[0] / l : 0.f, cy = l > 1e-7f ? d[1] / l : 0.f;
        u = 0.5f + 0.5f * r * cx; v = 0.5f - 0.5f * r * cy;
    } else {
        const float lon = std::atan2(d[0], -d[2]), lat = std::asin(std::clamp(d[1], -1.f, 1.f));
        v = 0.5f - lat / kPi;
        if (f.projection == VideoProjection::equirect180) {
            if (std::fabs(lon) > 0.5f * kPi + 1e-6f) return std::nullopt;
            u = 0.5f + lon / kPi;
        } else {
            u = 0.5f + lon / (2 * kPi);
            u -= std::floor(u);  // wrap (the shader uses fract)
        }
    }
    const auto r = eye_rect(f.stereo, eye);
    return std::array<float, 2>{r.x + std::clamp(u, 0.f, 1.f) * r.w, r.y + std::clamp(v, 0.f, 1.f) * r.h};
}

std::array<float, 3> view_ray(const Mat3& view, float x, float y, float hw, float hh) {
    const std::array<float, 3> e{x * hw, y * hh, -1.f};
    return xr::apply(transpose(view), e);
}

std::array<float, 2> half_fov_tan(float diagonal_deg, std::uint32_t width, std::uint32_t height) {
    const double d = std::tan(double(diagonal_deg) * M_PI / 360.0), a = height ? double(width) / height : 16.0 / 10.0;
    return {float(d * a / std::sqrt(1 + a * a)), float(d / std::sqrt(1 + a * a))};
}

std::vector<EyeViewport> eye_viewports(std::uint32_t width, std::uint32_t height) {
    if (!width || !height) return {};
    if (width >= 3 * height) {
        const std::uint32_t half = width / 2;
        return {{0, 0, half, height, 0}, {half, 0, width - half, height, 1}};
    }
    return {{0, 0, width, height, 0}};
}

std::array<std::uint32_t, 2> video_texture_size(std::uint32_t w, std::uint32_t h, std::uint32_t max_side) {
    if (!w || !h || !max_side) return {0, 0};
    if (w <= max_side && h <= max_side) return {w, h};
    const double s = double(max_side) / double(std::max(w, h));
    return {std::max<std::uint32_t>(1, std::uint32_t(std::lround(w * s))), std::max<std::uint32_t>(1, std::uint32_t(std::lround(h * s)))};
}

void VideoFramePacer::tick(std::int64_t now) {
    constexpr std::int64_t kMin = 2'000'000, kMax = 50'000'000;  // 500 Hz .. 20 Hz; anything else is not a display frame
    const std::int64_t dt = last_tick_ ? now - last_tick_ : 0;
    last_tick_ = now;
    if (dt < kMin || dt > kMax) return;
    samples_[next_] = dt;
    next_ = (next_ + 1) % kSamples;
    count_ = std::min(count_ + 1, kSamples);
    if (count_ < 5) return;  // too few gaps for a median: keep the default
    auto sorted = samples_;
    const auto mid = sorted.begin() + std::ptrdiff_t(count_ / 2);
    std::nth_element(sorted.begin(), mid, sorted.begin() + std::ptrdiff_t(count_));
    interval_ = *mid;
}

bool VideoFramePacer::due(std::int64_t target, std::int64_t now) {
    if (!pending_since_) pending_since_ = now;
    const std::int64_t t = interval_, early_by = target - (now + t);  // > 0: it would be on screen before its time
    if (early_by <= t / 2 || now - pending_since_ >= kMaxHoldNs) return true;  // the vblank nearest the target, or late
    const std::int64_t spacing = target - last_target_;
    if (last_target_ && spacing > 0 && spacing < 1'000'000'000 && early_by <= t && now - last_drawn_ >= spacing - t / 2) return true;
    ++held_;
    return false;
}

void VideoFramePacer::drawn(std::int64_t target, std::int64_t now) {
    const double lead = double(target - (now + interval_)) / 1e6, interval = double(interval_) / 1e6;
    if (!timed_) lead_min_ = lead_max_ = lead;
    lead_min_ = std::min(lead_min_, lead); lead_max_ = std::max(lead_max_, lead); lead_sum_ += lead; ++timed_;
    if (lead > interval) ++early_; else if (lead < -interval) ++late_;
    last_target_ = target; last_drawn_ = now; pending_since_ = 0;
}

void VideoFramePacer::untimed() { last_target_ = 0; pending_since_ = 0; }
void VideoFramePacer::restart() { untimed(); }

VideoFramePacer::Stats VideoFramePacer::stats() const {
    return {held_, timed_, early_, late_, lead_min_, timed_ ? lead_sum_ / double(timed_) : 0.0, lead_max_};
}

const char* video_state_name(VideoState s) {
    switch (s) {
    case VideoState::off: return "off";
    case VideoState::loading: return "loading";
    case VideoState::playing: return "playing";
    case VideoState::paused: return "paused";
    case VideoState::ended: return "ended";
    case VideoState::error: return "error";
    }
    return "off";
}

std::string format_clock(double seconds) {
    if (!std::isfinite(seconds) || seconds < 0) seconds = 0;
    const auto total = std::uint64_t(seconds);
    const auto h = total / 3600, m = (total / 60) % 60, s = total % 60;
    auto two = [](std::uint64_t n) { return (n < 10 ? "0" : "") + std::to_string(n); };
    return h ? std::to_string(h) + ":" + two(m) + ":" + two(s) : std::to_string(m) + ":" + two(s);
}

std::vector<std::string> video_card_hints(bool ascii) {
    return {std::string("Space  play / pause"), ascii ? "Left/Right  10 s" : "←/→  10 s", ascii ? "Shift+Left/Right  60 s" : "Shift+←/→  60 s",
            "Tab  format", "Q  quality", "Ctrl+Alt+Space  recenter", "Esc  back to desktops"};
}

std::vector<std::string> video_menu_hints(bool ascii) {
    return {ascii ? "Up/Down  setting" : "↑/↓  setting", ascii ? "Left/Right  change" : "←/→  change", "Q / Esc  close",
            "Space  play / pause", "Changes apply now and are saved."};
}

bool VideoControlsOverlay::set_visible(bool visible, double now_ms) {
    const bool changed = fade_.set(visible, now_ms);
    if (changed && visible) { ++shown_; if (dirty_ || !generation_) render(); }
    return changed;
}
bool VideoControlsOverlay::update(const VideoCardState& state, double) {
    if (state != state_) { state_ = state; dirty_ = true; }
    if (!dirty_ || !fade_.visible()) return false;
    render();
    return true;
}
void VideoControlsOverlay::render() {
    image_ = render_video_card(state_, {}, font_.get());
    rendered_ = state_; dirty_ = false; ++generation_;
}
void VideoControlsOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); dirty_ = true; }
float VideoControlsOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
bool VideoControlsOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }
}
