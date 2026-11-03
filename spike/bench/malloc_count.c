// LD_PRELOAD allocation counter for capture_e2e_bench (spike/bench/loopback_compositor.cpp).
// Counts every malloc/calloc/realloc/memalign-family call of the process (all threads, Mesa's included) into a
// shared page the harness reads: the memfd named by XR_MALLOC_COUNT_FD (word 0: count; word 1: nonzero while the
// harness measures; word 2: set when the shim loaded). With XR_MALLOC_TRACE=<file> it also records the call stacks of allocations made while measuring
// and writes them, with /proc/self/maps, to that file at exit (symbolise with bench/malloc_report.py).
// Without either variable the shim only forwards.
#define _GNU_SOURCE
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern void* __libc_malloc(size_t);
extern void* __libc_calloc(size_t, size_t);
extern void* __libc_realloc(void*, size_t);
extern void* __libc_memalign(size_t, size_t);
extern void __libc_free(void*);

static _Atomic uint64_t* page;
static _Atomic uint64_t fallback[3];
static const char* trace_path;

enum { kDepth = 8, kSlots = 8192 };
struct Site { _Atomic uint64_t hash; _Atomic uint64_t count; void* frames[kDepth]; int depth; };
static struct Site* sites;
static _Thread_local int in_trace;

__attribute__((constructor)) static void xr_malloc_count_init(void) {
    const char* fd = getenv("XR_MALLOC_COUNT_FD");
    if (fd && *fd) {
        void* p = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, atoi(fd), 0);
        if (p != MAP_FAILED) page = p;
    }
    if (page && page != fallback) atomic_store(&page[2], 0x786d616c6c6f63ull);  // "xmalloc": tells the harness the shim loaded
    if (!page) page = fallback;
    trace_path = getenv("XR_MALLOC_TRACE");
    if (trace_path && *trace_path) {
        void* p = mmap(NULL, sizeof(struct Site) * kSlots, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p != MAP_FAILED) sites = p;
        in_trace = 1;  // warm up backtrace() (it may load libgcc_s and allocate) outside any recording
        void* warm[4]; backtrace(warm, 4);
        in_trace = 0;
    }
}
static void record(void) {
    if (!sites || in_trace || !atomic_load_explicit(&page[1], memory_order_relaxed)) return;
    in_trace = 1;
    void* frames[kDepth + 2];
    const int n = backtrace(frames, kDepth + 2);
    uint64_t h = 1469598103934665603ull;
    for (int i = 2; i < n; ++i) { h ^= (uint64_t)(uintptr_t)frames[i]; h *= 1099511628211ull; }
    if (!h) h = 1;
    for (uint64_t k = 0; k < kSlots; ++k) {
        struct Site* s = &sites[(h + k) % kSlots];
        uint64_t expected = 0;
        if (atomic_load(&s->hash) == h || atomic_compare_exchange_strong(&s->hash, &expected, h)) {
            if (atomic_fetch_add(&s->count, 1) == 0) {
                s->depth = n > 2 ? n - 2 : 0;
                memcpy(s->frames, frames + 2, sizeof(void*) * (size_t)s->depth);
            }
            break;
        }
    }
    in_trace = 0;
}
static inline void count(void) {
    atomic_fetch_add_explicit(page ? &page[0] : &fallback[0], 1, memory_order_relaxed);
    if (sites) record();
}

__attribute__((destructor)) static void xr_malloc_count_dump(void) {
    if (!sites || !trace_path) return;
    in_trace = 1;
    FILE* out = fopen(trace_path, "w");
    if (!out) return;
    for (int i = 0; i < kSlots; ++i) {
        if (!atomic_load(&sites[i].count)) continue;
        fprintf(out, "site %llu", (unsigned long long)atomic_load(&sites[i].count));
        for (int d = 0; d < sites[i].depth; ++d) fprintf(out, " %p", sites[i].frames[d]);
        fputc('\n', out);
    }
    int maps = open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
    if (maps >= 0) {
        char buf[4096]; ssize_t n;
        fputs("maps\n", out);
        while ((n = read(maps, buf, sizeof buf)) > 0) fwrite(buf, 1, (size_t)n, out);
        close(maps);
    }
    fclose(out);
}

void* malloc(size_t n) { count(); return __libc_malloc(n); }
void* calloc(size_t a, size_t b) { count(); return __libc_calloc(a, b); }
void* realloc(void* p, size_t n) { count(); return __libc_realloc(p, n); }
void free(void* p) { __libc_free(p); }
void* memalign(size_t a, size_t n) { count(); return __libc_memalign(a, n); }
void* aligned_alloc(size_t a, size_t n) { count(); return __libc_memalign(a, n); }
int posix_memalign(void** out, size_t a, size_t n) {
    count();
    void* p = __libc_memalign(a, n);
    if (!p) return ENOMEM;
    *out = p; return 0;
}
