#include "xr/presentation.hpp"
#include <wayland-client.h>
#include "presentation-time-client-protocol.h"
#include "ext-image-capture-source-v1-client-protocol.h"
#include "ext-image-copy-capture-v1-client-protocol.h"
#include "linux-dmabuf-v1-client-protocol.h"
#include "fractional-scale-v1-client-protocol.h"
#include "viewporter-client-protocol.h"
#include "dmabuf.hpp"
#include "xr/capture.hpp"
#include "gl.hpp"
#include "xr/view.hpp"
#include "xr/pose.hpp"
#include "xr/predict.hpp"
#include "xr/hud.hpp"
#include "xr/environment.hpp"
#include "video_mode.hpp"
#include <cmath>
// Pinned upstream XML names this C parameter `namespace`. Keep the vendored XML
// byte-identical; scope the C++ spelling adaptation to the generated C header.
#define namespace layer_namespace
#include "wlr-layer-shell-unstable-v1-client-protocol.h"
#undef namespace
#include <algorithm>
#include <array>
#include <bit>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <poll.h>
#include <optional>
#include <regex>
#include <span>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

namespace xr {
namespace {
std::uint64_t monotonic_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
}
struct Presentation::Impl : SurfaceRequests {
    wl_display* display = nullptr;
    wl_registry* registry = nullptr;
    wl_compositor* compositor = nullptr;
    wl_shm* shm = nullptr;
    zwlr_layer_shell_v1* shell = nullptr;
    wl_surface* surface = nullptr;
    zwlr_layer_surface_v1* layer = nullptr;
    wl_callback* frame = nullptr;
    wp_presentation* presentation = nullptr;
    // Native resolution (optional): the glasses run at a fractional scale (1.25), so the surface's logical size (width x
    // height, the canvas everything is laid out in) is fewer pixels than the panel. With both globals the frame is drawn
    // at the preferred scale (px_w x px_h, scaled_surface_size) and the viewport maps it back onto the logical size, so
    // the compositor shows it 1:1 instead of upscaling it. Without them: drawn at the logical size, as before.
    wp_viewporter* viewporter = nullptr;
    wp_fractional_scale_manager_v1* fractional_manager = nullptr;
    wp_viewport* viewport = nullptr;
    wp_fractional_scale_v1* fractional = nullptr;
    std::uint32_t scale120 = 0, px_w = 0, px_h = 0;
    Metrics metrics;
    struct Feedback {
        Impl* owner = nullptr;
        struct wp_presentation_feedback* proxy = nullptr;
        std::uint64_t ticket = 0;
        bool selected_output = false, other_output = false;
    };
    std::array<Feedback, Metrics::capacity> feedbacks{};
    static void clock_id(void* data, wp_presentation*, uint32_t id) {
        static_cast<Impl*>(data)->metrics.clock(id);
        static_cast<Impl*>(data)->latency.clock(id);
    }
    inline static const wp_presentation_listener presentation_listener{clock_id};
    static void sync_output(void* data, struct wp_presentation_feedback*, wl_output* output) {
        auto& f = *static_cast<Feedback*>(data);
        if (f.owner->selected && output == f.owner->selected->proxy) f.selected_output = true;
        else f.other_output = true;
    }
    static void presented(void* data, struct wp_presentation_feedback* proxy, uint32_t hi, uint32_t lo,
                          uint32_t ns, uint32_t refresh, uint32_t seq_hi, uint32_t seq_lo, uint32_t flags) {
        auto& f = *static_cast<Feedback*>(data);
        f.owner->metrics.presented(f.ticket, hi, lo, ns, refresh, seq_hi, seq_lo, flags,
                                   f.selected_output && !f.other_output);
        f.owner->latency.presented(f.ticket, ((std::uint64_t(hi) << 32) | lo) * 1'000'000'000ull + ns);
        wp_presentation_feedback_destroy(proxy); f = {};
    }
    static void discarded(void* data, struct wp_presentation_feedback* proxy) {
        auto& f = *static_cast<Feedback*>(data);
        f.owner->metrics.discarded(f.ticket);
        f.owner->latency.discarded(f.ticket);
        wp_presentation_feedback_destroy(proxy); f = {};
    }
    inline static const wp_presentation_feedback_listener feedback_listener{sync_output, presented, discarded};
    void cancel_feedback() {
        for (auto& f : feedbacks) if (f.proxy) { wp_presentation_feedback_destroy(f.proxy); f = {}; }
        metrics.abandon();
        latency.abandon();
    }
    struct Output { wl_output* proxy = nullptr; std::uint32_t global = 0; std::string name; };
    std::vector<std::unique_ptr<Output>> outputs;
    Output* selected = nullptr;
    struct Buffer {
        wl_buffer* proxy = nullptr;
        void* memory = MAP_FAILED;
        std::size_t bytes = 0;
        std::uint32_t width = 0, height = 0, stride = 0;
        bool busy = false;
        // CPU composition: the tiles and each tile's source frame this buffer's pixels show (only we write them, and a
        // capture never writes the slot that is its ready frame), so redrawing the same content writes nothing.
        struct Content {
            bool valid = false;
            std::size_t n = 0;
            std::array<Rect, kMaxDesktops> tiles{};
            std::array<TileSource, kMaxDesktops> sources{};
            std::array<std::uint64_t, kMaxDesktops> frames{};
            bool operator==(const Content& o) const {
                if (valid != o.valid || n != o.n) return false;
                for (std::size_t i = 0; i < n; ++i) {
                    const auto &a = tiles[i], &b = o.tiles[i];
                    const auto &s = sources[i], &t = o.sources[i];
                    if (a.x != b.x || a.y != b.y || a.width != b.width || a.height != b.height || s.pixels != t.pixels ||
                        s.width != t.width || s.height != t.height || s.stride_px != t.stride_px || frames[i] != o.frames[i])
                        return false;
                }
                return true;
            }
        } content;
        ~Buffer() { if (proxy) wl_buffer_destroy(proxy); if (memory != MAP_FAILED) munmap(memory, bytes); }
    };
    std::vector<std::unique_ptr<Buffer>> buffers;
    std::uint32_t width = 0, height = 0;
    bool configured = false, closed = false;
    PatternCadence cadence;
    // The theme colour last set by the supervisor's `style` op (the default until then) and how many changed it: the
    // run's status shows a theme switch reached the renderer (the pills, the border and the glow all draw from it).
    std::array<std::uint8_t, 3> accent{0x8d, 0x8d, 0x8d}; std::uint64_t accent_updates = 0;
    std::vector<std::uint32_t> required_globals;
    // Managed-output capture (allowlisted names only; frames stay in these SHM buffers).
    bool capture_requested = false;
    ext_output_image_capture_source_manager_v1* source_manager = nullptr;
    ext_image_copy_capture_manager_v1* copy_manager = nullptr;
    zwp_linux_dmabuf_v1* linux_dmabuf = nullptr;
    std::unique_ptr<DmabufAllocator> allocator;  // render node of the compositor's capture device
    std::uint64_t allocator_device = 0;
    bool allocation_enabled = false;              // buffers are allocated once GL exists (import needs it)
    struct Capture;
    struct ParamsCtx { Capture* capture = nullptr; int slot = 0; };
    struct Capture {
        Impl* owner = nullptr;
        std::size_t index = 0;
        std::string name;
        Output* output = nullptr;
        ext_image_capture_source_v1* source = nullptr;
        ext_image_copy_capture_session_v1* session = nullptr;
        ext_image_copy_capture_frame_v1* frame = nullptr;
        SessionConstraints constraints;
        CaptureBuffers buffers;  // when to (re)create the capture buffers, and the size of the frame on screen
        ResizeSettle resize;     // ...not at once after its output was resized
        SizeEase shape;          // the size the layout draws it at (GPU composition): eases to a new shape
        std::array<std::unique_ptr<Buffer>, 2> slots{};
        int capturing = -1, ready = -1;
        bool stopped = false;
        // Desktops change during a run (set_desktops). `gone`: its output was removed (expected: the supervisor dropped the
        // capture first, or it is about to); it never re-attaches on its own. A session the compositor stops (an output
        // reshaped to ultrawide and back) restarts after kRestartMs, at most kMaxRestarts times; then the run ends as before.
        bool gone = false;
        std::uint32_t restarts = 0;
        std::uint64_t restart_ms = 0;
        std::uint64_t frames = 0, failures = 0, failure_run = 0, last_ready_ms = 0;
        // While buffers are re-created, the frame on screen stays there until the new buffers deliver one: GPU composition
        // keeps a dmabuf frame's GL image (GlCompositor::hold_dmabuf; its buffer lives here) or an SHM frame's texture;
        // the CPU composition keeps the SHM slot itself.
        std::unique_ptr<DmabufBuffer> held_dbuf;
        std::unique_ptr<Buffer> held_shm;
        // Zero-copy path (C2b): three GBM buffers imported into GL once; the compositor renders
        // into the slot that is neither the latest frame nor the one GL is sampling.
        enum class Path { None, Shm, Dmabuf } path = Path::None;
        std::optional<SessionConstraints::Batch> batch;
        bool no_dmabuf = false, dmabuf_failed = false;
        std::array<std::unique_ptr<DmabufBuffer>, 3> dbufs{};
        std::array<wl_buffer*, 3> dproxy{};
        std::array<zwp_linux_buffer_params_v1*, 3> params{};
        std::array<ParamsCtx, 3> params_ctx{};
        int pending = 0, presented = -1;
        DmabufChoice choice{};
        std::uint64_t dmabuf_frames = 0;
        std::string fallback;  // why SHM is used (fixed strings only)
        void drop_dmabuf() {
            for (auto& p : params) if (p) { zwp_linux_buffer_params_v1_destroy(p); p = nullptr; }
            for (auto& b : dproxy) if (b) { wl_buffer_destroy(b); b = nullptr; }
            for (auto& d : dbufs) d.reset();
            pending = 0; dmabuf_failed = false;
        }
        ~Capture() {
            drop_dmabuf();
            if (frame) ext_image_copy_capture_frame_v1_destroy(frame);
            if (session) ext_image_copy_capture_session_v1_destroy(session);
            if (source) ext_image_capture_source_v1_destroy(source);
        }
    };
    std::vector<std::unique_ptr<Capture>> captures;
    static constexpr std::uint64_t kRestartMs = 100;
    static constexpr std::uint32_t kMaxRestarts = 16;
    // Each capture's own size for layout; one without a frame size yet holds a 1920x1080 tile. At most kMaxDesktops
    // captures (open() and set_desktops() take only a parse_capture_list allowlist), so no allocation.
    struct Sizes {
        std::array<Size, kMaxDesktops> v{};
        std::size_t n = 0;
        std::span<const Size> span() const { return {v.data(), n}; }
    };
    static Size settled_size(const Capture& c) { return c.buffers.layout_size().value_or(Size{1920, 1080}); }
    Sizes settled_sizes() const {  // what the layout is heading for: the frame on screen's size (the supervisor's hit testing)
        Sizes out;
        for (const auto& c : captures) {
            if (out.n == out.v.size()) break;
            out.v[out.n++] = settled_size(*c);
        }
        return out;
    }
    Sizes capture_sizes() const {  // what is drawn: the same, but a desktop changing shape part way there (SizeEase)
        Sizes out;
        for (const auto& c : captures) {
            if (out.n == out.v.size()) break;
            out.v[out.n++] = c->shape.value().value_or(settled_size(*c));
        }
        return out;
    }
    void ease_sizes(double now_ms) {  // a capture's first frame only sets its size; after that a new shape eases
        for (const auto& c : captures) c->shape.advance(settled_size(*c), now_ms, c->frames > 0);
    }
    bool sizes_easing() const {
        return std::any_of(captures.begin(), captures.end(), [](const auto& c) { return c->shape.animating(); });
    }
    // Layouts are pure functions of the capture sizes, the surface size and a few view floats: remembered with those
    // inputs (bit for bit) and recomputed only when one changes, so a settled view lays nothing out per frame or wake.
    static constexpr std::size_t kKeySizes = 2 * kMaxDesktops;
    using LayoutKey = std::array<std::uint32_t, kKeySizes + 8>;
    LayoutKey layout_key(const Sizes& z, float a, float b, float c, float d = 0, std::uint32_t e = 0) const {
        LayoutKey k{};
        for (std::size_t i = 0; i < z.v.size(); ++i) {
            k[2 * i] = i < z.n ? std::bit_cast<std::uint32_t>(z.v[i].w) : 0u;
            k[2 * i + 1] = i < z.n ? std::bit_cast<std::uint32_t>(z.v[i].h) : 0u;
        }
        k[kKeySizes] = std::uint32_t(z.n); k[kKeySizes + 1] = width; k[kKeySizes + 2] = height;
        k[kKeySizes + 3] = std::bit_cast<std::uint32_t>(a); k[kKeySizes + 4] = std::bit_cast<std::uint32_t>(b);
        k[kKeySizes + 5] = std::bit_cast<std::uint32_t>(c); k[kKeySizes + 6] = std::bit_cast<std::uint32_t>(d); k[kKeySizes + 7] = e;
        return k;
    }
    std::optional<LayoutKey> draw_tiles_key, overview_key, cpu_tiles_key;
    std::vector<FRect> draw_tiles;       // blended_layout for the GL frame
    TileEase tile_ease;                  // the tiles on their way to it through a desktop change (Ctrl+Alt+R)
    std::string overview_line;           // overview_geometry_json of the settled overview
    std::vector<Rect> cpu_tiles;         // side_by_side for the CPU frame
    std::vector<Placement> resolved;     // resolved_placements() of the frame being drawn
    std::unique_ptr<GlCompositor> gl;     // GPU composition when available (C2a)
    std::unique_ptr<VideoMode> video;     // VR video (video_mode.hpp); created on the first open
    VideoQuality video_quality;           // the last `quality` frame, for a VideoMode created after it
    std::string gl_fallback;              // non-empty: why the CPU blit is in use
    std::vector<std::uint64_t> uploaded;  // per capture: frame counter last uploaded
    std::vector<std::uint64_t> drawn;     // per capture: `uploaded` as of the last draw (SHM: one upload ahead at most)
    std::vector<std::uint64_t> frames_at_draw;  // per capture: its frame counter at the last draw
    std::vector<std::uint32_t> early_hold;      // per capture: draws left with early uploads off (it outpaced the draws)
    static constexpr std::uint32_t kEarlyHoldDraws = 32;
    bool upload_failed = false;           // an early SHM upload failed; the next draw reports it
    ViewAnimator view{kViewEaseMs};
    // Per-desktop placement (ipc.hpp WirePlacement units), in capture order; absent entries are flat.
    std::vector<std::array<std::int32_t, 4>> placements;
    std::uint64_t placement_updates = 0;
    std::array<std::int32_t, kMaxDesktops> rolls{};  // levelling roll per desktop, hundredths of a degree (set_roll)
    Placement target_placement(std::size_t i) const {
        Placement p = i < placements.size() ? placement_from_wire(placements[i]) : Placement{};
        if (i < rolls.size()) p.roll = float(rolls[i]) / 100.f * float(M_PI) / 180.f;
        return p;
    }
    struct LevelBars { std::array<bool, kMaxDesktops> bars{}; std::int32_t hover = -1; std::int8_t side = -1; bool active = false; std::uint64_t updates = 0; } roll;
    std::string roll_geometry_sent;      // last roll_geometry line handed to the supervisor
    double roll_geometry_ms = -1e9;      // when it was sent
    bool roll_geometry_measured = false; // ...and whether it held measurements (then later changes are rate-limited)
    // Drawn placements ease toward the latest ones (view.hpp ease_toward), so a pill drag or corner resize, whose
    // updates arrive at the pointer's rate, moves smoothly at the display's rate. A frame with a glide (a discrete edit
    // that bumped desktops out of the way) instead takes every desktop from where it was drawn to its new placement along
    // one smootherstep curve over glide_ms (view.hpp glide_toward); any later frame without one eases on from there.
    std::vector<Placement> eased;
    double eased_ms = -1;
    PlacementGlide glide;           // a glide under way (glide.ms > 0), view.hpp step_placements
    std::vector<Placement> targets; // ease_placements' scratch
    void ease_placements(double now_ms) {
        const float dt = eased_ms < 0 ? 1e9f : float(now_ms - eased_ms);
        eased_ms = now_ms;
        targets.clear();
        for (std::size_t i = 0; i < captures.size(); ++i) targets.push_back(target_placement(i));
        if (eased.size() > targets.size()) eased.resize(targets.size());
        while (eased.size() < targets.size()) eased.push_back(Placement{});  // eased in from flat, as before
        step_placements(eased, targets, dt, now_ms, glide);
    }
    bool placements_easing() const {
        for (std::size_t i = 0; i < eased.size(); ++i) if (!same_placement(eased[i], target_placement(i))) return true;
        return false;
    }
    std::vector<Placement> own;           // resolved_placements' scratch: each desktop's eased placement
    const std::vector<Placement>& resolved_placements(std::span<const FRect> tiles) {
        own.clear();  // keeps its capacity
        for (std::size_t i = 0; i < captures.size(); ++i) own.push_back(i < eased.size() ? eased[i] : target_placement(i));
        // Every mode: zooming never moves a desktop round you. The zoomed one grows to fit the glasses where it was dragged;
        // zoomed in anchor mode, one resized bigger than that keeps its size instead (keep_size). The rest stay put in every
        // mode. Since maximize-follow (2026-11-03) a maximized desktop is follow or locked and fits; anchor is the overview,
        // so keep_size is never on while un-maximizing either: there it would make a desktop resized bigger than the view
        // jump from the fit to its overview size as the zoom out starts, instead of easing there with the zoom.
        const auto overview = overview_layout(capture_sizes().span(), float(width), float(height), view.spacing());
        resolved = zoomed_placements(Canvas{float(width), float(height), half_w, half_h}, overview, tiles, own, view.zoom(), view.position(),
                                     modes.mode() == ViewMode::anchor && maximized, view.left_zoom(), view.left());
        return resolved;
    }
    // Follow and locked modes centre the view on the focused desktop wherever it was dragged (view.hpp focus_direction /
    // focus_weight; pose.hpp ModeController::set_aim): the canvas turns, never a placement. 0 in the overview.
    // An edge pan (view.hpp EdgePan) aims by itself: one rotation from the desktop being left to the target, the hand driving it and
    // the landing finishing it, so the target lands in front of you in follow and on the view in locked and the plain aim
    // takes over with nothing moving.
    Quat focus_aim() const {
        const float w = focus_weight(view);
        if ((!(w > 0.f) && !view.pan_active()) || !half_w) return Quat{};
        const auto overview = overview_layout(capture_sizes().span(), float(width), float(height), view.spacing());
        std::array<Placement, kMaxDesktops> placed{};
        const std::size_t n = std::min(captures.size(), placed.size());
        for (std::size_t i = 0; i < n; ++i) placed[i] = i < eased.size() ? eased[i] : target_placement(i);
        const std::span<const Placement> ps(placed.data(), n);
        const Canvas c{float(width), float(height), half_w, half_h};
        if (view.pan_active()) {
            const auto from = focus_direction(c, overview, ps, view.pan().from), to = focus_direction(c, overview, ps, view.pan().to);
            return aim_along(aim_rotation(from[0], from[1]), aim_rotation(to[0], to[1]), view.pan().way, view.pan_turn(), view.pan().side);
        }
        const auto d = focus_direction(c, overview, ps, view.focus());
        return aim_rotation(w * d[0], w * d[1]);
    }
    // The dot of an edge pan, as a direction {azimuth, elevation} on the canvas: on the desktop's edge as it is drawn until
    // it leaves it, then on its way across. nullopt when there is none to draw (landing: the real cursor is on the target).
    std::optional<std::array<float, 2>> pan_dot(std::span<const FRect> tiles, std::span<const Placement> drawn) const {
        if (!view.pan_active() || view.pan_landing() || !half_w) return std::nullopt;
        const auto overview = overview_layout(capture_sizes().span(), float(width), float(height), view.spacing());
        std::array<Placement, kMaxDesktops> placed{};
        const std::size_t n = std::min(captures.size(), placed.size());
        for (std::size_t i = 0; i < n; ++i) placed[i] = i < eased.size() ? eased[i] : target_placement(i);
        const Canvas c{float(width), float(height), half_w, half_h};
        const auto path = edge_pan_path(c, capture_sizes().span(), view.spacing(), overview, std::span<const Placement>(placed.data(), n), view.pan());
        const auto& p = view.pan();
        if (!path || p.from >= tiles.size() || p.from >= drawn.size()) return std::nullopt;
        Vec3 start = path->from;
        if (view.zoom() > p.zoom) {
            const auto e = edge_point(tiles[p.from], p.side, p.exit);
            start = place_point(c, tiles[p.from], drawn[p.from], e[0], e[1]);
            const float len = std::sqrt(start.x * start.x + start.y * start.y + start.z * start.z);
            if (!(len > 0)) return std::nullopt;
            start = {start.x / len, start.y / len, start.z / len};
        }
        const Vec3 d = edge_pan_dot(*path, start, view.pan_cross());
        return std::array<float, 2>{std::atan2(d.x, -d.z), std::atan2(d.y, std::sqrt(d.x * d.x + d.z * d.z))};
    }
    // A landed desktop switch's turn (ViewAnimator::take_landed_turn) becomes the room's: anchor mode turns the pinned
    // canvas by exactly the camera turn the switch ended on, so the view stays on the target instead of easing back to the
    // desktop it left. Follow and locked need nothing (their aim already ends on the target). Call once per frame after
    // view.advance, before modes.update.
    // A centred switch (a cursor switch, ViewAnimator::set_target's `center`) turns from the direction at the centre of the
    // view when it began, so the target lands centred: that direction, per switch serial, from the last drawn view.
    Mat3 last_drawn{1, 0, 0, 0, 1, 0, 0, 0, 1};
    struct Centre { bool valid = false; std::uint32_t serial = 0; std::array<float, 2> dir{}; } centre;
    void capture_centre() {
        if (!view.turn_centred() || (centre.valid && centre.serial == view.serial())) return;
        centre = {true, view.serial(), view_centre(last_drawn)};  // the turn holds 0 until phase 2: nothing drawn turned yet
    }
    std::array<float, 2> centre_of(std::uint32_t serial) const {
        return centre.valid && centre.serial == serial ? centre.dir : view_centre(last_drawn);
    }
    void land_turns() {
        const auto landed = view.take_landed_turns();
        if (landed.empty() || modes.mode() != ViewMode::anchor || !half_w) return;
        const auto overview = overview_layout(capture_sizes().span(), float(width), float(height), view.spacing());
        std::array<Placement, kMaxDesktops> placed{};
        const std::size_t n = std::min(captures.size(), placed.size());
        for (std::size_t i = 0; i < n; ++i) placed[i] = i < eased.size() ? eased[i] : target_placement(i);
        const Canvas c{float(width), float(height), half_w, half_h};
        const std::span<const Placement> ps(placed.data(), n);
        for (const auto& l : landed) {  // in order: turns do not commute
            const auto a = l.centred ? centre_of(l.serial) : focus_direction(c, overview, ps, l.from);
            const auto b = focus_direction(c, overview, ps, l.to);
            modes.turn_anchor(quat_mul(quat_conj(aim_rotation(a[0], a[1])), aim_rotation(b[0], b[1])));
        }
    }
    // A desktop switch's view turn (ViewAnimator::turn, view.hpp look_turn) applied to this frame's head rotation: the
    // camera turns, never a placement. Call after resolved_placements (it reads `own`). Identity turn at rest. Anchor
    // mode only: follow and locked already turn to the target through the aim (focus_aim).
    Mat3 turned(const Mat3& head) const {
        if (!(view.turn() > 0.f) || modes.mode() != ViewMode::anchor) return head;
        const auto overview = overview_layout(capture_sizes().span(), float(width), float(height), view.spacing());
        const Canvas c{float(width), float(height), half_w, half_h};
        const Mat3 m = view.turn_centred() ? look_turn_from(c, overview, own, centre_of(view.serial()), view.look_to(), view.turn())
                                           : look_turn(c, overview, own, view.look_from(), view.look_to(), view.turn());
        Mat3 out{};
        for (int r = 0; r < 3; ++r)
            for (int c = 0; c < 3; ++c) out[r * 3 + c] = head[r * 3] * m[c] + head[r * 3 + 1] * m[3 + c] + head[r * 3 + 2] * m[6 + c];
        return out;
    }
    // Head tracking (XRLinuxDriver feed); absent/stale feed holds the last pose, never blocks.
    bool tracking_requested = false;
    std::string pose_prefix;
    std::unique_ptr<PoseFeed> pose_feed;
    std::string pose_why;
    std::uint64_t pose_retry_ms = 0;
    PoseState pose;
    ModeController modes;  // T2: anchor / locked (0DoF) / follow
    // The supervisor's last view: maximized (zoomed in, or zooming in). A maximize or un-maximize changes the viewing mode
    // with it (maximize-follow): the room is kept and restored (ModeController::Change), with no mode pop-up.
    bool maximized = false;
    // Software latency (pose read -> compositor presented, same CLOCK_MONOTONIC) and bounded
    // prediction (predict.hpp). Neither is motion-to-photon latency.
    LatencyTracker latency;
    PoseAge pose_age;
    bool prediction = false;
    std::uint64_t predicted_frames = 0, unpredicted_frames = 0;  // while enabled and tracking
    float last_horizon_ms = 0, last_predicted_deg = 0;
    static constexpr float kMaxPredictionDeg = 15.f;
    StatusPopups popups;   // viewing mode / cursor follow pop-ups (GPU composition only)
    TrackingHint tracking_hint;  // "Reconnecting tracking..." through a USB drop, in the pop-ups' place
    std::uint64_t tracking_hints = 0;  // times it came up
    bool popup_drawn = false;  // last frame showed a pop-up: draw once more so it clears
    HelpOverlay help;          // keyboard help card (Ctrl+Alt+H)
    bool help_drawn = false;   // last frame showed the help card: draw once more so it clears
    BindPanelOverlay binds;    // key bind panel (Ctrl+Alt+K)
    bool binds_drawn = false;  // last frame showed it: draw once more so it clears
    GlassPills glass;          // glass pills and the open placement panel (supervisor-owned state)
    bool glass_drawn = false;  // last frame showed an open or closing panel: draw once more so it clears
    PanelShift panel_shift;    // the view pitched down while a panel is open, so all of it shows (glass.hpp glass_panel_shift)
    // The background behind the desktops and a VR video (xr/environment.hpp): world-locked when drawn, in the theme colour.
    EnvironmentFade environment;
    Background environment_background = Background::none;  // the one drawn: None's fade-out keeps the last one to the end
    std::uint16_t environment_brightness = kBrightnessDefault;
    bool environment_drawn = false;      // last frame drew it: draw once more after it has faded out
    struct ResizeHandle { bool visible = false, active = false; std::uint32_t desktop = 0, corner = 0; std::uint64_t shown = 0; } handle;
    struct MoveHandle { bool visible = false, hover = false, active = false; std::uint32_t desktop = 0; std::uint64_t shown = 0; } pills;
    struct FreePointer {
        bool visible = false, held = false, directed = false; std::int32_t x = 0, y = 0, az = 0, el = 0; std::uint64_t shown = 0;
        std::int32_t ghost_w = 0, ghost_h = 0, target = -1;  // window drag (0: none)
        PointerLook look = PointerLook::plain;
    } free_pointer;
    std::string overview_geometry_sent;  // last overview_geometry line handed to the supervisor
    std::string help_geometry_sent;      // last help_geometry line handed to the supervisor
    double help_geometry_ms = -1e9;      // when it was sent
    Mat3 last_head{1, 0, 0, 0, 1, 0, 0, 0, 1};  // head rotation of the last desktop frame (help card hit testing)
    std::string popup_font = "none";  // reported: loaded family, or why the pixel font is used
    float half_w = 0, half_h = 0, fov_deg = 0;
    bool fov_from_driver = false;
    void update_fov() {
        // Fill the glasses' FOV at the centre pose; diagonal FOV from the driver when published.
        const auto published = pose_feed ? pose_feed->display_fov() : std::nullopt;
        fov_from_driver = published.has_value();
        const float diag = published.value_or(46.f);  // fallback is reported, never silent
        fov_deg = diag;
        const double d = std::tan(diag * M_PI / 360.0), a = height ? double(width) / height : 16.0 / 10.0;
        half_w = float(d * a / std::sqrt(1 + a * a)); half_h = float(d / std::sqrt(1 + a * a));
    }
    static void cap_buffer_size(void* data, ext_image_copy_capture_session_v1*, uint32_t w, uint32_t h) {
        static_cast<Capture*>(data)->constraints.size(w, h);
    }
    static void cap_shm_format(void* data, ext_image_copy_capture_session_v1*, uint32_t format) {
        static_cast<Capture*>(data)->constraints.format(format);
    }
    static void cap_dmabuf_device(void* data, ext_image_copy_capture_session_v1*, wl_array* device) {
        dev_t dev{};
        if (device && device->size == sizeof dev) { std::memcpy(&dev, device->data, sizeof dev); static_cast<Capture*>(data)->constraints.dmabuf_device(dev); }
    }
    static void cap_dmabuf_format(void* data, ext_image_copy_capture_session_v1*, uint32_t fourcc, wl_array* modifiers) {
        std::vector<std::uint64_t> mods;
        if (modifiers) for (std::size_t i = 0; i + sizeof(std::uint64_t) <= modifiers->size; i += sizeof(std::uint64_t)) {
            std::uint64_t m; std::memcpy(&m, static_cast<const char*>(modifiers->data) + i, sizeof m); mods.push_back(m);
        }
        static_cast<Capture*>(data)->constraints.dmabuf_format(fourcc, std::move(mods));
    }
    static void cap_done(void* data, ext_image_copy_capture_session_v1*) {
        auto& c = *static_cast<Capture*>(data);
        auto batch = c.constraints.done_all();
        if (!batch.shm) { c.stopped = true; c.owner->closed = true; return; }  // unsupported constraints: fail closed
        c.buffers.constraints(*batch.shm);
        c.batch = std::move(batch);  // the latest batch: what a re-creation (settle_captures) allocates for
        c.owner->capture_next(c);    // same buffers: keep capturing into them; no-op while a re-creation is pending
    }
    // After each dispatch: re-create the buffers of a capture whose constraints changed, once no frame is in flight
    // (CaptureBuffers::settle), so constraints that change and change back within one dispatch re-create nothing; after
    // a resize, only kResizeSettleMs later (ResizeSettle: the output's clients catch up behind the frame on screen).
    void settle_captures() {
        if (!allocation_enabled) return;
        const auto now = monotonic_ms();
        for (auto& p : captures) {
            auto& c = *p;
            if (c.stopped) continue;
            if (c.resize.hold(c.buffers, now)) continue;  // pump() comes back here at least every 50 ms
            if (c.buffers.settle(c.frame != nullptr)) allocate(c);
            else capture_next(c);
        }
    }
    // Keep what is on screen through a re-creation (see Capture::held_dbuf): the newest frame, drawn or not.
    void hold_frame(Capture& c) {
        if (c.path == Capture::Path::Dmabuf && gl) {
            const int show = c.ready >= 0 ? c.ready : c.presented;
            if (show >= 0 && c.dbufs[std::size_t(show)]) {
                if (show != c.presented) gl->present_dmabuf(c.index, show);
                if (gl->hold_dmabuf(c.index)) {
                    c.held_dbuf = std::move(c.dbufs[std::size_t(show)]);
                    if (c.index < uploaded.size()) uploaded[c.index] = c.frames;
                }
            }
        } else if (c.path == Capture::Path::Shm && c.ready >= 0 && c.slots[std::size_t(c.ready)]) {
            if (gl) {  // the texture keeps the frame; upload the newest one if it was not drawn yet
                const auto& slot = *c.slots[std::size_t(c.ready)];
                if (c.index < uploaded.size() && uploaded[c.index] != c.frames && gl->upload(c.index, slot.memory, slot.width, slot.height, slot.stride)) {
                    uploaded[c.index] = c.frames; c.held_dbuf.reset();
                }
            } else c.held_shm = std::move(c.slots[std::size_t(c.ready)]);
        }
    }
    // (Re)allocate capture targets: dmabuf when everything lines up, otherwise SHM with a reason.
    void allocate(Capture& c) {
        hold_frame(c);
        c.ready = c.presented = -1; c.path = Capture::Path::None;
        if (gl) gl->drop_dmabuf(c.index);  // GL lets go of the images before their buffers die (a held frame stays)
        c.drop_dmabuf();
        for (auto& slot : c.slots) slot.reset();
        if (try_dmabuf(c)) return;  // capture starts once the compositor has created all buffers
        c.drop_dmabuf();
        if (gl) gl->drop_dmabuf(c.index);
        const auto& plan = *c.buffers.allocated();
        for (auto& slot : c.slots) { slot = make_shm(plan.width, plan.height, plan.stride, plan.format, "xr-managed-capture");
                                     if (!slot) { c.stopped = true; closed = true; return; } }
        c.path = Capture::Path::Shm;
        capture_next(c);
    }
    bool try_dmabuf(Capture& c) {
        auto fail = [&](const char* why) { c.fallback = why; return false; };
        if (c.no_dmabuf) return false;  // fallback reason already recorded
        if (!gl) return fail("no gpu composition");
        if (!linux_dmabuf) return fail("compositor lacks zwp_linux_dmabuf_v1");
        if (!c.batch || !c.batch->device || c.batch->dmabuf.empty()) return fail("session offers no dmabuf");
        if (!allocator || allocator_device != *c.batch->device) {
            for (auto& other : captures) {  // held frames were allocated on the old device
                if (gl) gl->release_held(other->index);
                other->held_dbuf.reset();
            }
            std::string why; allocator.reset();
            allocator = DmabufAllocator::open(*c.batch->device, why);
            if (!allocator) { c.no_dmabuf = true; c.fallback = why; return false; }
            allocator_device = *c.batch->device;
        }
        auto choice = choose_dmabuf(c.batch->dmabuf, [&](std::uint32_t f, std::uint64_t m) { return gl->dmabuf_importable(f, m); });
        if (!choice) { c.no_dmabuf = true; return fail("no offered dmabuf format is importable"); }
        c.choice = *choice;
        const auto w = c.buffers.allocated()->width, h = c.buffers.allocated()->height;
        for (int i = 0; i < 3; ++i) {
            std::string why;
            c.dbufs[i] = allocator->allocate(w, h, *choice, why);
            if (!c.dbufs[i]) { c.no_dmabuf = true; c.fallback = why; return false; }
            if (!gl->import_dmabuf(c.index, std::size_t(i), *c.dbufs[i])) { c.no_dmabuf = true; return fail("egl dmabuf import failed"); }
        }
        for (int i = 0; i < 3; ++i) {
            const auto& b = *c.dbufs[i];
            auto* p = zwp_linux_dmabuf_v1_create_params(linux_dmabuf);
            for (int plane = 0; plane < b.planes; ++plane)
                zwp_linux_buffer_params_v1_add(p, b.fds[plane], std::uint32_t(plane), b.offsets[plane], b.strides[plane],
                                               std::uint32_t(b.modifier >> 32), std::uint32_t(b.modifier & 0xffffffffu));
            c.params[i] = p; c.params_ctx[i] = {&c, i};
            zwp_linux_buffer_params_v1_add_listener(p, &params_listener, &c.params_ctx[i]);
            zwp_linux_buffer_params_v1_create(p, int(w), int(h), b.fourcc, 0);
            ++c.pending;
        }
        c.path = Capture::Path::Dmabuf; c.fallback.clear();
        return true;
    }
    void params_settled(Capture& c) {
        if (--c.pending > 0) return;
        if (c.dmabuf_failed) { c.no_dmabuf = true; c.fallback = "compositor rejected dmabuf buffer"; allocate(c); return; }
        capture_next(c);
    }
    static void params_created(void* data, zwp_linux_buffer_params_v1* p, wl_buffer* buffer) {
        auto& ctx = *static_cast<ParamsCtx*>(data); auto& c = *ctx.capture;
        zwp_linux_buffer_params_v1_destroy(p); c.params[ctx.slot] = nullptr;
        c.dproxy[ctx.slot] = buffer;
        c.owner->params_settled(c);
    }
    static void params_failed(void* data, zwp_linux_buffer_params_v1* p) {
        auto& ctx = *static_cast<ParamsCtx*>(data); auto& c = *ctx.capture;
        zwp_linux_buffer_params_v1_destroy(p); c.params[ctx.slot] = nullptr;
        c.dmabuf_failed = true;
        c.owner->params_settled(c);
    }
    inline static const zwp_linux_buffer_params_v1_listener params_listener{params_created, params_failed};
    static void cap_stopped(void* data, ext_image_copy_capture_session_v1*) { auto& c = *static_cast<Capture*>(data); c.owner->capture_stopped(c); }
    // The compositor ended a capture session. Expected when its output went (`gone`); after a reshape it is restarted by
    // service_captures (never from inside the session's own event), a bounded number of times; otherwise the run ends.
    void capture_stopped(Capture& c) {
        c.stopped = true;
        if (c.gone) return;
        if (!capture_requested || !allocation_enabled || ++c.restarts > kMaxRestarts) { closed = true; return; }
        c.restart_ms = monotonic_ms() + kRestartMs;
    }
    Output* find_output(const std::string& name) {
        Output* found = nullptr;
        for (auto& o : outputs) if (o->name == name && o.get() != selected) {
            if (found) return nullptr;  // ambiguous: never pick one
            found = o.get();
        }
        return found;
    }
    void start_session(Capture& c) {
        c.source = ext_output_image_capture_source_manager_v1_create_source(source_manager, c.output->proxy);
        c.session = ext_image_copy_capture_manager_v1_create_session(copy_manager, c.source,
                    EXT_IMAGE_COPY_CAPTURE_MANAGER_V1_OPTIONS_PAINT_CURSORS);  // pointer visible on the managed desktop
        ext_image_copy_capture_session_v1_add_listener(c.session, &session_listener, &c);
        c.stopped = false;
    }
    // Ends a capture's session and its buffers. The frame on screen is held (hold_frame) until a new one lands, and the
    // layout keeps that frame's size meanwhile (CaptureBuffers::restart).
    void stop_session(Capture& c) {
        hold_frame(c);
        if (gl) gl->drop_dmabuf(c.index);  // GL lets go of the images before their buffers die (a held frame stays)
        c.drop_dmabuf();
        if (c.frame) { ext_image_copy_capture_frame_v1_destroy(c.frame); c.frame = nullptr; }
        if (c.session) { ext_image_copy_capture_session_v1_destroy(c.session); c.session = nullptr; }
        if (c.source) { ext_image_capture_source_v1_destroy(c.source); c.source = nullptr; }
        for (auto& slot : c.slots) slot.reset();
        c.capturing = c.ready = c.presented = -1;
        c.path = Capture::Path::None; c.batch.reset(); c.buffers.restart();  // the new session allocates afresh
        (void)c.constraints.done_all();
    }
    // Every loop pass: restart stopped sessions that are due and start captures whose output Hyprland now advertises.
    void service_captures() {
        if (!capture_requested || !allocation_enabled) return;
        const auto now = monotonic_ms();
        for (auto& ptr : captures) {
            auto& c = *ptr;
            if (c.gone) continue;
            if (c.stopped && c.restart_ms && now >= c.restart_ms) {
                stop_session(c);
                c.restart_ms = 0;
                c.output = find_output(c.name);
                if (c.output) start_session(c);
                else c.stopped = false;  // its output is not advertised (yet): attached below once it is
                cadence.invalidate();
            } else if (!c.session && !c.stopped) {
                if ((c.output = find_output(c.name))) { start_session(c); cadence.invalidate(); }
            }
        }
    }
    // The run's managed desktops changed (ipc.hpp DesktopsPayload).
    void set_desktops(const std::vector<std::string>& wanted, const std::vector<std::string>& renew) {
        if (!capture_requested || !allocation_enabled || !source_manager || !copy_manager) return;
        std::string joined;
        for (const auto& name : wanted) joined += (joined.empty() ? "" : ",") + name;
        const auto names = parse_capture_list(joined, selected ? selected->name : std::string());
        if (!names) return;
        std::vector<std::string> was;  // the tiles of an eased change follow their desktops by name
        for (const auto& c : captures) was.push_back(c->name);
        auto erase_at = [](auto& v, std::size_t i) { if (i < v.size()) v.erase(v.begin() + std::ptrdiff_t(i)); };
        for (std::size_t i = captures.size(); i-- > 0;) {  // back to front: indices below stay valid
            if (std::find(names->begin(), names->end(), captures[i]->name) != names->end()) continue;
            stop_session(*captures[i]);
            if (gl) gl->remove_source(i);
            captures.erase(captures.begin() + std::ptrdiff_t(i));
            erase_at(uploaded, i); erase_at(drawn, i); erase_at(frames_at_draw, i); erase_at(early_hold, i);
            erase_at(placements, i); erase_at(eased, i);
            for (std::size_t k = i; k < captures.size(); ++k) captures[k]->index = k;
        }
        for (std::size_t i = 0; i < names->size(); ++i) {  // both sorted: what is missing goes in at its place
            const auto& name = (*names)[i];
            if (i < captures.size() && captures[i]->name == name) continue;
            auto c = std::make_unique<Capture>(); c->owner = this; c->name = name;
            captures.insert(captures.begin() + std::ptrdiff_t(i), std::move(c));
            if (gl) gl->insert_source(i);
            for (auto* v : {&uploaded, &drawn, &frames_at_draw}) v->insert(v->begin() + std::ptrdiff_t(std::min(i, v->size())), 0);
            early_hold.insert(early_hold.begin() + std::ptrdiff_t(std::min(i, early_hold.size())), 0);
            if (i < placements.size()) placements.insert(placements.begin() + std::ptrdiff_t(i), std::array<std::int32_t, 4>{0, 0, 100, 0});
            if (i < eased.size()) eased.insert(eased.begin() + std::ptrdiff_t(i), Placement{});
            for (std::size_t k = i; k < captures.size(); ++k) captures[k]->index = k;
        }
        for (const auto& name : renew)
            for (auto& c : captures) if (c->name == name) {
                if (c->session) stop_session(*c);
                c->output = nullptr; c->gone = false; c->stopped = false; c->restart_ms = 0; c->restarts = 0;
            }
        if (tile_ease.armed(double(monotonic_ms()))) {
            std::array<int, kMaxDesktops> before;
            before.fill(-1);
            const std::size_t n = std::min(captures.size(), before.size());
            for (std::size_t i = 0; i < n; ++i)
                if (const auto at = std::find(was.begin(), was.end(), captures[i]->name); at != was.end()) before[i] = int(at - was.begin());
            tile_ease.reindex(std::span<const int>(before.data(), n));
        }
        draw_tiles_key.reset(); overview_key.reset(); cpu_tiles_key.reset();
        service_captures();
        cadence.invalidate();
    }
    inline static const ext_image_copy_capture_session_v1_listener session_listener{
        cap_buffer_size, cap_shm_format, cap_dmabuf_device, cap_dmabuf_format, cap_done, cap_stopped};
    static void frame_transform(void*, ext_image_copy_capture_frame_v1*, uint32_t) {}
    static void frame_damage(void*, ext_image_copy_capture_frame_v1*, int32_t, int32_t, int32_t, int32_t) {}
    static void frame_time(void*, ext_image_copy_capture_frame_v1*, uint32_t, uint32_t, uint32_t) {}
    static void frame_ready(void* data, ext_image_copy_capture_frame_v1* frame) {
        auto& c = *static_cast<Capture*>(data);
        ext_image_copy_capture_frame_v1_destroy(frame); c.frame = nullptr;
        c.ready = c.capturing; c.capturing = -1; ++c.frames; c.failure_run = 0; c.last_ready_ms = monotonic_ms();
        c.buffers.delivered();
        if (c.path == Capture::Path::Dmabuf) ++c.dmabuf_frames;
        if (!c.owner->gl) c.held_shm.reset();  // CPU composition: this frame replaces a held one
        c.owner->cadence.invalidate();
        c.owner->capture_next(c);  // never targets the ready slot, which upload_early reads; no-op if re-creation is pending
        c.owner->upload_early(c);
    }
    static void frame_failed(void* data, ext_image_copy_capture_frame_v1* frame, uint32_t reason) {
        auto& c = *static_cast<Capture*>(data);
        ext_image_copy_capture_frame_v1_destroy(frame); c.frame = nullptr; c.capturing = -1; ++c.failures; ++c.failure_run;
        if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_STOPPED) { c.owner->capture_stopped(c); return; }
        if (c.failure_run > 1000) { c.stopped = true; c.owner->closed = true; return; }  // consecutive: no frame in between
        if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS) {
            if (c.buffers.reallocation_pending()) return;  // new constraints already arrived: settle_captures re-creates
            if (c.path == Capture::Path::Dmabuf && !c.dmabuf_frames) {   // refused before any frame: use SHM
                c.no_dmabuf = true; c.fallback = "compositor refused dmabuf capture"; c.owner->allocate(c); return;
            }
            c.buffers.constraints_failed(); return;  // wait for done, keeping the buffers and the frame on screen
        }
        c.owner->capture_next(c);  // no-op while a re-creation is pending
    }
    inline static const ext_image_copy_capture_frame_v1_listener capture_frame_listener{
        frame_transform, frame_damage, frame_time, frame_ready, frame_failed};
    // SHM capture with GPU composition: upload a new frame when it lands, while waiting for the frame callback, instead of
    // between the callback and the commit. At most one upload ahead of the draws per source: a frame that lands before
    // that one is drawn waits for the draw-time upload as before, so the draw still shows the newest frame. A source that
    // delivered two or more frames between two draws (capture faster than the display, or a starved renderer) would
    // mostly upload frames that are never drawn, so it goes back to draw-time uploads only for kEarlyHoldDraws draws.
    // (Only difference in what is shown: a frame uploaded early and then dropped by a capture reallocation stays on the
    // texture until the next frame, where the draw-time-only path kept the frame before it.)
    void upload_early(Capture& c) {
        if (c.path != Capture::Path::Shm || !gl || c.ready < 0 || c.index >= uploaded.size() || c.index >= drawn.size() ||
            c.index >= early_hold.size() || early_hold[c.index] || uploaded[c.index] != drawn[c.index] || !c.slots[std::size_t(c.ready)])
            return;
        const auto& slot = *c.slots[std::size_t(c.ready)];
        (void)wl_display_flush(display);  // the next capture request goes out before this 1-2 ms copy, not after it
        if (gl->upload(c.index, slot.memory, slot.width, slot.height, slot.stride)) {
            uploaded[c.index] = c.frames;
            if (!gl->holding(c.index)) c.held_dbuf.reset();
        } else upload_failed = true;
    }
    std::unique_ptr<Buffer> make_shm(std::uint32_t w, std::uint32_t h, std::uint32_t stride, std::uint32_t format, const char* tag) {
        auto b = std::make_unique<Buffer>(); b->width = w; b->height = h; b->stride = stride; b->bytes = std::size_t(stride) * h;
        int fd = memfd_create(tag, MFD_CLOEXEC);
        if (fd < 0) return nullptr;
        if (ftruncate(fd, static_cast<off_t>(b->bytes)) < 0) { ::close(fd); return nullptr; }
        b->memory = mmap(nullptr, b->bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (b->memory == MAP_FAILED) { ::close(fd); return nullptr; }
        auto* pool = wl_shm_create_pool(shm, fd, static_cast<int>(b->bytes));
        b->proxy = wl_shm_pool_create_buffer(pool, 0, static_cast<int>(w), static_cast<int>(h), static_cast<int>(stride), format);
        wl_shm_pool_destroy(pool); ::close(fd);
        return b;
    }
    void capture_next(Capture& c) {
        if (c.stopped || c.frame || !c.buffers.may_capture()) return;
        int slot = -1; wl_buffer* target = nullptr;
        if (c.path == Capture::Path::Dmabuf) {
            if (c.pending) return;
            for (int i = 0; i < 3; ++i) if (i != c.ready && i != c.presented) { slot = i; break; }  // never the latest or the sampled frame
            if (slot < 0 || !c.dproxy[slot]) return;
            target = c.dproxy[slot];
        } else if (c.path == Capture::Path::Shm) {
            if (!c.slots[0] || !c.slots[1]) return;
            slot = c.ready == 0 ? 1 : 0;  // never overwrite the frame we may be presenting
            target = c.slots[slot]->proxy;
        } else return;
        c.frame = ext_image_copy_capture_session_v1_create_frame(c.session);
        ext_image_copy_capture_frame_v1_add_listener(c.frame, &capture_frame_listener, &c);
        ext_image_copy_capture_frame_v1_attach_buffer(c.frame, target);
        ext_image_copy_capture_frame_v1_damage_buffer(c.frame, 0, 0, static_cast<int>(c.buffers.allocated()->width),
                                                      static_cast<int>(c.buffers.allocated()->height));
        ext_image_copy_capture_frame_v1_capture(c.frame);
        c.capturing = slot;
    }

    void keyboard_interactivity(std::uint32_t value) override { zwlr_layer_surface_v1_set_keyboard_interactivity(layer, value); }
    void exclusive_zone(std::int32_t value) override { zwlr_layer_surface_v1_set_exclusive_zone(layer, value); }
    void empty_input_region() override {
        auto* region = wl_compositor_create_region(compositor);
        wl_surface_set_input_region(surface, region); wl_region_destroy(region);
    }
    void commit() override { wl_surface_commit(surface); }
    static void geometry(void*, wl_output*, int32_t, int32_t, int32_t, int32_t, int32_t, const char*, const char*, int32_t) {}
    static void mode(void*, wl_output*, uint32_t, int32_t, int32_t, int32_t) {}
    static void done(void*, wl_output*) {}
    static void scale(void*, wl_output*, int32_t) {}
    static void name(void* data, wl_output*, const char* name) { static_cast<Output*>(data)->name = name; }
    static void description(void*, wl_output*, const char*) {}
    inline static const wl_output_listener output_listener{geometry, mode, done, scale, name, description};
    static void global(void* data, wl_registry* registry, uint32_t id, const char* interface, uint32_t version) {
        auto& s = *static_cast<Impl*>(data);
        if (std::strcmp(interface, "wl_compositor") == 0 && version >= 4 && !s.compositor) {
            s.compositor = static_cast<wl_compositor*>(wl_registry_bind(registry, id, &wl_compositor_interface, 4));
            s.required_globals.push_back(id);
        } else if (std::strcmp(interface, "wl_shm") == 0 && !s.shm) {
            s.shm = static_cast<wl_shm*>(wl_registry_bind(registry, id, &wl_shm_interface, 1)); s.required_globals.push_back(id);
        } else if (std::strcmp(interface, "zwlr_layer_shell_v1") == 0 && version > 0 && !s.shell) {
            s.shell = static_cast<zwlr_layer_shell_v1*>(wl_registry_bind(registry, id, &zwlr_layer_shell_v1_interface, layer_version(version)));
            s.required_globals.push_back(id);
        } else if (std::strcmp(interface, "wp_presentation") == 0 && version > 0 && !s.presentation) {
            s.metrics.bind(version); // v1 semantics, even when installed XML advertises v2.
            s.presentation = static_cast<wp_presentation*>(wl_registry_bind(registry, id,
                             &wp_presentation_interface, s.metrics.snapshot().protocol_version));
            wp_presentation_add_listener(s.presentation, &presentation_listener, &s);
            s.required_globals.push_back(id); // Removal stops this run, never silently changes timing capability.
        } else if (s.capture_requested && std::strcmp(interface, "ext_output_image_capture_source_manager_v1") == 0 && !s.source_manager) {
            s.source_manager = static_cast<ext_output_image_capture_source_manager_v1*>(
                wl_registry_bind(registry, id, &ext_output_image_capture_source_manager_v1_interface, 1));
            s.required_globals.push_back(id);
        } else if (s.capture_requested && std::strcmp(interface, "ext_image_copy_capture_manager_v1") == 0 && !s.copy_manager) {
            s.copy_manager = static_cast<ext_image_copy_capture_manager_v1*>(
                wl_registry_bind(registry, id, &ext_image_copy_capture_manager_v1_interface, 1));
            s.required_globals.push_back(id);
        } else if (s.capture_requested && std::strcmp(interface, "zwp_linux_dmabuf_v1") == 0 && version >= 3 && !s.linux_dmabuf) {
            // Optional: absent or removed means SHM capture; no listener (format events are ignored).
            s.linux_dmabuf = static_cast<zwp_linux_dmabuf_v1*>(wl_registry_bind(registry, id, &zwp_linux_dmabuf_v1_interface, 3));
        } else if (std::strcmp(interface, "wp_viewporter") == 0 && !s.viewporter) {
            s.viewporter = static_cast<wp_viewporter*>(wl_registry_bind(registry, id, &wp_viewporter_interface, 1));
        } else if (std::strcmp(interface, "wp_fractional_scale_manager_v1") == 0 && !s.fractional_manager) {
            s.fractional_manager = static_cast<wp_fractional_scale_manager_v1*>(
                wl_registry_bind(registry, id, &wp_fractional_scale_manager_v1_interface, 1));
        } else if (std::strcmp(interface, "wl_output") == 0 && version >= 4) {
            auto output = std::make_unique<Output>(); output->global = id;
            output->proxy = static_cast<wl_output*>(wl_registry_bind(registry, id, &wl_output_interface, 4));
            wl_output_add_listener(output->proxy, &output_listener, output.get());
            s.outputs.push_back(std::move(output));
        }
        // Never bind wl_seat, virtual input or SDK globals. Capture managers only with an explicit allowlist.
    }
    static void removed(void* data, wl_registry*, uint32_t id) {
        auto& s = *static_cast<Impl*>(data);
        if ((s.selected && s.selected->global == id) ||
            std::find(s.required_globals.begin(), s.required_globals.end(), id) != s.required_globals.end()) s.closed = true;
        // A captured output gone: only ever after (or while) the supervisor removes that desktop (it sends `desktops`
        // without it first) or when it ends the run; its own guard ends the run if an output goes any other way. The
        // capture stops for good (it never re-attaches to a later output of that name on its own).
        for (auto& c : s.captures) if (c->output && c->output->global == id) {
            if (!s.allocation_enabled) s.closed = true;  // still starting up: fail closed as before
            c->gone = true; c->stopped = true; c->output = nullptr;
        }
        for (auto& output : s.outputs) if (output->global == id) output->name.clear();
    }
    inline static const wl_registry_listener registry_listener{global, removed};
    static void configure(void* data, zwlr_layer_surface_v1* layer, uint32_t serial, uint32_t w, uint32_t h) {
        auto& s = *static_cast<Impl*>(data);
        zwlr_layer_surface_v1_ack_configure(layer, serial);
        if (!valid_surface_size(w, h)) { s.closed = true; return; }
        s.width = w; s.height = h; s.configured = true; s.cadence.invalidate();
        s.help.set_viewport(w, h);  // the help card's text size comes from the output height
        s.binds.set_viewport(w, h);
        s.apply_scale();
        s.half_w = 0;  // recompute FOV split for the new aspect
    }
    // The buffer size for the current logical size and preferred scale; sent with the next commit (the next swap).
    void apply_scale() {
        if (!width || !height) return;
        const auto px = viewport ? scaled_surface_size(width, height, scale120) : std::array<std::uint32_t, 2>{width, height};
        px_w = px[0]; px_h = px[1];
        if (viewport) wp_viewport_set_destination(viewport, int(width), int(height));
        if (gl) gl->resize(width, height, px_w, px_h);
        cadence.invalidate();
    }
    static void preferred_scale(void* data, wp_fractional_scale_v1*, uint32_t scale) {
        auto& s = *static_cast<Impl*>(data);
        if (scale == s.scale120) return;
        s.scale120 = scale; s.apply_scale();
    }
    inline static const wp_fractional_scale_v1_listener fractional_listener{preferred_scale};
    static void layer_closed(void* data, zwlr_layer_surface_v1*) { static_cast<Impl*>(data)->closed = true; }
    inline static const zwlr_layer_surface_v1_listener layer_listener{configure, layer_closed};
    static void frame_done(void* data, wl_callback* callback, uint32_t) {
        auto& s = *static_cast<Impl*>(data); wl_callback_destroy(callback); s.frame = nullptr; s.cadence.frame_done();
        const double now = double(monotonic_ms());
        if (s.view.animating() || s.tracking_requested || s.popup_drawn || s.popups.active(now) || s.help.animating(now) ||
            (s.help_drawn && !s.help.visible()) || s.binds.animating(now) || (s.binds_drawn && !s.binds.visible()) || (s.glass.animating(now) && s.view.zoom() < 0.01f) ||  // glass: overview only
            (s.glass_drawn && !s.glass.any_expanded(now)) || s.placements_easing() || s.sizes_easing() || (s.gl && s.environment.animating(now)) || s.panel_shift.animating(now) ||
            (s.environment_drawn && !s.environment.on()) ||  // its fade; settled it needs no frames of its own: see environment.hpp
            (s.video && (s.video->active() || s.video->animating(now))))  // a playing video draws every frame
            s.cadence.invalidate();  // animate / follow the head / fade a pop-up or the help card, morph a glass pill every frame
        if (s.tile_ease.active()) s.cadence.invalidate();
    }
    inline static const wl_callback_listener frame_listener{frame_done};
    static void released(void* data, wl_buffer*) { static_cast<Buffer*>(data)->busy = false; }
    inline static const wl_buffer_listener buffer_listener{released};
    static void sync_done(void* data, wl_callback*, uint32_t) { *static_cast<bool*>(data) = true; }
    inline static const wl_callback_listener sync_listener{sync_done};
    // `wake_ready` (optional) is false only when poll saw the wake fd idle; true whenever it may need reading.
    bool pump(int timeout, int wake_fd = -1, bool* wake_ready = nullptr) {
        if (wake_ready) *wake_ready = true;
        if (!display || closed) return false;
        int dispatched = 0;
        while (wl_display_prepare_read(display) != 0) {
            const int n = wl_display_dispatch_pending(display);
            if (n < 0) return false;
            dispatched += n;
        }
        if (dispatched) settle_captures();
        const int flushed = wl_display_flush(display);
        if (flushed < 0 && errno != EAGAIN) { wl_display_cancel_read(display); return false; }
        pollfd fds[2] = {{wl_display_get_fd(display), static_cast<short>(POLLIN | (flushed < 0 ? POLLOUT : 0)), 0}, {wake_fd, POLLIN, 0}};
        auto& fd = fds[0];
        // A readable wake fd only ends the wait; the caller reads it. Its errors are the caller's to notice too.
        // Events another reader (Mesa) had queued were just dispatched and may allow a draw: look, do not wait.
        int result = poll(fds, wake_fd >= 0 ? 2 : 1, dispatched > 0 ? 0 : timeout);
        if (result < 0 && errno == EINTR) { wl_display_cancel_read(display); return true; }
        if (result < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) { wl_display_cancel_read(display); return false; }
        if (wake_ready && wake_fd >= 0) *wake_ready = fds[1].revents != 0;
        if (fd.revents & POLLIN) {
            if (wl_display_read_events(display) < 0) return false;
        } else wl_display_cancel_read(display);
        if (wl_display_dispatch_pending(display) < 0 || closed) return false;
        settle_captures();
        if (closed) return false;
        // Send what the callbacks queued (a capture's next frame request above all) now, not with the next swap or pump.
        // A full socket (EAGAIN) or a broken one is left to the next flush, as before.
        (void)wl_display_flush(display);
        return true;
    }
    bool roundtrip() {
        bool finished = false;
        auto* callback = wl_display_sync(display); wl_callback_add_listener(callback, &sync_listener, &finished);
        auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!finished && std::chrono::steady_clock::now() < deadline) if (!pump(50)) break;
        wl_callback_destroy(callback); return finished;
    }
    ~Impl() {
        cadence.stop();
        cancel_feedback();
        video.reset();  // mpv and its textures need the GL context, so before it goes
        gl.reset();  // before the wl_surface it wraps and the dmabufs it imported
        captures.clear();
        allocator.reset();  // after every GBM buffer
        if (linux_dmabuf) zwp_linux_dmabuf_v1_destroy(linux_dmabuf);
        if (copy_manager) ext_image_copy_capture_manager_v1_destroy(copy_manager);
        if (source_manager) ext_output_image_capture_source_manager_v1_destroy(source_manager);
        if (presentation) wp_presentation_destroy(presentation);
        if (fractional) wp_fractional_scale_v1_destroy(fractional);
        if (viewport) wp_viewport_destroy(viewport);
        if (fractional_manager) wp_fractional_scale_manager_v1_destroy(fractional_manager);
        if (viewporter) wp_viewporter_destroy(viewporter);
        if (frame) wl_callback_destroy(frame);
        if (layer) zwlr_layer_surface_v1_destroy(layer);
        if (surface) wl_surface_destroy(surface);
        buffers.clear();
        for (auto& o : outputs) if (o->proxy) wl_output_release(o->proxy);
        if (shell) {
            if (zwlr_layer_shell_v1_get_version(shell) >= 3) zwlr_layer_shell_v1_destroy(shell);
            else wl_proxy_destroy(reinterpret_cast<wl_proxy*>(shell));
        }
        if (shm) wl_shm_destroy(shm);
        if (compositor) wl_compositor_destroy(compositor);
        if (registry) wl_registry_destroy(registry);
        if (display) { wl_display_flush(display); wl_display_disconnect(display); }
    }
};
Presentation::Presentation() = default;
Presentation::~Presentation() = default;
void Presentation::close() {
    if (impl_) { impl_->cancel_feedback(); final_metrics_ = impl_->metrics; final_capture_ = capture_report(); impl_.reset(); }
}
std::string Presentation::status_json(double now_ms) const {
    if (!impl_) return "{}";
    auto raw = impl_->metrics.status_json(now_ms);
    const auto add = [&raw](const std::string& field) {  // one more `"key":value` in the JSON object
        raw.pop_back();
        if (raw.size() > 1) raw += ",";
        raw += field + "}";
    };
    if (impl_->video && impl_->video->state() != VideoState::off)  // VR video state for the supervisor (video.py)
        add(std::string("\"video\":\"") + video_state_name(impl_->video->state()) + "\"");
    // What the exit report says about tracking and capture, while it can still be acted on (incident_detect.py).
    if (impl_->tracking_requested) add(std::string("\"tracking\":") + (impl_->pose.tracking() ? "true" : "false"));
    if (!impl_->captures.empty()) {  // in a row: a resize fails a capture or two each time, and the next frame ends that
        std::uint64_t run = 0;
        for (const auto& ptr : impl_->captures) run = std::max(run, ptr->failure_run);
        add("\"capture_failure_run\":" + std::to_string(run));
    }
    // PoseAge is relative to an unknown transport floor/device clock offset;
    // it cannot substantiate an absolute pose_age_ms measurement.
    std::string path;
    for (const auto& ptr : impl_->captures) {
        const auto& c = *ptr;
        if (!c.frames) return raw;
        const std::string current = c.path == Impl::Capture::Path::Dmabuf ? "dmabuf" : c.path == Impl::Capture::Path::Shm ? "shm" : "";
        if (current.empty() || (!path.empty() && current != path)) return raw;
        path = current;
    }
    if (!path.empty()) add("\"capture_path\":\"" + path + "\"");
    return raw;
}
std::string Presentation::timing_report() const {
    return (impl_ ? impl_->metrics : final_metrics_).report();
}
std::string Presentation::capture_report() const {
    if (!impl_) return final_capture_;
    std::string out = "{\"kind\":\"managed_output_capture\",\"outputs\":[";
    for (std::size_t i = 0; i < impl_->captures.size(); ++i) {
        const auto& c = *impl_->captures[i];
        const auto& plan = c.buffers.allocated() ? c.buffers.allocated() : c.buffers.target();
        out += (i ? "," : "") + std::string("{\"name\":\"") + c.name + "\",\"frames\":" + std::to_string(c.frames) +
               ",\"failures\":" + std::to_string(c.failures) + ",\"width\":" + std::to_string(plan ? plan->width : 0) +
               ",\"height\":" + std::to_string(plan ? plan->height : 0) + ",\"format\":" + std::to_string(plan ? plan->format : 0) +
               ",\"constraint_batches\":" + std::to_string(c.buffers.batches()) + ",\"reallocations\":" + std::to_string(c.buffers.reallocations()) +
               ",\"resize_settles\":" + std::to_string(c.resize.settles()) + ",\"shape_eases\":" + std::to_string(c.shape.eases()) +
               ",\"path\":\"" + (c.path == Impl::Capture::Path::Dmabuf ? "dmabuf" : c.path == Impl::Capture::Path::Shm ? "shm" : "none") +
               "\",\"dmabuf_frames\":" + std::to_string(c.dmabuf_frames) +
               (c.path == Impl::Capture::Path::Dmabuf ? ",\"fourcc\":" + std::to_string(c.choice.fourcc) + ",\"modifier\":" + std::to_string(c.choice.modifier) : std::string()) +
               ",\"fallback\":\"" + c.fallback + "\",\"stopped\":" + (c.stopped ? "true" : "false") + "}";
    }
    out += "],\"help\":{\"visible\":" + std::string(impl_->help.visible() ? "true" : "false") +
           ",\"shown\":" + std::to_string(impl_->help.shown_count()) + ",\"hidden\":" + std::to_string(impl_->help.hidden_count()) +
           ",\"page\":" + std::to_string(impl_->help.page()) + ",\"pages\":" + std::to_string(impl_->help.pages()) +
           ",\"turns\":" + std::to_string(impl_->help.turns()) + "}";
    out += ",\"binds\":{\"visible\":" + std::string(impl_->binds.visible() ? "true" : "false") +
           ",\"shown\":" + std::to_string(impl_->binds.shown_count()) + ",\"hidden\":" + std::to_string(impl_->binds.hidden_count()) +
           ",\"first\":" + std::to_string(impl_->binds.first()) + ",\"slots\":" + std::to_string(impl_->binds.slots()) + "}";
    out += ",\"placement_menu\":{\"open\":" + std::string(impl_->glass.open() ? "true" : "false") +
           ",\"desktop\":" + std::to_string(impl_->glass.content_desktop()) +
           ",\"shown\":" + std::to_string(impl_->glass.opened()) + ",\"hidden\":" + std::to_string(impl_->glass.closed()) +
           ",\"renders\":" + std::to_string(impl_->glass.renders()) +
           ",\"view_shift_mrad\":" + std::to_string(int(std::lround(impl_->panel_shift.value(double(monotonic_ms())) * 1000.f))) + "}";
    out += ",\"resize_handle\":{\"shown\":" + std::to_string(impl_->handle.shown) + "}";
    out += ",\"move_handle\":{\"shown\":" + std::to_string(impl_->pills.shown) + "}";
    out += ",\"overview_pointer\":{\"shown\":" + std::to_string(impl_->free_pointer.shown) + "}";
    out += ",\"theme\":{\"accent\":[" + std::to_string(impl_->accent[0]) + "," + std::to_string(impl_->accent[1]) + "," +
           std::to_string(impl_->accent[2]) + "],\"updates\":" + std::to_string(impl_->accent_updates) + "}";
    const auto mode = impl_->modes.mode();
    out += ",\"tracking\":{\"requested\":" + std::string(impl_->tracking_requested ? "true" : "false") +
           ",\"mode\":\"" + (mode == ViewMode::locked ? "locked" : mode == ViewMode::follow ? "follow" : "anchor") + "\"" +
           ",\"feed\":" + (impl_->pose_feed ? "true" : "false") + ",\"tracking\":" + (impl_->pose.tracking() ? "true" : "false") +
           ",\"samples\":" + std::to_string(impl_->pose.samples()) + ",\"lost_events\":" + std::to_string(impl_->pose.lost_events()) +
           ",\"reanchors\":" + std::to_string(impl_->pose.reanchors()) + ",\"hints\":" + std::to_string(impl_->tracking_hints) +
           ",\"diagonal_fov_deg\":" + std::to_string(impl_->fov_deg) + ",\"fov_source\":\"" +
           (impl_->fov_from_driver ? "driver" : "fallback-46") + "\",\"half_fov_tan\":[" + std::to_string(impl_->half_w) + "," +
           std::to_string(impl_->half_h) + "]}";
    out += ",\"surface\":{\"logical\":[" + std::to_string(impl_->width) + "," + std::to_string(impl_->height) + "],\"buffer\":[" +
           std::to_string(impl_->px_w) + "," + std::to_string(impl_->px_h) + "],\"scale120\":" + std::to_string(impl_->scale120) +
           ",\"viewport\":" + (impl_->viewport ? "true" : "false") + "}";
    {  // software timing only; see predict.hpp
        auto stats = [](const LatencySummary& l) {
            return "{\"count\":" + std::to_string(l.count) + ",\"dropped\":" + std::to_string(l.dropped) + ",\"mean_ms\":" +
                   std::to_string(l.mean_ms) + ",\"p50_ms\":" + std::to_string(l.p50_ms) + ",\"p95_ms\":" + std::to_string(l.p95_ms) +
                   ",\"max_ms\":" + std::to_string(l.max_ms) + "}";
        };
        const auto& s = *impl_;
        const auto cfg = s.pose_feed ? s.pose_feed->look_ahead_cfg() : std::nullopt;
        std::string cfg_json = "null";
        if (cfg && look_ahead_from(*cfg))
            cfg_json = "[" + std::to_string((*cfg)[0]) + "," + std::to_string((*cfg)[1]) + "," + std::to_string((*cfg)[2]) + "," +
                       std::to_string((*cfg)[3]) + "]";
        out += ",\"latency\":{\"kind\":\"software_read_to_present\",\"clock_monotonic\":" + std::string(s.latency.clock_ok() ? "true" : "false") +
               ",\"read_to_present\":" + stats(s.latency.summary()) + ",\"pose_age_relative\":" + stats(s.pose_age.summary()) +
               ",\"prediction\":{\"enabled\":" + (s.prediction ? "true" : "false") + ",\"predicted_frames\":" +
               std::to_string(s.predicted_frames) + ",\"unpredicted_frames\":" + std::to_string(s.unpredicted_frames) +
               ",\"last_horizon_ms\":" + std::to_string(s.last_horizon_ms) + ",\"last_predicted_deg\":" + std::to_string(s.last_predicted_deg) +
               ",\"max_deg\":" + std::to_string(Impl::kMaxPredictionDeg) + ",\"look_ahead_cfg\":" + cfg_json + "}}";
    }
    out += ",\"view\":{\"selected\":" + std::to_string(impl_->view.selected()) + ",\"zoom\":" + std::to_string(impl_->view.zoom()) +
           ",\"spacing\":" + std::to_string(impl_->view.spacing()) + ",\"placement_updates\":" + std::to_string(impl_->placement_updates) +
           ",\"roll_updates\":" + std::to_string(impl_->roll.updates) +
           ",\"placements\":[";
    for (std::size_t i = 0; i < impl_->placements.size(); ++i) {
        out += i ? ",[" : "[";
        for (std::size_t k = 0; k < impl_->placements[i].size(); ++k) out += (k ? "," : "") + std::to_string(impl_->placements[i][k]);
        out += "]";
    }
    out += "]}";
    std::string font;  // bounded, JSON-escaped (font paths come from fontconfig)
    for (char c : impl_->popup_font.substr(0, 256)) {
        if (c == '"' || c == '\\') font += '\\';
        font += static_cast<unsigned char>(c) < 0x20 ? '?' : c;
    }
    out += ",\"popup_font\":\"" + font + "\"";
    out += ",\"video\":" + (impl_->video ? impl_->video->report() : std::string("null"));
    out += ",\"composition\":";
    out += impl_->gl ? impl_->gl->report() : std::string("{\"path\":\"cpu-blit\",\"fallback\":\"") + impl_->gl_fallback + "\"}";
    return out + "}";
}
Status Presentation::open(std::string output_name, std::vector<std::string> capture) {
    if (!std::regex_match(output_name, std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) return Error{"output", "invalid output name"};
    if (impl_) return Error{"state", "already open"};
    std::string joined;
    for (const auto& name : capture) joined += (joined.empty() ? "" : ",") + name;
    if (!capture.empty() && parse_capture_list(joined, output_name) != capture) return Error{"capture", "invalid capture allowlist"};
    auto s = std::make_unique<Impl>();
    s->capture_requested = !capture.empty();
    s->display = wl_display_connect(nullptr);
    if (!s->display) return Error{"connect", "Wayland connection failed"};
    s->registry = wl_display_get_registry(s->display);
    wl_registry_add_listener(s->registry, &Impl::registry_listener, s.get());
    if (!s->roundtrip() || !s->roundtrip() || !s->compositor || !s->shm || !s->shell)
        return Error{"globals", "requires compositor v4, shm v1, layer-shell v1..5 and named output v4"};
    for (auto& output : s->outputs) if (output->name == output_name) {
        if (s->selected) return Error{"output", "ambiguous output name"};
        s->selected = output.get();
    }
    if (!s->selected) return Error{"output", "named output not advertised"};
    if (s->capture_requested) {
        if (!s->source_manager || !s->copy_manager) return Error{"capture", "compositor lacks ext image copy capture"};
        for (const auto& name : capture) {
            Impl::Output* found = nullptr;
            for (auto& output : s->outputs) if (output->name == name) {
                if (found) return Error{"capture", "ambiguous managed output"};
                found = output.get();
            }
            if (!found || found == s->selected) return Error{"capture", "managed output not advertised"};
            auto c = std::make_unique<Impl::Capture>(); c->owner = s.get(); c->index = s->captures.size(); c->name = name; c->output = found;
            s->start_session(*c);
            s->captures.push_back(std::move(c));
        }
        // Constraints arrive asynchronously (after the output renders); bounded wait for every session.
        auto ready = [&] { return std::all_of(s->captures.begin(), s->captures.end(), [](const auto& c) { return c->buffers.target() || c->stopped; }); };
        auto capture_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!ready() && std::chrono::steady_clock::now() < capture_deadline) if (!s->pump(50)) break;
        std::string missing;
        for (auto& c : s->captures) if (c->stopped || !c->buffers.target()) missing += (missing.empty() ? "" : ",") + c->name;
        if (!missing.empty()) return Error{"capture", "capture constraints unavailable for " + missing};
    }
    s->surface = wl_compositor_create_surface(s->compositor);
    if (s->viewporter && s->fractional_manager) {  // native resolution; the scale arrives with or after the first configure
        s->viewport = wp_viewporter_get_viewport(s->viewporter, s->surface);
        s->fractional = wp_fractional_scale_manager_v1_get_fractional_scale(s->fractional_manager, s->surface);
        wp_fractional_scale_v1_add_listener(s->fractional, &Impl::fractional_listener, s.get());
    }
    s->layer = zwlr_layer_shell_v1_get_layer_surface(s->shell, s->surface, s->selected->proxy,
                 ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, "xr-workspace-spike-test-pattern");
    zwlr_layer_surface_v1_add_listener(s->layer, &Impl::layer_listener, s.get());
    zwlr_layer_surface_v1_set_anchor(s->layer, ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
                                              ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_size(s->layer, 0, 0);
    apply_presentation_policy(*s);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!s->configured && std::chrono::steady_clock::now() < deadline) if (!s->pump(50)) break;
    if (!s->configured || s->closed) return Error{"configure", "configure failed or timed out"};
    if (s->capture_requested) {
        s->gl = GlCompositor::create(s->display, s->surface, s->width, s->height, s->captures.size(), s->gl_fallback);
        if (s->gl) s->gl->resize(s->width, s->height, s->px_w, s->px_h);  // a scale that came before it
        if (!s->gl && s->gl_fallback.empty()) s->gl_fallback = "unknown";
        s->uploaded.assign(s->captures.size(), 0);
        s->drawn.assign(s->captures.size(), 0);
        s->frames_at_draw.assign(s->captures.size(), 0);
        s->early_hold.assign(s->captures.size(), 0);
        s->allocation_enabled = true;
        s->settle_captures();  // first allocation of every capture's buffers
        if (s->closed) return Error{"capture", "capture buffer allocation failed"};
        // The capture buffer (dmabuf params) or first frame requests go out now, so the compositor works on them while
        // the fonts load below instead of after. A failed flush is found by the next pump, as before.
        (void)wl_display_flush(s->display);
        if (s->gl) {  // pop-up text font; the built-in pixel font is the reported fallback
            std::string why; auto font = HudFont::load(kHudFontFamily, why);
            s->popup_font = font ? font->description() : "pixel-fallback: " + why;
            s->popups.set_font(std::move(font));
            s->tracking_hint.set_font(s->popups.font());
            std::string help_why; s->help.set_font(HudFont::load(kHudFontFamily, help_why));  // same family, own instance
            std::string binds_why; s->binds.set_font(HudFont::load(kHudFontFamily, binds_why));
            std::string glass_why; s->glass.set_font(HudFont::load(kHudFontFamily, glass_why));
        }
    }
    impl_ = std::move(s); return {};
}
Status Presentation::dispatch(int timeout_ms, int wake_fd, bool* wake_ready) {
    if (wake_ready) *wake_ready = true;
    if (timeout_ms < 0 || timeout_ms > 100 || !impl_ || !impl_->pump(timeout_ms, wake_fd, wake_ready)) return Error{"dispatch", "closed, removed or disconnected"};
    impl_->service_captures();
    return {};
}
void Presentation::set_desktops(const std::vector<std::string>& outputs, const std::vector<std::string>& renew) {
    if (!impl_) return;
    impl_->glide.ms = 0;  // a glide is per desktop index: eased on from where it got to
    impl_->set_desktops(outputs, renew);
}
Status Presentation::animate_pattern(std::uint32_t duration_ms) {
    if (!impl_ || !duration_ms || duration_ms > 60000 || impl_->metrics.snapshot().submitted)
        return Error{"pattern", "animation requires configured surface, finite 1..60000 ms, before submission"};
    impl_->cadence = PatternCadence(duration_ms, monotonic_ms());
    return {};
}
void Presentation::enable_tracking(std::string prefix) {
    if (!impl_ || !impl_->gl) return;  // head tracking needs GPU composition
    impl_->tracking_requested = true; impl_->pose_prefix = std::move(prefix); impl_->cadence.invalidate();
}
void Presentation::set_accent(std::uint8_t r, std::uint8_t g, std::uint8_t b) {
    if (!impl_ || !impl_->gl) return;
    const std::array<float, 3> seed{r / 255.f, g / 255.f, b / 255.f};  // the seed of the M3 scheme (material.hpp)
    impl_->gl->set_accent(r, g, b);  // highlight border, glow, the pills' glass, the level bars and corner caps
    impl_->glass.set_accent(seed);   // an open panel (glass_theme.hpp)
    impl_->popups.set_accent(seed, double(monotonic_ms()));  // the pop-ups and the cards, redrawn if showing
    impl_->tracking_hint.set_accent(seed);
    impl_->help.set_accent(seed);
    impl_->binds.set_accent(seed);
    if (impl_->video) impl_->video->set_accent(seed);
    if (impl_->accent != std::array<std::uint8_t, 3>{r, g, b}) { impl_->accent = {r, g, b}; ++impl_->accent_updates; }
    impl_->cadence.invalidate();
}
void Presentation::set_environment(std::uint8_t background, std::uint16_t brightness) {
    if (!impl_) return;  // kept even before the first configure creates the compositor: a saved one is the run's first frames
    auto& s = *impl_;
    const auto chosen = background < kBackgroundCount ? Background(background) : Background::none;
    if (chosen != Background::none) s.environment_background = chosen;
    s.environment_brightness = brightness;
    s.environment.set(chosen != Background::none, double(monotonic_ms()));
    s.cadence.invalidate();
}
void Presentation::video_open(int fd, const VideoFormat& format, const std::string& title) {
    if (!impl_ || !impl_->gl) { if (fd >= 0) ::close(fd); return; }  // needs GPU composition
    auto& s = *impl_;
    if (!s.video) {
        s.video = std::make_unique<VideoMode>();
        std::string why; s.video->set_font(HudFont::load(kHudFontFamily, why));
        s.video->set_accent({s.accent[0] / 255.f, s.accent[1] / 255.f, s.accent[2] / 255.f});  // the theme colour so far
        s.video->set_quality(s.video_quality);
    }
    s.video->open(fd, format, title, s.display, double(monotonic_ms()));
    s.cadence.invalidate();
}
void Presentation::video_close() { if (impl_ && impl_->video) { impl_->video->close(); impl_->cadence.invalidate(); } }
void Presentation::video_pause() { if (impl_ && impl_->video) impl_->video->toggle_pause(); }
void Presentation::video_seek(std::int32_t seconds) { if (impl_ && impl_->video) impl_->video->seek(double(seconds)); }
void Presentation::video_format(const VideoFormat& format) {
    if (impl_ && impl_->video) { impl_->video->set_format(format); impl_->cadence.invalidate(); }
}
void Presentation::video_controls(bool visible) {
    if (impl_ && impl_->video) { impl_->video->set_controls(visible, double(monotonic_ms())); impl_->cadence.invalidate(); }
}
void Presentation::video_quality(const VideoQuality& quality) {
    if (!impl_) return;
    impl_->video_quality = quality;  // kept for a VideoMode created later (the first open)
    if (impl_->video) { impl_->video->set_quality(quality); impl_->cadence.invalidate(); }
}
void Presentation::video_menu(bool visible, const std::vector<VideoMenuRow>& rows, std::uint32_t selected) {
    if (impl_ && impl_->video) { impl_->video->set_menu(visible, rows, selected); impl_->cadence.invalidate(); }
}
void Presentation::set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows,
                            const std::vector<std::uint32_t>& sections, std::int32_t turn) {
    if (!impl_) return;
    std::vector<HelpRow> help;
    for (const auto& [keys, action] : rows) help.push_back({keys, action});
    std::size_t start = 0;
    for (const auto n : sections) {  // ipc.cpp checked they add up to the rows
        if (start < help.size()) help[start].section = true;
        start += n;
    }
    if (impl_->help.set(visible, help, double(monotonic_ms()), turn)) impl_->cadence.invalidate();
}
void Presentation::set_binds(const BindPanelState& state) {
    if (impl_ && impl_->binds.set(state, double(monotonic_ms()))) impl_->cadence.invalidate();
}
void Presentation::set_placement_menu(const PlacementMenuState& state, const std::optional<std::array<std::int32_t, 4>>& surface) {
    if (!impl_) return;
    if (impl_->glass.set(state, double(monotonic_ms()), surface)) impl_->cadence.invalidate();
}
void Presentation::set_placements(const std::vector<std::array<std::int32_t, 4>>& placements, std::uint32_t glide_ms) {
    if (!impl_) return;
    start_glide(impl_->glide, impl_->eased, double(monotonic_ms()), glide_ms);  // (0: a glide under way ends; eased on from where it is)
    impl_->placements.assign(placements.begin(), placements.begin() + std::ptrdiff_t(std::min(placements.size(), impl_->captures.size())));
    ++impl_->placement_updates;
    impl_->cadence.invalidate();
}
void Presentation::set_resize_handle(bool visible, std::uint32_t desktop, std::uint32_t corner, bool active) {
    if (!impl_) return;
    auto& h = impl_->handle;
    if (visible && !h.visible) ++h.shown;
    h.visible = visible; h.desktop = desktop; h.corner = corner; h.active = active;
    impl_->cadence.invalidate();
}
void Presentation::set_move_handle(bool visible, std::uint32_t desktop, bool hover, bool active) {
    if (!impl_) return;
    auto& p = impl_->pills;
    if (visible && !p.visible) ++p.shown;
    p.visible = visible; p.desktop = desktop; p.hover = hover; p.active = active;
    impl_->cadence.invalidate();
}
void Presentation::set_overview_pointer(bool visible, std::int32_t x, std::int32_t y, bool held, bool directed, std::int32_t az,
                                        std::int32_t el, std::int32_t ghost_w, std::int32_t ghost_h, std::int32_t target, PointerLook look) {
    if (!impl_) return;
    auto& p = impl_->free_pointer;
    if (visible && !p.visible) ++p.shown;
    p.visible = visible; p.x = x; p.y = y; p.held = held; p.directed = directed; p.az = az; p.el = el;
    p.ghost_w = ghost_w; p.ghost_h = ghost_h; p.target = target; p.look = look;
    impl_->cadence.invalidate();
}
void Presentation::set_roll(const std::array<std::int32_t, kMaxDesktops>& roll, const std::array<bool, kMaxDesktops>& bars, std::int32_t hover,
                            std::int8_t side, bool active) {
    if (!impl_) return;
    auto& s = *impl_;
    if (s.rolls != roll) s.glide.ms = 0;  // a glide aims at the placements it began with: eased on from where it got to
    s.rolls = roll;
    s.roll.bars = bars; s.roll.hover = hover; s.roll.side = side; s.roll.active = active; ++s.roll.updates;
    s.cadence.invalidate();
}
std::optional<std::string> Presentation::take_roll_geometry(double now_ms) {
    if (!impl_ || !impl_->gl || impl_->captures.empty() || !impl_->half_w) return std::nullopt;
    auto& s = *impl_;
    const auto reference = s.pose.reference();
    const bool measured = s.pose.tracking() && reference && s.modes.mode() == ViewMode::anchor && s.modes.settled() &&
                          !(s.video && s.video->active());
    if (measured && s.roll_geometry_measured && now_ms - s.roll_geometry_ms < kRollGeometryMs) return std::nullopt;  // rate limit
    std::string line = "{\"roll_geometry\":{\"tilt\":[";
    const auto tiles = measured ? overview_layout(s.settled_sizes().span(), float(s.width), float(s.height), s.view.target_spacing())
                                : std::vector<FRect>{};
    const Canvas canvas{float(s.width), float(s.height), s.half_w, s.half_h};
    const auto up = measured ? canvas_up(*reference, s.modes.anchor()) : std::array<float, 3>{0, 1, 0};
    for (std::size_t i = 0; i < s.captures.size(); ++i) {
        std::optional<float> tilt;
        if (measured && i < tiles.size()) {
            Placement p = s.target_placement(i);
            p.roll = 0;  // the supervisor adds its own roll
            tilt = desktop_tilt(canvas, tiles[i], p, Vec3{up[0], up[1], up[2]});
        }
        line += i ? "," : "";
        line += tilt ? std::to_string(std::clamp(int(std::lround(*tilt * 180.f / float(M_PI) * 100.f)), -18000, 18000)) : "null";
    }
    line += "]}}";
    if (line == s.roll_geometry_sent) return std::nullopt;
    s.roll_geometry_sent = line; s.roll_geometry_ms = now_ms; s.roll_geometry_measured = measured;
    return line;
}
void Presentation::resend_roll_geometry() { if (impl_) impl_->roll_geometry_sent.clear(); }
void Presentation::ease_tiles() {
    if (impl_) impl_->tile_ease.arm(impl_->capture_sizes().span(), impl_->draw_tiles, double(monotonic_ms()));
}

std::optional<std::string> Presentation::take_overview_geometry() {
    if (!impl_ || !impl_->gl || impl_->captures.empty() || !impl_->half_w) return std::nullopt;
    auto& s = *impl_;
    // Called on every main-loop wake: lay out and format only when an input changed.
    const auto sizes = s.settled_sizes();
    const auto key = s.layout_key(sizes, s.view.target_spacing(), s.half_w, s.half_h);
    if (s.overview_key != key) {
        const auto tiles = overview_layout(sizes.span(), float(s.width), float(s.height), s.view.target_spacing());
        s.overview_line = overview_geometry_json(tiles, s.width, s.height, s.half_w, s.half_h);
        s.overview_key = key;
    }
    const auto& line = s.overview_line;
    if (line.empty() || line == s.overview_geometry_sent) return std::nullopt;
    s.overview_geometry_sent = line;
    return line;
}
void Presentation::resend_overview_geometry() { if (impl_) impl_->overview_geometry_sent.clear(); }
std::optional<std::string> Presentation::take_help_geometry(double now_ms) {
    if (!impl_ || !impl_->gl) return std::nullopt;
    auto& s = *impl_;
    const bool up = s.help.visible() && s.help.generation() && s.view.zoom() < 0.01f && s.half_w > 0 &&
                    !(s.video && s.video->active());  // the free pointer is overview-only and off during a video
    // Called on every main-loop wake. The two common cases return what the full path below would, without building a line:
    // the card is down and "null" was already sent, or it stays up inside the rate limit (equal line or not, no send).
    static const std::string kNullLine = "{\"help_geometry\":null}";
    if (!up && s.help_geometry_sent == kNullLine) return std::nullopt;
    if (up && s.help_geometry_sent.rfind("{\"help_geometry\":{", 0) == 0 && now_ms - s.help_geometry_ms < kHelpGeometryMs)
        return std::nullopt;
    std::string line = kNullLine;
    if (up) {
        const auto& im = s.help.image();
        line = help_geometry_json(s.last_head, s.width, s.height, s.half_w, s.half_h,
                                  centered_card_origin(s.width, s.height, im.width, im.height, 0.f), im.width, im.height,
                                  s.help.controls());
        if (line.empty()) line = "{\"help_geometry\":null}";
    }
    if (line == s.help_geometry_sent) return std::nullopt;
    // While the card stays up, changes are rate-limited (head motion, mostly; a new page size is at most kHelpGeometryMs
    // late, the main loop retries every pass). A show or hide goes out at once.
    const bool still_up = up && s.help_geometry_sent.rfind("{\"help_geometry\":{", 0) == 0;
    if (still_up && now_ms - s.help_geometry_ms < kHelpGeometryMs) return std::nullopt;
    s.help_geometry_sent = line; s.help_geometry_ms = now_ms;
    return line;
}
void Presentation::resend_help_geometry() { if (impl_) impl_->help_geometry_sent.clear(); }
void Presentation::recenter() { if (impl_) { impl_->pose.recenter(); impl_->modes.recenter(); } }
void Presentation::set_edge_pan(const EdgePan& pan) {
    if (!impl_) return;
    impl_->view.set_pan(pan, double(monotonic_ms()));
    impl_->cadence.invalidate();
}
void Presentation::set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode, bool cursor_follow,
                            bool prediction, bool typing, bool center, bool cut, bool pan) {
    if (!impl_) return;
    // Maximize-follow (2026-11-03): the overview is always anchor and a maximized desktop follow (or locked), so a maximize
    // and an un-maximize change the mode as well. Those changes need no pop-up (only Ctrl+Alt+V's does), the zoom-in grows
    // the desktop in place before follow takes over, and the zoom-out returns to the room exactly as it was left.
    const bool maximize = zoomed && !impl_->maximized, unmaximize = !zoomed && impl_->maximized;
    const auto view_mode = mode == 1 ? ViewMode::locked : mode == 2 ? ViewMode::follow : ViewMode::anchor;
    if (maximize || unmaximize) impl_->popups.track_mode(mode);
    impl_->popups.update(mode, cursor_follow, prediction, typing, double(monotonic_ms()));
    impl_->prediction = prediction;
    using Change = ModeController::Change;
    impl_->modes.set_mode(view_mode, double(monotonic_ms()),
                          maximize && view_mode != ViewMode::anchor ? Change::maximize
                          : unmaximize && view_mode == ViewMode::anchor ? Change::restore : Change::blend);
    impl_->maximized = zoomed;
    impl_->view.set_target(zoomed, std::min<std::uint32_t>(selected, impl_->captures.empty() ? 0 : std::uint32_t(impl_->captures.size() - 1)),
                           spacing, double(monotonic_ms()), center, cut, pan);
    impl_->cadence.invalidate();
}
bool Presentation::finished() const { return impl_ && impl_->cadence.finished(monotonic_ms()); }
Status Presentation::draw_test_pattern() {
    if (!impl_ || !impl_->configured || impl_->closed) return Error{"state", "surface not configured"};
    auto& s = *impl_;
    if (!s.cadence.ready(monotonic_ms(), true, s.metrics.can_submit())) return {};
    if (s.gl) {
        if (s.upload_failed) return Error{"gl", "texture upload failed"};  // an early upload (upload_early) failed
        for (std::size_t i = 0; i < s.captures.size(); ++i) {
            auto& c = *s.captures[i];
            if (c.ready < 0 || c.frames == s.uploaded[i]) continue;
            if (c.path == Impl::Capture::Path::Dmabuf) {  // zero copy: sample the imported slot directly
                s.gl->present_dmabuf(i, c.ready); c.presented = c.ready; s.uploaded[i] = c.frames;
                if (!s.gl->holding(i)) c.held_dbuf.reset();  // the new buffers' first frame replaced the held one
                continue;
            }
            if (!c.slots[c.ready]) continue;
            const auto& slot = *c.slots[c.ready];  // the slot's own geometry (it may differ from the latest constraints)
            if (!s.gl->upload(i, slot.memory, slot.width, slot.height, slot.stride))
                return Error{"gl", "texture upload failed"};
            s.uploaded[i] = c.frames;
            if (!s.gl->holding(i)) c.held_dbuf.reset();
        }
        std::copy(s.uploaded.begin(), s.uploaded.end(), s.drawn.begin());
        for (std::size_t i = 0; i < s.captures.size() && i < s.early_hold.size(); ++i) {  // see upload_early
            const auto frames = s.captures[i]->frames;
            if (frames - s.frames_at_draw[i] >= 2) s.early_hold[i] = Impl::kEarlyHoldDraws;
            else if (s.early_hold[i]) --s.early_hold[i];
            s.frames_at_draw[i] = frames;
        }
        s.view.advance(double(monotonic_ms()));
        if (!s.half_w) s.update_fov();
        s.ease_placements(double(monotonic_ms()));
        s.ease_sizes(double(monotonic_ms()));
        s.land_turns();
        s.capture_centre();
        Mat3 head{1, 0, 0, 0, 1, 0, 0, 0, 1};
        // Untracked, anchor mode's room is still turned by landed switches (ModeController::turn_anchor; identity otherwise).
        if (!s.tracking_requested && s.modes.mode() == ViewMode::anchor)
            head = view_from(Quat{}, s.modes.update(Quat{}, double(monotonic_ms())));
        Mat3 world = head;  // VR video: world-locked to the recentre reference in every viewing mode
        Mat3 environment_world{1, 0, 0, 0, 1, 0, 0, 0, 1};  // the background: world-locked and level (environment_view)
        timespec read_ts{};
        clock_gettime(CLOCK_MONOTONIC, &read_ts);  // the wp_presentation clock domain (checked in LatencyTracker)
        const std::uint64_t read_ns = std::uint64_t(read_ts.tv_sec) * 1'000'000'000ull + std::uint64_t(read_ts.tv_nsec);
        if (s.tracking_requested) {  // late sample: just before drawing
            const auto now = monotonic_ms();
            if (!s.pose_feed && now >= s.pose_retry_ms) {
                s.pose_feed = PoseFeed::open(s.pose_prefix, s.pose_why); s.pose_retry_ms = now + 1000;
                if (s.pose_feed) s.half_w = 0;  // pick up the driver's FOV
            }
            const auto sample = s.pose_feed ? s.pose_feed->read() : std::nullopt;
            s.pose.update(sample, double(now));
            const bool lost = s.pose.reconnecting(double(now));
            if (s.tracking_hint.update(lost, double(now)) && lost) ++s.tracking_hints;
            Quat relative = s.pose.relative();
            const auto age = sample ? s.pose_age.observe(double(read_ns) / 1e6, sample->device_ms) : std::nullopt;
            if (s.prediction && s.pose.tracking()) {  // never extrapolate a held (stale) pose
                const auto omega = sample ? angular_velocity(history_of(*sample)) : std::nullopt;
                const auto refresh = s.metrics.snapshot().last_refresh_prediction_ns;
                const auto horizon = prediction_horizon(s.pose_feed ? look_ahead_from(s.pose_feed->look_ahead_cfg().value_or(std::array<float, 4>{}))
                                                                    : std::nullopt,
                                                        s.latency.expected_ms(), age.value_or(0.0), refresh ? refresh / 1e6 : 1000.0 / 120);
                if (omega && horizon) {
                    const Quat delta = prediction_delta(*omega, *horizon, Impl::kMaxPredictionDeg);
                    relative = quat_mul(relative, delta);  // body frame: newest sample * delta
                    s.last_horizon_ms = *horizon; s.last_predicted_deg = quat_angle(Quat{}, delta) * float(180 / M_PI);
                    ++s.predicted_frames;
                } else ++s.unpredicted_frames;
            }
            s.modes.set_aim(s.focus_aim());
            // A maximize holds the canvas in the room while the desktop zooms in; follow / locked take over once it lands.
            if (s.modes.holding() && !(s.view.zoom() < 1.f)) s.modes.land_maximize(double(now));
            head = view_from(relative, s.modes.update(relative, double(now)));
            world = view_from(relative, Quat{});
            environment_world = environment_view(relative, s.pose.reference());
        }
        if (!s.half_w) s.update_fov();  // the feed just opened: the driver's FOV
        {  // the settled view keeps its layout; only a change (zoom, switch, spacing, size) lays the tiles out again
            const auto sizes = s.capture_sizes();
            const auto key = s.layout_key(sizes, s.view.spacing(), s.view.zoom(), s.view.position(), s.view.left_zoom(), s.view.left());
            if (s.draw_tiles_key != key) {
                s.draw_tiles = blended_layout(sizes.span(), float(s.width), float(s.height), s.view.spacing(), s.view.zoom(), s.view.position(), s.view.left_zoom(), s.view.left());
                s.draw_tiles_key = key;
            }
        }
        const auto& tiles = s.tile_ease.tiles(s.capture_sizes().span(), s.draw_tiles, s.view.zoom() < 0.01f && !s.view.switching(),
                                              double(monotonic_ms()));  // Ctrl+Alt+R: on their way to that layout
        s.frame = wl_surface_frame(s.surface); wl_callback_add_listener(s.frame, &Impl::frame_listener, &s);
        if (s.presentation) {
            auto slot = std::find_if(s.feedbacks.begin(), s.feedbacks.end(), [](const auto& f) { return !f.proxy; });
            if (slot == s.feedbacks.end()) return Error{"feedback", "feedback capacity invariant failed"};
            auto* proxy = wp_presentation_feedback(s.presentation, s.surface);
            if (!proxy) return Error{"feedback", "feedback allocation failed"};
            *slot = {&s, proxy, *s.metrics.submit(), false, false};
            s.latency.read(slot->ticket, read_ns);
            wp_presentation_feedback_add_listener(proxy, &Impl::feedback_listener, &*slot);
        } else s.metrics.submit();
        const float popup = s.popups.alpha(double(monotonic_ms()));
        const float hint = s.tracking_hint.alpha(double(monotonic_ms()));
        if (popup > 0.f || hint <= 0.f) s.gl->set_popup(popup > 0.f ? &s.popups.image() : nullptr, s.popups.generation(), popup);
        else s.gl->set_popup(&s.tracking_hint.image(), s.tracking_hint.generation(), hint);  // a pop-up goes first, and is brief
        s.popup_drawn = popup > 0.f || hint > 0.f;
        {  // The background behind everything, whatever the viewing mode: the head pose turns it, never the desktops' anchor.
            const double now = double(monotonic_ms());
            const float level = s.environment.level(now);
            s.gl->set_environment(level, s.environment_background, float(s.environment_brightness),
                                  float(std::fmod(now / 1000.0, kEnvironmentLoopS)), environment_world);
            s.environment_drawn = level > 0.f;
        }
        if (s.video && s.video->active()) {  // VR video replaces the desktops; help card and placement panel are closed (video.py)
            const double video_now = double(monotonic_ms());
            if (!s.video->prepare(video_now)) return Error{"gl", "video update failed"};
            float card_alpha = 0; std::uint64_t card_generation = 0;
            const HudImage* card = s.video->card(video_now, card_alpha, card_generation);
            const float fov = s.fov_deg > 0 ? s.fov_deg : 46.f;
            // The background behind the video, in each eye's viewport: what a 180° or fisheye picture leaves of the sphere.
            const VideoMode::Behind behind = [&](float half_w, float half_h) { return s.gl->draw_environment(half_w, half_h); };
            if (!s.gl->draw_scene_and_swap([&](std::uint32_t w, std::uint32_t h) { s.video->draw(world, fov, w, h, behind); }, card, card_generation, card_alpha))
                return Error{"gl", "draw or swap failed"};
            s.video->after_swap();
            s.help_drawn = s.glass_drawn = s.binds_drawn = false;
            s.panel_shift.set(0.f, video_now);  // a video closes the panel
            s.cadence.committed();
            if (wl_display_flush(s.display) < 0 && errno != EAGAIN) return Error{"flush", "connection failed"};
            return {};
        }
        const double help_now = double(monotonic_ms());
        const float help = s.help.alpha(help_now);
        s.gl->set_help(help > 0.f ? &s.help.image() : nullptr, s.help.generation(), help, s.help.offset_px(help_now));
        s.help_drawn = help > 0.f;
        const float binds = s.binds.alpha(help_now);
        s.gl->set_binds(binds > 0.f ? &s.binds.image() : nullptr, s.binds.generation(), binds, s.binds.offset_px(help_now));
        s.binds_drawn = binds > 0.f;
        {  // Glass pills and the open placement panel: overview only (a zoom hides them, and holds an expanding pill's
           // morph until the overview is reached: GlassPills::frame), never moving on their own.
            const bool overview = s.view.zoom() < 0.01f && !s.view.switching();  // not while a switch turns through it
            GlassFrame glass = s.glass.frame(overview, help_now);
            glass.pills = s.pills.visible && overview;
            if (s.pills.hover || s.pills.active) { glass.hover = int(s.pills.desktop); glass.hover_level = s.pills.active ? kGlassActive : kGlassHover; }
            if (glass.surface && glass.surface_desktop >= 0)  // a held slider's panel stays on its desktop's levelling roll
                glass.surface->roll = s.target_placement(std::size_t(glass.surface_desktop)).roll;
            for (std::size_t i = 0; i < glass.bars.size() && i < s.roll.bars.size(); ++i) glass.bars[i] = s.roll.bars[i];
            glass.bar_hover = s.roll.hover; glass.bar_segment = LevelSegment(s.roll.side); glass.bar_active = s.roll.active;
            // The corner under the dot or held: overview only (any zoom hides the caps, so it never lands on a zooming desktop).
            if (s.handle.visible && overview) { glass.cap_hover = int(s.handle.desktop); glass.cap_corner = s.handle.corner; glass.cap_active = s.handle.active; }
            s.gl->set_glass(glass);
            s.glass_drawn = std::any_of(glass.expand.begin(), glass.expand.end(), [](float e) { return e > 0.f; });
            float shift = 0;  // for the panel as it is drawn: on the surface a held slider keeps it on
            const int open = overview && s.glass.open() ? s.glass.content_desktop() : -1;
            if (open >= 0 && std::size_t(open) < tiles.size()) {
                const Canvas canvas{float(s.width), float(s.height), s.half_w, s.half_h};
                shift = glass_panel_shift(canvas, tiles[std::size_t(open)], glass.surface && glass.surface_desktop == open ? *glass.surface : s.target_placement(std::size_t(open)));
            }
            s.panel_shift.set(shift, help_now);
        }
        const bool free_pointer = s.free_pointer.visible && s.view.zoom() < 0.01f && !s.view.switching();  // overview only; world-locked on the canvas
        if (const auto dot = s.pan_dot(tiles, s.resolved_placements(tiles)))  // the hand's: the real cursor is hidden and held
            // (degrees, as the frames below)
            s.gl->set_free_pointer(true, float(s.width) / 2, float(s.height) / 2, kFreePointerLevel, true, (*dot)[0] * float(180 / M_PI), (*dot)[1] * float(180 / M_PI), PointerLook::plain);
        else
            s.gl->set_free_pointer(free_pointer, float(s.free_pointer.x), float(s.free_pointer.y),
                                   s.free_pointer.held ? kFreePointerHeldLevel : kFreePointerLevel, s.free_pointer.directed,
                                   float(s.free_pointer.az) / 100.f, float(s.free_pointer.el) / 100.f, s.free_pointer.look);
        s.gl->set_window_drag(float(s.free_pointer.ghost_w), float(s.free_pointer.ghost_h), free_pointer ? s.free_pointer.target : -1);
        const float panel_shift = s.panel_shift.value(double(monotonic_ms()));
        s.last_head = glass_view_shift(head, panel_shift);  // the help card's hit box is worked out on the canvas as drawn
        const auto& placed = s.resolved_placements(tiles);
        // focus(): the desktop the zoom is on (the one being left while a switch zooms out), drawn on top once zoomed in.
        s.last_drawn = glass_view_shift(s.turned(head), panel_shift);
        const auto pan_layers = s.view.pan_layers();
        if (!s.gl->draw_and_swap(tiles, placed, int(s.view.focus()), s.view.highlight_alpha(), s.last_drawn, s.half_w, s.half_h, pan_layers ? &*pan_layers : nullptr))
            return Error{"gl", "draw or swap failed"};  // swap attaches and commits
        s.cadence.committed();
        if (wl_display_flush(s.display) < 0 && errno != EAGAIN) return Error{"flush", "connection failed"};
        return {};
    }
    std::erase_if(s.buffers, [&](const auto& b) { return !b->busy && (b->width != s.width || b->height != s.height); });
    Impl::Buffer* buffer = nullptr;
    for (auto& b : s.buffers) if (!b->busy) { buffer = b.get(); break; }
    if (!buffer) {
        if (!s.cadence.ready(monotonic_ms(), s.buffers.size() < 3, s.metrics.can_submit())) return {}; // Wait for release; never reuse busy memory.
        auto b = std::make_unique<Impl::Buffer>(); b->width = s.width; b->height = s.height;
        b->bytes = std::size_t(s.width) * s.height * 4; b->stride = s.width * 4;
        int fd = memfd_create("xr-test-pattern", MFD_CLOEXEC);
        if (fd < 0) return Error{"shm", "memfd failed"};
        if (ftruncate(fd, static_cast<off_t>(b->bytes)) < 0) { ::close(fd); return Error{"shm", "resize failed"}; }
        b->memory = mmap(nullptr, b->bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (b->memory == MAP_FAILED) { ::close(fd); return Error{"shm", "map failed"}; }
        auto* pool = wl_shm_create_pool(s.shm, fd, static_cast<int>(b->bytes));
        b->proxy = wl_shm_pool_create_buffer(pool, 0, static_cast<int>(s.width), static_cast<int>(s.height),
                                             static_cast<int>(s.width * 4), WL_SHM_FORMAT_XRGB8888);
        wl_shm_pool_destroy(pool); ::close(fd);
        wl_buffer_add_listener(b->proxy, &Impl::buffer_listener, b.get());
        buffer = b.get(); s.buffers.push_back(std::move(b));
    }
    auto* pixels = static_cast<std::uint32_t*>(buffer->memory);
    if (!s.captures.empty()) {
        const auto sizes = s.capture_sizes();
        const auto key = s.layout_key(sizes, 0.f, 0.f, 0.f);
        if (s.cpu_tiles_key != key) { s.cpu_tiles = side_by_side(sizes.span(), s.width, s.height); s.cpu_tiles_key = key; }
        // Black, each captured source nearest-scaled into its tile; one not captured yet stays black.
        Impl::Buffer::Content content;
        content.valid = true;
        content.n = std::min({s.captures.size(), s.cpu_tiles.size(), content.sources.size()});
        for (std::size_t i = 0; i < content.n; ++i) {
            content.tiles[i] = s.cpu_tiles[i];
            const auto& c = *s.captures[i];
            // The newest frame, or the one held while the buffers are re-created (Capture::held_shm).
            const auto* shown = c.ready >= 0 && c.slots[c.ready] ? c.slots[c.ready].get() : c.held_shm.get();
            if (!shown) continue;
            const auto& slot = *shown;  // the slot's own geometry (it may differ from the latest constraints)
            content.sources[i] = {static_cast<const std::uint32_t*>(slot.memory), slot.width, slot.height, slot.stride / 4};
            content.frames[i] = c.frames;
        }
        if (!(buffer->content == content)) {  // the same frame again (static desktops) is committed as is
            compose_tiles(pixels, s.width, s.height, s.width, std::span<const TileSource>(content.sources.data(), content.n),
                          std::span<const Rect>(content.tiles.data(), content.n), 0xff000000u);
            buffer->content = content;
        }
    } else {
        buffer->content.valid = false;
        render_animated_pattern(pixels, s.width, s.height, s.width, s.cadence.phase());
    }
    if (s.cadence.finished(monotonic_ms())) return {};
    buffer->busy = true;
    wl_surface_attach(s.surface, buffer->proxy, 0, 0);
    wl_surface_damage_buffer(s.surface, 0, 0, static_cast<int>(s.width), static_cast<int>(s.height));
    s.frame = wl_surface_frame(s.surface); wl_callback_add_listener(s.frame, &Impl::frame_listener, &s);
    // Empty configure/policy commit is not content; only this commit is counted.
    // A full feedback queue backpressures above rather than dropping requests.
    if (s.presentation) {
        auto slot = std::find_if(s.feedbacks.begin(), s.feedbacks.end(), [](const auto& f) { return !f.proxy; });
        if (slot == s.feedbacks.end()) return Error{"feedback", "feedback capacity invariant failed"};
        auto* proxy = wp_presentation_feedback(s.presentation, s.surface);
        if (!proxy) return Error{"feedback", "feedback allocation failed"};
        *slot = {&s, proxy, *s.metrics.submit(), false, false};
        wp_presentation_feedback_add_listener(proxy, &Impl::feedback_listener, &*slot);
    } else s.metrics.submit();
    wl_surface_commit(s.surface); s.cadence.committed();
    if (wl_display_flush(s.display) < 0 && errno != EAGAIN) return Error{"flush", "connection failed"};
    return {};
}
}
