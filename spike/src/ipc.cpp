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
    {"x", -1800, 1800}, {"y", -900, 900}, {"distance", 10, 200}, {"curvature", 0, 120}};
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
    const bool drag = p.is_object() && p.contains("drag");
    if (!p.is_object() || p.size() != (directed ? 6u : 4u) + (drag ? 1u : 0u)) return false;
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
    std::int32_t ghost[3] = {0, 0, -1};  // w, h, target
    if (drag) {
        const auto& d = p["drag"];
        if (!d.is_object() || d.size() != 3u) return false;
        const char* keys[] = {"w", "h", "target"};
        const std::int64_t lo[] = {1, 1, -1}, hi[] = {kMaxGhostPx, kMaxGhostPx, std::int64_t(kMaxDesktops) - 1};
        for (int i = 0; i < 3; ++i) {
            if (!d.contains(keys[i]) || !d[keys[i]].is_number_integer()) return false;
            const auto& n = d[keys[i]];
            if (n.is_number_unsigned() && n.get<std::uint64_t>() > std::uint64_t(hi[i])) return false;
            const auto v = n.get<std::int64_t>();
            if (v < lo[i] || v > hi[i]) return false;
            ghost[i] = std::int32_t(v);
        }
    }
    out = {p["visible"].get<bool>(), xy[0], xy[1], p["held"].get<bool>(), directed, dir[0], dir[1], drag, ghost[0], ghost[1], ghost[2]};
    return true;
}
bool placement_menu(const Json& p, PlacementMenuPayload& out) {
    // "surface" (optional, open panels only): the placement the open panel is drawn on while a slider is held. "names",
    // "wide", "armed", "busy" (optional): the panel's desktop row. "scale", "scales", "picker" (optional): its Scale
    // dropdown (the list open only on an open panel).
    if (!p.is_object()) return false;
    std::size_t optional = 0;
    for (const char* key : {"surface", "names", "wide", "armed", "busy", "scale", "scales", "picker"}) optional += p.contains(key);
    if (p.size() != 4 + optional || !p.contains("open") || !p["open"].is_boolean() || !p.contains("placements") ||
        !p["placements"].is_array() || p["placements"].empty() || p["placements"].size() > kPlacementMenuDesktops ||
        !p.contains("desktop") || !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() >= p["placements"].size() ||
        !p.contains("field") || !p["field"].is_string())
        return false;
    const auto& field = p["field"].get_ref<const std::string&>();
    const auto* named = std::find_if(std::begin(kPlacementBounds), std::end(kPlacementBounds), [&](const Bound& b) { return field == b.key; });
    if (named == std::end(kPlacementBounds)) return false;
    out = {};
    out.open = p["open"].get<bool>();
    out.desktop = p["desktop"].get<std::uint32_t>();
    out.field = std::uint32_t(named - std::begin(kPlacementBounds));
    out.count = std::uint32_t(p["placements"].size());
    for (std::size_t i = 0; i < out.count; ++i)
        if (!placement(p["placements"][i], out.placements[i])) return false;
    for (std::size_t i = 0; i < kPlacementMenuDesktops; ++i) out.numbers[i] = std::uint8_t(i + 1);
    if (p.contains("names")) {  // increasing output numbers, one per desktop
        const auto& names = p["names"];
        if (!names.is_array() || names.size() != out.count) return false;
        std::uint64_t last = 0;
        for (std::size_t i = 0; i < out.count; ++i) {
            if (!names[i].is_number_unsigned()) return false;
            const auto n = names[i].get<std::uint64_t>();
            if (n <= last || n > kPlacementMenuDesktops) return false;
            out.numbers[i] = std::uint8_t(n); last = n;
        }
    }
    for (auto [key, flag] : {std::pair{"wide", &out.wide}, std::pair{"armed", &out.armed}, std::pair{"busy", &out.busy},
                             std::pair{"picker", &out.picker}})
        if (p.contains(key)) {
            if (!p[key].is_boolean()) return false;
            *flag = p[key].get<bool>();
        }
    if (out.picker && !out.open) return false;
    auto step = [](const Json& v) -> int {  // index into kScalePercents, -1 if v is not one of them
        if (!v.is_number_unsigned()) return -1;
        const auto n = v.get<std::uint64_t>();
        const auto* at = std::find(kScalePercents.begin(), kScalePercents.end(), n);
        return at == kScalePercents.end() ? -1 : int(at - kScalePercents.begin());
    };
    if (p.contains("scale")) {
        const int i = step(p["scale"]);
        if (i < 0) return false;
        out.scale = kScalePercents[std::size_t(i)];
    }
    if (p.contains("scales")) {  // increasing steps
        const auto& scales = p["scales"];
        if (!scales.is_array() || scales.size() > kScaleSteps) return false;
        int last = -1;
        for (const auto& v : scales) {
            const int i = step(v);
            if (i <= last) return false;
            out.scales = std::uint8_t(out.scales | (1u << unsigned(i))); last = i;
        }
    }
    out.surface.reset();
    if (p.contains("surface")) {
        if (!out.open) return false;
        WirePlacement held;
        if (!placement(p["surface"], held)) return false;
        out.surface = held;
    }
    return true;
}
bool desktop_names(const Json& value, std::vector<std::string>& out, std::size_t min) {
    if (!value.is_array() || value.size() < min || value.size() > kMaxDesktops) return false;
    for (const auto& n : value) {
        if (!n.is_string()) return false;
        const auto& name = n.get_ref<const std::string&>();
        // XR-1..XR-kMaxDesktops (capture.cpp parse_capture_list), each at most once.
        if (name.size() != 4 || name.compare(0, 3, "XR-") != 0 || name[3] < '1' || name[3] > char('0' + kMaxDesktops) ||
            std::find(out.begin(), out.end(), name) != out.end()) return false;
        out.push_back(name);
    }
    return true;
}
bool desktops(const Json& p, DesktopsPayload& out) {
    if (!p.is_object() || p.size() != 1u + p.contains("renew") || !p.contains("outputs")) return false;
    if (!desktop_names(p["outputs"], out.outputs, 1)) return false;
    if (p.contains("renew")) {
        if (!desktop_names(p["renew"], out.renew, 0)) return false;
        for (const auto& name : out.renew)
            if (std::find(out.outputs.begin(), out.outputs.end(), name) == out.outputs.end()) return false;
    }
    std::sort(out.outputs.begin(), out.outputs.end());
    return true;
}
constexpr const char* kHandleCorners[] = {"tl", "tr", "bl", "br"};
bool resize_handle(const Json& p, ResizeHandlePayload& out) {
    if (!p.is_object() || p.size() != 4 || !p.contains("visible") || !p["visible"].is_boolean() || !p.contains("active") ||
        !p["active"].is_boolean() || !p.contains("desktop") || !p["desktop"].is_number_unsigned() ||
        !p.contains("corner") || !p["corner"].is_string() || p["desktop"].get<std::uint64_t>() >= kMaxDesktops)
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
    if (!p.contains("desktop") || !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() >= kMaxDesktops) return false;
    out = {p["visible"].get<bool>(), p["desktop"].get<std::uint32_t>(), p["hover"].get<bool>(), p["active"].get<bool>()};
    return true;
}
bool roll(const Json& p, RollPayload& out) {
    if (!p.is_object() || p.size() != 5 || !p.contains("roll") || !p["roll"].is_array() || p["roll"].size() != kMaxDesktops || !p.contains("icons") ||
        !p["icons"].is_array() || p["icons"].size() != kMaxDesktops || !p.contains("hover") || !p["hover"].is_number_integer() ||
        !p.contains("side") || !p["side"].is_string() || !p.contains("active") || !p["active"].is_boolean())
        return false;
    for (std::size_t i = 0; i < kMaxDesktops; ++i) {
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
        else if (name == "both") out.icons[i] = 2;
        else return false;
    }
    const auto& h = p["hover"];
    const auto top = std::int64_t(kMaxDesktops) - 1;
    if (h.is_number_unsigned() ? h.get<std::uint64_t>() > std::uint64_t(top) : (h.get<std::int64_t>() < -1 || h.get<std::int64_t>() > top)) return false;
    out.hover = std::int32_t(h.get<std::int64_t>());
    const auto& side = p["side"].get_ref<const std::string&>();
    if (side == "none") out.side = -1;
    else if (side == "ccw") out.side = 0;
    else if (side == "cw") out.side = 1;
    else return false;
    if ((out.hover < 0) != (out.side < 0)) return false;
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
        else if (name == "desktops") op = ControlOp::desktops;
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
            const bool has_center = p.contains("center"), has_cut = p.contains("cut");
            if (p.size() != 7u + has_center + has_cut || (has_center && !p["center"].is_boolean()) ||
                (has_cut && !p["cut"].is_boolean()) || !p.contains("typing") || !p["typing"].is_boolean() ||
                !p.contains("prediction") || !p["prediction"].is_boolean() || !p.contains("zoomed") || !p.contains("selected") || !p.contains("spacing") || !p.contains("mode") ||
                !p.contains("cursor_follow") || !p["cursor_follow"].is_boolean() || !p["zoomed"].is_boolean() || !p["selected"].is_number_unsigned() || !p["spacing"].is_number_unsigned() || !p["mode"].is_string() ||
                p["selected"].get<std::uint64_t>() >= kMaxDesktops || p["spacing"].get<std::uint64_t>() > 400) return invalid();
            const auto mode = p["mode"].get<std::string>();
            std::uint8_t m = 0;
            if (mode == "anchor") m = 0; else if (mode == "locked") m = 1; else if (mode == "follow") m = 2; else return invalid();
            payload = ViewPayload{p["zoomed"].get<bool>(), p["selected"].get<std::uint32_t>(), p["spacing"].get<std::uint32_t>(), m,
                                  p["cursor_follow"].get<bool>(), p["prediction"].get<bool>(), p["typing"].get<bool>(),
                                  has_center && p["center"].get<bool>(), has_cut && p["cut"].get<bool>()};
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
            if (p.size() != 1 || !p.contains("desktops") || !p["desktops"].is_array() || p["desktops"].empty() || p["desktops"].size() > kMaxDesktops)
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
        } else if (op == ControlOp::desktops) {
            DesktopsPayload set;
            if (!desktops(j["payload"], set)) return invalid();
            payload = std::move(set);
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
