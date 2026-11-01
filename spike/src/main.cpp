#include "xr/ipc.hpp"
#include "xr/presentation.hpp"
#include "xr/capture.hpp"
#include <cerrno>
#include <charconv>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <map>
#include <poll.h>
#include <string>
#include <vector>
#include <sys/socket.h>
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
                     "Presentation-only: python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE.\n"
                     "Managed output ownership qualification is currently BLOCKED. No 120 Hz claim.\n"
                     "Optional presentation-time v1 feedback: bounded aggregate JSON on stderr at orderly exit/error.\n"
                     "Default static; optional --animate-pattern-ms 1..60000 (exact matching consent required).\n"
                     "Or --capture XR-1[,XR-2[,XR-3]]: show only the app's own managed outputs (managed-output capture consent),\n"
                     "  optionally with --pose-feed xrld: world-anchored 3DoF from the network-blocked XRLinuxDriver feed.\n"
                     "Generated SHM only; callback-paced redraw is not scanout or production GPU evidence.\n";
        return 0;
    }
    std::map<std::string, std::string> args;
    if (argc != 11 && argc != 13 && argc != 15) { std::cerr << "permission: supervised capability sockets required; see --help\n"; return 2; }
    for (int i = 1; i < argc; i += 2) if (!args.emplace(argv[i], argv[i + 1]).second) return 2;
    for (const char* key : {"--supervised-fd", "--wayland-fd", "--output", "--run-id", "--session"}) if (!args.contains(key)) return 2;
    std::uint32_t animation_ms = 0;
    std::vector<std::string> capture;
    bool pose_feed = false;
    // Optional modes: generated animation alone; or capture, optionally with the XRLinuxDriver pose feed.
    const std::size_t optional = std::size_t(argc - 11) / 2;
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
    for (int fd : {control, wayland}) {
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
    if (!result && pose_feed) presentation.enable_tracking("/tmp/shader_runtime_");  // XRLinuxDriver IPC (fixed path)
    if (!result && animation_ms && !report(presentation.animate_pattern(animation_ms))) result = 1;
    // Further supervisor frames (view, stop) on the same private socket; replay-protected queue.
    xr::ControlQueue queue(args["--run-id"], args["--session"]);
    (void)queue.push(raw);  // the validated start frame sets the replay floor
    std::string pending;
    auto read_control = [&]() -> bool {
        char buffer[4096];
        for (;;) {
            const ssize_t n = recv(control, buffer, sizeof(buffer), MSG_DONTWAIT);
            if (n == 0) return false;  // supervisor gone
            if (n < 0) return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR;
            pending.append(buffer, std::size_t(n));
            if (pending.size() > 65536) return false;
            for (std::size_t nl; (nl = pending.find('\n')) != std::string::npos;) {
                auto frame = pending.substr(0, nl + 1); pending.erase(0, nl + 1);
                if (!std::holds_alternative<std::monostate>(queue.push(frame))) return false;  // malformed/replayed: stop
                while (auto m = queue.pop()) {
                    if (m->op == xr::ControlOp::stop) stopping = 1;
                    else if (m->op == xr::ControlOp::recenter) presentation.recenter();
                    else if (m->op == xr::ControlOp::style) {
                        const auto& c = std::get<xr::StylePayload>(m->payload);
                        presentation.set_accent(c.r, c.g, c.b);
                    }
                    else if (m->op == xr::ControlOp::view) {
                        const auto& v = std::get<xr::ViewPayload>(m->payload);
                        presentation.set_view(v.zoomed, v.selected, v.spacing, v.mode);
                    }
                }
            }
        }
    };
    while (!stopping && !result && !presentation.finished()) {
        if (!report(presentation.draw_test_pattern()) || !report(presentation.dispatch(50))) { result = 1; break; }
        if (!read_control()) { std::cerr << "control: supervisor channel closed or invalid\n"; result = 1; break; }
        if (send(control, "H", 1, MSG_NOSIGNAL | MSG_DONTWAIT) != 1) { result = 1; break; }
    }
    presentation.close(); // Pending feedback becomes abandoned, never invented discard.
    std::cerr << presentation.timing_report() << '\n';
    if (!capture.empty()) std::cerr << presentation.capture_report() << '\n';
    ::close(control); return result;
}
