#include "xr/presentation.hpp"
#include <wayland-client.h>
#include "presentation-time-client-protocol.h"
#include "ext-image-capture-source-v1-client-protocol.h"
#include "ext-image-copy-capture-v1-client-protocol.h"
#include "linux-dmabuf-v1-client-protocol.h"
#include "dmabuf.hpp"
#include "xr/capture.hpp"
#include "gl.hpp"
#include "xr/view.hpp"
#include "xr/pose.hpp"
#include "xr/hud.hpp"
#include <cmath>
// Pinned upstream XML names this C parameter `namespace`. Keep the vendored XML
// byte-identical; scope the C++ spelling adaptation to the generated C header.
#define namespace layer_namespace
#include "wlr-layer-shell-unstable-v1-client-protocol.h"
#undef namespace
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <poll.h>
#include <regex>
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
        wp_presentation_feedback_destroy(proxy); f = {};
    }
    static void discarded(void* data, struct wp_presentation_feedback* proxy) {
        auto& f = *static_cast<Feedback*>(data);
        f.owner->metrics.discarded(f.ticket);
        wp_presentation_feedback_destroy(proxy); f = {};
    }
    inline static const wp_presentation_feedback_listener feedback_listener{sync_output, presented, discarded};
    void cancel_feedback() {
        for (auto& f : feedbacks) if (f.proxy) { wp_presentation_feedback_destroy(f.proxy); f = {}; }
        metrics.abandon();
    }
    struct Output { wl_output* proxy = nullptr; std::uint32_t global = 0; std::string name; };
    std::vector<std::unique_ptr<Output>> outputs;
    Output* selected = nullptr;
    struct Buffer {
        wl_buffer* proxy = nullptr;
        void* memory = MAP_FAILED;
        std::size_t bytes = 0;
        std::uint32_t width = 0, height = 0;
        bool busy = false;
        ~Buffer() { if (proxy) wl_buffer_destroy(proxy); if (memory != MAP_FAILED) munmap(memory, bytes); }
    };
    std::vector<std::unique_ptr<Buffer>> buffers;
    std::uint32_t width = 0, height = 0;
    bool configured = false, closed = false;
    PatternCadence cadence;
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
        std::optional<ShmPlan> plan;
        std::array<std::unique_ptr<Buffer>, 2> slots{};
        int capturing = -1, ready = -1;
        bool stopped = false;
        std::uint64_t frames = 0, failures = 0, last_ready_ms = 0;
        // Zero-copy path (C2b): three GBM buffers imported into GL once; the compositor renders
        // into the slot that is neither the latest frame nor the one GL is sampling.
        enum class Path { None, Shm, Dmabuf } path = Path::None;
        std::optional<SessionConstraints::Batch> batch;
        bool realloc_pending = false, no_dmabuf = false, dmabuf_failed = false;
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
    std::unique_ptr<GlCompositor> gl;     // GPU composition when available (C2a)
    std::string gl_fallback;              // non-empty: why the CPU blit is in use
    std::vector<std::uint64_t> uploaded;  // per capture: frame counter last uploaded
    ViewAnimator view{220.f};
    // Head tracking (XRLinuxDriver feed); absent/stale feed holds the last pose, never blocks.
    bool tracking_requested = false;
    std::string pose_prefix;
    std::unique_ptr<PoseFeed> pose_feed;
    std::string pose_why;
    std::uint64_t pose_retry_ms = 0;
    PoseState pose;
    ModeController modes;  // T2: anchor / locked (0DoF) / follow
    StatusPopups popups;   // viewing mode / cursor follow pop-ups (GPU composition only)
    bool popup_drawn = false;  // last frame showed a pop-up: draw once more so it clears
    HelpOverlay help;          // keyboard help card (Ctrl+Alt+H)
    bool help_drawn = false;   // last frame showed the help card: draw once more so it clears
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
        const bool changed = !c.plan || c.plan->width != batch.shm->width || c.plan->height != batch.shm->height ||
                             c.plan->format != batch.shm->format;
        c.batch = std::move(batch);
        if (changed) {
            if (c.capturing >= 0) { c.realloc_pending = true; return; }  // after the in-flight frame completes or fails
            c.owner->apply_batch(c); return;
        }
        c.owner->capture_next(c);
    }
    void apply_batch(Capture& c) {
        c.realloc_pending = false; c.plan = c.batch->shm;
        if (allocation_enabled) allocate(c);
    }
    // (Re)allocate capture targets: dmabuf when everything lines up, otherwise SHM with a reason.
    void allocate(Capture& c) {
        c.ready = c.presented = -1; c.path = Capture::Path::None;
        if (gl) gl->drop_dmabuf(c.index);  // GL lets go of the images before their buffers die
        c.drop_dmabuf();
        for (auto& slot : c.slots) slot.reset();
        if (try_dmabuf(c)) return;  // capture starts once the compositor has created all buffers
        c.drop_dmabuf();
        if (gl) gl->drop_dmabuf(c.index);
        const auto& plan = *c.plan;
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
            std::string why; allocator.reset();
            allocator = DmabufAllocator::open(*c.batch->device, why);
            if (!allocator) { c.no_dmabuf = true; c.fallback = why; return false; }
            allocator_device = *c.batch->device;
        }
        auto choice = choose_dmabuf(c.batch->dmabuf, [&](std::uint32_t f, std::uint64_t m) { return gl->dmabuf_importable(f, m); });
        if (!choice) { c.no_dmabuf = true; return fail("no offered dmabuf format is importable"); }
        c.choice = *choice;
        const auto w = c.plan->width, h = c.plan->height;
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
    static void cap_stopped(void* data, ext_image_copy_capture_session_v1*) {
        auto& c = *static_cast<Capture*>(data); c.stopped = true; c.owner->closed = true;
    }
    inline static const ext_image_copy_capture_session_v1_listener session_listener{
        cap_buffer_size, cap_shm_format, cap_dmabuf_device, cap_dmabuf_format, cap_done, cap_stopped};
    static void frame_transform(void*, ext_image_copy_capture_frame_v1*, uint32_t) {}
    static void frame_damage(void*, ext_image_copy_capture_frame_v1*, int32_t, int32_t, int32_t, int32_t) {}
    static void frame_time(void*, ext_image_copy_capture_frame_v1*, uint32_t, uint32_t, uint32_t) {}
    static void frame_ready(void* data, ext_image_copy_capture_frame_v1* frame) {
        auto& c = *static_cast<Capture*>(data);
        ext_image_copy_capture_frame_v1_destroy(frame); c.frame = nullptr;
        c.ready = c.capturing; c.capturing = -1; ++c.frames; c.last_ready_ms = monotonic_ms();
        if (c.path == Capture::Path::Dmabuf) ++c.dmabuf_frames;
        c.owner->cadence.invalidate();
        if (c.realloc_pending) { c.owner->apply_batch(c); return; }
        c.owner->capture_next(c);
    }
    static void frame_failed(void* data, ext_image_copy_capture_frame_v1* frame, uint32_t reason) {
        auto& c = *static_cast<Capture*>(data);
        ext_image_copy_capture_frame_v1_destroy(frame); c.frame = nullptr; c.capturing = -1; ++c.failures;
        if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_STOPPED) { c.stopped = true; c.owner->closed = true; return; }
        if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS) {
            if (c.realloc_pending) { c.owner->apply_batch(c); return; }  // new constraints already arrived
            if (c.path == Capture::Path::Dmabuf && !c.dmabuf_frames) {   // refused before any frame: use SHM
                c.no_dmabuf = true; c.fallback = "compositor refused dmabuf capture"; c.owner->allocate(c); return;
            }
            c.plan.reset(); return;  // wait for done
        }
        if (c.failures > 1000) { c.stopped = true; c.owner->closed = true; return; }
        c.owner->capture_next(c);
    }
    inline static const ext_image_copy_capture_frame_v1_listener capture_frame_listener{
        frame_transform, frame_damage, frame_time, frame_ready, frame_failed};
    std::unique_ptr<Buffer> make_shm(std::uint32_t w, std::uint32_t h, std::uint32_t stride, std::uint32_t format, const char* tag) {
        auto b = std::make_unique<Buffer>(); b->width = w; b->height = h; b->bytes = std::size_t(stride) * h;
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
        if (c.stopped || c.frame || !c.plan) return;
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
        ext_image_copy_capture_frame_v1_damage_buffer(c.frame, 0, 0, static_cast<int>(c.plan->width), static_cast<int>(c.plan->height));
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
        for (auto& c : s.captures) if (c->output && c->output->global == id) s.closed = true;  // captured output gone
        for (auto& output : s.outputs) if (output->global == id) output->name.clear();
    }
    inline static const wl_registry_listener registry_listener{global, removed};
    static void configure(void* data, zwlr_layer_surface_v1* layer, uint32_t serial, uint32_t w, uint32_t h) {
        auto& s = *static_cast<Impl*>(data);
        zwlr_layer_surface_v1_ack_configure(layer, serial);
        if (!valid_surface_size(w, h)) { s.closed = true; return; }
        s.width = w; s.height = h; s.configured = true; s.cadence.invalidate();
        if (s.gl) s.gl->resize(w, h);
        s.half_w = 0;  // recompute FOV split for the new aspect
    }
    static void layer_closed(void* data, zwlr_layer_surface_v1*) { static_cast<Impl*>(data)->closed = true; }
    inline static const zwlr_layer_surface_v1_listener layer_listener{configure, layer_closed};
    static void frame_done(void* data, wl_callback* callback, uint32_t) {
        auto& s = *static_cast<Impl*>(data); wl_callback_destroy(callback); s.frame = nullptr; s.cadence.frame_done();
        const double now = double(monotonic_ms());
        if (s.view.animating() || s.tracking_requested || s.popup_drawn || s.popups.active(now) || s.help.animating(now) ||
            (s.help_drawn && !s.help.visible()))
            s.cadence.invalidate();  // animate / follow the head / fade a pop-up or the help card every frame
    }
    inline static const wl_callback_listener frame_listener{frame_done};
    static void released(void* data, wl_buffer*) { static_cast<Buffer*>(data)->busy = false; }
    inline static const wl_buffer_listener buffer_listener{released};
    static void sync_done(void* data, wl_callback*, uint32_t) { *static_cast<bool*>(data) = true; }
    inline static const wl_callback_listener sync_listener{sync_done};
    bool pump(int timeout) {
        if (!display || closed) return false;
        while (wl_display_prepare_read(display) != 0)
            if (wl_display_dispatch_pending(display) < 0) return false;
        const int flushed = wl_display_flush(display);
        if (flushed < 0 && errno != EAGAIN) { wl_display_cancel_read(display); return false; }
        pollfd fd{wl_display_get_fd(display), static_cast<short>(POLLIN | (flushed < 0 ? POLLOUT : 0)), 0};
        int result = poll(&fd, 1, timeout);
        if (result < 0 && errno == EINTR) { wl_display_cancel_read(display); return true; }
        if (result < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) { wl_display_cancel_read(display); return false; }
        if (fd.revents & POLLIN) {
            if (wl_display_read_events(display) < 0) return false;
        } else wl_display_cancel_read(display);
        return wl_display_dispatch_pending(display) >= 0 && !closed;
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
        gl.reset();  // before the wl_surface it wraps and the dmabufs it imported
        captures.clear();
        allocator.reset();  // after every GBM buffer
        if (linux_dmabuf) zwp_linux_dmabuf_v1_destroy(linux_dmabuf);
        if (copy_manager) ext_image_copy_capture_manager_v1_destroy(copy_manager);
        if (source_manager) ext_output_image_capture_source_manager_v1_destroy(source_manager);
        if (presentation) wp_presentation_destroy(presentation);
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
std::string Presentation::timing_report() const {
    return (impl_ ? impl_->metrics : final_metrics_).report();
}
std::string Presentation::capture_report() const {
    if (!impl_) return final_capture_;
    std::string out = "{\"kind\":\"managed_output_capture\",\"outputs\":[";
    for (std::size_t i = 0; i < impl_->captures.size(); ++i) {
        const auto& c = *impl_->captures[i];
        out += (i ? "," : "") + std::string("{\"name\":\"") + c.name + "\",\"frames\":" + std::to_string(c.frames) +
               ",\"failures\":" + std::to_string(c.failures) + ",\"width\":" + std::to_string(c.plan ? c.plan->width : 0) +
               ",\"height\":" + std::to_string(c.plan ? c.plan->height : 0) + ",\"format\":" + std::to_string(c.plan ? c.plan->format : 0) +
               ",\"path\":\"" + (c.path == Impl::Capture::Path::Dmabuf ? "dmabuf" : c.path == Impl::Capture::Path::Shm ? "shm" : "none") +
               "\",\"dmabuf_frames\":" + std::to_string(c.dmabuf_frames) +
               (c.path == Impl::Capture::Path::Dmabuf ? ",\"fourcc\":" + std::to_string(c.choice.fourcc) + ",\"modifier\":" + std::to_string(c.choice.modifier) : std::string()) +
               ",\"fallback\":\"" + c.fallback + "\",\"stopped\":" + (c.stopped ? "true" : "false") + "}";
    }
    const auto mode = impl_->modes.mode();
    out += "],\"tracking\":{\"requested\":" + std::string(impl_->tracking_requested ? "true" : "false") +
           ",\"mode\":\"" + (mode == ViewMode::locked ? "locked" : mode == ViewMode::follow ? "follow" : "anchor") + "\"" +
           ",\"feed\":" + (impl_->pose_feed ? "true" : "false") + ",\"tracking\":" + (impl_->pose.tracking() ? "true" : "false") +
           ",\"samples\":" + std::to_string(impl_->pose.samples()) + ",\"lost_events\":" + std::to_string(impl_->pose.lost_events()) +
           ",\"diagonal_fov_deg\":" + std::to_string(impl_->fov_deg) + ",\"fov_source\":\"" +
           (impl_->fov_from_driver ? "driver" : "fallback-46") + "\",\"half_fov_tan\":[" + std::to_string(impl_->half_w) + "," +
           std::to_string(impl_->half_h) + "]}";
    out += ",\"view\":{\"selected\":" + std::to_string(impl_->view.selected()) + ",\"zoom\":" + std::to_string(impl_->view.zoom()) +
           ",\"spacing\":" + std::to_string(impl_->view.spacing()) + "}";
    std::string font;  // bounded, JSON-escaped (font paths come from fontconfig)
    for (char c : impl_->popup_font.substr(0, 256)) {
        if (c == '"' || c == '\\') font += '\\';
        font += static_cast<unsigned char>(c) < 0x20 ? '?' : c;
    }
    out += ",\"popup_font\":\"" + font + "\"";
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
            c->source = ext_output_image_capture_source_manager_v1_create_source(s->source_manager, found->proxy);
            c->session = ext_image_copy_capture_manager_v1_create_session(s->copy_manager, c->source,
                         EXT_IMAGE_COPY_CAPTURE_MANAGER_V1_OPTIONS_PAINT_CURSORS);  // pointer visible on the managed desktop
            ext_image_copy_capture_session_v1_add_listener(c->session, &Impl::session_listener, c.get());
            s->captures.push_back(std::move(c));
        }
        // Constraints arrive asynchronously (after the output renders); bounded wait for every session.
        auto ready = [&] { return std::all_of(s->captures.begin(), s->captures.end(), [](const auto& c) { return c->plan || c->stopped; }); };
        auto capture_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!ready() && std::chrono::steady_clock::now() < capture_deadline) if (!s->pump(50)) break;
        std::string missing;
        for (auto& c : s->captures) if (c->stopped || !c->plan) missing += (missing.empty() ? "" : ",") + c->name;
        if (!missing.empty()) return Error{"capture", "capture constraints unavailable for " + missing};
    }
    s->surface = wl_compositor_create_surface(s->compositor);
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
        if (!s->gl && s->gl_fallback.empty()) s->gl_fallback = "unknown";
        if (s->gl) {  // pop-up text font; the built-in pixel font is the reported fallback
            std::string why; auto font = HudFont::load(kHudFontFamily, why);
            s->popup_font = font ? font->description() : "pixel-fallback: " + why;
            s->popups.set_font(std::move(font));
            std::string help_why; s->help.set_font(HudFont::load(kHudFontFamily, help_why));  // same family, own instance
        }
        s->uploaded.assign(s->captures.size(), 0);
        s->allocation_enabled = true;
        for (auto& c : s->captures) if (!c->stopped && c->plan && c->capturing < 0) s->allocate(*c);
        if (s->closed) return Error{"capture", "capture buffer allocation failed"};
    }
    impl_ = std::move(s); return {};
}
Status Presentation::dispatch(int timeout_ms) {
    if (timeout_ms < 0 || timeout_ms > 100 || !impl_ || !impl_->pump(timeout_ms)) return Error{"dispatch", "closed, removed or disconnected"};
    return {};
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
    if (impl_ && impl_->gl) { impl_->gl->set_accent(r, g, b); impl_->cadence.invalidate(); }
}
void Presentation::set_help(bool visible, const std::vector<std::pair<std::string, std::string>>& rows) {
    if (!impl_) return;
    std::vector<HelpRow> help;
    for (const auto& [keys, action] : rows) help.push_back({keys, action});
    if (impl_->help.set(visible, help, double(monotonic_ms()))) impl_->cadence.invalidate();
}
void Presentation::recenter() { if (impl_) { impl_->pose.recenter(); impl_->modes.recenter(); } }
void Presentation::set_view(bool zoomed, std::uint32_t selected, std::uint32_t spacing, std::uint8_t mode, bool cursor_follow) {
    if (!impl_) return;
    impl_->popups.update(mode, cursor_follow, double(monotonic_ms()));
    impl_->modes.set_mode(mode == 1 ? ViewMode::locked : mode == 2 ? ViewMode::follow : ViewMode::anchor, double(monotonic_ms()));
    impl_->view.set_target(zoomed, std::min<std::uint32_t>(selected, impl_->captures.empty() ? 0 : std::uint32_t(impl_->captures.size() - 1)),
                           spacing, double(monotonic_ms()));
    impl_->cadence.invalidate();
}
bool Presentation::finished() const { return impl_ && impl_->cadence.finished(monotonic_ms()); }
Status Presentation::draw_test_pattern() {
    if (!impl_ || !impl_->configured || impl_->closed) return Error{"state", "surface not configured"};
    auto& s = *impl_;
    if (!s.cadence.ready(monotonic_ms(), true, s.metrics.can_submit())) return {};
    if (s.gl) {
        for (std::size_t i = 0; i < s.captures.size(); ++i) {
            auto& c = *s.captures[i];
            if (c.ready < 0 || c.frames == s.uploaded[i]) continue;
            if (c.path == Impl::Capture::Path::Dmabuf) {  // zero copy: sample the imported slot directly
                s.gl->present_dmabuf(i, c.ready); c.presented = c.ready; s.uploaded[i] = c.frames; continue;
            }
            if (!c.slots[c.ready]) continue;
            if (!s.gl->upload(i, c.slots[c.ready]->memory, c.plan->width, c.plan->height, c.plan->stride))
                return Error{"gl", "texture upload failed"};
            s.uploaded[i] = c.frames;
        }
        const auto& first = s.captures.front()->plan;
        s.view.advance(double(monotonic_ms()));
        Mat3 head{1, 0, 0, 0, 1, 0, 0, 0, 1};
        if (s.tracking_requested) {  // late sample: just before drawing
            const auto now = monotonic_ms();
            if (!s.pose_feed && now >= s.pose_retry_ms) {
                s.pose_feed = PoseFeed::open(s.pose_prefix, s.pose_why); s.pose_retry_ms = now + 1000;
                if (s.pose_feed) s.half_w = 0;  // pick up the driver's FOV
            }
            s.pose.update(s.pose_feed ? s.pose_feed->read() : std::nullopt, double(now));
            const Quat relative = s.pose.relative();
            head = view_from(relative, s.modes.update(relative, double(now)));
        }
        if (!s.half_w) s.update_fov();
        auto tiles = blended_layout(s.captures.size(), float(s.width), float(s.height), float(first ? first->width : 1920),
                                    float(first ? first->height : 1080), s.view.spacing(), s.view.zoom(), s.view.position());
        s.frame = wl_surface_frame(s.surface); wl_callback_add_listener(s.frame, &Impl::frame_listener, &s);
        if (s.presentation) {
            auto slot = std::find_if(s.feedbacks.begin(), s.feedbacks.end(), [](const auto& f) { return !f.proxy; });
            if (slot == s.feedbacks.end()) return Error{"feedback", "feedback capacity invariant failed"};
            auto* proxy = wp_presentation_feedback(s.presentation, s.surface);
            if (!proxy) return Error{"feedback", "feedback allocation failed"};
            *slot = {&s, proxy, *s.metrics.submit(), false, false};
            wp_presentation_feedback_add_listener(proxy, &Impl::feedback_listener, &*slot);
        } else s.metrics.submit();
        const float popup = s.popups.alpha(double(monotonic_ms()));
        s.gl->set_popup(popup > 0.f ? &s.popups.image() : nullptr, s.popups.generation(), popup);
        s.popup_drawn = popup > 0.f;
        const double help_now = double(monotonic_ms());
        const float help = s.help.alpha(help_now);
        s.gl->set_help(help > 0.f ? &s.help.image() : nullptr, s.help.generation(), help, s.help.offset_px(help_now));
        s.help_drawn = help > 0.f;
        if (!s.gl->draw_and_swap(tiles, int(s.view.selected()), 1.f - s.view.zoom(), head, s.half_w, s.half_h))
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
        b->bytes = std::size_t(s.width) * s.height * 4;
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
        std::fill(pixels, pixels + std::size_t(s.width) * s.height, 0xff000000u);
        const auto& first = s.captures.front()->plan;
        auto tiles = side_by_side(s.captures.size(), s.width, s.height, first ? first->width : 1920, first ? first->height : 1080);
        for (std::size_t i = 0; i < s.captures.size() && i < tiles.size(); ++i) {
            auto& c = *s.captures[i];
            if (c.ready < 0 || !c.slots[c.ready]) continue;  // not yet captured: stays black
            blit_nearest(static_cast<const std::uint32_t*>(c.slots[c.ready]->memory), c.plan->width, c.plan->height, c.plan->stride / 4,
                         pixels, s.width, s.height, s.width, tiles[i]);
        }
    } else {
        for (std::uint32_t y = 0; y < s.height; ++y)
            for (std::uint32_t x = 0; x < s.width; ++x) pixels[std::size_t(y) * s.width + x] = animated_pattern_pixel(x, y, s.width, s.height, s.cadence.phase());
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
