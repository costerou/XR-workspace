// Golden vectors here come from two places, and each says which:
//   - bytes printed in a public source: viture-v2 PROTOCOL.md, go-viture frames;
//   - bytes computed by an independent script from a published layout, using
//     published sensor values, so they check this code against the layout.
//   - bytes a real device or VITURE's own code produced, kept in go-viture's
//     tests: the strongest kind, marked as such where used;
//   - bytes from our own usbmon capture of a live session on Cody's Luma Ultra
//     (2026-11-04, spike/driver/docs/capture-findings.md), marked "captured". The
//     longer real stream is tests/fixtures/luma_imu_real.bin, used by real_capture_test.
#include "luma/protocol.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <new>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

// Counts every allocation in this program, so the real-time path's claim to make
// none is measured rather than promised.
static std::size_t g_allocations = 0;
void* operator new(std::size_t size) {
    ++g_allocations;
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__func__) + ":" + std::to_string(__LINE__) + ": " #x); } while (0)
using namespace luma;
using Bytes = std::vector<std::uint8_t>;

// The daemon stores the parser as this plain pointer (daemon.hpp, Parser), so
// the name must not be overloaded. The hot path must not be able to throw.
using ParserPointer = std::optional<ImuSample> (*)(std::span<const std::uint8_t>);
[[maybe_unused]] constexpr ParserPointer kParserPointerFits = &luma::parse_imu;
static_assert(noexcept(parse_imu(std::span<const std::uint8_t>{})));
static_assert(noexcept(parse_imu_with(std::span<const std::uint8_t>{}, std::declval<const ParseOptions&>())));
static_assert(noexcept(classify(std::span<const std::uint8_t>{})));
static_assert(noexcept(parse_data_frame(std::span<const std::uint8_t>{})));
static_assert(noexcept(crc16_xmodem(std::span<const std::uint8_t>{})));
static_assert(noexcept(byte_sum16(std::span<const std::uint8_t>{})));
static_assert(noexcept(std::declval<SequenceTracker&>().observe(std::declval<ImuSample&>())));

namespace {

Bytes from_hex(const std::string& hex) {
    Bytes out;
    for (std::size_t i = 0; i + 1 < hex.size(); i += 2)
        out.push_back(std::uint8_t(std::stoi(hex.substr(i, 2), nullptr, 16)));
    return out;
}

bool near(float a, float b, float eps = 1e-5f) { return std::fabs(a - b) <= eps; }
void put_le16(Bytes& b, std::size_t at, std::uint16_t v) {
    b[at] = std::uint8_t(v);
    b[at + 1] = std::uint8_t(v >> 8);
}
void put_float(Bytes& b, std::size_t at, float v, bool big_endian = false) {
    const auto u = std::bit_cast<std::uint32_t>(v);
    for (std::size_t i = 0; i < 4; ++i)
        b[at + (big_endian ? 3 - i : i)] = std::uint8_t(u >> (8 * i));
}
void put_u32(Bytes& b, std::size_t at, std::uint32_t v, bool big_endian = false) {
    for (std::size_t i = 0; i < 4; ++i)
        b[at + (big_endian ? 3 - i : i)] = std::uint8_t(v >> (8 * i));
}

// A V2 raw event. Gyro and timestamp are invented; the accelerometer triple is
// the one viture-v2 PROTOCOL.md prints from a real Pro 2 (magnitude 0.983 g),
// and 117.30, 60.75, 254.85 are its three unexplained constants.
const Bytes kV2Golden = {
    0x10, 0x00, 0x09, 0x73, 0x38, 0x00, 0x5f, 0x19, 0x34, 0x12, 0x00, 0x00, 0xe8, 0x03, 0x00, 0x00,
    0xba, 0x00, 0x0a, 0xd7, 0x23, 0x3c, 0x0a, 0xd7, 0xa3, 0xbc, 0x8f, 0xc2, 0xf5, 0x3c, 0x52, 0x49,
    0x1d, 0x3b, 0x4b, 0xea, 0xa4, 0xbe, 0x48, 0xbf, 0x6d, 0xbf, 0x9a, 0x99, 0xea, 0x42, 0x00, 0x00,
    0x73, 0x42, 0x9a, 0xd9, 0x7e, 0x43, 0xa0, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9};

Bytes v2_frame(float gyro_x, float accel_x, std::uint32_t ticks = 7) {
    Bytes f = kV2Golden;
    put_u32(f, 8 + 4, ticks);
    put_float(f, 8 + 10, gyro_x);
    put_float(f, 8 + 22, accel_x);
    put_le16(f, 6, byte_sum16(std::span(f).subspan(8)));
    return f;
}

// A data-envelope frame from the device, CRC included.
Bytes device_frame(std::uint8_t sync, std::uint16_t msg, const Bytes& body, std::size_t packet = 512) {
    Bytes f(std::max<std::size_t>(packet, 18 + body.size()), 0);
    f[0] = 0xff;
    f[1] = sync;
    const auto pklen = std::uint16_t(12 + body.size());
    put_le16(f, 4, pklen);
    put_u32(f, 6, 0xAABBCCDDu);
    put_le16(f, 14, msg);
    std::copy(body.begin(), body.end(), f.begin() + 18);
    put_le16(f, 2, crc16_xmodem(std::span(f).subspan(4, 2u + pklen)));
    return f;
}

// An invented layout: the Ultra's real one is unknown. It exists only to prove
// the table-driven path, and uses the shape of the SDK's callback (accelerometer
// first, in m/s2).
constexpr ImuFields kSyntheticFields{
    .msg_id = 0x0abc,
    .big_endian = true,
    .timestamp_offset = 0,
    .gyro_offset = 28,
    .accel_offset = 16,
    .gyro_to_rad_s = 0.5f,
    .accel_to_m_s2 = 1.0f,
    .tick_ns = 2'000'000,
};
ParseOptions synthetic_options() {
    ParseOptions o;
    o.luma_ultra = kSyntheticFields;
    return o;
}
Bytes synthetic_body(bool big_endian) {
    Bytes body(40, 0);
    put_u32(body, 0, 12345, big_endian);
    put_float(body, 16, 0.25f, big_endian);
    put_float(body, 20, 9.5f, big_endian);
    put_float(body, 24, -1.0f, big_endian);
    put_float(body, 28, 2.0f, big_endian);
    put_float(body, 32, -4.0f, big_endian);
    put_float(body, 36, 8.0f, big_endian);
    return body;
}

void test_checks_match_published_values() {
    const std::string s = "123456789";
    CHECK(crc16_xmodem(std::span(reinterpret_cast<const std::uint8_t*>(s.data()), s.size())) == 0x31C3);
    CHECK(crc16_xmodem({}) == 0);
    // viture-v2 PROTOCOL.md: the payload 01 02 sums to 0x0003.
    const Bytes p = {0x01, 0x02};
    CHECK(byte_sum16(p) == 0x0003);
    // Mod 2^16, so 257 bytes of 0xFF wrap past 65535.
    CHECK(byte_sum16(Bytes(257, 0xff)) == std::uint16_t(257 * 255));
}

void test_data_request_golden() {
    // Layout from go-viture info.go; bytes computed independently.
    const auto r = data_request(0x0001);
    CHECK(r);
    CHECK(r->endpoint == 0x06);
    CHECK(r->bytes.size() == 512);
    const Bytes head = {0xff, 0xfe, 0x7a, 0x60, 0x0c, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0x01, 0x00, 0, 0};
    CHECK(std::equal(head.begin(), head.end(), r->bytes.begin()));
    CHECK(std::all_of(r->bytes.begin() + 18, r->bytes.end(), [](auto b) { return b == 0; }));

    const Bytes one = {0x01};
    const auto with_body = data_request(0x0015, one);
    CHECK(with_body);
    const Bytes head2 = {0xff, 0xfe, 0x27, 0x25, 0x0d, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0x15, 0x00, 0, 0, 0x01, 0};
    CHECK(std::equal(head2.begin(), head2.end(), with_body->bytes.begin()));
}

void test_data_request_matches_vendor_frames() {
    // Real frames from VITURE's own updater, kept in go-viture info_test.go.
    const struct { std::uint16_t msg; const char* head; } frames[] = {
        {0x0001, "fffe7a600c0000000000000000000100"},
        {0x0010, "fffe690d0c0000000000000000001000"},
        {0x0011, "fffedd7b0c0000000000000000001100"},
        {0x000b, "fffed1080c0000000000000000000b00"},
        {0x0063, "fffec0d40c0000000000000000006300"},
    };
    for (const auto& f : frames) {
        const auto r = data_request(f.msg);
        CHECK(r);
        const Bytes want = from_hex(f.head);
        CHECK(std::equal(want.begin(), want.end(), r->bytes.begin()));
        CHECK(classify(r->bytes).intact);
    }
}

void test_data_request_sizes_match_the_sdk_log() {
    // What the SDK's own builder logged on Cody's glasses, in driver.log:
    //   MsgID: 0x0007, PayloadLen: 0, DataLen: 12, PacketLen: 512
    //   MsgID: 0x100B, PayloadLen: 1, DataLen: 13, PacketLen: 512
    // Both are built only to compare sizes: neither is a command we send, and
    // the 0x100B value is unknown, so the byte here is a placeholder.
    const auto display_mode = data_request(0x0007);
    CHECK(display_mode);
    CHECK(display_mode->bytes.size() == 512);
    CHECK(display_mode->bytes[4] == 12 && display_mode->bytes[5] == 0);
    // CRC computed independently; drv-protocol-research's tool gets the same.
    const Bytes want = from_hex("fffee3470c0000000000000000000700");
    CHECK(std::equal(want.begin(), want.end(), display_mode->bytes.begin()));

    const Bytes one_byte = {0x01};
    const auto status_report = data_request(0x100B, one_byte);
    CHECK(status_report);
    CHECK(status_report->bytes.size() == 512);
    CHECK(status_report->bytes[4] == 13 && status_report->bytes[5] == 0);
}

void test_real_luma_ultra_reply_passes_every_check() {
    // Sent by a real Luma Ultra on 2026-09-07, kept in go-viture info_test.go. It
    // proves that device replies carry the CRC this code checks: go-viture
    // itself never tests it.
    const Bytes reply =
        from_hex("fffdaf0f210000000000763e42000100000000" "31322e302e30312e3130315f3230323630363035");
    const auto info = classify(reply);
    CHECK(info.kind == FrameKind::data_reply);
    CHECK(info.msg_id == 0x0001);
    CHECK(info.intact);
    const auto frame = parse_data_frame(reply);
    CHECK(frame);
    CHECK(frame->body.size() == 21);
    CHECK(frame->body[0] == 0x00);  // status: success
    const std::string text(frame->body.begin() + 1, frame->body.end());
    CHECK(text == "12.0.01.101_20260605");
    // Padded to the packet size, as it arrives on the wire.
    Bytes padded = reply;
    padded.resize(kDataPacketSize, 0);
    CHECK(parse_data_frame(padded));
}

void test_hot_path_never_allocates() {
    const auto options = synthetic_options();
    const Bytes good = kV2Golden;
    Bytes bad = kV2Golden;
    bad[20] ^= 1;
    const Bytes data = device_frame(0xfc, kSyntheticFields.msg_id, synthetic_body(true));
    const Bytes* const packets[] = {&good, &bad, &data};
    SequenceTracker tracker;
    const std::size_t before = g_allocations;
    std::uint64_t seen = 0;
    for (int i = 0; i < 1000; ++i) {
        for (const Bytes* packet : packets) {
            auto sample = parse_imu_with(*packet, options);
            (void)classify(*packet);
            (void)parse_data_frame(*packet);
            if (sample) seen += tracker.observe(*sample).lost;
        }
    }
    const std::size_t during = g_allocations - before;
    CHECK(during == 0);
    // A command does allocate, which shows the counter can see one.
    CHECK(data_request(1));
    CHECK(g_allocations > before);
    (void)seen;
}

void test_data_request_refuses_oversize_payload() {
    CHECK(data_request(1, Bytes(512 - 18, 0)));
    CHECK(!data_request(1, Bytes(512 - 18 + 1, 0)));
}

void test_data_frame_round_trip() {
    const auto req = data_request(0x0010);
    const auto parsed_req = parse_data_frame(req->bytes);
    CHECK(parsed_req);
    CHECK(parsed_req->kind == FrameKind::data_request);
    CHECK(parsed_req->msg_id == 0x0010);
    CHECK(parsed_req->body.empty());

    const auto reply = device_frame(0xfd, 0x0001, {0x00, 'o', 'k'});
    const auto p = parse_data_frame(reply);
    CHECK(p);
    CHECK(p->kind == FrameKind::data_reply);
    CHECK(p->counter == 0xAABBCCDDu);
    CHECK(p->body.size() == 3);
    CHECK(p->body[0] == 0x00 && p->body[2] == 'k');
    CHECK(parse_data_frame(device_frame(0xfc, 2, {1}))->kind == FrameKind::data_stream);
}

void test_data_frame_rejects_damage() {
    auto f = device_frame(0xfd, 1, {0, 1, 2, 3});
    f[18] ^= 0x01;
    CHECK(!parse_data_frame(f));

    auto crc = device_frame(0xfd, 1, {0, 1, 2, 3});
    crc[2] ^= 0x80;
    CHECK(!parse_data_frame(crc));

    // A length that runs past the packet, and one too short to hold a header.
    auto too_long = device_frame(0xfd, 1, {}, 64);
    put_le16(too_long, 4, 60);
    CHECK(!parse_data_frame(too_long));
    auto too_short = device_frame(0xfd, 1, {}, 64);
    put_le16(too_short, 4, 11);
    put_le16(too_short, 2, crc16_xmodem(std::span(too_short).subspan(4, 13)));
    CHECK(!parse_data_frame(too_short));

    // A good frame cut short inside a larger buffer: its CRC would still match
    // if read past the end of the span, so only the length bound can refuse it.
    const auto whole = device_frame(0xfd, 1, Bytes(20, 0x5a), 128);
    CHECK(parse_data_frame(whole));
    CHECK(parse_data_frame(std::span(whole).first(38)));
    CHECK(!parse_data_frame(std::span(whole).first(37)));

    // Bytes after the CRC'd part are padding, so damage there is not damage.
    auto padded = device_frame(0xfd, 1, {0});
    padded[300] = 0x55;
    CHECK(parse_data_frame(padded));

    auto sync = device_frame(0xfd, 1, {0});
    sync[1] = 0xfb;
    CHECK(!parse_data_frame(sync));
    CHECK(!parse_data_frame({}));
}

void test_v2_golden_packet() {
    const auto s = parse_imu(kV2Golden);
    CHECK(s);
    CHECK(s->seq == 1000);
    CHECK(s->t_ns == 1'000'000'000ull);
    CHECK(near(s->gyro_rad_s[0], 0.01f) && near(s->gyro_rad_s[1], -0.02f) && near(s->gyro_rad_s[2], 0.03f));
    CHECK(near(s->accel_m_s2[0], 0.0024f * 9.80665f));
    CHECK(near(s->accel_m_s2[1], -0.3221f * 9.80665f));
    CHECK(near(s->accel_m_s2[2], -0.9287f * 9.80665f));
    CHECK(!s->mag);
    // viture-v2's own cross-check: this reading has a magnitude of 0.983 g.
    const float m = std::hypot(s->accel_m_s2[0], s->accel_m_s2[1], s->accel_m_s2[2]);
    CHECK(near(m / kStandardGravity, 0.983f, 0.001f));
}

void test_v2_padding_is_ignored() {
    Bytes padded = kV2Golden;
    padded.resize(128, 0x77);
    CHECK(parse_imu(padded));
}

void test_v2_tick_length_is_configurable() {
    ParseOptions o;
    o.v2.tick_ns = 8'333'333;  // 120 Hz
    CHECK(parse_imu_with(kV2Golden, o)->t_ns == 8'333'333'000ull);
}

void test_every_truncation_is_rejected() {
    for (std::size_t n = 0; n < kV2Golden.size(); ++n)
        CHECK(!parse_imu(std::span(kV2Golden).first(n)));
}

void test_every_single_bit_flip_is_rejected() {
    for (std::size_t bit = 0; bit < kV2Golden.size() * 8; ++bit) {
        Bytes f = kV2Golden;
        f[bit / 8] ^= std::uint8_t(1u << (bit % 8));
        CHECK(!parse_imu(f));
    }
}

void test_other_messages_are_not_samples() {
    // The fused pose event 0x7308 has an intact frame but is not raw data.
    Bytes pose(8 + 24, 0);
    pose[0] = 0x10;
    put_le16(pose, 2, 0x7308);
    put_le16(pose, 4, 24);
    pose[20] = 0x3f;
    put_le16(pose, 6, byte_sum16(std::span(pose).subspan(8)));
    CHECK(classify(pose).intact);
    CHECK(!parse_imu(pose));

    // The fields end at byte 34 of the payload: one byte less must be refused,
    // with a correct check, so that only the length can be the reason.
    const auto raw_of = [](std::uint16_t length) {
        Bytes f(8u + length, 1);
        f[0] = 0x10;
        f[1] = 0x00;
        put_le16(f, 2, 0x7309);
        put_le16(f, 4, length);
        put_le16(f, 6, byte_sum16(std::span(f).subspan(8)));
        return f;
    };
    CHECK(parse_imu(raw_of(34)));
    CHECK(!parse_imu(raw_of(33)));
}

void test_impossible_readings_are_rejected() {
    const float inf = std::numeric_limits<float>::infinity();
    const float nan = std::numeric_limits<float>::quiet_NaN();
    CHECK(parse_imu(v2_frame(1.0f, 0.5f)));
    CHECK(!parse_imu(v2_frame(nan, 0.5f)));
    CHECK(!parse_imu(v2_frame(inf, 0.5f)));
    CHECK(!parse_imu(v2_frame(-inf, 0.5f)));
    CHECK(!parse_imu(v2_frame(1.0f, nan)));
    CHECK(!parse_imu(v2_frame(1.0f, inf)));
    CHECK(!parse_imu(v2_frame(kMaxGyroRadS + 1.0f, 0.5f)));
    CHECK(!parse_imu(v2_frame(-kMaxGyroRadS - 1.0f, 0.5f)));
    // In range as g on the wire, past the limit once scaled. Also the case
    // where scaling, not the raw value, is what overflows.
    CHECK(!parse_imu(v2_frame(1.0f, kMaxAccelMs2)));
    CHECK(!parse_imu(v2_frame(1.0f, std::numeric_limits<float>::max())));
}

void test_blank_frame_is_rejected() {
    // Zeros sum to zero, so a blank payload passes a byte sum.
    Bytes blank(8 + 56, 0);
    blank[0] = 0x10;
    put_le16(blank, 2, 0x7309);
    put_le16(blank, 4, 56);
    CHECK(classify(blank).intact);
    CHECK(!parse_imu(blank));
}

void test_luma_ultra_frames_decode_to_nothing_until_a_capture_settles_them() {
    CHECK(!kLumaUltraImuFields.has_value());
    CHECK(!parse_imu(device_frame(0xfc, 0x0abc, synthetic_body(true))));
    CHECK(!parse_imu(device_frame(0xfd, 0x0abc, synthetic_body(true))));
}

void test_data_envelope_decoder_big_endian() {
    const auto s = parse_imu_with(device_frame(0xfc, kSyntheticFields.msg_id, synthetic_body(true)), synthetic_options());
    CHECK(s);
    CHECK(s->seq == 12345);
    CHECK(s->t_ns == 24'690'000'000ull);
    CHECK(s->accel_m_s2 == (std::array<float, 3>{0.25f, 9.5f, -1.0f}));
    CHECK(s->gyro_rad_s == (std::array<float, 3>{1.0f, -2.0f, 4.0f}));
}

void test_data_envelope_decoder_little_endian() {
    auto o = synthetic_options();
    o.luma_ultra->big_endian = false;
    const auto s = parse_imu_with(device_frame(0xfd, kSyntheticFields.msg_id, synthetic_body(false)), o);
    CHECK(s);
    CHECK(s->seq == 12345);
    CHECK(s->accel_m_s2 == (std::array<float, 3>{0.25f, 9.5f, -1.0f}));
}

void test_data_envelope_decoder_rejections() {
    const auto o = synthetic_options();
    CHECK(!parse_imu_with(device_frame(0xfc, 0x0abd, synthetic_body(true)), o));  // another message
    Bytes short_body = synthetic_body(true);
    short_body.resize(39);
    CHECK(!parse_imu_with(device_frame(0xfc, kSyntheticFields.msg_id, short_body), o));
    auto damaged = device_frame(0xfc, kSyntheticFields.msg_id, synthetic_body(true));
    damaged[20] ^= 0x10;
    CHECK(!parse_imu_with(damaged, o));
    // A host request is ours, never a reading, even with the right message id.
    auto ours = device_frame(0xfe, kSyntheticFields.msg_id, synthetic_body(true));
    CHECK(parse_data_frame(ours));
    CHECK(!parse_imu_with(ours, o));
}

void test_classify() {
    CHECK(classify(kV2Golden).kind == FrameKind::v2);
    CHECK(classify(kV2Golden).msg_id == 0x7309);
    CHECK(classify(kV2Golden).intact);

    Bytes bad = kV2Golden;
    bad[20] ^= 1;
    CHECK(classify(bad).kind == FrameKind::v2);
    CHECK(!classify(bad).intact);

    auto reply = device_frame(0xfd, 0x0007, {0, 1});
    CHECK(classify(reply).kind == FrameKind::data_reply);
    CHECK(classify(reply).msg_id == 0x0007);
    CHECK(classify(reply).intact);
    // A wrong CRC is still worth naming: on the real device this is how a
    // mistaken CRC assumption would show up in a capture.
    reply[2] ^= 1;
    CHECK(classify(reply).kind == FrameKind::data_reply);
    CHECK(classify(reply).msg_id == 0x0007);
    CHECK(!classify(reply).intact);

    CHECK(classify(device_frame(0xfc, 3, {1})).kind == FrameKind::data_stream);
    CHECK(classify(*&data_request(1)->bytes).kind == FrameKind::data_request);
    CHECK(classify({}).kind == FrameKind::unknown);
    CHECK(classify(Bytes{0xff}).kind == FrameKind::unknown);
    CHECK(classify(Bytes{0xff, 0xfd, 0, 0}).kind == FrameKind::unknown);
    CHECK(classify(Bytes(64, 0x42)).kind == FrameKind::unknown);
}

void test_v2_commands_match_published_bytes() {
    const auto start = start_imu(Device::v2, ImuRate::hz120);
    CHECK(start);
    // viture-v2 PROTOCOL.md: "raw stream, 120 Hz".
    CHECK(start->bytes == (Bytes{0x10, 0x00, 0x01, 0x03, 0x02, 0x00, 0x04, 0x00, 0x02, 0x02}));
    CHECK(start->endpoint == kV2EndpointOut);
    const auto stop = stop_imu(Device::v2);
    CHECK(stop);
    // "both off".
    CHECK(stop->bytes == (Bytes{0x10, 0x00, 0x01, 0x03, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00}));
    // The default is the Luma Ultra's 1 kHz, and our own frame must parse back.
    const auto fast = start_imu(Device::v2);
    CHECK(fast->bytes[9] == 5);
    CHECK(classify(fast->bytes).intact);
    CHECK(classify(fast->bytes).msg_id == 0x0301);
}

void test_luma_ultra_start_is_the_captured_bytes_and_stop_stays_unknown() {
    const auto start = start_imu(Device::luma_ultra);
    CHECK(start);
    CHECK(start->endpoint == 0x04);
    CHECK(start->bytes == Bytes({0xfa, 0x55, 0x95, 0x00, 0x00}));  // captured, 5.526 s into the session
    CHECK(start_imu(Device::luma_ultra, ImuRate::hz60)->bytes == start->bytes);  // nothing sets a rate on this device
    CHECK(mcu_request(kMcuBeforeStart)->bytes == Bytes({0xfa, 0x55, 0x97, 0x00, 0x00}));  // captured
    const std::array<std::uint8_t, 2> exposure{0x06, 0xc0};
    CHECK(mcu_request(0x90, exposure)->bytes == Bytes({0xfa, 0x55, 0x90, 0x00, 0x02, 0x06, 0xc0}));  // captured framing
    CHECK(!stop_imu(Device::luma_ultra));  // no capture has shown one
}

Bytes hex(const char* text) {
    Bytes out;
    for (const char* p = text; *p;) {
        if (*p == ' ') { ++p; continue; }
        out.push_back(std::uint8_t(std::stoi(std::string(p, 2), nullptr, 16)));
        p += 2;
    }
    return out;
}

constexpr float kGyroLsb = 3.14159265358979f / 180.0f / 16.4f;
constexpr float kAccelLsb = 9.80665f / 8192.0f;
bool close_to(float a, float b) { return std::fabs(a - b) < 1e-5f; }

void test_captured_imu_packet_decodes_to_the_body_frame() {
    // captured: the first IMU packet of the session, glasses in Cody's hands.
    const auto s = parse_imu(hex("aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00"));
    CHECK(s);
    CHECK(s->seq == 0x153c);
    CHECK(s->t_ns == 0x11edd * 100'000ull);
    // wire gyro -12, 5, 36 and accel -235, -2031, 8052. Forward = -y, left = +x, up = +z.
    CHECK(close_to(s->gyro_rad_s[0], -5 * kGyroLsb));
    CHECK(close_to(s->gyro_rad_s[1], -12 * kGyroLsb));
    CHECK(close_to(s->gyro_rad_s[2], 36 * kGyroLsb));
    CHECK(close_to(s->accel_m_s2[0], 2031 * kAccelLsb));
    CHECK(close_to(s->accel_m_s2[1], -235 * kAccelLsb));
    CHECK(close_to(s->accel_m_s2[2], 8052 * kAccelLsb));
    CHECK(!s->mag);
    const float g = std::hypot(s->accel_m_s2[0], s->accel_m_s2[1], s->accel_m_s2[2]);
    CHECK(g > 9.5f && g < 10.2f);  // the check a wrong layout fails: about 9.8 at rest
}

void test_captured_mid_session_packets() {
    // captured: 506 s in, 1 ms apart; the temperature word has climbed.
    const auto a = parse_imu(hex("aa 8f 00 88 eb b8 10 00 f0 89 4d 00 00 00 f4 ff 04 00 09 ff 96 02 17 20 98 0a 00 00"));
    const auto b = parse_imu(hex("aa 8f 00 88 ec b8 10 00 fa 89 4d 00 00 00 f3 ff 03 00 1e ff 8c 02 1b 20 98 0a 00 00"));
    CHECK(a && b);
    CHECK(a->seq + 1 == b->seq);
    CHECK(b->t_ns - a->t_ns == 1'000'000);
}

void test_stream_packets_that_are_not_imu_are_refused() {
    // captured: a display event from 0x83 and a camera tile header from 0x82.
    CHECK(!parse_imu(hex("aa 8f 00 91 00 00 00 00 58 1e 01 00 00 00 00 00")));
    CHECK(!parse_imu(hex("aa 8f 00 12 01 00 f4 3f d2 20 01 00 11 0e 14 14 11 0f 10 14 15 0e 11 0d 10 12 0e 13")));
    // captured: the MCU's reply to the start.
    CHECK(!parse_imu(hex("fa 55 95 00 00 00")));
    auto good = hex("aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00");
    for (std::size_t n = 0; n < good.size(); ++n) CHECK(!parse_imu(std::span(good).first(n)));
    // An all-zero accelerometer is a blank packet, and the length field must say 16.
    CHECK(!parse_imu(hex("aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 00 00 00 00 00 00 e8 06 00 00")));
    CHECK(!parse_imu(hex("aa 8f 00 88 3c 15 0f 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00")));
}

void test_luma_encoder_is_the_parser_inverse() {
    const auto real = hex("aa 8f 00 88 3c 15 10 00 dd 1e 01 00 f4 ff 05 00 24 00 15 ff 11 f8 74 1f e8 06 00 00");
    const auto s = parse_imu(real);
    CHECK(s);
    const auto back = encode_luma_imu(*s, 0x6e8);
    CHECK(Bytes(back.begin(), back.end()) == real);
}

void test_captured_envelope_frames_match_our_builder() {
    // captured: the old driver's two data-channel messages, byte for byte over the first 48.
    const auto status = data_request(0x100B, std::array<std::uint8_t, 1>{0x01});
    CHECK(status);
    const auto want = hex("ff fe 72 f5 0d 00 00 00 00 00 00 00 00 00 0b 10 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00");
    CHECK(std::equal(want.begin(), want.end(), status->bytes.begin()));
    const auto mode = data_request(0x0007);
    const auto want_mode = hex("ff fe e3 47 0c 00 00 00 00 00 00 00 00 00 07 00 00 00 00 00");
    CHECK(mode && std::equal(want_mode.begin(), want_mode.end(), mode->bytes.begin()));
    // captured replies, with a device timer in the counter field and the status in the body.
    const auto reply = hex("ff fd a0 cf 0e 00 00 00 00 00 fb 16 00 00 0b 10 00 00 00 01");
    const auto frame = parse_data_frame(reply);
    CHECK(frame && frame->msg_id == 0x100B && (frame->counter >> 32) == 0x16fb);
    const auto mode_reply = hex("ff fd b2 30 0e 00 00 00 00 00 e7 1a 00 00 07 00 00 00 00 31");
    const auto mode_frame = parse_data_frame(mode_reply);
    CHECK(mode_frame && mode_frame->msg_id == 0x0007 && mode_frame->body[1] == 0x31);
}

void test_tracker_widens_the_16_bit_sequence() {
    auto tracker = luma_sequence_tracker();
    ImuSample first{};
    first.seq = 65534;
    first.t_ns = 1;
    CHECK(tracker.observe(first).verdict == SequenceTracker::Verdict::accepted);
    const std::uint32_t base = first.seq;
    for (std::uint32_t i = 1; i <= 5; ++i) {
        ImuSample s{};
        s.seq = (65534 + i) & 0xffff;
        const auto r = tracker.observe(s);
        CHECK(r.verdict == SequenceTracker::Verdict::accepted && r.lost == 0 && !r.restarted);
        CHECK(s.seq == base + i);  // does not wrap
        CHECK(s.t_ns == std::uint64_t(base + i) * 1'000'000ull);
    }
    ImuSample repeat{};
    repeat.seq = 3;
    CHECK(tracker.observe(repeat).verdict == SequenceTracker::Verdict::duplicate);  // the session's first 63 packets come twice
    ImuSample gap{};
    gap.seq = 3 + 36;
    CHECK(tracker.observe(gap).lost == 35);
    CHECK(tracker.lost_total() == 35);
}

ImuSample at(std::uint32_t seq) {
    ImuSample s{};
    s.seq = seq;
    return s;
}

void test_tracker_in_order() {
    SequenceTracker t;
    auto a = at(100), b = at(101);
    CHECK(t.observe(a).verdict == SequenceTracker::Verdict::accepted);
    CHECK(a.t_ns == 100'000'000);
    const auto r = t.observe(b);
    CHECK(r.verdict == SequenceTracker::Verdict::accepted);
    CHECK(r.lost == 0 && !r.restarted);
    CHECK(b.t_ns == 101'000'000);
}

void test_tracker_counts_gaps() {
    SequenceTracker t;
    auto a = at(10), b = at(14), c = at(16);
    t.observe(a);
    CHECK(t.observe(b).lost == 3);
    CHECK(t.observe(c).lost == 1);
    CHECK(t.lost_total() == 4);
    CHECK(c.t_ns == 16'000'000);
}

void test_tracker_rejects_duplicate_and_late_packets() {
    SequenceTracker t;
    auto a = at(1000), dup = at(1000), late = at(990), next = at(1001);
    const std::uint64_t untouched = 777;
    dup.t_ns = untouched;
    t.observe(a);
    CHECK(t.observe(dup).verdict == SequenceTracker::Verdict::duplicate);
    CHECK(dup.t_ns == untouched);
    CHECK(t.observe(late).verdict == SequenceTracker::Verdict::stale);
    // A late packet must not move the reference: the next one is still in order.
    const auto r = t.observe(next);
    CHECK(r.verdict == SequenceTracker::Verdict::accepted);
    CHECK(r.lost == 0);
}

void test_tracker_survives_32_bit_wrap() {
    SequenceTracker t;
    auto a = at(0xFFFFFFFFu), b = at(0), c = at(2);
    t.observe(a);
    const auto r = t.observe(b);
    CHECK(r.verdict == SequenceTracker::Verdict::accepted);
    CHECK(r.lost == 0 && !r.restarted);
    CHECK(b.t_ns == (1ull << 32) * 1'000'000ull);
    CHECK(t.observe(c).lost == 1);
    CHECK(c.t_ns > b.t_ns);
}

void test_tracker_device_restart_keeps_time_moving_forward() {
    SequenceTracker t;
    auto a = at(5'000'000), b = at(3), c = at(4);
    t.observe(a);
    const auto r = t.observe(b);
    CHECK(r.verdict == SequenceTracker::Verdict::accepted);
    CHECK(r.restarted);
    CHECK(r.lost == 0);
    CHECK(b.t_ns > a.t_ns);
    t.observe(c);
    CHECK(c.t_ns == b.t_ns + 1'000'000);
}

void test_tracker_reset_for_a_reconnect() {
    SequenceTracker t;
    auto a = at(100), b = at(100), c = at(95);
    t.observe(a);
    t.reset();
    // After a reconnect, even a repeat or a lower value is a fresh start.
    CHECK(t.observe(b).verdict == SequenceTracker::Verdict::accepted);
    CHECK(b.t_ns > a.t_ns);
    t.reset();
    CHECK(t.observe(c).verdict == SequenceTracker::Verdict::accepted);
    CHECK(c.t_ns > b.t_ns);
    CHECK(t.lost_total() == 0);
}

void check_sane(const std::optional<ImuSample>& s) {
    if (!s) return;
    for (const float v : s->gyro_rad_s) CHECK(std::isfinite(v) && std::fabs(v) <= kMaxGyroRadS);
    for (const float v : s->accel_m_s2) CHECK(std::isfinite(v) && std::fabs(v) <= kMaxAccelMs2);
}

void test_fuzz_random_bytes() {
    std::mt19937_64 rng(0x1104);
    const auto o = synthetic_options();
    std::array<std::uint8_t, 600> buf{};
    for (int i = 0; i < 400'000; ++i) {
        const std::size_t n = rng() % (buf.size() + 1);
        for (std::size_t k = 0; k < n; ++k) buf[k] = std::uint8_t(rng());
        const auto packet = std::span<const std::uint8_t>(buf).first(n);
        check_sane(parse_imu(packet));
        check_sane(parse_imu_with(packet, o));
        (void)classify(packet);
        (void)parse_data_frame(packet);
    }
}

// Random bytes almost never get past a check, so the decoders above barely run.
// These frames carry a correct check, so every float reaches the range tests.
void test_fuzz_past_the_checks() {
    std::mt19937_64 rng(0x1301);
    const auto o = synthetic_options();
    int decoded = 0;
    for (int i = 0; i < 200'000; ++i) {
        Bytes f = kV2Golden;
        for (std::size_t k = 8; k < f.size(); ++k) f[k] = std::uint8_t(rng());
        put_le16(f, 6, byte_sum16(std::span(f).subspan(8)));
        const auto a = parse_imu(f);
        check_sane(a);
        decoded += a.has_value();

        Bytes body(40);
        for (auto& b : body) b = std::uint8_t(rng());
        const auto b = parse_imu_with(device_frame(0xfc, kSyntheticFields.msg_id, body, 64), o);
        check_sane(b);
        decoded += b.has_value();
    }
    // Most random floats are out of range, but some must get through, or this
    // test would prove nothing about the path past the range tests.
    CHECK(decoded > 0);
}

void test_fuzz_mutations_of_valid_frames() {
    std::mt19937_64 rng(0x35ca);
    const auto o = synthetic_options();
    const Bytes data = device_frame(0xfc, kSyntheticFields.msg_id, synthetic_body(true), 64);
    for (int i = 0; i < 200'000; ++i) {
        Bytes f = rng() % 2 ? kV2Golden : data;
        for (unsigned m = 0; m < 1 + rng() % 4; ++m) f[rng() % f.size()] = std::uint8_t(rng());
        f.resize(rng() % (f.size() + 1));
        check_sane(parse_imu_with(f, o));
        (void)classify(f);
    }
}

void test_fuzz_tracker_time_never_goes_backwards() {
    std::mt19937_64 rng(42);
    SequenceTracker t;
    std::uint64_t last = 0;
    std::uint32_t seq = 0;
    for (int i = 0; i < 200'000; ++i) {
        seq += std::uint32_t(rng() % 5) - 1;
        if (rng() % 2000 == 0) seq = std::uint32_t(rng());
        if (rng() % 5000 == 0) t.reset();
        auto s = at(seq);
        if (t.observe(s).verdict == SequenceTracker::Verdict::accepted) {
            CHECK(s.t_ns >= last);
            last = s.t_ns;
        }
    }
}

}  // namespace

int main() {
    const struct { const char* name; void (*fn)(); } tests[] = {
#define T(n) {#n, n}
        T(test_checks_match_published_values),
        T(test_data_request_golden),
        T(test_data_request_matches_vendor_frames),
        T(test_data_request_sizes_match_the_sdk_log),
        T(test_real_luma_ultra_reply_passes_every_check),
        T(test_hot_path_never_allocates),
        T(test_data_request_refuses_oversize_payload),
        T(test_data_frame_round_trip),
        T(test_data_frame_rejects_damage),
        T(test_v2_golden_packet),
        T(test_v2_padding_is_ignored),
        T(test_v2_tick_length_is_configurable),
        T(test_every_truncation_is_rejected),
        T(test_every_single_bit_flip_is_rejected),
        T(test_other_messages_are_not_samples),
        T(test_impossible_readings_are_rejected),
        T(test_blank_frame_is_rejected),
        T(test_luma_ultra_frames_decode_to_nothing_until_a_capture_settles_them),
        T(test_data_envelope_decoder_big_endian),
        T(test_data_envelope_decoder_little_endian),
        T(test_data_envelope_decoder_rejections),
        T(test_classify),
        T(test_v2_commands_match_published_bytes),
        T(test_luma_ultra_start_is_the_captured_bytes_and_stop_stays_unknown),
        T(test_captured_imu_packet_decodes_to_the_body_frame),
        T(test_captured_mid_session_packets),
        T(test_stream_packets_that_are_not_imu_are_refused),
        T(test_luma_encoder_is_the_parser_inverse),
        T(test_captured_envelope_frames_match_our_builder),
        T(test_tracker_widens_the_16_bit_sequence),
        T(test_tracker_in_order),
        T(test_tracker_counts_gaps),
        T(test_tracker_rejects_duplicate_and_late_packets),
        T(test_tracker_survives_32_bit_wrap),
        T(test_tracker_device_restart_keeps_time_moving_forward),
        T(test_tracker_reset_for_a_reconnect),
        T(test_fuzz_random_bytes),
        T(test_fuzz_past_the_checks),
        T(test_fuzz_mutations_of_valid_frames),
        T(test_fuzz_tracker_time_never_goes_backwards),
#undef T
    };
    int failed = 0;
    for (const auto& t : tests) {
        try {
            t.fn();
            std::cout << "ok   " << t.name << "\n";
        } catch (const std::exception& e) {
            ++failed;
            std::cout << "FAIL " << e.what() << "\n";
        }
    }
    std::cout << (std::size(tests) - std::size_t(failed)) << "/" << std::size(tests) << " passed\n";
    return failed ? 1 : 0;
}
