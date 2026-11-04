#pragma once
// luma::Transport: the byte pipe to the glasses, with no knowledge of what the bytes mean.
//
// What the hardware looks like (read from sysfs on 2026-11-03, cached by the kernel, device untouched):
//   35ca:1104 "VITURE Luma Ultra XR GLASSES", high speed: ONE vendor interface (class 00), no kernel
//     driver and so NO hidraw node. Endpoints: interrupt IN 0x81 (1024) and 0x83 (512), bulk IN 0x82,
//     0x85, 0x87 (512), bulk OUT 0x04, 0x06 (512). Reachable through usbfs only.
//   35ca:1102 "VITURE Microphone", full speed: interfaces 3 and 4 are 64-byte interrupt HID
//     (hidraw), vendor-undefined reports with no report id.
// Which of these carries the IMU is the protocol's question, so both backends are first class:
// Hidraw for a HID interface, Usbfs (raw /dev/bus/usb URBs, no libusb) for the rest. Auto takes a
// matching hidraw node, otherwise the USB device. The driver log's "returned -7" is LIBUSB_ERROR_TIMEOUT,
// which shows the vendor SDK talks to the 1104 through usbfs too.
//
// Hotplug uses neither libudev nor netlink, on purpose. tracking.py runs the driver in a network
// namespace owned by a non-initial user namespace, and the kernel only delivers uevents to namespaces
// owned by the initial user namespace (lib/kobject_uevent.c, "Restrict uevents to initial user
// namespace"), so a monitor there would stay silent forever. Measured 2026-11-03 with one netlink
// listener in a normal session and one in tracking.py's exact sandbox, side by side: 73 events
// reached the first and none the second. Instead:
//   removal  : the open file itself: POLLHUP/POLLERR, ENODEV, EIO. Immediate, works in any namespace.
//   re-arrival: rescan sysfs every rescan_interval while down. Reads attributes the kernel caches,
//              so it puts nothing on the USB bus.
//
// Open errors are retried like absence, since EACCES is normal for a moment after arrival. It can also
// last: a device present since boot may have no user ACL (the udev rule matches ACTION=="add" only).
// last_error() then keeps saying "Permission denied".
//
// Safety: this class never resets the device, never sets a configuration or alternate setting,
// never clears a halt, never detaches a kernel driver and never touches the Ultra's firmware endpoints. Every ioctl goes through an allowlist,
// is_allowed_ioctl(), so those calls cannot be made by accident. If another program holds the
// interface (a running xrDriver), the claim fails with EBUSY and we keep waiting; we never take it.
//
// Threading: one thread drives read(), write(), open(), close(), next_event() and stats(). Only
// interrupt() may be called from another thread or a signal handler. There are no internal threads:
// reconnecting happens inside read().
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
// pair, 0x04/0x85, also carries firmware updates, so Transport refuses to use it on this device.
inline constexpr std::uint8_t kLumaUltraDataOut = 0x06;
inline constexpr std::uint8_t kLumaUltraDataIn = 0x87;
inline constexpr std::uint8_t kLumaUltraFlashOut = 0x04;
inline constexpr std::uint8_t kLumaUltraFlashIn = 0x85;

enum class Backend { Auto, Hidraw, Usbfs };

struct TransportConfig {
    std::uint16_t vendor_id = kVitureVendorId;
    std::uint16_t product_id = kLumaUltraProductId;
    int interface_number = 0;
    Backend backend = Backend::Auto;

    // Usbfs only. Addresses as the descriptor gives them (IN has 0x80 set). Every IN endpoint listed
    // is read at once and read() says which one a packet came from; out_endpoint 0 means no writes.
    std::vector<std::uint8_t> in_endpoints;
    std::uint8_t out_endpoint = 0;
    std::size_t packet_size = 1024;  // Bytes per URB. Must be at least the endpoint's largest transfer.
    int urbs_per_endpoint = 2;       // In flight per IN endpoint, so a packet never waits for us to resubmit.
    std::size_t queue_frames = 256;  // Received packets held until read() takes them; the oldest goes first.
    int max_urb_errors = 8;          // Consecutive failed transfers on one endpoint before the link is reopened.

    std::chrono::milliseconds rescan_interval{100};  // Sysfs rescan period while disconnected.

    std::filesystem::path sysfs_root = "/sys";
    std::filesystem::path dev_root = "/dev";
};

struct EndpointInfo {
    std::uint8_t address = 0;
    bool bulk = false;  // Otherwise interrupt; control and isochronous endpoints are not usable here.
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
    Ok,            // read: a packet is in the buffer. write: everything went out.
    Timeout,       // Nothing within the timeout. The link is as it was.
    Interrupted,   // interrupt() was called, or a signal arrived.
    Connected,     // read only: the link just came up (first time or again). Send start-stream now.
    Disconnected,  // read: the link just went down, reported once. write: there is no link now.
    Closed,        // close() was called, or open() never was.
    Error,         // write only: the transfer failed but the link stands (detail in last_error()).
};

struct ReadResult {
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
};

struct TransportStats {
    std::uint64_t connects = 0;
    std::uint64_t disconnects = 0;
    std::uint64_t packets = 0;          // Handed to read().
    std::uint64_t dropped_packets = 0;  // Pushed out of a full queue.
    std::uint64_t urb_errors = 0;       // Failed transfers that did not by themselves end the link.
    std::uint64_t open_failures = 0;    // Found the device but could not open or claim it.
    std::uint64_t last_downtime_ms = 0;
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

class Transport {
public:
    explicit Transport(TransportConfig config, std::shared_ptr<Sys> sys = nullptr);
    ~Transport();
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    // Starts the link. True if the device is up right now. False is not fatal: read() keeps looking
    // (see last_error()). Either way read() reports Connected once the link is up.
    bool open();

    // Waits up to timeout for a packet or a link change. Timeout 0 polls. Hidraw returns one report;
    // a report longer than the buffer is cut by the kernel with no notice, so pass a big enough one.
    ReadResult read(std::span<std::uint8_t> buffer, std::chrono::milliseconds timeout);

    // Sends one packet, once: no retries and nothing is queued for later. Packets arriving meanwhile
    // are kept for read(). Usbfs waits up to timeout, then cancels the transfer. Hidraw sends the bytes
    // as they are, so the first byte is the report id (0 on the VITURE HID interfaces, then 64 bytes),
    // and the kernel may block for up to 5 s (usbhid's output timeout) whatever timeout says.
    IoStatus write(std::span<const std::uint8_t> data, std::chrono::milliseconds timeout = std::chrono::milliseconds{1000});

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
