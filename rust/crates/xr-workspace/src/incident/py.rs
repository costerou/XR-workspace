//! The few Python behaviours the incident reports depend on, so the Rust files match the Python ones byte for byte:
//! `json.dumps` (ASCII only, `repr` floats), truthiness, `str()`, `splitlines`, `strip`, and the
//! `datetime` conversions the detectors and reports use.

use serde_json::{Number, Value};

pub static NULL: Value = Value::Null;

/// `d.get(key)`, with a non-object or a missing key as `None`.
pub fn get<'a>(value: &'a Value, key: &str) -> &'a Value {
    value.get(key).unwrap_or(&NULL)
}

/// Python's `bool(value)`.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `value or default`.
pub fn or<'a>(value: &'a Value, default: &'a Value) -> &'a Value {
    if truthy(value) { value } else { default }
}

/// `type(value) is int` (a JSON bool is not one).
pub fn as_int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) if !n.is_f64() => n.as_i64(),
        _ => None,
    }
}

/// A number, int or float, as Python compares them.
pub fn as_num(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    }
}

pub fn as_str(value: &Value) -> Option<&str> {
    value.as_str()
}

/// Python's `==` for decoded JSON: numbers compare by value, so 1920 == 1920.0.
pub fn py_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(_) | Value::Bool(_), Value::Number(_) | Value::Bool(_)) => {
            as_num(a) == as_num(b)
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| py_eq(v, w)))
        }
        _ => a == b,
    }
}

pub fn float(f: f64) -> Value {
    Number::from_f64(f).map_or(Value::Null, Value::Number)
}

pub fn opt_float(f: Option<f64>) -> Value {
    f.map_or(Value::Null, float)
}

pub fn opt_str(s: Option<&str>) -> Value {
    s.map_or(Value::Null, |s| Value::String(s.to_owned()))
}

/// `repr(float)`: the shortest text that reads back, fixed between 1e-4 and 1e16, else scientific.
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let sci = format!("{f:e}");
    let (mantissa, exp) = sci.split_once('e').expect("{:e} has an exponent");
    let exp: i32 = exp.parse().expect("{:e} exponent");
    let (sign, mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |m| ("-", m));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if (-4..16).contains(&exp) {
        let point = exp + 1;
        let body = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        format!("{sign}{body}")
    } else {
        let mut m = digits[..1].to_owned();
        if digits.len() > 1 {
            m.push('.');
            m.push_str(&digits[1..]);
        }
        format!(
            "{sign}{m}e{}{:02}",
            if exp < 0 { '-' } else { '+' },
            exp.abs()
        )
    }
}

fn number_text(n: &Number) -> String {
    if n.is_f64() {
        float_repr(n.as_f64().unwrap_or(f64::NAN))
    } else {
        n.to_string()
    }
}

fn json_string(out: &mut String, s: &str) {
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
}

fn dump(out: &mut String, value: &Value, indent: Option<usize>, level: usize) {
    let newline = |out: &mut String, level: usize| {
        if let Some(step) = indent {
            out.push('\n');
            out.push_str(&" ".repeat(step * level));
        }
    };
    let comma = if indent.is_some() { "," } else { ", " };
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&number_text(n)),
        Value::String(s) => json_string(out, s),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(comma);
                }
                newline(out, level + 1);
                dump(out, item, indent, level + 1);
            }
            newline(out, level);
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(comma);
                }
                newline(out, level + 1);
                json_string(out, key);
                out.push_str(": ");
                dump(out, item, indent, level + 1);
            }
            newline(out, level);
            out.push('}');
        }
    }
}

/// `json.dumps(value)` (no indent) or `json.dumps(value, indent=n)`.
pub fn dumps(value: &Value, indent: Option<usize>) -> String {
    let mut out = String::new();
    dump(&mut out, value, indent, 0);
    out
}

/// `str(value)` for a decoded JSON value (f-strings).
pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => py_repr(other),
    }
}

pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(n) => number_text(n),
        Value::String(s) => {
            let quote = if s.contains('\'') && !s.contains('"') {
                '"'
            } else {
                '\''
            };
            let mut out = String::from(quote);
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
                    c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                        out.push_str(&format!("\\x{:02x}", c as u32))
                    }
                    c => out.push(c),
                }
            }
            out.push(quote);
            out
        }
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(py_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", py_repr(&Value::String(k.clone())), py_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

// --- text -------------------------------------------------------------------------------------------------------------

fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// `str.splitlines()`.
pub fn splitlines(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut line = String::new();
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                out.push(std::mem::take(&mut line));
            }
            '\n' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => {
                out.push(std::mem::take(&mut line));
            }
            c => line.push(c),
        }
    }
    if !line.is_empty() {
        out.push(line);
    }
    out
}

/// `bytes.decode('utf-8', errors='replace')`.
pub fn decode(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

// --- time -------------------------------------------------------------------------------------------------------------

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

/// `datetime.fromtimestamp(at, timezone.utc).isoformat().replace('+00:00', 'Z')`.
pub fn iso_utc(at: f64) -> String {
    // _PyTime_DoubleToDenominator, ROUND_HALF_EVEN.
    let mut int = at.trunc();
    let mut frac = ((at - int) * 1e6).round_ties_even();
    if frac >= 1e6 {
        frac -= 1e6;
        int += 1.0;
    } else if frac < 0.0 {
        frac += 1e6;
        int -= 1.0;
    }
    let secs = int as i64;
    let us = frac as i64;
    let (days, rem) = (secs.div_euclid(86400), secs.rem_euclid(86400));
    let (y, m, d) = civil_from_days(days);
    let base = format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}",
        rem / 3600,
        rem / 60 % 60,
        rem % 60
    );
    if us != 0 {
        format!("{base}.{us:06}Z")
    } else {
        format!("{base}Z")
    }
}

/// `time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))`.
pub fn gm_stamp(now: f64) -> String {
    let secs = now.floor() as i64;
    let (days, rem) = (secs.div_euclid(86400), secs.rem_euclid(86400));
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z",
        rem / 3600,
        rem / 60 % 60,
        rem % 60
    )
}

fn digits(s: &[u8], at: usize, n: usize) -> Option<i64> {
    let part = s.get(at..at + n)?;
    if !part.iter().all(u8::is_ascii_digit) {
        return None;
    }
    std::str::from_utf8(part).ok()?.parse().ok()
}

fn local_seconds(y: i64, mo: i64, d: i64, h: i64, mi: i64, s: i64) -> Option<i64> {
    // datetime.timestamp() of a naive datetime: the local zone, as mktime sees it.
    // SAFETY: struct tm is plain data; all zero is a valid value.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    tm.tm_year = i32::try_from(y - 1900).ok()?;
    tm.tm_mon = i32::try_from(mo - 1).ok()?;
    tm.tm_mday = i32::try_from(d).ok()?;
    tm.tm_hour = i32::try_from(h).ok()?;
    tm.tm_min = i32::try_from(mi).ok()?;
    tm.tm_sec = i32::try_from(s).ok()?;
    tm.tm_isdst = -1;
    // SAFETY: `tm` is a valid, initialised struct tm that mktime may normalise in place.
    let t = unsafe { libc::mktime(&mut tm) };
    if t == -1 { None } else { Some(t) }
}

/// `datetime.fromisoformat(text).timestamp()` for the extended forms logs use: `YYYY-MM-DD[?HH[:MM[:SS[.f]]]][Z|±HH[:MM[:SS[.f]]]|±HHMM]`,
/// with any one character between the date and the time. A naive time is local, as in Python. None where Python raises.
pub fn iso_timestamp(text: &str) -> Option<f64> {
    let s = text.as_bytes();
    let (y, mo, d) = (digits(s, 0, 4)?, digits(s, 5, 2)?, digits(s, 8, 2)?);
    if s.get(4) != Some(&b'-') || s.get(7) != Some(&b'-') || !(1..=12).contains(&mo) {
        return None;
    }
    let month_days = [
        31,
        if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 {
            29
        } else {
            28
        },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if y < 1 || d < 1 || d > month_days[(mo - 1) as usize] {
        return None;
    }
    let (mut h, mut mi, mut sec, mut us) = (0, 0, 0, 0);
    let mut offset: Option<i64> = None; // microseconds east of UTC
    let mut offset_ok = true;
    if s.len() > 10 {
        let rest = text.get(10..)?;
        let mut chars = rest.chars();
        chars.next()?; // the separator: any one character
        let time = chars.as_str();
        // Where the time ends and the zone begins.
        let cut = time.find(['+', '-', 'Z']).unwrap_or(time.len());
        let (clock, zone) = time.split_at(cut);
        let c = clock.as_bytes();
        h = digits(c, 0, 2)?;
        let mut at = 2;
        if c.len() > at {
            if c[at] != b':' {
                return None;
            }
            mi = digits(c, at + 1, 2)?;
            at += 3;
        }
        if c.len() > at {
            if c[at] != b':' {
                return None;
            }
            sec = digits(c, at + 1, 2)?;
            at += 3;
        }
        if c.len() > at {
            if (c[at] != b'.' && c[at] != b',')
                || c.len() == at + 1
                || !c[at + 1..].iter().all(u8::is_ascii_digit)
            {
                return None;
            }
            let frac = &clock[at + 1..];
            let six = format!("{:0<6}", &frac[..frac.len().min(6)]);
            us = six.parse().ok()?;
        }
        if h > 24 || mi > 59 || sec > 59 || (h == 24 && (mi, sec, us) != (0, 0, 0)) {
            return None;
        }
        if !zone.is_empty() {
            if zone == "Z" {
                offset = Some(0);
            } else {
                let sign = if zone.starts_with('-') { -1 } else { 1 };
                let z = &zone.as_bytes()[1..];
                let zh = digits(z, 0, 2)?;
                let (mut zm, mut zs, mut zus) = (0, 0, 0);
                if z.len() == 4 {
                    zm = digits(z, 2, 2)?;
                } else if z.len() > 2 {
                    if z[2] != b':' {
                        return None;
                    }
                    zm = digits(z, 3, 2)?;
                    if z.len() > 5 {
                        if z[5] != b':' {
                            return None;
                        }
                        zs = digits(z, 6, 2)?;
                        if z.len() > 8 {
                            if z[8] != b'.'
                                || z.len() == 9
                                || !z[9..].iter().all(u8::is_ascii_digit)
                            {
                                return None;
                            }
                            let frac = std::str::from_utf8(&z[9..]).ok()?;
                            zus = format!("{:0<6}", &frac[..frac.len().min(6)]).parse().ok()?;
                        }
                    }
                }
                offset_ok = zh < 24 && zm < 60 && zs < 60;
                offset = Some(sign * (((zh * 60 + zm) * 60 + zs) * 1_000_000 + zus));
            }
        }
    }
    if !offset_ok {
        return None;
    }
    let days = days_from_civil(y, mo, d);
    match offset {
        Some(east) => {
            let total = ((days * 86400 + h * 3600 + mi * 60 + sec) * 1_000_000 + us) - east;
            Some(total as f64 / 1e6)
        }
        None => {
            let (y, mo, d) = if h == 24 {
                civil_from_days(days + 1)
            } else {
                (y, mo, d)
            };
            let local = local_seconds(y, mo, d, h % 24, mi, sec)?;
            Some(local as f64 + us as f64 / 1e6)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn floats_print_as_python_repr() {
        for (f, text) in [
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1793750000.0, "1793750000.0"),
            (1e16, "1e+16"),
            (1.5e-5, "1.5e-05"),
            (0.0001, "0.0001"),
            (-2.5, "-2.5"),
            (123456789012345.6, "123456789012345.6"),
            (1e22, "1e+22"),
            (0.0, "0.0"),
        ] {
            assert_eq!(float_repr(f), text, "{f}");
        }
    }

    #[test]
    fn dumps_matches_python() {
        let value = json!({"a": [1, 2.0, "é\u{1F600}\u{7f}"], "b": {}, "c": [], "d": {"e": null}});
        assert_eq!(
            dumps(&value, None),
            r#"{"a": [1, 2.0, "\u00e9\ud83d\ude00\u007f"], "b": {}, "c": [], "d": {"e": null}}"#
        );
        assert_eq!(
            dumps(&value, Some(1)),
            "{\n \"a\": [\n  1,\n  2.0,\n  \"\\u00e9\\ud83d\\ude00\\u007f\"\n ],\n \"b\": {},\n \"c\": [],\n \"d\": {\n  \"e\": null\n }\n}"
        );
    }

    #[test]
    fn iso_times() {
        assert_eq!(iso_utc(1793750000.0), "2026-11-03T23:53:20Z");
        assert_eq!(iso_utc(1793750000.25), "2026-11-03T23:53:20.250000Z");
        assert_eq!(gm_stamp(1793750000.9), "20261103T235320Z");
        assert_eq!(
            iso_timestamp("2026-11-03T23:53:20+00:00"),
            Some(1793750000.0)
        );
        assert_eq!(
            iso_timestamp("2026-11-04T00:53:20.5+0100"),
            Some(1793750000.5)
        );
        assert_eq!(iso_timestamp("2026-11-03T23:53:20Z"), Some(1793750000.0));
        assert_eq!(iso_timestamp("2026-11-03T23:53:20 "), None);
        assert_eq!(iso_timestamp("2026-13-03"), None);
        assert_eq!(iso_timestamp("garbage"), None);
    }

    #[test]
    fn splitlines_and_strip() {
        assert_eq!(
            splitlines("a\r\nb\rc\n\nd\u{2028}e\n"),
            ["a", "b", "c", "", "d", "e"]
        );
        assert_eq!(strip("\u{1f} x \n"), "x");
    }
}
