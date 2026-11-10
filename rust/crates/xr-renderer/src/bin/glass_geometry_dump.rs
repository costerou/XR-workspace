//! Prints the glass pill and level bar geometry constants and a few laid-out pills / panels / bars as one JSON line, for
//! glass_contract_test.py, which checks that tools/native_spike/free_pointer.py (the supervisor's hit testing) mirrors
//! them. Bars: their scale and rect, and probes all round and across them (the segment at each point). Byte for byte
//! the output of spike/tests/glass_geometry_dump.cpp.

use std::fmt::Write as _;

use xr_renderer::desktops::{
    MAX_DESKTOPS, RESOLUTION_COUNT, RESOLUTION_GROUP_COUNT, RESOLUTION_GROUPS, RESOLUTIONS, SCALE_PERCENTS, SCALE_STEPS,
};
use xr_renderer::glass::*;
use xr_renderer::hud::{BACKGROUND_COUNT, BACKGROUNDS, BRIGHTNESS_DEFAULT, BRIGHTNESS_MAX, BRIGHTNESS_MIN};
use xr_renderer::view::{BORDER_PX, FRect};

/// printf "[%.4f,%.4f,%.4f,%.4f]".
fn rect(r: &FRect) -> String {
    format!("[{:.4},{:.4},{:.4},{:.4}]", f64::from(r.x), f64::from(r.y), f64::from(r.width), f64::from(r.height))
}

fn sep(first: bool) -> &'static str {
    if first { "" } else { "," }
}

fn main() {
    let mut out = String::from("{\"constants\":{");
    let constants: [(&str, f32); 43] = [
        ("pill_share", GLASS_PILL_SHARE),
        ("pill_min_w", GLASS_PILL_MIN_W),
        ("pill_max_w", GLASS_PILL_MAX_W),
        ("pill_h", GLASS_PILL_H),
        ("pill_gap", GLASS_PILL_GAP),
        ("border", BORDER_PX),
        ("panel_w", PANEL_W),
        ("panel_h", PANEL_H),
        ("header_h", PANEL_HEADER_H),
        ("rows_top", PANEL_ROWS_TOP),
        ("row_h", PANEL_ROW_H),
        ("knob_lo", PANEL_KNOB_LO),
        ("knob_hi", PANEL_KNOB_HI),
        ("bar_w", LEVEL_BAR_W),
        ("bar_h", LEVEL_BAR_H),
        ("bar_gap", LEVEL_BAR_GAP),
        ("bar_pad", LEVEL_BAR_PAD),
        ("bar_share", LEVEL_BAR_SHARE),
        ("bar_min_w", LEVEL_BAR_MIN_W),
        ("edge_clear", LEVEL_EDGE_CLEAR),
        ("actions_top", PANEL_ACTIONS_TOP),
        ("action_h", PANEL_ACTION_H),
        ("action_w", PANEL_ACTION_W),
        ("action_gap", PANEL_ACTION_GAP),
        ("actions", PANEL_ACTIONS as f32),
        ("max_desktops", MAX_DESKTOPS as f32),
        ("scale_top", PANEL_SCALE_TOP),
        ("scale_item_h", SCALE_ITEM_H),
        ("scale_list_gap", SCALE_LIST_GAP),
        ("resolution_top", PANEL_RESOLUTION_TOP),
        ("resolution_item_h", RES_ITEM_H),
        ("resolution_list_gap", RES_LIST_GAP),
        ("background_top", PANEL_BACKGROUND_TOP),
        ("brightness_top", PANEL_BRIGHTNESS_TOP),
        ("background_item_h", BACKGROUND_ITEM_H),
        ("background_list_gap", BACKGROUND_LIST_GAP),
        ("brightness_min", f32::from(BRIGHTNESS_MIN)),
        ("brightness_max", f32::from(BRIGHTNESS_MAX)),
        ("brightness_default", f32::from(BRIGHTNESS_DEFAULT)),
        ("immersive_gap", IMMERSIVE_GAP),
        ("immersive_main_w", IMMERSIVE_MAIN_W),
        ("immersive_tail_w", IMMERSIVE_TAIL_W),
        ("immersive_clear", IMMERSIVE_CLEAR),
    ];
    for (i, (name, value)) in constants.iter().enumerate() {
        let _ = write!(out, "{}\"{name}\":{:.4}", sep(i == 0), f64::from(*value));
    }
    let _ = write!(
        out,
        "}},\"cap\":{{\"gap\":{:.4},\"leg\":{:.4},\"stroke\":{:.4}",
        f64::from(CAP_GAP),
        f64::from(CAP_LEG),
        f64::from(CAP_STROKE)
    );
    out += "},\"actions\":[";
    for a in 0..PANEL_ACTIONS {
        out += sep(a == 0);
        out += &rect(&glass_panel_action(a));
    }
    out += &format!("],\"scale_box\":{},\"scale_items\":[", rect(&glass_scale_box()));
    for i in 0..SCALE_STEPS {
        out += sep(i == 0);
        out += &rect(&glass_scale_item(i));
    }
    out += "],\"scale_percents\":[";
    for (i, p) in SCALE_PERCENTS.iter().enumerate() {
        let _ = write!(out, "{}{p}", sep(i == 0));
    }
    out += &format!(
        "],\"background_box\":{},\"background_switch\":{},\"background_items\":[",
        rect(&glass_background_box()),
        rect(&glass_background_switch())
    );
    for i in 0..BACKGROUND_COUNT {
        out += sep(i == 0);
        out += &rect(&glass_background_item(i));
    }
    out += "],\"backgrounds\":[";
    for (i, b) in BACKGROUNDS.iter().enumerate() {
        let _ = write!(out, "{}[\"{}\",\"{}\"]", sep(i == 0), b.name, b.label);
    }
    out += &format!(
        "],\"resolution_box\":{},\"resolution_list\":{},\"resolution_items\":[",
        rect(&glass_resolution_box()),
        rect(&glass_resolution_list())
    );
    for i in 0..RESOLUTION_COUNT {
        out += sep(i == 0);
        out += &rect(&glass_resolution_item(i));
    }
    out += "],\"resolutions\":[";
    for (i, r) in RESOLUTIONS.iter().enumerate() {
        let _ = write!(out, "{}[{},{}]", sep(i == 0), r.width, r.height);
    }
    out += "],\"resolution_groups\":[";
    for (g, group) in RESOLUTION_GROUPS.iter().enumerate().take(RESOLUTION_GROUP_COUNT) {
        let _ = write!(out, "{}[\"{}\",{}]", sep(g == 0), group.name, group.count);
    }
    out += "],\"cases\":[";
    let tiles = [
        FRect::new(100.0, 300.0, 459.0, 258.0),
        FRect::new(0.0, 0.0, 4000.0, 2000.0),
        FRect::new(10.0, 20.0, 120.0, 60.0),
        FRect::new(5.0, 5.0, 40.0, 30.0),
        FRect::new(-200.5, 410.25, 812.0, 457.0),
    ];
    for (n, t) in tiles.iter().enumerate() {
        let _ = write!(
            out,
            "{}{{\"tile\":{},\"pill\":{},\"panel\":{},\"bar\":{{\"scale\":{:.4},\"rect\":{},\"probes\":[",
            sep(n == 0),
            rect(t),
            rect(&glass_pill(t)),
            rect(&glass_panel(t)),
            f64::from(level_bar_scale(t)),
            rect(&level_bar_rect(t))
        );
        // A grid over the bar and its padding, out past them on every side, and down onto the desktop.
        let mut first_probe = true;
        let r = level_bar_rect(t);
        let s = level_bar_scale(t);
        let step_x = r.width / 23.0;
        let step_y = r.height / 7.0;
        let mut u = -3.0 * step_x;
        while u <= r.width + 3.0 * step_x + 1e-3 {
            let mut v = -3.0 * step_y;
            while v <= r.height + (LEVEL_BAR_GAP + 8.0) * s + 1e-3 {
                let px = r.x + u + 0.137 * s; // off the grid's exact edges
                let py = r.y + v - 0.291 * s;
                let _ = write!(
                    out,
                    "{}[{:.4},{:.4},{}]",
                    sep(first_probe),
                    f64::from(px),
                    f64::from(py),
                    level_bar_segment(t, px, py) as i8
                );
                first_probe = false;
                v += step_y / 2.0;
            }
            u += step_x / 2.0;
        }
        let _ = write!(
            out,
            "]}},\"button\":{{\"main\":{},\"tail\":{},\"probes\":[",
            rect(&immersive_main_rect(t)),
            rect(&immersive_tail_rect(t))
        );
        // Round the bar and past where the button sits, whether this desktop has one or not.
        first_probe = true;
        let mut u = -0.1f32;
        while u <= 1.1 {
            let mut v = -60.0 * s;
            while v <= 12.0 * s {
                let px = t.x + u * t.width;
                let py = t.y + v;
                let _ = write!(
                    out,
                    "{}[{:.4},{:.4},{}]",
                    sep(first_probe),
                    f64::from(px),
                    f64::from(py),
                    immersive_part(t, px, py) as u8
                );
                first_probe = false;
                v += 2.13 * s;
            }
            u += 0.0073;
        }
        out += "]}}";
    }
    println!("{out}]}}");
}
