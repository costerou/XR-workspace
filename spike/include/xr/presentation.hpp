#pragma once
#include "xr/types.hpp"
#include "xr/metrics.hpp"
#include "xr/video.hpp"
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
    Status dispatch(int timeout_ms = 50, int wake_fd = -1);
    void close();
    std::string timing_report() const;
    std::string status_json(double now_ms) const;
    std::string capture_report() const;  // bounded JSON; never contains pixel data
    // Animate the glasses view (overview / zoom into `selected`, gap `spacing`); capture mode only.
    // Shows a short pop-up for the viewing mode (first call, and on change) and cursor follow (on change).
    void set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode = 0, bool cursor_follow = false,
                  bool prediction = false);
    // World-anchored 3DoF from the pose feed at `prefix` (GPU composition only); recenter on request.
    void enable_tracking(std::string prefix);
    void recenter();
    // Per-desktop placement in capture order, each {x, y, distance, yaw, pitch, roll, curvature} in the validated wire
    // units of ipc.hpp WirePlacement; entries beyond the captured outputs are ignored. GPU composition only.
    void set_placements(const std::vector<std::array<std::int32_t, 7>>& placements);
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
    // Keyboard help card (Ctrl+Alt+H): fades in/out in the middle of the view; rows are (keys, action).
    void set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows);
    // Keyboard placement menu (display only; placements are applied by set_placements): fades in/out in the
    // middle of the view like the help card, which it replaces while shown. `desktop` 0..2, `field` 0..6 in
    // WirePlacement order; `placements` for all three desktops in the same units as set_placements.
    void set_placement_menu(bool open, std::uint32_t desktop, std::uint32_t field,
                            const std::array<std::array<std::int32_t, 7>, 3>& placements);
    // Mouse-mode pointer over the menu, in card pixels from the card's top-left (hud.hpp MenuGeometry). It is its own
    // small overlay, redrawn the next frame; the card is not re-rendered.
    void set_menu_pointer(bool visible, std::int32_t x, std::int32_t y, bool held);
    // The menu card's geometry as one status line (hud.hpp menu_geometry_json), when it changed since the last call
    // and the menu is open; the supervisor hit-tests mouse-mode clicks with it.
    std::optional<std::string> take_menu_geometry();
    void resend_menu_geometry();  // the last take was not delivered
    // Overview corner-resize handle (display only; the supervisor owns hover/drag and applies sizes with set_placements):
    // a dim bracket in corner `corner` (0 tl, 1 tr, 2 bl, 3 br) of desktop `desktop`, brighter while `active` (dragging).
    // Drawn only in the overview (hidden while zoomed or zooming), never orbiting or drifting. GPU composition only.
    void set_resize_handle(bool visible, std::uint32_t desktop, std::uint32_t corner, bool active);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    Metrics final_metrics_;
    std::string final_capture_ = "{\"kind\":\"managed_output_capture\",\"outputs\":[]}";
};
}
