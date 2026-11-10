//! The unified cursor's module-level maths: a port of `tools/native_spike/unified_cursor.py` lines 1-273 (its
//! constants, the Lua it sends, and the helpers the confinement and the push past an edge use). The classes
//! (`EdgePush`, `UnifiedPointer`, `UnifiedSampler`) are the sampler's ([`super::sampler::push::EdgePush`]).
//!
//! Points are layout pixels; a cursor read from Hyprland may be fractional, as the Python keeps it. The Python's
//! numbers are ints (`PUSH_PX = 48`); the sampler does f64 maths with them, so they are f64 here.

pub use crate::lua::unified_cursor::{CLICK_OPTS, click_binds_lua, confine_lua, enter_desktop_lua};

use super::columns::EDGE_PX;
use super::free_pointer::{INSET_PX, JUMP_PX};
use super::python::{py_hypot, py_max, py_min, py_round};
use super::sampler::Side;
use crate::resize::OutputRect;

/// `PUSH_PX, PUSH_WINDOW_S`: zoomed in, PUSH_PX of push past one edge, each part within PUSH_WINDOW_S of the last,
/// switches to the desktop placed that way.
pub const PUSH_PX: f64 = 48.0;
pub const PUSH_WINDOW_S: f64 = 0.25;
/// `NUDGE_PX`: a touch on an edge Hyprland clamps the cursor at counts this much, and the cursor goes this far back in.
pub const NUDGE_PX: f64 = 12.0;
/// `SWITCH_COOLDOWN_S`: after a switch no push counts for this long.
pub const SWITCH_COOLDOWN_S: f64 = 0.8;
/// `PAN_HOLD_PX, PAN_LEASH_PX, PAN_PULL_PX`: during a pan the hidden cursor waits PAN_HOLD_PX inside the edge, goes
/// back there once PAN_LEASH_PX off, and a pan that ended puts it PAN_PULL_PX inside the edge.
pub const PAN_HOLD_PX: f64 = 64.0;
pub const PAN_LEASH_PX: f64 = 24.0;
pub const PAN_PULL_PX: f64 = 2.0;
/// `ENTRY_PX`: the cursor enters the new desktop this far inside the edge it came in over.
pub const ENTRY_PX: f64 = 24.0;
/// `CARRY_INSET_PX, CARRY_LEASH_PX`: a Super drag between desktops waits CARRY_INSET_PX inside the edge it left by,
/// and goes back there once it strays CARRY_LEASH_PX.
pub const CARRY_INSET_PX: f64 = 12.0;
pub const CARRY_LEASH_PX: f64 = 6.0;
/// `PENDING_S`: a requested switch the loop has not made by then is dropped.
pub const PENDING_S: f64 = 0.5;
/// `OVERVIEW_LATERAL_PX`: the overview push ends once the hand moves this far along the edge since it left.
pub const OVERVIEW_LATERAL_PX: f64 = 48.0;
/// `OVERVIEW_LAND_S`: after an overview pan lands no push arms for this long.
pub const OVERVIEW_LAND_S: f64 = 0.35;
/// `SIDE_EDGE`: a push side to free_pointer's name for that resize edge.
pub const SIDE_EDGE: [(&str, &str); 4] = [("right", "r"), ("left", "l"), ("up", "t"), ("down", "b")];

/// `SIDE_EDGE[side]`.
pub fn side_edge(side: Side) -> &'static str {
    match side {
        Side::Right => "r",
        Side::Left => "l",
        Side::Up => "t",
        Side::Down => "b",
    }
}

/// A layout point, fractional as Hyprland gives it.
pub type Point = (f64, f64);

fn contains(o: &OutputRect, c: Point) -> bool {
    let (lx, ly) = (c.0 - o.x as f64, c.1 - o.y as f64);
    0.0 <= lx && lx < o.width && 0.0 <= ly && ly < o.height
}

/// `_output_at(outputs, cursor)`: the output the cursor is on, the first in `outputs`.
pub fn output_at(outputs: &[OutputRect], cursor: Point) -> Option<&OutputRect> {
    outputs.iter().find(|o| contains(o, cursor))
}

/// The output whose index is `i` (the Python's `next((o for o in outputs if o.index == i), None)`).
pub fn by_index(outputs: &[OutputRect], i: i64) -> Option<&OutputRect> {
    outputs.iter().find(|o| o.index as i64 == i)
}

/// `inside(o, cursor, inset=INSET_PX)`: the integer layout pixel of `o` nearest `cursor`, at least `inset` px inside
/// every edge (`int`, so truncated).
pub fn inside(o: &OutputRect, cursor: Point, inset: f64) -> (i64, i64) {
    let (x, y) = (o.x as f64, o.y as f64);
    (
        py_min(py_max(cursor.0, x + inset), x + o.width - 1.0 - inset).trunc() as i64,
        py_min(py_max(cursor.1, y + inset), y + o.height - 1.0 - inset).trunc() as i64,
    )
}

/// `confine_target(outputs, home, cursor, previous=None)`: zoomed in, where to put the cursor back on output `home`,
/// or None to leave it. On another XR output: back. Off the XR outputs: back only if the previous sample was on home
/// and it is within JUMP_PX of home (the hand, not a warp).
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
            let dx = py_max(py_max(x - cursor.0, 0.0), cursor.0 - (x + o.width - 1.0));
            let dy = py_max(py_max(y - cursor.1, 0.0), cursor.1 - (y + o.height - 1.0));
            if py_hypot(dx, dy) > JUMP_PX {
                return None;
            }
        }
    }
    Some(inside(o, cursor, INSET_PX))
}

/// `_contact(o, cursor)`: the edges of `o` whose outermost pixel the cursor (on `o`) is on, horizontal first.
pub fn contact(o: &OutputRect, c: Point) -> Vec<Side> {
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

/// `_crossing(o, cursor)`: (edge, px past it) of a cursor off `o`, the axis it is furthest past (ties horizontal).
pub fn crossing(o: &OutputRect, c: Point) -> (Side, f64) {
    let (lx, ly) = (c.0 - o.x as f64, c.1 - o.y as f64);
    let dx = lx - py_min(py_max(lx, 0.0), o.width - 1.0);
    let dy = ly - py_min(py_max(ly, 0.0), o.height - 1.0);
    if dx.abs() >= dy.abs() {
        (if dx > 0.0 { Side::Right } else { Side::Left }, dx.abs())
    } else {
        (if dy > 0.0 { Side::Down } else { Side::Up }, dy.abs())
    }
}

/// `_nudge(o, cursor, side)`: the cursor NUDGE_PX back inside `o` from edge `side`, the other coordinate kept.
pub fn nudge(o: &OutputRect, c: Point, side: Side) -> (i64, i64) {
    let (x, y) = inside(o, c, INSET_PX);
    let (x, y) = (x as f64, y as f64);
    let (ox, oy) = (o.x as f64, o.y as f64);
    let p = match side {
        Side::Left => (ox + NUDGE_PX, y),
        Side::Right => (ox + o.width - 1.0 - NUDGE_PX, y),
        Side::Up => (x, oy + NUDGE_PX),
        Side::Down => (x, oy + o.height - 1.0 - NUDGE_PX),
    };
    inside(o, p, INSET_PX)
}

/// `along(o, cursor, side)`: how far along edge `side` of `o` the cursor is, 0..1 (left / top to right / bottom).
pub fn along(o: &OutputRect, c: Point, side: Side) -> f64 {
    let p = inside(o, c, 0.0);
    let (lx, ly) = ((p.0 - o.x) as f64, (p.1 - o.y) as f64);
    match side {
        Side::Up | Side::Down => lx / py_max(o.width - 1.0, 1.0),
        Side::Left | Side::Right => ly / py_max(o.height - 1.0, 1.0),
    }
}

/// `_band(o, cursor)`: Left / Right, the cursor (on `o`) within columns.EDGE_PX of that edge; or None.
pub fn band(o: &OutputRect, c: Point) -> Option<Side> {
    let lx = c.0 - o.x as f64;
    if lx >= o.width - EDGE_PX {
        Some(Side::Right)
    } else if lx <= EDGE_PX - 1.0 {
        Some(Side::Left)
    } else {
        None
    }
}

/// `hold_point(o, cursor, side)`: a push held at edge `side` (left / right) of `o` for its columns, in the edge band
/// 1 px in from the outermost pixel, as far along the edge as it is.
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
        Side::Up => (py_round(t * w) as f64, near),
        Side::Down => (py_round(t * w) as f64, h - near),
        Side::Left => (near, py_round(t * h) as f64),
        Side::Right => (w - near, py_round(t * h) as f64),
    };
    inside(o, (o.x as f64 + local.0, o.y as f64 + local.1), INSET_PX)
}

/// `pan_point(o, side, t, inset)`: `inset` px inside edge `side` of `o`, fraction `t` along it.
pub fn pan_point(o: &OutputRect, side: Side, t: f64, inset: f64) -> (i64, i64) {
    edge_point(o, side, t, inset)
}

/// `entry_point(o, side, t, inset=ENTRY_PX)`: where the cursor enters `o` after a push past the old desktop's `side`
/// edge, `inset` px inside the opposite edge (pushed up, it comes in at the bottom), fraction `t` along it.
pub fn entry_point(o: &OutputRect, side: Side, t: f64, inset: f64) -> (i64, i64) {
    let opposite = match side {
        Side::Up => Side::Down,
        Side::Down => Side::Up,
        Side::Left => Side::Right,
        Side::Right => Side::Left,
    };
    edge_point(o, opposite, t, inset)
}
