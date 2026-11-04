// luma::Transport against fakes. No real device is ever opened.
//
// Hidraw runs through the real read/poll/write path: a fake sysfs tree whose /dev node is a symlink
// to a pty. Closing the pty master gives the slave POLLHUP and EIO, which is what a removed hidraw
// node gives (drivers/hid/hidraw.c).
//
// Usbfs has no such stand-in without root (dummy_hcd), so FakeUsbfs imitates the kernel's URB
// behaviour, read from the kernel source: drivers/usb/core/devio.c, driver.c, message.c and xhci-ring.c.
// See the class comment for what it models.
#include "luma/transport.hpp"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <linux/usbdevice_fs.h>
#include <mutex>
#include <new>
#include <set>
#include <stdlib.h>
#include <string>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;
using namespace std::chrono_literals;
using luma::Backend;
using luma::IoStatus;
using luma::Transport;
using luma::TransportConfig;
using luma::TransportEvent;
using Clock = std::chrono::steady_clock;

static std::atomic<long> heap_allocations{0};
[[gnu::noinline]] void* operator new(std::size_t size) {
    heap_allocations.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

static int failures = 0;
#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            std::fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                   \
        }                                                                 \
    } while (0)

namespace {

long ms_since(Clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count();
}

int open_fd_count() {
    int count = 0;
    for ([[maybe_unused]] const auto& entry : fs::directory_iterator("/proc/self/fd")) ++count;
    return count;
}

void write_file(const fs::path& path, const std::string& text) {
    fs::create_directories(path.parent_path());
    std::ofstream(path) << text << '\n';
}

// Builds the same shape as the real /sys and /dev. Paths inside are the ones seen on the machine.
class FakeTree {
public:
    FakeTree() {
        std::string pattern = (fs::temp_directory_path() / "luma-transport-XXXXXX").string();
        root_ = mkdtemp(pattern.data());
        fs::create_directories(sysfs() / "class/hidraw");
        fs::create_directories(sysfs() / "bus/usb/devices");
        fs::create_directories(dev());
    }
    ~FakeTree() { fs::remove_all(root_); }
    fs::path sysfs() const { return root_ / "sys"; }
    fs::path dev() const { return root_ / "dev"; }
    fs::path usb_dir(const std::string& name) const { return sysfs() / "devices/usb5/5-2" / name; }

    void add_usb(const std::string& name, const char* vid, const char* pid, int devnum,
                 const std::vector<std::pair<std::string, std::string>>& endpoints = {}, int interface_number = 0) {
        fs::path d = usb_dir(name);
        write_file(d / "idVendor", vid);
        write_file(d / "idProduct", pid);
        write_file(d / "busnum", "5");
        write_file(d / "devnum", std::to_string(devnum));
        char number[8];
        std::snprintf(number, sizeof number, "%02x", interface_number);
        fs::path intf = d / (name + ":1." + std::to_string(interface_number));
        write_file(intf / "bInterfaceNumber", number);
        for (const auto& [address, type] : endpoints) {
            write_file(intf / ("ep_" + address) / "type", type);
            write_file(intf / ("ep_" + address) / "wMaxPacketSize", address == "81" ? "0400" : "0200");  // As on the glasses.
        }
        fs::create_directory_symlink(d, sysfs() / "bus/usb/devices" / name);
        fs::create_directory_symlink(intf, sysfs() / "bus/usb/devices" / intf.filename());
        char node[32];
        std::snprintf(node, sizeof node, "bus/usb/005/%03d", devnum);
        write_file(dev() / node, "");
    }
    void remove_usb(const std::string& name, int devnum) {
        std::error_code ec;
        for (const auto& entry : fs::directory_iterator(sysfs() / "bus/usb/devices"))
            if (entry.path().filename().string().rfind(name, 0) == 0) fs::remove(entry.path(), ec);
        fs::remove_all(usb_dir(name), ec);
        char node[32];
        std::snprintf(node, sizeof node, "bus/usb/005/%03d", devnum);
        fs::remove(dev() / node, ec);
    }

    void add_hidraw(const std::string& node, const std::string& usb_name, const char* hid_id, int interface_number,
                    const fs::path& target, const char* vid = "35ca", const char* pid = "1102") {
        fs::path d = usb_dir(usb_name);
        write_file(d / "idVendor", vid);
        write_file(d / "idProduct", pid);
        write_file(d / "busnum", "5");
        write_file(d / "devnum", "7");
        fs::path intf = d / (usb_name + ":1." + std::to_string(interface_number));
        char number[8];
        std::snprintf(number, sizeof number, "%02x", interface_number);
        write_file(intf / "bInterfaceNumber", number);
        fs::path hid = intf / (std::string("0003:") + node);
        write_file(hid / "uevent", std::string("DRIVER=hid-generic\nHID_ID=") + hid_id + "\nHID_NAME=fake");
        fs::path dir = hid / "hidraw" / node;
        fs::create_directories(dir);
        fs::create_directory_symlink("../..", dir / "device");
        fs::create_directory_symlink(dir, sysfs() / "class/hidraw" / node);
        fs::create_symlink(target, dev() / node);
    }
    void remove_hidraw(const std::string& node, const std::string& usb_name) {
        std::error_code ec;
        fs::remove(sysfs() / "class/hidraw" / node, ec);
        fs::remove(dev() / node, ec);
        fs::remove_all(usb_dir(usb_name), ec);
    }

private:
    fs::path root_;
};

// The device end of a hidraw stand-in.
class Pty {
public:
    Pty() {
        master_ = posix_openpt(O_RDWR | O_NOCTTY | O_CLOEXEC);
        if (master_ < 0 || grantpt(master_) != 0 || unlockpt(master_) != 0) std::abort();
        termios raw{};
        tcgetattr(master_, &raw);
        cfmakeraw(&raw);
        tcsetattr(master_, TCSANOW, &raw);
        path_ = ptsname(master_);
    }
    ~Pty() { hang_up(); }
    const std::string& path() const { return path_; }
    void send(const std::vector<std::uint8_t>& bytes) {
        if (::write(master_, bytes.data(), bytes.size()) != static_cast<ssize_t>(bytes.size())) std::abort();
    }
    std::vector<std::uint8_t> receive(std::size_t size) {
        std::vector<std::uint8_t> out(size);
        std::size_t have = 0;
        pollfd p{master_, POLLIN, 0};
        while (have < size && ::poll(&p, 1, 500) > 0) {
            ssize_t n = ::read(master_, out.data() + have, size - have);
            if (n <= 0) break;
            have += static_cast<std::size_t>(n);
        }
        out.resize(have);
        return out;
    }
    void hang_up() {
        if (master_ >= 0) ::close(master_);
        master_ = -1;
    }

private:
    int master_ = -1;
    std::string path_;
};

TransportConfig hid_config(const FakeTree& tree) {
    TransportConfig c;
    c.product_id = luma::kVitureMicProductId;
    c.interface_number = 3;
    c.backend = Backend::Hidraw;
    c.rescan_interval = 10ms;
    c.arrival_settle = 0ms;
    c.open_retry_max = 20ms;
    c.sysfs_root = tree.sysfs();
    c.dev_root = tree.dev();
    return c;
}

TransportConfig usb_config(const FakeTree& tree) {
    TransportConfig c;
    c.in_endpoints = {0x81, 0x82};
    c.out_endpoint = luma::kLumaUltraDataOut;
    c.urbs_per_endpoint = 2;
    c.rescan_interval = 10ms;
    c.arrival_settle = 0ms;
    c.open_retry_max = 20ms;
    c.sysfs_root = tree.sysfs();
    c.dev_root = tree.dev();
    return c;
}

const std::vector<std::pair<std::string, std::string>> kEndpoints = {
    {"81", "Interrupt"}, {"82", "Bulk"}, {"87", "Bulk"}, {"04", "Bulk"}, {"06", "Bulk"}, {"85", "Bulk"}};

// A stand-in for usbfs. What it copies from the kernel, and where:
//  - A transfer ends on a short packet or a full buffer; a packet larger than the URB is babble.
//  - Status, length and data reach user memory only at reap (processcompl).
//  - A disconnect has stages: connected() goes false first, so POLLHUP shows and reap gives ENODEV
//    with URBs still pending, and DISCARD gives ENODEV. URBs then flush, often with -EPROTO first, and
//    sysfs and the node go last (usb_disconnect). A hang-up wakes poll with nothing in flight (usbdev_remove).
//  - A failed bulk URB leaves the others queued (cancel_bulk_urbs does nothing without CONTINUATION).
//  - One fault fails every queued URB: xHCI's soft retries are shared, so after the first they run out.
//  - A stall outlives a reopen: nothing on the close or claim path sends CLEAR_FEATURE(HALT).
//  - Another program's reset flushes URBs with -ESHUTDOWN and drops our claim, with no hang-up and the
//    same device number (usb_reset_device forces an unbind; usbfs has no pre_reset).
//  - DISCARD on a URB that already completed gives EINVAL.
class FakeUsbfs : public luma::Sys {
public:
    explicit FakeUsbfs(FakeTree& tree) : tree_(tree) {}
    ~FakeUsbfs() override {
        if (event_ >= 0) ::close(event_);
    }

    // Test controls -----------------------------------------------------
    void arrive(int devnum) {
        std::lock_guard lock(m_);
        devnum_ = devnum;
        gone_ = false;
        stalled_.clear();  // Re-enumeration is what clears a halt.
        tree_.add_usb("5-2.1", "35ca", "1104", devnum, kEndpoints);
    }
    void begin_disconnect() {
        std::lock_guard lock(m_);
        gone_ = true;
        signal();
    }
    void flush(int status) {
        std::lock_guard lock(m_);
        for (auto* urb : inflight_) finish(urb, status, 0);
        inflight_.clear();
    }
    void remove_nodes() {
        std::lock_guard lock(m_);
        tree_.remove_usb("5-2.1", devnum_);
    }
    void yank(int status = -ESHUTDOWN) {
        begin_disconnect();
        flush(status);
        remove_nodes();
    }
    bool deliver(std::uint8_t endpoint, const std::vector<std::uint8_t>& bytes, int status = 0) {
        std::lock_guard lock(m_);
        for (auto it = inflight_.begin(); it != inflight_.end(); ++it) {
            usbdevfs_urb* urb = *it;
            if (urb->endpoint != endpoint) continue;
            int n = static_cast<int>(bytes.size());
            int max_packet = endpoint == 0x81 ? 1024 : 512;
            if (status == 0 && n > urb->buffer_length) {
                status = -EOVERFLOW;
            } else if (status == 0 && n > 0 && n % max_packet == 0 && n < urb->buffer_length) {
                return true;  // A full-size packet into a bigger buffer: the URB waits for more.
            }
            inflight_.erase(it);
            finish(urb, status, status == 0 ? n : 0, status == 0 ? bytes : std::vector<std::uint8_t>{});
            return true;
        }
        ++missed_;
        return false;
    }
    // Everything queued on one endpoint fails in one go.
    void fail_all(std::uint8_t endpoint, int status) {
        std::lock_guard lock(m_);
        fail_queued(endpoint, status);
    }
    // Halted on the device: every transfer fails until a re-enumeration.
    void stall(std::uint8_t endpoint) {
        std::lock_guard lock(m_);
        stalled_.insert(endpoint);
        fail_queued(endpoint, -EPIPE);
    }
    void foreign_reset() {
        std::lock_guard lock(m_);
        for (auto* urb : inflight_) finish(urb, -ESHUTDOWN, 0);
        inflight_.clear();
        claimed_now_ = false;
    }
    void set_busy(bool busy) { std::lock_guard lock(m_); busy_ = busy; }
    void fail_opens(int count, int error) { std::lock_guard lock(m_); open_failures_ = count; open_errno_ = error; }
    void hold_out_urbs(bool hold) { std::lock_guard lock(m_); hold_out_ = hold; }
    void out_status(int status) { std::lock_guard lock(m_); out_status_ = status; }
    void finish_held_urb_on_discard() { std::lock_guard lock(m_); finish_on_discard_ = true; }
    void stop_recording() { std::lock_guard lock(m_); recording_ = false; }

    // Inspection --------------------------------------------------------
    struct SeenUrb { int endpoint, type, length; unsigned flags; };
    std::vector<SeenUrb> seen() { std::lock_guard lock(m_); return seen_; }
    std::vector<int> claimed_list() { std::lock_guard lock(m_); return claimed_list_; }
    int opens() { std::lock_guard lock(m_); return opens_; }
    std::vector<std::vector<std::uint8_t>> sent() { std::lock_guard lock(m_); return sent_; }
    std::vector<int> submitted_endpoints() { std::lock_guard lock(m_); return submitted_; }
    std::multiset<unsigned long> requests() { std::lock_guard lock(m_); return requests_; }
    std::size_t inflight() { std::lock_guard lock(m_); return inflight_.size(); }
    int claims() { std::lock_guard lock(m_); return claims_; }
    int releases() { std::lock_guard lock(m_); return releases_; }
    int auto_claims() { std::lock_guard lock(m_); return auto_claims_; }
    int claimed_interface() { std::lock_guard lock(m_); return claimed_; }
    int open_flags() { std::lock_guard lock(m_); return open_flags_; }
    int missed() { std::lock_guard lock(m_); return missed_; }
    bool descriptor_open() { std::lock_guard lock(m_); return event_ >= 0; }

    // Sys ---------------------------------------------------------------
    int open(const char* path, int flags) override {
        std::lock_guard lock(m_);
        if (!fs::exists(path)) return fail(ENOENT);
        ++opens_;
        if (gone_) return fail(ENODEV);  // The node outlives the device for a moment.
        if (open_failures_ > 0) {
            --open_failures_;
            return fail(open_errno_);
        }
        open_flags_ = flags;
        event_ = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        return event_;
    }
    int close(int fd) override {
        std::lock_guard lock(m_);
        if (fd != event_) return ::close(fd);
        ::close(event_);  // usbdev_release() destroys every URB of this file.
        event_ = -1;
        inflight_.clear();
        done_.clear();
        claimed_now_ = false;
        return 0;
    }
    int poll(pollfd* fds, nfds_t count, int timeout_ms) override {
        pollfd real[4];
        std::copy(fds, fds + count, real);
        int event;
        {
            std::lock_guard lock(m_);
            event = event_;
            for (nfds_t i = 0; i < count; ++i)
                if (real[i].fd == event && gone_) timeout_ms = 0;  // A hang-up never blocks.
        }
        for (nfds_t i = 0; i < count; ++i)
            if (real[i].fd == event && (real[i].events & POLLOUT)) real[i].events = POLLIN;  // eventfd stands for "a URB completed".
        int result = ::poll(real, count, timeout_ms);
        int saved = errno;
        if (result < 0) {
            errno = saved;
            return result;
        }
        std::lock_guard lock(m_);
        result = 0;
        for (nfds_t i = 0; i < count; ++i) {
            fds[i].revents = real[i].revents;
            if (fds[i].fd == event) {
                if (real[i].revents & POLLIN) fds[i].revents = static_cast<short>((fds[i].revents & ~POLLIN) | POLLOUT);
                if (gone_) fds[i].revents |= POLLHUP | POLLERR;
            }
            if (fds[i].revents) ++result;
        }
        errno = saved;
        return result;
    }
    int ioctl(int, unsigned long request, void* argument) override {
        std::lock_guard lock(m_);
        if (recording_) requests_.insert(request);
        if (request == USBDEVFS_CLAIMINTERFACE) {
            if (gone_) return fail(ENODEV);
            if (busy_) return fail(EBUSY);
            ++claims_;
            claimed_ = static_cast<int>(*static_cast<unsigned int*>(argument));
            claimed_list_.push_back(claimed_);
            claimed_now_ = true;
            return 0;
        }
        if (request == USBDEVFS_RELEASEINTERFACE) {
            ++releases_;
            if (gone_) return fail(ENODEV);
            if (!claimed_now_) return fail(EINVAL);
            claimed_now_ = false;
            return 0;
        }
        if (request == USBDEVFS_SUBMITURB) {
            if (gone_) return fail(ENODEV);
            if (!claimed_now_) ++auto_claims_;  // checkintf() would claim it behind our back, with a warning.
            auto* urb = static_cast<usbdevfs_urb*>(argument);
            if (recording_) {
                submitted_.push_back(urb->endpoint);
                seen_.push_back({urb->endpoint, urb->type, urb->buffer_length, urb->flags});
            }
            if (stalled_.count(urb->endpoint)) {
                finish(urb, -EPIPE, 0);
            } else if (urb->endpoint & 0x80) {
                inflight_.push_back(urb);
            } else if (hold_out_) {
                inflight_.push_back(urb);
            } else {
                auto* data = static_cast<std::uint8_t*>(urb->buffer);
                sent_.emplace_back(data, data + urb->buffer_length);
                finish(urb, out_status_, out_status_ == 0 ? urb->buffer_length : 0);
            }
            return 0;
        }
        if (request == USBDEVFS_DISCARDURB) {
            if (gone_) return fail(ENODEV);
            auto it = std::find(inflight_.begin(), inflight_.end(), static_cast<usbdevfs_urb*>(argument));
            if (it == inflight_.end()) return fail(EINVAL);
            usbdevfs_urb* urb = *it;
            inflight_.erase(it);
            if (finish_on_discard_) {  // It finished just before the discard reached it.
                finish(urb, 0, urb->buffer_length);
                return fail(EINVAL);
            }
            finish(urb, -ENOENT, 0);
            return 0;
        }
        if (request == USBDEVFS_REAPURBNDELAY) {
            if (done_.empty()) {
                drain();
                return fail(gone_ ? ENODEV : EAGAIN);
            }
            Done done = std::move(done_.front());
            done_.pop_front();
            done.urb->status = done.status;  // processcompl: user memory changes here, not at completion.
            done.urb->actual_length = done.actual;
            if (!done.data.empty()) std::memcpy(done.urb->buffer, done.data.data(), done.data.size());
            *static_cast<void**>(argument) = done.urb;
            if (done_.empty()) drain();
            return 0;
        }
        return fail(ENOTTY);
    }

private:
    struct Done {
        usbdevfs_urb* urb;
        int status;
        int actual;
        std::vector<std::uint8_t> data;
    };
    int fail(int error) {
        errno = error;
        return -1;
    }
    void fail_queued(std::uint8_t endpoint, int status) {
        for (auto it = inflight_.begin(); it != inflight_.end();) {
            if ((*it)->endpoint != endpoint) {
                ++it;
                continue;
            }
            finish(*it, status, 0);
            it = inflight_.erase(it);
        }
    }
    void finish(usbdevfs_urb* urb, int status, int actual, std::vector<std::uint8_t> data = {}) {
        done_.push_back({urb, status, actual, std::move(data)});
        signal();
    }
    void signal() {
        std::uint64_t one = 1;
        if (event_ >= 0 && ::write(event_, &one, sizeof one) < 0) std::abort();
    }
    void drain() {
        std::uint64_t value;
        if (event_ >= 0 && ::read(event_, &value, sizeof value) < 0 && errno != EAGAIN) std::abort();
    }

    FakeTree& tree_;
    std::mutex m_;
    int event_ = -1;
    int devnum_ = 0;
    bool gone_ = true;
    bool busy_ = false;
    bool hold_out_ = false;
    bool finish_on_discard_ = false;
    bool recording_ = true;
    bool claimed_now_ = false;
    int out_status_ = 0;
    int open_failures_ = 0;
    int open_errno_ = 0;
    int open_flags_ = 0;
    int opens_ = 0;
    int claims_ = 0;
    int releases_ = 0;
    int auto_claims_ = 0;
    int claimed_ = -1;
    int missed_ = 0;
    std::deque<usbdevfs_urb*> inflight_;
    std::deque<Done> done_;
    std::set<std::uint8_t> stalled_;
    std::vector<std::vector<std::uint8_t>> sent_;
    std::vector<int> submitted_;
    std::vector<SeenUrb> seen_;
    std::vector<int> claimed_list_;
    std::multiset<unsigned long> requests_;
};

using Buffer = std::array<std::uint8_t, 2048>;

// Reads until the status appears or the budget runs out; skips Timeouts. Returns the matching result.
luma::TransportRead read_until(Transport& t, Buffer& buffer, IoStatus wanted, std::chrono::milliseconds budget = 2000ms) {
    auto end = Clock::now() + budget;
    luma::TransportRead r;
    while (Clock::now() < end) {
        r = t.read(buffer, 20ms);
        if (r.status == wanted) return r;
    }
    r.status = IoStatus::Timeout;
    return r;
}

std::shared_ptr<FakeUsbfs> connected_usb(FakeTree& tree, Transport*& out, TransportConfig config) {
    auto fake = std::make_shared<FakeUsbfs>(tree);
    fake->arrive(101);
    out = new Transport(std::move(config), fake);
    return fake;
}

// ---------------------------------------------------------------- discovery

void test_finds_hid_interface_among_decoys() {
    FakeTree tree;
    Pty a, b, c;
    tree.add_hidraw("hidraw2", "5-1", "0003:000033E4:00005317", 3, a.path(), "33e4", "5317");  // Other vendor.
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 2, b.path());               // Right device, wrong interface.
    tree.add_hidraw("hidraw11", "5-2.3", "0003:000035CA:00001102", 3, c.path());               // This one.
    auto found = luma::find_device(hid_config(tree));
    CHECK(found.has_value());
    if (!found) return;
    CHECK(found->backend == Backend::Hidraw);
    CHECK(found->sys_name == "hidraw11");
    CHECK(found->node == tree.dev() / "hidraw11");
}

void test_finds_usb_device_and_endpoints() {
    FakeTree tree;
    tree.add_usb("5-1", "1532", "0574", 2);
    tree.add_usb("5-2.1", "35ca", "1104", 101, kEndpoints);
    auto found = luma::find_device(usb_config(tree));
    CHECK(found.has_value());
    if (!found) return;
    CHECK(found->backend == Backend::Usbfs);
    CHECK(found->sys_name == "5-2.1");
    CHECK(found->node == tree.dev() / "bus/usb/005/101");
    CHECK(found->endpoints.size() == 6);
    auto type = [&](std::uint8_t address) {
        for (const auto& e : found->endpoints)
            if (e.address == address) return e.bulk ? 1 : 0;
        return -1;
    };
    CHECK(type(0x81) == 0);
    CHECK(type(0x82) == 1);
    CHECK(type(0x04) == 1);
    for (const auto& e : found->endpoints) CHECK(e.max_packet == (e.address == 0x81 ? 1024u : 512u));
}

void test_finds_nothing_for_absent_device() {
    FakeTree tree;
    tree.add_usb("5-1", "1532", "0574", 2);
    CHECK(!luma::find_device(usb_config(tree)).has_value());
}

void test_finds_nothing_when_interface_absent() {
    FakeTree tree;
    tree.add_usb("5-2.1", "35ca", "1104", 101, kEndpoints);
    auto config = usb_config(tree);
    config.interface_number = 1;
    CHECK(!luma::find_device(config).has_value());
}

// Auto on the real glasses: there is no hidraw node for the 1104.
void test_auto_prefers_hidraw_then_falls_back_to_usbfs() {
    FakeTree tree;
    tree.add_usb("5-2.1", "35ca", "1104", 101, kEndpoints);
    auto config = usb_config(tree);
    config.backend = Backend::Auto;
    auto found = luma::find_device(config);
    CHECK(found && found->backend == Backend::Usbfs);

    Pty pty;
    tree.add_hidraw("hidraw3", "5-9", "0003:000035CA:00001104", 0, pty.path(), "35ca", "1104");
    found = luma::find_device(config);
    CHECK(found && found->backend == Backend::Hidraw);
}

void test_finds_real_shaped_paths_only_through_given_roots() {
    FakeTree tree;  // An empty fake root must not fall through to the machine's real /sys.
    CHECK(!luma::find_device(hid_config(tree)).has_value());
}

// ---------------------------------------------------------------- hidraw

void test_hidraw_read_and_write() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    Transport t(hid_config(tree));
    CHECK(t.open());
    CHECK(t.connected());
    Buffer buffer;
    CHECK(t.read(buffer, 100ms).status == IoStatus::Connected);
    pty.send({0xfd, 1, 2, 3});
    auto r = t.read(buffer, 500ms);
    CHECK(r.status == IoStatus::Ok);
    CHECK(r.bytes == 4);
    CHECK(buffer[0] == 0xfd && buffer[3] == 3);
    CHECK(r.t_ns != 0);
    CHECK(t.write(std::vector<std::uint8_t>{0x00, 0xaa, 0xbb}) == IoStatus::Ok);
    CHECK(pty.receive(3) == (std::vector<std::uint8_t>{0x00, 0xaa, 0xbb}));
    CHECK(t.stats().packets == 1);
}

void test_read_times_out_on_time() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 0ms);
    auto start = Clock::now();
    CHECK(t.read(buffer, 60ms).status == IoStatus::Timeout);
    long took = ms_since(start);
    CHECK(took >= 55 && took < 300);
    start = Clock::now();
    CHECK(t.read(buffer, 0ms).status == IoStatus::Timeout);
    CHECK(ms_since(start) < 50);
}

void test_hidraw_disconnect_mid_read_then_reconnect() {
    FakeTree tree;
    Pty first;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, first.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);

    std::thread yank([&] {
        std::this_thread::sleep_for(50ms);
        tree.remove_hidraw("hidraw10", "5-2.3");
        first.hang_up();
    });
    auto start = Clock::now();
    auto r = t.read(buffer, 5000ms);  // Blocked here when the device goes.
    long noticed = ms_since(start);
    yank.join();
    CHECK(r.status == IoStatus::Disconnected);
    CHECK(noticed < 1000);
    CHECK(!t.connected());

    Pty second;
    tree.add_hidraw("hidraw12", "5-2.3", "0003:000035CA:00001102", 3, second.path());  // Comes back under a new number.
    start = Clock::now();
    r = read_until(t, buffer, IoStatus::Connected);
    CHECK(r.status == IoStatus::Connected);
    CHECK(ms_since(start) < 1000);
    CHECK(t.connected());
    CHECK(t.device() && t.device()->sys_name == "hidraw12");
    second.send({9, 8});
    r = t.read(buffer, 500ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 2 && buffer[0] == 9);
    CHECK(t.stats().disconnects == 1 && t.stats().connects == 2);
}

void test_no_device_at_start_connects_when_it_arrives() {
    FakeTree tree;
    Transport t(hid_config(tree));
    CHECK(!t.open());
    CHECK(!t.connected());
    CHECK(!t.last_error().empty());
    Buffer buffer;
    CHECK(t.read(buffer, 30ms).status == IoStatus::Timeout);
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    CHECK(read_until(t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
}

void test_interrupt_wakes_a_blocked_read() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);
    std::thread waker([&] {
        std::this_thread::sleep_for(30ms);
        t.interrupt();
    });
    auto start = Clock::now();
    CHECK(t.read(buffer, 5000ms).status == IoStatus::Interrupted);
    CHECK(ms_since(start) < 1000);
    waker.join();
    CHECK(t.read(buffer, 0ms).status == IoStatus::Timeout);  // One wake, one Interrupted.
}

void test_interrupt_wakes_a_read_while_disconnected() {
    FakeTree tree;
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    std::thread waker([&] {
        std::this_thread::sleep_for(30ms);
        t.interrupt();
    });
    auto start = Clock::now();
    CHECK(t.read(buffer, 5000ms).status == IoStatus::Interrupted);
    CHECK(ms_since(start) < 1000);
    waker.join();
}

void test_write_while_down_says_so_and_queues_nothing() {
    FakeTree tree;
    Pty first;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, first.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);
    tree.remove_hidraw("hidraw10", "5-2.3");
    first.hang_up();
    CHECK(read_until(t, buffer, IoStatus::Disconnected, 1000ms).status == IoStatus::Disconnected);
    CHECK(t.write(std::vector<std::uint8_t>{1, 2, 3}) == IoStatus::Disconnected);

    Pty second;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, second.path());
    read_until(t, buffer, IoStatus::Connected, 1000ms);
    CHECK(second.receive(1).empty());  // The refused write did not come back to life.
}

void test_close_stops_reads_and_open_reconnects() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    int before = open_fd_count();
    {
        Transport t(hid_config(tree));
        Buffer buffer;
        CHECK(t.read(buffer, 0ms).status == IoStatus::Closed);  // open() not called yet.
        t.open();
        t.close();
        CHECK(!t.connected());
        CHECK(t.read(buffer, 0ms).status == IoStatus::Closed);
        CHECK(t.write(std::vector<std::uint8_t>{1}) == IoStatus::Closed);
        CHECK(t.open());
        CHECK(t.read(buffer, 0ms).status == IoStatus::Connected);
    }
    CHECK(open_fd_count() == before);
}

void test_events_tell_the_story_once() {
    FakeTree tree;
    Pty first;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, first.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);
    tree.remove_hidraw("hidraw10", "5-2.3");
    first.hang_up();
    read_until(t, buffer, IoStatus::Disconnected, 1000ms);
    std::this_thread::sleep_for(40ms);
    Pty second;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, second.path());
    read_until(t, buffer, IoStatus::Connected, 1000ms);

    auto e = t.next_event();
    CHECK(e && e->kind == TransportEvent::Kind::Connected && e->backend == Backend::Hidraw && e->downtime_ms == 0);
    e = t.next_event();
    CHECK(e && e->kind == TransportEvent::Kind::Disconnected && !e->reason.empty() && e->t_ns != 0);
    e = t.next_event();
    CHECK(e && e->kind == TransportEvent::Kind::Connected && e->downtime_ms >= 30 && e->downtime_ms < 1000);
    CHECK(!t.next_event().has_value());
    CHECK(t.stats().last_downtime_ms >= 30);
}

// ---------------------------------------------------------------- usbfs

void test_usbfs_claims_then_keeps_urbs_in_flight_on_every_in_endpoint() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    CHECK(t->device() && t->device()->backend == Backend::Usbfs);
    CHECK(fake->claims() == 1);
    CHECK(fake->claimed_interface() == 0);
    CHECK(fake->inflight() == 4);  // 2 URBs x 2 endpoints.
    CHECK(fake->open_flags() & O_CLOEXEC);
    CHECK(fake->open_flags() & O_RDWR);
    Buffer buffer;
    CHECK(t->read(buffer, 100ms).status == IoStatus::Connected);
}

void test_usbfs_stream_keeps_order_and_names_the_endpoint() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    std::atomic<int> taken{0};
    std::thread device([&] {
        for (int i = 0; i < 2000; ++i) {
            // Stays within the ring's reach: a real device has a fixed rate, this one has none.
            while (i - taken.load() > 100) std::this_thread::yield();
            auto end = Clock::now() + 1000ms;
            while (!fake->deliver(0x81, {static_cast<std::uint8_t>(i), static_cast<std::uint8_t>(i >> 8)}) && Clock::now() < end)
                std::this_thread::yield();
        }
    });
    int expected = 0;
    bool in_order = true;
    auto end = Clock::now() + 5000ms;
    while (expected < 2000 && Clock::now() < end) {
        auto r = t->read(buffer, 50ms);
        if (r.status != IoStatus::Ok) continue;
        CHECK(r.endpoint == 0x81);
        in_order = in_order && (buffer[0] | buffer[1] << 8) == expected;
        ++expected;
        taken.store(expected);
    }
    device.join();
    CHECK(expected == 2000);
    CHECK(in_order);

    fake->deliver(0x82, {7, 7, 7});
    auto r = t->read(buffer, 500ms);
    CHECK(r.status == IoStatus::Ok && r.endpoint == 0x82 && r.bytes == 3);
    CHECK(t->stats().dropped_packets == 0);
}

void test_usbfs_ignores_empty_packets_but_keeps_reading() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->deliver(0x82, {});
    CHECK(t->read(buffer, 30ms).status == IoStatus::Timeout);
    CHECK(fake->inflight() == 4);
}

void test_usbfs_rejects_endpoints_the_device_does_not_have() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {0x8f};
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(!t->open());
    CHECK(t->last_error().find("0x8f") != std::string::npos);
    CHECK(fake->claims() == 0);
    CHECK(!fake->descriptor_open());
}

// 0x04/0x85 also carry firmware updates, so a wrong endpoint in a config must not reach them.
void test_usbfs_refuses_the_flash_channel_on_the_ultra() {
    for (bool out : {true, false}) {
        FakeTree tree;
        auto config = usb_config(tree);
        if (out) config.out_endpoint = 0x04;
        else config.in_endpoints = {0x85};
        Transport* raw;
        auto fake = connected_usb(tree, raw, config);
        std::unique_ptr<Transport> t(raw);
        CHECK(!t->open());
        CHECK(t->last_error().find("firmware") != std::string::npos);
        CHECK(fake->claims() == 0);
        CHECK(!fake->descriptor_open());
    }
}

// The stream start lives on that same channel, so it opens on request. Then only the exact captured frames pass.
TransportConfig live_config(const FakeTree& tree) {
    auto config = usb_config(tree);
    config.in_endpoints = {0x81, 0x85};
    config.out_endpoint = 0x04;
    config.allow_mcu_channel = true;
    return config;
}

void test_mcu_channel_passes_only_allowlisted_frames() {
    using Bytes = std::vector<std::uint8_t>;
    CHECK(luma::allowed_mcu_frame(Bytes{0xfa, 0x55, 0x95, 0x00, 0x00}));
    CHECK(luma::allowed_mcu_frame(Bytes{0xfa, 0x55, 0x97, 0x00, 0x00}));
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, live_config(tree));
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    CHECK(t->write(Bytes{0xfa, 0x55, 0x95, 0x00, 0x00}) == IoStatus::Ok);
    CHECK(fake->sent().size() == 1);
    const std::vector<Bytes> refused{
        {},
        {0xfa, 0x55},
        {0xfa, 0x55, 0x95, 0x00},                    // cut short
        {0xfa, 0x55, 0x95, 0x00, 0x00, 0x00},        // one byte long
        {0xfa, 0x55, 0x95, 0x00, 0x01, 0x00},        // with a payload
        {0xfa, 0x55, 0x96, 0x00, 0x00},              // a neighbouring id: exactly the guess this list is here to stop
        {0xfa, 0x55, 0x90, 0x00, 0x02, 0x06, 0xc0},  // captured, but exposure control has no place here
        {0xfa, 0x55, 0xe5, 0x01, 0x00},              // captured, but it reads calibration
        {0xff, 0xfe, 0x00, 0x00},
    };
    for (const Bytes& frame : refused) {
        CHECK(t->write(frame) == IoStatus::Error);
        CHECK(t->last_error().find("allowlist") != std::string::npos);
    }
    CHECK(fake->sent().size() == 1);  // none of them reached the device
    CHECK(t->connected());
}

void test_live_path_with_captured_packets() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, live_config(tree));
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    std::vector<std::uint8_t> buffer(1024);
    CHECK(t->read(buffer, 100ms).status == IoStatus::Connected);
    CHECK(t->write(std::vector<std::uint8_t>{0xfa, 0x55, 0x95, 0x00, 0x00}) == IoStatus::Ok);
    CHECK(fake->deliver(0x85, {0xfa, 0x55, 0x95, 0x00, 0x00, 0x00}));  // captured reply
    const std::vector<std::uint8_t> imu{0xaa, 0x8f, 0x00, 0x88, 0x3c, 0x15, 0x10, 0x00, 0xdd, 0x1e, 0x01, 0x00, 0xf4, 0xff,
                                        0x05, 0x00, 0x24, 0x00, 0x15, 0xff, 0x11, 0xf8, 0x74, 0x1f, 0xe8, 0x06, 0x00, 0x00};
    CHECK(fake->deliver(0x81, imu));
    auto reply = t->read(buffer, 100ms);
    CHECK(reply.status == IoStatus::Ok && reply.endpoint == 0x85 && reply.bytes == 6);
    auto sample = t->read(buffer, 100ms);
    CHECK(sample.status == IoStatus::Ok && sample.endpoint == 0x81 && sample.bytes == imu.size());
    CHECK(std::equal(imu.begin(), imu.end(), buffer.begin()));
}

void test_write_to_reaches_only_configured_endpoints() {
    FakeTree tree;
    auto config = live_config(tree);
    config.extra_out_endpoints = {0x06};
    config.in_endpoints.push_back(0x87);
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    CHECK(t->write_to(0x06, std::vector<std::uint8_t>(512, 1)) == IoStatus::Ok);
    CHECK(t->write_to(0x02, std::vector<std::uint8_t>{1}) == IoStatus::Error);
    CHECK(t->write_to(0x04, std::vector<std::uint8_t>{0xfa, 0x55, 0x90, 0x00, 0x00}) == IoStatus::Error);  // still the allowlist
    CHECK(t->write_to(0x04, std::vector<std::uint8_t>{0xfa, 0x55, 0x97, 0x00, 0x00}) == IoStatus::Ok);
    CHECK(fake->sent().size() == 2);
}

void test_drain_endpoint_reads_and_throws_away() {
    FakeTree tree;
    auto config = live_config(tree);
    config.drain_endpoints = {0x82};
    config.drain_urbs = 3;
    config.drain_urb_bytes = 16384;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    std::vector<std::uint8_t> buffer(1024);
    CHECK(t->read(buffer, 100ms).status == IoStatus::Connected);
    CHECK(fake->deliver(0x82, std::vector<std::uint8_t>(16384, 9)));
    auto r = t->read(buffer, 30ms);
    CHECK(r.status == IoStatus::Timeout);  // nothing for the reader
    const auto endpoints = fake->submitted_endpoints();
    CHECK(std::count(endpoints.begin(), endpoints.end(), 0x82) == 4);  // 3 queued, and the finished one went straight back
    CHECK(t->connected());
}

void test_usbfs_needs_an_in_endpoint() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints.clear();
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(!t->open());
    CHECK(t->last_error().find("endpoint") != std::string::npos);
    CHECK(fake->claims() == 0);
}

void test_usbfs_waits_for_a_busy_interface_and_never_takes_it() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    fake->set_busy(true);
    CHECK(!t->open());
    CHECK(t->last_error().find("busy") != std::string::npos || t->last_error().find("Busy") != std::string::npos);
    CHECK(!fake->descriptor_open());  // Failed claim: nothing left open.
    Buffer buffer;
    CHECK(t->read(buffer, 40ms).status == IoStatus::Timeout);
    CHECK(t->stats().open_failures >= 2);
    fake->set_busy(false);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
}

void test_usbfs_retries_while_udev_applies_permissions() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    fake->fail_opens(3, EACCES);
    CHECK(!t->open());
    Buffer buffer;
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
}

void test_usbfs_disconnect_mid_read_then_reconnect() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);

    fake->deliver(0x81, {1});
    CHECK(t->read(buffer, 200ms).status == IoStatus::Ok);
    fake->deliver(0x81, {2});  // Arrives just before the drop: must still reach the reader.
    std::thread yank([&] {
        std::this_thread::sleep_for(30ms);
        fake->yank();
    });
    auto r = t->read(buffer, 500ms);
    CHECK(r.status == IoStatus::Ok && buffer[0] == 2);
    auto start = Clock::now();
    r = t->read(buffer, 5000ms);
    yank.join();
    CHECK(r.status == IoStatus::Disconnected);
    CHECK(ms_since(start) < 1000);
    CHECK(!fake->descriptor_open());
    CHECK(t->read(buffer, 5ms).status == IoStatus::Timeout);  // Disconnected is reported once.

    std::this_thread::sleep_for(60ms);
    fake->arrive(102);  // Same port, new device number, as after a real re-enumeration.
    start = Clock::now();
    r = read_until(*t, buffer, IoStatus::Connected, 1000ms);
    CHECK(r.status == IoStatus::Connected);
    CHECK(ms_since(start) < 1000);
    CHECK(t->device() && t->device()->node == tree.dev() / "bus/usb/005/102");
    CHECK(fake->inflight() == 4);  // URBs are back up without being asked.
    CHECK(fake->claims() == 2);
    fake->deliver(0x81, {3});
    r = t->read(buffer, 500ms);
    CHECK(r.status == IoStatus::Ok && buffer[0] == 3);
    CHECK(t->stats().disconnects == 1);
}

// Drops come in bursts seconds apart. Nothing may pile up.
void test_usbfs_survives_a_burst_of_drops_without_leaking() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    int fds = open_fd_count();
    for (int i = 0; i < 30; ++i) {
        fake->yank();
        CHECK(read_until(*t, buffer, IoStatus::Disconnected, 500ms).status == IoStatus::Disconnected);
        fake->arrive(102 + i);
        CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
        CHECK(fake->inflight() == 4);
    }
    CHECK(open_fd_count() == fds);
    CHECK(t->stats().disconnects == 30);
    CHECK(t->stats().connects == 31);
}

void test_usbfs_write_goes_out_once_with_the_right_type() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    CHECK(t->write(std::vector<std::uint8_t>{0xff, 0xfe, 1}) == IoStatus::Ok);
    auto sent = fake->sent();
    CHECK(sent.size() == 1 && sent[0] == (std::vector<std::uint8_t>{0xff, 0xfe, 1}));
    auto endpoints = fake->submitted_endpoints();
    CHECK(std::count(endpoints.begin(), endpoints.end(), luma::kLumaUltraDataOut) == 1);
}

void test_usbfs_write_timeout_cancels_and_does_not_retry_or_drop_the_link() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->hold_out_urbs(true);
    auto start = Clock::now();
    CHECK(t->write(std::vector<std::uint8_t>{1}, 40ms) == IoStatus::Timeout);
    CHECK(ms_since(start) < 500);
    CHECK(t->connected());
    CHECK(fake->requests().count(USBDEVFS_DISCARDURB) == 1);
    CHECK(fake->inflight() == 4);  // Only the IN URBs remain.
    auto endpoints = fake->submitted_endpoints();
    CHECK(std::count(endpoints.begin(), endpoints.end(), luma::kLumaUltraDataOut) == 1);  // One try.
    fake->hold_out_urbs(false);
    CHECK(t->write(std::vector<std::uint8_t>{2}) == IoStatus::Ok);
}

void test_usbfs_write_keeps_packets_that_arrive_meanwhile() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->hold_out_urbs(true);
    std::thread device([&] {
        std::this_thread::sleep_for(10ms);
        fake->deliver(0x81, {42});
    });
    t->write(std::vector<std::uint8_t>{1}, 40ms);
    device.join();
    auto r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && buffer[0] == 42);
    CHECK(fake->inflight() == 4);
}

// write() sends one packet, so a larger buffer is refused rather than split in two.
void test_usbfs_write_refuses_more_than_one_packet_and_missing_out_endpoint() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    CHECK(t->write(std::vector<std::uint8_t>(513, 1)) == IoStatus::Error);
    CHECK(fake->sent().empty());
    CHECK(t->connected());
    CHECK(t->write(std::vector<std::uint8_t>(512, 1)) == IoStatus::Ok);

    FakeTree other;
    auto no_out = usb_config(other);
    no_out.out_endpoint = 0;
    Transport* raw2;
    auto fake2 = connected_usb(other, raw2, no_out);
    std::unique_ptr<Transport> t2(raw2);
    t2->open();
    CHECK(t2->write(std::vector<std::uint8_t>{1}) == IoStatus::Error);
}

void test_usbfs_write_during_a_drop_reports_disconnected() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->yank();
    CHECK(t->write(std::vector<std::uint8_t>{1}) == IoStatus::Disconnected);
    CHECK(!t->connected());
    CHECK(read_until(*t, buffer, IoStatus::Disconnected, 500ms).status == IoStatus::Disconnected);  // read still hears of it.
}

void test_usbfs_full_queue_drops_oldest_and_counts_it() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.queue_frames = 4;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    // A slow reader: packets land while write() pumps the queue.
    fake->hold_out_urbs(true);
    std::thread device([&] {
        for (int i = 0; i < 6; ++i) {
            auto end = Clock::now() + 500ms;
            while (!fake->deliver(0x81, {static_cast<std::uint8_t>(i)}) && Clock::now() < end) std::this_thread::yield();
        }
    });
    t->write(std::vector<std::uint8_t>{1}, 100ms);
    device.join();
    CHECK(t->stats().dropped_packets == 2);
    auto r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && buffer[0] == 2);  // 0 and 1 are gone; the newest survive.
}

void test_usbfs_flags_a_truncated_packet() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    std::array<std::uint8_t, 4> small{};
    t->read(small, 100ms);
    fake->deliver(0x81, {1, 2, 3, 4, 5, 6});
    auto r = t->read(small, 500ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 4 && r.truncated);
}

// A zero-length packet is legal on USB, and a null span must not reach memcpy (UBSan builds catch that).
void test_usbfs_empty_write_and_empty_read_buffer() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(t->write(std::span<const std::uint8_t>{}) == IoStatus::Ok);
    CHECK(fake->sent().size() == 1 && fake->sent()[0].empty());
    fake->deliver(0x81, {1, 2});
    auto r = t->read(std::span<std::uint8_t>{}, 500ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 0 && r.truncated);
}

// The data channel's frames are exactly one packet. A URB bigger than that would hold each frame
// back until the next one arrives.
void test_usbfs_full_size_packet_arrives_at_once() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {luma::kLumaUltraDataIn, 0x81};
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(fake->deliver(luma::kLumaUltraDataIn, std::vector<std::uint8_t>(512, 7)));
    auto r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 512 && r.endpoint == luma::kLumaUltraDataIn);
    CHECK(fake->deliver(0x81, std::vector<std::uint8_t>(1024, 8)));  // Keeps its own, larger size.
    r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 1024);
}

void test_a_disconnect_records_what_led_up_to_it() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(fake->deliver(0x81, {1}));
    t->read(buffer, 100ms);
    std::this_thread::sleep_for(50ms);
    fake->begin_disconnect();
    fake->flush(-EILSEQ);  // A dying device often shows a transaction error before the flush.
    fake->remove_nodes();
    read_until(*t, buffer, IoStatus::Disconnected, 500ms);
    t->next_event();  // Connected
    auto e = t->next_event();
    CHECK(e && e->kind == TransportEvent::Kind::Disconnected);
    if (!e) return;
    CHECK(e->last_urb_status == -EILSEQ);
    CHECK(e->ms_since_last_packet >= 50 && e->ms_since_last_packet < 2000);
    CHECK(e->reason.find(std::strerror(EILSEQ)) != std::string::npos);  // The first cause, not the ENODEV that ends the reap.
    CHECK(e->reason.find("0x81") != std::string::npos);
    CHECK(t->stats().urb_errors >= 1);
}

void test_read_path_allocates_nothing() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->stop_recording();
    const std::vector<std::uint8_t> packet(64, 1);
    long allocations = 0;
    int packets = 0;
    for (int i = 0; i < 5000; ++i) {
        fake->deliver(0x81, packet);
        long before = heap_allocations.load();
        auto r = t->read(buffer, 100ms);
        allocations += heap_allocations.load() - before;
        packets += r.status == IoStatus::Ok;
    }
    CHECK(packets == 5000);
    CHECK(allocations == 0);
}

// The configuration nobody has to think about is the Ultra's own data channel.
void test_default_config_is_the_ultras_bulk_channel() {
    FakeTree tree;
    TransportConfig config;
    config.sysfs_root = tree.sysfs();
    config.dev_root = tree.dev();
    CHECK(config.backend == Backend::Usbfs);
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    CHECK(fake->inflight() == 8);
    CHECK(config.arrival_settle == 2000ms);  // protocol.md's starting value, for a soak to tune.
    for (const auto& urb : fake->seen()) {
        CHECK(urb.endpoint == luma::kLumaUltraDataIn);
        CHECK(urb.type == USBDEVFS_URB_TYPE_BULK);
        CHECK(urb.length == 512);
        CHECK(urb.flags == 0);
    }
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(fake->deliver(luma::kLumaUltraDataIn, std::vector<std::uint8_t>(512, 3)));
    auto r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && r.bytes == 512 && r.endpoint == luma::kLumaUltraDataIn);

    CHECK(t->write(std::vector<std::uint8_t>(512, 0xff)) == IoStatus::Ok);
    auto urbs = fake->seen();
    CHECK(!urbs.empty());
    if (urbs.empty()) return;
    auto last = urbs.back();
    CHECK(last.endpoint == luma::kLumaUltraDataOut && last.type == USBDEVFS_URB_TYPE_BULK && last.length == 512);
    CHECK(last.flags == 0);  // No zero-length packet after a full-size frame: frames are one packet each.
}

// An interrupt endpoint remains possible for the stream: which one it is awaits a capture.
void test_interrupt_endpoints_get_interrupt_urbs() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    for (const auto& urb : fake->seen())
        CHECK(urb.type == (urb.endpoint == 0x81 ? USBDEVFS_URB_TYPE_INTERRUPT : USBDEVFS_URB_TYPE_BULK));
}

void test_usbfs_claims_only_the_needed_interface_every_time() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.interface_number = 0;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->yank();
    read_until(*t, buffer, IoStatus::Disconnected, 500ms);
    fake->arrive(102);
    read_until(*t, buffer, IoStatus::Connected, 500ms);
    CHECK(fake->claimed_list() == (std::vector<int>{0, 0}));
}

// The queue is what keeps the glasses from backing up: every read must leave it full.
void test_usbfs_reads_stay_queued_through_a_burst() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {luma::kLumaUltraDataIn};
    config.urbs_per_endpoint = 8;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    for (int round = 0; round < 50; ++round) {
        for (int i = 0; i < 8; ++i) CHECK(fake->deliver(luma::kLumaUltraDataIn, std::vector<std::uint8_t>(512, static_cast<std::uint8_t>(i))));
        CHECK(fake->inflight() == 0);  // The whole queue has been used: the worst moment.
        for (int i = 0; i < 8; ++i) {
            auto r = t->read(buffer, 100ms);
            CHECK(r.status == IoStatus::Ok && buffer[0] == i);
            CHECK(fake->inflight() == 8);  // Back to full from the first read on.
        }
    }
    CHECK(fake->missed() == 0);
}

// Only teardown cancels our reads, and it reaps its own, so this should not happen on a real kernel.
// If it ever does, the queue is short and carrying on would leave a link that reads nothing.
void test_usbfs_a_read_cancelled_by_the_kernel_is_a_lost_link() {
    for (int status : {-ENOENT, -ECONNRESET}) {
        FakeTree tree;
        Transport* raw;
        auto fake = connected_usb(tree, raw, usb_config(tree));
        std::unique_ptr<Transport> t(raw);
        t->open();
        Buffer buffer;
        t->read(buffer, 100ms);
        CHECK(fake->deliver(0x81, {}, status));
        CHECK(read_until(*t, buffer, IoStatus::Disconnected, 300ms).status == IoStatus::Disconnected);
        CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
        CHECK(fake->inflight() == 4);  // A full queue again, not a link that looks open and reads nothing.
    }
}

// Reconnecting straight after a drop has been followed by another drop (usb-drop-fix: 12 of 20,
// against 0 of 160 fresh starts). A settle holds the open back, and the wait restarts if the device
// vanishes again during it.
void test_arrival_settle_holds_the_reopen_back() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.arrival_settle = 120ms;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    auto start = Clock::now();
    CHECK(t->open());  // A first open has nothing to settle from.
    CHECK(ms_since(start) < 100);
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(fake->deliver(0x81, {1}));  // A good session: only the plain settle applies.
    CHECK(t->read(buffer, 100ms).status == IoStatus::Ok);
    fake->yank();
    read_until(*t, buffer, IoStatus::Disconnected, 500ms);
    int opens = fake->opens();

    fake->arrive(102);
    t->read(buffer, 70ms);
    CHECK(fake->opens() == opens);
    fake->yank();  // Gone again, part way through.
    t->read(buffer, 30ms);
    fake->arrive(103);
    start = Clock::now();
    auto r = read_until(*t, buffer, IoStatus::Connected, 1000ms);
    CHECK(r.status == IoStatus::Connected);
    CHECK(ms_since(start) >= 100);  // The full settle again, not what was left of the first one.
    CHECK(fake->opens() == opens + 1);  // Then opened once.
}

// While something else holds the glasses, open and claim must not run at the rescan rate.
void test_failed_opens_back_off_but_finding_the_device_stays_fast() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.open_retry_max = 1000ms;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    fake->set_busy(true);
    t->open();
    Buffer buffer;
    auto start = Clock::now();
    while (ms_since(start) < 300) t->read(buffer, 20ms);
    CHECK(fake->opens() <= 7);  // 10 ms apart it would be 30.
    CHECK(fake->opens() >= 3);
    fake->set_busy(false);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1500ms).status == IoStatus::Connected);

    // A device that is simply absent is still looked for at full rate, however long it has been.
    fake->yank();
    read_until(*t, buffer, IoStatus::Disconnected, 500ms);
    start = Clock::now();
    while (ms_since(start) < 100) t->read(buffer, 20ms);
    fake->arrive(150);
    start = Clock::now();
    CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
    CHECK(ms_since(start) < 60);
}

// Incident 20261104T051931Z: after a cold boot the node had no user ACL, xrDriver's SDK failed to open
// it, went on to use the handle anyway and crashed. Here a failed open must end all contact with
// the device, say why in a way a program can read, and recover once access appears.
void test_no_permission_never_touches_the_device_and_says_why() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    fake->fail_opens(1000000, EACCES);
    CHECK(!t->open());
    Buffer buffer;
    CHECK(t->read(buffer, 80ms).status == IoStatus::Timeout);
    CHECK(!t->connected());
    CHECK(t->stats().last_open_errno == EACCES);
    CHECK(t->last_error().find("Permission denied") != std::string::npos);
    CHECK(t->last_error().find("replug") != std::string::npos);  // Tells a person what to do.
    CHECK(t->write(std::vector<std::uint8_t>(512, 1)) == IoStatus::Disconnected);
    CHECK(fake->requests().empty());  // Not one ioctl: no claim, no URB, nothing on a handle that never opened.
    CHECK(t->stats().connects == 0);

    fake->fail_opens(0, 0);  // Replugged, or the rule installed.
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1500ms).status == IoStatus::Connected);
    CHECK(t->stats().last_open_errno == 0);
    CHECK(t->last_error().empty());
}

void test_busy_interface_reports_its_errno() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    fake->set_busy(true);
    CHECK(!t->open());
    CHECK(t->stats().last_open_errno == EBUSY);
}

// A slow sysfs scan could leave next_scan in the past. A negative poll timeout waits for ever, and
// read() would then stop rescanning, so the glasses would never come back.
void test_a_slow_scan_never_makes_poll_wait_for_ever() {
    class Watch : public luma::Sys {
    public:
        int poll(pollfd* fds, nfds_t count, int timeout_ms) override {
            lowest = std::min(lowest, timeout_ms);
            return luma::Sys::poll(fds, count, std::max(timeout_ms, 0));
        }
        int lowest = 0;
    };
    FakeTree tree;
    for (int i = 0; i < 4000; ++i) {  // Enough entries that one scan outlasts the rescan interval.
        fs::path d = tree.sysfs() / "bus/usb/devices" / ("9-" + std::to_string(i));
        write_file(d / "idVendor", "1234");
        write_file(d / "idProduct", "5678");
    }
    auto config = usb_config(tree);
    config.rescan_interval = 1ms;
    auto watch = std::make_shared<Watch>();
    Transport t(config, watch);
    t.open();
    Buffer buffer;
    auto start = Clock::now();
    while (ms_since(start) < 120) CHECK(t.read(buffer, 30ms).status == IoStatus::Timeout);
    CHECK(watch->lowest >= 0);
}

void test_huge_timeouts_mean_a_long_wait() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);
    std::thread waker([&] {
        std::this_thread::sleep_for(30ms);
        t.interrupt();
    });
    CHECK(t.read(buffer, std::chrono::milliseconds::max()).status == IoStatus::Interrupted);
    waker.join();
}

// The moment the glasses are backed up is the moment the ring holds packets. Handing those over must
// not leave the kernel's queue empty.
void test_usbfs_reads_refill_while_the_ring_drains() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {luma::kLumaUltraDataIn};
    config.urbs_per_endpoint = 8;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    int next = 0, expected = 0;
    auto send = [&](int n) {
        for (int i = 0; i < n; ++i) CHECK(fake->deliver(luma::kLumaUltraDataIn, std::vector<std::uint8_t>(512, static_cast<std::uint8_t>(next++))));
    };
    send(8);
    auto r = t->read(buffer, 100ms);  // Takes all 8 from the kernel, hands over one.
    CHECK(r.status == IoStatus::Ok && buffer[0] == expected++);
    send(8);  // The glasses keep going while the caller works through the other 7.
    CHECK(fake->inflight() == 0);
    r = t->read(buffer, 100ms);
    CHECK(r.status == IoStatus::Ok && buffer[0] == expected++);
    CHECK(fake->inflight() == 8);  // Refilled by a read that came from the ring.
    while (expected < 16) {
        r = t->read(buffer, 100ms);
        CHECK(r.status == IoStatus::Ok && buffer[0] == expected);
        ++expected;
    }
    CHECK(t->stats().dropped_packets == 0);
}

// One fault fails the whole queue at once. It is one failure: close, once, and wait.
void test_usbfs_a_failed_read_closes_everything_once() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {luma::kLumaUltraDataIn};
    config.urbs_per_endpoint = 8;
    config.arrival_settle = 80ms;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    CHECK(fake->deliver(luma::kLumaUltraDataIn, {1}));
    CHECK(t->read(buffer, 100ms).status == IoStatus::Ok);

    fake->fail_all(luma::kLumaUltraDataIn, -EPROTO);
    CHECK(t->read(buffer, 100ms).status == IoStatus::Disconnected);
    CHECK(!t->connected());
    CHECK(fake->inflight() == 0);  // Nothing is left polling a sick link.
    CHECK(!fake->descriptor_open());
    CHECK(fake->claims() == 1);
    CHECK(t->read(buffer, 30ms).status == IoStatus::Timeout);  // Reported once, and held off.
    CHECK(fake->claims() == 1);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
    CHECK(fake->inflight() == 8);
    CHECK(t->stats().disconnects == 1);
    t->next_event();
    auto e = t->next_event();
    CHECK(e && e->reason.find("0x87") != std::string::npos);
}

// Nothing here may clear a halt, so a reopen cannot fix one. Without a limit that is a reconnect storm:
// 79 connects in 2 s, measured against an earlier version. Only a new enumeration heals it.
void test_a_stall_cannot_become_a_reopen_storm() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {luma::kLumaUltraDataIn};
    config.arrival_settle = 20ms;
    config.open_retry_max = 30ms;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->stall(luma::kLumaUltraDataIn);
    auto start = Clock::now();
    int connects = 0;
    while (ms_since(start) < 500) connects += t->read(buffer, 20ms).status == IoStatus::Connected;
    CHECK(connects >= 2);  // It does keep trying, in case something else cleared it.
    CHECK(connects <= 5);  // Holds of 40, 80, 160, 320 ms, not one every 20 ms.
    CHECK(t->last_error().find("halted") != std::string::npos);

    fake->yank();  // The glasses leave and come back: that clears the halt, and the penalty with it.
    read_until(*t, buffer, IoStatus::Disconnected, 1000ms);
    t->read(buffer, 30ms);
    fake->arrive(150);
    start = Clock::now();
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
    CHECK(ms_since(start) < 200);
    CHECK(fake->deliver(luma::kLumaUltraDataIn, {1}));
    CHECK(t->read(buffer, 100ms).status == IoStatus::Ok);
}

// Another program's reset raises no poll bit and keeps the device number, and takes our claim away.
void test_a_foreign_reset_is_noticed_and_the_interface_reclaimed() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->foreign_reset();
    CHECK(read_until(*t, buffer, IoStatus::Disconnected, 300ms).status == IoStatus::Disconnected);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
    CHECK(fake->claims() == 2);
    CHECK(fake->inflight() == 4);
    CHECK(fake->auto_claims() == 0);  // Never submitted on a claim the kernel had dropped.
}

// The kernel's real order: hung up first, URBs still pending, node still there for a while.
void test_a_disconnect_in_real_order() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    std::thread stages([&] {
        std::this_thread::sleep_for(20ms);
        fake->begin_disconnect();
    });
    auto start = Clock::now();
    auto r = t->read(buffer, 5000ms);
    stages.join();
    CHECK(r.status == IoStatus::Disconnected);  // On the hang-up alone, nothing has completed yet.
    CHECK(ms_since(start) < 500);
    CHECK(t->read(buffer, 40ms).status == IoStatus::Timeout);  // The dying node is still there and will not open.
    fake->flush(-ESHUTDOWN);
    fake->remove_nodes();
    fake->arrive(102);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
    CHECK(t->stats().disconnects == 1);  // One drop, reported once.
    CHECK(fake->inflight() == 4);
}

void test_default_settle_holds_a_reconnect_after_a_drop() {
    FakeTree tree;
    TransportConfig config;
    config.sysfs_root = tree.sysfs();
    config.dev_root = tree.dev();
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(t->open());
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->yank();
    read_until(*t, buffer, IoStatus::Disconnected, 500ms);
    int opens = fake->opens();
    t->read(buffer, 60ms);
    fake->arrive(102);
    t->read(buffer, 200ms);
    CHECK(fake->opens() == opens);  // Back, and left alone.
}

void test_a_failed_write_closes_the_link() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->out_status(-EPIPE);
    CHECK(t->write(std::vector<std::uint8_t>{1}) == IoStatus::Disconnected);
    CHECK(!t->connected());
    CHECK(read_until(*t, buffer, IoStatus::Disconnected, 300ms).status == IoStatus::Disconnected);
    t->next_event();
    auto e = t->next_event();
    CHECK(e && e->last_urb_status == -EPIPE);
    CHECK(e && e->reason.find("0x06") != std::string::npos);
}

// A write that finished just as the timeout fired went out. Saying otherwise invites sending it twice.
void test_a_write_finishing_as_it_times_out_reports_the_truth() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    fake->hold_out_urbs(true);
    fake->finish_held_urb_on_discard();
    CHECK(t->write(std::vector<std::uint8_t>(4, 1), 10ms) == IoStatus::Ok);
    CHECK(t->connected());
}

void test_a_write_with_no_timeout_still_gets_one_look() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    CHECK(t->write(std::vector<std::uint8_t>{1}, 0ms) == IoStatus::Ok);
}

// A packet larger than the URB is babble on a real host, not a shorter read, so refuse it up front.
void test_a_packet_size_below_the_endpoints_is_refused() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.packet_size = 64;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    CHECK(!t->open());
    CHECK(t->last_error().find("packet_size") != std::string::npos);
    CHECK(fake->claims() == 0);
    CHECK(!fake->descriptor_open());
}

void test_open_while_down_leaves_reconnecting_to_read() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    fake->yank();
    CHECK(t->write(std::vector<std::uint8_t>{1}) == IoStatus::Disconnected);
    fake->arrive(102);
    CHECK(!t->open());  // read() has not heard of the drop yet.
    CHECK(t->read(buffer, 100ms).status == IoStatus::Disconnected);
    CHECK(read_until(*t, buffer, IoStatus::Connected, 500ms).status == IoStatus::Connected);
}

void test_at_least_two_reads_stay_queued() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.in_endpoints = {0x81};
    config.urbs_per_endpoint = 1;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    CHECK(fake->inflight() == 2);
}

void test_usbfs_close_cancels_and_releases() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    t->close();
    CHECK(fake->requests().count(USBDEVFS_DISCARDURB) == 4);
    CHECK(fake->inflight() == 0);
    CHECK(fake->releases() == 1);
    CHECK(!fake->descriptor_open());
}

// ---------------------------------------------------------------- never reset

void test_only_the_harmless_ioctls_are_allowed() {
    CHECK(luma::is_allowed_ioctl(USBDEVFS_CLAIMINTERFACE));
    CHECK(luma::is_allowed_ioctl(USBDEVFS_RELEASEINTERFACE));
    CHECK(luma::is_allowed_ioctl(USBDEVFS_SUBMITURB));
    CHECK(luma::is_allowed_ioctl(USBDEVFS_REAPURBNDELAY));
    CHECK(luma::is_allowed_ioctl(USBDEVFS_DISCARDURB));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_RESET));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_RESETEP));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_SETCONFIGURATION));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_SETINTERFACE));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_CLEAR_HALT));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_DISCONNECT));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_DISCONNECT_CLAIM));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_IOCTL));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_CONTROL));
    CHECK(!luma::is_allowed_ioctl(USBDEVFS_BULK));
}

void test_a_whole_session_only_used_allowed_ioctls() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    {
        std::unique_ptr<Transport> t(raw);
        t->open();
        Buffer buffer;
        t->read(buffer, 50ms);
        fake->deliver(0x81, {1});
        t->read(buffer, 50ms);
        t->write(std::vector<std::uint8_t>{1});
        fake->stall(0x81);  // A halted endpoint: close, never clear the halt.
        read_until(*t, buffer, IoStatus::Disconnected, 500ms);
        read_until(*t, buffer, IoStatus::Connected, 500ms);
        fake->yank();
        read_until(*t, buffer, IoStatus::Disconnected, 500ms);
        fake->arrive(103);
        read_until(*t, buffer, IoStatus::Connected, 500ms);
    }
    auto used = fake->requests();
    CHECK(used.count(USBDEVFS_SUBMITURB) > 0);
    for (unsigned long request : used) CHECK(luma::is_allowed_ioctl(request));
}

// The default Sys refuses a forbidden ioctl before it reaches the kernel, even if handed a real file.
void test_default_sys_refuses_forbidden_ioctl_itself() {
    luma::Sys sys;
    int fd = ::open("/dev/null", O_RDWR | O_CLOEXEC);
    errno = 0;
    CHECK(sys.ioctl(fd, USBDEVFS_RESET, nullptr) == -1);
    CHECK(errno == EPERM);
    ::close(fd);
}

// A signal handler's plain flag must get a chance to be seen: poll's EINTR comes back as Interrupted.
void test_signal_interrupts_a_blocked_read() {
    FakeTree tree;
    Pty pty;
    tree.add_hidraw("hidraw10", "5-2.3", "0003:000035CA:00001102", 3, pty.path());
    Transport t(hid_config(tree));
    t.open();
    Buffer buffer;
    t.read(buffer, 100ms);
    struct sigaction action{};
    action.sa_handler = [](int) {};
    sigemptyset(&action.sa_mask);
    struct sigaction old{};
    sigaction(SIGUSR1, &action, &old);
    pthread_t self = pthread_self();
    std::thread signaller([&] {
        std::this_thread::sleep_for(30ms);
        pthread_kill(self, SIGUSR1);
    });
    auto start = Clock::now();
    CHECK(t.read(buffer, 5000ms).status == IoStatus::Interrupted);
    CHECK(ms_since(start) < 1000);
    signaller.join();
    sigaction(SIGUSR1, &old, nullptr);
}

}  // namespace

int main() {
    const std::pair<const char*, void (*)()> tests[] = {
#define T(name) {#name, name}
        T(test_finds_hid_interface_among_decoys),
        T(test_finds_usb_device_and_endpoints),
        T(test_finds_nothing_for_absent_device),
        T(test_finds_nothing_when_interface_absent),
        T(test_auto_prefers_hidraw_then_falls_back_to_usbfs),
        T(test_finds_real_shaped_paths_only_through_given_roots),
        T(test_hidraw_read_and_write),
        T(test_read_times_out_on_time),
        T(test_hidraw_disconnect_mid_read_then_reconnect),
        T(test_no_device_at_start_connects_when_it_arrives),
        T(test_interrupt_wakes_a_blocked_read),
        T(test_interrupt_wakes_a_read_while_disconnected),
        T(test_write_while_down_says_so_and_queues_nothing),
        T(test_close_stops_reads_and_open_reconnects),
        T(test_events_tell_the_story_once),
        T(test_usbfs_claims_then_keeps_urbs_in_flight_on_every_in_endpoint),
        T(test_usbfs_stream_keeps_order_and_names_the_endpoint),
        T(test_usbfs_ignores_empty_packets_but_keeps_reading),
        T(test_usbfs_rejects_endpoints_the_device_does_not_have),
        T(test_usbfs_refuses_the_flash_channel_on_the_ultra),
        T(test_mcu_channel_passes_only_allowlisted_frames),
        T(test_live_path_with_captured_packets),
        T(test_write_to_reaches_only_configured_endpoints),
        T(test_drain_endpoint_reads_and_throws_away),
        T(test_usbfs_needs_an_in_endpoint),
        T(test_usbfs_waits_for_a_busy_interface_and_never_takes_it),
        T(test_usbfs_retries_while_udev_applies_permissions),
        T(test_usbfs_disconnect_mid_read_then_reconnect),
        T(test_usbfs_survives_a_burst_of_drops_without_leaking),
        T(test_usbfs_write_goes_out_once_with_the_right_type),
        T(test_usbfs_write_timeout_cancels_and_does_not_retry_or_drop_the_link),
        T(test_usbfs_write_keeps_packets_that_arrive_meanwhile),
        T(test_usbfs_write_refuses_more_than_one_packet_and_missing_out_endpoint),
        T(test_usbfs_write_during_a_drop_reports_disconnected),
        T(test_usbfs_full_queue_drops_oldest_and_counts_it),
        T(test_usbfs_flags_a_truncated_packet),
        T(test_usbfs_empty_write_and_empty_read_buffer),
        T(test_usbfs_full_size_packet_arrives_at_once),
        T(test_a_disconnect_records_what_led_up_to_it),
        T(test_read_path_allocates_nothing),
        T(test_default_config_is_the_ultras_bulk_channel),
        T(test_interrupt_endpoints_get_interrupt_urbs),
        T(test_usbfs_claims_only_the_needed_interface_every_time),
        T(test_usbfs_reads_stay_queued_through_a_burst),
        T(test_usbfs_a_read_cancelled_by_the_kernel_is_a_lost_link),
        T(test_arrival_settle_holds_the_reopen_back),
        T(test_failed_opens_back_off_but_finding_the_device_stays_fast),
        T(test_no_permission_never_touches_the_device_and_says_why),
        T(test_busy_interface_reports_its_errno),
        T(test_a_slow_scan_never_makes_poll_wait_for_ever),
        T(test_huge_timeouts_mean_a_long_wait),
        T(test_usbfs_reads_refill_while_the_ring_drains),
        T(test_usbfs_a_failed_read_closes_everything_once),
        T(test_a_stall_cannot_become_a_reopen_storm),
        T(test_a_foreign_reset_is_noticed_and_the_interface_reclaimed),
        T(test_a_disconnect_in_real_order),
        T(test_default_settle_holds_a_reconnect_after_a_drop),
        T(test_a_failed_write_closes_the_link),
        T(test_a_write_finishing_as_it_times_out_reports_the_truth),
        T(test_a_write_with_no_timeout_still_gets_one_look),
        T(test_a_packet_size_below_the_endpoints_is_refused),
        T(test_open_while_down_leaves_reconnecting_to_read),
        T(test_at_least_two_reads_stay_queued),
        T(test_usbfs_close_cancels_and_releases),
        T(test_only_the_harmless_ioctls_are_allowed),
        T(test_a_whole_session_only_used_allowed_ioctls),
        T(test_default_sys_refuses_forbidden_ioctl_itself),
        T(test_signal_interrupts_a_blocked_read),
#undef T
    };
    for (const auto& [name, test] : tests) {
        int before = failures;
        test();
        std::printf("%s %s\n", failures == before ? "ok  " : "FAIL", name);
        std::fflush(stdout);
    }
    std::printf("%d failed checks\n", failures);
    return failures == 0 ? 0 : 1;
}
