#include "xr/ipc.hpp"
#include "xr/presentation.hpp"
#include <charconv>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <map>
#include <poll.h>
#include <string>
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
        std::cout << "xr-native-spike: native output-bound layer-shell test pattern (no capture/SDK/input).\n"
                     "Launched only by the permission-gated Python supervisor with inherited capability sockets.\n"
                     "Internal: --supervised-fd FD --wayland-fd FD --output NAME --run-id RUN --session SESSION\n"
                     "Presentation-only: python -m tools.native_spike present --instance INSTANCE --output OUTPUT --consent-file FILE.\n"
                     "Managed output ownership qualification is currently BLOCKED. No 120 Hz claim.\n";
        return 0;
    }
    std::map<std::string, std::string> args;
    if (argc != 11) { std::cerr << "permission: supervised capability sockets required; see --help\n"; return 2; }
    for (int i = 1; i < argc; i += 2) if (!args.emplace(argv[i], argv[i + 1]).second) return 2;
    for (const char* key : {"--supervised-fd", "--wayland-fd", "--output", "--run-id", "--session"}) if (!args.contains(key)) return 2;
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
    if (!report(presentation.open(args["--output"]))) return 1;
    while (!stopping) {
        if (!report(presentation.draw_test_pattern()) || !report(presentation.dispatch(50))) return 1;
        if (send(control, "H", 1, MSG_NOSIGNAL | MSG_DONTWAIT) != 1) return 1;
    }
    presentation.close(); ::close(control); return 0;
}
