//! Hyprland's JSON read the way the Python supervisor reads it.
//!
//! The ported modules take Hyprland's `clients`, `workspaces` and `monitors` replies as JSON values and must make the
//! same decisions the shipped Python makes on the same reply, odd values included. These helpers are those Python
//! rules: `type(v) is int` (a bool is not an int), `v in (None, False, 0)`, `a == b` across int, float and bool,
//! truthiness, `int(round(v))` (round half to even) and ordering of mixed values.

use serde_json::Value;
use std::cmp::Ordering;

/// `type(v) is int`: an integer JSON number, never a bool or a float.
pub(crate) fn int(v: Option<&Value>) -> Option<i64> {
    match v {
        Some(Value::Number(n)) if !n.is_f64() => n.as_i64(),
        _ => None,
    }
}

/// `type(v) is int and low <= v <= high`.
pub(crate) fn int_in(v: Option<&Value>, low: i64, high: i64) -> bool {
    int(v).is_some_and(|i| (low..=high).contains(&i))
}

/// `type(v) in (int, float) and math.isfinite(v)` (JSON has no infinities).
pub(crate) fn number(v: Option<&Value>) -> Option<f64> {
    match v {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

/// Python's `round()` on a float: half to even.
pub(crate) fn round(v: f64) -> f64 {
    v.round_ties_even()
}

/// The value as a number the way Python's `==` sees it: ints, floats and bools (True is 1).
fn numeric(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Python's `a == b` on JSON values, a missing key read as None.
pub(crate) fn eq(a: Option<&Value>, b: Option<&Value>) -> bool {
    let (a, b) = (a.unwrap_or(&Value::Null), b.unwrap_or(&Value::Null));
    if let (Some(x), Some(y)) = (numeric(a), numeric(b)) {
        // Exact integers compare exactly (a float only ever equals an integer it represents).
        if let (Some(i), Some(j)) = (int(Some(a)), int(Some(b))) {
            return i == j;
        }
        return x == y;
    }
    match (a, b) {
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| eq(Some(p), Some(q)))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.contains_key(k) && eq(Some(v), y.get(k)))
        }
        _ => a == b,
    }
}

/// `v == i` for an int `i`.
pub(crate) fn eq_int(v: Option<&Value>, i: i64) -> bool {
    eq(v, Some(&Value::from(i)))
}

/// `v in (None, False, 0)`: missing, null, false or a zero number.
pub(crate) fn off(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => true,
        Some(Value::Number(n)) => n.as_f64() == Some(0.0),
        _ => false,
    }
}

/// `v is True`.
pub(crate) fn is_true(v: Option<&Value>) -> bool {
    matches!(v, Some(Value::Bool(true)))
}

/// `v is False`.
pub(crate) fn is_false(v: Option<&Value>) -> bool {
    matches!(v, Some(Value::Bool(false)))
}

/// `bool(v)`.
pub(crate) fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `v` if it is a str.
pub(crate) fn text(v: Option<&Value>) -> Option<&str> {
    v.and_then(Value::as_str)
}

/// `v` if it is a list.
pub(crate) fn list(v: Option<&Value>) -> Option<&Vec<Value>> {
    v.and_then(Value::as_array)
}

/// `isinstance(v, dict) and set(v) == keys`.
pub(crate) fn has_exactly(v: &Value, keys: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|m| m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k)))
}

/// Python's `<` on values that it can order (numbers and bools, strings, lists of them); anything else orders equal
/// (where Python would raise, which Hyprland's replies never make it do).
pub(crate) fn cmp(a: &Value, b: &Value) -> Ordering {
    if let (Some(x), Some(y)) = (numeric(a), numeric(b)) {
        if let (Some(i), Some(j)) = (int(Some(a)), int(Some(b))) {
            return i.cmp(&j);
        }
        return x.partial_cmp(&y).unwrap_or(Ordering::Equal);
    }
    match (a, b) {
        (Value::String(x), Value::String(y)) => x.cmp(y),
        (Value::Array(x), Value::Array(y)) => {
            for (p, q) in x.iter().zip(y) {
                if !eq(Some(p), Some(q)) {
                    return cmp(p, q);
                }
            }
            x.len().cmp(&y.len())
        }
        _ => Ordering::Equal,
    }
}

/// The first `limit` characters of `v` if it is a str, else ''.
pub(crate) fn cut(v: Option<&Value>, limit: usize) -> String {
    text(v)
        .map(|s| s.chars().take(limit).collect())
        .unwrap_or_default()
}

// Python's `re.match(pattern + r'\Z', s)` for the few patterns the supervisor checks, by hand: no regex dependency.

/// `0x[0-9a-f]{1,16}\Z`: a Hyprland window address.
pub(crate) fn is_address(s: &str) -> bool {
    s.strip_prefix("0x").is_some_and(|hex| {
        (1..=16).contains(&hex.len())
            && hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// `XR-[1-9]\Z`: one of the run's outputs.
pub(crate) fn is_output(s: &str) -> bool {
    s.len() == 4 && s.starts_with("XR-") && (b'1'..=b'9').contains(&s.as_bytes()[3])
}

/// `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z`: a monitor name or a command token.
pub(crate) fn is_token(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b[1..]
            .iter()
            .all(|c| c.is_ascii_alphanumeric() || b"_.:-".contains(c))
}

/// `[a-z]{1,32}\Z`: a Hyprland layout name.
pub(crate) fn is_layout(s: &str) -> bool {
    (1..=32).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_equality_and_order() {
        assert!(eq(Some(&json!(3)), Some(&json!(3.0))));
        assert!(eq(Some(&json!(true)), Some(&json!(1))));
        assert!(!eq(Some(&json!("1")), Some(&json!(1))));
        assert!(eq(None, Some(&Value::Null)));
        assert!(off(Some(&json!(0.0))) && off(None) && !off(Some(&json!(2))));
        assert_eq!(cmp(&json!([1, 2]), &json!([1, 3])), Ordering::Less);
        assert_eq!(cmp(&json!([1]), &json!([1, 0])), Ordering::Less);
        assert_eq!(round(2.5), 2.0);
        assert_eq!(round(3.5), 4.0);
        assert_eq!(int(Some(&json!(true))), None);
        assert_eq!(int(Some(&json!(2.0))), None);
    }

    #[test]
    fn patterns() {
        assert!(is_address("0x1") && is_address("0x0123456789abcdef"));
        assert!(
            !is_address("0x")
                && !is_address("0x0123456789abcdef0")
                && !is_address("0xA")
                && !is_address("address:0x1")
        );
        assert!(
            is_output("XR-1")
                && is_output("XR-9")
                && !is_output("XR-0")
                && !is_output("XR-10")
                && !is_output("DP-1")
        );
        assert!(
            is_token("DP-1")
                && !is_token("-DP")
                && !is_token("")
                && is_token(&"a".repeat(128))
                && !is_token(&"a".repeat(129))
        );
        assert!(is_layout("scrolling") && !is_layout("Scrolling") && !is_layout(""));
    }
}
