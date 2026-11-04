// xr-luma-driver: our own head-tracking driver for the VITURE Luma Ultra. See include/luma/daemon.hpp.
#include "luma/daemon.hpp"
#include "luma/wiring.hpp"
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <string>
#include <sys/file.h>
#include <unistd.h>

namespace {
std::atomic<bool> g_stop{false};
extern "C" void on_signal(int) { g_stop.store(true, std::memory_order_relaxed); }

// One write per line, so a line is never torn. The wall time lets a log be laid beside the kernel's USB messages. If the
// reader of stderr is gone the line is lost and tracking carries on.
void log_line(std::string_view line) {
    timespec ts{};
    clock_gettime(CLOCK_REALTIME, &ts);
    char at[40];
    const int n = std::snprintf(at, sizeof at, " at=%lld.%03ld\n", (long long)ts.tv_sec, ts.tv_nsec / 1'000'000);
    std::string out(line);
    out.append(at, std::size_t(n));
    [[maybe_unused]] const ssize_t ignored = ::write(STDERR_FILENO, out.data(), out.size());
}
int fail(int code, const std::string& reason) {
    log_line("luma: error reason=" + reason);
    return code;
}
std::uint64_t monotonic_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
// Two drivers on one feed would fight over its segments. The kernel drops the lock with the process, however it ends.
// The fd stays open for the life of the process. Empty: taken; else why not.
std::string take_instance_lock(const std::string& prefix) {
    const std::string path = prefix + "luma-driver.lock";
    const int fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return "lock " + path + " " + std::strerror(errno);
    if (::flock(fd, LOCK_EX | LOCK_NB) != 0) return errno == EWOULDBLOCK ? "already-running" : "lock " + path + " " + std::strerror(errno);
    return {};
}
}

int main(int argc, char** argv) {
    using namespace luma;
    std::string why;
    const auto options = parse_options(std::span<const char* const>(argv + 1, std::size_t(argc > 0 ? argc - 1 : 0)), why);
    if (!options) { std::fprintf(stderr, "luma: error reason=usage %s\n%s", why.c_str(), kUsage); return 2; }
    if (options->help) { std::fputs(kUsage, stdout); return 0; }

    // No SA_RESTART: a signal ends a blocking read at once. SIGPIPE: stderr's reader going away must not end tracking.
    struct sigaction action{};
    action.sa_handler = on_signal;
    sigemptyset(&action.sa_mask);
    for (int sig : {SIGTERM, SIGINT, SIGHUP}) sigaction(sig, &action, nullptr);
    std::signal(SIGPIPE, SIG_IGN);

    if (*wiring::kStandIn) log_line(std::string("luma: warning stand-in=\"") + wiring::kStandIn + "\"");

    if (const std::string refused = take_instance_lock(options->ipc_prefix); !refused.empty()) return fail(5, refused);

    std::unique_ptr<Link> link;
    ReplayLink* replay = nullptr;
    if (options->replay) {
        auto records = load_capture(*options->replay, why);
        if (!records) return fail(3, "replay " + why);
        auto owned = std::make_unique<ReplayLink>(std::move(*records), options->replay_realtime);
        replay = owned.get();
        link = std::move(owned);
    } else {
        link = wiring::live_link(why);
        if (!link) return fail(3, "transport " + why);
    }
    std::unique_ptr<Sink> sink = wiring::feed_sink(options->ipc_prefix, why);
    if (!sink) return fail(4, "feed " + why);

    DaemonParts parts{*link, wiring::parser(), &wiring::estimator, *sink,
                      replay ? std::function<std::uint64_t()>([replay] { return replay->now_ns(); }) : std::function<std::uint64_t()>(&monotonic_ns),
                      &log_line, wiring::start_command(), wiring::stop_command()};
    Daemon daemon(std::move(parts), options->config);
    daemon.run(g_stop);
    return 0;
}
