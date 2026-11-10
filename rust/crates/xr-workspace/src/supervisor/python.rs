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

/// Python's two-argument `min(a, b)`: `a` unless `b < a`, so a tie (or `-0.0` against `0.0`) keeps the first.
pub(crate) fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Python's two-argument `max(a, b)`: `a` unless `b > a`.
pub(crate) fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python's `sum()` of floats (CPython 3.12+): Neumaier's compensated sum, the compensation added at the end.
pub(crate) fn py_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let (mut total, mut c) = (0.0f64, 0.0f64);
    for x in values {
        let t = total + x;
        if total.abs() >= x.abs() {
            c += (total - t) + x;
        } else {
            c += (x - t) + total;
        }
        total = t;
    }
    if c != 0.0 && c.is_finite() {
        total += c;
    }
    total
}

/// Python's `math.hypot(x, y)` (CPython's `vector_norm`: scaled, squared and summed in double length, then one
/// correction step), which is not libm's `hypot`.
pub(crate) fn py_hypot(x: f64, y: f64) -> f64 {
    let (x, y) = (x.abs(), y.abs());
    let max = py_max(x, y);
    if max.is_infinite() {
        return max;
    }
    if x.is_nan() || y.is_nan() {
        return f64::NAN;
    }
    if max == 0.0 {
        return max;
    }
    if max < f64::MIN_POSITIVE {
        // (subnormal: CPython scales them up to normals first)
        return f64::MIN_POSITIVE * py_hypot(x / f64::MIN_POSITIVE, y / f64::MIN_POSITIVE);
    }
    // frexp(max): max = m * 2**e with 0.5 <= m < 1, so the scale is 2**-e, exact.
    let e = ((max.to_bits() >> 52) & 0x7ff) as i32 - 1022;
    let scale = f64::from_bits(((1023 - e) as u64) << 52);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for v in [x, y] {
        let v = v * scale;
        let hi = v * v;
        let lo = v.mul_add(v, -hi);
        let sum = csum + hi;
        frac2 += (csum - sum) + hi;
        csum = sum;
        frac1 += lo;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let hi = -h * h;
    let lo = (-h).mul_add(h, -hi);
    let sum = csum + hi;
    frac2 += (csum - sum) + hi;
    csum = sum;
    frac1 += lo;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

/// Python's `x ** 2` for a float: libm's `pow`, not `x * x` (the exponent is hidden from the optimiser, which would
/// make it a multiply).
pub(crate) fn py_square(x: f64) -> f64 {
    x.powf(std::hint::black_box(2.0))
}
