//! `view_controls::view`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md): the view
//! keys as each run's own key helper sends them, with a saved profile restored at the start. `parity` compares the
//! renderer's frames (the start's view, placement and style frames, then each key's), the compositor requests (the
//! theme's border, each selected desktop's focus), and the profile as the run left it (`save_setup`, the spacing).
//!
//! The pill panel's keys are not driven here: opening a panel installs its modal binds, which are the `binds` port's.

use std::time::Duration;

use serde_json::{Value, json};

use super::{Start, Who, parity_run};

const PROFILE: &str = "config/viture-workspace/profiles.json";

/// A saved two-desktop setup the run's consent matches (so its placements come back) and a spacing of 60.
fn saved() -> Start {
    let desktop = json!({"width": 1920, "height": 1080, "scale": 1.0});
    let profile = json!({"schema": 2, "profiles": {"luma-ultra": {
        "spacing": 60, "prediction": true,
        "desktops": [desktop, desktop],
        "placements": [{"x": -300, "y": 40, "distance": 120, "curvature": 10},
                       {"x": 300, "y": 0, "distance": 100, "curvature": 0}],
        "rolls": [0, 0],
    }}});
    Start {
        files: vec![(PROFILE, profile.to_string())],
        ..Start::default()
    }
}

/// Ctrl+Alt+Up, Right, Left, Down, then spacing up, help on and off: each through `who`'s own helper.
fn keys(s: &super::Session, who: Who) -> Value {
    let mut sent = Vec::new();
    for op in [
        "zoom_in",
        "next",
        "prev",
        "zoom_out",
        "spacing_up",
        "help",
        "help_next",
        "help",
    ] {
        sent.push(s.control(who, op, &[]));
        std::thread::sleep(Duration::from_millis(100)); // each op's frames and focus out before the next
    }
    json!(sent)
}

#[test]
fn the_view_keys_send_the_same_frames_and_leave_the_same_profile() {
    let (run, _, [python, rust]) = parity_run("stop", &saved(), Some(keys));
    assert_eq!(python, rust);
    assert_eq!(rust, json!([0, 0, 0, 0, 0, 0, 0, 0]), "a run listened");
    let ops: Vec<&str> = run
        .frames
        .iter()
        .filter_map(|f| f.split("\"op\":\"").nth(1)?.split('"').next())
        .collect();
    assert_eq!(
        ops,
        [
            "view",
            "placement",
            "style",
            "view",
            "view",
            "view",
            "view",
            "view",
            "help",
            "help",
            "help"
        ],
        "{:#?}",
        run.frames
    );
    assert!(
        run.frames[4].contains(r#""selected":1,"#) && run.frames[4].contains(r#""cut":true"#),
        "maximized, Ctrl+Alt+Right cuts to the next desktop: {}",
        run.frames[4]
    );
    let focus: Vec<&String> = run
        .trace
        .iter()
        .filter(|r| r.starts_with("/dispatch hl.dsp.focus("))
        .collect();
    assert_eq!(focus.len(), 3, "zoom_in, next, prev focus: {focus:?}");
    let profile: Value = serde_json::from_str(run.files[0].as_deref().unwrap()).unwrap();
    let saved = &profile["profiles"]["luma-ultra"];
    assert_eq!(saved["spacing"], 80, "{saved}");
    assert_eq!(saved["placements"][0]["x"], -300, "{saved}");
}
