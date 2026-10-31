#include "xr/presentation.hpp"
#include <wayland-client.h>
#include "presentation-time-client-protocol.h"
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
    bool configured = false, closed = false, dirty = true;
    std::vector<std::uint32_t> required_globals;

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
        } else if (std::strcmp(interface, "wl_output") == 0 && version >= 4) {
            auto output = std::make_unique<Output>(); output->global = id;
            output->proxy = static_cast<wl_output*>(wl_registry_bind(registry, id, &wl_output_interface, 4));
            wl_output_add_listener(output->proxy, &output_listener, output.get());
            s.outputs.push_back(std::move(output));
        }
        // Deliberately never bind wl_seat, virtual input, capture, or SDK globals.
    }
    static void removed(void* data, wl_registry*, uint32_t id) {
        auto& s = *static_cast<Impl*>(data);
        if ((s.selected && s.selected->global == id) ||
            std::find(s.required_globals.begin(), s.required_globals.end(), id) != s.required_globals.end()) s.closed = true;
        for (auto& output : s.outputs) if (output->global == id) output->name.clear();
    }
    inline static const wl_registry_listener registry_listener{global, removed};
    static void configure(void* data, zwlr_layer_surface_v1* layer, uint32_t serial, uint32_t w, uint32_t h) {
        auto& s = *static_cast<Impl*>(data);
        zwlr_layer_surface_v1_ack_configure(layer, serial);
        if (!valid_surface_size(w, h)) { s.closed = true; return; }
        s.width = w; s.height = h; s.configured = true; s.dirty = true;
    }
    static void layer_closed(void* data, zwlr_layer_surface_v1*) { static_cast<Impl*>(data)->closed = true; }
    inline static const zwlr_layer_surface_v1_listener layer_listener{configure, layer_closed};
    static void frame_done(void* data, wl_callback* callback, uint32_t) {
        auto& s = *static_cast<Impl*>(data); wl_callback_destroy(callback); s.frame = nullptr;
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
        cancel_feedback();
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
    if (impl_) { impl_->cancel_feedback(); final_metrics_ = impl_->metrics; impl_.reset(); }
}
std::string Presentation::timing_report() const {
    return (impl_ ? impl_->metrics : final_metrics_).report();
}
Status Presentation::open(std::string output_name) {
    if (!std::regex_match(output_name, std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) return Error{"output", "invalid output name"};
    if (impl_) return Error{"state", "already open"};
    auto s = std::make_unique<Impl>();
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
    impl_ = std::move(s); return {};
}
Status Presentation::dispatch(int timeout_ms) {
    if (timeout_ms < 0 || timeout_ms > 100 || !impl_ || !impl_->pump(timeout_ms)) return Error{"dispatch", "closed, removed or disconnected"};
    return {};
}
Status Presentation::draw_test_pattern() {
    if (!impl_ || !impl_->configured || impl_->closed) return Error{"state", "surface not configured"};
    auto& s = *impl_;
    if (s.frame || !s.dirty || !s.metrics.can_submit()) return {};
    std::erase_if(s.buffers, [&](const auto& b) { return !b->busy && (b->width != s.width || b->height != s.height); });
    Impl::Buffer* buffer = nullptr;
    for (auto& b : s.buffers) if (!b->busy) { buffer = b.get(); break; }
    if (!buffer) {
        if (s.buffers.size() >= 3) return {}; // Wait for release; never reuse busy memory.
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
    for (std::uint32_t y = 0; y < s.height; ++y)
        for (std::uint32_t x = 0; x < s.width; ++x) pixels[std::size_t(y) * s.width + x] = pattern_pixel(x, y, s.width, s.height);
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
    wl_surface_commit(s.surface); s.dirty = false;
    if (wl_display_flush(s.display) < 0 && errno != EAGAIN) return Error{"flush", "connection failed"};
    return {};
}
}
