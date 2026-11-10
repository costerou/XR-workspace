#pragma once
// luma::Transport: the byte pipe to the glasses, with no knowledge of what the bytes mean.
//
// Primary path: usbfs bulk on the Ultra's data channel, raw /dev/bus/usb URBs with no libusb. The 35ca:1104
// has one vendor interface and no kernel driver, so no hidraw node exists for it. Sysfs, read 2026-11-03
// with the device untouched:
//   interrupt IN 0x81 (1024, 500 us): the IMU, one 28-byte packet per ms (usbmon, docs/capture-findings.md)
//   interrupt IN 0x83 (512, 1 ms): 16-byte events at the display rate
//   bulk IN 0x82 (512): the cameras, 19 MB/s in 16 KiB transfers
//   bulk OUT 0x06, bulk IN 0x87 (512): the data channel, 512-byte frames (spike/driver/docs/protocol.md)
//   bulk OUT 0x04, bulk IN 0x85 (512): the MCU channel, which also flashes firmware. The stream start lives
//     here, so it can be opened on request (allow_mcu_channel), and then only allowed_mcu_frame() bytes pass.
// URB type and size come from sysfs per endpoint.
// Backend::Hidraw remains for the HID interfaces on the 35ca:1102 mic device, on request only.
// docs/transport.md has the reasoning and what has not been proven on real hardware.
//
// Hotplug uses neither libudev nor netlink, on purpose. tracking.py runs the driver in a network
// namespace owned by a non-initial user namespace, and the kernel only delivers uevents to namespaces
// owned by the initial user namespace (lib/kobject_uevent.c, "Restrict uevents to initial user
// namespace"), so a monitor there would stay silent forever. Measured 2026-11-03 with one netlink
// listener in a normal session and one in tracking.py's exact sandbox, side by side: 73 events
// reached the first and none the second. Instead:
//   removal  : the open file itself: POLLHUP/POLLERR, ENODEV, EIO. Immediate, works in any namespace.
//   re-arrival: rescan sysfs every rescan_interval while down. Reads attributes the kernel caches,
//              so it puts nothing on the USB bus. With event_reopen, an inotify watch on /dev/bus/usb
//              also wakes the scan the moment a node appears, goes or changes its ACL. inotify works
//              in that sandbox: it is a filesystem event, not a uevent.
//
// Open errors do not end anything: read() keeps retrying, with a doubling interval, because EACCES is
// normal for a moment after arrival. It can also last: a device present since boot may have no user ACL
// (the udev rule matches ACTION=="add" only). last_error() then says what to do.
//
// Safety: this class never resets the device, never sets a configuration or alternate setting,
// never clears a halt, never detaches a kernel driver, and sends the Ultra's firmware channel nothing but the exact
// frames seen from VITURE's own software in a normal session. Every ioctl goes through an allowlist, is_allowed_ioctl(),
// and every frame on that channel through allowed_mcu_frame(), so those cannot go wrong by accident. If another program holds the
// interface (a running xrDriver), the claim fails with EBUSY and we keep waiting; we never take it.
//
// Threading: one thread drives read(), write(), open(), close(), next_event() and stats(). Only
// interrupt() may be called from another thread or a signal handler. There are no internal threads:
// reconnecting and refilling the read queue happen inside read(), so keep calling it. If eventfd
// fails at construction, interrupt() does nothing; signals still wake a read.
//
// Timeouts and interrupt() bound the wait in poll(), not the kernel's device lock: usbfs ioctls, open
// and close block while the device is being reset or removed, usually for milliseconds.
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <poll.h>
#include <span>
#include <string>
#include <sys/types.h>
#include <vector>

namespace luma {

inline constexpr std::uint16_t kVitureVendorId = 0x35ca;
inline constexpr std::uint16_t kLumaUltraProductId = 0x1104;
inline constexpr std::uint16_t kVitureMicProductId = 0x1102;
// The Ultra's command channel: 512-byte frames (drv-protocol-research, from go-viture). Its other
// pair, 0x04/0x85, also carries firmware updates, so Transport refuses it on this device unless asked,
// and even then passes only the frames allowed_mcu_frame() knows.
inline constexpr std::uint8_t kLumaUltraDataOut = 0x06;
inline constexpr std::uint8_t kLumaUltraDataIn = 0x87;
inline constexpr std::uint8_t kLumaUltraFlashOut = 0x04;
inline constexpr std::uint8_t kLumaUltraFlashIn = 0x85;

enum class Backend { Auto, Hidraw, Usbfs };

struct TransportConfig {
    std::uint16_t vendor_id = kVitureVendorId;
    std::uint16_t product_id = kLumaUltraProductId;
    int interface_number = 0;
    Backend backend = Backend::Usbfs;

    // Usbfs only. Addresses as the descriptor gives them (IN has 0x80 set). Every IN endpoint listed
    // is read at once and read() says which one a packet came from; out_endpoint 0 means no writes.
    // List only the endpoints in use: a queued read keeps the host polling that endpoint, and
    // reads on a camera endpoint could keep sensors busy.
    std::vector<std::uint8_t> in_endpoints{kLumaUltraDataIn};
    std::uint8_t out_endpoint = kLumaUltraDataOut;
    // More OUT endpoints, reached with write_to().
    std::vector<std::uint8_t> extra_out_endpoints;
    // Opens 0x04/0x85 on the Ultra. Without it a config naming them is refused. With it, a write to 0x04 that is
    // not exactly an allowed_mcu_frame() is refused, without reaching the device.
    bool allow_mcu_channel = false;
    // IN endpoints read and thrown away, with URBs of drain_urb_bytes rather than one packet, for a device that
    // stalls if an endpoint backs up. Off unless a live test shows the need: it means 19 MB/s on the bus.
    std::vector<std::uint8_t> drain_endpoints;
    std::size_t drain_urb_bytes = 16384;
    int drain_urbs = 6;  // What VITURE's own software queues on the camera endpoint.
    // Room for one packet. Every URB is exactly the endpoint's wMaxPacketSize, because USB ends a transfer
    // on a short packet or a full buffer: a bigger URB would hold back a frame of exactly one packet
    // until the next arrived, and a smaller one would be babble. An endpoint larger than this is refused.
    std::size_t packet_size = 1024;
    // Reads kept queued per IN endpoint, so the glasses always have somewhere to put a frame. Each
    // completed read is resubmitted before read() returns. While our thread is off the CPU the host
    // keeps collecting frames until these run out: 8 URBs ride out an 8 ms stall on a 1 kHz stream.
    int urbs_per_endpoint = 8;  // At least 2.
    std::size_t queue_frames = 256;  // Received packets held until read() takes them; the oldest goes first.
    // After losing the link, wait this long from first seeing the device before touching it: no open,
    // no claim, and no queued reads, which start the host polling. It restarts if the device vanishes
    // meanwhile. 2 s is protocol.md's starting value: 12 of 20 reconnects right after a drop were
    // followed by another drop, against 0 of 160 fresh starts (usb-drop-fix). A soak should tune it. A
    // first open never waits. If links keep dying before sustained traffic while the device stays, the hold
    // doubles per link, up to 16 times this and at least open_retry_max.
    std::chrono::milliseconds arrival_settle{2000};
    // Clear failed-session history only after payloads span this duration (minimum 1 ms).
    // Gaps over min(100 ms, healthy_after) restart the healthy interval. Transport counts bytes,
    // not valid IMU samples; this is a bounded retry mitigation, not a device-health diagnosis.
    std::chrono::milliseconds healthy_after{2000};
    // A device that is there but will not open (busy, no permission) is retried at a doubling
    // interval up to this, not at the rescan rate. Looking for an absent device stays at rescan_interval.
    std::chrono::milliseconds open_retry_max{1000};

    // Sysfs rescan period while disconnected. One scan takes about 0.3 ms on this machine, so 25 ms is
    // about 1% of a core, only while the glasses are gone.
    std::chrono::milliseconds rescan_interval{25};

    // D8 (--event-reopen). While down, watch dev_root/bus/usb with inotify and scan as soon as a node
    // appears, vanishes or gets its ACL, then every 5 ms for 500 ms, since the node exists before sysfs
    // lists its interfaces. The timed rescan stays as the fallback. A device back after a link with
    // sustained traffic (healthy_after) skips arrival_settle; after a link that died young the hold
    // applies exactly as without the flag.
    bool event_reopen = false;

    std::filesystem::path sysfs_root = "/sys";
    std::filesystem::path dev_root = "/dev";
};

struct EndpointInfo {
    std::uint8_t address = 0;
    bool bulk = false;  // Otherwise interrupt; control and isochronous endpoints are not usable here.
    std::size_t max_packet = 0;  // Bytes, from wMaxPacketSize. 0 if sysfs did not say.
};

struct FoundDevice {
    Backend backend = Backend::Usbfs;  // Never Auto.
    std::filesystem::path node;        // /dev/hidrawN or /dev/bus/usb/BBB/DDD
    std::string sys_name;              // hidraw10, 5-2.1
    std::vector<EndpointInfo> endpoints;  // Usbfs only.
};

// Sysfs lookup only: opens no device node. The first match in name order wins.
std::optional<FoundDevice> find_device(const TransportConfig& config);

enum class IoStatus {
    Ok,            // read: a packet is in the buffer. write: the device acknowledged it (see write()).
    Timeout,       // Nothing within the timeout. The link is as it was.
    Interrupted,   // interrupt() was called, or a signal arrived.
    Connected,     // read only: the link just came up (first time or again). Reset any parser.
    Disconnected,  // read: the link just went down, reported once. write: there is no link now.
    Closed,        // close() was called, or open() never was.
    Error,         // write only: not sent, wrong for the configuration (detail in last_error()). Link stands.
};

struct TransportRead {
    IoStatus status = IoStatus::Timeout;
    std::size_t bytes = 0;       // Copied into the caller's buffer.
    std::uint8_t endpoint = 0;   // Usbfs: IN endpoint it came from. Hidraw: 0.
    std::uint64_t t_ns = 0;      // CLOCK_MONOTONIC when we received it.
    bool truncated = false;      // Usbfs: the packet was longer than the buffer.
};

struct TransportEvent {
    enum class Kind { Connected, Disconnected };
    Kind kind = Kind::Connected;
    Backend backend = Backend::Usbfs;
    std::string node;
    std::string reason;             // Disconnected: what gave it away.
    std::uint64_t t_ns = 0;         // CLOCK_MONOTONIC.
    std::uint64_t downtime_ms = 0;  // Connected after a drop: how long the link was down.
    // Disconnected: what led up to it, for working out why it dropped.
    std::uint64_t ms_since_last_packet = 0;   // A stream that stopped before the drop reads high.
    int last_urb_status = 0;                  // Newest failed transfer this session (-EPROTO etc), 0 if none.
};

struct TransportStats {
    std::uint64_t connects = 0;
    std::uint64_t disconnects = 0;
    std::uint64_t packets = 0;          // Handed to read().
    std::uint64_t dropped_packets = 0;  // Pushed out of a full queue.
    std::uint64_t urb_errors = 0;       // Failed transfers, reads and writes. Any one of them ends the link.
    std::uint64_t open_failures = 0;    // Found the device but could not open or claim it.
    std::uint64_t last_downtime_ms = 0;
    int last_open_errno = 0;  // Why the device will not open now (EACCES, EBUSY); 0 if it opened or is absent.
};

// The system calls Transport makes, so tests can stand in for the kernel. Same return conventions as
// libc (-1 and errno). The default forwards to libc.
class Sys {
public:
    virtual ~Sys() = default;
    virtual int open(const char* path, int flags);
    virtual int close(int fd);
    virtual ssize_t read(int fd, void* buffer, std::size_t size);
    virtual ssize_t write(int fd, const void* buffer, std::size_t size);
    virtual int ioctl(int fd, unsigned long request, void* argument);
    virtual int poll(pollfd* fds, nfds_t count, int timeout_ms);
};

// The only ioctls Transport may issue: claim, release, submit, reap and discard.
bool is_allowed_ioctl(unsigned long request);

// The only bytes that may reach the Ultra's firmware channel, compared whole: FA 55 95 00 00 (starts the stream) and
// FA 55 97 00 00 (sent just before it). Both were captured from VITURE's own software in a normal session
// (docs/capture-findings.md). Adding to this list needs a capture showing the frame, never a guess.
bool allowed_mcu_frame(std::span<const std::uint8_t> frame) noexcept;

class Transport {
public:
    explicit Transport(TransportConfig config, std::shared_ptr<Sys> sys = nullptr);
    ~Transport();
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    // Starts the link. True if the device is up right now. False is not fatal: read() keeps looking
    // (see last_error()). Either way read() reports Connected once the link is up. Once started, this
    // only says whether the link is up: reconnecting belongs to read(). A link lost by write() before
    // read() saw it come up is reported as Disconnected alone.
    bool open();

    // Waits up to timeout for a packet or a link change. Timeout 0 polls. Hidraw returns one report;
    // a report longer than the buffer is cut by the kernel with no notice, so pass a big enough one.
    TransportRead read(std::span<std::uint8_t> buffer, std::chrono::milliseconds timeout);

    // Sends one packet, once: no retries and nothing is queued for later. Packets arriving meanwhile
    // are kept for read(). Usbfs waits up to timeout, then cancels the transfer and returns Timeout, and
    // the link stays up. A transfer that fails ends the link and returns Disconnected, and read()
    // reports it too. Hidraw sends the bytes as they are, so the first byte is the report id (0 on the
    // VITURE HID interfaces, then 64 bytes), and the kernel may block for up to 5 s (usbhid's output
    // timeout) whatever timeout says.
    // Ok means the device acknowledged the packet. After a failed transfer, the host's data toggle can
    // be out of step until the next one (nothing here may send CLEAR_HALT), and a device in that state
    // acknowledges a packet and drops it. Anything that must arrive needs a reply or a sequence check.
    IoStatus write(std::span<const std::uint8_t> data, std::chrono::milliseconds timeout = std::chrono::milliseconds{1000});
    // The same, to out_endpoint or one of extra_out_endpoints. Any other endpoint is Error.
    IoStatus write_to(std::uint8_t endpoint, std::span<const std::uint8_t> data, std::chrono::milliseconds timeout = std::chrono::milliseconds{1000});

    // Cancels transfers, releases the interface and closes. No reset. open() may be called again.
    void close();

    // Wakes a blocked read() with Interrupted. Safe from another thread or a signal handler.
    void interrupt() noexcept;

    bool connected() const;
    std::optional<FoundDevice> device() const;  // What is open now.
    std::optional<TransportEvent> next_event();  // Oldest unread link change.
    TransportStats stats() const;
    std::string last_error() const;  // Why the link is not up, or why the last write failed.

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace luma
