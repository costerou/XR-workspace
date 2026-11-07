#include <nlohmann/json.hpp>
#include "xr/ipc.hpp"
#include "xr/presentation.hpp"
#include <sys/stat.h>
#include <fcntl.h>
#include "xr/capture.hpp"
#include <cerrno>
#include <cstring>
#include <chrono>
#include <time.h>
#include <charconv>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <map>
#include <poll.h>
#include <string>
#include <vector>
#include <sys/socket.h>
#include <deque>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t stopping = 0;
void stop(int) { stopping = 1; }
bool report(const xr::Status& status) {
    if (auto* error = std::get_if<xr::Error>(&status)) { std::cerr << error->code << ": " << error->detail << '\n'; return false; }
    return true;
}
int number(const std::string& value) {
    int n = -1; auto r = std::from_chars(value.data(), value.data() + value.size(), n);
    if (r.ec != std::errc{} || r.ptr != value.data() + value.size() || n < 3) return -1;
    return n;
}
}
int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--help") {
        std::cout << "xr-native-spike: native output-bound layer-shell renderer (no SDK/input; capture only of managed outputs).\n"
                     "Launched only by the permission-gated Python supervisor with inherited capability sockets.\n"
                     "Internal: --supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION\n"
                     "Optional --status-fd FD: nonblocking measured status JSON, at most once per second.\n"
                     "Optional --depth-fd FD: the depth engine's shared memory, for immersive 3D (src/depth_link.hpp).\n"
                     "Presentation-only: python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE.\n"
                     "Managed output ownership qualification is currently BLOCKED. No 120 Hz claim.\n"
                     "Optional presentation-time v1 feedback: bounded aggregate JSON on stderr at orderly exit/error.\n"
                     "Optional --control-errors fatal, for tests: a bad control frame stops the renderer. Otherwise it is logged and dropped.\n"
                     "Default static; optional --animate-pattern-ms 1..60000 (exact matching consent required).\n"
                     "Or --capture XR-1[,XR-2[,XR-3[,XR-4]]]: show only the app's own managed outputs (managed-output capture consent),\n"
                     "  optionally with --pose-feed xrld: world-anchored 3DoF from the network-blocked XRLinuxDriver feed.\n"
                     "Generated SHM only; callback-paced redraw is not scanout or production GPU evidence.\n";
        return 0;
    }
    std::map<std::string, std::string> args;
    if (argc != 11 && argc != 13 && argc != 15 && argc != 17 && argc != 19 && argc != 21 && argc != 23) { std::cerr << "permission: supervised capability sockets required; see --help\n"; return 2; }
    for (int i = 1; i < argc; i += 2) if (!args.emplace(argv[i], argv[i + 1]).second) return 2;
    for (const char* key : {"--supervised-fd", "--wayland-fd", "--output", "--run-id", "--session"}) if (!args.contains(key)) return 2;
    std::uint32_t animation_ms = 0;
    std::vector<std::string> capture;
    bool pose_feed = false;
    // Optional modes: generated animation alone; or capture, optionally with the XRLinuxDriver pose feed.
    const int status_fd = args.contains("--status-fd") ? number(args["--status-fd"]) : -1;
    if (args.contains("--status-fd") && status_fd < 0) return 2;
    const int continuity_fd = args.contains("--continuity-fd") ? number(args["--continuity-fd"]) : -1;
    if (args.contains("--continuity-fd")) {
        struct stat st{};
        if (continuity_fd < 0 || fstat(continuity_fd, &st) || !S_ISREG(st.st_mode)
            || st.st_uid != getuid() || st.st_size > 65536 || fcntl(continuity_fd, F_GET_SEALS) < 0) return 2;
    }
    const int depth_fd = args.contains("--depth-fd") ? number(args["--depth-fd"]) : -1;
    if (args.contains("--depth-fd") && depth_fd < 0) return 2;
    if (args.contains("--control-errors") && args["--control-errors"] != "fatal") return 2;
    const bool control_errors_fatal = args.contains("--control-errors");
    const std::size_t optional = std::size_t(argc - 11) / 2 - args.count("--status-fd") - args.count("--control-errors") - args.count("--depth-fd") - args.count("--continuity-fd");
    if (optional > 2) return 2;
    if (optional == 1 && args.contains("--animate-pattern-ms")) {
        auto duration = xr::parse_pattern_duration(args["--animate-pattern-ms"]);
        if (!duration) return 2;
        animation_ms = *duration;
    } else if (optional >= 1) {
        if (!args.contains("--capture")) return 2;
        auto names = xr::parse_capture_list(args["--capture"], args["--output"]);
        if (!names) return 2;
        capture = *names;
        if (optional == 2) {
            if (!args.contains("--pose-feed") || args["--pose-feed"] != "xrld") return 2;
            pose_feed = true;
        }
    }
    const int control = number(args["--supervised-fd"]), wayland = number(args["--wayland-fd"]);
    if (control < 0 || wayland < 0 || control == wayland) return 2;
    if (status_fd >= 0 && (status_fd == control || status_fd == wayland)) return 2;
    if (depth_fd >= 0 && (depth_fd == control || depth_fd == wayland || depth_fd == status_fd)) return 2;
    if (continuity_fd >= 0 && (continuity_fd == control || continuity_fd == wayland || continuity_fd == status_fd || continuity_fd == depth_fd)) return 2;
    for (int fd : {control, wayland, status_fd}) {
        if (fd < 0) continue;
        ucred peer{}; socklen_t size = sizeof(peer);
        if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) != 0 || peer.uid != getuid()) return 2;
    }
    // Startup is a validated Task 2 start frame on a private inherited socket,
    // never a public JSON permission grant. No paths or commands in this frame.
    std::string raw;
    pollfd input{control, POLLIN, 0};
    while (raw.size() < 16384) {
        if (poll(&input, 1, 250) <= 0) return 2;
        char c; if (recv(control, &c, 1, 0) != 1) return 2;
        raw += c; if (c == '\n') break;
    }
    auto decoded = xr::decode_control(raw);
    auto* message = std::get_if<xr::ControlMessage>(&decoded);
    if (!message || message->op != xr::ControlOp::start || message->run_id != args["--run-id"] || message->session != args["--session"]) return 2;
    setenv("WAYLAND_SOCKET", args["--wayland-fd"].c_str(), 1);
    std::signal(SIGTERM, stop); std::signal(SIGINT, stop);
    xr::Presentation presentation;
    int result = 0;
    if (!report(presentation.open(args["--output"], capture))) result = 1;
    if (!result && depth_fd >= 0) presentation.set_depth_fd(depth_fd);
    if (!result && pose_feed) presentation.enable_tracking("/tmp/shader_runtime_");  // XRLinuxDriver IPC (fixed path)
    if (!result && presentation.restore_continuity(continuity_fd)) std::cerr << "continuity: restored\n";
    if (!result && animation_ms && !report(presentation.animate_pattern(animation_ms))) result = 1;
    // VR video files arrive as SCM_RIGHTS descriptors with their `video` open frame (docs/vr-video.md). Each open frame
    // takes the oldest received one; at most two wait, extras are closed, and none is ever opened by path here.
    std::deque<int> video_fds;
    auto receive = [&](char* buffer, std::size_t size) -> ssize_t {
        alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int) * 4)];
        iovec io{buffer, size};
        msghdr msg{}; msg.msg_iov = &io; msg.msg_iovlen = 1; msg.msg_control = ancillary; msg.msg_controllen = sizeof(ancillary);
        const ssize_t n = recvmsg(control, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
        if (n < 0) return n;
        for (cmsghdr* c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
            if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS) continue;
            const std::size_t count = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
            for (std::size_t i = 0; i < count; ++i) {
                int fd; std::memcpy(&fd, CMSG_DATA(c) + i * sizeof(int), sizeof(int));
                if (video_fds.size() < 2) video_fds.push_back(fd); else ::close(fd);
            }
        }
        return n;
    };
    auto apply = [&](xr::ControlMessage* m) {
        if (m->op == xr::ControlOp::video) {  // VR video, display only (video_mode.hpp)
            const auto& v = std::get<xr::VideoPayload>(m->payload);
            switch (v.action) {
            case xr::VideoAction::open: {
                if (video_fds.empty()) { std::cerr << "control: video open without a file descriptor\n"; break; }
                const int fd = video_fds.front(); video_fds.pop_front();
                presentation.video_open(fd, v.format, v.title);
                break;
            }
            case xr::VideoAction::close: presentation.video_close(); break;
            case xr::VideoAction::pause: presentation.video_pause(); break;
            case xr::VideoAction::seek: presentation.video_seek(v.seconds); break;
            case xr::VideoAction::format: presentation.video_format(v.format); break;
            case xr::VideoAction::controls: presentation.video_controls(v.visible); break;
            case xr::VideoAction::quality: presentation.video_quality(v.quality); break;
            case xr::VideoAction::menu: presentation.video_menu(v.visible, v.rows, v.selected); break;
            }
            return;
        }
        if (m->op == xr::ControlOp::stop) stopping = 1;
        else if (m->op == xr::ControlOp::recenter) presentation.recenter();
        else if (m->op == xr::ControlOp::style) {
            const auto& c = std::get<xr::StylePayload>(m->payload);
            presentation.set_accent(c.r, c.g, c.b);
        }
        else if (m->op == xr::ControlOp::help) {
            const auto& h = std::get<xr::HelpPayload>(m->payload);
            presentation.set_help(h.visible, h.rows, h.sections, h.turn);
        }
        else if (m->op == xr::ControlOp::binds) {  // display only; malformed payloads are dropped above
            if (const auto* b = std::get_if<xr::BindPanelState>(&m->payload)) presentation.set_binds(*b);
        }
        else if (m->op == xr::ControlOp::options_menu) {
            if(const auto* menu=std::get_if<xr::OptionsMenuState>(&m->payload)) presentation.set_options_menu(*menu);
        }
        else if (m->op == xr::ControlOp::placement_menu) {
            // Display only; malformed payloads never get here (dropped above, see ipc.hpp).
            if (const auto* menu = std::get_if<xr::PlacementMenuPayload>(&m->payload)) {
                xr::PlacementMenuState state;
                state.open = menu->open; state.desktop = menu->desktop; state.field = menu->field;
                for (std::size_t i = 0; i < state.placements.size(); ++i) {
                    const auto& w = menu->placements[i];
                    state.placements[i] = {w.x, w.y, w.distance, w.curvature};
                }
                state.count = menu->count; state.numbers = menu->numbers;
                state.wide = menu->wide; state.armed = menu->armed; state.busy = menu->busy;
                state.scale = menu->scale; state.scales = menu->scales; state.picker = menu->picker;
                state.resolution_w = menu->resolution_w; state.resolution_h = menu->resolution_h;
                state.resolution_picker = menu->resolution_picker;
                state.background = menu->background; state.brightness = menu->brightness;
                state.background_picker = menu->background_picker;
                std::optional<std::array<std::int32_t, 4>> surface;
                if (const auto& w = menu->surface)  // a held slider: the panel's drawing surface
                    surface = std::array<std::int32_t, 4>{w->x, w->y, w->distance, w->curvature};
                presentation.set_placement_menu(state, surface);
            }
        }
        else if (m->op == xr::ControlOp::resize_handle) {  // display only; malformed ones were dropped above
            if (const auto* h = std::get_if<xr::ResizeHandlePayload>(&m->payload))
                presentation.set_resize_handle(h->visible, h->desktop, h->corner, h->active);
        }
        else if (m->op == xr::ControlOp::overview_pointer) {  // display only; malformed ones were dropped above
            if (const auto* p = std::get_if<xr::OverviewPointerPayload>(&m->payload))
                presentation.set_overview_pointer(p->visible, p->x, p->y, p->held, p->directed, p->az, p->el,
                                                 p->drag ? p->ghost_w : 0, p->drag ? p->ghost_h : 0, p->drag ? p->target : -1, p->look);
        }
        else if (m->op == xr::ControlOp::edge_pan) {  // display only; malformed ones were dropped above
            if (const auto* p = std::get_if<xr::EdgePan>(&m->payload)) presentation.set_edge_pan(*p);
        }
        else if (m->op == xr::ControlOp::roll) {  // display and levelling roll; malformed ones were dropped above
            if (const auto* r = std::get_if<xr::RollPayload>(&m->payload))
                presentation.set_roll(r->roll, r->bars, r->hover, r->side, r->active);
        }
        else if (m->op == xr::ControlOp::immersive) {  // 3D; malformed ones were dropped above
            if (const auto* i = std::get_if<xr::ImmersivePayload>(&m->payload)) presentation.set_immersive(i->desktops, i->global, i->stereo, i->hover, i->tail, i->params);
        }
        else if (m->op == xr::ControlOp::restore_band) {  // display only; malformed ones were dropped above
            if (const auto* b = std::get_if<xr::RestoreBandPayload>(&m->payload)) presentation.set_restore_band(b->open);
        }
        else if (m->op == xr::ControlOp::move_handle) {  // display only; malformed ones were dropped above
            if (const auto* g = std::get_if<xr::MoveHandlePayload>(&m->payload))
                presentation.set_move_handle(g->visible, g->desktop, g->hover, g->active);
        }
        else if (m->op == xr::ControlOp::environment) {  // the background; malformed ones were dropped above
            if (const auto* e = std::get_if<xr::EnvironmentPayload>(&m->payload)) presentation.set_environment(e->background, e->brightness);
        }
        else if (m->op == xr::ControlOp::desktops) {  // a pill's Add / Remove / Ultrawide (desktops.py)
            const auto& d = std::get<xr::DesktopsPayload>(m->payload);
            if (d.ease) presentation.ease_tiles();  // Ctrl+Alt+R: before the captures change
            presentation.set_desktops(d.outputs, d.renew);
        }
        else if (m->op == xr::ControlOp::placement) {
            std::vector<std::array<std::int32_t, 4>> placements;
            const auto& payload = std::get<xr::PlacementPayload>(m->payload);
            for (const auto& w : payload.desktops)
                placements.push_back({w.x, w.y, w.distance, w.curvature});
            presentation.set_placements(placements, payload.glide_ms);
        }
        else if (m->op == xr::ControlOp::view) {
            const auto& v = std::get<xr::ViewPayload>(m->payload);
            presentation.set_view(v.zoomed, v.selected, v.spacing, v.mode, v.cursor_follow, v.prediction, v.typing, v.center, v.cut, v.pan, v.stabilization);
        }
    };
    // Further supervisor frames (view, stop) on the same private socket, replay-protected. A bad one is dropped (ipc.hpp).
    std::uint64_t refusals = 0;
    xr::ControlStream stream(args["--run-id"], args["--session"], control_errors_fatal, [&](xr::ControlMessage& m) {
        apply(&m);
        if (status_fd >= 0) {
            const std::string receipt="{\"control_applied\":"+std::to_string(m.request_id)+"}\n";
            (void)send(status_fd,receipt.data(),receipt.size(),MSG_NOSIGNAL|MSG_DONTWAIT);
        }
    },
                             [&](const xr::ControlRefusal& r) {
        if(status_fd>=0){const std::string line=nlohmann::json{{"control_refused",{{"request_id",r.request_id},{"op",r.op.substr(0,64)},{"detail",r.error.detail.substr(0,256)}}}}.dump()+"\n";(void)send(status_fd,line.data(),line.size(),MSG_NOSIGNAL|MSG_DONTWAIT);}

        if (++refusals <= 20 || refusals % 1000 == 0)  // bounded: a supervisor that keeps doing this must not fill the journal
            std::cerr << "control: refused frame " << r.request_id << " (" << (r.op.empty() ? "unreadable" : r.op) << "): " << r.error.code
                      << ": " << r.error.detail << " (" << refusals << " so far): " << r.frame << '\n';
        // A video open brings its descriptor, and only its own open takes one: left here, the next open would play this file.
        if ((r.op == "video" || r.op.empty()) && !video_fds.empty()) { ::close(video_fds.front()); video_fds.pop_front(); }
    });
    stream.start(raw);  // the validated start frame sets the replay floor
    std::string closed = "EOF";  // why the socket ended, for the log
    auto read_control = [&]() -> bool {
        char buffer[4096];
        for (;;) {
            const ssize_t n = receive(buffer, sizeof(buffer));
            if (n == 0) return false;  // supervisor gone
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return true;
                closed = std::string("recv: ") + std::strerror(errno);
                return false;
            }
            if (!stream.feed({buffer, std::size_t(n)})) return false;
        }
    };
    auto next_status = std::chrono::steady_clock::now();
    while (!stopping && !result && !presentation.finished()) {
        // Wake on a control frame too: a free-pointer move is drawn on the next frame, not after the 50 ms wait.
        // Most wakes are Wayland events: read the control socket only when the wait did not see it idle.
        bool control_ready = true;
        if (!report(presentation.draw_test_pattern()) || !report(presentation.dispatch(50, control, &control_ready))) { result = 1; break; }
        if (control_ready && !read_control()) {
            if (stream.stopped()) std::cerr << "control: stopping on a refused frame (--control-errors fatal)\n";
            else std::cerr << "control: supervisor channel closed (" << closed << ")\n";
            result = 1; break;
        }
        if (status_fd >= 0)  // overview layout for the supervisor's free-pointer hit testing, only when it changes
            if (const auto geometry = presentation.take_overview_geometry()) {
                const auto line = *geometry + "\n";
                if (send(status_fd, line.data(), line.size(), MSG_NOSIGNAL | MSG_DONTWAIT) != ssize_t(line.size()))
                    presentation.resend_overview_geometry();
            }
        if (status_fd >= 0) {  // how far each desktop is from level (the supervisor's roll-level icons)
            timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
            if (const auto tilt = presentation.take_roll_geometry(double(ts.tv_sec) * 1000 + double(ts.tv_nsec) / 1e6)) {
                const auto line = *tilt + "\n";
                if (send(status_fd, line.data(), line.size(), MSG_NOSIGNAL | MSG_DONTWAIT) != ssize_t(line.size()))
                    presentation.resend_roll_geometry();
            }
        }
        if (status_fd >= 0) {  // the help card on the overview's canvas: free-pointer clicks on its page arrows
            timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
            if (const auto geometry = presentation.take_help_geometry(double(ts.tv_sec) * 1000 + double(ts.tv_nsec) / 1e6)) {
                const auto line = *geometry + "\n";
                if (send(status_fd, line.data(), line.size(), MSG_NOSIGNAL | MSG_DONTWAIT) != ssize_t(line.size()))
                    presentation.resend_help_geometry();
            }
        }
        if(status_fd>=0)if(const auto geometry=presentation.take_maximize_geometry()){
            const auto line=*geometry+"\n";
            if(send(status_fd,line.data(),line.size(),MSG_NOSIGNAL|MSG_DONTWAIT)!=ssize_t(line.size()))presentation.resend_maximize_geometry();
        }
        if(status_fd>=0){timespec ts{};clock_gettime(CLOCK_MONOTONIC,&ts);
            if(const auto snapshot=presentation.take_owner_readback(double(ts.tv_sec)*1000+double(ts.tv_nsec)/1e6)){
              const auto line=*snapshot+"\n";if(send(status_fd,line.data(),line.size(),MSG_NOSIGNAL|MSG_DONTWAIT)!=ssize_t(line.size()))presentation.resend_owner_readback();
            }
        }
        const auto now = std::chrono::steady_clock::now();
        if (status_fd >= 0 && now >= next_status) {
            next_status = now + std::chrono::seconds(1);
            timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
            const auto line = presentation.status_json(double(ts.tv_sec)*1000 + double(ts.tv_nsec)/1e6) + "\n";
            // Datagram socketpair: EAGAIN drops a whole sample, never a partial JSON line.
            (void)send(status_fd, line.data(), line.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
        }
        if (send(control, "H", 1, MSG_NOSIGNAL | MSG_DONTWAIT) != 1) {
            std::cerr << "control: supervisor channel closed (heartbeat: " << std::strerror(errno) << ")\n";
            result = 1; break;
        }
    }
    for (int fd : video_fds) ::close(fd);
    presentation.save_continuity(continuity_fd);
    if (continuity_fd >= 0) ::close(continuity_fd);
    presentation.close(); // Pending feedback becomes abandoned, never invented discard.
    std::cerr << presentation.timing_report() << '\n';
    if (!capture.empty()) std::cerr << presentation.capture_report() << '\n';
    std::cerr << "{\"kind\":\"control_stream\",\"accepted\":" << stream.accepted() << ",\"refused\":" << stream.refused() << "}\n";
    if (status_fd >= 0) ::close(status_fd);
    ::close(control); return result;
}
