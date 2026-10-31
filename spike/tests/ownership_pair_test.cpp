#include "registry.hpp"
#include <iostream>
#include <stdexcept>
using namespace xr::ownership;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(0)
struct Outputs : PairBackend {
    std::map<std::string,Handle> outputs{{"Virtual-1",std::make_shared<Object>()},{"Virtual-2",std::make_shared<Object>()}};
    int selections=0;
    Handle selectPairOutput(const std::string& name) override { ++selections; return outputs.at(name); }
    PairObservation observePairOutput(const Handle& h) override {
        for(auto& [name,object]:outputs) if(object==h && h->live)
            return {name,226,0,name=="Virtual-1" ? 31U:32U};
        throw std::runtime_error("lost object");
    }
};
int main() {
    Json hello={{"schema",2},{"session","guest"},{"run_id","run"},{"request_id",1},
        {"epoch","unknown"},{"op","pair_hello"},{"payload",{{"mode","isolated-vm"},{"target_name","Virtual-1"},{"fallback_name","Virtual-2"}}}};
    CHECK(parse_request("xrspike "+hello.dump())==hello);
    for(const auto& field:{"schema","op","payload"}) {
        auto bad=hello; bad.erase(field); bool rejected=false;
        try { parse_request("xrspike "+bad.dump()); } catch(...) { rejected=true; }
        CHECK(rejected);
    }
    for(int fault=0;fault<13;++fault) {
        auto bad=hello;
        switch(fault) {
        case 0: bad["schema"]=1; break;
        case 1: bad["schema"]=2.0; break;
        case 2: bad["schema"]=true; break;
        case 3: bad["payload"]["mode"]="normal"; break;
        case 4: bad["payload"]["extra"]=true; break;
        case 5: bad["payload"]["target_name"]=std::string(129,'a'); break;
        case 6: bad["payload"]["target_name"]=Json::array(); break;
        case 7: bad["request_id"]=18446744073709551615ULL; break;
        case 8: bad["request_id"]=-1; break;
        case 9: bad["op"]="create"; break;
        case 10: bad["payload"]["fallback_name"]="bad/name"; break;
        case 11: bad["session"]=false; break;
        case 12: bad["schema"]=3; break;
        }
        bool rejected=false; try {parse_request("xrspike "+bad.dump());} catch(...) {rejected=true;} CHECK(rejected);
    }
    for(auto wire: {"xrspike "+hello.dump().insert(1,"\"schema\":2,"),std::string("xrspike ")+std::string(8193,' ')}) {
        bool rejected=false; try {parse_request(wire);} catch(...) {rejected=true;} CHECK(rejected);
    }
    Outputs outputs; unsigned tokens=0;
    PairRegistry pairs(outputs,[&]{return std::string(47,'a')+std::to_string(++tokens);});
    auto request=[&](const Json& j) {return pairs.handle(parse_request("xrspike "+j.dump()),"epoch");};
    auto proof=request(hello);
    CHECK(proof["target"]["handle"]!=proof["fallback"]["handle"]);
    CHECK(proof["target"]["connector_id"]==31);
    CHECK(proof["target"]["classification"]=="unclassified");
    Json observe=hello; observe["op"]="pair_observe"; observe["epoch"]="epoch";
    observe["payload"]={{"mode","isolated-vm"},{"target",proof["target"]["handle"]},{"fallback",proof["fallback"]["handle"]}};
    for(const auto& badToken: {std::string("a"),std::string(48,'G'),std::string(49,'a')}) {
        auto bad=observe; bad["payload"]["target"]=badToken;
        bool rejected=false; try {parse_request("xrspike "+bad.dump());} catch(...) {rejected=true;} CHECK(rejected);
    }
    CHECK(request(observe)==proof); CHECK(outputs.selections==2);
    for(int fault=0;fault<5;++fault) {
        auto bad=observe;
        if(fault==0) bad["epoch"]="other";
        if(fault==1) bad["session"]="other";
        if(fault==2) bad["run_id"]="other";
        if(fault==3) bad["payload"]["target"]=proof["fallback"]["handle"];
        if(fault==4) bad["payload"]["fallback"]=proof["target"]["handle"];
        bool rejected=false; try {request(bad);} catch(...) {rejected=true;} CHECK(rejected);
        CHECK(outputs.selections==2 && tokens==2);
    }
    for(auto name:{"Virtual-1","Virtual-2"}) {
        auto original=outputs.outputs.at(name);
        outputs.outputs[name]=std::make_shared<Object>(); // Same name AND connector; wrong object.
        bool rejected=false; try { request(observe); } catch(...) {rejected=true;} CHECK(rejected);
        outputs.outputs[name]=original;
    }
    bool latched=false; try {request(hello);} catch(...) {latched=true;} CHECK(latched);
    PairRegistry restarted(outputs,[&]{++tokens; return "should-not-run";});
    bool rejected=false; try {restarted.handle(parse_request("xrspike "+observe.dump()),"epoch");} catch(...) {rejected=true;}
    CHECK(rejected && outputs.selections==2 && tokens==2);
    std::cout << "pair wire and registry passed\n";
}
