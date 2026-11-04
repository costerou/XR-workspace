// See transport.hpp for why this looks the way it does (usbfs and hidraw, no uevents, no resets).
//
// Kernel behaviour relied on, checked against the source (drivers/usb/core/devio.c, drivers/hid/hidraw.c):
//  - usbfs poll: EPOLLOUT when a URB completed, EPOLLHUP once disconnected, EPOLLERR when the file
//    has no device left. A hang-up can show while URBs are still pending.
//  - REAPURBNDELAY: EAGAIN when nothing is ready, ENODEV when disconnected with nothing left. Completed
//    URBs stay reapable after a disconnect. Status, length and data reach user memory at reap, not before.
//  - closing a usbfs file kills its URBs and releases its interfaces, and writes nothing to user
//    memory afterwards, so URB buffers may be freed once close() returns.
//  - hidraw poll: EPOLLERR|EPOLLHUP after removal; queued reports stay readable, then EIO.
//  - open, claim, submit on bulk or interrupt, reap, discard, release and close send no control transfer.
#include "luma/transport.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <fstream>
#include <linux/usbdevice_fs.h>
#include <sstream>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace luma {
namespace {

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using std::chrono::milliseconds;

std::uint64_t now_ns(Clock::time_point t = Clock::now()) {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(t.time_since_epoch()).count());
}

std::string read_line(const fs::path& path) {
    std::ifstream file(path);
    std::string line;
    std::getline(file, line);
    return line;
}

std::optional<unsigned long> parse(const std::string& text, int base) {
    if (text.empty()) return std::nullopt;
    char* end = nullptr;
    unsigned long value = std::strtoul(text.c_str(), &end, base);
    if (end == text.c_str()) return std::nullopt;
    return value;
}

std::optional<unsigned long> read_number(const fs::path& path, int base) { return parse(read_line(path), base); }

std::vector<fs::path> sorted_entries(const fs::path& dir) {
    std::vector<fs::path> entries;
    std::error_code ec;
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec)) entries.push_back(it->path());
    std::sort(entries.begin(), entries.end());
    return entries;  // Devices can vanish mid-walk; whatever goes missing just does not match.
}

std::string hex(std::uint8_t value) {
    char text[8];
    std::snprintf(text, sizeof text, "0x%02x", value);
    return text;
}

// HID_ID=0003:000035CA:00001102 in the HID device's uevent. Bus 3 is USB.
bool hid_id_matches(const fs::path& uevent, const TransportConfig& config) {
    std::ifstream file(uevent);
    for (std::string line; std::getline(file, line);) {
        unsigned bus = 0, vendor = 0, product = 0;
        if (std::sscanf(line.c_str(), "HID_ID=%x:%x:%x", &bus, &vendor, &product) == 3)
            return bus == 3 && vendor == config.vendor_id && product == config.product_id;
    }
    return false;
}

std::optional<FoundDevice> find_hidraw(const TransportConfig& config) {
    for (const fs::path& entry : sorted_entries(config.sysfs_root / "class/hidraw")) {
        std::error_code ec;
        fs::path hid = fs::canonical(entry / "device", ec);  // .../5-2.3:1.3/0003:35CA:1102.00B9
        if (ec || !hid_id_matches(hid / "uevent", config)) continue;
        auto number = read_number(hid.parent_path() / "bInterfaceNumber", 16);
        if (!number || static_cast<int>(*number) != config.interface_number) continue;
        FoundDevice found;
        found.backend = Backend::Hidraw;
        found.sys_name = entry.filename().string();
        found.node = config.dev_root / found.sys_name;
        return found;
    }
    return std::nullopt;
}

std::vector<EndpointInfo> read_endpoints(const fs::path& interface_dir) {
    std::vector<EndpointInfo> endpoints;
    for (const fs::path& entry : sorted_entries(interface_dir)) {
        const std::string name = entry.filename().string();
        if (name.rfind("ep_", 0) != 0) continue;
        auto address = parse(name.substr(3), 16);
        std::string type = read_line(entry / "type");
        if (!address || (type != "Bulk" && type != "Interrupt")) continue;
        std::size_t max_packet = read_number(entry / "wMaxPacketSize", 16).value_or(0);
        endpoints.push_back({static_cast<std::uint8_t>(*address), type == "Bulk", max_packet});
    }
    return endpoints;
}

std::optional<FoundDevice> find_usb(const TransportConfig& config) {
    for (const fs::path& entry : sorted_entries(config.sysfs_root / "bus/usb/devices")) {
        auto vendor = read_number(entry / "idVendor", 16);
        auto product = read_number(entry / "idProduct", 16);
        if (!vendor || !product || *vendor != config.vendor_id || *product != config.product_id) continue;
        auto bus = read_number(entry / "busnum", 10);
        auto device = read_number(entry / "devnum", 10);
        if (!bus || !device) continue;
        for (const fs::path& child : sorted_entries(entry)) {
            auto number = read_number(child / "bInterfaceNumber", 16);
            if (!number || static_cast<int>(*number) != config.interface_number) continue;
            FoundDevice found;
            found.backend = Backend::Usbfs;
            found.sys_name = entry.filename().string();
            char node[32];
            std::snprintf(node, sizeof node, "bus/usb/%03lu/%03lu", *bus, *device);
            found.node = config.dev_root / node;
            found.endpoints = read_endpoints(child);
            return found;
        }
    }
    return std::nullopt;
}

bool is_gone_errno(int error) { return error == ENODEV || error == ESHUTDOWN || error == EIO || error == ENXIO; }

// A negative poll timeout means wait for ever, so everything that reaches poll() comes through here.
int poll_ms(Clock::duration wait) {
    return static_cast<int>(std::clamp<long long>(std::chrono::ceil<milliseconds>(wait).count(), 0, 1'000'000));
}

// Adding a huge timeout to now would overflow. A year is as good as for ever here.
Clock::time_point deadline_after(milliseconds timeout) {
    return Clock::now() + std::clamp(timeout, milliseconds{0}, milliseconds{std::chrono::hours{24 * 365}});
}

std::string transfer_failure(std::uint8_t endpoint, int status) {
    if (status == -EPIPE) return "endpoint " + hex(endpoint) + " halted: only a re-enumeration clears that";
    if (status == -ENODEV || status == -ESHUTDOWN) return "transfer shut down on endpoint " + hex(endpoint) + ": device gone or reset";
    if (status == -ENOENT || status == -ECONNRESET) return "transfer on endpoint " + hex(endpoint) + " cancelled by the kernel";
    return "transfer failed on endpoint " + hex(endpoint) + ": " + std::strerror(-status);
}

}  // namespace

std::optional<FoundDevice> find_device(const TransportConfig& config) {
    if (config.backend != Backend::Usbfs)
        if (auto found = find_hidraw(config)) return found;
    if (config.backend != Backend::Hidraw) return find_usb(config);
    return std::nullopt;
}

bool allowed_mcu_frame(std::span<const std::uint8_t> frame) noexcept {
    static constexpr std::array<std::array<std::uint8_t, 5>, 2> kFrames{{{0xfa, 0x55, 0x95, 0x00, 0x00}, {0xfa, 0x55, 0x97, 0x00, 0x00}}};
    return std::any_of(kFrames.begin(), kFrames.end(), [&](const auto& known) {
        return frame.size() == known.size() && std::equal(frame.begin(), frame.end(), known.begin());
    });
}

bool is_allowed_ioctl(unsigned long request) {
    switch (request) {
    case USBDEVFS_CLAIMINTERFACE:
    case USBDEVFS_RELEASEINTERFACE:
    case USBDEVFS_SUBMITURB:
    case USBDEVFS_REAPURBNDELAY:
    case USBDEVFS_DISCARDURB:
        return true;
    default:
        return false;  // In particular RESET, SETCONFIGURATION, SETINTERFACE, CLEAR_HALT, DISCONNECT.
    }
}

int Sys::open(const char* path, int flags) { return ::open(path, flags); }
int Sys::close(int fd) { return ::close(fd); }
ssize_t Sys::read(int fd, void* buffer, std::size_t size) { return ::read(fd, buffer, size); }
ssize_t Sys::write(int fd, const void* buffer, std::size_t size) { return ::write(fd, buffer, size); }
int Sys::ioctl(int fd, unsigned long request, void* argument) {
    if (!is_allowed_ioctl(request)) {  // Second lock: holds even if a caller skips Impl::guarded_ioctl.
        errno = EPERM;
        return -1;
    }
    return ::ioctl(fd, request, argument);
}
int Sys::poll(pollfd* fds, nfds_t count, int timeout_ms) { return ::poll(fds, count, timeout_ms); }

struct Transport::Impl {
    struct Urb {
        std::vector<std::uint8_t> buffer;
        int length = 0;  // One packet: the most a transfer on this endpoint moves.
        std::uint8_t endpoint = 0;
        bool busy = false;  // With the kernel.
        bool out = false;    // Host to device.
        bool drain = false;  // Read and thrown away.
        std::unique_ptr<usbdevfs_urb> urb = std::make_unique<usbdevfs_urb>();  // Own allocation: it ends in a flexible array.
    };
    struct Slot {
        std::size_t bytes = 0;
        std::uint8_t endpoint = 0;
        std::uint64_t t_ns = 0;
    };

    TransportConfig config;
    std::shared_ptr<Sys> sys;

    int fd = -1;
    int wake_fd = -1;
    bool opened = false;
    std::optional<FoundDevice> current;
    std::optional<IoStatus> pending;  // Link change that read() still has to report.
    std::optional<std::string> lost;  // Set during reaping: the link ended; finish the batch first.
    Clock::time_point next_scan{};
    std::optional<Clock::time_point> first_seen;  // When the device came back, for arrival_settle.
    int open_attempts = 0;                        // Failures in a row on a device that is there.
    int failed_sessions = 0;                      // Links in a row that ended with no packet, with the device still there.
    bool session_had_packet = false;
    std::optional<Clock::time_point> down_since;
    std::string last_error;
    std::deque<TransportEvent> events;
    TransportStats stats;

    std::deque<Urb> urbs;  // Deque: the kernel keeps pointers to these.
    int last_urb_status = 0;
    Clock::time_point last_packet{};
    Urb* out_urb = nullptr;
    std::vector<Urb*> extra_out_urbs;

    // Received packets, preallocated so the hot path does not touch the heap.
    std::vector<std::uint8_t> ring;
    std::vector<Slot> slots;
    std::size_t head = 0;
    std::size_t count = 0;

    Impl(TransportConfig c, std::shared_ptr<Sys> s) : config(std::move(c)), sys(s ? std::move(s) : std::make_shared<Sys>()) {
        config.queue_frames = std::max<std::size_t>(config.queue_frames, 1);
        config.packet_size = std::max<std::size_t>(config.packet_size, 1);
        config.urbs_per_endpoint = std::max(config.urbs_per_endpoint, 2);
        config.rescan_interval = std::max(config.rescan_interval, milliseconds{1});
        ring.resize(config.queue_frames * config.packet_size);
        slots.resize(config.queue_frames);
        wake_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    }
    ~Impl() {
        if (wake_fd >= 0) ::close(wake_fd);
    }

    int guarded_ioctl(unsigned long request, void* argument) {
        if (!is_allowed_ioctl(request)) {
            errno = EPERM;
            return -1;
        }
        return sys->ioctl(fd, request, argument);
    }

    void push_event(TransportEvent event) {
        if (events.size() >= 64) events.pop_front();
        events.push_back(std::move(event));
    }

    // ------------------------------------------------------------- link up

    bool fail_open(std::string why) {
        last_error = std::move(why);
        return false;
    }

    bool check_endpoints(const FoundDevice& found) {
        if (config.in_endpoints.empty()) return fail_open("usbfs needs at least one IN endpoint in the configuration");
        auto check = [&](std::uint8_t address, bool in) {
            if (is_ultra() && !config.allow_mcu_channel && (address == kLumaUltraFlashOut || address == kLumaUltraFlashIn))
                return fail_open("refusing endpoint " + hex(address) + ": it also carries firmware updates");
            if (static_cast<bool>(address & 0x80) != in)
                return fail_open("endpoint " + hex(address) + (in ? " is not an IN endpoint" : " is not an OUT endpoint"));
            auto known = [&](const EndpointInfo& e) { return e.address == address; };
            auto endpoint = std::find_if(found.endpoints.begin(), found.endpoints.end(), known);
            if (endpoint == found.endpoints.end()) return fail_open("device has no bulk or interrupt endpoint " + hex(address));
            // A packet larger than its URB is babble, an error on the wire, not a shorter read.
            if (endpoint->max_packet > config.packet_size)
                return fail_open("packet_size is below the " + std::to_string(endpoint->max_packet) + " bytes of endpoint " + hex(address));
            return true;
        };
        for (std::uint8_t address : config.in_endpoints)
            if (!check(address, true)) return false;
        for (std::uint8_t address : config.drain_endpoints)
            if (!check(address, true)) return false;
        for (std::uint8_t address : config.extra_out_endpoints)
            if (!check(address, false)) return false;
        return config.out_endpoint == 0 || check(config.out_endpoint, false);
    }

    bool is_ultra() const { return config.vendor_id == kVitureVendorId && config.product_id == kLumaUltraProductId; }

    void prepare_urb(Urb& u, std::uint8_t endpoint, const FoundDevice& found) {
        u.endpoint = endpoint;
        u.buffer.resize(config.packet_size);
        bool bulk = false;
        std::size_t length = config.packet_size;
        for (const EndpointInfo& e : found.endpoints) {
            if (e.address != endpoint) continue;
            bulk = e.bulk;
            if (e.max_packet > 0) length = e.max_packet;
        }
        u.length = static_cast<int>(length);
        u.urb->type = bulk ? USBDEVFS_URB_TYPE_BULK : USBDEVFS_URB_TYPE_INTERRUPT;
        u.urb->endpoint = endpoint;
        u.urb->buffer = u.buffer.data();
        u.urb->usercontext = &u;
    }

    bool submit(Urb& u, int length) {
        u.urb->status = 0;
        u.urb->actual_length = 0;
        u.urb->buffer_length = length;
        if (guarded_ioctl(USBDEVFS_SUBMITURB, u.urb.get()) != 0) return false;
        u.busy = true;
        return true;
    }

    bool start_usbfs(const FoundDevice& found) {
        unsigned int interface_number = static_cast<unsigned int>(config.interface_number);
        if (guarded_ioctl(USBDEVFS_CLAIMINTERFACE, &interface_number) != 0) {
            int error = errno;
            ++stats.open_failures;
            stats.last_open_errno = error;
            return fail_open(error == EBUSY ? "interface busy: another program or kernel driver holds it"
                                            : std::string("claim failed: ") + std::strerror(error));
        }
        for (std::uint8_t endpoint : config.in_endpoints) {
            for (int i = 0; i < config.urbs_per_endpoint; ++i) {
                Urb& u = urbs.emplace_back();
                prepare_urb(u, endpoint, found);
                if (!submit(u, u.length)) {
                    int error = errno;
                    ++stats.open_failures;
                    stats.last_open_errno = error;
                    return fail_open(std::string("URB submit failed: ") + std::strerror(error));
                }
            }
        }
        for (std::uint8_t endpoint : config.drain_endpoints) {
            for (int i = 0; i < config.drain_urbs; ++i) {
                Urb& u = urbs.emplace_back();
                prepare_urb(u, endpoint, found);
                u.drain = true;
                u.buffer.resize(config.drain_urb_bytes);
                u.length = static_cast<int>(config.drain_urb_bytes);
                u.urb->buffer = u.buffer.data();
                if (!submit(u, u.length)) {
                    int error = errno;
                    ++stats.open_failures;
                    stats.last_open_errno = error;
                    return fail_open(std::string("URB submit failed: ") + std::strerror(error));
                }
            }
        }
        const auto add_out = [&](std::uint8_t endpoint) {
            Urb* urb = &urbs.emplace_back();
            prepare_urb(*urb, endpoint, found);
            urb->out = true;
            return urb;
        };
        if (config.out_endpoint != 0) out_urb = add_out(config.out_endpoint);
        extra_out_urbs.clear();
        for (std::uint8_t endpoint : config.extra_out_endpoints) extra_out_urbs.push_back(add_out(endpoint));
        return true;
    }

    bool retry_later() {
        auto delay = config.rescan_interval * (1 << std::min(++open_attempts, 10));
        next_scan = Clock::now() + std::max(config.rescan_interval, std::min<milliseconds>(delay, config.open_retry_max));
        return false;
    }

    // Reopening cannot clear a halt, so a link that keeps dying with the device still present must not
    // cycle: each such session doubles the hold. The device leaving and returning clears it.
    bool settling() {
        if (!down_since) return false;
        auto hold = config.arrival_settle * (1 << std::min(failed_sessions, 4));
        if (failed_sessions > 0) hold = std::max(hold, config.open_retry_max);
        if (hold <= milliseconds{0}) return false;
        const auto now = Clock::now();
        if (!first_seen) first_seen = now;
        return now < *first_seen + hold;
    }

    bool try_open() {
        auto found = find_device(config);
        if (!found) {
            first_seen.reset();
            open_attempts = 0;
            failed_sessions = 0;
            stats.last_open_errno = 0;
            return fail_open("device not found");
        }
        if (settling()) return false;  // last_error() keeps saying why the link went down.
        if (found->backend == Backend::Usbfs && !check_endpoints(*found)) return retry_later();
        fd = sys->open(found->node.c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
        if (fd < 0) {
            int error = errno;  // EACCES is normal for a moment: udev is still applying the ACL.
            ++stats.open_failures;
            stats.last_open_errno = error;
            std::string why = "open " + found->node.string() + ": " + std::strerror(error);
            // Lasting EACCES: a device present since boot can have no user ACL (see transport.md).
            if (error == EACCES || error == EPERM) why += " (no user access to the glasses: replug them, or install the udev rule)";
            fail_open(std::move(why));
            return retry_later();
        }
        if (found->backend == Backend::Usbfs && !start_usbfs(*found)) {
            teardown();
            return retry_later();
        }
        first_seen.reset();
        open_attempts = 0;
        stats.last_open_errno = 0;
        session_had_packet = false;
        auto now = Clock::now();
        last_packet = now;
        last_urb_status = 0;
        TransportEvent event;
        event.kind = TransportEvent::Kind::Connected;
        event.backend = found->backend;
        event.node = found->node.string();
        event.t_ns = now_ns(now);
        if (down_since) {
            event.downtime_ms = static_cast<std::uint64_t>(std::chrono::duration_cast<milliseconds>(now - *down_since).count());
            stats.last_downtime_ms = event.downtime_ms;
            down_since.reset();
        }
        push_event(std::move(event));
        ++stats.connects;
        current = std::move(found);
        pending = IoStatus::Connected;
        last_error.clear();
        return true;
    }

    // ------------------------------------------------------------- link down

    // Works whether the device is still there or not: on a removed device these ioctls fail harmlessly.
    void teardown() {
        if (!urbs.empty()) {  // Only exists once the interface is claimed.
            for (Urb& u : urbs)
                if (u.busy) guarded_ioctl(USBDEVFS_DISCARDURB, u.urb.get());
            for (std::size_t i = 0; i < urbs.size(); ++i) {  // Collects what the discards just finished.
                void* done = nullptr;
                if (guarded_ioctl(USBDEVFS_REAPURBNDELAY, &done) != 0) break;
            }
            unsigned int interface_number = static_cast<unsigned int>(config.interface_number);
            guarded_ioctl(USBDEVFS_RELEASEINTERFACE, &interface_number);
        }
        if (fd >= 0) sys->close(fd);
        fd = -1;
        urbs.clear();
        out_urb = nullptr;
        extra_out_urbs.clear();
    }

    void lose_link(std::string reason) {
        Backend backend = current ? current->backend : Backend::Usbfs;
        std::string node = current ? current->node.string() : std::string();
        teardown();
        current.reset();
        auto now = Clock::now();
        TransportEvent event;
        event.kind = TransportEvent::Kind::Disconnected;
        event.last_urb_status = last_urb_status;
        event.ms_since_last_packet = static_cast<std::uint64_t>(std::chrono::duration_cast<milliseconds>(now - last_packet).count());
        event.backend = backend;
        event.node = std::move(node);
        event.reason = reason;
        event.t_ns = now_ns(now);
        push_event(std::move(event));
        ++stats.disconnects;
        down_since = now;
        first_seen.reset();
        if (!session_had_packet) ++failed_sessions;
        next_scan = now + config.rescan_interval;
        pending = IoStatus::Disconnected;
        last_error = std::move(reason);
    }

    // ------------------------------------------------------------- packets

    void note_packet() {
        last_packet = Clock::now();
        session_had_packet = true;
        failed_sessions = 0;
    }

    void enqueue(const Urb& u) {
        std::size_t index = (head + count) % slots.size();
        if (count == slots.size()) {  // Newest data matters most for head tracking.
            head = (head + 1) % slots.size();
            --count;
            ++stats.dropped_packets;
        }
        std::size_t bytes = std::min(static_cast<std::size_t>(u.urb->actual_length), config.packet_size);
        std::memcpy(ring.data() + index * config.packet_size, u.buffer.data(), bytes);
        note_packet();
        slots[index] = {bytes, u.endpoint, now_ns(last_packet)};
        ++count;
    }

    TransportRead dequeue(std::span<std::uint8_t> buffer) {
        const Slot& slot = slots[head];
        TransportRead result;
        result.status = IoStatus::Ok;
        result.bytes = std::min(slot.bytes, buffer.size());
        result.truncated = slot.bytes > buffer.size();
        result.endpoint = slot.endpoint;
        result.t_ns = slot.t_ns;
        if (result.bytes > 0) std::memcpy(buffer.data(), ring.data() + head * config.packet_size, result.bytes);
        head = (head + 1) % slots.size();
        --count;
        ++stats.packets;
        return result;
    }

    void mark_lost(std::string why) {
        if (!lost) lost = std::move(why);  // The first cause is the one worth keeping.
    }

    // Any failed read ends the link (protocol.md: close everything after a failed transfer). The host
    // has already used its own retries by now, every other queued read tends to fail with it, and
    // nothing here may clear a halt or resynchronise a data toggle. Closing is also what actually gets
    // the host to stop polling. The queue is therefore always either full or gone.
    void handle_in(Urb& u) {
        const int status = u.urb->status;
        if (status != 0) {
            ++stats.urb_errors;
            last_urb_status = status;
            mark_lost(transfer_failure(u.endpoint, status));
            return;
        }
        if (u.urb->actual_length > 0 && !u.drain) enqueue(u);
        if (!lost && !submit(u, u.length)) mark_lost(std::string("URB resubmit failed: ") + std::strerror(errno));
    }

    void reap_all() {
        for (;;) {
            void* done = nullptr;
            if (guarded_ioctl(USBDEVFS_REAPURBNDELAY, &done) != 0) {
                if (errno == EINTR) continue;
                if (errno != EAGAIN) mark_lost(std::string("reap: ") + std::strerror(errno));
                break;
            }
            Urb& u = *static_cast<Urb*>(static_cast<usbdevfs_urb*>(done)->usercontext);
            u.busy = false;
            if (!u.out) handle_in(u);
        }
    }

    // Everything reaped so far stays readable: read() hands it out before it reports the disconnect.
    void finish_reap() {
        if (lost) {
            std::string reason = std::move(*lost);
            lost.reset();
            lose_link(std::move(reason));
        }
    }

    // ------------------------------------------------------------- read

    bool drain_wake() {
        std::uint64_t value = 0;
        return ::read(wake_fd, &value, sizeof value) > 0;
    }

    // One step of waiting for the device. Empty means look again; try_open() leaves Connected in pending.
    std::optional<TransportRead> wait_while_down(Clock::time_point deadline) {
        auto now = Clock::now();
        if (now >= next_scan) {
            next_scan = now + config.rescan_interval;
            if (try_open()) return std::nullopt;
            now = Clock::now();
        }
        if (now >= deadline) return TransportRead{IoStatus::Timeout};
        auto wait = std::min(deadline, next_scan) - now;
        pollfd p{wake_fd, POLLIN, 0};
        int ready = sys->poll(&p, 1, poll_ms(wait));
        if (ready < 0 && errno == EINTR) return TransportRead{IoStatus::Interrupted};
        if (ready > 0 && drain_wake()) return TransportRead{IoStatus::Interrupted};
        return std::nullopt;
    }

    std::optional<TransportRead> read_hidraw(std::span<std::uint8_t> buffer) {
        ssize_t n = sys->read(fd, buffer.data(), buffer.size());
        if (n > 0) {
            ++stats.packets;
            note_packet();
            TransportRead result;
            result.status = IoStatus::Ok;
            result.bytes = static_cast<std::size_t>(n);
            result.t_ns = now_ns(last_packet);
            return result;
        }
        if (n < 0 && errno == EAGAIN) return std::nullopt;
        if (n < 0 && errno == EINTR) return TransportRead{IoStatus::Interrupted};
        lose_link(n == 0 ? std::string("read: end of file") : std::string("read: ") + std::strerror(errno));
        return std::nullopt;
    }

    TransportRead read(std::span<std::uint8_t> buffer, milliseconds timeout) {
        const auto deadline = deadline_after(timeout);
        for (;;) {
            if (!opened) return {IoStatus::Closed};
            if (count > 0) {
                // A backed-up device is when the ring holds packets, and its queue must not run dry
                // while the caller works through them, so handing one over refills it too.
                if (fd >= 0 && current->backend == Backend::Usbfs) {
                    reap_all();
                    finish_reap();
                }
                return dequeue(buffer);
            }
            if (pending) {
                IoStatus status = *pending;
                pending.reset();
                return {status};
            }
            if (fd < 0) {
                if (auto result = wait_while_down(deadline)) return *result;
                continue;
            }

            const bool hidraw = current->backend == Backend::Hidraw;
            pollfd fds[2] = {{fd, static_cast<short>(hidraw ? POLLIN : POLLOUT), 0}, {wake_fd, POLLIN, 0}};
            int ready = sys->poll(fds, 2, poll_ms(deadline - Clock::now()));
            if (ready < 0) {
                if (errno == EINTR) return {IoStatus::Interrupted};
                lose_link(std::string("poll: ") + std::strerror(errno));
                continue;
            }
            if ((fds[1].revents & POLLIN) && drain_wake()) return {IoStatus::Interrupted};
            if (ready == 0) {
                if (Clock::now() >= deadline) return {IoStatus::Timeout};
                continue;  // Only a very long wait, split up by poll_ms().
            }

            if (hidraw) {
                // Removal with reports still queued shows POLLIN and POLLHUP together: read those first.
                if (fds[0].revents & POLLIN) {
                    if (auto result = read_hidraw(buffer)) return *result;
                } else if (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL)) {
                    lose_link("poll: device removed");
                }
                continue;
            }
            reap_all();
            if (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL)) mark_lost("poll: device removed");
            finish_reap();
        }
    }

    // ------------------------------------------------------------- write

    IoStatus write_hidraw(std::span<const std::uint8_t> data) {
        ssize_t n = sys->write(fd, data.data(), data.size());
        if (n == static_cast<ssize_t>(data.size())) return IoStatus::Ok;
        if (n >= 0) {
            last_error = "short write";
            return IoStatus::Error;
        }
        int error = errno;
        if (error == EINTR) return IoStatus::Interrupted;
        if (error == EAGAIN || error == ETIMEDOUT) return IoStatus::Timeout;
        if (is_gone_errno(error)) {
            lose_link(std::string("write: ") + std::strerror(error));
            return IoStatus::Disconnected;
        }
        last_error = std::string("write: ") + std::strerror(error);
        return IoStatus::Error;
    }

    // One poll-and-reap. Packets that arrive go to the queue.
    void pump(int timeout_ms) {
        pollfd p{fd, POLLOUT, 0};
        int ready = sys->poll(&p, 1, timeout_ms);
        if (ready < 0) {
            if (errno != EINTR) mark_lost(std::string("poll: ") + std::strerror(errno));
        } else if (ready > 0) {
            reap_all();
            if (p.revents & (POLLHUP | POLLERR | POLLNVAL)) mark_lost("poll: device removed");
        }
        finish_reap();
    }

    IoStatus write_usbfs(Urb* out_urb, std::span<const std::uint8_t> data, milliseconds timeout) {
        if (!out_urb) {
            last_error = "no OUT endpoint configured";
            return IoStatus::Error;
        }
        if (is_ultra() && out_urb->endpoint == kLumaUltraFlashOut && !allowed_mcu_frame(data)) {
            last_error = "refusing a frame on 0x04 that is not on the allowlist: it also carries firmware updates";
            return IoStatus::Error;
        }
        if (data.size() > static_cast<std::size_t>(out_urb->length)) {
            last_error = "write is larger than one packet (" + std::to_string(out_urb->length) + " bytes)";
            return IoStatus::Error;
        }
        if (!data.empty()) std::memcpy(out_urb->buffer.data(), data.data(), data.size());
        if (!submit(*out_urb, static_cast<int>(data.size()))) {
            lose_link(std::string("URB submit: ") + std::strerror(errno));
            return IoStatus::Disconnected;
        }
        const auto deadline = deadline_after(timeout);
        do {
            pump(poll_ms(deadline - Clock::now()));
            if (fd < 0) return IoStatus::Disconnected;
        } while (out_urb->busy && Clock::now() < deadline);
        if (out_urb->busy) {
            // A cancelled transfer is no reason to touch the link: the caller logs it and does not retry.
            guarded_ioctl(USBDEVFS_DISCARDURB, out_urb->urb.get());
            const auto give_up = Clock::now() + milliseconds{100};
            while (out_urb->busy && Clock::now() < give_up) {
                pump(10);
                if (fd < 0) return IoStatus::Disconnected;
            }
            if (out_urb->busy) {
                lose_link("write would not cancel");
                return IoStatus::Disconnected;
            }
        }
        const int status = out_urb->urb->status;
        if (status == 0) {
            // It may have finished just as the timeout fired. Calling that a failure invites a duplicate.
            if (out_urb->urb->actual_length == static_cast<int>(data.size())) return IoStatus::Ok;
            last_error = "short write";
            return IoStatus::Error;
        }
        if (status == -ENOENT || status == -ECONNRESET) {
            last_error = "write timed out";
            return IoStatus::Timeout;
        }
        ++stats.urb_errors;
        last_urb_status = status;
        lose_link(transfer_failure(out_urb->endpoint, status));
        return IoStatus::Disconnected;
    }

    void reset_queue() {
        head = 0;
        count = 0;
    }
};

Transport::Transport(TransportConfig config, std::shared_ptr<Sys> sys) : impl_(std::make_unique<Impl>(std::move(config), std::move(sys))) {}

Transport::~Transport() { close(); }

bool Transport::open() {
    Impl& i = *impl_;
    if (i.opened) return i.fd >= 0;  // Already started: reconnecting belongs to read().
    i.opened = true;
    i.next_scan = Clock::now() + i.config.rescan_interval;
    return i.try_open();
}

TransportRead Transport::read(std::span<std::uint8_t> buffer, std::chrono::milliseconds timeout) { return impl_->read(buffer, timeout); }

IoStatus Transport::write(std::span<const std::uint8_t> data, std::chrono::milliseconds timeout) {
    Impl& i = *impl_;
    if (!i.opened) return IoStatus::Closed;
    if (i.fd < 0) return IoStatus::Disconnected;
    if (i.current->backend == Backend::Hidraw) return i.write_hidraw(data);
    return i.write_usbfs(i.out_urb, data, timeout);
}

IoStatus Transport::write_to(std::uint8_t endpoint, std::span<const std::uint8_t> data, std::chrono::milliseconds timeout) {
    Impl& i = *impl_;
    if (!i.opened) return IoStatus::Closed;
    if (i.fd < 0) return IoStatus::Disconnected;
    if (i.current->backend == Backend::Hidraw) {
        i.last_error = "hidraw has no endpoints to choose from";
        return IoStatus::Error;
    }
    if (i.out_urb && i.out_urb->endpoint == endpoint) return i.write_usbfs(i.out_urb, data, timeout);
    for (Impl::Urb* urb : i.extra_out_urbs)
        if (urb->endpoint == endpoint) return i.write_usbfs(urb, data, timeout);
    i.last_error = "endpoint " + hex(endpoint) + " is not configured for writes";
    return IoStatus::Error;
}

void Transport::close() {
    Impl& i = *impl_;
    i.teardown();
    i.opened = false;
    i.current.reset();
    i.pending.reset();
    i.lost.reset();
    i.down_since.reset();
    i.first_seen.reset();
    i.open_attempts = 0;
    i.failed_sessions = 0;
    i.reset_queue();
}

void Transport::interrupt() noexcept {
    std::uint64_t one = 1;
    [[maybe_unused]] ssize_t ignored = ::write(impl_->wake_fd, &one, sizeof one);
}

bool Transport::connected() const { return impl_->fd >= 0; }

std::optional<FoundDevice> Transport::device() const { return impl_->current; }

std::optional<TransportEvent> Transport::next_event() {
    Impl& i = *impl_;
    if (i.events.empty()) return std::nullopt;
    TransportEvent event = std::move(i.events.front());
    i.events.pop_front();
    return event;
}

TransportStats Transport::stats() const { return impl_->stats; }

std::string Transport::last_error() const { return impl_->last_error; }

}  // namespace luma
