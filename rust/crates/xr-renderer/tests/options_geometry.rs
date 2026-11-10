//! `spike/tests/options_geometry_test.cpp`, check for check, but for the two raster checks: a choice's dropdown arrow is
//! not printed over (render_options_panel's pixels) and the Maximize glyph is centred in its pill (render_handle_atlas's
//! pixels). Neither rasteriser is ported. Plus the nlohmann dump() formatting, against output of the C++ dump.

use xr_renderer::glass::{glass_pill, level_bar_rect, level_bar_scale};
use xr_renderer::options_geometry::*;
use xr_renderer::pose::{Quat, aim_rotation, view_from};
use xr_renderer::view::{
    Canvas, FRect, Placement, Size, Vec3, blended_layout, focus_direction, overview_layout, place_point,
    zoomed_placements,
};

#[test]
fn gear_maximize_and_panel_sit_by_the_pill_and_bar() {
    for w in [80.0f32, 240.0, 640.0, 1800.0] {
        for x in [-800.0f32, 0.0, 1200.0] {
            let t = FRect::new(x, 100.0, w, 360.0);
            let p = glass_pill(&t);
            let g = options_gear(&t);
            let m = options_maximize(&t);
            let b = level_bar_rect(&t);
            let s = level_bar_scale(&t);
            assert_eq!(g.x - (p.x + p.width), 12.0, "w {w} x {x}");
            assert_eq!(g.y, p.y);
            assert_eq!(m.width, 26.0 * s);
            assert!(f64::from((b.x - (m.x + m.width) - 12.0 * s).abs()) <= 0.001);
            let panel = options_panel(&t);
            assert_eq!((panel.width, panel.height, panel.y), (600.0, 440.0, p.y));
        }
    }
}

#[test]
fn a_ray_to_the_maximize_pill_hits_it() {
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
                assert!(options_maximize_ray(&c, &t, &on, &ray).is_some(), "{curve} {roll} {distance}");
            }
        }
    }
}

#[test]
fn rows() {
    assert_eq!(options_row_at(20.0, 88.0, 38, 10), 1);
    assert_eq!(options_row_at(20.0, 392.0, 0, 64), -1);
}

#[test]
fn drawn_tabs_and_their_hit_regions_agree() {
    // The next page's tabs are not under the page you see.
    assert_eq!(options_tab_at(16.0, 62.0, 0, 14), 0);
    assert_eq!(options_tab_at(151.9, 62.0, 0, 14), 0);
    assert_eq!(options_tab_at(153.0, 62.0, 0, 14), -1);
    assert_eq!(options_tab_at(158.0, 62.0, 0, 14), 1);
    assert_eq!(options_tab_at(583.0, 62.0, 0, 14), -1);
    assert_eq!(options_tab_at(590.0, 62.0, 0, 14), -1);
    assert_eq!(options_tab_at(16.0 + 142.0 * 2.0, 62.0, 12, 14), -1);
}

#[test]
fn header_controls() {
    assert_eq!(options_header_at(346.0, 20.0), OptionsHeader::Scope);
    assert_eq!(options_header_at(440.0, 20.0), OptionsHeader::Next);
    assert_eq!(options_header_at(593.0, 20.0), OptionsHeader::Close);
    assert_eq!(options_header_at(594.0, 20.0), OptionsHeader::None);
    assert_eq!(options_header_at(300.0, 20.0), OptionsHeader::None);
}

fn number(v: f64) -> String {
    let mut s = String::new();
    json_number(&mut s, v);
    s
}

#[test]
fn numbers_print_as_nlohmann_dumps_them() {
    // Each as the C++ options_geometry_dump printed it.
    let cases: [(f64, &str); 14] = [
        (0.0, "0.0"),
        (-0.0, "-0.0"),
        (300.0, "300.0"),
        (1920.0, "1920.0"),
        (0.5625, "0.5625"),
        (f64::from(0.1f32), "0.10000000149011612"),
        (f64::from(-0.080_605_13f32), "-0.08060512691736221"),
        (f64::from(1e-5f32), "9.999999747378752e-06"),
        (f64::from(9.313_226e-10f32), "9.313225746154785e-10"),
        (f64::from(1e17f32), "9.999999843067494e+16"),
        (f64::from(1e18f32), "9.999999843067494e+17"),
        (f64::from(1.115_604_7e21f32), "1.115604678094455e+21"),
        (f64::NAN, "null"),
        (f64::NEG_INFINITY, "null"),
    ];
    for (v, want) in cases {
        assert_eq!(number(v), want, "{v:e}");
    }
    // Fixed point while the point falls within 15 digits of the start, else exponential (one digit has no point), as
    // a C++ nlohmann::json(double).dump() prints them.
    assert_eq!(number(123_456_789_012_345.6), "123456789012345.6");
    assert_eq!(number(1e15), "1e+15");
    assert_eq!(number(9.99e15), "9.99e+15");
    assert_eq!(number(5e-324), "5e-324");
    assert_eq!(number(12345.678), "12345.678");
    assert_eq!(number(0.0001), "0.0001");
    assert_eq!(number(0.00001), "1e-05");
    assert_eq!(number(1.5e300), "1.5e+300");
}

#[test]
fn strings_print_as_nlohmann_dumps_them() {
    let mut s = String::new();
    json_string(&mut s, "a\"b\\c\t\n\r\u{8}\u{c}\u{1}\u{1f}\u{7f}ünï€");
    assert_eq!(s, "\"a\\\"b\\\\c\\t\\n\\r\\b\\f\\u0001\\u001f\u{7f}ünï€\"");
}

#[test]
fn the_band_line_is_the_cpp_one() {
    // options_geometry_dump --band of {"monitor":"tab\there\u0001","source":[2560,1600],"canvas":[1920,1080,1,0.5625],
    // "placement":[-0.7,0.3,2,2.094,0.5]}, from the C++ build: a curved, rolled desktop that steps back.
    let c = Canvas::new(1920.0, 1080.0, 1.0, 0.5625);
    let sources = [Size::new(2560.0, 1600.0)];
    let own = [Placement::new(-0.7, 0.3, 2.0, 2.094, 0.5)];
    let overview = overview_layout(&sources, c.view_w, c.view_h, 20.0);
    let tiles = blended_layout(&sources, c.view_w, c.view_h, 20.0, 1.0, 0.0);
    let placed = zoomed_placements(&c, &overview, &tiles, &own, 1.0, 0.0)[0];
    let aim = focus_direction(&c, &overview, &own, 0);
    let view = view_from(Quat::default(), aim_rotation(aim[0], aim[1]));
    let want = "{\"maximize_geometry\":{\"canvas\":[1920.0,1080.0,1.0,0.5625],\"hit_mesh\":[[-0.6749039888381958,0.9378277063369751,-0.6763366460800171],[-0.6829308867454529,0.8975481390953064,-0.6858664751052856],[-0.6459007263183594,0.9386645555496216,-0.7043029069900513],[-0.6539276242256165,0.8983849883079529,-0.7138327360153198],[-0.6156682372093201,0.9389437437057495,-0.7309471368789673],[-0.6236951351165771,0.898664116859436,-0.7404770255088806],[-0.584273099899292,0.9386645555496216,-0.7562111020088196],[-0.5922999978065491,0.8983849883079529,-0.7657409310340881],[-0.5517841577529907,0.9378277063369751,-0.7800390124320984],[-0.559810996055603,0.8975481390953064,-0.7895689010620117]],\"maximized\":true,\"monitor\":\"tab\\there\\u0001\",\"picture\":[20.78704833984375,86.40007781982422,1899.213134765625,993.60009765625],\"placement\":[-0.699999988079071,0.30000001192092896,1.1904761791229248,2.0940001010894775,0.0],\"rect\":[887.3809814453125,-45.23809814453125,145.23809814453125,30.952381134033203],\"tile\":[96.0,0.0,1728.0,1080.0],\"view\":[0.7648422122001648,0.0,-0.6442177295684814,0.19037936627864838,0.9553364515304565,0.22602635622024536,0.6154446601867676,-0.29552024602890015,0.7306816577911377]}}";
    assert_eq!(maximize_geometry_json("tab\there\u{1}", &c, &tiles[0], &placed, &view), want);
}

#[test]
fn the_step_back() {
    // A picture that leaves the band keeps its size; one that does not steps back to RESTORE_STEP_SCALE.
    assert_eq!(options_step_scale(&FRect::new(0.0, 46.0, 100.0, 100.0)), 1.0);
    assert_eq!(options_step_scale(&FRect::new(0.0, 45.9, 100.0, 100.0)), RESTORE_STEP_SCALE);
    let on = options_stepped(Placement::new(0.0, 0.0, 1.0, 0.0, 0.0), 0.84, 1.0);
    assert_eq!(on.distance, 1.0 / 0.84f32);
    let r = options_restore_rect(&FRect::new(0.0, 0.0, 1920.0, 1080.0), 1.0);
    assert_eq!(r, FRect::new(899.0, -38.0, 122.0, 26.0));
    assert_eq!(options_restore_hit(&r, 1.0), FRect::new(895.0, -42.0, 130.0, 34.0));
}
