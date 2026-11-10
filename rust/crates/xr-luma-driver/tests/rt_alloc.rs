//! The hot path, both modes: nothing on any thread may touch the heap once running. Alone in its
//! own test binary, because the probe counts every allocation in the process and other tests
//! running in parallel would allocate.

#[path = "../src/bin/luma_rt_bench/alloc_probe.rs"]
mod alloc_probe;

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use xr_luma_driver::rt::*;

#[derive(Debug, Clone, Copy, Default)]
struct Numbered {
    n: u64,
}

struct CountSink(Arc<AtomicU64>);
impl Sink<Numbered> for CountSink {
    fn publish(&mut self, i: &Numbered) {
        let seen = self.0.load(Ordering::Relaxed);
        assert_eq!(i.n, seen); // only formats (allocates) on failure
        self.0.store(seen + 1, Ordering::Release);
    }
}

struct Released {
    seen: Arc<AtomicU64>,
    released: Arc<AtomicU64>,
    n: u64,
}
impl Source for Released {
    type Item = Numbered;
    fn next(&mut self, out: &mut Numbered) -> SourceStatus {
        // Paced by the sink: a burst of 20000 would overrun the ring, and an overrun item never
        // reaches the sink.
        if self.n >= self.released.load(Ordering::Acquire)
            || self.n > self.seen.load(Ordering::Acquire) + 100
        {
            thread::yield_now();
            return SourceStatus::Idle;
        }
        out.n = self.n;
        self.n += 1;
        SourceStatus::Item
    }
}

#[test]
fn hot_path_never_allocates() {
    for mode in [PipelineMode::Ring, PipelineMode::Inline] {
        let seen = Arc::new(AtomicU64::new(0));
        let released = Arc::new(AtomicU64::new(0));
        let src = Released {
            seen: Arc::clone(&seen),
            released: Arc::clone(&released),
            n: 0,
        };
        let o = Options {
            mode,
            ..Options::default()
        };
        let p: Pipeline<_, _> = Pipeline::start(src, CountSink(Arc::clone(&seen)), &o).unwrap();
        released.store(100, Ordering::Release);
        while seen.load(Ordering::Acquire) < 100 {
            std::hint::spin_loop();
        }
        let before = alloc_probe::alloc_count();
        released.store(20_000, Ordering::Release);
        while seen.load(Ordering::Acquire) < 20_000 {
            std::hint::spin_loop();
        }
        let after = alloc_probe::alloc_count();
        let (_, _, s) = p.stop();
        assert_eq!(after, before, "{mode:?}: {} allocations", after - before);
        assert_eq!(s.items_published, 20_000);
    }
    // ...and the probe itself sees allocations, or the zero above means nothing.
    let before = alloc_probe::alloc_count();
    drop(std::hint::black_box(Box::new([0u8; 64])));
    assert!(alloc_probe::alloc_count() > before);
}
