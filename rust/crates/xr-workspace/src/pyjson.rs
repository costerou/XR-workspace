//! JSON as the Python supervisor reads and writes its files.
//!
//! Reading matches `ipc.strict_json` on bytes: a repeated key or a non-finite number is refused, and a UTF-8 byte order
//! mark is skipped (Python's `json.loads` on bytes does that). Writing matches `json.dumps(value)` with its defaults:
//! `", "` and `": "` separators, keys in their stored order, and every non-ASCII character escaped. Both supervisors
//! therefore write a journal byte for byte the same.
//!
//! Integers only: the files written here never hold a float, and a float read is a type error to the caller.

use serde::Deserialize;
use serde::de::{self, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value};
use std::fmt::{self, Write};

const DUPLICATE: &str = "duplicate key";

struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictVisitor)
    }
}

struct StrictVisitor;

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Strict;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value")
    }
    fn visit_unit<E>(self) -> Result<Strict, E> {
        Ok(Strict(Value::Null))
    }
    fn visit_bool<E>(self, v: bool) -> Result<Strict, E> {
        Ok(Strict(Value::Bool(v)))
    }
    fn visit_i64<E>(self, v: i64) -> Result<Strict, E> {
        Ok(Strict(v.into()))
    }
    fn visit_u64<E>(self, v: u64) -> Result<Strict, E> {
        Ok(Strict(v.into()))
    }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Strict, E> {
        serde_json::Number::from_f64(v)
            .map(|n| Strict(Value::Number(n)))
            .ok_or_else(|| E::custom("nonfinite number"))
    }
    fn visit_str<E>(self, v: &str) -> Result<Strict, E> {
        Ok(Strict(Value::String(v.to_owned())))
    }
    fn visit_string<E>(self, v: String) -> Result<Strict, E> {
        Ok(Strict(Value::String(v)))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Strict, A::Error> {
        let mut items = Vec::new();
        while let Some(Strict(item)) = seq.next_element()? {
            items.push(item);
        }
        Ok(Strict(Value::Array(items)))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Strict, A::Error> {
        let mut out = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            if out.contains_key(&key) {
                return Err(de::Error::custom(DUPLICATE));
            }
            let Strict(value) = map.next_value()?;
            out.insert(key, value);
        }
        Ok(Strict(Value::Object(out)))
    }
}

/// `strict_json(raw)`. The error is the Python's words: `duplicate key`, or `invalid JSON` for anything else the
/// parser refuses (the Python's own text there is its library's and is not kept).
pub fn loads(raw: &[u8]) -> Result<Value, &'static str> {
    let raw = raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw);
    let mut deserializer = serde_json::Deserializer::from_slice(raw);
    let parsed =
        Strict::deserialize(&mut deserializer).and_then(|v| deserializer.end().map(|()| v.0));
    parsed.map_err(|e| {
        if e.is_data() && e.to_string().starts_with(DUPLICATE) {
            DUPLICATE
        } else {
            "invalid JSON"
        }
    })
}

/// `json.dumps(value)`.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write(value, &mut out);
    out
}

fn write(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                string(key, out);
                out.push_str(": ");
                write(item, out);
            }
            out.push('}');
        }
    }
}

/// Python's `ensure_ascii` string: printable ASCII as is, the usual short escapes, everything else `\uXXXX` (lower
/// case hex, surrogate pairs above the BMP).
fn string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_like_python() {
        let v = loads(
            r#"{"b": [1, -2, null, true, false, {}], "a": "é😀\u007f\n\"\\\/\u0001"}"#.as_bytes(),
        )
        .unwrap();
        // As json.dumps(json.loads(...)) writes it.
        assert_eq!(
            dumps(&v),
            r#"{"b": [1, -2, null, true, false, {}], "a": "\u00e9\ud83d\ude00\u007f\n\"\\/\u0001"}"#
        );
        assert_eq!(dumps(&loads(b"[]").unwrap()), "[]");
    }

    #[test]
    fn refuses_what_strict_json_refuses() {
        assert_eq!(loads(br#"{"a": 1, "a": 1}"#), Err("duplicate key"));
        assert_eq!(loads(br#"[{"x": {"a": 1, "a": 2}}]"#), Err("duplicate key"));
        assert_eq!(loads(b"NaN"), Err("invalid JSON"));
        assert_eq!(loads(b"[Infinity]"), Err("invalid JSON"));
        assert_eq!(loads(b"{} x"), Err("invalid JSON"));
        assert_eq!(loads(b"\xef\xbb\xbf {}\n"), Ok(Value::Object(Map::new())));
    }
}
