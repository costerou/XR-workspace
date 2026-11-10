//! `unified_cursor.UnifiedPointer` (tools/native_spike/unified_cursor.py lines 387-929): the overview free pointer with
//! the real cursor on home. It is an [`OverviewPointer`] whose `ext` holds the state below; the overridden methods
//! dispatch here (`u_*`), and `*_base` is the Python's `super()`.
//!
//! The sampler thread's calls (`neighbour`, `pan_plan`, `set_pan`, `request_switch`, `can_switch`, `can_scroll`) take
//! the pointer as it is; whoever shares it across threads holds it under one lock (the Python's `_lock`).

use std::sync::Arc;

use super::edge_pan::{self, PanFrame, Plan};
use super::free_pointer::{self as fp, Hit};
use super::neighbors::{self, Direction};
use super::orbit::Pose;
use super::overview_pointer::{Control, Drag, Found, OVERLAY_IDLE_CLOSE_S, OverviewPointer, RESIZE_OPS, window_press};
use super::python::{Invalid, Result, py_hypot, py_max, py_min};
use super::sampler::{Clock, Mode, Side, park_point};
use super::unified_cursor::{CARRY_INSET_PX, ENTRY_PX, OVERVIEW_LAND_S, OVERVIEW_LATERAL_PX, along, inside, side_edge};
use crate::lua::view::SUPER_OPS;
use crate::pyjson::Json;
use crate::resize::OutputRect;

/// Over home, left button down: the dot and time at the press.
#[derive(Clone, Copy, Debug)]
pub struct Click {
    pub start: Option<[f64; 2]>,
    pub at: f64,
    pub moved: bool,
}

/// The overview push from the exit until the pan starts: travel, logical px of push so far; lateral, along the edge.
#[derive(Clone, Copy, Debug)]
pub struct Arm {
    pub plan: Plan,
    pub travel: f64,
    pub lateral: f64,
}

/// The overview pan under way: travel past START_PX; moved, when the hand last moved; pointer, the free dot it took
/// over from.
#[derive(Clone, Copy, Debug)]
pub struct Span {
    pub plan: Plan,
    pub travel: f64,
    pub moved: f64,
    pub pointer: Option<[f64; 2]>,
}

/// The unified cursor's own state (`UnifiedPointer.__init__`).
#[derive(Debug)]
pub struct Unified {
    /// Over home, left button down.
    pub click: Option<Click>,
    // Diagnostics.
    pub rehomes: u64,
    pub pullbacks: u64,
    pub window_clicks: u64,
    pub follows: u64,
    pub desktop_presses: u64,
    /// Zoomed in: (home, target, panned) the sampler asked the loop to switch to (EdgePush).
    pub switch: Option<(usize, usize, bool)>,
    /// The edge pan's latest payload (edge_pan.py), or None.
    pub pan: Option<PanFrame>,
    /// Not yet sent.
    pub pan_dirty: bool,
    pub edge_switches: u64,
    /// 'move' / 'resize': a Super drag of Hyprland's is under way (on_super).
    pub super_drag: Option<&'static str>,
    /// It went through carry mode.
    pub super_crossed: bool,
    /// Diagnostics: drops that asked for a landing.
    pub landings: u64,
    /// Diagnostics: Super drags that left a desktop.
    pub carries: u64,
    /// The overview push (unified_cursor's module docstring: Overview push).
    pub arm: Option<Arm>,
    pub span: Option<Span>,
    /// The last `edge_pan` payload sent for it.
    pub span_sent: Option<PanFrame>,
    /// When the last overview pan landed.
    pub landed_at: f64,
    /// Diagnostics: overview pans that landed / ended on home.
    pub overview_pans: u64,
    pub overview_pulls: u64,
}

impl Default for Unified {
    fn default() -> Unified {
        Unified {
            click: None,
            rehomes: 0,
            pullbacks: 0,
            window_clicks: 0,
            follows: 0,
            desktop_presses: 0,
            switch: None,
            pan: None,
            pan_dirty: false,
            edge_switches: 0,
            super_drag: None,
            super_crossed: false,
            landings: 0,
            carries: 0,
            arm: None,
            span: None,
            span_sent: None,
            landed_at: f64::NEG_INFINITY,
            overview_pans: 0,
            overview_pulls: 0,
        }
    }
}

/// Frames, or None for none (the Python's `out or None`).
fn some(out: Vec<u8>) -> Option<Vec<u8>> {
    (!out.is_empty()).then_some(out)
}

fn direction(side: Side) -> Direction {
    Direction::parse(side.as_str()).expect("the same four sides")
}

fn side(direction: Direction) -> Side {
    Side::parse(direction.name()).expect("the same four sides")
}

/// `_axis(side, dx, dy)`: (along the push, along the edge) of a motion: + is the push's way.
fn axis(side: Direction, dx: f64, dy: f64) -> (f64, f64) {
    match side {
        Direction::Right => (dx, dy),
        Direction::Left => (-dx, dy),
        Direction::Down => (dy, dx),
        Direction::Up => (-dy, dx),
    }
}

/// The cursor as the layout point the Python's helpers take.
fn point(c: (i64, i64)) -> (f64, f64) {
    (c.0 as f64, c.1 as f64)
}

/// The unified cursor: the overview pointer with its overrides on.
pub struct UnifiedPointer<C: Control>(pub OverviewPointer<C>);

impl<C: Control> UnifiedPointer<C> {
    /// `UnifiedPointer(control, clock)`.
    pub fn new(control: C, clock: Box<dyn Clock + Send>) -> std::io::Result<Self> {
        let mut p = OverviewPointer::new(control, clock)?;
        p.ext = Some(Box::default());
        Ok(UnifiedPointer(p))
    }
}

impl<C: Control> std::ops::Deref for UnifiedPointer<C> {
    type Target = OverviewPointer<C>;
    fn deref(&self) -> &OverviewPointer<C> {
        &self.0
    }
}

impl<C: Control> std::ops::DerefMut for UnifiedPointer<C> {
    fn deref_mut(&mut self) -> &mut OverviewPointer<C> {
        &mut self.0
    }
}

impl<C: Control> OverviewPointer<C> {
    /// The unified state; only called with `ext` set.
    pub fn unified(&self) -> &Unified {
        self.ext.as_deref().expect("a UnifiedPointer")
    }

    pub fn unified_mut(&mut self) -> &mut Unified {
        self.ext.as_deref_mut().expect("a UnifiedPointer")
    }

    fn super_drag(&self) -> Option<&'static str> {
        self.ext.as_ref().and_then(|u| u.super_drag)
    }

    /// `overview()`: as free_pointer's, and in typing mode too while a Super move lasts: that is what carries it across
    /// the gaps. It needs nothing else of the overview (no pills, _handles_on) and goes off again with the drag. A
    /// resize stays on its desktop, which the plain confinement does in typing mode.
    pub(crate) fn u_overview(&self) -> bool {
        let s = self.control.state();
        if self.super_drag() == Some("move") && s.typing {
            return !s.zoomed && !self.control.video_active();
        }
        self.overview_base()
    }

    pub(crate) fn u_handles_on(&self) -> bool {
        self.active() && !self.control.state().typing
    }

    // --- zoomed in: the push past an edge -----------------------------------------------------------------------

    /// `can_switch()`: a push past home's edge switches desktops: zoomed in, not in typing mode and with no VR video (as
    /// the Ctrl+Alt+Left / Right keys, whose binds typing mode and a video release).
    pub fn can_switch(&self) -> bool {
        let s = self.control.state();
        s.zoomed && !s.typing && !self.control.video_active()
            && self.super_drag().is_none() // never mid Super drag: no switch, the cursor just stays on home
    }

    /// `can_scroll()`: a push at home's left / right edge scrolls a scrolling-layout workspace's columns before it
    /// switches (columns.py): zoomed in, in typing mode too (switching is what needs the keys' binds; scrolling needs
    /// none), never during a VR video.
    pub fn can_scroll(&self) -> bool {
        self.control.state().zoomed && !self.control.video_active()
    }

    /// `neighbour(edge, along=0.5)`: the desktop placed past home's `edge` round you, seen from the point `along` of the
    /// way along that edge (neighbors.pick_at on the renderer's overview layout and the placements as drawn: without
    /// their rolls, home being level while maximized and a neighbour's roll turning it about its own centre), or None.
    /// Without a layout with one slot per desktop: the row order (neighbors.row_fallback). Read from the sampler
    /// thread: the layout and placements are replaced, never changed in place.
    pub fn neighbour(&self, edge: Direction, along: f64) -> Option<usize> {
        let s = self.control.state();
        let (home, count) = (s.selected, s.names.len());
        match &self.geometry {
            Some(g) if g.tiles.len() == count => {
                let placements: Vec<Pose> = s.placements.iter().map(|p| Pose::from(*p)).collect();
                neighbors::pick_at(g, &placements, home, edge, along, neighbors::PAN_CONE)
            }
            _ => neighbors::row_fallback(count, home, edge),
        }
    }

    /// `pan_plan(target, edge, along, output)`: the push's edge_pan.Plan, or None without a layout with one slot per
    /// desktop (it then switches with a cut).
    pub fn pan_plan(&self, target: usize, edge: Direction, along: f64, output: &OutputRect) -> Option<Plan> {
        let s = self.control.state();
        let g = self.geometry.as_ref().filter(|g| g.tiles.len() == s.names.len())?;
        // by name, as the renderer's are (dict(zip(...)): the last of a name wins)
        let roll = |n: &String| {
            self.roll_names.iter().zip(&self.rolls).rev().find(|(m, _)| *m == n).map_or(0, |(_, r)| *r) as f64
        };
        let rolls: Vec<f64> = s.names.iter().map(roll).collect();
        let placements: Vec<Pose> = s.placements.iter().map(|p| Pose::from(*p)).collect();
        edge_pan::plan(
            g,
            &placements,
            s.selected,
            target,
            edge,
            along,
            (output.width, output.height),
            Some(&rolls),
            false,
        )
    }

    /// `set_pan(payload)`: sampler thread: the edge pan's payload now; the loop sends the newest (poll), so a slow loop
    /// only skips some.
    pub fn set_pan(&mut self, payload: Option<PanFrame>) {
        let u = self.unified_mut();
        if payload == u.pan {
            return;
        }
        (u.pan, u.pan_dirty) = (payload, true);
        self.poke();
    }

    fn take_pan(&mut self) -> Vec<u8> {
        let u = self.unified_mut();
        if !u.pan_dirty {
            return Vec::new();
        }
        u.pan_dirty = false;
        let payload = u.pan;
        self.control.pan_frame(payload.as_ref())
    }

    /// `request_switch(home, target, panned=False)`: sampler thread: the push past home's edge reached a neighbour; the
    /// loop switches (poll). `panned`: by hand, so it lands from the pan (edge_pan.py); otherwise it is a cut.
    pub fn request_switch(&mut self, home: usize, target: usize, panned: bool) {
        self.unified_mut().switch = Some((home, target, panned));
        self.poke();
    }

    fn take_switch(&mut self) -> Vec<u8> {
        let Some((home, target, panned)) = self.unified_mut().switch.take() else { return Vec::new() };
        let mut out = if self.can_switch() && self.home() == home {
            self.control.switch_desktop(target, panned)
        } else {
            Vec::new()
        };
        if !out.is_empty() {
            self.unified_mut().edge_switches += 1;
        } else if panned {
            // refused: the view goes back to home
            let u = self.unified_mut();
            (u.pan, u.pan_dirty) = (None, false);
            out = self.control.pan_frame(None);
        }
        out
    }

    fn home(&self) -> usize {
        self.control.state().selected
    }

    // --- modes --------------------------------------------------------------------------------------------------

    pub(crate) fn u_poll(&mut self, now: f64) -> Vec<u8> {
        if !self.active() || now - self.last_move >= OVERLAY_IDLE_CLOSE_S {
            self.unified_mut().click = None;
        }
        let mut out = if self.super_drag().is_some() && now - self.last_move >= OVERLAY_IDLE_CLOSE_S {
            self.super_end()
        } else {
            Vec::new()
        };
        out.extend(self.take_pan());
        out.extend(self.take_switch());
        if let Some(span) = self.unified().span
            && (!self.active() || self.mode != Mode::Free || self.drag.is_some() || now - span.moved >= edge_pan::REST_S)
        {
            out.extend(self.span_end()); // gone from under it, or the hand rested: the view goes back
        }
        if self.unified().arm.is_some() && (!self.active() || self.mode != Mode::Free) {
            self.unified_mut().arm = None;
        }
        if self.active() && self.mode.dot() && self.desktop != Some(self.home()) {
            out.extend(self.to_home(now)); // a key changed the selected desktop: the cursor goes with it
            self.unified_mut().rehomes += 1;
        } else if self.mode == Mode::Free && self.active() {
            out.extend(self.follow(now));
        }
        out.extend(self.poll_base(now));
        if self.unified().click.is_some() && self.mode == Mode::Desktop && self.active() {
            out.extend(self.hold_home(now));
        }
        out
    }

    /// `_to_home(now)`: desktop mode in the middle of home, the cursor warped there (a key switch, or a cursor that
    /// came onto another XR monitor).
    #[allow(clippy::wrong_self_convention)] // the Python's name
    fn to_home(&mut self, now: f64) -> Vec<u8> {
        let i = self.home();
        let o = self.output(Some(i));
        let out = self.end_drag();
        let Some(o) = o else {
            (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
            return out;
        };
        (self.mode, self.desktop, self.hover) = (Mode::Desktop, Some(i), None);
        self.unified_mut().click = None;
        self.pointer = self.from_logical(i, o.width / 2.0, o.height / 2.0);
        self.last_move = now;
        let middle = (o.x as f64 + o.width / 2.0, o.y as f64 + o.height / 2.0);
        self.set_want(Mode::Desktop, Some(inside(&o, middle, fp::INSET_PX)), true);
        out
    }

    /// `_absolute(cursor, now)`: away / desktop mode: the cursor's own position decides, on home only.
    pub(crate) fn u_absolute(&mut self, cursor: (i64, i64), now: f64) -> Vec<u8> {
        let home = self.home();
        let Some(o) = self.output(Some(home)) else {
            (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
            return self.wants();
        };
        let at = self.at(cursor);
        let off_home = at.is_none_or(|a| a.0 != home);
        if self.mode == Mode::Away {
            if at.is_none() && !self.summoned {
                return self.wants();
            }
            self.summoned = false;
            if off_home {
                // Ctrl+Alt+Down, or another XR monitor
                let mut out = self.to_home(now);
                out.extend(self.wants());
                return out;
            }
            self.mode = Mode::Desktop;
        }
        self.summoned = false;
        self.desktop = Some(home);
        let (lx, ly) = ((cursor.0 - o.x) as f64, (cursor.1 - o.y) as f64);
        let (w, h) = (o.width, o.height);
        if fp::EDGE_PX <= lx && lx <= w - 1.0 - fp::EDGE_PX && fp::EDGE_PX <= ly && ly <= h - 1.0 - fp::EDGE_PX {
            let point = self.from_logical(home, lx, ly);
            if point.is_some() && point != self.pointer {
                (self.pointer, self.last_move) = (point, now);
            }
            self.onto_help();
            return self.wants();
        }
        let (cx, cy) = (py_min(py_max(lx, 0.0), w - 1.0), py_min(py_max(ly, 0.0), h - 1.0));
        let over = (lx - cx, ly - cy);
        if self.unified().click.is_some() || self.super_drag() == Some("resize") {
            // held: the window keeps the cursor, which stays on home
            if off_home {
                self.unified_mut().pullbacks += 1;
                self.set_want(Mode::Desktop, Some(inside(&o, point(cursor), fp::INSET_PX)), true);
            }
            let point = self.from_logical(home, cx, cy);
            if point.is_some() && point != self.pointer {
                (self.pointer, self.last_move) = (point, now);
            }
            return self.wants();
        }
        if py_hypot(over.0, over.1) > fp::JUMP_PX {
            // not the hand
            if at.is_none() {
                // a window sent back to the physical monitor took the cursor: let go
                (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
                return self.wants();
            }
            self.unified_mut().pullbacks += 1;
            let mut out = self.to_home(now);
            out.extend(self.wants());
            return out;
        }
        if self.super_drag() == Some("move") {
            return self.start_carry(home, &o, cursor, now);
        }
        // just past the edge it reached: on that edge's resize handle
        let Some(start) = self.exit(home, lx, ly) else { return self.wants() };
        self.gain = self.gain_of(home);
        self.pointer = Some(self.clamp(start));
        (self.mode, self.last_move) = (Mode::Free, now);
        self.exits += 1;
        let park = self.park(&o, Some((cx, cy)));
        self.set_want(Mode::Free, Some(park), false); // the grab parks it back on home
        self.hover = self.hit();
        self.arm_push(home, &o, lx, ly, now);
        self.wants()
    }

    // --- the overview push --------------------------------------------------------------------------------------

    /// `_arm_push(home, o, lx, ly, now)`: the cursor left home at monitor-local (lx, ly) and the dot is free: if it left
    /// over one edge (not a corner, whose cap it is on) and a desktop lies that way from that point of the edge
    /// (neighbors.pick_at, as maximized), pushing on will pan there (module docstring: Overview push).
    fn arm_push(&mut self, home: usize, o: &OutputRect, lx: f64, ly: f64, now: f64) {
        self.unified_mut().arm = None;
        if self.super_drag().is_some() || now - self.unified().landed_at < OVERVIEW_LAND_S {
            return;
        }
        let (w, h) = (o.width, o.height);
        let sides: Vec<Side> = [
            (Side::Left, lx < fp::EDGE_PX),
            (Side::Right, lx > w - 1.0 - fp::EDGE_PX),
            (Side::Up, ly < fp::EDGE_PX),
            (Side::Down, ly > h - 1.0 - fp::EDGE_PX),
        ]
        .into_iter()
        .filter_map(|(s, on)| on.then_some(s))
        .collect();
        let [side] = sides[..] else { return };
        let s = self.control.state();
        let Some(g) = self.geometry.as_ref().filter(|g| g.tiles.len() == s.names.len()) else { return };
        let t = along(o, (o.x as f64 + lx, o.y as f64 + ly), side);
        let placements: Vec<Pose> = s.placements.iter().map(|p| Pose::from(*p)).collect();
        let d = direction(side);
        let Some(target) = neighbors::pick_at(g, &placements, home, d, t, neighbors::PAN_CONE) else { return };
        let Some(plan) = edge_pan::plan(g, &placements, home, target, d, t, (w, h), None, true) else { return };
        let over = match side {
            Side::Left => -lx,
            Side::Right => lx - (w - 1.0),
            Side::Up => -ly,
            Side::Down => ly - (h - 1.0),
        }; // Hyprland let it that far
        self.unified_mut().arm = Some(Arm { plan, travel: py_max(over, 0.0), lateral: 0.0 });
    }

    /// `_move(dx, dy, now)`: free mode (free_pointer._move), and the overview push: armed, the dot never goes onto
    /// another desktop (it waits on home's edge while the hand pushes on: in a row the neighbour's edge is only px
    /// away), and the hand's push counts wherever the dot is on nothing but space or that edge (a pill or bar on the way
    /// holds the count, so each can still be reached); START_PX of it starts the pan. Under way, the hand's motion is
    /// the pan's (_span_move).
    pub(crate) fn u_move(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        if self.unified().span.is_some() {
            return self.span_move(dx, dy, now);
        }
        let Some(mut a) = self.unified().arm.filter(|_| self.drag.is_none()) else {
            self.unified_mut().arm = None;
            return self.move_base(dx, dy, now);
        };
        let (plan, gain) = (a.plan, if self.gain != 0.0 { self.gain } else { 1.0 });
        let (step, lateral) = axis(plan.side, dx / gain, dy / gain);
        a.lateral += lateral;
        if a.lateral.abs() > OVERVIEW_LATERAL_PX {
            // gone off along the edge: just the free dot
            self.unified_mut().arm = None;
            return self.move_base(dx, dy, now);
        }
        let Some(before) = self.pointer else { return self.move_base(dx, dy, now) };
        let elsewhere = |h: &Option<Hit>| {
            matches!(*h, Some(Hit::Desktop(i, _) | Hit::Edge(i, _) | Hit::Corner(i, _)) if i != plan.home)
        };
        // already on another desktop's content (put there, not pushed there): it goes on as ever. Its border may reach
        // over home's edge in a tight row: no
        let h = self.hit();
        let was = matches!(h, Some(Hit::Desktop(i, _)) if i != plan.home);
        self.pointer = Some(self.clamp([before[0] + dx, before[1] + dy]));
        self.last_move = now;
        if self.control.state().menu_open {
            self.control.set_last_input(now);
        }
        let mut hover = self.hit();
        let blocked = elsewhere(&hover) && !was;
        if blocked {
            // not onto another desktop, nor its handles: the dot waits where it was
            self.pointer = Some(before);
            hover = self.hit();
        }
        let on_edge = hover == Some(Hit::Edge(plan.home, side_edge(side(plan.side))));
        let counts = blocked || hover.is_none() || on_edge;
        self.hover = hover;
        if counts {
            a.travel = py_max(a.travel + step, 0.0);
        }
        self.unified_mut().arm = Some(a);
        let mut out = if blocked { Vec::new() } else { self.settle() }; // back onto home's content: the cursor is there again
        if self.mode != Mode::Free {
            self.unified_mut().arm = None;
            return out;
        }
        if a.travel >= edge_pan::START_PX {
            let span = Span { plan, travel: a.travel - edge_pan::START_PX, moved: now, pointer: self.pointer };
            let u = self.unified_mut();
            (u.arm, u.span) = (None, Some(span));
            self.hover = None;
            out.extend(self.span_frame());
        }
        out
    }

    fn span_frame(&mut self) -> Vec<u8> {
        let Some(span) = self.unified().span else { return Vec::new() };
        let payload = span.plan.frame(span.travel);
        if Some(payload) == self.unified().span_sent {
            return Vec::new();
        }
        self.unified_mut().span_sent = Some(payload);
        self.control.pan_frame(Some(&payload))
    }

    /// `_span_move(dx, dy, now)`: the overview pan under way (as EdgePush._pan, maximized): the hand's motion along the
    /// push, signed, is the pan's; back to its start ends it on home, reaching Plan.total_px lands it.
    fn span_move(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        let Some(mut p) = self.unified().span else { return Vec::new() };
        let (plan, gain) = (p.plan, if self.gain != 0.0 { self.gain } else { 1.0 });
        let (step, _) = axis(plan.side, dx / gain, dy / gain);
        self.last_move = now;
        if step != 0.0 {
            p.travel = py_max(p.travel + step, 0.0);
            p.moved = now;
        }
        self.unified_mut().span = Some(p);
        if step < 0.0 && p.travel <= 0.0 {
            // pulled back: the dot is free past the edge again, still armed
            let out = self.span_end();
            self.unified_mut().arm = Some(Arm { plan, travel: edge_pan::START_PX, lateral: 0.0 });
            return out;
        }
        if p.travel >= plan.total_px {
            return self.span_land(now);
        }
        self.span_frame()
    }

    /// `_span_end()`: the overview pan ends on home: the view goes back, and the free dot is where it was when the pan
    /// took over.
    fn span_end(&mut self) -> Vec<u8> {
        let u = self.unified_mut();
        let p = u.span.take();
        u.span_sent = None;
        let Some(p) = p else { return Vec::new() };
        u.overview_pulls += 1;
        if self.mode == Mode::Free {
            self.pointer = p.pointer;
            self.hover = self.hit();
        }
        self.control.pan_frame(None)
    }

    /// `_span_land(now)`: the hand reached the target: it is selected in the overview with `pan` (the renderer lands the
    /// turn, the room keeping it), and the real cursor comes in just inside its near edge at the entry point (_enter:
    /// desktop mode there).
    fn span_land(&mut self, now: f64) -> Vec<u8> {
        let u = self.unified_mut();
        let p = u.span.take().expect("a pan under way");
        u.span_sent = None;
        let plan = p.plan;
        let target = plan.target;
        let mut out = self.control.select_desktop(target, Some(true));
        let (tile, o, placement) = (self.tile(target), self.output(Some(target)), self.placement(target));
        let (Some(tile), Some(o), Some(placement), true, true) =
            (tile, o, placement, self.control.state().selected == target, self.active())
        else {
            self.pointer = p.pointer; // stale: the view goes back, the dot stays free
            out.extend(self.control.pan_frame(None));
            return out;
        };
        let near = plan.side.opposite();
        let (x, y) = edge_pan::edge_pixel(&tile, near, plan.entry);
        let q = if plan.side.sideways() {
            let inset = ENTRY_PX * tile[2] / o.width;
            if near == Direction::Left { [x + inset, y] } else { [x - inset, y] }
        } else {
            let inset = ENTRY_PX * tile[3] / o.height;
            if near == Direction::Up { [x, y + inset] } else { [x, y - inset] }
        };
        out.extend(self.enter(target, q));
        if let Some(g) = &self.geometry {
            self.pointer = Some(fp::project(g, &tile, &placement, q[0], q[1]));
        }
        self.last_move = now;
        let u = self.unified_mut();
        u.landed_at = now;
        u.overview_pans += 1;
        out
    }

    /// `_wants()`: the click binds go with desktop mode, and nothing is bound away from the desktops.
    fn wants(&mut self) -> Vec<u8> {
        if self.mode == Mode::Desktop && self.want.kind != Mode::Desktop {
            self.set_want(Mode::Desktop, None, false);
        } else if self.mode == Mode::Away && !matches!(self.want.kind, Mode::Away | Mode::Off) {
            self.set_want(Mode::Away, None, false);
        }
        Vec::new()
    }

    pub(crate) fn u_onto_help(&mut self) {
        if self.unified().click.is_none() && self.super_drag().is_none() {
            self.onto_help_base(); // a held button stays with its window
        }
    }

    /// `_held()`: the left button held on a window (desktop mode), or a Super drag: the dot is drawn held, as for a
    /// handle drag.
    pub(crate) fn u_held(&self) -> bool {
        (self.unified().click.is_some() && self.mode == Mode::Desktop) || self.super_drag().is_some()
    }

    /// `_look()`: a Super drag shows what it does, wherever the dot is; otherwise the zone contract (free_pointer._look).
    pub(crate) fn u_look(&self) -> Option<&'static str> {
        match self.super_drag() {
            Some("move") => Some("move"),
            Some("resize") => Some("resize_nwse"),
            _ => self.look_base(),
        }
    }

    // --- Super drags --------------------------------------------------------------------------------------------

    /// `on_super(op)`: view.SUPER_OPS: Hyprland began a window move or resize under the cursor, or a button came up
    /// (module docstring: Super drags). Only the run's own idea of the drag changes here: the drag itself is Hyprland's.
    pub fn on_super(&mut self, op: &str) -> Result<Option<Vec<u8>>> {
        if !SUPER_OPS.contains(&op) {
            return Err(Invalid::new("unsupported Super drag operation"));
        }
        let now = self.now();
        self.last_move = now;
        self.unified_mut().arm = None;
        // Hyprland's drag wins over an overview pan
        let pan_end = if op != "super_release" { self.span_end() } else { Vec::new() };
        if !pan_end.is_empty() {
            let out = self.on_super(op)?;
            let mut all = pan_end;
            all.extend(out.unwrap_or_default());
            return Ok(Some(all));
        }
        if op == "super_release" {
            self.land();
            return Ok(some(self.super_end()));
        }
        let u = self.unified_mut();
        u.super_drag = Some(if op == "super_left_press" { "move" } else { "resize" });
        u.click = None;
        u.super_crossed = false;
        if self.super_drag() == Some("move") {
            self.identify();
        }
        if self.mode == Mode::Off && self.overview() {
            // typing mode: the last cursor seen is old, wait for the next sample
            (self.abs, self.fresh) = (None, false);
        }
        // the drag takes the window, keys and all
        let mut out =
            if self.active() && self.control.state().menu_open { self.control.pill_close() } else { Vec::new() };
        if self.active() && self.mode == Mode::Free {
            // the hand beat the helper to the edge: the grab must not stay on the drag
            let home = self.home();
            if let (Some(o), Some(cursor)) = (self.output(Some(home)), self.abs) {
                if self.super_drag() == Some("move") {
                    out.extend(self.start_carry(home, &o, cursor, now));
                } else {
                    (self.mode, self.desktop, self.hover) = (Mode::Desktop, Some(home), None);
                    self.set_want(Mode::Desktop, Some(inside(&o, point(cursor), fp::INSET_PX)), true);
                }
            }
        }
        if self.active() {
            out.extend(self.frames(false));
        }
        Ok(some(out))
    }

    /// `_identify()`: a Super move's press: ask (off the loop) which window is under the cursor, the one Hyprland just
    /// began dragging. An answer that comes after the next press belongs to no drag.
    fn identify(&mut self) {
        self.inbox.lock().unwrap_or_else(|e| e.into_inner()).super_window = None;
        if !self.control.has_sender() {
            return;
        }
        self.token += 1;
        let token = self.token;
        self.inbox.lock().unwrap_or_else(|e| e.into_inner()).super_token = token;
        let inbox = Arc::clone(&self.inbox);
        let found: Found = Box::new(move |window| {
            // the sender's worker thread
            let mut i = inbox.lock().unwrap_or_else(|e| e.into_inner());
            if i.super_token == token {
                i.super_window = Some(window);
            }
        });
        self.control.sender_under_cursor(found);
    }

    /// `_land()`: a Super move's release after crossing desktops, with the cursor on one: the window lands on that
    /// monitor even if its middle has not crossed (send.WindowSender.land_now, off the loop). Dropped between desktops
    /// it stays.
    fn land(&mut self) {
        let window = self.inbox.lock().unwrap_or_else(|e| e.into_inner()).super_window.take();
        let crossed = std::mem::replace(&mut self.unified_mut().super_crossed, false);
        let home = self.home();
        if self.super_drag() != Some("move")
            || !crossed
            || self.mode != Mode::Desktop
            || !self.control.has_sender()
            || home >= self.control.state().names.len()
        {
            return;
        }
        let Some(window) = window.filter(|w| w.as_object().is_some()) else { return };
        let pinned = window.get("pinned").is_some_and(super::overview_pointer::truthy);
        if let (Some(Json::Str(address)), false) = (window.get("address"), pinned) {
            self.unified_mut().landings += 1;
            let monitor = self.control.state().names[home].clone();
            self.control.sender_land(address, &monitor);
        }
    }

    /// `_super_end()`: the Super drag's button came up (or its release was lost: _desktop_button, the idle close).
    /// Dropped between desktops, the dot is free there again; Hyprland left the window where the cursor waited. In
    /// typing mode the pointer was only on for the drag: it goes off, the dot with it.
    fn super_end(&mut self) -> Vec<u8> {
        self.unified_mut().super_drag = None;
        if !self.active() {
            if self.mode == Mode::Off {
                return Vec::new();
            }
            (self.mode, self.hover, self.pointer) = (Mode::Off, None, None);
            self.set_want(Mode::Off, None, false);
            return self.frames(false);
        }
        if self.mode != Mode::Carry {
            return self.frames(false);
        }
        let Some(o) = self.output(self.desktop) else {
            (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
            self.set_want(Mode::Away, None, false);
            return Vec::new();
        };
        self.mode = Mode::Free;
        let park = self.park(&o, None);
        self.set_want(Mode::Free, Some(park), false);
        self.hover = self.hit();
        self.frames(false)
    }

    /// `_start_carry(i, o, cursor, now)`: a Super drag reached desktop i's edge: the dot goes on through space from just
    /// past it, as ever (free_pointer._exit), but nothing is grabbed, so the button stays Hyprland's and the drag goes
    /// on. The cursor, and the window, wait just inside the edge.
    fn start_carry(&mut self, i: usize, o: &OutputRect, cursor: (i64, i64), now: f64) -> Vec<u8> {
        let Some(start) = self.exit(i, (cursor.0 - o.x) as f64, (cursor.1 - o.y) as f64) else { return self.wants() };
        self.gain = self.gain_of(i);
        self.pointer = Some(self.clamp(start));
        (self.mode, self.desktop, self.hover, self.last_move) = (Mode::Carry, Some(i), None, now);
        self.exits += 1;
        let u = self.unified_mut();
        u.carries += 1;
        u.super_crossed = true;
        self.set_want(Mode::Carry, Some(inside(o, point(cursor), CARRY_INSET_PX)), true);
        Vec::new()
    }

    /// `_carry(dx, dy, now)`: carry mode: the dot moves through space. Handles do nothing here. Onto any desktop's
    /// content the cursor warps to the matching pixel (a warp, no focus call, which would take the window from under the
    /// drag): Hyprland's drag follows the cursor there, by the same offset, and the window goes to that monitor's
    /// workspace with its middle. That desktop is home now: a plain view frame, so the highlight moves with no
    /// animation.
    pub(crate) fn u_carry(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        let Some(p) = self.pointer else { return Vec::new() };
        self.pointer = Some(self.clamp([p[0] + dx, p[1] + dy]));
        self.last_move = now;
        let Some(i) = fp::desktop_at(self.geometry.as_ref(), &self.rolled(), self.pointer) else { return Vec::new() };
        let (Some(g), Some(tile), Some(placement), Some(pointer)) =
            (self.geometry.as_ref(), self.tile(i), self.placement(i), self.pointer)
        else {
            return Vec::new();
        };
        let q = fp::unproject(g, &tile, &placement, pointer);
        let target = q.filter(|q| fp::inside_by(&tile, *q) >= fp::ENTER_PX).and_then(|q| self.to_layout(i, q));
        let Some(target) = target else { return Vec::new() };
        (self.mode, self.desktop) = (Mode::Desktop, Some(i));
        self.entries += 1;
        self.set_want(Mode::Desktop, Some(target), true);
        self.select(i)
    }

    /// `_park(o, local)`: free mode holds the hidden cursor just inside home where it left (input_lock.park_point), not
    /// in the middle: with focus-follows-mouse the window it was over keeps the focus.
    pub(crate) fn u_park(&self, o: &OutputRect, local: Option<(f64, f64)>) -> (i64, i64) {
        let cursor = match local {
            Some((lx, ly)) => Some((o.x as f64 + lx, o.y as f64 + ly)),
            None => self.abs.map(point),
        };
        park_point(o, cursor)
    }

    /// `_follow(now)`: free mode: a parked cursor more than JUMP_PX away was moved by something other than the hand. On
    /// home: the real cursor is the pointer there again. Elsewhere on the XR monitors: parked back on home. Off them:
    /// let go.
    fn follow(&mut self, _now: f64) -> Vec<u8> {
        let (kind, park) = (self.want.kind, self.want.point);
        let (cursor, applied) = (self.abs, self.applied);
        let (Mode::Free, Some(park), Some(cursor), false) = (kind, park, cursor, applied < self.sync) else {
            return Vec::new();
        };
        if py_hypot((cursor.0 - park.0) as f64, (cursor.1 - park.1) as f64) <= fp::JUMP_PX {
            return Vec::new();
        }
        self.unified_mut().follows += 1;
        let at = self.at(cursor);
        if at.is_some_and(|a| a.0 != self.home()) {
            self.unified_mut().pullbacks += 1;
            self.set_want(Mode::Free, Some(park), true); // the grab warps it back to its parking point
            return Vec::new();
        }
        let out = self.end_drag();
        self.hover = None;
        let Some((i, lx, ly)) = at else {
            (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
            self.set_want(Mode::Away, None, false);
            return out;
        };
        self.mode = Mode::Desktop;
        self.pointer = self.from_logical(i, lx, ly);
        self.set_want(Mode::Desktop, None, false); // the grab goes, the click binds come back; the cursor stays where it is
        out
    }

    // --- the left button ----------------------------------------------------------------------------------------

    pub(crate) fn u_on_op(&mut self, op: &str) -> Result<Option<Vec<u8>>> {
        if !RESIZE_OPS.contains(&op) {
            return Err(Invalid::new("unsupported resize operation"));
        }
        self.unified_mut().arm = None; // a press is no push: on the edge it left it takes that resize handle as ever
        if self.unified().span.is_some() {
            // a press mid-pan only ends it, on home
            self.last_move = self.now();
            if op != "resize_press" {
                return Ok(None);
            }
            let mut out = self.span_end();
            out.extend(self.frames(false));
            return Ok(Some(out));
        }
        // A plain press means no button is held: a Super drag's release was lost. A plain release mid drag (Super let go
        // first) is nothing: its own release bind says when it ends.
        let mut stale =
            if op == "resize_press" && self.super_drag().is_some() { self.super_end() } else { Vec::new() };
        if self.mode == Mode::Carry {
            return Ok(some(stale));
        }
        if self.mode == Mode::Desktop || (op == "resize_release" && self.unified().click.is_some()) {
            stale.extend(self.desktop_button(op).unwrap_or_default());
            return Ok(some(stale));
        }
        self.unified_mut().click = None;
        stale.extend(self.on_op_base(op)?.unwrap_or_default());
        Ok(some(stale))
    }

    /// `_desktop_button(op)`: over home, the non-consuming left button: the window already has it. The press brings an
    /// open placement panel here with its modal keys off the keyboard (view.ViewControl.desktop_press: they would take
    /// the typing meant for the window) and is noted for the held-button guard (and, with window_drag on, a hold that
    /// lifts the window: _hold_home); a click is the window's alone and never maximizes home
    /// (anchor-no-click-maximize).
    fn desktop_button(&mut self, op: &str) -> Option<Vec<u8>> {
        let now = self.now();
        let idle = now - self.last_move >= OVERLAY_IDLE_CLOSE_S;
        self.last_move = now; // input: the dot and pills come back
        let live = self.active() && self.mode == Mode::Desktop && self.pointer.is_some();
        let mut out = Vec::new();
        if op == "resize_press" {
            let click = (live && !idle).then_some(Click { start: self.pointer, at: now, moved: false });
            self.unified_mut().click = click;
            let keys = self.control.state().menu_open
                || (self.control.has_options() && self.control.options_keyboard_owned());
            if live && keys {
                self.unified_mut().desktop_presses += 1;
                out.extend(self.control.desktop_press());
            }
        } else {
            let u = self.unified_mut();
            u.click = None;
            u.window_clicks += 1;
        }
        out.extend(self.handle());
        out.extend(self.pills_frame(false));
        out.extend(self.pointer_frame(false));
        some(out)
    }

    /// `_hold_home(now)`: over home with the button down, window_drag on: moving more than CLICK_PX first is the
    /// window's own drag (never lifts); held HOLD_S still, the window under the cursor lifts (_lift_home). Off: nothing,
    /// ever.
    fn hold_home(&mut self, now: f64) -> Vec<u8> {
        let Some(c) = self.unified().click else { return Vec::new() };
        let (true, false, Some(pointer), Some(start)) = (self.window_drag, c.moved, self.pointer, c.start) else {
            return Vec::new();
        };
        if py_hypot(pointer[0] - start[0], pointer[1] - start[1]) > fp::CLICK_PX {
            if let Some(c) = &mut self.unified_mut().click {
                c.moved = true;
            }
            return Vec::new();
        }
        if now - c.at < fp::HOLD_S {
            return Vec::new();
        }
        self.lift_home(c, now)
    }

    /// `_lift_home(c, now)`: the held press on home becomes a window drag, and the dot turns free where it is (the grab
    /// hides and parks the cursor and takes the left button's release, which ends the drag in OverviewPointer.on_op).
    fn lift_home(&mut self, c: Click, now: f64) -> Vec<u8> {
        let home = self.home();
        let (o, tile, p) = (self.output(Some(home)), self.tile(home), self.placement(home));
        let (Some(o), Some(tile), Some(p), Some(cursor), Some(pointer), Some(g)) =
            (o, tile, p, self.abs, self.pointer, self.geometry.as_ref())
        else {
            return Vec::new();
        };
        let q = fp::unproject(g, &tile, &p, pointer).unwrap_or([tile[0] + tile[2] / 2.0, tile[1] + tile[3] / 2.0]);
        self.unified_mut().click = None;
        let mut d = window_press(home, q, pointer, Some(cursor), c.at);
        self.lift(&mut d);
        if let Drag::Window { target, .. } = &mut d {
            *target = fp::desktop_at(self.geometry.as_ref(), &self.rolled(), self.pointer);
        }
        self.drag = Some(d);
        self.gain = self.gain_of(home);
        (self.mode, self.desktop, self.hover, self.last_move) = (Mode::Free, Some(home), None, now);
        self.exits += 1;
        let park = self.park(&o, Some(((cursor.0 - o.x) as f64, (cursor.1 - o.y) as f64)));
        self.set_want(Mode::Free, Some(park), false);
        self.frames(false)
    }
}

/// The unified pointer's own trace methods (on_super, request_switch, neighbour, pan_plan, set_pan, can_switch).
#[cfg(test)]
pub(crate) fn replay_call<C: Control>(
    p: &mut UnifiedPointer<C>,
    m: &str,
    a: &[Json],
    k: &Json,
) -> Option<std::result::Result<Json, String>> {
    let arg = |n: usize| a.get(n).unwrap_or(&Json::Null);
    let int = |j: &Json| match *j {
        Json::Int(v) => v,
        _ => panic!("not an int: {j:?}"),
    };
    let num = |j: &Json| match *j {
        Json::Int(v) => v as f64,
        Json::Float(v) => v,
        _ => panic!("not a number: {j:?}"),
    };
    let frames = |r: Option<Vec<u8>>| match r {
        Some(b) => Json::Object(vec![("$b".into(), Json::Str(String::from_utf8(b).expect("UTF-8 frames")))]),
        None => Json::Null,
    };
    Some(Ok(match m {
        "on_super" => match p.on_super(arg(0).as_str().unwrap_or("")) {
            Ok(r) => frames(r),
            Err(_) => return Some(Err("ValueError".into())),
        },
        "request_switch" => {
            let panned = a.get(2).or(k.get("panned")).is_some_and(super::overview_pointer::truthy);
            p.request_switch(int(arg(0)) as usize, int(arg(1)) as usize, panned);
            Json::Null
        }
        "neighbour" => {
            let along = a.get(1).or(k.get("along")).map_or(0.5, num);
            let edge = Direction::parse(arg(0).as_str().unwrap_or("")).expect("an edge");
            p.neighbour(edge, along).map_or(Json::Null, |i| Json::Int(i as i64))
        }
        "pan_plan" => {
            let o = arg(3);
            let output = OutputRect {
                index: int(o.get("index").unwrap()) as usize,
                x: int(o.get("x").unwrap()),
                y: int(o.get("y").unwrap()),
                width: num(o.get("width").unwrap()),
                height: num(o.get("height").unwrap()),
            };
            let edge = Direction::parse(arg(1).as_str().unwrap_or("")).expect("an edge");
            match p.pan_plan(int(arg(0)) as usize, edge, num(arg(2)), &output) {
                None => Json::Null,
                Some(plan) => Json::Str(format!("{plan:?}")), // a Plan is no JSON: the corpus records none
            }
        }
        "set_pan" => {
            let f = arg(0);
            let payload = (*f != Json::Null).then(|| {
                let i = |n: &str| int(f.get(n).unwrap());
                PanFrame {
                    active: f.get("active") == Some(&Json::Bool(true)),
                    from: i("from") as usize,
                    to: i("to") as usize,
                    side: Direction::parse(f.get("side").and_then(Json::as_str).unwrap()).unwrap(),
                    exit: i("exit"),
                    entry: i("entry"),
                    peek: i("peek"),
                    cross: i("cross"),
                    turn: i("turn"),
                    way: i("way"),
                    zoom: i("zoom"),
                }
            });
            p.set_pan(payload);
            Json::Null
        }
        "can_switch" => Json::Bool(p.can_switch()),
        "can_scroll" => Json::Bool(p.can_scroll()),
        _ => return None,
    }))
}
