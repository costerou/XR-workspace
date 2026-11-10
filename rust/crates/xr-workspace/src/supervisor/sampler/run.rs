//! The sampler's thread: [`start`] runs [`Sampler::step`] until [`Handle::stop`], waiting between steps as each one
//! says ([`Wait`]). A [`Idle::Block`] wait lasts until [`Waker::wake`]; an [`Idle::Until`] one until that time on the
//! sampler's clock or a wake, whichever is first; a sampling wait is `secs`. A wake that comes while a step runs ends
//! the wait after it at once, so none is lost.

use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::{Clock, Idle, SampledPointer, Sampler, SamplerClient};

#[derive(Default)]
struct Signal {
    woken: bool,
    stop: bool,
    names: Option<Vec<String>>,
}

#[derive(Default)]
struct Shared {
    signal: Mutex<Signal>,
    changed: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Signal> {
        // A panic in the thread leaves nothing half-written in a Signal.
        self.signal.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn poke(&self, f: impl FnOnce(&mut Signal)) {
        let mut s = self.lock();
        f(&mut s);
        s.woken = true;
        drop(s);
        self.changed.notify_all();
    }
}

/// Ends the sampler's idle wait. The pointer's owner calls it whenever what the pointer reports may have changed: the
/// overview opened or closed, restore sampling started or stopped, a want or a pan was set.
#[derive(Clone)]
pub struct Waker(Arc<Shared>);

impl Waker {
    pub fn wake(&self) {
        self.0.poke(|_| {});
    }
}

/// The running sampler thread.
pub struct Handle<P: SampledPointer, C: SamplerClient, K: Clock> {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<Sampler<P, C, K>>>,
}

/// The sampler's thread, started (the Python's `start()`).
pub fn start<P, C, K>(mut sampler: Sampler<P, C, K>) -> Handle<P, C, K>
where
    P: SampledPointer + 'static,
    Sampler<P, C, K>: Send,
    C: SamplerClient + 'static,
    K: Clock + 'static,
{
    let shared = Arc::new(Shared::default());
    let theirs = Arc::clone(&shared);
    let thread = thread::Builder::new()
        .name("xr-sampler".into())
        .spawn(move || {
            run(&mut sampler, &theirs);
            sampler
        })
        .expect("spawn the sampler thread");
    Handle {
        shared,
        thread: Some(thread),
    }
}

fn run<P: SampledPointer, C: SamplerClient, K: Clock>(sampler: &mut Sampler<P, C, K>, shared: &Shared) {
    loop {
        {
            let mut s = shared.lock();
            if s.stop {
                return;
            }
            s.woken = false;
            if let Some(names) = s.names.take() {
                sampler.set_names(names);
            }
        }
        let wait = sampler.step();
        let limit = match wait.idle {
            Idle::No => Some(wait.secs),
            Idle::Block => None,
            Idle::Until(t) => Some((t - sampler.clock.now()).max(wait.secs)),
        };
        let s = shared.lock();
        let quiet = |s: &mut Signal| !s.woken && !s.stop;
        // Poisoned or not, the guard is dropped here: the next iteration locks again.
        match limit {
            None => drop(shared.changed.wait_while(s, quiet)),
            Some(secs) => {
                let secs = Duration::from_secs_f64(secs.max(0.0));
                drop(shared.changed.wait_timeout_while(s, secs, quiet));
            }
        }
    }
}

impl<P: SampledPointer, C: SamplerClient, K: Clock> Handle<P, C, K> {
    /// A [`Waker`] for the pointer's owner.
    pub fn waker(&self) -> Waker {
        Waker(Arc::clone(&self.shared))
    }

    pub fn wake(&self) {
        self.shared.poke(|_| {});
    }

    /// The run's desktops changed: the thread reads their monitors again on its next step, which starts at once.
    pub fn set_names(&self, names: Vec<String>) {
        self.shared.poke(|s| s.names = Some(names));
    }

    /// The thread stopped and joined, then the binds removed and the cursor shown (the Python's `stop()`). The
    /// sampler comes back for its counters; `None` if its thread panicked (nothing could be undone then).
    pub fn stop(mut self) -> Option<Sampler<P, C, K>> {
        self.finish()
    }

    fn finish(&mut self) -> Option<Sampler<P, C, K>> {
        let thread = self.thread.take()?;
        self.shared.poke(|s| s.stop = true);
        let mut sampler = thread.join().ok()?;
        sampler.stop();
        Some(sampler)
    }
}

impl<P: SampledPointer, C: SamplerClient, K: Clock> Drop for Handle<P, C, K> {
    fn drop(&mut self) {
        self.finish();
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::time::Instant;

    use super::super::{
        CallError, Mode, Monotonic, PanPlan, SampledPointer, Side, Variant, Want,
    };
    use super::*;
    use crate::hyprland::Query;
    use crate::resize::OutputRect;
    use crate::supervisor::columns::ColumnsClient;
    use crate::supervisor::overview_cursor::CursorClient;

    struct NoPlan;

    impl PanPlan for NoPlan {
        type Frame = ();
        fn home(&self) -> i64 {
            0
        }
        fn target(&self) -> i64 {
            0
        }
        fn side(&self) -> Side {
            Side::Left
        }
        fn exit(&self) -> f64 {
            0.0
        }
        fn entry(&self) -> f64 {
            0.0
        }
        fn total_px(&self) -> f64 {
            0.0
        }
        fn frame(&self, _: f64) {}
    }

    /// A pointer that only says whether the overview is open.
    struct Pointer(Arc<AtomicBool>);

    impl SampledPointer for Pointer {
        type Plan = NoPlan;
        fn overview(&mut self) -> bool {
            self.0.load(Ordering::SeqCst)
        }
        fn restore_sampling(&mut self) -> bool {
            false
        }
        fn restore_release(&mut self) {}
        fn want(&mut self) -> Want {
            Want {
                serial: 0,
                kind: Mode::Away,
                point: None,
            }
        }
        fn binds_dropped(&mut self) -> bool {
            false
        }
        fn clear_binds_dropped(&mut self) {}
        fn mode(&mut self) -> Mode {
            Mode::Away
        }
        fn feed(&mut self, _: Option<(i64, i64)>, _: (f64, f64), _: &[OutputRect], _: i64) {}
        fn handles_on(&mut self) -> bool {
            false
        }
        fn sync(&mut self) -> i64 {
            0
        }
        fn shape_gated(&mut self) -> bool {
            false
        }
        fn shape_pushed(&mut self) -> bool {
            false
        }
        fn shape_serial(&mut self) -> i64 {
            0
        }
        fn feed_shape(&mut self, _: Option<&str>, _: i64) {}
        fn set_shape_wired(&mut self, _: bool) {}
        fn reloads(&mut self) -> i64 {
            0
        }
        fn selected(&mut self) -> i64 {
            0
        }
        fn set_pan(&mut self, _: Option<&()>) {}
        fn request_switch(&mut self, _: i64, _: i64, _: bool) {}
        fn pan_plan(&mut self, _: i64, _: Side, _: f64, _: &OutputRect) -> Option<NoPlan> {
            None
        }
        fn neighbour(&mut self, _: Side, _: f64) -> Option<i64> {
            None
        }
        fn can_switch(&mut self) -> bool {
            false
        }
        fn can_scroll(&mut self) -> bool {
            false
        }
    }

    /// Counts every compositor call; `monitors` is empty (a run with no XR monitor).
    #[derive(Clone, Default)]
    struct Counts {
        cursor: Arc<AtomicUsize>,
        other: Arc<AtomicUsize>,
    }

    struct Client(Counts);

    impl Client {
        fn call(&self) -> Result<(), CallError> {
            self.0.other.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }

    impl CursorClient for Client {
        fn hide_overview_cursor(&mut self) -> Result<(), CallError> {
            self.call()
        }
        fn show_overview_cursor(&mut self) -> Result<(), CallError> {
            self.call()
        }
    }

    impl ColumnsClient for Client {
        fn query(&mut self, _: Query, _: Duration) -> Result<serde_json::Value, CallError> {
            self.call().map(|()| serde_json::json!([]))
        }
        fn scroll_to_window(&mut self, _: &str) -> Result<(), CallError> {
            self.call()
        }
    }

    impl SamplerClient for Client {
        fn cursor_pos(&mut self, _: Duration) -> Result<Option<(f64, f64)>, CallError> {
            self.0.cursor.fetch_add(1, Ordering::SeqCst);
            Ok(Some((10.0, 10.0)))
        }
        fn grab_pointer(&mut self, _: &[String], _: i64, _: i64) -> Result<(), CallError> {
            self.call()
        }
        fn lock_pointer(&mut self, _: &[String], _: i64, _: i64) -> Result<(), CallError> {
            self.call()
        }
        fn release_pointer(&mut self, _: Option<i64>, _: Option<i64>) -> Result<(), CallError> {
            self.call()
        }
        fn warp_cursor(&mut self, _: i64, _: i64) -> Result<(), CallError> {
            self.call()
        }
        fn remove_resize_binds(&mut self) -> Result<(), CallError> {
            self.call()
        }
        fn enter_desktop(
            &mut self,
            _: &[String],
            _: Option<i64>,
            _: Option<i64>,
            _: Option<&str>,
        ) -> Result<(), CallError> {
            self.call()
        }
        fn arm_click_binds(&mut self, _: &[String]) -> Result<(), CallError> {
            self.call()
        }
        fn confine_cursor(&mut self, _: i64, _: i64, _: &str) -> Result<(), CallError> {
            self.call()
        }
        fn scrolls(&self) -> bool {
            false
        }
    }

    fn total(c: &Counts) -> usize {
        c.cursor.load(Ordering::SeqCst) + c.other.load(Ordering::SeqCst)
    }

    /// Until `done`, or panics after 2 s.
    fn eventually(done: impl Fn() -> bool) {
        let end = Instant::now() + Duration::from_secs(2);
        while !done() {
            assert!(Instant::now() < end, "timed out");
            thread::sleep(Duration::from_millis(1));
        }
    }

    fn idle_then_wake(variant: Variant) {
        let overview = Arc::new(AtomicBool::new(false));
        let counts = Counts::default();
        let sampler = Sampler::new(
            variant,
            Client(counts.clone()),
            vec!["XR-1".into()],
            Pointer(Arc::clone(&overview)),
            Vec::new(),
            super::super::SAMPLE_S,
            None,
            Monotonic,
        );
        let handle = start(sampler);
        // The first step shows the cursor (its state starts unknown) and, unified, reads the monitors; then nothing.
        let first = if variant == Variant::Unified { 2 } else { 1 };
        eventually(|| total(&counts) >= first);
        thread::sleep(Duration::from_millis(300));
        assert_eq!(total(&counts), first, "{variant:?}: calls while idle");
        assert_eq!(counts.cursor.load(Ordering::SeqCst), 0);

        // The overview opens: one wake and the samples start at once, at the sampling cadence.
        overview.store(true, Ordering::SeqCst);
        let woken = Instant::now();
        handle.wake();
        eventually(|| counts.cursor.load(Ordering::SeqCst) >= 1);
        assert!(woken.elapsed() < Duration::from_millis(50), "{variant:?}: slow wake");
        eventually(|| counts.cursor.load(Ordering::SeqCst) >= 20);

        // Closed again: it goes back to waiting (no wake needed to notice: sampling steps are short).
        overview.store(false, Ordering::SeqCst);
        thread::sleep(Duration::from_millis(50));
        let settled = counts.cursor.load(Ordering::SeqCst);
        thread::sleep(Duration::from_millis(300));
        assert_eq!(counts.cursor.load(Ordering::SeqCst), settled);
        let sampler = handle.stop().expect("the thread ran");
        assert_eq!(sampler.cursor.hidden, Some(false));
    }

    #[test]
    fn plain_makes_no_call_while_idle_and_wakes_when_the_overview_opens() {
        idle_then_wake(Variant::Plain);
    }

    #[test]
    fn locked_makes_no_call_while_idle_and_wakes_when_the_overview_opens() {
        idle_then_wake(Variant::Locked);
    }

    #[test]
    fn unified_without_xr_monitors_makes_no_call_while_idle_and_wakes() {
        idle_then_wake(Variant::Unified);
    }

    #[test]
    fn set_names_wakes_and_rereads_the_monitors() {
        let counts = Counts::default();
        let sampler = Sampler::new(
            Variant::Unified,
            Client(counts.clone()),
            vec!["XR-1".into()],
            Pointer(Arc::new(AtomicBool::new(false))),
            Vec::new(),
            super::super::SAMPLE_S,
            None,
            Monotonic,
        );
        let handle = start(sampler);
        eventually(|| total(&counts) >= 2);
        thread::sleep(Duration::from_millis(50));
        handle.set_names(vec!["XR-2".into()]);
        eventually(|| total(&counts) >= 3);
        let sampler = handle.stop().unwrap();
        assert_eq!(sampler.names, vec!["XR-2".to_string()]);
        assert!(!sampler.names_changed);
    }
}
