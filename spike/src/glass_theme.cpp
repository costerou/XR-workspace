#include "xr/glass_theme.hpp"
#include <cstddef>

namespace xr {
float glass_luma(std::array<float, 3> c) { return 0.2126f * c[0] + 0.7152f * c[1] + 0.0722f * c[2]; }

const MaterialScheme& glass_theme_scheme(std::array<float, 3> accent) {
    struct Entry { std::array<float, 3> accent{}; MaterialScheme scheme; bool used = false; };
    thread_local std::array<Entry, kGlassSchemeCache> cache;
    thread_local std::size_t next = 0;
    for (const auto& e : cache)
        if (e.used && e.accent == accent) return e.scheme;
    auto& e = cache[next];
    next = (next + 1) % cache.size();
    e = {accent, material_scheme(accent), true};
    return e.scheme;
}
}
