// VITURE wire protocol: frames, checks, IMU decoding and the stream commands.
//
// THE LUMA ULTRA'S IMU LAYOUT BELOW IS MEASURED, FROM A PASSIVE USBMON CAPTURE OF A
// LIVE XR SESSION (2026-11-04, 19.5 minutes, spike/driver/docs/capture-findings.md).
// Nothing here comes from the VITURE SDK: the capture records the device's own
// traffic on Cody's machine, and no SDK code was read.
//
//   Luma Ultra, IMU stream: interrupt IN 0x81, 28 bytes, one sample per packet at
//     1 kHz. Layout and scales are in parse_imu's section below. Golden bytes from
//     the capture are in tests/protocol_test.cpp and tests/fixtures/.
//
//   Luma Ultra, start: FA 55 95 00 00 on bulk OUT 0x04. The first IMU packet
//     arrives 0.74 ms later, with reads queued on 0x81 for 1.2 s before it and
//     none delivered. That it is the cause is inferred from the timing, and the
//     same command very probably starts the cameras: bulk 0x82 begins with it.
//     Stop: never captured, so stop_imu returns nothing for this device. Sending
//     a guessed id at the channel that also flashes firmware is not an option.
//
//   Luma Ultra, data envelope (FF FE / FF FD), endpoints 0x06 / 0x87:
//     github.com/go-viture/luma (BSD-3-Clause, cc37e96), info.go, and now the
//     capture: 0x100B once, then 0x0007 about once a second, all CRCs good.
//
//   VITURE Pro 2 (35ca:1301), protocol V2: github.com/elasticjava/viture-v2
//     (MIT, 8b29df0), PROTOCOL.md. Measured with usbmon on a Pro 2.
//
// Every command sent is a chance to upset the firmware, and zero USB drops comes
// first, so a stream gets one start and no polling. Deliberately absent, all
// optional for head tracking: display mode (message 0x0007, the one timing out in
// the current driver's log), status reports (0x100B, whose effect is unknown),
// brightness, volume, film, serial and version reads, the calibration read (FA 55
// E4/E5) and exposure control (FA 55 90).
#pragma once
#include "luma/transport.hpp"
#include "luma/types.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

namespace luma {

inline constexpr std::uint16_t kVendorId = kVitureVendorId;
inline constexpr std::uint8_t kDataEndpointOut = 0x06;
inline constexpr std::uint8_t kDataEndpointIn = 0x87;
inline constexpr std::size_t kDataPacketSize = 512;
inline constexpr std::uint8_t kV2EndpointOut = 0x01;
inline constexpr std::uint8_t kMcuEndpointOut = kLumaUltraFlashOut;
inline constexpr std::uint8_t kMcuEndpointIn = kLumaUltraFlashIn;
inline constexpr std::uint8_t kLumaImuEndpoint = 0x81;    // interrupt IN, polled every 500 us
inline constexpr std::uint8_t kLumaEventEndpoint = 0x83;  // interrupt IN: 16-byte type 0x91 frames at the display rate
inline constexpr std::uint8_t kLumaCameraEndpoint = 0x82; // bulk IN: 16 KiB tiles, 19 MB/s while the cameras run. Never read by default

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

// The data envelope carries no IMU on the Luma Ultra: its stream has a framing of
// its own (below). Left for a device or firmware that does put one there.
inline constexpr std::optional<ImuFields> kLumaUltraImuFields = std::nullopt;

// The Luma Ultra's stream packet, all little-endian, as measured:
//   0..2   AA 8F 00
//   3      type: 0x88 IMU on 0x81, 0x91 display event on 0x83, others on camera 0x82
//   4..5   sequence, +1 per packet, wraps at 65536 (65.5 s at 1 kHz)
//   6..7   payload length, 16 for an IMU packet
//   8..11  device time, 100 us per tick, from the device's boot
//   12..17 gyro x, y, z, i16, 16.4 LSB per deg/s
//   18..23 accel x, y, z, i16, 8192 LSB per g
//   24..27 u32, most likely temperature: climbs 1760 to 2910 as the glasses warm
// Gyro scale: fitted, rotation of gravity between still moments, error minimum at
// 16.4 (+-2000 deg/s) out of 8.2 to 65.5. Accel: 8270 LSB at rest is 1.01 g, so
// +-4 g. Axes: the same frame for both (a sign flip gives 12 degrees of error
// against 0.4), right-handed, z up from gravity, y the roll axis by PCA, and the
// sensor sits 8.2 +- 0.5 cm along -y from the head's rotation centre, from a
// rigid-body fit (spike/driver/docs/capture-findings.md). So on the wire
//   forward = -y, left = +x, up = +z
// and parse_imu returns the body frame Fusion and Predictor want, x forward, y left,
// z up, accel as specific force (+9.8 on z when level).
inline constexpr std::size_t kLumaImuPacketSize = 28;
inline constexpr std::uint8_t kLumaImuType = 0x88;
inline constexpr std::uint16_t kLumaImuPayload = 16;
inline constexpr std::uint64_t kLumaTickNs = 100'000;
inline constexpr std::uint64_t kLumaSampleNs = 1'000'000;
inline constexpr float kLumaGyroLsbPerDegS = 16.4f;
inline constexpr float kLumaAccelLsbPerG = 8192.0f;
inline constexpr unsigned kLumaSeqBits = 16;

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
// For a Luma Ultra packet, t_ns is device time and seq the 16-bit wire sequence:
// pass both through a SequenceTracker made with luma_sequence_tracker().
std::optional<ImuSample> parse_imu(std::span<const std::uint8_t> packet) noexcept;
// Same, with other layouts. A separate name, because the daemon stores parse_imu
// as a plain function pointer, which an overload would make ambiguous.
std::optional<ImuSample> parse_imu_with(std::span<const std::uint8_t> packet, const ParseOptions& options) noexcept;

// The inverse of parse_imu for a Luma Ultra packet, for fixtures and fakes. The
// sample's body frame goes back to wire axes; values saturate at the i16 range.
std::array<std::uint8_t, kLumaImuPacketSize> encode_luma_imu(const ImuSample& sample, std::uint32_t temperature_raw = 2000) noexcept;

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

// One FA 55 frame for the MCU channel: FA 55, command, big-endian payload length,
// payload. Transport passes only the exact frames on its allowlist (allowed_mcu_frame),
// because this channel also flashes firmware.
std::optional<Command> mcu_request(std::uint8_t command, std::span<const std::uint8_t> payload = {});
inline constexpr std::uint8_t kMcuStartStream = 0x95;
inline constexpr std::uint8_t kMcuBeforeStart = 0x97;  // sent 100 ms before 0x95 by the SDK; its meaning is unknown

// The only commands a stream needs. stop_imu is empty for the Luma Ultra because no
// capture has shown one: leave the stream on rather than guess. rate is ignored
// there: nothing sent in the capture sets one, and the device runs at 1 kHz.
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

    // seq_bits: how wide the device's counter is. Below 32 the tracker also widens
    // sample.seq to one that does not wrap, which Fusion's gap check relies on.
    explicit SequenceTracker(std::uint64_t tick_ns = kV2RawFields.tick_ns, unsigned seq_bits = 32)
        : tick_ns_(tick_ns), mask_(seq_bits >= 32 ? 0xffffffffu : (1u << seq_bits) - 1u) {}

    Result observe(ImuSample& sample) noexcept;
    // Call after a reconnect: the next sample starts a fresh count.
    void reset() noexcept { have_last_ = false; }
    std::uint64_t lost_total() const noexcept { return lost_total_; }

private:
    std::uint64_t tick_ns_;
    std::uint32_t mask_;
    std::uint64_t ticks_ = 0;  // extended past 32 bits
    std::uint64_t lost_total_ = 0;
    std::uint32_t last_ = 0;
    bool have_last_ = false;
    bool have_ticks_ = false;
};

// The tracker for the Luma Ultra's stream: one sample per millisecond, 16-bit sequence.
// Its t_ns counts samples, not the device's 100 us clock, on purpose: over the
// capture the packets ran at 1000.00 Hz of host time within 8 ppm, while the device
// stamp ran 65 ppm fast and carries +-100 us of jitter.
inline SequenceTracker luma_sequence_tracker() { return SequenceTracker(kLumaSampleNs, kLumaSeqBits); }

}  // namespace luma
