// See features.hpp for the rules and docs/features.md for sources.
#include "luma/features.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <system_error>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace luma::features {
namespace {

constexpr std::size_t idx(Request r) { return static_cast<std::size_t>(r); }

Nanos to_ns(std::chrono::nanoseconds d) { return static_cast<Nanos>(d.count()); }

const char* request_name(Request r) {
    switch (r) {
        case Request::QueryFirmware: return "query-firmware";
        case Request::QueryWear: return "query-wear";
        case Request::SetBrightness: return "set-brightness";
        case Request::SetDimming: return "set-dimming";
    }
    return "?";
}

// Host-to-glasses ids that start or stop the IMU stream. A map entry must not
// be able to turn a feature into a tracking outage.
// 0x0015: V1 IMU control. 0x0301: V2 IMU control.
constexpr std::array<std::uint16_t, 3> kForbiddenSend{0x0000, 0x0015, 0x0301};

bool parse_int(std::string_view text, long& out) {
    int base = 10;
    if (text.starts_with("0x") || text.starts_with("0X")) {
        text.remove_prefix(2);
        base = 16;
    }
    if (text.empty()) return false;
    const auto [ptr, ec] = std::from_chars(text.data(), text.data() + text.size(), out, base);
    return ec == std::errc{} && ptr == text.data() + text.size();
}

std::uint32_t fnv1a(std::span<const std::uint8_t> bytes) {
    std::uint32_t h = 2166136261u;
    for (const auto b : bytes) h = (h ^ b) * 16777619u;
    return h;
}

// Keeps text safe to embed in a JSON string: printable ASCII, no quote or backslash.
void copy_clean(std::span<const std::uint8_t> in, std::array<char, 40>& out) {
    out.fill('\0');
    std::size_t n = 0;
    for (const auto c : in) {
        if (c == 0 || n + 1 >= out.size()) break;
        if (c < 0x20 || c > 0x7e || c == '"' || c == '\\') continue;
        out[n++] = static_cast<char>(c);
    }
}

void copy_clean(std::string_view in, std::array<char, 40>& out) {
    copy_clean(std::span(reinterpret_cast<const std::uint8_t*>(in.data()), in.size()), out);
}

}  // namespace

WireMap default_map(WireProfile profile) {
    WireMap m;
    if (profile == WireProfile::CarinaV1) {
        // Event ids from the older SDK header in XRLinuxDriver's history
        // (include/sdks/viture_one.h @ 321cc88). Unproven on the Luma Ultra.
        m.ev_brightness = 0x0301;
        m.ev_display_mode = 0x0302;
        m.ev_volume = 0x0304;
        return m;
    }
    // elasticjava/viture-v2 PROTOCOL.md, measured on a Pro 2.
    m.routes[idx(Request::QueryFirmware)] = {0x3003, 0x5003};
    m.routes[idx(Request::QueryWear)] = {0x3321, 0x5321};
    return m;
}

bool apply_map_override(WireMap& map, std::string_view entry, std::string* error) {
    const auto fail = [&](std::string message) {
        if (error) *error = std::move(message) + ": " + std::string(entry);
        return false;
    };
    const auto eq = entry.find('=');
    if (eq == std::string_view::npos) return fail("expected key=value");
    const auto key = entry.substr(0, eq);
    long value = 0;
    if (!parse_int(entry.substr(eq + 1), value)) return fail("bad number");

    if (key == "reply_status") {
        if (value != 0 && value != 1) return fail("reply_status is 0 or 1");
        map.reply_has_status = value == 1;
        return true;
    }
    if (key == "film_levels") {
        if (value < 1 || value > 255) return fail("film_levels is 1..255");
        map.film_levels = static_cast<int>(value);
        return true;
    }
    if (value < 0 || value > 0xFFFF) return fail("id out of range");
    const auto id = static_cast<std::uint16_t>(value);

    const struct { std::string_view name; std::optional<std::uint16_t>* slot; } events[] = {
        {"ev_brightness", &map.ev_brightness}, {"ev_display_mode", &map.ev_display_mode},
        {"ev_volume", &map.ev_volume},         {"ev_film", &map.ev_film},
        {"ev_wear", &map.ev_wear},
    };
    for (const auto& e : events) {
        if (key == e.name) {
            *e.slot = id;
            return true;
        }
    }
    const struct { std::string_view stem; Request request; } routes[] = {
        {"firmware", Request::QueryFirmware}, {"wear", Request::QueryWear},
        {"brightness", Request::SetBrightness}, {"dimming", Request::SetDimming},
    };
    for (const auto& r : routes) {
        if (!key.starts_with(r.stem)) continue;
        const auto suffix = key.substr(r.stem.size());
        if (suffix == "_reply") {
            map.routes[idx(r.request)].reply = id;
            return true;
        }
        if (suffix == "_send") {
            if (std::find(kForbiddenSend.begin(), kForbiddenSend.end(), id) != kForbiddenSend.end())
                return fail("refused: that id controls the IMU stream");
            map.routes[idx(r.request)].send = id;
            return true;
        }
    }
    return fail("unknown key");
}

std::span<const FlagInfo> flag_table() {
    static constexpr std::array<FlagInfo, 14> table{{
        {"state-events", true, false, "None: listens for what the glasses already report."},
        {"wear-events", true, false, "None: listens only."},
        {"temperature", true, false, "None: reads a value already in the stream, if there is one."},
        {"usb-identity", true, false, "None: reads the kernel's cached descriptors."},
        {"discovery", false, false, "Log noise only, capped per message id."},
        {"still-wear", false, false, "A guess from stillness: glasses resting on a stand read as off, and so might a very still head."},
        {"auto-pause", false, false, "XR could pause while worn if the wear signal is wrong."},
        {"oled-saver", false, false, "The picture could blank while worn if the wear signal is wrong."},
        {"ipc", false, false, "None for USB: a dropped datagram loses one event."},
        {"firmware-query", false, true, "One command per connection. Commands are the suspect in the USB drops."},
        {"wear-query", false, true, "One command per connection. Commands are the suspect in the USB drops."},
        {"control", false, true, "Commands on explicit request only, paced and capped."},
        {"brightness", false, true, "One command per connection. Changes the panel."},
        {"dimming", false, true, "One command per connection. Changes the lens tint."},
    }};
    return table;
}

FlagResult parse_flag(Config& config, std::string_view arg, std::string* error) {
    const auto bad = [&](std::string message) {
        if (error) *error = std::move(message);
        return FlagResult::Bad;
    };
    bool on = true;
    if (arg.starts_with("--no-feature-")) {
        arg.remove_prefix(13);
        on = false;
    } else if (arg.starts_with("--feature-")) {
        arg.remove_prefix(10);
    } else {
        return FlagResult::NotMine;
    }
    const auto eq = arg.find('=');
    const auto name = arg.substr(0, eq);
    const bool has_value = eq != std::string_view::npos;
    const auto text = has_value ? arg.substr(eq + 1) : std::string_view{};
    const std::string shown(name);

    const struct { std::string_view name; bool Config::*member; } bools[] = {
        {"state-events", &Config::state_events}, {"wear-events", &Config::wear_events},
        {"temperature", &Config::temperature},   {"usb-identity", &Config::usb_identity},
        {"discovery", &Config::discovery},       {"still-wear", &Config::still_wear},
        {"auto-pause", &Config::auto_pause},     {"oled-saver", &Config::oled_saver},
        {"ipc", &Config::ipc},                   {"firmware-query", &Config::firmware_query},
        {"wear-query", &Config::wear_query},     {"control", &Config::control},
    };
    for (const auto& b : bools) {
        if (name != b.name) continue;
        if (has_value) return bad("--feature-" + shown + " takes no value");
        config.*(b.member) = on;
        return FlagResult::Ok;
    }

    if (!on) {
        if (name == "brightness") config.brightness.reset();
        else if (name == "dimming") config.dimming.reset();
        else return bad("no such switch: " + shown);
        return FlagResult::Ok;
    }
    if (!has_value) return bad("--feature-" + shown + " needs a value");

    if (name == "profile") {
        // Switching would silently throw away entries given earlier.
        if (config.map_overridden) return bad("--feature-profile must come before --feature-map");
        if (text == "carina") config.profile = WireProfile::CarinaV1;
        else if (text == "gen2") config.profile = WireProfile::Gen2V2;
        else return bad("profile is carina or gen2");
        config.map = default_map(config.profile);
        return FlagResult::Ok;
    }
    if (name == "map") {
        std::string message;
        if (!apply_map_override(config.map, text, &message)) return bad(message);
        config.map_overridden = true;
        return FlagResult::Ok;
    }
    long value = 0;
    if (!parse_int(text, value)) return bad("--feature-" + shown + ": bad number");
    if (name == "brightness") {
        if (value < 0 || value > kBrightnessMax) return bad("brightness is 0..8");
        config.brightness = static_cast<int>(value);
    } else if (name == "dimming") {
        if (value < 0 || value > config.map.film_levels) return bad("dimming is above film_levels");
        config.dimming = static_cast<int>(value);
    } else if (name == "wear-off-ms") {
        if (value < 0) return bad("wear-off-ms is not negative");
        config.wear_off_debounce = std::chrono::milliseconds(value);
    } else if (name == "saver-delay-ms") {
        if (value < 0) return bad("saver-delay-ms is not negative");
        config.saver_delay = std::chrono::milliseconds(value);
    } else if (name == "still-off-s") {
        if (value < 10) return bad("still-off-s is at least 10");
        config.still_off = std::chrono::seconds(value);
    } else {
        return bad("no such feature: " + shown);
    }
    return FlagResult::Ok;
}

std::string flag_usage() {
    std::string out = "Feature flags (--feature-NAME, --no-feature-NAME):\n";
    for (const auto& f : flag_table()) {
        out += "  ";
        out += f.name;
        if (f.name == "brightness") out += "=0..8";
        if (f.name == "dimming") out += "=LEVEL";
        out += f.default_on ? " [on]" : " [off]";
        out += f.sends_usb ? " sends USB. " : " ";
        out += f.risk;
        out += '\n';
    }
    out += "  profile=carina|gen2, map=KEY=ID, wear-off-ms=N, saver-delay-ms=N, still-off-s=N\n";
    return out;
}

std::size_t format_event(const Event& e, std::span<char> out) {
    if (out.empty()) return 0;
    const auto t = static_cast<unsigned long long>(e.t_ns);
    const auto line = [&](const char* name, const char* format, auto... args) {
        char tail[160];
        std::snprintf(tail, sizeof tail, format, args...);
        const int n = std::snprintf(out.data(), out.size(), R"({"src":"luma-feature","ev":"%s","t_ns":%llu%s})",
                                    name, t, tail);
        if (n < 0) return std::size_t{0};
        return std::min(static_cast<std::size_t>(n), out.size() - 1);
    };
    const auto req = request_name(e.request);
    switch (e.kind) {
        case EventKind::Brightness: return line("brightness", R"(,"value":%d)", e.value);
        case EventKind::Volume: return line("volume", R"(,"value":%d)", e.value);
        case EventKind::DisplayMode: return line("display_mode", R"(,"value":%d)", e.value);
        case EventKind::Dimming: return line("dimming", R"(,"value":%d)", e.value);
        case EventKind::Wear:
            return line("wear", R"(,"worn":%s,"source":"%s")", e.value ? "true" : "false",
                        e.wear_source == WearSource::Hardware ? "hardware" : "stillness");
        case EventKind::Pause: return line("pause", "%s", "");
        case EventKind::Resume: return line("resume", "%s", "");
        case EventKind::SaverOn: return line("saver_on", "%s", "");
        case EventKind::SaverOff: return line("saver_off", "%s", "");
        case EventKind::Firmware: return line("firmware", R"(,"version":"%s")", e.text.data());
        case EventKind::Temperature: return line("temperature", R"(,"celsius":%.1f)", static_cast<double>(e.celsius));
        case EventKind::Identity:
            return line("identity", R"(,"product":"%s","bcd_device":"%04x","speed_mbps":%d)", e.text.data(),
                        static_cast<unsigned>(e.msg_id), e.value);
        case EventKind::LinkDegraded: return line("link_degraded", R"(,"speed_mbps":%d)", e.value);
        case EventKind::Unavailable: return line("unavailable", R"(,"request":"%s")", req);
        case EventKind::Sent: return line("sent", R"(,"request":"%s","id":"0x%04x")", req, static_cast<unsigned>(e.msg_id));
        case EventKind::Rejected: return line("rejected", R"(,"request":"%s","status":%d)", req, e.value);
        case EventKind::TimedOut: return line("timed_out", R"(,"request":"%s")", req);
        case EventKind::Tripped: return line("commands_stopped", R"(,"scope":"connection","request":"%s")", req);
        case EventKind::LatchedOff: return line("commands_stopped", R"(,"scope":"until_restart","request":"%s")", req);
        case EventKind::EventsDropped: return line("events_dropped", R"(,"count":%d)", e.value);
        case EventKind::Discovered: {
            char hex[sizeof e.data * 2 + 1] = {};
            for (std::size_t i = 0; i < e.data_len && i < e.data.size(); ++i)
                std::snprintf(hex + i * 2, 3, "%02x", static_cast<unsigned>(e.data[i]));
            return line("discovered", R"(,"id":"0x%04x","len":%d,"data":"%s")", static_cast<unsigned>(e.msg_id),
                        e.value, hex);
        }
    }
    return 0;
}

Manager::Manager(Config config) : cfg_(std::move(config)) {}

Event& Manager::push(EventKind kind, Nanos now) {
    if (event_count_ == kEventSlots) {  // keep the newest; nothing may block here
        event_head_ = (event_head_ + 1) % kEventSlots;
        --event_count_;
        ++events_dropped_;
    }
    auto& e = events_[(event_head_ + event_count_) % kEventSlots];
    ++event_count_;
    e = Event{};
    e.kind = kind;
    e.t_ns = now;
    return e;
}

std::optional<Event> Manager::pop_event() {
    if (event_count_ == 0) {
        if (events_dropped_ == 0) return std::nullopt;
        Event e;
        e.kind = EventKind::EventsDropped;
        e.value = static_cast<std::int32_t>(events_dropped_);
        events_dropped_ = 0;
        return e;
    }
    const auto e = events_[event_head_];
    event_head_ = (event_head_ + 1) % kEventSlots;
    --event_count_;
    return e;
}

void Manager::on_connect(Nanos now) {
    end_wear_actions(now);  // in case the transport missed the disconnect
    connected_ = true;
    tripped_ = false;
    connected_at_ = now;
    inflight_.reset();
    awaiting_write_ = false;
    pending_.fill(false);
    sent_this_connection_ = 0;
    brightness_.reset();
    volume_.reset();
    display_mode_.reset();
    dimming_.reset();
    firmware_.fill('\0');
    worn_.reset();
    have_candidate_ = false;
    hw_wear_seen_ = false;
    last_celsius_.reset();
    seen_count_ = 0;

    const auto want = [&](Request kind, bool wanted, int value) {
        if (!wanted) return;
        if (!cfg_.map.routes[idx(kind)].send) {
            push(EventKind::Unavailable, now).request = kind;
            return;
        }
        pending_[idx(kind)] = true;
        pending_value_[idx(kind)] = static_cast<std::uint8_t>(value);
    };
    want(Request::QueryFirmware, cfg_.firmware_query, 0);
    want(Request::QueryWear, cfg_.wear_query, 0);
    want(Request::SetBrightness, cfg_.brightness.has_value(), cfg_.brightness.value_or(0));
    want(Request::SetDimming, cfg_.dimming.has_value(), cfg_.dimming.value_or(0));
}

void Manager::on_disconnect(Nanos now) {
    if (!connected_) return;
    connected_ = false;
    // A drop on the heels of a command: we cannot prove the command caused it,
    // and drops are the one thing this driver exists to stop.
    if (ever_sent_ && !latched_ && now - last_sent_ <= to_ns(cfg_.drop_window)) {
        latched_ = true;
        push(EventKind::LatchedOff, now).request = inflight_.value_or(Request::QueryFirmware);
    }
    inflight_.reset();
    awaiting_write_ = false;
    pending_.fill(false);
    end_wear_actions(now);  // never leave XR paused because the cable went
    worn_.reset();
    have_candidate_ = false;
}

void Manager::end_wear_actions(Nanos now) {
    if (paused_) {
        paused_ = false;
        push(EventKind::Resume, now);
    }
    saver_armed_ = false;
    if (saver_on_) {
        saver_on_ = false;
        push(EventKind::SaverOff, now);
    }
}

void Manager::state_changed(EventKind kind, std::optional<int>& known, int value, Nanos now) {
    if (known == value) return;  // a repeat is not a press
    known = value;
    push(kind, now).value = value;
}

void Manager::on_message(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now) {
    if (take_reply(id, payload, now)) return;

    const auto is = [&](const std::optional<std::uint16_t>& ev) { return ev && *ev == id; };
    const bool known = is(cfg_.map.ev_brightness) || is(cfg_.map.ev_volume) || is(cfg_.map.ev_display_mode) ||
                       is(cfg_.map.ev_film) || is(cfg_.map.ev_wear);
    if (!known) {
        if (cfg_.discovery) discover(id, payload, now);
        return;
    }
    if (payload.empty()) {
        ++malformed_;
        return;
    }
    const int value = payload[0];
    if (is(cfg_.map.ev_wear)) {
        if (!cfg_.wear_events) return;
        if (value > 1) {
            ++malformed_;
            return;
        }
        hw_wear_seen_ = true;
        feed_worn(value == 1, WearSource::Hardware, now);
        return;
    }
    if (!cfg_.state_events) return;
    if (is(cfg_.map.ev_brightness)) {
        if (value > kBrightnessMax) ++malformed_;
        else state_changed(EventKind::Brightness, brightness_, value, now);
    } else if (is(cfg_.map.ev_volume)) {
        state_changed(EventKind::Volume, volume_, value, now);
    } else if (is(cfg_.map.ev_display_mode)) {
        state_changed(EventKind::DisplayMode, display_mode_, value, now);
    } else if (value > cfg_.map.film_levels) {
        ++malformed_;
    } else {
        state_changed(EventKind::Dimming, dimming_, value, now);
    }
}

bool Manager::take_reply(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now) {
    if (!inflight_ || awaiting_write_) return false;
    const auto kind = *inflight_;
    if (cfg_.map.routes[idx(kind)].reply != id) return false;
    inflight_.reset();

    if (cfg_.map.reply_has_status) {
        if (payload.empty()) {
            ++malformed_;
            return true;
        }
        if (payload[0] != 0) {
            auto& e = push(EventKind::Rejected, now);
            e.request = kind;
            e.value = payload[0];
            return true;
        }
        payload = payload.subspan(1);
    }
    switch (kind) {
        case Request::QueryFirmware:
            copy_clean(payload, firmware_);
            if (firmware_[0] == '\0') ++malformed_;
            else push(EventKind::Firmware, now).text = firmware_;
            break;
        case Request::QueryWear:
            if (payload.empty() || payload[0] > 1) {
                ++malformed_;
            } else if (cfg_.wear_events) {
                hw_wear_seen_ = true;
                feed_worn(payload[0] == 1, WearSource::Hardware, now);
            }
            break;
        case Request::SetBrightness:
            brightness_ = inflight_value_;
            break;
        case Request::SetDimming:
            dimming_ = inflight_value_;
            break;
    }
    return true;
}

void Manager::feed_worn(bool worn, WearSource source, Nanos now) {
    if (have_candidate_ && candidate_ == worn) return;
    have_candidate_ = true;
    candidate_ = worn;
    candidate_since_ = now;
    candidate_source_ = source;
}

void Manager::commit_wear(Nanos now) {
    worn_ = candidate_;
    auto& e = push(EventKind::Wear, now);
    e.value = candidate_ ? 1 : 0;
    e.wear_source = candidate_source_;
    if (candidate_) {
        end_wear_actions(now);
        return;
    }
    if (cfg_.auto_pause && !paused_) {
        paused_ = true;
        push(EventKind::Pause, now);
    }
    if (cfg_.oled_saver && !saver_on_) {
        saver_armed_ = true;
        saver_due_ = now + to_ns(cfg_.saver_delay);
    }
}

void Manager::tick(Nanos now, std::optional<Nanos> last_motion) {
    if (connected_ && cfg_.wear_events && cfg_.still_wear && !hw_wear_seen_ && last_motion) {
        const Nanos since = std::max(*last_motion, connected_at_);
        feed_worn(now - std::min(now, since) < to_ns(cfg_.still_off), WearSource::Stillness, now);
    }
    if (connected_ && have_candidate_ && worn_ != candidate_) {
        const auto debounce = candidate_ ? cfg_.wear_on_debounce : cfg_.wear_off_debounce;
        if (now - candidate_since_ >= to_ns(debounce)) commit_wear(now);
    }
    if (saver_armed_ && now >= saver_due_) {
        saver_armed_ = false;
        saver_on_ = true;
        push(EventKind::SaverOn, now);
    }
    if (inflight_ && !awaiting_write_ && now - last_sent_ >= to_ns(cfg_.reply_timeout)) {
        push(EventKind::TimedOut, now).request = *inflight_;
        stop_connection(now);
    }
}

void Manager::stop_connection(Nanos now) {
    tripped_ = true;
    pending_.fill(false);
    push(EventKind::Tripped, now).request = inflight_.value_or(Request::QueryFirmware);
    inflight_.reset();
    awaiting_write_ = false;
}

void Manager::on_temperature(float celsius, Nanos now) {
    if (!cfg_.temperature || !std::isfinite(celsius) || celsius < -40.f || celsius > 125.f) return;
    constexpr Nanos kMinGap = 10'000'000'000;
    if (last_celsius_ && (std::fabs(celsius - *last_celsius_) < 1.f || now - last_celsius_at_ < kMinGap)) return;
    last_celsius_ = celsius;
    last_celsius_at_ = now;
    push(EventKind::Temperature, now).celsius = celsius;
}

void Manager::on_identity(const UsbIdentity& identity, Nanos now) {
    if (!cfg_.usb_identity) return;
    auto& e = push(EventKind::Identity, now);
    copy_clean(identity.product, e.text);
    e.msg_id = identity.bcd_device;
    e.value = identity.speed_mbps;
    if (identity.speed_mbps > 0 && identity.speed_mbps < 480) push(EventKind::LinkDegraded, now).value = identity.speed_mbps;
}

void Manager::discover(std::uint16_t id, std::span<const std::uint8_t> payload, Nanos now) {
    constexpr std::uint8_t kPerIdCap = 8;
    const auto hash = fnv1a(payload);
    Seen* entry = nullptr;
    for (std::size_t i = 0; i < seen_count_; ++i) {
        if (seen_[i].id == id) entry = &seen_[i];
    }
    if (!entry) {
        if (seen_count_ == kSeenSlots) return;
        entry = &seen_[seen_count_++];
        *entry = Seen{id, 0, hash ^ 1u};
    }
    if (entry->hash == hash || entry->emitted >= kPerIdCap) return;
    entry->hash = hash;
    ++entry->emitted;
    auto& e = push(EventKind::Discovered, now);
    e.msg_id = id;
    e.value = static_cast<std::int32_t>(payload.size());
    e.data_len = static_cast<std::uint8_t>(std::min(payload.size(), e.data.size()));
    std::copy_n(payload.begin(), e.data_len, e.data.begin());
}

RequestResult Manager::request_set(Request kind, int level, int max, const std::optional<int>& known) {
    if (!cfg_.control) return RequestResult::Disabled;
    if (level < 0 || level > max) return RequestResult::OutOfRange;
    if (!cfg_.map.routes[idx(kind)].send) return RequestResult::Unavailable;
    if (!connected_) return RequestResult::NotConnected;
    if (tripped_ || latched_) return RequestResult::Stopped;
    if (known == level) {
        pending_[idx(kind)] = false;
        return RequestResult::AlreadySet;
    }
    if (sent_this_connection_ >= cfg_.max_commands_per_connection) return RequestResult::BudgetExhausted;
    pending_[idx(kind)] = true;
    pending_value_[idx(kind)] = static_cast<std::uint8_t>(level);
    return RequestResult::Queued;
}

RequestResult Manager::request_brightness(int level) {
    return request_set(Request::SetBrightness, level, kBrightnessMax, brightness_);
}

RequestResult Manager::request_dimming(int level) {
    return request_set(Request::SetDimming, level, cfg_.map.film_levels, dimming_);
}

std::optional<OutRequest> Manager::next_request(Nanos now) {
    if (!connected_ || tripped_ || latched_ || inflight_) return std::nullopt;
    if (now - connected_at_ < to_ns(cfg_.settle)) return std::nullopt;
    if (ever_sent_ && now - last_sent_ < to_ns(cfg_.min_gap)) return std::nullopt;
    if (sent_this_connection_ >= cfg_.max_commands_per_connection) return std::nullopt;

    const auto found = std::find(pending_.begin(), pending_.end(), true);
    if (found == pending_.end()) return std::nullopt;
    const auto index = static_cast<std::size_t>(found - pending_.begin());
    const auto kind = static_cast<Request>(index);
    pending_[index] = false;

    OutRequest out;
    out.kind = kind;
    out.id = *cfg_.map.routes[index].send;
    if (kind == Request::SetBrightness || kind == Request::SetDimming) {
        out.payload_len = 1;
        out.payload[0] = pending_value_[index];
    }
    inflight_ = kind;
    inflight_value_ = pending_value_[index];
    awaiting_write_ = true;
    last_sent_ = now;
    ever_sent_ = true;
    ++sent_this_connection_;
    ++sent_total_;
    auto& e = push(EventKind::Sent, now);
    e.request = kind;
    e.msg_id = out.id;
    return out;
}

void Manager::on_write_result(bool ok, Nanos now) {
    if (!awaiting_write_ || !inflight_) return;
    awaiting_write_ = false;
    last_sent_ = now;  // the reply clock starts when the write finished
    if (!ok) {
        push(EventKind::TimedOut, now).request = *inflight_;
        stop_connection(now);
        return;
    }
    const auto kind = *inflight_;
    if (cfg_.map.routes[idx(kind)].reply) return;
    if (kind == Request::SetBrightness) brightness_ = inflight_value_;
    if (kind == Request::SetDimming) dimming_ = inflight_value_;
    inflight_.reset();
}

void StillWatch::update(Nanos t_ns, const std::array<float, 3>& gyro_rad_s, const std::array<float, 3>& accel_m_s2) {
    if (!started_) {
        started_ = true;
        block_start_ = t_ns;
        last_motion_.store(t_ns, std::memory_order_relaxed);
    }
    for (std::size_t i = 0; i < 3; ++i) {
        sum_[i] += gyro_rad_s[i];
        sum_[i + 3] += accel_m_s2[i];
    }
    ++count_;
    if (t_ns - block_start_ < kBlockNs) return;

    std::array<float, 6> mean{};
    for (std::size_t i = 0; i < 6; ++i) mean[i] = sum_[i] / static_cast<float>(count_);
    if (have_prev_) {
        bool moved = false;
        for (std::size_t i = 0; i < 6; ++i) {
            const float limit = i < 3 ? thr_.gyro_rad_s : thr_.accel_m_s2;
            // !(<=) so a NaN from a bad packet counts as motion: fail towards "worn".
            moved = moved || !(std::fabs(mean[i] - prev_[i]) <= limit);
        }
        if (moved) last_motion_.store(t_ns, std::memory_order_relaxed);
    }
    prev_ = mean;
    have_prev_ = true;
    sum_.fill(0.f);
    count_ = 0;
    block_start_ = t_ns;
}

namespace {
std::string read_line(const std::filesystem::path& file) {
    std::ifstream in(file);
    std::string line;
    std::getline(in, line);
    return line;
}

std::optional<std::uint16_t> read_hex16(const std::filesystem::path& file) {
    const auto text = read_line(file);
    unsigned value = 0;
    const auto [ptr, ec] = std::from_chars(text.data(), text.data() + text.size(), value, 16);
    if (ec != std::errc{} || ptr == text.data() || value > 0xFFFF) return std::nullopt;
    return static_cast<std::uint16_t>(value);
}
}  // namespace

std::optional<UsbIdentity> find_usb_identity(const std::filesystem::path& root, std::uint16_t vendor,
                                             std::uint16_t product) {
    std::error_code ec;
    for (const auto& entry : std::filesystem::directory_iterator(root, ec)) {
        const auto dir = entry.path();
        if (read_hex16(dir / "idVendor") != vendor || read_hex16(dir / "idProduct") != product) continue;
        UsbIdentity id;
        id.busid = dir.filename().string();
        id.vendor = vendor;
        id.product_id = product;
        id.product = read_line(dir / "product");
        id.bcd_device = read_hex16(dir / "bcdDevice").value_or(0);
        const auto speed = read_line(dir / "speed");
        long mbps = 0;
        if (parse_int(speed, mbps)) id.speed_mbps = static_cast<int>(mbps);
        return id;
    }
    return std::nullopt;
}

EventSocket::~EventSocket() {
    if (fd_ >= 0) ::close(fd_);
}

bool EventSocket::open(const std::filesystem::path& path) {
    if (fd_ >= 0) ::close(fd_);
    fd_ = -1;
    path_ = path.string();
    if (path_.empty() || path_.size() >= sizeof(sockaddr_un::sun_path)) return false;
    fd_ = ::socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    return fd_ >= 0;
}

bool EventSocket::send(std::string_view line) {
    if (fd_ < 0) {
        ++dropped_;
        return false;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::memcpy(addr.sun_path, path_.c_str(), path_.size() + 1);
    const auto sent = ::sendto(fd_, line.data(), line.size(), MSG_DONTWAIT | MSG_NOSIGNAL,
                               reinterpret_cast<const sockaddr*>(&addr), sizeof addr);
    if (sent != static_cast<ssize_t>(line.size())) {
        ++dropped_;
        return false;
    }
    return true;
}

std::filesystem::path default_event_socket_path() {
    const char* dir = std::getenv("XDG_RUNTIME_DIR");
    if (!dir || !*dir) return {};
    return std::filesystem::path(dir) / "xr-luma" / "events.sock";
}

}  // namespace luma::features
