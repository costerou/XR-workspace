// VITURE wire protocol: frames, checks, IMU decoding and the stream commands.
//
// READ THIS FIRST. THE LUMA ULTRA'S IMU PACKET IS NOT PUBLICLY KNOWN.
//
// XRLinuxDriver holds no wire format at all: src/devices/viture.c calls the
// closed libglasses.so and only sees decoded callbacks. This file is built
// from what other open projects published, and each fact below says which
// device it was seen on. Nothing here comes from the VITURE SDK.
//
//   Luma Ultra, data envelope (FF FE / FF FD), endpoints 0x06 / 0x87:
//     github.com/go-viture/luma (BSD-3-Clause, cc37e96), info.go. Measured on
//     a Luma Ultra for read-only requests. That project read the layout out of
//     the vendor's library, then confirmed it on hardware. A real reply from
//     its tests passes the CRC below, and so does VITURE's own updater's frames.
//     Not established: any message beyond its five reads.
//
//   Luma Ultra, IMU stream: UNKNOWN. The pipe, the start command and the packet
//     layout are all unpublished. The descriptors of 35ca:1104 show interrupt
//     IN pipes 0x81 and 0x83, which are candidates and nothing more. The HID
//     interfaces of the companion 35ca:1102 are a dead end: Spatial-Screens
//     (cee6556, docs/plan/roadmap.md) sent the classic IMU enable to both on
//     real hardware and got no reply, and their report descriptors say usage
//     page 0x0C (consumer), not vendor.
//     Cody's driver.log (2026-11-03) narrows where to look. Healthy starts with
//     6DOF on and off show the same steps: "get exp flag", a 15872-byte transfer
//     and "open_vst_str", then 0x100B (one byte, once), then 0x0007 polling. The
//     SDK's message builder logs no other id, so whatever starts the stream does
//     not go through it (inferred). A capture has to cover every pipe and
//     control transfers, not only 0x06 / 0x87.
//
//   VITURE Pro 2 (35ca:1301), protocol V2: github.com/elasticjava/viture-v2
//     (MIT, 8b29df0), PROTOCOL.md. Measured with usbmon on a Pro 2. This is the
//     only gyro and accelerometer packet with a public byte layout, and no
//     Luma Ultra has been shown to speak it.
//
// So until a capture of a real session settles it (recording a session running
// the SDK is Cody's call, see spike/driver/docs/protocol.md):
//   - parse_imu() returns nothing for every Luma Ultra frame, because
//     kLumaUltraImuFields is empty. It never guesses, since a wrong guess would
//     turn into plausible head motion.
//   - start_imu(Device::luma_ultra) returns nothing, because an unknown opcode
//     must not be written to glasses somebody is wearing.
// The decoder is table driven, so a capture needs one kLumaUltraImuFields entry
// and one start message, not a new parser. Tests prove that path with a
// synthetic layout.
//
// Every command sent is a chance to upset the firmware, and zero USB drops comes
// first, so a stream gets one start, one stop and no polling. Deliberately absent,
// all optional for head tracking: display mode (message 0x0007, the one timing
// out in the current driver's log), status reports (0x100B, whose effect is
// unknown), brightness, volume, film, serial and version reads. The FA 55
// envelope on endpoints 0x04 / 0x85 is absent too: go-viture reports it as the
// channel the vendor's updater flashes through.
#pragma once
#include "luma/types.hpp"
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

namespace luma {

inline constexpr std::uint16_t kVendorId = 0x35ca;
inline constexpr std::uint16_t kLumaUltraProductId = 0x1104;
inline constexpr std::uint8_t kDataEndpointOut = 0x06;
inline constexpr std::uint8_t kDataEndpointIn = 0x87;
inline constexpr std::size_t kDataPacketSize = 512;
inline constexpr std::uint8_t kV2EndpointOut = 0x01;

// CRC-16/XMODEM: polynomial 0x1021, initial value 0, no reflection, no final
// xor. Check value: "123456789" gives 0x31C3. Older VITURE write-ups disagree
// (bfvogel/viture-webxr-extension says initial 0xFFFF, big-endian); this is the
// one a real Luma Ultra uses in both directions.
std::uint16_t crc16_xmodem(std::span<const std::uint8_t> bytes) noexcept;
// Sum of the bytes modulo 2^16: the check of protocol V2, which is not a CRC.
std::uint16_t byte_sum16(std::span<const std::uint8_t> bytes) noexcept;

enum class FrameKind {
    unknown,       // no known framing, or a data frame too short to hold a header
    v2,            // 10 00 ...
    data_request,  // FF FE, host to device
    data_reply,    // FF FD, device to host
    data_stream,   // FF FC, device to host (classic IMU sync; unconfirmed on the Ultra)
};

// What a packet appears to be, for logs and for labelling captures. It never
// fails: `intact` is false when the frame's own length or check does not hold.
struct FrameInfo {
    FrameKind kind = FrameKind::unknown;
    std::uint16_t msg_id = 0;
    bool intact = false;
};
FrameInfo classify(std::span<const std::uint8_t> packet) noexcept;

// One intact data-envelope frame. All multi-byte fields are little-endian:
//   0..1   sync
//   2..3   CRC-16/XMODEM over bytes [4, 6 + pklen)
//   4..5   pklen, at least 12
//   6..13  counter, filled by the device, zero on a request
//   14..15 message id
//   16..17 zero on a request
//   18..   body; on a reply byte 18 is a status, 0 meaning success
struct DataFrame {
    FrameKind kind = FrameKind::unknown;
    std::uint16_t msg_id = 0;
    std::uint64_t counter = 0;
    std::span<const std::uint8_t> body;  // views the packet passed in
};
std::optional<DataFrame> parse_data_frame(std::span<const std::uint8_t> packet) noexcept;

// Where the fields of one IMU message live. Offsets count from the first byte
// of the payload (protocol V2) or of the body (data envelope). The timestamp
// is an unsigned 32-bit tick count, and the sample's seq is that same value.
struct ImuFields {
    std::uint16_t msg_id;
    bool big_endian;
    std::size_t timestamp_offset;
    std::size_t gyro_offset;   // three floats
    std::size_t accel_offset;  // three floats
    float gyro_to_rad_s;
    float accel_to_m_s2;
    std::uint64_t tick_ns;
};

inline constexpr float kStandardGravity = 9.80665f;

// Protocol V2 raw event 0x7309, 56-byte payload: gyro in rad/s, accelerometer
// in g, a timestamp that rises by 1 per sample. Hence tick_ns assumes the
// 1000 Hz rate requested by default; pass other fields for a lower rate.
inline constexpr ImuFields kV2RawFields{
    .msg_id = 0x7309,
    .big_endian = false,
    .timestamp_offset = 4,
    .gyro_offset = 10,
    .accel_offset = 22,
    .gyro_to_rad_s = 1.0f,
    .accel_to_m_s2 = kStandardGravity,
    .tick_ns = 1'000'000,
};

// EMPTY ON PURPOSE, see the top of this file. Fill it from a capture. Two
// published facts to check against then: the SDK hands over accelerometer
// first, then gyro (viture_device_carina.h), and Spatial-Screens saw about 9.8
// at rest, so that callback is already in m/s2, not g. Before trusting a layout,
// confirm on a capture that the accelerometer reads about 9.8 at rest: the range
// limits below do not catch a wrong one (reversed bytes can land in range).
inline constexpr std::optional<ImuFields> kLumaUltraImuFields = std::nullopt;

struct ParseOptions {
    ImuFields v2 = kV2RawFields;
    std::optional<ImuFields> luma_ultra = kLumaUltraImuFields;  // data envelope
};

// Sanity limits, not sensor specifications: wider than any head can produce, so
// a value past them means the bytes are not what the layout claims.
inline constexpr float kMaxGyroRadS = 70.0f;
inline constexpr float kMaxAccelMs2 = 320.0f;

// Decodes one packet into a sample, or returns nothing if the packet is
// malformed, fails its check, is another message, is not a known IMU layout, or
// holds a non-finite, out-of-range or all-zero reading. Trailing bytes past the
// frame's own length are padding and are ignored. Safe on the real-time thread:
// it cannot throw and makes no allocation, and a test measures that.
std::optional<ImuSample> parse_imu(std::span<const std::uint8_t> packet) noexcept;
// Same, with other layouts. A separate name, because the daemon stores parse_imu
// as a plain function pointer, which an overload would make ambiguous.
std::optional<ImuSample> parse_imu_with(std::span<const std::uint8_t> packet, const ParseOptions& options) noexcept;

// A command, and the endpoint it goes to.
struct Command {
    std::vector<std::uint8_t> bytes;
    std::uint8_t endpoint = 0;
};

enum class Device { luma_ultra, v2 };

// Wire values from viture-v2 PROTOCOL.md; XRLinuxDriver's header gives the same
// numbers for the SDK's frequency constants.
enum class ImuRate : std::uint8_t { hz60 = 0, hz90 = 1, hz120 = 2, hz240 = 3, hz500 = 4, hz1000 = 5 };

// Builds one 512-byte data-envelope request. Returns nothing if the payload
// does not fit. Check any message id before sending it: only read-only ones
// have ever been shown to be safe.
std::optional<Command> data_request(std::uint16_t msg_id, std::span<const std::uint8_t> payload = {});

// The only commands a stream needs. Empty for the Luma Ultra until it is known
// what starts its stream: the caller must treat that as "cannot start", and
// must not invent a fallback.
std::optional<Command> start_imu(Device device, ImuRate rate = ImuRate::hz1000);
std::optional<Command> stop_imu(Device device);

// Turns per-packet tick counts into one ordered timeline.
//
// parse_imu() is stateless, so it cannot see a 32-bit wrap, a lost sample or a
// device that restarted. Pass every sample through observe() and drop what it
// does not accept. An accepted sample gets a t_ns that never goes backwards, not
// across a wrap, a restart or reset().
class SequenceTracker {
public:
    // A backward step up to this size is a late packet; beyond it the device's
    // counter restarted. At 1 kHz it is 64 ms of samples.
    static constexpr std::uint32_t kMaxStaleStep = 64;

    enum class Verdict { accepted, duplicate, stale };
    struct Result {
        Verdict verdict = Verdict::accepted;
        std::uint32_t lost = 0;  // samples missing just before this one
        bool restarted = false;  // the device's counter started over
    };

    explicit SequenceTracker(std::uint64_t tick_ns = kV2RawFields.tick_ns) : tick_ns_(tick_ns) {}

    Result observe(ImuSample& sample) noexcept;
    // Call after a reconnect: the next sample starts a fresh count.
    void reset() noexcept { have_last_ = false; }
    std::uint64_t lost_total() const noexcept { return lost_total_; }

private:
    std::uint64_t tick_ns_;
    std::uint64_t ticks_ = 0;  // extended past 32 bits
    std::uint64_t lost_total_ = 0;
    std::uint32_t last_ = 0;
    bool have_last_ = false;
    bool have_ticks_ = false;
};

}  // namespace luma
