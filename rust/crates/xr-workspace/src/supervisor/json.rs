//! Small JSON helpers shared by the supervisor's feature modules.
//!
//! Outgoing frames are written field by field, in the order the Python supervisor writes them, so the bytes match
//! `json.dumps(..., separators=(',', ':'))` apart from how non-ASCII text is escaped. serde_json's `Value` keeps
//! object keys sorted, which would reorder them.

use serde_json::{Map, Value};

/// A JSON string literal.
pub(crate) fn string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| String::from("\"\""))
}

/// A JSON object from already-encoded values, in the given order.
pub(crate) fn object(fields: &[(&str, String)]) -> String {
    let mut out = String::from("{");
    for (i, (key, value)) in fields.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        out.push_str(&string(key));
        out.push(':');
        out.push_str(value);
    }
    out.push('}');
    out
}

/// A JSON array from already-encoded values.
pub(crate) fn array<I: IntoIterator<Item = String>>(items: I) -> String {
    let parts: Vec<String> = items.into_iter().collect();
    format!("[{}]", parts.join(","))
}

/// The control frame envelope (`schema` 1) around an encoded payload, newline-terminated.
pub(crate) fn frame(run_id: &str, session: &str, request_id: i64, op: &str, payload: &str) -> Vec<u8> {
    let mut line = object(&[
        ("schema", String::from("1")),
        ("request_id", request_id.to_string()),
        ("run_id", string(run_id)),
        ("session", string(session)),
        ("op", string(op)),
        ("payload", payload.to_owned()),
    ]);
    line.push('\n');
    line.into_bytes()
}

/// The object's keys are exactly `keys`.
pub(crate) fn has_exact_keys(map: &Map<String, Value>, keys: &[&str]) -> bool {
    map.len() == keys.len() && keys.iter().all(|k| map.contains_key(*k))
}

/// An integer, as Python's `type(v) is int` sees it: never a bool or a float.
pub(crate) fn int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64(),
        _ => None,
    }
}

/// An int or a float (Python's `type(v) in (int, float)`), never a bool.
pub(crate) fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Python truthiness of a JSON value.
pub(crate) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(value)` for the values ffprobe and yt-dlp give: text as is, numbers and bools as Python prints them.
pub(crate) fn text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => String::from("True"),
        Value::Bool(false) => String::from("False"),
        Value::Null => String::from("None"),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => i.to_string(),
            (_, Some(u), _) => u.to_string(),
            (_, _, Some(f)) => python_float(f),
            _ => n.to_string(),
        },
        other => other.to_string(),
    }
}

/// A float as Python's `repr` prints the usual cases (`30.0`, `0.5`).
pub(crate) fn python_float(f: f64) -> String {
    if f.is_nan() {
        String::from("nan")
    } else if f.is_infinite() {
        String::from(if f > 0.0 { "inf" } else { "-inf" })
    } else if f.fract() == 0.0 && f.abs() < 1e16 {
        format!("{f:.1}")
    } else {
        format!("{f}")
    }
}

/// At most `limit` characters (code points, as Python slices).
pub(crate) fn truncate_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// Printable ASCII only, 1 to `max` characters.
pub(crate) fn printable_ascii(text: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&text.len()) && text.bytes().all(|b| (0x20..0x7f).contains(&b))
}

/// Python's `str.isprintable()` for one character, closely enough for error text: no controls, no separators but
/// the plain space, no invisible format characters.
pub(crate) fn char_printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    if c.is_control() || c.is_whitespace() {
        return false;
    }
    !matches!(c as u32, 0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0x200b..=0x200f | 0x202a..=0x202e
        | 0x2060..=0x206f | 0xfeff | 0xfff9..=0xfffb | 0xe000..=0xf8ff | 0xf0000..=0x10ffff)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_envelope_is_pythons_compact_json() {
        let line = frame("run1", "s1", 3, "video", "{\"action\":\"close\"}");
        assert_eq!(
            String::from_utf8(line).unwrap(),
            "{\"schema\":1,\"request_id\":3,\"run_id\":\"run1\",\"session\":\"s1\",\"op\":\"video\",\"payload\":{\"action\":\"close\"}}\n"
        );
    }

    #[test]
    fn python_str_of_values() {
        assert_eq!(text(&serde_json::json!(30.0)), "30.0");
        assert_eq!(text(&serde_json::json!(5)), "5");
        assert_eq!(text(&serde_json::json!("x")), "x");
        assert_eq!(text(&serde_json::json!(true)), "True");
    }
}
