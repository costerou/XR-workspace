// Writes, or checks, the fusion golden files the Rust port is held to bit for bit
// (rust/corpus/luma-fusion/, format in rust/README.md).
//
//   luma_fusion_golden write VARIANT FIXTURE OUT
//   luma_fusion_golden check VARIANT FIXTURE GOLDEN
//
// The samples are what the daemon would feed Fusion: parse_imu, then the Luma sequence tracker, accepted
// samples only. VARIANT picks the run:
//   plain   default FusionConfig, the stream as recorded.
//   faults  a warm-start bias, two runs of dropped samples (one a short loss, one a gap too long to integrate),
//           one timestamp thrown a second ahead and one duplicate: Coarse from the start, the time-jump, gap,
//           Recovering and rejected paths.
// check exits 1 at the first byte that differs, so a change to fusion.cpp that moves its output fails ctest
// until the corpus is rewritten on purpose.
#include "luma/fusion.hpp"
#include "luma/protocol.hpp"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

using namespace luma;
namespace {
constexpr std::uint32_t kStatusEvery = 100;
constexpr float kPredictS = 0.017f;

struct Out {
    std::vector<std::uint8_t> bytes;
    template <class T> void put(T v) {
        unsigned char raw[sizeof(T)];
        std::memcpy(raw, &v, sizeof(T));
        bytes.insert(bytes.end(), raw, raw + sizeof(T));
    }
    void put3(const std::array<float, 3>& a) { for (float f : a) put(f); }
    void putq(const Quatf& q) { put(q.w); put(q.x); put(q.y); put(q.z); }
};

std::vector<ImuSample> samples(const std::string& path, bool faults) {
    std::ifstream in(path, std::ios::binary);
    const std::vector<std::uint8_t> raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    auto tracker = luma_sequence_tracker();
    std::vector<ImuSample> out;
    for (std::size_t at = 0; at + kLumaImuPacketSize <= raw.size(); at += kLumaImuPacketSize) {
        auto s = parse_imu(std::span<const std::uint8_t>(raw.data() + at, kLumaImuPacketSize));
        if (!s || tracker.observe(*s).verdict != SequenceTracker::Verdict::accepted) continue;
        out.push_back(*s);
    }
    if (!faults) return out;
    std::vector<ImuSample> mangled;
    for (std::size_t i = 0; i < out.size(); ++i) {
        if ((i >= 3000 && i < 3010) || (i >= 5000 && i < 5400)) continue;  // 10 ms lost; 400 ms gap
        ImuSample s = out[i];
        if (i == 4000) s.t_ns += 1'000'000'000;                              // clock jump, sequence whole
        mangled.push_back(s);
        if (i == 4500) mangled.push_back(s);                                 // duplicate
    }
    return mangled;
}

std::vector<std::uint8_t> golden(const std::vector<ImuSample>& in, bool faults) {
    FusionConfig config;
    if (faults) config.warm_start_bias = std::array<float, 3>{0.004f, -0.0025f, 0.0015f};
    Fusion fusion(config);
    Out o;
    o.bytes.insert(o.bytes.end(), {'L', 'U', 'M', 'A', 'F', 'U', 'S', '1'});
    o.put(std::uint32_t(in.size()));
    o.put(kStatusEvery);
    for (std::size_t i = 0; i < in.size(); ++i) {
        const ImuSample& s = in[i];
        o.put(s.t_ns);
        o.put(s.seq);
        o.put3(s.gyro_rad_s);
        o.put3(s.accel_m_s2);
        const PoseOut p = fusion.update(s);
        o.putq(p.q);
        o.put(p.t_ns);
        o.put(p.seq);
        o.put(std::uint8_t(p.calibrated));
        if ((i + 1) % kStatusEvery != 0 && i + 1 != in.size()) continue;
        const FusionStatus st = fusion.status();
        o.put(std::uint8_t(st.quality));
        o.put(std::uint8_t(st.still));
        o.put3(st.gyro_bias_rad_s);
        o.put3(st.omega_body_rad_s);
        o.put3(st.omega_world_rad_s);
        o.put3(st.up_body);
        o.put(st.calibrated_after_s);
        o.put(st.samples);
        o.put(st.rejected);
        o.put(st.gaps);
        o.put(st.time_jumps);
        o.putq(fusion.predict(kPredictS));
    }
    return o.bytes;
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 5 || (std::strcmp(argv[1], "write") != 0 && std::strcmp(argv[1], "check") != 0) ||
        (std::strcmp(argv[2], "plain") != 0 && std::strcmp(argv[2], "faults") != 0)) {
        std::cerr << "usage: luma_fusion_golden write|check plain|faults FIXTURE FILE\n";
        return 2;
    }
    const bool faults = std::strcmp(argv[2], "faults") == 0;
    const std::vector<ImuSample> in = samples(argv[3], faults);
    if (in.empty()) { std::cerr << "no samples in " << argv[3] << '\n'; return 1; }
    const std::vector<std::uint8_t> bytes = golden(in, faults);
    if (std::strcmp(argv[1], "write") == 0) {
        std::ofstream(argv[4], std::ios::binary).write(reinterpret_cast<const char*>(bytes.data()), std::streamsize(bytes.size()));
        std::cerr << argv[4] << ": " << in.size() << " samples, " << bytes.size() << " bytes\n";
        return 0;
    }
    std::ifstream f(argv[4], std::ios::binary);
    const std::vector<std::uint8_t> want((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    for (std::size_t i = 0; i < std::max(want.size(), bytes.size()); ++i) {
        if (i >= want.size() || i >= bytes.size() || want[i] != bytes[i]) {
            std::cerr << argv[4] << ": differs from fusion.cpp at byte " << i << " (golden " << want.size() << " bytes, now "
                      << bytes.size() << "). Rewrite it on purpose with: luma_fusion_golden write " << argv[2] << ' ' << argv[3]
                      << ' ' << argv[4] << '\n';
            return 1;
        }
    }
    std::cerr << argv[4] << ": matches, " << in.size() << " samples\n";
    return 0;
}
