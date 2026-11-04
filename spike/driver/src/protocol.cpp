// Layouts and their sources are in luma/protocol.hpp.
#include "luma/protocol.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>

namespace luma {
namespace {

using Bytes = std::span<const std::uint8_t>;

constexpr std::array<std::uint8_t, 2> kV2Preamble{0x10, 0x00};
constexpr std::size_t kV2HeaderSize = 8;
constexpr std::uint16_t kV2ImuControl = 0x0301;
constexpr std::uint8_t kV2StreamOff = 0x00;
constexpr std::uint8_t kV2StreamRaw = 0x02;  // on the wire; the SDK's own enum differs

constexpr float kDegToRad = 3.14159265358979f / 180.0f;
constexpr std::uint8_t kMcuSync0 = 0xfa;
constexpr std::uint8_t kMcuSync1 = 0x55;

constexpr std::uint8_t kSyncLead = 0xff;
constexpr std::uint8_t kSyncStream = 0xfc;
constexpr std::uint8_t kSyncReply = 0xfd;
constexpr std::uint8_t kSyncRequest = 0xfe;
constexpr std::size_t kCheckedFrom = 4;   // the CRC starts at the length field
constexpr std::size_t kLengthBase = 6;    // bytes before what pklen counts
constexpr std::size_t kMinPklen = 12;
constexpr std::size_t kCounterOffset = 6;
constexpr std::size_t kMsgIdOffset = 14;
constexpr std::size_t kBodyOffset = 18;

// One lookup per byte: a full packet is checked 1000 times a second.
constexpr std::array<std::uint16_t, 256> kCrcTable = [] {
    std::array<std::uint16_t, 256> table{};
    for (std::size_t i = 0; i < table.size(); ++i) {
        auto crc = std::uint16_t(i << 8);
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc & 0x8000) ? std::uint16_t((crc << 1) ^ 0x1021) : std::uint16_t(crc << 1);
        table[i] = crc;
    }
    return table;
}();

std::uint16_t read_le16(const std::uint8_t* p) {
    return std::uint16_t(p[0] | (p[1] << 8));
}
std::uint32_t read_u32(const std::uint8_t* p, bool big_endian) {
    std::uint32_t v = 0;
    for (std::size_t i = 0; i < 4; ++i) v |= std::uint32_t(p[big_endian ? 3 - i : i]) << (8 * i);
    return v;
}
float read_float(const std::uint8_t* p, bool big_endian) {
    return std::bit_cast<float>(read_u32(p, big_endian));
}
bool fits(std::size_t size, std::size_t offset, std::size_t count) {
    return count <= size && offset <= size - count;
}

// Scales three floats, or returns nothing if any result is not a usable number.
// The limit applies after scaling, because scaling itself can overflow.
std::optional<std::array<float, 3>> read_axes(const std::uint8_t* p, bool big_endian, float scale, float limit) {
    std::array<float, 3> out{};
    for (std::size_t i = 0; i < 3; ++i) {
        out[i] = read_float(p + 4 * i, big_endian) * scale;
        if (!std::isfinite(out[i]) || std::fabs(out[i]) > limit) return std::nullopt;
    }
    return out;
}

std::optional<ImuSample> decode(Bytes payload, const ImuFields& f) {
    if (!fits(payload.size(), f.timestamp_offset, 4) || !fits(payload.size(), f.gyro_offset, 12) ||
        !fits(payload.size(), f.accel_offset, 12))
        return std::nullopt;
    const auto gyro = read_axes(payload.data() + f.gyro_offset, f.big_endian, f.gyro_to_rad_s, kMaxGyroRadS);
    const auto accel = read_axes(payload.data() + f.accel_offset, f.big_endian, f.accel_to_m_s2, kMaxAccelMs2);
    if (!gyro || !accel) return std::nullopt;
    // An accelerometer never reads exactly zero on all axes, and a blank frame
    // passes a byte sum, so this is what catches zeroed packets.
    if (std::all_of(accel->begin(), accel->end(), [](float v) { return v == 0.0f; })) return std::nullopt;
    const std::uint32_t ticks = read_u32(payload.data() + f.timestamp_offset, f.big_endian);
    return ImuSample{std::uint64_t(ticks) * f.tick_ns, *gyro, *accel, std::nullopt, ticks};
}

// One Luma Ultra stream packet: see the layout in protocol.hpp.
std::optional<ImuSample> decode_luma(Bytes p) {
    if (p.size() < kLumaImuPacketSize || p[0] != 0xaa || p[1] != 0x8f || p[2] != 0x00 || p[3] != kLumaImuType)
        return std::nullopt;
    if (read_le16(p.data() + 6) != kLumaImuPayload) return std::nullopt;
    const auto axis = [&](std::size_t offset) { return float(std::int16_t(read_le16(p.data() + offset))); };
    const float gyro = kDegToRad / kLumaGyroLsbPerDegS, accel = kStandardGravity / kLumaAccelLsbPerG;
    const float ax = axis(18), ay = axis(20), az = axis(22);
    if (ax == 0.f && ay == 0.f && az == 0.f) return std::nullopt;  // no accelerometer reads exactly this
    return ImuSample{std::uint64_t(read_u32(p.data() + 8, false)) * kLumaTickNs,
                     {-axis(14) * gyro, axis(12) * gyro, axis(16) * gyro},
                     {-ay * accel, ax * accel, az * accel},
                     std::nullopt,
                     read_le16(p.data() + 4)};
}

bool is_v2(Bytes p) {
    return p.size() >= kV2HeaderSize && p[0] == kV2Preamble[0] && p[1] == kV2Preamble[1];
}
struct V2View {
    std::uint16_t msg_id;
    Bytes payload;
};
std::optional<V2View> intact_v2(Bytes p) {
    if (!is_v2(p)) return std::nullopt;
    const std::size_t length = read_le16(p.data() + 4);
    if (!fits(p.size(), kV2HeaderSize, length)) return std::nullopt;
    const Bytes payload = p.subspan(kV2HeaderSize, length);
    if (read_le16(p.data() + 6) != byte_sum16(payload)) return std::nullopt;
    return V2View{read_le16(p.data() + 2), payload};
}

FrameKind data_kind(Bytes p) {
    if (p.size() < kBodyOffset || p[0] != kSyncLead) return FrameKind::unknown;
    switch (p[1]) {
    case kSyncRequest: return FrameKind::data_request;
    case kSyncReply: return FrameKind::data_reply;
    case kSyncStream: return FrameKind::data_stream;
    default: return FrameKind::unknown;
    }
}

Command v2_imu_control(std::uint8_t stream, std::uint8_t rate) {
    const std::array<std::uint8_t, 2> payload{stream, rate};
    const std::uint16_t sum = byte_sum16(payload);
    return {{kV2Preamble[0], kV2Preamble[1], std::uint8_t(kV2ImuControl), std::uint8_t(kV2ImuControl >> 8),
             std::uint8_t(payload.size()), 0x00, std::uint8_t(sum), std::uint8_t(sum >> 8), payload[0], payload[1]},
            kV2EndpointOut};
}

}  // namespace

std::uint16_t crc16_xmodem(Bytes bytes) noexcept {
    std::uint16_t crc = 0;
    for (const std::uint8_t byte : bytes) crc = std::uint16_t((crc << 8) ^ kCrcTable[(crc >> 8) ^ byte]);
    return crc;
}

std::uint16_t byte_sum16(Bytes bytes) noexcept {
    std::uint16_t sum = 0;
    for (const std::uint8_t byte : bytes) sum = std::uint16_t(sum + byte);
    return sum;
}

std::optional<DataFrame> parse_data_frame(Bytes p) noexcept {
    const FrameKind kind = data_kind(p);
    if (kind == FrameKind::unknown) return std::nullopt;
    const std::size_t pklen = read_le16(p.data() + 4);
    if (pklen < kMinPklen || !fits(p.size(), kLengthBase, pklen)) return std::nullopt;
    const std::size_t end = kLengthBase + pklen;
    if (read_le16(p.data() + 2) != crc16_xmodem(p.subspan(kCheckedFrom, end - kCheckedFrom))) return std::nullopt;
    std::uint64_t counter = 0;
    for (std::size_t i = 0; i < 8; ++i) counter |= std::uint64_t(p[kCounterOffset + i]) << (8 * i);
    return DataFrame{kind, read_le16(p.data() + kMsgIdOffset), counter, p.subspan(kBodyOffset, end - kBodyOffset)};
}

FrameInfo classify(Bytes p) noexcept {
    if (is_v2(p)) {
        const auto frame = intact_v2(p);
        return {FrameKind::v2, read_le16(p.data() + 2), frame.has_value()};
    }
    const FrameKind kind = data_kind(p);
    if (kind == FrameKind::unknown) return {};
    return {kind, read_le16(p.data() + kMsgIdOffset), parse_data_frame(p).has_value()};
}

std::optional<ImuSample> parse_imu(Bytes packet) noexcept {
    return parse_imu_with(packet, ParseOptions{});
}

std::optional<ImuSample> parse_imu_with(Bytes packet, const ParseOptions& options) noexcept {
    if (const auto v2 = intact_v2(packet)) {
        if (v2->msg_id != options.v2.msg_id) return std::nullopt;
        return decode(v2->payload, options.v2);
    }
    if (const auto sample = decode_luma(packet)) return sample;
    if (!options.luma_ultra) return std::nullopt;
    const auto frame = parse_data_frame(packet);
    if (!frame || frame->kind == FrameKind::data_request || frame->msg_id != options.luma_ultra->msg_id)
        return std::nullopt;
    return decode(frame->body, *options.luma_ultra);
}

std::optional<Command> data_request(std::uint16_t msg_id, Bytes payload) {
    if (payload.size() > kDataPacketSize - kBodyOffset) return std::nullopt;
    Command c{std::vector<std::uint8_t>(kDataPacketSize, 0), kDataEndpointOut};
    auto& b = c.bytes;
    const std::size_t pklen = kMinPklen + payload.size();
    b[0] = kSyncLead;
    b[1] = kSyncRequest;
    b[4] = std::uint8_t(pklen);
    b[5] = std::uint8_t(pklen >> 8);
    b[kMsgIdOffset] = std::uint8_t(msg_id);
    b[kMsgIdOffset + 1] = std::uint8_t(msg_id >> 8);
    std::copy(payload.begin(), payload.end(), b.begin() + kBodyOffset);
    // Covers the length field through the payload, not the padding after it.
    const std::uint16_t crc = crc16_xmodem(Bytes(b).subspan(kCheckedFrom, kLengthBase + pklen - kCheckedFrom));
    b[2] = std::uint8_t(crc);
    b[3] = std::uint8_t(crc >> 8);
    return c;
}

std::array<std::uint8_t, kLumaImuPacketSize> encode_luma_imu(const ImuSample& s, std::uint32_t temperature_raw) noexcept {
    std::array<std::uint8_t, kLumaImuPacketSize> b{0xaa, 0x8f, 0x00, kLumaImuType};
    const auto put16 = [&](std::size_t at, std::uint16_t v) { b[at] = std::uint8_t(v); b[at + 1] = std::uint8_t(v >> 8); };
    const auto put32 = [&](std::size_t at, std::uint32_t v) { put16(at, std::uint16_t(v)); put16(at + 2, std::uint16_t(v >> 16)); };
    const auto lsb = [](float v) { return std::uint16_t(std::int16_t(std::lround(std::clamp(v, -32768.f, 32767.f)))); };
    const float gyro = kLumaGyroLsbPerDegS / kDegToRad, accel = kLumaAccelLsbPerG / kStandardGravity;
    put16(4, std::uint16_t(s.seq));
    put16(6, kLumaImuPayload);
    put32(8, std::uint32_t(s.t_ns / kLumaTickNs));
    put16(12, lsb(s.gyro_rad_s[1] * gyro));   // wire x = left
    put16(14, lsb(-s.gyro_rad_s[0] * gyro));  // wire y = back
    put16(16, lsb(s.gyro_rad_s[2] * gyro));   // wire z = up
    put16(18, lsb(s.accel_m_s2[1] * accel));
    put16(20, lsb(-s.accel_m_s2[0] * accel));
    put16(22, lsb(s.accel_m_s2[2] * accel));
    put32(24, temperature_raw);
    return b;
}

std::optional<Command> mcu_request(std::uint8_t command, Bytes payload) {
    if (payload.size() > 0xffff) return std::nullopt;
    Command c{{kMcuSync0, kMcuSync1, command, std::uint8_t(payload.size() >> 8), std::uint8_t(payload.size())}, kMcuEndpointOut};
    c.bytes.insert(c.bytes.end(), payload.begin(), payload.end());
    return c;
}

std::optional<Command> start_imu(Device device, ImuRate rate) {
    if (device == Device::v2) return v2_imu_control(kV2StreamRaw, std::uint8_t(rate));
    return mcu_request(kMcuStartStream);
}

std::optional<Command> stop_imu(Device device) {
    if (device == Device::v2) return v2_imu_control(kV2StreamOff, 0);
    return std::nullopt;  // Luma Ultra: no capture has shown a stop, see protocol.hpp
}

SequenceTracker::Result SequenceTracker::observe(ImuSample& sample) noexcept {
    Result result;
    if (!have_last_) {
        ticks_ = have_ticks_ ? ticks_ + 1 : sample.seq;
    } else {
        // Unsigned subtraction, so the wrap is one ordinary step forward.
        const std::uint32_t forward = (sample.seq - last_) & mask_;
        const std::uint32_t backward = (last_ - sample.seq) & mask_;
        if (forward == 0) return {Verdict::duplicate};
        if (forward <= mask_ / 2) {
            result.lost = forward - 1;
            lost_total_ += result.lost;
            ticks_ += forward;
        } else if (backward <= kMaxStaleStep) {
            return {Verdict::stale};
        } else {
            result.restarted = true;
            ticks_ += 1;
        }
    }
    last_ = sample.seq;
    have_last_ = true;
    have_ticks_ = true;
    sample.t_ns = ticks_ * tick_ns_;
    if (mask_ != 0xffffffffu) sample.seq = std::uint32_t(ticks_);
    return result;
}

}  // namespace luma
