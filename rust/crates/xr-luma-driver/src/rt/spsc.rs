//! Lock-free hand-over from the reader to fusion: one producer, one consumer, a fixed array, no
//! system calls. Hot. Each side keeps a private copy of the other's index, so the two cores only
//! exchange a cache line when that copy has run out.

use super::CachePadded;
use std::cell::UnsafeCell;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

struct Ring<T, const N: usize> {
    tail: CachePadded<AtomicUsize>, // the producer's line
    head: CachePadded<AtomicUsize>, // the consumer's line
    slots: [UnsafeCell<T>; N],
}

// SAFETY: a slot is written only by the one Producer while it lies outside head..tail, and read
// only by the one Consumer while it lies inside; the release store of the index that moves it
// across the boundary orders the slot access against the other side's acquire load.
unsafe impl<T: Send, const N: usize> Sync for Ring<T, N> {}

/// The writing end. Not `Clone`: there is only ever one.
pub struct Producer<T, const N: usize> {
    ring: Arc<Ring<T, N>>,
    cached_head: usize,
}

/// The reading end. Not `Clone`: there is only ever one.
pub struct Consumer<T, const N: usize> {
    ring: Arc<Ring<T, N>>,
    cached_tail: usize,
}

/// A ring of `N` slots (a power of two). Allocates once, here; never again.
pub fn spsc_ring<T: Copy + Default + Send, const N: usize>() -> (Producer<T, N>, Consumer<T, N>) {
    const {
        assert!(
            N != 0 && N.is_power_of_two(),
            "capacity must be a power of two"
        )
    };
    let ring = Arc::new(Ring {
        tail: CachePadded(AtomicUsize::new(0)),
        head: CachePadded(AtomicUsize::new(0)),
        slots: std::array::from_fn(|_| UnsafeCell::new(T::default())),
    });
    (
        Producer {
            ring: Arc::clone(&ring),
            cached_head: 0,
        },
        Consumer {
            ring,
            cached_tail: 0,
        },
    )
}

impl<T: Copy, const N: usize> Producer<T, N> {
    /// False when the ring is full; the value is not stored. Hot.
    #[inline]
    pub fn try_push(&mut self, value: T) -> bool {
        let ring = &*self.ring;
        let tail = ring.tail.load(Ordering::Relaxed);
        if tail.wrapping_sub(self.cached_head) == N {
            self.cached_head = ring.head.load(Ordering::Acquire);
            if tail.wrapping_sub(self.cached_head) == N {
                return false;
            }
        }
        // SAFETY: the slot at `tail` is outside head..tail, so the consumer does not touch it.
        unsafe { *ring.slots[tail & (N - 1)].get() = value };
        ring.tail.store(tail.wrapping_add(1), Ordering::Release);
        true
    }
}

impl<T: Copy, const N: usize> Consumer<T, N> {
    /// The oldest value, or `None` when the ring is empty. Hot.
    #[inline]
    pub fn try_pop(&mut self) -> Option<T> {
        let ring = &*self.ring;
        let head = ring.head.load(Ordering::Relaxed);
        if head == self.cached_tail {
            self.cached_tail = ring.tail.load(Ordering::Acquire);
            if head == self.cached_tail {
                return None;
            }
        }
        // SAFETY: the slot at `head` is inside head..tail, so the producer does not touch it.
        let value = unsafe { *ring.slots[head & (N - 1)].get() };
        ring.head.store(head.wrapping_add(1), Ordering::Release);
        Some(value)
    }

    /// Whether nothing is waiting. Hot.
    #[inline]
    pub fn is_empty(&self) -> bool {
        self.ring.head.load(Ordering::Acquire) == self.ring.tail.load(Ordering::Acquire)
    }
}
