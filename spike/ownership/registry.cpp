#include "registry.hpp"
#include <stdexcept>
#include <set>
#include <regex>
#include <cmath>
namespace xr::ownership {
// Mirror of tools/native_spike/geometry.validate_desktop (presets are within the custom bounds).
bool validDesktop(int w,int h,double scale) {
    if(w<640 || w>7680 || h<480 || h>4320 || w%2 || h%2) return false;
    const long n=std::lround(scale*4);  // allowed scales (geometry.SCALES): quarter steps 1 .. 2, then 2.5 and 3
    if(n<4 || n>12 || (n>8 && n%2) || n/4.0!=scale) return false;
    return (long(w)*4)%n==0 && (long(h)*4)%n==0;
}
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
    if(!j["schema"].is_number_integer() || (j["schema"]!=1 && j["schema"]!=2) || !j["request_id"].is_number_unsigned() ||
       j["request_id"].get<uint64_t>()>2147483647 || !j["payload"].is_object()) throw std::runtime_error("types");
    for(auto key:{"session","run_id","epoch"})
        if(!j[key].is_string() || !std::regex_match(j[key].get<std::string>(),std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) throw std::runtime_error("token");
    auto op=j.at("op").get<std::string>(); auto& p=j["payload"];
    std::set<std::string> wanted;
    if(op=="hello" && j["schema"]==1) wanted={"fallback_name"};
    else if(op=="pair_hello" && j["schema"]==2) wanted={"target_name","fallback_name"};
    else if(op=="pair_observe" && j["schema"]==2) wanted={"target","fallback"};
    else if(op=="inspect") wanted={"id"};
    else if(op=="evacuate_remove") wanted={"id","name","fallback"};
    else if(op=="create" || op=="reshape") wanted={"id","name","index","width","height","refresh_millihz","scale","x","y"};
    else throw std::runtime_error("operation");
    // Schema 2 is the explicit isolated-VM scope: every request carries the mode.
    if(j["schema"]==2) {
        if(!p.contains("mode") || p["mode"]!="isolated-vm") throw std::runtime_error("pair mode");
        wanted.insert("mode");
    }
    if(p.size()!=wanted.size()) throw std::runtime_error("payload shape");
    for(auto& key:wanted) if(!p.contains(key)) throw std::runtime_error("payload field");
    for(auto key:{"id","name","fallback","fallback_name","target","target_name"}) if(p.contains(key)) {
        if(!p[key].is_string() || !std::regex_match(p[key].get<std::string>(),std::regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"))) throw std::runtime_error("payload token");
    }
    if(j["schema"]==2) for(auto key:{"target","fallback"}) if(p.contains(key))
        if(!std::regex_match(p[key].get<std::string>(),std::regex("[0-9a-f]{48}"))) throw std::runtime_error("pair handle");
    if(p.contains("id") && !std::regex_match(p["id"].get<std::string>(),std::regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))) throw std::runtime_error("UUID");
    if(op=="create" || op=="reshape") {
        for(auto key:{"index","width","height","refresh_millihz","x","y"})
            if(!p[key].is_number_integer() || (p[key].is_number_unsigned() ? p[key].get<uint64_t>()>2147483647ULL :
                (p[key].get<int64_t>() < -2147483648LL || p[key].get<int64_t>()>2147483647LL))) throw std::runtime_error("integer");
        // XR-1..XR-4: tools/native_spike/geometry.py MAX_DESKTOPS (spike/include/xr/desktops.hpp kMaxDesktops).
        if(p["index"]<0 || p["index"]>3 || p["refresh_millihz"]!=120000 ||
           !p["scale"].is_number() || !validDesktop(p["width"].get<int>(),p["height"].get<int>(),p["scale"].get<double>())) throw std::runtime_error("geometry");
        // Stable names: Omarchy gives each managed output its own persistent workspace block.
        if(p["name"]!="XR-"+std::to_string(p["index"].get<int>()+1)) throw std::runtime_error("output name");
    }
    return j;
}
namespace {
struct Guard { bool& busy; explicit Guard(bool& b):busy(b) { if(b) throw std::runtime_error("reentrancy"); b=true; } ~Guard(){busy=false;} };
}
Json PairRegistry::observe(Pair& p) {
    if(!p.valid) throw std::runtime_error("pair invalidated");
    try {
    if(!p.target || !p.fallback || !p.target->live || !p.fallback->live || p.target==p.fallback)
        throw std::runtime_error("pair lifetime");
    const auto t=backend.observePairOutput(p.target), f=backend.observePairOutput(p.fallback);
    if(t!=p.targetProof || f!=p.fallbackProof || !t.connector || !f.connector || !t.deviceMajor || !f.deviceMajor ||
       (t.deviceMajor==f.deviceMajor && t.deviceMinor==f.deviceMinor && t.connector==f.connector))
        throw std::runtime_error("pair identity changed/aliased");
    auto encode=[](const PairObservation& o,const std::string& token) -> Json {
        return {{"handle",token},{"name",o.name},{"device_major",o.deviceMajor},{"device_minor",o.deviceMinor},
            {"connector_id",o.connector},{"drm_eligible",true},{"classification","unclassified"},
            {"enabled",true},{"dpms",true},{"mirrored",false}};
    };
    return {{"mode","isolated-vm"},{"target",encode(t,p.targetToken)},{"fallback",encode(f,p.fallbackToken)}};
    } catch(...) {p.valid=false; throw;}
}
Json PairRegistry::handle(const Json& j,const std::string& epoch) {
    Guard guard(busy);
    const auto run=j.at("run_id").get<std::string>(), session=j.at("session").get<std::string>();
    const auto& p=j.at("payload");
    if(j.at("schema")!=2 || p.at("mode")!="isolated-vm") throw std::runtime_error("pair scope");
    if(j.at("op")=="pair_hello") {
        if(j.at("epoch")!="unknown" && j.at("epoch")!=epoch) throw std::runtime_error("pair epoch");
        if(!pairs.contains(run)) {
            if(pairs.size()>=32 || p.at("target_name")==p.at("fallback_name")) throw std::runtime_error("pair capacity/names");
            Pair pair;
            pair.session=session;
            pair.target=backend.selectPairOutput(p.at("target_name"));
            pair.fallback=backend.selectPairOutput(p.at("fallback_name"));
            pair.targetProof=backend.observePairOutput(pair.target);
            pair.fallbackProof=backend.observePairOutput(pair.fallback);
            pair.targetToken=token(); pair.fallbackToken=token();
            if(pair.targetToken==pair.fallbackToken) throw std::runtime_error("pair token collision");
            observe(pair);
            pairs.emplace(run,std::move(pair));
        }
        auto& pair=pairs.at(run);
        if(pair.session!=session || pair.targetProof.name!=p.at("target_name").get<std::string>() || pair.fallbackProof.name!=p.at("fallback_name").get<std::string>())
            throw std::runtime_error("pair scope changed");
        return observe(pair);
    }
    // Restoring saved authority is observe-only: no name lookup, token minting or hello fallback.
    if(j.at("op")!="pair_observe" || j.at("epoch")!=epoch || !pairs.contains(run)) throw std::runtime_error("pair authority lost");
    auto& pair=pairs.at(run);
    if(pair.session!=session || pair.targetToken!=p.at("target").get<std::string>() || pair.fallbackToken!=p.at("fallback").get<std::string>())
        throw std::runtime_error("pair handles lost");
    return observe(pair);
}
Handle PairRegistry::fallback(const std::string& run) const {
    auto it=pairs.find(run);
    if(it==pairs.end() || !it->second.valid) throw std::runtime_error("pair authority lost");
    return it->second.fallback;
}
void PairRegistry::revalidate(const std::string& run,const std::string& session) {
    Guard guard(busy);
    auto it=pairs.find(run);
    if(it==pairs.end() || it->second.session!=session) throw std::runtime_error("pair authority lost");
    observe(it->second);
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
    // A slot whose output was removed with proof ("dead": a pill's Remove) may be created again (a pill's Add takes the
    // lowest free XR-n, tools/native_spike/desktops.py); any other prior entry still holds it.
    for(const auto& [key, prior]:entries) {
        (void)key;
        if(prior.run==run && prior.state!="dead" && prior.spec.at("name")==spec.at("name")) throw std::runtime_error("run slot already used");
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
std::string Registry::reshape(const std::string& s,const std::string& r,const Json& spec) {
    Guard guard(busy); auto& e=lookup(s,r,spec.at("id").get<std::string>());
    for(auto key:{"name","index","refresh_millihz"}) if(spec.at(key)!=e.spec.at(key)) throw std::runtime_error("reshape identity");
    if(e.state!="live") return e.state;
    if(!e.object || !e.object->live || !backend.verified(e.object,e.spec)) { e.state="invalid"; return e.state; }
    if(spec==e.spec) return e.state;
    // From here the compositor may hold either geometry: a failed reshape is never retried or rolled back here.
    const bool reshaped=backend.reshape(e.object,e.spec,spec);
    e.spec=spec;
    if(!reshaped || !e.object->live || !backend.verified(e.object,e.spec)) e.state="invalid";
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
    // Once the output is gone, workspace references are never needed (the not-live
    // reachable() check only fails on workspaces still bound to the dead source).
    if(!e.object->live) backend.release(e.object);
    if(e.object->live || !backend.usable(fallback) || !backend.reachable(e.object,fallback)) return "blocked";
    e.state="dead"; return e.state;
}
}
