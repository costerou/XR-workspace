// luma_latency_report: reads what `xr-luma-driver --latency-log FILE` wrote and prints how late each IMU packet reached us,
// against the fastest packet around it (method in include/luma/latency_log.hpp). Offline: no device, no feed.
//
//   luma_latency_report FILE [--window-s SECONDS] [--label NAME]
//
// Prints the tracking_bench layout, one metric per line, then a json line. Exit 0 with a report, 2 on a bad argument or file,
// 3 when the file holds no packets to report on.
#include "luma/latency_log.hpp"
#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>

int main(int argc, char** argv) {
    std::string path, label;
    double window_s = 10.0;
    for (int i = 1; i < argc; ++i) {
        const std::string_view arg = argv[i];
        if ((arg == "--window-s" || arg == "--label") && i + 1 >= argc) { std::fprintf(stderr, "%s needs a value\n", argv[i]); return 2; }
        if (arg == "--window-s") {
            char* end = nullptr;
            window_s = std::strtod(argv[++i], &end);
            if (!end || *end || !(window_s >= 0.1 && window_s <= 3600)) { std::fprintf(stderr, "--window-s needs 0.1 to 3600\n"); return 2; }
        } else if (arg == "--label") label = argv[++i];
        else if (arg == "--help" || arg == "-h") { std::puts("usage: luma_latency_report FILE [--window-s SECONDS] [--label NAME]"); return 0; }
        else if (path.empty() && !arg.starts_with("--")) path = arg;
        else { std::fprintf(stderr, "unknown argument: %s\n", argv[i]); return 2; }
    }
    if (path.empty()) { std::fprintf(stderr, "usage: luma_latency_report FILE [--window-s SECONDS] [--label NAME]\n"); return 2; }
    std::string why;
    const auto records = luma::latency::load(path, why);
    if (!records) { std::fprintf(stderr, "%s\n", why.c_str()); return 2; }
    const luma::latency::Report report = luma::latency::analyze(*records, window_s);
    std::fputs(luma::latency::format(report, label.empty() ? path : label).c_str(), stdout);
    if (report.packets == 0) { std::fprintf(stderr, "no packets in %s\n", path.c_str()); return 3; }
    return 0;
}
