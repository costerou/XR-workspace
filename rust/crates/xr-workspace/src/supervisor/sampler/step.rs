//! The sampler's decisions: one [`Sampler::step`] is one iteration of the Python's `_run` loop, and each method is
//! the Python method of the same name, its calls in the same order. Nothing here sleeps or spawns.

use super::push::{ENTRY_PX, EdgePush, PushEnv, along, entry_point, output_at};
use super::{
    CARRY_LEASH_PX, CURSOR_TIMEOUT, CallError, Clock, IDLE_S, JUMP_PX, LEASH_PX, MONITORS_TIMEOUT,
    Mode, OUTPUTS_REFRESH, PENDING_S, RECENTRE_PX, REGRAB_S, SAMPLE_JUMP_PX,
    SHAPE_CHECK_S, SHAPE_RETRY_S, SHAPE_S, SHAPE_SETTLE_S, SampledPointer, SamplerClient, Side,
    py_index,
};
use crate::hyprland::Query;
use crate::resize::{OutputRect, outputs_from_monitors};
use crate::supervisor::columns::{ColumnScroll, ColumnsClient};
use crate::supervisor::overview_cursor::OverviewCursor;

/// `() -> the requested cursor's name or none` (ownership.cursor_reader).
pub type ShapeReader = Box<dyn FnMut() -> Result<Option<String>, CallError> + Send>;

/// Which Python sampler this is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Variant {
    /// free_pointer.PointerSampler.
    Plain,
    /// input_lock.LockedSampler: grabs with lock_pointer, a short leash, warps by others left alone, a release that
    /// leaves the cursor where it is.
    Locked,
    /// unified_cursor.UnifiedSampler: the lock's grab, desktop mode's click binds, and outside the overview the
    /// cursor kept on home and the push past its edge.
    Unified,
}

/// How the thread may wait after a step.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Idle {
    /// Sampling: wait `secs`.
    No,
    /// Nothing changes until the thread is woken (the overview opens, restore sampling starts, the names change).
    Block,
    /// Nothing changes until then (the next monitors read, on the [`super::Clock`]), or until woken.
    Until(f64),
}

/// What a step asks of the thread: the Python's wait (`secs`), and whether it may wait longer.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Wait {
    pub secs: f64,
    pub idle: Idle,
}

impl Wait {
    fn sampling(secs: f64) -> Wait {
        Wait {
            secs,
            idle: Idle::No,
        }
    }
}

/// One run's sampler, its state the Python's attributes.
pub struct Sampler<P: SampledPointer, C: SamplerClient, K: Clock> {
    pub variant: Variant,
    pub pointer: P,
    pub client: C,
    pub clock: K,
    pub names: Vec<String>,
    pub bind_args: Vec<String>,
    pub period: f64,
    cursor_shape: Option<ShapeReader>,
    shape_due: f64,
    shape_read: f64,
    shape_open: bool,
    pub shape_errors: u64,
    pub names_changed: bool,
    /// The last want serial applied (`None`: re-apply when the overview is back).
    pub applied: Option<i64>,
    /// Binds installed and cursor hidden (free mode).
    pub grabbed: bool,
    pub ever_grabbed: bool,
    pub park: Option<(i64, i64)>,
    last: Option<(i64, i64)>,
    regrabbed_at: Option<f64>,
    pub errors: u64,
    pub samples: u64,
    pub recentres: u64,
    pub cursor: OverviewCursor,
    restore_sampling_active: bool,
    /// What `restore_sampling()` last said in a step (not the Python's: it decides whether the thread may block).
    restore_enabled: bool,
    /// The unified sampler's: the click binds are in (desktop mode), confinements made, the previous sample outside
    /// the overview, the push, a switch asked of the loop (target, edge, fraction along it, asked at), the scroller.
    pub bound: bool,
    pub confines: u64,
    outside: Option<(f64, f64)>,
    pub push: EdgePush<P::Plan>,
    pending: Option<(i64, Side, f64, f64)>,
    /// The XR monitors as last read.
    pub outputs: Vec<OutputRect>,
    pub columns: Option<ColumnScroll>,
    /// When the monitors were last read (`_run`'s local).
    refreshed: Option<f64>,
}

impl<P: SampledPointer, C: SamplerClient, K: Clock> Sampler<P, C, K> {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        variant: Variant,
        client: C,
        names: Vec<String>,
        mut pointer: P,
        bind_args: Vec<String>,
        period: f64,
        cursor_shape: Option<ShapeReader>,
        clock: K,
    ) -> Self {
        pointer.set_shape_wired(cursor_shape.is_some());
        let cursor = OverviewCursor::new(pointer.reloads());
        let columns =
            (variant == Variant::Unified && client.scrolls()).then(ColumnScroll::new);
        Sampler {
            variant,
            pointer,
            client,
            clock,
            names,
            bind_args,
            period,
            cursor_shape,
            shape_due: 0.0,
            shape_read: 0.0,
            shape_open: false,
            shape_errors: 0,
            names_changed: false,
            applied: Some(0),
            grabbed: false,
            ever_grabbed: false,
            park: None,
            last: None,
            regrabbed_at: None,
            errors: 0,
            samples: 0,
            recentres: 0,
            cursor,
            restore_sampling_active: false,
            restore_enabled: false,
            bound: false,
            confines: 0,
            outside: None,
            push: EdgePush::new(),
            pending: None,
            outputs: Vec::new(),
            columns,
            refreshed: None,
        }
    }

    /// The run's desktops changed: read their monitors again on the next step.
    pub fn set_names(&mut self, names: Vec<String>) {
        self.names = names;
        self.names_changed = true;
    }

    /// Run end, after the thread stopped: the binds go (best effort) and the cursor shows.
    pub fn stop(&mut self) {
        if self.ever_grabbed {
            let _ = self.client.remove_resize_binds();
            self.grabbed = false;
        }
        self.cursor.stop(&mut self.client);
    }

    fn applied_or(&self) -> i64 {
        self.applied.unwrap_or(-1)
    }

    /// The monitors read again when due; `now`: the clock's reading for the due check, or `None` to read it there.
    fn refresh(&mut self, now: Option<f64>, clear_first: bool) {
        let due = self.refreshed.is_none()
            || self.names_changed
            || now.unwrap_or_else(|| self.clock.now()) - self.refreshed.unwrap_or(0.0)
                >= OUTPUTS_REFRESH;
        if !due {
            return;
        }
        if clear_first {
            self.names_changed = false;
        }
        match self.client.query(Query::Monitors, MONITORS_TIMEOUT) {
            Ok(raw) => {
                let names: Vec<&str> = self.names.iter().map(String::as_str).collect();
                self.outputs = outputs_from_monitors(&raw, &names);
                self.refreshed = Some(now.unwrap_or_else(|| self.clock.now()));
                self.names_changed = false;
            }
            Err(_) => self.errors += 1,
        }
    }

    /// One iteration of `_run`.
    pub fn step(&mut self) -> Wait {
        match self.variant {
            Variant::Plain | Variant::Locked => self.step_pointer(),
            Variant::Unified => self.step_unified(),
        }
    }

    fn step_pointer(&mut self) -> Wait {
        if !self.pointer.overview() && self.pointer.restore_sampling() {
            self.refresh(None, false);
        }
        if !self.pointer.overview() && self.sample_restore_outputs() {
            return Wait::sampling(self.period);
        }
        if !self.pointer.overview() {
            self.release();
            let reloads = self.pointer.reloads();
            self.cursor
                .step(&mut self.client, false, reloads, &mut self.clock);
            self.applied = None;
            self.last = None;
            let settled = !self.restore_enabled
                && !self.restore_sampling_active
                && !self.grabbed
                && self.cursor.hidden == Some(false);
            return Wait {
                secs: IDLE_S,
                idle: if settled { Idle::Block } else { Idle::No },
            };
        }
        let now = self.clock.now();
        self.refresh(Some(now), true);
        self.overview_sample();
        Wait::sampling(self.period)
    }

    fn step_unified(&mut self) -> Wait {
        let now = self.clock.now();
        self.refresh(Some(now), true);
        if !self.pointer.overview() && self.sample_restore_outputs() {
            self.bound = false; // the grab or its release took the binds with it
            return Wait::sampling(self.period);
        }
        if !self.pointer.overview() {
            self.restore_enabled = self.pointer.restore_sampling();
            if self.restore_enabled {
                self.arm_clicks();
            } else {
                self.release();
            }
            // The normal cursor outside the overview, but for a hand's pan: the dot shows where the hand is.
            let hidden = self.push.frame.is_some() || self.pending.is_some();
            let reloads = self.pointer.reloads();
            self.cursor
                .step(&mut self.client, hidden, reloads, &mut self.clock);
            self.applied = None;
            self.last = None;
            let outputs = std::mem::take(&mut self.outputs);
            let fast = self.confine(&outputs);
            self.outputs = outputs;
            let settled = self.outputs.is_empty()
                && !self.restore_enabled
                && !self.restore_sampling_active
                && !self.grabbed
                && !self.bound
                && self.pending.is_none()
                && self.push.frame.is_none()
                && self.cursor.hidden == Some(false);
            let idle = match self.refreshed {
                Some(at) if settled => Idle::Until(at + OUTPUTS_REFRESH),
                _ => Idle::No,
            };
            return Wait {
                secs: if fast { self.period } else { IDLE_S },
                idle,
            };
        }
        self.outside = None;
        if self.push.frame.is_some() {
            // back to the overview mid-pan: the view frame ended it
            self.push.reset();
            self.pointer.set_pan(None);
        }
        self.overview_sample();
        Wait::sampling(self.period)
    }

    /// The overview's sample: the want applied, the cursor hidden while the dot is the pointer, a cursor read fed,
    /// the leash, the shape read.
    fn overview_sample(&mut self) {
        self.apply();
        let hidden = self.pointer.mode().dot();
        let reloads = self.pointer.reloads();
        self.cursor
            .step(&mut self.client, hidden, reloads, &mut self.clock);
        let (cursor, motion) = self.sample();
        let applied = self.applied_or();
        self.pointer.feed(cursor, motion, &self.outputs, applied);
        self.recentre(cursor);
        self.read_shape();
    }

    fn sample_restore_outputs(&mut self) -> bool {
        let outputs = std::mem::take(&mut self.outputs);
        let owns = self.sample_restore(&outputs);
        self.outputs = outputs;
        owns
    }

    /// `sample_restore`: true only during UI ownership or its pending release; uses the plain `_apply`, so returning
    /// to apps removes the modal binds.
    pub fn sample_restore(&mut self, outputs: &[OutputRect]) -> bool {
        let active = self.restore_sampling_active;
        let enabled = self.pointer.restore_sampling();
        self.restore_enabled = enabled;
        if !enabled && !active {
            return false;
        }
        if active {
            if !enabled {
                self.pointer.restore_release();
            }
            self.apply_plain();
        }
        let (cursor, motion) = self.sample();
        let applied = self.applied_or();
        self.pointer.feed(cursor, motion, outputs, applied);
        if self.pointer.mode() == Mode::Restore {
            self.restore_sampling_active = true;
            self.recentre(cursor);
            return true;
        }
        if active {
            // A status or lifecycle change can stop loop-side polling: release on the sampler too.
            if !enabled {
                self.pointer.restore_release();
                self.apply_plain();
            }
            self.restore_sampling_active = false;
            return true;
        }
        false
    }

    /// `_read_shape`: what cursor the app under the dot wants (free_pointer's docstring has the cadence).
    pub fn read_shape(&mut self) {
        let now = self.clock.now();
        if self.cursor_shape.is_none() {
            return;
        }
        let p = &mut self.pointer;
        if p.mode() != Mode::Desktop
            || !p.handles_on()
            || self.applied.is_none_or(|a| a < p.sync())
        {
            self.shape_due = now + SHAPE_SETTLE_S;
            self.shape_read = 0.0;
            return;
        }
        let gated = p.shape_gated();
        self.shape_open = self.shape_open || gated;
        let every = if p.shape_pushed() && !self.shape_open {
            SHAPE_CHECK_S
        } else {
            SHAPE_S
        };
        if now < self.shape_due || now - self.shape_read < every {
            return;
        }
        self.shape_read = now;
        self.shape_open = gated;
        let serial = p.shape_serial();
        let read = self.cursor_shape.as_mut().expect("checked above");
        let name = match read() {
            Ok(name) => name,
            Err(_) => {
                self.shape_errors += 1;
                self.shape_due = now + SHAPE_RETRY_S;
                None
            }
        };
        self.pointer.feed_shape(name.as_deref(), serial);
    }

    /// `_sample`: one cursor read, and the motion since the last.
    pub fn sample(&mut self) -> (Option<(i64, i64)>, (f64, f64)) {
        let (x, y) = match self.client.cursor_pos(CURSOR_TIMEOUT) {
            Ok(Some(pos)) => pos,
            _ => {
                self.errors += 1;
                return (None, (0.0, 0.0));
            }
        };
        self.samples += 1;
        let cursor = (x.trunc() as i64, y.trunc() as i64);
        let Some(last) = self.last.replace(cursor) else {
            return (Some(cursor), (0.0, 0.0));
        };
        let (dx, dy) = (cursor.0 - last.0, cursor.1 - last.1);
        if dx.abs() > SAMPLE_JUMP_PX || dy.abs() > SAMPLE_JUMP_PX {
            return (Some(cursor), (0.0, 0.0));
        }
        let motion = (dx as f64, dy as f64);
        if self.variant != Variant::Plain && motion.0.hypot(motion.1) > JUMP_PX {
            return (Some(cursor), (0.0, 0.0)); // a warp, never hand motion (LockedPointer._follow)
        }
        (Some(cursor), motion)
    }

    fn warp_to_park(&mut self) {
        let park = self.park.expect("checked by the caller");
        if self.client.warp_cursor(park.0, park.1).is_ok() {
            self.last = Some(park);
            self.recentres += 1;
        }
    }

    /// `_recentre`: the hidden cursor put back where it is held once it strays.
    pub fn recentre(&mut self, cursor: Option<(i64, i64)>) {
        let off = |park: (i64, i64), c: (i64, i64)| ((c.0 - park.0) as f64).hypot((c.1 - park.1) as f64);
        if self.variant == Variant::Unified
            && self.pointer.want().kind == Mode::Carry
            && self.pointer.overview()
        {
            // Carry mode: the cursor, and the dragged window with it, goes back once it strays CARRY_LEASH_PX.
            let (Some(c), Some(park)) = (cursor, self.park) else {
                return;
            };
            if off(park, c) > CARRY_LEASH_PX {
                self.warp_to_park();
            }
            return;
        }
        let (true, Some(c), Some(park)) = (self.grabbed, cursor, self.park) else {
            return;
        };
        if self.variant == Variant::Plain {
            if off(park, c) > RECENTRE_PX {
                self.warp_to_park();
            }
            return;
        }
        if !self.pointer.overview() {
            return;
        }
        // One that jumped more than JUMP_PX was warped by a focus change: left there (the loop follows it).
        let d = off(park, c);
        if d > LEASH_PX && d <= JUMP_PX {
            self.warp_to_park();
        }
    }

    /// The grab: lock_pointer under the lock (input_lock._LockingClient).
    fn grab(&mut self, x: i64, y: i64) -> Result<(), CallError> {
        match self.variant {
            Variant::Plain => self.client.grab_pointer(&self.bind_args, x, y),
            Variant::Locked | Variant::Unified => self.client.lock_pointer(&self.bind_args, x, y),
        }
    }

    /// A config reload removed the binds and showed the cursor: regrab, at most once per REGRAB_S.
    /// `unified`: the unified sampler's `_apply` (the click binds count as held, and go with the grab).
    fn binds_dropped(&mut self, unified: bool) {
        if !self.pointer.binds_dropped() {
            return;
        }
        self.pointer.clear_binds_dropped();
        if self.grabbed || (unified && self.bound) {
            let now = self.clock.now();
            if self.regrabbed_at.is_none_or(|at| now - at >= REGRAB_S) {
                self.regrabbed_at = Some(now);
                self.grabbed = false;
                if unified {
                    self.bound = false;
                }
                self.applied = None;
            }
        }
    }

    /// `_apply`: the pointer's want, made.
    pub fn apply(&mut self) {
        match self.variant {
            Variant::Plain | Variant::Locked => self.apply_plain(),
            Variant::Unified => self.apply_unified(),
        }
    }

    /// free_pointer.PointerSampler._apply.
    fn apply_plain(&mut self) {
        let want = self.pointer.want();
        self.binds_dropped(false);
        if Some(want.serial) == self.applied {
            return;
        }
        let made = match (want.kind, want.point) {
            (Mode::Free, None) => return,
            (Mode::Free, Some((x, y))) => {
                self.ever_grabbed = true; // set first: a failed or partial grab is still undone at stop
                self.grab(x, y).map(|()| {
                    self.grabbed = true;
                    self.park = Some((x, y));
                    self.last = Some((x, y));
                })
            }
            (Mode::Off, _) => {
                self.release();
                Ok(())
            }
            (_, Some((x, y))) => self.client.release_pointer(Some(x), Some(y)).map(|()| {
                self.grabbed = false;
                self.last = Some((x, y));
            }),
            (_, None) if self.grabbed => self
                .client
                .release_pointer(None, None)
                .map(|()| self.grabbed = false),
            _ => Ok(()),
        };
        if made.is_err() {
            self.errors += 1;
            return; // retried on the next sample
        }
        self.applied = Some(want.serial);
    }

    /// unified_cursor.UnifiedSampler._apply.
    fn apply_unified(&mut self) {
        let want = self.pointer.want();
        self.binds_dropped(true);
        if Some(want.serial) == self.applied {
            return;
        }
        let made = match (want.kind, want.point) {
            (Mode::Free, None) => return,
            (Mode::Free, Some((x, y))) => {
                self.ever_grabbed = true;
                self.grab(x, y).map(|()| {
                    self.grabbed = true;
                    self.bound = false;
                    self.park = Some((x, y));
                    self.last = Some((x, y));
                })
            }
            // `*point` of none raises before any call.
            (Mode::Carry, None) => Err(CallError::Failed),
            (Mode::Carry, Some((x, y))) => {
                // A Super drag between desktops: no grab, the button stays Hyprland's.
                let made = if self.grabbed {
                    self.client
                        .enter_desktop(&self.bind_args, Some(x), Some(y), None)
                        .map(|()| {
                            self.grabbed = false;
                            self.bound = true;
                        })
                } else {
                    self.client.warp_cursor(x, y)
                };
                made.map(|()| {
                    self.park = Some((x, y));
                    self.last = Some((x, y));
                })
            }
            (Mode::Desktop, Some((x, y))) if self.bound && !self.grabbed => self.client.warp_cursor(x, y),
            (Mode::Desktop, None) if self.bound && !self.grabbed => Ok(()),
            (Mode::Desktop, point) => {
                self.ever_grabbed = true;
                let monitor = self.monitor(point).map(str::to_string);
                self.client
                    .enter_desktop(
                        &self.bind_args,
                        point.map(|p| p.0),
                        point.map(|p| p.1),
                        monitor.as_deref(),
                    )
                    .map(|()| {
                        self.grabbed = false;
                        self.bound = true;
                        if point.is_some() {
                            self.last = point;
                        }
                    })
            }
            (Mode::Off, _) => {
                self.release();
                Ok(())
            }
            _ if self.grabbed || self.bound => {
                self.client.release_pointer(None, None).map(|()| {
                    self.grabbed = false;
                    self.bound = false;
                })
            }
            _ => Ok(()),
        };
        if made.is_err() {
            self.errors += 1;
            return;
        }
        self.applied = Some(want.serial);
    }

    /// The name of the run's XR monitor holding layout point `point`.
    fn monitor(&self, point: Option<(i64, i64)>) -> Option<&str> {
        let (x, y) = point?;
        let o = output_at(&self.outputs, (x as f64, y as f64))?;
        self.names.get(o.index).map(String::as_str)
    }

    /// `_release`: out of the overview, the binds go.
    pub fn release(&mut self) {
        let made = match self.variant {
            Variant::Plain if self.grabbed => self.client.remove_resize_binds(),
            Variant::Locked if self.grabbed => self.client.release_pointer(None, None),
            Variant::Unified if self.grabbed || self.bound => {
                self.client.release_pointer(None, None)
            }
            _ => return,
        };
        match made {
            Ok(()) => {
                self.grabbed = false;
                self.bound = false;
            }
            Err(_) => self.errors += 1,
        }
    }

    /// `_arm_clicks`: maximized, the non-consuming click binds while the band can open.
    pub fn arm_clicks(&mut self) {
        if self.bound || self.grabbed {
            return;
        }
        self.ever_grabbed = true; // undone at stop as any bind of this table is
        match self.client.arm_click_binds(&self.bind_args) {
            Ok(()) => self.bound = true,
            Err(_) => self.errors += 1,
        }
    }

    /// `_confine`: outside the overview, a cursor that left home put back and a push past home's edge switching
    /// desktops. True while it is on an XR monitor (sample fast).
    pub fn confine(&mut self, outputs: &[OutputRect]) -> bool {
        if outputs.is_empty() {
            return false;
        }
        let Ok(Some(mut cursor)) = self.client.cursor_pos(CURSOR_TIMEOUT) else {
            self.errors += 1;
            return false;
        };
        let now = self.clock.now();
        let home = self.pointer.selected();
        if let Some((target, edge, t, asked)) = self.pending {
            let o = outputs.iter().find(|o| o.index as i64 == target);
            if let Some(o) = o.filter(|_| home == target) {
                // the loop switched: the cursor comes in over the matching edge
                self.pending = None;
                self.push.reset();
                self.pointer.set_pan(None);
                let entry = entry_point(o, edge, t, ENTRY_PX);
                let made = match py_index(&self.names, target) {
                    Some(name) => self.client.confine_cursor(entry.0, entry.1, name),
                    None => Err(CallError::Failed),
                };
                match made {
                    Ok(()) => cursor = (entry.0 as f64, entry.1 as f64),
                    Err(_) => self.errors += 1,
                }
                self.outside = Some(cursor);
                return true;
            }
            if home == target || now - asked >= PENDING_S {
                self.pending = None;
                self.push.reset();
                self.pointer.set_pan(None);
            }
        }
        let live = self.pending.is_none() && self.pointer.can_switch();
        let columns = self.pending.is_none()
            && self.pointer.can_scroll()
            && self.columns.is_some()
            && (0..self.names.len() as i64).contains(&home);
        if !live && self.pending.is_none() {
            self.push.reset();
        }
        let o = outputs.iter().find(|o| o.index as i64 == home).copied();
        let mut env = Env {
            pointer: &mut self.pointer,
            client: &mut self.client,
            columns: if columns {
                self.columns.as_mut().map(|c| (c, self.names[home as usize].as_str()))
            } else {
                None
            },
            o: o.filter(|_| live),
            cursor,
            live,
        };
        let (warp, switch) =
            self.push
                .step(outputs, home, Some(cursor), self.outside, now, &mut env);
        let panned = self.push.panned;
        if !live && self.pending.is_none() {
            self.push.reset();
        }
        if self.pending.is_none() {
            self.pointer.set_pan(self.push.frame.as_ref());
        }
        if let Some((x, y)) = warp {
            let made = match py_index(&self.names, home) {
                Some(name) => self.client.confine_cursor(x, y, name),
                None => Err(CallError::Failed),
            };
            match made {
                Ok(()) => {
                    cursor = (x as f64, y as f64);
                    self.confines += 1;
                }
                Err(_) => self.errors += 1,
            }
        }
        if let Some((target, side, t)) = switch
            && (0..self.names.len() as i64).contains(&target)
        {
            self.pending = Some((target, side, t, now));
            self.pointer.request_switch(home, target, panned);
        }
        self.outside = Some(cursor);
        output_at(outputs, cursor).is_some()
    }
}

/// What the unified sampler gives its push: the pointer's neighbours and plans while a switch can be made, and the
/// scroller on home's monitor.
struct Env<'a, P, C> {
    pointer: &'a mut P,
    client: &'a mut C,
    columns: Option<(&'a mut ColumnScroll, &'a str)>,
    /// Home's output while a switch can be made.
    o: Option<OutputRect>,
    cursor: (f64, f64),
    live: bool,
}

impl<P: SampledPointer, C: ColumnsClient> PushEnv<P::Plan> for Env<'_, P, C> {
    fn neighbour(&mut self, side: Side) -> Option<i64> {
        let o = self.o.as_ref()?;
        self.pointer.neighbour(side, along(o, self.cursor, side))
    }

    fn has_columns(&self) -> bool {
        self.columns.is_some()
    }

    fn columns(&mut self, side: Side, now: f64, confirm: bool) -> bool {
        match &mut self.columns {
            Some((scroll, name)) => scroll.held(self.client, name, side, now, confirm),
            None => false,
        }
    }

    fn has_planner(&self) -> bool {
        self.live
    }

    fn plan(&mut self, target: i64, side: Side, along: f64, output: &OutputRect) -> Option<P::Plan> {
        self.pointer.pan_plan(target, side, along, output)
    }
}

#[cfg(test)]
impl<P: SampledPointer, C: SamplerClient, K: Clock> Sampler<P, C, K>
where
    P::Plan: super::Snapshot,
    <P::Plan as super::PanPlan>::Frame: super::Snapshot,
{
    /// The corpus's view of the state (the Python's attributes).
    pub(crate) fn state(&self) -> serde_json::Value {
        use super::test_float;
        use serde_json::{Value, json};
        let pair = |p: Option<(i64, i64)>| p.map_or(Value::Null, |p| json!([p.0, p.1]));
        let mut s = json!({
            "applied": self.applied,
            "grabbed": self.grabbed,
            "ever_grabbed": self.ever_grabbed,
            "park": pair(self.park),
            "_last": pair(self.last),
            "_regrabbed_at": self.regrabbed_at.map(test_float),
            "errors": self.errors,
            "samples": self.samples,
            "recentres": self.recentres,
            "shape_errors": self.shape_errors,
            "names_changed": self.names_changed,
            "_shape_due": test_float(self.shape_due),
            "_shape_read": test_float(self.shape_read),
            "_shape_open": self.shape_open,
            "names": self.names,
            "cursor": self.cursor.state(),
            "_restore_sampling_active": self.restore_sampling_active,
        });
        if self.variant == Variant::Unified {
            let extra = json!({
                "bound": self.bound,
                "confines": self.confines,
                "_outside": self.outside.map(|c| json!([c.0, c.1])),
                "push": self.push.state(),
                "_pending": self.pending.map(|p| json!([p.0, p.1.as_str(), p.2, test_float(p.3)])),
                "outputs": self.outputs.iter().map(super::test_output).collect::<Vec<_>>(),
                "columns": self.columns.as_ref().map(|c| c.state()),
            });
            s.as_object_mut()
                .unwrap()
                .extend(extra.as_object().unwrap().clone());
        }
        s
    }
}
