//! Scroll before switch: a push held at home's left or right edge while a scrolling-layout column is left that way.
//!
//! A port of `tools/native_spike/columns.py`: [`logical_rect`], [`columns_past`], [`read`] and [`ColumnScroll`]. The
//! scroll Lua is [`crate::lua::columns::scroll_lua`] and the address pattern `0x[0-9a-f]{1,16}\Z` is
//! [`crate::py::is_address`]. The sampler thread's edge push ([`super::sampler::push::EdgePush`]) asks
//! [`ColumnScroll::held`] about every sample at or past home's left or right edge. While a column is left that way the
//! push counts for nothing, and the column is scrolled in here unless scroll-edge.py does it first.
//!
//! Hyprland's replies are read the Python way ([`crate::py`]): a bool is not a number, `==` works across int, float and
//! bool, and truthiness is Python's. A layout's positions are kept as `f64`, which equals the Python's int and float
//! values exactly up to 2^53.

use std::time::Duration;

use serde_json::Value;

use super::sampler::{CallError, Side};
use crate::hyprland::Query;
use crate::py;

/// A client this far past an edge, or less, is not past it (scroll-edge.py's own +1 / -1).
pub const TOLERANCE_PX: f64 = 1.0;
/// The last 2 px at the left / right edge are "at the edge" (scroll-edge.py EDGE_PX).
pub const EDGE_PX: f64 = 2.0;
/// Held at an edge: the layout is read again after this long.
pub const REFRESH_S: f64 = 0.1;
/// A layout change counts as just now only if the read before it is this recent.
pub const CONTINUOUS_S: f64 = 0.5;
/// At an edge this long before this scrolls the first column (scroll-edge.py acts well within it).
pub const SCROLL_DWELL_S: f64 = 0.15;
/// Then a column each time this long (scroll-edge.py COOLDOWN).
pub const SCROLL_REPEAT_S: f64 = 0.35;
/// After a scroll this did not make, this long without another before this scrolls.
pub const HELPER_GRACE_S: f64 = 0.8;
/// This scroll's change to the layout is seen within this; none: that client does not move.
pub const OWN_SETTLE_S: f64 = 0.25;
/// No column left: the push rests this long after the layout last changed.
pub const END_REST_S: f64 = 0.6;
/// A hold no sample has touched for this long is over (the cursor left the edge).
pub const HOLD_GAP_S: f64 = 0.1;
/// Each read, from the sampler thread (its cursorpos read has the same).
pub const QUERY_TIMEOUT: Duration = Duration::from_millis(250);

/// The compositor calls the scroller makes (`hyprland.HyprlandClient`'s `query` and `scroll_to_window`).
pub trait ColumnsClient {
    /// `monitors`, `workspaces` or `clients`, the reply as parsed JSON.
    fn query(&mut self, kind: Query, timeout: Duration) -> Result<Value, CallError>;
    fn scroll_to_window(&mut self, address: &str) -> Result<(), CallError>;
}

/// Why a reply was refused (the Python's `ValueError`, `KeyError` or `TypeError` on it).
#[derive(Debug, PartialEq)]
pub struct Malformed;

/// One tiled client as the layout compares it: its address, position and size.
pub type Placed = (String, [f64; 4]);

/// A monitor's columns: the tiled clients past each edge, nearest first, and every tiled client sorted.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Past {
    pub left: Vec<String>,
    pub right: Vec<String>,
    pub layout: Vec<Placed>,
}

/// A monitor's columns now ([`read`]): `past` is empty when the workspace is not scrolling.
#[derive(Clone, Debug, PartialEq)]
pub struct Snap {
    pub name: String,
    pub workspace: i64,
    pub scrolling: bool,
    pub past: Past,
}

impl Snap {
    fn side(&self, side: Side) -> &[String] {
        match side {
            Side::Left => &self.past.left,
            Side::Right => &self.past.right,
            Side::Up | Side::Down => &[],
        }
    }
}

/// Monitor `m`'s (`j/monitors`) layout rectangle (x, y, w, h) in logical px, as scroll-edge.py computes it.
pub fn logical_rect(m: &Value) -> Result<[f64; 4], Malformed> {
    let [w, h, x, y, scale] = ["width", "height", "x", "y", "scale"].map(|k| py::number(m.get(k)));
    let (Some(mut w), Some(mut h), Some(x), Some(y), Some(scale)) = (w, h, x, y, scale) else {
        return Err(Malformed);
    };
    let transform = match m.get("transform") {
        None => 0,
        v => py::int(v).ok_or(Malformed)?,
    };
    if scale <= 0.0 || w <= 0.0 || h <= 0.0 {
        return Err(Malformed);
    }
    if transform.rem_euclid(2) == 1 {
        std::mem::swap(&mut w, &mut h);
    }
    Ok([x, y, w / scale, h / scale])
}

/// A list of two finite numbers.
fn pair(v: Option<&Value>) -> Option<[f64; 2]> {
    match py::list(v)?.as_slice() {
        [a, b] => Some([py::number(Some(a))?, py::number(Some(b))?]),
        _ => None,
    }
}

/// The tiled clients of `workspace` past the left and right edge of `rect` ([`logical_rect`]), nearest first (the one
/// to focus next), and the layout as it is (every tiled client's address, position and size, to see it change).
/// Refused: a malformed tiled client.
pub fn columns_past(rect: [f64; 4], clients: &Value, workspace: i64) -> Result<Past, Malformed> {
    let [x, _, w, _] = rect;
    let right = x + w;
    let clients = clients.as_array().ok_or(Malformed)?;
    let mut tiled: Vec<(&str, [f64; 4])> = Vec::new();
    for c in clients {
        let Some(ws) = c.get("workspace").filter(|v| v.is_object()) else {
            continue;
        };
        if !c.is_object() || !py::eq_int(ws.get("id"), workspace) {
            continue;
        }
        let mapped = c.get("mapped").is_none_or(|v| py::truthy(Some(v)));
        if py::truthy(c.get("floating")) || !mapped || py::truthy(c.get("hidden")) {
            continue;
        }
        let address = py::text(c.get("address")).filter(|a| py::is_address(a));
        let (Some(address), Some(at), Some(size)) = (address, pair(c.get("at")), pair(c.get("size")))
        else {
            return Err(Malformed);
        };
        tiled.push((address, [at[0], at[1], size[0], size[1]]));
    }
    let order = |a: f64, b: f64| a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal);
    let mut past_right: Vec<_> = tiled
        .iter()
        .filter(|(_, p)| p[0] + p[2] > right + TOLERANCE_PX)
        .collect();
    past_right.sort_by(|a, b| order(a.1[0], b.1[0]));
    let mut past_left: Vec<_> = tiled.iter().filter(|(_, p)| p[0] < x - TOLERANCE_PX).collect();
    past_left.sort_by(|a, b| order(-(a.1[0] + a.1[2]), -(b.1[0] + b.1[2])));
    let mut layout: Vec<Placed> = tiled.iter().map(|(a, p)| (a.to_string(), *p)).collect();
    layout.sort_by(|a, b| {
        a.0.cmp(&b.0).then_with(|| {
            a.1.iter()
                .zip(&b.1)
                .map(|(p, q)| order(*p, *q))
                .find(|o| o.is_ne())
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    });
    Ok(Past {
        left: past_left.iter().map(|(a, _)| a.to_string()).collect(),
        right: past_right.iter().map(|(a, _)| a.to_string()).collect(),
        layout,
    })
}

/// Why [`read`] failed: the call, or its reply.
#[derive(Debug, PartialEq)]
pub enum ReadError {
    Call(CallError),
    Malformed,
}

impl From<CallError> for ReadError {
    fn from(e: CallError) -> Self {
        ReadError::Call(e)
    }
}

impl From<Malformed> for ReadError {
    fn from(_: Malformed) -> Self {
        ReadError::Malformed
    }
}

/// Monitor `name`'s columns now, or `None` (no such monitor, or disabled).
pub fn read(
    client: &mut (impl ColumnsClient + ?Sized),
    name: &str,
    timeout: Duration,
) -> Result<Option<Snap>, ReadError> {
    let monitors = client.query(Query::Monitors, timeout)?;
    let monitors = monitors.as_array().ok_or(Malformed)?;
    let Some(m) = monitors
        .iter()
        .find(|m| m.is_object() && py::text(m.get("name")) == Some(name))
    else {
        return Ok(None);
    };
    if py::truthy(m.get("disabled")) {
        return Ok(None);
    }
    // m['activeWorkspace']['id']: a missing key or a non-dict raises.
    let ws = m
        .get("activeWorkspace")
        .filter(|a| a.is_object())
        .and_then(|a| a.get("id"))
        .ok_or(Malformed)?;
    let ws = py::int(Some(ws)).ok_or(Malformed)?;
    let rect = logical_rect(m)?;
    let workspaces = client.query(Query::Workspaces, timeout)?;
    let workspaces = workspaces.as_array().ok_or(Malformed)?;
    let w = workspaces
        .iter()
        .find(|w| w.is_object() && py::eq_int(w.get("id"), ws));
    let mut out = Snap {
        name: name.to_string(),
        workspace: ws,
        scrolling: false,
        past: Past::default(),
    };
    let Some(w) = w else { return Ok(Some(out)) };
    if py::text(w.get("tiledLayout")) != Some("scrolling") || py::truthy(w.get("hasfullscreen")) {
        return Ok(Some(out));
    }
    let clients = client.query(Query::Clients, timeout)?;
    out.past = columns_past(rect, &clients, ws)?;
    out.scrolling = true;
    Ok(Some(out))
}

/// The cursor at / past an edge of home: the monitor, the edge, since when, and the last sample there.
#[derive(Clone, Debug, PartialEq)]
struct Hold {
    name: String,
    side: Side,
    since: f64,
    last: f64,
}

/// The sampler thread's scroll-before-switch state. One per run, used from the sampler thread only.
#[derive(Clone, Debug, PartialEq)]
pub struct ColumnScroll {
    /// The last read (`None`: none, or it failed), of which monitor, and when.
    snap: Option<Snap>,
    read_name: Option<String>,
    read_at: f64,
    hold: Option<Hold>,
    /// When the layout was last seen to change.
    changed: f64,
    /// That change was not this one's scroll (scroll-edge.py, a reveal, a new window).
    foreign: bool,
    /// This one's scroll, its change not seen yet: (workspace, layout before, at).
    own: Option<(i64, Vec<Placed>, f64)>,
    /// A scroll that moved nothing: nothing is past while the layout stays so.
    stuck: Option<(i64, Vec<Placed>)>,
    pub scrolls: u64,
    pub yields: u64,
    pub reads: u64,
    pub errors: u64,
}

impl Default for ColumnScroll {
    fn default() -> Self {
        Self::new()
    }
}

impl ColumnScroll {
    pub fn new() -> Self {
        ColumnScroll {
            snap: None,
            read_name: None,
            read_at: f64::NEG_INFINITY,
            hold: None,
            changed: f64::NEG_INFINITY,
            foreign: false,
            own: None,
            stuck: None,
            scrolls: 0,
            yields: 0,
            reads: 0,
            errors: 0,
        }
    }

    /// The cursor is at, or pushed past, monitor `name`'s `side` edge (left or right; up and down answer `false`).
    /// `true`: the push is held there and counts for nothing (a column is left that way, scrolled here unless
    /// scroll-edge.py does; or the end came under END_REST_S ago). `confirm`: a push about to switch: read again
    /// first, and only answer.
    pub fn held(
        &mut self,
        client: &mut (impl ColumnsClient + ?Sized),
        name: &str,
        side: Side,
        now: f64,
        confirm: bool,
    ) -> bool {
        if !side.horizontal() {
            return false; // the Python raises: EdgePush never asks
        }
        let restart = self
            .hold
            .as_ref()
            .is_none_or(|h| h.name != name || h.side != side || now - h.last > HOLD_GAP_S);
        if restart {
            self.hold = Some(Hold {
                name: name.to_string(),
                side,
                since: now,
                last: now,
            });
        }
        if let Some(h) = &mut self.hold {
            h.last = now;
        }
        self.read(client, name, now, confirm);
        let Some(snap) = &self.snap else { return false };
        if !snap.scrolling {
            return false;
        }
        if !self.past(side).is_empty() {
            if !confirm {
                self.scroll(client, name, side, now);
            }
            return true;
        }
        now - self.changed < END_REST_S
    }

    /// The clients past `side` in the last read, unless that layout is stuck.
    fn past(&self, side: Side) -> &[String] {
        let Some(snap) = &self.snap else { return &[] };
        if self
            .stuck
            .as_ref()
            .is_some_and(|(ws, layout)| *ws == snap.workspace && *layout == snap.past.layout)
        {
            return &[];
        }
        snap.side(side)
    }

    /// The layout of monitor `name` into `snap`, read at most every REFRESH_S unless `fresh`; `None` if not readable.
    /// A change from the read before (same workspace, that read CONTINUOUS_S old at most) is a change just now.
    fn read(&mut self, client: &mut (impl ColumnsClient + ?Sized), name: &str, now: f64, fresh: bool) {
        let same = self.read_name.as_deref() == Some(name);
        if !fresh && same && now - self.read_at < REFRESH_S {
            return;
        }
        let (prev, prev_at) = if same {
            (self.snap.take(), self.read_at)
        } else {
            (None, f64::NEG_INFINITY)
        };
        let snap = match read(client, name, QUERY_TIMEOUT) {
            Ok(snap) => snap,
            Err(_) => {
                self.errors += 1;
                None
            }
        };
        self.reads += 1;
        if !same {
            self.read_name = Some(name.to_string());
        }
        self.read_at = now;
        if let Some((ws, layout, at)) = self.own.take() {
            if now - at > OWN_SETTLE_S {
                self.stuck = Some((ws, layout)); // the focus moved nothing: that client cannot come in
            } else {
                self.own = Some((ws, layout, at));
            }
        }
        match (&snap, &prev) {
            (Some(s), Some(p)) if p.workspace == s.workspace => {
                if s.past.layout != p.past.layout && now - prev_at <= CONTINUOUS_S {
                    self.foreign = self.own.is_none(); // after this one's scroll, the change is that scroll's
                    if self.foreign {
                        self.yields += 1;
                    }
                    self.changed = now;
                    self.own = None;
                }
            }
            _ => {
                // Nothing to compare with (a new monitor or workspace, a failed read): a new start.
                self.changed = f64::NEG_INFINITY;
                self.foreign = false;
                self.own = None;
                let keep = match (&snap, &self.stuck) {
                    (Some(s), Some((ws, _))) => *ws == s.workspace,
                    _ => false,
                };
                if !keep {
                    self.stuck = None;
                }
            }
        }
        self.snap = snap;
    }

    /// Held with a column left that way: scroll it in, unless it is not time yet or scroll-edge.py just did.
    fn scroll(&mut self, client: &mut (impl ColumnsClient + ?Sized), name: &str, side: Side, now: f64) {
        if self.own.is_some() {
            return; // the last scroll's change is not seen yet
        }
        let wait = if self.foreign {
            HELPER_GRACE_S
        } else {
            SCROLL_REPEAT_S
        };
        let since = self.hold.as_ref().map_or(now, |h| h.since);
        if now < since + SCROLL_DWELL_S || now < self.changed + wait {
            return;
        }
        let (before, ws) = (self.changed, self.snap.as_ref().map(|s| s.workspace));
        self.read(client, name, now, true); // the newest layout: scroll-edge.py may just have scrolled it
        let Some(snap) = &self.snap else { return };
        if !snap.scrolling || Some(snap.workspace) != ws || self.changed != before {
            return; // it did (that was this step), or the workspace changed under the hold: a new start
        }
        let Some(first) = self.past(side).first() else { return };
        let (workspace, layout) = (snap.workspace, snap.past.layout.clone());
        if client.scroll_to_window(first).is_err() {
            self.errors += 1;
            return;
        }
        self.scrolls += 1;
        self.own = Some((workspace, layout, now));
        self.read(client, name, now, true); // Hyprland lays the columns out on the dispatch: normally seen at once
    }

    /// The corpus's view of the state (the Python's attributes).
    #[cfg(test)]
    pub(crate) fn state(&self) -> Value {
        use serde_json::json;
        let layout = |l: &[Placed]| -> Value {
            l.iter()
                .map(|(a, p)| json!([a, p[0], p[1], p[2], p[3]]))
                .collect()
        };
        let snap = self.snap.as_ref().map(|s| {
            json!({"name": s.name, "workspace": s.workspace, "scrolling": s.scrolling, "left": s.past.left,
                   "right": s.past.right, "layout": layout(&s.past.layout)})
        });
        json!({
            "snap": snap,
            "read_name": self.read_name,
            "read_at": super::sampler::test_float(self.read_at),
            "hold": self.hold.as_ref().map(|h| json!([h.name, h.side.as_str(), h.since, h.last])),
            "changed": super::sampler::test_float(self.changed),
            "foreign": self.foreign,
            "own": self.own.as_ref().map(|(ws, l, at)| json!([ws, layout(l), at])),
            "stuck": self.stuck.as_ref().map(|(ws, l)| json!([ws, layout(l)])),
            "scrolls": self.scrolls, "yields": self.yields, "reads": self.reads, "errors": self.errors,
        })
    }
}
