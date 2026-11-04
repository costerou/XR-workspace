// Reader for .lcap capture files, for the daemon's --replay. Spec: docs/capture-format.md.
// Header-only, standard library only. tools/luma_capture writes these files; the test that keeps the two
// in step is tests/luma_capture/test_cpp_reader.py.
#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace luma::capture {

static_assert(std::endian::native == std::endian::little, "capture files are little endian");

inline constexpr std::size_t kFileHeaderSize = 32;
inline constexpr std::size_t kRecordHeaderSize = 48;
inline constexpr std::uint16_t kVersion = 1;

enum Kind : std::uint8_t { kUsb = 1, kNote = 2 };
enum Xfer : std::uint8_t { kIso = 0, kInterrupt = 1, kControl = 2, kBulk = 3 };
enum Flag : std::uint8_t {
  kSetup = 0x01,
  kGlasses = 0x02,
  kKernelTruncated = 0x04,
  kPolicyDropped = 0x08,
  kCapped = 0x10,
  kCompanion = 0x20,
};

struct Record {
  std::uint64_t t_ns = 0;  // CLOCK_REALTIME
  std::uint64_t urb_id = 0;
  std::int32_t status = 0;
  std::uint32_t urb_len = 0;
  std::uint16_t bus = 0;
  std::uint8_t kind = 0, event = 0, xfer = 0, ep = 0, dev = 0, flags = 0;
  std::array<std::uint8_t, 8> setup{};
  std::span<const std::uint8_t> data;  // valid until the next Reader::next()

  // Bytes the host received complete from the glasses: what Transport::read returns under --replay.
  [[nodiscard]] bool replayable() const noexcept {
    return kind == kUsb && event == 'C' && (ep & 0x80) != 0 && (flags & kGlasses) != 0 && status == 0 &&
           (xfer == kInterrupt || xfer == kBulk) && (flags & (kKernelTruncated | kPolicyDropped | kCapped)) == 0;
  }
  // Bytes the host sent to the glasses, to compare against what a replayed driver writes.
  [[nodiscard]] bool host_write() const noexcept {
    return kind == kUsb && event == 'S' && (ep & 0x80) == 0 && (flags & kGlasses) != 0 && xfer != kControl;
  }
};

class Reader {
 public:
  // Empty, with *error set, when the file is missing or is not a capture this reader understands.
  static std::optional<Reader> open(const std::string& path, std::string* error = nullptr) {
    auto fail = [&](const char* why) {
      if (error != nullptr) *error = path + ": " + why;
      return std::nullopt;
    };
    File file(std::fopen(path.c_str(), "rb"), &std::fclose);
    if (!file) return fail("cannot open");
    std::array<std::uint8_t, kFileHeaderSize> raw{};
    if (std::fread(raw.data(), 1, raw.size(), file.get()) != raw.size() ||
        std::memcmp(raw.data(), "LUMACAP1", 8) != 0) {
      return fail("not a capture file");
    }
    const auto version = load<std::uint16_t>(raw.data() + 8);
    const auto file_header = load<std::uint16_t>(raw.data() + 10);
    const auto record_header = load<std::uint16_t>(raw.data() + 12);
    if (version != kVersion) return fail("unsupported capture version");
    if (file_header < kFileHeaderSize || record_header < kRecordHeaderSize) return fail("headers too short");
    if (std::fseek(file.get(), file_header, SEEK_SET) != 0) return fail("cannot seek");
    return Reader(std::move(file), load<std::uint64_t>(raw.data() + 16), load<std::uint32_t>(raw.data() + 24),
                  record_header);
  }

  // The next USB or note record; false at the end. A cut-off file ends early with truncated() set.
  bool next(Record& out) {
    for (;;) {
      head_.resize(record_header_);
      const std::size_t got = std::fread(head_.data(), 1, head_.size(), file_.get());
      if (got == 0) return false;
      if (got < head_.size()) return cut();
      const auto* h = head_.data();
      const auto size = load<std::uint32_t>(h);
      const auto data_len = load<std::uint32_t>(h + 36);
      if (size < record_header_ || size - record_header_ != data_len) return cut();
      data_.resize(data_len);
      if (std::fread(data_.data(), 1, data_len, file_.get()) != data_len) return cut();
      if (h[4] != kUsb && h[4] != kNote) continue;  // a later version's kind: its length lets us step over it
      out.kind = h[4];
      out.event = h[5];
      out.xfer = h[6];
      out.ep = h[7];
      out.t_ns = load<std::uint64_t>(h + 8);
      out.urb_id = load<std::uint64_t>(h + 16);
      out.bus = load<std::uint16_t>(h + 24);
      out.dev = h[26];
      out.flags = h[27];
      out.status = load<std::int32_t>(h + 28);
      out.urb_len = load<std::uint32_t>(h + 32);
      std::memcpy(out.setup.data(), h + 40, out.setup.size());
      out.data = data_;
      return true;
    }
  }

  [[nodiscard]] std::uint64_t start_ns() const noexcept { return start_ns_; }
  [[nodiscard]] std::uint32_t bus() const noexcept { return bus_; }
  [[nodiscard]] bool truncated() const noexcept { return truncated_; }

 private:
  using File = std::unique_ptr<std::FILE, int (*)(std::FILE*)>;

  Reader(File file, std::uint64_t start_ns, std::uint32_t bus, std::size_t record_header)
      : file_(std::move(file)), start_ns_(start_ns), bus_(bus), record_header_(record_header) {}

  bool cut() noexcept {
    truncated_ = true;
    return false;
  }

  template <class T>
  static T load(const std::uint8_t* at) noexcept {
    T value;
    std::memcpy(&value, at, sizeof value);
    return value;
  }

  File file_;
  std::uint64_t start_ns_;
  std::uint32_t bus_;
  std::size_t record_header_;
  bool truncated_ = false;
  std::vector<std::uint8_t> head_, data_;
};

// What a replay plays back, in time order. The numbers match luma::CaptureKind in daemon.hpp.
enum class ReplayKind : std::uint8_t { from_device = 0, to_device = 1, device_gone = 2, device_back = 3 };
struct ReplayEvent {
  std::uint64_t t_ns = 0;
  ReplayKind kind = ReplayKind::from_device;
  std::vector<std::uint8_t> payload;
};

// Turns one capture file into events. A failed transfer is deliberately not device_gone: the kernel also fails
// transfers on a mere SET_CONFIGURATION. Only the recorder's sysfs notes say the device left and came back.
// Give an endpoint to keep only that one's from_device data. A file cut off mid-record yields what precedes the cut.
inline std::optional<std::vector<ReplayEvent>> load_replay(const std::string& path, std::string* error = nullptr,
                                                           std::optional<std::uint8_t> endpoint = std::nullopt) {
  auto reader = Reader::open(path, error);
  if (!reader) return std::nullopt;
  std::vector<ReplayEvent> events;
  Record record;
  while (reader->next(record)) {
    if (record.kind == kNote) {
      const std::string_view text(reinterpret_cast<const char*>(record.data.data()), record.data.size());
      if (text.find("\"type\":\"glasses_gone\"") != text.npos) {
        events.push_back({record.t_ns, ReplayKind::device_gone, {}});
      } else if (text.find("\"type\":\"glasses_back\"") != text.npos) {
        events.push_back({record.t_ns, ReplayKind::device_back, {}});
      }
    } else if (record.replayable() && (!endpoint || record.ep == *endpoint)) {
      events.push_back({record.t_ns, ReplayKind::from_device, {record.data.begin(), record.data.end()}});
    } else if (record.host_write()) {
      events.push_back({record.t_ns, ReplayKind::to_device, {record.data.begin(), record.data.end()}});
    }
  }
  std::stable_sort(events.begin(), events.end(),
                   [](const ReplayEvent& a, const ReplayEvent& b) { return a.t_ns < b.t_ns; });
  return events;
}

}  // namespace luma::capture
