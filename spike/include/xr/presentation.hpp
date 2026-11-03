#pragma once
#include "xr/types.hpp"
#include "xr/metrics.hpp"
#include "xr/video.hpp"
#include "xr/hud.hpp"
#include "xr/desktops.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace xr {
// Narrow request boundary: intentionally no seat, focus, keyboard or injection API.
struct SurfaceRequests {
    virtual ~SurfaceRequests() = default;
    virtual void keyboard_interactivity(std::uint32_t) = 0;
    virtual void exclusive_zone(std::int32_t) = 0;
    virtual void empty_input_region() = 0;
    virtual void commit() = 0;
};
void apply_presentation_policy(SurfaceRequests&);
std::uint32_t layer_version(std::uint32_t advertised);
bool valid_surface_size(std::uint32_t width, std::uint32_t height);
// The buffer size that fills a width x height (logical) surface pixel for pixel at wp_fractional_scale_v1 preferred
// scale `scale120` (120ths: 150 = 1.25). The glasses run at 1.25, so the 1536x960 surface is drawn at the panel's own
// 1920x1200 instead of being upscaled by the compositor. No scale (0), or a result valid_surface_size refuses: unscaled.
std::array<std::uint32_t, 2> scaled_surface_size(std::uint32_t width, std::uint32_t height, std::uint32_t scale120);
std::uint32_t pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t width, std::uint32_t height);

// Pure callback/ownership gate used by the real adapter and offline tests.
std::optional<std::uint32_t> parse_pattern_duration(const std::string&);
class PatternCadence {
public:
    explicit PatternCadence(std::uint32_t duration_ms = 0, std::uint64_t start_ms = 0)
        : duration_(duration_ms), start_(start_ms) {}
    bool finished(std::uint64_t now) const { return stopped_ || (duration_ && now >= start_ && now - start_ >= duration_); }
    bool ready(std::uint64_t now, bool buffer_available, bool feedback_available) const {
        return !finished(now) && dirty_ && !waiting_ && buffer_available && feedback_available;
    }
    void committed() { dirty_ = false; waiting_ = true; if (duration_) ++phase_; }
    void frame_done() { if (waiting_) { waiting_ = false; if (duration_) dirty_ = true; } }
    void invalidate() { dirty_ = true; }
    void stop() { stopped_ = true; }
    std::uint64_t phase() const { return phase_; }
private:
    std::uint32_t duration_;
    std::uint64_t start_, phase_ = 0;
    bool dirty_ = true, waiting_ = false, stopped_ = false;
};
std::uint32_t animated_pattern_pixel(std::uint32_t x, std::uint32_t y, std::uint32_t width,
                                     std::uint32_t height, std::uint64_t phase);
// The whole width x height frame of animated_pattern_pixel into rows `stride_px` apart, pixel for pixel the same, from
// three computed rows (top row, grid row, plain row) copied down instead of one call per pixel.
void render_animated_pattern(std::uint32_t* pixels, std::uint32_t width, std::uint32_t height, std::size_t stride_px,
                             std::uint64_t phase);

class Presentation {
public:
    Presentation();
    ~Presentation();
    Presentation(const Presentation&) = delete;
    Presentation& operator=(const Presentation&) = delete;
    // `capture` is an already-validated XR-1..XR-3 allowlist (parse_capture_list); empty = no capture.
    Status open(std::string output_name, std::vector<std::string> capture = {});
    Status animate_pattern(std::uint32_t duration_ms);
    bool finished() const;
    Status draw_test_pattern();
    // Bounded dispatch; suitable for an independently supervised heartbeat loop.
    // Wait up to `timeout_ms` for Wayland events, or until `wake_fd` (the supervisor's control socket, if given) is
    // readable, so a control frame such as a pointer move is handled at once rather than after the timeout.
    // `wake_ready` (optional) is set false only when the wait saw `wake_fd` idle, so the caller can skip reading it.
    Status dispatch(int timeout_ms = 50, int wake_fd = -1, bool* wake_ready = nullptr);
    void close();
    std::string timing_report() const;
    std::string status_json(double now_ms) const;
    std::string capture_report() const;  // bounded JSON; never contains pixel data
    // Animate the glasses view (overview / zoom into `selected`, gap `spacing`); capture mode only.
    // Shows a short pop-up for the viewing mode (first call, and on change), cursor follow, prediction and typing mode
    // (on change). `center`: a cursor switch; in anchor mode it lands with the target centred in view. `cut`: a cursor
    // switch with its animation off (anchor mode): shown at once, the view not turned (ViewAnimator).
    void set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode = 0, bool cursor_follow = false,
                  bool prediction = false, bool typing = false, bool center = false, bool cut = false);
    // World-anchored 3DoF from the pose feed at `prefix` (GPU composition only); recenter on request.
    void enable_tracking(std::string prefix);
    void recenter();
    // Per-desktop placement in capture order, each {x, y, distance, curvature} in the validated wire units of ipc.hpp
    // WirePlacement (every desktop always faces you); entries beyond the captured outputs are ignored. GPU composition only.
    void set_placements(const std::vector<std::array<std::int32_t, 4>>& placements);
    void set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b);  // theme border colour
    // VR video (docs/vr-video.md; capture runs with GPU composition only). While open the glasses show the head-tracked
    // video (world-locked to the recentre reference whatever the viewing mode) and its controls card instead of the
    // desktops. `fd` is the file the supervisor opened; ownership is taken, also when video is unavailable.
    void video_open(int fd, const VideoFormat& format, const std::string& title);
    void video_close();
    void video_pause();
    void video_seek(std::int32_t seconds);
    void video_format(const VideoFormat& format);
    void video_controls(bool visible);
    // Keyboard help card (Ctrl+Alt+H): fades in/out in the middle of the view; rows are (keys, action), `sections` their
    // section sizes (empty: one section). `turn` -1 / +1 flips a shown card to the previous / next page (hud.hpp).
    void set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows,
                  const std::vector<std::uint32_t>& sections = {}, std::int32_t turn = 0);
    // Placement panel (display only; placements are applied by set_placements): with `open`, desktop `desktop`'s glass
    // pill expands into its placement panel (glass.hpp) and any other collapses; closed, every pill is a pill again.
    // `desktop` 0..count-1, `field` 0..3 in WirePlacement order; `placements` for every desktop in set_placements' units,
    // plus the panel's desktop row (hud.hpp PlacementMenuState).
    // `surface` (open only): while the supervisor holds one of the panel's sliders, the placement the panel is drawn on
    // (its desktop's at the press; the desktop itself follows set_placements). Empty: the desktop's live placement.
    void set_placement_menu(const PlacementMenuState& state, const std::optional<std::array<std::int32_t, 4>>& surface = std::nullopt);
    // The run's managed desktops changed (ipc.hpp DesktopsPayload; capture runs only): captures of names not in `outputs`
    // stop, new names start once Hyprland advertises them, `renew` restarts those captures. Captures stay in name order,
    // which is the order of every per-desktop list (placements, the overview's tiles).
    void set_desktops(const std::vector<std::string>& outputs, const std::vector<std::string>& renew = {});
    // Overview corner-resize handle (display only; the supervisor owns hover/drag and applies sizes with set_placements):
    // a dim bracket in corner `corner` (0 tl, 1 tr, 2 bl, 3 br) of desktop `desktop`, brighter while `active` (dragging).
    // Drawn only in the overview (hidden while zoomed or zooming), never orbiting or drifting. GPU composition only.
    void set_resize_handle(bool visible, std::uint32_t desktop, std::uint32_t corner, bool active);
    // Overview glass pills (display only; the supervisor owns hover/drag/click and moves desktops with set_placements): a
    // frosted capsule under every desktop, brighter over `desktop` when `hover`, brightest when `active` (dragging).
    // Overview only, never moving on its own. GPU composition only.
    void set_move_handle(bool visible, std::uint32_t desktop, bool hover, bool active);
    // Roll-level icons and levelling roll (ipc.hpp RollPayload; the supervisor owns both): `roll` each desktop's turn about
    // its line of sight, hundredths of a degree counter-clockwise (view.hpp Placement::roll, eased like a placement, every
    // zoom); `icons` per desktop -1 none, 0 above the top-left corner (counter-clockwise), 1 above the top-right
    // (clockwise), drawn with the pills (overview only); `hover` the desktop whose icon is brighter, brightest when `active`.
    void set_roll(const std::array<std::int32_t, kMaxDesktops>& roll, const std::array<std::int8_t, kMaxDesktops>& icons, std::int32_t hover, bool active);
    // How far each desktop is from level as you see it, without its levelling roll (view.hpp desktop_tilt against the
    // world's up from the pose feed): {"roll_geometry":{"tilt":[t,...]}}, one entry per captured desktop in hundredths of a
    // degree (counter-clockwise positive), or null where it cannot be measured (no head tracking, not in anchor mode or
    // still blending into it, a VR video, a desktop seen from straight below or above). When it changed, at most every
    // kRollGeometryMs (a null line at once). GPU composition only.
    std::optional<std::string> take_roll_geometry(double now_ms);
    void resend_roll_geometry();  // the last take was not delivered
    static constexpr double kRollGeometryMs = 100;
    // Overview free pointer (display only; the supervisor owns it): a dim dot at canvas pixel (x, y) on the overview's
    // plane, world-locked, a little brighter while `held` (a pill, corner or panel-slider press/drag is in progress).
    // Overview only (hidden while zoomed or zooming), never moving on its own. GPU composition only. `directed`: drawn in
    // direction (az, el) (1/100 degree, ipc.hpp OverviewPointerPayload) instead, anywhere round you (view.hpp direction_disc).
    // ghost_w / ghost_h > 0: a window is being dragged (ipc.hpp OverviewPointerPayload drag): its ghost outline round the dot
    // and, target >= 0, the desktop it would land on framed in the accent.
    void set_overview_pointer(bool visible, std::int32_t x, std::int32_t y, bool held, bool directed = false, std::int32_t az = 0,
                              std::int32_t el = 0, std::int32_t ghost_w = 0, std::int32_t ghost_h = 0, std::int32_t target = -1);
    // The overview's canvas and settled desktop slots (view.hpp overview_geometry_json) when they changed since the last
    // call; the supervisor hit-tests the free pointer with them. GPU composition only.
    std::optional<std::string> take_overview_geometry();
    void resend_overview_geometry();  // the last take was not delivered
    // Where the help card and its page controls lie on the overview's canvas (hud.hpp help_geometry_json), or
    // {"help_geometry":null} when the card is not up in the overview, when that changed; a moving head re-sends it at
    // most every kHelpGeometryMs. The supervisor hit-tests the free pointer with it. GPU composition only.
    std::optional<std::string> take_help_geometry(double now_ms);
    void resend_help_geometry();  // the last take was not delivered
    static constexpr double kHelpGeometryMs = 40;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Metrics final_metrics_;
    std::string final_capture_ = "{\"kind\":\"managed_output_capture\",\"outputs\":[]}";
};
}
