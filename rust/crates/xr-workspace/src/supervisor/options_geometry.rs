//! The options glass's coordinates, shared with the native display renderer: a port of
//! `tools/native_spike/options_geometry.py`. Panel px from the panel's top-left; pure maths.
//!
//! The Python's numbers are ints here (`W = 600`), and its answers keep the types its inputs give (a rect echoes the
//! pill's `y`). The Rust gives f64 rects; `pointer_math_corpus_tests` rebuilds the Python's types from the inputs.

use super::bump::Num;
use super::orbit::Tile;
use super::python::{py_floordiv, py_round};

/// `W, H`: the panel's size.
pub const W: i64 = 600;
pub const H: i64 = 440;
/// `HEADER_H, TABS_TOP, TABS_H`: the header strip, then the tab row.
pub const HEADER_H: i64 = 44;
pub const TABS_TOP: i64 = 44;
pub const TABS_H: i64 = 36;
/// `BODY_TOP, BODY_BOTTOM, ROW_H, PAD, FOOTER_TOP`: the scrolling rows and the footer.
pub const BODY_TOP: i64 = 88;
pub const BODY_BOTTOM: i64 = 392;
pub const ROW_H: i64 = 38;
pub const PAD: i64 = 16;
pub const FOOTER_TOP: i64 = 400;
/// `GEAR_W, GEAR_H, GEAR_GAP, HIT_X, HIT_Y`: the gear beside a pill, and the padding that still hits it.
pub const GEAR_W: i64 = 28;
pub const GEAR_H: i64 = 14;
pub const GEAR_GAP: i64 = 12;
pub const HIT_X: i64 = 4;
pub const HIT_Y: i64 = 4;
/// `TRACK_X0, TRACK_X1`: a range row's track.
pub const TRACK_X0: i64 = 300;
pub const TRACK_X1: i64 = 450;
/// `SCOPE_X0, NEXT_X0, CLOSE_X0, CLOSE_X1`: the header controls' x ranges (spike options_geometry.hpp).
pub const SCOPE_X0: i64 = 346;
pub const NEXT_X0: i64 = 440;
pub const CLOSE_X0: i64 = 530;
pub const CLOSE_X1: i64 = 594;
/// `TABS_X, TAB_W, TAB_PITCH, TABS_PER_PAGE`: the tab cells.
pub const TABS_X: i64 = 16;
pub const TAB_W: i64 = 136;
pub const TAB_PITCH: i64 = 142;
pub const TABS_PER_PAGE: i64 = 4;

/// A range row's bounds and step (`row['minimum']`, `row['maximum']`, `row['step']`), ints or floats as the
/// Python's row has them.
#[derive(Clone, Copy, Debug)]
pub struct RangeRow {
    pub minimum: Num,
    pub maximum: Num,
    pub step: Num,
}

fn add(a: Num, b: Num) -> Num {
    match (a, b) {
        (Num::Int(a), Num::Int(b)) => Num::Int(a + b),
        _ => Num::Float(a.get() + b.get()),
    }
}

fn sub(a: Num, b: Num) -> Num {
    match (a, b) {
        (Num::Int(a), Num::Int(b)) => Num::Int(a - b),
        _ => Num::Float(a.get() - b.get()),
    }
}

fn mul(a: Num, b: Num) -> Num {
    match (a, b) {
        (Num::Int(a), Num::Int(b)) => Num::Int(a * b),
        _ => Num::Float(a.get() * b.get()),
    }
}

/// Python's `max(a, b)` on numbers: `a` unless `b > a`.
fn num_max(a: Num, b: Num) -> Num {
    if b.get() > a.get() { b } else { a }
}

/// Python's `min(a, b)` on numbers: `a` unless `b < a`.
fn num_min(a: Num, b: Num) -> Num {
    if b.get() < a.get() { b } else { a }
}

/// `range_value(row, x)`: the value a press at panel x sets on a range row, snapped to its step (`round`, so ties to
/// even) and kept within its bounds. An int where the Python's is one (a clamped end, a stepped value).
pub fn range_value(row: &RangeRow, x: Num) -> Num {
    let t = (x.get() - TRACK_X0 as f64) / (TRACK_X1 - TRACK_X0) as f64;
    let t = num_min(num_max(Num::Float(t), Num::Int(0)), Num::Int(1));
    let mut value = add(row.minimum, mul(t, sub(row.maximum, row.minimum)));
    if row.step.get() > 0.0 {
        let steps = py_round(sub(value, row.minimum).get() / row.step.get());
        value = add(row.minimum, mul(Num::Int(steps), row.step));
    }
    num_min(num_max(value, row.minimum), row.maximum)
}

/// `panel_rect(tile, pill)`: the options panel, centred under the desktop, its top at the pill's.
pub fn panel_rect(tile: &Tile, pill: &[f64; 4]) -> [f64; 4] {
    [tile[0] + (tile[2] - W as f64) / 2.0, pill[1], W as f64, H as f64]
}

/// `gear_rect(pill)`: the gear, GEAR_GAP right of the pill.
pub fn gear_rect(pill: &[f64; 4]) -> [f64; 4] {
    [pill[0] + pill[2] + GEAR_GAP as f64, pill[1], GEAR_W as f64, GEAR_H as f64]
}

/// `inside(rect, point)`: `point` (None: missed) in `rect`, its far edges out.
pub fn inside(rect: &[f64; 4], point: Option<[f64; 2]>) -> bool {
    point.is_some_and(|p| rect[0] <= p[0] && p[0] < rect[0] + rect[2] && rect[1] <= p[1] && p[1] < rect[1] + rect[3])
}

/// `gear_hit(pill, point)`: on the gear padded by HIT_X / HIT_Y.
pub fn gear_hit(pill: &[f64; 4], point: Option<[f64; 2]>) -> bool {
    let [x, y, w, h] = gear_rect(pill);
    let (hx, hy) = (HIT_X as f64, HIT_Y as f64);
    inside(&[x - hx, y - hy, w + 2.0 * hx, h + 2.0 * hy], point)
}

/// `header_at(x, y)`: 'scope', 'next', 'close' or None at panel point (x, y).
pub fn header_at(x: f64, y: f64) -> Option<&'static str> {
    if !(0.0 <= y && y < HEADER_H as f64) {
        return None;
    }
    let within = |lo: i64, hi: i64| lo as f64 <= x && x < hi as f64;
    if within(SCOPE_X0, NEXT_X0) {
        Some("scope")
    } else if within(NEXT_X0, CLOSE_X0) {
        Some("next")
    } else if within(CLOSE_X0, CLOSE_X1) {
        Some("close")
    } else {
        None
    }
}

/// `tab_at(x, y, category, count)`: the tab index drawn at panel point (x, y) with tab `category` open, or None (the
/// gaps, and past the last tab).
pub fn tab_at(x: f64, y: f64, category: i64, count: i64) -> Option<i64> {
    if !(TABS_TOP as f64 <= y && y < (TABS_TOP + TABS_H) as f64) || x < TABS_X as f64 {
        return None;
    }
    let cell = py_floordiv(x - TABS_X as f64, TAB_PITCH as f64) as i64;
    let index = category.div_euclid(TABS_PER_PAGE) * TABS_PER_PAGE + cell;
    let on = cell < TABS_PER_PAGE && x - TABS_X as f64 - ((cell * TAB_PITCH) as f64) < TAB_W as f64 && index < count;
    on.then_some(index)
}

/// `row_at(x, y, scroll, count)`: the body row at panel point (x, y) scrolled by `scroll`, or None.
pub fn row_at(x: f64, y: f64, scroll: f64, count: i64) -> Option<i64> {
    if !(PAD as f64 <= x && x < (W - PAD) as f64) || !(BODY_TOP as f64 <= y && y < BODY_BOTTOM as f64) {
        return None;
    }
    let index = py_floordiv(y - BODY_TOP as f64 + scroll, ROW_H as f64) as i64;
    (0 <= index && index < count).then_some(index)
}

/// `row_rect(index, scroll)`: body row `index`'s rect, scrolled by `scroll`.
pub fn row_rect(index: i64, scroll: f64) -> [f64; 4] {
    [PAD as f64, (BODY_TOP + index * ROW_H) as f64 - scroll, (W - 2 * PAD) as f64, ROW_H as f64]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_header_tabs_and_rows_are_where_the_python_puts_them() {
        assert_eq!(header_at(400.0, 10.0), Some("scope"));
        assert_eq!(header_at(500.0, 10.0), Some("next"));
        assert_eq!(header_at(593.9, 43.9), Some("close"));
        assert_eq!(header_at(594.0, 10.0), None);
        assert_eq!(header_at(400.0, 44.0), None);
        assert_eq!(tab_at(16.0, 44.0, 0, 6), Some(0));
        assert_eq!(tab_at(16.0 + 136.0, 50.0, 0, 6), None); // the gap
        assert_eq!(tab_at(16.0 + 142.0, 50.0, 5, 6), Some(5));
        assert_eq!(tab_at(16.0 + 2.0 * 142.0, 50.0, 5, 6), None); // past the last
        assert_eq!(tab_at(15.9, 50.0, 0, 6), None);
        assert_eq!(row_at(20.0, 88.0, 38.0, 64), Some(1));
        assert_eq!(row_at(20.0, 88.0, -1.0, 64), None);
        assert_eq!(row_rect(2, 10.0), [16.0, 154.0, 568.0, 38.0]);
        assert_eq!(panel_rect(&[0.0, 0.0, 480.0, 270.0], &[1.0, 290.0, 3.0, 14.0]), [-60.0, 290.0, 600.0, 440.0]);
    }

    #[test]
    fn a_range_value_keeps_the_pythons_types() {
        let row = RangeRow { minimum: Num::Int(-180), maximum: Num::Int(180), step: Num::Int(1) };
        assert!(matches!(range_value(&row, Num::Int(375)), Num::Int(0)));
        assert!(matches!(range_value(&row, Num::Int(0)), Num::Int(-180)));
        let row = RangeRow { minimum: Num::Float(0.5), maximum: Num::Int(2), step: Num::Int(0) };
        assert!(matches!(range_value(&row, Num::Int(1000)), Num::Float(v) if v == 2.0));
        assert!(matches!(range_value(&row, Num::Float(375.0)), Num::Float(v) if v == 1.25));
    }
}
