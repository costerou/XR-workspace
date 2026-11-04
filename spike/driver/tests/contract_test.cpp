// Driver swarm contract test: do the luma headers match the DESIGN CONTRACT every swarm thread coded to?
//
// Three layers, none of which needs the library linked, a device or a running driver:
//   1. Checkers proven on local fakes (always compiled and run, so a skipped test still tests itself). Each
//      checker must accept a good fake and reject fakes broken the way a real branch could break it.
//   2. The same checkers applied to the real headers, as static_asserts. A mismatch is a compile error.
//   3. A scan of the driver's sources for what the contract and the safety rules forbid (device resets, network,
//      the VITURE SDK, libraries beyond libc/libudev, get_display_mode), and for what pose.cpp needs from the feed.
//
// It compares against the contract only where the contract is exact. Names it leaves open (how a Transport is
// opened, Publisher's write method, the Command builders, hotplug events) are listed in docs/acceptance.md under
// "Seams the contract does not pin", not guessed at here.
//
// When it runs: the real checks need XR_ENABLE_LUMA_DRIVER defined and all five headers on the include path. Until
// then it runs layer 1, says what is missing and exits 77, which CTest reports as Skipped (SKIP_RETURN_CODE 77).
// Define LUMA_CONTRACT_REQUIRED once every branch is merged: a missing header is then a failure, so a renamed header
// cannot turn this test into a permanent skip. CMake wiring: docs/acceptance.md, "Wiring this test".
//
// Usage: contract_test [spike/driver source directory]   (default: found from this file's path)
// Failures return 1. It never aborts, so a failing run does not dump core and notify on the desktop.

// The real headers come first so each is compiled before any standard header this file includes for itself.
#if __has_include(<luma/types.hpp>)
#include <luma/types.hpp>
#define LUMA_HAS_TYPES 1
#endif
#if __has_include(<luma/transport.hpp>)
#include <luma/transport.hpp>
#define LUMA_HAS_TRANSPORT 1
#endif
#if __has_include(<luma/protocol.hpp>)
#include <luma/protocol.hpp>
#define LUMA_HAS_PROTOCOL 1
#endif
#if __has_include(<luma/fusion.hpp>)
#include <luma/fusion.hpp>
#define LUMA_HAS_FUSION 1
#endif
#if __has_include(<luma/publish.hpp>)
#include <luma/publish.hpp>
#define LUMA_HAS_PUBLISH 1
#endif
#if __has_include(<xr/pose.hpp>)
#include <xr/pose.hpp>
#define LUMA_HAS_READER 1
#endif

#if defined(XR_ENABLE_LUMA_DRIVER) && defined(LUMA_HAS_TYPES) && defined(LUMA_HAS_TRANSPORT) && \
    defined(LUMA_HAS_PROTOCOL) && defined(LUMA_HAS_FUSION) && defined(LUMA_HAS_PUBLISH)
#define LUMA_CONTRACT_READY 1
#endif

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <concepts>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <optional>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace contract {
using Vec3 = std::array<float, 3>;

// types.hpp: exact field names, order (designated initializers need declaration order), types, and count (one more
// initializer than the contract has fields must be refused, which also catches a field hidden in padding).
template <class T>
concept ImuSampleShape =
    std::is_aggregate_v<T> &&
    requires {
        T{.t_ns = std::uint64_t{}, .gyro_rad_s = Vec3{}, .accel_m_s2 = Vec3{}, .mag = std::optional<Vec3>{}, .seq = std::uint32_t{}};
    } &&
    std::same_as<decltype(T::t_ns), std::uint64_t> && std::same_as<decltype(T::gyro_rad_s), Vec3> &&
    std::same_as<decltype(T::accel_m_s2), Vec3> && std::same_as<decltype(T::mag), std::optional<Vec3>> &&
    std::same_as<decltype(T::seq), std::uint32_t> && !requires { T{{}, {}, {}, {}, {}, {}}; };

template <class T>
constexpr bool identity_by_default() {
    const T q{};
    return q.w == 1.f && q.x == 0.f && q.y == 0.f && q.z == 0.f;
}
// Quatf is w first. xr::Quat, which the feed is read into, is x first: see the reader checks below.
template <class T>
concept QuatfShape =
    std::is_aggregate_v<T> && requires { T{.w = 0.f, .x = 0.f, .y = 0.f, .z = 0.f}; } &&
    std::same_as<decltype(T::w), float> && std::same_as<decltype(T::x), float> && std::same_as<decltype(T::y), float> &&
    std::same_as<decltype(T::z), float> && !requires { T{0.f, 0.f, 0.f, 0.f, 0.f}; } && (identity_by_default<T>());

template <class T>
constexpr bool pose_out_defaults() {
    const T p{};
    return identity_by_default<std::remove_cvref_t<decltype(p.q)>>() && !p.calibrated;
}
// Exact on purpose: prediction wants angular velocity, but every thread coded to these four fields, so anything more
// goes beside PoseOut (see docs/acceptance.md) or through a deliberate change to the contract and this test.
template <class T, class Q>
concept PoseOutShape =
    std::is_aggregate_v<T> &&
    requires { T{.q = Q{}, .t_ns = std::uint64_t{}, .seq = std::uint32_t{}, .calibrated = false}; } &&
    std::same_as<decltype(T::q), Q> && std::same_as<decltype(T::t_ns), std::uint64_t> &&
    std::same_as<decltype(T::seq), std::uint32_t> && std::same_as<decltype(T::calibrated), bool> &&
    !requires { T{{}, {}, {}, {}, {}}; } && (pose_out_defaults<T>());

template <class T>
concept NotVoid = !std::is_void_v<T>;

// transport.hpp. write(...) is open in the contract, so with or without a timeout. A transport owns a file descriptor:
// a copy would close it twice, and "clean close" means the destructor must not throw. No member may reset the device.
template <class T>
concept TransportShape =
    requires(T& t, std::span<std::uint8_t> in, std::span<const std::uint8_t> out, std::chrono::milliseconds ms) {
        { t.read(in, ms) } -> NotVoid;
        requires requires { t.write(out); } || requires { t.write(out, ms); };
    } &&
    std::is_nothrow_destructible_v<T> && !std::is_copy_constructible_v<T> && !std::is_copy_assignable_v<T> &&
    !requires(T& t) { t.reset(); } && !requires(T& t) { t.reset_device(); } && !requires(T& t) { t.usb_reset(); };

// protocol.hpp, through a small adapter (P) so fakes can stand in for the namespace-level function. The span must be
// const: parsing must not be able to write to the packet.
template <class P>
concept ProtocolShape = requires(std::span<const std::uint8_t> packet) {
    { P::parse_imu(packet) } -> std::same_as<std::optional<typename P::Imu>>;
};

// fusion.hpp: PoseOut update(const ImuSample&), callable on a const sample.
template <class T, class Imu, class Pose>
concept FusionShape = requires(T& f, const Imu& sample) {
    { f.update(sample) } -> std::same_as<Pose>;
};

// publish.hpp: only that it exists and owns its segments. Its bytes are drv-publisher's test against the real pose.cpp.
template <class T>
concept PublisherShape = std::is_class_v<T> && !std::is_copy_constructible_v<T> && !std::is_copy_assignable_v<T>;

// spike/include/xr/pose.hpp must stay as it is: "the renderer needs no change".
template <class Sample, class Quat>
concept ReaderSampleShape =
    std::same_as<decltype(Sample::orientation), Quat> && std::same_as<decltype(Sample::device_ms), float> &&
    std::same_as<decltype(Sample::older), std::array<Quat, 2>> &&
    std::same_as<decltype(Sample::older_ms), std::array<float, 2>>;
template <class Quat>
constexpr bool x_first() {
    const Quat q{1.f, 2.f, 3.f, 4.f};
    return q.x == 1.f && q.y == 2.f && q.z == 3.f && q.w == 4.f;
}

// Layer 1: what each checker must accept and refuse.
namespace fake {
struct Quat { float w = 1, x = 0, y = 0, z = 0; };
struct Imu { std::uint64_t t_ns; Vec3 gyro_rad_s; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; };
struct Pose { Quat q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated; };

struct ImuSwapped { Vec3 gyro_rad_s; std::uint64_t t_ns; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; };
struct ImuRenamed { std::uint64_t t_ns; Vec3 gyro; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; };
struct ImuDouble { std::uint64_t t_ns; std::array<double, 3> gyro_rad_s; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; };
struct ImuPlainMag { std::uint64_t t_ns; Vec3 gyro_rad_s; Vec3 accel_m_s2; Vec3 mag; std::uint32_t seq; };
struct ImuNarrowSeq { std::uint64_t t_ns; Vec3 gyro_rad_s; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint16_t seq; };
struct ImuExtra { std::uint64_t t_ns; Vec3 gyro_rad_s; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; std::uint32_t in_padding; };
struct ImuCtor { std::uint64_t t_ns; Vec3 gyro_rad_s; Vec3 accel_m_s2; std::optional<Vec3> mag; std::uint32_t seq; ImuCtor() {} };

struct QuatXFirst { float x = 0, y = 0, z = 0, w = 1; };
struct QuatZeroW { float w = 0, x = 0, y = 0, z = 0; };
struct QuatDouble { double w = 1, x = 0, y = 0, z = 0; };

struct PoseGyro { Quat q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated; Vec3 gyro_rad_s; };
struct PosePadding { Quat q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated; bool stale; };
struct PoseIntFlag { Quat q; std::uint64_t t_ns; std::uint32_t seq; int calibrated; };
struct PoseWrongQuat { QuatXFirst q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated; };
struct PoseCalibratedDefault { Quat q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated = true; };

struct Transport {
    Transport() = default;
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;
    int read(std::span<std::uint8_t>, std::chrono::milliseconds);
    int write(std::span<const std::uint8_t>);
};
struct TransportWriteTimeout : Transport { int write(std::span<const std::uint8_t>, std::chrono::milliseconds); };
struct TransportNoTimeout {
    TransportNoTimeout(const TransportNoTimeout&) = delete;
    int read(std::span<std::uint8_t>);
    int write(std::span<const std::uint8_t>);
};
struct TransportVoidRead : Transport { void read(std::span<std::uint8_t>, std::chrono::milliseconds); };
struct TransportNoWrite {
    TransportNoWrite(const TransportNoWrite&) = delete;
    int read(std::span<std::uint8_t>, std::chrono::milliseconds);
};
struct TransportCopyable {
    int read(std::span<std::uint8_t>, std::chrono::milliseconds);
    int write(std::span<const std::uint8_t>);
};
struct TransportResets : Transport { void reset(); };
struct TransportResetsDevice : Transport { void reset_device(); };
struct TransportThrowingClose : Transport { ~TransportThrowingClose() noexcept(false); };

struct Protocol { using Imu = fake::Imu; static std::optional<Imu> parse_imu(std::span<const std::uint8_t>); };
struct ProtocolNonConst { using Imu = fake::Imu; static std::optional<Imu> parse_imu(std::span<std::uint8_t>); };
struct ProtocolNoOptional { using Imu = fake::Imu; static Imu parse_imu(std::span<const std::uint8_t>); };

struct Fusion { Pose update(const Imu&); };
struct FusionByMutableRef { Pose update(Imu&); };
struct FusionWrongReturn { Quat update(const Imu&); };
struct FusionTwoArgs { Pose update(const Imu&, float dt); };

struct Publisher { Publisher(const Publisher&) = delete; Publisher& operator=(const Publisher&) = delete; };
struct PublisherCopyable {};

struct ReaderQuat { float x = 0, y = 0, z = 0, w = 1; };
struct ReaderSample { ReaderQuat orientation; float device_ms = 0; std::array<ReaderQuat, 2> older{}; std::array<float, 2> older_ms{}; };
struct ReaderSampleDoubleMs { ReaderQuat orientation; double device_ms = 0; std::array<ReaderQuat, 2> older{}; std::array<float, 2> older_ms{}; };
}  // namespace fake

static_assert(ImuSampleShape<fake::Imu>);
static_assert(!ImuSampleShape<fake::ImuSwapped>);
static_assert(!ImuSampleShape<fake::ImuRenamed>);
static_assert(!ImuSampleShape<fake::ImuDouble>);
static_assert(!ImuSampleShape<fake::ImuPlainMag>);
static_assert(!ImuSampleShape<fake::ImuNarrowSeq>);
static_assert(!ImuSampleShape<fake::ImuExtra>);
static_assert(!ImuSampleShape<fake::ImuCtor>);

static_assert(QuatfShape<fake::Quat>);
static_assert(!QuatfShape<fake::QuatXFirst>);
static_assert(!QuatfShape<fake::QuatZeroW>);
static_assert(!QuatfShape<fake::QuatDouble>);

static_assert(PoseOutShape<fake::Pose, fake::Quat>);
static_assert(!PoseOutShape<fake::PoseGyro, fake::Quat>);
static_assert(!PoseOutShape<fake::PosePadding, fake::Quat>);
static_assert(!PoseOutShape<fake::PoseIntFlag, fake::Quat>);
static_assert(!PoseOutShape<fake::PoseWrongQuat, fake::Quat>);
static_assert(!PoseOutShape<fake::PoseCalibratedDefault, fake::Quat>);

static_assert(TransportShape<fake::Transport>);
static_assert(TransportShape<fake::TransportWriteTimeout>);
static_assert(!TransportShape<fake::TransportNoTimeout>);
static_assert(!TransportShape<fake::TransportVoidRead>);
static_assert(!TransportShape<fake::TransportNoWrite>);
static_assert(!TransportShape<fake::TransportCopyable>);
static_assert(!TransportShape<fake::TransportResets>);
static_assert(!TransportShape<fake::TransportResetsDevice>);
static_assert(!TransportShape<fake::TransportThrowingClose>);

static_assert(ProtocolShape<fake::Protocol>);
static_assert(!ProtocolShape<fake::ProtocolNonConst>);
static_assert(!ProtocolShape<fake::ProtocolNoOptional>);

static_assert(FusionShape<fake::Fusion, fake::Imu, fake::Pose>);
static_assert(!FusionShape<fake::FusionByMutableRef, fake::Imu, fake::Pose>);
static_assert(!FusionShape<fake::FusionWrongReturn, fake::Imu, fake::Pose>);
static_assert(!FusionShape<fake::FusionTwoArgs, fake::Imu, fake::Pose>);

static_assert(PublisherShape<fake::Publisher>);
static_assert(!PublisherShape<fake::PublisherCopyable>);
static_assert(!PublisherShape<int>);

static_assert(ReaderSampleShape<fake::ReaderSample, fake::ReaderQuat>);
static_assert(!ReaderSampleShape<fake::ReaderSampleDoubleMs, fake::ReaderQuat>);
static_assert(x_first<fake::ReaderQuat>());
static_assert(!x_first<fake::Quat>());

// Layer 3: the source scan. Comments are blanked first, because the contract asks for sources to be cited and a
// comment saying "we never call USBDEVFS_RESET" must pass. Strings stay: a path or a library name in one still counts.
inline bool ident_char(char c) { return std::isalnum(static_cast<unsigned char>(c)) != 0 || c == '_'; }

inline std::string strip_comments(std::string_view s) {
    enum class In { code, line, block, string, character } in = In::code;
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) {
        const char c = s[i], next = i + 1 < s.size() ? s[i + 1] : '\0';
        switch (in) {
        case In::code:
            if (c == '/' && next == '/') { in = In::line; out += "  "; ++i; }
            else if (c == '/' && next == '*') { in = In::block; out += "  "; ++i; }
            else if (c == 'R' && next == '"' && (i == 0 || !ident_char(s[i - 1]))) {  // raw string: copied whole
                const std::size_t open = s.find('(', i + 2);
                if (open == std::string_view::npos) { out += s.substr(i); return out; }
                const std::string close = ")" + std::string(s.substr(i + 2, open - i - 2)) + "\"";
                const std::size_t end = s.find(close, open);
                if (end == std::string_view::npos) { out += s.substr(i); return out; }
                out += s.substr(i, end + close.size() - i);
                i = end + close.size() - 1;
            }
            else if (c == '"') { in = In::string; out += c; }
            // After a letter or digit an apostrophe is a digit separator (1'000), not a character literal.
            else if (c == '\'' && !(i > 0 && std::isalnum(static_cast<unsigned char>(s[i - 1])) != 0)) { in = In::character; out += c; }
            else out += c;
            break;
        case In::line:
            if (c == '\\' && next == '\n') { out += " \n"; ++i; }
            else if (c == '\n') { in = In::code; out += c; }
            else out += ' ';
            break;
        case In::block:
            if (c == '*' && next == '/') { in = In::code; out += "  "; ++i; }
            else out += c == '\n' ? '\n' : ' ';
            break;
        case In::string:
        case In::character:
            out += c;
            if (c == '\\' && next != '\0') { out += next; ++i; }
            else if (c == (in == In::string ? '"' : '\'') || c == '\n') in = In::code;
            break;
        }
    }
    return out;
}

struct Rule {
    std::string_view text;
    bool word;  // whole identifier only, so AF_INET does not match AF_INET6 or MY_AF_INET
    std::string_view why;
};
inline constexpr Rule kForbidden[] = {
    // "never resets the device": the driver may only claim, read and write.
    {"USBDEVFS_RESET", true, "resets the device"},
    {"USBDEVFS_RESETEP", true, "resets an endpoint"},
    {"USBDEVFS_CLEAR_HALT", true, "resets an endpoint"},
    {"USBDEVFS_DISCONNECT", true, "detaches a kernel driver"},
    {"USBDEVFS_DISCONNECT_CLAIM", true, "detaches a kernel driver"},
    {"USBDEVFS_SETCONFIGURATION", true, "changes the configuration"},
    {"USBDEVFS_SETINTERFACE", true, "changes the interface setting"},
    {"libusb_reset_device", true, "resets the device"},
    {"/authorized", false, "deauthorizes the device in sysfs"},
    {"/unbind", false, "unbinds a kernel driver in sysfs"},
    {"power/control", false, "changes autosuspend, which the usb-drop-fix thread owns"},
    // No network, ever: the driver also runs in a network namespace, but that does not stop name lookups here.
    {"AF_INET", true, "opens a network socket"},
    {"AF_INET6", true, "opens a network socket"},
    {"AF_PACKET", true, "opens a raw network socket"},
    {"getaddrinfo", true, "resolves a name over the network"},
    {"gethostbyname", true, "resolves a name over the network"},
    {"<netdb.h>", false, "is the name lookup API"},
    {"<netinet/", false, "is the network API"},
    {"<arpa/inet.h>", false, "is the network API"},
    {"<curl/", false, "is a network library and a new dependency"},
    // No VITURE SDK (licence not accepted) and no runtime dependency beyond libc and libudev.
    {"libglasses", false, "is the VITURE SDK"},
    {"libcarina", false, "is the VITURE SDK"},
    {"viture_device", false, "is a VITURE SDK header"},
    {"viture_glasses_provider", false, "is a VITURE SDK header"},
    {"viture_protocol", false, "is a VITURE SDK header"},
    {"xr_device_provider", false, "is the VITURE SDK API"},
    {"dlopen", true, "loads a library the build cannot see"},
    {"<libusb", false, "is a new runtime dependency"},
    {"<hidapi", false, "is a new runtime dependency"},
    // Minimal commands: the suspect in the drops. This driver has no business asking the display mode.
    {"get_display_mode", false, "is the command the drops point at; the driver must not poll it"},
    // The driver publishes a feed. It must not be able to move the mouse, as XRLinuxDriver's output modes can.
    {"uinput", false, "can drive the mouse or keyboard"},
};

struct Finding { std::string file; std::size_t line; std::string message; };

inline void scan_text(std::string_view file, std::string_view text, std::vector<Finding>& findings) {
    const std::string code = strip_comments(text);
    for (const Rule& rule : kForbidden) {
        for (std::size_t at = code.find(rule.text); at != std::string::npos; at = code.find(rule.text, at + 1)) {
            const std::size_t end = at + rule.text.size();
            if (rule.word && ((at > 0 && ident_char(code[at - 1])) || (end < code.size() && ident_char(code[end])))) continue;
            const auto line = 1 + static_cast<std::size_t>(std::count(code.begin(), code.begin() + static_cast<std::ptrdiff_t>(at), '\n'));
            findings.push_back({std::string(file), line, std::string(rule.text) + " " + std::string(rule.why)});
        }
    }
}

// What pose.cpp cannot work without (PoseFeed::open fails if any is missing) or cannot read safely.
inline constexpr std::string_view kPublisherNeeds[] = {"/tmp/shader_runtime_", "pose_orientation", "pose_orientation_mutex", "disabled",
                                                       "PTHREAD_PROCESS_SHARED", "PTHREAD_MUTEX_ROBUST"};

inline std::vector<std::string> publisher_missing(std::string_view text) {
    const std::string code = strip_comments(text);
    std::vector<std::string> missing;
    for (const std::string_view need : kPublisherNeeds)
        if (code.find(need) == std::string::npos) missing.emplace_back(need);
    return missing;
}

inline bool read_file(const std::filesystem::path& path, std::string& text) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::ostringstream all;
    all << in.rdbuf();
    text = all.str();
    return true;
}

class Report {
public:
    void check(bool ok, std::string_view what) {
        ++checks_;
        if (ok) return;
        ++failures_;
        std::fprintf(stderr, "contract_test: FAIL %.*s\n", static_cast<int>(what.size()), what.data());
    }
    int failures() const { return failures_; }
    int checks() const { return checks_; }
private:
    int checks_ = 0, failures_ = 0;
};

inline std::size_t count_for(std::string_view text) {
    std::vector<Finding> findings;
    scan_text("fixture", text, findings);
    return findings.size();
}

// Layer 1 for the scanner: the cases that would make it cry wolf or go quiet.
inline void scanner_self_test(Report& r) {
    r.check(count_for("ioctl(fd, USBDEVFS_RESET, 0);") == 1, "a reset ioctl is found");
    r.check(count_for("// we never call USBDEVFS_RESET\n") == 0, "a line comment citing a reset is not a finding");
    r.check(count_for("/* see USBDEVFS_RESET\n   and AF_INET */ int x;") == 0, "a block comment is not a finding");
    r.check(count_for("/* comment */ ioctl(fd, USBDEVFS_RESET, 0);") == 1, "code after a block comment is scanned");
    r.check(count_for("int v = 1'000'000; ioctl(fd, USBDEVFS_RESET, 0);") == 1, "a digit separator does not hide what follows");
    r.check(count_for("char q = '\"'; ioctl(fd, USBDEVFS_RESET, 0);") == 1, "a quote character does not hide what follows");
    r.check(count_for("const char* s = \"// not a comment\"; ioctl(fd, USBDEVFS_RESET, 0);") == 1, "// inside a string is not a comment");
    r.check(count_for("const char* s = \"a\\\"b // x\"; ioctl(fd, USBDEVFS_RESET, 0);") == 1, "an escaped quote does not end a string");
    r.check(count_for("auto s = R\"x(a \" // b)x\"; ioctl(fd, USBDEVFS_RESET, 0);") == 1, "a raw string does not hide what follows");
    r.check(count_for("socket(AF_NETLINK, SOCK_RAW, 15); socket(AF_UNIX, SOCK_STREAM, 0);") == 0, "netlink and unix sockets are allowed");
    r.check(count_for("socket(AF_INET, SOCK_STREAM, 0);") == 1, "an IPv4 socket is found");
    r.check(count_for("socket(AF_INET6, SOCK_STREAM, 0);") == 1, "an IPv6 socket is found");
    r.check(count_for("int MY_AF_INET_COUNT = 0;") == 0, "a longer identifier is not a match");
    r.check(count_for("USBDEVFS_RESET") == 1 && count_for("USBDEVFS_RESETEP") == 1, "RESET and RESETEP are told apart");
    r.check(count_for("#include <netinet/in.h>\n") == 1, "a network header is found");
    r.check(count_for("#include <libudev.h>\n#include <linux/hidraw.h>\n") == 0, "libudev and hidraw are allowed");
    r.check(count_for("#include <libusb-1.0/libusb.h>\n") == 1, "libusb is a new dependency");
    r.check(count_for("std::ofstream(dev + \"/authorized\") << 0;") == 1, "a path inside a string is scanned");
    r.check(count_for("device.get_display_mode();") == 1, "the suspect command is found");
    r.check(count_for("// get_display_mode is what the drops point at\n") == 0, "a comment about the suspect is fine");

    std::vector<Finding> findings;
    scan_text("f", "\n\n/* a\n b */\nsocket(AF_INET, 1, 0);", findings);
    r.check(findings.size() == 1 && findings[0].line == 5, "findings carry the right line");

    const std::string all =
        "const char* p = \"/tmp/shader_runtime_\"; \"pose_orientation\"; \"pose_orientation_mutex\"; \"disabled\";\n"
        "pthread_mutexattr_setpshared(&a, PTHREAD_PROCESS_SHARED); pthread_mutexattr_setrobust(&a, PTHREAD_MUTEX_ROBUST);";
    r.check(publisher_missing(all).empty(), "a publisher with everything passes");
    r.check(publisher_missing("// PTHREAD_MUTEX_ROBUST\n\"/tmp/shader_runtime_\"").size() == 5, "a mention in a comment does not count");
}
}  // namespace contract

// Layer 2: the real headers.
#ifdef LUMA_CONTRACT_READY
namespace contract {
struct RealProtocol {
    using Imu = luma::ImuSample;
    static auto parse_imu(std::span<const std::uint8_t> packet) -> decltype(luma::parse_imu(packet)) { return luma::parse_imu(packet); }
};
}  // namespace contract
static_assert(contract::ImuSampleShape<luma::ImuSample>, "types.hpp: ImuSample differs from the contract");
static_assert(contract::QuatfShape<luma::Quatf>, "types.hpp: Quatf differs from the contract");
static_assert(contract::PoseOutShape<luma::PoseOut, luma::Quatf>, "types.hpp: PoseOut differs from the contract");
static_assert(contract::TransportShape<luma::Transport>, "transport.hpp: Transport differs from the contract");
static_assert(contract::ProtocolShape<contract::RealProtocol>, "protocol.hpp: parse_imu differs from the contract");
static_assert(sizeof(luma::Command) > 0, "protocol.hpp: luma::Command must be a type");
static_assert(contract::FusionShape<luma::Fusion, luma::ImuSample, luma::PoseOut>, "fusion.hpp: Fusion::update differs from the contract");
static_assert(contract::PublisherShape<luma::Publisher>, "publish.hpp: Publisher differs from the contract");
#endif

#ifdef LUMA_HAS_READER
static_assert(contract::ReaderSampleShape<xr::FeedSample, xr::Quat>, "pose.hpp: the renderer's FeedSample changed; the contract says it does not");
static_assert(contract::x_first<xr::Quat>(), "pose.hpp: the feed stores x, y, z, w, and pose.cpp reads it into xr::Quat in that order");
#endif
#if defined(LUMA_HAS_READER) && defined(LUMA_HAS_TYPES)
// The trap this pins: Quatf{a, b, c, d} and xr::Quat{a, b, c, d} are different rotations. Publisher has to reorder.
static_assert(!contract::x_first<luma::Quatf>(), "Quatf is w first by contract, so publish must reorder it to x, y, z, w");
#endif

namespace contract {
namespace {
#ifndef LUMA_CONTRACT_READY
void report_missing() {
    const auto say = [](const char* what) { std::printf("contract_test: waiting for %s\n", what); };
#ifndef XR_ENABLE_LUMA_DRIVER
    say("XR_ENABLE_LUMA_DRIVER (defined by the wiring in docs/acceptance.md)");
#endif
#ifndef LUMA_HAS_TYPES
    say("luma/types.hpp (drv-protocol-impl)");
#endif
#ifndef LUMA_HAS_TRANSPORT
    say("luma/transport.hpp (drv-transport)");
#endif
#ifndef LUMA_HAS_PROTOCOL
    say("luma/protocol.hpp (drv-protocol-impl)");
#endif
#ifndef LUMA_HAS_FUSION
    say("luma/fusion.hpp (drv-fusion)");
#endif
#ifndef LUMA_HAS_PUBLISH
    say("luma/publish.hpp (drv-publisher)");
#endif
}
#endif

[[maybe_unused]] void scan_tree(const std::filesystem::path& root, Report& r) {
    namespace fs = std::filesystem;
    r.check(fs::is_directory(root / "include/luma") && fs::is_directory(root / "src"), "the source directory holds include/luma and src");
    if (r.failures() != 0) return;

    // tests/ and bench/ are left out: fixtures and this file name forbidden things on purpose. docs/ is not code.
    std::vector<Finding> findings;
    std::string publisher;
    std::size_t scanned = 0;
    for (const char* dir : {"include", "src"}) {
        for (const auto& entry : fs::recursive_directory_iterator(root / dir)) {
            const std::string extension = entry.path().extension().string();
            if (!entry.is_regular_file() || (extension != ".hpp" && extension != ".h" && extension != ".cpp" && extension != ".c")) continue;
            std::string text;
            r.check(read_file(entry.path(), text), "every source file can be read");
            scan_text(fs::relative(entry.path(), root).string(), text, findings);
            ++scanned;
            if (entry.path().filename() == "publish.cpp" || entry.path().filename() == "publish.hpp") publisher += text + "\n";
        }
    }
    r.check(scanned != 0, "the scan found source files");
    for (const Finding& f : findings)
        r.check(false, f.file + ":" + std::to_string(f.line) + ": " + f.message);

    r.check(!publisher.empty(), "src/publish.cpp or include/luma/publish.hpp exists");
    for (const std::string& missing : publisher_missing(publisher))
        r.check(false, "the publisher never mentions " + missing + ", which pose.cpp needs");

    r.check(fs::is_regular_file(root / "src/main.cpp"), "src/main.cpp exists (the daemon)");
    std::string cmake;
    r.check(read_file(root / "CMakeLists.txt", cmake), "spike/driver/CMakeLists.txt exists");
    r.check(cmake.find("xr_luma_driver") != std::string::npos, "CMake defines the static library xr_luma_driver");
    r.check(cmake.find("xr-luma-driver") != std::string::npos, "CMake defines the executable xr-luma-driver");
}
}  // namespace
}  // namespace contract

int main(int argc, char** argv) {
    contract::Report report;
    contract::scanner_self_test(report);
    if (report.failures() != 0) {
        std::fprintf(stderr, "contract_test: the checker itself is broken (%d of %d)\n", report.failures(), report.checks());
        return 1;
    }

#ifndef LUMA_CONTRACT_READY
    contract::report_missing();
    (void)argc;
    (void)argv;
#ifdef LUMA_CONTRACT_REQUIRED
    std::fprintf(stderr, "contract_test: FAIL the driver is meant to be complete (LUMA_CONTRACT_REQUIRED) but is not\n");
    return 1;
#else
    std::printf("contract_test: SKIP real headers not all present (%d self-checks passed)\n", report.checks());
    return 77;
#endif
#else
    const std::filesystem::path root = argc > 1 ? std::filesystem::path(argv[1]) : std::filesystem::path(__FILE__).parent_path().parent_path();
    contract::scan_tree(root, report);
    if (report.failures() != 0) {
        std::fprintf(stderr, "contract_test: %d of %d checks failed\n", report.failures(), report.checks());
        return 1;
    }
    std::printf("contract_test: PASS (%d checks; the headers match the contract)\n", report.checks());
    return 0;
#endif
}
