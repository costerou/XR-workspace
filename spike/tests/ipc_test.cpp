#include "xr/ipc.hpp"
#include <iostream>
#include <stdexcept>
#include <string>
#include <variant>

void require(bool value) { if (!value) throw std::runtime_error("IPC assertion failed"); }
std::string wire(unsigned id = 1) {
    return "{\"schema\":1,\"request_id\":" + std::to_string(id) +
        ",\"run_id\":\"run\",\"session\":\"session\",\"op\":\"status\",\"payload\":{}}\n";
}
int main() {
    const auto good = xr::decode_control(wire());
    require(std::holds_alternative<xr::ControlMessage>(good));
    require(std::get<xr::ControlMessage>(good).schema == 1);
    for (const auto& bad : {
        std::string("{\"schema\":1,\"schema\":1}\n"),
        std::string("{\"schema\":NaN}\n"),
        std::string("{\"schema\":2}\n"),
        std::string(16384, ' ') + "\n", wire().substr(0, wire().size()-1),
        std::string("{\"schema\":1,\"request_id\":1,\"run_id\":\"run\",\"session\":\"session\",\"op\":\"status\",\"payload\":{\"x\":1,\"x\":2}}\n"),
        std::string("{\"schema\":1,\"request_id\":1,\"run_id\":\"run\",\"session\":\"session\",\"op\":\"exec\",\"payload\":{}}\n")}) {
        require(std::holds_alternative<xr::Error>(xr::decode_control(bad)));
    }
    for (const auto& [from, to] : {
        std::pair<std::string, std::string>{"\"schema\":1", "\"schema\":2"},
        {"\"schema\":1", "\"schema\":true"},
        {"\"request_id\":1", "\"request_id\":0"},
        {"\"request_id\":1", "\"request_id\":1.0"},
        {"\"request_id\":1", "\"request_id\":18446744073709551616"},
        {"\"payload\":{}", "\"payload\":{\"x\":NaN}"},
        {"\"payload\":{}", "\"payload\":{\"x\":1e999}"},
        {"\"payload\":{}", "\"payload\":{},\"command\":\"evil\""}}) {
        auto bad = wire();
        bad.replace(bad.find(from), from.size(), to);
        require(std::holds_alternative<xr::Error>(xr::decode_control(bad)));
    }
    auto select = wire();
    select.replace(select.find("status"), 6, "select");
    select.replace(select.find("{}"), 2, "{\"output_id\":\"out\"}");
    auto selected = xr::decode_control(select);
    require(std::holds_alternative<xr::ControlMessage>(selected));
    require(std::get<xr::SelectPayload>(std::get<xr::ControlMessage>(selected).payload).output_id == "out");
    xr::ControlQueue wrong_session("run", "other");
    require(std::holds_alternative<xr::Error>(wrong_session.push(wire())));
    xr::ControlQueue queue("run", "session");
    for (unsigned i = 1; i <= 8; ++i) require(std::holds_alternative<std::monostate>(queue.push(wire(i))));
    require(std::holds_alternative<xr::Error>(queue.push(wire(9))));
    require(queue.pop()->request_id == 1);
    require(std::holds_alternative<xr::Error>(queue.push(wire(8))));
    require(std::holds_alternative<std::monostate>(queue.push(wire(9))));
    // View control (zoom / select / spacing), bounded.
    auto view = [](const std::string& payload) {
        return std::string(R"({"schema":1,"request_id":9,"run_id":"run","session":"session","op":"view","payload":)") + payload + "}\n";
    };
    auto view_decoded = xr::decode_control(view(R"({"zoomed":true,"selected":2,"spacing":40,"mode":"follow","cursor_follow":true,"prediction":true,"typing":true})"));
    require(std::holds_alternative<xr::ControlMessage>(view_decoded));
    auto& message = std::get<xr::ControlMessage>(view_decoded);
    require(message.op == xr::ControlOp::view);
    auto& v = std::get<xr::ViewPayload>(message.payload);
    require(v.zoomed && v.selected == 2 && v.spacing == 40 && v.mode == 2 && v.cursor_follow && v.prediction && v.typing);
    {  // `selected` reaches the last desktop a run can have, and no further.
        auto selecting = [&](std::size_t selected) {
            return view(R"({"zoomed":true,"selected":)" + std::to_string(selected) + R"(,"spacing":40,"mode":"follow","cursor_follow":false,"prediction":false,"typing":false})");
        };
        auto last = xr::decode_control(selecting(xr::kMaxDesktops - 1));
        require(std::holds_alternative<xr::ControlMessage>(last));
        require(std::get<xr::ViewPayload>(std::get<xr::ControlMessage>(last).payload).selected == xr::kMaxDesktops - 1);
        require(std::holds_alternative<xr::Error>(xr::decode_control(selecting(xr::kMaxDesktops))));
    }
    auto control_mode =xr::decode_control(view(R"({"zoomed":false,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false})"));
    require(std::holds_alternative<xr::ControlMessage>(control_mode) &&
            !std::get<xr::ViewPayload>(std::get<xr::ControlMessage>(control_mode).payload).typing);
    require(!v.center);  // optional, false
    auto centred = xr::decode_control(view(R"({"zoomed":true,"selected":1,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"center":true})"));
    require(std::holds_alternative<xr::ControlMessage>(centred) && std::get<xr::ViewPayload>(std::get<xr::ControlMessage>(centred).payload).center);
    auto cut = xr::decode_control(view(R"({"zoomed":true,"selected":1,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"cut":true})"));
    require(std::holds_alternative<xr::ControlMessage>(cut) && std::get<xr::ViewPayload>(std::get<xr::ControlMessage>(cut).payload).cut &&
            !std::get<xr::ViewPayload>(std::get<xr::ControlMessage>(cut).payload).center && !v.cut);
    auto both = xr::decode_control(view(R"({"zoomed":true,"selected":1,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"center":false,"cut":true})"));
    require(std::holds_alternative<xr::ControlMessage>(both));
    for (const char* bad : {R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"cut":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"cut":true})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"cut":true,"center":true,"x":1})"})
        require(std::holds_alternative<xr::Error>(xr::decode_control(view(bad))));
    for (const char* bad : {R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"center":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"center":true})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"center":true,"x":1})"})
        require(std::holds_alternative<xr::Error>(xr::decode_control(view(bad))));
    for (const char* bad : {R"({"zoomed":true,"selected":0,"spacing":0})", R"({"zoomed":true,"selected":0,"spacing":0,"mode":"orbit"})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":1})", R"({"zoomed":1,"selected":0,"spacing":0,"mode":"anchor"})", R"({"zoomed":true,"selected":4,"spacing":0})",
                            R"({"zoomed":true,"selected":0,"spacing":401})", R"({"zoomed":true,"selected":-1,"spacing":0})",
                            R"({"zoomed":true,"selected":0})", R"({"zoomed":true,"selected":0,"spacing":0,"x":1})",
                            R"({"zoomed":true,"selected":0.5,"spacing":0})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor"})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"x":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"x":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"typing":false,"x":1})"})
        require(std::holds_alternative<xr::Error>(xr::decode_control(view(bad))));
    auto style = [](const std::string& payload) {
        return std::string(R"({"schema":1,"request_id":9,"run_id":"run","session":"session","op":"style","payload":)") + payload + "}\n";
    };
    auto st = xr::decode_control(style(R"({"border":[141,20,255]})"));
    require(std::holds_alternative<xr::ControlMessage>(st));
    auto& sp = std::get<xr::StylePayload>(std::get<xr::ControlMessage>(st).payload);
    require(sp.r == 141 && sp.g == 20 && sp.b == 255);
    for (const char* bad : {R"({"border":[1,2]})", R"({"border":[1,2,256]})", R"({"border":[1,2,-1]})", R"({"border":"8d8d8d"})",
                            R"({"border":[1,2,3],"x":1})", R"({})", R"({"border":[1,2,3.5]})"})
        require(std::holds_alternative<xr::Error>(xr::decode_control(style(bad))));
    auto recenter = std::string(R"({"schema":1,"request_id":9,"run_id":"run","session":"session","op":"recenter","payload":{}})") + "\n";
    auto rc = xr::decode_control(recenter);
    require(std::holds_alternative<xr::ControlMessage>(rc) && std::get<xr::ControlMessage>(rc).op == xr::ControlOp::recenter);
    auto rc_bad = recenter; rc_bad.replace(rc_bad.find("{}"), 2, "{\"x\":1}");
    require(std::holds_alternative<xr::Error>(xr::decode_control(rc_bad)));
    auto help = [](const std::string& payload) {
        return std::string(R"({"schema":1,"request_id":9,"run_id":"run","session":"session","op":"help","payload":)") + payload + "}\n";
    };
    auto hd = xr::decode_control(help(R"({"visible":true,"rows":[["Ctrl+Alt+H","Show / hide this help"],["Ctrl+Alt+[","Less"]]})"));
    require(std::holds_alternative<xr::ControlMessage>(hd) && std::get<xr::ControlMessage>(hd).op == xr::ControlOp::help);
    const auto& hp = std::get<xr::HelpPayload>(std::get<xr::ControlMessage>(hd).payload);
    require(hp.visible && hp.rows.size() == 2 && hp.rows[1].first == "Ctrl+Alt+[" && hp.rows[0].second == "Show / hide this help");
    require(std::holds_alternative<xr::ControlMessage>(xr::decode_control(help(R"({"visible":false,"rows":[]})"))));
    // Optional sections (sizes adding up to the rows) and turn (-1 / 1): the help card's pages.
    auto paged = xr::decode_control(help(R"({"visible":true,"rows":[["a","b"],["c","d"],["e","f"]],"sections":[2,1],"turn":-1})"));
    require(std::holds_alternative<xr::ControlMessage>(paged));
    const auto& hpp = std::get<xr::HelpPayload>(std::get<xr::ControlMessage>(paged).payload);
    require(hpp.sections == std::vector<std::uint32_t>{2, 1} && hpp.turn == -1 && hpp.rows.size() == 3);
    require(std::get<xr::HelpPayload>(std::get<xr::ControlMessage>(xr::decode_control(help(R"({"visible":true,"rows":[["a","b"]],"turn":1})"))).payload).turn == 1);
    for (const char* bad : {R"({"visible":true,"rows":[["a","b"]],"sections":[2]})", R"({"visible":true,"rows":[["a","b"]],"sections":[0,1]})",
                            R"({"visible":true,"rows":[["a","b"]],"sections":[1.0]})", R"({"visible":true,"rows":[["a","b"]],"sections":[-1,2]})",
                            R"({"visible":true,"rows":[["a","b"]],"sections":1})", R"({"visible":true,"rows":[["a","b"]],"turn":0})",
                            R"({"visible":true,"rows":[["a","b"]],"turn":2})", R"({"visible":true,"rows":[["a","b"]],"turn":-2})",
                            R"({"visible":true,"rows":[["a","b"]],"turn":true})", R"({"visible":true,"rows":[["a","b"]],"turn":1.0})",
                            R"({"visible":true,"rows":[["a","b"]],"turn":1,"x":1})"})
        require(std::holds_alternative<xr::Error>(xr::decode_control(help(bad))));
    std::string full = R"({"visible":true,"rows":[)";  // kMaxHelpRows rows: accepted (the card pages them)
    for (std::size_t i = 0; i < xr::kMaxHelpRows; ++i) full += std::string(i ? "," : "") + R"(["Ctrl+Alt+X","Some action"])";
    require(std::holds_alternative<xr::ControlMessage>(xr::decode_control(help(full + "]}"))));
    std::string many = R"({"visible":true,"rows":[)";
    for (std::size_t i = 0; i <= xr::kMaxHelpRows; ++i) many += std::string(i ? "," : "") + R"(["a","b"])";
    many += "]}";
    for (const std::string& bad : {std::string(R"({"visible":true})"), std::string(R"({"visible":1,"rows":[]})"),
                                  std::string(R"({"visible":true,"rows":[["a"]]})"), std::string(R"({"visible":true,"rows":[["a",1]]})"),
                                  std::string(R"({"visible":true,"rows":[["","b"]]})"), std::string(R"({"visible":true,"rows":[["a\u0007","b"]]})"),
                                  std::string(R"({"visible":true,"rows":[["\u00e9","b"]]})"), std::string(R"({"visible":true,"rows":{}})"),
                                  std::string(R"({"visible":true,"rows":[],"x":1})"),
                                  std::string(R"({"visible":true,"rows":[[")") + std::string(xr::kMaxHelpText + 1, 'a') + R"(","b"]]})", many})
        require(std::holds_alternative<xr::Error>(xr::decode_control(help(bad))));
    // Per-desktop placement: absolute, 1..3 desktops, exactly the four integer fields (x, y, distance, curvature) inside the
    // mirrored bounds. The removed facing settings (yaw / pitch / roll) are rejected, alone or alongside the four.
    auto placement = [](const std::string& payload) {
        return std::string(R"({"schema":1,"request_id":10,"run_id":"run","session":"session","op":"placement","payload":)") + payload + "}\n";
    };
    const std::string flat = R"({"x":0,"y":0,"distance":100,"curvature":0})";
    const std::string edge = R"({"x":-1800,"y":900,"distance":200,"curvature":120})";
    const std::string low = R"({"x":1800,"y":-900,"distance":10,"curvature":0})";
    auto pd = xr::decode_control(placement(R"({"desktops":[)" + flat + "," + edge + "," + low + "]}"));
    require(std::holds_alternative<xr::ControlMessage>(pd) && std::get<xr::ControlMessage>(pd).op == xr::ControlOp::placement);
    const auto& pp = std::get<xr::PlacementPayload>(std::get<xr::ControlMessage>(pd).payload);
    require(pp.desktops.size() == 3 && pp.desktops[0].distance == 100 && pp.desktops[1].x == -1800 && pp.desktops[1].y == 900 &&
            pp.desktops[1].distance == 200 && pp.desktops[1].curvature == 120 && pp.desktops[2].x == 1800 && pp.desktops[2].y == -900 &&
            pp.desktops[2].distance == 10 && pp.desktops[2].curvature == 0);
    require(pp.glide_ms == 0);  // no glide: the quick exponential easing
    // Key order does not matter.
    require(std::holds_alternative<xr::ControlMessage>(
        xr::decode_control(placement(R"({"desktops":[{"curvature":3,"distance":75,"y":-1,"x":2}]})"))));
    // Optional glide (bumping, view.py GLIDE_MS): 1..kMaxPlacementGlideMs whole milliseconds, either side of "desktops".
    for (const auto& [payload, ms] : {std::pair{R"({"desktops":[)" + flat + R"(],"glide":240})", 240u},
                                      std::pair{R"({"glide":1,"desktops":[)" + flat + "]}", 1u},
                                      std::pair{R"({"desktops":[)" + flat + R"(],"glide":1000})", 1000u}}) {
        auto gd = xr::decode_control(placement(payload));
        require(std::holds_alternative<xr::ControlMessage>(gd));
        require(std::get<xr::PlacementPayload>(std::get<xr::ControlMessage>(gd).payload).glide_ms == ms);
    }
    for (const std::string& bad : {R"({"desktops":[)" + flat + R"(],"glide":0})", R"({"desktops":[)" + flat + R"(],"glide":1001})",
                                  R"({"desktops":[)" + flat + R"(],"glide":-5})", R"({"desktops":[)" + flat + R"(],"glide":2.5})",
                                  R"({"desktops":[)" + flat + R"(],"glide":true})", R"({"desktops":[)" + flat + R"(],"glide":"240"})",
                                  R"({"desktops":[)" + flat + R"(],"glide":240,"extra":1})", std::string(R"({"glide":240})")})
        require(std::holds_alternative<xr::Error>(xr::decode_control(placement(bad))));
    auto with = [&](const std::string& key, const std::string& value) {
        auto p = flat; const auto at = p.find("\"" + key + "\":") + key.size() + 3;
        return p.substr(0, at) + value + p.substr(p.find_first_of(",}", at));
    };
    for (const std::string& bad : {std::string(R"({"desktops":[]})"), std::string(R"({"desktops":{}})"), std::string(R"({})"),
                                  R"({"desktops":[)" + flat + "," + flat + "," + flat + "," + flat + "," + flat + "]}",
                                  R"({"desktops":[)" + flat + R"(],"extra":1})",
                                  R"({"desktops":[)" + with("x", "1801") + "]}", R"({"desktops":[)" + with("x", "-1801") + "]}",
                                  R"({"desktops":[)" + with("y", "901") + "]}", R"({"desktops":[)" + with("y", "-901") + "]}",
                                  R"({"desktops":[)" + with("distance", "9") + "]}", R"({"desktops":[)" + with("distance", "201") + "]}",
                                  R"({"desktops":[)" + with("curvature", "-1") + "]}",
                                  R"({"desktops":[)" + with("curvature", "121") + "]}", R"({"desktops":[)" + with("x", "1.5") + "]}",
                                  R"({"desktops":[)" + with("x", "true") + "]}", R"({"desktops":[)" + with("x", "\"1\"") + "]}",
                                  R"({"desktops":[)" + with("x", "18446744073709551615") + "]}",
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"curvature":0,"z":0}]})"),
                                  // The old seven-key placement, and the four with any removed facing setting in place of one
                                  // of them or added.
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0,"curvature":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"yaw":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"pitch":0,"curvature":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"roll":0,"distance":100,"curvature":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"curvature":0,"yaw":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"curvature":0,"roll":0}]})")})
        require(std::holds_alternative<xr::Error>(xr::decode_control(placement(bad))));
    // Placement menu: display-only state. Valid payloads decode; a malformed payload in a well-framed message is
    // rejected with the ignorable invalid_menu code (dropped, never fatal); malformed framing is fatal as before.
    auto menu = [](const std::string& payload, const char* op = "placement_menu") {
        return std::string(R"({"schema":1,"request_id":11,"run_id":"run","session":"session","op":")") + op + R"(","payload":)" + payload + "}\n";
    };
    const std::string three = "[" + flat + "," + edge + "," + flat + "]";
    auto md = xr::decode_control(menu(R"({"open":true,"desktop":1,"field":"curvature","placements":)" + three + "}"));
    require(std::holds_alternative<xr::ControlMessage>(md) && std::get<xr::ControlMessage>(md).op == xr::ControlOp::placement_menu);
    const auto* mp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(md).payload);
    require(mp && mp->open && mp->desktop == 1 && mp->field == 3 && mp->placements[1].x == -1800 && mp->placements[1].curvature == 120 &&
            mp->placements[2].distance == 100 && !mp->surface);
    const char* fields[] = {"x", "y", "distance", "curvature"};
    for (std::uint32_t i = 0; i < std::size(fields); ++i) {
        auto fd = xr::decode_control(menu(R"({"open":false,"desktop":2,"field":")" + std::string(fields[i]) + R"(","placements":)" + three + "}"));
        const auto* fp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(fd).payload);
        require(fp && !fp->open && fp->desktop == 2 && fp->field == i);
    }
    {  // A held slider's surface: a four-key placement like the rest.
        auto sd = xr::decode_control(menu(R"({"open":true,"desktop":1,"field":"distance","placements":)" + three + R"(,"surface":)" + edge + "}"));
        const auto* sp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(sd).payload);
        require(sp && sp->surface && sp->surface->x == -1800 && sp->surface->y == 900 && sp->surface->distance == 200 &&
                sp->surface->curvature == 120);
    }
    {  // One placement per desktop (1..kMaxDesktops) and the desktop row: names, ultrawide, armed Remove, busy.
        auto one = xr::decode_control(menu(R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "]}"));
        const auto* op = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(one).payload);
        require(op && op->count == 1 && op->numbers[0] == 1 && !op->wide && !op->armed && !op->busy);
        auto row = xr::decode_control(menu(R"({"open":true,"desktop":3,"field":"x","placements":[)" + flat + "," + edge + "," + flat + "," + flat +
                                           R"(],"names":[1,2,3,4],"wide":true,"armed":true,"busy":true})"));
        const auto* rp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(row).payload);
        require(rp && rp->count == 4 && rp->desktop == 3 && rp->wide && rp->armed && rp->busy && rp->placements[1].x == -1800);
        auto gap = xr::decode_control(menu(R"({"open":true,"desktop":1,"field":"x","placements":[)" + flat + "," + flat + R"(],"names":[2,4]})"));
        const auto* gp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(gap).payload);
        require(gp && gp->count == 2 && gp->numbers[0] == 2 && gp->numbers[1] == 4);
    }
    {  // The run's desktops changed: the set to capture (sorted), and the ones to restart. Anything else stops the renderer.
        auto set = [](const std::string& payload) {
            return std::string(R"({"schema":1,"request_id":12,"run_id":"run","session":"session","op":"desktops","payload":)") + payload + "}\n";
        };
        auto dd = xr::decode_control(set(R"({"outputs":["XR-4","XR-1"],"renew":["XR-4"]})"));
        const auto* dp = std::get_if<xr::DesktopsPayload>(&std::get<xr::ControlMessage>(dd).payload);
        require(dp && dp->outputs == std::vector<std::string>({"XR-1", "XR-4"}) && dp->renew == std::vector<std::string>({"XR-4"}));
        auto plain = xr::decode_control(set(R"({"outputs":["XR-2"]})"));
        require(std::holds_alternative<xr::ControlMessage>(plain) && std::get<xr::DesktopsPayload>(std::get<xr::ControlMessage>(plain).payload).renew.empty());
        require(!std::get<xr::DesktopsPayload>(std::get<xr::ControlMessage>(plain).payload).ease);  // only when asked
        auto eased = xr::decode_control(set(R"({"outputs":["XR-2"],"renew":["XR-2"],"ease":true})"));  // Ctrl+Alt+R
        require(std::get<xr::DesktopsPayload>(std::get<xr::ControlMessage>(eased).payload).ease);
        require(!std::get<xr::DesktopsPayload>(std::get<xr::ControlMessage>(xr::decode_control(set(R"({"outputs":["XR-2"],"ease":false})"))).payload).ease);
        for (const char* bad : {R"({})", R"({"outputs":[]})", R"({"outputs":["XR-5"]})", R"({"outputs":["XR-0"]})", R"({"outputs":["XR-1","XR-1"]})",
                                R"({"outputs":["DP-1"]})", R"({"outputs":["XR-1","XR-2","XR-3","XR-4","XR-1"]})", R"({"outputs":"XR-1"})",
                                R"({"outputs":["XR-1"],"renew":["XR-2"]})", R"({"outputs":["XR-1"],"extra":1})", R"({"outputs":["xr-1"]})",
                                R"({"outputs":["XR-10"]})", R"({"outputs":[1]})", R"({"outputs":["XR-1"],"ease":1})",
                                R"({"outputs":["XR-1"],"ease":"true"})", R"({"ease":true})"}) {
            auto rejected = xr::decode_control(set(bad));
            require(std::holds_alternative<xr::Error>(rejected) && !xr::ignorable_control_error(std::get<xr::Error>(rejected)));
        }
    }
    for (const std::string& bad : {std::string("{}"),  // past its placements
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"names":[1,2]})",
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"names":[1,3,3]})",
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"names":[1,2,5]})",
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"wide":1})",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + flat + "," + flat + "," + flat + "," + flat + "]}",
                                  R"({"open":true,"desktop":-1,"field":"x","placements":)" + three + "}",
                                  R"({"open":true,"desktop":1.0,"field":"x","placements":)" + three + "}",
                                  R"({"open":1,"desktop":0,"field":"x","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"z","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"X","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"yaw","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"pitch","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"roll","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":0,"placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + with("curvature", "121") + "," + flat + "]}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + R"(,{"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0,"curvature":0},)" + flat + "]}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"surface":{"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0,"curvature":0}})",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + flat + ",1]}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":)" + three + R"(,"extra":1})",
                                  R"({"open":true,"desktop":0,"placements":)" + three + "}"}) {
        auto ignored = xr::decode_control(menu(bad));
        require(std::holds_alternative<xr::Error>(ignored) && xr::ignorable_control_error(std::get<xr::Error>(ignored)));
    }
    // Framing problems are not ignorable, even on a placement_menu frame.
    for (const auto& fatal : {xr::decode_control(menu("[]")), xr::decode_control(menu("{}", "placement-menu")),
                              xr::decode_control(menu(R"({"open":true,"desktop":0,"field":"x","placements":)" + three + "}").substr(1))})
        require(std::holds_alternative<xr::Error>(fatal) && !xr::ignorable_control_error(std::get<xr::Error>(fatal)));
    for (const std::string& bad : {wire().substr(1), std::string(R"({"schema":2})") + "\n"})
        require(!xr::ignorable_control_error(std::get<xr::Error>(xr::decode_control(bad))));
    {  // A dropped menu frame does not consume its request id; later frames still go through.
        xr::ControlQueue q("run", "session");
        auto bad = q.push(menu(R"({"open":true,"desktop":9,"field":"x","placements":)" + three + "}"));
        require(std::holds_alternative<xr::Error>(bad) && xr::ignorable_control_error(std::get<xr::Error>(bad)));
        require(std::holds_alternative<std::monostate>(q.push(menu(R"({"open":true,"desktop":2,"field":"x","placements":)" + three + "}"))));
        require(q.pop().has_value());
    }
    {
        auto frame = menu(R"({"open":true,"desktop":0,"field":"x","placements":)" + three + "}");
        frame.replace(frame.find("\"run\""), 5, "\"other\"");
        xr::ControlQueue q("run", "session");
        require(std::holds_alternative<xr::Error>(q.push(frame)));  // identity is still checked
    }
    {  // The key bind panel (binds_panel.hpp BindPanelState): display only; a malformed payload is dropped, never fatal.
        auto binds = [](const std::string& payload) {
            return "{\"schema\":1,\"request_id\":1,\"run_id\":\"run\",\"session\":\"session\",\"op\":\"binds\",\"payload\":" + payload + "}\n";
        };
        const std::string row = R"({"action":"Next desktop","keys":"Ctrl+Alt+Right","note":"","state":"normal"})";
        const std::string open = R"({"visible":true,"title":"Key binds","rows":[)" + row +
            R"(,{"action":"Overview","keys":"Ctrl+Alt+Delete","note":"Off: Hyprland binds Ctrl+Alt+Delete","state":"conflict"}],)"
            R"("selected":1,"hint":"Up/Down pick","status":"","alert":true})";
        const auto shown = xr::decode_control(binds(open));
        require(std::holds_alternative<xr::ControlMessage>(shown));
        const auto& m = std::get<xr::ControlMessage>(shown);
        require(m.op == xr::ControlOp::binds);
        const auto& p = std::get<xr::BindPanelState>(m.payload);
        require(p.visible && p.title == "Key binds" && p.rows.size() == 2 && p.selected == 1 && p.alert && p.status.empty());
        require(p.rows[1].state == xr::BindRowState::conflict && p.rows[1].note == "Off: Hyprland binds Ctrl+Alt+Delete");
        require(p.rows[0].keys == "Ctrl+Alt+Right" && p.rows[0].state == xr::BindRowState::normal);
        for (const char* state : {"custom", "capture"}) {
            auto r = row; r.replace(r.find("normal"), 6, state);
            const auto ok = xr::decode_control(binds(R"({"visible":true,"title":"K","rows":[)" + r + R"(],"selected":0,"hint":"h","status":"s","alert":false})"));
            require(std::holds_alternative<xr::ControlMessage>(ok));
        }
        const auto hidden = xr::decode_control(binds(R"({"visible":false})"));
        require(std::holds_alternative<xr::ControlMessage>(hidden) && !std::get<xr::BindPanelState>(std::get<xr::ControlMessage>(hidden).payload).visible);
        for (const auto& bad : {std::string(R"({"visible":false,"rows":[]})"), std::string(R"({})"), std::string(R"({"visible":1})"),
                                std::string(R"({"visible":true,"title":"K","rows":[],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[)" + row + R"(],"selected":1,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"","rows":[)" + row + R"(],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[)" + row + R"(],"selected":0,"hint":"","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[)" + row + R"(],"selected":0,"hint":"h","status":"","alert":0})"),
                                std::string(R"({"visible":true,"title":"K","rows":[)" + row + R"(],"selected":0,"hint":"h","status":"","alert":false,"x":1})"),
                                std::string(R"({"visible":true,"title":"K","rows":[{"action":"A","keys":"B","note":"","state":"loud"}],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[{"action":"A","keys":"","note":"","state":"normal"}],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[{"action":"A","keys":"B","state":"normal"}],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[{"action":"\u00e9","keys":"B","note":"","state":"normal"}],"selected":0,"hint":"h","status":"","alert":false})"),
                                std::string(R"({"visible":true,"title":"K","rows":[{"action":"A","keys":"B","note":")") + std::string(97, 'n') +
                                    R"(","state":"conflict"}],"selected":0,"hint":"h","status":"","alert":false})"}) {
            const auto dropped = xr::decode_control(binds(bad));
            require(std::holds_alternative<xr::Error>(dropped) && xr::ignorable_control_error(std::get<xr::Error>(dropped)));
        }
        std::string many = R"({"visible":true,"title":"K","rows":[)";
        for (std::size_t i = 0; i <= xr::kMaxBindRows; ++i) many += (i ? "," : "") + row;
        const auto too_many = xr::decode_control(binds(many + R"(],"selected":0,"hint":"h","status":"","alert":false})"));
        require(std::holds_alternative<xr::Error>(too_many) && xr::ignorable_control_error(std::get<xr::Error>(too_many)));
    }
    std::cout << "strict framing, schema, duplicate/nonfinite rejection, queue/replay, view, recenter, help, placement, placement_menu, binds: PASS\n";
}
