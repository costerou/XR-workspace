//! The two hot threads and the hand-over between them. Not hot itself: `start` and `stop` allocate
//! and block; only the loops inside the threads are.

use super::policy::{RealSysOps, ThreadPolicy, ThreadPolicyResult, apply_thread_policy};
use super::spsc::{Consumer, Producer, spsc_ring};
use super::{CachePadded, Parker, prefault_stack};
use std::io;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc;
use std::thread::{self, JoinHandle};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum PipelineMode {
    /// Reader thread -> SPSC ring -> fusion thread. A slow sink can never delay the reader.
    #[default]
    Ring,
    /// The reader thread runs the sink itself: one thread, no hand-over, no second wake-up.
    Inline,
}

#[derive(Debug, Clone, Copy)]
pub struct Options {
    pub mode: PipelineMode,
    pub reader: ThreadPolicy,
    pub fusion: ThreadPolicy,
    /// How long fusion sleeps at most; only matters for noticing a stop.
    pub fusion_wait_ms: u32,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            mode: PipelineMode::Ring,
            reader: ThreadPolicy::default(),
            fusion: ThreadPolicy::default(),
            fusion_wait_ms: 100,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PipelineStats {
    pub items_read: u64,
    pub items_published: u64,
    /// Items the ring had no room for: lost, and the reason to look at fusion.
    pub ring_overruns: u64,
    /// Most items fusion found waiting in one wake-up.
    pub max_backlog: u64,
    pub fusion_sleeps: u64,
}

pub const RT_STACK_BYTES: usize = 512 * 1024;
pub const RT_STACK_PREFAULT: usize = 256 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SourceStatus {
    /// `item` holds a new sample.
    Item,
    /// Nothing yet (a timeout): the reader checks for a stop and asks again.
    Idle,
    /// The source is finished.
    Stop,
}

/// Runs on the reader thread. `next` blocks in the transport with a timeout of its own (a stop
/// waits for it) and stamps the item with [`super::monotonic_raw_ns`] as soon as the read returns.
pub trait Source {
    type Item: Copy + Default + Send + 'static;
    fn next(&mut self, item: &mut Self::Item) -> SourceStatus;
}

/// Called once per item, in order, on the fusion thread (or the reader thread when inline). It
/// must not block: this is where fusion runs and the seqlock is written, so each sample is
/// published at once; nothing here ever batches them. A stop publishes everything already read.
pub trait Sink<Item> {
    fn publish(&mut self, item: &Item);
}

/// One writer per counter: a plain load and store, no locked instruction.
#[inline]
fn bump(counter: &AtomicU64) {
    counter.store(counter.load(Ordering::Relaxed) + 1, Ordering::Relaxed);
}

#[derive(Default)]
struct ReaderCounters {
    read: AtomicU64,
    overruns: AtomicU64,
}

#[derive(Default)]
struct FusionCounters {
    published: AtomicU64,
    backlog: AtomicU64,
    sleeps: AtomicU64,
}

#[derive(Default)]
struct Shared {
    stop: AtomicBool,
    reader_done: AtomicBool,
    parker: Parker,
    reader: CachePadded<ReaderCounters>, // the reader's line
    fusion: CachePadded<FusionCounters>, // fusion's line
}

struct Ready {
    fusion: bool,
    policy: ThreadPolicyResult,
    tid: libc::pid_t,
}

/// Applies the policy, prefaults the stack and reports in. Run first on each hot thread.
fn enter(policy: &ThreadPolicy, fusion: bool, ready: mpsc::SyncSender<Ready>) {
    let policy = apply_thread_policy(policy, &RealSysOps);
    prefault_stack::<RT_STACK_PREFAULT>();
    // SAFETY: plain system call.
    let tid = unsafe { libc::gettid() };
    let _ = ready.send(Ready {
        fusion,
        policy,
        tid,
    });
}

fn spawn<T: Send + 'static>(
    name: &str,
    body: impl FnOnce() -> T + Send + 'static,
) -> io::Result<JoinHandle<T>> {
    thread::Builder::new()
        .name(name.into())
        .stack_size(RT_STACK_BYTES)
        .spawn(body)
        .map_err(|e| io::Error::new(e.kind(), format!("cannot start {name}: {e}")))
}

enum ReaderOut<Snk, Item, const N: usize> {
    Inline(Snk),
    Ring(Producer<Item, N>),
}

fn reader_main<Src: Source, Snk: Sink<Src::Item>, const N: usize>(
    shared: &Shared,
    source: &mut Src,
    out: &mut ReaderOut<Snk, Src::Item, N>,
) {
    let mut item = Src::Item::default();
    while !shared.stop.load(Ordering::Acquire) {
        match source.next(&mut item) {
            SourceStatus::Stop => break,
            SourceStatus::Idle => continue,
            SourceStatus::Item => {}
        }
        bump(&shared.reader.read);
        match out {
            ReaderOut::Inline(sink) => {
                sink.publish(&item);
                bump(&shared.fusion.published);
            }
            ReaderOut::Ring(ring) => {
                if ring.try_push(item) {
                    shared.parker.notify();
                } else {
                    // Never wait here: the reader's only job is to keep draining the kernel's queue.
                    bump(&shared.reader.overruns);
                }
            }
        }
    }
    shared.reader_done.store(true, Ordering::Release);
    shared.parker.notify();
}

fn fusion_main<Item: Copy, Snk: Sink<Item>, const N: usize>(
    shared: &Shared,
    ring: &mut Consumer<Item, N>,
    sink: &mut Snk,
    wait_ms: u32,
) {
    loop {
        // Read before draining: once the reader is done nothing more arrives, so this drain gets
        // everything.
        let done = shared.reader_done.load(Ordering::Acquire);
        let mut drained = 0u64;
        while let Some(item) = ring.try_pop() {
            sink.publish(&item);
            bump(&shared.fusion.published);
            drained += 1;
        }
        if drained > shared.fusion.backlog.load(Ordering::Relaxed) {
            shared.fusion.backlog.store(drained, Ordering::Relaxed);
        }
        if done {
            return;
        }
        bump(&shared.fusion.sleeps);
        shared.parker.wait(
            || !ring.is_empty() || shared.reader_done.load(Ordering::Acquire),
            wait_ms,
        );
    }
}

/// A running reader (and, in ring mode, fusion) thread. Owns the source and the sink until
/// [`Pipeline::stop`] hands them back. Dropping it stops it.
pub struct Pipeline<Src: Source, Snk: Sink<Src::Item>, const N: usize = 256> {
    shared: Arc<Shared>,
    reader: Option<JoinHandle<(Src, Option<Snk>)>>,
    fusion: Option<JoinHandle<Snk>>,
    reader_policy: ThreadPolicyResult,
    fusion_policy: ThreadPolicyResult,
    reader_tid: libc::pid_t,
    fusion_tid: libc::pid_t,
}

impl<Src, Snk, const N: usize> Pipeline<Src, Snk, N>
where
    Src: Source + Send + 'static,
    Snk: Sink<Src::Item> + Send + 'static,
{
    /// Starts the threads and returns once each has applied its policy, so the policy results and
    /// thread ids are final. On error nothing is left running; the source and sink are dropped.
    pub fn start(source: Src, sink: Snk, options: &Options) -> io::Result<Self> {
        let shared = Arc::new(Shared::default());
        let (ready_tx, ready_rx) = mpsc::sync_channel(2);
        let mut fusion = None;
        let reader_out = match options.mode {
            PipelineMode::Inline => ReaderOut::Inline(sink),
            PipelineMode::Ring => {
                let (producer, mut consumer) = spsc_ring::<Src::Item, N>();
                let shared = Arc::clone(&shared);
                let policy = options.fusion;
                let wait_ms = options.fusion_wait_ms;
                let ready = ready_tx.clone();
                let mut sink = sink;
                fusion = Some(spawn("luma-fusion", move || {
                    enter(&policy, true, ready);
                    fusion_main(&shared, &mut consumer, &mut sink, wait_ms);
                    sink
                })?);
                ReaderOut::Ring(producer)
            }
        };
        let reader = {
            let shared = Arc::clone(&shared);
            let policy = options.reader;
            let ready = ready_tx;
            let mut source = source;
            let mut out = reader_out;
            spawn("luma-reader", move || {
                enter(&policy, false, ready);
                reader_main(&shared, &mut source, &mut out);
                match out {
                    ReaderOut::Inline(sink) => (source, Some(sink)),
                    ReaderOut::Ring(_) => (source, None),
                }
            })
        };
        let reader = match reader {
            Ok(handle) => handle,
            Err(e) => {
                if let Some(fusion) = fusion {
                    shared.reader_done.store(true, Ordering::Release);
                    shared.parker.notify();
                    let _ = fusion.join();
                }
                return Err(e);
            }
        };
        let mut pipeline = Self {
            shared,
            reader: Some(reader),
            fusion,
            reader_policy: ThreadPolicyResult::default(),
            fusion_policy: ThreadPolicyResult::default(),
            reader_tid: 0,
            fusion_tid: 0,
        };
        let threads = if pipeline.fusion.is_some() { 2 } else { 1 };
        for _ in 0..threads {
            // A thread that dies before reporting in drops its sender; the join in stop reports it.
            let Ok(ready) = ready_rx.recv() else { break };
            if ready.fusion {
                pipeline.fusion_policy = ready.policy;
                pipeline.fusion_tid = ready.tid;
            } else {
                pipeline.reader_policy = ready.policy;
                pipeline.reader_tid = ready.tid;
            }
        }
        Ok(pipeline)
    }

    /// Stops the reader (after its current `next` returns), lets fusion publish everything already
    /// read, joins both and hands back the source, the sink and the final counts.
    pub fn stop(mut self) -> (Src, Snk, PipelineStats) {
        let (source, sink) = self.join();
        (source, sink, self.stats())
    }

    fn join(&mut self) -> (Src, Snk) {
        self.shared.stop.store(true, Ordering::Release);
        let reader = self.reader.take().expect("joined once");
        let (source, inline_sink) = reader
            .join()
            .unwrap_or_else(|e| std::panic::resume_unwind(e));
        let sink = match self.fusion.take() {
            Some(fusion) => fusion
                .join()
                .unwrap_or_else(|e| std::panic::resume_unwind(e)),
            None => inline_sink.expect("inline mode keeps the sink on the reader"),
        };
        (source, sink)
    }
}

impl<Src: Source, Snk: Sink<Src::Item>, const N: usize> Pipeline<Src, Snk, N> {
    pub fn stats(&self) -> PipelineStats {
        let s = &self.shared;
        PipelineStats {
            items_read: s.reader.read.load(Ordering::Relaxed),
            items_published: s.fusion.published.load(Ordering::Relaxed),
            ring_overruns: s.reader.overruns.load(Ordering::Relaxed),
            max_backlog: s.fusion.backlog.load(Ordering::Relaxed),
            fusion_sleeps: s.fusion.sleeps.load(Ordering::Relaxed),
        }
    }
    pub fn reader_policy(&self) -> &ThreadPolicyResult {
        &self.reader_policy
    }
    pub fn fusion_policy(&self) -> &ThreadPolicyResult {
        &self.fusion_policy
    }
    pub fn reader_tid(&self) -> libc::pid_t {
        self.reader_tid
    }
    /// 0 when inline: there is no fusion thread.
    pub fn fusion_tid(&self) -> libc::pid_t {
        self.fusion_tid
    }
    /// The reader has stopped (the source said so, or [`Pipeline::stop`] asked it to).
    pub fn reader_done(&self) -> bool {
        self.shared.reader_done.load(Ordering::Acquire)
    }
}

impl<Src: Source, Snk: Sink<Src::Item>, const N: usize> Drop for Pipeline<Src, Snk, N> {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        // Join quietly: a panic from a thread is reported by `stop`, never from a destructor.
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
        if let Some(fusion) = self.fusion.take() {
            let _ = fusion.join();
        }
    }
}
