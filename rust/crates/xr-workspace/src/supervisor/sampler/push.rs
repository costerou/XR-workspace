//! Zoomed in: the confinement to home and the push past an edge of home that switches desktops (a port of
//! `unified_cursor.EdgePush` and its helpers, lines 173-386). Points are layout pixels; a cursor read from Hyprland
//! may be fractional, as the Python keeps it.

use super::{INSET_PX, JUMP_PX, PanPlan, Side};
use crate::resize::OutputRect;
use crate::supervisor::columns::EDGE_PX;

/// unified_cursor: PUSH_PX of push past one edge, each part within PUSH_WINDOW_S of the last, switch.
pub const PUSH_PX: f64 = 48.0;
pub const PUSH_WINDOW_S: f64 = 0.25;
/// The touch on an edge Hyprland clamps the cursor at counts this much, and the cursor goes this far back in.
pub const NUDGE_PX: f64 = 12.0;
/// After a switch no push counts for this long.
pub const SWITCH_COOLDOWN_S: f64 = 0.8;
/// During a pan the hidden cursor waits this far inside the edge, goes back there once this far off, and a pan that
/// ended puts it this far inside the edge.
pub const PAN_HOLD_PX: f64 = 64.0;
pub const PAN_LEASH_PX: f64 = 24.0;
pub const PAN_PULL_PX: f64 = 2.0;
/// The cursor enters the new desktop this far inside the edge it came in over.
pub const ENTRY_PX: f64 = 24.0;
/// edge_pan.START_PX (logical px of push before anything shows) and REST_S (a hand this long still ends the pan).
pub const START_PX: f64 = 24.0;
pub const REST_S: f64 = 0.6;

pub type Point = (f64, f64);

fn contains(o: &OutputRect, c: Point) -> bool {
    let (lx, ly) = (c.0 - o.x as f64, c.1 - o.y as f64);
    0.0 <= lx && lx < o.width && 0.0 <= ly && ly < o.height
}

/// The output the cursor is on.
pub fn output_at(outputs: &[OutputRect], cursor: Point) -> Option<&OutputRect> {
    outputs.iter().find(|o| contains(o, cursor))
}

fn by_index(outputs: &[OutputRect], i: i64) -> Option<&OutputRect> {
    outputs.iter().find(|o| o.index as i64 == i)
}

/// The integer layout pixel of `o` nearest `cursor`, at least `inset` px inside every edge.
pub fn inside(o: &OutputRect, cursor: Point, inset: f64) -> (i64, i64) {
    let (x, y) = (o.x as f64, o.y as f64);
    (
        cursor.0.max(x + inset).min(x + o.width - 1.0 - inset).trunc() as i64,
        cursor.1.max(y + inset).min(y + o.height - 1.0 - inset).trunc() as i64,
    )
}

fn float(p: (i64, i64)) -> Point {
    (p.0 as f64, p.1 as f64)
}

/// Zoomed in: where to put the cursor back on output `home`, or none to leave it. On another XR output: back. Off the
/// XR outputs: back only if the previous sample was on home and it is within JUMP_PX of home (the hand, not a warp).
pub fn confine_target(
    outputs: &[OutputRect],
    home: i64,
    cursor: Option<Point>,
    previous: Option<Point>,
) -> Option<(i64, i64)> {
    let o = by_index(outputs, home)?;
    let cursor = cursor?;
    match output_at(outputs, cursor) {
        Some(now) if now.index as i64 == home => return None,
        Some(_) => {}
        None => {
            let prev = previous.and_then(|p| output_at(outputs, p))?;
            if prev.index as i64 != home {
                return None;
            }
            let (x, y) = (o.x as f64, o.y as f64);
            let dx = (x - cursor.0).max(0.0).max(cursor.0 - (x + o.width - 1.0));
            let dy = (y - cursor.1).max(0.0).max(cursor.1 - (y + o.height - 1.0));
            if dx.hypot(dy) > JUMP_PX {
                return None;
            }
        }
    }
    Some(inside(o, cursor, INSET_PX))
}

/// The edges of `o` whose outermost pixel the cursor (on `o`) is on, horizontal first.
fn contact(o: &OutputRect, c: Point) -> Vec<Side> {
    let (lx, ly) = (c.0 - o.x as f64, c.1 - o.y as f64);
    let mut sides = Vec::new();
    if lx < 1.0 {
        sides.push(Side::Left);
    } else if lx >= o.width - 1.0 {
        sides.push(Side::Right);
    }
    if ly < 1.0 {
        sides.push(Side::Up);
    } else if ly >= o.height - 1.0 {
        sides.push(Side::Down);
    }
    sides
}

/// (edge, px past it) of a cursor off `o`: the axis it is furthest past.
fn crossing(o: &OutputRect, c: Point) -> (Side, f64) {
    let (lx, ly) = (c.0 - o.x as f64, c.1 - o.y as f64);
    let dx = lx - lx.max(0.0).min(o.width - 1.0);
    let dy = ly - ly.max(0.0).min(o.height - 1.0);
    if dx.abs() >= dy.abs() {
        (if dx > 0.0 { Side::Right } else { Side::Left }, dx.abs())
    } else {
        (if dy > 0.0 { Side::Down } else { Side::Up }, dy.abs())
    }
}

/// The cursor NUDGE_PX back inside `o` from edge `side`, the other coordinate kept.
fn nudge(o: &OutputRect, c: Point, side: Side) -> (i64, i64) {
    let (x, y) = float(inside(o, c, INSET_PX));
    let (ox, oy) = (o.x as f64, o.y as f64);
    let p = match side {
        Side::Left => (ox + NUDGE_PX, y),
        Side::Right => (ox + o.width - 1.0 - NUDGE_PX, y),
        Side::Up => (x, oy + NUDGE_PX),
        Side::Down => (x, oy + o.height - 1.0 - NUDGE_PX),
    };
    inside(o, p, INSET_PX)
}

/// How far along edge `side` of `o` the cursor is, 0..1 (left / top to right / bottom).
pub fn along(o: &OutputRect, c: Point, side: Side) -> f64 {
    let p = inside(o, c, 0.0);
    let (lx, ly) = ((p.0 - o.x) as f64, (p.1 - o.y) as f64);
    match side {
        Side::Up | Side::Down => lx / (o.width - 1.0).max(1.0),
        Side::Left | Side::Right => ly / (o.height - 1.0).max(1.0),
    }
}

/// Left / right: the cursor (on `o`) within columns.EDGE_PX of that edge.
fn band(o: &OutputRect, c: Point) -> Option<Side> {
    let lx = c.0 - o.x as f64;
    if lx >= o.width - EDGE_PX {
        Some(Side::Right)
    } else if lx <= EDGE_PX - 1.0 {
        Some(Side::Left)
    } else {
        None
    }
}

/// A push held at edge `side` (left / right) of `o` for its columns: in the edge band, 1 px in from the outermost
/// pixel, as far along the edge as it is.
pub fn hold_point(o: &OutputRect, c: Point, side: Side) -> (i64, i64) {
    let (_, y) = inside(o, c, INSET_PX);
    let x = if side == Side::Left {
        (o.x as f64 + EDGE_PX - 1.0).trunc()
    } else {
        (o.x as f64 + o.width - EDGE_PX).ceil()
    };
    (x as i64, y)
}

fn edge_point(o: &OutputRect, side: Side, t: f64, near: f64) -> (i64, i64) {
    let (w, h) = (o.width - 1.0, o.height - 1.0);
    let local = match side {
        Side::Up => ((t * w).round_ties_even(), near),
        Side::Down => ((t * w).round_ties_even(), h - near),
        Side::Left => (near, (t * h).round_ties_even()),
        Side::Right => (w - near, (t * h).round_ties_even()),
    };
    inside(
        o,
        (o.x as f64 + local.0, o.y as f64 + local.1),
        INSET_PX,
    )
}

/// `inset` px inside edge `side` of `o`, fraction `t` along it.
pub fn pan_point(o: &OutputRect, side: Side, t: f64, inset: f64) -> (i64, i64) {
    edge_point(o, side, t, inset)
}

/// Where the cursor enters `o` after a push past the old desktop's `side` edge: just inside the opposite edge.
pub fn entry_point(o: &OutputRect, side: Side, t: f64) -> (i64, i64) {
    let opposite = match side {
        Side::Up => Side::Down,
        Side::Down => Side::Up,
        Side::Left => Side::Right,
        Side::Right => Side::Left,
    };
    edge_point(o, opposite, t, ENTRY_PX)
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
