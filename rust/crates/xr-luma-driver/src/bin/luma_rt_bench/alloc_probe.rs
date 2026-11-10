//! Counts every heap allocation in the process, from any thread, including ones libc makes for
//! itself: replacing `malloc` catches more than a `#[global_allocator]` would. Include from
//! exactly one test or bench binary (`#[path]`), never from the driver. The port of
//! `spike/driver/bench/alloc_probe.hpp`.

use std::ffi::{c_int, c_void};
use std::sync::atomic::{AtomicU64, Ordering};

static ALLOCS: AtomicU64 = AtomicU64::new(0);

pub fn alloc_count() -> u64 {
    ALLOCS.load(Ordering::Relaxed)
}

#[inline(always)]
fn counted() {
    ALLOCS.fetch_add(1, Ordering::Relaxed);
}

unsafe extern "C" {
    fn __libc_malloc(n: usize) -> *mut c_void;
    fn __libc_calloc(n: usize, size: usize) -> *mut c_void;
    fn __libc_realloc(p: *mut c_void, n: usize) -> *mut c_void;
    fn __libc_memalign(alignment: usize, n: usize) -> *mut c_void;
}

// SAFETY (all below): same contract as the libc functions they replace; each forwards to glibc's
// own implementation, so `free` still matches.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn malloc(n: usize) -> *mut c_void {
    counted();
    unsafe { __libc_malloc(n) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn calloc(n: usize, size: usize) -> *mut c_void {
    counted();
    unsafe { __libc_calloc(n, size) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn realloc(p: *mut c_void, n: usize) -> *mut c_void {
    counted();
    unsafe { __libc_realloc(p, n) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn memalign(alignment: usize, n: usize) -> *mut c_void {
    counted();
    unsafe { __libc_memalign(alignment, n) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn aligned_alloc(alignment: usize, n: usize) -> *mut c_void {
    counted();
    unsafe { __libc_memalign(alignment, n) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn posix_memalign(
    out: *mut *mut c_void,
    alignment: usize,
    n: usize,
) -> c_int {
    counted();
    let p = unsafe { __libc_memalign(alignment, n) };
    if p.is_null() {
        return libc::ENOMEM;
    }
    unsafe { *out = p };
    0
}
