// xr-luma-driver: our own head-tracking driver for the VITURE Luma Ultra. See include/luma/daemon.hpp.
#include "luma/daemon.hpp"
#include "luma/rt.hpp"
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

    // Live only. It helps when it can and costs nothing when it cannot: acceptance P3, works without privileges.
    // The daemon is one thread, this one, so the policy covers the whole hot path. Memory first: a FIFO thread that faults waits
    // on the disk like any other. lock_memory declines below a safe RLIMIT_MEMLOCK, and then the stack is still prefaulted.
    if (!options->replay && !options->no_rt) {
        const rt::MemoryLockResult memory = rt::lock_memory();
        rt::prefault_stack(256 * 1024);
        rt::ThreadPolicy policy;
        policy.fifo_cpu = options->rt_cpu;
        const rt::ThreadPolicyResult result = rt::apply_thread_policy(policy);
        log_line("luma: scheduling " + rt::describe(result) + (memory.locked ? " memory=locked" : " memory=unlocked")
                 + (result.needs_setup ? " setup=docs/performance.md#setting-up-real-time-once" : ""));
        if (!result.detail.empty()) log_line("luma: scheduling " + result.detail);
        if (!memory.detail.empty()) log_line("luma: scheduling " + memory.detail);
    }

    std::unique_ptr<wiring::BiasStore> bias;
    if (!options->replay) {
        const std::string dir = options->state_dir.empty() ? wiring::default_state_dir() : options->state_dir;
        if (!dir.empty()) {
            bias = std::make_unique<wiring::BiasStore>(dir + "/luma-gyro-bias");
            bias->load();
            if (bias->bias()) log_line("luma: bias restored");
        }
    }

    std::unique_ptr<Link> link;
    ReplayLink* replay = nullptr;
    if (options->replay) {
        auto records = load_capture(*options->replay, why, kReplayEndpoint, options->read_events);
        if (!records) return fail(3, "replay " + why);
        auto owned = std::make_unique<ReplayLink>(std::move(*records), options->replay_realtime);
        replay = owned.get();
        link = std::move(owned);
    } else {
        link = wiring::live_link(*options, &log_line);
    }
    if (options->latency_log) {
        link = wiring::latency_log_link(std::move(link), *options->latency_log, &log_line, why);
        if (!link) return fail(4, why);
    }
    // Only the real feed is shared and left in place. Anything else, a replay or a bench run, makes its own segments and removes
    // them: ftok keys hold 16 bits of the inode, so attaching to whatever exists could land on the live feed.
    std::unique_ptr<Sink> sink = wiring::feed_sink(options->ipc_prefix, why, options->ipc_prefix != kLiveIpcPrefix, options->read_events,
                                                   options->pose_seqlock ? rt::kPoseShmName : "");
    if (!sink) return fail(4, "feed " + why);
    if (options->pose_log) {
        sink = wiring::logging_sink(std::move(sink), *options->pose_log, options->pose_log_every, why);
        if (!sink) return fail(4, why);
    }

    const std::unique_ptr<Decoder> decoder = wiring::decoder();
    std::function<std::unique_ptr<Estimator>()> make_estimator = &wiring::estimator;
    if (bias) make_estimator = [store = bias.get()] { return wiring::estimator_with(*store); };
    DaemonParts parts{*link, *decoder, std::move(make_estimator), *sink,
                      replay ? std::function<std::uint64_t()>([replay] { return replay->now_ns(); }) : std::function<std::uint64_t()>(&monotonic_ns),
                      &log_line, wiring::start_for(options->start), options->stop_command ? wiring::stop_command() : std::vector<std::uint8_t>{},
                      wiring::before_start(options->start)};
    if (options->start == Options::Start::listen) parts.stop_command.clear();
    auto features = wiring::FeatureHost::make(options->feature_flags, &log_line, why);
    if (!features) return fail(2, "feature " + why);
    if (!replay) {
        parts.on_link = [host = features.get()](bool up, std::uint64_t now) { host->link(up, now); };
        parts.on_tick = [host = features.get(), d = decoder.get(), store = bias.get(), next_save = std::uint64_t(0)](std::uint64_t now) mutable {
            host->tick(now, d->temperature_c());
            if (store && now >= next_save) {  // a small file, and the read queue rides out far longer than it takes
                if (next_save) store->save();
                next_save = now + 300'000'000'000ull;
            }
        };
    }
    {
        Daemon daemon(std::move(parts), options->config);
        daemon.run(g_stop);
        if (replay) {
            const rt::LatencyHistogram& times = daemon.process_times();
            char line[160];
            std::snprintf(line, sizeof line, "luma: replay process_us p50=%.2f p99=%.2f p999=%.2f max=%.2f packets=%llu",
                          double(times.percentile(50.0)) / 1000.0, double(times.percentile(99.0)) / 1000.0,
                          double(times.percentile(99.9)) / 1000.0, double(times.max()) / 1000.0, (unsigned long long)times.count());
            log_line(line);
        }
    }  // the estimator hands over its bias as the daemon goes
    if (bias) bias->save();
    return 0;
}
