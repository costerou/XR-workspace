//! `unified_cursor.UnifiedPointer` (tools/native_spike/unified_cursor.py lines 387-929): stub until its port lands.

use super::overview_pointer::{Control, OverviewPointer};
use super::python::Result;
use crate::resize::OutputRect;

/// The unified cursor's own state.
#[derive(Debug, Default)]
pub struct Unified {
    pub super_drag: Option<&'static str>,
    pub pan: Option<()>,
}

impl<C: Control> OverviewPointer<C> {
    pub(crate) fn u_overview(&self) -> bool { self.overview_base() }
    pub(crate) fn u_handles_on(&self) -> bool { self.active() }
    pub(crate) fn u_poll(&mut self, now: f64) -> Vec<u8> { self.poll_base(now) }
    pub(crate) fn u_absolute(&mut self, cursor: (i64, i64), now: f64) -> Vec<u8> { self.absolute_base(cursor, now) }
    pub(crate) fn u_onto_help(&mut self) { self.onto_help_base() }
    pub(crate) fn u_park(&self, o: &OutputRect, _local: Option<(f64, f64)>) -> (i64, i64) {
        ((o.x as f64 + o.width / 2.0) as i64, (o.y as f64 + o.height / 2.0) as i64)
    }
    pub(crate) fn u_move(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> { self.move_base(dx, dy, now) }
    pub(crate) fn u_on_op(&mut self, op: &str) -> Result<Option<Vec<u8>>> { self.on_op_base(op) }
    pub(crate) fn u_held(&self) -> bool { false }
    pub(crate) fn u_carry(&mut self, _dx: f64, _dy: f64, _now: f64) -> Vec<u8> { Vec::new() }
    pub(crate) fn u_look(&self) -> Option<&'static str> { self.look_base() }
}

/// The unified cursor: the overview pointer with its overrides on.
pub struct UnifiedPointer<C: Control>(pub OverviewPointer<C>);

impl<C: Control> UnifiedPointer<C> {
    pub fn new(control: C, clock: Box<dyn super::sampler::Clock + Send>) -> std::io::Result<Self> {
        let mut p = OverviewPointer::new(control, clock)?;
        p.ext = Some(Box::default());
        Ok(UnifiedPointer(p))
    }
}

impl<C: Control> std::ops::Deref for UnifiedPointer<C> {
    type Target = OverviewPointer<C>;
    fn deref(&self) -> &OverviewPointer<C> { &self.0 }
}

impl<C: Control> std::ops::DerefMut for UnifiedPointer<C> {
    fn deref_mut(&mut self) -> &mut OverviewPointer<C> { &mut self.0 }
}

/// The unified pointer's own trace methods (on_super, request_switch, neighbour, pan_plan, set_pan, can_switch).
#[cfg(test)]
pub(crate) fn replay_call<C: Control>(
    _p: &mut UnifiedPointer<C>,
    _m: &str,
    _a: &[crate::pyjson::Json],
    _k: &crate::pyjson::Json,
) -> Option<std::result::Result<crate::pyjson::Json, String>> {
    None
}
