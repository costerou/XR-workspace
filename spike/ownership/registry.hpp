#pragma once
#include <nlohmann/json.hpp>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>
namespace xr::ownership {
using Json = nlohmann::json;
Json parse_request(const std::string&);
bool validDesktop(int w,int h,double scale);
// cursor_shape (plugin.cpp): `name`, a requested cursor's name, if it is one plain word (1..64 of A-Za-z0-9_.-, starting with
// a letter or digit), else nothing: whatever the compositor holds, the broker never reports more than that.
std::string cursorShapeName(const std::string& name);
struct Object { virtual ~Object() = default; bool live=true, physical=false; };
using Handle=std::shared_ptr<Object>;
using Capture=std::function<void(Handle,bool)>;
struct Backend {
    virtual ~Backend()=default;
    virtual bool collision(const Json&)=0;
    virtual bool create(const Json&,const Capture&)=0;
    virtual bool configure(const Handle&,const Json&)=0;
    virtual bool verified(const Handle&,const Json&)=0;
    virtual bool usable(const Handle&)=0;
    virtual bool evacuate(const Handle&,const Handle&)=0;
    virtual bool reachable(const Handle&,const Handle&)=0;
    virtual bool destroy(const Handle&)=0;
    virtual bool cleanup(const Handle&,const Json&)=0;
    // Called once an entry is proven dead: drop compositor references (workspaces,
    // snapshots) so recovered runs leave nothing listed. Identity/readback data stays.
    virtual void release(const Handle&)=0;
    // A live managed output takes new geometry (a pill's Ultrawide toggle, or a neighbour's change moving it along the
    // row): `after` replaces `before` (same id, name, index and refresh). Only the plugin implements it; elsewhere refused.
    virtual bool reshape(const Handle&,const Json& before,const Json& after) { (void)before; (void)after; return false; }
};
// A DRM binding is not a physical/virtual classification or VM attestation.
struct PairObservation {
    std::string name;
    uint32_t deviceMajor=0, deviceMinor=0, connector=0;
    bool operator==(const PairObservation&) const = default;
};
struct PairBackend {
    virtual ~PairBackend()=default;
    virtual Handle selectPairOutput(const std::string&)=0;
    // Must reject disconnected, replaced, non-DRM, disabled, DPMS-off or mirrored outputs.
    virtual PairObservation observePairOutput(const Handle&)=0;
};
class PairRegistry {
    struct Pair {
        std::string session,targetToken,fallbackToken;
        Handle target,fallback;
        PairObservation targetProof,fallbackProof;
        bool valid=true;
    };
    PairBackend& backend;
    std::function<std::string()> token;
    std::map<std::string,Pair> pairs;
    bool busy=false;
    Json observe(Pair&);
public:
    PairRegistry(PairBackend& b,std::function<std::string()> t):backend(b),token(std::move(t)) {}
    bool contains(const std::string& run) const { return pairs.contains(run); }
    // Retained fallback object of a valid pair; lifecycle never re-selects by name.
    Handle fallback(const std::string& run) const;
    // Full target+fallback proof; any failure latches the pair invalid.
    void revalidate(const std::string& run,const std::string& session);
    // Takes only parse_request-validated schema-2 requests; never dispatches lifecycle mutations.
    Json handle(const Json&,const std::string& epoch);
};
// `blocked`: the step the last refused removal stopped at (unavailable, evacuate, unreachable, rule, destroy).
struct Entry { std::string session,run; Json spec; Handle object; std::string state="pending"; std::string blocked={}; };
class Registry {
    Backend& backend;
    bool busy=false;
    std::map<std::string,Entry> entries;
    Entry& lookup(const std::string&,const std::string&,const std::string&);
public:
    explicit Registry(Backend& b): backend(b) {}
    std::string create(const std::string&,const std::string&,const Json&);
    std::string inspect(const std::string&,const std::string&,const std::string&);
    std::string remove(const std::string&,const std::string&,const std::string&,const Handle&);
    // New geometry for a live entry of this run (see Backend::reshape); its state after readback: "live" if the output
    // verifies against `spec` (which becomes its spec), else "invalid" (recovery still removes it).
    std::string reshape(const std::string&,const std::string&,const Json&);
    const Entry& entry(const std::string& id) const { return entries.at(id); }
    // This run's outputs in state "live" and still alive (cursor_shape asks which one the pointer is on). Only reads.
    std::vector<Handle> liveOutputs(const std::string& session,const std::string& run) const;
};
}
