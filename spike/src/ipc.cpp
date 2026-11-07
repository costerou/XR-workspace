#include "xr/ipc.hpp"
#include "xr/environment.hpp"
#include "xr/hud.hpp"
#include "video_ipc.hpp"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <iterator>
#include <set>
#include <stdexcept>
#include <vector>

namespace xr {
namespace {
using Json = nlohmann::json;
bool options_menu(const Json& p, OptionsMenuState& out, const Json& session) {
 auto exact=[](const Json& v, std::initializer_list<const char*> keys) {
  if(!v.is_object() || v.size()!=keys.size())return false;
  for(auto k:keys)if(!v.contains(k))return false;
  return true;
 };
 auto str=[](const Json& v, std::size_t n){return v.is_string() && v.get_ref<const std::string&>().size()<=n;};
 auto one=[](const Json& v, std::initializer_list<const char*> names){for(auto n:names)if(v==n)return true;return false;};
 auto num=[](const Json& v, double lo,double hi){return v.is_number_integer() && v.get<double>()>=lo && v.get<double>()<=hi;};
 if(!exact(p,{"open","namespace","monitor","scope","category","focus","list","scroll","revision","status","message","rows"}))return false;
 if(!p["open"].is_boolean() || !str(p["namespace"],64) || p["namespace"]!=session || !str(p["monitor"],32))return false;
 const auto monitor=p["monitor"].get<std::string>();
 if(monitor.size()<4 || monitor.substr(0,3)!="XR-" || monitor[3]=='0' || !std::all_of(monitor.begin()+3,monitor.end(),[](char c){return c>='0' && c<='9';}))return false;
 if(!one(p["scope"],{"monitor","workspace"}) || !one(p["category"],{"placement","output","3d","stabilization","layout","environment","view","video","input","keybinds","windows","privacy","help","session"}) || !one(p["status"],{"idle","pending","applied","error"}))return false;
 if(!num(p["focus"],-4,63)||!num(p["list"],-1,31)||!num(p["scroll"],0,4096)||!num(p["revision"],0,4294967295.)||!str(p["message"],256)||!p["rows"].is_array()||p["rows"].size()>64)return false;
 std::set<std::string> ids;
 for(auto r:p["rows"]) {
  if(!r.is_object())return false;
  const std::set<std::string> allowed{"id","label","scope","kind","value","minimum","maximum","step","unit","enabled","choices"};
  for(const auto& [key,v]:r.items())if(!allowed.contains(key))return false;
  for(auto key:{"id","label","scope","kind","value","enabled"})if(!r.contains(key))return false;
  if(r["kind"]=="range")for(auto key:{"minimum","maximum","step"})if(!r.contains(key))return false;
  for(auto key:{"minimum","maximum","step"})if(!r.contains(key))r[key]=0;
  if(!r.contains("unit"))r["unit"]="";
  if(!r.contains("choices"))r["choices"]=Json::array();
  if(!str(r["id"],64)||!str(r["label"],96)||!str(r["value"],256)||!str(r["unit"],16)||!r["enabled"].is_boolean()||r["scope"]!=p["scope"]||!one(r["kind"],{"action","toggle","range","choice","text","readonly"}))return false;
  if(!ids.insert(r["id"].get<std::string>()).second)return false;
  for(auto k:{"minimum","maximum","step"})if(!r[k].is_number()||!std::isfinite(r[k].get<double>()))return false;
  if(!r["choices"].is_array()||r["choices"].size()>32)return false;
  for(const auto& c:r["choices"])if(!str(c,96))return false;
  out.rows.push_back({r["id"],r["label"],r["scope"],r["kind"],r["value"],r["unit"],r["minimum"],r["maximum"],r["step"],r["enabled"],r["choices"].get<std::vector<std::string>>()});
 }
 out.open=p["open"];out.name_space=p["namespace"];out.monitor=monitor;out.scope=p["scope"];out.category=p["category"];out.status=p["status"];out.message=p["message"];out.focus=p["focus"];out.list=p["list"];out.scroll=p["scroll"];out.revision=p["revision"];
 return true;
}
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
Error invalid(std::string why) { return {"invalid_control", std::move(why)}; }
// String values a log may show: this protocol's own words, never a person's text (redacted_frame).
bool harmless_string(const std::string& key, const Json& value) {
    static const std::set<std::string> keys{"action", "background", "corner", "field", "look", "mode", "op", "projection", "side", "state", "stereo"};
    return keys.contains(key) && token(value) && value.get_ref<const std::string&>().size() <= 32;
}
void redact(Json& value, const std::string& key, int depth) {
    if (depth > 8) { value = "***"; return; }  // deeper than any frame this protocol has, and dump() recurses
    if (value.is_object()) {
        Json out = Json::object();
        for (auto& [name, item] : value.items()) {
            redact(item, name, depth + 1);
            out[token(Json(name)) ? name : "***"] = std::move(item);
        }
        value = std::move(out);
    } else if (value.is_array()) {
        for (auto& item : value) redact(item, key, depth + 1);
    } else if (value.is_string() && !harmless_string(key, value)) value = "***";
}
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
// An `immersive` frame's payload (ipc.hpp ImmersivePayload).
bool immersive(const Json& p, ImmersivePayload& out) {
    if (!p.is_object() || p.size() != 6) return false;
    for (const char* key : {"desktops", "global", "stereo", "hover", "tail", "params"}) if (!p.contains(key)) return false;
    const auto& d = p["desktops"];
    if (!d.is_array() || d.size() != kMaxDesktops || !p["global"].is_boolean() || !p["stereo"].is_boolean() || !p["tail"].is_boolean() ||
        !p["hover"].is_number_integer()) return false;
    for (std::size_t i = 0; i < kMaxDesktops; ++i) {
        if (!d[i].is_boolean()) return false;
        out.desktops[i] = d[i].get<bool>();
    }
    if (p["hover"].is_number_unsigned() && p["hover"].get<std::uint64_t>() >= kMaxDesktops) return false;  // before it could wrap as int64
    const auto hover = p["hover"].get<std::int64_t>();
    if (hover < -1 || hover >= std::int64_t(kMaxDesktops)) return false;
    out.hover = std::int32_t(hover);
    out.global = p["global"].get<bool>(); out.stereo = p["stereo"].get<bool>(); out.tail = p["tail"].get<bool>();
    const auto& w = p["params"];
    if (!w.is_object() || (w.size() != 6 && w.size() != 7)) return false;
    if (w.contains("strengths")) {
        const auto& a = w["strengths"];
        if (!a.is_array() || a.size() != kMaxDesktops) return false;
        for (std::size_t i = 0; i < kMaxDesktops; ++i) {
            if (!a[i].is_number_integer() || a[i] < 0 || a[i] > 200) return false;
            out.params.strengths[i] = a[i].get<int>();
        }
    } else if (w.size() != 6) return false;
    const struct { const char* key; std::int32_t max; std::int32_t* into; } fields[] = {
        {"strength", 200, &out.params.strength}, {"convergence", 100, &out.params.convergence}, {"near", 90, &out.params.near_arcmin},
        {"far", 60, &out.params.far_arcmin}, {"text", 100, &out.params.text}, {"range_ms", 10000, &out.params.range_ms}};
    for (const auto& f : fields) {
        if (!w.contains(f.key) || !w[f.key].is_number_integer()) return false;
        const auto v = w[f.key].is_number_unsigned() ? std::int64_t(std::min<std::uint64_t>(w[f.key].get<std::uint64_t>(), 1u << 30)) : w[f.key].get<std::int64_t>();
        if (v < 0 || v > f.max) return false;
        *f.into = std::int32_t(v);
    }
    return !(out.tail && out.hover < 0);
}
// The background's fields, on an `environment` frame and on a placement_menu frame.
bool background_name(const Json& v, std::uint8_t& out) {
    if (!v.is_string()) return false;
    const auto i = background_index(v.get_ref<const std::string&>());
    if (i >= kBackgroundCount) return false;
    out = std::uint8_t(i);
    return true;
}
bool brightness_percent(const Json& v, std::uint16_t& out) {
    if (!v.is_number_unsigned()) return false;
    const auto n = v.get<std::uint64_t>();
    if (n < kBrightnessMin || n > kBrightnessMax) return false;
    out = std::uint16_t(n);
    return true;
}
bool edge_pan(const Json& p, EdgePan& out) {
    if (!p.is_object() || !p.contains("active") || !p["active"].is_boolean()) return false;
    out.active = p["active"].get<bool>();
    if (!out.active) return p.size() == 1;
    if (p.size() != 11) return false;
    for (const char* key : {"from", "to"})
        if (!p.contains(key) || !p[key].is_number_unsigned() || p[key].get<std::uint64_t>() >= kMaxDesktops) return false;
    out.from = p["from"].get<std::uint32_t>(); out.to = p["to"].get<std::uint32_t>();
    if (!p.contains("side") || !p["side"].is_string()) return false;
    const auto& side = p["side"].get_ref<const std::string&>();
    const char* const sides[] = {"right", "left", "up", "down"};
    const auto* found = std::find(std::begin(sides), std::end(sides), side);
    if (found == std::end(sides)) return false;
    out.side = std::uint8_t(found - std::begin(sides));
    auto fraction = [&](const char* key, float& v) {
        if (!p.contains(key) || !p[key].is_number_unsigned() || p[key].get<std::uint64_t>() > 10000) return false;
        v = float(p[key].get<std::uint64_t>()) / 10000.f;
        return true;
    };
    if (!fraction("turn", out.turn)) return false;
    out.turn *= float(M_PI);  // the wire's is a share of half a turn
    if (!fraction("way", out.way)) return false;
    out.way *= 2 * float(M_PI);  // and this one of a whole turn: past half of one is the long way round
    return fraction("exit", out.exit) && fraction("entry", out.entry) && fraction("peek", out.peek) && fraction("cross", out.cross) &&
           fraction("zoom", out.zoom);
}
bool overview_pointer(const Json& p, OverviewPointerPayload& out) {
    const bool directed = p.is_object() && (p.contains("az") || p.contains("el"));
    const bool drag = p.is_object() && p.contains("drag");
    const bool looks = p.is_object() && p.contains("look");
    if (!p.is_object() || p.size() != (directed ? 6u : 4u) + (drag ? 1u : 0u) + (looks ? 1u : 0u)) return false;
    PointerLook look = PointerLook::plain;
    if (looks) {
        static constexpr std::pair<const char*, PointerLook> names[] = {
            {"resize_ew", PointerLook::resize_ew}, {"resize_ns", PointerLook::resize_ns}, {"resize_nwse", PointerLook::resize_nwse},
            {"resize_nesw", PointerLook::resize_nesw}, {"move", PointerLook::move}, {"rotate", PointerLook::rotate},
            {"text", PointerLook::text}};
        if (!p["look"].is_string()) return false;
        const auto& name = p["look"].get_ref<const std::string&>();
        const auto* found = std::find_if(std::begin(names), std::end(names), [&](const auto& n) { return name == n.first; });
        if (found == std::end(names)) return false;
        look = found->second;
    }
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
    out = {p["visible"].get<bool>(), xy[0], xy[1], p["held"].get<bool>(), directed, dir[0], dir[1], drag, ghost[0], ghost[1], ghost[2], look};
    return true;
}
bool placement_menu(const Json& p, PlacementMenuPayload& out) {
    // "surface" (optional, open panels only): the placement the open panel is drawn on while a slider is held. "names",
    // "wide", "armed", "busy" (optional): the panel's desktop row. "scale", "scales", "picker" (optional): its Scale
    // dropdown (the list open only on an open panel). "resolution", "resolution_picker" (optional): its Resolution
    // dropdown (likewise; never both lists open).
    if (!p.is_object()) return false;
    std::size_t optional = 0;
    for (const char* key : {"surface", "names", "wide", "armed", "busy", "scale", "scales", "picker", "resolution", "resolution_picker", "background",
                            "brightness", "background_picker"})
        optional += p.contains(key);
    if (p.size() != 4 + optional || !p.contains("open") || !p["open"].is_boolean() || !p.contains("placements") ||
        !p["placements"].is_array() || p["placements"].empty() || p["placements"].size() > kPlacementMenuDesktops ||
        !p.contains("desktop") || !p["desktop"].is_number_unsigned() || p["desktop"].get<std::uint64_t>() >= p["placements"].size() ||
        !p.contains("field") || !p["field"].is_string())
        return false;
    const auto& field = p["field"].get_ref<const std::string&>();
    const auto* named = std::find_if(std::begin(kPlacementBounds), std::end(kPlacementBounds), [&](const Bound& b) { return field == b.key; });
    if (named == std::end(kPlacementBounds) && field != "brightness") return false;
    out = {};
    out.open = p["open"].get<bool>();
    out.desktop = p["desktop"].get<std::uint32_t>();
    out.field = named == std::end(kPlacementBounds) ? std::uint32_t(kPlacementMenuBrightness) : std::uint32_t(named - std::begin(kPlacementBounds));
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
                             std::pair{"picker", &out.picker}, std::pair{"resolution_picker", &out.resolution_picker},
                             std::pair{"background_picker", &out.background_picker}})
        if (p.contains(key)) {
            if (!p[key].is_boolean()) return false;
            *flag = p[key].get<bool>();
        }
    if ((out.picker || out.resolution_picker || out.background_picker) && !out.open) return false;
    if (int(out.picker) + int(out.resolution_picker) + int(out.background_picker) > 1) return false;  // one list at a time
    if (p.contains("resolution")) {  // [width, height]: a desktop size the broker accepts
        const auto& r = p["resolution"];
        if (!r.is_array() || r.size() != 2 || !r[0].is_number_unsigned() || !r[1].is_number_unsigned()) return false;
        const auto w = r[0].get<std::uint64_t>(), h = r[1].get<std::uint64_t>();
        if (w < kDesktopMinW || w > kDesktopMaxW || h < kDesktopMinH || h > kDesktopMaxH || w % 2 || h % 2) return false;
        out.resolution_w = std::uint16_t(w); out.resolution_h = std::uint16_t(h);
    }
    if (p.contains("background") && !background_name(p["background"], out.background)) return false;
    if (p.contains("brightness") && !brightness_percent(p["brightness"], out.brightness)) return false;
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
    if (!p.is_object() || p.size() != 1u + p.contains("renew") + p.contains("ease") || !p.contains("outputs")) return false;
    if (!desktop_names(p["outputs"], out.outputs, 1)) return false;
    if (p.contains("ease")) {
        if (!p["ease"].is_boolean()) return false;
        out.ease = p["ease"].get<bool>();
    }
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
bool restore_band(const Json& p, RestoreBandPayload& out) {
    if (!p.is_object() || p.size() != 1 || !p.contains("open") || !p["open"].is_boolean()) return false;
    out.open = p["open"].get<bool>();
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
// binds_panel.hpp BindPanelState: printable ASCII text of `lo`..`hi` characters.
bool ascii_text(const Json& value, std::size_t lo, std::size_t hi) {
    if (!value.is_string()) return false;
    const auto& s = value.get_ref<const std::string&>();
    return s.size() >= lo && s.size() <= hi && std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 0x20 && c < 0x7f; });
}
bool binds(const Json& p, BindPanelState& out) {
    if (!p.is_object() || !p.contains("visible") || !p["visible"].is_boolean()) return false;
    out = {};
    out.visible = p["visible"].get<bool>();
    if (!out.visible) return p.size() == 1;
    if (p.size() != 7 || !ascii_text(p.value("title", Json()), 1, kMaxBindLabel) || !ascii_text(p.value("hint", Json()), 1, kMaxBindText) ||
        !ascii_text(p.value("status", Json()), 0, kMaxBindText) || !p.contains("alert") || !p["alert"].is_boolean() ||
        !p.contains("rows") || !p["rows"].is_array() || p["rows"].empty() || p["rows"].size() > kMaxBindRows ||
        !p.contains("selected") || !p["selected"].is_number_unsigned() || p["selected"].get<std::uint64_t>() >= p["rows"].size())
        return false;
    out.title = p["title"].get<std::string>(); out.hint = p["hint"].get<std::string>(); out.status = p["status"].get<std::string>();
    out.alert = p["alert"].get<bool>();
    out.selected = std::uint32_t(p["selected"].get<std::uint64_t>());
    for (const auto& r : p["rows"]) {
        if (!r.is_object() || r.size() != 4 || !ascii_text(r.value("action", Json()), 1, kMaxBindLabel) ||
            !ascii_text(r.value("keys", Json()), 1, kMaxBindLabel) || !ascii_text(r.value("note", Json()), 0, kMaxBindText) ||
            !r.contains("state") || !r["state"].is_string())
            return false;
        BindRow row{r["action"].get<std::string>(), r["keys"].get<std::string>(), r["note"].get<std::string>(), BindRowState::normal};
        const auto& state = r["state"].get_ref<const std::string&>();
        if (state == "normal") row.state = BindRowState::normal;
        else if (state == "custom") row.state = BindRowState::custom;
        else if (state == "conflict") row.state = BindRowState::conflict;
        else if (state == "capture") row.state = BindRowState::capture;
        else return false;
        out.rows.push_back(std::move(row));
    }
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
        if (name != "none" && name != "bar") return false;
        out.bars[i] = name == "bar";
    }
    const auto& h = p["hover"];
    const auto top = std::int64_t(kMaxDesktops) - 1;
    if (h.is_number_unsigned() ? h.get<std::uint64_t>() > std::uint64_t(top) : (h.get<std::int64_t>() < -1 || h.get<std::int64_t>() > top)) return false;
    out.hover = std::int32_t(h.get<std::int64_t>());
    const auto& side = p["side"].get_ref<const std::string&>();
    if (side == "none") out.side = -1;
    else if (side == "ccw") out.side = 0;
    else if (side == "level") out.side = 1;
    else if (side == "cw") out.side = 2;
    else return false;
    if ((out.hover < 0) != (out.side < 0)) return false;
    out.active = p["active"].get<bool>();
    return true;
}
}
Result<ControlMessage> decode_control(std::string_view frame) {
    if (frame.empty() || frame.size() > 524288 || frame.back() != '\n' || frame.substr(0, frame.size()-1).find('\n') != frame.npos) return invalid("bad framing");
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
        if (!j.is_object() || j.size() != 6) return invalid("bad envelope");
        for (auto field : {"schema", "request_id", "run_id", "session", "op", "payload"})
            if (!j.contains(field)) return invalid("bad envelope");
        if (!j["schema"].is_number_unsigned() || j["schema"] != 1 || !j["request_id"].is_number_unsigned() || j["request_id"].get<std::uint64_t>() == 0) return invalid("bad envelope");
        if (!token(j["run_id"]) || !token(j["session"]) || !j["op"].is_string()) return invalid("bad envelope");
        ControlOp op;
        const auto name = j["op"].get<std::string>();
        if(name != "options_menu" && (frame.size()>16384 || !j["payload"].is_object()))return invalid("bad envelope");
        const auto bad = [&name] { return invalid("bad " + name + " payload"); };
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
        else if (name == "options_menu") op = ControlOp::options_menu;
        else if (name == "placement_menu") op = ControlOp::placement_menu;
        else if (name == "resize_handle") op = ControlOp::resize_handle;
        else if (name == "move_handle") op = ControlOp::move_handle;
        else if (name == "restore_band") op = ControlOp::restore_band;
        else if (name == "overview_pointer") op = ControlOp::overview_pointer;
        else if (name == "roll") op = ControlOp::roll;
        else if (name == "desktops") op = ControlOp::desktops;
        else if (name == "environment") op = ControlOp::environment;
        else if (name == "binds") op = ControlOp::binds;
        else if (name == "edge_pan") op = ControlOp::edge_pan;
        else if (name == "immersive") op = ControlOp::immersive;
        else return invalid("unknown op");
        ControlPayload payload;
        if (op == ControlOp::video) {
            VideoPayload video;
            if (!decode_video_payload(j["payload"], video)) return bad();
            payload = std::move(video);
        } else if (op == ControlOp::select) {
            if (j["payload"].size() != 1 || !j["payload"].contains("output_id") || !token(j["payload"]["output_id"])) return bad();
            payload = SelectPayload{j["payload"]["output_id"].get<std::string>()};
        } else if (op == ControlOp::view) {
            const auto& p = j["payload"];
            const bool has_center = p.contains("center"), has_cut = p.contains("cut"), has_pan = p.contains("pan");
            if (p.size() != 7u + has_center + has_cut + has_pan + p.contains("stabilization") || (has_center && !p["center"].is_boolean()) ||
                (has_cut && !p["cut"].is_boolean()) || (has_pan && !p["pan"].is_boolean()) || !p.contains("typing") || !p["typing"].is_boolean() ||
                !p.contains("prediction") || !p["prediction"].is_boolean() || !p.contains("zoomed") || !p.contains("selected") || !p.contains("spacing") || !p.contains("mode") ||
                !p.contains("cursor_follow") || !p["cursor_follow"].is_boolean() || !p["zoomed"].is_boolean() || !p["selected"].is_number_unsigned() || !p["spacing"].is_number_unsigned() || !p["mode"].is_string() ||
                p["selected"].get<std::uint64_t>() >= kMaxDesktops || p["spacing"].get<std::uint64_t>() > 400) return bad();
            if (p.contains("stabilization") && (!p["stabilization"].is_number_unsigned() || p["stabilization"].get<std::uint64_t>() > 100)) return bad();
            const auto mode = p["mode"].get<std::string>();
            std::uint8_t m = 0;
            if (mode == "anchor") m = 0; else if (mode == "locked") m = 1; else if (mode == "follow") m = 2; else return bad();
            payload = ViewPayload{p["zoomed"].get<bool>(), p["selected"].get<std::uint32_t>(), p["spacing"].get<std::uint32_t>(), m,
                                  p["cursor_follow"].get<bool>(), p["prediction"].get<bool>(), p["typing"].get<bool>(),
                                  has_center && p["center"].get<bool>(), has_cut && p["cut"].get<bool>(), has_pan && p["pan"].get<bool>(), p.value("stabilization", 25u)};
        } else if (op == ControlOp::style) {
            const auto& p = j["payload"];
            if (p.size() != 1 || !p.contains("border") || !p["border"].is_array() || p["border"].size() != 3) return bad();
            std::uint8_t c[3];
            for (int i = 0; i < 3; ++i) {
                const auto& v = p["border"][std::size_t(i)];
                if (!v.is_number_unsigned() || v.get<std::uint64_t>() > 255) return bad();
                c[i] = std::uint8_t(v.get<std::uint64_t>());
            }
            payload = StylePayload{c[0], c[1], c[2]};
        } else if (op == ControlOp::help) {
            const auto& p = j["payload"];
            if (!p.contains("visible") || !p["visible"].is_boolean() || !p.contains("rows") || !p["rows"].is_array() ||
                p["rows"].size() > kMaxHelpRows || p.size() != std::size_t(2 + p.contains("sections") + p.contains("turn"))) return bad();
            HelpPayload help{p["visible"].get<bool>(), {}, {}, 0};
            for (const auto& row : p["rows"]) {
                if (!row.is_array() || row.size() != 2 || !help_text(row[0]) || !help_text(row[1])) return bad();
                help.rows.emplace_back(row[0].get<std::string>(), row[1].get<std::string>());
            }
            if (p.contains("sections")) {
                const auto& sections = p["sections"];
                if (!sections.is_array() || sections.size() > kMaxHelpRows) return bad();
                std::size_t total = 0;
                for (const auto& n : sections) {
                    if (!n.is_number_unsigned() || n.get<std::uint64_t>() < 1 || n.get<std::uint64_t>() > kMaxHelpRows) return bad();
                    help.sections.push_back(std::uint32_t(n.get<std::uint64_t>()));
                    total += help.sections.back();
                }
                if (total != help.rows.size()) return bad();
            }
            if (p.contains("turn")) {
                const auto& t = p["turn"];
                if (!t.is_number_integer() || (t.is_number_unsigned() ? t.get<std::uint64_t>() != 1 : t.get<std::int64_t>() != -1))
                    return bad();
                help.turn = std::int32_t(t.get<std::int64_t>());
            }
            payload = std::move(help);
        } else if (op == ControlOp::placement) {
            const auto& p = j["payload"];
            const bool glide = p.contains("glide");  // optional: the frame's desktops glide there together (ipc.hpp)
            if (p.size() != 1 + std::size_t(glide) || !p.contains("desktops") || !p["desktops"].is_array() || p["desktops"].empty() ||
                p["desktops"].size() > kMaxDesktops)
                return bad();
            PlacementPayload placements;
            if (glide) {
                const auto& g = p["glide"];
                if (!g.is_number_unsigned() || g.get<std::uint64_t>() < 1 || g.get<std::uint64_t>() > kMaxPlacementGlideMs) return bad();
                placements.glide_ms = std::uint32_t(g.get<std::uint64_t>());
            }
            for (const auto& d : p["desktops"]) {
                WirePlacement w;
                if (!placement(d, w)) return bad();
                placements.desktops.push_back(w);
            }
            payload = std::move(placements);
        } else if (op == ControlOp::options_menu) {
            OptionsMenuState menu;
            if (!options_menu(j["payload"], menu, j["session"])) return Error{kInvalidOptionsMenuCode, "Ignored malformed options_menu payload"};
            payload = std::move(menu);
        } else if (op == ControlOp::placement_menu) {
            PlacementMenuPayload menu;
            if (!placement_menu(j["payload"], menu)) return Error{kInvalidMenuCode, "Ignored malformed placement_menu payload"};
            payload = menu;
        } else if (op == ControlOp::resize_handle) {
            ResizeHandlePayload handle;
            if (!resize_handle(j["payload"], handle)) return Error{kInvalidHandleCode, "Ignored malformed resize_handle payload"};
            payload = handle;
        } else if (op == ControlOp::restore_band) {
            RestoreBandPayload band;
            if (!restore_band(j["payload"], band)) return Error{kInvalidRestoreBandCode, "Ignored malformed restore_band payload"};
            payload = band;
        } else if (op == ControlOp::move_handle) {
            MoveHandlePayload pill;
            if (!move_handle(j["payload"], pill)) return Error{kInvalidMoveHandleCode, "Ignored malformed move_handle payload"};
            payload = pill;
        } else if (op == ControlOp::overview_pointer) {
            OverviewPointerPayload pointer;
            if (!overview_pointer(j["payload"], pointer)) return Error{kInvalidOverviewPointerCode, "Ignored malformed overview_pointer payload"};
            payload = pointer;
        } else if (op == ControlOp::edge_pan) {
            EdgePan pan;
            if (!edge_pan(j["payload"], pan)) return Error{kInvalidEdgePanCode, "Ignored malformed edge_pan payload"};
            payload = pan;
        } else if (op == ControlOp::roll) {
            RollPayload r;
            if (!roll(j["payload"], r)) return Error{kInvalidRollCode, "Ignored malformed roll payload"};
            payload = r;
        } else if (op == ControlOp::immersive) {
            ImmersivePayload i;
            if (!immersive(j["payload"], i)) return Error{kInvalidImmersiveCode, "Ignored malformed immersive payload"};
            payload = i;
        } else if (op == ControlOp::binds) {
            BindPanelState panel;
            if (!binds(j["payload"], panel)) return Error{kInvalidBindsCode, "Ignored malformed binds payload"};
            payload = std::move(panel);
        } else if (op == ControlOp::desktops) {
            DesktopsPayload set;
            if (!desktops(j["payload"], set)) return bad();
            payload = std::move(set);
        } else if (op == ControlOp::environment) {
            const auto& p = j["payload"];
            EnvironmentPayload e;
            if (p.size() != 2 || !p.contains("background") || !background_name(p["background"], e.background) ||
                !p.contains("brightness") || !brightness_percent(p["brightness"], e.brightness))
                return Error{kInvalidEnvironmentCode, "Ignored malformed environment payload"};
            payload = e;
        } else if (!j["payload"].empty()) return bad();
        return ControlMessage{1, j["request_id"].get<std::uint64_t>(), j["run_id"].get<std::string>(), j["session"].get<std::string>(), op, payload};
    } catch (const Json::exception&) { return invalid(frame.size()>16384 ? "bad framing" : "not JSON"); }
    catch (const std::exception& e) { return invalid(e.what()); }  // the parser's callback: a duplicate key, nesting too deep
}
ControlQueue::ControlQueue(std::string run_id, std::string session)
    : run_id_(std::move(run_id)), session_(std::move(session)) {
    if (!token(Json(run_id_)) || !token(Json(session_))) throw std::invalid_argument("invalid identity");
}
Status ControlQueue::push(std::string_view frame) {
    auto decoded = decode_control(frame);
    if (const auto* error = std::get_if<Error>(&decoded)) return *error;
    auto message = std::get<ControlMessage>(std::move(decoded));
    if (message.run_id != run_id_ || message.session != session_) return Error{kControlWrongRunCode, "frame is for another run or session"};
    if (message.request_id <= last_)
        return Error{kControlReplayCode, "request id " + std::to_string(message.request_id) + " is not above " + std::to_string(last_)};
    if (messages_.size() >= 8) return Error{"queue_full", "Control queue capacity is eight"};
    last_ = message.request_id;
    messages_.push_back(std::move(message));
    return {};
}
std::optional<ControlMessage> ControlQueue::pop() {
    if (messages_.empty()) return std::nullopt;
    auto message = std::move(messages_.front()); messages_.pop_front(); return message;
}
std::string redacted_frame(std::string_view frame) {
    if (frame.empty()) return {};
    auto parsed = Json::parse(frame, nullptr, false);
    std::string out;
    if (!parsed.is_discarded()) {
        redact(parsed, {}, 0);
        out = parsed.dump(-1, ' ', true, Json::error_handler_t::replace);
    } else {  // not JSON: nothing tells its strings from any other text, so no words at all
        for (const unsigned char c : frame.substr(0, kMaxRedactedFrame + 1))
            out += std::isalnum(c) ? 'x' : c >= 0x20 && c < 0x7f ? char(c) : '.';
    }
    if (out.size() > kMaxRedactedFrame) { out.resize(kMaxRedactedFrame); out += "..."; }
    return out;
}
ControlStream::ControlStream(std::string run_id, std::string session, bool fatal, Deliver deliver, Refused refused)
    : queue_(std::move(run_id), std::move(session)), fatal_(fatal), deliver_(std::move(deliver)), refused_callback_(std::move(refused)) {}
void ControlStream::start(std::string_view frame) {
    (void)queue_.push(frame);
    while (queue_.pop()) {}
}
bool ControlStream::refuse(const Error& error, std::string_view frame) {
    ++refused_;
    ControlRefusal refusal{error, {}, 0, redacted_frame(frame)};
    // Only for the log and the descriptor a video frame may have brought: nothing of a refused frame is acted on.
    const auto parsed = Json::parse(frame, nullptr, false);
    if (parsed.is_object()) {
        if (parsed.contains("op") && token(parsed["op"])) refusal.op = parsed["op"].get<std::string>();
        if (parsed.contains("request_id") && parsed["request_id"].is_number_unsigned()) refusal.request_id = parsed["request_id"].get<std::uint64_t>();
    }
    refused_callback_(refusal);
    if (fatal_ && !ignorable_control_error(error)) stopped_ = true;
    return !stopped_;
}
bool ControlStream::feed(std::string_view bytes) {
    if (skipping_) {
        const auto end = bytes.find('\n');
        if (end == bytes.npos) return true;
        bytes.remove_prefix(end + 1);
        skipping_ = false;
    }
    pending_.append(bytes);
    for (std::size_t nl; (nl = pending_.find('\n')) != std::string::npos;) {
        const auto frame = pending_.substr(0, nl + 1);
        pending_.erase(0, nl + 1);
        const auto pushed = queue_.push(frame);
        if (const auto* error = std::get_if<Error>(&pushed)) {
            if (!refuse(*error, frame)) return false;
            continue;
        }
        while (auto message = queue_.pop()) {
            ++accepted_;
            deliver_(*message);
        }
    }
    if (pending_.size() > kMaxControlPending) {
        const auto start = pending_.substr(0, kMaxRedactedFrame + 1);
        pending_.clear();
        skipping_ = true;
        return refuse({kControlOverflowCode, "no newline in " + std::to_string(kMaxControlPending) + " bytes"}, start);
    }
    return true;
}
} // namespace xr
