//! `view_controls::setup`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md): a
//! saved setup in the session's `profiles.json` whose second output the broker does not make live. Both runners leave
//! the same file: reset to the default three when the run's desktops are the saved four, untouched when they are the
//! default three.

use super::{Start, parity_from};
use serde_json::{Value, json};

const PROFILES: &str = "config/viture-workspace/profiles.json";

fn four() -> Value {
    json!([
        {"width": 2560, "height": 1080, "scale": 1.0},
        {"width": 2560, "height": 1440, "scale": 1.25},
        {"width": 1920, "height": 1080, "scale": 1.0},
        {"width": 3840, "height": 2160, "scale": 2.0},
    ])
}

/// A profile as a person's last session left it, `desktops` with their placements and rolls, beside another model's.
fn saved(desktops: &Value) -> String {
    let n = desktops.as_array().unwrap().len();
    let placed: Vec<Value> = (0..n as i64)
        .map(|i| json!({"x": -450 + 300 * i, "y": 30, "distance": 80, "curvature": 40}))
        .collect();
    json!({"schema": 2, "profiles": {
        "beast": {"spacing": 120, "unknown": [1, 2.5]},
        "luma-ultra": {"spacing": 80, "mode": "follow", "tracking": false, "tracking_driver": "luma", "fallback": "DP-1",
                       "desktops": desktops, "placements": placed, "rolls": vec![250; n]},
    }})
    .to_string()
}

#[test]
fn a_saved_setup_the_broker_refused_goes_back_to_the_default_and_the_default_stays() {
    let start = Start {
        desktops: four(),
        files: vec![(PROFILES, saved(&four()))],
    };
    let (run, _) = parity_from("create-fail", &start);
    assert_eq!(
        run.result["error"],
        "creation incomplete; ownership plugin retained for explicit recovery"
    );
    let after: Value = serde_json::from_str(run.files[0].as_deref().unwrap()).unwrap();
    let profile = &after["profiles"]["luma-ultra"];
    let flat = json!({"x": 0, "y": 0, "distance": 100, "curvature": 0});
    assert_eq!(
        profile["desktops"],
        Value::Array(vec![
            json!({"width": 1920, "height": 1080, "scale": 1.0});
            3
        ])
    );
    assert_eq!(profile["placements"], json!([flat, flat, flat]));
    assert_eq!(profile["rolls"], json!([0, 0, 0]));
    assert_eq!(profile["spacing"], 80, "the rest of the profile is kept");
    assert_eq!(
        after["profiles"]["beast"],
        json!({"spacing": 120, "unknown": [1, 2.5]})
    );

    // The default three are not a saved setup to fall back from: the file stays as it was.
    let three = Value::Array(vec![
        json!({"width": 1920, "height": 1080, "scale": 1.0});
        3
    ]);
    let start = Start {
        desktops: three.clone(),
        files: vec![(PROFILES, saved(&three))],
    };
    let (run, _) = parity_from("create-fail", &start);
    assert_eq!(run.files[0].as_deref(), Some(saved(&three).as_str()));
}
