//! `spike/tests/restore_band_test.cpp`, check for check: the Step-Back Band, every shape. The serialized geometry is
//! the drawn pill and the stepped picture; the band's ease; the step scale of real desktop sizes. No compositor or GPU.

use serde_json::Value;
use xr_renderer::glass::RestoreStep;
use xr_renderer::options_geometry::{
    RESTORE_BAND_GAP, RESTORE_STEP_MS, RESTORE_STEP_SCALE, json_floats, maximize_geometry_json, options_picture_box,
    options_restore_hit, options_restore_rect, options_step_scale, options_stepped,
};
use xr_renderer::pose::{Quat, aim_rotation, apply, view_from};
use xr_renderer::view::{
    Canvas, Placement, Size, blended_layout, focus_direction, overview_layout, place_point, tile_mesh,
    zoomed_placements,
};

/// `values` as the serializer prints them. serde_json's default parse may be an ULP off, so the C++ test's comparison
/// of parsed doubles is made on the text (shortest round-trip doubles: the same comparison).
fn floats(values: &[f32]) -> String {
    let mut s = String::new();
    json_floats(&mut s, values);
    s
}

#[test]
fn every_shape_serializes_the_drawn_band() {
    let mut cases = 0;
    for width in [320.0f32, 640.0, 1440.0, 2400.0] {
        for height in [240.0f32, 600.0, 1080.0] {
            for distance in [0.25f32, 1.5] {
                for curve in [0.0f32, 1.0, 2.094395] {
                    for roll in [-0.5f32, 0.0, 0.5] {
                        let case = format!("{width}x{height} d {distance} curve {curve} roll {roll}");
                        let canvas = Canvas::new(2560.0, 1440.0, 1.0, 0.5625);
                        let sources = [Size::new(width, height)];
                        let overview = overview_layout(&sources, 2560.0, 1440.0, 40.0);
                        let tiles = blended_layout(&sources, 2560.0, 1440.0, 40.0, 1.0, 0.0);
                        let originals = [Placement::new(0.2, -0.1, distance, curve, roll)];
                        // keep_size is never on maximized (wayland.cpp)
                        let placed = zoomed_placements(&canvas, &overview, &tiles, &originals, 1.0, 0.0)[0];
                        let tile = tiles[0];
                        assert!(placed.roll == 0.0 && originals[0].roll == roll, "{case}");
                        let aim = focus_direction(&canvas, &overview, &originals, 0);
                        let view = view_from(Quat::default(), aim_rotation(aim[0], aim[1]));
                        let wire = maximize_geometry_json("XR-3", &canvas, &tile, &placed, &view);
                        let parsed: Value = serde_json::from_str(&wire).unwrap();
                        let emitted = &parsed["maximize_geometry"];
                        let scale = options_step_scale(&tile);
                        let on = options_stepped(placed, scale, 1.0);
                        let rect = options_restore_rect(&tile, scale);
                        let rect_text = format!(",\"rect\":{},", floats(&[rect.x, rect.y, rect.width, rect.height]));
                        assert!(wire.contains(&rect_text), "{case}");
                        let drawn = tile_mesh(&canvas, &tile, &on, &options_restore_hit(&rect, scale));
                        let hit = emitted["hit_mesh"].as_array().unwrap();
                        assert_eq!(hit.len(), drawn.len(), "{case}");
                        let mut mesh_text = String::from("\"hit_mesh\":[");
                        for (i, d) in drawn.iter().enumerate() {
                            let v = d.world;
                            if i > 0 {
                                mesh_text.push(',');
                            }
                            mesh_text.push_str(&floats(&[v.x, v.y, v.z]));
                            let e = apply(&view, [v.x, v.y, v.z]);
                            assert!(
                                e[2] < 0.0
                                    && (e[0] / e[2]).abs() <= canvas.half_w + 1e-5
                                    && (e[1] / e[2]).abs() <= canvas.half_h + 1e-5,
                                "the pill is cut by the view: {case} e {} {}",
                                e[0] / e[2],
                                e[1] / e[2]
                            );
                        }
                        mesh_text.push(']');
                        assert!(wire.contains(&mesh_text), "{case}");
                        // The band: the pill above the picture at the middle column, RESTORE_BAND_GAP clear.
                        let b = options_picture_box(&canvas, &tile, &on, &view);
                        let point = place_point(&canvas, &tile, &on, rect.x + rect.width / 2.0, rect.y + rect.height);
                        let eye = apply(&view, [point.x, point.y, point.z]);
                        let bottom = (1.0 + eye[1] / eye[2] / canvas.half_h) * canvas.view_h / 2.0;
                        assert!(
                            bottom < b[1] && b[1] - bottom <= RESTORE_BAND_GAP * 2.0 && bottom >= 0.0,
                            "{case}: pill bottom {bottom} against the picture's top {}",
                            b[1]
                        );
                        // A picture filling the view steps back to the scale; one already leaving a band keeps its size.
                        let full = options_picture_box(&canvas, &tile, &placed, &view);
                        let grown = (full[3] - full[1]) / (b[3] - b[1]);
                        assert!(
                            (grown * scale - 1.0).abs() <= 0.03,
                            "{case}: stepped by {} not {scale}",
                            1.0 / grown
                        );
                        // At rest the picture is untouched.
                        assert_eq!(options_stepped(placed, scale, 0.0).distance, placed.distance, "{case}");
                        cases += 1;
                    }
                }
            }
        }
    }
    assert_eq!(cases, 216);
}

#[test]
fn the_band_eases_both_ways() {
    let ms = f64::from(RESTORE_STEP_MS);
    let mut s = RestoreStep::default();
    assert_eq!(s.value(0.0), 0.0);
    s.set(true, 1000.0);
    assert_eq!(s.value(1000.0), 0.0);
    assert!(s.animating(1100.0));
    assert!((s.value(1000.0 + ms / 2.0) - 0.5).abs() <= 0.001);
    assert_eq!(s.value(1000.0 + ms), 1.0);
    assert!(!s.animating(1000.0 + ms));
    s.set(false, 2000.0);
    assert_eq!(s.value(2000.0), 1.0);
    assert_eq!(s.value(2000.0 + ms), 0.0);
    // A change of mind from where it is.
    s.set(true, 3000.0);
    let mid = s.value(3000.0 + ms / 2.0);
    s.set(false, 3000.0 + ms / 2.0);
    assert!((s.value(3000.0 + ms / 2.0) - mid).abs() <= 0.001);
}

#[test]
fn real_sizes_in_the_16_10_view() {
    // The Pro 2's sizes already leave a band, the Luma's step back.
    for (w, h, want) in [
        (5120.0f32, 1440.0f32, 1.0f32),
        (2560.0, 1440.0, 1.0),
        (2560.0, 1600.0, RESTORE_STEP_SCALE),
        (1920.0, 1200.0, RESTORE_STEP_SCALE),
    ] {
        let tiles = blended_layout(&[Size::new(w, h)], 1920.0, 1200.0, 20.0, 1.0, 0.0);
        assert_eq!(options_step_scale(&tiles[0]), want, "{w}x{h}");
    }
}

/// The `restore_band_wire` ctest's twin: `restore_band_dump --dump` read by the Python supervisor
/// (spike/tests/restore_band_wire_test.py), each of the 216 shapes rested on, pointed into the band and Restored.
#[test]
fn the_dump_restores_through_the_python_supervisor() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");
    let out = std::process::Command::new("/usr/bin/python3")
        .arg(format!("{root}/spike/tests/restore_band_wire_test.py"))
        .arg(env!("CARGO_BIN_EXE_restore_band_dump"))
        .current_dir(root)
        .output()
        .expect("run /usr/bin/python3");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        out.status.success(),
        "{}\n{stdout}{}",
        out.status,
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(stdout.starts_with("216 native-exported Step-Back Band routes passed"), "{stdout}");
}
