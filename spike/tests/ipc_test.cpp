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
    auto view_decoded = xr::decode_control(view(R"({"zoomed":true,"selected":2,"spacing":40,"mode":"follow","cursor_follow":true,"prediction":true})"));
    require(std::holds_alternative<xr::ControlMessage>(view_decoded));
    auto& message = std::get<xr::ControlMessage>(view_decoded);
    require(message.op == xr::ControlOp::view);
    auto& v = std::get<xr::ViewPayload>(message.payload);
    require(v.zoomed && v.selected == 2 && v.spacing == 40 && v.mode == 2 && v.cursor_follow && v.prediction);
    for (const char* bad : {R"({"zoomed":true,"selected":0,"spacing":0})", R"({"zoomed":true,"selected":0,"spacing":0,"mode":"orbit"})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":1})", R"({"zoomed":1,"selected":0,"spacing":0,"mode":"anchor"})", R"({"zoomed":true,"selected":3,"spacing":0})",
                            R"({"zoomed":true,"selected":0,"spacing":401})", R"({"zoomed":true,"selected":-1,"spacing":0})",
                            R"({"zoomed":true,"selected":0})", R"({"zoomed":true,"selected":0,"spacing":0,"x":1})",
                            R"({"zoomed":true,"selected":0.5,"spacing":0})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor"})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"x":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":1})",
                            R"({"zoomed":true,"selected":0,"spacing":0,"mode":"anchor","cursor_follow":false,"prediction":false,"x":1})"})
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
    // Per-desktop placement: absolute, 1..3 desktops, exact integer fields inside the mirrored bounds.
    auto placement = [](const std::string& payload) {
        return std::string(R"({"schema":1,"request_id":10,"run_id":"run","session":"session","op":"placement","payload":)") + payload + "}\n";
    };
    const std::string flat = R"({"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0,"curvature":0})";
    const std::string edge = R"({"x":-100,"y":100,"distance":200,"yaw":-60,"pitch":45,"roll":-45,"curvature":120})";
    auto pd = xr::decode_control(placement(R"({"desktops":[)" + flat + "," + edge + "]}"));
    require(std::holds_alternative<xr::ControlMessage>(pd) && std::get<xr::ControlMessage>(pd).op == xr::ControlOp::placement);
    const auto& pp = std::get<xr::PlacementPayload>(std::get<xr::ControlMessage>(pd).payload);
    require(pp.desktops.size() == 2 && pp.desktops[0].distance == 100 && pp.desktops[1].x == -100 && pp.desktops[1].y == 100 &&
            pp.desktops[1].distance == 200 && pp.desktops[1].yaw == -60 && pp.desktops[1].pitch == 45 && pp.desktops[1].roll == -45 &&
            pp.desktops[1].curvature == 120);
    auto with = [&](const std::string& key, const std::string& value) {
        auto p = flat; const auto at = p.find("\"" + key + "\":") + key.size() + 3;
        return p.substr(0, at) + value + p.substr(p.find_first_of(",}", at));
    };
    for (const std::string& bad : {std::string(R"({"desktops":[]})"), std::string(R"({"desktops":{}})"), std::string(R"({})"),
                                  R"({"desktops":[)" + flat + "," + flat + "," + flat + "," + flat + "]}",
                                  R"({"desktops":[)" + flat + R"(],"extra":1})",
                                  R"({"desktops":[)" + with("x", "101") + "]}", R"({"desktops":[)" + with("distance", "49") + "]}",
                                  R"({"desktops":[)" + with("yaw", "61") + "]}", R"({"desktops":[)" + with("pitch", "-46") + "]}",
                                  R"({"desktops":[)" + with("roll", "46") + "]}", R"({"desktops":[)" + with("curvature", "-1") + "]}",
                                  R"({"desktops":[)" + with("curvature", "121") + "]}", R"({"desktops":[)" + with("x", "1.5") + "]}",
                                  R"({"desktops":[)" + with("x", "true") + "]}", R"({"desktops":[)" + with("x", "\"1\"") + "]}",
                                  R"({"desktops":[)" + with("x", "18446744073709551615") + "]}",
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0}]})"),
                                  std::string(R"({"desktops":[{"x":0,"y":0,"distance":100,"yaw":0,"pitch":0,"roll":0,"curvature":0,"z":0}]})")})
        require(std::holds_alternative<xr::Error>(xr::decode_control(placement(bad))));
    // Placement menu: display-only state. Valid payloads decode; a malformed payload in a well-framed message is
    // rejected with the ignorable invalid_menu code (dropped, never fatal); malformed framing is fatal as before.
    auto menu = [](const std::string& payload, const char* op = "placement_menu") {
        return std::string(R"({"schema":1,"request_id":11,"run_id":"run","session":"session","op":")") + op + R"(","payload":)" + payload + "}\n";
    };
    const std::string three = "[" + flat + "," + edge + "," + flat + "]";
    auto md = xr::decode_control(menu(R"({"open":true,"desktop":1,"field":"pitch","placements":)" + three + "}"));
    require(std::holds_alternative<xr::ControlMessage>(md) && std::get<xr::ControlMessage>(md).op == xr::ControlOp::placement_menu);
    const auto* mp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(md).payload);
    require(mp && mp->open && mp->desktop == 1 && mp->field == 4 && mp->placements[1].x == -100 && mp->placements[1].curvature == 120 &&
            mp->placements[2].distance == 100);
    const char* fields[] = {"x", "y", "distance", "yaw", "pitch", "roll", "curvature"};
    for (std::uint32_t i = 0; i < 7; ++i) {
        auto fd = xr::decode_control(menu(R"({"open":false,"desktop":2,"field":")" + std::string(fields[i]) + R"(","placements":)" + three + "}"));
        const auto* fp = std::get_if<xr::PlacementMenuPayload>(&std::get<xr::ControlMessage>(fd).payload);
        require(fp && !fp->open && fp->desktop == 2 && fp->field == i);
    }
    for (const std::string& bad : {std::string("{}"),
                                  R"({"open":true,"desktop":3,"field":"x","placements":)" + three + "}",
                                  R"({"open":true,"desktop":-1,"field":"x","placements":)" + three + "}",
                                  R"({"open":true,"desktop":1.0,"field":"x","placements":)" + three + "}",
                                  R"({"open":1,"desktop":0,"field":"x","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"z","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"X","placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":0,"placements":)" + three + "}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + flat + "]}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + flat + "," + flat + "," + flat + "]}",
                                  R"({"open":true,"desktop":0,"field":"x","placements":[)" + flat + "," + with("yaw", "61") + "," + flat + "]}",
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
    std::cout << "strict framing, schema, duplicate/nonfinite rejection, queue/replay, view, recenter, help, placement, placement_menu: PASS\n";
}
