// The driver on real bytes: tests/fixtures/luma_imu_real.bin is 8 s of the IMU stream from a live session on Cody's Luma
// Ultra (2026-11-04, spike/driver/docs/capture-findings.md), 28-byte packets end to end. Cut by
// `python3 -m tools.luma_capture imu-fixture`, which refuses anything that is not an IMU packet, so it holds sensor
// readings and nothing that identifies the device. The head rests, turns about 60 degrees, and comes back.
//
// These are the facts the simulators cannot give: that the layout, scales and axes are right for the real device.
#include "luma/fusion.hpp"
#include "luma/protocol.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace luma;
namespace {
int g_failures = 0;
#define CHECK(x) do { if (!(x)) { ++g_failures; std::cerr << "  FAILED " << __FILE__ << ':' << __LINE__ << ": " #x "\n"; } } while (0)
constexpr double kDeg = 180.0 / 3.14159265358979323846;

using Packet = std::array<std::uint8_t, kLumaImuPacketSize>;
std::vector<Packet> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<Packet> packets;
    Packet packet{};
    while (in.read(reinterpret_cast<char*>(packet.data()), std::streamsize(packet.size()))) packets.push_back(packet);
    return packets;
}

double yaw_of(const Quatf& q) { return std::atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)) * kDeg; }
double pitch_of(const Quatf& q) { return std::asin(std::clamp(2.0 * (q.w * q.y - q.z * q.x), -1.0, 1.0)) * kDeg; }

void test_fixture_is_sensor_readings_and_nothing_else(const std::vector<Packet>& packets) {
    CHECK(packets.size() > 7000);
    for (const Packet& p : packets) CHECK(p[0] == 0xaa && p[1] == 0x8f && p[2] == 0x00 && p[3] == 0x88 && p[6] == 16 && p[7] == 0);
}

void test_every_packet_parses_and_the_stream_is_whole(const std::vector<Packet>& packets) {
    auto tracker = luma_sequence_tracker();
    std::uint64_t last_t = 0;
    for (const Packet& p : packets) {
        auto s = parse_imu(p);
        CHECK(s);
        if (!s) return;
        const auto seen = tracker.observe(*s);
        CHECK(seen.verdict == SequenceTracker::Verdict::accepted);
        CHECK(!seen.restarted);
        if (last_t) CHECK(s->t_ns > last_t);
        last_t = s->t_ns;
    }
    CHECK(tracker.lost_total() <= 2);  // usbmon, not the glasses: see capture-findings.md
}

void test_device_clock_runs_at_100_us_per_tick_and_1_khz(const std::vector<Packet>& packets) {
    const auto first = parse_imu(packets.front()), last = parse_imu(packets.back());
    CHECK(first && last);
    const std::uint32_t seq_span = (last->seq - first->seq) & 0xffff;
    const double per_sample_ms = double(last->t_ns - first->t_ns) * 1e-6 / double(seq_span);
    CHECK(std::fabs(per_sample_ms - 1.0) < 0.001);
}

void test_gravity_is_up_and_the_right_size_at_rest(const std::vector<Packet>& packets) {
    double a[3]{}, g[3]{};
    const std::size_t n = 2000;
    for (std::size_t i = 0; i < n; ++i) {
        const auto s = parse_imu(packets[i]);
        for (int k = 0; k < 3; ++k) { a[k] += s->accel_m_s2[std::size_t(k)]; g[k] += s->gyro_rad_s[std::size_t(k)]; }
    }
    for (int k = 0; k < 3; ++k) { a[k] /= double(n); g[k] /= double(n); }
    const double magnitude = std::hypot(a[0], a[1], a[2]);
    CHECK(magnitude > 9.7 && magnitude < 10.1);  // a wrong layout or scale fails here first
    CHECK(a[2] > 9.5);                           // z is up, and the reading is specific force
    CHECK(std::fabs(a[1]) < 1.0);                // looking ahead, not leaning to one side
    for (int k = 0; k < 3; ++k) CHECK(std::fabs(g[k]) < 0.05);  // at rest, only the bias: well under 3 deg/s
}

// The gyro's scale and sign against gravity alone: the rotation the gyro measured between two still moments must carry one
// gravity reading onto the other. It is the same check that picked 16.4 LSB per deg/s over the alternatives.
void test_gyro_and_accelerometer_agree(const std::vector<Packet>& packets) {
    Fusion fusion;
    FusionStatus status;
    std::vector<double> yaw;
    PoseOut pose{};
    auto tracker = luma_sequence_tracker();
    double most = 0, least = 0;
    for (const Packet& p : packets) {
        auto s = parse_imu(p);
        tracker.observe(*s);
        pose = fusion.update(*s);
        if (pose.calibrated) { most = std::max(most, yaw_of(pose.q)); least = std::min(least, yaw_of(pose.q)); }
    }
    status = fusion.status();
    CHECK(status.quality != FusionQuality::Uncalibrated);
    CHECK(std::fabs(status.calibrated_after_s - 2.6f) < 0.2f);      // the head moved before 1.5 s of stillness: Coarse on time
    CHECK(most - least > 40);                                       // the turn is there
    CHECK(std::fabs(yaw_of(pose.q)) < 3);                           // and it came back: heading closes the loop
    const std::array<float, 3> up = status.up_body;
    const auto last = parse_imu(packets.back());
    const double n = std::hypot(last->accel_m_s2[0], last->accel_m_s2[1], last->accel_m_s2[2]);
    const double cosine = (up[0] * last->accel_m_s2[0] + up[1] * last->accel_m_s2[1] + up[2] * last->accel_m_s2[2]) / n;
    CHECK(std::acos(std::clamp(cosine, -1.0, 1.0)) * kDeg < 2.5);    // tilt holds through it
    CHECK(std::fabs(pitch_of(pose.q)) < 15);
}

void test_axes_rotate_the_right_way(const std::vector<Packet>& packets) {
    // Over the first big swing, world yaw rate and the raw gyro's z must carry the same sign: z is up in both frames.
    Fusion fusion;
    auto tracker = luma_sequence_tracker();
    std::size_t agree = 0, total = 0;
    double last_yaw = 0;
    bool have = false;
    for (std::size_t i = 0; i < packets.size(); ++i) {
        auto s = parse_imu(packets[i]);
        tracker.observe(*s);
        const PoseOut pose = fusion.update(*s);
        const double y = yaw_of(pose.q);
        if (have && pose.calibrated && std::fabs(s->gyro_rad_s[2]) > 0.5) {
            ++total;
            if ((std::remainder(y - last_yaw, 360.0) > 0) == (s->gyro_rad_s[2] > 0)) ++agree;
        }
        last_yaw = y;
        have = pose.calibrated;
    }
    CHECK(total > 100);
    CHECK(double(agree) / double(total) > 0.98);
}
}

int main(int argc, char** argv) {
    if (argc < 2) { std::cerr << "usage: real_capture_test FIXTURE\n"; return 2; }
    const std::vector<Packet> packets = load(argv[1]);
    if (packets.empty()) { std::cerr << "no packets in " << argv[1] << '\n'; return 1; }
    test_fixture_is_sensor_readings_and_nothing_else(packets);
    test_every_packet_parses_and_the_stream_is_whole(packets);
    test_device_clock_runs_at_100_us_per_tick_and_1_khz(packets);
    test_gravity_is_up_and_the_right_size_at_rest(packets);
    test_gyro_and_accelerometer_agree(packets);
    test_axes_rotate_the_right_way(packets);
    std::cerr << (g_failures ? "FAILED\n" : "all passed\n");
    return g_failures ? 1 : 0;
}
