// Writes golden/motion_smooth.txt: spike/tests/pro2_smooth_replay.cpp itself (built with its main renamed), run over the
// fixtures tests/fixtures/pro2_smooth_replay{,_wire}.in with each case's flags. Per case: "case <fixture> <flags>", the
// replay's stdout, "stderr <its stderr line>" and "exit <code>". tests/motion_smooth.rs replays the same and compares bytes.
// From the repository root:
//   g++ -std=c++20 -O1 -pthread -Ispike/include -Ispike/driver/include -Dmain=replay_main -c spike/tests/pro2_smooth_replay.cpp -o /tmp/replay.o &&
//   g++ -std=c++20 -O1 -pthread -Ispike/include -Ispike/driver/include rust/crates/xr-renderer/golden/motion_smooth.cpp /tmp/replay.o \
//       spike/src/motion_smooth.cpp spike/src/predict.cpp spike/src/pose.cpp spike/driver/src/rt.cpp -o /tmp/motion-smooth-golden &&
//   /tmp/motion-smooth-golden rust/crates/xr-renderer/tests/fixtures > rust/crates/xr-renderer/golden/motion_smooth.txt
// The fixtures are tools/pro2_runtime/smooth_acceptance.synthetic: 3 s on the 60 Hz cadence with the turn axis tilted 7 deg
// forward, and 1.5 s on the 240 Hz one with its yaw rate as the wire's velocity.
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
int replay_main(int argc, char** argv);
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string dir = argv[1];
    const struct { const char* fixture; std::vector<std::string> flags; } cases[] = {
        {"pro2_smooth_replay.in", {"--mode", "hold"}},
        {"pro2_smooth_replay.in", {"--mode", "legacy", "--constant", "4", "--stabilization", "60"}},
        {"pro2_smooth_replay.in", {"--mode", "smooth"}},
        {"pro2_smooth_replay.in", {"--mode", "smooth", "--phase", "2.1", "--read-jitter", "1.5", "--tau", "20"}},
        {"pro2_smooth_replay.in", {"--mode", "smooth", "--level", "1"}},
        {"pro2_smooth_replay.in", {"--mode", "smooth", "--stabilization", "0", "--max-predict", "2", "--snap", "0.2", "--brake", "1"}},
        {"pro2_smooth_replay.in", {"--mode", "smooth", "--cap", "12", "--constant", "8", "--brake-floor", "40", "--r2p", "9"}},
        {"pro2_smooth_replay_wire.in", {"--mode", "wire"}},
        {"pro2_smooth_replay_wire.in", {"--mode", "wire", "--period", "4.1667", "--level", "1", "--publish-delay", "0.3"}},
    };
    for (const auto& c : cases) {
        std::cout << "case " << c.fixture;
        for (const auto& f : c.flags) std::cout << ' ' << f;
        std::cout << '\n' << std::flush;
        if (!std::freopen((dir + "/" + c.fixture).c_str(), "r", stdin)) return 1;
        std::cin.clear();
        std::vector<std::string> args{"pro2_smooth_replay"};
        args.insert(args.end(), c.flags.begin(), c.flags.end());
        std::vector<char*> argv2;
        for (auto& a : args) argv2.push_back(a.data());
        std::ostringstream err;
        auto* old = std::cerr.rdbuf(err.rdbuf());
        const int code = replay_main(int(argv2.size()), argv2.data());
        std::cerr.rdbuf(old);
        std::cout << "stderr " << err.str() << "exit " << code << '\n';
    }
}
