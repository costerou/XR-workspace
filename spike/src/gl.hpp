#pragma once
// GPU composition of captured managed outputs onto the presentation surface (milestone C2a).
// Frame pacing stays with the caller's frame callbacks and presentation feedback; this class
// only renders and swaps. No readback: pixels never return to the CPU.
#include "xr/capture.hpp"
#include "xr/environment.hpp"
#include "xr/view.hpp"
#include "xr/pose.hpp"
#include "xr/hud.hpp"
#include "xr/glass.hpp"
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

struct wl_display;
struct wl_surface;

namespace xr {
struct DmabufBuffer;
class GlCompositor {
public:
    // Returns nullptr (with `why`) if EGL/GLES are unavailable; the caller reports the fallback.
    static std::unique_ptr<GlCompositor> create(wl_display*, wl_surface*, std::uint32_t width, std::uint32_t height,
                                                std::size_t sources, std::string& why);
    // The same compositor without a window (tests and benchmarks): a surfaceless EGL context drawing into its own
    // width x height RGBA8 framebuffer, left bound and current; "swap" is a glFlush. nullptr (with `why`) without one.
    static std::unique_ptr<GlCompositor> create_offscreen(std::uint32_t width, std::uint32_t height, std::size_t sources, std::string& why);
    ~GlCompositor();
    // `width` x `height`: the canvas (logical surface) every placement, HUD card and tile is laid out in. The frame is
    // drawn into a px_width x px_height buffer (the output's native pixels under a fractional scale; 0: the canvas size),
    // so the layout never changes with the scale, only how many pixels it is drawn with.
    void resize(std::uint32_t width, std::uint32_t height, std::uint32_t px_width = 0, std::uint32_t px_height = 0);
    // Upload one source frame (32-bit wl_shm XRGB/ARGB, little-endian B,G,R,X bytes).
    bool upload(std::size_t source, const void* pixels, std::uint32_t width, std::uint32_t height, std::uint32_t stride);
    // Draw each uploaded source into its (possibly off-screen) rect, placed in space by `placements[i]`
    // (view.hpp; missing entries are flat), back to front, with an optional highlight frame behind
    // `highlight` with `highlight_alpha`, then eglSwapBuffers (which attaches and commits).
    // `view` rotates the world-anchored canvas (identity = head-locked); half_w/h = tan(half FOV).
    bool draw_and_swap(const std::vector<FRect>& tiles, const std::vector<Placement>& placements, int highlight,
                       float highlight_alpha, const Mat3& view, float half_w, float half_h);
    // Zero-copy (C2b): can EGL import this fourcc/modifier; import a capture slot's buffer once;
    // mark slot as the source's current frame (sampled without B/R swap); drop on reallocation.
    bool dmabuf_importable(std::uint32_t fourcc, std::uint64_t modifier) const;
    bool import_dmabuf(std::size_t source, std::size_t slot, const DmabufBuffer&);
    void present_dmabuf(std::size_t source, int slot);
    void drop_dmabuf(std::size_t source);
    // Capture buffers being re-created: keep the source's current imported frame (its texture and EGL image; the caller
    // keeps the DmabufBuffer alive) and drop the other slots. It is drawn until present_dmabuf or upload gives the source
    // a new frame, which releases it. True if a frame is held. drop_dmabuf never drops the held frame.
    bool hold_dmabuf(std::size_t source);
    void release_held(std::size_t source);
    bool holding(std::size_t source) const;
    // A desktop added or removed during the run (Presentation::set_desktops): a new, empty source at `at` (later sources
    // move up one), or source `at` dropped with its texture and imported images (later ones move down). Context current.
    void insert_source(std::size_t at);
    void remove_source(std::size_t at);
    std::size_t sources() const;
    // Status pop-up drawn head-locked over the next frames (nullptr or alpha 0: none). `image`
    // must outlive the calls; it is re-uploaded only when `generation` changes.
    void set_popup(const HudImage* image, std::uint64_t generation, float alpha);
    // Keyboard help card, head-locked in the middle of the view, under the pop-ups; `drop_px`
    // moves it down (fade-in rise). Same lifetime/upload rules as set_popup.
    void set_help(const HudImage* image, std::uint64_t generation, float alpha, float drop_px);
    // Key bind panel (binds_panel.hpp), head-locked in the middle of the view over the help card; same rules.
    void set_binds(const HudImage* image, std::uint64_t generation, float alpha, float drop_px);
    // Glass pills (glass.hpp): a Material Design 3 tonal capsule under each desktop, on its own surface, and the open one morphing into its
    // placement panel with the panel's content (frame.image, re-uploaded when frame.generation changes) fading in. With
    // them the level bars and corner caps (GlassFrame::bars), under the desktops. Drawn blended over the desktops, under
    // the free pointer. Nothing to draw: a default frame.
    void set_glass(const GlassFrame& frame);
    // Overview free pointer (view.hpp canvas_disc): a grey dot of `level` (capped at kFreePointerHeldLevel) in a black ring at
    // canvas (x, y) on the overview's plane, drawn over the desktops and under the cards; visible false: none.
    // `directed`: the dot is drawn in direction (az, el) degrees instead (view.hpp direction_disc), anywhere round you.
    // `look`: marks round the dot (view.hpp direction_marks) in its grey, each with a black outline as the disc has its ring.
    void set_free_pointer(bool visible, float x, float y, float level, bool directed = false, float az = 0, float el = 0,
                          PointerLook look = PointerLook::plain);
    // Window drag (free_pointer.py): a grey w x h canvas px outline (view.hpp direction_outline) round the free pointer,
    // turned with it, and an accent frame (view.hpp drop_frame) on desktop `target` (< 0: none). w or h <= 0: no ghost.
    // Drawn only while the free pointer is.
    void set_window_drag(float w, float h, int target);
    void set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b);  // highlight border + glow, and the M3 scheme's seed (theme)
    // The background (xr/environment.hpp): `background` drawn first, behind everything, in place of the black clear,
    // world-locked by `world` (environment_view; world -> eye, row-major), at fade `level` (<= 0 or Background::none: not
    // drawn at all, the frame is exactly as without it) and `brightness` percent, animated by `seconds`. Its colour is the
    // highlight border's accent (set_accent), so a theme switch recolours both in one frame. False if this GL cannot draw it
    // (its shaders failed): nothing is drawn then. draw_and_swap draws it by itself; a VR video scene calls draw_environment.
    bool set_environment(float level, Background background, float brightness, float seconds, const Mat3& world);
    // That background over the current viewport, seen through tan(half FOV) half_w / half_h. False if nothing was drawn.
    bool draw_environment(float half_w, float half_h);
    // VR video mode (video_mode.hpp): `scene` draws the whole frame instead of the desktops; then the video controls card
    // (`card`, head-locked near the bottom, never moved; nullptr or alpha 0: none) and any status pop-up, then swap.
    // The help card and the glass pills are not drawn: the supervisor closes them before a video opens.
    bool draw_scene_and_swap(const std::function<void(std::uint32_t width, std::uint32_t height)>& scene, const HudImage* card,
                             std::uint64_t card_generation, float card_alpha);
    std::string report() const;  // bounded JSON: renderer strings, timing; no pixel data
private:
    GlCompositor() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
