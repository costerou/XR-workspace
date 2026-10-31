#pragma once
#include <nlohmann/json.hpp>
#include <functional>
#include <map>
#include <memory>
#include <string>
namespace xr::ownership {
using Json = nlohmann::json;
Json parse_request(const std::string&);
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
};
struct Entry { std::string session,run; Json spec; Handle object; std::string state="pending"; };
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
    const Entry& entry(const std::string& id) const { return entries.at(id); }
};
}
