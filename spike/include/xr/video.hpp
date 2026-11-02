#pragma once
// VR video (docs/vr-video.md): 360° and 180° equirectangular and 180°+ fisheye video, mono or stereo (side-by-side,
// top-bottom), shown head-tracked in the glasses instead of the desktops. Every output pixel is re-projected from the
// decoded frame with the current head pose, so the view follows the head at the display rate whatever the video's frame
// rate. This header is the pure, offline-testable part: formats, the projection maths (the GLSL in video_gl.cpp mirrors
// video_uv), output eye viewports and the controls card state. Decoding is video_player.hpp; drawing is video_gl.hpp.
#include "xr/hud.hpp"
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace xr {
// Wire values (ipc.hpp VideoPayload, tools/native_spike/video.py): "360" | "180" | "fisheye"; "mono" | "sbs" | "tb".
enum class VideoProjection : std::uint8_t { equirect360 = 0, equirect180 = 1, fisheye = 2 };
enum class VideoStereo : std::uint8_t { mono = 0, sbs = 1, tb = 2 };
// Fisheye lens field of view (equidistant model). Ignored for equirectangular video.
constexpr std::uint32_t kFisheyeFovMinDeg = 180, kFisheyeFovMaxDeg = 220;
struct VideoFormat {
    VideoProjection projection = VideoProjection::equirect360;
    VideoStereo stereo = VideoStereo::mono;
    std::uint32_t fov_deg = 180;
    bool operator==(const VideoFormat&) const = default;
};
// "360° mono", "180° side-by-side 3D", "Fisheye 190° side-by-side 3D"; `ascii` writes " deg" (the pixel font is ASCII-only).
std::string video_format_label(const VideoFormat& format, bool ascii = false);

// Texture sub-rectangle holding `eye` (0 left, 1 right), in 0..1 with v = 0 at the top of the frame. Mono: the whole
// frame for both eyes. Side-by-side: left eye on the left. Top-bottom: left eye on top (the common convention).
struct UvRect { float x = 0, y = 0, w = 1, h = 1; };
UvRect eye_rect(VideoStereo stereo, int eye);
// Where a view direction (GL eye/world frame: +x right, +y up, -z forward; any length) lands in the frame for `eye`,
// as (u, v) in 0..1 with v = 0 at the top; nullopt outside the picture (behind a 180° video, outside the fisheye circle).
// Straight ahead is the centre of the eye's image. 360°: longitude wraps, u = 0.5 + lon / 2π. 180°: u = 0.5 + lon / π
// for |lon| <= 90°. Fisheye: equidistant, radius = angle from forward / (fov / 2).
std::optional<std::array<float, 2>> video_uv(const VideoFormat& format, std::array<float, 3> direction, int eye);
// World direction seen through output NDC (x, y in -1..1) with tan(half FOV) `half_w`/`half_h`, after the head rotation
// `view` (pose.hpp: world -> eye, row-major). Inverse of the desktops' projection: direction = viewᵀ · (x·hw, y·hh, -1).
std::array<float, 3> view_ray(const Mat3& view, float ndc_x, float ndc_y, float half_w, float half_h);
// tan(half FOV) per axis for a `width` x `height` viewport covering a `diagonal_deg` diagonal field of view.
std::array<float, 2> half_fov_tan(float diagonal_deg, std::uint32_t width, std::uint32_t height);
// Output eyes. A surface at least 3:1 wide is the glasses' side-by-side 3D mode (3840x1200 / 3840x1080): one viewport per
// eye, left half = left eye. Anything else is the 2D mode: one viewport, the left eye (stereo video is shown mono).
struct EyeViewport { std::uint32_t x = 0, y = 0, width = 0, height = 0; int eye = 0; };
std::vector<EyeViewport> eye_viewports(std::uint32_t width, std::uint32_t height);
// Decoded frames larger than this (either side) are scaled down to fit before projection (texture memory, sampling cost).
constexpr std::uint32_t kVideoMaxTextureSide = 8192;
std::array<std::uint32_t, 2> video_texture_size(std::uint32_t width, std::uint32_t height, std::uint32_t max_side = kVideoMaxTextureSide);

// Player state. Wire (status JSON "video", tools/native_spike/video.py VIDEO_STATES): off, loading, playing, paused,
// ended, error.
enum class VideoState : std::uint8_t { off, loading, playing, paused, ended, error };
const char* video_state_name(VideoState state);
// "0:07", "4:05", "1:02:03"; negative or non-finite: "0:00".
std::string format_clock(double seconds);

// Controls card (head-locked, near the bottom of the view). It follows the OLED rules of the help card and placement
// menu: the help card's dimmed text and accent (nothing above kCardMaxLevel), no pixel orbit or drift, and the supervisor
// closes it after kOverlayIdleCloseS without input (tools/native_spike/video.py).
struct VideoCardState {
    std::string title;
    VideoFormat format;
    VideoState state = VideoState::loading;
    std::uint32_t position_s = 0, duration_s = 0;  // whole seconds: the card re-renders at most once a second
    std::string detail;                            // error text (printable ASCII), empty otherwise
    bool operator==(const VideoCardState&) const = default;
};
// Key hints on the card's footer (shared with the help text in docs/vr-video.md).
std::vector<std::string> video_card_hints(bool ascii);
HudImage render_video_card(const VideoCardState& state, std::array<float, 3> accent, const HudFont* font = nullptr);

class VideoControlsOverlay {
public:
    bool set_visible(bool visible, double now_ms);              // true if visibility changed
    bool update(const VideoCardState& state, double now_ms);    // re-renders when visible and changed; true if redrawn
    void set_font(std::unique_ptr<HudFont> font);
    bool visible() const { return fade_.visible(); }
    float alpha(double now_ms) const;
    bool animating(double now_ms) const;
    const HudImage& image() const { return image_; }
    std::uint64_t generation() const { return generation_; }
    std::uint64_t shown_count() const { return shown_; }
private:
    void render();
    CardFade fade_{kHelpFadeInMs, kHelpFadeOutMs};
    VideoCardState state_, rendered_;
    bool dirty_ = true;
    std::uint64_t generation_ = 0, shown_ = 0;
    HudImage image_;
    std::unique_ptr<HudFont> font_;
};
}
