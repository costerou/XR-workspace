//! The overview pointer hosted by the Rust supervisor: one [`OverviewPointer`] (or [`UnifiedPointer`]) under one lock,
//! read by the sampler thread through [`SampledPointer`] and polled by the run loop through [`LoopHook`]. The Python's
//! `_lock` is this mutex; each sampler call takes it once, as the Python's attribute reads do.
//!
//! The Python loop polls the pointer on every pass (50 ms at the most). Here the loop polls it only when it is
//! signalled (a sample that changed something, a lookup answered, a pan or switch from the sampler) or when one of the
//! pointer's own timers is up ([`due`]): an idle close, a held press, a resting pan, a beam's wait, the restore band's
//! dwell and idle. Between them it sleeps.

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use super::edge_pan::{self, PanFrame, Plan};
use super::free_pointer::{HOLD_S, TEXT_BOUNCE_S};
use super::hook::{LoopHook, WakeFd};
use super::neighbors::Direction;
use super::overview_pointer::{Control, Drag, OVERLAY_IDLE_CLOSE_S, OverviewPointer};
use super::restore_band;
use super::sampler::{Clock, Mode, PanPlan, SampledPointer, Side, Want};
use super::unified_pointer::UnifiedPointer;
use crate::resize::OutputRect;

/// What the sampler's [`super::sampler::push::EdgePush`] reads of an `edge_pan.Plan`.
impl PanPlan for Plan {
    type Frame = PanFrame;
    fn home(&self) -> i64 {
        self.home as i64
    }
    fn target(&self) -> i64 {
        self.target as i64
    }
    fn side(&self) -> Side {
        side(self.side)
    }
    fn exit(&self) -> f64 {
        self.exit
    }
    fn entry(&self) -> f64 {
        self.entry
    }
    fn total_px(&self) -> f64 {
        self.total_px
    }
    fn frame(&self, travel: f64) -> PanFrame {
        Plan::frame(self, travel)
    }
}

fn side(d: Direction) -> Side {
    Side::parse(d.name()).expect("the same four sides")
}

fn direction(s: Side) -> Direction {
    Direction::parse(s.as_str()).expect("the same four sides")
}

/// The pointer both threads share.
pub struct SharedPointer<C: Control>(Arc<Mutex<OverviewPointer<C>>>);

impl<C: Control> Clone for SharedPointer<C> {
    fn clone(&self) -> Self {
        SharedPointer(Arc::clone(&self.0))
    }
}

impl<C: Control> SharedPointer<C> {
    pub fn overview(pointer: OverviewPointer<C>) -> Self {
        SharedPointer(Arc::new(Mutex::new(pointer)))
    }

    pub fn unified(pointer: UnifiedPointer<C>) -> Self {
        SharedPointer::overview(pointer.0)
    }

    /// The pointer, locked; a panic elsewhere while it was held leaves it as it was.
    pub fn lock(&self) -> MutexGuard<'_, OverviewPointer<C>> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The run loop's side, signalled through the pointer's own wake fd.
    pub fn hook(&self) -> PointerHook<C> {
        let wake = Arc::clone(self.lock().wake());
        PointerHook { pointer: self.clone(), wake, polled: f64::NEG_INFINITY }
    }
}

/// The sampler thread's reads and calls. The unified-only calls do nothing on a plain OverviewPointer (the locked
/// sampler never makes them).
impl<C: Control> SampledPointer for SharedPointer<C> {
    type Plan = Plan;
    fn overview(&mut self) -> bool {
        self.lock().overview()
    }
    fn restore_sampling(&mut self) -> bool {
        self.lock().restore_sampling()
    }
    fn restore_release(&mut self) {
        self.lock().restore_release()
    }
    fn want(&mut self) -> Want {
        self.lock().want
    }
    fn binds_dropped(&mut self) -> bool {
        self.lock().binds_dropped
    }
    fn clear_binds_dropped(&mut self) {
        self.lock().binds_dropped = false;
    }
    fn mode(&mut self) -> Mode {
        self.lock().mode
    }
    fn feed(&mut self, cursor: Option<(i64, i64)>, motion: (f64, f64), outputs: &[OutputRect], applied: i64) {
        self.lock().feed(cursor, motion, outputs, applied);
    }
    fn handles_on(&mut self) -> bool {
        self.lock().handles_on()
    }
    fn sync(&mut self) -> i64 {
        self.lock().sync
    }
    fn shape_gated(&mut self) -> bool {
        self.lock().shape_gated
    }
    fn shape_pushed(&mut self) -> bool {
        self.lock().shape_pushed
    }
    fn shape_serial(&mut self) -> i64 {
        self.lock().shape_serial()
    }
    fn feed_shape(&mut self, name: Option<&str>, serial: i64) {
        self.lock().feed_shape(name, Some(serial));
    }
    fn set_shape_wired(&mut self, wired: bool) {
        self.lock().shape_wired = wired;
    }
    fn reloads(&mut self) -> i64 {
        self.lock().control.reloads()
    }
    fn selected(&mut self) -> i64 {
        self.lock().control.state().selected as i64
    }
    fn set_pan(&mut self, frame: Option<&PanFrame>) {
        let mut p = self.lock();
        if p.ext.is_some() {
            p.set_pan(frame.copied());
        }
    }
    fn request_switch(&mut self, home: i64, target: i64, panned: bool) {
        let mut p = self.lock();
        if p.ext.is_some() && home >= 0 && target >= 0 {
            p.request_switch(home as usize, target as usize, panned);
        }
    }
    fn pan_plan(&mut self, target: i64, side: Side, along: f64, output: &OutputRect) -> Option<Plan> {
        let p = self.lock();
        if p.ext.is_none() || target < 0 {
            return None;
        }
        p.pan_plan(target as usize, direction(side), along, output)
    }
    fn neighbour(&mut self, side: Side, along: f64) -> Option<i64> {
        let p = self.lock();
        p.ext.as_ref()?;
        p.neighbour(direction(side), along).map(|i| i as i64)
    }
    fn can_switch(&mut self) -> bool {
        let p = self.lock();
        p.ext.is_some() && p.can_switch()
    }
    fn can_scroll(&mut self) -> bool {
        let p = self.lock();
        p.ext.is_some() && p.can_scroll()
    }
}

/// `due(pointer)`: when, on the pointer's clock, its next poll changes something though nothing signalled it: the
/// earliest of the times its `poll` compares `now` against. `now` itself while the pointer is in a mode its poll leaves
/// at once (the overview opened or closed under a still mouse: the Python's loop poll sees that at its next tick); the
/// plain poll always leaves it, so it never repeats. (A maximize geometry comes with a signal: set_maximize_geometry.)
pub fn due<C: Control>(p: &OverviewPointer<C>, now: f64) -> Vec<f64> {
    let mut at = vec![p.last_move + OVERLAY_IDLE_CLOSE_S];
    if let Some(Drag::Window { lifted: false, at: pressed, .. }) = &p.drag
        && p.window_drag
    {
        at.push(pressed + HOLD_S);
    }
    if let Some(u) = p.ext.as_deref() {
        if let (Some(c), true) = (u.click, p.window_drag) {
            at.push(c.at + HOLD_S);
        }
        if let Some(s) = u.span {
            at.push(s.moved + edge_pan::REST_S);
        }
    }
    let t = &p.text_hold;
    at.extend(t.until);
    if t.on {
        at.push(t.sweep);
    }
    at.extend(t.left.map(|left| left + TEXT_BOUNCE_S));
    if p.restore_sampling() {
        at.extend(p.dwell.since.map(|since| since + restore_band::DWELL_S));
        at.extend(p.zoom_held.map(|held| held + restore_band::HELD_MAX_S as f64));
        if p.mode == Mode::Restore {
            at.push(p.band_moved + restore_band::IDLE_S as f64);
        }
    } else if p.active() == (p.mode == Mode::Off) || p.mode == Mode::Restore {
        at.push(now);
    }
    at
}

/// The run loop's side of the shared pointer: polled when its wake fd is signalled or a [`due`] time is up.
pub struct PointerHook<C: Control> {
    pointer: SharedPointer<C>,
    wake: Arc<WakeFd>,
    /// The pointer clock's time at the last poll: a due time no later than it has been seen to.
    polled: f64,
}

impl<C: Control> PointerHook<C> {
    pub fn pointer(&self) -> &SharedPointer<C> {
        &self.pointer
    }
}

impl<C: Control> LoopHook for PointerHook<C> {
    fn wake_fd(&self) -> &WakeFd {
        &self.wake
    }

    fn deadline(&mut self) -> Option<Instant> {
        let mut p = self.pointer.lock();
        let now = p.now();
        let next = due(&p, now).into_iter().filter(|&t| t > self.polled).min_by(f64::total_cmp)?;
        Some(Instant::now() + Duration::from_secs_f64((next - now).clamp(0.0, 3600.0)))
    }

    fn poll(&mut self, _: Instant, out: &mut Vec<u8>) {
        let mut p = self.pointer.lock();
        let now = p.now();
        self.polled = now;
        out.extend(p.poll(now));
    }
}

/// A clock for the pointer: the sampler's `CLOCK_MONOTONIC`, as `time.monotonic` reads it.
pub fn monotonic() -> Box<dyn Clock + Send> {
    Box::new(super::sampler::Monotonic)
}
