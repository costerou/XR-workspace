// luma::Transport against fakes. No real device is ever opened.
//
// Hidraw runs through the real read/poll/write path: a fake sysfs tree whose /dev node is a symlink
// to a pty. Closing the pty master gives the slave POLLHUP and EIO, which is what a removed hidraw
// node gives (drivers/hid/hidraw.c).
//
// Usbfs has no such stand-in without root (dummy_hcd), so FakeUsbfs imitates the kernel's URB
// behaviour from drivers/usb/core/devio.c: completion raises POLLOUT, reap returns EAGAIN then
// ENODEV, disconnect raises POLLHUP|POLLERR, and it records every ioctl.
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
        for (const auto& [address, type] : endpoints) write_file(intf / ("ep_" + address) / "type", type);
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
    c.sysfs_root = tree.sysfs();
    c.dev_root = tree.dev();
    return c;
}

TransportConfig usb_config(const FakeTree& tree) {
    TransportConfig c;
    c.in_endpoints = {0x81, 0x82};
    c.out_endpoint = luma::kLumaUltraDataOut;
    c.rescan_interval = 10ms;
    c.sysfs_root = tree.sysfs();
    c.dev_root = tree.dev();
    return c;
}

const std::vector<std::pair<std::string, std::string>> kEndpoints = {
    {"81", "Interrupt"}, {"82", "Bulk"}, {"04", "Bulk"}, {"06", "Bulk"}};

// A stand-in for the usbfs side of the kernel (drivers/usb/core/devio.c).
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
        tree_.add_usb("5-2.1", "35ca", "1104", devnum, kEndpoints);
    }
    // USB disconnect: in-flight URBs finish with -ESHUTDOWN, poll says hang-up, sysfs and the node go.
    void yank() {
        std::lock_guard lock(m_);
        gone_ = true;
        for (auto* urb : inflight_) finish(urb, -ESHUTDOWN, 0);
        inflight_.clear();
        tree_.remove_usb("5-2.1", devnum_);
    }
    bool deliver(std::uint8_t endpoint, const std::vector<std::uint8_t>& bytes, int status = 0) {
        std::lock_guard lock(m_);
        for (auto it = inflight_.begin(); it != inflight_.end(); ++it) {
            usbdevfs_urb* urb = *it;
            if (urb->endpoint != endpoint) continue;
            int n = std::min<int>(urb->buffer_length, static_cast<int>(bytes.size()));
            if (n > 0) std::memcpy(urb->buffer, bytes.data(), static_cast<std::size_t>(n));
            inflight_.erase(it);
            finish(urb, status, status == 0 ? n : 0);
            return true;
        }
        ++missed_;
        return false;
    }
    void set_busy(bool busy) { std::lock_guard lock(m_); busy_ = busy; }
    void fail_opens(int count, int error) { std::lock_guard lock(m_); open_failures_ = count; open_errno_ = error; }
    void hold_out_urbs(bool hold) { std::lock_guard lock(m_); hold_out_ = hold; }
    void out_status(int status) { std::lock_guard lock(m_); out_status_ = status; }

    // Inspection --------------------------------------------------------
    std::vector<std::vector<std::uint8_t>> sent() { std::lock_guard lock(m_); return sent_; }
    std::vector<int> submitted_endpoints() { std::lock_guard lock(m_); return submitted_; }
    std::multiset<unsigned long> requests() { std::lock_guard lock(m_); return requests_; }
    std::size_t inflight() { std::lock_guard lock(m_); return inflight_.size(); }
    int claims() { std::lock_guard lock(m_); return claims_; }
    int releases() { std::lock_guard lock(m_); return releases_; }
    int claimed_interface() { std::lock_guard lock(m_); return claimed_; }
    int open_flags() { std::lock_guard lock(m_); return open_flags_; }
    int missed() { std::lock_guard lock(m_); return missed_; }
    bool descriptor_open() { std::lock_guard lock(m_); return event_ >= 0; }

    // Sys ---------------------------------------------------------------
    int open(const char* path, int flags) override {
        std::lock_guard lock(m_);
        if (!fs::exists(path)) return fail(ENOENT);
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
        return 0;
    }
    int poll(pollfd* fds, nfds_t count, int timeout_ms) override {
        std::vector<pollfd> real(fds, fds + count);
        int event;
        {
            std::lock_guard lock(m_);
            event = event_;
        }
        for (auto& p : real)
            if (p.fd == event && (p.events & POLLOUT)) p.events = POLLIN;  // eventfd stands for "a URB completed".
        int result = ::poll(real.data(), count, timeout_ms);
        int saved = errno;
        std::lock_guard lock(m_);
        for (nfds_t i = 0; i < count; ++i) {
            fds[i].revents = real[i].revents;
            if (fds[i].fd != event) continue;
            if (real[i].revents & POLLIN) fds[i].revents = static_cast<short>((fds[i].revents & ~POLLIN) | POLLOUT);
            if (gone_) fds[i].revents |= POLLHUP | POLLERR;
        }
        errno = saved;
        return result;
    }
    int ioctl(int, unsigned long request, void* argument) override {
        std::lock_guard lock(m_);
        requests_.insert(request);
        if (request == USBDEVFS_CLAIMINTERFACE) {
            if (gone_) return fail(ENODEV);
            if (busy_) return fail(EBUSY);
            ++claims_;
            claimed_ = static_cast<int>(*static_cast<unsigned int*>(argument));
            return 0;
        }
        if (request == USBDEVFS_RELEASEINTERFACE) {
            ++releases_;
            return gone_ ? fail(ENODEV) : 0;
        }
        if (request == USBDEVFS_SUBMITURB) {
            if (gone_) return fail(ENODEV);
            auto* urb = static_cast<usbdevfs_urb*>(argument);
            submitted_.push_back(urb->endpoint);
            if (urb->endpoint & 0x80) {
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
            auto it = std::find(inflight_.begin(), inflight_.end(), static_cast<usbdevfs_urb*>(argument));
            if (it == inflight_.end()) return fail(EINVAL);
            usbdevfs_urb* urb = *it;
            inflight_.erase(it);
            finish(urb, -ENOENT, 0);
            return 0;
        }
        if (request == USBDEVFS_REAPURBNDELAY) {
            if (done_.empty()) {
                drain();
                return fail(gone_ ? ENODEV : EAGAIN);
            }
            *static_cast<void**>(argument) = done_.front();
            done_.pop_front();
            if (done_.empty() && !gone_) drain();
            return 0;
        }
        return fail(ENOTTY);
    }

private:
    int fail(int error) {
        errno = error;
        return -1;
    }
    void finish(usbdevfs_urb* urb, int status, int actual) {
        urb->status = status;
        urb->actual_length = actual;
        done_.push_back(urb);
        if (event_ >= 0) {
            std::uint64_t one = 1;
            if (::write(event_, &one, sizeof one) < 0) std::abort();
        }
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
    int out_status_ = 0;
    int open_failures_ = 0;
    int open_errno_ = 0;
    int open_flags_ = 0;
    int claims_ = 0;
    int releases_ = 0;
    int claimed_ = -1;
    int missed_ = 0;
    std::deque<usbdevfs_urb*> inflight_;
    std::deque<usbdevfs_urb*> done_;
    std::vector<std::vector<std::uint8_t>> sent_;
    std::vector<int> submitted_;
    std::multiset<unsigned long> requests_;
};

using Buffer = std::array<std::uint8_t, 2048>;

// Reads until the status appears or the budget runs out; skips Timeouts. Returns the matching result.
luma::ReadResult read_until(Transport& t, Buffer& buffer, IoStatus wanted, std::chrono::milliseconds budget = 2000ms) {
    auto end = Clock::now() + budget;
    luma::ReadResult r;
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
    CHECK(found->endpoints.size() == 4);
    auto type = [&](std::uint8_t address) {
        for (const auto& e : found->endpoints)
            if (e.address == address) return e.bulk ? 1 : 0;
        return -1;
    };
    CHECK(type(0x81) == 0);
    CHECK(type(0x82) == 1);
    CHECK(type(0x04) == 1);
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
    std::thread device([&] {
        for (int i = 0; i < 2000; ++i) {
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

void test_usbfs_write_failure_is_an_error_not_a_disconnect() {
    FakeTree tree;
    Transport* raw;
    auto fake = connected_usb(tree, raw, usb_config(tree));
    std::unique_ptr<Transport> t(raw);
    t->open();
    fake->out_status(-EPIPE);
    CHECK(t->write(std::vector<std::uint8_t>{1}) == IoStatus::Error);
    CHECK(t->connected());
    CHECK(!t->last_error().empty());
}

void test_usbfs_write_refuses_oversize_and_missing_out_endpoint() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.packet_size = 16;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    CHECK(t->write(std::vector<std::uint8_t>(17, 1)) == IoStatus::Error);
    CHECK(fake->sent().empty());

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

void test_usbfs_a_good_packet_forgives_earlier_errors() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.max_urb_errors = 3;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    for (int round = 0; round < 3; ++round) {
        fake->deliver(0x81, {}, -EPROTO);
        fake->deliver(0x81, {}, -EPROTO);
        CHECK(t->read(buffer, 5ms).status == IoStatus::Timeout);
        fake->deliver(0x81, {5});
        CHECK(t->read(buffer, 200ms).status == IoStatus::Ok);
    }
    CHECK(t->connected());
    CHECK(t->stats().urb_errors == 6);
    CHECK(t->stats().disconnects == 0);
}

void test_usbfs_repeated_errors_reopen_the_link_without_a_reset() {
    FakeTree tree;
    auto config = usb_config(tree);
    config.max_urb_errors = 3;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    std::unique_ptr<Transport> t(raw);
    t->open();
    Buffer buffer;
    t->read(buffer, 100ms);
    for (int i = 0; i < 3; ++i) {
        fake->deliver(0x81, {}, -EPROTO);
        t->read(buffer, 5ms);
    }
    CHECK(read_until(*t, buffer, IoStatus::Connected, 1000ms).status == IoStatus::Connected);
    CHECK(fake->claims() == 2);
    CHECK(fake->releases() == 1);
    CHECK(t->stats().disconnects == 1);
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
    auto config = usb_config(tree);
    config.max_urb_errors = 2;
    Transport* raw;
    auto fake = connected_usb(tree, raw, config);
    {
        std::unique_ptr<Transport> t(raw);
        t->open();
        Buffer buffer;
        t->read(buffer, 50ms);
        fake->deliver(0x81, {1});
        t->read(buffer, 50ms);
        t->write(std::vector<std::uint8_t>{1});
        fake->deliver(0x81, {}, -EPIPE);
        fake->deliver(0x81, {}, -EPIPE);  // A stalled endpoint: reopen, never clear the halt.
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
        T(test_usbfs_needs_an_in_endpoint),
        T(test_usbfs_waits_for_a_busy_interface_and_never_takes_it),
        T(test_usbfs_retries_while_udev_applies_permissions),
        T(test_usbfs_disconnect_mid_read_then_reconnect),
        T(test_usbfs_survives_a_burst_of_drops_without_leaking),
        T(test_usbfs_write_goes_out_once_with_the_right_type),
        T(test_usbfs_write_timeout_cancels_and_does_not_retry_or_drop_the_link),
        T(test_usbfs_write_keeps_packets_that_arrive_meanwhile),
        T(test_usbfs_write_failure_is_an_error_not_a_disconnect),
        T(test_usbfs_write_refuses_oversize_and_missing_out_endpoint),
        T(test_usbfs_write_during_a_drop_reports_disconnected),
        T(test_usbfs_a_good_packet_forgives_earlier_errors),
        T(test_usbfs_repeated_errors_reopen_the_link_without_a_reset),
        T(test_usbfs_full_queue_drops_oldest_and_counts_it),
        T(test_usbfs_flags_a_truncated_packet),
        T(test_usbfs_empty_write_and_empty_read_buffer),
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
