#include "xr/ipc.hpp"
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
    {"x", -100, 100}, {"y", -100, 100}, {"distance", 50, 200}, {"yaw", -60, 60}, {"pitch", -45, 45}, {"roll", -45, 45}, {"curvature", 0, 120}};
bool placement(const Json& value, WirePlacement& out) {
    if (!value.is_object() || value.size() != std::size(kPlacementBounds)) return false;
    std::int32_t* fields[] = {&out.x, &out.y, &out.distance, &out.yaw, &out.pitch, &out.roll, &out.curvature};
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
bool menu_pointer(const Json& p, PlacementMenuPayload& out) {
    if (!p.is_object() || p.size() != 3 || !p.contains("row") || !p["row"].is_number_integer() || !p.contains("x") ||
        !p["x"].is_number_unsigned() || !p.contains("drag") || !p["drag"].is_boolean()) return false;
    // Unsigned first: huge unsigned values must not wrap into range as int64.
    if (p["row"].is_number_unsigned() && p["row"].get<std::uint64_t>() >= std::size(kPlacementBounds)) return false;
    const auto row = p["row"].get<std::int64_t>();
    if (row < -1 || row >= std::int64_t(std::size(kPlacementBounds)) || p["x"].get<std::uint64_t>() > 1000) return false;
    out.pointer = true; out.pointer_row = std::int32_t(row); out.pointer_x = p["x"].get<std::uint32_t>(); out.pointer_drag = p["drag"].get<bool>();
    return true;
}
bool placement_menu(const Json& p, PlacementMenuPayload& out) {
    const bool pointer = p.is_object() && p.size() == 5 && p.contains("pointer");
    if (!p.is_object() || p.size() != (pointer ? 5u : 4u) || !p.contains("open") || !p["open"].is_boolean() || !p.contains("desktop") ||
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
    // Mouse mode only on an open menu (tools/native_spike/view.py placement_menu_frame checks the same).
    if (pointer && (!out.open || !menu_pointer(p["pointer"], out))) return false;
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
        else return invalid();
        ControlPayload payload;
        if (op == ControlOp::select) {
            if (j["payload"].size() != 1 || !j["payload"].contains("output_id") || !token(j["payload"]["output_id"])) return invalid();
            payload = SelectPayload{j["payload"]["output_id"].get<std::string>()};
        } else if (op == ControlOp::view) {
            const auto& p = j["payload"];
            if (p.size() != 6 || !p.contains("prediction") || !p["prediction"].is_boolean() || !p.contains("zoomed") || !p.contains("selected") || !p.contains("spacing") || !p.contains("mode") ||
                !p.contains("cursor_follow") || !p["cursor_follow"].is_boolean() || !p["zoomed"].is_boolean() || !p["selected"].is_number_unsigned() || !p["spacing"].is_number_unsigned() || !p["mode"].is_string() ||
                p["selected"].get<std::uint64_t>() > 2 || p["spacing"].get<std::uint64_t>() > 400) return invalid();
            const auto mode = p["mode"].get<std::string>();
            std::uint8_t m = 0;
            if (mode == "anchor") m = 0; else if (mode == "locked") m = 1; else if (mode == "follow") m = 2; else return invalid();
            payload = ViewPayload{p["zoomed"].get<bool>(), p["selected"].get<std::uint32_t>(), p["spacing"].get<std::uint32_t>(), m,
                                  p["cursor_follow"].get<bool>(), p["prediction"].get<bool>()};
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
            if (p.size() != 2 || !p.contains("visible") || !p["visible"].is_boolean() || !p.contains("rows") || !p["rows"].is_array() ||
                p["rows"].size() > kMaxHelpRows) return invalid();
            HelpPayload help{p["visible"].get<bool>(), {}};
            for (const auto& row : p["rows"]) {
                if (!row.is_array() || row.size() != 2 || !help_text(row[0]) || !help_text(row[1])) return invalid();
                help.rows.emplace_back(row[0].get<std::string>(), row[1].get<std::string>());
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
