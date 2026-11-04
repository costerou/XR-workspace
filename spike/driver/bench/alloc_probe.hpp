#pragma once
// Counts every heap allocation in the process, from any thread, including ones libc makes for itself: replacing malloc catches
// more than replacing operator new. Include from exactly one .cpp of a test or bench, never from the driver.
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace luma::bench {
inline std::atomic<std::uint64_t> g_allocs{0};
inline std::uint64_t alloc_count() noexcept { return g_allocs.load(std::memory_order_relaxed); }
}

#if !defined(__SANITIZE_ADDRESS__) && !defined(__SANITIZE_THREAD__)
#define LUMA_ALLOC_PROBE 1
extern "C" {
void* __libc_malloc(std::size_t) noexcept;
void* __libc_calloc(std::size_t, std::size_t) noexcept;
void* __libc_realloc(void*, std::size_t) noexcept;
void* __libc_memalign(std::size_t, std::size_t) noexcept;
void* malloc(std::size_t n) noexcept {
    luma::bench::g_allocs.fetch_add(1, std::memory_order_relaxed);
    return __libc_malloc(n);
}
void* calloc(std::size_t n, std::size_t size) noexcept {
    luma::bench::g_allocs.fetch_add(1, std::memory_order_relaxed);
    return __libc_calloc(n, size);
}
void* realloc(void* p, std::size_t n) noexcept {
    luma::bench::g_allocs.fetch_add(1, std::memory_order_relaxed);
    return __libc_realloc(p, n);
}
void* aligned_alloc(std::size_t alignment, std::size_t n) noexcept {
    luma::bench::g_allocs.fetch_add(1, std::memory_order_relaxed);
    return __libc_memalign(alignment, n);
}
int posix_memalign(void** out, std::size_t alignment, std::size_t n) noexcept {
    luma::bench::g_allocs.fetch_add(1, std::memory_order_relaxed);
    void* p = __libc_memalign(alignment, n);
    if (!p) return ENOMEM;
    *out = p;
    return 0;
}
}
#else
#define LUMA_ALLOC_PROBE 0
#endif
