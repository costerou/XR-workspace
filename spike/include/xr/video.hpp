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

// Frame timing (docs/vr-video.md#frame-timing): the display frame a decoded video frame goes on. The decoder (mpv) gives
// each frame a target time, when it should be seen in sync with the sound, and hands it over up to 50 ms before that.
// A frame drawn on a display frame is taken to be on screen one display interval later (the next vblank after the
// swap); the interval is the median of the last 15 gaps between display frames (120 Hz until 5 are known), so a missed
// vsync does not move it. A frame goes on the
// display frame whose vblank is nearest its target. After one frame, the next keeps the video's own cadence (2 display
// frames at 60 fps on 120 Hz, 4 at 30) as long as that keeps it at most one display interval early; otherwise a target
// halfway between two vblanks flips between them with timing noise (judder). So every frame is on screen between half a
// display interval late and one interval early. All times are in ns, on one clock.
class VideoFramePacer {
public:
    static constexpr std::int64_t kDefaultIntervalNs = 1'000'000'000 / 120;
    static constexpr std::int64_t kMaxHoldNs = 100'000'000;  // never hold a frame longer (mpv hands them over <= 50 ms early)
    void tick(std::int64_t now);                      // once per display frame, before due()
    bool due(std::int64_t target, std::int64_t now);  // draw the pending frame (due at `target`) on this display frame?
    void drawn(std::int64_t target, std::int64_t now);  // it was drawn now: the next frame's cadence starts here
    void untimed();                                   // a frame without a target (a redraw) was drawn: no cadence to keep
    void restart();                                   // a new file or stream: no cadence, nothing pending
    std::int64_t interval_ns() const { return interval_; }
    struct Stats {
        std::uint64_t held = 0, timed = 0, early = 0, late = 0;  // early / late: by more than one display interval
        double lead_min_ms = 0, lead_mean_ms = 0, lead_max_ms = 0;  // target - on screen; > 0: on screen before its time
    };
    Stats stats() const;
private:
    static constexpr std::size_t kSamples = 15;
    std::array<std::int64_t, kSamples> samples_{};
    std::size_t count_ = 0, next_ = 0;
    std::int64_t last_tick_ = 0, interval_ = kDefaultIntervalNs, pending_since_ = 0, last_target_ = 0, last_drawn_ = 0;
    std::uint64_t held_ = 0, timed_ = 0, early_ = 0, late_ = 0;
    double lead_sum_ = 0, lead_min_ = 0, lead_max_ = 0;
};

// Quality settings (docs/vr-video.md#quality-menu; tools/native_spike/quality.py SETTINGS and DEFAULTS mirror these).
// The defaults are what the renderer does without a `quality` frame; quality.py sends the saved settings after every
// open. Read them through VideoMode::quality(). Wire: "filter": "linear" | "nearest", "deband": bool.
enum class VideoFilter : std::uint8_t { linear = 0, nearest = 1 };
struct VideoQuality {
    VideoFilter filter = VideoFilter::linear;  // how the projection pass samples the decoded frame
    bool deband = false;                       // mpv's debanding pass while it converts each frame into the texture
    bool operator==(const VideoQuality&) const = default;
};
// Quality menu rows on the controls card: labels and values from the supervisor (printable ASCII).
constexpr std::size_t kMaxQualityRows = 8, kMaxQualityText = 24;
struct VideoMenuRow {
    std::string label, value;
    bool operator==(const VideoMenuRow&) const = default;
};

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
    // The quality menu (Q): when open the card shows these rows, `menu_selected` highlighted, instead of its usual body.
    bool menu = false;
    std::vector<VideoMenuRow> menu_rows;
    std::uint32_t menu_selected = 0;
    bool operator==(const VideoCardState&) const = default;
};
// Key hints on the card's footer (shared with the help text in docs/vr-video.md).
std::vector<std::string> video_card_hints(bool ascii);
std::vector<std::string> video_menu_hints(bool ascii);  // the same, while the quality menu is open
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
