// The C++ side of tests/cpp_interop.rs, built from the driver's real headers and sources
// (spike/driver/include/luma/rt.hpp, predict.hpp, src/rt.cpp, src/predict.cpp), so the Rust crate is
// checked against what the C++ compiler lays out and what the C++ code writes and reads.
//
// record(i) and state(i) must match the generators in cpp_interop.rs.
#include "luma/predict.hpp"
#include "luma/rt.hpp"

#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>

using luma::MotionState;
using luma::PredictionWire;
using luma::rt::PoseRecord;
using luma::rt::SeqlockReader;
using luma::rt::SeqlockSlot;
using luma::rt::SeqlockWriter;

namespace {
float fbits(std::uint32_t v) { return std::bit_cast<float>(v & 0x3fffffffu); }

PoseRecord record(std::uint64_t i) {
    const auto lo = std::uint32_t(i);
    return PoseRecord{fbits(lo), fbits(lo + 1), fbits(lo + 2), fbits(lo + 3), i, -std::int64_t(i), lo, lo ^ 0x5a5a5a5au};
}
bool same(const PoseRecord& a, const PoseRecord& b) { return std::memcmp(&a, &b, sizeof a) == 0; }

MotionState state(std::uint32_t i) {
    const auto f = [i](std::uint32_t k) { return fbits(i * 31u + k); };
    MotionState s;
    s.q = {f(0), f(1), f(2), f(3)};
    s.host_ns = std::uint64_t(i) << 32 | i;
    s.seq = i;
    s.omega_rad_s = {f(4), f(5), f(6)};
    s.alpha_rad_s2 = {f(7), f(8), f(9)};
    s.valid = true;
    s.calibrated = i % 2 == 0;
    return s;
}

int fail(const std::string& why) {
    std::fprintf(stderr, "%s\n", why.c_str());
    return 1;
}

void print_pose(const PoseRecord& r) {
    std::printf("%08x %08x %08x %08x %llu %lld %u %u\n", std::bit_cast<std::uint32_t>(r.w), std::bit_cast<std::uint32_t>(r.x),
                std::bit_cast<std::uint32_t>(r.y), std::bit_cast<std::uint32_t>(r.z), (unsigned long long)r.t_raw_ns,
                (long long)r.raw_to_mono_ns, r.seq, r.flags);
}
}  // namespace

int main(int argc, char** argv) {
    const std::string cmd = argc > 1 ? argv[1] : "";
    std::string why;
    if (cmd == "layout") {
        std::printf("pose_size %zu\n", sizeof(PoseRecord));
        std::printf("pose_align %zu\n", alignof(PoseRecord));
        std::printf("pose_w %zu\n", offsetof(PoseRecord, w));
        std::printf("pose_x %zu\n", offsetof(PoseRecord, x));
        std::printf("pose_y %zu\n", offsetof(PoseRecord, y));
        std::printf("pose_z %zu\n", offsetof(PoseRecord, z));
        std::printf("pose_t_raw_ns %zu\n", offsetof(PoseRecord, t_raw_ns));
        std::printf("pose_raw_to_mono_ns %zu\n", offsetof(PoseRecord, raw_to_mono_ns));
        std::printf("pose_seq %zu\n", offsetof(PoseRecord, seq));
        std::printf("pose_flags %zu\n", offsetof(PoseRecord, flags));
        std::printf("pose_calibrated %u\n", luma::rt::kPoseCalibrated);
        std::printf("pose_shm_name %s\n", luma::rt::kPoseShmName);
        std::printf("slot_size %zu\n", sizeof(SeqlockSlot<PoseRecord>));
        std::printf("slot_words %zu\n", offsetof(SeqlockSlot<PoseRecord>, words));
        std::printf("slot_offset %zu\n", luma::rt::ShmMapping::kSlotOffset);
        std::printf("shm_total %zu\n", luma::rt::ShmMapping::kSlotOffset + sizeof(SeqlockSlot<PoseRecord>));
        std::printf("wire_size %zu\n", sizeof(PredictionWire));
        std::printf("wire_align %zu\n", alignof(PredictionWire));
        std::printf("wire_seq %zu\n", offsetof(PredictionWire, seq));
        std::printf("wire_magic %zu\n", offsetof(PredictionWire, magic));
        std::printf("wire_words %zu\n", offsetof(PredictionWire, words));
        std::printf("wire_magic_value %u\n", luma::kPredictionMagic);
        std::printf("wire_ipc_name %s\n", luma::kPredictionIpcName);
        return 0;
    }
    if (cmd == "write-pose" && argc == 4) {
        auto w = SeqlockWriter<PoseRecord>::create(argv[2], why);
        if (!w) return fail(why);
        w->publish(record(std::strtoull(argv[3], nullptr, 10)));
        return 0;
    }
    if (cmd == "read-pose" && argc == 3) {
        auto r = SeqlockReader<PoseRecord>::open(argv[2], why);
        if (!r) return fail(why);
        PoseRecord p;
        if (!r->read(p)) return fail("no record");
        print_pose(p);
        return 0;
    }
    if (cmd == "stress-write" && argc == 3) {  // until killed
        auto w = SeqlockWriter<PoseRecord>::create(argv[2], why);
        if (!w) return fail(why);
        w->publish(record(1));
        std::printf("ready\n");
        std::fflush(stdout);
        for (std::uint64_t i = 2;; ++i) w->publish(record(i));
    }
    if (cmd == "stress-read" && argc == 4) {
        auto r = SeqlockReader<PoseRecord>::open(argv[2], why);
        if (!r) return fail(why);
        const long n = std::strtol(argv[3], nullptr, 10);
        long ok = 0;
        std::uint64_t last = 0, first = 0;
        for (long k = 0; k < n; ++k) {
            PoseRecord p;
            if (!r->read(p)) continue;
            if (!same(p, record(p.t_raw_ns))) return fail("torn read at " + std::to_string(p.t_raw_ns));
            if (p.t_raw_ns < last) return fail("went backwards");
            if (!ok) first = p.t_raw_ns;
            last = p.t_raw_ns;
            ++ok;
        }
        std::printf("%ld %llu\n", ok, (unsigned long long)(last - first));
        return 0;
    }
    alignas(64) unsigned char buf[sizeof(PredictionWire)] = {};
    auto* wire = new (buf) PredictionWire();
    if (cmd == "write-prediction" && argc == 3) {
        luma::write_prediction(*wire, state(std::uint32_t(std::strtoul(argv[2], nullptr, 10))));
        for (unsigned char c : buf) std::printf("%02x", c);
        std::printf("\n");
        return 0;
    }
    if (cmd == "read-prediction" && argc == 3) {
        const std::string hex = argv[2];
        if (hex.size() != 2 * sizeof buf) return fail("bad hex");
        for (std::size_t i = 0; i < sizeof buf; ++i) buf[i] = (unsigned char)std::stoul(hex.substr(2 * i, 2), nullptr, 16);
        const auto s = luma::read_prediction(*wire);
        if (!s) {
            std::printf("none\n");
            return 0;
        }
        const MotionState want = state(s->seq);
        const bool match = s->host_ns == want.host_ns && std::memcmp(&s->q, &want.q, sizeof want.q) == 0 &&
                           s->omega_rad_s == want.omega_rad_s && s->alpha_rad_s2 == want.alpha_rad_s2 &&
                           s->calibrated == want.calibrated && s->valid;
        std::printf("%s %u\n", match ? "match" : "differs", s->seq);
        return 0;
    }
    return fail("usage: pose_interop layout | write-pose NAME I | read-pose NAME | stress-write NAME | stress-read NAME N | "
                "write-prediction I | read-prediction HEX");
}
