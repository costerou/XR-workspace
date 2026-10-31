#include "registry.hpp"
#include <stdexcept>
#include <set>
#include <regex>
namespace xr::ownership {
Json parse_request(const std::string& wire) {
    if(wire.size()>8192 || !wire.starts_with("xrspike ")) throw std::runtime_error("request bounds/prefix");
    std::map<int,std::set<std::string>> keys;
    auto j=Json::parse(wire.substr(8),[&](int depth,Json::parse_event_t event,Json& v) {
        if(depth>4) throw std::runtime_error("nesting limit");
        if(event==Json::parse_event_t::object_start) keys[depth+1].clear();
        if(event==Json::parse_event_t::key && !keys[depth].insert(v.get<std::string>()).second)
            throw std::runtime_error("duplicate key");
        return true;
    });
    const std::set<std::string> fields={"schema","session","run_id","request_id","epoch","op","payload"};
    if(!j.is_object() || j.size()!=fields.size()) throw std::runtime_error("envelope");
    for(auto& key:fields) if(!j.contains(key)) throw std::runtime_error("missing field");
    if(!j["schema"].is_number_integer() || j["schema"]!=1 || !j["request_id"].is_number_unsigned() ||
       j["request_id"].get<uint64_t>()>2147483647 || !j["payload"].is_object()) throw std::runtime_error("types");
    for(auto key:{"session","run_id","epoch"})
        if(!j[key].is_string() || !std::regex_match(j[key].get<std::string>(),std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) throw std::runtime_error("token");
    auto op=j.at("op").get<std::string>(); auto& p=j["payload"];
    std::set<std::string> wanted;
    if(op=="hello") wanted={"fallback_name"};
    else if(op=="inspect") wanted={"id"};
    else if(op=="evacuate_remove") wanted={"id","name","fallback"};
    else if(op=="create") wanted={"id","name","index","width","height","refresh_millihz","scale","x","y","workspace","workspace_name"};
    else throw std::runtime_error("operation");
    if(p.size()!=wanted.size()) throw std::runtime_error("payload shape");
    for(auto& key:wanted) if(!p.contains(key)) throw std::runtime_error("payload field");
    for(auto key:{"id","name","fallback","fallback_name","workspace_name"}) if(p.contains(key)) {
        if(!p[key].is_string() || !std::regex_match(p[key].get<std::string>(),std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) throw std::runtime_error("payload token");
    }
    if(p.contains("id") && !std::regex_match(p["id"].get<std::string>(),std::regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))) throw std::runtime_error("UUID");
    if(op=="create") {
        for(auto key:{"index","width","height","refresh_millihz","x","y","workspace"})
            if(!p[key].is_number_integer() || (p[key].is_number_unsigned() ? p[key].get<uint64_t>()>2147483647ULL :
                (p[key].get<int64_t>() < -2147483648LL || p[key].get<int64_t>()>2147483647LL))) throw std::runtime_error("integer");
        if(p["index"]<0 || p["index"]>2 || p["width"]!=1920 || p["height"]!=1080 || p["refresh_millihz"]!=120000 || !p["scale"].is_number() || p["scale"]!=1 || p["workspace"]<=0) throw std::runtime_error("geometry/workspace");
        auto prefix="XR-SPIKE-"+j["run_id"].get<std::string>();
        auto index=std::to_string(p["index"].get<int>());
        if(p["name"]!=prefix+"-"+index || p["workspace_name"]!=prefix+"-workspace-"+index) throw std::runtime_error("run names");
    }
    return j;
}
namespace {
struct Guard { bool& busy; explicit Guard(bool& b):busy(b) { if(b) throw std::runtime_error("reentrancy"); b=true; } ~Guard(){busy=false;} };
}
Entry& Registry::lookup(const std::string& session,const std::string& run,const std::string& id) {
    auto it=entries.find(id);
    if(it==entries.end() || it->second.session!=session || it->second.run!=run)
        throw std::runtime_error("unproven UUID/session/run");
    return it->second;
}
std::string Registry::create(const std::string& session,const std::string& run,const Json& spec) {
    Guard guard(busy);
    auto id=spec.at("id").get<std::string>();
    if(entries.contains(id)) {
        auto& e=lookup(session,run,id);
        if(e.spec!=spec) throw std::runtime_error("UUID/spec collision");
        if(e.state=="live" && (!e.object->live || !backend.verified(e.object,e.spec))) e.state="invalid";
        return e.state;
    }
    for(const auto& [key, prior]:entries) {
        (void)key;
        if(prior.run==run && prior.spec.at("name")==spec.at("name")) throw std::runtime_error("run slot already used");
    }
    if(entries.size()>=96 || backend.collision(spec)) throw std::runtime_error("collision/capacity");
    auto& e=entries.emplace(id,Entry{session,run,spec,nullptr,"pending"}).first->second;
    unsigned emitted=0;
    bool good=true;
    const bool created=backend.create(spec,[&](Handle object,bool selected) {
        ++emitted;
        if(emitted!=1 || !selected || !object) { good=false; return; }
        e.object=std::move(object);
    });
    if(!created || emitted!=1 || !good || !e.object || !e.object->live) { e.state="failed"; return e.state; }
    if(!backend.configure(e.object,spec) || !e.object->live || !backend.verified(e.object,spec)) { e.state="failed"; return e.state; }
    e.state="live"; return e.state;
}
std::string Registry::inspect(const std::string& s,const std::string& r,const std::string& id) {
    Guard guard(busy); auto& e=lookup(s,r,id);
    if(e.state=="live" && (!e.object->live || !backend.verified(e.object,e.spec))) e.state="invalid";
    return e.state;
}
std::string Registry::remove(const std::string& s,const std::string& r,const std::string& id,const Handle& fallback) {
    Guard guard(busy); auto& e=lookup(s,r,id);
    if(e.state=="dead") return backend.usable(fallback) && backend.reachable(e.object,fallback) ? "dead" : "blocked";
    if(!e.object || !e.object->live || !backend.usable(fallback) || fallback==e.object) return "blocked";
    if(!backend.evacuate(e.object,fallback) || !e.object->live || !backend.usable(fallback) ||
       !backend.reachable(e.object,fallback)) return "blocked";
    if(!backend.cleanup(e.object,e.spec) || !e.object->live || !backend.usable(fallback) ||
       !backend.reachable(e.object,fallback)) return "blocked";
    backend.destroy(e.object);
    if(e.object->live || !backend.usable(fallback) || !backend.reachable(e.object,fallback)) return "blocked";
    e.state="dead"; return e.state;
}
}
