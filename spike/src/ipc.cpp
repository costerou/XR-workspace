#include "xr/ipc.hpp"
#include "video_ipc.hpp"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <set>
#include <stdexcept>
#include <vector>

namespace xr {
namespace {
using Json = nlohmann::json;
bool token(const Json& value) {
    if (!value.is_string()) return false;
    const auto& s = value.get_ref<const std::string&>();
    const auto alnum = [](unsigned char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'); };
    if (s.empty() || s.size() > 128 || !alnum(static_cast<unsigned char>(s[0]))) return false;
    return std::all_of(s.begin(), s.end(), [&](unsigned char c) { return alnum(c) || c == '_' || c == '.' || c == ':' || c == '-'; });
}
bool help_text(const Json& value) {
    if (!value.is_string()) return false;
    const auto& s = value.get_ref<const std::string&>();
    return !s.empty() && s.size() <= kMaxHelpText && std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 0x20 && c < 0x7f; });
}
Error invalid() { return {"invalid_control", "Rejected malformed or unsupported control message"}; }
struct Bound { const char* key; std::int32_t lo, hi; };
// Same order and values as tools/native_spike/geometry.py PLACEMENT_BOUNDS (tests/native_spike/test_placement.py checks).
constexpr Bound kPlacementBounds[] = {
    {"x", -1800, 1800}, {"y", -900, 900}, {"distance", 50, 200}, {"curvature", 0, 120}};
bool placement(const Json& value, WirePlacement& out) {
    if (!value.is_object() || value.size() != std::size(kPlacementBounds)) return false;
    std::int32_t* fields[] = {&out.x, &out.y, &out.distance, &out.curvature};
    static_assert(std::size(fields) == std::size(kPlacementBounds));
    for (std::size_t i = 0; i < std::size(kPlacementBounds); ++i) {
        const auto& b = kPlacementBounds[i];
        if (!value.contains(b.key) || !value[b.key].is_number_integer()) return false;
        const auto& n = value[b.key];
        // Unsigned first: huge unsigned values must not wrap into range as int64.
        if (n.is_number_unsigned() ? n.get<std::uint64_t>() > std::uint64_t(std::max(b.hi, 0)) : false) return false;
        const auto v = n.get<std::int64_t>();
        if (v < b.lo || v > b.hi) return false;
        *fields[i] = std::int32_t(v);
    }
    return true;
}
bool overview_pointer(const Json& p, OverviewPointerPayload& out) {
    const bool directed = p.is_object() && (p.contains("az") || p.contains("el"));
    if (!p.is_object() || p.size() != (directed ? 6u : 4u)) return false;
    for (const char* key : {"visible", "held"})
        if (!p.contains(key) || !p[key].is_boolean()) return false;
    std::int32_t xy[2];
    for (int i = 0; i < 2; ++i) {
        const char* key = i ? "y" : "x";
        if (!p.contains(key) || !p[key].is_number_integer()) return false;
        const auto& n = p[key];
        if (n.is_number_unsigned() ? n.get<std::uint64_t>() > kMaxPointerPx : false) return false;  // never wrap into range
        const auto v = n.get<std::int64_t>();
        if (v < -std::int64_t(kMaxPointerPx) || v > std::int64_t(kMaxPointerPx)) return false;
        xy[i] = std::int32_t(v);
    }
    std::int32_t dir[2] = {0, 0};
    if (directed) {
        for (int i = 0; i < 2; ++i) {
            const char* key = i ? "el" : "az";
            const std::int64_t limit = i ? kMaxPointerEl : kMaxPointerAz;
            if (!p.contains(key) || !p[key].is_number_integer()) return false;
            const auto& n = p[key];
            if (n.is_number_unsigned() && n.get<std::uint64_t>() > std::uint64_t(limit)) return false;
            const auto v = n.get<std::int64_t>();
            if (v < -limit || v > limit) return false;
            dir[i] = std::int32_t(v);
        }
    }
    out = {p["visible"].get<bool>(), xy[0], xy[1], p["held"].get<bool>(), directed, dir[0], dir[1]};
    return true;
}
bool placement_menu(const Json& p, PlacementMenuPayload& out) {
    // "surface" (optional, open panels only): the placement the open panel is drawn on while a slider is held.
    const bool surface = p.is_object() && p.contains("surface");
    if (!p.is_object() || p.size() != (surface ? 5u : 4u) || !p.contains("open") || !p["open"].is_boolean() || !p.contains("desktop") ||
        !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() >= kPlacementMenuDesktops || !p.contains("field") ||
        !p["field"].is_string() || !p.contains("placements") || !p["placements"].is_array() || p["placements"].size() != kPlacementMenuDesktops)
        return false;
    const auto& field = p["field"].get_ref<const std::string&>();
    const auto* named = std::find_if(std::begin(kPlacementBounds), std::end(kPlacementBounds), [&](const Bound& b) { return field == b.key; });
    if (named == std::end(kPlacementBounds)) return false;
    out.open = p["open"].get<bool>();
    out.desktop = p["desktop"].get<std::uint32_t>();
    out.field = std::uint32_t(named - std::begin(kPlacementBounds));
    for (std::size_t i = 0; i < kPlacementMenuDesktops; ++i)
        if (!placement(p["placements"][i], out.placements[i])) return false;
    out.surface.reset();
    if (surface) {
        if (!out.open) return false;
        WirePlacement held;
        if (!placement(p["surface"], held)) return false;
        out.surface = held;
    }
    return true;
}
constexpr const char* kHandleCorners[] = {"tl", "tr", "bl", "br"};
bool resize_handle(const Json& p, ResizeHandlePayload& out) {
    if (!p.is_object() || p.size() != 4 || !p.contains("visible") || !p["visible"].is_boolean() || !p.contains("active") ||
        !p["active"].is_boolean() || !p.contains("desktop") || !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() > 2 ||
        !p.contains("corner") || !p["corner"].is_string())
        return false;
    const auto& corner = p["corner"].get_ref<const std::string&>();
    const auto* named = std::find(std::begin(kHandleCorners), std::end(kHandleCorners), corner);
    if (named == std::end(kHandleCorners)) return false;
    out = {p["visible"].get<bool>(), p["desktop"].get<std::uint32_t>(), std::uint32_t(named - std::begin(kHandleCorners)), p["active"].get<bool>()};
    return true;
}
bool move_handle(const Json& p, MoveHandlePayload& out) {
    if (!p.is_object() || p.size() != 4) return false;
    for (const char* key : {"visible", "hover", "active"})
        if (!p.contains(key) || !p[key].is_boolean()) return false;
    if (!p.contains("desktop") || !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() > 2) return false;
    out = {p["visible"].get<bool>(), p["desktop"].get<std::uint32_t>(), p["hover"].get<bool>(), p["active"].get<bool>()};
    return true;
}
bool roll(const Json& p, RollPayload& out) {
    if (!p.is_object() || p.size() != 4 || !p.contains("roll") || !p["roll"].is_array() || p["roll"].size() != 3 || !p.contains("icons") ||
        !p["icons"].is_array() || p["icons"].size() != 3 || !p.contains("hover") || !p["hover"].is_number_integer() ||
        !p.contains("active") || !p["active"].is_boolean())
        return false;
    for (std::size_t i = 0; i < 3; ++i) {
        const auto& n = p["roll"][i];
        if (!n.is_number_integer() || (n.is_number_unsigned() && n.get<std::uint64_t>() > std::uint64_t(kMaxRoll))) return false;
        const auto v = n.get<std::int64_t>();
        if (v < -kMaxRoll || v > kMaxRoll) return false;
        out.roll[i] = std::int32_t(v);
        const auto& icon = p["icons"][i];
        if (!icon.is_string()) return false;
        const auto& name = icon.get_ref<const std::string&>();
        if (name == "none") out.icons[i] = -1;
        else if (name == "ccw") out.icons[i] = 0;
        else if (name == "cw") out.icons[i] = 1;
        else return false;
    }
    const auto& h = p["hover"];
    if (h.is_number_unsigned() ? h.get<std::uint64_t>() > 2 : (h.get<std::int64_t>() < -1 || h.get<std::int64_t>() > 2)) return false;
    out.hover = std::int32_t(h.get<std::int64_t>());
    out.active = p["active"].get<bool>();
    return true;
}
}
Result<ControlMessage> decode_control(std::string_view frame) {
    if (frame.empty() || frame.size() > 16384 || frame.back() != '\n' || frame.substr(0, frame.size()-1).find('\n') != frame.npos) return invalid();
    try {
        std::vector<std::set<std::string>> keys;
        auto callback = [&](int depth, Json::parse_event_t event, Json& parsed) {
            if (depth > 32) throw std::invalid_argument("nesting limit");
            if (event == Json::parse_event_t::object_start) keys.emplace_back();
            if (event == Json::parse_event_t::key && !keys.back().insert(parsed.get<std::string>()).second)
                throw std::invalid_argument("duplicate key");
            if (event == Json::parse_event_t::object_end) keys.pop_back();
            return true;
        };
        auto j = Json::parse(frame, callback);
        if (!j.is_object() || j.size() != 6) return invalid();
        for (auto field : {"schema", "request_id", "run_id", "session", "op", "payload"})
            if (!j.contains(field)) return invalid();
        if (!j["schema"].is_number_unsigned() || j["schema"] != 1 || !j["request_id"].is_number_unsigned() || j["request_id"].get<std::uint64_t>() == 0) return invalid();
        if (!token(j["run_id"]) || !token(j["session"]) || !j["op"].is_string() || !j["payload"].is_object()) return invalid();
        ControlOp op;
        const auto name = j["op"].get<std::string>();
        if (name == "status") op = ControlOp::status;
        else if (name == "video") op = ControlOp::video;
        else if (name == "start") op = ControlOp::start;
        else if (name == "stop") op = ControlOp::stop;
        else if (name == "select") op = ControlOp::select;
        else if (name == "recover") op = ControlOp::recover;
        else if (name == "view") op = ControlOp::view;
        else if (name == "recenter") op = ControlOp::recenter;
        else if (name == "style") op = ControlOp::style;
        else if (name == "help") op = ControlOp::help;
        else if (name == "placement") op = ControlOp::placement;
        else if (name == "placement_menu") op = ControlOp::placement_menu;
        else if (name == "resize_handle") op = ControlOp::resize_handle;
        else if (name == "move_handle") op = ControlOp::move_handle;
        else if (name == "overview_pointer") op = ControlOp::overview_pointer;
        else if (name == "roll") op = ControlOp::roll;
        else return invalid();
        ControlPayload payload;
        if (op == ControlOp::video) {
            VideoPayload video;
            if (!decode_video_payload(j["payload"], video)) return invalid();
            payload = std::move(video);
        } else if (op == ControlOp::select) {
            if (j["payload"].size() != 1 || !j["payload"].contains("output_id") || !token(j["payload"]["output_id"])) return invalid();
            payload = SelectPayload{j["payload"]["output_id"].get<std::string>()};
        } else if (op == ControlOp::view) {
            const auto& p = j["payload"];
            if (p.size() != 7 || !p.contains("typing") || !p["typing"].is_boolean() ||
                !p.contains("prediction") || !p["prediction"].is_boolean() || !p.contains("zoomed") || !p.contains("selected") || !p.contains("spacing") || !p.contains("mode") ||
                !p.contains("cursor_follow") || !p["cursor_follow"].is_boolean() || !p["zoomed"].is_boolean() || !p["selected"].is_number_unsigned() || !p["spacing"].is_number_unsigned() || !p["mode"].is_string() ||
                p["selected"].get<std::uint64_t>() > 2 || p["spacing"].get<std::uint64_t>() > 400) return invalid();
            const auto mode = p["mode"].get<std::string>();
            std::uint8_t m = 0;
            if (mode == "anchor") m = 0; else if (mode == "locked") m = 1; else if (mode == "follow") m = 2; else return invalid();
            payload = ViewPayload{p["zoomed"].get<bool>(), p["selected"].get<std::uint32_t>(), p["spacing"].get<std::uint32_t>(), m,
                                  p["cursor_follow"].get<bool>(), p["prediction"].get<bool>(), p["typing"].get<bool>()};
        } else if (op == ControlOp::style) {
            const auto& p = j["payload"];
            if (p.size() != 1 || !p.contains("border") || !p["border"].is_array() || p["border"].size() != 3) return invalid();
            std::uint8_t c[3];
            for (int i = 0; i < 3; ++i) {
                const auto& v = p["border"][std::size_t(i)];
                if (!v.is_number_unsigned() || v.get<std::uint64_t>() > 255) return invalid();
                c[i] = std::uint8_t(v.get<std::uint64_t>());
            }
            payload = StylePayload{c[0], c[1], c[2]};
        } else if (op == ControlOp::help) {
            const auto& p = j["payload"];
            if (!p.contains("visible") || !p["visible"].is_boolean() || !p.contains("rows") || !p["rows"].is_array() ||
                p["rows"].size() > kMaxHelpRows || p.size() != std::size_t(2 + p.contains("sections") + p.contains("turn"))) return invalid();
            HelpPayload help{p["visible"].get<bool>(), {}, {}, 0};
            for (const auto& row : p["rows"]) {
                if (!row.is_array() || row.size() != 2 || !help_text(row[0]) || !help_text(row[1])) return invalid();
                help.rows.emplace_back(row[0].get<std::string>(), row[1].get<std::string>());
            }
            if (p.contains("sections")) {
                const auto& sections = p["sections"];
                if (!sections.is_array() || sections.size() > kMaxHelpRows) return invalid();
                std::size_t total = 0;
                for (const auto& n : sections) {
                    if (!n.is_number_unsigned() || n.get<std::uint64_t>() < 1 || n.get<std::uint64_t>() > kMaxHelpRows) return invalid();
                    help.sections.push_back(std::uint32_t(n.get<std::uint64_t>()));
                    total += help.sections.back();
                }
                if (total != help.rows.size()) return invalid();
            }
            if (p.contains("turn")) {
                const auto& t = p["turn"];
                if (!t.is_number_integer() || (t.is_number_unsigned() ? t.get<std::uint64_t>() != 1 : t.get<std::int64_t>() != -1))
                    return invalid();
                help.turn = std::int32_t(t.get<std::int64_t>());
            }
            payload = std::move(help);
        } else if (op == ControlOp::placement) {
            const auto& p = j["payload"];
            if (p.size() != 1 || !p.contains("desktops") || !p["desktops"].is_array() || p["desktops"].empty() || p["desktops"].size() > 3)
                return invalid();
            PlacementPayload placements;
            for (const auto& d : p["desktops"]) {
                WirePlacement w;
                if (!placement(d, w)) return invalid();
                placements.desktops.push_back(w);
            }
            payload = std::move(placements);
        } else if (op == ControlOp::placement_menu) {
            PlacementMenuPayload menu;
            if (!placement_menu(j["payload"], menu)) return Error{kInvalidMenuCode, "Ignored malformed placement_menu payload"};
            payload = menu;
        } else if (op == ControlOp::resize_handle) {
            ResizeHandlePayload handle;
            if (!resize_handle(j["payload"], handle)) return Error{kInvalidHandleCode, "Ignored malformed resize_handle payload"};
            payload = handle;
        } else if (op == ControlOp::move_handle) {
            MoveHandlePayload pill;
            if (!move_handle(j["payload"], pill)) return Error{kInvalidMoveHandleCode, "Ignored malformed move_handle payload"};
            payload = pill;
        } else if (op == ControlOp::overview_pointer) {
            OverviewPointerPayload pointer;
            if (!overview_pointer(j["payload"], pointer)) return Error{kInvalidOverviewPointerCode, "Ignored malformed overview_pointer payload"};
            payload = pointer;
        } else if (op == ControlOp::roll) {
            RollPayload r;
            if (!roll(j["payload"], r)) return Error{kInvalidRollCode, "Ignored malformed roll payload"};
            payload = r;
        } else if (!j["payload"].empty()) return invalid();
        return ControlMessage{1, j["request_id"].get<std::uint64_t>(), j["run_id"].get<std::string>(), j["session"].get<std::string>(), op, payload};
    } catch (const std::exception&) { return invalid(); }
}
ControlQueue::ControlQueue(std::string run_id, std::string session)
    : run_id_(std::move(run_id)), session_(std::move(session)) {
    if (!token(Json(run_id_)) || !token(Json(session_))) throw std::invalid_argument("invalid identity");
}
Status ControlQueue::push(std::string_view frame) {
    auto decoded = decode_control(frame);
    if (const auto* error = std::get_if<Error>(&decoded)) return *error;
    auto message = std::get<ControlMessage>(std::move(decoded));
    if (message.run_id != run_id_ || message.session != session_ || message.request_id <= last_) return invalid();
    if (messages_.size() >= 8) return Error{"queue_full", "Control queue capacity is eight"};
    last_ = message.request_id;
    messages_.push_back(std::move(message));
    return {};
}
std::optional<ControlMessage> ControlQueue::pop() {
    if (messages_.empty()) return std::nullopt;
    auto message = std::move(messages_.front()); messages_.pop_front(); return message;
}
} // namespace xr
