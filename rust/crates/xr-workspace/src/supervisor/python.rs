//! What the view modules share with the Python: its `ValueError`, `round()` and `str.isspace()`.

/// A refused input, with the message the Python raises (`ValueError(...)`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Invalid(pub String);

impl Invalid {
    pub fn new(message: impl Into<String>) -> Invalid {
        Invalid(message.into())
    }
}

impl std::fmt::Display for Invalid {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Invalid {}

/// The shared ports (`crate::geometry`) refuse with a static message; the same message here.
impl From<crate::lua::Refused> for Invalid {
    fn from(r: crate::lua::Refused) -> Invalid {
        Invalid::new(r.0)
    }
}

/// The video side's refusals (`super::error`) carry the Python message too.
impl From<super::error::Error> for Invalid {
    fn from(e: super::error::Error) -> Invalid {
        Invalid::new(e.message())
    }
}

pub type Result<T> = std::result::Result<T, Invalid>;

/// Python's `round()` (ties to even) then `int()`, for the values these modules round.
pub(crate) fn py_round(value: f64) -> i64 {
    value.round_ties_even() as i64
}

/// Python's `str.isspace()`, which `str.split()` splits on: Unicode White_Space plus the four ASCII separators
/// U+001C..U+001F.
pub(crate) fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
