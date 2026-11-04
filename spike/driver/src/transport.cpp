// See transport.hpp for why this looks the way it does (usbfs and hidraw, no uevents, no resets).
//
// Kernel behaviour relied on, all from drivers/usb/core/devio.c and drivers/hid/hidraw.c:
//  - usbfs poll: EPOLLOUT when a URB completed, EPOLLHUP once disconnected, EPOLLERR when the file
//    has no device left.
//  - REAPURBNDELAY: EAGAIN when nothing is ready, ENODEV when disconnected with nothing left.
//  - closing a usbfs file kills its URBs and releases its interfaces, and writes nothing to user
//    memory afterwards, so URB buffers may be freed once close() returns.
//  - hidraw poll: EPOLLERR|EPOLLHUP after removal; queued reports stay readable, then EIO.
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
        // Bits 11-12 are extra transactions per microframe on high-bandwidth interrupt endpoints.
        unsigned long raw = read_number(entry / "wMaxPacketSize", 16).value_or(0);
        std::size_t max_packet = (raw & 0x7ff) * (1 + ((raw >> 11) & 3));
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

}  // namespace

std::optional<FoundDevice> find_device(const TransportConfig& config) {
    if (config.backend != Backend::Usbfs)
        if (auto found = find_hidraw(config)) return found;
    if (config.backend != Backend::Hidraw) return find_usb(config);
    return std::nullopt;
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
        int length = 0;  // What an IN transfer asks for.
        std::uint8_t endpoint = 0;
        bool busy = false;    // With the kernel.
        bool parked = false;  // Failed: resubmits at retry_at.
        Clock::time_point retry_at{};
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
    std::optional<Clock::time_point> down_since;
    std::string last_error;
    std::deque<TransportEvent> events;
    TransportStats stats;

    std::deque<Urb> urbs;  // Deque: the kernel keeps pointers to these.
    std::array<int, 256> endpoint_errors{};  // Consecutive failures, by endpoint: several URBs share one.
    int parked = 0;
    int last_urb_status = 0;
    Clock::time_point last_packet{};
    Urb* out_urb = nullptr;

    // Received packets, preallocated so the hot path does not touch the heap.
    std::vector<std::uint8_t> ring;
    std::vector<Slot> slots;
    std::size_t head = 0;
    std::size_t count = 0;

    Impl(TransportConfig c, std::shared_ptr<Sys> s) : config(std::move(c)), sys(s ? std::move(s) : std::make_shared<Sys>()) {
        config.queue_frames = std::max<std::size_t>(config.queue_frames, 1);
        config.packet_size = std::max<std::size_t>(config.packet_size, 1);
        config.urbs_per_endpoint = std::max(config.urbs_per_endpoint, 1);
        config.max_urb_errors = std::max(config.max_urb_errors, 1);
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
            if (config.vendor_id == kVitureVendorId && config.product_id == kLumaUltraProductId &&
                (address == kLumaUltraFlashOut || address == kLumaUltraFlashIn))
                return fail_open("refusing endpoint " + hex(address) + ": it also carries firmware updates");
            if (static_cast<bool>(address & 0x80) != in)
                return fail_open("endpoint " + hex(address) + (in ? " is not an IN endpoint" : " is not an OUT endpoint"));
            auto known = [&](const EndpointInfo& e) { return e.address == address; };
            if (std::none_of(found.endpoints.begin(), found.endpoints.end(), known))
                return fail_open("device has no bulk or interrupt endpoint " + hex(address));
            return true;
        };
        for (std::uint8_t address : config.in_endpoints)
            if (!check(address, true)) return false;
        return config.out_endpoint == 0 || check(config.out_endpoint, false);
    }

    void prepare_urb(Urb& u, std::uint8_t endpoint, const FoundDevice& found) {
        u.endpoint = endpoint;
        u.buffer.resize(config.packet_size);
        bool bulk = false;
        std::size_t length = config.packet_size;
        for (const EndpointInfo& e : found.endpoints) {
            if (e.address != endpoint) continue;
            bulk = e.bulk;
            if (e.max_packet > 0) length = std::min(length, e.max_packet);
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
                    return fail_open(std::string("URB submit failed: ") + std::strerror(error));
                }
            }
        }
        if (config.out_endpoint != 0) {
            out_urb = &urbs.emplace_back();
            prepare_urb(*out_urb, config.out_endpoint, found);
        }
        return true;
    }

    bool try_open() {
        auto found = find_device(config);
        if (!found) return fail_open("device not found");
        if (found->backend == Backend::Usbfs && !check_endpoints(*found)) return false;
        fd = sys->open(found->node.c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
        if (fd < 0) {
            int error = errno;  // EACCES is normal for a moment: udev is still applying the ACL.
            ++stats.open_failures;
            return fail_open("open " + found->node.string() + ": " + std::strerror(error));
        }
        if (found->backend == Backend::Usbfs && !start_usbfs(*found)) {
            teardown();
            return false;
        }
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
        endpoint_errors.fill(0);
        parked = 0;
    }

    void lose_link(std::string reason) {
        Backend backend = current ? current->backend : Backend::Usbfs;
        std::string node = current ? current->node.string() : std::string();
        const int errors_before = *std::max_element(endpoint_errors.begin(), endpoint_errors.end());
        teardown();
        current.reset();
        auto now = Clock::now();
        TransportEvent event;
        event.kind = TransportEvent::Kind::Disconnected;
        event.consecutive_urb_errors = static_cast<std::uint32_t>(errors_before);
        event.last_urb_status = last_urb_status;
        event.ms_since_last_packet = static_cast<std::uint64_t>(std::chrono::duration_cast<milliseconds>(now - last_packet).count());
        event.backend = backend;
        event.node = std::move(node);
        event.reason = reason;
        event.t_ns = now_ns(now);
        push_event(std::move(event));
        ++stats.disconnects;
        down_since = now;
        next_scan = now + config.rescan_interval;
        pending = IoStatus::Disconnected;
        last_error = std::move(reason);
    }

    // ------------------------------------------------------------- packets

    void enqueue(const Urb& u) {
        std::size_t index = (head + count) % slots.size();
        if (count == slots.size()) {  // Newest data matters most for head tracking.
            head = (head + 1) % slots.size();
            --count;
            ++stats.dropped_packets;
        }
        std::size_t bytes = std::min(static_cast<std::size_t>(u.urb->actual_length), config.packet_size);
        std::memcpy(ring.data() + index * config.packet_size, u.buffer.data(), bytes);
        last_packet = Clock::now();
        slots[index] = {bytes, u.endpoint, now_ns(last_packet)};
        ++count;
    }

    ReadResult dequeue(std::span<std::uint8_t> buffer) {
        const Slot& slot = slots[head];
        ReadResult result;
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

    void handle_in(Urb& u) {
        int status = u.urb->status;
        if (status == -ENOENT || status == -ECONNRESET) return;  // We discarded it.
        if (status == -ENODEV || status == -ESHUTDOWN) {
            lost = "transfer failed: device gone";
            return;
        }
        if (status != 0) {
            // A stall would take a CLEAR_HALT to fix, a control request we refuse to send. Reopen instead.
            // Transfer errors often come just before a drop, so give the link room (usbhid backs off too).
            ++stats.urb_errors;
            last_urb_status = status;
            int& errors = endpoint_errors[u.endpoint];
            if (++errors >= config.max_urb_errors) {
                lost = "repeated transfer errors on endpoint " + hex(u.endpoint) + ": " + std::strerror(-status);
                return;
            }
            u.parked = true;
            u.retry_at = Clock::now() + config.urb_retry_delay * (1 << std::min(errors - 1, 4));
            ++parked;
            return;
        }
        endpoint_errors[u.endpoint] = 0;
        if (u.urb->actual_length > 0) enqueue(u);
        if (lost) return;
        if (!submit(u, u.length)) lost = std::string("URB resubmit failed: ") + std::strerror(errno);
    }

    void resubmit_due() {
        if (parked == 0) return;
        const auto now = Clock::now();
        for (Urb& u : urbs) {
            if (!u.parked || now < u.retry_at) continue;
            u.parked = false;
            --parked;
            if (!lost && !submit(u, u.length)) lost = std::string("URB resubmit failed: ") + std::strerror(errno);
        }
        finish_reap();
    }

    // How long a poll may sleep before a parked URB is due.
    Clock::time_point next_wake(Clock::time_point deadline) const {
        if (parked == 0) return deadline;
        for (const Urb& u : urbs)
            if (u.parked) deadline = std::min(deadline, u.retry_at);
        return deadline;
    }

    void reap_all() {
        for (;;) {
            void* done = nullptr;
            if (guarded_ioctl(USBDEVFS_REAPURBNDELAY, &done) != 0) {
                if (errno == EINTR) continue;
                if (errno != EAGAIN) lost = std::string("reap: ") + std::strerror(errno);
                break;
            }
            Urb& u = *static_cast<Urb*>(static_cast<usbdevfs_urb*>(done)->usercontext);
            u.busy = false;
            if (&u != out_urb) handle_in(u);
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
    std::optional<ReadResult> wait_while_down(Clock::time_point deadline) {
        auto now = Clock::now();
        if (now >= next_scan) {
            next_scan = now + config.rescan_interval;
            if (try_open()) return std::nullopt;
            now = Clock::now();
        }
        if (now >= deadline) return ReadResult{IoStatus::Timeout};
        auto wait = std::min(deadline, next_scan) - now;
        pollfd p{wake_fd, POLLIN, 0};
        int ready = sys->poll(&p, 1, static_cast<int>(std::chrono::ceil<milliseconds>(wait).count()));
        if (ready < 0 && errno == EINTR) return ReadResult{IoStatus::Interrupted};
        if (ready > 0 && drain_wake()) return ReadResult{IoStatus::Interrupted};
        return std::nullopt;
    }

    std::optional<ReadResult> read_hidraw(std::span<std::uint8_t> buffer) {
        ssize_t n = sys->read(fd, buffer.data(), buffer.size());
        if (n > 0) {
            ++stats.packets;
            last_packet = Clock::now();
            ReadResult result;
            result.status = IoStatus::Ok;
            result.bytes = static_cast<std::size_t>(n);
            result.t_ns = now_ns(last_packet);
            return result;
        }
        if (n < 0 && errno == EAGAIN) return std::nullopt;
        if (n < 0 && errno == EINTR) return ReadResult{IoStatus::Interrupted};
        lose_link(n == 0 ? std::string("read: end of file") : std::string("read: ") + std::strerror(errno));
        return std::nullopt;
    }

    ReadResult read(std::span<std::uint8_t> buffer, milliseconds timeout) {
        const auto deadline = Clock::now() + std::max(timeout, milliseconds{0});
        for (;;) {
            if (!opened) return {IoStatus::Closed};
            if (count > 0) return dequeue(buffer);
            if (pending) {
                IoStatus status = *pending;
                pending.reset();
                return {status};
            }
            if (fd < 0) {
                if (auto result = wait_while_down(deadline)) return *result;
                continue;
            }

            resubmit_due();
            if (fd < 0) continue;

            const bool hidraw = current && current->backend == Backend::Hidraw;
            auto remaining = std::chrono::ceil<milliseconds>(next_wake(deadline) - Clock::now());
            pollfd fds[2] = {{fd, static_cast<short>(hidraw ? POLLIN : POLLOUT), 0}, {wake_fd, POLLIN, 0}};
            int ready = sys->poll(fds, 2, static_cast<int>(std::clamp<long long>(remaining.count(), 0, 1'000'000)));
            if (ready < 0) {
                if (errno == EINTR) return {IoStatus::Interrupted};
                lose_link(std::string("poll: ") + std::strerror(errno));
                continue;
            }
            if ((fds[1].revents & POLLIN) && drain_wake()) return {IoStatus::Interrupted};
            if (ready == 0) {
                if (Clock::now() >= deadline) return {IoStatus::Timeout};
                continue;  // A parked URB is due; the caller's wait goes on.
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
            if (!lost && (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL))) lost = "poll: device removed";
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
        resubmit_due();
        if (fd < 0) return;
        pollfd p{fd, POLLOUT, 0};
        int ready = sys->poll(&p, 1, timeout_ms);
        if (ready < 0) {
            if (errno != EINTR) lost = std::string("poll: ") + std::strerror(errno);
        } else if (ready > 0) {
            reap_all();
            if (!lost && (p.revents & (POLLHUP | POLLERR | POLLNVAL))) lost = "poll: device removed";
        }
        finish_reap();
    }

    IoStatus write_usbfs(std::span<const std::uint8_t> data, milliseconds timeout) {
        if (!out_urb) {
            last_error = "no OUT endpoint configured";
            return IoStatus::Error;
        }
        if (data.size() > config.packet_size) {
            last_error = "packet larger than packet_size";
            return IoStatus::Error;
        }
        if (!data.empty()) std::memcpy(out_urb->buffer.data(), data.data(), data.size());
        if (!submit(*out_urb, static_cast<int>(data.size()))) {
            int error = errno;
            if (is_gone_errno(error)) {
                lose_link(std::string("URB submit: ") + std::strerror(error));
                return IoStatus::Disconnected;
            }
            last_error = std::string("URB submit: ") + std::strerror(error);
            return IoStatus::Error;
        }
        const auto deadline = Clock::now() + std::max(timeout, milliseconds{0});
        while (out_urb->busy) {
            auto remaining = std::chrono::ceil<milliseconds>(deadline - Clock::now()).count();
            if (remaining <= 0) break;
            auto wake = std::chrono::ceil<milliseconds>(next_wake(deadline) - Clock::now()).count();
            pump(static_cast<int>(std::clamp<long long>(wake, 1, 1'000'000)));
            if (fd < 0) return IoStatus::Disconnected;
        }
        if (out_urb->busy) {
            // The caller decides whether to try again. A cancelled transfer is not a reason to touch the link.
            last_error = "write timed out";
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
            return IoStatus::Timeout;
        }
        int status = out_urb->urb->status;
        if (status == 0 && out_urb->urb->actual_length == static_cast<int>(data.size())) return IoStatus::Ok;
        if (status == -ENODEV || status == -ESHUTDOWN) {
            lose_link("write: device gone");
            return IoStatus::Disconnected;
        }
        last_error = status == 0 ? "short write" : std::string("write: ") + std::strerror(-status);
        return IoStatus::Error;
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
    if (i.fd >= 0) return true;
    i.opened = true;
    i.next_scan = Clock::now() + i.config.rescan_interval;
    return i.try_open();
}

ReadResult Transport::read(std::span<std::uint8_t> buffer, std::chrono::milliseconds timeout) { return impl_->read(buffer, timeout); }

IoStatus Transport::write(std::span<const std::uint8_t> data, std::chrono::milliseconds timeout) {
    Impl& i = *impl_;
    if (!i.opened) return IoStatus::Closed;
    if (i.fd < 0) return IoStatus::Disconnected;
    if (i.current->backend == Backend::Hidraw) return i.write_hidraw(data);
    return i.write_usbfs(data, timeout);
}

void Transport::close() {
    Impl& i = *impl_;
    i.teardown();
    i.opened = false;
    i.current.reset();
    i.pending.reset();
    i.lost.reset();
    i.down_since.reset();
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
