//! Zoomed in: the confinement to home and the push past an edge of home that switches desktops (a port of
//! `unified_cursor.EdgePush`, lines 276-386; its helpers are [`crate::supervisor::unified_cursor`]'s, re-exported
//! here). Points are layout pixels; a cursor read from Hyprland may be fractional, as the Python keeps it.

use super::{JUMP_PX, PanPlan, Side};
use crate::resize::OutputRect;
use crate::supervisor::unified_cursor::by_index;
pub use crate::supervisor::unified_cursor::{
    ENTRY_PX, NUDGE_PX, PAN_HOLD_PX, PAN_LEASH_PX, PAN_PULL_PX, PUSH_PX, PUSH_WINDOW_S, Point, SWITCH_COOLDOWN_S,
    along, band, confine_target, contact, crossing, entry_point, hold_point, inside, nudge, output_at, pan_point,
};
/// edge_pan.START_PX (logical px of push before anything shows) and REST_S (a hand this long still ends the pan).
pub const START_PX: f64 = 24.0;
pub const REST_S: f64 = 0.6;

fn float(p: (i64, i64)) -> Point {
    (p.0 as f64, p.1 as f64)
}

/// What [`EdgePush::step`] asks of its caller: the desktop past an edge of home, the columns (when given), and the
/// pan plan (when a planner is given).
pub trait PushEnv<T> {
    /// The desktop placed past home's `side` edge, or none (plain confinement that way).
    fn neighbour(&mut self, side: Side) -> Option<i64>;
    /// columns.ColumnScroll.held is given.
    fn has_columns(&self) -> bool;
    fn columns(&mut self, side: Side, now: f64, confirm: bool) -> bool;
    /// A planner is given.
    fn has_planner(&self) -> bool;
    fn plan(&mut self, target: i64, side: Side, along: f64, output: &OutputRect) -> Option<T>;
}

/// A pan under way.
#[derive(Clone, Debug)]
pub struct Track<T> {
    pub plan: T,
    pub travel: f64,
    /// The last cursor sample.
    pub at: Point,
    /// When the hand last moved.
    pub moved: f64,
    pub hold: (i64, i64),
}

/// The push's answer for one sample: a layout pixel on home to put the cursor at, and a switch (target, edge, fraction
/// along it) once the push reached its target.
pub type Step = (Option<(i64, i64)>, Option<(i64, Side, f64)>);

/// The confinement plus the push past an edge of home (the Python's class docstring has the rules).
#[derive(Clone, Debug)]
pub struct EdgePush<T: PanPlan> {
    pub edge: Option<Side>,
    pub amount: f64,
    pub last: Option<f64>,
    pub quiet_until: f64,
    pub track: Option<Track<T>>,
    /// The pan's `edge_pan` payload.
    pub frame: Option<T::Frame>,
    /// The switch step() just returned ended a pan.
    pub panned: bool,
    /// Pans that ended on home.
    pub pulled_back: u64,
}

impl<T: PanPlan> Default for EdgePush<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: PanPlan> EdgePush<T> {
    pub fn new() -> Self {
        EdgePush {
            edge: None,
            amount: 0.0,
            last: None,
            quiet_until: f64::NEG_INFINITY,
            track: None,
            frame: None,
            panned: false,
            pulled_back: 0,
        }
    }

    pub fn reset(&mut self) {
        self.edge = None;
        self.amount = 0.0;
        self.last = None;
        self.track = None;
        self.frame = None;
    }

    fn start(&mut self, o: &OutputRect, plan: T, now: f64) -> (i64, i64) {
        let hold = pan_point(o, plan.side(), plan.exit(), PAN_HOLD_PX);
        let travel = self.amount - START_PX;
        self.frame = Some(plan.frame(travel));
        self.track = Some(Track {
            plan,
            travel,
            at: float(hold),
            moved: now,
            hold,
        });
        hold
    }

    /// One sample of the pan under way. The cursor's motion along the push is the hand's.
    fn pan(
        &mut self,
        o: &OutputRect,
        home: i64,
        cursor: Point,
        now: f64,
        env: &mut dyn PushEnv<T>,
    ) -> Step {
        let p = self.track.as_mut().expect("a pan under way");
        if home != p.plan.home() {
            self.reset(); // a key took the selection: that switch is what counts
            return (None, None);
        }
        let side = p.plan.side();
        let along_axis = |c: Point| if side.horizontal() { c.0 } else { c.1 };
        let sign = if matches!(side, Side::Right | Side::Down) {
            1.0
        } else {
            -1.0
        };
        let mut step = (along_axis(cursor) - along_axis(p.at)) * sign;
        if step.abs() > JUMP_PX {
            step = 0.0; // a warp, not the hand
        }
        if step != 0.0 {
            p.travel = (p.travel + step).max(0.0);
            p.moved = now;
        }
        p.at = cursor;
        if (step < 0.0 && p.travel <= 0.0) || now - p.moved >= REST_S {
            let exit = p.plan.exit();
            self.reset();
            self.pulled_back += 1;
            return (Some(pan_point(o, side, exit, PAN_PULL_PX)), None);
        }
        let total = p.plan.total_px();
        if p.travel >= total {
            if env.has_columns() && side.horizontal() && env.columns(side, now, true) {
                // a column turned up
                self.reset();
                self.pulled_back += 1;
                return (Some(hold_point(o, cursor, side)), None);
            }
            let p = self.track.as_ref().expect("a pan under way");
            let final_ = p.plan.frame(total);
            let switch = (p.plan.target(), side, p.plan.entry());
            self.reset();
            self.frame = Some(final_);
            self.panned = true;
            self.quiet_until = now + SWITCH_COOLDOWN_S;
            return (None, Some(switch));
        }
        self.frame = Some(p.plan.frame(p.travel));
        let hold = p.hold;
        if (cursor.0 - hold.0 as f64).hypot(cursor.1 - hold.1 as f64) <= PAN_LEASH_PX {
            return (None, None);
        }
        p.at = float(hold);
        (Some(hold), None)
    }

    /// One cursor sample on home `home` (`previous`: the sample before, as left).
    pub fn step(
        &mut self,
        outputs: &[OutputRect],
        home: i64,
        cursor: Option<Point>,
        previous: Option<Point>,
        now: f64,
        env: &mut dyn PushEnv<T>,
    ) -> Step {
        let o = by_index(outputs, home);
        self.panned = false;
        let (Some(o), Some(cursor)) = (o, cursor) else {
            self.reset();
            return (None, None);
        };
        if self.track.is_some() {
            return self.pan(o, home, cursor, now, env);
        }
        let (warp, side, amount);
        match output_at(outputs, cursor) {
            Some(at) if at.index as i64 == home => {
                if env.has_columns()
                    && let Some(b) = band(o, cursor)
                    && env.columns(b, now, false)
                {
                    // columns left that way: they scroll first, the cursor stays
                    self.reset();
                    return (None, None);
                }
                let mut sides = Vec::new();
                for s in contact(o, cursor) {
                    if env.neighbour(s).is_some() {
                        sides.push(s);
                    }
                }
                if sides.is_empty() || now < self.quiet_until {
                    return (None, None);
                }
                side = sides[0];
                amount = NUDGE_PX;
                warp = Some(nudge(o, cursor, side));
            }
            _ => {
                let Some(w) = confine_target(outputs, home, Some(cursor), previous) else {
                    return (None, None);
                };
                warp = Some(w);
                (side, amount) = crossing(o, cursor);
                if env.has_columns()
                    && side.horizontal()
                    && amount <= JUMP_PX
                    && env.columns(side, now, false)
                {
                    self.reset();
                    return (Some(hold_point(o, cursor, side)), None); // back into the band: the columns scroll first
                }
                if amount > JUMP_PX || now < self.quiet_until || env.neighbour(side).is_none() {
                    return (warp, None);
                }
            }
        }
        if Some(side) != self.edge || self.last.is_none_or(|last| now - last > PUSH_WINDOW_S) {
            self.edge = Some(side);
            self.amount = 0.0;
        }
        self.amount += amount;
        self.last = Some(now);
        let target = env.neighbour(side);
        let plan = if env.has_planner() {
            // The Python passes `target` as it is (None too) to the planner.
            target.and_then(|t| env.plan(t, side, along(o, cursor, side), o))
        } else {
            None
        };
        if let Some(plan) = plan {
            if self.amount >= START_PX {
                return (Some(self.start(o, plan, now)), None);
            }
            return (warp, None);
        }
        if self.amount < PUSH_PX {
            return (warp, None);
        }
        if env.has_columns() && side.horizontal() && env.columns(side, now, true) {
            // a column turned up: it scrolls first
            self.reset();
            return (Some(hold_point(o, cursor, side)), None);
        }
        // The neighbour was there a moment ago; one gone since is no switch (the Python's would raise in its caller).
        let Some(target) = target else {
            return (warp, None);
        };
        self.reset();
        self.quiet_until = now + SWITCH_COOLDOWN_S;
        (warp, Some((target, side, along(o, cursor, side))))
    }
}

#[cfg(test)]
impl<T: PanPlan + super::Snapshot> EdgePush<T>
where
    T::Frame: super::Snapshot,
{
    /// The corpus's view of the state (the Python's attributes).
    pub(crate) fn state(&self) -> serde_json::Value {
        use super::{Snapshot, test_float};
        use serde_json::json;
        json!({
            "edge": self.edge.map(Side::as_str),
            "amount": self.amount,
            "last": self.last.map(test_float),
            "quiet_until": test_float(self.quiet_until),
            "track": self.track.as_ref().map(|t| json!({
                "plan": t.plan.snapshot(),
                "travel": t.travel,
                "at": [t.at.0, t.at.1],
                "moved": test_float(t.moved),
                "hold": [t.hold.0, t.hold.1],
            })),
            "frame": self.frame.as_ref().map(Snapshot::snapshot),
            "panned": self.panned,
            "pulled_back": self.pulled_back,
        })
    }
}
