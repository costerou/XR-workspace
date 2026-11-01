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
Error invalid() { return {"invalid_control", "Rejected malformed or unsupported control message"}; }
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
        else return invalid();
        ControlPayload payload;
        if (op == ControlOp::select) {
            if (j["payload"].size() != 1 || !j["payload"].contains("output_id") || !token(j["payload"]["output_id"])) return invalid();
            payload = SelectPayload{j["payload"]["output_id"].get<std::string>()};
        } else if (op == ControlOp::view) {
            const auto& p = j["payload"];
            if (p.size() != 4 || !p.contains("zoomed") || !p.contains("selected") || !p.contains("spacing") || !p.contains("mode") ||
                !p["zoomed"].is_boolean() || !p["selected"].is_number_unsigned() || !p["spacing"].is_number_unsigned() || !p["mode"].is_string() ||
                p["selected"].get<std::uint64_t>() > 2 || p["spacing"].get<std::uint64_t>() > 400) return invalid();
            const auto mode = p["mode"].get<std::string>();
            std::uint8_t m = 0;
            if (mode == "anchor") m = 0; else if (mode == "locked") m = 1; else if (mode == "follow") m = 2; else return invalid();
            payload = ViewPayload{p["zoomed"].get<bool>(), p["selected"].get<std::uint32_t>(), p["spacing"].get<std::uint32_t>(), m};
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
