// The latency report (include/luma/latency_log.hpp) on synthetic streams whose per-packet delay is known, then the
// luma_latency_report binary (argv[1]) on files written here. No device, no feed.
#include "luma/latency_log.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

using namespace luma::latency;
namespace {
int g_failures = 0;
#define CHECK(x) do { if (!(x)) { ++g_failures; std::cerr << "  FAILED " << __FILE__ << ':' << __LINE__ << ": " #x "\n"; } } while (0)
#define RUN(f, ...) do { std::cerr << #f "\n"; f(__VA_ARGS__); } while (0)
constexpr std::uint64_t kMs = 1'000'000;

struct Lcg {  // deterministic, so a failure reproduces
    std::uint64_t s = 0x2545F4914F6CDD1Dull;
    double unit() { s = s * 6364136223846793005ull + 1442695040888963407ull; return double(s >> 11) / double(1ull << 53); }
};

double pick(std::vector<double> v, double q) {
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, std::size_t(std::ceil(q * double(v.size()))) - 1)];
}

// host = offset + device * (1 + ppm) + delay, the way a link with a fast host clock delivers. Packets keep their order.
struct Stream {
    std::vector<Record> records;
    std::vector<double> delays;  // what each packet saw beyond the ideal line, in order of the records it made
    std::uint64_t host = 0;
    void open() { records.push_back({.flags = kOpened}); }
    void packet(std::uint64_t device, std::uint32_t seq, std::uint64_t delay, double ppm, std::uint64_t offset = 5'000'000'017) {
        const std::uint64_t ideal = offset + device + std::uint64_t(std::llround(double(device) * ppm * 1e-6));
        host = std::max(host + 1, ideal + delay);
        records.push_back({host, device, seq & 0xFFFF, 0});
        delays.push_back(double(host - ideal));
    }
};

void test_recovers_known_delays_and_drift() {
    for (const double ppm : {75.0, -40.0, 0.0}) {
        Stream s;
        Lcg rng;
        s.open();
        s.packet(0, 0, 0, ppm);  // the leftover
        for (std::uint64_t i = 1; i <= 60'000; ++i) {  // a minute at 1 kHz: six 10 s windows
            const double u = rng.unit();
            std::uint64_t delay = u < 0.05 ? 0 : std::uint64_t(-std::log(rng.unit()) * 40'000);  // mostly tens of us
            if (i % 997 == 0) delay += 3 * kMs;  // a busy host now and then
            if (i == 41'234) delay += 12'500'000;  // and the packets queued behind it come in the same reap
            s.packet(i * kMs, std::uint32_t(i), delay, ppm);
        }
        const Report r = analyze(s.records);
        std::vector<double> want(s.delays.begin() + 1, s.delays.end());
        CHECK(r.opens == 1 && r.leftovers == 1 && r.repeats == 0 && r.lost == 0 && r.segments == 1 && r.windows == 6);
        CHECK(r.packets == want.size());
        const auto near = [](double got, double expect) { return std::fabs(got - expect) <= 1000; };  // 1 us
        CHECK(near(r.excess.p50, pick(want, 0.5)));
        CHECK(near(r.excess.p99, pick(want, 0.99)));
        CHECK(near(r.excess.p999, pick(want, 0.999)));
        CHECK(near(r.excess.max, pick(want, 1.0)));
        CHECK(r.excess.max > 12.5e6);
        CHECK(r.over_8ms == std::size_t(std::count_if(want.begin(), want.end(), [](double d) { return d > 8e6; })) && r.over_8ms >= 5);
        CHECK(r.over_2ms >= 60);
        CHECK(std::fabs(r.drift_ppm - ppm) < 0.1);
        CHECK(std::fabs(r.duration_s - 60) < 0.1 && std::fabs(r.rate_hz - 1000) < 2);
        if (!near(r.excess.p99, pick(want, 0.99)))
            std::cerr << "  ppm " << ppm << " got " << r.excess.p50 << " " << r.excess.p99 << " want " << pick(want, 0.5) << " "
                      << pick(want, 0.99) << "\n";
    }
}

// The Ultra stamps in 100 us ticks off its own clock, so the stamp itself is up to a tick off: the floor of the report.
void test_device_stamp_rounding_is_the_floor() {
    Stream s;
    Lcg rng;
    s.open();
    s.packet(0, 0, 0, 0);
    std::vector<double> want;
    for (std::uint64_t i = 1; i <= 20'000; ++i) {
        const std::uint64_t truth = i * kMs + std::uint64_t(rng.unit() * 300'000);  // when the sample was really taken
        const std::uint64_t stamp = truth / 100'000 * 100'000;
        const std::uint64_t delay = rng.unit() < 0.01 ? 2 * kMs : 50'000;
        s.packet(stamp, std::uint32_t(i), delay + (truth - stamp), 30);
        want.push_back(double(delay));
    }
    const Report r = analyze(s.records);
    CHECK(r.packets == 20'000);
    CHECK(std::fabs(r.excess.p50 - (pick(want, 0.5) - 50'000)) <= 100'000);  // within a tick of the truth above the floor
    CHECK(std::fabs(r.excess.p999 - (pick(want, 0.999) - 50'000)) <= 100'000);
    CHECK(std::fabs(r.drift_ppm - 30) < 2);
}

void test_resends_losses_and_wraps() {
    Stream s;
    s.open();
    s.packet(1 * kMs, 1, 0, 0);  // the leftover
    for (std::uint64_t i = 1; i <= 60; ++i) {  // the Ultra's opening resends
        s.packet(i * kMs, std::uint32_t(65469 + i), 0, 0);
        s.packet(i * kMs, std::uint32_t(65469 + i), 0, 0);
    }
    std::uint64_t t = 61;
    for (std::uint32_t seq = 65530; seq != 65540; ++seq, ++t) s.packet(t * kMs, seq, 0, 0);  // across the 16-bit wrap: no loss
    t += 3;  // three samples missing
    for (std::uint32_t seq = 65543; seq != 65560; ++seq, ++t) s.packet(t * kMs, seq, 0, 0);
    const Report r = analyze(s.records);
    CHECK(r.leftovers == 1);
    CHECK(r.repeats == 60);
    CHECK(r.lost == 3);
    CHECK(r.segments == 1);
    CHECK(r.packets == 60 + 10 + 17);
}

void test_reopens_restarts_and_silences_start_new_lines() {
    Stream s;
    s.open();
    s.packet(0, 0, 0, 0);
    for (std::uint64_t i = 1; i <= 2000; ++i) s.packet(i * kMs, std::uint32_t(i), i % 10 ? 0 : 200'000, 0);
    // The device restarted: its clock went back to zero, and the offset is new. Not a resend.
    for (std::uint64_t i = 1; i <= 2000; ++i) s.packet(i * kMs, std::uint32_t(i), i % 10 ? 0 : 200'000, 0, 9'000'000'000);
    // Silent for 2 s with no reopen, then on with another offset.
    s.host += 2'000 * kMs;
    for (std::uint64_t i = 4001; i <= 6000; ++i) s.packet(i * kMs, std::uint32_t(i), i % 10 ? 0 : 200'000, 0, 11'000'000'000);
    s.open();
    s.packet(6001 * kMs, 6001, 0, 0, 11'000'000'000);  // leftover: whatever its time, never counted
    for (std::uint64_t i = 6002; i <= 8000; ++i) s.packet(i * kMs, std::uint32_t(i), i % 10 ? 0 : 200'000, 0, 13'000'000'000);
    const Report r = analyze(s.records);
    CHECK(r.segments == 4);
    CHECK(r.opens == 2 && r.leftovers == 2 && r.repeats == 0 && r.lost == 0);
    CHECK(std::fabs(r.excess.p50) < 1000);
    CHECK(std::fabs(r.excess.max - 200'000) < 1000);  // one line per segment: no offset step shows as delay
    CHECK(r.interarrival.max < 2 * kMs);              // the silences are between segments, not inside one
}

void test_file_round_trip(const std::filesystem::path& dir) {
    Stream s;
    s.open();
    for (std::uint64_t i = 0; i < 10; ++i) s.packet(i * kMs, std::uint32_t(i), 0, 0);
    const std::string path = dir / "a.lat";
    CHECK(save(path, s.records));
    std::string why;
    const auto back = load(path, why);
    CHECK(back && back->size() == s.records.size() && (*back)[3].device_ns == 2 * kMs && (*back)[0].flags == kOpened);
    CHECK(!load((dir / "absent").string(), why));
    {
        std::FILE* f = std::fopen((dir / "short.lat").c_str(), "wb");
        const Header h{};
        std::fwrite(&h, sizeof h, 1, f);
        std::fwrite("123", 3, 1, f);
        std::fclose(f);
    }
    CHECK(!load((dir / "short.lat").string(), why) && why.find("inside a record") != std::string::npos);
    {
        std::FILE* f = std::fopen((dir / "bad.lat").c_str(), "wb");
        std::fputs("LUMACAP0xxxxxxxx", f);
        std::fclose(f);
    }
    CHECK(!load((dir / "bad.lat").string(), why) && why.find("not a latency log") != std::string::npos);
    const std::string text = format(analyze(s.records), "idle \"q\"");
    CHECK(text.find("usb   excess over fastest packet") != std::string::npos);
    CHECK(text.find("json {\"label\":\"idle q\",\"packets\":9,") != std::string::npos);
}

int run(const std::string& binary, std::vector<std::string> args, std::string& out) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;
    const pid_t pid = fork();
    if (pid == 0) {
        dup2(pipefd[1], 1);
        close(pipefd[0]); close(pipefd[1]);
        std::vector<char*> argv{const_cast<char*>(binary.c_str())};
        for (auto& a : args) argv.push_back(a.data());
        argv.push_back(nullptr);
        execv(binary.c_str(), argv.data());
        _exit(127);
    }
    close(pipefd[1]);
    char chunk[4096];
    for (ssize_t n; (n = read(pipefd[0], chunk, sizeof chunk)) > 0;) out.append(chunk, std::size_t(n));
    close(pipefd[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

void test_report_binary(const std::string& binary, const std::filesystem::path& dir) {
    if (binary.empty()) { ++g_failures; std::cerr << "  FAILED no binary given\n"; return; }
    Stream s;
    s.open();
    for (std::uint64_t i = 0; i < 5000; ++i) s.packet(i * kMs, std::uint32_t(i), i % 100 == 0 ? 1'500'000 : 0, 20);
    const std::string path = dir / "run.lat";
    CHECK(save(path, s.records));
    std::string out;
    CHECK(run(binary, {path, "--label", "idle", "--window-s", "2"}, out) == 0);
    CHECK(out.find("json {\"label\":\"idle\",\"packets\":4999,") != std::string::npos);
    CHECK(out.find("\"over_1ms\":49,") != std::string::npos);
    std::cerr << out;
    const std::string empty = dir / "empty.lat";
    CHECK(save(empty, std::vector<Record>{{.flags = kOpened}}));
    out.clear();
    CHECK(run(binary, {empty}, out) == 3);
    out.clear();
    CHECK(run(binary, {(dir / "absent.lat").string()}, out) == 2);
    CHECK(run(binary, {path, "--window-s", "0"}, out) == 2);
    CHECK(run(binary, {}, out) == 2);
}
}  // namespace

int main(int argc, char** argv) {
    const std::filesystem::path dir = std::filesystem::temp_directory_path() / ("luma_latency_test_" + std::to_string(getpid()));
    std::filesystem::create_directories(dir);
    RUN(test_recovers_known_delays_and_drift);
    RUN(test_device_stamp_rounding_is_the_floor);
    RUN(test_resends_losses_and_wraps);
    RUN(test_reopens_restarts_and_silences_start_new_lines);
    RUN(test_file_round_trip, dir);
    RUN(test_report_binary, argc > 1 ? argv[1] : "", dir);
    std::filesystem::remove_all(dir);
    std::cerr << (g_failures ? "FAILED\n" : "all passed\n");
    return g_failures ? 1 : 0;
}
