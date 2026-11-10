//! Strict JSON with the key order kept, and an encoder that writes exactly the bytes Python's
//! `json.dumps` writes.
//!
//! Decoding is the one strict JSON layer every wire protocol and file uses. It accepts what both
//! shipped decoders accept, Python's `ipc.strict_json` and the renderer's `decode_control`
//! (`spike/src/ipc.cpp`), and refuses a repeated key (compared after unescaping), `NaN`, `Infinity`
//! and anything that is not JSON. [`parse`] also checks a size and a depth limit before parsing.
//! Where Python and C++ differ, it takes the stricter verdict: it refuses values nested past
//! [`MAX_DEPTH`], numbers that overflow to infinity and lone surrogate escapes (Python takes them),
//! and a byte-order mark (C++ skips it). Integers outside the 64-bit range are read as floats, as
//! C++ does. `rust/corpus/json/strict_json.json` (`tests/native_spike/strict_json_corpus.py`)
//! records the three verdicts for each of its cases.
//! (Python reads a file as bytes, where `json.loads` skips a byte-order mark, so `disk` skips one
//! at the start of a file; the wire still refuses it.)
//!
//! Encoding takes a [`Style`] that names the `json.dumps` arguments a writer uses, `ensure_ascii`
//! among them, and floats are written as Python's `repr` writes them, so a file or frame read here
//! and written again is the same bytes the Python writer produced.

use std::collections::BTreeSet;
use std::fmt;

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::Number;

/// A JSON value. Objects keep their keys in file order, as a Python `dict` does.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    /// An integer (`i64`/`u64`) or a finite float, as serde_json read it.
    Number(Number),
    String(String),
    Array(Vec<Value>),
    Object(Vec<(String, Value)>),
}

impl Value {
    /// The value under `key`, if this is an object that has it.
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Object(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(s) => Some(s),
            _ => None,
        }
    }

    /// The integer, when this is one. Python's `type(x) is int`: never a bool, never a float.
    pub fn as_int(&self) -> Option<i128> {
        match self {
            Value::Number(n) => n.as_i64().map(i128::from).or_else(|| n.as_u64().map(i128::from)),
            _ => None,
        }
    }

    /// The object's keys, when this is an object.
    pub fn keys(&self) -> Option<BTreeSet<&str>> {
        match self {
            Value::Object(entries) => Some(entries.iter().map(|(k, _)| k.as_str()).collect()),
            _ => None,
        }
    }
}

/// Why a document was refused.
#[derive(Debug)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

/// `decode_control`'s nesting limit: a value may sit inside at most 32 arrays or objects.
pub const MAX_DEPTH: usize = 32;

/// The bounds [`parse`] checks before parsing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Limits {
    /// The most bytes the text may have.
    pub max_bytes: usize,
    /// The most arrays and objects any value may sit inside. The top-level value sits inside none.
    pub max_depth: usize,
}

/// Parse `raw` strictly within `limits`. The size, then the depth, are checked before parsing.
pub fn parse(raw: &[u8], limits: Limits) -> Result<Value, Error> {
    if raw.len() > limits.max_bytes {
        return Err(Error(format!(
            "{} bytes is over the limit of {}",
            raw.len(),
            limits.max_bytes
        )));
    }
    if too_deep(raw, limits.max_depth) {
        return Err(Error(format!("nested deeper than {}", limits.max_depth)));
    }
    parse_strict(raw)
}

/// Parse `raw` strictly: one JSON value, no repeated key, no `NaN` or `Infinity`. No size or depth
/// limit of its own (the readers in `disk` bound the size first); [`parse`] adds both.
pub fn parse_strict(raw: &[u8]) -> Result<Value, Error> {
    serde_json::from_slice(raw).map_err(|e| Error(format!("invalid JSON: {e}")))
}

/// Whether any value sits inside more than `max` arrays or objects. Counts what `decode_control`
/// counts: every token outside a string (a key, a scalar or a nested container) at the current
/// number of open brackets. It only has to be right for valid JSON; anything else the parser refuses.
fn too_deep(raw: &[u8], max: usize) -> bool {
    let (mut open, mut in_string, mut escaped) = (0usize, false, false);
    for &b in raw {
        if in_string {
            match (escaped, b) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => in_string = false,
                _ => {}
            }
            continue;
        }
        match b {
            b' ' | b'\t' | b'\r' | b'\n' | b',' | b':' => {}
            b']' | b'}' => open = open.saturating_sub(1),
            _ => {
                if open > max {
                    return true;
                }
                match b {
                    b'"' => in_string = true,
                    b'[' | b'{' => open += 1,
                    _ => {}
                }
            }
        }
    }
    false
}

impl<'de> Deserialize<'de> for Value {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(ValueVisitor)
    }
}

struct ValueVisitor;

impl<'de> Visitor<'de> for ValueVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a JSON value")
    }

    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }

    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Value, E> {
        Number::from_f64(v).map(Value::Number).ok_or_else(|| E::custom("nonfinite number"))
    }

    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_owned()))
    }

    fn visit_string<E>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::new();
        while let Some(item) = seq.next_element()? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut seen = BTreeSet::new();
        let mut entries = Vec::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom("duplicate key"));
            }
            entries.push((key, map.next_value()?));
        }
        Ok(Value::Object(entries))
    }
}

/// The `json.dumps` arguments a writer uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Style {
    /// `sort_keys=True`.
    pub sort_keys: bool,
    /// `separators=(',', ':')`; otherwise Python's defaults, `(', ', ': ')`, or `(',', ': ')` with an indent.
    pub compact: bool,
    /// `indent=N`.
    pub indent: Option<usize>,
    /// `ensure_ascii=True`, Python's default: everything outside printable ASCII escaped. Off, other
    /// characters are written as they are and only `"`, `\\` and control characters are escaped,
    /// which is also what JavaScript's `JSON.stringify` writes.
    pub ensure_ascii: bool,
}

impl Style {
    /// `json.dumps(value)`.
    pub const DEFAULT: Style = Style { sort_keys: false, compact: false, indent: None, ensure_ascii: true };
    /// `json.dumps(value, sort_keys=True)`.
    pub const SORTED: Style = Style { sort_keys: true, compact: false, indent: None, ensure_ascii: true };
    /// `json.dumps(value, separators=(',', ':'))`.
    pub const COMPACT: Style = Style { sort_keys: false, compact: true, indent: None, ensure_ascii: true };
    /// `json.dumps(value, separators=(',', ':'), ensure_ascii=False)`, and `JSON.stringify(value)`.
    pub const COMPACT_UNICODE: Style = Style { sort_keys: false, compact: true, indent: None, ensure_ascii: false };
    /// `json.dumps(value, separators=(',', ':'), sort_keys=True)`.
    pub const COMPACT_SORTED: Style = Style { sort_keys: true, compact: true, indent: None, ensure_ascii: true };
    /// `json.dumps(value, indent=2)`.
    pub const INDENT2: Style = Style { sort_keys: false, compact: false, indent: Some(2), ensure_ascii: true };
}

/// `value` as `json.dumps` with `style` writes it.
pub fn encode(value: &Value, style: Style) -> String {
    let mut out = String::new();
    write_value(&mut out, value, style, 0);
    out
}

fn write_value(out: &mut String, value: &Value, style: Style, level: usize) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => write_number(out, n),
        Value::String(s) => write_string(out, s, style.ensure_ascii),
        Value::Array(items) => {
            if items.is_empty() {
                return out.push_str("[]");
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                separator(out, style, level + 1, i == 0);
                write_value(out, item, style, level + 1);
            }
            close(out, style, level);
            out.push(']');
        }
        Value::Object(entries) => {
            if entries.is_empty() {
                return out.push_str("{}");
            }
            let mut order: Vec<&(String, Value)> = entries.iter().collect();
            if style.sort_keys {
                // Python sorts str keys by code point, which is the byte order of their UTF-8.
                order.sort_by(|a, b| a.0.cmp(&b.0));
            }
            out.push('{');
            for (i, (key, item)) in order.into_iter().enumerate() {
                separator(out, style, level + 1, i == 0);
                write_string(out, key, style.ensure_ascii);
                out.push_str(if style.compact { ":" } else { ": " });
                write_value(out, item, style, level + 1);
            }
            close(out, style, level);
            out.push('}');
        }
    }
}

/// What comes before an item: the item separator after the first, and the newline and indent.
fn separator(out: &mut String, style: Style, level: usize, first: bool) {
    if !first {
        out.push(',');
        if style.indent.is_none() && !style.compact {
            out.push(' ');
        }
    }
    if let Some(indent) = style.indent {
        out.push('\n');
        out.extend(std::iter::repeat_n(' ', indent * level));
    }
}

fn close(out: &mut String, style: Style, level: usize) {
    if let Some(indent) = style.indent {
        out.push('\n');
        out.extend(std::iter::repeat_n(' ', indent * level));
    }
}

/// `json.dumps(s, ensure_ascii=...)`: printable ASCII as is, `"`, `\\` and control characters escaped,
/// and anything else escaped only with `ensure_ascii`.
fn write_string(out: &mut String, s: &str, ensure_ascii: bool) {
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
            _ if !ensure_ascii && c >= ' ' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

fn write_number(out: &mut String, n: &Number) {
    if let Some(i) = n.as_i64() {
        out.push_str(&i.to_string());
    } else if let Some(u) = n.as_u64() {
        out.push_str(&u.to_string());
    } else {
        // Number only holds finite floats.
        out.push_str(&python_float_repr(n.as_f64().unwrap_or(0.0)));
    }
}

/// `repr(x)` for a finite float: the shortest digits that round-trip, positional when the decimal
/// point sits within 16 places, otherwise exponential with a signed two-digit exponent.
pub fn python_float_repr(x: f64) -> String {
    // `{:e}` gives the shortest round-trip digits as `d.ddde<exp>`.
    let sci = format!("{x:e}");
    let (mantissa, exp) = sci.split_once('e').expect("LowerExp always writes an exponent");
    let exp: i32 = exp.parse().expect("LowerExp exponent is an integer");
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let decpt = exp + 1; // value = 0.<digits> x 10^decpt
    let n = digits.len() as i32;
    let body = if decpt <= -4 || decpt > 16 {
        let (first, rest) = digits.split_at(1);
        let fraction = if rest.is_empty() { String::new() } else { format!(".{rest}") };
        let e = decpt - 1;
        format!("{first}{fraction}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
    } else if decpt <= 0 {
        format!("0.{}{digits}", "0".repeat((-decpt) as usize))
    } else if decpt >= n {
        format!("{digits}{}.0", "0".repeat((decpt - n) as usize))
    } else {
        let (int, frac) = digits.split_at(decpt as usize);
        format!("{int}.{frac}")
    };
    format!("{sign}{body}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_print_as_python_repr() {
        // Each pair checked against CPython's repr().
        for (x, want) in [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (4.2, "4.2"),
            (-4.0, "-4.0"),
            (46.5, "46.5"),
            (1762790400.0, "1762790400.0"),
            (0.1 + 0.2, "0.30000000000000004"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.2345678901234568e17, "1.2345678901234568e+17"),
            (123456789.125, "123456789.125"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
        ] {
            assert_eq!(python_float_repr(x), want, "{x:e}");
        }
    }

    #[test]
    fn strings_escape_as_ensure_ascii() {
        let value = Value::String("a\"b\\c\n\r\t\u{8}\u{c}\u{1}\u{7f}é\u{1F600}/".into());
        assert_eq!(encode(&value, Style::DEFAULT), r#""a\"b\\c\n\r\t\b\f\u0001\u007f\u00e9\ud83d\ude00/""#);
    }

    #[test]
    fn styles_match_json_dumps() {
        let value = parse_strict(br#"{"b": [1, 2.5, {}], "a": {"z": null, "y": []}, "c": true}"#).unwrap();
        assert_eq!(encode(&value, Style::DEFAULT), r#"{"b": [1, 2.5, {}], "a": {"z": null, "y": []}, "c": true}"#);
        assert_eq!(encode(&value, Style::SORTED), r#"{"a": {"y": [], "z": null}, "b": [1, 2.5, {}], "c": true}"#);
        assert_eq!(encode(&value, Style::COMPACT_SORTED), r#"{"a":{"y":[],"z":null},"b":[1,2.5,{}],"c":true}"#);
        assert_eq!(
            encode(&value, Style::INDENT2),
            "{\n  \"b\": [\n    1,\n    2.5,\n    {}\n  ],\n  \"a\": {\n    \"z\": null,\n    \"y\": []\n  },\n  \"c\": true\n}"
        );
    }

    #[test]
    fn strict_parse_refuses_what_strict_json_refuses() {
        for bad in [
            &br#"{"a": 1, "a": 2}"#[..],
            br#"{"x": {"a": 1, "a": 1}}"#,
            b"NaN",
            b"[Infinity]",
            b"[-Infinity]",
            b"{\"a\": 1",
            b"[1,]",
            b"'a'",
            b"\xff",
            b"",
        ] {
            assert!(parse_strict(bad).is_err(), "{}", String::from_utf8_lossy(bad));
        }
        let value = parse_strict(br#"{"z": 1, "a": [1.0, -3, 18446744073709551615]}"#).unwrap();
        assert_eq!(value.keys().unwrap().into_iter().collect::<Vec<_>>(), ["a", "z"]);
        assert_eq!(encode(&value, Style::DEFAULT), r#"{"z": 1, "a": [1.0, -3, 18446744073709551615]}"#);
        assert_eq!(value.get("z").and_then(Value::as_int), Some(1));
        assert_eq!(value.get("a").unwrap(), &parse_strict(b"[1.0, -3, 18446744073709551615]").unwrap());
    }

    const LIMITS: Limits = Limits {
        max_bytes: 16384,
        max_depth: MAX_DEPTH,
    };

    fn nested(n: usize, inner: &str) -> String {
        format!("{}{inner}{}", "[".repeat(n), "]".repeat(n))
    }

    #[test]
    fn size_is_checked_before_parsing() {
        let text = format!("[{}", " ".repeat(20));
        let err = parse(
            text.as_bytes(),
            Limits {
                max_bytes: 21,
                ..LIMITS
            },
        )
        .unwrap_err();
        assert!(err.0.starts_with("invalid JSON"), "{err}");
        let err = parse(
            text.as_bytes(),
            Limits {
                max_bytes: 20,
                ..LIMITS
            },
        )
        .unwrap_err();
        assert_eq!(err.0, "21 bytes is over the limit of 20");
        assert!(
            parse(
                b"[1]",
                Limits {
                    max_bytes: 3,
                    ..LIMITS
                }
            )
            .is_ok()
        );
    }

    #[test]
    fn depth_is_checked_before_parsing() {
        // Too deep is reported even though the text is cut off: the scan runs first.
        assert_eq!(
            parse("[".repeat(40).as_bytes(), LIMITS).unwrap_err().0,
            "nested deeper than 32"
        );
        assert!(parse(nested(32, "1").as_bytes(), LIMITS).is_ok());
        assert!(parse(nested(33, "").as_bytes(), LIMITS).is_ok());
        assert!(parse(nested(33, "1").as_bytes(), LIMITS).is_err());
        assert!(parse(nested(34, "").as_bytes(), LIMITS).is_err());
        assert!(
            parse(
                format!("{}1{}", "{\"k\":".repeat(33), "}".repeat(33)).as_bytes(),
                LIMITS
            )
            .is_err()
        );
        // Brackets inside strings do not count, escaped quotes included.
        assert!(parse(format!("[\"{}\"]", "[{".repeat(40)).as_bytes(), LIMITS).is_ok());
        assert!(parse(format!("[\"\\\"{}\"]", "[".repeat(40)).as_bytes(), LIMITS).is_ok());
    }

    #[test]
    fn repeated_keys_compare_after_unescaping() {
        assert!(parse(br#"{"\u0061":1,"a":2}"#, LIMITS).is_err());
        assert!(parse(br#"[{"a":1},{"a":1}]"#, LIMITS).is_ok());
    }

    #[test]
    fn numbers_past_f64_are_refused() {
        for text in ["1e400", "[-1e400]", "1e309"] {
            assert!(parse(text.as_bytes(), LIMITS).is_err(), "{text}");
        }
        assert!(parse(b"1e-400", LIMITS).is_ok());
    }
}
