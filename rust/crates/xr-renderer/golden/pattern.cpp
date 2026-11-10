// Writes golden/pattern.txt from the C++ pattern (spike/src/presentation.cpp): width height phase FNV-1a of the
// frame's little-endian pixels. From the repository root:
//   g++ -std=c++20 -Ispike/include rust/crates/xr-renderer/golden/pattern.cpp spike/src/presentation.cpp -o /tmp/pattern-golden && /tmp/pattern-golden > rust/crates/xr-renderer/golden/pattern.txt
#include "xr/presentation.hpp"
#include <cstdio>
#include <vector>
int main() {
    const struct { std::uint32_t w, h; std::uint64_t phase; } cases[] = {
        {1, 1, 0}, {64, 48, 0}, {300, 130, 7}, {129, 200, 1000}, {1920, 1080, 123}, {9000, 2, 3}};
    for (const auto& c : cases) {
        std::vector<std::uint32_t> px(std::size_t(c.w) * c.h);
        xr::render_animated_pattern(px.data(), c.w, c.h, c.w, c.phase);
        std::uint64_t h = 0xcbf29ce484222325ull;
        for (auto p : px) for (int i = 0; i < 4; ++i) { h ^= (p >> (8 * i)) & 0xff; h *= 0x100000001b3ull; }
        std::printf("%u %u %llu %llu\n", c.w, c.h, (unsigned long long)c.phase, (unsigned long long)h);
    }
}
