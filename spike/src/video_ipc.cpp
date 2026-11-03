#include "video_ipc.hpp"
#include <algorithm>

namespace xr {
namespace {
using Json = nlohmann::json;
bool exact_keys(const Json& p, std::initializer_list<const char*> keys) {
    if (!p.is_object() || p.size() != keys.size()) return false;
    return std::all_of(keys.begin(), keys.end(), [&](const char* k) { return p.contains(k); });
}
bool format(const Json& p, VideoFormat& out) {
    if (!p["projection"].is_string() || !p["stereo"].is_string() || !p["fov"].is_number_unsigned()) return false;
    const auto& projection = p["projection"].get_ref<const std::string&>();
    const auto& stereo = p["stereo"].get_ref<const std::string&>();
    const auto fov = p["fov"].get<std::uint64_t>();
    if (projection == "360") out.projection = VideoProjection::equirect360;
    else if (projection == "180") out.projection = VideoProjection::equirect180;
    else if (projection == "fisheye") out.projection = VideoProjection::fisheye;
    else return false;
    if (stereo == "mono") out.stereo = VideoStereo::mono;
    else if (stereo == "sbs") out.stereo = VideoStereo::sbs;
    else if (stereo == "tb") out.stereo = VideoStereo::tb;
    else return false;
    if (fov < kFisheyeFovMinDeg || fov > kFisheyeFovMaxDeg) return false;
    out.fov_deg = std::uint32_t(fov);
    return true;
}
bool menu_text(const Json& value, std::string& out) {
    if (!value.is_string()) return false;
    const auto& s = value.get_ref<const std::string&>();
    if (s.empty() || s.size() > kMaxQualityText || !std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 0x20 && c < 0x7f; })) return false;
    out = s;
    return true;
}
bool title(const Json& value, std::string& out) {
    if (!value.is_string()) return false;
    const auto& s = value.get_ref<const std::string&>();
    if (s.empty() || s.size() > kMaxHelpText || !std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 0x20 && c < 0x7f; })) return false;
    out = s;
    return true;
}
}

bool decode_video_payload(const Json& p, VideoPayload& out) {
    if (!p.is_object() || !p.contains("action") || !p["action"].is_string()) return false;
    const auto& action = p["action"].get_ref<const std::string&>();
    if (action == "open") {
        out.action = VideoAction::open;
        return exact_keys(p, {"action", "projection", "stereo", "fov", "title"}) && format(p, out.format) && title(p["title"], out.title);
    }
    if (action == "format") { out.action = VideoAction::format; return exact_keys(p, {"action", "projection", "stereo", "fov"}) && format(p, out.format); }
    if (action == "close") { out.action = VideoAction::close; return exact_keys(p, {"action"}); }
    if (action == "pause") { out.action = VideoAction::pause; return exact_keys(p, {"action"}); }
    if (action == "controls") {
        out.action = VideoAction::controls;
        if (!exact_keys(p, {"action", "visible"}) || !p["visible"].is_boolean()) return false;
        out.visible = p["visible"].get<bool>();
        return true;
    }
    if (action == "quality") {  // tools/native_spike/quality.py renderer_payload
        out.action = VideoAction::quality;
        if (!exact_keys(p, {"action", "filter", "deband"}) || !p["filter"].is_string() || !p["deband"].is_boolean()) return false;
        const auto& filter = p["filter"].get_ref<const std::string&>();
        if (filter == "catmull-rom") out.quality.filter = VideoFilter::catmull_rom;
        else if (filter == "sharp") out.quality.filter = VideoFilter::sharp;
        else if (filter == "bilinear" || filter == "linear") out.quality.filter = VideoFilter::bilinear;  // "linear": the first menu's name
        else if (filter == "nearest") out.quality.filter = VideoFilter::nearest;
        else return false;
        out.quality.deband = p["deband"].get<bool>();
        return true;
    }
    if (action == "menu") {  // quality.py menu_payload
        out.action = VideoAction::menu;
        if (!exact_keys(p, {"action", "visible", "selected", "rows"}) || !p["visible"].is_boolean() || !p["rows"].is_array() ||
            !p["selected"].is_number_unsigned())
            return false;
        const auto& rows = p["rows"];
        if (rows.empty() || rows.size() > kMaxQualityRows || p["selected"].get<std::uint64_t>() >= rows.size()) return false;
        out.visible = p["visible"].get<bool>();
        out.selected = std::uint32_t(p["selected"].get<std::uint64_t>());
        out.rows.clear();
        for (const auto& r : rows) {
            VideoMenuRow row;
            if (!exact_keys(r, {"label", "value"}) || !menu_text(r["label"], row.label) || !menu_text(r["value"], row.value)) return false;
            out.rows.push_back(std::move(row));
        }
        return true;
    }
    if (action == "seek") {
        out.action = VideoAction::seek;
        if (!exact_keys(p, {"action", "seconds"}) || !p["seconds"].is_number_integer()) return false;
        const auto& n = p["seconds"];
        if (n.is_number_unsigned() && n.get<std::uint64_t>() > std::uint64_t(kMaxVideoSeekS)) return false;
        const auto v = n.get<std::int64_t>();
        if (v == 0 || v < -kMaxVideoSeekS || v > kMaxVideoSeekS) return false;
        out.seconds = std::int32_t(v);
        return true;
    }
    return false;
}
}
