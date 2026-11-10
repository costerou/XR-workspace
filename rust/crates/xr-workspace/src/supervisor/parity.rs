//! Python behaviour the supervisor ports share: `str.strip`, `repr`, `json.dumps` strings and JSON integers, plus the
//! test loader for `rust/corpus/supervisor/`, which `tests/native_spike/keybinds_corpus.py` makes from the Python.

/// Python's `str.isspace`, which `str.strip` uses: Unicode White_Space plus the ASCII file, group, record and unit
/// separators (U+001C to U+001F).
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.strip()`.
pub(crate) fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// A string as Python's `json.dumps` writes it (`ensure_ascii`: every non-ASCII character as `\uXXXX`, astral ones
/// as a surrogate pair).
pub(crate) fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
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
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// Python's `repr()` of a string, for error texts that quote their input: single quotes unless the text holds a
/// single quote and no double quote; backslash, quote and control characters escaped.
pub(crate) fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (0x7f..0xa0).contains(&(c as u32)) => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// A JSON integer, as Python's `type(v) is int` takes it (not a float, not a bool).
pub(crate) fn json_int(v: &serde_json::Value) -> Option<i64> {
    if v.is_f64() { None } else { v.as_i64() }
}

#[cfg(test)]
pub(crate) mod corpus {
    use serde_json::Value;

    /// `rust/corpus/supervisor/<name>`, parsed.
    pub fn load(name: &str) -> Value {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../corpus/supervisor")
            .join(name);
        let text =
            std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        serde_json::from_str(&text).unwrap()
    }

    pub fn s(v: &Value) -> &str {
        v.as_str().unwrap_or_else(|| panic!("not a string: {v}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_strings_match_python() {
        assert_eq!(
            json_str("a\"b\\c\n\t\u{7f}é😀"),
            r#""a\"b\\c\n\t\u007f\u00e9\ud83d\ude00""#
        );
        assert_eq!(py_strip("\u{1c} x \u{a0}"), "x");
        assert_eq!(py_repr("CTRL + "), "'CTRL + '");
        assert_eq!(py_repr("it's"), "\"it's\"");
    }
}
