//! The background behind the desktops and behind a VR video (spike/include/xr/environment.hpp draws it): the port of
//! `tools/native_spike/environment.py`, whose module docstring explains the setting, where it is set and its wire
//! frame. The setting is read from saved JSON, so every reader takes a `serde_json::Value` and cleans it.

use serde::Serialize;
use serde_json::{Value, json};

use super::{json_int, json_str};

/// (wire name, label), in menu order: spike/include/xr/environment.hpp kBackgrounds.
pub const BACKGROUNDS: [(&str, &str); 3] = [("none", "None"), ("grid", "Grid"), ("smoke", "Smoke")];
pub const NONE: &str = "none";
/// Renderer kBrightnessMin / Max / Default, in percent.
pub const BRIGHTNESS_BOUNDS: (i64, i64) = (10, 200);
pub const DEFAULT_BRIGHTNESS: i64 = 100;
/// The placement panel's Left / Right on the Brightness row: (small, Shift).
pub const BRIGHTNESS_STEPS: (i64, i64) = (5, 25);
/// The panel's `field` while its Brightness row is the selected one.
pub const BRIGHTNESS_FIELD: &str = "brightness";
pub const TOGGLE_OP: &str = "environment_toggle";
pub const SET_OP: &str = "environment";
pub const TOGGLE_BIND: (&str, &str) = ("CTRL + ALT + E", TOGGLE_OP);
pub const MENU_LABEL: &str = "Background";
const KEYS: [&str; 3] = ["background", "last", "brightness"];

/// The setting, always valid: `last` is never "none", and `background` is "none" or `last`. Serialized in
/// `layout.json`'s key order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Environment {
    pub background: &'static str,
    /// The last background that was not "none" (what Ctrl+Alt+E comes back to).
    pub last: &'static str,
    pub brightness: i64,
}

pub const DEFAULTS: Environment = Environment {
    background: NONE,
    last: "grid",
    brightness: DEFAULT_BRIGHTNESS,
};

impl Environment {
    pub fn to_json(self) -> Value {
        json!({"background": self.background, "last": self.last, "brightness": self.brightness})
    }
}

fn name(value: Option<&Value>) -> Option<&'static str> {
    let text = value?.as_str()?;
    BACKGROUNDS.iter().map(|(n, _)| *n).find(|n| *n == text)
}

fn brightness(value: Option<&Value>) -> Option<i64> {
    json_int(value?).filter(|b| (BRIGHTNESS_BOUNDS.0..=BRIGHTNESS_BOUNDS.1).contains(b))
}

/// Each field validated on its own; anything missing or invalid falls back to its default. Never fails.
pub fn clean(value: &Value) -> Environment {
    let field = |k: &str| value.as_object().and_then(|m| m.get(k));
    let background = name(field("background")).unwrap_or(NONE);
    let last = name(field("last"))
        .filter(|l| *l != NONE)
        .unwrap_or(DEFAULTS.last);
    Environment {
        background,
        last: if background != NONE { background } else { last },
        brightness: brightness(field("brightness")).unwrap_or(DEFAULT_BRIGHTNESS),
    }
}

/// Exactly the three keys, each valid, and consistent with each other.
pub fn is_valid(value: &Value) -> bool {
    let Some(map) = value.as_object() else {
        return false;
    };
    if map.len() != KEYS.len() || !KEYS.iter().all(|k| map.contains_key(*k)) {
        return false;
    }
    match (
        name(map.get("background")),
        name(map.get("last")),
        brightness(map.get("brightness")),
    ) {
        (Some(background), Some(last), Some(_)) => {
            last != NONE && (background == NONE || background == last)
        }
        _ => false,
    }
}

pub fn validate(value: &Value) -> Result<Environment, String> {
    if !is_valid(value) {
        return Err("invalid environment".into());
    }
    Ok(clean(value))
}

/// The accessor: the cleaned setting from a saved settings object (its "environment" entry).
pub fn environment(settings: &Value) -> Environment {
    clean(settings.get("environment").unwrap_or(&Value::Null))
}

pub fn on(env: &Value) -> bool {
    clean(env).background != NONE
}

/// The background `name` chosen. "none" keeps the last one for Ctrl+Alt+E.
pub fn picked(env: &Value, background: Option<&str>) -> Result<Environment, String> {
    let background = name(background.map(Value::from).as_ref()).ok_or("invalid background")?;
    Ok(pick(clean(env), background))
}

fn pick(env: Environment, background: &'static str) -> Environment {
    Environment {
        background,
        last: if background != NONE {
            background
        } else {
            env.last
        },
        ..env
    }
}

/// Ctrl+Alt+E and the panel's switch: none <-> the last background used.
pub fn toggled(env: &Value) -> Environment {
    let env = clean(env);
    pick(
        env,
        if env.background != NONE {
            NONE
        } else {
            env.last
        },
    )
}

/// The quality menu's row: the next or previous background, wrapping round.
pub fn stepped(env: &Value, delta: i64) -> Environment {
    let env = clean(env);
    let at = BACKGROUNDS
        .iter()
        .position(|(n, _)| *n == env.background)
        .unwrap_or(0) as i64;
    pick(
        env,
        BACKGROUNDS[(at + delta).rem_euclid(BACKGROUNDS.len() as i64) as usize].0,
    )
}

/// The brightness set to `value` percent, kept within BRIGHTNESS_BOUNDS.
pub fn brightened(env: &Value, value: i64) -> Environment {
    Environment {
        brightness: value.clamp(BRIGHTNESS_BOUNDS.0, BRIGHTNESS_BOUNDS.1),
        ..clean(env)
    }
}

/// The Brightness slider pressed or dragged at fraction `t` of its track (0 = low bound, 1 = high): whole percents.
pub fn slid(env: &Value, t: f64) -> Result<Environment, String> {
    if !t.is_finite() {
        return Err("invalid slider".into());
    }
    let (lo, hi) = BRIGHTNESS_BOUNDS;
    Ok(brightened(
        env,
        lo + (t.clamp(0.0, 1.0) * (hi - lo) as f64 + 0.5).floor() as i64,
    ))
}

/// The background's label as the menus show it.
pub fn label(env: &Value) -> &'static str {
    let background = clean(env).background;
    BACKGROUNDS
        .iter()
        .find(|(n, _)| *n == background)
        .map(|(_, l)| *l)
        .unwrap_or("None")
}

/// The quality menu's row (quality.menu_payload `extra`): (label, value).
pub fn menu_row(env: &Value) -> (&'static str, &'static str) {
    (MENU_LABEL, label(env))
}

/// The renderer's `environment` frame (spike/include/xr/ipc.hpp EnvironmentPayload), one JSON line.
pub fn renderer_frame(
    run_id: &str,
    session: &str,
    request_id: u64,
    env: &Value,
) -> Result<Vec<u8>, String> {
    let env = validate(env)?;
    Ok(format!(
        "{{\"schema\":1,\"request_id\":{request_id},\"run_id\":{},\"session\":{},\"op\":\"environment\",\
         \"payload\":{{\"background\":{},\"brightness\":{}}}}}\n",
        json_str(run_id),
        json_str(session),
        json_str(env.background),
        env.brightness
    )
    .into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::corpus::{self, s};

    fn result(r: Result<Environment, String>) -> Value {
        match r {
            Ok(e) => json!({"value": e.to_json()}),
            Err(e) => json!({"error": e}),
        }
    }

    #[test]
    fn tables_match_python() {
        let c = corpus::load("environment.json");
        assert_eq!(c["backgrounds"], json!(BACKGROUNDS));
        assert_eq!(c["defaults"], DEFAULTS.to_json());
        assert_eq!(c["bounds"], json!(BRIGHTNESS_BOUNDS));
        assert_eq!(c["steps"], json!(BRIGHTNESS_STEPS));
        assert_eq!(c["ops"], json!([TOGGLE_OP, SET_OP]));
        assert_eq!(c["toggle_bind"], json!(TOGGLE_BIND));
        assert_eq!(c["menu_label"], MENU_LABEL);
    }

    #[test]
    fn every_function_matches_python() {
        let c = corpus::load("environment.json");
        let slides: Vec<f64> = c["slides"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect();
        for case in c["cases"].as_array().unwrap() {
            let v = &case["value"];
            assert_eq!(clean(v).to_json(), case["clean"], "{v}");
            assert_eq!(json!(is_valid(v)), case["is_valid"], "{v}");
            assert_eq!(result(validate(v)), case["validate"], "{v}");
            assert_eq!(
                environment(&json!({"environment": v})).to_json(),
                case["environment_dict"],
                "{v}"
            );
            assert_eq!(json!(on(v)), case["on"], "{v}");
            assert_eq!(toggled(v).to_json(), case["toggled"], "{v}");
            let steps: Vec<Value> = [-4, -1, 0, 1, 2, 7]
                .iter()
                .map(|d| stepped(v, *d).to_json())
                .collect();
            assert_eq!(Value::from(steps), case["stepped"], "{v}");
            let picks: Vec<Value> = [Some("none"), Some("grid"), Some("smoke"), Some("fog"), None]
                .iter()
                .map(|n| result(picked(v, *n)))
                .collect();
            assert_eq!(Value::from(picks), case["picked"], "{v}");
            let bright: Vec<Value> = [-5, 9, 10, 77, 200, 201, 1_000_000]
                .iter()
                .map(|b| brightened(v, *b).to_json())
                .collect();
            assert_eq!(Value::from(bright), case["brightened"], "{v}");
            let slid_all: Vec<Value> = slides
                .iter()
                .map(|t| slid(v, *t).unwrap().to_json())
                .collect();
            assert_eq!(Value::from(slid_all), case["slid"], "{v}");
            assert_eq!(label(v), s(&case["label"]));
            let (l, value) = menu_row(v);
            assert_eq!(json!({"label": l, "value": value}), case["menu_row"]);
        }
        for frame in c["frames"].as_array().unwrap() {
            let got = renderer_frame(
                s(&frame["run_id"]),
                s(&frame["session"]),
                frame["request_id"].as_u64().unwrap(),
                &frame["env"],
            );
            match got {
                Ok(bytes) => assert_eq!(String::from_utf8(bytes).unwrap(), s(&frame["value"])),
                Err(e) => assert_eq!(e, s(&frame["error"])),
            }
        }
        for case in c["bad_slides"].as_array().unwrap() {
            // Non-numbers are refused by type in the Python; NaN and infinity reach the finite check.
            let t = match case[0].as_str() {
                Some("nan") => Some(f64::NAN),
                Some("inf") => Some(f64::INFINITY),
                _ => case[0].as_f64(),
            };
            let got = t.map_or_else(
                || Err("invalid slider".to_string()),
                |t| slid(&Value::Null, t),
            );
            assert_eq!(result(got), case[1], "{case}");
        }
    }
}
