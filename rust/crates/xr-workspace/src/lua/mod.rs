//! The generated Lua the supervisor and the key helper send to Hyprland's `/eval`.
//!
//! A port of the Python generators in `tools/native_spike/` (one submodule per Python module, same names). Every
//! generator gives the same string as its Python twin for the same arguments, byte for byte, and refuses the same
//! values with the same message. `tests/lua_corpus.rs` holds them to `rust/corpus/hyprland/lua.json`.
//!
//! Arguments are typed. Where Python refuses a value of the wrong type (a float for an int, a missing key), the Rust
//! signature makes that value impossible, so only value checks are left here. Every value that reaches the Lua is
//! checked against a fixed pattern first: nothing a caller passes can escape a Lua string or number.

pub mod adopt;
pub mod columns;
pub mod fit_windows;
pub mod free_pointer;
pub mod input_lock;
pub mod keybinds;
pub mod overview_cursor;
pub mod privacy;
pub mod resize;
pub mod restore;
pub mod sbs;
pub mod send;
pub mod unified_cursor;
pub mod video;
pub mod view;
pub mod window_memory;

use std::fmt;

/// The longest eval `hyprland.HyprlandClient._eval` sends, in characters.
pub const EVAL_LIMIT: usize = 4096;

/// A refused input. The message is the Python `ValueError`'s, word for word.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Refused(pub &'static str);

impl fmt::Display for Refused {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for Refused {}

pub type Result<T> = std::result::Result<T, Refused>;

/// The bind helper's command line, as a run passes it to every bind generator: `<python> -I <client> <control_socket>
/// <run_id> <session>`.
#[derive(Clone, Copy, Debug)]
pub struct BindCommand<'a> {
    pub python: &'a str,
    pub client: &'a str,
    pub control_socket: &'a str,
    pub run_id: &'a str,
    pub session: &'a str,
}

impl BindCommand<'_> {
    /// The command line, after the checks every Python bind generator makes first: paths match `view.SAFE_PATH`, the
    /// run id and session match `view.SAFE_TOKEN`.
    pub fn base(&self) -> Result<String> {
        for p in [self.python, self.client, self.control_socket] {
            if !safe_path(p) {
                return Err(Refused("unsafe path for bind command"));
            }
        }
        for t in [self.run_id, self.session] {
            if !token(t) {
                return Err(Refused("unsafe token for bind command"));
            }
        }
        Ok(format!(
            "{} -I {} {} {} {}",
            self.python, self.client, self.control_socket, self.run_id, self.session
        ))
    }
}

// --- the Python modules' regular expressions, as full matches ------------------------------------------------------

fn digits(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}

/// `0x[0-9a-f]{1,16}`: a Hyprland window address (adopt, columns, send, restore, window_memory).
pub fn address(s: &str) -> bool {
    s.strip_prefix("0x").is_some_and(|h| {
        (1..=16).contains(&h.len()) && h.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// `XR-[1-9]`: an XR desktop's output (adopt.OUTPUT).
pub fn output(s: &str) -> bool {
    s.strip_prefix("XR-")
        .is_some_and(|n| n.len() == 1 && matches!(n.as_bytes()[0], b'1'..=b'9'))
}

/// `XR-[1-4]`: a managed output (view.MANAGED).
pub fn managed(s: &str) -> bool {
    s.strip_prefix("XR-")
        .is_some_and(|n| n.len() == 1 && matches!(n.as_bytes()[0], b'1'..=b'4'))
}

/// `(?:<prefix>)-[0-9]{min,max}` for one of `prefixes`.
pub(crate) fn connector(s: &str, prefixes: &[&str], min: usize, max: usize) -> bool {
    prefixes.iter().any(|p| {
        s.strip_prefix(p)
            .and_then(|r| r.strip_prefix('-'))
            .is_some_and(|n| digits(n, min, max))
    })
}

/// `/[A-Za-z0-9_./:-]+` (view.SAFE_PATH).
pub fn safe_path(s: &str) -> bool {
    s.strip_prefix('/').is_some_and(|r| {
        !r.is_empty()
            && r.bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'/' | b':' | b'-'))
    })
}

/// `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` (view.SAFE_TOKEN, ipc.TOKEN, restore.MONITOR).
pub fn token(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=128).contains(&b.len())
        && b[0].is_ascii_alphanumeric()
        && b[1..]
            .iter()
            .all(|&c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.' | b':' | b'-'))
}

/// Python's `format(value, 'g')`: six significant digits, trailing zeros dropped, exponent form outside 1e-4..1e6.
pub fn py_g(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.into();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    let sci = format!("{value:.5e}"); // rounded to six significant digits: "d.ddddde<exp>"
    let (mantissa, exp) = sci.split_once('e').expect("exponent form");
    let exp: i32 = exp.parse().expect("exponent");
    let strip = |s: String| -> String {
        if s.contains('.') {
            s.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            s
        }
    };
    if (-4..6).contains(&exp) {
        strip(format!("{value:.prec$}", prec = (5 - exp) as usize))
    } else {
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{}e{sign}{:02}", strip(mantissa.to_string()), exp.abs())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn patterns() {
        assert!(address("0x1") && address("0x0123456789abcdef"));
        assert!(
            !address("0x")
                && !address("0x0123456789abcdef0")
                && !address("0xA")
                && !address("55d0")
        );
        assert!(
            output("XR-1")
                && output("XR-9")
                && !output("XR-0")
                && !output("XR-10")
                && !output("DP-1")
        );
        assert!(managed("XR-4") && !managed("XR-5"));
        assert!(
            safe_path("/usr/bin/python3")
                && !safe_path("/")
                && !safe_path("relative")
                && !safe_path("/a b")
        );
        assert!(
            token("a1b2c3")
                && token("run-1")
                && !token("-x")
                && !token("")
                && !token(&"a".repeat(129))
        );
        assert!(token(&"a".repeat(128)));
        assert!(
            connector("DP-1", &["DP"], 1, 3)
                && !connector("DP-1234", &["DP"], 1, 3)
                && !connector("DP-", &["DP"], 1, 3)
        );
    }

    #[test]
    fn g_format() {
        for (v, s) in [
            (1.0, "1"),
            (1.25, "1.25"),
            (59.94, "59.94"),
            (90.0, "90"),
            (120.0, "120"),
            (0.25, "0.25"),
            (1234567.0, "1.23457e+06"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (999999.5, "1e+06"),
            (-2.5, "-2.5"),
            (100000.0, "100000"),
            (1e16, "1e+16"),
        ] {
            assert_eq!(py_g(v), s, "{v}");
        }
    }
}
