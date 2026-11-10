//! The options panel's geometry dump: byte for byte the output (and exit codes) of spike/tests/options_geometry_test.cpp.
//!
//! - `--layout`: for each {"canvas":[w,h,hw,hh],"tile":[x,y,w,h],"placement":[x,y,d,c,r]} (radians) on stdin, the rects
//!   as drawn and where glass and tile points land in the world, for
//!   tests/xr_options_integration/test_options_live_routes.py.
//! - `--band`: for each {"monitor","source","canvas","placement"} on stdin, the production maximize_geometry line of that
//!   desktop maximized.
//! - otherwise: the geometry checks (exit 1..4 on a failure), then the constants as one JSON line. The C++ also checks
//!   render_options_panel's and render_handle_atlas's pixels (exits 6 and 5); those rasterisers are not ported, so those
//!   checks are left out here.

use std::io::Read as _;
use std::process::ExitCode;

use serde_json::Value;
use xr_renderer::cfmt;
use xr_renderer::glass::{glass_pill, level_bar_rect, level_bar_scale};
use xr_renderer::options_geometry::*;
use xr_renderer::pose::{Quat, aim_rotation, view_from};
use xr_renderer::view::{
    Canvas, FRect, Placement, Size, Vec3, blended_layout, focus_direction, glass_to_desktop, overview_layout,
    place_point, zoomed_placements,
};

/// nlohmann's conversion of a JSON value to float: an integer (in i64 / u64 range) is cast straight to float, anything
/// else read as a double first; true and false are 1 and 0.
fn num(v: &Value) -> Result<f32, String> {
    match v {
        Value::Number(n) => {
            if let Some(u) = n.as_u64() {
                Ok(u as f32)
            } else if let Some(i) = n.as_i64() {
                Ok(i as f32)
            } else {
                n.as_f64().map(|d| d as f32).ok_or_else(|| format!("bad number {n}"))
            }
        }
        Value::Bool(b) => Ok(if *b { 1.0 } else { 0.0 }),
        other => Err(format!("type must be number, but is {other}")),
    }
}

/// nlohmann reads the integer `-0` as the integer 0 (so +0), serde_json as the double -0.0: rewrite that token,
/// outside strings, to `0` before parsing. `-0.0` and `-0e1` stay as they are.
fn integer_minus_zero_to_zero(text: &str) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let (mut in_string, mut escaped, mut i) = (false, false, 0);
    while i < b.len() {
        let c = b[i];
        if in_string {
            if escaped {
                escaped = false;
            } else if c == b'\\' {
                escaped = true;
            } else if c == b'"' {
                in_string = false;
            }
        } else if c == b'"' {
            in_string = true;
        } else if c == b'-' && b.get(i + 1) == Some(&b'0') && !matches!(b.get(i + 2), Some(b'.' | b'e' | b'E' | b'0'..=b'9')) {
            out.push('0');
            i += 2;
            continue;
        }
        // Copy the whole UTF-8 character: only ASCII bytes are ever inspected above.
        let len = text[i..].chars().next().map_or(1, char::len_utf8);
        out.push_str(&text[i..i + len]);
        i += len;
    }
    out
}

fn at<'a>(e: &'a Value, key: &str, i: usize) -> Result<&'a Value, String> {
    e.get(key).and_then(|a| a.get(i)).ok_or_else(|| format!("missing {key}[{i}]"))
}

fn floats<const N: usize>(e: &Value, key: &str) -> Result<[f32; N], String> {
    let mut out = [0.0; N];
    for (i, o) in out.iter_mut().enumerate() {
        *o = num(at(e, key, i)?)?;
    }
    Ok(out)
}

fn read_input() -> Result<Value, String> {
    let mut text = String::new();
    std::io::stdin().read_to_string(&mut text).map_err(|e| e.to_string())?;
    let all: Value = serde_json::from_str(&integer_minus_zero_to_zero(&text)).map_err(|e| e.to_string())?;
    if all.is_array() { Ok(all) } else { Err("input must be a JSON array".into()) }
}

fn entry(e: &Value) -> Result<(Canvas, Placement), String> {
    let [cw, ch, hw, hh] = floats::<4>(e, "canvas")?;
    let [px, py, pd, pc, pr] = floats::<5>(e, "placement")?;
    Ok((Canvas::new(cw, ch, hw, hh), Placement::new(px, py, pd, pc, pr)))
}

fn layout() -> Result<(), String> {
    let all = read_input()?;
    let mut out = String::from("[");
    for (i, e) in all.as_array().into_iter().flatten().enumerate() {
        let (c, on) = entry(e)?;
        let [tx, ty, tw, th] = floats::<4>(e, "tile")?;
        let t = FRect::new(tx, ty, tw, th);
        let rect = |out: &mut String, r: FRect| json_floats(out, &[r.x, r.y, r.width, r.height]);
        let world = |out: &mut String, x: f32, y: f32| {
            let v = place_point(&c, &t, &on, x, y);
            json_floats(out, &[v.x, v.y, v.z]);
        };
        let glass = |out: &mut String, x: f32, y: f32| {
            let q = glass_to_desktop(&t, &on, x, y);
            world(out, q[0], q[1]);
        };
        let panel = options_panel(&t);
        let gear = options_gear(&t);
        let mx = options_maximize(&t);
        if i > 0 {
            out.push(',');
        }
        // nlohmann's objects are std::maps: the keys come out sorted.
        out.push_str("{\"gear\":");
        rect(&mut out, gear);
        out.push_str(",\"gear_hit\":");
        rect(&mut out, options_gear_hit(&t));
        out.push_str(",\"gear_mid\":");
        glass(&mut out, gear.x + 14.0, gear.y + 7.0);
        out.push_str(",\"maximize\":");
        rect(&mut out, mx);
        out.push_str(",\"maximize_hit\":");
        rect(&mut out, options_maximize_hit(&t));
        out.push_str(",\"maximize_mid\":");
        world(&mut out, mx.x + mx.width / 2.0, mx.y + mx.height / 2.0);
        out.push_str(",\"panel\":");
        rect(&mut out, panel);
        out.push_str(",\"panel_mid\":");
        glass(&mut out, panel.x + 300.0, panel.y + 220.0);
        out.push_str(",\"pill\":");
        rect(&mut out, glass_pill(&t));
        out.push('}');
    }
    out.push(']');
    println!("{out}");
    Ok(())
}

fn band() -> Result<(), String> {
    let all = read_input()?;
    for e in all.as_array().into_iter().flatten() {
        let (c, placement) = entry(e)?;
        let [sw, sh] = floats::<2>(e, "source")?;
        let monitor = e.get("monitor").and_then(Value::as_str).ok_or("monitor must be a string")?;
        let sources = [Size::new(sw, sh)];
        let own = [placement];
        let overview = overview_layout(&sources, c.view_w, c.view_h, 20.0);
        let tiles = blended_layout(&sources, c.view_w, c.view_h, 20.0, 1.0, 0.0);
        let placed = zoomed_placements(&c, &overview, &tiles, &own, 1.0, 0.0)[0];
        let aim = focus_direction(&c, &overview, &own, 0);
        let view = view_from(Quat::default(), aim_rotation(aim[0], aim[1]));
        println!("{}", maximize_geometry_json(monitor, &c, &tiles[0], &placed, &view));
    }
    Ok(())
}

/// The C++ test's checks but the raster ones: Some(its exit code) on the first failure.
fn checks() -> Option<u8> {
    for w in [80.0f32, 240.0, 640.0, 1800.0] {
        for x in [-800.0f32, 0.0, 1200.0] {
            let t = FRect::new(x, 100.0, w, 360.0);
            let p = glass_pill(&t);
            let g = options_gear(&t);
            let m = options_maximize(&t);
            let b = level_bar_rect(&t);
            let s = level_bar_scale(&t);
            if g.x - (p.x + p.width) != 12.0
                || g.y != p.y
                || m.width != 26.0 * s
                || f64::from((b.x - (m.x + m.width) - 12.0 * s).abs()) > 0.001
            {
                return Some(1);
            }
            let panel = options_panel(&t);
            if panel.width != 600.0 || panel.height != 440.0 || panel.y != p.y {
                return Some(2);
            }
        }
    }
    for curve in [0.0f32, 1.0, 2.094] {
        for roll in [-0.5f32, 0.0, 0.5] {
            for distance in [0.1f32, 1.0, 2.0] {
                let c = Canvas::new(1920.0, 1080.0, 1.0, 0.5625);
                let t = FRect::new(300.0, 200.0, 640.0, 360.0);
                let on = Placement::new(0.1, 0.2, distance, curve, roll);
                let r = options_maximize(&t);
                let q = place_point(&c, &t, &on, r.x + r.width / 2.0, r.y + r.height / 2.0);
                let len = (q.x * q.x + q.y * q.y + q.z * q.z).sqrt();
                let ray = Vec3::new(q.x / len, q.y / len, q.z / len);
                if options_maximize_ray(&c, &t, &on, &ray).is_none() {
                    let g = |v: f32| cfmt::g(f64::from(v), 6);
                    eprintln!("{} {} {}", g(curve), g(roll), g(distance));
                    return Some(4);
                }
            }
        }
    }
    if options_row_at(20.0, 88.0, 38, 10) != 1 || options_row_at(20.0, 392.0, 0, 64) != -1 {
        return Some(3);
    }
    // Drawn cells and hit regions agree; the next page's tabs are not under the page you see.
    if options_tab_at(16.0, 62.0, 0, 14) != 0
        || options_tab_at(151.9, 62.0, 0, 14) != 0
        || options_tab_at(153.0, 62.0, 0, 14) != -1
        || options_tab_at(158.0, 62.0, 0, 14) != 1
    {
        return Some(4);
    }
    if options_tab_at(583.0, 62.0, 0, 14) != -1
        || options_tab_at(590.0, 62.0, 0, 14) != -1
        || options_tab_at(16.0 + 142.0 * 2.0, 62.0, 12, 14) != -1
    {
        return Some(4);
    }
    if options_header_at(346.0, 20.0) != OptionsHeader::Scope
        || options_header_at(440.0, 20.0) != OptionsHeader::Next
        || options_header_at(593.0, 20.0) != OptionsHeader::Close
        || options_header_at(594.0, 20.0) != OptionsHeader::None
        || options_header_at(300.0, 20.0) != OptionsHeader::None
    {
        return Some(4);
    }
    // Not ported (raster): a choice's dropdown arrow is not printed over (render_options_panel, exit 6), and the
    // Maximize glyph is centred in its pill (render_handle_atlas, exit 5).
    None
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let run = |f: fn() -> Result<(), String>| match f() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("options_geometry_dump: {e}");
            ExitCode::FAILURE
        }
    };
    if args.len() == 2 && args[1] == "--layout" {
        return run(layout);
    }
    if args.len() == 2 && args[1] == "--band" {
        return run(band);
    }
    if let Some(code) = checks() {
        return ExitCode::from(code);
    }
    let g = |v: f32| cfmt::g(f64::from(v), 6);
    println!(
        "{{\"step_scale\":{},\"step_ms\":{},\"band_min\":{},\"band_gap\":{},\"width\":600,\"height\":440,\"header\":44,\"tabs_top\":44,\"tabs_height\":36,\"body_top\":88,\"body_bottom\":392,\"row_height\":38,\"pad\":16,\"footer_top\":400,\"gear_width\":28,\"gear_height\":14,\"gear_gap\":12,\"hit_pad\":4,\"maximize_width\":26,\"maximize_gap\":12,\"scope_x0\":346,\"next_x0\":440,\"close_x0\":530,\"close_x1\":594,\"tabs_x\":16,\"tab_w\":136,\"tab_pitch\":142,\"tabs_per_page\":4}}",
        g(RESTORE_STEP_SCALE),
        g(RESTORE_STEP_MS),
        g(RESTORE_BAND_MIN),
        g(RESTORE_BAND_GAP)
    );
    ExitCode::SUCCESS
}
