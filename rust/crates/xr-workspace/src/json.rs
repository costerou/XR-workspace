//! The few JSON shapes the installer writes, byte for byte as Python's `json.dumps` writes them
//! (`ensure_ascii`, insertion order), so a file is the same whichever installer wrote it.

/// A JSON value. Objects keep their insertion order, like a Python dict.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Json {
    Int(i64),
    Str(String),
    List(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    /// `json.dumps(value, indent=2)`.
    pub fn pretty(&self) -> String {
        let mut out = String::new();
        self.write(&mut out, Some(2), 0);
        out
    }

    /// `json.dumps(value)`: `", "` between items and `": "` after keys.
    pub fn compact(&self) -> String {
        let mut out = String::new();
        self.write(&mut out, None, 0);
        out
    }

    /// An object from `(key, value)` pairs.
    pub fn obj<K: Into<String>>(pairs: impl IntoIterator<Item = (K, Json)>) -> Json {
        Json::Obj(pairs.into_iter().map(|(k, v)| (k.into(), v)).collect())
    }

    pub fn str(s: impl Into<String>) -> Json {
        Json::Str(s.into())
    }

    fn write(&self, out: &mut String, indent: Option<usize>, level: usize) {
        match self {
            Json::Int(n) => out.push_str(&n.to_string()),
            Json::Str(s) => quote(out, s),
            Json::List(items) => container(out, '[', ']', items.len(), indent, level, |out, i| {
                items[i].write(out, indent, level + 1)
            }),
            Json::Obj(pairs) => container(out, '{', '}', pairs.len(), indent, level, |out, i| {
                quote(out, &pairs[i].0);
                out.push_str(": ");
                pairs[i].1.write(out, indent, level + 1);
            }),
        }
    }
}

fn container(
    out: &mut String,
    open: char,
    close: char,
    len: usize,
    indent: Option<usize>,
    level: usize,
    mut item: impl FnMut(&mut String, usize),
) {
    out.push(open);
    if len == 0 {
        out.push(close);
        return;
    }
    for i in 0..len {
        match indent {
            Some(width) => {
                out.push_str(if i == 0 { "\n" } else { ",\n" });
                out.push_str(&" ".repeat(width * (level + 1)));
            }
            None if i > 0 => out.push_str(", "),
            None => {}
        }
        item(out, i);
    }
    if let Some(width) = indent {
        out.push('\n');
        out.push_str(&" ".repeat(width * level));
    }
    out.push(close);
}

/// A string as Python's `ensure_ascii` encoder writes it.
fn quote(out: &mut String, s: &str) {
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
            ' '..='\u{7f}' => out.push(c),
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

#[cfg(test)]
mod tests {
    use super::Json;

    #[test]
    fn pretty_matches_python_indent_2() {
        let value = Json::obj([
            (
                "a",
                Json::obj([("path", Json::str("/x")), ("sha256", Json::str("ab"))]),
            ),
            ("list", Json::List(vec![Json::str("0.56.2"), Json::Int(1)])),
            ("empty", Json::obj::<String>([])),
        ]);
        assert_eq!(
            value.pretty(),
            "{\n  \"a\": {\n    \"path\": \"/x\",\n    \"sha256\": \"ab\"\n  },\n  \"list\": [\n    \"0.56.2\",\n    1\n  ],\n  \"empty\": {}\n}"
        );
    }

    #[test]
    fn compact_matches_python_default() {
        let value = Json::obj([("schema", Json::Int(1)), ("model", Json::str("pro2"))]);
        assert_eq!(value.compact(), "{\"schema\": 1, \"model\": \"pro2\"}");
    }

    #[test]
    fn strings_escape_like_ensure_ascii() {
        assert_eq!(
            Json::str("q\"\\\n\u{1}é😀\u{7f}").compact(),
            "\"q\\\"\\\\\\n\\u0001\\u00e9\\ud83d\\ude00\u{7f}\""
        );
    }
}
