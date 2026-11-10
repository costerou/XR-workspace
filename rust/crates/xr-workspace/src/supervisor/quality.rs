//! VR video quality settings and their in-glasses menu (tools/native_spike/quality.py, docs/vr-video.md#quality-menu).
//!
//! The defaults equal what the renderer does with no `quality` frame (spike/include/xr/video.hpp VideoQuality), so an
//! empty or old layout.json changes nothing.

use serde_json::Value;

use super::json;
use super::error::{Error, Result};

/// The texture filter's wire values and labels, in menu order.
pub const FILTERS: [(&str, &str); 4] =
    [("catmull-rom", "Catmull-Rom"), ("sharp", "Sharp"), ("bilinear", "Bilinear"), ("nearest", "Nearest pixels")];
const DEBAND: [(bool, &str); 2] = [(false, "Off"), (true, "On")];
/// The settings' keys, in menu order.
pub const KEYS: [&str; 2] = ["filter", "deband"];
const LABELS: [&str; 2] = ["Texture filter", "Deband"];
/// Renderer: kMaxQualityText.
pub const MAX_LABEL: usize = 24;

/// The quality settings: `filter` indexes FILTERS.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Quality {
    pub filter: usize,
    pub deband: bool,
}

/// As saved (`to_value`), in the Python dict's key order: layout.json is written with serde.
impl serde::Serialize for Quality {
    fn serialize<S: serde::Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = s.serialize_map(Some(2))?;
        map.serialize_entry("filter", self.filter_name())?;
        map.serialize_entry("deband", &self.deband)?;
        map.end()
    }
}

impl Quality {
    pub fn filter_name(&self) -> &'static str {
        FILTERS[self.filter].0
    }
}

fn filter_index(value: &Value, aliases: bool) -> Option<usize> {
    let name = value.as_str()?;
    let name = if aliases && name == "linear" { "bilinear" } else { name };
    FILTERS.iter().position(|(v, _)| *v == name)
}

/// Each setting checked on its own; anything missing or invalid falls back to its default. Never fails.
pub fn clean(value: &Value) -> Quality {
    let mut q = Quality::default();
    if let Value::Object(map) = value {
        if let Some(i) = map.get("filter").and_then(|v| filter_index(v, true)) {
            q.filter = i;
        }
        if let Some(Value::Bool(b)) = map.get("deband") {
            q.deband = *b;
        }
    }
    q
}

pub fn is_valid(value: &Value) -> bool {
    match value {
        Value::Object(map) => {
            json::has_exact_keys(map, &KEYS) && filter_index(&map["filter"], false).is_some() && map["deband"].is_boolean()
        }
        _ => false,
    }
}

pub fn validate(value: &Value) -> Result<Quality> {
    if !is_valid(value) {
        return Err(Error::Invalid("invalid video quality"));
    }
    Ok(clean(value))
}

/// The cleaned settings from a saved settings object (its `video_quality` entry). Missing or invalid: the defaults.
pub fn video_quality(source: &Value) -> Quality {
    clean(source.get("video_quality").unwrap_or(&Value::Null))
}

/// `quality` with the setting on menu `row` moved `delta` choices along, wrapping round.
pub fn step(quality: Quality, row: i64, delta: i64) -> Quality {
    let mut q = quality;
    match row.rem_euclid(KEYS.len() as i64) {
        0 => q.filter = (q.filter as i64 + delta).rem_euclid(FILTERS.len() as i64) as usize,
        _ => {
            let i = DEBAND.iter().position(|(v, _)| *v == q.deband).unwrap_or(0) as i64;
            q.deband = DEBAND[(i + delta).rem_euclid(DEBAND.len() as i64) as usize].0;
        }
    }
    q
}

/// The settings as saved (layout.json, a profile): `{"filter": ..., "deband": ...}`.
pub fn to_value(quality: Quality) -> Value {
    serde_json::json!({"filter": quality.filter_name(), "deband": quality.deband})
}

/// `video` frame payload applying the settings (spike/src/video_ipc.cpp).
pub fn renderer_payload(quality: Quality) -> String {
    json::object(&[
        ("action", json::string("quality")),
        ("filter", json::string(quality.filter_name())),
        ("deband", quality.deband.to_string()),
    ])
}

/// One menu row: label and value text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MenuRow {
    pub label: String,
    pub value: String,
}

/// `video` frame payload drawing the menu on the controls card: one row per setting, then the `extra` rows (not
/// video settings: the background), the selected one marked.
pub fn menu_payload(quality: Quality, visible: bool, selected: i64, extra: &[MenuRow]) -> String {
    let mut rows = vec![
        MenuRow { label: LABELS[0].into(), value: FILTERS[quality.filter].1.into() },
        MenuRow { label: LABELS[1].into(), value: DEBAND[usize::from(quality.deband)].1.into() },
    ];
    rows.extend(extra.iter().cloned());
    let selected = if visible { selected.rem_euclid(rows.len() as i64) } else { 0 };
    let rows = json::array(
        rows.iter().map(|r| json::object(&[("label", json::string(&r.label)), ("value", json::string(&r.value))])),
    );
    json::object(&[
        ("action", json::string("menu")),
        ("visible", visible.to_string()),
        ("selected", selected.to_string()),
        ("rows", rows),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn clean_falls_back_per_setting() {
        assert_eq!(clean(&json!(null)), Quality::default());
        assert_eq!(clean(&json!({"filter": "nearest", "deband": 1})), Quality { filter: 3, deband: false });
        assert_eq!(clean(&json!({"filter": "linear", "deband": true})), Quality { filter: 2, deband: true });
        assert_eq!(clean(&json!({"filter": "cubic"})), Quality::default());
        assert!(is_valid(&json!({"filter": "sharp", "deband": false})));
        assert!(!is_valid(&json!({"filter": "linear", "deband": false})));
        assert!(!is_valid(&json!({"filter": "sharp", "deband": false, "x": 1})));
        assert_eq!(video_quality(&json!({"video_quality": {"filter": "sharp", "deband": true}})), Quality { filter: 1, deband: true });
        assert_eq!(validate(&json!([])), Err(Error::Invalid("invalid video quality")));
    }

    #[test]
    fn step_wraps_round() {
        let q = Quality::default();
        assert_eq!(step(q, 0, -1).filter_name(), "nearest");
        assert_eq!(step(q, 2, 5).filter_name(), "sharp");
        assert!(step(q, 1, 1).deband);
        assert!(!step(q, -1, 2).deband);
    }

    #[test]
    fn payloads() {
        let q = Quality { filter: 2, deband: true };
        assert_eq!(renderer_payload(q), r#"{"action":"quality","filter":"bilinear","deband":true}"#);
        let extra = [MenuRow { label: "Background".into(), value: "Grid".into() }];
        assert_eq!(
            menu_payload(q, true, -1, &extra),
            r#"{"action":"menu","visible":true,"selected":2,"rows":[{"label":"Texture filter","value":"Bilinear"},{"label":"Deband","value":"On"},{"label":"Background","value":"Grid"}]}"#
        );
        assert!(menu_payload(q, false, 2, &[]).contains(r#""selected":0"#));
        assert_eq!(to_value(q), json!({"filter": "bilinear", "deband": true}));
    }
}
