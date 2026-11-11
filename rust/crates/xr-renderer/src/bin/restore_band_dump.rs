//! `restore_band_test --dump` (spike/tests/restore_band_test.cpp): one `maximize_geometry` line for every Step-Back Band
//! shape the C++ test walks (216), for spike/tests/restore_band_wire_test.py, which feeds each one through the Python
//! supervisor's free pointer to Restore. Byte for byte the C++ dump (cpp_parity.sh). The shape checks themselves are
//! tests/restore_band.rs.

use xr_renderer::options_geometry::maximize_geometry_json;
use xr_renderer::pose::{Quat, aim_rotation, view_from};
use xr_renderer::view::{Canvas, Placement, Size, blended_layout, focus_direction, overview_layout, zoomed_placements};

fn main() {
    if std::env::args().nth(1).as_deref() != Some("--dump") {
        eprintln!("usage: restore_band_dump --dump");
        std::process::exit(2);
    }
    let mut out = String::new();
    for width in [320.0f32, 640.0, 1440.0, 2400.0] {
        for height in [240.0f32, 600.0, 1080.0] {
            for distance in [0.25f32, 1.5] {
                for curve in [0.0f32, 1.0, 2.094395] {
                    for roll in [-0.5f32, 0.0, 0.5] {
                        let canvas = Canvas::new(2560.0, 1440.0, 1.0, 0.5625);
                        let sources = [Size::new(width, height)];
                        let overview = overview_layout(&sources, 2560.0, 1440.0, 40.0);
                        let tiles = blended_layout(&sources, 2560.0, 1440.0, 40.0, 1.0, 0.0);
                        let originals = [Placement::new(0.2, -0.1, distance, curve, roll)];
                        let placed = zoomed_placements(&canvas, &overview, &tiles, &originals, 1.0, 0.0)[0];
                        let aim = focus_direction(&canvas, &overview, &originals, 0);
                        let view = view_from(Quat::default(), aim_rotation(aim[0], aim[1]));
                        out.push_str(&maximize_geometry_json("XR-3", &canvas, &tiles[0], &placed, &view));
                        out.push('\n');
                    }
                }
            }
        }
    }
    print!("{out}");
}
