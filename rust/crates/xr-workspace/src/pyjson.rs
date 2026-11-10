//! JSON the way the Python supervisor reads and writes it, byte for byte.
//!
//! Writing: [`compact`] is `json.dumps(v, separators=(',', ':'))` (every renderer frame) and [`spaced`] is a bare
//! `json.dumps(v)` / `json.dump(v, f)` (`layout.json`). Both keep Python's default `ensure_ascii=True`: every
//! character outside printable ASCII is written as `\uXXXX` (a surrogate pair above U+FFFF), and so is DEL (U+007F).
//! Floats are written as Python's `repr` writes them (`1.0`, `1e-05`, `1e+20`). Key order is the order the value
//! serializes in: serde derive keeps struct field order, which mirrors the Python dict literals.
//!
//! Reading: [`Json`] keeps object keys in document order and, like `json.loads`, lets a repeated key replace the
//! earlier value in the earlier position. Only the places that read untrusted dicts the way Python does (`layout.json`,
//! a Hyprland reply) use it; wire protocols decode strictly elsewhere (xr-wire).

use serde::Serialize;
use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::ser::{CharEscape, Formatter, Serializer};
use std::fmt;
use std::io::{self, Write};

/// `json.dumps(value, separators=(',', ':'))`.
pub fn compact<T: Serialize + ?Sized>(value: &T) -> String {
    write(value, Python { spaced: false })
}

/// `json.dumps(value)`: `", "` between items and `": "` after keys.
pub fn spaced<T: Serialize + ?Sized>(value: &T) -> String {
    write(value, Python { spaced: true })
}

fn write<T: Serialize + ?Sized>(value: &T, formatter: Python) -> String {
    let mut out = Vec::new();
    let mut ser = Serializer::with_formatter(&mut out, formatter);
    value
        .serialize(&mut ser)
        .expect("serializing into memory cannot fail");
    String::from_utf8(out).expect("ensure_ascii output is ASCII")
}

struct Python {
    spaced: bool,
}

impl Formatter for Python {
    fn begin_array_value<W: ?Sized + Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
        self.separator(w, first)
    }

    fn begin_object_key<W: ?Sized + Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
        self.separator(w, first)
    }

    fn begin_object_value<W: ?Sized + Write>(&mut self, w: &mut W) -> io::Result<()> {
        w.write_all(if self.spaced { b": " } else { b":" })
    }

    fn write_string_fragment<W: ?Sized + Write>(
        &mut self,
        w: &mut W,
        fragment: &str,
    ) -> io::Result<()> {
        for c in fragment.chars() {
            if (' '..='~').contains(&c) {
                w.write_all(&[c as u8])?;
            } else {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    write!(w, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }

    fn write_char_escape<W: ?Sized + Write>(
        &mut self,
        w: &mut W,
        escape: CharEscape,
    ) -> io::Result<()> {
        // Python's escapes: the short forms for these, \u00XX (lower-case hex) for the other control characters.
        let s: &[u8] = match escape {
            CharEscape::Quote => b"\\\"",
            CharEscape::ReverseSolidus => b"\\\\",
            CharEscape::Solidus => b"/",
            CharEscape::Backspace => b"\\b",
            CharEscape::FormFeed => b"\\f",
            CharEscape::LineFeed => b"\\n",
            CharEscape::CarriageReturn => b"\\r",
            CharEscape::Tab => b"\\t",
            CharEscape::AsciiControl(byte) => return write!(w, "\\u{byte:04x}"),
        };
        w.write_all(s)
    }

    fn write_f32<W: ?Sized + Write>(&mut self, w: &mut W, value: f32) -> io::Result<()> {
        self.write_f64(w, f64::from(value))
    }

    fn write_f64<W: ?Sized + Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
        w.write_all(float_repr(value).as_bytes())
    }
}

impl Python {
    fn separator<W: ?Sized + Write>(&self, w: &mut W, first: bool) -> io::Result<()> {
        match (first, self.spaced) {
            (true, _) => Ok(()),
            (false, true) => w.write_all(b", "),
            (false, false) => w.write_all(b","),
        }
    }
}

/// Python's `repr(float)` (what `json.dumps` writes): the shortest round-trip digits, positional for decimal
/// exponents -4..15, else `d.ddde+XX`. NaN and the infinities are written as `json.dumps` writes them.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    // `{:e}` gives the shortest round-trip digits: "-1.25e3", "5e-5", "0e0". Where two strings of that length are
    // equally near (664.26177978515625: ...1562 or ...1563) it can take the upper; Python takes the correctly rounded
    // one, half to even, which `{:.N$e}` gives (the nearest of that length, so it round-trips too).
    let shortest = format!("{value:e}");
    let sig = shortest
        .split_once('e')
        .map_or(0, |(m, _)| m.bytes().filter(u8::is_ascii_digit).count());
    let sci = format!("{value:.prec$e}", prec = sig.saturating_sub(1));
    let (mantissa, exp) = sci.split_once('e').expect("{:e} always has an exponent");
    let exp: i32 = exp.parse().expect("{:e} exponent is an integer");
    let (sign, mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |m| ("-", m));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if (-4..16).contains(&exp) {
        let point = exp + 1; // digits before the decimal point
        let body = if point <= 0 {
            format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
        } else if point as usize >= digits.len() {
            format!("{digits}{}.0", "0".repeat(point as usize - digits.len()))
        } else {
            let (int, frac) = digits.split_at(point as usize);
            format!("{int}.{frac}")
        };
        format!("{sign}{body}")
    } else {
        let (first, rest) = digits.split_at(1);
        let mantissa = if rest.is_empty() {
            first.to_string()
        } else {
            format!("{first}.{rest}")
        };
        let esign = if exp < 0 { '-' } else { '+' };
        format!("{sign}{mantissa}e{esign}{:02}", exp.unsigned_abs())
    }
}

/// A parsed JSON value with Python's view of it: `true` is not an integer, `25.0` is a float, and objects keep their
/// keys in order. An integer outside i64 (Python would keep it as a big int) is held as a float: every reader here
/// range-checks integers, and refuses both alike.
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    /// `json.loads(text)`. Unlike Python, NaN and Infinity are refused (as is any non-standard JSON).
    pub fn parse(text: &str) -> Result<Json, serde_json::Error> {
        serde_json::from_str(text)
    }

    /// The order-blind `serde_json::Value`, for the readers that take one. A non-finite float becomes null; `parse`
    /// never makes one.
    pub fn to_value(&self) -> serde_json::Value {
        use serde_json::Value;
        match self {
            Json::Null => Value::Null,
            Json::Bool(b) => Value::Bool(*b),
            Json::Int(i) => Value::from(*i),
            Json::Float(f) => serde_json::Number::from_f64(*f).map_or(Value::Null, Value::Number),
            Json::Str(s) => Value::String(s.clone()),
            Json::Array(a) => Value::Array(a.iter().map(Json::to_value).collect()),
            Json::Object(o) => {
                Value::Object(o.iter().map(|(k, v)| (k.clone(), v.to_value())).collect())
            }
        }
    }

    /// `d.get(key)` for a dict, None for anything else.
    pub fn get(&self, key: &str) -> Option<&Json> {
        self.as_object()?
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
    }

    pub fn as_object(&self) -> Option<&[(String, Json)]> {
        match self {
            Json::Object(entries) => Some(entries),
            _ => None,
        }
    }

    /// The value when it is a Python `int` (not a bool, not a float).
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Json::Int(v) => Some(*v),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Json::Bool(v) => Some(*v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(v) => Some(v),
            _ => None,
        }
    }
}

/// Written back as `json.dumps` writes the Python value it stands for: keys in order, `25.0` a float.
impl Serialize for Json {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        use serde::ser::{SerializeMap, SerializeSeq};
        match self {
            Json::Null => s.serialize_unit(),
            Json::Bool(b) => s.serialize_bool(*b),
            Json::Int(i) => s.serialize_i64(*i),
            Json::Float(f) => s.serialize_f64(*f),
            Json::Str(v) => s.serialize_str(v),
            Json::Array(items) => {
                let mut seq = s.serialize_seq(Some(items.len()))?;
                for item in items {
                    seq.serialize_element(item)?;
                }
                seq.end()
            }
            Json::Object(entries) => {
                let mut map = s.serialize_map(Some(entries.len()))?;
                for (k, v) in entries {
                    map.serialize_entry(k, v)?;
                }
                map.end()
            }
        }
    }
}

impl<'de> Deserialize<'de> for Json {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Json, D::Error> {
        deserializer.deserialize_any(JsonVisitor)
    }
}

struct JsonVisitor;

impl<'de> Visitor<'de> for JsonVisitor {
    type Value = Json;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("any JSON value")
    }

    fn visit_unit<E>(self) -> Result<Json, E> {
        Ok(Json::Null)
    }

    fn visit_bool<E>(self, v: bool) -> Result<Json, E> {
        Ok(Json::Bool(v))
    }

    fn visit_i64<E>(self, v: i64) -> Result<Json, E> {
        Ok(Json::Int(v))
    }

    fn visit_u64<E>(self, v: u64) -> Result<Json, E> {
        Ok(i64::try_from(v).map_or(Json::Float(v as f64), Json::Int))
    }

    fn visit_f64<E>(self, v: f64) -> Result<Json, E> {
        Ok(Json::Float(v))
    }

    fn visit_str<E>(self, v: &str) -> Result<Json, E> {
        Ok(Json::Str(v.to_owned()))
    }

    fn visit_string<E>(self, v: String) -> Result<Json, E> {
        Ok(Json::Str(v))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Json, A::Error> {
        let mut out = Vec::new();
        while let Some(v) = seq.next_element()? {
            out.push(v);
        }
        Ok(Json::Array(out))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Json, A::Error> {
        let mut out: Vec<(String, Json)> = Vec::new();
        while let Some((k, v)) = map.next_entry::<String, Json>()? {
            match out.iter_mut().find(|(key, _)| *key == k) {
                Some(slot) => slot.1 = v, // json.loads: the last value wins, in the first key's place
                None => out.push((k, v)),
            }
        }
        Ok(Json::Object(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_match_python_repr() {
        for (v, want) in [
            (1.0, "1.0"),
            (1.25, "1.25"),
            (-0.5, "-0.5"),
            (0.0, "0.0"),
            (100.0, "100.0"),
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.2345e20, "1.2345e+20"),
            (123456.789, "123456.789"),
            (0.1, "0.1"),
            // a tie between two shortest strings: Python's is the half-to-even one
            (664.2617797851562, "664.2617797851562"),
            (31.978530883789062, "31.978530883789062"),
            (f64::NAN, "NaN"),
            (f64::NEG_INFINITY, "-Infinity"),
        ] {
            assert_eq!(float_repr(v), want, "{v}");
        }
    }

    #[test]
    fn strings_are_ensure_ascii() {
        assert_eq!(
            compact("a\"b\\c\n\t\u{1}\u{7f}é😀/"),
            r#""a\"b\\c\n\t\u0001\u007f\u00e9\ud83d\ude00/""#
        );
    }

    #[test]
    fn separators() {
        let v = serde_json::json!([1, {"a": [true, null]}]);
        assert_eq!(compact(&v), r#"[1,{"a":[true,null]}]"#);
        assert_eq!(spaced(&v), r#"[1, {"a": [true, null]}]"#);
        assert_eq!(spaced(&serde_json::json!({})), "{}");
    }

    #[test]
    fn parse_keeps_order_and_python_duplicates() {
        let v = Json::parse(r#"{"b": 1, "a": true, "b": 2.0, "n": 18446744073709551615}"#).unwrap();
        assert_eq!(
            v,
            Json::Object(vec![
                ("b".into(), Json::Float(2.0)),
                ("a".into(), Json::Bool(true)),
                ("n".into(), Json::Float(18446744073709551615.0)),
            ])
        );
        assert_eq!(v.get("a").and_then(Json::as_int), None);
        assert!(Json::parse("NaN").is_err());
    }
}
